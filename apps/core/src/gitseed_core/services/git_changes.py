from __future__ import annotations

import asyncio
import logging
import os
import time
import urllib.parse
from typing import Any

import httpx
from gitseed_common.enums import ActivityType, SeedStage, SeedStatus
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.activity import Activity
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.git_browse import (
    _github_api_base_and_headers,
    _gitlab_api_base_and_headers,
    build_git_web_url,
    resolve_git_web_base,
)
from gitseed_core.services.git_merge import (
    _build_git_urls,
    _merge_seed_branch_via_cli,
    check_seed_pull_request_status,
    ensure_seed_pull_request,
    format_seed_branch,
    is_auto_create_pr_enabled,
    merge_seed_branch_into_default,
    reconcile_seed_if_pr_merged_externally,
)
from gitseed_core.services.git_pr_providers import (
    _bitbucket_api_base_and_headers,
    extract_pr_number_from_url,
    is_github_rate_limited,
    record_github_rate_limit,
)

logger = logging.getLogger("uvicorn.error")

_SEED_CHANGES_CACHE: dict[tuple[Any, ...], tuple[float, dict[str, Any]]] = {}
_LIVE_CHANGES_CACHE_TTL = 20.0
_TERMINAL_CHANGES_CACHE_TTL = 600.0

__all__ = [
    "_merge_seed_branch_via_cli",
    "format_seed_branch",
    "get_seed_changes",
    "merge_seed_branch_into_default",
]


def _split_unified_diff_by_file(diff_text: str) -> dict[str, dict[str, Any]]:
    """Split a unified `git diff` response into per-file patch metadata keyed by file path."""
    if not diff_text or not diff_text.strip():
        return {}
    sections: list[list[str]] = []
    current: list[str] = []
    for line in diff_text.splitlines():
        if line.startswith("diff --git "):
            if current:
                sections.append(current)
            current = [line]
        elif current:
            current.append(line)
    if current:
        sections.append(current)

    result: dict[str, dict[str, Any]] = {}
    for sec in sections:
        header = sec[0]
        old_path = ""
        new_path = ""
        parts = header.split(" ")
        if len(parts) >= 4 and parts[2].startswith("a/") and parts[3].startswith("b/"):
            old_path = parts[2][2:]
            new_path = parts[3][2:]

        status_str = "modified"
        hunk_start_idx: int | None = None
        for idx, ln in enumerate(sec[1:], start=1):
            if ln.startswith("@@ "):
                hunk_start_idx = idx
                break
            if ln.startswith("new file mode"):
                status_str = "added"
            elif ln.startswith("deleted file mode"):
                status_str = "removed"
            elif ln.startswith("rename from "):
                status_str = "renamed"
                old_path = ln.removeprefix("rename from ").strip()
            elif ln.startswith("rename to "):
                status_str = "renamed"
                new_path = ln.removeprefix("rename to ").strip()
            elif ln.startswith("--- a/"):
                old_path = ln.removeprefix("--- a/").strip()
            elif ln.startswith("--- /dev/null"):
                status_str = "added"
            elif ln.startswith("+++ b/"):
                new_path = ln.removeprefix("+++ b/").strip()
            elif ln.startswith("+++ /dev/null"):
                status_str = "removed"

        file_path = new_path or old_path
        if not file_path:
            continue

        patch_lines = sec[hunk_start_idx:] if hunk_start_idx is not None else []
        adds = sum(
            1 for ln in patch_lines if ln.startswith("+") and not ln.startswith("+++")
        )
        dels = sum(
            1 for ln in patch_lines if ln.startswith("-") and not ln.startswith("---")
        )
        raw_patch = "\n".join(patch_lines)
        truncated = len(raw_patch) > 120_000
        if truncated:
            raw_patch = raw_patch[:120_000]

        result[file_path] = {
            "path": file_path,
            "previous_path": old_path if (status_str == "renamed" and old_path != file_path) else None,
            "status": status_str,
            "additions": adds,
            "deletions": dels,
            "patch": raw_patch or None,
            "truncated": truncated,
        }
    return result


def _parse_github_commit_item(
    item: dict[str, Any],
    provider: str,
    endpoint: str | None,
    full_name: str,
) -> dict[str, Any] | None:
    sha = str(item.get("sha") or "").strip()
    if not sha:
        return None
    commit_obj = item.get("commit") if isinstance(item.get("commit"), dict) else {}
    author_obj = (
        commit_obj.get("author") if isinstance(commit_obj.get("author"), dict) else {}
    )
    msg = str(commit_obj.get("message") or "").strip()
    first_line = msg.splitlines()[0].strip() if msg else sha[:7]
    return {
        "sha": sha,
        "short_sha": sha[:7],
        "message": first_line,
        "author_name": str(author_obj.get("name") or "").strip() or None,
        "committed_at": str(author_obj.get("date") or "").strip() or None,
        "web_url": str(item.get("html_url") or "")
        or build_git_web_url(provider, endpoint, full_name, commit_sha=sha),
    }


def _parse_github_file_item(
    f: dict[str, Any],
    provider: str,
    endpoint: str | None,
    full_name: str,
    head_ref: str,
) -> dict[str, Any] | None:
    filename = str(f.get("filename") or "").strip()
    if not filename:
        return None
    raw_status = str(f.get("status") or "modified").lower()
    if raw_status in ("added", "new"):
        norm_status = "added"
    elif raw_status in ("removed", "deleted"):
        norm_status = "removed"
    elif raw_status == "renamed":
        norm_status = "renamed"
    else:
        norm_status = "modified"

    additions = int(f.get("additions") or 0)
    deletions = int(f.get("deletions") or 0)
    changes = int(f.get("changes") or (additions + deletions))
    raw_patch = f.get("patch")
    patch_str = str(raw_patch) if isinstance(raw_patch, str) else None
    truncated = False
    if patch_str and len(patch_str) > 120_000:
        patch_str = patch_str[:120_000]
        truncated = True

    return {
        "path": filename,
        "previous_path": str(f.get("previous_filename") or "").strip() or None,
        "status": norm_status,
        "additions": additions,
        "deletions": deletions,
        "changes": changes,
        "patch": patch_str,
        "truncated": truncated,
        "web_url": str(f.get("blob_url") or "")
        or build_git_web_url(
            provider,
            endpoint,
            full_name,
            ref=head_ref,
            path=filename,
            is_dir=False,
        ),
    }


def _fetch_remote_seed_changes_via_git_cli(
    *,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    default_branch: str,
    seed_branch: str,
    pr_number: int | None,
    merge_commit_sha: str | None,
    execute_commits: list[str],
    base_commit_sha: str | None,
    is_merged: bool,
) -> dict[str, Any]:
    """Fallback diff & commit resolver using Git Smart HTTP against a cached bare repo.

    Works even when provider REST APIs (e.g. `api.github.com`) are rate-limited (`403`)
    or when a merged seed's feature branch was deleted after merge. Never persists
    credentials in `.git/config`.
    """
    from gitseed_core.services.local_git import (
        _run_git,
        local_repos_root,
        validate_refname,
    )

    provider = (backend.provider or "github").strip().lower()
    endpoint = backend.endpoint
    default_branch = validate_refname(default_branch)
    seed_branch = validate_refname(seed_branch)
    if merge_commit_sha:
        merge_commit_sha = validate_refname(merge_commit_sha)
    clean_exec_commits = [validate_refname(c) for c in execute_commits if c]
    if base_commit_sha:
        base_commit_sha = validate_refname(base_commit_sha)

    cache_root = local_repos_root().parent / "remote_diff_cache"
    cache_root.mkdir(parents=True, exist_ok=True)
    bare_path = cache_root / f"repo_{repo.id or 'default'}.git"
    if not (bare_path / "HEAD").exists():
        bare_path.mkdir(parents=True, exist_ok=True)
        _run_git(["init", "--bare", "--", str(bare_path)])
        _run_git(["-C", str(bare_path), "config", "gc.auto", "0"])

    auth_url, _ = _build_git_urls(backend, repo.full_name, token)

    # 1. Fetch default_branch and seed_branch over Git Smart HTTP (transient URL, never stored)
    _run_git(
        [
            "-C",
            str(bare_path),
            "fetch",
            "--depth=50",
            "--no-tags",
            "--",
            auth_url,
            f"+refs/heads/{default_branch}:refs/heads/{default_branch}",
        ],
        timeout=25,
    )
    branch_fetch = _run_git(
        [
            "-C",
            str(bare_path),
            "fetch",
            "--depth=50",
            "--no-tags",
            "--",
            auth_url,
            f"+refs/heads/{seed_branch}:refs/heads/{seed_branch}",
        ],
        timeout=25,
    )
    branch_exists = branch_fetch.returncode == 0
    if not branch_exists:
        _run_git(
            ["-C", str(bare_path), "update-ref", "-d", f"refs/heads/{seed_branch}"]
        )

    pr_local_ref: str | None = None
    if pr_number and pr_number > 0:
        remote_pr_ref = (
            f"refs/merge-requests/{pr_number}/head"
            if provider == "gitlab"
            else f"refs/pull/{pr_number}/head"
            if provider in ("github", "github-enterprise", "github-app")
            else None
        )
        if remote_pr_ref:
            pr_target = f"refs/remotes/pr/{pr_number}"
            pr_fetch = _run_git(
                [
                    "-C",
                    str(bare_path),
                    "fetch",
                    "--depth=50",
                    "--no-tags",
                    "--",
                    auth_url,
                    f"+{remote_pr_ref}:{pr_target}",
                ],
                timeout=25,
            )
            if pr_fetch.returncode == 0:
                pr_local_ref = pr_target

    for explicit_sha in [merge_commit_sha, clean_exec_commits[-1] if clean_exec_commits else None, base_commit_sha]:
        if not explicit_sha:
            continue
        has_sha = _run_git(
            ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"{explicit_sha}^{{commit}}"]
        )
        if has_sha.returncode != 0:
            _run_git(
                [
                    "-C",
                    str(bare_path),
                    "fetch",
                    "--depth=50",
                    "--no-tags",
                    "--",
                    auth_url,
                    explicit_sha,
                ],
                timeout=25,
            )

    def _resolve_sha(ref_expr: str) -> str | None:
        r = _run_git(
            ["-C", str(bare_path), "rev-parse", "--verify", "--end-of-options", f"{ref_expr}^{{commit}}"]
        )
        return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None

    branch_head_sha = _resolve_sha(f"refs/heads/{seed_branch}") if branch_exists else None
    pr_head_sha = _resolve_sha(pr_local_ref) if pr_local_ref else None
    resolved_merge_sha = _resolve_sha(merge_commit_sha) if merge_commit_sha else None
    resolved_exec_head = _resolve_sha(clean_exec_commits[-1]) if clean_exec_commits else None
    resolved_base_sha = _resolve_sha(base_commit_sha) if base_commit_sha else None
    default_head_sha = _resolve_sha(f"refs/heads/{default_branch}")

    candidate_ranges: list[tuple[str, str, str, str]] = []
    if branch_exists and branch_head_sha and not is_merged and default_head_sha:
        candidate_ranges.append(
            ("branch", f"refs/heads/{default_branch}..refs/heads/{seed_branch}", f"refs/heads/{default_branch}...refs/heads/{seed_branch}", branch_head_sha)
        )
    if resolved_merge_sha and _resolve_sha(f"{resolved_merge_sha}^1"):
        candidate_ranges.append(
            ("merged_commit", f"{resolved_merge_sha}^1..{resolved_merge_sha}", f"{resolved_merge_sha}^1..{resolved_merge_sha}", resolved_merge_sha)
        )
    seed_tip_sha = pr_head_sha or branch_head_sha or resolved_exec_head
    if seed_tip_sha:
        tip_mode = "merged_commit" if (is_merged or resolved_merge_sha) else ("branch" if branch_exists else "execute_commit")
        if resolved_base_sha and resolved_base_sha != seed_tip_sha:
            candidate_ranges.append(
                (tip_mode, f"{resolved_base_sha}..{seed_tip_sha}", f"{resolved_base_sha}...{seed_tip_sha}", seed_tip_sha)
            )
        if default_head_sha and default_head_sha != seed_tip_sha:
            candidate_ranges.append(
                (tip_mode, f"refs/heads/{default_branch}..{seed_tip_sha}", f"refs/heads/{default_branch}...{seed_tip_sha}", seed_tip_sha)
            )
        first_sha = _resolve_sha(clean_exec_commits[0]) if clean_exec_commits else seed_tip_sha
        if first_sha and _resolve_sha(f"{first_sha}^"):
            candidate_ranges.append(
                (tip_mode, f"{first_sha}^..{seed_tip_sha}", f"{first_sha}^..{seed_tip_sha}", seed_tip_sha)
            )

    commits: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    chosen_mode = "branch" if branch_exists else "none"
    chosen_head = branch_head_sha or resolved_merge_sha or pr_head_sha or resolved_exec_head

    for cand_mode, log_range, diff_range, cand_head in candidate_ranges:
        cand_commits: list[dict[str, Any]] = []
        cand_files: list[dict[str, Any]] = []

        log_res = _run_git(
            [
                "-C",
                str(bare_path),
                "log",
                "--reverse",
                "--format=%H|%h|%s|%an|%aI",
                "--end-of-options",
                log_range,
                "--",
            ]
        )
        if log_res.returncode == 0:
            for line in log_res.stdout.splitlines():
                parts = line.split("|", 4)
                if len(parts) < 5:
                    continue
                sha, short_sha, subj, author, date_iso = parts
                cand_commits.append(
                    {
                        "sha": sha,
                        "short_sha": short_sha,
                        "message": subj,
                        "author_name": author or None,
                        "committed_at": date_iso or None,
                        "web_url": build_git_web_url(
                            provider, endpoint, repo.full_name, commit_sha=sha
                        ),
                    }
                )

        diff_res = _run_git(
            ["-C", str(bare_path), "diff", "--end-of-options", diff_range, "--"],
            timeout=25,
        )
        parsed_map = (
            _split_unified_diff_by_file(diff_res.stdout)
            if diff_res.returncode == 0
            else {}
        )
        file_ref = cand_head or (seed_branch if branch_exists else default_branch)
        for f_path, meta in parsed_map.items():
            adds = int(meta["additions"])
            dels = int(meta["deletions"])
            cand_files.append(
                {
                    "path": f_path,
                    "previous_path": meta["previous_path"],
                    "status": meta["status"],
                    "additions": adds,
                    "deletions": dels,
                    "changes": adds + dels,
                    "patch": meta["patch"],
                    "truncated": bool(meta["truncated"]),
                    "web_url": build_git_web_url(
                        provider,
                        endpoint,
                        repo.full_name,
                        ref=file_ref,
                        path=f_path,
                        is_dir=False,
                    ),
                }
            )

        if cand_files or cand_commits:
            commits = cand_commits
            files = cand_files
            chosen_mode = cand_mode
            chosen_head = cand_head
            break

    return {
        "branch_exists": branch_exists,
        "comparison_mode": chosen_mode,
        "head_commit_sha": chosen_head,
        "commits": commits,
        "files": files,
    }


async def get_seed_changes(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    repo_id: int | None = None,
) -> dict[str, Any]:
    """Return the commit list and unified file diffs attributed to a seed (live branch or post-merge)."""
    from gitseed_core.services import seeds as seeds_service

    seed = await seeds_service.get_seed(session, actor, seed_id)
    project = await session.get(Project, seed.project_id)
    project_slug = (project.slug if project else "").strip()
    canonical_branch = format_seed_branch(project_slug, seed.number)

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

    # Gather commit SHAs and branch_name recorded across the seed's runs
    seed_runs = (
        await session.execute(
            select(Run)
            .where(Run.seed_id == seed.id)
            .order_by(col(Run.created_at).asc())
        )
    ).scalars().all()

    execute_commits: list[str] = []
    merge_commit_sha: str | None = None
    recorded_branch: str | None = None
    pull_request_url: str | None = None
    blocked_paths_reverted: list[str] = []
    base_heads_by_repo: dict[str, str] = {}
    for r in seed_runs:
        if not isinstance(r.output, dict):
            continue
        trusted_obj = (
            r.output.get("trusted")
            if isinstance(r.output.get("trusted"), dict)
            else {}
        )
        b_name = str(r.output.get("branch_name") or "").strip()
        if not b_name and (trusted_obj.get("pushed") or trusted_obj.get("commit_sha")):
            b_name = str(trusted_obj.get("branch") or "").strip()
        if b_name:
            recorded_branch = b_name
        c_sha = str(
            r.output.get("commit_sha") or trusted_obj.get("commit_sha") or ""
        ).strip()
        if c_sha and c_sha not in execute_commits:
            execute_commits.append(c_sha)
        if r.stage == SeedStage.EXECUTE and isinstance(
            trusted_obj.get("base_heads"), dict
        ):
            for k_repo, v_sha in trusted_obj["base_heads"].items():
                if k_repo and v_sha and k_repo not in base_heads_by_repo:
                    base_heads_by_repo[str(k_repo)] = str(v_sha).strip()
        m_sha = str(r.output.get("merge_commit_sha") or "").strip()
        if m_sha:
            merge_commit_sha = m_sha
        pr_url = str(r.output.get("pull_request_url") or "").strip()
        if pr_url:
            pull_request_url = pr_url
        rev_blocked = r.output.get("blocked_paths_reverted")
        if isinstance(rev_blocked, list):
            for bp in rev_blocked:
                if bp and str(bp) not in blocked_paths_reverted:
                    blocked_paths_reverted.append(str(bp))

    if not merge_commit_sha or not pull_request_url:
        try:
            merged_events = (
                await session.execute(
                    select(Activity)
                    .where(
                        Activity.seed_id == seed.id,
                        Activity.type == ActivityType.SEED_MERGED,
                    )
                    .order_by(col(Activity.id).desc())
                    .limit(3)
                )
            ).scalars().all()
            for ev in merged_events:
                if not isinstance(ev.data, dict):
                    continue
                if not merge_commit_sha and ev.data.get("merge_commit_sha"):
                    merge_commit_sha = str(ev.data["merge_commit_sha"]).strip() or None
                if not pull_request_url and ev.data.get("pull_request_url"):
                    pull_request_url = str(ev.data["pull_request_url"]).strip() or None
        except Exception:
            pass

    seed_branch = recorded_branch or canonical_branch
    has_code_activity = (
        bool(execute_commits)
        or bool(merge_commit_sha)
        or bool(recorded_branch)
        or bool(pull_request_url)
        or seed.stage in (SeedStage.EXECUTE, SeedStage.REVIEW, SeedStage.MERGE)
        or seed.status == SeedStatus.MERGED
    )

    if not repo_rows:
        return {
            "seed_id": seed.id,
            "seed_number": seed.number,
            "repository_id": None,
            "repository_full_name": None,
            "provider": None,
            "base_ref": "main",
            "head_ref": seed_branch,
            "comparison_mode": "none",
            "branch_exists": False,
            "merge_commit_sha": None,
            "head_commit_sha": None,
            "web_diff_url": None,
            "pull_request_url": pull_request_url,
            "blocked_paths_reverted": blocked_paths_reverted,
            "total_commits": 0,
            "total_files": 0,
            "total_additions": 0,
            "total_deletions": 0,
            "commits": [],
            "files": [],
        }

    repo = next((r for r in repo_rows if repo_id is not None and r.id == repo_id), None)
    if repo is None:
        repo = repo_rows[0]

    backend = await session.get(Backend, repo.backend_id)
    provider = (backend.provider if backend else "github").strip().lower()
    endpoint = backend.endpoint if backend else None
    default_branch = (repo.default_branch or "main").strip()
    web_base = resolve_git_web_base(provider, endpoint)
    base_commit_sha = base_heads_by_repo.get(repo.full_name)
    is_merged_seed = seed.status == SeedStatus.MERGED
    cache_enabled = "PYTEST_CURRENT_TEST" not in os.environ
    changes_cache_key = (
        seed.id,
        int(repo.id or 0),
        str(seed.stage),
        str(seed.status),
        merge_commit_sha or "",
        execute_commits[-1] if execute_commits else "",
        pull_request_url or "",
    )
    if cache_enabled:
        cached_changes = _SEED_CHANGES_CACHE.get(changes_cache_key)
        if cached_changes is not None:
            exp_ts, cached_body = cached_changes
            if time.time() < exp_ts:
                return dict(cached_body)

    secrets_map = decrypt_secrets(backend.secrets_encrypted) if backend else {}
    token = await resolve_git_backend_token(
        backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=False,
    )

    commits: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    branch_exists = False
    comparison_mode = "none"
    head_ref = seed_branch
    head_commit_sha: str | None = execute_commits[-1] if execute_commits else None
    web_diff_url: str | None = None

    # Check remote PR/MR state up front so external merges reconcile immediately and provide merge_commit_sha / PR number for diff fallback
    pull_request_status: dict[str, Any] | None = None
    pr_info: dict[str, Any] = {}
    if has_code_activity:
        try:
            pr_info = await check_seed_pull_request_status(
                session, seed, project, seed_branch, repo_id=repo.id
            )
        except Exception:
            pr_info = {}
        if pr_info.get("pull_request_url") or pr_info.get("state"):
            pull_request_status = pr_info
        if pr_info.get("pull_request_url") and not pull_request_url:
            pull_request_url = str(pr_info["pull_request_url"])
        if pr_info.get("merge_commit_sha") and not merge_commit_sha:
            merge_commit_sha = str(pr_info["merge_commit_sha"])
        if pr_info.get("merged") and seed.status not in (
            SeedStatus.MERGED,
            SeedStatus.CLOSED,
        ):
            try:
                await reconcile_seed_if_pr_merged_externally(
                    session, seed, project, pr_info=pr_info
                )
                is_merged_seed = seed.status == SeedStatus.MERGED
            except Exception:
                await session.rollback()

    resolved_pr_number: int | None = None
    raw_pr_num = pr_info.get("number")
    if isinstance(raw_pr_num, int) and raw_pr_num > 0:
        resolved_pr_number = raw_pr_num
    elif pull_request_url:
        resolved_pr_number = extract_pr_number_from_url(pull_request_url)

    rest_rate_limited_or_failed = False

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        if provider == "local":
            from gitseed_core.services.local_git import get_local_seed_changes

            local_res = get_local_seed_changes(
                repo,
                default_branch=default_branch,
                seed_branch=seed_branch,
                merge_commit_sha=merge_commit_sha,
                execute_commits=execute_commits,
                base_commit_sha=base_commit_sha,
                is_merged=is_merged_seed,
            )
            branch_exists = bool(local_res["branch_exists"])
            comparison_mode = str(local_res["comparison_mode"])
            if local_res.get("head_commit_sha"):
                head_commit_sha = str(local_res["head_commit_sha"])
            commits.extend(local_res.get("commits") or [])
            files.extend(local_res.get("files") or [])

        elif provider in ("github", "github-enterprise", "github-app"):
            backend_id = backend.id if backend else None
            if is_github_rate_limited(backend_id):
                rest_rate_limited_or_failed = True
            else:
                api_base, headers = _github_api_base_and_headers(endpoint, token)
                enc_base = urllib.parse.quote(default_branch, safe="/")
                enc_head = urllib.parse.quote(seed_branch, safe="/")

                # 1. Try live branch compare: base...gitseed/<project-slug>/seed-<N>
                compare_resp = await client.get(
                    f"{api_base}/repos/{repo.full_name}/compare/{enc_base}...{enc_head}",
                    headers=headers,
                )
                if record_github_rate_limit(backend_id, compare_resp) or compare_resp.status_code >= 500:
                    rest_rate_limited_or_failed = True
                elif 200 <= compare_resp.status_code < 300 and isinstance(
                    compare_resp.json(), dict
                ):
                    data = compare_resp.json()
                    branch_exists = True
                    comparison_mode = "branch"
                    head_ref = seed_branch
                    web_diff_url = (
                        str(data.get("html_url") or "")
                        or f"{web_base}/{repo.full_name}/compare/{default_branch}...{seed_branch}"
                    )
                    for c_item in data.get("commits") or []:
                        if isinstance(c_item, dict):
                            parsed_c = _parse_github_commit_item(
                                c_item, provider, endpoint, repo.full_name
                            )
                            if parsed_c:
                                commits.append(parsed_c)
                    if commits:
                        head_commit_sha = commits[-1]["sha"]
                    for f_item in data.get("files") or []:
                        if isinstance(f_item, dict):
                            parsed_f = _parse_github_file_item(
                                f_item,
                                provider,
                                endpoint,
                                repo.full_name,
                                head_commit_sha or seed_branch,
                            )
                            if parsed_f:
                                files.append(parsed_f)

                # 1b. Fallback to PR commits & files when PR was merged (or branch deleted) so compare has 0 files
                if not files and resolved_pr_number and not is_github_rate_limited(backend_id):
                    pr_commits_resp = await client.get(
                        f"{api_base}/repos/{repo.full_name}/pulls/{resolved_pr_number}/commits",
                        params={"per_page": 100},
                        headers=headers,
                    )
                    if record_github_rate_limit(backend_id, pr_commits_resp):
                        rest_rate_limited_or_failed = True
                    elif 200 <= pr_commits_resp.status_code < 300 and isinstance(
                        pr_commits_resp.json(), list
                    ):
                        commits.clear()
                        for c_item in pr_commits_resp.json():
                            if isinstance(c_item, dict):
                                parsed_c = _parse_github_commit_item(
                                    c_item, provider, endpoint, repo.full_name
                                )
                                if parsed_c:
                                    commits.append(parsed_c)
                        if commits:
                            head_commit_sha = commits[-1]["sha"]

                    if not is_github_rate_limited(backend_id):
                        pr_files_resp = await client.get(
                            f"{api_base}/repos/{repo.full_name}/pulls/{resolved_pr_number}/files",
                            params={"per_page": 100},
                            headers=headers,
                        )
                        if record_github_rate_limit(backend_id, pr_files_resp):
                            rest_rate_limited_or_failed = True
                        elif 200 <= pr_files_resp.status_code < 300 and isinstance(
                            pr_files_resp.json(), list
                        ):
                            file_ref = merge_commit_sha or head_commit_sha or default_branch
                            for f_item in pr_files_resp.json():
                                if isinstance(f_item, dict):
                                    parsed_f = _parse_github_file_item(
                                        f_item,
                                        provider,
                                        endpoint,
                                        repo.full_name,
                                        file_ref,
                                    )
                                    if parsed_f:
                                        files.append(parsed_f)
                            if files:
                                is_merged_pr = bool(
                                    pr_info.get("merged")
                                    or merge_commit_sha
                                    or is_merged_seed
                                )
                                comparison_mode = (
                                    "merged_commit" if is_merged_pr else "branch"
                                )
                                if is_merged_pr and merge_commit_sha:
                                    head_ref = merge_commit_sha[:7]
                                if pull_request_url:
                                    web_diff_url = f"{pull_request_url.rstrip('/')}/files"

                # 2. Fallback when seed branch has been merged & deleted (or only commit SHAs exist)
                if not files and has_code_activity and not is_github_rate_limited(backend_id):
                    target_sha = merge_commit_sha or (
                        execute_commits[-1] if execute_commits else None
                    )
                    if not target_sha:
                        recent_resp = await client.get(
                            f"{api_base}/repos/{repo.full_name}/commits",
                            params={"sha": default_branch, "per_page": 35},
                            headers=headers,
                        )
                        if record_github_rate_limit(backend_id, recent_resp):
                            rest_rate_limited_or_failed = True
                        elif 200 <= recent_resp.status_code < 300 and isinstance(
                            recent_resp.json(), list
                        ):
                            needles: list[str] = [seed_branch.lower()]
                            if project_slug:
                                slug_low = project_slug.lower()
                                needles.extend(
                                    [
                                        f"gitseed/{slug_low}/seed-{seed.number}",
                                        f"{slug_low}#seed-{seed.number}",
                                        f"{slug_low}#{seed.number}",
                                    ]
                                )
                            else:
                                needles.extend(
                                    [
                                        f"gitseed/seed-{seed.number}",
                                        f"seed #{seed.number}",
                                        f"seed-{seed.number}",
                                    ]
                                )
                            for c_item in recent_resp.json():
                                if not isinstance(c_item, dict):
                                    continue
                                c_msg = (
                                    str(
                                        (c_item.get("commit") or {}).get("message") or ""
                                    )
                                    .strip()
                                    .lower()
                                )
                                if any(n in c_msg for n in needles):
                                    found_sha = str(c_item.get("sha") or "").strip()
                                    if found_sha:
                                        target_sha = found_sha
                                        if "merge" in c_msg:
                                            merge_commit_sha = found_sha
                                        break

                    if target_sha and base_commit_sha and base_commit_sha != target_sha and len(execute_commits) > 1 and not is_github_rate_limited(backend_id):
                        range_resp = await client.get(
                            f"{api_base}/repos/{repo.full_name}/compare/{urllib.parse.quote(base_commit_sha, safe='')}...{urllib.parse.quote(target_sha, safe='')}",
                            headers=headers,
                        )
                        if record_github_rate_limit(backend_id, range_resp):
                            rest_rate_limited_or_failed = True
                        elif 200 <= range_resp.status_code < 300 and isinstance(
                            range_resp.json(), dict
                        ):
                            r_data = range_resp.json()
                            for f_item in r_data.get("files") or []:
                                if isinstance(f_item, dict):
                                    parsed_f = _parse_github_file_item(
                                        f_item,
                                        provider,
                                        endpoint,
                                        repo.full_name,
                                        target_sha,
                                    )
                                    if parsed_f:
                                        files.append(parsed_f)
                            if files:
                                commits.clear()
                                for c_item in r_data.get("commits") or []:
                                    if isinstance(c_item, dict):
                                        parsed_c = _parse_github_commit_item(
                                            c_item, provider, endpoint, repo.full_name
                                        )
                                        if parsed_c:
                                            commits.append(parsed_c)
                                comparison_mode = (
                                    "merged_commit"
                                    if (merge_commit_sha or is_merged_seed)
                                    else "execute_commit"
                                )
                                head_commit_sha = target_sha
                                head_ref = target_sha[:7]
                                web_diff_url = str(r_data.get("html_url") or "") or None

                    if not files and target_sha and not is_github_rate_limited(backend_id):
                        commit_resp = await client.get(
                            f"{api_base}/repos/{repo.full_name}/commits/{urllib.parse.quote(target_sha, safe='')}",
                            headers=headers,
                        )
                        if record_github_rate_limit(backend_id, commit_resp):
                            rest_rate_limited_or_failed = True
                        elif 200 <= commit_resp.status_code < 300 and isinstance(
                            commit_resp.json(), dict
                        ):
                            c_data = commit_resp.json()
                            comparison_mode = (
                                "merged_commit"
                                if (merge_commit_sha or str(seed.status) == "merged")
                                else "execute_commit"
                            )
                            head_commit_sha = str(c_data.get("sha") or target_sha)
                            head_ref = head_commit_sha[:7]
                            web_diff_url = (
                                str(c_data.get("html_url") or "")
                                or build_git_web_url(
                                    provider,
                                    endpoint,
                                    repo.full_name,
                                    commit_sha=head_commit_sha,
                                )
                            )
                            parsed_c = _parse_github_commit_item(
                                c_data, provider, endpoint, repo.full_name
                            )
                            if parsed_c:
                                commits.append(parsed_c)
                            for f_item in c_data.get("files") or []:
                                if isinstance(f_item, dict):
                                    parsed_f = _parse_github_file_item(
                                        f_item,
                                        provider,
                                        endpoint,
                                        repo.full_name,
                                        head_commit_sha,
                                    )
                                    if parsed_f:
                                        files.append(parsed_f)

        elif provider == "gitlab":
            api_base, headers = _gitlab_api_base_and_headers(endpoint, token)
            encoded_repo = urllib.parse.quote(repo.full_name, safe="")
            compare_resp = await client.get(
                f"{api_base}/projects/{encoded_repo}/repository/compare",
                params={"from": default_branch, "to": seed_branch},
                headers=headers,
            )
            if compare_resp.status_code in (403, 429) or compare_resp.status_code >= 500:
                rest_rate_limited_or_failed = True

            def _append_gitlab_diffs(raw_diffs: list[Any], ref_for_url: str) -> None:
                for d_item in raw_diffs:
                    if not isinstance(d_item, dict):
                        continue
                    new_path = str(
                        d_item.get("new_path") or d_item.get("old_path") or ""
                    ).strip()
                    if not new_path:
                        continue
                    diff_text = str(d_item.get("diff") or "")
                    adds = sum(
                        1
                        for ln in diff_text.splitlines()
                        if ln.startswith("+") and not ln.startswith("+++")
                    )
                    dels = sum(
                        1
                        for ln in diff_text.splitlines()
                        if ln.startswith("-") and not ln.startswith("---")
                    )
                    status_str = (
                        "added"
                        if d_item.get("new_file")
                        else "removed"
                        if d_item.get("deleted_file")
                        else "renamed"
                        if d_item.get("renamed_file")
                        else "modified"
                    )
                    files.append(
                        {
                            "path": new_path,
                            "previous_path": str(d_item.get("old_path") or "").strip()
                            if d_item.get("renamed_file")
                            else None,
                            "status": status_str,
                            "additions": adds,
                            "deletions": dels,
                            "changes": adds + dels,
                            "patch": diff_text or None,
                            "truncated": False,
                            "web_url": build_git_web_url(
                                provider,
                                endpoint,
                                repo.full_name,
                                ref=ref_for_url,
                                path=new_path,
                                is_dir=False,
                            ),
                        }
                    )

            if 200 <= compare_resp.status_code < 300 and isinstance(
                compare_resp.json(), dict
            ):
                data = compare_resp.json()
                branch_exists = True
                comparison_mode = "branch"
                head_ref = seed_branch
                web_diff_url = f"{web_base}/{repo.full_name}/-/compare/{default_branch}...{seed_branch}"
                for c_item in data.get("commits") or []:
                    if not isinstance(c_item, dict):
                        continue
                    c_sha = str(c_item.get("id") or "").strip()
                    if not c_sha:
                        continue
                    commits.append(
                        {
                            "sha": c_sha,
                            "short_sha": str(c_item.get("short_id") or c_sha[:7]),
                            "message": str(c_item.get("title") or "").strip()
                            or c_sha[:7],
                            "author_name": str(c_item.get("author_name") or "").strip()
                            or None,
                            "committed_at": str(c_item.get("created_at") or "").strip()
                            or None,
                            "web_url": str(c_item.get("web_url") or "")
                            or build_git_web_url(
                                provider, endpoint, repo.full_name, commit_sha=c_sha
                            ),
                        }
                    )
                if commits:
                    head_commit_sha = commits[-1]["sha"]
                _append_gitlab_diffs(
                    data.get("diffs") or [], head_commit_sha or seed_branch
                )

            if not files and resolved_pr_number:
                mr_commits_resp = await client.get(
                    f"{api_base}/projects/{encoded_repo}/merge_requests/{resolved_pr_number}/commits",
                    headers=headers,
                )
                if 200 <= mr_commits_resp.status_code < 300 and isinstance(
                    mr_commits_resp.json(), list
                ):
                    commits.clear()
                    for c_item in mr_commits_resp.json():
                        if not isinstance(c_item, dict):
                            continue
                        c_sha = str(c_item.get("id") or "").strip()
                        if not c_sha:
                            continue
                        commits.append(
                            {
                                "sha": c_sha,
                                "short_sha": str(c_item.get("short_id") or c_sha[:7]),
                                "message": str(c_item.get("title") or "").strip()
                                or c_sha[:7],
                                "author_name": str(c_item.get("author_name") or "").strip()
                                or None,
                                "committed_at": str(c_item.get("created_at") or "").strip()
                                or None,
                                "web_url": str(c_item.get("web_url") or "")
                                or build_git_web_url(
                                    provider, endpoint, repo.full_name, commit_sha=c_sha
                                ),
                            }
                        )
                    if commits:
                        head_commit_sha = commits[-1]["sha"]

                mr_changes_resp = await client.get(
                    f"{api_base}/projects/{encoded_repo}/merge_requests/{resolved_pr_number}/changes",
                    headers=headers,
                )
                if 200 <= mr_changes_resp.status_code < 300 and isinstance(
                    mr_changes_resp.json(), dict
                ):
                    file_ref = merge_commit_sha or head_commit_sha or default_branch
                    _append_gitlab_diffs(
                        mr_changes_resp.json().get("changes") or [], file_ref
                    )
                    if files:
                        is_merged_mr = bool(
                            pr_info.get("merged") or merge_commit_sha or is_merged_seed
                        )
                        comparison_mode = "merged_commit" if is_merged_mr else "branch"
                        if is_merged_mr and merge_commit_sha:
                            head_ref = merge_commit_sha[:7]
                        if pull_request_url:
                            web_diff_url = f"{pull_request_url.rstrip('/')}/diffs"

        elif provider == "bitbucket":
            api_base, headers = _bitbucket_api_base_and_headers(endpoint, token)
            enc_head = urllib.parse.quote(seed_branch, safe="")
            enc_base = urllib.parse.quote(default_branch, safe="")
            spec = f"{enc_head}..{enc_base}"
            diffstat_url = f"{api_base}/repositories/{repo.full_name}/diffstat/{spec}"
            diff_url = f"{api_base}/repositories/{repo.full_name}/diff/{spec}"

            diffstat_resp = await client.get(
                diffstat_url, headers=headers, params={"pagelen": 100}
            )
            if diffstat_resp.status_code in (403, 429) or diffstat_resp.status_code >= 500:
                rest_rate_limited_or_failed = True
            used_pr_fallback = False
            pr_num = (
                pull_request_status.get("number")
                if isinstance(pull_request_status, dict) and pull_request_status.get("number")
                else resolved_pr_number
            )
            initial_values = (
                diffstat_resp.json().get("values")
                if 200 <= diffstat_resp.status_code < 300 and isinstance(diffstat_resp.json(), dict)
                else None
            )
            if (diffstat_resp.status_code == 404 or not initial_values or is_merged_seed) and pr_num:
                diffstat_url = f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_num}/diffstat"
                diff_url = f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_num}/diff"
                diffstat_resp = await client.get(
                    diffstat_url, headers=headers, params={"pagelen": 100}
                )
                used_pr_fallback = True

            diff_resp = await client.get(
                diff_url,
                headers={**headers, "Accept": "text/plain, */*"},
            )
            parsed_patches = (
                _split_unified_diff_by_file(diff_resp.text)
                if 200 <= diff_resp.status_code < 300
                else {}
            )

            if (
                200 <= diffstat_resp.status_code < 300
                or parsed_patches
            ):
                branch_exists = not used_pr_fallback
                comparison_mode = (
                    "merged_commit"
                    if (used_pr_fallback and (merge_commit_sha or str(seed.status) == "merged"))
                    else "branch"
                )
                head_ref = seed_branch
                web_diff_url = (
                    pull_request_url
                    or f"{web_base}/{repo.full_name}/branches/compare/{enc_head}%0D{enc_base}"
                )

                try:
                    commits_url = (
                        f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_num}/commits"
                        if used_pr_fallback and pr_num
                        else f"{api_base}/repositories/{repo.full_name}/commits/{enc_head}"
                    )
                    commits_params: dict[str, Any] = {"pagelen": 100}
                    if not (used_pr_fallback and pr_num):
                        commits_params["exclude"] = default_branch
                    commits_resp = await client.get(
                        commits_url, headers=headers, params=commits_params
                    )
                    if 200 <= commits_resp.status_code < 300 and isinstance(
                        commits_resp.json(), dict
                    ):
                        for c_item in commits_resp.json().get("values") or []:
                            if not isinstance(c_item, dict):
                                continue
                            c_sha = str(c_item.get("hash") or "").strip()
                            if not c_sha:
                                continue
                            raw_msg = str(c_item.get("message") or "").strip()
                            author_raw = (
                                ((c_item.get("author") or {}).get("raw"))
                                if isinstance(c_item.get("author"), dict)
                                else None
                            )
                            author_name = (
                                str(author_raw).split("<", 1)[0].strip()
                                if author_raw
                                else None
                            )
                            html_href = (
                                (((c_item.get("links") or {}).get("html") or {}).get("href"))
                                if isinstance(c_item.get("links"), dict)
                                else None
                            )
                            commits.append(
                                {
                                    "sha": c_sha,
                                    "short_sha": c_sha[:7],
                                    "message": raw_msg.splitlines()[0].strip()
                                    if raw_msg
                                    else c_sha[:7],
                                    "author_name": author_name or None,
                                    "committed_at": str(c_item.get("date") or "").strip()
                                    or None,
                                    "web_url": str(html_href or "")
                                    or build_git_web_url(
                                        provider,
                                        endpoint,
                                        repo.full_name,
                                        commit_sha=c_sha,
                                    ),
                                }
                            )
                        if commits:
                            head_commit_sha = commits[0]["sha"]
                except Exception:
                    pass

                stat_values = (
                    diffstat_resp.json().get("values")
                    if (
                        200 <= diffstat_resp.status_code < 300
                        and isinstance(diffstat_resp.json(), dict)
                    )
                    else []
                )
                if isinstance(stat_values, list):
                    for st_item in stat_values:
                        if not isinstance(st_item, dict):
                            continue
                        new_obj = st_item.get("new") if isinstance(st_item.get("new"), dict) else {}
                        old_obj = st_item.get("old") if isinstance(st_item.get("old"), dict) else {}
                        f_path = str(new_obj.get("path") or old_obj.get("path") or "").strip()
                        if not f_path:
                            continue
                        old_p = str(old_obj.get("path") or "").strip()
                        raw_status = str(st_item.get("status") or "modified").strip().lower()
                        norm_status = (
                            "added"
                            if raw_status == "added"
                            else "removed"
                            if raw_status == "removed"
                            else "renamed"
                            if raw_status in ("renamed", "merge conflict") and old_p and old_p != f_path
                            else "modified"
                        )
                        patch_meta = parsed_patches.pop(f_path, None) or (
                            parsed_patches.pop(old_p, None) if old_p else None
                        )
                        adds = int(
                            st_item.get("lines_added")
                            if st_item.get("lines_added") is not None
                            else (patch_meta["additions"] if patch_meta else 0)
                        )
                        dels = int(
                            st_item.get("lines_removed")
                            if st_item.get("lines_removed") is not None
                            else (patch_meta["deletions"] if patch_meta else 0)
                        )
                        files.append(
                            {
                                "path": f_path,
                                "previous_path": old_p
                                if (norm_status == "renamed" and old_p and old_p != f_path)
                                else (patch_meta["previous_path"] if patch_meta else None),
                                "status": norm_status,
                                "additions": adds,
                                "deletions": dels,
                                "changes": adds + dels,
                                "patch": patch_meta["patch"] if patch_meta else None,
                                "truncated": bool(patch_meta["truncated"]) if patch_meta else False,
                                "web_url": build_git_web_url(
                                    provider,
                                    endpoint,
                                    repo.full_name,
                                    ref=head_commit_sha or seed_branch,
                                    path=f_path,
                                    is_dir=False,
                                ),
                            }
                        )

                for rem_path, rem_meta in parsed_patches.items():
                    adds = int(rem_meta["additions"])
                    dels = int(rem_meta["deletions"])
                    files.append(
                        {
                            "path": rem_path,
                            "previous_path": rem_meta["previous_path"],
                            "status": rem_meta["status"],
                            "additions": adds,
                            "deletions": dels,
                            "changes": adds + dels,
                            "patch": rem_meta["patch"],
                            "truncated": bool(rem_meta["truncated"]),
                            "web_url": build_git_web_url(
                                provider,
                                endpoint,
                                repo.full_name,
                                ref=head_commit_sha or seed_branch,
                                path=rem_path,
                                is_dir=False,
                            ),
                        }
                    )

    # Git Smart HTTP fallback when REST APIs are rate-limited (e.g. GitHub 403) or post-merge refs were cleaned up
    if (
        not files
        and has_code_activity
        and provider != "local"
        and backend is not None
        and token
        and cache_enabled
        and (
            rest_rate_limited_or_failed
            or is_merged_seed
            or bool(execute_commits)
            or bool(merge_commit_sha)
            or bool(resolved_pr_number)
        )
    ):
        try:
            cli_res = await asyncio.to_thread(
                _fetch_remote_seed_changes_via_git_cli,
                backend=backend,
                repo=repo,
                token=token,
                default_branch=default_branch,
                seed_branch=seed_branch,
                pr_number=resolved_pr_number,
                merge_commit_sha=merge_commit_sha,
                execute_commits=execute_commits,
                base_commit_sha=base_commit_sha,
                is_merged=is_merged_seed,
            )
            if cli_res.get("files") or cli_res.get("commits"):
                branch_exists = bool(cli_res.get("branch_exists"))
                comparison_mode = str(cli_res.get("comparison_mode") or comparison_mode)
                if cli_res.get("head_commit_sha"):
                    head_commit_sha = str(cli_res["head_commit_sha"])
                if comparison_mode == "merged_commit" and merge_commit_sha:
                    head_ref = merge_commit_sha[:7]
                elif not branch_exists and head_commit_sha:
                    head_ref = head_commit_sha[:7]
                commits = list(cli_res.get("commits") or [])
                files = list(cli_res.get("files") or [])
                if not web_diff_url:
                    if pull_request_url:
                        web_diff_url = pull_request_url
                    elif branch_exists:
                        web_diff_url = f"{web_base}/{repo.full_name}/compare/{default_branch}...{seed_branch}"
                    elif head_commit_sha:
                        web_diff_url = build_git_web_url(
                            provider, endpoint, repo.full_name, commit_sha=head_commit_sha
                        )
        except Exception as exc:
            logger.debug("Git Smart HTTP diff fallback failed for seed %s: %s", seed.id, type(exc).__name__)

    total_additions = sum(int(f.get("additions") or 0) for f in files)
    total_deletions = sum(int(f.get("deletions") or 0) for f in files)

    if (
        has_code_activity
        and not pull_request_url
        and branch_exists
        and not rest_rate_limited_or_failed
        and project is not None
        and seed.stage in (SeedStage.REVIEW, SeedStage.MERGE)
        and seed.status not in (SeedStatus.MERGED, SeedStatus.CLOSED)
        and is_auto_create_pr_enabled(project)
    ):
        try:
            created_pr = await ensure_seed_pull_request(
                session, seed, project, seed_branch
            )
            if created_pr:
                pull_request_url = created_pr
                refreshed_pr = await check_seed_pull_request_status(
                    session, seed, project, seed_branch, repo_id=repo.id
                )
                if refreshed_pr.get("pull_request_url") or refreshed_pr.get("state"):
                    pull_request_status = refreshed_pr
        except Exception:
            pass

    response_payload = {
        "seed_id": seed.id,
        "seed_number": seed.number,
        "repository_id": repo.id,
        "repository_full_name": repo.full_name,
        "provider": provider,
        "base_ref": default_branch,
        "head_ref": head_ref,
        "comparison_mode": comparison_mode,
        "branch_exists": branch_exists,
        "merge_commit_sha": merge_commit_sha,
        "head_commit_sha": head_commit_sha,
        "web_diff_url": web_diff_url,
        "pull_request_url": pull_request_url,
        "pull_request_status": pull_request_status,
        "blocked_paths_reverted": blocked_paths_reverted,
        "total_commits": len(commits),
        "total_files": len(files),
        "total_additions": total_additions,
        "total_deletions": total_deletions,
        "commits": commits,
        "files": files,
    }
    if cache_enabled and (files or commits):
        ttl = (
            _TERMINAL_CHANGES_CACHE_TTL
            if seed.status in (SeedStatus.MERGED, SeedStatus.CLOSED)
            else _LIVE_CHANGES_CACHE_TTL
        )
        _SEED_CHANGES_CACHE[changes_cache_key] = (
            time.time() + ttl,
            dict(response_payload),
        )
    return response_payload

