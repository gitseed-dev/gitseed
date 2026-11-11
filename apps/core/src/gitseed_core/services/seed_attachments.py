"""Seed attachments: files given to a seed as reference material for its stage agents."""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, func, select

from gitseed_core.config import settings
from gitseed_core.models.seed_attachment import SeedAttachment
from gitseed_core.models.user import User
from gitseed_core.services.errors import Invalid, NotFound
from gitseed_core.services.seeds import _require_seed_developer, get_seed

MAX_FILE_BYTES = 10 * 1024 * 1024
MAX_SEED_BYTES = 25 * 1024 * 1024
MAX_FILES = 10


def _seed_dir(seed_id: str) -> Path:
    return Path(settings.ARTIFACTS_DIR) / "seed-attachments" / seed_id


def attachment_path(att: SeedAttachment) -> Path:
    return _seed_dir(att.seed_id) / att.id


def safe_filename(name: str) -> str:
    """Basename only, no path tricks or control characters; keeps the extension."""
    base = Path((name or "").replace("\\", "/")).name.strip()
    base = re.sub(r"[\x00-\x1f\x7f]", "", base)
    base = re.sub(r"[^\w.\- ()\[\]+@]", "_", base).strip(". ") or "file"
    return base[:200]


def serialize(att: SeedAttachment) -> dict[str, Any]:
    return {
        "id": att.id,
        "seed_id": att.seed_id,
        "filename": att.filename,
        "content_type": att.content_type,
        "size": att.size,
        "sha256": att.sha256,
        "created_by": att.created_by,
        "created_at": att.created_at,
    }


async def list_attachments_for_seed(session: AsyncSession, seed_id: str) -> list[SeedAttachment]:
    rows = (
        await session.execute(
            select(SeedAttachment)
            .where(SeedAttachment.seed_id == seed_id)
            .order_by(col(SeedAttachment.created_at).asc())
        )
    ).scalars().all()
    return list(rows)


async def list_attachments(session: AsyncSession, actor: User, seed_id: str) -> list[SeedAttachment]:
    seed = await get_seed(session, actor, seed_id)
    return await list_attachments_for_seed(session, seed.id)


async def add_attachment(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    filename: str,
    content_type: str | None,
    data: bytes,
) -> SeedAttachment:
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    if not data:
        raise Invalid("The file is empty")
    if len(data) > MAX_FILE_BYTES:
        raise Invalid(f"Files can be at most {MAX_FILE_BYTES // (1024 * 1024)} MB")

    count, total = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(SeedAttachment.size), 0)).where(
                SeedAttachment.seed_id == seed.id
            )
        )
    ).one()
    if int(count) >= MAX_FILES:
        raise Invalid(f"A seed can have at most {MAX_FILES} files")
    if int(total) + len(data) > MAX_SEED_BYTES:
        raise Invalid(f"A seed's files can total at most {MAX_SEED_BYTES // (1024 * 1024)} MB")

    att = SeedAttachment(
        seed_id=seed.id,
        filename=safe_filename(filename),
        content_type=(content_type or "application/octet-stream")[:160],
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        created_by=actor.id,
        created_at=datetime.now(UTC),
    )
    path = attachment_path(att)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    session.add(att)
    try:
        await session.commit()
    except Exception:
        path.unlink(missing_ok=True)
        raise
    await session.refresh(att)
    return att


async def get_attachment(
    session: AsyncSession, actor: User, seed_id: str, attachment_id: str
) -> tuple[SeedAttachment, Path]:
    seed = await get_seed(session, actor, seed_id)
    att = await session.get(SeedAttachment, attachment_id)
    if att is None or att.seed_id != seed.id:
        raise NotFound("Attachment not found")
    path = attachment_path(att)
    if not path.is_file():
        raise NotFound("Attachment file is missing on the server")
    return att, path


async def delete_attachment(session: AsyncSession, actor: User, seed_id: str, attachment_id: str) -> None:
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    att = await session.get(SeedAttachment, attachment_id)
    if att is None or att.seed_id != seed.id:
        raise NotFound("Attachment not found")
    attachment_path(att).unlink(missing_ok=True)
    await session.delete(att)
    await session.commit()


def remove_seed_attachment_files(seed_id: str) -> None:
    """Delete all stored bytes for a seed (rows go with the seed via ON DELETE CASCADE)."""
    shutil.rmtree(_seed_dir(seed_id), ignore_errors=True)

