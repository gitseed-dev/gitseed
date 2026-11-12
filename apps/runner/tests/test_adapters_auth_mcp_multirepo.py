"""Unit tests for CLI auth path parity, OpenCode cli_oauth mode, MCP transports, and multi-repo workspaces."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from gitseed_runner.adapters import (
    ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME,
    AntigravityAdapter,
    CodexAdapter,
    GeminiAdapter,
    GrokBuildAdapter,
    OpenCodeAdapter,
)
from gitseed_runner.prompts import build_stage_prompt


def test_allowed_cli_auth_paths_gemini_and_antigravity_exact_parity() -> None:
    expected_gemini = frozenset(
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
    )
    expected_antigravity = frozenset(
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
    )
    assert ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["gemini"] == expected_gemini
    assert ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME["antigravity"] == expected_antigravity


def test_opencode_adapter_personal_cli_oauth_mode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "work"
    home_dir.mkdir()
    work_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("OPENAI_BASE_URL", "http://stale-proxy:4000/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "stale-key")
    output_file = work_dir / ".gitseed" / "output.json"

    # 1. With no auth bundle file on disk, oauth_token falls back to provider env var
    ctx_anthropic: dict[str, Any] = {
        "runtime": "opencode",
        "provider": "anthropic",
        "model": "anthropic/claude-sonnet-4-5",
        "gateway": {
            "auth_mode": "cli_oauth",
            "base_url": "http://litellm:4000/v1",
            "api_key": "sk-unused-gateway",
            "oauth_token": "ant-oauth-tok-123",
        },
        "mcp_servers": [
            {
                "slug": "gitseed-tools",
                "transport": "http",
                "endpoint": "http://mcp:8000/mcp",
                "headers": {"Authorization": "Bearer mcp-tok"},
            }
        ],
    }
    oc_ant = OpenCodeAdapter(ctx=ctx_anthropic, working_dir=work_dir, output_file=output_file)
    env_ant = oc_ant.prepare_workspace()

    cfg = json.loads((work_dir / "opencode.json").read_text(encoding="utf-8"))
    assert "provider" not in cfg
    assert cfg["model"] == "anthropic/claude-sonnet-4-5"
    assert "small_model" not in cfg
    assert cfg["permission"] == {"edit": "allow", "bash": "allow", "webfetch": "allow"}
    assert cfg["mcp"]["gitseed-tools"]["url"] == "http://mcp:8000/mcp"
    assert "OPENAI_BASE_URL" not in env_ant
    assert "OPENAI_API_KEY" not in env_ant
    assert "ANTHROPIC_BASE_URL" not in env_ant
    assert env_ant["ANTHROPIC_AUTH_TOKEN"] == "ant-oauth-tok-123"
    assert env_ant["ANTHROPIC_API_KEY"] == "ant-oauth-tok-123"

    cmd = oc_ant.build_command("Implement task")
    assert cmd == [
        "opencode",
        "run",
        "--format",
        "json",
        "--model",
        "anthropic/claude-sonnet-4-5",
        "Implement task",
    ]

    # 2. Provider env fallbacks for openai, gemini, and xai when no auth file exists
    for prov, expected_var in [
        ("openai", "OPENAI_API_KEY"),
        ("gemini", "GEMINI_API_KEY"),
        ("xai", "XAI_API_KEY"),
    ]:
        oc_prov = OpenCodeAdapter(
            ctx={
                "runtime": "opencode",
                "provider": prov,
                "model": "default",
                "gateway": {"auth_mode": "cli_oauth", "oauth_token": f"tok-{prov}"},
            },
            working_dir=work_dir,
            output_file=output_file,
        )
        env_prov = oc_prov.prepare_workspace()
        assert env_prov[expected_var] == f"tok-{prov}"
        cfg_default = json.loads((work_dir / "opencode.json").read_text(encoding="utf-8"))
        assert "model" not in cfg_default
        assert oc_prov.build_command("Go") == ["opencode", "run", "--format", "json", "Go"]

    # 3. When cli_auth_bundle restores OpenCode's auth.json, oauth_token does not clobber env
    oc_bundle = OpenCodeAdapter(
        ctx={
            "runtime": "opencode",
            "provider": "anthropic",
            "model": "anthropic/claude-sonnet-4-5",
            "gateway": {
                "auth_mode": "cli_oauth",
                "oauth_token": "should-not-override-bundle",
                "cli_auth_bundle": {
                    ".local/share/opencode/auth.json": '{"anthropic": {"type": "oauth", "access": "tok"}}'
                },
            },
        },
        working_dir=work_dir,
        output_file=output_file,
    )
    env_bundle = oc_bundle.prepare_workspace()
    assert (home_dir / ".local" / "share" / "opencode" / "auth.json").is_file()
    assert "ANTHROPIC_API_KEY" not in env_bundle
    assert "ANTHROPIC_AUTH_TOKEN" not in env_bundle


def test_mcp_transport_http_vs_sse_across_standard_gemini_and_antigravity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "home"
    work_dir = tmp_path / "work"
    home_dir.mkdir()
    work_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    output_file = work_dir / ".gitseed" / "output.json"

    mcp_servers = [
        {
            "slug": "stream-mcp",
            "provider": "mcp-http",
            "endpoint": "http://mcp:8000/mcp",
            "headers": {"Authorization": "Bearer h-tok"},
        },
        {
            "slug": "stream-mcp-2",
            "transport": "streamable-http",
            "endpoint": "http://mcp2:8000/mcp",
        },
        {
            "slug": "sse-mcp",
            "provider": "mcp-sse",
            "endpoint": "http://mcp:8000/sse",
            "headers": {"X-Api-Key": "s-tok"},
        },
        {
            "slug": "stdio-mcp",
            "provider": "mcp-stdio",
            "endpoint": "npx -y @gitseed/mcp-stdio",
            "env": {"MCP_ENV": "1"},
        },
    ]

    # GeminiAdapter
    gem = GeminiAdapter(
        ctx={
            "runtime": "gemini",
            "model": "gemini-2.5-pro",
            "gateway": {
                "auth_mode": "api_key",
                "api_key": "sk-test",
                "base_url": "http://litellm:4000/v1",
            },
            "mcp_servers": mcp_servers,
        },
        working_dir=work_dir,
        output_file=output_file,
    )
    std_map = gem._build_standard_mcp_map()
    assert std_map["stream-mcp"]["type"] == "http"
    assert std_map["stream-mcp-2"]["type"] == "http"
    assert std_map["sse-mcp"]["type"] == "sse"
    assert std_map["stdio-mcp"]["type"] == "stdio"

    gem.prepare_workspace()
    gem_settings = json.loads(
        (home_dir / ".gemini" / "settings.json").read_text(encoding="utf-8")
    )
    gem_mcp = gem_settings["mcpServers"]
    assert gem_mcp["stream-mcp"] == {
        "httpUrl": "http://mcp:8000/mcp",
        "headers": {"Authorization": "Bearer h-tok"},
    }
    assert "url" not in gem_mcp["stream-mcp"]
    assert "type" not in gem_mcp["stream-mcp"]
    assert gem_mcp["sse-mcp"] == {
        "url": "http://mcp:8000/sse",
        "headers": {"X-Api-Key": "s-tok"},
    }
    assert "httpUrl" not in gem_mcp["sse-mcp"]
    assert gem_mcp["stdio-mcp"] == {
        "command": "npx",
        "args": ["-y", "@gitseed/mcp-stdio"],
        "env": {"MCP_ENV": "1"},
    }

    # AntigravityAdapter
    home_agy = tmp_path / "home_agy"
    home_agy.mkdir()
    monkeypatch.setenv("HOME", str(home_agy))
    monkeypatch.setenv("USERPROFILE", str(home_agy))
    agy = AntigravityAdapter(
        ctx={
            "runtime": "antigravity",
            "model": "gemini-2.5-pro",
            "gateway": {
                "auth_mode": "cli_oauth",
                "oauth_token": "ya29.test",
            },
            "mcp_servers": mcp_servers,
        },
        working_dir=work_dir,
        output_file=output_file,
    )
    agy.prepare_workspace()
    assert (home_agy / ".gemini" / "settings.json").is_file()
    mcp_cfg_path = home_agy / ".gemini" / "config" / "mcp_config.json"
    assert mcp_cfg_path.is_file()
    mcp_cfg = json.loads(mcp_cfg_path.read_text(encoding="utf-8"))["mcpServers"]
    assert mcp_cfg["stream-mcp"] == {
        "serverUrl": "http://mcp:8000/mcp",
        "headers": {"Authorization": "Bearer h-tok"},
    }
    assert mcp_cfg["sse-mcp"] == {
        "serverUrl": "http://mcp:8000/sse",
        "headers": {"X-Api-Key": "s-tok"},
    }
    assert mcp_cfg["stdio-mcp"] == {
        "command": "npx",
        "args": ["-y", "@gitseed/mcp-stdio"],
        "env": {"MCP_ENV": "1"},
    }


def test_multirepo_workspace_flags_grok_user_settings_and_prompt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    home_dir = tmp_path / "home"
    ws_root = tmp_path / "workspace"
    primary_repo = ws_root / "backend-api"
    sibling_repo = ws_root / "frontend-web"
    home_dir.mkdir()
    primary_repo.mkdir(parents=True)
    sibling_repo.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))

    output_file = ws_root / ".gitseed" / "output.json"

    # Single-repo layout: no extra --add-dir / --include-directories flags
    codex_single = CodexAdapter(
        ctx={"runtime": "codex", "model": "gpt-5-codex"},
        working_dir=ws_root,
        output_file=output_file,
    )
    assert "--add-dir" not in codex_single.build_command("Single repo")
    assert "--add-dir" not in codex_single.build_interactive_command()

    gem_single = GeminiAdapter(
        ctx={"runtime": "gemini", "model": "gemini-2.5-pro"},
        working_dir=ws_root,
        output_file=output_file,
    )
    assert "--include-directories" not in gem_single.build_command("Single repo")
    assert "--include-directories" not in gem_single.build_interactive_command()

    # Multi-repo layout: working_dir is /workspace/backend-api, output_file is /workspace/.gitseed/output.json
    codex_multi = CodexAdapter(
        ctx={"runtime": "codex", "model": "gpt-5-codex"},
        working_dir=primary_repo,
        output_file=output_file,
    )
    codex_cmd = codex_multi.build_command("Multi repo")
    codex_resume = codex_multi.build_command("Continue", continue_session=True)
    codex_tui = codex_multi.build_interactive_command()
    for argv in (codex_cmd, codex_resume, codex_tui):
        idx = argv.index("--add-dir")
        assert argv[idx + 1] == str(ws_root)

    gem_multi = GeminiAdapter(
        ctx={"runtime": "gemini", "model": "gemini-2.5-pro"},
        working_dir=primary_repo,
        output_file=output_file,
    )
    gem_cmd = gem_multi.build_command("Multi repo")
    gem_tui = gem_multi.build_interactive_command()
    for argv in (gem_cmd, gem_tui):
        idx = argv.index("--include-directories")
        assert argv[idx + 1] == str(ws_root)

    # GrokBuildAdapter writes both ~/.grok/settings.json and ~/.grok/user-settings.json
    grok = GrokBuildAdapter(
        ctx={
            "runtime": "grok-build",
            "model": "grok-4",
            "gateway": {
                "auth_mode": "api_key",
                "base_url": "http://litellm:4000/v1",
                "api_key": "sk-grok-virtual",
            },
            "mcp_servers": [
                {"slug": "tools", "transport": "http", "endpoint": "http://mcp:8000/mcp"}
            ],
        },
        working_dir=primary_repo,
        output_file=output_file,
    )
    grok.prepare_workspace()
    grok_settings = json.loads((home_dir / ".grok" / "settings.json").read_text(encoding="utf-8"))
    grok_user_settings = json.loads(
        (home_dir / ".grok" / "user-settings.json").read_text(encoding="utf-8")
    )
    assert grok_settings["apiKey"] == "sk-grok-virtual"
    assert grok_settings["baseURL"] == "http://litellm:4000/v1"
    assert grok_settings["model"] == "grok-4"
    assert grok_user_settings["apiKey"] == "sk-grok-virtual"
    assert grok_user_settings["baseURL"] == "http://litellm:4000/v1"
    assert grok_user_settings["defaultModel"] == "grok-4"
    assert "tools" in grok_user_settings["mcpServers"]

    # Multi-repo stage prompt mentions /workspace/<name>, ../<name>, and /workspace/.gitseed/output.json
    prompt = build_stage_prompt(
        {
            "stage": "execute",
            "seed_number": 7,
            "project_slug": "platform",
            "repositories": [
                {"full_name": "org/backend-api", "default_branch": "main", "is_primary": True},
                {"full_name": "org/frontend-web", "default_branch": "main", "is_primary": False},
            ],
        },
        Path("/workspace/.gitseed/output.json"),
        workspace_dir=ws_root,
    )
    assert "/workspace/backend-api" in prompt
    assert "/workspace/frontend-web" in prompt
    assert "../<name>" in prompt
    assert "/workspace/.gitseed/output.json" in prompt
