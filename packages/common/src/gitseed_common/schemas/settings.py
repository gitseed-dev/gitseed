from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import QueueStrategy, SeedAutonomy

ThemeSetting = Literal["dark", "light", "system"]


class OrgSettingsResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    default_autonomy: SeedAutonomy
    default_monthly_budget: Decimal
    default_seed_budget_cap: Decimal | None = None
    default_spend_checkpoint: Decimal | None = None
    enforcement: Literal["enforced", "estimated"] = "enforced"
    enforcement_detail: str | None = None
    max_concurrent_runners: int
    max_sandbox_sessions: int = 2
    default_project_concurrency: int
    queue_strategy: QueueStrategy
    pat_lifetime_days: int
    default_theme: ThemeSetting = "system"
    default_ambient_bg: bool = True
    default_show_guide: bool = True
    default_stage_seeds: bool = False
    updated_at: datetime | None = None
    updated_by: str | None = None


class UpdateOrgSettingsRequest(BaseModel):
    default_autonomy: SeedAutonomy | None = None
    default_monthly_budget: Decimal | None = Field(default=None, ge=0)
    default_seed_budget_cap: Decimal | None = Field(default=None, gt=0)
    default_spend_checkpoint: Decimal | None = Field(default=None, gt=0)
    max_concurrent_runners: int | None = Field(default=None, ge=1)
    max_sandbox_sessions: int | None = Field(default=None, ge=1)
    default_project_concurrency: int | None = Field(default=None, ge=0)
    queue_strategy: QueueStrategy | None = None
    pat_lifetime_days: int | None = Field(default=None, ge=30, le=365)
    default_theme: ThemeSetting | None = None
    default_ambient_bg: bool | None = None
    default_show_guide: bool | None = None
    default_stage_seeds: bool | None = None
