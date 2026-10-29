"""Batch 1C - Alibaba Cloud Model Studio LLM provider (PRIMARY).

Alibaba is AVM's primary AI provider; OpenRouter is the classified fallback
(``app.content.providers.fallback``). Alibaba is a PROVIDER, Qwen is a MODEL,
and the DashScope OpenAI-compatible endpoint is an ENDPOINT/PROTOCOL: this
adapter translates Alibaba responses into the EXISTING ``LLMResponse``
contract and maps failures onto the EXISTING AVM content-engine exceptions
(enriched with a Batch 1C classification category) — nothing else in AVM
learns Alibaba-specific response structures.

Credential boundary (roadmap 4.10 / 13): AVM uses only its dedicated
application/API credential. The Alibaba Token Plan Personal Edition
credential (``sk-sp-...``) is reserved for interactive coding tools and is
FORBIDDEN inside AVM; a configured ``sk-sp-`` key fails clearly and safely
(never a network call, never a silent fallback, never a leaked key).

Model selection is configuration-driven; no model ID is hardcoded at call
sites. Default model IDs below are the documented DashScope model families
(Qwen text/reasoning) — always overridable per environment.

No method performs a request during import; ``generate`` is the only
network-reaching call path.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional, Type

import requests
from pydantic import BaseModel

from app.content.config import LLM_REQUEST_TIMEOUT
from app.content.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMProviderError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMResponseError,
    LLMModelUnavailableError,
    LLMOutputTruncatedError,
)
from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.providers.fallback_policy import (
    CategoryMixin,
    FailureCategory,
)

logger = logging.getLogger(__name__)

# ----- configuration (environment-driven; never hardcoded at call sites) ----

ALIBABA_API_KEY_ENV = "ALIBABA_API_KEY"
ALIBABA_BASE_URL_ENV = "ALIBABA_BASE_URL"
ALIBABA_TEXT_MODEL_ENV = "ALIBABA_TEXT_MODEL"
ALIBABA_REASONING_MODEL_ENV = "ALIBABA_REASONING_MODEL"
ALIBABA_VISION_MODEL_ENV = "ALIBABA_VISION_MODEL"
ALIBABA_REQUEST_TIMEOUT_ENV = "ALIBABA_REQUEST_TIMEOUT"

# Alibaba Cloud Model Studio (DashScope) OpenAI-compatible endpoint.
# International region by default; override for the Beijing region.
DEFAULT_ALIBABA_BASE_URL = (
    "https://dashscope-intl.aliyuncs.com/compatible-mode/v1"
)
DEFAULT_ALIBABA_TEXT_MODEL = "qwen-plus"
DEFAULT_ALIBABA_REASONING_MODEL = "qwen-plus"
DEFAULT_ALIBABA_VISION_MODEL = "qwen-vl-plus"

# The Token Plan Personal Edition credential class — FORBIDDEN inside AVM.
ALIBABA_FORBIDDEN_CREDENTIAL_PREFIX = "sk-sp-"


# ----- Batch 1C credential classification -----------------------------------

class AlibabaCredentialClassError(LLMProviderError, CategoryMixin):
    """Raised when the configured Alibaba key is the forbidden ``sk-sp-`` class.

    The exception message NEVER contains the key itself. Classified as
    ``credential_class``: NEVER fallback-eligible (the problem is local
    configuration, and re-sending the request to OpenRouter would silently
    paper over credential misuse).
    """

    category = FailureCategory.CREDENTIAL_CLASS

    def __init__(self) -> None:
        super().__init__(
            "Alibaba credential class rejected: AVM requires the dedicated "
            "application/API key (e.g. sk-ws-...); the Token Plan Personal "
            "Edition key class (sk-sp-...) is forbidden inside AVM and is "
            "reserved for interactive coding tools."
        )


def validate_alibaba_credential(api_key: Optional[str]) -> str:
    """Validate the credential CLASS (never the secret) and return it.

    Raises :class:`AlibabaCredentialClassError` for the forbidden Token Plan
    ``sk-sp-`` class. Empty/None keys are reported as ``None`` (the provider
    reports its own missing-credential condition without raising here, so
    availability checks stay non-throwing).
    """
    key = (api_key or "").strip()
    if key and key.startswith(ALIBABA_FORBIDDEN_CREDENTIAL_PREFIX):
        raise AlibabaCredentialClassError()
    return key


def alibaba_api_key_from_env() -> str:
    """Read the configured Alibaba credential without exposing it."""
    return validate_alibaba_credential(os.getenv(ALIBABA_API_KEY_ENV, ""))


# ----- Batch 1C classified LLM exceptions ------------------------------------
#
# The existing AVM content-engine exception semantics are preserved verbatim
# (same types raised, same messages); each adds only the deterministic
# Batch 1C category so the fallback policy can classify without new seams.


class AlibabaAuthenticationError(LLMAuthenticationError, CategoryMixin):
    category = FailureCategory.AUTHENTICATION


class AlibabaQuotaError(LLMQuotaError, CategoryMixin):
    category = FailureCategory.QUOTA


class AlibabaRateLimitError(LLMRateLimitError, CategoryMixin):
    category = FailureCategory.RATE_LIMIT


class AlibabaConnectionError(LLMConnectionError, CategoryMixin):
    category = FailureCategory.NETWORK


class AlibabaProviderUnavailableError(LLMConnectionError, CategoryMixin):
    category = FailureCategory.PROVIDER_UNAVAILABLE


class AlibabaServerBusyError(LLMConnectionError, CategoryMixin):
    category = FailureCategory.PROVIDER_5XX


class AlibabaResponseError(LLMResponseError, CategoryMixin):
    category = FailureCategory.RESPONSE_MALFORMED


class AlibabaContentModerationError(LLMResponseError, CategoryMixin):
    """Provider content-policy rejection. NEVER fallback-eligible: re-sending
    the same prompt to OpenRouter would be policy circumvention by stealth."""

    category = FailureCategory.CONTENT_MODERATION


class AlibabaInvalidRequestError(LLMResponseError, CategoryMixin):
    """4xx parameter rejection caused by the AVM-side request. NEVER
    fallback-eligible: the same bad request would be sent to OpenRouter."""

    category = FailureCategory.INVALID_REQUEST


def _trim_key(value: str, limit: int = 500) -> str:
    return value if len(value) <= limit else value[:limit]


def _normalize_usage(raw: Any) -> Optional[Dict[str, int]]:
    """Normalize Alibaba's OpenAI-compatible usage object into the EXISTING
    ``LLMResponse.usage: Dict[str, int]`` contract.

    DashScope (OpenAI-compatible mode) returns nested usage details, e.g.::

        {
          "prompt_tokens": 12,
          "completion_tokens": 5,
          "total_tokens": 17,
          "prompt_tokens_details": {"cached_tokens": 0},
          "completion_tokens_details": {"reasoning_tokens": 0},
        }

    ``LLMResponse.usage`` is a frozen AVM contract (``Dict[str, int]``) and is
    deliberately NOT loosened for Alibaba: this adapter-local normalization
    flattens every numeric leaf into a ``<parent>_<leaf>`` key (top-level
    numeric keys keep their documented names), so nested structures such as
    ``prompt_tokens_details.cached_tokens`` become
    ``prompt_tokens_details_cached_tokens`` without ever failing Pydantic
    validation. Non-numeric leaves are dropped; a missing or null usage maps
    to ``None`` exactly as before.
    """
    if not isinstance(raw, dict):
        return None

    flat: Dict[str, int] = {}

    def walk(node: Any, prefix: str) -> None:
        if not isinstance(node, dict):
            return
        for key, value in node.items():
            name = f"{prefix}_{key}" if prefix else str(key)
            if isinstance(value, bool):
                # bool is an int subclass; never a usage quantity.
                continue
            if isinstance(value, int):
                flat[name] = value
            elif isinstance(value, float) and value.is_integer():
                flat[name] = int(value)
            elif isinstance(value, dict):
                walk(value, name)

    walk(raw, "")
    return flat or None


class AlibabaProvider(BaseLLMProvider):
    """Alibaba Cloud Model Studio OpenAI-compatible LLM provider (PRIMARY).

    Supports ordinary text generation, structured JSON generation, and
    reasoning where the configured model supports it. The provider exposes
    enough metadata (provider, model, request kind, latency, usage) for
    provenance without exposing secrets.
    """

    name = "alibaba"

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
        reasoning_model: Optional[str] = None,
        vision_model: Optional[str] = None,
    ) -> None:
        try:
            self._api_key = (
                validate_alibaba_credential(api_key)
                if api_key is not None
                else alibaba_api_key_from_env()
            )
            self._credential_class_error: Optional[
                AlibabaCredentialClassError
            ] = None
        except AlibabaCredentialClassError as exc:
            # Construction stays safe; the clear failure is raised by
            # ``generate`` so credential misuse can never be silently
            # converted into a fallback trigger.
            self._api_key = ""
            self._credential_class_error = exc
        self._model = (model or os.getenv(ALIBABA_TEXT_MODEL_ENV, "")
                       or DEFAULT_ALIBABA_TEXT_MODEL)
        self._reasoning_model = (
            reasoning_model
            or os.getenv(ALIBABA_REASONING_MODEL_ENV, "")
            or DEFAULT_ALIBABA_REASONING_MODEL
        )
        self._vision_model = (
            vision_model
            or os.getenv(ALIBABA_VISION_MODEL_ENV, "")
            or DEFAULT_ALIBABA_VISION_MODEL
        )
        self._base_url = (
            base_url
            or os.getenv(ALIBABA_BASE_URL_ENV, "")
            or DEFAULT_ALIBABA_BASE_URL
        ).rstrip("/")
        self._timeout = int(
            timeout
            or os.getenv(ALIBABA_REQUEST_TIMEOUT_ENV, "")
            or LLM_REQUEST_TIMEOUT
        )
        # Provenance of the most recent call (never includes secrets).
        self.last_latency_ms: Optional[int] = None
        self.last_request_kind: Optional[str] = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def reasoning_model(self) -> str:
        return self._reasoning_model

    @property
    def vision_model(self) -> str:
        return self._vision_model

    def is_available(self) -> bool:
        """Configuration-only availability (never performs a request).

        A forbidden ``sk-sp-`` key reports ``False``; the clear, safe failure
        itself is raised by :meth:`generate` so credential misuse can never be
        silently converted into a fallback trigger.
        """
        if self._credential_class_error is not None:
            return False
        return bool(self._api_key)

    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        *,
        json_mode: bool = False,
        use_reasoning: bool = False,
        enable_thinking: Optional[bool] = None,
    ) -> LLMResponse:
        """Generate via the DashScope OpenAI-compatible chat-completions API."""
        if self._credential_class_error is not None:
            raise self._credential_class_error
        if not self._api_key:
            raise AlibabaAuthenticationError(self.name)
        # Reject the forbidden credential class BEFORE any network activity.
        validate_alibaba_credential(self._api_key)

        selected_model = self._reasoning_model if use_reasoning else self._model
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})

        payload: Dict[str, Any] = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode or response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        if enable_thinking is not None:
            payload["extra_body"] = {"enable_thinking": bool(enable_thinking)}

        started = time.monotonic()
        data = self._post_chat(payload)
        self.last_latency_ms = int((time.monotonic() - started) * 1000)
        self.last_request_kind = "reasoning" if use_reasoning else (
            "json" if json_mode else "text"
        )

        choices = data.get("choices") or []
        if not choices:
            raise AlibabaResponseError(
                "response contained no choices",
                _trim_key(json.dumps(data)[:2000], 500),
            )
        finish_reason = choices[0].get("finish_reason", "")
        if finish_reason in {"length", "max_tokens"}:
            raise LLMOutputTruncatedError()
        if finish_reason == "content_filter":
            raise AlibabaContentModerationError("response was rejected by provider policy")
        message = choices[0].get("message") or {}
        content = message.get("content")
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
        if not isinstance(content, str) or not content.strip():
            raise AlibabaResponseError(
                "response content was empty or not a string",
                _trim_key(json.dumps(data)[:2000], 500),
            )
        usage = _normalize_usage(data.get("usage"))
        return LLMResponse(
            content=content,
            provider=self.name,
            model=selected_model,
            usage=usage,
            metadata={"finish_reason": finish_reason},
        )

    # -- HTTP boundary --------------------------------------------------------

    def _post_chat(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        try:
            response = requests.post(
                url, headers=headers, json=payload, timeout=self._timeout
            )
        except requests.exceptions.Timeout:
            raise AlibabaConnectionError(self.name, "request timed out")
        except requests.exceptions.ConnectionError as exc:
            raise AlibabaConnectionError(self.name, str(exc))

        if response.status_code == 200:
            try:
                return response.json()
            except ValueError as exc:
                raise AlibabaResponseError(f"non-JSON response body: {exc}")

        self._raise_for_status(response)

    def _raise_for_status(self, response: Any) -> None:
        """Map DashScope HTTP/error-code semantics to AVM exceptions.

        The mapping is deterministic and never includes the credential.
        """
        status = response.status_code
        code = ""
        message = ""
        try:
            body = response.json()
            error = body.get("error") or {}
            code = str(error.get("code") or "")
            message = str(error.get("message") or "")
            if not message:
                message = str(body)[:200]
        except ValueError:
            message = (response.text or "")[:200]

        lowered = f"{code} {message}".lower()
        flat = lowered.replace("_", "").replace(".", "")

        if status == 404 or any(marker in flat for marker in ("modelnotfound", "invalidmodel", "modelnotsupported")):
            raise LLMModelUnavailableError()

        if status == 401 or (status == 403 and "invalidapikey" in flat):
            raise AlibabaAuthenticationError(self.name)
        if status == 429 or "throttling" in flat or "ratelimit" in flat:
            retry_after = response.headers.get("Retry-After")
            raise AlibabaRateLimitError(
                self.name, int(retry_after) if retry_after else None
            )
        if (
            "arrearage" in flat
            or "allocationquota" in flat
            or "quota" in flat
            or status == 402
        ):
            raise AlibabaQuotaError(self.name)
        if "datainspectionfailed" in flat:
            raise AlibabaContentModerationError(
                "content was rejected by provider policy",
                _trim_key(message),
            )
        if status >= 500 or code.lower() in (
            "systemerror",
            "internalerror",
            "serviceunavailable",
        ):
            raise AlibabaServerBusyError(
                self.name, f"HTTP {status}: {_trim_key(message)}"
            )
        if 400 <= status < 500:
            raise AlibabaInvalidRequestError(
                f"HTTP {status}: {_trim_key(message)}"
            )
        raise AlibabaConnectionError(
            self.name, f"unexpected HTTP {status}: {_trim_key(message)}"
        )

    def supports_multimodal(self) -> bool:
        return True

    def generate_multimodal(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        video: Optional[str] = None,
        images: Optional[list] = None,
        video_bytes: Optional[bytes] = None,
        response_schema: Optional[type] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Multimodal via DashScope vision model (Qwen-VL): images/frames + transcript."""
        # Keep conversion provider-side: if video supplied, extract frames via FFmpeg
        if self._credential_class_error is not None:
            raise self._credential_class_error
        if not self._api_key:
            raise AlibabaAuthenticationError(self.name)
        validate_alibaba_credential(self._api_key)
        import base64, pathlib, tempfile, subprocess, os
        # Build OpenAI-compatible message with multimodal content
        content_parts: list[dict] = [{"type": "text", "text": prompt}]
        # Prefer video_bytes, then video file, then images
        if video_bytes is not None:
            b64 = base64.b64encode(video_bytes).decode("ascii")
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{b64}"}})
        elif video is not None and os.path.isfile(video):
            # Provider-specific conversion: extract frames deterministically (no fixed-count in contract)
            # Use FFmpeg to sample frames; keep small and deterministic
            try:
                from app.assembly.ffmpeg import resolve_ffmpeg
                ff = resolve_ffmpeg()
                if ff:
                    tmpd = pathlib.Path(tempfile.mkdtemp(prefix="qwen_vl_"))
                    # Sample ~1 frame per 30s bounded to 6 frames max for this understanding window
                    # Deterministic: caller windows are ~120s, so 4 frames covers it
                    duration_probe = None
                    try:
                        from app.assembly.ffmpeg import probe_media
                        duration_probe = probe_media(pathlib.Path(video)).duration or 0
                    except Exception:
                        duration_probe = 0
                    # Use window prompt to infer sub-range: we treat whole file as evidence for now
                    # Extract at 10%, 40%, 70%, 90% to capture visual change
                    fracs = [0.15, 0.40, 0.70, 0.92]
                    for fi, frac in enumerate(fracs):
                        ts = max(0.5, float(duration_probe) * frac) if duration_probe and duration_probe > 2 else fi * 2.0
                        out = tmpd / f"frame_{fi:02d}.jpg"
                        cmd = [ff, "-y", "-ss", f"{ts:.2f}", "-i", str(video), "-vframes", "1", "-vf", "scale=512:-2", str(out)]
                        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
                        if out.is_file() and out.stat().st_size > 1000:
                            b64 = base64.b64encode(out.read_bytes()).decode("ascii")
                            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                    # cleanup tmp
                    try:
                        import shutil as _sh
                        _sh.rmtree(tmpd, ignore_errors=True)
                    except Exception:
                        pass
            except Exception:
                pass
        if images:
            for p in images:
                try:
                    ib = pathlib.Path(p).read_bytes()
                    b64 = base64.b64encode(ib).decode("ascii")
                    content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                except Exception:
                    continue
        # If no visual evidence was attached, still call vision model with text only (still multimodal capable)
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})
        payload: dict = {
            "model": self._vision_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        self.last_request_kind = "multimodal"
        started = time.monotonic()
        data = self._post_chat(payload)
        self.last_latency_ms = int((time.monotonic() - started) * 1000)
        choices = data.get("choices") or []
        if not choices:
            raise AlibabaResponseError("multimodal response no choices", str(data)[:500])
        content = (choices[0].get("message") or {}).get("content")
        if isinstance(content, list):
            # OpenAI list content: join text parts
            content = "".join(p.get("text", "") for p in content if isinstance(p, dict))
        if not isinstance(content, str) or not content.strip():
            raise AlibabaResponseError("multimodal empty content", str(data)[:500])
        usage = _normalize_usage(data.get("usage"))
        return LLMResponse(content=content, provider=self.name, model=self._vision_model, usage=usage)

    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Native JSON-mode generation through the compatible-mode endpoint."""
        return self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            json_mode=True,
        )


