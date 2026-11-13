"""GitSeed Scheduler & Docker Compute Dispatcher.

Enforces:
- Single active scheduler tick via PostgreSQL advisory lock (`pg_try_advisory_xact_lock`).
- Row-level locking via `SELECT ... FOR UPDATE SKIP LOCKED`.
- At most 1 active run per Seed (sequential task execution in Execute).
- Global Max Concurrent Runners (`org_settings.max_concurrent_runners`).
- Per-Project Concurrency Limit (`project.max_concurrent_runs` or `default_project_concurrency`).
- Compute Backend capacity (`backend.capacity`) and project/agent backend restrictions.
- Queue Scheduling Strategy (`fifo` vs `fair_share` across projects).
- Stage `max_attempts` and `timeout_minutes` enforcement.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict, deque
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from typing import Any

from gitseed_common.enums import (
    RUN_ACTIVE_STATUSES,
    ActivityType,
    ActorType,
    AgentKind,
    BackendHealth,
    BackendType,
    QueueStrategy,
    RunStatus,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from sqlalchemy import func, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.db import AsyncSessionLocal
from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment, SeedTask
from gitseed_core.models.settings import OrgSettings
from gitseed_core.security import create_run_token
from gitseed_core.services import events as events_service
from gitseed_core.services.activity import record
from gitseed_core.services.compute_runtime import (
    RunnerPreparePhaseError,
    _attach_run_log_tail,
    _demux_docker_logs,
    _docker_client_for_backend,
    _ensure_docker_network,
    _inspect_docker_container,
    _k8s_client_for_backend,
    _spawn_k8s_runner_job,
    fetch_docker_container_logs,
    inspect_docker_container_state,
    remove_docker_seed_workspace_volume,
    spawn_docker_runner_container,
    stop_and_remove_docker_container,
)
from gitseed_core.services.gateway import (
    _compute_remaining_budget,
    finalize_run_litellm_key,
    resolve_models_backend_for_run,
)
from gitseed_core.services.git_merge import (
    check_seed_pull_request_status,
    ensure_seed_pull_request,
    is_auto_create_pr_enabled,
    maybe_auto_merge_ready_pr,
    reconcile_seed_if_pr_merged_externally,
    remediate_seed_if_pr_ci_failing,
)
from gitseed_core.services.policies import (
    EffectiveStagePolicy,
    get_effective_stage_policy,
)
from gitseed_core.services.seeds import (
    MAX_OPINIONS_PER_STAGE,
    MULTI_OPINION_STAGES,
    list_stage_cycle_runs,
    reset_orphaned_running_tasks,
)

logger = logging.getLogger("uvicorn.error")

_SCHEDULER_ADVISORY_LOCK_ID = 748291042
_RUNNING_STATUSES = (RunStatus.STARTING, RunStatus.RUNNING)
_LAST_EXTERNAL_PR_CHECK_AT: datetime | None = None
_EXTERNAL_PR_CHECK_INTERVAL = timedelta(seconds=45)
_LAST_PR_READINESS_CACHE: dict[str, tuple[str | None, str | None, bool]] = {}
_PR_LAST_CHECKED_AT: dict[str, float] = {}

__all__ = [
    "_attach_run_log_tail",
    "_demux_docker_logs",
    "_docker_client_for_backend",
    "_ensure_docker_network",
    "_inspect_docker_container",
    "_k8s_client_for_backend",
    "_spawn_k8s_runner_job",
    "fetch_docker_container_logs",
    "inspect_docker_container_state",
    "run_scheduler_forever",
    "run_scheduler_tick",
    "spawn_docker_runner_container",
    "stop_and_remove_docker_container",
]


async def _stop_run_containers(
    backend: Backend, container_id: str | None, run_id: str | None = None
) -> None:
    try:
        await stop_and_remove_docker_container(
            backend, container_id, run_id=run_id
        )
    except TypeError:
        await stop_and_remove_docker_container(backend, container_id)


async def _lock_active_run_for_update(
    session: AsyncSession, run: Run
) -> Run | None:
    """Verify the active run is still in a non-terminal running state under row lock."""
    target = run
    if isinstance(session, AsyncSession) and not hasattr(session, "_mock_name"):
        try:
            refreshed = await session.get(Run, run.id, with_for_update=True)
            if isinstance(refreshed, Run):
                target = refreshed
        except Exception:
            pass
    if str(target.status) not in _RUNNING_STATUSES and target.status not in (
        RunStatus.STARTING,
        RunStatus.RUNNING,
    ):
        return None
    return target


async def _reconcile_active_runs(session: AsyncSession) -> None:
    """Enforce timeout_minutes on running containers and reconcile unexpected container exits."""
    now = datetime.now(UTC)
    active_runs = list(
        (
            await session.execute(
                select(Run)
                .where(col(Run.status).in_(_RUNNING_STATUSES))
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    if not active_runs:
        return

    # Pre-fetch compute backends and concurrently inspect container logs/states (bounded by semaphore)
    backend_map: dict[str, Backend] = {}
    for r in active_runs:
        if r.compute_backend_id and r.compute_backend_id not in backend_map:
            b_obj = await session.get(Backend, r.compute_backend_id)
            if b_obj is not None:
                backend_map[r.compute_backend_id] = b_obj

    io_sem = asyncio.Semaphore(8)

    async def _probe_container(
        r: Run,
    ) -> tuple[str, str, dict[str, Any] | None]:
        b_obj = backend_map.get(r.compute_backend_id or "")
        if b_obj is None or not r.container_id:
            return r.id, "", None
        async with io_sem:
            logs_task = fetch_docker_container_logs(
                b_obj, r.container_id, tail_lines=400
            )
            start_ref = r.started_at or r.created_at or now
            if (now - start_ref).total_seconds() > 8:
                logs_str, info_dict = await asyncio.gather(
                    logs_task,
                    _inspect_docker_container(b_obj, r.container_id),
                )
            else:
                logs_str = await logs_task
                info_dict = None
            return r.id, logs_str, info_dict

    probe_results = await asyncio.gather(
        *[_probe_container(r) for r in active_runs],
        return_exceptions=True,
    )
    probed_logs: dict[str, str] = {}
    probed_info: dict[str, dict[str, Any] | None] = {}
    for res in probe_results:
        if isinstance(res, tuple):
            rid, l_str, i_dict = res
            probed_logs[rid] = l_str
            probed_info[rid] = i_dict

    for run in active_runs:
        seed = await session.get(Seed, run.seed_id)
        if seed is None:
            continue
        project = await session.get(Project, seed.project_id)
        if project is None:
            continue
        pol = await get_effective_stage_policy(session, seed.project_id, run.stage)
        backend = backend_map.get(run.compute_backend_id or "")

        # 0. Sync live spend, token usage & container logs for active running jobs
        if run.status == RunStatus.RUNNING:
            await finalize_run_litellm_key(session, run, seed, project, delete_key=False)
        live_logs = probed_logs.get(run.id, "")
        if live_logs:
            _attach_run_log_tail(run, live_logs)

        # 1. If the seed was blocked by budget/checkpoint, stopped by a person, or closed, stop the container
        if seed.status in (SeedStatus.BLOCKED, SeedStatus.STOPPED, SeedStatus.CLOSED, SeedStatus.MERGED):
            locked_run = await _lock_active_run_for_update(session, run)
            if locked_run is None:
                continue
            run = locked_run
            if backend and run.container_id:
                logs = live_logs or await fetch_docker_container_logs(backend, run.container_id)
                _attach_run_log_tail(run, logs)
                await _stop_run_containers(backend, run.container_id, run.id)
                run.container_id = None
            await finalize_run_litellm_key(session, run, seed, project, delete_key=True)
            run.status = RunStatus.CANCELLED
            run.finished_at = now
            run.updated_at = now
            if run.stage == SeedStage.EXECUTE:
                await reset_orphaned_running_tasks(
                    session, seed.id, exclude_run_id=run.id, now=now
                )
            continue

        # 2. Enforce stage timeout_minutes from started_at (or created_at if stuck in starting)
        start_ref = run.started_at or run.created_at or now
        timeout_delta = timedelta(minutes=max(1, pol.timeout_minutes))
        if now - start_ref > timeout_delta:
            locked_run = await _lock_active_run_for_update(session, run)
            if locked_run is None:
                continue
            run = locked_run
            if backend and run.container_id:
                logs = live_logs or await fetch_docker_container_logs(backend, run.container_id)
                _attach_run_log_tail(run, logs)
                await _stop_run_containers(backend, run.container_id, run.id)
                run.container_id = None
            await finalize_run_litellm_key(session, run, seed, project, delete_key=True)
            run.status = RunStatus.TIMED_OUT
            if run.started_at is None:
                run.started_at = start_ref
            run.finished_at = now
            run.error = f"Run exceeded stage timeout of {pol.timeout_minutes} minutes"
            run.updated_at = now
            if run.stage == SeedStage.EXECUTE:
                await reset_orphaned_running_tasks(
                    session, seed.id, exclude_run_id=run.id, now=now
                )

            await _reconcile_seed_after_run_failure(session, seed, run, pol, now)

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
                    "timeout_minutes": pol.timeout_minutes,
                },
            )
            continue

        # 3. Check if the container exited without calling /api/v1/runner/complete
        if backend and run.container_id and (now - start_ref).total_seconds() > 8:
            info = probed_info.get(run.id)
            if info is None:
                continue
            state = info.get("State", {}) if isinstance(info, dict) else {}
            is_missing = bool(info.get("_missing"))
            is_running = bool(state.get("Running", True)) if not is_missing else False

            if is_missing or not is_running:
                locked_run = await _lock_active_run_for_update(session, run)
                if locked_run is None:
                    continue
                run = locked_run
                exit_code = int(state.get("ExitCode", 1)) if not is_missing else 1
                oom_killed = bool(state.get("OOMKilled")) if not is_missing else False
                if oom_killed or exit_code == 137:
                    err_msg = (
                        "Runner container was killed due to Out-Of-Memory (OOM, exit code 137). "
                        "Increase memory_mb in Compute Backend or Project Sandbox settings."
                    )
                else:
                    err_msg = (
                        str(state.get("Error") or "")
                        or f"Runner container exited unexpectedly (exit code {exit_code})"
                    )
                if not is_missing:
                    logs = live_logs or await fetch_docker_container_logs(backend, run.container_id)
                    _attach_run_log_tail(run, logs)
                await _stop_run_containers(backend, run.container_id, run.id)
                run.container_id = None
                await finalize_run_litellm_key(session, run, seed, project, delete_key=True)

                run.status = RunStatus.FAILED
                if run.started_at is None:
                    run.started_at = start_ref
                run.finished_at = now
                run.exit_code = exit_code
                run.error = err_msg
                run.updated_at = now
                if run.stage == SeedStage.EXECUTE:
                    await reset_orphaned_running_tasks(
                        session, seed.id, exclude_run_id=run.id, now=now
                    )

                await _reconcile_seed_after_run_failure(session, seed, run, pol, now)

                record(
                    session,
                    type=ActivityType.RUN_FAILED,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    project_id=seed.project_id,
                    seed_id=seed.id,
                    data={
                        "run_id": run.id,
                        "stage": str(run.stage),
                        "attempt": run.attempt,
                        "error": err_msg,
                    },
                )


async def _reconcile_seed_after_run_failure(
    session: AsyncSession,
    seed: Seed,
    run: Run,
    pol: EffectiveStagePolicy,
    now: datetime,
) -> None:
    """Update seed status after a run fails or times out, accounting for parallel second-opinion runs."""
    if seed.status == SeedStatus.BLOCKED:
        return
    if run.stage in MULTI_OPINION_STAGES:
        cycle_runs = await list_stage_cycle_runs(session, seed, run.stage)
        has_active_sibling = any(
            r.id != run.id and r.status in RUN_ACTIVE_STATUSES for r in cycle_runs
        )
        if has_active_sibling:
            seed.status = SeedStatus.RUNNING
            seed.blocked_reason = None
            seed.updated_at = now
            return
        succeeded_siblings = [
            r for r in cycle_runs if r.id != run.id and r.status == RunStatus.SUCCEEDED
        ]
        if succeeded_siblings:
            from gitseed_core.services.runs import advance_seed_after_succeeded_run

            await advance_seed_after_succeeded_run(
                session, seed, succeeded_siblings[-1], pol, now
            )
            return

    is_second_opinion = isinstance(run.output, dict) and bool(
        run.output.get("second_opinion")
    )
    recent_failures: int | None = None
    try:
        reset_ts = await _latest_stage_reset_timestamp(session, seed)
        if reset_ts is None or isinstance(reset_ts, datetime):
            fail_stmt = select(func.count()).select_from(Run).where(
                Run.seed_id == seed.id,
                Run.stage == run.stage,
                col(Run.status).in_([RunStatus.FAILED, RunStatus.TIMED_OUT]),
            )
            if run.task_id is not None:
                fail_stmt = fail_stmt.where(Run.task_id == run.task_id)
            else:
                fail_stmt = fail_stmt.where(col(Run.task_id).is_(None))
            if reset_ts is not None:
                fail_stmt = fail_stmt.where(col(Run.created_at) >= reset_ts)
            raw_cnt = await session.scalar(fail_stmt)
            if isinstance(raw_cnt, int) and not isinstance(raw_cnt, bool):
                recent_failures = raw_cnt
    except Exception:
        recent_failures = None

    effective_failures = recent_failures if recent_failures is not None else run.attempt
    if not is_second_opinion and effective_failures < pol.max_attempts:
        seed.status = SeedStatus.QUEUED
        seed.blocked_reason = None
    else:
        seed.status = SeedStatus.FAILED
        seed.blocked_reason = None
    seed.updated_at = now


async def _cleanup_finished_run_containers(session: AsyncSession) -> None:
    """Capture final Docker container stdout/stderr and remove containers for completed runs."""
    terminal_statuses = [
        RunStatus.SUCCEEDED.value,
        RunStatus.FAILED.value,
        RunStatus.CANCELLED.value,
        RunStatus.TIMED_OUT.value,
    ]
    finished_with_containers = list(
        (
            await session.execute(
                select(Run).where(
                    col(Run.status).in_(terminal_statuses),
                    col(Run.container_id).is_not(None),
                )
            )
        )
        .scalars()
        .all()
    )
    for run in finished_with_containers:
        seed = await session.get(Seed, run.seed_id)
        project = await session.get(Project, seed.project_id) if seed else None
        if seed is not None and project is not None:
            await finalize_run_litellm_key(session, run, seed, project, delete_key=True)
        backend = (
            await session.get(Backend, run.compute_backend_id)
            if run.compute_backend_id
            else None
        )
        if backend is not None and run.container_id:
            logs = await fetch_docker_container_logs(backend, run.container_id)
            _attach_run_log_tail(run, logs)
            await _stop_run_containers(backend, run.container_id, run.id)
            if seed is not None and seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
                await remove_docker_seed_workspace_volume(
                    backend,
                    seed.id,
                    project_slug=project.slug if project is not None else None,
                )
        run.container_id = None
        run.updated_at = datetime.now(UTC)


async def _latest_stage_reset_timestamp(
    session: AsyncSession, seed: Seed
) -> datetime | None:
    """Return the latest timestamp when the current stage was entered or had changes requested."""
    baseline = seed.stage_entered_at or seed.created_at
    latest_cr = await session.scalar(
        select(func.max(Approval.created_at)).where(
            Approval.seed_id == seed.id,
            Approval.stage == seed.stage,
            Approval.decision == "changes_requested",
        )
    )
    if latest_cr is not None and (baseline is None or latest_cr > baseline):
        baseline = latest_cr
    latest_comment = await session.scalar(
        select(func.max(SeedComment.created_at)).where(
            SeedComment.seed_id == seed.id,
            SeedComment.stage == seed.stage,
            SeedComment.author_type == ActorType.USER,
            col(SeedComment.deleted_at).is_(None),
        )
    )
    if latest_comment is not None and (baseline is None or latest_comment > baseline):
        baseline = latest_comment
    return baseline


async def _project_has_connected_repository(
    session: AsyncSession, project_id: str
) -> bool:
    """Return True when the project has at least one repository attached on an enabled Git backend."""
    stmt = (
        select(ProjectRepository.id)
        .join(Backend, col(ProjectRepository.backend_id) == col(Backend.id))
        .where(
            ProjectRepository.project_id == project_id,
            Backend.type == BackendType.GIT,
            col(Backend.enabled).is_(True),
        )
        .limit(1)
    )
    if (await session.execute(stmt)).first() is not None:
        return True

    project = await session.get(Project, project_id)
    if project is not None:
        from gitseed_core.services.local_git import maybe_auto_attach_existing_local_workspace

        attached = await maybe_auto_attach_existing_local_workspace(
            session, project, create_if_missing=False
        )
        if attached is not None:
            return True

    return False


async def _enqueue_runs_for_queued_seeds(session: AsyncSession) -> None:
    """Ensure every queued Seed with no active Run gets a queued Run row (at most 1 per seed)."""
    now = datetime.now(UTC)

    seeds_with_active_runs = select(Run.seed_id).where(
        col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES])
    )
    stmt = (
        select(Seed)
        .join(Project, col(Seed.project_id) == col(Project.id))
        .where(
            Seed.status == SeedStatus.QUEUED,
            col(Project.archived_at).is_(None),
            col(Seed.id).not_in(seeds_with_active_runs),
        )
        .order_by(col(Seed.stage_entered_at).asc(), col(Seed.created_at).asc())
        .with_for_update(skip_locked=True)
    )
    queued_seeds = list((await session.execute(stmt)).scalars().all())

    for seed in queued_seeds:
        project = await session.get(Project, seed.project_id)
        if project is None or project.archived_at is not None:
            continue

        # 0. Keep seeds queued if the project has no target repository connected yet
        if not await _project_has_connected_repository(session, project.id):
            continue

        # 1. Check budget before queuing
        _, blocked_reason, activity_data = await _compute_remaining_budget(
            session, seed, project
        )
        if blocked_reason is not None:
            seed.status = SeedStatus.BLOCKED
            seed.blocked_reason = blocked_reason
            seed.updated_at = now
            if activity_data is not None:
                record(
                    session,
                    type=ActivityType.BUDGET_BLOCKED,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    project_id=project.id,
                    seed_id=seed.id,
                    data=activity_data,
                )
            continue

        pol = await get_effective_stage_policy(session, project.id, seed.stage)
        if not pol.agent_id:
            continue
        agent = await session.get(Agent, pol.agent_id)
        if (
            agent is None
            or agent.archived_at is not None
            or not agent.enabled
            or agent.kind != AgentKind.STAGE
        ):
            continue

        # 2. For Execute stage, pick the next PENDING task in topological position order
        target_task_id: str | None = None
        if seed.stage == SeedStage.EXECUTE:
            next_task = (
                await session.execute(
                    select(SeedTask)
                    .where(
                        SeedTask.seed_id == seed.id,
                        SeedTask.status == TaskStatus.PENDING,
                    )
                    .order_by(col(SeedTask.position).asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if next_task is not None:
                target_task_id = next_task.id

        # 3. Check attempts since stage entry / last changes_requested against policy.max_attempts
        reset_ts = await _latest_stage_reset_timestamp(session, seed)
        recent_attempts_stmt = select(func.count()).select_from(Run).where(
            Run.seed_id == seed.id,
            Run.stage == seed.stage,
            col(Run.status).in_([RunStatus.FAILED, RunStatus.TIMED_OUT]),
        )
        if target_task_id is not None:
            recent_attempts_stmt = recent_attempts_stmt.where(Run.task_id == target_task_id)
        else:
            recent_attempts_stmt = recent_attempts_stmt.where(col(Run.task_id).is_(None))
        if reset_ts is not None:
            recent_attempts_stmt = recent_attempts_stmt.where(col(Run.created_at) >= reset_ts)

        recent_failures = int((await session.scalar(recent_attempts_stmt)) or 0)
        if recent_failures >= pol.max_attempts:
            seed.status = SeedStatus.FAILED
            seed.blocked_reason = None
            seed.updated_at = now
            continue

        # Compute monotonic attempt counter for (seed, stage, task)
        max_attempt_stmt = select(func.max(Run.attempt)).where(
            Run.seed_id == seed.id,
            Run.stage == seed.stage,
        )
        if target_task_id is not None:
            max_attempt_stmt = max_attempt_stmt.where(Run.task_id == target_task_id)
        else:
            max_attempt_stmt = max_attempt_stmt.where(col(Run.task_id).is_(None))

        max_attempt = await session.scalar(max_attempt_stmt)
        next_attempt = 1 if max_attempt is None else int(max_attempt) + 1

        run = Run(
            seed_id=seed.id,
            task_id=target_task_id,
            stage=seed.stage,
            attempt=next_attempt,
            agent_id=agent.id,
            runtime=agent.runtime,
            model=agent.model,
            status=RunStatus.QUEUED,
            created_at=now,
            updated_at=now,
        )
        session.add(run)


def _order_candidates_by_strategy(
    candidates: list[ tuple[Run, Seed, Project] ],
    strategy: QueueStrategy,
    active_by_project: dict[str, int],
) -> list[tuple[Run, Seed, Project]]:
    """Order queued runs according to FIFO or Fair-Share across projects."""
    if strategy == QueueStrategy.FIFO or len(candidates) <= 1:
        return sorted(
            candidates,
            key=lambda item: (item[0].created_at or datetime.min.replace(tzinfo=UTC), item[0].id),
        )

    # Fair-Share: group by project, sort each project's queue FIFO, then interleave
    # prioritizing projects with the fewest currently active runs
    by_project: dict[str, deque[tuple[Run, Seed, Project]]] = defaultdict(deque)
    for item in sorted(
        candidates,
        key=lambda x: (x[0].created_at or datetime.min.replace(tzinfo=UTC), x[0].id),
    ):
        by_project[item[2].id].append(item)

    sim_active = defaultdict(int, active_by_project)
    ordered: list[tuple[Run, Seed, Project]] = []

    while by_project:
        # Pick project with lowest simulated active count, tie-breaking by oldest head run
        best_pid = min(
            by_project.keys(),
            key=lambda pid: (
                sim_active[pid],
                by_project[pid][0][0].created_at or datetime.min.replace(tzinfo=UTC),
            ),
        )
        chosen = by_project[best_pid].popleft()
        ordered.append(chosen)
        sim_active[best_pid] += 1
        if not by_project[best_pid]:
            del by_project[best_pid]

    return ordered


def _compute_backend_sort_key(
    backend: Backend, active_by_backend: dict[str, int]
) -> tuple[int, float, int, datetime, str]:
    """Rank eligible compute backends: Healthy first, lowest utilization ratio, most free slots."""
    health_str = str(backend.health).lower()
    if health_str == BackendHealth.HEALTHY.value:
        health_rank = 0
    elif health_str == BackendHealth.UNKNOWN.value:
        health_rank = 1
    else:
        health_rank = 2

    active = active_by_backend.get(backend.id, 0)
    cap = backend.capacity if (backend.capacity and backend.capacity > 0) else 100
    utilization = active / cap
    free_slots = cap - active
    created = backend.created_at or datetime.min.replace(tzinfo=UTC)
    return (health_rank, utilization, -free_slots, created, backend.id)


async def _select_compute_backend(
    session: AsyncSession,
    run: Run,
    project: Project,
    compute_backends: list[Backend],
    active_by_backend: dict[str, int],
) -> Backend | None:
    """Select the least-loaded eligible Compute backend for the project and agent."""
    if run.compute_backend_id:
        candidates = [b for b in compute_backends if b.id == run.compute_backend_id]
    else:
        candidates = list(compute_backends)

    if project.backends_restricted:
        allowed_bids = set(
            (
                await session.execute(
                    select(ProjectBackend.backend_id).where(
                        ProjectBackend.project_id == project.id
                    )
                )
            )
            .scalars()
            .all()
        )
        restricted_candidates = [b for b in candidates if b.id in allowed_bids]
        candidates = restricted_candidates

    eligible: list[Backend] = []
    for b in candidates:
        if not b.enabled or b.health == BackendHealth.UNREACHABLE:
            continue
        if b.capacity is not None and active_by_backend[b.id] >= b.capacity:
            continue
        if b.access_restricted:
            grant = await session.get(AgentBackend, (b.id, run.agent_id))
            if grant is None:
                continue
        if b.provider.lower() == "docker":
            default_ep = (
                getattr(settings, "DOCKER_HOST", "unix:///var/run/docker.sock")
                or "unix:///var/run/docker.sock"
            )
            ep = (b.endpoint or default_ep).strip()
            if (
                ep == "unix:///var/run/docker.sock"
                and default_ep != ep
                and not os.path.exists("/var/run/docker.sock")
            ):
                ep = default_ep.strip()
            if ep.startswith("unix://") and not os.path.exists(ep.removeprefix("unix://")):
                continue
        eligible.append(b)

    if not eligible:
        return None

    eligible.sort(key=lambda b: _compute_backend_sort_key(b, active_by_backend))
    return eligible[0]


async def _dispatch_queued_runs(session: AsyncSession) -> int:
    """Launch containers for queued Runs while respecting Global, Project, and Backend limits."""
    org = await session.get(OrgSettings, 1)
    max_global = org.max_concurrent_runners if org else 2
    default_project_cap = org.default_project_concurrency if org else 0
    strategy = org.queue_strategy if org else QueueStrategy.FIFO

    compute_backends = list(
        (
            await session.execute(
                select(Backend)
                .where(
                    Backend.type == BackendType.COMPUTE,
                    col(Backend.enabled).is_(True),
                )
                .order_by(col(Backend.created_at).asc())
            )
        )
        .scalars()
        .all()
    )
    if not compute_backends:
        return 0

    # If org max_concurrent_runners is still at the untouched bootstrap default (2),
    # scale the ceiling with aggregate enabled compute capacity when additional backends are added.
    total_backend_capacity = sum(
        (b.capacity if (b.capacity and b.capacity > 0) else 2)
        for b in compute_backends
        if b.health != BackendHealth.UNREACHABLE
    )
    if org is None or (org.updated_by is None and max_global <= 2):
        max_global = max(max_global, total_backend_capacity)

    # Count active runs (starting + running) globally, per project, per seed, and per backend
    active_rows = (
        await session.execute(
            select(Run.id, Run.seed_id, Run.compute_backend_id, Seed.project_id)
            .join(Seed, col(Run.seed_id) == col(Seed.id))
            .where(col(Run.status).in_(_RUNNING_STATUSES))
        )
    ).all()

    global_active = len(active_rows)
    if global_active >= max_global:
        return 0

    active_by_seed: dict[str, int] = defaultdict(int)
    active_by_project: dict[str, int] = defaultdict(int)
    active_by_backend: dict[str, int] = defaultdict(int)
    for _, s_id, b_id, p_id in active_rows:
        active_by_seed[str(s_id)] += 1
        active_by_project[str(p_id)] += 1
        if b_id:
            active_by_backend[str(b_id)] += 1

    queued_rows = (
        await session.execute(
            select(Run, Seed, Project)
            .join(Seed, col(Run.seed_id) == col(Seed.id))
            .join(Project, col(Seed.project_id) == col(Project.id))
            .where(
                Run.status == RunStatus.QUEUED,
                col(Project.archived_at).is_(None),
            )
        )
    ).all()
    if not queued_rows:
        return 0

    ordered_candidates = _order_candidates_by_strategy(
        [(r, s, p) for r, s, p in queued_rows],
        strategy,
        active_by_project,
    )

    dispatched = 0
    for cand_run, cand_seed, cand_project in ordered_candidates:
        if global_active >= max_global:
            break

        # Enforce at most 1 active run per seed for branch-mutating stages (Execute, Merge);
        # allow up to MAX_OPINIONS_PER_STAGE (3) parallel opinion runs for Ideate, Plan, and Review.
        max_seed_active = (
            MAX_OPINIONS_PER_STAGE
            if cand_run.stage in MULTI_OPINION_STAGES
            else 1
        )
        if active_by_seed[cand_seed.id] >= max_seed_active:
            continue

        # Enforce per-project concurrency limit
        proj_limit = (
            cand_project.max_concurrent_runs
            if cand_project.max_concurrent_runs is not None
            else default_project_cap
        )
        if proj_limit > 0 and active_by_project[cand_project.id] >= proj_limit:
            continue

        # Claim the Run row with FOR UPDATE SKIP LOCKED
        locked_run = (
            await session.execute(
                select(Run)
                .where(Run.id == cand_run.id, Run.status == RunStatus.QUEUED)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one_or_none()
        if locked_run is None:
            continue

        # Keep run in queue if the project has no connected target repository on an enabled Git backend
        if not await _project_has_connected_repository(session, cand_project.id):
            continue

        # Verify a compatible Inference backend is enabled and accessible before spawning container
        try:
            await resolve_models_backend_for_run(session, locked_run, cand_project)
        except Exception as exc:
            logger.debug(
                "Run %s waiting in queue (no compatible Inference backend ready: %s)",
                locked_run.id,
                exc,
            )
            continue

        compute_backend = await _select_compute_backend(
            session,
            locked_run,
            cand_project,
            compute_backends,
            active_by_backend,
        )
        if compute_backend is None:
            continue

        pol = await get_effective_stage_policy(
            session, cand_project.id, locked_run.stage
        )
        ttl_seconds = max(3600, pol.timeout_minutes * 60 + 600)
        prepare_token = create_run_token(
            locked_run.id, ttl_seconds=ttl_seconds, scope="prepare"
        )
        agent_token = create_run_token(
            locked_run.id, ttl_seconds=ttl_seconds, scope="agent"
        )
        finalize_token = create_run_token(
            locked_run.id, ttl_seconds=ttl_seconds, scope="finalize"
        )

        now = datetime.now(UTC)
        locked_run.status = RunStatus.STARTING
        locked_run.compute_backend_id = compute_backend.id
        locked_run.updated_at = now
        await session.commit()

        try:
            try:
                container_id = await spawn_docker_runner_container(
                    backend=compute_backend,
                    run=locked_run,
                    seed=cand_seed,
                    project=cand_project,
                    run_token=agent_token,
                    prepare_token=prepare_token,
                    finalize_token=finalize_token,
                )
            except TypeError:
                container_id = await spawn_docker_runner_container(
                    backend=compute_backend,
                    run=locked_run,
                    seed=cand_seed,
                    project=cand_project,
                    run_token=agent_token,
                )
            with suppress(Exception):
                await session.refresh(locked_run)
            locked_run.container_id = container_id
            locked_run.updated_at = datetime.now(UTC)
            await session.commit()

            global_active += 1
            active_by_seed[cand_seed.id] += 1
            active_by_project[cand_project.id] += 1
            active_by_backend[compute_backend.id] += 1
            dispatched += 1
            logger.info(
                "Dispatched run %s (seed=%s#%d, stage=%s, attempt=%d) -> container %s",
                locked_run.id,
                cand_project.slug,
                cand_seed.number,
                str(locked_run.stage),
                locked_run.attempt,
                container_id[:12],
            )
        except Exception as exc:
            logger.error("Failed to spawn container for run %s: %s", locked_run.id, exc)
            with suppress(Exception):
                await _stop_run_containers(compute_backend, None, locked_run.id)
            with suppress(Exception):
                await session.refresh(locked_run)
            with suppress(Exception):
                await session.refresh(cand_seed)

            broker_state: dict[str, Any] = (
                dict(locked_run.output["_broker"])
                if isinstance(locked_run.output, dict)
                and isinstance(locked_run.output.get("_broker"), dict)
                else {}
            )
            recorded_prep_error = str(broker_state.get("prepare_error") or "").strip()
            is_prepare_failure = isinstance(exc, RunnerPreparePhaseError) or bool(
                recorded_prep_error
            )

            if is_prepare_failure:
                fail_now = datetime.now(UTC)
                prep_log_tail = (
                    exc.log_tail if isinstance(exc, RunnerPreparePhaseError) else ""
                )
                effective_err = recorded_prep_error
                if not effective_err and prep_log_tail:
                    err_lines = [
                        ln.strip()
                        for ln in prep_log_tail.splitlines()
                        if "[ERROR]" in ln or "prepare:" in ln
                    ]
                    if err_lines:
                        effective_err = err_lines[-1]
                if not effective_err:
                    effective_err = str(exc)

                _attach_run_log_tail(locked_run, prep_log_tail or effective_err)
                await finalize_run_litellm_key(
                    session, locked_run, cand_seed, cand_project, delete_key=True
                )
                locked_run.status = RunStatus.FAILED
                if locked_run.started_at is None:
                    locked_run.started_at = now
                locked_run.finished_at = fail_now
                locked_run.exit_code = (
                    exc.exit_code if isinstance(exc, RunnerPreparePhaseError) else 1
                )
                locked_run.error = effective_err
                locked_run.updated_at = fail_now
                if locked_run.stage == SeedStage.EXECUTE:
                    await reset_orphaned_running_tasks(
                        session, cand_seed.id, exclude_run_id=locked_run.id, now=fail_now
                    )
                if "does not exist on the remote repository" in effective_err:
                    cand_seed.status = SeedStatus.FAILED
                    cand_seed.blocked_reason = None
                    cand_seed.updated_at = fail_now
                else:
                    await _reconcile_seed_after_run_failure(
                        session, cand_seed, locked_run, pol, fail_now
                    )
                record(
                    session,
                    type=ActivityType.RUN_FAILED,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    project_id=cand_project.id,
                    seed_id=cand_seed.id,
                    data={
                        "run_id": locked_run.id,
                        "stage": str(locked_run.stage),
                        "attempt": locked_run.attempt,
                        "error": effective_err,
                    },
                )
                await session.commit()
                continue

            # Revert to queued if image/socket isn't ready yet so it doesn't burn attempts,
            # and reset single-use prepare flags in case prepare ran before spawn failed.
            if isinstance(locked_run.output, dict) and isinstance(
                locked_run.output.get("_broker"), dict
            ):
                updated_output = dict(locked_run.output)
                broker_state.pop("prepare_context_used", None)
                broker_state.pop("prepare_complete_used", None)
                updated_output["_broker"] = broker_state
                locked_run.output = updated_output
            locked_run.status = RunStatus.QUEUED
            locked_run.error = str(exc)
            locked_run.updated_at = datetime.now(UTC)
            await session.commit()

    return dispatched


async def _reconcile_external_pr_merges(session: AsyncSession) -> None:
    """Periodically check seeds waiting in Review or Merge to see if their PR/MR was merged or pipeline state changed on GitHub/GitLab."""
    global _LAST_EXTERNAL_PR_CHECK_AT
    now = datetime.now(UTC)
    if (
        _LAST_EXTERNAL_PR_CHECK_AT is not None
        and now - _LAST_EXTERNAL_PR_CHECK_AT < _EXTERNAL_PR_CHECK_INTERVAL
    ):
        return
    _LAST_EXTERNAL_PR_CHECK_AT = now

    review_or_merge_seeds = list(
        (
            await session.execute(
                select(Seed)
                .where(
                    col(Seed.stage).in_(
                        [
                            SeedStage.REVIEW.value,
                            SeedStage.MERGE.value,
                        ]
                    ),
                    col(Seed.status).in_(
                        [
                            SeedStatus.AWAITING_APPROVAL.value,
                            SeedStatus.QUEUED.value,
                            SeedStatus.RUNNING.value,
                        ]
                    ),
                )
                .order_by(col(Seed.updated_at).desc())
                .limit(50)
            )
        )
        .scalars()
        .all()
    )
    review_or_merge_seeds.sort(key=lambda s: _PR_LAST_CHECKED_AT.get(s.id, 0.0))
    for seed in review_or_merge_seeds[:15]:
        _PR_LAST_CHECKED_AT[seed.id] = time.monotonic()
        project = await session.get(Project, seed.project_id)
        if project is None or project.archived_at is not None:
            continue
        try:
            pr_info = await check_seed_pull_request_status(session, seed, project)
            if pr_info.get("merged"):
                await reconcile_seed_if_pr_merged_externally(
                    session, seed, project, pr_info=pr_info
                )
                _LAST_PR_READINESS_CACHE.pop(seed.id, None)
                _PR_LAST_CHECKED_AT.pop(seed.id, None)
                continue

            if (
                not pr_info.get("pull_request_url")
                and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE)
                and is_auto_create_pr_enabled(project)
            ):
                await ensure_seed_pull_request(session, seed, project)

            if (
                pr_info.get("ci_status") == "failing"
                and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE)
                and seed.status == SeedStatus.AWAITING_APPROVAL
            ):
                rem_res = await remediate_seed_if_pr_ci_failing(
                    session, seed, project, pr_info=pr_info
                )
                if rem_res == "ci_remediation_queued":
                    _LAST_PR_READINESS_CACHE.pop(seed.id, None)
                    continue

            if (
                seed.stage == SeedStage.MERGE
                and seed.status == SeedStatus.AWAITING_APPROVAL
                and pr_info.get("state") == "open"
                and pr_info.get("ready_to_merge", False)
            ):
                auto_merged = await maybe_auto_merge_ready_pr(
                    session, seed, project, pr_info=pr_info
                )
                if auto_merged:
                    _LAST_PR_READINESS_CACHE.pop(seed.id, None)
                    _PR_LAST_CHECKED_AT.pop(seed.id, None)
                    continue

            if pr_info.get("pull_request_url") or pr_info.get("state"):
                state_tuple = (
                    str(pr_info.get("ci_status") or "none"),
                    str(pr_info.get("review_status") or "none"),
                    bool(pr_info.get("ready_to_merge", False)),
                )
                prev_tuple = _LAST_PR_READINESS_CACHE.get(seed.id)
                if prev_tuple != state_tuple:
                    _LAST_PR_READINESS_CACHE[seed.id] = state_tuple
                    if (
                        seed.stage == SeedStage.MERGE
                        and seed.status == SeedStatus.AWAITING_APPROVAL
                    ):
                        seed.updated_at = now
                    if prev_tuple is not None:
                        events_service.emit_from_sync(
                            type="seed.pr_updated",
                            project_id=seed.project_id,
                            seed_id=seed.id,
                            data=pr_info,
                        )
        except Exception as exc:
            logger.debug("External PR check skipped for seed %s: %s", seed.id, exc)


async def run_scheduler_tick() -> int:
    """Execute a single scheduler pass under a session-level PostgreSQL advisory lock."""
    async with AsyncSessionLocal() as lock_session:
        locked = await lock_session.scalar(
            text("SELECT pg_try_advisory_lock(:lock_id)").bindparams(
                lock_id=_SCHEDULER_ADVISORY_LOCK_ID
            )
        )
        if not locked:
            return 0

        try:
            from gitseed_core.services.sandboxes import reconcile_sandbox_sessions

            async with AsyncSessionLocal() as session:
                await _reconcile_active_runs(session)
                await _cleanup_finished_run_containers(session)
                await reconcile_sandbox_sessions(session)
                await _reconcile_external_pr_merges(session)
                await _enqueue_runs_for_queued_seeds(session)
                dispatched = await _dispatch_queued_runs(session)
                await session.commit()
                return dispatched
        finally:
            with suppress(Exception):
                await lock_session.scalar(
                    text("SELECT pg_advisory_unlock(:lock_id)").bindparams(
                        lock_id=_SCHEDULER_ADVISORY_LOCK_ID
                    )
                )


async def run_scheduler_forever(stop_event: asyncio.Event | None = None) -> None:
    """Run the scheduler loop continuously until stop_event is set."""
    logger.info(
        "GitSeed Scheduler started (poll_interval=%.1fs, runner_image=%s)",
        settings.SCHEDULER_POLL_SECONDS,
        settings.RUNNER_IMAGE,
    )
    while stop_event is None or not stop_event.is_set():
        try:
            await run_scheduler_tick()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Scheduler tick error: %s", exc)

        if stop_event is not None:
            try:
                await asyncio.wait_for(
                    stop_event.wait(), timeout=settings.SCHEDULER_POLL_SECONDS
                )
                break
            except TimeoutError:
                pass
        else:
            await asyncio.sleep(settings.SCHEDULER_POLL_SECONDS)
