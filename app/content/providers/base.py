"""
AUTO Video Maker - Base LLM Provider

Abstract base class for LLM providers.
"""

from abc import ABC, abstractmethod
from typing import Optional, Dict, Any, Type
from pydantic import BaseModel
import logging

logger = logging.getLogger(__name__)


class LLMResponse(BaseModel):
    """Response from an LLM provider."""
    content: str
    provider: str
    model: str
    usage: Optional[Dict[str, int]] = None
    raw_response: Optional[Dict[str, Any]] = None
    # Batch 1C: optional provider-provenance metadata (primary/final provider,
    # fallback_used, fallback_reason, latency). Additive only — existing
    # providers leave it unset and every existing consumer is unaffected.
    metadata: Optional[Dict[str, Any]] = None


class BaseLLMProvider(ABC):
    """
    Abstract base class for LLM providers.
    
    All LLM providers must implement this interface.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Provider name (e.g., 'openrouter', 'openai')."""
        pass
    
    @property
    @abstractmethod
    def model(self) -> str:
        """Current model being used."""
        pass
    
    @abstractmethod
    def generate(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        response_schema: Optional[Type[BaseModel]] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """
        Generate a response from the LLM.
        
        Args:
            prompt: The user prompt.
            system_prompt: Optional system prompt for context.
            response_schema: Optional Pydantic model for structured output.
            temperature: Sampling temperature (0.0-2.0).
            max_tokens: Maximum tokens in response.
        
        Returns:
            LLMResponse with the generated content.
        
        Raises:
            LLMAuthenticationError: If authentication fails.
            LLMQuotaError: If quota is exhausted.
            LLMRateLimitError: If rate limit is hit.
            LLMResponseError: If response is invalid.
            LLMConnectionError: If connection fails.
        """
        pass
    
    @abstractmethod
    def is_available(self) -> bool:
        """
        Check if the provider is available and configured.
        
        Returns:
            True if provider can be used, False otherwise.
        """
        pass
    
    def generate_json(
        self,
        prompt: str,
        system_prompt: Optional[str] = None,
        temperature: float = 0.7,
        max_tokens: int = 4096,
    ) -> LLMResponse:
        """
        Generate a JSON response from the LLM.
        
        This is a convenience method that instructs the LLM to return JSON.
        
        Args:
            prompt: The user prompt (should request JSON output).
            system_prompt: Optional system prompt.
            temperature: Sampling temperature.
            max_tokens: Maximum tokens.
        
        Returns:
            LLMResponse with JSON content.
        """
        # Default implementation - subclasses may override for native JSON mode
        return self.generate(
            prompt=prompt,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    
    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(model={self.model})"
