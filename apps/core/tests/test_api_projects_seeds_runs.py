from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from gitseed_common.enums import (
    ActorType,
    AgentRuntime,
    ApprovalDecision,
    ProjectRole,
    SeedAutonomy,
    SeedStage,
    TaskStatus,
)
from gitseed_core.api.v1 import (
    projects as projects_api,
)
from gitseed_core.api.v1 import (
    runner as runner_api,
)
from gitseed_core.api.v1 import (
    runs as runs_api,
)
from gitseed_core.api.v1 import (
    sandboxes as sandboxes_api,
)
from gitseed_core.api.v1 import (
    seeds as seeds_api,
)
from gitseed_core.models.approval import Approval
from gitseed_core.models.project import ProjectMember, ProjectRepository
from gitseed_core.models.seed import SeedComment, SeedTask
from gitseed_core.models.user import User
from gitseed_core.schemas.runner import (
    RunnerContextResponse,
    RunnerGatewayConfig,
    RunnerGitPushTokenResponse,
    RunnerSteeringResponse,
)

from conftest import _make_project, _make_run, _make_sandbox, _make_seed, _now


@pytest.mark.asyncio
async def test_projects_routes(
    client: httpx.AsyncClient,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    proj = _make_project()
    monkeypatch.setattr(
        projects_api.projects_service, "list_projects", AsyncMock(return_value=[proj])
    )
    monkeypatch.setattr(
        projects_api.projects_service,
        "get_allowed_backend_ids_map",
        AsyncMock(return_value={proj.id: ["bnd_1"]}),
    )
    monkeypatch.setattr(
        projects_api.projects_service,
        "get_member_roles_map",
        AsyncMock(return_value={proj.id: ProjectRole.MAINTAINER}),
    )
    monkeypatch.setattr(
        projects_api.projects_service,
        "get_allowed_backend_ids",
        AsyncMock(return_value=["bnd_1"]),
    )
    monkeypatch.setattr(
        projects_api.projects_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.MAINTAINER),
    )

    resp = await client.get("/api/v1/projects")
    assert resp.status_code == 200
    assert resp.json()[0]["id"] == proj.id

    monkeypatch.setattr(
        projects_api.projects_service, "create_project", AsyncMock(return_value=proj)
    )
    resp = await client.post(
        "/api/v1/projects",
        json={"name": "Alpha Project", "slug": "alpha-project"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        projects_api.projects_service, "get_project", AsyncMock(return_value=proj)
    )
    resp = await client.get(f"/api/v1/projects/{proj.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        projects_api.projects_service, "update_project", AsyncMock(return_value=proj)
    )
    resp = await client.patch(
        f"/api/v1/projects/{proj.id}", json={"description": "New description"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        projects_api.projects_service, "archive_project", AsyncMock(return_value=proj)
    )
    resp = await client.post(f"/api/v1/projects/{proj.id}/archive")
    assert resp.status_code == 200

    monkeypatch.setattr(
        projects_api.projects_service, "restore_project", AsyncMock(return_value=proj)
    )
    resp = await client.post(f"/api/v1/projects/{proj.id}/restore")
    assert resp.status_code == 200

    # Members
    member = ProjectMember(
        id=1,
        project_id=proj.id,
        user_id=admin_user.id,
        role=ProjectRole.MAINTAINER,
        created_at=_now(),
        updated_at=_now(),
    )
    monkeypatch.setattr(
        projects_api.projects_service,
        "list_members",
        AsyncMock(return_value=[(member, admin_user)]),
    )
    resp = await client.get(f"/api/v1/projects/{proj.id}/members")
    assert resp.status_code == 200
    assert resp.json()[0]["email"] == admin_user.email

    monkeypatch.setattr(
        projects_api.projects_service,
        "add_member",
        AsyncMock(return_value=(member, admin_user)),
    )
    resp = await client.post(
        f"/api/v1/projects/{proj.id}/members",
        json={"user_id": admin_user.id, "role": "maintainer"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        projects_api.projects_service,
        "update_member_role",
        AsyncMock(return_value=(member, admin_user)),
    )
    resp = await client.patch(
        f"/api/v1/projects/{proj.id}/members/{admin_user.id}",
        json={"role": "developer"},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(projects_api.projects_service, "remove_member", AsyncMock())
    resp = await client.delete(f"/api/v1/projects/{proj.id}/members/{admin_user.id}")
    assert resp.status_code == 204

    # Repositories
    repo = ProjectRepository(
        id=10,
        project_id=proj.id,
        backend_id="bnd_git123456",
        full_name="org/repo",
        default_branch="main",
        is_primary=True,
        created_at=_now(),
        updated_at=_now(),
    )
    monkeypatch.setattr(
        projects_api.projects_service,
        "list_repositories",
        AsyncMock(return_value=[repo]),
    )
    resp = await client.get(f"/api/v1/projects/{proj.id}/repositories")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        projects_api.projects_service, "add_repository", AsyncMock(return_value=repo)
    )
    resp = await client.post(
        f"/api/v1/projects/{proj.id}/repositories",
        json={"backend_id": "bnd_git123456", "full_name": "org/repo"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        projects_api.projects_service, "update_repository", AsyncMock(return_value=repo)
    )
    resp = await client.patch(
        f"/api/v1/projects/{proj.id}/repositories/10",
        json={"default_branch": "develop"},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(projects_api.projects_service, "remove_repository", AsyncMock())
    resp = await client.delete(f"/api/v1/projects/{proj.id}/repositories/10")
    assert resp.status_code == 204

    # Branches / tree / blob
    monkeypatch.setattr(
        projects_api.git_browse,
        "list_repo_branches",
        AsyncMock(
            return_value={
                "repository_id": 10,
                "full_name": "org/repo",
                "provider": "github",
                "default_branch": "main",
                "web_url": "https://github.com/org/repo",
                "branches": [
                    {
                        "name": "main",
                        "commit_sha": "abc",
                        "is_default": True,
                        "protected": True,
                        "web_url": "https://github.com/org/repo/tree/main",
                    }
                ],
            }
        ),
    )
    resp = await client.get(f"/api/v1/projects/{proj.id}/repositories/10/branches")
    assert resp.status_code == 200
    assert resp.json()["default_branch"] == "main"

    monkeypatch.setattr(
        projects_api.git_browse,
        "get_repo_tree",
        AsyncMock(
            return_value={
                "repository_id": 10,
                "full_name": "org/repo",
                "provider": "github",
                "ref": "main",
                "path": "",
                "web_url": "https://github.com/org/repo/tree/main",
                "entries": [],
                "readme": None,
            }
        ),
    )
    resp = await client.get(f"/api/v1/projects/{proj.id}/repositories/10/tree")
    assert resp.status_code == 200

    monkeypatch.setattr(
        projects_api.git_browse,
        "get_repo_blob",
        AsyncMock(
            return_value={
                "repository_id": 10,
                "full_name": "org/repo",
                "provider": "github",
                "ref": "main",
                "path": "README.md",
                "name": "README.md",
                "size": 12,
                "sha": "abc",
                "is_binary": False,
                "truncated": False,
                "content": "# Hello",
                "web_url": "https://github.com/org/repo/blob/main/README.md",
            }
        ),
    )
    resp = await client.get(
        f"/api/v1/projects/{proj.id}/repositories/10/blob",
        params={"path": "README.md"},
    )
    assert resp.status_code == 200
    assert resp.json()["content"] == "# Hello"

    # Allowed backends
    monkeypatch.setattr(
        projects_api.projects_service,
        "allow_project_backend",
        AsyncMock(return_value=["bnd_1"]),
    )
    resp = await client.post(f"/api/v1/projects/{proj.id}/backends/bnd_1")
    assert resp.status_code == 200

    monkeypatch.setattr(
        projects_api.projects_service,
        "disallow_project_backend",
        AsyncMock(return_value=[]),
    )
    resp = await client.delete(f"/api/v1/projects/{proj.id}/backends/bnd_1")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_seeds_routes(
    client: httpx.AsyncClient,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _make_seed()
    monkeypatch.setattr(
        seeds_api.seeds_service, "list_seeds", AsyncMock(return_value=[seed])
    )
    resp = await client.get("/api/v1/seeds")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        seeds_api.seeds_service, "create_seed", AsyncMock(return_value=seed)
    )
    resp = await client.post(
        "/api/v1/seeds",
        json={"project_id": seed.project_id, "title": "Add API route tests"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        seeds_api.seeds_service, "get_seed", AsyncMock(return_value=seed)
    )
    resp = await client.get(f"/api/v1/seeds/{seed.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        seeds_api.seeds_service, "update_seed", AsyncMock(return_value=seed)
    )
    resp = await client.patch(
        f"/api/v1/seeds/{seed.id}", json={"title": "Updated title"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        seeds_api.seeds_service, "advance_seed", AsyncMock(return_value=seed)
    )
    resp = await client.post(
        f"/api/v1/seeds/{seed.id}/advance", json={"stage": "execute"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        seeds_api.seeds_service, "approve_checkpoint", AsyncMock(return_value=seed)
    )
    resp = await client.post(
        f"/api/v1/seeds/{seed.id}/approve-checkpoint",
        json={"approved_through": "100.00"},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        seeds_api.seeds_service, "close_seed", AsyncMock(return_value=seed)
    )
    resp = await client.post(f"/api/v1/seeds/{seed.id}/close")
    assert resp.status_code == 200

    monkeypatch.setattr(
        seeds_api.seeds_service, "reopen_seed", AsyncMock(return_value=seed)
    )
    resp = await client.post(f"/api/v1/seeds/{seed.id}/reopen")
    assert resp.status_code == 200

    # Approvals
    approval = Approval(
        id="apr_123",
        seed_id=seed.id,
        stage=SeedStage.PLAN,
        snapshot={},
        decision=ApprovalDecision.APPROVED,
        comment="LGTM",
        automatic=False,
        decided_by=admin_user.id,
        approver_role=ProjectRole.MAINTAINER,
        created_at=_now(),
    )
    monkeypatch.setattr(
        seeds_api.seeds_service, "list_approvals", AsyncMock(return_value=[approval])
    )
    resp = await client.get(f"/api/v1/seeds/{seed.id}/approvals")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        seeds_api.seeds_service, "create_approval", AsyncMock(return_value=approval)
    )
    resp = await client.post(
        f"/api/v1/seeds/{seed.id}/approvals",
        json={"decision": "approved", "comment": "LGTM"},
    )
    assert resp.status_code == 201

    # Tasks
    task = SeedTask(
        id="tsk_123",
        seed_id=seed.id,
        position=0,
        title="Task 1",
        description="Do task 1",
        status=TaskStatus.PENDING,
        created_at=_now(),
        updated_at=_now(),
    )
    monkeypatch.setattr(
        seeds_api.seeds_service, "list_tasks", AsyncMock(return_value=[task])
    )
    resp = await client.get(f"/api/v1/seeds/{seed.id}/tasks")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        seeds_api.seeds_service, "create_task", AsyncMock(return_value=task)
    )
    resp = await client.post(
        f"/api/v1/seeds/{seed.id}/tasks",
        json={"title": "Task 1", "description": "Do task 1", "position": 0},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        seeds_api.seeds_service, "update_task", AsyncMock(return_value=task)
    )
    resp = await client.patch(
        f"/api/v1/seeds/{seed.id}/tasks/tsk_123",
        json={"title": "Task 1 edited", "position": 1, "status": "done"},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(seeds_api.seeds_service, "delete_task", AsyncMock())
    resp = await client.delete(f"/api/v1/seeds/{seed.id}/tasks/tsk_123")
    assert resp.status_code == 204

    # Comments (including soft-deleted comment masking body -> "")
    now = _now()
    comment = SeedComment(
        id="cmt_123",
        seed_id=seed.id,
        author_type=ActorType.USER,
        author_id=admin_user.id,
        stage=SeedStage.PLAN,
        body="Looks good",
        created_at=now,
        updated_at=now,
    )
    deleted_comment = SeedComment(
        id="cmt_del",
        seed_id=seed.id,
        author_type=ActorType.USER,
        author_id=admin_user.id,
        stage=SeedStage.PLAN,
        body="Secret text",
        deleted_at=now,
        deleted_by=admin_user.id,
        created_at=now,
        updated_at=now,
    )
    monkeypatch.setattr(
        seeds_api.seeds_service,
        "list_comments",
        AsyncMock(return_value=[comment, deleted_comment]),
    )
    resp = await client.get(f"/api/v1/seeds/{seed.id}/comments")
    assert resp.status_code == 200
    assert resp.json()[0]["body"] == "Looks good"
    assert resp.json()[1]["body"] == ""

    monkeypatch.setattr(
        seeds_api.seeds_service, "create_comment", AsyncMock(return_value=comment)
    )
    resp = await client.post(
        f"/api/v1/seeds/{seed.id}/comments", json={"body": "Looks good"}
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        seeds_api.seeds_service, "update_comment", AsyncMock(return_value=comment)
    )
    resp = await client.patch(
        f"/api/v1/seeds/{seed.id}/comments/cmt_123", json={"body": "Edited body"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        seeds_api.seeds_service,
        "delete_comment",
        AsyncMock(return_value=deleted_comment),
    )
    resp = await client.delete(f"/api/v1/seeds/{seed.id}/comments/cmt_del")
    assert resp.status_code == 200
    assert resp.json()["body"] == ""

    # Seed changes
    monkeypatch.setattr(
        seeds_api.git_browse_service,
        "get_seed_changes",
        AsyncMock(
            return_value={
                "seed_id": seed.id,
                "seed_number": seed.number,
                "repository_id": 10,
                "repository_full_name": "org/repo",
                "provider": "github",
                "base_ref": "main",
                "head_ref": "gitseed/seed-1",
                "comparison_mode": "branch",
                "branch_exists": True,
                "merge_commit_sha": None,
                "head_commit_sha": "abc1234",
                "web_diff_url": "https://github.com/org/repo/compare/main...gitseed/seed-1",
                "total_commits": 1,
                "total_files": 1,
                "total_additions": 10,
                "total_deletions": 2,
                "commits": [],
                "files": [],
            }
        ),
    )
    resp = await client.get(f"/api/v1/seeds/{seed.id}/changes")
    assert resp.status_code == 200
    assert resp.json()["branch_exists"] is True


@pytest.mark.asyncio
async def test_runs_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run = _make_run()
    monkeypatch.setattr(
        runs_api.runs_service, "list_runs", AsyncMock(return_value=[run])
    )
    resp = await client.get("/api/v1/runs")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        runs_api.runs_service, "create_run", AsyncMock(return_value=run)
    )
    resp = await client.post("/api/v1/runs", json={"seed_id": run.seed_id})
    assert resp.status_code == 201

    monkeypatch.setattr(runs_api.runs_service, "get_run", AsyncMock(return_value=run))
    resp = await client.get(f"/api/v1/runs/{run.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        runs_api.runs_service, "update_run", AsyncMock(return_value=run)
    )
    resp = await client.patch(
        f"/api/v1/runs/{run.id}",
        json={"status": "running", "input_tokens": 200, "output": {"log_tail": "ok"}},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        runs_api.runs_service, "cancel_run", AsyncMock(return_value=run)
    )
    resp = await client.post(f"/api/v1/runs/{run.id}/cancel")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_sandboxes_and_runner_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    # Sandboxes
    sbx = _make_sandbox()
    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "get_sandbox_run_config",
        AsyncMock(
            return_value={
                "source": "project_settings",
                "ref": "main",
                "image": "python:3.12-slim",
                "setup_command": "pip install -e .",
                "run_command": "pytest",
                "test_command": "pytest",
                "artifact_globs": ["dist/*"],
            }
        ),
    )
    resp = await client.get("/api/v1/projects/prj_test123456/sandboxes/config")
    assert resp.status_code == 200
    assert resp.json()["run_command"] == "pytest"

    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "list_sandbox_sessions",
        AsyncMock(return_value=[sbx]),
    )
    resp = await client.get("/api/v1/projects/prj_test123456/sandboxes")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "create_sandbox_session",
        AsyncMock(return_value=sbx),
    )
    resp = await client.post(
        "/api/v1/projects/prj_test123456/sandboxes",
        json={"command": "pytest", "image": "python:3.12-slim"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "get_sandbox_session",
        AsyncMock(return_value=sbx),
    )
    resp = await client.get(f"/api/v1/projects/prj_test123456/sandboxes/{sbx.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "send_sandbox_input",
        AsyncMock(return_value=sbx),
    )
    resp = await client.post(
        f"/api/v1/projects/prj_test123456/sandboxes/{sbx.id}/input",
        json={"input": "y", "append_newline": True},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "stop_sandbox_session",
        AsyncMock(return_value=sbx),
    )
    resp = await client.post(f"/api/v1/projects/prj_test123456/sandboxes/{sbx.id}/stop")
    assert resp.status_code == 200

    artifact_file = tmp_path / "report.txt"
    artifact_file.write_text("sandbox report", encoding="utf-8")
    monkeypatch.setattr(
        sandboxes_api.sandboxes_service,
        "resolve_sandbox_artifact_path",
        AsyncMock(return_value=(artifact_file, "report.txt")),
    )
    resp = await client.get(
        f"/api/v1/projects/prj_test123456/sandboxes/{sbx.id}/artifacts/dist/report.txt"
    )
    assert resp.status_code == 200
    assert resp.text == "sandbox report"

    # Runner Bridge
    run_ctx = RunnerContextResponse(
        run_id="run_test123456",
        stage=SeedStage.PLAN,
        attempt=1,
        timeout_minutes=30,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        task_id=None,
        seed_id="sed_test123456",
        seed_number=1,
        project_id="prj_test123456",
        project_slug="alpha-project",
        project_name="Alpha Project",
        title="Seed title",
        description="Desc",
        specification="Spec",
        acceptance_criteria=["AC1"],
        plan=None,
        autonomy=SeedAutonomy.SUPERVISED,
        agent_id="agt_arch123456",
        agent_handle="architect",
        agent_name="Architect",
        agent_instructions="Instructions",
        skills=[],
        tasks=[],
        comments=[],
        approval_feedback=[],
        repositories=[],
        mcp_servers=[],
        sandbox_config={},
        gateway=RunnerGatewayConfig(
            base_url="http://litellm:4000/v1",
            anthropic_base_url="http://litellm:4000",
            api_key="sk-run",
            model="gpt-5.4",
            provider="openai",
        ),
        output_schema={},
    )
    monkeypatch.setattr(
        runner_api.runner_bridge,
        "get_runner_context",
        AsyncMock(return_value=run_ctx),
    )
    resp = await client.get(
        "/api/v1/runner/context", headers={"Authorization": "Bearer gsr_token123"}
    )
    assert resp.status_code == 200
    assert resp.json()["run_id"] == "run_test123456"

    monkeypatch.setattr(
        runner_api.runner_bridge,
        "poll_runner_steering",
        AsyncMock(return_value=RunnerSteeringResponse(should_abort=False)),
    )
    resp = await client.get(
        "/api/v1/runner/steering", headers={"x-run-token": "gsr_token123"}
    )
    assert resp.status_code == 200
    assert resp.json()["should_abort"] is False

    monkeypatch.setattr(
        runner_api.runner_bridge,
        "issue_git_push_token",
        AsyncMock(
            return_value=RunnerGitPushTokenResponse(
                repository_full_name="org/repo",
                push_url="https://x-access-token:tok@github.com/org/repo.git",
                clean_remote_url="https://github.com/org/repo.git",
            )
        ),
    )
    resp = await client.post(
        "/api/v1/runner/git-push-token",
        headers={"Authorization": "Bearer gsr_token123"},
        json={"repository_full_name": "org/repo"},
    )
    assert resp.status_code == 200
    assert resp.json()["repository_full_name"] == "org/repo"

    run = _make_run()
    monkeypatch.setattr(
        runner_api.runner_bridge,
        "complete_runner_execution",
        AsyncMock(return_value=run),
    )
    resp = await client.post(
        "/api/v1/runner/complete",
        headers={"Authorization": "Bearer gsr_token123"},
        json={"status": "succeeded", "exit_code": 0, "stage_output": {}},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == run.id
