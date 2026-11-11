from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import BackendHealth, BackendType


class CreateBackendRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    type: BackendType
    provider: str = Field(min_length=1, max_length=64)
    endpoint: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)
    capacity: int | None = Field(default=None, gt=0)
    access_restricted: bool = False
    enabled: bool = True


class UpdateBackendRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    endpoint: str | None = None
    config: dict[str, Any] | None = None
    secrets: dict[str, str] | None = None
    capacity: int | None = Field(default=None, gt=0)
    access_restricted: bool | None = None
    enabled: bool | None = None


class BackendResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    type: BackendType
    provider: str
    endpoint: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secret_keys: list[str] = Field(default_factory=list)
    capacity: int | None = None
    access_restricted: bool
    allowed_agent_ids: list[str] = Field(default_factory=list)
    health: BackendHealth
    health_checked_at: datetime | None = None
    health_latency_ms: int | None = None
    health_error: str | None = None
    enabled: bool
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None


class ProbeBackendRequest(BaseModel):
    type: BackendType
    provider: str = Field(min_length=1, max_length=64)
    endpoint: str | None = None
    config: dict[str, Any] = Field(default_factory=dict)
    secrets: dict[str, str] = Field(default_factory=dict)


class ProbeBackendResponse(BaseModel):
    health: BackendHealth
    latency_ms: int | None = None
    error: str | None = None


class CliAuthInputRequest(BaseModel):
    code: str = Field(min_length=1, max_length=8192)


class CliAuthSessionResponse(BaseModel):
    status: str
    auth_url: str | None = None
    user_code: str | None = None
    log_output: str = ""
    authenticated: bool = False
    error: str | None = None
    backend: BackendResponse | None = None


class CreateRemoteRepoRequest(BaseModel):
    name: str = Field(min_length=1, max_length=140)
    description: str = Field(default="", max_length=350)
    private: bool = True
    auto_init: bool = True


class CreateRemoteRepoResponse(BaseModel):
    full_name: str
    default_branch: str = "main"
    private: bool = True
    web_url: str | None = None
    backend: BackendResponse


class GitHubAppManifestStartRequest(BaseModel):
    backend_id: str | None = None
    name: str | None = Field(default=None, max_length=120)
    organization: str | None = Field(default=None, max_length=100)
    endpoint: str | None = None
    origin: str | None = None


class GitHubAppManifestStartResponse(BaseModel):
    action_url: str
    manifest: str
    state: str
    backend: BackendResponse

