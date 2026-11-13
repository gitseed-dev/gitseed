"""Provider-specific Pull Request / Merge Request creation, CI/review inspection, and API merge helpers."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import time
import urllib.parse
from datetime import datetime
from typing import Any

import httpx

from gitseed_core.models.backend import Backend
from gitseed_core.models.project import ProjectRepository
from gitseed_core.services.errors import Conflict
from gitseed_core.services.git_browse import (
    _github_api_base_and_headers,
    _gitlab_api_base_and_headers,
)

logger = logging.getLogger("uvicorn.error")

_GITHUB_RATE_LIMIT_UNTIL: dict[str, float] = {}
_PR_URL_NUMBER_RE = re.compile(
    r"/(?:pull|pulls|merge_requests|pull-requests)/(\d+)(?:[/?#]|$)"
)


def extract_pr_number_from_url(url: str | None) -> int | None:
    """Extract a numeric PR/MR ID from a GitHub, GitLab, or Bitbucket URL."""
    if not url or not isinstance(url, str):
        return None
    m = _PR_URL_NUMBER_RE.search(url.strip())
    if not m:
        return None
    try:
        num = int(m.group(1))
        return num if num > 0 else None
    except ValueError:
        return None


def record_github_rate_limit(backend_id: str | None, resp: httpx.Response) -> bool:
    """Record GitHub primary/secondary rate-limit reset window when HTTP 403/429 occurs."""
    if resp.status_code not in (403, 429):
        return False
    remaining = resp.headers.get("x-ratelimit-remaining", "")
    retry_after = resp.headers.get("retry-after", "")
    is_rl = remaining == "0" or resp.status_code == 429
    if not is_rl:
        try:
            body_text = resp.text[:400].lower()
            if "rate limit" in body_text:
                is_rl = True
        except Exception:
            pass
    if not is_rl:
        return False
    if backend_id and "PYTEST_CURRENT_TEST" not in os.environ:
        now_ts = time.time()
        reset_hdr = resp.headers.get("x-ratelimit-reset", "")
        until_ts = now_ts + 60.0
        if retry_after.isdigit():
            until_ts = max(until_ts, now_ts + min(int(retry_after), 3600))
        elif reset_hdr.isdigit():
            until_ts = max(now_ts + 15.0, min(float(int(reset_hdr)), now_ts + 3600.0))
        _GITHUB_RATE_LIMIT_UNTIL[str(backend_id)] = until_ts
    return True


def is_github_rate_limited(backend_id: str | None) -> bool:
    """Return True if `backend_id` is currently within a recorded GitHub REST rate-limit cooldown."""
    if not backend_id or "PYTEST_CURRENT_TEST" in os.environ:
        return False
    until_ts = _GITHUB_RATE_LIMIT_UNTIL.get(str(backend_id))
    if until_ts is None:
        return False
    if time.time() >= until_ts:
        _GITHUB_RATE_LIMIT_UNTIL.pop(str(backend_id), None)
        return False
    return True


def _bitbucket_api_base_and_headers(
    endpoint: str | None, token: str
) -> tuple[str, dict[str, str]]:
    base = (endpoint or "https://api.bitbucket.org/2.0").strip().rstrip("/")
    if "bitbucket.org" in base and not base.endswith("/2.0"):
        base = "https://api.bitbucket.org/2.0"
    headers: dict[str, str] = {
        "Accept": "application/json",
    }
    if token:
        if ":" in token:
            encoded = base64.b64encode(token.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {encoded}"
        else:
            headers["Authorization"] = f"Bearer {token}"
    return base, headers


def _compute_duration_seconds(
    started_at: str | None, completed_at: str | None
) -> int | None:
    if not started_at or not completed_at:
        return None
    try:
        s_dt = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        c_dt = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
        diff = int((c_dt - s_dt).total_seconds())
        return max(0, diff)
    except Exception:
        return None


async def ensure_provider_pull_request(
    client: httpx.AsyncClient,
    *,
    provider: str,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    seed_branch: str,
    default_branch: str,
    pr_title: str,
    pr_body: str,
    review_comment: str | None = None,
) -> str | None:
    """Create or retrieve an open PR/MR on GitHub, GitLab, or Bitbucket and return its web URL."""
    found_url: str | None = None

    if provider in ("github", "github-enterprise", "github-app"):
        api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
        pr_number: int | None = None
        try:
            create_resp = await client.post(
                f"{api_base}/repos/{repo.full_name}/pulls",
                headers=headers,
                json={
                    "title": pr_title,
                    "head": seed_branch,
                    "base": default_branch,
                    "body": pr_body,
                },
            )
            if create_resp.status_code in (200, 201) and isinstance(
                create_resp.json(), dict
            ):
                pr_data = create_resp.json()
                html_url = str(pr_data.get("html_url") or "").strip()
                if pr_data.get("number"):
                    pr_number = int(pr_data["number"])
                if html_url:
                    found_url = html_url
            elif create_resp.status_code == 422:
                owner = repo.full_name.split("/", 1)[0] if "/" in repo.full_name else ""
                head_filter = f"{owner}:{seed_branch}" if owner else seed_branch
                list_resp = await client.get(
                    f"{api_base}/repos/{repo.full_name}/pulls",
                    headers=headers,
                    params={"state": "open", "head": head_filter},
                )
                if list_resp.status_code == 200 and isinstance(list_resp.json(), list):
                    items = list_resp.json()
                    if items and isinstance(items[0], dict):
                        html_url = str(items[0].get("html_url") or "").strip()
                        if items[0].get("number"):
                            pr_number = int(items[0]["number"])
                        if html_url:
                            found_url = html_url

            if pr_number and review_comment and review_comment.strip():
                await client.post(
                    f"{api_base}/repos/{repo.full_name}/issues/{pr_number}/comments",
                    headers=headers,
                    json={
                        "body": f"### 🤖 GitSeed Automated Review\n\n{review_comment.strip()}"
                    },
                )
        except Exception as exc:
            logger.debug("GitHub PR creation skipped for %s: %s", repo.full_name, exc)

    elif provider == "gitlab":
        api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
        enc_repo = urllib.parse.quote(repo.full_name, safe="")
        mr_iid: int | None = None
        try:
            mr_resp = await client.post(
                f"{api_base}/projects/{enc_repo}/merge_requests",
                headers=headers,
                json={
                    "source_branch": seed_branch,
                    "target_branch": default_branch,
                    "title": pr_title,
                    "description": pr_body,
                    "remove_source_branch": True,
                },
            )
            if mr_resp.status_code in (200, 201) and isinstance(mr_resp.json(), dict):
                mr_data = mr_resp.json()
                web_url = str(mr_data.get("web_url") or "").strip()
                if mr_data.get("iid"):
                    mr_iid = int(mr_data["iid"])
                if web_url:
                    found_url = web_url
            elif mr_resp.status_code == 409:
                list_mr = await client.get(
                    f"{api_base}/projects/{enc_repo}/merge_requests",
                    headers=headers,
                    params={"state": "opened", "source_branch": seed_branch},
                )
                if list_mr.status_code == 200 and isinstance(list_mr.json(), list):
                    items = list_mr.json()
                    if items and isinstance(items[0], dict):
                        web_url = str(items[0].get("web_url") or "").strip()
                        if items[0].get("iid"):
                            mr_iid = int(items[0]["iid"])
                        if web_url:
                            found_url = web_url

            if mr_iid and review_comment and review_comment.strip():
                await client.post(
                    f"{api_base}/projects/{enc_repo}/merge_requests/{mr_iid}/notes",
                    headers=headers,
                    json={
                        "body": f"### 🤖 GitSeed Automated Review\n\n{review_comment.strip()}"
                    },
                )
        except Exception as exc:
            logger.debug("GitLab MR creation skipped for %s: %s", repo.full_name, exc)

    elif provider == "bitbucket":
        api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
        pr_id: int | None = None
        try:
            bb_resp = await client.post(
                f"{api_base}/repositories/{repo.full_name}/pullrequests",
                headers=headers,
                json={
                    "title": pr_title,
                    "description": pr_body,
                    "source": {"branch": {"name": seed_branch}},
                    "destination": {"branch": {"name": default_branch}},
                    "close_source_branch": True,
                },
            )
            if bb_resp.status_code in (200, 201) and isinstance(bb_resp.json(), dict):
                bb_data = bb_resp.json()
                pr_id = int(bb_data["id"]) if bb_data.get("id") else None
                html_url = str(
                    ((bb_data.get("links") or {}).get("html") or {}).get("href") or ""
                ).strip()
                if html_url:
                    found_url = html_url
            elif bb_resp.status_code in (400, 409):
                list_bb = await client.get(
                    f"{api_base}/repositories/{repo.full_name}/pullrequests",
                    headers=headers,
                    params={"state": "OPEN", "q": f'source.branch.name="{seed_branch}"'},
                )
                if list_bb.status_code == 200 and isinstance(list_bb.json(), dict):
                    vals = list_bb.json().get("values") or []
                    if vals and isinstance(vals[0], dict):
                        pr_id = int(vals[0]["id"]) if vals[0].get("id") else None
                        html_url = str(
                            ((vals[0].get("links") or {}).get("html") or {}).get("href") or ""
                        ).strip()
                        if html_url:
                            found_url = html_url

            if pr_id and review_comment and review_comment.strip():
                await client.post(
                    f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_id}/comments",
                    headers=headers,
                    json={
                        "content": {
                            "raw": f"### 🤖 GitSeed Automated Review\n\n{review_comment.strip()}"
                        }
                    },
                )
        except Exception as exc:
            logger.debug("Bitbucket PR creation skipped for %s: %s", repo.full_name, exc)

    return found_url


def _github_pr_targets_branch(pr: dict[str, Any], expected_base: str) -> bool:
    base_obj = pr.get("base") if isinstance(pr.get("base"), dict) else {}
    base_ref = str(base_obj.get("ref") or "").strip()
    return not base_ref or base_ref == expected_base


def _gitlab_mr_targets_branch(mr: dict[str, Any], expected_base: str) -> bool:
    target = str(mr.get("target_branch") or "").strip()
    return not target or target == expected_base


def _bitbucket_pr_targets_branch(pr: dict[str, Any], expected_base: str) -> bool:
    dest = pr.get("destination") if isinstance(pr.get("destination"), dict) else {}
    br = dest.get("branch") if isinstance(dest.get("branch"), dict) else {}
    target = str(br.get("name") or "").strip()
    return not target or target == expected_base


async def inspect_github_pr_status(
    client: httpx.AsyncClient,
    *,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    seed_branch: str,
    pr_number_hint: int | None = None,
) -> dict[str, Any] | None:
    """Inspect a GitHub Pull Request's state, reviews, comments, and CI check-runs/statuses."""
    if is_github_rate_limited(backend.id):
        return None
    api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
    owner = repo.full_name.split("/", 1)[0] if "/" in repo.full_name else ""
    head_filter = f"{owner}:{seed_branch}" if owner else seed_branch
    expected_base = str(repo.default_branch or "main").strip() or "main"
    try:
        pr: dict[str, Any] | None = None
        already_fetched_detail = False
        if pr_number_hint:
            direct_resp = await client.get(
                f"{api_base}/repos/{repo.full_name}/pulls/{pr_number_hint}",
                headers=headers,
            )
            record_github_rate_limit(backend.id, direct_resp)
            if direct_resp.status_code == 200 and isinstance(direct_resp.json(), dict):
                cand = direct_resp.json()
                cand_head = cand.get("head") if isinstance(cand.get("head"), dict) else {}
                cand_head_ref = str(cand_head.get("ref") or "").strip()
                if (not cand_head_ref or cand_head_ref == seed_branch) and _github_pr_targets_branch(
                    cand, expected_base
                ):
                    pr = cand
                    already_fetched_detail = True

        if pr is None:
            if is_github_rate_limited(backend.id):
                return None
            resp = await client.get(
                f"{api_base}/repos/{repo.full_name}/pulls",
                headers=headers,
                params={
                    "state": "all",
                    "head": head_filter,
                    "base": expected_base,
                    "per_page": 5,
                },
            )
            record_github_rate_limit(backend.id, resp)
            if resp.status_code == 200 and isinstance(resp.json(), list):
                pulls = [
                    p
                    for p in resp.json()
                    if isinstance(p, dict) and _github_pr_targets_branch(p, expected_base)
                ]
                if not pulls:
                    fallback_resp = await client.get(
                        f"{api_base}/repos/{repo.full_name}/pulls",
                        headers=headers,
                        params={"state": "all", "base": expected_base, "per_page": 25},
                    )
                    record_github_rate_limit(backend.id, fallback_resp)
                    if fallback_resp.status_code == 200 and isinstance(fallback_resp.json(), list):
                        pulls = [
                            p
                            for p in fallback_resp.json()
                            if isinstance(p, dict)
                            and isinstance(p.get("head"), dict)
                            and str(p["head"].get("ref") or "").strip() == seed_branch
                            and _github_pr_targets_branch(p, expected_base)
                        ]
                if pulls:
                    pr = pulls[0]

        if pr is not None:
            if True:
                pr_number = pr.get("number")
                head_obj = pr.get("head") if isinstance(pr.get("head"), dict) else {}
                head_sha = str(head_obj.get("sha") or "").strip()

                tasks: list[Any] = [
                    asyncio.sleep(0, result=None)
                    if already_fetched_detail
                    else client.get(
                        f"{api_base}/repos/{repo.full_name}/pulls/{pr_number}",
                        headers=headers,
                    ),
                    client.get(
                        f"{api_base}/repos/{repo.full_name}/pulls/{pr_number}/reviews",
                        headers=headers,
                        params={"per_page": 100},
                    ),
                    client.get(
                        f"{api_base}/repos/{repo.full_name}/issues/{pr_number}/comments",
                        headers=headers,
                        params={"per_page": 100},
                    ),
                    client.get(
                        f"{api_base}/repos/{repo.full_name}/pulls/{pr_number}/comments",
                        headers=headers,
                        params={"per_page": 100},
                    ),
                ]
                if head_sha:
                    tasks.extend(
                        [
                            client.get(
                                f"{api_base}/repos/{repo.full_name}/commits/{head_sha}/status",
                                headers=headers,
                            ),
                            client.get(
                                f"{api_base}/repos/{repo.full_name}/commits/{head_sha}/check-runs",
                                headers=headers,
                                params={"per_page": 100},
                            ),
                        ]
                    )
                sub_resps = await asyncio.gather(*tasks, return_exceptions=True)
                detail_resp = sub_resps[0] if len(sub_resps) > 0 else None
                reviews_resp = sub_resps[1] if len(sub_resps) > 1 else None
                issue_comments_resp = sub_resps[2] if len(sub_resps) > 2 else None
                review_comments_resp = sub_resps[3] if len(sub_resps) > 3 else None
                status_resp = sub_resps[4] if len(sub_resps) > 4 else None
                checks_resp = sub_resps[5] if len(sub_resps) > 5 else None

                if (
                    isinstance(detail_resp, httpx.Response)
                    and detail_resp.status_code == 200
                    and isinstance(detail_resp.json(), dict)
                ):
                    pr = {**pr, **detail_resp.json()}

                is_merged = bool(pr.get("merged_at") or pr.get("merged"))
                state_raw = str(pr.get("state") or "open").lower()
                merged_by_obj = pr.get("merged_by") if isinstance(pr.get("merged_by"), dict) else {}
                merged_by = str(merged_by_obj.get("login") or "").strip() or None
                user_obj = pr.get("user") if isinstance(pr.get("user"), dict) else {}
                pr_author = str(user_obj.get("login") or "").strip() or None
                base_obj = pr.get("base") if isinstance(pr.get("base"), dict) else {}
                head_obj_full = pr.get("head") if isinstance(pr.get("head"), dict) else {}
                base_ref_val = str(base_obj.get("ref") or repo.default_branch or "main").strip()
                head_ref_val = str(head_obj_full.get("ref") or seed_branch).strip()
                requested_reviewers: list[str] = []
                for rr in pr.get("requested_reviewers") or []:
                    if isinstance(rr, dict):
                        r_login = str(rr.get("login") or "").strip()
                        if r_login and r_login not in requested_reviewers:
                            requested_reviewers.append(r_login)

                latest_review_by_user: dict[str, str] = {}
                pr_comments: list[dict[str, Any]] = []
                if (
                    isinstance(reviews_resp, httpx.Response)
                    and reviews_resp.status_code == 200
                    and isinstance(reviews_resp.json(), list)
                ):
                    for rev in reviews_resp.json():
                        if not isinstance(rev, dict):
                            continue
                        u_obj = rev.get("user") if isinstance(rev.get("user"), dict) else {}
                        login = str(u_obj.get("login") or "").strip()
                        avatar = str(u_obj.get("avatar_url") or "").strip() or None
                        r_state = str(rev.get("state") or "").upper()
                        if login and r_state in ("APPROVED", "CHANGES_REQUESTED", "DISMISSED"):
                            latest_review_by_user[login] = r_state
                        r_body = str(rev.get("body") or "").strip()
                        if login and (r_body or r_state in ("APPROVED", "CHANGES_REQUESTED")):
                            kind = (
                                "review_approved"
                                if r_state == "APPROVED"
                                else "review_changes_requested"
                                if r_state == "CHANGES_REQUESTED"
                                else "review_comment"
                            )
                            default_label = (
                                "Approved these changes."
                                if r_state == "APPROVED"
                                else "Requested changes."
                                if r_state == "CHANGES_REQUESTED"
                                else ""
                            )
                            pr_comments.append(
                                {
                                    "id": f"review-{rev.get('id')}",
                                    "author": login,
                                    "author_avatar_url": avatar,
                                    "body": r_body or default_label,
                                    "kind": kind,
                                    "path": None,
                                    "line": None,
                                    "diff_hunk": None,
                                    "created_at": str(rev.get("submitted_at") or "").strip() or None,
                                    "updated_at": str(rev.get("submitted_at") or "").strip() or None,
                                    "web_url": str(rev.get("html_url") or "").strip() or None,
                                }
                            )
                approved_by = [u for u, st in latest_review_by_user.items() if st == "APPROVED"]
                changes_requested_by = [
                    u for u, st in latest_review_by_user.items() if st == "CHANGES_REQUESTED"
                ]

                if (
                    isinstance(issue_comments_resp, httpx.Response)
                    and issue_comments_resp.status_code == 200
                    and isinstance(issue_comments_resp.json(), list)
                ):
                    for ic in issue_comments_resp.json():
                        if not isinstance(ic, dict):
                            continue
                        u_obj = ic.get("user") if isinstance(ic.get("user"), dict) else {}
                        login = str(u_obj.get("login") or "user").strip()
                        avatar = str(u_obj.get("avatar_url") or "").strip() or None
                        c_body = str(ic.get("body") or "").strip()
                        if not c_body:
                            continue
                        pr_comments.append(
                            {
                                "id": f"issue-comment-{ic.get('id')}",
                                "author": login,
                                "author_avatar_url": avatar,
                                "body": c_body,
                                "kind": "comment",
                                "path": None,
                                "line": None,
                                "diff_hunk": None,
                                "created_at": str(ic.get("created_at") or "").strip() or None,
                                "updated_at": str(ic.get("updated_at") or "").strip() or None,
                                "web_url": str(ic.get("html_url") or "").strip() or None,
                            }
                        )

                if (
                    isinstance(review_comments_resp, httpx.Response)
                    and review_comments_resp.status_code == 200
                    and isinstance(review_comments_resp.json(), list)
                ):
                    for rc in review_comments_resp.json():
                        if not isinstance(rc, dict):
                            continue
                        u_obj = rc.get("user") if isinstance(rc.get("user"), dict) else {}
                        login = str(u_obj.get("login") or "user").strip()
                        avatar = str(u_obj.get("avatar_url") or "").strip() or None
                        c_body = str(rc.get("body") or "").strip()
                        if not c_body:
                            continue
                        line_num = rc.get("line") or rc.get("original_line")
                        pr_comments.append(
                            {
                                "id": f"diff-comment-{rc.get('id')}",
                                "author": login,
                                "author_avatar_url": avatar,
                                "body": c_body,
                                "kind": "inline_comment",
                                "path": str(rc.get("path") or "").strip() or None,
                                "line": int(line_num) if isinstance(line_num, int) else None,
                                "diff_hunk": str(rc.get("diff_hunk") or "").strip() or None,
                                "created_at": str(rc.get("created_at") or "").strip() or None,
                                "updated_at": str(rc.get("updated_at") or "").strip() or None,
                                "web_url": str(rc.get("html_url") or "").strip() or None,
                            }
                        )

                pr_comments.sort(key=lambda item: str(item.get("created_at") or ""))

                passing_count = 0
                pending_count = 0
                failing_count = 0
                failing_checks: list[str] = []
                check_items: list[dict[str, Any]] = []

                if (
                    isinstance(checks_resp, httpx.Response)
                    and checks_resp.status_code == 200
                    and isinstance(checks_resp.json(), dict)
                ):
                    check_runs = checks_resp.json().get("check_runs")
                    if isinstance(check_runs, list):
                        latest_checks: dict[str, dict[str, Any]] = {}
                        for cr in check_runs:
                            if isinstance(cr, dict):
                                c_name = str(cr.get("name") or cr.get("id") or "")
                                latest_checks[c_name] = cr
                        for cr in latest_checks.values():
                            c_status = str(cr.get("status") or "").lower()
                            c_conclusion = str(cr.get("conclusion") or "").lower()
                            c_name = str(cr.get("name") or "check").strip()
                            c_url = str(cr.get("html_url") or cr.get("details_url") or "").strip() or None
                            app_obj = cr.get("app") if isinstance(cr.get("app"), dict) else {}
                            workflow_label = str(app_obj.get("name") or "").strip() or None
                            out_obj = cr.get("output") if isinstance(cr.get("output"), dict) else {}
                            out_title = str(out_obj.get("title") or "").strip()
                            out_summary = str(out_obj.get("summary") or "").strip()
                            detail_bits = [b for b in (out_title, out_summary[:400]) if b]
                            summary_text = " — ".join(detail_bits) if detail_bits else None
                            started_at_str = str(cr.get("started_at") or "").strip() or None
                            completed_at_str = str(cr.get("completed_at") or "").strip() or None

                            if c_status != "completed":
                                pending_count += 1
                                norm_check_status = "running" if c_status == "in_progress" else "queued"
                            elif c_conclusion in ("success", "neutral", "skipped"):
                                passing_count += 1
                                norm_check_status = (
                                    "skipped" if c_conclusion in ("neutral", "skipped") else "passed"
                                )
                            else:
                                failing_count += 1
                                norm_check_status = (
                                    "cancelled" if c_conclusion == "cancelled" else "failed"
                                )
                                detail_str = f" — {' | '.join(detail_bits)}" if detail_bits else ""
                                url_str = f" ({c_url})" if c_url else ""
                                failing_checks.append(
                                    f"Check `{c_name}` ({c_conclusion or 'failed'}){detail_str}{url_str}"
                                )

                            check_items.append(
                                {
                                    "id": f"check-{cr.get('id') or c_name}",
                                    "name": c_name,
                                    "stage_or_workflow": workflow_label,
                                    "status": norm_check_status,
                                    "conclusion": c_conclusion or None,
                                    "started_at": started_at_str,
                                    "completed_at": completed_at_str,
                                    "duration_seconds": _compute_duration_seconds(
                                        started_at_str, completed_at_str
                                    ),
                                    "summary": summary_text,
                                    "details_url": c_url,
                                }
                            )

                if (
                    isinstance(status_resp, httpx.Response)
                    and status_resp.status_code == 200
                    and isinstance(status_resp.json(), dict)
                ):
                    statuses = status_resp.json().get("statuses")
                    if isinstance(statuses, list):
                        seen_contexts: dict[str, dict[str, Any]] = {}
                        for st_item in statuses:
                            if isinstance(st_item, dict):
                                ctx = str(st_item.get("context") or "default")
                                if ctx not in seen_contexts:
                                    seen_contexts[ctx] = st_item
                        for ctx, st_obj in seen_contexts.items():
                            st_val = str(st_obj.get("state") or "").lower()
                            st_desc = str(st_obj.get("description") or "").strip() or None
                            st_url = str(st_obj.get("target_url") or "").strip() or None
                            if st_val == "pending":
                                pending_count += 1
                                norm_st = "running"
                            elif st_val == "success":
                                passing_count += 1
                                norm_st = "passed"
                            elif st_val in ("failure", "error"):
                                failing_count += 1
                                norm_st = "failed"
                                desc_part = f" — {st_desc}" if st_desc else ""
                                url_part = f" ({st_url})" if st_url else ""
                                failing_checks.append(
                                    f"Status `{ctx}` ({st_val}){desc_part}{url_part}"
                                )
                            else:
                                norm_st = "queued"
                            check_items.append(
                                {
                                    "id": f"status-{st_obj.get('id') or ctx}",
                                    "name": ctx,
                                    "stage_or_workflow": "Status Check",
                                    "status": norm_st,
                                    "conclusion": st_val or None,
                                    "started_at": str(st_obj.get("created_at") or "").strip() or None,
                                    "completed_at": str(st_obj.get("updated_at") or "").strip() or None,
                                    "duration_seconds": None,
                                    "summary": st_desc,
                                    "details_url": st_url,
                                }
                            )

                total_checks = passing_count + pending_count + failing_count
                if total_checks == 0:
                    ci_status = "none"
                    ci_summary = None
                elif failing_count > 0:
                    ci_status = "failing"
                    ci_summary = f"{failing_count}/{total_checks} check{'s' if total_checks != 1 else ''} failing"
                elif pending_count > 0:
                    ci_status = "pending"
                    ci_summary = f"{pending_count}/{total_checks} check{'s' if total_checks != 1 else ''} running"
                else:
                    ci_status = "passing"
                    ci_summary = f"{passing_count}/{total_checks} check{'s' if total_checks != 1 else ''} passing"

                mergeable_state = str(pr.get("mergeable_state") or "").lower() or None
                has_conflicts = bool(mergeable_state == "dirty" or pr.get("mergeable") is False)
                if changes_requested_by:
                    review_status = "changes_requested"
                elif mergeable_state == "blocked" and ci_status in ("none", "passing"):
                    review_status = "review_required"
                elif approved_by:
                    review_status = "approved"
                else:
                    review_status = "none"

                ready_to_merge = True
                block_reason: str | None = None
                if not is_merged and state_raw == "open":
                    if bool(pr.get("draft")):
                        ready_to_merge = False
                        block_reason = f"GitHub PR #{pr_number} is still marked as Draft."
                    elif ci_status == "pending":
                        ready_to_merge = False
                        block_reason = (
                            f"CI pipeline is still running on GitHub PR #{pr_number} ({ci_summary})."
                        )
                    elif ci_status == "failing":
                        ready_to_merge = False
                        block_reason = (
                            f"CI pipeline is failing on GitHub PR #{pr_number} ({ci_summary})."
                        )
                    elif review_status == "changes_requested":
                        ready_to_merge = False
                        reviewers = ", ".join(f"@{u}" for u in changes_requested_by)
                        block_reason = (
                            f"Changes were requested on GitHub PR #{pr_number} by {reviewers}."
                        )
                    elif has_conflicts:
                        ready_to_merge = False
                        block_reason = (
                            f"GitHub PR #{pr_number} has merge conflicts with the base branch."
                        )
                    elif mergeable_state == "blocked" or review_status == "review_required":
                        ready_to_merge = False
                        block_reason = (
                            f"GitHub PR #{pr_number} requires approving reviews or branch protection rules to be satisfied in GitHub."
                        )

                return {
                    "pull_request_url": str(pr.get("html_url") or "").strip() or None,
                    "number": int(pr_number) if pr_number is not None else None,
                    "repository_full_name": repo.full_name,
                    "provider": "github",
                    "state": "merged" if is_merged else state_raw,
                    "merged": is_merged,
                    "title": str(pr.get("title") or "").strip() or None,
                    "body": str(pr.get("body") or "").strip() or None,
                    "draft": bool(pr.get("draft")),
                    "author": pr_author,
                    "base_ref": base_ref_val,
                    "head_ref": head_ref_val,
                    "head_sha": head_sha or None,
                    "created_at": str(pr.get("created_at") or "").strip() or None,
                    "updated_at": str(pr.get("updated_at") or "").strip() or None,
                    "merged_at": str(pr.get("merged_at") or "").strip() or None,
                    "merge_commit_sha": str(pr.get("merge_commit_sha") or "").strip() or None,
                    "merged_by": merged_by,
                    "has_conflicts": has_conflicts,
                    "approved_by": approved_by,
                    "changes_requested_by": changes_requested_by,
                    "requested_reviewers": requested_reviewers,
                    "ci_status": ci_status,
                    "ci_summary": ci_summary,
                    "failing_checks": failing_checks,
                    "checks": check_items,
                    "comments": pr_comments,
                    "review_status": review_status,
                    "mergeable_state": mergeable_state,
                    "ready_to_merge": ready_to_merge,
                    "block_reason": block_reason,
                }
    except Exception as exc:
        logger.debug("GitHub PR status check failed for %s: %s", repo.full_name, exc)
    return None


async def inspect_gitlab_mr_status(
    client: httpx.AsyncClient,
    *,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    seed_branch: str,
) -> dict[str, Any] | None:
    """Inspect a GitLab Merge Request's state, approvals, notes, and pipeline jobs."""
    api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
    enc_repo = urllib.parse.quote(repo.full_name, safe="")
    expected_base = str(repo.default_branch or "main").strip() or "main"
    try:
        resp = await client.get(
            f"{api_base}/projects/{enc_repo}/merge_requests",
            headers=headers,
            params={
                "state": "all",
                "source_branch": seed_branch,
                "target_branch": expected_base,
                "per_page": 5,
            },
        )
        if resp.status_code == 200 and isinstance(resp.json(), list):
            mrs = [
                m
                for m in resp.json()
                if isinstance(m, dict) and _gitlab_mr_targets_branch(m, expected_base)
            ]
            if mrs:
                mr = mrs[0]
                mr_iid = mr.get("iid")
                sub_resps = await asyncio.gather(
                    client.get(
                        f"{api_base}/projects/{enc_repo}/merge_requests/{mr_iid}",
                        headers=headers,
                    ),
                    client.get(
                        f"{api_base}/projects/{enc_repo}/merge_requests/{mr_iid}/approvals",
                        headers=headers,
                    ),
                    client.get(
                        f"{api_base}/projects/{enc_repo}/merge_requests/{mr_iid}/notes",
                        headers=headers,
                        params={"sort": "asc", "per_page": 100},
                    ),
                    return_exceptions=True,
                )
                detail_resp = sub_resps[0] if len(sub_resps) > 0 else None
                approvals_resp = sub_resps[1] if len(sub_resps) > 1 else None
                notes_resp = sub_resps[2] if len(sub_resps) > 2 else None
                if (
                    isinstance(detail_resp, httpx.Response)
                    and detail_resp.status_code == 200
                    and isinstance(detail_resp.json(), dict)
                ):
                    mr = {**mr, **detail_resp.json()}

                state_raw = str(mr.get("state") or "opened").lower()
                is_merged = state_raw == "merged" or bool(mr.get("merged_at"))
                merged_by_obj = (
                    mr.get("merged_by")
                    if isinstance(mr.get("merged_by"), dict)
                    else (mr.get("merge_user") if isinstance(mr.get("merge_user"), dict) else {})
                )
                merged_by = str(merged_by_obj.get("username") or "").strip() or None
                author_obj = mr.get("author") if isinstance(mr.get("author"), dict) else {}
                mr_author = str(author_obj.get("username") or "").strip() or None
                requested_reviewers_gl: list[str] = []
                for rv in mr.get("reviewers") or []:
                    if isinstance(rv, dict):
                        rv_u = str(rv.get("username") or "").strip()
                        if rv_u and rv_u not in requested_reviewers_gl:
                            requested_reviewers_gl.append(rv_u)

                approved_by: list[str] = []
                approvals_left = 0
                if (
                    isinstance(approvals_resp, httpx.Response)
                    and approvals_resp.status_code == 200
                    and isinstance(approvals_resp.json(), dict)
                ):
                    app_json = approvals_resp.json()
                    approvals_left = int(app_json.get("approvals_left") or 0)
                    for item in app_json.get("approved_by") or []:
                        if isinstance(item, dict):
                            u_obj = item.get("user") if isinstance(item.get("user"), dict) else item
                            uname = str(u_obj.get("username") or "").strip()
                            if uname and uname not in approved_by:
                                approved_by.append(uname)

                mr_comments: list[dict[str, Any]] = []
                if (
                    isinstance(notes_resp, httpx.Response)
                    and notes_resp.status_code == 200
                    and isinstance(notes_resp.json(), list)
                ):
                    for note in notes_resp.json():
                        if not isinstance(note, dict) or bool(note.get("system")):
                            continue
                        n_body = str(note.get("body") or "").strip()
                        if not n_body:
                            continue
                        n_author_obj = note.get("author") if isinstance(note.get("author"), dict) else {}
                        n_author = str(n_author_obj.get("username") or n_author_obj.get("name") or "user").strip()
                        n_avatar = str(n_author_obj.get("avatar_url") or "").strip() or None
                        pos_obj = note.get("position") if isinstance(note.get("position"), dict) else None
                        n_path = str(pos_obj.get("new_path") or pos_obj.get("old_path") or "").strip() or None if pos_obj else None
                        n_line = pos_obj.get("new_line") or pos_obj.get("old_line") if pos_obj else None
                        mr_comments.append(
                            {
                                "id": f"gl-note-{note.get('id')}",
                                "author": n_author,
                                "author_avatar_url": n_avatar,
                                "body": n_body,
                                "kind": "inline_comment" if n_path else "comment",
                                "path": n_path,
                                "line": int(n_line) if isinstance(n_line, int) else None,
                                "diff_hunk": None,
                                "created_at": str(note.get("created_at") or "").strip() or None,
                                "updated_at": str(note.get("updated_at") or "").strip() or None,
                                "web_url": None,
                            }
                        )

                pipeline = (
                    mr.get("head_pipeline")
                    if isinstance(mr.get("head_pipeline"), dict)
                    else (mr.get("pipeline") if isinstance(mr.get("pipeline"), dict) else None)
                )
                failing_checks_gl: list[str] = []
                checks_gl: list[dict[str, Any]] = []
                head_sha_gl = str(
                    (pipeline.get("sha") if isinstance(pipeline, dict) else None)
                    or mr.get("sha")
                    or ""
                ).strip()
                if pipeline is None:
                    ci_status = "none"
                    ci_summary = None
                else:
                    p_status = str(pipeline.get("status") or "").lower()
                    p_id = pipeline.get("id")
                    p_label = f"Pipeline #{p_id}" if p_id else "Pipeline"
                    if p_status in (
                        "running",
                        "pending",
                        "created",
                        "waiting_for_resource",
                        "preparing",
                        "scheduled",
                        "manual",
                    ):
                        ci_status = "pending"
                        ci_summary = f"{p_label} {p_status}"
                    elif p_status in ("success", "skipped"):
                        ci_status = "passing"
                        ci_summary = f"{p_label} passed"
                    else:
                        ci_status = "failing"
                        ci_summary = f"{p_label} {p_status or 'failed'}"

                    if p_id:
                        try:
                            jobs_resp = await client.get(
                                f"{api_base}/projects/{enc_repo}/pipelines/{p_id}/jobs",
                                headers=headers,
                                params={"per_page": 100},
                            )
                            if jobs_resp.status_code == 200 and isinstance(jobs_resp.json(), list):
                                failed_trace_fetched = 0
                                for job in jobs_resp.json():
                                    if not isinstance(job, dict):
                                        continue
                                    j_id = job.get("id")
                                    j_name = str(job.get("name") or "job").strip()
                                    j_stage = str(job.get("stage") or "").strip() or None
                                    j_status_raw = str(job.get("status") or "").lower()
                                    j_reason = str(job.get("failure_reason") or "").strip()
                                    j_url = str(job.get("web_url") or "").strip() or None
                                    j_started = str(job.get("started_at") or "").strip() or None
                                    j_finished = str(job.get("finished_at") or "").strip() or None
                                    j_dur_raw = job.get("duration")
                                    j_dur = (
                                        int(float(j_dur_raw))
                                        if isinstance(j_dur_raw, (int, float))
                                        else _compute_duration_seconds(j_started, j_finished)
                                    )

                                    if j_status_raw == "running":
                                        norm_j_status = "running"
                                    elif j_status_raw in (
                                        "pending",
                                        "created",
                                        "waiting_for_resource",
                                        "preparing",
                                        "scheduled",
                                    ):
                                        norm_j_status = "queued"
                                    elif j_status_raw == "success":
                                        norm_j_status = "passed"
                                    elif j_status_raw in ("skipped", "manual"):
                                        norm_j_status = "skipped"
                                    elif j_status_raw == "canceled":
                                        norm_j_status = "cancelled"
                                    else:
                                        norm_j_status = "failed"

                                    summary_excerpt: str | None = j_reason or None
                                    if norm_j_status == "failed":
                                        trace_excerpt = ""
                                        if j_id and failed_trace_fetched < 6:
                                            failed_trace_fetched += 1
                                            t_resp = await client.get(
                                                f"{api_base}/projects/{enc_repo}/jobs/{j_id}/trace",
                                                headers=headers,
                                            )
                                            if t_resp.status_code == 200 and t_resp.text:
                                                tail_lines = [
                                                    ln.strip()
                                                    for ln in t_resp.text.splitlines()
                                                    if ln.strip()
                                                ][-10:]
                                                if tail_lines:
                                                    joined_tail = " / ".join(tail_lines)[:400]
                                                    trace_excerpt = f" | Tail: {joined_tail}"
                                                    summary_excerpt = (
                                                        f"{j_reason} — {joined_tail}"
                                                        if j_reason
                                                        else joined_tail
                                                    )
                                        stage_part = f" [{j_stage}]" if j_stage else ""
                                        reason_part = f" ({j_reason})" if j_reason else ""
                                        url_part = f" ({j_url})" if j_url else ""
                                        failing_checks_gl.append(
                                            f"Job `{j_name}`{stage_part} failed{reason_part}{trace_excerpt}{url_part}"
                                        )

                                    checks_gl.append(
                                        {
                                            "id": f"gl-job-{j_id or j_name}",
                                            "name": j_name,
                                            "stage_or_workflow": j_stage,
                                            "status": norm_j_status,
                                            "conclusion": j_status_raw or None,
                                            "started_at": j_started,
                                            "completed_at": j_finished,
                                            "duration_seconds": j_dur,
                                            "summary": summary_excerpt,
                                            "details_url": j_url,
                                        }
                                    )
                        except Exception:
                            pass

                detailed_status = (
                    str(mr.get("detailed_merge_status") or mr.get("merge_status") or "").lower()
                    or None
                )
                has_conflicts_gl = bool(
                    mr.get("has_conflicts") or detailed_status in ("conflict", "cannot_be_merged")
                )
                is_draft_gl = bool(
                    mr.get("draft") or mr.get("work_in_progress") or detailed_status == "draft_status"
                )
                if approvals_left > 0 or detailed_status == "not_approved":
                    review_status = "review_required"
                elif approved_by:
                    review_status = "approved"
                else:
                    review_status = "none"

                ready_to_merge = True
                block_reason = None
                norm_state = "merged" if is_merged else ("open" if state_raw == "opened" else state_raw)
                if not is_merged and norm_state == "open":
                    if is_draft_gl:
                        ready_to_merge = False
                        block_reason = f"GitLab MR !{mr_iid} is still marked as Draft."
                    elif ci_status == "pending" or detailed_status == "ci_still_running":
                        ready_to_merge = False
                        block_reason = (
                            f"CI pipeline is still running on GitLab MR !{mr_iid} ({ci_summary or 'pipeline running'})."
                        )
                    elif ci_status == "failing" or detailed_status == "ci_must_pass":
                        ready_to_merge = False
                        block_reason = (
                            f"CI pipeline is failing on GitLab MR !{mr_iid} ({ci_summary or 'pipeline failed'})."
                        )
                    elif review_status == "review_required":
                        ready_to_merge = False
                        block_reason = (
                            f"GitLab MR !{mr_iid} requires {approvals_left or 'additional'} approval(s) in GitLab before merging."
                        )
                    elif has_conflicts_gl:
                        ready_to_merge = False
                        block_reason = (
                            f"GitLab MR !{mr_iid} has merge conflicts with the target branch."
                        )
                    elif detailed_status == "discussions_not_resolved" or mr.get("blocking_discussions_resolved") is False:
                        ready_to_merge = False
                        block_reason = (
                            f"GitLab MR !{mr_iid} has unresolved blocking discussions."
                        )

                return {
                    "pull_request_url": str(mr.get("web_url") or "").strip() or None,
                    "number": int(mr_iid) if mr_iid is not None else None,
                    "repository_full_name": repo.full_name,
                    "provider": "gitlab",
                    "state": norm_state,
                    "merged": is_merged,
                    "title": str(mr.get("title") or "").strip() or None,
                    "body": str(mr.get("description") or "").strip() or None,
                    "draft": is_draft_gl,
                    "author": mr_author,
                    "base_ref": str(mr.get("target_branch") or repo.default_branch or "main").strip(),
                    "head_ref": str(mr.get("source_branch") or seed_branch).strip(),
                    "head_sha": head_sha_gl or None,
                    "created_at": str(mr.get("created_at") or "").strip() or None,
                    "updated_at": str(mr.get("updated_at") or "").strip() or None,
                    "merged_at": str(mr.get("merged_at") or "").strip() or None,
                    "merge_commit_sha": str(mr.get("merge_commit_sha") or mr.get("sha") or "").strip() or None,
                    "merged_by": merged_by,
                    "has_conflicts": has_conflicts_gl,
                    "approved_by": approved_by,
                    "changes_requested_by": [],
                    "requested_reviewers": requested_reviewers_gl,
                    "ci_status": ci_status,
                    "ci_summary": ci_summary,
                    "failing_checks": failing_checks_gl,
                    "checks": checks_gl,
                    "comments": mr_comments,
                    "review_status": review_status,
                    "mergeable_state": detailed_status,
                    "ready_to_merge": ready_to_merge,
                    "block_reason": block_reason,
                }
    except Exception as exc:
        logger.debug("GitLab MR status check failed for %s: %s", repo.full_name, exc)
    return None


async def inspect_bitbucket_pr_status(
    client: httpx.AsyncClient,
    *,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    seed_branch: str,
) -> dict[str, Any] | None:
    """Inspect a Bitbucket Pull Request's state, participants, comments, and commit build statuses."""
    api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
    expected_base = str(repo.default_branch or "main").strip() or "main"
    try:
        resp = await client.get(
            f"{api_base}/repositories/{repo.full_name}/pullrequests",
            headers=headers,
            params={
                "state": "OPEN,MERGED,DECLINED",
                "q": f'source.branch.name="{seed_branch}" AND destination.branch.name="{expected_base}"',
                "pagelen": 5,
            },
        )
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            vals = [
                p
                for p in (resp.json().get("values") or [])
                if isinstance(p, dict) and _bitbucket_pr_targets_branch(p, expected_base)
            ]
            if vals:
                bb_pr = vals[0]
                pr_id = bb_pr.get("id")
                state_upper = str(bb_pr.get("state") or "OPEN").upper()
                is_merged = state_upper == "MERGED"
                norm_state = (
                    "merged"
                    if is_merged
                    else ("open" if state_upper == "OPEN" else "closed")
                )
                src_commit = (
                    ((bb_pr.get("source") or {}).get("commit") or {}).get("hash")
                )
                head_sha_bb = str(src_commit or "").strip()
                merge_sha_bb = str(
                    ((bb_pr.get("merge_commit") or {}).get("hash")) or ""
                ).strip() or None
                closed_by_obj = (
                    bb_pr.get("closed_by")
                    if isinstance(bb_pr.get("closed_by"), dict)
                    else {}
                )
                merged_by = (
                    str(
                        closed_by_obj.get("nickname")
                        or closed_by_obj.get("display_name")
                        or ""
                    ).strip()
                    or None
                )
                bb_author_obj = bb_pr.get("author") if isinstance(bb_pr.get("author"), dict) else {}
                bb_author = (
                    str(
                        bb_author_obj.get("nickname")
                        or bb_author_obj.get("display_name")
                        or ""
                    ).strip()
                    or None
                )
                approved_by_bb: list[str] = []
                changes_req_bb: list[str] = []
                for part in bb_pr.get("participants") or []:
                    if not isinstance(part, dict):
                        continue
                    u_obj = part.get("user") if isinstance(part.get("user"), dict) else {}
                    uname = str(
                        u_obj.get("nickname") or u_obj.get("display_name") or ""
                    ).strip()
                    p_state = str(part.get("state") or "").lower()
                    if part.get("approved") is True or p_state == "approved":
                        if uname and uname not in approved_by_bb:
                            approved_by_bb.append(uname)
                    elif p_state == "changes_requested":
                        if uname and uname not in changes_req_bb:
                            changes_req_bb.append(uname)

                ci_status = "none"
                ci_summary = None
                failing_checks_bb: list[str] = []
                checks_bb: list[dict[str, Any]] = []
                if head_sha_bb:
                    st_resp = await client.get(
                        f"{api_base}/repositories/{repo.full_name}/commit/{head_sha_bb}/statuses",
                        headers=headers,
                        params={"pagelen": 50},
                    )
                    if st_resp.status_code == 200 and isinstance(st_resp.json(), dict):
                        st_vals = [
                            s
                            for s in (st_resp.json().get("values") or [])
                            if isinstance(s, dict)
                        ]
                        pass_c = 0
                        pend_c = 0
                        fail_c = 0
                        for st_item in st_vals:
                            b_state = str(st_item.get("state") or "").upper()
                            b_name = str(
                                st_item.get("name") or st_item.get("key") or "build"
                            ).strip()
                            b_desc = str(st_item.get("description") or "").strip() or None
                            b_url = str(st_item.get("url") or "").strip() or None
                            if b_state == "INPROGRESS":
                                pend_c += 1
                                norm_b_st = "running"
                            elif b_state == "SUCCESSFUL":
                                pass_c += 1
                                norm_b_st = "passed"
                            elif b_state in ("FAILED", "STOPPED"):
                                fail_c += 1
                                norm_b_st = "cancelled" if b_state == "STOPPED" else "failed"
                                failing_checks_bb.append(
                                    f"Build `{b_name}` ({b_state.lower()})"
                                    + (f" — {b_desc}" if b_desc else "")
                                    + (f" ({b_url})" if b_url else "")
                                )
                            else:
                                norm_b_st = "queued"
                            checks_bb.append(
                                {
                                    "id": f"bb-build-{st_item.get('key') or b_name}",
                                    "name": b_name,
                                    "stage_or_workflow": "Pipeline",
                                    "status": norm_b_st,
                                    "conclusion": b_state.lower() or None,
                                    "started_at": str(st_item.get("created_on") or "").strip() or None,
                                    "completed_at": str(st_item.get("updated_on") or "").strip() or None,
                                    "duration_seconds": _compute_duration_seconds(
                                        str(st_item.get("created_on") or "").strip() or None,
                                        str(st_item.get("updated_on") or "").strip() or None,
                                    ),
                                    "summary": b_desc,
                                    "details_url": b_url,
                                }
                            )
                        tot_c = pass_c + pend_c + fail_c
                        if tot_c > 0:
                            if fail_c > 0:
                                ci_status = "failing"
                                ci_summary = f"{fail_c}/{tot_c} build{'s' if tot_c != 1 else ''} failing"
                            elif pend_c > 0:
                                ci_status = "pending"
                                ci_summary = f"{pend_c}/{tot_c} build{'s' if tot_c != 1 else ''} running"
                            else:
                                ci_status = "passing"
                                ci_summary = f"{pass_c}/{tot_c} build{'s' if tot_c != 1 else ''} passing"

                comments_bb: list[dict[str, Any]] = []
                if pr_id:
                    try:
                        c_resp = await client.get(
                            f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_id}/comments",
                            headers=headers,
                            params={"pagelen": 50},
                        )
                        if c_resp.status_code == 200 and isinstance(c_resp.json(), dict):
                            for c_item in c_resp.json().get("values") or []:
                                if not isinstance(c_item, dict) or c_item.get("deleted"):
                                    continue
                                raw_txt = str(
                                    ((c_item.get("content") or {}).get("raw")) or ""
                                ).strip()
                                if not raw_txt:
                                    continue
                                cu_obj = c_item.get("user") if isinstance(c_item.get("user"), dict) else {}
                                cu_name = str(
                                    cu_obj.get("nickname") or cu_obj.get("display_name") or "user"
                                ).strip()
                                inline_obj = c_item.get("inline") if isinstance(c_item.get("inline"), dict) else None
                                c_path = str(inline_obj.get("path") or "").strip() or None if inline_obj else None
                                c_line = inline_obj.get("to") or inline_obj.get("from") if inline_obj else None
                                comments_bb.append(
                                    {
                                        "id": f"bb-comment-{c_item.get('id')}",
                                        "author": cu_name,
                                        "author_avatar_url": None,
                                        "body": raw_txt,
                                        "kind": "inline_comment" if c_path else "comment",
                                        "path": c_path,
                                        "line": int(c_line) if isinstance(c_line, int) else None,
                                        "diff_hunk": None,
                                        "created_at": str(c_item.get("created_on") or "").strip() or None,
                                        "updated_at": str(c_item.get("updated_on") or "").strip() or None,
                                        "web_url": str(
                                            (((c_item.get("links") or {}).get("html") or {}).get("href")) or ""
                                        ).strip() or None,
                                    }
                                )
                    except Exception:
                        pass

                review_status = (
                    "changes_requested"
                    if changes_req_bb
                    else ("approved" if approved_by_bb else "none")
                )
                ready_to_merge = True
                block_reason = None
                if not is_merged and norm_state == "open":
                    if ci_status == "pending":
                        ready_to_merge = False
                        block_reason = f"Bitbucket Pipelines still running on PR #{pr_id} ({ci_summary})."
                    elif ci_status == "failing":
                        ready_to_merge = False
                        block_reason = f"Bitbucket Pipelines failing on PR #{pr_id} ({ci_summary})."
                    elif review_status == "changes_requested":
                        ready_to_merge = False
                        block_reason = f"Changes were requested on Bitbucket PR #{pr_id}."

                html_url = str(
                    ((bb_pr.get("links") or {}).get("html") or {}).get("href") or ""
                ).strip() or None
                return {
                    "pull_request_url": html_url,
                    "number": int(pr_id) if pr_id is not None else None,
                    "repository_full_name": repo.full_name,
                    "provider": "bitbucket",
                    "state": norm_state,
                    "merged": is_merged,
                    "title": str(bb_pr.get("title") or "").strip() or None,
                    "body": str(bb_pr.get("description") or "").strip() or None,
                    "draft": False,
                    "author": bb_author,
                    "base_ref": str(
                        (((bb_pr.get("destination") or {}).get("branch") or {}).get("name"))
                        or repo.default_branch
                        or "main"
                    ).strip(),
                    "head_ref": str(
                        (((bb_pr.get("source") or {}).get("branch") or {}).get("name"))
                        or seed_branch
                    ).strip(),
                    "head_sha": head_sha_bb or None,
                    "created_at": str(bb_pr.get("created_on") or "").strip() or None,
                    "updated_at": str(bb_pr.get("updated_on") or "").strip() or None,
                    "merged_at": str(bb_pr.get("updated_on") or "").strip() or None if is_merged else None,
                    "merge_commit_sha": merge_sha_bb,
                    "merged_by": merged_by,
                    "has_conflicts": False,
                    "approved_by": approved_by_bb,
                    "changes_requested_by": changes_req_bb,
                    "requested_reviewers": [],
                    "ci_status": ci_status,
                    "ci_summary": ci_summary,
                    "failing_checks": failing_checks_bb,
                    "checks": checks_bb,
                    "comments": comments_bb,
                    "review_status": review_status,
                    "mergeable_state": None,
                    "ready_to_merge": ready_to_merge,
                    "block_reason": block_reason,
                }
    except Exception as exc:
        logger.debug("Bitbucket PR status check failed for %s: %s", repo.full_name, exc)
    return None


async def merge_provider_pull_request(
    client: httpx.AsyncClient,
    *,
    provider: str,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    seed_branch: str,
    default_branch: str,
    merge_label: str,
) -> tuple[bool, str | None]:
    """Attempt to merge `seed_branch` into `default_branch` via provider REST API, returning `(merged_via_api, merge_sha)`."""
    merged_via_api = False
    merge_sha: str | None = None

    if provider in ("github", "github-enterprise", "github-app"):
        api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
        encoded_branch = urllib.parse.quote(seed_branch, safe="/")
        owner = repo.full_name.split("/", 1)[0] if "/" in repo.full_name else ""
        head_filter = f"{owner}:{seed_branch}" if owner else seed_branch

        try:
            list_pr = await client.get(
                f"{api_base}/repos/{repo.full_name}/pulls",
                headers=headers,
                params={
                    "state": "all",
                    "head": head_filter,
                    "base": default_branch,
                    "per_page": 5,
                },
            )
            if list_pr.status_code == 200 and isinstance(list_pr.json(), list):
                pulls = [
                    p
                    for p in list_pr.json()
                    if isinstance(p, dict) and _github_pr_targets_branch(p, default_branch)
                ]
                if pulls:
                    pr = pulls[0]
                    pr_number = pr.get("number")
                    if pr.get("merged_at") or pr.get("merged"):
                        merged_via_api = True
                        sha = pr.get("merge_commit_sha")
                        if isinstance(sha, str) and sha:
                            merge_sha = sha
                        await client.delete(
                            f"{api_base}/repos/{repo.full_name}/git/refs/heads/{encoded_branch}",
                            headers=headers,
                        )
                    elif str(pr.get("state") or "").lower() == "open" and pr_number:
                        pr_merge_resp = await client.put(
                            f"{api_base}/repos/{repo.full_name}/pulls/{pr_number}/merge",
                            headers=headers,
                            json={
                                "commit_title": f"Merge PR #{pr_number} ({merge_label}) into {default_branch}",
                            },
                        )
                        if pr_merge_resp.status_code in (200, 201) and isinstance(
                            pr_merge_resp.json(), dict
                        ):
                            merged_via_api = True
                            sha = pr_merge_resp.json().get("sha")
                            if isinstance(sha, str) and sha:
                                merge_sha = sha
                            await client.delete(
                                f"{api_base}/repos/{repo.full_name}/git/refs/heads/{encoded_branch}",
                                headers=headers,
                            )
                        elif pr_merge_resp.status_code in (405, 409, 422):
                            err_msg = (
                                pr_merge_resp.json().get("message")
                                if isinstance(pr_merge_resp.json(), dict)
                                else None
                            ) or "Pull Request is not mergeable yet."
                            raise Conflict(
                                f"GitHub PR #{pr_number} cannot be merged yet: {err_msg}"
                            )
        except Conflict:
            raise
        except Exception as exc:
            logger.debug(
                "GitHub PR merge lookup/merge skipped for %s (%s): %s",
                repo.full_name,
                seed_branch,
                exc,
            )

        if not merged_via_api:
            try:
                merge_resp = await client.post(
                    f"{api_base}/repos/{repo.full_name}/merges",
                    headers=headers,
                    json={
                        "base": default_branch,
                        "head": seed_branch,
                        "commit_message": f"Merge {merge_label} into {default_branch}",
                    },
                )
                if merge_resp.status_code in (200, 201, 204):
                    merged_via_api = True
                    if merge_resp.status_code in (200, 201) and isinstance(
                        merge_resp.json(), dict
                    ):
                        sha = merge_resp.json().get("sha")
                        if isinstance(sha, str) and sha:
                            merge_sha = sha
                    await client.delete(
                        f"{api_base}/repos/{repo.full_name}/git/refs/heads/{encoded_branch}",
                        headers=headers,
                    )
            except Exception as exc:
                logger.warning(
                    "GitHub API merge failed for %s (%s), falling back to git CLI: %s",
                    repo.full_name,
                    seed_branch,
                    exc,
                )

    elif provider == "gitlab":
        api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
        enc_repo = urllib.parse.quote(repo.full_name, safe="")
        try:
            list_mr = await client.get(
                f"{api_base}/projects/{enc_repo}/merge_requests",
                headers=headers,
                params={
                    "state": "all",
                    "source_branch": seed_branch,
                    "target_branch": default_branch,
                    "per_page": 5,
                },
            )
            if list_mr.status_code == 200 and isinstance(list_mr.json(), list):
                mrs = [
                    m
                    for m in list_mr.json()
                    if isinstance(m, dict) and _gitlab_mr_targets_branch(m, default_branch)
                ]
                if mrs:
                    mr = mrs[0]
                    mr_state = str(mr.get("state") or "").lower()
                    mr_iid = mr.get("iid")
                    if mr_state == "merged" or mr.get("merged_at"):
                        merged_via_api = True
                        sha = mr.get("merge_commit_sha") or mr.get("sha")
                        if isinstance(sha, str) and sha:
                            merge_sha = sha
                    elif mr_state == "opened" and mr_iid:
                        acc_resp = await client.put(
                            f"{api_base}/projects/{enc_repo}/merge_requests/{mr_iid}/merge",
                            headers=headers,
                            json={"should_remove_source_branch": True},
                        )
                        if acc_resp.status_code in (200, 201) and isinstance(
                            acc_resp.json(), dict
                        ):
                            merged_via_api = True
                            sha = acc_resp.json().get("merge_commit_sha") or acc_resp.json().get("sha")
                            if isinstance(sha, str) and sha:
                                merge_sha = sha
                        elif acc_resp.status_code in (405, 406, 409, 422):
                            err_msg = (
                                acc_resp.json().get("message")
                                if isinstance(acc_resp.json(), dict)
                                else None
                            ) or "Merge Request is not mergeable yet."
                            raise Conflict(
                                f"GitLab MR !{mr_iid} cannot be merged yet: {err_msg}"
                            )
        except Conflict:
            raise
        except Exception as exc:
            logger.warning(
                "GitLab API merge failed for %s (%s), falling back to git CLI: %s",
                repo.full_name,
                seed_branch,
                exc,
            )

    elif provider == "bitbucket":
        api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
        try:
            list_bb = await client.get(
                f"{api_base}/repositories/{repo.full_name}/pullrequests",
                headers=headers,
                params={
                    "state": "OPEN,MERGED",
                    "q": f'source.branch.name="{seed_branch}" AND destination.branch.name="{default_branch}"',
                    "pagelen": 5,
                },
            )
            if list_bb.status_code == 200 and isinstance(list_bb.json(), dict):
                vals = [
                    p
                    for p in (list_bb.json().get("values") or [])
                    if isinstance(p, dict) and _bitbucket_pr_targets_branch(p, default_branch)
                ]
                if vals:
                    bb_pr = vals[0]
                    pr_id = bb_pr.get("id")
                    state_upper = str(bb_pr.get("state") or "").upper()
                    if state_upper == "MERGED":
                        merged_via_api = True
                        sha = ((bb_pr.get("merge_commit") or {}).get("hash"))
                        if isinstance(sha, str) and sha:
                            merge_sha = sha
                    elif state_upper == "OPEN" and pr_id:
                        bb_merge = await client.post(
                            f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_id}/merge",
                            headers=headers,
                            json={
                                "message": f"Merge PR #{pr_id} ({merge_label}) into {default_branch}",
                                "close_source_branch": True,
                            },
                        )
                        if bb_merge.status_code in (200, 201) and isinstance(
                            bb_merge.json(), dict
                        ):
                            merged_via_api = True
                            sha = ((bb_merge.json().get("merge_commit") or {}).get("hash"))
                            if isinstance(sha, str) and sha:
                                merge_sha = sha
                        elif bb_merge.status_code in (400, 409, 422):
                            raise Conflict(
                                f"Bitbucket PR #{pr_id} cannot be merged yet."
                            )
        except Conflict:
            raise
        except Exception as exc:
            logger.warning(
                "Bitbucket API merge failed for %s (%s), falling back to git CLI: %s",
                repo.full_name,
                seed_branch,
                exc,
            )

    return merged_via_api, merge_sha


GOVERNANCE_RECEIPT_MARKER = "<!-- gitseed-governance-receipt -->"
RECEIPT_COMMENT_MARKER = GOVERNANCE_RECEIPT_MARKER


def format_governance_receipt_comment(envelope: dict[str, Any]) -> str:
    """Format a signed governance receipt envelope into a Markdown comment with a machine-verifiable JSON block."""
    receipt = envelope.get("receipt") if isinstance(envelope.get("receipt"), dict) else {}
    seed_info = receipt.get("seed") if isinstance(receipt.get("seed"), dict) else {}
    policy_info = receipt.get("policy") if isinstance(receipt.get("policy"), dict) else {}
    runs = receipt.get("runs") if isinstance(receipt.get("runs"), list) else []
    approvals = receipt.get("approvals") if isinstance(receipt.get("approvals"), list) else []

    key_id = str(envelope.get("key_id") or "unknown")
    algorithm = str(envelope.get("algorithm") or "Ed25519")
    signature = str(envelope.get("signature") or "")
    sig_preview = f"{signature[:16]}...{signature[-8:]}" if len(signature) > 28 else signature

    seed_num = seed_info.get("number")
    project_slug = seed_info.get("project_slug") or ""
    seed_title = seed_info.get("title") or ""
    seed_ref = f"`{project_slug}#{seed_num}`" if project_slug and seed_num else f"`{seed_info.get('id', '')}`"

    total_cost = 0.0
    enforcements: set[str] = set()
    for r in runs:
        if isinstance(r, dict):
            total_cost += float(r.get("cost_usd") or 0.0)
            enf = str(r.get("cost_enforcement") or "enforced")
            enforcements.add(enf)

    overall_enforcement = "estimated" if "estimated" in enforcements else "enforced"

    approvals_by_stage: dict[str, str] = {}
    for a in approvals:
        if isinstance(a, dict):
            st = str(a.get("stage") or "")
            status_val = str(a.get("status") or "")
            who = str(a.get("approver_name") or a.get("approver_user_id") or "auto")
            approvals_by_stage[st] = f"{status_val} ({who})"

    lines: list[str] = [
        GOVERNANCE_RECEIPT_MARKER,
        f"### GitSeed Governance Receipt — {seed_ref} {seed_title}".strip(),
        "",
        f"- **Schema**: `{receipt.get('schema_version', 'gitseed.receipt.v1')}`",
        f"- **Autonomy / Policy**: `{policy_info.get('autonomy', 'standard')}` · min role `{policy_info.get('min_approver_role', 'maintainer')}` · prevent self-approval `{bool(policy_info.get('prevent_self_approval', False))}`",
        f"- **Total Spend**: `${total_cost:.4f}` (**{overall_enforcement}**)",
        f"- **Signature**: `{algorithm}` · key `{key_id}` · `{sig_preview}`",
        "",
        "| Stage | Runtime / Model | Status | Approval | Spend | Enforcement | Commit |",
        "| :--- | :--- | :--- | :--- | :--- | :--- | :--- |",
    ]

    if runs:
        for r in runs:
            if not isinstance(r, dict):
                continue
            st = str(r.get("stage") or "-")
            rt = str(r.get("runtime") or "-")
            mdl = str(r.get("model") or "-")
            st_status = str(r.get("status") or "-")
            app_summary = approvals_by_stage.get(st, "—")
            cost_val = float(r.get("cost_usd") or 0.0)
            enf_val = str(r.get("cost_enforcement") or "enforced")
            trusted = r.get("trusted") if isinstance(r.get("trusted"), dict) else {}
            commit_sha = str(trusted.get("commit_sha") or "")[:7] if trusted else ""
            commit_cell = f"`{commit_sha}`" if commit_sha else "—"
            lines.append(
                f"| `{st}` | `{rt}` / `{mdl}` | `{st_status}` | {app_summary} | `${cost_val:.4f}` | `{enf_val}` | {commit_cell} |"
            )
    else:
        lines.append("| — | — | — | — | `$0.0000` | `enforced` | — |")

    envelope_for_json = {
        k: v for k, v in envelope.items() if k != "markdown"
    }
    signed_json_str = json.dumps(envelope_for_json, indent=2, sort_keys=True)

    lines.extend(
        [
            "",
            "<details>",
            "<summary>Signed Governance Receipt JSON (Ed25519)</summary>",
            "",
            "```json",
            signed_json_str,
            "```",
            "</details>",
        ]
    )
    return "\n".join(lines)


async def sync_seed_governance_receipt_comment(
    client: httpx.AsyncClient,
    *,
    provider: str,
    backend: Backend,
    repo: ProjectRepository,
    token: str,
    pr_number: int | str,
    envelope: dict[str, Any],
) -> dict[str, Any] | None:
    """Post or update the signed governance receipt comment on a GitHub PR, GitLab MR, or Bitbucket PR."""
    body = format_governance_receipt_comment(envelope)
    norm_provider = (provider or backend.provider or "github").strip().lower()

    try:
        if norm_provider in ("github", "github-enterprise", "github-app"):
            api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
            comments_url = f"{api_base}/repos/{repo.full_name}/issues/{pr_number}/comments"
            list_resp = await client.get(comments_url, headers=headers, params={"per_page": 100})
            existing_id: int | str | None = None
            if list_resp.status_code == 200 and isinstance(list_resp.json(), list):
                for item in list_resp.json():
                    if isinstance(item, dict) and GOVERNANCE_RECEIPT_MARKER in str(item.get("body") or ""):
                        existing_id = item.get("id")
                        break
            if existing_id is not None:
                patch_resp = await client.patch(
                    f"{api_base}/repos/{repo.full_name}/issues/comments/{existing_id}",
                    headers=headers,
                    json={"body": body},
                )
                if patch_resp.status_code in (200, 201) and isinstance(patch_resp.json(), dict):
                    return {"id": str(existing_id), "updated": True, "provider": "github"}
            post_resp = await client.post(
                comments_url,
                headers=headers,
                json={"body": body},
            )
            if post_resp.status_code in (200, 201) and isinstance(post_resp.json(), dict):
                return {"id": str(post_resp.json().get("id") or ""), "updated": False, "provider": "github"}

        elif norm_provider == "gitlab":
            api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
            enc_repo = urllib.parse.quote(repo.full_name, safe="")
            notes_url = f"{api_base}/projects/{enc_repo}/merge_requests/{pr_number}/notes"
            list_resp = await client.get(notes_url, headers=headers, params={"per_page": 100})
            existing_note_id: int | str | None = None
            if list_resp.status_code == 200 and isinstance(list_resp.json(), list):
                for note in list_resp.json():
                    if isinstance(note, dict) and GOVERNANCE_RECEIPT_MARKER in str(note.get("body") or ""):
                        existing_note_id = note.get("id")
                        break
            if existing_note_id is not None:
                put_resp = await client.put(
                    f"{notes_url}/{existing_note_id}",
                    headers=headers,
                    json={"body": body},
                )
                if put_resp.status_code in (200, 201):
                    return {"id": str(existing_note_id), "updated": True, "provider": "gitlab"}
            post_resp = await client.post(
                notes_url,
                headers=headers,
                json={"body": body},
            )
            if post_resp.status_code in (200, 201) and isinstance(post_resp.json(), dict):
                return {"id": str(post_resp.json().get("id") or ""), "updated": False, "provider": "gitlab"}

        elif norm_provider == "bitbucket":
            api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
            comments_url = f"{api_base}/repositories/{repo.full_name}/pullrequests/{pr_number}/comments"
            list_resp = await client.get(comments_url, headers=headers, params={"pagelen": 100})
            existing_bb_id: int | str | None = None
            if list_resp.status_code == 200 and isinstance(list_resp.json(), dict):
                values = list_resp.json().get("values")
                if isinstance(values, list):
                    for item in values:
                        if not isinstance(item, dict) or item.get("deleted"):
                            continue
                        content_obj = item.get("content")
                        raw_body = (
                            str(content_obj.get("raw") or "")
                            if isinstance(content_obj, dict)
                            else str(item.get("body") or "")
                        )
                        if GOVERNANCE_RECEIPT_MARKER in raw_body:
                            existing_bb_id = item.get("id")
                            break
            if existing_bb_id is not None:
                put_resp = await client.put(
                    f"{comments_url}/{existing_bb_id}",
                    headers=headers,
                    json={"content": {"raw": body}},
                )
                if put_resp.status_code in (200, 201):
                    return {"id": str(existing_bb_id), "updated": True, "provider": "bitbucket"}
            post_resp = await client.post(
                comments_url,
                headers=headers,
                json={"content": {"raw": body}},
            )
            if post_resp.status_code in (200, 201) and isinstance(post_resp.json(), dict):
                return {"id": str(post_resp.json().get("id") or ""), "updated": False, "provider": "bitbucket"}
    except Exception as exc:
        logger.debug("Governance receipt PR comment sync failed for %s: %s", repo.full_name, exc)

    return None


