from __future__ import annotations

import asyncio
import base64
import fnmatch
import hashlib
import io
import json
import logging
import os
import shlex
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

from gitseed_common.enums import (
    SANDBOX_ACTIVE_STATUSES,
    ActivityType,
    ActorType,
    BackendHealth,
    BackendType,
    OrgRole,
    ProjectRole,
    SandboxStatus,
)
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.db import AsyncSessionLocal
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend, ProjectRepository
from gitseed_core.models.sandbox import SandboxSession
from gitseed_core.models.seed import Seed
from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets
from gitseed_core.services import git_browse, session_workspace
from gitseed_core.services import projects as projects_service
from gitseed_core.services.activity import record
from gitseed_core.services.compute_runtime import (
    ensure_docker_session_workspace_volume,
    is_k8s_backend,
    k8s_exec_command,
    k8s_put_archive,
    remove_docker_session_workspace_volume,
    spawn_k8s_pod,
)
from gitseed_core.services.errors import Forbidden, Invalid, NotFound
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.git_merge import format_seed_branch
from gitseed_core.services.sandbox_detect import (  # noqa: F401
    get_sandbox_run_config,
)
from gitseed_core.services.scheduler import (
    _docker_client_for_backend,
    _ensure_docker_network,
    fetch_docker_container_logs,
    inspect_docker_container_state,
    stop_and_remove_docker_container,
)
from gitseed_core.services.session_pty import close_live_pty
from gitseed_core.services.session_vscode import (
    VSCODE_BUNDLE_VOLUME,
    VSCODE_MOUNT_PATH,
    close_session_vscode,
    ensure_session_vscode,
)

logger = logging.getLogger("uvicorn.error")

_MAX_ARTIFACTS_PER_SESSION = 25
_DEFAULT_ARTIFACT_GLOBS = [
    "*.apk",
    "**/*.apk",
    "*.aab",
    "**/*.aab",
    "dist/*",
    "dist/**/*",
    "*.whl",
    "*.zip",
    "*.tar.gz",
]


async def _require_project_developer(
    session: AsyncSession, actor: User, project_id_or_slug: str
) -> Project:
    project = await projects_service.get_project(session, actor, project_id_or_slug)
    if project.archived_at is not None:
        raise Invalid("Cannot run sandboxes in an archived project")
    if actor.org_role == OrgRole.ADMIN:
        return project
    role = await projects_service.get_member_role(session, project.id, actor.id)
    if role not in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER):
        raise Forbidden("Project developer or maintainer privileges required to run sandboxes")
    return project


async def _select_docker_compute_backend(
    session: AsyncSession,
    project: Project | None = None,
) -> Backend:
    backends = list(
        (
            await session.execute(
                select(Backend)
                .where(
                    Backend.type == BackendType.COMPUTE,
                    col(Backend.enabled).is_(True),
                )
                .order_by(col(Backend.created_at).asc())
            )
        )
        .scalars()
        .all()
    )
    if project is not None and project.backends_restricted:
        allowed_bids = set(
            (
                await session.execute(
                    select(ProjectBackend.backend_id).where(
                        ProjectBackend.project_id == project.id
                    )
                )
            )
            .scalars()
            .all()
        )
        backends = [b for b in backends if b.id in allowed_bids]
        if not backends:
            raise Invalid(
                "Project has backends_restricted enabled and no granted Compute backend is available"
            )
    healthy = [b for b in backends if b.health != BackendHealth.UNREACHABLE]
    for b in healthy:
        if b.provider.lower() == "docker":
            default_ep = (
                getattr(settings, "DOCKER_HOST", "unix:///var/run/docker.sock")
                or "unix:///var/run/docker.sock"
            )
            ep = (b.endpoint or default_ep).strip()
            if (
                ep == "unix:///var/run/docker.sock"
                and default_ep != ep
                and not os.path.exists("/var/run/docker.sock")
            ):
                ep = default_ep.strip()
            if ep.startswith("unix://") and not os.path.exists(ep.removeprefix("unix://")) and len(healthy) > 1:
                continue
            return b
    for b in healthy:
        if is_k8s_backend(b):
            return b
    if healthy:
        return healthy[0]
    if backends:
        return backends[0]
    raise Invalid("No enabled Compute backend is available to run a sandbox container")


def _clone_and_pack_workspace_tar(
    *,
    backend: Backend,
    full_name: str,
    token: str,
    ref: str,
    default_branch: str,
) -> tuple[bytes, str, str]:
    """Trusted staging step: shallow-clone repo at `ref`, resolve `head_sha`, strip `.git`, return tar bytes."""
    auth_url = git_browse._build_authenticated_git_url(backend, full_name, token)
    chosen_ref = (ref or default_branch or "main").strip()

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        try:
            clone_res = subprocess.run(
                [
                    "git",
                    "clone",
                    "--depth",
                    "1",
                    "--branch",
                    chosen_ref,
                    auth_url,
                    str(tmp_path),
                ],
                capture_output=True,
                text=True,
                check=False,
                timeout=120,
            )
            if clone_res.returncode != 0 and chosen_ref != default_branch:
                # Fallback to default branch if seed branch was already merged/deleted
                shutil.rmtree(tmp_path, ignore_errors=True)
                tmp_path.mkdir(parents=True, exist_ok=True)
                chosen_ref = default_branch
                clone_res = subprocess.run(
                    [
                        "git",
                        "clone",
                        "--depth",
                        "1",
                        "--branch",
                        chosen_ref,
                        auth_url,
                        str(tmp_path),
                    ],
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=120,
                )
        except subprocess.TimeoutExpired as exc:
            raise Invalid(
                f"Timed out cloning repository '{full_name}' at '{chosen_ref}'"
            ) from exc

        if clone_res.returncode != 0:
            stderr_clean = (clone_res.stderr or "git clone failed").strip()
            if token:
                stderr_clean = stderr_clean.replace(token, "***")
            raise Invalid(f"Failed to clone repository '{full_name}' at '{chosen_ref}': {stderr_clean[:240]}")

        rev_res = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        head_sha = rev_res.stdout.strip() if rev_res.returncode == 0 else ""

        # Strip the cloned .git (which contains the remote auth URL) and initialize a
        # clean, remote-free baseline commit so VS Code and git diff work inside /workspace
        session_workspace.init_clean_git_baseline(
            tmp_path, ref=chosen_ref, head_sha=head_sha
        )

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            for item in tmp_path.iterdir():
                tar.add(str(item), arcname=item.name)
        return buf.getvalue(), chosen_ref, head_sha


async def _spawn_sandbox_container(
    *,
    compute_backend: Backend,
    sandbox: SandboxSession,
    workspace_tar: bytes,
    initial_stdin: str | None = None,
) -> str:
    """Create an isolated container on `gitseed-sandboxes`, upload clean workspace tar, and start it."""
    cfg = compute_backend.config or {}
    cpus = float(cfg.get("cpus") or 2.0)
    memory_mb = int(cfg.get("memory_mb") or 2048)
    network_name = settings.SANDBOX_DOCKER_NETWORK
    container_name = f"gitseed-sbx-{sandbox.id}"

    # Create a persistent FIFO pipe (`/tmp/.gitseed_stdin`) held open for writing by a
    # background sleep loop so interactive scripts (e.g. `input()`, `read`) wait for
    # operator input instead of immediately failing with `EOFError`.
    cmd_body = (
        f"set -e\n{sandbox.setup_command.strip()}\n({sandbox.command.strip()}) < /tmp/.gitseed_stdin"
        if sandbox.setup_command and sandbox.setup_command.strip()
        else f"({sandbox.command.strip()}) < /tmp/.gitseed_stdin"
    )
    full_script = (
        "rm -f /tmp/.gitseed_stdin /tmp/.gitseed_eof\n"
        "mkfifo /tmp/.gitseed_stdin\n"
        "(while [ ! -f /tmp/.gitseed_eof ]; do sleep 1; done) > /tmp/.gitseed_stdin &\n"
        "_GS_KEEPALIVE=$!\n"
        "if [ -s /tmp/.gitseed_initial_stdin ]; then cat /tmp/.gitseed_initial_stdin > /tmp/.gitseed_stdin & fi\n"
        f"{cmd_body}\n"
        "_GS_EXIT=$?\n"
        "kill $_GS_KEEPALIVE 2>/dev/null || true\n"
        "exit $_GS_EXIT"
    )
    if sandbox.kind == "terminal":
        # Interactive terminal: run the optional setup, then stay alive so the browser
        # can attach real PTY shells via `docker exec -it` (see session_pty.py).
        setup = (sandbox.setup_command or "").strip()
        full_script = (
            "mkdir -p /workspace/.gitseed/artifacts 2>/dev/null || true\n"
            + (
                f"printf '%s\\n' {shlex.quote(f'$ {setup}')}\n({setup}) || echo '[setup exited with an error]'\n"
                if setup
                else ""
            )
            + "trap 'exit 0' TERM INT\n"
            "while true; do sleep 2; done"
        )

    # Build the outer tar with "workspace/" prefix (plus optional initial stdin & root apt config)
    wrapped_buf = io.BytesIO()
    with tarfile.open(fileobj=wrapped_buf, mode="w") as outer_tar:
        ws_info = tarfile.TarInfo(name="workspace")
        ws_info.type = tarfile.DIRTYPE
        ws_info.mode = 0o755
        outer_tar.addfile(ws_info)
        with tarfile.open(fileobj=io.BytesIO(workspace_tar), mode="r") as inner_tar:
            for member in inner_tar.getmembers():
                member_copy = member
                member_copy.name = f"workspace/{member.name.lstrip('./')}"
                extracted = inner_tar.extractfile(member) if member.isfile() else None
                outer_tar.addfile(member_copy, extracted)
        if initial_stdin is not None and initial_stdin != "":
            stdin_bytes = (
                initial_stdin
                if initial_stdin.endswith("\n")
                else f"{initial_stdin}\n"
            ).encode("utf-8")
            stdin_info = tarfile.TarInfo(name="tmp/.gitseed_initial_stdin")
            stdin_info.size = len(stdin_bytes)
            stdin_info.mode = 0o644
            outer_tar.addfile(stdin_info, io.BytesIO(stdin_bytes))
        if sandbox.as_root:
            # Let apt run its download sandbox as root instead of `_apt`
            apt_cfg = b'APT::Sandbox::User "root";\n'
            apt_info = tarfile.TarInfo(name="etc/apt/apt.conf.d/99gitseed-root")
            apt_info.size = len(apt_cfg)
            apt_info.mode = 0o644
            outer_tar.addfile(apt_info, io.BytesIO(apt_cfg))

    sbx_env_map: dict[str, str] = {
        "PYTHONUNBUFFERED": "1",
        "TERM": "xterm-256color",
    }
    proxy_url = str(getattr(settings, "RUNNER_EGRESS_PROXY_URL", "") or "").strip()
    if proxy_url:
        internal_hosts = str(
            getattr(settings, "EGRESS_INTERNAL_HOSTS", "litellm,api") or "litellm,api"
        ).strip()
        no_proxy_val = f"{internal_hosts},localhost,127.0.0.1,host.docker.internal"
        sbx_env_map.update(
            {
                "HTTPS_PROXY": proxy_url,
                "HTTP_PROXY": proxy_url,
                "https_proxy": proxy_url,
                "http_proxy": proxy_url,
                "NO_PROXY": no_proxy_val,
                "no_proxy": no_proxy_val,
            }
        )

    if is_k8s_backend(compute_backend):
        k8s_script = (
            "while [ ! -f /tmp/.gitseed_staged ]; do sleep 0.2; done\n"
            + full_script
        )
        pod_ref = await spawn_k8s_pod(
            backend=compute_backend,
            pod_name=container_name,
            image=sandbox.image,
            command=["sh", "-c", k8s_script],
            env_map=sbx_env_map,
            labels={
                "app.gitseed.managed": "true",
                "app.gitseed.sandbox_id": sandbox.id,
                "app.gitseed.project_id": sandbox.project_id,
            },
            cpus=cpus,
            memory_mb=memory_mb,
            as_root=sandbox.as_root,
            tty=True,
            stdin=True,
            working_dir="/workspace",
            project_slug=sandbox.project_id,
            workload="sandbox",
        )
        try:
            await k8s_put_archive(compute_backend, pod_ref, "/", wrapped_buf.getvalue(), timeout=60.0)
            await k8s_exec_command(compute_backend, pod_ref, ["touch", "/tmp/.gitseed_staged"], timeout=15.0)
        except Exception as exc:
            await stop_and_remove_docker_container(compute_backend, pod_ref)
            raise Invalid(f"Failed to stage workspace files into Kubernetes sandbox Pod: {exc}") from exc
        return pod_ref

    create_payload: dict[str, Any] = {
        "Image": sandbox.image,
        "Cmd": ["sh", "-c", full_script],
        "WorkingDir": "/workspace",
        "Tty": True,
        "OpenStdin": True,
        "Env": [f"{k}={v}" for k, v in sbx_env_map.items()],
        "Labels": {
            "app.gitseed.managed": "true",
            "app.gitseed.sandbox_id": sandbox.id,
            "app.gitseed.project_id": sandbox.project_id,
        },
        "HostConfig": {
            "NetworkMode": network_name,
            "NanoCPUs": int(cpus * 1_000_000_000),
            "Memory": int(memory_mb * 1024 * 1024),
            "PidsLimit": 512,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            "AutoRemove": False,
        },
    }

    # If as_root is requested and authorized, run as root (uid 0) with the minimal
    # capabilities package managers need (apt drops to `_apt` via setgroups/setuid).
    # Non-root sessions keep the fully locked-down profile.
    if sandbox.as_root:
        create_payload["User"] = "0"
        create_payload["HostConfig"]["CapDrop"] = ["ALL"]
        create_payload["HostConfig"]["CapAdd"] = [
            "CHOWN",
            "DAC_OVERRIDE",
            "FOWNER",
            "SETUID",
            "SETGID",
        ]
        create_payload["HostConfig"]["SecurityOpt"] = ["no-new-privileges:true"]

    if getattr(settings, "SANDBOX_ALLOW_HOST_GATEWAY", False):
        create_payload["HostConfig"]["ExtraHosts"] = ["host.docker.internal:host-gateway"]

    client, base_url = _docker_client_for_backend(compute_backend)
    async with client:
        await _ensure_docker_network(client, base_url, network_name)
        ws_vol_name, _ = await ensure_docker_session_workspace_volume(
            client, base_url, sandbox.id, sandbox.project_id
        )
        await client.post(
            f"{base_url}/volumes/create",
            json={
                "Name": VSCODE_BUNDLE_VOLUME,
                "Labels": {"app.gitseed.managed": "true", "app.gitseed.vscode_bundle": "true"},
            },
        )
        create_payload["HostConfig"]["Binds"] = [
            f"{ws_vol_name}:/workspace",
            f"{VSCODE_BUNDLE_VOLUME}:{VSCODE_MOUNT_PATH}:ro",
        ]
        await client.delete(
            f"{base_url}/containers/{quote(container_name, safe='')}?force=true"
        )

        create_resp = await client.post(
            f"{base_url}/containers/create?name={quote(container_name, safe='')}",
            json=create_payload,
        )
        if create_resp.status_code == 404:
            # Image not present locally yet; pull it from registry first
            pull_resp = await client.post(
                f"{base_url}/images/create",
                params={"fromImage": sandbox.image},
                timeout=180.0,
            )
            if pull_resp.status_code not in (200, 201):
                raise Invalid(
                    f"Unable to pull sandbox image '{sandbox.image}': {pull_resp.text[:240]}"
                )
            create_resp = await client.post(
                f"{base_url}/containers/create?name={quote(container_name, safe='')}",
                json=create_payload,
            )

        if create_resp.status_code not in (200, 201):
            raise Invalid(
                f"Docker container create failed (HTTP {create_resp.status_code}): {create_resp.text[:240]}"
            )

        container_id = str(create_resp.json().get("Id") or container_name)

        put_resp = await client.put(
            f"{base_url}/containers/{quote(container_id, safe='')}/archive",
            params={"path": "/"},
            content=wrapped_buf.getvalue(),
            headers={"Content-Type": "application/x-tar"},
            timeout=60.0,
        )
        if put_resp.status_code not in (200, 201, 204):
            await client.delete(
                f"{base_url}/containers/{quote(container_id, safe='')}?force=true"
            )
            raise Invalid(
                f"Failed to stage workspace files into sandbox container (HTTP {put_resp.status_code})"
            )

        start_resp = await client.post(
            f"{base_url}/containers/{quote(container_id, safe='')}/start"
        )
        if start_resp.status_code not in (200, 204, 304):
            await client.delete(
                f"{base_url}/containers/{quote(container_id, safe='')}?force=true"
            )
            raise Invalid(
                f"Failed to start sandbox container (HTTP {start_resp.status_code}): {start_resp.text[:240]}"
            )

        return container_id


def _first_matching_glob(rel_posix_path: str, globs: list[str]) -> str | None:
    p = PurePosixPath(rel_posix_path)
    basename = p.name
    for pattern in globs:
        clean_pat = pattern.strip().lstrip("./")
        if not clean_pat:
            continue
        if fnmatch.fnmatch(rel_posix_path, clean_pat) or fnmatch.fnmatch(basename, clean_pat):
            return pattern.strip()
        try:
            if p.match(clean_pat):
                return pattern.strip()
        except Exception:
            pass
    return None


def _matches_any_glob(rel_posix_path: str, globs: list[str]) -> bool:
    return _first_matching_glob(rel_posix_path, globs) is not None


def _store_artifacts(
    sandbox: SandboxSession,
    candidates: list[tuple[str, bytes, str]],
    existing: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Write `(rel_path, data, rule)` files to the Core artifacts volume.

    Identical files (same SHA-256) are kept once (shortest path); total size and count are capped.
    """
    dest_root = (Path(settings.ARTIFACTS_DIR) / sandbox.id).resolve()
    collected: list[dict[str, Any]] = [dict(a) for a in (existing or []) if isinstance(a, dict)]
    by_digest: dict[str, dict[str, Any]] = {str(a.get("sha256")): a for a in collected}
    total_bytes = sum(int(a.get("size_bytes") or 0) for a in collected)
    max_bytes = settings.SANDBOX_MAX_ARTIFACT_BYTES

    for rel_path, data, rule in candidates:
        if len(collected) >= _MAX_ARTIFACTS_PER_SESSION:
            break
        digest = hashlib.sha256(data).hexdigest()
        target = (dest_root / rel_path).resolve()
        if not target.is_relative_to(dest_root) or Path(rel_path).name.startswith(".baseline"):
            continue
        prev = by_digest.get(digest)
        if prev is not None:
            if len(rel_path) < len(str(prev["rel_path"])):
                old = (dest_root / str(prev["rel_path"])).resolve()
                if old.is_file() and old.is_relative_to(dest_root):
                    old.unlink(missing_ok=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(data)
                prev["rel_path"] = rel_path
                prev["name"] = Path(rel_path).name
            continue
        if total_bytes + len(data) > max_bytes:
            logger.warning("Sandbox %s artifact cap reached; skipping %s", sandbox.id, rel_path)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        total_bytes += len(data)
        entry = {
            "name": Path(rel_path).name,
            "rel_path": rel_path,
            "size_bytes": len(data),
            "sha256": digest,
            "rule": rule,
        }
        by_digest[digest] = entry
        collected.append(entry)
    return collected


async def _collect_container_artifacts(
    compute_backend: Backend,
    sandbox: SandboxSession,
) -> list[dict[str, Any]]:
    """Collect session artifacts onto the Core volume before the container is removed.

    - `terminal` sessions never guess: only files the user put in `/workspace/.gitseed/artifacts/`
      (plus anything saved explicitly while running) are kept.
    - `job` sessions match globs (request → project/run.json → defaults), skipping files identical
      to the original checkout so committed binaries aren't reported as build outputs.
    """
    if not sandbox.container_id:
        return list(sandbox.artifacts or [])

    if sandbox.kind == "terminal":
        archive = await session_workspace.fetch_container_archive(
            compute_backend, sandbox.container_id, "/workspace/.gitseed/artifacts"
        )
        if archive is None:
            return list(sandbox.artifacts or [])
        try:
            files = session_workspace.files_from_archive(archive, "artifacts")
        except Exception as exc:
            logger.warning("Failed to unpack artifacts for sandbox %s: %s", sandbox.id, exc)
            return list(sandbox.artifacts or [])
        candidates = [(rel, data, ".gitseed/artifacts") for rel, data in files.items() if data]
        return _store_artifacts(sandbox, candidates, sandbox.artifacts)

    patterns = [g.strip() for g in (sandbox.artifact_globs or []) if g and g.strip()]
    if not patterns:
        patterns = list(_DEFAULT_ARTIFACT_GLOBS)

    archive = await session_workspace.fetch_container_archive(
        compute_backend, sandbox.container_id, "/workspace"
    )
    if archive is None:
        return list(sandbox.artifacts or [])

    baseline = session_workspace.load_baseline(sandbox.id) or {}
    job_candidates: list[tuple[str, bytes, str]] = []
    try:
        files = session_workspace.files_from_archive(archive, "workspace")
    except Exception as exc:
        logger.warning("Failed to unpack artifacts for sandbox %s: %s", sandbox.id, exc)
        return list(sandbox.artifacts or [])
    for rel_path, data in files.items():
        if not data or session_workspace.is_ignored_path(rel_path):
            continue
        rule = _first_matching_glob(rel_path, patterns)
        if rule is None:
            continue
        # Skip files that existed unchanged in the checkout (e.g. committed APKs)
        if baseline.get(rel_path) == hashlib.sha256(data).hexdigest():
            continue
        job_candidates.append((rel_path, data, rule))
    return _store_artifacts(sandbox, job_candidates, sandbox.artifacts)


async def reconcile_sandbox_sessions(session: AsyncSession) -> None:
    """Reconcile active sandbox containers, collect artifacts on exit, and sweep expired sessions/artifacts."""
    now = datetime.now(UTC)

    active_rows = (
        await session.execute(
            select(SandboxSession)
            .where(
                col(SandboxSession.status).in_([s.value for s in SANDBOX_ACTIVE_STATUSES])
            )
            .with_for_update(skip_locked=True)
        )
    ).scalars().all()

    for sbx in active_rows:
        backend = (
            await session.get(Backend, sbx.compute_backend_id)
            if sbx.compute_backend_id
            else None
        )
        if backend is None or not sbx.container_id:
            sbx.status = SandboxStatus.FAILED
            sbx.error = "Compute backend or container reference missing"
            sbx.stopped_at = now
            continue

        # Interactive terminals stop after a period of inactivity
        idle_ref = sbx.last_activity_at or sbx.started_at
        if (
            sbx.kind == "terminal"
            and idle_ref is not None
            and now - idle_ref >= timedelta(minutes=settings.SESSION_IDLE_MINUTES)
        ):
            artifacts = await _collect_container_artifacts(backend, sbx)
            sbx.artifacts = artifacts
            await session_workspace.snapshot_k8s_workspace(backend, sbx.container_id, sbx.id)
            await close_session_vscode(f"sandbox:{sbx.id}")
            await stop_and_remove_docker_container(backend, sbx.container_id)
            sbx.status = SandboxStatus.STOPPED
            sbx.error = f"Stopped after {settings.SESSION_IDLE_MINUTES} minutes of inactivity"
            sbx.stopped_at = now
            continue

        # Check TTL expiration (collect artifacts first so they aren't lost)
        if sbx.expires_at and now >= sbx.expires_at:
            logs = await fetch_docker_container_logs(backend, sbx.container_id)
            if logs:
                sbx.log_tail = logs[-50000:]
            sbx.artifacts = await _collect_container_artifacts(backend, sbx)
            await session_workspace.snapshot_k8s_workspace(backend, sbx.container_id, sbx.id)
            await close_session_vscode(f"sandbox:{sbx.id}")
            await stop_and_remove_docker_container(backend, sbx.container_id)
            sbx.status = SandboxStatus.TIMED_OUT
            sbx.error = (
                f"Session reached its {settings.SESSION_MAX_HOURS}h limit"
                if sbx.kind == "terminal"
                else f"Sandbox session exceeded TTL ({settings.SANDBOX_DEFAULT_TTL_MINUTES}m)"
            )
            sbx.stopped_at = now
            continue

        state = await inspect_docker_container_state(backend, sbx.container_id)
        if not state.get("exists"):
            await close_session_vscode(f"sandbox:{sbx.id}")
            sbx.status = SandboxStatus.FAILED
            sbx.error = sbx.error or "Sandbox container exited and was removed"
            sbx.stopped_at = now
            continue

        logs = await fetch_docker_container_logs(backend, sbx.container_id)
        if logs:
            sbx.log_tail = logs[-50000:]

        if state.get("running"):
            if sbx.status == SandboxStatus.STARTING:
                sbx.status = SandboxStatus.RUNNING
            continue

        # Container finished! Collect artifacts before removing the container
        exit_code = int(state.get("exit_code") or 0)
        sbx.exit_code = exit_code
        artifacts = await _collect_container_artifacts(backend, sbx)
        if artifacts:
            sbx.artifacts = artifacts
        await session_workspace.snapshot_k8s_workspace(backend, sbx.container_id, sbx.id)
        await close_session_vscode(f"sandbox:{sbx.id}")
        await stop_and_remove_docker_container(backend, sbx.container_id)

        if exit_code == 0:
            sbx.status = SandboxStatus.SUCCEEDED
        else:
            sbx.status = SandboxStatus.FAILED
            sbx.error = f"Command exited with code {exit_code}"
        sbx.stopped_at = now

    # Terminals that were never used (no input, no artifacts, not pushed) disappear once stopped
    unused_terminals = (
        await session.execute(
            select(SandboxSession).where(
                SandboxSession.kind == "terminal",
                col(SandboxSession.status).not_in([s.value for s in SANDBOX_ACTIVE_STATUSES]),
                col(SandboxSession.last_activity_at).is_(None),
                col(SandboxSession.pushed_seed_id).is_(None),
            )
        )
    ).scalars().all()
    for unused in unused_terminals:
        if unused.artifacts:
            continue
        if unused.compute_backend_id:
            unused_backend = await session.get(Backend, unused.compute_backend_id)
            if unused_backend is not None:
                await remove_docker_session_workspace_volume(unused_backend, unused.id)
        session_workspace.remove_session_data(unused.id)
        await session.delete(unused)

    # Sweep artifact directories older than SANDBOX_ARTIFACT_RETENTION_DAYS (default 7 days)
    cutoff = now - timedelta(days=settings.SANDBOX_ARTIFACT_RETENTION_DAYS)
    expired_rows = (
        await session.execute(
            select(SandboxSession).where(
                func.coalesce(col(SandboxSession.stopped_at), col(SandboxSession.started_at)) < cutoff,
                col(SandboxSession.artifacts) != [],
            )
        )
    ).scalars().all()
    for old_sbx in expired_rows:
        art_dir = Path(settings.ARTIFACTS_DIR) / old_sbx.id
        if art_dir.exists():
            shutil.rmtree(art_dir, ignore_errors=True)
        old_sbx.artifacts = []


async def list_sandbox_sessions(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    limit: int = 25,
) -> list[SandboxSession]:
    project = await projects_service.get_project(session, actor, project_id_or_slug)
    has_active = (
        await session.scalar(
            select(SandboxSession.id)
            .where(
                SandboxSession.project_id == project.id,
                col(SandboxSession.status).in_(
                    [SandboxStatus.STARTING.value, SandboxStatus.RUNNING.value]
                ),
            )
            .limit(1)
        )
    ) is not None
    if has_active:
        await reconcile_sandbox_sessions(session)
        await session.commit()

    rows = (
        await session.execute(
            select(SandboxSession)
            .where(SandboxSession.project_id == project.id)
            .order_by(col(SandboxSession.started_at).desc())
            .limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def get_sandbox_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
) -> SandboxSession:
    project = await projects_service.get_project(session, actor, project_id_or_slug)
    sbx = await session.get(SandboxSession, sandbox_id)
    if sbx is None or sbx.project_id != project.id:
        raise NotFound("Sandbox session not found")

    if sbx.status in (SandboxStatus.STARTING, SandboxStatus.RUNNING):
        await reconcile_sandbox_sessions(session)
        await session.commit()
        await session.refresh(sbx)

    return sbx


async def create_sandbox_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    repository_id: int | None = None,
    seed_id: str | None = None,
    ref: str | None = None,
    image: str = "python:3.12-slim",
    setup_command: str | None = None,
    command: str = "",
    artifact_globs: list[str] | None = None,
    initial_stdin: str | None = None,
    as_root: bool = False,
    kind: str = "job",
    title: str | None = None,
) -> SandboxSession:
    project = await _require_project_developer(session, actor, project_id_or_slug)
    await reconcile_sandbox_sessions(session)

    kind = "terminal" if kind == "terminal" else "job"
    if kind == "job" and not (command or "").strip():
        raise Invalid("A command is required to run a sandbox job")

    # Root is limited to Org Admins and Project Maintainers, and to terminal sessions
    privileged_root = False
    if as_root and kind == "terminal":
        if actor.org_role == OrgRole.ADMIN:
            privileged_root = True
        else:
            role = await projects_service.get_member_role(session, project.id, actor.id)
            if role == ProjectRole.MAINTAINER:
                privileged_root = True
        if not privileged_root:
            raise Forbidden("Root terminals require Org Admin or Project Maintainer privileges")

    # Enforce dedicated max_sandbox_sessions concurrency limit
    org = await session.get(OrgSettings, 1)
    max_sandboxes = org.max_sandbox_sessions if org else 2
    active_count = int(
        (
            await session.scalar(
                select(func.count())
                .select_from(SandboxSession)
                .where(
                    col(SandboxSession.status).in_(
                        [s.value for s in SANDBOX_ACTIVE_STATUSES]
                    )
                )
            )
        )
        or 0
    )
    if active_count >= max_sandboxes:
        raise Invalid(
            f"Maximum concurrent sandbox sessions ({max_sandboxes}) reached. "
            "Stop an active sandbox or wait for it to finish."
        )

    repo_rows = (
        await session.execute(
            select(ProjectRepository)
            .where(ProjectRepository.project_id == project.id)
            .order_by(
                col(ProjectRepository.is_primary).desc(),
                col(ProjectRepository.id).asc(),
            )
        )
    ).scalars().all()
    if not repo_rows:
        raise Invalid("Attach a Git repository to this project before running a sandbox")

    repo = next(
        (r for r in repo_rows if repository_id is not None and r.id == repository_id),
        repo_rows[0],
    )
    git_backend = await session.get(Backend, repo.backend_id)
    if git_backend is None or git_backend.type != BackendType.GIT:
        raise Invalid("Git backend for repository not found")

    seed_obj: Seed | None = None
    if seed_id:
        seed_obj = await session.get(Seed, seed_id)
        if seed_obj is None or seed_obj.project_id != project.id:
            seed_obj = None

    secrets_map = decrypt_secrets(git_backend.secrets_encrypted)
    token = await resolve_git_backend_token(
        git_backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=False,
    )
    default_branch = (repo.default_branch or "main").strip()
    requested_ref = (
        ref.strip()
        if ref and ref.strip()
        else (f"gitseed/seed-{seed_obj.number}" if seed_obj else default_branch)
    )

    workspace_tar, resolved_ref, head_sha = await asyncio.to_thread(
        _clone_and_pack_workspace_tar,
        backend=git_backend,
        full_name=repo.full_name,
        token=token,
        ref=requested_ref,
        default_branch=default_branch,
    )

    compute_backend = await _select_docker_compute_backend(session, project=project)
    now = datetime.now(UTC)
    expires_at = now + (
        timedelta(hours=settings.SESSION_MAX_HOURS)
        if kind == "terminal"
        else timedelta(minutes=settings.SANDBOX_DEFAULT_TTL_MINUTES)
    )

    cleaned_globs = [
        g.strip() for g in (artifact_globs or []) if isinstance(g, str) and g.strip()
    ]
    detected_cfg: dict[str, Any] | None = None
    if not (image or "").strip() or (kind == "job" and not cleaned_globs):
        # Prefer the project's own config (settings / .gitseed/run.json / detected stack)
        try:
            detected_cfg = await get_sandbox_run_config(
                session, actor, project.id, repo_id=repo.id, ref=resolved_ref
            )
        except Exception:
            detected_cfg = None
    if not (image or "").strip():
        image = str((detected_cfg or {}).get("image") or "python:3.12-slim")
    if kind == "job" and not cleaned_globs and detected_cfg:
        cleaned_globs = [
            str(g).strip() for g in (detected_cfg.get("artifact_globs") or []) if str(g).strip()
        ]
    sbx = SandboxSession(
        project_id=project.id,
        seed_id=seed_obj.id if seed_obj else None,
        repository_id=repo.id,
        ref=resolved_ref,
        head_sha=head_sha,
        image=image.strip() or "python:3.12-slim",
        setup_command=setup_command.strip() if setup_command and setup_command.strip() else None,
        command=(command or "").strip() or ("interactive shell" if kind == "terminal" else ""),
        artifact_globs=cleaned_globs if kind == "job" else [],
        as_root=privileged_root,
        status=SandboxStatus.RUNNING,
        compute_backend_id=compute_backend.id,
        started_by=actor.id,
        started_at=now,
        expires_at=expires_at,
        kind=kind,
        title=(title or "").strip()[:160] or None,
    )
    session.add(sbx)
    await session.flush()
    session_workspace.save_baseline(sbx.id, workspace_tar)

    container_id = await _spawn_sandbox_container(
        compute_backend=compute_backend,
        sandbox=sbx,
        workspace_tar=workspace_tar,
        initial_stdin=initial_stdin,
    )
    sbx.container_id = container_id

    record(
        session,
        type=ActivityType.SANDBOX_STARTED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        seed_id=sbx.seed_id,
        data={
            "sandbox_id": sbx.id,
            "ref": sbx.ref,
            "head_sha": sbx.head_sha[:7] if sbx.head_sha else "",
            "image": sbx.image,
            "command": sbx.command,
        },
    )
    await session.commit()
    await session.refresh(sbx)
    return sbx


async def send_sandbox_input(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
    *,
    input_text: str = "",
    append_newline: bool = True,
    eof: bool = False,
) -> SandboxSession:
    """Write interactive stdin bytes (and/or EOF) into the running sandbox container's `/tmp/.gitseed_stdin` FIFO."""
    project = await _require_project_developer(session, actor, project_id_or_slug)
    sbx = await session.get(SandboxSession, sandbox_id)
    if sbx is None or sbx.project_id != project.id:
        raise NotFound("Sandbox session not found")
    if sbx.status not in SANDBOX_ACTIVE_STATUSES or not sbx.container_id:
        raise Invalid("Sandbox session is not running")

    backend = (
        await session.get(Backend, sbx.compute_backend_id)
        if sbx.compute_backend_id
        else None
    )
    if backend is None:
        raise Invalid("Compute backend for sandbox session not found")

    payload_str = input_text
    sbx.last_activity_at = datetime.now(UTC)
    if payload_str and append_newline and not payload_str.endswith("\n"):
        payload_str = f"{payload_str}\n"
    elif not payload_str and append_newline and not eof:
        payload_str = "\n"

    script_parts: list[str] = []
    if payload_str:
        b64_data = base64.b64encode(payload_str.encode("utf-8")).decode("ascii")
        # Echo interactive input into container stdout (/proc/1/fd/1) and feed the FIFO
        script_parts.append(
            f"printf '%s' '{b64_data}' | base64 -d >> /proc/1/fd/1 2>/dev/null || true; "
            f"printf '%s' '{b64_data}' | base64 -d > /tmp/.gitseed_stdin"
        )
    if eof:
        script_parts.append(
            "touch /tmp/.gitseed_eof && pkill -f 'sleep 1' 2>/dev/null || true"
        )

    if script_parts:
        exec_cmd = ["sh", "-c", " ; ".join(script_parts)]
        if is_k8s_backend(backend):
            rc, _, _ = await k8s_exec_command(backend, sbx.container_id, exec_cmd, timeout=15.0)
            if rc != 0:
                raise Invalid(f"Failed to send input to Kubernetes sandbox Pod (exit {rc})")
        else:
            client, base_url = _docker_client_for_backend(backend)
            async with client:
                exec_resp = await client.post(
                    f"{base_url}/containers/{quote(sbx.container_id, safe='')}/exec",
                    json={
                        "AttachStdin": False,
                        "AttachStdout": True,
                        "AttachStderr": True,
                        "Cmd": exec_cmd,
                    },
                )
                if exec_resp.status_code not in (200, 201):
                    raise Invalid(
                        f"Failed to send input to sandbox container (HTTP {exec_resp.status_code})"
                    )
                exec_id = str(exec_resp.json().get("Id") or "")
                if exec_id:
                    await client.post(
                        f"{base_url}/exec/{quote(exec_id, safe='')}/start",
                        json={"Detach": True, "Tty": False},
                    )

    await reconcile_sandbox_sessions(session)
    await session.commit()
    await session.refresh(sbx)
    return sbx


async def stop_sandbox_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
) -> SandboxSession:
    project = await _require_project_developer(session, actor, project_id_or_slug)
    sbx = await session.get(SandboxSession, sandbox_id)
    if sbx is None or sbx.project_id != project.id:
        raise NotFound("Sandbox session not found")

    if sbx.status in SANDBOX_ACTIVE_STATUSES:
        backend = (
            await session.get(Backend, sbx.compute_backend_id)
            if sbx.compute_backend_id
            else None
        )
        await close_session_vscode(f"sandbox:{sbx.id}")
        if backend and sbx.container_id:
            logs = await fetch_docker_container_logs(backend, sbx.container_id)
            if logs:
                sbx.log_tail = logs[-50000:]
            artifacts = await _collect_container_artifacts(backend, sbx)
            if artifacts:
                sbx.artifacts = artifacts
            await session_workspace.snapshot_k8s_workspace(backend, sbx.container_id, sbx.id)
            await stop_and_remove_docker_container(backend, sbx.container_id)
        sbx.status = SandboxStatus.STOPPED
        sbx.stopped_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(sbx)

    return sbx


async def resolve_sandbox_artifact_path(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
    artifact_rel_path: str,
) -> tuple[Path, str]:
    sbx = await get_sandbox_session(session, actor, project_id_or_slug, sandbox_id)
    clean_rel = artifact_rel_path.strip().lstrip("/")
    matched = next(
        (
            a
            for a in (sbx.artifacts or [])
            if isinstance(a, dict)
            and (a.get("rel_path") == clean_rel or a.get("name") == clean_rel)
        ),
        None,
    )
    if matched is None:
        raise NotFound("Sandbox artifact not found")

    base_dir = (Path(settings.ARTIFACTS_DIR) / sbx.id).resolve()
    target = (base_dir / str(matched["rel_path"])).resolve()
    if not target.is_relative_to(base_dir) or not target.is_file():
        raise NotFound("Artifact file is no longer available on disk")

    return target, str(matched.get("name") or target.name)



# ---------------------------------------------------------------------------
# Interactive terminal sessions: rename, artifacts, delete, PTY, push → seed
# ---------------------------------------------------------------------------


async def _get_project_sandbox(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str, *, write: bool
) -> tuple[Project, SandboxSession]:
    project = (
        await _require_project_developer(session, actor, project_id_or_slug)
        if write
        else await projects_service.get_project(session, actor, project_id_or_slug)
    )
    sbx = await session.get(SandboxSession, sandbox_id)
    if sbx is None or sbx.project_id != project.id:
        raise NotFound("Sandbox session not found")
    return project, sbx


async def _running_backend(session: AsyncSession, sbx: SandboxSession) -> Backend:
    if sbx.status not in SANDBOX_ACTIVE_STATUSES or not sbx.container_id:
        raise Invalid("The session is not running")
    backend = await session.get(Backend, sbx.compute_backend_id) if sbx.compute_backend_id else None
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    return backend


async def update_sandbox_session(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str, *, title: str | None
) -> SandboxSession:
    _, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    if title is not None and title.strip():
        sbx.title = title.strip()[:160]
    await session.commit()
    await session.refresh(sbx)
    return sbx


async def touch_sandbox_activity(sandbox_id: str) -> None:
    """Record terminal activity (keeps idle auto-stop from firing, marks the session as used)."""
    async with AsyncSessionLocal() as db:
        sbx = await db.get(SandboxSession, sandbox_id)
        if sbx is not None:
            sbx.last_activity_at = datetime.now(UTC)
            await db.commit()


async def save_sandbox_artifact(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str, *, path: str
) -> SandboxSession:
    """Explicitly keep a file (or folder) from a running session's `/workspace`."""
    _, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    backend = await _running_backend(session, sbx)
    rel = path.strip().removeprefix("/workspace/").lstrip("/")
    if not rel or ".." in PurePosixPath(rel).parts:
        raise Invalid("Path must be inside /workspace")
    archive = await session_workspace.fetch_container_archive(
        backend, sbx.container_id or "", f"/workspace/{rel}"
    )
    if archive is None:
        raise NotFound(f"'{rel}' was not found in the session workspace")
    files = session_workspace.files_from_archive(archive, "")
    parent = str(PurePosixPath(rel).parent)
    candidates = [
        ((f"{parent}/{name}" if parent not in ("", ".") else name), data, "saved")
        for name, data in files.items()
        if data and not session_workspace.is_ignored_path(name)
    ]
    if not candidates:
        raise Invalid(f"Nothing to save at '{rel}'")
    sbx.artifacts = _store_artifacts(sbx, candidates, sbx.artifacts)
    sbx.last_activity_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(sbx)
    return sbx


async def delete_sandbox_artifact(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str, artifact_rel_path: str
) -> SandboxSession:
    _, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    clean_rel = artifact_rel_path.strip().lstrip("/")
    remaining = [a for a in (sbx.artifacts or []) if isinstance(a, dict) and a.get("rel_path") != clean_rel]
    if len(remaining) == len(sbx.artifacts or []):
        raise NotFound("Sandbox artifact not found")
    base_dir = (Path(settings.ARTIFACTS_DIR) / sbx.id).resolve()
    target = (base_dir / clean_rel).resolve()
    if target.is_relative_to(base_dir) and target.is_file():
        target.unlink(missing_ok=True)
    sbx.artifacts = remaining
    await session.commit()
    await session.refresh(sbx)
    return sbx


async def delete_sandbox_session(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str
) -> None:
    """Stop the container (if running) and permanently delete the session and its artifacts."""
    _, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    await close_session_vscode(f"sandbox:{sbx.id}")
    if sbx.compute_backend_id:
        backend = await session.get(Backend, sbx.compute_backend_id)
        if backend is not None:
            if sbx.container_id:
                try:
                    await stop_and_remove_docker_container(backend, sbx.container_id)
                except Exception:
                    pass
            try:
                await remove_docker_session_workspace_volume(backend, sbx.id)
            except Exception:
                pass
    await close_live_pty(f"sandbox:{sbx.id}")
    session_workspace.remove_session_data(sbx.id)
    await session.delete(sbx)
    await session.commit()


async def open_sandbox_vscode(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
    *,
    theme: str | None = None,
) -> dict[str, Any]:
    """Ensure on-demand VS Code Web (`openvscode-server`) is running for a sandbox session."""
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    backend = await _running_backend(session, sbx)
    res = await ensure_session_vscode(
        session_key=f"sandbox:{sbx.id}",
        backend=backend,
        container_id=sbx.container_id or "",
        kind="terminal",
        session_id=sbx.id,
        project_id=project.id,
        ref=sbx.ref or "main",
        head_sha=sbx.head_sha or "",
        theme=theme,
        on_activity=lambda: touch_sandbox_activity(sbx.id),
    )
    sbx.last_activity_at = datetime.now(UTC)
    await session.commit()
    return res


async def prepare_sandbox_pty(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str
) -> tuple[SandboxSession, Backend, bool]:
    """Return (session, compute backend, can_write) for attaching a PTY to a terminal session."""
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=False)
    backend = await _running_backend(session, sbx)
    can_write = actor.org_role == OrgRole.ADMIN or (
        await projects_service.get_member_role(session, project.id, actor.id)
        in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER)
    )
    return sbx, backend, bool(can_write and project.archived_at is None)


def sandbox_transcript_path(sandbox_id: str) -> Path:
    return session_workspace.session_data_dir(sandbox_id) / ".transcript.cast"


def _transcript_tail_text(sandbox_id: str, limit: int = 6000) -> str:
    p = sandbox_transcript_path(sandbox_id)
    if not p.is_file():
        return ""
    chunks: list[str] = []
    try:
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, list) and len(ev) >= 3 and ev[1] == "o":
                chunks.append(str(ev[2]))
    except Exception:
        return ""
    return session_workspace.strip_ansi("".join(chunks))[-limit:]


async def _sandbox_changes(
    session: AsyncSession, project: Project, sbx: SandboxSession
) -> tuple[list[dict[str, str]], dict[str, bytes]]:
    backend = await session.get(Backend, sbx.compute_backend_id) if sbx.compute_backend_id else None
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    baseline = session_workspace.load_baseline(sbx.id)
    if baseline is None:
        raise Invalid("This session started before change tracking was available; start a new terminal")
    container_id = sbx.container_id if sbx.status in SANDBOX_ACTIVE_STATUSES else ""
    files = await session_workspace.export_workspace(
        backend, container_id or "", session_id=sbx.id
    )
    changes = session_workspace.diff_workspace(
        files, baseline, session_workspace.project_blocked_paths(project)
    )
    return changes, files


def _sandbox_label(sbx: SandboxSession) -> str:
    return sbx.title or f"terminal session {sbx.id}"


async def list_sandbox_files(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str
) -> dict[str, Any]:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=False)
    backend = await session.get(Backend, sbx.compute_backend_id) if sbx.compute_backend_id else None
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    container_id = sbx.container_id if sbx.status in SANDBOX_ACTIVE_STATUSES else ""
    return await session_workspace.list_workspace_files(
        backend, container_id or "", sbx.id, session_workspace.project_blocked_paths(project)
    )


async def read_sandbox_file(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str, *, path: str
) -> dict[str, Any]:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=False)
    backend = await session.get(Backend, sbx.compute_backend_id) if sbx.compute_backend_id else None
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    container_id = sbx.container_id if sbx.status in SANDBOX_ACTIVE_STATUSES else ""
    return await session_workspace.read_workspace_file(
        backend, container_id or "", sbx.id, path, session_workspace.project_blocked_paths(project)
    )


async def write_sandbox_file(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
    *,
    path: str,
    content: str,
) -> dict[str, Any]:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    backend = await _running_backend(session, sbx)
    res = await session_workspace.write_workspace_file(
        backend,
        sbx.container_id or "",
        sbx.id,
        path,
        content,
        session_workspace.project_blocked_paths(project),
    )
    sbx.last_activity_at = datetime.now(UTC)
    await session.commit()
    return res


async def delete_sandbox_file(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str, *, path: str
) -> None:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    backend = await _running_backend(session, sbx)
    await session_workspace.delete_workspace_file(
        backend, sbx.container_id or "", path, session_workspace.project_blocked_paths(project)
    )
    sbx.last_activity_at = datetime.now(UTC)
    await session.commit()


async def discard_sandbox_changes(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    backend = await _running_backend(session, sbx)
    res = await session_workspace.discard_workspace_changes(
        backend,
        sbx.container_id or "",
        sbx.id,
        session_workspace.project_blocked_paths(project),
        path=path,
    )
    sbx.last_activity_at = datetime.now(UTC)
    await session.commit()
    return res


async def sandbox_push_draft(
    session: AsyncSession, actor: User, project_id_or_slug: str, sandbox_id: str
) -> dict[str, Any]:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    changes, files = await _sandbox_changes(session, project, sbx)
    title, summary, criteria, drafted_by = await session_workspace.draft_push_summary(
        session,
        project,
        session_label=_sandbox_label(sbx),
        context_text=_transcript_tail_text(sbx.id),
        changes=changes,
        files=files,
    )
    linked = await session_workspace.resolve_linked_seed_info(
        session,
        project,
        seed_id=sbx.seed_id or sbx.pushed_seed_id,
        ref=sbx.ref,
    )
    return {
        "title": title,
        "summary": summary,
        "acceptance_criteria": criteria,
        "changed_files": changes,
        "branch": format_seed_branch(project.slug, (project.seed_counter or 0) + 1),
        "drafted_by": drafted_by,
        **linked,
    }


async def push_sandbox_to_seed(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    sandbox_id: str,
    *,
    title: str,
    summary: str,
    acceptance_criteria: list[str],
    target_seed_id: str | None = None,
) -> dict[str, Any]:
    project, sbx = await _get_project_sandbox(session, actor, project_id_or_slug, sandbox_id, write=True)
    changes, files = await _sandbox_changes(session, project, sbx)
    result = await session_workspace.push_session_to_seed(
        session,
        actor,
        project,
        repository_id=sbx.repository_id,
        base_ref=sbx.ref,
        head_sha=sbx.head_sha,
        changes=changes,
        files=files,
        title=title,
        summary=summary,
        acceptance_criteria=acceptance_criteria,
        session_label=_sandbox_label(sbx),
        target_seed_id=target_seed_id,
    )
    fresh = await session.get(SandboxSession, sandbox_id)
    if fresh is not None:
        fresh.pushed_seed_id = result["seed_id"]
        fresh.last_activity_at = datetime.now(UTC)
        await session.commit()
    return result


def artifacts_expire_at(sbx: SandboxSession) -> datetime | None:
    if not sbx.artifacts:
        return None
    ref = sbx.stopped_at or sbx.started_at
    return ref + timedelta(days=settings.SANDBOX_ARTIFACT_RETENTION_DAYS) if ref else None
