from datetime import datetime
from uuid import uuid4

from sqlalchemy import Column, DateTime, ForeignKey, Index, String, text
from sqlmodel import Field, SQLModel


def _link_id() -> str:
    return f"lnk_{uuid4().hex[:12]}"


class SeedLink(SQLModel, table=True):
    """A link between two seeds in the same project.

    - `kind="related"`: symmetric link (shown as "Related" on both seeds).
    - `kind="follow_up"`: directional link where `source_seed_id` is a follow-up of `target_seed_id`
      (shown as "Follow-up of #N" on `source_seed_id` and "Followed up by #M" on `target_seed_id`).
    """

    __tablename__ = "seed_link"
    __table_args__ = (
        Index("ix_seed_link_source", "source_seed_id"),
        Index("ix_seed_link_target", "target_seed_id"),
        Index("ix_seed_link_project", "project_id"),
    )

    id: str = Field(default_factory=_link_id, primary_key=True)
    project_id: str = Field(
        sa_column=Column(String, ForeignKey("project.id", ondelete="CASCADE"), nullable=False)
    )
    source_seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    target_seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    kind: str = Field(
        default="related",
        sa_column=Column(String(32), nullable=False, server_default="related"),
    )
    created_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )

