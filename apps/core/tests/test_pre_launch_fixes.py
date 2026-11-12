from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from gitseed_common.enums import (
    BACKEND_PROVIDER_SPECS,
    AgentKind,
    AgentRuntime,
    BackendType,
    OrgRole,
    ProjectVisibility,
    QueueStrategy,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
    UserStatus,
)
from gitseed_common.schemas.project import ProjectResponse
from gitseed_common.schemas.run import RunResponse
from gitseed_common.schemas.seed import SeedResponse
from gitseed_common.schemas.settings import OrgSettingsResponse
from gitseed_core.config import settings
from gitseed_core.models.agent import Agent
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedTask
from gitseed_core.models.user import User
from gitseed_core.security import encrypt_secrets
from gitseed_core.services import backends as backends_svc
from gitseed_core.services import runs as runs_svc
from gitseed_core.services import seeds as seeds_svc
from gitseed_core.services.cli_auth import (
    ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME as CORE_ALLOWED_CLI_AUTH_PATHS,
)
from gitseed_core.services.cli_auth import (
    _build_pty_auth_script,
    _strip_ansi,
)
from gitseed_core.services.errors import Invalid
from gitseed_core.services.local_git import (
    default_project_workspace_path,
    ensure_local_bare_repo,
)
from gitseed_core.services.runner_bridge import (
    _validate_and_merge_refreshed_cli_auth_bundle,
)
from gitseed_core.services.session_vscode import (
    _append_upstream_vscode_token,
    _build_vscode_frame_headers,
    derive_vscode_upstream_token,
    ensure_session_vscode,
)
from gitseed_core.services.webhooks import _is_trusted_bitbucket_author
from gitseed_runner.adapters import (
    ALLOWED_CLI_AUTH_PATHS_BY_RUNTIME as RUNNER_ALLOWED_CLI_AUTH_PATHS,
)


def test_cli_auth_paths_parity_and_jetski_state_pbtxt_harvest() -> None:
    assert RUNNER_ALLOWED_CLI_AUTH_PATHS == CORE_ALLOWED_CLI_AUTH_PATHS
    assert (
        ".config/Antigravity/User/globalStorage/jetski_state.pbtxt"
        in CORE_ALLOWED_CLI_AUTH_PATHS["antigravity"]
    )

    # Wide-PTY padded TUI box lines collapse cleanly
    padded_box = (
        "╭" + ("─" * 300) + "╮\r\n"
        "│   2. Trust parent folder (home)" + (" " * 267) + "│\r\n"
        "╰" + ("─" * 300) + "╯"
    )
    cleaned = _strip_ansi(padded_box)
    assert " " * 50 not in cleaned
    assert "2. Trust parent folder (home)" in cleaned

    pty_script = _build_pty_auth_script("antigravity")
    assert "trustedFolders.json" in pty_script
    assert "onboardingComplete" in pty_script

    # Non-JSON jetski_state.pbtxt and OpenCode wrapper keys survive refresh validation
    merged, updated_keys = _validate_and_merge_refreshed_cli_auth_bundle(
        {
            ".config/Antigravity/User/globalStorage/jetski_state.pbtxt": 'oauth_token: "ya29.test"\n',
        },
        runtime="antigravity",
        existing_raw_json=None,
        backend_provider="antigravity",
    )
    assert ".config/Antigravity/User/globalStorage/jetski_state.pbtxt" in merged
    assert updated_keys == [".config/Antigravity/User/globalStorage/jetski_state.pbtxt"]

    oc_merged, oc_keys = _validate_and_merge_refreshed_cli_auth_bundle(
        {
            ".local/share/opencode/auth.json": '{"openrouter": {"key": "sk-or-123"}, "mcpoauth": {"srv": {"token": "tok"}}}',
        },
        runtime="opencode",
        existing_raw_json=None,
        backend_provider="openai",
    )
    assert ".local/share/opencode/auth.json" in oc_merged
    assert oc_keys == [".local/share/opencode/auth.json"]


def test_budget_enforcement_unpriced_schema_serialization() -> None:
    now = datetime.now(UTC)
    assert "mcp-http" in BACKEND_PROVIDER_SPECS[BackendType.TOOLS]

    org_resp = OrgSettingsResponse(
        default_autonomy=SeedAutonomy.SUPERVISED,
        default_monthly_budget=Decimal("100.00"),
        max_concurrent_runners=4,
        default_project_concurrency=2,
        queue_strategy=QueueStrategy.FIFO,
        pat_lifetime_days=90,
        enforcement="unpriced",
        enforcement_detail="Unpriced local model",
        updated_at=now,
    )
    assert org_resp.enforcement == "unpriced"

    proj_resp = ProjectResponse(
        id="prj_1",
        slug="demo",
        name="Demo",
        visibility=ProjectVisibility.PRIVATE,
        enforcement="unpriced",
        created_at=now,
        updated_at=now,
    )
    assert proj_resp.enforcement == "unpriced"

    seed_resp = SeedResponse(
        id="sed_1",
        project_id="prj_1",
        number=1,
        title="Test seed",
        stage=SeedStage.IDEATE,
        status=SeedStatus.QUEUED,
        autonomy=SeedAutonomy.SUPERVISED,
        enforcement="unpriced",
        created_at=now,
        updated_at=now,
    )
    assert seed_resp.enforcement == "unpriced"

    run_resp = RunResponse.model_validate(
        {
            "id": "run_1",
            "seed_id": "sed_1",
            "stage": "ideate",
            "attempt": 1,
            "agent_id": "agt_1",
            "runtime": "opencode",
            "model": "custom-local",
            "status": "succeeded",
            "output": {
                "cost_enforcement": "unpriced",
                "enforcement_detail": "No token pricing configured",
            },
            "created_at": now,
            "updated_at": now,
        }
    )
    assert run_resp.enforcement == "unpriced"
    assert run_resp.cost_enforcement == "unpriced"


def test_bitbucket_webhook_author_trust_fails_closed_on_public_repos() -> None:
    public_payload: dict[str, Any] = {
        "repository": {
            "full_name": "acme/open-repo",
            "is_private": False,
            "owner": {"uuid": "{owner-1}", "username": "acme-owner"},
        },
        "actor": {"uuid": "{stranger-9}", "nickname": "random-forker"},
        "comment": {"user": {"uuid": "{stranger-9}"}},
    }
    trusted, assoc = _is_trusted_bitbucket_author(
        public_payload["comment"], public_payload
    )
    assert trusted is False
    assert assoc == "UNTRUSTED"

    unknown_vis_payload: dict[str, Any] = {
        "repository": {
            "full_name": "acme/unknown-repo",
            "owner": {"uuid": "{owner-1}"},
        },
        "actor": {"uuid": "{stranger-9}"},
    }
    trusted_unk, _ = _is_trusted_bitbucket_author({}, unknown_vis_payload)
    assert trusted_unk is False

    owner_payload: dict[str, Any] = {
        "repository": {
            "full_name": "acme/open-repo",
            "is_private": False,
            "owner": {"uuid": "{owner-1}"},
        },
        "actor": {"uuid": "{owner-1}"},
    }
    trusted_owner, assoc_owner = _is_trusted_bitbucket_author({}, owner_payload)
    assert trusted_owner is True
    assert assoc_owner == "OWNER"

    private_payload: dict[str, Any] = {
        "repository": {
            "full_name": "acme/private-repo",
            "is_private": True,
            "owner": {"uuid": "{owner-1}"},
        },
        "actor": {"uuid": "{teammate-2}"},
    }
    trusted_priv, assoc_priv = _is_trusted_bitbucket_author({}, private_payload)
    assert trusted_priv is True
    assert assoc_priv == "MEMBER"


@pytest.mark.asyncio
async def test_session_vscode_sandbox_origin_frame_headers_and_k8s_guard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "SANDBOX_ORIGIN", "http://127.0.0.1:8423")
    monkeypatch.setattr(settings, "PUBLIC_WEB_URL", "http://localhost:8421")
    headers = _build_vscode_frame_headers({})
    assert "X-Frame-Options" not in headers
    assert "frame-ancestors 'self'" in headers["Content-Security-Policy"]
    assert "http://localhost:8421" in headers["Content-Security-Policy"]
    assert "http://127.0.0.1:8421" in headers["Content-Security-Policy"]

    monkeypatch.setattr(settings, "SANDBOX_ORIGIN", "")
    same_origin_headers = _build_vscode_frame_headers({})
    assert same_origin_headers["X-Frame-Options"] == "SAMEORIGIN"
    assert same_origin_headers["Content-Security-Policy"] == "frame-ancestors 'self'"

    tkn = derive_vscode_upstream_token("sandbox:sbx_test123", "cid_1")
    assert len(tkn) == 48
    assert _append_upstream_vscode_token("/?folder=/workspace", tkn).endswith(
        f"&tkn={tkn}"
    )

    k8s_backend = Backend(
        id="bnd_k8s",
        name="Cluster",
        type=BackendType.COMPUTE,
        provider="kubernetes",
        endpoint="https://kubernetes.default.svc",
        enabled=True,
    )
    with pytest.raises(Invalid, match="Docker compute backend"):
        await ensure_session_vscode(
            kind="terminal",
            session_id="sbx_test123",
            project_id="prj_1",
            backend=k8s_backend,
            container_id="pod-123",
        )


@pytest.mark.asyncio
async def test_local_git_backend_probe_discover_create_and_workspace_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ws_root = tmp_path / "workspaces"
    repos_root = tmp_path / "local_repos"
    ws_root.mkdir(parents=True, exist_ok=True)
    repos_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOTS", str(ws_root))
    monkeypatch.setenv("GITSEED_LOCAL_REPOS_DIR", str(repos_root))

    resolved_default = default_project_workspace_path("my-local-proj")
    assert Path(resolved_default) == (ws_root / "my-local-proj").resolve()

    ensure_local_bare_repo(
        "alpha-repo", "Alpha Repo", workspace_path=str(ws_root / "alpha-repo")
    )

    local_backend = Backend(
        id="bnd_local_git",
        name="Local Folders",
        type=BackendType.GIT,
        provider="local",
        endpoint=str(ws_root),
        config={},
        secrets_encrypted=encrypt_secrets({}),
        enabled=True,
    )

    health, detail = await backends_svc._probe_backend(local_backend, {})
    assert str(health) == "healthy"
    assert detail is None

    discovered = await backends_svc._fetch_git_repositories(local_backend, {})
    assert any("alpha-repo" in r["full_name"] for r in discovered)

    session = AsyncMock()
    session.get = AsyncMock(return_value=local_backend)
    admin = User(
        id="usr_admin",
        email="admin@example.com",
        name="Admin",
        org_role=OrgRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    _, created = await backends_svc.create_remote_repository(
        session,
        local_backend.id,
        actor=admin,
        name="beta-repo",
        private=True,
    )
    assert "beta-repo" in created["full_name"]
    assert created["default_branch"] == "main"


@pytest.mark.asyncio
async def test_github_app_setup_rejects_unauthenticated_installation_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    awaiting_backend = Backend(
        id="bnd_ghapp_1",
        name="GitHub App Pending",
        type=BackendType.GIT,
        provider="github-app",
        endpoint="https://api.github.com",
        config={"awaiting_installation": True, "manifest_state": "expected_state_token"},
        secrets_encrypted=encrypt_secrets({}),
        enabled=True,
    )
    session = AsyncMock()
    scalar_res = MagicMock()
    scalar_res.scalars.return_value.all.return_value = [awaiting_backend]
    session.execute = AsyncMock(return_value=scalar_res)

    sync_mock = AsyncMock(return_value=awaiting_backend)
    monkeypatch.setattr(backends_svc, "sync_github_app_installation", sync_mock)

    # Without state or valid RSA private_key ownership check, claim returns None
    unclaimed = await backends_svc.complete_github_app_setup(
        session,
        installation_id="999999",
        state=None,
    )
    assert unclaimed is None
    sync_mock.assert_not_awaited()

    # With matching manifest_state, binds installation_id
    bound = await backends_svc.complete_github_app_setup(
        session,
        installation_id="999999",
        state="expected_state_token",
    )
    assert bound is awaiting_backend
    sync_mock.assert_awaited_once_with(
        session, awaiting_backend.id, explicit_installation_id="999999"
    )


@pytest.mark.asyncio
async def test_cancel_run_resets_orphaned_running_task_and_advance_seed_forward_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    admin = User(
        id="usr_admin",
        email="admin@example.com",
        name="Admin",
        org_role=OrgRole.ADMIN,
        status=UserStatus.ACTIVE,
    )
    proj = Project(
        id="prj_1",
        slug="demo",
        name="Demo",
        visibility=ProjectVisibility.PUBLIC,
    )
    seed = Seed(
        id="sed_1",
        project_id=proj.id,
        number=1,
        title="Implement auth",
        stage=SeedStage.EXECUTE,
        status=SeedStatus.RUNNING,
    )
    running_task = SeedTask(
        id="tsk_1",
        seed_id=seed.id,
        position=0,
        title="Task 1",
        status=TaskStatus.RUNNING,
        started_at=datetime.now(UTC),
    )
    comp = Backend(
        id="bnd_comp",
        name="Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        enabled=True,
    )
    agent = Agent(
        id="agt_coder",
        handle="coder",
        name="Coder",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        enabled=True,
    )
    active_run = Run(
        id="run_exec_1",
        seed_id=seed.id,
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id=agent.id,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        compute_backend_id=comp.id,
        container_id="cid_exec_1",
        status=RunStatus.RUNNING,
    )

    session = AsyncMock()
    session.add = MagicMock()
    session.get = AsyncMock(
        side_effect=lambda model, ident: (
            active_run
            if model is Run
            else seed
            if model is Seed
            else proj
            if model is Project
            else comp
        )
    )

    call_idx = 0

    async def _exec(stmt: Any) -> Any:
        nonlocal call_idx
        call_idx += 1
        res = MagicMock()
        if call_idx == 1:
            res.scalars.return_value.all.return_value = []
        else:
            res.scalars.return_value.all.return_value = [running_task]
        return res

    session.execute = _exec
    stop_mock = AsyncMock()
    monkeypatch.setattr(runs_svc, "finalize_run_litellm_key", AsyncMock())
    monkeypatch.setattr(
        runs_svc, "fetch_docker_container_logs", AsyncMock(return_value="")
    )
    monkeypatch.setattr(runs_svc, "stop_and_remove_docker_container", stop_mock)

    await runs_svc.cancel_run(session, admin, active_run.id)
    assert running_task.status == TaskStatus.PENDING
    assert running_task.started_at is None
    stop_mock.assert_awaited_once_with(comp, "cid_exec_1", run_id="run_exec_1")

    # Verify advance_seed rejects moving backwards
    monkeypatch.setattr(seeds_svc, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(
        seeds_svc, "_require_seed_developer", AsyncMock(return_value=(proj, None))
    )
    with pytest.raises(Invalid, match="move-back"):
        await seeds_svc.advance_seed(
            session, admin, seed.id, target_stage=SeedStage.PLAN
        )
