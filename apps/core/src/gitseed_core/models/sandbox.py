from datetime import datetime
from typing import Any
from uuid import uuid4

from gitseed_common.enums import SandboxStatus
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _sandbox_id() -> str:
    return f"sbx_{uuid4().hex[:12]}"


class SandboxSession(SQLModel, table=True):
    """One ephemeral, credential-free sandbox container session for running or testing a project."""

    __tablename__ = "sandbox_session"
    __table_args__ = (
        CheckConstraint(
            "status IN ('starting', 'running', 'succeeded', 'failed', 'stopped', 'timed_out')",
            name="ck_sandbox_session_status",
        ),
        Index("ix_sandbox_session_project_started", "project_id", "started_at"),
        Index(
            "ix_sandbox_session_seed",
            "seed_id",
            postgresql_where=text("seed_id IS NOT NULL"),
        ),
        Index(
            "ix_sandbox_session_active",
            "status",
            postgresql_where=text("status IN ('starting', 'running')"),
        ),
    )

    id: str = Field(default_factory=_sandbox_id, primary_key=True)
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
    ref: str = Field(sa_column=Column(String(160), nullable=False))
    head_sha: str = Field(
        default="",
        sa_column=Column(String(64), nullable=False, server_default=""),
    )
    image: str = Field(
        default="python:3.12-slim",
        sa_column=Column(
            String(160), nullable=False, server_default="python:3.12-slim"
        ),
    )
    setup_command: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    command: str = Field(sa_column=Column(Text, nullable=False))
    artifact_globs: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    status: SandboxStatus = Field(
        default=SandboxStatus.STARTING,
        sa_column=Column(
            String(32), nullable=False, server_default=SandboxStatus.STARTING.value
        ),
    )
    exit_code: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    as_root: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    container_id: str | None = Field(
        default=None, sa_column=Column(String(120), nullable=True)
    )
    compute_backend_id: str | None = Field(
        default=None,
        sa_column=Column(
            String, ForeignKey("backend.id", ondelete="SET NULL"), nullable=True
        ),
    )
    artifacts: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    log_tail: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    started_by: str | None = Field(
        default=None,
        sa_column=Column(
            String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True
        ),
    )
    started_at: datetime | None = Field(
        default=None,
        sa_column=Column(
            DateTime(timezone=True), nullable=False, server_default=text("now()")
        ),
    )
    expires_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False),
    )
    stopped_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    # `job` = non-interactive run/test with pattern-based artifacts;
    # `terminal` = interactive shell session (PTY over WebSocket, explicit artifacts only)
    kind: str = Field(
        default="job",
        sa_column=Column(String(16), nullable=False, server_default="job"),
    )
    title: str | None = Field(default=None, sa_column=Column(String(160), nullable=True))
    last_activity_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=True),
    )
    pushed_seed_id: str | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )

