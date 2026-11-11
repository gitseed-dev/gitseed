from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from gitseed_common.enums import (
    AgentRuntime,
    BackendHealth,
    BackendType,
    OrgRole,
    ProjectVisibility,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    UserStatus,
)
from gitseed_core.api.v1 import local_git as local_git_api
from gitseed_core.config import settings
from gitseed_core.db import get_session
from gitseed_core.main import app
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.models.user import User
from gitseed_core.security import create_run_token, encrypt_secrets, hash_password
from gitseed_core.services import compute_runtime, session_vscode, webhooks
from gitseed_core.services.errors import Forbidden, Invalid, Unauthorized
from gitseed_core.services.gateway import validate_personal_mode_backend_access
from gitseed_core.services.git_changes import _split_unified_diff_by_file, get_seed_changes
from gitseed_core.services.git_merge import issue_git_push_token
from gitseed_core.services.git_pr_providers import (
    RECEIPT_COMMENT_MARKER,
    sync_seed_governance_receipt_comment,
)
from gitseed_core.services.local_git import (
    InvalidGitInputError,
    _ensure_git_repo_initialized,
    validate_workspace_path,
)

_PW_HASH = hash_password("ValidPass123!")


def _exec_result(
    *,
    scalar_one_or_none: Any = None,
    all_rows: list[Any] | None = None,
    scalars_all: list[Any] | None = None,
) -> MagicMock:
    res = MagicMock()
    res.scalar_one_or_none.return_value = scalar_one_or_none
    res.all.return_value = all_rows or []
    scalars_mock = MagicMock()
    scalars_mock.all.return_value = scalars_all if scalars_all is not None else (all_rows or [])
    scalars_mock.first.return_value = scalars_all[0] if scalars_all else scalar_one_or_none
    res.scalars.return_value = scalars_mock
    return res


def _make_user(user_id: str = "usr_admin123456") -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        email="admin@gitseed.local",
        name="Admin User",
        preferred_name="Admin",
        org_role=OrgRole.ADMIN,
        status=UserStatus.ACTIVE,
        password_hash=_PW_HASH,
        preferences={"theme": "dark"},
        last_active_at=now,
        created_at=now,
        updated_at=now,
    )


def _make_project(project_id: str = "prj_test123456") -> Project:
    now = datetime.now(UTC)
    return Project(
        id=project_id,
        name="Alpha Project",
        slug="alpha-project",
        description="Test project",
        tags=["core"],
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
    now = datetime.now(UTC)
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
        stage=SeedStage.EXECUTE,
        status=SeedStatus.QUEUED,
        autonomy=SeedAutonomy.SUPERVISED,
        stage_entered_at=now,
        spent=Decimal("0"),
        checkpoint_approved_through=Decimal("0"),
        created_by="usr_admin123456",
        created_at=now,
        updated_at=now,
    )


def _make_run(run_id: str = "run_test123456", seed_id: str = "sed_test123456") -> Run:
    now = datetime.now(UTC)
    return Run(
        id=run_id,
        seed_id=seed_id,
        task_id=None,
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_arch123456",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        compute_backend_id="bnd_comp123456",
        status=RunStatus.RUNNING,
        input_tokens=100,
        output_tokens=50,
        cost=Decimal("0.05"),
        output={},
        created_at=now,
        updated_at=now,
    )


def _make_backend(
    backend_id: str = "bnd_git123456",
    btype: BackendType = BackendType.GIT,
) -> Backend:
    now = datetime.now(UTC)
    return Backend(
        id=backend_id,
        name="Backend Main",
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


# ---------------------------------------------------------------------------
# 1. Kubernetes & Docker Compute Runtime Hardening
# ---------------------------------------------------------------------------


def test_resolve_k8s_ssl_context_defaults_to_system_ca_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(compute_runtime, "INCLUSTER_CA_CERT_PATH", Path("/nonexistent/ca.crt"))
    backend = _make_backend(btype=BackendType.COMPUTE)
    backend.provider = "kubernetes"
    backend.endpoint = "https://k8s.prod.example.com:6443"
    backend.config = {}

    assert compute_runtime._resolve_k8s_ssl_context(backend, {}) is True

    backend.config = {"insecure_skip_tls_verify": False}
    assert compute_runtime._resolve_k8s_ssl_context(backend, {}) is True

    backend.config = {"insecure_skip_tls_verify": "false"}
    assert compute_runtime._resolve_k8s_ssl_context(backend, {}) is True

    backend.config = {"insecure_skip_tls_verify": True}
    assert compute_runtime._resolve_k8s_ssl_context(backend, {}) is False


@pytest.mark.asyncio
async def test_ensure_docker_network_sets_internal_true() -> None:
    posted_payloads: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path == "/networks/create":
            posted_payloads.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201, json={"Id": "net_123"})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        await compute_runtime._ensure_docker_network(client, "http://docker", "gitseed-runners")

    assert len(posted_payloads) == 1
    assert posted_payloads[0]["Name"] == "gitseed-runners"
    assert posted_payloads[0]["Internal"] is True


@pytest.mark.asyncio
async def test_ensure_k8s_namespace_applies_default_egress_network_policy() -> None:
    created_policies: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/api/v1/namespaces/gitseed-alpha":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path == "/api/v1/namespaces":
            return httpx.Response(201, json={"metadata": {"name": "gitseed-alpha"}})
        if (
            request.method == "POST"
            and request.url.path
            == "/apis/networking.k8s.io/v1/namespaces/gitseed-alpha/networkpolicies"
        ):
            created_policies.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(409, json={"reason": "AlreadyExists"})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        ns = await compute_runtime._ensure_k8s_namespace(
            client,
            "https://k8s.local",
            "gitseed-alpha",
            "gitseed",
            project_slug="alpha",
            workload="runner",
        )

    assert ns == "gitseed-alpha"
    assert len(created_policies) == 1
    np_spec = created_policies[0]["spec"]
    assert np_spec["policyTypes"] == ["Egress"]
    ip_blocks = [
        rule["to"][0]["ipBlock"]
        for rule in np_spec["egress"]
        if "to" in rule and "ipBlock" in rule["to"][0]
    ]
    assert ip_blocks
    assert "10.0.0.0/8" in ip_blocks[0]["except"]
    assert "172.16.0.0/12" in ip_blocks[0]["except"]
    assert "192.168.0.0/16" in ip_blocks[0]["except"]


@pytest.mark.asyncio
async def test_k8s_workspace_pvc_subpath_and_timeout_scaling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_jobs: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "DELETE":
            return httpx.Response(404)
        if request.method == "POST" and request.url.path.endswith("/jobs"):
            captured_jobs.append(json.loads(request.content.decode("utf-8")))
            return httpx.Response(201, json={"metadata": {"name": "gitseed-run-1"}})
        return httpx.Response(200, json={})

    backend = _make_backend(btype=BackendType.COMPUTE)
    backend.provider = "kubernetes"
    backend.config = {"namespace": "gitseed", "workspace_pvc": "shared-ws-pvc"}
    project = _make_project()
    seed = _make_seed()
    run = _make_run(seed_id=seed.id)

    monkeypatch.setattr(
        compute_runtime,
        "_k8s_client_for_backend",
        lambda _b: (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            "https://k8s",
            "gitseed",
        ),
    )

    await compute_runtime.spawn_docker_runner_container(
        backend=backend,
        run=run,
        seed=seed,
        project=project,
        timeout_seconds=7200,
    )

    assert len(captured_jobs) == 1
    pod_spec = captured_jobs[0]["spec"]["template"]["spec"]
    for c_spec in [pod_spec["initContainers"][0], *pod_spec["containers"]]:
        ws_mounts = [m for m in c_spec["volumeMounts"] if m["mountPath"] == "/workspace"]
        assert ws_mounts[0]["subPath"] == f"ws-{seed.id}"
        env_dict = {item["name"]: item["value"] for item in c_spec["env"]}
        assert env_dict["RUNNER_TIMEOUT_SECONDS"] == "7200"
        assert env_dict["GITSEED_FINALIZE_WAIT_SECONDS"] == "7800"


@pytest.mark.asyncio
async def test_k8s_two_container_pod_logs_specify_agent_container(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen_queries: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/pods"):
            return httpx.Response(
                200, json={"items": [{"metadata": {"name": "gitseed-run-abc-pod"}}]}
            )
        if request.url.path.endswith("/log"):
            q = request.url.query.decode("utf-8")
            seen_queries.append(q)
            if "container=agent" in q:
                return httpx.Response(200, text="agent line 1")
            if "container=finalize" in q:
                return httpx.Response(200, text="finalize pushed branch")
            return httpx.Response(400, text="a container name must be specified")
        return httpx.Response(404)

    backend = _make_backend(btype=BackendType.COMPUTE)
    backend.provider = "kubernetes"
    monkeypatch.setattr(
        compute_runtime,
        "_k8s_client_for_backend",
        lambda _b: (
            httpx.AsyncClient(transport=httpx.MockTransport(handler)),
            "https://k8s",
            "gitseed",
        ),
    )

    logs = await compute_runtime.fetch_docker_container_logs(backend, "gitseed-run-abc")
    assert "agent line 1" in logs
    assert "[finalize]\nfinalize pushed branch" in logs
    assert any("container=agent" in q for q in seen_queries)


@pytest.mark.asyncio
async def test_inspect_run_execution_detects_oomkilled_agent_and_stops_finalize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stopped_containers: list[str] = []
    old_finished = (datetime.now(UTC) - timedelta(seconds=45)).isoformat().replace("+00:00", "Z")

    def docker_handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/containers/gitseed-run-run_oom/json":
            return httpx.Response(
                200,
                json={
                    "Config": {
                        "Labels": {"app.gitseed.run_id": "run_oom", "app.gitseed.role": "agent"}
                    },
                    "State": {
                        "Running": False,
                        "ExitCode": 137,
                        "OOMKilled": True,
                        "FinishedAt": old_finished,
                    },
                },
            )
        if (
            request.method == "GET"
            and request.url.path == "/containers/gitseed-run-run_oom-finalize/json"
        ):
            return httpx.Response(
                200,
                json={"State": {"Running": True, "ExitCode": 0}},
            )
        if (
            request.method in ("POST", "DELETE")
            and "gitseed-run-run_oom-finalize" in request.url.path
        ):
            stopped_containers.append(f"{request.method}:{request.url.path}")
            return httpx.Response(204)
        return httpx.Response(404)

    backend = _make_backend(btype=BackendType.COMPUTE)
    backend.provider = "docker"
    monkeypatch.setattr(
        compute_runtime,
        "_docker_client_for_backend",
        lambda _b: (
            httpx.AsyncClient(transport=httpx.MockTransport(docker_handler)),
            "http://docker",
        ),
    )

    exec_state = await compute_runtime.inspect_run_execution(backend, "gitseed-run-run_oom")
    assert exec_state["running"] is False
    assert exec_state["exit_code"] == 137
    assert exec_state["oom_killed"] is True
    assert stopped_containers


# ---------------------------------------------------------------------------
# 2. Embedded VS Code Web Origin Isolation, Expiring Tokens & Direct Fast-Path
# ---------------------------------------------------------------------------


def test_vscode_url_builder_and_production_sandbox_origin_enforcement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "production")
    monkeypatch.setattr(settings, "SANDBOX_ORIGIN", "")
    monkeypatch.setattr(settings, "ALLOW_SAME_ORIGIN_VSCODE", False)

    with pytest.raises(Invalid, match="SANDBOX_ORIGIN must be configured in production"):
        session_vscode.enforce_vscode_production_origin()

    monkeypatch.setattr(settings, "SANDBOX_ORIGIN", "https://sandbox.gitseed.example.com/")
    session_vscode.enforce_vscode_production_origin()
    launch_url = session_vscode.build_vscode_launch_url("sandbox:sbx_1", "tok_abc")
    assert (
        launch_url
        == "https://sandbox.gitseed.example.com/api/v1/vscode/sandbox:sbx_1/tok_abc/?folder=/workspace"
    )


def test_vscode_expiring_scoped_tokens_reject_expired_timestamps() -> None:
    tok = session_vscode.mint_vscode_token(
        "sandbox:sbx_1", "cid_1", ttl_seconds=60, now=1_797_681_600
    )
    assert "." in tok
    assert (
        session_vscode.verify_vscode_token(
            "sandbox:sbx_1", "cid_1", tok, expected_token=tok, now=1_797_681_630
        )
        is True
    )
    assert (
        session_vscode.verify_vscode_token(
            "sandbox:sbx_1", "cid_1", tok, expected_token=tok, now=1_797_681_661
        )
        is False
    )
    assert (
        session_vscode.verify_vscode_token(
            "sandbox:sbx_other", "cid_1", tok, expected_token=tok, now=1_797_681_630
        )
        is False
    )


@pytest.mark.asyncio
async def test_vscode_proxy_http_uses_direct_ip_fast_path_and_sets_nosniff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "SANDBOX_ORIGIN", "")

    backend = _make_backend(btype=BackendType.COMPUTE)
    backend.provider = "docker"
    tok = session_vscode.mint_vscode_token("sandbox:sbx_fast", "cid_fast")
    state = session_vscode.VsCodeSessionState(
        session_key="sandbox:sbx_fast",
        kind="terminal",
        session_id="sbx_fast",
        project_id="prj_1",
        backend=backend,
        container_id="cid_fast",
        token=tok,
        mode="in_container",
        direct_ip="172.28.0.5",
        direct_ip_probed=True,
    )
    session_vscode._VSCODE_SESSIONS["sandbox:sbx_fast"] = state

    tunnel_called = False

    async def _fail_if_tunnel(*_args: Any, **_kwargs: Any) -> Any:
        nonlocal tunnel_called
        tunnel_called = True
        raise AssertionError("docker exec tunnel should not be called when direct IP is reachable")

    monkeypatch.setattr(session_vscode, "open_container_tcp_tunnel", _fail_if_tunnel)
    mock_http_client = httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda _req: httpx.Response(
                200,
                headers={"Content-Type": "application/javascript"},
                content=b"console.log('ok');",
            )
        )
    )
    monkeypatch.setattr(session_vscode, "_get_direct_vscode_http_client", lambda: mock_http_client)

    try:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/vscode/sandbox:sbx_fast/{tok}/out/vs/loader.js")
        assert resp.status_code == 200
        assert resp.headers.get("X-Content-Type-Options") == "nosniff"
        assert tunnel_called is False
    finally:
        session_vscode._VSCODE_SESSIONS.pop("sandbox:sbx_fast", None)
        await mock_http_client.aclose()


# ---------------------------------------------------------------------------
# 3. GitLab Webhook Membership Check & Bitbucket Parity
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gitlab_webhook_author_check_fails_closed_via_api(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = AsyncMock()
    project = _make_project()
    gl_backend = _make_backend(btype=BackendType.GIT)
    gl_backend.provider = "gitlab"
    gl_backend.endpoint = "https://gitlab.example.com"
    gl_backend.secrets_encrypted = encrypt_secrets({"token": "glpat-secret"})
    repo = ProjectRepository(
        id=1,
        project_id=project.id,
        backend_id=gl_backend.id,
        full_name="org/repo",
        default_branch="main",
        is_primary=True,
    )
    session.execute = AsyncMock(return_value=_exec_result(all_rows=[(repo, gl_backend)]))

    payload = {
        "user": {"id": 42, "username": "outsider"},
        "project": {"id": 99},
    }

    trusted_no_ctx, reason_no_ctx = await webhooks._is_trusted_gitlab_author({}, payload)
    assert trusted_no_ctx is False
    assert reason_no_ctx == "UNTRUSTED"

    class _MockClient404:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _MockClient404:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(self, _url: str, headers: dict[str, str] | None = None) -> httpx.Response:
            return httpx.Response(404, json={"message": "404 Not found"})

    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _MockClient404)
    trusted_404, _ = await webhooks._is_trusted_gitlab_author(
        {}, payload, session=session, project=project
    )
    assert trusted_404 is False

    class _MockClientReporter(_MockClient404):
        async def get(self, _url: str, headers: dict[str, str] | None = None) -> httpx.Response:
            return httpx.Response(200, json={"access_level": 20, "state": "active"})

    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _MockClientReporter)
    trusted_20, _ = await webhooks._is_trusted_gitlab_author(
        {}, payload, session=session, project=project
    )
    assert trusted_20 is False

    class _MockClientDev(_MockClient404):
        async def get(self, _url: str, headers: dict[str, str] | None = None) -> httpx.Response:
            return httpx.Response(200, json={"access_level": 30, "state": "active"})

    monkeypatch.setattr(webhooks.httpx, "AsyncClient", _MockClientDev)
    trusted_30, role_30 = await webhooks._is_trusted_gitlab_author(
        {}, payload, session=session, project=project
    )
    assert trusted_30 is True
    assert role_30 == "ACCESS_LEVEL_30"


def test_personal_mode_rejects_webhook_created_unowned_seed() -> None:
    backend = _make_backend(btype=BackendType.INFERENCE)
    backend.provider = "cli_oauth"
    backend.config = {
        "personal_mode_acknowledged": True,
        "personal_mode_owner_user_id": "usr_owner1",
    }
    seed = _make_seed()
    seed.created_by = None
    seed.tags = ["gh-issue-1", "webhook", "webhook_triggered"]

    with pytest.raises(Invalid, match="webhook-triggered"):
        validate_personal_mode_backend_access(None, backend, seed=seed, allow_multi_user=False)


@pytest.mark.asyncio
async def test_bitbucket_issue_git_push_token_username() -> None:
    project = _make_project()
    seed = _make_seed(project_id=project.id)
    seed.stage = SeedStage.EXECUTE
    run = _make_run(seed_id=seed.id)
    run.stage = SeedStage.EXECUTE
    run.status = RunStatus.RUNNING
    run.output = {"_broker": {}}

    bb_backend = _make_backend(btype=BackendType.GIT)
    bb_backend.provider = "bitbucket"
    bb_backend.endpoint = "https://api.bitbucket.org/2.0"
    bb_backend.secrets_encrypted = encrypt_secrets({"token": "bb_repo_access_token_xyz"})
    repo = ProjectRepository(
        id=1,
        project_id=project.id,
        backend_id=bb_backend.id,
        full_name="workspace/repo",
        default_branch="main",
        is_primary=True,
    )

    session = AsyncMock()
    session.get = AsyncMock(
        side_effect=lambda _model, ident: {
            run.id: run,
            seed.id: seed,
            project.id: project,
            bb_backend.id: bb_backend,
        }.get(ident)
    )
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=repo))

    finalize_tok = create_run_token(run.id, scope="finalize")
    res = await issue_git_push_token(
        session,
        finalize_tok,
        repository_full_name="workspace/repo",
    )
    assert res.username == "x-token-auth"
    assert res.push_token == "bb_repo_access_token_xyz"
    assert "x-token-auth:bb_repo_access_token_xyz@bitbucket.org" in res.push_url

    run.output = {"_broker": {}}
    bb_backend.secrets_encrypted = encrypt_secrets({"token": "myuser:myapppass"})
    res_app_pw = await issue_git_push_token(
        session,
        finalize_tok,
        repository_full_name="workspace/repo",
    )
    assert res_app_pw.username == "myuser"
    assert res_app_pw.push_token == "myapppass"


@pytest.mark.asyncio
async def test_bitbucket_receipt_comment_upsert_uses_put() -> None:
    bb_backend = _make_backend(btype=BackendType.GIT)
    bb_backend.provider = "bitbucket"
    bb_backend.endpoint = "https://api.bitbucket.org/2.0"
    repo = ProjectRepository(
        id=1,
        project_id="prj_1",
        backend_id=bb_backend.id,
        full_name="workspace/repo",
        default_branch="main",
        is_primary=True,
    )
    methods_called: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        methods_called.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(
                200,
                json={
                    "values": [
                        {
                            "id": 777,
                            "content": {"raw": f"{RECEIPT_COMMENT_MARKER}\nOld receipt"},
                        }
                    ]
                },
            )
        if request.method == "PUT" and request.url.path.endswith("/comments/777"):
            return httpx.Response(200, json={"id": 777})
        return httpx.Response(500)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await sync_seed_governance_receipt_comment(
            client,
            provider="bitbucket",
            backend=bb_backend,
            repo=repo,
            token="bb_tok",
            pr_number=12,
            envelope={"receipt": {"seed": {"number": 1}}, "signature": "sig123"},
        )

    assert result == {"id": "777", "updated": True, "provider": "bitbucket"}
    assert ("PUT", "/2.0/repositories/workspace/repo/pullrequests/12/comments/777") in methods_called


@pytest.mark.asyncio
async def test_bitbucket_diff_changes_viewer_and_webhook_hmac(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    webhooks.reset_webhook_delivery_cache()
    raw_diff = (
        "diff --git a/app/main.py b/app/main.py\n"
        "index 1111111..2222222 100644\n"
        "--- a/app/main.py\n"
        "+++ b/app/main.py\n"
        "@@ -1,2 +1,3 @@\n"
        " import os\n"
        "+import sys\n"
        " print('hi')\n"
    )
    split = _split_unified_diff_by_file(raw_diff)
    assert "app/main.py" in split
    assert split["app/main.py"]["additions"] == 1
    assert split["app/main.py"]["deletions"] == 0
    assert "+import sys" in str(split["app/main.py"]["patch"])

    secret = "whsec_bitbucket_test_secret_123456"
    project = _make_project()
    project.sandbox_config = {
        "webhook_config": {
            "enabled": True,
            "secret_encrypted": webhooks._encrypt_webhook_secret(secret),
        }
    }
    seed = _make_seed(project_id=project.id)
    seed.number = 4

    session = AsyncMock()
    session.get = AsyncMock(return_value=project)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=seed))
    reconcile_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(webhooks, "reconcile_seed_if_pr_merged_externally", reconcile_mock)

    body_dict = {
        "repository": {"full_name": "workspace/repo"},
        "actor": {"nickname": "alice"},
        "pullrequest": {
            "id": 9,
            "title": "Seed #4 implementation",
            "source": {"branch": {"name": "gitseed/seed-4-fix"}},
            "links": {"html": {"href": "https://bitbucket.org/workspace/repo/pull-requests/9"}},
            "merge_commit": {"hash": "deadbeef1234"},
        },
    }
    raw_body = json.dumps(body_dict).encode("utf-8")
    valid_sig = "sha256=" + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()

    assert webhooks.verify_bitbucket_signature(raw_body, secret, valid_sig) is True
    assert webhooks.verify_bitbucket_signature(raw_body, secret, "sha256=bad") is False

    with pytest.raises(Forbidden, match="Invalid X-Hub-Signature"):
        await webhooks.handle_bitbucket_webhook(
            session,
            project.id,
            event_name="pullrequest:fulfilled",
            raw_body=raw_body,
            signature_header="sha256=invalid",
            payload=body_dict,
        )

    resp = await webhooks.handle_bitbucket_webhook(
        session,
        project.id,
        event_name="pullrequest:fulfilled",
        raw_body=raw_body,
        signature_header=valid_sig,
        payload=body_dict,
        delivery_id="req-bb-1",
    )
    assert resp.accepted is True
    assert resp.provider == "bitbucket"
    assert resp.action == "seed_merged"
    reconcile_mock.assert_awaited_once()

    bb_backend = _make_backend(btype=BackendType.GIT)
    bb_backend.provider = "bitbucket"
    bb_backend.endpoint = "https://api.bitbucket.org/2.0"
    bb_backend.secrets_encrypted = encrypt_secrets({"token": "bb_tok"})
    bb_repo = ProjectRepository(
        id=2,
        project_id=project.id,
        backend_id=bb_backend.id,
        full_name="workspace/repo",
        default_branch="main",
        is_primary=True,
    )
    monkeypatch.setattr("gitseed_core.services.seeds.get_seed", AsyncMock(return_value=seed))

    class _MockBBClient:
        def __init__(self, **_kwargs: Any) -> None:
            pass

        async def __aenter__(self) -> _MockBBClient:
            return self

        async def __aexit__(self, *_args: Any) -> None:
            return None

        async def get(
            self, url: str, headers: dict[str, str] | None = None, params: Any = None
        ) -> httpx.Response:
            if "/diffstat/" in url:
                return httpx.Response(
                    200,
                    json={
                        "values": [
                            {
                                "status": "modified",
                                "lines_added": 1,
                                "lines_removed": 0,
                                "new": {"path": "app/main.py"},
                                "old": {"path": "app/main.py"},
                            }
                        ]
                    },
                )
            if "/diff/" in url:
                return httpx.Response(200, text=raw_diff)
            if "/commits/" in url:
                return httpx.Response(
                    200,
                    json={
                        "values": [
                            {
                                "hash": "abcdef1234567",
                                "message": "Add sys import",
                                "author": {"raw": "Alice <alice@example.com>"},
                                "date": "2026-11-11T00:00:00Z",
                            }
                        ]
                    },
                )
            return httpx.Response(404)

    monkeypatch.setattr("gitseed_core.services.git_changes.httpx.AsyncClient", _MockBBClient)
    monkeypatch.setattr(
        "gitseed_core.services.git_changes.check_seed_pull_request_status",
        AsyncMock(return_value={}),
    )
    changes_session = AsyncMock()
    changes_session.get = AsyncMock(
        side_effect=lambda _model, ident: {
            project.id: project,
            bb_backend.id: bb_backend,
        }.get(ident)
    )
    changes_session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[bb_repo]),
            _exec_result(scalars_all=[]),
        ]
    )
    changes = await get_seed_changes(changes_session, _make_user(), seed.id)
    assert changes["provider"] == "bitbucket"
    assert changes["branch_exists"] is True
    assert changes["total_files"] == 1
    assert changes["files"][0]["path"] == "app/main.py"
    assert "+import sys" in str(changes["files"][0]["patch"])


# ---------------------------------------------------------------------------
# 4. Local Git Security Hardening
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_local_git_rejects_unauthenticated_loopback_clone_requests() -> None:
    fake_db = AsyncMock()

    async def _override_db() -> Any:
        yield fake_db

    app.dependency_overrides[get_session] = _override_db
    try:
        transport = httpx.ASGITransport(app=app, client=("127.0.0.1", 54321))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1") as ac:
            resp = await ac.get(
                "/api/v1/local-git/alpha-project.git/info/refs?service=git-upload-pack"
            )
        assert resp.status_code == 401
    finally:
        app.dependency_overrides.clear()

    mock_req = MagicMock()
    mock_req.headers = {}
    mock_req.cookies = {}
    mock_req.client.host = "127.0.0.1"
    with pytest.raises(Unauthorized):
        await local_git_api._verify_local_git_auth(
            mock_req, fake_db, repo_slug="alpha-project", require_write=False
        )


def test_local_git_blocks_home_root_and_sensitive_dot_directories(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = Path.home().resolve()
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOTS", "")

    with pytest.raises(InvalidGitInputError):
        validate_workspace_path(home)
    with pytest.raises(InvalidGitInputError):
        validate_workspace_path(Path(home.anchor))

    for forbidden_sub in (".ssh", ".aws", ".gnupg", ".claude", ".gitseed", "AppData"):
        with pytest.raises(InvalidGitInputError):
            validate_workspace_path(home / forbidden_sub)

    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOTS", str(tmp_path))
    safe_ws = tmp_path / "clean-repo"
    safe_ws.mkdir()
    assert validate_workspace_path(safe_ws) == safe_ws.resolve()

    with pytest.raises(InvalidGitInputError):
        validate_workspace_path(tmp_path / ".ssh" / "keys")
    with pytest.raises(InvalidGitInputError):
        validate_workspace_path(tmp_path / ".hidden_dir" / "proj")

    cred_ws = tmp_path / "has-creds"
    (cred_ws / ".ssh").mkdir(parents=True)
    bare_dir = tmp_path / "bare.git"
    bare_dir.mkdir()
    with pytest.raises(InvalidGitInputError, match="sensitive folder"):
        _ensure_git_repo_initialized(
            cred_ws,
            bare_dir,
            project_name="Demo",
            default_branch="main",
        )
