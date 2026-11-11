import logging

from fastapi import APIRouter, Depends, Query, Response, WebSocket
from fastapi.responses import FileResponse
from gitseed_common.schemas.sandbox import (
    CreateSandboxSessionRequest,
    DiscardSessionWorkspaceRequest,
    SandboxInputRequest,
    SandboxRunConfigResponse,
    SandboxSessionResponse,
    SaveSandboxArtifactRequest,
    SessionPushDraftResponse,
    SessionPushRequest,
    SessionPushResponse,
    SessionVsCodeResponse,
    SessionWorkspaceFileContentResponse,
    SessionWorkspaceFilesResponse,
    UpdateSandboxSessionRequest,
    WriteSessionWorkspaceFileRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.api.deps import authenticate_websocket, get_current_user, verify_websocket_origin
from gitseed_core.db import AsyncSessionLocal, get_session
from gitseed_core.models.sandbox import SandboxSession
from gitseed_core.models.user import User
from gitseed_core.services import sandboxes as sandboxes_service
from gitseed_core.services import session_pty
from gitseed_core.services.compute_runtime import fetch_docker_container_logs
from gitseed_core.services.errors import Forbidden

logger = logging.getLogger("uvicorn.error")

router = APIRouter(prefix="/projects/{project_id}/sandboxes", tags=["Sandboxes"])
# WebSocket routes authenticate in the handshake; mounted without HTTP auth dependencies
pty_router = APIRouter(prefix="/projects/{project_id}/sandboxes", tags=["Sandboxes"])


def _to_response(sbx: SandboxSession) -> SandboxSessionResponse:
    resp = SandboxSessionResponse.model_validate(sbx)
    resp.artifacts_expire_at = sandboxes_service.artifacts_expire_at(sbx)
    return resp


@router.get("/config", response_model=SandboxRunConfigResponse)
async def get_sandbox_config(
    project_id: str,
    repo_id: int | None = Query(default=None),
    ref: str | None = Query(default=None),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxRunConfigResponse:
    cfg = await sandboxes_service.get_sandbox_run_config(
        session,
        actor,
        project_id,
        repo_id=repo_id,
        ref=ref,
    )
    return SandboxRunConfigResponse(**cfg)


@router.get("", response_model=list[SandboxSessionResponse])
async def list_sandboxes(
    project_id: str,
    limit: int = Query(default=25, ge=1, le=100),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> list[SandboxSessionResponse]:
    rows = await sandboxes_service.list_sandbox_sessions(
        session,
        actor,
        project_id,
        limit=limit,
    )
    return [_to_response(r) for r in rows]


@router.post("", response_model=SandboxSessionResponse, status_code=201)
async def create_sandbox(
    project_id: str,
    body: CreateSandboxSessionRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.create_sandbox_session(
        session,
        actor,
        project_id,
        repository_id=body.repository_id,
        seed_id=body.seed_id,
        ref=body.ref,
        image=body.image,
        setup_command=body.setup_command,
        command=body.command,
        artifact_globs=body.artifact_globs,
        initial_stdin=body.initial_stdin,
        as_root=body.as_root,
        kind=body.kind,
        title=body.title,
    )
    return _to_response(sbx)


@router.get("/{sandbox_id}", response_model=SandboxSessionResponse)
async def get_sandbox(
    project_id: str,
    sandbox_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.get_sandbox_session(
        session,
        actor,
        project_id,
        sandbox_id,
    )
    return _to_response(sbx)


@router.patch("/{sandbox_id}", response_model=SandboxSessionResponse)
async def update_sandbox(
    project_id: str,
    sandbox_id: str,
    body: UpdateSandboxSessionRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.update_sandbox_session(
        session, actor, project_id, sandbox_id, title=body.title
    )
    return _to_response(sbx)


@router.delete("/{sandbox_id}", status_code=204)
async def delete_sandbox(
    project_id: str,
    sandbox_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await sandboxes_service.delete_sandbox_session(session, actor, project_id, sandbox_id)
    return Response(status_code=204)


@router.post("/{sandbox_id}/input", response_model=SandboxSessionResponse)
async def send_sandbox_input(
    project_id: str,
    sandbox_id: str,
    body: SandboxInputRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.send_sandbox_input(
        session,
        actor,
        project_id,
        sandbox_id,
        input_text=body.input,
        append_newline=body.append_newline,
        eof=body.eof,
    )
    return _to_response(sbx)


@router.post("/{sandbox_id}/stop", response_model=SandboxSessionResponse)
async def stop_sandbox(
    project_id: str,
    sandbox_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.stop_sandbox_session(
        session,
        actor,
        project_id,
        sandbox_id,
    )
    return _to_response(sbx)


@router.post("/{sandbox_id}/artifacts", response_model=SandboxSessionResponse)
async def save_sandbox_artifact(
    project_id: str,
    sandbox_id: str,
    body: SaveSandboxArtifactRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.save_sandbox_artifact(
        session, actor, project_id, sandbox_id, path=body.path
    )
    return _to_response(sbx)


@router.get("/{sandbox_id}/artifacts/{artifact_path:path}")
async def download_sandbox_artifact(
    project_id: str,
    sandbox_id: str,
    artifact_path: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    file_path, filename = await sandboxes_service.resolve_sandbox_artifact_path(
        session,
        actor,
        project_id,
        sandbox_id,
        artifact_path,
    )
    return FileResponse(
        path=str(file_path),
        filename=filename,
        media_type="application/octet-stream",
    )


@router.delete("/{sandbox_id}/artifacts/{artifact_path:path}", response_model=SandboxSessionResponse)
async def delete_sandbox_artifact(
    project_id: str,
    sandbox_id: str,
    artifact_path: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SandboxSessionResponse:
    sbx = await sandboxes_service.delete_sandbox_artifact(
        session, actor, project_id, sandbox_id, artifact_path
    )
    return _to_response(sbx)


@router.get("/{sandbox_id}/files", response_model=SessionWorkspaceFilesResponse)
async def list_sandbox_files(
    project_id: str,
    sandbox_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFilesResponse:
    data = await sandboxes_service.list_sandbox_files(session, actor, project_id, sandbox_id)
    return SessionWorkspaceFilesResponse(**data)


@router.get("/{sandbox_id}/file", response_model=SessionWorkspaceFileContentResponse)
async def read_sandbox_file(
    project_id: str,
    sandbox_id: str,
    path: str = Query(min_length=1),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFileContentResponse:
    data = await sandboxes_service.read_sandbox_file(
        session, actor, project_id, sandbox_id, path=path
    )
    return SessionWorkspaceFileContentResponse(**data)


@router.put("/{sandbox_id}/file", response_model=SessionWorkspaceFileContentResponse)
async def write_sandbox_file(
    project_id: str,
    sandbox_id: str,
    body: WriteSessionWorkspaceFileRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFileContentResponse:
    data = await sandboxes_service.write_sandbox_file(
        session, actor, project_id, sandbox_id, path=body.path, content=body.content
    )
    return SessionWorkspaceFileContentResponse(**data)


@router.delete("/{sandbox_id}/file", status_code=204)
async def delete_sandbox_file(
    project_id: str,
    sandbox_id: str,
    path: str = Query(min_length=1),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> Response:
    await sandboxes_service.delete_sandbox_file(
        session, actor, project_id, sandbox_id, path=path
    )
    return Response(status_code=204)


@router.post("/{sandbox_id}/discard", response_model=SessionWorkspaceFilesResponse)
async def discard_sandbox_changes(
    project_id: str,
    sandbox_id: str,
    body: DiscardSessionWorkspaceRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionWorkspaceFilesResponse:
    data = await sandboxes_service.discard_sandbox_changes(
        session, actor, project_id, sandbox_id, path=body.path
    )
    return SessionWorkspaceFilesResponse(**data)


@router.post("/{sandbox_id}/vscode", response_model=SessionVsCodeResponse)
async def open_sandbox_vscode(
    project_id: str,
    sandbox_id: str,
    theme: str | None = Query(default=None),
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionVsCodeResponse:
    data = await sandboxes_service.open_sandbox_vscode(
        session, actor, project_id, sandbox_id, theme=theme
    )
    return SessionVsCodeResponse(**data)


@router.post("/{sandbox_id}/push/draft", response_model=SessionPushDraftResponse)
async def draft_sandbox_push(
    project_id: str,
    sandbox_id: str,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionPushDraftResponse:
    data = await sandboxes_service.sandbox_push_draft(session, actor, project_id, sandbox_id)
    return SessionPushDraftResponse(**data)


@router.post("/{sandbox_id}/push", response_model=SessionPushResponse)
async def push_sandbox(
    project_id: str,
    sandbox_id: str,
    body: SessionPushRequest,
    actor: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
) -> SessionPushResponse:
    data = await sandboxes_service.push_sandbox_to_seed(
        session,
        actor,
        project_id,
        sandbox_id,
        title=body.title,
        summary=body.summary,
        acceptance_criteria=body.acceptance_criteria,
        target_seed_id=body.target_seed_id,
    )
    return SessionPushResponse(**data)


@pty_router.websocket("/{sandbox_id}/pty")
async def sandbox_pty(websocket: WebSocket, project_id: str, sandbox_id: str) -> None:
    """Interactive terminal: attach a real PTY shell (`docker exec -it`) to a running terminal session."""
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
        try:
            sbx, backend, can_write = await sandboxes_service.prepare_sandbox_pty(
                db, actor, project_id, sandbox_id
            )
        except Exception as exc:
            await websocket.close(code=4400, reason=str(exc)[:120])
            return
        container_id = sbx.container_id or ""
        user = "0" if sbx.as_root else None
        preamble_logs = await fetch_docker_container_logs(backend, container_id) if sbx.setup_command else ""

    cols = int(websocket.query_params.get("cols") or 120)
    rows = int(websocket.query_params.get("rows") or 32)
    key = f"sandbox:{sandbox_id}"
    # The shell keeps running when the browser leaves; reattach to it if it's still alive
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
            stream = await session_pty.open_exec_pty(
                backend,
                container_id,
                ["sh", "-c", "if command -v bash >/dev/null 2>&1; then exec bash -l; else exec sh -l; fi"],
                user=user,
                cols=cols,
                rows=rows,
            )
        except Exception as exc:
            await websocket.close(code=4500, reason=str(exc)[:120])
            return
        live = session_pty.register_live_pty(
            key,
            backend,
            container_id,
            stream,
            session_pty.TranscriptWriter(sandboxes_service.sandbox_transcript_path(sandbox_id), cols, rows),
        )

    preamble = None
    if preamble_logs and not resumed:
        preamble = (
            "\x1b[2m" + preamble_logs.strip().replace("\n", "\r\n")[-8000:] + "\x1b[0m\r\n"
        ).encode("utf-8", errors="replace")
    if not can_write:
        note = "\x1b[33m[read-only: you can watch this session but not type]\x1b[0m\r\n"
        preamble = (preamble or b"") + note.encode()

    await session_pty.attach_websocket(
        websocket,
        live,
        can_write=can_write,
        cols=cols,
        rows=rows,
        resumed=resumed,
        on_activity=lambda: sandboxes_service.touch_sandbox_activity(sandbox_id),
        preamble=preamble,
    )
