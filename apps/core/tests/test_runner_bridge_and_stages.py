from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from gitseed_common.enums import (
    ActorType,
    AgentKind,
    AgentRuntime,
    OrgRole,
    ProjectRole,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
    UserStatus,
)
from gitseed_core.models.agent import Agent
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment, SeedTask
from gitseed_core.models.user import User
from gitseed_core.schemas.runner import RunnerCompleteRequest
from gitseed_core.services.git_merge import reconcile_seed_if_pr_merged_externally
from gitseed_core.services.policies import EffectiveStagePolicy
from gitseed_core.services.runner_bridge import (
    complete_runner_execution,
    poll_runner_steering,
)


def _make_policy(
    stage: SeedStage,
    *,
    requires_approval: bool = False,
    allow_auto_approve: bool = True,
    max_attempts: int = 3,
) -> EffectiveStagePolicy:
    return EffectiveStagePolicy(
        id="pol_test",
        project_id="prj_1",
        stage=stage,
        enabled=True,
        agent_id="agt_1",
        requires_approval=requires_approval,
        min_approvers=1,
        min_approver_role=ProjectRole.DEVELOPER,
        allow_auto_approve=allow_auto_approve,
        allow_self_approval=True,
        max_attempts=max_attempts,
        timeout_minutes=30,
    )


def _make_entities(
    *,
    stage: SeedStage = SeedStage.IDEATE,
    attempt: int = 1,
    autonomy: SeedAutonomy = SeedAutonomy.SUPERVISED,
    task_id: str | None = None,
) -> tuple[Run, Seed, Project, Agent]:
    now = datetime.now(UTC)
    project = Project(
        id="prj_1",
        name="Alpha Project",
        slug="alpha",
    )
    seed = Seed(
        id="sed_1",
        project_id=project.id,
        number=1,
        title="Initial Seed Title",
        description="Initial seed description",
        specification="",
        acceptance_criteria=[],
        stage=stage,
        status=SeedStatus.RUNNING,
        autonomy=autonomy,
        spent=Decimal("0"),
        created_at=now,
        updated_at=now,
    )
    agent = Agent(
        id="agt_1",
        handle="coder",
        name="Coder Agent",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="default",
        enabled=True,
    )
    run = Run(
        id="run_1",
        seed_id=seed.id,
        task_id=task_id,
        stage=stage,
        attempt=attempt,
        agent_id=agent.id,
        runtime=AgentRuntime.OPENCODE,
        model="default",
        status=RunStatus.RUNNING,
        started_at=now,
        created_at=now,
        updated_at=now,
    )
    return run, seed, project, agent


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return list(self._items)

    def first(self) -> Any:
        return self._items[0] if self._items else None

    def scalar_one_or_none(self) -> Any:
        return self._items[0] if self._items else None


class TestPollRunnerSteering:
    @pytest.mark.asyncio
    async def test_returns_comments_from_org_admin_with_agent_or_handle_mention(
        self,
    ) -> None:
        run, seed, _, agent = _make_entities(stage=SeedStage.EXECUTE)
        now = datetime.now(UTC)

        admin_user = User(
            id="usr_admin",
            email="admin@example.com",
            name="Alice Admin",
            preferred_name="Alice",
            org_role=OrgRole.ADMIN,
            status=UserStatus.ACTIVE,
        )
        c1 = SeedComment(
            id="cmt_1",
            seed_id=seed.id,
            author_type=ActorType.USER,
            author_id=admin_user.id,
            stage=SeedStage.EXECUTE,
            body="Hey @agent please use async httpx here.",
            created_at=now,
        )
        c2 = SeedComment(
            id="cmt_2",
            seed_id=seed.id,
            author_type=ActorType.USER,
            author_id=admin_user.id,
            stage=SeedStage.EXECUTE,
            body="@coder also add retry backoff.",
            created_at=now,
        )
        c_unmentioned = SeedComment(
            id="cmt_3",
            seed_id=seed.id,
            author_type=ActorType.USER,
            author_id=admin_user.id,
            stage=SeedStage.EXECUTE,
            body="General note without mentioning the agent.",
            created_at=now,
        )

        session = AsyncMock()

        async def _get(model: Any, pk: Any) -> Any:
            if model is Run and pk == run.id:
                return run
            if model is Seed and pk == seed.id:
                return seed
            if model is Agent and pk == agent.id:
                return agent
            if model is User and pk == admin_user.id:
                return admin_user
            return None

        session.get.side_effect = _get
        session.execute.return_value = _ScalarResult([c1, c2, c_unmentioned])

        with patch(
            "gitseed_core.services.runner_bridge.verify_run_token",
            return_value=run.id,
        ):
            res = await poll_runner_steering(session, "gsr_valid_token")

        assert res.should_abort is False
        assert [item.id for item in res.steering_comments] == ["cmt_1", "cmt_2"]
        assert res.steering_comments[0].author_name == "Alice"

    @pytest.mark.asyncio
    async def test_filters_member_roles_allowing_developer_and_maintainer_but_not_viewer(
        self,
    ) -> None:
        run, seed, _, agent = _make_entities(stage=SeedStage.EXECUTE)
        now = datetime.now(UTC)

        dev_user = User(
            id="usr_dev",
            email="dev@example.com",
            name="Bob Dev",
            org_role=OrgRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        maint_user = User(
            id="usr_maint",
            email="maint@example.com",
            name="Carol Maintainer",
            org_role=OrgRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        viewer_user = User(
            id="usr_viewer",
            email="viewer@example.com",
            name="Dave Viewer",
            org_role=OrgRole.MEMBER,
            status=UserStatus.ACTIVE,
        )
        outsider_user = User(
            id="usr_outsider",
            email="outsider@example.com",
            name="Eve Outsider",
            org_role=OrgRole.MEMBER,
            status=UserStatus.ACTIVE,
        )

        users_by_id = {
            u.id: u for u in (dev_user, maint_user, viewer_user, outsider_user)
        }
        comments = [
            SeedComment(
                id="cmt_dev",
                seed_id=seed.id,
                author_type=ActorType.USER,
                author_id=dev_user.id,
                stage=SeedStage.EXECUTE,
                body="@agent focus on developer feedback",
                created_at=now,
            ),
            SeedComment(
                id="cmt_maint",
                seed_id=seed.id,
                author_type=ActorType.USER,
                author_id=maint_user.id,
                stage=SeedStage.EXECUTE,
                body="@coder maintainer instruction",
                created_at=now,
            ),
            SeedComment(
                id="cmt_viewer",
                seed_id=seed.id,
                author_type=ActorType.USER,
                author_id=viewer_user.id,
                stage=SeedStage.EXECUTE,
                body="@agent viewer trying to steer",
                created_at=now,
            ),
            SeedComment(
                id="cmt_outsider",
                seed_id=seed.id,
                author_type=ActorType.USER,
                author_id=outsider_user.id,
                stage=SeedStage.EXECUTE,
                body="@agent non-member trying to steer",
                created_at=now,
            ),
        ]

        memberships = [
            ProjectMember(
                project_id=seed.project_id,
                user_id=dev_user.id,
                role=ProjectRole.DEVELOPER,
            ),
            ProjectMember(
                project_id=seed.project_id,
                user_id=maint_user.id,
                role=ProjectRole.MAINTAINER,
            ),
            ProjectMember(
                project_id=seed.project_id,
                user_id=viewer_user.id,
                role=ProjectRole.VIEWER,
            ),
            None,  # outsider
        ]

        session = AsyncMock()

        async def _get(model: Any, pk: Any) -> Any:
            if model is Run and pk == run.id:
                return run
            if model is Seed and pk == seed.id:
                return seed
            if model is Agent and pk == agent.id:
                return agent
            if model is User:
                return users_by_id.get(pk)
            return None

        session.get.side_effect = _get
        execute_results = [_ScalarResult(comments)] + [
            _ScalarResult([m] if m is not None else []) for m in memberships
        ]
        session.execute.side_effect = execute_results

        with patch(
            "gitseed_core.services.runner_bridge.verify_run_token",
            return_value=run.id,
        ):
            res = await poll_runner_steering(session, "gsr_valid_token")

        assert res.should_abort is False
        assert [c.id for c in res.steering_comments] == ["cmt_dev", "cmt_maint"]


class TestCompleteRunnerExecution:
    @pytest.mark.asyncio
    async def test_ideate_stage_updates_seed_title_description_spec_and_criteria(
        self,
    ) -> None:
        run, seed, project, agent = _make_entities(stage=SeedStage.IDEATE)
        pol = _make_policy(SeedStage.IDEATE, requires_approval=True)
        session = AsyncMock()
        session.add = MagicMock()

        payload = RunnerCompleteRequest(
            status=RunStatus.SUCCEEDED,
            exit_code=0,
            input_tokens=120,
            output_tokens=250,
            stage_output={
                "title": "Refined OAuth PKCE Title",
                "description": "Concise executive summary of PKCE flow.",
                "specification": "## Technical Specification\nComplete OAuth PKCE flow details.",
                "acceptance_criteria": [
                    "Generates S256 code challenge",
                    "Verifies state parameter",
                ],
                "summary_comment": "Completed ideation specification.",
            },
        )

        with (
            patch(
                "gitseed_core.services.runner_bridge._authenticate_active_run",
                return_value=(run, seed, project, agent),
            ),
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                return_value=pol,
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                new_callable=AsyncMock,
            ),
        ):
            updated_run = await complete_runner_execution(
                session, "gsr_token", payload
            )

        assert updated_run.status == RunStatus.SUCCEEDED
        assert seed.title == "Refined OAuth PKCE Title"
        assert seed.description == "Concise executive summary of PKCE flow."
        assert seed.specification == "## Technical Specification\nComplete OAuth PKCE flow details."
        assert seed.acceptance_criteria == [
            "Generates S256 code challenge",
            "Verifies state parameter",
        ]
        assert updated_run.output["previous_title"] == "Initial Seed Title"
        assert updated_run.output["previous_description"] == "Initial seed description"
        assert updated_run.output["previous_acceptance_criteria"] == []
        assert seed.status == SeedStatus.AWAITING_APPROVAL

    @pytest.mark.asyncio
    async def test_plan_stage_sets_seed_plan_and_creates_seed_task_rows(
        self,
    ) -> None:
        run, seed, project, agent = _make_entities(stage=SeedStage.PLAN)
        pol = _make_policy(SeedStage.PLAN, requires_approval=True)
        session = AsyncMock()
        added_objects: list[Any] = []
        session.add = MagicMock(side_effect=added_objects.append)

        payload = RunnerCompleteRequest(
            status=RunStatus.SUCCEEDED,
            exit_code=0,
            stage_output={
                "plan": "## Step-by-Step Plan\n1. Update models\n2. Add API endpoints",
                "tasks": [
                    {
                        "title": "Update database models",
                        "description": "Add columns and migration",
                    },
                    {
                        "title": "Add REST endpoints",
                        "description": "Expose router and tests",
                    },
                ],
                "target_repositories": [],
                "summary_comment": "Synthesized 2 tasks for implementation.",
            },
        )

        with (
            patch(
                "gitseed_core.services.runner_bridge._authenticate_active_run",
                return_value=(run, seed, project, agent),
            ),
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                return_value=pol,
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                new_callable=AsyncMock,
            ),
        ):
            await complete_runner_execution(session, "gsr_token", payload)

        assert seed.plan == "## Step-by-Step Plan\n1. Update models\n2. Add API endpoints"
        created_tasks = [obj for obj in added_objects if isinstance(obj, SeedTask)]
        assert len(created_tasks) == 2
        assert created_tasks[0].position == 0
        assert created_tasks[0].title == "Update database models"
        assert created_tasks[0].status == TaskStatus.PENDING
        assert created_tasks[1].position == 1
        assert created_tasks[1].title == "Add REST endpoints"

    @pytest.mark.asyncio
    async def test_review_stage_rejected_by_agent_creates_remediation_task_and_returns_to_execute(
        self,
    ) -> None:
        run, seed, project, agent = _make_entities(
            stage=SeedStage.REVIEW,
            autonomy=SeedAutonomy.SUPERVISED,
        )
        pol = _make_policy(SeedStage.REVIEW, requires_approval=True, max_attempts=2)
        session = AsyncMock()
        added_objects: list[Any] = []
        session.add = MagicMock(side_effect=added_objects.append)

        # scalar calls in advance_seed_after_succeeded_run and create_remediation_task_and_return_to_execute:
        # 1) latest_human_reset -> None
        # 2) review_run_count -> 1 (<= max_attempts)
        # 3) max_pos for SeedTask -> 1
        session.scalar.side_effect = [None, 1, 1]
        # execute call in create_remediation_task_and_return_to_execute looking for existing_pending task
        session.execute.return_value = _ScalarResult([])

        payload = RunnerCompleteRequest(
            status=RunStatus.SUCCEEDED,
            exit_code=0,
            stage_output={
                "approved": False,
                "findings": [
                    "Missing input validation in `apps/core/routes.py`",
                    "Unit test `test_auth` is failing",
                ],
                "summary_comment": "Review failed due to missing validation.",
            },
        )

        with (
            patch(
                "gitseed_core.services.runner_bridge._authenticate_active_run",
                return_value=(run, seed, project, agent),
            ),
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                return_value=pol,
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                new_callable=AsyncMock,
            ),
        ):
            await complete_runner_execution(session, "gsr_token", payload)

        assert seed.stage == SeedStage.EXECUTE
        assert seed.status == SeedStatus.QUEUED
        remediation_tasks = [obj for obj in added_objects if isinstance(obj, SeedTask)]
        assert len(remediation_tasks) == 1
        assert remediation_tasks[0].position == 2
        assert remediation_tasks[0].status == TaskStatus.PENDING
        assert "Missing input validation" in remediation_tasks[0].description

    @pytest.mark.asyncio
    async def test_failed_run_retry_and_exhaustion_transitions_seed_to_failed(
        self,
    ) -> None:
        # Case 1: attempt < max_attempts -> re-queues seed
        run_retry, seed_retry, project, agent = _make_entities(
            stage=SeedStage.EXECUTE, attempt=1
        )
        pol = _make_policy(SeedStage.EXECUTE, max_attempts=2)
        session = AsyncMock()
        session.add = MagicMock()

        fail_payload = RunnerCompleteRequest(
            status=RunStatus.FAILED,
            exit_code=1,
            error="Subprocess exited with code 1",
            log_tail="Traceback: error",
        )

        with (
            patch(
                "gitseed_core.services.runner_bridge._authenticate_active_run",
                return_value=(run_retry, seed_retry, project, agent),
            ),
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                return_value=pol,
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                new_callable=AsyncMock,
            ),
        ):
            await complete_runner_execution(session, "gsr_token", fail_payload)

        assert run_retry.status == RunStatus.FAILED
        assert seed_retry.status == SeedStatus.QUEUED

        # Case 2: attempt >= max_attempts -> transitions seed to SeedStatus.FAILED
        run_exhausted, seed_exhausted, _, _ = _make_entities(
            stage=SeedStage.EXECUTE, attempt=2
        )
        with (
            patch(
                "gitseed_core.services.runner_bridge._authenticate_active_run",
                return_value=(run_exhausted, seed_exhausted, project, agent),
            ),
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                return_value=pol,
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                new_callable=AsyncMock,
            ),
        ):
            await complete_runner_execution(session, "gsr_token", fail_payload)

        assert run_exhausted.status == RunStatus.FAILED
        assert seed_exhausted.status == SeedStatus.FAILED

    @pytest.mark.asyncio
    async def test_review_stage_ensures_pr_and_stores_pull_request_url(
        self,
    ) -> None:
        run, seed, project, agent = _make_entities(
            stage=SeedStage.REVIEW,
            autonomy=SeedAutonomy.SUPERVISED,
        )
        pol = _make_policy(SeedStage.REVIEW, requires_approval=True)
        session = AsyncMock()
        session.add = MagicMock()

        payload = RunnerCompleteRequest(
            status=RunStatus.SUCCEEDED,
            exit_code=0,
            stage_output={
                "approved": True,
                "findings": [],
                "summary_comment": "All acceptance criteria verified.",
            },
        )

        with (
            patch(
                "gitseed_core.services.runner_bridge._authenticate_active_run",
                return_value=(run, seed, project, agent),
            ),
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                return_value=pol,
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                new_callable=AsyncMock,
            ),
            patch(
                "gitseed_core.services.runner_bridge.ensure_seed_pull_request",
                new_callable=AsyncMock,
                return_value="https://github.com/acme/alpha/pull/42",
            ) as mock_pr,
        ):
            updated_run = await complete_runner_execution(
                session, "gsr_token", payload
            )

        mock_pr.assert_awaited_once()
        assert updated_run.output["pull_request_url"] == "https://github.com/acme/alpha/pull/42"
        assert seed.status == SeedStatus.AWAITING_APPROVAL

    @pytest.mark.asyncio
    async def test_external_pr_merge_reconciliation_marks_seed_merged(
        self,
    ) -> None:
        _, seed, project, _ = _make_entities(
            stage=SeedStage.REVIEW,
            autonomy=SeedAutonomy.SUPERVISED,
        )
        seed.status = SeedStatus.AWAITING_APPROVAL
        session = AsyncMock()
        added_objects: list[Any] = []
        session.add = MagicMock(side_effect=added_objects.append)
        exec_res = MagicMock()
        exec_res.scalars.return_value.first.return_value = None
        session.execute.return_value = exec_res

        reconciled = await reconcile_seed_if_pr_merged_externally(
            session,
            seed,
            project,
            pr_info={
                "merged": True,
                "provider": "github",
                "pull_request_url": "https://github.com/acme/alpha/pull/42",
                "merge_commit_sha": "abc1234567890",
                "merged_by": "octocat",
            },
        )

        assert reconciled is True
        assert seed.stage == SeedStage.MERGE
        assert seed.status == SeedStatus.MERGED
        assert seed.merged_at is not None
        comments = [obj for obj in added_objects if isinstance(obj, SeedComment)]
        assert len(comments) == 1
        assert "merged by **@octocat**" in comments[0].body
        assert "https://github.com/acme/alpha/pull/42" in comments[0].body

    @pytest.mark.asyncio
    async def test_broker_scope_enforcement_trusted_output_and_cli_auth_refresh(
        self,
    ) -> None:
        import json

        from gitseed_common.enums import BackendType
        from gitseed_core.models.backend import Backend
        from gitseed_core.schemas.runner import (
            RunnerCliAuthRefreshRequest,
            RunnerPrepareCompleteRequest,
        )
        from gitseed_core.security import create_run_token, decrypt_secrets, encrypt_secrets
        from gitseed_core.services.errors import Forbidden, Invalid
        from gitseed_core.services.runner_bridge import (
            complete_runner_prepare,
            get_runner_finalize_context,
            get_trusted_run_result,
            refresh_runner_cli_auth,
        )

        run, seed, project, agent = _make_entities(
            stage=SeedStage.EXECUTE,
            autonomy=SeedAutonomy.AUTONOMOUS,
        )
        run.runtime = AgentRuntime.CLAUDE_CODE
        models_backend = Backend(
            id="bnd_models_oauth",
            name="Anthropic OAuth",
            type=BackendType.INFERENCE,
            provider="anthropic_oauth",
            secrets_encrypted=encrypt_secrets({"cli_auth_bundle": "{}"}),
        )
        run.output = {"models_backend_id": models_backend.id}

        session = AsyncMock()
        session.add = MagicMock()

        async def _get_ext(model: Any, ident: str) -> Any:
            if model is Run and ident == run.id:
                return run
            if model is Seed and ident == seed.id:
                return seed
            if model is Project and ident == project.id:
                return project
            if model is Agent and ident == agent.id:
                return agent
            if model is Backend and ident == models_backend.id:
                return models_backend
            return None

        session.get = AsyncMock(side_effect=_get_ext)
        exec_res = MagicMock()
        exec_res.scalar_one_or_none.return_value = run
        exec_res.scalars.return_value.all.return_value = []
        session.execute = AsyncMock(return_value=exec_res)

        prep_tok = create_run_token(run.id, scope="prepare")
        agent_tok = create_run_token(run.id, scope="agent")
        fin_tok = create_run_token(run.id, scope="finalize")

        # 1. /prepare-complete requires prepare scope and is single-use
        with pytest.raises(Forbidden):
            await complete_runner_prepare(
                session,
                agent_tok,
                RunnerPrepareCompleteRequest(base_heads={"acme/alpha": "1111111"}),
            )
        prep_resp = await complete_runner_prepare(
            session,
            prep_tok,
            RunnerPrepareCompleteRequest(
                base_heads={"acme/alpha": "1111111"},
                remote_seed_heads={"acme/alpha": None},
                seed_branch="gitseed/seed-1-test",
            ),
        )
        assert prep_resp.base_heads == {"acme/alpha": "1111111"}
        with pytest.raises(Forbidden, match="already been recorded"):
            await complete_runner_prepare(
                session,
                prep_tok,
                RunnerPrepareCompleteRequest(base_heads={"acme/alpha": "2222222"}),
            )

        # 2. /finalize-context requires finalize scope and returns Core-stored base_heads
        with pytest.raises(Forbidden):
            await get_runner_finalize_context(session, agent_tok)
        with (
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                AsyncMock(return_value=_make_policy(SeedStage.EXECUTE)),
            ),
            patch(
                "gitseed_core.services.runner_bridge._resolve_runner_repositories",
                AsyncMock(return_value=[]),
            ),
        ):
            fin_ctx = await get_runner_finalize_context(session, fin_tok)
        assert fin_ctx.base_heads == {"acme/alpha": "1111111"}

        # 3. /cli-auth-refresh requires agent scope and enforces strict per-runtime allowlist
        with pytest.raises(Forbidden):
            await refresh_runner_cli_auth(
                session,
                prep_tok,
                RunnerCliAuthRefreshRequest(
                    cli_auth_bundle={
                        ".claude/.credentials.json": '{"accessToken":"tok"}'
                    }
                ),
            )
        with pytest.raises(Invalid):
            await refresh_runner_cli_auth(
                session,
                agent_tok,
                RunnerCliAuthRefreshRequest(
                    cli_auth_bundle={".bashrc": '{"evil": 1}'}
                ),
            )
        with patch(
            "gitseed_core.services.runner_bridge.resolve_models_backend_for_run",
            AsyncMock(return_value=(models_backend, "claude-sonnet-4")),
        ):
            refresh_resp = await refresh_runner_cli_auth(
                session,
                agent_tok,
                RunnerCliAuthRefreshRequest(
                    cli_auth_bundle={
                        ".claude/.credentials.json": '{"accessToken":"refreshed"}'
                    }
                ),
            )
        assert refresh_resp.updated_paths == [".claude/.credentials.json"]
        assert json.loads(
            decrypt_secrets(models_backend.secrets_encrypted)["cli_auth_bundle"]
        ) == {".claude/.credentials.json": '{"accessToken":"refreshed"}'}

        # 4. /complete rejects agent token, accepts finalize token once, strips forged commit_sha from agent stage_output, and populates run.output["trusted"]
        complete_payload = RunnerCompleteRequest(
            status="succeeded",
            exit_code=0,
            stage_output={
                "summary_comment": "Implemented feature",
                "files_modified": ["src/app.py"],
                "commit_sha": "forged_by_agent_999",
                "branch_name": "forged_branch",
                "pushed": True,
                "verification": {"passed": True, "commands": []},
            },
            trusted={
                "commit_sha": "trusted_sha_abc",
                "branch": "gitseed/seed-1-test",
                "pushed": True,
                "base_heads": {"acme/alpha": "1111111"},
                "blocked_paths_violations": [],
                "secret_scan_findings": [],
                "diff_stats": {"files": 1, "insertions": 10, "deletions": 2},
            },
        )
        with pytest.raises(Forbidden):
            await complete_runner_execution(session, agent_tok, complete_payload)

        with (
            patch(
                "gitseed_core.services.runner_bridge.get_effective_stage_policy",
                AsyncMock(return_value=_make_policy(SeedStage.EXECUTE)),
            ),
            patch(
                "gitseed_core.services.runner_bridge.finalize_run_litellm_key",
                AsyncMock(),
            ),
        ):
            await complete_runner_execution(session, fin_tok, complete_payload)

        assert run.status == RunStatus.SUCCEEDED
        assert run.output is not None
        assert run.output["commit_sha"] == "trusted_sha_abc"
        assert "forged_by_agent_999" not in str(run.output)
        trusted_res = get_trusted_run_result(run)
        assert trusted_res["commit_sha"] == "trusted_sha_abc"
        assert trusted_res["pushed"] is True
        assert trusted_res["base_heads"] == {"acme/alpha": "1111111"}
        assert trusted_res["verification"]["source"] == "agent_reported"

