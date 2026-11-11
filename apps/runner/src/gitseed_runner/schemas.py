"""Pydantic schemas for /workspace/.gitseed/output.json validation inside the runner container."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

try:
    from gitseed_common.schemas.runner_output import (
        STAGE_OUTPUT_SCHEMAS,
        ExecuteStageOutput,
        IdeateStageOutput,
        MergeStageOutput,
        PlanStageOutput,
        PlanTaskItem,
        ReviewStageOutput,
        SuggestedFollowUpSeed,
        check_stage_policy_bounds,
        validate_stage_output_data,
    )
except ModuleNotFoundError:  # Standalone apps/runner container build context

    class IdeateStageOutput(BaseModel):  # type: ignore[no-redef]
        title: str | None = Field(default=None, max_length=200)
        description: str | None = None
        specification: str = Field(..., min_length=10)
        acceptance_criteria: list[str] = Field(..., min_length=1)
        summary_comment: str = Field(..., min_length=5)
        fast_tracked_plan: dict[str, Any] | None = None
        policy_limit_warning: dict[str, Any] | None = None

        @field_validator("acceptance_criteria")
        @classmethod
        def _clean_criteria(cls, v: list[str]) -> list[str]:
            cleaned = [item.strip() for item in v if item and item.strip()]
            if not cleaned:
                raise ValueError("acceptance_criteria must contain at least one non-empty item")
            return cleaned

    class PlanTaskItem(BaseModel):  # type: ignore[no-redef]
        title: str = Field(..., min_length=2, max_length=200)
        description: str = Field(default="")
        target_files: list[str] = Field(default_factory=list)

    class SuggestedFollowUpSeed(BaseModel):  # type: ignore[no-redef]
        title: str = Field(..., min_length=2, max_length=200)
        description: str = Field(default="")
        rationale: str | None = None

    class PlanStageOutput(BaseModel):  # type: ignore[no-redef]
        plan: str = Field(..., min_length=10)
        tasks: list[PlanTaskItem] = Field(..., min_length=1)
        target_repositories: list[str] = Field(default_factory=list)
        suggested_follow_ups: list[SuggestedFollowUpSeed] = Field(default_factory=list)
        policy_limit_warning: dict[str, Any] | None = None
        summary_comment: str = Field(..., min_length=5)

    class ExecuteStageOutput(BaseModel):  # type: ignore[no-redef]
        summary_comment: str = Field(..., min_length=5)
        branch_name: str | None = None
        commit_sha: str | None = None
        pull_request_url: str | None = None
        completed_task_ids: list[str] = Field(default_factory=list)

    class ReviewStageOutput(BaseModel):  # type: ignore[no-redef]
        approved: bool = Field(...)
        findings: list[str] = Field(default_factory=list)
        summary_comment: str = Field(..., min_length=5)

    class MergeStageOutput(BaseModel):  # type: ignore[no-redef]
        merged: bool = Field(...)
        merge_commit_sha: str | None = None
        summary_comment: str = Field(..., min_length=5)

    STAGE_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {  # type: ignore[no-redef]
        "ideate": IdeateStageOutput,
        "plan": PlanStageOutput,
        "execute": ExecuteStageOutput,
        "review": ReviewStageOutput,
        "merge": MergeStageOutput,
    }

    def check_stage_policy_bounds(
        stage: str,
        validated_data: dict[str, Any],
        *,
        min_acceptance_criteria: int | None = None,
        max_acceptance_criteria: int | None = None,
        max_tasks: int | None = None,
    ) -> dict[str, Any] | None:
        st = stage.strip().lower()
        if st == "ideate":
            items = validated_data.get("acceptance_criteria") or []
            count = len(items)
            if min_acceptance_criteria is not None and count < min_acceptance_criteria:
                return {
                    "field": "acceptance_criteria",
                    "count": count,
                    "min": min_acceptance_criteria,
                    "max": max_acceptance_criteria,
                    "message": (
                        f"{count} {'criterion' if count == 1 else 'criteria'}, "
                        f"policy minimum is {min_acceptance_criteria}"
                    ),
                }
            if max_acceptance_criteria is not None and count > max_acceptance_criteria:
                return {
                    "field": "acceptance_criteria",
                    "count": count,
                    "min": min_acceptance_criteria,
                    "max": max_acceptance_criteria,
                    "message": f"{count} criteria, policy maximum is {max_acceptance_criteria}",
                }
        elif st == "plan":
            tasks = validated_data.get("tasks") or []
            count = len(tasks)
            if max_tasks is not None and count > max_tasks:
                return {
                    "field": "tasks",
                    "count": count,
                    "max": max_tasks,
                    "message": f"{count} tasks, policy maximum is {max_tasks}",
                }
        return None

    def validate_stage_output_data(
        stage: str,
        raw_data: dict[str, Any],
        *,
        min_acceptance_criteria: int | None = None,
        max_acceptance_criteria: int | None = None,
        max_tasks: int | None = None,
        enforce_bounds: bool = False,
    ) -> dict[str, Any]:
        st = stage.strip().lower()
        schema_cls = STAGE_OUTPUT_SCHEMAS.get(st)
        if schema_cls is None:
            raise ValueError(f"Unknown stage '{stage}'")
        dumped = schema_cls.model_validate(raw_data).model_dump()
        warning = check_stage_policy_bounds(
            st,
            dumped,
            min_acceptance_criteria=min_acceptance_criteria,
            max_acceptance_criteria=max_acceptance_criteria,
            max_tasks=max_tasks,
        )
        if warning is not None:
            if enforce_bounds:
                if warning["field"] == "acceptance_criteria":
                    bounds_desc = (
                        f"between {min_acceptance_criteria or 1} and {max_acceptance_criteria}"
                        if max_acceptance_criteria is not None
                        else f"at least {min_acceptance_criteria or 1}"
                    )
                    raise ValueError(
                        f"acceptance_criteria has {warning['count']} items, "
                        f"but stage policy requires {bounds_desc}. "
                        "Consolidate or refine your acceptance criteria so each describes a distinct, "
                        "checkable outcome without repeating the specification."
                    )
                raise ValueError(
                    f"tasks has {warning['count']} items, but stage policy maximum is {max_tasks}. "
                    f"Group related file changes into at most {max_tasks} cohesive milestone tasks, "
                    "or if the seed is genuinely too large, split the overflow into `suggested_follow_ups`."
                )
            dumped["policy_limit_warning"] = warning
        return dumped


__all__ = [
    "STAGE_OUTPUT_SCHEMAS",
    "ExecuteStageOutput",
    "IdeateStageOutput",
    "MergeStageOutput",
    "PlanStageOutput",
    "PlanTaskItem",
    "ReviewStageOutput",
    "SuggestedFollowUpSeed",
    "check_stage_policy_bounds",
    "validate_stage_output_data",
]
