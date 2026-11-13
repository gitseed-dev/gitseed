"""Regression tests for Workstream 5 (scheduler retry count after reset, gateway checkpoint budget calculation, key deletion in finally)."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from gitseed_common.enums import RunStatus, SeedStage, SeedStatus
from gitseed_core.services import gateway as gateway_service
from gitseed_core.services import scheduler as scheduler_service

from conftest import (
    _make_policy,
    _make_project,
    _make_run,
    _make_seed,
    _make_session,
)


@pytest.mark.asyncio
async def test_checkpoint_approved_through_caps_at_exact_approved_amount() -> None:
    session = _make_session()
    session.get = AsyncMock(return_value=None)
    session.scalar = AsyncMock(return_value=Decimal("0"))

    project = _make_project("prj_cp1")
    project.spend_checkpoint = Decimal("50.00")
    project.seed_budget_cap = Decimal("500.00")
    project.monthly_budget = Decimal("1000.00")

    seed = _make_seed("sed_cp1", project.id)
    seed.spent = Decimal("50.00")
    seed.checkpoint_approved_through = Decimal("75.00")

    rem, blocked, _ = await gateway_service._compute_remaining_budget(
        session, seed, project
    )
    assert blocked is None
    assert rem == Decimal("25.00")

    # Once spent reaches approved_through ($75), it blocks again
    seed.spent = Decimal("75.00")
    rem2, blocked2, _ = await gateway_service._compute_remaining_budget(
        session, seed, project
    )
    assert rem2 == Decimal("0")
    assert blocked2 is not None
    assert "checkpoint" in blocked2.lower()


@pytest.mark.asyncio
async def test_finalize_run_litellm_key_deletes_key_even_when_info_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deleted_urls: list[str] = []

    class _FailingInfoClient:
        def __init__(self, **kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FailingInfoClient:
            return self

        async def __aexit__(self, *args: Any) -> None:
            return None

        async def get(self, url: str, **kwargs: Any) -> Any:
            raise RuntimeError("LiteLLM /key/info connection error")

        async def post(self, url: str, **kwargs: Any) -> Any:
            deleted_urls.append(url)
            return AsyncMock(status_code=200)

    monkeypatch.setattr(gateway_service.httpx, "AsyncClient", _FailingInfoClient)

    session = _make_session()
    project = _make_project("prj_fk1")
    seed = _make_seed("sed_fk1", project.id)
    run = _make_run("run_fk1", seed.id)

    await gateway_service.finalize_run_litellm_key(
        session, run, seed, project, delete_key=True
    )
    assert any(u.endswith("/key/delete") for u in deleted_urls)


@pytest.mark.asyncio
async def test_reconcile_seed_after_run_failure_uses_since_reset_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    project = _make_project("prj_rf1")
    seed = _make_seed("sed_rf1", project.id)
    seed.stage = SeedStage.EXECUTE
    seed.status = SeedStatus.RUNNING
    # Historical attempt is 4 (> max_attempts=3), but since last reset only 1 failure occurred
    run = _make_run("run_rf1", seed.id)
    run.stage = SeedStage.EXECUTE
    run.attempt = 4
    run.status = RunStatus.TIMED_OUT

    pol = _make_policy(SeedStage.EXECUTE, project.id)
    monkeypatch.setattr(
        scheduler_service,
        "_latest_stage_reset_timestamp",
        AsyncMock(return_value=datetime.now(UTC)),
    )
    session.scalar = AsyncMock(return_value=1)

    await scheduler_service._reconcile_seed_after_run_failure(
        session, seed, run, pol, datetime.now(UTC)
    )
    assert seed.status == SeedStatus.QUEUED

