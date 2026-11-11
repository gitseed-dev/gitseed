import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from gitseed_common.enums import ProjectRole, ProjectVisibility

_FORBIDDEN_REF_CHARS_RE = re.compile(r"[\x00-\x1f\x7f ~^:?*\[\\]")


def validate_refname(ref: str) -> str:
    """Validate a Git ref/branch name against git-check-ref-format rules and option injection."""
    if not isinstance(ref, str):
        raise ValueError("Git ref name must be a string")
    cleaned = ref.strip()
    if not cleaned:
        raise ValueError("Git ref name cannot be empty")
    if len(cleaned) > 255:
        raise ValueError("Git ref name is too long")
    if cleaned.startswith(("-", "/")):
        raise ValueError("Git ref name must not start with '-' or '/'")
    if cleaned.endswith(("/", ".", ".lock")):
        raise ValueError("Git ref name must not end with '/', '.', or '.lock'")
    if ".." in cleaned or "@{" in cleaned or "//" in cleaned or cleaned == "@":
        raise ValueError("Git ref name contains forbidden sequence")
    if _FORBIDDEN_REF_CHARS_RE.search(cleaned) or any(ch.isspace() for ch in cleaned):
        raise ValueError("Git ref name contains invalid characters")
    for part in cleaned.split("/"):
        if not part or part.startswith(".") or part.endswith((".", ".lock")):
            raise ValueError("Git ref name contains an invalid path component")
    return cleaned


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    slug: str
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility: ProjectVisibility
    seed_counter: int = 0
    monthly_budget: Decimal | None = None
    seed_budget_cap: Decimal | None = None
    spend_checkpoint: Decimal | None = None
    enforcement: Literal["enforced", "estimated"] = "enforced"
    enforcement_detail: str | None = None
    max_concurrent_runs: int | None = None
    backends_restricted: bool = False
    sandbox_config: dict[str, Any] = Field(default_factory=dict)
    allowed_backend_ids: list[str] = Field(default_factory=list)
    my_role: ProjectRole | None = None
    archived_at: datetime | None = None
    archived_by: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class CreateProjectRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=2, max_length=63)
    description: str = ""
    tags: list[str] = Field(default_factory=list)
    visibility: ProjectVisibility = ProjectVisibility.PRIVATE
    monthly_budget: Decimal | None = Field(default=None, ge=0)
    seed_budget_cap: Decimal | None = Field(default=None, gt=0)
    spend_checkpoint: Decimal | None = Field(default=None, gt=0)
    max_concurrent_runs: int | None = Field(default=None, ge=0)
    backends_restricted: bool = False


class UpdateProjectRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    slug: str | None = Field(default=None, min_length=2, max_length=63)
    description: str | None = None
    tags: list[str] | None = None
    visibility: ProjectVisibility | None = None
    monthly_budget: Decimal | None = Field(default=None, ge=0)
    seed_budget_cap: Decimal | None = Field(default=None, gt=0)
    spend_checkpoint: Decimal | None = Field(default=None, gt=0)
    max_concurrent_runs: int | None = Field(default=None, ge=0)
    backends_restricted: bool | None = None
    sandbox_config: dict[str, Any] | None = None


class ProjectMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    project_id: str
    user_id: str
    email: str = ""
    name: str = ""
    preferred_name: str | None = None
    role: ProjectRole
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class AddProjectMemberRequest(BaseModel):
    user_id: str
    role: ProjectRole = ProjectRole.DEVELOPER


class UpdateProjectMemberRequest(BaseModel):
    role: ProjectRole


class ProjectRepositoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int | None = None
    project_id: str
    backend_id: str
    full_name: str
    default_branch: str
    is_primary: bool
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class AddProjectRepositoryRequest(BaseModel):
    backend_id: str
    full_name: str = Field(min_length=3, max_length=200)
    default_branch: str = Field(default="main", min_length=1, max_length=100)
    is_primary: bool | None = None

    @field_validator("default_branch")
    @classmethod
    def _check_default_branch(cls, v: str) -> str:
        return validate_refname(v)


class InitLocalProjectRepositoryRequest(BaseModel):
    workspace_path: str | None = Field(default=None, max_length=1024)
    default_branch: str = Field(default="main", min_length=1, max_length=100)

    @field_validator("default_branch")
    @classmethod
    def _check_default_branch(cls, v: str) -> str:
        return validate_refname(v)


class InitLocalProjectRepositoryResponse(BaseModel):
    repository: ProjectRepositoryResponse
    backend_id: str
    bare_repo_path: str
    workspace_path: str | None = None
    http_clone_url: str
    initialized_git: bool = True


class UpdateProjectRepositoryRequest(BaseModel):
    default_branch: str | None = Field(default=None, min_length=1, max_length=100)
    is_primary: bool | None = None

    @field_validator("default_branch")
    @classmethod
    def _check_default_branch(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return validate_refname(v)


class RepoBranchItem(BaseModel):
    name: str
    commit_sha: str | None = None
    is_default: bool = False
    protected: bool = False
    web_url: str


class RepoBranchesResponse(BaseModel):
    repository_id: int | None = None
    full_name: str
    provider: str
    default_branch: str
    web_url: str
    branches: list[RepoBranchItem] = Field(default_factory=list)


class RepoTreeEntry(BaseModel):
    name: str
    path: str
    type: str  # "dir" | "file"
    size: int | None = None
    sha: str | None = None
    web_url: str


class RepoReadmePreview(BaseModel):
    name: str
    path: str
    content: str


class RepoTreeResponse(BaseModel):
    repository_id: int | None = None
    full_name: str
    provider: str
    ref: str
    path: str
    web_url: str
    entries: list[RepoTreeEntry] = Field(default_factory=list)
    readme: RepoReadmePreview | None = None


class RepoBlobResponse(BaseModel):
    repository_id: int | None = None
    full_name: str
    provider: str
    ref: str
    path: str
    name: str
    size: int = 0
    sha: str | None = None
    is_binary: bool = False
    truncated: bool = False
    content: str | None = None
    web_url: str


DEFAULT_OUTBOUND_WEBHOOK_EVENTS: list[str] = [
    "seed.created",
    "seed.awaiting_approval",
    "seed.merged",
    "run.failed",
]


class ProjectWebhookConfigResponse(BaseModel):
    project_id: str
    enabled: bool = False
    has_secret: bool = False
    secret_preview: str | None = None
    secret: str | None = None
    github_webhook_url: str
    gitlab_webhook_url: str
    issue_label_trigger: str = "gitseed"
    auto_create_seed_on_issue: bool = True
    sync_pr_comments: bool = True
    auto_remediate_ci: bool = True
    auto_create_pr: bool = True
    outbound_url: str | None = None
    outbound_events: list[str] = Field(
        default_factory=lambda: list(DEFAULT_OUTBOUND_WEBHOOK_EVENTS)
    )


class UpdateProjectWebhookConfigRequest(BaseModel):
    enabled: bool | None = None
    secret: str | None = None
    issue_label_trigger: str | None = None
    auto_create_seed_on_issue: bool | None = None
    sync_pr_comments: bool | None = None
    auto_remediate_ci: bool | None = None
    auto_create_pr: bool | None = None
    outbound_url: str | None = None
    outbound_events: list[str] | None = None


class WebhookDeliveryResponse(BaseModel):
    accepted: bool
    provider: str
    event: str
    action: str
    seed_id: str | None = None
    seed_number: int | None = None
    detail: str = ""


class WebhookTestResponse(BaseModel):
    delivered: bool
    status_code: int | None = None
    outbound_url: str | None = None
    detail: str = ""

