from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    BackendHealth,
    BackendType,
    OrgRole,
    ProjectRole,
    ProjectVisibility,
    SeedStage,
)
from gitseed_core.models.agent import Agent, AgentBackend
from gitseed_core.models.backend import Backend
from gitseed_core.models.policy import Policy
from gitseed_core.models.project import Project
from gitseed_core.security import encrypt_secrets
from gitseed_core.services import (
    agents as agents_svc,
)
from gitseed_core.services import (
    backends as backends_svc,
)
from gitseed_core.services import (
    policies as policies_svc,
)
from gitseed_core.services import (
    skills as skills_svc,
)
from gitseed_core.services.errors import Conflict, Invalid
from sqlalchemy.exc import IntegrityError

from conftest import _exec_result, _make_session, _make_user


@pytest.mark.asyncio
async def test_agents_and_skills_services() -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)

    # agents.py
    assert agents_svc._slugify_handle("123") == "agent-123"
    assert (
        agents_svc.get_runtime_model_compatibility_warning(
            AgentRuntime.CLAUDE_CODE, "ollama", "qwen"
        )
        is not None
    )
    assert (
        agents_svc.get_runtime_model_compatibility_warning(
            AgentRuntime.OPENCODE, "openai", "text-embedding-3"
        )
        is not None
    )

    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    agent = await agents_svc.create_agent(
        session,
        admin,
        name="Security Auditor",
        handle="sec-auditor",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
    )
    assert agent.handle == "sec-auditor"

    session.get = AsyncMock(return_value=agent)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    updated_agent = await agents_svc.update_agent(
        session,
        agent.id,
        name="Sec Auditor V2",
        handle="sec-auditor-v2",
        instructions="Audit code",
        enabled=True,
        fields_set={"template"},
        template="reviewer",
    )
    assert updated_agent.handle == "sec-auditor-v2"

    session.scalar = AsyncMock(return_value=0)
    archived_agent = await agents_svc.archive_agent(session, admin, agent.id)
    assert archived_agent.archived_at is not None

    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    restored_agent = await agents_svc.restore_agent(session, agent.id)
    assert restored_agent.archived_at is None

    # compute_agent_compatibility_warning
    models_backend = Backend(
        id="bnd_mod1",
        name="OpenAI",
        type=BackendType.INFERENCE,
        provider="openai",
        config={"models": [{"id": "gpt-5.4", "enabled": True}]},
        enabled=True,
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[models_backend]))
    warn = await agents_svc.compute_agent_compatibility_warning(session, agent)
    assert warn is None

    # skills.py
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    skill = await skills_svc.create_skill(
        session, admin, name="OWASP Playbook", content="# OWASP"
    )
    assert skill.slug == "owasp-playbook"

    session.get = AsyncMock(return_value=skill)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    await skills_svc.update_skill(
        session, skill.id, name="OWASP V2", slug="owasp-v2", content="# Updated"
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    await skills_svc.archive_skill(session, admin, skill.id)
    assert skill.archived_at is not None
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    await skills_svc.restore_skill(session, skill.id)
    assert skill.archived_at is None


@pytest.mark.asyncio
async def test_policies_service_comprehensive(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    proj = Project(
        id="prj_1",
        name="Alpha",
        slug="alpha",
        visibility=ProjectVisibility.PUBLIC,
    )

    # ensure_org_policies creates all 5 stage policies when empty
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    org_map = await policies_svc.ensure_org_policies(session, commit_if_created=True)
    assert len(org_map) == 5
    assert SeedStage.IDEATE in org_map

    # list_org_policies
    session.execute = AsyncMock(
        return_value=_exec_result(scalars_all=list(org_map.values()))
    )
    org_list = await policies_svc.list_org_policies(session)
    assert len(org_list) == 5

    # update_org_policy validation errors & success
    with pytest.raises(Invalid):
        await policies_svc.update_org_policy(
            session, admin, SeedStage.EXECUTE, enabled=False
        )
    with pytest.raises(Invalid):
        await policies_svc.update_org_policy(
            session, admin, SeedStage.PLAN, min_approver_role=ProjectRole.VIEWER
        )

    stage_agent = Agent(
        id="agt_stage1",
        handle="architect",
        name="Architect",
        kind=AgentKind.STAGE,
    )
    session.get = AsyncMock(return_value=stage_agent)
    session.execute = AsyncMock(
        return_value=_exec_result(scalars_all=list(org_map.values()))
    )
    updated_pol = await policies_svc.update_org_policy(
        session,
        admin,
        SeedStage.PLAN,
        enabled=True,
        agent_id=stage_agent.id,
        requires_approval=True,
        min_approvers=2,
        min_approver_role=ProjectRole.MAINTAINER,
        allow_auto_approve=False,
        allow_self_approval=False,
        max_attempts=4,
        timeout_minutes=45,
        fields_set={"agent_id"},
    )
    assert updated_pol.min_approvers == 2
    assert updated_pol.agent_id == stage_agent.id

    # update_project_policy & reset_project_policy
    monkeypatch.setattr(
        policies_svc, "require_project_maintainer", AsyncMock(return_value=proj)
    )
    monkeypatch.setattr(policies_svc, "get_project", AsyncMock(return_value=proj))
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=list(org_map.values())),
            _exec_result(scalar_one_or_none=None),
        ]
    )
    proj_eff = await policies_svc.update_project_policy(
        session,
        admin,
        proj.id,
        SeedStage.PLAN,
        requires_approval=False,
        timeout_minutes=60,
        fields_set={"requires_approval", "timeout_minutes"},
    )
    assert proj_eff.requires_approval is False
    assert "requires_approval" in proj_eff.overridden_fields

    proj_override_row = Policy(
        id="pol_proj1",
        project_id=proj.id,
        stage=SeedStage.PLAN,
        requires_approval=False,
    )
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=list(org_map.values())),
            _exec_result(scalar_one_or_none=proj_override_row),
        ]
    )
    reset_eff = await policies_svc.reset_project_policy(
        session, admin, proj.id, SeedStage.PLAN
    )
    assert reset_eff.overridden_fields == []


@pytest.mark.asyncio
async def test_backends_service_validation_and_crud(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)

    # _validate_provider_and_secrets
    with pytest.raises(Invalid):
        backends_svc._validate_provider_and_secrets(
            BackendType.GIT, "unknown-provider", {}
        )
    with pytest.raises(Invalid):
        backends_svc._validate_provider_and_secrets(
            BackendType.GIT, "github", {"bad_key": "123"}
        )
    with pytest.raises(Invalid):
        backends_svc._validate_provider_and_secrets(BackendType.GIT, "github", {})

    cleaned = backends_svc._validate_provider_and_secrets(
        BackendType.GIT, "github", {"token": " ghp_abc "}
    )
    assert cleaned == {"token": "ghp_abc"}

    # _merge_discovered_repos & _merge_discovered_models
    merged_repos = backends_svc._merge_discovered_repos(
        [{"full_name": "org/old", "enabled": False}],
        [
            {"full_name": "org/old", "default_branch": "main", "private": True},
            {"full_name": "org/new", "default_branch": "develop", "private": False},
        ],
    )
    assert len(merged_repos) == 2

    merged_models = backends_svc._merge_discovered_models(
        [{"id": "gpt-5.4", "enabled": False}],
        ["legacy-model"],
        [{"id": "gpt-5.4", "owned_by": "openai"}, {"id": "text-embedding-3", "owned_by": "openai"}],
    )
    assert any(m["id"] == "gpt-5.4" and m["enabled"] is False for m in merged_models)
    assert any(m["id"] == "text-embedding-3" and m["enabled"] is False for m in merged_models)

    # create_backend & update_backend & delete_backend
    monkeypatch.setattr(backends_svc, "sync_backend_to_litellm", AsyncMock())
    monkeypatch.setattr(backends_svc, "remove_backend_from_litellm", AsyncMock())
    monkeypatch.setattr(backends_svc, "maybe_auto_provision_sdlc_agents", AsyncMock())
    monkeypatch.setattr(
        backends_svc,
        "_fetch_backend_models",
        AsyncMock(return_value=[{"id": "gpt-5.4", "owned_by": "openai"}]),
    )
    b_models = await backends_svc.create_backend(
        session,
        admin,
        name="OpenAI Prod",
        type=BackendType.INFERENCE,
        provider="openai",
        secrets={"api_key": "sk-test123"},
    )
    assert b_models.health == BackendHealth.HEALTHY
    assert backends_svc.get_secret_keys(b_models) == ["api_key"]

    session.get = AsyncMock(return_value=b_models)
    updated_b = await backends_svc.update_backend(
        session,
        admin,
        b_models.id,
        name="OpenAI Prod V2",
        secrets={"api_key": "sk-rotated"},
        capacity=10,
        fields_set={"capacity"},
    )
    assert updated_b.name == "OpenAI Prod V2"

    # delete_backend conflict on IntegrityError
    session.commit = AsyncMock(side_effect=IntegrityError("stmt", {}, Exception()))
    with pytest.raises(Conflict):
        await backends_svc.delete_backend(session, b_models.id)
    session.commit = AsyncMock()
    await backends_svc.delete_backend(session, b_models.id)

    # grant_agent_access & revoke_agent_access
    agent = Agent(id="agt_1", handle="coder", name="Coder")
    session.get = AsyncMock(side_effect=[b_models, agent, None])
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[agent.id]))
    granted = await backends_svc.grant_agent_access(
        session, admin, b_models.id, agent.id
    )
    assert granted == [agent.id]

    ab = AgentBackend(backend_id=b_models.id, agent_id=agent.id)
    session.get = AsyncMock(side_effect=[b_models, ab])
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    revoked = await backends_svc.revoke_agent_access(
        session, admin, b_models.id, agent.id
    )
    assert revoked == []


@pytest.mark.asyncio
async def test_backends_service_probing_and_discovery(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    monkeypatch.setattr(backends_svc, "sync_backend_to_litellm", AsyncMock())
    monkeypatch.setattr(
        backends_svc,
        "_fetch_backend_models",
        AsyncMock(return_value=[{"id": "gpt-5.4", "owned_by": "openai"}]),
    )

    mock_resp = MagicMock(status_code=200)
    mock_resp.json.return_value = {"data": [{"id": "gpt-5.4", "owned_by": "openai"}]}
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get = AsyncMock(return_value=mock_resp)
    mock_client.post = AsyncMock(
        return_value=MagicMock(
            status_code=201,
            json=lambda: {
                "full_name": "org/created",
                "default_branch": "main",
                "private": True,
                "html_url": "https://github.com/org/created",
            },
        )
    )
    monkeypatch.setattr(backends_svc.httpx, "AsyncClient", lambda **kw: mock_client)

    h, _lat, err = await backends_svc.probe_backend_config(
        type=BackendType.COMPUTE,
        provider="kubernetes",
        endpoint="https://k8s.local",
        secrets={"token": "k8s-tok"},
    )
    assert h == BackendHealth.HEALTHY and err is None

    h2, _, _ = await backends_svc.probe_backend_config(
        type=BackendType.INFERENCE,
        provider="anthropic",
        secrets={"api_key": "sk-ant"},
    )
    assert h2 == BackendHealth.HEALTHY

    h3, _, _ = await backends_svc.probe_backend_config(
        type=BackendType.GIT,
        provider="gitlab",
        secrets={"token": "glpat-123"},
    )
    assert h3 == BackendHealth.HEALTHY

    # create_remote_repository
    git_b = Backend(
        id="bnd_git",
        name="GitHub",
        type=BackendType.GIT,
        provider="github",
        secrets_encrypted=encrypt_secrets({"token": "ghp_123"}),
        config={},
    )
    session.get = AsyncMock(return_value=git_b)
    _, created_info = await backends_svc.create_remote_repository(
        session, git_b.id, name="org/created", description="Test repo"
    )
    assert created_info["full_name"] == "org/created"

    # test_backend_connection & discover_backend_models & discover_backend_repositories
    monkeypatch.setattr(
        backends_svc,
        "_fetch_git_repositories",
        AsyncMock(return_value=[{"full_name": "org/created", "default_branch": "main"}]),
    )
    tested = await backends_svc.test_backend_connection(session, git_b.id)
    assert tested.health == BackendHealth.HEALTHY

    disc_git = await backends_svc.discover_backend_repositories(session, git_b.id)
    assert len(disc_git.config["repositories"]) >= 1

    b_models = Backend(
        id="bnd_models",
        name="OpenAI Prod",
        type=BackendType.INFERENCE,
        provider="openai",
        secrets_encrypted=encrypt_secrets({"api_key": "sk-test123"}),
        config={},
    )
    session.get = AsyncMock(return_value=b_models)
    disc_mod = await backends_svc.discover_backend_models(session, b_models.id)
    assert len(disc_mod.config["models"]) >= 1
