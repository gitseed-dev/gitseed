from __future__ import annotations

import asyncio
import io
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from gitseed_mcp.config import MCPConfig, MCPSettings
from gitseed_mcp.main import SSESession, build_arg_parser, create_app, run_stdio_async
from gitseed_mcp.server import (
    PROMPTS_CATALOG,
    RESOURCE_TEMPLATES_CATALOG,
    RESOURCES_CATALOG,
    TOOLS,
    TOOLS_CATALOG,
    TOOLSET_BY_TOOL,
    TOOLSETS,
    GitSeedMCPServer,
)
from gitseed_runner.adapters import create_runtime_adapter


def _make_mock_api_transport(
    recorded_requests: list[dict[str, Any]],
) -> httpx.MockTransport:
    """Create a mock upstream GitSeed Control Plane API transport."""

    async def handler(request: httpx.Request) -> httpx.Response:
        body: Any = None
        if request.content:
            try:
                body = json.loads(request.content.decode("utf-8"))
            except ValueError:
                body = request.content.decode("utf-8", errors="replace")

        recorded_requests.append(
            {
                "method": request.method,
                "path": request.url.path,
                "raw_path": request.url.raw_path.decode("ascii"),
                "query": dict(request.url.params),
                "headers": dict(request.headers),
                "body": body,
            }
        )

        path = request.url.path
        method = request.method

        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        if path == "/version":
            return httpx.Response(200, json={"version": "0.1.0-test"})
        if path == "/api/v1/auth/me":
            auth = request.headers.get("authorization", "")
            if auth == "Bearer gsp_valid_token":
                return httpx.Response(
                    200,
                    json={
                        "id": "usr_1",
                        "email": "admin@gitseed.local",
                        "name": "Admin",
                        "role": "admin",
                    },
                )
            return httpx.Response(401, json={"detail": "Invalid or expired token"})

        if path == "/api/v1/projects" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "prj_1", "slug": "gitseed", "name": "GitSeed"}],
            )
        if path == "/api/v1/projects" and method == "POST":
            return httpx.Response(
                201,
                json={"id": "prj_new", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/projects/gitseed" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "prj_1", "slug": "gitseed", "name": "GitSeed"},
            )
        if path == "/api/v1/projects/gitseed" and method == "PATCH":
            return httpx.Response(
                200,
                json={
                    "id": "prj_1",
                    "slug": "gitseed",
                    **(body if isinstance(body, dict) else {}),
                },
            )
        if path == "/api/v1/projects/gitseed/repositories" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "rep_1", "name": "gitseed-repo", "default_branch": "main"}],
            )
        if path == "/api/v1/policies/projects/gitseed" and method == "GET":
            return httpx.Response(
                200,
                json=[{"stage": "review", "require_approval": True, "required_approvals": 1}],
            )
        if path == "/api/v1/projects/gitseed/repositories/rep_1/branches":
            return httpx.Response(
                200,
                json=[{"name": "main", "sha": "abc1234", "is_default": True}],
            )
        if path == "/api/v1/projects/gitseed/repositories/rep_1/tree":
            return httpx.Response(
                200,
                json={
                    "ref": request.url.params.get("ref", "main"),
                    "path": request.url.params.get("path", ""),
                    "entries": [{"name": "README.md", "type": "blob"}],
                },
            )
        if path == "/api/v1/projects/gitseed/repositories/rep_1/blob":
            return httpx.Response(
                200,
                json={
                    "path": request.url.params.get("path"),
                    "content": "# GitSeed\n",
                    "encoding": "utf-8",
                },
            )

        if path == "/api/v1/seeds" and method == "GET":
            return httpx.Response(
                200,
                json=[
                    {
                        "id": "sed_1",
                        "number": 12,
                        "title": "Implement MCP Server",
                        "stage": "execute",
                        "status": "running",
                    }
                ],
            )
        if path == "/api/v1/seeds" and method == "POST":
            return httpx.Response(
                201,
                json={"id": "sed_2", "number": 13, **(body if isinstance(body, dict) else {})},
            )
        if path in ("/api/v1/seeds/sed_1", "/api/v1/seeds/gitseed#12") and method == "GET":
            return httpx.Response(
                200,
                json={
                    "id": "sed_1",
                    "number": 12,
                    "title": "Implement MCP Server",
                    "stage": "review",
                    "status": "awaiting_approval",
                },
            )
        if path == "/api/v1/seeds/sed_1" and method == "PATCH":
            return httpx.Response(
                200,
                json={"id": "sed_1", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/seeds/sed_1/start" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "sed_1", "stage": "ideate", "status": "queued"},
            )
        if path == "/api/v1/seeds/sed_1/advance" and method == "POST":
            target_stage = (
                body.get("stage", "merge") if isinstance(body, dict) else "merge"
            )
            return httpx.Response(
                200,
                json={"id": "sed_1", "stage": target_stage, "status": "merged"},
            )
        if path == "/api/v1/seeds/sed_1/move-back" and method == "POST":
            return httpx.Response(
                200,
                json={
                    "id": "sed_1",
                    "stage": body.get("stage", "plan") if isinstance(body, dict) else "plan",
                    "status": "queued",
                },
            )
        if path == "/api/v1/seeds/sed_1/stop" and method == "POST":
            return httpx.Response(200, json={"id": "sed_1", "status": "stopped"})
        if path == "/api/v1/seeds/sed_1/resume" and method == "POST":
            return httpx.Response(200, json={"id": "sed_1", "status": "queued"})
        if path == "/api/v1/seeds/sed_1/approve-checkpoint" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "sed_1", "status": "queued", "checkpoint_approved": True},
            )
        if path == "/api/v1/seeds/sed_1/close" and method == "POST":
            return httpx.Response(200, json={"id": "sed_1", "status": "closed"})
        if path == "/api/v1/seeds/sed_1/reopen" and method == "POST":
            return httpx.Response(200, json={"id": "sed_1", "status": "queued"})
        if path == "/api/v1/seeds/sed_1/changes" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "base_branch": "main",
                    "head_branch": "seed/12-implement-mcp",
                    "files_changed": 4,
                    "diff": "+class GitSeedMCPServer:",
                },
            )
        if path == "/api/v1/seeds/sed_1/receipt" and method == "GET":
            return httpx.Response(
                200,
                json={
                    "schema_version": "gitseed.receipt.v1",
                    "seed_id": "sed_1",
                    "signature": {"algorithm": "ed25519", "key_id": "kid_test"},
                },
            )
        if path == "/api/v1/seeds/sed_1/approvals" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "apr_1", "decision": "approved", "stage": "review"}],
            )
        if path == "/api/v1/seeds/sed_1/approvals" and method == "POST":
            return httpx.Response(
                201,
                json={"id": "apr_2", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/seeds/sed_1/tasks" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "tsk_1", "title": "Build protocol handler", "status": "completed"}],
            )
        if path == "/api/v1/seeds/sed_1/tasks" and method == "POST":
            return httpx.Response(
                201,
                json={"id": "tsk_2", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/seeds/sed_1/tasks/tsk_1" and method == "PATCH":
            return httpx.Response(
                200,
                json={"id": "tsk_1", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/seeds/sed_1/tasks/tsk_1" and method == "DELETE":
            return httpx.Response(204)
        if path == "/api/v1/seeds/sed_1/comments" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "cmt_1", "body": "Looks good"}],
            )
        if path == "/api/v1/seeds/sed_1/comments" and method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "cmt_2",
                    "body": body.get("body") if isinstance(body, dict) else "",
                    "steered_run_id": "run_1",
                },
            )
        if path == "/api/v1/seeds/sed_1/comments/cmt_1" and method == "PATCH":
            return httpx.Response(
                200,
                json={"id": "cmt_1", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/seeds/sed_1/comments/cmt_1" and method == "DELETE":
            return httpx.Response(204)

        if path == "/api/v1/runs" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "run_1", "seed_id": "sed_1", "status": "running"}],
            )
        if path == "/api/v1/runs" and method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "run_2",
                    "status": "queued",
                    **(body if isinstance(body, dict) else {}),
                },
            )
        if path == "/api/v1/runs/run_1" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "run_1", "seed_id": "sed_1", "status": "running", "cost": 0.04},
            )
        if path == "/api/v1/runs/run_1/cancel" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "run_1", "status": "cancelled"},
            )
        if path == "/api/v1/runs/run_1/logs" and method == "GET":
            return httpx.Response(
                200,
                json={"run_id": "run_1", "status": "running", "log_tail": "Step 1 complete"},
            )

        if path == "/api/v1/agents" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "agt_coder", "name": "Coder", "kind": "coder"}],
            )
        if path == "/api/v1/agents/benchmarks" and method == "GET":
            return httpx.Response(
                200,
                json={"entries": [{"agent_id": "agt_coder", "agent_handle": "coder", "run_count": 5}]},
            )
        if path == "/api/v1/agents/agt_coder" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "agt_coder", "name": "Coder", "kind": "coder", "enabled": True},
            )
        if path == "/api/v1/agents/coordinator/chat" and method == "POST":
            return httpx.Response(
                200,
                json={
                    "reply": "Seed #12 is ready for review.",
                    "context": body,
                },
            )

        if path == "/api/v1/skills" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "skl_1", "slug": "pytest", "name": "Pytest"}],
            )
        if path == "/api/v1/skills/skl_1" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "skl_1", "slug": "pytest", "name": "Pytest", "content": "Use pytest"},
            )
        if path == "/api/v1/policies" and method == "GET":
            return httpx.Response(
                200,
                json=[{"stage": "review", "requires_approval": True}],
            )
        if path == "/api/v1/policies/review" and method == "PATCH":
            return httpx.Response(
                200,
                json={"stage": "review", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/policies/projects/gitseed/review" and method == "PATCH":
            return httpx.Response(
                200,
                json={"stage": "review", "project_id": "prj_1", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/policies/projects/gitseed/review" and method == "DELETE":
            return httpx.Response(204)
        if path == "/api/v1/backends" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "bnd_1", "name": "LiteLLM", "type": "inference"}],
            )
        if path == "/api/v1/backends/bnd_1" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "bnd_1", "name": "LiteLLM", "type": "inference", "enabled": True},
            )
        if path == "/api/v1/sessions" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "asn_1", "kind": "chat", "status": "idle", "project_id": "prj_1"}],
            )
        if path == "/api/v1/projects/gitseed/agent-sessions" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "asn_1", "project_id": "prj_1", "status": "idle"}],
            )
        if path == "/api/v1/projects/gitseed/agent-sessions" and method == "POST":
            return httpx.Response(
                201,
                json={"id": "asn_2", "project_id": "prj_1", "status": "running", **(body if isinstance(body, dict) else {})},
            )
        if path == "/api/v1/projects/gitseed/agent-sessions/asn_1" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "asn_1", "project_id": "prj_1", "status": "idle", "messages": []},
            )
        if path == "/api/v1/projects/gitseed/agent-sessions/asn_1/messages" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "asn_1", "project_id": "prj_1", "status": "running"},
            )
        if path == "/api/v1/projects/gitseed/agent-sessions/asn_1/stop" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "asn_1", "project_id": "prj_1", "status": "stopped"},
            )
        if path == "/api/v1/projects/gitseed/agent-sessions/asn_1" and method == "DELETE":
            return httpx.Response(204)

        if path == "/api/v1/activity" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "act_1", "type": "seed.created", "seed_id": "sed_1"}],
            )

        if path == "/api/v1/projects/gitseed/sandboxes/config" and method == "GET":
            return httpx.Response(
                200,
                json={"default_image": "python:3.12-slim", "default_command": "pytest"},
            )
        if path == "/api/v1/projects/gitseed/sandboxes" and method == "GET":
            return httpx.Response(
                200,
                json=[{"id": "sbx_1", "status": "running"}],
            )
        if path == "/api/v1/projects/gitseed/sandboxes" and method == "POST":
            return httpx.Response(
                201,
                json={
                    "id": "sbx_2",
                    "status": "running",
                    **(body if isinstance(body, dict) else {}),
                },
            )
        if path == "/api/v1/projects/gitseed/sandboxes/sbx_1" and method == "GET":
            return httpx.Response(
                200,
                json={"id": "sbx_1", "status": "completed", "exit_code": 0, "stdout": "1 passed"},
            )
        if path == "/api/v1/projects/gitseed/sandboxes/sbx_1/input" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "sbx_1", "accepted": True},
            )
        if path == "/api/v1/projects/gitseed/sandboxes/sbx_1/stop" and method == "POST":
            return httpx.Response(
                200,
                json={"id": "sbx_1", "status": "stopped"},
            )

        return httpx.Response(404, json={"detail": f"Route not found: {method} {path}"})

    return httpx.MockTransport(handler)


def test_settings_url_and_token_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GITSEED_API_URL", raising=False)
    monkeypatch.delenv("RUNNER_API_URL", raising=False)
    monkeypatch.delenv("GITSEED_API_TOKEN", raising=False)
    monkeypatch.delenv("GITSEED_TOKEN", raising=False)

    s1 = MCPSettings(
        API_PORT=8420,
        RUNNER_API_URL="http://api:8420/",
        GITSEED_API_TOKEN="gsp_env_default",
    )
    assert s1.api_base_url == "http://api:8420"
    assert s1.resolve_token() == "gsp_env_default"
    assert s1.resolve_token("gsp_override") == "gsp_override"

    s2 = MCPSettings(
        GITSEED_API_URL="https://gitseed.example.com/",
        RUNNER_API_URL="http://api:8420",
    )
    assert s2.api_base_url == "https://gitseed.example.com"


@pytest.mark.asyncio
async def test_mcp_protocol_initialize_and_catalogs() -> None:
    recorded: list[dict[str, Any]] = []
    server = GitSeedMCPServer(
        settings=MCPSettings(GITSEED_VERSION="0.1.0-test"),
        http_transport=_make_mock_api_transport(recorded),
    )

    init_resp = await server.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "claude-code", "version": "1.0.0"},
            },
        }
    )
    assert isinstance(init_resp, dict)
    assert init_resp["id"] == 1
    assert init_resp["result"]["protocolVersion"] == "2024-11-05"
    assert init_resp["result"]["serverInfo"]["name"] == "gitseed-mcp"

    # Notification produces no response
    notif_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "method": "notifications/initialized"}
    )
    assert notif_resp is None

    # Ping
    ping_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "id": 2, "method": "ping"}
    )
    assert ping_resp == {"jsonrpc": "2.0", "id": 2, "result": {}}

    # Tools catalog
    tools_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}
    )
    assert isinstance(tools_resp, dict)
    assert len(tools_resp["result"]["tools"]) == len(TOOLS_CATALOG)

    # Resources & templates catalog
    res_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "id": 4, "method": "resources/list"}
    )
    assert isinstance(res_resp, dict)
    assert len(res_resp["result"]["resources"]) == len(RESOURCES_CATALOG)

    tmpl_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "id": 5, "method": "resources/templates/list"}
    )
    assert isinstance(tmpl_resp, dict)
    assert len(tmpl_resp["result"]["resourceTemplates"]) == len(
        RESOURCE_TEMPLATES_CATALOG
    )

    # Prompts catalog
    prompts_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "id": 6, "method": "prompts/list"}
    )
    assert isinstance(prompts_resp, dict)
    assert len(prompts_resp["result"]["prompts"]) == len(PROMPTS_CATALOG)


@pytest.mark.asyncio
async def test_mcp_all_sdlc_tools_execution() -> None:
    recorded: list[dict[str, Any]] = []
    server = GitSeedMCPServer(
        settings=MCPSettings(
            GITSEED_API_URL="http://api:8420",
            GITSEED_API_TOKEN="gsp_valid_token",
        ),
        http_transport=_make_mock_api_transport(recorded),
    )

    async def call_tool(name: str, arguments: dict[str, Any] | None = None) -> Any:
        resp = await server.handle_payload(
            {
                "jsonrpc": "2.0",
                "id": f"call-{name}",
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments or {}},
            }
        )
        assert isinstance(resp, dict)
        result = resp["result"]
        assert result["isError"] is False, f"Tool {name} failed: {result}"
        return json.loads(result["content"][0]["text"])

    # System status
    status = await call_tool("get_system_status")
    assert status["authenticated"] is True
    assert status["user"]["email"] == "admin@gitseed.local"

    # Projects & Repositories
    projects = await call_tool("list_projects")
    assert projects[0]["slug"] == "gitseed"

    proj_detail = await call_tool(
        "get_project",
        {"project_id": "gitseed", "include_repositories": True, "include_policies": True},
    )
    assert proj_detail["project"]["id"] == "prj_1"
    assert len(proj_detail["repositories"]) == 1
    assert len(proj_detail["policies"]) == 1

    created_proj = await call_tool("create_project", {"name": "New Project"})
    assert created_proj["id"] == "prj_new"

    updated_proj = await call_tool(
        "update_project", {"project_id": "gitseed", "description": "Updated"}
    )
    assert updated_proj["description"] == "Updated"

    branches = await call_tool(
        "list_repository_branches", {"project_id": "gitseed", "repo_id": "rep_1"}
    )
    assert branches[0]["name"] == "main"

    tree = await call_tool(
        "get_repository_tree",
        {"project_id": "gitseed", "repo_id": "rep_1", "ref": "main"},
    )
    assert tree["entries"][0]["name"] == "README.md"

    blob = await call_tool(
        "get_repository_blob",
        {"project_id": "gitseed", "repo_id": "rep_1", "path": "README.md"},
    )
    assert "GitSeed" in blob["content"]

    # Seeds (including slug#number encoding)
    seeds = await call_tool("list_seeds", {"project_id": "gitseed", "open_only": True})
    assert seeds[0]["id"] == "sed_1"

    seed_by_ref = await call_tool("get_seed", {"seed_id": "gitseed#12"})
    assert seed_by_ref["id"] == "sed_1"
    assert any(r["raw_path"] == "/api/v1/seeds/gitseed%2312" for r in recorded)

    new_seed = await call_tool(
        "create_seed",
        {
            "project_id": "gitseed",
            "title": "Add SSE keepalive",
            "acceptance_criteria": ["Streams SSE endpoint event"],
        },
    )
    assert new_seed["id"] == "sed_2"

    patched_seed = await call_tool(
        "update_seed", {"seed_id": "sed_1", "autonomy": "autonomous"}
    )
    assert patched_seed["autonomy"] == "autonomous"

    started = await call_tool("start_seed", {"seed_id": "sed_1"})
    assert started["status"] == "queued"

    advanced = await call_tool(
        "advance_seed", {"seed_id": "sed_1", "stage": "merge"}
    )
    assert advanced["stage"] == "merge"

    moved_back = await call_tool(
        "move_seed_back",
        {"seed_id": "sed_1", "stage": "plan", "reason": "Revise task breakdown"},
    )
    assert moved_back["stage"] == "plan"

    stopped = await call_tool("stop_seed", {"seed_id": "sed_1"})
    assert stopped["status"] == "stopped"

    resumed = await call_tool("resume_seed", {"seed_id": "sed_1"})
    assert resumed["status"] == "queued"

    checkpoint = await call_tool(
        "approve_seed_checkpoint", {"seed_id": "sed_1", "approved_through": 10.0}
    )
    assert checkpoint["checkpoint_approved"] is True

    closed = await call_tool("close_seed", {"seed_id": "sed_1"})
    assert closed["status"] == "closed"

    reopened = await call_tool("reopen_seed", {"seed_id": "sed_1"})
    assert reopened["status"] == "queued"

    changes = await call_tool("get_seed_changes", {"seed_id": "sed_1"})
    assert changes["files_changed"] == 4

    receipt = await call_tool("get_seed_receipt", {"seed_id": "sed_1"})
    assert receipt["schema_version"] == "gitseed.receipt.v1"
    assert receipt["seed_id"] == "sed_1"

    # Stage Gate Approvals
    approvals = await call_tool("list_seed_approvals", {"seed_id": "sed_1"})
    assert approvals[0]["decision"] == "approved"

    submitted = await call_tool(
        "submit_seed_approval",
        {"seed_id": "sed_1", "decision": "approved", "comment": "Ship it!"},
    )
    assert submitted["decision"] == "approved"

    # Seed Tasks
    tasks = await call_tool("list_seed_tasks", {"seed_id": "sed_1"})
    assert len(tasks) == 1

    new_task = await call_tool(
        "create_seed_task", {"seed_id": "sed_1", "title": "Write unit tests"}
    )
    assert new_task["id"] == "tsk_2"

    updated_task = await call_tool(
        "update_seed_task",
        {"seed_id": "sed_1", "task_id": "tsk_1", "status": "done"},
    )
    assert updated_task["status"] == "done"

    deleted_task = await call_tool(
        "delete_seed_task", {"seed_id": "sed_1", "task_id": "tsk_1"}
    )
    assert deleted_task["ok"] is True

    # Seed Comments & @agent steering
    comments = await call_tool("list_seed_comments", {"seed_id": "sed_1"})
    assert len(comments) == 1

    steered = await call_tool(
        "create_seed_comment",
        {"seed_id": "sed_1", "body": "@agent please add strict type annotations"},
    )
    assert steered["steered_run_id"] == "run_1"

    updated_cmt = await call_tool(
        "update_seed_comment",
        {"seed_id": "sed_1", "comment_id": "cmt_1", "body": "Edited comment"},
    )
    assert updated_cmt["body"] == "Edited comment"

    deleted_cmt = await call_tool(
        "delete_seed_comment", {"seed_id": "sed_1", "comment_id": "cmt_1"}
    )
    assert deleted_cmt["ok"] is True

    # Runs
    runs = await call_tool("list_runs", {"seed_id": "sed_1", "active_only": True})
    assert runs[0]["id"] == "run_1"

    run_detail = await call_tool("get_run", {"run_id": "run_1"})
    assert run_detail["cost"] == 0.04

    created_run = await call_tool("create_run", {"seed_id": "sed_1", "stage": "execute"})
    assert created_run["id"] == "run_2"

    cancelled_run = await call_tool("cancel_run", {"run_id": "run_1"})
    assert cancelled_run["status"] == "cancelled"

    run_logs = await call_tool("get_run_logs", {"run_id": "run_1", "tail": 200})
    assert "Step 1 complete" in run_logs["log_tail"]

    # Agents, Skills, Policies, Backends & @coordinator
    agents = await call_tool("list_agents", {"kind": "stage"})
    assert agents[0]["id"] == "agt_coder"

    agent_detail = await call_tool("get_agent", {"agent_id": "agt_coder"})
    assert agent_detail["enabled"] is True

    benchmarks = await call_tool("get_agent_benchmarks", {"project_id": "gitseed"})
    assert benchmarks["entries"][0]["agent_handle"] == "coder"

    skills = await call_tool("list_skills")
    assert skills[0]["slug"] == "pytest"

    skill_detail = await call_tool("get_skill", {"skill_id": "skl_1"})
    assert skill_detail["content"] == "Use pytest"

    org_policies = await call_tool("list_org_policies")
    assert org_policies[0]["stage"] == "review"

    updated_org_pol = await call_tool(
        "update_org_policy", {"stage": "review", "max_attempts": 4}
    )
    assert updated_org_pol["max_attempts"] == 4

    updated_proj_pol = await call_tool(
        "update_project_policy",
        {"project_id": "gitseed", "stage": "review", "requires_approval": True},
    )
    assert updated_proj_pol["requires_approval"] is True

    reset_pol = await call_tool(
        "reset_project_policy", {"project_id": "gitseed", "stage": "review"}
    )
    assert reset_pol["ok"] is True

    backends = await call_tool("list_backends", {"type": "inference"})
    assert backends[0]["id"] == "bnd_1"

    backend_detail = await call_tool("get_backend", {"backend_id": "bnd_1"})
    assert backend_detail["enabled"] is True

    coord = await call_tool(
        "ask_coordinator",
        {
            "prompt": "What is the status of seed #12?",
            "project_id": "gitseed",
            "mode": "brainstorm",
            "propose_seeds": True,
        },
    )
    assert "Seed #12" in coord["reply"]

    # Interactive Sessions & Agent Sessions
    sessions = await call_tool("list_sessions", {"project_id": "gitseed"})
    assert sessions[0]["id"] == "asn_1"

    agent_sessions = await call_tool("list_agent_sessions", {"project_id": "gitseed"})
    assert agent_sessions[0]["id"] == "asn_1"

    agent_session_detail = await call_tool(
        "get_agent_session", {"project_id": "gitseed", "session_id": "asn_1"}
    )
    assert agent_session_detail["id"] == "asn_1"

    created_asn = await call_tool(
        "create_agent_session",
        {
            "project_id": "gitseed",
            "agent_id": "agt_coder",
            "repository_id": "1",
            "initial_prompt": "Fix flaky test",
        },
    )
    assert created_asn["id"] == "asn_2"
    assert created_asn["repository_id"] == 1

    sent_asn = await call_tool(
        "send_agent_session_message",
        {
            "project_id": "gitseed",
            "session_id": "asn_1",
            "content": "Also check edge cases",
        },
    )
    assert sent_asn["status"] == "running"

    stopped_asn = await call_tool(
        "stop_agent_session", {"project_id": "gitseed", "session_id": "asn_1"}
    )
    assert stopped_asn["status"] == "stopped"

    deleted_asn = await call_tool(
        "delete_agent_session", {"project_id": "gitseed", "session_id": "asn_1"}
    )
    assert deleted_asn["ok"] is True

    # Activity
    activity = await call_tool("list_activity", {"project_id": "gitseed"})
    assert activity[0]["type"] == "seed.created"

    # Sandboxes
    sbx_cfg = await call_tool("get_sandbox_config", {"project_id": "gitseed"})
    assert sbx_cfg["default_image"] == "python:3.12-slim"

    sbx_list = await call_tool("list_sandboxes", {"project_id": "gitseed"})
    assert sbx_list[0]["id"] == "sbx_1"

    sbx_created = await call_tool(
        "create_sandbox",
        {"project_id": "gitseed", "seed_id": "sed_1", "command": "pytest"},
    )
    assert sbx_created["id"] == "sbx_2"

    sbx_detail = await call_tool(
        "get_sandbox", {"project_id": "gitseed", "sandbox_id": "sbx_1"}
    )
    assert sbx_detail["exit_code"] == 0

    sbx_input = await call_tool(
        "send_sandbox_input",
        {"project_id": "gitseed", "sandbox_id": "sbx_1", "input": "y", "eof": True},
    )
    assert sbx_input["accepted"] is True

    sbx_stopped = await call_tool(
        "stop_sandbox", {"project_id": "gitseed", "sandbox_id": "sbx_1"}
    )
    assert sbx_stopped["status"] == "stopped"


@pytest.mark.asyncio
async def test_mcp_resources_and_prompts() -> None:
    recorded: list[dict[str, Any]] = []
    server = GitSeedMCPServer(
        settings=MCPSettings(
            GITSEED_API_URL="http://api:8420",
            GITSEED_API_TOKEN="gsp_valid_token",
        ),
        http_transport=_make_mock_api_transport(recorded),
    )

    for uri in (
        "gitseed://status",
        "gitseed://projects",
        "gitseed://projects/gitseed",
        "gitseed://seeds",
        "gitseed://seeds/sed_1",
        "gitseed://runs/active",
        "gitseed://activity",
    ):
        resp = await server.handle_payload(
            {
                "jsonrpc": "2.0",
                "id": f"res-{uri}",
                "method": "resources/read",
                "params": {"uri": uri},
            }
        )
        assert isinstance(resp, dict)
        contents = resp["result"]["contents"]
        assert len(contents) == 1
        assert contents[0]["uri"] == uri
        parsed = json.loads(contents[0]["text"])
        assert "error" not in parsed

    for prompt_name, prompt_args in (
        ("triage-workspace", {"project_id": "gitseed"}),
        ("review-seed", {"seed_id": "gitseed#12"}),
        ("plant-seed", {"project_id": "gitseed", "goal": "Support MCP Server"}),
        ("steer-run", {"seed_id": "sed_1", "guidance": "Focus on tests"}),
    ):
        resp = await server.handle_payload(
            {
                "jsonrpc": "2.0",
                "id": f"prompt-{prompt_name}",
                "method": "prompts/get",
                "params": {"name": prompt_name, "arguments": prompt_args},
            }
        )
        assert isinstance(resp, dict)
        messages = resp["result"]["messages"]
        assert len(messages) == 1
        assert messages[0]["role"] == "user"


@pytest.mark.asyncio
async def test_http_and_sse_transports() -> None:
    recorded: list[dict[str, Any]] = []
    settings = MCPSettings(
        GITSEED_VERSION="0.1.0-test",
        GITSEED_API_URL="http://api:8420",
    )
    app = create_app(
        settings=settings,
        http_transport=_make_mock_api_transport(recorded),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp.test",
    ) as client:
        # Discovery root
        root_resp = await client.get("/")
        assert root_resp.status_code == 200
        assert root_resp.json()["name"] == "gitseed-mcp"

        # Health check with upstream probe
        health_resp = await client.get("/healthz?probe_upstream=true")
        assert health_resp.status_code == 200
        assert health_resp.json()["upstream_status"] == "ok"

        # Streamable HTTP POST /mcp with per-request Bearer token
        mcp_resp = await client.post(
            "/mcp",
            headers={"Authorization": "Bearer gsp_valid_token"},
            json={
                "jsonrpc": "2.0",
                "id": 10,
                "method": "tools/call",
                "params": {"name": "get_system_status", "arguments": {}},
            },
        )
        assert mcp_resp.status_code == 200
        session_id = mcp_resp.headers.get("mcp-session-id")
        assert session_id
        payload = json.loads(mcp_resp.json()["result"]["content"][0]["text"])
        assert payload["authenticated"] is True

        # Streamable HTTP POST /mcp with SSE response preference
        sse_post_resp = await client.post(
            "/mcp",
            headers={
                "Mcp-Session-Id": session_id,
                "Authorization": "Bearer gsp_valid_token",
                "Accept": "text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 11, "method": "ping"},
        )
        assert sse_post_resp.status_code == 200
        assert "event: message" in sse_post_resp.text

        # Unauthenticated caller passing victim's Mcp-Session-Id is rejected with 401
        unauth_hijack_resp = await client.post(
            "/mcp",
            headers={"Mcp-Session-Id": session_id},
            json={
                "jsonrpc": "2.0",
                "id": 12,
                "method": "tools/call",
                "params": {"name": "get_system_status", "arguments": {}},
            },
        )
        assert unauth_hijack_resp.status_code == 401

        # GET /mcp with Accept: text/event-stream stores queue in streamable_sessions
        mcp_sse_resp = await client.get(
            "/mcp?once=1",
            headers={
                "Mcp-Session-Id": session_id,
                "Authorization": "Bearer gsp_valid_token",
                "Accept": "text/event-stream",
            },
        )
        assert mcp_sse_resp.status_code == 200
        assert f": session {session_id}" in mcp_sse_resp.text
        assert session_id in app.state.streamable_sessions

        # Delete Streamable HTTP session requires matching auth
        unauth_del_resp = await client.delete(
            "/mcp", headers={"Mcp-Session-Id": session_id}
        )
        assert unauth_del_resp.status_code == 401

        del_resp = await client.delete(
            "/mcp",
            headers={
                "Mcp-Session-Id": session_id,
                "Authorization": "Bearer gsp_valid_token",
            },
        )
        assert del_resp.status_code == 204

        # SSE endpoint handshake (GET /sse?once=1)
        sse_connect = await client.get(
            "/sse?once=1",
            headers={"X-API-Key": "gsp_valid_token"},
        )
        assert sse_connect.status_code == 200
        assert "event: endpoint" in sse_connect.text
        assert "/messages?session_id=" in sse_connect.text

        # Active SSE session queue integration via POST /messages
        manual_session = SSESession(session_id="sess_test_1", token="gsp_valid_token")
        app.state.sse_sessions["sess_test_1"] = manual_session

        unauth_msg_resp = await client.post(
            "/messages?session_id=sess_test_1",
            json={
                "jsonrpc": "2.0",
                "id": 98,
                "method": "tools/call",
                "params": {"name": "get_system_status", "arguments": {}},
            },
        )
        assert unauth_msg_resp.status_code == 401

        msg_resp = await client.post(
            "/messages?session_id=sess_test_1",
            headers={"Authorization": "Bearer gsp_valid_token"},
            json={
                "jsonrpc": "2.0",
                "id": 99,
                "method": "tools/call",
                "params": {"name": "get_system_status", "arguments": {}},
            },
        )
        assert msg_resp.status_code == 202
        queued_msg = await manual_session.queue.get()
        assert queued_msg is not None
        queued_json = json.loads(queued_msg)
        assert queued_json["id"] == 99
        queued_status = json.loads(queued_json["result"]["content"][0]["text"])
        assert queued_status["authenticated"] is True


@pytest.mark.asyncio
async def test_stdio_transport_loop() -> None:
    recorded: list[dict[str, Any]] = []
    server = GitSeedMCPServer(
        settings=MCPSettings(
            GITSEED_API_URL="http://api:8420",
            GITSEED_API_TOKEN="gsp_valid_token",
        ),
        http_transport=_make_mock_api_transport(recorded),
    )

    input_lines = (
        json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping"})
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"})
        + "\n"
        + json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "list_projects", "arguments": {}},
            }
        )
        + "\n"
    )
    in_stream = io.StringIO(input_lines)
    out_stream = io.StringIO()

    await run_stdio_async(server, input_stream=in_stream, output_stream=out_stream)

    output_lines = [
        line for line in out_stream.getvalue().splitlines() if line.strip()
    ]
    assert len(output_lines) == 2
    first = json.loads(output_lines[0])
    second = json.loads(output_lines[1])
    assert first == {"jsonrpc": "2.0", "id": 1, "result": {}}
    assert second["id"] == 2
    assert second["result"]["isError"] is False


def test_runner_adapters_mcp_config_injection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    ctx: dict[str, Any] = {
        "runtime": "opencode",
        "model": "gpt-4.1",
        "gateway": {
            "base_url": "http://litellm:4000/v1",
            "anthropic_base_url": "http://litellm:4000",
            "api_key": "sk-gsr-test",
            "model": "gpt-4.1",
            "provider": "openai",
        },
        "mcp_servers": [
            {
                "id": "bnd_mcp1",
                "name": "GitSeed MCP",
                "slug": "gitseed-mcp",
                "provider": "mcp-sse",
                "endpoint": "http://mcp:8422/sse",
                "headers": {"Authorization": "Bearer gsp_valid_token"},
                "env": {},
            },
            {
                "id": "bnd_mcp2",
                "name": "Local Stdio MCP",
                "slug": "local-stdio",
                "provider": "mcp-stdio",
                "endpoint": "gitseed-mcp --stdio",
                "headers": {},
                "env": {"MCP_ENV_SECRET": "sec_123"},
            },
        ],
    }

    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    out_file = work_dir / "output.json"

    opencode_adapter = create_runtime_adapter(
        ctx=ctx, working_dir=work_dir, output_file=out_file
    )
    opencode_adapter.prepare_workspace()
    opencode_cfg = json.loads((work_dir / "opencode.json").read_text(encoding="utf-8"))
    assert "gitseed-mcp" in opencode_cfg["mcp"]
    assert opencode_cfg["mcp"]["gitseed-mcp"]["url"] == "http://mcp:8422/sse"
    assert opencode_cfg["mcp"]["local-stdio"]["command"] == ["gitseed-mcp", "--stdio"]

    claude_adapter = create_runtime_adapter(
        ctx={**ctx, "runtime": "claude-code"},
        working_dir=work_dir,
        output_file=out_file,
    )
    claude_adapter.prepare_workspace()
    claude_cfg = json.loads((tmp_path / ".claude.json").read_text(encoding="utf-8"))
    assert claude_cfg["mcpServers"]["gitseed-mcp"]["url"] == "http://mcp:8422/sse"
    assert claude_cfg["mcpServers"]["local-stdio"]["command"] == "gitseed-mcp"
    assert claude_cfg["mcpServers"]["local-stdio"]["args"] == ["--stdio"]


@pytest.mark.asyncio
async def test_mcp_tool_annotations_and_readme_sync() -> None:
    assert TOOLS is TOOLS_CATALOG
    assert len(TOOLS) == 65
    assert set(TOOLSET_BY_TOOL) == {t["name"] for t in TOOLS}
    assert sum(len(members) for members in TOOLSETS.values()) == 65

    expected_destructive = {
        "update_org_policy",
        "reset_project_policy",
        "delete_seed_task",
        "delete_seed_comment",
        "delete_agent_session",
        "close_seed",
        "stop_seed",
        "cancel_run",
        "stop_sandbox",
        "stop_agent_session",
    }

    for tool in TOOLS:
        name = tool["name"]
        annotations = tool.get("annotations")
        assert isinstance(annotations, dict), f"Missing annotations on {name}"
        assert isinstance(annotations.get("title"), str) and annotations["title"].strip()
        for hint_key in (
            "readOnlyHint",
            "destructiveHint",
            "idempotentHint",
            "openWorldHint",
        ):
            assert isinstance(annotations.get(hint_key), bool), (
                f"{name} annotation {hint_key} must be a bool"
            )

        if name.startswith(("get_", "list_")):
            assert annotations["readOnlyHint"] is True, f"{name} should be readOnlyHint=True"
            assert annotations["destructiveHint"] is False, (
                f"{name} should be destructiveHint=False"
            )
            assert annotations["idempotentHint"] is True, (
                f"{name} should be idempotentHint=True"
            )
        elif name in expected_destructive:
            assert annotations["readOnlyHint"] is False, (
                f"{name} should be readOnlyHint=False"
            )
            assert annotations["destructiveHint"] is True, (
                f"{name} should be destructiveHint=True"
            )
        else:
            assert annotations["readOnlyHint"] is False, (
                f"{name} should be readOnlyHint=False"
            )
            assert annotations["destructiveHint"] is False, (
                f"{name} should be destructiveHint=False"
            )

    # Verify tools/list JSON-RPC response includes annotations on every tool descriptor
    recorded: list[dict[str, Any]] = []
    server = GitSeedMCPServer(
        settings=MCPSettings(GITSEED_VERSION="0.1.0-test"),
        http_transport=_make_mock_api_transport(recorded),
    )
    tools_resp = await server.handle_payload(
        {"jsonrpc": "2.0", "id": "tools-list-annotations", "method": "tools/list"}
    )
    assert isinstance(tools_resp, dict)
    listed_tools = tools_resp["result"]["tools"]
    assert len(listed_tools) == 65
    for descriptor in listed_tools:
        assert "annotations" in descriptor
        assert isinstance(descriptor["annotations"]["readOnlyHint"], bool)
        assert isinstance(descriptor["annotations"]["destructiveHint"], bool)

    # Verify README.md has zero phantom or undocumented tools (1:1 match with TOOLS)
    readme_path = Path(__file__).resolve().parents[1] / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")
    catalog_section_match = re.search(
        r"## Exposed Tool Catalog.*?\n(.*?)(?:\n---|\Z)",
        readme_text,
        flags=re.DOTALL,
    )
    assert catalog_section_match is not None, "Missing Exposed Tool Catalog section in README.md"
    readme_tool_names = re.findall(r"`([a-z][a-z0-9_]+)`", catalog_section_match.group(1))
    catalog_tool_names = [t["name"] for t in TOOLS]

    phantom_tools = set(readme_tool_names) - set(catalog_tool_names)
    undocumented_tools = set(catalog_tool_names) - set(readme_tool_names)
    assert not phantom_tools, f"Phantom tools in apps/mcp/README.md: {sorted(phantom_tools)}"
    assert not undocumented_tools, (
        f"Undocumented tools missing from apps/mcp/README.md: {sorted(undocumented_tools)}"
    )
    assert len(readme_tool_names) == len(set(readme_tool_names)) == 65


@pytest.mark.asyncio
async def test_http_security_no_static_token_fallback_and_csrf_guards(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GITSEED_TOKEN", "gsp_valid_token")
    recorded: list[dict[str, Any]] = []
    settings = MCPConfig(
        GITSEED_VERSION="0.1.0-test",
        GITSEED_API_URL="http://api:8420",
        GITSEED_TOKEN="gsp_valid_token",
        token="gsp_valid_token",
    )
    assert settings.resolve_token(None, allow_env_fallback=True) == "gsp_valid_token"
    assert settings.resolve_token(None, allow_env_fallback=False) is None

    app = create_app(
        settings=settings,
        http_transport=_make_mock_api_transport(recorded),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp.test",
    ) as client:
        # 1. Missing Authorization MUST return 401 even when MCPConfig.token / GITSEED_TOKEN is set
        unauth_post = await client.post(
            "/mcp",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert unauth_post.status_code == 401

        unauth_sse = await client.get("/sse?once=1")
        assert unauth_sse.status_code == 401

        unauth_messages = await client.post(
            "/messages",
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        assert unauth_messages.status_code == 401

        # 2. CORS simple request Content-Type: text/plain MUST be rejected with 415
        text_plain_resp = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer gsp_valid_token",
                "Content-Type": "text/plain",
            },
            content='{"jsonrpc":"2.0","id":3,"method":"ping"}',
        )
        assert text_plain_resp.status_code == 415

        # 3. Untrusted browser Origin MUST be rejected with 403
        evil_origin_post = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer gsp_valid_token",
                "Origin": "https://evil.example",
            },
            json={"jsonrpc": "2.0", "id": 4, "method": "ping"},
        )
        assert evil_origin_post.status_code == 403

        evil_origin_sse = await client.get(
            "/sse?once=1",
            headers={
                "Authorization": "Bearer gsp_valid_token",
                "Origin": "https://evil.example",
            },
        )
        assert evil_origin_sse.status_code == 403

        # 4. Trusted localhost web UI Origin with valid Bearer token succeeds
        trusted_origin_post = await client.post(
            "/mcp",
            headers={
                "Authorization": "Bearer gsp_valid_token",
                "Origin": "http://localhost:8421",
            },
            json={"jsonrpc": "2.0", "id": 5, "method": "ping"},
        )
        assert trusted_origin_post.status_code == 200
        assert trusted_origin_post.json()["result"] == {}


def test_cli_default_loopback_host_and_list_seeds_limit_schema(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import gitseed_mcp.main as mcp_main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("HOST", raising=False)
    parsed = build_arg_parser().parse_args([])
    assert parsed.host is None
    assert MCPSettings().HOST == "127.0.0.1"

    uvicorn_calls: list[dict[str, Any]] = []

    def fake_uvicorn_run(app_obj: Any, **kwargs: Any) -> None:
        uvicorn_calls.append({"app": app_obj, **kwargs})

    monkeypatch.setattr(mcp_main.uvicorn, "run", fake_uvicorn_run)

    # Default when HOST env is unset -> 127.0.0.1
    mcp_main.cli([])
    assert uvicorn_calls[-1]["host"] == "127.0.0.1"

    # HOST env var is respected when --host is omitted
    monkeypatch.setenv("HOST", "0.0.0.0")
    mcp_main.cli([])
    assert uvicorn_calls[-1]["host"] == "0.0.0.0"

    # Explicit --host flag overrides HOST env var
    mcp_main.cli(["--host", "127.0.0.1"])
    assert uvicorn_calls[-1]["host"] == "127.0.0.1"

    list_seeds_tool = next(tool for tool in TOOLS_CATALOG if tool["name"] == "list_seeds")
    limit_prop = list_seeds_tool["inputSchema"]["properties"]["limit"]
    assert "(1-1000)" in limit_prop["description"]

    approve_ckpt_tool = next(
        tool for tool in TOOLS_CATALOG if tool["name"] == "approve_seed_checkpoint"
    )
    assert approve_ckpt_tool["inputSchema"]["required"] == ["seed_id", "approved_through"]


@pytest.mark.asyncio
async def test_mcp_cors_query_token_rejection_and_sse_messages_session_guard() -> None:
    recorded: list[dict[str, Any]] = []
    settings = MCPSettings(
        GITSEED_VERSION="0.1.0-test",
        GITSEED_API_URL="http://api:8420",
        GITSEED_MCP_ALLOWED_ORIGINS="http://localhost:8421,http://127.0.0.1:8421",
    )
    app = create_app(
        settings=settings,
        http_transport=_make_mock_api_transport(recorded),
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mcp.test",
    ) as client:
        # 1. Query-string PATs (?token= / ?api_key=) MUST be ignored and rejected with 401
        query_tok_mcp = await client.post(
            "/mcp?token=gsp_valid_token",
            json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert query_tok_mcp.status_code == 401

        query_apikey_sse = await client.get("/sse?once=1&api_key=gsp_valid_token")
        assert query_apikey_sse.status_code == 401

        app.state.sse_sessions["sess_bound"] = SSESession(
            session_id="sess_bound",
            token="gsp_valid_token",
        )
        query_tok_msg = await client.post(
            "/messages?session_id=sess_bound&token=gsp_valid_token",
            json={"jsonrpc": "2.0", "id": 2, "method": "ping"},
        )
        assert query_tok_msg.status_code == 401

        # 2. CORS OPTIONS /mcp preflight and exposed Mcp-Session-Id header
        preflight_resp = await client.options(
            "/mcp",
            headers={
                "Origin": "http://localhost:8421",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "Authorization, Content-Type, Mcp-Session-Id",
            },
        )
        assert preflight_resp.status_code == 200
        assert (
            preflight_resp.headers.get("access-control-allow-origin")
            == "http://localhost:8421"
        )
        allow_methods = preflight_resp.headers.get("access-control-allow-methods", "")
        assert "POST" in allow_methods
        allow_headers = preflight_resp.headers.get(
            "access-control-allow-headers", ""
        ).lower()
        assert "authorization" in allow_headers
        assert "mcp-session-id" in allow_headers

        cors_post_resp = await client.post(
            "/mcp",
            headers={
                "Origin": "http://localhost:8421",
                "Authorization": "Bearer gsp_valid_token",
            },
            json={"jsonrpc": "2.0", "id": 3, "method": "ping"},
        )
        assert cors_post_resp.status_code == 200
        assert (
            cors_post_resp.headers.get("access-control-allow-origin")
            == "http://localhost:8421"
        )
        assert (
            cors_post_resp.headers.get("access-control-expose-headers")
            == "Mcp-Session-Id"
        )

        # 3. POST /messages?session_id=nonexistent returns 404 without invoking upstream API
        recorded.clear()
        missing_sess_resp = await client.post(
            "/messages?session_id=nonexistent",
            headers={"Authorization": "Bearer gsp_valid_token"},
            json={
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {"name": "list_projects", "arguments": {}},
            },
        )
        assert missing_sess_resp.status_code == 404
        assert missing_sess_resp.json() == {"error": "Unknown or expired SSE session"}
        assert recorded == []

        no_sess_param_resp = await client.post(
            "/messages",
            headers={"Authorization": "Bearer gsp_valid_token"},
            json={
                "jsonrpc": "2.0",
                "id": 5,
                "method": "tools/call",
                "params": {"name": "list_projects", "arguments": {}},
            },
        )
        assert no_sess_param_resp.status_code == 404
        assert recorded == []

        # Mismatched bound_token on SSE session is rejected with 401 without upstream calls
        hijack_sess_resp = await client.post(
            "/messages?session_id=sess_bound",
            headers={"Authorization": "Bearer gsp_other_token"},
            json={
                "jsonrpc": "2.0",
                "id": 6,
                "method": "tools/call",
                "params": {"name": "list_projects", "arguments": {}},
            },
        )
        assert hijack_sess_resp.status_code == 401
        assert recorded == []


def test_streamable_http_post_does_not_duplicate_response_into_sse_queue() -> None:
    """POST /mcp returns JSONResponse directly without pushing a duplicate onto GET /mcp SSE queue."""
    cfg = MCPSettings(
        GITSEED_API_URL="http://localhost:8420",
        GITSEED_TOKEN="gsp_env_token",
    )
    app = create_app(settings=cfg, http_transport=_make_mock_api_transport([]))

    queue: asyncio.Queue[str | None] = asyncio.Queue()
    session_id = "sess_no_dup_1"
    app.state.streamable_sessions[session_id] = {
        "token": "gsp_valid_token",
        "queue": queue,
    }

    raw_body = json.dumps({"jsonrpc": "2.0", "id": 77, "method": "ping"}).encode("utf-8")
    sent_messages: list[dict[str, Any]] = []
    body_consumed = False

    async def receive() -> dict[str, Any]:
        nonlocal body_consumed
        if not body_consumed:
            body_consumed = True
            return {"type": "http.request", "body": raw_body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/mcp",
        "raw_path": b"/mcp",
        "query_string": b"",
        "headers": [
            (b"authorization", b"Bearer gsp_valid_token"),
            (b"content-type", b"application/json"),
            (b"accept", b"application/json, text/event-stream"),
            (b"mcp-session-id", session_id.encode("utf-8")),
        ],
    }

    coro = app(scope, receive, send)
    try:
        while True:
            coro.send(None)
    except StopIteration:
        pass
    finally:
        coro.close()

    start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
    body_msg = next(m for m in sent_messages if m["type"] == "http.response.body")
    assert start_msg["status"] == 200
    parsed = json.loads(body_msg["body"].decode("utf-8"))
    assert parsed == {"jsonrpc": "2.0", "id": 77, "result": {}}
    assert queue.empty() is True


def _step_coro_sync(coro: Any) -> Any:
    """Drive a non-sleeping coroutine synchronously without creating a socketpair loop."""
    try:
        while True:
            coro.send(None)
    except StopIteration as stop:
        return stop.value
    finally:
        coro.close()


def _send_asgi_sync(
    app: Any,
    *,
    method: str,
    path: str,
    query_string: str = "",
    headers: list[tuple[bytes, bytes]] | None = None,
    body: bytes = b"",
) -> tuple[int, list[tuple[bytes, bytes]], bytes]:
    sent_messages: list[dict[str, Any]] = []
    body_consumed = False

    async def receive() -> dict[str, Any]:
        nonlocal body_consumed
        if not body_consumed:
            body_consumed = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict[str, Any]) -> None:
        sent_messages.append(message)

    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": method,
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("utf-8"),
        "query_string": query_string.encode("utf-8"),
        "headers": headers or [],
    }
    _step_coro_sync(app(scope, receive, send))
    start_msg = next(m for m in sent_messages if m["type"] == "http.response.start")
    resp_body = b"".join(
        m.get("body", b"") for m in sent_messages if m["type"] == "http.response.body"
    )
    return int(start_msg["status"]), list(start_msg.get("headers", [])), resp_body


def test_cross_user_mcp_get_and_delete_against_sse_session_return_403() -> None:
    """Cross-user GET /mcp and DELETE /mcp targeting an /sse session must return 403 Forbidden."""
    cfg = MCPSettings(GITSEED_API_URL="http://api:8420")
    app = create_app(settings=cfg, http_transport=_make_mock_api_transport([]))

    victim_sse = SSESession(session_id="sse_victim_1", token="gsp_owner_token")
    app.state.sse_sessions["sse_victim_1"] = victim_sse

    # Attacker with different token attempts DELETE /mcp against /sse session -> 403
    del_status, _, _ = _send_asgi_sync(
        app,
        method="DELETE",
        path="/mcp",
        headers=[
            (b"mcp-session-id", b"sse_victim_1"),
            (b"authorization", b"Bearer gsp_attacker_token"),
        ],
    )
    assert del_status == 403
    assert app.state.sse_sessions.get("sse_victim_1") is victim_sse
    assert victim_sse.queue.empty() is True

    # Attacker with different token attempts GET /mcp (SSE and JSON) against /sse session -> 403
    get_sse_status, _, _ = _send_asgi_sync(
        app,
        method="GET",
        path="/mcp",
        query_string="once=1",
        headers=[
            (b"mcp-session-id", b"sse_victim_1"),
            (b"authorization", b"Bearer gsp_attacker_token"),
            (b"accept", b"text/event-stream"),
        ],
    )
    assert get_sse_status == 403
    assert app.state.sse_sessions.get("sse_victim_1") is victim_sse
    assert victim_sse.queue.empty() is True

    get_json_status, _, _ = _send_asgi_sync(
        app,
        method="GET",
        path="/mcp",
        headers=[
            (b"mcp-session-id", b"sse_victim_1"),
            (b"authorization", b"Bearer gsp_attacker_token"),
        ],
    )
    assert get_json_status == 403
    assert app.state.sse_sessions.get("sse_victim_1") is victim_sse

    # Owner with matching token can terminate via DELETE /mcp -> 204 and None pushed to queue
    del_owner_status, _, _ = _send_asgi_sync(
        app,
        method="DELETE",
        path="/mcp",
        headers=[
            (b"mcp-session-id", b"sse_victim_1"),
            (b"authorization", b"Bearer gsp_owner_token"),
        ],
    )
    assert del_owner_status == 204
    assert "sse_victim_1" not in app.state.sse_sessions
    assert victim_sse.queue.get_nowait() is None


def test_mcp_get_reconnect_does_not_clobber_replacement_session() -> None:
    """Reconnecting GET /mcp closes old stream queue and old finally block preserves new session."""
    from starlette.requests import Request

    cfg = MCPSettings(GITSEED_API_URL="http://api:8420")
    app = create_app(settings=cfg, http_transport=_make_mock_api_transport([]))
    sid = "sess_reconnect_1"

    mcp_get_route = next(
        r
        for r in app.routes
        if getattr(r, "path", None) == "/mcp" and "GET" in getattr(r, "methods", set())
    )

    def _make_request() -> Request:
        async def receive() -> dict[str, Any]:
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/mcp",
            "raw_path": b"/mcp",
            "query_string": b"",
            "headers": [
                (b"authorization", b"Bearer gsp_valid_token"),
                (b"accept", b"text/event-stream"),
                (b"mcp-session-id", sid.encode("utf-8")),
            ],
        }
        return Request(scope, receive)

    # 1. Start stream 1 and advance into its event_stream() try-block
    resp1 = _step_coro_sync(mcp_get_route.endpoint(_make_request()))
    it1 = resp1.body_iterator
    first_chunk = _step_coro_sync(it1.__anext__())
    assert f": session {sid}" in (
        first_chunk.decode("utf-8") if isinstance(first_chunk, bytes) else first_chunk
    )
    first_session = app.state.sse_sessions.get(sid)
    assert first_session is not None
    assert app.state.streamable_sessions[sid]["queue"] is first_session.queue
    assert first_session.queue.empty() is True

    # 2. Reconnect stream 2 with the same Mcp-Session-Id while stream 1 is still open
    resp2 = _step_coro_sync(mcp_get_route.endpoint(_make_request()))
    it2 = resp2.body_iterator
    second_chunk = _step_coro_sync(it2.__anext__())
    assert f": session {sid}" in (
        second_chunk.decode("utf-8") if isinstance(second_chunk, bytes) else second_chunk
    )

    # Stream 1's queue received the None sentinel and stream 2 replaced the active session
    assert first_session.queue.get_nowait() is None
    second_session = app.state.sse_sessions.get(sid)
    assert second_session is not None
    assert second_session is not first_session
    assert app.state.streamable_sessions[sid]["queue"] is second_session.queue

    # 3. Run stream 1's finally block AFTER stream 2 is active — must NOT clobber stream 2!
    _step_coro_sync(it1.aclose())
    assert app.state.sse_sessions.get(sid) is second_session
    assert app.state.streamable_sessions[sid]["queue"] is second_session.queue

    # 4. Closing stream 2 now cleans up its own session cleanly
    _step_coro_sync(it2.aclose())
    assert sid not in app.state.sse_sessions
    assert app.state.streamable_sessions[sid]["queue"] is None


@pytest.mark.asyncio
async def test_mcp_toolsets_read_only_and_origin_env_fallbacks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify --read-only, --toolsets scoping, CLI flag wiring, and CORS_ORIGINS fallback."""
    monkeypatch.setenv("GITSEED_MCP_ALLOWED_ORIGINS", "")
    monkeypatch.setenv("CORS_ORIGINS", "https://app.gitseed.example/")
    monkeypatch.setenv("PUBLIC_WEB_URL", "https://portal.gitseed.example/")
    monkeypatch.setenv("ALLOWED_ORIGINS", "https://ops.gitseed.example")

    fallback_cfg = MCPSettings()
    assert "https://app.gitseed.example" in fallback_cfg.allowed_origins
    assert "https://portal.gitseed.example" in fallback_cfg.allowed_origins
    assert "https://ops.gitseed.example" in fallback_cfg.allowed_origins
    assert fallback_cfg.is_origin_allowed("https://app.gitseed.example") is True
    assert fallback_cfg.is_origin_allowed("https://attacker.example") is False

    parsed_cli = build_arg_parser().parse_args(["--toolsets", "seeds,runs", "--read-only"])
    assert parsed_cli.toolsets == "seeds,runs"
    assert parsed_cli.read_only is True

    recorded: list[dict[str, Any]] = []
    ro_seeds_server = GitSeedMCPServer(
        settings=MCPSettings(
            GITSEED_API_URL="http://api:8420",
            GITSEED_API_TOKEN="gsp_valid_token",
            GITSEED_MCP_TOOLSETS="seeds",
            GITSEED_MCP_READ_ONLY=True,
        ),
        http_transport=_make_mock_api_transport(recorded),
    )

    tools_resp = await ro_seeds_server.handle_payload(
        {"jsonrpc": "2.0", "id": "ro-list", "method": "tools/list"}
    )
    assert isinstance(tools_resp, dict)
    listed = tools_resp["result"]["tools"]
    listed_names = {t["name"] for t in listed}
    assert listed_names == {
        "list_seeds",
        "get_seed",
        "get_seed_changes",
        "get_seed_receipt",
        "list_seed_approvals",
    }
    assert all(t["annotations"]["readOnlyHint"] is True for t in listed)

    # Mutating tool inside seeds toolset is blocked in read-only mode without hitting upstream API
    recorded.clear()
    blocked_mut = await ro_seeds_server.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": "ro-mut",
            "method": "tools/call",
            "params": {
                "name": "create_seed",
                "arguments": {"project_id": "gitseed", "title": "Should fail"},
            },
        }
    )
    assert isinstance(blocked_mut, dict)
    assert blocked_mut["result"]["isError"] is True
    assert "disabled by server configuration" in blocked_mut["result"]["content"][0]["text"]
    assert recorded == []

    # Read-only tool outside seeds toolset is also blocked
    blocked_other = await ro_seeds_server.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": "ro-other",
            "method": "tools/call",
            "params": {"name": "list_agents", "arguments": {}},
        }
    )
    assert isinstance(blocked_other, dict)
    assert blocked_other["result"]["isError"] is True
    assert recorded == []

    # Allowed read-only tool in seeds toolset succeeds
    ok_receipt = await ro_seeds_server.handle_payload(
        {
            "jsonrpc": "2.0",
            "id": "ro-ok",
            "method": "tools/call",
            "params": {"name": "get_seed_receipt", "arguments": {"seed_id": "sed_1"}},
        }
    )
    assert isinstance(ok_receipt, dict)
    assert ok_receipt["result"]["isError"] is False
    assert len(recorded) == 1
    assert recorded[0]["path"] == "/api/v1/seeds/sed_1/receipt"







