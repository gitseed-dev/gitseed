"""Regression tests for C1 (commondir/alternates/env hardening) and H1 (-z quoted pathnames)."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from gitseed_runner.broker_modes import run_finalize_mode
from gitseed_runner.gitsafe import (
    build_safe_git_env,
    construct_clean_commit_and_verify_policy,
    neutralize_git_repo,
    run_safe_git,
)


def _init_repo_with_commit(repo_dir: Path) -> str:
    repo_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo_dir)], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo_dir), "config", "user.email", "test@example.com"], check=True
    )
    (repo_dir / "README.md").write_text("# Test\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo_dir), "add", "README.md"], check=True)
    subprocess.run(["git", "-C", str(repo_dir), "commit", "-q", "-m", "init"], check=True)
    proc = subprocess.run(
        ["git", "-C", str(repo_dir), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    )
    return proc.stdout.strip()


def test_c1_commondir_and_alternates_are_neutralized(tmp_path: Path) -> None:
    """An agent-planted .git/commondir + external config with credential.helper must be neutralized."""
    ws = tmp_path / "ws"
    _init_repo_with_commit(ws)

    evil_common = ws / ".evil_common"
    evil_common.mkdir(parents=True, exist_ok=True)
    (evil_common / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n\tbare = false\n[credential]\n\thelper = "!echo PWNED"\n',
        encoding="utf-8",
    )
    (ws / ".git" / "commondir").write_text("../.evil_common\n", encoding="utf-8")
    alt_file = ws / ".git" / "objects" / "info" / "alternates"
    alt_file.parent.mkdir(parents=True, exist_ok=True)
    alt_file.write_text("/tmp/evil-objects\n", encoding="utf-8")

    neutralize_git_repo(ws, clean_remote_url="https://github.com/org/repo.git", default_branch="main")

    assert not (ws / ".git" / "commondir").exists()
    assert not alt_file.exists()
    rc, out, _ = run_safe_git(["config", "--get-all", "credential.helper"], ws)
    assert "PWNED" not in out


def test_c1_build_safe_git_env_strips_run_token_and_secrets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_safe_git_env must never leak GITSEED_RUN_TOKEN or API keys into git child processes."""
    monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_secret_prepare_token_123456")
    monkeypatch.setenv("GITSEED_SECRET_KEY", "super_secret_control_plane_key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    monkeypatch.setenv("CUSTOM_SERVICE_TOKEN", "tok_xyz")

    env = build_safe_git_env()
    assert "GITSEED_RUN_TOKEN" not in env
    assert "GITSEED_SECRET_KEY" not in env
    assert "ANTHROPIC_API_KEY" not in env
    assert "CUSTOM_SERVICE_TOKEN" not in env


@pytest.mark.skipif(
    sys.platform == "win32",
    reason="POSIX quotes in filenames",
)
def test_h1_quoted_pathname_blocked_paths_and_size_cap_enforced(tmp_path: Path) -> None:
    """A path containing double quotes under .github/workflows/ must not bypass blocked_paths or max_file_bytes."""
    clean_repo = tmp_path / "clean"
    base_sha = _init_repo_with_commit(clean_repo)

    ws_repo = tmp_path / "ws"
    subprocess.run(["git", "clone", "-q", str(clean_repo), str(ws_repo)], check=True)

    wf_dir = ws_repo / ".github" / "workflows"
    wf_dir.mkdir(parents=True, exist_ok=True)
    quoted_file = wf_dir / 'evil".yml'
    quoted_file.write_text("name: evil\n", encoding="utf-8")

    res = construct_clean_commit_and_verify_policy(
        clean_repo_dir=clean_repo,
        workspace_repo_dir=ws_repo,
        base_sha=base_sha,
        seed_branch="gitseed/alpha/seed-1",
        commit_message="test quoted path",
        blocked_paths=[".github/workflows/**"],
        blocked_paths_action="fail",
    )
    assert res.ok is False
    assert any('evil".yml' in v for v in res.blocked_paths_violations)

    # Also verify size cap enforcement on a quoted filename outside blocked_paths
    shutil.rmtree(wf_dir)
    big_quoted = ws_repo / 'big"file.bin'
    big_quoted.write_bytes(b"x" * 2048)
    res_sz = construct_clean_commit_and_verify_policy(
        clean_repo_dir=clean_repo,
        workspace_repo_dir=ws_repo,
        base_sha=base_sha,
        seed_branch="gitseed/alpha/seed-1",
        commit_message="test quoted size cap",
        blocked_paths=[".github/workflows/**"],
        blocked_paths_action="fail",
        max_file_bytes=512,
    )
    assert res_sz.ok is False
    assert res_sz.error is not None and "exceeds max_file_bytes" in res_sz.error


@pytest.mark.asyncio
async def test_finalize_ignores_agent_writable_ctl_meta_when_core_empty(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """run_finalize_mode must not fall back to /gitseed-ctl/{run_id}.meta.json for base_heads or seed_branch."""
    ws_dir = tmp_path / "ws"
    ctl_dir = tmp_path / "ctl"
    secrets_dir = tmp_path / "secrets"
    ws_dir.mkdir()
    ctl_dir.mkdir()
    secrets_dir.mkdir()

    monkeypatch.setenv("GITSEED_API_URL", "http://api:8420")
    monkeypatch.setenv("GITSEED_RUN_TOKEN", "gsr_fin_tok")
    monkeypatch.setenv("GITSEED_RUN_ID", "run_fin_1")
    monkeypatch.setenv("GITSEED_WORKSPACE", str(ws_dir))
    monkeypatch.setenv("GITSEED_CTL_DIR", str(ctl_dir))
    monkeypatch.setenv("GITSEED_SECRETS_DIR", str(secrets_dir))

    (ctl_dir / "run_fin_1.meta.json").write_text(
        json.dumps(
            {
                "seed_branch": "main",
                "base_heads": {"org/api": "deadbeef" * 5},
            }
        ),
        encoding="utf-8",
    )
    (ctl_dir / "run_fin_1.agent-done").write_text(
        json.dumps({"exit_code": 0}), encoding="utf-8"
    )
    (ctl_dir / "agent-result.json").write_text(
        json.dumps(
            {
                "status": "succeeded",
                "exit_code": 0,
                "stage_output": {
                    "title": "T",
                    "description": "D",
                    "specification": "Spec text",
                    "acceptance_criteria": ["AC1"],
                    "summary_comment": "Done",
                },
            }
        ),
        encoding="utf-8",
    )

    reported: list[dict[str, Any]] = []
    with patch(
        "gitseed_runner.broker_modes.fetch_finalize_context",
        AsyncMock(
            return_value={
                "run_id": "run_fin_1",
                "stage": "ideate",
                "seed_number": 5,
                "project_slug": "alpha",
                "base_heads": {},
                "repositories": [],
            }
        ),
    ):
        await run_finalize_mode(
            report_complete_fn=AsyncMock(side_effect=lambda *a, **kw: reported.append(kw))
        )

    assert reported
    assert reported[-1]["trusted"]["branch"] == "gitseed/alpha/seed-5"
    assert reported[-1]["trusted"]["base_heads"] == {}
