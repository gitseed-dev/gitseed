from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from gitseed_common.enums import AgentRuntime, AgentSessionStatus
from sqlalchemy import (
    BigInteger,
    Boolean,
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


def _session_id() -> str:
    return f"ses_{uuid4().hex[:12]}"


def _session_msg_id() -> str:
    return f"smsg_{uuid4().hex[:12]}"


class AgentSession(SQLModel, table=True):
    """An interactive, sandbox-backed live session with a configured Agent on a project's codebase."""

    __tablename__ = "agent_session"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting', 'idle', 'running_turn', 'stopped', 'failed')",
            name="ck_agent_session_status",
        ),
        Index("ix_agent_session_project_updated", "project_id", "updated_at"),
        Index("ix_agent_session_saved", "project_id", "is_saved"),
    )

    id: str = Field(default_factory=_session_id, primary_key=True)
    project_id: str = Field(
        sa_column=Column(
            String, ForeignKey("project.id", ondelete="CASCADE"), nullable=False
        )
    )
    seed_id: str | None = Field(
        default=None,
        sa_column=Column(
            String, ForeignKey("seed.id", ondelete="SET NULL"), nullable=True
        ),
    )
    repository_id: int | None = Field(
        default=None,
        sa_column=Column(
            Integer,
            ForeignKey("project_repository.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    ref: str = Field(
        default="main",
        sa_column=Column(String(160), nullable=False, server_default="main"),
    )
    head_sha: str = Field(
        default="",
        sa_column=Column(String(64), nullable=False, server_default=""),
    )
    agent_id: str = Field(
        sa_column=Column(
            String, ForeignKey("agent.id", ondelete="CASCADE"), nullable=False
        )
    )
    runtime: AgentRuntime = Field(
        default=AgentRuntime.OPENCODE,
        sa_column=Column(
            String(40), nullable=False, server_default=AgentRuntime.OPENCODE.value
        ),
    )
    model: str = Field(
        default="default",
        sa_column=Column(String(120), nullable=False, server_default="default"),
    )
    models_backend_id: str | None = Field(
        default=None,
        sa_column=Column(
            String, ForeignKey("backend.id", ondelete="SET NULL"), nullable=True
        ),
    )
    compute_backend_id: str | None = Field(
        default=None,
        sa_column=Column(
            String, ForeignKey("backend.id", ondelete="SET NULL"), nullable=True
        ),
    )
    title: str = Field(
        default="New Agent Session",
        sa_column=Column(
            String(160), nullable=False, server_default="New Agent Session"
        ),
    )
    status: AgentSessionStatus = Field(
        default=AgentSessionStatus.STARTING,
        sa_column=Column(
            String(32), nullable=False, server_default=AgentSessionStatus.STARTING.value
        ),
    )
    is_saved: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    container_id: str | None = Field(
        default=None,
        sa_column=Column(String(120), nullable=True),
    )
    live_turn_log: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    error: str | None = Field(
        default=None,
        sa_column=Column(Text, nullable=True),
    )
    total_input_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    total_output_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    total_cost: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(
            Numeric(12, 6), nullable=False, server_default=text("0")
        ),
    )
    created_by: str | None = Field(
        default=None,
        sa_column=Column(
            String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
        ),
    )
    pushed_seed_id: str | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        ),
    )
    updated_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        ),
    )


class AgentSessionMessage(SQLModel, table=True):
    """A single user or assistant turn in an interactive AgentSession."""

    __tablename__ = "agent_session_message"
    __table_args__ = (
        CheckConstraint(
            "role IN ('user', 'assistant')",
            name="ck_agent_session_message_role",
        ),
        Index("ix_agent_session_msg_session_created", "session_id", "created_at"),
    )

    id: str = Field(default_factory=_session_msg_id, primary_key=True)
    session_id: str = Field(
        sa_column=Column(
            String,
            ForeignKey("agent_session.id", ondelete="CASCADE"),
            nullable=False,
        )
    )
    role: str = Field(sa_column=Column(String(16), nullable=False))
    content: str = Field(default="", sa_column=Column(Text, nullable=False))
    tool_log: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    proposed_seeds: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    referenced_files: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    input_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    output_tokens: int = Field(
        default=0,
        sa_column=Column(BigInteger, nullable=False, server_default=text("0")),
    )
    cost: Decimal = Field(
        default=Decimal("0"),
        sa_column=Column(
            Numeric(12, 6), nullable=False, server_default=text("0")
        ),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        ),
    )

