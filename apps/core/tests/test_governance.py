from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    BackendType,
    OrgRole,
    ProjectRole,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from gitseed_core.models.agent import Agent
from gitseed_core.models.agent_session import AgentSession
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project
from gitseed_core.models.seed import Seed
from gitseed_core.models.skill import Skill
from gitseed_core.models.user import User
from gitseed_core.services import (
    agent_sessions as agent_sessions_service,
)
from gitseed_core.services import (
    backends as backends_service,
)
from gitseed_core.services import (
    projects as projects_service,
)
from gitseed_core.services import (
    runs as runs_service,
)
from gitseed_core.services import (
    seeds as seeds_service,
)
from gitseed_core.services import (
    skills as skills_service,
)
from gitseed_core.services import (
    webhooks as webhooks_service,
)
from gitseed_core.services.errors import Conflict, Forbidden, Invalid

from conftest import _exec_result, _make_policy, _make_run


def _make_user(user_id: str = "usr_dev", org_role: OrgRole = OrgRole.MEMBER) -> User:
    return User(
        id=user_id,
        email=f"{user_id}@gitseed.local",
        name="Test User",
        password_hash="x",
        org_role=org_role,
    )


def _make_project(project_id: str = "prj_api") -> Project:
    return Project(
        id=project_id,
        name="API",
        slug="api",
    )


def _make_seed(
    stage: SeedStage = SeedStage.IDEATE,
    status: SeedStatus = SeedStatus.AWAITING_APPROVAL,
) -> Seed:
    return Seed(
        id="sed_12",
        project_id="prj_api",
        number=12,
        title="Original title",
        description="Original summary",
        specification="Original specification",
        acceptance_criteria=["Criterion 1"],
        stage=stage,
        status=status,
        autonomy=SeedAutonomy.SUPERVISED,
    )


@pytest.mark.asyncio
async def test_update_seed_locks_specification_and_criteria_past_ideate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    user = _make_user()
    project = _make_project()
    seed = _make_seed(stage=SeedStage.PLAN, status=SeedStatus.QUEUED)

    monkeypatch.setattr(seeds_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(
        seeds_service,
        "_require_seed_developer",
        AsyncMock(return_value=(project, ProjectRole.DEVELOPER)),
    )

    # Editing specification past Ideate raises 409 Conflict
    with pytest.raises(Conflict, match="Specification is locked after the Ideate stage"):
        await seeds_service.update_seed(
            session,
            user,
            seed.id,
            specification="Modified specification after approval",
        )

    # Editing acceptance_criteria past Ideate raises 409 Conflict
    with pytest.raises(Conflict, match="Acceptance criteria are locked after the Ideate stage"):
        await seeds_service.update_seed(
            session,
            user,
            seed.id,
            acceptance_criteria=["Criterion 1", "New criterion"],
        )

    # Updating title, description, or passing unchanged specification succeeds
    updated = await seeds_service.update_seed(
        session,
        user,
        seed.id,
        title="Refined title",
        description="Refined summary",
        specification="Original specification",
    )
    assert updated.title == "Refined title"
    assert updated.description == "Refined summary"


@pytest.mark.asyncio
async def test_reopen_seed_requires_maintainer_role(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    developer = _make_user("usr_dev", OrgRole.MEMBER)
    maintainer = _make_user("usr_maint", OrgRole.MEMBER)
    project = _make_project()
    seed = _make_seed(stage=SeedStage.PLAN, status=SeedStatus.CLOSED)

    monkeypatch.setattr(seeds_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(seeds_service, "get_project", AsyncMock(return_value=project))

    # Developer cannot reopen a closed/rejected seed
    monkeypatch.setattr(
        seeds_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.DEVELOPER),
    )
    with pytest.raises(Forbidden, match="Project maintainer privileges required to reopen a seed"):
        await seeds_service.reopen_seed(session, developer, seed.id)

    # Maintainer can reopen a closed seed and records seed.reopened
    monkeypatch.setattr(
        seeds_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.MAINTAINER),
    )
    reopened = await seeds_service.reopen_seed(session, maintainer, seed.id)
    assert reopened.status == SeedStatus.QUEUED


@pytest.mark.asyncio
async def test_archive_skill_restricts_attached_agents_and_restores(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    skill = Skill(
        id="skl_pytest",
        slug="pytest-conventions",
        name="Pytest Conventions",
        description="Pytest guide",
        content="Use fixtures.",
    )
    attached_agent = Agent(
        id="agt_coder",
        handle="coder",
        name="Coder",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.CLAUDE_CODE,
        model="default",
        skills=["pytest-conventions"],
    )

    class _FakeScalarResult:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def all(self) -> list[Any]:
            return self._items

    class _FakeExecResult:
        def __init__(self, items: list[Any]) -> None:
            self._items = items

        def scalars(self) -> _FakeScalarResult:
            return _FakeScalarResult(self._items)

    session = AsyncMock()
    monkeypatch.setattr(skills_service, "get_skill", AsyncMock(return_value=skill))

    # When an active agent references the skill, archive_skill raises 409 Conflict
    session.execute = AsyncMock(return_value=_FakeExecResult([attached_agent]))
    with pytest.raises(
        Conflict,
        match="Cannot archive skill 'pytest-conventions' while attached to agent\\(s\\): @coder",
    ):
        await skills_service.archive_skill(session, admin, skill.slug)

    # When no active agent references the skill, archive_skill sets archived_at
    session.execute = AsyncMock(return_value=_FakeExecResult([]))
    archived = await skills_service.archive_skill(session, admin, skill.slug)
    assert archived.archived_at is not None
    assert archived.archived_by == admin.id

    # Restore clears archived_at and archived_by
    monkeypatch.setattr(
        skills_service, "_ensure_active_slug_available", AsyncMock(return_value=None)
    )
    restored = await skills_service.restore_skill(session, skill.slug)
    assert restored.archived_at is None
    assert restored.archived_by is None


@pytest.mark.asyncio
async def test_rbac_scoping_and_gate_protections(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    developer = _make_user("usr_dev", OrgRole.MEMBER)
    viewer = _make_user("usr_view", OrgRole.MEMBER)
    project = _make_project()
    seed = _make_seed(stage=SeedStage.PLAN, status=SeedStatus.AWAITING_APPROVAL)

    monkeypatch.setattr(seeds_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(seeds_service, "get_project", AsyncMock(return_value=project))

    # 1. Org admin always resolves to ProjectRole.MAINTAINER even if listed as developer
    monkeypatch.setattr(
        seeds_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.DEVELOPER),
    )
    _, effective_role = await seeds_service._require_seed_developer(
        session, admin, seed
    )
    assert effective_role == ProjectRole.MAINTAINER

    # 2. Developer cannot bypass AWAITING_APPROVAL gate via advance_seed
    pol = _make_policy(stage=SeedStage.PLAN, project_id=project.id)
    monkeypatch.setattr(
        seeds_service,
        "next_enabled_stage",
        AsyncMock(return_value=SeedStage.EXECUTE),
    )
    monkeypatch.setattr(
        seeds_service,
        "get_effective_stage_policy",
        AsyncMock(return_value=pol),
    )
    with pytest.raises(Forbidden, match="stage gate approval"):
        await seeds_service.advance_seed(session, developer, seed.id)

    # 3. Developer cannot manually mark a gated or merge run SUCCEEDED via update_run
    run = _make_run(seed_id=seed.id)
    run.status = RunStatus.RUNNING
    monkeypatch.setattr(runs_service, "get_run", AsyncMock(return_value=run))
    monkeypatch.setattr(runs_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(runs_service, "get_project", AsyncMock(return_value=project))
    monkeypatch.setattr(
        runs_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.DEVELOPER),
    )
    monkeypatch.setattr(
        runs_service,
        "get_effective_stage_policy",
        AsyncMock(return_value=pol),
    )
    with pytest.raises(
        Forbidden,
        match="Project maintainer privileges required to manually mark a gated or merge run as succeeded",
    ):
        await runs_service.update_run(
            session, developer, run.id, status=RunStatus.SUCCEEDED
        )

    # 4. Non-maintainer cannot call discover_backend_repositories or create_remote_repository
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    with pytest.raises(
        Forbidden,
        match="Organization admin or project maintainer privileges required",
    ):
        await backends_service.discover_backend_repositories(
            session, "bnd_git1", actor=developer
        )
    with pytest.raises(
        Forbidden,
        match="Organization admin or project maintainer privileges required",
    ):
        await backends_service.create_remote_repository(
            session, "bnd_git1", actor=viewer, name="org/repo"
        )

    # 5. Webhook config endpoint requires project maintainer
    monkeypatch.setattr(
        webhooks_service,
        "require_project_maintainer",
        AsyncMock(side_effect=Forbidden("Project maintainer required")),
    )
    with pytest.raises(Forbidden, match="Project maintainer required"):
        await webhooks_service.get_project_webhook_config(
            session, developer, project.id
        )

    # 6. Read-only viewer cannot wake a sleeping agent session container via prepare_agent_pty
    ag_sess = AgentSession(
        id="ses_1",
        project_id=project.id,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="default",
        container_id=None,
        compute_backend_id=None,
        created_by=developer.id,
    )
    agent = Agent(
        id="agt_1",
        handle="coder",
        name="Coder",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="default",
    )
    monkeypatch.setattr(
        agent_sessions_service,
        "_require_project_access",
        AsyncMock(return_value=project),
    )
    monkeypatch.setattr(
        agent_sessions_service.projects_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.VIEWER),
    )
    session.get = AsyncMock(
        side_effect=lambda model, ident: ag_sess if model is AgentSession else agent
    )
    with pytest.raises(
        Forbidden,
        match="Read-only viewers can only watch an already active terminal session",
    ):
        await agent_sessions_service.prepare_agent_pty(
            session, viewer, project.id, ag_sess.id
        )

    # 7. Enforces project.backends_restricted in add_repository
    restricted_proj = _make_project("prj_rest")
    restricted_proj.backends_restricted = True
    git_b = Backend(
        id="bnd_git_unallowed",
        name="GitHub",
        type=BackendType.GIT,
        provider="github",
        enabled=True,
    )
    monkeypatch.setattr(
        projects_service,
        "require_project_maintainer",
        AsyncMock(return_value=restricted_proj),
    )
    monkeypatch.setattr(
        projects_service,
        "get_allowed_backend_ids",
        AsyncMock(return_value=["bnd_git_allowed"]),
    )
    session.get = AsyncMock(return_value=git_b)
    with pytest.raises(
        Forbidden,
        match="This Git backend is not in the project's allowed backends list",
    ):
        await projects_service.add_repository(
            session,
            admin,
            restricted_proj.id,
            backend_id=git_b.id,
            full_name="org/repo",
        )


@pytest.mark.asyncio
async def test_second_opinion_and_select_run_and_approval_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    session.add = MagicMock()
    maintainer = _make_user("usr_maint", OrgRole.ADMIN)
    project = _make_project()
    seed = _make_seed(stage=SeedStage.IDEATE, status=SeedStatus.AWAITING_APPROVAL)

    monkeypatch.setattr(seeds_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(
        seeds_service,
        "_require_seed_developer",
        AsyncMock(return_value=(project, ProjectRole.MAINTAINER)),
    )

    # 1. Second opinions are rejected on Execute stage
    seed.stage = SeedStage.EXECUTE
    with pytest.raises(Invalid, match="Second opinions are only supported in the Ideate, Plan, and Review stages"):
        await seeds_service.request_second_opinion(
            session, maintainer, seed.id, agent_id="agt_2"
        )

    # 2. Enqueue a second opinion during Ideate gate
    seed.stage = SeedStage.IDEATE
    pol = _make_policy(stage=SeedStage.IDEATE, project_id=project.id)
    monkeypatch.setattr(
        seeds_service, "get_effective_stage_policy", AsyncMock(return_value=pol)
    )
    run1 = _make_run(run_id="run_1", seed_id=seed.id)
    run1.stage = SeedStage.IDEATE
    run1.status = RunStatus.SUCCEEDED
    run1.agent_id = "agt_1"
    run1.output = {
        "specification": "Spec from agt_1",
        "acceptance_criteria": ["AC 1"],
    }
    monkeypatch.setattr(
        seeds_service, "list_stage_cycle_runs", AsyncMock(return_value=[run1])
    )
    agent1 = Agent(
        id="agt_1",
        handle="ideator",
        name="Ideator",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="default",
        enabled=True,
    )
    agent2 = Agent(
        id="agt_2",
        handle="claude-arch",
        name="Claude Architect",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.CLAUDE_CODE,
        model="default",
        enabled=True,
    )
    session.get = AsyncMock(
        side_effect=lambda model, ident: agent1 if ident == "agt_1" else agent2
    )

    # Rejects duplicate agent in same stage cycle
    with pytest.raises(Conflict, match="has already run or is queued for this stage"):
        await seeds_service.request_second_opinion(
            session, maintainer, seed.id, agent_id="agt_1"
        )

    # Enqueues second opinion for agt_2 and moves seed back to QUEUED for dispatch
    enqueued_run = await seeds_service.request_second_opinion(
        session, maintainer, seed.id, agent_id="agt_2"
    )
    assert enqueued_run.agent_id == "agt_2"
    assert enqueued_run.output.get("second_opinion") is True
    assert seed.status == SeedStatus.QUEUED

    # 3. Switching active draft via select_seed_run and restoring original user draft
    seed.status = SeedStatus.AWAITING_APPROVAL
    run2 = _make_run(run_id="run_2", seed_id=seed.id)
    run2.stage = SeedStage.IDEATE
    run2.status = RunStatus.SUCCEEDED
    run2.agent_id = "agt_2"
    run2.output = {
        "specification": "Spec from agt_2",
        "acceptance_criteria": ["AC 2A", "AC 2B"],
    }
    monkeypatch.setattr(
        seeds_service, "list_stage_cycle_runs", AsyncMock(return_value=[run1, run2])
    )
    session.get = AsyncMock(return_value=run2)

    await seeds_service.select_seed_run(session, maintainer, seed.id, "run_2")
    assert seed.specification == "Spec from agt_2"
    assert seed.acceptance_criteria == ["AC 2A", "AC 2B"]
    assert run1.output.get("previous_specification") == "Original specification"

    # Switch back to "original" (Your draft)
    await seeds_service.select_seed_run(session, maintainer, seed.id, "original")
    assert seed.specification == "Original specification"
    assert seed.acceptance_criteria == ["Criterion 1"]

    # Switch to run_2 and verify approval snapshot records selected & unselected runs
    await seeds_service.select_seed_run(session, maintainer, seed.id, "run_2")
    snap = await seeds_service.build_stage_approval_snapshot(session, seed)
    assert snap["selected_run_id"] == "run_2"
    assert snap["selected_agent_id"] == "agt_2"
    assert snap["unselected_runs"] == [
        {"run_id": "run_1", "agent_id": "agt_1", "status": "succeeded"}
    ]

    # 4. Review approval snapshot records all reviewer verdicts together
    seed.stage = SeedStage.REVIEW
    rev1 = _make_run(run_id="run_rev1", seed_id=seed.id)
    rev1.stage = SeedStage.REVIEW
    rev1.status = RunStatus.SUCCEEDED
    rev1.agent_id = "agt_rev1"
    rev1.output = {"approved": True, "summary_comment": "LGTM", "findings": []}
    rev2 = _make_run(run_id="run_rev2", seed_id=seed.id)
    rev2.stage = SeedStage.REVIEW
    rev2.status = RunStatus.SUCCEEDED
    rev2.agent_id = "agt_rev2"
    rev2.output = {
        "approved": False,
        "summary_comment": "Needs fix",
        "findings": [{"severity": "error", "message": "Missing check"}],
    }
    monkeypatch.setattr(
        seeds_service, "list_stage_cycle_runs", AsyncMock(return_value=[rev1, rev2])
    )
    rev_snap = await seeds_service.build_stage_approval_snapshot(session, seed)
    assert len(rev_snap["reviews"]) == 2
    assert rev_snap["reviews"][0]["run_id"] == "run_rev1"
    assert rev_snap["reviews"][0]["approved"] is True
    assert rev_snap["reviews"][1]["run_id"] == "run_rev2"
    assert rev_snap["reviews"][1]["approved"] is False


@pytest.mark.asyncio
async def test_fast_track_seed_creation_and_midflight_advance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gitseed_core.models.seed import SeedTask
    from gitseed_core.services.policies import DEFAULT_ORG_STAGE_POLICIES

    # 1. Autonomous mode policies allow auto-approve through Ideate, Plan, and Review, pausing at Merge
    assert DEFAULT_ORG_STAGE_POLICIES[SeedStage.IDEATE]["allow_auto_approve"] is True
    assert DEFAULT_ORG_STAGE_POLICIES[SeedStage.PLAN]["allow_auto_approve"] is True
    assert DEFAULT_ORG_STAGE_POLICIES[SeedStage.REVIEW]["allow_auto_approve"] is True
    assert DEFAULT_ORG_STAGE_POLICIES[SeedStage.MERGE]["allow_auto_approve"] is False

    # 2. create_seed with fast_track=True starts at EXECUTE, backfills specification, and creates SeedTask
    session = AsyncMock()
    added_objs: list[Any] = []
    session.add = MagicMock(side_effect=lambda obj: added_objs.append(obj))
    session.execute = AsyncMock(return_value=_exec_result(scalar_one=7))
    developer = _make_user("usr_dev", OrgRole.MEMBER)
    project = _make_project()
    exec_pol = _make_policy(stage=SeedStage.EXECUTE, project_id=project.id)

    monkeypatch.setattr(
        seeds_service,
        "_require_seed_developer",
        AsyncMock(return_value=(project, ProjectRole.DEVELOPER)),
    )
    monkeypatch.setattr(
        seeds_service, "get_effective_stage_policy", AsyncMock(return_value=exec_pol)
    )
    monkeypatch.setattr(
        seeds_service, "resolve_should_stage_seed", AsyncMock(return_value=False)
    )

    created = await seeds_service.create_seed(
        session,
        developer,
        project_id=project.id,
        title="Fix login timeout",
        description="Increase OAuth callback timeout from 5s to 15s.",
        fast_track=True,
    )
    assert created.stage == SeedStage.EXECUTE
    assert created.specification == "Increase OAuth callback timeout from 5s to 15s."
    created_tasks = [o for o in added_objs if isinstance(o, SeedTask)]
    assert len(created_tasks) == 1
    assert created_tasks[0].title == "Fix login timeout"

    # 3. advance_seed with fast_track=True jumps from IDEATE (awaiting_approval) to EXECUTE for developer
    added_objs.clear()
    ideate_seed = _make_seed(stage=SeedStage.IDEATE, status=SeedStatus.AWAITING_APPROVAL)
    ideate_seed.created_by = developer.id
    ideate_seed.specification = ""
    ideate_seed.description = "Add /healthz readiness check"
    ideate_pol = _make_policy(stage=SeedStage.IDEATE, project_id=project.id)
    plan_pol = _make_policy(stage=SeedStage.PLAN, project_id=project.id)

    async def _fake_policy(_sess: Any, _pid: str, st: SeedStage) -> Any:
        return {
            SeedStage.IDEATE: ideate_pol,
            SeedStage.PLAN: plan_pol,
            SeedStage.EXECUTE: exec_pol,
        }[st]

    monkeypatch.setattr(seeds_service, "get_seed", AsyncMock(return_value=ideate_seed))
    monkeypatch.setattr(seeds_service, "get_effective_stage_policy", _fake_policy)
    session.scalar = AsyncMock(return_value=0)

    advanced = await seeds_service.advance_seed(
        session, developer, ideate_seed.id, fast_track=True
    )
    assert advanced.stage == SeedStage.EXECUTE
    assert advanced.status == SeedStatus.QUEUED
    assert advanced.specification == "Add /healthz readiness check"
    midflight_tasks = [o for o in added_objs if isinstance(o, SeedTask)]
    assert len(midflight_tasks) == 1


def test_cli_oauth_bundle_health_check() -> None:
    import json
    import time

    from gitseed_common.enums import BackendHealth
    from gitseed_core.services.backends import _check_cli_oauth_bundle_health

    expired_ms = int((time.time() - 3600) * 1000)

    # Missing credentials -> DEGRADED
    health, err = _check_cli_oauth_bundle_health({})
    assert health == BackendHealth.DEGRADED
    assert err is not None and "CLI login required" in err

    # Expired access token WITHOUT refresh token -> DEGRADED
    expired_no_refresh = json.dumps(
        {
            ".claude/.credentials.json": json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat01-expired",
                        "expiresAt": expired_ms,
                    }
                }
            )
        }
    )
    health, err = _check_cli_oauth_bundle_health({"cli_auth_bundle": expired_no_refresh})
    assert health == BackendHealth.DEGRADED
    assert err is not None and "expired" in err

    # Expired access token WITH refresh token -> HEALTHY (CLI can auto-refresh)
    expired_with_refresh = json.dumps(
        {
            ".claude/.credentials.json": json.dumps(
                {
                    "claudeAiOauth": {
                        "accessToken": "sk-ant-oat01-expired",
                        "refreshToken": "sk-ant-ort01-valid-refresh",
                        "expiresAt": expired_ms,
                    }
                }
            )
        }
    )
    health, err = _check_cli_oauth_bundle_health({"cli_auth_bundle": expired_with_refresh})
    assert health == BackendHealth.HEALTHY
    assert err is None




