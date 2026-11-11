from fastapi import APIRouter, Depends, Query, Response, WebSocket
from gitseed_common.schemas.agent_session import (
    AgentSessionResponse,
    CreateAgentSessionRequest,
    SendAgentSessionMessageRequest,
    UpdateAgentSessionRequest,
)
from gitseed_common.schemas.sandbox import (
    DiscardSessionWorkspaceRequest,
    SessionPushDraftResponse,
    SessionPushRequest,
    SessionPushResponse,
    SessionVsCodeResponse,
    SessionWorkspaceFileContentResponse,
    SessionWorkspaceFilesResponse,
    WriteSessionWorkspaceFileRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.api.deps import authenticate_websocket, get_current_user, verify_websocket_origin
from gitseed_core.db import AsyncSessionLocal, get_session
from gitseed_core.models.user import User
from gitseed_core.services import agent_sessions as agent_sessions_service
from gitseed_core.services import session_pty
from gitseed_core.services.errors import Forbidden

router = APIRouter(
    prefix="/projects/{project_id}/agent-sessions",
    tags=["Agent Sessions"],
)
# WebSocket routes authenticate in the handshake; mounted without HTTP auth dependencies
pty_router = APIRouter(
    prefix="/projects/{project_id}/agent-sessions",
    tags=["Agent Sessions"],
)


@router.get("", response_model=list[AgentSessionResponse])
async def list_agent_sessions(
    project_id: str,
    saved_only: bool = Query(default=False),
    limit: int = Query(default=30, ge=1, le=100),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[AgentSessionResponse]:
    items = await agent_sessions_service.list_agent_sessions(
        session,
        actor,
        project_id,
        saved_only=saved_only,
        limit=limit,
    )
    return [AgentSessionResponse.model_validate(item) for item in items]


@router.post("", response_model=AgentSessionResponse, status_code=201)
async def create_agent_session(
    project_id: str,
    body: CreateAgentSessionRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentSessionResponse:
    created = await agent_sessions_service.create_agent_session(
        session,
        actor,
        project_id,
        agent_id=body.agent_id,
        runtime=body.runtime,
        models_backend_id=(
            body.inference_backend_id or body.models_backend_id or body.provider_backend_id
        ),
        model=body.model,
        repository_id=body.repository_id,
        seed_id=body.seed_id,
        ref=body.ref,
        title=body.title,
        is_saved=body.is_saved,
        initial_message=body.initial_message or body.initial_prompt,
        propose_seeds=body.propose_seeds,
    )
    return AgentSessionResponse.model_validate(created)


@router.get("/{session_id}", response_model=AgentSessionResponse)
async def get_agent_session(
    project_id: str,
    session_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentSessionResponse:
    item = await agent_sessions_service.get_agent_session(
        session,
        actor,
        project_id,
        session_id,
    )
    return AgentSessionResponse.model_validate(item)


@router.post("/{session_id}/messages", response_model=AgentSessionResponse)
async def send_agent_session_message(
    project_id: str,
    session_id: str,
    body: SendAgentSessionMessageRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentSessionResponse:
    updated = await agent_sessions_service.send_agent_session_message(
        session,
        actor,
        project_id,
        session_id,
        content=body.content,
        propose_seeds=body.propose_seeds,
    )
    return AgentSessionResponse.model_validate(updated)


@router.patch("/{session_id}", response_model=AgentSessionResponse)
async def update_agent_session(
    project_id: str,
    session_id: str,
    body: UpdateAgentSessionRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentSessionResponse:
    updated = await agent_sessions_service.update_agent_session(
        session,
        actor,
        project_id,
        session_id,
        title=body.title,
        is_saved=body.is_saved,
        agent_id=body.agent_id,
    )
    return AgentSessionResponse.model_validate(updated)


@router.post("/{session_id}/stop", response_model=AgentSessionResponse)
async def stop_agent_session(
    project_id: str,
    session_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> AgentSessionResponse:
    stopped = await agent_sessions_service.stop_agent_session(
        session,
        actor,
        project_id,
        session_id,
    )
    return AgentSessionResponse.model_validate(stopped)


@router.delete("/{session_id}", status_code=204)
async def delete_agent_session(
    project_id: str,
    session_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await agent_sessions_service.delete_agent_session(
        session,
        actor,
        project_id,
        session_id,
    )
    return Response(status_code=204)


@router.get("/{session_id}/files", response_model=SessionWorkspaceFilesResponse)
async def list_agent_session_files(
    project_id: str,
    session_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFilesResponse:
    data = await agent_sessions_service.list_agent_session_files(
        session, actor, project_id, session_id
    )
    return SessionWorkspaceFilesResponse(**data)


@router.get("/{session_id}/file", response_model=SessionWorkspaceFileContentResponse)
async def read_agent_session_file(
    project_id: str,
    session_id: str,
    path: str = Query(min_length=1),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFileContentResponse:
    data = await agent_sessions_service.read_agent_session_file(
        session, actor, project_id, session_id, path=path
    )
    return SessionWorkspaceFileContentResponse(**data)


@router.put("/{session_id}/file", response_model=SessionWorkspaceFileContentResponse)
async def write_agent_session_file(
    project_id: str,
    session_id: str,
    body: WriteSessionWorkspaceFileRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFileContentResponse:
    data = await agent_sessions_service.write_agent_session_file(
        session, actor, project_id, session_id, path=body.path, content=body.content
    )
    return SessionWorkspaceFileContentResponse(**data)


@router.delete("/{session_id}/file", status_code=204)
async def delete_agent_session_file(
    project_id: str,
    session_id: str,
    path: str = Query(min_length=1),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await agent_sessions_service.delete_agent_session_file(
        session, actor, project_id, session_id, path=path
    )
    return Response(status_code=204)


@router.post("/{session_id}/discard", response_model=SessionWorkspaceFilesResponse)
async def discard_agent_session_changes(
    project_id: str,
    session_id: str,
    body: DiscardSessionWorkspaceRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFilesResponse:
    data = await agent_sessions_service.discard_agent_session_changes(
        session, actor, project_id, session_id, path=body.path
    )
    return SessionWorkspaceFilesResponse(**data)


@router.post("/{session_id}/vscode", response_model=SessionVsCodeResponse)
async def open_agent_session_vscode(
    project_id: str,
    session_id: str,
    theme: str | None = Query(default=None),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionVsCodeResponse:
    data = await agent_sessions_service.open_agent_session_vscode(
        session, actor, project_id, session_id, theme=theme
    )
    return SessionVsCodeResponse(**data)


@router.post("/{session_id}/push/draft", response_model=SessionPushDraftResponse)
async def draft_agent_session_push(
    project_id: str,
    session_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionPushDraftResponse:
    data = await agent_sessions_service.agent_push_draft(session, actor, project_id, session_id)
    return SessionPushDraftResponse(**data)


@router.post("/{session_id}/push", response_model=SessionPushResponse)
async def push_agent_session(
    project_id: str,
    session_id: str,
    body: SessionPushRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionPushResponse:
    data = await agent_sessions_service.push_agent_session_to_seed(
        session,
        actor,
        project_id,
        session_id,
        title=body.title,
        summary=body.summary,
        acceptance_criteria=body.acceptance_criteria,
        target_seed_id=body.target_seed_id,
    )
    return SessionPushResponse(**data)


@pty_router.websocket("/{session_id}/pty")
async def agent_session_pty(websocket: WebSocket, project_id: str, session_id: str) -> None:
    """Open the agent's own terminal UI (OpenCode, Claude Code, agy, …) in the session container."""
    try:
        verify_websocket_origin(websocket)
    except Forbidden as exc:
        await websocket.close(code=4403, reason=str(exc)[:120])
        return
    await websocket.accept()
    async with AsyncSessionLocal() as db:
        try:
            actor = await authenticate_websocket(websocket, db)
        except Forbidden as exc:
            await websocket.close(code=4403, reason=str(exc)[:120])
            return
        if actor is None:
            await websocket.close(code=4401, reason="Not authenticated")
            return
        # Starting a container (clone + boot) can take a moment; say so instead of a blank screen
        await websocket.send_bytes(b"\x1b[2mPreparing the session workspace\xe2\x80\xa6\x1b[0m\r\n")
        try:
            backend, container_id, can_write, cmd = await agent_sessions_service.prepare_agent_pty(
                db, actor, project_id, session_id
            )
        except Exception as exc:
            await websocket.close(code=4400, reason=str(exc)[:120])
            return

    cols = int(websocket.query_params.get("cols") or 120)
    rows = int(websocket.query_params.get("rows") or 32)
    key = f"agent:{session_id}"
    # The agent CLI keeps running when the browser leaves; reattach to it if it's still alive
    live = session_pty.get_live_pty(key, container_id)
    resumed = live is not None
    if live is None:
        if not can_write:
            await websocket.close(
                code=4403,
                reason="Read-only viewers cannot start a new terminal process",
            )
            return
        try:
            stream = await session_pty.open_exec_pty(backend, container_id, cmd, cols=cols, rows=rows)
        except Exception as exc:
            await websocket.close(code=4500, reason=str(exc)[:120])
            return
        live = session_pty.register_live_pty(
            key,
            backend,
            container_id,
            stream,
            session_pty.TranscriptWriter(
                agent_sessions_service.agent_transcript_path(session_id), cols, rows
            ),
        )
    preamble = b"\x1bc" if resumed else None  # clear the "Preparing…" line before replay
    if not can_write:
        preamble = (preamble or b"") + b"\x1b[33m[read-only: you can watch this session but not type]\x1b[0m\r\n"
    await session_pty.attach_websocket(
        websocket,
        live,
        can_write=can_write,
        cols=cols,
        rows=rows,
        resumed=resumed,
        on_activity=lambda: agent_sessions_service.touch_agent_activity(session_id),
        preamble=preamble,
    )

