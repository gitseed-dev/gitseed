from datetime import datetime
from decimal import Decimal
from uuid import uuid4

from gitseed_common.enums import (
    ActorType,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from sqlalchemy import (
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _seed_id() -> str:
    return f"sed_{uuid4().hex[:12]}"


def _comment_id() -> str:
    return f"cmt_{uuid4().hex[:12]}"


def _task_id() -> str:
    return f"tsk_{uuid4().hex[:12]}"


class Seed(SQLModel, table=True):
    """One piece of work moving through Ideate -> Plan -> Execute -> Review -> Merge.

    stage says where it is; status says what is happening there. "Needs you" is
    status = awaiting_approval, labelled by stage. Runs, approvals and spend detail
    live in their own tables; spent is a cache of the run costs, kept by the scheduler.
    """

    __tablename__ = "seed"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('ideate', 'plan', 'execute', 'review', 'merge')",
            name="ck_seed_stage",
        ),
        CheckConstraint(
            "status IN ('staged', 'queued', 'running', 'awaiting_approval', 'blocked', "
            "'failed', 'stopped', 'merged', 'closed')",
            name="ck_seed_status",
        ),
        CheckConstraint("status <> 'merged' OR stage = 'merge'", name="ck_seed_merged_stage"),
        CheckConstraint(
            "(status = 'merged') = (merged_at IS NOT NULL)", name="ck_seed_merged_at"
        ),
        CheckConstraint(
            "(status = 'closed') = (closed_at IS NOT NULL)", name="ck_seed_closed_at"
        ),
        CheckConstraint(
            "closed_at IS NOT NULL OR closed_by IS NULL", name="ck_seed_closed_by"
        ),
        CheckConstraint(
            "(status = 'blocked') = (blocked_reason IS NOT NULL)",
            name="ck_seed_blocked_reason",
        ),
        CheckConstraint("budget_cap IS NULL OR budget_cap > 0", name="ck_seed_budget_cap"),
        CheckConstraint("spent >= 0", name="ck_seed_spent"),
        CheckConstraint(
            "autonomy IN ('manual', 'supervised', 'autonomous')", name="ck_seed_autonomy"
        ),
        CheckConstraint(
            "checkpoint_approved_through >= 0", name="ck_seed_checkpoint_approved"
        ),
        UniqueConstraint("project_id", "number", name="uq_seed_project_number"),
        Index("ix_seed_project_status", "project_id", "status"),
        Index(
            "ix_seed_open",
            "status",
            "stage_entered_at",
            postgresql_where=text("status NOT IN ('merged', 'closed')"),
        ),
    )

    id: str = Field(default_factory=_seed_id, primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("project.id", ondelete="RESTRICT"), nullable=False)
    )
    number: int = Field(sa_column=Column(Integer, nullable=False))
    title: str = Field(sa_column=Column(String(200), nullable=False))
    description: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    specification: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    acceptance_criteria: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    plan: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    stage: SeedStage = Field(
        default=SeedStage.IDEATE,
        sa_column=Column(String, nullable=False, server_default=SeedStage.IDEATE.value),
    )
    status: SeedStatus = Field(
        default=SeedStatus.QUEUED,
        sa_column=Column(String, nullable=False, server_default=SeedStatus.QUEUED.value),
    )
    autonomy: SeedAutonomy = Field(
        default=SeedAutonomy.SUPERVISED,
        sa_column=Column(String, nullable=False, server_default=SeedAutonomy.SUPERVISED.value),
    )
    stage_entered_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    blocked_reason: str | None = Field(
        default=None, sa_column=Column(String(200), nullable=True)
    )
    budget_cap: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(12, 2), nullable=True)
    )
    spent: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False, server_default=text("0")),
    )
    checkpoint_approved_through: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False, server_default=text("0")),
    )
    merged_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    closed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    closed_by: str | None = Field(
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


class SeedComment(SQLModel, table=True):
    """A comment on a seed, by a person or an agent.
 
    Approval notes stay on the approval row; the seed timeline merges comments,
    approvals and activity for display. Comments are soft-deleted so the
    timeline can show "comment removed".
    """
 
    __tablename__ = "seed_comment"
    __table_args__ = (
        CheckConstraint("author_type IN ('user', 'agent')", name="ck_seed_comment_author_type"),
        CheckConstraint(
            "stage IN ('ideate', 'plan', 'execute', 'review', 'merge')",
            name="ck_seed_comment_stage",
        ),
        CheckConstraint("length(btrim(body)) > 0", name="ck_seed_comment_body"),
        CheckConstraint(
            "deleted_at IS NOT NULL OR deleted_by IS NULL", name="ck_seed_comment_deleted_by"
        ),
        Index("ix_seed_comment_seed_created", "seed_id", "created_at"),
    )
 
    id: str = Field(default_factory=_comment_id, primary_key=True)
    seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    author_type: ActorType = Field(sa_column=Column(String, nullable=False))
    author_id: str = Field(sa_column=Column(String, nullable=False))
    stage: SeedStage = Field(
        sa_column=Column(String, nullable=False)
    )
    body: str = Field(sa_column=Column(Text, nullable=False))
    edited_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    deleted_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    deleted_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
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


class SeedTask(SQLModel, table=True):
    """One step of a seed's plan, worked through during Execute.
 
    Plan creates the list and approving the plan approves it. Runs point at the
    task they worked on (run.task_id), so cost and attempts are not stored here.
    """
 
    __tablename__ = "seed_task"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'done', 'failed', 'skipped')",
            name="ck_seed_task_status",
        ),
        CheckConstraint("position >= 0", name="ck_seed_task_position"),
        CheckConstraint(
            "status NOT IN ('running', 'done', 'failed') OR started_at IS NOT NULL",
            name="ck_seed_task_started_at",
        ),
        CheckConstraint(
            "(status IN ('done', 'failed', 'skipped')) = (completed_at IS NOT NULL)",
            name="ck_seed_task_completed_at",
        ),
        UniqueConstraint(
            "seed_id",
            "position",
            name="uq_seed_task_position",
            deferrable=True,
            initially="DEFERRED",
        ),
    )
 
    id: str = Field(default_factory=_task_id, primary_key=True)
    seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    position: int = Field(sa_column=Column(Integer, nullable=False))
    title: str = Field(sa_column=Column(String(200), nullable=False))
    description: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    status: TaskStatus = Field(
        default=TaskStatus.PENDING,
        sa_column=Column(String, nullable=False, server_default=TaskStatus.PENDING.value),
    )
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    completed_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
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
