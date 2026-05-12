class ConflictError(ValueError):
    """Raised when an optimistic version or idempotency conflict is detected. (HTTP 409)"""


class ValidationError(ValueError):
    """Raised when user input validation fails. (HTTP 400)"""


class AccountError(ValueError):
    """Raised for account-related errors (expired, not found, platform mismatch). (HTTP 400)"""

