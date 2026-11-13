from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import typer
from pydantic import BaseModel, Field

from gitseed_cli.client import GitSeedClient

try:
    import keyring  # type: ignore[import-not-found]
except Exception:
    keyring = None  # type: ignore[assignment]

DEFAULT_API_URL = "http://localhost:8420"
DEFAULT_CONTEXT_NAME = "default"
KEYRING_SERVICE = "gitseed-cli"


class ContextConfig(BaseModel):
    api_url: str = DEFAULT_API_URL
    project: str | None = None


class CLIConfigFile(BaseModel):
    current_context: str = DEFAULT_CONTEXT_NAME
    contexts: dict[str, ContextConfig] = Field(
        default_factory=lambda: {DEFAULT_CONTEXT_NAME: ContextConfig()}
    )


@dataclass
class RuntimeOptions:
    context: str | None = None
    project: str | None = None
    api_url: str | None = None
    token: str | None = None
    output: str = "table"


def get_config_dir() -> Path:
    override = os.environ.get("GITSEED_CONFIG_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    xdg = os.environ.get("XDG_CONFIG_HOME", "").strip()
    if xdg:
        return Path(xdg).expanduser() / "gitseed"
    return Path.home() / ".config" / "gitseed"


def get_config_path() -> Path:
    return get_config_dir() / "config.json"


def get_credentials_path() -> Path:
    return get_config_dir() / "credentials.json"


def default_token_name() -> str:
    hostname = socket.gethostname().strip() or "local"
    return f"CLI on {hostname}"[:80]


def load_config() -> CLIConfigFile:
    path = get_config_path()
    if not path.exists():
        return CLIConfigFile()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        cfg = CLIConfigFile.model_validate(raw)
        if not cfg.contexts:
            cfg.contexts[DEFAULT_CONTEXT_NAME] = ContextConfig()
        if cfg.current_context not in cfg.contexts:
            cfg.current_context = next(iter(cfg.contexts))
        return cfg
    except Exception:
        return CLIConfigFile()


def save_config(cfg: CLIConfigFile) -> None:
    cfg_dir = get_config_dir()
    cfg_dir.mkdir(parents=True, exist_ok=True)
    path = get_config_path()
    payload = json.dumps(cfg.model_dump(mode="json"), indent=2) + "\n"
    path.write_text(payload, encoding="utf-8")


def _write_restricted_file(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = os.open(str(path), flags, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass
    if os.name == "nt":
        username = os.environ.get("USERNAME", "").strip()
        if username:
            try:
                subprocess.run(
                    [
                        "icacls",
                        str(path),
                        "/inheritance:r",
                        "/grant:r",
                        f"{username}:(R,W)",
                    ],
                    capture_output=True,
                    check=False,
                    timeout=5,
                )
            except Exception:
                pass


def _load_file_credentials() -> dict[str, str]:
    path = get_credentials_path()
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items() if isinstance(v, str) and v.strip()}
    except Exception:
        pass
    return {}


def _save_file_credentials(creds: dict[str, str]) -> None:
    path = get_credentials_path()
    payload = json.dumps(creds, indent=2) + "\n"
    _write_restricted_file(path, payload)


def store_token(context_name: str, token: str) -> str:
    """Store token in OS keyring if available, falling back to 0600 credentials.json.

    Returns the backend used ('keyring' or 'file').
    """
    cleaned = token.strip()
    if not os.environ.get("GITSEED_DISABLE_KEYRING") and keyring is not None:
        try:
            keyring.set_password(KEYRING_SERVICE, context_name, cleaned)
            return "keyring"
        except Exception:
            pass

    creds = _load_file_credentials()
    creds[context_name] = cleaned
    _save_file_credentials(creds)
    return "file"


def load_token(context_name: str) -> str | None:
    """Load token for the given context from OS keyring or 0600 credentials.json."""
    if not os.environ.get("GITSEED_DISABLE_KEYRING") and keyring is not None:
        try:
            val: Any = keyring.get_password(KEYRING_SERVICE, context_name)
            if isinstance(val, str) and val.strip():
                return val.strip()
        except Exception:
            pass

    creds = _load_file_credentials()
    token = creds.get(context_name)
    return token.strip() if token and token.strip() else None


def delete_token(context_name: str) -> None:
    """Remove stored token for the given context from keyring and credentials.json."""
    if not os.environ.get("GITSEED_DISABLE_KEYRING") and keyring is not None:
        try:
            keyring.delete_password(KEYRING_SERVICE, context_name)
        except Exception:
            pass

    creds = _load_file_credentials()
    if context_name in creds:
        del creds[context_name]
        _save_file_credentials(creds)


def _url_origin(raw_url: str) -> str:
    parsed = urlsplit((raw_url or "").strip())
    scheme = (parsed.scheme or "http").lower()
    netloc = (parsed.netloc or parsed.path).strip().rstrip("/").lower()
    return f"{scheme}://{netloc}"


def resolve_connection(opts: RuntimeOptions) -> tuple[str, str, str | None, str | None]:
    """Return (context_name, api_url, token, default_project)."""
    cfg = load_config()
    ctx_name = opts.context or os.environ.get("GITSEED_CONTEXT") or cfg.current_context
    ctx = cfg.contexts.get(ctx_name, ContextConfig())

    override_url = (opts.api_url or os.environ.get("GITSEED_API_URL") or "").strip()
    ctx_url = (ctx.api_url or DEFAULT_API_URL).strip()
    api_url = (override_url or ctx_url).rstrip("/")

    explicit_token = (
        opts.token
        or os.environ.get("GITSEED_API_TOKEN")
        or os.environ.get("GITSEED_TOKEN")
    )
    allow_stored_token = not override_url or (
        _url_origin(override_url) == _url_origin(ctx_url)
    )
    token = explicit_token or (load_token(ctx_name) if allow_stored_token else None)
    if token:
        token = token.strip() or None

    project = opts.project or os.environ.get("GITSEED_PROJECT") or ctx.project
    if project:
        project = project.strip() or None

    return ctx_name, api_url, token, project


def get_opts(ctx: typer.Context) -> RuntimeOptions:
    if isinstance(ctx.obj, RuntimeOptions):
        return ctx.obj
    return RuntimeOptions()


def make_client(ctx: typer.Context) -> GitSeedClient:
    opts = get_opts(ctx)
    _, api_url, token, project = resolve_connection(opts)
    return GitSeedClient(api_url, token=token, default_project=project)
