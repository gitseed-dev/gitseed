from datetime import datetime
from uuid import uuid4

from gitseed_common.enums import AgentKind, AgentRuntime
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


def _agent_id() -> str:
    return f"agt_{uuid4().hex[:12]}"


class Agent(SQLModel, table=True):
    """A reusable worker definition: how work is done (instructions, capabilities,
    runtime, model). Which stage it works on is set in Policies, not here.
    Run counts, first-pass rate and spend are computed from runs, not stored."""

    __tablename__ = "agent"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('stage', 'assistant')", name="ck_agent_kind"
        ),
        CheckConstraint(
            "runtime IN ('opencode', 'claude-code', 'codex', 'antigravity', 'gemini', 'grok-build')",
            name="ck_agent_runtime",
        ),
        CheckConstraint(
            "handle ~ '^[a-z][a-z0-9-]{1,31}$'", name="ck_agent_handle_format"
        ),
        Index(
            "ux_agent_handle_active",
            "handle",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: str = Field(default_factory=_agent_id, primary_key=True)
    handle: str = Field(sa_column=Column(String(32), nullable=False))
    name: str = Field(sa_column=Column(String(80), nullable=False))
    kind: AgentKind = Field(
        default=AgentKind.STAGE,
        sa_column=Column(String, nullable=False, server_default=AgentKind.STAGE.value),
    )
    template: str | None = None
    instructions: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    runtime: AgentRuntime = Field(
        default=AgentRuntime.OPENCODE,
        sa_column=Column(String, nullable=False, server_default=AgentRuntime.OPENCODE.value),
    )
    model: str = Field(
        default="default",
        sa_column=Column(String, nullable=False, server_default="default"),
    )
    skills: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    tools: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true"))
    )
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


class AgentBackend(SQLModel, table=True):
    """An agent allowed to use a restricted backend (e.g. EKS → @coder only)."""

    __tablename__ = "agent_backend"
    __table_args__ = (Index("ix_agent_backend_agent", "agent_id"),)

    backend_id: str = Field(
        sa_column=Column(
            String, ForeignKey("backend.id", ondelete="CASCADE"), primary_key=True
        )
    )
    agent_id: str = Field(
        sa_column=Column(String, ForeignKey("agent.id", ondelete="CASCADE"), primary_key=True)
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    created_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
