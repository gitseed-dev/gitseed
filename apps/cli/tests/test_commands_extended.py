from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import click
import httpx
import pytest
from gitseed_cli import config as cli_config
from gitseed_cli.client import CLIError, GitSeedClient
from gitseed_cli.main import app
from typer.testing import CliRunner

runner = CliRunner()

NOW_DT = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
NOW_ISO = NOW_DT.isoformat()


def _extended_mock_state() -> dict[str, Any]:
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
        "users": [
            {
                "id": "usr_01hadmin",
                "email": "admin@gitseed.local",
                "name": "Admin User",
                "preferred_name": "Admin",
                "org_role": "admin",
                "status": "active",
                "last_active_at": NOW_ISO,
                "created_at": NOW_ISO,
            },
            {
                "id": "usr_01hinvited",
                "email": "dev@gitseed.local",
                "name": "Dev User",
                "preferred_name": None,
                "org_role": "member",
                "status": "invited",
                "last_active_at": None,
                "created_at": NOW_ISO,
            },
        ],
        "projects": [
            {
                "id": "prj_01hapi",
                "slug": "api",
                "name": "Core API",
                "description": "Backend API service",
                "visibility": "private",
                "monthly_budget": "500.00",
                "seed_budget_cap": "25.00",
                "spend_checkpoint": "10.00",
                "max_concurrent_runs": 4,
                "backends_restricted": False,
                "tags": ["backend"],
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
        "tasks": [
            {
                "id": "tsk_01",
                "seed_id": "sed_01hratelimit",
                "position": 0,
                "title": "Implement rate limiter middleware",
                "description": "Add sliding window counter",
                "status": "done",
                "started_at": NOW_ISO,
                "completed_at": NOW_ISO,
                "created_at": NOW_ISO,
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
                "run_count": 5,
                "succeeded_runs": 5,
                "total_cost": "1.25",
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
                "run_count": 3,
                "succeeded_runs": 2,
                "total_cost": "0.80",
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
                "project_id": "prj_01hapi",
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
                "project_id": "prj_01hapi",
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
        "backends": [
            {
                "id": "bnd_01hmodel",
                "name": "Anthropic Gateway",
                "type": "inference",
                "provider": "anthropic",
                "endpoint": "https://api.anthropic.com",
                "health": "healthy",
                "enabled": True,
                "access_restricted": False,
                "capacity": 10,
                "config": {},
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            }
        ],
        "sandboxes": [
            {
                "id": "sbx_01hsession",
                "project_id": "prj_01hapi",
                "seed_id": "sed_01hratelimit",
                "repository_id": 1,
                "ref": "main",
                "image": "python:3.12-slim",
                "setup_command": "pip install -e .",
                "command": "pytest -q",
                "status": "running",
                "exit_code": None,
                "log_tail": "=== test session starts ===\n1 passed\n",
                "artifacts": [],
                "started_at": NOW_ISO,
                "finished_at": None,
                "created_by": "usr_01hadmin",
                "created_at": NOW_ISO,
            }
        ],
        "sandbox_config": {
            "project_id": "prj_01hapi",
            "repository_id": 1,
            "ref": "main",
            "source": "run_json",
            "image": "python:3.12-slim",
            "setup_command": "pip install -e .",
            "run_command": "python -m app",
            "test_command": "pytest -q",
            "artifact_globs": ["dist/*"],
        },
        "changes": {
            "seed_id": "sed_01hratelimit",
            "seed_number": 12,
            "repository_id": 1,
            "repository_full_name": "org/api",
            "base_ref": "main",
            "head_ref": "gitseed/seed-12",
            "total_commits": 1,
            "total_files": 1,
            "total_additions": 10,
            "total_deletions": 2,
            "commits": [
                {
                    "sha": "9f2c1e7abcd1234567890",
                    "short_sha": "9f2c1e7",
                    "message": "feat(seed-12): add rate limiter",
                    "author_name": "GitSeed Agent",
                    "committed_at": NOW_ISO,
                }
            ],
            "files": [
                {
                    "path": "src/rate_limit.py",
                    "status": "added",
                    "additions": 10,
                    "deletions": 2,
                    "patch": "@@ -0,0 +1,10 @@\n+def check_rate():\n+    return True\n",
                }
            ],
        },
        "activity": [
            {
                "id": 1,
                "project_id": "prj_01hapi",
                "seed_id": "sed_01hratelimit",
                "run_id": "run_01hfirst",
                "actor_type": "user",
                "actor_id": "usr_01hadmin",
                "type": "seed.created",
                "data": {"stage": "ideate"},
                "occurred_at": NOW_ISO,
            }
        ],
        "tokens": [
            {
                "id": "tok_01hcli",
                "name": "CLI on workstation",
                "token_prefix": "gsp_abcd1234",
                "expires_at": NOW_ISO,
                "last_used_at": NOW_ISO,
                "revoked_at": None,
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
                "status": "running",
                "input_tokens": 1200,
                "output_tokens": 450,
                "cost": "0.18",
                "output": {"log_tail": "Drafted specification.\nDone."},
                "error": "minor warning",
                "started_at": NOW_ISO,
                "finished_at": NOW_ISO,
                "created_at": NOW_ISO,
            }
        ],
        "comments": [
            {
                "id": "cmt_01",
                "seed_id": "sed_01hratelimit",
                "author_type": "user",
                "author_id": "usr_01hadmin",
                "stage": "ideate",
                "body": "Looks promising!",
                "deleted_at": None,
                "created_at": NOW_ISO,
            }
        ],
        "approvals": [
            {
                "id": "apr_01",
                "seed_id": "sed_01hratelimit",
                "stage": "ideate",
                "decided_by": "usr_01hadmin",
                "decision": "approved",
                "comment": "LGTM",
                "created_at": NOW_ISO,
            }
        ],
    }


@pytest.fixture()
def full_cli_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    monkeypatch.setenv("GITSEED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITSEED_DISABLE_KEYRING", "1")
    state = _extended_mock_state()

    def handler(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path).replace("/seeds/api#12", "/seeds/sed_01hratelimit")
        method = request.method
        body = json.loads(request.content.decode()) if request.content else {}

        # Auth & system
        if method == "GET" and path == "/api/v1/auth/me":
            return httpx.Response(200, json=state["user"])
        if method == "GET" and path == "/api/v1/auth/tokens":
            return httpx.Response(200, json=state["tokens"])
        if method == "DELETE" and path.startswith("/api/v1/auth/tokens/"):
            return httpx.Response(204)
        if method == "GET" and path in {"/healthz", "/api/v1/health"}:
            return httpx.Response(
                200,
                json={
                    "status": "ok",
                    "service": "gitseed-core",
                    "version": "0.1.0",
                    "environment": "test",
                    "database": "ok",
                    "redis": "ok",
                    "timestamp": NOW_ISO,
                },
            )
        if method == "GET" and path in {"/version", "/api/v1/version"}:
            return httpx.Response(
                200,
                json={
                    "platform": "gitseed",
                    "service": "gitseed-core",
                    "version": "0.1.0",
                    "environment": "test",
                },
            )

        # Projects
        if method == "GET" and path == "/api/v1/projects":
            return httpx.Response(200, json=state["projects"])
        if method == "GET" and path in {"/api/v1/projects/api", "/api/v1/projects/prj_01hapi"}:
            return httpx.Response(200, json=state["projects"][0])
        if method == "POST" and path == "/api/v1/projects":
            created = {
                **state["projects"][0],
                "id": "prj_02",
                "slug": body["slug"],
                "name": body["name"],
            }
            state["projects"].append(created)
            return httpx.Response(201, json=created)
        if method == "PATCH" and path in {"/api/v1/projects/api", "/api/v1/projects/prj_01hapi"}:
            state["projects"][0].update(body)
            return httpx.Response(200, json=state["projects"][0])
        if method == "POST" and path.endswith("/projects/api/archive"):
            proj = {**state["projects"][0], "archived_at": NOW_ISO}
            return httpx.Response(200, json=proj)
        if method == "POST" and path.endswith("/projects/api/restore"):
            proj = {**state["projects"][0], "archived_at": None}
            return httpx.Response(200, json=proj)

        # Sandboxes
        if method == "GET" and path.endswith("/sandboxes/config"):
            return httpx.Response(200, json=state["sandbox_config"])
        if method == "GET" and path.endswith("/sandboxes"):
            return httpx.Response(200, json=state["sandboxes"])
        if method == "POST" and path.endswith("/sandboxes"):
            sbx = {
                **state["sandboxes"][0],
                "id": "sbx_01hnew",
                "command": body["command"],
                "image": body.get("image", "python:3.12-slim"),
                "ref": body.get("ref") or "main",
            }
            return httpx.Response(201, json=sbx)
        if method == "GET" and "/sandboxes/sbx_" in path:
            return httpx.Response(200, json=state["sandboxes"][0])
        if method == "POST" and path.endswith("/input"):
            sbx = {
                **state["sandboxes"][0],
                "log_tail": state["sandboxes"][0]["log_tail"] + body["input"],
            }
            return httpx.Response(200, json=sbx)
        if method == "POST" and path.endswith("/stop"):
            sbx = {**state["sandboxes"][0], "status": "stopped", "exit_code": 130}
            state["sandboxes"][0] = sbx
            return httpx.Response(200, json=sbx)

        # Seeds
        if method == "GET" and path == "/api/v1/seeds":
            return httpx.Response(200, json=state["seeds"])
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit":
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds":
            created = {**state["seeds"][0], "id": "sed_02", "number": 13, "title": body["title"]}
            return httpx.Response(201, json=created)
        if method == "PATCH" and path == "/api/v1/seeds/sed_01hratelimit":
            state["seeds"][0].update(body)
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/close":
            state["seeds"][0]["status"] = "closed"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/reopen":
            state["seeds"][0]["status"] = "queued"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/approve-checkpoint":
            state["seeds"][0]["checkpoint_approved_through"] = str(body["approved_through"])
            state["seeds"][0]["status"] = "running"
            return httpx.Response(200, json=state["seeds"][0])
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/tasks":
            return httpx.Response(200, json=state["tasks"])
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/approvals":
            return httpx.Response(200, json=state["approvals"])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/approvals":
            apr = {
                "id": "apr_02",
                "seed_id": "sed_01hratelimit",
                "stage": state["seeds"][0]["stage"],
                "decided_by": "usr_01hadmin",
                "decision": body["decision"],
                "comment": body.get("comment"),
                "created_at": NOW_ISO,
            }
            if body["decision"] == "rejected":
                state["seeds"][0]["status"] = "closed"
            elif body["decision"] == "changes_requested":
                state["seeds"][0]["status"] = "queued"
            else:
                state["seeds"][0]["stage"] = "plan"
                state["seeds"][0]["status"] = "queued"
            return httpx.Response(201, json=apr)
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/comments":
            return httpx.Response(200, json=state["comments"])
        if method == "POST" and path == "/api/v1/seeds/sed_01hratelimit/comments":
            cmt = {
                "id": "cmt_02",
                "seed_id": "sed_01hratelimit",
                "author_type": "user",
                "author_id": "usr_01hadmin",
                "stage": state["seeds"][0]["stage"],
                "body": body["body"],
                "created_at": NOW_ISO,
            }
            state["comments"].append(cmt)
            return httpx.Response(201, json=cmt)
        if method == "GET" and path == "/api/v1/seeds/sed_01hratelimit/changes":
            return httpx.Response(200, json=state["changes"])

        # Runs
        if method == "GET" and path == "/api/v1/runs":
            return httpx.Response(200, json=state["runs"])
        if method == "GET" and path == "/api/v1/runs/run_01hfirst":
            return httpx.Response(200, json=state["runs"][0])
        if method == "POST" and path == "/api/v1/runs/run_01hfirst/cancel":
            cancelled = {**state["runs"][0], "status": "cancelled"}
            return httpx.Response(200, json=cancelled)

        # Agents
        if method == "GET" and path == "/api/v1/agents":
            return httpx.Response(200, json=state["agents"])
        if method == "GET" and path in {
            "/api/v1/agents/architect",
            "/api/v1/agents/agt_01harchitect",
        }:
            return httpx.Response(200, json=state["agents"][0])
        if method == "GET" and path in {"/api/v1/agents/coder", "/api/v1/agents/agt_01hcoder"}:
            return httpx.Response(200, json=state["agents"][1])
        if method == "POST" and path == "/api/v1/agents":
            created = {
                **state["agents"][0],
                "id": "agt_new",
                "handle": body["handle"],
                "name": body["name"],
            }
            return httpx.Response(201, json=created)
        if method == "PATCH" and path.startswith("/api/v1/agents/"):
            updated = {**state["agents"][0], **body}
            return httpx.Response(200, json=updated)
        if method == "POST" and path.endswith("/agents/architect/archive"):
            return httpx.Response(200, json={**state["agents"][0], "archived_at": NOW_ISO})
        if method == "POST" and path.endswith("/agents/architect/restore"):
            return httpx.Response(200, json={**state["agents"][0], "archived_at": None})

        # Skills
        if method == "GET" and path == "/api/v1/skills":
            return httpx.Response(200, json=state["skills"])
        if method == "GET" and path in {
            "/api/v1/skills/pytest-conventions",
            "/api/v1/skills/skl_01hpytest",
        }:
            return httpx.Response(200, json=state["skills"][0])
        if method == "POST" and path == "/api/v1/skills":
            created = {
                **state["skills"][0],
                "id": "skl_new",
                "slug": body["slug"],
                "name": body["name"],
            }
            return httpx.Response(201, json=created)
        if method == "PATCH" and path.startswith("/api/v1/skills/"):
            updated = {**state["skills"][0], **body}
            return httpx.Response(200, json=updated)

        # Policies
        if method == "GET" and path.startswith("/api/v1/policies/projects/"):
            return httpx.Response(200, json=state["policies"])
        if method == "GET" and path == "/api/v1/policies":
            return httpx.Response(200, json=state["policies"])
        if method == "PATCH" and path.startswith("/api/v1/policies/projects/"):
            return httpx.Response(200, json=state["policies"][0])
        if method == "PATCH" and path.startswith("/api/v1/policies/"):
            return httpx.Response(200, json=state["policies"][0])

        # Backends
        if method == "GET" and path == "/api/v1/backends":
            return httpx.Response(200, json=state["backends"])
        if method == "GET" and path == "/api/v1/backends/bnd_01hmodel":
            return httpx.Response(200, json=state["backends"][0])
        if method == "DELETE" and path == "/api/v1/backends/bnd_01hmodel":
            return httpx.Response(204)

        # Users
        if method == "GET" and path == "/api/v1/users":
            return httpx.Response(200, json=state["users"])
        if method == "GET" and path == "/api/v1/users/usr_01hadmin":
            return httpx.Response(200, json=state["users"][0])
        if method == "DELETE" and path == "/api/v1/users/usr_01hinvited":
            return httpx.Response(204)

        # Activity
        if method == "GET" and path == "/api/v1/activity":
            return httpx.Response(200, json=state["activity"])

        # Coordinator
        if method == "POST" and path == "/api/v1/agents/coordinator/chat":
            return httpx.Response(
                200,
                json={
                    "agent_handle": "coordinator",
                    "reply": "Everything is healthy.",
                    "model": "claude-sonnet-4-20250514",
                    "input_tokens": 50,
                    "output_tokens": 12,
                },
            )

        return httpx.Response(404, json={"detail": f"Not found: {method} {path}"})

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
    runner.invoke(app, ["login", "http://localhost:8420", "--token", "gsp_testtoken", "-p", "api"])
    return state


def test_sandbox_commands_cli(full_cli_env: dict[str, Any]) -> None:
    # sandbox config (table and json)
    res_cfg = runner.invoke(app, ["sandbox", "config"])
    assert res_cfg.exit_code == 0, res_cfg.output
    assert "python:3.12-slim" in res_cfg.output

    res_cfg_json = runner.invoke(app, ["sandbox", "config", "-o", "json"])
    assert res_cfg_json.exit_code == 0
    assert json.loads(res_cfg_json.output)["image"] == "python:3.12-slim"

    # sandbox start with defaults from project config, with --seed, and with -o json
    res_start = runner.invoke(app, ["sandbox", "start", "--seed", "api#12", "--stdin", "hello\n"])
    assert res_start.exit_code == 0, res_start.output
    assert "Started sandbox sbx_01hnew" in res_start.output

    res_start_json = runner.invoke(
        app,
        ["sandbox", "start", "-c", "pytest", "--image", "python:3.12-slim", "-o", "json"],
    )
    assert res_start_json.exit_code == 0
    assert json.loads(res_start_json.output)["id"] == "sbx_01hnew"

    # sandbox input (table and json)
    res_in = runner.invoke(app, ["sandbox", "input", "sbx_01hsession", "echo hi", "--eof"])
    assert res_in.exit_code == 0, res_in.output
    assert "Sent input to sandbox sbx_01hsession" in res_in.output

    res_in_json = runner.invoke(
        app, ["sandbox", "input", "sbx_01hsession", "echo hi", "-o", "json"]
    )
    assert res_in_json.exit_code == 0

    # sandbox logs (table and json)
    res_logs = runner.invoke(app, ["sandbox", "logs", "sbx_01hsession"])
    assert res_logs.exit_code == 0
    assert "1 passed" in res_logs.output

    res_logs_json = runner.invoke(app, ["sandbox", "logs", "sbx_01hsession", "-o", "json"])
    assert res_logs_json.exit_code == 0
    assert json.loads(res_logs_json.output)["id"] == "sbx_01hsession"

    # sandbox stop (table and json)
    res_stop = runner.invoke(app, ["sandbox", "stop", "sbx_01hsession"])
    assert res_stop.exit_code == 0
    assert "Stopped sandbox sbx_01hsession" in res_stop.output

    res_stop_json = runner.invoke(app, ["sandbox", "stop", "sbx_01hsession", "-o", "json"])
    assert res_stop_json.exit_code == 0
    assert json.loads(res_stop_json.output)["status"] == "stopped"


def test_resources_get_describe_diff_events_archive_restore_delete(
    full_cli_env: dict[str, Any], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # get list and single item for all resource types in table, wide, and json
    resource_cases = [
        (["get", "projects"], ["get", "project", "api"]),
        (["get", "seeds"], ["get", "seed", "api#12"]),
        (["get", "tasks", "--seed", "api#12"], ["get", "tasks", "api#12"]),
        (["get", "runs", "--seed", "api#12"], ["get", "run", "run_01hfirst"]),
        (["get", "agents"], ["get", "agent", "architect"]),
        (["get", "skills"], ["get", "skill", "pytest-conventions"]),
        (["get", "policies"], ["get", "policy", "ideate"]),
        (["get", "backends"], ["get", "backend", "bnd_01hmodel"]),
        (["get", "sandboxes"], ["get", "sandbox", "sbx_01hsession"]),
        (["get", "users"], ["get", "user", "usr_01hadmin"]),
        (["get", "tokens"], ["get", "token", "tok_01hcli"]),
    ]
    for list_cmd, single_cmd in resource_cases:
        for fmt in ("table", "wide", "json"):
            r1 = runner.invoke(app, [*list_cmd, "-o", fmt])
            assert r1.exit_code == 0, f"{list_cmd} -o {fmt}: {r1.output}"
            r2 = runner.invoke(app, [*single_cmd, "-o", fmt])
            assert r2.exit_code == 0, f"{single_cmd} -o {fmt}: {r2.output}"

    # -o md for supported resources
    for res_type, res_id in [
        ("seed", "api#12"),
        ("agent", "architect"),
        ("skill", "pytest-conventions"),
        ("project", "api"),
        ("policy", "ideate"),
    ]:
        md_out = runner.invoke(app, ["get", res_type, res_id, "-o", "md"])
        assert md_out.exit_code == 0, md_out.output
        assert "kind:" in md_out.output

    # describe for all supported resources in table and json
    for res_type, res_id in [
        ("seed", "api#12"),
        ("project", "api"),
        ("agent", "architect"),
        ("skill", "pytest-conventions"),
        ("run", "run_01hfirst"),
        ("backend", "bnd_01hmodel"),
        ("user", "usr_01hadmin"),
        ("sandbox", "sbx_01hsession"),
    ]:
        d_tbl = runner.invoke(app, ["describe", res_type, res_id])
        assert d_tbl.exit_code == 0, d_tbl.output
        d_json = runner.invoke(app, ["describe", res_type, res_id, "-o", "json"])
        assert d_json.exit_code == 0, d_json.output

    # diff and events
    assert runner.invoke(app, ["diff", "api#12"]).exit_code == 0
    assert runner.invoke(app, ["diff", "api#12", "-o", "json"]).exit_code == 0
    assert runner.invoke(app, ["events", "--seed", "api#12"]).exit_code == 0
    assert runner.invoke(app, ["events", "-o", "json"]).exit_code == 0

    # archive and restore project and agent
    for res_type, res_id in [("project", "api"), ("agent", "architect")]:
        assert runner.invoke(app, ["archive", res_type, res_id]).exit_code == 0
        assert runner.invoke(app, ["archive", res_type, res_id, "-o", "json"]).exit_code == 0
        assert runner.invoke(app, ["restore", res_type, res_id]).exit_code == 0
        assert runner.invoke(app, ["restore", res_type, res_id, "-o", "json"]).exit_code == 0

    # delete token, backend, user
    assert runner.invoke(app, ["delete", "token", "tok_01hcli"]).exit_code == 0
    assert runner.invoke(app, ["delete", "backend", "bnd_01hmodel"]).exit_code == 0
    assert runner.invoke(app, ["delete", "user", "usr_01hinvited"]).exit_code == 0

    # apply directory of manifests (Agent, Skill, Project, Policy)
    manifests_dir = tmp_path / "manifests"
    manifests_dir.mkdir()
    (manifests_dir / "01-agent.md").write_text(
        "---\nkind: Agent\nhandle: architect\nname: Architect Updated\n"
        "runtime: claude-code\nmodel: claude-sonnet-4-20250514\n---\nUpdated prompt.\n",
        encoding="utf-8",
    )
    (manifests_dir / "02-skill.md").write_text(
        "---\nkind: Skill\nslug: pytest-conventions\nname: Pytest Updated\n---\nUse fixtures.\n",
        encoding="utf-8",
    )
    (manifests_dir / "03-project.md").write_text(
        "---\nkind: Project\nslug: api\nname: Core API Updated\n---\nUpdated desc.\n",
        encoding="utf-8",
    )
    (manifests_dir / "04-policy.md").write_text(
        "---\nkind: Policy\nstage: ideate\nproject: api\n"
        "agent: '@architect'\nrequires_approval: true\n---\n",
        encoding="utf-8",
    )
    apply_dir_res = runner.invoke(app, ["apply", "-f", str(manifests_dir), "-o", "json"])
    assert apply_dir_res.exit_code == 0, apply_dir_res.output
    assert len(json.loads(apply_dir_res.output)["applied"]) == 4

    # edit command with mocked click.edit
    monkeypatch.delenv("VISUAL", raising=False)
    monkeypatch.delenv("EDITOR", raising=False)
    monkeypatch.setattr(
        click,
        "edit",
        lambda text, extension=".md": text.replace(
            "Standard pytest patterns", "Edited description"
        ),
    )
    edit_res = runner.invoke(app, ["edit", "skill", "pytest-conventions"])
    assert edit_res.exit_code == 0, edit_res.output
    assert "configured" in edit_res.output


def test_workflow_and_auth_commands(full_cli_env: dict[str, Any]) -> None:
    # approve --checkpoint
    cp_res = runner.invoke(app, ["approve", "api#12", "--checkpoint", "20.0"])
    assert cp_res.exit_code == 0, cp_res.output
    assert "$20.00" in cp_res.output

    # request-changes and reject
    rc_res = runner.invoke(app, ["request-changes", "api#12", "-m", "Add rate limit headers"])
    assert rc_res.exit_code == 0, rc_res.output
    assert "Requested changes" in rc_res.output

    rej_res = runner.invoke(app, ["reject", "api#12", "-m", "Out of scope"])
    assert rej_res.exit_code == 0, rej_res.output
    assert "Rejected seed #12" in rej_res.output

    # close and reopen
    close_res = runner.invoke(app, ["close", "api#12"])
    assert close_res.exit_code == 0, close_res.output
    reopen_res = runner.invoke(app, ["reopen", "api#12"])
    assert reopen_res.exit_code == 0, reopen_res.output

    # comment and steer with explicit --agent
    cmt_res = runner.invoke(app, ["comment", "api#12", "Great progress"])
    assert cmt_res.exit_code == 0, cmt_res.output
    steer_res = runner.invoke(
        app, ["steer", "api#12", "Check edge cases", "--agent", "@architect", "-o", "json"]
    )
    assert steer_res.exit_code == 0, steer_res.output

    # cancel by seed reference and by run ID
    cancel_seed = runner.invoke(app, ["cancel", "api#12"])
    assert cancel_seed.exit_code == 0, cancel_seed.output
    cancel_run = runner.invoke(app, ["cancel", "run_01hfirst", "-o", "json"])
    assert cancel_run.exit_code == 0, cancel_run.output

    # ask with --seed and -o json
    ask_json = runner.invoke(app, ["ask", "Status?", "--seed", "api#12", "-o", "json"])
    assert ask_json.exit_code == 0, ask_json.output

    # status (table and json), config use-context, config use-project, logout, --version
    st_tbl = runner.invoke(app, ["status"])
    assert st_tbl.exit_code == 0, st_tbl.output
    st_json = runner.invoke(app, ["status", "-o", "json"])
    assert st_json.exit_code == 0
    assert json.loads(st_json.output)["health"]["status"] == "ok"

    uc_res = runner.invoke(
        app, ["config", "use-context", "staging", "--api-url", "http://staging:8420"]
    )
    assert uc_res.exit_code == 0, uc_res.output
    assert cli_config.load_config().current_context == "staging"

    up_set = runner.invoke(app, ["config", "use-project", "web"])
    assert up_set.exit_code == 0
    up_clear = runner.invoke(app, ["config", "use-project", "-"])
    assert up_clear.exit_code == 0

    ctx_json = runner.invoke(app, ["config", "get-contexts", "-o", "json"])
    assert ctx_json.exit_code == 0

    logout_res = runner.invoke(app, ["logout", "--context", "default"])
    assert logout_res.exit_code == 0

    ver_res = runner.invoke(app, ["--version"])
    assert ver_res.exit_code == 0
    assert "gitseed" in ver_res.output


def test_client_error_extraction_and_reference_resolution() -> None:
    err_400 = GitSeedClient._extract_error({"detail": "Invalid input"}, 400)
    assert "Invalid input (HTTP 400)" in err_400
    assert "Validation error: body_field: bad (HTTP 422)" in GitSeedClient._extract_error(
        {"detail": [{"loc": ["body", "body_field"], "msg": "bad"}]}, 422
    )
    assert "Conflict msg (HTTP 409)" in GitSeedClient._extract_error(
        {"error": {"message": "Conflict msg"}}, 409
    )
    assert "Plain error (HTTP 500)" in GitSeedClient._extract_error("Plain error", 500)

    c = GitSeedClient("http://localhost:8420", default_project="api")
    assert c.resolve_project_ref() == "api"
    assert c.resolve_seed_ref("sed_123") == "sed_123"
    assert c.resolve_seed_ref("12") == "api#12"
    assert c.resolve_seed_ref("#12") == "api#12"
    assert c.resolve_seed_ref("web#5") == "web#5"
    with pytest.raises(CLIError):
        c.resolve_seed_ref("api#notanumber")
    with pytest.raises(CLIError):
        GitSeedClient("http://localhost:8420").resolve_project_ref()


def test_logs_sandbox_logs_and_describe_escape_rich_markup(
    full_cli_env: dict[str, Any],
) -> None:
    hostile = "line with [/div] and [0m] and arr[/] markup"
    full_cli_env["runs"][0]["error"] = f"Runner error: {hostile}"
    full_cli_env["runs"][0]["output"] = {"log_tail": f"stdout: {hostile}\n"}
    full_cli_env["run_logs"] = {
        "run_01hfirst": {"run_id": "run_01hfirst", "log_tail": f"stream: {hostile}\n"}
    }
    full_cli_env["sandboxes"][0]["log_tail"] = f"sbx out: {hostile}\n"
    full_cli_env["seeds"][0]["title"] = f"Title {hostile}"
    full_cli_env["seeds"][0]["description"] = f"Desc {hostile}"
    full_cli_env["projects"][0]["description"] = f"Proj {hostile}"
    full_cli_env["agents"][0]["instructions"] = f"Prompt {hostile}"
    full_cli_env["skills"][0]["content"] = f"Skill {hostile}"

    res_logs = runner.invoke(app, ["logs", "run_01hfirst"])
    assert res_logs.exit_code == 0, res_logs.output
    assert "[/div]" in res_logs.output
    assert "arr[/]" in res_logs.output

    res_sbx_logs = runner.invoke(app, ["sandbox", "logs", "sbx_01hsession"])
    assert res_sbx_logs.exit_code == 0, res_sbx_logs.output
    assert "[/div]" in res_sbx_logs.output
    assert "[0m]" in res_sbx_logs.output

    for res_type, res_id in [
        ("seed", "api#12"),
        ("project", "api"),
        ("agent", "architect"),
        ("skill", "pytest-conventions"),
        ("run", "run_01hfirst"),
    ]:
        res_desc = runner.invoke(app, ["describe", res_type, res_id])
        assert res_desc.exit_code == 0, f"describe {res_type}: {res_desc.output}"
        assert "[/div]" in res_desc.output

