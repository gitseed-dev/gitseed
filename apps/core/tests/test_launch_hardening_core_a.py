from __future__ import annotations

import asyncio
import copy
import dataclasses
import hashlib
import json
import stat
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from gitseed_common.enums import (
    AgentRuntime,
    ApprovalDecision,
    BackendType,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from gitseed_core import bootstrap as bootstrap_mod
from gitseed_core.api import deps as deps_mod
from gitseed_core.config import settings
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.seed import SeedTask
from gitseed_core.schemas.runner import (
    RunnerCliAuthRefreshRequest,
    RunnerCompleteRequest,
)
from gitseed_core.security import create_run_token, decrypt_secrets, encrypt_secrets
from gitseed_core.services import (
    agent_sessions as agent_sessions_mod,
)
from gitseed_core.services import (
    backends as backends_mod,
)
from gitseed_core.services import (
    gateway as gateway_mod,
)
from gitseed_core.services import (
    receipts as receipts_mod,
)
from gitseed_core.services import (
    runner_bridge as runner_bridge_mod,
)
from gitseed_core.services import (
    runs as runs_mod,
)
from gitseed_core.services import (
    sandboxes as sandboxes_mod,
)
from gitseed_core.services import (
    scheduler as scheduler_mod,
)
from gitseed_core.services import (
    seeds as seeds_mod,
)
from gitseed_core.services.errors import Forbidden, Invalid

from conftest import (
    _exec_result,
    _make_agent,
    _make_backend,
    _make_policy,
    _make_project,
    _make_run,
    _make_seed,
    _make_session,
    _now,
)

# ---------------------------------------------------------------------------
# 1. Governance Receipts Security, Seed Ref Lookup, Artifacts & Unpriced Cost
# ---------------------------------------------------------------------------


def test_verify_governance_receipt_requires_explicit_trusted_key() -> None:
    sample_payload = {
        "schema_version": "gitseed.receipt.v1",
        "seed": {"id": "sed_1", "number": 1},
        "generated_at": _now().isoformat(),
    }
    envelope = receipts_mod.sign_governance_receipt(
        sample_payload,
        secret_key="trusted-signing-secret-key-for-tests-only-32chars",
    )
    assert envelope.get("public_key_pem")

    # Never trust envelope["public_key_pem"] when public_key_pem=None
    res_none = receipts_mod.verify_governance_receipt(envelope, public_key_pem=None)
    assert res_none["valid"] is False
    assert bool(res_none) is False
    assert "Trusted public key is required" in str(res_none.get("error"))

    res_omitted = receipts_mod.verify_governance_receipt(envelope)
    assert res_omitted["valid"] is False
    assert bool(res_omitted) is False

    # Explicit trusted public_key_pem or trusted_public_keys succeeds
    trusted_pem = str(envelope["public_key_pem"])
    res_ok = receipts_mod.verify_governance_receipt(envelope, public_key_pem=trusted_pem)
    assert res_ok["valid"] is True
    assert bool(res_ok) is True

    res_list = receipts_mod.verify_governance_receipt(
        envelope, trusted_public_keys=[trusted_pem]
    )
    assert res_list["valid"] is True

    # Different untrusted Ed25519 key must be rejected even when envelope embeds attacker key
    other_info = receipts_mod.get_receipt_signing_public_key_info(
        secret_key="different-untrusted-signing-secret-32chars-xyz"
    )
    res_wrong = receipts_mod.verify_governance_receipt(
        envelope, public_key_pem=str(other_info["public_key_pem"])
    )
    assert res_wrong["valid"] is False


@pytest.mark.asyncio
async def test_build_seed_governance_receipt_attests_artifacts_and_unpriced(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    project = _make_project("prj_rcpt")
    seed = _make_seed("sed_rcpt", project.id)
    seed.specification = "## Locked Specification\nEnforce Ed25519 receipt attestation."
    seed.acceptance_criteria = ["Verify spec hash", "Verify task ordering"]
    seed.plan = "1. Hash spec\n2. Include ordered tasks"

    task_b = SeedTask(
        id="tsk_second",
        seed_id=seed.id,
        position=1,
        title="Implement verification",
        description="Step 2",
        status=TaskStatus.DONE,
    )
    task_a = SeedTask(
        id="tsk_first",
        seed_id=seed.id,
        position=0,
        title="Update schema",
        description="Step 1",
        status=TaskStatus.DONE,
    )

    local_backend = _make_backend("bnd_ollama", BackendType.INFERENCE)
    local_backend.provider = "ollama"
    local_backend.config = {"models": [{"id": "qwen3-coder", "enabled": True}]}

    unpriced_run = _make_run("run_ollama", seed.id)
    unpriced_run.stage = SeedStage.IDEATE
    unpriced_run.model = "ollama/qwen3-coder"
    unpriced_run.status = RunStatus.SUCCEEDED
    unpriced_run.cost = Decimal("0")
    unpriced_run.output = {"models_backend_id": local_backend.id}

    oauth_run = _make_run("run_oauth", seed.id)
    oauth_run.stage = SeedStage.PLAN
    oauth_run.runtime = AgentRuntime.CLAUDE_CODE
    oauth_run.model = "claude-sonnet-4-6"
    oauth_run.status = RunStatus.SUCCEEDED
    oauth_run.cost = Decimal("0.0420")
    oauth_run.output = {
        "auth_mode": "cli_oauth",
        "cost_enforcement": "estimated",
    }

    async def _fake_get(model_cls: Any, key: Any) -> Any:
        if model_cls is type(seed) and key == seed.id:
            return seed
        if model_cls is type(project) and key == project.id:
            return project
        if model_cls is Backend and key == local_backend.id:
            return local_backend
        return None

    session.get = AsyncMock(side_effect=_fake_get)
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[unpriced_run, oauth_run]),  # Run rows
            _exec_result(scalars_all=[]),  # Approval rows
            _exec_result(scalars_all=[local_backend]),  # Inference Backend rows
            _exec_result(scalars_all=[task_b, task_a]),  # SeedTask rows
        ]
    )
    monkeypatch.setattr(
        receipts_mod,
        "get_effective_stage_policy",
        AsyncMock(return_value=_make_policy(SeedStage.PLAN, project.id)),
    )

    envelope = await receipts_mod.build_seed_governance_receipt(session, seed.id)
    trusted_pem = str(envelope["public_key_pem"])
    verified = receipts_mod.verify_governance_receipt(envelope, public_key_pem=trusted_pem)
    assert verified["valid"] is True

    artifacts = envelope["receipt"]["artifacts"]
    assert (
        artifacts["specification_sha256"]
        == hashlib.sha256(seed.specification.encode("utf-8")).hexdigest()
    )
    expected_ac = "\n".join(seed.acceptance_criteria)
    assert (
        artifacts["acceptance_criteria_sha256"]
        == hashlib.sha256(expected_ac.encode("utf-8")).hexdigest()
    )
    assert (
        artifacts["plan_sha256"]
        == hashlib.sha256(seed.plan.encode("utf-8")).hexdigest()
    )
    assert artifacts["task_count"] == 2
    assert [t["id"] for t in artifacts["tasks"]] == ["tsk_first", "tsk_second"]
    assert [t["order_index"] for t in artifacts["tasks"]] == [0, 1]

    runs_info = {r["id"]: r for r in envelope["receipt"]["runs"]}
    assert runs_info["run_ollama"]["cost_enforcement"] == "unpriced"
    assert runs_info["run_oauth"]["cost_enforcement"] == "estimated"

    # Tampering with attested artifact hashes invalidates Ed25519 verification
    tampered = copy.deepcopy(envelope)
    tampered["receipt"]["artifacts"]["plan_sha256"] = "0" * 64
    tampered_res = receipts_mod.verify_governance_receipt(
        tampered, public_key_pem=trusted_pem
    )
    assert tampered_res["valid"] is False


@pytest.mark.asyncio
async def test_receipt_endpoint_resolves_project_slug_number_ref(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seed = _make_seed("sed_resolved_1", "prj_test123456")
    get_seed_mock = AsyncMock(return_value=seed)
    sample_env = receipts_mod.sign_governance_receipt(
        {
            "schema_version": "gitseed.receipt.v1",
            "seed": {
                "id": seed.id,
                "number": 1,
                "title": seed.title,
                "project_id": seed.project_id,
                "project_slug": "alpha-project",
                "status": "queued",
                "stage": "plan",
            },
            "policy": {
                "autonomy": "supervised",
                "min_approver_role": "developer",
                "prevent_self_approval": False,
                "blocked_paths": [],
            },
            "artifacts": {
                "specification_sha256": "a" * 64,
                "acceptance_criteria_sha256": None,
                "plan_sha256": None,
                "task_count": 0,
                "tasks": [],
            },
            "runs": [],
            "approvals": [],
            "generated_at": _now().isoformat(),
        },
        secret_key="endpoint-receipt-signing-secret-key-32chars",
    )
    build_mock = AsyncMock(return_value=sample_env)
    monkeypatch.setattr(seeds_mod, "get_seed", get_seed_mock)
    monkeypatch.setattr(
        "gitseed_core.api.v1.receipts.build_seed_governance_receipt", build_mock
    )

    resp = await client.get("/api/v1/seeds/alpha-project%231/receipt")
    assert resp.status_code == 200
    assert get_seed_mock.await_args is not None
    assert get_seed_mock.await_args.args[2] == "alpha-project#1"
    build_mock.assert_awaited_once()
    assert build_mock.await_args is not None
    assert build_mock.await_args.args[1] == "sed_resolved_1"


# ---------------------------------------------------------------------------
# 2. Fail-Closed backends_restricted Across gateway, scheduler, and sandboxes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_backends_restricted_fails_closed_when_grants_empty() -> None:
    project = _make_project("prj_restricted")
    project.backends_restricted = True

    inf_backend = _make_backend("bnd_org_inf", BackendType.INFERENCE)
    inf_backend.provider = "openai"
    inf_backend.secrets_encrypted = encrypt_secrets({"api_key": "sk-org-secret-key"})
    inf_backend.config = {"models": [{"id": "gpt-5.4", "enabled": True}]}

    comp_backend = _make_backend("bnd_org_comp", BackendType.COMPUTE)
    comp_backend.provider = "docker"
    comp_backend.endpoint = "http://docker-proxy:2375"
    comp_backend.capacity = 4

    run = _make_run("run_rest", "sed_test123456")

    # 2a. gateway.resolve_models_backend_for_run fails closed
    gw_session = _make_session()
    gw_session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[inf_backend]),  # All enabled inference backends
            _exec_result(scalars_all=[]),  # ProjectBackend grants (empty!)
        ]
    )
    with pytest.raises(Invalid, match="backends_restricted"):
        await gateway_mod.resolve_models_backend_for_run(gw_session, run, project)

    # 2b. scheduler._select_compute_backend fails closed (returns None, never org backend)
    sched_session = _make_session()
    sched_session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    chosen = await scheduler_mod._select_compute_backend(
        sched_session,
        run,
        project,
        compute_backends=[comp_backend],
        active_by_backend={},
    )
    assert chosen is None

    # 2c. sandboxes._select_docker_compute_backend fails closed
    sbx_session = _make_session()
    sbx_session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[comp_backend]),  # All enabled compute backends
            _exec_result(scalars_all=[]),  # ProjectBackend grants (empty!)
        ]
    )
    with pytest.raises(Invalid, match="backends_restricted"):
        await sandboxes_mod._select_docker_compute_backend(sbx_session, project=project)


# ---------------------------------------------------------------------------
# 3. Fast-Track (Ideate -> Plan) Stage Progression Without Duplicate IDEATE
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fast_track_ideate_to_plan_progression_without_duplicate_ideate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project("prj_ft")
    agent = _make_agent("agt_arch123456")

    ideate_pol = dataclasses.replace(
        _make_policy(SeedStage.IDEATE, project.id),
        requires_approval=False,
    )

    # Case A: PLAN requires human approval -> pauses at PLAN in AWAITING_APPROVAL with tasks intact
    seed_gated = _make_seed("sed_ft_gated", project.id)
    seed_gated.stage = SeedStage.IDEATE
    seed_gated.status = SeedStatus.RUNNING
    seed_gated.autonomy = SeedAutonomy.SUPERVISED

    run_gated = _make_run("run_ft_gated", seed_gated.id)
    run_gated.stage = SeedStage.IDEATE
    run_gated.status = RunStatus.RUNNING
    run_gated.agent_id = agent.id

    plan_pol_gated = dataclasses.replace(
        _make_policy(SeedStage.PLAN, project.id),
        requires_approval=True,
        allow_auto_approve=False,
    )

    added_gated: list[Any] = []
    session_gated = _make_session()
    session_gated.add = MagicMock(side_effect=added_gated.append)

    async def _get_gated(model_cls: Any, key: Any) -> Any:
        if key == run_gated.id:
            return run_gated
        if key == seed_gated.id:
            return seed_gated
        if key == project.id:
            return project
        if key == agent.id:
            return agent
        return None

    session_gated.get = AsyncMock(side_effect=_get_gated)
    session_gated.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))

    async def _pol_gated(
        _s: Any, _pid: str, st: SeedStage
    ) -> Any:
        return ideate_pol if st == SeedStage.IDEATE else plan_pol_gated

    monkeypatch.setattr(runner_bridge_mod, "get_effective_stage_policy", _pol_gated)
    monkeypatch.setattr(runner_bridge_mod, "finalize_run_litellm_key", AsyncMock(return_value=Decimal("0")))
    monkeypatch.setattr(runs_mod, "list_stage_cycle_runs", AsyncMock(return_value=[run_gated]))
    monkeypatch.setattr(
        runs_mod,
        "next_enabled_stage",
        AsyncMock(
            side_effect=lambda _s, _p, st: (
                SeedStage.PLAN if st == SeedStage.IDEATE else SeedStage.EXECUTE
            )
        ),
    )
    monkeypatch.setattr(runs_mod, "build_stage_approval_snapshot", AsyncMock(return_value={}))

    ft_payload = RunnerCompleteRequest(
        status=RunStatus.SUCCEEDED,
        exit_code=0,
        input_tokens=500,
        output_tokens=200,
        stage_output={
            "specification": "Detailed fast-track technical specification.",
            "acceptance_criteria": ["Criterion A", "Criterion B"],
            "summary_comment": "Completed ideation and fast-tracked plan.",
            "fast_tracked_plan": {
                "plan": "1. Implement core fix\n2. Add unit tests",
                "tasks": [
                    {
                        "title": "Fix fast-track stage override",
                        "description": "Pass completed_stage_override=SeedStage.PLAN",
                        "target_files": ["apps/core/src/gitseed_core/services/runner_bridge.py"],
                    }
                ],
                "summary_comment": "Generated 1 concrete implementation task.",
            },
        },
    )
    tok_gated = create_run_token(run_gated.id, scope="finalize")
    await runner_bridge_mod.complete_runner_execution(session_gated, tok_gated, ft_payload)

    assert seed_gated.stage == SeedStage.PLAN
    assert seed_gated.status == SeedStatus.AWAITING_APPROVAL
    created_tasks = [obj for obj in added_gated if isinstance(obj, SeedTask)]
    assert len(created_tasks) == 1
    assert created_tasks[0].title == "Fix fast-track stage override"
    approvals_gated = [obj for obj in added_gated if isinstance(obj, Approval)]
    assert len(approvals_gated) == 0

    # Case B: AUTONOMOUS with auto-approval on IDEATE and PLAN -> 1 IDEATE approval, 1 PLAN approval, advances to EXECUTE
    seed_auto = _make_seed("sed_ft_auto", project.id)
    seed_auto.stage = SeedStage.IDEATE
    seed_auto.status = SeedStatus.RUNNING
    seed_auto.autonomy = SeedAutonomy.AUTONOMOUS

    run_auto = _make_run("run_ft_auto", seed_auto.id)
    run_auto.stage = SeedStage.IDEATE
    run_auto.status = RunStatus.RUNNING
    run_auto.agent_id = agent.id

    ideate_pol_auto = dataclasses.replace(
        _make_policy(SeedStage.IDEATE, project.id),
        requires_approval=True,
        allow_auto_approve=True,
    )

    plan_pol_auto = dataclasses.replace(
        _make_policy(SeedStage.PLAN, project.id),
        requires_approval=True,
        allow_auto_approve=True,
    )

    added_auto: list[Any] = []
    session_auto = _make_session()
    session_auto.add = MagicMock(side_effect=added_auto.append)

    async def _get_auto(model_cls: Any, key: Any) -> Any:
        if key == run_auto.id:
            return run_auto
        if key == seed_auto.id:
            return seed_auto
        if key == project.id:
            return project
        if key == agent.id:
            return agent
        return None

    session_auto.get = AsyncMock(side_effect=_get_auto)
    session_auto.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))

    async def _pol_auto(_s: Any, _pid: str, st: SeedStage) -> Any:
        return ideate_pol_auto if st == SeedStage.IDEATE else plan_pol_auto

    monkeypatch.setattr(runner_bridge_mod, "get_effective_stage_policy", _pol_auto)
    monkeypatch.setattr(runs_mod, "list_stage_cycle_runs", AsyncMock(return_value=[run_auto]))

    tok_auto = create_run_token(run_auto.id, scope="finalize")
    await runner_bridge_mod.complete_runner_execution(session_auto, tok_auto, ft_payload)

    approvals_auto = [obj for obj in added_auto if isinstance(obj, Approval)]
    assert [a.stage for a in approvals_auto] == [SeedStage.IDEATE, SeedStage.PLAN]
    assert all(a.decision == ApprovalDecision.APPROVED for a in approvals_auto)
    assert seed_auto.stage == SeedStage.EXECUTE
    assert seed_auto.status == SeedStatus.QUEUED


# ---------------------------------------------------------------------------
# 4. Preserve cost_enforcement & auth_mode, Estimate cli_oauth Cost, Redact & Harden CLI Auth
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_runner_execution_preserves_cli_oauth_and_estimates_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _make_project("prj_preserve")
    seed = _make_seed("sed_preserve", project.id)
    seed.stage = SeedStage.IDEATE
    seed.status = SeedStatus.RUNNING
    seed.spent = Decimal("0.00")

    agent = _make_agent("agt_arch123456")
    run = _make_run("run_cli_oauth_1", seed.id)
    run.stage = SeedStage.IDEATE
    run.runtime = AgentRuntime.CLAUDE_CODE
    run.model = "claude-sonnet-4-6"
    run.status = RunStatus.RUNNING
    run.cost = Decimal("0")
    run.input_tokens = 0
    run.output_tokens = 0
    run.output = {
        "auth_mode": "cli_oauth",
        "cost_enforcement": "estimated",
        "budget_enforcement": "estimated",
        "gateway_model": "anthropic/claude-sonnet-4-6",
        "_actor_user_id": "usr_admin123456",
    }

    session = _make_session()

    async def _get_obj(_cls: Any, key: Any) -> Any:
        if key == run.id:
            return run
        if key == seed.id:
            return seed
        if key == project.id:
            return project
        if key == agent.id:
            return agent
        return None

    session.get = AsyncMock(side_effect=_get_obj)
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))

    pol = _make_policy(SeedStage.IDEATE, project.id)
    monkeypatch.setattr(
        runner_bridge_mod, "get_effective_stage_policy", AsyncMock(return_value=pol)
    )
    monkeypatch.setattr(
        runner_bridge_mod,
        "finalize_run_litellm_key",
        AsyncMock(return_value=Decimal("0")),
    )
    monkeypatch.setattr(
        runner_bridge_mod,
        "resolve_models_backend_for_run",
        AsyncMock(return_value=(None, "anthropic/claude-sonnet-4-6")),
    )
    monkeypatch.setattr(runs_mod, "list_stage_cycle_runs", AsyncMock(return_value=[run]))

    payload = RunnerCompleteRequest(
        status=RunStatus.SUCCEEDED,
        exit_code=0,
        input_tokens=10_000,
        output_tokens=2_000,
        stage_output={
            "specification": "Specification generated via subscription CLI OAuth.",
            "acceptance_criteria": ["Preserve auth_mode and cost_enforcement"],
            "summary_comment": "Completed ideation.",
        },
    )
    tok = create_run_token(run.id, scope="finalize")
    completed = await runner_bridge_mod.complete_runner_execution(session, tok, payload)

    assert completed.output["auth_mode"] == "cli_oauth"
    assert completed.output["cost_enforcement"] == "estimated"
    assert completed.output["gateway_model"] == "anthropic/claude-sonnet-4-6"
    assert completed.output["_actor_user_id"] == "usr_admin123456"
    assert Decimal(str(completed.cost)) > Decimal("0")
    assert Decimal(str(seed.spent)) > Decimal("0")


@pytest.mark.asyncio
async def test_cli_auth_secret_redaction_context_chmod_and_refresh_hardening(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 4a. Recursive secret extraction (>8 chars) from cli_auth_bundle
    bundle = {
        "provider": "anthropic",
        ".claude/.credentials.json": json.dumps(
            {
                "claudeAiOauth": {
                    "accessToken": "sk-ant-oat01-super-secret-access",
                    "refreshToken": "sk-ant-ort01-super-secret-refresh",
                    "tokenType": "Bearer",
                    "issuer": "https://console.anthropic.com",
                }
            }
        ),
    }
    extracted = runner_bridge_mod._extract_cli_auth_secrets(bundle)
    assert "sk-ant-oat01-super-secret-access" in extracted
    assert "sk-ant-ort01-super-secret-refresh" in extracted
    assert "Bearer" not in extracted
    assert "anthropic" not in extracted
    assert "https://console.anthropic.com" not in extracted

    # 4b. write_agent_context_file enforces 0o600
    chmod_calls: list[tuple[str, int]] = []
    orig_chmod = runner_bridge_mod.os.chmod

    def _spy_chmod(p: Any, mode: int) -> None:
        chmod_calls.append((str(p), mode))
        orig_chmod(p, mode)

    monkeypatch.setattr(runner_bridge_mod.os, "chmod", _spy_chmod)
    ctx_file = runner_bridge_mod.write_agent_context_file(
        tmp_path / "agent-context.json", {"run_id": "run_1"}
    )
    assert ctx_file.is_file()
    assert chmod_calls and chmod_calls[-1][1] == stat.S_IRUSR | stat.S_IWUSR

    # 4c. refresh_runner_cli_auth_bundle enforces <=65536 bytes, dict, provider, primitive fields
    project = _make_project("prj_refresh")
    seed = _make_seed("sed_refresh", project.id)
    agent = _make_agent("agt_claude")
    run = _make_run("run_refresh_1", seed.id)
    run.agent_id = agent.id
    run.runtime = AgentRuntime.CLAUDE_CODE
    run.status = RunStatus.RUNNING

    mb = _make_backend("bnd_claude_oauth", BackendType.INFERENCE)
    mb.provider = "anthropic"
    mb.config = {"auth_mode": "cli_oauth", "personal_mode_acknowledged": True}
    mb.secrets_encrypted = encrypt_secrets(
        {
            "cli_auth_bundle": json.dumps(
                {
                    "provider": "anthropic",
                    ".claude/.credentials.json": json.dumps(
                        {"access_token": "old-access-token-123"}
                    ),
                }
            )
        }
    )

    session = _make_session()

    async def _get_refresh(_cls: Any, key: Any) -> Any:
        if key == run.id:
            return run
        if key == seed.id:
            return seed
        if key == project.id:
            return project
        if key == agent.id:
            return agent
        return None

    session.get = AsyncMock(side_effect=_get_refresh)
    monkeypatch.setattr(
        runner_bridge_mod,
        "resolve_models_backend_for_run",
        AsyncMock(return_value=(mb, "claude-sonnet-4-6")),
    )
    agent_tok = create_run_token(run.id, scope="agent")

    # Non-dict bundle rejected
    with pytest.raises(Invalid, match="JSON object"):
        await runner_bridge_mod.refresh_runner_cli_auth_bundle(
            session, agent_tok, ["not", "a", "dict"]  # type: ignore[arg-type]
        )

    # Oversized bundle (>65,536 bytes) rejected
    huge_payload = RunnerCliAuthRefreshRequest(
        cli_auth_bundle={
            ".claude/.credentials.json": json.dumps({"access_token": "x" * 70_000})
        }
    )
    with pytest.raises(Invalid, match="65536"):
        await runner_bridge_mod.refresh_runner_cli_auth_bundle(
            session, agent_tok, huge_payload
        )

    # Provider mutation rejected
    with pytest.raises(Invalid, match="provider"):
        await runner_bridge_mod.refresh_runner_cli_auth_bundle(
            session,
            agent_tok,
            {
                "provider": "openai",
                ".claude/.credentials.json": json.dumps({"access_token": "tok_123456789"}),
            },
        )

    # Executable / nested non-credential fields inside JSON rejected
    with pytest.raises(Invalid):
        await runner_bridge_mod.refresh_runner_cli_auth_bundle(
            session,
            agent_tok,
            RunnerCliAuthRefreshRequest(
                cli_auth_bundle={
                    ".claude/.credentials.json": json.dumps(
                        {"hooks": {"PreToolUse": "curl attacker"}}
                    )
                }
            ),
        )

    # Valid refresh succeeds and preserves existing "provider" key
    ok_res = await runner_bridge_mod.refresh_runner_cli_auth_bundle(
        session,
        agent_tok,
        RunnerCliAuthRefreshRequest(
            cli_auth_bundle={
                ".claude/.credentials.json": json.dumps(
                    {
                        "access_token": "sk-ant-oat01-fresh-token",
                        "expires_at": 1893801600,
                    }
                )
            }
        ),
    )
    assert ok_res.ok is True
    saved_bundle = json.loads(decrypt_secrets(mb.secrets_encrypted)["cli_auth_bundle"])
    assert saved_bundle["provider"] == "anthropic"
    assert "sk-ant-oat01-fresh-token" in saved_bundle[".claude/.credentials.json"]


# ---------------------------------------------------------------------------
# 5. CSRF SANDBOX_ORIGIN Guard + Additional Part A Hardening Checks
# ---------------------------------------------------------------------------


def test_verify_csrf_blocks_sandbox_origin(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "PUBLIC_WEB_URL", "http://localhost:8421")
    monkeypatch.setattr(
        settings,
        "CORS_ORIGINS",
        "http://localhost:8421,http://sandbox.localhost:8425",
    )
    monkeypatch.setattr(settings, "ALLOWED_ORIGINS", "")
    monkeypatch.setattr(settings, "SANDBOX_ORIGIN", "http://sandbox.localhost:8425")

    sandbox_req = SimpleNamespace(
        method="POST",
        headers={"origin": "http://sandbox.localhost:8425/"},
    )
    with pytest.raises(
        Forbidden,
        match="Cross-origin requests from sandbox origin cannot use session cookies",
    ):
        deps_mod.verify_csrf(sandbox_req)  # type: ignore[arg-type]

    evil_req = SimpleNamespace(
        method="POST",
        headers={"origin": "https://evil.example.com"},
    )
    with pytest.raises(Forbidden, match="CSRF"):
        deps_mod.verify_csrf(evil_req)  # type: ignore[arg-type]

    valid_req = SimpleNamespace(
        method="POST",
        headers={"origin": "http://localhost:8421"},
    )
    deps_mod.verify_csrf(valid_req)  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_list_seeds_accepts_limit_up_to_1000(
    client: httpx.AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    list_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(seeds_mod, "list_seeds", list_mock)

    resp = await client.get("/api/v1/seeds?limit=500")
    assert resp.status_code == 200
    assert list_mock.await_args is not None
    assert list_mock.await_args.kwargs["limit"] == 500


@pytest.mark.asyncio
async def test_stream_agent_stdout_drains_oversized_lines_without_deadlock() -> None:
    # Case 1: Oversized line already contains '\n' in buffer beyond StreamReader limit
    reader_buffered = asyncio.StreamReader(limit=64)
    reader_buffered.feed_data(b'{"type":"first"}\n' + (b"A" * 256) + b'\n{"type":"after"}\n')
    reader_buffered.feed_eof()
    lines_a = await agent_sessions_mod._stream_agent_stdout(reader_buffered, chunk_size=64)
    assert lines_a == ['{"type":"first"}', '{"type":"after"}']

    # Case 2: Oversized line arrives in chunks without '\n' when readline() first trips limit
    reader_chunked = asyncio.StreamReader(limit=64)

    async def _producer() -> None:
        reader_chunked.feed_data(b'{"type":"init"}\n')
        await asyncio.sleep(0)
        reader_chunked.feed_data(b"X" * 100)  # Exceeds 64B without newline
        await asyncio.sleep(0.01)
        reader_chunked.feed_data(b"Y" * 100 + b'\n{"type":"final_reply"}\n')
        reader_chunked.feed_eof()

    prod_task = asyncio.create_task(_producer())
    lines_b = await agent_sessions_mod._stream_agent_stdout(reader_chunked, chunk_size=48)
    await prod_task
    assert lines_b == ['{"type":"init"}', '{"type":"final_reply"}']


@pytest.mark.asyncio
async def test_personal_mode_actor_and_webhook_isolation_and_session_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "GITSEED_ALLOW_PERSONAL_CLI_OAUTH", True)
    monkeypatch.setattr(settings, "ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS", True)

    personal_mb = _make_backend("bnd_personal", BackendType.INFERENCE)
    personal_mb.provider = "anthropic"
    personal_mb.secrets_encrypted = encrypt_secrets({"oauth_token": "sk-ant-oat01-owner-secret"})
    personal_mb.config = {
        "auth_mode": "cli_oauth",
        "personal_mode": True,
        "personal_mode_acknowledged": True,
        "personal_mode_owner_user_id": "usr_owner",
    }

    # Webhook-created seed rejected even when created_by matches or is None
    webhook_seed = _make_seed("sed_wh", "prj_test123456")
    webhook_seed.created_by = None
    webhook_seed.tags = ["webhook"]
    with pytest.raises((Invalid, Forbidden), match="webhook"):
        gateway_mod.validate_personal_mode_backend_access(None, personal_mb, webhook_seed)

    # User B triggering a run on User A's seed is rejected via _actor_user_id
    owner_seed = _make_seed("sed_owner", "prj_test123456")
    owner_seed.created_by = "usr_owner"
    other_user_run = _make_run("run_user_b", owner_seed.id)
    other_user_run.output = {"_actor_user_id": "usr_other_member"}
    with pytest.raises(Forbidden, match="Personal Mode"):
        gateway_mod.validate_personal_mode_backend_access(
            None, personal_mb, owner_seed, run=other_user_run
        )

    # Owner's own run succeeds
    owner_run = _make_run("run_owner", owner_seed.id)
    owner_run.output = {"_actor_user_id": "usr_owner"}
    gateway_mod.validate_personal_mode_backend_access(
        None, personal_mb, owner_seed, run=owner_run
    )

    # maybe_auto_provision_sdlc_agents does not overwrite existing agents in multi-user org mode
    monkeypatch.setattr(settings, "ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS", False)
    prov_session = _make_session()
    prov_session.scalar = AsyncMock(return_value=3)  # 3 users in org
    provision_spy = AsyncMock()
    monkeypatch.setattr(backends_mod, "provision_sdlc_agents_for_backend", provision_spy)
    await backends_mod.maybe_auto_provision_sdlc_agents(prov_session, personal_mb)
    provision_spy.assert_not_awaited()

    # Interactive session virtual key enforces remaining seed budget via max_budget
    api_mb = _make_backend("bnd_api", BackendType.INFERENCE)
    api_mb.provider = "openai"
    api_mb.secrets_encrypted = encrypt_secrets({"api_key": "sk-real-provider-key"})
    api_mb.config = {"models": [{"id": "gpt-5.4", "enabled": True}]}

    budget_seed = _make_seed("sed_budget", "prj_test123456")
    budget_seed.budget_cap = Decimal("15.00")
    budget_seed.spent = Decimal("3.50")

    captured_json: dict[str, Any] = {}

    class _FakeAsyncClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _FakeAsyncClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def post(self, url: str, **kwargs: Any) -> Any:
            if url.endswith("/key/generate"):
                captured_json.update(kwargs.get("json") or {})
            return SimpleNamespace(
                status_code=200,
                text="{}",
                json=lambda: {"key": "sk-gss-ses_123"},
            )

    monkeypatch.setattr(gateway_mod, "sync_backend_to_litellm", AsyncMock(return_value=["openai/gpt-5.4"]))
    monkeypatch.setattr(gateway_mod.httpx, "AsyncClient", _FakeAsyncClient)

    await gateway_mod.provision_session_gateway_access(
        session_id="ses_123",
        project_id="prj_test123456",
        agent_id="agt_arch123456",
        backend=api_mb,
        model="gpt-5.4",
        seed=budget_seed,
    )
    assert captured_json.get("max_budget") == pytest.approx(11.50)


@pytest.mark.asyncio
async def test_bootstrap_auto_migrate_and_dynamic_alembic_head_and_scheduler_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    heads = bootstrap_mod._resolve_alembic_head_revisions()
    assert len(heads) >= 1
    assert all(isinstance(h, str) and len(h) >= 6 for h in heads)

    # When GITSEED_AUTO_MIGRATE=False, engine.begin() is skipped completely
    monkeypatch.setattr(settings, "GITSEED_AUTO_MIGRATE", False)
    begin_spy = MagicMock()
    monkeypatch.setattr(bootstrap_mod, "engine", SimpleNamespace(begin=begin_spy))

    boot_session = _make_session()
    boot_session.get = AsyncMock(return_value=SimpleNamespace(id=1))
    boot_session.scalar = AsyncMock(side_effect=[1, 1, 1])  # user_count=1, compute_count=1, agent_count=1
    boot_session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))

    @asynccontextmanager
    async def _boot_cm() -> AsyncIterator[AsyncMock]:
        yield boot_session

    monkeypatch.setattr(bootstrap_mod, "AsyncSessionLocal", _boot_cm)
    monkeypatch.setattr(bootstrap_mod, "ensure_org_policies", AsyncMock(return_value={}))
    await bootstrap_mod.bootstrap_database()
    begin_spy.assert_not_called()

    # Scheduler tick acquires session-level pg_try_advisory_lock and unlocks in finally
    lock_session = _make_session()
    work_session = _make_session()
    sql_calls: list[str] = []

    async def _lock_scalar(stmt: Any) -> bool:
        sql_calls.append(str(stmt))
        return True

    lock_session.scalar = AsyncMock(side_effect=_lock_scalar)
    sessions_iter = iter([lock_session, work_session])

    @asynccontextmanager
    async def _sched_cm() -> AsyncIterator[AsyncMock]:
        yield next(sessions_iter)

    monkeypatch.setattr(scheduler_mod, "AsyncSessionLocal", _sched_cm)
    monkeypatch.setattr(scheduler_mod, "_reconcile_active_runs", AsyncMock())
    monkeypatch.setattr(scheduler_mod, "_cleanup_finished_run_containers", AsyncMock())
    monkeypatch.setattr(sandboxes_mod, "reconcile_sandbox_sessions", AsyncMock())
    monkeypatch.setattr(scheduler_mod, "_reconcile_external_pr_merges", AsyncMock())
    monkeypatch.setattr(scheduler_mod, "_enqueue_runs_for_queued_seeds", AsyncMock())
    monkeypatch.setattr(scheduler_mod, "_dispatch_queued_runs", AsyncMock(return_value=2))

    dispatched: Callable[[], Any] = scheduler_mod.run_scheduler_tick
    count = await dispatched()
    assert count == 2
    assert any("pg_try_advisory_lock(" in s for s in sql_calls)
    assert any("pg_advisory_unlock(" in s for s in sql_calls)
    work_session.commit.assert_awaited_once()
