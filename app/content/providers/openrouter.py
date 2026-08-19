"""
AUTO Video Maker - OpenRouter LLM Provider

OpenRouter API integration for accessing multiple LLM models.
"""

import json
import requests
from typing import Optional, Type
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
)

logger = logging.getLogger(__name__)


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
            
            content = data["choices"][0].get("message", {}).get("content", "")
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
