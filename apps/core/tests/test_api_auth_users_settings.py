from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import gitseed_core.bootstrap as bootstrap_mod
import gitseed_core.main as main_mod
import httpx
import pytest
from fastapi.security import HTTPAuthorizationCredentials
from gitseed_common.enums import (
    ActivityType,
    ActorType,
    BackendType,
    OrgRole,
    QueueStrategy,
    SeedAutonomy,
    SeedStage,
    UserStatus,
)
from gitseed_core.api import deps as deps_mod
from gitseed_core.api.v1 import (
    activity as activity_api,
)
from gitseed_core.api.v1 import (
    auth as auth_api,
)
from gitseed_core.api.v1 import (
    settings as settings_api,
)
from gitseed_core.api.v1 import (
    users as users_api,
)
from gitseed_core.main import app
from gitseed_core.models.activity import Activity
from gitseed_core.models.agent import Agent
from gitseed_core.models.backend import Backend
from gitseed_core.models.policy import Policy
from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import PersonalAccessToken, User
from gitseed_core.services.backends import resolve_provider_sdlc_profile
from gitseed_core.services.errors import (
    Conflict,
    Forbidden,
    Invalid,
    NotFound,
    RateLimited,
    Unauthorized,
)

from conftest import _make_user, _now


@pytest.mark.asyncio
async def test_main_discovery_health_version_and_error_handlers(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Root discovery
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "/version" in resp.json()["paths"]

    # Version
    v_resp = await client.get("/version")
    assert v_resp.status_code == 200
    assert "version" in v_resp.json()

    # Health check - connected
    @asynccontextmanager
    async def _ok_session():
        s = AsyncMock()
        yield s

    fake_redis = MagicMock()
    fake_redis.ping = AsyncMock(return_value=True)
    monkeypatch.setattr(main_mod, "AsyncSessionLocal", _ok_session)
    monkeypatch.setattr(main_mod, "get_redis", lambda: fake_redis)

    h_resp = await client.get("/healthz")
    assert h_resp.status_code == 200
    assert h_resp.json()["status"] == "ok"
    assert h_resp.json()["database"] == "connected"
    assert h_resp.json()["redis"] == "connected"

    # Health check - degraded when DB and Redis fail
    @asynccontextmanager
    async def _bad_session():
        s = AsyncMock()
        s.execute.side_effect = RuntimeError("db down")
        yield s

    def _bad_redis():
        raise RuntimeError("redis down")

    monkeypatch.setattr(main_mod, "AsyncSessionLocal", _bad_session)
    monkeypatch.setattr(main_mod, "get_redis", _bad_redis)

    h_deg = await client.get("/healthz")
    assert h_deg.status_code == 200
    assert h_deg.json()["status"] == "degraded"
    assert h_deg.json()["database"] == "unreachable"
    assert h_deg.json()["redis"] == "unreachable"

    # Domain error handlers mapped to HTTP status codes
    for exc, expected_code in [
        (NotFound("missing item"), 404),
        (Conflict("duplicate item"), 409),
        (Forbidden("denied"), 403),
        (Invalid("bad input"), 422),
        (Unauthorized("bad token"), 401),
        (RateLimited("slow down"), 429),
    ]:
        monkeypatch.setattr(
            users_api.users_service,
            "get_user",
            AsyncMock(side_effect=exc),
        )
        err_resp = await client.get("/api/v1/users/usr_1")
        assert err_resp.status_code == expected_code
        assert err_resp.json()["detail"] == exc.message
        if expected_code == 401:
            assert err_resp.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.asyncio
async def test_main_startup_sync_and_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    @asynccontextmanager
    async def _mock_session():
        yield AsyncMock()

    async def _sync(_s: Any) -> None:
        calls.append("synced")

    monkeypatch.setattr(main_mod, "AsyncSessionLocal", _mock_session)
    monkeypatch.setattr(main_mod, "sync_all_backends_to_litellm", _sync)
    await main_mod._startup_sync_litellm()
    assert calls == ["synced"]

    # Lifespan context manager
    monkeypatch.setattr(main_mod, "bootstrap_database", AsyncMock())
    monkeypatch.setattr(main_mod, "close_connections", AsyncMock())
    monkeypatch.setattr(main_mod.settings, "GITSEED_EMBED_SCHEDULER", False)
    async with main_mod.lifespan(app):
        pass

    # _run_scheduler_main
    monkeypatch.setattr(main_mod, "run_scheduler_forever", AsyncMock())
    await main_mod._run_scheduler_main()


@pytest.mark.asyncio
async def test_deps_functions_directly(monkeypatch: pytest.MonkeyPatch) -> None:
    user = _make_user(user_id="usr_admin123456", org_role=OrgRole.ADMIN)
    member = _make_user(user_id="usr_mem", org_role=OrgRole.MEMBER)
    session = AsyncMock()
    req = SimpleNamespace(state=SimpleNamespace())

    # get_current_user via bearer token
    monkeypatch.setattr(
        deps_mod.auth_service, "authenticate_token", AsyncMock(return_value=user)
    )
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="gsp_secret")
    got = await deps_mod.get_current_user(req, session, bearer=creds, session_cookie=None)  # type: ignore[arg-type]
    assert got.id == user.id

    # get_current_user via session cookie
    monkeypatch.setattr(
        deps_mod.auth_service,
        "authenticate_session_cookie",
        AsyncMock(return_value=user),
    )
    got2 = await deps_mod.get_current_user(
        req, session, bearer=None, session_cookie="sess_123"  # type: ignore[arg-type]
    )
    assert got2.id == user.id

    # get_current_user unauthorized
    monkeypatch.setattr(
        deps_mod.auth_service,
        "authenticate_session_cookie",
        AsyncMock(return_value=None),
    )
    with pytest.raises(Unauthorized):
        await deps_mod.get_current_user(req, session, bearer=None, session_cookie="expired")  # type: ignore[arg-type]

    # get_session_user rejects bearer PAT
    with pytest.raises(Forbidden):
        await deps_mod.get_session_user(req, session, bearer=creds, session_cookie=None)  # type: ignore[arg-type]

    # get_session_user accepts valid cookie
    monkeypatch.setattr(
        deps_mod.auth_service,
        "authenticate_session_cookie",
        AsyncMock(return_value=user),
    )
    got3 = await deps_mod.get_session_user(
        req, session, bearer=None, session_cookie="sess_123"  # type: ignore[arg-type]
    )
    assert got3.id == user.id

    # get_session_user unauthorized without cookie
    with pytest.raises(Unauthorized):
        await deps_mod.get_session_user(req, session, bearer=None, session_cookie=None)  # type: ignore[arg-type]

    # require_org_admin
    assert (await deps_mod.require_org_admin(user)).id == user.id
    with pytest.raises(Forbidden):
        await deps_mod.require_org_admin(member)


@pytest.mark.asyncio
async def test_bootstrap_database_empty_and_existing(monkeypatch: pytest.MonkeyPatch) -> None:
    # 1. Empty database with no inference backend yet: creates OrgSettings, admin user, and compute backend
    # without pre-seeding generic opencode stage agents until an inference provider is connected
    added: list[Any] = []
    session = AsyncMock()
    session.get = AsyncMock(return_value=None)
    session.scalar = AsyncMock(side_effect=[0, 0, 0])  # user_count=0, compute_count=0, agent_count=0
    no_inf_res = MagicMock()
    no_inf_res.scalar_one_or_none.return_value = None
    session.execute = AsyncMock(return_value=no_inf_res)
    session.add = MagicMock(side_effect=lambda obj: added.append(obj))
    session.add_all = MagicMock(side_effect=lambda objs: added.extend(objs))

    pol_map = {stage: Policy(stage=stage, agent_id=None) for stage in SeedStage}
    monkeypatch.setattr(
        bootstrap_mod, "ensure_org_policies", AsyncMock(return_value=pol_map)
    )
    monkeypatch.setattr(bootstrap_mod.settings, "GITSEED_ADMIN_PASSWORD", "")

    @asynccontextmanager
    async def _engine_begin():
        yield AsyncMock()

    @asynccontextmanager
    async def _session_ctx():
        yield session

    monkeypatch.setattr(bootstrap_mod, "engine", SimpleNamespace(begin=_engine_begin))
    monkeypatch.setattr(bootstrap_mod, "AsyncSessionLocal", _session_ctx)
    await bootstrap_mod.bootstrap_database()
    assert any(isinstance(x, OrgSettings) for x in added)
    assert any(isinstance(x, User) for x in added)
    assert any(isinstance(x, Backend) for x in added)
    assert sum(1 for x in added if isinstance(x, Agent)) == 0
    session.commit.assert_awaited_once()

    # 2. Verify provider-prefixed 5-stage agent provisioning (e.g. claude-ideator .. claude-merger, grok-ideator ..)
    claude_b = Backend(name="Claude", type=BackendType.INFERENCE, provider="anthropic")
    grok_b = Backend(name="Grok", type=BackendType.INFERENCE, provider="xai")
    openai_b = Backend(name="OpenAI", type=BackendType.INFERENCE, provider="openai")
    agy_b = Backend(name="Antigravity", type=BackendType.INFERENCE, provider="antigravity")

    assert resolve_provider_sdlc_profile(claude_b)[:2] == ("claude", "Claude")
    assert resolve_provider_sdlc_profile(grok_b)[:2] == ("grok", "Grok")
    assert resolve_provider_sdlc_profile(openai_b)[:2] == ("open", "OpenAI")
    assert resolve_provider_sdlc_profile(agy_b)[:2] == ("agy", "Antigravity")


@pytest.mark.asyncio
async def test_auth_routes(
    client: httpx.AsyncClient,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        auth_api.auth_service,
        "authenticate",
        AsyncMock(return_value=(admin_user, "sess_abc")),
    )
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@gitseed.local", "password": "password123"},
    )
    assert resp.status_code == 200
    assert resp.json()["email"] == "admin@gitseed.local"

    monkeypatch.setattr(auth_api.auth_service, "change_password", AsyncMock())
    resp = await client.post(
        "/api/v1/auth/change-password",
        json={"current_password": "oldpassword", "new_password": "newpassword123"},
    )
    assert resp.status_code == 204

    invited_user = _make_user(user_id="usr_admin123456", status=UserStatus.INVITED)
    monkeypatch.setattr(
        auth_api.users_service,
        "get_invite_by_token",
        AsyncMock(return_value=invited_user),
    )
    resp = await client.get("/api/v1/auth/invite", params={"token": "gsi_tok"})
    assert resp.status_code == 200
    assert resp.json()["email"] == invited_user.email

    monkeypatch.setattr(
        auth_api.users_service,
        "accept_invite",
        AsyncMock(return_value=(admin_user, "sess_new")),
    )
    resp = await client.post(
        "/api/v1/auth/accept-invite",
        json={"token": "gsi_tok", "password": "newpassword123", "preferred_name": "Admin"},
    )
    assert resp.status_code == 200
    assert resp.json()["id"] == admin_user.id

    monkeypatch.setattr(auth_api.auth_service, "end_session", AsyncMock())
    resp = await client.post("/api/v1/auth/logout")
    assert resp.status_code == 204

    resp = await client.get("/api/v1/auth/me")
    assert resp.status_code == 200
    assert resp.json()["id"] == admin_user.id

    now = _now()
    pat = PersonalAccessToken(
        id="pat_123",
        user_id=admin_user.id,
        name="CLI Token",
        token_prefix="gsp_1234",
        token_hash="hash",
        expires_at=now + timedelta(days=30),
        created_at=now,
    )
    monkeypatch.setattr(
        auth_api.auth_service, "list_tokens", AsyncMock(return_value=[pat])
    )
    resp = await client.get("/api/v1/auth/tokens")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    monkeypatch.setattr(
        auth_api.auth_service,
        "create_token",
        AsyncMock(return_value=(pat, "gsp_1234_raw_secret")),
    )
    resp = await client.post(
        "/api/v1/auth/tokens", json={"name": "CLI Token", "expires_in_days": 30}
    )
    assert resp.status_code == 201
    assert resp.json()["token"] == "gsp_1234_raw_secret"

    monkeypatch.setattr(auth_api.auth_service, "revoke_token", AsyncMock())
    resp = await client.delete("/api/v1/auth/tokens/pat_123")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_users_routes(
    client: httpx.AsyncClient,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        users_api.users_service, "list_users", AsyncMock(return_value=[admin_user])
    )
    resp = await client.get("/api/v1/users")
    assert resp.status_code == 200
    assert len(resp.json()) == 1

    invited = _make_user(user_id="usr_inv", status=UserStatus.INVITED)
    monkeypatch.setattr(
        users_api.users_service,
        "invite_user",
        AsyncMock(return_value=(invited, "gsi_raw123")),
    )
    resp = await client.post(
        "/api/v1/users/invite",
        json={"email": "new@gitseed.local", "name": "New User", "org_role": "member"},
    )
    assert resp.status_code == 201
    assert resp.json()["invite_token"] == "gsi_raw123"

    monkeypatch.setattr(
        users_api.users_service,
        "reinvite_user",
        AsyncMock(return_value=(invited, "gsi_raw456")),
    )
    resp = await client.post("/api/v1/users/usr_inv/reinvite")
    assert resp.status_code == 200
    assert resp.json()["invite_token"] == "gsi_raw456"

    monkeypatch.setattr(
        users_api.users_service, "get_user", AsyncMock(return_value=admin_user)
    )
    resp = await client.get(f"/api/v1/users/{admin_user.id}")
    assert resp.status_code == 200

    monkeypatch.setattr(
        users_api.users_service, "update_user", AsyncMock(return_value=admin_user)
    )
    resp = await client.patch(
        f"/api/v1/users/{admin_user.id}",
        json={"name": "Updated Name", "preferred_name": "Admin"},
    )
    assert resp.status_code == 200

    suspended = _make_user(user_id="usr_sus", status=UserStatus.SUSPENDED)
    monkeypatch.setattr(
        users_api.users_service, "suspend_user", AsyncMock(return_value=suspended)
    )
    resp = await client.post("/api/v1/users/usr_sus/suspend")
    assert resp.status_code == 200
    assert resp.json()["status"] == "suspended"

    monkeypatch.setattr(
        users_api.users_service, "reactivate_user", AsyncMock(return_value=admin_user)
    )
    resp = await client.post("/api/v1/users/usr_sus/reactivate")
    assert resp.status_code == 200

    monkeypatch.setattr(users_api.users_service, "delete_invited_user", AsyncMock())
    resp = await client.delete("/api/v1/users/usr_inv")
    assert resp.status_code == 204


@pytest.mark.asyncio
async def test_settings_and_activity_routes(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Settings
    org_settings = OrgSettings(
        id=1,
        default_autonomy=SeedAutonomy.SUPERVISED,
        default_monthly_budget=Decimal("100.00"),
        default_seed_budget_cap=Decimal("15.00"),
        default_spend_checkpoint=Decimal("50.00"),
        max_concurrent_runners=2,
        max_sandbox_sessions=2,
        default_project_concurrency=0,
        queue_strategy=QueueStrategy.FIFO,
        pat_lifetime_days=365,
        default_theme="dark",
        default_ambient_bg=True,
        default_show_guide=True,
        updated_at=_now(),
    )
    monkeypatch.setattr(
        settings_api.settings_service,
        "get_org_settings",
        AsyncMock(return_value=org_settings),
    )
    resp = await client.get("/api/v1/settings")
    assert resp.status_code == 200
    assert resp.json()["default_theme"] == "dark"

    monkeypatch.setattr(
        settings_api.settings_service,
        "update_org_settings",
        AsyncMock(return_value=org_settings),
    )
    resp = await client.patch(
        "/api/v1/settings", json={"max_concurrent_runners": 4, "default_theme": "dark"}
    )
    assert resp.status_code == 200

    # Activity
    act = Activity(
        id=1,
        occurred_at=_now(),
        project_id="prj_test123456",
        seed_id="sed_test123456",
        actor_type=ActorType.USER,
        actor_id="usr_admin123456",
        type=ActivityType.SEED_CREATED,
        data={"number": 1},
    )
    monkeypatch.setattr(
        activity_api.activity_service,
        "list_activity",
        AsyncMock(return_value=[act]),
    )
    resp = await client.get("/api/v1/activity")
    assert resp.status_code == 200
    assert len(resp.json()) == 1
