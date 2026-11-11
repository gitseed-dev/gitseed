from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import AgentRuntime, AgentSessionStatus
from gitseed_common.schemas.agent import ProposedSeedDraft


class AgentSessionMessageResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    session_id: str
    role: Literal["user", "assistant"]
    content: str
    tool_log: str | None = None
    proposed_seeds: list[ProposedSeedDraft] = Field(default_factory=list)
    referenced_files: list[str] = Field(default_factory=list)
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")
    created_at: datetime | None = None


class AgentSessionResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    seed_id: str | None = None
    repository_id: int | None = None
    ref: str
    head_sha: str = ""
    agent_id: str
    agent_handle: str = ""
    agent_name: str = ""
    runtime: AgentRuntime
    model: str = "default"
    inference_backend_id: str | None = None
    models_backend_id: str | None = None
    provider_backend_id: str | None = None
    compute_backend_id: str | None = None
    title: str
    status: AgentSessionStatus
    is_saved: bool = False
    container_id: str | None = None
    live_turn_log: str | None = None
    error: str | None = None
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_cost: Decimal = Decimal("0")
    context_window: dict[str, Any] | None = None
    started_by: str | None = None
    created_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    stopped_at: datetime | None = None
    pushed_seed_id: str | None = None
    messages: list[AgentSessionMessageResponse] = Field(default_factory=list)


class CreateAgentSessionRequest(BaseModel):
    agent_id: str | None = None
    runtime: AgentRuntime | None = None
    inference_backend_id: str | None = None
    models_backend_id: str | None = None
    provider_backend_id: str | None = None
    model: str | None = None
    repository_id: int | None = None
    seed_id: str | None = None
    ref: str | None = None
    title: str | None = Field(default=None, max_length=160)
    is_saved: bool = False
    initial_message: str | None = Field(default=None, max_length=12000)
    initial_prompt: str | None = Field(default=None, max_length=12000)
    propose_seeds: bool = False


class SendAgentSessionMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=12000)
    propose_seeds: bool = False


class UpdateAgentSessionRequest(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=160)
    is_saved: bool | None = None
    agent_id: str | None = None

