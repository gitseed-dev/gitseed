import inspect
import logging
import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gitseed_common.enums import (
    RUN_ACTIVE_STATUSES,
    SEED_OPEN_STATUSES,
    ActivityType,
    ActorType,
    AgentKind,
    ApprovalDecision,
    OrgRole,
    ProjectRole,
    ProjectVisibility,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from sqlalchemy import delete, func, or_, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.activity import Activity
from gitseed_core.models.agent import Agent
from gitseed_core.models.approval import Approval
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment, SeedTask
from gitseed_core.models.user import User
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Conflict, Forbidden, Invalid, NotFound
from gitseed_core.services.git_browse import merge_seed_branch_into_default
from gitseed_core.services.git_merge import (
    check_seed_pull_request_status,
    reconcile_seed_if_pr_merged_externally,
)
from gitseed_core.services.policies import (
    STAGE_ORDER,
    first_enabled_stage,
    get_effective_stage_policy,
    next_enabled_stage,
)
from gitseed_core.services.projects import get_member_role, get_project
from gitseed_core.services.settings import get_org_settings

logger = logging.getLogger(__name__)


async def resolve_should_stage_seed(
    session: AsyncSession,
    project: Project,
    staged: bool | None = None,
) -> bool:
    """Resolve whether a newly created seed should start in STAGED status."""
    if staged is not None:
        return bool(staged)
    cfg = project.sandbox_config or {}
    if (
        "stage_seeds_by_default" in cfg
        and cfg.get("stage_seeds_by_default") is not None
    ):
        return bool(cfg.get("stage_seeds_by_default"))
    org_settings = await get_org_settings(session)
    return bool(getattr(org_settings, "default_stage_seeds", False))


async def reset_orphaned_running_tasks(
    session: AsyncSession,
    seed_id: str,
    *,
    exclude_run_id: str | None = None,
    now: datetime | None = None,
) -> int:
    """Reset `SeedTask` rows stuck in `RUNNING` back to `PENDING` when no active `EXECUTE` run remains."""
    exec_fn = getattr(session, "execute", None)
    if not callable(exec_fn) or getattr(exec_fn, "side_effect", None) is not None:
        return 0

    ts = now or datetime.now(UTC)
    stmt = select(Run).where(
        Run.seed_id == seed_id,
        Run.stage == SeedStage.EXECUTE,
        col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
    )
    if exclude_run_id:
        stmt = stmt.where(Run.id != exclude_run_id)

    try:
        active_res = await session.execute(stmt)
        scalars_fn = getattr(active_res, "scalars", None)
        if callable(scalars_fn):
            scalars_obj = scalars_fn()
            if inspect.iscoroutine(scalars_obj):
                scalars_obj.close()
                return 0
            all_fn = getattr(scalars_obj, "all", None)
            active_runs = all_fn() if callable(all_fn) else []
            if inspect.iscoroutine(active_runs):
                active_runs.close()
                return 0
            if isinstance(active_runs, list) and active_runs:
                return 0

        running_res = await session.execute(
            select(SeedTask).where(
                SeedTask.seed_id == seed_id,
                SeedTask.status == TaskStatus.RUNNING,
            )
        )
        t_scalars_fn = getattr(running_res, "scalars", None)
        if not callable(t_scalars_fn):
            return 0
        t_scalars = t_scalars_fn()
        if inspect.iscoroutine(t_scalars):
            t_scalars.close()
            return 0
        t_all_fn = getattr(t_scalars, "all", None)
        tasks = t_all_fn() if callable(t_all_fn) else []
        if inspect.iscoroutine(tasks):
            tasks.close()
            return 0
        if not isinstance(tasks, list):
            return 0
        reset_count = 0
        for task in tasks:
            if isinstance(task, SeedTask) and task.status == TaskStatus.RUNNING:
                task.status = TaskStatus.PENDING
                task.started_at = None
                task.updated_at = ts
                reset_count += 1
        return reset_count
    except Exception as exc:
        logger.warning(
            "Failed to reset orphaned running tasks for seed %s: %s",
            seed_id,
            exc,
        )
        return 0


async def get_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> Seed:
    """Retrieve a seed by ID (or 'slug#number') and verify the caller can view its project."""
    seed: Seed | None = None
    if "#" in seed_id:
        slug_part, _, num_part = seed_id.partition("#")
        if num_part.isdigit():
            project = await get_project(session, actor, slug_part)
            stmt = select(Seed).where(
                Seed.project_id == project.id,
                Seed.number == int(num_part),
            )
            seed = (await session.execute(stmt)).scalar_one_or_none()
    else:
        seed = await session.get(Seed, seed_id)

    if seed is None:
        raise NotFound("Seed not found")

    # Verify project visibility
    await get_project(session, actor, seed.project_id)
    return seed


async def _require_seed_developer(
    session: AsyncSession,
    actor: User,
    seed_or_project_id: Seed | str,
) -> tuple[Project, ProjectRole]:
    """Ensure the caller has at least DEVELOPER role on the project and it is not archived."""
    project_id = (
        seed_or_project_id.project_id
        if isinstance(seed_or_project_id, Seed)
        else seed_or_project_id
    )
    project = await get_project(session, actor, project_id)
    if project.archived_at is not None:
        raise Invalid("Cannot modify seeds in an archived project")

    if actor.org_role == OrgRole.ADMIN:
        return project, ProjectRole.MAINTAINER

    role = await get_member_role(session, project.id, actor.id)
    if role not in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER):
        raise Forbidden("Project developer or maintainer privileges required")
    return project, role


async def list_seeds(
    session: AsyncSession,
    actor: User,
    *,
    project_id: str | None = None,
    stage: SeedStage | None = None,
    status: SeedStatus | None = None,
    open_only: bool = False,
    autonomy: SeedAutonomy | None = None,
    tag: str | None = None,
    created_by: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[Seed]:
    """List seeds across visible projects, ordered by updated_at descending."""
    if limit < 1 or limit > 1000:
        raise Invalid("Limit must be between 1 and 1000")
    if offset < 0:
        raise Invalid("Offset must be non-negative")

    stmt = select(Seed)

    if project_id is not None:
        project = await get_project(session, actor, project_id)
        stmt = stmt.where(Seed.project_id == project.id)
    elif actor.org_role != OrgRole.ADMIN:
        visible_projects = (
            select(Project.id)
            .outerjoin(
                ProjectMember,
                (col(ProjectMember.project_id) == col(Project.id))
                & (col(ProjectMember.user_id) == actor.id),
            )
            .where(
                or_(
                    col(Project.visibility) == ProjectVisibility.PUBLIC,
                    col(ProjectMember.user_id).is_not(None),
                )
            )
        )
        stmt = stmt.where(col(Seed.project_id).in_(visible_projects))

    if stage is not None:
        stmt = stmt.where(Seed.stage == stage)
    if status is not None:
        stmt = stmt.where(Seed.status == status)
    elif open_only:
        stmt = stmt.where(col(Seed.status).in_([s.value for s in SEED_OPEN_STATUSES]))
    if autonomy is not None:
        stmt = stmt.where(Seed.autonomy == autonomy)
    if created_by is not None:
        stmt = stmt.where(Seed.created_by == created_by)
    if tag and tag.strip():
        stmt = stmt.where(
            text(
                "EXISTS (SELECT 1 FROM jsonb_array_elements_text(seed.tags) AS t "
                "WHERE lower(t) = :tag_val)"
            ).bindparams(tag_val=tag.strip().lower())
        )

    stmt = stmt.order_by(col(Seed.updated_at).desc()).offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


MULTI_OPINION_STAGES: tuple[SeedStage, ...] = (
    SeedStage.IDEATE,
    SeedStage.PLAN,
    SeedStage.REVIEW,
)
MAX_OPINIONS_PER_STAGE = 3


async def get_stage_cycle_start(
    session: AsyncSession,
    seed: Seed,
    stage: SeedStage | None = None,
) -> datetime | None:
    """Return the timestamp when the current stage gate cycle began."""
    target_stage = stage or seed.stage
    return (
        seed.stage_entered_at or seed.created_at
        if target_stage == seed.stage
        else seed.created_at
    )


async def list_stage_cycle_runs(
    session: AsyncSession,
    seed: Seed,
    stage: SeedStage | None = None,
) -> list[Run]:
    """List all runs belonging to the current stage gate cycle in chronological order."""
    target_stage = stage or seed.stage
    cycle_start = await get_stage_cycle_start(session, seed, target_stage)
    stmt = select(Run).where(
        Run.seed_id == seed.id,
        Run.stage == target_stage,
    )
    if cycle_start is not None:
        stmt = stmt.where(col(Run.created_at) >= cycle_start)
    stmt = stmt.order_by(col(Run.created_at).asc(), col(Run.id).asc())
    res = await session.execute(stmt)
    scalars_fn = getattr(res, "scalars", None)
    if not callable(scalars_fn):
        return []
    scalars_obj = scalars_fn()
    if inspect.iscoroutine(scalars_obj):
        scalars_obj.close()
        return []
    all_fn = getattr(scalars_obj, "all", None)
    if not callable(all_fn):
        return []
    rows = all_fn()
    if inspect.iscoroutine(rows):
        rows.close()
        return []
    if not isinstance(rows, (list, tuple)):
        return []
    return [r for r in rows if isinstance(r, Run)]


def _format_plan_task_description(
    description: str,
    target_files: list[str] | None,
) -> str:
    base = description.strip()
    cleaned_files = [str(f).strip() for f in (target_files or []) if str(f).strip()]
    if not cleaned_files:
        return base
    files_line = ", ".join(f"`{f}`" for f in cleaned_files)
    if f"Target files: {files_line}" in base:
        return base
    return f"{base}\n\nTarget files: {files_line}" if base else f"Target files: {files_line}"


async def apply_run_draft_to_seed(
    session: AsyncSession,
    seed: Seed,
    target_run: Run,
    cycle_runs: list[Run] | None = None,
    *,
    selected_by_user: bool = False,
) -> None:
    """Apply a succeeded Ideate or Plan run's draft output to the Seed and mark it selected."""
    out = dict(target_run.output) if isinstance(target_run.output, dict) else {}
    now = datetime.now(UTC)
    runs_to_update = (
        cycle_runs
        if cycle_runs is not None
        else await list_stage_cycle_runs(session, seed, target_run.stage)
    )
    if target_run.stage == SeedStage.IDEATE:
        first_run = runs_to_update[0] if runs_to_update else target_run
        f_out = dict(first_run.output) if isinstance(first_run.output, dict) else {}
        if "previous_specification" not in f_out:
            f_out["previous_title"] = seed.title
            f_out["previous_description"] = seed.description
            f_out["previous_specification"] = seed.specification
            f_out["previous_acceptance_criteria"] = list(seed.acceptance_criteria or [])
            first_run.output = f_out
            if first_run.id == target_run.id:
                out = dict(f_out)

        raw_title = str(out.get("title") or "").strip()
        if raw_title:
            seed.title = raw_title[:200]
        raw_desc = str(out.get("description") or "").strip()
        if raw_desc:
            seed.description = raw_desc
        if "specification" in out and out.get("specification") is not None:
            seed.specification = str(out["specification"]).strip()
        raw_criteria = out.get("acceptance_criteria")
        if isinstance(raw_criteria, list):
            seed.acceptance_criteria = [
                str(c).strip() for c in raw_criteria if str(c).strip()
            ]
    elif target_run.stage == SeedStage.PLAN:
        if "plan" in out and out.get("plan") is not None:
            seed.plan = str(out["plan"]).strip()
        raw_tasks = out.get("tasks")
        if isinstance(raw_tasks, list):
            await session.execute(
                delete(SeedTask).where(col(SeedTask.seed_id) == seed.id)
            )
            await session.flush()
            for idx, item in enumerate(raw_tasks):
                if not isinstance(item, dict):
                    continue
                t_title = str(item.get("title") or "").strip()
                if not t_title:
                    continue
                t_desc = str(item.get("description") or "")
                t_files = item.get("target_files") if isinstance(item.get("target_files"), list) else None
                session.add(
                    SeedTask(
                        seed_id=seed.id,
                        position=idx,
                        title=t_title[:200],
                        description=_format_plan_task_description(t_desc, t_files),
                        status=TaskStatus.PENDING,
                        created_at=now,
                        updated_at=now,
                    )
                )

    for r in runs_to_update:
        if r.id == target_run.id:
            r_out = dict(r.output) if isinstance(r.output, dict) else {}
            r_out["selected"] = True
            if selected_by_user:
                r_out["selected_by_user"] = True
            r.output = r_out
        else:
            r_out = dict(r.output) if isinstance(r.output, dict) else {}
            if r_out.get("selected") or r_out.get("selected_by_user"):
                r_out["selected"] = False
                r_out["selected_by_user"] = False
                r.output = r_out


async def _validate_and_resolve_ideate_agents(
    session: AsyncSession,
    project_id: str,
    initial_stage: SeedStage,
    autonomy: SeedAutonomy,
    extra_agent_ids: list[str],
) -> list[Agent]:
    """Validate upfront multi-agent Ideate selection and return the ordered list of Stage Agents."""
    cleaned = [a.strip() for a in extra_agent_ids if a and a.strip()]
    if not cleaned:
        return []
    if initial_stage != SeedStage.IDEATE:
        raise Invalid("Upfront multi-agent selection is only supported for the Ideate stage")
    if autonomy == SeedAutonomy.AUTONOMOUS:
        raise Invalid("Upfront multi-agent selection is not supported for autonomous seeds")
    pol = await get_effective_stage_policy(session, project_id, SeedStage.IDEATE)
    if not pol.allow_second_opinions:
        raise Forbidden("Second opinions are disabled by policy for the Ideate stage")

    ordered_ids: list[str] = []
    if pol.agent_id:
        ordered_ids.append(pol.agent_id)
    for aid in cleaned:
        if aid not in ordered_ids:
            ordered_ids.append(aid)

    if len(ordered_ids) > MAX_OPINIONS_PER_STAGE:
        raise Invalid(
            f"At most {MAX_OPINIONS_PER_STAGE} agents (default + {MAX_OPINIONS_PER_STAGE - 1} extra) can run per stage"
        )

    agents: list[Agent] = []
    for aid in ordered_ids:
        ag = await session.get(Agent, aid)
        if ag is None or ag.archived_at is not None or not ag.enabled:
            raise Invalid(f"Agent '{aid}' is not found or not enabled")
        if ag.kind != AgentKind.STAGE:
            raise Invalid(f"Agent '@{ag.handle}' is not a stage agent")
        agents.append(ag)
    return agents


async def _enqueue_multi_agent_ideate_runs(
    session: AsyncSession,
    seed: Seed,
    agents: list[Agent],
    now: datetime,
) -> list[Run]:
    """Create queued Ideate runs for the default agent and any extra upfront opinion agents."""
    if len(agents) <= 1:
        return []
    created_runs: list[Run] = []
    for idx, ag in enumerate(agents, start=1):
        run = Run(
            seed_id=seed.id,
            task_id=None,
            stage=SeedStage.IDEATE,
            attempt=idx,
            agent_id=ag.id,
            runtime=ag.runtime,
            model=ag.model,
            status=RunStatus.QUEUED,
            output={"multi_opinion": True, "second_opinion": True}
            if idx > 1
            else {"multi_opinion": True},
            created_at=now,
            updated_at=now,
        )
        session.add(run)
        created_runs.append(run)
    await session.flush()
    return created_runs


async def create_seed(
    session: AsyncSession,
    actor: User,
    *,
    project_id: str,
    title: str,
    description: str = "",
    specification: str = "",
    acceptance_criteria: list[str] | None = None,
    plan: str | None = None,
    tags: list[str] | None = None,
    stage: SeedStage | None = None,
    fast_track: bool = False,
    autonomy: SeedAutonomy = SeedAutonomy.SUPERVISED,
    budget_cap: Decimal | None = None,
    staged: bool | None = None,
    ideate_agent_ids: list[str] | None = None,
) -> Seed:
    """Create a seed with an atomic project-scoped number and record seed.created."""
    project, _ = await _require_seed_developer(session, actor, project_id)

    initial_stage = (
        SeedStage.EXECUTE
        if fast_track
        else (stage or await first_enabled_stage(session, project.id))
    )
    pol = await get_effective_stage_policy(session, project.id, initial_stage)
    if not pol.enabled and initial_stage not in (SeedStage.EXECUTE, SeedStage.MERGE):
        raise Invalid(f"Stage '{str(initial_stage)}' is disabled for this project")

    ideate_agents = await _validate_and_resolve_ideate_agents(
        session,
        project.id,
        initial_stage,
        autonomy,
        [] if fast_track else (ideate_agent_ids or []),
    )

    should_stage = await resolve_should_stage_seed(session, project, staged=staged)
    initial_status = SeedStatus.STAGED if should_stage else SeedStatus.QUEUED

    counter_stmt = (
        update(Project)
        .where(col(Project.id) == project.id)
        .values(seed_counter=col(Project.seed_counter) + 1)
        .returning(col(Project.seed_counter))
    )
    seed_number = int((await session.execute(counter_stmt)).scalar_one())

    now = datetime.now(UTC)
    cleaned_criteria = [
        c.strip() for c in (acceptance_criteria or []) if c and c.strip()
    ]
    cleaned_tags = [t.strip() for t in (tags or []) if t and t.strip()]
    effective_spec = specification.strip()
    if initial_stage != SeedStage.IDEATE and not effective_spec:
        effective_spec = description.strip() or title.strip()

    seed = Seed(
        project_id=project.id,
        number=seed_number,
        title=title.strip(),
        description=description.strip(),
        specification=effective_spec,
        acceptance_criteria=cleaned_criteria,
        plan=plan.strip() if plan else None,
        tags=cleaned_tags,
        stage=initial_stage,
        status=initial_status,
        autonomy=autonomy,
        stage_entered_at=now,
        budget_cap=budget_cap,
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(seed)
    await session.flush()

    if initial_stage == SeedStage.EXECUTE:
        session.add(
            SeedTask(
                seed_id=seed.id,
                position=0,
                title=seed.title[:200],
                description=seed.description or seed.specification,
                status=TaskStatus.PENDING,
                created_at=now,
                updated_at=now,
            )
        )
        await session.flush()

    created_data: dict[str, Any] = {
        "number": seed.number,
        "title": seed.title,
        "description": seed.description,
        "specification": seed.specification,
        "acceptance_criteria": list(seed.acceptance_criteria or []),
        "stage": str(seed.stage),
        "status": str(seed.status),
        "autonomy": str(seed.autonomy),
    }
    if fast_track or initial_stage == SeedStage.EXECUTE:
        created_data["fast_track"] = True
    if len(ideate_agents) > 1:
        created_data["ideate_agent_ids"] = [ag.id for ag in ideate_agents[1:]]
        if initial_status == SeedStatus.QUEUED:
            await _enqueue_multi_agent_ideate_runs(session, seed, ideate_agents, now)

    record(
        session,
        type=ActivityType.SEED_CREATED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        seed_id=seed.id,
        data=created_data,
    )
    await session.commit()
    await session.refresh(seed)
    return seed


async def update_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    specification: str | None = None,
    acceptance_criteria: list[str] | None = None,
    plan: str | None = None,
    tags: list[str] | None = None,
    autonomy: SeedAutonomy | None = None,
    budget_cap: Decimal | None = None,
    fields_set: set[str] | None = None,
) -> Seed:
    """Update a seed's fields and record autonomy or budget changes."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    fields = fields_set or set()

    is_closed_or_merged = seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED)

    if title is not None:
        seed.title = title.strip()
    if description is not None:
        seed.description = description.strip()
    if specification is not None:
        cleaned_spec = specification.strip()
        if cleaned_spec != (seed.specification or ""):
            if is_closed_or_merged or seed.stage != SeedStage.IDEATE:
                raise Conflict(
                    "Specification is locked after the Ideate stage (or when a seed is closed/merged) and cannot be modified"
                )
            seed.specification = cleaned_spec
    if (
        fields_set is None or "acceptance_criteria" in fields
    ) and acceptance_criteria is not None:
        cleaned_criteria = [
            c.strip() for c in acceptance_criteria if c and c.strip()
        ]
        if cleaned_criteria != list(seed.acceptance_criteria or []):
            if is_closed_or_merged or seed.stage != SeedStage.IDEATE:
                raise Conflict(
                    "Acceptance criteria are locked after the Ideate stage (or when a seed is closed/merged) and cannot be modified"
                )
            seed.acceptance_criteria = cleaned_criteria
    if "plan" in fields:
        cleaned_plan = plan.strip() if plan else None
        if (cleaned_plan or None) != ((seed.plan or "").strip() or None):
            if is_closed_or_merged or seed.stage not in (SeedStage.IDEATE, SeedStage.PLAN):
                raise Conflict(
                    "Implementation plan is locked after the Plan stage (or when a seed is closed/merged) and cannot be modified"
                )
            seed.plan = cleaned_plan
    if tags is not None:
        seed.tags = [t.strip() for t in tags if t and t.strip()]

    if autonomy is not None and autonomy != seed.autonomy:
        old_autonomy = seed.autonomy
        seed.autonomy = autonomy
        record(
            session,
            type=ActivityType.SEED_AUTONOMY_CHANGED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={"from": str(old_autonomy), "to": str(autonomy)},
        )

    if "budget_cap" in fields:
        old_cap = seed.budget_cap
        seed.budget_cap = budget_cap
        if budget_cap is not None and (old_cap is None or budget_cap > old_cap):
            if (
                seed.status == SeedStatus.BLOCKED
                and seed.blocked_reason
                and "budget" in seed.blocked_reason.lower()
                and budget_cap > seed.spent
            ):
                seed.status = SeedStatus.QUEUED
                seed.blocked_reason = None
            record(
                session,
                type=ActivityType.BUDGET_RAISED,
                actor_type=ActorType.USER,
                actor_id=actor.id,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={"budget_cap": str(budget_cap)},
            )

    seed.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(seed)
    return seed


async def start_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> Seed:
    """Transition a staged seed into queued status so the scheduler picks it up."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
        raise Invalid(f"Cannot start a {str(seed.status)} seed")
    if seed.status != SeedStatus.STAGED:
        return seed

    now = datetime.now(UTC)
    seed.status = SeedStatus.QUEUED
    seed.blocked_reason = None
    seed.stage_entered_at = now
    seed.updated_at = now

    if seed.stage == SeedStage.IDEATE and seed.autonomy != SeedAutonomy.AUTONOMOUS:
        created_ev = (
            await session.execute(
                select(Activity)
                .where(
                    Activity.seed_id == seed.id,
                    Activity.type == ActivityType.SEED_CREATED,
                )
                .order_by(col(Activity.id).asc())
                .limit(1)
            )
        ).scalars().first()
        if created_ev is not None and isinstance(created_ev.data, dict):
            raw_extra = created_ev.data.get("ideate_agent_ids")
            if isinstance(raw_extra, list) and raw_extra:
                extra_ids = [str(x).strip() for x in raw_extra if str(x).strip()]
                if extra_ids:
                    ideate_agents = await _validate_and_resolve_ideate_agents(
                        session,
                        seed.project_id,
                        seed.stage,
                        seed.autonomy,
                        extra_ids,
                    )
                    await _enqueue_multi_agent_ideate_runs(
                        session, seed, ideate_agents, now
                    )

    record(
        session,
        type=ActivityType.STAGE_CHANGED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"from": "staged", "to": str(seed.stage)},
    )
    await session.commit()
    await session.refresh(seed)
    return seed


async def advance_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    target_stage: SeedStage | None = None,
    fast_track: bool = False,
) -> Seed:
    """Move an open seed to a target stage (or the next enabled stage) and queue it."""
    seed = await get_seed(session, actor, seed_id)
    _, actor_role = await _require_seed_developer(session, actor, seed)

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
        raise Invalid(f"Cannot advance a {str(seed.status)} seed")

    if fast_track and target_stage is None:
        target_stage = SeedStage.EXECUTE

    if (
        seed.status == SeedStatus.STAGED
        and not fast_track
        and (target_stage is None or target_stage == seed.stage)
    ):
        return await start_seed(session, actor, seed_id)

    next_stage = target_stage or await next_enabled_stage(
        session, seed.project_id, seed.stage
    )
    if next_stage is None:
        raise Invalid("Seed is already at the final stage ('merge')")

    cur_idx = STAGE_ORDER.index(seed.stage)
    tgt_idx = STAGE_ORDER.index(next_stage)
    if tgt_idx <= cur_idx:
        raise Invalid(
            "Target stage must be ahead of current stage; use move-back to return a seed to an earlier stage"
        )

    pol = await get_effective_stage_policy(session, seed.project_id, next_stage)
    if not pol.enabled and next_stage not in (SeedStage.EXECUTE, SeedStage.MERGE):
        raise Invalid(f"Stage '{str(next_stage)}' is disabled for this project")

    is_fast_track_to_execute = bool(
        (fast_track or target_stage == SeedStage.EXECUTE)
        and seed.stage in (SeedStage.IDEATE, SeedStage.PLAN)
        and next_stage == SeedStage.EXECUTE
    )

    if actor.org_role != OrgRole.ADMIN:
        stages_to_check = STAGE_ORDER[cur_idx:tgt_idx]
        for st in stages_to_check:
            st_pol = (
                pol
                if st == next_stage
                else await get_effective_stage_policy(session, seed.project_id, st)
            )
            if not st_pol.requires_approval:
                continue
            if (
                st_pol.min_approver_role == ProjectRole.MAINTAINER
                and actor_role != ProjectRole.MAINTAINER
            ):
                raise Forbidden(
                    f"Stage '{str(st)}' requires a project maintainer to approve or advance"
                )
            if not st_pol.allow_self_approval and seed.created_by == actor.id:
                raise Forbidden(
                    f"Self-approval is disabled by policy for stage '{str(st)}'; another approver must approve this stage"
                )
            if st_pol.min_approvers > 1:
                raise Forbidden(
                    f"Stage '{str(st)}' requires {st_pol.min_approvers} approvals via stage gate review before advancing"
                )
            if (
                st == seed.stage
                and seed.status == SeedStatus.AWAITING_APPROVAL
                and actor_role != ProjectRole.MAINTAINER
                and not is_fast_track_to_execute
            ):
                raise Forbidden(
                    f"Use stage gate approval to advance a seed awaiting approval at '{str(st)}', or ask a project maintainer"
                )

    now = datetime.now(UTC)
    old_stage = seed.stage
    old_status = seed.status

    if is_fast_track_to_execute:
        if not (seed.specification or "").strip():
            seed.specification = (seed.description or seed.title).strip()
        existing_task_count = await session.scalar(
            select(func.count())
            .select_from(SeedTask)
            .where(SeedTask.seed_id == seed.id)
        )
        if not isinstance(existing_task_count, int) or existing_task_count == 0:
            session.add(
                SeedTask(
                    seed_id=seed.id,
                    position=0,
                    title=seed.title[:200],
                    description=seed.description or seed.specification,
                    status=TaskStatus.PENDING,
                    created_at=now,
                    updated_at=now,
                )
            )

    seed.stage = next_stage
    seed.status = SeedStatus.QUEUED
    seed.blocked_reason = None
    seed.stage_entered_at = now
    seed.updated_at = now

    if old_stage != next_stage:
        stage_data: dict[str, Any] = {"from": str(old_stage), "to": str(next_stage)}
        if is_fast_track_to_execute:
            stage_data["fast_track"] = True
        record(
            session,
            type=ActivityType.STAGE_CHANGED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data=stage_data,
        )

    await session.commit()

    if old_status in (SeedStatus.RUNNING, SeedStatus.QUEUED):
        from gitseed_core.services.runs import cancel_run

        active_runs_res = await session.execute(
            select(Run).where(
                Run.seed_id == seed.id,
                Run.stage == old_stage,
                col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
            )
        )
        scalars_fn = getattr(active_runs_res, "scalars", None)
        if callable(scalars_fn):
            scalars_obj = scalars_fn()
            if not inspect.iscoroutine(scalars_obj):
                all_fn = getattr(scalars_obj, "all", None)
                if callable(all_fn):
                    rows = all_fn()
                    if isinstance(rows, (list, tuple)):
                        for r in rows:
                            if isinstance(r, Run):
                                await cancel_run(session, actor, r.id)

    await session.refresh(seed)
    return seed


async def approve_checkpoint(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    approved_through: Decimal,
) -> Seed:
    """Raise checkpoint_approved_through and unblock the seed if paused at a spend checkpoint."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    if approved_through <= seed.checkpoint_approved_through:
        raise Invalid(
            "approved_through must be greater than current checkpoint_approved_through"
        )

    seed.checkpoint_approved_through = approved_through
    if seed.status == SeedStatus.BLOCKED and approved_through > seed.spent:
        seed.status = SeedStatus.QUEUED
        seed.blocked_reason = None
    seed.updated_at = datetime.now(UTC)

    record(
        session,
        type=ActivityType.BUDGET_RAISED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"checkpoint_approved_through": str(approved_through)},
    )
    await session.commit()
    await session.refresh(seed)
    return seed


async def close_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> Seed:
    """Close/abandon a seed without merging and record seed.closed."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    if seed.status == SeedStatus.CLOSED:
        return seed
    if seed.status == SeedStatus.MERGED:
        raise Invalid("Cannot close a seed that has already been merged")

    old_status = seed.status
    now = datetime.now(UTC)
    seed.status = SeedStatus.CLOSED
    seed.blocked_reason = None
    seed.closed_at = now
    seed.closed_by = actor.id
    seed.updated_at = now

    record(
        session,
        type=ActivityType.SEED_CLOSED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"stage": str(seed.stage)},
    )
    await session.commit()

    if old_status in (SeedStatus.RUNNING, SeedStatus.QUEUED):
        from gitseed_core.services.runs import cancel_run

        active_runs_res = await session.execute(
            select(Run).where(
                Run.seed_id == seed.id,
                col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
            )
        )
        scalars_fn = getattr(active_runs_res, "scalars", None)
        if callable(scalars_fn):
            scalars_obj = scalars_fn()
            if not inspect.iscoroutine(scalars_obj):
                all_fn = getattr(scalars_obj, "all", None)
                if callable(all_fn):
                    rows = all_fn()
                    if isinstance(rows, (list, tuple)):
                        for r in rows:
                            if isinstance(r, Run):
                                await cancel_run(session, actor, r.id)

    await session.refresh(seed)
    return seed


async def reopen_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> Seed:
    """Reopen a closed seed back to QUEUED in its current stage (maintainer only)."""
    seed = await get_seed(session, actor, seed_id)
    _, role = await _require_seed_developer(session, actor, seed)
    if role != ProjectRole.MAINTAINER:
        raise Forbidden("Project maintainer privileges required to reopen a seed")

    if seed.status != SeedStatus.CLOSED:
        raise Invalid("Only closed seeds can be reopened")

    now = datetime.now(UTC)
    seed.status = SeedStatus.QUEUED
    seed.closed_at = None
    seed.closed_by = None
    seed.blocked_reason = None
    seed.stage_entered_at = now
    seed.updated_at = now

    record(
        session,
        type=ActivityType.SEED_REOPENED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"stage": str(seed.stage)},
    )
    await session.commit()
    await session.refresh(seed)
    return seed


async def stop_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> Seed:
    """Stop the agent working on a seed: cancel its active run and park the seed as STOPPED.

    The seed keeps its stage, branch and history; `resume_seed` re-queues the same stage.
    """
    from gitseed_core.services.runs import cancel_run  # local: runs imports this module

    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    if seed.status == SeedStatus.STOPPED:
        return seed
    if seed.status not in (SeedStatus.RUNNING, SeedStatus.QUEUED):
        raise Invalid(f"Only a running or queued seed can be stopped (this one is {str(seed.status)})")

    # Park the seed first so the scheduler can't re-queue it between the two commits
    now = datetime.now(UTC)
    stopped_from = str(seed.status)
    seed.status = SeedStatus.STOPPED
    seed.blocked_reason = None
    seed.updated_at = now
    record(
        session,
        type=ActivityType.SEED_STOPPED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"stage": str(seed.stage), "from": stopped_from},
    )
    await session.commit()

    active_runs = (
        await session.execute(
            select(Run).where(
                Run.seed_id == seed.id,
                col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
            )
        )
    ).scalars().all()
    for run in active_runs:
        await cancel_run(session, actor, run.id)

    await session.refresh(seed)
    return seed


async def resume_seed(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> Seed:
    """Resume a stopped seed: re-queue its current stage so an agent picks it up again."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    if seed.status != SeedStatus.STOPPED:
        raise Invalid("Only a stopped seed can be resumed")

    seed.status = SeedStatus.QUEUED
    seed.updated_at = datetime.now(UTC)
    record(
        session,
        type=ActivityType.SEED_RESUMED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"stage": str(seed.stage)},
    )
    await session.commit()
    await session.refresh(seed)
    return seed


MOVE_BACK_ALLOWED_STATUSES = (
    SeedStatus.AWAITING_APPROVAL,
    SeedStatus.STOPPED,
    SeedStatus.FAILED,
    SeedStatus.BLOCKED,
)


async def move_seed_back(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    target_stage: SeedStage,
    reason: str,
) -> Seed:
    """Send a seed back to an earlier stage with a reason, and re-queue it there.

    Approvals for every stage from the target up to the current one are reset (a
    changes_requested row per stage, same rule the gate counter uses), so each gate must
    pass again. The reason is posted as a comment @mentioning the target stage's agent so
    the re-run knows what to change. Branch, commits, spec and plan are kept.
    """
    seed = await get_seed(session, actor, seed_id)
    _, actor_role = await _require_seed_developer(session, actor, seed)

    cleaned = (reason or "").strip()
    if not cleaned:
        raise Invalid("Say what should change when moving a seed back")
    if seed.status not in MOVE_BACK_ALLOWED_STATUSES:
        raise Invalid(
            f"A seed can be moved back only at a gate, or when stopped, failed or blocked (it is {str(seed.status)})"
        )
    cur_idx = STAGE_ORDER.index(seed.stage)
    tgt_idx = STAGE_ORDER.index(target_stage)
    if tgt_idx >= cur_idx:
        raise Invalid("Pick an earlier stage to move back to")
    target_pol = await get_effective_stage_policy(session, seed.project_id, target_stage)
    if not target_pol.enabled and target_stage not in (SeedStage.EXECUTE, SeedStage.MERGE):
        raise Invalid(f"Stage '{str(target_stage)}' is disabled for this project")

    now = datetime.now(UTC)
    from_stage = seed.stage
    snapshot = _build_stage_snapshot(seed)
    for stage in STAGE_ORDER[tgt_idx : cur_idx + 1]:
        session.add(
            Approval(
                seed_id=seed.id,
                stage=stage,
                snapshot=snapshot,
                decision=ApprovalDecision.CHANGES_REQUESTED,
                comment=f"Moved back to {str(target_stage)}: {cleaned}",
                automatic=False,
                decided_by=actor.id,
                approver_role=actor_role,
                created_at=now,
            )
        )

    agent = await session.get(Agent, target_pol.agent_id) if target_pol.agent_id else None
    mention = f"@{agent.handle} " if agent else ""
    session.add(
        SeedComment(
            seed_id=seed.id,
            author_type=ActorType.USER,
            author_id=actor.id,
            stage=target_stage,
            body=f"{mention}Moved back from {str(from_stage).capitalize()} to "
            f"{str(target_stage).capitalize()}: {cleaned}",
            created_at=now,
            updated_at=now,
        )
    )

    seed.stage = target_stage
    seed.status = SeedStatus.QUEUED
    seed.blocked_reason = None
    seed.stage_entered_at = now
    seed.updated_at = now
    record(
        session,
        type=ActivityType.STAGE_CHANGED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={"from": str(from_stage), "to": str(target_stage), "reason": "moved_back", "comment": cleaned[:500]},
    )
    await session.commit()
    await session.refresh(seed)
    return seed


def _build_stage_snapshot(seed: Seed) -> dict[str, Any]:
    return {
        "title": seed.title,
        "specification": seed.specification,
        "acceptance_criteria": seed.acceptance_criteria,
        "plan": seed.plan,
    }


async def build_stage_approval_snapshot(
    session: AsyncSession,
    seed: Seed,
    stage: SeedStage | None = None,
    *,
    cycle_runs: list[Run] | None = None,
) -> dict[str, Any]:
    """Build the stage gate snapshot including chosen vs unselected runs or review verdicts."""
    target_stage = stage or seed.stage
    snap = _build_stage_snapshot(seed)
    if target_stage not in MULTI_OPINION_STAGES:
        return snap
    resolved_runs = (
        cycle_runs
        if cycle_runs is not None
        else await list_stage_cycle_runs(session, seed, target_stage)
    )
    if not resolved_runs:
        return snap

    if target_stage in (SeedStage.IDEATE, SeedStage.PLAN):
        succeeded_runs = [r for r in resolved_runs if r.status == RunStatus.SUCCEEDED]
        explicitly_selected = [
            r
            for r in succeeded_runs
            if isinstance(r.output, dict) and r.output.get("selected") is True
        ]
        explicitly_unselected_all = (
            bool(succeeded_runs)
            and all(
                isinstance(r.output, dict) and r.output.get("selected") is False
                for r in succeeded_runs
            )
        )
        if explicitly_selected:
            chosen = explicitly_selected[-1]
        elif explicitly_unselected_all:
            chosen = None
        elif succeeded_runs:
            chosen = succeeded_runs[-1]
        else:
            chosen = None

        snap["selected_run_id"] = chosen.id if chosen else None
        snap["selected_agent_id"] = chosen.agent_id if chosen else None
        snap["unselected_runs"] = [
            {
                "run_id": r.id,
                "agent_id": r.agent_id,
                "status": str(r.status),
            }
            for r in resolved_runs
            if chosen is None or r.id != chosen.id
        ]
    elif target_stage == SeedStage.REVIEW:
        reviews_list: list[dict[str, Any]] = []
        for r in resolved_runs:
            r_out = r.output if isinstance(r.output, dict) else {}
            reviews_list.append(
                {
                    "run_id": r.id,
                    "agent_id": r.agent_id,
                    "status": str(r.status),
                    "approved": r_out.get("approved"),
                    "summary_comment": r_out.get("summary_comment"),
                    "findings": r_out.get("findings")
                    if isinstance(r_out.get("findings"), list)
                    else [],
                }
            )
        snap["reviews"] = reviews_list
    return snap


async def request_second_opinion(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    agent_id: str,
    compute_backend_id: str | None = None,
) -> Run:
    """Enqueue an extra stage agent run for Ideate, Plan, or Review (up to 3 total per cycle)."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
        raise Invalid(f"Cannot request a second opinion on a {str(seed.status)} seed")

    if seed.stage not in MULTI_OPINION_STAGES:
        raise Invalid(
            "Second opinions are only supported in the Ideate, Plan, and Review stages"
        )

    pol = await get_effective_stage_policy(session, seed.project_id, seed.stage)
    if not pol.allow_second_opinions:
        raise Forbidden(
            f"Second opinions are disabled by policy for stage '{str(seed.stage)}'"
        )

    agent = await session.get(Agent, agent_id.strip())
    if agent is None or agent.archived_at is not None or not agent.enabled:
        raise NotFound("Agent not found or disabled")
    if agent.kind != AgentKind.STAGE:
        raise Invalid(f"Agent '@{agent.handle}' is not a stage agent")

    cycle_runs = await list_stage_cycle_runs(session, seed, seed.stage)
    non_cancelled_runs = [
        r for r in cycle_runs if r.status != RunStatus.CANCELLED
    ]
    if len(non_cancelled_runs) >= MAX_OPINIONS_PER_STAGE:
        raise Conflict(
            f"Maximum of {MAX_OPINIONS_PER_STAGE} agent opinions reached for this stage"
        )

    for r in non_cancelled_runs:
        if r.agent_id == agent.id and r.status in (
            RunStatus.QUEUED,
            RunStatus.STARTING,
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
        ):
            raise Conflict(
                f"Agent '@{agent.handle}' has already run or is queued for this stage"
            )

    max_attempt = await session.scalar(
        select(func.max(Run.attempt)).where(
            Run.seed_id == seed.id,
            Run.stage == seed.stage,
            col(Run.task_id).is_(None),
        )
    )
    next_attempt = 1 if max_attempt is None else int(max_attempt) + 1
    now = datetime.now(UTC)

    for r in cycle_runs:
        r_out = dict(r.output) if isinstance(r.output, dict) else {}
        r_out["multi_opinion"] = True
        r.output = r_out

    run = Run(
        seed_id=seed.id,
        task_id=None,
        stage=seed.stage,
        attempt=next_attempt,
        agent_id=agent.id,
        runtime=agent.runtime,
        model=agent.model,
        compute_backend_id=compute_backend_id,
        status=RunStatus.QUEUED,
        output={"second_opinion": True, "multi_opinion": True},
        created_at=now,
        updated_at=now,
    )
    session.add(run)

    any_running = any(
        r.status in (RunStatus.STARTING, RunStatus.RUNNING) for r in cycle_runs
    )
    seed.status = SeedStatus.RUNNING if any_running else SeedStatus.QUEUED
    seed.blocked_reason = None
    seed.updated_at = now

    await session.commit()
    await session.refresh(run)
    return run


async def select_seed_run(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    run_id: str,
) -> Seed:
    """Select which agent run's draft (or 'original' user draft) is active on the seed."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
        raise Conflict("Cannot switch drafts on a closed or merged seed")

    cleaned_run_id = run_id.strip()
    now = datetime.now(UTC)

    if cleaned_run_id == "original":
        if seed.stage != SeedStage.IDEATE:
            raise Conflict(
                "Specification is locked after the Ideate stage and cannot be reverted"
            )
        cycle_runs = await list_stage_cycle_runs(session, seed, SeedStage.IDEATE)
        res = await session.execute(
            select(Activity)
            .where(
                Activity.seed_id == seed.id,
                Activity.type == ActivityType.SEED_CREATED,
            )
            .order_by(col(Activity.id).asc())
            .limit(1)
        )
        created_ev: Activity | None = None
        scalars_fn = getattr(res, "scalars", None)
        if callable(scalars_fn):
            scalars_obj = scalars_fn()
            if inspect.iscoroutine(scalars_obj):
                scalars_obj.close()
            else:
                first_fn = getattr(scalars_obj, "first", None)
                if callable(first_fn):
                    maybe_ev = first_fn()
                    if inspect.iscoroutine(maybe_ev):
                        maybe_ev.close()
                    elif isinstance(maybe_ev, Activity):
                        created_ev = maybe_ev
        ev_data = (
            created_ev.data
            if created_ev is not None and isinstance(created_ev.data, dict)
            else {}
        )
        first_out = (
            cycle_runs[0].output
            if cycle_runs and isinstance(cycle_runs[0].output, dict)
            else {}
        )
        orig_title = str(
            ev_data.get("title") or first_out.get("previous_title") or seed.title
        ).strip()
        orig_desc = str(
            ev_data.get("description")
            if "description" in ev_data
            else (first_out.get("previous_description") or seed.description or "")
        ).strip()
        orig_spec = str(
            ev_data.get("specification")
            if "specification" in ev_data
            else (first_out.get("previous_specification") or "")
        ).strip()
        orig_criteria = ev_data.get("acceptance_criteria")
        if not isinstance(orig_criteria, list):
            orig_criteria = first_out.get("previous_acceptance_criteria") or []

        if orig_title:
            seed.title = orig_title[:200]
        seed.description = orig_desc
        seed.specification = orig_spec
        seed.acceptance_criteria = [
            str(c).strip() for c in orig_criteria if str(c).strip()
        ]
        for r in cycle_runs:
            r_out = dict(r.output) if isinstance(r.output, dict) else {}
            r_out["selected"] = False
            r_out["selected_by_user"] = False
            r.output = r_out
        seed.updated_at = now
        await session.commit()
        await session.refresh(seed)
        return seed

    target_run = await session.get(Run, cleaned_run_id)
    if target_run is None or target_run.seed_id != seed.id:
        raise NotFound("Run not found for this seed")
    if target_run.status != RunStatus.SUCCEEDED:
        raise Invalid("Only a succeeded run can be selected as the active draft")
    if target_run.stage == SeedStage.IDEATE:
        if seed.stage != SeedStage.IDEATE:
            raise Conflict(
                "Specification is locked after the Ideate stage and cannot be modified"
            )
    elif target_run.stage == SeedStage.PLAN:
        if seed.stage not in (SeedStage.IDEATE, SeedStage.PLAN):
            raise Conflict(
                "Implementation plan is locked after the Plan stage and cannot be modified"
            )
    else:
        raise Invalid("Selecting a run draft is only supported for Ideate and Plan runs")

    cycle_runs = await list_stage_cycle_runs(session, seed, target_run.stage)
    await apply_run_draft_to_seed(
        session,
        seed,
        target_run,
        cycle_runs,
        selected_by_user=True,
    )
    seed.updated_at = now
    await session.commit()
    await session.refresh(seed)
    return seed


async def list_approvals(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> list[Approval]:
    """List approval decisions on a seed in chronological order."""
    seed = await get_seed(session, actor, seed_id)
    stmt = (
        select(Approval)
        .where(Approval.seed_id == seed.id)
        .order_by(col(Approval.created_at).asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def _extract_latest_review_feedback(
    session: AsyncSession, seed_id: str
) -> tuple[str | None, list[str]]:
    """Return `(summary_comment, findings)` aggregated across current cycle Review runs."""
    stmt = (
        select(Run)
        .where(
            Run.seed_id == seed_id,
            Run.stage == SeedStage.REVIEW,
            Run.status == RunStatus.SUCCEEDED,
        )
        .order_by(col(Run.created_at).desc())
    )
    res = await session.execute(stmt)
    cycle_runs: list[Run] = []
    scalars_fn = getattr(res, "scalars", None)
    if callable(scalars_fn):
        scalars_obj = scalars_fn()
        if inspect.iscoroutine(scalars_obj):
            scalars_obj.close()
        else:
            all_fn = getattr(scalars_obj, "all", None)
            if callable(all_fn):
                rows = all_fn()
                if inspect.iscoroutine(rows):
                    rows.close()
                elif isinstance(rows, (list, tuple)):
                    cycle_runs = [
                        r
                        for r in rows
                        if isinstance(r, Run) and isinstance(r.output, dict)
                    ]
    if not cycle_runs:
        single_fn = getattr(res, "scalar_one_or_none", None)
        rev_run = single_fn() if callable(single_fn) else None
        if inspect.iscoroutine(rev_run):
            rev_run.close()
            rev_run = None
        if isinstance(rev_run, Run) and isinstance(rev_run.output, dict):
            cycle_runs = [rev_run]
    if not cycle_runs:
        return None, []

    rejecting = [
        r for r in cycle_runs if isinstance(r.output, dict) and r.output.get("approved") is False
    ]
    source_runs = rejecting if rejecting else cycle_runs
    summaries: list[str] = []
    findings: list[str] = []
    for r in source_runs:
        r_out = r.output if isinstance(r.output, dict) else {}
        s = str(r_out.get("summary_comment") or "").strip()
        if s and s not in summaries:
            summaries.append(s)
        raw_f = r_out.get("findings")
        if isinstance(raw_f, list):
            for item in raw_f:
                cleaned_item = str(item).strip()
                if cleaned_item and cleaned_item not in findings:
                    findings.append(cleaned_item)
    summary_text = "\n\n".join(summaries) if summaries else None
    return summary_text, findings


async def create_remediation_task_and_return_to_execute(
    session: AsyncSession,
    seed: Seed,
    *,
    from_stage: SeedStage,
    operator_comment: str | None,
    review_summary: str | None,
    review_findings: list[str],
    actor_type: ActorType,
    actor_id: str | None,
    now: datetime,
) -> SeedTask:
    """Create a PENDING remediation SeedTask with review/operator feedback and move seed to Execute."""
    desc_parts: list[str] = []
    if operator_comment:
        desc_parts.append(f"**Operator Changes Requested:**\n{operator_comment}")
    if review_summary:
        desc_parts.append(f"**Reviewer Audit Summary:**\n{review_summary}")
    if review_findings:
        bullet_findings = "\n".join(f"- {item}" for item in review_findings)
        desc_parts.append(f"**Reviewer Findings to Fix:**\n{bullet_findings}")
    desc_parts.append(
        "Inspect the existing implementation on the seed branch in `/workspace`, "
        "fix all reported issues and integration bugs, verify tests and entry points run "
        "cleanly, and save updated files to `/workspace`."
    )
    full_description = "\n\n".join(desc_parts)

    existing_pending = (
        await session.execute(
            select(SeedTask)
            .where(
                SeedTask.seed_id == seed.id,
                SeedTask.status == TaskStatus.PENDING,
            )
            .order_by(col(SeedTask.position).desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing_pending is not None:
        existing_pending.description = full_description
        existing_pending.updated_at = now
        remediation_task = existing_pending
    else:
        max_pos = await session.scalar(
            select(func.max(SeedTask.position)).where(SeedTask.seed_id == seed.id)
        )
        next_pos = 0 if max_pos is None else int(max_pos) + 1
        short_label = (operator_comment or review_summary or "Resolve review findings").splitlines()[0].strip()
        if len(short_label) > 90:
            short_label = f"{short_label[:87]}..."
        task_title = f"Address review feedback: {short_label}"[:200]
        remediation_task = SeedTask(
            seed_id=seed.id,
            position=next_pos,
            title=task_title,
            description=full_description,
            status=TaskStatus.PENDING,
            created_at=now,
            updated_at=now,
        )
        session.add(remediation_task)
        await session.flush()

    seed.stage = SeedStage.EXECUTE
    seed.status = SeedStatus.QUEUED
    seed.blocked_reason = None
    seed.stage_entered_at = now
    seed.updated_at = now

    if from_stage != SeedStage.EXECUTE:
        record(
            session,
            type=ActivityType.STAGE_CHANGED,
            actor_type=actor_type,
            actor_id=actor_id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={
                "from": str(from_stage),
                "to": str(SeedStage.EXECUTE),
                "reason": "changes_requested",
                "remediation_task_id": remediation_task.id,
            },
        )
    return remediation_task


async def create_approval(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    decision: ApprovalDecision,
    comment: str | None = None,
) -> Approval:
    """Record a stage gate approval decision and transition the seed accordingly."""
    seed = await get_seed(session, actor, seed_id)
    _, actor_role = await _require_seed_developer(session, actor, seed)

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED, SeedStatus.STAGED):
        raise Invalid(f"Cannot submit an approval on a {str(seed.status)} seed")
    if decision == ApprovalDecision.APPROVED and seed.status == SeedStatus.FAILED:
        raise Invalid("Cannot approve a failed seed; retry or request changes instead")

    cleaned_comment = comment.strip() if comment and comment.strip() else None
    review_summary: str | None = None
    review_findings: list[str] = []
    if (
        decision == ApprovalDecision.CHANGES_REQUESTED
        and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE)
    ):
        review_summary, review_findings = await _extract_latest_review_feedback(
            session, seed.id
        )
        if not cleaned_comment and (review_summary or review_findings):
            cleaned_comment = (
                review_summary
                or "; ".join(review_findings[:3])
                or "Changes requested based on review findings"
            )

    if (
        decision in (ApprovalDecision.REJECTED, ApprovalDecision.CHANGES_REQUESTED)
        and not cleaned_comment
    ):
        raise Invalid(f"A comment is required when decision is '{str(decision)}'")

    pol = await get_effective_stage_policy(session, seed.project_id, seed.stage)
    if (
        pol.min_approver_role == ProjectRole.MAINTAINER
        and actor_role != ProjectRole.MAINTAINER
    ):
        raise Forbidden(
            f"Stage '{str(seed.stage)}' requires a project maintainer to approve"
        )

    code_editor_ids: set[str] = set()
    latest_code_edit_at: datetime | None = None
    if decision == ApprovalDecision.APPROVED and not pol.allow_self_approval:
        code_edit_events = (
            await session.execute(
                select(Activity)
                .where(
                    Activity.seed_id == seed.id,
                    Activity.type == ActivityType.STAGE_CHANGED,
                )
                .order_by(col(Activity.id).asc())
            )
        ).scalars().all()
        for ev in code_edit_events:
            if isinstance(ev.data, dict) and ev.data.get("reason") == "code_edited":
                if ev.actor_id:
                    code_editor_ids.add(ev.actor_id)
                if ev.occurred_at is not None:
                    latest_code_edit_at = ev.occurred_at

    if (
        decision == ApprovalDecision.APPROVED
        and seed.created_by == actor.id
        and not pol.allow_self_approval
    ):
        raise Forbidden("Self-approval is disabled by policy for this stage")

    if (
        decision == ApprovalDecision.APPROVED
        and not pol.allow_self_approval
        and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE)
        and actor.id in code_editor_ids
    ):
        raise Forbidden(
            "You cannot approve this stage after editing its code unless self-approval is enabled by policy"
        )

    if decision == ApprovalDecision.APPROVED and seed.stage == SeedStage.MERGE:
        pr_info = await check_seed_pull_request_status(session, seed)
        if pr_info.get("merged"):
            await reconcile_seed_if_pr_merged_externally(
                session, seed, pr_info=pr_info
            )
            existing_merge_app = (
                await session.execute(
                    select(Approval)
                    .where(
                        Approval.seed_id == seed.id,
                        Approval.stage == SeedStage.MERGE,
                    )
                    .order_by(col(Approval.created_at).desc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if existing_merge_app is not None:
                return existing_merge_app
        elif pr_info.get("state") == "open" and not pr_info.get(
            "ready_to_merge", True
        ):
            raise Conflict(
                str(
                    pr_info.get("block_reason")
                    or "Pull Request cannot be merged yet while CI checks or required PR approvals are pending."
                )
            )

    now = datetime.now(UTC)
    approval = Approval(
        seed_id=seed.id,
        stage=seed.stage,
        snapshot=await build_stage_approval_snapshot(
            session,
            seed,
            seed.stage,
            cycle_runs=None
            if decision == ApprovalDecision.APPROVED
            and seed.stage in MULTI_OPINION_STAGES
            else [],
        ),
        decision=decision,
        comment=cleaned_comment,
        automatic=False,
        decided_by=actor.id,
        approver_role=actor_role,
        created_at=now,
    )
    session.add(approval)
    await session.flush()

    if decision == ApprovalDecision.REJECTED:
        seed.status = SeedStatus.CLOSED
        seed.blocked_reason = None
        seed.closed_at = now
        seed.closed_by = actor.id
        seed.updated_at = now
        record(
            session,
            type=ActivityType.APPROVAL_REJECTED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={"stage": str(seed.stage), "approval_id": approval.id},
        )
        record(
            session,
            type=ActivityType.SEED_CLOSED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={"stage": str(seed.stage), "reason": "approval_rejected"},
        )
    elif decision == ApprovalDecision.CHANGES_REQUESTED:
        original_stage = seed.stage
        record(
            session,
            type=ActivityType.APPROVAL_CHANGES_REQUESTED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={"stage": str(original_stage), "approval_id": approval.id},
        )
        if original_stage in (SeedStage.REVIEW, SeedStage.MERGE):
            await create_remediation_task_and_return_to_execute(
                session,
                seed,
                from_stage=original_stage,
                operator_comment=cleaned_comment,
                review_summary=review_summary,
                review_findings=review_findings,
                actor_type=ActorType.USER,
                actor_id=actor.id,
                now=now,
            )
        else:
            seed.status = SeedStatus.QUEUED
            seed.blocked_reason = None
            seed.stage_entered_at = now
            seed.updated_at = now
    else:
        record(
            session,
            type=ActivityType.APPROVAL_GRANTED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={"stage": str(seed.stage), "approval_id": approval.id},
        )

        # Count distinct valid approvers on this stage since stage_entered_at
        # (and after any code edit or changes_requested)
        stage_approvals = (
            await session.execute(
                select(Approval)
                .where(
                    Approval.seed_id == seed.id,
                    Approval.stage == seed.stage,
                )
                .order_by(col(Approval.created_at).asc())
            )
        ).scalars().all()

        def _as_utc(dt: datetime | None) -> datetime | None:
            if dt is None:
                return None
            return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)

        stage_cutoff = _as_utc(seed.stage_entered_at)
        edit_cutoff = _as_utc(latest_code_edit_at)

        approvers: set[str] = set()
        for row in stage_approvals:
            row_ts = _as_utc(row.created_at)
            if stage_cutoff and row_ts and row_ts < stage_cutoff:
                continue
            if edit_cutoff and row_ts and row_ts < edit_cutoff:
                continue
            if row.decision in (
                ApprovalDecision.CHANGES_REQUESTED,
                ApprovalDecision.REJECTED,
            ):
                approvers.clear()
            elif row.decision == ApprovalDecision.APPROVED and row.decided_by:
                if row.decided_by == seed.created_by and not pol.allow_self_approval:
                    continue
                if (
                    not pol.allow_self_approval
                    and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE)
                    and row.decided_by in code_editor_ids
                ):
                    continue
                if (
                    pol.min_approver_role == ProjectRole.MAINTAINER
                    and row.approver_role != ProjectRole.MAINTAINER
                ):
                    continue
                approvers.add(row.decided_by)

        if len(approvers) >= pol.min_approvers:
            if seed.stage == SeedStage.MERGE:
                merge_sha = await merge_seed_branch_into_default(session, seed)
                if not merge_sha:
                    raise Conflict(
                        "Failed to merge seed branch into default branch; check PR mergeability and repository credentials."
                    )
                seed.status = SeedStatus.MERGED
                seed.blocked_reason = None
                seed.merged_at = now
                seed.updated_at = now
                merged_data: dict[str, Any] = {"approval_id": approval.id}
                if merge_sha:
                    merged_data["merge_commit_sha"] = merge_sha
                    latest_merge_run = (
                        await session.execute(
                            select(Run)
                            .where(
                                Run.seed_id == seed.id,
                                Run.stage == SeedStage.MERGE,
                            )
                            .order_by(col(Run.created_at).desc())
                            .limit(1)
                        )
                    ).scalar_one_or_none()
                    if latest_merge_run is not None:
                        latest_merge_run.output = {
                            **(latest_merge_run.output if isinstance(latest_merge_run.output, dict) else {}),
                            "merge_commit_sha": merge_sha,
                        }
                record(
                    session,
                    type=ActivityType.SEED_MERGED,
                    actor_type=ActorType.USER,
                    actor_id=actor.id,
                    project_id=seed.project_id,
                    seed_id=seed.id,
                    data=merged_data,
                )
            else:
                next_stage = await next_enabled_stage(
                    session, seed.project_id, seed.stage
                )
                if next_stage is not None:
                    old_stage = seed.stage
                    seed.stage = next_stage
                    seed.status = SeedStatus.QUEUED
                    seed.blocked_reason = None
                    seed.stage_entered_at = now
                    seed.updated_at = now
                    record(
                        session,
                        type=ActivityType.STAGE_CHANGED,
                        actor_type=ActorType.USER,
                        actor_id=actor.id,
                        project_id=seed.project_id,
                        seed_id=seed.id,
                        data={"from": str(old_stage), "to": str(next_stage)},
                    )

    await session.commit()
    await session.refresh(approval)
    return approval


async def list_tasks(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> list[SeedTask]:
    """List tasks for a seed ordered by position."""
    seed = await get_seed(session, actor, seed_id)
    stmt = (
        select(SeedTask)
        .where(SeedTask.seed_id == seed.id)
        .order_by(col(SeedTask.position).asc())
    )
    return list((await session.execute(stmt)).scalars().all())


def _return_seed_to_plan_gate_if_past_plan(
    session: AsyncSession,
    seed: Seed,
    actor: User,
    now: datetime,
) -> bool:
    """If a seed's tasks are modified after Plan approval, return it to the Plan gate.

    Returns True when an active/queued `EXECUTE` seed was reverted and any in-flight
    `EXECUTE` runs should be cancelled.
    """
    if seed.stage in (SeedStage.EXECUTE, SeedStage.REVIEW):
        prev_stage = seed.stage
        prev_status = seed.status
        seed.stage = SeedStage.PLAN
        seed.status = SeedStatus.AWAITING_APPROVAL
        seed.stage_entered_at = now
        seed.updated_at = now
        record(
            session,
            type=ActivityType.STAGE_CHANGED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={
                "from": str(prev_stage),
                "to": str(SeedStage.PLAN),
                "reason": "tasks_modified",
            },
        )
        return prev_stage == SeedStage.EXECUTE and prev_status in (
            SeedStatus.RUNNING,
            SeedStatus.QUEUED,
        )
    return False


async def _cancel_active_execute_runs(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> None:
    """Cancel any active `EXECUTE` runs on `seed_id` after reverting back to `PLAN`."""
    from gitseed_core.services.runs import cancel_run

    exec_fn = getattr(session, "execute", None)
    if not callable(exec_fn):
        return
    try:
        active_runs_res = await session.execute(
            select(Run).where(
                Run.seed_id == seed_id,
                Run.stage == SeedStage.EXECUTE,
                col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
            )
        )
    except Exception:
        return

    scalars_fn = getattr(active_runs_res, "scalars", None)
    if not callable(scalars_fn):
        return
    scalars_obj = scalars_fn()
    if inspect.iscoroutine(scalars_obj):
        scalars_obj.close()
        return
    all_fn = getattr(scalars_obj, "all", None)
    rows = all_fn() if callable(all_fn) else []
    if inspect.iscoroutine(rows):
        rows.close()
        return
    if isinstance(rows, (list, tuple)):
        for r in rows:
            if isinstance(r, Run) and r.stage == SeedStage.EXECUTE:
                await cancel_run(session, actor, r.id)


async def create_task(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    title: str,
    description: str = "",
    position: int | None = None,
) -> SeedTask:
    """Create a task on a seed at the requested or next position."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    max_pos = await session.scalar(
        select(func.max(SeedTask.position)).where(SeedTask.seed_id == seed.id)
    )
    next_pos = 0 if max_pos is None else int(max_pos) + 1
    target_pos = next_pos if position is None else min(position, next_pos)

    if target_pos < next_pos:
        tasks_to_shift = (
            await session.execute(
                select(SeedTask)
                .where(
                    SeedTask.seed_id == seed.id,
                    col(SeedTask.position) >= target_pos,
                )
                .order_by(col(SeedTask.position).desc())
            )
        ).scalars().all()
        for t in tasks_to_shift:
            t.position += 1
        await session.flush()

    now = datetime.now(UTC)
    task = SeedTask(
        seed_id=seed.id,
        position=target_pos,
        title=title.strip(),
        description=description.strip(),
        status=TaskStatus.PENDING,
        created_at=now,
        updated_at=now,
    )
    session.add(task)
    cancel_execute = _return_seed_to_plan_gate_if_past_plan(session, seed, actor, now)
    await session.commit()
    if cancel_execute:
        await _cancel_active_execute_runs(session, actor, seed.id)
    await session.refresh(task)
    return task


async def update_task(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    task_id: str,
    *,
    title: str | None = None,
    description: str | None = None,
    position: int | None = None,
    status: TaskStatus | None = None,
) -> SeedTask:
    """Update a seed task's title, description, position, or status."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    task = await session.get(SeedTask, task_id)
    if task is None or task.seed_id != seed.id:
        raise NotFound("Seed task not found")

    now = datetime.now(UTC)
    plan_spec_changed = False
    if title is not None and title.strip() != task.title:
        task.title = title.strip()
        plan_spec_changed = True
    if description is not None and description.strip() != task.description:
        task.description = description.strip()
        plan_spec_changed = True

    if status is not None and status != task.status:
        task.status = status
        if status in (TaskStatus.RUNNING, TaskStatus.DONE, TaskStatus.FAILED):
            if task.started_at is None:
                task.started_at = now
        if status in (TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.SKIPPED):
            task.completed_at = now
        else:
            task.completed_at = None

    if position is not None and position != task.position:
        plan_spec_changed = True
        all_tasks = (
            await session.execute(
                select(SeedTask)
                .where(SeedTask.seed_id == seed.id)
                .order_by(col(SeedTask.position).asc())
            )
        ).scalars().all()
        ordered = [t for t in all_tasks if t.id != task.id]
        bounded_pos = max(0, min(position, len(ordered)))
        ordered.insert(bounded_pos, task)
        for idx, item in enumerate(ordered):
            item.position = idx

    cancel_execute = False
    if plan_spec_changed:
        cancel_execute = _return_seed_to_plan_gate_if_past_plan(session, seed, actor, now)

    task.updated_at = now
    await session.commit()
    if cancel_execute:
        await _cancel_active_execute_runs(session, actor, seed.id)
    await session.refresh(task)
    return task


async def delete_task(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    task_id: str,
) -> None:
    """Delete a task from a seed and compact remaining task positions."""
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)

    task = await session.get(SeedTask, task_id)
    if task is None or task.seed_id != seed.id:
        raise NotFound("Seed task not found")

    await session.delete(task)
    await session.flush()

    remaining = (
        await session.execute(
            select(SeedTask)
            .where(SeedTask.seed_id == seed.id)
            .order_by(col(SeedTask.position).asc())
        )
    ).scalars().all()
    for idx, item in enumerate(remaining):
        item.position = idx

    now = datetime.now(UTC)
    cancel_execute = _return_seed_to_plan_gate_if_past_plan(session, seed, actor, now)
    await session.commit()
    if cancel_execute:
        await _cancel_active_execute_runs(session, actor, seed.id)


async def list_comments(
    session: AsyncSession,
    actor: User,
    seed_id: str,
) -> list[SeedComment]:
    """List comments on a seed in chronological order."""
    seed = await get_seed(session, actor, seed_id)
    stmt = (
        select(SeedComment)
        .where(SeedComment.seed_id == seed.id)
        .order_by(col(SeedComment.created_at).asc())
    )
    return list((await session.execute(stmt)).scalars().all())


_MENTION_RE = re.compile(r"(?:^|(?<=\s))@([a-z][a-z0-9-]{1,31})\b", re.IGNORECASE)


def extract_agent_mentions(body: str) -> set[str]:
    """Return lowercase agent handles mentioned as @handle in a comment body."""
    return {m.group(1).lower() for m in _MENTION_RE.finditer(body or "")}


async def _mentions_stage_agent(
    session: AsyncSession, seed: Seed, body: str
) -> bool:
    """Return True if the comment @mentions the stage policy's agent or any active stage agent."""
    mentions = extract_agent_mentions(body)
    if not mentions:
        return False
    if "agent" in mentions or "stage" in mentions:
        return True
    pol = await get_effective_stage_policy(session, seed.project_id, seed.stage)
    if pol.agent_id:
        assigned = await session.get(Agent, pol.agent_id)
        if assigned is not None and assigned.handle.lower() in mentions:
            return True
    matched_stage_agent = (
        await session.execute(
            select(Agent.id).where(
                col(Agent.handle).in_(mentions),
                col(Agent.archived_at).is_(None),
            )
        )
    ).first()
    return matched_stage_agent is not None


async def create_comment(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    body: str,
) -> SeedComment:
    """Create a comment on a seed at its current stage (requires developer or maintainer role).

    If the seed is awaiting_approval and the comment @mentions the stage agent, it is
    recorded as a changes_requested Approval row so approvals reset and the stage re-queues.
    """
    seed = await get_seed(session, actor, seed_id)
    _, actor_role = await _require_seed_developer(session, actor, seed)
    cleaned_body = body.strip()
    if not cleaned_body:
        raise Invalid("Comment body cannot be empty")

    now = datetime.now(UTC)
    comment = SeedComment(
        seed_id=seed.id,
        author_type=ActorType.USER,
        author_id=actor.id,
        stage=seed.stage,
        body=cleaned_body,
        created_at=now,
        updated_at=now,
    )
    session.add(comment)
    await session.flush()

    if seed.status in (SeedStatus.AWAITING_APPROVAL, SeedStatus.FAILED):
        if await _mentions_stage_agent(session, seed, cleaned_body):
            if seed.status == SeedStatus.AWAITING_APPROVAL:
                pol = await get_effective_stage_policy(session, seed.project_id, seed.stage)
                can_request_changes = (
                    pol.min_approver_role != ProjectRole.MAINTAINER
                    or actor_role == ProjectRole.MAINTAINER
                )
                if can_request_changes:
                    approval = Approval(
                        seed_id=seed.id,
                        stage=seed.stage,
                        snapshot=_build_stage_snapshot(seed),
                        decision=ApprovalDecision.CHANGES_REQUESTED,
                        comment=cleaned_body,
                        automatic=False,
                        decided_by=actor.id,
                        approver_role=actor_role,
                        created_at=now,
                    )
                    session.add(approval)
                    await session.flush()
                    seed.status = SeedStatus.QUEUED
                    seed.blocked_reason = None
                    seed.updated_at = now
                    record(
                        session,
                        type=ActivityType.APPROVAL_CHANGES_REQUESTED,
                        actor_type=ActorType.USER,
                        actor_id=actor.id,
                        project_id=seed.project_id,
                        seed_id=seed.id,
                        data={
                            "stage": str(seed.stage),
                            "approval_id": approval.id,
                            "via": "comment_mention",
                        },
                    )
            elif seed.status == SeedStatus.FAILED:
                seed.status = SeedStatus.QUEUED
                seed.blocked_reason = None
                seed.updated_at = now

    await session.commit()
    await session.refresh(comment)
    return comment


async def update_comment(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    comment_id: str,
    *,
    body: str,
) -> SeedComment:
    """Edit a comment owned by the caller."""
    seed = await get_seed(session, actor, seed_id)
    comment = await session.get(SeedComment, comment_id)
    if comment is None or comment.seed_id != seed.id:
        raise NotFound("Comment not found")
    if comment.deleted_at is not None:
        raise Invalid("Cannot edit a deleted comment")
    if comment.author_type != ActorType.USER or comment.author_id != actor.id:
        raise Forbidden("You can only edit your own comments")

    cleaned_body = body.strip()
    if not cleaned_body:
        raise Invalid("Comment body cannot be empty")

    now = datetime.now(UTC)
    comment.body = cleaned_body
    comment.edited_at = now
    comment.updated_at = now
    await session.commit()
    await session.refresh(comment)
    return comment


async def delete_comment(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    comment_id: str,
) -> SeedComment:
    """Soft-delete a comment (allowed by author, project maintainer, or org admin)."""
    seed = await get_seed(session, actor, seed_id)
    comment = await session.get(SeedComment, comment_id)
    if comment is None or comment.seed_id != seed.id:
        raise NotFound("Comment not found")

    if comment.deleted_at is not None:
        return comment

    is_author = comment.author_type == ActorType.USER and comment.author_id == actor.id
    if not is_author and actor.org_role != OrgRole.ADMIN:
        role = await get_member_role(session, seed.project_id, actor.id)
        if role != ProjectRole.MAINTAINER:
            raise Forbidden(
                "Only the comment author, a project maintainer, or an org admin can delete comments"
            )

    now = datetime.now(UTC)
    comment.deleted_at = now
    comment.deleted_by = actor.id
    comment.updated_at = now
    await session.commit()
    await session.refresh(comment)
    return comment
