from __future__ import annotations

import gitseed_common.schemas as common_schemas
import pytest
from gitseed_common.enums import (
    BACKEND_PROVIDER_SPECS,
    BackendType,
    ProviderSpec,
    SeedStage,
)
from gitseed_common.schemas import (
    STAGE_OUTPUT_SCHEMAS,
    CliAuthInputRequest,
    CliAuthSessionResponse,
    ExecuteStageOutput,
    IdeateStageOutput,
    MergeStageOutput,
    PlanStageOutput,
    PlanTaskItem,
    ReviewStageOutput,
    validate_stage_output_data,
)
from pydantic import ValidationError


class TestStageOutputValidation:
    def test_validate_ideate_stage_output_with_optional_fields(self) -> None:
        raw = {
            "title": "Add OAuth PKCE support",
            "description": "Support PKCE flow for CLI authentication.",
            "specification": "## Overview\nDetailed PKCE specification with state verification.",
            "acceptance_criteria": [
                "  PKCE code_verifier is generated with 64 bytes  ",
                "",
                "   ",
                "Token exchange verifies S256 challenge",
            ],
            "summary_comment": "Refined specification and acceptance criteria.",
        }
        result = validate_stage_output_data(SeedStage.IDEATE, raw)
        assert result["title"] == "Add OAuth PKCE support"
        assert result["description"] == "Support PKCE flow for CLI authentication."
        assert result["acceptance_criteria"] == [
            "PKCE code_verifier is generated with 64 bytes",
            "Token exchange verifies S256 challenge",
        ]

    def test_ideate_stage_output_defaults_and_validation_errors(self) -> None:
        out = IdeateStageOutput(
            specification="## Spec\nAt least ten characters here.",
            acceptance_criteria=["Criterion 1"],
            summary_comment="Valid summary",
        )
        assert out.title is None
        assert out.description is None
        assert out.acceptance_criteria == ["Criterion 1"]

        # Reject whitespace-only acceptance_criteria
        with pytest.raises(ValidationError):
            IdeateStageOutput(
                specification="## Spec\nAt least ten characters here.",
                acceptance_criteria=["   ", ""],
                summary_comment="Valid summary",
            )

        # Reject empty list acceptance_criteria
        with pytest.raises(ValidationError):
            IdeateStageOutput(
                specification="## Spec\nAt least ten characters here.",
                acceptance_criteria=[],
                summary_comment="Valid summary",
            )

        # Reject short specification (< 10 chars)
        with pytest.raises(ValidationError):
            IdeateStageOutput(
                specification="Too short",
                acceptance_criteria=["Criterion 1"],
                summary_comment="Valid summary",
            )

    def test_validate_plan_stage_output(self) -> None:
        raw = {
            "plan": "## Architecture Plan\n1. Create service module\n2. Add tests",
            "tasks": [
                {
                    "title": "Implement core service",
                    "description": "- Write service functions\n- Add unit tests",
                }
            ],
            "target_repositories": ["org/repo-a"],
            "summary_comment": "Created implementation plan with 1 milestone task.",
        }
        result = validate_stage_output_data("plan", raw)
        assert result["plan"].startswith("## Architecture Plan")
        assert len(result["tasks"]) == 1
        assert result["tasks"][0]["title"] == "Implement core service"
        assert result["target_repositories"] == ["org/repo-a"]

    def test_validate_execute_stage_output(self) -> None:
        raw = {
            "summary_comment": "Implemented feature and ran pytest.",
            "branch_name": "gitseed/seed-7",
            "commit_sha": "abc1234",
            "pull_request_url": "https://github.com/org/repo/pull/7",
        }
        result = validate_stage_output_data("EXECUTE", raw)
        assert result["branch_name"] == "gitseed/seed-7"
        assert result["commit_sha"] == "abc1234"
        assert result["pull_request_url"] == "https://github.com/org/repo/pull/7"

    def test_validate_review_stage_output(self) -> None:
        raw = {
            "approved": False,
            "findings": ["Missing null check in `handler.py`"],
            "summary_comment": "Found 1 issue during review.",
        }
        result = validate_stage_output_data("review", raw)
        assert result["approved"] is False
        assert result["findings"] == ["Missing null check in `handler.py`"]

        # Omitting explicit `approved` verdict must fail validation
        with pytest.raises(ValidationError):
            validate_stage_output_data(
                "review",
                {"findings": [], "summary_comment": "Missing approved field."},
            )

    def test_validate_merge_stage_output(self) -> None:
        raw = {
            "merged": True,
            "merge_commit_sha": "deadbeef1234",
            "summary_comment": "Merged feature branch cleanly.",
        }
        result = validate_stage_output_data("merge", raw)
        assert result["merged"] is True
        assert result["merge_commit_sha"] == "deadbeef1234"

        # Omitting explicit `merged` verdict must fail validation
        with pytest.raises(ValidationError):
            validate_stage_output_data(
                "merge",
                {"summary_comment": "Missing merged field."},
            )

    def test_validate_unknown_stage_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown stage"):
            validate_stage_output_data("deploy", {"summary_comment": "Nope"})

    def test_validate_stage_policy_bounds_and_soft_fallback(self) -> None:
        ideate_raw = {
            "specification": "## Spec\nDetailed specification here.",
            "acceptance_criteria": [f"Criterion {i}" for i in range(1, 9)],
            "summary_comment": "Refined spec.",
        }
        with pytest.raises(ValueError, match="acceptance_criteria has 8 items"):
            validate_stage_output_data(
                "ideate",
                ideate_raw,
                min_acceptance_criteria=1,
                max_acceptance_criteria=6,
                enforce_bounds=True,
            )

        soft_ideate = validate_stage_output_data(
            "ideate",
            ideate_raw,
            min_acceptance_criteria=1,
            max_acceptance_criteria=6,
            enforce_bounds=False,
        )
        assert soft_ideate["policy_limit_warning"]["message"] == "8 criteria, policy maximum is 6"

        plan_raw = {
            "plan": "## Architecture Plan\nDetailed implementation steps.",
            "tasks": [{"title": f"Task {i}"} for i in range(1, 8)],
            "suggested_follow_ups": [
                {
                    "title": "Split database migration",
                    "description": "Extract schema changes into a dedicated seed.",
                    "reason": "Too large for a single 5-task implementation run.",
                }
            ],
            "summary_comment": "Created plan and suggested follow-up split.",
        }
        with pytest.raises(ValueError, match="tasks has 7 items"):
            validate_stage_output_data(
                "plan",
                plan_raw,
                max_tasks=5,
                enforce_bounds=True,
            )

        soft_plan = validate_stage_output_data(
            "plan",
            plan_raw,
            max_tasks=5,
            enforce_bounds=False,
        )
        assert soft_plan["policy_limit_warning"]["message"] == "7 tasks, policy maximum is 5"
        assert len(soft_plan["suggested_follow_ups"]) == 1
        assert soft_plan["suggested_follow_ups"][0]["title"] == "Split database migration"


class TestBackendProviderSpecs:
    def test_all_backend_types_covered(self) -> None:
        assert BackendType("models") is BackendType.INFERENCE
        for btype in BackendType:
            assert btype in BACKEND_PROVIDER_SPECS
            assert len(BACKEND_PROVIDER_SPECS[btype]) > 0

    def test_git_provider_specs(self) -> None:
        git_specs = BACKEND_PROVIDER_SPECS[BackendType.GIT]
        assert git_specs["github"].required_secrets == frozenset({"token"})
        assert git_specs["github"].optional_secrets == frozenset({"webhook_secret"})
        assert git_specs["github"].allowed_secrets == frozenset({"token", "webhook_secret"})

        assert git_specs["github-app"].required_secrets == frozenset({"private_key"})
        assert git_specs["github-app"].optional_secrets == frozenset({"webhook_secret", "token"})

        assert git_specs["git"].required_secrets == frozenset()
        assert git_specs["git"].optional_secrets == frozenset({"token", "ssh_key"})

    def test_inference_provider_specs(self) -> None:
        model_specs = BACKEND_PROVIDER_SPECS[BackendType.INFERENCE]
        for cloud_provider in ("anthropic", "openai", "gemini", "xai"):
            spec = model_specs[cloud_provider]
            assert spec.required_secrets == frozenset({"api_key"})
            assert "oauth_token" in spec.optional_secrets
            assert "cli_auth_bundle" in spec.optional_secrets
            assert spec.allowed_secrets == spec.required_secrets | spec.optional_secrets

        agy_spec = model_specs["antigravity"]
        assert agy_spec.required_secrets == frozenset()
        assert agy_spec.optional_secrets == frozenset(
            {"api_key", "oauth_token", "cli_auth_bundle"}
        )

        for local_provider in ("ollama", "lmstudio", "vllm", "custom"):
            spec = model_specs[local_provider]
            assert spec.required_secrets == frozenset()
            assert spec.optional_secrets == frozenset({"api_key"})

    def test_compute_and_tools_provider_specs(self) -> None:
        compute_specs = BACKEND_PROVIDER_SPECS[BackendType.COMPUTE]
        assert compute_specs["docker"].allowed_secrets == frozenset(
            {"tls_cert", "tls_key", "tls_ca"}
        )
        assert compute_specs["kubernetes"].allowed_secrets == frozenset(
            {"token", "ca_cert"}
        )

        tools_specs = BACKEND_PROVIDER_SPECS[BackendType.TOOLS]
        assert tools_specs["mcp-sse"].allowed_secrets == frozenset(
            {"api_key", "bearer_token"}
        )
        assert tools_specs["mcp-stdio"].allowed_secrets == frozenset({"env_secret"})


class TestSchemaExports:
    def test_all_exported_symbols_exist_and_import_cleanly(self) -> None:
        assert hasattr(common_schemas, "__all__")
        for symbol in common_schemas.__all__:
            assert hasattr(common_schemas, symbol), f"Missing export: {symbol}"

        req = CliAuthInputRequest(code="ABCD-1234")
        assert req.code == "ABCD-1234"

        resp = CliAuthSessionResponse(
            status="awaiting_user",
            auth_url="https://claude.ai/oauth/authorize",
            user_code="ABCD-1234",
            log_output="Waiting for login...",
            authenticated=False,
        )
        assert resp.status == "awaiting_user"
        assert resp.authenticated is False
        assert set(STAGE_OUTPUT_SCHEMAS.keys()) == {
            "ideate",
            "plan",
            "execute",
            "review",
            "merge",
        }
        assert PlanTaskItem(title="Task 1").title == "Task 1"
        assert isinstance(
            ProviderSpec(required_secrets=frozenset({"a"})), ProviderSpec
        )
        assert ExecuteStageOutput is not None
        assert ReviewStageOutput is not None
        assert MergeStageOutput is not None
        assert PlanStageOutput is not None
