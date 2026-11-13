"""On-demand VS Code Web (`openvscode-server`) runtime & HTTP/WebSocket proxy for sessions.

Design:
- Every session container mounts a shared read-only volume (`gitseed-vscode-bundle:/opt/.gitseed-vscode:ro`)
  alongside its per-session workspace volume (`gitseed-ws-<session_id>:/workspace`).
- Sessions that never click "Open in VS Code" pay zero RAM/CPU cost.
- When requested:
  1. Verifies the target container has GitSeed's managed session labels before any `docker exec`.
  2. Ensures the shared `gitseed-vscode-bundle` volume is populated from `gitpod/openvscode-server:latest`
     matching the container's architecture (`amd64` / `arm64`).
  3. Raises the session container's memory limit by +1 GB (to at least 4 GB) via `POST /containers/{id}/update`
     so VS Code + language servers fit comfortably.
  4. Starts `openvscode-server` inside the session container via `docker exec` with its writable state in
     `/tmp/.gitseed-vscode` (never inside `/workspace`), bound to `127.0.0.1:31337` and gated by a
     per-session connection token.
  5. If the session container is Alpine/musl and cannot execute the bundled glibc Node runtime, falls back
     to a companion container on the same isolated network mounting `gitseed-ws-<session_id>:/workspace`,
     and returns a notice explaining that language servers run outside the project image.
- All HTTP and WebSocket traffic through the proxy updates the session's activity timestamp so typing in
  VS Code resets the idle timer, and closing/stopping the session immediately revokes the token.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import logging
import re
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit

import httpx
from fastapi import Request, Response, WebSocket, WebSocketDisconnect
from gitseed_common.enums import BackendType

from gitseed_core.config import settings
from gitseed_core.db import get_redis
from gitseed_core.models.backend import Backend
from gitseed_core.services import session_workspace
from gitseed_core.services.compute_runtime import (
    _docker_client_for_backend,
    _inspect_docker_container,
    _k8s_client_for_backend,
    _split_k8s_ref,
    is_k8s_backend,
    session_workspace_volume_name,
    stop_and_remove_docker_container,
)
from gitseed_core.services.errors import Forbidden, Invalid, NotFound
from gitseed_core.services.session_pty import _open_raw_connection

logger = logging.getLogger("uvicorn.error")

VSCODE_BUNDLE_VOLUME = "gitseed-vscode-bundle"
VSCODE_MOUNT_PATH = "/opt/.gitseed-vscode"
VSCODE_DATA_ROOT = "/tmp/.gitseed-vscode"
VSCODE_INTERNAL_PORT = 31337
DEFAULT_OPENVSCODE_IMAGE = "gitpod/openvscode-server:latest"
MIN_VSCODE_MEMORY_MB = 4096
EXTRA_VSCODE_MEMORY_MB = 1024
MAX_AUTO_BUMP_MEMORY_MB = 8192
MAX_VSCODE_HTTP_BODY_BYTES = 32 * 1024 * 1024


def _trusted_host_header(headers: Any) -> str:
    """Extract request host, honoring `X-Forwarded-Host` only when `TRUST_PROXY_HEADERS` is True."""
    if bool(getattr(settings, "TRUST_PROXY_HEADERS", False)):
        raw = headers.get("x-forwarded-host") or headers.get("host") or ""
    else:
        raw = headers.get("host") or ""
    return str(raw).split(",", 1)[0].strip().lower()


async def _read_bounded_vscode_body(request: Request) -> bytes:
    """Read upstream VS Code proxy HTTP request body with a strict 32 MiB memory cap."""
    raw_cl = request.headers.get("content-length")
    if raw_cl:
        try:
            if int(raw_cl.strip()) > MAX_VSCODE_HTTP_BODY_BYTES:
                raise Invalid(
                    f"VS Code proxy request body exceeds maximum size ({MAX_VSCODE_HTTP_BODY_BYTES} bytes)"
                )
        except ValueError:
            pass
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > MAX_VSCODE_HTTP_BODY_BYTES:
            raise Invalid(
                f"VS Code proxy request body exceeds maximum size ({MAX_VSCODE_HTTP_BODY_BYTES} bytes)"
            )
        chunks.append(chunk)
    return b"".join(chunks)


@dataclass
class VsCodeSessionState:
    session_key: str  # "sandbox:<id>" or "agent:<id>"
    kind: str  # "terminal" or "chat"
    session_id: str
    project_id: str
    backend: Backend
    container_id: str
    token: str
    mode: str  # "in_container" or "sidecar"
    arch: str = "amd64"
    memory_mb: int = 4096
    memory_raised: bool = False
    notice: str | None = None
    sidecar_container_id: str | None = None
    theme: str = "dark"
    direct_ip: str | None = None
    direct_ip_probed: bool = False
    on_activity: Callable[[], Awaitable[None]] | None = None
    last_activity_mono: float = field(default_factory=time.monotonic)


_VSCODE_SESSIONS: dict[str, VsCodeSessionState] = {}
_BUNDLE_INIT_LOCKS: dict[str, asyncio.Lock] = {}
_DIRECT_VSCODE_HTTP_CLIENT: httpx.AsyncClient | None = None
_VSCODE_REDIS_PREFIX = "vscode_session:"
VSCODE_COOKIE_NAME = "gitseed_vscode"
_STRIPPED_UPSTREAM_REQUEST_HEADERS = frozenset(
    {
        "host",
        "connection",
        "transfer-encoding",
        "content-length",
        "upgrade",
        "cookie",
        "authorization",
        "x-api-key",
        "proxy-authorization",
    }
)
_STRIPPED_UPSTREAM_RESPONSE_HEADERS = frozenset(
    {
        "connection",
        "content-length",
        "set-cookie",
    }
)


def enforce_vscode_production_origin() -> None:
    """Require `SANDBOX_ORIGIN` in production unless `ALLOW_SAME_ORIGIN_VSCODE` is enabled."""
    if (
        settings.ENVIRONMENT.strip().lower() == "production"
        and not (getattr(settings, "SANDBOX_ORIGIN", "") or "").strip()
        and not bool(getattr(settings, "ALLOW_SAME_ORIGIN_VSCODE", False))
    ):
        raise Invalid(
            "SANDBOX_ORIGIN must be configured in production to isolate Embedded VS Code"
        )


def build_vscode_launch_url(
    session_key: str,
    token: str,
    *,
    query: str = "folder=/workspace",
) -> str:
    """Build the authenticated VS Code launch URL, prepending `SANDBOX_ORIGIN` when configured."""
    base_path = f"/api/v1/vscode/{session_key}/{token}"
    origin = (getattr(settings, "SANDBOX_ORIGIN", "") or "").strip().rstrip("/")
    prefix = f"{origin}{base_path}" if origin else base_path
    return f"{prefix}/?{query}" if query else f"{prefix}/"


def _split_netloc_host_port(netloc: str, default_scheme: str = "http") -> tuple[str, str]:
    parsed = urlsplit(f"{default_scheme}://{netloc}")
    host = (parsed.hostname or "").strip().lower()
    port = str(parsed.port) if parsed.port else ("443" if default_scheme == "https" else "80")
    return host, port


def _enforce_sandbox_origin(headers: Any) -> None:
    """When `settings.SANDBOX_ORIGIN` is configured, require requests to match that origin/host."""
    enforce_vscode_production_origin()
    sandbox_origin_raw = (getattr(settings, "SANDBOX_ORIGIN", "") or "").strip()
    if not sandbox_origin_raw:
        return
    parsed = urlsplit(sandbox_origin_raw)
    expected_scheme = (parsed.scheme or "http").lower()
    expected_netloc = (parsed.netloc or parsed.path).strip().rstrip("/").lower()
    expected_origin = f"{expected_scheme}://{expected_netloc}"
    expected_host, expected_port = _split_netloc_host_port(expected_netloc, expected_scheme)
    is_loopback_sandbox_default = expected_host in ("127.0.0.1", "localhost")
    web_port = str(getattr(settings, "WEB_PORT", 8421) or 8421)

    host_hdr = _trusted_host_header(headers)
    req_host, req_port = _split_netloc_host_port(host_hdr, expected_scheme) if host_hdr else ("", "")

    if is_loopback_sandbox_default and expected_port != web_port:
        # Allow LAN/Tailscale access on the dedicated sandbox port (e.g. 192.168.x.x:8423)
        # while strictly rejecting the main authenticated Web UI port (e.g. :8421).
        if not req_host or req_port != expected_port:
            raise Forbidden("VS Code proxy request host does not match configured SANDBOX_ORIGIN")
        origin_hdr = (headers.get("origin") or "").strip().rstrip("/").lower()
        if origin_hdr:
            orig_parsed = urlsplit(origin_hdr)
            orig_host = (orig_parsed.hostname or "").strip().lower()
            orig_port = str(orig_parsed.port) if orig_parsed.port else (
                "443" if orig_parsed.scheme == "https" else "80"
            )
            if orig_port != expected_port or (
                orig_host != req_host
                and not ({orig_host, req_host} <= {"127.0.0.1", "localhost"})
            ):
                raise Forbidden(
                    "VS Code proxy request origin does not match configured SANDBOX_ORIGIN"
                )
        return

    origin_hdr = (headers.get("origin") or "").strip().rstrip("/").lower()
    if origin_hdr and origin_hdr != expected_origin:
        raise Forbidden("VS Code proxy request origin does not match configured SANDBOX_ORIGIN")

    if host_hdr != expected_netloc:
        raise Forbidden("VS Code proxy request host does not match configured SANDBOX_ORIGIN")


def mint_vscode_token(
    session_key: str,
    container_id: str = "",
    *,
    ttl_seconds: int | None = None,
    now: int | None = None,
) -> str:
    """Mint an expiring, HMAC-authenticated VS Code proxy token (`<exp_hex>.<hmac_hex>`)."""
    effective_ttl = (
        int(ttl_seconds)
        if ttl_seconds is not None and int(ttl_seconds) > 0
        else max(3600, int(getattr(settings, "SESSION_MAX_HOURS", 12) or 12) * 3600)
    )
    cur_ts = int(now if now is not None else time.time())
    exp_ts = cur_ts + effective_ttl
    exp_hex = f"{exp_ts:x}"
    sig_hex = hmac.new(
        settings.GITSEED_SECRET_KEY.encode("utf-8"),
        f"vscode:{session_key}:{container_id}:{exp_hex}".encode(),
        hashlib.sha256,
    ).hexdigest()[:48]
    return f"{exp_hex}.{sig_hex}"


def verify_vscode_token(
    session_key: str,
    container_id: str,
    candidate_token: str,
    *,
    expected_token: str | None = None,
    now: int | None = None,
) -> bool:
    """Verify an expiring `<exp_hex>.<hmac_hex>` VS Code proxy token in constant time."""
    cand = (candidate_token or "").strip()
    if not cand:
        return False
    if expected_token is not None and not secrets.compare_digest(expected_token, cand):
        return False
    if "." in cand:
        exp_hex, sig_hex = cand.split(".", 1)
        if not exp_hex or not sig_hex:
            return False
        try:
            exp_ts = int(exp_hex, 16)
        except ValueError:
            return False
        cur_ts = int(now if now is not None else time.time())
        if cur_ts > exp_ts:
            return False
        expected_sig = hmac.new(
            settings.GITSEED_SECRET_KEY.encode("utf-8"),
            f"vscode:{session_key}:{container_id}:{exp_hex}".encode(),
            hashlib.sha256,
        ).hexdigest()[:48]
        return hmac.compare_digest(expected_sig, sig_hex)
    if expected_token is not None:
        return True
    legacy_mac = hmac.new(
        settings.GITSEED_SECRET_KEY.encode("utf-8"),
        f"vscode:{session_key}:{container_id}".encode(),
        hashlib.sha256,
    ).digest()
    legacy_tok = base64.urlsafe_b64encode(legacy_mac[:24]).rstrip(b"=").decode("ascii")
    return hmac.compare_digest(legacy_tok, cand)


_derive_vscode_token = mint_vscode_token


def derive_vscode_upstream_token(session_key: str, container_id: str) -> str:
    """Derive a per-container connection token gating `openvscode-server` on the bridge network."""
    return hmac.new(
        settings.GITSEED_SECRET_KEY.encode("utf-8"),
        f"vscode-upstream:{session_key}:{container_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:48]


def _append_upstream_vscode_token(target_path: str, upstream_token: str) -> str:
    """Append `tkn=<upstream_token>` to an upstream `openvscode-server` path if not present."""
    if not upstream_token:
        return target_path
    sep = "&" if "?" in target_path else "?"
    if "tkn=" in target_path.split("?", 1)[-1]:
        return target_path
    return f"{target_path}{sep}tkn={quote(upstream_token, safe='')}"


def _build_vscode_frame_headers(
    existing_headers: dict[str, str],
    request_headers: Any | None = None,
) -> dict[str, str]:
    """Build clickjacking & MIME-isolation headers compatible with `SANDBOX_ORIGIN` framing."""
    headers_out: dict[str, str] = {"X-Content-Type-Options": "nosniff"}
    has_csp = any(k.lower() == "content-security-policy" for k in existing_headers)
    sandbox_origin_raw = (getattr(settings, "SANDBOX_ORIGIN", "") or "").strip()
    if not sandbox_origin_raw:
        headers_out["X-Frame-Options"] = "SAMEORIGIN"
        if not has_csp:
            headers_out["Content-Security-Policy"] = "frame-ancestors 'self'"
        return headers_out

    # Cross-origin SANDBOX_ORIGIN mode: omit X-Frame-Options: SAMEORIGIN (which blocks cross-origin
    # iframes from PUBLIC_WEB_URL) and restrict framing via CSP frame-ancestors instead.
    ancestors: list[str] = ["'self'"]
    raw_candidates: list[str] = [
        str(getattr(settings, "PUBLIC_WEB_URL", "") or ""),
        *str(getattr(settings, "CORS_ORIGINS", "") or "").split(","),
        *str(getattr(settings, "ALLOWED_ORIGINS", "") or "").split(","),
    ]
    for raw in raw_candidates:
        cand = raw.strip().rstrip("/")
        if not cand or cand == "*":
            continue
        parsed = urlsplit(cand)
        if not parsed.scheme or not parsed.netloc:
            continue
        origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
        if origin not in ancestors:
            ancestors.append(origin)
        host_only, sep, port = parsed.netloc.lower().partition(":")
        if host_only in ("localhost", "127.0.0.1"):
            alt_host = "127.0.0.1" if host_only == "localhost" else "localhost"
            alt_origin = f"{parsed.scheme.lower()}://{alt_host}{sep}{port}"
            if alt_origin not in ancestors:
                ancestors.append(alt_origin)

    if request_headers is not None:
        sbx_parsed = urlsplit(sandbox_origin_raw)
        sbx_scheme = (sbx_parsed.scheme or "http").lower()
        sbx_host = (sbx_parsed.hostname or "").strip().lower()
        if sbx_host in ("127.0.0.1", "localhost"):
            host_hdr = _trusted_host_header(request_headers)
            req_host, _ = _split_netloc_host_port(host_hdr, sbx_scheme) if host_hdr else ("", "")
            if req_host and req_host not in ("127.0.0.1", "localhost"):
                web_port = int(getattr(settings, "WEB_PORT", 8421) or 8421)
                formatted_host = f"[{req_host}]" if ":" in req_host else req_host
                lan_web_origin = f"{sbx_scheme}://{formatted_host}:{web_port}"
                if lan_web_origin not in ancestors:
                    ancestors.append(lan_web_origin)

    if not has_csp:
        headers_out["Content-Security-Policy"] = f"frame-ancestors {' '.join(ancestors)}"
    return headers_out


async def _persist_vscode_state_redis(state: VsCodeSessionState) -> None:
    """Best-effort persistence of VS Code proxy metadata in Valkey for multi-worker routing."""
    try:
        redis = get_redis()
        payload = json.dumps(
            {
                "session_key": state.session_key,
                "kind": state.kind,
                "session_id": state.session_id,
                "project_id": state.project_id,
                "container_id": state.container_id,
                "token": state.token,
                "mode": state.mode,
                "arch": state.arch,
                "memory_mb": state.memory_mb,
                "memory_raised": state.memory_raised,
                "notice": state.notice,
                "sidecar_container_id": state.sidecar_container_id,
                "theme": state.theme,
                "backend": {
                    "id": state.backend.id,
                    "name": state.backend.name,
                    "provider": state.backend.provider,
                    "endpoint": state.backend.endpoint,
                    "config": state.backend.config or {},
                    "secrets_encrypted": (
                        base64.b64encode(state.backend.secrets_encrypted).decode("ascii")
                        if state.backend.secrets_encrypted
                        else None
                    ),
                },
            }
        )
        ttl_seconds = max(3600, int(getattr(settings, "SESSION_MAX_HOURS", 4)) * 3600)
        await redis.set(f"{_VSCODE_REDIS_PREFIX}{state.session_key}", payload, ex=ttl_seconds)
    except Exception:
        pass


async def _delete_vscode_state_redis(session_key: str) -> None:
    """Best-effort removal of VS Code proxy metadata from Valkey upon session close."""
    try:
        redis = get_redis()
        await redis.delete(f"{_VSCODE_REDIS_PREFIX}{session_key}")
    except Exception:
        pass


def get_vscode_state(session_key: str) -> VsCodeSessionState | None:
    return _VSCODE_SESSIONS.get(session_key)


async def close_session_vscode(session_key: str) -> None:
    """Revoke the session's VS Code token and tear down any companion container."""
    state = _VSCODE_SESSIONS.pop(session_key, None)
    await _delete_vscode_state_redis(session_key)
    if state is None:
        return
    if state.sidecar_container_id:
        try:
            await stop_and_remove_docker_container(state.backend, state.sidecar_container_id)
        except Exception:
            pass


def _verify_managed_session_container(
    info: dict[str, Any] | None,
    *,
    session_id: str,
    kind: str | None = None,
    project_id: str | None = None,
) -> dict[str, str]:
    """Ensure `docker exec` is only ever called on containers labelled as GitSeed sessions."""
    if not info or info.get("_missing"):
        raise Invalid("Session container is not running")
    config = info.get("Config") or {}
    labels = config.get("Labels") if isinstance(config, dict) else {}
    if not isinstance(labels, dict) or labels.get("app.gitseed.managed") != "true":
        raise Forbidden("Refusing to exec into an unmanaged container")
    sbx_id = labels.get("app.gitseed.sandbox_id")
    ag_id = labels.get("app.gitseed.agent_session_id")
    if sbx_id != session_id and ag_id != session_id:
        raise Forbidden("Container session label mismatch")
    if project_id and labels.get("app.gitseed.project_id") not in (None, project_id):
        raise Forbidden("Container project label mismatch")
    return {str(k): str(v) for k, v in labels.items()}


async def _exec_check(
    client: Any,
    base_url: str,
    container_id: str,
    cmd: list[str],
    *,
    user: str | None = None,
    timeout: float = 20.0,
) -> tuple[int, str]:
    """Run a non-interactive command via Docker exec and return `(exit_code, output)`."""
    payload: dict[str, Any] = {
        "AttachStdin": False,
        "AttachStdout": True,
        "AttachStderr": True,
        "Tty": False,
        "Cmd": cmd,
    }
    if user is not None:
        payload["User"] = user
    create_resp = await client.post(
        f"{base_url}/containers/{quote(container_id, safe='')}/exec",
        json=payload,
    )
    if create_resp.status_code not in (200, 201):
        return 1, f"exec create HTTP {create_resp.status_code}"
    exec_id = str(create_resp.json().get("Id") or "")
    if not exec_id:
        return 1, "missing exec id"
    start_resp = await client.post(
        f"{base_url}/exec/{quote(exec_id, safe='')}/start",
        json={"Detach": False, "Tty": False},
        timeout=timeout,
    )
    out = start_resp.content.decode("utf-8", errors="replace") if start_resp.content else ""
    inspect_resp = await client.get(f"{base_url}/exec/{quote(exec_id, safe='')}/json")
    rc = int((inspect_resp.json() or {}).get("ExitCode") or 0) if inspect_resp.status_code == 200 else 1
    return rc, out.strip()


async def _detect_container_arch(
    client: Any,
    base_url: str,
    container_id: str,
) -> str:
    rc, out = await _exec_check(client, base_url, container_id, ["uname", "-m"])
    arch_raw = out.lower().strip() if rc == 0 else ""
    if "aarch64" in arch_raw or "arm64" in arch_raw:
        return "arm64"
    return "amd64"


async def _ensure_vscode_memory_headroom(
    client: Any,
    base_url: str,
    container_id: str,
    info: dict[str, Any],
) -> tuple[int, bool]:
    """Ensure the session container has enough RAM for VS Code + language servers (~0.5-1 GB extra)."""
    host_cfg = info.get("HostConfig") or {}
    current_bytes = int(host_cfg.get("Memory") or 0)
    current_mb = current_bytes // (1024 * 1024) if current_bytes > 0 else 4096
    if current_bytes <= 0:
        return current_mb, False

    target_mb = max(
        MIN_VSCODE_MEMORY_MB,
        min(MAX_AUTO_BUMP_MEMORY_MB, current_mb + EXTRA_VSCODE_MEMORY_MB),
    )
    if target_mb <= current_mb:
        return current_mb, False

    target_bytes = target_mb * 1024 * 1024
    try:
        resp = await client.post(
            f"{base_url}/containers/{quote(container_id, safe='')}/update",
            json={
                "Memory": target_bytes,
                "MemorySwap": target_bytes * 2,
            },
        )
        if resp.status_code in (200, 201):
            return target_mb, True
    except Exception as exc:
        logger.debug("Container memory update skipped for %s: %s", container_id[:12], exc)
    return current_mb, False


async def _ensure_vscode_bundle_populated(
    client: Any,
    base_url: str,
    backend_id: str,
) -> bool:
    """Populate the shared `gitseed-vscode-bundle` Docker volume once from `gitpod/openvscode-server`."""
    lock = _BUNDLE_INIT_LOCKS.setdefault(backend_id, asyncio.Lock())
    async with lock:
        await client.post(
            f"{base_url}/volumes/create",
            json={
                "Name": VSCODE_BUNDLE_VOLUME,
                "Labels": {"app.gitseed.managed": "true", "app.gitseed.vscode_bundle": "true"},
            },
        )
        init_name = "gitseed-vscode-bundle-init"
        await client.delete(f"{base_url}/containers/{quote(init_name, safe='')}?force=true")
        copy_cmd = (
            "if [ -x /opt/.gitseed-vscode/bin/openvscode-server ]; then exit 0; fi; "
            "for src in /home/.openvscode-server /opt/openvscode-server /usr/local/openvscode-server; do "
            "  if [ -x \"$src/bin/openvscode-server\" ]; then "
            "    cp -a \"$src/.\" /opt/.gitseed-vscode/ && chmod -R a+rX /opt/.gitseed-vscode && exit 0; "
            "  fi; "
            "done; exit 1"
        )
        create_payload = {
            "Image": DEFAULT_OPENVSCODE_IMAGE,
            "Entrypoint": ["sh", "-c"],
            "Cmd": [copy_cmd],
            "User": "0",
            "Labels": {"app.gitseed.managed": "true"},
            "HostConfig": {
                "Binds": [f"{VSCODE_BUNDLE_VOLUME}:{VSCODE_MOUNT_PATH}"],
                "Privileged": False,
                "SecurityOpt": ["no-new-privileges:true"],
                "AutoRemove": False,
            },
        }
        create_resp = await client.post(
            f"{base_url}/containers/create?name={quote(init_name, safe='')}",
            json=create_payload,
        )
        if create_resp.status_code == 404:
            pull_resp = await client.post(
                f"{base_url}/images/create",
                params={"fromImage": DEFAULT_OPENVSCODE_IMAGE},
                timeout=240.0,
            )
            if pull_resp.status_code not in (200, 201):
                return False
            create_resp = await client.post(
                f"{base_url}/containers/create?name={quote(init_name, safe='')}",
                json=create_payload,
            )
        if create_resp.status_code not in (200, 201):
            return False
        cid = str(create_resp.json().get("Id") or init_name)
        try:
            start_resp = await client.post(f"{base_url}/containers/{quote(cid, safe='')}/start")
            if start_resp.status_code not in (200, 204, 304):
                return False
            wait_resp = await client.post(
                f"{base_url}/containers/{quote(cid, safe='')}/wait",
                timeout=120.0,
            )
            status_code = int((wait_resp.json() or {}).get("StatusCode") or 1) if wait_resp.status_code == 200 else 1
            return status_code == 0
        finally:
            await client.delete(f"{base_url}/containers/{quote(cid, safe='')}?force=true")


async def _start_sidecar_vscode(
    client: Any,
    base_url: str,
    *,
    session_id: str,
    project_id: str,
    network_name: str,
    token: str,
    base_path: str,
    upstream_token: str = "",
    source_container_id: str | None = None,
    has_ws_volume: bool = True,
) -> str:
    """Fallback for Alpine/musl or pre-volume sessions: run `openvscode-server` in a companion container."""
    sidecar_name = f"gitseed-vscode-{session_id.lower()}"
    ws_vol = session_workspace_volume_name(session_id)
    await client.delete(f"{base_url}/containers/{quote(sidecar_name, safe='')}?force=true")
    server_args = [
        f"{VSCODE_MOUNT_PATH}/bin/openvscode-server",
        "--host",
        "0.0.0.0",
        "--port",
        str(VSCODE_INTERNAL_PORT),
        *(
            ["--connection-token-file", "/tmp/.gitseed-vscode-tkn"]
            if upstream_token
            else ["--without-connection-token"]
        ),
        "--server-base-path",
        base_path,
        "--user-data-dir",
        f"{VSCODE_DATA_ROOT}/data",
        "--extensions-dir",
        f"{VSCODE_DATA_ROOT}/extensions",
        "--default-folder",
        "/workspace",
    ]
    host_cfg: dict[str, Any] = {
        "NetworkMode": network_name,
        "Memory": MIN_VSCODE_MEMORY_MB * 1024 * 1024,
        "PidsLimit": 512,
        "Privileged": False,
        "CapDrop": ["ALL"],
        "SecurityOpt": ["no-new-privileges:true"],
        "AutoRemove": False,
        "Binds": [f"{VSCODE_BUNDLE_VOLUME}:{VSCODE_MOUNT_PATH}:ro"],
    }
    if has_ws_volume:
        host_cfg["Binds"].insert(0, f"{ws_vol}:/workspace")
    elif source_container_id:
        host_cfg["VolumesFrom"] = [source_container_id]
    if upstream_token:
        quoted_args = " ".join(f"'{a.replace(chr(39), chr(39) + chr(92) + chr(39) + chr(39))}'" for a in server_args)
        entrypoint = [
            "sh",
            "-c",
            f"umask 077 && printf '%s' \"$GITSEED_VSCODE_TKN\" > /tmp/.gitseed-vscode-tkn && unset GITSEED_VSCODE_TKN && exec {quoted_args}",
        ]
        env_list = [f"GITSEED_VSCODE_TKN={upstream_token}"]
    else:
        entrypoint = server_args
        env_list = []
    payload: dict[str, Any] = {
        "Image": DEFAULT_OPENVSCODE_IMAGE,
        "Entrypoint": entrypoint,
        "Cmd": [],
        **({"Env": env_list} if env_list else {}),
        "WorkingDir": "/workspace",
        "Labels": {
            "app.gitseed.managed": "true",
            "app.gitseed.vscode_sidecar": "true",
            "app.gitseed.sandbox_id": session_id,
            "app.gitseed.project_id": project_id,
        },
        "HostConfig": host_cfg,
    }
    create_resp = await client.post(
        f"{base_url}/containers/create?name={quote(sidecar_name, safe='')}",
        json=payload,
    )
    if create_resp.status_code == 404:
        pull_resp = await client.post(
            f"{base_url}/images/create",
            params={"fromImage": DEFAULT_OPENVSCODE_IMAGE},
            timeout=240.0,
        )
        if pull_resp.status_code not in (200, 201):
            raise Invalid("Could not pull VS Code server image (`gitpod/openvscode-server:latest`)")
        create_resp = await client.post(
            f"{base_url}/containers/create?name={quote(sidecar_name, safe='')}",
            json=payload,
        )
    if create_resp.status_code not in (200, 201):
        raise Invalid(f"Could not create VS Code companion container (HTTP {create_resp.status_code})")
    sidecar_id = str(create_resp.json().get("Id") or sidecar_name)
    start_resp = await client.post(f"{base_url}/containers/{quote(sidecar_id, safe='')}/start")
    if start_resp.status_code not in (200, 204, 304):
        await client.delete(f"{base_url}/containers/{quote(sidecar_id, safe='')}?force=true")
        raise Invalid("Could not start VS Code companion container")
    return sidecar_id


async def _wait_for_vscode_port(
    client: Any,
    base_url: str,
    target_container_id: str,
) -> bool:
    """Wait up to ~12s for `127.0.0.1:31337` to accept connections inside `target_container_id`."""
    probe_script = (
        f"for i in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20; do "
        f"  if {VSCODE_MOUNT_PATH}/node -e \"const s=require('net').connect({VSCODE_INTERNAL_PORT},'127.0.0.1',()=>{{s.end();process.exit(0)}});s.on('error',()=>process.exit(1))\" 2>/dev/null; then exit 0; fi; "
        f"  sleep 0.4; "
        f"done; exit 1"
    )
    rc, _ = await _exec_check(
        client, base_url, target_container_id, ["sh", "-c", probe_script], timeout=15.0
    )
    return rc == 0


def _vscode_color_theme_name(theme: str | None) -> str:
    return "Default Light Modern" if (theme or "").strip().lower() == "light" else "Default Dark Modern"


def _default_vscode_settings_dict(theme: str | None) -> dict[str, Any]:
    color_theme = _vscode_color_theme_name(theme)
    return {
        "workbench.colorTheme": color_theme,
        "workbench.preferredDarkColorTheme": "Default Dark Modern",
        "workbench.preferredLightColorTheme": "Default Light Modern",
        "git.enabled": True,
        "git.autoRepositoryDetection": True,
        "git.openRepositoryInParentFolders": "always",
        "git.decorations.enabled": True,
        "scm.diffDecorations": "all",
        "scm.alwaysShowRepositories": False,
        "diffEditor.renderSideBySide": True,
        "security.workspace.trust.enabled": False,
        "workbench.startupEditor": "none",
        "workbench.sideBar.location": "left",
        "workbench.secondarySideBar.defaultVisibility": "hidden",
        "window.commandCenter": False,
        "workbench.navigationControl.enabled": False,
        "workbench.layoutControl.enabled": False,
        "chat.commandCenter.enabled": False,
        "chat.disableAIFeatures": True,
        "chat.agent.enabled": False,
    }


def _build_vscode_settings_json(theme: str | None) -> str:
    return json.dumps(_default_vscode_settings_dict(theme), indent=2)


async def _ensure_workspace_git_and_vscode_settings(
    client: Any,
    base_url: str,
    *,
    target_container_id: str,
    session_id: str,
    ref: str = "main",
    head_sha: str = "",
    theme: str | None = None,
) -> None:
    """Ensure `/workspace/.git` exists for diff tracking and sync VS Code theme & Git settings."""
    rc_git, _ = await _exec_check(
        client,
        base_url,
        target_container_id,
        ["sh", "-c", "test -f /workspace/.git/HEAD"],
    )
    if rc_git != 0:
        git_tar = await asyncio.to_thread(
            session_workspace.build_baseline_git_tar,
            session_id,
            ref=ref,
            head_sha=head_sha,
        )
        if git_tar:
            try:
                await client.put(
                    f"{base_url}/containers/{quote(target_container_id, safe='')}/archive",
                    params={"path": "/"},
                    content=git_tar,
                    headers={"Content-Type": "application/x-tar"},
                    timeout=30.0,
                )
            except Exception as exc:
                logger.debug("Could not upload baseline .git tar to %s: %s", target_container_id[:12], exc)

    settings_json = _build_vscode_settings_json(theme)
    settings_escaped = settings_json.replace("'", "'\"'\"'")
    setup_script = (
        f"mkdir -p {VSCODE_DATA_ROOT}/data/Machine {VSCODE_DATA_ROOT}/data/User && "
        f"printf '%s\\n' '{settings_escaped}' > {VSCODE_DATA_ROOT}/data/Machine/settings.json && "
        f"printf '%s\\n' '{settings_escaped}' > {VSCODE_DATA_ROOT}/data/User/settings.json && "
        "git config --global --add safe.directory /workspace 2>/dev/null || true; "
        "git config --global --add safe.directory '*' 2>/dev/null || true; "
        "if [ -d /workspace/.git ]; then "
        "  chmod -R a+rwX /workspace/.git 2>/dev/null || true; "
        "  (cd /workspace && git update-index -q --refresh 2>/dev/null || true); "
        "fi"
    )
    await _exec_check(
        client,
        base_url,
        target_container_id,
        ["sh", "-c", setup_script],
        user="0",
    )


async def ensure_session_vscode(
    *,
    session_key: str | None = None,
    kind: str,
    session_id: str,
    project_id: str,
    backend: Backend,
    container_id: str,
    ref: str = "main",
    head_sha: str = "",
    theme: str | None = None,
    on_activity: Callable[[], Awaitable[None]] | None = None,
) -> dict[str, Any]:
    """Start `openvscode-server` on demand for a session and return its authenticated launch URL."""
    enforce_vscode_production_origin()
    if is_k8s_backend(backend):
        raise Invalid("Embedded VS Code currently requires a Docker compute backend")
    resolved_key = session_key or f"{'agent' if kind == 'chat' else 'sandbox'}:{session_id}"
    resolved_theme = "light" if (theme or "").strip().lower() == "light" else "dark"
    info = await _inspect_docker_container(backend, container_id)
    _verify_managed_session_container(
        info, session_id=session_id, kind=kind, project_id=project_id
    )
    state_dict = (info or {}).get("State") or {}
    if not state_dict.get("Running"):
        raise Invalid("Session container is not running; resume or start the session first")

    existing = _VSCODE_SESSIONS.get(resolved_key)
    if (
        existing is not None
        and existing.container_id == container_id
        and verify_vscode_token(
            resolved_key, existing.container_id, existing.token, expected_token=existing.token
        )
    ):
        target_cid = existing.sidecar_container_id or existing.container_id
        t_info = await _inspect_docker_container(backend, target_cid)
        if t_info and ((t_info.get("State") or {}).get("Running")):
            existing.theme = resolved_theme
            client, base_url = _docker_client_for_backend(backend)
            async with client:
                await _ensure_workspace_git_and_vscode_settings(
                    client,
                    base_url,
                    target_container_id=target_cid,
                    session_id=session_id,
                    ref=ref,
                    head_sha=head_sha,
                    theme=resolved_theme,
                )
            return {
                "url": build_vscode_launch_url(resolved_key, existing.token),
                "mode": existing.mode,
                "arch": existing.arch,
                "memory_mb": existing.memory_mb,
                "memory_raised": existing.memory_raised,
                "notice": existing.notice,
            }

    token = mint_vscode_token(resolved_key, container_id)
    upstream_token = derive_vscode_upstream_token(resolved_key, container_id)
    base_path = f"/api/v1/vscode/{resolved_key}/{token}"

    client, base_url = _docker_client_for_backend(backend)
    async with client:
        arch = await _detect_container_arch(client, base_url, container_id)
        memory_mb, memory_raised = await _ensure_vscode_memory_headroom(
            client, base_url, container_id, info or {}
        )

        # Ensure the shared VS Code bundle volume is populated on this Docker host
        rc_has_bin, _ = await _exec_check(
            client,
            base_url,
            container_id,
            ["sh", "-c", f"test -x {VSCODE_MOUNT_PATH}/bin/openvscode-server"],
        )
        if rc_has_bin != 0:
            await _ensure_vscode_bundle_populated(client, base_url, backend.id)

        # Test whether the bundled Node binary can execute inside the session container
        # (glibc images succeed; Alpine/musl images without gcompat fall back to sidecar)
        rc_compat, _ = await _exec_check(
            client,
            base_url,
            container_id,
            ["sh", "-c", f"{VSCODE_MOUNT_PATH}/bin/openvscode-server --version >/dev/null 2>&1"],
        )

        mode = "in_container"
        notice: str | None = None
        sidecar_cid: str | None = None

        if rc_compat == 0:
            await _ensure_workspace_git_and_vscode_settings(
                client,
                base_url,
                target_container_id=container_id,
                session_id=session_id,
                ref=ref,
                head_sha=head_sha,
                theme=resolved_theme,
            )
            # Start openvscode-server inside the session container gated by a per-session
            # --connection-token-file so neighboring containers on shared Docker bridge networks
            # cannot connect to port 31337 without our reverse proxy's upstream token.
            kill_existing_js = (
                f"{VSCODE_MOUNT_PATH}/node -e '"
                "const fs=require(\"fs\");"
                "for(const p of fs.readdirSync(\"/proc\")){"
                "  if(!/^[0-9]+$/.test(p)||Number(p)===process.pid)continue;"
                "  try{const c=fs.readFileSync(\"/proc/\"+p+\"/cmdline\",\"utf8\");if(c.includes(\"openvscode-server\"))process.kill(Number(p),\"SIGKILL\")}catch(e){}"
                "}' 2>/dev/null || true; "
                "sleep 0.3; "
            )
            launch_cmd = (
                kill_existing_js
                + f"mkdir -p {VSCODE_DATA_ROOT}/data {VSCODE_DATA_ROOT}/extensions && "
                f"(umask 077 && printf '%s' '{upstream_token}' > {VSCODE_DATA_ROOT}/conn.token) && "
                f"if [ -d {VSCODE_MOUNT_PATH}/bundled-extensions ]; then "
                f"  cp -rn {VSCODE_MOUNT_PATH}/bundled-extensions/* {VSCODE_DATA_ROOT}/extensions/ 2>/dev/null || true; "
                f"fi && "
                f"nohup {VSCODE_MOUNT_PATH}/bin/openvscode-server "
                f"--host 0.0.0.0 "
                f"--port {VSCODE_INTERNAL_PORT} "
                f"--connection-token-file {VSCODE_DATA_ROOT}/conn.token "
                f"--server-base-path {base_path} "
                f"--user-data-dir {VSCODE_DATA_ROOT}/data "
                f"--extensions-dir {VSCODE_DATA_ROOT}/extensions "
                f"--default-folder /workspace "
                f">{VSCODE_DATA_ROOT}/server.log 2>&1 &"
            )
            exec_create = await client.post(
                f"{base_url}/containers/{quote(container_id, safe='')}/exec",
                json={
                    "AttachStdin": False,
                    "AttachStdout": False,
                    "AttachStderr": False,
                    "Tty": False,
                    "WorkingDir": "/workspace",
                    "Cmd": ["sh", "-c", launch_cmd],
                },
            )
            if exec_create.status_code in (200, 201):
                eid = str(exec_create.json().get("Id") or "")
                if eid:
                    await client.post(
                        f"{base_url}/exec/{quote(eid, safe='')}/start",
                        json={"Detach": True, "Tty": False},
                    )
            ready = await _wait_for_vscode_port(client, base_url, container_id)
            if not ready:
                rc_compat = 1  # Fall back to sidecar if in-container startup failed

        if rc_compat != 0:
            mode = "sidecar"
            host_cfg = (info or {}).get("HostConfig") or {}
            network_name = str(host_cfg.get("NetworkMode") or "bridge")
            binds = host_cfg.get("Binds") or []
            ws_vol = session_workspace_volume_name(session_id)
            has_ws_vol = any(isinstance(b, str) and b.startswith(f"{ws_vol}:") for b in binds)
            if not has_ws_vol:
                # Ensure the workspace volume exists and seed it from the running container's /workspace
                await client.post(
                    f"{base_url}/volumes/create",
                    json={
                        "Name": ws_vol,
                        "Labels": {
                            "app.gitseed.managed": "true",
                            "app.gitseed.session_id": session_id,
                            "app.gitseed.project_id": project_id,
                        },
                    },
                )
            sidecar_cid = await _start_sidecar_vscode(
                client,
                base_url,
                session_id=session_id,
                project_id=project_id,
                network_name=network_name,
                token=token,
                base_path=base_path,
                upstream_token=upstream_token,
                source_container_id=container_id,
                has_ws_volume=True,
            )
            if not has_ws_vol:
                # Copy existing /workspace files from the pre-volume session container into the sidecar's /workspace
                arch_resp = await client.get(
                    f"{base_url}/containers/{quote(container_id, safe='')}/archive",
                    params={"path": "/workspace"},
                    timeout=60.0,
                )
                if arch_resp.status_code == 200 and arch_resp.content:
                    await client.put(
                        f"{base_url}/containers/{quote(sidecar_cid, safe='')}/archive",
                        params={"path": "/"},
                        content=arch_resp.content,
                        headers={"Content-Type": "application/x-tar"},
                        timeout=60.0,
                    )
            await _ensure_workspace_git_and_vscode_settings(
                client,
                base_url,
                target_container_id=sidecar_cid,
                session_id=session_id,
                ref=ref,
                head_sha=head_sha,
                theme=resolved_theme,
            )
            await _wait_for_vscode_port(client, base_url, sidecar_cid)
            notice = (
                "This session image uses musl/Alpine or was started before the shared VS Code mount, so VS Code "
                "is running in a companion container sharing /workspace. Language servers run outside "
                "the project image."
            )

    state = VsCodeSessionState(
        session_key=resolved_key,
        kind=kind,
        session_id=session_id,
        project_id=project_id,
        backend=backend,
        container_id=container_id,
        token=token,
        mode=mode,
        arch=arch,
        memory_mb=memory_mb,
        memory_raised=memory_raised,
        notice=notice,
        sidecar_container_id=sidecar_cid,
        theme=resolved_theme,
        on_activity=on_activity,
    )
    _VSCODE_SESSIONS[resolved_key] = state
    await _persist_vscode_state_redis(state)
    return {
        "url": build_vscode_launch_url(resolved_key, token),
        "mode": mode,
        "arch": arch,
        "memory_mb": memory_mb,
        "memory_raised": memory_raised,
        "notice": notice,
    }


async def _touch_vscode_activity(state: VsCodeSessionState) -> None:
    now = time.monotonic()
    if state.on_activity and now - state.last_activity_mono > 30.0:
        state.last_activity_mono = now
        try:
            await state.on_activity()
        except Exception:
            pass


async def open_container_tcp_tunnel(
    backend: Backend,
    container_id: str,
    port: int = VSCODE_INTERNAL_PORT,
) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
    """Open a clean, binary-safe bidirectional stream to `127.0.0.1:<port>` inside `container_id`.

    Why not `Tty: true` + `stty raw -echo`?
    Before `sh -c "stty raw -echo ..."` executes inside the container, the kernel PTY line
    discipline is briefly in default canonical/echo mode (`ECHO | ONLCR`). Any bytes written
    immediately after Docker returns `101 UPGRADED` get echoed back to stdout with `\r\n` -> `\r\r\n`
    translation, corrupting the HTTP request and response stream!

    Instead, we use `Tty: false` (no PTY line discipline at all; pure pipes) and had our tiny
    Node bridge emit a 1-byte `\\x00` ready marker on stdout once connected to `127.0.0.1:<port>`.
    On the client side, `_DemuxExecReader` strips Docker's 8-byte `stdcopy` frame headers from
    stdout so callers receive a completely transparent `asyncio.StreamReader`.
    """
    bridge_js = (
        f"const s=require('net').connect({port},'127.0.0.1',()=>{{process.stdout.write(Buffer.from([0]));process.stdin.pipe(s);s.pipe(process.stdout)}});"
        "s.on('end',()=>process.exit(0));s.on('error',()=>process.exit(1));"
    )
    cmd = [f"{VSCODE_MOUNT_PATH}/node", "-e", bridge_js]
    client, base_url = _docker_client_for_backend(backend)
    async with client:
        resp = await client.post(
            f"{base_url}/containers/{quote(container_id, safe='')}/exec",
            json={
                "AttachStdin": True,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": False,
                "Cmd": cmd,
            },
        )
        if resp.status_code not in (200, 201):
            raise Invalid(f"VS Code tunnel exec failed (HTTP {resp.status_code})")
        exec_id = str(resp.json().get("Id") or "")
    if not exec_id:
        raise Invalid("Docker did not return an exec id for VS Code tunnel")

    raw_reader, writer, host = await _open_raw_connection(backend)
    body = json.dumps({"Detach": False, "Tty": False}).encode()
    req = (
        f"POST /exec/{exec_id}/start HTTP/1.1\r\n"
        f"Host: {host}\r\n"
        "Content-Type: application/json\r\n"
        "Connection: Upgrade\r\n"
        "Upgrade: tcp\r\n"
        f"Content-Length: {len(body)}\r\n\r\n"
    ).encode() + body
    writer.write(req)
    await writer.drain()

    header = await asyncio.wait_for(raw_reader.readuntil(b"\r\n\r\n"), timeout=15.0)
    status_line = header.split(b"\r\n", 1)[0].decode("latin-1")
    if " 101 " not in status_line and " 200 " not in status_line:
        writer.close()
        raise Invalid(f"VS Code tunnel attach failed: {status_line}")

    demux_reader = asyncio.StreamReader()

    async def _demux_docker_frames() -> None:
        try:
            while True:
                frm_hdr = await raw_reader.readexactly(8)
                stream_type = frm_hdr[0]
                frame_size = int.from_bytes(frm_hdr[4:8], "big")
                if frame_size <= 0:
                    continue
                payload = await raw_reader.readexactly(frame_size)
                if stream_type == 1:  # stdout from Node net bridge
                    demux_reader.feed_data(payload)
        except (asyncio.IncompleteReadError, ConnectionError, asyncio.CancelledError):
            pass
        except Exception as exc:
            logger.debug("VS Code tunnel demux ended: %s", exc)
        finally:
            demux_reader.feed_eof()

    asyncio.create_task(_demux_docker_frames())

    # Wait for the 1-byte `\x00` ready signal confirming Node connected to 127.0.0.1:31337
    ready_byte = await asyncio.wait_for(demux_reader.readexactly(1), timeout=10.0)
    if ready_byte != b"\x00":
        writer.close()
        raise Invalid("VS Code tunnel handshake failed")

    return demux_reader, writer


def _get_direct_vscode_http_client() -> httpx.AsyncClient:
    global _DIRECT_VSCODE_HTTP_CLIENT
    if _DIRECT_VSCODE_HTTP_CLIENT is None or _DIRECT_VSCODE_HTTP_CLIENT.is_closed:
        _DIRECT_VSCODE_HTTP_CLIENT = httpx.AsyncClient(
            follow_redirects=False,
            timeout=httpx.Timeout(30.0, connect=1.5),
        )
    return _DIRECT_VSCODE_HTTP_CLIENT


async def _resolve_reachable_vscode_ip(
    state: VsCodeSessionState, target_cid: str
) -> str | None:
    """Return a directly reachable container/Pod IP for `target_cid`, probing once and caching per session."""
    if state.direct_ip_probed:
        return state.direct_ip

    candidate_ips: list[str] = []
    try:
        if is_k8s_backend(state.backend):
            k_client, k_base, default_ns = _k8s_client_for_backend(state.backend)
            target_ns, pod_name = _split_k8s_ref(default_ns, target_cid)
            async with k_client:
                p_resp = await k_client.get(
                    f"{k_base}/api/v1/namespaces/{quote(target_ns, safe='')}/pods/{quote(pod_name, safe='')}"
                )
                if p_resp.status_code == 200:
                    pod_ip = str(
                        ((p_resp.json() or {}).get("status") or {}).get("podIP") or ""
                    ).strip()
                    if pod_ip:
                        candidate_ips.append(pod_ip)
        else:
            info = await _inspect_docker_container(state.backend, target_cid)
            net_settings = (info or {}).get("NetworkSettings") or {}
            if isinstance(net_settings, dict):
                networks = net_settings.get("Networks") or {}
                if isinstance(networks, dict):
                    for net_obj in networks.values():
                        if isinstance(net_obj, dict):
                            ip_val = str(net_obj.get("IPAddress") or "").strip()
                            if ip_val and ip_val not in candidate_ips:
                                candidate_ips.append(ip_val)
                legacy_ip = str(net_settings.get("IPAddress") or "").strip()
                if legacy_ip and legacy_ip not in candidate_ips:
                    candidate_ips.append(legacy_ip)
    except Exception:
        pass

    for ip_addr in candidate_ips:
        try:
            _, probe_writer = await asyncio.wait_for(
                asyncio.open_connection(ip_addr, VSCODE_INTERNAL_PORT),
                timeout=0.75,
            )
            probe_writer.close()
            try:
                await probe_writer.wait_closed()
            except Exception:
                pass
            state.direct_ip = ip_addr
            state.direct_ip_probed = True
            return ip_addr
        except Exception:
            continue

    state.direct_ip = None
    state.direct_ip_probed = True
    return None


def resolve_verified_vscode_state(session_key: str, token: str) -> VsCodeSessionState:
    state = _VSCODE_SESSIONS.get(session_key)
    if state is None or not verify_vscode_token(
        session_key, state.container_id, token, expected_token=state.token
    ):
        raise NotFound("VS Code session is no longer active or token is invalid")
    return state


async def resolve_or_recover_vscode_state(
    session_key: str, token: str
) -> VsCodeSessionState:
    """Resolve VS Code session state from process memory or recover from Valkey across workers/restarts."""
    state = _VSCODE_SESSIONS.get(session_key)
    if state is not None:
        if not verify_vscode_token(
            session_key, state.container_id, token, expected_token=state.token
        ):
            raise NotFound("VS Code session is no longer active or token is invalid")
        return state

    try:
        redis = get_redis()
        raw = await redis.get(f"{_VSCODE_REDIS_PREFIX}{session_key}")
        if raw:
            data = json.loads(str(raw))
            saved_token = str(data.get("token") or "")
            saved_cid = str(data.get("container_id") or "")
            if saved_token and verify_vscode_token(
                session_key, saved_cid, token, expected_token=saved_token
            ):
                b_raw = data.get("backend") or {}
                sec_b64 = b_raw.get("secrets_encrypted")
                backend = Backend(
                    id=str(b_raw.get("id") or "bck_docker"),
                    name=str(b_raw.get("name") or "Docker"),
                    type=BackendType.COMPUTE,
                    provider=str(b_raw.get("provider") or "docker"),
                    endpoint=b_raw.get("endpoint"),
                    config=(
                        dict(b_raw["config"])
                        if isinstance(b_raw.get("config"), dict)
                        else {}
                    ),
                    secrets_encrypted=(
                        base64.b64decode(sec_b64) if isinstance(sec_b64, str) and sec_b64 else None
                    ),
                )
                recovered = VsCodeSessionState(
                    session_key=str(data.get("session_key") or session_key),
                    kind=str(data.get("kind") or "terminal"),
                    session_id=str(data.get("session_id") or ""),
                    project_id=str(data.get("project_id") or ""),
                    backend=backend,
                    container_id=str(data.get("container_id") or ""),
                    token=saved_token,
                    mode=str(data.get("mode") or "in_container"),
                    arch=str(data.get("arch") or "amd64"),
                    memory_mb=int(data.get("memory_mb") or MIN_VSCODE_MEMORY_MB),
                    memory_raised=bool(data.get("memory_raised", False)),
                    notice=data.get("notice"),
                    sidecar_container_id=data.get("sidecar_container_id"),
                    theme=str(data.get("theme") or "dark"),
                )
                _VSCODE_SESSIONS[session_key] = recovered
                return recovered
    except Exception:
        pass

    raise NotFound("VS Code session is no longer active or token is invalid")


async def proxy_vscode_http(
    request: Request,
    *,
    session_key: str,
    token: str,
    subpath: str,
) -> Response:
    """Forward an HTTP request into `openvscode-server` inside the session container."""
    _enforce_sandbox_origin(request.headers)
    query_token = (request.query_params.get("token") or "").strip()
    cookie_token = (request.cookies.get(VSCODE_COOKIE_NAME) or "").strip()
    path_token = (token or "").strip() if token not in ("", "_cookie", "-") else ""

    state: VsCodeSessionState | None = None
    if query_token:
        state = await resolve_or_recover_vscode_state(session_key, query_token)
        if path_token and path_token != state.token:
            subpath = f"{path_token}/{subpath}".strip("/") if subpath else path_token
    elif path_token:
        try:
            state = await resolve_or_recover_vscode_state(session_key, path_token)
        except NotFound:
            if cookie_token:
                state = await resolve_or_recover_vscode_state(session_key, cookie_token)
                subpath = f"{path_token}/{subpath}".strip("/") if subpath else path_token
            else:
                raise
    elif cookie_token:
        state = await resolve_or_recover_vscode_state(session_key, cookie_token)
    else:
        raise NotFound("VS Code session token is missing")

    await _touch_vscode_activity(state)
    target_cid = state.sidecar_container_id or state.container_id

    raw_req_path = request.scope.get("raw_path", b"").decode("latin-1", errors="ignore")
    raw_parts = raw_req_path.split("/") if raw_req_path.startswith("/api/v1/vscode/") else []
    raw_session_seg = raw_parts[4] if len(raw_parts) > 4 and raw_parts[4] else session_key
    cookie_path = f"/api/v1/vscode/{raw_session_seg}"
    is_secure = request.url.scheme == "https" or (
        bool(getattr(settings, "TRUST_PROXY_HEADERS", False))
        and (request.headers.get("x-forwarded-proto") or "").strip().lower() == "https"
    )

    # If the token was supplied via ?token=..., exchange it for a path-scoped HttpOnly cookie
    # and redirect to strip the bearer token from the URL.
    if query_token and request.method in ("GET", "HEAD"):
        remaining_pairs = [(k, v) for k, v in parse_qsl(request.url.query, keep_blank_values=True) if k != "token"]
        clean_query = urlencode(remaining_pairs)
        redir_url = request.url.path
        if clean_query:
            redir_url = f"{redir_url}?{clean_query}"
        redir_resp = Response(status_code=302, headers={"Location": redir_url})
        redir_resp.set_cookie(
            key=VSCODE_COOKIE_NAME,
            value=state.token,
            path=cookie_path,
            httponly=True,
            samesite="lax",
            secure=is_secure,
        )
        return redir_resp

    base_path = f"/api/v1/vscode/{session_key}/{state.token}"
    clean_sub = subpath.lstrip("/")
    target_path = f"{base_path}/{clean_sub}" if clean_sub else f"{base_path}/"
    # Browser may percent-encode ':' as '%3A' in the request URL; inspect raw scope path
    # so we match whichever encoding the browser used when resolving relative `./out/...` assets.
    if raw_req_path and not raw_req_path.endswith("/") and not clean_sub:
        redir_target = f"{raw_req_path}/"
        if request.url.query:
            redir_target = f"{redir_target}?{request.url.query}"
        redir_slash = Response(status_code=307, headers={"Location": redir_target})
        redir_slash.set_cookie(
            key=VSCODE_COOKIE_NAME,
            value=state.token,
            path=cookie_path,
            httponly=True,
            samesite="lax",
            secure=is_secure,
        )
        return redir_slash
    if request.url.query:
        target_path = f"{target_path}?{request.url.query}"
    # Authenticate upstream HTTP via `Cookie: vscode-tkn=<upstream_tkn>` only — passing `?tkn=`
    # on HTTP causes openvscode-server (`webClientServer.ts`) to issue a 302 redirect stripping
    # `tkn` and the trailing slash, creating a 302 <-> 307 redirect loop.
    upstream_tkn = derive_vscode_upstream_token(session_key, state.container_id)

    body = await _read_bounded_vscode_body(request)
    upstream_req_headers: dict[str, str] = {
        "Host": request.headers.get("host", "localhost"),
        "Cookie": f"vscode-tkn={upstream_tkn}",
    }
    for k, v in request.headers.items():
        if k.lower() not in _STRIPPED_UPSTREAM_REQUEST_HEADERS:
            upstream_req_headers[k] = v

    status_code = 200
    resp_headers: dict[str, str] = {}
    content_bytes = b""
    used_direct = False

    direct_ip = await _resolve_reachable_vscode_ip(state, target_cid)
    if direct_ip:
        try:
            direct_client = _get_direct_vscode_http_client()
            upstream_resp = await direct_client.request(
                method=request.method,
                url=f"http://{direct_ip}:{VSCODE_INTERNAL_PORT}{target_path}",
                headers=upstream_req_headers,
                content=body,
            )
            status_code = upstream_resp.status_code
            for hk, hv in upstream_resp.headers.items():
                hk_low = hk.strip().lower()
                if hk_low == "transfer-encoding" or hk_low in _STRIPPED_UPSTREAM_RESPONSE_HEADERS:
                    continue
                resp_headers[hk.strip()] = hv.strip()
            content_bytes = upstream_resp.content
            used_direct = True
        except httpx.TransportError:
            state.direct_ip = None
            state.direct_ip_probed = False

    if not used_direct:
        reader, writer = await open_container_tcp_tunnel(state.backend, target_cid)
        try:
            hdr_lines = [
                f"{request.method} {target_path} HTTP/1.1",
                f"Host: {request.headers.get('host', 'localhost')}",
                "Connection: close",
                f"Cookie: vscode-tkn={upstream_tkn}",
                f"Content-Length: {len(body)}",
            ]
            for k, v in request.headers.items():
                if k.lower() not in _STRIPPED_UPSTREAM_REQUEST_HEADERS:
                    hdr_lines.append(f"{k}: {v}")
            raw_req = ("\r\n".join(hdr_lines) + "\r\n\r\n").encode("latin-1", errors="ignore") + body
            writer.write(raw_req)
            await writer.drain()

            raw_header = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=30.0)
            header_text = raw_header[:-4].decode("latin-1", errors="replace")
            lines = header_text.split("\r\n")
            status_parts = lines[0].split(" ", 2)
            status_code = int(status_parts[1]) if len(status_parts) >= 2 and status_parts[1].isdigit() else 200

            is_chunked = False
            for line in lines[1:]:
                if ":" not in line:
                    continue
                hk, hv = line.split(":", 1)
                hk_low = hk.strip().lower()
                hv_val = hv.strip()
                if hk_low == "transfer-encoding" and "chunked" in hv_val.lower():
                    is_chunked = True
                    continue
                if hk_low in _STRIPPED_UPSTREAM_RESPONSE_HEADERS:
                    continue
                resp_headers[hk.strip()] = hv_val

            raw_body = await reader.read()
            if is_chunked:
                decoded = bytearray()
                idx = 0
                while idx < len(raw_body):
                    crlf = raw_body.find(b"\r\n", idx)
                    if crlf == -1:
                        break
                    size_hex = raw_body[idx:crlf].split(b";", 1)[0].strip()
                    try:
                        chunk_len = int(size_hex, 16)
                    except ValueError:
                        break
                    if chunk_len == 0:
                        break
                    start = crlf + 2
                    decoded.extend(raw_body[start : start + chunk_len])
                    idx = start + chunk_len + 2
                content_bytes = bytes(decoded)
            else:
                content_bytes = raw_body
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    # Isolate origin capabilities, block MIME sniffing & prevent embedding in foreign frames
    resp_headers.update(_build_vscode_frame_headers(resp_headers, request.headers))

    content_type = (resp_headers.get("Content-Type") or resp_headers.get("content-type") or "").lower()
    if not clean_sub and "text/html" in content_type and content_bytes:
        req_theme = request.query_params.get("theme") or state.theme
        html_str = content_bytes.decode("utf-8", errors="replace")

        def _inject_config_defaults(match: re.Match[str]) -> str:
            raw_attr = html.unescape(match.group(1))
            try:
                cfg_obj = json.loads(raw_attr)
                if isinstance(cfg_obj, dict):
                    defaults = cfg_obj.get("configurationDefaults")
                    if not isinstance(defaults, dict):
                        defaults = {}
                    defaults.update(_default_vscode_settings_dict(req_theme))
                    cfg_obj["configurationDefaults"] = defaults
                    if upstream_tkn:
                        cfg_obj["connectionToken"] = upstream_tkn
                    if "defaultLayout" not in cfg_obj:
                        cfg_obj["defaultLayout"] = {
                            "views": [{"id": "workbench.explorer.fileView"}],
                        }
                    escaped = html.escape(json.dumps(cfg_obj), quote=True)
                    return f'id="vscode-workbench-web-configuration" data-settings="{escaped}"'
            except Exception:
                pass
            return match.group(0)

        html_str = re.sub(
            r'id="vscode-workbench-web-configuration"\s+data-settings="([^"]*)"',
            _inject_config_defaults,
            html_str,
            count=1,
        )
        html_str = re.sub(
            r'(<meta\s+name="viewport"\s+content="[^"]*)"',
            lambda m: m.group(0) if "viewport-fit=cover" in m.group(1) else f'{m.group(1)}, viewport-fit=cover"',
            html_str,
            count=1,
        )
        bg_hex = "#ffffff" if (req_theme or "").strip().lower() == "light" else "#1f1f1f"
        tkn_js = json.dumps(upstream_tkn)
        cpath_js = json.dumps(cookie_path)
        init_layout_script = (
            f"<style>html,body,#workbench\\.parts\\.editor,.monaco-workbench{{height:100%!important;min-height:100dvh!important;margin:0!important;padding:0!important;overflow:hidden!important;background-color:{bg_hex}!important;}}</style>"
            "<script>"
            "(function(){"
            "try{"
            f"var tkn={tkn_js},cp={cpath_js};"
            "if(tkn){"
            "try{document.cookie='vscode-tkn='+encodeURIComponent(tkn)+'; path='+cp+'; SameSite=Lax';}catch(e){}"
            "if((document.cookie||'').indexOf('vscode-tkn=')===-1){"
            "var d=Object.getOwnPropertyDescriptor(Document.prototype,'cookie')||Object.getOwnPropertyDescriptor(HTMLDocument.prototype,'cookie');"
            "if(d&&d.get){"
            "Object.defineProperty(document,'cookie',{"
            "configurable:true,"
            "get:function(){var c=d.get.call(document)||'';return c.indexOf('vscode-tkn=')!==-1?c:(c?c+'; ':'')+'vscode-tkn='+encodeURIComponent(tkn);},"
            "set:function(v){if(d.set)d.set.call(document,v);}"
            "});"
            "}"
            "}"
            "}"
            f"var k='gitseed.vscode.layout.v2.{token[:12]}';"
            "if(!sessionStorage.getItem(k)){"
            "sessionStorage.setItem(k,'1');"
            "if(window.indexedDB&&indexedDB.databases){"
            "indexedDB.databases().then(function(dbs){"
            "(dbs||[]).forEach(function(info){"
            "if(!info||!info.name||info.name.indexOf('vscode-web-state-db-')!==0)return;"
            "var req=indexedDB.open(info.name);"
            "req.onsuccess=function(){"
            "var db=req.result;"
            "if(!db.objectStoreNames.contains('ItemTable')){db.close();return;}"
            "var tx=db.transaction('ItemTable','readwrite');"
            "var st=tx.objectStore('ItemTable');"
            "st.put('true','workbench.auxiliarybar.hidden');"
            "st.put('false','workbench.sidebar.hidden');"
            "st.put('workbench.view.explorer','workbench.sidebar.activeviewletid');"
            "st.put('true','workbench.panel.hidden');"
            "tx.oncomplete=function(){db.close();};"
            "};"
            "});"
            "}).catch(function(){});"
            "}"
            "}"
            "window.addEventListener('load',function(){setTimeout(function(){window.dispatchEvent(new Event('resize'));},150);setTimeout(function(){window.dispatchEvent(new Event('resize'));},600);});"
            "}catch(e){}"
            "})();"
            "</script>"
        )
        if "<head>" in html_str:
            html_str = html_str.replace("<head>", f"<head>{init_layout_script}", 1)
        elif "</head>" in html_str:
            html_str = html_str.replace("</head>", f"{init_layout_script}</head>", 1)
        content_bytes = html_str.encode("utf-8")

    final_resp = Response(content=content_bytes, status_code=status_code, headers=resp_headers)
    if cookie_token != state.token:
        final_resp.set_cookie(
            key=VSCODE_COOKIE_NAME,
            value=state.token,
            path=cookie_path,
            httponly=True,
            samesite="lax",
            secure=is_secure,
        )
    if not clean_sub and upstream_tkn:
        final_resp.set_cookie(
            key="vscode-tkn",
            value=upstream_tkn,
            path=cookie_path,
            httponly=False,
            samesite="lax",
            secure=is_secure,
        )
    return final_resp


async def proxy_vscode_websocket(
    websocket: WebSocket,
    *,
    session_key: str,
    token: str,
    subpath: str,
) -> None:
    """Bridge a browser WebSocket upgrade directly into `openvscode-server` inside the container."""
    try:
        _enforce_sandbox_origin(websocket.headers)
    except (Forbidden, Invalid) as exc:
        await websocket.close(code=4403, reason=str(exc))
        return

    query_token = (websocket.query_params.get("token") or "").strip()
    cookie_token = (websocket.cookies.get(VSCODE_COOKIE_NAME) or "").strip()
    path_token = (token or "").strip() if token not in ("", "_cookie", "-") else ""

    try:
        if query_token:
            state = await resolve_or_recover_vscode_state(session_key, query_token)
            if path_token and path_token != state.token:
                subpath = f"{path_token}/{subpath}".strip("/") if subpath else path_token
        elif path_token:
            try:
                state = await resolve_or_recover_vscode_state(session_key, path_token)
            except NotFound:
                if cookie_token:
                    state = await resolve_or_recover_vscode_state(session_key, cookie_token)
                    subpath = f"{path_token}/{subpath}".strip("/") if subpath else path_token
                else:
                    raise
        elif cookie_token:
            state = await resolve_or_recover_vscode_state(session_key, cookie_token)
        else:
            raise NotFound("VS Code session token is missing")
    except Exception:
        await websocket.close(code=4401, reason="Invalid or expired VS Code session token")
        return

    await websocket.accept()
    await _touch_vscode_activity(state)
    target_cid = state.sidecar_container_id or state.container_id

    base_path = f"/api/v1/vscode/{session_key}/{state.token}"
    clean_sub = subpath.lstrip("/")
    target_path = f"{base_path}/{clean_sub}" if clean_sub else f"{base_path}/"
    if websocket.url.query:
        target_path = f"{target_path}?{websocket.url.query}"
    upstream_tkn = derive_vscode_upstream_token(session_key, state.container_id)
    target_path = _append_upstream_vscode_token(target_path, upstream_tkn)

    # Connect to openvscode-server's WebSocket endpoint via direct container/Pod IP when reachable,
    # falling back to the raw Docker exec TCP bridge when on an isolated network.
    direct_conn: tuple[asyncio.StreamReader, asyncio.StreamWriter] | None = None
    direct_ip = await _resolve_reachable_vscode_ip(state, target_cid)
    if direct_ip:
        try:
            direct_conn = await asyncio.wait_for(
                asyncio.open_connection(direct_ip, VSCODE_INTERNAL_PORT),
                timeout=2.0,
            )
        except Exception:
            state.direct_ip = None
            state.direct_ip_probed = False
    if direct_conn is not None:
        reader, writer = direct_conn
    else:
        reader, writer = await open_container_tcp_tunnel(state.backend, target_cid)
    ws_key = "dGhlIHNhbXBsZSBub25jZQ=="
    ws_hdr_lines = [
        f"GET {target_path} HTTP/1.1",
        f"Host: {websocket.headers.get('host', '127.0.0.1')}",
        "Upgrade: websocket",
        "Connection: Upgrade",
        f"Cookie: vscode-tkn={upstream_tkn}",
        f"Sec-WebSocket-Key: {ws_key}",
        "Sec-WebSocket-Version: 13",
    ]
    for fwd_hdr in ("origin", "sec-websocket-protocol", "user-agent"):
        val = websocket.headers.get(fwd_hdr)
        if val:
            ws_hdr_lines.append(f"{fwd_hdr}: {val}")
    handshake = ("\r\n".join(ws_hdr_lines) + "\r\n\r\n").encode("latin-1", errors="ignore")
    writer.write(handshake)
    await writer.drain()

    try:
        resp_hdr = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=15.0)
        if b" 101 " not in resp_hdr.split(b"\r\n", 1)[0]:
            await websocket.close(code=1011, reason="Upstream VS Code WebSocket upgrade failed")
            return
    except Exception:
        await websocket.close(code=1011, reason="Upstream VS Code WebSocket timeout")
        return

    def _encode_ws_frame(payload: bytes, opcode: int) -> bytes:
        mask_key = secrets.token_bytes(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length < 65536:
            header.append(0x80 | 126)
            header.extend(length.to_bytes(2, "big"))
        else:
            header.append(0x80 | 127)
            header.extend(length.to_bytes(8, "big"))
        header.extend(mask_key)
        masked = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
        return bytes(header) + masked

    async def _pump_client_to_upstream() -> None:
        while True:
            msg = await websocket.receive()
            if msg.get("type") == "websocket.disconnect":
                break
            await _touch_vscode_activity(state)
            if msg.get("bytes") is not None:
                writer.write(_encode_ws_frame(msg["bytes"], 0x02))
                await writer.drain()
            elif msg.get("text") is not None:
                writer.write(_encode_ws_frame(msg["text"].encode("utf-8"), 0x01))
                await writer.drain()

    max_ws_frame_bytes = 32 * 1024 * 1024

    async def _pump_upstream_to_client() -> None:
        while True:
            first2 = await reader.readexactly(2)
            opcode = first2[0] & 0x0F
            masked = bool(first2[1] & 0x80)
            length = first2[1] & 0x7F
            if length == 126:
                length = int.from_bytes(await reader.readexactly(2), "big")
            elif length == 127:
                length = int.from_bytes(await reader.readexactly(8), "big")
            if length > max_ws_frame_bytes:
                logger.warning(
                    "Closing VS Code WebSocket for %s: upstream frame (%s bytes) exceeds 32 MiB cap",
                    session_key,
                    length,
                )
                break
            mask_key = await reader.readexactly(4) if masked else b""
            payload = await reader.readexactly(length) if length > 0 else b""
            if masked:
                payload = bytes(b ^ mask_key[i & 3] for i, b in enumerate(payload))
            if opcode == 0x08:  # Close
                break
            if opcode == 0x09:  # Ping -> Pong
                writer.write(_encode_ws_frame(payload, 0x0A))
                await writer.drain()
                continue
            if opcode == 0x01:
                await websocket.send_text(payload.decode("utf-8", errors="replace"))
            elif opcode == 0x02:
                await websocket.send_bytes(payload)

    in_task = asyncio.create_task(_pump_client_to_upstream())
    out_task = asyncio.create_task(_pump_upstream_to_client())
    try:
        done, pending = await asyncio.wait(
            {in_task, out_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        for t in done:
            _ = t.exception()
    except (WebSocketDisconnect, asyncio.CancelledError):
        pass
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        try:
            await websocket.close()
        except Exception:
            pass

