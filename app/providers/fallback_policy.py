"""Batch 1C - provider failure classification policy.

The single authorized cross-provider policy is FIXED:

    Alibaba (primary) -> classified fallback -> OpenRouter (fallback)

Fallback is CLASSIFIED, never blind. Every provider failure is reduced to a
deterministic category; only a fixed set of categories is fallback-eligible.
This module is the single source of truth for that classification so no
provider-specific fallback logic is scattered through production orchestration.

Non-eligible categories exist to make problems TRUTHFUL instead of hiding
them behind a second paid provider: a malformed AVM request, an invalid
duration/size, credential misuse, an ``sk-sp-`` Token-Plan key, invalid
configuration, or a deterministic application bug must never be silently
re-sent to OpenRouter.
"""

from typing import Optional

from app.content.exceptions import (
    LLMAuthenticationError,
    LLMConnectionError,
    LLMQuotaError,
    LLMRateLimitError,
    LLMResponseError,
)


class FailureCategory:
    """Deterministic, string-valued failure categories (stable for persistence)."""

    # --- fallback-eligible (provider-side / transport-side) ----------------
    RATE_LIMIT = "rate_limit"
    QUOTA = "quota"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    PROVIDER_5XX = "provider_5xx"
    NETWORK = "network"
    TIMEOUT = "timeout"
    CAPABILITY = "capability"
    RESPONSE_MALFORMED = "response_malformed"
    TASK_FAILURE = "task_failure"

    # --- NOT fallback-eligible (AVM-side / request-side / policy-side) -----
    AUTHENTICATION = "authentication"
    CREDENTIAL_CLASS = "credential_class"
    CONTENT_MODERATION = "content_moderation"
    INVALID_REQUEST = "invalid_request"
    CONFIGURATION = "configuration"
    ARTIFACT_VALIDATION = "artifact_validation"
    POLL_TIMEOUT = "poll_timeout"
    UNKNOWN = "unknown"


# The fixed Batch 1C fallback-eligible set. Anything NOT in this set fails
# truthfully on the primary provider without involving OpenRouter.
FALLBACK_ELIGIBLE_CATEGORIES = frozenset(
    {
        FailureCategory.RATE_LIMIT,
        FailureCategory.QUOTA,
        FailureCategory.PROVIDER_UNAVAILABLE,
        FailureCategory.PROVIDER_5XX,
        FailureCategory.NETWORK,
        FailureCategory.TIMEOUT,
        FailureCategory.CAPABILITY,
        FailureCategory.RESPONSE_MALFORMED,
        FailureCategory.TASK_FAILURE,
    }
)

_NON_ELIGIBLE_CATEGORIES = frozenset(
    {
        FailureCategory.AUTHENTICATION,
        FailureCategory.CREDENTIAL_CLASS,
        FailureCategory.CONTENT_MODERATION,
        FailureCategory.INVALID_REQUEST,
        FailureCategory.CONFIGURATION,
        FailureCategory.ARTIFACT_VALIDATION,
        FailureCategory.POLL_TIMEOUT,
        FailureCategory.UNKNOWN,
    }
)


class CategoryMixin:
    """Mixin for exception classes that carry a deterministic category.

    Provider adapters set ``category`` at raise time (they know the HTTP
    status / error code); the policy simply reads it. The default is the
    conservative non-eligible ``unknown``.
    """

    category: str = FailureCategory.UNKNOWN


class ClassifiedProviderError(CategoryMixin, Exception):
    """Base class for provider errors that carry a Batch 1C category."""


def is_fallback_eligible(category: Optional[str]) -> bool:
    """Whether one classified category authorizes the OpenRouter fallback."""
    return category in FALLBACK_ELIGIBLE_CATEGORIES


def classify_provider_failure(exc: BaseException) -> str:
    """Map any provider exception to its deterministic Batch 1C category.

    Order matters: an explicit category mixin on the raised exception always
    wins (provider adapters know their HTTP semantics), then the existing AVM
    content-engine LLM exceptions are mapped to their established meanings,
    and anything unrecognized is the conservative non-eligible ``unknown``.
    """
    category = getattr(exc, "category", None)
    if isinstance(category, str) and (
        category in FALLBACK_ELIGIBLE_CATEGORIES
        or category in _NON_ELIGIBLE_CATEGORIES
    ):
        return category

    if isinstance(exc, LLMAuthenticationError):
        return FailureCategory.AUTHENTICATION
    if isinstance(exc, LLMQuotaError):
        return FailureCategory.QUOTA
    if isinstance(exc, LLMRateLimitError):
        return FailureCategory.RATE_LIMIT
    if isinstance(exc, LLMConnectionError):
        return FailureCategory.NETWORK
    if isinstance(exc, LLMResponseError):
        return FailureCategory.RESPONSE_MALFORMED
    return FailureCategory.UNKNOWN
