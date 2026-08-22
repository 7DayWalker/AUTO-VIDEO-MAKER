"""
AUTO Video Maker - Orchestrator Exceptions

Custom exceptions for the production orchestrator.
"""


class OrchestratorError(Exception):
    """Base exception for orchestrator errors."""
    pass


class StageFailedError(OrchestratorError):
    """Raised when a pipeline stage fails."""
    
    def __init__(self, stage: str, message: str, details: dict = None):
        self.stage = stage
        self.details = details or {}
        super().__init__(f"Stage '{stage}' failed: {message}")


class ResumeError(OrchestratorError):
    """Raised when pipeline cannot be resumed."""
    
    def __init__(self, message: str, manifest_path: str = None):
        self.manifest_path = manifest_path
        super().__init__(message)


class ValidationError(OrchestratorError):
    """Raised when validation fails."""
    
    def __init__(self, stage: str, message: str, path: str = None):
        self.stage = stage
        self.path = path
        super().__init__(f"Validation failed at '{stage}': {message}")


class ConfigurationError(OrchestratorError):
    """Raised when configuration is invalid."""
    pass


class DependencyError(OrchestratorError):
    """Raised when a required dependency is missing."""
    
    def __init__(self, dependency: str, message: str = None):
        self.dependency = dependency
        msg = message or f"Required dependency not available: {dependency}"
        super().__init__(msg)
