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
    assert 'env["GEMINI_FORCE_FILE_STORAGE"] = "true"' in pty_script
    assert 'env["GEMINI_FORCE_ENCRYPTED_FILE_STORAGE"] = "false"' in pty_script

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
    assert trusted_priv is False
    assert assoc_priv == "UNTRUSTED"


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


@pytest.mark.asyncio
async def test_patch_seed_partial_update_preserves_acceptance_criteria_and_org_admin_rbac(
    api_client_factory: Any,
    admin_user: User,
    member_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gitseed_core.services import seeds as seeds_svc

    proj = Project(id="prj_patch", name="Patch", slug="patch")
    seed = Seed(
        id="sed_patch_1",
        project_id=proj.id,
        number=1,
        title="Original title",
        acceptance_criteria=["Criterion A", "Criterion B"],
        stage=SeedStage.IDEATE,
        status=SeedStatus.AWAITING_APPROVAL,
    )
    monkeypatch.setattr(seeds_svc, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(
        seeds_svc, "_require_seed_developer", AsyncMock(return_value=(proj, None))
    )

    async with api_client_factory(admin_user) as ac:
        resp = await ac.patch(
            f"/api/v1/seeds/{seed.id}",
            json={"title": "Updated title only"},
            headers={"X-GitSeed-CSRF": "1"},
        )
        assert resp.status_code == 200
        assert seed.title == "Updated title only"
        assert seed.acceptance_criteria == ["Criterion A", "Criterion B"]

    # Non-admin member must receive 403 on require_org_admin endpoints
    async with api_client_factory(member_user) as member_ac:
        admin_resp = await member_ac.patch(
            "/api/v1/settings",
            json={"org_name": "Unauthorized Rename"},
            headers={"X-GitSeed-CSRF": "1"},
        )
        assert admin_resp.status_code == 403


@pytest.mark.asyncio
async def test_egress_proxy_port_scoping_sandbox_image_allowlist_and_session_push_governance() -> None:
    from gitseed_core.services.egress_proxy import (
        EgressDeniedError,
        check_egress_destination,
    )
    from gitseed_core.services.sandbox_detect import sanitize_run_json_image
    from gitseed_core.services.session_workspace import (
        _validate_session_push_governance,
    )

    async def _fake_resolver(host: str, port: int) -> list[str]:
        if host in ("litellm", "api"):
            return ["172.18.0.5"]
        return ["93.184.216.34"]

    internal_hosts = ["litellm:4000", "api:8420"]
    allowlist = ["api.openai.com"]
    assert await check_egress_destination(
        "litellm",
        4000,
        allowlist=allowlist,
        internal_hosts=internal_hosts,
        resolver=_fake_resolver,
    ) == ["172.18.0.5"]
    assert await check_egress_destination(
        "api",
        8420,
        allowlist=allowlist,
        internal_hosts=internal_hosts,
        resolver=_fake_resolver,
    ) == ["172.18.0.5"]
    assert await check_egress_destination(
        "api.openai.com",
        443,
        allowlist=allowlist,
        internal_hosts=internal_hosts,
        resolver=_fake_resolver,
    ) == ["93.184.216.34"]
    for blocked_host, blocked_port in (
        ("api", 5432),
        ("litellm", 6379),
        ("api.openai.com", 5432),
    ):
        with pytest.raises(EgressDeniedError):
            await check_egress_destination(
                blocked_host,
                blocked_port,
                allowlist=allowlist,
                internal_hosts=internal_hosts,
                resolver=_fake_resolver,
            )

    # Untrusted .gitseed/run.json cannot force arbitrary external registry pulls
    assert sanitize_run_json_image("evil.attacker.io/crypto/miner:latest") == "python:3.12-slim"
    assert sanitize_run_json_image("node:22-bookworm-slim") == "node:22-bookworm-slim"
    assert (
        sanitize_run_json_image(
            "registry.internal/team/dev:1.0",
            project_sandbox_config={"image": "registry.internal/team/dev:1.0"},
        )
        == "registry.internal/team/dev:1.0"
    )

    # Interactive session push governance allows .env.example, blocks .env and leaked tokens
    _validate_session_push_governance(
        [{"path": ".env.example", "status": "added"}],
        {".env.example": b"API_URL=http://localhost:8420\n"},
    )
    with pytest.raises(Invalid, match="protected path"):
        _validate_session_push_governance(
            [{"path": ".env", "status": "added"}],
            {".env": b"SECRET=1\n"},
        )
    fake_gh_pat = "ghp_" + ("A" * 36)
    with pytest.raises(Invalid, match="Secret scan blocked push"):
        _validate_session_push_governance(
            [{"path": "src/config.py", "status": "modified"}],
            {"src/config.py": f'TOKEN = "{fake_gh_pat}"\n'.encode()},
        )


@pytest.mark.asyncio
async def test_second_pass_xss_dotfiles_local_git_budget_and_network_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import io
    import tarfile
    from decimal import Decimal

    from gitseed_core.api.v1 import backends as backends_api
    from gitseed_core.services import (
        compute_runtime,
        gateway,
        local_git,
        sandboxes,
        session_workspace,
    )

    # 1. Reflected XSS blocked & BroadcastChannel emitted on GitHub App setup callback
    mock_session = AsyncMock()
    setup_mock = AsyncMock()
    monkeypatch.setattr(backends_api.backends_service, "complete_github_app_setup", setup_mock)

    resp_xss = await backends_api.github_app_setup_callback(
        mock_session,
        installation_id="</script><script>alert(1)</script>",
        state="st1",
    )
    setup_mock.assert_not_awaited()
    raw_xss = bytes(resp_xss.body).decode("utf-8")
    assert "<script>alert(1)" not in raw_xss
    assert "BroadcastChannel('gitseed:github-app')" in raw_xss
    assert "window.close()" in raw_xss

    fake_backend = MagicMock()
    fake_backend.id = "bkd_123"
    setup_mock.return_value = fake_backend
    resp_ok = await backends_api.github_app_setup_callback(
        mock_session,
        installation_id="  987654  ",
        state="st2",
    )
    setup_mock.assert_awaited_once_with(mock_session, installation_id="987654", state="st2")
    raw_ok = bytes(resp_ok.body).decode("utf-8")
    assert '"installationId": "987654"' in raw_ok

    # 2. Dotfile preservation in tar manifests, archive unpack, and protected push paths
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data in (
            ("./.env", b"A=1\n"),
            ("workspace/.gitignore", b"dist/\n"),
            (".vscode/tasks.json", b"{}"),
        ):
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    tar_bytes = buf.getvalue()
    manifest = session_workspace.manifest_from_tar(tar_bytes)
    assert ".env" in manifest and "env" not in manifest
    assert ".gitignore" in manifest and "gitignore" not in manifest
    extracted = session_workspace.files_from_archive(tar_bytes, "workspace")
    assert ".env" in extracted and ".gitignore" in extracted and ".vscode/tasks.json" in extracted

    assert session_workspace._is_protected_push_path(".env") is True
    assert session_workspace._is_protected_push_path(".ENV") is True
    assert session_workspace._is_protected_push_path(".env.local") is True
    assert session_workspace._is_protected_push_path(".vscode/tasks.json") is True
    assert session_workspace._is_protected_push_path(".gitmodules") is True
    assert session_workspace._is_protected_push_path(".git/config") is True
    assert (
        session_workspace._is_protected_push_path(
            ".github/workflows/ci.yml", [".github/workflows/*"]
        )
        is True
    )
    assert session_workspace._is_protected_push_path(".env.example") is False
    assert session_workspace._is_protected_push_path(".env.example", [".env*"]) is True
    assert (
        sandboxes._first_matching_glob(".artifacts/out.zip", ["./.artifacts/*.zip"])
        == "./.artifacts/*.zip"
    )

    # 3. Local Git workspace init rejects unsafe .git/config directives
    ws = tmp_path / "ws"
    (ws / ".git").mkdir(parents=True)
    (ws / ".git" / "config").write_text("[core]\n\tfsmonitor = evil\n", encoding="utf-8")
    bare = tmp_path / "bare.git"
    bare.mkdir()
    with pytest.raises(local_git.InvalidGitInputError, match="unsafe directives"):
        local_git._ensure_git_repo_initialized(
            ws, bare, project_name="Test", default_branch="main"
        )

    # 4. Remaining monthly budget & seed spend summing Runs + AgentSessions
    proj = Project(id="prj_1", name="P", slug="p-slug", monthly_budget=Decimal("100.00"))
    db_sess = AsyncMock()
    db_sess.scalar = AsyncMock(return_value=Decimal("75.00"))
    rem = await gateway.compute_project_remaining_monthly_budget(db_sess, proj)
    assert rem == Decimal("25.00")
    assert (
        gateway._resolve_session_seed_max_budget(
            project=proj, remaining_project_budget=rem
        )
        == 25.0
    )

    seed = Seed(id="sed_1", project_id="prj_1", number=1, title="T", spent=Decimal("0"))
    db_sess.get = AsyncMock(return_value=None)
    db_sess.scalar = AsyncMock(return_value=Decimal("42.50"))
    await runs_svc._recompute_seed_spent(db_sess, seed, proj)
    assert seed.spent == Decimal("42.50")

    # 5. Dynamic LiteLLM port in K8s NetworkPolicy
    monkeypatch.setattr(settings, "LITELLM_BASE_URL", "http://litellm:4567")
    np = compute_runtime._build_k8s_default_egress_network_policy("ns-run", "ns-cp")
    ports = [p["port"] for p in np["spec"]["egress"][1]["ports"]]
    assert 4567 in ports and 4000 not in ports


@pytest.mark.asyncio
async def test_probe_discovery_and_personal_mode_explicit_sdlc_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gitseed_core.models.policy import Policy

    from conftest import _exec_result, _make_session

    # 1. discover_probe_metadata returns discovered models for healthy LM Studio probe
    async def _fake_fetch_models(backend: Any, _secrets: dict[str, str]) -> list[dict[str, Any]]:
        assert backend.provider == "lmstudio"
        return [{"id": "qwen2.5-coder-7b-instruct", "owned_by": "lmstudio"}]

    monkeypatch.setattr(backends_svc, "_fetch_backend_models", _fake_fetch_models)
    models, repos = await backends_svc.discover_probe_metadata(
        type=BackendType.INFERENCE,
        provider="lmstudio",
        endpoint="http://localhost:1234/v1",
        config={},
        secrets={},
    )
    assert repos == []
    assert models == [
        {
            "id": "qwen2.5-coder-7b-instruct",
            "owned_by": "lmstudio",
            "enabled": True,
            "is_default": True,
        }
    ]

    # 2. Personal Mode (cli_oauth) backend in single-user org with set_sdlc_default=True
    #    calls provision_sdlc_agents_for_backend with assign_policies=True, force_switch=True
    lm_backend = Backend(
        id="bnd_lm",
        name="LM Studio",
        type=BackendType.INFERENCE,
        provider="lmstudio",
        endpoint="http://localhost:1234/v1",
        config={
            "auth_mode": "api_key",
            "models": [{"id": "qwen2.5-coder-7b-instruct", "enabled": True}],
            "is_sdlc_default": True,
        },
        enabled=True,
    )
    agy_backend = Backend(
        id="bnd_agy",
        name="Google Antigravity",
        type=BackendType.INFERENCE,
        provider="antigravity",
        config={
            "auth_mode": "cli_oauth",
            "personal_mode": True,
            "personal_mode_acknowledged": True,
            "cli_auth_status": "authenticated",
            "models": [{"id": "gemini-2.5-pro", "enabled": True}],
            "auto_create_agents": True,
            "set_sdlc_default": True,
        },
        secrets_encrypted=encrypt_secrets(
            {"cli_auth_bundle": '{"oauth_creds.json": "{}"}'}
        ),
        enabled=True,
    )

    sess = _make_session()
    sess.scalar = AsyncMock(return_value=1)  # single-user org
    sess.execute = AsyncMock(return_value=_exec_result(scalars_all=[lm_backend, agy_backend]))
    fake_policies = {
        st: Policy(stage=st, agent_id=f"agt_{st.value}")
        for st in SeedStage
    }
    monkeypatch.setattr(
        backends_svc, "ensure_org_policies", AsyncMock(return_value=fake_policies)
    )
    provision_spy = AsyncMock(return_value=[])
    monkeypatch.setattr(backends_svc, "provision_sdlc_agents_for_backend", provision_spy)

    await backends_svc.maybe_auto_provision_sdlc_agents(sess, agy_backend)
    provision_spy.assert_awaited_once_with(
        sess,
        agy_backend,
        force_switch=True,
        assign_policies=True,
        overwrite_existing=True,
    )

    # 3. provision_sdlc_agents_for_backend preserves existing open-* agents when creating agy-*
    open_ideator = Agent(
        id="agt_open_ideate",
        handle="open-ideator",
        name="OpenCode Ideator",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="lmstudio/qwen2.5-coder-7b-instruct",
        enabled=True,
    )
    added_agents: list[Agent] = []
    sess2 = _make_session()
    sess2.add = MagicMock(side_effect=lambda obj: added_agents.append(obj) if isinstance(obj, Agent) else None)
    sess2.get = AsyncMock(return_value=open_ideator)
    sess2.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[lm_backend, agy_backend]),  # all_inference
            _exec_result(scalars_all=[]),  # coordinator lookup
            _exec_result(scalars_all=[]),  # agy-ideator lookup
            _exec_result(scalars_all=[]),  # agy-planner lookup
            _exec_result(scalars_all=[]),  # agy-coder lookup
            _exec_result(scalars_all=[]),  # agy-reviewer lookup
            _exec_result(scalars_all=[]),  # agy-merger lookup
        ]
    )
    monkeypatch.undo()
    monkeypatch.setattr(
        backends_svc, "ensure_org_policies", AsyncMock(return_value=fake_policies)
    )
    await backends_svc.provision_sdlc_agents_for_backend(
        sess2, agy_backend, force_switch=True, assign_policies=True, overwrite_existing=True
    )
    assert open_ideator.handle == "open-ideator"
    created_handles = {a.handle for a in added_agents}
    assert {
        "coordinator",
        "agy-ideator",
        "agy-planner",
        "agy-coder",
        "agy-reviewer",
        "agy-merger",
    }.issubset(created_handles)


def test_antigravity_pty_auth_script_requires_agy_and_syncs_headless_keyring() -> None:
    from gitseed_core.services.cli_auth import (
        _build_pty_auth_script,
        _extract_auth_url_and_code,
    )

    script = _build_pty_auth_script("antigravity")
    compile(script, "<pty_auth_antigravity>", "exec")
    assert 'env["SSH_CONNECTION"] = "127.0.0.1 22 127.0.0.1 22"' in script
    agy_branch = script.split('if provider == "antigravity":\n    if shutil.which("agy"):')[1].split('elif provider == "gemini":')[0]
    assert 'cmd = ["agy"]' in agy_branch
    assert 'cmd = ["gemini"]' not in agy_branch
    assert "sys.exit(1)" in agy_branch
    assert "Falling back to Google OAuth sign-in flow" not in script
    assert "gnome-keyring-daemon" in script
    assert "secret-tool" in script
    assert "sync_keyring_to_files()" in script

    # Banner doc links (/docs, /terms) never shadow real OAuth URLs
    url, _ = _extract_auth_url_and_code(
        "Docs: https://antigravity.google/docs/cli\nSign in: https://accounts.google.com/o/oauth2/v2/auth?client_id=123"
    )
    assert url == "https://accounts.google.com/o/oauth2/v2/auth?client_id=123"
    url_doc_only, _ = _extract_auth_url_and_code("Docs: https://antigravity.google/docs/cli")
    assert url_doc_only is None


@pytest.mark.asyncio
async def test_antigravity_auto_provisions_stage_agents_when_added_after_local_inference(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from gitseed_core.models.policy import Policy
    from gitseed_core.services.cli_auth import _has_verified_provider_credentials

    from conftest import _exec_result, _make_session

    local_backend = Backend(
        id="bnd_local_ollama",
        name="Local Model Inference",
        type=BackendType.INFERENCE,
        provider="ollama",
        endpoint="http://localhost:11434/v1",
        config={
            "auth_mode": "api_key",
            "models": [{"id": "qwen2.5-coder:14b", "enabled": True}],
            "is_sdlc_default": True,
        },
        enabled=True,
    )

    # Case A: Antigravity added in api_key mode without a secret key (optional api_key)
    agy_keyless = Backend(
        id="bnd_agy_keyless",
        name="Google Antigravity",
        type=BackendType.INFERENCE,
        provider="antigravity",
        config={
            "auth_mode": "api_key",
            "models": [{"id": "gemini-2.5-pro", "enabled": True}],
            "auto_create_agents": True,
            "set_sdlc_default": False,
        },
        secrets_encrypted=encrypt_secrets({}),
        enabled=True,
    )
    assert backends_svc._is_inference_backend_ready(agy_keyless) is True

    sess = _make_session()
    sess.scalar = AsyncMock(return_value=1)
    sess.execute = AsyncMock(
        return_value=_exec_result(scalars_all=[local_backend, agy_keyless])
    )
    fake_policies = {
        st: Policy(stage=st, agent_id=f"agt_open_{st.value}") for st in SeedStage
    }
    monkeypatch.setattr(
        backends_svc, "ensure_org_policies", AsyncMock(return_value=fake_policies)
    )
    provision_spy = AsyncMock(return_value=[])
    monkeypatch.setattr(backends_svc, "provision_sdlc_agents_for_backend", provision_spy)

    await backends_svc.maybe_auto_provision_sdlc_agents(sess, agy_keyless)
    provision_spy.assert_awaited_once_with(
        sess,
        agy_keyless,
        force_switch=False,
        assign_policies=False,
        overwrite_existing=False,
    )

    # Case B: Antigravity saved in cli_oauth mode with cli_auth_status="pending" and auto_create_agents=True
    provision_spy.reset_mock()
    agy_pending_oauth = Backend(
        id="bnd_agy_oauth_pending",
        name="Google Antigravity OAuth",
        type=BackendType.INFERENCE,
        provider="antigravity",
        config={
            "auth_mode": "cli_oauth",
            "personal_mode": True,
            "personal_mode_acknowledged": True,
            "cli_auth_status": "pending",
            "models": [{"id": "gemini-2.5-pro", "enabled": True}],
            "auto_create_agents": True,
            "set_sdlc_default": False,
        },
        secrets_encrypted=encrypt_secrets({}),
        enabled=True,
    )
    assert backends_svc._is_inference_backend_ready(agy_pending_oauth) is False
    await backends_svc.maybe_auto_provision_sdlc_agents(sess, agy_pending_oauth)
    provision_spy.assert_awaited_once_with(
        sess,
        agy_pending_oauth,
        force_switch=False,
        assign_policies=False,
        overwrite_existing=False,
    )

    # Case C: Antigravity CLI auth credential verification accepts jetski_state.pbtxt, acp-auth-state.json, and auth.json keyring dumps
    assert (
        _has_verified_provider_credentials(
            "antigravity",
            {
                ".config/Antigravity/User/globalStorage/jetski_state.pbtxt": 'oauth_token: "ya29.abc"\n'
            },
        )
        is True
    )
    assert (
        _has_verified_provider_credentials(
            "antigravity",
            {".gemini/antigravity-cli/acp-auth-state.json": '{"authenticated": true}'},
        )
        is True
    )
    assert (
        _has_verified_provider_credentials(
            "antigravity",
            {
                ".gemini/antigravity-cli/auth.json": '{"keyring_items": {"item_0": "secret-val"}}'
            },
        )
        is True
    )
    assert (
        _has_verified_provider_credentials(
            "antigravity",
            {
                ".gemini/antigravity-cli/jetski_state.pbtxt": "onboarding_complete: true\n",
                ".gemini/antigravity-cli/acp-auth-state.json": '{"authenticated": false}',
            },
        )
        is False
    )


@pytest.mark.asyncio
async def test_xai_opencode_default_and_coordinator_personal_mode_and_bootstrap_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify xAI OpenCode SDLC provisioning, Personal Mode coordinator handling, Alembic lock release, and finalize retry."""
    from contextlib import asynccontextmanager
    from types import SimpleNamespace

    import httpx
    from gitseed_common.schemas.agent import CoordinatorChatMessage, CoordinatorChatRequest
    from gitseed_core import bootstrap as bootstrap_mod
    from gitseed_core.services import coordinator as coordinator_svc
    from gitseed_core.services import session_workspace as session_ws_svc
    from gitseed_runner import broker_modes

    from conftest import _exec_result, _make_session  # type: ignore[import-not-found]

    # 1. xAI API-key backend provisions OpenCode stage agents out of the box (while honoring explicit grok-build)
    xai_api = Backend(
        id="bnd_xai_api",
        name="xAI Grok API",
        type=BackendType.INFERENCE,
        provider="xai",
        config={"models": [{"id": "grok-4.7", "enabled": True}]},
        secrets_encrypted=encrypt_secrets({"api_key": "xai-key"}),
        enabled=True,
    )
    assert backends_svc.resolve_provider_sdlc_profile(xai_api) == (
        "grok",
        "Grok",
        AgentRuntime.OPENCODE,
        "grok-4.7",
    )

    xai_explicit = Backend(
        id="bnd_xai_cli",
        name="xAI Grok CLI",
        type=BackendType.INFERENCE,
        provider="xai",
        config={"runtime": "grok-build"},
        secrets_encrypted=encrypt_secrets({"api_key": "xai-key"}),
        enabled=True,
    )
    assert backends_svc.resolve_provider_sdlc_profile(xai_explicit) == (
        "grok",
        "Grok",
        AgentRuntime.GROK_BUILD,
        "default",
    )

    # 2. _resolve_coordinator_model prefers HTTP/LiteLLM backends over Personal Mode cli_oauth
    personal_claude = Backend(
        id="bnd_personal_claude",
        name="Claude Personal",
        type=BackendType.INFERENCE,
        provider="anthropic",
        config={
            "auth_mode": "cli_oauth",
            "personal_mode": True,
            "personal_mode_acknowledged": True,
            "personal_owner_user_id": "usr_owner_1",
            "models": [{"id": "claude-sonnet-5-5", "enabled": True}],
        },
        secrets_encrypted=encrypt_secrets({"oauth_token": "tok"}),
        enabled=True,
    )
    local_ollama = Backend(
        id="bnd_local_ollama",
        name="Local Ollama",
        type=BackendType.INFERENCE,
        provider="ollama",
        endpoint="http://localhost:11434",
        config={"models": [{"id": "qwen2.5-coder:32b", "enabled": True}]},
        secrets_encrypted=encrypt_secrets({}),
        enabled=True,
    )
    coord_sess = _make_session()
    coord_sess.execute = AsyncMock(
        return_value=_exec_result(scalars_all=[personal_claude, local_ollama])
    )
    monkeypatch.setattr(
        coordinator_svc, "validate_personal_mode_backend_access", lambda *a, **kw: None
    )
    picked_b, picked_m = await coordinator_svc._resolve_coordinator_model(
        coord_sess, None, None, actor_user_id="usr_owner_1"
    )
    assert picked_b is local_ollama
    assert picked_m == "qwen2.5-coder:32b"

    # When ONLY a Personal Mode cli_oauth backend exists, coordinator & draft_push_summary skip LiteLLM HTTP calls
    litellm_http_spy = AsyncMock(side_effect=AssertionError("LiteLLM HTTP must not be called"))
    monkeypatch.setattr(coordinator_svc, "sync_backend_to_litellm", litellm_http_spy)
    monkeypatch.setattr(
        coordinator_svc,
        "_resolve_coordinator_agent",
        AsyncMock(return_value=None),
    )
    monkeypatch.setattr(
        coordinator_svc,
        "_resolve_coordinator_model",
        AsyncMock(return_value=(personal_claude, "claude-sonnet-5-5")),
    )
    monkeypatch.setattr(
        coordinator_svc,
        "_build_workspace_snapshot",
        AsyncMock(return_value=("## Workspace", "Workspace snapshot OK.", None)),
    )
    monkeypatch.setattr(
        coordinator_svc,
        "_build_brainstorm_context",
        AsyncMock(return_value=("", [], None, [])),
    )
    actor = User(
        id="usr_owner_1",
        email="owner@gitseed.local",
        name="Owner",
        org_role=OrgRole.ADMIN,
        status=UserStatus.ACTIVE,
        password_hash="x",
    )
    chat_res = await coordinator_svc.chat_with_coordinator(
        coord_sess,
        actor,
        CoordinatorChatRequest(
            messages=[CoordinatorChatMessage(role="user", content="Status update?")],
            mode="general",
        ),
    )
    assert chat_res.model == "personal-mode-cli"
    assert "Personal Mode" in chat_res.reply
    assert "Workspace snapshot OK." in chat_res.reply

    monkeypatch.setattr(
        session_ws_svc,
        "_resolve_coordinator_model",
        AsyncMock(return_value=(personal_claude, "claude-sonnet-5-5")),
    )
    monkeypatch.setattr(
        session_ws_svc,
        "get_effective_stage_policy",
        AsyncMock(return_value=SimpleNamespace(min_acceptance_criteria=1, max_acceptance_criteria=4)),
    )
    proj = Project(id="prj_1", name="Demo", slug="demo")
    _, _, _, draft_src = await session_ws_svc.draft_push_summary(
        coord_sess,
        proj,
        session_label="sandbox-1",
        context_text="Implemented auth fix",
        changes=[{"path": "app.py", "status": "modified"}],
        files={"app.py": b"print('ok')\n"},
    )
    assert draft_src == "fallback"

    # 3. bootstrap_database closes engine.begin() BEFORE running synchronous Alembic upgrade
    tx_open = False
    upgrade_saw_open_tx: list[bool] = []

    @asynccontextmanager
    async def _fake_begin():
        nonlocal tx_open
        tx_open = True
        fake_conn = AsyncMock()
        fake_conn.dialect = SimpleNamespace(name="postgresql")

        async def _run_sync(fn: Any) -> None:
            sync_conn = MagicMock()
            sync_conn.dialect = SimpleNamespace(name="postgresql")
            monkeypatch.setattr(
                bootstrap_mod, "inspect", lambda _c: SimpleNamespace(has_table=lambda _t: True)
            )
            fn(sync_conn)

        fake_conn.run_sync = _run_sync
        try:
            yield fake_conn
        finally:
            tx_open = False

    monkeypatch.setattr(bootstrap_mod.settings, "GITSEED_AUTO_MIGRATE", True)
    monkeypatch.setattr(bootstrap_mod, "engine", SimpleNamespace(begin=_fake_begin, dialect=SimpleNamespace(name="postgresql")))
    monkeypatch.setattr(
        bootstrap_mod.command,
        "upgrade",
        lambda _cfg, _rev: upgrade_saw_open_tx.append(tx_open),
    )
    boot_sess = _make_session()
    boot_sess.get = AsyncMock(return_value=SimpleNamespace(id=1))
    boot_sess.scalar = AsyncMock(side_effect=[1, 1, 1])
    executed_sql: list[str] = []

    async def _track_exec(stmt: Any) -> Any:
        executed_sql.append(str(stmt))
        return _exec_result(scalars_all=[], scalar_one=None)

    boot_sess.execute = AsyncMock(side_effect=_track_exec)

    @asynccontextmanager
    async def _boot_sess_cm():
        yield boot_sess

    monkeypatch.setattr(bootstrap_mod, "AsyncSessionLocal", _boot_sess_cm)
    monkeypatch.setattr(bootstrap_mod, "ensure_org_policies", AsyncMock(return_value={}))
    await bootstrap_mod.bootstrap_database()
    assert upgrade_saw_open_tx == [False]
    assert any("UPDATE agent_session" in s for s in executed_sql)

    # 4. fetch_finalize_context retries transient HTTP 409 during K8s container termination settle
    attempts = 0

    class _FakeClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, url: str, headers: dict[str, str]) -> httpx.Response:
            nonlocal attempts
            attempts += 1
            req = httpx.Request("GET", url, headers=headers)
            if attempts < 3:
                return httpx.Response(409, json={"detail": "agent still terminating"}, request=req)
            return httpx.Response(200, json={"blocked_paths": [".github/**"]}, request=req)

    monkeypatch.setattr(broker_modes.httpx, "AsyncClient", _FakeClient)
    monkeypatch.setattr(broker_modes.asyncio, "sleep", AsyncMock())
    fin_ctx = await broker_modes.fetch_finalize_context("http://api:8420", "gsr_fin_tok")
    assert attempts == 3
    assert fin_ctx == {"blocked_paths": [".github/**"]}


