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
from urllib.parse import urlsplit

import requests

from app.content.exceptions import (
    LLMAuthenticationError, LLMConnectionError, LLMModelUnavailableError,
    LLMOutputTruncatedError, LLMQuotaError, LLMRateLimitError, LLMResponseError,
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
    def __init__(self, api_key=None, base_url=None, model=None, timeout=None):
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
        if not valid or not math.isfinite(self._timeout) or not 0 < self._timeout <= 300:
            raise ValueError("Invalid AgentRouter endpoint or timeout configuration")
        if not parsed.path or parsed.path == "/":
            self._base_url += "/v1"
        # Only these endpoint/transport pairs passed account-tier verification.
        # No speculative Messages/remote Chat readiness is advertised.
        self._api_style = "chat_completions" if loopback else "responses"
        self._reasoning_effort = os.getenv("AGENTROUTER_REASONING_EFFORT", "none")
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

    def generate_json(self, prompt, system_prompt=None, temperature=0.7, max_tokens=4096):
        return self.generate(prompt, system_prompt=system_prompt, response_schema=dict,
                             temperature=temperature, max_tokens=max_tokens)

    def generate(self, prompt, system_prompt=None, response_schema=None, temperature=0.7, max_tokens=4096):
        prompt = require_text_only(prompt)
        if system_prompt is not None:
            require_text_only(system_prompt)
        if not self.is_available():
            raise LLMAuthenticationError(self.name)
        if isinstance(max_tokens, bool) or not isinstance(max_tokens, int) or not 1 <= max_tokens <= 131072:
            raise ValueError("AgentRouter output token budget must be an integer from 1 to 131072")
        if self._api_style == "responses":
            endpoint = "/responses"
            payload = {"model": self._model,
                       "input": [{"role": "user", "content": [{"type": "input_text", "text": prompt}]}],
                       "max_output_tokens": max_tokens, "stream": False,
                       "reasoning": {"effort": self._reasoning_effort}}
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
                       "max_tokens": max_tokens, "stream": False, "reasoning_effort": self._reasoning_effort}
            if response_schema is not None:
                payload["response_format"] = {"type": "json_object"}
        try:
            response = requests.post(
                self._base_url + endpoint, json=payload,
                headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json",
                         "User-Agent": AGENTROUTER_USER_AGENT},
                timeout=self._timeout, allow_redirects=False,
            )
        except requests.exceptions.Timeout:
            error = LLMConnectionError(self.name, "Request timed out")
            error.category = "timeout"
            raise error from None
        except requests.exceptions.RequestException:
            raise LLMConnectionError(self.name, "Transport request failed") from None
        status = response.status_code
        if status in (401, 403):
            raise LLMAuthenticationError(self.name)
        if status == 402:
            raise LLMQuotaError(self.name)
        if status == 429:
            raise LLMRateLimitError(self.name)
        if status >= 500:
            raise LLMConnectionError(self.name, f"Server error: {status}")
        if status in (400, 404):
            # Never include an upstream body: it can echo credentials/payloads.
            raise LLMModelUnavailableError()
        if status != 200:
            raise LLMResponseError(f"HTTP {status}")
        try:
            data = response.json()
            if not isinstance(data, dict) or data.get("error"):
                raise LLMResponseError("Malformed text completion envelope")
            if self._api_style == "responses":
                response_status = data.get("status")
                incomplete_reason = (data.get("incomplete_details") or {}).get("reason")
                if response_status == "incomplete" and incomplete_reason != "content_filter":
                    raise LLMOutputTruncatedError()
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
                raise LLMOutputTruncatedError()
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
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMResponseError("Malformed text completion envelope") from None
        return LLMResponse(
            content=content, provider=self.name, model=self._model, usage=usage or None,
            # Raw responses and unvalidated response IDs are deliberately withheld.
            metadata={"finish_reason": finish, "input_modality": "text",
                      "identity_verification": "declared", "image_payloads_sent": False,
                      "api_style": self._api_style, "http_status": status,
                      "transport": "direct" if self._api_style == "responses" else "local_proxy"},
        )