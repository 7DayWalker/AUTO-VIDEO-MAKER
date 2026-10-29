"""
AVM Step 4 -- CodeCraft AI Brain Provider Adapter
OpenAI-compatible POST {base_url}/chat/completions

TRACK 9 TASK C -- VERIFIED CAPABILITIES (live discovery, not inference)
-----------------------------------------------------------------------
Established against the real API (``https://codecraftapi.com/v1``):

* ``GET /models`` returns 33 models WITH genuine capability metadata:
  ``architecture.input_modalities`` and a ``capabilities`` list that includes
  ``vision`` for image-capable entries. This metadata is USED here rather than
  guessing from names.

* IMAGE INPUT IS REAL. OpenAI-style ``image_url`` content parts are accepted
  and a real frame from the real source film was described accurately. The
  previous "text-only" classification was an ADAPTER LIMITATION, not a provider
  limitation.

* NATIVE VIDEO: ``video_url`` content parts were accepted and a temporal
  discrimination test (two clips identical except for colour ORDER) was answered
  correctly for both, proving multi-frame/temporal ingestion at the gateway.
  It is NOT advertised as a stable capability because the route returns
  intermittent 5xx; the supported route is frame-based visual analysis.

* NO BALANCE ENDPOINT: ``/balance``, ``/credits``, ``/usage``, ``/account`` and
  ``/me`` all return 404. Remaining credit is therefore UNKNOWN and is never
  reported as available.

* GATEWAY FLAKINESS: valid payloads intermittently return 500 ``api_error`` /
  502. Identical payloads succeed on retry, so transient 5xx is retried while
  explicit 4xx (auth/quota/invalid request) is treated as authoritative.

* MODEL IDENTITY IS NOT PROVABLE per model: the API echoes back whatever
  ``model`` was requested, and a declared 2B text-only model (``gemma-2-2b``)
  both solved a frontier reasoning task and described a real image. CodeCraft
  bindings are therefore registered with ``identity_verification='declared'``
  and never as a route for a first-party canonical model.
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple, Type

import requests
from pydantic import BaseModel

from app.content.config import LLM_REQUEST_TIMEOUT
from app.content.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMResponseError,
)
from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.providers.fallback_policy import CategoryMixin, FailureCategory

logger = logging.getLogger(__name__)

CODECRAFT_API_KEY_ENV = "CODECRAFT_API_KEY"
CODECRAFT_BASE_URL_ENV = "CODECRAFT_BASE_URL"
CODECRAFT_TEXT_MODEL_ENV = "CODECRAFT_TEXT_MODEL"
CODECRAFT_REQUEST_TIMEOUT_ENV = "CODECRAFT_REQUEST_TIMEOUT"
DEFAULT_CODECRAFT_BASE_URL = "https://api.codecraft.ai/v1"
DEFAULT_CODECRAFT_TEXT_MODEL = "qwen3.8-27b"

#: Transient-5xx retry policy for the CodeCraft gateway. Explicit 4xx is NEVER
#: retried: an authoritative refusal is evidence and must surface truthfully.
CODECRAFT_TRANSIENT_RETRIES = 3
CODECRAFT_RETRY_BACKOFF_SECONDS = 2.0

#: Per-request image budget. Keeps payloads inside real provider limits.
CODECRAFT_MAX_IMAGES_PER_REQUEST = 8
CODECRAFT_MAX_IMAGE_BYTES = 4 * 1024 * 1024

#: REASONING-MODEL TOKEN FLOOR.
#:
#: CodeCraft's vision models are REASONING models: they spend completion tokens
#: on hidden reasoning before emitting any visible content. Live evidence -- a
#: request with ``max_tokens=200`` returned ``choices[0].message.content = null``
#: with ``finish_reason='length'`` and ``completion_tokens_details
#: .reasoning_tokens = 197`` (every token consumed by reasoning). The SAME
#: request with ``max_tokens=3000`` completed normally in 239 tokens
#: (184 reasoning + 55 content).
#:
#: A caller-supplied budget below this floor is raised to it for models known to
#: reason, because a starved budget is a REQUEST CONFIGURATION failure, not a
#: model capability failure. The adjustment is recorded in the response metadata
#: so the effective budget is never misrepresented.
CODECRAFT_REASONING_MODELS: frozenset = frozenset({
    "gpt-5.6-sol", "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-luna", "gpt-5.6-terra",
    "claude-opus-5.5", "claude-opus-5", "claude-opus-4.6", "claude-opus-4.7",
    "claude-opus-4.8", "claude-fable-5", "claude-fable-5.1",
    "claude-mythos-preview", "claude-sonnet-5",
    "gemini-3.1-pro", "gemini-3.6-flash", "gemini-3.7-flash",
    "muse-spark-1.1", "kimi-k3", "glm-5.3", "qwen3.8-max", "qwen3.7-max",
    "qwen3.8-27b", "deepseek-v4-pro-0813", "deepseek-v4-pro-max",
})
CODECRAFT_REASONING_MIN_TOKENS = 2048


#: Models whose IMAGE input AVM has actually exercised against the real API
#: (real source frame described accurately). Capability is model-specific:
#: this adapter does NOT mark every CodeCraft model vision-capable.
#: Evidence: _real_smoke_wk/codecraft_per_model_verify.json
CODECRAFT_VERIFIED_VISION_MODELS: frozenset = frozenset({
    "gpt-5.6-sol",
    "claude-opus-5.5",
    "claude-opus-5",
})

#: Models CodeCraft's own /models metadata declares as image-capable but which
#: AVM has not separately exercised. Declared, not verified.
CODECRAFT_DECLARED_VISION_MODELS: frozenset = frozenset({
    "claude-fable-5", "claude-fable-5.1", "claude-mythos-preview",
    "claude-sonnet-5", "claude-opus-4.6", "claude-opus-4.7", "claude-opus-4.8",
    "gpt-5.5", "gpt-5.5-pro", "gpt-5.6-luna", "gpt-5.6-terra",
    "gemini-3.1-pro", "gemini-3.6-flash", "gemini-3.7-flash",
    "muse-spark-1.1", "kimi-k3", "glm-5.3", "qwen3.8-max", "qwen3.7-max",
})



def _trim_key(text: str) -> str:
    if not text:
        return text
    return re.sub(r"sk-[A-Za-z0-9\-_]{8,}", "sk-***", text)


class CodeCraftAuthenticationError(LLMAuthenticationError, CategoryMixin):
    category = FailureCategory.AUTHENTICATION


class CodeCraftQuotaError(LLMQuotaError, CategoryMixin):
    category = FailureCategory.QUOTA


class CodeCraftRateLimitError(LLMRateLimitError, CategoryMixin):
    category = FailureCategory.RATE_LIMIT


class CodeCraftConnectionError(LLMConnectionError, CategoryMixin):
    category = FailureCategory.NETWORK


class CodeCraftResponseError(LLMResponseError, CategoryMixin):
    category = FailureCategory.RESPONSE_MALFORMED


class CodeCraftServerBusyError(LLMConnectionError, CategoryMixin):
    category = FailureCategory.PROVIDER_5XX


class CodeCraftInvalidRequestError(LLMResponseError, CategoryMixin):
    category = FailureCategory.INVALID_REQUEST


def codecraft_api_key_from_env() -> str:
    return (os.getenv(CODECRAFT_API_KEY_ENV) or "").strip()


def codecraft_runtime_enabled() -> bool:
    """Deployment quarantine independent of credentials and canonical identity.

    Existing isolated integrations remain backward-compatible when no switch
    is supplied. Deployments explicitly set 0 to quarantine this gateway and
    must opt back in with 1; a present API key never overrides the quarantine.
    """
    return (os.getenv("AVM_ENABLE_CODECRAFT", "1") or "").strip() == "1"


def _normalize_usage(raw: Any) -> Optional[Dict[str, int]]:
    """Flatten CodeCraft's nested usage into the frozen ``Dict[str, int]`` contract.

    CodeCraft returns nested detail, e.g.::

        {"prompt_tokens": 599, "completion_tokens": 239, "total_tokens": 838,
         "completion_tokens_details": {"reasoning_tokens": 184},
         "prompt_tokens_details": {"cached_tokens": 497}}

    ``LLMResponse.usage`` is a frozen AVM contract (``Dict[str, int]``) and is
    deliberately NOT loosened. This follows the SAME established pattern as
    ``app.content.providers.openrouter._normalize_usage``: every numeric leaf is
    flattened to ``<parent>_<leaf>`` so nested structures never fail Pydantic
    validation and no real accounting evidence is silently dropped.

    ``reasoning_tokens`` is preserved (not discarded) because it is operationally
    significant here: it is what explains a null-content, ``length``-terminated
    response from a reasoning model.
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
                continue
            if isinstance(value, int):
                flat[name] = value
            elif isinstance(value, float):
                if value.is_integer():
                    flat[name] = int(value)
                else:
                    flat[f"{name}_micro"] = int(round(value * 1_000_000))
            elif isinstance(value, dict):
                walk(value, name)

    walk(raw, "")
    return flat or None


def codecraft_base_url_from_env() -> str:
    return ((os.getenv(CODECRAFT_BASE_URL_ENV) or "").strip() or DEFAULT_CODECRAFT_BASE_URL).rstrip("/")


def _encode_image(data: bytes, mime: str = "image/jpeg") -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode('ascii')}"


def _build_image_parts(images) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """Build OpenAI-style ``image_url`` parts from frames.

    Accepts, per entry:
      * ``str``/``Path``              -- image file path
      * ``bytes``                    -- raw image bytes
      * ``(timestamp_seconds, path)``-- TIMESTAMPED frame (preferred for Pro)
      * ``{"timestamp": t, "path"/"bytes": ...}``

    Frames exceeding the per-image byte budget are SKIPPED (and reported as
    skipped) rather than silently truncating the request. Returns
    ``(parts, frame_metadata)``; frame metadata preserves timestamps so source
    grounding survives into the execution record.
    """
    if images is None:
        return [], []
    if isinstance(images, (str, Path, bytes)):
        images = [images]

    parts: List[Dict[str, Any]] = []
    meta: List[Dict[str, Any]] = []
    for entry in list(images)[:CODECRAFT_MAX_IMAGES_PER_REQUEST]:
        timestamp: Optional[float] = None
        payload: Optional[bytes] = None
        mime = "image/jpeg"
        label: Optional[str] = None

        if isinstance(entry, dict):
            timestamp = entry.get("timestamp", entry.get("timestamp_seconds"))
            if entry.get("bytes") is not None:
                payload = entry["bytes"]
            elif entry.get("path"):
                label = str(entry["path"])
        elif isinstance(entry, (tuple, list)) and len(entry) == 2:
            first, second = entry
            if isinstance(first, (int, float)):
                timestamp = float(first)
                if isinstance(second, bytes):
                    payload = second
                else:
                    label = str(second)
            else:
                label = str(first)
        elif isinstance(entry, bytes):
            payload = entry
        else:
            label = str(entry)

        if payload is None and label:
            path = Path(label)
            if not path.is_file():
                continue
            payload = path.read_bytes()
            guessed = mimetypes.guess_type(path.name)[0]
            if guessed and guessed.startswith("image/"):
                mime = guessed

        if not payload:
            continue
        if len(payload) > CODECRAFT_MAX_IMAGE_BYTES:
            meta.append({
                "timestamp_seconds": timestamp,
                "skipped": True,
                "reason": f"frame exceeds {CODECRAFT_MAX_IMAGE_BYTES} byte limit",
            })
            continue

        parts.append({"type": "image_url", "image_url": {"url": _encode_image(payload, mime)}})
        meta.append({
            "timestamp_seconds": float(timestamp) if timestamp is not None else None,
            "bytes": len(payload),
            "mime": mime,
        })
    return parts, meta



class CodeCraftProvider(BaseLLMProvider):
    """CodeCraft provider (OpenAI-compatible chat/completions)."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout: Optional[int] = None,
    ) -> None:
        self._api_key = (api_key if api_key is not None else codecraft_api_key_from_env()).strip()
        self._model = (model or os.getenv(CODECRAFT_TEXT_MODEL_ENV, "") or DEFAULT_CODECRAFT_TEXT_MODEL).strip()
        self._base_url = (base_url or os.getenv(CODECRAFT_BASE_URL_ENV, "") or DEFAULT_CODECRAFT_BASE_URL).rstrip("/")
        raw_timeout = timeout if timeout is not None else (os.getenv(CODECRAFT_REQUEST_TIMEOUT_ENV, "") or LLM_REQUEST_TIMEOUT)
        self._timeout = int(raw_timeout)  # type: ignore[arg-type]
        #: Records any max_tokens floor applied to the CURRENT request, so the
        #: effective budget is never misrepresented in the response metadata.
        self._budget_adjustment: Optional[str] = None

    @property
    def name(self) -> str:
        return "codecraft"

    @property
    def model(self) -> str:
        return self._model

    def is_available(self) -> bool:
        return codecraft_runtime_enabled() and bool(self._api_key)

    # ---- TASK C2: model-specific image capability ------------------------

    def supports_multimodal(self) -> bool:
        """Image input, decided PER MODEL -- never blanket-true for CodeCraft.

        Only models whose image input is verified or explicitly declared by the
        provider's own capability metadata qualify. A text-only model such as
        ``qwen3.8-27b`` returns False even though the gateway would answer.
        """
        return self._model in CODECRAFT_VERIFIED_VISION_MODELS or (
            self._model in CODECRAFT_DECLARED_VISION_MODELS
        )

    def supports_native_video(self) -> bool:
        """CodeCraft native VIDEO ingestion is NOT offered by this adapter.

        A ``video_url`` content part was accepted by the gateway and passed a
        temporal-order discrimination test, but the route returns intermittent
        5xx and per-model identity is unprovable. AVM therefore uses frame-based
        visual analysis and does not claim native video support.
        """
        return False

    def vision_capability_source(self) -> Optional[str]:
        """Provenance of this model's image capability, or None."""
        if self._model in CODECRAFT_VERIFIED_VISION_MODELS:
            return "verified_real_request"
        if self._model in CODECRAFT_DECLARED_VISION_MODELS:
            return "provider_declared_metadata"
        return None

    def generate_multimodal(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        *,
        video=None,
        images=None,
        video_bytes=None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Image (frame-based) multimodal request through the existing path.

        ``images`` accepts file paths, raw bytes, or ``(timestamp, path)`` pairs
        so Summary Pro can supply TIMESTAMPED frames from bounded source windows.

        Native video is refused truthfully: a ``video`` argument is never
        silently downgraded to a text-only request pretending to have seen it.
        """
        if not self._api_key:
            raise CodeCraftAuthenticationError(self.name)
        if not self.supports_multimodal():
            raise CodeCraftInvalidRequestError(
                f"CodeCraft model '{self._model}' is not image-capable; "
                f"refusing to send a multimodal request"
            )
        if (video or video_bytes) and not images:
            raise CodeCraftInvalidRequestError(
                "CodeCraft adapter does not perform native video ingestion; "
                "supply extracted frames via images= for frame-based analysis"
            )

        parts, frame_meta = _build_image_parts(images)
        if not parts:
            raise CodeCraftInvalidRequestError(
                "no usable image frames supplied for a multimodal request"
            )

        messages: List[Dict[str, Any]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        content: List[Dict[str, Any]] = [{"type": "text", "text": prompt}]
        content.extend(parts)
        messages.append({"role": "user", "content": content})

        max_tokens, adjustment = self._effective_max_tokens(max_tokens)
        self._budget_adjustment = adjustment
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}

        raw = self._post(payload)
        response = self._to_response(raw)
        meta = dict(response.metadata or {})
        # TRUTHFUL execution labelling: frame-based, never native video.
        meta["input_modality"] = "frame_sequence"
        meta["visual_analysis_method"] = "frame_based_visual_analysis"
        meta["native_video_ingestion"] = False
        meta["frame_count"] = len(parts)
        meta["vision_capability_source"] = self.vision_capability_source()
        if frame_meta:
            meta["frame_timestamps"] = frame_meta
        return response.model_copy(update={"metadata": meta})


    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
        *,
        json_mode: bool = False,
    ) -> LLMResponse:
        if not self._api_key:
            raise CodeCraftAuthenticationError(self.name)
        use_json = bool(json_mode or response_schema is not None)
        messages: list[Dict[str, str]] = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        max_tokens, adjustment = self._effective_max_tokens(max_tokens)
        self._budget_adjustment = adjustment
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if use_json:
            payload["response_format"] = {"type": "json_object"}
        raw = self._post(payload)
        return self._to_response(raw)

    def _effective_max_tokens(self, requested: int) -> Tuple[int, Optional[str]]:
        """Raise a starved budget for reasoning models. Returns (value, reason)."""
        if self._model in CODECRAFT_REASONING_MODELS and requested < CODECRAFT_REASONING_MIN_TOKENS:
            return CODECRAFT_REASONING_MIN_TOKENS, (
                f"raised max_tokens {requested} -> {CODECRAFT_REASONING_MIN_TOKENS}: "
                f"'{self._model}' is a reasoning model that spends completion "
                f"tokens on hidden reasoning before emitting content"
            )
        return requested, None

    def _to_response(self, raw: Dict[str, Any]) -> LLMResponse:
        """Parse an OpenAI-compatible completion body into ``LLMResponse``.

        Single parsing path shared by text and multimodal requests, so the
        model/provider identity recorded on the response is always the one this
        adapter actually used.
        """
        choices = raw.get("choices") or []
        first = choices[0] if choices and isinstance(choices[0], dict) else {}
        finish_reason = first.get("finish_reason")
        usage = _normalize_usage(raw.get("usage"))
        reasoning_tokens = None
        if isinstance(usage, dict):
            reasoning_tokens = (
                usage.get("completion_tokens_details_reasoning_tokens")
                or usage.get("reasoning_tokens")
            )

        try:
            content = ""
            if choices:
                content = ((first.get("message") or {}).get("content")) or ""
            if not content:
                content = str(raw.get("content") or "")
            if not content:
                # TRUTHFUL diagnosis rather than a bare "malformed response".
                if reasoning_tokens:
                    raise CodeCraftResponseError(
                        f"no visible content: all {reasoning_tokens} completion tokens "
                        f"were consumed by reasoning (finish_reason='{finish_reason}'). "
                        f"This is a request budget failure for a reasoning model -- "
                        f"increase max_tokens."
                    )
                raise KeyError("missing choices[0].message.content")
        except CodeCraftResponseError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise CodeCraftResponseError(f"malformed response: {exc}", str(raw)[:500])

        request_id = raw.get("id") if isinstance(raw.get("id"), str) else None
        metadata: Dict[str, Any] = {}
        if request_id:
            metadata["request_id"] = request_id
        if reasoning_tokens is not None:
            metadata["reasoning_tokens"] = reasoning_tokens
        if finish_reason:
            metadata["finish_reason"] = finish_reason
        # Record the model id the provider ECHOED alongside the id we requested.
        echoed = raw.get("model") if isinstance(raw.get("model"), str) else None
        if echoed:
            metadata["provider_reported_model"] = echoed
            if echoed != self._model:
                # Never hidden: a substitution is recorded as real evidence.
                metadata["provider_model_mismatch"] = True
        if self._budget_adjustment:
            metadata["max_tokens_adjustment"] = self._budget_adjustment
        return LLMResponse(
            content=content,
            provider=self.name,
            model=self._model,
            usage=usage,
            raw_response=raw,
            metadata=metadata or None,
        )



    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        return self.generate(prompt=prompt, system_prompt=system_prompt, temperature=temperature, max_tokens=max_tokens, json_mode=True)

    def _post(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """POST to chat/completions, retrying ONLY transient gateway 5xx.

        The CodeCraft gateway intermittently answers valid payloads with
        500 ``api_error`` / 502; identical payloads succeed on retry (proven
        live). Explicit 4xx -- authentication, quota, invalid request -- is
        authoritative evidence and is raised immediately without retry.
        """
        if not codecraft_runtime_enabled():
            raise CodeCraftInvalidRequestError("CodeCraft runtime route is quarantined; enable it explicitly only after verifying service recovery.")
        url = f"{self._base_url}/chat/completions"
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json"}
        attempts = max(1, CODECRAFT_TRANSIENT_RETRIES + 1)
        last_transient: Optional[BaseException] = None

        for attempt in range(attempts):
            try:
                response = requests.post(url, headers=headers, json=payload, timeout=self._timeout)
            except requests.exceptions.Timeout:
                raise CodeCraftConnectionError(self.name, "request timed out")
            except requests.exceptions.ConnectionError as exc:
                raise CodeCraftConnectionError(self.name, str(exc))

            if response.status_code == 200:
                try:
                    return response.json()
                except ValueError as exc:
                    raise CodeCraftResponseError(f"non-JSON response body: {exc}")

            # 4xx (and anything non-transient) raises immediately.
            if not (500 <= int(response.status_code) < 600):
                self._raise_for_status(response)

            try:
                self._raise_for_status(response)
            except Exception as exc:  # noqa: BLE001
                last_transient = exc
                if attempt >= attempts - 1:
                    raise
                logger.debug(
                    "codecraft transient HTTP %s on attempt %s/%s; retrying",
                    response.status_code, attempt + 1, attempts,
                )
                time.sleep(CODECRAFT_RETRY_BACKOFF_SECONDS * (attempt + 1))

        if last_transient is not None:
            raise last_transient
        raise CodeCraftConnectionError(self.name, "request failed with no response")

    # ---- TASK B3/C1: authoritative provider model discovery --------------

    def list_models(self) -> List[Dict[str, Any]]:
        """Fetch CodeCraft's authoritative model list (``GET /models``).

        Returns raw entries including ``architecture.input_modalities`` and the
        ``capabilities`` list. Network call -- callers decide when to invoke it.
        """
        if not codecraft_runtime_enabled():
            raise CodeCraftInvalidRequestError("CodeCraft runtime route is quarantined; model discovery is disabled.")
        if not self._api_key:
            raise CodeCraftAuthenticationError(self.name)
        url = f"{self._base_url}/models"
        headers = {"Authorization": f"Bearer {self._api_key}"}
        try:
            response = requests.get(url, headers=headers, timeout=self._timeout)
        except requests.exceptions.Timeout:
            raise CodeCraftConnectionError(self.name, "model list request timed out")
        except requests.exceptions.ConnectionError as exc:
            raise CodeCraftConnectionError(self.name, str(exc))
        if response.status_code != 200:
            self._raise_for_status(response)
        try:
            body = response.json()
        except ValueError as exc:
            raise CodeCraftResponseError(f"non-JSON model list: {exc}")
        data = body.get("data") if isinstance(body, dict) else body
        return [item for item in (data or []) if isinstance(item, dict)]

    @staticmethod
    def model_capability_metadata_present(entry: Dict[str, Any]) -> bool:
        """Whether a ``/models`` entry carries ANY capability metadata at all.

        This is a TRI-STATE guard, not a capability check. CodeCraft's
        ``/models`` payload has been observed in two different shapes:

        * RICH (earlier): ``architecture.input_modalities``, ``capabilities``,
          ``pricing``, ``context_length``, ``owned_by='CodeCraft API'``.
        * BARE (current): only ``id``, ``object``, ``created``,
          ``owned_by='cleanapis'`` -- no capability fields whatsoever.

        With the BARE shape, ``model_declares_image_input()`` correctly returns
        False because nothing is declared -- but that is ABSENCE OF EVIDENCE, not
        evidence of absence. Callers must use this helper to tell the two apart
        instead of reading "no declared image input" as "text-only".
        """
        arch = entry.get("architecture")
        if isinstance(arch, dict) and arch.get("input_modalities"):
            return True
        caps = entry.get("capabilities")
        if isinstance(caps, (list, tuple)) and caps:
            return True
        return False

    @staticmethod
    def model_declares_image_input(entry: Dict[str, Any]) -> bool:
        """Whether a ``/models`` entry declares IMAGE input.

        Uses the provider's own capability metadata -- never the model name.
        Returns False when NO metadata is present; pair with
        ``model_capability_metadata_present()`` to distinguish that case.
        """
        arch = entry.get("architecture") or {}
        mods = arch.get("input_modalities") or []
        if isinstance(mods, (list, tuple)) and "image" in [str(m).lower() for m in mods]:
            return True
        caps = entry.get("capabilities") or []
        if isinstance(caps, (list, tuple)) and "vision" in [str(c).lower() for c in caps]:
            return True
        return False


    @staticmethod
    def model_declares_native_video(entry: Dict[str, Any]) -> bool:
        """Whether a ``/models`` entry declares NATIVE VIDEO input.

        Live discovery found ZERO CodeCraft models declaring video input, so
        native video is never inferred from a vision label or an upload control.
        """
        arch = entry.get("architecture") or {}
        mods = arch.get("input_modalities") or []
        return isinstance(mods, (list, tuple)) and "video" in [str(m).lower() for m in mods]


    def _raise_for_status(self, response: Any) -> None:
        status = response.status_code
        code = ""
        message = ""
        try:
            body = response.json()
            error = body.get("error") or {}
            if isinstance(error, str):
                message = error
            else:
                code = str(error.get("code") or "")
                message = str(error.get("message") or "")
                if not message:
                    message = str(body)[:200]
        except ValueError:
            message = (response.text or "")[:200]
        lowered = f"{code} {message}".lower()
        flat = lowered.replace("_", "").replace(".", "")
        if status in (401, 403) or "invalidapikey" in flat or "authentication" in flat or "unauthorized" in flat:
            raise CodeCraftAuthenticationError(self.name)
        if status == 429 or "throttling" in flat or "ratelimit" in flat or "rate limit" in flat:
            ra = None
            try:
                ra = int(response.headers.get("Retry-After")) if response.headers.get("Retry-After") else None
            except ValueError:
                ra = None
            raise CodeCraftRateLimitError(self.name, ra)
        if "arrearage" in flat or "allocationquota" in flat or "quota" in flat or status == 402:
            raise CodeCraftQuotaError(self.name)
        if status >= 500 or code.lower() in ("systemerror", "internalerror", "serviceunavailable"):
            raise CodeCraftServerBusyError(self.name, f"HTTP {status}: {_trim_key(message)}")
        if 400 <= status < 500:
            raise CodeCraftInvalidRequestError(f"HTTP {status}: {_trim_key(message)}")
        raise CodeCraftConnectionError(self.name, f"unexpected HTTP {status}: {_trim_key(message)}")
