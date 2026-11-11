from datetime import datetime
from typing import Any

from gitseed_common.enums import AgentRuntime, RunStatus, SeedAutonomy, SeedStage
from gitseed_common.schemas.runner_output import (
    STAGE_OUTPUT_SCHEMAS,
    ExecuteStageOutput,
    IdeateStageOutput,
    MergeStageOutput,
    PlanStageOutput,
    PlanTaskItem,
    ReviewStageOutput,
    validate_stage_output_data,
)
from pydantic import BaseModel, Field


class RunnerSkillItem(BaseModel):
    id: str
    name: str
    slug: str
    description: str
    content: str


class RunnerTaskItem(BaseModel):
    id: str
    position: int
    title: str
    description: str
    status: str
    target_files: list[str] = Field(default_factory=list)


class RunnerCommentItem(BaseModel):
    id: str
    author_type: str
    author_name: str
    stage: SeedStage
    body: str
    created_at: datetime


class RunnerApprovalFeedbackItem(BaseModel):
    id: str
    stage: SeedStage
    decision: str
    comment: str | None
    created_at: datetime


class RunnerRepositoryItem(BaseModel):
    id: int
    backend_id: str
    provider: str
    full_name: str
    default_branch: str
    is_primary: bool
    clone_url: str
    clean_remote_url: str


class RunnerGatewayConfig(BaseModel):
    base_url: str
    anthropic_base_url: str
    api_key: str
    model: str
    provider: str
    auth_mode: str = "api_key"
    oauth_token: str | None = None
    cli_auth_bundle: dict[str, str] = Field(default_factory=dict)


class RunnerMCPServerItem(BaseModel):
    id: str
    name: str
    slug: str
    provider: str
    endpoint: str
    headers: dict[str, str] = Field(default_factory=dict)
    env: dict[str, str] = Field(default_factory=dict)


class RunnerAttachmentItem(BaseModel):
    id: str
    filename: str
    content_type: str
    size: int


class RunnerRelatedSeedItem(BaseModel):
    id: str
    number: int
    title: str
    status: str
    stage: str
    relation: str
    branch_name: str
    description: str | None = None
    specification: str | None = None
    plan: str | None = None
    summary_comment: str | None = None


class RunnerContextResponse(BaseModel):
    run_id: str
    stage: SeedStage
    attempt: int
    timeout_minutes: int
    runtime: AgentRuntime
    model: str
    task_id: str | None
    seed_id: str
    seed_number: int
    project_id: str
    project_slug: str
    project_name: str
    title: str
    description: str
    specification: str
    acceptance_criteria: list[str]
    original_title: str | None = None
    original_description: str | None = None
    original_acceptance_criteria: list[str] = Field(default_factory=list)
    plan: str | None
    autonomy: SeedAutonomy
    agent_id: str
    agent_handle: str
    agent_name: str
    agent_instructions: str
    # Stage policy instructions: org defaults followed by the project's (additive)
    stage_instructions: str | None = None
    min_acceptance_criteria: int | None = None
    max_acceptance_criteria: int | None = None
    max_tasks: int | None = None
    skills: list[RunnerSkillItem]
    tasks: list[RunnerTaskItem]
    comments: list[RunnerCommentItem]
    approval_feedback: list[RunnerApprovalFeedbackItem]
    repositories: list[RunnerRepositoryItem]
    mcp_servers: list[RunnerMCPServerItem] = Field(default_factory=list)
    # Files attached to the seed; the runner downloads them via /runner/attachments/{id}
    attachments: list["RunnerAttachmentItem"] = Field(default_factory=list)
    # Related / follow-up seeds; written by the runner to /workspace/.gitseed/related/seed-<n>.md
    related_seeds: list["RunnerRelatedSeedItem"] = Field(default_factory=list)
    sandbox_config: dict[str, Any] = Field(default_factory=dict)
    gateway: RunnerGatewayConfig
    output_schema: dict[str, Any]


class RunnerSteeringRequest(BaseModel):
    since: datetime | None = None
    log_tail: str | None = None
    context_tokens: int | None = None
    peak_context_tokens: int | None = None
    turn_count: int | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None


class RunnerSteeringResponse(BaseModel):
    should_abort: bool = False
    abort_reason: str | None = None
    steering_comments: list[RunnerCommentItem] = Field(default_factory=list)


class RunnerGitPushTokenRequest(BaseModel):
    repository_full_name: str


class RunnerGitPushTokenResponse(BaseModel):
    repository_full_name: str
    push_url: str
    clean_remote_url: str
    username: str = "x-access-token"
    push_token: str | None = None


class RunnerPrepareCompleteRequest(BaseModel):
    base_heads: dict[str, str] = Field(default_factory=dict)
    remote_seed_heads: dict[str, str | None] = Field(default_factory=dict)
    seed_branch: str | None = None
    error: str | None = None


class RunnerPrepareCompleteResponse(BaseModel):
    ok: bool = True
    run_id: str
    base_heads: dict[str, str] = Field(default_factory=dict)


class RunnerFinalizeContextResponse(BaseModel):
    run_id: str
    seed_id: str
    seed_number: int
    project_id: str
    project_slug: str
    stage: SeedStage
    runtime: AgentRuntime
    model: str
    seed_branch: str
    base_heads: dict[str, str] = Field(default_factory=dict)
    remote_seed_heads: dict[str, str] = Field(default_factory=dict)
    blocked_paths: list[str] = Field(default_factory=list)
    blocked_paths_action: str = "fail"
    max_file_bytes: int = 5 * 1024 * 1024
    max_diff_bytes: int = 20 * 1024 * 1024
    issued_secrets: list[str] = Field(default_factory=list)
    repositories: list[RunnerRepositoryItem] = Field(default_factory=list)
    min_acceptance_criteria: int | None = None
    max_acceptance_criteria: int | None = None
    max_tasks: int | None = None
    output_schema: dict[str, Any] = Field(default_factory=dict)


class RunnerCliAuthRefreshRequest(BaseModel):
    cli_auth_bundle: dict[str, str] = Field(default_factory=dict)


class RunnerCliAuthRefreshResponse(BaseModel):
    ok: bool = True
    updated_paths: list[str] = Field(default_factory=list)


class RunnerCompleteRequest(BaseModel):
    status: RunStatus
    exit_code: int = 0
    error: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    peak_context_tokens: int = 0
    turn_count: int = 0
    stage_output: dict[str, Any] = Field(default_factory=dict)
    log_tail: str | None = None
    updated_cli_auth_bundle: dict[str, str] | None = None
    trusted: dict[str, Any] | None = None


__all__ = [
    "STAGE_OUTPUT_SCHEMAS",
    "ExecuteStageOutput",
    "IdeateStageOutput",
    "MergeStageOutput",
    "PlanStageOutput",
    "PlanTaskItem",
    "ReviewStageOutput",
    "RunnerApprovalFeedbackItem",
    "RunnerCliAuthRefreshRequest",
    "RunnerCliAuthRefreshResponse",
    "RunnerCommentItem",
    "RunnerCompleteRequest",
    "RunnerContextResponse",
    "RunnerFinalizeContextResponse",
    "RunnerGatewayConfig",
    "RunnerGitPushTokenRequest",
    "RunnerGitPushTokenResponse",
    "RunnerMCPServerItem",
    "RunnerPrepareCompleteRequest",
    "RunnerPrepareCompleteResponse",
    "RunnerRepositoryItem",
    "RunnerSkillItem",
    "RunnerSteeringResponse",
    "RunnerTaskItem",
    "validate_stage_output_data",
]
