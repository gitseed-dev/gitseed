from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from gitseed_common.enums import OrgRole, UserStatus


class LoginRequest(BaseModel):
    email: str
    password: str = Field(max_length=1024)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(max_length=1024)
    new_password: str = Field(min_length=8, max_length=200)


class AcceptInviteRequest(BaseModel):
    token: str
    password: str = Field(min_length=8, max_length=200)
    preferred_name: str | None = Field(default=None, max_length=60)


class InvitePreviewResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    email: str
    name: str
    org_role: OrgRole
    invite_expires_at: datetime | None = None


class CurrentUserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: str
    name: str
    preferred_name: str | None = None
    org_role: OrgRole
    status: UserStatus
    preferences: dict[str, Any] = Field(default_factory=dict)
    last_active_at: datetime | None = None
    created_at: datetime | None = None


class CreateTokenRequest(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    expires_in_days: int | None = Field(default=None, ge=1)


class TokenResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_prefix: str
    last_used_at: datetime | None = None
    expires_at: datetime
    revoked_at: datetime | None = None
    created_at: datetime | None = None


class TokenCreatedResponse(TokenResponse):
    """Returned once on creation with the plaintext token."""

    token: str = ""


class PushDeviceRegisterRequest(BaseModel):
    """Register a native mobile/wearable device token (APNs or FCM) for encrypted push relay notifications."""

    platform: str = Field(
        ...,
        min_length=2,
        max_length=24,
        description="Device platform: ios, ipados, watchos, macos, android, or wearos.",
    )
    push_token: str = Field(
        ...,
        min_length=8,
        max_length=512,
        description="APNs or FCM device push token (encrypted at rest; never returned in API responses).",
    )
    device_name: str | None = Field(default=None, max_length=120)
    app_bundle_id: str | None = Field(default=None, max_length=160)


class PushDeviceResponse(BaseModel):
    """Sanitized push device registration metadata (never includes raw push_token)."""

    device_id: str
    platform: str
    device_name: str | None = None
    app_bundle_id: str | None = None
    registered_at: datetime

