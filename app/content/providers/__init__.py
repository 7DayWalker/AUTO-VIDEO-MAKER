"""
AUTO Video Maker - LLM Providers

Abstraction layer for LLM providers.
"""

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.providers.mock import MockLLMProvider
from app.content.providers.openrouter import OpenRouterProvider
from app.content.providers.gemini import GeminiProvider
from app.content.config import DEFAULT_LLM_PROVIDER


def get_default_provider() -> BaseLLMProvider:
    """
    Get the default LLM provider based on configuration.
    
    Returns:
        Configured LLM provider instance.
    
    Raises:
        ValueError: If provider is not recognized.
    """
    provider_name = DEFAULT_LLM_PROVIDER.lower()
    
    if provider_name == "mock":
        return MockLLMProvider()
    elif provider_name == "openrouter":
        return OpenRouterProvider()
    elif provider_name == "gemini":
        return GeminiProvider()
    else:
        raise ValueError(f"Unknown LLM provider: {provider_name}")


__all__ = [
    "BaseLLMProvider",
    "LLMResponse",
    "MockLLMProvider",
    "OpenRouterProvider",
    "GeminiProvider",
    "get_default_provider",
]
