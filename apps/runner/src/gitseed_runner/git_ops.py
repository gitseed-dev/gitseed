"""Git operations for the 3-phase runner (`prepare` → `agent` → `finalize`).

Security invariant:
1. In `prepare`, repositories are cloned via private `GIT_ASKPASS`, `.git/hooks` and `.git/config`
   are neutralized (`gitsafe.py`), and embedded credentials are stripped from `origin` before the
   untrusted `agent` container starts.
2. In `agent`, in-container task worktrees operate with zero Git tokens or push credentials.
3. In `finalize`, `/workspace` is mounted read-only (`:ro`), changes are staged into a fresh clean
   `.git` directory outside `/workspace`, `blocked_paths` and secret scans are enforced, and
   single-use push tokens (`POST /api/v1/runner/git-push-token`) push `gitseed/<project>/seed-<N>`.
"""

from __future__ import annotations

import asyncio
import fnmatch
import logging
import os
import re
import shutil
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

from gitseed_runner.gitsafe import (
    SAFE_GIT_CONFIG_ARGS,
    _exclude_internal_paths,
    build_safe_git_env,
    is_internal_excluded_path,
    is_local_repo_path,
    neutralize_git_repo,
    sanitize_git_dir,
    strip_url_credentials,
)

fcntl: Any = None
with suppress(ImportError):
    import fcntl  # type: ignore[no-redef]

logger = logging.getLogger("gitseed_runner")


def cleanup_git_locks(repo_dirs: dict[str, Path]) -> list[str]:
    """Remove stale .git/*.lock files and sanitize .git/hooks and .git/config."""
    removed: list[str] = []
    for full_name, repo_dir in repo_dirs.items():
        git_dir = repo_dir / ".git"
        if not git_dir.is_dir():
            continue
        for lock_file in git_dir.rglob("*.lock"):
            if lock_file.is_file():
                try:
                    lock_file.unlink()
                    rel = f"{full_name}:{lock_file.relative_to(repo_dir).as_posix()}"
                    removed.append(rel)
                    logger.info("Removed stale git lock file: %s", rel)
                except OSError:
                    pass
        sanitize_git_dir(repo_dir)
    return removed


def matches_blocked_path(rel_posix_path: str, blocked_globs: list[str]) -> bool:
    """Return True if a repository-relative POSIX path matches any configured blocked path glob."""
    clean_rel = (
        rel_posix_path.strip().replace("\\", "/").removeprefix("./").lstrip("/").lower()
    )
    if not clean_rel:
        return False
    p = PurePosixPath(clean_rel)
    basename = p.name
    for pattern in blocked_globs:
        clean_pat = (
            str(pattern or "").strip().replace("\\", "/").removeprefix("./").lstrip("/").lower()
        )
        if not clean_pat:
            continue
        if (
            fnmatch.fnmatchcase(clean_rel, clean_pat)
            or fnmatch.fnmatchcase(basename, clean_pat)
        ):
            return True
        if clean_pat.endswith("/**"):
            prefix = clean_pat[:-3].rstrip("/")
            if clean_rel == prefix or clean_rel.startswith(f"{prefix}/"):
                return True
        try:
            if p.match(clean_pat):
                return True
        except Exception:
            pass
    return False


def enforce_blocked_paths(
    repo_dirs: dict[str, Path],
    initial_heads: dict[str, str],
    blocked_globs: list[str],
) -> list[str]:
    """Detect and revert any changes to files matching `blocked_globs` before pushing."""
    cleaned_globs = [str(g).strip() for g in (blocked_globs or []) if str(g).strip()]
    if not cleaned_globs:
        return []

    reverted: list[str] = []
    for full_name, repo_dir in repo_dirs.items():
        if not (repo_dir / ".git").exists():
            continue
        sanitize_git_dir(repo_dir)
        repo_resolved = repo_dir.resolve()
        base_sha = initial_heads.get(full_name) or "HEAD"
        # Gather modified/added/deleted paths relative to initial head + untracked files
        changed_paths: set[str] = set()
        diff_res = _run_git(["diff", "--name-only", base_sha], repo_dir)
        if diff_res.returncode == 0:
            for line in diff_res.stdout.splitlines():
                if line.strip():
                    changed_paths.add(line.strip())
        status_res = _run_git(["status", "--porcelain", "-uall"], repo_dir)
        if status_res.returncode == 0:
            for line in status_res.stdout.splitlines():
                if len(line) > 3:
                    raw_p = line[3:].strip()
                    if " -> " in raw_p:
                        old_p, new_p = raw_p.split(" -> ", 1)
                        if old_p.strip():
                            changed_paths.add(old_p.strip().strip('"'))
                        raw_p = new_p.strip()
                    if raw_p:
                        changed_paths.add(raw_p.strip('"'))

        for rel_p in sorted(changed_paths):
            if is_internal_excluded_path(rel_p):
                continue
            if matches_blocked_path(rel_p, cleaned_globs):
                # Revert to base_sha if tracked in base_sha, otherwise remove untracked file
                chk = _run_git(["checkout", base_sha, "--", rel_p], repo_dir)
                if chk.returncode != 0:
                    _run_git(["reset", "-q", "HEAD", "--", rel_p], repo_dir)
                    raw_target = repo_dir / rel_p
                    if raw_target.is_symlink():
                        parent_resolved = raw_target.parent.resolve()
                        if parent_resolved == repo_resolved or parent_resolved.is_relative_to(
                            repo_resolved
                        ):
                            raw_target.unlink(missing_ok=True)
                    else:
                        target_path = raw_target.resolve()
                        if (
                            target_path.is_relative_to(repo_resolved)
                            and target_path != repo_resolved
                            and target_path.exists()
                        ):
                            if target_path.is_dir():
                                shutil.rmtree(target_path, ignore_errors=True)
                            else:
                                target_path.unlink(missing_ok=True)
                label = f"{full_name}:{rel_p}" if len(repo_dirs) > 1 else rel_p
                reverted.append(label)
                logger.warning("Reverted modification to protected path: %s", label)
    return reverted


def _run_git(
    args: list[str],
    cwd: Path,
    *,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    if (cwd / ".git").is_dir() and os.access(cwd / ".git", os.W_OK):
        sanitize_git_dir(cwd)
    env = build_safe_git_env(extra_env=extra_env)
    return subprocess.run(
        ["git", *SAFE_GIT_CONFIG_ARGS, *args],
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def format_seed_branch(project_slug: str | None, seed_number: int) -> str:
    """Return the canonical project-scoped Git branch name for a seed."""
    slug = (project_slug or "").strip().strip("/")
    if slug:
        return f"gitseed/{slug}/seed-{seed_number}"
    return f"gitseed/seed-{seed_number}"


_EMBEDDED_URL_CREDS_RE = re.compile(r"(https?://)[^@\s/'\"]+@", re.IGNORECASE)


def _redact_url_credentials(text: str, *urls: str) -> str:
    """Scrub embedded URL credentials and raw tokens from Git diagnostic strings."""
    cleaned = text
    for raw_url in urls:
        if not raw_url:
            continue
        stripped = strip_url_credentials(raw_url)
        if stripped and stripped != raw_url:
            cleaned = cleaned.replace(raw_url, stripped)
        m = re.search(r"https?://[^:/@\s]+:([^@\s]+)@", raw_url)
        if m and len(m.group(1)) >= 4:
            cleaned = cleaned.replace(m.group(1), "***")
    return _EMBEDDED_URL_CREDS_RE.sub(r"\1***@", cleaned)


def _configure_repo_after_checkout(
    target_dir: Path,
    *,
    clean_url: str,
    default_branch: str,
    seed_branch: str,
    stage: str,
    depth_args: list[str],
    auto_rebase: bool,
) -> None:
    """Fetch seed/default branch refs, optionally rebase, and strip credentials."""
    local_proto = ["-c", "protocol.file.allow=always"] if is_local_repo_path(clean_url) else []
    try:
        if stage in ("execute", "review", "merge"):
            _run_git(
                [
                    *local_proto,
                    "fetch",
                    *depth_args,
                    "origin",
                    f"+refs/heads/{default_branch}:refs/remotes/origin/{default_branch}",
                ],
                target_dir,
            )
            fetch_res = _run_git(
                [
                    *local_proto,
                    "fetch",
                    *depth_args,
                    "origin",
                    f"+refs/heads/{seed_branch}:refs/remotes/origin/{seed_branch}",
                ],
                target_dir,
            )
            if fetch_res.returncode == 0:
                checkout_res = _run_git(
                    ["checkout", "-f", "-B", seed_branch, f"refs/remotes/origin/{seed_branch}"],
                    target_dir,
                )
                if checkout_res.returncode != 0:
                    _run_git(["clean", "-fd", "-x", "-e", ".gitseed/deps.sha256"], target_dir)
                    checkout_res = _run_git(
                        ["checkout", "-f", "-B", seed_branch, f"refs/remotes/origin/{seed_branch}"],
                        target_dir,
                    )
                    if checkout_res.returncode != 0:
                        logger.warning(
                            "Failed to check out '%s' from origin/%s: %s",
                            seed_branch,
                            seed_branch,
                            (checkout_res.stderr or checkout_res.stdout or "").strip()[:240],
                        )
                if checkout_res.returncode == 0 and stage == "execute" and auto_rebase:
                    rebase_res = _run_git(
                        ["rebase", f"refs/remotes/origin/{default_branch}"],
                        target_dir,
                    )
                    if rebase_res.returncode != 0:
                        _run_git(["rebase", "--abort"], target_dir)
                        logger.info(
                            "Skipped auto-rebase of '%s' onto 'origin/%s' due to conflict; keeping branch head.",
                            seed_branch,
                            default_branch,
                        )
            elif stage == "execute":
                _run_git(["checkout", "-f", "-B", seed_branch], target_dir)
    finally:
        # Strip credentials and neutralize hooks/config unconditionally
        neutralize_git_repo(
            target_dir,
            clean_remote_url=clean_url,
            default_branch=default_branch,
        )
        _run_git(["remote", "set-url", "origin", clean_url], target_dir)
        _run_git(["config", "user.name", "GitSeed Agent"], target_dir)
        _run_git(["config", "user.email", "agent@gitseed.local"], target_dir)
        _run_git(["config", "core.untrackedCache", "true"], target_dir)

    # Exclude runner harness artifacts locally so they never pollute git status or commits
    exclude_file = target_dir / ".git" / "info" / "exclude"
    exclude_file.parent.mkdir(parents=True, exist_ok=True)
    existing_exclude = (
        exclude_file.read_text(encoding="utf-8", errors="ignore")
        if exclude_file.exists()
        else ""
    )
    extra_ignores = [
        pat
        for pat in (
            ".gitseed/output.json",
            ".gitseed/deps.sha256",
            ".gitseed/attachments/",
            ".gitseed/related/",
            ".gitseed-worktrees/",
            "opencode.json",
        )
        if pat not in existing_exclude
    ]
    if extra_ignores:
        exclude_file.write_text(
            existing_exclude.rstrip("\n") + "\n" + "\n".join(extra_ignores) + "\n",
            encoding="utf-8",
        )


@contextmanager
def _lock_git_mirror(lock_path: Path) -> Iterator[None]:
    """Acquire an advisory cross-process file lock around a shared bare Git mirror."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fh = lock_path.open("a+", encoding="utf-8")
    try:
        if fcntl is not None:
            with suppress(Exception):
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if fcntl is not None:
            with suppress(Exception):
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        with suppress(Exception):
            fh.close()


def _prepare_shared_git_mirror(
    mirror_root: Path,
    *,
    full_name: str,
    clone_url: str,
    clean_url: str,
    default_branch: str,
    depth_args: list[str],
    fetch_depth_args: list[str],
) -> Path | None:
    """Create or incrementally update a bare Git mirror in the shared project cache volume.

    Strips embedded credentials from the mirror's `config` immediately after fetch/clone so
    no token remains on the shared volume.
    """
    safe_slug = re.sub(r"[^A-Za-z0-9._-]+", "__", full_name.strip("/")) or "repo"
    mirror_dir = mirror_root / f"{safe_slug}.git"
    lock_file = mirror_root / f"{safe_slug}.lock"
    local_proto = ["-c", "protocol.file.allow=always"] if is_local_repo_path(clone_url) else []
    try:
        with _lock_git_mirror(lock_file):
            if (mirror_dir / "HEAD").is_file():
                for stale_lock in mirror_dir.rglob("*.lock"):
                    with suppress(OSError):
                        stale_lock.unlink()
                _run_git(["remote", "set-url", "origin", clone_url], mirror_dir)
                try:
                    _run_git(
                        [
                            *local_proto,
                            "fetch",
                            *fetch_depth_args,
                            "origin",
                            f"+refs/heads/{default_branch}:refs/heads/{default_branch}",
                        ],
                        mirror_dir,
                    )
                finally:
                    _run_git(["remote", "set-url", "origin", clean_url], mirror_dir)
                return mirror_dir

            if mirror_dir.exists():
                shutil.rmtree(mirror_dir, ignore_errors=True)
            mirror_root.mkdir(parents=True, exist_ok=True)
            res = _run_git(
                [
                    *local_proto,
                    "clone",
                    "--bare",
                    *depth_args,
                    "--branch",
                    default_branch,
                    clone_url,
                    str(mirror_dir),
                ],
                mirror_root,
            )
            if res.returncode != 0:
                res = _run_git(
                    [*local_proto, "clone", "--bare", *depth_args, clone_url, str(mirror_dir)],
                    mirror_root,
                )
            if res.returncode == 0 and (mirror_dir / "HEAD").is_file():
                _run_git(["remote", "set-url", "origin", clean_url], mirror_dir)
                return mirror_dir
            if mirror_dir.exists():
                shutil.rmtree(mirror_dir, ignore_errors=True)
    except Exception as exc:
        logger.debug(
            "Shared git mirror preparation skipped for %s: %s",
            full_name,
            _redact_url_credentials(str(exc), clone_url),
        )
    return None


def clone_project_repositories(
    workspace_dir: Path,
    repositories: list[dict[str, Any]],
    *,
    stage: str = "ideate",
    seed_number: int = 1,
    project_slug: str = "",
    clone_depth: int = 50,
    auto_rebase: bool = True,
    warm_workspace: bool = True,
    git_cache_dir: Path | None = None,
) -> tuple[Path, dict[str, Path]]:
    """Clone (or incrementally fetch on a warm workspace) project repositories and strip credentials.

    - Supports configurable `clone_depth` (`> 0` for shallow clone with `--filter=blob:none`,
      or `0` for full commit history so `git log` / `git blame` have complete context).
    - When `warm_workspace=True` (default) and `<target_dir>/.git` is already present from a previous
      stage/task run on the same seed volume, performs a fast incremental `git fetch` instead of
      wiping and re-cloning from scratch.
    - When `git_cache_dir` (or `GITSEED_GIT_CACHE_DIR`) is configured, maintains a bare Git mirror
      in the shared project cache volume and clones with `--reference-if-able <mirror> --dissociate`
      so new seed workspaces copy local packfiles in milliseconds without network transfer or lock contention.
    - When `auto_rebase=True` in `execute`, automatically rebases an existing seed branch
      onto `origin/<default_branch>` (safely aborting if conflicts arise) to prevent base-branch drift.

    Returns (primary_working_dir, map_of_full_name_to_dir).
    """
    workspace_dir.mkdir(parents=True, exist_ok=True)
    repo_dirs: dict[str, Path] = {}
    primary_dir: Path = workspace_dir

    effective_cache_dir = git_cache_dir
    if effective_cache_dir is None:
        env_cache = os.environ.get("GITSEED_GIT_CACHE_DIR", "").strip()
        if env_cache:
            effective_cache_dir = Path(env_cache)

    single_repo = len(repositories) == 1
    seed_branch = format_seed_branch(project_slug, seed_number)
    depth_args = (
        ["--depth", str(int(clone_depth)), "--filter=blob:none"]
        if clone_depth and int(clone_depth) > 0
        else ["--filter=blob:none"]
    )
    fetch_depth_args = (
        ["--depth", str(int(clone_depth))]
        if clone_depth and int(clone_depth) > 0
        else []
    )

    for repo in repositories:
        full_name = str(repo.get("full_name") or "").strip()
        if not full_name:
            continue
        clone_url = str(repo.get("clone_url") or "").strip()
        clean_url = strip_url_credentials(
            str(repo.get("clean_remote_url") or clone_url).strip()
        )
        default_branch = str(repo.get("default_branch") or "main").strip()
        is_primary = bool(repo.get("is_primary", False))

        folder_name = full_name.split("/")[-1] if "/" in full_name else full_name
        target_dir = workspace_dir if single_repo else (workspace_dir / folder_name)
        local_proto = (
            ["-c", "protocol.file.allow=always"] if is_local_repo_path(clone_url) else []
        )

        # Fast-path: Reuse warm repository checkout if present and warm_workspace is enabled
        if warm_workspace and (target_dir / ".git").exists():
            neutralize_git_repo(
                target_dir,
                clean_remote_url=clean_url,
                default_branch=default_branch,
            )
        if warm_workspace and (target_dir / ".git").is_dir():
            cleanup_git_locks({full_name: target_dir})
            if (
                (target_dir / "opencode.json").exists()
                and not _run_git(["ls-files", "opencode.json"], target_dir).stdout.strip()
            ):
                (target_dir / "opencode.json").unlink(missing_ok=True)
            _run_git(["remote", "set-url", "origin", clone_url], target_dir)
            try:
                warm_fetch = _run_git(
                    [
                        *local_proto,
                        "fetch",
                        *fetch_depth_args,
                        "origin",
                        f"+refs/heads/{default_branch}:refs/remotes/origin/{default_branch}",
                    ],
                    target_dir,
                )
                if warm_fetch.returncode == 0:
                    _run_git(["reset", "--hard"], target_dir)
                    if (
                        (target_dir / ".gitseed" / "run.json").exists()
                        and not _run_git(["ls-files", ".gitseed/run.json"], target_dir).stdout.strip()
                    ):
                        (target_dir / ".gitseed" / "run.json").unlink(missing_ok=True)
                    for stale_meta in ("output.json", "prompt.md"):
                        (target_dir / ".gitseed" / stale_meta).unlink(missing_ok=True)
                    _run_git(
                        [
                            "clean",
                            "-fdx",
                            "-e",
                            ".gitseed",
                            "-e",
                            "node_modules",
                            "-e",
                            ".venv",
                            "-e",
                            "venv",
                        ],
                        target_dir,
                    )
                    _run_git(
                        ["checkout", "-f", "-B", default_branch, f"refs/remotes/origin/{default_branch}"],
                        target_dir,
                    )
                    _configure_repo_after_checkout(
                        target_dir,
                        clean_url=clean_url,
                        default_branch=default_branch,
                        seed_branch=seed_branch,
                        stage=stage,
                        depth_args=fetch_depth_args,
                        auto_rebase=auto_rebase,
                    )
                    repo_dirs[full_name] = target_dir
                    if is_primary or primary_dir == workspace_dir:
                        primary_dir = target_dir
                    logger.info("Reused warm workspace repository '%s' via incremental fetch.", full_name)
                    continue
            finally:
                if (target_dir / ".git").is_dir():
                    _run_git(["remote", "set-url", "origin", clean_url], target_dir)

        if target_dir == workspace_dir:
            # Ensure /workspace is completely empty before `git clone` so git does not fail
            # with "destination path '/workspace' already exists and is not an empty directory"
            for child in workspace_dir.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        elif target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=True)

        mirror_dir: Path | None = None
        if effective_cache_dir is not None:
            mirror_dir = _prepare_shared_git_mirror(
                effective_cache_dir,
                full_name=full_name,
                clone_url=clone_url,
                clean_url=clean_url,
                default_branch=default_branch,
                depth_args=depth_args,
                fetch_depth_args=fetch_depth_args,
            )
        ref_args = (
            ["--reference-if-able", str(mirror_dir), "--dissociate"]
            if mirror_dir is not None
            else []
        )

        clone_cmd = [
            *local_proto,
            "clone",
            *ref_args,
            *depth_args,
            "--branch",
            default_branch,
            clone_url,
            str(target_dir),
        ]
        res = _run_git(clone_cmd, workspace_dir)
        if res.returncode != 0:
            # Fallback without --branch in case default_branch differs on remote
            fallback_cmd = [
                *local_proto,
                "clone",
                *ref_args,
                *depth_args,
                clone_url,
                str(target_dir),
            ]
            res = _run_git(fallback_cmd, workspace_dir)

        if res.returncode == 0 and (target_dir / ".git").exists():
            _configure_repo_after_checkout(
                target_dir,
                clean_url=clean_url,
                default_branch=default_branch,
                seed_branch=seed_branch,
                stage=stage,
                depth_args=fetch_depth_args,
                auto_rebase=auto_rebase,
            )
            repo_dirs[full_name] = target_dir
            if is_primary or primary_dir == workspace_dir:
                primary_dir = target_dir
        else:
            # Remove any partial `.git` checkout so embedded `clone_url` tokens never remain in /workspace
            if target_dir == workspace_dir:
                shutil.rmtree(workspace_dir / ".git", ignore_errors=True)
            elif target_dir.exists():
                shutil.rmtree(target_dir, ignore_errors=True)
            redacted_detail = _redact_url_credentials(
                (res.stderr or res.stdout or "").strip(),
                clone_url,
            )[:200]
            logger.warning(
                "Could not clone repository '%s' (continuing with workspace): %s",
                full_name,
                redacted_detail,
            )

    return primary_dir, repo_dirs


def get_repo_heads(repo_dirs: dict[str, Path]) -> dict[str, str]:
    """Return the current HEAD commit SHA for each cloned repository."""
    heads: dict[str, str] = {}
    for full_name, repo_dir in repo_dirs.items():
        if (repo_dir / ".git").exists():
            res = _run_git(["rev-parse", "HEAD"], repo_dir)
            if res.returncode == 0 and res.stdout.strip():
                heads[full_name] = res.stdout.strip()
    return heads


def has_remote_seed_branch(
    repo_dirs: dict[str, Path],
    project_slug: str | None,
    seed_number: int,
) -> bool:
    """Return True if at least one cloned repository checked out `seed_branch` from `origin`."""
    git_dirs = [d for d in repo_dirs.values() if (d / ".git").exists()]
    if not git_dirs:
        return True
    seed_branch = format_seed_branch(project_slug, seed_number)
    for repo_dir in git_dirs:
        branch_res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir)
        if branch_res.returncode == 0 and branch_res.stdout.strip() == seed_branch:
            return True
    return False


def _is_ignored_harness_status_line(line: str) -> bool:
    """Return True if a `git status --porcelain` line refers only to internal runner artifacts."""
    stripped = line.strip()
    if not stripped:
        return True
    path_part = line[3:].strip() if len(line) > 3 else stripped
    if " -> " in path_part:
        path_part = path_part.split(" -> ", 1)[1].strip()
    path_part = path_part.strip('"').strip("/")
    return is_internal_excluded_path(path_part)


def has_workspace_code_changes(
    repo_dirs: dict[str, Path],
    initial_heads: dict[str, str],
) -> bool:
    """Return True if any cloned repository has new commits or modified/untracked files."""
    for full_name, repo_dir in repo_dirs.items():
        if not (repo_dir / ".git").exists():
            continue
        rev_res = _run_git(["rev-parse", "HEAD"], repo_dir)
        current_head = rev_res.stdout.strip() if rev_res.returncode == 0 else ""
        if current_head and current_head != initial_heads.get(full_name, ""):
            return True
        status_res = _run_git(["status", "--porcelain"], repo_dir)
        dirty_lines = [
            line
            for line in status_res.stdout.splitlines()
            if not _is_ignored_harness_status_line(line)
        ]
        if dirty_lines or (repo_dir / ".gitseed" / "run.json").is_file():
            return True
    return False


async def push_execute_changes_after_agent_exit(
    *,
    repo_dirs: dict[str, Path],
    seed_number: int,
    project_slug: str = "",
    stage_output: dict[str, Any],
    api_url: str,
    run_token: str,
    initial_heads: dict[str, str] | None = None,
    blocked_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Commit any uncommitted agent work and push using a post-run write token."""
    if not repo_dirs:
        return stage_output

    cleanup_git_locks(repo_dirs)
    if blocked_paths:
        reverted = enforce_blocked_paths(
            repo_dirs,
            initial_heads or {},
            blocked_paths,
        )
        if reverted:
            stage_output["blocked_paths_reverted"] = reverted
            existing_summary = str(stage_output.get("summary_comment") or "").strip()
            blocked_md = ", ".join(f"`{p}`" for p in reverted)
            note = f"\n\n> **Governance Guard**: Reverted changes to protected path(s): {blocked_md}"
            if note not in existing_summary:
                stage_output["summary_comment"] = f"{existing_summary}{note}".strip()

    desired_branch = format_seed_branch(project_slug, seed_number)
    stage_output["branch_name"] = desired_branch
    clean_slug = (project_slug or "").strip().strip("/")
    commit_scope = f"{clean_slug}#seed-{seed_number}" if clean_slug else f"seed-{seed_number}"

    push_errors: list[str] = []
    for full_name, repo_dir in repo_dirs.items():
        if not (repo_dir / ".git").exists():
            continue

        sanitize_git_dir(repo_dir)
        # Remove untracked harness config file before inspecting or staging repo changes
        if (
            (repo_dir / "opencode.json").exists()
            and not _run_git(["ls-files", "opencode.json"], repo_dir).stdout.strip()
        ):
            (repo_dir / "opencode.json").unlink(missing_ok=True)

        # Check if there are uncommitted changes or new commits
        status_res = _run_git(["status", "--porcelain", "-uall"], repo_dir)
        has_dirty = bool(status_res.stdout.strip())

        _run_git(["checkout", "-B", desired_branch], repo_dir)
        if has_dirty:
            _run_git(["add", "-A"], repo_dir)
            # Exclude internal .gitseed/* scratch files while preserving .gitseed/run.json
            _exclude_internal_paths(
                repo_dir,
                base_sha=(initial_heads or {}).get(full_name),
                remove_worktree_files=False,
            )
            diff_cached = _run_git(["diff", "--cached", "--quiet"], repo_dir)
            if diff_cached.returncode != 0:
                _run_git(
                    ["commit", "-m", f"feat({commit_scope}): execute stage changes"],
                    repo_dir,
                )

        rev_res = _run_git(["rev-parse", "HEAD"], repo_dir)
        head_sha = rev_res.stdout.strip() if rev_res.returncode == 0 else None
        if head_sha:
            stage_output["commit_sha"] = head_sha

        # Request short-lived push token from Core now that agent has exited
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(
                    f"{api_url.rstrip('/')}/api/v1/runner/git-push-token",
                    headers={"Authorization": f"Bearer {run_token}"},
                    json={"repository_full_name": full_name},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    push_url = str(data.get("push_url") or "").strip()
                    clean_url = str(data.get("clean_remote_url") or "").strip()
                    if push_url:
                        local_push_proto = (
                            ["-c", "protocol.file.allow=always"]
                            if is_local_repo_path(push_url)
                            else []
                        )
                        # Git hosts occasionally fail server-side (e.g. GitHub "fatal error in
                        # commit_refs"); retry with backoff before giving up.
                        push_res = None
                        for attempt, delay in enumerate((0, 5, 15), start=1):
                            if delay:
                                await asyncio.sleep(delay)
                            push_res = _run_git(
                                [
                                    *local_push_proto,
                                    "push",
                                    "--force-with-lease",
                                    push_url,
                                    f"HEAD:refs/heads/{desired_branch}",
                                ],
                                repo_dir,
                            )
                            if push_res.returncode != 0:
                                # Fallback to plain force push if --force-with-lease failed on new ref
                                push_res = _run_git(
                                    [
                                        *local_push_proto,
                                        "push",
                                        "--force",
                                        push_url,
                                        f"HEAD:refs/heads/{desired_branch}",
                                    ],
                                    repo_dir,
                                )
                            if push_res.returncode == 0:
                                break
                            logger.warning(
                                "Git push attempt %d failed for %s (%s): %s",
                                attempt,
                                full_name,
                                desired_branch,
                                _redact(push_res.stderr or push_res.stdout or "", push_url)[:300],
                            )
                        if push_res is not None and push_res.returncode == 0:
                            logger.info(
                                "Pushed branch '%s' (%s) to %s",
                                desired_branch,
                                head_sha or "HEAD",
                                full_name,
                            )
                        else:
                            detail = _redact(
                                (push_res.stderr or push_res.stdout or "") if push_res else "", push_url
                            )
                            push_errors.append(f"{full_name}: {_summarize_push_error(detail)}")
                    else:
                        push_errors.append(f"{full_name}: no push credential was issued")
                    if clean_url:
                        _run_git(["remote", "set-url", "origin", clean_url], repo_dir)
                else:
                    push_errors.append(
                        f"{full_name}: could not get a push credential (HTTP {resp.status_code})"
                    )
        except Exception as exc:
            logger.warning("Post-agent git push skipped or failed for %s: %s", full_name, exc)
            push_errors.append(f"{full_name}: {exc}")

    if push_errors:
        # Surfaced by the runner as a failed run: later stages must not review a branch
        # that never reached the remote.
        stage_output["push_errors"] = push_errors
    return stage_output


def _redact(text: str, push_url: str) -> str:
    """Never echo the tokenized push URL back into logs or errors."""
    return text.replace(push_url, "<remote>") if push_url else text


def _summarize_push_error(detail: str) -> str:
    """The most useful line of git's push output (the remote's reason), trimmed."""
    lines = [ln.strip() for ln in detail.splitlines() if ln.strip()]
    for ln in lines:
        if ln.startswith("remote:") and len(ln) > len("remote:") + 3:
            return ln[:240]
    for ln in lines:
        if "rejected" in ln or ln.startswith(("error:", "fatal:")):
            return ln[:240]
    return (lines[-1] if lines else "push failed")[:240]


def collect_workspace_git_summary(
    repo_dirs: dict[str, Path],
    repositories: list[dict[str, Any]],
) -> str:
    """Return a concise Markdown summary of recent git commits and diff stats against default_branch."""
    if not repo_dirs:
        return ""
    default_branches: dict[str, str] = {}
    for r in repositories:
        if isinstance(r, dict) and r.get("full_name"):
            default_branches[str(r["full_name"]).strip()] = str(
                r.get("default_branch") or "main"
            ).strip()

    sections: list[str] = []
    for full_name, repo_dir in repo_dirs.items():
        if not (repo_dir / ".git").exists():
            continue
        def_branch = default_branches.get(full_name, "main")
        branch_res = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_dir)
        cur_branch = branch_res.stdout.strip() if branch_res.returncode == 0 else "HEAD"
        sha_res = _run_git(["rev-parse", "--short", "HEAD"], repo_dir)
        short_sha = sha_res.stdout.strip() if sha_res.returncode == 0 else ""

        log_res = _run_git(["log", "--oneline", "-n", "8"], repo_dir)
        log_text = log_res.stdout.strip() if log_res.returncode == 0 else ""

        diff_stat = ""
        for base_ref in (f"origin/{def_branch}", def_branch):
            d_res = _run_git(["diff", "--stat", f"{base_ref}...HEAD"], repo_dir)
            if d_res.returncode == 0 and d_res.stdout.strip():
                diff_stat = d_res.stdout.strip()
                break

        repo_lines = [
            f"### Repository `{full_name}` (branch `{cur_branch}` @ `{short_sha}`)"
        ]
        if log_text:
            repo_lines.extend(["**Recent Commits (`git log --oneline -n 8`)**:", "```", log_text, "```"])
        if diff_stat:
            repo_lines.extend(
                [
                    f"**Branch Diff Stat vs `{def_branch}` (`git diff --stat`)**:",
                    "```",
                    diff_stat,
                    "```",
                ]
            )
        sections.append("\n".join(repo_lines))
    return "\n\n".join(sections)


def extract_task_target_files(task: dict[str, Any]) -> list[str]:
    """Return normalized relative POSIX target file paths for a task dict."""
    raw_list = task.get("target_files")
    results: list[str] = []
    if isinstance(raw_list, list):
        for item in raw_list:
            cleaned = str(item or "").strip().removeprefix("./").lstrip("/")
            if cleaned and cleaned not in results:
                results.append(cleaned)
    if results:
        return results

    desc = str(task.get("description") or "")
    for line in desc.splitlines():
        if "**Files**" in line or "target_files" in line.lower():
            for match in re.findall(r"`([^`\s]+\.[A-Za-z0-9_]+)`", line):
                cleaned = match.strip().removeprefix("./").lstrip("/")
                if cleaned and cleaned not in results:
                    results.append(cleaned)
    return results


def tasks_have_disjoint_targets(
    task_a: dict[str, Any],
    task_b: dict[str, Any],
) -> bool:
    """Return True if both tasks declare explicit target_files and share no overlapping paths."""
    files_a = extract_task_target_files(task_a)
    files_b = extract_task_target_files(task_b)
    if not files_a or not files_b:
        return False
    for pa in files_a:
        for pb in files_b:
            if (
                pa == pb
                or pa.startswith(f"{pb.rstrip('/')}/")
                or pb.startswith(f"{pa.rstrip('/')}/")
            ):
                return False
    return True


def create_task_worktree(
    primary_dir: Path,
    worktree_dir: Path,
    branch_name: str,
) -> bool:
    """Create an isolated git worktree checked out at primary_dir HEAD on `branch_name`."""
    if not (primary_dir / ".git").exists():
        return False
    if worktree_dir.exists():
        _run_git(["worktree", "remove", "--force", str(worktree_dir)], primary_dir)
        shutil.rmtree(worktree_dir, ignore_errors=True)
    worktree_dir.parent.mkdir(parents=True, exist_ok=True)
    res = _run_git(
        ["worktree", "add", "-B", branch_name, str(worktree_dir), "HEAD"],
        primary_dir,
    )
    return res.returncode == 0 and worktree_dir.is_dir()


def cleanup_task_worktree(
    primary_dir: Path,
    worktree_dir: Path,
    branch_name: str,
) -> None:
    """Remove an aborted or failed task git worktree and delete its temporary branch without merging."""
    try:
        _run_git(["worktree", "remove", "--force", str(worktree_dir)], primary_dir)
    finally:
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)
    _run_git(["branch", "-D", branch_name], primary_dir)


def merge_and_remove_task_worktree(
    primary_dir: Path,
    worktree_dir: Path,
    branch_name: str,
    commit_message: str = "feat: complete parallel task in sub-worktree",
) -> bool:
    """Commit worktree changes, remove worktree, and merge `branch_name` into `primary_dir`."""
    sanitize_git_dir(primary_dir)
    try:
        if worktree_dir.is_dir():
            if (
                (worktree_dir / "opencode.json").exists()
                and _run_git(
                    ["ls-files", "--error-unmatch", "opencode.json"], worktree_dir
                ).returncode
                != 0
            ):
                (worktree_dir / "opencode.json").unlink(missing_ok=True)
            status_res = _run_git(["status", "--porcelain", "-uall"], worktree_dir)
            if status_res.returncode == 0 and status_res.stdout.strip():
                _run_git(["add", "-A"], worktree_dir)
                _exclude_internal_paths(worktree_dir, remove_worktree_files=False)
                diff_cached = _run_git(["diff", "--cached", "--quiet"], worktree_dir)
                if diff_cached.returncode != 0:
                    _run_git(["commit", "-m", commit_message], worktree_dir)
    finally:
        _run_git(["worktree", "remove", "--force", str(worktree_dir)], primary_dir)
        if worktree_dir.exists():
            shutil.rmtree(worktree_dir, ignore_errors=True)

    merge_res = _run_git(["merge", "--no-edit", branch_name], primary_dir)
    if merge_res.returncode != 0:
        _run_git(["merge", "--abort"], primary_dir)
        _run_git(["branch", "-D", branch_name], primary_dir)
        return False

    _run_git(["branch", "-D", branch_name], primary_dir)
    return True


