from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import (
    ActorType,
    ApprovalDecision,
    ProjectRole,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)

BudgetEnforcement = Literal["enforced", "estimated"]


class BudgetStatusResponse(BaseModel):
    spent: Decimal = Decimal("0")
    budget_cap: Decimal | None = None
    remaining_budget: Decimal | None = None
    spend_checkpoint: Decimal | None = None
    checkpoint_approved_through: Decimal = Decimal("0")
    monthly_budget: Decimal | None = None
    blocked_reason: str | None = None
    enforcement: BudgetEnforcement = "enforced"
    enforcement_detail: str | None = None


class SeedResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    number: int
    title: str
    description: str = ""
    specification: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    plan: str | None = None
    tags: list[str] = Field(default_factory=list)
    stage: SeedStage
    status: SeedStatus
    autonomy: SeedAutonomy
    stage_entered_at: datetime | None = None
    blocked_reason: str | None = None
    budget_cap: Decimal | None = None
    spent: Decimal = Decimal("0")
    checkpoint_approved_through: Decimal = Decimal("0")
    enforcement: BudgetEnforcement = "enforced"
    enforcement_detail: str | None = None
    merged_at: datetime | None = None
    closed_at: datetime | None = None
    closed_by: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class CreateSeedRequest(BaseModel):
    project_id: str
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    specification: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    plan: str | None = None
    tags: list[str] = Field(default_factory=list)
    stage: SeedStage | None = None
    fast_track: bool = False
    autonomy: SeedAutonomy = SeedAutonomy.SUPERVISED
    budget_cap: Decimal | None = Field(default=None, gt=0)
    staged: bool | None = None
    ideate_agent_ids: list[str] = Field(default_factory=list)


class UpdateSeedRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    specification: str | None = None
    acceptance_criteria: list[str] = Field(default_factory=list)
    plan: str | None = None
    tags: list[str] | None = None
    autonomy: SeedAutonomy | None = None
    budget_cap: Decimal | None = Field(default=None, gt=0)


class AdvanceSeedRequest(BaseModel):
    stage: SeedStage | None = None
    fast_track: bool = False


class SecondOpinionRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64)
    compute_backend_id: str | None = None


class SelectSeedRunRequest(BaseModel):
    run_id: str = Field(min_length=1, max_length=64)


class ApproveCheckpointRequest(BaseModel):
    approved_through: Decimal = Field(gt=0)


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seed_id: str
    stage: SeedStage
    snapshot: dict[str, Any] = Field(default_factory=dict)
    decision: ApprovalDecision
    comment: str | None = None
    automatic: bool = False
    decided_by: str | None = None
    approver_role: ProjectRole | None = None
    created_at: datetime | None = None


class CreateApprovalRequest(BaseModel):
    decision: ApprovalDecision
    comment: str | None = None


class SeedTaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seed_id: str
    position: int
    title: str
    description: str = ""
    status: TaskStatus
    started_at: datetime | None = None
    completed_at: datetime | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateSeedTaskRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    position: int | None = Field(default=None, ge=0)


class UpdateSeedTaskRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = None
    position: int | None = Field(default=None, ge=0)
    status: TaskStatus | None = None


class SeedCommentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seed_id: str
    author_type: ActorType
    author_id: str
    stage: SeedStage
    body: str
    edited_at: datetime | None = None
    deleted_at: datetime | None = None
    deleted_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class CreateSeedCommentRequest(BaseModel):
    body: str = Field(min_length=1)


class UpdateSeedCommentRequest(BaseModel):
    body: str = Field(min_length=1)


class SeedChangeCommitResponse(BaseModel):
    sha: str
    short_sha: str
    message: str
    author_name: str | None = None
    committed_at: str | None = None
    web_url: str | None = None


class SeedChangeFileResponse(BaseModel):
    path: str
    previous_path: str | None = None
    status: str = "modified"
    additions: int = 0
    deletions: int = 0
    changes: int = 0
    patch: str | None = None
    truncated: bool = False
    web_url: str | None = None


class PullRequestCheckItem(BaseModel):
    id: str
    name: str
    stage_or_workflow: str | None = None
    status: str = "queued"
    conclusion: str | None = None
    started_at: str | None = None
    completed_at: str | None = None
    duration_seconds: int | None = None
    summary: str | None = None
    details_url: str | None = None


class PullRequestCommentItem(BaseModel):
    id: str
    author: str
    author_avatar_url: str | None = None
    body: str
    kind: str = "comment"
    path: str | None = None
    line: int | None = None
    diff_hunk: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    web_url: str | None = None


class CreatePullRequestCommentRequest(BaseModel):
    body: str = Field(min_length=1, max_length=16000)
    repo_id: int | None = None
    mirror_to_seed: bool = True


class PullRequestStatusResponse(BaseModel):
    pull_request_url: str | None = None
    number: int | None = None
    repository_full_name: str | None = None
    provider: str | None = None
    state: str | None = None
    merged: bool = False
    title: str | None = None
    body: str | None = None
    draft: bool = False
    author: str | None = None
    base_ref: str | None = None
    head_ref: str | None = None
    head_sha: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    merged_at: str | None = None
    merge_commit_sha: str | None = None
    merged_by: str | None = None
    has_conflicts: bool = False
    approved_by: list[str] = Field(default_factory=list)
    changes_requested_by: list[str] = Field(default_factory=list)
    requested_reviewers: list[str] = Field(default_factory=list)
    ci_status: str = "none"
    ci_summary: str | None = None
    failing_checks: list[str] = Field(default_factory=list)
    checks: list[PullRequestCheckItem] = Field(default_factory=list)
    comments: list[PullRequestCommentItem] = Field(default_factory=list)
    review_status: str = "none"
    mergeable_state: str | None = None
    ready_to_merge: bool = True
    block_reason: str | None = None


class SeedChangesResponse(BaseModel):
    seed_id: str
    seed_number: int
    repository_id: int | None = None
    repository_full_name: str | None = None
    provider: str | None = None
    base_ref: str = "main"
    head_ref: str = ""
    comparison_mode: str = "none"
    branch_exists: bool = False
    merge_commit_sha: str | None = None
    head_commit_sha: str | None = None
    web_diff_url: str | None = None
    pull_request_url: str | None = None
    pull_request_status: PullRequestStatusResponse | None = None
    blocked_paths_reverted: list[str] = Field(default_factory=list)
    total_commits: int = 0
    total_files: int = 0
    total_additions: int = 0
    total_deletions: int = 0
    commits: list[SeedChangeCommitResponse] = Field(default_factory=list)
    files: list[SeedChangeFileResponse] = Field(default_factory=list)
