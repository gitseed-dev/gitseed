from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gitseed_runner.adapters import (
    AntigravityAdapter,
    ClaudeCodeAdapter,
    CodexAdapter,
    GeminiAdapter,
    GrokBuildAdapter,
    OpenCodeAdapter,
    _apply_synthesized_files,
    _summarize_workspace_tree,
    create_runtime_adapter,
    extract_json_object,
    synthesize_output_via_gateway,
)
from gitseed_runner.git_ops import (
    cleanup_git_locks,
    clone_project_repositories,
    create_task_worktree,
    enforce_blocked_paths,
    get_repo_heads,
    has_workspace_code_changes,
    matches_blocked_path,
    merge_and_remove_task_worktree,
    push_execute_changes_after_agent_exit,
    tasks_have_disjoint_targets,
)
from gitseed_runner.main import (
    _compute_dependency_fingerprint,
    _fetch_context,
    _format_cli_stream_line,
    _is_gateway_fallback_allowed,
    _load_and_validate_output_file,
    _poll_steering,
    _run_cli_with_steering,
    _run_deterministic_verification_gate,
    run_container_job,
)
from gitseed_runner.prompts import (
    build_ideate_to_plan_continuation_prompt,
    build_stage_prompt,
)


class TestGitOps:
    def test_clone_project_repositories_and_strip_credentials(
        self, tmp_path: Path
    ) -> None:
        git_calls: list[list[str]] = []

        def fake_run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            git_calls.append(args)
            if args[0] == "clone":
                target = Path(args[-1])
                (target / ".git" / "info").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(["git", *args], 0, stdout="abc1234\n", stderr="")

        # Pre-populate a file in workspace to verify single-repo cleanup
        (tmp_path / "stale.txt").write_text("old", encoding="utf-8")

        with patch("gitseed_runner.git_ops._run_git", side_effect=fake_run_git):
            primary_dir, repo_dirs = clone_project_repositories(
                tmp_path,
                [
                    {
                        "full_name": "org/api",
                        "clone_url": "https://x-access-token:secret@github.com/org/api.git",
                        "clean_remote_url": "https://github.com/org/api.git",
                        "default_branch": "main",
                        "is_primary": True,
                    }
                ],
                stage="execute",
                seed_number=7,
            )

        assert primary_dir == tmp_path
        assert "org/api" in repo_dirs
        assert not (tmp_path / "stale.txt").exists()
        assert any(
            c[:3] == ["remote", "set-url", "origin"]
            and c[3] == "https://github.com/org/api.git"
            for c in git_calls
        )
        exclude_text = (tmp_path / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        assert ".gitseed/output.json" in exclude_text
        assert "opencode.json" in exclude_text

    def test_get_repo_heads_and_has_workspace_code_changes(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        repo_dirs = {"org/api": tmp_path}

        def fake_git_clean(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[0] == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="sha_initial\n", stderr="")
            if args[0] == "status":
                return subprocess.CompletedProcess(
                    args, 0, stdout="?? .gitseed/output.json\n?? opencode.json\n", stderr=""
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=fake_git_clean):
            heads = get_repo_heads(repo_dirs)
            assert heads == {"org/api": "sha_initial"}
            assert not has_workspace_code_changes(repo_dirs, heads)

        def fake_git_dirty(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[0] == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="sha_initial\n", stderr="")
            if args[0] == "status":
                return subprocess.CompletedProcess(args, 0, stdout=" M src/app.py\n", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=fake_git_dirty):
            assert has_workspace_code_changes(repo_dirs, {"org/api": "sha_initial"})

    @pytest.mark.asyncio
    async def test_push_execute_changes_after_agent_exit(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "opencode.json").write_text("{}", encoding="utf-8")
        git_cmds: list[list[str]] = []

        def fake_run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            git_cmds.append(args)
            if args[0] == "status":
                return subprocess.CompletedProcess(args, 0, stdout=" M src/main.py\n", stderr="")
            if args[0] == "diff":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
            if args[0] == "rev-parse":
                return subprocess.CompletedProcess(args, 0, stdout="deadbeef1234\n", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        class _MockPushClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _MockPushClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "push_url": "https://x-access-token:tok@github.com/org/api.git",
                    "clean_remote_url": "https://github.com/org/api.git",
                }
                return resp

        with (
            patch("gitseed_runner.git_ops._run_git", side_effect=fake_run_git),
            patch("gitseed_runner.git_ops.httpx.AsyncClient", _MockPushClient),
        ):
            out = await push_execute_changes_after_agent_exit(
                repo_dirs={"org/api": tmp_path},
                seed_number=9,
                stage_output={"summary_comment": "Implemented feature"},
                api_url="http://api:8420",
                run_token="gsr_tok",
            )

        assert out["branch_name"] == "gitseed/seed-9"
        assert out["commit_sha"] == "deadbeef1234"
        assert not (tmp_path / "opencode.json").exists()
        assert any(c[0] == "commit" for c in git_cmds)
        assert any(c[0] == "push" for c in git_cmds)


class TestAdaptersAndSynthesis:
    def test_extract_json_object_and_summarize_workspace(
        self, tmp_path: Path
    ) -> None:
        # Fenced JSON
        fenced = 'Here is the output:\n```json\n{"approved": true, "nested": {"k": 1}}\n```'
        assert extract_json_object(fenced) == {"approved": True, "nested": {"k": 1}}

        # Bare JSON with surrounding text
        bare = 'Prefix text {"merged": true, "summary_comment": "ok"} trailing'
        assert extract_json_object(bare) == {"merged": True, "summary_comment": "ok"}

        # Invalid text
        assert extract_json_object("no braces here") is None
        assert extract_json_object("{invalid json}") is None

        # Workspace summary
        (tmp_path / "README.md").write_text("# Demo Project\nHello world", encoding="utf-8")
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "index.py").write_text("print(1)", encoding="utf-8")
        (tmp_path / "opencode.json").write_text("{}", encoding="utf-8")
        tree = _summarize_workspace_tree(tmp_path)
        assert "README.md" in tree
        assert "Demo Project" in tree
        assert "opencode.json" not in tree

        # _apply_synthesized_files writes valid paths and blocks traversal
        parsed_payload: dict[str, Any] = {
            "files": [
                {"path": "src/new_file.py", "content": "x = 42\n"},
                {"path": "../outside.py", "content": "bad"},
            ],
            "summary_comment": "Created file",
        }
        _apply_synthesized_files(tmp_path, parsed_payload)
        assert (tmp_path / "src" / "new_file.py").read_text(encoding="utf-8") == "x = 42\n"
        assert "files" not in parsed_payload

    @pytest.mark.parametrize(
        ("runtime", "expected_cls", "binary"),
        [
            ("opencode", OpenCodeAdapter, "opencode"),
            ("claude-code", ClaudeCodeAdapter, "claude"),
            ("codex", CodexAdapter, "codex"),
            ("antigravity", AntigravityAdapter, "agy"),
            ("gemini", GeminiAdapter, "gemini"),
            ("grok-build", GrokBuildAdapter, "grok"),
        ],
    )
    def test_all_adapters_prepare_workspace_and_commands_api_key_and_oauth(
        self,
        runtime: str,
        expected_cls: type,
        binary: str,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        output_file = tmp_path / ".gitseed" / "output.json"

        # 1. API key mode with MCP servers (both sse and stdio)
        ctx_api: dict[str, Any] = {
            "runtime": runtime,
            "model": "claude-sonnet-4-20250514",
            "gateway": {
                "provider": "anthropic",
                "base_url": "http://litellm:4000/v1",
                "api_key": "sk-gsr-test",
                "auth_mode": "api_key",
            },
            "mcp_servers": [
                {"slug": "docs", "provider": "mcp-sse", "endpoint": "http://mcp:8000/sse", "headers": {"X-Key": "1"}},
                {"slug": "local", "provider": "mcp-stdio", "endpoint": "npx -y @mcp/server", "env": {"FOO": "BAR"}},
            ],
        }
        adapter_api = create_runtime_adapter(
            ctx=ctx_api, working_dir=tmp_path, output_file=output_file
        )
        assert isinstance(adapter_api, expected_cls)
        env_api = adapter_api.prepare_workspace()
        assert isinstance(env_api, dict)
        cmd_init = adapter_api.build_command("Run task", continue_session=False)
        cmd_cont = adapter_api.build_command("Continue task", continue_session=True)
        assert cmd_init[0] in {binary, "gemini"}
        assert cmd_cont != cmd_init
        if runtime == "codex":
            assert "--json" in cmd_init
            assert "--json" in cmd_cont
            codex_toml = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
            assert "[mcp_servers.docs]" in codex_toml
            assert "[mcp_servers.local]" in codex_toml

        # 2. CLI OAuth mode with cli_auth_bundle and oauth_token
        ctx_oauth: dict[str, Any] = {
            "runtime": runtime,
            "model": "gpt-4o",
            "gateway": {
                "provider": "openai",
                "auth_mode": "cli_oauth",
                "oauth_token": "oauth-secret-token",
                "cli_auth_bundle": {
                    ".claude/.credentials.json": '{"token": "claude_tok"}',
                    "codex/auth.json": '{"access_token": "codex_tok"}',
                    ".gemini/oauth_creds.json": '{"refresh_token": "gem_tok"}',
                    ".grok/credentials.json": '{"key": "grok_tok"}',
                },
            },
        }
        adapter_oauth = create_runtime_adapter(
            ctx=ctx_oauth, working_dir=tmp_path, output_file=output_file
        )
        adapter_oauth.restore_cli_auth_bundle()
        env_oauth = adapter_oauth.prepare_workspace()
        assert isinstance(env_oauth, dict)
        collected = adapter_oauth.collect_updated_cli_auth_bundle()
        assert collected is not None
        assert ".claude/.credentials.json" in collected

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("stage", "expected_payload"),
        [
            (
                "ideate",
                {
                    "title": "Refined Seed",
                    "description": "Short summary",
                    "specification": "## Spec\nDetails here",
                    "acceptance_criteria": ["Check 1"],
                    "summary_comment": "Ideation complete",
                },
            ),
            (
                "plan",
                {
                    "plan": "## Plan\nSteps",
                    "tasks": [{"title": "Task 1", "description": "Do work"}],
                    "target_repositories": [],
                    "summary_comment": "Plan complete",
                },
            ),
            (
                "execute",
                {
                    "files": [{"path": "src/feature.py", "content": "val = 1\n"}],
                    "summary_comment": "Executed task",
                    "branch_name": "gitseed/seed-1",
                    "commit_sha": None,
                    "pull_request_url": None,
                },
            ),
            (
                "review",
                {
                    "approved": True,
                    "findings": [],
                    "summary_comment": "Tests pass",
                },
            ),
            (
                "merge",
                {
                    "merged": True,
                    "merge_commit_sha": None,
                    "summary_comment": "Ready to merge",
                },
            ),
        ],
    )
    async def test_synthesize_output_via_gateway_all_stages(
        self,
        stage: str,
        expected_payload: dict[str, Any],
        tmp_path: Path,
    ) -> None:
        output_file = tmp_path / ".gitseed" / "output.json"
        adapter = OpenCodeAdapter(
            ctx={
                "model": "gpt-4o",
                "gateway": {
                    "base_url": "http://litellm:4000/v1",
                    "api_key": "sk-gsr-123",
                },
            },
            working_dir=tmp_path,
            output_file=output_file,
        )

        class _MockGatewayClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _MockGatewayClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 200
                resp.json.return_value = {
                    "usage": {"prompt_tokens": 50, "completion_tokens": 30},
                    "choices": [
                        {
                            "message": {
                                "content": "```json\n" + json.dumps(expected_payload) + "\n```"
                            }
                        }
                    ],
                }
                return resp

        with patch("gitseed_runner.adapters.httpx.AsyncClient", _MockGatewayClient):
            ok = await synthesize_output_via_gateway(
                adapter=adapter,
                prompt=f"Synthesize {stage}",
                cli_stdout="prior output",
                stage=stage,
                require_files=(stage == "execute"),
            )

        assert ok is True
        assert output_file.exists()
        assert adapter.input_tokens == 50
        assert adapter.output_tokens == 30
        if stage == "execute":
            assert (tmp_path / "src" / "feature.py").exists()

    @pytest.mark.asyncio
    @pytest.mark.parametrize("stage", ["ideate", "plan", "execute", "review", "merge"])
    async def test_synthesize_output_cli_oauth_stdout_fallback(
        self, stage: str, tmp_path: Path
    ) -> None:
        output_file = tmp_path / ".gitseed" / "output.json"
        adapter = ClaudeCodeAdapter(
            ctx={
                "model": "claude-sonnet-4-20250514",
                "gateway": {"auth_mode": "cli_oauth"},
            },
            working_dir=tmp_path,
            output_file=output_file,
        )

        class _MockFailGateway:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _MockFailGateway:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 500
                resp.text = "gateway error"
                return resp

        with patch("gitseed_runner.adapters.httpx.AsyncClient", _MockFailGateway):
            ok = await synthesize_output_via_gateway(
                adapter=adapter,
                prompt="Run stage",
                cli_stdout="Detailed agent CLI prose output explaining the work completed.",
                stage=stage,
                require_files=False,
            )

        assert ok is True
        assert output_file.exists()
        assert adapter.synthesized_via == "cli_oauth_stdout"

        if stage == "review":
            output_file.unlink()
            with patch("gitseed_runner.adapters.httpx.AsyncClient", _MockFailGateway):
                ok_neg = await synthesize_output_via_gateway(
                    adapter=adapter,
                    prompt="Run stage",
                    cli_stdout="Review Verdict: Changes requested due to blocking issue in auth.py.",
                    stage="review",
                    require_files=False,
                )
            assert ok_neg is True
            review_data = json.loads(output_file.read_text(encoding="utf-8"))
            assert review_data["approved"] is False
            assert len(review_data["findings"]) == 1

    @pytest.mark.asyncio
    async def test_cli_oauth_fallback_rejects_flag_errors_and_usage_dumps(
        self, tmp_path: Path
    ) -> None:
        from gitseed_runner.adapters import detect_fatal_agent_error

        # 1. CLI unknown option / flag error is caught as fatal
        flag_err = detect_fatal_agent_error("error: unknown option '--nonexistent-flag'")
        assert flag_err is not None
        assert "unknown option" in flag_err.lower()

        # 1b. Gemini CLI FatalAuthenticationError and ANSI-colored auth errors are caught as fatal
        gemini_auth_err = detect_fatal_agent_error(
            "YOLO mode is enabled. All tool calls will be automatically approved.\n"
            "Error authenticating: FatalAuthenticationError: Manual authorization is required "
            "but the current session is non-interactive.\n"
            "\x1b[31mManual authorization is required but the current session is non-interactive.\x1b[0m"
        )
        assert gemini_auth_err is not None
        assert "manual authorization is required" in gemini_auth_err.lower()

        # 2. Usage help dump is rejected by cli_oauth stdout fallback
        output_file = tmp_path / ".gitseed" / "output.json"
        adapter = ClaudeCodeAdapter(
            ctx={
                "model": "claude-sonnet-4-20250514",
                "gateway": {"auth_mode": "cli_oauth"},
            },
            working_dir=tmp_path,
            output_file=output_file,
        )
        ok_usage = await synthesize_output_via_gateway(
            adapter=adapter,
            prompt="Run stage",
            cli_stdout="Usage: claude [options] [command] [prompt]\nOptions:\n  -p, --print",
            stage="ideate",
            require_files=False,
        )
        assert ok_usage is False
        assert not output_file.exists()

        # 3. FatalAuthenticationError stdout is never synthesized into stage output
        ok_auth_err = await synthesize_output_via_gateway(
            adapter=adapter,
            prompt="Run stage",
            cli_stdout=(
                "Error authenticating: FatalAuthenticationError: Manual authorization is required "
                "but the current session is non-interactive."
            ),
            stage="ideate",
            require_files=False,
        )
        assert ok_auth_err is False
        assert not output_file.exists()

    def test_antigravity_and_gemini_oauth_folder_trust_and_model_normalization(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        output_file = tmp_path / ".gitseed" / "output.json"

        ctx_agy: dict[str, Any] = {
            "runtime": "antigravity",
            "model": "gemini-4-argon",
            "gateway": {
                "provider": "antigravity",
                "auth_mode": "cli_oauth",
                "oauth_token": "ya29.test-token",
                "cli_auth_bundle": {
                    ".gemini/antigravity-cli/antigravity-oauth-token": '{"refresh_token": "1//test"}',
                },
            },
        }
        agy_adapter = AntigravityAdapter(
            ctx=ctx_agy, working_dir=tmp_path, output_file=output_file
        )
        env = agy_adapter.prepare_workspace()
        assert env["GOOGLE_CLOUD_ACCESS_TOKEN"] == "ya29.test-token"
        assert env["GOOGLE_GENAI_USE_GCA"] == "true"

        trusted_folders = json.loads(
            (tmp_path / ".gemini" / "trustedFolders.json").read_text(encoding="utf-8")
        )
        assert trusted_folders.get("/workspace") == "TRUST_FOLDER"

        gem_settings = json.loads(
            (tmp_path / ".gemini" / "settings.json").read_text(encoding="utf-8")
        )
        assert gem_settings["security"]["folderTrust"]["enabled"] is False

        with patch("gitseed_runner.adapters.shutil.which", side_effect=lambda b: "/usr/local/bin/agy" if b == "agy" else None):
            assert agy_adapter.binary_name == "agy"
            cmd = agy_adapter.build_command("Review code")
            assert cmd[0] == "agy"
            assert "--model" in cmd
            assert cmd[cmd.index("--model") + 1] == "gemini-2.5-pro"

            # GeminiAdapter backed only by Antigravity OAuth token should also use agy when available
            gem_adapter = GeminiAdapter(
                ctx={**ctx_agy, "runtime": "gemini", "model": "default"},
                working_dir=tmp_path,
                output_file=output_file,
            )
            assert gem_adapter.binary_name == "agy"
            gem_cmd = gem_adapter.build_command("Review code")
            assert gem_cmd[0] == "agy"
            assert "--model" not in gem_cmd


class TestRunnerMainEndToEnd:
    @pytest.mark.asyncio
    async def test_fetch_context_and_poll_steering_and_load_validate(
        self, tmp_path: Path
    ) -> None:
        class _MockControlPlaneClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _MockControlPlaneClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def get(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 200
                resp.raise_for_status = MagicMock()
                if url.endswith("/context"):
                    resp.json.return_value = {"stage": "ideate", "seed_number": 5}
                else:
                    resp.json.return_value = {
                        "should_abort": False,
                        "steering_comments": [{"author_name": "Dev", "body": "@architect hi"}],
                    }
                return resp

        with patch("gitseed_runner.main.httpx.AsyncClient", _MockControlPlaneClient):
            ctx = await _fetch_context("http://api:8420", "tok_123")
            assert ctx["seed_number"] == 5
            steer = await _poll_steering("http://api:8420", "tok_123", "2026-11-11T00:00:00Z")
            assert len(steer["steering_comments"]) == 1

        out_file = tmp_path / "output.json"
        val, err = _load_and_validate_output_file("ideate", out_file)
        assert val is None and err is not None

        out_file.write_text(
            json.dumps(
                {
                    "title": "Valid Title",
                    "description": "Valid Desc",
                    "specification": "Detailed specification text for validation.",
                    "acceptance_criteria": ["Criterion 1"],
                    "summary_comment": "Completed ideation.",
                }
            ),
            encoding="utf-8",
        )
        val2, err2 = _load_and_validate_output_file("ideate", out_file)
        assert err2 is None
        assert val2 is not None

    @pytest.mark.asyncio
    async def test_run_container_job_ideate_and_execute_end_to_end(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        reported: list[dict[str, Any]] = []

        async def fake_report_complete(
            api_url: str, run_token: str, **kwargs: Any
        ) -> None:
            reported.append(kwargs)

        # 1. Ideate stage end-to-end where CLI outputs JSON to stdout
        monkeypatch.setenv("GITSEED_API_URL", "http://api:8420")
        monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_ideate_token")
        monkeypatch.setenv("GITSEED_WORKSPACE", str(tmp_path / "ws_ideate"))

        ideate_json = json.dumps(
            {
                "title": "Refined Rate Limiter",
                "description": "Add Redis rate limiting",
                "specification": "## Overview\nComplete Redis rate limiter spec.",
                "acceptance_criteria": ["Returns HTTP 429 on limit exceeded"],
                "summary_comment": "Refined spec.",
            }
        )

        with (
            patch(
                "gitseed_runner.main._fetch_context",
                AsyncMock(
                    return_value={
                        "stage": "ideate",
                        "seed_number": 12,
                        "runtime": "opencode",
                        "model": "gpt-4o",
                        "gateway": {"base_url": "http://litellm:4000/v1", "api_key": "k"},
                        "repositories": [],
                    }
                ),
            ),
            patch(
                "gitseed_runner.main._run_cli_with_steering",
                AsyncMock(return_value=(0, f"```json\n{ideate_json}\n```", None)),
            ),
            patch("gitseed_runner.main._report_complete", side_effect=fake_report_complete),
        ):
            rc = await run_container_job()

        assert rc == 0
        assert reported[-1]["status"] == "succeeded"
        assert reported[-1]["stage_output"]["title"] == "Refined Rate Limiter"

        # 2. Execute stage end-to-end with repository clone and post-agent git push
        ws_exec = tmp_path / "ws_exec"
        ws_exec.mkdir(parents=True, exist_ok=True)
        (ws_exec / ".git").mkdir()
        monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_exec_token")
        monkeypatch.setenv("GITSEED_WORKSPACE", str(ws_exec))

        exec_json = json.dumps(
            {
                "summary_comment": "Implemented rate limiter.",
                "branch_name": "gitseed/seed-12",
                "commit_sha": None,
                "pull_request_url": None,
            }
        )

        async def fake_exec_cli(**kwargs: Any) -> tuple[int, str, str | None]:
            adapter = kwargs["adapter"]
            adapter.output_file.parent.mkdir(parents=True, exist_ok=True)
            adapter.output_file.write_text(exec_json, encoding="utf-8")
            (ws_exec / "feature.py").write_text("x = 1\n", encoding="utf-8")
            return 0, "Implemented feature.py\n", None

        with (
            patch(
                "gitseed_runner.main._fetch_context",
                AsyncMock(
                    return_value={
                        "stage": "execute",
                        "seed_number": 12,
                        "runtime": "opencode",
                        "model": "gpt-4o",
                        "gateway": {"base_url": "http://litellm:4000/v1", "api_key": "k"},
                        "repositories": [{"full_name": "org/api", "is_primary": True}],
                    }
                ),
            ),
            patch(
                "gitseed_runner.main.clone_project_repositories",
                return_value=(ws_exec, {"org/api": ws_exec}),
            ),
            patch(
                "gitseed_runner.main.get_repo_heads",
                return_value={"org/api": "sha1"},
            ),
            patch(
                "gitseed_runner.main.has_workspace_code_changes",
                return_value=True,
            ),
            patch(
                "gitseed_runner.main._run_cli_with_steering",
                side_effect=fake_exec_cli,
            ),
            patch(
                "gitseed_runner.main.push_execute_changes_after_agent_exit",
                AsyncMock(
                    return_value={
                        "summary_comment": "Implemented rate limiter.",
                        "branch_name": "gitseed/seed-12",
                        "commit_sha": "abcdef123456",
                        "pull_request_url": None,
                    }
                ),
            ) as mock_push,
            patch("gitseed_runner.main._report_complete", side_effect=fake_report_complete),
        ):
            rc_exec = await run_container_job()

        assert rc_exec == 0
        mock_push.assert_called_once()
        assert reported[-1]["status"] == "succeeded"
        assert reported[-1]["stage_output"]["commit_sha"] == "abcdef123456"

    def test_cleanup_git_locks_and_blocked_paths(self, tmp_path: Path) -> None:
        git_dir = tmp_path / ".git"
        refs_dir = git_dir / "refs" / "heads"
        refs_dir.mkdir(parents=True)
        (git_dir / "index.lock").write_text("", encoding="utf-8")
        (refs_dir / "main.lock").write_text("", encoding="utf-8")

        removed = cleanup_git_locks({"org/api": tmp_path})
        assert len(removed) == 2
        assert not (git_dir / "index.lock").exists()
        assert not (refs_dir / "main.lock").exists()

        # Verify blocked path glob matching
        assert matches_blocked_path(
            ".github/workflows/ci.yml", [".github/workflows/*", "*.env*"]
        )
        assert matches_blocked_path(
            "config/.env.production", [".github/workflows/*", "*.env*"]
        )
        assert not matches_blocked_path(
            "src/service.py", [".github/workflows/*", "*.env*"]
        )

        # Verify enforce_blocked_paths reverts blocked tracked and untracked files
        untracked_secret = tmp_path / ".env.local"
        untracked_secret.write_text("SECRET=1", encoding="utf-8")
        git_calls: list[list[str]] = []

        def fake_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            git_calls.append(args)
            if args[:2] == ["diff", "--name-only"]:
                return subprocess.CompletedProcess(
                    args, 0, stdout=".github/workflows/ci.yml\nsrc/app.py\n", stderr=""
                )
            if args[:2] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(args, 0, stdout="?? .env.local\n", stderr="")
            if args[0] == "checkout" and ".env.local" in args:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="pathspec did not match")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=fake_git):
            reverted = enforce_blocked_paths(
                {"org/api": tmp_path},
                {"org/api": "sha_base"},
                [".github/workflows/*", "*.env*"],
            )

        assert ".github/workflows/ci.yml" in reverted
        assert ".env.local" in reverted
        assert not untracked_secret.exists()
        assert any(
            c[:2] == ["checkout", "sha_base"] and ".github/workflows/ci.yml" in c
            for c in git_calls
        )

    def test_apply_synthesized_files_overwrite_protection(self, tmp_path: Path) -> None:
        existing = tmp_path / "src" / "existing.py"
        existing.parent.mkdir(parents=True, exist_ok=True)
        existing.write_text(" ORIGINAL_CODE = True\n", encoding="utf-8")

        payload = {
            "files": [
                {"path": "src/existing.py", "content": "OVERWRITTEN = True\n"},
                {"path": "src/brand_new.py", "content": "NEW = True\n"},
            ]
        }
        written = _apply_synthesized_files(tmp_path, payload, allow_overwrite=False)
        assert written == ["src/brand_new.py"]
        assert existing.read_text(encoding="utf-8") == " ORIGINAL_CODE = True\n"
        assert (tmp_path / "src" / "brand_new.py").read_text(encoding="utf-8") == "NEW = True\n"

        # Explicit per-file overwrite=True is honored
        payload_explicit = {
            "files": [
                {
                    "path": "src/existing.py",
                    "content": "EXPLICIT_OVERWRITE = True\n",
                    "overwrite": True,
                }
            ]
        }
        written2 = _apply_synthesized_files(
            tmp_path, payload_explicit, allow_overwrite=False
        )
        assert written2 == ["src/existing.py"]
        assert existing.read_text(encoding="utf-8") == "EXPLICIT_OVERWRITE = True\n"

    @pytest.mark.asyncio
    async def test_deterministic_verification_gate(self, tmp_path: Path) -> None:
        passed_ok, code_ok, out_ok = await _run_deterministic_verification_gate(
            primary_dir=tmp_path,
            env=dict(os.environ),
            setup_command="echo setup_ok",
            test_command="echo ALL_TESTS_OK",
        )
        assert passed_ok is True
        assert code_ok == 0
        assert "ALL_TESTS_OK" in out_ok

    def test_warm_workspace_incremental_fetch_and_auto_rebase_abort_on_conflict(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / ".git" / "info").mkdir(parents=True, exist_ok=True)
        git_calls: list[list[str]] = []

        def fake_warm_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            git_calls.append(args)
            if args[0] == "rebase" and args[1] != "--abort":
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="CONFLICT")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=fake_warm_git):
            primary_dir, repo_dirs = clone_project_repositories(
                tmp_path,
                [
                    {
                        "full_name": "org/api",
                        "clone_url": "https://x-access-token:secret@github.com/org/api.git",
                        "clean_remote_url": "https://github.com/org/api.git",
                        "default_branch": "main",
                        "is_primary": True,
                    }
                ],
                stage="execute",
                seed_number=11,
                project_slug="alpha",
                clone_depth=0,
                auto_rebase=True,
                warm_workspace=True,
            )

        assert primary_dir == tmp_path
        assert "org/api" in repo_dirs
        # Warm workspace should perform incremental fetch rather than git clone
        assert not any(c[0] == "clone" for c in git_calls)
        assert any(c[0] == "fetch" and "--depth" not in c for c in git_calls)
        # Rebase conflict must trigger safe `rebase --abort`
        assert any(c == ["rebase", "--abort"] for c in git_calls)

    @pytest.mark.asyncio
    async def test_strict_cli_execution_and_batch_sequential_tasks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # 1. Verify _summarize_workspace_tree includes prompt-referenced file contents
        src_dir = tmp_path / "src"
        src_dir.mkdir(parents=True, exist_ok=True)
        (src_dir / "rate_limiter.py").write_text(
            "def check_rate_limit():\n    return True\n", encoding="utf-8"
        )
        summary = _summarize_workspace_tree(
            tmp_path, prompt_hint="Update src/rate_limiter.py to enforce limits"
        )
        assert "Referenced Workspace File Contents" in summary
        assert "def check_rate_limit():" in summary

        # 2. Verify strict CLI execution fails fast when binary is missing (and defaults to strict for execute/merge)
        assert _is_gateway_fallback_allowed({}, stage="ideate") is True
        assert _is_gateway_fallback_allowed({}, stage="execute") is False
        assert _is_gateway_fallback_allowed({}, stage="merge") is False
        assert _is_gateway_fallback_allowed({"allow_gateway_fallback": True}, stage="execute") is True
        output_file = tmp_path / ".gitseed" / "output.json"
        adapter = OpenCodeAdapter(
            ctx={"model": "gpt-4o", "gateway": {"base_url": "http://litellm:4000/v1"}},
            working_dir=tmp_path,
            output_file=output_file,
        )
        with patch.object(adapter, "is_binary_available", return_value=False):
            code, _, err = await _run_cli_with_steering(
                adapter=adapter,
                env={"PATH": "/usr/bin"},
                initial_prompt="Run task",
                api_url="http://api:8420",
                run_token="gsr_tok",
                allow_gateway_fallback=False,
            )
        assert code == 127
        assert err is not None and "is not installed in this runner image" in err
        assert "third-party-agent-clis" in err

        # 3. Verify batch sequential task execution completes multiple pending tasks in one container
        ws_batch = tmp_path / "ws_batch"
        ws_batch.mkdir(parents=True, exist_ok=True)
        (ws_batch / ".git").mkdir()
        monkeypatch.setenv("GITSEED_API_URL", "http://api:8420")
        monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_batch_token")
        monkeypatch.setenv("GITSEED_WORKSPACE", str(ws_batch))

        reported_batch: list[dict[str, Any]] = []
        cli_prompts: list[tuple[str, bool]] = []

        async def fake_batch_cli(**kwargs: Any) -> tuple[int, str, str | None]:
            adp = kwargs["adapter"]
            cli_prompts.append(
                (kwargs["initial_prompt"], bool(kwargs.get("continue_session", False)))
            )
            adp.output_file.parent.mkdir(parents=True, exist_ok=True)
            adp.output_file.write_text(
                json.dumps(
                    {
                        "summary_comment": "Completed batch tasks 1 and 2.",
                        "branch_name": "gitseed/alpha/seed-15",
                        "commit_sha": None,
                        "pull_request_url": None,
                    }
                ),
                encoding="utf-8",
            )
            (ws_batch / "impl.py").write_text("done = True\n", encoding="utf-8")
            return 0, "Executed task pass\n", None

        with (
            patch(
                "gitseed_runner.main._fetch_context",
                AsyncMock(
                    return_value={
                        "stage": "execute",
                        "seed_number": 15,
                        "project_slug": "alpha",
                        "runtime": "opencode",
                        "model": "gpt-4o",
                        "task_id": "tsk_1",
                        "tasks": [
                            {
                                "id": "tsk_1",
                                "position": 0,
                                "title": "Task 1: Core service",
                                "description": "Build service",
                                "status": "running",
                            },
                            {
                                "id": "tsk_2",
                                "position": 1,
                                "title": "Task 2: Unit tests",
                                "description": "Write tests",
                                "status": "pending",
                            },
                        ],
                        "sandbox_config": {
                            "batch_execute_tasks": True,
                            "strict_cli_execution": True,
                        },
                        "gateway": {"base_url": "http://litellm:4000/v1", "api_key": "k"},
                        "repositories": [{"full_name": "org/api", "is_primary": True}],
                    }
                ),
            ),
            patch(
                "gitseed_runner.main.clone_project_repositories",
                return_value=(ws_batch, {"org/api": ws_batch}),
            ),
            patch(
                "gitseed_runner.main.get_repo_heads",
                return_value={"org/api": "sha1"},
            ),
            patch(
                "gitseed_runner.main.has_workspace_code_changes",
                return_value=True,
            ),
            patch.object(OpenCodeAdapter, "is_binary_available", return_value=True),
            patch(
                "gitseed_runner.main._poll_steering",
                AsyncMock(return_value={"should_abort": False, "steering_comments": []}),
            ),
            patch(
                "gitseed_runner.main._run_cli_with_steering",
                side_effect=fake_batch_cli,
            ),
            patch(
                "gitseed_runner.main.push_execute_changes_after_agent_exit",
                AsyncMock(
                    side_effect=lambda **kw: {
                        **kw["stage_output"],
                        "commit_sha": "batchsha999",
                    }
                ),
            ),
            patch(
                "gitseed_runner.main._report_complete",
                AsyncMock(side_effect=lambda *a, **kw: reported_batch.append(kw)),
            ),
        ):
            rc = await run_container_job()

        assert rc == 0
        assert len(cli_prompts) == 2
        assert cli_prompts[0][1] is False
        assert cli_prompts[1][1] is True
        assert "NEXT SEQUENTIAL TASK (#2: Task 2: Unit tests)" in cli_prompts[1][0]
        assert reported_batch[-1]["stage_output"]["completed_task_ids"] == [
            "tsk_1",
            "tsk_2",
        ]

    def test_format_cli_stream_line_parses_stream_json_events(
        self, tmp_path: Path
    ) -> None:
        adp = AntigravityAdapter(
            ctx={"model": "gemini-2.5-pro", "gateway": {}},
            working_dir=tmp_path,
            output_file=tmp_path / ".gitseed" / "output.json",
        )
        assert "stream-json" in adp.build_command("test prompt")

        init_line = json.dumps({"type": "init", "model": "gemini-2.5-pro"}) + "\n"
        assert "[agent] Session initialized" in _format_cli_stream_line(init_line, adp)

        thought_line = json.dumps({"type": "thinking", "thinking": "Inspecting workspace files"}) + "\n"
        assert _format_cli_stream_line(thought_line, adp) == "[thinking] Inspecting workspace files\n"

        tool_line = json.dumps(
            {
                "type": "tool_use",
                "name": "read_file",
                "input": {"path": "src/main.py"},
            }
        ) + "\n"
        assert _format_cli_stream_line(tool_line, adp) == "[tool] read_file path=src/main.py\n"

        res_line = json.dumps(
            {
                "type": "result",
                "result": "Completed changes.",
                "usage": {"input_tokens": 320, "output_tokens": 140},
            }
        ) + "\n"
        assert _format_cli_stream_line(res_line, adp) == "Completed changes.\n"
        assert adp.input_tokens == 320
        assert adp.output_tokens == 140

    def test_shared_git_mirror_reference_clone(self, tmp_path: Path) -> None:
        ws_dir = tmp_path / "workspace"
        cache_dir = tmp_path / "git-mirrors"
        git_calls: list[list[str]] = []

        def fake_mirror_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            git_calls.append(args)
            if args[:2] == ["clone", "--bare"]:
                mirror_target = Path(args[-1])
                mirror_target.mkdir(parents=True, exist_ok=True)
                (mirror_target / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
            elif args[0] == "clone":
                target = Path(args[-1])
                (target / ".git" / "info").mkdir(parents=True, exist_ok=True)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=fake_mirror_git):
            primary_dir, repo_dirs = clone_project_repositories(
                ws_dir,
                [
                    {
                        "full_name": "org/api",
                        "clone_url": "https://x-access-token:secret@github.com/org/api.git",
                        "clean_remote_url": "https://github.com/org/api.git",
                        "default_branch": "main",
                        "is_primary": True,
                    }
                ],
                stage="execute",
                seed_number=21,
                git_cache_dir=cache_dir,
            )

        assert primary_dir == ws_dir
        assert "org/api" in repo_dirs
        assert any(c[:2] == ["clone", "--bare"] for c in git_calls)
        assert any(
            c[0] == "clone" and "--reference-if-able" in c and "--dissociate" in c
            for c in git_calls
        )

    def test_prompt_injects_workspace_tree_and_git_diff_summary(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "engine.py").write_text("x = 1\n", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_engine.py").write_text("assert True\n", encoding="utf-8")
        (tmp_path / "node_modules").mkdir()
        (tmp_path / "node_modules" / "ignored.js").write_text("", encoding="utf-8")
        (tmp_path / "app-release.apk").write_bytes(b"\x00")

        diff = "### Repository `demo/app` (branch `gitseed/demo/seed-9` @ `a1b2c3d`)\n- `src/engine.py` | 10 ++++++++++"
        prompt = build_stage_prompt(
            {"stage": "execute", "seed_number": 9, "project_slug": "demo"},
            tmp_path / ".gitseed" / "output.json",
            workspace_dir=tmp_path,
            git_diff_summary=diff,
        )
        assert "## Workspace File Tree Snapshot (`/workspace`, partial)" in prompt
        assert "src/engine.py" in prompt
        assert "tests/test_engine.py" in prompt
        assert "node_modules" not in prompt
        assert "app-release.apk" not in prompt  # binaries are omitted
        assert "## Workspace Git History & Branch Diff Summary" in prompt
        assert "gitseed/demo/seed-9" in prompt

        # Review works from the diff: no tree. Ideate gets a shallow tree and no diff.
        review = build_stage_prompt(
            {"stage": "review", "seed_number": 9, "project_slug": "demo"},
            tmp_path / ".gitseed" / "output.json",
            workspace_dir=tmp_path,
            git_diff_summary=diff,
        )
        assert "Workspace File Tree Snapshot" not in review
        assert "## Workspace Git History & Branch Diff Summary" in review
        (tmp_path / "pkg" / "sub" / "deep").mkdir(parents=True)
        (tmp_path / "pkg" / "sub" / "deep" / "leaf.py").write_text("", encoding="utf-8")
        ideate = build_stage_prompt(
            {"stage": "ideate", "seed_number": 9, "project_slug": "demo", "plan": "SECRET-PLAN"},
            tmp_path / ".gitseed" / "output.json",
            workspace_dir=tmp_path,
            git_diff_summary=diff,
        )
        assert "pkg/sub/" in ideate and "leaf.py" not in ideate  # Ideate sees only the top 2 levels
        assert "Branch Diff Summary" not in ideate
        assert "SECRET-PLAN" not in ideate  # plan isn't Ideate's concern

        cont_prompt = build_ideate_to_plan_continuation_prompt(
            {
                "specification": "## Spec\nBuild module X",
                "acceptance_criteria": ["Criterion A", "Criterion B"],
            },
            tmp_path / ".gitseed" / "output.json",
        )
        assert "FAST-TRACK CONTINUATION: `PLAN` STAGE" in cont_prompt
        assert "Build module X" in cont_prompt
        assert "target_files" in cont_prompt

    @pytest.mark.asyncio
    async def test_lockfile_fingerprint_skips_setup_command_when_unchanged(
        self, tmp_path: Path
    ) -> None:
        (tmp_path / "pyproject.toml").write_text(
            '[project]\nname = "demo"\nversion = "0.1.0"\n', encoding="utf-8"
        )
        fp = _compute_dependency_fingerprint(tmp_path, "echo setup-ran")
        assert len(fp) == 64

        shell_cmds: list[str] = []

        class _FakeProc:
            returncode = 0

            async def communicate(self) -> tuple[bytes, bytes]:
                return b"tests ok\n", b""

        async def _fake_shell(cmd: str, **kwargs: Any) -> _FakeProc:
            shell_cmds.append(cmd)
            return _FakeProc()

        with patch("asyncio.create_subprocess_shell", side_effect=_fake_shell):
            # 1st run: no stamp yet -> runs setup_command && test_command and writes stamp
            passed1, code1, _ = await _run_deterministic_verification_gate(
                primary_dir=tmp_path,
                env={},
                setup_command="echo setup-ran",
                test_command="pytest -q",
            )
            assert passed1 is True
            assert code1 == 0
            assert shell_cmds[-1] == "echo setup-ran && pytest -q"
            assert (tmp_path / ".gitseed" / "deps.sha256").read_text(encoding="utf-8") == fp

            # 2nd run: lockfile unchanged -> skips setup_command and runs only test_command
            passed2, code2, _ = await _run_deterministic_verification_gate(
                primary_dir=tmp_path,
                env={},
                setup_command="echo setup-ran",
                test_command="pytest -q",
            )
            assert passed2 is True
            assert code2 == 0
            assert shell_cmds[-1] == "pytest -q"

    def test_disjoint_target_files_and_task_worktree_helpers(
        self, tmp_path: Path
    ) -> None:
        t1 = {"id": "1", "target_files": ["src/auth/login.py", "tests/test_login.py"]}
        t2 = {"id": "2", "target_files": ["src/billing/invoice.py", "tests/test_invoice.py"]}
        t3 = {"id": "3", "target_files": ["src/auth/login.py"]}
        assert tasks_have_disjoint_targets(t1, t2) is True
        assert tasks_have_disjoint_targets(t1, t3) is False

        # Fallback extraction from Markdown description `- **Files**: ...`
        t4 = {"id": "4", "description": "- **Files**: `src/ui/button.tsx`, `src/ui/card.tsx`"}
        assert tasks_have_disjoint_targets(t1, t4) is True

        repo_dir = tmp_path / "repo"
        (repo_dir / ".git").mkdir(parents=True)
        wt_dir = tmp_path / "wt-1"

        def _fake_wt_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            if args[:2] == ["worktree", "add"]:
                Path(args[4]).mkdir(parents=True, exist_ok=True)
            elif args[:2] == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(args, 0, stdout=" M src/billing/invoice.py\n", stderr="")
            elif args[:3] == ["diff", "--cached", "--quiet"]:
                return subprocess.CompletedProcess(args, 1, stdout="", stderr="")
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=_fake_wt_git):
            assert create_task_worktree(repo_dir, wt_dir, "gitseed-wt/1-task2") is True
            assert merge_and_remove_task_worktree(
                repo_dir, wt_dir, "gitseed-wt/1-task2", commit_message="feat: task 2"
            ) is True

    def test_has_workspace_code_changes_ignores_all_gitseed_harness_paths(
        self, tmp_path: Path
    ) -> None:
        from gitseed_runner.git_ops import has_workspace_code_changes

        repo_dir = tmp_path / "repo"
        (repo_dir / ".git").mkdir(parents=True)

        def _fake_status_only_harness(
            args: list[str], cwd: Path
        ) -> subprocess.CompletedProcess[str]:
            if args == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, stdout="sha_init\n", stderr="")
            if args == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout=(
                        "?? .gitseed/\n"
                        "?? .gitseed/attachments/spec.pdf\n"
                        "?? .gitseed/related/seed-1.md\n"
                        "?? .gitseed-worktrees/\n"
                        "?? opencode.json\n"
                    ),
                    stderr="",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=_fake_status_only_harness):
            assert (
                has_workspace_code_changes({"org/api": repo_dir}, {"org/api": "sha_init"})
                is False
            )

        def _fake_status_with_real_change(
            args: list[str], cwd: Path
        ) -> subprocess.CompletedProcess[str]:
            if args == ["rev-parse", "HEAD"]:
                return subprocess.CompletedProcess(args, 0, stdout="sha_init\n", stderr="")
            if args == ["status", "--porcelain"]:
                return subprocess.CompletedProcess(
                    args,
                    0,
                    stdout="?? .gitseed/\n M src/app.py\n",
                    stderr="",
                )
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch("gitseed_runner.git_ops._run_git", side_effect=_fake_status_with_real_change):
            assert (
                has_workspace_code_changes({"org/api": repo_dir}, {"org/api": "sha_init"})
                is True
            )

    @pytest.mark.asyncio
    async def test_run_prepare_mode_canonical_sandbox_keys_and_rebased_base_heads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gitseed_runner.broker_modes import run_prepare_mode

        ws_dir = tmp_path / "ws"
        ctl_dir = tmp_path / "ctl"
        monkeypatch.setenv("GITSEED_API_URL", "http://api:8420")
        monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_prep_tok")
        monkeypatch.setenv("GITSEED_RUN_ID", "run_prep_1")
        monkeypatch.setenv("GITSEED_WORKSPACE", str(ws_dir))
        monkeypatch.setenv("GITSEED_CTL_DIR", str(ctl_dir))

        captured_clone_kwargs: dict[str, Any] = {}
        captured_prepare_complete: dict[str, Any] = {}

        def _fake_clone(
            workspace_dir: Path, repositories: list[dict[str, Any]], **kwargs: Any
        ) -> tuple[Path, dict[str, Path]]:
            captured_clone_kwargs.update(kwargs)
            (workspace_dir / ".git" / "info").mkdir(parents=True, exist_ok=True)
            return workspace_dir, {"org/api": workspace_dir}

        def _fake_safe_git(
            args: list[str], cwd: Path, **kwargs: Any
        ) -> tuple[int, str, str]:
            if args == ["rev-parse", "--verify", "refs/remotes/origin/gitseed/alpha/seed-7"]:
                return 0, "remote_seed_sha_old\n", ""
            if args == ["rev-parse", "--verify", "refs/remotes/origin/main"]:
                return 0, "remote_main_sha_new\n", ""
            return 0, "", ""

        async def _fake_post_prepare_complete(
            api_url: str, prepare_token: str, **kwargs: Any
        ) -> None:
            captured_prepare_complete.update(kwargs)

        with (
            patch(
                "gitseed_runner.broker_modes.clone_project_repositories",
                side_effect=_fake_clone,
            ),
            patch(
                "gitseed_runner.broker_modes.get_repo_heads",
                return_value={"org/api": "local_only_rebased_sha"},
            ),
            patch(
                "gitseed_runner.broker_modes.run_safe_git",
                side_effect=_fake_safe_git,
            ),
            patch(
                "gitseed_runner.broker_modes.post_prepare_complete",
                side_effect=_fake_post_prepare_complete,
            ),
        ):
            rc = await run_prepare_mode(
                fetch_context_fn=AsyncMock(
                    return_value={
                        "run_id": "run_prep_1",
                        "stage": "execute",
                        "seed_number": 7,
                        "project_slug": "alpha",
                        "sandbox_config": {
                            "git_clone_depth": 15,
                            "auto_rebase_seed_branch": False,
                            "enable_warm_workspace": False,
                        },
                        "repositories": [
                            {
                                "full_name": "org/api",
                                "clone_url": "https://x-access-token:tok@github.com/org/api.git",
                                "clean_remote_url": "https://github.com/org/api.git",
                                "default_branch": "main",
                                "is_primary": True,
                            }
                        ],
                    }
                ),
                download_attachments_fn=AsyncMock(return_value=[]),
                write_related_seeds_fn=MagicMock(return_value=[]),
            )

        assert rc == 0
        assert captured_clone_kwargs["clone_depth"] == 15
        assert captured_clone_kwargs["auto_rebase"] is False
        assert captured_clone_kwargs["warm_workspace"] is False
        # Local-only rebased SHA must resolve to remote default branch SHA so finalize can fetch it
        assert captured_prepare_complete["base_heads"] == {"org/api": "remote_main_sha_new"}
        assert captured_prepare_complete["remote_seed_heads"] == {
            "org/api": "remote_seed_sha_old"
        }

    def test_non_json_cli_auth_bundle_restore_and_codex_opencode_oauth_configs(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        output_file = tmp_path / ".gitseed" / "output.json"

        # 1. Antigravity non-JSON allowlisted files (.pbtxt, installation_id, jetski-standalone-oauth-token)
        ctx_agy = {
            "runtime": "antigravity",
            "model": "gemini-2.5-pro",
            "gateway": {
                "auth_mode": "cli_oauth",
                "cli_auth_bundle": {
                    ".gemini/jetski-standalone-oauth-token": "ya29.raw-oauth-token-value",
                    ".gemini/antigravity-cli/installation_id": "550e8400-e29b-41d4-a716-446655440000",
                    ".config/Antigravity/User/globalStorage/jetski_state.pbtxt": 'oauth_token: "ya29.pbtxt"\n',
                    ".gemini/oauth_creds.json": '{"refresh_token": "1//valid-json"}',
                    ".gemini/google_accounts.json": "not-valid-json",
                },
            },
        }
        agy = AntigravityAdapter(ctx=ctx_agy, working_dir=tmp_path, output_file=output_file)
        agy.prepare_workspace()

        assert (tmp_path / ".gemini" / "jetski-standalone-oauth-token").read_text(
            encoding="utf-8"
        ) == "ya29.raw-oauth-token-value"
        assert (
            tmp_path / ".gemini" / "antigravity-cli" / "installation_id"
        ).read_text(encoding="utf-8") == "550e8400-e29b-41d4-a716-446655440000"
        assert (
            tmp_path / ".config" / "Antigravity" / "User" / "globalStorage" / "jetski_state.pbtxt"
        ).read_text(encoding="utf-8") == 'oauth_token: "ya29.pbtxt"'
        # Invalid JSON for a .json file is still rejected
        assert not (tmp_path / ".gemini" / "google_accounts.json").exists()

        harvested = agy.collect_updated_cli_auth_bundle(strict_runtime=True)
        assert harvested is not None
        assert harvested[".gemini/jetski-standalone-oauth-token"] == "ya29.raw-oauth-token-value"
        assert (
            harvested[".gemini/antigravity-cli/installation_id"]
            == "550e8400-e29b-41d4-a716-446655440000"
        )
        assert (
            harvested[".config/Antigravity/User/globalStorage/jetski_state.pbtxt"]
            == 'oauth_token: "ya29.pbtxt"'
        )
        assert harvested[".gemini/oauth_creds.json"] == '{"refresh_token": "1//valid-json"}'

        # 2. OpenCodeAdapter restores CLI auth bundle inside prepare_workspace()
        ctx_oc = {
            "runtime": "opencode",
            "model": "gpt-4o",
            "gateway": {
                "auth_mode": "cli_oauth",
                "cli_auth_bundle": {
                    ".local/share/opencode/auth.json": '{"openai": {"access": "oc_tok"}}',
                },
            },
        }
        oc = OpenCodeAdapter(ctx=ctx_oc, working_dir=tmp_path, output_file=output_file)
        oc.prepare_workspace()
        assert (tmp_path / ".local" / "share" / "opencode" / "auth.json").exists()

        # 3. CodexAdapter writes model and MCP env/headers in cli_oauth mode
        ctx_codex = {
            "runtime": "codex",
            "model": "openai/gpt-5.3-codex",
            "gateway": {
                "auth_mode": "cli_oauth",
                "oauth_token": "codex-oauth-tok",
            },
            "mcp_servers": [
                {
                    "slug": "docs",
                    "provider": "mcp-sse",
                    "endpoint": "http://mcp:8000/sse",
                    "headers": {"Authorization": "Bearer secret"},
                },
                {
                    "slug": "local",
                    "provider": "mcp-stdio",
                    "endpoint": "npx -y @mcp/server",
                    "env": {"API_TOKEN": "xyz"},
                },
            ],
        }
        codex = CodexAdapter(ctx=ctx_codex, working_dir=tmp_path, output_file=output_file)
        codex.prepare_workspace()
        codex_toml = (tmp_path / ".codex" / "config.toml").read_text(encoding="utf-8")
        assert 'model = "gpt-5.3-codex"' in codex_toml
        assert 'headers = { "Authorization" = "Bearer secret" }' in codex_toml
        assert 'env = { "API_TOKEN" = "xyz" }' in codex_toml

    @pytest.mark.asyncio
    async def test_broker_prepare_and_finalize_prefer_gitseed_secrets_meta_and_worktree_failure_cleanup(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from gitseed_runner.broker_modes import (
            _meta_file_path,
            run_finalize_mode,
            run_prepare_mode,
        )
        from gitseed_runner.git_ops import cleanup_task_worktree

        ws_dir = tmp_path / "ws"
        ctl_dir = tmp_path / "ctl"
        secrets_dir = tmp_path / "secrets"
        secrets_dir.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("GITSEED_API_URL", "http://api:8420")
        monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_tok")
        monkeypatch.setenv("GITSEED_RUN_ID", "run_sec_1")
        monkeypatch.setenv("GITSEED_WORKSPACE", str(ws_dir))
        monkeypatch.setenv("GITSEED_CTL_DIR", str(ctl_dir))
        monkeypatch.setenv("GITSEED_SECRETS_DIR", str(secrets_dir))

        with (
            patch(
                "gitseed_runner.broker_modes.clone_project_repositories",
                return_value=(ws_dir, {"org/api": ws_dir}),
            ),
            patch(
                "gitseed_runner.broker_modes.get_repo_heads",
                return_value={"org/api": "trusted_sha_111"},
            ),
            patch(
                "gitseed_runner.broker_modes.run_safe_git",
                return_value=(0, "trusted_sha_111\n", ""),
            ),
            patch(
                "gitseed_runner.broker_modes.post_prepare_complete",
                AsyncMock(),
            ),
        ):
            rc = await run_prepare_mode(
                fetch_context_fn=AsyncMock(
                    return_value={
                        "run_id": "run_sec_1",
                        "stage": "ideate",
                        "seed_number": 3,
                        "project_slug": "alpha",
                        "repositories": [{"full_name": "org/api", "is_primary": True}],
                    }
                ),
                download_attachments_fn=AsyncMock(return_value=[]),
                write_related_seeds_fn=MagicMock(return_value=[]),
            )
        assert rc == 0
        assert (secrets_dir / "run_sec_1.meta.json").is_file()
        assert (ctl_dir / "run_sec_1.meta.json").is_file()

        # Simulate untrusted agent tampering with /gitseed-ctl/run_sec_1.meta.json
        (ctl_dir / "run_sec_1.meta.json").write_text(
            json.dumps({"base_heads": {"org/api": "tampered_sha_999"}}),
            encoding="utf-8",
        )
        assert _meta_file_path("run_sec_1", prefer_secrets=True) == (
            secrets_dir / "run_sec_1.meta.json"
        )

        (ctl_dir / "run_sec_1.agent-done").write_text("done", encoding="utf-8")
        (ctl_dir / "agent-result.json").write_text(
            json.dumps(
                {
                    "status": "succeeded",
                    "exit_code": 0,
                    "stage_output": {
                        "title": "T",
                        "description": "D",
                        "specification": "Detailed specification text here",
                        "acceptance_criteria": ["C1"],
                        "summary_comment": "Done ideate",
                    },
                }
            ),
            encoding="utf-8",
        )
        reported_fin: list[dict[str, Any]] = []
        with patch(
            "gitseed_runner.broker_modes.fetch_finalize_context",
            AsyncMock(
                return_value={
                    "run_id": "run_sec_1",
                    "stage": "ideate",
                    "seed_number": 3,
                    "project_slug": "alpha",
                    "base_heads": {"org/api": "tampered_sha_999"},
                    "repositories": [],
                }
            ),
        ):
            rc_fin = await run_finalize_mode(
                report_complete_fn=AsyncMock(
                    side_effect=lambda *a, **kw: reported_fin.append(kw)
                )
            )
        assert rc_fin == 0
        # Trusted /gitseed-secrets metadata overrides tampered base_heads
        assert reported_fin[-1]["trusted"]["base_heads"] == {"org/api": "trusted_sha_111"}

        # Verify cleanup_task_worktree removes worktree and branch without merging
        git_wt_calls: list[list[str]] = []
        wt_dir = tmp_path / "wt-failed"
        wt_dir.mkdir(parents=True, exist_ok=True)
        def _fake_run_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
            git_wt_calls.append(args)
            return subprocess.CompletedProcess(args, 0, stdout="", stderr="")

        with patch(
            "gitseed_runner.git_ops._run_git",
            side_effect=_fake_run_git,
        ):
            cleanup_task_worktree(ws_dir, wt_dir, "gitseed-wt/3-failed")
        assert not wt_dir.exists()
        assert not any(c[0] == "merge" for c in git_wt_calls)
        assert ["branch", "-D", "gitseed-wt/3-failed"] in git_wt_calls








