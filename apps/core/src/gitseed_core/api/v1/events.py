from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import time
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Query, Request, Security
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials
from gitseed_common.enums import ActivityType, OrgRole, ProjectVisibility, UserStatus
from pydantic import BaseModel
from sqlalchemy import or_
from sqlmodel import col, select

from gitseed_core.api.deps import CurrentUser, DBSession, bearer_scheme, cookie_scheme
from gitseed_core.config import settings
from gitseed_core.db import AsyncSessionLocal
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.user import User
from gitseed_core.services import auth as auth_service
from gitseed_core.services.errors import Forbidden, Unauthorized
from gitseed_core.services.events import format_sse_message, subscribe_events

router = APIRouter(prefix="/events", tags=["Realtime Events (SSE)"])

SSE_STREAM_TOKEN_PREFIX = "gssse_"
SSE_STREAM_TOKEN_TTL_SECONDS = 60


class StreamTokenResponse(BaseModel):
    token: str
    expires_in: int = SSE_STREAM_TOKEN_TTL_SECONDS


def create_sse_stream_token(
    user_id: str,
    *,
    ttl_seconds: int = SSE_STREAM_TOKEN_TTL_SECONDS,
) -> str:
    """Create a short-lived HMAC-SHA256 signed SSE stream token (`gssse_...`)."""
    exp = int(time.time()) + ttl_seconds
    payload = f"sse:{user_id}:{exp}".encode()
    payload_b64 = base64.urlsafe_b64encode(payload).rstrip(b"=").decode("ascii")
    sig = hmac.new(
        settings.GITSEED_SECRET_KEY.encode(),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    return f"{SSE_STREAM_TOKEN_PREFIX}{payload_b64}.{sig}"


def verify_sse_stream_token(token: str) -> str | None:
    """Verify a short-lived `gssse_...` stream token and return `user_id` if valid."""
    if not token or not token.startswith(SSE_STREAM_TOKEN_PREFIX):
        return None
    body = token[len(SSE_STREAM_TOKEN_PREFIX) :]
    if "." not in body:
        return None
    payload_b64, sig = body.split(".", 1)
    expected_sig = hmac.new(
        settings.GITSEED_SECRET_KEY.encode(),
        payload_b64.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(sig, expected_sig):
        return None
    pad = "=" * (-len(payload_b64) % 4)
    try:
        decoded = base64.urlsafe_b64decode(payload_b64 + pad).decode()
        prefix, user_id, exp_s = decoded.split(":", 2)
        if prefix != "sse" or not user_id:
            return None
        if int(time.time()) > int(exp_s):
            return None
        return user_id
    except Exception:
        return None

_ORG_ADMIN_ONLY_EVENT_TYPES = {
    str(ActivityType.USER_INVITED),
    str(ActivityType.USER_ACTIVATED),
    str(ActivityType.USER_SUSPENDED),
    str(ActivityType.USER_REACTIVATED),
    str(ActivityType.BACKEND_GRANTED),
    str(ActivityType.BACKEND_REVOKED),
    str(ActivityType.BACKEND_SECRET_UPDATED),
    str(ActivityType.PAT_CREATED),
    str(ActivityType.PAT_REVOKED),
}
_VISIBILITY_MUTATING_EVENT_TYPES = {
    str(ActivityType.PROJECT_CREATED),
    str(ActivityType.PROJECT_ARCHIVED),
    str(ActivityType.PROJECT_RESTORED),
    str(ActivityType.MEMBER_ADDED),
    str(ActivityType.MEMBER_ROLE_CHANGED),
    str(ActivityType.MEMBER_REMOVED),
    str(ActivityType.SETTINGS_UPDATED),
}


async def _authenticate_sse_request(
    request: Request,
    session: DBSession,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)] = None,
    session_cookie: Annotated[str | None, Security(cookie_scheme)] = None,
    token: str | None = Query(
        default=None,
        description="Short-lived SSE stream token (`gssse_...`) from POST /api/v1/events/stream-token",
    ),
) -> User:
    if bearer is not None and bearer.credentials:
        user = await auth_service.authenticate_token(session, bearer.credentials)
        request.state.user = user
        return user

    if token:
        if not token.startswith(SSE_STREAM_TOKEN_PREFIX):
            raise Unauthorized(
                "Personal access tokens cannot be passed in query strings; exchange for a short-lived stream token via POST /api/v1/events/stream-token"
            )
        user_id = verify_sse_stream_token(token)
        if not user_id:
            raise Unauthorized("Invalid or expired SSE stream token")
        user = await session.get(User, user_id)
        if user is None:
            raise Unauthorized("User not found for SSE stream token")
        if user.status == UserStatus.SUSPENDED:
            raise Forbidden("Account is suspended")
        request.state.user = user
        return user

    if session_cookie:
        session_user = await auth_service.authenticate_session_cookie(session, session_cookie)
        if session_user is not None:
            request.state.user = session_user
            return session_user

    raise Unauthorized("Not authenticated")


@router.post("/stream-token", response_model=StreamTokenResponse)
async def issue_stream_token(current_user: CurrentUser) -> StreamTokenResponse:
    """Issue a short-lived (60s) HMAC-signed token (`gssse_...`) for authenticating an EventSource connection."""
    return StreamTokenResponse(
        token=create_sse_stream_token(
            current_user.id, ttl_seconds=SSE_STREAM_TOKEN_TTL_SECONDS
        ),
        expires_in=SSE_STREAM_TOKEN_TTL_SECONDS,
    )


async def _load_visible_project_ids(session: DBSession, user: User) -> set[str]:
    stmt = (
        select(Project.id)
        .outerjoin(
            ProjectMember,
            (col(ProjectMember.project_id) == col(Project.id))
            & (col(ProjectMember.user_id) == user.id),
        )
        .where(
            or_(
                col(Project.visibility) == ProjectVisibility.PUBLIC,
                col(ProjectMember.user_id).is_not(None),
            )
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    return set(rows)


@router.get("/stream")
async def stream_events(
    request: Request,
    session: DBSession,
    bearer: Annotated[HTTPAuthorizationCredentials | None, Security(bearer_scheme)] = None,
    session_cookie: Annotated[str | None, Security(cookie_scheme)] = None,
    token: str | None = Query(default=None),
    project_id: str | None = Query(default=None),
    seed_id: str | None = Query(default=None),
    max_events: int | None = Query(default=None, ge=1, le=100),
    heartbeat_seconds: float = Query(default=15.0, ge=0.5, le=60.0),
) -> StreamingResponse:
    """Server-Sent Events (SSE) stream for real-time seed, run, comment, and log updates."""
    user = await _authenticate_sse_request(
        request=request,
        session=session,
        bearer=bearer,
        session_cookie=session_cookie,
        token=token,
    )
    is_admin = user.org_role == OrgRole.ADMIN
    visible_projects = set() if is_admin else await _load_visible_project_ids(session, user)
    try:
        await session.close()
    except Exception:
        pass

    async def _event_generator() -> AsyncIterator[str]:
        nonlocal visible_projects
        # Emit initial handshake event so client knows stream is live
        yield format_sse_message(
            {
                "id": "connected",
                "type": "stream.connected",
                "user_id": user.id,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            event_name="connected",
        )

        delivered = 0
        sub_iter = subscribe_events().__aiter__()
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event: dict[str, Any] = await asyncio.wait_for(
                        sub_iter.__anext__(),
                        timeout=heartbeat_seconds,
                    )
                except TimeoutError:
                    yield f": heartbeat {datetime.now(UTC).isoformat()}\n\n"
                    continue
                except StopAsyncIteration:
                    break

                ev_type = str(event.get("type") or "")
                ev_proj = event.get("project_id")
                ev_seed = event.get("seed_id")

                # Enforce RBAC visibility (dynamically refresh membership on project/member changes)
                if not is_admin:
                    if ev_type in _VISIBILITY_MUTATING_EVENT_TYPES:
                        try:
                            async with AsyncSessionLocal() as refresh_session:
                                visible_projects = await _load_visible_project_ids(
                                    refresh_session, user
                                )
                        except Exception:
                            pass
                    if ev_type in _ORG_ADMIN_ONLY_EVENT_TYPES:
                        continue
                    if (
                        ev_type == str(ActivityType.SETTINGS_UPDATED)
                        and not ev_proj
                    ):
                        continue
                    if not ev_proj or ev_proj not in visible_projects:
                        continue

                # Optional client-side scope filters
                if project_id and ev_proj != project_id:
                    continue
                if seed_id and ev_seed != seed_id:
                    continue

                yield format_sse_message(event, event_name="gitseed_event")
                delivered += 1
                if max_events is not None and delivered >= max_events:
                    break
        finally:
            await sub_iter.aclose()  # type: ignore[attr-defined]

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )

