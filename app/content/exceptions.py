"""
AUTO Video Maker - Content Engine Exceptions

Structured exceptions for content generation and planning.
"""


class ContentError(Exception):
    """Base exception for all content-related errors."""
    pass


class ContentBriefError(ContentError):
    """Raised when the content brief is invalid."""
    pass


class ScriptGenerationError(ContentError):
    """Raised when script generation fails."""
    pass


class ScenePlanningError(ContentError):
    """Raised when scene planning fails."""
    pass


class ValidationError(ContentError):
    """Raised when validation fails."""
    
    def __init__(self, errors: list):
        self.errors = errors
        super().__init__(f"Validation failed: {'; '.join(errors)}")


class DurationError(ContentError):
    """Raised when duration is outside acceptable tolerance."""
    
    def __init__(self, actual: float, target: float, tolerance: float):
        self.actual = actual
        self.target = target
        self.tolerance = tolerance
        min_dur = target - tolerance
        max_dur = target + tolerance
        super().__init__(
            f"Duration {actual:.1f}s outside acceptable range "
            f"({min_dur:.1f}s - {max_dur:.1f}s, target: {target:.1f}s)"
        )


class LLMProviderError(ContentError):
    """Base exception for LLM provider errors."""
    pass


class LLMAuthenticationError(LLMProviderError):
    """Raised when LLM authentication fails (invalid API key)."""
    
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"Authentication failed for {provider}. Check your API key.")


class LLMQuotaError(LLMProviderError):
    """Raised when LLM quota is exhausted."""
    
    def __init__(self, provider: str):
        self.provider = provider
        super().__init__(f"Quota exhausted for {provider}. Check your account limits.")


class LLMRateLimitError(LLMProviderError):
    """Raised when rate limit is hit."""
    
    def __init__(self, provider: str, retry_after: int = None):
        self.provider = provider
        self.retry_after = retry_after
        msg = f"Rate limit hit for {provider}."
        if retry_after:
            msg += f" Retry after {retry_after} seconds."
        super().__init__(msg)


class LLMResponseError(LLMProviderError):
    """Raised when LLM response is invalid or malformed."""
    
    def __init__(self, reason: str, raw_response: str = None):
        self.reason = reason
        self.raw_response = raw_response
        super().__init__(f"Invalid LLM response: {reason}")


class LLMOutputTruncatedError(LLMResponseError):
    """A provider exhausted its output budget; partial JSON is not a plan."""

    def __init__(self):
        super().__init__("Response exceeded the output token budget; incomplete output was discarded")


class LLMModelUnavailableError(LLMResponseError):
    """The exact wire model/operation is unavailable, not an invalid AVM brief."""

    category = "capability"

    def __init__(self):
        super().__init__("The provider does not support the requested model or operation")


class LLMConnectionError(LLMProviderError):
    """Raised when connection to LLM provider fails."""
    
    def __init__(self, provider: str, details: str = None):
        self.provider = provider
        self.details = details
        msg = f"Connection failed to {provider}"
        if details:
            msg += f": {details}"
        super().__init__(msg)


class RetryExhaustedError(ContentError):
    """Raised when all retry attempts are exhausted."""
    
    def __init__(self, attempts: int, last_error: str):
        self.attempts = attempts
        self.last_error = last_error
        super().__init__(
            f"All {attempts} retry attempts exhausted. Last error: {last_error}"
        )
