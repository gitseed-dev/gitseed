from fastapi import APIRouter, Request, Response, status
from gitseed_common.schemas.auth import (
    AcceptInviteRequest,
    ChangePasswordRequest,
    CreateTokenRequest,
    CurrentUserResponse,
    InvitePreviewResponse,
    LoginRequest,
    PushDeviceRegisterRequest,
    PushDeviceResponse,
    TokenCreatedResponse,
    TokenResponse,
)

from gitseed_core.api.deps import CurrentUser, DBSession, SessionUser
from gitseed_core.config import settings
from gitseed_core.services import auth as auth_service
from gitseed_core.services import push_relay as push_relay_service
from gitseed_core.services import users as users_service

router = APIRouter(prefix="/auth", tags=["Authentication"])


def _extract_client_ip(request: Request | None) -> str | None:
    """Resolve client IP address, honoring `X-Forwarded-For` only when `TRUST_PROXY_HEADERS` is enabled."""
    if request is None:
        return None
    if bool(getattr(settings, "TRUST_PROXY_HEADERS", False)):
        xff = (request.headers.get("x-forwarded-for") or "").strip()
        if xff:
            first_ip = xff.split(",")[0].strip()
            if first_ip:
                return first_ip
    return request.client.host if request.client else None


def _is_secure_cookie_request(request: Request | None = None) -> bool:
    """Return True when the session cookie should carry the `Secure` attribute."""
    if getattr(settings, "ENVIRONMENT", "").strip().lower() == "production":
        return True
    if (getattr(settings, "PUBLIC_WEB_URL", "") or "").strip().lower().startswith("https://"):
        return True
    if request is not None:
        url_obj = getattr(request, "url", None)
        if getattr(url_obj, "scheme", "").lower() == "https":
            return True
        if bool(getattr(settings, "TRUST_PROXY_HEADERS", False)):
            xfp = (request.headers.get("x-forwarded-proto") or "").split(",")[0].strip().lower()
            if xfp == "https":
                return True
    return False


@router.post("/login", response_model=CurrentUserResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DBSession,
) -> CurrentUserResponse:
    """Authenticate with email and password and set a web session cookie."""
    client_ip = _extract_client_ip(request)
    user, session_id = await auth_service.authenticate(
        session,
        payload.email,
        payload.password,
        client_ip=client_ip,
    )
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=settings.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie_request(request),
    )
    return CurrentUserResponse.model_validate(user)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: SessionUser,
    session: DBSession,
) -> None:
    """Change the current user's password and revoke all other active web sessions."""
    await auth_service.change_password(
        session,
        current_user,
        current_password=payload.current_password,
        new_password=payload.new_password,
        current_session_id=request.cookies.get(settings.SESSION_COOKIE_NAME),
    )


@router.get("/invite", response_model=InvitePreviewResponse)
async def get_invite_preview(
    token: str,
    session: DBSession,
) -> InvitePreviewResponse:
    """Validate an invitation token and return basic user info for the accept-invite page."""
    user = await users_service.get_invite_by_token(session, token=token)
    return InvitePreviewResponse.model_validate(user)


@router.post("/accept-invite", response_model=CurrentUserResponse)
async def accept_invite(
    payload: AcceptInviteRequest,
    request: Request,
    response: Response,
    session: DBSession,
) -> CurrentUserResponse:
    """Accept an invitation token, set password, activate account, and start a session."""
    user, session_id = await users_service.accept_invite(
        session,
        token=payload.token,
        password=payload.password,
        preferred_name=payload.preferred_name,
    )
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=session_id,
        max_age=settings.SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=_is_secure_cookie_request(request),
    )
    return CurrentUserResponse.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    request: Request,
    response: Response,
    _current_user: CurrentUser,
) -> None:
    """Invalidate the current web session cookie."""
    await auth_service.end_session(request.cookies.get(settings.SESSION_COOKIE_NAME))
    response.delete_cookie(key=settings.SESSION_COOKIE_NAME)


@router.get("/me", response_model=CurrentUserResponse)
async def get_me(current_user: CurrentUser) -> CurrentUserResponse:
    """Return the currently authenticated user."""
    return CurrentUserResponse.model_validate(current_user)


@router.get("/tokens", response_model=list[TokenResponse])
async def list_tokens(
    current_user: CurrentUser,
    session: DBSession,
) -> list[TokenResponse]:
    """List personal access tokens belonging to the current user."""
    tokens = await auth_service.list_tokens(session, current_user)
    return [TokenResponse.model_validate(t) for t in tokens]


@router.post(
    "/tokens",
    response_model=TokenCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_token(
    payload: CreateTokenRequest,
    current_user: SessionUser,
    session: DBSession,
) -> TokenCreatedResponse:
    """Create a new personal access token (interactive web session only)."""
    pat, raw = await auth_service.create_token(
        session, current_user, payload.name, payload.expires_in_days
    )
    return TokenCreatedResponse.model_validate(pat).model_copy(update={"token": raw})


@router.delete("/tokens/{token_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke_token(
    token_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> None:
    """Revoke a personal access token owned by the current user."""
    await auth_service.revoke_token(session, current_user, token_id)


@router.get("/push-devices", response_model=list[PushDeviceResponse])
async def list_push_devices(
    current_user: CurrentUser,
) -> list[PushDeviceResponse]:
    """List mobile and wearable push devices registered by the current user (without raw push tokens)."""
    items = await push_relay_service.list_push_devices(current_user.id)
    return [PushDeviceResponse.model_validate(item) for item in items]


@router.post(
    "/push-devices",
    response_model=PushDeviceResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register_push_device(
    payload: PushDeviceRegisterRequest,
    current_user: CurrentUser,
) -> PushDeviceResponse:
    """Register or refresh an APNs/FCM push device token for the current user."""
    item = await push_relay_service.register_push_device(
        current_user.id,
        platform=payload.platform,
        push_token=payload.push_token,
        device_name=payload.device_name,
        app_bundle_id=payload.app_bundle_id,
    )
    return PushDeviceResponse.model_validate(item)


@router.delete("/push-devices/{device_id}", status_code=status.HTTP_204_NO_CONTENT)
async def unregister_push_device(
    device_id: str,
    current_user: CurrentUser,
) -> None:
    """Unregister a mobile or wearable push device owned by the current user."""
    await push_relay_service.unregister_push_device(current_user.id, device_id)

