from __future__ import annotations

import dataclasses
import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from gitseed_common.enums import (
    AgentRuntime,
    ApprovalDecision,
    BackendHealth,
    BackendType,
    ProjectRole,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
)
from gitseed_common.schemas.run import RunResponse
from gitseed_core.config import settings
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.models.user import User
from gitseed_core.security import encrypt_secrets
from gitseed_core.services import backends as backends_service
from gitseed_core.services import cli_auth as cli_auth_service
from gitseed_core.services import gateway as gateway_service
from gitseed_core.services import git_pr_providers as pr_providers_service
from gitseed_core.services import projects as projects_service
from gitseed_core.services import receipts as receipts_service
from gitseed_core.services.errors import Forbidden, Invalid

from conftest import (
    _exec_result,
    _make_backend,
    _make_policy,
    _make_project,
    _make_run,
    _make_seed,
    _make_session,
    _make_user,
)


@pytest.mark.asyncio
async def test_personal_mode_backend_acknowledgement_and_access_control(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    owner = _make_user("usr_owner123", name="Owner User")
    other_user = _make_user("usr_other456", name="Other User")

    # 1. Creating a cli_oauth inference backend with personal_mode_acknowledged=False fails
    with pytest.raises(Invalid, match="Personal Mode"):
        await backends_service.create_backend(
            session,
            owner,
            name="Claude Subscription",
            type=BackendType.INFERENCE,
            provider="anthropic",
            endpoint=None,
            config={"auth_mode": "cli_oauth", "personal_mode_acknowledged": False},
            secrets={},
        )

    # 2. Creating with personal_mode_acknowledged=True records owner and timestamp
    created = await backends_service.create_backend(
        session,
        owner,
        name="Claude Subscription",
        type=BackendType.INFERENCE,
        provider="anthropic",
        endpoint=None,
        config={"auth_mode": "cli_oauth", "personal_mode_acknowledged": True},
        secrets={},
    )
    assert created.config["personal_mode"] is True
    assert created.config["personal_mode_acknowledged"] is True
    assert created.config["personal_mode_owner_user_id"] == owner.id
    assert isinstance(created.config.get("personal_mode_acknowledged_at"), str)

    # 3. Updating a cli_oauth backend to personal_mode_acknowledged=False while enabled fails
    session.get = AsyncMock(return_value=created)
    with pytest.raises(Invalid, match="Personal Mode"):
        await backends_service.update_backend(
            session,
            owner,
            created.id,
            config={"auth_mode": "cli_oauth", "personal_mode_acknowledged": False},
        )

    # 4. Seed created by owner succeeds; seed created by another user or webhook (None) is rejected
    owner_seed = _make_seed("sed_owner123")
    owner_seed.created_by = owner.id
    gateway_service.validate_personal_mode_backend_access(
        session, created, owner_seed, allow_multi_user=False
    )

    other_seed = _make_seed("sed_other456")
    other_seed.created_by = other_user.id
    with pytest.raises((Forbidden, Invalid), match="Personal Mode"):
        gateway_service.validate_personal_mode_backend_access(
            session, created, other_seed, allow_multi_user=False
        )

    webhook_seed = _make_seed("sed_webhook789")
    webhook_seed.created_by = None
    with pytest.raises((Forbidden, Invalid), match="Personal Mode"):
        gateway_service.validate_personal_mode_backend_access(
            session, created, webhook_seed, allow_multi_user=False
        )

    # 5. When ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS=True, multi-user seeds are allowed
    monkeypatch.setattr(settings, "ALLOW_PERSONAL_MODE_IN_MULTI_USER_ORGS", True)
    gateway_service.validate_personal_mode_backend_access(session, created, other_seed)
    gateway_service.validate_personal_mode_backend_access(session, created, webhook_seed)

    # 6. When GITSEED_ALLOW_PERSONAL_CLI_OAUTH=False, Personal Mode is blocked instance-wide
    monkeypatch.setattr(settings, "GITSEED_ALLOW_PERSONAL_CLI_OAUTH", False)
    with pytest.raises(Forbidden, match="GITSEED_ALLOW_PERSONAL_CLI_OAUTH=false"):
        gateway_service.validate_personal_mode_backend_access(session, created, owner_seed)
    with pytest.raises(Forbidden, match="GITSEED_ALLOW_PERSONAL_CLI_OAUTH=false"):
        await backends_service.create_backend(
            session,
            owner,
            name="Blocked Personal Mode",
            type=BackendType.INFERENCE,
            provider="anthropic",
            endpoint=None,
            config={"auth_mode": "cli_oauth", "personal_mode_acknowledged": True},
            secrets={},
        )


@pytest.mark.asyncio
async def test_cli_auth_persist_bundle_records_personal_mode() -> None:
    session = _make_session()
    owner = _make_user("usr_owner123", name="Owner User")
    backend = Backend(
        id="bnd_cli123456",
        name="Claude CLI",
        type=BackendType.INFERENCE,
        provider="anthropic",
        endpoint=None,
        config={"auth_mode": "cli_oauth"},
        secrets_encrypted=None,
        health=BackendHealth.UNKNOWN,
        enabled=True,
        created_by=owner.id,
    )
    session.get = AsyncMock(return_value=backend)
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))

    updated = await cli_auth_service._persist_authenticated_bundle(
        session,
        owner,
        backend,
        bundle={
            ".claude/.credentials.json": json.dumps(
                {"claudeAiOauth": {"accessToken": "sk-ant-oat01-test-token"}}
            )
        },
    )
    assert updated.config["personal_mode"] is True
    assert updated.config["personal_mode_acknowledged"] is True
    assert updated.config["personal_mode_owner_user_id"] == "usr_owner123"
    assert isinstance(updated.config.get("personal_mode_acknowledged_at"), str)


@pytest.mark.asyncio
async def test_budget_enforcement_enforced_vs_estimated() -> None:
    session = _make_session()
    project = _make_project("prj_budget123")
    seed = _make_seed("sed_budget123", project.id)
    seed.spent = Decimal("2.50")
    seed.budget_cap = Decimal("10.00")

    api_key_backend = Backend(
        id="bnd_api123",
        name="Anthropic API Key",
        type=BackendType.INFERENCE,
        provider="anthropic",
        config={"auth_mode": "api_key"},
        secrets_encrypted=encrypt_secrets({"api_key": "sk-ant-test"}),
        enabled=True,
    )
    cli_oauth_backend = Backend(
        id="bnd_oauth123",
        name="Claude CLI OAuth",
        type=BackendType.INFERENCE,
        provider="anthropic",
        config={
            "auth_mode": "cli_oauth",
            "personal_mode": True,
            "personal_mode_acknowledged": True,
            "personal_mode_owner_user_id": seed.created_by,
        },
        secrets_encrypted=encrypt_secrets({"cli_auth_bundle": "eyJ0ZXN0IjoxfQ=="}),
        enabled=True,
    )

    session.get = AsyncMock(return_value=None)
    session.scalar = AsyncMock(return_value=Decimal("2.50"))

    enforced_status = await gateway_service.compute_budget_status(
        session, seed, project, backend=api_key_backend
    )
    assert enforced_status["enforcement"] == "enforced"
    assert "LiteLLM" in enforced_status["enforcement_detail"]

    estimated_status = await gateway_service.compute_budget_status(
        session, seed, project, backend=cli_oauth_backend
    )
    assert estimated_status["enforcement"] == "estimated"
    assert "CLI" in estimated_status["enforcement_detail"]

    # RunResponse derives cost_enforcement from run.output
    run = _make_run("run_est123", seed.id)
    run.output = {"cost_enforcement": "estimated", "auth_mode": "cli_oauth"}
    run_resp = RunResponse.model_validate(run)
    assert run_resp.cost_enforcement == "estimated"
    assert run_resp.enforcement == "estimated"


@pytest.mark.asyncio
async def test_governance_receipt_build_sign_verify_and_tamper_detection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime(2026, 11, 11, 12, 0, 0, tzinfo=UTC)
    project = _make_project("prj_rec123")
    project.slug = "payments"
    project.sandbox_config = {"blocked_paths": [".github/workflows/**", "infra/**"]}

    seed = _make_seed("sed_rec123", project.id)
    seed.number = 42
    seed.title = "Harden webhook signature verification"
    seed.stage = SeedStage.REVIEW
    seed.status = SeedStatus.AWAITING_APPROVAL
    seed.autonomy = SeedAutonomy.SUPERVISED

    trusted_metadata = {
        "commit_sha": "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678",
        "branch": "gitseed/payments/seed-42",
        "pushed": True,
        "base_heads": {"org/payments": "00112233445566778899aabbccddeeff00112233"},
        "blocked_paths_violations": [],
        "secret_scan_findings": [],
        "diff_stats": {"files_changed": 3, "insertions": 48, "deletions": 6},
        "verification": {"exit_code": 0, "passed": True},
    }
    run1 = Run(
        id="run_exec123",
        seed_id=seed.id,
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_coder123",
        runtime=AgentRuntime.CLAUDE_CODE,
        model="claude-sonnet-4-20250514",
        status=RunStatus.SUCCEEDED,
        input_tokens=4200,
        output_tokens=1100,
        cost=Decimal("0.0845"),
        output={
            "branch_name": "gitseed/payments/seed-42",
            "pull_request_url": "https://github.com/org/payments/pull/99",
            "cost_enforcement": "enforced",
            "trusted": trusted_metadata,
        },
        started_at=now,
        finished_at=now,
        created_at=now,
        updated_at=now,
    )
    approval1 = Approval(
        id="apr_plan123",
        seed_id=seed.id,
        stage=SeedStage.PLAN,
        snapshot={},
        decision=ApprovalDecision.APPROVED,
        comment="LGTM — proceed to execute",
        automatic=False,
        decided_by="usr_admin123456",
        approver_role=ProjectRole.MAINTAINER,
        created_at=now,
    )
    approver_user = _make_user("usr_admin123456", name="Alice Maintainer", preferred_name="Alice")
    merge_pol = dataclasses.replace(
        _make_policy(SeedStage.MERGE, project.id),
        min_approver_role=ProjectRole.MAINTAINER,
        allow_self_approval=False,
    )
    monkeypatch.setattr(
        receipts_service,
        "get_effective_stage_policy",
        AsyncMock(return_value=merge_pol),
    )

    session = _make_session()

    async def _get(model: Any, ident: Any) -> Any:
        if model is Seed and ident == seed.id:
            return seed
        if model is Project and ident == project.id:
            return project
        if model is User and ident == approver_user.id:
            return approver_user
        return None

    session.get = AsyncMock(side_effect=_get)
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[run1]),
            _exec_result(scalars_all=[approval1]),
            _exec_result(scalars_all=[approver_user]),
            _exec_result(scalars_all=[]),
        ]
    )

    envelope = await receipts_service.build_seed_governance_receipt(session, seed.id)
    assert envelope["algorithm"] == "Ed25519"
    assert len(envelope["key_id"]) == 16
    assert "BEGIN PUBLIC KEY" in envelope["public_key_pem"]

    receipt = envelope["receipt"]
    assert receipt["schema_version"] == "gitseed.receipt.v1"
    assert receipt["seed"]["id"] == seed.id
    assert receipt["seed"]["number"] == 42
    assert receipt["seed"]["project_slug"] == "payments"
    assert receipt["seed"]["branch_name"] == "gitseed/payments/seed-42"
    assert receipt["seed"]["pr_url"] == "https://github.com/org/payments/pull/99"
    assert receipt["policy"]["prevent_self_approval"] is True
    assert receipt["policy"]["blocked_paths"] == [".github/workflows/**", "infra/**"]
    assert len(receipt["runs"]) == 1
    assert receipt["runs"][0]["cost_enforcement"] == "enforced"
    assert receipt["runs"][0]["trusted"] == trusted_metadata
    assert len(receipt["approvals"]) == 1
    assert receipt["approvals"][0]["approver_name"] == "Alice"

    # Untrusted embedded public_key_pem alone is rejected; explicit trusted public_key_pem verifies
    assert bool(receipts_service.verify_governance_receipt(envelope)) is False
    trusted_pub_pem = envelope["public_key_pem"]
    assert (
        bool(
            receipts_service.verify_governance_receipt(
                envelope, public_key_pem=trusted_pub_pem
            )
        )
        is True
    )

    # Tamper with receipt payload -> fails verification
    tampered_payload = json.loads(json.dumps(envelope))
    tampered_payload["receipt"]["runs"][0]["cost_usd"] = 0.0
    assert (
        bool(
            receipts_service.verify_governance_receipt(
                tampered_payload, public_key_pem=trusted_pub_pem
            )
        )
        is False
    )

    # Tamper with both receipt and canonical_json -> signature mismatch fails verification
    tampered_canonical = json.loads(json.dumps(tampered_payload))
    tampered_canonical["canonical_json"] = receipts_service.canonicalize_receipt_payload(
        tampered_canonical["receipt"]
    )
    assert (
        bool(
            receipts_service.verify_governance_receipt(
                tampered_canonical, public_key_pem=trusted_pub_pem
            )
        )
        is False
    )

    # Tamper with signature bytes -> fails verification
    tampered_sig = json.loads(json.dumps(envelope))
    sig_str = tampered_sig["signature"]
    tampered_sig["signature"] = ("A" if sig_str[0] != "A" else "B") + sig_str[1:]
    assert (
        bool(
            receipts_service.verify_governance_receipt(
                tampered_sig, public_key_pem=trusted_pub_pem
            )
        )
        is False
    )

    # Wrong public key -> fails verification
    other_priv = Ed25519PrivateKey.generate()
    other_pub_pem = (
        other_priv.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    assert (
        bool(receipts_service.verify_governance_receipt(envelope, public_key_pem=other_pub_pem))
        is False
    )

    # Format PR comment and sync via GitHub provider helper
    comment_md = pr_providers_service.format_governance_receipt_comment(envelope)
    assert pr_providers_service.GOVERNANCE_RECEIPT_MARKER in comment_md
    assert "payments#42" in comment_md
    assert "`a1b2c3d`" in comment_md
    assert "Signed Governance Receipt JSON (Ed25519)" in comment_md

    recorded_requests: list[tuple[str, str, dict[str, Any]]] = []

    def _gh_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(
                200,
                json=[{"id": 777, "body": f"{pr_providers_service.GOVERNANCE_RECEIPT_MARKER}\nold"}],
            )
        body_json = json.loads(request.content.decode("utf-8"))
        recorded_requests.append((request.method, str(request.url), body_json))
        return httpx.Response(200, json={"id": 777, "body": body_json["body"]})

    git_backend = _make_backend("bnd_gh123", BackendType.GIT)
    repo = ProjectRepository(
        id=1,
        project_id=project.id,
        backend_id=git_backend.id,
        full_name="org/payments",
        default_branch="main",
        is_primary=True,
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(_gh_handler)) as gh_client:
        sync_res = await pr_providers_service.sync_seed_governance_receipt_comment(
            gh_client,
            provider="github",
            backend=git_backend,
            repo=repo,
            token="ghp_test",
            pr_number=99,
            envelope=envelope,
        )
    assert sync_res == {"id": "777", "updated": True, "provider": "github"}
    assert len(recorded_requests) == 1
    assert recorded_requests[0][0] == "PATCH"
    assert pr_providers_service.GOVERNANCE_RECEIPT_MARKER in recorded_requests[0][2]["body"]


@pytest.mark.asyncio
async def test_receipt_api_endpoints(
    client: httpx.AsyncClient,
    fake_db: AsyncMock,
    admin_user: User,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. GET /api/v1/receipts/public-key
    pk_resp = await client.get("/api/v1/receipts/public-key")
    assert pk_resp.status_code == 200
    pk_data = pk_resp.json()
    assert pk_data["algorithm"] == "Ed25519"
    assert len(pk_data["key_id"]) == 16
    assert "BEGIN PUBLIC KEY" in pk_data["public_key_pem"]

    # 2. GET /api/v1/seeds/{seed_id}/receipt
    project = _make_project("prj_api123")
    seed = _make_seed("sed_api123", project.id)
    monkeypatch.setattr(
        projects_service,
        "get_project",
        AsyncMock(return_value=project),
    )

    async def _get_api(model: Any, ident: Any) -> Any:
        if model is Seed and ident == seed.id:
            return seed
        if model is Project and ident == project.id:
            return project
        return None

    fake_db.get = AsyncMock(side_effect=_get_api)
    fake_db.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[]),
            _exec_result(scalars_all=[]),
            _exec_result(scalars_all=[]),
            _exec_result(scalar_one_or_none=None),
        ]
    )

    rec_resp = await client.get(f"/api/v1/seeds/{seed.id}/receipt")
    assert rec_resp.status_code == 200
    rec_data = rec_resp.json()
    assert rec_data["key_id"] == pk_data["key_id"]
    assert rec_data["receipt"]["schema_version"] == "gitseed.receipt.v1"
    assert rec_data["receipt"]["seed"]["id"] == seed.id
    assert pr_providers_service.GOVERNANCE_RECEIPT_MARKER in rec_data["markdown"]
    assert (
        bool(
            receipts_service.verify_governance_receipt(
                rec_data, public_key_pem=pk_data["public_key_pem"]
            )
        )
        is True
    )

    # 3. Key rotation: receipts signed with a retired key in GITSEED_SECRET_PREVIOUS_KEYS still verify
    old_secret = settings.GITSEED_SECRET_KEY
    new_secret = "new-rotated-production-secret-key-at-least-32-chars"
    monkeypatch.setattr(settings, "GITSEED_SECRET_KEY", new_secret)
    monkeypatch.setattr(settings, "GITSEED_SECRET_PREVIOUS_KEYS", old_secret)

    rotated_pk_info = receipts_service.get_receipt_signing_public_key_info()
    assert rotated_pk_info["key_id"] != pk_data["key_id"]
    assert any(p["key_id"] == pk_data["key_id"] for p in rotated_pk_info["previous_public_keys"])

    stripped_envelope = {k: v for k, v in rec_data.items() if k != "public_key_pem"}
    trusted_keys = [
        rotated_pk_info["public_key_pem"],
        *[p["public_key_pem"] for p in rotated_pk_info["previous_public_keys"]],
    ]
    assert (
        bool(
            receipts_service.verify_governance_receipt(
                stripped_envelope, trusted_public_keys=trusted_keys
            )
        )
        is True
    )
