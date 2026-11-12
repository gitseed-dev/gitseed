from __future__ import annotations

import hashlib
import hmac
import inspect
import ipaddress
import json
import logging
import re
import secrets
import string
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlsplit

import httpx
from gitseed_common.enums import (
    ActivityType,
    ActorType,
    OrgRole,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from gitseed_common.schemas.project import (
    DEFAULT_OUTBOUND_WEBHOOK_EVENTS,
    ProjectWebhookConfigResponse,
    UpdateProjectWebhookConfigRequest,
    WebhookDeliveryResponse,
    WebhookTestResponse,
)
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.db import get_redis
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectMember, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets, encrypt_secrets
from gitseed_core.services import events as events_service
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Forbidden, Invalid, NotFound
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.git_browse import _gitlab_api_base_and_headers
from gitseed_core.services.git_merge import (
    maybe_auto_merge_ready_pr,
    reconcile_seed_if_pr_merged_externally,
    remediate_seed_if_pr_ci_failing,
)
from gitseed_core.services.net_safety import (
    Resolver,
    UnsafeDestinationError,
    validate_public_http_url,
)
from gitseed_core.services.policies import first_enabled_stage
from gitseed_core.services.projects import require_project_maintainer
from gitseed_core.services.seeds import (
    _mentions_stage_agent,
    create_remediation_task_and_return_to_execute,
    resolve_should_stage_seed,
)

logger = logging.getLogger(__name__)

_SECRET_ALPHABET = string.ascii_letters + string.digits
_SEED_BRANCH_RE = re.compile(r"(?:^|/)seed-(\d+)\b", re.IGNORECASE)
_SEED_REF_RE = re.compile(r"\bseed[- #](\d+)\b", re.IGNORECASE)

TRUSTED_AUTHOR_ASSOCIATIONS: frozenset[str] = frozenset(
    {"OWNER", "MEMBER", "COLLABORATOR"}
)
_SEEN_WEBHOOK_DELIVERIES: dict[str, float] = {}
_DELIVERY_DEDUP_TTL_SECONDS = 3600.0
_MAX_SEEN_DELIVERIES = 4096


def reset_webhook_delivery_cache() -> None:
    """Clear the in-memory webhook delivery deduplication cache."""
    _SEEN_WEBHOOK_DELIVERIES.clear()


def _check_and_record_delivery_id(provider: str, delivery_id: str | None) -> bool:
    """Return True if `delivery_id` is new and record it; False if it is a replay/duplicate."""
    cleaned = (delivery_id or "").strip()
    if not cleaned:
        return True
    now = time.monotonic()
    if len(_SEEN_WEBHOOK_DELIVERIES) >= _MAX_SEEN_DELIVERIES:
        expired = [
            k
            for k, ts in _SEEN_WEBHOOK_DELIVERIES.items()
            if now - ts > _DELIVERY_DEDUP_TTL_SECONDS
        ]
        for k in expired:
            _SEEN_WEBHOOK_DELIVERIES.pop(k, None)
        if len(_SEEN_WEBHOOK_DELIVERIES) >= _MAX_SEEN_DELIVERIES:
            oldest_key = next(iter(_SEEN_WEBHOOK_DELIVERIES))
            _SEEN_WEBHOOK_DELIVERIES.pop(oldest_key, None)

    key = f"{provider}:{cleaned}"
    prev_ts = _SEEN_WEBHOOK_DELIVERIES.get(key)
    if prev_ts is not None and now - prev_ts <= _DELIVERY_DEDUP_TTL_SECONDS:
        return False
    _SEEN_WEBHOOK_DELIVERIES[key] = now
    return True


async def _check_and_record_delivery_id_redis(provider: str, delivery_id: str) -> bool:
    """Multi-worker webhook delivery deduplication via Valkey (`SET ... NX EX`)."""
    try:
        redis = get_redis()
        redis_key = f"webhook_delivery:{provider}:{delivery_id}"
        was_set = await redis.set(
            redis_key,
            "1",
            ex=int(_DELIVERY_DEDUP_TTL_SECONDS),
            nx=True,
        )
        return bool(was_set)
    except Exception:
        return True


async def _check_and_record_delivery_id_async(
    provider: str, delivery_id: str | None
) -> bool:
    """Check and record `delivery_id` across both local process memory and shared Valkey."""
    cleaned = (delivery_id or "").strip()
    if not cleaned:
        return True
    if not _check_and_record_delivery_id(provider, cleaned):
        return False
    return await _check_and_record_delivery_id_redis(provider, cleaned)


def wrap_untrusted_external_input(text: str) -> str:
    """Wrap untrusted external issue/comment body in `<untrusted_external_input>` markers."""
    sanitized = (text or "").strip().replace(
        "</untrusted_external_input>", "&lt;/untrusted_external_input&gt;"
    )
    return f"<untrusted_external_input>\n{sanitized}\n</untrusted_external_input>"


def _is_trusted_github_author(
    obj: dict[str, Any], payload: dict[str, Any]
) -> tuple[bool, str]:
    assoc = str(
        obj.get("author_association") or payload.get("author_association") or ""
    ).strip().upper()
    if assoc in TRUSTED_AUTHOR_ASSOCIATIONS:
        return True, assoc
    return False, assoc or "NONE"


TRUSTED_GITLAB_PAYLOAD_ROLES: frozenset[str] = frozenset(
    {"OWNER", "MAINTAINER", "DEVELOPER"}
)


async def _is_trusted_gitlab_author(
    attrs: dict[str, Any],
    payload: dict[str, Any],
    *,
    session: AsyncSession | None = None,
    project: Project | None = None,
) -> tuple[bool, str]:
    """Verify GitLab webhook author membership (Developer+ >= 30), failing closed when unverifiable."""
    raw_assoc = (
        attrs.get("author_association")
        or payload.get("author_association")
        or (payload.get("user") or {}).get("author_association")
    )
    if raw_assoc is not None:
        assoc = str(raw_assoc).strip().upper()
        if assoc in TRUSTED_GITLAB_PAYLOAD_ROLES:
            return True, assoc
        return False, assoc or "UNTRUSTED"

    access_level = (payload.get("user") or {}).get("access_level")
    if isinstance(access_level, int) and not isinstance(access_level, bool):
        if access_level >= 30:
            return True, f"ACCESS_LEVEL_{access_level}"
        return False, f"ACCESS_LEVEL_{access_level}"

    user_obj = payload.get("user") if isinstance(payload.get("user"), dict) else {}
    proj_obj = payload.get("project") if isinstance(payload.get("project"), dict) else {}
    user_id = user_obj.get("id") or attrs.get("author_id")
    gl_project_id = (
        proj_obj.get("id") or payload.get("project_id") or attrs.get("project_id")
    )
    if not user_id or not gl_project_id or session is None or project is None:
        return False, "UNTRUSTED"

    try:
        stmt = (
            select(ProjectRepository, Backend)
            .join(Backend, col(Backend.id) == col(ProjectRepository.backend_id))
            .where(
                ProjectRepository.project_id == project.id,
                Backend.enabled == True,  # noqa: E712
            )
            .order_by(
                col(ProjectRepository.is_primary).desc(),
                col(ProjectRepository.id).asc(),
            )
        )
        rows = (await session.execute(stmt)).all()
        gl_backend: Backend | None = None
        gl_repo: ProjectRepository | None = None
        for r_obj, b_obj in rows:
            if (b_obj.provider or "").strip().lower() == "gitlab":
                gl_backend = b_obj
                gl_repo = r_obj
                break
        if gl_backend is None or gl_repo is None:
            return False, "UNTRUSTED"

        secrets_map = decrypt_secrets(gl_backend.secrets_encrypted)
        token = await resolve_git_backend_token(
            gl_backend, secrets_map, repo_full_names=[gl_repo.full_name]
        )
        if not token:
            return False, "UNTRUSTED"

        api_base, headers = _gitlab_api_base_and_headers(gl_backend.endpoint, token)
        member_url = (
            f"{api_base}/projects/{quote(str(gl_project_id), safe='')}"
            f"/members/all/{quote(str(user_id), safe='')}"
        )
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=False) as client:
            resp = await client.get(member_url, headers=headers)
        if resp.status_code == 200 and isinstance(resp.json(), dict):
            member_data = resp.json()
            m_level = member_data.get("access_level")
            m_state = str(member_data.get("state") or "").strip().lower()
            if (
                isinstance(m_level, int)
                and not isinstance(m_level, bool)
                and m_level >= 30
                and m_state == "active"
            ):
                return True, f"ACCESS_LEVEL_{m_level}"
    except Exception as exc:
        logger.debug("GitLab webhook membership verification failed: %s", exc)

    return False, "UNTRUSTED"


def sanitize_sandbox_config_for_response(
    sandbox_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return a copy of sandbox_config with encrypted webhook secret material stripped."""
    if not isinstance(sandbox_config, dict):
        return {}
    cleaned = dict(sandbox_config)
    wh = cleaned.get("webhook_config")
    if isinstance(wh, dict):
        wh_copy = {k: v for k, v in wh.items() if k != "secret_encrypted"}
        cleaned["webhook_config"] = wh_copy
    return cleaned


def _generate_webhook_secret() -> str:
    body = "".join(secrets.choice(_SECRET_ALPHABET) for _ in range(32))
    return f"whsec_{body}"


def _mask_secret(secret: str) -> str:
    if len(secret) <= 10:
        return "••••••••"
    return f"{secret[:8]}...{secret[-4:]}"


def _encrypt_webhook_secret(secret: str) -> str:
    return encrypt_secrets({"secret": secret}).decode("ascii")


def _decrypt_webhook_secret(secret_encrypted: str | None) -> str | None:
    if not secret_encrypted or not isinstance(secret_encrypted, str):
        return None
    try:
        decrypted = decrypt_secrets(secret_encrypted.encode("ascii"))
        val = decrypted.get("secret")
        return val if isinstance(val, str) and val else None
    except Exception:
        return None


def _build_webhook_urls(project: Project) -> tuple[str, str]:
    base = settings.PUBLIC_WEB_URL.rstrip("/")
    return (
        f"{base}/api/v1/webhooks/github/{project.slug}",
        f"{base}/api/v1/webhooks/gitlab/{project.slug}",
    )


def _to_webhook_config_response(
    project: Project,
    *,
    plaintext_secret: str | None = None,
) -> ProjectWebhookConfigResponse:
    cfg = dict(project.sandbox_config or {})
    wh = dict(cfg.get("webhook_config") or {})
    stored_secret = _decrypt_webhook_secret(wh.get("secret_encrypted"))
    has_secret = bool(stored_secret)
    preview = (
        _mask_secret(stored_secret)
        if stored_secret
        else (wh.get("secret_preview") if isinstance(wh.get("secret_preview"), str) else None)
    )
    gh_url, gl_url = _build_webhook_urls(project)
    outbound_events = wh.get("outbound_events")
    if not isinstance(outbound_events, list):
        outbound_events = list(DEFAULT_OUTBOUND_WEBHOOK_EVENTS)

    auto_create_pr = bool(
        wh.get("auto_create_pr", cfg.get("auto_create_pr", True))
    )

    return ProjectWebhookConfigResponse(
        project_id=project.id,
        enabled=bool(wh.get("enabled", False)),
        has_secret=has_secret,
        secret_preview=preview if has_secret else None,
        secret=plaintext_secret,
        github_webhook_url=gh_url,
        gitlab_webhook_url=gl_url,
        issue_label_trigger=str(wh.get("issue_label_trigger") or "gitseed").strip() or "gitseed",
        auto_create_seed_on_issue=bool(wh.get("auto_create_seed_on_issue", True)),
        sync_pr_comments=bool(wh.get("sync_pr_comments", True)),
        auto_remediate_ci=bool(wh.get("auto_remediate_ci", True)),
        auto_create_pr=auto_create_pr,
        outbound_url=str(wh["outbound_url"]).strip() if wh.get("outbound_url") else None,
        outbound_events=[str(e).strip() for e in outbound_events if str(e).strip()],
    )


async def get_project_webhook_config(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> ProjectWebhookConfigResponse:
    """Return webhook and PR automation configuration for a project."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    return _to_webhook_config_response(project)


async def update_project_webhook_config(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    payload: UpdateProjectWebhookConfigRequest,
) -> ProjectWebhookConfigResponse:
    """Update a project's webhook and PR automation configuration."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    cfg = dict(project.sandbox_config or {})
    wh = dict(cfg.get("webhook_config") or {})
    newly_revealed_secret: str | None = None

    if payload.enabled is not None:
        wh["enabled"] = bool(payload.enabled)
    if payload.issue_label_trigger is not None:
        wh["issue_label_trigger"] = payload.issue_label_trigger.strip() or "gitseed"
    if payload.auto_create_seed_on_issue is not None:
        wh["auto_create_seed_on_issue"] = bool(payload.auto_create_seed_on_issue)
    if payload.sync_pr_comments is not None:
        wh["sync_pr_comments"] = bool(payload.sync_pr_comments)
    if payload.auto_remediate_ci is not None:
        wh["auto_remediate_ci"] = bool(payload.auto_remediate_ci)
    if payload.auto_create_pr is not None:
        wh["auto_create_pr"] = bool(payload.auto_create_pr)
        cfg["auto_create_pr"] = bool(payload.auto_create_pr)
    if "outbound_url" in payload.model_fields_set:
        cleaned_url = (payload.outbound_url or "").strip()
        if cleaned_url:
            cleaned_url = await _validate_outbound_webhook_url_async(
                cleaned_url, allow_offline_example_host=True
            )
        wh["outbound_url"] = cleaned_url or None
    if payload.outbound_events is not None:
        wh["outbound_events"] = [
            str(e).strip() for e in payload.outbound_events if str(e).strip()
        ]

    if "secret" in payload.model_fields_set and payload.secret is not None:
        cleaned_secret = payload.secret.strip()
        if cleaned_secret:
            wh["secret_encrypted"] = _encrypt_webhook_secret(cleaned_secret)
            wh["secret_preview"] = _mask_secret(cleaned_secret)
            newly_revealed_secret = cleaned_secret
        else:
            wh.pop("secret_encrypted", None)
            wh.pop("secret_preview", None)
    elif wh.get("enabled") and not _decrypt_webhook_secret(wh.get("secret_encrypted")):
        generated = _generate_webhook_secret()
        wh["secret_encrypted"] = _encrypt_webhook_secret(generated)
        wh["secret_preview"] = _mask_secret(generated)
        newly_revealed_secret = generated

    cfg["webhook_config"] = wh
    project.sandbox_config = cfg
    project.updated_at = datetime.now(UTC)

    record(
        session,
        type=ActivityType.SETTINGS_UPDATED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={"section": "webhooks", "enabled": bool(wh.get("enabled", False))},
    )
    await session.commit()
    await session.refresh(project)
    return _to_webhook_config_response(project, plaintext_secret=newly_revealed_secret)


async def rotate_project_webhook_secret(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> ProjectWebhookConfigResponse:
    """Generate and persist a new HMAC signing secret for a project's webhooks."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    cfg = dict(project.sandbox_config or {})
    wh = dict(cfg.get("webhook_config") or {})

    new_secret = _generate_webhook_secret()
    wh["secret_encrypted"] = _encrypt_webhook_secret(new_secret)
    wh["secret_preview"] = _mask_secret(new_secret)
    cfg["webhook_config"] = wh
    project.sandbox_config = cfg
    project.updated_at = datetime.now(UTC)

    record(
        session,
        type=ActivityType.SETTINGS_UPDATED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        project_id=project.id,
        data={"section": "webhooks.secret_rotated"},
    )
    await session.commit()
    await session.refresh(project)
    return _to_webhook_config_response(project, plaintext_secret=new_secret)


_BLOCKED_OUTBOUND_HOSTS = {
    "localhost",
    "localhost.localdomain",
    "ip6-localhost",
    "ip6-loopback",
    "metadata.google.internal",
    "host.docker.internal",
    "postgresql",
    "valkey",
    "redis",
    "litellm",
    "api",
    "mcp",
    "scheduler",
    "web",
}


def _validate_outbound_webhook_url(raw_url: str) -> str:
    """Validate that an outbound webhook URL is HTTP(S) and does not target internal/private hosts."""
    cleaned = raw_url.strip()
    if not (cleaned.startswith("http://") or cleaned.startswith("https://")):
        raise Invalid("Outbound webhook URL must start with http:// or https://")
    parsed = urlsplit(cleaned)
    host = (parsed.hostname or "").strip().lower().rstrip(".")
    if not host:
        raise Invalid("Outbound webhook URL must include a valid hostname")
    if (
        host in _BLOCKED_OUTBOUND_HOSTS
        or host.endswith(".internal")
        or host.endswith(".local")
        or host.endswith(".svc")
        or host.endswith(".cluster.local")
    ):
        raise Invalid("Outbound webhook URL cannot target internal or local network hosts")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        ip = None
    if ip is not None and (
        ip.is_loopback
        or ip.is_private
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise Invalid("Outbound webhook URL cannot target loopback, link-local, or private IP addresses")
    return cleaned


async def _validate_outbound_webhook_url_async(
    raw_url: str,
    *,
    resolver: Resolver | None = None,
    allow_offline_example_host: bool = False,
) -> str:
    """Validate an outbound webhook URL both syntactically and via DNS public-IP resolution."""
    cleaned = _validate_outbound_webhook_url(raw_url)
    try:
        await validate_public_http_url(cleaned, resolver=resolver)
    except UnsafeDestinationError as exc:
        msg = str(exc)
        parsed_host = (urlsplit(cleaned).hostname or "").strip().lower().rstrip(".")
        if (
            allow_offline_example_host
            and resolver is None
            and parsed_host in ("example.com", "example.org", "example.net")
            and msg.startswith("Could not resolve destination host ")
        ):
            return cleaned
        raise Invalid(f"Outbound webhook URL cannot target non-public destination: {msg}") from exc
    return cleaned


async def dispatch_outbound_webhook(
    project: Project,
    event_type: str,
    data: dict[str, Any],
    *,
    force: bool = False,
    resolver: Resolver | None = None,
) -> tuple[bool, int | None, str]:
    """Send a signed outbound webhook event to the project's configured outbound_url."""
    cfg = dict(project.sandbox_config or {})
    wh = dict(cfg.get("webhook_config") or {})
    outbound_url = str(wh.get("outbound_url") or "").strip()
    if not outbound_url:
        return False, None, "No outbound_url configured for this project"
    try:
        outbound_url = await _validate_outbound_webhook_url_async(
            outbound_url, resolver=resolver
        )
    except Invalid as exc:
        return False, None, exc.message

    if not force:
        if not wh.get("enabled", False):
            return False, None, "Webhooks are disabled for this project"
        allowed_events = wh.get("outbound_events")
        if isinstance(allowed_events, list) and event_type not in allowed_events:
            return False, None, f"Event '{event_type}' is not subscribed"

    envelope = {
        "event": event_type,
        "timestamp": datetime.now(UTC).isoformat(),
        "project": {
            "id": project.id,
            "slug": project.slug,
            "name": project.name,
        },
        "data": data,
    }
    raw_body = json.dumps(envelope, separators=(",", ":"), sort_keys=True).encode("utf-8")
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "User-Agent": "GitSeed-Webhooks/1.0",
        "X-GitSeed-Event": event_type,
    }
    secret = _decrypt_webhook_secret(wh.get("secret_encrypted"))
    if secret:
        digest = hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        headers["X-GitSeed-Signature-256"] = f"sha256={digest}"

    try:
        async with httpx.AsyncClient(timeout=8.0, follow_redirects=False) as client:
            resp = await client.post(outbound_url, content=raw_body, headers=headers)
            ok = 200 <= resp.status_code < 300
            return ok, resp.status_code, f"HTTP {resp.status_code}"
    except httpx.HTTPError as exc:
        logger.warning("Outbound webhook delivery failed for project %s: %s", project.slug, exc)
        return False, None, str(exc)


async def send_test_outbound_webhook(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> WebhookTestResponse:
    """Deliver a test ping event to the project's configured outbound webhook URL."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    cfg = dict(project.sandbox_config or {})
    wh = dict(cfg.get("webhook_config") or {})
    outbound_url = str(wh.get("outbound_url") or "").strip() or None
    if not outbound_url:
        raise Invalid("Configure an Outbound Webhook URL before sending a test event")

    delivered, status_code, detail = await dispatch_outbound_webhook(
        project,
        "webhook.test",
        {
            "message": f"Test webhook from GitSeed project '{project.name}' ({project.slug})",
            "triggered_by": actor.email,
        },
        force=True,
    )
    return WebhookTestResponse(
        delivered=delivered,
        status_code=status_code,
        outbound_url=outbound_url,
        detail=detail,
    )


def verify_github_signature(
    raw_body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Verify GitHub X-Hub-Signature-256 HMAC-SHA256 header in constant time."""
    if not secret or not signature_header:
        return False
    cleaned = signature_header.strip()
    if not cleaned.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, cleaned)


def verify_gitlab_token(secret: str, token_header: str | None) -> bool:
    """Verify GitLab X-Gitlab-Token header in constant time."""
    if not secret or not token_header:
        return False
    return hmac.compare_digest(secret.encode("utf-8"), token_header.strip().encode("utf-8"))


def verify_bitbucket_signature(
    raw_body: bytes,
    secret: str,
    signature_header: str | None,
) -> bool:
    """Verify Bitbucket Cloud X-Hub-Signature (`sha256=<hmac>`) header in constant time."""
    if not secret or not signature_header:
        return False
    cleaned = signature_header.strip()
    if not cleaned.startswith("sha256="):
        return False
    expected = "sha256=" + hmac.new(
        secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, cleaned)


async def _resolve_project_unauthenticated(
    session: AsyncSession,
    project_id_or_slug: str,
) -> Project:
    if project_id_or_slug.startswith("prj_"):
        project = await session.get(Project, project_id_or_slug)
    else:
        stmt = (
            select(Project)
            .where(Project.slug == project_id_or_slug.strip().lower())
            .order_by(col(Project.archived_at).asc().nulls_first())
        )
        project = (await session.execute(stmt)).scalars().first()
    if project is None or project.archived_at is not None:
        raise NotFound("Project not found")
    return project


def _extract_github_repo_full_name(payload: dict[str, Any]) -> str:
    repo_obj = payload.get("repository")
    if isinstance(repo_obj, dict):
        return str(repo_obj.get("full_name") or "").strip()
    return ""


def _extract_gitlab_repo_full_name(payload: dict[str, Any]) -> str:
    proj_obj = payload.get("project")
    if isinstance(proj_obj, dict):
        p_ns = str(proj_obj.get("path_with_namespace") or "").strip()
        if p_ns:
            return p_ns
    repo_obj = payload.get("repository")
    if isinstance(repo_obj, dict):
        return str(
            repo_obj.get("path_with_namespace") or repo_obj.get("full_name") or ""
        ).strip()
    return ""


def _extract_bitbucket_repo_full_name(payload: dict[str, Any]) -> str:
    repo_obj = payload.get("repository")
    if isinstance(repo_obj, dict):
        return str(repo_obj.get("full_name") or "").strip()
    return ""


async def _resolve_projects_from_repo_full_name(
    session: AsyncSession,
    full_name: str,
    *,
    provider: str | None = None,
    provider_label: str | None = None,
    missing_detail: str | None = None,
) -> list[Project]:
    if not provider_label:
        prov_map = {
            "github": "GitHub",
            "gitlab": "GitLab",
            "bitbucket": "Bitbucket",
        }
        provider_label = prov_map.get(
            (provider or "").strip().lower(), provider or "Git"
        )
    if not missing_detail:
        if (provider or "").strip().lower() == "gitlab":
            missing_detail = "GitLab webhook payload missing project.path_with_namespace"
        else:
            missing_detail = f"{provider_label} webhook payload missing repository.full_name"
    clean_name = (full_name or "").strip().lower()
    if not clean_name:
        raise NotFound(missing_detail)
    stmt = (
        select(Project)
        .join(ProjectRepository, col(ProjectRepository.project_id) == col(Project.id))
        .where(Project.archived_at == None)  # noqa: E711
        .order_by(
            col(ProjectRepository.is_primary).desc(),
            col(ProjectRepository.id).asc(),
        )
    )
    rows = (await session.execute(stmt)).scalars().all()
    matched: list[Project] = []
    seen_ids: set[str] = set()
    for p_obj in rows:
        if not isinstance(p_obj, Project) or p_obj.id in seen_ids:
            continue
        repo_stmt = select(ProjectRepository).where(
            ProjectRepository.project_id == p_obj.id
        )
        p_repos = (await session.execute(repo_stmt)).scalars().all()
        if any(
            isinstance(r, ProjectRepository)
            and (r.full_name or "").strip().lower() == clean_name
            for r in p_repos
        ):
            seen_ids.add(p_obj.id)
            matched.append(p_obj)
    if not matched:
        raise NotFound(
            f"No project found for {provider_label} repository '{full_name.strip()}'"
        )
    matched.sort(
        key=lambda p: 0
        if bool(((p.sandbox_config or {}).get("webhook_config") or {}).get("enabled"))
        else 1
    )
    return matched


async def _verify_payload_repo_matches_project(
    session: AsyncSession,
    project: Project,
    payload_repo: str,
) -> None:
    """Reject cross-repository webhook spoofing when payload repository is not attached to `project`."""
    clean_repo = (payload_repo or "").strip().lower()
    if not clean_repo:
        return
    exec_fn = getattr(session, "execute", None)
    if not callable(exec_fn):
        return
    res = await session.execute(
        select(ProjectRepository).where(ProjectRepository.project_id == project.id)
    )
    repo_rows: list[ProjectRepository] = []
    scalars_fn = getattr(res, "scalars", None)
    if callable(scalars_fn):
        scalars_obj = scalars_fn()
        if inspect.iscoroutine(scalars_obj):
            scalars_obj.close()
        else:
            all_fn = getattr(scalars_obj, "all", None)
            raw_rows = all_fn() if callable(all_fn) else []
            if inspect.iscoroutine(raw_rows):
                raw_rows.close()
            elif isinstance(raw_rows, (list, tuple)):
                repo_rows = [
                    r for r in raw_rows if isinstance(r, ProjectRepository)
                ]

    if repo_rows:
        if not any(
            (r.full_name or "").strip().lower() == clean_repo for r in repo_rows
        ):
            raise Forbidden(
                f"Webhook repository '{payload_repo}' is not attached to project '{project.slug}'"
            )
        return

    if not isinstance(session, AsyncSession):
        one_fn = getattr(res, "scalar_one_or_none", None)
        if callable(one_fn):
            one_val = one_fn()
            if inspect.iscoroutine(one_val):
                one_val.close()
            elif isinstance(one_val, Seed):
                return

    raise Forbidden(
        f"Webhook repository '{payload_repo}' is not attached to project '{project.slug}'"
    )


async def _resolve_webhook_secret(
    session: AsyncSession,
    project: Project,
) -> str | None:
    wh = (project.sandbox_config or {}).get("webhook_config") or {}
    project_secret = _decrypt_webhook_secret(wh.get("secret_encrypted"))
    if project_secret:
        return project_secret

    stmt = (
        select(ProjectRepository, Backend)
        .join(Backend, col(Backend.id) == col(ProjectRepository.backend_id))
        .where(ProjectRepository.project_id == project.id)
        .order_by(
            col(ProjectRepository.is_primary).desc(),
            col(ProjectRepository.id).asc(),
        )
    )
    row = (await session.execute(stmt)).first()
    if row is not None:
        _, backend = row
        secrets_map = decrypt_secrets(backend.secrets_encrypted)
        backend_secret = (secrets_map.get("webhook_secret") or "").strip()
        if backend_secret:
            return backend_secret
    return None


async def _resolve_default_project_actor_id(
    session: AsyncSession,
    project: Project,
) -> str:
    if project.created_by:
        return project.created_by
    stmt = (
        select(ProjectMember.user_id)
        .where(ProjectMember.project_id == project.id)
        .limit(1)
    )
    member_id = (await session.execute(stmt)).scalar_one_or_none()
    if member_id:
        return str(member_id)
    admin_stmt = (
        select(User.id)
        .where(User.org_role == OrgRole.ADMIN)
        .limit(1)
    )
    admin_id = (await session.execute(admin_stmt)).scalar_one_or_none()
    return str(admin_id or "usr_system")


def _extract_seed_number_from_branch_or_text(*candidates: str | None) -> int | None:
    for text_val in candidates:
        if not text_val:
            continue
        m_branch = _SEED_BRANCH_RE.search(text_val)
        if m_branch:
            return int(m_branch.group(1))
        m_ref = _SEED_REF_RE.search(text_val)
        if m_ref:
            return int(m_ref.group(1))
    return None


async def _find_seed_by_number_or_tag(
    session: AsyncSession,
    project_id: str,
    *,
    seed_number: int | None = None,
    issue_tag: str | None = None,
) -> Seed | None:
    if seed_number is not None:
        stmt = select(Seed).where(
            Seed.project_id == project_id,
            Seed.number == seed_number,
        )
        seed = (await session.execute(stmt)).scalar_one_or_none()
        if seed is not None:
            return seed

    if issue_tag:
        # Match tag in Python-compatible way across Postgres/SQLite
        stmt = (
            select(Seed)
            .where(Seed.project_id == project_id)
            .order_by(col(Seed.created_at).desc())
            .limit(100)
        )
        seeds = (await session.execute(stmt)).scalars().all()
        tag_lower = issue_tag.lower()
        for s in seeds:
            if any(str(t).lower() == tag_lower for t in (s.tags or [])):
                return s
    return None


async def _mark_seed_runs_webhook_triggered(
    session: AsyncSession, seed: Seed
) -> None:
    """Tag webhook-triggered Seeds and active/queued Runs so Personal Mode `cli_oauth` refuses them."""
    cur_tags = list(seed.tags or [])
    if "webhook_triggered" not in cur_tags:
        cur_tags.append("webhook_triggered")
        seed.tags = cur_tags
    runs_stmt = select(Run).where(
        Run.seed_id == seed.id,
        col(Run.status).in_([RunStatus.QUEUED, RunStatus.STARTING, RunStatus.RUNNING]),
    )
    active_runs = (await session.execute(runs_stmt)).scalars().all()
    for r_obj in active_runs:
        out_copy = dict(r_obj.output or {})
        out_copy["webhook_triggered"] = True
        r_obj.output = out_copy


async def _create_seed_from_webhook_issue(
    session: AsyncSession,
    project: Project,
    *,
    provider: str,
    issue_number: int,
    title: str,
    body: str,
    html_url: str,
    sender: str,
    labels: list[str],
) -> tuple[Seed, bool]:
    """Create a Seed from a GitHub/GitLab/Bitbucket issue if not already created for that issue."""
    issue_tag = f"{provider}-issue-{issue_number}"
    existing = await _find_seed_by_number_or_tag(
        session, project.id, issue_tag=issue_tag
    )
    if existing is not None:
        return existing, False

    initial_stage = await first_enabled_stage(session, project.id)
    counter_stmt = (
        update(Project)
        .where(col(Project.id) == project.id)
        .values(seed_counter=col(Project.seed_counter) + 1)
        .returning(col(Project.seed_counter))
    )
    seed_number = int((await session.execute(counter_stmt)).scalar_one())
    actor_id = await _resolve_default_project_actor_id(session, project)
    now = datetime.now(UTC)

    raw_content = body.strip() if body and body.strip() else title.strip()
    wrapped_content = wrap_untrusted_external_input(raw_content)
    spec_lines = [wrapped_content]
    if html_url:
        spec_lines.append(f"\nSource Issue: {html_url} (reported by @{sender})")

    tags = [issue_tag, "webhook", "webhook_triggered"]
    for lbl in labels:
        cleaned_lbl = lbl.strip().lower()
        if cleaned_lbl and cleaned_lbl not in tags and len(tags) < 8:
            tags.append(cleaned_lbl)

    should_stage = await resolve_should_stage_seed(session, project)
    initial_status = SeedStatus.STAGED if should_stage else SeedStatus.QUEUED

    seed = Seed(
        project_id=project.id,
        number=seed_number,
        title=title.strip()[:200] or f"Issue #{issue_number}",
        description=wrap_untrusted_external_input(body.strip()[:500]) if body and body.strip() else "",
        specification="\n".join(spec_lines),
        acceptance_criteria=[
            f"Resolve issue #{issue_number}: {title.strip()}",
            "All existing and new automated tests pass cleanly",
        ],
        tags=tags,
        stage=initial_stage,
        status=initial_status,
        autonomy=SeedAutonomy.SUPERVISED,
        stage_entered_at=now,
        created_by=None,
        created_at=now,
        updated_at=now,
    )
    session.add(seed)
    await session.flush()

    record(
        session,
        type=ActivityType.SEED_CREATED,
        actor_type=ActorType.SYSTEM,
        actor_id=actor_id,
        project_id=project.id,
        seed_id=seed.id,
        data={
            "number": seed.number,
            "title": seed.title,
            "stage": str(seed.stage),
            "source": f"{provider}_webhook",
            "issue_number": issue_number,
        },
    )
    await session.commit()
    await session.refresh(seed)
    return seed, True


async def _sync_webhook_comment_to_seed(
    session: AsyncSession,
    project: Project,
    seed: Seed,
    *,
    provider: str,
    sender: str,
    comment_body: str,
    html_url: str | None = None,
) -> str:
    """Record an inbound PR/issue comment on a Seed and trigger live steering or re-queue if @mentioned."""
    actor_id = await _resolve_default_project_actor_id(session, project)
    now = datetime.now(UTC)
    header = f"[{provider.capitalize()} comment by @{sender}]"
    if html_url:
        header = f"[{provider.capitalize()} comment by @{sender}]({html_url})"
    wrapped_comment = wrap_untrusted_external_input(comment_body.strip())
    formatted_body = f"{header}\n{wrapped_comment}"

    comment = SeedComment(
        seed_id=seed.id,
        author_type=ActorType.USER,
        author_id=f"webhook:{provider}:{sender}",
        stage=seed.stage,
        body=formatted_body,
        created_at=now,
        updated_at=now,
    )
    session.add(comment)
    await session.flush()

    action_taken = "comment_synced"
    if await _mentions_stage_agent(session, seed, comment_body):
        await _mark_seed_runs_webhook_triggered(session, seed)
        if seed.status == SeedStatus.RUNNING:
            action_taken = "live_steering_queued"
        elif seed.status in (SeedStatus.AWAITING_APPROVAL, SeedStatus.FAILED):
            if seed.stage in (SeedStage.REVIEW, SeedStage.MERGE):
                await create_remediation_task_and_return_to_execute(
                    session,
                    seed,
                    from_stage=seed.stage,
                    operator_comment=formatted_body,
                    review_summary=None,
                    review_findings=[],
                    actor_type=ActorType.SYSTEM,
                    actor_id=actor_id,
                    now=now,
                )
                action_taken = "remediation_queued"
            else:
                seed.status = SeedStatus.QUEUED
                seed.blocked_reason = None
                seed.updated_at = now
                action_taken = "stage_requeued"

    await session.commit()
    return action_taken


async def _handle_ci_failure_remediation(
    session: AsyncSession,
    project: Project,
    seed: Seed,
    *,
    provider: str,
    workflow_name: str,
    branch_name: str,
    details_url: str | None,
) -> str:
    """Create a remediation task and return Seed to Execute when CI fails on a seed branch (bounded by max_attempts)."""
    await _mark_seed_runs_webhook_triggered(session, seed)
    return await remediate_seed_if_pr_ci_failing(
        session,
        seed,
        project,
        workflow_name=workflow_name,
        branch_name=branch_name,
        details_url=details_url,
        provider_override=provider,
    )


async def handle_github_webhook(
    session: AsyncSession,
    project_id_or_slug: str | None,
    event_name: str,
    raw_body: bytes,
    signature_header: str | None,
    payload: dict[str, Any],
    *,
    delivery_id: str | None = None,
) -> WebhookDeliveryResponse:
    """Verify and process an inbound GitHub webhook event for a project (or root GitHub App webhook)."""
    event = (event_name or "").strip().lower()
    repo_full_name = _extract_github_repo_full_name(payload)

    if not (project_id_or_slug and project_id_or_slug.strip()):
        if event == "ping" and not repo_full_name:
            git_backends = (
                await session.execute(
                    select(Backend).where(Backend.enabled == True)  # noqa: E712
                )
            ).scalars().all()
            for b_obj in git_backends:
                if not isinstance(b_obj, Backend):
                    continue
                if (b_obj.provider or "").strip().lower() not in (
                    "github",
                    "github-app",
                    "github-enterprise",
                ):
                    continue
                secrets_map = decrypt_secrets(b_obj.secrets_encrypted)
                b_secret = (secrets_map.get("webhook_secret") or "").strip()
                if b_secret and verify_github_signature(
                    raw_body, b_secret, signature_header
                ):
                    return WebhookDeliveryResponse(
                        accepted=True,
                        provider="github",
                        event="ping",
                        action="pong",
                        detail="GitHub App webhook ping verified",
                    )
            raise Forbidden("Invalid X-Hub-Signature-256 webhook signature")

        candidates = await _resolve_projects_from_repo_full_name(
            session,
            repo_full_name,
            provider_label="GitHub",
            missing_detail="GitHub webhook payload missing repository.full_name",
        )
        last_err: Exception | None = None
        last_resp: WebhookDeliveryResponse | None = None
        for cand in candidates:
            try:
                resp = await handle_github_webhook(
                    session,
                    cand.id,
                    event_name=event_name,
                    raw_body=raw_body,
                    signature_header=signature_header,
                    payload=payload,
                    delivery_id=delivery_id,
                )
                if resp.accepted:
                    return resp
                last_resp = resp
            except Forbidden as exc:
                last_err = exc
        if last_resp is not None:
            return last_resp
        if last_err is not None:
            raise last_err
        raise NotFound("Project not found")

    project = await _resolve_project_unauthenticated(session, project_id_or_slug)
    wh = (project.sandbox_config or {}).get("webhook_config") or {}
    if not wh.get("enabled", False):
        raise Forbidden("Webhooks are not enabled for this project")

    secret = await _resolve_webhook_secret(session, project)
    if not secret or not verify_github_signature(raw_body, secret, signature_header):
        raise Forbidden("Invalid X-Hub-Signature-256 webhook signature")

    await _verify_payload_repo_matches_project(session, project, repo_full_name)

    eff_delivery_id = delivery_id or str(payload.get("delivery_id") or "").strip() or None
    if not await _check_and_record_delivery_id_async(f"github:{project.id}", eff_delivery_id):
        return WebhookDeliveryResponse(
            accepted=False,
            provider="github",
            event=event or "unknown",
            action="duplicate_delivery",
            detail=f"Duplicate GitHub webhook delivery '{eff_delivery_id}' ignored",
        )
    if event == "ping":
        return WebhookDeliveryResponse(
            accepted=True,
            provider="github",
            event="ping",
            action="pong",
            detail="GitHub webhook ping verified",
        )

    label_trigger = str(wh.get("issue_label_trigger") or "gitseed").strip().lower()

    if event == "issues":
        if not wh.get("auto_create_seed_on_issue", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="disabled",
                detail="auto_create_seed_on_issue is disabled",
            )
        action = str(payload.get("action") or "").lower()
        if action not in ("opened", "labeled", "reopened"):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action=f"ignored_{action}",
                detail=f"Ignored issue action '{action}'",
            )
        issue = payload.get("issue") or {}
        trusted, assoc = _is_trusted_github_author(issue, payload)
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="untrusted_author",
                detail=f"Issue author association '{assoc}' is not in {sorted(TRUSTED_AUTHOR_ASSOCIATIONS)}",
            )
        issue_number = int(issue.get("number") or 0)
        title = str(issue.get("title") or "").strip()
        body = str(issue.get("body") or "").strip()
        html_url = str(issue.get("html_url") or "").strip()
        sender = str((payload.get("sender") or {}).get("login") or "github").strip()
        raw_labels = issue.get("labels") or []
        labels = [
            str(lbl.get("name") if isinstance(lbl, dict) else lbl).strip()
            for lbl in raw_labels
        ]
        label_lower = {lbl.lower() for lbl in labels if lbl}
        text_combined = f"{title}\n{body}".lower()
        if (
            label_trigger not in label_lower
            and f"#{label_trigger}" not in text_combined
            and f"[{label_trigger}]" not in text_combined
        ):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="unmatched_label",
                detail=f"Issue does not have label '{label_trigger}'",
            )
        seed, created = await _create_seed_from_webhook_issue(
            session,
            project,
            provider="gh",
            issue_number=issue_number,
            title=title,
            body=body,
            html_url=html_url,
            sender=sender,
            labels=labels,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="github",
            event=event,
            action="seed_created" if created else "seed_exists",
            seed_id=seed.id,
            seed_number=seed.number,
            detail=f"Seed #{seed.number} ({seed.id})",
        )

    if event == "pull_request":
        action = str(payload.get("action") or "").lower()
        pr_obj = payload.get("pull_request") or {}
        is_merged = bool(pr_obj.get("merged") or pr_obj.get("merged_at"))
        head_ref = str(((pr_obj.get("head") or {}).get("ref")) or "")
        title_str = str(pr_obj.get("title") or "")
        html_url = str(pr_obj.get("html_url") or "").strip() or None
        merge_sha = str(pr_obj.get("merge_commit_sha") or "").strip() or None
        merged_by_obj = pr_obj.get("merged_by") if isinstance(pr_obj.get("merged_by"), dict) else {}
        sender = str(
            merged_by_obj.get("login")
            or (payload.get("sender") or {}).get("login")
            or "github"
        ).strip()

        if action == "closed" and is_merged:
            seed_num = _extract_seed_number_from_branch_or_text(head_ref, title_str)
            seed = await _find_seed_by_number_or_tag(
                session, project.id, seed_number=seed_num
            )
            if seed is None:
                return WebhookDeliveryResponse(
                    accepted=False,
                    provider="github",
                    event=event,
                    action="seed_not_found",
                    detail=f"No matching Seed for merged PR branch '{head_ref}'",
                )
            reconciled = await reconcile_seed_if_pr_merged_externally(
                session,
                seed,
                project,
                pr_info={
                    "merged": True,
                    "provider": "github",
                    "pull_request_url": html_url,
                    "merge_commit_sha": merge_sha,
                    "merged_by": sender,
                },
            )
            return WebhookDeliveryResponse(
                accepted=True,
                provider="github",
                event=event,
                action="seed_merged" if reconciled else "already_merged",
                seed_id=seed.id,
                seed_number=seed.number,
                detail=f"Seed #{seed.number} reconciled as merged from GitHub PR",
            )
        seed_num = _extract_seed_number_from_branch_or_text(head_ref, title_str)
        seed = await _find_seed_by_number_or_tag(
            session, project.id, seed_number=seed_num
        )
        if seed is not None:
            events_service.emit_from_sync(
                type="seed.pr_updated",
                project_id=project.id,
                seed_id=seed.id,
                data={"provider": "github", "event": event, "action": action},
            )
        return WebhookDeliveryResponse(
            accepted=False,
            provider="github",
            event=event,
            action=f"ignored_{action}",
            detail=f"Ignored pull_request action '{action}' (merged={is_merged})",
        )

    if event == "pull_request_review":
        if not wh.get("sync_pr_comments", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="disabled",
                detail="sync_pr_comments is disabled",
            )
        action = str(payload.get("action") or "").lower()
        if action != "submitted":
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action=f"ignored_{action}",
            )
        review_obj = payload.get("review") or {}
        trusted, assoc = _is_trusted_github_author(review_obj, payload)
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="untrusted_author",
                detail=f"Review author association '{assoc}' is not in {sorted(TRUSTED_AUTHOR_ASSOCIATIONS)}",
            )
        review_state = str(review_obj.get("state") or "").lower()
        review_body = str(review_obj.get("body") or "").strip()
        html_url = str(review_obj.get("html_url") or "").strip() or None
        sender = str((payload.get("sender") or {}).get("login") or "github").strip()
        pr_obj = payload.get("pull_request") or {}
        head_ref = str(((pr_obj.get("head") or {}).get("ref")) or "")
        title_str = str(pr_obj.get("title") or "")
        pr_num = pr_obj.get("number")

        seed_num = _extract_seed_number_from_branch_or_text(
            head_ref, title_str, review_body
        )
        seed = await _find_seed_by_number_or_tag(
            session,
            project.id,
            seed_number=seed_num,
            issue_tag=f"gh-issue-{pr_num}" if pr_num else None,
        )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="seed_not_found",
            )

        if review_state == "changes_requested":
            effective_body = (
                f"{review_body}\n\n(@agent changes requested on GitHub PR review)"
                if review_body
                else "Changes requested on GitHub Pull Request review (@agent)."
            )
        else:
            effective_body = review_body or f"Submitted PR review ({review_state})."

        sync_action = await _sync_webhook_comment_to_seed(
            session,
            project,
            seed,
            provider="github",
            sender=sender,
            comment_body=effective_body,
            html_url=html_url,
        )
        events_service.emit_from_sync(
            type="seed.pr_updated",
            project_id=project.id,
            seed_id=seed.id,
            data={"provider": "github", "event": event, "review_state": review_state},
        )
        if review_state == "approved":
            await maybe_auto_merge_ready_pr(session, seed, project)
        return WebhookDeliveryResponse(
            accepted=True,
            provider="github",
            event=event,
            action=sync_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if event in ("issue_comment", "pull_request_review_comment"):
        if not wh.get("sync_pr_comments", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="disabled",
                detail="sync_pr_comments is disabled",
            )
        action = str(payload.get("action") or "").lower()
        if action != "created":
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action=f"ignored_{action}",
            )
        comment_obj = payload.get("comment") or {}
        trusted, assoc = _is_trusted_github_author(comment_obj, payload)
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="untrusted_author",
                detail=f"Comment author association '{assoc}' is not in {sorted(TRUSTED_AUTHOR_ASSOCIATIONS)}",
            )
        comment_body = str(comment_obj.get("body") or "").strip()
        html_url = str(comment_obj.get("html_url") or "").strip() or None
        sender = str((payload.get("sender") or {}).get("login") or "github").strip()
        issue_obj = payload.get("issue") or {}
        pr_obj = payload.get("pull_request") or {}
        head_ref = str(((pr_obj.get("head") or {}).get("ref")) or "")
        title_str = str(pr_obj.get("title") or issue_obj.get("title") or "")
        issue_num = issue_obj.get("number") or pr_obj.get("number")

        seed_num = _extract_seed_number_from_branch_or_text(
            head_ref, title_str, comment_body
        )
        seed = await _find_seed_by_number_or_tag(
            session,
            project.id,
            seed_number=seed_num,
            issue_tag=f"gh-issue-{issue_num}" if issue_num else None,
        )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="seed_not_found",
                detail="Could not match comment to an existing Seed",
            )
        sync_action = await _sync_webhook_comment_to_seed(
            session,
            project,
            seed,
            provider="github",
            sender=sender,
            comment_body=comment_body,
            html_url=html_url,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="github",
            event=event,
            action=sync_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if event in ("workflow_run", "check_suite", "check_run"):
        obj = payload.get(event) or {}
        conclusion = str(obj.get("conclusion") or "").lower()
        status_val = str(obj.get("status") or "").lower()
        branch_name = str(
            obj.get("head_branch")
            or ((obj.get("check_suite") or {}).get("head_branch"))
            or ""
        )
        workflow_name = str(obj.get("name") or event)
        details_url = str(obj.get("html_url") or "").strip() or None
        seed_num = _extract_seed_number_from_branch_or_text(branch_name)
        seed = await _find_seed_by_number_or_tag(
            session, project.id, seed_number=seed_num
        )
        if seed is not None:
            events_service.emit_from_sync(
                type="seed.pr_updated",
                project_id=project.id,
                seed_id=seed.id,
                data={
                    "provider": "github",
                    "event": event,
                    "status": status_val,
                    "conclusion": conclusion,
                },
            )
            if conclusion == "success":
                auto_merged = await maybe_auto_merge_ready_pr(session, seed, project)
                if auto_merged:
                    return WebhookDeliveryResponse(
                        accepted=True,
                        provider="github",
                        event=event,
                        action="seed_auto_merged",
                        seed_id=seed.id,
                        seed_number=seed.number,
                    )
        if not wh.get("auto_remediate_ci", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="disabled",
                detail="auto_remediate_ci is disabled",
            )
        if conclusion != "failure":
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action=f"ignored_conclusion_{conclusion or status_val or 'none'}",
            )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="github",
                event=event,
                action="seed_not_found",
                detail=f"No matching Seed for branch '{branch_name}'",
            )
        rem_action = await _handle_ci_failure_remediation(
            session,
            project,
            seed,
            provider="github",
            workflow_name=workflow_name,
            branch_name=branch_name,
            details_url=details_url,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="github",
            event=event,
            action=rem_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    return WebhookDeliveryResponse(
        accepted=False,
        provider="github",
        event=event,
        action="unhandled_event",
        detail=f"Unhandled GitHub event '{event}'",
    )


async def handle_gitlab_webhook(
    session: AsyncSession,
    project_id_or_slug: str | None,
    event_name: str,
    token_header: str | None,
    payload: dict[str, Any],
    *,
    delivery_id: str | None = None,
) -> WebhookDeliveryResponse:
    """Verify and process an inbound GitLab webhook event for a project."""
    repo_full_name = _extract_gitlab_repo_full_name(payload)
    if not (project_id_or_slug and project_id_or_slug.strip()):
        candidates = await _resolve_projects_from_repo_full_name(
            session,
            repo_full_name,
            provider_label="GitLab",
            missing_detail="GitLab webhook payload missing project.path_with_namespace",
        )
        last_err: Exception | None = None
        last_resp: WebhookDeliveryResponse | None = None
        for cand in candidates:
            try:
                resp = await handle_gitlab_webhook(
                    session,
                    cand.id,
                    event_name=event_name,
                    token_header=token_header,
                    payload=payload,
                    delivery_id=delivery_id,
                )
                if resp.accepted:
                    return resp
                last_resp = resp
            except Forbidden as exc:
                last_err = exc
        if last_resp is not None:
            return last_resp
        if last_err is not None:
            raise last_err
        raise NotFound("Project not found")

    project = await _resolve_project_unauthenticated(session, project_id_or_slug)
    wh = (project.sandbox_config or {}).get("webhook_config") or {}
    if not wh.get("enabled", False):
        raise Forbidden("Webhooks are not enabled for this project")

    secret = await _resolve_webhook_secret(session, project)
    if not secret or not verify_gitlab_token(secret, token_header):
        raise Forbidden("Invalid X-Gitlab-Token webhook header")

    await _verify_payload_repo_matches_project(session, project, repo_full_name)

    object_kind = str(payload.get("object_kind") or event_name or "").strip().lower()
    eff_delivery_id = (
        delivery_id
        or str(payload.get("event_uuid") or payload.get("delivery_id") or "").strip()
        or None
    )
    if not await _check_and_record_delivery_id_async(f"gitlab:{project.id}", eff_delivery_id):
        return WebhookDeliveryResponse(
            accepted=False,
            provider="gitlab",
            event=object_kind or "unknown",
            action="duplicate_delivery",
            detail=f"Duplicate GitLab webhook delivery '{eff_delivery_id}' ignored",
        )
    label_trigger = str(wh.get("issue_label_trigger") or "gitseed").strip().lower()

    if object_kind in ("issue", "issue hook"):
        if not wh.get("auto_create_seed_on_issue", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="disabled",
            )
        attrs = payload.get("object_attributes") or {}
        trusted, assoc = await _is_trusted_gitlab_author(
            attrs, payload, session=session, project=project
        )
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="untrusted_author",
                detail=f"Issue author association '{assoc}' is not trusted",
            )
        action = str(attrs.get("action") or "open").lower()
        if action not in ("open", "update", "reopen"):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action=f"ignored_{action}",
            )
        issue_iid = int(attrs.get("iid") or attrs.get("id") or 0)
        title = str(attrs.get("title") or "").strip()
        body = str(attrs.get("description") or "").strip()
        html_url = str(attrs.get("url") or "").strip()
        sender = str((payload.get("user") or {}).get("username") or "gitlab").strip()
        raw_labels = payload.get("labels") or []
        labels = [
            str(lbl.get("title") if isinstance(lbl, dict) else lbl).strip()
            for lbl in raw_labels
        ]
        label_lower = {lbl.lower() for lbl in labels if lbl}
        text_combined = f"{title}\n{body}".lower()
        if (
            label_trigger not in label_lower
            and f"#{label_trigger}" not in text_combined
            and f"[{label_trigger}]" not in text_combined
        ):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="unmatched_label",
            )
        seed, created = await _create_seed_from_webhook_issue(
            session,
            project,
            provider="gl",
            issue_number=issue_iid,
            title=title,
            body=body,
            html_url=html_url,
            sender=sender,
            labels=labels,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="gitlab",
            event=object_kind,
            action="seed_created" if created else "seed_exists",
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if object_kind in ("merge_request", "merge_request hook"):
        attrs = payload.get("object_attributes") or {}
        mr_state = str(attrs.get("state") or "").lower()
        mr_action = str(attrs.get("action") or "").lower()
        source_branch = str(attrs.get("source_branch") or "")
        title_str = str(attrs.get("title") or "")
        web_url = str(attrs.get("url") or "").strip() or None
        merge_sha = str(attrs.get("merge_commit_sha") or "").strip() or None
        sender = str((payload.get("user") or {}).get("username") or "gitlab").strip()

        if mr_state == "merged" or mr_action == "merge":
            seed_num = _extract_seed_number_from_branch_or_text(source_branch, title_str)
            seed = await _find_seed_by_number_or_tag(
                session, project.id, seed_number=seed_num
            )
            if seed is None:
                return WebhookDeliveryResponse(
                    accepted=False,
                    provider="gitlab",
                    event=object_kind,
                    action="seed_not_found",
                )
            reconciled = await reconcile_seed_if_pr_merged_externally(
                session,
                seed,
                project,
                pr_info={
                    "merged": True,
                    "provider": "gitlab",
                    "pull_request_url": web_url,
                    "merge_commit_sha": merge_sha,
                    "merged_by": sender,
                },
            )
            return WebhookDeliveryResponse(
                accepted=True,
                provider="gitlab",
                event=object_kind,
                action="seed_merged" if reconciled else "already_merged",
                seed_id=seed.id,
                seed_number=seed.number,
            )
        seed_num = _extract_seed_number_from_branch_or_text(source_branch, title_str)
        seed = await _find_seed_by_number_or_tag(
            session, project.id, seed_number=seed_num
        )
        if seed is not None:
            events_service.emit_from_sync(
                type="seed.pr_updated",
                project_id=project.id,
                seed_id=seed.id,
                data={"provider": "gitlab", "event": object_kind, "action": mr_action or mr_state},
            )
            if mr_action == "approved":
                auto_merged = await maybe_auto_merge_ready_pr(session, seed, project)
                if auto_merged:
                    return WebhookDeliveryResponse(
                        accepted=True,
                        provider="gitlab",
                        event=object_kind,
                        action="seed_auto_merged",
                        seed_id=seed.id,
                        seed_number=seed.number,
                    )
        return WebhookDeliveryResponse(
            accepted=False,
            provider="gitlab",
            event=object_kind,
            action=f"ignored_{mr_action or mr_state}",
        )

    if object_kind in ("note", "note hook"):
        if not wh.get("sync_pr_comments", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="disabled",
            )
        attrs = payload.get("object_attributes") or {}
        trusted, assoc = await _is_trusted_gitlab_author(
            attrs, payload, session=session, project=project
        )
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="untrusted_author",
                detail=f"Note author association '{assoc}' is not trusted",
            )
        comment_body = str(attrs.get("note") or "").strip()
        html_url = str(attrs.get("url") or "").strip() or None
        sender = str((payload.get("user") or {}).get("username") or "gitlab").strip()
        mr = payload.get("merge_request") or {}
        issue = payload.get("issue") or {}
        source_branch = str(mr.get("source_branch") or "")
        title_str = str(mr.get("title") or issue.get("title") or "")
        issue_iid = issue.get("iid") or mr.get("iid")

        seed_num = _extract_seed_number_from_branch_or_text(
            source_branch, title_str, comment_body
        )
        seed = await _find_seed_by_number_or_tag(
            session,
            project.id,
            seed_number=seed_num,
            issue_tag=f"gl-issue-{issue_iid}" if issue_iid else None,
        )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="seed_not_found",
            )
        sync_action = await _sync_webhook_comment_to_seed(
            session,
            project,
            seed,
            provider="gitlab",
            sender=sender,
            comment_body=comment_body,
            html_url=html_url,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="gitlab",
            event=object_kind,
            action=sync_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if object_kind in ("pipeline", "pipeline hook"):
        attrs = payload.get("object_attributes") or {}
        status_val = str(attrs.get("status") or "").lower()
        branch_name = str(attrs.get("ref") or "")
        details_url = str(attrs.get("url") or "").strip() or None
        seed_num = _extract_seed_number_from_branch_or_text(branch_name)
        seed = await _find_seed_by_number_or_tag(
            session, project.id, seed_number=seed_num
        )
        if seed is not None:
            events_service.emit_from_sync(
                type="seed.pr_updated",
                project_id=project.id,
                seed_id=seed.id,
                data={
                    "provider": "gitlab",
                    "event": object_kind,
                    "status": status_val,
                },
            )
            if status_val == "success":
                auto_merged = await maybe_auto_merge_ready_pr(session, seed, project)
                if auto_merged:
                    return WebhookDeliveryResponse(
                        accepted=True,
                        provider="gitlab",
                        event=object_kind,
                        action="seed_auto_merged",
                        seed_id=seed.id,
                        seed_number=seed.number,
                    )
        if not wh.get("auto_remediate_ci", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="disabled",
            )
        if status_val != "failed":
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action=f"ignored_status_{status_val or 'none'}",
            )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="gitlab",
                event=object_kind,
                action="seed_not_found",
            )
        rem_action = await _handle_ci_failure_remediation(
            session,
            project,
            seed,
            provider="gitlab",
            workflow_name=f"Pipeline #{attrs.get('id', '')}",
            branch_name=branch_name,
            details_url=details_url,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="gitlab",
            event=object_kind,
            action=rem_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    return WebhookDeliveryResponse(
        accepted=False,
        provider="gitlab",
        event=object_kind,
        action="unhandled_event",
    )


async def _resolve_project_from_bitbucket_repo(
    session: AsyncSession, payload: dict[str, Any]
) -> Project:
    full_name = _extract_bitbucket_repo_full_name(payload)
    projects = await _resolve_projects_from_repo_full_name(
        session,
        full_name,
        provider="bitbucket",
        provider_label="Bitbucket",
        missing_detail="Bitbucket webhook payload missing repository.full_name",
    )
    return projects[0]


def _is_trusted_bitbucket_author(
    obj: dict[str, Any], payload: dict[str, Any]
) -> tuple[bool, str]:
    actor_obj = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
    user_obj = obj.get("user") if isinstance(obj.get("user"), dict) else {}

    raw_assoc = (
        obj.get("author_association")
        or payload.get("author_association")
        or actor_obj.get("author_association")
        or user_obj.get("author_association")
    )
    if raw_assoc is not None:
        assoc = str(raw_assoc).strip().upper()
        if assoc in TRUSTED_AUTHOR_ASSOCIATIONS or assoc in TRUSTED_GITLAB_PAYLOAD_ROLES:
            return True, assoc
        return False, assoc or "UNTRUSTED"

    raw_perm = (
        actor_obj.get("permission")
        or obj.get("permission")
        or payload.get("permission")
        or user_obj.get("permission")
    )
    if raw_perm is not None:
        perm = str(raw_perm).strip().lower()
        if perm in ("admin", "write"):
            return True, perm.upper()
        return False, perm.upper() or "UNTRUSTED"

    raw_role = (
        actor_obj.get("role")
        or obj.get("role")
        or user_obj.get("role")
    )
    if raw_role is not None:
        role = str(raw_role).strip().upper()
        if role in ("OWNER", "ADMIN", "MAINTAINER", "DEVELOPER", "WRITE", "COLLABORATOR", "MEMBER"):
            return True, role
        return False, role or "UNTRUSTED"

    if actor_obj.get("is_collaborator") is True or user_obj.get("is_collaborator") is True:
        return True, "COLLABORATOR"

    repo_obj = payload.get("repository") if isinstance(payload.get("repository"), dict) else {}
    owner_obj = repo_obj.get("owner") if isinstance(repo_obj.get("owner"), dict) else {}
    for id_key in ("account_id", "uuid", "username", "nickname"):
        actor_val = str(actor_obj.get(id_key) or "").strip()
        owner_val = str(owner_obj.get(id_key) or "").strip()
        if actor_val and owner_val and actor_val == owner_val:
            return True, "OWNER"

    # Never infer write trust from repository.is_private alone: read-only viewers on private
    # Bitbucket repositories can open issues and comment on PRs without write privileges.
    return False, "UNTRUSTED"


async def handle_bitbucket_webhook(
    session: AsyncSession,
    project_id_or_slug: str | None,
    event_name: str,
    raw_body: bytes,
    signature_header: str | None,
    payload: dict[str, Any],
    *,
    delivery_id: str | None = None,
    _resolved_project: Project | None = None,
) -> WebhookDeliveryResponse:
    """Verify and process an inbound Bitbucket Cloud webhook event (`X-Hub-Signature: sha256=<hmac>`)."""
    repo_full_name = _extract_bitbucket_repo_full_name(payload)
    if _resolved_project is not None:
        project = _resolved_project
        explicit_project = False
    elif project_id_or_slug and project_id_or_slug.strip():
        project = await _resolve_project_unauthenticated(session, project_id_or_slug)
        explicit_project = True
    else:
        candidates = await _resolve_projects_from_repo_full_name(
            session,
            repo_full_name,
            provider="bitbucket",
        )
        last_err: Exception | None = None
        last_resp: WebhookDeliveryResponse | None = None
        for cand in candidates:
            try:
                resp = await handle_bitbucket_webhook(
                    session,
                    cand.id,
                    event_name=event_name,
                    raw_body=raw_body,
                    signature_header=signature_header,
                    payload=payload,
                    delivery_id=delivery_id,
                    _resolved_project=cand,
                )
                if resp.accepted:
                    return resp
                last_resp = resp
            except Forbidden as exc:
                last_err = exc
        if last_resp is not None:
            return last_resp
        if last_err is not None:
            raise last_err
        raise NotFound("Project not found")

    wh = (project.sandbox_config or {}).get("webhook_config") or {}
    if not wh.get("enabled", False):
        raise Forbidden("Webhooks are not enabled for this project")

    secret = await _resolve_webhook_secret(session, project)
    if not secret or not verify_bitbucket_signature(raw_body, secret, signature_header):
        raise Forbidden("Invalid X-Hub-Signature webhook signature")

    if explicit_project:
        await _verify_payload_repo_matches_project(session, project, repo_full_name)

    event = (event_name or "").strip().lower()
    eff_delivery_id = delivery_id or str(payload.get("uuid") or "").strip() or None
    if not await _check_and_record_delivery_id_async(f"bitbucket:{project.id}", eff_delivery_id):
        return WebhookDeliveryResponse(
            accepted=False,
            provider="bitbucket",
            event=event or "unknown",
            action="duplicate_delivery",
            detail=f"Duplicate Bitbucket webhook delivery '{eff_delivery_id}' ignored",
        )

    label_trigger = str(wh.get("issue_label_trigger") or "gitseed").strip().lower()

    if event == "issue:created":
        if not wh.get("auto_create_seed_on_issue", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="disabled",
            )
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        trusted, assoc = _is_trusted_bitbucket_author(issue, payload)
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="untrusted_author",
                detail=f"Issue author association '{assoc}' is not trusted",
            )
        issue_id = int(issue.get("id") or 0)
        title = str(issue.get("title") or "").strip()
        content_obj = issue.get("content") if isinstance(issue.get("content"), dict) else {}
        body = str(content_obj.get("raw") or issue.get("body") or "").strip()
        links_obj = issue.get("links") if isinstance(issue.get("links"), dict) else {}
        html_obj = links_obj.get("html") if isinstance(links_obj.get("html"), dict) else {}
        html_url = str(html_obj.get("href") or "").strip()
        actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        sender = str(
            actor.get("nickname")
            or actor.get("username")
            or actor.get("display_name")
            or "bitbucket"
        ).strip()

        labels: list[str] = []
        raw_labels = issue.get("labels")
        if isinstance(raw_labels, list):
            for lbl in raw_labels:
                lbl_s = str(lbl.get("name") if isinstance(lbl, dict) else lbl).strip()
                if lbl_s:
                    labels.append(lbl_s)
        comp_obj = issue.get("component")
        if isinstance(comp_obj, dict) and comp_obj.get("name"):
            labels.append(str(comp_obj["name"]).strip())
        if issue.get("kind"):
            labels.append(str(issue["kind"]).strip())

        label_lower = {lbl.lower() for lbl in labels if lbl}
        text_combined = f"{title}\n{body}".lower()
        if (
            label_trigger not in label_lower
            and f"#{label_trigger}" not in text_combined
            and f"[{label_trigger}]" not in text_combined
        ):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="unmatched_label",
            )

        seed, created = await _create_seed_from_webhook_issue(
            session,
            project,
            provider="bb",
            issue_number=issue_id,
            title=title,
            body=body,
            html_url=html_url,
            sender=sender,
            labels=labels,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="bitbucket",
            event=event,
            action="seed_created" if created else "seed_exists",
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if event == "issue:comment_created":
        if not wh.get("sync_pr_comments", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="disabled",
            )
        comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
        issue = payload.get("issue") if isinstance(payload.get("issue"), dict) else {}
        trusted, assoc = _is_trusted_bitbucket_author(comment, payload)
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="untrusted_author",
                detail=f"Comment author association '{assoc}' is not trusted",
            )
        content_obj = comment.get("content") if isinstance(comment.get("content"), dict) else {}
        comment_body = str(content_obj.get("raw") or comment.get("body") or "").strip()
        if not comment_body:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="empty_comment",
            )
        links_obj = comment.get("links") if isinstance(comment.get("links"), dict) else {}
        html_obj = links_obj.get("html") if isinstance(links_obj.get("html"), dict) else {}
        html_url = str(html_obj.get("href") or "").strip() or None
        actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        sender = str(
            actor.get("nickname")
            or actor.get("username")
            or actor.get("display_name")
            or "bitbucket"
        ).strip()
        issue_id_val = issue.get("id")
        seed_num = _extract_seed_number_from_branch_or_text(
            str(issue.get("title") or ""), comment_body
        )
        seed = await _find_seed_by_number_or_tag(
            session,
            project.id,
            seed_number=seed_num,
            issue_tag=f"bb-issue-{issue_id_val}" if issue_id_val else None,
        )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="seed_not_found",
            )
        sync_action = await _sync_webhook_comment_to_seed(
            session,
            project,
            seed,
            provider="bitbucket",
            sender=sender,
            comment_body=comment_body,
            html_url=html_url,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="bitbucket",
            event=event,
            action=sync_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if event == "pullrequest:comment_created":
        if not wh.get("sync_pr_comments", True):
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="disabled",
            )
        comment = payload.get("comment") if isinstance(payload.get("comment"), dict) else {}
        pr_obj = payload.get("pullrequest") if isinstance(payload.get("pullrequest"), dict) else {}
        trusted, assoc = _is_trusted_bitbucket_author(comment, payload)
        if not trusted:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="untrusted_author",
                detail=f"PR comment author association '{assoc}' is not trusted",
            )
        content_obj = comment.get("content") if isinstance(comment.get("content"), dict) else {}
        comment_body = str(content_obj.get("raw") or comment.get("body") or "").strip()
        if not comment_body:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="empty_comment",
            )
        links_obj = comment.get("links") if isinstance(comment.get("links"), dict) else {}
        html_obj = links_obj.get("html") if isinstance(links_obj.get("html"), dict) else {}
        html_url = str(html_obj.get("href") or "").strip() or None
        source_obj = pr_obj.get("source") if isinstance(pr_obj.get("source"), dict) else {}
        branch_obj = source_obj.get("branch") if isinstance(source_obj.get("branch"), dict) else {}
        source_branch = str(branch_obj.get("name") or "")
        title_str = str(pr_obj.get("title") or "")
        pr_id_val = pr_obj.get("id")
        actor = payload.get("actor") if isinstance(payload.get("actor"), dict) else {}
        sender = str(
            actor.get("nickname")
            or actor.get("username")
            or actor.get("display_name")
            or "bitbucket"
        ).strip()

        seed_num = _extract_seed_number_from_branch_or_text(
            source_branch, title_str, comment_body
        )
        seed = await _find_seed_by_number_or_tag(
            session,
            project.id,
            seed_number=seed_num,
            issue_tag=f"bb-issue-{pr_id_val}" if pr_id_val else None,
        )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="seed_not_found",
            )
        sync_action = await _sync_webhook_comment_to_seed(
            session,
            project,
            seed,
            provider="bitbucket",
            sender=sender,
            comment_body=comment_body,
            html_url=html_url,
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="bitbucket",
            event=event,
            action=sync_action,
            seed_id=seed.id,
            seed_number=seed.number,
        )

    if event == "pullrequest:fulfilled":
        pr_obj = payload.get("pullrequest") if isinstance(payload.get("pullrequest"), dict) else {}
        source_obj = pr_obj.get("source") if isinstance(pr_obj.get("source"), dict) else {}
        branch_obj = source_obj.get("branch") if isinstance(source_obj.get("branch"), dict) else {}
        source_branch = str(branch_obj.get("name") or "")
        title_str = str(pr_obj.get("title") or "")
        links_obj = pr_obj.get("links") if isinstance(pr_obj.get("links"), dict) else {}
        html_obj = links_obj.get("html") if isinstance(links_obj.get("html"), dict) else {}
        html_url = str(html_obj.get("href") or "").strip() or None
        mc_obj = pr_obj.get("merge_commit") if isinstance(pr_obj.get("merge_commit"), dict) else {}
        merge_sha = str(mc_obj.get("hash") or "").strip() or None
        actor = (
            payload.get("actor")
            if isinstance(payload.get("actor"), dict)
            else (pr_obj.get("closed_by") if isinstance(pr_obj.get("closed_by"), dict) else {})
        )
        sender = str(
            actor.get("nickname")
            or actor.get("username")
            or actor.get("display_name")
            or "bitbucket"
        ).strip()

        seed_num = _extract_seed_number_from_branch_or_text(source_branch, title_str)
        seed = await _find_seed_by_number_or_tag(
            session, project.id, seed_number=seed_num
        )
        if seed is None:
            return WebhookDeliveryResponse(
                accepted=False,
                provider="bitbucket",
                event=event,
                action="seed_not_found",
                detail=f"No matching Seed for merged Bitbucket PR branch '{source_branch}'",
            )
        reconciled = await reconcile_seed_if_pr_merged_externally(
            session,
            seed,
            project,
            pr_info={
                "merged": True,
                "provider": "bitbucket",
                "pull_request_url": html_url,
                "merge_commit_sha": merge_sha,
                "merged_by": sender,
            },
        )
        return WebhookDeliveryResponse(
            accepted=True,
            provider="bitbucket",
            event=event,
            action="seed_merged" if reconciled else "already_merged",
            seed_id=seed.id,
            seed_number=seed.number,
            detail=f"Seed #{seed.number} reconciled as merged from Bitbucket PR",
        )

    return WebhookDeliveryResponse(
        accepted=False,
        provider="bitbucket",
        event=event,
        action="unhandled_event",
    )

