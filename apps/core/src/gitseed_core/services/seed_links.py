"""Seed links: connect related or follow-up seeds within a project."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from gitseed_common.enums import ActorType
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.seed import Seed, SeedComment
from gitseed_core.models.seed_link import SeedLink
from gitseed_core.models.user import User
from gitseed_core.services.errors import Invalid, NotFound
from gitseed_core.services.seeds import _require_seed_developer, get_seed

MAX_LINKS_PER_SEED = 10
VALID_LINK_KINDS = ("related", "follow_up")


def _relation_for_seed(link: SeedLink, viewer_seed_id: str) -> str:
    if link.kind == "follow_up":
        if link.source_seed_id == viewer_seed_id:
            return "follow_up_of"
        return "followed_up_by"
    return "related"


async def list_links_for_seed(session: AsyncSession, seed_id: str) -> list[dict[str, Any]]:
    """Return all non-deleted linked seeds for `seed_id` (both directions)."""
    links = (
        await session.execute(
            select(SeedLink)
            .where(
                or_(
                    SeedLink.source_seed_id == seed_id,
                    SeedLink.target_seed_id == seed_id,
                )
            )
            .order_by(col(SeedLink.created_at).asc())
        )
    ).scalars().all()
    if not links:
        return []

    other_ids = [
        lnk.target_seed_id if lnk.source_seed_id == seed_id else lnk.source_seed_id
        for lnk in links
    ]
    seeds = (
        await session.execute(
            select(Seed).where(
                col(Seed.id).in_(other_ids),
            )
        )
    ).scalars().all()
    seed_by_id = {s.id: s for s in seeds}

    items: list[dict[str, Any]] = []
    for lnk in links:
        other_id = lnk.target_seed_id if lnk.source_seed_id == seed_id else lnk.source_seed_id
        other = seed_by_id.get(other_id)
        if other is None:
            continue
        items.append(
            {
                "id": lnk.id,
                "seed_id": other.id,
                "number": other.number,
                "title": other.title,
                "status": str(other.status),
                "stage": str(other.stage),
                "relation": _relation_for_seed(lnk, seed_id),
                "description": other.description,
                "specification": other.specification,
                "plan": other.plan,
                "created_by": lnk.created_by,
                "created_at": lnk.created_at,
            }
        )
    return items


async def list_runner_related_seeds(
    session: AsyncSession, seed_id: str, project_slug: str
) -> list[dict[str, Any]]:
    """Return linked seeds enriched with branch name and latest agent summary for the runner."""
    base_items = await list_links_for_seed(session, seed_id)
    if not base_items:
        return []
    slug = (project_slug or "").strip().strip("/")
    enriched: list[dict[str, Any]] = []
    for item in base_items:
        other_id = str(item["seed_id"])
        num = int(item["number"])
        branch_name = f"gitseed/{slug}/seed-{num}" if slug else f"gitseed/seed-{num}"
        latest_comment = (
            await session.execute(
                select(SeedComment)
                .where(
                    SeedComment.seed_id == other_id,
                    SeedComment.author_type == ActorType.AGENT,
                    col(SeedComment.deleted_at).is_(None),
                )
                .order_by(col(SeedComment.created_at).desc())
                .limit(1)
            )
        ).scalars().first()
        enriched.append(
            {
                **item,
                "branch_name": branch_name,
                "summary_comment": latest_comment.body if latest_comment else None,
            }
        )
    return enriched


async def list_links(session: AsyncSession, actor: User, seed_id: str) -> list[dict[str, Any]]:
    seed = await get_seed(session, actor, seed_id)
    return await list_links_for_seed(session, seed.id)


async def add_link(
    session: AsyncSession,
    actor: User,
    seed_id: str,
    *,
    target_seed_id: str,
    kind: str = "related",
) -> dict[str, Any]:
    norm_kind = (kind or "related").strip().lower()
    if norm_kind not in VALID_LINK_KINDS:
        raise Invalid("Link kind must be 'related' or 'follow_up'")

    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    if target_seed_id == seed.id:
        raise Invalid("A seed cannot be linked to itself")

    target = await get_seed(session, actor, target_seed_id)
    if target.project_id != seed.project_id:
        raise Invalid("Linked seeds must belong to the same project")

    existing = (
        await session.execute(
            select(SeedLink).where(
                or_(
                    (SeedLink.source_seed_id == seed.id) & (SeedLink.target_seed_id == target.id),
                    (SeedLink.source_seed_id == target.id) & (SeedLink.target_seed_id == seed.id),
                )
            )
        )
    ).scalars().first()

    if existing is not None:
        existing.source_seed_id = seed.id
        existing.target_seed_id = target.id
        existing.kind = norm_kind
        await session.commit()
        await session.refresh(existing)
        lnk = existing
    else:
        existing_links = await list_links_for_seed(session, seed.id)
        if len(existing_links) >= MAX_LINKS_PER_SEED:
            raise Invalid(f"A seed can have at most {MAX_LINKS_PER_SEED} linked seeds")
        lnk = SeedLink(
            project_id=seed.project_id,
            source_seed_id=seed.id,
            target_seed_id=target.id,
            kind=norm_kind,
            created_by=actor.id,
            created_at=datetime.now(UTC),
        )
        session.add(lnk)
        await session.commit()
        await session.refresh(lnk)

    return {
        "id": lnk.id,
        "seed_id": target.id,
        "number": target.number,
        "title": target.title,
        "status": str(target.status),
        "stage": str(target.stage),
        "relation": _relation_for_seed(lnk, seed.id),
        "description": target.description,
        "specification": target.specification,
        "plan": target.plan,
        "created_by": lnk.created_by,
        "created_at": lnk.created_at,
    }


async def delete_link(
    session: AsyncSession, actor: User, seed_id: str, link_id: str
) -> None:
    seed = await get_seed(session, actor, seed_id)
    await _require_seed_developer(session, actor, seed)
    lnk = await session.get(SeedLink, link_id)
    if lnk is None or (lnk.source_seed_id != seed.id and lnk.target_seed_id != seed.id):
        raise NotFound("Seed link not found")
    await session.delete(lnk)
    await session.commit()

