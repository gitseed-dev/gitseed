from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import tarfile
from datetime import UTC, datetime
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote, urlsplit

from gitseed_common.enums import (
    ActivityType,
    ActorType,
    BackendHealth,
    BackendType,
)
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets, encrypt_secrets
from gitseed_core.services.activity import record
from gitseed_core.services.backends import (
    _DEFAULT_CLOUD_MODELS,
    _merge_discovered_models,
    get_backend,
    maybe_auto_provision_sdlc_agents,
)
from gitseed_core.services.errors import Invalid
from gitseed_core.services.scheduler import (
    _docker_client_for_backend,
    fetch_docker_container_logs,
    inspect_docker_container_state,
    stop_and_remove_docker_container,
)

logger = logging.getLogger("uvicorn.error")

_OSC8_URL_RE = re.compile(r"\x1b\]8;[^;]*;(https?://[^\x07\x1b]+)(?:\x07|\x1b\\)")
_OSC_ESCAPE_RE = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_DCS_ESCAPE_RE = re.compile(r"\x1b[P_^][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_ESCAPE_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~]|\([A-Z0-9])")
_URL_RE = re.compile(r"https?://[^\s\"'<>)\]\x00-\x1f\x7f]+")
_DEVICE_CODE_RE = re.compile(
    r"(?:user\s*code|device\s*code|one-time\s*code|verification\s*code)[:\s]+([A-Z0-9]{4,8}-[A-Z0-9]{4,8})",
    re.IGNORECASE,
)


def _strip_ansi(text: str) -> str:
    cleaned = _OSC_ESCAPE_RE.sub("", text or "")
    cleaned = _DCS_ESCAPE_RE.sub("", cleaned)
    cleaned = _ANSI_ESCAPE_RE.sub("", cleaned)
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse long horizontal box-drawing divider lines and wide-PTY padding
    cleaned = re.sub(r"─{40,}", "─" * 40, cleaned)
    cleaned = re.sub(r"[ \t]{16,}([│|])", r"  \1", cleaned)
    cleaned = re.sub(r"[ \t]+\n", "\n", cleaned)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned)
    return cleaned


def _extract_auth_url_and_code(log_text: str) -> tuple[str | None, str | None]:
    osc_urls = _OSC8_URL_RE.findall(log_text or "")
    cleaned = _strip_ansi(log_text)
    urls = [*osc_urls, *_URL_RE.findall(cleaned)]
    chosen_url: str | None = None
    for raw_url in urls:
        u = raw_url.rstrip(".,;:")
        lower = u.lower()
        if any(
            kw in lower
            for kw in (
                "oauth",
                "authorize",
                "login",
                "auth",
                "accounts.google.com",
                "claude.ai",
                "anthropic.com",
                "openai.com",
                "x.ai",
                "device",
            )
        ):
            chosen_url = u
            break
    if not chosen_url and urls:
        for raw_url in urls:
            u = raw_url.rstrip(".,;:")
            if not u.startswith(("http://localhost", "http://127.0.0.1")):
                chosen_url = u
                break

    code_match = _DEVICE_CODE_RE.search(cleaned)
    user_code = code_match.group(1) if code_match else None
    return chosen_url, user_code


def _container_name_for_backend(backend_id: str) -> str:
    safe_id = re.sub(r"[^a-zA-Z0-9_.-]", "-", backend_id)[:40]
    return f"gitseed-cli-auth-{safe_id}"


async def _select_docker_compute_backend(session: AsyncSession) -> Backend:
    backends = (
        await session.execute(
            select(Backend)
            .where(
                Backend.type == BackendType.COMPUTE,
                col(Backend.enabled).is_(True),
            )
            .order_by(col(Backend.created_at).asc())
        )
    ).scalars().all()
    for b in backends:
        if b.health == BackendHealth.UNREACHABLE:
            continue
        if b.provider.lower() == "docker":
            return b
    if backends:
        return backends[0]
    return Backend(
        id="default-local-docker",
        name="Local Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        endpoint="unix:///var/run/docker.sock",
        enabled=True,
        health=BackendHealth.HEALTHY,
    )


def _build_pty_auth_script(provider: str) -> str:
    """Return a self-contained Python script that drives the provider CLI inside a real PTY."""
    return f"""
import fcntl, os, pty, select, shutil, struct, subprocess, sys, termios, time, json
from pathlib import Path

provider = {provider!r}
home = Path("/home/runner")
fifo_path = "/tmp/.gitseed_stdin"
if not os.path.exists(fifo_path):
    os.mkfifo(fifo_path)

env = dict(os.environ)
env["HOME"] = str(home)
env["TERM"] = "xterm-256color"
env["BROWSER"] = "echo"
env["NO_BROWSER"] = "true"
env["DISPLAY"] = ""

if provider in ("gemini", "antigravity"):
    gemini_dir = home / ".gemini"
    gemini_dir.mkdir(parents=True, exist_ok=True)
    trusted_folders_path = gemini_dir / "trustedFolders.json"
    trusted_folders = {{
        str(home): "TRUST_FOLDER",
        str(home.parent): "TRUST_FOLDER",
        "/workspace": "TRUST_FOLDER",
    }}
    trusted_folders_path.write_text(json.dumps(trusted_folders, indent=2))
    default_gem_cfg = {{
        "selectedAuthType": "oauth-personal",
        "security": {{
            "auth": {{"selectedType": "oauth-personal"}},
            "folderTrust": {{"enabled": False}},
        }},
        "theme": "Default",
        "ui": {{
            "theme": "Default",
            "autoThemeSwitching": False,
        }},
    }}
    settings_path = gemini_dir / "settings.json"
    settings_path.write_text(json.dumps(default_gem_cfg, indent=2))
if provider == "antigravity":
    agy_dir = home / ".gemini" / "antigravity-cli"
    agy_dir.mkdir(parents=True, exist_ok=True)
    default_agy_cfg = {{
        "onboardingComplete": True,
        "colorScheme": "dark",
        "telemetryEnabled": False,
        "trustedWorkspaces": [str(home), "/workspace"],
    }}
    agy_settings_path = agy_dir / "settings.json"
    if not agy_settings_path.exists():
        agy_settings_path.write_text(json.dumps(default_agy_cfg, indent=2))
elif provider == "anthropic":
    claude_cfg = home / ".claude.json"
    if not claude_cfg.exists():
        claude_cfg.write_text(json.dumps({{"hasCompletedOnboarding": True, "theme": "dark"}}, indent=2))

if provider == "antigravity":
    if shutil.which("agy"):
        cmd = ["agy"]
    else:
        cmd = ["gemini"]
elif provider == "gemini":
    cmd = ["gemini"]
elif provider == "anthropic":
    cmd = ["claude", "setup-token"]
elif provider == "openai":
    cmd = ["codex", "login", "--device-auth"]
elif provider == "xai":
    if shutil.which("grok"):
        cmd = ["grok", "login"]
    else:
        print("Grok CLI binary not installed in runner container. Paste an OAuth token or API key directly.", flush=True)
        cmd = ["sleep", "300"]
else:
    cmd = ["sleep", "300"]

print(f"Starting CLI authentication for {{provider}} ({{' '.join(cmd)}})...", flush=True)

def spawn_in_pty(argv):
    m_fd, s_fd = pty.openpty()
    try:
        fcntl.ioctl(s_fd, termios.TIOCSWINSZ, struct.pack("HHHH", 60, 1000, 0, 0))
    except Exception:
        pass
    p = subprocess.Popen(
        argv,
        stdin=s_fd,
        stdout=s_fd,
        stderr=s_fd,
        env=env,
        close_fds=True,
    )
    os.close(s_fd)
    return m_fd, p

master_fd, proc = spawn_in_pty(cmd)
fifo_fd = os.open(fifo_path, os.O_RDONLY | os.O_NONBLOCK)
start_ts = time.time()
sent_folder_trust_enter = False
sent_agy_oauth_enter = False
accum_text = ""

try:
    while time.time() - start_ts < 600:
        rlist, _, _ = select.select([master_fd, fifo_fd], [], [], 0.25)
        if master_fd in rlist:
            try:
                data = os.read(master_fd, 4096)
                if data:
                    sys.stdout.buffer.write(data)
                    sys.stdout.buffer.flush()
                    accum_text += data.decode("utf-8", errors="ignore")
                    if (
                        provider in ("antigravity", "gemini")
                        and "https://" not in accum_text
                        and "Enter the authorization code" not in accum_text
                    ):
                        if not sent_folder_trust_enter and (
                            "Trust parent folder" in accum_text
                            or "Do you trust this folder" in accum_text
                        ):
                            sent_folder_trust_enter = True
                            time.sleep(0.2)
                            os.write(master_fd, b"\\r")
                        elif (
                            cmd == ["agy"]
                            and not sent_agy_oauth_enter
                            and "Select login method" in accum_text
                        ):
                            sent_agy_oauth_enter = True
                            time.sleep(0.2)
                            os.write(master_fd, b"\\r")
            except OSError:
                break
        if fifo_fd in rlist:
            try:
                user_in = os.read(fifo_fd, 4096)
                if user_in:
                    text = user_in.decode("utf-8", errors="ignore").strip()
                    if text:
                        os.write(master_fd, text.encode("utf-8"))
                        time.sleep(0.15)
                        os.write(master_fd, b"\\r")
            except OSError:
                pass
        if proc.poll() is not None:
            if provider == "antigravity" and proc.returncode != 0 and cmd[0] == "agy" and shutil.which("gemini"):
                print("\\nFalling back to Google OAuth sign-in flow...", flush=True)
                cmd = ["gemini"]
                master_fd, proc = spawn_in_pty(cmd)
                continue
            if provider == "openai" and proc.returncode != 0 and "--device-auth" in cmd:
                cmd = ["codex", "login"]
                master_fd, proc = spawn_in_pty(cmd)
                continue
            if provider == "anthropic" and proc.returncode != 0 and cmd == ["claude", "setup-token"]:
                cmd = ["claude", "login"]
                master_fd, proc = spawn_in_pty(cmd)
                continue
            break
    # Keep container briefly alive so Core can harvest credentials from /home/runner
    time.sleep(15)
finally:
    try:
        os.close(fifo_fd)
    except OSError:
        pass
    try:
        os.close(master_fd)
    except OSError:
        pass
"""


MAX_CLI_AUTH_FILE_BYTES = 64 * 1024

ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME: dict[str, frozenset[str]] = {
    "claude-code": frozenset(
        {
            ".claude/.credentials.json",
            ".claude/credentials.json",
        }
    ),
    "codex": frozenset(
        {
            ".codex/auth.json",
        }
    ),
    "gemini": frozenset(
        {
            ".gemini/oauth_creds.json",
            ".gemini/google_accounts.json",
            ".gemini/antigravity-cli/antigravity-oauth-token",
            ".gemini/antigravity-cli/keyring/antigravity-oauth-token",
            ".gemini/antigravity-cli/installation_id",
            ".gemini/antigravity-cli/jetski_state.pbtxt",
            ".gemini/config/projects/default-cli-project.json",
            ".gemini/config/config.json",
        }
    ),
    "antigravity": frozenset(
        {
            ".gemini/jetski-standalone-oauth-token",
            ".gemini/antigravity-cli/antigravity-oauth-token",
            ".gemini/antigravity-cli/keyring/antigravity-oauth-token",
            ".gemini/antigravity-cli/installation_id",
            ".gemini/antigravity-cli/jetski_state.pbtxt",
            ".config/Antigravity/User/globalStorage/jetski_state.pbtxt",
            ".gemini/antigravity-cli/auth.json",
            ".gemini/antigravity-cli/acp-auth-state.json",
            ".gemini/antigravity-cli/oauth_creds.json",
            ".gemini/config/projects/default-cli-project.json",
            ".gemini/config/config.json",
            ".gemini/oauth_creds.json",
            ".gemini/google_accounts.json",
            ".config/antigravity/oauth_creds.json",
            ".config/antigravity/auth.json",
        }
    ),
    "grok-build": frozenset(
        {
            ".grok/credentials.json",
            ".grok/auth.json",
            ".config/grok/credentials.json",
            ".config/grok/auth.json",
        }
    ),
    "opencode": frozenset(
        {
            ".local/share/opencode/auth.json",
            ".config/opencode/auth.json",
        }
    ),
}

ALLOWED_CLI_AUTH_PATHS_BY_PROVIDER: dict[str, frozenset[str]] = {
    "anthropic": ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["claude-code"]
    | ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"],
    "openai": ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["codex"]
    | ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"],
    "gemini": ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["gemini"]
    | ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"],
    "antigravity": ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["antigravity"]
    | ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"],
    "xai": ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["grok-build"]
    | ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"],
}

ALL_ALLOWED_CLI_AUTH_PATHS: frozenset[str] = frozenset().union(
    *ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME.values()
)


def normalize_cli_auth_rel_path(rel_path: str) -> str | None:
    """Normalize a relative $HOME credential path and reject traversal or invalid segments."""
    if not isinstance(rel_path, str):
        return None
    cleaned = rel_path.strip().lstrip("/\\")
    if not cleaned or "\\" in cleaned or "\x00" in cleaned:
        return None
    posix = PurePosixPath(cleaned)
    parts = posix.parts
    if not parts or any(p in ("", ".", "..") for p in parts):
        return None
    norm = "/".join(parts)
    if not norm.startswith("."):
        norm = f".{norm}"
    return norm


_NON_JSON_CLI_AUTH_PATHS: frozenset[str] = frozenset(
    {
        ".gemini/jetski-standalone-oauth-token",
        ".gemini/antigravity-cli/antigravity-oauth-token",
        ".gemini/antigravity-cli/keyring/antigravity-oauth-token",
        ".gemini/antigravity-cli/installation_id",
        ".gemini/antigravity-cli/jetski_state.pbtxt",
        ".config/Antigravity/User/globalStorage/jetski_state.pbtxt",
    }
)


def validate_and_filter_cli_auth_bundle(
    bundle: dict[str, Any],
    *,
    runtime: str | None = None,
    provider: str | None = None,
    strict: bool = True,
) -> dict[str, str]:
    """Validate a CLI auth credential bundle against the strict per-runtime/provider allowlist, JSON format, and 64KB cap."""
    if not isinstance(bundle, dict):
        if strict:
            raise Invalid("cli_auth_bundle must be a JSON object mapping paths to JSON strings")
        return {}

    if runtime:
        rt_key = runtime.strip().lower()
        allowed_paths = ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME.get(rt_key, frozenset())
    elif provider:
        prov_key = provider.strip().lower()
        allowed_paths = ALLOWED_CLI_AUTH_PATHS_BY_PROVIDER.get(
            prov_key, ALL_ALLOWED_CLI_AUTH_PATHS
        )
    else:
        allowed_paths = ALL_ALLOWED_CLI_AUTH_PATHS

    validated: dict[str, str] = {}
    for raw_path, raw_content in bundle.items():
        norm_path = normalize_cli_auth_rel_path(str(raw_path))
        if not norm_path or norm_path not in allowed_paths:
            if strict:
                raise Invalid(
                    f"Disallowed CLI auth credential path '{raw_path}' for runtime={runtime or provider or 'any'}"
                )
            continue
        if not isinstance(raw_content, str):
            if strict:
                raise Invalid(f"Credential content for '{norm_path}' must be a string")
            continue
        stripped = raw_content.strip()
        if not stripped:
            if strict:
                raise Invalid(f"Credential content for '{norm_path}' cannot be empty")
            continue
        if len(stripped.encode("utf-8")) > MAX_CLI_AUTH_FILE_BYTES:
            if strict:
                raise Invalid(
                    f"Credential file '{norm_path}' exceeds maximum size of {MAX_CLI_AUTH_FILE_BYTES} bytes"
                )
            continue
        if norm_path not in _NON_JSON_CLI_AUTH_PATHS:
            try:
                parsed = json.loads(stripped)
            except Exception as exc:
                if strict:
                    raise Invalid(
                        f"Credential file '{norm_path}' must contain valid JSON"
                    ) from exc
                continue
            if not isinstance(parsed, dict):
                if strict:
                    raise Invalid(
                        f"Credential file '{norm_path}' must contain a JSON object"
                    )
                continue
        validated[norm_path] = stripped
    return validated


_CREDENTIAL_ARCHIVE_ROOTS: dict[str, tuple[str, ...]] = {
    "antigravity": (".gemini", ".config/antigravity", ".config/Antigravity"),
    "gemini": (".gemini",),
    "anthropic": (".claude",),
    "openai": (".codex",),
    "xai": (".grok", ".config/grok"),
}

_EXCLUDED_ARCHIVE_SEGMENTS = (
    "/cache/",
    "/builtin/",
    "/implicit/",
    "/log/",
    "/logs/",
    "/crashes/",
    "/updater/",
    "/completions/",
    "/docs/",
    "/downloads/",
    "/bin/",
)


def _is_excluded_archive_member(rel_path: str) -> bool:
    wrapped = f"/{rel_path.strip('/')}/"
    if any(seg in wrapped for seg in _EXCLUDED_ARCHIVE_SEGMENTS):
        return True
    lower = rel_path.lower()
    return lower.endswith((".log", ".db", ".db-shm", ".db-wal", ".lock", ".fish"))


def _has_verified_provider_credentials(provider: str, bundle: dict[str, str]) -> bool:
    """Return True only when an actual OAuth token or credential file has been written."""
    for rel_path, content in bundle.items():
        norm_key = rel_path.lstrip("/")
        if not norm_key.startswith("."):
            norm_key = f".{norm_key}"
        lower_key = norm_key.lower()
        lower_val = content.lower()

        if provider == "antigravity":
            if (
                lower_key.endswith("jetski-standalone-oauth-token")
                or lower_key.endswith("-oauth-token")
                or "/keyring/" in lower_key
                or lower_key.endswith("oauth_creds.json")
            ):
                return True
            if '"refresh_token"' in lower_val or '"access_token"' in lower_val:
                return True
        elif provider == "gemini":
            if lower_key.endswith("oauth_creds.json"):
                return True
            if '"refresh_token"' in lower_val or '"access_token"' in lower_val:
                return True
        elif provider == "anthropic":
            if lower_key in (".claude/.credentials.json", ".claude/credentials.json"):
                return True
            if lower_key == ".claude.json" and (
                "oauthaccount" in lower_val or "primaryapikey" in lower_val
            ):
                return True
            if "sk-ant-oat" in lower_val or '"refresh_token"' in lower_val or '"access_token"' in lower_val:
                return True
        elif provider == "openai":
            if lower_key.endswith("auth.json") and (
                '"access_token"' in lower_val
                or '"refresh_token"' in lower_val
                or '"id_token"' in lower_val
                or '"openai_api_key"' in lower_val
            ):
                return True
        elif provider == "xai":
            if lower_key.endswith(("credentials.json", "auth.json")) or (
                '"refresh_token"' in lower_val or '"access_token"' in lower_val
            ):
                return True
    return False


async def _harvest_credentials_from_container(
    compute_backend: Backend,
    container_name: str,
    provider: str,
) -> dict[str, str]:
    """Extract credential files from /home/runner inside the auth container."""
    roots = _CREDENTIAL_ARCHIVE_ROOTS.get(provider, ())
    if not roots:
        return {}

    bundle: dict[str, str] = {}
    try:
        client, base_url = _docker_client_for_backend(compute_backend)
    except Exception:
        return {}

    async with client:
        for root_rel in roots:
            full_container_path = f"/home/runner/{root_rel}"
            try:
                resp = await client.get(
                    f"{base_url}/containers/{quote(container_name, safe='')}/archive",
                    params={"path": full_container_path},
                    timeout=8.0,
                )
                if resp.status_code != 200:
                    continue
                parent_prefix = str(PurePosixPath(root_rel).parent)
                with tarfile.open(fileobj=io.BytesIO(resp.content), mode="r:*") as tf:
                    for member in tf.getmembers():
                        if not member.isfile() or member.size <= 0 or member.size > 512_000:
                            continue
                        member_name = re.sub(r"^\./+", "", member.name).lstrip("/")
                        if not member_name:
                            continue
                        rel_path = (
                            f"{parent_prefix}/{member_name}"
                            if parent_prefix and parent_prefix != "."
                            else member_name
                        )
                        if not rel_path.startswith("."):
                            rel_path = f".{rel_path}"
                        if _is_excluded_archive_member(rel_path):
                            continue
                        extracted = tf.extractfile(member)
                        if extracted is None:
                            continue
                        content = extracted.read().decode("utf-8", errors="ignore").strip()
                        if not content:
                            continue
                        bundle[rel_path] = content
            except Exception:
                continue

    if not _has_verified_provider_credentials(provider, bundle):
        return {}

    # Mirror .gemini/oauth_creds.json into .gemini/antigravity-cli/oauth_creds.json for Antigravity compatibility
    if provider == "antigravity" and ".gemini/oauth_creds.json" in bundle:
        bundle.setdefault(
            ".gemini/antigravity-cli/oauth_creds.json",
            bundle[".gemini/oauth_creds.json"],
        )

    bundle = validate_and_filter_cli_auth_bundle(
        bundle, provider=provider, strict=False
    )
    if not _has_verified_provider_credentials(provider, bundle):
        return {}

    return bundle


async def _persist_authenticated_bundle(
    session: AsyncSession,
    admin: User,
    backend: Backend,
    *,
    bundle: dict[str, str] | None = None,
    oauth_token: str | None = None,
) -> Backend:
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    provider = backend.provider.lower()
    if bundle:
        validated_new = validate_and_filter_cli_auth_bundle(
            bundle, provider=provider, strict=True
        )
        existing_bundle: dict[str, str] = {}
        if secrets_map.get("cli_auth_bundle"):
            try:
                parsed = json.loads(secrets_map["cli_auth_bundle"])
                if isinstance(parsed, dict):
                    existing_bundle = validate_and_filter_cli_auth_bundle(
                        parsed, provider=provider, strict=False
                    )
            except Exception:
                pass
        existing_bundle.update(validated_new)
        secrets_map["cli_auth_bundle"] = json.dumps(existing_bundle)
    if oauth_token:
        secrets_map["oauth_token"] = oauth_token.strip()

    backend.secrets_encrypted = encrypt_secrets(secrets_map)
    now = datetime.now(UTC)

    cfg = dict(backend.config or {})
    if cfg.get("personal_mode_acknowledged") is False:
        raise Invalid(
            "Personal Mode (subscription CLI OAuth) requires explicit acknowledgement "
            "(personal_mode_acknowledged=true)."
        )
    cfg["auth_mode"] = "cli_oauth"
    cfg["personal_mode"] = True
    cfg["personal_mode_acknowledged"] = True
    cfg.setdefault("personal_mode_owner_user_id", admin.id)
    cfg.setdefault("personal_mode_acknowledged_at", now.isoformat())
    cfg["cli_auth_status"] = "authenticated"
    cfg["cli_auth_updated_at"] = now.isoformat()

    provider = backend.provider.lower()
    if provider in _DEFAULT_CLOUD_MODELS and not cfg.get("models"):
        merged_models = _merge_discovered_models(
            [],
            [],
            list(_DEFAULT_CLOUD_MODELS[provider]),
        )
        cfg["models"] = merged_models
        cfg["discovered_models"] = [
            m["id"] for m in merged_models if m.get("enabled", True)
        ]

    backend.config = cfg
    backend.health = BackendHealth.HEALTHY
    backend.health_checked_at = now
    backend.health_error = None
    backend.updated_at = now
    backend.updated_by = admin.id

    record(
        session,
        type=ActivityType.BACKEND_SECRET_UPDATED,
        actor_type=ActorType.USER,
        actor_id=admin.id,
        data={
            "backend_id": backend.id,
            "name": backend.name,
            "secret_keys": sorted(secrets_map.keys()),
            "auth_mode": "cli_oauth",
        },
    )
    await session.commit()
    await session.refresh(backend)
    await maybe_auto_provision_sdlc_agents(session, backend)
    return backend


async def start_cli_auth_session(
    session: AsyncSession,
    admin: User,
    backend_id: str,
) -> dict[str, Any]:
    """Spawn an ephemeral runner container to perform interactive vendor CLI OAuth login."""
    backend = await get_backend(session, backend_id)
    if backend.type != BackendType.INFERENCE:
        raise Invalid("CLI OAuth login is only supported for Inference backends")

    cfg = dict(backend.config or {})
    if cfg.get("personal_mode_acknowledged") is False:
        raise Invalid(
            "Personal Mode (subscription CLI OAuth) requires explicit acknowledgement "
            "(personal_mode_acknowledged=true)."
        )
    cfg["personal_mode"] = True
    cfg["personal_mode_acknowledged"] = True
    cfg.setdefault("personal_mode_owner_user_id", admin.id)
    cfg.setdefault("personal_mode_acknowledged_at", datetime.now(UTC).isoformat())
    backend.config = cfg

    compute_backend = await _select_docker_compute_backend(session)
    container_name = _container_name_for_backend(backend.id)

    # Clean up any prior auth container for this backend
    await stop_and_remove_docker_container(compute_backend, container_name)

    provider = backend.provider.lower()
    pty_script = _build_pty_auth_script(provider)

    client, base_url = _docker_client_for_backend(compute_backend)
    create_payload: dict[str, Any] = {
        "Image": settings.RUNNER_IMAGE,
        "Entrypoint": ["/app/.venv/bin/python", "-u", "-c", pty_script],
        "Cmd": [],
        "WorkingDir": "/home/runner",
        "Env": [
            "HOME=/home/runner",
            "TERM=xterm-256color",
            "BROWSER=echo",
            "NO_BROWSER=true",
        ],
        "Labels": {
            "gitseed.managed": "true",
            "gitseed.kind": "cli-auth",
            "gitseed.backend_id": backend.id,
            "gitseed.provider": provider,
        },
        "HostConfig": {
            "AutoRemove": False,
        },
    }

    async with client:
        create_resp = await client.post(
            f"{base_url}/containers/create",
            params={"name": container_name},
            json=create_payload,
            timeout=15.0,
        )
        if create_resp.status_code not in (200, 201):
            return {
                "status": "error",
                "auth_url": None,
                "user_code": None,
                "log_output": "",
                "authenticated": False,
                "error": f"Failed to create CLI auth helper container (HTTP {create_resp.status_code}): {create_resp.text[:240]}",
                "backend": backend,
            }
        start_resp = await client.post(
            f"{base_url}/containers/{quote(container_name, safe='')}/start",
            timeout=15.0,
        )
        if start_resp.status_code not in (200, 204):
            return {
                "status": "error",
                "auth_url": None,
                "user_code": None,
                "log_output": "",
                "authenticated": False,
                "error": f"Failed to start CLI auth helper container (HTTP {start_resp.status_code})",
                "backend": backend,
            }

    # Poll logs briefly (up to ~5 seconds) so the OAuth URL is ready on first response
    log_text = ""
    auth_url: str | None = None
    user_code: str | None = None
    for _ in range(10):
        await asyncio.sleep(0.5)
        raw_logs = await fetch_docker_container_logs(
            compute_backend, container_name, tail_lines=200
        )
        auth_url, user_code = _extract_auth_url_and_code(raw_logs or "")
        log_text = _strip_ansi(raw_logs or "")
        if auth_url:
            break

    return {
        "status": "awaiting_user",
        "auth_url": auth_url,
        "user_code": user_code,
        "log_output": log_text[-4000:],
        "authenticated": False,
        "error": None,
        "backend": backend,
    }


async def get_cli_auth_status(
    session: AsyncSession,
    admin: User,
    backend_id: str,
) -> dict[str, Any]:
    """Inspect active CLI auth container logs and harvest credentials if sign-in completed."""
    backend = await get_backend(session, backend_id)
    compute_backend = await _select_docker_compute_backend(session)
    container_name = _container_name_for_backend(backend.id)
    provider = backend.provider.lower()

    state = await inspect_docker_container_state(compute_backend, container_name)
    if not state or not state.get("exists"):
        secrets_map = decrypt_secrets(backend.secrets_encrypted)
        is_authed = bool(
            secrets_map.get("cli_auth_bundle") or secrets_map.get("oauth_token")
        )
        return {
            "status": "authenticated" if is_authed else "idle",
            "auth_url": None,
            "user_code": None,
            "log_output": "",
            "authenticated": is_authed,
            "error": None,
            "backend": backend,
        }

    raw_logs = await fetch_docker_container_logs(
        compute_backend, container_name, tail_lines=200
    )
    auth_url, user_code = _extract_auth_url_and_code(raw_logs or "")
    log_text = _strip_ansi(raw_logs or "")

    bundle = await _harvest_credentials_from_container(
        compute_backend, container_name, provider
    )
    if bundle:
        # Allow any post-token-exchange user tier / onboarding writes to flush before stopping
        await asyncio.sleep(0.8)
        final_bundle = await _harvest_credentials_from_container(
            compute_backend, container_name, provider
        )
        if final_bundle:
            bundle.update(final_bundle)
        updated_backend = await _persist_authenticated_bundle(
            session, admin, backend, bundle=bundle
        )
        await stop_and_remove_docker_container(compute_backend, container_name)
        return {
            "status": "authenticated",
            "auth_url": auth_url,
            "user_code": user_code,
            "log_output": log_text[-4000:],
            "authenticated": True,
            "error": None,
            "backend": updated_backend,
        }

    running = bool(state.get("running", False))
    return {
        "status": "awaiting_user" if running else "exited",
        "auth_url": auth_url,
        "user_code": user_code,
        "log_output": log_text[-4000:],
        "authenticated": False,
        "error": None if running else "CLI login process exited before credentials were created",
        "backend": backend,
    }


async def submit_cli_auth_code(
    session: AsyncSession,
    admin: User,
    backend_id: str,
    code: str,
) -> dict[str, Any]:
    """Submit an OAuth verification code, localhost callback URL, or direct setup token."""
    backend = await get_backend(session, backend_id)
    trimmed = code.strip()
    if not trimmed:
        raise Invalid("Verification code or token cannot be empty")

    provider = backend.provider.lower()

    # Case 1: User pasted a JSON credential file directly (e.g. ~/.gemini/oauth_creds.json or ~/.claude/.credentials.json)
    if trimmed.startswith("{") and trimmed.endswith("}"):
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict):
                default_rel = {
                    "antigravity": ".gemini/oauth_creds.json",
                    "gemini": ".gemini/oauth_creds.json",
                    "anthropic": ".claude/.credentials.json",
                    "openai": ".codex/auth.json",
                    "xai": ".grok/credentials.json",
                }.get(provider, ".credentials.json")
                bundle = {default_rel: trimmed}
                if provider == "antigravity":
                    bundle[".gemini/antigravity-cli/oauth_creds.json"] = trimmed
                updated_backend = await _persist_authenticated_bundle(
                    session, admin, backend, bundle=bundle
                )
                compute_backend = await _select_docker_compute_backend(session)
                await stop_and_remove_docker_container(
                    compute_backend, _container_name_for_backend(backend.id)
                )
                return {
                    "status": "authenticated",
                    "auth_url": None,
                    "user_code": None,
                    "log_output": "Stored JSON OAuth credential bundle directly.",
                    "authenticated": True,
                    "error": None,
                    "backend": updated_backend,
                }
        except Exception:
            pass

    # Case 2: User pasted a direct OAuth setup token (e.g. `claude setup-token` sk-ant-oat01-... or ya29....)
    if trimmed.startswith(("sk-ant-oat", "ya29.", "1//")):
        updated_backend = await _persist_authenticated_bundle(
            session, admin, backend, oauth_token=trimmed
        )
        compute_backend = await _select_docker_compute_backend(session)
        await stop_and_remove_docker_container(
            compute_backend, _container_name_for_backend(backend.id)
        )
        return {
            "status": "authenticated",
            "auth_url": None,
            "user_code": None,
            "log_output": "Stored OAuth token directly.",
            "authenticated": True,
            "error": None,
            "backend": updated_backend,
        }

    compute_backend = await _select_docker_compute_backend(session)
    container_name = _container_name_for_backend(backend.id)
    state = await inspect_docker_container_state(compute_backend, container_name)
    if not state or not state.get("running", False):
        raise Invalid("CLI login session is not running. Click 'Launch CLI Sign-In' to start a new session.")

    client, base_url = _docker_client_for_backend(compute_backend)
    async with client:
        parsed_url = urlsplit(trimmed)
        if parsed_url.scheme == "http" and parsed_url.hostname in ("localhost", "127.0.0.1"):
            # Hit the CLI's in-container localhost OAuth callback listener directly
            exec_cmd = [
                "sh",
                "-c",
                'curl -sS --max-time 5 "$1" >/dev/null 2>&1 || true',
                "sh",
                trimmed,
            ]
        else:
            # Write the verification code into the PTY FIFO pipe
            exec_cmd = [
                "sh",
                "-c",
                'printf "%s\\n" "$1" > /tmp/.gitseed_stdin',
                "sh",
                trimmed,
            ]

        exec_create = await client.post(
            f"{base_url}/containers/{quote(container_name, safe='')}/exec",
            json={
                "AttachStdin": False,
                "AttachStdout": True,
                "AttachStderr": True,
                "Tty": False,
                "Cmd": exec_cmd,
            },
            timeout=8.0,
        )
        if exec_create.status_code in (200, 201):
            exec_id = exec_create.json().get("Id")
            if exec_id:
                await client.post(
                    f"{base_url}/exec/{quote(str(exec_id), safe='')}/start",
                    json={"Detach": False, "Tty": False},
                    timeout=8.0,
                )

    # Poll for up to 8 seconds for the CLI to exchange the code and write credentials
    for _ in range(16):
        await asyncio.sleep(0.5)
        status_res = await get_cli_auth_status(session, admin, backend_id)
        if status_res.get("authenticated"):
            return status_res

    return await get_cli_auth_status(session, admin, backend_id)


async def cancel_cli_auth_session(
    session: AsyncSession,
    admin: User,
    backend_id: str,
) -> dict[str, Any]:
    """Stop and remove any running CLI auth helper container for a backend."""
    backend = await get_backend(session, backend_id)
    compute_backend = await _select_docker_compute_backend(session)
    container_name = _container_name_for_backend(backend.id)
    await stop_and_remove_docker_container(compute_backend, container_name)
    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    is_authed = bool(
        secrets_map.get("cli_auth_bundle") or secrets_map.get("oauth_token")
    )
    return {
        "status": "authenticated" if is_authed else "cancelled",
        "auth_url": None,
        "user_code": None,
        "log_output": "",
        "authenticated": is_authed,
        "error": None,
        "backend": backend,
    }

