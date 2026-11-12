"""Interactive, sandbox-backed Live Agent Sessions service."""

from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from urllib.parse import quote

import httpx
from gitseed_common.enums import (
    AgentRuntime,
    AgentSessionStatus,
    BackendType,
    OrgRole,
    ProjectRole,
)
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.db import AsyncSessionLocal
from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.agent_session import AgentSession, AgentSessionMessage
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend, ProjectRepository
from gitseed_core.models.seed import Seed
from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.skill import Skill
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets, encrypt_secrets
from gitseed_core.services import projects as projects_service
from gitseed_core.services import session_pty, session_workspace
from gitseed_core.services.cli_auth import validate_and_filter_cli_auth_bundle
from gitseed_core.services.compute_runtime import (
    ensure_docker_session_workspace_volume,
    is_k8s_backend,
    k8s_exec_command,
    k8s_put_archive,
    remove_docker_session_workspace_volume,
    spawn_k8s_pod,
)
from gitseed_core.services.coordinator import (
    _build_brainstorm_context,
    _build_fallback_brainstorm_proposals,
    _build_workspace_snapshot,
    _extract_seed_proposals,
    _resolve_coordinator_model,
    docker_host_fallback_url,
    extract_enabled_model_ids,
    sync_backend_to_litellm,
)
from gitseed_core.services.errors import Forbidden, Invalid, NotFound
from gitseed_core.services.gateway import (
    compute_project_remaining_monthly_budget,
    create_session_virtual_key,
    is_personal_mode_backend,
    resolve_session_context_window,
    revoke_session_virtual_key,
    validate_personal_mode_backend_access,
)
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.git_merge import format_seed_branch
from gitseed_core.services.sandboxes import (
    _clone_and_pack_workspace_tar,
    _select_docker_compute_backend,
)
from gitseed_core.services.scheduler import (
    _docker_client_for_backend,
    _ensure_docker_network,
    inspect_docker_container_state,
    stop_and_remove_docker_container,
)
from gitseed_core.services.session_vscode import (
    VSCODE_BUNDLE_VOLUME,
    VSCODE_MOUNT_PATH,
    close_session_vscode,
    ensure_session_vscode,
)

logger = logging.getLogger("uvicorn.error")

_ACTIVE_TURN_TASKS: dict[str, asyncio.Task[None]] = {}
_CONTAINER_TURN_COUNT: dict[str, int] = {}
# Default client-generated names like "Chat · @architect · 11:42" may be replaced
# by an auto-title derived from the first user message.
_AUTO_TITLE_RE = re.compile(r"^(Chat|Agent) · @[\w.-]+ · \d{1,2}:\d{2}( ?[AP]M)?$")
_EMPTY_SESSION_TTL_SECONDS = 30 * 60


async def _require_project_access(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    require_write: bool = False,
) -> Project:
    project = await projects_service.get_project(session, actor, project_id_or_slug)
    if require_write:
        if project.archived_at is not None:
            raise Invalid("Cannot modify agent sessions in an archived project")
        if actor.org_role == OrgRole.ADMIN:
            return project
        role = await projects_service.get_member_role(session, project.id, actor.id)
        if role not in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER):
            raise Forbidden(
                "Project developer or maintainer privileges required for live agent sessions"
            )
    return project


async def _resolve_session_agent(
    session: AsyncSession,
    agent_id: str | None,
) -> Agent:
    if agent_id:
        agent = await session.get(Agent, agent_id)
        if agent is None:
            clean_handle = agent_id.strip().lstrip("@").lower()
            agent = (
                await session.execute(
                    select(Agent).where(
                        col(Agent.archived_at).is_(None),
                        col(Agent.handle) == clean_handle,
                    )
                )
            ).scalars().first()
        if agent is None or agent.archived_at is not None:
            raise NotFound(f"Agent '{agent_id}' not found")
        return agent

    # Default to @ideator / @planner or first enabled stage/assistant agent
    stmt = (
        select(Agent)
        .where(col(Agent.archived_at).is_(None))
        .order_by(
            col(Agent.enabled).desc(),
            col(Agent.handle).in_(["ideator", "planner", "architect"]).desc(),
            col(Agent.created_at).asc(),
        )
    )
    agent = (await session.execute(stmt)).scalars().first()
    if agent is None:
        raise Invalid("No configured agents available")
    return agent


async def _resolve_session_models_backend(
    session: AsyncSession,
    *,
    agent: Agent,
    project: Project,
    models_backend_id: str | None = None,
    requested_model: str | None = None,
    actor_user_id: str | None = None,
) -> tuple[Backend | None, str]:
    if models_backend_id:
        b = await session.get(Backend, models_backend_id)
        if b is not None and b.type == BackendType.INFERENCE and b.enabled:
            if project.backends_restricted:
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
                if b.id not in allowed_bids:
                    raise Forbidden(
                        "This inference backend is not in the project's allowed backends list"
                    )
            if b.access_restricted:
                grant = await session.get(AgentBackend, (b.id, agent.id))
                if grant is None:
                    raise Forbidden(
                        "Agent is not granted access to this restricted inference backend"
                    )
            validate_personal_mode_backend_access(
                session, b, actor_user_id=actor_user_id
            )
            chosen_model = (requested_model or agent.model or "").strip()
            if not chosen_model or chosen_model == "default":
                enabled_ids = extract_enabled_model_ids(b)
                chosen_model = enabled_ids[0] if enabled_ids else "default"
            return b, chosen_model

    # Respect runtime-native backend preference when possible
    runtime_str = str(agent.runtime or "opencode").strip().lower()
    preferred_provider = {
        "claude-code": "anthropic",
        "codex": "openai",
        "antigravity": "antigravity",
        "gemini": "gemini",
        "grok-build": "xai",
    }.get(runtime_str)

    stmt = (
        select(Backend)
        .where(
            Backend.type == BackendType.INFERENCE,
            col(Backend.enabled).is_(True),
        )
        .order_by(col(Backend.created_at).asc())
    )
    backends = list((await session.execute(stmt)).scalars().all())
    if project.backends_restricted:
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

    accessible: list[Backend] = []
    for b in backends:
        if b.access_restricted:
            grant = await session.get(AgentBackend, (b.id, agent.id))
            if grant is None:
                continue
        if is_personal_mode_backend(b):
            try:
                validate_personal_mode_backend_access(
                    session, b, actor_user_id=actor_user_id
                )
            except Exception:
                continue
        accessible.append(b)
    if not accessible:
        return None, (requested_model or agent.model or "default").strip() or "default"

    if preferred_provider and accessible:
        for b in accessible:
            prov = b.provider.lower()
            if prov == preferred_provider or (
                preferred_provider in ("gemini", "antigravity")
                and prov in ("gemini", "antigravity", "google")
            ):
                model_str = (requested_model or agent.model or "default").strip()
                if model_str == "default":
                    enabled_ids = extract_enabled_model_ids(b)
                    if enabled_ids:
                        # Filter out invalid alias tier names (flash, pro, flash_lite) that Antigravity CLI does not accept
                        valid_models = [
                            mid
                            for mid in enabled_ids
                            if mid not in ("flash", "flash_lite", "pro", "default", "inherit")
                        ]
                        model_str = valid_models[0] if valid_models else enabled_ids[0]
                return b, model_str

    backend, resolved_model = await _resolve_coordinator_model(
        session, agent, project, actor_user_id=actor_user_id
    )
    if requested_model and requested_model.strip() and requested_model.strip() != "default":
        return backend, requested_model.strip()
    return backend, resolved_model


def _strip_dot_slash(p: str) -> str:
    cleaned = p.replace("\\", "/")
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


async def _gateway_ctx(
    ag_session: AgentSession,
    models_backend: Backend,
    model: str,
    *,
    actor_user_id: str | None = None,
    seed: Seed | None = None,
    project: Project | None = None,
    org: Any | None = None,
    session: AsyncSession | None = None,
    remaining_project_budget: Decimal | None = None,
) -> dict[str, Any]:
    """Gateway/auth context consumed by the runner's CLI adapters inside the container.

    Like seed runs, the CLI authenticates to the LiteLLM gateway with a per-session virtual
    key; the provider's real API key is never placed in the container.
    """
    validate_personal_mode_backend_access(
        session,
        models_backend,
        seed,
        actor_user_id=actor_user_id or ag_session.created_by,
    )
    if (
        remaining_project_budget is None
        and session is not None
        and project is not None
        and getattr(project, "monthly_budget", None) is not None
    ):
        remaining_project_budget = await compute_project_remaining_monthly_budget(
            session, project
        )
    if remaining_project_budget is not None and remaining_project_budget <= Decimal("0"):
        raise Invalid("Project monthly budget exhausted")

    models_secrets = decrypt_secrets(models_backend.secrets_encrypted)
    auth_mode = str((models_backend.config or {}).get("auth_mode") or "api_key").strip().lower()
    oauth_token = models_secrets.get("oauth_token")
    runtime_str = (
        ag_session.runtime.value
        if hasattr(ag_session.runtime, "value")
        else str(ag_session.runtime or "")
    )
    cli_auth_bundle: dict[str, str] = {}
    if models_secrets.get("cli_auth_bundle"):
        try:
            parsed_b = json.loads(models_secrets["cli_auth_bundle"])
            if isinstance(parsed_b, dict):
                cli_auth_bundle = validate_and_filter_cli_auth_bundle(
                    parsed_b,
                    runtime=runtime_str,
                    provider=models_backend.provider,
                    strict=False,
                )
        except Exception:
            pass
    if auth_mode != "cli_oauth" and not models_secrets.get("api_key") and (oauth_token or cli_auth_bundle):
        auth_mode = "cli_oauth"
    gw_base = settings.RUNNER_GATEWAY_URL.rstrip("/")
    api_key = await create_session_virtual_key(
        session_id=ag_session.id,
        project_id=ag_session.project_id,
        agent_id=ag_session.agent_id,
        backend=models_backend,
        model=model,
        ttl_seconds=int(settings.SESSION_MAX_HOURS * 3600) + 600,
        seed=seed,
        project=project,
        org=org,
        remaining_project_budget=remaining_project_budget,
        session=session,
    )
    return {
        "provider": models_backend.provider,
        "model": model,
        "api_key": api_key,
        "base_url": gw_base,
        "anthropic_base_url": gw_base.removesuffix("/v1"),
        "auth_mode": auth_mode,
        "oauth_token": oauth_token,
        "cli_auth_bundle": cli_auth_bundle,
    }


async def _resolve_session_mcp_servers(
    session: AsyncSession,
    project: Project,
    agent_id: str,
) -> list[dict[str, Any]]:
    """Resolve accessible Tools (MCP) backends for an interactive AgentSession."""
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
        tools_rows = [b for b in tools_rows if b.id in allowed_bids]

    mcp_servers: list[dict[str, Any]] = []
    for tb in tools_rows:
        if tb.access_restricted:
            grant = await session.get(AgentBackend, (tb.id, agent_id))
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
        mcp_servers.append(
            {
                "id": tb.id,
                "name": tb.name,
                "slug": tb_slug,
                "provider": tb.provider,
                "endpoint": resolved_ep,
                "headers": headers,
                "env": env_vars,
            }
        )
    return mcp_servers


# Agent containers run as the runner image's non-root ``runner`` user (uid/gid
# 1000) with all capabilities dropped, so a post-start ``chown`` cannot work.
# Docker preserves tar ownership on extraction, so stamp it on every entry.
_RUNNER_UID = 1000
_RUNNER_GID = 1000


def _runner_owned(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = _RUNNER_UID
    info.gid = _RUNNER_GID
    info.uname = "runner"
    info.gname = "runner"
    return info


def _pack_single_file_tar(arcname: str, data: bytes) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        info = tarfile.TarInfo(name=arcname)
        info.size = len(data)
        info.mode = 0o644
        tar.addfile(_runner_owned(info), io.BytesIO(data))
    return buf.getvalue()


async def _ensure_warm_session_container(
    session: AsyncSession,
    ag_session: AgentSession,
    project: Project,
) -> tuple[Backend | None, str | None]:
    """Ensure a warm `gitseed-runner:latest` container with `/workspace` checked out is running."""
    try:
        compute_backend = await _select_docker_compute_backend(session, project=project)
    except Invalid:
        if project.backends_restricted:
            raise
        return None, None
    except Exception as exc:
        logger.debug("No Docker compute backend available for session %s: %s", ag_session.id, exc)
        return None, None

    if ag_session.container_id:
        state = await inspect_docker_container_state(
            compute_backend, ag_session.container_id
        )
        if state.get("running"):
            return compute_backend, ag_session.container_id

    # Resolve target repository to clone into /workspace
    repo: ProjectRepository | None = None
    if ag_session.repository_id is not None:
        repo = await session.get(ProjectRepository, ag_session.repository_id)
        if repo is not None and repo.project_id != project.id:
            repo = None
    if repo is None:
        repos = (
            await session.execute(
                select(ProjectRepository)
                .where(ProjectRepository.project_id == project.id)
                .order_by(
                    col(ProjectRepository.is_primary).desc(),
                    col(ProjectRepository.id).asc(),
                )
            )
        ).scalars().all()
        repo = repos[0] if repos else None

    workspace_tar: bytes | None = None
    chosen_ref = ag_session.ref or "main"
    head_sha = ag_session.head_sha or ""
    has_baseline = session_workspace.load_baseline(ag_session.id) is not None

    cfg = compute_backend.config or {}
    sbx_cfg = project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    image = str(cfg.get("runner_image") or settings.RUNNER_IMAGE).strip()
    network_name = str(cfg.get("network") or settings.RUNNER_DOCKER_NETWORK).strip()
    cpus = float(sbx_cfg.get("cpus") or cfg.get("cpus") or 2.0)
    memory_mb = int(sbx_cfg.get("memory_mb") or cfg.get("memory_mb") or 4096)
    container_name = f"gitseed-ses-{ag_session.id}"

    keepalive_cmd = (
        "mkdir -p /workspace /tmp/gitseed_ses && "
        "trap 'exit 0' TERM INT; "
        "while true; do sleep 2; done"
    )
    ses_env_map: dict[str, str] = {
        "PYTHONUNBUFFERED": "1",
        "GITSEED_GATEWAY_URL": settings.RUNNER_GATEWAY_URL.rstrip("/"),
    }
    proxy_url = str(getattr(settings, "RUNNER_EGRESS_PROXY_URL", "") or "").strip()
    if proxy_url:
        internal_hosts = str(
            getattr(settings, "EGRESS_INTERNAL_HOSTS", "litellm,api") or "litellm,api"
        ).strip()
        no_proxy_val = f"{internal_hosts},localhost,127.0.0.1,host.docker.internal"
        ses_env_map.update(
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
        try:
            snap_file = session_workspace.session_data_dir(ag_session.id) / ".workspace.tar"
            if snap_file.is_file():
                workspace_tar = snap_file.read_bytes()
            elif repo is not None:
                git_backend = await session.get(Backend, repo.backend_id)
                if git_backend is not None:
                    try:
                        token = await resolve_git_backend_token(
                            git_backend, repo_full_names=[repo.full_name]
                        )
                        workspace_tar, chosen_ref, head_sha = await asyncio.to_thread(
                            _clone_and_pack_workspace_tar,
                            backend=git_backend,
                            full_name=repo.full_name,
                            token=token,
                            ref=chosen_ref,
                            default_branch=repo.default_branch,
                        )
                        if workspace_tar:
                            session_workspace.save_baseline(ag_session.id, workspace_tar)
                    except Exception as exc:
                        logger.warning(
                            "Session %s workspace clone warning for %s: %s",
                            ag_session.id,
                            repo.full_name,
                            exc,
                        )

            pod_ref = await spawn_k8s_pod(
                backend=compute_backend,
                pod_name=container_name,
                image=image,
                command=["sh", "-c", keepalive_cmd],
                env_map=ses_env_map,
                labels={
                    "app.gitseed.managed": "true",
                    "app.gitseed.agent_session_id": ag_session.id,
                    "app.gitseed.project_id": project.id,
                },
                cpus=cpus,
                memory_mb=memory_mb,
                as_root=False,
                tty=False,
                stdin=False,
                working_dir="/workspace",
                project_slug=project.slug,
                workload="sandbox",
            )
            if workspace_tar:
                await k8s_put_archive(compute_backend, pod_ref, "/workspace", workspace_tar, timeout=60.0)

            ag_session.container_id = pod_ref
            ag_session.compute_backend_id = compute_backend.id
            ag_session.ref = chosen_ref
            if head_sha:
                ag_session.head_sha = head_sha
            _CONTAINER_TURN_COUNT[pod_ref] = 0
            await session.commit()
            return compute_backend, pod_ref
        except Exception as exc:
            logger.warning("K8s warm session pod provisioning skipped for %s: %s", ag_session.id, exc)
            return None, None

    create_payload: dict[str, Any] = {
        "Image": image,
        "Entrypoint": ["sh", "-c"],
        "Cmd": [keepalive_cmd],
        "WorkingDir": "/workspace",
        "Tty": False,
        "Env": [f"{k}={v}" for k, v in ses_env_map.items()],
        "Labels": {
            "app.gitseed.managed": "true",
            "app.gitseed.agent_session_id": ag_session.id,
            "app.gitseed.project_id": project.id,
        },
        "HostConfig": {
            "NetworkMode": network_name,
            "NanoCPUs": int(cpus * 1_000_000_000),
            "Memory": int(memory_mb * 1024 * 1024),
            "PidsLimit": 512,
            "Privileged": False,
            "CapDrop": ["ALL"],
            "SecurityOpt": ["no-new-privileges:true"],
            **(
                {"ExtraHosts": ["host.docker.internal:host-gateway"]}
                if settings.SANDBOX_ALLOW_HOST_GATEWAY
                else {}
            ),
            "AutoRemove": False,
        },
    }

    try:
        client, base_url = _docker_client_for_backend(compute_backend)
        async with client:
            await _ensure_docker_network(client, base_url, network_name)
            ws_vol_name, vol_existed = await ensure_docker_session_workspace_volume(
                client, base_url, ag_session.id, project.id
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

            if repo is not None and not (vol_existed and has_baseline):
                git_backend = await session.get(Backend, repo.backend_id)
                if git_backend is not None:
                    try:
                        token = await resolve_git_backend_token(
                            git_backend, repo_full_names=[repo.full_name]
                        )
                        workspace_tar, chosen_ref, head_sha = await asyncio.to_thread(
                            _clone_and_pack_workspace_tar,
                            backend=git_backend,
                            full_name=repo.full_name,
                            token=token,
                            ref=chosen_ref,
                            default_branch=repo.default_branch,
                        )
                        # Record what the checkout looked like so Push → seed can find real changes
                        if workspace_tar:
                            session_workspace.save_baseline(ag_session.id, workspace_tar)
                    except Exception as exc:
                        logger.warning(
                            "Session %s workspace clone warning for %s: %s",
                            ag_session.id,
                            repo.full_name,
                            exc,
                        )

            await client.delete(
                f"{base_url}/containers/{quote(container_name, safe='')}?force=true"
            )
            create_resp = await client.post(
                f"{base_url}/containers/create?name={quote(container_name, safe='')}",
                json=create_payload,
            )
            if create_resp.status_code not in (200, 201):
                logger.warning(
                    "Could not create warm session container (HTTP %s): %s",
                    create_resp.status_code,
                    create_resp.text[:200],
                )
                return None, None
            cid = str(create_resp.json().get("Id") or container_name)

            if workspace_tar:
                wrapped_buf = io.BytesIO()
                with tarfile.open(fileobj=wrapped_buf, mode="w") as outer_tar:
                    ws_info = tarfile.TarInfo(name="workspace")
                    ws_info.type = tarfile.DIRTYPE
                    ws_info.mode = 0o755
                    outer_tar.addfile(_runner_owned(ws_info))
                    ses_info = tarfile.TarInfo(name="tmp/gitseed_ses")
                    ses_info.type = tarfile.DIRTYPE
                    ses_info.mode = 0o755
                    outer_tar.addfile(_runner_owned(ses_info))
                    with tarfile.open(
                        fileobj=io.BytesIO(workspace_tar), mode="r"
                    ) as inner_tar:
                        for member in inner_tar.getmembers():
                            rel_member = _strip_dot_slash(member.name)
                            if not rel_member or rel_member == ".":
                                continue
                            member_copy = member
                            member_copy.name = f"workspace/{rel_member}"
                            extracted = (
                                inner_tar.extractfile(member)
                                if member.isfile()
                                else None
                            )
                            outer_tar.addfile(_runner_owned(member_copy), extracted)

                await client.put(
                    f"{base_url}/containers/{quote(cid, safe='')}/archive",
                    params={"path": "/"},
                    content=wrapped_buf.getvalue(),
                    headers={"Content-Type": "application/x-tar"},
                    timeout=60.0,
                )

            start_resp = await client.post(
                f"{base_url}/containers/{quote(cid, safe='')}/start"
            )
            if start_resp.status_code not in (200, 204, 304):
                logger.warning(
                    "Could not start warm session container (HTTP %s): %s",
                    start_resp.status_code,
                    start_resp.text[:200],
                )
                return None, None

            # Fix ownership inside /workspace for runner user
            exec_create = await client.post(
                f"{base_url}/containers/{quote(cid, safe='')}/exec",
                json={
                    "AttachStdout": False,
                    "AttachStderr": False,
                    "User": "0",
                    "Cmd": ["sh", "-c", "chown -R runner:runner /workspace /tmp/gitseed_ses 2>/dev/null || true"],
                },
            )
            if exec_create.status_code in (200, 201):
                eid = exec_create.json().get("Id")
                if eid:
                    await client.post(
                        f"{base_url}/exec/{quote(str(eid), safe='')}/start",
                        json={"Detach": False, "Tty": False},
                    )

            ag_session.container_id = cid
            ag_session.compute_backend_id = compute_backend.id
            ag_session.ref = chosen_ref
            if head_sha:
                ag_session.head_sha = head_sha
            _CONTAINER_TURN_COUNT[cid] = 0
            await session.commit()
            return compute_backend, cid
    except Exception as exc:
        logger.warning("Warm container provisioning skipped for %s: %s", ag_session.id, exc)
        return None, None


STREAM_READER_LIMIT_BYTES = 16 * 1024 * 1024


async def _stream_agent_stdout(
    stdout: asyncio.StreamReader,
    on_line: Any = None,
    *,
    chunk_size: int = 65536,
) -> list[str]:
    """Consume CLI `stream-json` stdout lines safely, draining oversized lines past `\\n`."""
    collected: list[str] = []
    pending_prefix = b""

    async def _emit(raw_bytes: bytes) -> None:
        text_line = raw_bytes.decode("utf-8", errors="replace").rstrip("\r\n")
        collected.append(text_line)
        if on_line is not None:
            res = on_line(text_line)
            if asyncio.iscoroutine(res):
                await res

    while True:
        try:
            raw = await stdout.readline()
        except (asyncio.LimitOverrunError, ValueError) as exc:
            pending_prefix = b""
            # When StreamReader.readline() sees '\n' beyond limit, it already removed
            # bytes up to and including '\n' and raised ValueError("Separator is found...").
            # Otherwise ('Separator is not found...' or direct LimitOverrunError), drain to '\n'.
            if isinstance(exc, asyncio.LimitOverrunError) or "Separator is found" not in str(exc):
                while True:
                    chunk = await stdout.read(chunk_size)
                    if not chunk:
                        break
                    nl_idx = chunk.find(b"\n")
                    if nl_idx != -1:
                        remainder = chunk[nl_idx + 1 :]
                        if remainder:
                            parts = remainder.split(b"\n")
                            for complete_part in parts[:-1]:
                                await _emit(complete_part)
                            pending_prefix = parts[-1]
                        break
            continue

        if not raw:
            if pending_prefix:
                await _emit(pending_prefix)
                pending_prefix = b""
            break

        if pending_prefix:
            raw = pending_prefix + raw
            pending_prefix = b""
        await _emit(raw)

    return collected


_CONTAINER_TURN_RUNNER_SCRIPT = r"""
import asyncio
import json
import os
from pathlib import Path
from gitseed_runner.adapters import create_runtime_adapter
from gitseed_runner.main import _format_cli_stream_line

STREAM_READER_LIMIT_BYTES = 16 * 1024 * 1024


async def _stream_agent_stdout(stdout, on_line, chunk_size=65536):
    pending_prefix = b""
    while True:
        try:
            raw = await stdout.readline()
        except (asyncio.LimitOverrunError, ValueError) as exc:
            pending_prefix = b""
            if isinstance(exc, asyncio.LimitOverrunError) or "Separator is found" not in str(exc):
                while True:
                    chunk = await stdout.read(chunk_size)
                    if not chunk:
                        break
                    nl_idx = chunk.find(b"\n")
                    if nl_idx != -1:
                        remainder = chunk[nl_idx + 1:]
                        if remainder:
                            parts = remainder.split(b"\n")
                            for complete_part in parts[:-1]:
                                on_line(complete_part.decode("utf-8", errors="replace").rstrip("\r\n"))
                            pending_prefix = parts[-1]
                        break
            continue
        if not raw:
            if pending_prefix:
                on_line(pending_prefix.decode("utf-8", errors="replace").rstrip("\r\n"))
                pending_prefix = b""
            break
        if pending_prefix:
            raw = pending_prefix + raw
            pending_prefix = b""
        on_line(raw.decode("utf-8", errors="replace").rstrip("\r\n"))


async def main():
    ctx_path = Path("/tmp/gitseed_ses/turn_ctx.json")
    log_path = Path("/tmp/gitseed_ses/turn.log")
    res_path = Path("/tmp/gitseed_ses/turn_result.json")
    log_path.write_text("", encoding="utf-8")
    if res_path.exists():
        res_path.unlink()

    ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
    working_dir = Path("/workspace")
    output_file = Path("/tmp/gitseed_ses/output.json")
    adapter = create_runtime_adapter(ctx=ctx, working_dir=working_dir, output_file=output_file)
    env = adapter.prepare_workspace()
    prompt = str(ctx.get("prompt") or "")
    continue_session = bool(ctx.get("continue_session", False))

    if not adapter.is_binary_available():
        res_path.write_text(json.dumps({
            "exit_code": 127,
            "reply": "",
            "tool_log": f"[agent] CLI binary '{adapter.binary_name}' not installed; using gateway.",
            "input_tokens": 0,
            "output_tokens": 0,
        }), encoding="utf-8")
        return

    cmd = adapter.build_command(prompt, continue_session=continue_session)
    with log_path.open("a", encoding="utf-8") as lf:
        lf.write(f"[agent] Running @{ctx.get('agent_handle', 'agent')} ({ctx.get('runtime')}) in /workspace...\n")
        lf.flush()

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(working_dir),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        limit=STREAM_READER_LIMIT_BYTES,
    )

    formatted_lines = []
    narrative_lines = []
    assert proc.stdout is not None

    def _handle_line(line: str) -> None:
        fmt = _format_cli_stream_line(line, adapter)
        if fmt is not None:
            formatted_lines.append(fmt)
            with log_path.open("a", encoding="utf-8") as lf:
                lf.write(fmt + "\n")
                lf.flush()
            if not (
                fmt.startswith("[tool]")
                or fmt.startswith("[tool:ok]")
                or fmt.startswith("[tool:error]")
                or fmt.startswith("[thinking]")
                or fmt.startswith("[agent]")
                or fmt.startswith("[agent:error]")
            ):
                narrative_lines.append(fmt)

    await _stream_agent_stdout(proc.stdout, _handle_line)

    rc = await proc.wait()
    full_log = Path(log_path).read_text(encoding="utf-8", errors="ignore")
    reply_text = "\n".join(narrative_lines).strip()
    if output_file.exists() and not reply_text:
        try:
            reply_text = output_file.read_text(encoding="utf-8", errors="ignore").strip()
        except Exception:
            pass

    res_path.write_text(json.dumps({
        "exit_code": rc,
        "reply": reply_text,
        "tool_log": full_log,
        "input_tokens": adapter.input_tokens,
        "output_tokens": adapter.output_tokens,
        "updated_cli_auth_bundle": adapter.collect_updated_cli_auth_bundle(),
    }), encoding="utf-8")

asyncio.run(main())
"""


async def _read_container_file(
    client: httpx.AsyncClient,
    base_url: str,
    container_id: str,
    file_path: str,
) -> str:
    try:
        resp = await client.get(
            f"{base_url}/containers/{quote(container_id, safe='')}/archive?path={quote(file_path, safe='')}"
        )
        if resp.status_code != 200:
            return ""
        buf = io.BytesIO(resp.content)
        with tarfile.open(fileobj=buf, mode="r:*") as tar:
            for member in tar.getmembers():
                if member.isfile():
                    f = tar.extractfile(member)
                    if f is not None:
                        return f.read().decode("utf-8", errors="replace")
    except Exception:
        pass
    return ""


async def _run_turn_in_warm_container(
    *,
    session_id: str,
    compute_backend: Backend,
    container_id: str,
    turn_ctx: dict[str, Any],
) -> dict[str, Any] | None:
    """Execute one interactive agent CLI turn inside the warm container and stream live tool logs."""
    ctx_bytes = json.dumps(turn_ctx, indent=2).encode("utf-8")
    script_bytes = _CONTAINER_TURN_RUNNER_SCRIPT.encode("utf-8")

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        dir_info = tarfile.TarInfo(name="tmp/gitseed_ses")
        dir_info.type = tarfile.DIRTYPE
        dir_info.mode = 0o777
        tar.addfile(_runner_owned(dir_info))
        for name, b_data in (
            ("tmp/gitseed_ses/turn_ctx.json", ctx_bytes),
            ("tmp/gitseed_ses/run_turn.py", script_bytes),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(b_data)
            info.mode = 0o600 if name.endswith("_ctx.json") else 0o644
            tar.addfile(_runner_owned(info), io.BytesIO(b_data))

    if is_k8s_backend(compute_backend):
        try:
            await k8s_put_archive(compute_backend, container_id, "/", buf.getvalue(), timeout=30.0)
            await k8s_exec_command(
                compute_backend,
                container_id,
                ["sh", "-c", "cd /workspace && nohup python3 /tmp/gitseed_ses/run_turn.py >/dev/null 2>&1 &"],
                timeout=15.0,
            )
            for _ in range(150):
                await asyncio.sleep(1.2)
                rc_log, out_log, _ = await k8s_exec_command(
                    compute_backend,
                    container_id,
                    ["sh", "-c", "cat /tmp/gitseed_ses/turn.log 2>/dev/null || true"],
                    timeout=10.0,
                )
                live_log = out_log.decode("utf-8", errors="replace") if rc_log == 0 else ""
                if live_log.strip():
                    async with AsyncSessionLocal() as poll_db:
                        s_row = await poll_db.get(AgentSession, session_id)
                        if s_row is not None and s_row.status == AgentSessionStatus.RUNNING_TURN:
                            s_row.live_turn_log = live_log[-24000:]
                            s_row.updated_at = datetime.now(UTC)
                            await poll_db.commit()

                rc_res, out_res, _ = await k8s_exec_command(
                    compute_backend,
                    container_id,
                    ["sh", "-c", "cat /tmp/gitseed_ses/turn_result.json 2>/dev/null"],
                    timeout=10.0,
                )
                if rc_res == 0 and out_res.strip():
                    parsed = json.loads(out_res.decode("utf-8", errors="replace"))
                    if isinstance(parsed, dict):
                        _CONTAINER_TURN_COUNT[container_id] = (
                            _CONTAINER_TURN_COUNT.get(container_id, 0) + 1
                        )
                        await session_workspace.snapshot_k8s_workspace(
                            compute_backend, container_id, session_id
                        )
                        return parsed
        except Exception as exc:
            logger.warning("K8s warm Pod turn execution error for %s: %s", session_id, exc)
        return None

    client, base_url = _docker_client_for_backend(compute_backend)
    try:
        async with client:
            put_resp = await client.put(
                f"{base_url}/containers/{quote(container_id, safe='')}/archive",
                params={"path": "/"},
                content=buf.getvalue(),
                headers={"Content-Type": "application/x-tar"},
            )
            if put_resp.status_code not in (200, 204):
                return None

            exec_resp = await client.post(
                f"{base_url}/containers/{quote(container_id, safe='')}/exec",
                json={
                    "AttachStdout": False,
                    "AttachStderr": False,
                    "WorkingDir": "/workspace",
                    "Cmd": ["python3", "/tmp/gitseed_ses/run_turn.py"],
                },
            )
            if exec_resp.status_code not in (200, 201):
                return None
            exec_id = str(exec_resp.json().get("Id") or "")
            if not exec_id:
                return None

            await client.post(
                f"{base_url}/exec/{quote(exec_id, safe='')}/start",
                json={"Detach": True, "Tty": False},
            )

            # Poll exec status & live turn.log up to 180 seconds
            for _ in range(150):
                await asyncio.sleep(1.2)
                live_log = await _read_container_file(
                    client, base_url, container_id, "/tmp/gitseed_ses/turn.log"
                )
                if live_log.strip():
                    async with AsyncSessionLocal() as poll_db:
                        s_row = await poll_db.get(AgentSession, session_id)
                        if s_row is not None and s_row.status == AgentSessionStatus.RUNNING_TURN:
                            s_row.live_turn_log = live_log[-24000:]
                            s_row.updated_at = datetime.now(UTC)
                            await poll_db.commit()

                insp = await client.get(
                    f"{base_url}/exec/{quote(exec_id, safe='')}/json"
                )
                if insp.status_code == 200 and not insp.json().get("Running", False):
                    break

            res_raw = await _read_container_file(
                client, base_url, container_id, "/tmp/gitseed_ses/turn_result.json"
            )
            if res_raw.strip():
                parsed = json.loads(res_raw)
                if isinstance(parsed, dict):
                    _CONTAINER_TURN_COUNT[container_id] = (
                        _CONTAINER_TURN_COUNT.get(container_id, 0) + 1
                    )
                    return parsed
    except Exception as exc:
        logger.warning("Warm container turn execution error for %s: %s", session_id, exc)
    return None


def _extract_referenced_files_from_log(tool_log: str) -> list[str]:
    if not tool_log:
        return []
    found: list[str] = []
    for m in re.findall(r"\[tool\]\s+[A-Za-z0-9_.:-]+\(([^)\n]+)\)", tool_log):
        candidate = m.strip().strip("'\"")
        if (
            candidate
            and "/" in candidate
            and " " not in candidate
            and not candidate.startswith("-")
        ):
            clean = candidate.removeprefix("/workspace/").lstrip("/")
            if clean and clean not in found:
                found.append(clean)
    return found[:12]


async def _execute_session_turn_background(
    session_id: str,
    actor_id: str,
    propose_seeds: bool,
) -> None:
    """Background task executing an interactive AgentSession turn in the warm container or gateway."""
    try:
        async with AsyncSessionLocal() as session:
            ag_session = await session.get(AgentSession, session_id)
            if ag_session is None:
                return
            project = await session.get(Project, ag_session.project_id)
            agent = await session.get(Agent, ag_session.agent_id)
            actor = await session.get(User, actor_id)
            if project is None or agent is None or actor is None:
                ag_session.status = AgentSessionStatus.FAILED
                ag_session.error = "Project, agent, or user no longer exists"
                await session.commit()
                return

            msg_rows = list(
                (
                    await session.execute(
                        select(AgentSessionMessage)
                        .where(AgentSessionMessage.session_id == session_id)
                        .order_by(col(AgentSessionMessage.created_at).asc())
                    )
                )
                .scalars()
                .all()
            )
            user_messages = [m for m in msg_rows if m.role == "user"]
            latest_user_msg = user_messages[-1].content if user_messages else ""

            # Auto-title session from first user message if still default
            if (
                ag_session.title in ("New Agent Session", "Untitled Session")
                or _AUTO_TITLE_RE.match(ag_session.title or "")
            ) and latest_user_msg and len(user_messages) == 1:
                first_line = latest_user_msg.strip().splitlines()[0].strip()
                ag_session.title = (
                    first_line[:72] + ("…" if len(first_line) > 72 else "")
                ) or "Agent Session"

            ag_session.status = AgentSessionStatus.RUNNING_TURN
            ag_session.error = None
            ag_session.live_turn_log = (
                f"[agent] Preparing @{agent.handle} ({ag_session.runtime}) workspace for {project.slug}@{ag_session.ref}..."
            )
            ag_session.updated_at = datetime.now(UTC)
            await session.commit()

            # Resolve Models backend & credentials
            models_backend, resolved_model = await _resolve_session_models_backend(
                session,
                agent=agent,
                project=project,
                models_backend_id=ag_session.models_backend_id,
                requested_model=ag_session.model,
                actor_user_id=actor.id,
            )
            if resolved_model and resolved_model != "default":
                ag_session.model = resolved_model

            # Resolve Skills for the agent
            skill_blocks: list[str] = []
            if agent.skills:
                sk_rows = (
                    await session.execute(
                        select(Skill).where(
                            col(Skill.archived_at).is_(None),
                            (col(Skill.id).in_(agent.skills))
                            | (col(Skill.slug).in_(agent.skills)),
                        )
                    )
                ).scalars().all()
                for sk in sk_rows:
                    skill_blocks.append(f"### Skill: {sk.name}\n{sk.content}")

            # Build rich repository & seed context
            brainstorm_ctx, referenced_files, _, project_seeds = (
                await _build_brainstorm_context(
                    session,
                    actor,
                    focused_project=project,
                    messages=msg_rows,
                )
            )

            seed_proposal_instructions = (
                "\n\n## Seed Proposal Output Format\n"
                "When the user asks to propose seeds, break work into seeds, or `propose_seeds` is requested, "
                "include a `<seed_proposals>` JSON array at the end of your response:\n"
                "<seed_proposals>\n"
                '[\n  {\n    "title": "Concise actionable seed title",\n'
                '    "description": "1-2 sentence executive summary",\n'
                '    "specification": "Detailed Markdown technical specification citing exact repository files",\n'
                '    "acceptance_criteria": ["Testable criterion 1", "Testable criterion 2"],\n'
                '    "tags": ["feature"],\n'
                '    "autonomy": "supervised"\n  }\n]\n'
                "</seed_proposals>"
            )

            history_transcript = "\n\n".join(
                f"**{'User' if m.role == 'user' else f'@{agent.handle}'}**: {m.content}"
                for m in msg_rows[:-1][-10:]
            )

            system_persona = (
                f"You are @{agent.handle} ({agent.name}), running an interactive Live Agent Session "
                f"inside the `{project.name}` (`{project.slug}`) repository checked out at branch `{ag_session.ref}` in `/workspace`.\n"
                f"Agent Instructions: {agent.instructions or 'Assist the developer with codebase exploration, architecture, debugging, and formulating high-signal GitSeed seeds.'}\n"
            )
            if skill_blocks:
                system_persona += "\n## Attached Agent Skills\n" + "\n\n".join(skill_blocks) + "\n"
            if brainstorm_ctx:
                system_persona += "\n" + brainstorm_ctx + "\n"
            system_persona += seed_proposal_instructions

            cli_prompt_parts = [system_persona]
            if history_transcript:
                cli_prompt_parts.append(f"## Prior Session History\n{history_transcript}")
            cli_prompt_parts.append(f"## Current User Request\n{latest_user_msg}")
            if propose_seeds:
                cli_prompt_parts.append(
                    "IMPORTANT: The user clicked 'Propose Seeds'. Provide your architectural synthesis AND include a `<seed_proposals>[...]</seed_proposals>` JSON block with 1 to 4 concrete, well-scoped Seed drafts."
                )
            full_cli_prompt = "\n\n".join(cli_prompt_parts)

            # 1. Try executing inside warm Docker container with the agent's CLI runtime
            compute_backend, container_id = await _ensure_warm_session_container(
                session, ag_session, project
            )

            reply_text = ""
            tool_log = ""
            in_tok = 0
            out_tok = 0

            linked_seed = (
                await session.get(Seed, ag_session.seed_id)
                if ag_session.seed_id
                else None
            )
            org_settings = await session.get(OrgSettings, 1)
            remaining_proj_budget = await compute_project_remaining_monthly_budget(
                session, project
            )
            if remaining_proj_budget is not None and remaining_proj_budget <= Decimal("0"):
                raise Invalid("Project monthly budget exhausted")

            if compute_backend is not None and container_id is not None and models_backend is not None:
                already_ran_turns = _CONTAINER_TURN_COUNT.get(container_id, 0) > 0
                mcp_servers = await _resolve_session_mcp_servers(session, project, agent.id)
                turn_ctx = {
                    "runtime": str(ag_session.runtime),
                    "agent_handle": agent.handle,
                    "model": ag_session.model,
                    "continue_session": already_ran_turns,
                    "prompt": (
                        latest_user_msg
                        + (
                            "\n\nPlease also output a `<seed_proposals>[...]</seed_proposals>` JSON block with concrete Seed drafts based on our discussion."
                            if propose_seeds
                            else ""
                        )
                        if already_ran_turns
                        else full_cli_prompt
                    ),
                    "gateway": await _gateway_ctx(
                        ag_session,
                        models_backend,
                        ag_session.model,
                        actor_user_id=actor.id,
                        seed=linked_seed,
                        project=project,
                        org=org_settings,
                        session=session,
                        remaining_project_budget=remaining_proj_budget,
                    ),
                    "mcp_servers": mcp_servers,
                }

                cli_res = await _run_turn_in_warm_container(
                    session_id=ag_session.id,
                    compute_backend=compute_backend,
                    container_id=container_id,
                    turn_ctx=turn_ctx,
                )
                if cli_res is not None:
                    reply_text = str(cli_res.get("reply") or "").strip()
                    tool_log = str(cli_res.get("tool_log") or "").strip()
                    in_tok = int(cli_res.get("input_tokens") or 0)
                    out_tok = int(cli_res.get("output_tokens") or 0)
                    updated_bundle = cli_res.get("updated_cli_auth_bundle")
                    if isinstance(updated_bundle, dict) and updated_bundle:
                        try:
                            filtered_bundle = validate_and_filter_cli_auth_bundle(
                                updated_bundle,
                                runtime=str(ag_session.runtime or ""),
                                provider=models_backend.provider,
                                strict=False,
                            )
                            if filtered_bundle:
                                cur_secrets = decrypt_secrets(models_backend.secrets_encrypted)
                                cur_secrets["cli_auth_bundle"] = json.dumps(filtered_bundle)
                                models_backend.secrets_encrypted = encrypt_secrets(cur_secrets)
                        except Exception:
                            pass

            # 2. If CLI didn't produce a direct narrative reply, synthesize via Models backend / LiteLLM
            if not reply_text and models_backend is not None:
                await sync_backend_to_litellm(models_backend)
                sys_snapshot, _, _ = await _build_workspace_snapshot(
                    session, actor, project_id=project.id
                )
                llm_messages: list[dict[str, str]] = [
                    {
                        "role": "system",
                        "content": f"{system_persona}\n\n{sys_snapshot}"
                        + (
                            f"\n\n## Sandbox Tool Execution Log\n```\n{tool_log[-4000:]}\n```"
                            if tool_log
                            else ""
                        ),
                    }
                ]
                for m in msg_rows[-12:]:
                    llm_messages.append({"role": m.role, "content": m.content})
                if propose_seeds and llm_messages and llm_messages[-1]["role"] == "user":
                    llm_messages[-1]["content"] += (
                        "\n\nPlease synthesize our discussion and include a `<seed_proposals>[...]</seed_proposals>` JSON block with concrete Seed drafts."
                    )

                synth_model = (
                    ag_session.model
                    if ag_session.model != "default"
                    else (extract_enabled_model_ids(models_backend) or ["claude-sonnet-4-5"])[0]
                )
                session_vk = await create_session_virtual_key(
                    session_id=ag_session.id,
                    project_id=ag_session.project_id,
                    agent_id=ag_session.agent_id,
                    backend=models_backend,
                    model=synth_model,
                    ttl_seconds=int(settings.SESSION_MAX_HOURS * 3600) + 600,
                    seed=linked_seed,
                    project=project,
                    org=org_settings,
                    remaining_project_budget=remaining_proj_budget,
                    session=session,
                )
                gw_url = f"{settings.LITELLM_URL.rstrip('/')}/v1/chat/completions"
                litellm_unreachable = False
                budget_blocked = False
                try:
                    async with httpx.AsyncClient(timeout=45.0) as client:
                        resp = await client.post(
                            gw_url,
                            headers={
                                "Authorization": f"Bearer {session_vk}",
                                "Content-Type": "application/json",
                            },
                            json={
                                "model": synth_model,
                                "messages": llm_messages,
                                "temperature": 0.3,
                            },
                        )
                        if resp.status_code == 200:
                            body = resp.json()
                            choices = body.get("choices") or []
                            if choices:
                                reply_text = str(
                                    (choices[0].get("message") or {}).get("content")
                                    or ""
                                ).strip()
                            usage = body.get("usage") or {}
                            in_tok += int(usage.get("prompt_tokens") or 0)
                            out_tok += int(usage.get("completion_tokens") or 0)
                        elif resp.status_code in (400, 402, 429) and "budget" in resp.text.lower():
                            budget_blocked = True
                            raise Invalid("Session budget cap reached on LiteLLM gateway")
                except Invalid:
                    raise
                except Exception as exc:
                    litellm_unreachable = True
                    logger.debug("Gateway synthesis fallback skipped: %s", exc)

                # Direct local-provider fallback ONLY for unpriced local runtimes when LiteLLM is unreachable
                if (
                    not reply_text
                    and litellm_unreachable
                    and not budget_blocked
                    and models_backend.provider.lower() in ("ollama", "lmstudio")
                ):
                    endpoint = docker_host_fallback_url(models_backend.endpoint or "")
                    if endpoint:
                        chat_url = (
                            endpoint.rstrip("/")
                            if endpoint.rstrip("/").endswith("/chat/completions")
                            else f"{endpoint.rstrip('/')}/v1/chat/completions"
                            if not endpoint.rstrip("/").endswith("/v1")
                            else f"{endpoint.rstrip('/')}/chat/completions"
                        )
                        try:
                            async with httpx.AsyncClient(timeout=45.0) as client:
                                resp = await client.post(
                                    chat_url,
                                    headers={"Content-Type": "application/json"},
                                    json={
                                        "model": synth_model,
                                        "messages": llm_messages,
                                        "temperature": 0.3,
                                    },
                                )
                                if resp.status_code == 200:
                                    body = resp.json()
                                    choices = body.get("choices") or []
                                    if choices:
                                        reply_text = str(
                                            (choices[0].get("message") or {}).get(
                                                "content"
                                            )
                                            or ""
                                        ).strip()
                        except Exception:
                            pass

            # 3. Deterministic architectural fallback if no external LLM responded
            proposals_list: list[dict[str, Any]] = []
            if reply_text:
                cleaned_reply, parsed_proposals = _extract_seed_proposals(reply_text)
                reply_text = cleaned_reply
                proposals_list = [p.model_dump(mode="json") for p in parsed_proposals]
                if propose_seeds and not proposals_list:
                    _, fallback_props = _build_fallback_brainstorm_proposals(
                        msg_rows,
                        project,
                        referenced_files,
                        project_seeds,
                        propose_now=True,
                    )
                    proposals_list = [p.model_dump(mode="json") for p in fallback_props]
            else:
                fb_reply, fb_props = _build_fallback_brainstorm_proposals(
                    msg_rows,
                    project,
                    referenced_files,
                    project_seeds,
                    propose_now=propose_seeds,
                )
                reply_text = fb_reply
                proposals_list = [p.model_dump(mode="json") for p in fb_props]

            if not tool_log:
                inspected_str = (
                    ", ".join(f"`{f}`" for f in referenced_files[:5])
                    if referenced_files
                    else f"`{project.slug}@{ag_session.ref}`"
                )
                tool_log = (
                    f"[agent] Workspace session active for {project.slug} ({ag_session.ref})\n"
                    f"[tool] Read({inspected_str})\n"
                    f"[tool:ok] Loaded repository structure and {len(project_seeds)} project seed(s)"
                )

            log_files = _extract_referenced_files_from_log(tool_log)
            merged_files = list(dict.fromkeys([*log_files, *referenced_files]))[:12]

            if in_tok == 0 and out_tok == 0:
                in_tok = max(64, len(full_cli_prompt) // 4)
                out_tok = max(48, len(reply_text) // 4)
            turn_cost = Decimal(str(round((in_tok * 0.000003) + (out_tok * 0.000015), 6)))

            assistant_msg = AgentSessionMessage(
                session_id=ag_session.id,
                role="assistant",
                content=reply_text,
                tool_log=tool_log,
                proposed_seeds=proposals_list,
                referenced_files=merged_files,
                input_tokens=in_tok,
                output_tokens=out_tok,
                cost=turn_cost,
                created_at=datetime.now(UTC),
            )
            session.add(assistant_msg)

            ag_session.status = AgentSessionStatus.IDLE
            ag_session.live_turn_log = None
            ag_session.total_input_tokens = (ag_session.total_input_tokens or 0) + in_tok
            ag_session.total_output_tokens = (ag_session.total_output_tokens or 0) + out_tok
            ag_session.total_cost = (ag_session.total_cost or Decimal("0")) + turn_cost
            ag_session.updated_at = datetime.now(UTC)
            if linked_seed is not None and turn_cost > Decimal("0"):
                linked_seed.spent = (linked_seed.spent or Decimal("0")) + turn_cost
                linked_seed.updated_at = datetime.now(UTC)
            await session.commit()

    except Exception as exc:
        logger.exception("AgentSession turn failed for %s: %s", session_id, exc)
        try:
            async with AsyncSessionLocal() as err_db:
                ag_session = await err_db.get(AgentSession, session_id)
                if ag_session is not None:
                    ag_session.status = AgentSessionStatus.IDLE
                    ag_session.error = str(exc)[:400]
                    ag_session.live_turn_log = None
                    ag_session.updated_at = datetime.now(UTC)
                    await err_db.commit()
        except Exception:
            pass
    finally:
        _ACTIVE_TURN_TASKS.pop(session_id, None)


async def serialize_agent_session(
    session: AsyncSession,
    ag_session: AgentSession,
    *,
    include_messages: bool = True,
) -> dict[str, Any]:
    agent = await session.get(Agent, ag_session.agent_id)
    messages_data: list[dict[str, Any]] = []
    if include_messages:
        rows = (
            await session.execute(
                select(AgentSessionMessage)
                .where(AgentSessionMessage.session_id == ag_session.id)
                .order_by(col(AgentSessionMessage.created_at).asc())
            )
        ).scalars().all()
        for m in rows:
            messages_data.append(
                {
                    "id": m.id,
                    "session_id": m.session_id,
                    "role": m.role,
                    "content": m.content,
                    "tool_log": m.tool_log,
                    "proposed_seeds": m.proposed_seeds or [],
                    "referenced_files": m.referenced_files or [],
                    "input_tokens": m.input_tokens or 0,
                    "output_tokens": m.output_tokens or 0,
                    "cost": m.cost or Decimal("0"),
                    "created_at": m.created_at,
                }
            )

    backend = (
        await session.get(Backend, ag_session.models_backend_id)
        if ag_session.models_backend_id
        else None
    )
    context_window = await resolve_session_context_window(
        ag_session,
        backend=backend,
        messages=messages_data,
    )

    return {
        "id": ag_session.id,
        "project_id": ag_session.project_id,
        "seed_id": ag_session.seed_id,
        "repository_id": ag_session.repository_id,
        "ref": ag_session.ref,
        "head_sha": ag_session.head_sha or "",
        "agent_id": ag_session.agent_id,
        "agent_handle": agent.handle if agent else "agent",
        "agent_name": agent.name if agent else "Agent",
        "runtime": ag_session.runtime,
        "model": ag_session.model,
        "models_backend_id": ag_session.models_backend_id,
        "provider_backend_id": ag_session.models_backend_id,
        "compute_backend_id": ag_session.compute_backend_id,
        "title": ag_session.title,
        "status": ag_session.status,
        "is_saved": bool(ag_session.is_saved),
        "container_id": ag_session.container_id,
        "live_turn_log": ag_session.live_turn_log,
        "error": ag_session.error,
        "total_input_tokens": ag_session.total_input_tokens or 0,
        "total_output_tokens": ag_session.total_output_tokens or 0,
        "total_cost": ag_session.total_cost or Decimal("0"),
        "context_window": context_window,
        "started_by": ag_session.created_by,
        "created_by": ag_session.created_by,
        "created_at": ag_session.created_at,
        "updated_at": ag_session.updated_at,
        "stopped_at": ag_session.updated_at
        if ag_session.status == AgentSessionStatus.STOPPED
        else None,
        "pushed_seed_id": ag_session.pushed_seed_id,
        "messages": messages_data,
    }


async def _purge_idle_empty_sessions(session: AsyncSession, project_id: str) -> None:
    """Sessions with activity are kept automatically; idle sessions with no messages disappear."""
    cutoff = datetime.now(UTC).timestamp() - _EMPTY_SESSION_TTL_SECONDS
    has_messages = (
        select(AgentSessionMessage.id)
        .where(AgentSessionMessage.session_id == AgentSession.id)
        .exists()
    )
    stale = list(
        (
            await session.execute(
                select(AgentSession).where(
                    AgentSession.project_id == project_id,
                    ~has_messages,
                    col(AgentSession.status) != AgentSessionStatus.RUNNING_TURN,
                )
            )
        )
        .scalars()
        .all()
    )
    removed = False
    # Idle chat containers stop after SESSION_IDLE_MINUTES (history is kept; the next message wakes it)
    idle_cutoff = datetime.now(UTC).timestamp() - settings.SESSION_IDLE_MINUTES * 60
    idle_rows = (
        await session.execute(
            select(AgentSession).where(
                AgentSession.project_id == project_id,
                col(AgentSession.container_id).is_not(None),
                col(AgentSession.status) != AgentSessionStatus.RUNNING_TURN,
            )
        )
    ).scalars().all()
    for s in idle_rows:
        ts = s.updated_at or s.created_at
        if ts is None or ts.timestamp() > idle_cutoff or s.id in _ACTIVE_TURN_TASKS:
            continue
        await close_session_vscode(f"agent:{s.id}")
        backend = await session.get(Backend, s.compute_backend_id) if s.compute_backend_id else None
        if backend is not None and s.container_id:
            try:
                await session_workspace.snapshot_k8s_workspace(backend, s.container_id, s.id)
                await stop_and_remove_docker_container(backend, s.container_id)
            except Exception:
                pass
        if s.container_id:
            _CONTAINER_TURN_COUNT.pop(s.container_id, None)
        s.container_id = None
        s.status = AgentSessionStatus.STOPPED
        removed = True
    for s in stale:
        ts = s.updated_at or s.created_at
        if ts is None or ts.timestamp() > cutoff or s.id in _ACTIVE_TURN_TASKS:
            continue
        await close_session_vscode(f"agent:{s.id}")
        if s.compute_backend_id:
            backend = await session.get(Backend, s.compute_backend_id)
            if backend is not None:
                if s.container_id:
                    try:
                        await stop_and_remove_docker_container(backend, s.container_id)
                    except Exception:
                        pass
                try:
                    await remove_docker_session_workspace_volume(backend, s.id)
                except Exception:
                    pass
        if s.container_id:
            _CONTAINER_TURN_COUNT.pop(s.container_id, None)
        session_workspace.remove_session_data(s.id)
        await session.delete(s)
        removed = True
    if removed:
        await session.commit()


async def list_agent_sessions(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    saved_only: bool = False,
    limit: int = 30,
) -> list[dict[str, Any]]:
    project = await _require_project_access(session, actor, project_id_or_slug)
    try:
        await _purge_idle_empty_sessions(session, project.id)
    except Exception as exc:
        logger.debug("Empty session purge skipped: %s", exc)
        await session.rollback()
    stmt = select(AgentSession).where(AgentSession.project_id == project.id)
    if saved_only:
        stmt = stmt.where(col(AgentSession.is_saved).is_(True))
    stmt = stmt.order_by(
        col(AgentSession.updated_at).desc(),
    ).limit(limit)
    rows = list((await session.execute(stmt)).scalars().all())
    return [
        await serialize_agent_session(session, r, include_messages=True)
        for r in rows
    ]


async def get_agent_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    return await serialize_agent_session(session, ag_session, include_messages=True)


async def create_agent_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    agent_id: str | None = None,
    runtime: AgentRuntime | None = None,
    models_backend_id: str | None = None,
    model: str | None = None,
    repository_id: int | None = None,
    seed_id: str | None = None,
    ref: str | None = None,
    title: str | None = None,
    is_saved: bool = False,
    initial_message: str | None = None,
    propose_seeds: bool = False,
) -> dict[str, Any]:
    project = await _require_project_access(
        session, actor, project_id_or_slug, require_write=True
    )
    if getattr(project, "monthly_budget", None) is not None:
        rem_proj = await compute_project_remaining_monthly_budget(session, project)
        if rem_proj is not None and rem_proj <= Decimal("0"):
            raise Invalid("Project monthly budget exhausted")
    agent = await _resolve_session_agent(session, agent_id)
    models_backend, resolved_model = await _resolve_session_models_backend(
        session,
        agent=agent,
        project=project,
        models_backend_id=models_backend_id,
        requested_model=model,
        actor_user_id=actor.id,
    )

    if seed_id is not None:
        seed_row = await session.get(Seed, seed_id)
        if seed_row is None or (
            isinstance(seed_row, Seed) and seed_row.project_id != project.id
        ):
            raise NotFound("Seed not found in this project")

    # Resolve repository and default branch ref
    repo: ProjectRepository | None = None
    if repository_id is not None:
        repo = await session.get(ProjectRepository, repository_id)
        if repo is None or repo.project_id != project.id:
            raise NotFound("Repository not found in this project")
    else:
        repos = (
            await session.execute(
                select(ProjectRepository)
                .where(ProjectRepository.project_id == project.id)
                .order_by(
                    col(ProjectRepository.is_primary).desc(),
                    col(ProjectRepository.id).asc(),
                )
            )
        ).scalars().all()
        repo = repos[0] if repos else None

    chosen_ref = (ref or (repo.default_branch if repo else "main") or "main").strip()
    now = datetime.now(UTC)

    clean_initial = (initial_message or "").strip()
    resolved_title = (title or "").strip()
    if not resolved_title:
        if clean_initial:
            first_line = clean_initial.splitlines()[0].strip()
            resolved_title = first_line[:72] + ("…" if len(first_line) > 72 else "")
        else:
            resolved_title = "New Agent Session"

    ag_session = AgentSession(
        project_id=project.id,
        seed_id=seed_id,
        repository_id=repo.id if repo else None,
        ref=chosen_ref,
        head_sha="",
        agent_id=agent.id,
        runtime=runtime or agent.runtime,
        model=resolved_model or agent.model or "default",
        models_backend_id=models_backend.id if models_backend else models_backend_id,
        title=resolved_title,
        status=(
            AgentSessionStatus.RUNNING_TURN
            if clean_initial
            else AgentSessionStatus.STARTING
        ),
        is_saved=bool(is_saved),
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(ag_session)
    await session.flush()

    if clean_initial:
        user_msg = AgentSessionMessage(
            session_id=ag_session.id,
            role="user",
            content=clean_initial,
            created_at=now,
        )
        session.add(user_msg)

    await session.commit()
    await session.refresh(ag_session)

    if clean_initial:
        task = asyncio.create_task(
            _execute_session_turn_background(
                ag_session.id, actor.id, propose_seeds=propose_seeds
            )
        )
        _ACTIVE_TURN_TASKS[ag_session.id] = task
    else:
        # Warm container in the background; the session shows "Starting" until it's ready
        asyncio.create_task(_warm_session_background(ag_session.id))

    return await serialize_agent_session(session, ag_session, include_messages=True)


async def _warm_session_background(session_id: str) -> None:
    """Provision the warm container, then mark the session ready (IDLE).

    If no container can be started (e.g. no Docker compute), the session still becomes
    ready: turns fall back to the Models gateway.
    """
    try:
        async with AsyncSessionLocal() as bg_db:
            s_obj = await bg_db.get(AgentSession, session_id)
            p_obj = await bg_db.get(Project, s_obj.project_id) if s_obj else None
            if s_obj and p_obj:
                try:
                    await _ensure_warm_session_container(bg_db, s_obj, p_obj)
                except Exception as exc:
                    logger.debug("Warm-up failed for %s: %s", session_id, exc)
                s_obj = await bg_db.get(AgentSession, session_id)
                if s_obj is not None and s_obj.status == AgentSessionStatus.STARTING:
                    s_obj.status = AgentSessionStatus.IDLE
                    s_obj.updated_at = datetime.now(UTC)
                    await bg_db.commit()
    except Exception:
        pass


async def send_agent_session_message(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
    *,
    content: str,
    propose_seeds: bool = False,
) -> dict[str, Any]:
    project = await _require_project_access(
        session, actor, project_id_or_slug, require_write=True
    )
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")

    if ag_session.models_backend_id:
        existing_mb = await session.get(Backend, ag_session.models_backend_id)
        if existing_mb is not None:
            validate_personal_mode_backend_access(
                session, existing_mb, actor_user_id=actor.id
            )

    if ag_session.status == AgentSessionStatus.RUNNING_TURN:
        raise Invalid("Agent is currently processing the previous message")

    if getattr(project, "monthly_budget", None) is not None:
        rem_proj = await compute_project_remaining_monthly_budget(session, project)
        if rem_proj is not None and rem_proj <= Decimal("0"):
            raise Invalid("Project monthly budget exhausted")

    clean_content = content.strip()
    if not clean_content:
        raise Invalid("Message content cannot be empty")

    now = datetime.now(UTC)
    user_msg = AgentSessionMessage(
        session_id=ag_session.id,
        role="user",
        content=clean_content,
        created_at=now,
    )
    session.add(user_msg)
    ag_session.status = AgentSessionStatus.RUNNING_TURN
    ag_session.error = None
    ag_session.live_turn_log = "[agent] Starting turn..."
    ag_session.updated_at = now
    await session.commit()
    await session.refresh(ag_session)

    task = asyncio.create_task(
        _execute_session_turn_background(
            ag_session.id, actor.id, propose_seeds=propose_seeds
        )
    )
    _ACTIVE_TURN_TASKS[ag_session.id] = task

    return await serialize_agent_session(session, ag_session, include_messages=True)


async def update_agent_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
    *,
    title: str | None = None,
    is_saved: bool | None = None,
    agent_id: str | None = None,
) -> dict[str, Any]:
    project = await _require_project_access(
        session, actor, project_id_or_slug, require_write=True
    )
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")

    if title is not None and title.strip():
        ag_session.title = title.strip()[:160]
    if is_saved is not None:
        ag_session.is_saved = bool(is_saved)
    if agent_id and agent_id != ag_session.agent_id:
        if ag_session.status == AgentSessionStatus.RUNNING_TURN:
            raise Invalid("Wait for the current turn to finish before switching agents")
        new_agent = await _resolve_session_agent(session, agent_id)
        ag_session.agent_id = new_agent.id
        ag_session.runtime = new_agent.runtime
        ag_session.model = new_agent.model or "default"
        ag_session.models_backend_id = None
        # Same warm container, but the next turn must send full history to the new CLI
        if ag_session.container_id:
            _CONTAINER_TURN_COUNT[ag_session.container_id] = 0
        # The running CLI belongs to the old agent; the next terminal attach launches the new one
        await session_pty.close_live_pty(f"agent:{ag_session.id}")
        if _AUTO_TITLE_RE.match(ag_session.title or ""):
            ag_session.title = re.sub(
                r"@[\w.-]+", f"@{new_agent.handle}", ag_session.title, count=1
            )
    ag_session.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(ag_session)
    return await serialize_agent_session(session, ag_session, include_messages=True)


async def stop_agent_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
) -> dict[str, Any]:
    project = await _require_project_access(
        session, actor, project_id_or_slug, require_write=True
    )
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")

    bg_task = _ACTIVE_TURN_TASKS.pop(ag_session.id, None)
    if bg_task is not None and not bg_task.done():
        bg_task.cancel()

    await close_session_vscode(f"agent:{ag_session.id}")
    if ag_session.container_id and ag_session.compute_backend_id:
        compute_backend = await session.get(Backend, ag_session.compute_backend_id)
        if compute_backend is not None:
            await session_workspace.snapshot_k8s_workspace(
                compute_backend, ag_session.container_id, ag_session.id
            )
            await stop_and_remove_docker_container(
                compute_backend, ag_session.container_id
            )
        _CONTAINER_TURN_COUNT.pop(ag_session.container_id, None)
        ag_session.container_id = None

    ag_session.status = AgentSessionStatus.STOPPED
    ag_session.live_turn_log = None
    ag_session.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(ag_session)
    return await serialize_agent_session(session, ag_session, include_messages=True)


async def delete_agent_session(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
) -> None:
    """Stop any running sandbox container and permanently delete ('throw away') the session."""
    project = await _require_project_access(
        session, actor, project_id_or_slug, require_write=True
    )
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")

    bg_task = _ACTIVE_TURN_TASKS.pop(ag_session.id, None)
    if bg_task is not None and not bg_task.done():
        bg_task.cancel()

    await close_session_vscode(f"agent:{ag_session.id}")
    if ag_session.compute_backend_id:
        compute_backend = await session.get(Backend, ag_session.compute_backend_id)
        if compute_backend is not None:
            if ag_session.container_id:
                await stop_and_remove_docker_container(
                    compute_backend, ag_session.container_id
                )
            await remove_docker_session_workspace_volume(compute_backend, ag_session.id)
    if ag_session.container_id:
        _CONTAINER_TURN_COUNT.pop(ag_session.container_id, None)
    await revoke_session_virtual_key(ag_session.id)
    await session_pty.close_live_pty(f"agent:{ag_session.id}")

    await session.execute(
        delete(AgentSessionMessage).where(
            AgentSessionMessage.session_id == ag_session.id
        )
    )
    session_workspace.remove_session_data(ag_session.id)
    await session.delete(ag_session)
    await session.commit()


async def open_agent_session_vscode(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
    *,
    theme: str | None = None,
) -> dict[str, Any]:
    """Wake/ensure the session container and start on-demand VS Code Web (`openvscode-server`)."""
    project = await _require_project_access(
        session, actor, project_id_or_slug, require_write=True
    )
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")

    if ag_session.models_backend_id:
        existing_mb = await session.get(Backend, ag_session.models_backend_id)
        if existing_mb is not None:
            validate_personal_mode_backend_access(
                session, existing_mb, actor_user_id=actor.id
            )

    for _ in range(120):
        if ag_session.status != AgentSessionStatus.STARTING:
            break
        await asyncio.sleep(1)
        await session.refresh(ag_session)

    compute_backend, container_id = await _ensure_warm_session_container(
        session, ag_session, project
    )
    if compute_backend is None or container_id is None:
        raise Invalid("Could not start the session container")
    if ag_session.status in (AgentSessionStatus.STOPPED, AgentSessionStatus.FAILED):
        ag_session.status = AgentSessionStatus.IDLE
        ag_session.error = None

    res = await ensure_session_vscode(
        session_key=f"agent:{ag_session.id}",
        backend=compute_backend,
        container_id=container_id,
        kind="chat",
        session_id=ag_session.id,
        project_id=project.id,
        ref=ag_session.ref or "main",
        head_sha=ag_session.head_sha or "",
        theme=theme,
        on_activity=lambda: touch_agent_activity(ag_session.id),
    )
    ag_session.updated_at = datetime.now(UTC)
    await session.commit()
    return res



# ---------------------------------------------------------------------------
# Agent terminal UI (PTY) and Push → seed for chat sessions
# ---------------------------------------------------------------------------

_TUI_LAUNCH_SCRIPT = r"""
import json, os, shutil
from pathlib import Path
from gitseed_runner.adapters import create_runtime_adapter

ctx_path = Path("/tmp/gitseed_ses/tui_ctx.json")
ctx = json.loads(ctx_path.read_text(encoding="utf-8"))
try:
    ctx_path.unlink()
except OSError:
    pass
adapter = create_runtime_adapter(
    ctx=ctx, working_dir=Path("/workspace"), output_file=Path("/tmp/gitseed_ses/tui_output.json")
)
env = adapter.prepare_workspace()
env["TERM"] = os.environ.get("TERM", "xterm-256color")
env["COLORTERM"] = os.environ.get("COLORTERM", "truecolor")
binary = adapter.binary_name
os.chdir("/workspace")
if not adapter.is_binary_available():
    print(f"\x1b[33m{binary} is not installed in this image; opening a shell instead.\x1b[0m")
    shell = shutil.which("bash") or "sh"
    os.execvpe(shell, [shell, "-l"], env)
cmd = adapter.build_interactive_command()
os.execvpe(cmd[0], cmd, env)
"""


async def prepare_agent_pty(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
) -> tuple[Backend, str, bool, list[str]]:
    """Ensure the warm container and stage the agent CLI launcher; returns (backend, container, can_write, cmd)."""
    project = await _require_project_access(session, actor, project_id_or_slug)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    agent = await session.get(Agent, ag_session.agent_id)
    if agent is None:
        raise NotFound("Agent no longer exists")

    can_write = actor.org_role == OrgRole.ADMIN or (
        await projects_service.get_member_role(session, project.id, actor.id)
        in (ProjectRole.MAINTAINER, ProjectRole.DEVELOPER)
    )
    effective_can_write = bool(can_write and project.archived_at is None)
    if not effective_can_write:
        if not ag_session.compute_backend_id or not ag_session.container_id:
            raise Forbidden(
                "Read-only viewers can only watch an already active terminal session"
            )
        compute_backend = await session.get(Backend, ag_session.compute_backend_id)
        if compute_backend is None:
            raise Invalid("Compute backend not found")
        if (
            session_pty.get_live_pty(f"agent:{ag_session.id}", ag_session.container_id)
            is None
        ):
            raise Forbidden(
                "Read-only viewers can only watch an already active terminal session"
            )
        return (
            compute_backend,
            ag_session.container_id,
            False,
            [
                "python3",
                "/tmp/gitseed_ses/run_tui.py",
            ],
        )

    # A freshly created session may still be warming up in the background; wait for it
    # rather than racing it (both would create the same named container).
    for _ in range(120):
        if ag_session.status != AgentSessionStatus.STARTING:
            break
        await asyncio.sleep(1)
        await session.refresh(ag_session)

    compute_backend, container_id = await _ensure_warm_session_container(session, ag_session, project)
    if compute_backend is None or container_id is None:
        raise Invalid("Could not start the session container")
    if ag_session.status in (AgentSessionStatus.STOPPED, AgentSessionStatus.FAILED):
        # Opening the terminal wakes a sleeping session
        ag_session.status = AgentSessionStatus.IDLE
        ag_session.error = None

    models_backend, resolved_model = await _resolve_session_models_backend(
        session,
        agent=agent,
        project=project,
        models_backend_id=ag_session.models_backend_id,
        requested_model=ag_session.model,
        actor_user_id=actor.id,
    )
    if models_backend is None:
        raise Invalid("No Inference backend is available for this agent")
    model = resolved_model or ag_session.model
    mcp_servers = await _resolve_session_mcp_servers(session, project, agent.id)
    linked_seed = (
        await session.get(Seed, ag_session.seed_id) if ag_session.seed_id else None
    )
    org_settings = await session.get(OrgSettings, 1)
    ctx = {
        "runtime": str(ag_session.runtime),
        "agent_handle": agent.handle,
        "model": model,
        "gateway": await _gateway_ctx(
            ag_session,
            models_backend,
            model,
            actor_user_id=actor.id,
            seed=linked_seed,
            project=project,
            org=org_settings,
            session=session,
        ),
        "mcp_servers": mcp_servers,
    }

    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        # Re-stamp /workspace itself as runner-owned so containers created
        # before ownership was set in the tar can still write config files
        # (e.g. opencode.json) at the workspace root.
        ws_info = tarfile.TarInfo(name="workspace")
        ws_info.type = tarfile.DIRTYPE
        ws_info.mode = 0o755
        tar.addfile(_runner_owned(ws_info))
        dir_info = tarfile.TarInfo(name="tmp/gitseed_ses")
        dir_info.type = tarfile.DIRTYPE
        dir_info.mode = 0o777
        tar.addfile(_runner_owned(dir_info))
        for name, data in (
            ("tmp/gitseed_ses/tui_ctx.json", json.dumps(ctx).encode("utf-8")),
            ("tmp/gitseed_ses/run_tui.py", _TUI_LAUNCH_SCRIPT.encode("utf-8")),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o600 if name.endswith("_ctx.json") else 0o644
            tar.addfile(_runner_owned(info), io.BytesIO(data))
    if is_k8s_backend(compute_backend):
        await k8s_put_archive(compute_backend, container_id, "/", buf.getvalue(), timeout=30.0)
    else:
        client, base_url = _docker_client_for_backend(compute_backend)
        async with client:
            put = await client.put(
                f"{base_url}/containers/{quote(container_id, safe='')}/archive",
                params={"path": "/"},
                content=buf.getvalue(),
                headers={"Content-Type": "application/x-tar"},
            )
            if put.status_code not in (200, 204):
                raise Invalid("Could not stage the agent terminal in the container")

    ag_session.updated_at = datetime.now(UTC)
    await session.commit()
    return compute_backend, container_id, bool(can_write and project.archived_at is None), [
        "python3",
        "/tmp/gitseed_ses/run_tui.py",
    ]


def agent_transcript_path(session_id: str) -> Any:
    return session_workspace.session_data_dir(session_id) / ".transcript.cast"


async def touch_agent_activity(session_id: str) -> None:
    async with AsyncSessionLocal() as db:
        s = await db.get(AgentSession, session_id)
        if s is not None:
            s.updated_at = datetime.now(UTC)
            await db.commit()


async def _agent_changes(
    session: AsyncSession, project: Project, ag_session: AgentSession
) -> tuple[list[dict[str, str]], dict[str, bytes]]:
    if not ag_session.compute_backend_id:
        raise Invalid("The session container isn't running; send a message to wake it, then push")
    backend = await session.get(Backend, ag_session.compute_backend_id)
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    baseline = session_workspace.load_baseline(ag_session.id)
    if baseline is None:
        raise Invalid("This session started before change tracking was available; start a new chat")
    files = await session_workspace.export_workspace(
        backend, ag_session.container_id or "", session_id=ag_session.id
    )
    changes = session_workspace.diff_workspace(
        files, baseline, session_workspace.project_blocked_paths(project)
    )
    return changes, files


async def _chat_context(session: AsyncSession, ag_session: AgentSession) -> str:
    rows = (
        await session.execute(
            select(AgentSessionMessage)
            .where(AgentSessionMessage.session_id == ag_session.id)
            .order_by(col(AgentSessionMessage.created_at).asc())
        )
    ).scalars().all()
    parts = [f"{m.role.upper()}: {m.content[:2000]}" for m in rows[-16:]]
    # Agent sessions run the CLI in a terminal; its transcript is the main context
    transcript = _transcript_tail_text(agent_transcript_path(ag_session.id))
    if transcript:
        parts.append(f"TERMINAL TRANSCRIPT (tail):\n{transcript}")
    return "\n\n".join(parts)


def _transcript_tail_text(path: Any, limit: int = 6000) -> str:
    try:
        if not path.is_file():
            return ""
        chunks: list[str] = []
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines()[1:]:
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(ev, list) and len(ev) >= 3 and ev[1] == "o":
                chunks.append(str(ev[2]))
        return session_workspace.strip_ansi("".join(chunks))[-limit:]
    except Exception:
        return ""


async def _running_agent_backend(
    session: AsyncSession, ag_session: AgentSession
) -> tuple[Backend, str]:
    if not ag_session.container_id or not ag_session.compute_backend_id:
        raise Invalid("The session container isn't running; wake or resume the session first")
    backend = await session.get(Backend, ag_session.compute_backend_id)
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    return backend, ag_session.container_id


async def list_agent_session_files(
    session: AsyncSession, actor: User, project_id_or_slug: str, session_id: str
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    if not ag_session.compute_backend_id:
        raise Invalid("The session container isn't running; wake or resume the session first")
    backend = await session.get(Backend, ag_session.compute_backend_id)
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    return await session_workspace.list_workspace_files(
        backend,
        ag_session.container_id or "",
        ag_session.id,
        session_workspace.project_blocked_paths(project),
    )


async def read_agent_session_file(
    session: AsyncSession, actor: User, project_id_or_slug: str, session_id: str, *, path: str
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    if not ag_session.compute_backend_id:
        raise Invalid("The session container isn't running; wake or resume the session first")
    backend = await session.get(Backend, ag_session.compute_backend_id)
    if backend is None:
        raise Invalid("Compute backend for this session not found")
    return await session_workspace.read_workspace_file(
        backend,
        ag_session.container_id or "",
        ag_session.id,
        path,
        session_workspace.project_blocked_paths(project),
    )


async def write_agent_session_file(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
    *,
    path: str,
    content: str,
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug, require_write=True)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    backend, container_id = await _running_agent_backend(session, ag_session)
    res = await session_workspace.write_workspace_file(
        backend,
        container_id,
        ag_session.id,
        path,
        content,
        session_workspace.project_blocked_paths(project),
    )
    ag_session.updated_at = datetime.now(UTC)
    await session.commit()
    return res


async def delete_agent_session_file(
    session: AsyncSession, actor: User, project_id_or_slug: str, session_id: str, *, path: str
) -> None:
    project = await _require_project_access(session, actor, project_id_or_slug, require_write=True)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    backend, container_id = await _running_agent_backend(session, ag_session)
    await session_workspace.delete_workspace_file(
        backend, container_id, path, session_workspace.project_blocked_paths(project)
    )
    ag_session.updated_at = datetime.now(UTC)
    await session.commit()


async def discard_agent_session_changes(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
    *,
    path: str | None = None,
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug, require_write=True)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    backend, container_id = await _running_agent_backend(session, ag_session)
    res = await session_workspace.discard_workspace_changes(
        backend,
        container_id,
        ag_session.id,
        session_workspace.project_blocked_paths(project),
        path=path,
    )
    ag_session.updated_at = datetime.now(UTC)
    await session.commit()
    return res


async def agent_push_draft(
    session: AsyncSession, actor: User, project_id_or_slug: str, session_id: str
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug, require_write=True)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    changes, files = await _agent_changes(session, project, ag_session)
    title, summary, criteria, drafted_by = await session_workspace.draft_push_summary(
        session,
        project,
        session_label=f"agent session “{ag_session.title}”",
        context_text=await _chat_context(session, ag_session),
        changes=changes,
        files=files,
    )
    linked = await session_workspace.resolve_linked_seed_info(
        session,
        project,
        seed_id=ag_session.seed_id or ag_session.pushed_seed_id,
        ref=ag_session.ref,
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


async def push_agent_session_to_seed(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    session_id: str,
    *,
    title: str,
    summary: str,
    acceptance_criteria: list[str],
    target_seed_id: str | None = None,
) -> dict[str, Any]:
    project = await _require_project_access(session, actor, project_id_or_slug, require_write=True)
    ag_session = await session.get(AgentSession, session_id)
    if ag_session is None or ag_session.project_id != project.id:
        raise NotFound(f"Agent session '{session_id}' not found")
    if ag_session.status == AgentSessionStatus.RUNNING_TURN:
        raise Invalid("Wait for the agent to finish its turn before pushing")
    changes, files = await _agent_changes(session, project, ag_session)
    result = await session_workspace.push_session_to_seed(
        session,
        actor,
        project,
        repository_id=ag_session.repository_id,
        base_ref=ag_session.ref,
        head_sha=ag_session.head_sha,
        changes=changes,
        files=files,
        title=title,
        summary=summary,
        acceptance_criteria=acceptance_criteria,
        session_label=f"agent session “{ag_session.title}”",
        target_seed_id=target_seed_id,
    )
    fresh = await session.get(AgentSession, session_id)
    if fresh is not None:
        fresh.pushed_seed_id = result["seed_id"]
        fresh.updated_at = datetime.now(UTC)
        await session.commit()
    return result
