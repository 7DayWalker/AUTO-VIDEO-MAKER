"""AVM shared provider-layer primitives.

This package holds the small pieces that are shared ACROSS provider seams
(LLM/text, image sourcing, video generation) without belonging to any single
seam: the Batch 1C failure-classification policy that governs the fixed

    Alibaba (primary) -> classified fallback -> OpenRouter (fallback)

resilience rule. It contains no I/O and never performs any request.
"""

from app.providers.fallback_policy import (
    FALLBACK_ELIGIBLE_CATEGORIES,
    CategoryMixin,
    ClassifiedProviderError,
    FailureCategory,
    classify_provider_failure,
    is_fallback_eligible,
)

__all__ = [
    "FALLBACK_ELIGIBLE_CATEGORIES",
    "CategoryMixin",
    "ClassifiedProviderError",
    "FailureCategory",
    "classify_provider_failure",
    "is_fallback_eligible",
]
