import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gitseed_common.enums import (
    ActivityType,
    ActorType,
    BackendType,
    OrgRole,
    ProjectRole,
    ProjectVisibility,
)
from sqlalchemy import func, or_, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.backend import Backend
from gitseed_core.models.project import (
    Project,
    ProjectBackend,
    ProjectMember,
    ProjectRepository,
)
from gitseed_core.models.user import User
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Conflict, Forbidden, Invalid, NotFound

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,62}$")


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if len(slug) == 1:
        slug = f"{slug}-project"
    return slug[:63].rstrip("-")


def _validate_slug(slug: str) -> str:
    cleaned = slug.strip().lower()
    if not _SLUG_RE.match(cleaned):
        raise Invalid(
            "Project slug must be 2-63 characters, start with a lowercase letter or digit, "
            "and contain only lowercase letters, digits, and hyphens"
        )
    return cleaned


async def _ensure_active_slug_available(
    session: AsyncSession, slug: str, *, exclude_project_id: str | None = None
) -> None:
    stmt = select(Project).where(
        Project.slug == slug,
        col(Project.archived_at).is_(None),
    )
    if exclude_project_id is not None:
        stmt = stmt.where(Project.id != exclude_project_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"An active project with slug '{slug}' already exists")


async def get_member_role(
    session: AsyncSession, project_id: str, user_id: str
) -> ProjectRole | None:
    stmt = select(ProjectMember.role).where(
        ProjectMember.project_id == project_id,
        ProjectMember.user_id == user_id,
    )
    role_val = (await session.execute(stmt)).scalar_one_or_none()
    return ProjectRole(role_val) if role_val is not None else None


async def get_member_roles_map(
    session: AsyncSession, project_ids: list[str], user_id: str
) -> dict[str, ProjectRole]:
    """Batch-fetch a user's project role across multiple projects."""
    if not project_ids:
        return {}
    stmt = select(ProjectMember.project_id, ProjectMember.role).where(
        col(ProjectMember.project_id).in_(project_ids),
        ProjectMember.user_id == user_id,
    )
    return {
        str(pid): ProjectRole(role_val)
        for pid, role_val in (await session.execute(stmt)).all()
    }


async def get_allowed_backend_ids(
    session: AsyncSession, project_id: str
) -> list[str]:
    stmt = (
        select(ProjectBackend.backend_id)
        .where(ProjectBackend.project_id == project_id)
        .order_by(col(ProjectBackend.backend_id).asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_allowed_backend_ids_map(
    session: AsyncSession, project_ids: list[str]
) -> dict[str, list[str]]:
    """Batch-fetch allowed backend IDs across multiple projects."""
    if not project_ids:
        return {}
    stmt = (
        select(ProjectBackend.project_id, ProjectBackend.backend_id)
        .where(col(ProjectBackend.project_id).in_(project_ids))
        .order_by(col(ProjectBackend.backend_id).asc())
    )
    result: dict[str, list[str]] = {pid: [] for pid in project_ids}
    for project_id, backend_id in (await session.execute(stmt)).all():
        result.setdefault(str(project_id), []).append(str(backend_id))
    return result


async def get_project(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> Project:
    """Resolve a project by ID or slug and verify the caller is allowed to view it."""
    if project_id_or_slug.startswith("prj_"):
        project = await session.get(Project, project_id_or_slug)
    else:
        stmt = (
            select(Project)
            .where(Project.slug == project_id_or_slug.strip().lower())
            .order_by(col(Project.archived_at).asc().nulls_first())
        )
        project = (await session.execute(stmt)).scalars().first()

    if project is None:
        raise NotFound("Project not found")

    if actor.org_role != OrgRole.ADMIN and project.visibility != ProjectVisibility.PUBLIC:
        role = await get_member_role(session, project.id, actor.id)
        if role is None:
            raise NotFound("Project not found")

    return project


async def require_project_maintainer(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> Project:
    """Resolve a project and verify the caller is an org admin or project maintainer."""
    project = await get_project(session, actor, project_id_or_slug)
    if actor.org_role == OrgRole.ADMIN:
        return project

    role = await get_member_role(session, project.id, actor.id)
    if role != ProjectRole.MAINTAINER:
        raise Forbidden("Project maintainer or organization admin privileges required")
    return project


async def require_project_contributor(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> Project:
    """Resolve a project and verify the caller is an org admin, project maintainer, or project developer."""
    project = await get_project(session, actor, project_id_or_slug)
    if actor.org_role == OrgRole.ADMIN:
        return project

    role = await get_member_role(session, project.id, actor.id)
    if role not in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER):
        raise Forbidden("Project developer or maintainer privileges required")
    return project


async def list_projects(
    session: AsyncSession,
    actor: User,
    *,
    include_archived: bool = False,
    tag: str | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[Project]:
    """List projects visible to the caller, ordered by name."""
    if limit < 1 or limit > 500:
        raise Invalid("Limit must be between 1 and 500")
    if offset < 0:
        raise Invalid("Offset must be non-negative")
    stmt = select(Project)

    if not include_archived:
        stmt = stmt.where(col(Project.archived_at).is_(None))

    if actor.org_role != OrgRole.ADMIN:
        stmt = stmt.outerjoin(
            ProjectMember,
            (col(ProjectMember.project_id) == col(Project.id))
            & (col(ProjectMember.user_id) == actor.id),
        ).where(
            or_(
                col(Project.visibility) == ProjectVisibility.PUBLIC,
                col(ProjectMember.user_id).is_not(None),
            )
        )

    if tag and tag.strip():
        stmt = stmt.where(
            text(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(project.tags) AS t "
                "WHERE lower(t) = :tag_val)"
            ).bindparams(tag_val=tag.strip().lower())
        )

    stmt = stmt.order_by(col(Project.name).asc()).offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def create_project(
    session: AsyncSession,
    actor: User,
    *,
    name: str,
    slug: str | None = None,
    description: str = "",
    tags: list[str] | None = None,
    visibility: ProjectVisibility = ProjectVisibility.PRIVATE,
    monthly_budget: Decimal | None = None,
    seed_budget_cap: Decimal | None = None,
    spend_checkpoint: Decimal | None = None,
    max_concurrent_runs: int | None = None,
    backends_restricted: bool = False,
) -> Project:
    """Create a project, assign creator as MAINTAINER, and record project.created."""
    if actor.org_role != OrgRole.ADMIN:
        member_roles = list(
            (
                await session.execute(
                    select(ProjectMember.role).where(ProjectMember.user_id == actor.id)
                )
            )
            .scalars()
            .all()
        )
        if member_roles and all(
            ProjectRole(r) == ProjectRole.VIEWER for r in member_roles
        ):
            raise Forbidden("Read-only viewers cannot create new projects")

    resolved_slug = _validate_slug(slug if slug else _slugify(name))
    await _ensure_active_slug_available(session, resolved_slug)

    cleaned_tags = [t.strip() for t in (tags or []) if t and t.strip()]
    now = datetime.now(UTC)

    project = Project(
        name=name.strip(),
        slug=resolved_slug,
        description=description.strip(),
        tags=cleaned_tags,
        visibility=visibility,
        monthly_budget=monthly_budget,
        seed_budget_cap=seed_budget_cap,
        spend_checkpoint=spend_checkpoint,
        max_concurrent_runs=max_concurrent_runs,
        backends_restricted=backends_restricted,
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(project)
    await session.flush()

    session.add(
        ProjectMember(
            project_id=project.id,
            user_id=actor.id,
            role=ProjectRole.MAINTAINER,
            created_by=actor.id,
            created_at=now,
            updated_at=now,
        )
    )
    record(
        session,
        type=ActivityType.PROJECT_CREATED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={
            "name": project.name,
            "slug": project.slug,
            "visibility": str(project.visibility),
        },
    )
    await session.commit()
    await session.refresh(project)
    return project


async def update_project(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    tags: list[str] | None = None,
    visibility: ProjectVisibility | None = None,
    monthly_budget: Decimal | None = None,
    seed_budget_cap: Decimal | None = None,
    spend_checkpoint: Decimal | None = None,
    max_concurrent_runs: int | None = None,
    backends_restricted: bool | None = None,
    sandbox_config: dict[str, Any] | None = None,
    fields_set: set[str] | None = None,
) -> Project:
    """Update a project's metadata, visibility, or budget/concurrency overrides."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    fields = fields_set or set()

    if name is not None:
        project.name = name.strip()

    if slug is not None and slug.strip().lower() != project.slug:
        old_slug = project.slug
        new_slug = _validate_slug(slug)
        if project.archived_at is None:
            await _ensure_active_slug_available(
                session, new_slug, exclude_project_id=project.id
            )
        project.slug = new_slug
        local_repos = (
            await session.execute(
                select(ProjectRepository).where(
                    ProjectRepository.project_id == project.id,
                    ProjectRepository.full_name == f"local/{old_slug}",
                )
            )
        ).scalars().all()
        for lr in local_repos:
            if isinstance(lr, ProjectRepository):
                lr.full_name = f"local/{new_slug}"

    if description is not None:
        project.description = description.strip()
    if tags is not None:
        project.tags = [t.strip() for t in tags if t and t.strip()]
    if visibility is not None:
        project.visibility = visibility
    if backends_restricted is not None:
        if backends_restricted != project.backends_restricted and actor.org_role != OrgRole.ADMIN:
            raise Forbidden("Only organization administrators can modify backends_restricted")
        project.backends_restricted = backends_restricted
    if sandbox_config is not None:
        existing_cfg = dict(project.sandbox_config or {})
        cleaned_cfg: dict[str, Any] = {}
        if isinstance(sandbox_config.get("image"), str) and sandbox_config["image"].strip():
            cleaned_cfg["image"] = sandbox_config["image"].strip()
        if isinstance(sandbox_config.get("setup_command"), str) and sandbox_config["setup_command"].strip():
            cleaned_cfg["setup_command"] = sandbox_config["setup_command"].strip()
        if isinstance(sandbox_config.get("run_command"), str) and sandbox_config["run_command"].strip():
            cleaned_cfg["run_command"] = sandbox_config["run_command"].strip()
        if isinstance(sandbox_config.get("test_command"), str) and sandbox_config["test_command"].strip():
            cleaned_cfg["test_command"] = sandbox_config["test_command"].strip()
        if isinstance(sandbox_config.get("artifact_globs"), list):
            cleaned_cfg["artifact_globs"] = [
                str(g).strip()
                for g in sandbox_config["artifact_globs"]
                if str(g).strip()
            ]
        for bool_key in (
            "require_tests_pass",
            "enable_dependency_cache",
            "enable_warm_workspace",
            "batch_execute_tasks",
            "strict_cli_execution",
            "auto_rebase_seed_branch",
            "auto_create_pr",
            "stage_seeds_by_default",
        ):
            if bool_key in sandbox_config and sandbox_config.get(bool_key) is not None:
                cleaned_cfg[bool_key] = bool(sandbox_config.get(bool_key))
            elif bool_key in sandbox_config and sandbox_config.get(bool_key) is None:
                pass
            elif bool_key in existing_cfg:
                cleaned_cfg[bool_key] = bool(existing_cfg.get(bool_key))

        if "git_clone_depth" in sandbox_config:
            try:
                depth = int(sandbox_config["git_clone_depth"])
                if depth >= 0:
                    cleaned_cfg["git_clone_depth"] = depth
            except (TypeError, ValueError):
                pass
        elif "git_clone_depth" in existing_cfg:
            cleaned_cfg["git_clone_depth"] = existing_cfg["git_clone_depth"]

        if isinstance(sandbox_config.get("blocked_paths"), list):
            cleaned_cfg["blocked_paths"] = [
                str(p).strip()
                for p in sandbox_config["blocked_paths"]
                if str(p).strip()
            ]
        elif isinstance(existing_cfg.get("blocked_paths"), list):
            cleaned_cfg["blocked_paths"] = list(existing_cfg["blocked_paths"])

        if isinstance(existing_cfg.get("webhook_config"), dict):
            wh_cfg = dict(existing_cfg["webhook_config"])
            if "auto_create_pr" in cleaned_cfg:
                wh_cfg["auto_create_pr"] = cleaned_cfg["auto_create_pr"]
            cleaned_cfg["webhook_config"] = wh_cfg

        project.sandbox_config = cleaned_cfg

    budget_raised: dict[str, str] = {}
    if "monthly_budget" in fields:
        if (
            monthly_budget is not None
            and project.monthly_budget is not None
            and monthly_budget > project.monthly_budget
        ):
            budget_raised["monthly_budget"] = str(monthly_budget)
        project.monthly_budget = monthly_budget

    if "seed_budget_cap" in fields:
        if (
            seed_budget_cap is not None
            and project.seed_budget_cap is not None
            and seed_budget_cap > project.seed_budget_cap
        ):
            budget_raised["seed_budget_cap"] = str(seed_budget_cap)
        project.seed_budget_cap = seed_budget_cap

    if "spend_checkpoint" in fields:
        project.spend_checkpoint = spend_checkpoint

    if "max_concurrent_runs" in fields:
        project.max_concurrent_runs = max_concurrent_runs

    if budget_raised:
        record(
            session,
            type=ActivityType.BUDGET_RAISED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=project.id,
            data=budget_raised,
        )

    project.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(project)
    return project


async def archive_project(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> Project:
    """Archive a project and record project.archived."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    if project.archived_at is not None:
        return project

    now = datetime.now(UTC)
    project.archived_at = now
    project.archived_by = actor.id
    project.updated_at = now

    record(
        session,
        type=ActivityType.PROJECT_ARCHIVED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={"name": project.name, "slug": project.slug},
    )
    await session.commit()
    await session.refresh(project)
    return project


async def restore_project(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> Project:
    """Restore an archived project (if its slug is free) and record project.restored."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    if project.archived_at is None:
        return project

    await _ensure_active_slug_available(
        session, project.slug, exclude_project_id=project.id
    )

    now = datetime.now(UTC)
    project.archived_at = None
    project.archived_by = None
    project.updated_at = now

    record(
        session,
        type=ActivityType.PROJECT_RESTORED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={"name": project.name, "slug": project.slug},
    )
    await session.commit()
    await session.refresh(project)
    return project


# ---------------------------------------------------------------------------
# Project Members
# ---------------------------------------------------------------------------


async def _count_project_maintainers(session: AsyncSession, project_id: str) -> int:
    stmt = (
        select(func.count())
        .select_from(ProjectMember)
        .where(
            ProjectMember.project_id == project_id,
            ProjectMember.role == ProjectRole.MAINTAINER,
        )
    )
    return int(await session.scalar(stmt) or 0)


async def list_members(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> list[tuple[ProjectMember, User]]:
    """List members of a project along with their User records."""
    project = await get_project(session, actor, project_id_or_slug)
    stmt = (
        select(ProjectMember, User)
        .join(User, col(ProjectMember.user_id) == col(User.id))
        .where(ProjectMember.project_id == project.id)
        .order_by(col(ProjectMember.created_at).asc())
    )
    return list((await session.execute(stmt)).all())


async def add_member(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    user_id: str,
    role: ProjectRole,
) -> tuple[ProjectMember, User]:
    """Add a user to a project and record member.added."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    target_user = await session.get(User, user_id)
    if target_user is None:
        raise NotFound("User not found")

    existing = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("User is already a member of this project")

    now = datetime.now(UTC)
    member = ProjectMember(
        project_id=project.id,
        user_id=target_user.id,
        role=role,
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(member)
    await session.flush()

    record(
        session,
        type=ActivityType.MEMBER_ADDED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={
            "user_id": target_user.id,
            "email": target_user.email,
            "role": str(role),
        },
    )
    await session.commit()
    await session.refresh(member)
    return member, target_user


async def update_member_role(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    target_user_id: str,
    *,
    role: ProjectRole,
) -> tuple[ProjectMember, User]:
    """Change a project member's role and record member.role_changed."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    row = (
        await session.execute(
            select(ProjectMember, User)
            .join(User, col(ProjectMember.user_id) == col(User.id))
            .where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user_id,
            )
        )
    ).first()
    if row is None:
        raise NotFound("Project member not found")

    member, target_user = row
    if member.role == role:
        return member, target_user

    if (
        member.role == ProjectRole.MAINTAINER
        and role != ProjectRole.MAINTAINER
        and await _count_project_maintainers(session, project.id) <= 1
    ):
        raise Invalid("Cannot demote the last maintainer of a project")

    old_role = member.role
    member.role = role
    member.updated_at = datetime.now(UTC)

    record(
        session,
        type=ActivityType.MEMBER_ROLE_CHANGED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={
            "user_id": target_user.id,
            "from": str(old_role),
            "to": str(role),
        },
    )
    await session.commit()
    await session.refresh(member)
    return member, target_user


async def remove_member(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    target_user_id: str,
) -> None:
    """Remove a user from a project and record member.removed."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    member = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == project.id,
                ProjectMember.user_id == target_user_id,
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise NotFound("Project member not found")

    if (
        member.role == ProjectRole.MAINTAINER
        and await _count_project_maintainers(session, project.id) <= 1
    ):
        raise Invalid("Cannot remove the last maintainer of a project")

    await session.delete(member)
    record(
        session,
        type=ActivityType.MEMBER_REMOVED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={"user_id": target_user_id},
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Project Repositories
# ---------------------------------------------------------------------------


async def list_repositories(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> list[ProjectRepository]:
    """List repositories attached to a project, primary first."""
    project = await get_project(session, actor, project_id_or_slug)
    stmt = (
        select(ProjectRepository)
        .where(ProjectRepository.project_id == project.id)
        .order_by(
            col(ProjectRepository.is_primary).desc(),
            col(ProjectRepository.created_at).asc(),
        )
    )
    rows = list((await session.execute(stmt)).scalars().all())
    if not rows:
        from gitseed_core.services.local_git import maybe_auto_attach_existing_local_workspace

        attached = await maybe_auto_attach_existing_local_workspace(session, project)
        if attached is not None:
            return [attached]
    return rows


async def add_repository(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    backend_id: str,
    full_name: str,
    default_branch: str = "main",
    is_primary: bool | None = None,
) -> ProjectRepository:
    """Attach a Git repository to a project and record repository.added."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)

    backend = await session.get(Backend, backend_id)
    if backend is None:
        raise NotFound("Backend not found")
    if backend.type != BackendType.GIT:
        raise Invalid("Repository backend must be of type 'git'")
    if project.backends_restricted:
        allowed_bids = await get_allowed_backend_ids(session, project.id)
        if backend.id not in allowed_bids:
            raise Forbidden(
                "This Git backend is not in the project's allowed backends list"
            )

    cleaned_name = full_name.strip().strip("/")
    if "/" not in cleaned_name:
        raise Invalid("Repository full_name must be in 'owner/repo' format")
    if (backend.provider or "").strip().lower() == "local":
        expected_local = f"local/{project.slug}"
        if cleaned_name != expected_local:
            raise Forbidden(
                f"Local Git repository full_name must match '{expected_local}' for this project"
            )

    existing = (
        await session.execute(
            select(ProjectRepository).where(
                ProjectRepository.project_id == project.id,
                ProjectRepository.backend_id == backend.id,
                ProjectRepository.full_name == cleaned_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("This repository is already attached to the project")

    existing_repos = (
        await session.execute(
            select(ProjectRepository).where(ProjectRepository.project_id == project.id)
        )
    ).scalars().all()

    make_primary = is_primary if is_primary is not None else (len(existing_repos) == 0)
    if make_primary:
        for r in existing_repos:
            if r.is_primary:
                r.is_primary = False
        await session.flush()

    now = datetime.now(UTC)
    repo = ProjectRepository(
        project_id=project.id,
        backend_id=backend.id,
        full_name=cleaned_name,
        default_branch=default_branch.strip() or "main",
        is_primary=make_primary,
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(repo)
    await session.flush()

    record(
        session,
        type=ActivityType.REPOSITORY_ADDED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={
            "repository_id": repo.id,
            "backend_id": backend.id,
            "full_name": repo.full_name,
            "is_primary": repo.is_primary,
        },
    )
    await session.commit()
    await session.refresh(repo)
    return repo


async def update_repository(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    repo_id: int,
    *,
    default_branch: str | None = None,
    is_primary: bool | None = None,
) -> ProjectRepository:
    """Update a project repository's default branch or primary status."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    repo = await session.get(ProjectRepository, repo_id)
    if repo is None or repo.project_id != project.id:
        raise NotFound("Project repository not found")

    if default_branch is not None:
        repo.default_branch = default_branch.strip() or "main"

    if is_primary is True and not repo.is_primary:
        others = (
            await session.execute(
                select(ProjectRepository).where(
                    ProjectRepository.project_id == project.id,
                    ProjectRepository.id != repo.id,
                    col(ProjectRepository.is_primary).is_(True),
                )
            )
        ).scalars().all()
        for other in others:
            other.is_primary = False
        await session.flush()
        repo.is_primary = True
    elif is_primary is False and repo.is_primary:
        raise Invalid(
            "Cannot unset the primary repository directly; "
            "promote another repository to primary instead"
        )

    repo.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(repo)
    return repo


async def remove_repository(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    repo_id: int,
) -> None:
    """Detach a repository from a project and record repository.removed."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    repo = await session.get(ProjectRepository, repo_id)
    if repo is None or repo.project_id != project.id:
        raise NotFound("Project repository not found")

    was_primary = repo.is_primary
    full_name = repo.full_name
    backend_id = repo.backend_id

    await session.delete(repo)
    await session.flush()

    if was_primary:
        next_repo = (
            await session.execute(
                select(ProjectRepository)
                .where(ProjectRepository.project_id == project.id)
                .order_by(col(ProjectRepository.created_at).asc())
            )
        ).scalars().first()
        if next_repo is not None:
            next_repo.is_primary = True

    record(
        session,
        type=ActivityType.REPOSITORY_REMOVED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={
            "repository_id": repo_id,
            "backend_id": backend_id,
            "full_name": full_name,
        },
    )
    await session.commit()


# ---------------------------------------------------------------------------
# Project Allowed Backends
# ---------------------------------------------------------------------------


async def allow_project_backend(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    backend_id: str,
) -> list[str]:
    """Allow a project to use a specific backend when backends_restricted is enabled."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    if actor.org_role != OrgRole.ADMIN:
        raise Forbidden(
            "Only organization administrators can modify project backend allowlists"
        )
    backend = await session.get(Backend, backend_id)
    if backend is None:
        raise NotFound("Backend not found")

    existing = await session.get(ProjectBackend, (project.id, backend.id))
    if existing is None:
        session.add(
            ProjectBackend(
                project_id=project.id,
                backend_id=backend.id,
                created_by=actor.id,
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()

    return await get_allowed_backend_ids(session, project.id)


async def disallow_project_backend(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    backend_id: str,
) -> list[str]:
    """Remove a backend from a project's allowed backend list."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    if actor.org_role != OrgRole.ADMIN:
        raise Forbidden(
            "Only organization administrators can modify project backend allowlists"
        )
    existing = await session.get(ProjectBackend, (project.id, backend_id))
    if existing is not None:
        await session.delete(existing)
        await session.commit()

    return await get_allowed_backend_ids(session, project.id)
