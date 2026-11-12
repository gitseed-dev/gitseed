"""Shared helpers for interactive sessions (terminal sandboxes and agent chats).

- Baseline manifests: a SHA-256 map of the cloned checkout, written outside the
  container at clone time, so we can tell which files a session created or changed.
- Workspace export/diff: copy `/workspace` out of a running container and compare
  it with the baseline.
- Push → seed: Core (never the container) applies the session's changes onto a
  fresh clone, commits to the seed branch, pushes with Core's token, and opens a
  seed at Review.
"""

from __future__ import annotations

import asyncio
import fnmatch
import hashlib
import io
import json
import logging
import re
import shutil
import subprocess
import tarfile
import tempfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import quote

import httpx
from gitseed_common.enums import (
    RUN_ACTIVE_STATUSES,
    ActivityType,
    ActorType,
    RunStatus,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment, SeedTask
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets
from gitseed_core.services import git_browse
from gitseed_core.services import seeds as seeds_service
from gitseed_core.services.activity import record
from gitseed_core.services.compute_runtime import (
    _docker_client_for_backend,
    is_k8s_backend,
    k8s_exec_command,
    k8s_fetch_archive,
    k8s_put_archive,
    session_workspace_volume_name,
    stop_and_remove_docker_container,
)
from gitseed_core.services.coordinator import _resolve_coordinator_model
from gitseed_core.services.errors import Invalid
from gitseed_core.services.gateway import (
    _litellm_headers,
    finalize_run_litellm_key,
    sync_backend_to_litellm,
)
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.git_merge import (
    ensure_seed_pull_request,
    format_seed_branch,
    is_auto_create_pr_enabled,
)
from gitseed_core.services.policies import get_effective_stage_policy

logger = logging.getLogger("uvicorn.error")

# Never treated as session output / pushed changes
_IGNORED_PARTS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".gitseed-worktrees",
    ".opencode",
    ".claude",
    ".gemini",
    ".codex",
    ".grok",
    ".gitseed-vscode",
    ".openvscode-server",
    ".vscode-server",
    ".vscode-remote",
}
_IGNORED_PREFIXES = (".gitseed/",)
_IGNORED_FILES = {
    "opencode.json",
    ".claude.json",
    ".aider.tags.cache.v3",
    ".aider.input.history",
    ".aider.chat.history.md",
}
MAX_PUSH_FILES = 500
MAX_PUSH_BYTES = 50 * 1024 * 1024
_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07]*(\x07|\x1b\\)|\x1b[@-Z\\-_]")


def session_data_dir(owner_id: str) -> Path:
    """Per-session directory on the Core volume (outside any container)."""
    return Path(settings.ARTIFACTS_DIR) / owner_id


def is_ignored_path(rel_path: str) -> bool:
    name = PurePosixPath(rel_path).name
    if rel_path in _IGNORED_FILES or name in _IGNORED_FILES:
        return True
    parts = PurePosixPath(rel_path).parts
    if any(p in _IGNORED_PARTS for p in parts):
        return True
    return rel_path.startswith(_IGNORED_PREFIXES)


def strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text).replace("\r", "")


# ---------------------------------------------------------------------------
# Baseline manifest
# ---------------------------------------------------------------------------


def manifest_from_tar(tar_bytes: bytes) -> dict[str, str]:
    """Return `{rel_path: sha256}` for regular files in a workspace tar (paths relative to repo root)."""
    manifest: dict[str, str] = {}
    with tarfile.open(fileobj=io.BytesIO(tar_bytes), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            rel = member.name.lstrip("./").removeprefix("workspace/").strip("/")
            if not rel or is_ignored_path(rel):
                continue
            f_obj = tar.extractfile(member)
            if f_obj is None:
                continue
            manifest[rel] = hashlib.sha256(f_obj.read()).hexdigest()
    return manifest


def init_clean_git_baseline(repo_dir: Path, *, ref: str = "main", head_sha: str = "") -> None:
    """Replace any cloned `.git` with a clean, credential-free baseline commit.

    Stripping the cloned `.git` first guarantees zero remote URLs, auth tokens, or reflogs
    enter the session container, while re-initializing a single baseline commit gives
    VS Code (`vscode.git`) and CLI tools inside `/workspace` full gutter diffs, file tree
    M/U/D badges, and side-by-side diff views against the session's starting state.
    """
    git_dir = repo_dir / ".git"
    if git_dir.exists():
        shutil.rmtree(git_dir, ignore_errors=True)
    branch = (ref or "main").strip() or "main"
    short_sha = head_sha[:7] if head_sha else "initial"
    env = {
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "HOME": str(repo_dir),
        "GIT_AUTHOR_NAME": "GitSeed",
        "GIT_AUTHOR_EMAIL": "session@gitseed.local",
        "GIT_COMMITTER_NAME": "GitSeed",
        "GIT_COMMITTER_EMAIL": "session@gitseed.local",
    }
    try:
        subprocess.run(
            ["git", "init", "-q", "-b", branch],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            check=False,
            timeout=15,
        )
        subprocess.run(
            ["git", "config", "user.name", "GitSeed"],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            check=False,
            timeout=10,
        )
        subprocess.run(
            ["git", "config", "user.email", "session@gitseed.local"],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            check=False,
            timeout=10,
        )
        subprocess.run(
            ["git", "add", "-A"],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            check=False,
            timeout=30,
        )
        subprocess.run(
            [
                "git",
                "commit",
                "-q",
                "--allow-empty",
                "--no-verify",
                "-m",
                f"Baseline ({branch} @ {short_sha})",
            ],
            cwd=str(repo_dir),
            env=env,
            capture_output=True,
            check=False,
            timeout=30,
        )
        if git_dir.exists():
            for p in [git_dir, *git_dir.rglob("*")]:
                try:
                    p.chmod(0o777 if p.is_dir() else 0o666)
                except OSError:
                    pass
    except Exception as exc:
        logger.debug("Could not initialize baseline git repo in %s: %s", repo_dir, exc)


def build_baseline_git_tar(
    owner_id: str, *, ref: str = "main", head_sha: str = ""
) -> bytes | None:
    """Build a tar archive containing only `workspace/.git` from the session's `.baseline.tar`.

    Used to backfill `/workspace/.git` in already-running sessions without overwriting
    any working-tree files that the user or agent has already modified in `/workspace`.
    """
    baseline_tar_path = session_data_dir(owner_id) / ".baseline.tar"
    if not baseline_tar_path.is_file():
        return None
    try:
        baseline_files = files_from_archive(baseline_tar_path.read_bytes(), "workspace")
        with tempfile.TemporaryDirectory(prefix="gitseed-basegit-") as tmp_dir:
            tmp_path = Path(tmp_dir)
            for rel, data in baseline_files.items():
                if is_ignored_path(rel):
                    continue
                dest = tmp_path / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
            init_clean_git_baseline(tmp_path, ref=ref, head_sha=head_sha)
            git_dir = tmp_path / ".git"
            if not git_dir.is_dir():
                return None
            buf = io.BytesIO()
            with tarfile.open(fileobj=buf, mode="w") as tar:
                for p in [git_dir, *git_dir.rglob("*")]:
                    rel_git = p.relative_to(tmp_path).as_posix()
                    info = tar.gettarinfo(str(p), arcname=f"workspace/{rel_git}")
                    info.uid = 1000
                    info.gid = 1000
                    info.uname = "runner"
                    info.gname = "runner"
                    info.mode = 0o777 if p.is_dir() else 0o666
                    if p.is_file():
                        with p.open("rb") as f_obj:
                            tar.addfile(info, f_obj)
                    else:
                        tar.addfile(info)
            return buf.getvalue()
    except Exception as exc:
        logger.debug("Could not build baseline .git tar for %s: %s", owner_id, exc)
        return None


def save_baseline(owner_id: str, tar_bytes: bytes) -> None:
    try:
        manifest = manifest_from_tar(tar_bytes)
        d = session_data_dir(owner_id)
        d.mkdir(parents=True, exist_ok=True)
        (d / ".baseline.json").write_text(json.dumps(manifest), encoding="utf-8")
        (d / ".baseline.tar").write_bytes(tar_bytes)
    except Exception as exc:
        logger.warning("Could not record baseline manifest for %s: %s", owner_id, exc)


def load_baseline(owner_id: str) -> dict[str, str] | None:
    p = session_data_dir(owner_id) / ".baseline.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def load_baseline_files(owner_id: str) -> dict[str, bytes]:
    p = session_data_dir(owner_id) / ".baseline.tar"
    if not p.is_file():
        return {}
    try:
        return files_from_archive(p.read_bytes(), "workspace")
    except Exception:
        return {}



# ---------------------------------------------------------------------------
# Workspace export & diff
# ---------------------------------------------------------------------------


async def fetch_container_archive(
    compute_backend: Backend, container_id: str, path: str, *, timeout: float = 120.0
) -> bytes | None:
    """GET a tar of `path` from a container or Kubernetes Pod."""
    if is_k8s_backend(compute_backend):
        return await k8s_fetch_archive(compute_backend, container_id, path, timeout=timeout)

    client, base_url = _docker_client_for_backend(compute_backend)
    try:
        async with client:
            resp = await client.get(
                f"{base_url}/containers/{quote(container_id, safe='')}/archive",
                params={"path": path},
                timeout=timeout,
            )
            if resp.status_code != 200:
                return None
            return resp.content
    except Exception as exc:
        logger.debug("Archive fetch of %s from %s failed: %s", path, container_id[:12], exc)
        return None


async def snapshot_k8s_workspace(
    compute_backend: Backend, container_id: str | None, session_id: str
) -> None:
    """Persist a snapshot of `/workspace` from a running Kubernetes session Pod before removal."""
    if not container_id or not is_k8s_backend(compute_backend):
        return
    try:
        archive = await fetch_container_archive(compute_backend, container_id, "/workspace")
        if archive:
            d = session_data_dir(session_id)
            d.mkdir(parents=True, exist_ok=True)
            (d / ".workspace.tar").write_bytes(archive)
    except Exception as exc:
        logger.debug("K8s workspace snapshot for %s skipped: %s", session_id, exc)


def files_from_archive(archive: bytes, strip_prefix: str) -> dict[str, bytes]:
    """Return `{rel_path: bytes}` for regular files, stripping the archive's top-level dir."""
    out: dict[str, bytes] = {}
    prefix = strip_prefix.strip("/") + "/"
    with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
        for member in tar.getmembers():
            if not member.isfile():
                continue
            name = member.name.lstrip("./")
            rel = name[len(prefix):] if name.startswith(prefix) else name
            rel = rel.strip("/")
            if not rel or ".." in PurePosixPath(rel).parts or is_ignored_path(rel):
                continue
            f_obj = tar.extractfile(member)
            if f_obj is not None:
                out[rel] = f_obj.read()
    return out


async def fetch_volume_archive(
    compute_backend: Backend,
    session_id: str,
    path: str = "/workspace",
    *,
    timeout: float = 120.0,
) -> bytes | None:
    """Read a tar of `path` directly from the per-session volume `gitseed-ws-<session_id>`.

    Docker's `/containers/{id}/archive` endpoint works on a created container without
    starting it, allowing workspace export/push even when a session container is asleep.
    For Kubernetes, reads the cached `.workspace.tar` snapshot (or `.baseline.tar`).
    """
    if is_k8s_backend(compute_backend):
        snap_path = session_data_dir(session_id) / ".workspace.tar"
        if not snap_path.is_file():
            snap_path = session_data_dir(session_id) / ".baseline.tar"
        if not snap_path.is_file():
            return None
        try:
            raw_tar = snap_path.read_bytes()
            clean_target = (path or "/workspace").strip().removeprefix("/workspace").strip("/")
            if not clean_target:
                return raw_tar
            all_files = files_from_archive(raw_tar, "workspace")
            if clean_target in all_files:
                return _pack_files_tar({PurePosixPath(clean_target).name: all_files[clean_target]})
            return None
        except Exception:
            return None

    vol_name = session_workspace_volume_name(session_id)
    client, base_url = _docker_client_for_backend(compute_backend)
    try:
        async with client:
            vol_resp = await client.get(f"{base_url}/volumes/{quote(vol_name, safe='')}")
            if vol_resp.status_code != 200:
                return None
            cfg = compute_backend.config or {}
            helper_image = str(cfg.get("runner_image") or settings.RUNNER_IMAGE).strip()
            create_resp = await client.post(
                f"{base_url}/containers/create",
                json={
                    "Image": helper_image,
                    "Cmd": ["true"],
                    "Labels": {
                        "app.gitseed.managed": "true",
                        "app.gitseed.session_id": session_id,
                    },
                    "HostConfig": {
                        "Binds": [f"{vol_name}:/workspace:ro"],
                    },
                },
            )
            if create_resp.status_code not in (200, 201):
                return None
            tmp_cid = str(create_resp.json().get("Id") or "")
            if not tmp_cid:
                return None
            try:
                arch_resp = await client.get(
                    f"{base_url}/containers/{quote(tmp_cid, safe='')}/archive",
                    params={"path": path},
                    timeout=timeout,
                )
                if arch_resp.status_code == 200:
                    return arch_resp.content
                return None
            finally:
                await client.delete(
                    f"{base_url}/containers/{quote(tmp_cid, safe='')}?force=true"
                )
    except Exception as exc:
        logger.debug("Volume archive fetch for session %s failed: %s", session_id, exc)
        return None


async def export_workspace(
    compute_backend: Backend,
    container_id: str,
    *,
    session_id: str | None = None,
) -> dict[str, bytes]:
    archive = (
        await fetch_container_archive(compute_backend, container_id, "/workspace")
        if container_id
        else None
    )
    if archive is None and session_id:
        archive = await fetch_volume_archive(compute_backend, session_id, "/workspace")
    if archive is None:
        raise Invalid("Could not read the session workspace. Is the container still running?")
    return files_from_archive(archive, "workspace")


def _blocked(rel_path: str, blocked_paths: list[str]) -> bool:
    for pat in blocked_paths:
        clean = str(pat).strip().lstrip("./")
        if clean and (
            fnmatch.fnmatch(rel_path, clean)
            or rel_path.startswith(clean.rstrip("/") + "/")
        ):
            return True
    return False


def diff_workspace(
    files: dict[str, bytes],
    baseline: dict[str, str],
    blocked_paths: list[str] | None = None,
) -> list[dict[str, str]]:
    """Compare exported files with the baseline: added / modified / deleted."""
    blocked = [p for p in (blocked_paths or []) if p]
    changes: list[dict[str, str]] = []
    for rel, data in files.items():
        if is_ignored_path(rel) or _blocked(rel, blocked):
            continue
        digest = hashlib.sha256(data).hexdigest()
        prev = baseline.get(rel)
        if prev is None:
            changes.append({"path": rel, "status": "added"})
        elif prev != digest:
            changes.append({"path": rel, "status": "modified"})
    for rel in baseline:
        if rel not in files and not is_ignored_path(rel) and not _blocked(rel, blocked):
            changes.append({"path": rel, "status": "deleted"})
    changes.sort(key=lambda c: c["path"])
    return changes


def project_blocked_paths(project: Project) -> list[str]:
    cfg = project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    raw = cfg.get("blocked_paths") or []
    return [str(p) for p in raw if str(p).strip()] if isinstance(raw, list) else []


def _clean_rel_path(path: str) -> str:
    rel = path.strip().replace("\\", "/").removeprefix("/workspace/").lstrip("/")
    if not rel:
        raise Invalid("File path cannot be empty")
    parts = PurePosixPath(rel).parts
    if any(p in ("..", ".") for p in parts):
        raise Invalid("Invalid relative file path")
    if ".git" in parts:
        raise Invalid("Cannot read or modify .git metadata directly")
    return "/".join(parts)


def _runner_tar_info(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = 1000
    info.gid = 1000
    info.uname = "runner"
    info.gname = "runner"
    return info


async def put_container_archive(
    compute_backend: Backend, container_id: str, path: str, tar_bytes: bytes, *, timeout: float = 60.0
) -> None:
    """Upload a tar archive into `path` inside a running container or Kubernetes Pod."""
    if is_k8s_backend(compute_backend):
        try:
            await k8s_put_archive(compute_backend, container_id, path, tar_bytes, timeout=timeout)
        except Exception as exc:
            raise Invalid(f"Could not write to session workspace: {exc}") from exc
        return

    client, base_url = _docker_client_for_backend(compute_backend)
    async with client:
        resp = await client.put(
            f"{base_url}/containers/{quote(container_id, safe='')}/archive",
            params={"path": path},
            content=tar_bytes,
            headers={"Content-Type": "application/x-tar"},
            timeout=timeout,
        )
        if resp.status_code not in (200, 204):
            raise Invalid(f"Could not write to session workspace (HTTP {resp.status_code})")


async def exec_in_container(
    compute_backend: Backend, container_id: str, cmd: list[str], *, user: str = "0"
) -> None:
    """Run a quick non-interactive command inside the container or Kubernetes Pod and wait for completion."""
    if is_k8s_backend(compute_backend):
        rc, _, err = await k8s_exec_command(compute_backend, container_id, cmd, timeout=30.0)
        if rc != 0:
            raise Invalid(
                f"Container exec failed (exit {rc}): {err.decode('utf-8', errors='replace')[:200]}"
            )
        return

    client, base_url = _docker_client_for_backend(compute_backend)
    async with client:
        exec_resp = await client.post(
            f"{base_url}/containers/{quote(container_id, safe='')}/exec",
            json={
                "AttachStdin": False,
                "AttachStdout": True,
                "AttachStderr": True,
                "User": user,
                "Cmd": cmd,
            },
        )
        if exec_resp.status_code not in (200, 201):
            raise Invalid(f"Container exec failed (HTTP {exec_resp.status_code})")
        exec_id = str(exec_resp.json().get("Id") or "")
        if exec_id:
            await client.post(
                f"{base_url}/exec/{quote(exec_id, safe='')}/start",
                json={"Detach": False, "Tty": False},
                timeout=30.0,
            )


def _pack_files_tar(file_map: dict[str, bytes]) -> bytes:
    """Pack `{rel_path: bytes}` into a tar relative to `/workspace` with runner ownership."""
    buf = io.BytesIO()
    seen_dirs: set[str] = set()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for rel, data in sorted(file_map.items()):
            parts = PurePosixPath(rel).parts
            for i in range(1, len(parts)):
                d_rel = "/".join(parts[:i])
                if d_rel not in seen_dirs:
                    seen_dirs.add(d_rel)
                    d_info = tarfile.TarInfo(name=d_rel)
                    d_info.type = tarfile.DIRTYPE
                    d_info.mode = 0o755
                    tar.addfile(_runner_tar_info(d_info))
            f_info = tarfile.TarInfo(name=rel)
            f_info.size = len(data)
            f_info.mode = 0o644
            tar.addfile(_runner_tar_info(f_info), io.BytesIO(data))
    return buf.getvalue()


async def list_workspace_files(
    compute_backend: Backend,
    container_id: str,
    owner_id: str,
    blocked_paths: list[str] | None = None,
) -> dict[str, Any]:
    """List all non-ignored files in `/workspace` with their baseline diff status."""
    blocked = [p for p in (blocked_paths or []) if p]
    files = await export_workspace(compute_backend, container_id, session_id=owner_id)
    baseline = load_baseline(owner_id) or {}
    changes = diff_workspace(files, baseline, blocked) if baseline else []
    status_by_path = {c["path"]: c["status"] for c in changes}

    entries: list[dict[str, Any]] = []
    for rel, data in files.items():
        if is_ignored_path(rel) or _blocked(rel, blocked):
            continue
        entries.append(
            {
                "path": rel,
                "size": len(data),
                "status": status_by_path.get(rel),
            }
        )
    for c in changes:
        if c["status"] == "deleted":
            entries.append({"path": c["path"], "size": 0, "status": "deleted"})

    entries.sort(key=lambda e: e["path"])
    return {"files": entries, "changed_files": changes}


def _decode_text_or_binary(raw: bytes | None) -> tuple[str, bool]:
    if raw is None:
        return "", False
    if b"\x00" in raw[:8192]:
        return "", True
    try:
        return raw.decode("utf-8"), False
    except UnicodeDecodeError:
        return "", True


async def read_workspace_file(
    compute_backend: Backend,
    container_id: str,
    owner_id: str,
    path: str,
    blocked_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Read a single file from `/workspace` along with its baseline content and status."""
    rel = _clean_rel_path(path)
    blocked = [p for p in (blocked_paths or []) if p]
    if is_ignored_path(rel) or _blocked(rel, blocked):
        raise Invalid(f"Path '{rel}' is ignored or protected")

    baseline = load_baseline(owner_id) or {}
    baseline_files = load_baseline_files(owner_id)
    orig_bytes = baseline_files.get(rel)

    archive = (
        await fetch_container_archive(compute_backend, container_id, f"/workspace/{rel}")
        if container_id
        else None
    )
    if archive is None and owner_id:
        archive = await fetch_volume_archive(compute_backend, owner_id, f"/workspace/{rel}")
    current_bytes: bytes | None = None
    if archive is not None:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:*") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f_obj = tar.extractfile(member)
                    if f_obj is not None:
                        current_bytes = f_obj.read()
                        break

    if current_bytes is None:
        if rel in baseline:
            orig_text, orig_bin = _decode_text_or_binary(orig_bytes)
            return {
                "path": rel,
                "content": "",
                "original_content": None if orig_bin else orig_text,
                "is_binary": orig_bin,
                "status": "deleted",
            }
        raise Invalid(f"File '{rel}' not found in workspace")

    digest = hashlib.sha256(current_bytes).hexdigest()
    prev = baseline.get(rel)
    if not baseline:
        status = "unchanged"
    elif prev is None:
        status = "added"
    elif prev != digest:
        status = "modified"
    else:
        status = "unchanged"

    content_text, is_bin = _decode_text_or_binary(current_bytes)
    orig_text_out: str | None = None
    if orig_bytes is not None:
        decoded_orig, orig_bin = _decode_text_or_binary(orig_bytes)
        if not orig_bin:
            orig_text_out = decoded_orig
    elif status == "unchanged" and not is_bin:
        orig_text_out = content_text

    return {
        "path": rel,
        "content": content_text,
        "original_content": orig_text_out,
        "is_binary": is_bin,
        "status": status,
    }


async def write_workspace_file(
    compute_backend: Backend,
    container_id: str,
    owner_id: str,
    path: str,
    content: str,
    blocked_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Create or overwrite `/workspace/{rel}` inside the container and return its updated state."""
    rel = _clean_rel_path(path)
    blocked = [p for p in (blocked_paths or []) if p]
    if is_ignored_path(rel) or _blocked(rel, blocked):
        raise Invalid(f"Path '{rel}' is protected by project governance rules")

    tar_bytes = _pack_files_tar({rel: content.encode("utf-8")})
    await put_container_archive(compute_backend, container_id, "/workspace", tar_bytes)
    return await read_workspace_file(compute_backend, container_id, owner_id, rel, blocked)


async def delete_workspace_file(
    compute_backend: Backend,
    container_id: str,
    path: str,
    blocked_paths: list[str] | None = None,
) -> None:
    """Delete `/workspace/{rel}` inside the container."""
    rel = _clean_rel_path(path)
    blocked = [p for p in (blocked_paths or []) if p]
    if is_ignored_path(rel) or _blocked(rel, blocked):
        raise Invalid(f"Path '{rel}' is protected by project governance rules")
    await exec_in_container(compute_backend, container_id, ["rm", "-f", "--", f"/workspace/{rel}"])


async def discard_workspace_changes(
    compute_backend: Backend,
    container_id: str,
    owner_id: str,
    blocked_paths: list[str] | None = None,
    path: str | None = None,
) -> dict[str, Any]:
    """Revert a single file (or all workspace changes when `path` is None) back to the session baseline."""
    blocked = [p for p in (blocked_paths or []) if p]
    baseline = load_baseline(owner_id)
    if baseline is None:
        raise Invalid("This session started before baseline tracking was available")
    baseline_files = load_baseline_files(owner_id)
    files = await export_workspace(compute_backend, container_id)
    changes = diff_workspace(files, baseline, blocked)

    if path is not None and path.strip():
        rel = _clean_rel_path(path)
        target_changes = [c for c in changes if c["path"] == rel]
    else:
        target_changes = changes

    added_paths = [f"/workspace/{c['path']}" for c in target_changes if c["status"] == "added"]
    if added_paths:
        await exec_in_container(compute_backend, container_id, ["rm", "-f", "--", *added_paths])

    restore_map: dict[str, bytes] = {}
    git_fallback_paths: list[str] = []
    for c in target_changes:
        if c["status"] in ("modified", "deleted"):
            rel_p = c["path"]
            if rel_p in baseline_files:
                restore_map[rel_p] = baseline_files[rel_p]
            else:
                git_fallback_paths.append(rel_p)

    if restore_map:
        tar_bytes = _pack_files_tar(restore_map)
        await put_container_archive(compute_backend, container_id, "/workspace", tar_bytes)
    if git_fallback_paths:
        await exec_in_container(
            compute_backend,
            container_id,
            ["git", "-C", "/workspace", "checkout", "HEAD", "--", *git_fallback_paths],
        )

    return await list_workspace_files(compute_backend, container_id, owner_id, blocked)



# ---------------------------------------------------------------------------
# Draft summary + acceptance criteria
# ---------------------------------------------------------------------------


def _fallback_draft(session_label: str, changes: list[dict[str, str]]) -> tuple[str, str, list[str]]:
    paths = [c["path"] for c in changes]
    top = ", ".join(f"`{p}`" for p in paths[:6]) + (f" and {len(paths) - 6} more" if len(paths) > 6 else "")
    title = f"Changes from {session_label}"[:200]
    summary = (
        f"Work done interactively in {session_label}.\n\n"
        f"Changed files ({len(paths)}): {top or 'none'}."
    )
    criteria = [
        f"The changes to {top or 'the workspace'} behave as intended",
        "Existing tests pass with these changes",
    ]
    return title, summary, criteria


async def draft_push_summary(
    session: AsyncSession,
    project: Project,
    *,
    session_label: str,
    context_text: str,
    changes: list[dict[str, str]],
    files: dict[str, bytes],
) -> tuple[str, str, list[str], str]:
    """Ask the project's Models backend to draft (title, summary, criteria); fall back deterministically."""
    title, summary, criteria = _fallback_draft(session_label, changes)
    try:
        ideate_pol = await get_effective_stage_policy(session, project.id, SeedStage.IDEATE)
        min_crit = ideate_pol.min_acceptance_criteria or 1
        max_crit = ideate_pol.max_acceptance_criteria or 6
        backend, model = await _resolve_coordinator_model(session, None, project)
        if backend is None:
            return title, summary, criteria, "fallback"
        await sync_backend_to_litellm(backend, extra_models=[model])

        excerpts: list[str] = []
        budget = 12000
        for c in changes[:20]:
            if c["status"] == "deleted":
                excerpts.append(f"### {c['path']} (deleted)")
                continue
            raw = files.get(c["path"], b"")
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError:
                excerpts.append(f"### {c['path']} ({c['status']}, binary)")
                continue
            snippet = text[: min(1500, budget)]
            budget -= len(snippet)
            excerpts.append(f"### {c['path']} ({c['status']})\n```\n{snippet}\n```")
            if budget <= 0:
                break

        prompt = (
            "A developer worked interactively in a sandbox and wants to submit the work for code review.\n"
            "Draft a seed for the reviewer. Respond with ONLY a JSON object: "
            '{"title": str (imperative, <80 chars), "summary": str (markdown, what changed and why), '
            f'"acceptance_criteria": [str, ...] ({min_crit}-{max_crit} concrete, testable criteria the reviewer can check; '
            "describe outcomes rather than implementation steps, do not repeat the summary, and include one for tests where relevant)}.\n\n"
            f"## Session context\n{context_text[-6000:]}\n\n"
            f"## Changed files\n" + "\n".join(f"- {c['status']}: {c['path']}" for c in changes[:100])
            + "\n\n## File excerpts (new content)\n" + "\n\n".join(excerpts)
        )
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{settings.LITELLM_URL.rstrip('/')}/v1/chat/completions",
                headers=_litellm_headers(),
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.2,
                },
            )
        if resp.status_code != 200:
            return title, summary, criteria, "fallback"
        content = str(
            ((resp.json().get("choices") or [{}])[0].get("message") or {}).get("content") or ""
        )
        match = re.search(r"\{.*\}", content, re.DOTALL)
        if not match:
            return title, summary, criteria, "fallback"
        parsed = json.loads(match.group(0))
        d_title = str(parsed.get("title") or "").strip()
        d_summary = str(parsed.get("summary") or "").strip()
        d_criteria = [
            str(c).strip() for c in (parsed.get("acceptance_criteria") or []) if str(c).strip()
        ]
        if d_title and d_criteria:
            return d_title[:200], d_summary or summary, d_criteria[:8], model
    except Exception as exc:
        logger.debug("Push draft generation fell back: %s", exc)
    return title, summary, criteria, "fallback"


# ---------------------------------------------------------------------------
# Push changes to a branch (runs in Core; the token never enters a container)
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: str, token: str = "", timeout: int = 180) -> subprocess.CompletedProcess[str]:
    res = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, check=False, timeout=timeout
    )
    if res.returncode != 0:
        err = (res.stderr or res.stdout or "git failed").strip()
        if token:
            err = err.replace(token, "***")
        raise Invalid(f"git {args[0]} failed: {err[:300]}")
    return res


def push_changes_to_branch(
    *,
    auth_url: str,
    token: str,
    base_ref: str,
    head_sha: str,
    branch: str,
    changes: list[dict[str, str]],
    files: dict[str, bytes],
    commit_message: str,
    author_name: str,
    author_email: str,
) -> str:
    """Clone `base_ref`, check out `head_sha`, apply changes, commit to `branch`, push. Returns commit SHA."""
    with tempfile.TemporaryDirectory() as tmp:
        repo_dir = str(Path(tmp) / "repo")
        _run_git(["clone", "--depth", "50", "--branch", base_ref, auth_url, repo_dir], tmp, token)
        if head_sha:
            try:
                _run_git(["checkout", "-q", head_sha], repo_dir, token)
            except Invalid:
                try:
                    _run_git(["fetch", "--depth", "1", "origin", head_sha], repo_dir, token)
                    _run_git(["checkout", "-q", head_sha], repo_dir, token)
                except Invalid:
                    logger.warning("Session base %s unavailable; applying on %s tip", head_sha[:7], base_ref)
        _run_git(["checkout", "-q", "-B", branch], repo_dir, token)

        root = Path(repo_dir).resolve()
        for c in changes:
            target = (root / c["path"]).resolve()
            if not target.is_relative_to(root) or ".git" in Path(c["path"]).parts:
                continue
            if c["status"] == "deleted":
                if target.is_file():
                    target.unlink()
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(files[c["path"]])

        _run_git(["add", "-A"], repo_dir, token)
        staged = subprocess.run(
            ["git", "diff", "--cached", "--quiet"], cwd=repo_dir, check=False
        )
        if staged.returncode == 0:
            raise Invalid("No changes to push: the workspace matches the branch it started from")
        _run_git(
            [
                "-c", f"user.name={author_name}",
                "-c", f"user.email={author_email}",
                "commit", "-q", "-m", commit_message,
            ],
            repo_dir,
            token,
        )
        sha = _run_git(["rev-parse", "HEAD"], repo_dir, token).stdout.strip()
        _run_git(["push", "--force-with-lease", "origin", f"HEAD:refs/heads/{branch}"], repo_dir, token)
        return sha


async def resolve_linked_seed_info(
    session: AsyncSession,
    project: Project,
    *,
    seed_id: str | None,
    ref: str,
) -> dict[str, Any]:
    """Return linked seed metadata if the session is associated with an open seed."""
    seed: Seed | None = None
    if seed_id:
        candidate = await session.get(Seed, seed_id)
        if candidate is not None and candidate.project_id == project.id and candidate.status != SeedStatus.MERGED:
            seed = candidate
    if seed is None and ref.startswith(f"gitseed/{project.slug}/seed-"):
        suffix = ref.removeprefix(f"gitseed/{project.slug}/seed-").strip()
        if suffix.isdigit():
            seed_num = int(suffix)
            seed = (
                await session.execute(
                    select(Seed).where(
                        Seed.project_id == project.id,
                        Seed.number == seed_num,
                        col(Seed.status) != SeedStatus.MERGED,
                    )
                )
            ).scalars().first()
    if seed is None:
        return {
            "linked_seed_id": None,
            "linked_seed_number": None,
            "linked_seed_title": None,
            "linked_seed_branch": None,
        }
    return {
        "linked_seed_id": seed.id,
        "linked_seed_number": seed.number,
        "linked_seed_title": seed.title,
        "linked_seed_branch": format_seed_branch(project.slug, seed.number),
    }


async def push_session_to_seed(
    session: AsyncSession,
    actor: User,
    project: Project,
    *,
    repository_id: int | None,
    base_ref: str,
    head_sha: str,
    changes: list[dict[str, str]],
    files: dict[str, bytes],
    title: str,
    summary: str,
    acceptance_criteria: list[str],
    session_label: str,
    target_seed_id: str | None = None,
) -> dict[str, Any]:
    """Push session changes either onto an existing seed's branch (`target_seed_id`) or into a new seed at Review."""
    if not changes:
        raise Invalid("No changes to push")
    total_bytes = sum(len(files.get(c["path"], b"")) for c in changes)
    if len(changes) > MAX_PUSH_FILES or total_bytes > MAX_PUSH_BYTES:
        raise Invalid(
            f"Too many changes to push ({len(changes)} files, {total_bytes // 1024} KB). "
            "Add build outputs to blocked paths or clean the workspace first."
        )

    repo: ProjectRepository | None = None
    if repository_id is not None:
        repo = await session.get(ProjectRepository, repository_id)
    if repo is None or repo.project_id != project.id:
        repo = (
            await session.execute(
                select(ProjectRepository)
                .where(ProjectRepository.project_id == project.id)
                .order_by(col(ProjectRepository.is_primary).desc(), col(ProjectRepository.id).asc())
            )
        ).scalars().first()
    if repo is None:
        raise Invalid("Project has no repository to push to")
    git_backend = await session.get(Backend, repo.backend_id)
    if git_backend is None:
        raise Invalid("Git backend for repository not found")

    if target_seed_id:
        seed = await session.get(Seed, target_seed_id)
        if seed is None or seed.project_id != project.id:
            raise Invalid("Target seed not found in this project")
        if seed.status == SeedStatus.MERGED:
            raise Invalid("Cannot push to a merged seed; open a new seed instead")
        branch = format_seed_branch(project.slug, seed.number)
        token = await resolve_git_backend_token(
            git_backend,
            decrypt_secrets(git_backend.secrets_encrypted),
            repo_full_names=[repo.full_name],
            write_access=True,
        )
        if not token:
            raise Invalid("No write token available for the repository's Git backend")
        auth_url = git_browse._build_authenticated_git_url(git_backend, repo.full_name, token)
        commit_msg = f"[{project.slug}#{seed.number}] {title.strip() or seed.title}"
        if summary.strip():
            commit_msg += f"\n\n{summary.strip()}"
        commit_msg += f"\n\nPushed from {session_label}."
        author_display = (
            getattr(actor, "preferred_name", None)
            or actor.name
            or actor.email
            or "GitSeed"
        )
        commit_sha = await asyncio.to_thread(
            push_changes_to_branch,
            auth_url=auth_url,
            token=token,
            base_ref=base_ref,
            head_sha=head_sha,
            branch=branch,
            changes=changes,
            files=files,
            commit_message=commit_msg,
            author_name=author_display,
            author_email=actor.email or "gitseed@localhost",
        )

        now = datetime.now(UTC)
        old_stage = seed.stage

        # Cancel any in-flight runs on the seed so an earlier stage runner doesn't conflict with the session push
        active_runs = (
            await session.execute(
                select(Run).where(
                    Run.seed_id == seed.id,
                    col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
                )
            )
        ).scalars().all()
        for r in active_runs:
            await finalize_run_litellm_key(session, r, seed, project, delete_key=True)
            if r.compute_backend_id and (r.container_id or r.id):
                comp_b = await session.get(Backend, r.compute_backend_id)
                if comp_b is not None:
                    try:
                        await stop_and_remove_docker_container(
                            comp_b, r.container_id, run_id=r.id
                        )
                    except TypeError:
                        if r.container_id:
                            await stop_and_remove_docker_container(comp_b, r.container_id)
                r.container_id = None
            r.status = RunStatus.CANCELLED
            r.finished_at = now
            r.updated_at = now
            r.error = f"Superseded by interactive session push from {session_label}"

        # Backfill specification and acceptance criteria if pushing from an earlier stage (Ideate/Plan/Execute)
        if not (seed.specification or "").strip():
            seed.specification = (
                summary.strip()
                or (seed.description or "").strip()
                or seed.title
            )
        if not seed.acceptance_criteria and acceptance_criteria:
            cleaned_crit = [c.strip() for c in acceptance_criteria if c and c.strip()]
            if cleaned_crit:
                seed.acceptance_criteria = cleaned_crit

        # Mark any open tasks as completed since the interactive session implemented and pushed the code
        open_tasks = (
            await session.execute(
                select(SeedTask).where(
                    SeedTask.seed_id == seed.id,
                    col(SeedTask.status).in_([TaskStatus.PENDING, TaskStatus.RUNNING]),
                )
            )
        ).scalars().all()
        for t in open_tasks:
            t.status = TaskStatus.DONE
            t.completed_at = now
            t.updated_at = now

        # Auto-fast-track the seed to Review (and re-queue Review if already in Review/Merge)
        review_policy = await get_effective_stage_policy(
            session, project.id, SeedStage.REVIEW
        )
        seed.stage = SeedStage.REVIEW
        seed.status = (
            SeedStatus.QUEUED
            if review_policy.enabled
            else SeedStatus.AWAITING_APPROVAL
        )
        seed.blocked_reason = None
        seed.stage_entered_at = now
        seed.updated_at = now

        short_sha = commit_sha[:7] if commit_sha else "HEAD"
        comment_body = (
            f"Pushed {len(changes)} changed file(s) from {session_label} "
            f"(commit `{short_sha}`): {title.strip() or seed.title}"
        )
        if summary.strip():
            comment_body += f"\n\n{summary.strip()}"
        session.add(
            SeedComment(
                seed_id=seed.id,
                author_type=ActorType.USER,
                author_id=actor.id,
                stage=seed.stage,
                body=comment_body,
                created_at=now,
            )
        )
        record(
            session,
            type=ActivityType.STAGE_CHANGED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=project.id,
            seed_id=seed.id,
            data={
                "from": str(old_stage),
                "to": str(seed.stage),
                "reason": "code_edited",
                "commit_sha": commit_sha,
                "changed_files": len(changes),
            },
        )
        await session.commit()
        await session.refresh(seed)

        pr_url: str | None = None
        if is_auto_create_pr_enabled(project) and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE):
            try:
                pr_url = await ensure_seed_pull_request(session, seed, project, branch)
            except Exception as exc:
                logger.warning("PR update after session push failed for %s: %s", seed.id, exc)
        return {
            "seed_id": seed.id,
            "seed_number": seed.number,
            "branch": branch,
            "commit_sha": commit_sha,
            "pull_request_url": pr_url,
            "changed_files": len(changes),
        }

    criteria = [c.strip() for c in acceptance_criteria if c and c.strip()]
    if not criteria:
        raise Invalid("At least one acceptance criterion is required so Review has something to check")

    spec = (
        f"{summary.strip()}\n\n---\n_Created from {session_label}. "
        f"Base: `{base_ref}`{'@' + head_sha[:7] if head_sha else ''}. "
        f"{len(changes)} changed file(s)._"
    )
    # Staged so the scheduler doesn't start Review before the branch exists
    seed = await seeds_service.create_seed(
        session,
        actor,
        project_id=project.id,
        title=title.strip(),
        description=summary.strip()[:2000],
        specification=spec,
        acceptance_criteria=criteria,
        tags=["session"],
        stage=SeedStage.REVIEW,
        staged=True,
    )
    branch = format_seed_branch(project.slug, seed.number)

    try:
        token = await resolve_git_backend_token(
            git_backend,
            decrypt_secrets(git_backend.secrets_encrypted),
            repo_full_names=[repo.full_name],
            write_access=True,
        )
        if not token:
            raise Invalid("No write token available for the repository's Git backend")
        auth_url = git_browse._build_authenticated_git_url(git_backend, repo.full_name, token)
        commit_sha = await asyncio.to_thread(
            push_changes_to_branch,
            auth_url=auth_url,
            token=token,
            base_ref=base_ref,
            head_sha=head_sha,
            branch=branch,
            changes=changes,
            files=files,
            commit_message=f"[{project.slug}#{seed.number}] {title.strip()}\n\nPushed from {session_label}.",
            author_name=actor.name or "GitSeed",
            author_email=actor.email or "gitseed@localhost",
        )
    except Exception:
        # Roll back the placeholder seed so a failed push leaves nothing behind
        try:
            await session.delete(seed)
            await session.commit()
        except Exception:
            await session.rollback()
        raise

    seed.status = SeedStatus.QUEUED
    await session.commit()
    await session.refresh(seed)

    pr_url = None
    if is_auto_create_pr_enabled(project):
        try:
            pr_url = await ensure_seed_pull_request(session, seed, project, branch)
        except Exception as exc:
            logger.warning("PR creation after session push failed for %s: %s", seed.id, exc)

    return {
        "seed_id": seed.id,
        "seed_number": seed.number,
        "branch": branch,
        "commit_sha": commit_sha,
        "pull_request_url": pr_url,
        "changed_files": len(changes),
    }


def remove_session_data(owner_id: str) -> None:
    d = session_data_dir(owner_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)

