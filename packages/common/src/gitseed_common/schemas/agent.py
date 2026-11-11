from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import AgentKind, AgentRuntime, SeedAutonomy, SeedStage


class AgentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    handle: str
    name: str
    kind: AgentKind
    template: str | None = None
    instructions: str = ""
    runtime: AgentRuntime
    model: str = "default"
    compatibility_warning: str | None = None
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True
    allowed_backend_ids: list[str] = Field(default_factory=list)
    run_count: int = 0
    succeeded_runs: int = 0
    total_cost: Decimal = Decimal("0")
    first_pass_rate: float | None = None
    avg_cost_per_seed: Decimal = Decimal("0")
    avg_attempts: float = 1.0
    archived_at: datetime | None = None
    archived_by: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class AgentBenchmarkEntry(BaseModel):
    """Empirical benchmark rollup for an (agent, runtime, model, stage) cohort."""

    agent_id: str
    agent_handle: str
    agent_name: str
    runtime: AgentRuntime
    model: str
    stage: SeedStage | None = None
    run_count: int = 0
    succeeded_runs: int = 0
    seed_count: int = 0
    stage_instances: int = 0
    first_pass_approved_count: int = 0
    evaluated_gate_count: int = 0
    first_pass_approval_rate: float | None = None
    technical_success_rate: float = 0.0
    avg_attempts_per_stage: float = 1.0
    total_cost: Decimal = Decimal("0")
    avg_cost_per_seed: Decimal = Decimal("0")
    avg_cost_per_stage: Decimal = Decimal("0")
    total_tokens: int = 0
    avg_tokens_per_run: int = 0
    avg_duration_seconds: float | None = None


class AgentBenchmarksResponse(BaseModel):
    """Comparative empirical scorecard across agents, runtimes, models, and stages."""

    project_id: str | None = None
    stage: SeedStage | None = None
    entries: list[AgentBenchmarkEntry] = Field(default_factory=list)
    insights: list[str] = Field(default_factory=list)


class CreateAgentRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    handle: str | None = Field(default=None, min_length=2, max_length=32)
    kind: AgentKind = AgentKind.STAGE
    template: str | None = None
    instructions: str = ""
    runtime: AgentRuntime = AgentRuntime.OPENCODE
    model: str = Field(default="default", min_length=1, max_length=100)
    skills: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    enabled: bool = True


class UpdateAgentRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    handle: str | None = Field(default=None, min_length=2, max_length=32)
    kind: AgentKind | None = None
    template: str | None = None
    instructions: str | None = None
    runtime: AgentRuntime | None = None
    model: str | None = Field(default=None, min_length=1, max_length=100)
    skills: list[str] | None = None
    tools: list[str] | None = None
    enabled: bool | None = None


class CoordinatorChatMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant)$")
    content: str = Field(min_length=1, max_length=8000)


class ProposedSeedDraft(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = ""
    specification: str = ""
    acceptance_criteria: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    autonomy: SeedAutonomy = SeedAutonomy.SUPERVISED


class CoordinatorChatRequest(BaseModel):
    messages: list[CoordinatorChatMessage] = Field(min_length=1, max_length=30)
    project_id: str | None = None
    seed_id: str | None = None
    mode: Literal["general", "brainstorm"] = "general"
    propose_seeds: bool = False


class CoordinatorChatResponse(BaseModel):
    reply: str
    agent_handle: str = "coordinator"
    model: str = "default"
    input_tokens: int = 0
    output_tokens: int = 0
    proposed_seeds: list[ProposedSeedDraft] = Field(default_factory=list)
    referenced_files: list[str] = Field(default_factory=list)
