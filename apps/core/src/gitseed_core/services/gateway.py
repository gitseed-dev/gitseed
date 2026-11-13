"""LiteLLM Proxy synchronization and per-run virtual key lifecycle service.

GitSeed remains the single source of truth for Inference backends and budgets:
- When an Inference backend is created, updated, or discovered (and again on Core startup),
  its enabled models are pushed to LiteLLM via `/model/new` with the `bnd_` ID recorded
  in `model_info.metadata`.
- Only Core holds `LITELLM_MASTER_KEY`. Each Run receives a scoped virtual key (`sk-gsr-...`)
  created via `/key/generate` with `max_budget` set to the smallest of the seed's remaining
  budget, the project's remaining monthly budget, and the next spend checkpoint.
- When a run ends (or is cancelled/timed out), Core reads final spend from `/key/info`,
  writes `run.cost`, atomically increments `seed.spent`, and deletes the virtual key.
"""

from __future__ import annotations

import hashlib
import hmac
import inspect
import json
import logging
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Literal

import httpx
from gitseed_common.enums import (
    ActivityType,
    ActorType,
    BackendType,
    SeedStatus,
)
from sqlalchemy import func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.agent import AgentBackend
from gitseed_core.models.agent_session import AgentSession
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.models.settings import OrgSettings
from gitseed_core.security import decrypt_secrets
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Forbidden, Invalid

logger = logging.getLogger("uvicorn.error")


def docker_host_fallback_url(url: str) -> str:
    """Replace localhost / 127.0.0.1 with host.docker.internal for container-to-host calls."""
    for local_host in (
        "://localhost:",
        "://127.0.0.1:",
        "://localhost/",
        "://127.0.0.1/",
    ):
        if local_host in url:
            return url.replace(
                local_host,
                local_host.replace("localhost", "host.docker.internal").replace(
                    "127.0.0.1", "host.docker.internal"
                ),
                1,
            )
    return url


def _litellm_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {settings.LITELLM_MASTER_KEY}",
        "Content-Type": "application/json",
    }


def extract_enabled_model_ids(backend: Backend) -> list[str]:
    cfg = backend.config or {}
    models = cfg.get("models")
    result: list[str] = []
    if isinstance(models, list):
        for item in models:
            if isinstance(item, dict) and item.get("enabled", True):
                mid = str(item.get("id", "")).strip()
                if mid:
                    result.append(mid)
    if not result:
        disc = cfg.get("discovered_models")
        if isinstance(disc, list):
            for item in disc:
                if isinstance(item, str) and item.strip():
                    result.append(item.strip())
    return result


def resolve_model_context_limit(
    model: str | None,
    backend: Backend | None = None,
    runtime: str | None = None,
) -> int | None:
    """Resolve the maximum context window tokens for a known model or explicit backend config.

    Returns `None` when the model's context limit is not explicitly configured or known,
    so callers never fabricate or guess a fake capacity.
    """
    cleaned = (model or "").strip().lower()
    if backend is not None and isinstance(backend.config, dict):
        for list_key in ("models", "discovered_models"):
            cfg_models = backend.config.get(list_key)
            if isinstance(cfg_models, list):
                for item in cfg_models:
                    if not isinstance(item, dict):
                        continue
                    item_id = str(item.get("id") or "").strip().lower()
                    if item_id and item_id == cleaned:
                        for key in (
                            "context_window",
                            "context_length",
                            "max_context_tokens",
                            "max_input_tokens",
                        ):
                            val = item.get(key)
                            if isinstance(val, int) and val > 0:
                                return val
        default_ctx = (
            backend.config.get("context_window")
            or backend.config.get("context_length")
            or backend.config.get("max_context_tokens")
        )
        if isinstance(default_ctx, int) and default_ctx > 0:
            return default_ctx

    if not cleaned or cleaned == "default":
        return None

    if any(tag in cleaned for tag in ("1m", "gemini-2.5", "gemini-2.0", "gemini-1.5", "gemini", "gpt-4.1")):
        return 1_000_000
    if "gpt-5" in cleaned:
        return 400_000
    if "grok-4" in cleaned:
        return 256_000
    if any(tag in cleaned for tag in ("claude", "sonnet", "opus", "haiku", "o3", "o4-mini", "codex")):
        return 200_000
    if any(tag in cleaned for tag in ("grok-3", "grok-2", "gpt-4o", "gpt-4-turbo", "deepseek")):
        return 128_000

    return None


def build_context_window_info(
    *,
    used_tokens: int,
    peak_tokens: int = 0,
    max_tokens: int | None = None,
    turns: int = 0,
) -> dict[str, Any] | None:
    """Build normalized `context_window` telemetry dictionary from real token measurements.

    Returns `None` if either real token usage or model context capacity is unavailable.
    """
    if max_tokens is None or int(max_tokens) <= 0:
        return None
    limit = int(max_tokens)
    used = max(0, int(used_tokens or 0))
    peak = max(used, int(peak_tokens or 0))
    if used <= 0 and peak <= 0:
        return None
    pct = round(min(100.0, (used / limit) * 100.0), 1)
    return {
        "used_tokens": used,
        "peak_tokens": peak,
        "max_tokens": limit,
        "utilization_pct": pct,
        "turns": max(0, int(turns or 0)),
    }


def merge_run_context_window(
    existing_output: dict[str, Any] | None,
    *,
    used_tokens: int,
    peak_tokens: int = 0,
    turns: int = 0,
    model: str | None = None,
    runtime: str | None = None,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Merge real context window telemetry into a Run's `output` dict without losing prior peak data."""
    out = dict(existing_output or {})
    prev = out.get("context_window") if isinstance(out.get("context_window"), dict) else {}
    prev_used = int(prev.get("used_tokens") or 0)
    prev_peak = int(prev.get("peak_tokens") or 0)
    prev_turns = int(prev.get("turns") or 0)
    prev_max = int(prev.get("max_tokens") or 0)

    max_tokens = prev_max if prev_max > 0 else resolve_model_context_limit(model, backend, runtime)
    resolved_used = int(used_tokens) if int(used_tokens or 0) > 0 else prev_used
    resolved_peak = max(prev_peak, resolved_used, int(peak_tokens or 0))
    resolved_turns = max(prev_turns, int(turns or 0))

    info = build_context_window_info(
        used_tokens=resolved_used,
        peak_tokens=resolved_peak,
        max_tokens=max_tokens,
        turns=resolved_turns,
    )
    if info is not None:
        out["context_window"] = info
    return out


def _upstream_litellm_params(
    backend: Backend,
    secrets_map: dict[str, str],
    model_id: str,
) -> dict[str, Any]:
    """Build `litellm_params` for `/model/new` for a specific backend and model."""
    provider = backend.provider.lower()
    raw_endpoint = (backend.endpoint or "").strip().rstrip("/")
    endpoint = docker_host_fallback_url(raw_endpoint) if raw_endpoint else ""
    api_key = secrets_map.get("api_key", "").strip()

    cleaned_id = model_id.strip()
    if provider == "anthropic":
        litellm_model = (
            cleaned_id
            if cleaned_id.startswith("anthropic/")
            else f"anthropic/{cleaned_id}"
        )
        api_base = endpoint or None
    elif provider in ("gemini", "antigravity"):
        base_id = cleaned_id.removeprefix("gemini/").removeprefix("models/")
        litellm_model = f"gemini/{base_id}"
        api_base = None
    elif provider == "xai":
        base_id = cleaned_id.removeprefix("xai/").removeprefix("openai/")
        litellm_model = f"openai/{base_id}"
        base_ep = endpoint or "https://api.x.ai/v1"
        api_base = base_ep if base_ep.endswith("/v1") else f"{base_ep}/v1"
    elif provider == "ollama":
        base_id = cleaned_id.removeprefix("ollama/").removeprefix("ollama_chat/")
        litellm_model = f"ollama_chat/{base_id}"
        api_base = (endpoint or "http://host.docker.internal:11434").removesuffix("/v1")
    elif provider == "lmstudio":
        base_id = cleaned_id.removeprefix("lm_studio/").removeprefix("openai/")
        litellm_model = f"openai/{base_id}"
        base_ep = endpoint or "http://host.docker.internal:1234/v1"
        api_base = base_ep if base_ep.endswith("/v1") else f"{base_ep}/v1"
        api_key = api_key or "lm-studio"
    elif provider == "vllm":
        base_id = cleaned_id.removeprefix("hosted_vllm/").removeprefix("openai/")
        litellm_model = f"hosted_vllm/{base_id}"
        base_ep = endpoint or "http://host.docker.internal:8000/v1"
        api_base = base_ep if base_ep.endswith("/v1") else f"{base_ep}/v1"
        api_key = api_key or "vllm"
    else:
        base_id = cleaned_id.removeprefix("openai/")
        litellm_model = f"openai/{base_id}"
        if endpoint:
            api_base = endpoint if endpoint.endswith("/v1") else f"{endpoint}/v1"
        else:
            api_base = None
        if provider in ("custom", "litellm") and not api_key:
            api_key = "custom-key"

    params: dict[str, Any] = {"model": litellm_model}
    if api_base:
        params["api_base"] = api_base
    if api_key:
        params["api_key"] = api_key

    cfg = backend.config or {}
    in_per_m = cfg.get("input_cost_per_million")
    out_per_m = cfg.get("output_cost_per_million")
    if in_per_m is not None:
        params["input_cost_per_token"] = float(Decimal(str(in_per_m)) / Decimal("1000000"))
    if out_per_m is not None:
        params["output_cost_per_token"] = float(Decimal(str(out_per_m)) / Decimal("1000000"))

    return params


def build_litellm_model_payloads(
    backend: Backend,
    *,
    extra_models: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Construct `/model/new` request bodies for all enabled models on an Inference backend."""
    if backend.type != BackendType.INFERENCE or not backend.enabled:
        return []

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    auth_mode = str((backend.config or {}).get("auth_mode") or "api_key").strip().lower()
    if auth_mode == "cli_oauth" and not secrets_map.get("api_key"):
        return []
    model_ids = extract_enabled_model_ids(backend)
    for extra in extra_models or []:
        cleaned = extra.strip()
        if cleaned and cleaned != "default" and cleaned not in model_ids:
            model_ids.append(cleaned)

    payloads: list[dict[str, Any]] = []

    for model_id in model_ids:
        payloads.append(
            {
                "model_name": model_id,
                "litellm_params": _upstream_litellm_params(
                    backend, secrets_map, model_id
                ),
                "model_info": {
                    "id": f"{backend.id}:{model_id}",
                    "metadata": {
                        "backend_id": backend.id,
                        "backend_name": backend.name,
                        "provider": backend.provider,
                        "upstream_model": model_id,
                    },
                },
            }
        )
    return payloads


async def remove_backend_from_litellm(backend_id: str) -> int:
    """Remove all models registered in LiteLLM for the given `bnd_` backend ID."""
    base_url = settings.LITELLM_URL.rstrip("/")
    removed = 0
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            info_resp = await client.get(
                f"{base_url}/model/info", headers=_litellm_headers()
            )
            if info_resp.status_code == 200 and isinstance(info_resp.json(), dict):
                data = info_resp.json().get("data") or []
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    model_info = entry.get("model_info") or {}
                    if not isinstance(model_info, dict):
                        continue
                    mid = str(model_info.get("id") or "")
                    meta = model_info.get("metadata") or {}
                    meta_bid = (
                        str(meta.get("backend_id") or "")
                        if isinstance(meta, dict)
                        else ""
                    )
                    if mid.startswith(f"{backend_id}:") or meta_bid == backend_id:
                        if mid:
                            del_resp = await client.post(
                                f"{base_url}/model/delete",
                                headers=_litellm_headers(),
                                json={"id": mid},
                            )
                            if 200 <= del_resp.status_code < 300:
                                removed += 1
    except Exception as exc:
        logger.debug("LiteLLM remove_backend_from_litellm(%s) skipped: %s", backend_id, exc)
    return removed


async def sync_backend_to_litellm(
    backend: Backend,
    *,
    extra_models: list[str] | None = None,
) -> int:
    """Upsert a backend's models in LiteLLM via `/model/update` / `/model/new` and prune stale entries."""
    if backend.type != BackendType.INFERENCE:
        return 0

    payloads = build_litellm_model_payloads(backend, extra_models=extra_models)
    if not payloads:
        await remove_backend_from_litellm(backend.id)
        return 0

    base_url = settings.LITELLM_URL.rstrip("/")
    pushed = 0
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            existing_ids: set[str] = set()
            info_resp = await client.get(
                f"{base_url}/model/info", headers=_litellm_headers()
            )
            if info_resp.status_code == 200 and isinstance(info_resp.json(), dict):
                data = info_resp.json().get("data") or []
                for entry in data:
                    if not isinstance(entry, dict):
                        continue
                    model_info = entry.get("model_info") or {}
                    if not isinstance(model_info, dict):
                        continue
                    mid = str(model_info.get("id") or "")
                    meta = model_info.get("metadata") or {}
                    meta_bid = (
                        str(meta.get("backend_id") or "")
                        if isinstance(meta, dict)
                        else ""
                    )
                    if mid and (
                        mid.startswith(f"{backend.id}:") or meta_bid == backend.id
                    ):
                        existing_ids.add(mid)

            desired_ids: set[str] = set()
            for item in payloads:
                m_info = item.get("model_info")
                item_id = (
                    str(m_info.get("id") or "")
                    if isinstance(m_info, dict)
                    else ""
                )
                if item_id:
                    desired_ids.add(item_id)

                if item_id and item_id in existing_ids:
                    upd_resp = await client.post(
                        f"{base_url}/model/update",
                        headers=_litellm_headers(),
                        json={"model_id": item_id, **item},
                    )
                    if 200 <= upd_resp.status_code < 300:
                        pushed += 1
                        continue
                    # Fall back to replace if /model/update is unsupported by this LiteLLM version
                    await client.post(
                        f"{base_url}/model/delete",
                        headers=_litellm_headers(),
                        json={"id": item_id},
                    )

                resp = await client.post(
                    f"{base_url}/model/new",
                    headers=_litellm_headers(),
                    json=item,
                )
                if 200 <= resp.status_code < 300:
                    pushed += 1
                else:
                    logger.warning(
                        "LiteLLM /model/new failed for %s (HTTP %d): %s",
                        item.get("model_name"),
                        resp.status_code,
                        resp.text[:200],
                    )

            stale_ids = existing_ids - desired_ids
            for stale_id in stale_ids:
                await client.post(
                    f"{base_url}/model/delete",
                    headers=_litellm_headers(),
                    json={"id": stale_id},
                )
    except Exception as exc:
        logger.debug("LiteLLM sync_backend_to_litellm(%s) deferred: %s", backend.id, exc)
    return pushed


async def sync_all_backends_to_litellm(session: AsyncSession) -> int:
    """Push all enabled Inference backends from GitSeed's database to LiteLLM on startup."""
    backends = list(
        (
            await session.execute(
                select(Backend).where(
                    Backend.type == BackendType.INFERENCE,
                    col(Backend.enabled).is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    total = 0
    for backend in backends:
        total += await sync_backend_to_litellm(backend)
    return total


BudgetEnforcement = Literal["enforced", "estimated", "unpriced"]

ENFORCED_BUDGET_DETAIL = "Spend is enforced via LiteLLM virtual key max_budget."
ESTIMATED_BUDGET_DETAIL = (
    "Spend is estimated from CLI token reports and bounded by wall-clock timeout and max turns "
    "(Personal Mode subscription OAuth)."
)
UNPRICED_BUDGET_DETAIL = (
    "Model has zero or unconfigured token pricing; spend cannot be enforced in USD."
)

_LOCAL_OR_CUSTOM_PROVIDERS = frozenset({"ollama", "lmstudio", "vllm", "local", "custom"})


def _extract_configured_token_rates(
    backend: Backend | None,
    model: str | None = None,
) -> tuple[Decimal | None, Decimal | None]:
    """Return `(input_cost_per_million, output_cost_per_million)` if configured on backend/model."""
    if backend is None or not isinstance(backend.config, dict):
        return None, None
    cfg = backend.config
    cleaned = (model or "").strip().lower()
    if cleaned and isinstance(cfg.get("models"), list):
        for item in cfg["models"]:
            if not isinstance(item, dict):
                continue
            mid = str(item.get("id") or "").strip().lower()
            if mid and mid == cleaned:
                m_in = item.get("input_cost_per_million")
                m_out = item.get("output_cost_per_million")
                m_cpt = item.get("cost_per_token")
                if m_in is not None or m_out is not None:
                    return (
                        Decimal(str(m_in)) if m_in is not None else Decimal("0"),
                        Decimal(str(m_out)) if m_out is not None else Decimal("0"),
                    )
                if m_cpt is not None:
                    rate_m = Decimal(str(m_cpt)) * Decimal("1000000")
                    return rate_m, rate_m

    in_per_m = cfg.get("input_cost_per_million")
    out_per_m = cfg.get("output_cost_per_million")
    if in_per_m is not None or out_per_m is not None:
        return (
            Decimal(str(in_per_m)) if in_per_m is not None else Decimal("0"),
            Decimal(str(out_per_m)) if out_per_m is not None else Decimal("0"),
        )
    in_cpt = cfg.get("input_cost_per_token")
    out_cpt = cfg.get("output_cost_per_token")
    cpt = cfg.get("cost_per_token")
    if in_cpt is not None or out_cpt is not None:
        return (
            Decimal(str(in_cpt)) * Decimal("1000000") if in_cpt is not None else Decimal("0"),
            Decimal(str(out_cpt)) * Decimal("1000000") if out_cpt is not None else Decimal("0"),
        )
    if cpt is not None:
        rate_m = Decimal(str(cpt)) * Decimal("1000000")
        return rate_m, rate_m
    return None, None


def is_unpriced_backend(backend: Backend | None, model: str | None = None) -> bool:
    """Return True when a backend/model has zero USD token pricing (`cost_per_token == 0`)."""
    if backend is None or backend.type != BackendType.INFERENCE:
        return False
    if is_personal_mode_backend(backend):
        return False
    in_rate, out_rate = _extract_configured_token_rates(backend, model)
    if in_rate is not None or out_rate is not None:
        return (in_rate or Decimal("0")) <= Decimal("0") and (
            out_rate or Decimal("0")
        ) <= Decimal("0")
    provider = str(backend.provider or "").strip().lower()
    return provider in _LOCAL_OR_CUSTOM_PROVIDERS


def estimate_model_token_cost(
    model: str | None,
    input_tokens: int,
    output_tokens: int,
    *,
    backend: Backend | None = None,
) -> Decimal:
    """Estimate USD cost from `(input_tokens, output_tokens)` for `cli_oauth` or fallback runs."""
    in_tok = max(0, int(input_tokens or 0))
    out_tok = max(0, int(output_tokens or 0))
    if in_tok == 0 and out_tok == 0:
        return Decimal("0")

    cfg_in, cfg_out = _extract_configured_token_rates(backend, model)
    if cfg_in is not None or cfg_out is not None:
        in_per_m = max(Decimal("0"), cfg_in or Decimal("0"))
        out_per_m = max(Decimal("0"), cfg_out or Decimal("0"))
    else:
        cleaned = (model or "").strip().lower()
        if "haiku" in cleaned:
            in_per_m, out_per_m = Decimal("0.80"), Decimal("4.00")
        elif "opus" in cleaned:
            in_per_m, out_per_m = Decimal("15.00"), Decimal("75.00")
        elif "flash-lite" in cleaned or "flash_lite" in cleaned or "4o-mini" in cleaned:
            in_per_m, out_per_m = Decimal("0.15"), Decimal("0.60")
        elif "gemini" in cleaned and "flash" in cleaned:
            in_per_m, out_per_m = Decimal("0.30"), Decimal("2.50")
        elif "gemini" in cleaned or cleaned == "pro":
            in_per_m, out_per_m = Decimal("1.25"), Decimal("10.00")
        elif "o3-mini" in cleaned or "o4-mini" in cleaned or "mini" in cleaned:
            in_per_m, out_per_m = Decimal("1.10"), Decimal("4.40")
        elif "gpt-" in cleaned or "codex" in cleaned or cleaned.startswith(("o1", "o3", "o4")):
            in_per_m, out_per_m = Decimal("2.50"), Decimal("10.00")
        else:
            in_per_m, out_per_m = Decimal("3.00"), Decimal("15.00")

    raw_usd = (
        (Decimal(in_tok) * in_per_m) + (Decimal(out_tok) * out_per_m)
    ) / Decimal("1000000")
    return raw_usd.quantize(Decimal("0.0001"))


def is_personal_mode_backend(backend: Backend | None) -> bool:
    """Return True when `backend` is an Inference backend operating in Personal Mode (`cli_oauth`)."""
    if backend is None or backend.type != BackendType.INFERENCE:
        return False
    cfg = backend.config or {}
    auth_mode = str(cfg.get("auth_mode") or "api_key").strip().lower()
    prov = str(backend.provider or "").strip().lower()
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    if secrets_map.get("api_key"):
        return False
    if auth_mode == "cli_oauth" or prov == "cli_oauth" or bool(cfg.get("personal_mode")):
        return True
    if secrets_map.get("cli_auth_bundle") or secrets_map.get("oauth_token"):
        return True
    return False


def _has_external_webhook_marker(seed: Any | None, run: Any | None) -> bool:
    """Return True if the seed or run carries external webhook origin markers."""
    run_out = getattr(run, "output", None) if run is not None else None
    if isinstance(run_out, dict) and bool(run_out.get("webhook_triggered")):
        return True
    if seed is None:
        return False
    raw_tags = getattr(seed, "tags", None) or []
    if isinstance(raw_tags, list | tuple | set):
        for tag in raw_tags:
            t_low = str(tag or "").strip().lower()
            if t_low in ("webhook", "webhook_triggered") or t_low.startswith("webhook:"):
                return True
    for attr in ("title", "description", "specification"):
        txt = str(getattr(seed, attr, "") or "")
        txt_low = txt.lower()
        if "[webhook:" in txt_low or "<untrusted_external_input>" in txt_low:
            return True
    return False


def validate_personal_mode_backend_access(
    session: AsyncSession | None,
    backend: Backend,
    seed: Seed | None = None,
    *,
    run: Run | Any | None = None,
    actor_user_id: str | None = None,
    allow_multi_user: bool | None = None,
) -> None:
    """Validate Personal Mode (`cli_oauth`) acknowledgement and single-owner seed/session restrictions."""
    del session
    if not is_personal_mode_backend(backend):
        return

    if not settings.GITSEED_ALLOW_PERSONAL_CLI_OAUTH:
        raise Forbidden(
            "Personal Mode (subscription CLI OAuth) is disabled on this instance "
            "(GITSEED_ALLOW_PERSONAL_CLI_OAUTH=false)."
        )

    cfg = backend.config or {}
    if cfg.get("personal_mode_acknowledged") is False:
        raise Invalid(
            "Personal Mode (subscription CLI OAuth) requires explicit acknowledgement "
            "(personal_mode_acknowledged=true)."
        )

    owner_user_id = str(cfg.get("personal_mode_owner_user_id") or "").strip()
    effective_allow_multi = (
        settings.ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS
        if allow_multi_user is None
        else bool(allow_multi_user)
    )
    run_out = getattr(run, "output", None) if run is not None else None
    run_actor_id = ""
    if isinstance(run_out, dict):
        run_actor_id = str(
            run_out.get("_actor_user_id") or run_out.get("actor_user_id") or ""
        ).strip()
    effective_actor_id = (actor_user_id or "").strip() or run_actor_id
    run_webhook_triggered = bool(
        isinstance(run_out, dict) and run_out.get("webhook_triggered")
    )
    has_webhook_marker = _has_external_webhook_marker(seed, run)
    explicit_owner_action = bool(
        owner_user_id
        and effective_actor_id
        and effective_actor_id == owner_user_id
        and not run_webhook_triggered
    )

    if not bool(cfg.get("allow_webhook_triggers")) and (
        run_webhook_triggered or (has_webhook_marker and not explicit_owner_action)
    ):
        raise Invalid(
            f"Personal Mode backend '{backend.name}' cannot execute external "
            "webhook-triggered Seeds or Runs without explicit owner action."
        )

    if not effective_allow_multi:
        if seed is not None:
            seed_creator = str(getattr(seed, "created_by", None) or "").strip()
            if not seed_creator:
                raise Invalid(
                    f"Personal Mode backend '{backend.name}' is bound to single owner "
                    f"'{owner_user_id or 'owner'}' and cannot execute webhook-triggered or "
                    "unowned Seeds unless ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS is enabled."
                )

    if owner_user_id and effective_actor_id and effective_actor_id != owner_user_id:
        raise Forbidden(
            f"Personal Mode backend '{backend.name}' is bound to owner '{owner_user_id}' "
            f"and cannot be used by user '{effective_actor_id}'."
        )

    if owner_user_id and not effective_allow_multi:
        if seed is not None and not effective_actor_id:
            seed_creator = str(getattr(seed, "created_by", None) or "").strip()
            if seed_creator != owner_user_id:
                raise Invalid(
                    f"Personal Mode backend '{backend.name}' is bound to owner '{owner_user_id}' "
                    f"and cannot execute Seeds created by '{seed_creator}' unless "
                    "ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS is enabled."
                )
        elif seed is not None:
            seed_creator = str(getattr(seed, "created_by", None) or "").strip()
            if seed_creator and seed_creator != owner_user_id:
                raise Invalid(
                    f"Personal Mode backend '{backend.name}' is bound to owner '{owner_user_id}' "
                    f"and cannot execute Seeds created by '{seed_creator}' unless "
                    "ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS is enabled."
                )


def resolve_budget_enforcement(
    backend: Backend | None = None,
    run: Run | None = None,
) -> tuple[BudgetEnforcement, str]:
    """Return `('enforced' | 'estimated' | 'unpriced', enforcement_detail)` for a backend or run."""
    if run is not None and isinstance(run.output, dict):
        raw_enf = str(
            run.output.get("cost_enforcement")
            or run.output.get("budget_enforcement")
            or run.output.get("enforcement")
            or ""
        ).strip().lower()
        raw_detail = run.output.get("enforcement_detail")
        if raw_enf == "unpriced":
            return (
                "unpriced",
                str(raw_detail).strip()
                if isinstance(raw_detail, str) and raw_detail.strip()
                else UNPRICED_BUDGET_DETAIL,
            )
        if raw_enf == "estimated":
            return (
                "estimated",
                str(raw_detail).strip()
                if isinstance(raw_detail, str) and raw_detail.strip()
                else ESTIMATED_BUDGET_DETAIL,
            )
        if str(run.output.get("auth_mode") or "").strip().lower() == "cli_oauth":
            return "estimated", ESTIMATED_BUDGET_DETAIL
        if raw_enf == "enforced" and backend is None:
            return (
                "enforced",
                str(raw_detail).strip()
                if isinstance(raw_detail, str) and raw_detail.strip()
                else ENFORCED_BUDGET_DETAIL,
            )

    if backend is not None and is_personal_mode_backend(backend):
        return "estimated", ESTIMATED_BUDGET_DETAIL

    run_model = (
        str(getattr(run, "model", "") or "")
        if run is not None
        else None
    )
    if backend is not None and is_unpriced_backend(backend, run_model):
        return "unpriced", UNPRICED_BUDGET_DETAIL

    return "enforced", ENFORCED_BUDGET_DETAIL


async def resolve_models_backend_for_run(
    session: AsyncSession,
    run: Run,
    project: Project,
    *,
    seed: Seed | None = None,
    allow_disabled: bool = False,
) -> tuple[Backend, str]:
    """Select the Inference backend and model alias for a Run."""
    conditions: list[Any] = [Backend.type == BackendType.INFERENCE]
    if not allow_disabled:
        conditions.append(col(Backend.enabled).is_(True))
    stmt = (
        select(Backend)
        .where(*conditions)
        .order_by(col(Backend.enabled).desc(), col(Backend.created_at).asc())
    )
    backends = list((await session.execute(stmt)).scalars().all())
    if not backends:
        raise Invalid("No enabled Inference backend configured")

    if project.backends_restricted:
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
        restricted_backends = [b for b in backends if b.id in allowed_project_bids]
        backends = restricted_backends
        if not backends:
            raise Invalid(
                "Project has backends_restricted enabled and no granted Inference backend is available"
            )

    accessible: list[Backend] = []
    for b in backends:
        if b.access_restricted:
            grant = await session.get(AgentBackend, (b.id, run.agent_id))
            if grant is None:
                continue
        accessible.append(b)

    if not accessible:
        raise Invalid("Agent is not granted access to any enabled Inference backend")

    if any(is_personal_mode_backend(b) for b in accessible):
        resolved_seed = seed
        if resolved_seed is None and getattr(run, "seed_id", None):
            maybe_seed = await session.get(Seed, run.seed_id)
            if isinstance(maybe_seed, Seed) or (
                maybe_seed is not None and hasattr(maybe_seed, "created_by")
            ):
                resolved_seed = maybe_seed

        validated_accessible: list[Backend] = []
        first_personal_err: Exception | None = None
        for b in accessible:
            try:
                validate_personal_mode_backend_access(
                    session, b, resolved_seed, run=run
                )
                validated_accessible.append(b)
            except (Invalid, Forbidden) as exc:
                if first_personal_err is None:
                    first_personal_err = exc
        if not validated_accessible and first_personal_err is not None:
            raise first_personal_err
        accessible = validated_accessible

    runtime_str = str(run.runtime or "opencode").strip().lower()
    preferred_providers_by_runtime: dict[str, tuple[str, ...]] = {
        "claude-code": ("anthropic",),
        "codex": ("openai",),
        "antigravity": ("antigravity", "gemini"),
        "gemini": ("gemini", "antigravity"),
        "grok-build": ("xai",),
    }
    preferred_providers = preferred_providers_by_runtime.get(runtime_str)
    if preferred_providers:
        accessible.sort(
            key=lambda b: (
                preferred_providers.index(b.provider.lower())
                if b.provider.lower() in preferred_providers
                else (10 if b.provider.lower() == "litellm" else 20)
            )
        )
    else:
        # For opencode (which routes exclusively through LiteLLM), prefer api_key/local backends over cli_oauth-only backends
        accessible.sort(
            key=lambda b: (
                1
                if str((b.config or {}).get("auth_mode") or "api_key").lower() == "cli_oauth"
                and not decrypt_secrets(b.secrets_encrypted).get("api_key")
                else 0
            )
        )

    requested_model = (run.model or "default").strip()
    if requested_model and requested_model != "default":
        for b in accessible:
            if requested_model in extract_enabled_model_ids(b):
                return b, requested_model
        return accessible[0], requested_model

    for b in accessible:
        enabled_ids = extract_enabled_model_ids(b)
        if enabled_ids:
            return b, enabled_ids[0]

    # Auto-probe live models if the backend hasn't had models discovered yet
    for b in accessible:
        probed = await _probe_live_backend_models(b)
        if probed:
            cfg = dict(b.config or {})
            cfg["discovered_models"] = probed
            if not cfg.get("models"):
                cfg["models"] = [{"id": mid, "enabled": True} for mid in probed]
            b.config = cfg
            await session.flush()
            return b, probed[0]

    default_by_provider = {
        "anthropic": "claude-sonnet-4-5",
        "openai": "gpt-5.4",
        "antigravity": "gemini-2.5-pro",
        "gemini": "gemini-2.5-pro",
        "xai": "grok-4.7",
        "ollama": "qwen2.5-coder:32b",
        "lmstudio": "local-model",
    }
    first = accessible[0]
    return first, default_by_provider.get(first.provider.lower(), "gpt-5.4")


async def _probe_live_backend_models(backend: Backend) -> list[str]:
    """Best-effort live model probe when a backend has no enabled models configured yet."""
    provider = backend.provider.lower()
    raw_ep = (backend.endpoint or "").strip().rstrip("/")
    endpoint = docker_host_fallback_url(raw_ep) if raw_ep else ""
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    api_key = secrets_map.get("api_key", "").strip()
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}

    try:
        async with httpx.AsyncClient(timeout=4.0) as client:
            if provider == "ollama":
                base = (endpoint or "http://host.docker.internal:11434").removesuffix("/v1")
                resp = await client.get(f"{base}/api/tags")
                if resp.status_code == 200 and isinstance(resp.json(), dict):
                    models = resp.json().get("models") or []
                    return [
                        str(m.get("name")).strip()
                        for m in models
                        if isinstance(m, dict) and m.get("name")
                    ]
            else:
                if not endpoint:
                    if provider == "lmstudio":
                        endpoint = "http://host.docker.internal:1234/v1"
                    elif provider == "vllm":
                        endpoint = "http://host.docker.internal:8000/v1"
                    elif provider == "openai":
                        endpoint = "https://api.openai.com/v1"
                if endpoint:
                    models_url = (
                        f"{endpoint}/models"
                        if endpoint.endswith("/v1")
                        else f"{endpoint}/v1/models"
                    )
                    resp = await client.get(models_url, headers=headers)
                    if resp.status_code == 200 and isinstance(resp.json(), dict):
                        data = resp.json().get("data") or []
                        return [
                            str(m.get("id")).strip()
                            for m in data
                            if isinstance(m, dict) and m.get("id")
                        ]
    except Exception:
        pass
    return []


async def _compute_remaining_budget(
    session: AsyncSession,
    seed: Seed,
    project: Project,
    *,
    backend: Backend | None = None,
) -> tuple[Decimal | None, str | None, dict[str, Any] | None]:
    """Return `(remaining_budget, blocked_reason, activity_data)` prior to or during a run."""
    org = await session.get(OrgSettings, 1)
    effective_seed_cap = (
        seed.budget_cap
        if seed.budget_cap is not None
        else (
            project.seed_budget_cap
            if project.seed_budget_cap is not None
            else (org.default_seed_budget_cap if org is not None else None)
        )
    )
    effective_checkpoint = (
        project.spend_checkpoint
        if project.spend_checkpoint is not None
        else (org.default_spend_checkpoint if org is not None else None)
    )

    enf, enf_detail = resolve_budget_enforcement(backend)
    seed_spent = Decimal(str(seed.spent or 0))
    candidates: list[Decimal] = []

    if effective_seed_cap is not None:
        rem = Decimal(str(effective_seed_cap)) - seed_spent
        if rem <= 0:
            return (
                Decimal("0"),
                f"Seed budget cap ({effective_seed_cap}) reached",
                {
                    "reason": "seed_budget_cap",
                    "spent": str(seed_spent),
                    "budget_cap": str(effective_seed_cap),
                    "enforcement": enf,
                    "enforcement_detail": enf_detail,
                },
            )
        candidates.append(rem)

    if effective_checkpoint is not None:
        cp_val = Decimal(str(effective_checkpoint))
        approved_through = Decimal(str(seed.checkpoint_approved_through or 0))
        if seed_spent >= cp_val and seed_spent >= approved_through:
            return (
                Decimal("0"),
                f"Spend checkpoint ({effective_checkpoint}) reached",
                {
                    "reason": "spend_checkpoint",
                    "spent": str(seed_spent),
                    "spend_checkpoint": str(effective_checkpoint),
                    "enforcement": enf,
                    "enforcement_detail": enf_detail,
                },
            )
        if approved_through > seed_spent:
            next_cp = approved_through
        else:
            next_cp = ((seed_spent // cp_val) + 1) * cp_val
        candidates.append(next_cp - seed_spent)

    if project.monthly_budget is not None:
        now = datetime.now(UTC)
        month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
        run_month_subq = (
            select(func.coalesce(func.sum(col(Run.cost)), Decimal("0")))
            .join(Seed, col(Run.seed_id) == col(Seed.id))
            .where(
                Seed.project_id == project.id,
                col(Run.created_at) >= month_start,
            )
            .scalar_subquery()
        )
        sess_month_subq = (
            select(func.coalesce(func.sum(col(AgentSession.total_cost)), Decimal("0")))
            .where(
                AgentSession.project_id == project.id,
                col(AgentSession.created_at) >= month_start,
            )
            .scalar_subquery()
        )
        monthly_spent_raw = await session.scalar(
            select(
                func.coalesce(run_month_subq, Decimal("0"))
                + func.coalesce(sess_month_subq, Decimal("0"))
            )
        )
        monthly_spent = Decimal(str(monthly_spent_raw or 0))
        rem_month = Decimal(str(project.monthly_budget)) - monthly_spent
        if rem_month <= 0:
            return (
                Decimal("0"),
                f"Project monthly budget ({project.monthly_budget}) reached",
                {
                    "reason": "project_monthly_budget",
                    "monthly_spent": str(monthly_spent),
                    "monthly_budget": str(project.monthly_budget),
                    "enforcement": enf,
                    "enforcement_detail": enf_detail,
                },
            )
        candidates.append(rem_month)

    return (min(candidates) if candidates else None), None, None


async def compute_budget_status(
    session: AsyncSession,
    seed: Seed,
    project: Project,
    *,
    backend: Backend | None = None,
) -> dict[str, Any]:
    """Build a complete budget status and spend summary dict including `enforcement`."""
    org = await session.get(OrgSettings, 1)
    effective_seed_cap = (
        seed.budget_cap
        if seed.budget_cap is not None
        else (
            project.seed_budget_cap
            if project.seed_budget_cap is not None
            else (org.default_seed_budget_cap if org is not None else None)
        )
    )
    effective_checkpoint = (
        project.spend_checkpoint
        if project.spend_checkpoint is not None
        else (org.default_spend_checkpoint if org is not None else None)
    )
    remaining_budget, blocked_reason, _ = await _compute_remaining_budget(
        session, seed, project, backend=backend
    )
    enf, enf_detail = resolve_budget_enforcement(backend)
    return {
        "spent": Decimal(str(seed.spent or 0)),
        "budget_cap": effective_seed_cap,
        "remaining_budget": remaining_budget,
        "spend_checkpoint": effective_checkpoint,
        "checkpoint_approved_through": Decimal(str(seed.checkpoint_approved_through or 0)),
        "monthly_budget": project.monthly_budget,
        "blocked_reason": blocked_reason,
        "enforcement": enf,
        "enforcement_detail": enf_detail,
    }


def _safe_extract_scalars(result: Any) -> list[Any]:
    """Extract scalar rows from a SQLAlchemy Result without triggering unawaited AsyncMock warnings."""
    if result is None or inspect.iscoroutinefunction(result):
        return []
    scalars_fn = getattr(result, "scalars", None)
    if not callable(scalars_fn) or inspect.iscoroutinefunction(scalars_fn):
        return []
    scalars_obj = scalars_fn()
    if inspect.iscoroutine(scalars_obj):
        scalars_obj.close()
        return []
    all_fn = getattr(scalars_obj, "all", None)
    if not callable(all_fn) or inspect.iscoroutinefunction(all_fn):
        return []
    rows = all_fn()
    if inspect.iscoroutine(rows):
        rows.close()
        return []
    return list(rows) if isinstance(rows, list | tuple) else []


async def resolve_org_or_seed_budget_enforcement(
    session: AsyncSession,
    seed_id: str | None = None,
) -> tuple[BudgetEnforcement, str]:
    """Determine whether spend for a seed or the organization is enforced, estimated, or unpriced."""
    try:
        if seed_id:
            res = await session.execute(select(Run).where(col(Run.seed_id) == seed_id))
            for r in _safe_extract_scalars(res):
                if isinstance(r, Run):
                    enf, detail = resolve_budget_enforcement(None, r)
                    if enf in ("estimated", "unpriced"):
                        return enf, detail

        res_backends = await session.execute(
            select(Backend).where(
                col(Backend.type) == BackendType.INFERENCE,
                col(Backend.enabled).is_(True),
            )
        )
        backends = [
            b for b in _safe_extract_scalars(res_backends) if isinstance(b, Backend)
        ]
        if backends and any(is_personal_mode_backend(b) for b in backends):
            return "estimated", ESTIMATED_BUDGET_DETAIL
        if backends and all(is_unpriced_backend(b) for b in backends):
            return "unpriced", UNPRICED_BUDGET_DETAIL
    except Exception:
        pass
    return "enforced", ENFORCED_BUDGET_DETAIL


def derive_litellm_virtual_key(run_id: str) -> str:
    """Derive a deterministic `sk-gsr-...` virtual key value for a Run ID."""
    digest = hmac.new(
        settings.GITSEED_SECRET_KEY.encode("utf-8"),
        f"litellm-vk:{run_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"sk-gsr-{run_id}-{digest}"


async def create_run_virtual_key(
    *,
    run: Run,
    seed: Seed,
    project: Project,
    backend: Backend,
    resolved_model: str,
    remaining_budget: Decimal | None,
    ttl_seconds: int = 7200,
) -> str:
    """Mint a per-run LiteLLM virtual key with `max_budget` via `/key/generate`."""
    validate_personal_mode_backend_access(None, backend, seed, run=run)
    enf, enf_detail = resolve_budget_enforcement(backend, run)
    if enf == "enforced" and is_unpriced_backend(backend, resolved_model):
        enf = "unpriced"
        enf_detail = UNPRICED_BUDGET_DETAIL
    out = dict(run.output or {})
    out["cost_enforcement"] = enf
    out["enforcement"] = enf
    out["enforcement_detail"] = enf_detail
    out["models_backend_id"] = backend.id
    out["gateway_model"] = resolved_model
    out["auth_mode"] = "cli_oauth" if is_personal_mode_backend(backend) else "api_key"
    run.output = out

    await sync_backend_to_litellm(backend, extra_models=[resolved_model])
    default_key = derive_litellm_virtual_key(run.id)
    payload: dict[str, Any] = {
        "key": default_key,
        "key_alias": f"run:{run.id}",
        "models": [resolved_model],
        "duration": f"{max(300, ttl_seconds)}s",
        "metadata": {
            "run_id": run.id,
            "seed_id": seed.id,
            "project_id": project.id,
            "backend_id": backend.id,
        },
    }
    if remaining_budget is not None:
        payload["max_budget"] = float(remaining_budget)

    base_url = settings.LITELLM_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url}/key/generate",
                headers=_litellm_headers(),
                json=payload,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), dict):
                returned_key = resp.json().get("key")
                if isinstance(returned_key, str) and returned_key.strip():
                    return returned_key.strip()
            upd = await client.post(
                f"{base_url}/key/update",
                headers=_litellm_headers(),
                json={k: v for k, v in payload.items() if k != "key_alias"},
            )
            if not 200 <= upd.status_code < 300:
                logger.warning(
                    "LiteLLM run key mint failed for %s (generate HTTP %s, update HTTP %s): %s",
                    run.id,
                    resp.status_code,
                    upd.status_code,
                    upd.text[:200],
                )
    except Exception as exc:
        logger.debug("LiteLLM /key/generate fallback for run %s: %s", run.id, exc)

    return default_key


def derive_session_virtual_key(session_id: str) -> str:
    """Deterministic `sk-gss-...` LiteLLM virtual key for an interactive agent session."""
    digest = hmac.new(
        settings.GITSEED_SECRET_KEY.encode("utf-8"),
        f"litellm-vk-session:{session_id}".encode(),
        hashlib.sha256,
    ).hexdigest()[:24]
    return f"sk-gss-{session_id}-{digest}"


async def compute_project_remaining_monthly_budget(
    session: AsyncSession | None,
    project: Project | Any | None,
) -> Decimal | None:
    """Compute clamped remaining current-month project budget (`max(0, monthly_budget - spent)`), or None."""
    if project is None or getattr(project, "monthly_budget", None) is None:
        return None
    month_cap = Decimal(str(project.monthly_budget))
    if session is None:
        return max(Decimal("0"), month_cap)
    scalar_fn = getattr(session, "scalar", None)
    if not callable(scalar_fn):
        return max(Decimal("0"), month_cap)
    now = datetime.now(UTC)
    month_start = datetime(now.year, now.month, 1, tzinfo=UTC)
    run_month_subq = (
        select(func.coalesce(func.sum(col(Run.cost)), Decimal("0")))
        .join(Seed, col(Run.seed_id) == col(Seed.id))
        .where(
            Seed.project_id == project.id,
            col(Run.created_at) >= month_start,
        )
        .scalar_subquery()
    )
    sess_month_subq = (
        select(func.coalesce(func.sum(col(AgentSession.total_cost)), Decimal("0")))
        .where(
            AgentSession.project_id == project.id,
            col(AgentSession.created_at) >= month_start,
        )
        .scalar_subquery()
    )
    raw_res = scalar_fn(
        select(
            func.coalesce(run_month_subq, Decimal("0"))
            + func.coalesce(sess_month_subq, Decimal("0"))
        )
    )
    monthly_spent_raw = await raw_res if inspect.iscoroutine(raw_res) else raw_res
    if isinstance(monthly_spent_raw, (int, float, Decimal)):
        monthly_spent = Decimal(str(monthly_spent_raw))
    elif isinstance(monthly_spent_raw, str):
        try:
            monthly_spent = Decimal(monthly_spent_raw)
        except Exception:
            monthly_spent = Decimal("0")
    else:
        monthly_spent = Decimal("0")
    return max(Decimal("0"), month_cap - monthly_spent)


def _resolve_session_seed_max_budget(
    seed: Any | None = None,
    max_budget: Decimal | float | None = None,
    *,
    project: Project | Any | None = None,
    org: OrgSettings | Any | None = None,
    remaining_project_budget: Decimal | float | None = None,
) -> float | None:
    """Compute remaining session budget from explicit `max_budget`, seed cap, project cap, or org default."""
    if max_budget is not None:
        return float(max(Decimal("0"), Decimal(str(max_budget))))
    candidates: list[Decimal] = []
    if seed is not None:
        raw_budget = getattr(seed, "budget_cap", None)
        if raw_budget is None:
            raw_budget = getattr(seed, "budget", None)
        if raw_budget is not None:
            budget_dec = Decimal(str(raw_budget))
            if budget_dec > Decimal("0"):
                spent_dec = Decimal(str(getattr(seed, "spent", None) or 0))
                candidates.append(max(Decimal("0"), budget_dec - spent_dec))
    if not candidates and project is not None and getattr(project, "seed_budget_cap", None) is not None:
        proj_cap = Decimal(str(project.seed_budget_cap))
        if proj_cap > Decimal("0"):
            spent_dec = Decimal(str(getattr(seed, "spent", None) or 0)) if seed is not None else Decimal("0")
            candidates.append(max(Decimal("0"), proj_cap - spent_dec))
    if not candidates and org is not None and getattr(org, "default_seed_budget_cap", None) is not None:
        org_cap = Decimal(str(org.default_seed_budget_cap))
        if org_cap > Decimal("0"):
            spent_dec = Decimal(str(getattr(seed, "spent", None) or 0)) if seed is not None else Decimal("0")
            candidates.append(max(Decimal("0"), org_cap - spent_dec))
    if remaining_project_budget is not None:
        candidates.append(max(Decimal("0"), Decimal(str(remaining_project_budget))))
    elif project is not None and getattr(project, "monthly_budget", None) is not None:
        month_cap = Decimal(str(project.monthly_budget))
        candidates.append(max(Decimal("0"), month_cap))
    return float(min(candidates)) if candidates else None


async def create_session_virtual_key(
    *,
    session_id: str,
    project_id: str,
    agent_id: str,
    backend: Backend,
    model: str,
    ttl_seconds: int,
    seed: Seed | Any | None = None,
    max_budget: Decimal | float | None = None,
    project: Project | Any | None = None,
    org: OrgSettings | Any | None = None,
    remaining_project_budget: Decimal | float | None = None,
    session: AsyncSession | None = None,
) -> str:
    """Mint (or refresh) a LiteLLM virtual key scoped to one interactive agent session.

    The provider's real API key never enters the container; the CLI talks to the LiteLLM
    gateway with this key instead, exactly like seed runs do with their per-run keys.
    """
    if (
        remaining_project_budget is None
        and session is not None
        and project is not None
        and getattr(project, "monthly_budget", None) is not None
    ):
        remaining_project_budget = await compute_project_remaining_monthly_budget(
            session, project
        )
    models = [m for m in dict.fromkeys([model, *extract_enabled_model_ids(backend)]) if m]
    await sync_backend_to_litellm(backend, extra_models=[model] if model else None)
    key = derive_session_virtual_key(session_id)
    resolved_max_budget = _resolve_session_seed_max_budget(
        seed=seed,
        max_budget=max_budget,
        project=project,
        org=org,
        remaining_project_budget=remaining_project_budget,
    )
    metadata: dict[str, Any] = {
        "agent_session_id": session_id,
        "project_id": project_id,
        "agent_id": agent_id,
        "backend_id": backend.id,
    }
    if seed is not None and getattr(seed, "id", None):
        metadata["seed_id"] = str(seed.id)
    payload: dict[str, Any] = {
        "key": key,
        "key_alias": f"session:{session_id}",
        "models": models,
        "duration": f"{max(300, ttl_seconds)}s",
        "metadata": metadata,
    }
    if resolved_max_budget is not None:
        payload["max_budget"] = resolved_max_budget
    base_url = settings.LITELLM_URL.rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{base_url}/key/generate", headers=_litellm_headers(), json=payload
            )
            if 200 <= resp.status_code < 300:
                returned = resp.json().get("key") if isinstance(resp.json(), dict) else None
                return str(returned or key).strip()
            # Key already exists (session reopened): refresh its expiry and model list.
            upd = await client.post(
                f"{base_url}/key/update",
                headers=_litellm_headers(),
                json={k: v for k, v in payload.items() if k != "key_alias"},
            )
            if not 200 <= upd.status_code < 300:
                logger.warning(
                    "LiteLLM session key mint failed for %s (generate HTTP %s, update HTTP %s): %s",
                    session_id,
                    resp.status_code,
                    upd.status_code,
                    upd.text[:200],
                )
    except Exception as exc:
        logger.warning("LiteLLM session key mint failed for %s: %s", session_id, exc)
    return key


async def provision_session_gateway_access(
    *,
    session_id: str,
    project_id: str,
    agent_id: str,
    backend: Backend,
    model: str,
    ttl_seconds: int = 15000,
    seed: Seed | Any | None = None,
    max_budget: Decimal | float | None = None,
    project: Project | Any | None = None,
    org: OrgSettings | Any | None = None,
    remaining_project_budget: Decimal | float | None = None,
    session: AsyncSession | None = None,
) -> str:
    """Mint a LiteLLM session virtual key enforcing any attached Seed budget cap."""
    return await create_session_virtual_key(
        session_id=session_id,
        project_id=project_id,
        agent_id=agent_id,
        backend=backend,
        model=model,
        ttl_seconds=ttl_seconds,
        seed=seed,
        max_budget=max_budget,
        project=project,
        org=org,
        remaining_project_budget=remaining_project_budget,
        session=session,
    )


async def revoke_session_virtual_key(session_id: str) -> None:
    """Best-effort delete of a session's LiteLLM virtual key."""
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(
                f"{settings.LITELLM_URL.rstrip('/')}/key/delete",
                headers=_litellm_headers(),
                json={"keys": [derive_session_virtual_key(session_id)]},
            )
    except Exception as exc:
        logger.debug("LiteLLM session key revoke skipped for %s: %s", session_id, exc)


async def revoke_run_virtual_key(run_id: str) -> None:
    """Best-effort delete of a run's LiteLLM virtual key after run state is finalized."""
    virtual_key = derive_litellm_virtual_key(run_id)
    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            await client.post(
                f"{settings.LITELLM_URL.rstrip('/')}/key/delete",
                headers=_litellm_headers(),
                json={"keys": [virtual_key], "key_aliases": [f"run:{run_id}"]},
            )
    except Exception as exc:
        logger.debug("LiteLLM run key revoke skipped for %s: %s", run_id, exc)


def _extract_tokens_and_spend_from_logs(
    logs_payload: Any,
    *,
    run_id: str,
    virtual_key: str,
    token_hashes: set[str],
    seen_request_ids: set[str],
) -> tuple[int, int, Decimal]:
    """Sum `(input_tokens, output_tokens, spend)` from a LiteLLM `/spend/logs` response."""
    entries: list[dict[str, Any]] = []
    if isinstance(logs_payload, list):
        entries = [x for x in logs_payload if isinstance(x, dict)]
    elif isinstance(logs_payload, dict):
        data = logs_payload.get("data") or logs_payload.get("logs") or []
        if isinstance(data, list):
            entries = [x for x in data if isinstance(x, dict)]

    in_tokens = 0
    out_tokens = 0
    total_spend = Decimal("0")
    expected_alias = f"run:{run_id}"

    for entry in entries:
        req_id = str(
            entry.get("request_id")
            or entry.get("id")
            or entry.get("call_id")
            or ""
        ).strip()
        if req_id and req_id in seen_request_ids:
            continue

        entry_key = str(entry.get("api_key") or "").strip()
        meta = entry.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        meta_dict = meta if isinstance(meta, dict) else {}
        alias = str(
            meta_dict.get("user_api_key_alias")
            or entry.get("key_alias")
            or entry.get("api_key_alias")
            or ""
        ).strip()
        user_key_meta = meta_dict.get("user_api_key_metadata")
        meta_run_id = str(
            (user_key_meta.get("run_id") if isinstance(user_key_meta, dict) else None)
            or meta_dict.get("run_id")
            or ""
        ).strip()

        matches = (
            (entry_key and (entry_key == virtual_key or entry_key in token_hashes))
            or alias == expected_alias
            or meta_run_id == run_id
        )
        if not matches:
            continue

        if req_id:
            seen_request_ids.add(req_id)

        p_tok = int(entry.get("prompt_tokens") or entry.get("input_tokens") or 0)
        c_tok = int(entry.get("completion_tokens") or entry.get("output_tokens") or 0)
        in_tokens += max(0, p_tok)
        out_tokens += max(0, c_tok)
        raw_sp = entry.get("spend")
        if raw_sp is not None:
            try:
                total_spend += Decimal(str(raw_sp))
            except Exception:
                pass

    return in_tokens, out_tokens, total_spend


def _extract_context_stats_from_logs(
    logs_payload: Any,
    *,
    expected_alias: str,
    virtual_key: str,
    token_hashes: set[str],
    meta_id_key: str = "run_id",
    meta_id_val: str = "",
) -> tuple[int, int, int]:
    """Return `(latest_turn_tokens, peak_turn_tokens, turn_count)` from LiteLLM `/spend/logs`."""
    entries: list[dict[str, Any]] = []
    if isinstance(logs_payload, list):
        entries = [x for x in logs_payload if isinstance(x, dict)]
    elif isinstance(logs_payload, dict):
        data = logs_payload.get("data") or logs_payload.get("logs") or []
        if isinstance(data, list):
            entries = [x for x in data if isinstance(x, dict)]

    seen_ids: set[str] = set()
    turn_tokens_list: list[tuple[str, int]] = []

    for idx, entry in enumerate(entries):
        req_id = str(
            entry.get("request_id")
            or entry.get("id")
            or entry.get("call_id")
            or ""
        ).strip()
        if req_id and req_id in seen_ids:
            continue

        entry_key = str(entry.get("api_key") or "").strip()
        meta = entry.get("metadata")
        if isinstance(meta, str):
            try:
                meta = json.loads(meta)
            except Exception:
                meta = {}
        meta_dict = meta if isinstance(meta, dict) else {}
        alias = str(
            meta_dict.get("user_api_key_alias")
            or entry.get("key_alias")
            or entry.get("api_key_alias")
            or ""
        ).strip()
        user_key_meta = meta_dict.get("user_api_key_metadata")
        matched_meta_id = str(
            (user_key_meta.get(meta_id_key) if isinstance(user_key_meta, dict) else None)
            or meta_dict.get(meta_id_key)
            or ""
        ).strip()

        matches = (
            (entry_key and (entry_key == virtual_key or entry_key in token_hashes))
            or (expected_alias and alias == expected_alias)
            or (meta_id_val and matched_meta_id == meta_id_val)
        )
        if not matches:
            continue

        if req_id:
            seen_ids.add(req_id)

        p_tok = max(0, int(entry.get("prompt_tokens") or entry.get("input_tokens") or 0))
        c_tok = max(0, int(entry.get("completion_tokens") or entry.get("output_tokens") or 0))
        turn_total = p_tok + c_tok
        if turn_total <= 0:
            continue
        sort_ts = str(
            entry.get("startTime")
            or entry.get("endTime")
            or entry.get("created_at")
            or f"{idx:08d}"
        )
        turn_tokens_list.append((sort_ts, turn_total))

    if not turn_tokens_list:
        return 0, 0, 0

    turn_tokens_list.sort(key=lambda item: item[0])
    latest_tok = turn_tokens_list[-1][1]
    peak_tok = max(t for _, t in turn_tokens_list)
    return latest_tok, peak_tok, len(turn_tokens_list)


_SESSION_CTX_CACHE: dict[str, tuple[float, dict[str, Any] | None]] = {}


async def resolve_session_context_window(
    ag_session: Any,
    *,
    backend: Backend | None = None,
    messages: list[Any] | None = None,
) -> dict[str, Any] | None:
    """Compute live/latest context window occupancy for an interactive `AgentSession` from real turn data."""
    sid = str(getattr(ag_session, "id", "") or "")
    now_mono = time.monotonic()
    cached = _SESSION_CTX_CACHE.get(sid)
    is_active = str(getattr(ag_session, "status", "")).lower() in ("starting", "running_turn", "idle") and bool(
        getattr(ag_session, "container_id", None)
    )
    if cached and (now_mono - cached[0]) < (3.0 if is_active else 15.0):
        return cached[1]

    max_tokens = resolve_model_context_limit(
        getattr(ag_session, "model", None),
        backend=backend,
        runtime=str(getattr(ag_session, "runtime", "") or ""),
    )
    if max_tokens is None:
        return None

    latest_tok = 0
    peak_tok = 0
    turns = 0

    if messages:
        for m in messages:
            m_in = int(getattr(m, "input_tokens", 0) if not isinstance(m, dict) else (m.get("input_tokens") or 0))
            m_out = int(getattr(m, "output_tokens", 0) if not isinstance(m, dict) else (m.get("output_tokens") or 0))
            if m_in + m_out > 0:
                turns += 1
                latest_tok = m_in + m_out
                peak_tok = max(peak_tok, latest_tok)

    if sid and is_active:
        vkey = derive_session_virtual_key(sid)
        vhash = hashlib.sha256(vkey.encode("utf-8")).hexdigest()
        token_hashes = {vhash}
        base_url = settings.LITELLM_URL.rstrip("/")
        try:
            async with httpx.AsyncClient(timeout=2.5) as client:
                logs_resp = await client.get(
                    f"{base_url}/spend/logs",
                    params={"api_key": vhash, "summarize": "false"},
                    headers=_litellm_headers(),
                )
                if logs_resp.status_code == 200:
                    l_tok, p_tok, t_cnt = _extract_context_stats_from_logs(
                        logs_resp.json(),
                        expected_alias=f"session:{sid}",
                        virtual_key=vkey,
                        token_hashes=token_hashes,
                        meta_id_key="agent_session_id",
                        meta_id_val=sid,
                    )
                    if l_tok > 0:
                        latest_tok = l_tok
                        peak_tok = max(peak_tok, p_tok)
                        turns = max(turns, t_cnt)
        except Exception:
            pass

    info = build_context_window_info(
        used_tokens=latest_tok,
        peak_tokens=peak_tok,
        max_tokens=max_tokens,
        turns=turns,
    )
    if sid:
        _SESSION_CTX_CACHE[sid] = (now_mono, info)
    return info


async def finalize_run_litellm_key(
    session: AsyncSession,
    run: Run,
    seed: Seed,
    project: Project,
    *,
    delete_key: bool = True,
) -> Decimal:
    """Read spend & token usage from LiteLLM, update `run` and `seed.spent`, and optionally delete key."""
    virtual_key = derive_litellm_virtual_key(run.id)
    expected_hash = hashlib.sha256(virtual_key.encode("utf-8")).hexdigest()
    token_hashes: set[str] = {expected_hash}
    base_url = settings.LITELLM_URL.rstrip("/")
    reported_spend: Decimal | None = None
    reported_in_tokens = 0
    reported_out_tokens = 0
    latest_ctx_tokens = 0
    peak_ctx_tokens = 0
    ctx_turns = 0

    try:
        async with httpx.AsyncClient(timeout=8.0) as client:
            info_resp = await client.get(
                f"{base_url}/key/info",
                params={"key": virtual_key},
                headers=_litellm_headers(),
            )
            if info_resp.status_code == 200 and isinstance(info_resp.json(), dict):
                body = info_resp.json()
                info_obj = body.get("info") if isinstance(body.get("info"), dict) else body
                raw_spend = info_obj.get("spend")
                if raw_spend is not None:
                    reported_spend = Decimal(str(raw_spend)).quantize(Decimal("0.0001"))
                tok_id = str(info_obj.get("token") or body.get("token") or "").strip()
                if tok_id:
                    token_hashes.add(tok_id)
                info_in = int(
                    info_obj.get("prompt_tokens")
                    or info_obj.get("input_tokens")
                    or 0
                )
                info_out = int(
                    info_obj.get("completion_tokens")
                    or info_obj.get("output_tokens")
                    or 0
                )
                if info_in > 0 or info_out > 0:
                    reported_in_tokens = max(reported_in_tokens, info_in)
                    reported_out_tokens = max(reported_out_tokens, info_out)

            # Query LiteLLM /spend/logs to capture prompt_tokens and completion_tokens
            seen_req_ids: set[str] = set()
            for candidate_key in [*token_hashes, virtual_key]:
                logs_resp = await client.get(
                    f"{base_url}/spend/logs",
                    params={"api_key": candidate_key, "summarize": "false"},
                    headers=_litellm_headers(),
                )
                if logs_resp.status_code == 200:
                    logs_json = logs_resp.json()
                    in_t, out_t, sp_t = _extract_tokens_and_spend_from_logs(
                        logs_json,
                        run_id=run.id,
                        virtual_key=virtual_key,
                        token_hashes=token_hashes,
                        seen_request_ids=seen_req_ids,
                    )
                    l_ctx, p_ctx, t_cnt = _extract_context_stats_from_logs(
                        logs_json,
                        expected_alias=f"run:{run.id}",
                        virtual_key=virtual_key,
                        token_hashes=token_hashes,
                        meta_id_key="run_id",
                        meta_id_val=run.id,
                    )
                    if l_ctx > 0:
                        latest_ctx_tokens = l_ctx
                        peak_ctx_tokens = max(peak_ctx_tokens, p_ctx)
                        ctx_turns = max(ctx_turns, t_cnt)
                    reported_in_tokens += in_t
                    reported_out_tokens += out_t
                    if sp_t > 0:
                        quant_sp = sp_t.quantize(Decimal("0.0001"))
                        if reported_spend is None or quant_sp > reported_spend:
                            reported_spend = quant_sp
                if reported_in_tokens > 0 or reported_out_tokens > 0:
                    break
    except Exception as exc:
        logger.debug("LiteLLM finalize_run_litellm_key(%s) skipped: %s", run.id, exc)
    finally:
        if delete_key:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    await client.post(
                        f"{base_url}/key/delete",
                        headers=_litellm_headers(),
                        json={"keys": [virtual_key], "key_aliases": [f"run:{run.id}"]},
                    )
            except Exception as exc:
                logger.debug("LiteLLM finalize key delete(%s) skipped: %s", run.id, exc)

    if reported_in_tokens > 0 or reported_out_tokens > 0:
        run.input_tokens = reported_in_tokens
        run.output_tokens = reported_out_tokens
        run.updated_at = datetime.now(UTC)

    if latest_ctx_tokens > 0:
        models_backend: Backend | None = None
        try:
            models_backend, _ = await resolve_models_backend_for_run(
                session, run, project, allow_disabled=True
            )
        except TypeError:
            models_backend, _ = await resolve_models_backend_for_run(session, run, project)
        except Exception:
            models_backend = None
        run.output = merge_run_context_window(
            run.output,
            used_tokens=latest_ctx_tokens,
            peak_tokens=max(peak_ctx_tokens, latest_ctx_tokens),
            turns=ctx_turns,
            model=run.model,
            runtime=str(run.runtime or ""),
            backend=models_backend,
        )

    prev_cost = Decimal(str(run.cost or 0))
    if reported_spend is not None and reported_spend > prev_cost:
        delta = reported_spend - prev_cost
        run.cost = reported_spend
        now = datetime.now(UTC)
        await session.execute(
            update(Seed)
            .where(col(Seed.id) == seed.id)
            .values(
                spent=col(Seed.spent) + delta,
                updated_at=now,
            )
        )
        await session.flush()
        await session.refresh(seed)

        _, blocked_reason, activity_data = await _compute_remaining_budget(
            session, seed, project
        )
        if blocked_reason is not None and seed.status not in (
            SeedStatus.BLOCKED,
            SeedStatus.MERGED,
            SeedStatus.CLOSED,
        ):
            seed.status = SeedStatus.BLOCKED
            seed.blocked_reason = blocked_reason
            seed.updated_at = now
            if activity_data is not None:
                record(
                    session,
                    type=ActivityType.BUDGET_BLOCKED,
                    actor_type=ActorType.SYSTEM,
                    actor_id=None,
                    project_id=project.id,
                    seed_id=seed.id,
                    data=activity_data,
                )

    return Decimal(str(run.cost or 0))
