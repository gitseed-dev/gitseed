import asyncio
import sys
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager, suppress
from datetime import UTC, datetime

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from gitseed_common.schemas import (
    HealthResponse,
    RootDiscoveryResponse,
    VersionResponse,
)
from sqlalchemy import text

from gitseed_core.api.deps import get_current_user
from gitseed_core.api.errors import register_error_handlers
from gitseed_core.api.router import api_v1_router
from gitseed_core.bootstrap import bootstrap_database
from gitseed_core.config import settings
from gitseed_core.db import AsyncSessionLocal, close_connections, get_redis
from gitseed_core.services.events import run_redis_events_listener
from gitseed_core.services.gateway import sync_all_backends_to_litellm
from gitseed_core.services.scheduler import run_scheduler_forever


async def _startup_sync_litellm() -> None:
    """Push all Inference backends to LiteLLM on Core startup, retrying if LiteLLM is booting."""
    for delay in (0.0, 2.0, 5.0):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            async with AsyncSessionLocal() as session:
                await sync_all_backends_to_litellm(session)
            return
        except Exception:
            continue


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncGenerator[None, None]:
    await bootstrap_database()
    sync_task = asyncio.create_task(_startup_sync_litellm())
    stop_event = asyncio.Event()
    events_listener_task = asyncio.create_task(run_redis_events_listener(stop_event))
    scheduler_task: asyncio.Task[None] | None = None
    if settings.GITSEED_EMBED_SCHEDULER:
        scheduler_task = asyncio.create_task(run_scheduler_forever(stop_event))
    try:
        yield
    finally:
        stop_event.set()
        sync_task.cancel()
        with suppress(asyncio.CancelledError):
            await sync_task
        events_listener_task.cancel()
        with suppress(asyncio.CancelledError):
            await events_listener_task
        if scheduler_task is not None:
            scheduler_task.cancel()
            with suppress(asyncio.CancelledError):
                await scheduler_task
        await close_connections()


app = FastAPI(
    title=settings.APP_NAME,
    version=settings.GITSEED_VERSION,
    description="Your SDLC, run by agents, governed by you.",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.CORS_ORIGINS.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_v1_router)
register_error_handlers(app)


@app.get("/", tags=["Discovery"], dependencies=[Depends(get_current_user)])
async def root() -> RootDiscoveryResponse:
    """Root discovery endpoint listing available API paths."""
    paths = {
        route.path
        for route in app.routes
        if hasattr(route, "path") and route.path not in {"/", "/docs/oauth2-redirect"}
    }
    return RootDiscoveryResponse(paths=sorted(paths))


@app.get("/healthz", tags=["Health"])
async def health_check() -> HealthResponse:
    """Standard health check endpoint for container orchestrators and status monitoring."""
    db_status = "connected"
    redis_status = "connected"

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
    except Exception:
        db_status = "unreachable"

    try:
        redis = get_redis()
        ping_res = redis.ping()
        if hasattr(ping_res, "__await__"):
            await ping_res
    except Exception:
        redis_status = "unreachable"

    overall_status = (
        "ok" if db_status == "connected" and redis_status == "connected" else "degraded"
    )
    return HealthResponse(
        status=overall_status,
        service=settings.APP_NAME,
        version=settings.GITSEED_VERSION,
        environment=settings.ENVIRONMENT,
        database=db_status,
        redis=redis_status,
        timestamp=datetime.now(UTC).isoformat(),
    )


@app.get("/version", tags=["Discovery"])
async def server_version() -> VersionResponse:
    """Control plane version discovery endpoint."""
    return VersionResponse(
        version=settings.GITSEED_VERSION,
        platform=sys.platform,
    )


async def _run_scheduler_main() -> None:
    await bootstrap_database()
    try:
        await run_scheduler_forever()
    finally:
        await close_connections()


async def _run_egress_proxy_main() -> None:
    from gitseed_core.services.egress_proxy import run_egress_proxy_forever

    await run_egress_proxy_forever()


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "egress-proxy":
        asyncio.run(_run_egress_proxy_main())
    else:
        asyncio.run(_run_scheduler_main())
