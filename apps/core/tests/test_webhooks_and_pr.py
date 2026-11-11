from __future__ import annotations

import hashlib
import hmac
import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gitseed_common.enums import (
    ActivityType,
    ActorType,
    AgentRuntime,
    ApprovalDecision,
    BackendHealth,
    BackendType,
    RunStatus,
    SeedStage,
    SeedStatus,
)
from gitseed_common.schemas.project import UpdateProjectWebhookConfigRequest
from gitseed_core.models.agent import Agent
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.security import create_run_token, encrypt_secrets
from gitseed_core.services.activity import record
from gitseed_core.services.agents import compute_agent_benchmarks
from gitseed_core.services.compute_runtime import spawn_docker_runner_container
from gitseed_core.services.events import format_sse_message, subscribe_events
from gitseed_core.services.git_merge import (
    create_or_sync_seed_pull_request,
    ensure_seed_pull_request,
    post_seed_pull_request_comment,
)
from gitseed_core.services.runner_bridge import poll_runner_steering
from gitseed_core.services.webhooks import (
    get_project_webhook_config,
    handle_github_webhook,
    handle_gitlab_webhook,
    rotate_project_webhook_secret,
    sanitize_sandbox_config_for_response,
    update_project_webhook_config,
    verify_github_signature,
    verify_gitlab_token,
)

from conftest import _exec_result, _make_project, _make_run, _make_seed, _make_session, _make_user


def _sign_github(raw_body: bytes, secret: str) -> str:
    return (
        "sha256="
        + hmac.new(secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    )


class TestWebhooksAndPrAutomation:
    def test_signature_verifiers_and_sanitizer(self) -> None:
        raw = b'{"action":"opened"}'
        secret = "whsec_testsecret123456"
        sig = _sign_github(raw, secret)
        assert verify_github_signature(raw, secret, sig) is True
        assert verify_github_signature(raw, secret, "sha256=deadbeef") is False
        assert verify_github_signature(raw, secret, None) is False

        assert verify_gitlab_token(secret, secret) is True
        assert verify_gitlab_token(secret, "wrong") is False

        sanitized = sanitize_sandbox_config_for_response(
            {
                "require_tests_pass": True,
                "webhook_config": {
                    "enabled": True,
                    "secret_encrypted": "ciphertext_do_not_expose",
                    "secret_preview": "whsec_te...3456",
                },
            }
        )
        assert "secret_encrypted" not in sanitized["webhook_config"]
        assert sanitized["webhook_config"]["secret_preview"] == "whsec_te...3456"

    @pytest.mark.asyncio
    async def test_webhook_config_crud_and_rotate(self) -> None:
        session = _make_session()
        user = _make_user()
        project = _make_project()
        session.get.return_value = project

        cfg0 = await get_project_webhook_config(session, user, project.id)
        assert cfg0.enabled is False
        assert cfg0.has_secret is False
        assert cfg0.github_webhook_url.endswith(f"/api/v1/webhooks/github/{project.slug}")

        cfg1 = await update_project_webhook_config(
            session,
            user,
            project.id,
            UpdateProjectWebhookConfigRequest(
                enabled=True,
                issue_label_trigger="autoseed",
                auto_create_pr=True,
                outbound_url="https://example.com/hook",
            ),
        )
        assert cfg1.enabled is True
        assert cfg1.has_secret is True
        assert cfg1.secret is not None and cfg1.secret.startswith("whsec_")
        assert cfg1.issue_label_trigger == "autoseed"
        assert cfg1.auto_create_pr is True
        assert project.sandbox_config["auto_create_pr"] is True

        first_secret = cfg1.secret
        cfg2 = await rotate_project_webhook_secret(session, user, project.id)
        assert cfg2.secret is not None
        assert cfg2.secret != first_secret

    @pytest.mark.asyncio
    async def test_github_webhook_issue_comment_and_ci_remediation(self) -> None:
        session = _make_session()
        user = _make_user()
        project = _make_project()
        session.get.return_value = project

        cfg = await update_project_webhook_config(
            session,
            user,
            project.id,
            UpdateProjectWebhookConfigRequest(enabled=True, secret="whsec_fixedsecret999"),
        )
        assert cfg.has_secret is True
        secret = "whsec_fixedsecret999"

        # 1. Ping
        ping_raw = b'{"zen":"Keep it logically awesome."}'
        res_ping = await handle_github_webhook(
            session,
            project.id,
            event_name="ping",
            raw_body=ping_raw,
            signature_header=_sign_github(ping_raw, secret),
            payload=json.loads(ping_raw),
        )
        assert res_ping.accepted is True
        assert res_ping.action == "pong"

        # 2. Labeled issue creates a Seed
        issue_payload = {
            "action": "opened",
            "issue": {
                "number": 42,
                "title": "Add OAuth PKCE flow",
                "body": "Support PKCE in login handler",
                "html_url": "https://github.com/org/api/issues/42",
                "author_association": "MEMBER",
                "labels": [{"name": "gitseed"}, {"name": "enhancement"}],
            },
            "sender": {"login": "alice"},
        }
        issue_raw = json.dumps(issue_payload).encode("utf-8")
        session.execute.side_effect = [
            _exec_result(scalars_all=[]),  # _find_seed_by_number_or_tag
            _exec_result(scalar_one=8),  # seed_counter increment
        ]
        with patch(
            "gitseed_core.services.webhooks.first_enabled_stage",
            AsyncMock(return_value=SeedStage.IDEATE),
        ):
            res_issue = await handle_github_webhook(
                session,
                project.id,
                event_name="issues",
                raw_body=issue_raw,
                signature_header=_sign_github(issue_raw, secret),
                payload=issue_payload,
            )
        assert res_issue.accepted is True
        assert res_issue.action == "seed_created"
        assert res_issue.seed_number == 8

        # 3. Workflow run failure on gitseed/alpha-project/seed-8 queues CI remediation
        seed = _make_seed()
        seed.number = 8
        seed.stage = SeedStage.REVIEW
        seed.status = SeedStatus.AWAITING_APPROVAL
        ci_payload = {
            "workflow_run": {
                "name": "Unit Tests",
                "conclusion": "failure",
                "head_branch": "gitseed/alpha-project/seed-8",
                "html_url": "https://github.com/org/api/actions/runs/101",
            }
        }
        ci_raw = json.dumps(ci_payload).encode("utf-8")
        session.execute.side_effect = [
            _exec_result(scalar_one_or_none=seed),  # _find_seed_by_number_or_tag
            *[
                _exec_result(scalar_one_or_none=None, scalars_all=[])
                for _ in range(10)
            ],
        ]
        session.scalar = AsyncMock(side_effect=[None, 2])
        with patch(
            "gitseed_core.services.git_merge.check_seed_pull_request_status",
            AsyncMock(return_value={"ci_status": "failing", "failing_checks": ["Unit Tests"]}),
        ):
            res_ci = await handle_github_webhook(
                session,
                project.id,
                event_name="workflow_run",
                raw_body=ci_raw,
                signature_header=_sign_github(ci_raw, secret),
                payload=ci_payload,
            )
        assert res_ci.accepted is True
        assert res_ci.action == "ci_remediation_queued"
        assert seed.stage == SeedStage.EXECUTE
        assert seed.status == SeedStatus.QUEUED

    @pytest.mark.asyncio
    async def test_gitlab_webhook_note_steering(self) -> None:
        session = _make_session()
        user = _make_user()
        project = _make_project()
        session.get.return_value = project

        await update_project_webhook_config(
            session,
            user,
            project.id,
            UpdateProjectWebhookConfigRequest(enabled=True, secret="gl_secret_token"),
        )

        seed = _make_seed()
        seed.number = 5
        seed.stage = SeedStage.EXECUTE
        seed.status = SeedStatus.RUNNING

        note_payload = {
            "object_kind": "note",
            "user": {"id": 42, "username": "bob", "access_level": 30},
            "merge_request": {
                "iid": 11,
                "source_branch": "gitseed/alpha-project/seed-5",
                "title": "Seed #5",
            },
            "object_attributes": {
                "note": "@coder please use connection pooling here",
                "url": "https://gitlab.com/org/api/-/merge_requests/11#note_1",
            },
        }
        session.execute.side_effect = [
            _exec_result(scalar_one_or_none=seed),
            _exec_result(scalars_all=[]),
        ]
        with patch(
            "gitseed_core.services.webhooks._mentions_stage_agent",
            AsyncMock(return_value=True),
        ):
            res_note = await handle_gitlab_webhook(
                session,
                project.id,
                event_name="Note Hook",
                token_header="gl_secret_token",
                payload=note_payload,
            )
        assert res_note.accepted is True
        assert res_note.action == "live_steering_queued"
        assert res_note.seed_number == 5

    @pytest.mark.asyncio
    async def test_ensure_seed_pull_request_github(self) -> None:
        session = _make_session()
        project = _make_project()
        seed = _make_seed()
        seed.number = 7
        repo = ProjectRepository(
            id=1,
            project_id=project.id,
            backend_id="bnd_git1",
            full_name="org/api",
            default_branch="main",
            is_primary=True,
        )
        backend = Backend(
            id="bnd_git1",
            name="GitHub",
            type=BackendType.GIT,
            provider="github",
            endpoint="https://api.github.com",
            config={},
            secrets_encrypted=encrypt_secrets({"token": "ghp_tok"}),
            health=BackendHealth.HEALTHY,
            enabled=True,
        )
        session.get.return_value = backend
        session.execute.return_value = _exec_result(scalars_all=[repo])

        class _MockGhClient:
            def __init__(self, **kwargs: Any) -> None:
                pass

            async def __aenter__(self) -> _MockGhClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def post(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 201
                resp.json.return_value = {
                    "html_url": "https://github.com/org/api/pull/77"
                }
                return resp

        with patch("gitseed_core.services.git_merge.httpx.AsyncClient", _MockGhClient):
            pr_url = await ensure_seed_pull_request(
                session,
                seed,
                project,
                branch_name="gitseed/alpha-project/seed-7",
            )
        assert pr_url == "https://github.com/org/api/pull/77"

        # Verify create_or_sync_seed_pull_request and post_seed_pull_request_comment
        user = _make_user()
        session.get = AsyncMock(
            side_effect=lambda model, ident: seed if model is type(seed) else (project if ident == project.id else backend)
        )
        session.execute = AsyncMock(return_value=_exec_result(first=(repo, backend)))
        with (
            patch(
                "gitseed_core.services.git_merge.projects_service.require_project_contributor",
                AsyncMock(return_value=project),
            ),
            patch(
                "gitseed_core.services.git_merge._resolve_seed_branch_name",
                AsyncMock(return_value="gitseed/alpha-project/seed-7"),
            ),
            patch(
                "gitseed_core.services.git_merge.check_seed_pull_request_status",
                AsyncMock(
                    return_value={
                        "pull_request_url": "https://github.com/org/api/pull/77",
                        "number": 77,
                        "repository_full_name": "org/api",
                        "state": "open",
                    }
                ),
            ),
            patch("gitseed_core.services.git_merge.httpx.AsyncClient", _MockGhClient),
            patch("gitseed_core.services.seeds.create_comment", AsyncMock()) as mock_create_comment,
        ):
            synced = await create_or_sync_seed_pull_request(session, user, seed.id)
            assert synced["number"] == 77
            comment_res = await post_seed_pull_request_comment(
                session, user, seed.id, "Looks good to merge!", mirror_to_seed=True
            )
            assert comment_res["body"] == "Looks good to merge!"
            mock_create_comment.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_spawn_docker_runner_container_mounts_warm_cache_volume(self) -> None:
        run = _make_run()
        seed = _make_seed()
        project = _make_project()
        backend = Backend(
            id="bnd_comp1",
            name="Local Docker",
            type=BackendType.COMPUTE,
            provider="docker",
            endpoint="http://docker:2375",
            config={"network": "gitseed-runners"},
            secrets_encrypted=b"",
            health=BackendHealth.HEALTHY,
            enabled=True,
        )

        captured_create_json: dict[str, Any] = {}

        class _FakeDockerClient:
            async def __aenter__(self) -> _FakeDockerClient:
                return self

            async def __aexit__(self, *args: Any) -> None:
                return None

            async def get(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 200
                return resp

            async def delete(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                resp.status_code = 204
                return resp

            async def post(self, url: str, **kwargs: Any) -> Any:
                resp = MagicMock()
                if "/containers/create" in url:
                    captured_create_json.update(kwargs.get("json") or {})
                    resp.status_code = 201
                    resp.json.return_value = {"Id": "container_1234567890abcdef"}
                else:
                    resp.status_code = 204
                return resp

        with patch(
            "gitseed_core.services.compute_runtime._docker_client_for_backend",
            return_value=(_FakeDockerClient(), "http://docker:2375"),
        ):
            cid = await spawn_docker_runner_container(
                backend=backend,
                run=run,
                seed=seed,
                project=project,
                run_token="gsr_tok",
            )

        assert cid == "container_1234567890abcdef"
        binds = captured_create_json["HostConfig"]["Binds"]
        assert f"gitseed-cache-{project.id.lower()}:/home/runner/.cache/packages" in binds
        env_list = captured_create_json["Env"]
        assert "UV_CACHE_DIR=/home/runner/.cache/packages/uv" in env_list
        assert "NPM_CONFIG_CACHE=/home/runner/.cache/packages/npm" in env_list

    @pytest.mark.asyncio
    async def test_sse_events_and_live_runner_log_streaming(self) -> None:
        session = _make_session()
        run = _make_run()
        run.status = RunStatus.RUNNING
        seed = _make_seed()
        agent = Agent(
            id="agt_1",
            handle="coder",
            name="Coder",
            kind="stage",
            template="coder",
            instructions="Write code",
            runtime="opencode",
            model="gpt-4o",
        )

        async def _fake_get(model_cls: Any, ident: str) -> Any:
            if ident == run.id:
                return run
            if ident == seed.id:
                return seed
            if ident == agent.id:
                return agent
            return None

        session.get.side_effect = _fake_get
        session.execute.return_value = _exec_result(scalars_all=[])

        sub = subscribe_events().__aiter__()
        try:
            # 1. Recording an activity automatically broadcasts an SSE event
            record(
                session,
                type=ActivityType.SEED_CREATED,
                actor_type=ActorType.USER,
                actor_id="usr_1",
                project_id=seed.project_id,
                seed_id=seed.id,
                data={"number": seed.number},
            )
            ev1 = await sub.__anext__()
            assert ev1["type"] == str(ActivityType.SEED_CREATED)
            assert ev1["seed_id"] == seed.id
            sse_frame = format_sse_message(ev1)
            assert "event: gitseed_event" in sse_frame
            assert f'"seed_id": "{seed.id}"' in sse_frame

            # 2. Polling steering with log_tail persists live log_tail on run.output and broadcasts run.log
            token = create_run_token(run.id)
            steer_resp = await poll_runner_steering(
                session,
                token,
                log_tail="Step 1: Running tests...\nStep 2: All tests passed!",
            )
            assert steer_resp.should_abort is False
            assert (
                run.output["log_tail"]
                == "Step 1: Running tests...\nStep 2: All tests passed!"
            )

            ev2 = await sub.__anext__()
            assert ev2["type"] == "run.log"
            assert ev2["data"]["run_id"] == run.id
            assert "Step 2: All tests passed!" in ev2["data"]["log_tail"]
        finally:
            await sub.aclose()  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_empirical_agent_model_benchmarking(self) -> None:
        session = _make_session()
        agent_sonnet = Agent(
            id="agt_sonnet",
            handle="coder-sonnet",
            name="Coder (Sonnet)",
            kind="stage",
            runtime=AgentRuntime.CLAUDE_CODE,
            model="claude-sonnet-4-20250514",
        )
        agent_gpt = Agent(
            id="agt_gpt",
            handle="coder-gpt",
            name="Coder (GPT)",
            kind="stage",
            runtime=AgentRuntime.OPENCODE,
            model="gpt-4o-mini",
        )

        # Agent Sonnet: 2 seeds in execute, both succeeded on attempt 1 and approved on 1st pass ($1.40/seed)
        r1 = Run(
            id="run_s1",
            seed_id="sed_1",
            stage=SeedStage.EXECUTE,
            attempt=1,
            agent_id=agent_sonnet.id,
            runtime=AgentRuntime.CLAUDE_CODE,
            model="claude-sonnet-4-20250514",
            status=RunStatus.SUCCEEDED,
            input_tokens=10000,
            output_tokens=2000,
            cost=Decimal("1.40"),
        )
        r2 = Run(
            id="run_s2",
            seed_id="sed_2",
            stage=SeedStage.EXECUTE,
            attempt=1,
            agent_id=agent_sonnet.id,
            runtime=AgentRuntime.CLAUDE_CODE,
            model="claude-sonnet-4-20250514",
            status=RunStatus.SUCCEEDED,
            input_tokens=10000,
            output_tokens=2000,
            cost=Decimal("1.40"),
        )

        # Agent GPT: 1 seed in execute, needed 2 attempts (attempt 1 had changes_requested, attempt 2 approved) ($0.60/seed)
        r3_a1 = Run(
            id="run_g1",
            seed_id="sed_3",
            stage=SeedStage.EXECUTE,
            attempt=1,
            agent_id=agent_gpt.id,
            runtime=AgentRuntime.OPENCODE,
            model="gpt-4o-mini",
            status=RunStatus.SUCCEEDED,
            input_tokens=5000,
            output_tokens=1000,
            cost=Decimal("0.30"),
        )
        r3_a2 = Run(
            id="run_g2",
            seed_id="sed_3",
            stage=SeedStage.EXECUTE,
            attempt=2,
            agent_id=agent_gpt.id,
            runtime=AgentRuntime.OPENCODE,
            model="gpt-4o-mini",
            status=RunStatus.SUCCEEDED,
            input_tokens=5000,
            output_tokens=1000,
            cost=Decimal("0.30"),
        )

        approvals = [
            Approval(
                id="apr_1",
                seed_id="sed_1",
                stage=SeedStage.EXECUTE,
                decision=ApprovalDecision.APPROVED,
            ),
            Approval(
                id="apr_2",
                seed_id="sed_2",
                stage=SeedStage.EXECUTE,
                decision=ApprovalDecision.APPROVED,
            ),
            Approval(
                id="apr_3",
                seed_id="sed_3",
                stage=SeedStage.EXECUTE,
                decision=ApprovalDecision.CHANGES_REQUESTED,
                comment="Fix edge case",
            ),
        ]

        session.execute.side_effect = [
            _exec_result(
                all_rows=[
                    (r1, agent_sonnet, "prj_1"),
                    (r2, agent_sonnet, "prj_1"),
                    (r3_a1, agent_gpt, "prj_1"),
                    (r3_a2, agent_gpt, "prj_1"),
                ]
            ),
            _exec_result(scalars_all=approvals),
        ]

        res = await compute_agent_benchmarks(session, project_id="prj_1")
        assert len(res.entries) == 2
        top, second = res.entries[0], res.entries[1]
        assert top.agent_handle == "coder-sonnet"
        assert top.first_pass_approval_rate == 100.0
        assert top.avg_cost_per_seed == Decimal("1.40")
        assert top.avg_attempts_per_stage == 1.0

        assert second.agent_handle == "coder-gpt"
        assert second.first_pass_approval_rate == 0.0
        assert second.avg_cost_per_seed == Decimal("0.60")
        assert second.avg_attempts_per_stage == 2.0

        assert len(res.insights) == 1
        assert "@coder-sonnet" in res.insights[0]
        assert "$1.40/seed" in res.insights[0]
        assert "@coder-gpt" in res.insights[0]
        assert "$0.60/seed" in res.insights[0]

    @pytest.mark.asyncio
    async def test_webhook_secret_required_and_outbound_ssrf_protection(self) -> None:
        from gitseed_core.services.errors import Forbidden, Invalid
        from gitseed_core.services.webhooks import dispatch_outbound_webhook

        session = _make_session()
        user = _make_user()
        project = _make_project()
        project.sandbox_config = {"webhook_config": {"enabled": True}}
        session.get.return_value = project
        session.execute.return_value = _exec_result(scalar_one_or_none=project)

        # Unauthenticated webhook must fail closed when no secret is configured
        with patch(
            "gitseed_core.services.webhooks._resolve_webhook_secret",
            AsyncMock(return_value=None),
        ):
            with pytest.raises(Forbidden):
                await handle_github_webhook(
                    session,
                    project.id,
                    event_name="ping",
                    raw_body=b"{}",
                    signature_header=None,
                    payload={},
                )
            with pytest.raises(Forbidden):
                await handle_gitlab_webhook(
                    session,
                    project.id,
                    event_name="Issue Hook",
                    token_header=None,
                    payload={},
                )

        # Outbound SSRF targets (loopback, metadata IP, internal container hostnames) are rejected
        for blocked_url in (
            "http://127.0.0.1:8420/admin",
            "http://localhost/hook",
            "http://169.254.169.254/latest/meta-data/",
            "http://10.0.0.5:8080/internal",
            "http://litellm:4000/key/generate",
            "http://api.gitseed.svc.cluster.local/hook",
        ):
            with pytest.raises(Invalid):
                await update_project_webhook_config(
                    session,
                    user,
                    project.id,
                    UpdateProjectWebhookConfigRequest(outbound_url=blocked_url),
                )

        project.sandbox_config = {
            "webhook_config": {
                "enabled": True,
                "outbound_url": "http://169.254.169.254/latest/meta-data/",
            }
        }
        ok, status_code, msg = await dispatch_outbound_webhook(
            project, "seed.created", {"id": "sed_1"}, force=True
        )
        assert ok is False
        assert status_code is None
        assert "cannot target" in msg

    @pytest.mark.asyncio
    async def test_local_git_token_scoping_and_k8s_archive_positional_args(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from fastapi import Request
        from gitseed_core.api.v1.local_git import _verify_local_git_auth
        from gitseed_core.services.compute_runtime import k8s_fetch_archive, k8s_put_archive
        from gitseed_core.services.errors import Forbidden, Unauthorized
        from gitseed_core.services.git_auth import resolve_git_backend_token

        session = _make_session()
        local_backend = Backend(
            id="bnd_local",
            name="Local Git",
            type=BackendType.GIT,
            provider="local",
        )
        write_tok = await resolve_git_backend_token(
            local_backend, repo_full_names=["local/alpha-project"], write_access=True
        )
        read_tok = await resolve_git_backend_token(
            local_backend, repo_full_names=["local/alpha-project"], write_access=False
        )
        run_tok = create_run_token("run_abc123456")

        def _req_with_bearer(tok: str) -> Request:
            scope = {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/local-git/alpha-project.git/info/refs",
                "headers": [(b"authorization", f"Bearer {tok}".encode())],
                "query_string": b"",
                "client": ("10.1.2.3", 54321),
            }
            return Request(scope)

        # Raw run_<id> token is rejected on local-git endpoints
        with pytest.raises(Unauthorized):
            await _verify_local_git_auth(
                _req_with_bearer(run_tok),
                session,
                repo_slug="alpha-project",
                require_write=False,
            )

        # Scoped read token can read alpha-project, cannot write, and cannot read other-project
        await _verify_local_git_auth(
            _req_with_bearer(read_tok),
            session,
            repo_slug="alpha-project",
            require_write=False,
        )
        with pytest.raises(Forbidden):
            await _verify_local_git_auth(
                _req_with_bearer(read_tok),
                session,
                repo_slug="alpha-project",
                require_write=True,
            )
        with pytest.raises(Forbidden):
            await _verify_local_git_auth(
                _req_with_bearer(write_tok),
                session,
                repo_slug="other-project",
                require_write=False,
            )
        await _verify_local_git_auth(
            _req_with_bearer(write_tok),
            session,
            repo_slug="alpha-project",
            require_write=True,
        )

        # Verify k8s_put_archive and k8s_fetch_archive pass paths via positional args ($1, $2, $3)
        recorded_k8s_cmds: list[list[str]] = []

        async def _fake_k8s_exec(
            backend: Backend,
            container_id: str,
            cmd: list[str],
            **kwargs: Any,
        ) -> tuple[int, bytes, bytes]:
            recorded_k8s_cmds.append(cmd)
            return 0, b"tar_data", b""

        monkeypatch.setattr(
            "gitseed_core.services.compute_runtime.k8s_exec_command", _fake_k8s_exec
        )
        tricky_path = "/workspace/dir'$(id)'"
        await k8s_put_archive(local_backend, "pod-1", tricky_path, b"12345")
        await k8s_fetch_archive(local_backend, "pod-1", tricky_path)
        assert recorded_k8s_cmds[0][2] == 'mkdir -p "$1" && head -c "$2" | tar -xf - -C "$1"'
        assert recorded_k8s_cmds[0][4] == tricky_path
        assert recorded_k8s_cmds[1][2] == 'test -e "$1" && tar -cf - -C "$2" "$3"'
        assert recorded_k8s_cmds[1][4] == tricky_path



