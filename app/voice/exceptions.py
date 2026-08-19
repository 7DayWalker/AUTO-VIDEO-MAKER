"""
AUTO Video Maker - Voice Engine Exceptions

Structured exceptions for voice generation.
"""


class VoiceError(Exception):
    """Base exception for all voice-related errors."""
    pass


class VoiceConfigurationError(VoiceError):
    """Raised when voice configuration is invalid."""
    pass


class VoiceProviderError(VoiceError):
    """Raised when voice provider encounters an error."""
    
    def __init__(self, provider: str, message: str):
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class VoiceGenerationError(VoiceError):
    """Raised when voice generation fails."""
    
    def __init__(self, reason: str, provider: str = None):
        self.reason = reason
        self.provider = provider
        msg = f"Voice generation failed: {reason}"
        if provider:
            msg = f"[{provider}] {msg}"
        super().__init__(msg)


class VoiceValidationError(VoiceError):
    """Raised when voice/audio validation fails."""
    
    def __init__(self, errors: list):
        self.errors = errors
        super().__init__(f"Voice validation failed: {'; '.join(errors)}")


class VoiceTimingError(VoiceError):
    """Raised when voice timing calculation fails."""
    
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Voice timing error: {reason}")


class VoiceNotFoundError(VoiceError):
    """Raised when requested voice is not available."""
    
    def __init__(self, voice: str, provider: str = None):
        self.voice = voice
        self.provider = provider
        msg = f"Voice '{voice}' not found"
        if provider:
            msg += f" in {provider}"
        super().__init__(msg)


class NarrationExtractionError(VoiceError):
    """Raised when narration extraction from production plan fails."""
    
    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(f"Narration extraction failed: {reason}")
