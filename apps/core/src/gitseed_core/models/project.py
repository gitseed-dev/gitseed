from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from gitseed_common.enums import ProjectRole, ProjectVisibility
from sqlalchemy import (
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
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlmodel import Field, SQLModel


def _project_id() -> str:
    return f"prj_{uuid4().hex[:12]}"


class Project(SQLModel, table=True):
    """Groups seeds with the repositories they deliver into and the rules they run under.

    Budget overrides use None to mean "inherit the org default". Backend access uses
    backends_restricted: False = all org backends, True = only those in project_backend.
    Stage gates and approvers are overridden in the policy table (policy.project_id),
    and members live in project_member.
    """

    __tablename__ = "project"
    __table_args__ = (
        CheckConstraint("visibility IN ('private', 'public')", name="ck_project_visibility"),
        CheckConstraint(
            "slug ~ '^[a-z0-9][a-z0-9-]{1,62}$'", name="ck_project_slug_format"
        ),
        CheckConstraint(
            "monthly_budget IS NULL OR monthly_budget >= 0", name="ck_project_monthly_budget"
        ),
        CheckConstraint(
            "seed_budget_cap IS NULL OR seed_budget_cap > 0", name="ck_project_seed_cap"
        ),
        CheckConstraint(
            "spend_checkpoint IS NULL OR spend_checkpoint > 0", name="ck_project_checkpoint"
        ),
        CheckConstraint(
            "archived_at IS NOT NULL OR archived_by IS NULL",
            name="ck_project_archived_by",
        ),
        CheckConstraint(
            "max_concurrent_runs IS NULL OR max_concurrent_runs >= 0",
            name="ck_project_max_concurrent_runs",
        ),
        Index(
            "ux_project_slug_active",
            "slug",
            unique=True,
            postgresql_where=text("archived_at IS NULL"),
        ),
    )

    id: str = Field(default_factory=_project_id, primary_key=True)
    name: str = Field(sa_column=Column(String(120), nullable=False))
    slug: str = Field(sa_column=Column(String(64), nullable=False))
    description: str = Field(
        default="", sa_column=Column(Text, nullable=False, server_default="")
    )
    tags: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    visibility: ProjectVisibility = Field(
        default=ProjectVisibility.PRIVATE,
        sa_column=Column(
            String, nullable=False, server_default=ProjectVisibility.PRIVATE.value
        ),
    )
    seed_counter: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )
    monthly_budget: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(12, 2), nullable=True)
    )
    seed_budget_cap: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(12, 2), nullable=True)
    )
    spend_checkpoint: Decimal | None = Field(
        default=None, sa_column=Column(Numeric(12, 2), nullable=True)
    )
    max_concurrent_runs: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    backends_restricted: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
    )
    sandbox_config: dict[str, Any] = Field(
        default_factory=dict,
        sa_column=Column(JSONB, nullable=False, server_default=text("'{}'::jsonb")),
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


class ProjectMember(SQLModel, table=True):
    """A user's role on one project. This is what "Maintainer on 2 · Viewer on 1"
    and "can approve" are computed from."""

    __tablename__ = "project_member"
    __table_args__ = (
        UniqueConstraint("project_id", "user_id", name="uq_project_member"),
        CheckConstraint(
            "role IN ('maintainer', 'developer', 'viewer')", name="ck_project_member_role"
        ),
        Index("ix_project_member_user", "user_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("project.id", ondelete="CASCADE"), nullable=False)
    )
    user_id: str = Field(
        sa_column=Column(String, ForeignKey("user.id", ondelete="CASCADE"), nullable=False)
    )
    role: ProjectRole = Field(sa_column=Column(String, nullable=False))
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


class ProjectBackend(SQLModel, table=True):
    """A backend a project may use, when the project restricts its backends."""

    __tablename__ = "project_backend"
    __table_args__ = (
        Index("ix_project_backend_backend", "backend_id"),
    )

    project_id: str = Field(
        sa_column=Column(
            String, ForeignKey("project.id", ondelete="CASCADE"), primary_key=True
        )
    )
    backend_id: str = Field(
        sa_column=Column(
            String, ForeignKey("backend.id", ondelete="RESTRICT"), primary_key=True
        )
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )
    created_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )


class ProjectRepository(SQLModel, table=True):
    """A repository a project delivers into. One is primary."""

    __tablename__ = "project_repository"
    __table_args__ = (
        UniqueConstraint("project_id", "backend_id", "full_name", name="uq_project_repository"),
        Index("ix_project_repository_backend", "backend_id"),
        Index(
            "ux_project_repository_primary",
            "project_id",
            unique=True,
            postgresql_where=text("is_primary"),
        ),
    )

    id: int | None = Field(default=None, primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("project.id", ondelete="CASCADE"), nullable=False)
    )
    backend_id: str = Field(
        sa_column=Column(String, ForeignKey("backend.id", ondelete="RESTRICT"), nullable=False)
    )
    full_name: str = Field(sa_column=Column(String(200), nullable=False))
    default_branch: str = Field(
        default="main", sa_column=Column(String(100), nullable=False, server_default="main")
    )
    is_primary: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
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
