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
        
        # Build URL with API key
        url = f"{self._base_url}/models/{self._model}:generateContent?key={self._api_key}"
        
        headers = {
            "Content-Type": "application/json",
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
                    raise LLMResponseError(
                        f"Content blocked: {feedback.get('blockReason')}",
                        str(feedback)
                    )
            
            # Extract content from candidates
            if "candidates" not in data or not data["candidates"]:
                raise LLMResponseError("No candidates in response", str(data)[:500])
            
            candidate = data["candidates"][0]
            
            # Check finish reason
            finish_reason = candidate.get("finishReason", "")
            if finish_reason == "SAFETY":
                raise LLMResponseError("Response blocked due to safety filters")
            
            # Extract text content
            content_parts = candidate.get("content", {}).get("parts", [])
            if not content_parts:
                raise LLMResponseError("No content parts in response")
            
            content = content_parts[0].get("text", "")
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
