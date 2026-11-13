"""Three-container broker lifecycle modes for GitSeed Runner: prepare, agent, finalize."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from gitseed_runner.git_ops import (
    _run_git,
    clone_project_repositories,
    format_seed_branch,
    get_repo_heads,
    has_remote_seed_branch,
)
from gitseed_runner.gitsafe import (
    DEFAULT_MAX_DIFF_BYTES,
    DEFAULT_MAX_FILE_BYTES,
    construct_clean_commit_and_verify_policy,
    is_local_repo_path,
    neutralize_git_repo,
    push_with_askpass,
    run_safe_git,
    sanitize_git_dir,
    strip_url_credentials,
)
from gitseed_runner.schemas import validate_stage_output_data

logger = logging.getLogger("gitseed_runner")


def get_ctl_dir() -> Path:
    """Return the per-run control volume directory (/gitseed-ctl by default)."""
    return Path(os.environ.get("GITSEED_CTL_DIR", "/gitseed-ctl"))


def get_secrets_dir() -> Path:
    """Return the trusted prepare/finalize secrets volume directory (/gitseed-secrets by default)."""
    return Path(os.environ.get("GITSEED_SECRETS_DIR", "/gitseed-secrets"))


def _meta_file_path(run_id: str, *, prefer_secrets: bool = True) -> Path:
    """Return the path to {run_id}.meta.json, preferring /gitseed-secrets when present."""
    safe_id = (run_id or "run").strip() or "run"
    secrets_dir = get_secrets_dir()
    secrets_meta = secrets_dir / f"{safe_id}.meta.json"
    if prefer_secrets and secrets_meta.is_file() and not secrets_meta.is_symlink():
        return secrets_meta
    return get_ctl_dir() / f"{safe_id}.meta.json"


def get_workspace_dir() -> Path:
    """Return the shared workspace directory (/workspace by default)."""
    return Path(os.environ.get("GITSEED_WORKSPACE", "/workspace"))


def resolve_existing_repo_dirs(
    workspace_dir: Path, repositories: list[dict[str, Any]]
) -> tuple[Path, dict[str, Path]]:
    """Resolve primary_dir and repo_dirs map in an already-prepared /workspace without running git clone."""
    repo_dirs: dict[str, Path] = {}
    primary_dir: Path = workspace_dir
    single_repo = len(repositories) == 1
    for repo in repositories:
        full_name = str(repo.get("full_name") or "").strip()
        if not full_name:
            continue
        is_primary = bool(repo.get("is_primary", False))
        folder_name = full_name.split("/")[-1] if "/" in full_name else full_name
        target_dir = workspace_dir if single_repo else (workspace_dir / folder_name)
        if target_dir.exists():
            repo_dirs[full_name] = target_dir
            if is_primary or primary_dir == workspace_dir:
                primary_dir = target_dir
    return primary_dir, repo_dirs


def scrub_context_for_agent(ctx: dict[str, Any]) -> dict[str, Any]:
    """Return a deep-copied context dict with all repository clone credentials removed."""
    scrubbed = json.loads(json.dumps(ctx))
    repos = scrubbed.get("repositories")
    if isinstance(repos, list):
        for repo in repos:
            if isinstance(repo, dict):
                clean_url = strip_url_credentials(
                    str(repo.get("clean_remote_url") or repo.get("clone_url") or "")
                )
                repo["clean_remote_url"] = clean_url
                repo["clone_url"] = clean_url
    return scrubbed


def _get_remote_seed_heads(
    repo_dirs: dict[str, Path], seed_branch: str
) -> dict[str, str]:
    """Return map of {full_name: remote_seed_branch_sha_or_empty_string} after prepare fetch."""
    remote_heads: dict[str, str] = {}
    for full_name, repo_dir in repo_dirs.items():
        rc, out, _ = run_safe_git(
            ["rev-parse", "--verify", f"refs/remotes/origin/{seed_branch}"],
            repo_dir,
        )
        remote_heads[full_name] = out.strip() if rc == 0 and out.strip() else ""
    return remote_heads


async def post_prepare_complete(
    api_url: str,
    prepare_token: str,
    *,
    base_heads: dict[str, str],
    remote_seed_heads: dict[str, str],
    seed_branch: str,
    error: str | None = None,
) -> None:
    """Report trusted base_heads and remote_seed_heads from prepare container to Core."""
    payload: dict[str, Any] = {
        "base_heads": base_heads,
        "remote_seed_heads": remote_seed_heads,
        "seed_branch": seed_branch,
        "error": error,
    }
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{api_url.rstrip('/')}/api/v1/runner/prepare-complete",
            headers={"Authorization": f"Bearer {prepare_token}"},
            json=payload,
        )
        resp.raise_for_status()


async def post_cli_auth_refresh(
    api_url: str,
    agent_token: str,
    cli_auth_bundle: dict[str, str],
) -> bool:
    """Send allowlisted refreshed CLI OAuth credentials from agent container to Core."""
    if not cli_auth_bundle:
        return False
    async with httpx.AsyncClient(timeout=15.0) as client:
        resp = await client.post(
            f"{api_url.rstrip('/')}/api/v1/runner/cli-auth-refresh",
            headers={"Authorization": f"Bearer {agent_token}"},
            json={"cli_auth_bundle": cli_auth_bundle},
        )
        return resp.status_code in (200, 204)


async def fetch_finalize_context(
    api_url: str,
    finalize_token: str,
) -> dict[str, Any]:
    """Fetch trusted finalize context (base_heads, blocked_paths, repositories) from Core."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{api_url.rstrip('/')}/api/v1/runner/finalize-context",
            headers={"Authorization": f"Bearer {finalize_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


async def request_git_push_credential(
    api_url: str,
    finalize_token: str,
    repository_full_name: str,
) -> dict[str, Any]:
    """Request single-use per-repo push credentials in finalize mode."""
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{api_url.rstrip('/')}/api/v1/runner/git-push-token",
            headers={"Authorization": f"Bearer {finalize_token}"},
            json={"repository_full_name": repository_full_name},
        )
        resp.raise_for_status()
        data = resp.json()
        return data if isinstance(data, dict) else {}


def _resolve_finalize_wait_seconds() -> float:
    """Compute finalize wait timeout dynamically from RUNNER_TIMEOUT_SECONDS when unset."""
    explicit = os.environ.get("GITSEED_FINALIZE_WAIT_SECONDS", "").strip()
    if explicit:
        try:
            return float(explicit)
        except ValueError:
            pass
    raw_runner_timeout = os.environ.get("RUNNER_TIMEOUT_SECONDS", "3600").strip() or "3600"
    try:
        runner_timeout = int(float(raw_runner_timeout))
    except ValueError:
        runner_timeout = 3600
    return float(max(3600, runner_timeout + 600))


def write_agent_done_marker(
    ctl_dir: Path,
    run_id: str,
    exit_code: int,
    *,
    error: str | None = None,
) -> Path:
    """Write the `.agent-done` JSON completion marker to `/gitseed-ctl`."""
    ctl_dir.mkdir(parents=True, exist_ok=True)
    effective_id = (run_id or os.environ.get("GITSEED_RUN_ID", "") or "run").strip() or "run"
    payload: dict[str, Any] = {
        "exit_code": int(exit_code),
        "completed_at": datetime.now(UTC).isoformat(),
    }
    if error:
        payload["error"] = error
    serialized = json.dumps(payload, indent=2)
    marker_path = ctl_dir / f"{effective_id}.agent-done"
    marker_path.write_text(serialized, encoding="utf-8")
    run_id_env = os.environ.get("GITSEED_RUN_ID", "").strip()
    if run_id_env and run_id_env != effective_id:
        (ctl_dir / f"{run_id_env}.agent-done").write_text(serialized, encoding="utf-8")
    return marker_path


async def run_agent_phase(
    agent_coro_fn: Any = None,
    *,
    run_id: str | None = None,
    ctl_dir: Path | None = None,
) -> int:
    """Run the `agent` broker phase, always writing `.agent-done` JSON inside `finally:`."""
    target_ctl = ctl_dir if ctl_dir is not None else get_ctl_dir()
    resolved_run_id = (run_id or os.environ.get("GITSEED_RUN_ID", "") or "run").strip() or "run"
    rc = 1
    unhandled_error: str | None = None
    try:
        if agent_coro_fn is None:
            from gitseed_runner.main import _run_agent_or_standalone_job

            res = await _run_agent_or_standalone_job(is_broker_agent_mode=True)
        else:
            res = await agent_coro_fn()
        rc = int(res) if isinstance(res, int) else 0
        return rc
    except BaseException as exc:
        rc = 1
        unhandled_error = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        with suppress(OSError):
            target_ctl.mkdir(parents=True, exist_ok=True)
            if unhandled_error is not None and not (target_ctl / "agent-result.json").exists():
                (target_ctl / "agent-result.json").write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "exit_code": rc,
                            "error": f"Unhandled agent phase error: {unhandled_error}",
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            existing_markers = list(target_ctl.glob("*.agent-done"))
            target_marker = target_ctl / f"{resolved_run_id}.agent-done"
            if not existing_markers or not target_marker.exists() or unhandled_error is not None:
                write_agent_done_marker(
                    target_ctl,
                    resolved_run_id,
                    rc,
                    error=unhandled_error,
                )


async def wait_for_marker(
    marker_path: Path, timeout_seconds: float | None = None
) -> bool:
    """Wait until marker_path exists on /gitseed-ctl."""
    effective_timeout = (
        timeout_seconds if timeout_seconds is not None else _resolve_finalize_wait_seconds()
    )
    deadline = asyncio.get_running_loop().time() + effective_timeout
    while asyncio.get_running_loop().time() < deadline:
        if marker_path.exists():
            return True
        await asyncio.sleep(0.25)
    return marker_path.exists()


async def run_prepare_mode(
    *,
    fetch_context_fn: Any,
    download_attachments_fn: Any,
    write_related_seeds_fn: Any,
) -> int:
    """Execute the trusted `prepare` container lifecycle."""
    api_url = os.environ.get("GITSEED_API_URL", "http://api:8420")
    prepare_token = os.environ.get("GITSEED_RUN_TOKEN", "")
    run_id_env = os.environ.get("GITSEED_RUN_ID", "").strip()
    if not prepare_token:
        logger.error("GITSEED_RUN_TOKEN is missing in prepare mode")
        return 1

    workspace_root = get_workspace_dir()
    ctl_dir = get_ctl_dir()
    ctl_dir.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)

    try:
        ctx = await fetch_context_fn(api_url, prepare_token)
    except Exception as exc:
        logger.error("prepare: failed to fetch execution context: %s", exc)
        return 1

    run_id = str(ctx.get("run_id") or run_id_env or "run").strip()
    stage = str(ctx.get("stage") or "ideate").lower()
    seed_number = int(ctx.get("seed_number") or 1)
    project_slug = str(ctx.get("project_slug") or "").strip()
    seed_branch = format_seed_branch(project_slug, seed_number)
    sandbox_cfg = (
        ctx.get("sandbox_config")
        if isinstance(ctx.get("sandbox_config"), dict)
        else {}
    )
    raw_depth = sandbox_cfg.get("git_clone_depth", sandbox_cfg.get("clone_depth", 50))
    try:
        clone_depth = int(raw_depth) if raw_depth is not None else 50
    except (TypeError, ValueError):
        clone_depth = 50
    auto_rebase = bool(
        sandbox_cfg.get(
            "auto_rebase_seed_branch", sandbox_cfg.get("auto_rebase", True)
        )
    )
    warm_workspace = bool(
        sandbox_cfg.get(
            "enable_warm_workspace", sandbox_cfg.get("warm_workspace", True)
        )
    )

    repositories = ctx.get("repositories") or []
    repo_list = [r for r in repositories if isinstance(r, dict)]

    # Neutralize any pre-existing warm repositories BEFORE running any git commands
    single_repo = len(repo_list) == 1
    for repo in repo_list:
        full_name = str(repo.get("full_name") or "").strip()
        if not full_name:
            continue
        clean_url = strip_url_credentials(
            str(repo.get("clean_remote_url") or repo.get("clone_url") or "")
        )
        default_branch = str(repo.get("default_branch") or "main").strip()
        folder_name = full_name.split("/")[-1] if "/" in full_name else full_name
        target_dir = workspace_root if single_repo else (workspace_root / folder_name)
        if (target_dir / ".git").exists() or (target_dir / ".git").is_symlink():
            neutralize_git_repo(
                target_dir,
                clean_remote_url=clean_url,
                default_branch=default_branch,
            )

    clone_kwargs: dict[str, Any] = {
        "stage": stage,
        "seed_number": seed_number,
        "project_slug": project_slug,
    }
    if clone_depth != 50:
        clone_kwargs["clone_depth"] = clone_depth
    if not auto_rebase:
        clone_kwargs["auto_rebase"] = False
    if not warm_workspace:
        clone_kwargs["warm_workspace"] = False

    _, repo_dirs = clone_project_repositories(
        workspace_root,
        repo_list,
        **clone_kwargs,
    )

    remote_seed_heads = _get_remote_seed_heads(repo_dirs, seed_branch)
    prep_error: str | None = None
    if repo_list and not repo_dirs and stage in ("execute", "review", "merge"):
        prep_error = (
            "Failed to clone project repository into /workspace. "
            "Verify the repository URL and Git backend token permissions."
        )
    elif (
        stage in ("review", "merge")
        and repo_dirs
        and not has_remote_seed_branch(repo_dirs, project_slug, seed_number)
    ):
        if any(str(v).strip() for v in remote_seed_heads.values()):
            prep_error = (
                f"Failed to check out remote seed branch '{seed_branch}' in /workspace."
            )
        else:
            prep_error = (
                f"Seed branch '{seed_branch}' does not exist on the remote repository. "
                "Move the seed back to Execute to push the branch before reviewing."
            )
    if prep_error:
        logger.error("prepare: %s", prep_error)

    # Ensure every cloned repo has neutralized hooks/config and zero credentials on disk
    known_repo_dirs = {p.resolve() for p in repo_dirs.values()}
    for repo in repo_list:
        full_name = str(repo.get("full_name") or "").strip()
        r_dir = repo_dirs.get(full_name)
        if r_dir and (r_dir / ".git").is_dir():
            clean_url = strip_url_credentials(
                str(repo.get("clean_remote_url") or repo.get("clone_url") or "")
            )
            default_branch = str(repo.get("default_branch") or "main").strip()
            neutralize_git_repo(
                r_dir,
                clean_remote_url=clean_url,
                default_branch=default_branch,
            )
            _run_git(["config", "user.name", "GitSeed Agent"], r_dir)
            _run_git(["config", "user.email", "agent@gitseed.local"], r_dir)

    # Sweep workspace root and immediate subdirectories so failed/partial clones can never leak credentials
    candidate_dirs = [
        workspace_root,
        *(
            child
            for child in workspace_root.iterdir()
            if child.is_dir() and not child.is_symlink() and child.name != ".git"
        ),
    ]
    for cand in candidate_dirs:
        git_sub = cand / ".git"
        if not git_sub.exists() and not git_sub.is_symlink():
            continue
        if cand.resolve() not in known_repo_dirs:
            if git_sub.is_symlink() or git_sub.is_file():
                git_sub.unlink(missing_ok=True)
            else:
                shutil.rmtree(git_sub, ignore_errors=True)
            continue
        cfg_file = git_sub / "config"
        if cfg_file.is_file():
            raw_cfg = cfg_file.read_text(encoding="utf-8", errors="replace")
            if "@" in raw_cfg and ("http://" in raw_cfg or "https://" in raw_cfg):
                neutralize_git_repo(cand)

    base_heads = get_repo_heads(repo_dirs)
    remote_seed_heads = _get_remote_seed_heads(repo_dirs, seed_branch)
    for repo in repo_list:
        full_name = str(repo.get("full_name") or "").strip()
        r_dir = repo_dirs.get(full_name)
        if not full_name or not r_dir or not (r_dir / ".git").exists():
            continue
        default_branch = str(repo.get("default_branch") or "main").strip()
        rc_def, out_def, _ = run_safe_git(
            ["rev-parse", "--verify", f"refs/remotes/origin/{default_branch}"],
            r_dir,
        )
        remote_default_sha = out_def.strip() if rc_def == 0 and out_def.strip() else ""
        remote_seed_sha = remote_seed_heads.get(full_name, "")
        local_head = base_heads.get(full_name, "")
        if (
            local_head
            and remote_default_sha
            and local_head not in (remote_seed_sha, remote_default_sha)
        ):
            base_heads[full_name] = remote_default_sha

    # Download attachments and write related seeds into /workspace/.gitseed
    output_file = workspace_root / ".gitseed" / "output.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        output_file.unlink(missing_ok=True)

    ctx["attachments_local"] = await download_attachments_fn(
        api_url,
        prepare_token,
        [a for a in (ctx.get("attachments") or []) if isinstance(a, dict)],
        workspace_root / ".gitseed" / "attachments",
    )
    ctx["related_seeds_local"] = write_related_seeds_fn(
        [r for r in (ctx.get("related_seeds") or []) if isinstance(r, dict)],
        workspace_root / ".gitseed" / "related",
    )

    # Report trusted base_heads to Core
    with suppress(Exception):
        await post_prepare_complete(
            api_url,
            prepare_token,
            base_heads=base_heads,
            remote_seed_heads=remote_seed_heads,
            seed_branch=seed_branch,
            error=prep_error,
        )

    # Write trusted prepare metadata to /gitseed-secrets (if writable) and /gitseed-ctl
    meta_payload: dict[str, Any] = {
        "run_id": run_id,
        "stage": stage,
        "seed_number": seed_number,
        "project_slug": project_slug,
        "seed_branch": seed_branch,
        "base_heads": base_heads,
        "remote_seed_heads": remote_seed_heads,
        "min_acceptance_criteria": ctx.get("min_acceptance_criteria"),
        "max_acceptance_criteria": ctx.get("max_acceptance_criteria"),
        "max_tasks": ctx.get("max_tasks"),
        "error": prep_error,
        "prepared_at": datetime.now(UTC).isoformat(),
    }
    meta_json = json.dumps(meta_payload, indent=2)
    with suppress(OSError):
        (ctl_dir / f"{run_id}.meta.json").write_text(meta_json, encoding="utf-8")
        if run_id_env and run_id_env != run_id:
            (ctl_dir / f"{run_id_env}.meta.json").write_text(
                meta_json, encoding="utf-8"
            )
    secrets_dir = get_secrets_dir()
    if secrets_dir.is_dir() and os.access(secrets_dir, os.W_OK):
        with suppress(OSError):
            (secrets_dir / f"{run_id}.meta.json").write_text(
                meta_json, encoding="utf-8"
            )
            if run_id_env and run_id_env != run_id:
                (secrets_dir / f"{run_id_env}.meta.json").write_text(
                    meta_json, encoding="utf-8"
                )

    # Write scrubbed agent context and marker to /gitseed-ctl
    agent_ctx = scrub_context_for_agent(ctx)
    if prep_error:
        agent_ctx["_prepare_error"] = prep_error
    (ctl_dir / "agent-context.json").write_text(
        json.dumps(agent_ctx, indent=2),
        encoding="utf-8",
    )
    marker = ctl_dir / f"{run_id}.prepared"
    marker.write_text(datetime.now(UTC).isoformat(), encoding="utf-8")
    if run_id_env and run_id_env != run_id:
        (ctl_dir / f"{run_id_env}.prepared").write_text(
            datetime.now(UTC).isoformat(), encoding="utf-8"
        )
    logger.info("prepare mode completed for run %s (base_heads=%s)", run_id, base_heads)
    return 0 if not prep_error else 1


def _parse_credentials_from_push_resp(push_resp: dict[str, Any]) -> tuple[str, str, str]:
    """Extract (clean_remote_url, username, password_or_token) from /git-push-token response."""
    clean_url = strip_url_credentials(
        str(push_resp.get("clean_remote_url") or push_resp.get("push_url") or "")
    )
    username = str(push_resp.get("username") or "x-access-token").strip()
    token = str(push_resp.get("push_token") or "").strip()
    if not token:
        push_url = str(push_resp.get("push_url") or "")
        if "://" in push_url and "@" in push_url:
            cred_part = push_url.split("://", 1)[1].split("@", 1)[0]
            if ":" in cred_part:
                u, p = cred_part.split(":", 1)
                username = u or username
                token = p
            else:
                token = cred_part
    return clean_url, username, token


def _clone_clean_repo_for_finalize(
    *,
    clean_repo_dir: Path,
    full_name: str,
    remote_url: str,
    default_branch: str,
    base_sha: str,
    username: str = "",
    token: str = "",
) -> tuple[bool, str]:
    """Create a fresh clean git clone in finalize's private directory at base_sha."""
    clean_repo_dir.parent.mkdir(parents=True, exist_ok=True)
    if clean_repo_dir.exists():
        shutil.rmtree(clean_repo_dir, ignore_errors=True)

    mirror_root_str = os.environ.get("GITSEED_GIT_CACHE_DIR", "").strip()
    ref_args: list[str] = []
    if mirror_root_str:
        safe_slug = "".join(
            c if c.isalnum() or c in "._-" else "__" for c in full_name.strip("/")
        ) or "repo"
        mirror_candidate = Path(mirror_root_str) / f"{safe_slug}.git"
        if (mirror_candidate / "HEAD").is_file():
            ref_args = ["--reference-if-able", str(mirror_candidate), "--dissociate"]

    clean_url = strip_url_credentials(remote_url)
    with tempfile.TemporaryDirectory(prefix="gitseed-fin-clone-") as tmp_dir:
        extra_env: dict[str, str] = {}
        if token:
            askpass = Path(tmp_dir) / "askpass.sh"
            askpass.write_text(
                "#!/bin/sh\n"
                'case "$1" in\n'
                '  *Username*|*username*) printf "%s\\n" "$GITSEED_GIT_USERNAME" ;;\n'
                '  *) printf "%s\\n" "$GITSEED_GIT_PASSWORD" ;;\n'
                "esac\n",
                encoding="utf-8",
            )
            os.chmod(askpass, 0o700)
            extra_env = {
                "GIT_ASKPASS": str(askpass),
                "GITSEED_GIT_USERNAME": username or "x-access-token",
                "GITSEED_GIT_PASSWORD": token,
            }

        local_proto = (
            ["-c", "protocol.file.allow=always"] if is_local_repo_path(clean_url) else []
        )
        rc, _, err = run_safe_git(
            [
                *local_proto,
                "clone",
                "--no-checkout",
                *ref_args,
                clean_url,
                str(clean_repo_dir),
            ],
            clean_repo_dir.parent,
            extra_env=extra_env,
        )
        if rc != 0:
            return False, f"Clean clone of {full_name} failed: {err}"

        # Ensure base_sha is present in clean_repo_dir
        rc_cat, _, _ = run_safe_git(
            ["cat-file", "-e", f"{base_sha}^{{commit}}"],
            clean_repo_dir,
        )
        if rc_cat != 0:
            run_safe_git(
                [*local_proto, "fetch", "origin", base_sha],
                clean_repo_dir,
                extra_env=extra_env,
            )

    neutralize_git_repo(
        clean_repo_dir,
        clean_remote_url=clean_url,
        default_branch=default_branch,
    )
    sanitize_git_dir(clean_repo_dir)
    return True, ""


async def run_finalize_mode(
    *,
    report_complete_fn: Any,
) -> int:
    """Execute the trusted `finalize` container lifecycle."""
    api_url = os.environ.get("GITSEED_API_URL", "http://api:8420")
    finalize_token = os.environ.get("GITSEED_RUN_TOKEN", "")
    run_id_env = os.environ.get("GITSEED_RUN_ID", "").strip()
    if not finalize_token:
        logger.error("GITSEED_RUN_TOKEN is missing in finalize mode")
        return 1

    workspace_root = get_workspace_dir()
    ctl_dir = get_ctl_dir()

    if workspace_root.is_dir() and os.access(workspace_root, os.W_OK):
        with suppress(OSError):
            sanitize_git_dir(workspace_root)

    # Wait for agent-done marker if ctl_dir exists
    if run_id_env:
        done_marker = ctl_dir / f"{run_id_env}.agent-done"
    else:
        done_marker = ctl_dir / "agent-done"

    wait_timeout = _resolve_finalize_wait_seconds()
    found_markers = list(ctl_dir.glob("*.agent-done")) if ctl_dir.exists() else []
    marker_found = not ctl_dir.exists() or done_marker.exists() or bool(found_markers)
    if ctl_dir.exists() and not marker_found:
        for _ in range(int(max(1.0, wait_timeout) * 4)):
            found_markers = list(ctl_dir.glob("*.agent-done"))
            if done_marker.exists() or found_markers:
                marker_found = True
                break
            await asyncio.sleep(0.25)

    # Read untrusted agent result from /gitseed-ctl
    agent_result: dict[str, Any] = {}
    agent_result_path = ctl_dir / "agent-result.json"
    if agent_result_path.is_file() and not agent_result_path.is_symlink():
        with suppress(Exception):
            raw_text = agent_result_path.read_text(encoding="utf-8", errors="ignore")
            if len(raw_text) <= 5_000_000:
                parsed = json.loads(raw_text)
                if isinstance(parsed, dict):
                    agent_result = parsed
    elif not marker_found:
        agent_result = {
            "status": "failed",
            "exit_code": 1,
            "error": "Agent phase exited or timed out without writing completion marker",
        }
    else:
        marker_to_read = done_marker if done_marker.is_file() else (
            found_markers[0] if found_markers else None
        )
        if marker_to_read is not None and marker_to_read.is_file():
            with suppress(Exception):
                marker_obj = json.loads(marker_to_read.read_text(encoding="utf-8"))
                if isinstance(marker_obj, dict):
                    m_rc = int(marker_obj.get("exit_code", 1))
                    agent_result = {
                        "status": "succeeded" if m_rc == 0 else "failed",
                        "exit_code": m_rc,
                        "error": marker_obj.get("error") or (
                            None if m_rc == 0 else "Agent phase failed without result payload"
                        ),
                    }

    try:
        fin_ctx = await fetch_finalize_context(api_url, finalize_token)
    except Exception as exc:
        logger.error("finalize: failed to fetch finalize context: %s", exc)
        return 1

    run_id = str(fin_ctx.get("run_id") or run_id_env or "run")
    local_meta: dict[str, Any] = {}
    meta_from_secrets = False
    meta_candidates = [run_id]
    if run_id_env and run_id_env not in meta_candidates:
        meta_candidates.append(run_id_env)
    for cand_id in meta_candidates:
        meta_path = _meta_file_path(cand_id, prefer_secrets=True)
        if meta_path.is_file() and not meta_path.is_symlink():
            with suppress(Exception):
                loaded_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                if isinstance(loaded_meta, dict):
                    local_meta = loaded_meta
                    meta_from_secrets = (meta_path.parent == get_secrets_dir())
                    break

    stage = str(fin_ctx.get("stage") or local_meta.get("stage") or "ideate").lower()
    seed_id = str(fin_ctx.get("seed_id") or "")
    seed_number = int(fin_ctx.get("seed_number") or local_meta.get("seed_number") or 1)
    project_slug = str(fin_ctx.get("project_slug") or local_meta.get("project_slug") or "")
    runtime = str(fin_ctx.get("runtime") or "opencode")
    model = str(fin_ctx.get("model") or "default")
    seed_branch = str(
        fin_ctx.get("seed_branch")
        or local_meta.get("seed_branch")
        or format_seed_branch(project_slug, seed_number)
    )
    raw_base_heads: dict[str, Any] = (
        dict(fin_ctx["base_heads"])
        if isinstance(fin_ctx.get("base_heads"), dict) and fin_ctx.get("base_heads")
        else (
            dict(local_meta["base_heads"])
            if isinstance(local_meta.get("base_heads"), dict)
            else {}
        )
    )
    if meta_from_secrets and isinstance(local_meta.get("base_heads"), dict) and local_meta["base_heads"]:
        raw_base_heads = {**raw_base_heads, **local_meta["base_heads"]}
    base_heads: dict[str, str] = (
        {str(k): str(v) for k, v in raw_base_heads.items() if k and v}
        if isinstance(raw_base_heads, dict)
        else {}
    )
    raw_remote_seed_heads: dict[str, Any] = (
        dict(fin_ctx["remote_seed_heads"])
        if isinstance(fin_ctx.get("remote_seed_heads"), dict) and fin_ctx.get("remote_seed_heads")
        else (
            dict(local_meta["remote_seed_heads"])
            if isinstance(local_meta.get("remote_seed_heads"), dict)
            else {}
        )
    )
    if meta_from_secrets and isinstance(local_meta.get("remote_seed_heads"), dict) and local_meta["remote_seed_heads"]:
        raw_remote_seed_heads = {**raw_remote_seed_heads, **local_meta["remote_seed_heads"]}
    remote_seed_heads: dict[str, str] = (
        {str(k): str(v) for k, v in raw_remote_seed_heads.items() if k}
        if isinstance(raw_remote_seed_heads, dict)
        else {}
    )
    blocked_paths: list[str] = (
        [str(p).strip() for p in fin_ctx["blocked_paths"] if str(p).strip()]
        if isinstance(fin_ctx.get("blocked_paths"), list)
        else []
    )
    blocked_paths_action = str(fin_ctx.get("blocked_paths_action") or "fail").lower()
    max_file_bytes = int(fin_ctx.get("max_file_bytes") or DEFAULT_MAX_FILE_BYTES)
    max_diff_bytes = int(fin_ctx.get("max_diff_bytes") or DEFAULT_MAX_DIFF_BYTES)
    issued_secrets: list[str] = (
        [str(s) for s in fin_ctx["issued_secrets"] if isinstance(s, str) and s]
        if isinstance(fin_ctx.get("issued_secrets"), list)
        else []
    )
    issued_secrets.append(finalize_token)

    repositories = [
        r for r in (fin_ctx.get("repositories") or []) if isinstance(r, dict)
    ]
    _, repo_dirs = resolve_existing_repo_dirs(workspace_root, repositories)

    status = str(agent_result.get("status") or "failed")
    raw_exit_code = agent_result.get("exit_code")
    exit_code = int(raw_exit_code) if isinstance(raw_exit_code, (int, str)) else 1
    error_msg: str | None = (
        str(agent_result["error"]) if agent_result.get("error") else None
    )
    log_tail = str(agent_result.get("log_tail") or "")

    # Validate agent-authored stage_output through stage schema
    raw_stage_output = agent_result.get("stage_output")
    if not isinstance(raw_stage_output, dict) or not raw_stage_output:
        ctl_output = ctl_dir / "output.json"
        if ctl_output.is_file() and not ctl_output.is_symlink():
            with suppress(Exception):
                loaded = json.loads(ctl_output.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    raw_stage_output = loaded

    validated_stage_output: dict[str, Any] = {}
    if isinstance(raw_stage_output, dict) and raw_stage_output:
        try:
            eff_min_crit = (
                fin_ctx.get("min_acceptance_criteria")
                if fin_ctx.get("min_acceptance_criteria") is not None
                else local_meta.get("min_acceptance_criteria")
            )
            eff_max_crit = (
                fin_ctx.get("max_acceptance_criteria")
                if fin_ctx.get("max_acceptance_criteria") is not None
                else local_meta.get("max_acceptance_criteria")
            )
            eff_max_tasks = (
                fin_ctx.get("max_tasks")
                if fin_ctx.get("max_tasks") is not None
                else local_meta.get("max_tasks")
            )
            prior_policy_warning = (
                raw_stage_output.get("policy_limit_warning")
                if isinstance(raw_stage_output.get("policy_limit_warning"), dict)
                else None
            )
            prior_synthesized_via = (
                raw_stage_output.get("synthesized_via")
                if isinstance(raw_stage_output.get("synthesized_via"), str)
                and raw_stage_output.get("synthesized_via")
                else None
            )
            validated_stage_output = validate_stage_output_data(
                stage,
                raw_stage_output,
                min_acceptance_criteria=int(eff_min_crit) if eff_min_crit is not None else None,
                max_acceptance_criteria=int(eff_max_crit) if eff_max_crit is not None else None,
                max_tasks=int(eff_max_tasks) if eff_max_tasks is not None else None,
                enforce_bounds=False,
            )
            if (
                validated_stage_output.get("policy_limit_warning") is None
                and prior_policy_warning is not None
            ):
                validated_stage_output["policy_limit_warning"] = prior_policy_warning
            if (
                validated_stage_output.get("synthesized_via") is None
                and prior_synthesized_via is not None
            ):
                validated_stage_output["synthesized_via"] = prior_synthesized_via
        except Exception as exc:
            if status == "succeeded":
                status = "failed"
                exit_code = 1
                error_msg = f"Stage output schema validation failed in finalize: {exc}"
            validated_stage_output = {}

    # Strip any trusted-looking fields from untrusted agent output
    for untrusted_field in ("commit_sha", "branch_name", "pushed", "trusted"):
        validated_stage_output.pop(untrusted_field, None)
    validated_stage_output["_source"] = "agent_authored"

    raw_verification = agent_result.get("verification")
    trusted_verification: dict[str, Any] = {"source": "agent_reported"}
    if isinstance(raw_verification, dict):
        trusted_verification.update(raw_verification)
        trusted_verification["source"] = "agent_reported"

    trusted_result: dict[str, Any] = {
        "commit_sha": None,
        "branch": seed_branch,
        "pushed": False,
        "base_heads": base_heads,
        "blocked_paths_violations": [],
        "secret_scan_findings": [],
        "diff_stats": {"files": 0, "insertions": 0, "deletions": 0},
        "verification": trusted_verification,
        "finalized_at": datetime.now(UTC).isoformat(),
    }

    if stage == "execute" and status == "succeeded" and repositories:
        summary_line = (
            str(validated_stage_output.get("summary_comment") or "")
            .strip()
            .splitlines()[0]
            if validated_stage_output.get("summary_comment")
            else f"Implement Seed #{seed_number}"
        )
        commit_title = f"feat(seed-{seed_number}): {summary_line[:68]}"
        trailers = {
            "GitSeed-Seed": seed_id or str(seed_number),
            "GitSeed-Run": run_id,
            "GitSeed-Stage": stage,
            "GitSeed-Runtime": runtime,
            "GitSeed-Model": model,
        }

        with tempfile.TemporaryDirectory(prefix=f"gitseed-finalize-{run_id[:8]}-") as priv_root:
            priv_root_path = Path(priv_root)
            all_violations: list[str] = []
            all_secret_findings: list[dict[str, Any]] = []
            agg_stats = {"files": 0, "insertions": 0, "deletions": 0}
            primary_commit_sha: str | None = None
            any_pushed = False

            for repo in repositories:
                full_name = str(repo.get("full_name") or "").strip()
                if not full_name:
                    continue
                ws_repo_dir = repo_dirs.get(full_name)
                if ws_repo_dir is None or not ws_repo_dir.is_dir():
                    continue
                base_sha = base_heads.get(full_name)
                if not base_sha:
                    status = "failed"
                    exit_code = 1
                    error_msg = f"Missing trusted base SHA from Core for repository '{full_name}'"
                    break

                default_branch = str(repo.get("default_branch") or "main").strip()
                clean_remote = strip_url_credentials(
                    str(repo.get("clean_remote_url") or repo.get("clone_url") or "")
                )

                # Request single-use push credential from Core for this repository
                try:
                    push_resp = await request_git_push_credential(
                        api_url, finalize_token, full_name
                    )
                except Exception as exc:
                    status = "failed"
                    exit_code = 1
                    error_msg = f"Failed to obtain git push credential for '{full_name}': {exc}"
                    break

                remote_url, git_user, git_token = _parse_credentials_from_push_resp(
                    push_resp
                )
                if git_token:
                    issued_secrets.append(git_token)

                safe_repo_slug = full_name.replace("/", "__")
                clean_repo_dir = priv_root_path / safe_repo_slug
                cloned_ok, clone_err = _clone_clean_repo_for_finalize(
                    clean_repo_dir=clean_repo_dir,
                    full_name=full_name,
                    remote_url=remote_url or clean_remote,
                    default_branch=default_branch,
                    base_sha=base_sha,
                    username=git_user,
                    token=git_token,
                )
                if not cloned_ok:
                    status = "failed"
                    exit_code = 1
                    error_msg = clone_err
                    break

                commit_res = construct_clean_commit_and_verify_policy(
                    clean_repo_dir=clean_repo_dir,
                    workspace_repo_dir=ws_repo_dir,
                    base_sha=base_sha,
                    seed_branch=seed_branch,
                    commit_message=commit_title,
                    trailers=trailers,
                    blocked_paths=blocked_paths,
                    blocked_paths_action=blocked_paths_action,
                    extra_secrets=issued_secrets,
                    max_file_bytes=max_file_bytes,
                    max_diff_bytes=max_diff_bytes,
                )
                if commit_res.blocked_paths_violations:
                    all_violations.extend(commit_res.blocked_paths_violations)
                if commit_res.secret_scan_findings:
                    all_secret_findings.extend(commit_res.secret_scan_findings)

                if not commit_res.ok:
                    status = "failed"
                    exit_code = 1
                    error_msg = commit_res.error or "Finalize commit policy check failed"
                    break

                agg_stats["files"] += commit_res.diff_stats.get("files", 0)
                agg_stats["insertions"] += commit_res.diff_stats.get("insertions", 0)
                agg_stats["deletions"] += commit_res.diff_stats.get("deletions", 0)

                if not commit_res.has_changes or not commit_res.commit_sha:
                    continue

                expected_remote = remote_seed_heads.get(full_name)
                pushed_ok, push_err = push_with_askpass(
                    clean_repo_dir=clean_repo_dir,
                    remote_url=remote_url or clean_remote,
                    branch=seed_branch,
                    expected_remote_sha=expected_remote,
                    username=git_user,
                    token=git_token,
                )
                if not pushed_ok:
                    status = "failed"
                    exit_code = 1
                    error_msg = f"Failed to push branch '{seed_branch}' to '{full_name}': {push_err}"
                    break

                any_pushed = True
                if primary_commit_sha is None or bool(repo.get("is_primary")):
                    primary_commit_sha = commit_res.commit_sha

            if (
                status == "succeeded"
                and repo_dirs
                and not any_pushed
                and not any(str(v).strip() for v in remote_seed_heads.values())
            ):
                status = "failed"
                exit_code = 1
                error_msg = (
                    f"Execute stage completed without creating or modifying any tracked "
                    f"repository files to push to branch '{seed_branch}'."
                )

            trusted_result["blocked_paths_violations"] = sorted(set(all_violations))
            trusted_result["secret_scan_findings"] = all_secret_findings
            trusted_result["diff_stats"] = agg_stats
            trusted_result["commit_sha"] = primary_commit_sha
            trusted_result["pushed"] = any_pushed
            trusted_result["finalized_at"] = datetime.now(UTC).isoformat()

            if status == "succeeded" and primary_commit_sha:
                validated_stage_output["branch_name"] = seed_branch
                validated_stage_output["commit_sha"] = primary_commit_sha

    await report_complete_fn(
        api_url,
        finalize_token,
        status=status,
        exit_code=exit_code,
        error=error_msg,
        input_tokens=int(agent_result.get("input_tokens") or 0),
        output_tokens=int(agent_result.get("output_tokens") or 0),
        context_tokens=int(agent_result.get("context_tokens") or 0),
        peak_context_tokens=int(agent_result.get("peak_context_tokens") or 0),
        turn_count=int(agent_result.get("turn_count") or 0),
        stage_output=validated_stage_output,
        log_tail=log_tail,
        trusted=trusted_result,
    )
    return 0 if status == "succeeded" else 1
