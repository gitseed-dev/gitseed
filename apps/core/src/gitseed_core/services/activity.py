from typing import Any

from gitseed_common.enums import ActivityType, ActorType, OrgRole, ProjectVisibility
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.activity import Activity
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.seed import Seed
from gitseed_core.models.user import User
from gitseed_core.services import events as events_service
from gitseed_core.services.errors import Invalid, NotFound


def record(
    session: AsyncSession,
    *,
    type: ActivityType,
    actor_type: ActorType,
    actor_id: str | None = None,
    project_id: str | None = None,
    seed_id: str | None = None,
    data: dict[str, Any] | None = None,
) -> Activity:
    """Append an event to the activity audit log within the active transaction and broadcast SSE."""
    event = Activity(
        type=type,
        actor_type=actor_type,
        actor_id=actor_id,
        project_id=project_id,
        seed_id=seed_id,
        data=data or {},
    )
    session.add(event)
    events_service.emit_from_sync(
        type=str(type),
        actor_type=str(actor_type),
        actor_id=actor_id,
        project_id=project_id,
        seed_id=seed_id,
        data=data or {},
    )
    return event


async def _resolve_visible_project(
    session: AsyncSession, user: User, project_id_or_slug: str
) -> Project | None:
    if project_id_or_slug.startswith("prj_"):
        project = await session.get(Project, project_id_or_slug)
    else:
        stmt_p = (
            select(Project)
            .where(Project.slug == project_id_or_slug.strip().lower())
            .order_by(col(Project.archived_at).asc().nulls_first())
        )
        project = (await session.execute(stmt_p)).scalars().first()
    if project is None:
        return None
    if user.org_role == OrgRole.ADMIN or project.visibility == ProjectVisibility.PUBLIC:
        return project
    stmt = select(ProjectMember.id).where(
        ProjectMember.project_id == project.id,
        ProjectMember.user_id == user.id,
    )
    return project if (await session.scalar(stmt)) is not None else None


async def _can_view_project(
    session: AsyncSession, user: User, project_id: str
) -> bool:
    return (await _resolve_visible_project(session, user, project_id)) is not None


async def list_activity(
    session: AsyncSession,
    user: User,
    *,
    project_id: str | None = None,
    seed_id: str | None = None,
    actor_type: ActorType | None = None,
    actor_id: str | None = None,
    type: ActivityType | None = None,
    before_id: int | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Activity]:
    """List activity events visible to the user, newest first, with cursor pagination."""
    if limit < 1 or limit > 500:
        raise Invalid("Limit must be between 1 and 500")
    if offset < 0:
        raise Invalid("Offset must be non-negative")

    stmt = select(Activity)

    org_admin_only_types = (
        ActivityType.USER_INVITED,
        ActivityType.USER_ACTIVATED,
        ActivityType.USER_SUSPENDED,
        ActivityType.USER_REACTIVATED,
        ActivityType.BACKEND_GRANTED,
        ActivityType.BACKEND_REVOKED,
        ActivityType.BACKEND_SECRET_UPDATED,
        ActivityType.PAT_CREATED,
        ActivityType.PAT_REVOKED,
    )

    if project_id is not None:
        project = await _resolve_visible_project(session, user, project_id)
        if project is None:
            raise NotFound("Project not found")
        stmt = stmt.where(Activity.project_id == project.id)
    elif user.org_role != OrgRole.ADMIN:
        visible_projects = (
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
        stmt = stmt.where(col(Activity.project_id).in_(visible_projects))

    if user.org_role != OrgRole.ADMIN:
        stmt = stmt.where(
            col(Activity.type).not_in(org_admin_only_types),
            or_(
                col(Activity.type) != ActivityType.SETTINGS_UPDATED,
                col(Activity.project_id).is_not(None),
            ),
        )

    if seed_id is not None:
        seed = await session.get(Seed, seed_id)
        if seed is None or not await _can_view_project(session, user, seed.project_id):
            raise NotFound("Seed not found")
        stmt = stmt.where(Activity.seed_id == seed.id)
    if actor_type is not None:
        stmt = stmt.where(Activity.actor_type == actor_type)
    if actor_id is not None:
        stmt = stmt.where(Activity.actor_id == actor_id)
    if type is not None:
        stmt = stmt.where(Activity.type == type)
    if before_id is not None:
        stmt = stmt.where(col(Activity.id) < before_id)

    stmt = stmt.order_by(col(Activity.id).desc()).offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars().all())
