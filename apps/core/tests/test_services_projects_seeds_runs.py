from __future__ import annotations

from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    ApprovalDecision,
    BackendType,
    OrgRole,
    ProjectRole,
    ProjectVisibility,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from gitseed_common.schemas.agent import CoordinatorChatMessage, CoordinatorChatRequest
from gitseed_core.models.agent import Agent
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectBackend, ProjectMember
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedTask
from gitseed_core.models.settings import OrgSettings
from gitseed_core.services import (
    coordinator as coordinator_svc,
)
from gitseed_core.services import (
    policies as policies_svc,
)
from gitseed_core.services import (
    projects as projects_svc,
)
from gitseed_core.services import (
    runs as runs_svc,
)
from gitseed_core.services import (
    seeds as seeds_svc,
)
from gitseed_core.services.errors import Invalid

from conftest import _exec_result, _make_session, _make_user, _now


@pytest.mark.asyncio
async def test_projects_service_crud_and_archive() -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    member_user = _make_user("usr_dev", OrgRole.MEMBER)

    assert projects_svc._slugify("A") == "a-project"
    assert projects_svc._validate_slug("valid-slug") == "valid-slug"
    with pytest.raises(Invalid):
        projects_svc._validate_slug("-bad")

    # create_project
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    proj = await projects_svc.create_project(
        session,
        admin,
        name="My Project",
        description="Desc",
        tags=["tag1"],
        visibility=ProjectVisibility.PRIVATE,
        monthly_budget=Decimal("100"),
    )
    assert proj.slug == "my-project"

    # list_projects
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[proj]))
    projs = await projects_svc.list_projects(
        session, member_user, include_archived=False, tag="tag1"
    )
    assert len(projs) == 1

    # get_project & update_project
    session.get = AsyncMock(return_value=proj)
    got_proj = await projects_svc.get_project(session, admin, proj.id)
    assert got_proj.id == proj.id

    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    updated_proj = await projects_svc.update_project(
        session,
        admin,
        proj.id,
        name="Renamed Project",
        slug="renamed-project",
        description="Updated",
        tags=["tag2"],
        visibility=ProjectVisibility.PUBLIC,
        monthly_budget=Decimal("250"),
        seed_budget_cap=Decimal("30"),
        spend_checkpoint=Decimal("60"),
        max_concurrent_runs=3,
        backends_restricted=True,
        sandbox_config={
            "image": "node:22",
            "setup_command": "npm ci",
            "run_command": "npm start",
            "test_command": "npm test",
            "artifact_globs": ["dist/*"],
        },
        fields_set={
            "monthly_budget",
            "seed_budget_cap",
            "spend_checkpoint",
            "max_concurrent_runs",
        },
    )
    assert updated_proj.slug == "renamed-project"
    assert updated_proj.sandbox_config["image"] == "node:22"

    # archive & restore project
    archived = await projects_svc.archive_project(session, admin, proj.id)
    assert archived.archived_at is not None

    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    restored = await projects_svc.restore_project(session, admin, proj.id)
    assert restored.archived_at is None


@pytest.mark.asyncio
async def test_projects_service_members_repositories_and_backends() -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    member_user = _make_user("usr_dev", OrgRole.MEMBER)
    proj = Project(
        id="prj_1",
        name="My Project",
        slug="my-project",
        visibility=ProjectVisibility.PRIVATE,
    )

    # Members CRUD
    pm = ProjectMember(
        id=1,
        project_id=proj.id,
        user_id=member_user.id,
        role=ProjectRole.DEVELOPER,
        created_at=_now(),
        updated_at=_now(),
    )
    session.get = AsyncMock(return_value=proj)
    session.execute = AsyncMock(return_value=_exec_result(all_rows=[(pm, member_user)]))
    members = await projects_svc.list_members(session, admin, proj.id)
    assert len(members) == 1

    session.get = AsyncMock(side_effect=[proj, member_user])
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    added_pm, _ = await projects_svc.add_member(
        session, admin, proj.id, user_id=member_user.id, role=ProjectRole.DEVELOPER
    )
    assert added_pm.role == ProjectRole.DEVELOPER

    session.get = AsyncMock(return_value=proj)
    session.execute = AsyncMock(return_value=_exec_result(first=(pm, member_user)))
    updated_pm, _ = await projects_svc.update_member_role(
        session, admin, proj.id, member_user.id, role=ProjectRole.MAINTAINER
    )
    assert updated_pm.role == ProjectRole.MAINTAINER

    session.scalar = AsyncMock(return_value=2)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=pm))
    await projects_svc.remove_member(session, admin, proj.id, member_user.id)

    # Repositories CRUD
    git_backend = Backend(
        id="bnd_git1",
        name="GH",
        type=BackendType.GIT,
        provider="github",
        enabled=True,
    )
    session.get = AsyncMock(side_effect=[proj, git_backend])
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalar_one_or_none=None),
            _exec_result(scalars_all=[]),
        ]
    )
    repo = await projects_svc.add_repository(
        session,
        admin,
        proj.id,
        backend_id=git_backend.id,
        full_name="org/service",
        default_branch="main",
    )
    assert repo.is_primary is True

    repo.id = 5
    session.get = AsyncMock(side_effect=[proj, repo])
    updated_repo = await projects_svc.update_repository(
        session, admin, proj.id, 5, default_branch="develop"
    )
    assert updated_repo.default_branch == "develop"

    session.get = AsyncMock(side_effect=[proj, repo])
    session.execute = AsyncMock(return_value=_exec_result(scalars_first=None))
    await projects_svc.remove_repository(session, admin, proj.id, 5)

    # Allow / disallow project backend
    session.get = AsyncMock(side_effect=[proj, git_backend, None])
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[git_backend.id]))
    allowed = await projects_svc.allow_project_backend(
        session, admin, proj.id, git_backend.id
    )
    assert allowed == [git_backend.id]

    pb = ProjectBackend(project_id=proj.id, backend_id=git_backend.id)
    session.get = AsyncMock(side_effect=[proj, pb])
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    disallowed = await projects_svc.disallow_project_backend(
        session, admin, proj.id, git_backend.id
    )
    assert disallowed == []


@pytest.mark.asyncio
async def test_seeds_service_lifecycle_and_approvals(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    proj = Project(
        id="prj_1",
        name="Alpha",
        slug="alpha",
        visibility=ProjectVisibility.PUBLIC,
    )
    monkeypatch.setattr(seeds_svc, "get_project", AsyncMock(return_value=proj))
    monkeypatch.setattr(
        seeds_svc,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.MAINTAINER),
    )

    eff_pol = policies_svc.EffectiveStagePolicy(
        id="pol_1",
        project_id=proj.id,
        stage=SeedStage.IDEATE,
        enabled=True,
        agent_id="agt_1",
        requires_approval=True,
        min_approvers=1,
        min_approver_role=ProjectRole.DEVELOPER,
        allow_auto_approve=False,
        allow_self_approval=True,
        max_attempts=3,
        timeout_minutes=30,
    )
    monkeypatch.setattr(
        seeds_svc, "first_enabled_stage", AsyncMock(return_value=SeedStage.IDEATE)
    )
    monkeypatch.setattr(
        seeds_svc, "get_effective_stage_policy", AsyncMock(return_value=eff_pol)
    )
    monkeypatch.setattr(
        seeds_svc, "next_enabled_stage", AsyncMock(return_value=SeedStage.PLAN)
    )

    # create_seed
    session.execute = AsyncMock(return_value=_exec_result(scalar_one=1))
    seed = await seeds_svc.create_seed(
        session,
        admin,
        project_id=proj.id,
        title="Build feature X",
        description="Desc",
        specification="Initial spec",
        acceptance_criteria=["AC1"],
        tags=["feat"],
        budget_cap=Decimal("20.00"),
    )
    assert seed.number == 1
    assert seed.stage == SeedStage.IDEATE

    # get_seed by slug#number and by ID
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=seed))
    got = await seeds_svc.get_seed(session, admin, "alpha#1")
    assert got.id == seed.id

    session.get = AsyncMock(return_value=seed)
    # update_seed in IDEATE
    seed.status = SeedStatus.BLOCKED
    seed.blocked_reason = "Seed budget cap reached"
    seed.spent = Decimal("15.00")
    updated = await seeds_svc.update_seed(
        session,
        admin,
        seed.id,
        title="Build feature X v2",
        specification="Updated spec",
        autonomy=SeedAutonomy.AUTONOMOUS,
        budget_cap=Decimal("50.00"),
        fields_set={"budget_cap"},
    )
    assert updated.status == SeedStatus.QUEUED
    assert updated.autonomy == SeedAutonomy.AUTONOMOUS

    # advance_seed
    advanced = await seeds_svc.advance_seed(session, admin, seed.id)
    assert advanced.stage == SeedStage.PLAN

    # approve_checkpoint
    seed.status = SeedStatus.BLOCKED
    ckpt_seed = await seeds_svc.approve_checkpoint(
        session, admin, seed.id, approved_through=Decimal("100.00")
    )
    assert ckpt_seed.checkpoint_approved_through == Decimal("100.00")
    assert ckpt_seed.status == SeedStatus.QUEUED

    # close_seed & reopen_seed
    closed = await seeds_svc.close_seed(session, admin, seed.id)
    assert closed.status == SeedStatus.CLOSED

    reopened = await seeds_svc.reopen_seed(session, admin, seed.id)
    assert reopened.status == SeedStatus.QUEUED

    # create_approval (APPROVED, CHANGES_REQUESTED on REVIEW, REJECTED, and MERGE)
    seed.stage = SeedStage.REVIEW
    seed.status = SeedStatus.AWAITING_APPROVAL
    rev_run = Run(
        id="run_rev",
        seed_id=seed.id,
        stage=SeedStage.REVIEW,
        attempt=1,
        agent_id="agt_rev",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        status=RunStatus.SUCCEEDED,
        output={"summary_comment": "Fix edge case", "findings": ["Missing null check"]},
    )
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalar_one_or_none=rev_run),
            _exec_result(scalar_one_or_none=None),
        ]
    )
    session.scalar = AsyncMock(return_value=0)
    cr_apr = await seeds_svc.create_approval(
        session,
        admin,
        seed.id,
        decision=ApprovalDecision.CHANGES_REQUESTED,
        comment="Please fix findings",
    )
    assert cr_apr.decision == ApprovalDecision.CHANGES_REQUESTED
    assert seed.stage == SeedStage.EXECUTE

    # Approval on MERGE stage triggers merge_seed_branch_into_default
    seed.stage = SeedStage.MERGE
    seed.status = SeedStatus.AWAITING_APPROVAL
    monkeypatch.setattr(
        seeds_svc,
        "check_seed_pull_request_status",
        AsyncMock(return_value={"merged": False, "ready_to_merge": True}),
    )
    monkeypatch.setattr(
        seeds_svc,
        "merge_seed_branch_into_default",
        AsyncMock(return_value="deadbeef123456"),
    )
    ok_apr_row = Approval(
        id="apr_m",
        seed_id=seed.id,
        stage=SeedStage.MERGE,
        decision=ApprovalDecision.APPROVED,
        decided_by=admin.id,
        approver_role=ProjectRole.MAINTAINER,
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[ok_apr_row]))
    merge_apr = await seeds_svc.create_approval(
        session, admin, seed.id, decision=ApprovalDecision.APPROVED
    )
    assert merge_apr.decision == ApprovalDecision.APPROVED
    assert seed.status == SeedStatus.MERGED


@pytest.mark.asyncio
async def test_seeds_service_tasks_and_comments(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    proj = Project(
        id="prj_1",
        name="Alpha",
        slug="alpha",
        visibility=ProjectVisibility.PUBLIC,
    )
    seed = Seed(
        id="sed_1",
        project_id=proj.id,
        number=1,
        title="Build feature X",
        stage=SeedStage.EXECUTE,
        status=SeedStatus.QUEUED,
        autonomy=SeedAutonomy.SUPERVISED,
    )
    eff_pol = policies_svc.EffectiveStagePolicy(
        id="pol_1",
        project_id=proj.id,
        stage=SeedStage.PLAN,
        enabled=True,
        agent_id="agt_1",
        requires_approval=True,
        min_approvers=1,
        min_approver_role=ProjectRole.DEVELOPER,
        allow_auto_approve=False,
        allow_self_approval=True,
        max_attempts=3,
        timeout_minutes=30,
    )
    monkeypatch.setattr(seeds_svc, "get_project", AsyncMock(return_value=proj))
    monkeypatch.setattr(
        seeds_svc,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.MAINTAINER),
    )
    monkeypatch.setattr(
        seeds_svc, "get_effective_stage_policy", AsyncMock(return_value=eff_pol)
    )

    # Tasks CRUD
    session.get = AsyncMock(return_value=seed)
    session.scalar = AsyncMock(return_value=0)
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    t1 = await seeds_svc.create_task(
        session, admin, seed.id, title="Implement endpoint", position=0
    )
    assert t1.title == "Implement endpoint"
    assert seed.stage == SeedStage.PLAN  # Returned to PLAN gate because tasks modified in EXECUTE

    t2 = SeedTask(id="tsk_2", seed_id=seed.id, position=1, title="Write tests")
    session.get = AsyncMock(side_effect=lambda model, ident: seed if model is Seed else t1)
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[t1, t2]))
    updated_t1 = await seeds_svc.update_task(
        session,
        admin,
        seed.id,
        t1.id,
        title="Implement endpoint v2",
        position=1,
        status=TaskStatus.DONE,
    )
    assert updated_t1.status == TaskStatus.DONE

    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[t2]))
    await seeds_svc.delete_task(session, admin, seed.id, t1.id)

    # Comments CRUD + @agent mention in AWAITING_APPROVAL
    assert seeds_svc.extract_agent_mentions("Hey @Architect and @coder!") == {
        "architect",
        "coder",
    }
    seed.status = SeedStatus.AWAITING_APPROVAL
    seed.stage = SeedStage.PLAN
    session.get = AsyncMock(return_value=seed)
    cmt = await seeds_svc.create_comment(
        session, admin, seed.id, body="@agent please split task 2"
    )
    assert cmt.body == "@agent please split task 2"
    assert seed.status == SeedStatus.QUEUED

    session.get = AsyncMock(
        side_effect=lambda model, ident: seed if model is Seed else cmt
    )
    edited_cmt = await seeds_svc.update_comment(
        session, admin, seed.id, cmt.id, body="Updated comment text"
    )
    assert edited_cmt.edited_at is not None

    deleted_cmt = await seeds_svc.delete_comment(session, admin, seed.id, cmt.id)
    assert deleted_cmt.deleted_at is not None


@pytest.mark.asyncio
async def test_runs_service_comprehensive(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    proj = Project(
        id="prj_1",
        name="Alpha",
        slug="alpha",
        visibility=ProjectVisibility.PUBLIC,
        seed_budget_cap=Decimal("10.00"),
        spend_checkpoint=Decimal("5.00"),
    )
    seed = Seed(
        id="sed_1",
        project_id=proj.id,
        number=1,
        title="Seed 1",
        stage=SeedStage.PLAN,
        status=SeedStatus.QUEUED,
        autonomy=SeedAutonomy.AUTONOMOUS,
        spent=Decimal("0"),
        checkpoint_approved_through=Decimal("0"),
    )
    agent = Agent(
        id="agt_1",
        handle="architect",
        name="Architect",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        enabled=True,
    )
    comp = Backend(
        id="bnd_comp",
        name="Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        enabled=True,
    )

    monkeypatch.setattr(runs_svc, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(runs_svc, "get_project", AsyncMock(return_value=proj))

    eff_pol = policies_svc.EffectiveStagePolicy(
        id="pol_1",
        project_id=proj.id,
        stage=SeedStage.PLAN,
        enabled=True,
        agent_id=agent.id,
        requires_approval=True,
        min_approvers=1,
        min_approver_role=ProjectRole.DEVELOPER,
        allow_auto_approve=True,
        allow_self_approval=True,
        max_attempts=3,
        timeout_minutes=30,
    )
    monkeypatch.setattr(
        runs_svc, "get_effective_stage_policy", AsyncMock(return_value=eff_pol)
    )
    monkeypatch.setattr(
        runs_svc, "next_enabled_stage", AsyncMock(return_value=SeedStage.EXECUTE)
    )

    # create_run
    session.get = AsyncMock(
        side_effect=lambda model, ident: agent if model is Agent else comp
    )
    session.scalar = AsyncMock(return_value=1)
    run = await runs_svc.create_run(
        session,
        admin,
        seed_id=seed.id,
        compute_backend_id=comp.id,
    )
    assert run.attempt == 2
    assert run.status == RunStatus.QUEUED

    # update_run -> RUNNING -> SUCCEEDED + _recompute_seed_spent
    def _get_entity(model: Any, ident: Any) -> Any:
        if model is Run:
            return run
        if model is Backend:
            return comp
        if model is OrgSettings:
            return OrgSettings(id=1)
        return None

    session.get = AsyncMock(side_effect=_get_entity)
    session.scalar = AsyncMock(return_value=Decimal("6.00"))  # Triggers spend_checkpoint
    await runs_svc.update_run(
        session,
        admin,
        run.id,
        status=RunStatus.RUNNING,
        container_id="cid_1",
        fields_set={"container_id"},
    )
    assert run.status == RunStatus.RUNNING

    await runs_svc.update_run(
        session,
        admin,
        run.id,
        status=RunStatus.SUCCEEDED,
        input_tokens=1000,
        output_tokens=500,
        cost=Decimal("6.00"),
        output={"plan_markdown": "# Plan"},
    )
    assert run.status == RunStatus.SUCCEEDED
    assert seed.status == SeedStatus.BLOCKED
    assert "checkpoint" in (seed.blocked_reason or "").lower()

    # cancel_run
    active_run = Run(
        id="run_active",
        seed_id=seed.id,
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id=agent.id,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        compute_backend_id=comp.id,
        container_id="cid_active",
        status=RunStatus.RUNNING,
    )
    session.get = AsyncMock(
        side_effect=lambda model, ident: active_run if model is Run else comp
    )
    monkeypatch.setattr(runs_svc, "finalize_run_litellm_key", AsyncMock())
    monkeypatch.setattr(
        runs_svc, "fetch_docker_container_logs", AsyncMock(return_value="container logs")
    )
    monkeypatch.setattr(runs_svc, "stop_and_remove_docker_container", AsyncMock())
    cancelled = await runs_svc.cancel_run(session, admin, active_run.id)
    assert cancelled.status == RunStatus.CANCELLED
    assert cancelled.output.get("log_tail") == "container logs"


@pytest.mark.asyncio
async def test_coordinator_service_snapshot_and_chat(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    agent = Agent(
        id="agt_coord",
        handle="coordinator",
        name="Coordinator",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        enabled=True,
    )
    models_backend = Backend(
        id="bnd_mod1",
        name="OpenAI",
        type=BackendType.INFERENCE,
        provider="openai",
        config={"models": [{"id": "gpt-5.4", "enabled": True}]},
        enabled=True,
    )
    proj = Project(
        id="prj_1",
        name="Demo",
        slug="demo",
        visibility=ProjectVisibility.PUBLIC,
        backends_restricted=False,
    )
    seed = Seed(
        id="sed_1",
        project_id="prj_1",
        number=1,
        title="Fix bug",
        stage=SeedStage.EXECUTE,
        status=SeedStatus.AWAITING_APPROVAL,
        autonomy=SeedAutonomy.SUPERVISED,
        spent=Decimal("2.50"),
    )
    run = Run(
        id="run_1",
        seed_id="sed_1",
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id=agent.id,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        status=RunStatus.SUCCEEDED,
        input_tokens=500,
        output_tokens=200,
        cost=Decimal("0.02"),
        output={"summary_comment": "Completed implementation"},
    )
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[proj]),
            _exec_result(scalars_all=[seed]),
            _exec_result(scalars_all=[run]),
            _exec_result(scalars_all=[]),
            _exec_result(scalars_all=[]),
        ]
    )
    ctx_md, fallback_reply, resolved_proj = await coordinator_svc._build_workspace_snapshot(
        session, admin, project_id="demo", seed_id="sed_1"
    )
    assert "Live GitSeed Control Plane Context" in ctx_md
    assert "Demo" in fallback_reply
    assert resolved_proj is not None

    # _resolve_coordinator_model
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[models_backend]))
    b_res, m_res = await coordinator_svc._resolve_coordinator_model(
        session, agent, proj
    )
    assert b_res == models_backend
    assert m_res == "gpt-5.4"

    # chat_with_coordinator (LiteLLM 200 OK)
    monkeypatch.setattr(
        coordinator_svc,
        "_resolve_coordinator_agent",
        AsyncMock(return_value=agent),
    )
    monkeypatch.setattr(
        coordinator_svc,
        "_build_workspace_snapshot",
        AsyncMock(return_value=(ctx_md, fallback_reply, proj)),
    )
    monkeypatch.setattr(
        coordinator_svc,
        "_resolve_coordinator_model",
        AsyncMock(return_value=(models_backend, "gpt-5.4")),
    )
    monkeypatch.setattr(coordinator_svc, "sync_backend_to_litellm", AsyncMock())

    mock_http_resp = MagicMock(status_code=200)
    mock_http_resp.json.return_value = {
        "choices": [{"message": {"content": "Coordinator live response"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30},
    }
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(return_value=mock_http_resp)
    monkeypatch.setattr(coordinator_svc.httpx, "AsyncClient", lambda **kw: mock_client)

    chat_res = await coordinator_svc.chat_with_coordinator(
        session,
        admin,
        CoordinatorChatRequest(
            messages=[CoordinatorChatMessage(role="user", content="What is the status?")]
        ),
    )
    assert chat_res.reply == "Coordinator live response"
    assert chat_res.input_tokens == 120
