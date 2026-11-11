from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gitseed_common.enums import (
    RUN_ACTIVE_STATUSES,
    ActivityType,
    ActorType,
    AgentKind,
    ApprovalDecision,
    BackendType,
    OrgRole,
    ProjectRole,
    ProjectVisibility,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from sqlalchemy import func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedTask
from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import User
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Forbidden, Invalid, NotFound
from gitseed_core.services.gateway import finalize_run_litellm_key
from gitseed_core.services.git_browse import merge_seed_branch_into_default
from gitseed_core.services.git_merge import (
    check_seed_pull_request_status,
    reconcile_seed_if_pr_merged_externally,
)
from gitseed_core.services.policies import (
    EffectiveStagePolicy,
    get_effective_stage_policy,
    next_enabled_stage,
)
from gitseed_core.services.projects import (
    get_allowed_backend_ids,
    get_member_role,
    get_project,
)
from gitseed_core.services.scheduler import (
    fetch_docker_container_logs,
    stop_and_remove_docker_container,
)
from gitseed_core.services.seeds import (
    MULTI_OPINION_STAGES,
    build_stage_approval_snapshot,
    create_remediation_task_and_return_to_execute,
    get_seed,
    list_stage_cycle_runs,
)

_TERMINAL_RUN_STATUSES = frozenset(
    {
        RunStatus.SUCCEEDED,
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.TIMED_OUT,
    }
)


async def _require_run_developer(
    session: AsyncSession, actor: User, seed: Seed
) -> Project:
    project = await get_project(session, actor, seed.project_id)
    if project.archived_at is not None:
        raise Invalid("Cannot modify runs in an archived project")
    if actor.org_role == OrgRole.ADMIN:
        return project
    role = await get_member_role(session, project.id, actor.id)
    if role not in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER):
        raise Forbidden("Project developer or maintainer privileges required")
    return project


async def advance_seed_after_succeeded_run(
    session: AsyncSession,
    seed: Seed,
    run: Run,
    pol: EffectiveStagePolicy | None = None,
    now: datetime | None = None,
    completed_stage_override: SeedStage | None = None,
) -> None:
    """Apply gate / auto-approve / stage transition rules after a Run succeeds."""
    effective_stage = completed_stage_override or run.stage
    if now is None:
        now = datetime.now(UTC)
    if pol is None:
        pol = await get_effective_stage_policy(session, seed.project_id, effective_stage)

    is_multi_opinion = bool(
        completed_stage_override is None
        and isinstance(run.output, dict)
        and (run.output.get("multi_opinion") or run.output.get("second_opinion"))
    )
    cycle_runs: list[Run] = [run]
    if is_multi_opinion and effective_stage in MULTI_OPINION_STAGES:
        fetched_runs = await list_stage_cycle_runs(session, seed, effective_stage)
        if fetched_runs:
            cycle_runs = fetched_runs
        # Wait for all parallel / second-opinion runs in the current stage cycle to finish
        has_active_sibling = any(
            r.id != run.id and r.status in RUN_ACTIVE_STATUSES
            for r in cycle_runs
        )
        if has_active_sibling:
            seed.status = SeedStatus.RUNNING
            seed.blocked_reason = None
            seed.updated_at = now
            return

    succeeded_cycle_runs = [
        r for r in cycle_runs if r.status == RunStatus.SUCCEEDED or r.id == run.id
    ]
    rejecting_review_runs = [
        r
        for r in (succeeded_cycle_runs if effective_stage == SeedStage.REVIEW else [])
        if isinstance(r.output, dict) and r.output.get("approved") is False
    ]
    review_rejected_by_agent = (
        effective_stage == SeedStage.REVIEW
        and (
            bool(rejecting_review_runs)
            or (isinstance(run.output, dict) and run.output.get("approved") is False)
        )
    )

    needs_human_gate = (
        seed.autonomy == SeedAutonomy.MANUAL
        or (seed.autonomy == SeedAutonomy.SUPERVISED and pol.requires_approval)
        or (
            seed.autonomy == SeedAutonomy.AUTONOMOUS
            and pol.requires_approval
            and not pol.allow_auto_approve
        )
    )
    if review_rejected_by_agent:
        # When multiple reviewers ran in a human-gated cycle, show all verdicts at the gate
        # rather than silently returning to Execute before the approver sees the second opinion.
        has_multiple_reviews = len(succeeded_cycle_runs) > 1
        if seed.autonomy != SeedAutonomy.MANUAL and not (
            has_multiple_reviews and needs_human_gate
        ):
            latest_human_reset = await session.scalar(
                select(func.max(Approval.created_at)).where(
                    Approval.seed_id == seed.id,
                    Approval.decision == ApprovalDecision.CHANGES_REQUESTED,
                )
            )
            review_count_stmt = (
                select(func.count())
                .select_from(Run)
                .where(
                    Run.seed_id == seed.id,
                    Run.stage == SeedStage.REVIEW,
                    Run.status == RunStatus.SUCCEEDED,
                )
            )
            if latest_human_reset is not None:
                review_count_stmt = review_count_stmt.where(
                    col(Run.created_at) >= latest_human_reset
                )
            review_run_count = int((await session.scalar(review_count_stmt)) or 0)
            if review_run_count <= max(1, pol.max_attempts):
                source_revs = rejecting_review_runs or [run]
                summaries: list[str] = []
                rev_findings: list[str] = []
                for rev_r in source_revs:
                    rev_out = rev_r.output if isinstance(rev_r.output, dict) else {}
                    s_txt = str(rev_out.get("summary_comment") or "").strip()
                    if s_txt and s_txt not in summaries:
                        summaries.append(s_txt)
                    raw_findings = rev_out.get("findings")
                    if isinstance(raw_findings, list):
                        for f in raw_findings:
                            cf = str(f).strip()
                            if cf and cf not in rev_findings:
                                rev_findings.append(cf)
                rev_summary = "\n\n".join(summaries) if summaries else None
                await create_remediation_task_and_return_to_execute(
                    session,
                    seed,
                    from_stage=SeedStage.REVIEW,
                    operator_comment=None,
                    review_summary=rev_summary,
                    review_findings=rev_findings,
                    actor_type=ActorType.AGENT,
                    actor_id=source_revs[-1].agent_id,
                    now=now,
                )
                return
        seed.status = SeedStatus.AWAITING_APPROVAL
        seed.blocked_reason = None
        seed.updated_at = now
        return

    if needs_human_gate:
        seed.status = SeedStatus.AWAITING_APPROVAL
        seed.blocked_reason = None
        seed.updated_at = now
        return

    if effective_stage == SeedStage.MERGE:
        pr_info = await check_seed_pull_request_status(session, seed)
        if pr_info.get("merged"):
            await reconcile_seed_if_pr_merged_externally(
                session, seed, pr_info=pr_info
            )
            return
        if pr_info.get("state") == "open" and not pr_info.get(
            "ready_to_merge", True
        ):
            seed.status = SeedStatus.AWAITING_APPROVAL
            seed.blocked_reason = None
            seed.updated_at = now
            return

    if (
        seed.autonomy == SeedAutonomy.AUTONOMOUS
        and pol.requires_approval
        and pol.allow_auto_approve
    ):
        auto_approval = Approval(
            seed_id=seed.id,
            stage=effective_stage,
            snapshot=await build_stage_approval_snapshot(
                session, seed, effective_stage, cycle_runs=cycle_runs
            ),
            decision=ApprovalDecision.APPROVED,
            automatic=True,
            decided_by=None,
            approver_role=None,
            created_at=now,
        )
        session.add(auto_approval)
        await session.flush()
        record(
            session,
            type=ActivityType.APPROVAL_GRANTED,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={
                "stage": str(effective_stage),
                "approval_id": auto_approval.id,
                "automatic": True,
            },
        )
    if effective_stage == SeedStage.MERGE:
        merge_sha = await merge_seed_branch_into_default(session, seed)
        seed.stage = SeedStage.MERGE
        seed.status = SeedStatus.MERGED
        seed.blocked_reason = None
        seed.merged_at = now
        seed.updated_at = now
        merged_data: dict[str, Any] = {"run_id": run.id}
        if merge_sha:
            merged_data["merge_commit_sha"] = merge_sha
        record(
            session,
            type=ActivityType.SEED_MERGED,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            project_id=seed.project_id,
            seed_id=seed.id,
            data=merged_data,
        )
    else:
        next_stage = await next_enabled_stage(session, seed.project_id, effective_stage)
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
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={"from": str(old_stage), "to": str(next_stage)},
            )


async def list_runs(
    session: AsyncSession,
    actor: User,
    *,
    seed_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    stage: SeedStage | None = None,
    status: RunStatus | None = None,
    active_only: bool = False,
    limit: int = 200,
    offset: int = 0,
) -> list[Run]:
    """List runs across visible projects, newest first."""
    if limit < 1 or limit > 1000:
        raise Invalid("Limit must be between 1 and 1000")
    if offset < 0:
        raise Invalid("Offset must be non-negative")

    stmt = select(Run).join(Seed, col(Run.seed_id) == col(Seed.id))

    if seed_id is not None:
        seed = await get_seed(session, actor, seed_id)
        stmt = stmt.where(Run.seed_id == seed.id)
    elif project_id is not None:
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

    if agent_id is not None:
        stmt = stmt.where(Run.agent_id == agent_id)
    if task_id is not None:
        stmt = stmt.where(Run.task_id == task_id)
    if stage is not None:
        stmt = stmt.where(Run.stage == stage)
    if status is not None:
        stmt = stmt.where(Run.status == status)
    elif active_only:
        stmt = stmt.where(col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]))

    stmt = stmt.order_by(col(Run.created_at).desc()).offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_run(
    session: AsyncSession,
    actor: User,
    run_id: str,
) -> Run:
    """Retrieve a single run by ID and verify the caller can view its seed's project."""
    run = await session.get(Run, run_id)
    if run is None:
        raise NotFound("Run not found")

    await get_seed(session, actor, run.seed_id)
    return run


_PROTECTED_INFRA_CONTAINERS = frozenset(
    {
        "gitseed-api",
        "gitseed-postgresql",
        "gitseed-postgres",
        "gitseed-litellm",
        "gitseed-web",
        "gitseed-gitea",
        "gitseed-egress-proxy",
        "gitseed-docker-proxy",
        "gitseed-mcp",
    }
)


def _is_safe_run_container_id(run_id: str, container_id: str | None) -> bool:
    """Return True if container_id is safe to inspect/manage for the given run_id."""
    if not container_id:
        return False
    cid = container_id.strip()
    if not cid or ".." in cid or any(ch in cid for ch in ("/", "\\", "?", "#", " ", "\t", "\n")):
        return False
    cid_lower = cid.lower()
    if cid_lower in _PROTECTED_INFRA_CONTAINERS:
        return False
    expected_prefix = f"gitseed-run-{run_id.lower()}"
    if cid_lower.startswith("gitseed-") and not (
        cid_lower == expected_prefix or cid_lower.startswith(f"{expected_prefix}-")
    ):
        return False
    return True


def _validate_safe_container_id(run_id: str, container_id: str | None) -> str | None:
    """Validate and normalize a container_id for a run."""
    if container_id is None:
        return None
    cid = container_id.strip()
    if not cid:
        return None
    if not _is_safe_run_container_id(run_id, cid):
        raise Forbidden("container_id cannot reference protected or foreign GitSeed containers")
    return cid


async def get_run_logs(
    session: AsyncSession,
    actor: User,
    run_id: str,
    *,
    tail_lines: int = 800,
) -> tuple[Run, str]:
    """Retrieve the latest stdout/stderr log tail for a run, querying the live container if active."""
    run = await get_run(session, actor, run_id)
    stored_tail = str((run.output or {}).get("log_tail") or "").strip()

    if (
        run.compute_backend_id
        and run.container_id
        and _is_safe_run_container_id(run.id, run.container_id)
        and (run.status in (RunStatus.STARTING, RunStatus.RUNNING) or not stored_tail)
    ):
        compute_backend = await session.get(Backend, run.compute_backend_id)
        if compute_backend is not None:
            live_logs = await fetch_docker_container_logs(
                compute_backend, run.container_id, tail_lines=tail_lines
            )
            if live_logs:
                existing = dict(run.output or {})
                prev_logs = str(existing.get("log_tail") or "").strip()
                if not prev_logs or len(live_logs) >= len(prev_logs):
                    merged_logs = live_logs[-50000:]
                elif live_logs not in prev_logs and prev_logs not in live_logs:
                    merged_logs = f"{live_logs}\n{prev_logs}"[-50000:]
                else:
                    merged_logs = prev_logs
                if merged_logs != prev_logs:
                    existing["log_tail"] = merged_logs
                    run.output = existing
                    await session.commit()
                    await session.refresh(run)
                stored_tail = merged_logs

    return run, stored_tail


async def create_run(
    session: AsyncSession,
    actor: User,
    *,
    seed_id: str,
    task_id: str | None = None,
    stage: SeedStage | None = None,
    agent_id: str | None = None,
    compute_backend_id: str | None = None,
) -> Run:
    """Queue a new run for a seed, snapshotting agent, runtime, and model."""
    seed = await get_seed(session, actor, seed_id)
    project = await _require_run_developer(session, actor, seed)

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
        raise Invalid(f"Cannot queue a run for a {str(seed.status)} seed")

    run_stage = stage or seed.stage
    pol = await get_effective_stage_policy(session, seed.project_id, run_stage)
    if not pol.enabled and run_stage not in (SeedStage.EXECUTE, SeedStage.MERGE):
        raise Invalid(f"Stage '{str(run_stage)}' is disabled for this project")

    resolved_agent_id = agent_id or pol.agent_id
    if not resolved_agent_id:
        raise Invalid(
            f"No agent specified and no stage policy agent configured for stage '{str(run_stage)}'"
        )

    agent = await session.get(Agent, resolved_agent_id)
    if agent is None and not resolved_agent_id.startswith("agt_"):
        clean_handle = resolved_agent_id.strip().lstrip("@").lower()
        if clean_handle:
            agent = (
                await session.execute(
                    select(Agent).where(
                        col(Agent.archived_at).is_(None),
                        col(Agent.handle) == clean_handle,
                    )
                )
            ).scalars().first()
    if agent is None or agent.archived_at is not None:
        raise NotFound("Agent not found")
    resolved_agent_id = agent.id
    if agent.kind != AgentKind.STAGE:
        raise Invalid("Only stage agents (kind='stage') can execute seed runs")
    if not agent.enabled:
        raise Invalid(f"Agent '@{agent.handle}' is currently disabled")

    if task_id is not None:
        task = await session.get(SeedTask, task_id)
        if task is None or task.seed_id != seed.id:
            raise NotFound("Seed task not found on this seed")

    if compute_backend_id is not None:
        backend = await session.get(Backend, compute_backend_id)
        if backend is None:
            raise NotFound("Compute backend not found")
        if backend.type != BackendType.COMPUTE:
            raise Invalid("compute_backend_id must reference a backend of type 'compute'")
        if project.backends_restricted:
            allowed_bids = await get_allowed_backend_ids(session, project.id)
            if backend.id not in allowed_bids:
                raise Forbidden(
                    "This compute backend is not in the project's allowed backends list"
                )
        if backend.access_restricted:
            grant = await session.get(AgentBackend, (backend.id, agent.id))
            if grant is None:
                raise Forbidden(
                    "Agent is not granted access to this restricted compute backend"
                )

    attempt_stmt = select(func.max(Run.attempt)).where(
        Run.seed_id == seed.id,
        Run.stage == run_stage,
    )
    if task_id is not None:
        attempt_stmt = attempt_stmt.where(Run.task_id == task_id)
    else:
        attempt_stmt = attempt_stmt.where(col(Run.task_id).is_(None))

    max_attempt = await session.scalar(attempt_stmt)
    next_attempt = 1 if max_attempt is None else int(max_attempt) + 1

    now = datetime.now(UTC)
    run = Run(
        seed_id=seed.id,
        task_id=task_id,
        stage=run_stage,
        attempt=next_attempt,
        agent_id=agent.id,
        runtime=agent.runtime,
        model=agent.model,
        compute_backend_id=compute_backend_id,
        status=RunStatus.QUEUED,
        output={"_actor_user_id": actor.id, "actor_user_id": actor.id},
        created_at=now,
        updated_at=now,
    )
    session.add(run)

    if seed.status != SeedStatus.RUNNING:
        seed.status = SeedStatus.QUEUED
        seed.blocked_reason = None
        seed.updated_at = now

    await session.commit()
    await session.refresh(run)
    return run


async def _recompute_seed_spent(
    session: AsyncSession, seed: Seed, project: Project
) -> None:
    total = await session.scalar(
        select(func.coalesce(func.sum(col(Run.cost)), Decimal("0"))).where(
            Run.seed_id == seed.id
        )
    )
    seed.spent = Decimal(str(total or 0))

    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED, SeedStatus.BLOCKED):
        return

    org = await session.get(OrgSettings, 1)
    effective_cap = (
        seed.budget_cap
        if seed.budget_cap is not None
        else (
            project.seed_budget_cap
            if project.seed_budget_cap is not None
            else (org.default_seed_budget_cap if org is not None else None)
        )
    )
    effective_checkpoint = (
        project.spend_checkpoint
        if project.spend_checkpoint is not None
        else (org.default_spend_checkpoint if org is not None else None)
    )

    if effective_cap is not None and seed.spent >= effective_cap:
        seed.status = SeedStatus.BLOCKED
        seed.blocked_reason = f"Seed budget cap ({effective_cap}) reached"
        seed.updated_at = datetime.now(UTC)
        record(
            session,
            type=ActivityType.BUDGET_BLOCKED,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={
                "reason": "seed_budget_cap",
                "spent": str(seed.spent),
                "budget_cap": str(effective_cap),
            },
        )
    elif (
        effective_checkpoint is not None
        and seed.spent >= effective_checkpoint
        and seed.spent > seed.checkpoint_approved_through
    ):
        seed.status = SeedStatus.BLOCKED
        seed.blocked_reason = f"Spend checkpoint ({effective_checkpoint}) reached"
        seed.updated_at = datetime.now(UTC)
        record(
            session,
            type=ActivityType.BUDGET_BLOCKED,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={
                "reason": "spend_checkpoint",
                "spent": str(seed.spent),
                "spend_checkpoint": str(effective_checkpoint),
            },
        )


async def update_run(
    session: AsyncSession,
    actor: User,
    run_id: str,
    *,
    status: RunStatus | None = None,
    compute_backend_id: str | None = None,
    container_id: str | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cost: Decimal | None = None,
    output: dict[str, Any] | None = None,
    exit_code: int | None = None,
    error: str | None = None,
    log_path: str | None = None,
    fields_set: set[str] | None = None,
) -> Run:
    """Update a run's status, container metadata, token usage, cost, or output."""
    run = await get_run(session, actor, run_id)
    seed = await get_seed(session, actor, run.seed_id)
    project = await _require_run_developer(session, actor, seed)
    fields = fields_set or set()

    if status == RunStatus.CANCELLED:
        raise Invalid("Use POST /api/v1/runs/{run_id}/cancel to cancel a run")

    if "compute_backend_id" in fields:
        if compute_backend_id is not None:
            backend = await session.get(Backend, compute_backend_id)
            if backend is None:
                raise NotFound("Compute backend not found")
            if backend.type != BackendType.COMPUTE:
                raise Invalid(
                    "compute_backend_id must reference a backend of type 'compute'"
                )
            if project.backends_restricted:
                allowed_bids = await get_allowed_backend_ids(session, project.id)
                if backend.id not in allowed_bids:
                    raise Forbidden(
                        "This compute backend is not in the project's allowed backends list"
                    )
            if backend.access_restricted:
                grant = await session.get(AgentBackend, (backend.id, run.agent_id))
                if grant is None:
                    raise Forbidden(
                        "Agent is not granted access to this restricted compute backend"
                    )
        run.compute_backend_id = compute_backend_id

    if "container_id" in fields:
        run.container_id = _validate_safe_container_id(run.id, container_id)
    if input_tokens is not None:
        run.input_tokens = input_tokens
    if output_tokens is not None:
        run.output_tokens = output_tokens
    if cost is not None:
        run.cost = cost
    if output is not None:
        protected_keys = (
            "trusted",
            "_broker",
            "_actor_user_id",
            "actor_user_id",
            "auth_mode",
            "cost_enforcement",
            "enforcement",
            "enforcement_detail",
            "models_backend_id",
            "gateway_model",
            "webhook_triggered",
        )
        sanitized_output = {k: v for k, v in output.items() if k not in protected_keys}
        if isinstance(run.output, dict):
            for pk in protected_keys:
                if pk in run.output:
                    sanitized_output[pk] = run.output[pk]
        run.output = sanitized_output
    if "exit_code" in fields:
        run.exit_code = exit_code
    if "error" in fields:
        run.error = error
    if "log_path" in fields:
        if log_path is not None and (".." in log_path or log_path.startswith("/")):
            raise Invalid("log_path must be a relative path without '..'")
        run.log_path = log_path

    now = datetime.now(UTC)
    old_status = run.status

    if status is not None and status != old_status:
        if old_status in _TERMINAL_RUN_STATUSES:
            raise Invalid(f"Cannot transition run from terminal status '{str(old_status)}'")

        pol_for_succeeded: EffectiveStagePolicy | None = None
        if status == RunStatus.SUCCEEDED:
            pol_for_succeeded = await get_effective_stage_policy(
                session, seed.project_id, run.stage
            )
            if actor.org_role != OrgRole.ADMIN:
                actor_role = await get_member_role(session, project.id, actor.id)
                if actor_role != ProjectRole.MAINTAINER and (
                    run.stage == SeedStage.MERGE or pol_for_succeeded.requires_approval
                ):
                    raise Forbidden(
                        "Project maintainer privileges required to manually mark a gated or merge run as succeeded"
                    )

        run.status = status
        if status in (
            RunStatus.RUNNING,
            RunStatus.SUCCEEDED,
            RunStatus.FAILED,
            RunStatus.TIMED_OUT,
        ):
            if run.started_at is None:
                run.started_at = now

        if status in _TERMINAL_RUN_STATUSES:
            run.finished_at = now

        if status == RunStatus.RUNNING:
            seed.status = SeedStatus.RUNNING
            seed.blocked_reason = None
            seed.updated_at = now
            record(
                session,
                type=ActivityType.RUN_STARTED,
                actor_type=ActorType.AGENT,
                actor_id=run.agent_id,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={
                    "run_id": run.id,
                    "stage": str(run.stage),
                    "attempt": run.attempt,
                },
            )
        elif status == RunStatus.SUCCEEDED:
            record(
                session,
                type=ActivityType.RUN_SUCCEEDED,
                actor_type=ActorType.AGENT,
                actor_id=run.agent_id,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={
                    "run_id": run.id,
                    "stage": str(run.stage),
                    "attempt": run.attempt,
                    "cost": str(run.cost),
                    "input_tokens": run.input_tokens,
                    "output_tokens": run.output_tokens,
                },
            )
            assert pol_for_succeeded is not None
            await advance_seed_after_succeeded_run(
                session, seed, run, pol_for_succeeded, now
            )
        elif status == RunStatus.FAILED:
            seed.status = SeedStatus.FAILED
            seed.blocked_reason = None
            seed.updated_at = now
            record(
                session,
                type=ActivityType.RUN_FAILED,
                actor_type=ActorType.AGENT,
                actor_id=run.agent_id,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={
                    "run_id": run.id,
                    "stage": str(run.stage),
                    "attempt": run.attempt,
                    "error": run.error,
                },
            )
        elif status == RunStatus.TIMED_OUT:
            seed.status = SeedStatus.FAILED
            seed.blocked_reason = None
            seed.updated_at = now
            record(
                session,
                type=ActivityType.RUN_TIMED_OUT,
                actor_type=ActorType.SYSTEM,
                actor_id=None,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={
                    "run_id": run.id,
                    "stage": str(run.stage),
                    "attempt": run.attempt,
                },
            )

    run.updated_at = now
    await session.flush()

    if cost is not None:
        await _recompute_seed_spent(session, seed, project)

    await session.commit()
    await session.refresh(run)
    return run


async def cancel_run(
    session: AsyncSession,
    actor: User,
    run_id: str,
) -> Run:
    """Cancel an active (queued, starting, running) run and record run.cancelled."""
    run = await get_run(session, actor, run_id)
    seed = await get_seed(session, actor, run.seed_id)
    project = await _require_run_developer(session, actor, seed)

    if run.status == RunStatus.CANCELLED:
        return run
    if run.status not in RUN_ACTIVE_STATUSES:
        raise Invalid(f"Cannot cancel a run with status '{str(run.status)}'")

    await finalize_run_litellm_key(session, run, seed, project, delete_key=True)

    now = datetime.now(UTC)
    run.status = RunStatus.CANCELLED
    run.finished_at = now
    run.cancelled_by = actor.id
    run.updated_at = now

    if run.compute_backend_id and run.container_id:
        compute_backend = await session.get(Backend, run.compute_backend_id)
        if compute_backend is not None:
            logs = await fetch_docker_container_logs(compute_backend, run.container_id)
            if logs:
                run.output = {**(run.output or {}), "log_tail": logs}
            await stop_and_remove_docker_container(compute_backend, run.container_id)
        run.container_id = None

    if seed.status == SeedStatus.RUNNING:
        seed.status = SeedStatus.QUEUED
        seed.updated_at = now

    record(
        session,
        type=ActivityType.RUN_CANCELLED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=seed.project_id,
        seed_id=seed.id,
        data={
            "run_id": run.id,
            "stage": str(run.stage),
            "attempt": run.attempt,
        },
    )
    await session.commit()
    await session.refresh(run)
    return run

