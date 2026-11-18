from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse

from gitseed_core.services.errors import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    RateLimited,
    ServiceError,
    Unauthorized,
)

_STATUS_MAP: dict[type[ServiceError], int] = {
    Unauthorized: status.HTTP_401_UNAUTHORIZED,
    Forbidden: status.HTTP_403_FORBIDDEN,
    NotFound: status.HTTP_404_NOT_FOUND,
    Conflict: status.HTTP_409_CONFLICT,
    Invalid: status.HTTP_422_UNPROCESSABLE_CONTENT,
    RateLimited: status.HTTP_429_TOO_MANY_REQUESTS,
}


def register_error_handlers(app: FastAPI) -> None:
    """Register a single exception handler mapping service-layer errors to HTTP status codes."""

    @app.exception_handler(ServiceError)
    async def handle_service_error(request: Request, exc: ServiceError) -> JSONResponse:
        status_code = _STATUS_MAP.get(type(exc), status.HTTP_400_BAD_REQUEST)
        headers: dict[str, str] | None = None
        if isinstance(exc, Unauthorized):
            if request.url.path.startswith("/api/v1/local-git/"):
                headers = {"WWW-Authenticate": 'Basic realm="GitSeed Local Git"'}
            else:
                headers = {"WWW-Authenticate": "Bearer"}
        return JSONResponse(
            status_code=status_code,
            content={"detail": exc.message},
            headers=headers,
        )

