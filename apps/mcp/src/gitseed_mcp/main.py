from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, TextIO

import httpx
import uvicorn
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

from gitseed_mcp.client import GitSeedApiError
from gitseed_mcp.config import MCPSettings, get_settings
from gitseed_mcp.server import (
    JSONRPC_PARSE_ERROR,
    PROMPTS_CATALOG,
    RESOURCE_TEMPLATES_CATALOG,
    RESOURCES_CATALOG,
    SUPPORTED_PROTOCOL_VERSIONS,
    TOOLS_CATALOG,
    GitSeedMCPServer,
)


@dataclass
class SSESession:
    session_id: str
    token: str | None = None
    queue: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)


def extract_request_token(request: Request) -> str | None:
    """Extract a caller API token from Authorization Bearer, X-API-Key, or query parameters."""
    auth_header = request.headers.get("authorization")
    if auth_header:
        parts = auth_header.strip().split(None, 1)
        if len(parts) == 2 and parts[0].lower() == "bearer" and parts[1].strip():
            return parts[1].strip()
        if len(parts) == 1 and parts[0].startswith("gsp_"):
            return parts[0]

    api_key = request.headers.get("x-api-key")
    if api_key and api_key.strip():
        return api_key.strip()

    query_token = request.query_params.get("token") or request.query_params.get("api_key")
    if query_token and query_token.strip():
        return query_token.strip()

    return None


def _validate_origin(request: Request, cfg: MCPSettings) -> JSONResponse | None:
    """Reject untrusted browser Origin headers to block CSRF and DNS-rebinding attacks."""
    origin = request.headers.get("origin")
    if origin is not None and not cfg.is_origin_allowed(origin):
        return JSONResponse(
            status_code=403,
            content={"error": "Forbidden: Origin header is not allowed"},
        )
    return None


def _validate_json_content_type(request: Request) -> JSONResponse | None:
    """Require application/json Content-Type on POST endpoints to block CORS simple requests."""
    content_type = request.headers.get("content-type", "")
    if not content_type.strip().lower().startswith("application/json"):
        return JSONResponse(
            status_code=415,
            content={
                "error": "Unsupported Media Type: Content-Type must be application/json"
            },
        )
    return None


def _unauthorized_response(
    detail: str = "Unauthorized: Authorization Bearer token is required",
) -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={"error": detail},
        headers={"WWW-Authenticate": "Bearer"},
    )


def create_app(
    settings: MCPSettings | None = None,
    *,
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    """Create the FastAPI ASGI application for the GitSeed MCP server."""
    cfg = settings or get_settings()
    mcp_server = GitSeedMCPServer(settings=cfg, http_transport=http_transport)
    sse_sessions: dict[str, SSESession] = {}
    streamable_sessions: dict[str, dict[str, Any]] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await mcp_server.aclose()

    app = FastAPI(
        title="GitSeed MCP Server",
        version=cfg.GITSEED_VERSION,
        description="Model Context Protocol (MCP) server for the GitSeed AI-native SDLC platform.",
        lifespan=lifespan,
    )
    app.state.mcp_server = mcp_server
    app.state.sse_sessions = sse_sessions
    app.state.streamable_sessions = streamable_sessions

    @app.get("/")
    async def root_info() -> dict[str, Any]:
        return {
            "name": "gitseed-mcp",
            "version": cfg.GITSEED_VERSION,
            "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            "apiBaseUrl": cfg.api_base_url,
            "transports": {
                "streamableHttp": "/mcp",
                "sse": "/sse",
                "messages": "/messages",
                "health": "/healthz",
            },
            "capabilities": {
                "tools": len(TOOLS_CATALOG),
                "resources": len(RESOURCES_CATALOG),
                "resourceTemplates": len(RESOURCE_TEMPLATES_CATALOG),
                "prompts": len(PROMPTS_CATALOG),
            },
        }

    @app.get("/healthz")
    @app.get("/health")
    async def health_check(request: Request) -> dict[str, Any]:
        probe_upstream = request.query_params.get("probe_upstream", "false").lower() in (
            "1",
            "true",
            "yes",
        )
        upstream_status = "unprobed"
        if probe_upstream:
            client = mcp_server.create_client(
                extract_request_token(request),
                allow_env_fallback=False,
            )
            try:
                await client.get_health()
                upstream_status = "ok"
            except GitSeedApiError:
                upstream_status = "unreachable"

        return {
            "status": "ok",
            "service": "gitseed-mcp",
            "version": cfg.GITSEED_VERSION,
            "api_base_url": cfg.api_base_url,
            "upstream_status": upstream_status,
            "active_sse_sessions": len(sse_sessions),
        }

    # ── MCP Streamable HTTP Transport (/mcp) ──────────────────────────

    @app.post("/mcp")
    async def handle_streamable_http_post(request: Request) -> Response:
        if (origin_err := _validate_origin(request, cfg)) is not None:
            return origin_err
        if (ct_err := _validate_json_content_type(request)) is not None:
            return ct_err

        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()

        session_id = request.headers.get("mcp-session-id")
        session_state = streamable_sessions.get(session_id) if session_id else None

        if session_state is not None:
            cached_token = session_state.get("token")
            if cached_token is not None and incoming_token != cached_token:
                # Never allow an unauthenticated or differently-authenticated caller
                # to inherit or overwrite another user's authenticated session.
                session_id = uuid.uuid4().hex
                session_state = None

        if not session_id:
            session_id = uuid.uuid4().hex

        if session_state is None:
            session_state = {"token": incoming_token, "queue": None}
            streamable_sessions[session_id] = session_state
            while len(streamable_sessions) > 1000:
                streamable_sessions.pop(next(iter(streamable_sessions)), None)

        request_token = incoming_token

        headers = {"Mcp-Session-Id": session_id}

        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            err = GitSeedMCPServer._error_response(
                None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}"
            )
            return JSONResponse(status_code=400, content=err, headers=headers)

        response_data = await mcp_server.handle_payload(
            payload,
            request_token=request_token,
            allow_env_fallback=False,
        )
        if response_data is None:
            return Response(status_code=202, headers=headers)

        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept and "application/json" not in accept:
            sse_payload = f"event: message\ndata: {json.dumps(response_data)}\n\n"
            return Response(
                content=sse_payload,
                media_type="text/event-stream",
                headers={
                    **headers,
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        q = session_state.get("queue") if isinstance(session_state, dict) else None
        if isinstance(q, asyncio.Queue):
            await q.put(json.dumps(response_data))

        return JSONResponse(content=response_data, headers=headers)

    @app.get("/mcp")
    async def handle_streamable_http_get(request: Request) -> Response:
        if (origin_err := _validate_origin(request, cfg)) is not None:
            return origin_err
        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()

        mcp_session_id = request.headers.get("mcp-session-id") or uuid.uuid4().hex
        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            existing_state = streamable_sessions.get(mcp_session_id)
            if existing_state is not None:
                cached_token = existing_state.get("token")
                if cached_token is not None and incoming_token != cached_token:
                    mcp_session_id = uuid.uuid4().hex
                    existing_state = None

            session = SSESession(
                session_id=mcp_session_id,
                token=incoming_token,
            )
            sse_sessions[mcp_session_id] = session
            while len(sse_sessions) > 1000:
                sse_sessions.pop(next(iter(sse_sessions)), None)

            if existing_state is None:
                streamable_sessions[mcp_session_id] = {
                    "token": incoming_token,
                    "queue": session.queue,
                }
                while len(streamable_sessions) > 1000:
                    streamable_sessions.pop(next(iter(streamable_sessions)), None)
            else:
                streamable_sessions[mcp_session_id]["queue"] = session.queue

            once = request.query_params.get("once") in ("1", "true")

            async def event_stream() -> AsyncIterator[str]:
                try:
                    yield f": session {mcp_session_id}\n\n"
                    if once:
                        return
                    while not await request.is_disconnected():
                        try:
                            msg = await asyncio.wait_for(session.queue.get(), timeout=15.0)
                        except TimeoutError:
                            yield ": ping\n\n"
                            continue
                        if msg is None:
                            break
                        yield f"event: message\ndata: {msg}\n\n"
                finally:
                    sse_sessions.pop(mcp_session_id, None)
                    st = streamable_sessions.get(mcp_session_id)
                    if isinstance(st, dict):
                        st["queue"] = None

            return StreamingResponse(
                event_stream(),
                media_type="text/event-stream",
                headers={
                    "Mcp-Session-Id": mcp_session_id,
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                },
            )

        return JSONResponse(
            content={
                "name": "gitseed-mcp",
                "version": cfg.GITSEED_VERSION,
                "transport": "streamable-http",
                "protocolVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
            },
            headers={"Mcp-Session-Id": mcp_session_id},
        )

    @app.delete("/mcp")
    async def handle_streamable_http_delete(request: Request) -> Response:
        if (origin_err := _validate_origin(request, cfg)) is not None:
            return origin_err
        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()

        session_id = request.headers.get("mcp-session-id")
        if session_id:
            existing_state = streamable_sessions.get(session_id)
            if (
                isinstance(existing_state, dict)
                and existing_state.get("token") is not None
                and existing_state.get("token") != incoming_token
            ):
                return _unauthorized_response(
                    "Unauthorized: mismatched bearer token for MCP session"
                )
            session_state = streamable_sessions.pop(session_id, None)
            if isinstance(session_state, dict):
                q = session_state.get("queue")
                if isinstance(q, asyncio.Queue):
                    await q.put(None)
            sse_session = sse_sessions.pop(session_id, None)
            if sse_session is not None:
                await sse_session.queue.put(None)
        return Response(status_code=204)

    # ── MCP HTTP + SSE Transport (/sse + /messages) ───────────────────

    @app.get("/sse")
    async def handle_sse_connect(request: Request) -> Response:
        if (origin_err := _validate_origin(request, cfg)) is not None:
            return origin_err
        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()

        session_id = uuid.uuid4().hex
        session = SSESession(
            session_id=session_id,
            token=incoming_token,
        )
        sse_sessions[session_id] = session
        while len(sse_sessions) > 1000:
            sse_sessions.pop(next(iter(sse_sessions)), None)
        once = request.query_params.get("once") in ("1", "true")

        async def sse_generator() -> AsyncIterator[str]:
            try:
                yield f"event: endpoint\ndata: /messages?session_id={session_id}\n\n"
                if once:
                    return
                while not await request.is_disconnected():
                    try:
                        msg = await asyncio.wait_for(session.queue.get(), timeout=15.0)
                    except TimeoutError:
                        yield ": ping\n\n"
                        continue
                    if msg is None:
                        break
                    yield f"event: message\ndata: {msg}\n\n"
            finally:
                sse_sessions.pop(session_id, None)

        return StreamingResponse(
            sse_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @app.post("/messages")
    async def handle_sse_messages(request: Request) -> Response:
        if (origin_err := _validate_origin(request, cfg)) is not None:
            return origin_err
        if (ct_err := _validate_json_content_type(request)) is not None:
            return ct_err

        session_id = request.query_params.get("session_id") or request.headers.get(
            "mcp-session-id"
        )
        session = sse_sessions.get(session_id) if session_id else None
        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()
        if session is not None and session.token is not None and incoming_token != session.token:
            return _unauthorized_response(
                "Unauthorized: missing or mismatched bearer token for SSE session"
            )

        raw_body = await request.body()
        try:
            payload = json.loads(raw_body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            err = GitSeedMCPServer._error_response(
                None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}"
            )
            if session is not None:
                await session.queue.put(json.dumps(err))
            return JSONResponse(status_code=400, content=err)

        response_data = await mcp_server.handle_payload(
            payload,
            request_token=incoming_token,
            allow_env_fallback=False,
        )
        if response_data is None:
            return Response(status_code=202)

        if session is not None:
            if isinstance(response_data, list):
                for item in response_data:
                    await session.queue.put(json.dumps(item))
            else:
                await session.queue.put(json.dumps(response_data))

        return JSONResponse(status_code=202, content=response_data)

    return app


# ── Stdio Transport Runner ────────────────────────────────────────────


async def run_stdio_async(
    server: GitSeedMCPServer,
    *,
    input_stream: TextIO | None = None,
    output_stream: TextIO | None = None,
) -> None:
    """Process newline-delimited JSON-RPC 2.0 messages over stdin/stdout."""
    reader = input_stream or sys.stdin
    writer = output_stream or sys.stdout

    while True:
        line = await asyncio.to_thread(reader.readline)
        if not line:
            break
        stripped = line.strip()
        if not stripped:
            continue

        try:
            payload = json.loads(stripped)
        except json.JSONDecodeError as exc:
            response: dict[str, Any] | list[dict[str, Any]] | None = (
                GitSeedMCPServer._error_response(
                    None, JSONRPC_PARSE_ERROR, f"Parse error: {exc}"
                )
            )
        else:
            response = await server.handle_payload(payload, allow_env_fallback=True)

        if response is not None:
            writer.write(json.dumps(response, separators=(",", ":"), default=str) + "\n")
            writer.flush()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gitseed-mcp",
        description="GitSeed Model Context Protocol (MCP) Server",
    )
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="Run MCP server over stdin/stdout instead of HTTP/SSE.",
    )
    parser.add_argument(
        "--transport",
        choices=["stdio", "streamable-http", "sse"],
        default=None,
        help="MCP transport mode (stdio, streamable-http, or sse).",
    )
    parser.add_argument(
        "--host",
        type=str,
        default="127.0.0.1",
        help="HTTP bind host (defaults to 127.0.0.1).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="HTTP bind port (defaults to MCP_PORT env or 8422).",
    )
    parser.add_argument(
        "--api-url",
        type=str,
        default=None,
        help="GitSeed Control Plane API URL (defaults to GITSEED_API_URL or RUNNER_API_URL).",
    )
    parser.add_argument(
        "--token",
        type=str,
        default=None,
        help="Default GitSeed Personal Access Token (gsp_...) for stdio transport.",
    )
    return parser


def cli(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    base_settings = get_settings()

    overrides: dict[str, Any] = {}
    if args.host is not None:
        overrides["HOST"] = args.host
    if args.port is not None:
        overrides["MCP_PORT"] = args.port
    if args.api_url is not None:
        overrides["GITSEED_API_URL"] = args.api_url
    if args.token is not None:
        overrides["GITSEED_API_TOKEN"] = args.token

    settings = base_settings.model_copy(update=overrides) if overrides else base_settings

    if args.stdio or args.transport == "stdio":
        server = GitSeedMCPServer(settings=settings)
        asyncio.run(run_stdio_async(server))
        return

    app = create_app(settings=settings)
    uvicorn.run(
        app,
        host=settings.HOST,
        port=settings.MCP_PORT,
        log_level="info",
    )


app = create_app()


if __name__ == "__main__":
    cli()
