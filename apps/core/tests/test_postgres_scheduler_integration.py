"""Real PostgreSQL Integration Tests for Scheduler Concurrency & Locking (W5).

Verifies against a live PostgreSQL 16 instance (spawned as a throwaway Docker container
from `postgres:16-alpine` or via `TEST_DATABASE_URL`):
1. `SELECT ... FOR UPDATE SKIP LOCKED` concurrent run claiming (zero double-spawns).
2. `pg_try_advisory_xact_lock` transaction-level scheduler tick mutual exclusion and release on commit.
3. Commit-before-spawn visibility so an immediate runner `/api/v1/runner/context` request sees
   `RunStatus.STARTING` rather than stale `RunStatus.QUEUED`.
"""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

import gitseed_core.models  # noqa: F401
import pytest
import pytest_asyncio
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    OrgRole,
    ProjectVisibility,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    UserStatus,
)
from gitseed_core.models.agent import Agent
from gitseed_core.models.project import Project
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.models.user import User
from gitseed_core.security import create_run_token
from gitseed_core.services.runner_bridge import _authenticate_active_run
from gitseed_core.services.scheduler import _SCHEDULER_ADVISORY_LOCK_ID
from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlmodel import SQLModel, select


@pytest.fixture(scope="module", autouse=True)
def _windows_selector_event_loop_policy() -> None:
    """Use WindowsSelectorEventLoopPolicy only for psycopg async integration tests on Windows."""
    if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def _start_ephemeral_postgres_container() -> tuple[str, str]:
    """Start a throwaway `postgres:16-alpine` container on an ephemeral localhost port.

    Returns `(container_id, async_database_url)`.
    """
    docker_info = subprocess.run(
        ["docker", "info"], capture_output=True, text=True, timeout=10, check=False
    )
    if docker_info.returncode != 0:
        pytest.skip("Docker daemon is not reachable and TEST_DATABASE_URL is not set")

    img_check = subprocess.run(
        ["docker", "image", "inspect", "postgres:16-alpine"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    if img_check.returncode != 0:
        pytest.skip("postgres:16-alpine image is not available locally")

    container_name = f"gitseed-test-pg-{uuid4().hex[:10]}"
    run_proc = subprocess.run(
        [
            "docker",
            "run",
            "-d",
            "--rm",
            "--name",
            container_name,
            "-e",
            "POSTGRES_USER=gitseed",
            "-e",
            "POSTGRES_PASSWORD=gitseed_test_pw",
            "-e",
            "POSTGRES_DB=gitseed_test",
            "-p",
            "127.0.0.1::5432",
            "postgres:16-alpine",
        ],
        capture_output=True,
        text=True,
        timeout=20,
        check=True,
    )
    container_id = run_proc.stdout.strip()

    try:
        port_proc = subprocess.run(
            ["docker", "port", container_id, "5432/tcp"],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        host_port = port_proc.stdout.strip().splitlines()[0].rsplit(":", 1)[-1]
        db_url = f"postgresql+psycopg://gitseed:gitseed_test_pw@127.0.0.1:{host_port}/gitseed_test"

        # Wait for postgres inside the container to accept connections
        deadline = time.monotonic() + 25.0
        while time.monotonic() < deadline:
            ready = subprocess.run(
                [
                    "docker",
                    "exec",
                    container_id,
                    "pg_isready",
                    "-U",
                    "gitseed",
                    "-d",
                    "gitseed_test",
                ],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
            if ready.returncode == 0:
                break
            time.sleep(0.25)
        else:
            raise RuntimeError("Timed out waiting for throwaway PostgreSQL container")

        return container_id, db_url
    except Exception:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            timeout=10,
            check=False,
        )
        raise


@pytest.fixture(scope="module")
def pg_database_url() -> Iterator[str]:
    """Start a throwaway `postgres:16-alpine` container for the module and tear it down on exit."""
    external_url = os.environ.get("TEST_DATABASE_URL", "").strip()
    if external_url:
        yield external_url
        return

    container_id, db_url = _start_ephemeral_postgres_container()
    try:
        yield db_url
    finally:
        subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True,
            timeout=15,
            check=False,
        )


@pytest_asyncio.fixture
async def pg_session_factory(
    pg_database_url: str,
) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    """Yield an async_sessionmaker connected to a real PostgreSQL 16 database with all SQLModel tables."""
    engine = create_async_engine(
        pg_database_url, pool_pre_ping=True, pool_size=5, max_overflow=5
    )
    try:
        # Retry initial connection briefly while PostgreSQL finishes initdb startup
        for attempt in range(25):
            try:
                async with engine.begin() as conn:
                    await conn.run_sync(SQLModel.metadata.create_all)
                break
            except Exception:
                if attempt == 24:
                    raise
                await asyncio.sleep(0.3)

        factory = async_sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        yield factory
    finally:
        await engine.dispose()


async def _seed_prerequisite_rows(
    session: AsyncSession,
    *,
    suffix: str,
    run_status: RunStatus = RunStatus.QUEUED,
) -> tuple[Project, Seed, Agent, Run]:
    """Insert valid User, Project, Agent, Seed, and Run rows into PostgreSQL."""
    now = datetime.now(UTC)
    user = User(
        id=f"usr_{suffix}",
        email=f"admin-{suffix}@gitseed.local",
        name="Test Admin",
        org_role=OrgRole.ADMIN,
        status=UserStatus.ACTIVE,
        password_hash="argon2id$test",
        preferences={},
        created_at=now,
        updated_at=now,
    )
    project = Project(
        id=f"prj_{suffix}",
        name=f"Project {suffix}",
        slug=f"proj-{suffix}",
        description="Integration test project",
        tags=[],
        visibility=ProjectVisibility.PRIVATE,
        seed_counter=1,
        backends_restricted=False,
        sandbox_config={},
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    agent = Agent(
        id=f"agt_{suffix}",
        handle=f"coder-{suffix}",
        name="Coder Agent",
        kind=AgentKind.STAGE,
        instructions="Write clean code",
        runtime=AgentRuntime.OPENCODE,
        model="default",
        skills=[],
        tools=[],
        enabled=True,
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    seed = Seed(
        id=f"sed_{suffix}",
        project_id=project.id,
        number=1,
        title="Test concurrent claiming",
        description="Verify Postgres locking",
        specification="Spec",
        acceptance_criteria=["No double spawn"],
        tags=[],
        stage=SeedStage.EXECUTE,
        status=SeedStatus.QUEUED,
        autonomy=SeedAutonomy.SUPERVISED,
        stage_entered_at=now,
        spent=Decimal("0"),
        checkpoint_approved_through=Decimal("0"),
        created_by=user.id,
        created_at=now,
        updated_at=now,
    )
    run = Run(
        id=f"run_{suffix}",
        seed_id=seed.id,
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id=agent.id,
        runtime=AgentRuntime.OPENCODE,
        model="default",
        status=run_status,
        input_tokens=0,
        output_tokens=0,
        cost=Decimal("0"),
        output={},
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()
    session.add_all([project, agent])
    await session.flush()
    session.add(seed)
    await session.flush()
    session.add(run)
    await session.commit()
    return project, seed, agent, run


# ==============================================================================
# Test 1: FOR UPDATE SKIP LOCKED concurrent run claiming
# ==============================================================================
@pytest.mark.asyncio
async def test_postgres_for_update_skip_locked_concurrent_run_claiming(
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two concurrent scheduler coroutines attempting to claim the same QUEUED Run result in exactly one claim."""
    async with pg_session_factory() as setup_session:
        _, _, _, run = await _seed_prerequisite_rows(setup_session, suffix="skiplock01")
        target_run_id = run.id

    first_locked_event = asyncio.Event()
    second_checked_event = asyncio.Event()

    async def _worker_1() -> str | None:
        async with pg_session_factory() as s1:
            claimed = (
                await s1.execute(
                    select(Run)
                    .where(Run.id == target_run_id, Run.status == RunStatus.QUEUED)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            first_locked_event.set()
            # Hold the row lock until worker 2 has executed its SKIP LOCKED query
            await asyncio.wait_for(second_checked_event.wait(), timeout=10.0)
            if claimed is not None:
                claimed.status = RunStatus.STARTING
                await s1.commit()
                return claimed.id
            return None

    async def _worker_2() -> str | None:
        await asyncio.wait_for(first_locked_event.wait(), timeout=10.0)
        try:
            async with pg_session_factory() as s2:
                claimed = (
                    await s2.execute(
                        select(Run)
                        .where(Run.id == target_run_id, Run.status == RunStatus.QUEUED)
                        .with_for_update(skip_locked=True)
                    )
                ).scalar_one_or_none()
                if claimed is not None:
                    claimed.status = RunStatus.STARTING
                    await s2.commit()
                    return claimed.id
                return None
        finally:
            second_checked_event.set()

    res1, res2 = await asyncio.gather(_worker_1(), _worker_2())
    claimed_ids = [r for r in (res1, res2) if r is not None]
    assert claimed_ids == [target_run_id], (
        f"Expected exactly one worker to claim {target_run_id}, got res1={res1}, res2={res2}"
    )
    assert (res1 is None) ^ (res2 is None)

    async with pg_session_factory() as verify_session:
        db_run = await verify_session.get(Run, target_run_id)
        assert db_run is not None
        assert db_run.status == RunStatus.STARTING


# ==============================================================================
# Test 2: PostgreSQL transaction-level advisory lock (pg_try_advisory_xact_lock)
# ==============================================================================
@pytest.mark.asyncio
async def test_postgres_advisory_xact_lock_mutual_exclusion(
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Two concurrent sessions contend on pg_try_advisory_xact_lock; second succeeds only after first commits."""
    lock_stmt = text("SELECT pg_try_advisory_xact_lock(:lock_id)").bindparams(
        lock_id=_SCHEDULER_ADVISORY_LOCK_ID
    )

    async with pg_session_factory() as session_a, pg_session_factory() as session_b:
        # Session A acquires the transaction-level advisory lock
        locked_a = await session_a.scalar(lock_stmt)
        assert locked_a is True

        # While Session A's transaction is open, Session B cannot acquire the same advisory lock
        locked_b_while_a_open = await session_b.scalar(lock_stmt)
        assert locked_b_while_a_open is False

        # Once Session A commits its transaction, the xact lock is automatically released
        await session_a.commit()

        # Now Session B can acquire the advisory lock
        locked_b_after_a_commit = await session_b.scalar(lock_stmt)
        assert locked_b_after_a_commit is True
        await session_b.commit()


# ==============================================================================
# Test 3: Commit-before-spawn visibility for immediate runner /context requests
# ==============================================================================
@pytest.mark.asyncio
async def test_postgres_commit_before_spawn_visibility(
    pg_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Committing RunStatus.STARTING before container spawn makes the run immediately visible to /runner/context."""
    async with pg_session_factory() as scheduler_session:
        _, _, _, run = await _seed_prerequisite_rows(
            scheduler_session, suffix="vis03", run_status=RunStatus.QUEUED
        )
        run_id = run.id
        prepare_token = create_run_token(run_id, scope="prepare")

        # Before scheduler marks STARTING and commits, runner authentication rejects QUEUED run
        async with pg_session_factory() as early_runner_session:
            with pytest.raises(Exception, match="is not active"):
                await _authenticate_active_run(early_runner_session, prepare_token)

        # Scheduler claims the run, transitions to STARTING, and commits BEFORE spawning the container
        locked_run = (
            await scheduler_session.execute(
                select(Run)
                .where(Run.id == run_id, Run.status == RunStatus.QUEUED)
                .with_for_update(skip_locked=True)
            )
        ).scalar_one()
        locked_run.status = RunStatus.STARTING
        locked_run.updated_at = datetime.now(UTC)
        await scheduler_session.commit()

        # Concurrent session (simulating runner container calling GET /api/v1/runner/context immediately on startup)
        # immediately observes RunStatus.STARTING
        async with pg_session_factory() as runner_session:
            authed_run, authed_seed, authed_project, authed_agent = (
                await _authenticate_active_run(runner_session, prepare_token)
            )
            assert authed_run.id == run_id
            assert authed_run.status == RunStatus.STARTING
            assert authed_seed.id == run.seed_id
            assert authed_project.slug == "proj-vis03"
            assert authed_agent.handle == "coder-vis03"
