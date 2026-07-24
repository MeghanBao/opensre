"""Safe external errors for Fargate tenant lifecycle operations."""

from __future__ import annotations


class LifecycleError(RuntimeError):
    """Base class whose message is safe to return from an external API."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


class LifecycleNotFoundError(LifecycleError):
    """Raised when an organization has no known Gateway deployment."""

    def __init__(self) -> None:
        super().__init__("Gateway deployment was not found", code="GATEWAY_NOT_FOUND")


class LifecycleValidationError(LifecycleError):
    """Raised for invalid server-controlled lifecycle input."""

    def __init__(self) -> None:
        super().__init__("Gateway lifecycle input is invalid", code="INVALID_LIFECYCLE_INPUT")


class LifecycleOperationError(LifecycleError):
    """Generic boundary error; provider details remain in server-side logs."""

    def __init__(self, *, code: str) -> None:
        super().__init__("Gateway lifecycle operation failed", code=code)


class LifecycleCapacityError(LifecycleError):
    """Raised before an operation would exceed the configured compute cap."""

    def __init__(self) -> None:
        super().__init__(
            "Maximum active Gateway organizations reached",
            code="ACTIVE_ORGANIZATION_LIMIT_REACHED",
        )
