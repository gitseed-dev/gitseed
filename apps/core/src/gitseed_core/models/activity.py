from datetime import datetime
from typing import Any

from gitseed_common.enums import ActivityType, ActorType
from sqlalchemy import BigInteger, CheckConstraint, Column, DateTime, Index, String, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


class Activity(SQLModel, table=True):
    """Append-only event log: every activity feed, and the audit trail."""

    __tablename__ = "activity"
    __table_args__ = (
        CheckConstraint(
            "actor_type IN ('user', 'agent', 'system')", name="ck_activity_actor_type"
        ),
        CheckConstraint(
            "seed_id IS NULL OR project_id IS NOT NULL",
            name="ck_activity_seed_has_project",
        ),
        CheckConstraint(
            "actor_type = 'system' OR actor_id IS NOT NULL",
            name="ck_activity_actor_id",
        ),
        Index("ix_activity_project_occurred", "project_id", "occurred_at"),
        Index(
            "ix_activity_seed_occurred",
            "seed_id",
            "occurred_at",
            postgresql_where=text("seed_id IS NOT NULL"),
        ),
        Index("ix_activity_occurred", "occurred_at"),
    )

    id: int | None = Field(
        default=None,
        sa_column=Column(BigInteger, primary_key=True, autoincrement=True),
    )
    occurred_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        ),
    )
    project_id: str | None = None
    seed_id: str | None = None
    actor_type: ActorType = Field(sa_column=Column(String, nullable=False))
    actor_id: str | None = None
    type: ActivityType = Field(sa_column=Column(String, nullable=False))
    data: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
