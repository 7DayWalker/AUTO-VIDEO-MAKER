"""
AUTO Video Maker - OpenRouter LLM Provider

OpenRouter API integration for accessing multiple LLM models.

Phase 11C Batch 1 hardening: OpenRouter is the project's single AI API
gateway, so image generation is also performed through THIS provider (its
dedicated Image API, ``POST {base_url}/images``) instead of a second
competing client. Model selection stays configuration-driven: callers pass
an image-capable model slug (discovery: ``GET {base_url}/images/models``);
nothing here hardcodes a model.
"""

import base64
import json
import requests
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Type
from pydantic import BaseModel
import logging

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.config import (
    OPENROUTER_API_KEY,
    OPENROUTER_BASE_URL,
    OPENROUTER_MODEL,
    LLM_REQUEST_TIMEOUT,
)
from app.content.exceptions import (
    LLMAuthenticationError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMResponseError,
    LLMConnectionError,
    LLMModelUnavailableError,
    LLMOutputTruncatedError,
)

logger = logging.getLogger(__name__)

# Image generation is materially slower than chat completion (documented
# examples take up to ~90-120s). The image timeout therefore has its own
# floor; it is never shorter than the configured LLM timeout.
IMAGE_REQUEST_TIMEOUT_FLOOR = 300


def _normalize_usage(raw: Any) -> Optional[Dict[str, int]]:
    """Flatten OpenRouter's nested usage object into ``LLMResponse.usage: Dict[str, int]``.

    OpenRouter returns nested/fractional usage detail, e.g.::

        {"prompt_tokens": 12, "cost": 0.0006298,
         "prompt_tokens_details": {"cached_tokens": 0, "video_tokens": 0},
         "cost_details": {"upstream_inference_cost": 0.0}}

    ``LLMResponse.usage`` is a frozen AVM contract (``Dict[str, int]``) and is
    deliberately NOT loosened: this adapter-local normalization flattens every
    numeric leaf into a ``<parent>_<leaf>`` key so nested structures never fail
    Pydantic validation. Fractional values (e.g. USD ``cost``) are preserved as
    micro-units under ``<key>_micro`` so no real accounting evidence is lost or
    silently rounded to zero. Non-numeric leaves are dropped; missing usage maps
    to ``None``.
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
            elif isinstance(value, float):
                if value.is_integer():
                    flat[name] = int(value)
                else:
                    # Preserve fractional precision truthfully (micro-units).
                    flat[f"{name}_micro"] = int(round(value * 1_000_000))
            elif isinstance(value, dict):
                walk(value, name)

    walk(raw, "")
    return flat or None


@dataclass
class OpenRouterImageResult:
    """One verified image generation result from OpenRouter's Image API.

    ``image_bytes`` are the decoded image bytes (from ``data[0].b64_json``);
    ``usage`` carries token counts and the USD ``cost`` when reported.
    """
    image_bytes: bytes
    media_type: str
    model: str
    usage: Optional[Dict[str, Any]] = field(default=None)


class OpenRouterProvider(BaseLLMProvider):
    """
    OpenRouter LLM provider.
    
    Provides access to multiple LLM models through the OpenRouter API.
    """
    
    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        base_url: str = None,
        timeout: int = None,
    ):
        self._api_key = api_key or OPENROUTER_API_KEY
        self._model = model or OPENROUTER_MODEL
        self._base_url = (base_url or OPENROUTER_BASE_URL).rstrip("/")
        self._timeout = timeout or LLM_REQUEST_TIMEOUT
    
    @property
    def name(self) -> str:
        return "openrouter"
    
    @property
    def model(self) -> str:
        return self._model
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a response using OpenRouter API."""
        if not self._api_key:
            raise LLMAuthenticationError(self.name)
        
        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": prompt})
        
        # Build request payload
        payload = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        # Add response format for JSON if schema provided
        if response_schema:
            payload["response_format"] = {"type": "json_object"}
        
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/auto-video-maker",
            "X-Title": "AUTO Video Maker",
        }
        
        try:
            logger.debug(f"Calling OpenRouter API with model: {self._model}")
            response = requests.post(
                f"{self._base_url}/chat/completions",
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            
            # Handle HTTP errors
            if response.status_code == 401:
                raise LLMAuthenticationError(self.name)
            elif response.status_code == 402:
                raise LLMQuotaError(self.name)
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise LLMRateLimitError(
                    self.name,
                    int(retry_after) if retry_after else None
                )
            elif response.status_code >= 500:
                raise LLMConnectionError(
                    self.name,
                    f"Server error: {response.status_code}"
                )
            elif response.status_code != 200:
                if response.status_code == 404:
                    raise LLMModelUnavailableError()
                raise LLMResponseError(
                    f"HTTP {response.status_code}",
                    response.text[:500]
                )
            
            # Parse response
            data = response.json()
            
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                raise LLMResponseError(error_msg)
            
            if "choices" not in data or not data["choices"]:
                raise LLMResponseError("No choices in response", str(data)[:500])
            
            choice = data["choices"][0]
            finish_reason = choice.get("finish_reason", "")
            if finish_reason in {"length", "max_tokens"}:
                raise LLMOutputTruncatedError()
            if finish_reason == "content_filter":
                error = LLMResponseError("Response blocked due to content policy")
                error.category = "content_moderation"
                raise error
            content = choice.get("message", {}).get("content", "")
            if isinstance(content, list):
                content = "".join(part.get("text", "") for part in content if isinstance(part, dict) and part.get("type") == "text")
            if not content:
                raise LLMResponseError("Empty content in response")
            
            # Extract usage info
            usage = None
            if "usage" in data:
                usage = {
                    "prompt_tokens": data["usage"].get("prompt_tokens", 0),
                    "completion_tokens": data["usage"].get("completion_tokens", 0),
                    "total_tokens": data["usage"].get("total_tokens", 0),
                }
            
            logger.debug(f"OpenRouter response received: {len(content)} chars")
            
            return LLMResponse(
                content=content,
                provider=self.name,
                model=self._model,
                usage=usage,
                raw_response=data,
                metadata={"finish_reason": finish_reason},
            )
            
        except requests.exceptions.Timeout:
            raise LLMConnectionError(self.name, f"Request timed out after {self._timeout}s")
        except requests.exceptions.ConnectionError as e:
            raise LLMConnectionError(self.name, str(e))
        except (LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, 
                LLMResponseError, LLMConnectionError):
            raise
        except Exception as e:
            raise LLMResponseError(f"Unexpected error: {e}")
    
    def is_available(self) -> bool:
        """Check if OpenRouter is configured with an API key."""
        return bool(self._api_key)
    
    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a JSON response using OpenRouter's JSON mode."""
        # Add JSON instruction to system prompt
        json_system = system_prompt or ""
        if json_system:
            json_system += "\n\n"
        json_system += "You must respond with valid JSON only. No markdown, no explanation, just JSON."
        
        return self.generate(
            prompt=prompt,
            system_prompt=json_system,
            response_schema=BaseModel,  # Triggers JSON mode
            temperature=temperature,
            max_tokens=max_tokens,
        )

    def generate_image(
        self,
        prompt: str,
        *,
        model: str,
        aspect_ratio: Optional[str] = None,
        output_format: Optional[str] = None,
        quality: Optional[str] = None,
    ) -> OpenRouterImageResult:
        """Generate one image through OpenRouter's dedicated Image API.

        Contract (https://openrouter.ai/docs — Multimodal / Image Generation):

        - ``POST {base_url}/images`` with ``{"model": <image-capable slug>,
          "prompt": ..., "n": 1}`` plus optional ``aspect_ratio`` /
          ``output_format`` / ``quality``;
        - response: ``{"created": ..., "data": [{"b64_json": ...,
          "media_type": "image/png"}], "usage": {..., "cost": ...}}``.

        The model is ALWAYS caller/config supplied — this provider never
        assumes a particular model is image-capable (discovery lives at
        ``GET {base_url}/images/models``). Failures raise the same truthful
        LLM exception family as ``generate()``; nothing is fabricated.
        """
        if not self._api_key:
            raise LLMAuthenticationError(self.name)
        image_model = (model or "").strip()
        if not image_model:
            raise LLMResponseError(
                "No OpenRouter image model configured: set AVM_IMAGE_MODEL to "
                "an image-capable model slug (see GET /api/v1/images/models)."
            )
        if not (prompt or "").strip():
            raise LLMResponseError("Image prompt cannot be empty")

        payload: Dict[str, Any] = {"model": image_model, "prompt": prompt, "n": 1}
        if aspect_ratio:
            payload["aspect_ratio"] = aspect_ratio
        if output_format:
            payload["output_format"] = output_format
        if quality:
            payload["quality"] = quality

        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "HTTP-Referer": "https://github.com/auto-video-maker",
            "X-Title": "AUTO Video Maker",
        }

        try:
            logger.debug(
                "Calling OpenRouter Image API with model: %s", image_model
            )
            response = requests.post(
                f"{self._base_url}/images",
                headers=headers,
                json=payload,
                timeout=max(self._timeout, IMAGE_REQUEST_TIMEOUT_FLOOR),
            )

            if response.status_code == 401:
                raise LLMAuthenticationError(self.name)
            elif response.status_code == 402:
                raise LLMQuotaError(self.name)
            elif response.status_code == 429:
                retry_after = response.headers.get("Retry-After")
                raise LLMRateLimitError(
                    self.name,
                    int(retry_after) if retry_after else None
                )
            elif response.status_code >= 500:
                raise LLMConnectionError(
                    self.name,
                    f"Server error: {response.status_code}"
                )
            elif response.status_code != 200:
                raise LLMResponseError(
                    f"HTTP {response.status_code}",
                    response.text[:500]
                )

            data = response.json()
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                raise LLMResponseError(error_msg)

            items = data.get("data") or []
            b64_json = (items[0] or {}).get("b64_json") if items else None
            if not b64_json:
                raise LLMResponseError(
                    "OpenRouter image response contained no image data",
                    str(data)[:500],
                )
            try:
                image_bytes = base64.b64decode(b64_json, validate=True)
            except Exception as exc:  # noqa: BLE001 - truthful decode failure
                raise LLMResponseError(f"Invalid base64 image payload: {exc}")
            if not image_bytes:
                raise LLMResponseError("Decoded image payload is empty")

            media_type = str((items[0] or {}).get("media_type") or "")
            usage = data.get("usage")
            logger.debug(
                "OpenRouter image received: %d bytes (%s)",
                len(image_bytes), media_type or "unknown media type",
            )
            return OpenRouterImageResult(
                image_bytes=image_bytes,
                media_type=media_type,
                model=image_model,
                usage=usage if isinstance(usage, dict) else None,
            )

        except requests.exceptions.Timeout:
            raise LLMConnectionError(
                self.name, "Image request timed out"
            )
        except requests.exceptions.ConnectionError as e:
            raise LLMConnectionError(self.name, str(e))
        except (LLMAuthenticationError, LLMQuotaError, LLMRateLimitError,
                LLMResponseError, LLMConnectionError):
            raise
        except Exception as e:
            raise LLMResponseError(f"Unexpected error: {e}")

    def supports_multimodal(self) -> bool:
        return True

    def generate_multimodal(self, prompt: str, system_prompt=None, *, video=None, images=None, video_bytes=None, response_schema=None, temperature: float = 0.7, max_tokens: int = 4096):
        if not self._api_key:
            raise LLMAuthenticationError(self.name)
        import base64, pathlib as _pl, tempfile, subprocess, os as _os
        content_parts = [{"type": "text", "text": prompt}]
        src = None
        if video_bytes is not None:
            b64 = base64.b64encode(video_bytes).decode("ascii")
            content_parts.append({"type": "image_url", "image_url": {"url": f"data:video/mp4;base64,{b64}"}})
        elif video is not None and _os.path.isfile(video):
            src = video
            try:
                from app.assembly.ffmpeg import resolve_ffmpeg
                ff = resolve_ffmpeg()
                if ff:
                    import pathlib as _pp
                    tmpd = _pp.Path(tempfile.mkdtemp(prefix="or_vl_"))
                    duration_probe = None
                    try:
                        from app.assembly.ffmpeg import probe_media
                        duration_probe = probe_media(_pp.Path(video)).duration or 0
                    except: duration_probe = 0
                    fracs = [0.15, 0.40, 0.70, 0.92]
                    for fi, frac in enumerate(fracs):
                        ts = max(0.5, float(duration_probe) * frac) if duration_probe and duration_probe > 2 else fi * 2.0
                        out = tmpd / f"frame_{fi:02d}.jpg"
                        cmd = [ff, "-y", "-ss", f"{ts:.2f}", "-i", str(video), "-vframes", "1", "-vf", "scale=512:-2", str(out)]
                        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=20)
                        if out.is_file() and out.stat().st_size > 1000:
                            b64 = base64.b64encode(out.read_bytes()).decode("ascii")
                            content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                    import shutil as _sh
                    try: _sh.rmtree(tmpd, ignore_errors=True)
                    except: pass
            except: pass
        if images:
            for pp in images:
                try:
                    ib = _pl.Path(pp).read_bytes()
                    b64 = base64.b64encode(ib).decode("ascii")
                    content_parts.append({"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}})
                except: continue
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": content_parts})
        payload = {"model": self._model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens}
        if response_schema is not None:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Authorization": f"Bearer {self._api_key}", "Content-Type": "application/json", "HTTP-Referer": "https://github.com/auto-video-maker", "X-Title": "AUTO Video Maker"}
        resp = __import__("requests").post(f"{self._base_url}/chat/completions", headers=headers, json=payload, timeout=self._timeout)
        if resp.status_code == 401:
            raise LLMAuthenticationError(self.name)
        if resp.status_code == 429:
            raise LLMRateLimitError(self.name)
        if resp.status_code == 402:
            raise LLMQuotaError(self.name)
        if resp.status_code >= 500:
            raise LLMConnectionError(self.name, f"Server error: {resp.status_code}")
        if resp.status_code != 200:
            raise LLMResponseError(f"HTTP {resp.status_code}", resp.text[:500])
        data = resp.json()
        if "choices" not in data or not data["choices"]:
            raise LLMResponseError("No choices", str(data)[:500])
        content = data["choices"][0].get("message", {}).get("content", "")
        if isinstance(content, list):
            content = "".join(p.get("text","") for p in content if isinstance(p, dict))
        if not content:
            raise LLMResponseError("Empty multimodal content")
        usage = _normalize_usage(data.get("usage"))
        return LLMResponse(content=content, provider=self.name, model=self._model, usage=usage, raw_response=data)
