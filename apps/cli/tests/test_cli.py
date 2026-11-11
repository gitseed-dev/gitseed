from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from gitseed_cli import config as cli_config
from gitseed_cli.client import GitSeedClient
from gitseed_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

NOW_ISO = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC).isoformat()


def _mock_State() -> dict[str, Any]:
    return {
        "user": {
            "id": "usr_01hadmin",
            "email": "admin@gitseed.local",
            "name": "Admin User",
            "preferred_name": "Admin",
            "org_role": "admin",
            "status": "active",
            "preferences": {},
            "last_active_at": NOW_ISO,
            "created_at": NOW_ISO,
        },
        "projects": [
            {
                "id": "prj_01hapi",
                "slug": "api",
                "name": "Core API",
                "description": "Backend API service",
                "visibility": "private",
                "monthly_budget": "500.00",
                "seed_budget_cap": "25.00",
                "seed_counter": 12,
                "archived_at": None,
                "my_role": "maintainer",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            }
        ],
        "seeds": [
            {
                "id": "sed_01hratelimit",
                "project_id": "prj_01hapi",
                "number": 12,
                "title": "Add sliding-window rate limiting",
                "description": "Protect auth endpoints",
                "specification": "Use Redis sliding window",
                "acceptance_criteria": ["Returns 429"],
                "plan": "1. Add middleware",
                "stage": "ideate",
                "status": "awaiting_approval",
                "autonomy": "supervised",
                "budget_cap": "15.00",
                "spent": "0.42",
                "checkpoint_approved_through": "0.00",
                "tags": ["security"],
                "created_by": "usr_01hadmin",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            }
        ],
        "agents": [
            {
                "id": "agt_01harchitect",
                "handle": "architect",
                "name": "Architect",
                "kind": "stage",
                "template": "architect",
                "instructions": "You are @architect.",
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "skills": [],
                "tools": ["repo_read"],
                "enabled": True,
                "archived_at": None,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
            {
                "id": "agt_01hcoder",
                "handle": "coder",
                "name": "Coder",
                "kind": "stage",
                "template": "coder",
                "instructions": "You are @coder.",
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "skills": ["pytest-conventions"],
                "tools": ["repo_read", "repo_write"],
                "enabled": True,
                "archived_at": None,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
        ],
        "skills": [
            {
                "id": "skl_01hpytest",
                "slug": "pytest-conventions",
                "name": "Pytest Conventions",
                "description": "Standard pytest patterns",
                "content": "Write deterministic tests.",
                "archived_at": None,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            }
        ],
        "policies": [
            {
                "stage": "ideate",
                "enabled": True,
                "agent_id": "agt_01harchitect",
                "requires_approval": True,
                "min_approvers": 1,
                "min_approver_role": "developer",
                "allow_auto_approve": False,
                "allow_self_approval": True,
                "max_attempts": 2,
                "timeout_minutes": 30,
                "overridden_fields": [],
            },
            {
                "stage": "execute",
                "enabled": True,
                "agent_id": "agt_01hcoder",
                "requires_approval": True,
                "min_approvers": 1,
                "min_approver_role": "developer",
                "allow_auto_approve": False,
                "allow_self_approval": True,
                "max_attempts": 2,
                "timeout_minutes": 30,
                "overridden_fields": ["agent_id"],
            },
        ],
        "tokens": [
            {
                "id": "tok_01hcli",
                "name": "CLI on workstation",
                "token_prefix": "gsp_abcd1234",
                "expires_at": NOW_ISO,
                "last_used_at": NOW_ISO,
                "created_at": NOW_ISO,
            }
        ],
        "runs": [
            {
                "id": "run_01hfirst",
                "seed_id": "sed_01hratelimit",
                "task_id": None,
                "agent_id": "agt_01harchitect",
                "stage": "ideate",
                "attempt": 1,
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "status": "succeeded",
                "input_tokens": 1200,
                "output_tokens": 450,
                "cost": "0.18",
                "output": {"log_tail": "Drafted specification.\nDone."},
                "error": None,
                "started_at": NOW_ISO,
                "finished_at": NOW_ISO,
                "created_at": NOW_ISO,
            }
        ],
        "comments": [],
        "approvals": [],
        "minted_token_names": [],
    }


@pytest.fixture()
def isolated_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("GITSEED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITSEED_DISABLE_KEYRING", "1")

    state = _mock_State()

    def handler(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path).replace("/seeds/api#12", "/seeds/sed_01hratelimit")
        method = request.method
        body = json.loads(request.content.decode()) if request.content else {}

        if method == "POST" and path == "/api/v1/auth/login":
            return httpx.Response(
                200, json=state["user"], headers={"set-cookie": "gitseed_session=xyz"}
            )
        if method == "POST" and path == "/api/v1/auth/tokens":
            state["minted_token_names"].append(body.get("name"))
            return httpx.Response(
                201,
                json={
                    **state["tokens"][0],
                    "name": body.get("name", "CLI"),
                    "token": "gsp_supersecret_token_value_do_not_print",
                },
            )
        if method == "GET" and path == "/api/v1/auth/me":
            return httpx.Response(200, json=state["user"])
        if method == "GET" and path == "/api/v1/auth/tokens":
            return httpx.Response(200, json=state["tokens"])
        if method == "DELETE" and path.startswith("/api/v1/auth/tokens/"):
            return httpx.Response(204)

        if method == "GET" and path in {"/healthz", "/api/v1/health"}:
            return httpx.Response(
                200,
                json={"status": "ok", "version": "0.1.0", "database": "ok", "redis": "ok"},
            )
        if method == "GET" and path in {"/version", "/api/v1/version"}:
            return httpx.Response(200, json={"service": "gitseed-core", "version": "0.1.0"})

        if method == "GET" and path == "/api/v1/projects":
            return httpx.Response(200, json=state["projects"])
        if method == "GET" and path in {"/api/v1/projects/api", "/api/v1/projects/prj_01hapi"}:
            return httpx.Response(200, json=state["projects"][0])

        if method == "GET" and path == "/api/v1/seeds":
            return httpx.Response(200, json=state["seeds"])
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit":
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds":
            new_seed = {
                **state["seeds"][0],
                "id": "sed_01hnew",
                "number": 13,
                "title": body["title"],
                "description": body.get("description", ""),
                "specification": body.get("specification", ""),
                "acceptance_criteria": body.get("acceptance_criteria", []),
            }
            state["seeds"].append(new_seed)
            return httpx.Response(201, json=new_seed)
        if method == "PATCH" and path == "/api/v1/seeds/sed_01hratelimit":
            seed = state["seeds"][0]
            if seed["stage"] != "ideate" and (
                "specification" in body or "acceptance_criteria" in body
            ):
                return httpx.Response(
                    409,
                    json={
                        "error": {
                            "code": "conflict",
                            "message": (
                                "Seed specification and acceptance criteria are locked "
                                f"once Ideate is approved (current stage: '{seed['stage']}')."
                            ),
                            "details": {},
                        }
                    },
                )
            seed.update(body)
            return httpx.Response(200, json=seed)

        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/start":
            state["seeds"][0]["status"] = "queued"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/advance":
            state["seeds"][0]["stage"] = body.get("stage") or "execute"
            state["seeds"][0]["status"] = "queued"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/move-back":
            state["seeds"][0]["stage"] = body["stage"]
            state["seeds"][0]["status"] = "queued"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/stop":
            state["seeds"][0]["status"] = "stopped"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/resume":
            state["seeds"][0]["status"] = "queued"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/tasks":
            return httpx.Response(200, json=[])
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/approvals":
            return httpx.Response(200, json=state["approvals"])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/approvals":
            approval = {
                "id": "apr_01",
                "seed_id": "sed_01hratelimit",
                "stage": state["seeds"][0]["stage"],
                "decided_by": "usr_01hadmin",
                "decision": body["decision"],
                "comment": body.get("comment"),
                "created_at": NOW_ISO,
            }
            state["approvals"].append(approval)
            if body["decision"] == "approved":
                state["seeds"][0]["stage"] = "plan"
                state["seeds"][0]["status"] = "queued"
            elif body["decision"] == "changes_requested":
                state["seeds"][0]["status"] = "queued"
            elif body["decision"] == "rejected":
                state["seeds"][0]["status"] = "closed"
            return httpx.Response(201, json=approval)

        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/comments":
            return httpx.Response(200, json=state["comments"])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/comments":
            c = {
                "id": "cmt_01",
                "seed_id": "sed_01hratelimit",
                "author_type": "user",
                "author_id": "usr_01hadmin",
                "stage": state["seeds"][0]["stage"],
                "body": body["body"],
                "created_at": NOW_ISO,
            }
            state["comments"].append(c)
            return httpx.Response(201, json=c)

        if method == "GET" and path == "/api/v1/agents":
            return httpx.Response(200, json=state["agents"])
        if method == "GET" and path in {"/api/v1/agents/coder", "/api/v1/agents/agt_01hcoder"}:
            return httpx.Response(200, json=state["agents"][1])
        if method == "GET" and path in {
            "/api/v1/agents/architect",
            "/api/v1/agents/agt_01harchitect",
        }:
            return httpx.Response(200, json=state["agents"][0])

        if method == "GET" and path == "/api/v1/skills":
            return httpx.Response(200, json=state["skills"])
        if method == "GET" and path in {
            "/api/v1/skills/pytest-conventions",
            "/api/v1/skills/skl_01hpytest",
        }:
            return httpx.Response(200, json=state["skills"][0])
        if method == "POST" and path == "/api/v1/skills/pytest-conventions/archive":
            sk = {**state["skills"][0], "archived_at": NOW_ISO}
            return httpx.Response(200, json=sk)
        if method == "POST" and path == "/api/v1/skills/pytest-conventions/restore":
            sk = {**state["skills"][0], "archived_at": None}
            return httpx.Response(200, json=sk)

        if method == "GET" and path.startswith("/api/v1/policies/projects/"):
            return httpx.Response(200, json=state["policies"])
        if method == "GET" and path == "/api/v1/policies":
            return httpx.Response(200, json=state["policies"])

        if method == "GET" and path == "/api/v1/runs":
            return httpx.Response(200, json=state["runs"])
        if method == "GET" and path == "/api/v1/runs/run_01hfirst":
            return httpx.Response(200, json=state["runs"][0])
        if method == "POST" and path == "/api/v1/runs":
            new_run = {
                **state["runs"][0],
                "id": "run_01hretry",
                "attempt": 2,
                "status": "queued",
            }
            state["runs"].insert(0, new_run)
            return httpx.Response(201, json=new_run)

        if method == "POST" and path == "/api/v1/agents/coordinator/chat":
            return httpx.Response(
                200,
                json={
                    "agent_handle": "coordinator",
                    "reply": "Seed api#12 is awaiting approval in Ideate.",
                    "model": "claude-sonnet-4-20250514",
                    "input_tokens": 100,
                    "output_tokens": 25,
                },
            )

        if method == "GET" and path == "/api/v1/sessions":
            return httpx.Response(
                200,
                json=[
                    {
                        "kind": "chat",
                        "id": "asn_01hchat",
                        "project_id": "prj_01hapi",
                        "project_slug": "api",
                        "project_name": "Core API",
                        "name": "Fix rate limiter",
                        "ref": "main",
                        "status": "idle",
                        "is_running": False,
                        "agent_handle": "coder",
                    }
                ],
            )
        if method == "POST" and path in {
            "/api/v1/projects/api/agent-sessions",
            "/api/v1/projects/prj_01hapi/agent-sessions",
        }:
            return httpx.Response(
                201,
                json={
                    "id": "asn_01hchat",
                    "project_id": "prj_01hapi",
                    "ref": body.get("ref") or "main",
                    "agent_id": body.get("agent_id") or "agt_01hcoder",
                    "agent_handle": "coder",
                    "agent_name": "Coder",
                    "runtime": "claude-code",
                    "model": "claude-sonnet-4-20250514",
                    "title": body.get("title") or "Interactive Session",
                    "status": "running_turn",
                    "messages": [],
                },
            )
        if method == "POST" and path.endswith("/agent-sessions/asn_01hchat/messages"):
            return httpx.Response(
                200,
                json={
                    "id": "asn_01hchat",
                    "project_id": "prj_01hapi",
                    "ref": "main",
                    "agent_id": "agt_01hcoder",
                    "agent_handle": "coder",
                    "agent_name": "Coder",
                    "runtime": "claude-code",
                    "model": "claude-sonnet-4-20250514",
                    "title": "Interactive Session",
                    "status": "running_turn",
                    "messages": [],
                },
            )
        if method == "POST" and path.endswith("/agent-sessions/asn_01hchat/stop"):
            return httpx.Response(
                200,
                json={
                    "id": "asn_01hchat",
                    "project_id": "prj_01hapi",
                    "ref": "main",
                    "agent_id": "agt_01hcoder",
                    "agent_handle": "coder",
                    "agent_name": "Coder",
                    "runtime": "claude-code",
                    "model": "claude-sonnet-4-20250514",
                    "title": "Interactive Session",
                    "status": "stopped",
                    "messages": [],
                },
            )
        if method == "DELETE" and path.endswith("/agent-sessions/asn_01hchat"):
            return httpx.Response(204)
        if method == "GET" and path == "/api/v1/receipts/public-key":
            return httpx.Response(200, json=state["receipt_public_key"])
        if method == "GET" and path.startswith("/api/v1/seeds/") and path.endswith("/receipt"):
            return httpx.Response(200, json=state["receipt_envelope"])

        return httpx.Response(
            404, json={"error": {"code": "not_found", "message": f"Unhandled {method} {path}"}}
        )

    transport = httpx.MockTransport(handler)
    orig_init = GitSeedClient.__init__

    def patched_init(
        self: GitSeedClient,
        base_url: str,
        *,
        token: str | None = None,
        default_project: str | None = None,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        orig_init(
            self,
            base_url,
            token=token,
            default_project=default_project,
            timeout=timeout,
            transport=mock_transport,
        )

    mock_transport = transport
    monkeypatch.setattr(GitSeedClient, "__init__", patched_init)
    return state


def test_login_mints_hostname_token_and_never_prints_secret(
    isolated_cli_env: dict[str, Any],
) -> None:
    res = runner.invoke(
        app,
        [
            "login",
            "http://localhost:8420",
            "--email",
            "admin@gitseed.local",
            "--password",
            "secret123",
            "-p",
            "api",
        ],
    )
    assert res.exit_code == 0, res.output
    assert "admin@gitseed.local" in res.output
    assert "gsp_supersecret_token_value_do_not_print" not in res.output

    # Token name should default to "CLI on <hostname>"
    assert len(isolated_cli_env["minted_token_names"]) == 1
    assert isolated_cli_env["minted_token_names"][0].startswith("CLI on ")

    # Stored in credentials.json, never in config.json
    cfg_raw = cli_config.get_config_path().read_text(encoding="utf-8")
    assert "gsp_supersecret" not in cfg_raw
    assert cli_config.load_token("default") == "gsp_supersecret_token_value_do_not_print"

    # whoami, config get-contexts, and get tokens must never print raw token
    for args in [["whoami"], ["config", "get-contexts"], ["get", "tokens"]]:
        out = runner.invoke(app, args)
        assert out.exit_code == 0, out.output
        assert "gsp_supersecret_token_value_do_not_print" not in out.output


def test_get_and_export_md_roundtrip_and_trimmed_aliases(
    isolated_cli_env: dict[str, Any], tmp_path: Path
) -> None:
    runner.invoke(
        app,
        ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"],
    )

    # Removed short aliases (sd, tsk, bnd, usr) must be rejected
    bad_alias = runner.invoke(app, ["get", "sd"])
    assert bad_alias.exit_code == 1
    assert "Unknown resource type 'sd'" in bad_alias.output

    # Export seed as Markdown (-o md placed after subcommand, kubectl style)
    md_res = runner.invoke(app, ["get", "seed", "api#12", "-o", "md"])
    assert md_res.exit_code == 0, md_res.output
    assert "kind: Seed" in md_res.output
    assert "## Summary" in md_res.output
    assert "## Plan (read-only)" in md_res.output

    # Write exported markdown and apply it back; read-only section is stripped with a notice
    seed_file = tmp_path / "seed12.md"
    seed_file.write_text(md_res.output, encoding="utf-8")
    apply_res = runner.invoke(app, ["apply", "-f", str(seed_file)])
    assert apply_res.exit_code == 0, apply_res.output
    normalized_apply_output = " ".join(apply_res.output.split())
    assert "ignored read-only workflow sections (Plan)" in normalized_apply_output
    assert "configured" in normalized_apply_output


def test_create_seed_and_lock_approved_ideate_spec_409(
    isolated_cli_env: dict[str, Any], tmp_path: Path
) -> None:
    runner.invoke(
        app,
        ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"],
    )

    # Create seed from flags
    create_res = runner.invoke(
        app,
        [
            "create",
            "seed",
            "Implement webhook signatures",
            "--summary",
            "HMAC-SHA256 verification",
            "-p",
            "api",
        ],
    )
    assert create_res.exit_code == 0, create_res.output
    assert "Created seed api#13" in create_res.output

    # Approve api#12 (advances stage from ideate -> plan)
    apr_res = runner.invoke(app, ["approve", "api#12", "-m", "Spec looks solid, proceed to plan."])
    assert apr_res.exit_code == 0, apr_res.output
    assert "Approved stage ideate on seed #12" in apr_res.output
    assert isolated_cli_env["seeds"][0]["stage"] == "plan"

    # Attempting to modify Specification via apply after Ideate is approved returns 409 Conflict
    locked_md = """---
kind: Seed
id: sed_01hratelimit
title: Add sliding-window rate limiting
---

## Summary
Protect auth endpoints

## Specification
Sneaky spec change after Ideate approval!

## Acceptance Criteria
- [ ] Returns 429
"""
    locked_file = tmp_path / "locked.md"
    locked_file.write_text(locked_md, encoding="utf-8")
    conflict_res = runner.invoke(app, ["apply", "-f", str(locked_file)])
    assert conflict_res.exit_code == 1
    assert "Error (409)" in conflict_res.output
    assert "locked once Ideate" in conflict_res.output


def test_archive_restore_skills_and_delete_guard(
    isolated_cli_env: dict[str, Any],
) -> None:
    runner.invoke(
        app,
        ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"],
    )

    # Deleting a skill, agent, or project is blocked with guidance to use archive
    del_skill = runner.invoke(app, ["delete", "skill", "pytest-conventions"])
    assert del_skill.exit_code == 1
    assert "Skills are archived rather than deleted" in del_skill.output

    # Archive and restore skill
    arch_res = runner.invoke(app, ["archive", "skill", "pytest-conventions"])
    assert arch_res.exit_code == 0, arch_res.output
    assert "Archived skill pytest-conventions" in arch_res.output

    rest_res = runner.invoke(app, ["restore", "skill", "pytest-conventions"])
    assert rest_res.exit_code == 0, rest_res.output
    assert "Restored skill pytest-conventions" in rest_res.output


def test_steer_retry_guard_logs_and_ask(
    isolated_cli_env: dict[str, Any],
) -> None:
    runner.invoke(
        app,
        ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"],
    )

    # Steer automatically prefixes active stage agent handle (@coder when stage=execute)
    isolated_cli_env["seeds"][0]["stage"] = "execute"
    steer_res = runner.invoke(app, ["steer", "api#12", "Reuse Redis pool from packages/common"])
    assert steer_res.exit_code == 0, steer_res.output
    assert "Sent steering directive to @coder" in steer_res.output
    assert isolated_cli_env["comments"][-1]["body"].startswith("@coder ")

    # Retry is blocked when seed status is not failed or blocked
    isolated_cli_env["seeds"][0]["status"] = "running"
    retry_blocked = runner.invoke(app, ["retry", "api#12"])
    assert retry_blocked.exit_code == 1
    assert "Only 'failed' or" in retry_blocked.output
    assert "seeds can be retried" in retry_blocked.output

    # Retry succeeds when seed status is failed
    isolated_cli_env["seeds"][0]["status"] = "failed"
    retry_ok = runner.invoke(app, ["retry", "api#12"])
    assert retry_ok.exit_code == 0, retry_ok.output
    assert "Queued retry run" in retry_ok.output

    # Logs prints run log output
    logs_res = runner.invoke(app, ["logs", "api#12"])
    assert logs_res.exit_code == 0, logs_res.output
    assert "Drafted specification." in logs_res.output

    # Ask queries @coordinator
    ask_res = runner.invoke(app, ["ask", "What is waiting for approval?"])
    assert ask_res.exit_code == 0, ask_res.output
    assert "Seed api#12 is awaiting approval" in ask_res.output

    # Seed workflow lifecycle commands: start, advance, move-back, stop, resume
    for cmd_args, expected in [
        (["start", "api#12"], "Started seed #12"),
        (["advance", "api#12", "--stage", "execute"], "Advanced seed #12"),
        (["move-back", "api#12", "--stage", "plan", "-m", "Revisit plan"], "Moved seed #12"),
        (["stop", "api#12"], "Stopped seed #12"),
        (["resume", "api#12"], "Resumed seed #12"),
    ]:
        wf_res = runner.invoke(app, cmd_args)
        assert wf_res.exit_code == 0, wf_res.output
        assert expected in wf_res.output

    # Interactive sessions commands
    sess_list = runner.invoke(app, ["session", "list"])
    assert sess_list.exit_code == 0, sess_list.output
    assert "asn_01hchat" in sess_list.output

    sess_start = runner.invoke(
        app, ["session", "start", "-a", "@coder", "-m", "Add unit tests"]
    )
    assert sess_start.exit_code == 0, sess_start.output
    assert "Started agent session asn_01hchat" in sess_start.output

    sess_send = runner.invoke(app, ["session", "send", "asn_01hchat", "Check edge cases"])
    assert sess_send.exit_code == 0, sess_send.output
    assert "Sent message to session asn_01hchat" in sess_send.output

    sess_stop = runner.invoke(app, ["session", "stop", "asn_01hchat"])
    assert sess_stop.exit_code == 0, sess_stop.output
    assert "Stopped agent session asn_01hchat" in sess_stop.output

    sess_del = runner.invoke(app, ["session", "delete", "asn_01hchat"])
    assert sess_del.exit_code == 0, sess_del.output
    assert "Deleted agent session asn_01hchat" in sess_del.output


def test_global_options_parse_args_and_editor_shlex(
    isolated_cli_env: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    runner.invoke(
        app,
        ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"],
    )

    # Option value equal to "-p" or "-o" must not be mistakenly hoisted as a global flag
    sess_start = runner.invoke(
        app,
        ["session", "start", "--title", "-p", "-m", "-o", "-p", "api"],
    )
    assert sess_start.exit_code == 0, sess_start.output
    assert "Started agent session asn_01hchat" in sess_start.output

    # Global flags after `--` separator must not be hoisted
    sep_res = runner.invoke(
        app,
        ["create", "seed", "--summary", "Test summary", "-p", "api", "--", "-p"],
    )
    assert sep_res.exit_code == 0, sep_res.output
    assert isolated_cli_env["seeds"][-1]["title"] == "-p"

    # EDITOR="code --wait" is split with shlex.split in `gitseed edit`
    recorded_cmd: list[str] = []

    def fake_run(cmd: list[str], check: bool = False) -> Any:
        recorded_cmd.extend(cmd)
        Path(cmd[-1]).write_text(
            Path(cmd[-1]).read_text(encoding="utf-8") + "\n", encoding="utf-8"
        )
        return None

    monkeypatch.setenv("EDITOR", "code --wait")
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.setattr("gitseed_cli.commands.resources.subprocess.run", fake_run)
    edit_res = runner.invoke(app, ["edit", "seed", "api#12"])
    assert edit_res.exit_code == 0, edit_res.output
    assert recorded_cmd[:2] == ["code", "--wait"]


def test_receipt_get_and_verify_cli(
    isolated_cli_env: dict[str, Any], tmp_path: Path
) -> None:
    import base64
    import hashlib

    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

    priv = Ed25519PrivateKey.generate()
    pub = priv.public_key()
    pub_pem = (
        pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    pub_raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    key_id = hashlib.sha256(pub_raw).hexdigest()[:16]

    receipt_payload: dict[str, Any] = {
        "schema_version": "gitseed.receipt.v1",
        "seed": {
            "id": "sed_01hratelimit",
            "number": 12,
            "title": "Add sliding-window rate limiting",
            "project_id": "prj_01hapi",
            "project_slug": "api",
            "status": "awaiting_approval",
            "stage": "ideate",
            "branch_name": "gitseed/api/seed-12",
            "pr_url": "https://github.com/org/api/pull/12",
        },
        "policy": {
            "autonomy": "supervised",
            "min_approver_role": "maintainer",
            "prevent_self_approval": True,
            "blocked_paths": [],
        },
        "artifacts": {
            "specification_sha256": "a" * 64,
            "acceptance_criteria_sha256": "b" * 64,
            "plan_sha256": "c" * 64,
            "task_count": 2,
            "tasks": [
                {
                    "id": "tsk_01",
                    "title": "Implement sliding window Lua script",
                    "status": "completed",
                    "order_index": 0,
                },
                {
                    "id": "tsk_02",
                    "title": "Add FastAPI middleware tests",
                    "status": "completed",
                    "order_index": 1,
                },
            ],
        },
        "runs": [
            {
                "id": "run_01hideate",
                "stage": "ideate",
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "status": "succeeded",
                "cost_usd": 0.42,
                "cost_enforcement": "enforced",
                "input_tokens": 1200,
                "output_tokens": 350,
                "started_at": NOW_ISO,
                "completed_at": NOW_ISO,
                "trusted": None,
            }
        ],
        "approvals": [],
        "generated_at": NOW_ISO,
    }
    canonical = json.dumps(receipt_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    sig_b64 = (
        base64.urlsafe_b64encode(priv.sign(canonical.encode("utf-8")))
        .rstrip(b"=")
        .decode("ascii")
    )

    isolated_cli_env["receipt_public_key"] = {
        "key_id": key_id,
        "algorithm": "Ed25519",
        "public_key_pem": pub_pem,
    }
    isolated_cli_env["receipt_envelope"] = {
        "receipt": receipt_payload,
        "canonical_json": canonical,
        "signature": sig_b64,
        "algorithm": "Ed25519",
        "key_id": key_id,
        "public_key_pem": pub_pem,
        "markdown": "<!-- gitseed-governance-receipt -->\n### GitSeed Governance Receipt",
    }

    runner.invoke(
        app,
        ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"],
    )

    # 1. gitseed receipt get <seed_id> -o json
    get_res = runner.invoke(app, ["receipt", "get", "api#12", "-o", "json"])
    assert get_res.exit_code == 0, get_res.output
    parsed_env = json.loads(get_res.output)
    assert parsed_env["key_id"] == key_id
    assert parsed_env["receipt"]["seed"]["id"] == "sed_01hratelimit"

    # 2. Write to file and verify with `gitseed receipt verify`
    receipt_file = tmp_path / "receipt.json"
    pub_file = tmp_path / "receipt_pub.pem"
    pub_file.write_text(pub_pem, encoding="utf-8")
    get_file_res = runner.invoke(
        app, ["receipt", "get", "api#12", "-f", str(receipt_file)]
    )
    assert get_file_res.exit_code == 0, get_file_res.output
    assert receipt_file.is_file()

    verify_res = runner.invoke(
        app,
        ["receipt", "verify", str(receipt_file), "--public-key-file", str(pub_file)],
    )
    assert verify_res.exit_code == 0, verify_res.output
    assert "VERIFIED" in verify_res.output

    # 3. Verify from stdin ("-")
    stdin_res = runner.invoke(
        app,
        ["receipt", "verify", "-"],
        input=receipt_file.read_text(encoding="utf-8"),
    )
    assert stdin_res.exit_code == 0, stdin_res.output
    assert "VERIFIED" in stdin_res.output

    # 3b. Verify directly by Seed reference (`api#12` and `#12`)
    ref_res = runner.invoke(app, ["receipt", "verify", "api#12"])
    assert ref_res.exit_code == 0, ref_res.output
    assert "VERIFIED" in ref_res.output
    short_ref_res = runner.invoke(app, ["receipt", "verify", "#12"])
    assert short_ref_res.exit_code == 0, short_ref_res.output
    assert "VERIFIED" in short_ref_res.output

    # 4. Tamper with file and ensure `gitseed receipt verify` exits non-zero
    tampered = json.loads(receipt_file.read_text(encoding="utf-8"))
    tampered["receipt"]["runs"][0]["cost_usd"] = 0.0
    tampered_file = tmp_path / "tampered.json"
    tampered_file.write_text(json.dumps(tampered), encoding="utf-8")

    bad_res = runner.invoke(app, ["receipt", "verify", str(tampered_file)])
    assert bad_res.exit_code == 1
    assert "INVALID" in bad_res.output

    # 5. Table output displays locked artifact SHA-256 digests and task_count
    get_table_res = runner.invoke(app, ["receipt", "get", "api#12"])
    assert get_table_res.exit_code == 0, get_table_res.output
    assert "Spec SHA-256" in get_table_res.output
    assert "Plan SHA-256" in get_table_res.output
    assert "Task Count" in get_table_res.output

    # 6. Offline verification requires --public-key-file and rejects attacker-forged public_key_pem
    offline_no_key = runner.invoke(
        app,
        ["receipt", "verify", "--offline", str(receipt_file)],
    )
    assert offline_no_key.exit_code == 1
    assert "--public-key-file" in offline_no_key.output

    offline_valid = runner.invoke(
        app,
        [
            "receipt",
            "verify",
            "--offline",
            str(receipt_file),
            "--public-key-file",
            str(pub_file),
        ],
    )
    assert offline_valid.exit_code == 0, offline_valid.output
    assert "VERIFIED" in offline_valid.output
    assert "Spec SHA-256" in offline_valid.output

    # Attacker signs a forged receipt with their own Ed25519 key and embeds their public_key_pem
    attacker_priv = Ed25519PrivateKey.generate()
    attacker_pub = attacker_priv.public_key()
    attacker_pub_pem = (
        attacker_pub.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    attacker_raw = attacker_pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    attacker_kid = hashlib.sha256(attacker_raw).hexdigest()[:16]
    forged_payload = {
        **receipt_payload,
        "policy": {**receipt_payload["policy"], "prevent_self_approval": False},
    }
    forged_canonical = json.dumps(
        forged_payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    forged_sig = (
        base64.urlsafe_b64encode(attacker_priv.sign(forged_canonical.encode("utf-8")))
        .rstrip(b"=")
        .decode("ascii")
    )
    forged_envelope = {
        "receipt": forged_payload,
        "canonical_json": forged_canonical,
        "signature": forged_sig,
        "algorithm": "Ed25519",
        "key_id": attacker_kid,
        "public_key_pem": attacker_pub_pem,
    }
    forged_file = tmp_path / "forged_receipt.json"
    forged_file.write_text(json.dumps(forged_envelope), encoding="utf-8")

    # Rejected in --offline mode without --public-key-file (never trusts embedded public_key_pem)
    forged_offline_no_key = runner.invoke(
        app,
        ["receipt", "verify", "--offline", str(forged_file)],
    )
    assert forged_offline_no_key.exit_code == 1

    # Rejected in --offline mode when verified against the legitimate trusted public key
    forged_offline_real_key = runner.invoke(
        app,
        [
            "receipt",
            "verify",
            "--offline",
            str(forged_file),
            "--public-key-file",
            str(pub_file),
        ],
    )
    assert forged_offline_real_key.exit_code == 1
    assert "INVALID" in forged_offline_real_key.output

    # Rejected in online mode because attacker key_id is not trusted by server
    forged_online = runner.invoke(app, ["receipt", "verify", str(forged_file)])
    assert forged_online.exit_code == 1
    assert "INVALID" in forged_online.output


