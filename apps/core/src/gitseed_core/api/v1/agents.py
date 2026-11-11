from typing import Literal

from fastapi import APIRouter, status
from gitseed_common.enums import AgentKind, SeedStage
from gitseed_common.schemas.agent import (
    AgentBenchmarksResponse,
    AgentResponse,
    CoordinatorChatRequest,
    CoordinatorChatResponse,
    CreateAgentRequest,
    UpdateAgentRequest,
)
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.api.deps import AdminUser, CurrentUser, DBSession
from gitseed_core.models.agent import Agent
from gitseed_core.services import agents as agents_service
from gitseed_core.services import coordinator as coordinator_service
from gitseed_core.services import runner_runtimes as runner_runtimes_service

router = APIRouter(prefix="/agents", tags=["Agents"])


async def _serialize_agent(
    session: AsyncSession,
    agent: Agent,
    metrics_map: dict[str, agents_service.AgentMetrics] | None = None,
    backends_map: dict[str, list[str]] | None = None,
) -> AgentResponse:
    if backends_map is not None:
        allowed_backends = backends_map.get(agent.id, [])
    else:
        allowed_backends = await agents_service.get_agent_allowed_backend_ids(
            session, agent.id
        )
    if metrics_map is None:
        metrics_map = await agents_service.get_agent_metrics(session, [agent.id])
    metrics = metrics_map.get(agent.id, agents_service.AgentMetrics())
    warning = await agents_service.compute_agent_compatibility_warning(session, agent)
    return AgentResponse.model_validate(agent).model_copy(
        update={
            "compatibility_warning": warning,
            "allowed_backend_ids": allowed_backends,
            "run_count": metrics.run_count,
            "succeeded_runs": metrics.succeeded_runs,
            "total_cost": metrics.total_cost,
            "first_pass_rate": metrics.first_pass_rate,
            "avg_cost_per_seed": metrics.avg_cost_per_seed,
            "avg_attempts": metrics.avg_attempts,
        }
    )


@router.get("", response_model=list[AgentResponse])
async def list_agents(
    _current_user: CurrentUser,
    session: DBSession,
    kind: AgentKind | None = None,
    enabled: bool | None = None,
    include_archived: bool = False,
) -> list[AgentResponse]:
    """List registered agents with computed run metrics."""
    agents = await agents_service.list_agents(
        session,
        kind=kind,
        enabled=enabled,
        include_archived=include_archived,
    )
    agent_ids = [a.id for a in agents]
    metrics_map = await agents_service.get_agent_metrics(session, agent_ids)
    backends_map = await agents_service.get_agent_allowed_backend_ids_map(
        session, agent_ids
    )
    return [
        await _serialize_agent(session, a, metrics_map, backends_map) for a in agents
    ]


@router.post(
    "",
    response_model=AgentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agent(
    payload: CreateAgentRequest,
    admin: AdminUser,
    session: DBSession,
) -> AgentResponse:
    """Register a new stage or assistant agent (admin only)."""
    agent = await agents_service.create_agent(
        session,
        admin,
        name=payload.name,
        handle=payload.handle,
        kind=payload.kind,
        template=payload.template,
        instructions=payload.instructions,
        runtime=payload.runtime,
        model=payload.model,
        skills=payload.skills,
        tools=payload.tools,
        enabled=payload.enabled,
    )
    return await _serialize_agent(session, agent)


@router.post("/coordinator/chat", response_model=CoordinatorChatResponse)
async def chat_coordinator(
    payload: CoordinatorChatRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> CoordinatorChatResponse:
    """Execute a conversational prompt against @coordinator with live workspace context."""
    return await coordinator_service.chat_with_coordinator(
        session, current_user, payload
    )


@router.get("/benchmarks", response_model=AgentBenchmarksResponse)
async def get_agent_benchmarks(
    current_user: CurrentUser,
    session: DBSession,
    project_id: str | None = None,
    stage: SeedStage | None = None,
) -> AgentBenchmarksResponse:
    """Retrieve empirical agent/model benchmark scorecards across stages and projects."""
    return await agents_service.compute_agent_benchmarks(
        session,
        actor=current_user,
        project_id=project_id,
        stage=stage,
    )


class RuntimeAvailabilityResponse(BaseModel):
    """Which agent CLI runtimes are installed in the configured runner image."""

    available: dict[str, bool] | None = None
    source: Literal["config", "probe", "unknown"] = "unknown"
    image: str | None = None


@router.get("/runtimes/availability", response_model=RuntimeAvailabilityResponse)
async def get_runtime_availability(
    _current_user: CurrentUser,
    session: DBSession,
) -> RuntimeAvailabilityResponse:
    """Report which runtimes (`opencode`, `claude-code`, ...) the runner image can execute.

    `available` is `null` when it can't be determined (e.g. Kubernetes without `RUNNER_RUNTIMES`).
    """
    result = await runner_runtimes_service.get_runtime_availability(session)
    return RuntimeAvailabilityResponse.model_validate(result)


@router.get("/{agent_id}", response_model=AgentResponse)
async def get_agent(
    agent_id: str,
    _current_user: CurrentUser,
    session: DBSession,
) -> AgentResponse:
    """Retrieve a single agent by ID or handle."""
    agent = await agents_service.get_agent(session, agent_id)
    return await _serialize_agent(session, agent)


@router.patch("/{agent_id}", response_model=AgentResponse)
async def update_agent(
    agent_id: str,
    payload: UpdateAgentRequest,
    _admin: AdminUser,
    session: DBSession,
) -> AgentResponse:
    """Update an agent's configuration (admin only)."""
    agent = await agents_service.update_agent(
        session,
        agent_id,
        name=payload.name,
        handle=payload.handle,
        kind=payload.kind,
        template=payload.template,
        instructions=payload.instructions,
        runtime=payload.runtime,
        model=payload.model,
        skills=payload.skills,
        tools=payload.tools,
        enabled=payload.enabled,
        fields_set=payload.model_fields_set,
    )
    return await _serialize_agent(session, agent)


@router.post("/{agent_id}/archive", response_model=AgentResponse)
async def archive_agent(
    agent_id: str,
    admin: AdminUser,
    session: DBSession,
) -> AgentResponse:
    """Archive an agent if no stage policy depends on it (admin only)."""
    agent = await agents_service.archive_agent(session, admin, agent_id)
    return await _serialize_agent(session, agent)


@router.post("/{agent_id}/restore", response_model=AgentResponse)
async def restore_agent(
    agent_id: str,
    _admin: AdminUser,
    session: DBSession,
) -> AgentResponse:
    """Restore an archived agent (admin only)."""
    agent = await agents_service.restore_agent(session, agent_id)
    return await _serialize_agent(session, agent)
