from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class SkillResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    name: str
    description: str = ""
    content: str = ""
    archived_at: datetime | None = None
    archived_by: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class CreateSkillRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    slug: str | None = Field(default=None, min_length=2, max_length=48)
    description: str = Field(default="", max_length=240)
    content: str = Field(default="")


class UpdateSkillRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    slug: str | None = Field(default=None, min_length=2, max_length=48)
    description: str | None = Field(default=None, max_length=240)
    content: str | None = None

