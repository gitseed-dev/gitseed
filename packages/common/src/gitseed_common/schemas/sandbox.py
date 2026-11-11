from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import SandboxStatus


class SandboxArtifactItem(BaseModel):
    name: str
    rel_path: str
    size_bytes: int
    sha256: str
    # Why this file was kept: e.g. "saved", ".gitseed/artifacts", or the glob that matched
    rule: str | None = None


class SandboxRunConfigResponse(BaseModel):
    source: Literal["project_settings", "run_json", "auto_detected", "default"] = "default"
    ref: str
    image: str = "python:3.12-slim"
    setup_command: str | None = None
    run_command: str = ""
    test_command: str | None = None
    artifact_globs: list[str] = Field(default_factory=list)


class CreateSandboxSessionRequest(BaseModel):
    repository_id: int | None = None
    seed_id: str | None = None
    ref: str | None = None
    # Empty = use the image detected from the repo (project settings / run.json / stack)
    image: str = Field(default="", max_length=160)
    setup_command: str | None = None
    # Required for `job`; ignored for `terminal` (an interactive shell is opened over the PTY)
    command: str = ""
    artifact_globs: list[str] = Field(default_factory=list)
    initial_stdin: str | None = None
    as_root: bool = False
    kind: Literal["job", "terminal"] = "job"
    title: str | None = Field(default=None, max_length=160)


class UpdateSandboxSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)


class SaveSandboxArtifactRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)


class SandboxInputRequest(BaseModel):
    input: str = Field(default="")
    append_newline: bool = True
    eof: bool = False


class SandboxSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    seed_id: str | None = None
    repository_id: int | None = None
    ref: str
    head_sha: str = ""
    image: str
    setup_command: str | None = None
    command: str
    artifact_globs: list[str] = Field(default_factory=list)
    status: SandboxStatus
    exit_code: int | None = None
    container_id: str | None = None
    compute_backend_id: str | None = None
    as_root: bool = False
    artifacts: list[SandboxArtifactItem] = Field(default_factory=list)
    log_tail: str | None = None
    error: str | None = None
    started_by: str | None = None
    started_at: datetime | None = None
    expires_at: datetime | None = None
    stopped_at: datetime | None = None
    kind: str = "job"
    title: str | None = None
    last_activity_at: datetime | None = None
    pushed_seed_id: str | None = None
    artifacts_expire_at: datetime | None = None


# ---------------------------------------------------------------------------
# Push session work to a branch and open a seed at Review (shared by terminal
# sandboxes and agent chat sessions)
# ---------------------------------------------------------------------------


class SessionChangedFile(BaseModel):
    path: str
    status: Literal["added", "modified", "deleted"]


class SessionPushDraftResponse(BaseModel):
    title: str
    summary: str
    acceptance_criteria: list[str] = Field(default_factory=list)
    changed_files: list[SessionChangedFile] = Field(default_factory=list)
    branch: str
    drafted_by: str = "fallback"
    linked_seed_id: str | None = None
    linked_seed_number: int | None = None
    linked_seed_title: str | None = None
    linked_seed_branch: str | None = None


class SessionPushRequest(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(default="", max_length=20000)
    acceptance_criteria: list[str] = Field(default_factory=list)
    target_seed_id: str | None = None


class SessionPushResponse(BaseModel):
    seed_id: str
    seed_number: int
    branch: str
    commit_sha: str
    pull_request_url: str | None = None
    changed_files: int


class SessionWorkspaceFileEntry(BaseModel):
    path: str
    size: int
    status: Literal["added", "modified", "deleted"] | None = None


class SessionWorkspaceFilesResponse(BaseModel):
    files: list[SessionWorkspaceFileEntry] = Field(default_factory=list)
    changed_files: list[SessionChangedFile] = Field(default_factory=list)


class SessionWorkspaceFileContentResponse(BaseModel):
    path: str
    content: str
    original_content: str | None = None
    is_binary: bool = False
    status: Literal["added", "modified", "deleted", "unchanged"] = "unchanged"


class WriteSessionWorkspaceFileRequest(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    content: str = Field(default="", max_length=5_000_000)


class DiscardSessionWorkspaceRequest(BaseModel):
    path: str | None = Field(default=None, max_length=1024)


class SessionVsCodeResponse(BaseModel):
    url: str
    mode: Literal["in_container", "sidecar"] = "in_container"
    arch: str = "amd64"
    memory_mb: int = 4096
    memory_raised: bool = False
    notice: str | None = None



class SessionListItem(BaseModel):
    """One row in the cross-project Sessions list (terminal sandbox or agent chat)."""

    kind: Literal["chat", "terminal"]
    id: str
    project_id: str
    project_slug: str
    project_name: str
    name: str
    ref: str
    head_sha: str = ""
    status: str
    is_running: bool
    started_by: str | None = None
    started_at: datetime | None = None
    last_activity_at: datetime | None = None
    expires_at: datetime | None = None
    agent_handle: str | None = None
    image: str | None = None
    pushed_seed_id: str | None = None
    context_window: dict[str, Any] | None = None
