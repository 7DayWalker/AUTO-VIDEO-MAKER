"""
AUTO Video Maker - Google Gemini LLM Provider

Google Gemini API integration for content generation.
"""

import json
import requests
from typing import Optional, Type
from pydantic import BaseModel
import logging

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
            
            # Handle HTTP errors
            if response.status_code == 401 or response.status_code == 403:
                raise LLMAuthenticationError(self.name)
            elif response.status_code == 429:
                # Rate limit or quota exceeded
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("error", {}).get("message", "Rate limit exceeded")
                if "quota" in error_msg.lower():
                    raise LLMQuotaError(self.name)
                raise LLMRateLimitError(self.name)
            elif response.status_code >= 500:
                raise LLMConnectionError(
                    self.name,
                    f"Server error: {response.status_code}"
                )
            elif response.status_code != 200:
                error_data = response.json() if response.text else {}
                error_msg = error_data.get("error", {}).get("message", response.text[:500])
                if response.status_code == 404 or (
                    response.status_code == 400 and "model" in error_msg.lower()
                    and any(word in error_msg.lower() for word in ("not found", "not supported", "unsupported"))
                ):
                    raise LLMModelUnavailableError()
                raise LLMResponseError(
                    f"HTTP {response.status_code}",
                    error_msg
                )
            
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
            raise LLMConnectionError(self.name, f"Request timed out after {self._timeout}s")
        except requests.exceptions.ConnectionError:
            raise LLMConnectionError(self.name, "Transport connection failed") from None
        except (LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, 
                LLMResponseError, LLMConnectionError):
            raise
        except Exception as e:
            raise LLMResponseError(f"Unexpected error: {e}")
    
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
        if images:
            for p in images:
                try:
                    ib = pathlib.Path(p).read_bytes()
                    b64 = base64.b64encode(ib).decode("ascii")
                    parts.append({"inline_data": {"mime_type": "image/jpeg", "data": b64}})
                except Exception:
                    continue
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
        url = f"{self._base_url}/models/{self._model}:generateContent"
        import requests
        from app.content.exceptions import LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, LLMResponseError, LLMConnectionError
        try:
            resp = requests.post(url, headers={"Content-Type": "application/json", "x-goog-api-key": self._api_key}, json=payload, timeout=self._timeout)
            if resp.status_code in (401, 403):
                raise LLMAuthenticationError(self.name)
            if resp.status_code == 429:
                raise LLMRateLimitError(self.name)
            if resp.status_code >= 500:
                raise LLMConnectionError(self.name, f"Server error: {resp.status_code}")
            if resp.status_code != 200:
                raise LLMResponseError(f"HTTP {resp.status_code}", resp.text[:500])
            data = resp.json()
            if "candidates" not in data or not data["candidates"]:
                raise LLMResponseError("No candidates", str(data)[:500])
            cand = data["candidates"][0]
            content_parts = cand.get("content", {}).get("parts", [])
            text = "".join(p.get("text", "") for p in content_parts if isinstance(p, dict))
            if not text:
                raise LLMResponseError("Empty multimodal content")
            usage = None
            if "usageMetadata" in data:
                ud = data["usageMetadata"]
                usage = {"prompt_tokens": ud.get("promptTokenCount", 0), "completion_tokens": ud.get("candidatesTokenCount", 0), "total_tokens": ud.get("totalTokenCount", 0)}
            return LLMResponse(content=text, provider=self.name, model=self._model, usage=usage, raw_response=data)
        except (LLMAuthenticationError, LLMQuotaError, LLMRateLimitError, LLMResponseError, LLMConnectionError):
            raise
        except Exception as e:
            from app.content.exceptions import LLMResponseError as _RE
            raise _RE(f"Multimodal error: {e}")

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
