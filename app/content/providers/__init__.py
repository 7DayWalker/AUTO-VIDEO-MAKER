"""
AUTO Video Maker - LLM Providers

Abstraction layer for LLM providers.
"""

from app.content.providers.base import BaseLLMProvider, LLMResponse
from app.content.providers.mock import MockLLMProvider
from app.content.providers.openrouter import OpenRouterProvider
from app.content.providers.gemini import GeminiProvider
from app.content.config import DEFAULT_LLM_PROVIDER, ALIBABA_FALLBACK_ENABLED


def _build_alibaba_provider() -> BaseLLMProvider:
    """Batch 1C: Alibaba PRIMARY with classified OpenRouter FALLBACK.

    Alibaba is the primary AI provider; OpenRouter is invoked ONLY after a
    fallback-eligible (classified) Alibaba failure, never in parallel and
    never as a quality-comparison or backup-copy call. With fallback disabled
    (or OpenRouter unconfigured) the deployment is honestly Alibaba-only.
    """
    from app.content.providers.alibaba import AlibabaProvider
    from app.content.providers.fallback import FallbackLLMProvider

    primary = AlibabaProvider()
    if not ALIBABA_FALLBACK_ENABLED:
        return primary
    try:
        fallback = OpenRouterProvider()
    except Exception:  # noqa: BLE001 - a fallback must never break startup
        fallback = None
    return FallbackLLMProvider(primary, fallback)


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
    elif provider_name == "alibaba":
        return _build_alibaba_provider()
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
