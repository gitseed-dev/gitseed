from __future__ import annotations

import argparse
import asyncio
import json
import secrets
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
from starlette.middleware.cors import CORSMiddleware

from gitseed_mcp.client import GitSeedApiError
from gitseed_mcp.config import MCPSettings, get_settings
from gitseed_mcp.server import (
    JSONRPC_PARSE_ERROR,
    PROMPTS_CATALOG,
    RESOURCE_TEMPLATES_CATALOG,
    RESOURCES_CATALOG,
    SUPPORTED_PROTOCOL_VERSIONS,
    GitSeedMCPServer,
)


@dataclass
class SSESession:
    session_id: str
    token: str | None = None
    queue: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)

    @property
    def bound_token(self) -> str | None:
        return self.token


def extract_request_token(request: Request) -> str | None:
    """Extract a caller API token from Authorization Bearer or X-API-Key headers."""
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


def _forbidden_session_response(
    detail: str = "Forbidden: mismatched bearer token for MCP session",
) -> JSONResponse:
    return JSONResponse(
        status_code=403,
        content={"error": detail},
    )


def _tokens_match(token_a: str | None, token_b: str | None) -> bool:
    """Compare bearer tokens in constant time; returns False if only one is set."""
    if token_a is None and token_b is None:
        return True
    if token_a is None or token_b is None:
        return False
    return secrets.compare_digest(
        token_a.encode("utf-8"),
        token_b.encode("utf-8"),
    )


def _signal_sse_close(sess: SSESession | None) -> None:
    """Push None sentinel onto an SSESession queue so its generator exits cleanly."""
    if sess is None:
        return
    try:
        sess.queue.put_nowait(None)
    except asyncio.QueueFull:
        pass


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
    allowed_origins = list(cfg.allowed_origins)
    if allowed_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=allowed_origins,
            allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
            allow_headers=[
                "Authorization",
                "Content-Type",
                "Accept",
                "Mcp-Session-Id",
                "Last-Event-ID",
                "X-API-Key",
            ],
            expose_headers=["Mcp-Session-Id"],
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
                "tools": len(mcp_server.visible_tools()),
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
        existing_sse = sse_sessions.get(session_id) if session_id else None

        if session_state is not None:
            cached_token = session_state.get("token")
            if not _tokens_match(cached_token, incoming_token):
                # Never allow an unauthenticated or differently-authenticated caller
                # to inherit or overwrite another user's authenticated session.
                session_id = uuid.uuid4().hex
                session_state = None
        elif existing_sse is not None and not _tokens_match(
            existing_sse.bound_token, incoming_token
        ):
            session_id = uuid.uuid4().hex
            session_state = None

        if not session_id:
            session_id = uuid.uuid4().hex

        if session_state is None:
            session_state = {"token": incoming_token, "queue": None}
            streamable_sessions[session_id] = session_state
            while len(streamable_sessions) > 1000:
                evicted_state = streamable_sessions.pop(
                    next(iter(streamable_sessions)), None
                )
                if isinstance(evicted_state, dict):
                    evicted_q = evicted_state.get("queue")
                    if isinstance(evicted_q, asyncio.Queue):
                        try:
                            evicted_q.put_nowait(None)
                        except asyncio.QueueFull:
                            pass

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

        return JSONResponse(content=response_data, headers=headers)

    @app.get("/mcp")
    async def handle_streamable_http_get(request: Request) -> Response:
        if (origin_err := _validate_origin(request, cfg)) is not None:
            return origin_err
        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()

        raw_session_id = request.headers.get("mcp-session-id")
        if raw_session_id:
            existing_sse = sse_sessions.get(raw_session_id)
            if existing_sse is not None and not _tokens_match(
                existing_sse.bound_token, incoming_token
            ):
                return _forbidden_session_response()
            existing_state_check = streamable_sessions.get(raw_session_id)
            if isinstance(existing_state_check, dict) and not _tokens_match(
                existing_state_check.get("token"), incoming_token
            ):
                return _forbidden_session_response()

        mcp_session_id = raw_session_id or uuid.uuid4().hex
        accept = request.headers.get("accept", "")
        if "text/event-stream" in accept:
            existing_state = streamable_sessions.get(mcp_session_id)
            prev_sse = sse_sessions.get(mcp_session_id)
            if prev_sse is not None:
                _signal_sse_close(prev_sse)
            if isinstance(existing_state, dict):
                prev_q = existing_state.get("queue")
                if isinstance(prev_q, asyncio.Queue) and (
                    prev_sse is None or prev_q is not prev_sse.queue
                ):
                    try:
                        prev_q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass

            session = SSESession(
                session_id=mcp_session_id,
                token=incoming_token,
            )
            sse_sessions[mcp_session_id] = session
            while len(sse_sessions) > 1000:
                evicted_sse = sse_sessions.pop(next(iter(sse_sessions)), None)
                if evicted_sse is not session:
                    _signal_sse_close(evicted_sse)

            if existing_state is None:
                streamable_sessions[mcp_session_id] = {
                    "token": incoming_token,
                    "queue": session.queue,
                }
                while len(streamable_sessions) > 1000:
                    evicted_st = streamable_sessions.pop(
                        next(iter(streamable_sessions)), None
                    )
                    if isinstance(evicted_st, dict):
                        evicted_q = evicted_st.get("queue")
                        if (
                            isinstance(evicted_q, asyncio.Queue)
                            and evicted_q is not session.queue
                        ):
                            try:
                                evicted_q.put_nowait(None)
                            except asyncio.QueueFull:
                                pass
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
                    if sse_sessions.get(mcp_session_id) is session:
                        sse_sessions.pop(mcp_session_id, None)
                    st = streamable_sessions.get(mcp_session_id)
                    if isinstance(st, dict) and st.get("queue") is session.queue:
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
            existing_sse = sse_sessions.get(session_id)
            if existing_sse is not None and not _tokens_match(
                existing_sse.bound_token, incoming_token
            ):
                return _forbidden_session_response()
            existing_state = streamable_sessions.get(session_id)
            if isinstance(existing_state, dict) and not _tokens_match(
                existing_state.get("token"), incoming_token
            ):
                return _forbidden_session_response()

            session_state = streamable_sessions.pop(session_id, None)
            if isinstance(session_state, dict):
                q = session_state.get("queue")
                if isinstance(q, asyncio.Queue):
                    try:
                        q.put_nowait(None)
                    except asyncio.QueueFull:
                        pass
            sse_session = sse_sessions.pop(session_id, None)
            _signal_sse_close(sse_session)
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
            evicted_sse = sse_sessions.pop(next(iter(sse_sessions)), None)
            if evicted_sse is not session:
                _signal_sse_close(evicted_sse)
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
                if sse_sessions.get(session_id) is session:
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

        incoming_token = extract_request_token(request)
        if incoming_token is None:
            return _unauthorized_response()

        session_id = request.query_params.get("session_id") or request.headers.get(
            "mcp-session-id"
        )
        session = sse_sessions.get(session_id) if session_id else None
        if not session_id or session is None:
            return JSONResponse(
                {"error": "Unknown or expired SSE session"},
                status_code=404,
            )
        if not _tokens_match(session.bound_token, incoming_token):
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
            await session.queue.put(json.dumps(err))
            return JSONResponse(status_code=400, content=err)

        response_data = await mcp_server.handle_payload(
            payload,
            request_token=incoming_token,
            allow_env_fallback=False,
        )
        if response_data is None:
            return Response(status_code=202)

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
    raw_buffer = getattr(reader, "buffer", None)

    while True:
        if raw_buffer is not None:
            raw_line: bytes = await asyncio.to_thread(raw_buffer.readline)
            line = raw_line.decode("utf-8", errors="replace")
        else:
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
        default=None,
        help="HTTP bind host (defaults to HOST env or 127.0.0.1).",
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
    parser.add_argument(
        "--toolsets",
        type=str,
        default=None,
        help=(
            "Comma-separated toolsets to expose "
            "(core,projects,seeds,tasks,runs,sessions,governance). Defaults to all."
        ),
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        default=False,
        help="Expose only read-only MCP tools and block mutating tool calls.",
    )
    return parser


def cli(argv: list[str] | None = None) -> None:
    args = build_arg_parser().parse_args(argv)
    base_settings = MCPSettings()

    overrides: dict[str, Any] = {}
    if args.host is not None:
        overrides["HOST"] = args.host
    if args.port is not None:
        overrides["MCP_PORT"] = args.port
    if args.api_url is not None:
        overrides["GITSEED_API_URL"] = args.api_url
    if args.token is not None:
        overrides["GITSEED_API_TOKEN"] = args.token
    if args.toolsets is not None:
        overrides["GITSEED_MCP_TOOLSETS"] = args.toolsets
    if args.read_only:
        overrides["GITSEED_MCP_READ_ONLY"] = True

    settings = base_settings.model_copy(update=overrides) if overrides else base_settings

    if args.stdio or args.transport == "stdio":
        server = GitSeedMCPServer(settings=settings)

        async def _run_stdio_with_cleanup() -> None:
            try:
                await run_stdio_async(server)
            finally:
                await server.aclose()

        asyncio.run(_run_stdio_with_cleanup())
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
