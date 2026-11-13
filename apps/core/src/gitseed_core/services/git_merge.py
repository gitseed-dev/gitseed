"""Git remote URL resolution, push token issuance, and branch merge helpers."""

from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlsplit, urlunsplit

import httpx
from gitseed_common.enums import (
    ActivityType,
    ActorType,
    ApprovalDecision,
    BackendType,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment
from gitseed_core.models.user import User
from gitseed_core.schemas.runner import (
    RunnerGitPushTokenResponse,
    RunnerMCPServerItem,
    RunnerRepositoryItem,
)
from gitseed_core.security import (
    decrypt_secrets,
    verify_scoped_run_token,
)
from gitseed_core.services import projects as projects_service
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Conflict, Forbidden, Invalid, NotFound, Unauthorized
from gitseed_core.services.gateway import docker_host_fallback_url
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.git_browse import (
    _bitbucket_api_base_and_headers,
    _build_authenticated_git_url,
    _github_api_base_and_headers,
    _gitlab_api_base_and_headers,
)
from gitseed_core.services.git_pr_providers import (
    ensure_provider_pull_request,
    extract_pr_number_from_url,
    inspect_bitbucket_pr_status,
    inspect_github_pr_status,
    inspect_gitlab_mr_status,
    merge_provider_pull_request,
)
from gitseed_core.services.policies import get_effective_stage_policy

logger = logging.getLogger("uvicorn.error")

_GIT_SUBPROCESS_TIMEOUT = 120
_PR_STATUS_CACHE: dict[tuple[str, int, str], tuple[float, dict[str, Any]]] = {}
_PR_STATUS_CACHE_TTL_SECONDS = 25.0


def invalidate_pr_status_cache(seed_id: str | None = None) -> None:
    """Clear cached PR/MR status entries (all or for a specific `seed_id`)."""
    if seed_id is None:
        _PR_STATUS_CACHE.clear()
        return
    for key in [k for k in _PR_STATUS_CACHE if k[0] == seed_id]:
        _PR_STATUS_CACHE.pop(key, None)


def _build_git_urls(
    backend: Backend,
    full_name: str,
    token: str | None,
) -> tuple[str, str]:
    """Return (authenticated_clone_url, clean_remote_url_without_credentials)."""
    provider = backend.provider.lower()
    endpoint = (backend.endpoint or "").strip().rstrip("/")
    cleaned_name = full_name.strip().lstrip("/")
    if not cleaned_name.endswith(".git"):
        repo_Suffix = f"{cleaned_name}.git"
    else:
        repo_Suffix = cleaned_name

    if provider == "local":
        from gitseed_core.services.local_git import build_local_git_http_url

        clean_url = build_local_git_http_url(full_name, for_container=True)
        if not token or token == "local-token":
            return clean_url, clean_url
        parts = urlsplit(clean_url)
        auth_netloc = f"x-access-token:{quote(token, safe='')}@{parts.netloc}"
        return urlunsplit((parts.scheme, auth_netloc, parts.path, "", "")), clean_url

    if provider in ("github", "github-app", "github-enterprise"):
        if not endpoint or "api.github.com" in endpoint:
            clean_url = f"https://github.com/{repo_Suffix}"
        else:
            parsed = urlsplit(endpoint)
            host = parsed.netloc or "github.com"
            scheme = parsed.scheme or "https"
            clean_url = f"{scheme}://{host}/{repo_Suffix}"
        if not token:
            return clean_url, clean_url
        parts = urlsplit(clean_url)
        auth_netloc = f"x-access-token:{quote(token, safe='')}@{parts.netloc}"
        return urlunsplit((parts.scheme, auth_netloc, parts.path, "", "")), clean_url

    if provider == "gitlab":
        base = endpoint or "https://gitlab.com"
        base_host = base.removesuffix("/api/v4")
        clean_url = f"{base_host}/{repo_Suffix}"
        if not token:
            return clean_url, clean_url
        parts = urlsplit(clean_url)
        auth_netloc = f"oauth2:{quote(token, safe='')}@{parts.netloc}"
        return urlunsplit((parts.scheme, auth_netloc, parts.path, "", "")), clean_url

    if provider == "bitbucket":
        clean_url = f"https://bitbucket.org/{repo_Suffix}"
        if not token:
            return clean_url, clean_url
        parts = urlsplit(clean_url)
        if ":" in token:
            bb_user, bb_pw = token.split(":", 1)
            auth_netloc = f"{quote(bb_user, safe='')}:{quote(bb_pw, safe='')}@{parts.netloc}"
        else:
            auth_netloc = f"x-token-auth:{quote(token, safe='')}@{parts.netloc}"
        return urlunsplit((parts.scheme, auth_netloc, parts.path, "", "")), clean_url

    # Generic git provider
    if endpoint.startswith(("http://", "https://")):
        clean_url = f"{endpoint}/{repo_Suffix}" if not endpoint.endswith(".git") else endpoint
        if not token:
            return clean_url, clean_url
        parts = urlsplit(clean_url)
        auth_netloc = f"git:{quote(token, safe='')}@{parts.netloc}"
        return urlunsplit((parts.scheme, auth_netloc, parts.path, "", "")), clean_url

    return endpoint or repo_Suffix, endpoint or repo_Suffix


async def _find_plan_target_repositories(
    session: AsyncSession, seed_id: str
) -> set[str]:
    """Check if the most recent succeeded Plan run specified a subset of target_repositories."""
    stmt = (
        select(Run)
        .where(
            Run.seed_id == seed_id,
            Run.stage == SeedStage.PLAN,
            Run.status == RunStatus.SUCCEEDED,
        )
        .order_by(col(Run.created_at).desc())
        .limit(1)
    )
    plan_run = (await session.execute(stmt)).scalar_one_or_none()
    if plan_run is not None and isinstance(plan_run.output, dict):
        targets = plan_run.output.get("target_repositories")
        if isinstance(targets, list):
            return {str(t).strip().lower() for t in targets if str(t).strip()}
    return set()


async def _resolve_runner_repositories(
    session: AsyncSession,
    project: Project,
    seed: Seed,
    run: Run,
) -> list[RunnerRepositoryItem]:
    """Resolve repositories to clone for a runner execution."""
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

    if run.stage == SeedStage.EXECUTE and len(repo_rows) > 1:
        target_repos = await _find_plan_target_repositories(session, seed.id)
        if target_repos:
            filtered_repos = [
                r for r in repo_rows if r.full_name.strip().lower() in target_repos or r.is_primary
            ]
            if filtered_repos:
                repo_rows = filtered_repos

    repos_by_backend: dict[str, list[ProjectRepository]] = {}
    for r in repo_rows:
        repos_by_backend.setdefault(r.backend_id, []).append(r)

    repo_items: list[RunnerRepositoryItem] = []
    for bid, b_repos in repos_by_backend.items():
        git_backend = await session.get(Backend, bid)
        if git_backend is None or not git_backend.enabled:
            continue
        b_secrets = decrypt_secrets(git_backend.secrets_encrypted)
        clone_token = await resolve_git_backend_token(
            git_backend,
            b_secrets,
            repo_full_names=[r.full_name for r in b_repos],
            write_access=False,
        )

        for r in b_repos:
            clone_url, clean_url = _build_git_urls(git_backend, r.full_name, clone_token)
            repo_items.append(
                RunnerRepositoryItem(
                    id=r.id or 0,
                    backend_id=git_backend.id,
                    provider=git_backend.provider,
                    full_name=r.full_name,
                    default_branch=r.default_branch,
                    is_primary=r.is_primary,
                    clone_url=clone_url,
                    clean_remote_url=clean_url,
                )
            )
    return repo_items


async def _resolve_runner_mcp_servers(
    session: AsyncSession,
    project: Project,
    run: Run,
) -> list[RunnerMCPServerItem]:
    """Resolve accessible Tools (MCP) backends for the project & agent."""
    tools_rows = list(
        (
            await session.execute(
                select(Backend)
                .where(
                    Backend.type == BackendType.TOOLS,
                    col(Backend.enabled).is_(True),
                )
                .order_by(col(Backend.created_at).asc())
            )
        )
        .scalars()
        .all()
    )
    if project.backends_restricted and tools_rows:
        allowed_project_bids = set(
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
        tools_rows = [b for b in tools_rows if b.id in allowed_project_bids]

    mcp_items: list[RunnerMCPServerItem] = []
    for tb in tools_rows:
        if tb.access_restricted:
            grant = await session.get(AgentBackend, (tb.id, run.agent_id))
            if grant is None:
                continue
        raw_ep = (tb.endpoint or "").strip()
        if not raw_ep:
            continue
        t_secrets = decrypt_secrets(tb.secrets_encrypted)
        headers: dict[str, str] = {}
        env_vars: dict[str, str] = {}
        if tb.provider == "mcp-stdio":
            resolved_ep = raw_ep
            if t_secrets.get("env_secret"):
                env_vars["MCP_ENV_SECRET"] = t_secrets["env_secret"]
        else:
            resolved_ep = docker_host_fallback_url(raw_ep)
            if t_secrets.get("bearer_token"):
                headers["Authorization"] = f"Bearer {t_secrets['bearer_token']}"
            if t_secrets.get("api_key"):
                headers["X-API-Key"] = t_secrets["api_key"]

        tb_slug = re.sub(r"[^a-z0-9_-]+", "-", tb.name.lower()).strip("-") or tb.id
        mcp_items.append(
            RunnerMCPServerItem(
                id=tb.id,
                name=tb.name,
                slug=tb_slug,
                provider=tb.provider,
                endpoint=resolved_ep,
                headers=headers,
                env=env_vars,
            )
        )
    return mcp_items


async def issue_git_push_token(
    session: AsyncSession,
    raw_token: str | None,
    *,
    repository_full_name: str,
) -> RunnerGitPushTokenResponse:
    """Issue a short-lived Git write credential to the finalize container (EXECUTE stage only, once per repo per run)."""
    if not raw_token:
        raise Unauthorized("Missing run token")
    scoped = verify_scoped_run_token(raw_token)
    if scoped is None:
        raise Unauthorized("Invalid or expired run token")
    run_id, token_scope = scoped
    if token_scope != "finalize":
        raise Forbidden(
            f"Git push credentials require a 'finalize' scoped run token (got '{token_scope}')"
        )

    run = await session.get(Run, run_id)
    if run is None or run.status not in (RunStatus.STARTING, RunStatus.RUNNING):
        raise Unauthorized(
            f"Run '{run_id}' is not active (status={run.status if run else 'missing'})"
        )
    seed = await session.get(Seed, run.seed_id)
    if seed is None:
        raise NotFound("Seed not found")
    project = await session.get(Project, seed.project_id)
    if project is None:
        raise NotFound("Project not found")

    if run.stage != SeedStage.EXECUTE:
        raise Forbidden(
            f"Git push access is only granted in Execute stage (stage='{str(run.stage)}')"
        )

    cleaned_repo = repository_full_name.strip()
    stmt = select(ProjectRepository).where(
        ProjectRepository.project_id == project.id,
        ProjectRepository.full_name == cleaned_repo,
    )
    project_repo = (await session.execute(stmt)).scalar_one_or_none()
    if project_repo is None:
        raise NotFound(f"Repository '{cleaned_repo}' is not attached to project '{project.slug}'")

    out_dict = dict(run.output or {}) if isinstance(run.output, dict) else {}
    broker_state = (
        dict(out_dict.get("_broker") or {})
        if isinstance(out_dict.get("_broker"), dict)
        else {}
    )
    issued_repos = (
        list(broker_state.get("push_tokens_issued") or [])
        if isinstance(broker_state.get("push_tokens_issued"), list)
        else []
    )
    if project_repo.full_name in issued_repos:
        raise Forbidden(
            f"Git push credential already issued for repository '{project_repo.full_name}' in run '{run.id}'"
        )

    git_backend = await session.get(Backend, project_repo.backend_id)
    if git_backend is None or not git_backend.enabled:
        raise Invalid("Git backend for repository is disabled or missing")

    b_secrets = decrypt_secrets(git_backend.secrets_encrypted)
    write_token = await resolve_git_backend_token(
        git_backend,
        b_secrets,
        repo_full_names=[project_repo.full_name],
        write_access=True,
    )

    if not write_token:
        raise Invalid("No write token or GitHub App key available on Git backend")

    issued_repos.append(project_repo.full_name)
    broker_state["push_tokens_issued"] = issued_repos
    out_dict["_broker"] = broker_state
    run.output = out_dict
    await session.commit()

    push_url, clean_url = _build_git_urls(
        git_backend, project_repo.full_name, write_token
    )
    provider_low = (git_backend.provider or "").strip().lower()
    effective_push_token = write_token
    if provider_low == "gitlab":
        git_username = "oauth2"
    elif provider_low == "bitbucket":
        if ":" in write_token:
            git_username, effective_push_token = write_token.split(":", 1)
        else:
            git_username = "x-token-auth"
    else:
        git_username = "x-access-token"
    return RunnerGitPushTokenResponse(
        repository_full_name=project_repo.full_name,
        push_url=push_url,
        clean_remote_url=clean_url,
        username=git_username,
        push_token=effective_push_token,
    )


_SAFE_CLI_GIT_ARGS: tuple[str, ...] = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "protocol.ext.allow=never",
)
_URL_CREDENTIAL_RE = re.compile(r"(https?://)([^/\s@]+)@")


def _scrub_url_credentials(text: str) -> str:
    """Redact any `https://user:token@host` credentials from error strings before logging."""
    return _URL_CREDENTIAL_RE.sub(r"\1***@", text or "")


def _prepare_clean_git_url_and_env(raw_url: str) -> tuple[str, dict[str, str]]:
    """Strip inline HTTP credentials from `raw_url` and pass auth via scoped `GIT_CONFIG_*` env vars."""
    env = os.environ.copy()
    env.pop("GIT_DIR", None)
    env.pop("GIT_WORK_TREE", None)
    env.pop("GIT_INDEX_FILE", None)
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_TERMINAL_PROMPT"] = "0"

    parsed = urlsplit(raw_url)
    if parsed.scheme in ("http", "https") and (
        parsed.username is not None or parsed.password is not None
    ):
        user = unquote(parsed.username or "")
        pw = unquote(parsed.password or "")
        host = parsed.hostname or ""
        formatted_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
        clean_netloc = f"{formatted_host}:{parsed.port}" if parsed.port else formatted_host
        clean_url = urlunsplit((parsed.scheme, clean_netloc, parsed.path, parsed.query, ""))
        b64_creds = base64.b64encode(f"{user}:{pw}".encode()).decode("ascii")
        origin_prefix = f"{parsed.scheme}://{clean_netloc}/"
        env["GIT_CONFIG_COUNT"] = "1"
        env["GIT_CONFIG_KEY_0"] = f"http.{origin_prefix}.extraHeader"
        env["GIT_CONFIG_VALUE_0"] = f"Authorization: Basic {b64_creds}"
        return clean_url, env

    return raw_url, env


def _wrap_untrusted_ci_text(text: str) -> str:
    sanitized = (text or "").strip().replace(
        "</untrusted_external_input>", "&lt;/untrusted_external_input&gt;"
    )
    return f"<untrusted_external_input>\n{sanitized}\n</untrusted_external_input>"


def _merge_seed_branch_via_cli(
    auth_url: str, default_branch: str, seed_branch: str
) -> str | None:
    """Synchronous git CLI fallback for merging a seed branch into `default_branch`."""
    clean_url, git_env = _prepare_clean_git_url_and_env(auth_url)
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / "repo"
        tmp_path.mkdir(parents=True, exist_ok=True)
        clone_res = subprocess.run(
            [
                "git",
                *_SAFE_CLI_GIT_ARGS,
                "clone",
                "--depth",
                "100",
                "--branch",
                default_branch,
                "--",
                clean_url,
                str(tmp_path),
            ],
            env=git_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
        if clone_res.returncode != 0:
            return None
        fetch_res = subprocess.run(
            [
                "git",
                *_SAFE_CLI_GIT_ARGS,
                "fetch",
                "--depth",
                "100",
                "origin",
                f"+refs/heads/{seed_branch}:refs/remotes/origin/{seed_branch}",
            ],
            cwd=str(tmp_path),
            env=git_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
        if fetch_res.returncode != 0:
            return None
        subprocess.run(
            ["git", *_SAFE_CLI_GIT_ARGS, "config", "user.name", "GitSeed"],
            cwd=str(tmp_path),
            env=git_env,
            check=False,
            timeout=15,
        )
        subprocess.run(
            [
                "git",
                *_SAFE_CLI_GIT_ARGS,
                "config",
                "user.email",
                "gitseed@gitseed.local",
            ],
            cwd=str(tmp_path),
            env=git_env,
            check=False,
            timeout=15,
        )
        merge_cmd = [
            "git",
            *_SAFE_CLI_GIT_ARGS,
            "merge",
            "--no-ff",
            f"refs/remotes/origin/{seed_branch}",
            "-m",
            f"Merge {seed_branch} into {default_branch}",
        ]
        merge_res = subprocess.run(
            merge_cmd,
            cwd=str(tmp_path),
            env=git_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
        if merge_res.returncode != 0:
            # Shallow clone may lack the merge base when a Seed branch exceeds 100 commits;
            # abort any partial index state, unshallow history, and retry once.
            subprocess.run(
                ["git", *_SAFE_CLI_GIT_ARGS, "merge", "--abort"],
                cwd=str(tmp_path),
                env=git_env,
                capture_output=True,
                text=True,
                check=False,
                timeout=15,
            )
            if (tmp_path / ".git" / "shallow").exists():
                unshallow_res = subprocess.run(
                    [
                        "git",
                        *_SAFE_CLI_GIT_ARGS,
                        "fetch",
                        "--unshallow",
                        "origin",
                        f"+refs/heads/{default_branch}:refs/remotes/origin/{default_branch}",
                        f"+refs/heads/{seed_branch}:refs/remotes/origin/{seed_branch}",
                    ],
                    cwd=str(tmp_path),
                    env=git_env,
                    capture_output=True,
                    text=True,
                    check=False,
                    timeout=_GIT_SUBPROCESS_TIMEOUT,
                )
                if unshallow_res.returncode == 0:
                    merge_res = subprocess.run(
                        merge_cmd,
                        cwd=str(tmp_path),
                        env=git_env,
                        capture_output=True,
                        text=True,
                        check=False,
                        timeout=_GIT_SUBPROCESS_TIMEOUT,
                    )
            if merge_res.returncode != 0:
                return None

        push_res = subprocess.run(
            [
                "git",
                *_SAFE_CLI_GIT_ARGS,
                "push",
                "origin",
                f"HEAD:refs/heads/{default_branch}",
            ],
            cwd=str(tmp_path),
            env=git_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
        if push_res.returncode != 0:
            return None

        rev_res = subprocess.run(
            ["git", *_SAFE_CLI_GIT_ARGS, "rev-parse", "HEAD"],
            cwd=str(tmp_path),
            env=git_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=15,
        )
        merge_sha = (
            rev_res.stdout.strip()
            if rev_res.returncode == 0 and rev_res.stdout.strip()
            else None
        )
        subprocess.run(
            ["git", *_SAFE_CLI_GIT_ARGS, "push", "origin", "--delete", seed_branch],
            cwd=str(tmp_path),
            env=git_env,
            capture_output=True,
            text=True,
            check=False,
            timeout=_GIT_SUBPROCESS_TIMEOUT,
        )
        return merge_sha


def format_seed_branch(project_slug: str | None, seed_number: int) -> str:
    """Return the canonical project-scoped Git branch name for a seed."""
    slug = (project_slug or "").strip().strip("/")
    if slug:
        return f"gitseed/{slug}/seed-{seed_number}"
    return f"gitseed/seed-{seed_number}"


def is_auto_create_pr_enabled(project: Project | None) -> bool:
    """Return True if automatic Pull Request / Merge Request creation is enabled (default True)."""
    if project is None:
        return True
    sbx = project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    wh = sbx.get("webhook_config") if isinstance(sbx.get("webhook_config"), dict) else {}
    if "auto_create_pr" in sbx and sbx["auto_create_pr"] is not None:
        return bool(sbx["auto_create_pr"])
    if "auto_create_pr" in wh and wh["auto_create_pr"] is not None:
        return bool(wh["auto_create_pr"])
    return True


def is_auto_remediate_ci_enabled(project: Project | None) -> bool:
    """Return True if automatic CI pipeline failure remediation is enabled (default True)."""
    if project is None:
        return True
    sbx = project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    wh = sbx.get("webhook_config") if isinstance(sbx.get("webhook_config"), dict) else {}
    if "auto_remediate_ci" in sbx and sbx["auto_remediate_ci"] is not None:
        return bool(sbx["auto_remediate_ci"])
    if "auto_remediate_ci" in wh and wh["auto_remediate_ci"] is not None:
        return bool(wh["auto_remediate_ci"])
    return True


async def _resolve_seed_branch_name(
    session: AsyncSession, seed: Seed, project: Project | None, branch_name: str | None = None
) -> str:
    if branch_name and branch_name.strip():
        return branch_name.strip()
    seed_runs = (
        await session.execute(
            select(Run)
            .where(Run.seed_id == seed.id)
            .order_by(col(Run.created_at).desc())
        )
    ).scalars().all()
    for r in seed_runs:
        if isinstance(r.output, dict):
            b_val = str(r.output.get("branch_name") or "").strip()
            if b_val:
                return b_val
    return format_seed_branch(project.slug if project else None, seed.number)


def _build_seed_pr_body(seed: Seed, project: Project, seed_branch: str) -> str:
    """Build a structured Markdown Pull Request / Merge Request body for a Seed."""
    lines: list[str] = [
        f"## GitSeed `{project.slug}#{seed.number}` — {seed.title}",
        "",
        f"- **Branch**: `{seed_branch}`",
        f"- **Autonomy**: `{seed.autonomy}`",
    ]
    if seed.description and seed.description.strip():
        lines.extend(["", "### Summary", seed.description.strip()])
    if seed.specification and seed.specification.strip():
        spec_excerpt = seed.specification.strip()
        if len(spec_excerpt) > 3000:
            spec_excerpt = spec_excerpt[:3000] + "\n\n*(truncated)*"
        lines.extend(["", "### Specification", spec_excerpt])
    if seed.acceptance_criteria:
        lines.extend(["", "### Acceptance Criteria"])
        for crit in seed.acceptance_criteria:
            if crit and str(crit).strip():
                lines.append(f"- [x] {str(crit).strip()}")
    return "\n".join(lines)


async def ensure_seed_pull_request(
    session: AsyncSession,
    seed: Seed,
    project: Project,
    branch_name: str | None = None,
    *,
    review_comment: str | None = None,
) -> str | None:
    """Create or retrieve an open Pull Request / Merge Request for the seed branch on GitHub or GitLab."""
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
        return None

    seed_branch = await _resolve_seed_branch_name(session, seed, project, branch_name)
    pr_title = f"[{project.slug}#{seed.number}] {seed.title}"[:240]
    pr_body = _build_seed_pr_body(seed, project, seed_branch)
    primary_pr_url: str | None = None

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for repo in repo_rows:
            backend = await session.get(Backend, repo.backend_id)
            if backend is None or not backend.enabled:
                continue
            secrets_map = decrypt_secrets(backend.secrets_encrypted)
            token = await resolve_git_backend_token(
                backend,
                secrets_map,
                repo_full_names=[repo.full_name],
                write_access=True,
            )
            if not token:
                continue

            provider = (backend.provider or "github").strip().lower()
            default_branch = (repo.default_branch or "main").strip()

            if provider == "local":
                continue

            created_or_found = await ensure_provider_pull_request(
                client,
                provider=provider,
                backend=backend,
                repo=repo,
                token=token,
                seed_branch=seed_branch,
                default_branch=default_branch,
                pr_title=pr_title,
                pr_body=pr_body,
                review_comment=review_comment,
            )
            if created_or_found and not primary_pr_url:
                primary_pr_url = created_or_found

    if primary_pr_url:
        invalidate_pr_status_cache(seed.id)
    return primary_pr_url


async def check_seed_pull_request_status(
    session: AsyncSession,
    seed: Seed,
    project: Project | None = None,
    branch_name: str | None = None,
    *,
    repo_id: int | None = None,
) -> dict[str, Any]:
    """Inspect the remote GitHub PR or GitLab MR state, CI pipeline status, checks, comments, and review readiness for a seed branch."""
    result: dict[str, Any] = {
        "pull_request_url": None,
        "number": None,
        "repository_full_name": None,
        "provider": None,
        "state": None,
        "merged": False,
        "title": None,
        "body": None,
        "draft": False,
        "author": None,
        "base_ref": None,
        "head_ref": None,
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
        "mergeable_state": None,
        "ready_to_merge": True,
        "block_reason": None,
    }
    if project is None:
        project = await session.get(Project, seed.project_id)
    if project is None:
        return result

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
        return result

    if repo_id is not None:
        matched = [r for r in repo_rows if r.id == repo_id]
        if matched:
            repo_rows = matched

    seed_branch = await _resolve_seed_branch_name(session, seed, project, branch_name)
    cache_enabled = "PYTEST_CURRENT_TEST" not in os.environ
    target_repo_key = int(repo_id if repo_id is not None else (repo_rows[0].id or 0))
    cache_key = (seed.id, target_repo_key, seed_branch)
    if cache_enabled:
        cached_entry = _PR_STATUS_CACHE.get(cache_key)
        if cached_entry is not None:
            expires_at, cached_payload = cached_entry
            if time.time() < expires_at:
                return dict(cached_payload)

    pr_number_hint: int | None = None
    try:
        pr_runs = (
            await session.execute(
                select(Run.output)
                .where(Run.seed_id == seed.id)
                .order_by(col(Run.created_at).desc())
                .limit(10)
            )
        ).scalars().all()
        for out in pr_runs:
            if isinstance(out, dict) and out.get("pull_request_url"):
                pr_number_hint = extract_pr_number_from_url(str(out["pull_request_url"]))
                if pr_number_hint:
                    break
    except Exception:
        pr_number_hint = None

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        for repo in repo_rows:
            backend = await session.get(Backend, repo.backend_id)
            if backend is None or not backend.enabled:
                continue
            secrets_map = decrypt_secrets(backend.secrets_encrypted)
            token = await resolve_git_backend_token(
                backend,
                secrets_map,
                repo_full_names=[repo.full_name],
                write_access=False,
            )
            if not token:
                continue

            provider = (backend.provider or "github").strip().lower()
            if provider == "local":
                from gitseed_core.services.local_git import check_local_seed_merge_status

                local_info = await asyncio.to_thread(
                    check_local_seed_merge_status,
                    repo,
                    seed_branch=seed_branch,
                    seed_title=seed.title,
                    is_seed_merged=(seed.status == SeedStatus.MERGED),
                )
                if cache_enabled:
                    _PR_STATUS_CACHE[cache_key] = (
                        time.time() + _PR_STATUS_CACHE_TTL_SECONDS,
                        dict(local_info),
                    )
                return local_info
            if provider in ("github", "github-enterprise", "github-app"):
                gh_res = await inspect_github_pr_status(
                    client,
                    backend=backend,
                    repo=repo,
                    token=token,
                    seed_branch=seed_branch,
                    pr_number_hint=pr_number_hint if len(repo_rows) == 1 else None,
                )
                if gh_res is not None:
                    if cache_enabled:
                        ttl = 300.0 if gh_res.get("merged") else _PR_STATUS_CACHE_TTL_SECONDS
                        _PR_STATUS_CACHE[cache_key] = (time.time() + ttl, dict(gh_res))
                    return gh_res

            elif provider == "gitlab":
                gl_res = await inspect_gitlab_mr_status(
                    client,
                    backend=backend,
                    repo=repo,
                    token=token,
                    seed_branch=seed_branch,
                )
                if gl_res is not None:
                    if cache_enabled:
                        ttl = 300.0 if gl_res.get("merged") else _PR_STATUS_CACHE_TTL_SECONDS
                        _PR_STATUS_CACHE[cache_key] = (time.time() + ttl, dict(gl_res))
                    return gl_res

            elif provider == "bitbucket":
                bb_res = await inspect_bitbucket_pr_status(
                    client,
                    backend=backend,
                    repo=repo,
                    token=token,
                    seed_branch=seed_branch,
                )
                if bb_res is not None:
                    if cache_enabled:
                        ttl = 300.0 if bb_res.get("merged") else _PR_STATUS_CACHE_TTL_SECONDS
                        _PR_STATUS_CACHE[cache_key] = (time.time() + ttl, dict(bb_res))
                    return bb_res

    return result


async def _resolve_automated_comment_author(
    session: AsyncSession,
    seed: Seed,
    project: Project | None,
    stage: SeedStage,
) -> tuple[ActorType, str]:
    """Return a valid `(author_type, author_id)` pair (`agent` or `user`) for automated seed comments.

    PostgreSQL enforces `ck_seed_comment_author_type: author_type IN ('user', 'agent')` and `author_id NOT NULL`.
    Prefer attributing automated stage/PR comments to the effective stage agent or `@coordinator`,
    falling back to the seed/project creator or first active user.
    """
    try:
        pol = await get_effective_stage_policy(session, seed.project_id, stage)
        if pol and pol.agent_id:
            return ActorType.AGENT, pol.agent_id
    except Exception:
        pass

    try:
        coord = (
            await session.execute(
                select(Agent)
                .where(col(Agent.archived_at).is_(None))
                .order_by(
                    (col(Agent.handle) == "coordinator").desc(),
                    col(Agent.created_at).asc(),
                )
                .limit(1)
            )
        ).scalars().first()
        if coord is not None and coord.id:
            return ActorType.AGENT, coord.id
    except Exception:
        pass

    if seed.created_by:
        return ActorType.USER, seed.created_by
    if project is not None and project.created_by:
        return ActorType.USER, project.created_by

    try:
        first_user = (
            await session.execute(select(User.id).limit(1))
        ).scalars().first()
        if first_user:
            return ActorType.USER, str(first_user)
    except Exception:
        pass

    return ActorType.AGENT, "agt_system"


async def reconcile_seed_if_pr_merged_externally(
    session: AsyncSession,
    seed: Seed,
    project: Project | None = None,
    *,
    pr_info: dict[str, Any] | None = None,
) -> bool:
    """If a Seed in Execute, Review, or Merge had its PR merged externally on GitHub/GitLab, transition it to MERGED."""
    if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED):
        return False
    if seed.stage not in (SeedStage.EXECUTE, SeedStage.REVIEW, SeedStage.MERGE):
        return False

    info = dict(pr_info) if pr_info else await check_seed_pull_request_status(session, seed, project)
    if not info.get("merged"):
        return False

    # Enrich webhook-supplied pr_info with live PR details (approvers, repo name, PR number) if missing
    if pr_info is not None and (not info.get("approved_by") or not info.get("repository_full_name")):
        try:
            live_info = await check_seed_pull_request_status(session, seed, project)
            for k, v in live_info.items():
                if v and not info.get(k):
                    info[k] = v
        except Exception:
            pass

    now = datetime.now(UTC)
    prev_stage = seed.stage
    seed.stage = SeedStage.MERGE
    seed.status = SeedStatus.MERGED
    seed.blocked_reason = None
    seed.merged_at = now
    seed.updated_at = now

    provider_raw = str(info.get("provider") or "github").lower()
    provider_label = (
        "GitHub"
        if "github" in provider_raw
        else (
            "GitLab"
            if "gitlab" in provider_raw
            else ("Bitbucket" if "bitbucket" in provider_raw else "Git")
        )
    )
    pr_url = info.get("pull_request_url")
    merge_sha = info.get("merge_commit_sha")
    merged_by = info.get("merged_by")
    approved_by = [str(u).strip() for u in (info.get("approved_by") or []) if str(u).strip()]
    repo_full_name = str(info.get("repository_full_name") or "").strip()
    pr_number = info.get("number")

    # Persist merge_commit_sha and pull_request_url on the latest run output so Changes tab has it cached
    if merge_sha or pr_url:
        latest_run = (
            await session.execute(
                select(Run)
                .where(Run.seed_id == seed.id)
                .order_by(col(Run.created_at).desc())
                .limit(1)
            )
        ).scalars().first()
        if latest_run is not None:
            updated_out = dict(latest_run.output or {})
            if merge_sha:
                updated_out["merge_commit_sha"] = str(merge_sha)
            if pr_url and not updated_out.get("pull_request_url"):
                updated_out["pull_request_url"] = str(pr_url)
            updated_out["merged"] = True
            latest_run.output = updated_out

    ref_prefix = "!" if provider_label == "GitLab" else "#"
    if repo_full_name and pr_number:
        location_label = f"{provider_label} (`{repo_full_name}{ref_prefix}{pr_number}`)"
    elif repo_full_name:
        location_label = f"{provider_label} (`{repo_full_name}`)"
    else:
        location_label = provider_label

    approvers_str = ", ".join(f"**@{u}**" for u in approved_by)
    if approved_by and merged_by:
        attribution_text = f"Approved by {approvers_str} and merged by **@{merged_by}** in {location_label}"
    elif merged_by:
        attribution_text = f"Approved and merged by **@{merged_by}** in {location_label}"
    elif approved_by:
        attribution_text = f"Approved by {approvers_str} and merged in {location_label}"
    else:
        attribution_text = f"Merged in {location_label}"

    pr_link = f" ([View PR]({pr_url}))" if pr_url else ""
    sha_text = f" (`{str(merge_sha)[:7]}`)" if merge_sha else ""

    # Record an automatic Approval on Merge (and Review if transitioning from Review) so governance gates show complete
    stages_to_approve = [SeedStage.MERGE]
    if prev_stage == SeedStage.REVIEW:
        stages_to_approve.insert(0, SeedStage.REVIEW)
    for st in stages_to_approve:
        existing_app = (
            await session.execute(
                select(Approval)
                .where(
                    Approval.seed_id == seed.id,
                    Approval.stage == st,
                    Approval.decision == ApprovalDecision.APPROVED,
                )
                .limit(1)
            )
        ).scalars().first()
        if existing_app is None:
            session.add(
                Approval(
                    seed_id=seed.id,
                    stage=st,
                    snapshot={"source": "external_pr_merge", "merged_by": merged_by, "approved_by": approved_by},
                    decision=ApprovalDecision.APPROVED,
                    comment=f"{attribution_text}{sha_text}",
                    automatic=True,
                    decided_by=None,
                    approver_role=None,
                    created_at=now,
                )
            )

    comment_author_type, comment_author_id = await _resolve_automated_comment_author(
        session, seed, project, SeedStage.MERGE
    )
    session.add(
        SeedComment(
            seed_id=seed.id,
            author_type=comment_author_type,
            author_id=comment_author_id,
            stage=SeedStage.MERGE,
            body=f"**Pull Request Merged Externally ({provider_label}):**\n{attribution_text}{sha_text}{pr_link}. Seed automatically reconciled as **Merged**.",
            created_at=now,
            updated_at=now,
        )
    )

    if prev_stage != SeedStage.MERGE:
        record(
            session,
            type=ActivityType.STAGE_CHANGED,
            actor_type=ActorType.SYSTEM,
            actor_id=None,
            project_id=seed.project_id,
            seed_id=seed.id,
            data={"from": str(prev_stage), "to": str(SeedStage.MERGE), "reason": "external_pr_merge"},
        )

    merged_data: dict[str, Any] = {"source": "external_pr_merge"}
    if merge_sha:
        merged_data["merge_commit_sha"] = merge_sha
    if pr_url:
        merged_data["pull_request_url"] = pr_url
    if merged_by:
        merged_data["merged_by"] = merged_by
    if approved_by:
        merged_data["approved_by"] = approved_by
    if repo_full_name:
        merged_data["repository_full_name"] = repo_full_name
    record(
        session,
        type=ActivityType.SEED_MERGED,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        project_id=seed.project_id,
        seed_id=seed.id,
        data=merged_data,
    )
    invalidate_pr_status_cache(seed.id)
    await session.commit()
    return True


async def merge_seed_branch_into_default(
    session: AsyncSession, seed: Seed
) -> str | None:
    """Merge the seed branch (preferring open PR/MR merge API) into each repository's default branch and clean up."""
    repo_rows = (
        await session.execute(
            select(ProjectRepository)
            .where(ProjectRepository.project_id == seed.project_id)
            .order_by(
                col(ProjectRepository.is_primary).desc(),
                col(ProjectRepository.id).asc(),
            )
        )
    ).scalars().all()
    if not repo_rows:
        return None

    project = await session.get(Project, seed.project_id)
    project_slug = (project.slug if project else "").strip()
    seed_branch = await _resolve_seed_branch_name(session, seed, project)
    merge_label = (
        f"{seed_branch} ({project_slug}#{seed.number})"
        if project_slug
        else seed_branch
    )
    primary_merge_sha: str | None = None

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        for repo in repo_rows:
            backend = await session.get(Backend, repo.backend_id)
            if backend is None or not backend.enabled:
                continue
            secrets_map = decrypt_secrets(backend.secrets_encrypted)
            token = await resolve_git_backend_token(
                backend,
                secrets_map,
                repo_full_names=[repo.full_name],
                write_access=True,
            )
            if not token:
                continue

            provider = (backend.provider or "github").strip().lower()
            default_branch = (repo.default_branch or "main").strip()
            merged_via_api, api_sha = await merge_provider_pull_request(
                client,
                provider=provider,
                backend=backend,
                repo=repo,
                token=token,
                default_branch=default_branch,
                seed_branch=seed_branch,
                merge_label=merge_label,
            )
            if isinstance(api_sha, str) and api_sha and not primary_merge_sha:
                primary_merge_sha = api_sha

            if merged_via_api:
                continue

            try:
                auth_url = _build_authenticated_git_url(backend, repo.full_name, token)
                cli_sha = await asyncio.to_thread(
                    _merge_seed_branch_via_cli,
                    auth_url,
                    default_branch,
                    seed_branch,
                )
                if cli_sha and not primary_merge_sha:
                    primary_merge_sha = cli_sha
            except Exception as exc:
                logger.warning(
                    "Git CLI merge failed for %s (%s): %s",
                    repo.full_name,
                    seed_branch,
                    _scrub_url_credentials(str(exc)),
                )

    if primary_merge_sha:
        merge_run = (
            await session.execute(
                select(Run)
                .where(Run.seed_id == seed.id, Run.stage == SeedStage.MERGE)
                .order_by(col(Run.created_at).desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        if merge_run is not None:
            merge_run.output = {
                **(merge_run.output or {}),
                "merge_commit_sha": primary_merge_sha,
                "merged": True,
            }

    invalidate_pr_status_cache(seed.id)
    return primary_merge_sha


async def remediate_seed_if_pr_ci_failing(
    session: AsyncSession,
    seed: Seed,
    project: Project | None = None,
    *,
    pr_info: dict[str, Any] | None = None,
    workflow_name: str | None = None,
    branch_name: str | None = None,
    details_url: str | None = None,
    provider_override: str | None = None,
) -> str:
    """Automatically return a Seed to Execute with failing CI details if within policy max_attempts."""
    from gitseed_core.services.seeds import create_remediation_task_and_return_to_execute

    if seed.status in (SeedStatus.RUNNING, SeedStatus.MERGED, SeedStatus.CLOSED, SeedStatus.STAGED):
        return f"ignored_seed_status_{seed.status}"

    if project is None:
        project = await session.get(Project, seed.project_id)
    if project is None or project.archived_at is not None:
        return "project_missing_or_archived"

    if not is_auto_remediate_ci_enabled(project):
        return "auto_remediate_ci_disabled"

    # Manual autonomy seeds wait for explicit human action
    if seed.autonomy == SeedAutonomy.MANUAL and workflow_name is None:
        return "manual_autonomy_skips_auto_ci_remediation"

    info = dict(pr_info) if pr_info else await check_seed_pull_request_status(session, seed, project)
    if pr_info is not None and not info.get("failing_checks"):
        try:
            live_info = await check_seed_pull_request_status(session, seed, project)
            for k, v in live_info.items():
                if v and not info.get(k):
                    info[k] = v
        except Exception:
            pass

    ci_status = str(info.get("ci_status") or "").lower()
    if ci_status != "failing" and workflow_name is None:
        return f"ci_not_failing_{ci_status or 'none'}"

    provider_str = str(provider_override or info.get("provider") or "github").lower()
    provider_label = (
        "GitHub"
        if "github" in provider_str
        else ("GitLab" if "gitlab" in provider_str else ("Bitbucket" if "bitbucket" in provider_str else "Git"))
    )
    resolved_branch = (
        branch_name
        or await _resolve_seed_branch_name(session, seed, project)
    )
    head_sha = str(info.get("head_sha") or "").strip()
    sha_short = head_sha[:7] if head_sha else ""
    pr_url = str(info.get("pull_request_url") or details_url or "").strip() or None
    ci_summary = str(info.get("ci_summary") or workflow_name or "CI pipeline failed").strip()
    failing_checks = [
        str(item).strip()
        for item in (info.get("failing_checks") or [])
        if str(item).strip()
    ]
    if workflow_name and not any(workflow_name.lower() in c.lower() for c in failing_checks):
        wf_line = f"Workflow/Pipeline `{workflow_name}` failed on `{resolved_branch}`"
        if details_url:
            wf_line += f" ({details_url})"
        failing_checks.insert(0, wf_line)
    if not failing_checks:
        fallback_line = f"{provider_label} CI failed on `{resolved_branch}` ({ci_summary})"
        if pr_url:
            fallback_line += f" ({pr_url})"
        failing_checks.append(fallback_line)

    # Compute deduplication marker so the same commit SHA / failure does not double-trigger
    dedup_marker = f"commit `{sha_short}`" if sha_short else f"summary `{ci_summary}`"

    latest_human_reset = await session.scalar(
        select(func.max(Approval.created_at)).where(
            Approval.seed_id == seed.id,
            Approval.decision == ApprovalDecision.CHANGES_REQUESTED,
        )
    )
    comments_stmt = (
        select(SeedComment)
        .where(
            SeedComment.seed_id == seed.id,
        )
        .order_by(col(SeedComment.created_at).desc())
    )
    if latest_human_reset is not None:
        comments_stmt = comments_stmt.where(col(SeedComment.created_at) >= latest_human_reset)
    sys_comments = list((await session.execute(comments_stmt)).scalars().all())

    ci_remediation_comments = [
        c for c in sys_comments if c.body.startswith("**Automated CI Failure Detected")
    ]
    limit_comments = [
        c for c in sys_comments if c.body.startswith("**Automated CI Remediation Limit Reached")
    ]

    # If we already queued a remediation or logged the limit for this exact commit SHA, skip
    if any(dedup_marker in c.body for c in ci_remediation_comments) or any(
        dedup_marker in c.body for c in limit_comments
    ):
        return "already_processed_for_commit"

    exec_pol = await get_effective_stage_policy(session, project.id, SeedStage.EXECUTE)
    max_attempts = max(1, exec_pol.max_attempts)
    attempts_used = len(ci_remediation_comments)
    now = datetime.now(UTC)
    comment_author_type, comment_author_id = await _resolve_automated_comment_author(
        session, seed, project, seed.stage
    )

    if attempts_used >= max_attempts:
        session.add(
            SeedComment(
                seed_id=seed.id,
                author_type=comment_author_type,
                author_id=comment_author_id,
                stage=seed.stage,
                body=(
                    f"**Automated CI Remediation Limit Reached ({attempts_used}/{max_attempts}):**\n"
                    f"{provider_label} CI is still failing on {dedup_marker} ({ci_summary}), and the configured "
                    f"Execute stage attempt limit (`{max_attempts}`) has been reached. Pausing for human review."
                ),
                created_at=now,
                updated_at=now,
            )
        )
        await session.commit()
        return "ci_remediation_limit_reached"

    attempt_num = attempts_used + 1
    fenced_checks = [
        chk.replace("</untrusted_external_input>", "&lt;/untrusted_external_input&gt;")
        for chk in failing_checks
    ]
    bullet_text = _wrap_untrusted_ci_text(
        "\n".join(f"- {chk}" for chk in fenced_checks)
    )
    safe_ci_summary = ci_summary.replace(
        "</untrusted_external_input>", "&lt;/untrusted_external_input&gt;"
    )
    ci_header = (
        f"{provider_label} CI pipeline failed on branch `{resolved_branch}` "
        f"({dedup_marker} · <untrusted_external_input>{safe_ci_summary}</untrusted_external_input>)."
        + (f" [View PR/Pipeline]({pr_url})" if pr_url else "")
    )
    comment_body = (
        f"**Automated CI Failure Detected ({provider_label} · Attempt {attempt_num}/{max_attempts}):**\n"
        f"{ci_header}\n\n"
        f"**Failing Checks / Jobs:**\n{bullet_text}\n\n"
        f"Automatically returning seed to **Execute** so `@coder` can inspect the pipeline failure and push a fix."
    )
    session.add(
        SeedComment(
            seed_id=seed.id,
            author_type=comment_author_type,
            author_id=comment_author_id,
            stage=seed.stage,
            body=comment_body,
            created_at=now,
            updated_at=now,
        )
    )
    await session.flush()

    await create_remediation_task_and_return_to_execute(
        session,
        seed,
        from_stage=seed.stage,
        operator_comment=ci_header,
        review_summary=(
            f"CI pipeline failed on `{resolved_branch}` ({dedup_marker}, remediation attempt {attempt_num}/{max_attempts}). "
            "Inspect the failing CI check details below (treated as untrusted external input), reproduce and fix the root cause in `/workspace`, "
            "and ensure all tests/checks pass."
        ),
        review_findings=[_wrap_untrusted_ci_text(chk) for chk in fenced_checks],
        actor_type=ActorType.SYSTEM,
        actor_id=project.created_by,
        now=now,
    )
    await session.commit()
    return "ci_remediation_queued"


async def maybe_auto_merge_ready_pr(
    session: AsyncSession,
    seed: Seed,
    project: Project | None = None,
    *,
    pr_info: dict[str, Any] | None = None,
) -> bool:
    """If a Seed in Merge (awaiting_approval) was waiting on PR readiness and is now ready & approved/auto-approvable, merge it."""
    if seed.stage != SeedStage.MERGE or seed.status != SeedStatus.AWAITING_APPROVAL:
        return False

    info = dict(pr_info) if pr_info else await check_seed_pull_request_status(session, seed, project)
    if info.get("state") != "open" or not info.get("ready_to_merge", True):
        return False

    pol = await get_effective_stage_policy(session, seed.project_id, SeedStage.MERGE)
    can_auto_complete = (
        not pol.requires_approval
        or (
            seed.autonomy == SeedAutonomy.AUTONOMOUS
            and pol.allow_auto_approve
        )
    )
    if not can_auto_complete:
        # Check if required human approvals for the current Merge stage entry have already been recorded
        app_stmt = select(Approval).where(
            Approval.seed_id == seed.id,
            Approval.stage == SeedStage.MERGE,
            Approval.decision == ApprovalDecision.APPROVED,
        )
        if seed.stage_entered_at is not None:
            app_stmt = app_stmt.where(col(Approval.created_at) >= seed.stage_entered_at)
        approvals = list((await session.execute(app_stmt)).scalars().all())
        distinct_approvers = {a.decided_by for a in approvals if a.decided_by}
        if len(distinct_approvers) >= max(1, pol.min_approvers):
            can_auto_complete = True

    if not can_auto_complete:
        return False

    now = datetime.now(UTC)
    if (
        seed.autonomy == SeedAutonomy.AUTONOMOUS
        and pol.requires_approval
        and pol.allow_auto_approve
    ):
        auto_approval = Approval(
            seed_id=seed.id,
            stage=SeedStage.MERGE,
            snapshot={
                "title": seed.title,
                "specification": seed.specification,
                "acceptance_criteria": seed.acceptance_criteria,
                "plan": seed.plan,
            },
            decision=ApprovalDecision.APPROVED,
            automatic=True,
            decided_by=None,
            approver_role=None,
            created_at=now,
        )
        session.add(auto_approval)
        await session.flush()

    merge_sha = await merge_seed_branch_into_default(session, seed)
    seed.stage = SeedStage.MERGE
    seed.status = SeedStatus.MERGED
    seed.blocked_reason = None
    seed.merged_at = now
    seed.updated_at = now
    merged_data: dict[str, Any] = {"source": "pr_ready_auto_merge"}
    if merge_sha:
        merged_data["merge_commit_sha"] = merge_sha
    if info.get("pull_request_url"):
        merged_data["pull_request_url"] = info["pull_request_url"]
    record(
        session,
        type=ActivityType.SEED_MERGED,
        actor_type=ActorType.SYSTEM,
        actor_id=None,
        project_id=seed.project_id,
        seed_id=seed.id,
        data=merged_data,
    )
    invalidate_pr_status_cache(seed.id)
    await session.commit()
    return True


async def create_or_sync_seed_pull_request(
    session: AsyncSession,
    actor: Any,
    seed_id: str,
    repo_id: int | None = None,
) -> dict[str, Any]:
    """Ensure a Pull Request / Merge Request exists for a seed (or sync its live status) and return PullRequestStatusResponse dict."""
    seed = await session.get(Seed, seed_id)
    if seed is None:
        raise NotFound("Seed not found.")

    project = await projects_service.require_project_contributor(
        session, actor, seed.project_id
    )
    seed_branch = await _resolve_seed_branch_name(session, seed, project)

    existing = await check_seed_pull_request_status(
        session, seed, project, seed_branch, repo_id=repo_id
    )
    if not existing.get("pull_request_url"):
        created_url = await ensure_seed_pull_request(
            session, seed, project, seed_branch
        )
        if created_url:
            await session.commit()
            existing = await check_seed_pull_request_status(
                session, seed, project, seed_branch, repo_id=repo_id
            )

    pr_num = existing.get("number")
    repo_full_name = existing.get("repository_full_name")
    if pr_num and repo_full_name:
        try:
            from gitseed_core.services.git_pr_providers import (
                sync_seed_governance_receipt_comment,
            )
            from gitseed_core.services.receipts import build_seed_governance_receipt

            stmt = (
                select(ProjectRepository, Backend)
                .join(Backend, col(Backend.id) == col(ProjectRepository.backend_id))
                .where(
                    col(ProjectRepository.project_id) == seed.project_id,
                    col(ProjectRepository.full_name) == repo_full_name,
                )
            )
            if repo_id is not None:
                stmt = stmt.where(col(ProjectRepository.id) == repo_id)
            row = (await session.execute(stmt)).first()
            if row is not None:
                repo_obj, backend_obj = row
                secrets_map = decrypt_secrets(backend_obj.secrets_encrypted)
                token = await resolve_git_backend_token(
                    backend_obj,
                    secrets_map,
                    repo_full_names=[repo_obj.full_name],
                    write_access=True,
                )
                if token:
                    envelope = await build_seed_governance_receipt(session, seed.id)
                    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                        await sync_seed_governance_receipt_comment(
                            client,
                            provider=str(existing.get("provider") or backend_obj.provider or "github"),
                            backend=backend_obj,
                            repo=repo_obj,
                            token=token,
                            pr_number=pr_num,
                            envelope=envelope,
                        )
        except Exception as exc:
            logger.debug("Best-effort governance receipt comment sync failed: %s", exc)

    return existing


async def post_seed_pull_request_comment(
    session: AsyncSession,
    actor: Any,
    seed_id: str,
    body: str,
    repo_id: int | None = None,
    mirror_to_seed: bool = True,
) -> dict[str, Any]:
    """Post a comment directly to the seed's GitHub PR / GitLab MR / Bitbucket PR and optionally mirror it to SeedComment."""
    from gitseed_core.services import seeds as seeds_service

    clean_body = (body or "").strip()
    if not clean_body:
        raise Invalid("Comment body must not be empty.")

    seed = await session.get(Seed, seed_id)
    if seed is None:
        raise NotFound("Seed not found.")

    project = await projects_service.require_project_contributor(
        session, actor, seed.project_id
    )
    seed_branch = await _resolve_seed_branch_name(session, seed, project)

    pr_info = await check_seed_pull_request_status(
        session, seed, project, seed_branch, repo_id=repo_id
    )
    if not pr_info.get("number"):
        await ensure_seed_pull_request(session, seed, project, seed_branch)
        pr_info = await check_seed_pull_request_status(
            session, seed, project, seed_branch, repo_id=repo_id
        )

    pr_num = pr_info.get("number")
    repo_full_name = pr_info.get("repository_full_name")
    if not pr_num or not repo_full_name:
        raise Conflict("No active Pull Request / Merge Request found for this seed.")

    stmt = (
        select(ProjectRepository, Backend)
        .join(Backend, col(Backend.id) == col(ProjectRepository.backend_id))
        .where(
            col(ProjectRepository.project_id) == seed.project_id,
            col(ProjectRepository.full_name) == repo_full_name,
        )
    )
    if repo_id is not None:
        stmt = stmt.where(col(ProjectRepository.id) == repo_id)
    row = (await session.execute(stmt)).first()
    if row is None:
        raise NotFound("Repository for Pull Request not found.")
    repo, backend = row

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    token = await resolve_git_backend_token(
        backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=True,
    )
    if not token:
        raise Conflict("No Git credentials available to post a Pull Request comment.")

    author_name = (
        getattr(actor, "name", None)
        or getattr(actor, "username", None)
        or getattr(actor, "email", None)
        or "GitSeed User"
    )
    posted_body = clean_body
    created_item: dict[str, Any] | None = None
    provider = (backend.provider or "").strip().lower()

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
        if provider in ("github", "github-enterprise", "github-app"):
            api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
            resp = await client.post(
                f"{api_base}/repos/{repo.full_name}/issues/{pr_num}/comments",
                headers=headers,
                json={"body": posted_body},
            )
            if resp.status_code not in (200, 201):
                raise Conflict(f"GitHub rejected PR comment (HTTP {resp.status_code}).")
            ic = resp.json() if isinstance(resp.json(), dict) else {}
            created_item = {
                "id": f"ic-{ic.get('id', 'new')}",
                "author": str((ic.get("user") or {}).get("login") or author_name),
                "body": str(ic.get("body") or posted_body),
                "kind": "comment",
                "review_state": None,
                "path": None,
                "line": None,
                "created_at": str(ic.get("created_at") or datetime.now(UTC).isoformat()),
                "html_url": str(ic.get("html_url") or pr_info.get("pull_request_url") or "") or None,
            }
        elif provider == "gitlab":
            api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
            encoded = quote(repo.full_name, safe="")
            resp = await client.post(
                f"{api_base}/projects/{encoded}/merge_requests/{pr_num}/notes",
                headers=headers,
                json={"body": posted_body},
            )
            if resp.status_code not in (200, 201):
                raise Conflict(f"GitLab rejected MR note (HTTP {resp.status_code}).")
            note = resp.json() if isinstance(resp.json(), dict) else {}
            created_item = {
                "id": f"gl-note-{note.get('id', 'new')}",
                "author": str((note.get("author") or {}).get("username") or author_name),
                "body": str(note.get("body") or posted_body),
                "kind": "comment",
                "review_state": None,
                "path": None,
                "line": None,
                "created_at": str(note.get("created_at") or datetime.now(UTC).isoformat()),
                "html_url": pr_info.get("pull_request_url"),
            }
        elif provider == "bitbucket":
            raw_cfg = backend.config or {}
            raw_ep, bb_headers = _bitbucket_api_base_and_headers(
                backend.endpoint or raw_cfg.get("url"), token
            )
            base_url = docker_host_fallback_url(raw_ep)
            bb_headers["Accept"] = "application/json"
            explicit_user = (
                raw_cfg.get("username")
                or raw_cfg.get("user")
                or raw_cfg.get("email")
            )
            auth: tuple[str, str] | None = None
            if ":" in token:
                user_part, pass_part = token.split(":", 1)
                auth = (user_part, pass_part)
                bb_headers.pop("Authorization", None)
            elif explicit_user:
                auth = (str(explicit_user), token)
                bb_headers.pop("Authorization", None)
            resp = await client.post(
                f"{base_url}/repositories/{repo.full_name}/pullrequests/{pr_num}/comments",
                headers=bb_headers,
                auth=auth,
                json={"content": {"raw": posted_body}},
            )
            if resp.status_code not in (200, 201):
                raise Conflict(f"Bitbucket rejected PR comment (HTTP {resp.status_code}).")
            cm = resp.json() if isinstance(resp.json(), dict) else {}
            created_item = {
                "id": f"bb-comment-{cm.get('id', 'new')}",
                "author": str(
                    (cm.get("user") or {}).get("nickname")
                    or (cm.get("user") or {}).get("display_name")
                    or author_name
                ),
                "body": str(((cm.get("content") or {}).get("raw")) or posted_body),
                "kind": "comment",
                "review_state": None,
                "path": None,
                "line": None,
                "created_at": str(cm.get("created_on") or datetime.now(UTC).isoformat()),
                "html_url": str(((cm.get("links") or {}).get("html") or {}).get("href") or "") or pr_info.get("pull_request_url"),
            }
        else:
            raise Conflict(f"Unsupported Git provider '{provider or backend.type}' for PR comments.")

    if mirror_to_seed:
        try:
            await seeds_service.create_comment(
                session,
                actor,
                seed_id=seed.id,
                body=f"**[PR #{pr_num}]** {clean_body}",
            )
        except Exception as exc:
            logger.debug("Mirroring PR comment to seed comment failed: %s", exc)

    return created_item



