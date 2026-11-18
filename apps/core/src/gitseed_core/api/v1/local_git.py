"""Git Smart HTTP routes (`/api/v1/local-git/{repo_slug}.git/...`) for on-device Local Git repos.

Enables Docker containers (Seed stage runners, Sandbox terminals, Agent CLI sessions) and
desktop clients to run standard `git clone`, `git fetch`, and `git push` against a project's
local bare repository without requiring GitHub, GitLab, or Bitbucket.
"""

from __future__ import annotations

import asyncio
import base64
import subprocess

from fastapi import APIRouter, HTTPException, Query, Request, Response
from gitseed_common.enums import OrgRole, ProjectRole, ProjectVisibility
from sqlmodel import col, select

from gitseed_core.api.deps import DBSession, verify_csrf
from gitseed_core.config import settings
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.user import User
from gitseed_core.security import verify_run_token
from gitseed_core.services import auth as auth_service
from gitseed_core.services.errors import Forbidden, Invalid, NotFound, Unauthorized
from gitseed_core.services.local_git import (
    _sanitized_git_env,
    ensure_local_bare_repo,
    extract_repo_slug,
    resolve_local_bare_repo_path,
    sync_bare_repo_to_workspace,
    sync_workspace_commits_to_bare_repo,
)

router = APIRouter(prefix="/local-git", tags=["Local Git"])

_ALLOWED_SERVICES = {"git-upload-pack", "git-receive-pack"}
MAX_PACK_REQUEST_BYTES = 100 * 1024 * 1024  # 100 MiB


async def _read_bounded_pack_body(
    request: Request,
    *,
    max_bytes: int = MAX_PACK_REQUEST_BYTES,
) -> bytes:
    raw_cl = (request.headers.get("content-length") or "").strip()
    if raw_cl:
        try:
            content_length = int(raw_cl)
        except ValueError as exc:
            raise Invalid("Invalid Content-Length header") from exc
        if content_length < 0:
            raise Invalid("Invalid Content-Length header")
        if content_length > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Git pack payload exceeds maximum size of {max_bytes} bytes",
            )

    chunks: list[bytes] = []
    total_bytes = 0
    async for chunk in request.stream():
        if not chunk:
            continue
        total_bytes += len(chunk)
        if total_bytes > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"Git pack payload exceeds maximum size of {max_bytes} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _pkt_line(text: str) -> bytes:
    data = text.encode("utf-8")
    length = len(data) + 4
    return f"{length:04x}".encode("ascii") + data


def _extract_git_http_token(request: Request) -> str | None:
    auth_header = (request.headers.get("authorization") or "").strip()
    if not auth_header:
        return None
    if auth_header.lower().startswith("bearer "):
        return auth_header[7:].strip() or None
    if auth_header.lower().startswith("basic "):
        b64_part = auth_header[6:].strip()
        try:
            decoded = base64.b64decode(b64_part).decode("utf-8", errors="replace")
        except Exception:
            return ""
        if ":" in decoded:
            user_part, pass_part = decoded.split(":", 1)
            return (pass_part or user_part).strip()
        return decoded.strip()
    return ""


async def _check_user_repo_access(
    session: DBSession,
    user: User,
    repo_slug: str,
    *,
    require_write: bool,
) -> None:
    slug = extract_repo_slug(repo_slug)
    proj = (
        await session.execute(
            select(Project).where(
                Project.slug == slug,
                col(Project.archived_at).is_(None),
            )
        )
    ).scalars().first()
    if proj is None or getattr(proj, "archived_at", None) is not None:
        raise Forbidden(f"Local Git repository '{slug}' is not bound to an accessible project")
    if user.org_role == OrgRole.ADMIN:
        return
    member = (
        await session.execute(
            select(ProjectMember).where(
                ProjectMember.project_id == proj.id,
                ProjectMember.user_id == user.id,
            )
        )
    ).scalars().first()
    if require_write:
        if member is None or member.role not in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER):
            raise Forbidden("Write access to project repository denied")
        return
    if proj.visibility != ProjectVisibility.PUBLIC and member is None:
        raise Forbidden("Read access to private project repository denied")


async def _verify_local_git_auth(
    request: Request,
    session: DBSession,
    *,
    repo_slug: str = "",
    require_write: bool,
) -> None:
    token = _extract_git_http_token(request)
    if token is not None:
        if not token:
            raise Unauthorized("Invalid Git HTTP Authorization header")
        if token.startswith("gsr_"):
            scope = verify_run_token(token)
            if not scope or not scope.startswith("local-git:"):
                raise Unauthorized("Invalid or expired runner Git token")
            parts = scope.split(":", 2)
            mode = parts[1] if len(parts) > 1 else ""
            if mode not in ("read", "write"):
                raise Unauthorized("Invalid or expired runner Git token")
            if require_write and mode == "read":
                raise Forbidden("Read-only Git clone token cannot push to repository")
            if len(parts) != 3 or not parts[2].strip():
                raise Forbidden("Unscoped runner Git token is not allowed")
            allowed_slugs = {s.strip() for s in parts[2].split(",") if s.strip()}
            if not repo_slug or extract_repo_slug(repo_slug) not in allowed_slugs:
                raise Forbidden("Runner Git token is not authorized for this repository")
            return
        user = await auth_service.authenticate_token(session, token)
        await _check_user_repo_access(session, user, repo_slug, require_write=require_write)
        return

    cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    if cookie:
        cookie_user = await auth_service.authenticate_session_cookie(session, cookie)
        if cookie_user is not None:
            if require_write and request.method.upper() not in ("GET", "HEAD", "OPTIONS"):
                verify_csrf(request)
            await _check_user_repo_access(session, cookie_user, repo_slug, require_write=require_write)
            return

    raise Unauthorized("Authentication required for Local Git Smart HTTP")


@router.get("/{repo_slug}.git/info/refs")
async def local_git_info_refs(
    repo_slug: str,
    request: Request,
    session: DBSession,
    service: str = Query(default=""),
) -> Response:
    if service not in _ALLOWED_SERVICES:
        raise Invalid("Unsupported Git Smart HTTP service")
    await _verify_local_git_auth(
        request,
        session,
        repo_slug=repo_slug,
        require_write=(service == "git-receive-pack"),
    )
    slug = extract_repo_slug(repo_slug)
    bare_path = resolve_local_bare_repo_path(slug)
    if not (bare_path / "HEAD").exists():
        proj = (
            await session.execute(select(Project).where(Project.slug == slug))
        ).scalars().first()
        if proj is None:
            raise NotFound(f"Local Git repository '{slug}' not found")
        await asyncio.to_thread(ensure_local_bare_repo, slug, proj.name or slug)
    elif service == "git-upload-pack":
        await asyncio.to_thread(sync_workspace_commits_to_bare_repo, bare_path)

    subcmd = "upload-pack" if service == "git-upload-pack" else "receive-pack"
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.ext.allow=never",
            subcmd,
            "--stateless-rpc",
            "--advertise-refs",
            str(bare_path),
        ],
        env=_sanitized_git_env(),
        capture_output=True,
        check=False,
        timeout=30,
    )
    if proc.returncode != 0:
        raise NotFound(f"Local Git repository '{slug}' could not advertise refs")

    header = _pkt_line(f"# service={service}\n") + b"0000"
    return Response(
        content=header + proc.stdout,
        media_type=f"application/x-{service}-advertisement",
        headers={"Cache-Control": "no-cache, max-age=0, must-revalidate"},
    )


@router.post("/{repo_slug}.git/{service}")
async def local_git_rpc(
    repo_slug: str,
    service: str,
    request: Request,
    session: DBSession,
) -> Response:
    if service not in _ALLOWED_SERVICES:
        raise Invalid("Unsupported Git Smart HTTP service")
    await _verify_local_git_auth(
        request,
        session,
        repo_slug=repo_slug,
        require_write=(service == "git-receive-pack"),
    )
    slug = extract_repo_slug(repo_slug)
    bare_path = resolve_local_bare_repo_path(slug)
    if not (bare_path / "HEAD").exists():
        raise NotFound(f"Local Git repository '{slug}' not found")

    body = await _read_bounded_pack_body(request)
    subcmd = "upload-pack" if service == "git-upload-pack" else "receive-pack"
    proc = await asyncio.to_thread(
        subprocess.run,
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "core.fsmonitor=false",
            "-c",
            "protocol.ext.allow=never",
            subcmd,
            "--stateless-rpc",
            str(bare_path),
        ],
        input=body,
        env=_sanitized_git_env(),
        capture_output=True,
        check=False,
        timeout=120,
    )
    if service == "git-receive-pack" and proc.returncode == 0:
        await asyncio.to_thread(sync_bare_repo_to_workspace, bare_path)
    return Response(
        content=proc.stdout,
        media_type=f"application/x-{service}-result",
        headers={"Cache-Control": "no-cache, max-age=0, must-revalidate"},
    )

