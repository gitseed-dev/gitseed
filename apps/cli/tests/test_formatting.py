from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from gitseed_cli import formatting
from gitseed_common.schemas import (
    ActivityResponse,
    AgentResponse,
    ApprovalResponse,
    BackendResponse,
    ProjectResponse,
    RunResponse,
    SandboxSessionResponse,
    SeedChangesResponse,
    SeedCommentResponse,
    SeedResponse,
    SeedTaskResponse,
    SkillResponse,
    StagePolicyResponse,
    TokenResponse,
    UserResponse,
)

NOW_DT = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
NOW_ISO = NOW_DT.isoformat()


def _formatting_sample_state() -> dict[str, Any]:
    return {
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
            }
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
            }
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
            }
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


def test_formatting_scalar_helpers_and_dump_json(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _formatting_sample_state()
    assert formatting._fmt_dt(None) == "-"
    assert formatting._fmt_dt(NOW_DT) == "2026-11-11 12:00"
    assert formatting._fmt_money(None) == "-"
    assert formatting._fmt_money(Decimal("12.5")) == "$12.50"
    assert formatting._fmt_money("invalid") == "invalid"

    proj = ProjectResponse.model_validate(state["projects"][0])
    formatting.dump_json(proj)
    formatting.dump_json([proj])
    formatting.dump_json({"ok": True})

    out = capsys.readouterr().out
    assert "prj_01hapi" in out
    assert '"ok": true' in out


def test_formatting_all_table_and_detail_printers(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _formatting_sample_state()

    proj = ProjectResponse.model_validate(state["projects"][0])
    seed = SeedResponse.model_validate(state["seeds"][0])
    task = SeedTaskResponse.model_validate(state["tasks"][0])
    run = RunResponse.model_validate(state["runs"][0])
    agent = AgentResponse.model_validate(state["agents"][0])
    skill = SkillResponse.model_validate(state["skills"][0])
    policy = StagePolicyResponse.model_validate(state["policies"][0])
    backend = BackendResponse.model_validate(state["backends"][0])
    sandbox = SandboxSessionResponse.model_validate(state["sandboxes"][0])
    user = UserResponse.model_validate(state["users"][0])
    token = TokenResponse.model_validate(state["tokens"][0])
    activity = ActivityResponse.model_validate(state["activity"][0])
    approval = ApprovalResponse.model_validate(state["approvals"][0])
    comment = SeedCommentResponse.model_validate(state["comments"][0])
    changes = SeedChangesResponse.model_validate(state["changes"])

    # All table printers in normal and wide mode
    for wide in (False, True):
        formatting.print_projects_table([proj], wide=wide)
        formatting.print_seeds_table([seed], project_slugs={proj.id: proj.slug}, wide=wide)
        formatting.print_tasks_table([task], wide=wide)
        formatting.print_runs_table([run], agent_handles={agent.id: f"@{agent.handle}"}, wide=wide)
        formatting.print_agents_table([agent], wide=wide)
        formatting.print_skills_table([skill], wide=wide)
        formatting.print_policies_table(
            [policy], agent_handles={agent.id: f"@{agent.handle}"}, wide=wide
        )
        formatting.print_backends_table([backend], wide=wide)
        formatting.print_sandboxes_table([sandbox], wide=wide)
        formatting.print_users_table([user], wide=wide)
        formatting.print_tokens_table([token], wide=wide)

    formatting.print_events_table([activity])
    formatting.describe_seed(
        seed,
        project_slug="api",
        tasks=[task],
        approvals=[approval],
        comments=[comment],
        runs=[run],
    )
    formatting.render_seed_diff(changes)
    empty_changes = SeedChangesResponse.model_validate({**state["changes"], "files": []})
    formatting.render_seed_diff(empty_changes)

    out = capsys.readouterr().out
    assert "Core API" in out
    assert "sliding-window" in out
    assert "src/rate_limit.py" in out


def test_formatting_escapes_untrusted_rich_markup_tags(
    capsys: pytest.CaptureFixture[str],
) -> None:
    state = _formatting_sample_state()
    hostile = "crash [/div] ansi [0m] slice arr[/] [bold]notbold"

    seed_data = {
        **state["seeds"][0],
        "title": f"Seed {hostile}",
        "description": f"Summary {hostile}",
        "specification": f"Spec {hostile}",
        "acceptance_criteria": [f"Criterion {hostile}"],
        "plan": f"Plan {hostile}",
        "blocked_reason": hostile,
        "tags": [hostile],
    }
    task_data = {**state["tasks"][0], "title": f"Task {hostile}"}
    approval_data = {**state["approvals"][0], "comment": hostile}
    comment_data = {**state["comments"][0], "body": hostile}
    changes_data = {
        **state["changes"],
        "repository_full_name": f"org/repo-{hostile}",
        "commits": [{**state["changes"]["commits"][0], "message": hostile}],
        "files": [{**state["changes"]["files"][0], "path": f"src/{hostile}.py"}],
    }

    seed = SeedResponse.model_validate(seed_data)
    task = SeedTaskResponse.model_validate(task_data)
    approval = ApprovalResponse.model_validate(approval_data)
    comment = SeedCommentResponse.model_validate(comment_data)
    changes = SeedChangesResponse.model_validate(changes_data)

    formatting.describe_seed(
        seed,
        project_slug="api",
        tasks=[task],
        approvals=[approval],
        comments=[comment],
    )
    formatting.render_seed_diff(changes)

    out = capsys.readouterr().out
    assert "[/div]" in out
    assert "[0m]" in out
    assert "arr[/]" in out
