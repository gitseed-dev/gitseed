"""Encrypted mobile/wearable device push registration and HMAC-signed push relay dispatcher.

Bridges self-hosted GitSeed control planes with official GitSeed Mobile & Wearable clients
(iOS, iPadOS, watchOS, macOS, Android, Wear OS) via an optional APNs/FCM push relay
(`settings.MOBILE_PUSH_RELAY_URL`) while keeping raw device push tokens encrypted at rest
and never exposing them in API responses.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
from datetime import UTC, datetime
from typing import Any

import httpx

from gitseed_core.config import settings
from gitseed_core.db import get_redis
from gitseed_core.security import decrypt_secrets, encrypt_secrets
from gitseed_core.services.errors import Invalid

logger = logging.getLogger("uvicorn.error")

_PUSH_DEVICES_REDIS_KEY = "gitseed:push:devices"
_ALLOWED_PLATFORMS = {"ios", "ipados", "watchos", "macos", "android", "wearos"}

# High-signal SDLC triage events that warrant waking a mobile or wearable device
PUSH_WORTHY_EVENT_TYPES = {
    "seed.blocked",
    "seed.stage_changed",
    "seed.merged",
    "run.failed",
    "approval.created",
    "comment.created",
}

# In-process fallback store when Valkey is unreachable (e.g. unit tests)
_MEMORY_PUSH_DEVICES: dict[str, dict[str, Any]] = {}


def _derive_device_id(user_id: str, platform: str, push_token: str) -> str:
    digest = hashlib.sha256(
        f"{user_id}:{platform}:{push_token}".encode()
    ).hexdigest()[:16]
    return f"pdev_{digest}"


async def _load_all_records() -> dict[str, dict[str, Any]]:
    records: dict[str, dict[str, Any]] = dict(_MEMORY_PUSH_DEVICES)
    try:
        redis = get_redis()
        raw = await redis.get(_PUSH_DEVICES_REDIS_KEY)
        if raw:
            parsed = json.loads(str(raw))
            if isinstance(parsed, dict):
                for k, v in parsed.items():
                    if isinstance(v, dict):
                        records[str(k)] = v
    except Exception:
        pass
    return records


async def _save_all_records(records: dict[str, dict[str, Any]]) -> None:
    _MEMORY_PUSH_DEVICES.clear()
    _MEMORY_PUSH_DEVICES.update(records)
    try:
        redis = get_redis()
        await redis.set(_PUSH_DEVICES_REDIS_KEY, json.dumps(records))
    except Exception:
        pass


def _to_public_response(record: dict[str, Any]) -> dict[str, Any]:
    reg_str = str(record.get("registered_at") or datetime.now(UTC).isoformat())
    return {
        "device_id": str(record["device_id"]),
        "platform": str(record["platform"]),
        "device_name": record.get("device_name"),
        "app_bundle_id": record.get("app_bundle_id"),
        "registered_at": datetime.fromisoformat(reg_str.replace("Z", "+00:00")),
    }


async def register_push_device(
    user_id: str,
    *,
    platform: str,
    push_token: str,
    device_name: str | None = None,
    app_bundle_id: str | None = None,
) -> dict[str, Any]:
    """Register or refresh an encrypted APNs/FCM push token for a user's mobile/wearable device."""
    norm_platform = (platform or "").strip().lower()
    if norm_platform not in _ALLOWED_PLATFORMS:
        raise Invalid(
            f"Unsupported push platform '{platform}'. Allowed: {', '.join(sorted(_ALLOWED_PLATFORMS))}"
        )
    clean_token = (push_token or "").strip()
    if len(clean_token) < 8:
        raise Invalid("push_token must be at least 8 characters")

    device_id = _derive_device_id(user_id, norm_platform, clean_token)
    encrypted_bytes = encrypt_secrets({"push_token": clean_token})
    now_iso = datetime.now(UTC).isoformat()

    record: dict[str, Any] = {
        "device_id": device_id,
        "user_id": user_id,
        "platform": norm_platform,
        "device_name": (device_name or "").strip() or None,
        "app_bundle_id": (app_bundle_id or "").strip() or None,
        "token_encrypted": base64.b64encode(encrypted_bytes).decode("ascii"),
        "registered_at": now_iso,
    }
    records = await _load_all_records()
    records[device_id] = record
    await _save_all_records(records)
    return _to_public_response(record)


async def list_push_devices(user_id: str) -> list[dict[str, Any]]:
    """List sanitized push device registrations belonging to `user_id`."""
    records = await _load_all_records()
    return [
        _to_public_response(rec)
        for rec in records.values()
        if rec.get("user_id") == user_id
    ]


async def unregister_push_device(user_id: str, device_id: str) -> bool:
    """Remove a registered push device owned by `user_id`."""
    records = await _load_all_records()
    rec = records.get(device_id)
    if rec is None or rec.get("user_id") != user_id:
        return False
    records.pop(device_id, None)
    await _save_all_records(records)
    return True


def should_push_notify_event(envelope: dict[str, Any]) -> bool:
    """Return True if an activity/SSE event envelope should wake mobile/wearable devices."""
    ev_type = str(envelope.get("type") or "").strip().lower()
    return ev_type in PUSH_WORTHY_EVENT_TYPES


async def dispatch_push_relay(envelope: dict[str, Any]) -> bool:
    """Best-effort dispatch of a push-worthy event to `settings.MOBILE_PUSH_RELAY_URL`."""
    relay_url = (getattr(settings, "MOBILE_PUSH_RELAY_URL", "") or "").strip()
    if not relay_url or not should_push_notify_event(envelope):
        return False

    records = await _load_all_records()
    if not records:
        return False

    targets: list[dict[str, str | None]] = []
    for rec in records.values():
        enc_b64 = rec.get("token_encrypted")
        if not isinstance(enc_b64, str) or not enc_b64:
            continue
        try:
            decrypted = decrypt_secrets(base64.b64decode(enc_b64))
            tok = decrypted.get("push_token")
            if tok:
                targets.append(
                    {
                        "device_id": str(rec.get("device_id") or ""),
                        "platform": str(rec.get("platform") or ""),
                        "push_token": tok,
                        "app_bundle_id": rec.get("app_bundle_id"),
                    }
                )
        except Exception:
            continue

    if not targets:
        return False

    signing_secret = (
        getattr(settings, "MOBILE_PUSH_RELAY_SECRET", "").strip()
        or settings.GITSEED_SECRET_KEY
    )
    payload = {
        "event_id": envelope.get("id"),
        "type": envelope.get("type"),
        "project_id": envelope.get("project_id"),
        "seed_id": envelope.get("seed_id"),
        "occurred_at": envelope.get("occurred_at"),
        "data": envelope.get("data") or {},
        "devices": targets,
    }
    body_bytes = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
    sig_hex = hmac.new(
        signing_secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                relay_url,
                content=body_bytes,
                headers={
                    "Content-Type": "application/json",
                    "X-GitSeed-Event": str(envelope.get("type") or ""),
                    "X-GitSeed-Signature-256": f"sha256={sig_hex}",
                },
            )
            return 200 <= resp.status_code < 300
    except Exception as exc:
        logger.debug("Mobile push relay dispatch skipped: %s", exc)
        return False

