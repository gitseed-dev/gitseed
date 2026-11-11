from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import ActivityType, ActorType


class ActivityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    occurred_at: datetime
    project_id: str | None = None
    seed_id: str | None = None
    actor_type: ActorType
    actor_id: str | None = None
    type: ActivityType
    data: dict[str, Any] = Field(default_factory=dict)
