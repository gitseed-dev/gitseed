from __future__ import annotations

import logging
import urllib.parse
from typing import Any

import httpx
from gitseed_common.enums import SeedStage, SeedStatus
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

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
    _merge_seed_branch_via_cli,
    check_seed_pull_request_status,
    ensure_seed_pull_request,
    format_seed_branch,
    is_auto_create_pr_enabled,
    merge_seed_branch_into_default,
    reconcile_seed_if_pr_merged_externally,
)
from gitseed_core.services.git_pr_providers import _bitbucket_api_base_and_headers

logger = logging.getLogger("uvicorn.error")

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
    for r in seed_runs:
        if not isinstance(r.output, dict):
            continue
        b_name = str(r.output.get("branch_name") or "").strip()
        if b_name:
            recorded_branch = b_name
        c_sha = str(r.output.get("commit_sha") or "").strip()
        if c_sha and c_sha not in execute_commits:
            execute_commits.append(c_sha)
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

    seed_branch = recorded_branch or canonical_branch
    has_code_activity = (
        bool(execute_commits)
        or bool(merge_commit_sha)
        or bool(recorded_branch)
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
    secrets_map = decrypt_secrets(backend.secrets_encrypted) if backend else {}
    token = await resolve_git_backend_token(
        backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=False,
    )
    default_branch = (repo.default_branch or "main").strip()
    web_base = resolve_git_web_base(provider, endpoint)

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
            except Exception:
                await session.rollback()

    async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
        if provider == "local":
            from gitseed_core.services.local_git import get_local_seed_changes

            local_res = get_local_seed_changes(
                repo,
                default_branch=default_branch,
                seed_branch=seed_branch,
                merge_commit_sha=merge_commit_sha,
                execute_commits=execute_commits,
            )
            branch_exists = bool(local_res["branch_exists"])
            comparison_mode = str(local_res["comparison_mode"])
            if local_res.get("head_commit_sha"):
                head_commit_sha = str(local_res["head_commit_sha"])
            commits.extend(local_res.get("commits") or [])
            files.extend(local_res.get("files") or [])

        elif provider in ("github", "github-enterprise", "github-app"):
            api_base, headers = _github_api_base_and_headers(endpoint, token)
            enc_base = urllib.parse.quote(default_branch, safe="/")
            enc_head = urllib.parse.quote(seed_branch, safe="/")

            # 1. Try live branch compare: base...gitseed/<project-slug>/seed-<N>
            compare_resp = await client.get(
                f"{api_base}/repos/{repo.full_name}/compare/{enc_base}...{enc_head}",
                headers=headers,
            )
            if 200 <= compare_resp.status_code < 300 and isinstance(
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
            pr_number = pr_info.get("number")
            if not files and pr_number:
                pr_commits_resp = await client.get(
                    f"{api_base}/repos/{repo.full_name}/pulls/{pr_number}/commits",
                    params={"per_page": 100},
                    headers=headers,
                )
                if 200 <= pr_commits_resp.status_code < 300 and isinstance(
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

                pr_files_resp = await client.get(
                    f"{api_base}/repos/{repo.full_name}/pulls/{pr_number}/files",
                    params={"per_page": 100},
                    headers=headers,
                )
                if 200 <= pr_files_resp.status_code < 300 and isinstance(
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
                            or seed.status == SeedStatus.MERGED
                        )
                        comparison_mode = (
                            "merged_commit" if is_merged_pr else "branch"
                        )
                        if is_merged_pr and merge_commit_sha:
                            head_ref = merge_commit_sha[:7]
                        if pull_request_url:
                            web_diff_url = f"{pull_request_url.rstrip('/')}/files"

            # 2. Fallback when seed branch has been merged & deleted (or only commit SHAs exist)
            if not files and has_code_activity:
                target_sha = merge_commit_sha or (
                    execute_commits[-1] if execute_commits else None
                )
                if not target_sha:
                    recent_resp = await client.get(
                        f"{api_base}/repos/{repo.full_name}/commits",
                        params={"sha": default_branch, "per_page": 35},
                        headers=headers,
                    )
                    if 200 <= recent_resp.status_code < 300 and isinstance(
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

                if target_sha:
                    commit_resp = await client.get(
                        f"{api_base}/repos/{repo.full_name}/commits/{urllib.parse.quote(target_sha, safe='')}",
                        headers=headers,
                    )
                    if 200 <= commit_resp.status_code < 300 and isinstance(
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
                for d_item in data.get("diffs") or []:
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
                                ref=head_commit_sha or seed_branch,
                                path=new_path,
                                is_dir=False,
                            ),
                        }
                    )

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
            used_pr_fallback = False
            pr_num = pull_request_status.get("number") if isinstance(pull_request_status, dict) else None
            if diffstat_resp.status_code == 404 and pr_num:
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

    total_additions = sum(int(f.get("additions") or 0) for f in files)
    total_deletions = sum(int(f.get("deletions") or 0) for f in files)

    if (
        has_code_activity
        and not pull_request_url
        and branch_exists
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

    return {
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

