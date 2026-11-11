from datetime import datetime
from typing import Any
from uuid import uuid4

from gitseed_common.enums import OrgRole, UserStatus
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _user_id() -> str:
    return f"usr_{uuid4().hex[:12]}"


def _pat_id() -> str:
    return f"pat_{uuid4().hex[:12]}"


class User(SQLModel, table=True):
    """A person who can sign in. What they can approve is not stored here: it is
    derived from their project roles (ProjectMember) and the stage Policies."""

    __tablename__ = "user"
    __table_args__ = (
        CheckConstraint("org_role IN ('admin', 'member')", name="ck_user_org_role"),
        CheckConstraint(
            "status IN ('invited', 'active', 'suspended')", name="ck_user_status"
        ),
        CheckConstraint(
            "status = 'invited' OR password_hash IS NOT NULL",
            name="ck_user_password_set",
        ),
        CheckConstraint(
            "(status = 'suspended') = (suspended_at IS NOT NULL)",
            name="ck_user_suspended_at",
        ),
        CheckConstraint(
            "suspended_at IS NOT NULL OR suspended_by IS NULL",
            name="ck_user_suspended_by",
        ),
        Index("ux_user_email_lower", func.lower(text("email")), unique=True),
        Index(
            "ux_user_invite_token_hash",
            "invite_token_hash",
            unique=True,
            postgresql_where=text("invite_token_hash IS NOT NULL"),
        ),
    )

    id: str = Field(default_factory=_user_id, primary_key=True)
    email: str = Field(sa_column=Column(String(254), nullable=False))
    name: str = Field(sa_column=Column(String(120), nullable=False))
    preferred_name: str | None = Field(
        default=None, sa_column=Column(String(60), nullable=True)
    )
    org_role: OrgRole = Field(
        default=OrgRole.MEMBER,
        sa_column=Column(String, nullable=False, server_default=OrgRole.MEMBER.value),
    )
    status: UserStatus = Field(
        default=UserStatus.INVITED,
        sa_column=Column(String, nullable=False, server_default=UserStatus.INVITED.value),
    )
    password_hash: str | None = Field(
        default=None, sa_column=Column(String(255), nullable=True)
    )
    invite_token_hash: str | None = Field(
        default=None, sa_column=Column(String(64), nullable=True)
    )
    invite_expires_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    last_active_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    preferences: dict[str, Any] = Field(
       default_factory=dict,
       sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    suspended_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    suspended_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    created_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True),
            nullable=False,
            server_default=text("now()"),
            onupdate=text("now()"),
        ),
    )


class PersonalAccessToken(SQLModel, table=True):
    """A token for the CLI, MCP server or scripts. It acts as its user, with their roles.

    The token is shown once at creation and only its SHA-256 hash is stored. Format:
    "gsp_" + 32 random characters; the first 8 characters are kept for display.
    """

    __tablename__ = "personal_access_token"
    __table_args__ = (
        CheckConstraint("expires_at > created_at", name="ck_pat_expires_at"),
        CheckConstraint(
            "revoked_at IS NOT NULL OR revoked_by IS NULL", name="ck_pat_revoked_by"
        ),
        Index("ux_pat_token_hash", "token_hash", unique=True),
        Index("ix_pat_user_active", "user_id", postgresql_where=text("revoked_at IS NULL")),
    )

    id: str = Field(default_factory=_pat_id, primary_key=True)
    user_id: str = Field(
        sa_column=Column(String, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    name: str = Field(sa_column=Column(String(80), nullable=False))
    token_prefix: str = Field(sa_column=Column(String(12), nullable=False))
    token_hash: str = Field(sa_column=Column(String(64), nullable=False))
    last_used_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    expires_at: datetime = Field(sa_column=Column(DateTime(timezone=True), nullable=False))
    revoked_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    revoked_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
