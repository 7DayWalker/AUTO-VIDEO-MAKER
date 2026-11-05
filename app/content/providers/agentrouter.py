"""AgentRouter's OpenAI-compatible TEXT-ONLY route through the local proxy.

Model names are gateway-declared identities, not independent attestation of
upstream weights. Images belong exclusively to the separate Gemini observer.
No request/response bodies, transport URLs or credentials enter diagnostics.
"""
from __future__ import annotations

import math
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
DEFAULT_AGENTROUTER_BASE_URL = "http://127.0.0.1:8318/v1"
_MEDIA_TEXT = re.compile(
    r"data:(?:image|video|audio)/|\b(?:image_url|input_image|inline_data|video_url)\b|"
    r"(?<![\w/+])[A-Za-z0-9+/]{1024,}={0,2}(?![\w/+])", re.I,
)


def require_text_only(value):
    """Fail closed before networking on binary/content-part/base64 input."""
    if not isinstance(value, str) or _MEDIA_TEXT.search(value):
        raise ValueError("AgentRouter accepts pure text only; media payloads are forbidden")
    return value


class AgentRouterProvider(BaseLLMProvider):
    def __init__(self, api_key=None, base_url=None, model=None, timeout=None):
        self._api_key = os.getenv("AGENTROUTER_API_KEY", "") if api_key is None else api_key
        self._base_url = (base_url or os.getenv("AGENTROUTER_BASE_URL") or DEFAULT_AGENTROUTER_BASE_URL).rstrip("/")
        self._model = model or os.getenv("AGENTROUTER_MODEL") or AGENTROUTER_MODELS[0]
        if self._model not in AGENTROUTER_MODELS:
            raise LLMModelUnavailableError()
        try:
            parsed = urlsplit(self._base_url)
            valid = (parsed.scheme in {"http", "https"} and parsed.hostname
                     and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment)
            self._timeout = float(timeout if timeout is not None else os.getenv("AGENTROUTER_REQUEST_TIMEOUT", "300"))
        except (ValueError, TypeError):
            raise ValueError("Invalid AgentRouter endpoint or timeout configuration") from None
        if not valid or not math.isfinite(self._timeout) or not 0 < self._timeout <= 300:
            raise ValueError("Invalid AgentRouter endpoint or timeout configuration")
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
                self._base_url + "/chat/completions", json=payload,
                headers={"Authorization": "Bearer " + self._api_key, "Content-Type": "application/json"},
                timeout=self._timeout,
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
            choice = data["choices"][0]
            finish = choice.get("finish_reason")
            if finish in {"length", "max_tokens", "max_output_tokens"}:
                raise LLMOutputTruncatedError()
            if finish == "content_filter":
                error = LLMResponseError("Response blocked by provider content policy")
                error.category = "content_moderation"
                raise error
            content = choice["message"].get("content")
            if not isinstance(content, str) or not content.strip():
                raise LLMResponseError("Empty text completion")
            returned_model = data.get("model")
            if returned_model and returned_model != self._model:
                raise LLMResponseError("Response model does not match the locked canonical model")
            raw_usage = data.get("usage") or {}
            usage = {key: value for key, value in raw_usage.items()
                     if isinstance(value, int) and not isinstance(value, bool)} if isinstance(raw_usage, dict) else {}
        except (ValueError, KeyError, IndexError, TypeError, AttributeError):
            raise LLMResponseError("Malformed text completion envelope") from None
        return LLMResponse(
            content=content, provider=self.name, model=self._model, usage=usage or None,
            # Raw responses and unvalidated response IDs are deliberately withheld.
            metadata={"finish_reason": finish, "input_modality": "text",
                      "identity_verification": "declared", "image_payloads_sent": False},
        )