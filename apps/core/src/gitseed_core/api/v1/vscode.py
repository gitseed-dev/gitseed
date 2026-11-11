"""HTTP & WebSocket proxy routes for on-demand VS Code Web (`openvscode-server`).

Mounted under `/api/v1/vscode/{session_key}/{token}` without router-level HTTP auth
dependencies because both HTTP sub-resources (scripts, web workers, WASM, extensions)
and WebSocket upgrades are gated by the cryptographic per-session `token` that is
revoked as soon as the session stops or is deleted.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, Response, WebSocket

from gitseed_core.services.errors import Invalid
from gitseed_core.services.session_vscode import (
    enforce_vscode_production_origin,
    proxy_vscode_http,
    proxy_vscode_websocket,
)

router = APIRouter(prefix="/vscode", tags=["VS Code"])


@router.api_route(
    "/{session_key}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def vscode_proxy_cookie_root(
    request: Request,
    session_key: str,
) -> Response:
    enforce_vscode_production_origin()
    return await proxy_vscode_http(
        request, session_key=session_key, token="_cookie", subpath=""
    )


@router.api_route(
    "/{session_key}/{token}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def vscode_proxy_root(
    request: Request,
    session_key: str,
    token: str,
) -> Response:
    enforce_vscode_production_origin()
    return await proxy_vscode_http(
        request, session_key=session_key, token=token, subpath=""
    )


@router.api_route(
    "/{session_key}/{token}/{subpath:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"],
)
async def vscode_proxy_subpath(
    request: Request,
    session_key: str,
    token: str,
    subpath: str,
) -> Response:
    enforce_vscode_production_origin()
    return await proxy_vscode_http(
        request, session_key=session_key, token=token, subpath=subpath
    )


@router.websocket("/{session_key}")
async def vscode_ws_cookie_root(
    websocket: WebSocket,
    session_key: str,
) -> None:
    try:
        enforce_vscode_production_origin()
    except Invalid as exc:
        await websocket.close(code=4403, reason=str(exc))
        return
    await proxy_vscode_websocket(
        websocket, session_key=session_key, token="_cookie", subpath=""
    )


@router.websocket("/{session_key}/{token}")
async def vscode_ws_root(
    websocket: WebSocket,
    session_key: str,
    token: str,
) -> None:
    try:
        enforce_vscode_production_origin()
    except Invalid as exc:
        await websocket.close(code=4403, reason=str(exc))
        return
    await proxy_vscode_websocket(
        websocket, session_key=session_key, token=token, subpath=""
    )


@router.websocket("/{session_key}/{token}/{subpath:path}")
async def vscode_ws_subpath(
    websocket: WebSocket,
    session_key: str,
    token: str,
    subpath: str,
) -> None:
    try:
        enforce_vscode_production_origin()
    except Invalid as exc:
        await websocket.close(code=4403, reason=str(exc))
        return
    await proxy_vscode_websocket(
        websocket, session_key=session_key, token=token, subpath=subpath
    )

