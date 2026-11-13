"""Regression tests for Workstream 2 (runner_bridge, compute_runtime, egress_proxy, net_safety, git_auth)."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gitseed_common.enums import (
    BackendType,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from gitseed_core.models.agent import Agent
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.schemas.runner import (
    RunnerCompleteRequest,
    RunnerPrepareCompleteRequest,
)
from gitseed_core.security import create_run_token, encrypt_secrets
from gitseed_core.services.errors import Invalid, Unauthorized
from gitseed_core.services.git_auth import resolve_git_backend_token
from gitseed_core.services.net_safety import is_public_ip
from gitseed_core.services.runner_bridge import (
    complete_runner_execution,
    complete_runner_prepare,
    resolve_runner_attachment,
)

from conftest import (
    _exec_result,
    _make_agent,
    _make_policy,
    _make_project,
    _make_run,
    _make_seed,
    _make_session,
)


def _setup_bridge_session(
    run: Run,
    seed: Seed,
    project: Project,
    agent: Agent,
    repos: list[ProjectRepository] | None = None,
) -> AsyncMock:
    session = _make_session()
    repo_list = repos or []

    async def _get(model_cls: Any, ident: Any) -> Any:
        if model_cls is Run and ident == run.id:
            return run
        if model_cls is Seed and ident == seed.id:
            return seed
        if model_cls is Project and ident == project.id:
            return project
        if model_cls is Agent and ident == agent.id:
            return agent
        return None

    async def _execute(stmt: Any, *_args: Any, **_kwargs: Any) -> MagicMock:
        sql_str = str(stmt).lower()
        if "project_repository" in sql_str:
            return _exec_result(
                scalar_one_or_none=repo_list[0] if repo_list else None,
                scalars_all=repo_list,
                scalars_first=repo_list[0] if repo_list else None,
            )
        if "from runs" in sql_str:
            return _exec_result(scalar_one_or_none=run, scalars_all=[run], scalars_first=run)
        if "from seeds" in sql_str:
            return _exec_result(scalar_one_or_none=seed, scalars_all=[seed], scalars_first=seed)
        return _exec_result()

    session.get.side_effect = _get
    session.execute.side_effect = _execute
    return session


@pytest.mark.asyncio
async def test_prepare_complete_validates_seed_branch_and_repo_heads() -> None:
    project = _make_project("prj_lf1")
    project.slug = "alpha"
    seed = _make_seed("sed_lf1", project.id)
    seed.number = 7
    seed.stage = SeedStage.EXECUTE
    seed.status = SeedStatus.RUNNING
    agent = _make_agent("agt_lf1")
    run = _make_run("run_lf1", seed.id)
    run.stage = SeedStage.EXECUTE
    run.status = RunStatus.RUNNING
    run.agent_id = agent.id
    repo = ProjectRepository(
        id=1,
        project_id=project.id,
        backend_id="bnd_git1",
        full_name="acme/alpha",
        default_branch="main",
        is_primary=True,
    )
    session = _setup_bridge_session(run, seed, project, agent, repos=[repo])
    prep_tok = create_run_token(run.id, scope="prepare")

    # Rejects protected or mismatched branch names
    with pytest.raises(Invalid, match="seed_branch"):
        await complete_runner_prepare(
            session,
            prep_tok,
            RunnerPrepareCompleteRequest(
                base_heads={"acme/alpha": "a" * 40},
                seed_branch="main",
            ),
        )

    # Rejects unattached repository in base_heads
    with pytest.raises(Invalid, match="not a repository of this project"):
        await complete_runner_prepare(
            session,
            prep_tok,
            RunnerPrepareCompleteRequest(
                base_heads={"evil/other-repo": "a" * 40},
                seed_branch="gitseed/alpha/seed-7",
            ),
        )

    # Rejects non-hex SHA values
    with pytest.raises(Invalid, match="not a valid Git object SHA"):
        await complete_runner_prepare(
            session,
            prep_tok,
            RunnerPrepareCompleteRequest(
                base_heads={"acme/alpha": "--upload-pack=evil"},
                seed_branch="gitseed/alpha/seed-7",
            ),
        )


@pytest.mark.asyncio
async def test_late_run_completion_does_not_revive_closed_or_advanced_seed() -> None:
    project = _make_project("prj_lf2")
    project.slug = "alpha"
    seed = _make_seed("sed_lf2", project.id)
    seed.number = 2
    seed.stage = SeedStage.EXECUTE
    seed.status = SeedStatus.CLOSED
    seed.autonomy = SeedAutonomy.AUTONOMOUS
    agent = _make_agent("agt_lf2")
    run = _make_run("run_lf2", seed.id)
    run.stage = SeedStage.EXECUTE
    run.status = RunStatus.RUNNING
    run.agent_id = agent.id
    session = _setup_bridge_session(run, seed, project, agent)
    fin_tok = create_run_token(run.id, scope="finalize")

    with (
        patch(
            "gitseed_core.services.runner_bridge.get_effective_stage_policy",
            AsyncMock(return_value=_make_policy(SeedStage.EXECUTE, project.id)),
        ),
        patch("gitseed_core.services.runner_bridge.finalize_run_litellm_key", AsyncMock()),
        patch(
            "gitseed_core.services.runner_bridge.advance_seed_after_succeeded_run",
            AsyncMock(),
        ) as adv_mock,
    ):
        await complete_runner_execution(
            session,
            fin_tok,
            RunnerCompleteRequest(
                status=RunStatus.SUCCEEDED,
                exit_code=0,
                stage_output={"summary_comment": "Completed work"},
            ),
        )
        assert run.status == RunStatus.SUCCEEDED
        assert seed.status == SeedStatus.CLOSED
        adv_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_runner_attachment_rejects_terminal_run() -> None:
    project = _make_project("prj_lf3")
    project.slug = "alpha"
    seed = _make_seed("sed_lf3", project.id)
    seed.number = 3
    seed.stage = SeedStage.EXECUTE
    seed.status = SeedStatus.RUNNING
    agent = _make_agent("agt_lf3")
    run = _make_run("run_lf3", seed.id)
    run.stage = SeedStage.EXECUTE
    run.status = RunStatus.SUCCEEDED
    run.agent_id = agent.id
    session = _setup_bridge_session(run, seed, project, agent)
    agent_tok = create_run_token(run.id, scope="agent")

    with pytest.raises(Unauthorized, match="not active"):
        await resolve_runner_attachment(session, agent_tok, "att_123")


def test_net_safety_blocks_nat64_and_ipv4_compatible_ipv6() -> None:
    # NAT64 well-known prefix 64:ff9b::/96 embedding 169.254.169.254 (a9fe:a9fe)
    assert is_public_ip("64:ff9b::a9fe:a9fe") is False
    # NAT64 local-use prefix 64:ff9b:1::/48
    assert is_public_ip("64:ff9b:1::10.0.0.1") is False
    # Deprecated IPv4-compatible IPv6 ::a.b.c.d
    assert is_public_ip("::10.0.0.1") is False
    assert is_public_ip("::169.254.169.254") is False
    # Public IPv4 and IPv6 remain allowed
    assert is_public_ip("93.184.216.34") is True
    assert is_public_ip("2606:2800:220:1:248:1893:25c8:1946") is True


@pytest.mark.asyncio
async def test_git_auth_prefers_read_token_when_write_access_false() -> None:
    backend = Backend(
        id="bnd_pat1",
        name="GitHub PAT",
        type=BackendType.GIT,
        provider="github",
        config={"auth_method": "pat"},
        secrets_encrypted=encrypt_secrets(
            {"token": "ghp_writeToken", "read_token": "ghp_readOnlyToken"}
        ),
    )
    read_tok = await resolve_git_backend_token(
        backend, repo_full_names=["acme/repo"], write_access=False
    )
    write_tok = await resolve_git_backend_token(
        backend, repo_full_names=["acme/repo"], write_access=True
    )
    assert read_tok == "ghp_readOnlyToken"
    assert write_tok == "ghp_writeToken"
