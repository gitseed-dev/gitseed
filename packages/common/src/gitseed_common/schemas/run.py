from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gitseed_common.enums import AgentRuntime, RunStatus, SeedStage


class RunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    seed_id: str
    task_id: str | None = None
    stage: SeedStage
    attempt: int
    agent_id: str
    runtime: AgentRuntime
    model: str
    compute_backend_id: str | None = None
    status: RunStatus
    container_id: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cost: Decimal = Decimal("0")
    cost_enforcement: Literal["enforced", "estimated", "unpriced"] = "enforced"
    enforcement: Literal["enforced", "estimated", "unpriced"] = "enforced"
    enforcement_detail: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    exit_code: int | None = None
    error: str | None = None
    log_path: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    cancelled_by: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    @model_validator(mode="after")
    def _populate_enforcement_from_output(self) -> "RunResponse":
        if isinstance(self.output, dict):
            raw_enf = str(
                self.output.get("cost_enforcement")
                or self.output.get("budget_enforcement")
                or self.output.get("enforcement")
                or ""
            ).strip().lower()
            if raw_enf in ("enforced", "estimated", "unpriced"):
                resolved: Literal["enforced", "estimated", "unpriced"] = (
                    "estimated"
                    if raw_enf == "estimated"
                    else "unpriced"
                    if raw_enf == "unpriced"
                    else "enforced"
                )
                self.cost_enforcement = resolved
                self.enforcement = resolved
            elif str(self.output.get("auth_mode") or "").strip().lower() == "cli_oauth":
                self.cost_enforcement = "estimated"
                self.enforcement = "estimated"
            raw_detail = self.output.get("enforcement_detail")
            if isinstance(raw_detail, str) and raw_detail.strip() and not self.enforcement_detail:
                self.enforcement_detail = raw_detail.strip()
        return self


class CreateRunRequest(BaseModel):
    seed_id: str
    task_id: str | None = None
    stage: SeedStage | None = None
    agent_id: str | None = None
    compute_backend_id: str | None = None


class UpdateRunRequest(BaseModel):
    status: RunStatus | None = None
    compute_backend_id: str | None = None
    container_id: str | None = Field(default=None, max_length=100)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cost: Decimal | None = Field(default=None, ge=0)
    output: dict[str, Any] | None = None
    exit_code: int | None = None
    error: str | None = None
    log_path: str | None = None


class RunLogsResponse(BaseModel):
    run_id: str
    status: RunStatus
    log_tail: str = ""
