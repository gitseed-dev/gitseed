from __future__ import annotations

import json
from datetime import UTC, datetime
from io import StringIO
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import httpx
import pytest
from gitseed_cli.client import CLIError, GitSeedClient
from gitseed_cli.main import app
from gitseed_cli.tui import (
    INSPECTOR_TABS,
    VIEWS,
    TUIController,
    TUIState,
    render_pipeline_bar,
    render_tui_frame,
    run_tui,
)
from rich.console import Console
from typer.testing import CliRunner

runner = CliRunner()

NOW_ISO = datetime(2026, 11, 11, 14, 0, 0, tzinfo=UTC).isoformat()


def _build_tui_mock_state() -> dict[str, Any]:
    return {
        "projects": [
            {
                "id": "prj_01hapi",
                "slug": "api",
                "name": "Core API",
                "description": "Control Plane service",
                "visibility": "private",
                "monthly_budget": "500.00",
                "seed_budget_cap": "25.00",
                "seed_counter": 2,
                "archived_at": None,
                "my_role": "maintainer",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
            {
                "id": "prj_01hweb",
                "slug": "web",
                "name": "Web Console",
                "description": "Next.js operator UI",
                "visibility": "public",
                "monthly_budget": "300.00",
                "seed_budget_cap": "15.00",
                "seed_counter": 1,
                "archived_at": None,
                "my_role": "developer",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
        ],
        "seeds": [
            {
                "id": "sed_01hratelimit",
                "project_id": "prj_01hapi",
                "number": 12,
                "title": "Add sliding-window rate limiting",
                "description": "Protect auth endpoints from brute force",
                "specification": "Use Redis Lua script for atomic sliding window.",
                "acceptance_criteria": ["Returns 429 with Retry-After header"],
                "plan": "1. Add Redis limiter\n2. Wire FastAPI middleware",
                "stage": "ideate",
                "status": "awaiting_approval",
                "autonomy": "supervised",
                "budget_cap": "15.00",
                "spent": "0.42",
                "checkpoint_approved_through": "0.00",
                "tags": ["security", "redis"],
                "created_by": "usr_01hadmin",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
            {
                "id": "sed_01hmetrics",
                "project_id": "prj_01hapi",
                "number": 13,
                "title": "Export Prometheus metrics",
                "description": "Expose /metrics for runner queue depth",
                "specification": "Counters and histograms for runs.",
                "acceptance_criteria": ["GET /metrics returns 200"],
                "plan": "1. Add prometheus-client",
                "stage": "execute",
                "status": "failed",
                "autonomy": "autonomous",
                "budget_cap": "10.00",
                "spent": "1.15",
                "checkpoint_approved_through": "0.00",
                "tags": ["observability"],
                "created_by": "usr_01hadmin",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
            {
                "id": "sed_01htheme",
                "project_id": "prj_01hweb",
                "number": 1,
                "title": "Dark mode contrast polish",
                "description": "Improve badge contrast",
                "specification": "WCAG AA compliance.",
                "acceptance_criteria": ["Contrast >= 4.5:1"],
                "plan": None,
                "stage": "review",
                "status": "running",
                "autonomy": "supervised",
                "budget_cap": "10.00",
                "spent": "0.80",
                "checkpoint_approved_through": "0.00",
                "tags": ["ui"],
                "created_by": "usr_01hadmin",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
        ],
        "tasks": [
            {
                "id": "tsk_01",
                "seed_id": "sed_01hratelimit",
                "position": 0,
                "title": "Implement Redis Lua sliding window",
                "description": "Atomic ZREMRANGEBYSCORE + ZCARD",
                "status": "done",
                "started_at": NOW_ISO,
                "completed_at": NOW_ISO,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
            {
                "id": "tsk_02",
                "seed_id": "sed_01hratelimit",
                "position": 1,
                "title": "Add FastAPI rate limit dependency",
                "description": "Attach to /api/v1/auth/login",
                "status": "running",
                "started_at": NOW_ISO,
                "completed_at": None,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
        ],
        "comments": [
            {
                "id": "cmt_01",
                "seed_id": "sed_01hratelimit",
                "author_type": "user",
                "author_id": "usr_01hadmin",
                "stage": "ideate",
                "body": "@architect Include IP + email composite key.",
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            }
        ],
        "approvals": [],
        "changes": {
            "seed_id": "sed_01hratelimit",
            "seed_number": 12,
            "repository_id": 1,
            "repository_full_name": "acme/core-api",
            "provider": "github",
            "base_ref": "main",
            "head_ref": "gitseed/seed-12",
            "comparison_mode": "branch",
            "branch_exists": True,
            "total_commits": 1,
            "total_files": 1,
            "total_additions": 14,
            "total_deletions": 2,
            "commits": [
                {
                    "sha": "a1b2c3d4e5f6",
                    "short_sha": "a1b2c3d",
                    "message": "feat(auth): add sliding window rate limiter",
                    "author_name": "@coder",
                    "committed_at": NOW_ISO,
                }
            ],
            "files": [
                {
                    "path": "apps/core/src/gitseed_core/rate_limit.py",
                    "status": "added",
                    "additions": 14,
                    "deletions": 2,
                    "changes": 16,
                    "patch": "\n".join(
                        [
                            "@@ -0,0 +1,30 @@",
                            "+def check_rate_limit(key: str) -> bool:",
                            *[f"+    line_{i} = {i}" for i in range(1, 28)],
                            "+    return True",
                        ]
                    ),
                },
                {
                    "path": "apps/core/tests/test_rate_limit.py",
                    "status": "modified",
                    "additions": 6,
                    "deletions": 1,
                    "changes": 7,
                    "patch": (
                        "@@ -1,3 +1,8 @@\n"
                        "+def test_limiter():\n"
                        "+    assert check_rate_limit('ip') is True"
                    ),
                },
            ],
        },
        "runs": [
            {
                "id": "run_01hactive",
                "seed_id": "sed_01hratelimit",
                "task_id": None,
                "agent_id": "agt_01harchitect",
                "stage": "ideate",
                "attempt": 1,
                "runtime": "claude-code",
                "model": "claude-sonnet-4-20250514",
                "status": "running",
                "input_tokens": 1500,
                "output_tokens": 600,
                "cost": "0.24",
                "output": {
                    "log_tail": "Inspecting auth routes...\nDrafting Redis sliding-window spec."
                },
                "error": None,
                "started_at": NOW_ISO,
                "finished_at": None,
                "created_at": NOW_ISO,
            }
        ],
        "sandboxes": [
            {
                "id": "sbx_01hdev",
                "project_id": "prj_01hapi",
                "seed_id": "sed_01hratelimit",
                "ref": "main",
                "head_sha": "a1b2c3d",
                "image": "python:3.12-slim",
                "command": "pytest -q",
                "status": "running",
                "started_at": NOW_ISO,
            }
        ],
        "backends": [
            {
                "id": "bnd_01hanthropic",
                "name": "Anthropic Direct",
                "type": "inference",
                "provider": "anthropic",
                "endpoint": "https://api.anthropic.com",
                "config": {},
                "secret_keys": ["api_key"],
                "capacity": None,
                "access_restricted": False,
                "allowed_agent_ids": [],
                "health": "healthy",
                "enabled": True,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
            {
                "id": "bnd_01hdocker",
                "name": "Local Docker Runner",
                "type": "compute",
                "provider": "docker",
                "endpoint": "unix:///var/run/docker.sock",
                "config": {},
                "secret_keys": [],
                "capacity": 4,
                "access_restricted": False,
                "allowed_agent_ids": [],
                "health": "healthy",
                "enabled": True,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
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
                "run_count": 5,
                "succeeded_runs": 5,
                "total_cost": "1.20",
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
                "skills": [],
                "tools": ["repo_read", "repo_write"],
                "enabled": True,
                "run_count": 8,
                "succeeded_runs": 7,
                "total_cost": "3.40",
                "archived_at": None,
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            },
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
                "overridden_fields": [],
            },
        ],
    }


@pytest.fixture()
def tui_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[GitSeedClient, dict[str, Any]]:
    monkeypatch.setenv("GITSEED_CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("GITSEED_DISABLE_KEYRING", "1")

    state = _build_tui_mock_state()

    def handler(request: httpx.Request) -> httpx.Response:
        path = unquote(request.url.path)
        method = request.method
        body = json.loads(request.content.decode()) if request.content else {}

        if method == "GET" and path == "/api/v1/projects":
            return httpx.Response(200, json=state["projects"])
        if method == "GET" and path == "/api/v1/seeds":
            return httpx.Response(200, json=state["seeds"])
        if method == "POST" and path == "/api/v1/seeds":
            created = {
                **state["seeds"][0],
                "id": "sed_01hcreated",
                "project_id": body["project_id"],
                "number": 14,
                "title": body["title"],
                "description": body.get("description") or body["title"],
                "autonomy": body.get("autonomy") or "supervised",
                "stage": "ideate",
                "status": "queued",
            }
            state["seeds"].insert(0, created)
            return httpx.Response(201, json=created)
        if method == "GET" and path.startswith("/api/v1/seeds/") and path.endswith("/tasks"):
            return httpx.Response(200, json=state["tasks"])
        if method == "GET" and path.startswith("/api/v1/seeds/") and path.endswith("/comments"):
            return httpx.Response(200, json=state["comments"])
        if method == "POST" and path.startswith("/api/v1/seeds/") and path.endswith("/comments"):
            seed_id = path.split("/")[4]
            cmt = {
                "id": f"cmt_{len(state['comments']) + 1:02d}",
                "seed_id": seed_id,
                "author_type": "user",
                "author_id": "usr_01hadmin",
                "stage": "ideate",
                "body": body["body"],
                "created_at": NOW_ISO,
                "updated_at": NOW_ISO,
            }
            state["comments"].append(cmt)
            return httpx.Response(201, json=cmt)
        if method == "GET" and path.startswith("/api/v1/seeds/") and path.endswith("/approvals"):
            return httpx.Response(200, json=state["approvals"])
        if method == "POST" and path.startswith("/api/v1/seeds/") and path.endswith("/approvals"):
            seed_id = path.split("/")[4]
            target_seed = next(s for s in state["seeds"] if s["id"] == seed_id)
            apr = {
                "id": f"apr_{len(state['approvals']) + 1:02d}",
                "seed_id": seed_id,
                "stage": target_seed["stage"],
                "decision": body["decision"],
                "comment": body.get("comment"),
                "decided_by": "usr_01hadmin",
                "created_at": NOW_ISO,
            }
            state["approvals"].append(apr)
            if body["decision"] == "approved":
                target_seed["stage"] = "plan"
                target_seed["status"] = "queued"
            elif body["decision"] == "changes_requested":
                target_seed["status"] = "queued"
            elif body["decision"] == "rejected":
                target_seed["status"] = "closed"
            return httpx.Response(201, json=apr)
        if method == "GET" and path.startswith("/api/v1/seeds/") and path.endswith("/changes"):
            return httpx.Response(200, json=state["changes"])
        if method == "GET" and path == "/api/v1/runs":
            return httpx.Response(200, json=state["runs"])
        if method == "POST" and path == "/api/v1/runs":
            new_run = {
                **state["runs"][0],
                "id": "run_01hretry",
                "seed_id": body["seed_id"],
                "attempt": 2,
                "status": "queued",
            }
            state["runs"].insert(0, new_run)
            return httpx.Response(201, json=new_run)
        if method == "POST" and path.startswith("/api/v1/runs/") and path.endswith("/cancel"):
            run_id = path.split("/")[4]
            for r in state["runs"]:
                if r["id"] == run_id:
                    r["status"] = "cancelled"
                    return httpx.Response(200, json=r)
            return httpx.Response(
                200, json={**state["runs"][0], "id": run_id, "status": "cancelled"}
            )
        if method == "GET" and path == "/api/v1/agents":
            return httpx.Response(200, json=state["agents"])
        if method == "GET" and path == "/api/v1/backends":
            return httpx.Response(200, json=state["backends"])
        if method == "GET" and path.startswith("/api/v1/policies"):
            return httpx.Response(200, json=state["policies"])
        if method == "GET" and path.endswith("/sandboxes"):
            return httpx.Response(200, json=state["sandboxes"])
        if method == "POST" and path == "/api/v1/agents/coordinator/chat":
            return httpx.Response(
                200,
                json={
                    "agent_handle": "coordinator",
                    "reply": (
                        "Seed api#12 is awaiting Ideate gate approval; "
                        "api#13 failed in Execute."
                    ),
                    "model": "claude-sonnet-4-20250514",
                    "input_tokens": 120,
                    "output_tokens": 30,
                },
            )
        return httpx.Response(404, json={"detail": f"Unhandled {method} {path}"})

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
    client = GitSeedClient("http://localhost:8420", token="gsp_test")
    return client, state


def _render_to_text(tui_state: TUIState) -> str:
    buf = StringIO()
    con = Console(file=buf, width=160, height=40, force_terminal=False, color_system=None)
    con.print(render_tui_frame(tui_state))
    return buf.getvalue()


def test_tui_views_and_inspector_tabs_render(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, _ = tui_env
    ctrl = TUIController(client)
    ctrl.refresh_data()

    assert ctrl.state.last_refresh_ok is True
    assert len(ctrl.state.seeds) == 3
    assert ctrl.state.selected_seed is not None
    assert ctrl.state.selected_seed.number == 12

    # Pipeline bar for awaiting_approval ideate seed
    pbar = render_pipeline_bar(ctrl.state.selected_seed).plain
    assert "Ideate (Needs Approval)" in pbar
    assert "Plan" in pbar
    assert "Merge" in pbar

    # Cycle through all 5 Seed Inspector tabs
    for expected_tab in INSPECTOR_TABS:
        assert ctrl.state.inspector_tab == expected_tab
        rendered = _render_to_text(ctrl.state)
        assert "GitSeed TUI" in rendered
        assert "api#12" in rendered
        ctrl.handle_key("TAB")

    # Direct shortcut keys for tabs (o, t, l, d, m)
    ctrl.handle_key("t")
    assert ctrl.state.inspector_tab == "tasks"
    assert "Implement Redis Lua sliding window" in _render_to_text(ctrl.state)

    ctrl.handle_key("l")
    assert ctrl.state.inspector_tab == "logs"
    assert "Drafting Redis sliding-window spec" in _render_to_text(ctrl.state)

    ctrl.handle_key("d")
    assert ctrl.state.inspector_tab == "diff"
    assert "acme/core-api" in _render_to_text(ctrl.state)

    ctrl.handle_key("m")
    assert ctrl.state.inspector_tab == "timeline"
    assert "@architect Include IP + email composite key." in _render_to_text(ctrl.state)

    ctrl.handle_key("o")
    assert ctrl.state.inspector_tab == "overview"
    assert "Use Redis Lua script" in _render_to_text(ctrl.state)

    # Switch across top-level views 1..4
    for key_num, view_name in zip(["1", "2", "3", "4"], VIEWS, strict=True):
        ctrl.handle_key(key_num)
        assert ctrl.state.active_view == view_name
        frame_text = _render_to_text(ctrl.state)
        assert "GitSeed TUI" in frame_text

    # Help overlay toggle
    ctrl.handle_key("?")
    assert ctrl.state.show_help is True
    assert "Shortcuts & Mouse Controls" in _render_to_text(ctrl.state)
    ctrl.handle_key("Escape")
    assert ctrl.state.show_help is False


def test_tui_navigation_project_cycle_and_filter(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, _ = tui_env
    ctrl = TUIController(client)
    ctrl.refresh_data()

    # Move down & up with j/k and G/g
    assert ctrl.state.selected_index == 0
    ctrl.handle_key("j")
    assert ctrl.state.selected_index == 1
    assert ctrl.state.selected_seed is not None
    assert ctrl.state.selected_seed.number == 13

    ctrl.handle_key("G")
    assert ctrl.state.selected_index == 2
    ctrl.handle_key("g")
    assert ctrl.state.selected_index == 0

    # Scroll inspector with J / U and PGDN / PGUP
    ctrl.handle_key("J")
    assert ctrl.state.scroll_offset == 4
    ctrl.handle_key("PGDN")
    assert ctrl.state.scroll_offset == 16
    ctrl.handle_key("PGUP")
    assert ctrl.state.scroll_offset == 4
    ctrl.handle_key("U")
    assert ctrl.state.scroll_offset == 0

    # Project filter cycling with 'p' (None -> api -> web -> None)
    assert ctrl.state.project_filter is None
    ctrl.handle_key("p")
    assert ctrl.state.project_filter == "api"
    assert len(ctrl.state.filtered_seeds) == 2

    ctrl.handle_key("p")
    assert ctrl.state.project_filter == "web"
    assert len(ctrl.state.filtered_seeds) == 1

    ctrl.handle_key("p")
    assert ctrl.state.project_filter is None
    assert len(ctrl.state.filtered_seeds) == 3

    # Interactive search filter with '/'
    ctrl.handle_key("/")
    assert ctrl.state.input_mode == "filter"
    for ch in "prometheus":
        ctrl.handle_key(ch)
    ctrl.handle_key("Enter")
    assert ctrl.state.input_mode is None
    assert ctrl.state.search_query == "prometheus"
    assert len(ctrl.state.filtered_seeds) == 1
    assert ctrl.state.selected_seed is not None
    assert ctrl.state.selected_seed.number == 13


def test_tui_operator_actions_approve_changes_steer_retry_cancel_and_coordinator(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, mock_state = tui_env
    ctrl = TUIController(client)
    ctrl.refresh_data()

    # 1. Approve seed #12 via 'a'
    ctrl.handle_key("a")
    assert ctrl.state.input_mode == "approve"
    for ch in "LGTM!":
        ctrl.handle_key(ch)
    ctrl.handle_key("Enter")
    assert mock_state["seeds"][0]["stage"] == "plan"
    assert "Approved stage ideate on seed #12" in ctrl.state.status_message

    # 2. Request changes via 'x'
    ctrl.handle_key("x")
    assert ctrl.state.input_mode == "request_changes"
    for ch in "Add fallback when Redis is down":
        ctrl.handle_key(ch)
    ctrl.handle_key("Enter")
    assert len(mock_state["approvals"]) == 2
    assert mock_state["approvals"][-1]["decision"] == "changes_requested"

    # 3. Steer active stage @agent via 'c'
    ctrl.handle_key("c")
    assert ctrl.state.input_mode == "steer"
    for ch in "Use pipeline for batching":
        ctrl.handle_key(ch)
    ctrl.handle_key("Enter")
    assert ctrl.state.inspector_tab == "timeline"
    assert mock_state["comments"][-1]["body"].startswith("@planner ")

    # 4. Ask @coordinator via 'K'
    ctrl.handle_key("K")
    assert ctrl.state.input_mode == "ask_coordinator"
    for ch in "Status summary?":
        ctrl.handle_key(ch)
    ctrl.handle_key("Enter")
    assert ctrl.state.coordinator_reply is not None
    assert "Seed api#12 is awaiting Ideate gate approval" in ctrl.state.coordinator_reply

    # 5. Cancel active run on seed #12 via 'z'
    ctrl.handle_key("z")
    assert mock_state["runs"][0]["status"] == "cancelled"
    assert "Cancelled run" in ctrl.state.status_message

    # 6. Retry failed seed #13 via 'r'
    ctrl.handle_key("j")  # select seed #13 (status=failed)
    assert ctrl.state.selected_seed is not None
    assert ctrl.state.selected_seed.number == 13
    ctrl.handle_key("r")
    assert "Queued retry run run_01hretry" in ctrl.state.status_message

    # 7. Guided 3-step New Seed flow via 'n' when no project is scoped:
    # Step 1: Project ('web' idx 1) -> Step 2: Autonomy ('autonomous' #2) -> Step 3: Prompt
    ctrl.handle_key("n")
    assert ctrl.state.input_mode == "new_seed_project"
    assert "New Seed Wizard" in _render_to_text(ctrl.state)
    ctrl.handle_key("j")  # move to project #2 (web)
    ctrl.handle_key("Enter")
    assert ctrl.state.input_mode == "new_seed_autonomy"
    assert ctrl.state.new_seed_project_id == "prj_01hweb"
    ctrl.handle_key("2")  # select #2 Autonomous and confirm
    assert ctrl.state.input_mode == "new_seed_prompt"
    assert ctrl.state.new_seed_autonomy == "autonomous"
    for ch in "Add OpenTelemetry tracing":
        ctrl.handle_key(ch)
    ctrl.handle_key("Enter")
    assert ctrl.state.input_mode is None
    expected_msg = "Created seed web#14 (autonomous): Add OpenTelemetry tracing"
    assert expected_msg in ctrl.state.status_message

    # 7b. Guided New Seed flow when project is already scoped skips Step 1 (Project Picker):
    ctrl.state.project_filter = "api"
    ctrl.handle_key("n")
    assert ctrl.state.input_mode == "new_seed_autonomy"
    assert ctrl.state.new_seed_project_id == "prj_01hapi"
    ctrl.handle_key("Escape")
    assert ctrl.state.input_mode is None

    # 8. Mouse click & scroll events
    ctrl.state.project_filter = None
    ctrl.state.terminal_width = 144
    ctrl.state.terminal_height = 36
    # Click top nav tab [2] Projects (row=3, col=22)
    ctrl.handle_key("MOUSE_CLICK:22:3")
    assert ctrl.state.active_view == "projects"
    # Click top nav tab [1] Seeds (row=3, col=6)
    ctrl.handle_key("MOUSE_CLICK:6:3")
    assert ctrl.state.active_view == "seeds"
    # Click second row in left Seeds table (row=9, col=10)
    ctrl.handle_key("MOUSE_CLICK:10:9")
    assert ctrl.state.selected_index == 1
    # Click inspector Tasks sub-tab on the right pane (row=9, col=88)
    ctrl.handle_key("MOUSE_CLICK:88:9")
    assert ctrl.state.inspector_tab == "tasks"
    # Mouse wheel scroll on right inspector pane
    ctrl.handle_key("MOUSE_WHEEL_DOWN:100:15")
    assert ctrl.state.scroll_offset == 3
    ctrl.handle_key("MOUSE_WHEEL_UP:100:15")
    assert ctrl.state.scroll_offset == 0

    # 9. Quit returns False
    assert ctrl.handle_key("q") is False


def test_tui_cli_command_once_and_error_handling(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, _ = tui_env

    # Direct run_tui(..., once=True)
    buf = StringIO()
    con = Console(file=buf, width=120, height=34, force_terminal=False, color_system=None)
    st = run_tui(client, initial_project="api", initial_view="seeds", once=True, console=con)
    assert st.project_filter == "api"
    assert "GitSeed TUI" in buf.getvalue()

    # CliRunner invocation of `gitseed tui --once` and `gitseed dash --once`
    res = runner.invoke(app, ["tui", "--once", "-p", "api", "--view", "projects"])
    assert res.exit_code == 0, res.output
    assert "GitSeed TUI" in res.output
    assert "Core API" in res.output

    dash_res = runner.invoke(app, ["dash", "--once", "--view", "backends"])
    assert dash_res.exit_code == 0, dash_res.output
    assert "Anthropic Direct" in dash_res.output

    # Invalid view returns exit code 2
    bad_view = runner.invoke(app, ["tui", "--once", "--view", "invalid"])
    assert bad_view.exit_code == 2
    assert "Unknown TUI view" in bad_view.output

    # API error during refresh sets error_message gracefully without crashing
    def broken_projects(*args: Any, **kwargs: Any) -> Any:
        raise CLIError("Connection refused", status_code=503)

    monkeypatch.setattr(client, "list_projects", broken_projects)
    ctrl = TUIController(client)
    ctrl.refresh_data()
    assert ctrl.state.last_refresh_ok is False
    assert ctrl.state.error_message == "Connection refused"
    err_render = _render_to_text(ctrl.state)
    assert "Connection refused" in err_render


def test_tui_diff_file_picker_and_line_scrolling(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, _ = tui_env
    ctrl = TUIController(client)
    ctrl.refresh_data()

    # Switch to Diff tab ('d')
    ctrl.handle_key("d")
    assert ctrl.state.inspector_tab == "diff"
    assert ctrl.state.diff_file_index == 0  # Default: All Files

    rendered_all = _render_to_text(ctrl.state)
    assert "All Files (2)" in rendered_all
    assert "apps/core/src/gitseed_core/rate_limit.py" in rendered_all
    assert "apps/core/tests/test_rate_limit.py" in rendered_all

    # Cycle to File 1 with 'f'
    ctrl.handle_key("f")
    assert ctrl.state.diff_file_index == 1
    assert ctrl.state.scroll_offset == 0
    rendered_f1 = _render_to_text(ctrl.state)
    assert "Diff: apps/core/src/gitseed_core/rate_limit.py" in rendered_f1

    # Scroll down inside File 1's patch lines with 'J' and 'PGDN' and verify line window advances
    ctrl.handle_key("J")
    assert ctrl.state.scroll_offset == 4
    rendered_scrolled = _render_to_text(ctrl.state)
    assert "Lines 5–" in rendered_scrolled
    assert "line_5 = 5" in rendered_scrolled

    ctrl.handle_key("PGDN")
    assert ctrl.state.scroll_offset == 16
    rendered_pgdn = _render_to_text(ctrl.state)
    assert "line_27 = 27" in rendered_pgdn

    # Cycle to File 2 with 'RIGHT' (resets scroll_offset to 0)
    ctrl.handle_key("RIGHT")
    assert ctrl.state.diff_file_index == 2
    assert ctrl.state.scroll_offset == 0
    rendered_f2 = _render_to_text(ctrl.state)
    assert "Diff: apps/core/tests/test_rate_limit.py" in rendered_f2
    assert "def test_limiter():" in rendered_f2

    # Press '0' to return to All Files view
    ctrl.handle_key("0")
    assert ctrl.state.diff_file_index == 0
    assert ctrl.state.scroll_offset == 0

    # Verify background refresh does not flash "Loading repository diff…" or re-fetch unchanged diff
    changes_calls = 0
    orig_get_changes = client.get_seed_changes

    def spy_get_changes(seed_id: str) -> Any:
        nonlocal changes_calls
        changes_calls += 1
        return orig_get_changes(seed_id)

    client.get_seed_changes = spy_get_changes  # type: ignore[method-assign]
    ctrl.handle_key("f")  # select file 1
    ctrl.handle_key("J")  # scroll to line 5
    ctrl.invalidate_cache(force_changes=False)
    ctrl._sync_in_memory_selection()
    assert ctrl.state.detail_loading is False
    assert ctrl.state.seed_changes is not None
    assert "Loading repository diff" not in _render_to_text(ctrl.state)

    ctrl.refresh_data(invalidate=True, force_changes=False)
    assert changes_calls == 0
    assert ctrl.state.detail_loading is False
    assert ctrl.state.diff_file_index == 1
    assert ctrl.state.scroll_offset == 4


def test_tui_paste_input_and_viewport_scrolling(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, mock_state = tui_env
    # Populate 30 seeds to exercise list viewport slicing and mouse offset
    base_seed = mock_state["seeds"][0]
    mock_state["seeds"] = [
        {
            **base_seed,
            "id": f"sed_{i:02d}",
            "number": i + 1,
            "title": f"Seed item {i + 1}",
        }
        for i in range(30)
    ]

    ctrl = TUIController(client)
    ctrl.refresh_data()
    ctrl.state.terminal_width = 144
    ctrl.state.terminal_height = 36  # max_rows = 20

    # Navigate down to index 25
    for _ in range(25):
        ctrl.handle_key("j")
    assert ctrl.state.selected_index == 25
    assert ctrl.state.list_scroll_offsets["seeds"] > 0

    rendered = _render_to_text(ctrl.state)
    assert "Seed item 26" in rendered

    # Mouse click on row 8 (first visible row in the scrolled viewport) selects start_idx
    expected_start = ctrl.state.list_scroll_offsets["seeds"]
    ctrl.handle_key("MOUSE_CLICK:10:8")
    assert ctrl.state.selected_index == expected_start

    # Multi-character paste into modal input buffer
    ctrl.handle_key("/")
    assert ctrl.state.input_mode == "filter"
    ctrl.handle_key("Seed item 26")
    assert ctrl.state.input_buffer == "Seed item 26"
    # Special tokens are ignored as text
    ctrl.handle_key("UP")
    assert ctrl.state.input_buffer == "Seed item 26"
    ctrl.handle_key("Enter")
    assert ctrl.state.search_query == "Seed item 26"
    assert len(ctrl.state.filtered_seeds) == 1


def test_tui_posix_utf8_and_escape_sequence_decoding(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import gitseed_cli.tui.app as tui_app

    tui_app._reset_posix_input_state()

    chunks: list[bytes] = [
        # Single 32-byte read containing UP arrow, DOWN arrow, and multi-byte UTF-8 ('é', '中')
        b"\x1b[A\x1b[B\xc3\xa9\xe4\xb8\xad",
        # Split 4-byte emoji '🚀' (\xf0\x9f\x9a\x80) across two os.read() calls
        b"\xf0\x9f",
        b"\x9a\x80\r",
    ]

    class _FakeSelect:
        @staticmethod
        def select(
            rlist: list[Any],
            _wlist: list[Any],
            _xlist: list[Any],
            _timeout: float = 0.0,
        ) -> tuple[list[Any], list[Any], list[Any]]:
            return (rlist if chunks else [], [], [])

    def _fake_os_read(_fd: int, n: int) -> bytes:
        assert n == 32
        return chunks.pop(0) if chunks else b""

    monkeypatch.setattr(tui_app, "select", _FakeSelect)
    monkeypatch.setattr(tui_app.os, "read", _fake_os_read)

    assert tui_app._read_key_posix_once(0.01, fd=99) == "UP"
    assert tui_app._read_key_posix_once(0.01, fd=99) == "DOWN"
    assert tui_app._read_key_posix_once(0.01, fd=99) == "é"
    assert tui_app._read_key_posix_once(0.01, fd=99) == "中"
    assert tui_app._read_key_posix_once(0.01, fd=99) == "🚀"
    assert tui_app._read_key_posix_once(0.01, fd=99) == "Enter"
    assert tui_app._read_key_posix_once(0.01, fd=99) is None
    tui_app._reset_posix_input_state()


def test_tui_async_debounced_navigation_and_rlock_safety(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    import threading
    import time

    client, _mock_state = tui_env
    ctrl = TUIController(client, async_mode=True, debounce_seconds=0.015)
    ctrl.refresh_data()
    try:
        main_thread_id = threading.get_ident()
        detail_call_threads: list[int] = []
        orig_tasks = client.list_seed_tasks
        orig_logs = client.get_run_logs

        def spy_tasks(seed_id: str) -> Any:
            detail_call_threads.append(threading.get_ident())
            return orig_tasks(seed_id)

        def spy_logs(run_id: str, *, tail: int = 200) -> Any:
            detail_call_threads.append(threading.get_ident())
            return orig_logs(run_id, tail=tail)

        client.list_seed_tasks = spy_tasks  # type: ignore[method-assign]
        client.get_run_logs = spy_logs  # type: ignore[method-assign]

        ctrl.invalidate_cache()
        detail_call_threads.clear()

        # Rapid j/j/k navigation updates selection immediately with zero HTTP calls on main thread
        ctrl.handle_key("j")
        ctrl.handle_key("j")
        ctrl.handle_key("k")
        assert ctrl.state.selected_index == 1
        assert main_thread_id not in detail_call_threads

        # Render concurrently under controller.lock while debounced background worker runs
        deadline = time.monotonic() + 0.5
        while time.monotonic() < deadline and not detail_call_threads:
            with ctrl.lock:
                _ = render_tui_frame(ctrl.state)
            time.sleep(0.01)

        assert detail_call_threads
        assert all(tid != main_thread_id for tid in detail_call_threads)
    finally:
        ctrl.close()


def test_tui_escape_sequences_extended_keys_and_modal_safety(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    import gitseed_cli.tui.app as tui_app

    def _feed_tail(seq_after_esc: str) -> tuple[str, str]:
        chars = list(seq_after_esc)
        tok = tui_app._parse_escape_tail(lambda: chars.pop(0) if chars else None)
        return tok, "".join(chars)

    # CSI / SS3 extended keys
    assert _feed_tail("[3~") == ("DELETE", "")
    assert _feed_tail("[H") == ("HOME", "")
    assert _feed_tail("[1~") == ("HOME", "")
    assert _feed_tail("[7~") == ("HOME", "")
    assert _feed_tail("OH") == ("HOME", "")
    assert _feed_tail("[F") == ("END", "")
    assert _feed_tail("[4~") == ("END", "")
    assert _feed_tail("[8~") == ("END", "")
    assert _feed_tail("OF") == ("END", "")
    assert _feed_tail("[Z") == ("SHIFT_TAB", "")
    assert _feed_tail("[1;5A") == ("UP", "")
    assert _feed_tail("[1;5B") == ("DOWN", "")
    assert _feed_tail("[1;5C") == ("RIGHT", "")
    assert _feed_tail("[1;5D") == ("LEFT", "")

    # Unrecognized function keys consume entire sequence including '~' and return "" (no-op)
    assert _feed_tail("[15~") == ("", "")
    assert _feed_tail("[24~") == ("", "")
    assert _feed_tail("OP") == ("", "")
    assert _feed_tail("[15~abc") == ("", "abc")

    # Standalone ESC returns "Escape"
    assert _feed_tail("") == ("Escape", "")
    assert _feed_tail("\x1b") == ("Escape", "")

    # Windows msvcrt extended scan codes
    assert tui_app._decode_msvcrt_char("\xe0", lambda: "S") == "DELETE"
    assert tui_app._decode_msvcrt_char("\xe0", lambda: "G") == "HOME"
    assert tui_app._decode_msvcrt_char("\xe0", lambda: "O") == "END"
    assert tui_app._decode_msvcrt_char("\x00", lambda: "S") == "DELETE"

    # Verify TUI modals do not close or leak '~' when DELETE / HOME / END / SHIFT_TAB / F5 arrive
    client, _ = tui_env
    ctrl = TUIController(client)
    ctrl.refresh_data()

    # Normal view navigation with END / HOME / SHIFT_TAB
    ctrl.handle_key("END")
    assert ctrl.state.selected_index == 2
    ctrl.handle_key("HOME")
    assert ctrl.state.selected_index == 0
    assert ctrl.state.inspector_tab == "overview"
    ctrl.handle_key("SHIFT_TAB")
    assert ctrl.state.inspector_tab == INSPECTOR_TABS[-1]
    ctrl.handle_key("TAB")
    assert ctrl.state.inspector_tab == "overview"

    # Modal safety
    ctrl.handle_key("a")
    assert ctrl.state.input_mode == "approve"
    for ch in "ok":
        ctrl.handle_key(ch)
    for special in ("DELETE", "HOME", "END", "SHIFT_TAB", _feed_tail("[15~")[0]):
        ctrl.handle_key(special)
    assert ctrl.state.input_mode == "approve"
    assert ctrl.state.input_buffer == "ok"


def test_tui_blocking_actions_never_hold_controller_lock_during_http(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import threading

    client, _ = tui_env
    ctrl = TUIController(client, async_mode=True, debounce_seconds=0.005)
    ctrl.refresh_data()
    try:
        lock_free_observations: list[bool] = []

        def _probe_lock_from_other_thread() -> bool:
            acquired: list[bool] = []

            def _try_lock() -> None:
                got = ctrl.lock.acquire(blocking=False)
                if got:
                    ctrl.lock.release()
                acquired.append(got)

            t = threading.Thread(target=_try_lock)
            t.start()
            t.join(timeout=1.0)
            return bool(acquired and acquired[0])

        orig_approval = client.create_seed_approval
        orig_create_seed = client.create_seed
        orig_coord = client.chat_coordinator
        orig_projects = client.list_projects

        def wrapped_approval(*args: Any, **kwargs: Any) -> Any:
            lock_free_observations.append(_probe_lock_from_other_thread())
            return orig_approval(*args, **kwargs)

        def wrapped_create_seed(*args: Any, **kwargs: Any) -> Any:
            lock_free_observations.append(_probe_lock_from_other_thread())
            return orig_create_seed(*args, **kwargs)

        def wrapped_coord(*args: Any, **kwargs: Any) -> Any:
            lock_free_observations.append(_probe_lock_from_other_thread())
            return orig_coord(*args, **kwargs)

        def wrapped_projects(*args: Any, **kwargs: Any) -> Any:
            lock_free_observations.append(_probe_lock_from_other_thread())
            return orig_projects(*args, **kwargs)

        monkeypatch.setattr(client, "create_seed_approval", wrapped_approval)
        monkeypatch.setattr(client, "create_seed", wrapped_create_seed)
        monkeypatch.setattr(client, "chat_coordinator", wrapped_coord)
        monkeypatch.setattr(client, "list_projects", wrapped_projects)

        # 1. Manual refresh on 'R'
        ctrl.handle_key("R")
        ctrl.wait_for_actions()
        assert "Refreshed workspace data" in ctrl.state.status_message

        # 2. Approval on 'a'
        ctrl.handle_key("a")
        ctrl.handle_key("LGTM")
        ctrl.handle_key("Enter")
        ctrl.wait_for_actions()
        assert "Approved stage" in ctrl.state.status_message

        # 3. Coordinator chat on 'K'
        ctrl.handle_key("K")
        ctrl.handle_key("Status?")
        ctrl.handle_key("Enter")
        ctrl.wait_for_actions()
        assert ctrl.state.coordinator_reply is not None

        # 4. Create seed on 'n'
        ctrl.handle_key("n")
        ctrl.handle_key("Enter")
        ctrl.handle_key("1")
        ctrl.handle_key("Ship non-blocking TUI")
        ctrl.handle_key("Enter")
        ctrl.wait_for_actions()
        assert "Created seed" in ctrl.state.status_message

        assert len(lock_free_observations) >= 4
        assert all(lock_free_observations)
    finally:
        ctrl.close()


def test_tui_help_overlay_consumes_all_action_keys(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, state = tui_env
    ctrl = TUIController(client)
    ctrl.refresh_data()

    initial_runs_len = len(state["runs"])
    initial_run_status = state["runs"][0]["status"]

    # Open Help overlay
    assert ctrl.handle_key("?") is True
    assert ctrl.state.show_help is True

    # Action and navigation keys must be consumed without mutating underlying TUI/API state
    for blocked_key in (
        "z",
        "r",
        "a",
        "x",
        "X",
        "c",
        "C",
        "n",
        "K",
        "1",
        "2",
        "3",
        "4",
        "j",
        "k",
        "p",
        "/",
        "R",
        "MOUSE_WHEEL_DOWN:20:10",
    ):
        assert ctrl.handle_key(blocked_key) is True
        assert ctrl.state.show_help is True
        assert ctrl.state.input_mode is None
        assert ctrl.state.active_view == "seeds"
        assert ctrl.state.selected_index == 0
        assert ctrl.state.project_filter is None

    assert len(state["runs"]) == initial_runs_len
    assert state["runs"][0]["status"] == initial_run_status

    # 'q', '?', 'Escape', and 'Enter' close Help without quitting the TUI
    for close_key in ("q", "?", "Escape", "Enter"):
        ctrl.state.show_help = True
        assert ctrl.handle_key(close_key) is True
        assert ctrl.state.show_help is False


def test_tui_renders_bracket_markup_paths_without_markup_error(
    tui_env: tuple[GitSeedClient, dict[str, Any]],
) -> None:
    client, state = tui_env
    state["seeds"][0]["title"] = "Fix Next.js route app/[...slug]/page.tsx [/0]"
    state["tasks"][0]["title"] = "Patch app/[...slug]/page.tsx and handle [/0] token"
    state["changes"]["files"][0]["path"] = "app/[...slug]/page.tsx"
    state["changes"]["files"][1]["path"] = "src/parser[/0]/lexer.ts"
    state["sandboxes"][0]["ref"] = "feat/[...slug]"
    state["sandboxes"][0]["image"] = "ghcr.io/org/runner:[v1][/0]"
    state["sandboxes"][0]["command"] = "pytest -k 'test_route[/0]'"
    state["agents"][0]["name"] = "Router Agent [/0]"
    state["agents"][0]["model"] = "custom/[...slug]/v1[/0]"

    ctrl = TUIController(client)
    ctrl.refresh_data()

    buf = StringIO()
    test_console = Console(file=buf, width=160, height=42, force_terminal=True)

    # Render Seeds -> Tasks tab
    ctrl.state.active_view = "seeds"
    ctrl.state.inspector_tab = "tasks"
    test_console.print(render_tui_frame(ctrl.state))

    # Render Seeds -> Diff tab (All Files and single file with app/[...slug]/page.tsx & [/0])
    ctrl.state.inspector_tab = "diff"
    ctrl.state.diff_file_index = 0
    test_console.print(render_tui_frame(ctrl.state))
    ctrl.state.diff_file_index = 1
    test_console.print(render_tui_frame(ctrl.state))
    ctrl.state.diff_file_index = 2
    test_console.print(render_tui_frame(ctrl.state))

    # Render Projects, Runs & Sandboxes, and Backends & Agents views
    for view_name in VIEWS:
        ctrl.state.active_view = view_name
        test_console.print(render_tui_frame(ctrl.state))

    rendered = buf.getvalue()
    assert "app/[...slug]/page.tsx" in rendered


def test_tui_alt_arrow_double_escape_drains_csi_without_buffering_bracket() -> None:
    import gitseed_cli.tui.app as tui_app

    def _feed_tail(seq_after_esc: str) -> tuple[str, str]:
        chars = list(seq_after_esc)
        tok = tui_app._parse_escape_tail(lambda: chars.pop(0) if chars else None)
        return tok, "".join(chars)

    # Double-ESC CSI/SS3 sequences (e.g. Alt+Up \x1b\x1b[A) drain cleanly without leaving '['
    assert _feed_tail("\x1b[A") == ("", "")
    assert _feed_tail("\x1b[1;3A") == ("", "")
    assert _feed_tail("\x1b[Z") == ("", "")
    assert _feed_tail("\x1bOA") == ("", "")
    assert _feed_tail("\x1b[Aj") == ("", "j")

    # Verify _read_key_posix_once drains \x1b\x1b[A from _POSIX_CHAR_BUFFER cleanly
    tui_app._reset_posix_input_state()
    try:
        tui_app._POSIX_CHAR_BUFFER.extend("\x1b\x1b[A")
        tok = tui_app._read_key_posix_once(0.0, fd=0)
        assert tok is None
        assert list(tui_app._POSIX_CHAR_BUFFER) == []
    finally:
        tui_app._reset_posix_input_state()





