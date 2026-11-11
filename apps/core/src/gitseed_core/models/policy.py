from datetime import datetime
from uuid import uuid4

from gitseed_common.enums import ProjectRole, SeedStage
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
from sqlmodel import Field, SQLModel


def _policy_id() -> str:
    return f"pol_{uuid4().hex[:12]}"


class Policy(SQLModel, table=True):
    """The rules for one stage: which agent runs it and who must approve it.

    One org row per stage (project_id IS NULL) holds the defaults, and every field
    is set. A project row overrides some of them: None means "inherit the org row".
    Execute and Merge can't be turned off; the other stages can.

    A gate passes when min_approvers distinct people, each at least min_approver_role,
    have approved the current snapshot since the last changes_requested. The seed's
    creator counts only if allow_self_approval is set.
    """

    __tablename__ = "policy"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('ideate', 'plan', 'execute', 'review', 'merge')",
            name="ck_policy_stage",
        ),
        CheckConstraint(
            "min_approver_role IS NULL OR min_approver_role IN ('maintainer', 'developer')",
            name="ck_policy_min_approver_role",
        ),
        CheckConstraint(
            "min_approvers IS NULL OR min_approvers BETWEEN 1 AND 5",
            name="ck_policy_min_approvers",
        ),
        CheckConstraint(
            "stage NOT IN ('execute', 'merge') OR enabled IS DISTINCT FROM false",
            name="ck_policy_required_stages",
        ),
        CheckConstraint(
            "stage NOT IN ('execute', 'merge') OR allow_second_opinions IS NULL OR allow_second_opinions = false",
            name="ck_policy_second_opinions_stage",
        ),
        CheckConstraint(
            "max_attempts IS NULL OR max_attempts BETWEEN 1 AND 10",
            name="ck_policy_max_attempts",
        ),
        CheckConstraint(
            "timeout_minutes IS NULL OR timeout_minutes BETWEEN 1 AND 1440",
            name="ck_policy_timeout",
        ),
        CheckConstraint(
            "(min_acceptance_criteria IS NULL AND max_acceptance_criteria IS NULL) OR stage = 'ideate'",
            name="ck_policy_ideate_criteria_stage",
        ),
        CheckConstraint(
            "(min_acceptance_criteria IS NULL OR min_acceptance_criteria BETWEEN 1 AND 20) "
            "AND (max_acceptance_criteria IS NULL OR max_acceptance_criteria BETWEEN 1 AND 20) "
            "AND (min_acceptance_criteria IS NULL OR max_acceptance_criteria IS NULL "
            "OR min_acceptance_criteria <= max_acceptance_criteria)",
            name="ck_policy_ideate_criteria_bounds",
        ),
        CheckConstraint(
            "max_tasks IS NULL OR (stage = 'plan' AND max_tasks BETWEEN 1 AND 20)",
            name="ck_policy_plan_max_tasks",
        ),
        CheckConstraint(
            "project_id IS NOT NULL OR ("
            "enabled IS NOT NULL AND requires_approval IS NOT NULL "
            "AND min_approvers IS NOT NULL AND min_approver_role IS NOT NULL "
            "AND allow_auto_approve IS NOT NULL AND allow_self_approval IS NOT NULL "
            "AND max_attempts IS NOT NULL AND timeout_minutes IS NOT NULL)",
            name="ck_policy_org_complete",
        ),
        Index(
            "ux_policy_org_stage",
            "stage",
            unique=True,
            postgresql_where=text("project_id IS NULL"),
        ),
        Index(
            "ux_policy_project_stage",
            "project_id",
            "stage",
            unique=True,
            postgresql_where=text("project_id IS NOT NULL"),
        ),
        Index("ix_policy_agent", "agent_id", postgresql_where=text("agent_id IS NOT NULL")),
    )

    id: str = Field(default_factory=_policy_id, primary_key=True)
    project_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("project.id", ondelete="CASCADE"), nullable=True),
    )
    stage: SeedStage = Field(sa_column=Column(String, nullable=False))
    enabled: bool | None = Field(default=None, sa_column=Column(Boolean, nullable=True))
    agent_id: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("agent.id", ondelete="RESTRICT"), nullable=True),
    )
    requires_approval: bool | None = Field(default=None, sa_column=Column(Boolean, nullable=True))
    min_approvers: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    min_approver_role: ProjectRole | None = Field(
        default=None, sa_column=Column(String, nullable=True)
    )
    allow_auto_approve: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    allow_self_approval: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    allow_second_opinions: bool | None = Field(
        default=None, sa_column=Column(Boolean, nullable=True)
    )
    max_attempts: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    timeout_minutes: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    min_acceptance_criteria: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    max_acceptance_criteria: int | None = Field(
        default=None, sa_column=Column(Integer, nullable=True)
    )
    max_tasks: int | None = Field(default=None, sa_column=Column(Integer, nullable=True))
    # Extra guidance for the stage agent (Markdown). Additive: a project's instructions are
    # appended after the org's, never replace them, so it isn't an "override" field.
    instructions: str | None = Field(default=None, sa_column=Column(Text, nullable=True))
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
    updated_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
