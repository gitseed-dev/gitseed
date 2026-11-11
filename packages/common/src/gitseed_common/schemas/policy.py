from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import ProjectRole, SeedStage


class StagePolicyResponse(BaseModel):
    """Resolved policy for one stage (org default or project effective policy)."""

    model_config = ConfigDict(from_attributes=True)

    id: str | None = None
    project_id: str | None = None
    stage: SeedStage
    enabled: bool
    agent_id: str | None = None
    requires_approval: bool
    min_approvers: int
    min_approver_role: ProjectRole
    allow_auto_approve: bool
    allow_self_approval: bool
    allow_second_opinions: bool = False
    max_attempts: int
    timeout_minutes: int
    min_acceptance_criteria: int | None = None
    max_acceptance_criteria: int | None = None
    max_tasks: int | None = None
    overridden_fields: list[str] = Field(default_factory=list)
    # This level's own stage instructions (org row for org policies, project row for projects)
    instructions: str | None = None
    # For project policies: the org's instructions, which run before the project's
    inherited_instructions: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class UpdateOrgPolicyRequest(BaseModel):
    enabled: bool | None = None
    agent_id: str | None = None
    requires_approval: bool | None = None
    min_approvers: int | None = Field(default=None, ge=1, le=5)
    min_approver_role: ProjectRole | None = None
    allow_auto_approve: bool | None = None
    allow_self_approval: bool | None = None
    allow_second_opinions: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    min_acceptance_criteria: int | None = Field(default=None, ge=1, le=20)
    max_acceptance_criteria: int | None = Field(default=None, ge=1, le=20)
    max_tasks: int | None = Field(default=None, ge=1, le=20)
    instructions: str | None = Field(default=None, max_length=20000)


class UpdateProjectPolicyRequest(BaseModel):
    """Update project-level stage policy overrides. Explicit null clears an override."""

    enabled: bool | None = None
    agent_id: str | None = None
    requires_approval: bool | None = None
    min_approvers: int | None = Field(default=None, ge=1, le=5)
    min_approver_role: ProjectRole | None = None
    allow_auto_approve: bool | None = None
    allow_self_approval: bool | None = None
    allow_second_opinions: bool | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    timeout_minutes: int | None = Field(default=None, ge=1, le=1440)
    min_acceptance_criteria: int | None = Field(default=None, ge=1, le=20)
    max_acceptance_criteria: int | None = Field(default=None, ge=1, le=20)
    max_tasks: int | None = Field(default=None, ge=1, le=20)
    instructions: str | None = Field(default=None, max_length=20000)
