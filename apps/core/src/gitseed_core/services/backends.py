import ipaddress
import json
import os
import re
import secrets as py_secrets
import shutil
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

import httpx
from gitseed_common.enums import (
    BACKEND_PROVIDER_SPECS,
    ActivityType,
    ActorType,
    AgentKind,
    AgentRuntime,
    BackendHealth,
    BackendType,
    OrgRole,
    ProjectRole,
    SeedStage,
)
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.run import Run
from gitseed_core.models.user import User
from gitseed_core.security import create_github_app_jwt, decrypt_secrets, encrypt_secrets
from gitseed_core.services.activity import record
from gitseed_core.services.compute_runtime import (
    _resolve_k8s_ssl_context,
    build_docker_ssl_context,
    read_incluster_k8s_token,
)
from gitseed_core.services.errors import Conflict, Forbidden, Invalid, NotFound
from gitseed_core.services.gateway import (
    docker_host_fallback_url,
    extract_enabled_model_ids,
    is_personal_mode_backend,
    remove_backend_from_litellm,
    sync_backend_to_litellm,
)
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.net_safety import normalize_hostname
from gitseed_core.services.policies import ensure_org_policies

_BLOCKED_BACKEND_ENDPOINT_HOSTS = frozenset(
    {
        "metadata.google.internal",
        "metadata",
        "instance-data",
        "postgresql",
        "gitseed-postgresql",
        "gitseed-postgres",
        "valkey",
        "gitseed-valkey",
        "redis",
        "litellm",
        "gitseed-litellm",
    }
)


def _validate_backend_endpoint(
    backend_type: BackendType,
    provider: str,
    endpoint: str | None,
) -> str | None:
    """Validate custom backend `endpoint` URLs against credential embedding, SSRF metadata, and unsafe schemes."""
    if endpoint is None:
        return None
    cleaned = endpoint.strip()
    if not cleaned:
        return None
    if any(ch in cleaned for ch in ("\r", "\n", "\x00")):
        raise Invalid("Backend endpoint must not contain control characters")

    norm_provider = provider.strip().lower()
    if backend_type == BackendType.TOOLS and norm_provider == "mcp-stdio":
        return cleaned
    if backend_type == BackendType.GIT and norm_provider == "local":
        return cleaned
    if backend_type == BackendType.COMPUTE and cleaned.startswith("unix://"):
        return cleaned

    parse_target = cleaned
    allowed_schemes: tuple[str, ...] = ("http", "https")
    if backend_type == BackendType.COMPUTE:
        if norm_provider == "docker":
            allowed_schemes = ("http", "https", "tcp")
            if not cleaned.startswith(("http://", "https://", "tcp://", "unix://")) and "://" not in cleaned:
                parse_target = f"tcp://{cleaned}"
        elif norm_provider in ("kubernetes", "k8s"):
            if "://" not in cleaned:
                parse_target = f"https://{cleaned}"

    try:
        parts = urlsplit(parse_target)
        _ = parts.port
    except ValueError as exc:
        raise Invalid("Backend endpoint URL is malformed") from exc

    scheme = parts.scheme.lower()
    if scheme not in allowed_schemes:
        raise Invalid(
            f"Backend endpoint URL must use {', '.join(s + '://' for s in allowed_schemes)}"
        )
    if parts.username or parts.password:
        raise Invalid("Backend endpoint URL must not embed credentials")

    host = normalize_hostname(parts.hostname or "")
    if not host:
        raise Invalid("Backend endpoint URL must include a valid hostname")
    if host in _BLOCKED_BACKEND_ENDPOINT_HOSTS:
        raise Invalid(
            f"Backend endpoint URL cannot target restricted internal or metadata host '{host}'"
        )

    try:
        ip_raw = ipaddress.ip_address(host)
    except ValueError:
        ip_raw = None

    if ip_raw is not None:
        ip_norm: ipaddress.IPv4Address | ipaddress.IPv6Address = ip_raw
        if isinstance(ip_raw, ipaddress.IPv6Address):
            mapped = ip_raw.ipv4_mapped or ip_raw.sixtofour
            if mapped is not None:
                ip_norm = mapped
        aws_imds_v6 = ipaddress.ip_network("fd00:ec2::/32")
        if (
            ip_norm.is_link_local
            or ip_norm.is_unspecified
            or ip_norm.is_multicast
            or (isinstance(ip_norm, ipaddress.IPv6Address) and ip_norm in aws_imds_v6)
        ):
            raise Invalid(
                "Backend endpoint URL cannot target cloud metadata, link-local, or unspecified IP addresses"
            )

    return cleaned


def _validate_provider_and_secrets(
    backend_type: BackendType,
    provider: str,
    secrets_map: dict[str, str],
    *,
    auth_mode: str = "api_key",
) -> dict[str, str]:
    """Validate provider against BACKEND_PROVIDER_SPECS and verify required secret keys."""
    providers_for_type = BACKEND_PROVIDER_SPECS.get(backend_type, {})
    spec = providers_for_type.get(provider)
    if spec is None:
        allowed = ", ".join(sorted(providers_for_type.keys()))
        raise Invalid(
            f"Unsupported provider '{provider}' for backend type '{backend_type.value}'. "
            f"Allowed providers: {allowed}"
        )

    cleaned = {k.strip(): v.strip() for k, v in secrets_map.items() if v and v.strip()}

    allowed_secret_keys = set(spec.allowed_secrets)
    if backend_type == BackendType.GIT and provider in ("github", "github-enterprise"):
        allowed_secret_keys.add("_device_code")

    unknown = sorted(set(cleaned.keys()) - allowed_secret_keys)
    if unknown:
        allowed_keys = ", ".join(sorted(spec.allowed_secrets)) or "none"
        raise Invalid(
            f"Unknown secret key(s) for provider '{provider}': {', '.join(unknown)}. "
            f"Allowed secret keys: {allowed_keys}"
        )

    required_keys = spec.required_secrets
    if backend_type == BackendType.INFERENCE and auth_mode == "cli_oauth":
        required_keys = frozenset()
    elif backend_type == BackendType.GIT and auth_mode in ("manifest", "device_oauth", "cli_oauth"):
        required_keys = frozenset()

    missing = sorted(required_keys - set(cleaned.keys()))
    if missing:
        raise Invalid(
            f"Provider '{provider}' requires secret(s): {', '.join(missing)}"
        )

    return cleaned


def get_secret_keys(backend: Backend) -> list[str]:
    """Return the sorted names of secrets configured on a backend (never their values)."""
    return sorted(
        k for k in decrypt_secrets(backend.secrets_encrypted) if not k.startswith("_")
    )


async def get_allowed_agent_ids(session: AsyncSession, backend_id: str) -> list[str]:
    stmt = (
        select(AgentBackend.agent_id)
        .where(AgentBackend.backend_id == backend_id)
        .order_by(col(AgentBackend.agent_id).asc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_allowed_agent_ids_map(
    session: AsyncSession, backend_ids: list[str]
) -> dict[str, list[str]]:
    """Batch-fetch allowed agent IDs for multiple backends in a single query."""
    if not backend_ids:
        return {}
    stmt = (
        select(AgentBackend.backend_id, AgentBackend.agent_id)
        .where(col(AgentBackend.backend_id).in_(backend_ids))
        .order_by(col(AgentBackend.agent_id).asc())
    )
    result: dict[str, list[str]] = {bid: [] for bid in backend_ids}
    for backend_id, agent_id in (await session.execute(stmt)).all():
        result.setdefault(str(backend_id), []).append(str(agent_id))
    return result


async def list_backends(
    session: AsyncSession,
    *,
    type: BackendType | None = None,
    enabled: bool | None = None,
) -> list[Backend]:
    """List registered backends, optionally filtered by type or enabled state."""
    stmt = select(Backend)
    if type is not None:
        stmt = stmt.where(Backend.type == type)
    if enabled is not None:
        stmt = stmt.where(Backend.enabled == enabled)
    stmt = stmt.order_by(col(Backend.created_at).asc())
    return list((await session.execute(stmt)).scalars().all())


async def get_backend(session: AsyncSession, backend_id: str) -> Backend:
    """Return a single backend by ID or raise NotFound."""
    backend = await session.get(Backend, backend_id)
    if backend is None:
        raise NotFound("Backend not found")
    return backend


async def create_backend(
    session: AsyncSession,
    admin: User,
    *,
    name: str,
    type: BackendType,
    provider: str,
    endpoint: str | None = None,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
    capacity: int | None = None,
    access_restricted: bool = False,
    enabled: bool = True,
) -> Backend:
    """Validate provider & required secrets, encrypt secrets, and create a Backend."""
    normalized_provider = provider.strip().lower()
    initial_config = dict(config or {})
    auth_mode = str(initial_config.get("auth_mode") or "api_key").strip().lower()
    cleaned_secrets = _validate_provider_and_secrets(
        type, normalized_provider, secrets or {}, auth_mode=auth_mode
    )
    cleaned_endpoint = _validate_backend_endpoint(type, normalized_provider, endpoint)

    now = datetime.now(UTC)
    if type == BackendType.INFERENCE and auth_mode == "cli_oauth":
        if not settings.GITSEED_ALLOW_PERSONAL_CLI_OAUTH:
            raise Forbidden(
                "Personal Mode (subscription CLI OAuth) is disabled on this instance "
                "(GITSEED_ALLOW_PERSONAL_CLI_OAUTH=false)."
            )
        if enabled and initial_config.get("personal_mode_acknowledged") is False:
            raise Invalid(
                "Personal Mode (subscription CLI OAuth) requires explicit acknowledgement "
                "(personal_mode_acknowledged=true)."
            )
        initial_config["personal_mode"] = True
        initial_config["personal_mode_acknowledged"] = bool(
            initial_config.get("personal_mode_acknowledged", True)
        )
        initial_config.setdefault("personal_mode_owner_user_id", admin.id)
        initial_config.setdefault("personal_mode_acknowledged_at", now.isoformat())

    backend = Backend(
        name=name.strip(),
        type=type,
        provider=normalized_provider,
        endpoint=cleaned_endpoint,
        config=initial_config,
        secrets_encrypted=encrypt_secrets(cleaned_secrets) if cleaned_secrets else None,
        capacity=capacity,
        access_restricted=access_restricted,
        health=BackendHealth.UNKNOWN,
        enabled=enabled,
        created_by=admin.id,
        created_at=now,
        updated_at=now,
        updated_by=admin.id,
    )
    if type == BackendType.GIT and (
        cleaned_secrets.get("token") or cleaned_secrets.get("private_key")
    ):
        try:
            discovered = await _fetch_git_repositories(backend, cleaned_secrets)
            if discovered:
                existing_repos = initial_config.get("repositories")
                initial_config["repositories"] = _merge_discovered_repos(
                    existing_repos if isinstance(existing_repos, list) else [],
                    discovered,
                )
                backend.config = dict(initial_config)
                backend.health = BackendHealth.HEALTHY
                backend.health_checked_at = now
        except Exception:
            pass
    elif type == BackendType.INFERENCE:
        try:
            discovered_models = await _fetch_backend_models(backend, cleaned_secrets)
            if discovered_models:
                existing_models = initial_config.get("models")
                legacy_models = initial_config.get("discovered_models")
                merged_models = _merge_discovered_models(
                    existing_models if isinstance(existing_models, list) else [],
                    legacy_models if isinstance(legacy_models, list) else [],
                    discovered_models,
                    preferred_default=str(initial_config.get("default_model") or "").strip() or None,
                )
                initial_config["models"] = merged_models
                initial_config["discovered_models"] = [
                    m["id"] for m in merged_models if m.get("enabled", True)
                ]
                _normalize_backend_models_default(initial_config)
                if auth_mode == "cli_oauth":
                    if cleaned_secrets.get("cli_auth_bundle") or cleaned_secrets.get("oauth_token"):
                        initial_config["cli_auth_status"] = "authenticated"
                        backend.health = BackendHealth.HEALTHY
                        backend.health_checked_at = now
                    else:
                        initial_config.setdefault("cli_auth_status", "pending")
                else:
                    backend.health = BackendHealth.HEALTHY
                    backend.health_checked_at = now
                backend.config = dict(initial_config)
        except Exception:
            pass

    if type == BackendType.INFERENCE and isinstance(backend.config, dict):
        norm_init_cfg = dict(backend.config)
        _normalize_backend_models_default(norm_init_cfg)
        backend.config = norm_init_cfg

    session.add(backend)
    await session.commit()
    await session.refresh(backend)
    if backend.type == BackendType.INFERENCE:
        await sync_backend_to_litellm(backend)
        await maybe_auto_provision_sdlc_agents(session, backend)
    return backend


async def update_backend(
    session: AsyncSession,
    admin: User,
    backend_id: str,
    *,
    name: str | None = None,
    endpoint: str | None = None,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
    capacity: int | None = None,
    access_restricted: bool | None = None,
    enabled: bool | None = None,
    fields_set: set[str] | None = None,
) -> Backend:
    """Update a backend and re-validate/record activity when secrets are updated."""
    backend = await get_backend(session, backend_id)
    fields = fields_set or set()

    target_enabled = enabled if enabled is not None else backend.enabled
    target_cfg = dict(config if config is not None else (backend.config or {}))
    if backend.type == BackendType.INFERENCE:
        _normalize_backend_models_default(target_cfg)
        if config is not None:
            config = target_cfg
        else:
            backend.config = target_cfg
        effective_auth = str(target_cfg.get("auth_mode") or "api_key").strip().lower()
        if effective_auth == "cli_oauth":
            if target_enabled and not settings.GITSEED_ALLOW_PERSONAL_CLI_OAUTH:
                raise Forbidden(
                    "Personal Mode (subscription CLI OAuth) is disabled on this instance "
                    "(GITSEED_ALLOW_PERSONAL_CLI_OAUTH=false)."
                )
            if target_enabled and target_cfg.get("personal_mode_acknowledged") is False:
                raise Invalid(
                    "Personal Mode (subscription CLI OAuth) requires explicit acknowledgement "
                    "(personal_mode_acknowledged=true)."
                )
            target_cfg["personal_mode"] = True
            target_cfg["personal_mode_acknowledged"] = bool(
                target_cfg.get("personal_mode_acknowledged", True)
            )
            target_cfg.setdefault("personal_mode_owner_user_id", admin.id)
            target_cfg.setdefault(
                "personal_mode_acknowledged_at", datetime.now(UTC).isoformat()
            )
            if config is not None:
                config = target_cfg
            else:
                backend.config = target_cfg

    if name is not None:
        backend.name = name.strip()
    if "endpoint" in fields:
        backend.endpoint = _validate_backend_endpoint(
            backend.type, backend.provider, endpoint
        )
    if config is not None:
        backend.config = config
    if "capacity" in fields:
        backend.capacity = capacity
    if access_restricted is not None:
        backend.access_restricted = access_restricted
    if enabled is not None:
        backend.enabled = enabled

    if secrets is not None:
        merged = decrypt_secrets(backend.secrets_encrypted)
        for k, v in secrets.items():
            if v and v.strip():
                merged[k.strip()] = v.strip()
            else:
                merged.pop(k.strip(), None)

        effective_auth_mode = str((backend.config or {}).get("auth_mode") or "api_key").strip().lower()
        cleaned_secrets = _validate_provider_and_secrets(
            backend.type, backend.provider, merged, auth_mode=effective_auth_mode
        )
        backend.secrets_encrypted = (
            encrypt_secrets(cleaned_secrets) if cleaned_secrets else None
        )
        record(
            session,
            type=ActivityType.BACKEND_SECRET_UPDATED,
            actor_type=ActorType.USER,
            actor_id=admin.id,
            data={
                "backend_id": backend.id,
                "name": backend.name,
                "secret_keys": sorted(cleaned_secrets.keys()),
            },
        )

    backend.updated_at = datetime.now(UTC)
    backend.updated_by = admin.id
    await session.commit()
    await session.refresh(backend)
    if backend.type == BackendType.INFERENCE:
        await sync_backend_to_litellm(backend)
        await maybe_auto_provision_sdlc_agents(session, backend)
    return backend


async def delete_backend(session: AsyncSession, backend_id: str) -> None:
    """Delete a backend if no projects, repositories, or runs depend on it."""
    backend = await get_backend(session, backend_id)
    was_models = backend.type == BackendType.INFERENCE
    await session.delete(backend)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise Conflict(
            "Cannot delete backend while projects, repositories, or runs still depend on it"
        ) from exc
    if was_models:
        await remove_backend_from_litellm(backend_id)


async def grant_agent_access(
    session: AsyncSession,
    admin: User,
    backend_id: str,
    agent_id: str,
) -> list[str]:
    """Allow an agent to use a restricted backend and record backend.granted."""
    backend = await get_backend(session, backend_id)
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise NotFound("Agent not found")

    existing = await session.get(AgentBackend, (backend.id, agent.id))
    if existing is None:
        session.add(
            AgentBackend(
                backend_id=backend.id,
                agent_id=agent.id,
                created_by=admin.id,
                created_at=datetime.now(UTC),
            )
        )
        record(
            session,
            type=ActivityType.BACKEND_GRANTED,
            actor_type=ActorType.USER,
            actor_id=admin.id,
            data={
                "backend_id": backend.id,
                "backend_name": backend.name,
                "agent_id": agent.id,
                "agent_handle": agent.handle,
            },
        )
        await session.commit()

    return await get_allowed_agent_ids(session, backend.id)


async def revoke_agent_access(
    session: AsyncSession,
    admin: User,
    backend_id: str,
    agent_id: str,
) -> list[str]:
    """Revoke an agent's access to a restricted backend and record backend.revoked."""
    backend = await get_backend(session, backend_id)
    existing = await session.get(AgentBackend, (backend.id, agent_id))
    if existing is not None:
        await session.delete(existing)
        record(
            session,
            type=ActivityType.BACKEND_REVOKED,
            actor_type=ActorType.USER,
            actor_id=admin.id,
            data={
                "backend_id": backend.id,
                "backend_name": backend.name,
                "agent_id": agent_id,
            },
        )
        await session.commit()

    return await get_allowed_agent_ids(session, backend.id)


def _parse_oauth_expiry_epoch(raw_val: Any) -> float | None:
    """Convert a numeric (sec/ms) or ISO-8601 expiry value into a UTC epoch timestamp in seconds."""
    if isinstance(raw_val, (int, float)) and not isinstance(raw_val, bool):
        val = float(raw_val)
        if val <= 0:
            return None
        return val / 1000.0 if val >= 1e11 else val
    if isinstance(raw_val, str) and raw_val.strip():
        txt = raw_val.strip()
        try:
            num = float(txt)
            if num > 0:
                return num / 1000.0 if num >= 1e11 else num
        except ValueError:
            pass
        try:
            dt = datetime.fromisoformat(txt.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.timestamp()
        except ValueError:
            return None
    return None


def _inspect_oauth_credential_obj(
    obj: Any, *, parent_has_refresh: bool = False, now_epoch: float
) -> tuple[bool, bool]:
    """Return `(found_expired_without_refresh, found_valid_or_refreshable)` for a parsed JSON credential tree."""
    if not isinstance(obj, dict):
        return False, False

    has_refresh = parent_has_refresh or any(
        bool(str(obj.get(k) or "").strip())
        for k in ("refresh_token", "refreshToken")
    )
    expiry_epoch: float | None = None
    for k in ("expires_at", "expiresAt", "expiry_date", "expiryDate", "expiry"):
        if k in obj:
            expiry_epoch = _parse_oauth_expiry_epoch(obj.get(k))
            if expiry_epoch is not None:
                break

    found_expired_no_refresh = False
    found_valid = False
    if expiry_epoch is not None:
        if expiry_epoch <= now_epoch and not has_refresh:
            found_expired_no_refresh = True
        else:
            found_valid = True

    for val in obj.values():
        if isinstance(val, dict):
            child_exp, child_val = _inspect_oauth_credential_obj(
                val, parent_has_refresh=has_refresh, now_epoch=now_epoch
            )
            found_expired_no_refresh = found_expired_no_refresh or child_exp
            found_valid = found_valid or child_val

    return found_expired_no_refresh, found_valid


def _check_cli_oauth_bundle_health(
    secrets_map: dict[str, str],
) -> tuple[BackendHealth, str | None]:
    """Validate stored CLI OAuth credentials and detect expired non-refreshable sessions."""
    raw_bundle = (secrets_map.get("cli_auth_bundle") or "").strip()
    oauth_token = (secrets_map.get("oauth_token") or "").strip()
    if not raw_bundle and not oauth_token:
        return (
            BackendHealth.DEGRADED,
            "CLI login required: click 'Launch CLI Sign-In' or paste an OAuth token",
        )

    if raw_bundle:
        try:
            bundle = json.loads(raw_bundle)
        except Exception:
            if oauth_token:
                return BackendHealth.HEALTHY, None
            return (
                BackendHealth.DEGRADED,
                "Stored CLI OAuth credential bundle is malformed; re-run CLI sign-in",
            )
        if not isinstance(bundle, dict) or not any(
            isinstance(v, str) and v.strip() for v in bundle.values()
        ):
            if oauth_token:
                return BackendHealth.HEALTHY, None
            return (
                BackendHealth.DEGRADED,
                "Stored CLI OAuth credential bundle is empty; re-run CLI sign-in",
            )

        now_epoch = datetime.now(UTC).timestamp()
        any_expired_no_refresh = False
        any_valid = False
        for content in bundle.values():
            if not isinstance(content, str) or not content.strip().startswith("{"):
                continue
            try:
                parsed = json.loads(content)
            except Exception:
                continue
            exp_no_ref, valid_cred = _inspect_oauth_credential_obj(
                parsed, now_epoch=now_epoch
            )
            any_expired_no_refresh = any_expired_no_refresh or exp_no_ref
            any_valid = any_valid or valid_cred

        if any_expired_no_refresh and not any_valid and not oauth_token:
            return (
                BackendHealth.DEGRADED,
                "CLI OAuth session has expired and has no refresh token; re-run CLI sign-in to refresh credentials",
            )

    return BackendHealth.HEALTHY, None


async def _probe_backend(
    backend: Backend,
    secrets_map: dict[str, str],
) -> tuple[BackendHealth, str | None]:
    """Perform a live connectivity/authentication probe against the target backend."""
    endpoint = (backend.endpoint or "").strip().rstrip("/")
    provider = backend.provider.lower()

    if backend.type == BackendType.COMPUTE:
        if provider in ("kubernetes", "k8s"):
            base_url = endpoint or "https://kubernetes.default.svc"
            if not base_url.startswith(("http://", "https://")):
                base_url = f"https://{base_url}"
            headers: dict[str, str] = {}
            token = secrets_map.get("token", "") or read_incluster_k8s_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            ssl_ctx = _resolve_k8s_ssl_context(backend, secrets_map)
            async with httpx.AsyncClient(
                headers=headers,
                verify=ssl_ctx,
                timeout=5.0,
                follow_redirects=False,
            ) as client:
                resp = await client.get(f"{base_url}/version")
                if resp.status_code in (401, 403):
                    return (
                        BackendHealth.UNREACHABLE,
                        f"Kubernetes authentication failed (HTTP {resp.status_code})",
                    )
                if 200 <= resp.status_code < 300:
                    return BackendHealth.HEALTHY, None
                return (
                    BackendHealth.UNREACHABLE,
                    f"Kubernetes API returned HTTP {resp.status_code}",
                )

        default_ep = (
            getattr(settings, "DOCKER_HOST", "unix:///var/run/docker.sock")
            or "unix:///var/run/docker.sock"
        )
        target = (endpoint or default_ep).strip().rstrip("/")
        if (
            target == "unix:///var/run/docker.sock"
            and default_ep != target
            and not os.path.exists("/var/run/docker.sock")
        ):
            target = default_ep.strip().rstrip("/")
        if target.startswith("unix://"):
            sock_path = target.removeprefix("unix://")
            if not os.path.exists(sock_path):
                return (
                    BackendHealth.UNREACHABLE,
                    f"Docker socket not found at {sock_path}",
                )
            transport = httpx.AsyncHTTPTransport(uds=sock_path)
            async with httpx.AsyncClient(transport=transport, timeout=5.0) as client:
                resp = await client.get("http://docker/_ping")
                if resp.status_code == 200:
                    return BackendHealth.HEALTHY, None
                return (
                    BackendHealth.UNREACHABLE,
                    f"Docker ping returned HTTP {resp.status_code}",
                )

        base_url = target
        has_tls_secrets = bool(secrets_map.get("tls_cert") or secrets_map.get("tls_ca"))
        if base_url.startswith("tcp://"):
            scheme = "https://" if has_tls_secrets else "http://"
            base_url = scheme + base_url.removeprefix("tcp://")
        docker_ssl_ctx = (
            build_docker_ssl_context(secrets_map)
            if base_url.startswith("https://")
            else None
        )
        async with httpx.AsyncClient(
            verify=docker_ssl_ctx if docker_ssl_ctx is not None else True,
            timeout=5.0,
            follow_redirects=False,
        ) as client:
            resp = await client.get(f"{base_url}/_ping")
            if resp.status_code == 200:
                return BackendHealth.HEALTHY, None
            return (
                BackendHealth.UNREACHABLE,
                f"Docker daemon returned HTTP {resp.status_code}",
            )

    if backend.type == BackendType.INFERENCE:
        auth_mode = str((backend.config or {}).get("auth_mode") or "api_key").strip().lower()
        if auth_mode == "cli_oauth":
            return _check_cli_oauth_bundle_health(secrets_map)
        api_key = secrets_map.get("api_key", "")
        headers: dict[str, str] = {}
        if provider == "anthropic":
            base = endpoint or "https://api.anthropic.com"
            url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            headers["x-api-key"] = api_key
            headers["anthropic-version"] = "2023-06-01"
        elif provider in ("gemini", "antigravity"):
            if not api_key and provider == "antigravity":
                return BackendHealth.HEALTHY, None
            base = endpoint or "https://generativelanguage.googleapis.com/v1beta"
            if base.endswith("/openai"):
                url = f"{base}/models"
                if api_key:
                    headers["Authorization"] = f"Bearer {api_key}"
            else:
                clean_base = base if base.endswith("/v1beta") else f"{base}/v1beta"
                url = f"{clean_base}/models?key={api_key}"
        elif provider == "xai":
            base = endpoint or "https://api.x.ai/v1"
            url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        elif provider == "ollama":
            base = endpoint or "http://localhost:11434"
            url = f"{base}/api/tags"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        else:
            base = endpoint or "https://api.openai.com/v1"
            url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

        async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.ConnectError:
                alt_url = docker_host_fallback_url(url)
                if alt_url != url:
                    resp = await client.get(alt_url, headers=headers)
                else:
                    raise
            if resp.status_code in (401, 403):
                return (
                    BackendHealth.UNREACHABLE,
                    f"Authentication failed (HTTP {resp.status_code})",
                )
            if 200 <= resp.status_code < 300:
                return BackendHealth.HEALTHY, None
            return (
                BackendHealth.DEGRADED,
                f"Endpoint returned HTTP {resp.status_code}",
            )

    if backend.type == BackendType.GIT:
        if provider == "local":
            from gitseed_core.services.local_git import local_repos_root

            root = local_repos_root()
            root.mkdir(parents=True, exist_ok=True)
            return BackendHealth.HEALTHY, None
        token = await resolve_git_backend_token(backend, secrets_map)
        headers = {}
        if provider in ("github", "github-enterprise", "github-app"):
            base = endpoint or "https://api.github.com"
            if provider == "github-app":
                if not token and secrets_map.get("private_key"):
                    return (
                        BackendHealth.UNREACHABLE,
                        "Failed to mint GitHub App installation token",
                    )
                url = f"{base}/installation/repositories?per_page=1"
            else:
                url = f"{base}/user"
            headers["Accept"] = "application/vnd.github+json"
            if token:
                headers["Authorization"] = f"Bearer {token}"
        elif provider == "gitlab":
            base = endpoint or "https://gitlab.com"
            url = (
                f"{base}/user"
                if base.endswith("/api/v4")
                else f"{base}/api/v4/user"
            )
            if token:
                headers["PRIVATE-TOKEN"] = token
        elif provider == "bitbucket":
            base = endpoint or "https://api.bitbucket.org/2.0"
            url = f"{base}/user" if base.endswith("/2.0") else f"{base}/2.0/user"
            if token:
                headers["Authorization"] = f"Bearer {token}"
        else:
            if not endpoint:
                return BackendHealth.UNREACHABLE, "No Git endpoint configured"
            if not endpoint.startswith(("http://", "https://")):
                return BackendHealth.HEALTHY, None
            url = endpoint
            if token:
                headers["Authorization"] = f"Bearer {token}"

        async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code in (401, 403):
                return (
                    BackendHealth.UNREACHABLE,
                    f"Git authentication failed (HTTP {resp.status_code})",
                )
            if 200 <= resp.status_code < 400:
                return BackendHealth.HEALTHY, None
            return (
                BackendHealth.UNREACHABLE,
                f"Git host returned HTTP {resp.status_code}",
            )

    if backend.type == BackendType.TOOLS:
        if provider == "mcp-stdio":
            parts = endpoint.split()
            if not parts:
                return BackendHealth.UNREACHABLE, "No command executable configured"
            cmd = parts[0]
            if shutil.which(cmd) or os.path.isfile(cmd):
                return BackendHealth.HEALTHY, None
            return (
                BackendHealth.UNREACHABLE,
                f"Executable '{cmd}' not found in system PATH",
            )

        if not endpoint:
            return BackendHealth.UNREACHABLE, "No MCP server URL configured"
        headers = {}
        if secrets_map.get("bearer_token"):
            headers["Authorization"] = f"Bearer {secrets_map['bearer_token']}"
        if secrets_map.get("api_key"):
            headers["X-API-Key"] = secrets_map["api_key"]

        async with httpx.AsyncClient(timeout=5.0, follow_redirects=False) as client:
            async with client.stream("GET", endpoint, headers=headers) as resp:
                if resp.status_code in (401, 403):
                    return (
                        BackendHealth.UNREACHABLE,
                        f"MCP server authentication failed (HTTP {resp.status_code})",
                    )
                if resp.status_code < 500:
                    return BackendHealth.HEALTHY, None
                return (
                    BackendHealth.UNREACHABLE,
                    f"MCP server returned HTTP {resp.status_code}",
                )

    return BackendHealth.UNKNOWN, None


async def probe_backend_config(
    *,
    type: BackendType,
    provider: str,
    endpoint: str | None = None,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
) -> tuple[BackendHealth, int | None, str | None]:
    """Execute a live probe against a backend configuration without persisting a row."""
    normalized_provider = provider.strip().lower()
    auth_mode = str((config or {}).get("auth_mode") or "api_key").strip().lower()
    try:
        cleaned_secrets = _validate_provider_and_secrets(
            type, normalized_provider, secrets or {}, auth_mode=auth_mode
        )
        cleaned_endpoint = _validate_backend_endpoint(
            type, normalized_provider, endpoint
        )
    except Invalid as exc:
        return BackendHealth.UNREACHABLE, None, exc.message

    temp_backend = Backend(
        name="probe",
        type=type,
        provider=normalized_provider,
        endpoint=cleaned_endpoint,
        config=dict(config or {}),
    )

    start = time.perf_counter()
    try:
        health, error_msg = await _probe_backend(temp_backend, cleaned_secrets)
    except httpx.TimeoutException:
        health = BackendHealth.UNREACHABLE
        error_msg = "Connection timed out"
    except httpx.ConnectError as exc:
        health = BackendHealth.UNREACHABLE
        error_msg = f"Connection failed: {exc}"
    except Exception as exc:
        health = BackendHealth.UNREACHABLE
        error_msg = str(exc)

    elapsed_ms = max(1, round((time.perf_counter() - start) * 1000))
    if health == BackendHealth.HEALTHY and elapsed_ms > 3500:
        health = BackendHealth.DEGRADED
        error_msg = f"High latency ({elapsed_ms}ms)"

    latency_ms = elapsed_ms if health != BackendHealth.UNREACHABLE else None
    return health, latency_ms, error_msg


async def discover_probe_metadata(
    *,
    type: BackendType,
    provider: str,
    endpoint: str | None = None,
    config: dict[str, Any] | None = None,
    secrets: dict[str, str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Discover models (for Inference) or repositories (for Git) after a successful connection probe."""
    normalized_provider = provider.strip().lower()
    auth_mode = str((config or {}).get("auth_mode") or "api_key").strip().lower()
    try:
        cleaned_secrets = _validate_provider_and_secrets(
            type, normalized_provider, secrets or {}, auth_mode=auth_mode
        )
        cleaned_endpoint = _validate_backend_endpoint(
            type, normalized_provider, endpoint
        )
    except Invalid:
        return [], []

    cfg = dict(config or {})
    temp_backend = Backend(
        name="probe",
        type=type,
        provider=normalized_provider,
        endpoint=cleaned_endpoint,
        config=cfg,
    )

    if type == BackendType.INFERENCE:
        try:
            discovered_models = await _fetch_backend_models(temp_backend, cleaned_secrets)
            if discovered_models:
                existing_models = cfg.get("models")
                legacy_models = cfg.get("discovered_models")
                merged_models = _merge_discovered_models(
                    existing_models if isinstance(existing_models, list) else [],
                    legacy_models if isinstance(legacy_models, list) else [],
                    discovered_models,
                )
                return merged_models, []
        except Exception:
            pass
    elif type == BackendType.GIT:
        try:
            discovered_repos = await _fetch_git_repositories(temp_backend, cleaned_secrets)
            if discovered_repos:
                existing_repos = cfg.get("repositories")
                merged_repos = _merge_discovered_repos(
                    existing_repos if isinstance(existing_repos, list) else [],
                    discovered_repos,
                )
                return [], merged_repos
        except Exception:
            pass

    return [], []


def _merge_discovered_repos(
    existing: list[Any],
    discovered: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge newly discovered repositories with existing repo entries, preserving enabled flags."""
    existing_by_key: dict[str, dict[str, Any]] = {}
    for item in existing:
        if isinstance(item, dict) and isinstance(item.get("full_name"), str):
            full_name = item["full_name"].strip()
            if full_name:
                existing_by_key[full_name.lower()] = item

    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()

    for repo in discovered:
        full_name = str(repo.get("full_name", "")).strip()
        if not full_name:
            continue
        key = full_name.lower()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        prev = existing_by_key.get(key)
        enabled = bool(prev.get("enabled", True)) if prev else True
        merged.append(
            {
                "full_name": full_name,
                "default_branch": str(
                    repo.get("default_branch")
                    or (prev.get("default_branch") if prev else None)
                    or "main"
                ),
                "private": bool(
                    repo.get("private", prev.get("private", False) if prev else False)
                ),
                "enabled": enabled,
            }
        )

    # Preserve any manually added repositories that weren't in the discovered batch
    for key, prev in existing_by_key.items():
        if key not in seen_keys:
            merged.append(
                {
                    "full_name": str(prev["full_name"]).strip(),
                    "default_branch": str(prev.get("default_branch") or "main"),
                    "private": bool(prev.get("private", False)),
                    "enabled": bool(prev.get("enabled", True)),
                }
            )

    merged.sort(key=lambda r: str(r.get("full_name", "")).lower())
    return merged


async def _fetch_git_repositories(
    backend: Backend,
    secrets_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Fetch accessible repositories from a Git backend provider."""
    endpoint = (backend.endpoint or "").strip().rstrip("/")
    provider = backend.provider.lower()
    if provider == "local":
        from gitseed_core.services.local_git import local_repos_root

        root = local_repos_root()
        out: list[dict[str, Any]] = []
        for p in sorted(root.glob("*.git")):
            slug = p.name[:-4] if p.name.endswith(".git") else p.name
            out.append(
                {
                    "full_name": f"local/{slug}",
                    "default_branch": "main",
                    "private": True,
                }
            )
        return out
    token = await resolve_git_backend_token(backend, secrets_map)
    headers: dict[str, str] = {}

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
        if provider in ("github", "github-enterprise", "github-app"):
            base = endpoint or "https://api.github.com"
            headers["Accept"] = "application/vnd.github+json"
            if token:
                headers["Authorization"] = f"Bearer {token}"

            if provider == "github-app":
                inst_resp = await client.get(
                    f"{base}/installation/repositories?per_page=100",
                    headers=headers,
                )
                if 200 <= inst_resp.status_code < 300:
                    payload = inst_resp.json()
                    raw_items = (
                        payload.get("repositories", [])
                        if isinstance(payload, dict)
                        else []
                    )
                    return [
                        {
                            "full_name": item["full_name"],
                            "default_branch": item.get("default_branch") or "main",
                            "private": bool(item.get("private", False)),
                        }
                        for item in raw_items
                        if isinstance(item, dict) and item.get("full_name")
                    ]

            resp = await client.get(
                f"{base}/user/repos?per_page=100&sort=updated&affiliation=owner,collaborator,organization_member",
                headers=headers,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), list):
                return [
                    {
                        "full_name": item["full_name"],
                        "default_branch": item.get("default_branch") or "main",
                        "private": bool(item.get("private", False)),
                    }
                    for item in resp.json()
                    if isinstance(item, dict) and item.get("full_name")
                ]
            return []

        if provider == "gitlab":
            base = endpoint or "https://gitlab.com"
            api_base = base if base.endswith("/api/v4") else f"{base}/api/v4"
            if token:
                headers["PRIVATE-TOKEN"] = token
            resp = await client.get(
                f"{api_base}/projects?membership=true&simple=true&per_page=100&order_by=last_activity_at",
                headers=headers,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), list):
                return [
                    {
                        "full_name": item["path_with_namespace"],
                        "default_branch": item.get("default_branch") or "main",
                        "private": item.get("visibility") != "public",
                    }
                    for item in resp.json()
                    if isinstance(item, dict) and item.get("path_with_namespace")
                ]
            return []

        if provider == "local":
            from gitseed_core.services.local_git import local_repos_root

            root = local_repos_root()
            out: list[dict[str, Any]] = []
            for p in sorted(root.glob("*.git")):
                slug = p.name[:-4] if p.name.endswith(".git") else p.name
                out.append(
                    {
                        "full_name": f"local/{slug}",
                        "default_branch": "main",
                        "private": True,
                    }
                )
            return out

        if provider == "bitbucket":
            base = endpoint or "https://api.bitbucket.org/2.0"
            api_base = base if base.endswith("/2.0") else f"{base}/2.0"
            if token:
                headers["Authorization"] = f"Bearer {token}"
            resp = await client.get(
                f"{api_base}/repositories?role=member&pagelen=100",
                headers=headers,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), dict):
                values = resp.json().get("values", [])
                return [
                    {
                        "full_name": item["full_name"],
                        "default_branch": (
                            item.get("mainbranch", {}).get("name")
                            if isinstance(item.get("mainbranch"), dict)
                            else None
                        )
                        or "main",
                        "private": bool(item.get("is_private", False)),
                    }
                    for item in values
                    if isinstance(item, dict) and item.get("full_name")
                ]
            return []

    return []


async def _require_git_backend_operator(
    session: AsyncSession,
    actor: User | None,
    *,
    backend_id: str | None = None,
) -> None:
    """Ensure the caller is an org admin or a project maintainer permitted to use `backend_id`."""
    if actor is None or actor.org_role == OrgRole.ADMIN:
        return
    stmt = (
        select(Project)
        .join(ProjectMember, col(Project.id) == col(ProjectMember.project_id))
        .where(
            ProjectMember.user_id == actor.id,
            ProjectMember.role == ProjectRole.MAINTAINER,
            col(Project.archived_at).is_(None),
        )
    )
    exec_res = await session.execute(stmt)
    scalars_obj = exec_res.scalars() if hasattr(exec_res, "scalars") else None
    projects = (
        list(scalars_obj.all())
        if scalars_obj is not None and hasattr(scalars_obj, "all")
        else []
    )
    first_row = exec_res.scalar_one_or_none() if not projects else projects[0]
    if first_row is None and not projects:
        raise Forbidden(
            "Organization admin or project maintainer privileges required to manage Git backend repositories"
        )
    if backend_id and projects and all(isinstance(p, Project) for p in projects):
        from gitseed_core.models.project import ProjectBackend

        allowed_somewhere = False
        for proj in projects:
            if not getattr(proj, "backends_restricted", False):
                allowed_somewhere = True
                break
            grant = await session.get(ProjectBackend, (proj.id, backend_id))
            if grant is not None:
                allowed_somewhere = True
                break
        if not allowed_somewhere:
            raise Forbidden(
                "This Git backend is not allowed on any project you maintain"
            )


async def discover_backend_repositories(
    session: AsyncSession,
    backend_id: str,
    *,
    actor: User | None = None,
) -> Backend:
    """Discover repositories from a Git backend and merge into backend.config['repositories']."""
    await _require_git_backend_operator(session, actor, backend_id=backend_id)
    backend = await get_backend(session, backend_id)
    if backend.type != BackendType.GIT:
        raise Invalid("Repository discovery is only supported for Git backends")

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    discovered = await _fetch_git_repositories(backend, secrets_map)

    cfg = dict(backend.config or {})
    existing_repos = cfg.get("repositories")
    cfg["repositories"] = _merge_discovered_repos(
        existing_repos if isinstance(existing_repos, list) else [],
        discovered,
    )
    backend.config = cfg
    backend.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(backend)
    return backend


async def create_remote_repository(
    session: AsyncSession,
    backend_id: str,
    *,
    actor: User | None = None,
    name: str,
    description: str = "",
    private: bool = True,
    auto_init: bool = True,
) -> tuple[Backend, dict[str, Any]]:
    """Create a new remote repository on a Git backend provider and merge it into backend.config['repositories']."""
    await _require_git_backend_operator(session, actor, backend_id=backend_id)
    backend = await get_backend(session, backend_id)
    if backend.type != BackendType.GIT:
        raise Invalid("Remote repository creation is only supported for Git backends")

    raw_name = name.strip().strip("/")
    if not raw_name:
        raise Invalid("Repository name is required")

    if "/" in raw_name:
        owner_prefix, repo_slug = raw_name.split("/", 1)
        owner_prefix = owner_prefix.strip()
        repo_slug = repo_slug.strip()
    else:
        owner_prefix = ""
        repo_slug = raw_name

    if not repo_slug:
        raise Invalid("Invalid repository name")

    endpoint = (backend.endpoint or "").strip().rstrip("/")
    provider = backend.provider.lower()
    created_repo: dict[str, Any] | None = None

    if provider == "local":
        from gitseed_core.services.local_git import ensure_local_bare_repo

        ensure_local_bare_repo(repo_slug, repo_slug, default_branch="main")
        created_repo = {
            "full_name": f"local/{repo_slug}",
            "default_branch": "main",
            "private": True,
            "web_url": f"local://local/{repo_slug}",
        }
    else:
        secrets_map = decrypt_secrets(backend.secrets_encrypted)
        token = await resolve_git_backend_token(backend, secrets_map, write_access=True)
        if not token:
            raise Invalid("Git backend does not have an access token configured")

        async with httpx.AsyncClient(timeout=15.0, follow_redirects=False) as client:
            if provider in ("github", "github-enterprise", "github-app"):
                base = endpoint or "https://api.github.com"
                headers = {
                    "Accept": "application/vnd.github+json",
                    "Authorization": f"Bearer {token}",
                }
                payload = {
                    "name": repo_slug,
                    "description": description.strip(),
                    "private": private,
                    "auto_init": auto_init,
                }

                target_url = f"{base}/user/repos"
                if owner_prefix:
                    user_resp = await client.get(f"{base}/user", headers=headers)
                    user_login = (
                        str((user_resp.json() or {}).get("login") or "").strip()
                        if user_resp.status_code == 200
                        else ""
                    )
                    if user_login and user_login.lower() != owner_prefix.lower():
                        target_url = f"{base}/orgs/{owner_prefix}/repos"

                resp = await client.post(target_url, headers=headers, json=payload)
                if resp.status_code == 404 and target_url != f"{base}/user/repos":
                    resp = await client.post(
                        f"{base}/user/repos", headers=headers, json=payload
                    )

                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    created_repo = {
                        "full_name": str(data.get("full_name") or f"{owner_prefix or 'user'}/{repo_slug}"),
                        "default_branch": str(data.get("default_branch") or "main"),
                        "private": bool(data.get("private", private)),
                        "web_url": str(data.get("html_url") or ""),
                    }
                else:
                    err_msg = ""
                    try:
                        err_json = resp.json()
                        if isinstance(err_json, dict):
                            err_msg = str(err_json.get("message") or "")
                            errors_list = err_json.get("errors")
                            if isinstance(errors_list, list) and errors_list:
                                first_err = errors_list[0]
                                if isinstance(first_err, dict) and first_err.get("message"):
                                    err_msg = f"{err_msg}: {first_err['message']}"
                    except Exception:
                        pass
                    raise Invalid(
                        f"GitHub API could not create repository (HTTP {resp.status_code}"
                        f"{f': {err_msg}' if err_msg else ''}). If your token is a fine-grained PAT "
                        "without Administration: Write permission, click 'New on GitHub' to create it "
                        "in your browser and then click 'Sync repositories'."
                    )

            elif provider == "gitlab":
                base = endpoint or "https://gitlab.com"
                api_base = base if base.endswith("/api/v4") else f"{base}/api/v4"
                headers = {"PRIVATE-TOKEN": token}
                payload = {
                    "name": repo_slug,
                    "path": repo_slug,
                    "description": description.strip(),
                    "private": None,
                    "visibility": "private" if private else "public",
                    "initialize_with_readme": auto_init,
                }
                payload.pop("private", None)
                resp = await client.post(f"{api_base}/projects", headers=headers, json=payload)
                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    created_repo = {
                        "full_name": str(data.get("path_with_namespace") or repo_slug),
                        "default_branch": str(data.get("default_branch") or "main"),
                        "private": data.get("visibility") != "public",
                        "web_url": str(data.get("web_url") or ""),
                    }
                else:
                    raise Invalid(
                        f"GitLab API could not create repository (HTTP {resp.status_code}). "
                        "You can click 'New on GitLab' to create it in your browser and then click 'Sync repositories'."
                    )

            elif provider == "bitbucket":
                base = endpoint or "https://api.bitbucket.org/2.0"
                api_base = base if base.endswith("/2.0") else f"{base}/2.0"
                headers = {"Authorization": f"Bearer {token}"}
                workspace = owner_prefix
                if not workspace:
                    ws_resp = await client.get(f"{api_base}/user", headers=headers)
                    if ws_resp.status_code == 200 and isinstance(ws_resp.json(), dict):
                        workspace = str(ws_resp.json().get("username") or "").strip()
                if not workspace:
                    raise Invalid("Specify workspace/repo-name for Bitbucket repository creation")
                resp = await client.post(
                    f"{api_base}/repositories/{workspace}/{repo_slug}",
                    headers=headers,
                    json={
                        "scm": "git",
                        "is_private": private,
                        "description": description.strip(),
                    },
                )
                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    created_repo = {
                        "full_name": str(data.get("full_name") or f"{workspace}/{repo_slug}"),
                        "default_branch": "main",
                        "private": bool(data.get("is_private", private)),
                        "web_url": str(
                            ((data.get("links") or {}).get("html") or {}).get("href") or ""
                        ),
                    }
                else:
                    raise Invalid(
                        f"Bitbucket API could not create repository (HTTP {resp.status_code}). "
                        "You can click 'New on Bitbucket' to create it in your browser and then click 'Sync repositories'."
                    )
            else:
                raise Invalid(f"Unsupported Git provider '{provider}' for remote creation")

    cfg = dict(backend.config or {})
    existing_repos = cfg.get("repositories")
    cfg["repositories"] = _merge_discovered_repos(
        existing_repos if isinstance(existing_repos, list) else [],
        [created_repo],
    )
    backend.config = cfg
    backend.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(backend)
    return backend, created_repo


async def test_backend_connection(
    session: AsyncSession,
    backend_id: str,
) -> Backend:
    """Execute a live probe against a backend and persist health, latency, and error."""
    backend = await get_backend(session, backend_id)
    secrets_map = decrypt_secrets(backend.secrets_encrypted)

    health, latency_ms, error_msg = await probe_backend_config(
        type=backend.type,
        provider=backend.provider,
        endpoint=backend.endpoint,
        config=backend.config,
        secrets=secrets_map,
    )

    backend.health = health
    backend.health_checked_at = datetime.now(UTC)
    backend.health_latency_ms = latency_ms
    backend.health_error = error_msg

    if backend.type == BackendType.GIT and health in (
        BackendHealth.HEALTHY,
        BackendHealth.DEGRADED,
    ):
        try:
            discovered = await _fetch_git_repositories(backend, secrets_map)
            if discovered:
                cfg = dict(backend.config or {})
                existing_repos = cfg.get("repositories")
                cfg["repositories"] = _merge_discovered_repos(
                    existing_repos if isinstance(existing_repos, list) else [],
                    discovered,
                )
                backend.config = cfg
        except Exception:
            pass
    elif backend.type == BackendType.INFERENCE and health in (
        BackendHealth.HEALTHY,
        BackendHealth.DEGRADED,
    ):
        try:
            discovered_models = await _fetch_backend_models(backend, secrets_map)
            if discovered_models:
                cfg = dict(backend.config or {})
                existing_models = cfg.get("models")
                legacy_models = cfg.get("discovered_models")
                merged_models = _merge_discovered_models(
                    existing_models if isinstance(existing_models, list) else [],
                    legacy_models if isinstance(legacy_models, list) else [],
                    discovered_models,
                    preferred_default=str(cfg.get("default_model") or "").strip() or None,
                )
                cfg["models"] = merged_models
                cfg["discovered_models"] = [
                    m["id"] for m in merged_models if m.get("enabled", True)
                ]
                default_entry = next(
                    (
                        m["id"]
                        for m in merged_models
                        if m.get("enabled", True) and m.get("is_default")
                    ),
                    None,
                )
                if default_entry:
                    cfg["default_model"] = default_entry
                backend.config = cfg
        except Exception:
            pass

    await session.commit()
    await session.refresh(backend)
    if backend.type == BackendType.INFERENCE:
        await sync_backend_to_litellm(backend)
    return backend


_DEFAULT_CLOUD_MODELS: dict[str, list[dict[str, Any]]] = {
    "anthropic": [
        {"id": "claude-sonnet-5-5", "owned_by": "anthropic", "is_default": True},
        {"id": "claude-opus-5-5", "owned_by": "anthropic"},
        {"id": "claude-haiku-5-5", "owned_by": "anthropic"},
        {"id": "claude-sonnet-4-6", "owned_by": "anthropic"},
        {"id": "claude-opus-4-6", "owned_by": "anthropic"},
    ],
    "openai": [
        {"id": "gpt-5.4", "owned_by": "openai", "is_default": True},
        {"id": "gpt-5.3-codex", "owned_by": "openai"},
        {"id": "gpt-5.2", "owned_by": "openai"},
        {"id": "gpt-5.2-codex", "owned_by": "openai"},
        {"id": "gpt-5.2-pro", "owned_by": "openai"},
    ],
    "gemini": [
        {"id": "gemini-4-argon", "owned_by": "google", "is_default": True},
        {"id": "gemini-4-hydrogen", "owned_by": "google"},
        {"id": "gemini-4-carbon", "owned_by": "google"},
        {"id": "gemini-3.8-flash", "owned_by": "google"},
        {"id": "gemini-3.5-flash", "owned_by": "google"},
        {"id": "gemini-3.1-pro", "owned_by": "google"},
        {"id": "gemini-3.1-flash-lite", "owned_by": "google"},
    ],
    "antigravity": [
        {"id": "gemini-4-argon", "owned_by": "antigravity", "is_default": True},
        {"id": "gemini-4-hydrogen", "owned_by": "antigravity"},
        {"id": "gemini-4-carbon", "owned_by": "antigravity"},
        {"id": "gemini-3.8-flash", "owned_by": "antigravity"},
        {"id": "gemini-3.5-flash", "owned_by": "antigravity"},
        {"id": "gemini-3.1-pro", "owned_by": "antigravity"},
        {"id": "pro", "owned_by": "antigravity"},
        {"id": "flash", "owned_by": "antigravity"},
        {"id": "flash_lite", "owned_by": "antigravity"},
    ],
    "xai": [
        {"id": "grok-4.7", "owned_by": "xai", "is_default": True},
        {"id": "grok-4.6", "owned_by": "xai"},
        {"id": "grok-4.5", "owned_by": "xai"},
        {"id": "grok-4.3", "owned_by": "xai"},
        {"id": "grok-4.20-reasoning", "owned_by": "xai"},
    ],
}

_KNOWN_MODEL_PRIORITY: dict[str, int] = {
    entry["id"].lower(): idx
    for provider_models in _DEFAULT_CLOUD_MODELS.values()
    for idx, entry in enumerate(provider_models)
}

_OPENAI_EXCLUDE_PREFIXES = (
    "text-embedding",
    "tts-",
    "whisper-",
    "dall-e-",
    "omni-moderation",
    "text-moderation",
    "babbage-",
    "davinci-",
    "gpt-image-",
    "chatgpt-4o-",
    "gpt-3.5-",
    "gpt-4-0",
    "gpt-4-1106",
    "gpt-4-vision",
    "gpt-4-turbo",
    "gpt-4o-2024",
    "gpt-4o-mini-2024",
    "gpt-4o-audio",
    "gpt-4o-realtime",
    "gpt-4o-transcribe",
    "gpt-4o-search",
    "o1-preview",
    "o1-mini",
    "computer-use-",
)

_OPENAI_EXCLUDE_SUBSTRINGS = (
    "-realtime-",
    "-audio-",
    "-transcribe",
    "-search-preview",
    "-tts",
)

_GEMINI_EXCLUDE_SUBSTRINGS = (
    "embedding",
    "aqa",
    "gemini-1.0",
    "gemini-1.5",
    "gemini-2.0",
    "gemini-exp",
    "imagen",
    "veo",
    "learnlm",
    "gemma",
    "-tts",
    "-image",
    "-vision",
)


def _normalize_backend_models_default(cfg: dict[str, Any]) -> None:
    """Ensure `cfg['models']` and `cfg['default_model']` agree on a single enabled default model."""
    models = cfg.get("models")
    if not isinstance(models, list) or not models:
        return
    explicit_cfg_default = str(cfg.get("default_model") or "").strip().lower()
    chosen_key: str | None = None

    for item in models:
        if not isinstance(item, dict) or not item.get("enabled", True):
            continue
        mid = str(item.get("id") or "").strip()
        if not mid:
            continue
        if bool(item.get("is_default")):
            chosen_key = mid.lower()
            break

    if chosen_key is None and explicit_cfg_default:
        for item in models:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            mid = str(item.get("id") or "").strip()
            if mid and mid.lower() == explicit_cfg_default:
                chosen_key = mid.lower()
                break

    if chosen_key is None:
        for item in models:
            if not isinstance(item, dict) or not item.get("enabled", True):
                continue
            mid = str(item.get("id") or "").strip()
            if mid:
                chosen_key = mid.lower()
                break

    chosen_id: str | None = None
    for item in models:
        if not isinstance(item, dict):
            continue
        mid = str(item.get("id") or "").strip()
        is_def = bool(
            mid
            and item.get("enabled", True)
            and chosen_key is not None
            and mid.lower() == chosen_key
        )
        item["is_default"] = is_def
        if is_def and chosen_id is None:
            chosen_id = mid

    if chosen_id:
        cfg["default_model"] = chosen_id


def _merge_discovered_models(
    existing: list[Any],
    legacy_discovered: list[Any],
    discovered: list[dict[str, Any]],
    *,
    preferred_default: str | None = None,
) -> list[dict[str, Any]]:
    """Merge newly discovered models with existing model entries, preserving enabled & default flags."""
    existing_by_key: dict[str, dict[str, Any]] = {}
    existing_order: dict[str, int] = {}
    existing_default_key: str | None = (
        preferred_default.strip().lower()
        if isinstance(preferred_default, str) and preferred_default.strip()
        else None
    )

    for idx, item in enumerate(existing):
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            mid = item["id"].strip()
            if mid:
                key = mid.lower()
                existing_by_key[key] = item
                existing_order.setdefault(key, idx)
                if bool(item.get("is_default")) and item.get("enabled", True):
                    existing_default_key = key
    for item in legacy_discovered:
        if isinstance(item, str) and item.strip():
            mid = item.strip()
            key = mid.lower()
            if key not in existing_by_key:
                existing_by_key[key] = {"id": mid, "enabled": True}

    merged: list[dict[str, Any]] = []
    seen_keys: set[str] = set()
    discovered_order: dict[str, int] = {}
    discovered_default_key: str | None = None

    for idx, m in enumerate(discovered):
        mid = str(m.get("id", "")).strip()
        if not mid:
            continue
        key = mid.lower()
        if key in seen_keys:
            continue
        seen_keys.add(key)
        discovered_order[key] = idx
        prev = existing_by_key.get(key)
        default_enabled = "embed" not in key
        enabled = bool(prev.get("enabled", default_enabled)) if prev else default_enabled
        if discovered_default_key is None and bool(m.get("is_default")) and enabled:
            discovered_default_key = key
        merged.append(
            {
                "id": mid,
                "owned_by": str(
                    m.get("owned_by")
                    or (prev.get("owned_by") if prev else "")
                    or ""
                ),
                "enabled": enabled,
            }
        )

    for key, prev in existing_by_key.items():
        if key not in seen_keys:
            merged.append(
                {
                    "id": str(prev["id"]).strip(),
                    "owned_by": str(prev.get("owned_by") or ""),
                    "enabled": bool(prev.get("enabled", True)),
                }
            )

    active_default_key = existing_default_key or discovered_default_key

    def _sort_key(item: dict[str, Any]) -> tuple[int, int, str]:
        key = str(item.get("id", "")).strip().lower()
        is_def = 0 if (active_default_key and key == active_default_key and item.get("enabled", True)) else 1
        if key in _KNOWN_MODEL_PRIORITY:
            rank = _KNOWN_MODEL_PRIORITY[key]
        elif key in discovered_order:
            rank = 100 + discovered_order[key]
        elif key in existing_order:
            rank = 500 + existing_order[key]
        else:
            rank = 1000
        return (is_def, rank, key)

    merged.sort(key=_sort_key)

    chosen_default_key: str | None = None
    if active_default_key and any(
        str(m.get("id", "")).lower() == active_default_key and m.get("enabled", True)
        for m in merged
    ):
        chosen_default_key = active_default_key
    else:
        first_enabled = next((m for m in merged if m.get("enabled", True)), None)
        if first_enabled is not None:
            chosen_default_key = str(first_enabled.get("id", "")).lower()

    for m in merged:
        m["is_default"] = bool(
            chosen_default_key
            and m.get("enabled", True)
            and str(m.get("id", "")).lower() == chosen_default_key
        )

    return merged


async def _fetch_backend_models(
    backend: Backend,
    secrets_map: dict[str, str],
) -> list[dict[str, Any]]:
    """Fetch available models from an Inference backend provider, with curated cloud fallbacks."""
    endpoint = (backend.endpoint or "").strip().rstrip("/")
    provider = backend.provider.lower()
    auth_mode = str((backend.config or {}).get("auth_mode") or "api_key").strip().lower()
    api_key = secrets_map.get("api_key", "")
    if auth_mode == "cli_oauth" and not api_key and provider in _DEFAULT_CLOUD_MODELS:
        return [dict(m) for m in _DEFAULT_CLOUD_MODELS[provider]]
    headers: dict[str, str] = {}

    if provider == "anthropic":
        base = endpoint or "https://api.anthropic.com"
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        if api_key:
            headers["x-api-key"] = api_key
        headers["anthropic-version"] = "2023-06-01"
    elif provider == "ollama":
        base = endpoint or "http://localhost:11434"
        url = f"{base}/api/tags"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "lmstudio":
        base = endpoint or "http://localhost:1234/v1"
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif provider == "vllm":
        base = endpoint or "http://localhost:8000/v1"
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    elif provider in ("gemini", "antigravity"):
        if not api_key and provider == "antigravity":
            return [dict(m) for m in _DEFAULT_CLOUD_MODELS["antigravity"]]
        base = endpoint or "https://generativelanguage.googleapis.com/v1beta"
        if base.endswith("/openai"):
            url = f"{base}/models"
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"
        else:
            clean_base = base if base.endswith("/v1beta") else f"{base}/v1beta"
            url = f"{clean_base}/models?key={api_key}"
    elif provider == "xai":
        base = endpoint or "https://api.x.ai/v1"
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
    else:
        base = endpoint or "https://api.openai.com/v1"
        url = f"{base}/models" if base.endswith("/v1") else f"{base}/v1/models"
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

    discovered: list[dict[str, Any]] = []
    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            try:
                resp = await client.get(url, headers=headers)
            except httpx.ConnectError:
                alt_url = docker_host_fallback_url(url)
                if alt_url != url:
                    resp = await client.get(alt_url, headers=headers)
                else:
                    raise

            if 200 <= resp.status_code < 300:
                payload = resp.json()
                if provider == "ollama" and isinstance(payload, dict):
                    raw_models = payload.get("models", [])
                    if isinstance(raw_models, list):
                        for item in raw_models:
                            if isinstance(item, dict):
                                mid = item.get("name") or item.get("model")
                                if isinstance(mid, str) and mid.strip():
                                    discovered.append(
                                        {
                                            "id": mid.strip(),
                                            "owned_by": "ollama",
                                        }
                                    )
                elif (
                    provider in ("gemini", "antigravity")
                    and isinstance(payload, dict)
                    and isinstance(payload.get("models"), list)
                ):
                    for item in payload["models"]:
                        if isinstance(item, dict) and isinstance(item.get("name"), str):
                            mid = item["name"].strip().removeprefix("models/")
                            mid_low = mid.lower()
                            if not mid or not mid_low.startswith("gemini"):
                                continue
                            if any(sub in mid_low for sub in _GEMINI_EXCLUDE_SUBSTRINGS):
                                continue
                            discovered.append(
                                {
                                    "id": mid,
                                    "owned_by": "antigravity" if provider == "antigravity" else "google",
                                }
                            )
                    if discovered and provider in _DEFAULT_CLOUD_MODELS:
                        seen_disc = {m["id"].lower() for m in discovered}
                        curated_first = [dict(m) for m in _DEFAULT_CLOUD_MODELS[provider]]
                        curated_keys = {m["id"].lower() for m in curated_first}
                        discovered = [
                            *curated_first,
                            *(m for m in discovered if m["id"].lower() not in curated_keys and m["id"].lower() in seen_disc),
                        ]
                elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
                    for item in payload["data"]:
                        if isinstance(item, dict) and isinstance(item.get("id"), str):
                            mid = item["id"].strip().removeprefix("models/")
                            mid_low = mid.lower()
                            if not mid:
                                continue
                            if provider == "openai" and (
                                mid_low.startswith(_OPENAI_EXCLUDE_PREFIXES)
                                or any(sub in mid_low for sub in _OPENAI_EXCLUDE_SUBSTRINGS)
                            ):
                                continue
                            discovered.append(
                                {
                                    "id": mid,
                                    "owned_by": str(
                                        item.get("owned_by")
                                        or item.get("type")
                                        or provider
                                    ),
                                }
                            )
    except Exception:
        pass

    if not discovered and provider in _DEFAULT_CLOUD_MODELS:
        return [dict(m) for m in _DEFAULT_CLOUD_MODELS[provider]]

    return discovered


async def discover_backend_models(
    session: AsyncSession,
    backend_id: str,
) -> Backend:
    """Discover models from an Inference backend and merge them into backend.config['models']."""
    backend = await get_backend(session, backend_id)
    if backend.type != BackendType.INFERENCE:
        raise Invalid("Model discovery is only supported for Inference backends")

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    discovered = await _fetch_backend_models(backend, secrets_map)

    cfg = dict(backend.config or {})
    existing_models = cfg.get("models")
    legacy_models = cfg.get("discovered_models")
    merged = _merge_discovered_models(
        existing_models if isinstance(existing_models, list) else [],
        legacy_models if isinstance(legacy_models, list) else [],
        discovered,
        preferred_default=str(cfg.get("default_model") or "").strip() or None,
    )
    cfg["models"] = merged
    cfg["discovered_models"] = [
        m["id"] for m in merged if m.get("enabled", True)
    ]
    default_entry = next(
        (
            m["id"]
            for m in merged
            if m.get("enabled", True) and m.get("is_default")
        ),
        None,
    )
    if default_entry:
        cfg["default_model"] = default_entry
    backend.config = cfg
    backend.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(backend)
    await sync_backend_to_litellm(backend)
    return backend


def _github_web_base_from_endpoint(endpoint: str | None) -> str:
    raw = (endpoint or "https://api.github.com").strip().rstrip("/")
    if not raw or raw == "https://api.github.com":
        return "https://github.com"
    if raw.endswith("/api/v3"):
        return raw[: -len("/api/v3")]
    return raw


def _github_api_base_from_endpoint(endpoint: str | None) -> str:
    raw = (endpoint or "https://api.github.com").strip().rstrip("/")
    return raw or "https://api.github.com"


async def start_github_app_manifest(
    session: AsyncSession,
    admin: User,
    *,
    backend_id: str | None = None,
    name: str | None = None,
    organization: str | None = None,
    endpoint: str | None = None,
    origin: str | None = None,
) -> dict[str, Any]:
    """Prepare a 1-click GitHub App Manifest registration payload and state token."""
    clean_origin = (origin or "http://localhost:8421").strip().rstrip("/")
    if endpoint is not None and endpoint.strip():
        _validate_backend_endpoint(BackendType.GIT, "github-app", endpoint)
    clean_endpoint = _github_api_base_from_endpoint(endpoint)
    web_base = _github_web_base_from_endpoint(clean_endpoint)
    clean_org = (organization or "").strip().strip("/")
    state = py_secrets.token_urlsafe(24)
    now = datetime.now(UTC)

    if backend_id:
        backend = await get_backend(session, backend_id)
        if backend.type != BackendType.GIT or backend.provider != "github-app":
            raise Invalid("GitHub App Manifest flow is only supported for github-app backends")
        if name and name.strip():
            backend.name = name.strip()
        if endpoint is not None:
            backend.endpoint = clean_endpoint
        cfg = dict(backend.config or {})
        cfg["manifest_state"] = state
        cfg["manifest_origin"] = clean_origin
        cfg["manifest_status"] = "awaiting_creation"
        if clean_org:
            cfg["github_org"] = clean_org
        backend.config = cfg
        backend.updated_at = now
        backend.updated_by = admin.id
        await session.commit()
        await session.refresh(backend)
    else:
        effective_name = (name or "").strip() or (
            f"GitHub App ({clean_org})" if clean_org else "GitHub App"
        )
        cfg = {
            "auth_mode": "manifest",
            "manifest_state": state,
            "manifest_origin": clean_origin,
            "manifest_status": "awaiting_creation",
        }
        if clean_org:
            cfg["github_org"] = clean_org
        backend = await create_backend(
            session,
            admin,
            name=effective_name,
            type=BackendType.GIT,
            provider="github-app",
            endpoint=clean_endpoint,
            config=cfg,
            secrets={},
            enabled=True,
        )

    short_suffix = backend.id[-6:].lower()
    app_display_name = f"GitSeed ({short_suffix})"[:34]
    manifest_obj = {
        "name": app_display_name,
        "url": clean_origin,
        "hook_attributes": {
            "url": f"{clean_origin}/api/v1/webhooks/github",
            "active": False,
        },
        "redirect_url": f"{clean_origin}/api/v1/backends/github-app/callback",
        "setup_url": f"{clean_origin}/api/v1/backends/github-app/setup",
        "public": False,
        "default_permissions": {
            "contents": "write",
            "pull_requests": "write",
            "metadata": "read",
            "checks": "read",
            "statuses": "write",
        },
        "default_events": [
            "pull_request",
            "push",
        ],
    }

    if clean_org:
        action_url = f"{web_base}/organizations/{clean_org}/settings/apps/new?state={state}"
    else:
        action_url = f"{web_base}/settings/apps/new?state={state}"

    return {
        "action_url": action_url,
        "manifest": json.dumps(manifest_obj),
        "state": state,
        "backend": backend,
    }


async def complete_github_app_manifest_callback(
    session: AsyncSession,
    *,
    code: str,
    state: str,
) -> tuple[Backend, str]:
    """Exchange GitHub's temporary manifest `code` for the App ID, PEM key, and webhook secret."""
    clean_state = (state or "").strip()
    if not clean_state:
        raise NotFound("No pending GitHub App setup matched this state token")
    stmt = select(Backend).where(
        Backend.type == BackendType.GIT,
        Backend.provider == "github-app",
    )
    candidates = list((await session.execute(stmt)).scalars().all())
    target: Backend | None = None
    for b in candidates:
        cfg = b.config or {}
        saved_state = str(cfg.get("manifest_state") or "").strip()
        if saved_state and py_secrets.compare_digest(saved_state, clean_state):
            target = b
            break
    if target is None:
        raise NotFound("No pending GitHub App setup matched this state token")

    clean_code = (code or "").strip()
    if not clean_code or not re.fullmatch(r"[A-Za-z0-9._-]+", clean_code):
        raise Invalid("Invalid GitHub App manifest conversion code")

    api_base = _github_api_base_from_endpoint(target.endpoint)
    web_base = _github_web_base_from_endpoint(target.endpoint)

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=False) as client:
        resp = await client.post(
            f"{api_base}/app-manifests/{clean_code}/conversions",
            headers={
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )
        if resp.status_code >= 300 or not isinstance(resp.json(), dict):
            raise Invalid(
                f"GitHub rejected manifest code exchange (HTTP {resp.status_code})"
            )
        data = resp.json()

    app_id = str(data.get("id") or "").strip()
    pem = str(data.get("pem") or "").strip()
    webhook_secret = str(data.get("webhook_secret") or "").strip()
    app_slug = str(data.get("slug") or "").strip()
    app_name = str(data.get("name") or "").strip()
    html_url = str(data.get("html_url") or "").strip() or (
        f"{web_base}/apps/{app_slug}" if app_slug else web_base
    )
    if not app_id or not pem:
        raise Invalid("GitHub manifest response did not include an App ID and private key")

    merged_secrets = decrypt_secrets(target.secrets_encrypted)
    merged_secrets["private_key"] = pem
    if webhook_secret:
        merged_secrets["webhook_secret"] = webhook_secret
    target.secrets_encrypted = encrypt_secrets(merged_secrets)

    cfg = dict(target.config or {})
    cfg["app_id"] = app_id
    if app_slug:
        cfg["app_slug"] = app_slug
    if app_name:
        cfg["app_name"] = app_name
    cfg["app_html_url"] = html_url
    cfg["manifest_status"] = "awaiting_install"
    target.config = cfg
    target.health = BackendHealth.DEGRADED
    target.health_error = "GitHub App created: install it on your account or organization to finish"
    target.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(target)

    install_url = f"{html_url.rstrip('/')}/installations/new?state={state}"
    return target, install_url


async def sync_github_app_installation(
    session: AsyncSession,
    backend_id: str,
    *,
    explicit_installation_id: str | None = None,
) -> Backend:
    """Discover the installation ID and accessible repositories for a GitHub App backend."""
    backend = await get_backend(session, backend_id)
    if backend.type != BackendType.GIT or backend.provider != "github-app":
        raise Invalid("Installation sync is only supported for github-app backends")

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    cfg = dict(backend.config or {})
    app_id = str(cfg.get("app_id") or "").strip()
    private_key = secrets_map.get("private_key", "").strip()
    if not app_id or not private_key:
        return backend

    api_base = _github_api_base_from_endpoint(backend.endpoint)
    installation_id = (
        (explicit_installation_id or "").strip()
        or str(cfg.get("installation_id") or "").strip()
    )

    if not installation_id:
        try:
            jwt_token = create_github_app_jwt(app_id, private_key)
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {jwt_token}",
                "X-GitHub-Api-Version": "2022-11-28",
            }
            async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
                inst_resp = await client.get(
                    f"{api_base}/app/installations", headers=headers
                )
                if 200 <= inst_resp.status_code < 300 and isinstance(
                    inst_resp.json(), list
                ):
                    installations = inst_resp.json()
                    if installations and isinstance(installations[0], dict):
                        installation_id = str(installations[0].get("id") or "").strip()
                        account = installations[0].get("account")
                        if isinstance(account, dict) and account.get("login"):
                            cfg["installed_account"] = str(account["login"])
        except Exception:
            pass

    if installation_id:
        cfg["installation_id"] = installation_id
        cfg["manifest_status"] = "connected"
        cfg.pop("manifest_state", None)
        backend.config = dict(cfg)
        try:
            discovered = await _fetch_git_repositories(backend, secrets_map)
            existing_repos = cfg.get("repositories")
            cfg["repositories"] = _merge_discovered_repos(
                existing_repos if isinstance(existing_repos, list) else [],
                discovered,
            )
            backend.config = dict(cfg)
            backend.health = BackendHealth.HEALTHY
            backend.health_error = None
            backend.health_checked_at = datetime.now(UTC)
        except Exception:
            pass
        backend.updated_at = datetime.now(UTC)
        await session.commit()
        await session.refresh(backend)

    return backend


async def complete_github_app_setup(
    session: AsyncSession,
    *,
    installation_id: str,
    state: str | None = None,
) -> Backend | None:
    """Handle GitHub's post-installation redirect (`setup_url`) and bind `installation_id`."""
    stmt = (
        select(Backend)
        .where(
            Backend.type == BackendType.GIT,
            Backend.provider == "github-app",
        )
        .order_by(col(Backend.updated_at).desc())
    )
    candidates = list((await session.execute(stmt)).scalars().all())
    target: Backend | None = None

    clean_state = (state or "").strip()
    if clean_state:
        for b in candidates:
            saved_state = str((b.config or {}).get("manifest_state") or "").strip()
            if saved_state and py_secrets.compare_digest(saved_state, clean_state):
                secrets_map = decrypt_secrets(b.secrets_encrypted)
                app_id = str((b.config or {}).get("app_id") or "").strip()
                private_key = secrets_map.get("private_key", "").strip()
                if app_id and private_key:
                    try:
                        jwt_token = create_github_app_jwt(app_id, private_key)
                        api_base = _github_api_base_from_endpoint(b.endpoint)
                        async with httpx.AsyncClient(
                            timeout=6.0, follow_redirects=False
                        ) as client:
                            resp = await client.get(
                                f"{api_base}/app/installations/{installation_id.strip()}",
                                headers={
                                    "Accept": "application/vnd.github+json",
                                    "Authorization": f"Bearer {jwt_token}",
                                    "X-GitHub-Api-Version": "2022-11-28",
                                },
                            )
                            if 200 <= resp.status_code < 300:
                                target = b
                                break
                    except Exception:
                        continue
                else:
                    target = b
                    break

    if target is None:
        # Match by verifying which GitHub App owns this installation_id via JWT
        for b in candidates:
            secrets_map = decrypt_secrets(b.secrets_encrypted)
            app_id = str((b.config or {}).get("app_id") or "").strip()
            private_key = secrets_map.get("private_key", "").strip()
            if not app_id or not private_key:
                continue
            try:
                jwt_token = create_github_app_jwt(app_id, private_key)
                api_base = _github_api_base_from_endpoint(b.endpoint)
                async with httpx.AsyncClient(timeout=6.0, follow_redirects=False) as client:
                    resp = await client.get(
                        f"{api_base}/app/installations/{installation_id.strip()}",
                        headers={
                            "Accept": "application/vnd.github+json",
                            "Authorization": f"Bearer {jwt_token}",
                            "X-GitHub-Api-Version": "2022-11-28",
                        },
                    )
                    if 200 <= resp.status_code < 300:
                        target = b
                        break
            except Exception:
                continue

    if target is None:
        return None

    return await sync_github_app_installation(
        session, target.id, explicit_installation_id=installation_id.strip()
    )


_DEFAULT_GH_OAUTH_CLIENT_ID = "178c6fc778ccc68e1d6a"


async def start_github_device_flow(
    session: AsyncSession,
    admin: User,
    *,
    backend_id: str | None = None,
    name: str | None = None,
    provider: str = "github",
    endpoint: str | None = None,
) -> dict[str, Any]:
    """Start a browser-based GitHub OAuth Device Flow (`https://github.com/login/device`)."""
    norm_provider = (provider or "github").strip().lower()
    if norm_provider not in ("github", "github-enterprise"):
        raise Invalid("GitHub Device OAuth is only supported for github and github-enterprise")

    if endpoint is not None and endpoint.strip():
        _validate_backend_endpoint(BackendType.GIT, norm_provider, endpoint)
    clean_endpoint = _github_api_base_from_endpoint(endpoint)
    web_base = _github_web_base_from_endpoint(clean_endpoint)
    client_id = (
        os.environ.get("GITSEED_GITHUB_OAUTH_CLIENT_ID", "").strip()
        or _DEFAULT_GH_OAUTH_CLIENT_ID
    )

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        resp = await client.post(
            f"{web_base}/login/device/code",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "scope": "repo read:org workflow",
            },
        )
        if resp.status_code >= 300 or not isinstance(resp.json(), dict):
            raise Invalid(
                f"GitHub Device OAuth request failed (HTTP {resp.status_code})"
            )
        payload = resp.json()

    device_code = str(payload.get("device_code") or "").strip()
    user_code = str(payload.get("user_code") or "").strip()
    verification_uri = str(
        payload.get("verification_uri") or f"{web_base}/login/device"
    ).strip()
    if not device_code or not user_code:
        raise Invalid(
            str(payload.get("error_description") or "GitHub did not return a device code")
        )

    now = datetime.now(UTC)
    if backend_id:
        backend = await get_backend(session, backend_id)
        if name and name.strip():
            backend.name = name.strip()
        if endpoint is not None:
            backend.endpoint = clean_endpoint
        cfg = dict(backend.config or {})
        cfg["auth_mode"] = "device_oauth"
        cfg["device_status"] = "awaiting_user"
        cfg.pop("_device_code", None)
        cfg["device_user_code"] = user_code
        cfg["device_verification_uri"] = verification_uri
        backend.config = cfg
        secrets_map = decrypt_secrets(backend.secrets_encrypted)
        secrets_map["_device_code"] = device_code
        backend.secrets_encrypted = encrypt_secrets(secrets_map)
        backend.updated_at = now
        backend.updated_by = admin.id
        await session.commit()
        await session.refresh(backend)
    else:
        effective_name = (name or "").strip() or (
            "GitHub Enterprise" if norm_provider == "github-enterprise" else "GitHub"
        )
        cfg = {
            "auth_mode": "device_oauth",
            "device_status": "awaiting_user",
            "device_user_code": user_code,
            "device_verification_uri": verification_uri,
        }
        backend = await create_backend(
            session,
            admin,
            name=effective_name,
            type=BackendType.GIT,
            provider=norm_provider,
            endpoint=clean_endpoint,
            config=cfg,
            secrets={"_device_code": device_code},
            enabled=True,
        )

    return {
        "status": "awaiting_user",
        "auth_url": verification_uri,
        "user_code": user_code,
        "log_output": "",
        "authenticated": False,
        "error": None,
        "backend": backend,
    }


async def poll_github_device_flow(
    session: AsyncSession,
    admin: User,
    backend_id: str,
) -> dict[str, Any]:
    """Poll GitHub's OAuth token endpoint to complete an active Device Flow login."""
    backend = await get_backend(session, backend_id)
    cfg = dict(backend.config or {})
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    device_code = str(
        secrets_map.get("_device_code") or cfg.get("_device_code") or ""
    ).strip()
    user_code = str(cfg.get("device_user_code") or "").strip() or None
    verification_uri = str(cfg.get("device_verification_uri") or "").strip() or None

    if not device_code:
        is_authed = bool(secrets_map.get("token"))
        return {
            "status": "authenticated" if is_authed else "idle",
            "auth_url": verification_uri,
            "user_code": user_code,
            "log_output": "",
            "authenticated": is_authed,
            "error": None,
            "backend": backend,
        }

    clean_endpoint = _github_api_base_from_endpoint(backend.endpoint)
    web_base = _github_web_base_from_endpoint(clean_endpoint)
    client_id = (
        os.environ.get("GITSEED_GITHUB_OAUTH_CLIENT_ID", "").strip()
        or _DEFAULT_GH_OAUTH_CLIENT_ID
    )

    async with httpx.AsyncClient(timeout=8.0, follow_redirects=True) as client:
        resp = await client.post(
            f"{web_base}/login/oauth/access_token",
            headers={"Accept": "application/json"},
            data={
                "client_id": client_id,
                "device_code": device_code,
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )
        data = resp.json() if isinstance(resp.json(), dict) else {}

    access_token = str(data.get("access_token") or "").strip()
    if access_token:
        secrets_map.pop("_device_code", None)
        secrets_map["token"] = access_token
        backend.secrets_encrypted = encrypt_secrets(secrets_map)
        cfg.pop("_device_code", None)
        cfg["device_status"] = "authenticated"
        cfg["cli_auth_updated_at"] = datetime.now(UTC).isoformat()
        backend.config = dict(cfg)
        try:
            discovered = await _fetch_git_repositories(backend, secrets_map)
            existing_repos = cfg.get("repositories")
            cfg["repositories"] = _merge_discovered_repos(
                existing_repos if isinstance(existing_repos, list) else [],
                discovered,
            )
            backend.config = dict(cfg)
            backend.health = BackendHealth.HEALTHY
            backend.health_error = None
            backend.health_checked_at = datetime.now(UTC)
        except Exception:
            pass
        backend.updated_at = datetime.now(UTC)
        backend.updated_by = admin.id
        await session.commit()
        await session.refresh(backend)
        return {
            "status": "authenticated",
            "auth_url": verification_uri,
            "user_code": user_code,
            "log_output": "",
            "authenticated": True,
            "error": None,
            "backend": backend,
        }

    err_code = str(data.get("error") or "").strip()
    if err_code in ("authorization_pending", "slow_down", ""):
        return {
            "status": "awaiting_user",
            "auth_url": verification_uri,
            "user_code": user_code,
            "log_output": "",
            "authenticated": False,
            "error": None,
            "backend": backend,
        }

    err_desc = str(data.get("error_description") or err_code or "Device authorization failed")
    secrets_map.pop("_device_code", None)
    backend.secrets_encrypted = encrypt_secrets(secrets_map)
    cfg.pop("_device_code", None)
    cfg["device_status"] = "failed"
    backend.config = cfg
    await session.commit()
    await session.refresh(backend)
    return {
        "status": "failed",
        "auth_url": verification_uri,
        "user_code": user_code,
        "log_output": "",
        "authenticated": False,
        "error": err_desc,
        "backend": backend,
    }


_SDLC_STAGE_ROLES: tuple[tuple[SeedStage, str, str, str], ...] = (
    (
        SeedStage.IDEATE,
        "ideator",
        "Ideator",
        "Refines raw seed ideas into structured technical specifications and testable, bounded acceptance criteria tailored to the repository architecture.",
    ),
    (
        SeedStage.PLAN,
        "planner",
        "Planner",
        "Inspects the repository and produces a concrete file-by-file change manifest (exact files to create/modify/delete, key functions/classes/interfaces, and verification commands) plus an ordered task list without repeating the specification.",
    ),
    (
        SeedStage.EXECUTE,
        "coder",
        "Coder",
        "Executes approved task plans inside isolated container runners, writes unit and integration tests, verifies test suites pass, and pushes seed branches.",
    ),
    (
        SeedStage.REVIEW,
        "reviewer",
        "Reviewer",
        "Audits code diffs and pull requests against the approved specification, implementation plan, acceptance criteria, CI status checks, and security boundaries.",
    ),
    (
        SeedStage.MERGE,
        "merger",
        "Merger",
        "Verifies final pull request readiness, CI status checks, branch conflicts, and approval gates before completing branch integration into the default branch.",
    ),
)


def resolve_provider_sdlc_profile(
    backend: Backend,
) -> tuple[str, str, AgentRuntime, str]:
    """Return `(handle_prefix, display_prefix, runtime, default_model)` for an inference backend."""
    provider = (backend.provider or "").strip().lower()
    if provider == "anthropic":
        return "claude", "Claude", AgentRuntime.CLAUDE_CODE, "default"
    if provider == "antigravity":
        return "agy", "Antigravity", AgentRuntime.ANTIGRAVITY, "default"
    if provider == "openai":
        return "open", "OpenAI", AgentRuntime.CODEX, "default"
    if provider == "gemini":
        return "gemini", "Gemini", AgentRuntime.GEMINI, "default"
    if provider == "xai":
        cfg = backend.config or {}
        explicit_rt = str(cfg.get("runtime") or "").strip().lower()
        if explicit_rt == AgentRuntime.GROK_BUILD.value or is_personal_mode_backend(backend):
            return "grok", "Grok", AgentRuntime.GROK_BUILD, "default"
        enabled_models = extract_enabled_model_ids(backend)
        default_model = enabled_models[0] if enabled_models else "grok-4.7"
        return "grok", "Grok", AgentRuntime.OPENCODE, default_model

    enabled_models = extract_enabled_model_ids(backend)
    default_model = enabled_models[0] if enabled_models else "default"
    return "open", "OpenCode", AgentRuntime.OPENCODE, default_model


def _is_inference_backend_ready(backend: Backend) -> bool:
    if backend.type != BackendType.INFERENCE or not backend.enabled:
        return False
    cfg = backend.config or {}
    auth_mode = str(cfg.get("auth_mode") or "api_key").strip().lower()
    if auth_mode == "cli_oauth":
        secret_keys = set(get_secret_keys(backend))
        return (
            cfg.get("cli_auth_status") == "authenticated"
            or "cli_auth_bundle" in secret_keys
            or "oauth_token" in secret_keys
        )
    provider = (backend.provider or "").strip().lower()
    if provider in ("ollama", "lmstudio", "vllm", "custom", "antigravity"):
        return True
    return bool(get_secret_keys(backend))


async def provision_sdlc_agents_for_backend(
    session: AsyncSession,
    backend: Backend,
    *,
    force_switch: bool = False,
    assign_policies: bool = True,
    overwrite_existing: bool = True,
) -> list[Agent]:
    """Seed the 5 stage-dedicated SDLC agents (+ `@coordinator`) for an inference backend, optionally assigning them as the active org policy defaults."""
    if backend.type != BackendType.INFERENCE:
        raise Invalid("Only Inference backends can provision SDLC stage agents")

    is_personal = is_personal_mode_backend(backend) or (
        str((backend.config or {}).get("auth_mode") or "").strip().lower() == "cli_oauth"
    )
    handle_prefix, display_prefix, runtime, default_model = resolve_provider_sdlc_profile(
        backend
    )
    org_policies = await ensure_org_policies(session)

    if assign_policies:
        # Mark this backend as `is_sdlc_default: True` and clear the flag on other inference backends
        all_inference = (
            await session.execute(
                select(Backend).where(Backend.type == BackendType.INFERENCE)
            )
        ).scalars().all()
        for b in all_inference:
            b_cfg = dict(b.config or {})
            should_be_default = b.id == backend.id
            if bool(b_cfg.get("is_sdlc_default")) != should_be_default:
                b_cfg["is_sdlc_default"] = should_be_default
                if not should_be_default:
                    b_cfg.pop("set_sdlc_default", None)
                b.config = b_cfg

    # 1. Ensure `@coordinator` assistant agent exists (and uses the primary runtime when switching defaults)
    coordinator = (
        await session.execute(
            select(Agent)
            .where(Agent.handle == "coordinator")
            .order_by(col(Agent.archived_at).is_not(None).asc(), col(Agent.created_at).asc())
        )
    ).scalars().first()
    if coordinator is None:
        coordinator = Agent(
            name="Coordinator",
            handle="coordinator",
            kind=AgentKind.ASSISTANT,
            template="coordinator",
            runtime=runtime,
            model=default_model,
            instructions=(
                "System assistant for workspace steering, conversational search, "
                "seed triage, and pipeline inspection."
            ),
            enabled=True,
        )
        session.add(coordinator)
    elif (
        overwrite_existing
        and assign_policies
        and (force_switch or coordinator.archived_at is not None)
    ):
        existing_coord_mb = getattr(coordinator, "models_backend_id", None)
        if not (is_personal and existing_coord_mb and existing_coord_mb != backend.id):
            coordinator.runtime = runtime
            coordinator.model = default_model
            coordinator.enabled = True
            coordinator.archived_at = None

    provisioned_agents: list[Agent] = [coordinator]

    # 2. Provision the 5 stage-specific agents (`{prefix}-ideator`, `{prefix}-planner`, `{prefix}-coder`, `{prefix}-reviewer`, `{prefix}-merger`)
    for stage, role_slug, role_title, instructions in _SDLC_STAGE_ROLES:
        target_handle = f"{handle_prefix}-{role_slug}"
        target_name = f"{display_prefix} {role_title}"
        pol = org_policies.get(stage)

        # Check if an agent with the target handle already exists
        target_agent = (
            await session.execute(
                select(Agent)
                .where(Agent.handle == target_handle)
                .order_by(col(Agent.archived_at).is_not(None).asc(), col(Agent.created_at).asc())
            )
        ).scalars().first()

        # If switching primary provider during onboarding, check if the currently assigned stage agent has 0 runs
        # so we can cleanly rename/re-runtime it in-place rather than leaving unused agents behind
        current_stage_agent: Agent | None = None
        if assign_policies and pol is not None and pol.agent_id is not None:
            current_stage_agent = await session.get(Agent, pol.agent_id)

        if (
            overwrite_existing
            and assign_policies
            and force_switch
            and target_agent is None
            and current_stage_agent is not None
            and current_stage_agent.handle == role_slug
            and not (
                is_personal
                and getattr(current_stage_agent, "models_backend_id", None) not in (None, backend.id)
            )
        ):
            run_cnt = int(
                (
                    await session.scalar(
                        select(func.count())
                        .select_from(Run)
                        .where(Run.agent_id == current_stage_agent.id)
                    )
                )
                or 0
            )
            other_stage_refs = sum(
                1
                for st, p_row in org_policies.items()
                if st != stage and p_row.agent_id == current_stage_agent.id
            )
            if run_cnt == 0 and other_stage_refs == 0:
                current_stage_agent.handle = target_handle
                current_stage_agent.name = target_name
                current_stage_agent.template = role_slug
                current_stage_agent.runtime = runtime
                current_stage_agent.model = default_model
                current_stage_agent.instructions = instructions
                current_stage_agent.enabled = True
                current_stage_agent.archived_at = None
                current_stage_agent.updated_at = datetime.now(UTC)
                target_agent = current_stage_agent

        if target_agent is None:
            target_agent = Agent(
                name=target_name,
                handle=target_handle,
                kind=AgentKind.STAGE,
                template=role_slug,
                runtime=runtime,
                model=default_model,
                instructions=instructions,
                enabled=True,
            )
            session.add(target_agent)
            await session.flush()
        elif overwrite_existing:
            existing_mb = getattr(target_agent, "models_backend_id", None)
            if not (is_personal and existing_mb and existing_mb != backend.id):
                target_agent.name = target_name
                target_agent.template = role_slug
                target_agent.runtime = runtime
                target_agent.model = default_model
                target_agent.enabled = True
                target_agent.archived_at = None
                target_agent.updated_at = datetime.now(UTC)

        if pol is not None and (
            (pol.agent_id is None and assign_policies)
            or (overwrite_existing and assign_policies and force_switch)
        ):
            pol.agent_id = target_agent.id
            pol.updated_at = datetime.now(UTC)

        provisioned_agents.append(target_agent)

    await session.commit()
    await session.refresh(backend)
    return provisioned_agents


async def maybe_auto_provision_sdlc_agents(
    session: AsyncSession,
    backend: Backend,
) -> None:
    """Automatically seed the 5 provider-prefixed SDLC stage agents when an inference backend is saved."""
    if backend.type != BackendType.INFERENCE or not backend.enabled:
        return
    cfg = dict(backend.config or {})
    auto_create_agents = cfg.get("auto_create_agents", True) is not False
    explicit_set_default = bool(cfg.get("set_sdlc_default", False))
    if not auto_create_agents and not explicit_set_default:
        return

    is_personal = is_personal_mode_backend(backend) or (
        str(cfg.get("auth_mode") or "").strip().lower() == "cli_oauth"
    )
    if is_personal:
        if not settings.GITSEED_ALLOW_PERSONAL_CLI_OAUTH:
            return
        try:
            raw_user_cnt = await session.scalar(select(func.count()).select_from(User))
            user_count = int(raw_user_cnt) if isinstance(raw_user_cnt, int) else 0
        except Exception:
            user_count = 0
        if user_count > 1 and not settings.ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS:
            return

    if not _is_inference_backend_ready(backend):
        if auto_create_agents:
            await provision_sdlc_agents_for_backend(
                session,
                backend,
                force_switch=False,
                assign_policies=False,
                overwrite_existing=False,
            )
        return

    org_policies = await ensure_org_policies(session)
    any_missing_stage_agent = any(p.agent_id is None for p in org_policies.values())
    all_inference = (
        await session.execute(
            select(Backend).where(Backend.type == BackendType.INFERENCE)
        )
    ).scalars().all()
    other_ready_inference = [
        b for b in all_inference if b.id != backend.id and _is_inference_backend_ready(b)
    ]
    has_other_explicit_default = any(
        bool((b.config or {}).get("is_sdlc_default"))
        for b in all_inference
        if b.id != backend.id
    )

    if (
        is_personal
        and not explicit_set_default
        and (
            len(other_ready_inference) > 0
            or has_other_explicit_default
            or not any_missing_stage_agent
        )
    ):
        await provision_sdlc_agents_for_backend(
            session,
            backend,
            force_switch=False,
            assign_policies=False,
            overwrite_existing=False,
        )
        return

    should_assign_policies = (
        explicit_set_default
        or any_missing_stage_agent
        or (not has_other_explicit_default and len(other_ready_inference) == 0)
    )
    await provision_sdlc_agents_for_backend(
        session,
        backend,
        force_switch=should_assign_policies,
        assign_policies=should_assign_policies,
        overwrite_existing=should_assign_policies,
    )


async def set_sdlc_default_backend(
    session: AsyncSession,
    admin: User,
    backend_id: str,
) -> Backend:
    """Explicitly set an inference backend as the primary SDLC default and update the 5 default stage agents + org policies."""
    backend = await get_backend(session, backend_id)
    if backend.type != BackendType.INFERENCE:
        raise Invalid("Only Inference backends can be selected as the SDLC default")
    await provision_sdlc_agents_for_backend(session, backend, force_switch=True)
    record(
        session,
        type=ActivityType.POLICY_UPDATED,
        actor_type=ActorType.USER,
        actor_id=admin.id,
        data={
            "backend_id": backend.id,
            "name": backend.name,
            "sdlc_default": True,
        },
    )
    await session.commit()
    await session.refresh(backend)
    return backend






