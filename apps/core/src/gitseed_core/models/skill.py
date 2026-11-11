from datetime import datetime
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlmodel import Field, SQLModel


def _skill_id() -> str:
    return f"skl_{uuid4().hex[:12]}"


class Skill(SQLModel, table=True):
    """A reusable Markdown instruction playbook (SKILL.md) that can be attached
    to one or more agents."""

    __tablename__ = "skill"
    __table_args__ = (
        CheckConstraint("slug ~ '^[a-z][a-z0-9-]{1,47}$'", name="ck_skill_slug_format"),
        Index(
            "ux_skill_slug_active",
            "slug",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: str = Field(default_factory=_skill_id, primary_key=True)
    slug: str = Field(sa_column=Column(String(48), nullable=False))
    name: str = Field(sa_column=Column(String(80), nullable=False))
    description: str = Field(
        default="", sa_column=Column(String(240), nullable=False, server_default="")
    )
    content: str = Field(default="", sa_column=Column(Text, nullable=False, server_default=""))
    archived_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    archived_by: str | None = Field(
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
