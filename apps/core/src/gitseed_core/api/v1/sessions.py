from typing import Literal

from fastapi import APIRouter, Depends, Query
from gitseed_common.schemas.sandbox import SessionListItem
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.api.deps import get_current_user
from gitseed_core.db import get_session
from gitseed_core.models.user import User
from gitseed_core.services import sessions as sessions_service

router = APIRouter(prefix="/sessions", tags=["Sessions"])


@router.get("", response_model=list[SessionListItem])
async def list_sessions(
    scope: Literal["mine", "everyone"] = Query(default="everyone"),
    state: Literal["running", "ended", "all"] = Query(default="all"),
    project_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=300),
    offset: int = Query(default=0, ge=0),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SessionListItem]:
    rows = await sessions_service.list_sessions(
        session, actor, scope=scope, state=state, project_id=project_id, limit=limit, offset=offset
    )
    return [SessionListItem(**r) for r in rows]


@router.get("/{kind}/{session_id}", response_model=SessionListItem)
async def get_session_item(
    kind: Literal["chat", "terminal"],
    session_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionListItem:
    row = await sessions_service.get_session_item(session, actor, kind, session_id)
    return SessionListItem(**row)
