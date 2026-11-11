from datetime import datetime
from uuid import uuid4

from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, Index, String, text
from sqlmodel import Field, SQLModel


def _attachment_id() -> str:
    return f"att_{uuid4().hex[:12]}"


class SeedAttachment(SQLModel, table=True):
    """A file attached to a seed (mockup, spec PDF, log, screenshot…).

    Reference material for the stage agents: the runner downloads attachments into
    `/workspace/.gitseed/attachments/` (git-excluded) and the prompt lists them by path,
    so agents open them only when needed. Bytes live on disk under ARTIFACTS_DIR.
    """

    __tablename__ = "seed_attachment"
    __table_args__ = (Index("ix_seed_attachment_seed", "seed_id"),)

    id: str = Field(default_factory=_attachment_id, primary_key=True)
    seed_id: str = Field(
        sa_column=Column(String, ForeignKey("seed.id", ondelete="CASCADE"), nullable=False)
    )
    filename: str = Field(sa_column=Column(String(255), nullable=False))
    content_type: str = Field(default="application/octet-stream", sa_column=Column(String(160), nullable=False))
    size: int = Field(sa_column=Column(BigInteger, nullable=False))
    sha256: str = Field(sa_column=Column(String(64), nullable=False))
    created_by: str | None = Field(
        default=None,
        sa_column=Column(String, ForeignKey("user.id", ondelete="SET NULL"), nullable=True),
    )
    created_at: datetime | None = Field(
        default=None,
        sa_column=Column(DateTime(timezone=True), nullable=False, server_default=text("now()")),
    )

