from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import OrgRole, UserStatus


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    preferred_name: str | None = None
    org_role: OrgRole
    status: UserStatus
    preferences: dict[str, Any] = Field(default_factory=dict)
    invite_expires_at: datetime | None = None
    last_active_at: datetime | None = None
    suspended_at: datetime | None = None
    suspended_by: str | None = None
    created_at: datetime | None = None
    created_by: str | None = None
    updated_at: datetime | None = None


class InviteUserRequest(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    name: str = Field(min_length=1, max_length=120)
    org_role: OrgRole = OrgRole.MEMBER


class UserInviteCreatedResponse(UserResponse):
    """Returned once on invite creation or reinvite with the plaintext invite token and URL."""

    invite_token: str = ""
    invite_url: str = ""


class UpdateUserRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    preferred_name: str | None = Field(default=None, max_length=60)
    org_role: OrgRole | None = None
    preferences: dict[str, Any] | None = None
