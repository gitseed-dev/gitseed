"""Built-in Local Git bare repository manager & local workspace initializer.

Allows desktop users on macOS and Windows (and self-hosted single-node setups) to create
"Local Folder" projects backed by a real Git repository on disk without requiring GitHub,
GitLab, or Bitbucket:
- Maintains a bare Git repository at `~/.gitseed/local_repos/<slug>.git` (or `GITSEED_LOCAL_REPOS_DIR`).
- Exposes Git Smart HTTP (`git-upload-pack` / `git-receive-pack`) so Docker containers
  (Seed runners, Sandbox terminals, Agent sessions) can clone and push via
  `http://host.docker.internal:8000/api/v1/local-git/<slug>.git`.
- Automatically initializes a local working directory (e.g. `~/GitSeed/workspaces/<slug>` or
  a user-selected local folder) with `git init -b main`, links `origin` to the bare repo,
  and installs a `post-receive` hook so merged Seeds fast-forward the local folder automatically.
"""

from __future__ import annotations

import logging
import os
import re
import stat
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from gitseed_common.enums import (
    ActivityType,
    ActorType,
    BackendHealth,
    BackendType,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend, ProjectRepository
from gitseed_core.models.user import User
from gitseed_core.services import projects as projects_service
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Invalid, NotFound

logger = logging.getLogger("uvicorn.error")

_SAFE_SLUG_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_FORBIDDEN_REF_CHARS_RE = re.compile(r"[\x00-\x1f\x7f ~^:?*\[\\]")


class InvalidGitInputError(Invalid, ValueError):
    """Raised when a Git ref name or local workspace path fails validation."""


def validate_refname(ref: str) -> str:
    """Validate a Git ref/branch name against git-check-ref-format rules and option injection."""
    if not isinstance(ref, str):
        raise InvalidGitInputError("Git ref name must be a string")
    cleaned = ref.strip()
    if not cleaned:
        raise InvalidGitInputError("Git ref name cannot be empty")
    if len(cleaned) > 255:
        raise InvalidGitInputError("Git ref name is too long")
    if cleaned.startswith(("-", "/")):
        raise InvalidGitInputError("Git ref name must not start with '-' or '/'")
    if cleaned.endswith(("/", ".", ".lock")):
        raise InvalidGitInputError("Git ref name must not end with '/', '.', or '.lock'")
    if ".." in cleaned or "@{" in cleaned or "//" in cleaned or cleaned == "@":
        raise InvalidGitInputError("Git ref name contains forbidden sequence")
    if _FORBIDDEN_REF_CHARS_RE.search(cleaned) or any(ch.isspace() for ch in cleaned):
        raise InvalidGitInputError("Git ref name contains invalid characters")
    for part in cleaned.split("/"):
        if not part or part.startswith(".") or part.endswith((".", ".lock")):
            raise InvalidGitInputError("Git ref name contains an invalid path component")
    return cleaned


def _validate_repo_subpath(path: str, *, allow_empty: bool = True) -> str:
    cleaned = (path or "").strip().strip("/")
    if not cleaned:
        if allow_empty:
            return ""
        raise InvalidGitInputError("File path is required")
    if cleaned.startswith("-"):
        raise InvalidGitInputError("Repository path must not start with '-'")
    if any(ord(ch) < 32 or ord(ch) == 127 for ch in cleaned) or "\\" in cleaned:
        raise InvalidGitInputError("Repository path contains invalid characters")
    segments = [seg for seg in cleaned.split("/") if seg]
    if any(seg in (".", "..") for seg in segments):
        raise InvalidGitInputError("Repository path must not contain '.' or '..' traversal segments")
    return "/".join(segments)


_SENSITIVE_DOT_DIRS: frozenset[str] = frozenset(
    {
        ".ssh",
        ".aws",
        ".gnupg",
        ".kube",
        ".docker",
        ".config",
        ".claude",
        ".codex",
        ".gemini",
        ".grok",
        ".gitseed",
    }
)
_SENSITIVE_WORKSPACE_DIRS: frozenset[str] = _SENSITIVE_DOT_DIRS | frozenset({"appdata"})


def _allowed_workspace_roots() -> list[Path]:
    raw = (getattr(settings, "LOCAL_WORKSPACE_ROOTS", "") or "").strip()
    roots: list[Path] = []
    for item in raw.split(","):
        cleaned = item.strip()
        if cleaned:
            roots.append(Path(cleaned).expanduser().resolve())
    if not roots:
        roots.append(Path.home().resolve())
    return roots


def validate_workspace_path(workspace_path: str | Path) -> Path:
    """Resolve `workspace_path` and verify it resides under `settings.LOCAL_WORKSPACE_ROOTS` (or `Path.home()`),
    rejecting `$HOME` itself, `/` root, hidden/credential dot-directories, and symlink escapes.
    """
    raw_str = str(workspace_path or "").strip()
    if not raw_str or "\x00" in raw_str:
        raise InvalidGitInputError("Workspace path is invalid")
    expanded = Path(raw_str).expanduser()
    if not expanded.is_absolute():
        raise InvalidGitInputError("Workspace path must be an absolute path")

    allowed_roots = _allowed_workspace_roots()
    home_dir = Path.home().resolve()

    # Check symlink components along the path before and after resolve()
    cur = Path(expanded.anchor)
    for part in expanded.parts[1:]:
        if part.lower() in _SENSITIVE_DOT_DIRS:
            raise InvalidGitInputError(
                f"Workspace path cannot traverse sensitive directory '{part}'"
            )
        cur = cur / part
        if cur.is_symlink():
            target = cur.resolve()
            if not any(target == root or target.is_relative_to(root) for root in allowed_roots):
                raise InvalidGitInputError(
                    f"Workspace path symlink '{cur}' points outside allowed workspace roots"
                )

    resolved = expanded.resolve()
    if resolved == Path(resolved.anchor) or len(resolved.parts) <= 1:
        raise InvalidGitInputError("Filesystem root cannot be used as a workspace directory")
    if resolved == home_dir or home_dir.is_relative_to(resolved):
        raise InvalidGitInputError(
            "Home directory or its ancestors cannot be used as a workspace directory"
        )

    for part in resolved.parts[1:]:
        if part.lower() in _SENSITIVE_DOT_DIRS:
            raise InvalidGitInputError(
                f"Workspace path cannot reside inside sensitive directory '{part}'"
            )

    matched_roots = [
        root for root in allowed_roots if resolved == root or resolved.is_relative_to(root)
    ]
    if not matched_roots:
        raise InvalidGitInputError(
            f"Workspace path '{resolved}' must reside under allowed workspace roots"
        )

    matched_root = matched_roots[0]
    rel_parts = resolved.relative_to(matched_root).parts
    for part in rel_parts:
        part_low = part.lower()
        if part_low in _SENSITIVE_WORKSPACE_DIRS or (
            part.startswith(".") and part not in (".", "..")
        ):
            raise InvalidGitInputError(
                f"Workspace path cannot traverse hidden or sensitive directory '{part}'"
            )

    root_parts_low = [p.lower() for p in matched_root.parts]
    if "appdata" in [p.lower() for p in resolved.parts[1:]] and "temp" not in root_parts_low:
        raise InvalidGitInputError(
            "Workspace path cannot reside inside sensitive directory 'AppData'"
        )

    git_entry = resolved / ".git"
    if git_entry.is_symlink():
        git_target = git_entry.resolve()
        if not any(git_target == root or git_target.is_relative_to(root) for root in allowed_roots):
            raise InvalidGitInputError("Workspace .git symlink points outside allowed workspace roots")

    return resolved


_POST_RECEIVE_HOOK_SCRIPT = """#!/bin/sh
# Auto-sync linked local GitSeed workspace folder when the configured default branch advances
WS_PATH="$(git config --get gitseed.workspacePath 2>/dev/null || true)"
DEF_BRANCH="$(git config --get gitseed.defaultBranch 2>/dev/null || printf 'main')"
if [ -z "$DEF_BRANCH" ]; then
  DEF_BRANCH="main"
fi
if [ -n "$WS_PATH" ] && [ -d "$WS_PATH/.git" ] && [ ! -L "$WS_PATH" ] && [ ! -L "$WS_PATH/.git" ]; then
  while read oldrev newrev refname; do
    if [ "$refname" = "refs/heads/$DEF_BRANCH" ]; then
      (
        cd "$WS_PATH" || exit 0
        git fetch origin >/dev/null 2>&1 || true
        CUR_BRANCH="$(git symbolic-ref --short HEAD 2>/dev/null || true)"
        if [ "$CUR_BRANCH" = "$DEF_BRANCH" ]; then
          if [ -z "$(git status --porcelain 2>/dev/null)" ]; then
            git merge --ff-only "origin/$DEF_BRANCH" >/dev/null 2>&1 || true
          else
            PRE_HEAD="$(git rev-parse HEAD 2>/dev/null || true)"
            if git stash push -u -m "gitseed-auto-sync" >/dev/null 2>&1; then
              if git merge --ff-only "origin/$DEF_BRANCH" >/dev/null 2>&1; then
                if ! git stash pop >/dev/null 2>&1; then
                  git reset --hard "$PRE_HEAD" >/dev/null 2>&1 || true
                  git stash pop >/dev/null 2>&1 || true
                fi
              else
                git stash pop >/dev/null 2>&1 || true
              fi
            fi
          fi
        fi
      ) &
    fi
  done
fi
"""


def local_repos_root() -> Path:
    custom = os.environ.get("GITSEED_LOCAL_REPOS_DIR", "").strip()
    if custom:
        root = Path(custom).expanduser().resolve()
        root.mkdir(parents=True, exist_ok=True)
        return root

    # Prefer the shared /app/data volume when running in Docker so api and scheduler containers share bare repos
    artifacts_dir = getattr(settings, "ARTIFACTS_DIR", "") or os.environ.get("ARTIFACTS_DIR", "")
    if artifacts_dir:
        shared_parent = Path(artifacts_dir).expanduser().resolve().parent
        if shared_parent.exists() and os.access(shared_parent, os.W_OK):
            root = shared_parent / "local_repos"
            root.mkdir(parents=True, exist_ok=True)
            return root

    root = (Path.home() / ".gitseed" / "local_repos").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def extract_repo_slug(full_name: str) -> str:
    cleaned = (full_name or "").strip().strip("/")
    if cleaned.endswith(".git"):
        cleaned = cleaned[:-4]
    slug = cleaned.split("/")[-1] if "/" in cleaned else cleaned
    slug = _SAFE_SLUG_RE.sub("-", slug).strip("-")
    return slug or "local-repo"


def resolve_local_bare_repo_path(full_name: str) -> Path:
    slug = extract_repo_slug(full_name)
    return local_repos_root() / f"{slug}.git"


def build_local_git_http_url(full_name: str, *, for_container: bool = False) -> str:
    slug = extract_repo_slug(full_name)
    if for_container:
        runner_api = (
            getattr(settings, "RUNNER_API_URL", None)
            or os.environ.get("RUNNER_API_URL", "")
        ).strip().rstrip("/")
        if runner_api:
            return f"{runner_api}/api/v1/local-git/{slug}.git"
    port = (
        getattr(settings, "API_PORT", None)
        or getattr(settings, "port", None)
        or int(os.environ.get("API_PORT", os.environ.get("GITSEED_PORT", "8420")))
    )
    host = "host.docker.internal" if for_container else "localhost"
    return f"http://{host}:{port}/api/v1/local-git/{slug}.git"


def _run_git(args: list[str], *, cwd: Path | None = None, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "GIT_TERMINAL_PROMPT": "0",
        "GIT_AUTHOR_NAME": os.environ.get("GIT_AUTHOR_NAME", "GitSeed"),
        "GIT_AUTHOR_EMAIL": os.environ.get("GIT_AUTHOR_EMAIL", "gitseed@localhost"),
        "GIT_COMMITTER_NAME": os.environ.get("GIT_COMMITTER_NAME", "GitSeed"),
        "GIT_COMMITTER_EMAIL": os.environ.get("GIT_COMMITTER_EMAIL", "gitseed@localhost"),
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd) if cwd else None,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=timeout,
    )


def _install_post_receive_hook(bare_path: Path, default_branch: str = "main") -> None:
    """Install a static post-receive hook that reads `gitseed.defaultBranch` from git config."""
    clean_branch = validate_refname(default_branch or "main")
    _run_git(
        [
            "config",
            "--file",
            str(bare_path / "config"),
            "gitseed.defaultBranch",
            clean_branch,
        ]
    )
    hooks_dir = bare_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    hook_file = hooks_dir / "post-receive"
    try:
        hook_file.write_text(_POST_RECEIVE_HOOK_SCRIPT, encoding="utf-8")
        hook_file.chmod(hook_file.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except Exception as exc:
        logger.debug("Could not write post-receive hook in %s: %s", bare_path, exc)


def sync_workspace_commits_to_bare_repo(
    bare_path: Path, default_branch: str = "main"
) -> None:
    """If the linked local workspace folder is on `default_branch` and has local commits
    ahead of the bare repository, fast-forward the bare repository before cloning/browsing.
    """
    clean_branch = validate_refname(default_branch or "main")
    if not (bare_path / "HEAD").exists():
        return
    ws_cfg = _run_git(["-C", str(bare_path), "config", "--get", "gitseed.workspacePath"])
    ws_raw = ws_cfg.stdout.strip() if ws_cfg.returncode == 0 else ""
    if not ws_raw:
        return
    try:
        ws_dir = validate_workspace_path(ws_raw)
    except InvalidGitInputError:
        return
    if not (ws_dir / ".git").exists():
        return

    cur_branch = _run_git(["symbolic-ref", "--short", "HEAD"], cwd=ws_dir)
    if cur_branch.returncode != 0 or cur_branch.stdout.strip() != clean_branch:
        return

    ws_head = _run_git(["rev-parse", "--verify", "--end-of-options", "HEAD"], cwd=ws_dir)
    if ws_head.returncode != 0:
        return
    ws_sha = ws_head.stdout.strip()

    bare_head = _run_git(
        ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"refs/heads/{clean_branch}"]
    )
    bare_sha = bare_head.stdout.strip() if bare_head.returncode == 0 else ""
    if not bare_sha or ws_sha != bare_sha:
        # Push only if fast-forward (workspace commit descends from bare_sha)
        _run_git(
            ["push", "--", str(bare_path), f"HEAD:refs/heads/{clean_branch}"],
            cwd=ws_dir,
        )


def _ensure_git_repo_initialized(
    ws_dir: Path,
    bare_path: Path,
    *,
    project_name: str,
    default_branch: str,
) -> None:
    """Initialize `ws_dir` as a Git repository and stage initial files only if safe (no credential folders, not `$HOME`/ancestor)."""
    ws_resolved = ws_dir.resolve()
    home_dir = Path.home().resolve()
    if (
        ws_resolved == Path(ws_resolved.anchor)
        or len(ws_resolved.parts) <= 1
        or ws_resolved == home_dir
        or home_dir.is_relative_to(ws_resolved)
    ):
        raise InvalidGitInputError(
            "Refusing to initialize Git repository in home directory or ancestor path"
        )

    for child in ws_resolved.iterdir():
        if child.name.lower() in _SENSITIVE_WORKSPACE_DIRS:
            raise InvalidGitInputError(
                f"Refusing to initialize Git repository in directory containing sensitive folder '{child.name}'"
            )

    has_ws_git = (ws_resolved / ".git").exists()
    if not has_ws_git:
        init_ws = _run_git(["init", f"--initial-branch={default_branch}"], cwd=ws_resolved)
        if init_ws.returncode != 0:
            _run_git(["init"], cwd=ws_resolved)
            _run_git(["checkout", "-B", default_branch], cwd=ws_resolved)

    # Configure origin to point to the bare repo if not already configured
    rem_res = _run_git(["remote", "get-url", "origin"], cwd=ws_resolved)
    if rem_res.returncode != 0:
        _run_git(["remote", "add", "origin", str(bare_path)], cwd=ws_resolved)
    elif rem_res.stdout.strip() != str(bare_path):
        # Keep existing origin and add/update gitseed-local remote, or update origin if it was a local path
        if rem_res.stdout.strip().endswith(".git") and "local_repos" in rem_res.stdout.strip():
            _run_git(["remote", "set-url", "origin", str(bare_path)], cwd=ws_resolved)

    # Check if working directory has any commits or files
    ws_head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=ws_resolved)
    if ws_head.returncode != 0:
        # Check if there are existing untracked files in ws_resolved
        non_git_items = [p for p in ws_resolved.iterdir() if p.name != ".git"]
        if non_git_items:
            _run_git(["add", "-A"], cwd=ws_resolved)
            _run_git(
                [
                    "-c",
                    "user.name=GitSeed Local",
                    "-c",
                    "user.email=local@gitseed.local",
                    "commit",
                    "-m",
                    f"Initial commit for {project_name}",
                ],
                cwd=ws_resolved,
            )
            _run_git(
                ["push", "-u", str(bare_path), f"HEAD:refs/heads/{default_branch}"],
                cwd=ws_resolved,
            )
    else:
        # Workspace already has commits; push them to the bare repo if bare repo is empty
        bare_head = _run_git(["-C", str(bare_path), "rev-parse", "--verify", "HEAD"])
        if bare_head.returncode != 0:
            _run_git(
                ["push", "-u", str(bare_path), f"HEAD:refs/heads/{default_branch}"],
                cwd=ws_resolved,
            )


def ensure_local_bare_repo(
    slug: str,
    project_name: str,
    *,
    default_branch: str = "main",
    workspace_path: str | None = None,
) -> tuple[Path, str | None]:
    """Ensure a bare Git repo exists for `slug` and optionally sync/init `workspace_path` on disk."""
    default_branch = validate_refname(default_branch or "main")
    bare_path = resolve_local_bare_repo_path(slug)
    if not (bare_path / "HEAD").exists():
        bare_path.mkdir(parents=True, exist_ok=True)
        init_res = _run_git(["init", "--bare", f"--initial-branch={default_branch}", "--", str(bare_path)])
        if init_res.returncode != 0:
            _run_git(["init", "--bare", "--", str(bare_path)])
            _run_git(["-C", str(bare_path), "symbolic-ref", "HEAD", f"refs/heads/{default_branch}"])

    _run_git(["-C", str(bare_path), "config", "http.receivepack", "true"])
    _run_git(["-C", str(bare_path), "config", "uploadpack.allowReachableSHA1InWant", "true"])
    _run_git(["-C", str(bare_path), "config", "receive.denyCurrentBranch", "ignore"])
    _install_post_receive_hook(bare_path, default_branch=default_branch)

    resolved_ws: str | None = None
    ws_accessible_locally = False
    if workspace_path and workspace_path.strip():
        ws_dir = validate_workspace_path(workspace_path)
        if ws_dir.exists() or ws_dir.parent.exists():
            ws_dir.mkdir(parents=True, exist_ok=True)
            resolved_ws = str(ws_dir)
            ws_accessible_locally = True
        else:
            resolved_ws = str(ws_dir)
        _run_git(["-C", str(bare_path), "config", "gitseed.workspacePath", resolved_ws])

    if ws_accessible_locally and resolved_ws:
        _ensure_git_repo_initialized(
            Path(resolved_ws),
            bare_path,
            project_name=project_name,
            default_branch=default_branch,
        )

    # Ensure bare repo has at least one commit on `default_branch` so clones/branches/seeds work immediately
    bare_has_commit = _run_git(["-C", str(bare_path), "rev-parse", "--verify", f"refs/heads/{default_branch}"])
    if bare_has_commit.returncode != 0:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            init_res = _run_git(["init", f"--initial-branch={default_branch}"], cwd=tmp_dir)
            if init_res.returncode != 0:
                _run_git(["init"], cwd=tmp_dir)
                _run_git(["checkout", "-B", default_branch], cwd=tmp_dir)
            readme_file = tmp_dir / "README.md"
            readme_file.write_text(
                f"# {project_name}\n\nLocal GitSeed workspace (`{slug}`).\n",
                encoding="utf-8",
            )
            gitignore_file = tmp_dir / ".gitignore"
            gitignore_file.write_text(
                ".DS_Store\nnode_modules/\n.venv/\n__pycache__/\ndist/\nbuild/\n",
                encoding="utf-8",
            )
            _run_git(["add", "README.md", ".gitignore"], cwd=tmp_dir)
            _run_git(
                [
                    "-c",
                    "user.name=GitSeed Local",
                    "-c",
                    "user.email=local@gitseed.local",
                    "commit",
                    "-m",
                    f"Initialize {project_name}",
                ],
                cwd=tmp_dir,
            )
            _run_git(["push", str(bare_path), f"HEAD:refs/heads/{default_branch}"], cwd=tmp_dir)

    # If workspace_path was accessible locally and had no commits before, pull the initial commit into it
    if ws_accessible_locally and resolved_ws:
        ws_dir = Path(resolved_ws)
        ws_head_after = _run_git(["rev-parse", "--verify", "HEAD"], cwd=ws_dir)
        if ws_head_after.returncode != 0:
            _run_git(["fetch", str(bare_path), default_branch], cwd=ws_dir)
            _run_git(["checkout", "-f", "-B", default_branch, "FETCH_HEAD"], cwd=ws_dir)
            _run_git(["branch", f"--set-upstream-to=origin/{default_branch}", default_branch], cwd=ws_dir)

    return bare_path, resolved_ws


async def ensure_local_git_backend(
    session: AsyncSession, actor: User | None = None
) -> Backend:
    """Find or create the built-in `Local Git` backend."""
    stmt = (
        select(Backend)
        .where(
            Backend.type == BackendType.GIT,
            Backend.provider == "local",
        )
        .order_by(col(Backend.created_at).asc())
    )
    existing = (await session.execute(stmt)).scalars().first()
    current_root = str(local_repos_root())
    if existing is not None:
        needs_update = (
            not existing.enabled
            or existing.health != BackendHealth.HEALTHY
            or existing.endpoint != current_root
        )
        if needs_update:
            existing.enabled = True
            existing.health = BackendHealth.HEALTHY
            existing.endpoint = current_root
            existing.config = {**(existing.config or {}), "auth_mode": "local", "local_repos_dir": current_root}
            existing.updated_at = datetime.now(UTC)
            await session.flush()
        return existing

    now = datetime.now(UTC)
    backend = Backend(
        name="Local Git (On-Device)",
        type=BackendType.GIT,
        provider="local",
        endpoint=current_root,
        enabled=True,
        health=BackendHealth.HEALTHY,
        health_checked_at=now,
        config={"auth_mode": "local", "local_repos_dir": current_root},
        created_by=actor.id if actor is not None else None,
        created_at=now,
        updated_at=now,
    )
    session.add(backend)
    await session.flush()
    return backend


async def maybe_auto_attach_existing_local_workspace(
    session: AsyncSession,
    project: Project,
    *,
    create_if_missing: bool = False,
) -> ProjectRepository | None:
    """If `project` has no repositories in DB but a local workspace folder (`~/GitSeed/workspaces/<slug>`),
    bare repo (`<local_repos_root>/<slug>.git`), or active/queued Seeds exist on a repo-less project,
    auto-attach `local/<slug>`.
    """
    slug = getattr(project, "slug", None)
    if not isinstance(slug, str) or not slug.strip():
        return None

    candidate_ws = Path.home() / "GitSeed" / "workspaces" / slug.strip()
    candidate_bare = resolve_local_bare_repo_path(slug.strip())
    if not candidate_ws.exists() and not candidate_bare.exists() and not create_if_missing:
        from gitseed_core.models.seed import Seed

        has_seed = (
            await session.execute(
                select(Seed.id).where(Seed.project_id == project.id).limit(1)
            )
        ).first()
        if has_seed is None:
            return None

    backend = await ensure_local_git_backend(session, actor=None)
    if getattr(project, "backends_restricted", False):
        # Clear accidental backends_restricted when no Compute/Inference backends are explicitly bound
        non_git_allow = (
            await session.execute(
                select(ProjectBackend.backend_id)
                .join(Backend, col(Backend.id) == col(ProjectBackend.backend_id))
                .where(
                    ProjectBackend.project_id == project.id,
                    col(Backend.type).in_([BackendType.COMPUTE, BackendType.INFERENCE]),
                )
                .limit(1)
            )
        ).first()
        if non_git_allow is None:
            project.backends_restricted = False
        existing_allow = await session.get(ProjectBackend, (project.id, backend.id))
        if existing_allow is None:
            session.add(
                ProjectBackend(
                    project_id=project.id,
                    backend_id=backend.id,
                    created_by=project.created_by,
                    created_at=datetime.now(UTC),
                )
            )
            await session.flush()

    ws_arg = str(candidate_ws) if candidate_ws.exists() else None
    _, resolved_ws = ensure_local_bare_repo(
        project.slug,
        project.name,
        default_branch="main",
        workspace_path=ws_arg,
    )

    full_name = f"local/{project.slug}"
    existing_repo = (
        await session.execute(
            select(ProjectRepository).where(
                ProjectRepository.project_id == project.id,
                ProjectRepository.full_name == full_name,
            )
        )
    ).scalar_one_or_none()
    if existing_repo is not None:
        return existing_repo

    now = datetime.now(UTC)
    repo = ProjectRepository(
        project_id=project.id,
        backend_id=backend.id,
        full_name=full_name,
        default_branch="main",
        is_primary=True,
        created_by=project.created_by,
        created_at=now,
        updated_at=now,
    )
    session.add(repo)
    await session.flush()
    record(
        session,
        type=ActivityType.REPOSITORY_ADDED,
        actor_type=ActorType.USER if project.created_by else ActorType.SYSTEM,
        actor_id=project.created_by,
        project_id=project.id,
        data={
            "repository_id": repo.id,
            "backend_id": backend.id,
            "full_name": repo.full_name,
            "is_primary": True,
            "local": True,
            "workspace_path": resolved_ws,
        },
    )
    await session.commit()
    await session.refresh(repo)
    return repo


async def init_project_local_repository(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    workspace_path: str | None = None,
    default_branch: str = "main",
) -> dict[str, Any]:
    """Initialize a local bare Git repository (and optional local workspace folder) and attach it to `project`."""
    project = await projects_service.require_project_maintainer(
        session, actor, project_id_or_slug
    )
    clean_branch = validate_refname((default_branch or "main").strip() or "main")
    backend = await ensure_local_git_backend(session, actor)

    # Ensure the backend is allowed on this project when backends_restricted is enabled
    if project.backends_restricted:
        non_git_allow = (
            await session.execute(
                select(ProjectBackend.backend_id)
                .join(Backend, col(Backend.id) == col(ProjectBackend.backend_id))
                .where(
                    ProjectBackend.project_id == project.id,
                    col(Backend.type).in_([BackendType.COMPUTE, BackendType.INFERENCE]),
                )
                .limit(1)
            )
        ).first()
        if non_git_allow is None:
            project.backends_restricted = False
        existing_allow = await session.get(ProjectBackend, (project.id, backend.id))
        if existing_allow is None:
            session.add(
                ProjectBackend(
                    project_id=project.id,
                    backend_id=backend.id,
                    created_by=actor.id,
                    created_at=datetime.now(UTC),
                )
            )
            await session.flush()

    default_ws = workspace_path
    if not default_ws:
        default_ws = str(Path.home() / "GitSeed" / "workspaces" / project.slug)

    bare_path, resolved_ws = ensure_local_bare_repo(
        project.slug,
        project.name,
        default_branch=clean_branch,
        workspace_path=default_ws,
    )

    full_name = f"local/{project.slug}"
    existing_repo = (
        await session.execute(
            select(ProjectRepository).where(
                ProjectRepository.project_id == project.id,
                ProjectRepository.backend_id == backend.id,
                ProjectRepository.full_name == full_name,
            )
        )
    ).scalar_one_or_none()

    if existing_repo is None:
        all_repos = (
            await session.execute(
                select(ProjectRepository).where(ProjectRepository.project_id == project.id)
            )
        ).scalars().all()
        for r in all_repos:
            if r.is_primary:
                r.is_primary = False
        await session.flush()

        now = datetime.now(UTC)
        repo = ProjectRepository(
            project_id=project.id,
            backend_id=backend.id,
            full_name=full_name,
            default_branch=clean_branch,
            is_primary=True,
            created_by=actor.id,
            created_at=now,
            updated_at=now,
        )
        session.add(repo)
        await session.flush()

        record(
            session,
            type=ActivityType.REPOSITORY_ADDED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=project.id,
            data={
                "repository_id": repo.id,
                "backend_id": backend.id,
                "full_name": repo.full_name,
                "is_primary": True,
                "local": True,
                "workspace_path": resolved_ws,
            },
        )
        await session.commit()
        await session.refresh(repo)
    else:
        repo = existing_repo
        await session.commit()
        await session.refresh(repo)

    return {
        "repository": repo,
        "backend_id": backend.id,
        "bare_repo_path": str(bare_path),
        "workspace_path": resolved_ws,
        "http_clone_url": build_local_git_http_url(full_name, for_container=False),
        "initialized_git": True,
    }


def list_local_repo_branches(repo: ProjectRepository) -> dict[str, Any]:
    bare_path = resolve_local_bare_repo_path(repo.full_name)
    default_branch = repo.default_branch or "main"
    branches: list[dict[str, Any]] = []
    if (bare_path / "HEAD").exists():
        res = _run_git(
            [
                "-C",
                str(bare_path),
                "for-each-ref",
                "--format=%(refname:short)|%(objectname)",
                "refs/heads/",
            ]
        )
        if res.returncode == 0:
            for line in res.stdout.splitlines():
                if "|" not in line:
                    continue
                b_name, sha = line.split("|", 1)
                b_name = b_name.strip()
                if not b_name:
                    continue
                branches.append(
                    {
                        "name": b_name,
                        "commit_sha": sha.strip() or None,
                        "is_default": b_name == default_branch,
                        "protected": False,
                        "web_url": f"local://{repo.full_name}/tree/{b_name}",
                    }
                )
    if not any(b["name"] == default_branch for b in branches):
        branches.insert(
            0,
            {
                "name": default_branch,
                "commit_sha": None,
                "is_default": True,
                "protected": False,
                "web_url": f"local://{repo.full_name}/tree/{default_branch}",
            },
        )
    branches.sort(
        key=lambda b: (
            0 if b["is_default"] else (1 if str(b["name"]).startswith("gitseed/") else 2),
            str(b["name"]).lower(),
        )
    )
    return {
        "repository_id": repo.id,
        "full_name": repo.full_name,
        "provider": "local",
        "default_branch": default_branch,
        "web_url": f"local://{repo.full_name}",
        "branches": branches,
    }


def get_local_repo_tree(
    repo: ProjectRepository,
    *,
    ref: str | None = None,
    path: str = "",
) -> dict[str, Any]:
    bare_path = resolve_local_bare_repo_path(repo.full_name)
    default_branch = validate_refname(repo.default_branch or "main")
    effective_ref = validate_refname((ref or "").strip() or default_branch)
    clean_path = _validate_repo_subpath(path, allow_empty=True)
    if not (bare_path / "HEAD").exists():
        ensure_local_bare_repo(
            extract_repo_slug(repo.full_name),
            extract_repo_slug(repo.full_name),
            default_branch=default_branch,
        )
    else:
        sync_workspace_commits_to_bare_repo(
            bare_path, default_branch=default_branch
        )
    tree_target = f"{effective_ref}:{clean_path}" if clean_path else effective_ref

    res = _run_git(["-C", str(bare_path), "ls-tree", "-l", "--end-of-options", tree_target, "--"])
    if res.returncode != 0 and effective_ref != default_branch:
        effective_ref = default_branch
        tree_target = f"{effective_ref}:{clean_path}" if clean_path else effective_ref
        res = _run_git(["-C", str(bare_path), "ls-tree", "-l", "--end-of-options", tree_target, "--"])
    if res.returncode != 0:
        raise NotFound(f"Path '{clean_path or '/'}' on ref '{effective_ref}' was not found in {repo.full_name}")

    entries: list[dict[str, Any]] = []
    for line in res.stdout.splitlines():
        if "\t" not in line:
            continue
        meta, name = line.split("\t", 1)
        meta_parts = meta.split()
        if len(meta_parts) < 3:
            continue
        obj_type = meta_parts[1]
        obj_sha = meta_parts[2]
        size_raw = meta_parts[3] if len(meta_parts) >= 4 else "-"
        entry_type = "dir" if obj_type == "tree" else "file"
        item_path = f"{clean_path}/{name}" if clean_path else name
        entries.append(
            {
                "name": name,
                "path": item_path,
                "type": entry_type,
                "size": int(size_raw) if size_raw.isdigit() else None,
                "sha": obj_sha,
                "web_url": f"local://{repo.full_name}/{ 'tree' if entry_type == 'dir' else 'blob' }/{effective_ref}/{item_path}",
            }
        )

    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, str(e["name"]).lower()))

    readme_payload: dict[str, Any] | None = None
    readme_entry = next(
        (
            e
            for e in entries
            if e["type"] == "file"
            and str(e["name"]).lower() in ("readme.md", "readme.mdx", "readme.txt", "readme")
        ),
        None,
    )
    if readme_entry is not None:
        blob_res = _run_git(
            [
                "-C",
                str(bare_path),
                "show",
                "--end-of-options",
                f"{effective_ref}:{readme_entry['path']}",
                "--",
            ]
        )
        if blob_res.returncode == 0:
            readme_payload = {
                "name": readme_entry["name"],
                "path": readme_entry["path"],
                "content": blob_res.stdout[:200_000],
            }

    return {
        "repository_id": repo.id,
        "full_name": repo.full_name,
        "provider": "local",
        "ref": effective_ref,
        "path": clean_path,
        "web_url": f"local://{repo.full_name}/tree/{effective_ref}/{clean_path}".rstrip("/"),
        "entries": entries,
        "readme": readme_payload,
    }


def get_local_repo_blob(
    repo: ProjectRepository,
    *,
    ref: str | None = None,
    path: str,
) -> dict[str, Any]:
    bare_path = resolve_local_bare_repo_path(repo.full_name)
    default_branch = validate_refname(repo.default_branch or "main")
    effective_ref = validate_refname((ref or "").strip() or default_branch)
    clean_path = _validate_repo_subpath(path, allow_empty=False)

    proc = subprocess.run(
        [
            "git",
            "-C",
            str(bare_path),
            "show",
            "--end-of-options",
            f"{effective_ref}:{clean_path}",
            "--",
        ],
        capture_output=True,
        check=False,
        timeout=20,
    )
    if proc.returncode != 0:
        raise NotFound(f"File '{clean_path}' on ref '{effective_ref}' was not found")

    raw_bytes = proc.stdout
    truncated = len(raw_bytes) > 512 * 1024
    sample = raw_bytes[: 512 * 1024]
    is_binary = b"\x00" in sample[:8000]
    text_content = None if is_binary else sample.decode("utf-8", errors="replace")
    file_name = clean_path.split("/")[-1]

    return {
        "repository_id": repo.id,
        "full_name": repo.full_name,
        "provider": "local",
        "ref": effective_ref,
        "path": clean_path,
        "name": file_name,
        "size": len(raw_bytes),
        "sha": None,
        "is_binary": is_binary,
        "truncated": truncated,
        "content": text_content,
        "web_url": f"local://{repo.full_name}/blob/{effective_ref}/{clean_path}",
    }


def get_local_seed_changes(
    repo: ProjectRepository,
    *,
    default_branch: str,
    seed_branch: str,
    merge_commit_sha: str | None = None,
    execute_commits: list[str] | None = None,
) -> dict[str, Any]:
    """Compute commits and file diffs for a Seed against a local bare repository."""
    default_branch = validate_refname(default_branch)
    seed_branch = validate_refname(seed_branch)
    if merge_commit_sha:
        merge_commit_sha = validate_refname(merge_commit_sha)
    if execute_commits:
        execute_commits = [validate_refname(c) for c in execute_commits]
    bare_path = resolve_local_bare_repo_path(repo.full_name)
    commits: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    branch_exists = False
    comparison_mode = "none"
    head_commit_sha: str | None = execute_commits[-1] if execute_commits else None

    if not (bare_path / "HEAD").exists():
        return {
            "branch_exists": False,
            "comparison_mode": "none",
            "head_commit_sha": head_commit_sha,
            "commits": [],
            "files": [],
        }

    branch_Verify = _run_git(
        ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"refs/heads/{seed_branch}"]
    )
    target_range: str | None = None
    diff_range: str | None = None

    if branch_Verify.returncode == 0:
        branch_exists = True
        comparison_mode = "branch"
        head_commit_sha = branch_Verify.stdout.strip()
        target_range = f"{default_branch}..{seed_branch}"
        diff_range = f"{default_branch}...{seed_branch}"
    elif merge_commit_sha:
        verify_merge = _run_git(
            ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"{merge_commit_sha}^{{commit}}"]
        )
        if verify_merge.returncode == 0:
            comparison_mode = "merge_commit"
            head_commit_sha = verify_merge.stdout.strip()
            target_range = f"{head_commit_sha}^1..{head_commit_sha}"
            diff_range = f"{head_commit_sha}^1..{head_commit_sha}"
    elif head_commit_sha:
        verify_head = _run_git(
            ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"{head_commit_sha}^{{commit}}"]
        )
        if verify_head.returncode == 0:
            comparison_mode = "commit_range"
            first_sha = (execute_commits or [head_commit_sha])[0]
            target_range = f"{first_sha}^..{head_commit_sha}"
            diff_range = f"{first_sha}^..{head_commit_sha}"

    if target_range:
        log_res = _run_git(
            [
                "-C",
                str(bare_path),
                "log",
                "--reverse",
                "--format=%H|%h|%s|%an|%aI",
                "--end-of-options",
                target_range,
                "--",
            ]
        )
        if log_res.returncode == 0:
            for line in log_res.stdout.splitlines():
                parts = line.split("|", 4)
                if len(parts) < 5:
                    continue
                sha, short_sha, subj, author, date_iso = parts
                commits.append(
                    {
                        "sha": sha,
                        "short_sha": short_sha,
                        "message": subj,
                        "author_name": author or None,
                        "committed_at": date_iso or None,
                        "web_url": f"local://{repo.full_name}/commit/{sha}",
                    }
                )

    if diff_range:
        numstat_res = _run_git(["-C", str(bare_path), "diff", "--numstat", "--end-of-options", diff_range, "--"])
        status_res = _run_git(["-C", str(bare_path), "diff", "--name-status", "--end-of-options", diff_range, "--"])
        status_map: dict[str, tuple[str, str | None]] = {}
        if status_res.returncode == 0:
            for line in status_res.stdout.splitlines():
                cols = line.split("\t")
                if len(cols) >= 2:
                    code = cols[0].strip().upper()
                    if code.startswith("R") and len(cols) >= 3:
                        status_map[cols[2]] = ("renamed", cols[1])
                    elif code.startswith("A"):
                        status_map[cols[1]] = ("added", None)
                    elif code.startswith("D"):
                        status_map[cols[1]] = ("removed", None)
                    else:
                        status_map[cols[1]] = ("modified", None)

        if numstat_res.returncode == 0:
            for line in numstat_res.stdout.splitlines():
                cols = line.split("\t")
                if len(cols) < 3:
                    continue
                add_s, del_s, raw_path = cols[0], cols[1], cols[2]
                # Handle rename format in numstat if present
                clean_f = raw_path
                st_kind, prev_fn = status_map.get(clean_f, ("modified", None))
                patch_res = _run_git(
                    ["-C", str(bare_path), "diff", "--end-of-options", diff_range, "--", clean_f]
                )
                patch_text = patch_res.stdout if patch_res.returncode == 0 else None
                if patch_text and len(patch_text) > 120_000:
                    patch_text = patch_text[:120_000]
                files.append(
                    {
                        "path": clean_f,
                        "previous_path": prev_fn,
                        "status": st_kind,
                        "additions": int(add_s) if add_s.isdigit() else 0,
                        "deletions": int(del_s) if del_s.isdigit() else 0,
                        "changes": (int(add_s) if add_s.isdigit() else 0)
                        + (int(del_s) if del_s.isdigit() else 0),
                        "patch": patch_text,
                        "truncated": bool(patch_res.stdout and len(patch_res.stdout) > 120_000),
                        "web_url": f"local://{repo.full_name}/blob/{head_commit_sha or default_branch}/{clean_f}",
                    }
                )

    return {
        "branch_exists": branch_exists,
        "comparison_mode": comparison_mode,
        "head_commit_sha": head_commit_sha,
        "commits": commits,
        "files": files,
    }


def check_local_seed_merge_status(
    repo: ProjectRepository,
    *,
    seed_branch: str,
    seed_title: str | None = None,
    is_seed_merged: bool = False,
) -> dict[str, Any]:
    """Inspect a local bare repository to check whether `seed_branch` exists and can merge cleanly into `default_branch`."""
    bare_path = resolve_local_bare_repo_path(repo.full_name)
    default_branch = validate_refname((repo.default_branch or "main").strip() or "main")
    seed_branch = validate_refname(seed_branch)
    sync_workspace_commits_to_bare_repo(bare_path, default_branch=default_branch)

    branch_verify = _run_git(
        ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"refs/heads/{seed_branch}"]
    )
    if branch_verify.returncode != 0:
        return {
            "pull_request_url": None,
            "number": None,
            "repository_full_name": repo.full_name,
            "provider": "local",
            "state": "merged" if is_seed_merged else None,
            "merged": is_seed_merged,
            "title": seed_title,
            "body": None,
            "draft": False,
            "author": None,
            "base_ref": default_branch,
            "head_ref": seed_branch,
            "head_sha": None,
            "created_at": None,
            "updated_at": None,
            "merged_at": None,
            "merge_commit_sha": None,
            "merged_by": None,
            "has_conflicts": False,
            "approved_by": [],
            "changes_requested_by": [],
            "requested_reviewers": [],
            "ci_status": "none",
            "ci_summary": None,
            "failing_checks": [],
            "checks": [],
            "comments": [],
            "review_status": "none",
            "mergeable_state": "clean",
            "ready_to_merge": True,
            "block_reason": None,
        }

    head_sha = branch_verify.stdout.strip()
    has_conflicts = False
    merge_tree_res = _run_git(
        [
            "-C",
            str(bare_path),
            "merge-tree",
            "--write-tree",
            f"refs/heads/{default_branch}",
            f"refs/heads/{seed_branch}",
        ]
    )
    if merge_tree_res.returncode != 0:
        has_conflicts = True

    return {
        "pull_request_url": None,
        "number": None,
        "repository_full_name": repo.full_name,
        "provider": "local",
        "state": "merged" if is_seed_merged else "open",
        "merged": is_seed_merged,
        "title": seed_title,
        "body": f"Local branch `{seed_branch}` → `{default_branch}` in `{repo.full_name}`.",
        "draft": False,
        "author": "GitSeed Local",
        "base_ref": default_branch,
        "head_ref": seed_branch,
        "head_sha": head_sha,
        "created_at": None,
        "updated_at": None,
        "merged_at": None,
        "merge_commit_sha": None,
        "merged_by": None,
        "has_conflicts": has_conflicts,
        "approved_by": [],
        "changes_requested_by": [],
        "requested_reviewers": [],
        "ci_status": "none",
        "ci_summary": "Local Git repository (no external CI)",
        "failing_checks": [],
        "checks": [],
        "comments": [],
        "review_status": "none",
        "mergeable_state": "dirty" if has_conflicts else "clean",
        "ready_to_merge": not has_conflicts,
        "block_reason": (
            f"Merge conflict detected between local branch `{seed_branch}` and `{default_branch}`."
            if has_conflicts
            else None
        ),
    }
