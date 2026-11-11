from datetime import datetime
from typing import Any
from uuid import uuid4

from gitseed_common.enums import BackendHealth, BackendType
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _backend_id() -> str:
    return f"bnd_{uuid4().hex[:12]}"


class Backend(SQLModel, table=True):
    """Something agents can use: a Git host, a model endpoint, compute, or a tool server.

    Usage ("Used by N projects", "1 / 2 busy") is computed from project_backend,
    project_repository and live runs, not stored. Deregistering is blocked by the
    RESTRICT foreign keys while anything still depends on the backend.
    """

    __tablename__ = "backend"
    __table_args__ = (
        CheckConstraint(
            "type IN ('git', 'inference', 'compute', 'tools')", name="ck_backend_type"
        ),
        CheckConstraint(
            "health IN ('unknown', 'healthy', 'degraded', 'unreachable')",
            name="ck_backend_health",
        ),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_backend_capacity"),
        Index("ix_backend_type", "type"),
    )

    id: str = Field(default_factory=_backend_id, primary_key=True)
    name: str = Field(sa_column=Column(String(120), nullable=False))
    type: BackendType = Field(sa_column=Column(String, nullable=False))
    provider: str = Field(sa_column=Column(String(64), nullable=False))
    endpoint: str | None = Field(
        default=None, sa_column=Column(Text, nullable=True)
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
    )
    secrets_encrypted: bytes | None = Field(
        default=None, sa_column=Column(LargeBinary, nullable=True)
    )
    capacity: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    access_restricted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    health: BackendHealth = Field(
        default=BackendHealth.UNKNOWN,
        sa_column=Column(String, nullable=False, server_default=BackendHealth.UNKNOWN.value),
    )
    health_checked_at: datetime | None = Field(
        default=None, sa_column=Column(DateTime(timezone=True), nullable=True)
    )
    health_latency_ms: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    health_error: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
    enabled: bool = Field(
        default=True, sa_column=Column(Boolean, nullable=False, server_default=text("true"))
    )
    created_by: str | None = Field(
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
    updated_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )

