from datetime import datetime
from decimal import Decimal

from gitseed_common.enums import QueueStrategy, SeedAutonomy
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    text,
)
from sqlmodel import Field, SQLModel


class OrgSettings(SQLModel, table=True):
    """Instance-wide defaults. Exactly one row (id = 1), created by bootstrap."""

    __tablename__ = "org_settings"
    __table_args__ = (
        CheckConstraint("id = 1", name="ck_org_settings_singleton"),
        CheckConstraint(
            "default_autonomy IN ('manual', 'supervised', 'autonomous')",
            name="ck_org_settings_autonomy",
        ),
        CheckConstraint(
            "queue_strategy IN ('fifo', 'fair_share')",
            name="ck_org_settings_queue",
        ),
        CheckConstraint("default_monthly_budget >= 0", name="ck_org_settings_monthly_budget"),
        CheckConstraint(
            "default_seed_budget_cap IS NULL OR default_seed_budget_cap > 0",
            name="ck_org_settings_seed_cap",
        ),
        CheckConstraint(
            "default_spend_checkpoint IS NULL OR default_spend_checkpoint > 0",
            name="ck_org_settings_checkpoint",
        ),
        CheckConstraint("max_concurrent_runners >= 1", name="ck_org_settings_runners"),
        CheckConstraint("max_sandbox_sessions >= 1", name="ck_org_settings_sandboxes"),
        CheckConstraint(
            "default_project_concurrency >= 0", name="ck_org_settings_project_concurrency"
        ),
        CheckConstraint(
            "pat_lifetime_days BETWEEN 30 AND 365", name="ck_org_settings_pat_lifetime"
        ),
        CheckConstraint(
            "default_theme IN ('dark', 'light', 'system')",
            name="ck_org_settings_default_theme",
        ),
    )

    id: int = Field(default=1, sa_column=Column(Integer, primary_key=True))
    default_autonomy: SeedAutonomy = Field(
        default=SeedAutonomy.SUPERVISED,
        sa_column=Column(String, nullable=False, server_default="supervised"),
    )
    default_monthly_budget: Decimal = Field(
        default=Decimal("100"),
        sa_column=Column(Numeric(12, 2), nullable=False, server_default=text("100")),
    )
    default_seed_budget_cap: Decimal | None = Field(
        default=Decimal("15"),
        sa_column=Column(Numeric(12, 2), nullable=True, server_default=text("15")),
    )
    default_spend_checkpoint: Decimal | None = Field(
        default=Decimal("50"),
        sa_column=Column(Numeric(12, 2), nullable=True, server_default=text("50")),
    )
    max_concurrent_runners: int = Field(
        default=2, sa_column=Column(Integer, nullable=False, server_default=text("2"))
    )
    max_sandbox_sessions: int = Field(
        default=2, sa_column=Column(Integer, nullable=False, server_default=text("2"))
    )
    default_project_concurrency: int = Field(
        default=0, sa_column=Column(Integer, nullable=False, server_default=text("0"))
    )  # 0 = fair share
    queue_strategy: QueueStrategy = Field(
        default=QueueStrategy.FIFO,
        sa_column=Column(String, nullable=False, server_default="fifo"),
    )
    pat_lifetime_days: int = Field(
        default=365, sa_column=Column(Integer, nullable=False, server_default=text("365"))
    )
    default_theme: str = Field(
        default="system",
        sa_column=Column(String(16), nullable=False, server_default="system"),
    )
    default_ambient_bg: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    default_show_guide: bool = Field(
        default=True,
        sa_column=Column(Boolean, nullable=False, server_default=text("true")),
    )
    default_stage_seeds: bool = Field(
        default=False,
        sa_column=Column(Boolean, nullable=False, server_default=text("false")),
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

