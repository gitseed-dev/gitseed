"""Detect which agent CLI runtimes are installed in the configured runner image.

The web UI uses this to mark runtimes that aren't installed (e.g. bring-your-own `agy` / `grok`,
or `claude` in published images built with `INSTALL_CLAUDE_CODE=false`) *before* an agent is
created, instead of the user discovering it from a failed run.

Resolution order:
  1. `RUNNER_RUNTIMES` setting (comma-separated runtime ids), an explicit declaration that
     works for any compute provider, including Kubernetes.
  2. Docker compute backend: probe the runner image once by running a short-lived,
     network-less container that checks `command -v` for each CLI binary. Results are cached
     per image ID, so rebuilding or retagging the image triggers a fresh probe.
  3. Otherwise availability is reported as unknown (`available: null`) and the UI shows no badges.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any
from urllib.parse import quote

from gitseed_common.enums import AgentRuntime, BackendHealth, BackendType
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.services.compute_runtime import _demux_docker_logs, _docker_client_for_backend

logger = logging.getLogger(__name__)

# Runtime id -> candidate binaries (any one present means the runtime is usable).
RUNTIME_BINARIES: dict[str, tuple[str, ...]] = {
    AgentRuntime.OPENCODE.value: ("opencode",),
    AgentRuntime.CLAUDE_CODE.value: ("claude",),
    AgentRuntime.CODEX.value: ("codex",),
    AgentRuntime.ANTIGRAVITY.value: ("agy",),
    AgentRuntime.GEMINI.value: ("gemini",),
    AgentRuntime.GROK_BUILD.value: ("grok",),
}

_PROBE_TIMEOUT_SECONDS = 30.0
_NEGATIVE_CACHE_TTL_SECONDS = 60.0

# image_id -> {runtime: bool}
_probe_cache: dict[str, dict[str, bool]] = {}
# image ref -> monotonic timestamp of the last failed probe (avoid hammering Docker)
_probe_failures: dict[str, float] = {}
_probe_lock = asyncio.Lock()


def _all_binaries() -> list[str]:
    seen: list[str] = []
    for bins in RUNTIME_BINARIES.values():
        for b in bins:
            if b not in seen:
                seen.append(b)
    return seen


def _availability_from_binaries(found: set[str]) -> dict[str, bool]:
    return {rt: any(b in found for b in bins) for rt, bins in RUNTIME_BINARIES.items()}


def parse_runtime_list(raw: str) -> dict[str, bool] | None:
    """Parse a comma-separated `RUNNER_RUNTIMES` value into an availability map."""
    declared = {part.strip().lower() for part in raw.split(",") if part.strip()}
    if not declared:
        return None
    return {rt: rt in declared for rt in RUNTIME_BINARIES}


def _probe_script() -> str:
    checks = " ".join(_all_binaries())
    return f'for b in {checks}; do command -v "$b" >/dev/null 2>&1 && echo "$b"; done; exit 0'


async def _select_compute_backend(session: AsyncSession) -> Backend | None:
    backends = (
        await session.execute(
            select(Backend)
            .where(
                Backend.type == BackendType.COMPUTE,
                col(Backend.enabled).is_(True),
            )
            .order_by(col(Backend.created_at).asc())
        )
    ).scalars().all()
    healthy = [b for b in backends if b.health != BackendHealth.UNREACHABLE]
    for b in healthy:
        if b.provider.lower() == "docker":
            return b
    if healthy:
        return healthy[0]
    return backends[0] if backends else None


async def _probe_docker_image(backend: Backend, image: str) -> dict[str, bool] | None:
    """Run the runner image with a tiny `command -v` script and return runtime availability."""
    client, base_url = _docker_client_for_backend(backend)
    async with client:
        inspect = await client.get(f"{base_url}/images/{quote(image, safe='')}/json")
        if inspect.status_code != 200:
            logger.info("Runner image %s not present on Docker backend; skipping probe", image)
            return None
        image_id = str(inspect.json().get("Id") or image)
        if image_id in _probe_cache:
            return _probe_cache[image_id]

        create = await client.post(
            f"{base_url}/containers/create",
            json={
                "Image": image,
                "Entrypoint": ["sh", "-c"],
                "Cmd": [_probe_script()],
                "User": "runner",
                "Labels": {"app.gitseed.managed": "true", "app.gitseed.purpose": "runtime-probe"},
                "HostConfig": {
                    "NetworkMode": "none",
                    "AutoRemove": False,
                    "Privileged": False,
                    "CapDrop": ["ALL"],
                    "SecurityOpt": ["no-new-privileges:true"],
                },
            },
        )
        if create.status_code not in (200, 201):
            logger.warning("Runtime probe create failed (HTTP %s)", create.status_code)
            return None
        container_id = str(create.json().get("Id") or "")
        cid = quote(container_id, safe="")
        try:
            start = await client.post(f"{base_url}/containers/{cid}/start")
            if start.status_code not in (200, 204, 304):
                return None
            await client.post(
                f"{base_url}/containers/{cid}/wait", timeout=_PROBE_TIMEOUT_SECONDS
            )
            logs = await client.get(f"{base_url}/containers/{cid}/logs?stdout=true&stderr=false")
            found = {
                line.strip()
                for line in _demux_docker_logs(logs.content).splitlines()
                if line.strip()
            }
        finally:
            await client.delete(f"{base_url}/containers/{cid}?force=true")

    result = _availability_from_binaries(found)
    _probe_cache[image_id] = result
    return result


async def get_runtime_availability(session: AsyncSession) -> dict[str, Any]:
    """Return `{"available": {runtime: bool} | None, "source": str, "image": str | None}`."""
    declared = parse_runtime_list(settings.RUNNER_RUNTIMES or "")
    if declared is not None:
        return {"available": declared, "source": "config", "image": None}

    backend = await _select_compute_backend(session)
    if backend is None or backend.provider.lower() != "docker":
        return {"available": None, "source": "unknown", "image": None}

    cfg = backend.config or {}
    image = str(cfg.get("runner_image") or settings.RUNNER_IMAGE).strip()

    failed_at = _probe_failures.get(image)
    if failed_at is not None and time.monotonic() - failed_at < _NEGATIVE_CACHE_TTL_SECONDS:
        return {"available": None, "source": "unknown", "image": image}

    async with _probe_lock:
        try:
            available = await _probe_docker_image(backend, image)
        except Exception as exc:  # Docker unreachable, timeout, etc. Never break the agent editor.
            logger.warning("Runner runtime probe failed for %s: %s", image, exc)
            available = None
    if available is None:
        _probe_failures[image] = time.monotonic()
        return {"available": None, "source": "unknown", "image": image}
    _probe_failures.pop(image, None)
    return {"available": available, "source": "probe", "image": image}


def clear_runtime_probe_cache() -> None:
    """Reset cached probe results (used by tests)."""
    _probe_cache.clear()
    _probe_failures.clear()

