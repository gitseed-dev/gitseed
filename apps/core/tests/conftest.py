from __future__ import annotations

import asyncio
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    BackendHealth,
    BackendType,
    OrgRole,
    ProjectRole,
    ProjectVisibility,
    RunStatus,
    SandboxStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    UserStatus,
)
from gitseed_core.api import deps as deps_mod
from gitseed_core.db import get_session
from gitseed_core.main import app
from gitseed_core.models.agent import Agent
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project
from gitseed_core.models.run import Run
from gitseed_core.models.sandbox import SandboxSession
from gitseed_core.models.seed import Seed
from gitseed_core.models.skill import Skill
from gitseed_core.models.user import User
from gitseed_core.security import hash_password
from gitseed_core.services.policies import EffectiveStagePolicy

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

_DEFAULT_PASSWORD_HASH = hash_password("ValidPass123!")


def _now() -> datetime:
    return datetime.now(UTC)


def _exec_result(
    *,
    scalar_one_or_none: Any = None,
    scalar_one: Any = None,
    first: Any = None,
    all_rows: list[Any] | None = None,
    scalars_all: list[Any] | None = None,
    scalars_first: Any = None,
) -> MagicMock:
    res = MagicMock()
    res.scalar_one_or_none.return_value = scalar_one_or_none
    res.scalar_one.return_value = scalar_one
    res.first.return_value = first
    res.all.return_value = all_rows or []
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all if scalars_all is not None else (all_rows or [])
    scalars_mock.first.return_value = (
        scalars_first
        if scalars_first is not None
        else (scalars_all[0] if scalars_all else scalar_one_or_none)
    )
    res.scalars.return_value = scalars_mock
    return res


def _make_session() -> AsyncMock:
    session = AsyncMock()
    session.add = MagicMock()
    session.add_all = MagicMock()
    return session


def _make_user(
    user_id: str = "usr_admin",
    org_role: OrgRole = OrgRole.ADMIN,
    status: UserStatus = UserStatus.ACTIVE,
    *,
    email: str | None = None,
    name: str = "User Name",
    preferred_name: str = "Preferred",
) -> User:
    now = _now()
    resolved_email = (
        email
        if email is not None
        else ("admin@gitseed.local" if user_id == "usr_admin123456" else f"{user_id}@gitseed.local")
    )
    return User(
        id=user_id,
        email=resolved_email,
        name=name,
        preferred_name=preferred_name,
        org_role=org_role,
        status=status,
        password_hash=_DEFAULT_PASSWORD_HASH,
        preferences={"theme": "dark"},
        last_active_at=now,
        created_at=now,
        updated_at=now,
    )


def _make_project(project_id: str = "prj_test123456") -> Project:
    now = _now()
    return Project(
        id=project_id,
        name="Alpha Project",
        slug="alpha-project",
        description="Test project",
        tags=["core", "api"],
        visibility=ProjectVisibility.PRIVATE,
        seed_counter=3,
        monthly_budget=Decimal("200.00"),
        seed_budget_cap=Decimal("25.00"),
        spend_checkpoint=Decimal("50.00"),
        max_concurrent_runs=2,
        backends_restricted=False,
        sandbox_config={},
        created_by="usr_admin123456",
        created_at=now,
        updated_at=now,
    )


def _make_seed(seed_id: str = "sed_test123456", project_id: str = "prj_test123456") -> Seed:
    now = _now()
    return Seed(
        id=seed_id,
        project_id=project_id,
        number=1,
        title="Add API route tests",
        description="Test all API endpoints",
        specification="Detailed spec",
        acceptance_criteria=["Pass tests"],
        plan="Step 1",
        tags=["testing"],
        stage=SeedStage.PLAN,
        status=SeedStatus.QUEUED,
        autonomy=SeedAutonomy.SUPERVISED,
        stage_entered_at=now,
        spent=Decimal("1.25"),
        checkpoint_approved_through=Decimal("0"),
        created_by="usr_admin123456",
        created_at=now,
        updated_at=now,
    )


def _make_run(run_id: str = "run_test123456", seed_id: str = "sed_test123456") -> Run:
    now = _now()
    return Run(
        id=run_id,
        seed_id=seed_id,
        task_id=None,
        stage=SeedStage.PLAN,
        attempt=1,
        agent_id="agt_arch123456",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        compute_backend_id="bnd_comp123456",
        status=RunStatus.QUEUED,
        input_tokens=100,
        output_tokens=50,
        cost=Decimal("0.05"),
        output={},
        created_at=now,
        updated_at=now,
    )


def _make_agent(agent_id: str = "agt_arch123456") -> Agent:
    now = _now()
    return Agent(
        id=agent_id,
        handle="architect",
        name="Architect",
        kind=AgentKind.STAGE,
        template="architect",
        instructions="Plan tasks",
        runtime=AgentRuntime.OPENCODE,
        model="default",
        skills=["skl_1"],
        tools=[],
        enabled=True,
        created_by="usr_admin123456",
        created_at=now,
        updated_at=now,
    )


def _make_skill(skill_id: str = "skl_test123456") -> Skill:
    now = _now()
    return Skill(
        id=skill_id,
        slug="test-skill",
        name="Test Skill",
        description="A reusable skill",
        content="# Skill markdown",
        created_by="usr_admin123456",
        created_at=now,
        updated_at=now,
    )


def _make_backend(
    backend_id: str = "bnd_git123456",
    btype: BackendType = BackendType.GIT,
) -> Backend:
    now = _now()
    return Backend(
        id=backend_id,
        name="GitHub Main",
        type=btype,
        provider="github",
        endpoint="https://api.github.com",
        config={},
        secrets_encrypted=None,
        capacity=None,
        access_restricted=False,
        health=BackendHealth.HEALTHY,
        enabled=True,
        created_by="usr_admin123456",
        created_at=now,
        updated_at=now,
    )


def _make_policy(
    stage: SeedStage = SeedStage.PLAN,
    project_id: str | None = None,
) -> EffectiveStagePolicy:
    return EffectiveStagePolicy(
        id="pol_test123456",
        project_id=project_id,
        stage=stage,
        enabled=True,
        agent_id="agt_arch123456",
        requires_approval=True,
        min_approvers=1,
        min_approver_role=ProjectRole.DEVELOPER,
        allow_auto_approve=False,
        allow_self_approval=True,
        allow_second_opinions=stage in (SeedStage.IDEATE, SeedStage.PLAN, SeedStage.REVIEW),
        max_attempts=3,
        timeout_minutes=30,
        overridden_fields=["requires_approval"] if project_id else [],
        updated_at=_now(),
        updated_by="usr_admin123456",
    )


def _make_sandbox(
    sandbox_id: str = "sbx_test123456",
    project_id: str = "prj_test123456",
) -> SandboxSession:
    now = _now()
    return SandboxSession(
        id=sandbox_id,
        project_id=project_id,
        seed_id="sed_test123456",
        repository_id=1,
        ref="main",
        head_sha="abc1234",
        image="python:3.12-slim",
        setup_command="pip install pytest",
        command="pytest",
        artifact_globs=["dist/*"],
        status=SandboxStatus.RUNNING,
        exit_code=None,
        container_id="cid_123",
        compute_backend_id="bnd_comp123456",
        artifacts=[],
        log_tail="running...",
        started_by="usr_admin123456",
        started_at=now,
        expires_at=now + timedelta(minutes=30),
    )


def pytest_asyncio_loop_factories(
    config: pytest.Config, item: pytest.Item
) -> dict[str, Callable[[], asyncio.AbstractEventLoop]]:
    """Use SelectorEventLoop on Windows for real psycopg async PostgreSQL integration tests."""
    del config
    if sys.platform == "win32" and "test_postgres_scheduler_integration" in item.nodeid:
        return {"selector": asyncio.SelectorEventLoop}
    return {"default": asyncio.new_event_loop}


@pytest.fixture(autouse=True)
def _disable_redis_event_publish(monkeypatch: pytest.MonkeyPatch) -> None:
    import gitseed_core.services.events as events_mod
    import gitseed_core.services.webhooks as webhooks_mod

    monkeypatch.setattr(events_mod, "_publish_redis", AsyncMock())
    monkeypatch.setattr(
        webhooks_mod, "_check_and_record_delivery_id_redis", AsyncMock(return_value=True)
    )


@pytest.fixture
def fake_db() -> AsyncMock:
    return _make_session()


@pytest.fixture
def mock_session(fake_db: AsyncMock) -> AsyncMock:
    return fake_db


@pytest.fixture
def admin_user() -> User:
    return _make_user(
        user_id="usr_admin123456",
        org_role=OrgRole.ADMIN,
        name="Admin User",
        preferred_name="Admin",
    )


@pytest.fixture
def member_user() -> User:
    return _make_user(
        user_id="usr_member123456",
        org_role=OrgRole.MEMBER,
        name="Member User",
        preferred_name="Member",
    )


@pytest.fixture
def viewer_user() -> User:
    return _make_user(
        user_id="usr_viewer123456",
        org_role=OrgRole.MEMBER,
        name="Viewer User",
        preferred_name="Viewer",
    )


@pytest.fixture
def api_client_factory(
    fake_db: AsyncMock,
    admin_user: User,
) -> Callable[[User | None], AsyncIterator[httpx.AsyncClient]]:
    @asynccontextmanager
    async def _factory(user: User | None = None) -> AsyncIterator[httpx.AsyncClient]:
        active_user = user or admin_user

        async def _override_db() -> AsyncIterator[AsyncMock]:
            yield fake_db

        app.dependency_overrides[get_session] = _override_db
        app.dependency_overrides[deps_mod.get_current_user] = lambda: active_user
        app.dependency_overrides[deps_mod.get_session_user] = lambda: active_user
        try:
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac
        finally:
            app.dependency_overrides.clear()

    return _factory


@pytest_asyncio.fixture
async def client(
    api_client_factory: Callable[[User | None], AsyncIterator[httpx.AsyncClient]],
    admin_user: User,
) -> AsyncIterator[httpx.AsyncClient]:
    async with api_client_factory(admin_user) as ac:
        yield ac
