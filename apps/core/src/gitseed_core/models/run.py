from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from gitseed_common.enums import AgentRuntime, RunStatus, SeedStage
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _run_id() -> str:
    return f"run_{uuid4().hex[:12]}"


class Run(SQLModel, table=True):
    """One agent execution for one stage of a seed, in one runner container.

    The scheduler creates it (queued), launches the container (starting), and the
    runner reports back (running -> succeeded / failed). Agent, runtime and model
    are copied at creation so history stays accurate after the agent is edited.
    Cost comes from the run's LiteLLM virtual key; the scheduler adds it to seed.spent.
    """

    __tablename__ = "run"
    __table_args__ = (
        CheckConstraint(
            "status IN ('queued', 'starting', 'running', 'succeeded', 'failed', "
            "'cancelled', 'timed_out')",
            name="ck_run_status",
        ),
        CheckConstraint(
            "stage IN ('ideate', 'plan', 'execute', 'review', 'merge')", name="ck_run_stage"
        ),
        CheckConstraint("attempt >= 1", name="ck_run_attempt"),
        CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0 AND cost >= 0", name="ck_run_usage"
        ),
        CheckConstraint(
            "status IN ('queued', 'starting', 'cancelled') OR started_at IS NOT NULL",
            name="ck_run_started_at",
        ),
        CheckConstraint(
            "(status IN ('succeeded', 'failed', 'cancelled', 'timed_out')) "
            "= (finished_at IS NOT NULL)",
            name="ck_run_finished_at",
        ),
        CheckConstraint(
            "status = 'cancelled' OR cancelled_by IS NULL", name="ck_run_cancelled_by"
        ),
        Index("ix_run_seed_created", "seed_id", "created_at"),
        Index("ix_run_agent_created", "agent_id", "created_at"),
        Index("ix_run_task", "task_id", postgresql_where=text("task_id IS NOT NULL")),
        Index(
            "ix_run_active",
            "compute_backend_id",
            "status",
            postgresql_where=text("status IN ('queued', 'starting', 'running')"),
        ),
    )

    id: str = Field(default_factory=_run_id, primary_key=True)
    seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    task_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("seed_task.id", ondelete="SET NULL"), nullable=True),
    )
    stage: SeedStage = Field(sa_column=Column(String, nullable=False))
    attempt: int = Field(
        default=1, sa_column=Column(Integer, nullable=False, server_default=text("1"))
    )
    agent_id: str = Field(
        sa_column=Column(String, ForeignKey("agent.id", ondelete="RESTRICT"), nullable=False)
    )
    runtime: AgentRuntime = Field(sa_column=Column(String, nullable=False))
    model: str = Field(sa_column=Column(String(100), nullable=False))
    compute_backend_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("backend.id", ondelete="RESTRICT"), nullable=True),
    )
    status: RunStatus = Field(
        default=RunStatus.QUEUED,
        sa_column=Column(String, nullable=False, server_default=RunStatus.QUEUED.value),
    )
    container_id: str | None = Field(
        default=None, sa_column=Column(String(100), nullable=True)
    )
    input_tokens: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    output_tokens: int = Field(
        default=0, sa_column=Column(BigInteger, nullable=False, server_default=text("0"))
    )
    cost: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(Numeric(12, 4), nullable=False, server_default=text("0")),
    )
    output: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    exit_code: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    log_path: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    started_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    finished_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    cancelled_by: str | None = Field(
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
