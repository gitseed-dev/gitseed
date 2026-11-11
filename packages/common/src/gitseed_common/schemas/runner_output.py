"""Canonical Pydantic schemas for /workspace/.gitseed/output.json across runner and control plane."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


class IdeateStageOutput(BaseModel):
    """Schema for /workspace/.gitseed/output.json during the Ideate stage."""

    title: str | None = Field(default=None, max_length=200)
    description: str | None = None
    specification: str = Field(
        ...,
        min_length=10,
        description="Comprehensive technical specification formatted in GitHub-Flavored Markdown.",
    )
    acceptance_criteria: list[str] = Field(
        ...,
        min_length=1,
        description="Testable acceptance criteria items (Markdown formatting supported).",
    )
    summary_comment: str = Field(
        ...,
        min_length=5,
        description=(
            "Timeline comment summarizing your ideation updates, formatted in "
            "GitHub-Flavored Markdown (use bullet points, **bold**, and `code` spans)."
        ),
    )
    fast_tracked_plan: dict[str, Any] | None = Field(
        default=None,
        description="Optional in-container fast-tracked PlanStageOutput when Ideate auto-advances.",
    )
    policy_limit_warning: dict[str, Any] | None = Field(
        default=None,
        description="Optional soft warning if output count exceeded stage policy bounds after repair.",
    )

    @field_validator("acceptance_criteria")
    @classmethod
    def _clean_criteria(cls, v: list[str]) -> list[str]:
        cleaned = [item.strip() for item in v if item and item.strip()]
        if not cleaned:
            raise ValueError("acceptance_criteria must contain at least one non-empty item")
        return cleaned


class PlanTaskItem(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    description: str = Field(
        default="",
        description="Task implementation details formatted in Markdown.",
    )
    target_files: list[str] = Field(
        default_factory=list,
        description="Relative file paths in /workspace created or modified by this task.",
    )


class SuggestedFollowUpSeed(BaseModel):
    title: str = Field(..., min_length=2, max_length=200)
    description: str = Field(
        default="",
        description="Executive summary and initial scope for the split follow-up seed.",
    )
    rationale: str | None = Field(
        default=None,
        description="Why this work should be split into a separate follow-up seed.",
    )


class PlanStageOutput(BaseModel):
    """Schema for /workspace/.gitseed/output.json during the Plan stage."""

    plan: str = Field(
        ...,
        min_length=10,
        description="Detailed implementation plan formatted in GitHub-Flavored Markdown.",
    )
    tasks: list[PlanTaskItem] = Field(..., min_length=1)
    target_repositories: list[str] = Field(default_factory=list)
    suggested_follow_ups: list[SuggestedFollowUpSeed] = Field(
        default_factory=list,
        description=(
            "Optional follow-up seeds to split out when the work exceeds the stage policy max_tasks limit."
        ),
    )
    policy_limit_warning: dict[str, Any] | None = Field(
        default=None,
        description="Optional soft warning if task count exceeded stage policy max_tasks after repair.",
    )
    summary_comment: str = Field(
        ...,
        min_length=5,
        description=(
            "Timeline comment summarizing the plan and task breakdown, "
            "formatted in GitHub-Flavored Markdown."
        ),
    )


class ExecuteStageOutput(BaseModel):
    """Schema for /workspace/.gitseed/output.json during the Execute stage."""

    summary_comment: str = Field(
        ...,
        min_length=5,
        description=(
            "Timeline comment summarizing files changed and tests verified, "
            "formatted in GitHub-Flavored Markdown (use bullet points and `code` spans)."
        ),
    )
    branch_name: str | None = None
    commit_sha: str | None = None
    pull_request_url: str | None = None
    completed_task_ids: list[str] = Field(default_factory=list)


class ReviewStageOutput(BaseModel):
    """Schema for /workspace/.gitseed/output.json during the Review stage."""

    approved: bool = Field(
        ...,
        description="Explicit review verdict (true if approved, false if changes are requested).",
    )
    findings: list[str] = Field(
        default_factory=list,
        description="Specific issues or review notes formatted in Markdown with `file` references.",
    )
    summary_comment: str = Field(
        ...,
        min_length=5,
        description=(
            "Timeline review summary formatted in GitHub-Flavored Markdown "
            "(use headings, bullet points, **bold**, and `code` spans)."
        ),
    )


class MergeStageOutput(BaseModel):
    """Schema for /workspace/.gitseed/output.json during the Merge stage."""

    merged: bool = Field(
        ...,
        description="Explicit merge readiness/completion verdict (true if ready/merged, false otherwise).",
    )
    merge_commit_sha: str | None = None
    summary_comment: str = Field(
        ...,
        min_length=5,
        description="Timeline merge readiness summary formatted in GitHub-Flavored Markdown.",
    )


STAGE_OUTPUT_SCHEMAS: dict[str, type[BaseModel]] = {
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
    """Return a structured policy_limit_warning dict if stage output violates count bounds, else None."""
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
    """Validate raw JSON dictionary against the stage's Pydantic model and return dumped dict."""
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
