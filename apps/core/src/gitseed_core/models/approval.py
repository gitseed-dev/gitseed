from datetime import datetime
from typing import Any
from uuid import uuid4

from gitseed_common.enums import ApprovalDecision, ProjectRole, SeedStage
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _approval_id() -> str:
    return f"apr_{uuid4().hex[:12]}"


class Approval(SQLModel, table=True):
    """A decision at a stage gate: by a person, or automatically by policy.

    Append-only: a decision is never edited. A new decision is a new row, so the
    latest row per (seed, stage) is the current one. The self-approval rule
    (decided_by != seed.created_by unless the policy allows it) is checked in code.
    """

    __tablename__ = "approval"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('ideate', 'plan', 'execute', 'review', 'merge')",
            name="ck_approval_stage",
        ),
        CheckConstraint(
            "decision IN ('approved', 'rejected', 'changes_requested')",
            name="ck_approval_decision",
        ),
        CheckConstraint(
            "automatic = (decided_by IS NULL)", name="ck_approval_decided_by"
        ),
        CheckConstraint(
            "automatic = (approver_role IS NULL)", name="ck_approval_approver_role"
        ),
        CheckConstraint(
            "NOT automatic OR decision = 'approved'", name="ck_approval_automatic_approves"
        ),
        CheckConstraint(
            "decision = 'approved' OR length(btrim(coalesce(comment, ''))) > 0",
            name="ck_approval_comment",
        ),
        Index("ix_approval_seed_created", "seed_id", "created_at"),
        Index(
            "ix_approval_decided_by",
            "decided_by",
            "created_at",
            postgresql_where=text("decided_by IS NOT NULL"),
        ),
    )

    id: str = Field(default_factory=_approval_id, primary_key=True)
    seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    stage: SeedStage = Field(sa_column=Column(String, nullable=False))
    snapshot: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    decision: ApprovalDecision = Field(sa_column=Column(String, nullable=False))
    comment: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    automatic: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    decided_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="RESTRICT"), nullable=True),
    )
    approver_role: ProjectRole | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
