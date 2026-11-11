"""AgentRouter's verified TEXT-ONLY Responses and local Chat proxy routes.

Model names are gateway-declared identities, not independent attestation of
upstream weights. Images belong exclusively to separate vision observers.
No request/response bodies, transport URLs or credentials enter diagnostics.
"""
from __future__ import annotations

import math
import ipaddress
import os
import re
import json
import time
from urllib.parse import urlsplit

import requests

from app.content.exceptions import (
    LLMAuthenticationError, LLMConnectionError, LLMModelUnavailableError,
    LLMOutputTruncatedError, LLMQuotaError, LLMRateLimitError, LLMResponseError,
    LLMInvalidRequestError, provider_diagnostics, with_provider_diagnostics,
)
from app.content.providers.base import BaseLLMProvider, LLMResponse


AGENTROUTER_MODELS = ("gpt-6.1-sol", "gpt-6-astra")
DEFAULT_AGENTROUTER_BASE_URL = "https://agentrouter.org/v1"
# Same public client header as the operator's proxy; no private proxy dependency.
AGENTROUTER_USER_AGENT = "codex_cli_rs/0.1.0"
_MEDIA_TEXT = re.compile(
    r"data:(?:image|video|audio)/|\b(?:image_url|input_image|inline_data|video_url)\b|"
    r"(?<![\w/+])[A-Za-z0-9+/]{1024,}={0,2}(?![\w/+])", re.I,
)


def require_text_only(value):
    """Fail closed before networking on binary/content-part/base64 input."""
    if not isinstance(value, str) or _MEDIA_TEXT.search(value):
        raise ValueError("AgentRouter accepts pure text only; media payloads are forbidden")
    return value


def _normalize_usage(raw, *, responses=False):
    """Keep known integer counters only, translating Responses accounting keys."""
    if not isinstance(raw, dict):
        return None
    names = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens", "total_tokens": "total_tokens"} if responses else {
        "prompt_tokens": "prompt_tokens", "completion_tokens": "completion_tokens", "total_tokens": "total_tokens"}
    usage = {target: raw[source] for source, target in names.items()
             if isinstance(raw.get(source), int) and not isinstance(raw[source], bool) and raw[source] >= 0}
    for source, counter, target in (
        ("input_tokens_details" if responses else "prompt_tokens_details", "cached_tokens", "prompt_tokens_details_cached_tokens"),
        ("output_tokens_details" if responses else "completion_tokens_details", "reasoning_tokens", "completion_tokens_details_reasoning_tokens"),
    ):
        detail = raw.get(source)
        value = detail.get(counter) if isinstance(detail, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
            usage[target] = value
    return usage or None


class AgentRouterProvider(BaseLLMProvider):
    def __init__(self, api_key=None, base_url=None, model=None, timeout=None, *, use_responses_api=None, stream=False):
        if use_responses_api is not None and type(use_responses_api) is not bool:
            raise ValueError("AgentRouter Responses selection must be a boolean")
        if type(stream) is not bool:
            raise ValueError("AgentRouter streaming selection must be a boolean")
        self.streaming = stream
        self._api_key = os.getenv("AGENTROUTER_API_KEY", "") if api_key is None else api_key
        configured_url = base_url or os.getenv("AGENTROUTER_BASE_URL") or DEFAULT_AGENTROUTER_BASE_URL
        if not isinstance(configured_url, str):
            raise ValueError("Invalid AgentRouter endpoint or timeout configuration")
        self._base_url = configured_url.rstrip("/")
        self._model = model or os.getenv("AGENTROUTER_MODEL") or AGENTROUTER_MODELS[0]
        if self._model not in AGENTROUTER_MODELS:
            raise LLMModelUnavailableError()
        try:
            parsed = urlsplit(self._base_url)
            host = parsed.hostname or ""
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host.lower() == "localhost"
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and (parsed.scheme == "https" or loopback)
                     and parsed.path in {"", "/v1"} and not any(c.isspace() for c in self._base_url)
                     and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
            parsed.port  # Validate a malformed/out-of-range port without exposing it.
            self._timeout = float(timeout if timeout is not None else os.getenv("AGENTROUTER_REQUEST_TIMEOUT", "300"))
        except (ValueError, TypeError):
            raise ValueError("Invalid AgentRouter endpoint or timeout configuration") from None
        if not valid or not math.isfinite(self._timeout) or not 0 < self._timeout <= 660:
            raise ValueError("Invalid AgentRouter endpoint or timeout configuration")
        if not parsed.path or parsed.path == "/":
            self._base_url += "/v1"
        # Only these endpoint/transport pairs passed account-tier verification.
        # No speculative Messages/remote Chat readiness is advertised.
        self._api_style = "chat_completions" if loopback else "responses"
        if use_responses_api is True and loopback:
            # Preserve the historic opt-in direct route. The current loopback
            # bridge also passes native Responses through, but defaults remain
            # unchanged for Summary callers. Select BEFORE dispatch, never probe,
            # retrying a local 404, or changing existing summary callers.
            self._base_url = DEFAULT_AGENTROUTER_BASE_URL
            self._api_style = "responses"
        elif use_responses_api is False and not loopback:
            raise ValueError("AgentRouter Chat transport requires a loopback bridge")
        self._reasoning_effort = os.getenv("AGENTROUTER_REASONING_EFFORT", "low")
        if self._reasoning_effort not in {"none", "low", "medium", "high"}:
            raise ValueError("Invalid AgentRouter reasoning effort configuration")

    @property
    def name(self):
        return "agentrouter"

    @property
    def model(self):
        return self._model

    def is_available(self):
        # Configuration readiness only, not a quota or uptime promise.
        return bool(isinstance(self._api_key, str) and self._api_key.strip())

    def supports_native_video(self):
        return False

    def generate_multimodal(self, *args, **kwargs):
        # Even an empty multimodal call is rejected before any HTTP request.
        raise LLMModelUnavailableError()

    def _raise_for_status(self, response):
        """Keep safe rejection evidence; a parameter 400 is not model unavailability."""
        status = response.status_code
        if status == 200:
            return
        detail = provider_diagnostics(response, secrets=(self._api_key,))
        detail.update(api_style=self._api_style,
                      transport="local_proxy" if self._api_style == "chat_completions" else "direct")
        message = (detail["error_code"] + " " + detail["message"]).lower()
        if status in (401, 403):
            error = LLMAuthenticationError(self.name)
        elif status == 402:
            error = LLMQuotaError(self.name)
        elif status == 429:
            error = LLMRateLimitError(self.name)
        elif status >= 500:
            error = LLMConnectionError(self.name, f"Server error: {status}")
        elif status == 404 or (status == 400 and any(marker in message for marker in (
            "model_not_found", "model not found", "model is not found", "model does not exist", "model does not support",
        ))):
            error = LLMModelUnavailableError()
        elif status in (400, 413, 422):
            error = LLMInvalidRequestError("Provider rejected the request parameters")
        else:
            error = LLMResponseError(f"HTTP {status}")
        raise with_provider_diagnostics(error, detail)

    def _stream_data(self, response):
        """Consume one SSE response; incomplete streams are never accepted/retried.

        Supports both Chat proxy deltas and native Responses events. The decoded
        envelope goes through the same model/completion checks as non-streaming.
        """
        started = time.monotonic()
        pieces, size, model, finish, usage, terminal, incomplete = [], 0, None, None, None, None, None
        event_lines = []
        response.encoding = "utf-8"

        def event(payload):
            nonlocal size, model, finish, usage, terminal, incomplete
            if payload == "[DONE]":
                return True
            data = json.loads(payload)
            if not isinstance(data, dict) or data.get("error") or data.get("type") == "error":
                raise LLMResponseError("Streaming provider reported an invalid/error event")
            if data.get("model"):
                if model is not None and data["model"] != model:
                    raise LLMResponseError("Streaming response model identity changed")
                model = data["model"]
            if isinstance(data.get("usage"), dict):
                usage = data["usage"]
            if isinstance(data.get("incomplete_details"), dict):
                reason = data["incomplete_details"].get("reason")
                incomplete = {"reason": reason if reason in {"max_output_tokens", "content_filter"} else "unknown"}
            if self._api_style == "responses":
                if isinstance(data.get("response"), dict):
                    reported = data["response"].get("model")
                    if reported and reported != self._model:
                        raise LLMResponseError("Streaming response model does not match the locked model")
                    model = reported or model
                    if data.get("type") in {"response.completed", "response.incomplete", "response.failed"}:
                        terminal = data["response"]
                delta = data.get("delta") if data.get("type") == "response.output_text.delta" else None
            else:
                delta = None
                for choice in data.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    delta = (choice.get("delta") or {}).get("content")
                    if choice.get("finish_reason"):
                        finish = choice["finish_reason"]
            if delta is not None:
                if not isinstance(delta, str):
                    raise LLMResponseError("Streaming text delta is not text")
                size += len(delta)
                if size > 1_000_000:
                    raise LLMResponseError("Streaming text exceeded the bounded response buffer")
                pieces.append(delta)
            return self._api_style == "responses" and terminal is not None

        for line in response.iter_lines(decode_unicode=True):
            if time.monotonic()-started > self._timeout:
                error = LLMConnectionError(self.name, "Streaming response exceeded the request deadline")
                error.category = "timeout"
                raise with_provider_diagnostics(error, {"http_status": 200, "error_code": "STREAM_TIMEOUT",
                    "message": "No completed stream before the request deadline; no retry was sent",
                    "timeout_seconds": self._timeout, "api_style": self._api_style,
                    "transport": "local_proxy" if self._api_style == "chat_completions" else "direct"})
            if isinstance(line, bytes):
                line = line.decode("utf-8")
            if len(line) > 262144:
                raise LLMResponseError("Streaming event exceeds the safe buffer size")
            if line.startswith("data:"):
                event_lines.append(line[5:].lstrip())
                if sum(len(value) for value in event_lines) > 262144:
                    raise LLMResponseError("Streaming event exceeds the safe buffer size")
            elif not line and event_lines:
                done = event("\n".join(event_lines))
                event_lines = []
                if done:
                    break
        if event_lines:
            event("\n".join(event_lines))
        if model != self._model:
            raise LLMResponseError("Streaming response model does not match the locked model")
        if self._api_style == "responses":
            if terminal is None:
                raise LLMResponseError("Responses stream ended without completion evidence")
            if terminal.get("model") != model:
                raise LLMResponseError("Streaming response model identity changed")
            return terminal
        if finish is None:
            raise LLMResponseError("Chat stream ended without a finish reason")
        return {"model": model, "choices": [{"finish_reason": finish, "message": {"content": "".join(pieces)}}],
                "usage": usage, "incomplete_details": incomplete}

    def _truncated(self, data, status, finish):
        """Fixed/count-only incomplete diagnostics; no partial draft is accepted."""
        raw_reason = (data.get("incomplete_details") or {}).get("reason")
        reason = raw_reason if raw_reason in {"max_output_tokens", "content_filter"} else "unknown"
        usage = _normalize_usage(data.get("usage"), responses=self._api_style == "responses") or {}
        tokens = usage.get("completion_tokens")
        message = f"Upstream response incomplete (reason={reason}, finish={finish}); partial output was not accepted"
        if tokens is not None:
            message += f"; reported output tokens={tokens}"
        raise with_provider_diagnostics(LLMOutputTruncatedError(), {"http_status": status,
            "error_code": "OUTPUT_INCOMPLETE", "message": message, "api_style": self._api_style,
            "transport": "local_proxy" if self._api_style == "chat_completions" else "direct"})

    def _response_data(self, response):
        try:
            self._raise_for_status(response)
            headers = getattr(response, "headers", {}) or {}
            if self.streaming and "text/event-stream" in str(headers.get("Content-Type", "")).lower():
                return self._stream_data(response)
            # Some gateways return a regular JSON envelope even when stream is
            # requested. Accept it only through the normal completion checks.
            return response.json()
        except requests.exceptions.RequestException:
            error = LLMConnectionError(self.name, "Streaming transport interrupted")
            error.category = "timeout"
            raise with_provider_diagnostics(error, {"http_status": response.status_code,
                "error_code": "STREAM_INTERRUPTED", "message": "The response stream was interrupted; upstream outcome is uncertain",
                "timeout_seconds": self._timeout, "api_style": self._api_style,
                "transport": "local_proxy" if self._api_style == "chat_completions" else "direct"}) from None
        finally:
            if self.streaming:
                close = getattr(response, "close", None)
                if callable(close):
                    close()

    def generate_json(self, prompt, system_prompt=None, temperature=0.7, max_tokens=16384, reasoning=None):
        return self.generate(prompt, system_prompt=system_prompt, response_schema=dict,
                             temperature=temperature, max_tokens=max_tokens, reasoning=reasoning)

    def generate(self, prompt, system_prompt=None, response_schema=None, temperature=0.7, max_tokens=16384, reasoning=None):
        prompt = require_text_only(prompt)
        if system_prompt is not None:
            require_text_only(system_prompt)
        if not self.is_available():
            raise LLMAuthenticationError(self.name)
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 131072:
            raise ValueError("AgentRouter output token budget must be an integer from 1 to 131072")
        effort = self._reasoning_effort
        if reasoning is not None:
            if (not isinstance(reasoning, dict) or set(reasoning) != {"effort"}
                    or reasoning.get("effort") not in {"none", "low", "medium", "high"}):
                raise ValueError("Invalid AgentRouter reasoning effort")
            effort = reasoning["effort"]
        if self._api_style == "responses":
            endpoint = "/responses"
            payload = {"model": self._model,
                       "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                       "max_output_tokens": max_tokens, "stream": self.streaming,
                       "reasoning": {"effort": effort}}
            if system_prompt:
                payload["instructions"] = system_prompt
            if response_schema is not None:
                payload["text"] = {"format": {"type": "json_object"}}
            # These reasoning routes reject temperature on the Responses API.
        else:
            endpoint = "/chat/completions"
            messages = []
            if system_prompt:
                messages.append({"role": "system", "content": system_prompt})
            messages.append({"role": "user", "content": prompt})
            payload = {"model": self._model, "messages": messages, "temperature": temperature,
                       "max_tokens": max_tokens, "stream": self.streaming, "reasoning_effort": effort}
            if response_schema is not None:
                payload["response_format"] = {"type": "json_object"}
        try:
            response = requests.post(
                self._base_url + endpoint, json=payload,
                headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json",
                         "User-Agent": AGENTROUTER_USER_AGENT},
                timeout=self._timeout, allow_redirects=False,
                **({"stream": True} if self.streaming else {}),
            )
        except requests.exceptions.Timeout:
            error = LLMConnectionError(self.name, "Request timed out")
            error.category = "timeout"
            raise with_provider_diagnostics(error, {
                "http_status": None, "error_code": "REQUEST_TIMEOUT",
                "message": "No complete response before the client timeout; upstream outcome is uncertain",
                "api_style": self._api_style,
                "transport": "local_proxy" if self._api_style == "chat_completions" else "direct",
                "timeout_seconds": self._timeout,
            }) from None
        except requests.exceptions.RequestException:
            raise with_provider_diagnostics(LLMConnectionError(self.name, "Transport request failed"), {
                "http_status": None, "error_code": "TRANSPORT_FAILURE",
                "message": "No complete provider response was received; upstream outcome is uncertain",
                "api_style": self._api_style,
                "transport": "local_proxy" if self._api_style == "chat_completions" else "direct",
            }) from None
        status = response.status_code
        try:
            data = self._response_data(response)
            if not isinstance(data, dict) or data.get("error"):
                raise LLMResponseError("Malformed text completion envelope")
            if self._api_style == "responses":
                response_status = data.get("status")
                incomplete_reason = (data.get("incomplete_details") or {}).get("reason")
                if response_status == "incomplete" and incomplete_reason != "content_filter":
                    self._truncated(data, status, "incomplete")
                if incomplete_reason == "content_filter":
                    finish = "content_filter"
                elif response_status != "completed":
                    raise LLMResponseError("Responses operation did not complete")
                else:
                    finish = "stop"
                parts = [part for item in data["output"] if item.get("type") == "message"
                         and item.get("role") == "assistant" for part in item.get("content", [])]
                if any(part.get("type") == "refusal" for part in parts):
                    finish = "content_filter"
                content = "".join(part["text"] for part in parts if part.get("type") == "output_text")
            else:
                choice = data["choices"][0]
                finish = choice.get("finish_reason")
                content = choice["message"].get("content")
            if finish in {"length", "max_tokens", "max_output_tokens"}:
                self._truncated(data, status, finish)
            if finish == "content_filter":
                error = LLMResponseError("Response blocked by provider content policy")
                error.category = "content_moderation"
                raise error
            if not isinstance(content, str) or not content.strip():
                raise LLMResponseError("Empty text completion")
            returned_model = data.get("model")
            if (self._api_style == "responses" or returned_model) and returned_model != self._model:
                raise LLMResponseError("Response model does not match the locked canonical model")
            usage = _normalize_usage(data.get("usage"), responses=self._api_style == "responses")
        except LLMResponseError as exc:
            if not getattr(exc, "diagnostics", None):
                from app.content.exceptions import safe_provider_text
                raise with_provider_diagnostics(exc, {"http_status": status,
                    "error_code": "INVALID_COMPLETION", "message": safe_provider_text(exc.reason, secrets=(self._api_key,)),
                    "api_style": self._api_style, "transport": "local_proxy" if self._api_style == "chat_completions" else "direct"}) from None
            raise
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMResponseError("Malformed text completion envelope") from None
        return LLMResponse(
            content=content, provider=self.name, model=self._model, usage=usage or None,
            # Raw responses and unvalidated response IDs are deliberately withheld.
            metadata={"finish_reason": finish, "input_modality": "text",
                      "identity_verification": "declared", "image_payloads_sent": False,
                       "api_style": self._api_style, "http_status": status, "streaming": self.streaming,
                      "transport": "direct" if self._api_style == "responses" else "local_proxy"},
        )