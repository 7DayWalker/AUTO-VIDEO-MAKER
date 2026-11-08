"""
AUTO Video Maker - Google Gemini LLM Provider

Google Gemini API integration for content generation.
"""

import json
import requests
from typing import Optional, Type
from pydantic import BaseModel
import logging
import time

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.config import (
    GEMINI_API_KEY,
    GEMINI_MODEL,
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
    LLMInvalidRequestError,
    LLMTimeoutError,
    provider_diagnostics,
    with_provider_diagnostics,
)

logger = logging.getLogger(__name__)


class GeminiProvider(BaseLLMProvider):
    """
    Google Gemini LLM provider.
    
    Provides access to Google's Gemini models through the Generative AI API.
    """
    
    def __init__(
        self,
        api_key: str = None,
        model: str = None,
        timeout: int = None,
    ):
        self._api_key = api_key or GEMINI_API_KEY
        self._model = model or GEMINI_MODEL
        self._timeout = timeout or LLM_REQUEST_TIMEOUT
        self._base_url = "https://generativelanguage.googleapis.com/v1beta"
    
    @property
    def name(self) -> str:
        return "gemini"
    
    @property
    def model(self) -> str:
        return self._model

    def _raise_for_status(self, response):
        """A 400 parameter error must never masquerade as missing vision."""
        if response.status_code == 200:
            return
        detail = provider_diagnostics(response, secrets=(self._api_key,))
        status = response.status_code
        message = (detail["error_code"] + " " + detail["message"]).lower()
        if status in (401, 403):
            error = LLMAuthenticationError(self.name)
        elif status == 402 or (status == 429 and any(word in message for word in ("quota", "billing", "daily limit"))):
            error = LLMQuotaError(self.name)
        elif status == 429:
            retry = (getattr(response, "headers", {}) or {}).get("Retry-After")
            error = LLMRateLimitError(self.name, int(retry) if str(retry).isdigit() else None)
        elif status in (408, 504):
            error = LLMTimeoutError(self.name, "Upstream request timed out")
        elif status >= 500:
            error = LLMConnectionError(self.name, f"Server error: {status}")
            error.category = "provider_5xx"
        elif status == 404 or (status == 400 and "model" in message and any(
            marker in message for marker in ("model is not found", "model not found", "not supported for generatecontent", "model does not support")
        )):
            error = LLMModelUnavailableError()
        elif status in (400, 413, 422):
            error = LLMInvalidRequestError("Provider rejected the request parameters")
        else:
            error = LLMResponseError(f"HTTP {status}")
        raise with_provider_diagnostics(error, detail)
    
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a response using Google Gemini API."""
        if not self._api_key:
            raise LLMAuthenticationError(self.name)
        
        # Build contents array
        contents = []
        
        # Add system instruction if provided
        system_instruction = None
        if system_prompt:
            system_instruction = {"parts": [{"text": system_prompt}]}
        
        # Add user message
        contents.append({
            "role": "user",
            "parts": [{"text": prompt}]
        })
        
        # Build request payload
        payload = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            }
        }
        
        # Add system instruction if provided
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        
        # Add JSON response format if schema provided
        if response_schema:
            payload["generationConfig"]["responseMimeType"] = "application/json"
            # Thinking and visible JSON share maxOutputTokens. Default high
            # thinking on Gemini 3 can spend 4K tokens before finishing one beat.
            if self._model.startswith("gemini-3"):
                payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "low"}
            elif self._model.startswith("gemini-2.5-flash"):
                payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        
        # Credentials must not appear in URLs/transport diagnostics.
        url = f"{self._base_url}/models/{self._model}:generateContent"
        
        headers = {
            "Content-Type": "application/json",
            "x-goog-api-key": self._api_key,
        }
        
        try:
            logger.debug(f"Calling Gemini API with model: {self._model}")
            response = requests.post(
                url,
                headers=headers,
                json=payload,
                timeout=self._timeout,
            )
            
            self._raise_for_status(response)
            
            # Parse response
            data = response.json()
            
            # Check for API errors
            if "error" in data:
                error_msg = data["error"].get("message", str(data["error"]))
                if "quota" in error_msg.lower():
                    raise LLMQuotaError(self.name)
                raise LLMResponseError(error_msg)
            
            # Check for blocked content
            if "promptFeedback" in data:
                feedback = data["promptFeedback"]
                if feedback.get("blockReason"):
                    error = LLMResponseError("Prompt blocked by provider content policy")
                    error.category = "content_moderation"
                    raise error
            
            # Extract content from candidates
            if "candidates" not in data or not data["candidates"]:
                raise LLMResponseError("No candidates in response", str(data)[:500])
            
            candidate = data["candidates"][0]
            
            # Check finish reason
            finish_reason = candidate.get("finishReason", "")
            if finish_reason == "MAX_TOKENS":
                raise LLMOutputTruncatedError()
            if finish_reason == "SAFETY":
                error = LLMResponseError("Response blocked due to safety filters")
                error.category = "content_moderation"
                raise error
            
            # Extract text content
            content_parts = candidate.get("content", {}).get("parts", [])
            if not content_parts:
                raise LLMResponseError("No content parts in response")
            
            content = "".join(
                part.get("text", "") for part in content_parts
                if isinstance(part, dict) and not part.get("thought")
            )
            if not content:
                raise LLMResponseError("Empty content in response")
            
            # Extract usage info
            usage = None
            if "usageMetadata" in data:
                usage_data = data["usageMetadata"]
                usage = {
                    "prompt_tokens": usage_data.get("promptTokenCount", 0),
                    "completion_tokens": usage_data.get("candidatesTokenCount", 0),
                    "total_tokens": usage_data.get("totalTokenCount", 0),
                }
            
            logger.debug(f"Gemini response received: {len(content)} chars")
            
            return LLMResponse(
                content=content,
                provider=self.name,
                model=self._model,
                usage=usage,
                raw_response=data,
                metadata={"finish_reason": finish_reason},
            )
            
        except requests.exceptions.Timeout:
            raise LLMTimeoutError(self.name, f"Request timed out after {self._timeout}s") from None
        except requests.exceptions.ConnectionError:
            raise LLMConnectionError(self.name, "Transport connection failed") from None
        except (LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, 
                LLMResponseError, LLMConnectionError):
            raise
        except Exception as e:
            raise LLMResponseError(f"Unexpected error: {e}")
    
    def supports_multimodal(self) -> bool:
        return True

    def supports_native_video(self) -> bool:
        # This adapter's bounded Summary Pro contract is timestamped images.
        # Large local-video upload/processing is not implemented here.
        return False

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
        """Multimodal: direct video/file reference when supported, else frames."""
        if not self._api_key:
            from app.content.exceptions import LLMAuthenticationError
            raise LLMAuthenticationError(self.name)
        # Build multimodal parts: text + optional video bytes / file ref / images
        # For Gemini: use inline_data for video bytes, fileData for URI; caller supplies path/bytes
        import base64, os, pathlib
        parts = [{"text": prompt}]
        if video_bytes is not None:
            b64 = base64.b64encode(video_bytes).decode("ascii")
            parts.append({"inline_data": {"mime_type": "video/mp4", "data": b64}})
        elif video is not None and os.path.isfile(video):
            # Try to inline small videos; for smoke use direct video path as inline fileData placeholder
            # Real production should upload via Files API and use file_data; here we inline as image fallback via adapter-managed frames
            # Keep provider adapter responsible for conversion: read file bytes when feasible
            try:
                data = pathlib.Path(video).read_bytes()
                if len(data) < 18 * 1024 * 1024:
                    b64 = base64.b64encode(data).decode("ascii")
                    parts.append({"inline_data": {"mime_type": "video/mp4", "data": b64}})
            except Exception:
                pass
        if images is not None:
            from app.content.providers.vision import encoded_frames
            for timestamp, mime, data in encoded_frames(images):
                if timestamp is not None:
                    parts.append({"text": f"Source frame at {timestamp:.3f}s"})
                parts.append({"inline_data": {"mime_type": mime, "data": data}})
        if (video is not None or video_bytes is not None) and len(parts) == 1:
            raise ValueError("No readable visual evidence attached to the Gemini request")
        # Build Gemini multimodal request (contents with single user parts)
        import json as _json
        system_instruction = None
        if system_prompt:
            system_instruction = {"parts": [{"text": system_prompt}]}
        payload = {
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction
        if response_schema is not None:
            payload["generationConfig"]["responseMimeType"] = "application/json"
        if self._model.startswith("gemini-3"):
            payload["generationConfig"]["thinkingConfig"] = {"thinkingLevel": "minimal" if self._model in {
                "gemini-3-flash-preview", "gemini-3.1-flash-lite", "gemini-3.5-flash-lite", "gemini-3.6-flash",
            } else "low"}
            if images:
                # Story actions, not OCR: bounded media tokens keep 30+ frame
                # chapters responsive without dropping any supplied evidence.
                payload["generationConfig"]["mediaResolution"] = "MEDIA_RESOLUTION_LOW"
        elif self._model.startswith("gemini-2.5-flash"):
            payload["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
        url = f"{self._base_url}/models/{self._model}:generateContent"
        import requests
        from app.content.exceptions import LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, LLMResponseError, LLMConnectionError
        payload_bytes = len(json.dumps(payload).encode("utf-8"))
        started = time.monotonic()
        logger.info("Gemini image request: model=%s frames=%d payload_bytes=%d timeout_s=%.1f",
                    self._model, len(images or []), payload_bytes, self._timeout)
        try:
            resp = requests.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key}, json=payload, timeout=self._timeout)
            self._raise_for_status(resp)
            data = resp.json()
            if (data.get("promptFeedback") or {}).get("blockReason"):
                error = LLMResponseError("Prompt blocked by provider content policy")
                error.category = "content_moderation"
                raise error
            if "candidates" not in data or not data["candidates"]:
                raise LLMResponseError("No multimodal candidates")
            cand = data["candidates"][0]
            finish_reason = cand.get("finishReason", "")
            if finish_reason == "MAX_TOKENS":
                raise LLMOutputTruncatedError()
            if finish_reason == "SAFETY":
                error = LLMResponseError("Response blocked by provider content policy")
                error.category = "content_moderation"
                raise error
            content_parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in content_parts if isinstance(p, dict) and not p.get("thought"))
            if not text:
                raise LLMResponseError("Empty multimodal content")
            usage = None
            if "usageMetadata" in data:
                ud = data["usageMetadata"]
                usage = {"prompt_tokens": ud.get("promptTokenCount", 0), "completion_tokens": ud.get("candidatesTokenCount", 0), "total_tokens": ud.get("totalTokenCount", 0)}
            return LLMResponse(content=text, provider=self.name, model=self._model, usage=usage,
                               raw_response=data, metadata={"finish_reason": finish_reason,
                               "visual_analysis_method": "frame_based_visual_analysis" if images else "native_video_ingestion",
                                "frame_count": len(images or []), "payload_bytes": payload_bytes,
                                "elapsed_seconds": round(time.monotonic() - started, 3), "timeout_seconds": self._timeout})
        except requests.exceptions.Timeout:
            raise LLMTimeoutError(self.name, f"Multimodal request timed out after {self._timeout}s") from None
        except requests.exceptions.ConnectionError:
            raise LLMConnectionError(self.name, "Multimodal transport connection failed") from None
        except (LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, LLMResponseError, LLMConnectionError):
            raise
        except Exception:
            from app.content.exceptions import LLMResponseError as _RE
            raise _RE("Malformed multimodal response") from None

    def is_available(self) -> bool:
        """Check if Gemini is configured with an API key."""
        return bool(self._api_key)
    
    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """Generate a JSON response using Gemini's JSON mode."""
        # Add JSON instruction to system prompt
        json_system = system_prompt or ""
        if json_system:
            json_system += "\n\n"
        json_system += "You must respond with valid JSON only. No markdown code blocks, no explanation, just raw JSON."
        
        return self.generate(
            prompt=prompt,
            system_prompt=json_system,
            response_schema=BaseModel,  # Triggers JSON mode
            temperature=temperature,
            max_tokens=max_tokens,
        )
