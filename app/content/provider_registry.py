"""
AVM Step 2 -- Provider Registry (READ-ONLY FACTORY MAP)

Responsibility:

    provider name (open string)
        |
    provider factory  -> BaseLLMProvider

Not:

    canonical model -> provider selection
    fallback
    intelligent routing
    availability/health checking
    model resolution

Future architecture:

    Canonical Model Registry (app/content/model_registry.py)
            |
    Provider Registry  (this module)
            |
    Model-bound Resolver  (Step 3, NOT here)
            |
    BaseLLMProvider  (existing contract, unchanged)
            |
    execution

This module is PURE INFRASTRUCTURE:

* No network calls.
* No provider instantiation at import time -- factories are stored, not instances.
* No API key reads -- factories read env only when invoked.
* No import of the canonical registry -- separation is intentional.
* No fallback, no model ranking, no AVM Auto.

The registry is extensible: a future provider (e.g. CodeCraft) is added by
``register_provider("codecraft", factory)`` without changing this file's
schema. Provider names are open strings, not a Literal/enum.
"""

from __future__ import annotations

from typing import Callable, Dict, Optional, Tuple

from app.content.providers.base import BaseLLMProvider

# Factory type: optional provider-specific wire model id -> provider instance.
ProviderFactory = Callable[[Optional[str]], BaseLLMProvider]

_REGISTRY: Dict[str, ProviderFactory] = {}


def _normalize_name(name: str) -> str:
    if not isinstance(name, str) or not name.strip():
        raise ValueError("provider name must be a non-empty string")
    return name.strip().lower()


def register_provider(
    name: str,
    factory: ProviderFactory,
    *,
    allow_override: bool = False,
) -> None:
    """Register a provider factory.

    Args:
        name: Provider name (open string, normalized to lower/strip).
        factory: Callable ``(provider_model_id: Optional[str]) -> BaseLLMProvider``.
        allow_override: If False, duplicate registration raises ValueError.

    No provider is instantiated and no network call is made.
    """
    key = _normalize_name(name)
    if not callable(factory):
        raise ValueError("factory must be callable")
    if key in _REGISTRY and not allow_override:
        raise ValueError(f"provider already registered: '{key}'")
    _REGISTRY[key] = factory


def get_provider_factory(name: str) -> ProviderFactory:
    """Return the factory for *name*.

    Raises:
        KeyError: If the provider is unknown.

    No fallback, no substitution — unknown is an error.
    """
    key = _normalize_name(name)
    try:
        return _REGISTRY[key]
    except KeyError:
        raise KeyError(f"unknown provider: '{name}'") from None


def has_provider(name: str) -> bool:
    """Whether *name* is registered (normalised)."""
    try:
        key = _normalize_name(name)
    except ValueError:
        return False
    return key in _REGISTRY


def list_providers() -> Tuple[str, ...]:
    """All registered provider names in deterministic registration order."""
    return tuple(_REGISTRY.keys())


def create_provider(
    name: str,
    provider_model_id: Optional[str] = None,
) -> BaseLLMProvider:
    """Create a provider instance via its registered factory.

    Thin convenience over ``get_provider_factory(name)(provider_model_id)``.
    The factory is responsible for constructing the provider (lazy import).
    """
    factory = get_provider_factory(name)
    # Factory is defined as (Optional[str]) -> BaseLLMProvider.
    # Passing None uses the provider's default/env model.
    if provider_model_id is not None and not str(provider_model_id).strip():
        raise ValueError("provider_model_id must be a non-empty string when set")
    # Normalise empty to None for factory delegation.
    model_arg = provider_model_id.strip() if isinstance(provider_model_id, str) and provider_model_id.strip() else provider_model_id
    return factory(model_arg)


# ---------------------------------------------------------------------------
# Built-in provider factories (lazy imports — registry import must not require
# API keys or make network calls).
# ---------------------------------------------------------------------------

def _create_alibaba(provider_model_id: Optional[str] = None) -> BaseLLMProvider:
    from app.content.providers.alibaba import AlibabaProvider

    if provider_model_id:
        return AlibabaProvider(model=provider_model_id)
    return AlibabaProvider()


def _create_openrouter(provider_model_id: Optional[str] = None) -> BaseLLMProvider:
    from app.content.providers.openrouter import OpenRouterProvider

    if provider_model_id:
        return OpenRouterProvider(model=provider_model_id)
    return OpenRouterProvider()


def _create_gemini(provider_model_id: Optional[str] = None) -> BaseLLMProvider:
    from app.content.providers.gemini import GeminiProvider

    if provider_model_id:
        return GeminiProvider(model=provider_model_id)
    return GeminiProvider()


def _create_mock(provider_model_id: Optional[str] = None) -> BaseLLMProvider:
    from app.content.providers.mock import MockLLMProvider

    if provider_model_id:
        return MockLLMProvider(model_name=provider_model_id)
    return MockLLMProvider()


def _create_codecraft(provider_model_id: Optional[str] = None) -> BaseLLMProvider:
    from app.content.providers.codecraft import CodeCraftProvider

    if provider_model_id:
        return CodeCraftProvider(model=provider_model_id)
    return CodeCraftProvider()


# Deterministic initial population (insertion order is the list order).
register_provider("alibaba", _create_alibaba)
register_provider("openrouter", _create_openrouter)
register_provider("gemini", _create_gemini)
register_provider("mock", _create_mock)
register_provider("codecraft", _create_codecraft)


__all__ = [
    "ProviderFactory",
    "register_provider",
    "get_provider_factory",
    "has_provider",
    "list_providers",
    "create_provider",
]
