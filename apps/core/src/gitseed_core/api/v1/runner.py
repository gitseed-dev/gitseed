from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse
from gitseed_common.schemas.run import RunResponse

from gitseed_core.api.deps import DBSession
from gitseed_core.schemas.runner import (
    RunnerCliAuthRefreshRequest,
    RunnerCliAuthRefreshResponse,
    RunnerCompleteRequest,
    RunnerContextResponse,
    RunnerFinalizeContextResponse,
    RunnerGitPushTokenRequest,
    RunnerGitPushTokenResponse,
    RunnerPrepareCompleteRequest,
    RunnerPrepareCompleteResponse,
    RunnerSteeringRequest,
    RunnerSteeringResponse,
)
from gitseed_core.services import runner_bridge

router = APIRouter(prefix="/runner", tags=["Runner Bridge"])


def _extract_bearer(request: Request) -> str | None:
    auth_header = request.headers.get("authorization") or ""
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip()
    return request.headers.get("x-run-token")


@router.get("/context", response_model=RunnerContextResponse)
async def get_context(
    request: Request,
    session: DBSession,
) -> RunnerContextResponse:
    """Fetch execution context for the prepare container and transition Run to 'running'."""
    return await runner_bridge.get_runner_context(session, _extract_bearer(request))


@router.post("/prepare-complete", response_model=RunnerPrepareCompleteResponse)
async def prepare_complete(
    payload: RunnerPrepareCompleteRequest,
    request: Request,
    session: DBSession,
) -> RunnerPrepareCompleteResponse:
    """Record trusted repository base SHAs and remote branch heads from the prepare container."""
    return await runner_bridge.complete_runner_prepare(
        session, _extract_bearer(request), payload
    )


@router.get("/finalize-context", response_model=RunnerFinalizeContextResponse)
async def get_finalize_context(
    request: Request,
    session: DBSession,
) -> RunnerFinalizeContextResponse:
    """Fetch trusted finalize context (base SHAs, blocked_paths, repositories) for the finalize container."""
    return await runner_bridge.get_runner_finalize_context(
        session, _extract_bearer(request)
    )


@router.get("/steering", response_model=RunnerSteeringResponse)
async def poll_steering(
    request: Request,
    session: DBSession,
    since: datetime | None = None,
    log_tail: str | None = None,
    context_tokens: int | None = None,
    peak_context_tokens: int | None = None,
    turn_count: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> RunnerSteeringResponse:
    """Poll for run cancellation/budget blocks or new @agent steering comments."""
    return await runner_bridge.poll_runner_steering(
        session,
        _extract_bearer(request),
        since=since,
        log_tail=log_tail,
        context_tokens=context_tokens,
        peak_context_tokens=peak_context_tokens,
        turn_count=turn_count,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


@router.post("/steering", response_model=RunnerSteeringResponse)
async def post_steering_heartbeat(
    payload: RunnerSteeringRequest,
    request: Request,
    session: DBSession,
) -> RunnerSteeringResponse:
    """Heartbeat endpoint for live runner log streaming + steering check."""
    return await runner_bridge.poll_runner_steering(
        session,
        _extract_bearer(request),
        since=payload.since,
        log_tail=payload.log_tail,
        context_tokens=payload.context_tokens,
        peak_context_tokens=payload.peak_context_tokens,
        turn_count=payload.turn_count,
        input_tokens=payload.input_tokens,
        output_tokens=payload.output_tokens,
    )


@router.get("/attachments/{attachment_id}")
async def download_attachment(
    attachment_id: str,
    request: Request,
    session: DBSession,
) -> FileResponse:
    """Download one of this run's seed attachments (run-token authenticated)."""
    path, filename, content_type = await runner_bridge.resolve_runner_attachment(
        session, _extract_bearer(request), attachment_id
    )
    return FileResponse(path=path, filename=filename, media_type=content_type)


@router.post("/git-push-token", response_model=RunnerGitPushTokenResponse)
@router.post("/git-push-credential", response_model=RunnerGitPushTokenResponse)
async def request_git_push_token(
    payload: RunnerGitPushTokenRequest,
    request: Request,
    session: DBSession,
) -> RunnerGitPushTokenResponse:
    """Issue a short-lived Git write credential to the trusted finalize container."""
    return await runner_bridge.issue_git_push_token(
        session,
        _extract_bearer(request),
        repository_full_name=payload.repository_full_name,
    )


@router.post("/cli-auth-refresh", response_model=RunnerCliAuthRefreshResponse)
async def refresh_cli_auth(
    payload: RunnerCliAuthRefreshRequest,
    request: Request,
    session: DBSession,
) -> RunnerCliAuthRefreshResponse:
    """Update allowlisted per-runtime OAuth credential files from an active agent container."""
    return await runner_bridge.refresh_runner_cli_auth(
        session,
        _extract_bearer(request),
        payload,
    )


@router.post("/complete", response_model=RunResponse)
async def complete_run(
    payload: RunnerCompleteRequest,
    request: Request,
    session: DBSession,
) -> RunResponse:
    """Validate /workspace/.gitseed/output.json and finalize the Run and Seed state."""
    run = await runner_bridge.complete_runner_execution(
        session, _extract_bearer(request), payload
    )
    return RunResponse.model_validate(run)

