from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gitseed_runner.adapters import OpenCodeAdapter
from gitseed_runner.main import _report_complete, _run_cli_with_steering
from gitseed_runner.prompts import (
    build_stage_prompt,
    build_steering_continuation_prompt,
    build_validation_repair_prompt,
)


class TestRunnerPrompts:
    @pytest.mark.parametrize(
        "stage",
        ["ideate", "plan", "execute", "review", "merge"],
    )
    def test_build_stage_prompt_all_five_stages(
        self, stage: str, tmp_path: Path
    ) -> None:
        output_file = tmp_path / ".gitseed" / "output.json"
        ctx: dict[str, Any] = {
            "stage": stage,
            "attempt": 1,
            "agent_handle": "coder",
            "agent_name": "Coder Agent",
            "agent_instructions": "Write clean, well-typed code.",
            "project_slug": "gitseed",
            "seed_number": 42,
            "title": "Add runner test suite",
            "description": "Ensure comprehensive tests for runner prompts and main.",
            "specification": "## Spec\nFull runner coverage.",
            "acceptance_criteria": ["All 5 stages tested"],
            "plan": "## Plan\n1. Test prompts\n2. Test main",
            "task_id": "tsk_1",
            "tasks": [
                {
                    "id": "tsk_1",
                    "position": 0,
                    "title": "Write runner unit tests",
                    "description": "Cover prompts and CLI execution",
                    "status": "running",
                }
            ],
            "skills": [
                {
                    "id": "sk_1",
                    "name": "Pytest Skill",
                    "slug": "pytest-skill",
                    "content": "Always use deterministic fixtures.",
                }
            ],
            "repositories": [
                {
                    "full_name": "org/gitseed",
                    "default_branch": "main",
                    "is_primary": True,
                }
            ],
            "sandbox_config": {
                "image": "python:3.12-slim",
                "test_command": "pytest -q",
            },
            "approval_feedback": [
                {
                    "stage": "plan",
                    "decision": "changes_requested",
                    "comment": "Include edge case for 20k log tail.",
                }
            ],
            "comments": [
                {
                    "author_name": "Alice",
                    "stage": stage,
                    "body": "Please verify all 5 stages.",
                }
            ],
            "output_schema": {"type": "object"},
        }

        prompt = build_stage_prompt(ctx, output_file)
        assert "gitseed#42" in prompt
        assert stage.upper() in prompt
        assert "@coder" in prompt
        assert "Write clean, well-typed code." in prompt
        assert "Pytest Skill" in prompt
        # Tasks are stage-scoped: only Plan and Execute carry the task list
        if stage in ("plan", "execute"):
            assert "[ACTIVE TASK FOR THIS RUN]" in prompt
        else:
            assert "[ACTIVE TASK FOR THIS RUN]" not in prompt
        assert str(output_file) in prompt
        if stage == "execute":
            assert "gitseed/seed-42" in prompt

    def test_build_steering_and_validation_repair_prompts(
        self, tmp_path: Path
    ) -> None:
        output_file = tmp_path / "output.json"
        steering_prompt = build_steering_continuation_prompt(
            [
                {
                    "author_name": "Bob",
                    "body": "@coder switch to httpx.AsyncClient",
                }
            ],
            output_file,
        )
        assert "LIVE OPERATOR STEERING UPDATE" in steering_prompt
        assert "**Bob**: @coder switch to httpx.AsyncClient" in steering_prompt
        assert str(output_file) in steering_prompt

        for stage in ("ideate", "plan", "execute", "review", "merge"):
            repair = build_validation_repair_prompt(
                "Field required: summary_comment", stage, output_file
            )
            assert "OUTPUT VALIDATION ERROR" in repair
            assert "Field required: summary_comment" in repair
            assert str(output_file) in repair


class TestRunnerMainExecution:
    @pytest.mark.asyncio
    async def test_report_complete_preserves_20000_char_log_tail_up_to_50000(
        self,
    ) -> None:
        captured_payloads: list[dict[str, Any]] = []

        class _MockAsyncClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _MockAsyncClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def post(
                self,
                url: str,
                *,
                headers: dict[str, str],
                json: dict[str, Any],
            ) -> Any:
                captured_payloads.append(json)
                resp = MagicMock()
                resp.raise_for_status = MagicMock()
                return resp

        log_20k = "A" * 20_000
        with patch("gitseed_runner.main.httpx.AsyncClient", _MockAsyncClient):
            await _report_complete(
                "http://api:8420",
                "gsr_test_token",
                status="succeeded",
                exit_code=0,
                input_tokens=100,
                output_tokens=200,
                stage_output={"summary_comment": "Done"},
                log_tail=log_20k,
            )

        assert len(captured_payloads) == 1
        sent_tail = captured_payloads[0]["log_tail"]
        assert isinstance(sent_tail, str)
        # Must preserve full 20,000 chars (not truncated at 8,000)
        assert len(sent_tail) == 20_000
        assert sent_tail == log_20k

        # Also verify strings > 50,000 chars are capped at 50,000
        log_60k = "B" * 10_000 + "C" * 50_000
        with patch("gitseed_runner.main.httpx.AsyncClient", _MockAsyncClient):
            await _report_complete(
                "http://api:8420",
                "gsr_test_token",
                status="succeeded",
                exit_code=0,
                log_tail=log_60k,
            )

        assert len(captured_payloads[1]["log_tail"]) == 50_000
        assert captured_payloads[1]["log_tail"] == "C" * 50_000

    @pytest.mark.asyncio
    async def test_run_cli_with_steering_passes_continue_session_true_to_build_command(
        self, tmp_path: Path
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

        build_command_calls: list[tuple[str, bool]] = []
        original_build_command = adapter.build_command

        def _spy_build_command(
            prompt: str, *, continue_session: bool = False
        ) -> list[str]:
            build_command_calls.append((prompt, continue_session))
            return original_build_command(
                prompt, continue_session=continue_session
            )

        class _FakeStdout:
            def __init__(self, lines: list[bytes]) -> None:
                self._lines = list(lines)

            async def readline(self) -> bytes:
                if self._lines:
                    return self._lines.pop(0)
                return b""

        class _FakeProcess:
            def __init__(self) -> None:
                self.returncode: int | None = 0
                self.stdout = _FakeStdout([b"Repaired output.json\n"])

            async def wait(self) -> int:
                self.returncode = 0
                return 0

        with (
            patch.object(adapter, "is_binary_available", return_value=True),
            patch.object(
                adapter, "build_command", side_effect=_spy_build_command
            ),
            patch(
                "gitseed_runner.main.asyncio.create_subprocess_exec",
                AsyncMock(return_value=_FakeProcess()),
            ) as mock_exec,
        ):
            exit_code, logs, abort_err = await _run_cli_with_steering(
                adapter=adapter,
                env={"PATH": "/usr/bin"},
                initial_prompt="Fix validation error in output.json",
                api_url="http://api:8420",
                run_token="gsr_token",
                continue_session=True,
            )

        assert exit_code == 0
        assert abort_err is None
        assert "Repaired output.json" in logs
        assert build_command_calls == [
            ("Fix validation error in output.json", True)
        ]
        called_argv = list(mock_exec.call_args.args)
        assert "--continue" in called_argv


class TestTrustedBrokerGitSafeAndFinalize:
    """Real-git security tests for prepare neutralization and finalize clean-repo commit/push."""

    @pytest.mark.asyncio
    async def test_hostile_hooks_and_fsmonitor_in_workspace_never_execute_in_finalize(
        self, tmp_path: Path
    ) -> None:
        import subprocess

        from gitseed_runner.gitsafe import (
            construct_clean_commit_and_verify_policy,
            neutralize_git_repo,
            push_with_askpass,
        )

        # 1. Create a real bare remote repository and initial commit
        remote_bare = tmp_path / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote_bare)], check=True)

        seed_clone = tmp_path / "seed_init"
        subprocess.run(["git", "clone", str(remote_bare), str(seed_clone)], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "config", "user.name", "Init"], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "config", "user.email", "init@test"], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "checkout", "-b", "main"], check=True)
        (seed_clone / "app.py").write_text("print('v1')\n", encoding="utf-8")
        (seed_clone / "DEPLOY.md").write_text("immutable deploy doc\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed_clone), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "commit", "-m", "initial"], check=True)
        base_sha = subprocess.check_output(
            ["git", "-C", str(seed_clone), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "-C", str(seed_clone), "push", "origin", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(remote_bare), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )

        # 2. Simulate agent workspace with hostile hooks and core.fsmonitor
        ws_repo = tmp_path / "workspace" / "repo"
        subprocess.run(["git", "clone", str(remote_bare), str(ws_repo)], check=True)
        sentinel = tmp_path / "PWNED_BY_HOOK"

        hooks_dir = ws_repo / ".git" / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        for hook_name in ("pre-commit", "pre-push", "post-checkout", "commit-msg"):
            hook_file = hooks_dir / hook_name
            hook_file.write_text(
                f"#!/bin/sh\ntouch {sentinel}\nexit 0\n", encoding="utf-8"
            )
            hook_file.chmod(0o755)

        fsmonitor_script = tmp_path / "evil_fsmonitor.sh"
        fsmonitor_script.write_text(
            f"#!/bin/sh\ntouch {sentinel}\n", encoding="utf-8"
        )
        fsmonitor_script.chmod(0o755)
        (ws_repo / ".git" / "config").write_text(
            f"[core]\n\trepositoryformatversion = 0\n\tfilemode = true\n\tfsmonitor = {fsmonitor_script}\n\thooksPath = {hooks_dir}\n",
            encoding="utf-8",
        )

        # Agent modifies code legitimately
        (ws_repo / "app.py").write_text("print('v2 from agent')\n", encoding="utf-8")
        # Agent also writes internal .gitseed/output.json which finalize must exclude
        (ws_repo / ".gitseed").mkdir(exist_ok=True)
        (ws_repo / ".gitseed" / "output.json").write_text("{}", encoding="utf-8")

        # 3. Finalize clones fresh into private dir and reads only working tree from ws_repo
        clean_repo = tmp_path / "finalize_private" / "repo"
        subprocess.run(["git", "clone", str(remote_bare), str(clean_repo)], check=True)

        commit_res = construct_clean_commit_and_verify_policy(
            clean_repo_dir=clean_repo,
            workspace_repo_dir=ws_repo,
            base_sha=base_sha,
            seed_branch="gitseed/seed-1-test",
            commit_message="Seed #1: Update app",
            trailers={
                "GitSeed-Seed": "sed_1",
                "GitSeed-Run": "run_1",
                "GitSeed-Stage": "execute",
                "GitSeed-Runtime": "claude_code",
                "GitSeed-Model": "claude-sonnet-4",
            },
            blocked_paths=["DEPLOY.md"],
            blocked_paths_action="fail",
            max_file_bytes=1024 * 1024,
            max_diff_bytes=5 * 1024 * 1024,
        )

        assert commit_res.error is None
        assert commit_res.commit_sha is not None
        assert commit_res.commit_sha != base_sha
        assert not sentinel.exists(), "Hostile hook or fsmonitor in /workspace must NEVER execute!"

        # Push from clean_repo to remote_bare via push_with_askpass
        pushed, push_err = push_with_askpass(
            clean_repo_dir=clean_repo,
            remote_url=str(remote_bare),
            branch="gitseed/seed-1-test",
            expected_remote_sha=None,
            username="x-access-token",
            token="",
        )
        assert pushed is True, f"Push failed: {push_err}"
        assert not sentinel.exists()

        # Verify trailers on remote commit and absence of .gitseed/output.json
        log_msg = subprocess.check_output(
            ["git", "--git-dir", str(remote_bare), "log", "-1", "--format=%B", "refs/heads/gitseed/seed-1-test"],
            text=True,
        )
        assert "GitSeed-Seed: sed_1" in log_msg
        assert "GitSeed-Run: run_1" in log_msg

        # 4. Also verify neutralize_git_repo strips hooks and rewrites .git/config on warm workspace
        neutralize_git_repo(ws_repo, clean_remote_url="https://github.com/acme/alpha.git")
        assert list((ws_repo / ".git" / "hooks").iterdir()) == []
        cfg_after = (ws_repo / ".git" / "config").read_text(encoding="utf-8")
        assert str(fsmonitor_script) not in cfg_after
        assert str(hooks_dir) not in cfg_after
        assert "fsmonitor = false" in cfg_after
        assert "hooksPath = /dev/null" in cfg_after

    @pytest.mark.asyncio
    async def test_finalize_enforces_blocked_paths_secret_scan_and_rejects_nested_git(
        self, tmp_path: Path
    ) -> None:
        import subprocess

        from gitseed_runner.gitsafe import construct_clean_commit_and_verify_policy

        remote_bare = tmp_path / "remote2.git"
        subprocess.run(["git", "init", "--bare", str(remote_bare)], check=True)
        seed_clone = tmp_path / "seed_init2"
        subprocess.run(["git", "clone", str(remote_bare), str(seed_clone)], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "config", "user.name", "Init"], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "config", "user.email", "init@test"], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "checkout", "-b", "main"], check=True)
        (seed_clone / "app.py").write_text("x = 1\n", encoding="utf-8")
        (seed_clone / "DEPLOY.md").write_text("prod config\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(seed_clone), "add", "-A"], check=True)
        subprocess.run(["git", "-C", str(seed_clone), "commit", "-m", "init"], check=True)
        base_sha = subprocess.check_output(
            ["git", "-C", str(seed_clone), "rev-parse", "HEAD"], text=True
        ).strip()
        subprocess.run(["git", "-C", str(seed_clone), "push", "origin", "main"], check=True)
        subprocess.run(
            ["git", "-C", str(remote_bare), "symbolic-ref", "HEAD", "refs/heads/main"],
            check=True,
        )

        # Case A: Agent modifies blocked path DEPLOY.md -> finalize FAILS the run with governance violation
        ws_repo = tmp_path / "ws_blocked"
        subprocess.run(["git", "clone", str(remote_bare), str(ws_repo)], check=True)
        (ws_repo / "DEPLOY.md").write_text("tampered deploy doc\n", encoding="utf-8")
        clean_a = tmp_path / "clean_a"
        subprocess.run(["git", "clone", str(remote_bare), str(clean_a)], check=True)

        res_blocked = construct_clean_commit_and_verify_policy(
            clean_repo_dir=clean_a,
            workspace_repo_dir=ws_repo,
            base_sha=base_sha,
            seed_branch="gitseed/seed-2",
            commit_message="Tamper deploy",
            trailers={"GitSeed-Run": "run_2"},
            blocked_paths=["DEPLOY.md"],
            blocked_paths_action="fail",
        )
        assert res_blocked.commit_sha is None
        assert res_blocked.blocked_paths_violations == ["DEPLOY.md"]
        assert res_blocked.error is not None
        assert "DEPLOY.md" in res_blocked.error

        # Case B: Agent adds a secret (GitHub PAT or issued run credential) -> finalize FAILS without leaking secret value
        ws_secret = tmp_path / "ws_secret"
        subprocess.run(["git", "clone", str(remote_bare), str(ws_secret)], check=True)
        leaked_pat = "ghp_" + ("A1b2C3d4E5f6G7h8I9j0K1l2M3n4O5p6Q7r8")
        (ws_secret / "app.py").write_text(f"TOKEN = '{leaked_pat}'\n", encoding="utf-8")
        clean_b = tmp_path / "clean_b"
        subprocess.run(["git", "clone", str(remote_bare), str(clean_b)], check=True)

        res_secret = construct_clean_commit_and_verify_policy(
            clean_repo_dir=clean_b,
            workspace_repo_dir=ws_secret,
            base_sha=base_sha,
            seed_branch="gitseed/seed-2",
            commit_message="Add token",
            trailers={"GitSeed-Run": "run_2"},
            blocked_paths=[],
        )
        assert res_secret.commit_sha is None
        assert len(res_secret.secret_scan_findings) >= 1
        assert res_secret.secret_scan_findings[0]["path"] == "app.py"
        assert res_secret.secret_scan_findings[0]["rule"] == "github_token"
        assert leaked_pat not in str(res_secret.secret_scan_findings)

        # Case C: Agent plants an unauthorized nested .git directory -> finalize rejects
        ws_nested = tmp_path / "ws_nested"
        subprocess.run(["git", "clone", str(remote_bare), str(ws_nested)], check=True)
        nested_git = ws_nested / "subpkg" / ".git"
        nested_git.mkdir(parents=True)
        clean_c = tmp_path / "clean_c"
        subprocess.run(["git", "clone", str(remote_bare), str(clean_c)], check=True)

        res_nested = construct_clean_commit_and_verify_policy(
            clean_repo_dir=clean_c,
            workspace_repo_dir=ws_nested,
            base_sha=base_sha,
            seed_branch="gitseed/seed-2",
            commit_message="Nested git",
            trailers={"GitSeed-Run": "run_2"},
            blocked_paths=[],
        )
        assert res_nested.commit_sha is None
        assert res_nested.error is not None
        assert "nested .git" in res_nested.error

        # Case D: Non-ASCII path matching blocked_paths (core.quotePath=false prevents C-quoted octal bypass)
        ws_unicode = tmp_path / "ws_unicode"
        subprocess.run(["git", "clone", str(remote_bare), str(ws_unicode)], check=True)
        unicode_env_file = ws_unicode / "secret_é.env"
        unicode_env_file.write_text("API_KEY=123\n", encoding="utf-8")
        clean_d = tmp_path / "clean_d"
        subprocess.run(["git", "clone", str(remote_bare), str(clean_d)], check=True)

        res_unicode = construct_clean_commit_and_verify_policy(
            clean_repo_dir=clean_d,
            workspace_repo_dir=ws_unicode,
            base_sha=base_sha,
            seed_branch="gitseed/seed-2",
            commit_message="Unicode blocked path",
            trailers={"GitSeed-Run": "run_2"},
            blocked_paths=["*.env"],
            blocked_paths_action="fail",
        )
        assert res_unicode.ok is False
        assert res_unicode.commit_sha is None
        assert "secret_é.env" in res_unicode.blocked_paths_violations

        # Case E: File containing NUL byte (\x00) and a secret is still scanned via --text (--binary not skipped)
        ws_nul = tmp_path / "ws_nul"
        subprocess.run(["git", "clone", str(remote_bare), str(ws_nul)], check=True)
        (ws_nul / "payload.bin").write_bytes(
            b"\x00header\nTOKEN=" + leaked_pat.encode("utf-8") + b"\n"
        )
        clean_e = tmp_path / "clean_e"
        subprocess.run(["git", "clone", str(remote_bare), str(clean_e)], check=True)

        res_nul = construct_clean_commit_and_verify_policy(
            clean_repo_dir=clean_e,
            workspace_repo_dir=ws_nul,
            base_sha=base_sha,
            seed_branch="gitseed/seed-2",
            commit_message="NUL byte secret",
            trailers={"GitSeed-Run": "run_2"},
            blocked_paths=[],
        )
        assert res_nul.ok is False
        assert res_nul.commit_sha is None
        assert any(f["rule"] == "github_token" for f in res_nul.secret_scan_findings)


