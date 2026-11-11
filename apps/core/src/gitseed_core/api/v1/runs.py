from fastapi import APIRouter, Query, status
from gitseed_common.enums import RunStatus, SeedStage
from gitseed_common.schemas.run import (
    CreateRunRequest,
    RunLogsResponse,
    RunResponse,
    UpdateRunRequest,
)

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.services import runs as runs_service

router = APIRouter(prefix="/runs", tags=["Runs"])


@router.get("", response_model=list[RunResponse])
async def list_runs(
    current_user: CurrentUser,
    session: DBSession,
    seed_id: str | None = None,
    project_id: str | None = None,
    agent_id: str | None = None,
    task_id: str | None = None,
    stage: SeedStage | None = None,
    status: RunStatus | None = None,
    active_only: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[RunResponse]:
    """List agent runs across projects visible to the current user."""
    runs = await runs_service.list_runs(
        session,
        current_user,
        seed_id=seed_id,
        project_id=project_id,
        agent_id=agent_id,
        task_id=task_id,
        stage=stage,
        status=status,
        active_only=active_only,
        limit=limit,
        offset=offset,
    )
    return [RunResponse.model_validate(r) for r in runs]


@router.post(
    "",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_run(
    payload: CreateRunRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> RunResponse:
    """Queue a new agent run for a seed."""
    run = await runs_service.create_run(
        session,
        current_user,
        seed_id=payload.seed_id,
        task_id=payload.task_id,
        stage=payload.stage,
        agent_id=payload.agent_id,
        compute_backend_id=payload.compute_backend_id,
    )
    return RunResponse.model_validate(run)


@router.get("/{run_id}", response_model=RunResponse)
async def get_run(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> RunResponse:
    """Retrieve a single run by ID."""
    run = await runs_service.get_run(session, current_user, run_id)
    return RunResponse.model_validate(run)


@router.get("/{run_id}/logs", response_model=RunLogsResponse)
async def get_run_logs(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
    tail: int = Query(default=800, ge=50, le=5000),
) -> RunLogsResponse:
    """Retrieve live or persisted container stdout/stderr logs for a run."""
    run, log_tail = await runs_service.get_run_logs(
        session, current_user, run_id, tail_lines=tail
    )
    return RunLogsResponse(run_id=run.id, status=run.status, log_tail=log_tail)


@router.patch("/{run_id}", response_model=RunResponse)
async def update_run(
    run_id: str,
    payload: UpdateRunRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> RunResponse:
    """Update a run's status, usage metrics, cost, or output."""
    run = await runs_service.update_run(
        session,
        current_user,
        run_id,
        status=payload.status,
        compute_backend_id=payload.compute_backend_id,
        container_id=payload.container_id,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
        cost=payload.cost,
        output=payload.output,
        exit_code=payload.exit_code,
        error=payload.error,
        log_path=payload.log_path,
        fields_set=payload.model_fields_set,
    )
    return RunResponse.model_validate(run)


@router.post("/{run_id}/cancel", response_model=RunResponse)
async def cancel_run(
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> RunResponse:
    """Cancel an active (queued, starting, or running) run."""
    run = await runs_service.cancel_run(session, current_user, run_id)
    return RunResponse.model_validate(run)
