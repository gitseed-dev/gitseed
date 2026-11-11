class ServiceError(Exception):
    """Base class for domain/service layer errors (no FastAPI imports)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class Unauthorized(ServiceError):
    """401: Missing, invalid, or expired credentials."""


class Forbidden(ServiceError):
    """403: Authenticated, but not allowed to perform this action."""


class NotFound(ServiceError):
    """404: Requested entity does not exist."""


class Conflict(ServiceError):
    """409: State or uniqueness conflict."""


class Invalid(ServiceError):
    """422: Domain rule or input validation failure."""


class RateLimited(ServiceError):
    """429: Too many requests / rate limit exceeded."""


