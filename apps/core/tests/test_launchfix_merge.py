"""Regression tests for Workstream 4 (H4 failed merge not marked MERGED, H5 merge readiness fail-closed & SHA pinning, update_run cost protection, close_seed run cancellation)."""

from __future__ import annotations

import dataclasses
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from gitseed_common.enums import (
    BackendType,
    OrgRole,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import ProjectRepository
from gitseed_core.services import git_merge as git_merge_service
from gitseed_core.services import git_pr_providers as pr_providers
from gitseed_core.services import runs as runs_service
from gitseed_core.services import seeds as seeds_service
from gitseed_core.services.errors import Conflict, Forbidden

from conftest import (
    _exec_result,
    _make_policy,
    _make_project,
    _make_run,
    _make_seed,
    _make_session,
    _make_user,
)


@pytest.mark.asyncio
async def test_h4_failed_merge_does_not_mark_seed_merged(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    project = _make_project("prj_m1")
    seed = _make_seed("sed_m1", project.id)
    seed.stage = SeedStage.MERGE
    seed.status = SeedStatus.RUNNING
    seed.autonomy = SeedAutonomy.AUTONOMOUS
    run = _make_run("run_m1", seed.id)
    run.stage = SeedStage.MERGE
    run.status = RunStatus.SUCCEEDED

    pol = dataclasses.replace(
        _make_policy(SeedStage.MERGE, project.id), requires_approval=False
    )

    monkeypatch.setattr(
        runs_service,
        "check_seed_pull_request_status",
        AsyncMock(return_value={"state": "open", "ready_to_merge": True, "merged": False}),
    )
    monkeypatch.setattr(
        runs_service,
        "merge_seed_branch_into_default",
        AsyncMock(return_value=None),
    )

    await runs_service.advance_seed_after_succeeded_run(
        session,
        seed=seed,
        run=run,
        pol=pol,
    )
    assert seed.status == SeedStatus.AWAITING_APPROVAL
    assert seed.merged_at is None


@pytest.mark.asyncio
async def test_h5_check_seed_pr_status_fails_closed_when_backend_unavailable() -> None:
    session = _make_session()
    project = _make_project("prj_m2")
    seed = _make_seed("sed_m2", project.id)
    repo = ProjectRepository(
        id=1,
        project_id=project.id,
        backend_id="bnd_disabled",
        full_name="acme/alpha",
        default_branch="main",
        is_primary=True,
    )
    disabled_backend = Backend(
        id="bnd_disabled",
        name="Disabled GitHub",
        type=BackendType.GIT,
        provider="github",
        enabled=False,
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[repo]))
    session.get = AsyncMock(return_value=disabled_backend)

    info = await git_merge_service.check_seed_pull_request_status(
        session, seed, project, branch_name="gitseed/alpha/seed-1"
    )
    assert info["ready_to_merge"] is False
    assert info["block_reason"] is not None


@pytest.mark.asyncio
async def test_h5_manual_seed_not_auto_merged_without_human_approval(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    project = _make_project("prj_m3")
    seed = _make_seed("sed_m3", project.id)
    seed.stage = SeedStage.MERGE
    seed.status = SeedStatus.AWAITING_APPROVAL
    seed.autonomy = SeedAutonomy.MANUAL

    pol = dataclasses.replace(
        _make_policy(SeedStage.MERGE, project.id), requires_approval=False
    )

    monkeypatch.setattr(
        git_merge_service,
        "get_effective_stage_policy",
        AsyncMock(return_value=pol),
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))

    merged = await git_merge_service.maybe_auto_merge_ready_pr(
        session,
        seed,
        project,
        pr_info={"state": "open", "ready_to_merge": True},
    )
    assert merged is False
    assert seed.status == SeedStatus.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_h5_github_pr_merge_pins_head_sha_and_does_not_fallback_on_open_pr() -> None:
    put_payloads: list[dict[str, Any]] = []
    post_called = False

    class _MockClient:
        async def get(self, url: str, **kwargs: Any) -> Any:
            resp = MagicMock()
            resp.status_code = 200
            resp.json.return_value = [
                {
                    "number": 42,
                    "state": "open",
                    "base": {"ref": "main"},
                    "head": {"ref": "gitseed/alpha/seed-1", "sha": "deadbeef" * 5},
                }
            ]
            return resp

        async def put(self, url: str, **kwargs: Any) -> Any:
            put_payloads.append(kwargs.get("json") or {})
            resp = MagicMock()
            resp.status_code = 409
            resp.json.return_value = {"message": "Head branch was modified"}
            return resp

        async def post(self, url: str, **kwargs: Any) -> Any:
            nonlocal post_called
            post_called = True
            return MagicMock(status_code=201)

    backend = Backend(
        id="bnd_gh",
        name="GitHub",
        type=BackendType.GIT,
        provider="github",
        endpoint="https://api.github.com",
        enabled=True,
    )
    repo = ProjectRepository(
        id=1,
        project_id="prj_1",
        backend_id=backend.id,
        full_name="acme/alpha",
        default_branch="main",
        is_primary=True,
    )

    with pytest.raises(Conflict, match="Head branch was modified"):
        await pr_providers.merge_provider_pull_request(
            _MockClient(),  # type: ignore[arg-type]
            provider="github",
            backend=backend,
            repo=repo,
            token="ghp_tok",
            default_branch="main",
            seed_branch="gitseed/alpha/seed-1",
            merge_label="gitseed/alpha/seed-1",
        )
    assert len(put_payloads) == 1
    assert put_payloads[0].get("sha") == "deadbeef" * 5
    assert post_called is False


@pytest.mark.asyncio
async def test_non_admin_developer_cannot_override_run_cost_or_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    developer = _make_user("usr_dev", OrgRole.MEMBER)
    project = _make_project("prj_r1")
    seed = _make_seed("sed_r1", project.id)
    run = _make_run("run_r1", seed.id)

    monkeypatch.setattr(runs_service, "get_run", AsyncMock(return_value=run))
    monkeypatch.setattr(runs_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(runs_service, "_require_run_developer", AsyncMock(return_value=project))

    with pytest.raises(Forbidden, match="Organization admin privileges required"):
        await runs_service.update_run(
            session,
            developer,
            run.id,
            cost=Decimal("0.00"),
            fields_set={"cost"},
        )


@pytest.mark.asyncio
async def test_close_seed_cancels_active_runs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    project = _make_project("prj_c1")
    seed = _make_seed("sed_c1", project.id)
    seed.status = SeedStatus.RUNNING
    active_run = _make_run("run_c1", seed.id)
    active_run.status = RunStatus.RUNNING

    monkeypatch.setattr(seeds_service, "get_seed", AsyncMock(return_value=seed))
    monkeypatch.setattr(
        seeds_service, "_require_seed_developer", AsyncMock(return_value=(project, None))
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[active_run]))
    cancel_mock = AsyncMock()
    monkeypatch.setattr("gitseed_core.services.runs.cancel_run", cancel_mock)

    await seeds_service.close_seed(session, admin, seed.id)
    assert seed.status == SeedStatus.CLOSED
    cancel_mock.assert_awaited_once_with(session, admin, active_run.id)
