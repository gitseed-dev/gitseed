from typing import Annotated
from urllib.parse import urlsplit

from fastapi import Depends, Request, Security, WebSocket
from fastapi.security import APIKeyCookie, HTTPAuthorizationCredentials, HTTPBearer
from gitseed_common.enums import OrgRole
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.config import settings
from gitseed_core.db import get_session
from gitseed_core.models.user import User
from gitseed_core.services import auth as auth_service
from gitseed_core.services.errors import Forbidden, Unauthorized

bearer_scheme = HTTPBearer(auto_error=False, scheme_name="PersonalAccessToken")
cookie_scheme = APIKeyCookie(
    name=settings.SESSION_COOKIE_NAME,
    auto_error=False,
    scheme_name="SessionCookie",
)

DBSession = Annotated[AsyncSession, Depends(get_session)]
_UNSAFE_HTTP_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _normalize_origin(raw: str) -> str:
    cleaned = (raw or "").strip().rstrip("/")
    if not cleaned:
        return ""
    parsed = urlsplit(cleaned)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"


def get_allowed_origins() -> set[str]:
    """Return normalized origins allowed for cookie-authenticated WebSockets and CSRF checks."""
    explicit = [
        _normalize_origin(o)
        for o in (getattr(settings, "ALLOWED_ORIGINS", "") or "").split(",")
        if _normalize_origin(o)
    ]
    if explicit:
        return set(explicit)

    origins: set[str] = set()
    pub = _normalize_origin(getattr(settings, "PUBLIC_WEB_URL", "") or "")
    if pub:
        origins.add(pub)
    for item in (getattr(settings, "CORS_ORIGINS", "") or "").split(","):
        norm = _normalize_origin(item)
        if norm:
            origins.add(norm)
    return origins


def _is_sandbox_origin(raw: str) -> bool:
    cleaned = (raw or "").strip().rstrip("/")
    sandbox_raw = (getattr(settings, "SANDBOX_ORIGIN", "") or "").strip().rstrip("/")
    if not cleaned or not sandbox_raw:
        return False
    if cleaned.lower() == sandbox_raw.lower():
        return True
    norm_cleaned = _normalize_origin(cleaned)
    norm_sandbox = _normalize_origin(sandbox_raw)
    return bool(norm_cleaned and norm_sandbox and norm_cleaned == norm_sandbox)


def verify_websocket_origin(websocket: WebSocket) -> None:
    """Verify the WebSocket `Origin` header against allowed origins when cookie auth is used."""
    headers = getattr(websocket, "headers", None) or {}
    cookies = getattr(websocket, "cookies", None) or {}
    auth_header = (headers.get("authorization") or "").strip()
    is_bearer = auth_header.lower().startswith("bearer ")
    has_cookie = bool(cookies.get(settings.SESSION_COOKIE_NAME))
    origin_raw = (headers.get("origin") or "").strip()

    if origin_raw and _is_sandbox_origin(origin_raw):
        raise Forbidden("Cross-origin requests from sandbox origin cannot use session cookies")

    if is_bearer and not origin_raw:
        return

    if has_cookie or not is_bearer or origin_raw:
        norm_origin = _normalize_origin(origin_raw)
        allowed = get_allowed_origins()
        if not norm_origin or norm_origin not in allowed:
            raise Forbidden("WebSocket origin is not allowed")


def verify_csrf(request: Request) -> None:
    """Enforce CSRF protection on cookie-authenticated unsafe HTTP methods."""
    method = str(getattr(request, "method", "GET") or "GET").upper()
    if method not in _UNSAFE_HTTP_METHODS:
        return

    headers = getattr(request, "headers", None) or {}
    allowed = get_allowed_origins()

    origin = (headers.get("origin") or "").strip().rstrip("/")
    if origin:
        if _is_sandbox_origin(origin):
            raise Forbidden(
                "Cross-origin requests from sandbox origin cannot use session cookies"
            )
        norm_origin = _normalize_origin(origin)
        if not norm_origin or norm_origin not in allowed:
            raise Forbidden("Cross-origin cookie-authenticated request blocked (CSRF)")
        return

    referer_raw = (headers.get("referer") or "").strip()
    if referer_raw:
        if _is_sandbox_origin(referer_raw):
            raise Forbidden(
                "Cross-origin requests from sandbox origin cannot use session cookies"
            )
        norm_referer = _normalize_origin(referer_raw)
        if not norm_referer or norm_referer not in allowed:
            raise Forbidden("Cross-origin cookie-authenticated request blocked (CSRF)")
        return

    sec_fetch_site = (headers.get("sec-fetch-site") or "").strip().lower()
    if sec_fetch_site and sec_fetch_site not in ("same-origin", "none"):
        raise Forbidden("Cross-site cookie-authenticated request blocked (CSRF)")

    csrf_hdr = (
        headers.get("x-gitseed-csrf") or headers.get("x-requested-with") or ""
    ).strip()
    if csrf_hdr:
        return

    raise Forbidden("CSRF validation failed: missing Origin, Referer, or X-GitSeed-CSRF header")


async def get_current_user(
    request: Request,
    session: DBSession,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)] = None,
    session_cookie: Annotated[str | None, Security(cookie_scheme)] = None,
) -> User:
    """Authenticate a request via Bearer Personal Access Token or Valkey session cookie."""
    if bearer is not None and bearer.credentials:
        user = await auth_service.authenticate_token(session, bearer.credentials)
        request.state.user = user
        return user

    if session_cookie:
        verify_csrf(request)
        session_user = await auth_service.authenticate_session_cookie(session, session_cookie)
        if session_user is not None:
            request.state.user = session_user
            return session_user

    raise Unauthorized("Not authenticated")


CurrentUser = Annotated[User, Depends(get_current_user)]


async def authenticate_websocket(websocket: WebSocket, session: AsyncSession) -> User | None:
    """Authenticate a WebSocket handshake via session cookie or `Authorization: Bearer` PAT."""
    auth_header = websocket.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        if websocket.headers.get("origin"):
            verify_websocket_origin(websocket)
        try:
            return await auth_service.authenticate_token(session, auth_header[7:].strip())
        except Exception:
            return None
    cookie = websocket.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie:
        verify_websocket_origin(websocket)
        return await auth_service.authenticate_session_cookie(session, cookie)
    return None


async def get_session_user(
    request: Request,
    session: DBSession,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)] = None,
    session_cookie: Annotated[str | None, Security(cookie_scheme)] = None,
) -> User:
    """Require interactive web session authentication (rejects Personal Access Tokens)."""
    if bearer is not None and bearer.credentials:
        raise Forbidden("Personal access tokens cannot be used to mint tokens or change passwords")

    if session_cookie:
        verify_csrf(request)
        session_user = await auth_service.authenticate_session_cookie(session, session_cookie)
        if session_user is not None:
            request.state.user = session_user
            return session_user

    raise Unauthorized("Interactive web session required")


SessionUser = Annotated[User, Depends(get_session_user)]


async def require_org_admin(current_user: CurrentUser) -> User:
    """Require the authenticated user to hold OrgRole.ADMIN."""
    if current_user.org_role != OrgRole.ADMIN:
        raise Forbidden("Organization admin privileges required")
    return current_user


AdminUser = Annotated[User, Depends(require_org_admin)]
