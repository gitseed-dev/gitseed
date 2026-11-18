import re
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    ApprovalDecision,
    BackendType,
    OrgRole,
    ProjectVisibility,
    RunStatus,
    SeedStage,
)
from gitseed_common.schemas.agent import (
    AgentBenchmarkEntry,
    AgentBenchmarksResponse,
)
from sqlalchemy import case, func, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.policy import Policy
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.models.user import User
from gitseed_core.services.errors import Conflict, Invalid, NotFound
from gitseed_core.services.gateway import extract_enabled_model_ids
from gitseed_core.services.projects import get_project

_HANDLE_RE = re.compile(r"^[a-z][a-z0-9-]{1,31}$")


@dataclass(frozen=True)
class AgentMetrics:
    run_count: int = 0
    succeeded_runs: int = 0
    total_cost: Decimal = Decimal("0")
    first_pass_rate: float | None = None
    avg_cost_per_seed: Decimal = Decimal("0")
    avg_attempts: float = 1.0


def _slugify_handle(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not raw or not raw[0].isalpha():
        raw = f"agent-{raw}".strip("-")
    if len(raw) == 1:
        raw = f"{raw}-agent"
    return raw[:32].rstrip("-")


def _validate_handle(handle: str) -> str:
    cleaned = handle.strip().lstrip("@").lower()
    if not _HANDLE_RE.match(cleaned):
        raise Invalid(
            "Agent handle must be 2-32 characters, start with a lowercase letter, "
            "and contain only lowercase letters, digits, and hyphens"
        )
    return cleaned


async def _ensure_active_handle_available(
    session: AsyncSession, handle: str, *, exclude_agent_id: str | None = None
) -> None:
    stmt = select(Agent).where(
        Agent.handle == handle,
        col(Agent.archived_at).is_(None),
    )
    if exclude_agent_id is not None:
        stmt = stmt.where(Agent.id != exclude_agent_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"An active agent with handle '@{handle}' already exists")


def get_runtime_model_compatibility_warning(
    runtime: AgentRuntime,
    provider: str | None,
    model: str,
) -> str | None:
    """Return a soft warning for runtime/provider/model combinations that may have limitations."""
    norm_provider = (provider or "").strip().lower()
    norm_model = (model or "default").strip().lower()

    if "embed" in norm_model:
        return "Embedding models do not support chat or tool calling."

    if norm_provider in ("ollama", "lmstudio", "vllm", "custom"):
        if runtime in (
            AgentRuntime.CLAUDE_CODE,
            AgentRuntime.CODEX,
            AgentRuntime.ANTIGRAVITY,
            AgentRuntime.GEMINI,
            AgentRuntime.GROK_BUILD,
        ):
            return (
                "This local model is translated via LiteLLM and may not support tool calling "
                "or reasoning expected by this CLI runtime."
            )
        return "This local model may not support tool calling."

    return None


async def compute_agent_compatibility_warning(
    session: AsyncSession,
    agent: Agent,
) -> str | None:
    """Evaluate whether an agent's runtime and configured model warrant a soft UI warning."""
    cleaned_model = (agent.model or "default").strip()
    model_backends = list(
        (
            await session.execute(
                select(Backend).where(
                    Backend.type == BackendType.INFERENCE,
                    col(Backend.enabled).is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    if not model_backends:
        return get_runtime_model_compatibility_warning(agent.runtime, None, cleaned_model)

    preferred_provider_by_runtime = {
        AgentRuntime.CLAUDE_CODE: "anthropic",
        AgentRuntime.CODEX: "openai",
        AgentRuntime.ANTIGRAVITY: "gemini",
        AgentRuntime.GEMINI: "gemini",
        AgentRuntime.GROK_BUILD: "xai",
    }
    preferred_provider = preferred_provider_by_runtime.get(agent.runtime)
    if preferred_provider:
        model_backends.sort(
            key=lambda b: (
                0
                if b.provider.lower() == preferred_provider
                else (1 if b.provider.lower() == "litellm" else 2)
            )
        )

    if cleaned_model in ("default", "pro", "flash", "flash_lite", "inherit"):
        if agent.runtime != AgentRuntime.OPENCODE:
            return None
        return get_runtime_model_compatibility_warning(
            agent.runtime, model_backends[0].provider, cleaned_model
        )

    for b in model_backends:
        if cleaned_model in extract_enabled_model_ids(b):
            return get_runtime_model_compatibility_warning(
                agent.runtime, b.provider, cleaned_model
            )

    return get_runtime_model_compatibility_warning(agent.runtime, None, cleaned_model)


async def get_agent_allowed_backend_ids(
    session: AsyncSession, agent_id: str
) -> list[str]:
    stmt = (
        select(AgentBackend.backend_id)
        .where(AgentBackend.agent_id == agent_id)
        .order_by(col(AgentBackend.backend_id).asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_agent_allowed_backend_ids_map(
    session: AsyncSession, agent_ids: list[str]
) -> dict[str, list[str]]:
    """Batch-fetch allowed backend IDs for multiple agents in a single query."""
    if not agent_ids:
        return {}
    stmt = (
        select(AgentBackend.agent_id, AgentBackend.backend_id)
        .where(col(AgentBackend.agent_id).in_(agent_ids))
        .order_by(col(AgentBackend.backend_id).asc())
    )
    result: dict[str, list[str]] = {aid: [] for aid in agent_ids}
    for agent_id, backend_id in (await session.execute(stmt)).all():
        result.setdefault(str(agent_id), []).append(str(backend_id))
    return result


async def get_agent_metrics(
    session: AsyncSession, agent_ids: list[str]
) -> dict[str, AgentMetrics]:
    """Compute run count, succeeded run count, first-pass rate, and cost per agent from Run rows."""
    if not agent_ids:
        return {}

    stmt = (
        select(
            Run.agent_id,
            func.count(col(Run.id)),
            func.sum(case((col(Run.status) == RunStatus.SUCCEEDED, 1), else_=0)),
            func.coalesce(func.sum(col(Run.cost)), Decimal("0")),
            func.sum(
                case(
                    (
                        (col(Run.status) == RunStatus.SUCCEEDED) & (col(Run.attempt) == 1),
                        1,
                    ),
                    else_=0,
                )
            ),
            func.count(func.distinct(col(Run.seed_id))),
        )
        .where(col(Run.agent_id).in_(agent_ids))
        .group_by(col(Run.agent_id))
    )
    rows = (await session.execute(stmt)).all()
    metrics_by_id: dict[str, AgentMetrics] = {}
    for row in rows:
        agent_id = str(row[0])
        run_count = int(row[1] or 0)
        succeeded = int(row[2] or 0)
        total_cost = Decimal(str(row[3] or 0))
        first_pass_succeeded = int(row[4] or 0) if len(row) > 4 else succeeded
        seed_count = int(row[5] or 0) if len(row) > 5 else max(1, succeeded)

        first_pass_rate = (
            round((first_pass_succeeded / run_count) * 100.0, 1)
            if run_count > 0
            else None
        )
        avg_cost_per_seed = (
            (total_cost / Decimal(seed_count)).quantize(Decimal("0.01"))
            if seed_count > 0
            else Decimal("0")
        )
        avg_attempts = (
            round(run_count / max(1, first_pass_succeeded), 2)
            if first_pass_succeeded > 0
            else 1.0
        )
        metrics_by_id[agent_id] = AgentMetrics(
            run_count=run_count,
            succeeded_runs=succeeded,
            total_cost=total_cost,
            first_pass_rate=first_pass_rate,
            avg_cost_per_seed=avg_cost_per_seed,
            avg_attempts=avg_attempts,
        )
    return metrics_by_id


async def list_agents(
    session: AsyncSession,
    *,
    kind: AgentKind | None = None,
    enabled: bool | None = None,
    include_archived: bool = False,
) -> list[Agent]:
    """List agents ordered by handle, reconciling any missing stage agents for enabled inference backends."""
    stmt = select(Agent)
    if not include_archived:
        stmt = stmt.where(col(Agent.archived_at).is_(None))
    if kind is not None:
        stmt = stmt.where(Agent.kind == kind)
    if enabled is not None:
        stmt = stmt.where(Agent.enabled == enabled)

    stmt = stmt.order_by(col(Agent.handle).asc())
    agents = list((await session.execute(stmt)).scalars().all())
    if kind is None and enabled is None and isinstance(session, AsyncSession):
        from gitseed_core.services.backends import ensure_missing_backend_stage_agents

        if await ensure_missing_backend_stage_agents(session, existing_agents=agents):
            agents = list((await session.execute(stmt)).scalars().all())
    return agents


async def get_agent(
    session: AsyncSession,
    agent_id_or_handle: str,
) -> Agent:
    """Resolve an agent by ID ('agt_...') or handle ('@coder' / 'coder')."""
    cleaned = agent_id_or_handle.strip()
    if cleaned.startswith("agt_"):
        agent = await session.get(Agent, cleaned)
    else:
        handle = cleaned.lstrip("@").lower()
        stmt = (
            select(Agent)
            .where(Agent.handle == handle)
            .order_by(col(Agent.archived_at).asc().nulls_first())
        )
        agent = (await session.execute(stmt)).scalars().first()

    if agent is None:
        raise NotFound("Agent not found")
    return agent


async def create_agent(
    session: AsyncSession,
    admin: User,
    *,
    name: str,
    handle: str | None = None,
    kind: AgentKind = AgentKind.STAGE,
    template: str | None = None,
    instructions: str = "",
    runtime: AgentRuntime = AgentRuntime.OPENCODE,
    model: str = "default",
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    enabled: bool = True,
) -> Agent:
    """Create a new reusable agent definition."""
    resolved_handle = _validate_handle(handle if handle else _slugify_handle(name))
    await _ensure_active_handle_available(session, resolved_handle)

    now = datetime.now(UTC)
    agent = Agent(
        name=name.strip(),
        handle=resolved_handle,
        kind=kind,
        template=template.strip() if template else None,
        instructions=instructions.strip(),
        runtime=runtime,
        model=model.strip() or "default",
        skills=[s.strip() for s in (skills or []) if s and s.strip()],
        tools=[t.strip() for t in (tools or []) if t and t.strip()],
        enabled=enabled,
        created_by=admin.id,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


async def update_agent(
    session: AsyncSession,
    agent_id_or_handle: str,
    *,
    name: str | None = None,
    handle: str | None = None,
    kind: AgentKind | None = None,
    template: str | None = None,
    instructions: str | None = None,
    runtime: AgentRuntime | None = None,
    model: str | None = None,
    skills: list[str] | None = None,
    tools: list[str] | None = None,
    enabled: bool | None = None,
    fields_set: set[str] | None = None,
) -> Agent:
    """Update an agent's configuration."""
    agent = await get_agent(session, agent_id_or_handle)
    fields = fields_set or set()

    if name is not None:
        agent.name = name.strip()

    if handle is not None:
        new_handle = _validate_handle(handle)
        if new_handle != agent.handle:
            if agent.archived_at is None:
                await _ensure_active_handle_available(
                    session, new_handle, exclude_agent_id=agent.id
                )
            agent.handle = new_handle

    if kind is not None and kind != agent.kind:
        if kind == AgentKind.ASSISTANT:
            in_policy = await session.scalar(
                select(func.count())
                .select_from(Policy)
                .where(Policy.agent_id == agent.id)
            )
            if in_policy:
                raise Conflict(
                    "Cannot change agent kind to 'assistant' while it is assigned to a stage policy"
                )
        agent.kind = kind

    if "template" in fields:
        agent.template = template.strip() if template else None
    if instructions is not None:
        agent.instructions = instructions.strip()
    if runtime is not None:
        agent.runtime = runtime
    if model is not None:
        agent.model = model.strip() or "default"
    if skills is not None:
        agent.skills = [s.strip() for s in skills if s and s.strip()]
    if tools is not None:
        agent.tools = [t.strip() for t in tools if t and t.strip()]
    if enabled is not None:
        agent.enabled = enabled

    agent.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(agent)
    return agent


async def archive_agent(
    session: AsyncSession,
    admin: User,
    agent_id_or_handle: str,
) -> Agent:
    """Archive an agent if no stage policy currently points to it."""
    agent = await get_agent(session, agent_id_or_handle)
    if agent.archived_at is not None:
        return agent

    in_policy = await session.scalar(
        select(func.count()).select_from(Policy).where(Policy.agent_id == agent.id)
    )
    if in_policy:
        raise Conflict(
            "Cannot archive an agent while it is assigned to a stage policy"
        )

    now = datetime.now(UTC)
    agent.archived_at = now
    agent.archived_by = admin.id
    agent.enabled = False
    agent.updated_at = now
    await session.commit()
    await session.refresh(agent)
    return agent


async def restore_agent(
    session: AsyncSession,
    agent_id_or_handle: str,
) -> Agent:
    """Restore an archived agent if its handle is still available."""
    agent = await get_agent(session, agent_id_or_handle)
    if agent.archived_at is None:
        return agent

    await _ensure_active_handle_available(
        session, agent.handle, exclude_agent_id=agent.id
    )

    now = datetime.now(UTC)
    agent.archived_at = None
    agent.archived_by = None
    agent.enabled = True
    agent.updated_at = now
    await session.commit()
    await session.refresh(agent)
    return agent


async def compute_agent_benchmarks(
    session: AsyncSession,
    *,
    actor: User | None = None,
    project_id: str | None = None,
    stage: SeedStage | None = None,
) -> AgentBenchmarksResponse:
    """Compute empirical agent/model benchmark scorecards from historical Runs and Approvals."""
    stmt = (
        select(Run, Agent, Seed.project_id)
        .join(Agent, col(Agent.id) == col(Run.agent_id))
        .join(Seed, col(Seed.id) == col(Run.seed_id))
    )
    resolved_project_id = project_id
    if project_id:
        if actor is not None:
            proj = await get_project(session, actor, project_id)
            resolved_project_id = proj.id
        stmt = stmt.where(col(Seed.project_id) == resolved_project_id)
    elif actor is not None and actor.org_role != OrgRole.ADMIN:
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
        stmt = stmt.where(col(Run.stage) == stage)

    stmt = stmt.order_by(col(Run.created_at).asc())
    run_rows = (await session.execute(stmt)).all()
    if not run_rows:
        return AgentBenchmarksResponse(
            project_id=resolved_project_id,
            stage=stage,
            entries=[],
            insights=[],
        )

    seed_ids = list({str(r[0].seed_id) for r in run_rows})
    approvals_stmt = (
        select(Approval)
        .where(col(Approval.seed_id).in_(seed_ids))
        .order_by(col(Approval.created_at).asc())
    )
    approval_rows = list((await session.execute(approvals_stmt)).scalars().all())

    # Record earliest approval decision per (seed_id, stage)
    first_approval_decision: dict[tuple[str, str], str] = {}
    for apr in approval_rows:
        key = (str(apr.seed_id), str(apr.stage))
        if key not in first_approval_decision:
            first_approval_decision[key] = str(apr.decision)

    # Group runs by (agent_id, runtime, model, stage)
    cohorts: dict[tuple[str, str, str, str], dict[str, object]] = {}
    for run, agent, _proj_id in run_rows:
        c_key = (
            str(run.agent_id),
            str(run.runtime),
            str(run.model),
            str(run.stage),
        )
        bucket = cohorts.get(c_key)
        if bucket is None:
            bucket = {
                "agent": agent,
                "runs": [],
            }
            cohorts[c_key] = bucket
        runs_list = bucket["runs"]
        assert isinstance(runs_list, list)
        runs_list.append(run)

    entries: list[AgentBenchmarkEntry] = []
    for (agent_id, runtime_str, model_str, stage_str), bucket in cohorts.items():
        agent = bucket["agent"]
        assert isinstance(agent, Agent)
        runs_list = bucket["runs"]
        assert isinstance(runs_list, list)

        run_count = len(runs_list)
        succeeded_runs = sum(1 for r in runs_list if r.status == RunStatus.SUCCEEDED)
        tech_rate = round((succeeded_runs / run_count) * 100.0, 1) if run_count > 0 else 0.0

        distinct_seeds = {str(r.seed_id) for r in runs_list}
        seed_count = len(distinct_seeds)

        # Group runs within this cohort by (seed_id, stage)
        by_seed_stage: dict[tuple[str, str], list[Run]] = {}
        for r in runs_list:
            by_seed_stage.setdefault((str(r.seed_id), str(r.stage)), []).append(r)

        stage_instances = len(by_seed_stage)
        avg_attempts = (
            round(run_count / stage_instances, 2) if stage_instances > 0 else 1.0
        )

        evaluated_gate_count = 0
        first_pass_approved_count = 0
        for ss_key, ss_runs in by_seed_stage.items():
            apr_dec = first_approval_decision.get(ss_key)
            has_terminal_run = any(
                r.status in (RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.TIMED_OUT)
                for r in ss_runs
            )
            if apr_dec is not None or has_terminal_run:
                evaluated_gate_count += 1
                attempt_1_succeeded = any(
                    r.attempt == 1 and r.status == RunStatus.SUCCEEDED for r in ss_runs
                )
                no_retries = len(ss_runs) == 1 and all(r.attempt == 1 for r in ss_runs)
                gate_ok = apr_dec in (None, str(ApprovalDecision.APPROVED))
                if attempt_1_succeeded and no_retries and gate_ok:
                    first_pass_approved_count += 1

        first_pass_rate = (
            round((first_pass_approved_count / evaluated_gate_count) * 100.0, 1)
            if evaluated_gate_count > 0
            else None
        )

        total_cost = sum(
            (Decimal(str(r.cost or 0)) for r in runs_list), Decimal("0")
        )
        avg_cost_per_seed = (
            (total_cost / Decimal(seed_count)).quantize(Decimal("0.01"))
            if seed_count > 0
            else Decimal("0.00")
        )
        avg_cost_per_stage = (
            (total_cost / Decimal(stage_instances)).quantize(Decimal("0.01"))
            if stage_instances > 0
            else Decimal("0.00")
        )

        total_tokens = sum(
            int(r.input_tokens or 0) + int(r.output_tokens or 0) for r in runs_list
        )
        avg_tokens = int(total_tokens // run_count) if run_count > 0 else 0

        durations: list[float] = []
        for r in runs_list:
            if r.started_at and r.finished_at:
                dur = (r.finished_at - r.started_at).total_seconds()
                if dur >= 0:
                    durations.append(dur)
        avg_duration = (
            round(sum(durations) / len(durations), 1) if durations else None
        )

        entries.append(
            AgentBenchmarkEntry(
                agent_id=agent_id,
                agent_handle=agent.handle,
                agent_name=agent.name,
                runtime=AgentRuntime(runtime_str),
                model=model_str,
                stage=SeedStage(stage_str),
                run_count=run_count,
                succeeded_runs=succeeded_runs,
                seed_count=seed_count,
                stage_instances=stage_instances,
                first_pass_approved_count=first_pass_approved_count,
                evaluated_gate_count=evaluated_gate_count,
                first_pass_approval_rate=first_pass_rate,
                technical_success_rate=tech_rate,
                avg_attempts_per_stage=avg_attempts,
                total_cost=total_cost,
                avg_cost_per_seed=avg_cost_per_seed,
                avg_cost_per_stage=avg_cost_per_stage,
                total_tokens=total_tokens,
                avg_tokens_per_run=avg_tokens,
                avg_duration_seconds=avg_duration,
            )
        )

    # Sort entries: stage order, then highest first-pass approval rate, then lowest avg_cost_per_seed
    stage_order = {"ideate": 0, "plan": 1, "execute": 2, "review": 3, "merge": 4}
    entries.sort(
        key=lambda e: (
            stage_order.get(str(e.stage), 99),
            -(e.first_pass_approval_rate if e.first_pass_approval_rate is not None else -1.0),
            e.avg_cost_per_seed,
        )
    )

    # Synthesize empirical systems-engineering insights
    insights: list[str] = []
    entries_by_stage: dict[str, list[AgentBenchmarkEntry]] = {}
    for e in entries:
        if e.first_pass_approval_rate is not None:
            entries_by_stage.setdefault(str(e.stage), []).append(e)

    for st_name, st_entries in entries_by_stage.items():
        if len(st_entries) >= 2:
            top = st_entries[0]
            alt = st_entries[-1]
            insights.append(
                f"@{top.agent_handle} on {top.model} ({top.runtime}) has a "
                f"{top.first_pass_approval_rate:.0f}% first-pass approval rate in '{st_name}' "
                f"at ${top.avg_cost_per_seed:.2f}/seed ({top.avg_attempts_per_stage:.2f}x attempts), "
                f"while @{alt.agent_handle} on {alt.model} is at "
                f"{alt.first_pass_approval_rate:.0f}% first-pass approval and "
                f"${alt.avg_cost_per_seed:.2f}/seed ({alt.avg_attempts_per_stage:.2f}x attempts)."
            )
        elif len(st_entries) == 1:
            only = st_entries[0]
            insights.append(
                f"@{only.agent_handle} on {only.model} ({only.runtime}) achieved a "
                f"{only.first_pass_approval_rate:.0f}% first-pass approval rate in '{st_name}' "
                f"across {only.seed_count} seed(s) at ${only.avg_cost_per_seed:.2f}/seed "
                f"({only.avg_attempts_per_stage:.2f}x attempts)."
            )

    return AgentBenchmarksResponse(
        project_id=project_id,
        stage=stage,
        entries=entries,
        insights=insights,
    )


