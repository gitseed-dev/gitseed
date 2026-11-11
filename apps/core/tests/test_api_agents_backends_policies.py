from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock

import httpx
import pytest
from gitseed_common.enums import BackendHealth
from gitseed_common.schemas.agent import CoordinatorChatResponse
from gitseed_core.api.v1 import (
    agents as agents_api,
)
from gitseed_core.api.v1 import (
    backends as backends_api,
)
from gitseed_core.api.v1 import (
    policies as policies_api,
)
from gitseed_core.api.v1 import (
    skills as skills_api,
)
from gitseed_core.services.agents import AgentMetrics

from conftest import _make_agent, _make_backend, _make_policy, _make_skill


@pytest.mark.asyncio
async def test_agents_and_skills_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent = _make_agent()
    monkeypatch.setattr(
        agents_api.agents_service, "list_agents", AsyncMock(return_value=[agent])
    )
    monkeypatch.setattr(
        agents_api.agents_service,
        "get_agent_metrics",
        AsyncMock(
            return_value={
                agent.id: AgentMetrics(
                    run_count=2, succeeded_runs=2, total_cost=Decimal("0.5")
                )
            }
        ),
    )
    monkeypatch.setattr(
        agents_api.agents_service,
        "get_agent_allowed_backend_ids_map",
        AsyncMock(return_value={agent.id: ["bnd_1"]}),
    )
    monkeypatch.setattr(
        agents_api.agents_service,
        "get_agent_allowed_backend_ids",
        AsyncMock(return_value=["bnd_1"]),
    )
    monkeypatch.setattr(
        agents_api.agents_service,
        "compute_agent_compatibility_warning",
        AsyncMock(return_value=None),
    )

    resp = await client.get("/api/v1/agents")
    assert resp.status_code == 200
    assert resp.json()[0]["run_count"] == 2

    monkeypatch.setattr(
        agents_api.agents_service, "create_agent", AsyncMock(return_value=agent)
    )
    resp = await client.post(
        "/api/v1/agents",
        json={"name": "Architect", "handle": "architect", "kind": "stage"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        agents_api.coordinator_service,
        "chat_with_coordinator",
        AsyncMock(
            return_value=CoordinatorChatResponse(
                reply="All systems nominal.",
                agent_handle="coordinator",
                model="gpt-5.4",
                input_tokens=20,
                output_tokens=10,
            )
        ),
    )
    resp = await client.post(
        "/api/v1/agents/coordinator/chat",
        json={"messages": [{"role": "user", "content": "Status?"}]},
    )
    assert resp.status_code == 200
    assert resp.json()["reply"] == "All systems nominal."

    monkeypatch.setattr(
        agents_api.agents_service, "get_agent", AsyncMock(return_value=agent)
    )
    resp = await client.get(f"/api/v1/agents/{agent.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        agents_api.agents_service, "update_agent", AsyncMock(return_value=agent)
    )
    resp = await client.patch(
        f"/api/v1/agents/{agent.id}", json={"instructions": "Updated"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        agents_api.agents_service, "archive_agent", AsyncMock(return_value=agent)
    )
    resp = await client.post(f"/api/v1/agents/{agent.id}/archive")
    assert resp.status_code == 200

    monkeypatch.setattr(
        agents_api.agents_service, "restore_agent", AsyncMock(return_value=agent)
    )
    resp = await client.post(f"/api/v1/agents/{agent.id}/restore")
    assert resp.status_code == 200

    # Skills
    skill = _make_skill()
    monkeypatch.setattr(
        skills_api.skills_service, "list_skills", AsyncMock(return_value=[skill])
    )
    resp = await client.get("/api/v1/skills")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        skills_api.skills_service, "create_skill", AsyncMock(return_value=skill)
    )
    resp = await client.post(
        "/api/v1/skills",
        json={"name": "Test Skill", "slug": "test-skill", "content": "# Test"},
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        skills_api.skills_service, "get_skill", AsyncMock(return_value=skill)
    )
    resp = await client.get(f"/api/v1/skills/{skill.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        skills_api.skills_service, "update_skill", AsyncMock(return_value=skill)
    )
    resp = await client.patch(
        f"/api/v1/skills/{skill.id}", json={"description": "Updated skill"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        skills_api.skills_service, "archive_skill", AsyncMock(return_value=skill)
    )
    resp = await client.post(f"/api/v1/skills/{skill.id}/archive")
    assert resp.status_code == 200

    monkeypatch.setattr(
        skills_api.skills_service, "restore_skill", AsyncMock(return_value=skill)
    )
    resp = await client.post(f"/api/v1/skills/{skill.id}/restore")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_backends_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend = _make_backend()
    monkeypatch.setattr(
        backends_api.backends_service,
        "list_backends",
        AsyncMock(return_value=[backend]),
    )
    monkeypatch.setattr(
        backends_api.backends_service,
        "get_allowed_agent_ids_map",
        AsyncMock(return_value={backend.id: ["agt_1"]}),
    )
    monkeypatch.setattr(
        backends_api.backends_service,
        "get_allowed_agent_ids",
        AsyncMock(return_value=["agt_1"]),
    )
    monkeypatch.setattr(
        backends_api.backends_service,
        "get_secret_keys",
        lambda _b: ["token"],
    )

    resp = await client.get("/api/v1/backends")
    assert resp.status_code == 200
    assert resp.json()[0]["secret_keys"] == ["token"]

    monkeypatch.setattr(
        backends_api.backends_service,
        "create_backend",
        AsyncMock(return_value=backend),
    )
    resp = await client.post(
        "/api/v1/backends",
        json={
            "name": "GitHub Main",
            "type": "git",
            "provider": "github",
            "secrets": {"token": "ghp_123"},
        },
    )
    assert resp.status_code == 201

    monkeypatch.setattr(
        backends_api.backends_service,
        "probe_backend_config",
        AsyncMock(return_value=(BackendHealth.HEALTHY, 42, None)),
    )
    resp = await client.post(
        "/api/v1/backends/probe",
        json={"type": "git", "provider": "github", "secrets": {"token": "ghp_123"}},
    )
    assert resp.status_code == 200
    assert resp.json()["health"] == "healthy"

    monkeypatch.setattr(
        backends_api.backends_service, "get_backend", AsyncMock(return_value=backend)
    )
    resp = await client.get(f"/api/v1/backends/{backend.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        backends_api.backends_service, "update_backend", AsyncMock(return_value=backend)
    )
    resp = await client.patch(
        f"/api/v1/backends/{backend.id}", json={"name": "GitHub Updated"}
    )
    assert resp.status_code == 200

    monkeypatch.setattr(backends_api.backends_service, "delete_backend", AsyncMock())
    resp = await client.delete(f"/api/v1/backends/{backend.id}")
    assert resp.status_code == 204

    monkeypatch.setattr(
        backends_api.backends_service,
        "grant_agent_access",
        AsyncMock(return_value=["agt_1"]),
    )
    resp = await client.post(f"/api/v1/backends/{backend.id}/agents/agt_1")
    assert resp.status_code == 200

    monkeypatch.setattr(
        backends_api.backends_service,
        "revoke_agent_access",
        AsyncMock(return_value=[]),
    )
    resp = await client.delete(f"/api/v1/backends/{backend.id}/agents/agt_1")
    assert resp.status_code == 200

    monkeypatch.setattr(
        backends_api.backends_service,
        "test_backend_connection",
        AsyncMock(return_value=backend),
    )
    resp = await client.post(f"/api/v1/backends/{backend.id}/test")
    assert resp.status_code == 200

    monkeypatch.setattr(
        backends_api.backends_service,
        "discover_backend_repositories",
        AsyncMock(return_value=backend),
    )
    resp = await client.post(f"/api/v1/backends/{backend.id}/discover-repos")
    assert resp.status_code == 200

    monkeypatch.setattr(
        backends_api.backends_service,
        "create_remote_repository",
        AsyncMock(
            return_value=(
                backend,
                {
                    "full_name": "org/new-repo",
                    "default_branch": "main",
                    "private": True,
                    "web_url": "https://github.com/org/new-repo",
                },
            )
        ),
    )
    resp = await client.post(
        f"/api/v1/backends/{backend.id}/create-repo",
        json={"name": "new-repo", "private": True},
    )
    assert resp.status_code == 201
    assert resp.json()["full_name"] == "org/new-repo"

    monkeypatch.setattr(
        backends_api.backends_service,
        "discover_backend_models",
        AsyncMock(return_value=backend),
    )
    resp = await client.post(f"/api/v1/backends/{backend.id}/discover-models")
    assert resp.status_code == 200

    # CLI Auth endpoints
    backend.provider = "anthropic"
    cli_state = {
        "status": "running",
        "auth_url": "https://auth.example.com",
        "user_code": "ABCD-1234",
        "log_output": "Open browser",
        "authenticated": False,
        "error": None,
        "backend": backend,
    }
    monkeypatch.setattr(
        backends_api.cli_auth_service,
        "start_cli_auth_session",
        AsyncMock(return_value=cli_state),
    )
    resp = await client.post(f"/api/v1/backends/{backend.id}/cli-auth/start")
    assert resp.status_code == 200
    assert resp.json()["user_code"] == "ABCD-1234"

    monkeypatch.setattr(
        backends_api.cli_auth_service,
        "get_cli_auth_status",
        AsyncMock(return_value=cli_state),
    )
    resp = await client.get(f"/api/v1/backends/{backend.id}/cli-auth/status")
    assert resp.status_code == 200

    monkeypatch.setattr(
        backends_api.cli_auth_service,
        "submit_cli_auth_code",
        AsyncMock(
            return_value={**cli_state, "authenticated": True, "status": "succeeded"}
        ),
    )
    resp = await client.post(
        f"/api/v1/backends/{backend.id}/cli-auth/submit",
        json={"code": "oauth-code-123"},
    )
    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True

    monkeypatch.setattr(
        backends_api.cli_auth_service,
        "cancel_cli_auth_session",
        AsyncMock(return_value={"status": "cancelled", "authenticated": False}),
    )
    resp = await client.post(f"/api/v1/backends/{backend.id}/cli-auth/cancel")
    assert resp.status_code == 200
    assert resp.json()["status"] == "cancelled"


@pytest.mark.asyncio
async def test_policies_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pol = _make_policy()
    monkeypatch.setattr(
        policies_api.policies_service,
        "list_org_policies",
        AsyncMock(return_value=[pol]),
    )
    resp = await client.get("/api/v1/policies")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        policies_api.policies_service,
        "update_org_policy",
        AsyncMock(return_value=pol),
    )
    resp = await client.patch(
        "/api/v1/policies/plan", json={"min_approvers": 2, "timeout_minutes": 45}
    )
    assert resp.status_code == 200

    proj_pol = _make_policy(project_id="prj_test123456")
    monkeypatch.setattr(
        policies_api.policies_service,
        "list_project_policies",
        AsyncMock(return_value=[proj_pol]),
    )
    resp = await client.get("/api/v1/policies/projects/prj_test123456")
    assert resp.status_code == 200

    monkeypatch.setattr(
        policies_api.policies_service,
        "update_project_policy",
        AsyncMock(return_value=proj_pol),
    )
    resp = await client.patch(
        "/api/v1/policies/projects/prj_test123456/plan",
        json={"requires_approval": False},
    )
    assert resp.status_code == 200

    monkeypatch.setattr(
        policies_api.policies_service,
        "reset_project_policy",
        AsyncMock(return_value=pol),
    )
    resp = await client.delete("/api/v1/policies/projects/prj_test123456/plan")
    assert resp.status_code == 200
