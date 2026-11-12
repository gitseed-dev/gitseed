"""Pluggable agent CLI runtime adapters (OpenCode, Claude Code, Codex, Antigravity, Gemini, Grok Build)."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import shutil
import signal
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath
from typing import Any

import httpx

logger = logging.getLogger("gitseed_runner")

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

ALL_ALLOWED_CLI_AUTH_PATHS: frozenset[str] = frozenset().union(
    *ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME.values()
)
CLI_AUTH_ALLOWED_FILES = ALL_ALLOWED_CLI_AUTH_PATHS


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


def is_valid_cli_auth_json_content(content: str) -> bool:
    """Return True if content is a JSON object string within the 64KB size cap."""
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped or len(stripped.encode("utf-8")) > MAX_CLI_AUTH_FILE_BYTES:
        return False
    try:
        parsed = json.loads(stripped)
        return isinstance(parsed, dict)
    except Exception:
        return False


def is_valid_cli_auth_file_content(clean_rel: str, content: str) -> bool:
    """Validate an allowlisted CLI auth file's content.

    Files ending in `.json` must be valid JSON objects within MAX_CLI_AUTH_FILE_BYTES.
    Allowlisted non-JSON files in ALL_ALLOWED_CLI_AUTH_PATHS are validated as non-empty UTF-8 text.
    """
    if not isinstance(content, str):
        return False
    stripped = content.strip()
    if not stripped or "\x00" in stripped or len(stripped.encode("utf-8")) > MAX_CLI_AUTH_FILE_BYTES:
        return False
    if clean_rel.lower().endswith(".json"):
        return is_valid_cli_auth_json_content(stripped)
    return clean_rel in ALL_ALLOWED_CLI_AUTH_PATHS


async def terminate_process_tree(proc: Any, *, grace_seconds: float = 5.0) -> None:
    """Gracefully terminate a subprocess and its entire POSIX process group, falling back to SIGKILL."""
    if getattr(proc, "returncode", None) is not None:
        return
    pid = getattr(proc, "pid", None)
    killpg_fn = getattr(os, "killpg", None)
    sigkill = int(getattr(signal, "SIGKILL", signal.SIGTERM))
    use_pg = (
        os.name != "nt"
        and callable(killpg_fn)
        and isinstance(pid, int)
        and pid > 0
    )
    if use_pg and callable(killpg_fn):
        try:
            killpg_fn(pid, int(signal.SIGTERM))
        except (ProcessLookupError, PermissionError, OSError):
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.terminate()
    else:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace_seconds)
    except TimeoutError:
        if use_pg and callable(killpg_fn):
            try:
                killpg_fn(pid, sigkill)
            except (ProcessLookupError, PermissionError, OSError):
                with contextlib.suppress(ProcessLookupError, OSError):
                    proc.kill()
        else:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
    except Exception:
        pass


class RuntimeAdapter(ABC):
    """Base adapter for driving a headless agent CLI inside the runner container."""

    def __init__(
        self,
        *,
        ctx: dict[str, Any],
        working_dir: Path,
        output_file: Path,
    ) -> None:
        self.ctx = ctx
        self.working_dir = working_dir
        self.output_file = output_file
        self.gateway = ctx.get("gateway") or {}
        self.model = str(ctx.get("model") or self.gateway.get("model") or "default")
        self.gateway_model = str(self.gateway.get("model") or self.model or "default")
        self.provider = str(
            ctx.get("provider") or self.gateway.get("provider") or "openai"
        ).lower()
        self.gateway_api_key = str(self.gateway.get("api_key") or "").strip()
        self.gateway_base_url = str(
            self.gateway.get("base_url") or os.environ.get("GITSEED_GATEWAY_URL") or ""
        ).rstrip("/")
        self.gateway_anthropic_url = str(
            self.gateway.get("anthropic_base_url")
            or self.gateway_base_url.removesuffix("/v1")
        ).rstrip("/")
        self.auth_mode = str(self.gateway.get("auth_mode") or "api_key").strip().lower()
        self.oauth_token = str(self.gateway.get("oauth_token") or "").strip()
        raw_bundle = self.gateway.get("cli_auth_bundle")
        self.cli_auth_bundle: dict[str, str] = (
            {str(k): str(v) for k, v in raw_bundle.items() if k and v}
            if isinstance(raw_bundle, dict)
            else {}
        )
        raw_mcp = ctx.get("mcp_servers")
        self.mcp_servers: list[dict[str, Any]] = (
            [m for m in raw_mcp if isinstance(m, dict)]
            if isinstance(raw_mcp, list)
            else []
        )
        self.input_tokens: int = 0
        self.output_tokens: int = 0
        self.cached_input_tokens: int = 0
        self.context_tokens: int = 0
        self.peak_context_tokens: int = 0
        self.turn_count: int = 0
        self.cost: float = 0.0
        self._completed_input_tokens: int = 0
        self._completed_output_tokens: int = 0
        self._completed_cached_input_tokens: int = 0
        self._invocation_input_tokens: int = 0
        self._invocation_output_tokens: int = 0
        self._invocation_cached_input_tokens: int = 0
        self.max_turns: int | None = self._resolve_max_turns()
        self.synthesized_via: str | None = None

    def begin_invocation(self) -> None:
        """Snapshot prior invocation totals before launching a new CLI subprocess."""
        self._completed_input_tokens = self.input_tokens
        self._completed_output_tokens = self.output_tokens
        self._completed_cached_input_tokens = self.cached_input_tokens
        self._invocation_input_tokens = 0
        self._invocation_output_tokens = 0
        self._invocation_cached_input_tokens = 0

    def finish_invocation(self) -> None:
        """Commit the current subprocess's peak/accumulated token totals into cumulative counters."""
        self._completed_input_tokens = self.input_tokens
        self._completed_output_tokens = self.output_tokens
        self._completed_cached_input_tokens = self.cached_input_tokens
        self._invocation_input_tokens = 0
        self._invocation_output_tokens = 0
        self._invocation_cached_input_tokens = 0

    def record_stream_usage(
        self,
        input_tokens: int,
        output_tokens: int,
        *,
        cached_input_tokens: int = 0,
        context_tokens: int | None = None,
        cumulative: bool = True,
    ) -> None:
        """Record stream token telemetry within the active CLI invocation without losing prior runs."""
        in_val = max(0, int(input_tokens))
        out_val = max(0, int(output_tokens))
        cached_val = max(0, int(cached_input_tokens))
        if cumulative:
            self._invocation_input_tokens = max(self._invocation_input_tokens, in_val)
            self._invocation_output_tokens = max(self._invocation_output_tokens, out_val)
            self._invocation_cached_input_tokens = max(
                self._invocation_cached_input_tokens, cached_val
            )
        else:
            self._invocation_input_tokens += in_val
            self._invocation_output_tokens += out_val
            self._invocation_cached_input_tokens += cached_val

        self.input_tokens = self._completed_input_tokens + self._invocation_input_tokens
        self.output_tokens = self._completed_output_tokens + self._invocation_output_tokens
        self.cached_input_tokens = (
            self._completed_cached_input_tokens + self._invocation_cached_input_tokens
        )
        ctx_val = (
            max(0, int(context_tokens))
            if context_tokens is not None
            else (in_val + out_val)
        )
        if ctx_val > 0:
            self.context_tokens = ctx_val
            self.peak_context_tokens = max(self.peak_context_tokens, ctx_val)
            self.turn_count += 1

    def _resolve_max_turns(self) -> int | None:
        """Resolve optional positive turn cap from context, sandbox_config, or env."""
        raw_sbx = self.ctx.get("sandbox_config")
        sbx_turns = raw_sbx.get("max_turns") if isinstance(raw_sbx, dict) else None
        for candidate in (
            self.ctx.get("max_turns"),
            sbx_turns,
            os.environ.get("RUNNER_MAX_TURNS"),
            os.environ.get("GITSEED_MAX_TURNS"),
        ):
            if candidate is not None and str(candidate).strip() != "":
                try:
                    parsed = int(str(candidate).strip())
                    if parsed > 0:
                        return parsed
                except (ValueError, TypeError):
                    continue
        return None

    def cleanup_workspace(self) -> None:
        """Clean up or restore any temporary harness files written into working_dir."""
        return None

    def __enter__(self) -> RuntimeAdapter:
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.cleanup_workspace()

    async def __aenter__(self) -> RuntimeAdapter:
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.cleanup_workspace()

    def parse_stream_line(self, raw_line: str) -> str | None:
        """Optional NDJSON line parser; returns None to use default formatting."""
        return None

    async def run_command(
        self,
        prompt: str,
        *,
        continue_session: bool = False,
        timeout: float | None = None,
    ) -> tuple[int, str]:
        """Execute the CLI command once, guaranteeing workspace secret cleanup in finally."""
        try:
            env = self.prepare_workspace()
            env.pop("GITSEED_RUN_TOKEN", None)
            cmd = self.build_command(prompt, continue_session=continue_session)
            self.begin_invocation()
            spawn_kwargs: dict[str, Any] = (
                {"start_new_session": True} if os.name != "nt" else {}
            )
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(self.working_dir),
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                limit=16 * 1024 * 1024,
                **spawn_kwargs,
            )
            try:
                if timeout is not None:
                    stdout_bytes, _ = await asyncio.wait_for(
                        proc.communicate(), timeout=timeout
                    )
                else:
                    stdout_bytes, _ = await proc.communicate()
            except BaseException:
                await terminate_process_tree(proc)
                raise

            raw_out = (stdout_bytes or b"").decode("utf-8", errors="replace")
            rendered_lines: list[str] = []
            for line in raw_out.splitlines(keepends=True):
                parsed_line = self.parse_stream_line(line)
                rendered_lines.append(line if parsed_line is None else parsed_line)
            return int(proc.returncode or 0), "".join(rendered_lines)
        finally:
            self.finish_invocation()
            self.cleanup_workspace()

    def restore_cli_auth_bundle(self) -> None:
        """Materialize allowlisted CLI OAuth credential files into /home/runner before running."""
        if not self.cli_auth_bundle:
            return
        home_root = Path.home().resolve()
        for rel_path, content in self.cli_auth_bundle.items():
            clean_rel = normalize_cli_auth_rel_path(rel_path)
            if not clean_rel or clean_rel not in ALL_ALLOWED_CLI_AUTH_PATHS:
                logger.warning("Skipping disallowed CLI auth bundle path on restore: %s", rel_path)
                continue
            if not is_valid_cli_auth_file_content(clean_rel, content):
                logger.warning("Skipping invalid/oversized CLI auth bundle entry: %s", clean_rel)
                continue
            raw_target = home_root / clean_rel
            if raw_target.is_symlink():
                continue
            target = raw_target.resolve()
            if not target.is_relative_to(home_root) or target == home_root:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content.strip(), encoding="utf-8")
            try:
                os.chmod(target, 0o600)
            except OSError:
                pass

    def collect_updated_cli_auth_bundle(
        self, *, strict_runtime: bool = False
    ) -> dict[str, str] | None:
        """Read back allowlisted CLI OAuth credential files after run execution if tokens were refreshed."""
        if self.auth_mode != "cli_oauth":
            return None
        home_root = Path.home().resolve()
        runtime_name = str(self.ctx.get("runtime") or "").strip().lower()
        runtime_allowed = ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME.get(
            runtime_name, ALL_ALLOWED_CLI_AUTH_PATHS
        )
        allowed_set = runtime_allowed if strict_runtime else ALL_ALLOWED_CLI_AUTH_PATHS
        candidate_rels = {
            norm
            for raw in (set(self.cli_auth_bundle.keys()) | set(allowed_set))
            if (norm := normalize_cli_auth_rel_path(raw)) and norm in allowed_set
        }
        updated: dict[str, str] = {}
        for clean_rel in sorted(candidate_rels):
            raw_target = home_root / clean_rel
            if raw_target.is_symlink():
                continue
            target = raw_target.resolve()
            if not target.is_relative_to(home_root) or target == home_root or not target.is_file():
                continue
            try:
                text = target.read_text(encoding="utf-8", errors="ignore").strip()
                if is_valid_cli_auth_file_content(clean_rel, text):
                    updated[clean_rel] = text
            except Exception:
                continue
        return updated or None

    harvest_updated_cli_auth_bundle = collect_updated_cli_auth_bundle

    def _multi_repo_workspace_root(self) -> Path | None:
        """Return parent workspace root when working_dir is inside a multi-repo / worktree layout."""
        if self.output_file.parent.name == ".gitseed":
            workspace_root = self.output_file.parent.parent
            if self.working_dir.resolve() != workspace_root.resolve():
                return workspace_root
            return None
        candidate = self.output_file.parent.parent
        try:
            wd_resolved = self.working_dir.resolve()
            if (
                self.output_file.parent.resolve() != wd_resolved
                and wd_resolved != candidate.resolve()
                and wd_resolved.is_relative_to(candidate.resolve())
            ):
                return candidate
        except OSError:
            pass
        if self.working_dir.parent == Path("/workspace") and self.working_dir != Path("/workspace"):
            return Path("/workspace")
        return None

    def _build_standard_mcp_map(self) -> dict[str, dict[str, Any]]:
        """Build standard `mcpServers` map with accurate `stdio`, `http`, or `sse` transport."""
        servers: dict[str, dict[str, Any]] = {}
        for item in self.mcp_servers:
            slug = str(item.get("slug") or item.get("id") or "mcp").strip()
            raw_transport = (
                str(item.get("transport") or item.get("provider") or "mcp-sse")
                .strip()
                .lower()
            )
            endpoint = str(item.get("endpoint") or item.get("url") or "").strip()
            if not slug or not endpoint:
                continue
            headers = item.get("headers") if isinstance(item.get("headers"), dict) else {}
            env_map = item.get("env") if isinstance(item.get("env"), dict) else {}
            if raw_transport in ("stdio", "mcp-stdio"):
                parts = endpoint.split()
                if parts:
                    entry: dict[str, Any] = {
                        "type": "stdio",
                        "command": parts[0],
                        "args": parts[1:],
                    }
                    if env_map:
                        entry["env"] = env_map
                    servers[slug] = entry
            elif raw_transport in ("http", "mcp-http", "streamable-http", "streamable_http"):
                entry = {
                    "type": "http",
                    "url": endpoint,
                }
                if headers:
                    entry["headers"] = headers
                servers[slug] = entry
            else:
                entry = {
                    "type": "sse",
                    "url": endpoint,
                }
                if headers:
                    entry["headers"] = headers
                servers[slug] = entry
        return servers

    @abstractmethod
    def prepare_workspace(self) -> dict[str, str]:
        """Write any harness config files and return environment variables for the subprocess."""

    @abstractmethod
    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        """Return the CLI argv for initial execution or session continuation."""

    def build_interactive_command(self) -> list[str]:
        """Return the CLI argv for launching an interactive TUI session inside the sandbox."""
        return [self.binary_name]

    @property
    @abstractmethod
    def binary_name(self) -> str:
        """Executable name on PATH."""

    def is_binary_available(self) -> bool:
        return shutil.which(self.binary_name) is not None


class OpenCodeAdapter(RuntimeAdapter):
    """Adapter for the OpenCode CLI (`opencode run --format json`)."""

    def __init__(
        self,
        *,
        ctx: dict[str, Any],
        working_dir: Path,
        output_file: Path,
    ) -> None:
        super().__init__(ctx=ctx, working_dir=working_dir, output_file=output_file)
        self._had_preexisting_opencode: bool = False
        self._preexisting_opencode_bytes: bytes | None = None
        self._workspace_prepared: bool = False
        self._external_config_path: Path | None = None

    @property
    def binary_name(self) -> str:
        return "opencode"

    def _resolve_opencode_oauth_model(self) -> str:
        """Return provider-prefixed model string (`<provider>/<model>`) for OpenCode OAuth mode."""
        raw = (self.model or "").strip()
        if not raw or raw == "default":
            return ""
        if "/" in raw:
            return raw

        provider_alias_map = {
            "gemini": "google",
            "grok": "xai",
            "claude": "anthropic",
        }
        explicit_provider = (
            str(self.ctx.get("provider") or self.gateway.get("provider") or "").strip().lower()
        )
        explicit_provider = provider_alias_map.get(explicit_provider, explicit_provider)

        bundle_providers: list[str] = []
        for rel in ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"]:
            raw_content = self.cli_auth_bundle.get(rel)
            if not raw_content:
                continue
            try:
                parsed = json.loads(raw_content)
                if isinstance(parsed, dict):
                    for key in parsed:
                        norm_k = provider_alias_map.get(str(key).strip().lower(), str(key).strip().lower())
                        if norm_k and norm_k not in bundle_providers:
                            bundle_providers.append(norm_k)
            except Exception:
                continue

        if len(bundle_providers) == 1:
            return f"{bundle_providers[0]}/{raw}"
        if explicit_provider and (not bundle_providers or explicit_provider in bundle_providers):
            return f"{explicit_provider}/{raw}"

        lower_model = raw.lower()
        if lower_model.startswith("claude-"):
            inferred = "anthropic"
        elif lower_model.startswith(("gpt-", "o1-", "o3-", "o4-", "codex-")) or lower_model in (
            "o1",
            "o3",
            "o4-mini",
        ):
            inferred = "openai"
        elif lower_model.startswith("gemini-"):
            inferred = "google"
        elif lower_model.startswith("grok-"):
            inferred = "xai"
        else:
            inferred = ""

        if inferred and (not bundle_providers or inferred in bundle_providers):
            return f"{inferred}/{raw}"
        if explicit_provider:
            return f"{explicit_provider}/{raw}"
        return raw

    def prepare_workspace(self) -> dict[str, str]:
        self.restore_cli_auth_bundle()
        env = os.environ.copy()
        mcp_cfg_full: dict[str, Any] = {}
        mcp_cfg_workspace: dict[str, Any] = {}
        if self.mcp_servers:
            for idx, item in enumerate(self.mcp_servers):
                slug = str(item.get("slug") or item.get("id") or "mcp").strip()
                raw_transport = (
                    str(item.get("transport") or item.get("provider") or "mcp-sse")
                    .strip()
                    .lower()
                )
                endpoint = str(item.get("endpoint") or item.get("url") or "").strip()
                if not slug or not endpoint:
                    continue
                if raw_transport in ("stdio", "mcp-stdio"):
                    parts = endpoint.split()
                    if parts:
                        raw_env = item.get("env") if isinstance(item.get("env"), dict) else {}
                        redacted_env: dict[str, str] = {}
                        for e_idx, (ek, ev) in enumerate(raw_env.items()):
                            env_var_name = f"GITSEED_OC_MCP_{idx}_ENV_{e_idx}"
                            env[env_var_name] = str(ev)
                            redacted_env[str(ek)] = f"{{env:{env_var_name}}}"
                        mcp_cfg_full[slug] = {
                            "type": "local",
                            "command": parts,
                            "enabled": True,
                            "environment": raw_env,
                        }
                        mcp_cfg_workspace[slug] = {
                            "type": "local",
                            "command": parts,
                            "enabled": True,
                            "environment": redacted_env,
                        }
                else:
                    raw_headers = (
                        item.get("headers") if isinstance(item.get("headers"), dict) else {}
                    )
                    redacted_headers: dict[str, str] = {}
                    for h_idx, (hk, hv) in enumerate(raw_headers.items()):
                        hdr_var_name = f"GITSEED_OC_MCP_{idx}_HDR_{h_idx}"
                        env[hdr_var_name] = str(hv)
                        redacted_headers[str(hk)] = f"{{env:{hdr_var_name}}}"
                    mcp_cfg_full[slug] = {
                        "type": "remote",
                        "url": endpoint,
                        "enabled": True,
                        "headers": raw_headers,
                    }
                    mcp_cfg_workspace[slug] = {
                        "type": "remote",
                        "url": endpoint,
                        "enabled": True,
                        "headers": redacted_headers,
                    }

        if self.auth_mode == "cli_oauth":
            oauth_model = self._resolve_opencode_oauth_model()
            opencode_config_full: dict[str, Any] = {
                "$schema": "https://opencode.ai/config.json",
                "permission": {
                    "edit": "allow",
                    "bash": "allow",
                    "webfetch": "allow",
                },
            }
            if oauth_model:
                opencode_config_full["model"] = oauth_model
            opencode_config_workspace = dict(opencode_config_full)
        else:
            opencode_config_full = {
                "$schema": "https://opencode.ai/config.json",
                "model": f"gitseed/{self.model}",
                "small_model": f"gitseed/{self.model}",
                "permission": {
                    "edit": "allow",
                    "bash": "allow",
                    "webfetch": "allow",
                },
                "provider": {
                    "gitseed": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "GitSeed LiteLLM Proxy",
                        "options": {
                            "baseURL": self.gateway_base_url,
                            "apiKey": self.gateway_api_key,
                        },
                        "models": {
                            self.model: {
                                "name": self.model,
                                "tool_call": True,
                            }
                        },
                    }
                },
            }
            opencode_config_workspace = {
                "$schema": "https://opencode.ai/config.json",
                "model": f"gitseed/{self.model}",
                "small_model": f"gitseed/{self.model}",
                "permission": {
                    "edit": "allow",
                    "bash": "allow",
                    "webfetch": "allow",
                },
                "provider": {
                    "gitseed": {
                        "npm": "@ai-sdk/openai-compatible",
                        "name": "GitSeed LiteLLM Proxy",
                        "options": {
                            "baseURL": self.gateway_base_url,
                            "apiKey": "{env:OPENAI_API_KEY}",
                        },
                        "models": {
                            self.model: {
                                "name": self.model,
                                "tool_call": True,
                            }
                        },
                    }
                },
            }

        if mcp_cfg_full:
            opencode_config_full["mcp"] = mcp_cfg_full
            opencode_config_workspace["mcp"] = mcp_cfg_workspace

        # Write full credentialed config outside /workspace so SIGKILL never leaves secrets in repo
        ext_dir = Path(tempfile.gettempdir())
        ext_dir.mkdir(parents=True, exist_ok=True)
        ext_cfg_path = ext_dir / f"gitseed-opencode-{os.getpid()}-{id(self)}.json"
        if ext_cfg_path.is_symlink():
            ext_cfg_path.unlink(missing_ok=True)
        ext_cfg_path.write_text(json.dumps(opencode_config_full, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(ext_cfg_path, 0o600)
        self._external_config_path = ext_cfg_path
        env["OPENCODE_CONFIG"] = str(ext_cfg_path)

        # Write secret-free workspace config using `{env:...}` placeholders
        config_path = self.working_dir / "opencode.json"
        if not self._workspace_prepared:
            if config_path.is_file() and not config_path.is_symlink():
                self._had_preexisting_opencode = True
                self._preexisting_opencode_bytes = config_path.read_bytes()
            else:
                self._had_preexisting_opencode = False
                self._preexisting_opencode_bytes = None
            self._workspace_prepared = True

        if config_path.is_symlink():
            config_path.unlink(missing_ok=True)
        config_path.write_text(json.dumps(opencode_config_workspace, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(config_path, 0o600)

        if self.auth_mode == "cli_oauth":
            env.pop("OPENAI_API_KEY", None)
            env.pop("OPENAI_BASE_URL", None)
            env.pop("ANTHROPIC_API_KEY", None)
            env.pop("ANTHROPIC_BASE_URL", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            home_root = Path.home().resolve()
            has_opencode_auth_file = any(
                (home_root / rel).is_file()
                for rel in ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["opencode"]
            )
            if self.oauth_token and not has_opencode_auth_file:
                provider_hint = (
                    str(self.ctx.get("provider") or self.gateway.get("provider") or "")
                    .strip()
                    .lower()
                )
                if provider_hint == "anthropic":
                    env["ANTHROPIC_AUTH_TOKEN"] = self.oauth_token
                    env["ANTHROPIC_API_KEY"] = self.oauth_token
                elif provider_hint in ("gemini", "google"):
                    env["GEMINI_API_KEY"] = self.oauth_token
                elif provider_hint in ("xai", "grok"):
                    env["XAI_API_KEY"] = self.oauth_token
                elif provider_hint == "openai":
                    env["OPENAI_API_KEY"] = self.oauth_token
            return env

        env["OPENAI_API_KEY"] = self.gateway_api_key
        env["OPENAI_BASE_URL"] = self.gateway_base_url
        env["ANTHROPIC_API_KEY"] = self.gateway_api_key
        env["ANTHROPIC_BASE_URL"] = self.gateway_anthropic_url
        return env

    def cleanup_workspace(self) -> None:
        if self._external_config_path is not None:
            with contextlib.suppress(OSError):
                self._external_config_path.unlink(missing_ok=True)
            self._external_config_path = None
        config_path = self.working_dir / "opencode.json"
        if not self._workspace_prepared:
            return
        try:
            if self._had_preexisting_opencode and self._preexisting_opencode_bytes is not None:
                if config_path.is_symlink():
                    config_path.unlink(missing_ok=True)
                config_path.write_bytes(self._preexisting_opencode_bytes)
            else:
                config_path.unlink(missing_ok=True)
        finally:
            self._workspace_prepared = False
            self._had_preexisting_opencode = False
            self._preexisting_opencode_bytes = None

    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        cmd = ["opencode", "run", "--format", "json"]
        if self.auth_mode == "cli_oauth":
            oauth_model = self._resolve_opencode_oauth_model()
            if oauth_model:
                cmd.extend(["--model", oauth_model])
        else:
            cmd.extend(["--model", f"gitseed/{self.model}"])
        if continue_session:
            cmd.append("--continue")
        cmd.append(prompt)
        return cmd

    def parse_stream_line(self, raw_line: str) -> str | None:
        """Parse OpenCode `--format json` NDJSON events, falling back to plain lines."""
        stripped = raw_line.strip()
        if not stripped.startswith("{") or not stripped.endswith("}"):
            return raw_line
        try:
            ev = json.loads(stripped)
        except Exception:
            return raw_line
        if not isinstance(ev, dict):
            return raw_line

        ev_type = str(ev.get("type") or ev.get("event") or "").strip().lower()
        raw_part = ev.get("part")
        part: dict[str, Any] = raw_part if isinstance(raw_part, dict) else {}
        part_type = str(part.get("type") or "").strip().lower()

        tokens_obj: dict[str, Any] | None = None
        part_tokens = part.get("tokens")
        ev_tokens = ev.get("tokens")
        if isinstance(part_tokens, dict):
            tokens_obj = part_tokens
        elif isinstance(ev_tokens, dict):
            tokens_obj = ev_tokens

        if tokens_obj is not None:
            in_tok = int(
                tokens_obj.get("input")
                or tokens_obj.get("input_tokens")
                or tokens_obj.get("prompt_tokens")
                or 0
            )
            out_tok = int(
                tokens_obj.get("output")
                or tokens_obj.get("output_tokens")
                or tokens_obj.get("completion_tokens")
                or 0
            )
            reasoning_tok = int(tokens_obj.get("reasoning") or 0)
            if out_tok == 0 and reasoning_tok > 0:
                out_tok = reasoning_tok
            cache_obj = (
                tokens_obj.get("cache") if isinstance(tokens_obj.get("cache"), dict) else {}
            )
            cache_read_tok = max(
                0,
                int(cache_obj.get("read") or 0)
                + int(tokens_obj.get("cache_read_input_tokens") or 0),
            )
            cache_write_tok = max(
                0,
                int(cache_obj.get("write") or 0)
                + int(tokens_obj.get("cache_creation_input_tokens") or 0),
            )
            # Discount repeated prompt cache reads (0.1x) so tool steps do not 10x inflate input_tokens,
            # while tracking full cached_input_tokens and context window size separately.
            billed_in = max(0, in_tok + cache_write_tok + (cache_read_tok // 10))
            step_out = max(0, out_tok)
            step_ctx = max(0, in_tok + cache_write_tok + cache_read_tok) + step_out
            if billed_in > 0 or step_out > 0 or step_ctx > 0:
                self.record_stream_usage(
                    billed_in,
                    step_out,
                    cached_input_tokens=cache_read_tok,
                    context_tokens=step_ctx,
                    cumulative=False,
                )

        raw_cost = part.get("cost") if part.get("cost") is not None else ev.get("cost")
        if (
            isinstance(raw_cost, (int, float))
            and not isinstance(raw_cost, bool)
            and raw_cost > 0
        ):
            self.cost = round(self.cost + float(raw_cost), 10)

        if ev_type in ("step_start", "step-start", "step_finish", "step-finish") or part_type in (
            "step-start",
            "step_start",
            "step-finish",
            "step_finish",
        ):
            return ""

        if ev_type == "text" or part_type == "text":
            txt = part.get("text") if part.get("text") is not None else ev.get("text")
            if isinstance(txt, str) and txt:
                return txt if txt.endswith("\n") else f"{txt}\n"
            return ""

        if ev_type in ("reasoning", "thinking") or part_type in ("reasoning", "thinking"):
            thought = part.get("text") or ev.get("thinking") or ev.get("text")
            if isinstance(thought, str) and thought.strip():
                return f"[thinking] {thought.strip()}\n"
            return ""

        raw_state = part.get("state")
        if (ev_type in ("tool_use", "tool") or part_type == "tool") and isinstance(
            raw_state, dict
        ):
            state = raw_state
            t_name = str(part.get("tool") or ev.get("name") or "tool")
            st_status = str(state.get("status") or "").lower()
            if st_status in ("completed", "error"):
                tag = "error" if st_status == "error" else "ok"
                out_raw = state.get("output") or state.get("error") or ""
                out_prev = str(out_raw).strip().replace("\n", " ")[:180]
                return f"[tool:{tag}] {t_name}" + (f" -> {out_prev}\n" if out_prev else "\n")
            return f"[tool] {t_name}\n"

        # Fall back to default formatter for unrecognized JSON shapes
        return None

    def parse_output(self, raw_output: str) -> str:
        """Parse multi-line stdout from `opencode run --format json` and return text output."""
        parts: list[str] = []
        for line in raw_output.splitlines(keepends=True):
            res = self.parse_stream_line(line)
            parts.append(line if res is None else res)
        return "".join(parts)


class ClaudeCodeAdapter(RuntimeAdapter):
    """Adapter for the Anthropic Claude Code CLI (`claude -p`)."""

    @property
    def binary_name(self) -> str:
        return "claude"

    def _normalized_claude_model(self) -> str:
        raw = (self.model or "").strip()
        legacy_map = {
            "claude-sonnet-5-5": "claude-sonnet-4-5",
            "claude-opus-5": "claude-opus-4-1",
            "claude-haiku-5": "claude-haiku-4-5",
        }
        return legacy_map.get(raw, raw)

    def prepare_workspace(self) -> dict[str, str]:
        self.restore_cli_auth_bundle()
        claude_cfg_path = Path.home() / ".claude.json"
        claude_cfg: dict[str, Any] = {}
        if claude_cfg_path.exists():
            try:
                loaded = json.loads(claude_cfg_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    claude_cfg = loaded
            except Exception:
                pass
        claude_cfg["hasCompletedOnboarding"] = True
        claude_cfg["bypassPermissionsModeAccepted"] = True
        mcp_map = self._build_standard_mcp_map()
        if mcp_map:
            claude_cfg["mcpServers"] = mcp_map
        claude_cfg_path.write_text(json.dumps(claude_cfg, indent=2), encoding="utf-8")

        env = os.environ.copy()
        env.pop("ANTHROPIC_API_KEY", None)
        if self.auth_mode == "cli_oauth":
            env.pop("ANTHROPIC_BASE_URL", None)
            env.pop("ANTHROPIC_AUTH_TOKEN", None)
            if self.oauth_token:
                env["CLAUDE_CODE_OAUTH_TOKEN"] = self.oauth_token
        else:
            env["ANTHROPIC_BASE_URL"] = self.gateway_anthropic_url
            env["ANTHROPIC_AUTH_TOKEN"] = self.gateway_api_key
        norm_model = self._normalized_claude_model()
        if norm_model and norm_model != "default":
            env["ANTHROPIC_MODEL"] = norm_model
            env["ANTHROPIC_SMALL_FAST_MODEL"] = norm_model
        env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = "1"
        return env

    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        cmd = [
            "claude",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--dangerously-skip-permissions",
        ]
        norm_model = self._normalized_claude_model()
        if norm_model and norm_model != "default":
            cmd.extend(["--model", norm_model])
        max_turns = self._resolve_max_turns()
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])
        if continue_session:
            cmd.append("--continue")
        return cmd

    def build_interactive_command(self) -> list[str]:
        cmd = ["claude", "--dangerously-skip-permissions"]
        norm_model = self._normalized_claude_model()
        if norm_model and norm_model != "default":
            cmd.extend(["--model", norm_model])
        return cmd


class CodexAdapter(RuntimeAdapter):
    """Adapter for the OpenAI Codex CLI (`codex exec`)."""

    @property
    def binary_name(self) -> str:
        return "codex"

    @staticmethod
    def _format_toml_inline_table(mapping: dict[str, Any]) -> str:
        items = [
            f"{json.dumps(str(k))} = {json.dumps(str(v))}"
            for k, v in mapping.items()
            if k is not None and v is not None
        ]
        return "{ " + ", ".join(items) + " }"

    def _build_codex_mcp_toml_lines(self) -> list[str]:
        lines: list[str] = []
        mcp_map = self._build_standard_mcp_map()
        for slug, cfg in mcp_map.items():
            safe_slug = re.sub(r"[^a-zA-Z0-9_-]", "_", str(slug))
            lines.append(f"[mcp_servers.{safe_slug}]")
            if "command" in cfg:
                lines.append(f"command = {json.dumps(cfg['command'])}")
                if cfg.get("args"):
                    lines.append(f"args = {json.dumps(list(cfg['args']))}")
                if isinstance(cfg.get("env"), dict) and cfg["env"]:
                    lines.append(f"env = {self._format_toml_inline_table(cfg['env'])}")
            elif "url" in cfg:
                lines.append(f"url = {json.dumps(cfg['url'])}")
                if isinstance(cfg.get("headers"), dict) and cfg["headers"]:
                    lines.append(f"headers = {self._format_toml_inline_table(cfg['headers'])}")
                    lines.append(
                        f"http_headers = {self._format_toml_inline_table(cfg['headers'])}"
                    )
            lines.append("")
        return lines

    def prepare_workspace(self) -> dict[str, str]:
        self.restore_cli_auth_bundle()
        codex_dir = Path.home() / ".codex"
        codex_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        mcp_lines = self._build_codex_mcp_toml_lines()
        if self.auth_mode == "cli_oauth":
            env.pop("OPENAI_BASE_URL", None)
            env.pop("OPENAI_API_KEY", None)
            if self.oauth_token and not (codex_dir / "auth.json").exists():
                auth_path = codex_dir / "auth.json"
                auth_path.write_text(
                    json.dumps(
                        {
                            "access_token": self.oauth_token,
                            "tokens": {"access_token": self.oauth_token},
                        },
                        indent=2,
                    ),
                    encoding="utf-8",
                )
                with contextlib.suppress(OSError):
                    os.chmod(auth_path, 0o600)
            oauth_toml_lines: list[str] = []
            if self.model and self.model != "default":
                clean_model = (
                    self.model.split("/", 1)[-1].strip()
                    if "/" in self.model
                    else self.model.strip()
                )
                if clean_model:
                    oauth_toml_lines.append(f"model = {json.dumps(clean_model)}")
                    oauth_toml_lines.append("")
            if mcp_lines:
                oauth_toml_lines.extend(mcp_lines)
            if oauth_toml_lines:
                (codex_dir / "config.toml").write_text(
                    "\n".join(oauth_toml_lines), encoding="utf-8"
                )
            return env

        model_name = self.model if self.model and self.model != "default" else "default"
        config_toml = "\n".join(
            [
                f'model = {json.dumps(model_name)}',
                'model_provider = "gitseed"',
                "",
                "[model_providers.gitseed]",
                'name = "GitSeed LiteLLM Proxy"',
                f'base_url = {json.dumps(self.gateway_base_url)}',
                'env_key = "OPENAI_API_KEY"',
                'wire_api = "chat"',
                "",
                *mcp_lines,
            ]
        )
        (codex_dir / "config.toml").write_text(config_toml, encoding="utf-8")

        env["OPENAI_BASE_URL"] = self.gateway_base_url
        env["OPENAI_API_KEY"] = self.gateway_api_key
        return env

    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        ws_root = self._multi_repo_workspace_root()
        if continue_session:
            cmd = [
                "codex",
                "exec",
                "resume",
                "--last",
                "--json",
                "--full-auto",
                "--skip-git-repo-check",
            ]
            if ws_root is not None:
                cmd.extend(["--add-dir", str(ws_root)])
            cmd.append(prompt)
            return cmd
        cmd = ["codex", "exec", "--json", "--full-auto", "--skip-git-repo-check"]
        if ws_root is not None:
            cmd.extend(["--add-dir", str(ws_root)])
        if self.model and self.model != "default":
            cmd.extend(["-m", self.model])
        cmd.append(prompt)
        return cmd

    def build_interactive_command(self) -> list[str]:
        cmd = ["codex", "--full-auto"]
        ws_root = self._multi_repo_workspace_root()
        if ws_root is not None:
            cmd.extend(["--add-dir", str(ws_root)])
        if self.model and self.model != "default":
            cmd.extend(["-m", self.model])
        return cmd


def _normalize_google_cli_model(raw_model: str | None) -> str:
    """Normalize model aliases and legacy placeholder names for `agy` and `gemini` CLIs."""
    normalized = (raw_model or "").strip()
    if normalized in ("default", ""):
        return ""
    if normalized in ("flash", "flash_lite", "gemini-3.8-flash", "gemini-3.5-flash"):
        return "gemini-2.5-flash"
    if normalized in ("pro", "inherit", "gemini-4-argon", "gemini-4-carbon"):
        return "gemini-2.5-pro"
    return normalized


def _write_gemini_trusted_folders(gemini_dir: Path, working_dir: Path) -> None:
    """Pre-populate ~/.gemini/trustedFolders.json so Gemini CLI never downgrades --yolo mode."""
    trusted_path = gemini_dir / "trustedFolders.json"
    trusted_map: dict[str, str] = {}
    if trusted_path.exists():
        with contextlib.suppress(Exception):
            loaded = json.loads(trusted_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                trusted_map = {str(k): str(v) for k, v in loaded.items()}
    for folder in dict.fromkeys(
        [
            "/workspace",
            str(working_dir),
            str(working_dir.resolve()),
            str(Path.cwd()),
            str(Path.home()),
        ]
    ):
        trusted_map[folder] = "TRUST_FOLDER"
    trusted_path.write_text(json.dumps(trusted_map, indent=2), encoding="utf-8")


def _format_gemini_cli_mcp_map(mcp_map: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Convert standard `mcpServers` entries into `@google/gemini-cli` settings.json schema."""
    formatted: dict[str, dict[str, Any]] = {}
    for slug, cfg in mcp_map.items():
        srv_type = str(cfg.get("type") or "sse").lower()
        if srv_type == "stdio" or "command" in cfg:
            entry: dict[str, Any] = {
                "command": cfg["command"],
                "args": list(cfg.get("args") or []),
            }
            if isinstance(cfg.get("env"), dict) and cfg["env"]:
                entry["env"] = cfg["env"]
            formatted[slug] = entry
        elif srv_type == "http":
            entry = {"httpUrl": cfg["url"]}
            if isinstance(cfg.get("headers"), dict) and cfg["headers"]:
                entry["headers"] = cfg["headers"]
            formatted[slug] = entry
        else:
            entry = {"url": cfg["url"]}
            if isinstance(cfg.get("headers"), dict) and cfg["headers"]:
                entry["headers"] = cfg["headers"]
            formatted[slug] = entry
    return formatted


def _write_antigravity_mcp_config(
    gemini_dir: Path, mcp_map: dict[str, dict[str, Any]]
) -> None:
    """Write `~/.gemini/config/mcp_config.json` with native Antigravity `serverUrl` / stdio entries."""
    if not mcp_map:
        return
    config_dir = gemini_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    mcp_config_path = config_dir / "mcp_config.json"
    existing_doc: dict[str, Any] = {}
    if mcp_config_path.exists():
        with contextlib.suppress(Exception):
            loaded = json.loads(mcp_config_path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                existing_doc = loaded
    agy_mcp_entries: dict[str, dict[str, Any]] = {}
    for slug, cfg in mcp_map.items():
        srv_type = str(cfg.get("type") or "sse").lower()
        if srv_type == "stdio" or "command" in cfg:
            entry: dict[str, Any] = {
                "command": cfg["command"],
                "args": list(cfg.get("args") or []),
            }
            if isinstance(cfg.get("env"), dict) and cfg["env"]:
                entry["env"] = cfg["env"]
            agy_mcp_entries[slug] = entry
        else:
            entry = {"serverUrl": cfg["url"]}
            if isinstance(cfg.get("headers"), dict) and cfg["headers"]:
                entry["headers"] = cfg["headers"]
            agy_mcp_entries[slug] = entry
    existing_servers = existing_doc.get("mcpServers")
    if isinstance(existing_servers, dict):
        existing_doc["mcpServers"] = {**existing_servers, **agy_mcp_entries}
    else:
        existing_doc["mcpServers"] = agy_mcp_entries
    mcp_config_path.write_text(json.dumps(existing_doc, indent=2), encoding="utf-8")


class AntigravityAdapter(RuntimeAdapter):
    """Adapter for the Google Antigravity headless CLI (`agy -p --dangerously-skip-permissions`)."""

    @property
    def binary_name(self) -> str:
        if shutil.which("agy") is not None:
            return "agy"
        if self.auth_mode == "cli_oauth" and shutil.which("gemini") is not None:
            return "gemini"
        return "agy"

    def prepare_workspace(self) -> dict[str, str]:
        self.restore_cli_auth_bundle()
        gemini_dir = Path.home() / ".gemini"
        gemini_dir.mkdir(parents=True, exist_ok=True)
        _write_gemini_trusted_folders(gemini_dir, self.working_dir)
        agy_cfg_dir = gemini_dir / "antigravity-cli"
        agy_cfg_dir.mkdir(parents=True, exist_ok=True)
        settings_path = agy_cfg_dir / "settings.json"
        agy_settings: dict[str, Any] = {}
        if settings_path.exists():
            try:
                loaded = json.loads(settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    agy_settings = loaded
            except Exception:
                pass
        if self.auth_mode == "cli_oauth":
            if self.binary_name == "gemini":
                agy_settings["selectedAuthType"] = "oauth-personal"
                agy_settings["security"] = {
                    "auth": {"selectedType": "oauth-personal"},
                    "folderTrust": {"enabled": False},
                }
        else:
            agy_settings.setdefault("modelProvider", "gemini")
            agy_settings.setdefault("enableAdc", False)
        normalized_model = _normalize_google_cli_model(self.model)
        if normalized_model:
            agy_settings["model"] = normalized_model
        else:
            agy_settings.pop("model", None)
        # Skip agy's interactive first-run flow (color scheme, ToS/data-use consent, workspace
        # trust) so sessions drop straight into the prompt. Data collection defaults to off.
        agy_settings["onboardingComplete"] = True
        agy_settings.setdefault("colorScheme", "dark")
        agy_settings.setdefault("telemetryEnabled", False)
        trusted = agy_settings.get("trustedWorkspaces")
        trusted_list = [str(p) for p in trusted] if isinstance(trusted, list) else []
        for ws_path in dict.fromkeys([str(self.working_dir), str(Path.cwd()), "/workspace"]):
            if ws_path not in trusted_list:
                trusted_list.append(ws_path)
        agy_settings["trustedWorkspaces"] = trusted_list
        mcp_map = self._build_standard_mcp_map()
        if mcp_map:
            existing_mcp = agy_settings.get("mcpServers")
            if isinstance(existing_mcp, dict):
                agy_settings["mcpServers"] = {**existing_mcp, **mcp_map}
            else:
                agy_settings["mcpServers"] = mcp_map
            _write_antigravity_mcp_config(gemini_dir, mcp_map)
        settings_path.write_text(
            json.dumps(agy_settings, indent=2), encoding="utf-8"
        )
        # Keep ~/.gemini/settings.json synchronized for OAuth fallback and MCP discovery
        if self.auth_mode == "cli_oauth" or mcp_map:
            gem_settings_path = gemini_dir / "settings.json"
            gem_settings: dict[str, Any] = {}
            if gem_settings_path.exists():
                with contextlib.suppress(Exception):
                    loaded_gem = json.loads(gem_settings_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_gem, dict):
                        gem_settings = loaded_gem
            if self.auth_mode == "cli_oauth":
                gem_settings["selectedAuthType"] = "oauth-personal"
                gem_settings["security"] = {
                    "auth": {"selectedType": "oauth-personal"},
                    "folderTrust": {"enabled": False},
                }
            if mcp_map:
                gem_mcp = _format_gemini_cli_mcp_map(mcp_map)
                existing_gem_mcp = gem_settings.get("mcpServers")
                if isinstance(existing_gem_mcp, dict):
                    gem_settings["mcpServers"] = {**existing_gem_mcp, **gem_mcp}
                else:
                    gem_settings["mcpServers"] = gem_mcp
            gem_settings_path.write_text(
                json.dumps(gem_settings, indent=2), encoding="utf-8"
            )

        env = os.environ.copy()
        if self.auth_mode == "cli_oauth":
            env.pop("GEMINI_API_KEY", None)
            env.pop("GOOGLE_GEMINI_BASE_URL", None)
            if self.oauth_token:
                env["GOOGLE_CLOUD_ACCESS_TOKEN"] = self.oauth_token
                env["GOOGLE_GENAI_USE_GCA"] = "true"
            return env

        env["GEMINI_API_KEY"] = self.gateway_api_key
        env["GOOGLE_GEMINI_BASE_URL"] = self.gateway_anthropic_url
        env["OPENAI_BASE_URL"] = self.gateway_base_url
        env["OPENAI_API_KEY"] = self.gateway_api_key
        env["ANTHROPIC_BASE_URL"] = self.gateway_anthropic_url
        env["ANTHROPIC_AUTH_TOKEN"] = self.gateway_api_key
        return env

    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        normalized_model = _normalize_google_cli_model(self.model)
        ws_root = self._multi_repo_workspace_root()
        if self.binary_name == "gemini":
            cmd = [
                "gemini",
                "-p",
                prompt,
                "--output-format",
                "stream-json",
                "--yolo",
            ]
            if ws_root is not None:
                cmd.extend(["--include-directories", str(ws_root)])
            if normalized_model:
                cmd.extend(["--model", normalized_model])
            if continue_session:
                cmd.extend(["--resume", "latest"])
            return cmd

        cmd = [
            "agy",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--dangerously-skip-permissions",
        ]
        if normalized_model:
            cmd.extend(["--model", normalized_model])
        max_turns = self._resolve_max_turns()
        if max_turns is not None:
            cmd.extend(["--max-turns", str(max_turns)])
        if continue_session:
            cmd.append("--continue")
        return cmd

    def build_interactive_command(self) -> list[str]:
        normalized_model = _normalize_google_cli_model(self.model)
        ws_root = self._multi_repo_workspace_root()
        if self.binary_name == "gemini":
            cmd = ["gemini", "--yolo"]
            if ws_root is not None:
                cmd.extend(["--include-directories", str(ws_root)])
            if normalized_model:
                cmd.extend(["--model", normalized_model])
            return cmd
        cmd = ["agy", "--dangerously-skip-permissions"]
        if normalized_model:
            cmd.extend(["--model", normalized_model])
        return cmd


class GeminiAdapter(RuntimeAdapter):
    """Adapter for the Google Gemini CLI (`gemini -p --yolo`)."""

    @property
    def binary_name(self) -> str:
        has_gemini_creds = any(
            k.lstrip("/\\") in (".gemini/oauth_creds.json", "gemini/oauth_creds.json")
            for k in self.cli_auth_bundle
        )
        has_agy_creds = any(
            "antigravity-oauth-token" in k or "jetski-standalone-oauth-token" in k
            for k in self.cli_auth_bundle
        )
        if (
            self.auth_mode == "cli_oauth"
            and not has_gemini_creds
            and has_agy_creds
            and shutil.which("agy") is not None
        ):
            return "agy"
        return "gemini"

    def prepare_workspace(self) -> dict[str, str]:
        self.restore_cli_auth_bundle()
        gemini_cfg_dir = Path.home() / ".gemini"
        gemini_cfg_dir.mkdir(parents=True, exist_ok=True)
        _write_gemini_trusted_folders(gemini_cfg_dir, self.working_dir)
        settings_path = gemini_cfg_dir / "settings.json"
        gemini_settings: dict[str, Any] = {}
        if settings_path.exists():
            try:
                loaded = json.loads(settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    gemini_settings = loaded
            except Exception:
                pass
        if self.auth_mode == "cli_oauth":
            gemini_settings["selectedAuthType"] = "oauth-personal"
            gemini_settings["security"] = {
                "auth": {"selectedType": "oauth-personal"},
                "folderTrust": {"enabled": False},
            }
        else:
            sec = gemini_settings.get("security")
            sec_dict = dict(sec) if isinstance(sec, dict) else {}
            sec_dict["folderTrust"] = {"enabled": False}
            gemini_settings["security"] = sec_dict
        mcp_map = self._build_standard_mcp_map()
        if mcp_map:
            gemini_mcp = _format_gemini_cli_mcp_map(mcp_map)
            existing_mcp = gemini_settings.get("mcpServers")
            if isinstance(existing_mcp, dict):
                gemini_settings["mcpServers"] = {**existing_mcp, **gemini_mcp}
            else:
                gemini_settings["mcpServers"] = gemini_mcp
            _write_antigravity_mcp_config(gemini_cfg_dir, mcp_map)
        settings_path.write_text(
            json.dumps(gemini_settings, indent=2), encoding="utf-8"
        )

        # Also configure ~/.gemini/antigravity-cli/settings.json when backed by Antigravity OAuth
        agy_cfg_dir = gemini_cfg_dir / "antigravity-cli"
        agy_cfg_dir.mkdir(parents=True, exist_ok=True)
        agy_settings_path = agy_cfg_dir / "settings.json"
        agy_settings: dict[str, Any] = {
            "onboardingComplete": True,
            "colorScheme": "dark",
            "telemetryEnabled": False,
            "trustedWorkspaces": list(
                dict.fromkeys([str(self.working_dir), str(Path.cwd()), "/workspace"])
            ),
        }
        normalized_model = _normalize_google_cli_model(self.model)
        if normalized_model:
            agy_settings["model"] = normalized_model
        if mcp_map:
            agy_settings["mcpServers"] = mcp_map
        agy_settings_path.write_text(
            json.dumps(agy_settings, indent=2), encoding="utf-8"
        )

        env = os.environ.copy()
        if self.auth_mode == "cli_oauth":
            env.pop("GEMINI_API_KEY", None)
            env.pop("GOOGLE_GEMINI_BASE_URL", None)
            if self.oauth_token:
                env["GOOGLE_CLOUD_ACCESS_TOKEN"] = self.oauth_token
                env["GOOGLE_GENAI_USE_GCA"] = "true"
            return env

        env["GEMINI_API_KEY"] = self.gateway_api_key
        env["GOOGLE_GEMINI_BASE_URL"] = self.gateway_anthropic_url
        env["OPENAI_BASE_URL"] = self.gateway_base_url
        env["OPENAI_API_KEY"] = self.gateway_api_key
        return env

    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        normalized_model = _normalize_google_cli_model(self.model)
        if self.binary_name == "agy":
            cmd = [
                "agy",
                "-p",
                prompt,
                "--output-format",
                "stream-json",
                "--dangerously-skip-permissions",
            ]
            if normalized_model:
                cmd.extend(["--model", normalized_model])
            if continue_session:
                cmd.append("--continue")
            return cmd

        cmd = [
            "gemini",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--yolo",
        ]
        ws_root = self._multi_repo_workspace_root()
        if ws_root is not None:
            cmd.extend(["--include-directories", str(ws_root)])
        if normalized_model:
            cmd.extend(["--model", normalized_model])
        if continue_session:
            cmd.extend(["--resume", "latest"])
        return cmd

    def build_interactive_command(self) -> list[str]:
        normalized_model = _normalize_google_cli_model(self.model)
        if self.binary_name == "agy":
            cmd = ["agy", "--dangerously-skip-permissions"]
            if normalized_model:
                cmd.extend(["--model", normalized_model])
            return cmd
        cmd = ["gemini", "--yolo"]
        ws_root = self._multi_repo_workspace_root()
        if ws_root is not None:
            cmd.extend(["--include-directories", str(ws_root)])
        if normalized_model:
            cmd.extend(["--model", normalized_model])
        return cmd


class GrokBuildAdapter(RuntimeAdapter):
    """Adapter for the xAI Grok Build CLI (`grok -p --full-auto`)."""

    @property
    def binary_name(self) -> str:
        return "grok"

    def prepare_workspace(self) -> dict[str, str]:
        self.restore_cli_auth_bundle()
        grok_cfg_dir = Path.home() / ".grok"
        grok_cfg_dir.mkdir(parents=True, exist_ok=True)
        env = os.environ.copy()
        mcp_map = self._build_standard_mcp_map()
        if self.auth_mode == "cli_oauth":
            grok_oauth_settings: dict[str, Any] = {}
            grok_oauth_user_settings: dict[str, Any] = {}
            if self.oauth_token:
                grok_oauth_user_settings["apiKey"] = self.oauth_token
            if self.model and self.model != "default":
                grok_oauth_settings["model"] = self.model
                grok_oauth_user_settings["defaultModel"] = self.model
            if mcp_map:
                grok_oauth_settings["mcpServers"] = mcp_map
                grok_oauth_user_settings["mcpServers"] = mcp_map
            if grok_oauth_settings:
                (grok_cfg_dir / "settings.json").write_text(
                    json.dumps(grok_oauth_settings, indent=2), encoding="utf-8"
                )
            if grok_oauth_user_settings:
                (grok_cfg_dir / "user-settings.json").write_text(
                    json.dumps(grok_oauth_user_settings, indent=2), encoding="utf-8"
                )
            if self.oauth_token:
                env["XAI_API_KEY"] = self.oauth_token
                env["GROK_API_KEY"] = self.oauth_token
            return env

        grok_settings: dict[str, Any] = {
            "baseURL": self.gateway_base_url,
            "apiKey": self.gateway_api_key,
        }
        grok_user_settings: dict[str, Any] = {
            "apiKey": self.gateway_api_key,
            "baseURL": self.gateway_base_url,
        }
        if self.model and self.model != "default":
            grok_settings["model"] = self.model
            grok_user_settings["defaultModel"] = self.model
        if mcp_map:
            grok_settings["mcpServers"] = mcp_map
            grok_user_settings["mcpServers"] = mcp_map
        (grok_cfg_dir / "settings.json").write_text(
            json.dumps(grok_settings, indent=2), encoding="utf-8"
        )
        (grok_cfg_dir / "user-settings.json").write_text(
            json.dumps(grok_user_settings, indent=2), encoding="utf-8"
        )

        env["XAI_API_KEY"] = self.gateway_api_key
        env["XAI_BASE_URL"] = self.gateway_base_url
        env["GROK_API_KEY"] = self.gateway_api_key
        env["GROK_BASE_URL"] = self.gateway_base_url
        env["OPENAI_BASE_URL"] = self.gateway_base_url
        env["OPENAI_API_KEY"] = self.gateway_api_key
        return env

    def build_command(self, prompt: str, *, continue_session: bool = False) -> list[str]:
        cmd = [
            "grok",
            "-p",
            prompt,
            "--full-auto",
        ]
        if self.model and self.model != "default":
            cmd.extend(["--model", self.model])
        max_turns = self._resolve_max_turns()
        if max_turns is not None:
            cmd.extend(["--max-tool-rounds", str(max_turns)])
        if continue_session:
            cmd.append("--continue")
        return cmd

    def build_interactive_command(self) -> list[str]:
        cmd = ["grok", "--full-auto"]
        if self.model and self.model != "default":
            cmd.extend(["--model", self.model])
        return cmd


def create_runtime_adapter(
    *,
    ctx: dict[str, Any],
    working_dir: Path,
    output_file: Path,
) -> RuntimeAdapter:
    runtime = str(ctx.get("runtime") or "opencode").strip().lower()
    if runtime == "claude-code":
        return ClaudeCodeAdapter(
            ctx=ctx, working_dir=working_dir, output_file=output_file
        )
    if runtime == "codex":
        return CodexAdapter(
            ctx=ctx, working_dir=working_dir, output_file=output_file
        )
    if runtime == "antigravity":
        return AntigravityAdapter(
            ctx=ctx, working_dir=working_dir, output_file=output_file
        )
    if runtime == "gemini":
        return GeminiAdapter(
            ctx=ctx, working_dir=working_dir, output_file=output_file
        )
    if runtime == "grok-build":
        return GrokBuildAdapter(
            ctx=ctx, working_dir=working_dir, output_file=output_file
        )
    return OpenCodeAdapter(
        ctx=ctx, working_dir=working_dir, output_file=output_file
    )


def _summarize_workspace_tree(
    working_dir: Path,
    max_files: int = 80,
    prompt_hint: str = "",
) -> str:
    """Collect a compact repository file tree, README excerpt, and any prompt-referenced file contents."""
    entries: list[str] = []
    readme_excerpt = ""
    for root, dirs, files in os.walk(working_dir):
        dirs[:] = [
            d
            for d in sorted(dirs)
            if d not in {".git", ".gitseed", "node_modules", ".venv", "__pycache__", "dist"}
        ]
        rel_root = Path(root).relative_to(working_dir)
        for f in sorted(files):
            if f == "opencode.json":
                continue
            rel_path = rel_root / f if str(rel_root) != "." else Path(f)
            entries.append(rel_path.as_posix())
            if (
                not readme_excerpt
                and f.lower() in {"readme.md", "pyproject.toml", "package.json"}
            ):
                try:
                    readme_excerpt = (Path(root) / f).read_text(
                        encoding="utf-8", errors="ignore"
                    )[:2500]
                except Exception:
                    pass
            if len(entries) >= max_files:
                break
        if len(entries) >= max_files:
            break

    summary = "## Workspace File Tree\n" + ("\n".join(f"- `{p}`" for p in entries) or "(empty)")
    if readme_excerpt:
        summary += f"\n\n## Key Project File Excerpt\n```\n{readme_excerpt}\n```"

    if prompt_hint and entries:
        referenced_snippets: list[str] = []
        total_chars = 0
        for rel_p in entries:
            basename = PurePosixPath(rel_p).name if "/" in rel_p else rel_p
            if rel_p in prompt_hint or (len(basename) > 4 and basename in prompt_hint):
                full_p = working_dir / rel_p
                if full_p.is_file():
                    try:
                        text = full_p.read_text(encoding="utf-8", errors="ignore")[:3000]
                        if text.strip():
                            referenced_snippets.append(f"### `{rel_p}`\n```\n{text}\n```")
                            total_chars += len(text)
                            if len(referenced_snippets) >= 5 or total_chars >= 10000:
                                break
                    except Exception:
                        pass
        if referenced_snippets:
            summary += "\n\n## Referenced Workspace File Contents\n" + "\n\n".join(
                referenced_snippets
            )
    return summary


def extract_json_object(raw_text: str) -> dict[str, Any] | None:
    cleaned = raw_text.strip()
    if not cleaned:
        return None
    fence_blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, re.DOTALL)
    for block in reversed(fence_blocks):
        try:
            obj = json.loads(block)
            if isinstance(obj, dict):
                return obj
        except Exception:
            continue

    decoder = json.JSONDecoder()
    last_dict: dict[str, Any] | None = None
    idx = cleaned.find("{")
    while idx != -1:
        try:
            obj, end_idx = decoder.raw_decode(cleaned, idx)
            if isinstance(obj, dict):
                last_dict = obj
                idx = cleaned.find("{", max(idx + 1, end_idx))
                continue
        except Exception:
            pass
        idx = cleaned.find("{", idx + 1)
    return last_dict


def _apply_synthesized_files(
    working_dir: Path,
    parsed: dict[str, Any],
    *,
    allow_overwrite: bool = False,
) -> list[str]:
    """If the synthesized JSON includes a `files` array during execute, safely write them to disk."""
    raw_files = parsed.pop("files", None)
    if not isinstance(raw_files, list):
        return []
    written: list[str] = []
    root_resolved = working_dir.resolve()
    for item in raw_files:
        if not isinstance(item, dict):
            continue
        rel_path = str(item.get("path") or "").strip().lstrip("/\\")
        content = item.get("content")
        if not rel_path or not isinstance(content, str):
            continue
        raw_target = working_dir / rel_path
        if raw_target.is_symlink():
            continue
        target = raw_target.resolve()
        if not target.is_relative_to(root_resolved) or target == root_resolved:
            continue
        rel_parts = target.relative_to(root_resolved).parts
        if any(part.lower() == ".git" for part in rel_parts):
            continue
        if (
            target.exists()
            and target.is_file()
            and target.stat().st_size > 0
            and not allow_overwrite
            and not bool(item.get("overwrite", False))
        ):
            logger.warning(
                "Skipping blind gateway overwrite of existing non-empty file: %s",
                rel_path,
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(rel_path)
        logger.info("Wrote synthesized workspace file: %s", rel_path)
    return written


_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[a-zA-Z]")

_FATAL_AGENT_ERROR_PATTERNS: tuple[tuple[str, str], ...] = (
    # (regex, category) checked against the agent CLI's own error output
    (r"RESOURCE_EXHAUSTED|quota (reached|exceeded)|usage limit|insufficient_quota|credit balance", "quota"),
    (r"\b429\b.*(rate|limit|quota)|rate[_ ]limit(ed)?\b|too many requests", "rate_limit"),
    (
        r"UNAUTHENTICATED|authentication_error|invalid (api|x-api) key|invalid_api_key|not (signed|logged) in|"
        r"please (log|sign) in|token (has )?expired|\b401\b.*unauthori[sz]ed|"
        r"FatalAuthenticationError|Manual authorization is required|OAuth token refresh failed|unauthorized_client",
        "auth",
    ),
    (r"PERMISSION_DENIED|\b403\b.*forbidden", "permission"),
    (r"(?:unknown|unrecognized|invalid)\s+(?:option|flag|argument)\b", "cli_flag"),
)


def detect_fatal_agent_error(output: str) -> str | None:
    """Return a short, human message if the agent CLI stopped on a provider error.

    Looks for the CLIs' structured error reports first (agy `AGY_ERROR`, stream-json
    `result` with status ERROR / is_error, `error:` lines), then known quota/auth/rate-limit
    phrasing in those error lines. Returns None for normal output.
    """
    if not output:
        return None
    text = output[-20000:]
    candidates: list[str] = []

    for line in text.splitlines():
        stripped = _ANSI_ESCAPE_RE.sub("", line).strip()
        if not stripped:
            continue
        if stripped.startswith("AGY_ERROR:"):
            try:
                payload = json.loads(stripped.split(":", 1)[1].strip())
                msg = payload.get("short_error") or payload.get("error") or ""
                if msg:
                    candidates.append(str(msg))
                    continue
            except Exception:
                pass
            candidates.append(stripped.split(":", 1)[1].strip())
        elif stripped.startswith("{") and ('"status":"ERROR"' in stripped.replace(" ", "") or '"is_error":true' in stripped.replace(" ", "")):
            with contextlib.suppress(Exception):
                obj = json.loads(stripped)
                res = obj.get("result") if isinstance(obj.get("result"), dict) else obj
                msg = res.get("error") or res.get("result") or res.get("message")
                if isinstance(msg, str) and msg.strip():
                    candidates.append(msg.strip())
        elif re.match(r"^(error|Error|ERROR)\b.*[:!]\s+\S", stripped):
            candidates.append(stripped.split(":", 1)[-1].strip())
        elif re.search(
            r"\b(FatalAuthenticationError|FatalConfigError|Manual authorization is required)\b",
            stripped,
        ):
            candidates.append(stripped)

    for msg in candidates:
        for pattern, _category in _FATAL_AGENT_ERROR_PATTERNS:
            if re.search(pattern, msg, flags=re.IGNORECASE):
                return msg[:400]
    return None


def synthesize_stage_output_from_text(
    stage: str,
    raw_text: str,
) -> dict[str, Any] | None:
    """Synthesize a safe fallback stage output dict when output.json was not written.

    NEVER fabricates `approved=True` for `review` or `merged=True` for `merge`.
    """
    cleaned_stdout = (raw_text or "").strip()
    st = (stage or "").strip().lower()
    if detect_fatal_agent_error(cleaned_stdout):
        return None
    if cleaned_stdout and re.match(r"^usage:\s+\S+", cleaned_stdout, re.IGNORECASE):
        return None

    summary_excerpt = cleaned_stdout[-4000:]
    has_negative_verdict = bool(
        re.search(
            r"\b(not approved|changes requested|reject(?:ed)?|blocking issue|failed)\b",
            cleaned_stdout,
            re.IGNORECASE,
        )
    )

    if st == "review":
        blocking_finding = (
            "[blocking] Reviewer agent exited without writing `.gitseed/output.json`; "
            "automatic approval is refused."
        )
        findings = (
            [summary_excerpt[:500]]
            if (has_negative_verdict and summary_excerpt)
            else [blocking_finding]
        )
        return {
            "approved": False,
            "findings": findings,
            "summary_comment": (
                summary_excerpt if len(summary_excerpt) >= 5 else blocking_finding
            ),
        }

    if st == "merge":
        merge_note = (
            "Merge agent exited without writing `.gitseed/output.json`; "
            "automatic merge confirmation is refused."
        )
        return {
            "merged": False,
            "merge_commit_sha": None,
            "summary_comment": summary_excerpt if len(summary_excerpt) >= 5 else merge_note,
        }

    if len(cleaned_stdout) < 15:
        return None

    if st == "ideate":
        return {
            "specification": (
                cleaned_stdout[-8000:]
                if len(cleaned_stdout) >= 10
                else f"Specification:\n\n{cleaned_stdout}"
            ),
            "acceptance_criteria": [
                "Verify implementation satisfies the specification and passes tests."
            ],
            "summary_comment": summary_excerpt,
        }
    if st == "plan":
        return {
            "plan": (
                cleaned_stdout[-8000:]
                if len(cleaned_stdout) >= 10
                else f"Implementation Plan:\n\n{cleaned_stdout}"
            ),
            "tasks": [
                {
                    "title": "Implement planned changes",
                    "description": summary_excerpt[:2000],
                }
            ],
            "target_repositories": [],
            "summary_comment": summary_excerpt,
        }
    if st == "execute":
        return {
            "summary_comment": summary_excerpt,
            "branch_name": None,
            "commit_sha": None,
            "pull_request_url": None,
        }
    return None


async def synthesize_output_via_gateway(
    *,
    adapter: RuntimeAdapter,
    prompt: str,
    cli_stdout: str = "",
    stage: str = "",
    require_files: bool = False,
) -> bool:
    """Direct LiteLLM proxy synthesis if the CLI exited without writing output.json.

    Ensures any runtime (`opencode`, `claude-code`, `codex`, `antigravity`, `gemini`,
    `grok-build`) paired with any model backend (including local LM Studio / Ollama models)
    produces a valid /workspace/.gitseed/output.json routed through LiteLLM.
    """
    repo_context = _summarize_workspace_tree(adapter.working_dir, prompt_hint=prompt)
    system_msg = (
        "You are a GitSeed stage execution agent. Respond ONLY with a single valid JSON "
        "object matching the required stage output schema. Do not wrap in commentary."
    )
    user_msg = f"{prompt}\n\n{repo_context}"
    if stage == "execute" and require_files:
        user_msg += (
            "\n\n## CRITICAL EXECUTE REQUIREMENT\n"
            "No source files were written to `/workspace` yet. In addition to the required "
            "stage output fields (`summary_comment`, `branch_name`, `commit_sha`, `pull_request_url`), "
            "you MUST include a `\"files\"` array in your JSON object containing the complete "
            "implementation and test files to create or update in `/workspace`:\n"
            "```json\n"
            "{\n"
            "  \"files\": [\n"
            "    {\"path\": \"relative/path/to/file.ts\", \"content\": \"...full file content...\"}\n"
            "  ],\n"
            "  \"summary_comment\": \"Implemented ...\",\n"
            "  \"branch_name\": \"gitseed/seed-1\",\n"
            "  \"commit_sha\": null,\n"
            "  \"pull_request_url\": null\n"
            "}\n"
            "```"
        )
    if cli_stdout.strip():
        user_msg += f"\n\n## Prior Agent CLI Output\n```\n{cli_stdout[-3000:]}\n```"

    gateway_key = (adapter.gateway_api_key or "").strip()
    gateway_url = (adapter.gateway_base_url or "").strip()
    can_use_gateway = (
        adapter.auth_mode != "cli_oauth" and bool(gateway_key) and bool(gateway_url)
    )

    if can_use_gateway:
        headers = {
            "Authorization": f"Bearer {gateway_key}",
            "x-api-key": gateway_key,
            "Content-Type": "application/json",
        }

        async with httpx.AsyncClient(timeout=180.0) as client:
            # 1. Primary path: LiteLLM unified OpenAI-compatible /v1/chat/completions
            chat_url = f"{gateway_url}/chat/completions"
            logger.info(
                "Synthesizing stage output via Model Gateway (model=%s, stage=%s)...",
                adapter.gateway_model,
                stage or "unknown",
            )
            chat_payload: dict[str, Any] = {
                "model": adapter.gateway_model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
                "temperature": 0.2,
            }
            try:
                resp = await client.post(chat_url, headers=headers, json=chat_payload)
                if 200 <= resp.status_code < 300:
                    data = resp.json()
                    usage = data.get("usage") if isinstance(data, dict) else None
                    if isinstance(usage, dict):
                        in_t = max(
                            0,
                            int(
                                usage.get("prompt_tokens")
                                or usage.get("input_tokens")
                                or 0
                            ),
                        )
                        out_t = max(
                            0,
                            int(
                                usage.get("completion_tokens")
                                or usage.get("output_tokens")
                                or 0
                            ),
                        )
                        adapter.finish_invocation()
                        adapter.record_stream_usage(in_t, out_t, cumulative=False)
                        adapter.finish_invocation()
                    choices = data.get("choices") or []
                    if choices and isinstance(choices[0], dict):
                        msg = choices[0].get("message") or {}
                        text = str(msg.get("content") or "")
                        parsed = extract_json_object(text)
                        if parsed is not None:
                            _apply_synthesized_files(adapter.working_dir, parsed)
                            adapter.output_file.parent.mkdir(parents=True, exist_ok=True)
                            adapter.output_file.write_text(
                                json.dumps(parsed, indent=2), encoding="utf-8"
                            )
                            adapter.synthesized_via = "gateway"
                            return True
                else:
                    logger.warning(
                        "LiteLLM /chat/completions synthesis returned %s: %s",
                        resp.status_code,
                        resp.text[:300],
                    )
            except Exception as exc:
                logger.warning("LiteLLM /chat/completions synthesis error: %s", exc)

            # 2. Secondary fallback for direct Anthropic /v1/messages pass-through
            if adapter.provider == "anthropic":
                msg_url = f"{adapter.gateway_anthropic_url}/v1/messages"
                msg_payload: dict[str, Any] = {
                    "model": adapter.gateway_model,
                    "max_tokens": 4096,
                    "system": system_msg,
                    "messages": [{"role": "user", "content": user_msg}],
                }
                try:
                    resp = await client.post(msg_url, headers=headers, json=msg_payload)
                    if 200 <= resp.status_code < 300:
                        data = resp.json()
                        usage = data.get("usage") if isinstance(data, dict) else None
                        if isinstance(usage, dict):
                            in_t = max(
                                0,
                                int(
                                    usage.get("input_tokens")
                                    or usage.get("prompt_tokens")
                                    or 0
                                ),
                            )
                            out_t = max(
                                0,
                                int(
                                    usage.get("output_tokens")
                                    or usage.get("completion_tokens")
                                    or 0
                                ),
                            )
                            adapter.finish_invocation()
                            adapter.record_stream_usage(in_t, out_t, cumulative=False)
                            adapter.finish_invocation()
                        content_blocks = data.get("content") or []
                        text_parts = [
                            b.get("text", "")
                            for b in content_blocks
                            if isinstance(b, dict) and b.get("type") == "text"
                        ]
                        parsed = extract_json_object("\n".join(text_parts))
                        if parsed is not None:
                            _apply_synthesized_files(adapter.working_dir, parsed)
                            adapter.output_file.parent.mkdir(parents=True, exist_ok=True)
                            adapter.output_file.write_text(
                                json.dumps(parsed, indent=2), encoding="utf-8"
                            )
                            adapter.synthesized_via = "gateway"
                            return True
                except Exception as exc:
                    logger.warning("LiteLLM /v1/messages synthesis error: %s", exc)

    # 3. Local fallback when CLI OAuth is active or no gateway API key is configured
    cleaned_stdout = cli_stdout.strip()
    # Never turn an agent *error* (quota, auth, rate limit…) into stage output
    if detect_fatal_agent_error(cleaned_stdout):
        return False
    if (adapter.auth_mode == "cli_oauth" or not gateway_key) and cleaned_stdout and not require_files:
        embedded_json = extract_json_object(cleaned_stdout)
        if embedded_json is not None:
            adapter.output_file.parent.mkdir(parents=True, exist_ok=True)
            adapter.output_file.write_text(
                json.dumps(embedded_json, indent=2), encoding="utf-8"
            )
            adapter.synthesized_via = "cli_oauth_json"
            return True

        # Reject CLI usage/help dumps or trivially short output from being wrapped as valid prose
        if len(cleaned_stdout) < 15 or re.match(r"^usage:\s+\S+", cleaned_stdout, re.IGNORECASE):
            return False

        fallback_obj = synthesize_stage_output_from_text(stage, cleaned_stdout)
        if fallback_obj is not None:
            adapter.output_file.parent.mkdir(parents=True, exist_ok=True)
            adapter.output_file.write_text(
                json.dumps(fallback_obj, indent=2), encoding="utf-8"
            )
            adapter.synthesized_via = "cli_oauth_stdout"
            logger.warning(
                "Synthesized %s stage output from raw CLI OAuth stdout (CLI did not write output.json).",
                stage or "unknown",
            )
            return True

    return False

