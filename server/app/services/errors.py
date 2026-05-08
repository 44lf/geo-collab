class ConflictError(ValueError):
    """Raised when an optimistic version or idempotency conflict is detected."""

