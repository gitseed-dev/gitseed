"""Cross-project listing of interactive sessions (terminals and agent chats)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from gitseed_common.enums import (
    SANDBOX_ACTIVE_STATUSES,
    AgentSessionStatus,
    OrgRole,
    ProjectVisibility,
)
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.agent import Agent
from gitseed_core.models.agent_session import AgentSession
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.sandbox import SandboxSession
from gitseed_core.models.user import User
from gitseed_core.services import agent_sessions as agent_sessions_service
from gitseed_core.services.errors import NotFound
from gitseed_core.services.gateway import resolve_session_context_window
from gitseed_core.services.sandboxes import reconcile_sandbox_sessions


async def _visible_projects(session: AsyncSession, actor: User) -> dict[str, Project]:
    if actor.org_role == OrgRole.ADMIN:
        stmt = select(Project).where(col(Project.archived_at).is_(None))
    else:
        stmt = (
            select(Project)
            .outerjoin(
                ProjectMember,
                (col(ProjectMember.project_id) == col(Project.id))
                & (col(ProjectMember.user_id) == actor.id),
            )
            .where(
                col(Project.archived_at).is_(None),
                or_(
                    col(Project.visibility) == ProjectVisibility.PUBLIC,
                    col(ProjectMember.user_id).is_not(None),
                ),
            )
        )
    return {p.id: p for p in (await session.execute(stmt)).scalars().all()}


def _terminal_name(s: SandboxSession) -> str:
    if s.title:
        return s.title
    if s.kind != "terminal":
        return f"Run · {s.command}"
    return f"Terminal · {s.ref}"


async def list_sessions(
    session: AsyncSession,
    actor: User,
    *,
    scope: Literal["mine", "everyone"] = "everyone",
    state: Literal["running", "ended", "all"] = "all",
    project_id: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[dict[str, Any]]:
    projects = await _visible_projects(session, actor)
    if project_id:
        match = projects.get(project_id) or next(
            (p for p in projects.values() if p.slug == project_id), None
        )
        projects = {match.id: match} if match else {}
    if not projects:
        return []
    pids = list(projects.keys())

    # Keep statuses fresh (container exits, idle stops, empty-session cleanup)
    has_active = (
        await session.scalar(
            select(SandboxSession.id)
            .where(
                col(SandboxSession.project_id).in_(pids),
                col(SandboxSession.status).in_([s.value for s in SANDBOX_ACTIVE_STATUSES]),
            )
            .limit(1)
        )
    ) is not None
    if has_active:
        await reconcile_sandbox_sessions(session)
        await session.commit()
    chat_pids = set(
        (
            await session.execute(
                select(AgentSession.project_id).where(col(AgentSession.project_id).in_(pids))
            )
        ).scalars().all()
    )
    for pid in chat_pids:
        try:
            await agent_sessions_service._purge_idle_empty_sessions(session, pid)
        except Exception:
            await session.rollback()

    items: list[dict[str, Any]] = []

    sbx_stmt = select(SandboxSession).where(col(SandboxSession.project_id).in_(pids))
    if scope == "mine":
        sbx_stmt = sbx_stmt.where(SandboxSession.started_by == actor.id)
    for s in (await session.execute(sbx_stmt.order_by(col(SandboxSession.started_at).desc()).limit(limit))).scalars().all():
        p = projects[s.project_id]
        running = s.status in SANDBOX_ACTIVE_STATUSES
        items.append(
            {
                "kind": "terminal",
                "id": s.id,
                "project_id": p.id,
                "project_slug": p.slug,
                "project_name": p.name,
                "name": _terminal_name(s),
                "ref": s.ref,
                "head_sha": s.head_sha or "",
                "status": str(s.status),
                "is_running": running,
                "started_by": s.started_by,
                "started_at": s.started_at,
                "last_activity_at": s.last_activity_at or s.started_at,
                "expires_at": s.expires_at if running else None,
                "image": s.image,
                "pushed_seed_id": s.pushed_seed_id,
            }
        )

    chat_stmt = select(AgentSession).where(col(AgentSession.project_id).in_(pids))
    if scope == "mine":
        chat_stmt = chat_stmt.where(AgentSession.created_by == actor.id)
    chats = (await session.execute(chat_stmt.order_by(col(AgentSession.updated_at).desc()).limit(limit))).scalars().all()
    agent_ids = {c.agent_id for c in chats}
    handles = {
        a.id: a.handle
        for a in (
            await session.execute(select(Agent).where(col(Agent.id).in_(agent_ids)))
        ).scalars().all()
    } if agent_ids else {}
    for c in chats:
        p = projects[c.project_id]
        running = c.status in (AgentSessionStatus.STARTING, AgentSessionStatus.RUNNING_TURN) or (
            c.status == AgentSessionStatus.IDLE and bool(c.container_id)
        )
        last = c.updated_at or c.created_at
        items.append(
            {
                "kind": "chat",
                "id": c.id,
                "project_id": p.id,
                "project_slug": p.slug,
                "project_name": p.name,
                "name": c.title,
                "ref": c.ref,
                "head_sha": c.head_sha or "",
                "status": str(c.status),
                "is_running": running,
                "started_by": c.created_by,
                "started_at": c.created_at,
                "last_activity_at": last,
                "expires_at": (last + timedelta(minutes=settings.SESSION_IDLE_MINUTES))
                if running and last
                else None,
                "agent_handle": handles.get(c.agent_id),
                "pushed_seed_id": c.pushed_seed_id,
                "context_window": await resolve_session_context_window(c),
            }
        )

    if state == "running":
        items = [i for i in items if i["is_running"]]
    elif state == "ended":
        items = [i for i in items if not i["is_running"]]

    epoch = datetime.min.replace(tzinfo=UTC)
    items.sort(key=lambda i: (not i["is_running"], -(i["last_activity_at"] or epoch).timestamp()))
    return items[offset : offset + limit]


async def get_session_item(
    session: AsyncSession, actor: User, kind: Literal["chat", "terminal"], session_id: str
) -> dict[str, Any]:
    """Resolve one session (and its project) by kind + id, for the `/sessions/<kind>/<id>` page."""
    owner_pid: str | None = None
    if kind == "terminal":
        row = await session.get(SandboxSession, session_id)
        owner_pid = row.project_id if row else None
    else:
        chat = await session.get(AgentSession, session_id)
        owner_pid = chat.project_id if chat else None
    if owner_pid is None:
        raise NotFound("Session not found")
    projects = await _visible_projects(session, actor)
    if owner_pid not in projects:
        raise NotFound("Session not found")
    items = await list_sessions(session, actor, project_id=owner_pid, limit=300)
    match = next((i for i in items if i["kind"] == kind and i["id"] == session_id), None)
    if match is None:
        raise NotFound("Session not found")
    return match
