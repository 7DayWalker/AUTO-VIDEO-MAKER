"""Batch 1C - shared Alibaba Cloud Model Studio (DashScope) HTTP gateway.

A deliberately small HTTP boundary shared by the Alibaba image source
(``alibaba_image``) and Alibaba video provider (``alibaba_video``) so both
translate DashScope's asynchronous task protocol (submit -> task_id ->
poll -> result URL -> download) through ONE seam. This module keeps ALL
DashScope mechanics inside the provider adapter boundary — nothing else in
AVM learns about task IDs, regions, or DashScope error codes.

No method performs a request during import or construction. Tests replace
``session`` with a fake; production uses ``requests.Session``.

Failures raise classified Batch 1C exceptions (``category`` attribute) so
the fixed Alibaba-primary -> OpenRouter-fallback policy can be applied
deterministically at the caller:

    fallback-eligible      rate limit, 5xx, network/timeout, transient
                           unavailability, provider-side task failure
    NOT fallback-eligible  invalid AVM request (4xx parameter rejection),
                           authentication/credential misuse, content-policy
                           rejection, configuration
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Mapping, Optional

import requests

from app.providers.fallback_policy import (
    ClassifiedProviderError,
    FailureCategory,
)

logger = logging.getLogger(__name__)

# DashScope (Alibaba Cloud Model Studio) native async API base.
DEFAULT_ALIBABA_DASHSCOPE_BASE_URL = "https://dashscope-intl.aliyuncs.com"

# DashScope async submission requires the async header.
ALIBABA_ASYNC_HEADER = {"X-DashScope-Async": "enable"}

# Remote task statuses (DashScope async task lifecycle).
TASK_STATUS_PENDING = "PENDING"
TASK_STATUS_RUNNING = "RUNNING"
TASK_STATUS_PAUSED = "PAUSED"
TASK_STATUS_SUCCEEDED = "SUCCEEDED"
TASK_STATUS_FAILED = "FAILED"
TASK_STATUS_CANCELED = "CANCELED"
TASK_STATUS_UNKNOWN = "UNKNOWN"

# Terminal remote states: the generation itself ended (success or failure).
TASK_TERMINAL_STATUSES = frozenset(
    {TASK_STATUS_SUCCEEDED, TASK_STATUS_FAILED, TASK_STATUS_CANCELED,
     TASK_STATUS_UNKNOWN}
)
# Remote states in which the task is still in flight and must be POLLED,
# never resubmitted.
TASK_IN_FLIGHT_STATUSES = frozenset(
    {TASK_STATUS_PENDING, TASK_STATUS_RUNNING, TASK_STATUS_PAUSED}
)


class AlibabaGatewayConfigurationError(ClassifiedProviderError):
    """Configuration/authorization problems. NOT fallback-eligible."""

    category = FailureCategory.CONFIGURATION


class AlibabaGatewayAuthError(ClassifiedProviderError):
    """Authentication/credential problems. NOT fallback-eligible."""

    category = FailureCategory.AUTHENTICATION


class AlibabaGatewayInvalidRequestError(ClassifiedProviderError):
    """4xx parameter rejection caused by the AVM-side request.

    NEVER fallback-eligible: the same invalid request must not be silently
    re-sent to OpenRouter.
    """

    category = FailureCategory.INVALID_REQUEST


class AlibabaGatewayModerationError(ClassifiedProviderError):
    """Provider content-policy rejection. NOT fallback-eligible."""

    category = FailureCategory.CONTENT_MODERATION


class AlibabaGatewayRateLimitError(ClassifiedProviderError):
    category = FailureCategory.RATE_LIMIT


class AlibabaGatewayServerError(ClassifiedProviderError):
    """Provider-side 5xx. Fallback-eligible."""

    category = FailureCategory.PROVIDER_5XX


class AlibabaGatewayNetworkError(ClassifiedProviderError):
    """Transport failure (DNS, connection, timeout). Fallback-eligible."""

    category = FailureCategory.NETWORK


class AlibabaGatewayResponseError(ClassifiedProviderError):
    """Malformed provider response. Fallback-eligible."""

    category = FailureCategory.RESPONSE_MALFORMED


class DashScopeClient:
    """Minimal DashScope async-task HTTP client (submit / poll / download)."""

    def __init__(
        self,
        api_key: str,
        base_url: Optional[str] = None,
        *,
        timeout: float = 30.0,
        session: Any = None,
    ) -> None:
        self._api_key = (api_key or "").strip()
        self.base_url = (base_url or DEFAULT_ALIBABA_DASHSCOPE_BASE_URL).rstrip("/")
        self._timeout = timeout
        self._session = session if session is not None else requests.Session()

    # -- request seam ---------------------------------------------------------

    def _headers(self, *, async_header: bool = False) -> Dict[str, str]:
        if not self._api_key:
            raise AlibabaGatewayConfigurationError(
                "Alibaba gateway is not configured; a DashScope API key is "
                "required (set ALIBABA_API_KEY)"
            )
        headers = {"Authorization": f"Bearer {self._api_key}"}
        if async_header:
            headers.update(ALIBABA_ASYNC_HEADER)
        return headers

    def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Optional[Mapping[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        operation: str = "request",
        stream: bool = False,
    ) -> Any:
        request_headers = self._headers()
        if headers:
            request_headers.update(headers)
        try:
            response = self._session.request(
                method,
                url,
                json=json_body,
                headers=request_headers,
                timeout=self._timeout,
                stream=stream,
            )
        except requests.exceptions.Timeout as exc:
            raise AlibabaGatewayNetworkError(f"{operation} timed out: {exc}")
        except requests.exceptions.ConnectionError as exc:
            raise AlibabaGatewayNetworkError(f"{operation} failed: {exc}")
        self._raise_for_status(response, operation)
        return response

    def _raise_for_status(self, response: Any, operation: str) -> None:
        status = response.status_code
        if 200 <= status < 300:
            return
        message = _extract_dashscope_message(self._safe_json(response))
        flat = message.lower().replace("_", "").replace(".", "")
        if status == 401:
            raise AlibabaGatewayAuthError(
                f"{operation} failed (HTTP 401, invalid credentials)"
            )
        if status == 403 and "invalidapikey" in flat:
            raise AlibabaGatewayAuthError(
                f"{operation} failed (HTTP 403, invalid API key)"
            )
        if "datainspectionfailed" in flat:
            raise AlibabaGatewayModerationError(
                f"{operation} rejected by provider content policy: {message}"
            )
        if status == 429 or "throttling" in flat:
            raise AlibabaGatewayRateLimitError(
                f"{operation} rate limited: {message}"
            )
        if "arrearage" in flat or "allocationquota" in flat or status == 402:
            raise AlibabaGatewayRateLimitError(
                f"{operation} quota exhausted: {message}"
            )
        if 400 <= status < 500:
            raise AlibabaGatewayInvalidRequestError(
                f"{operation} rejected invalid request (HTTP {status}): "
                f"{message}"
            )
        if status >= 500:
            raise AlibabaGatewayServerError(
                f"{operation} failed (HTTP {status}): {message}"
            )
        raise AlibabaGatewayNetworkError(
            f"{operation} failed (unexpected HTTP {status}): {message}"
        )

    @staticmethod
    def _safe_json(response: Any) -> Any:
        try:
            return response.json()
        except ValueError:
            return {}

    # -- async task protocol ----------------------------------------------------

    def submit_task(
        self,
        path: str,
        body: Mapping[str, Any],
        *,
        operation: str = "async task submission",
    ) -> Mapping[str, Any]:
        """Submit one asynchronous DashScope task and return its envelope.

        The response must carry ``output.task_id``; anything else is a
        malformed provider response (fallback-eligible response_malformed).
        """
        url = f"{self.base_url}{path}"
        response = self._request(
            "POST", url, json_body=dict(body),
            headers=dict(ALIBABA_ASYNC_HEADER), operation=operation,
        )
        payload = self._safe_json(response)
        if not isinstance(payload, Mapping):
            raise AlibabaGatewayResponseError(
                f"{operation} returned a non-object body"
            )
        output = payload.get("output")
        task_id = (output or {}).get("task_id") if isinstance(output, Mapping) else None
        if not task_id:
            raise AlibabaGatewayResponseError(
                f"{operation} did not return a task_id: "
                f"{_extract_dashscope_message(payload)}"
            )
        return dict(payload)

    def poll_task(
        self,
        task_id: str,
        *,
        operation: str = "task polling",
    ) -> Mapping[str, Any]:
        """Poll one DashScope async task by id (no resubmission, ever)."""
        url = f"{self.base_url}/api/v1/tasks/{task_id}"
        response = self._request("GET", url, operation=operation)
        payload = self._safe_json(response)
        if not isinstance(payload, Mapping):
            raise AlibabaGatewayResponseError(
                f"{operation} returned a non-object body"
            )
        output = payload.get("output")
        if not isinstance(output, Mapping) or not output.get("task_status"):
            raise AlibabaGatewayResponseError(
                f"{operation} returned no task_status: "
                f"{_extract_dashscope_message(payload)}"
            )
        return dict(payload)

    def download_bytes(
        self,
        url: str,
        *,
        allowed_prefixes: tuple = (
            "video/", "audio/", "image/", "application/octet-stream",
        ),
        operation: str = "media download",
    ) -> bytes:
        """Download generated media bytes with a content-type sanity check.

        A provider URL is never trusted as the final artifact: the bytes are
        returned for local materialization + validation by the caller.
        """
        try:
            response = self._session.get(
                url, timeout=max(self._timeout, 120.0)
            )
        except requests.exceptions.Timeout as exc:
            raise AlibabaGatewayNetworkError(f"{operation} timed out: {exc}")
        except requests.exceptions.ConnectionError as exc:
            raise AlibabaGatewayNetworkError(f"{operation} failed: {exc}")
        self._raise_for_status(response, operation)
        content_type = str(
            (response.headers or {}).get("Content-Type", "")
        ).split(";")[0].strip().lower()
        if content_type and not content_type.startswith(tuple(allowed_prefixes)):
            raise AlibabaGatewayResponseError(
                f"{operation} returned unexpected content type '{content_type}'"
            )
        content = response.content or b""
        if not content:
            raise AlibabaGatewayResponseError(f"{operation} returned empty media")
        return content


def _extract_dashscope_message(payload: Any) -> str:
    """Pull a human-readable (secret-free) message from a DashScope body."""
    if not isinstance(payload, Mapping):
        return str(payload)[:200]
    code = str(payload.get("code") or "")
    message = str(payload.get("message") or "")
    if not code and not message:
        inner = payload.get("output") or {}
        if isinstance(inner, Mapping):
            code = str(inner.get("code") or "")
            message = str(inner.get("message") or "")
    return f"{code}: {message}".strip(": ")[:300]
