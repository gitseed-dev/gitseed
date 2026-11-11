import re
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.agent import Agent
from gitseed_core.models.skill import Skill
from gitseed_core.models.user import User
from gitseed_core.services.errors import Conflict, Invalid, NotFound

_SLUG_RE = re.compile(r"^[a-z][a-z0-9-]{1,47}$")


def _slugify_skill(name: str) -> str:
    raw = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    if not raw or not raw[0].isalpha():
        raw = f"skill-{raw}".strip("-")
    if len(raw) == 1:
        raw = f"{raw}-skill"
    return raw[:48].rstrip("-")


def _validate_slug(slug: str) -> str:
    cleaned = slug.strip().lower()
    if not _SLUG_RE.match(cleaned):
        raise Invalid(
            "Skill slug must be 2-48 characters, start with a lowercase letter, "
            "and contain only lowercase letters, digits, and hyphens"
        )
    return cleaned


async def _ensure_active_slug_available(
    session: AsyncSession, slug: str, *, exclude_skill_id: str | None = None
) -> None:
    stmt = select(Skill).where(
        Skill.slug == slug,
        col(Skill.archived_at).is_(None),
    )
    if exclude_skill_id is not None:
        stmt = stmt.where(Skill.id != exclude_skill_id)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        raise Conflict(f"A skill with slug '{slug}' already exists")


async def list_skills(session: AsyncSession, *, include_archived: bool = False) -> list[Skill]:
    """List registered skills ordered by name."""
    stmt = select(Skill)
    if not include_archived:
        stmt = stmt.where(col(Skill.archived_at).is_(None))
    stmt = stmt.order_by(col(Skill.name).asc())
    return list((await session.execute(stmt)).scalars().all())


async def get_skill(session: AsyncSession, skill_id_or_slug: str) -> Skill:
    """Resolve a skill by ID ('skl_...') or slug."""
    cleaned = skill_id_or_slug.strip()
    if cleaned.startswith("skl_"):
        skill = await session.get(Skill, cleaned)
    else:
        stmt = (
            select(Skill)
            .where(Skill.slug == cleaned.lower())
            .order_by(col(Skill.archived_at).asc().nulls_first())
        )
        skill = (await session.execute(stmt)).scalars().first()

    if skill is None:
        raise NotFound("Skill not found")
    return skill


async def create_skill(
    session: AsyncSession,
    admin: User,
    *,
    name: str,
    slug: str | None = None,
    description: str = "",
    content: str = "",
) -> Skill:
    """Create a new reusable Markdown skill."""
    resolved_slug = _validate_slug(slug if slug else _slugify_skill(name))
    await _ensure_active_slug_available(session, resolved_slug)

    now = datetime.now(UTC)
    skill = Skill(
        name=name.strip(),
        slug=resolved_slug,
        description=description.strip(),
        content=content.strip(),
        created_by=admin.id,
        created_at=now,
        updated_at=now,
    )
    session.add(skill)
    await session.commit()
    await session.refresh(skill)
    return skill


async def update_skill(
    session: AsyncSession,
    skill_id_or_slug: str,
    *,
    name: str | None = None,
    slug: str | None = None,
    description: str | None = None,
    content: str | None = None,
) -> Skill:
    """Update an existing skill."""
    skill = await get_skill(session, skill_id_or_slug)

    if name is not None:
        skill.name = name.strip()
    if slug is not None:
        new_slug = _validate_slug(slug)
        if new_slug != skill.slug:
            if skill.archived_at is None:
                await _ensure_active_slug_available(session, new_slug, exclude_skill_id=skill.id)
            skill.slug = new_slug
    if description is not None:
        skill.description = description.strip()
    if content is not None:
        skill.content = content.strip()

    skill.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(skill)
    return skill


async def archive_skill(
    session: AsyncSession,
    admin: User,
    skill_id_or_slug: str,
) -> Skill:
    """Archive a skill if no active agent references it (RESTRICT)."""
    skill = await get_skill(session, skill_id_or_slug)
    if skill.archived_at is not None:
        return skill

    active_agents = (
        (await session.execute(select(Agent).where(col(Agent.archived_at).is_(None))))
        .scalars()
        .all()
    )
    attached_handles = [
        f"@{a.handle}"
        for a in active_agents
        if a.skills and (skill.id in a.skills or skill.slug in a.skills)
    ]
    if attached_handles:
        raise Conflict(
            f"Cannot archive skill '{skill.slug}' while attached to agent(s): "
            f"{', '.join(sorted(attached_handles))}"
        )

    now = datetime.now(UTC)
    skill.archived_at = now
    skill.archived_by = admin.id
    skill.updated_at = now
    await session.commit()
    await session.refresh(skill)
    return skill


async def restore_skill(
    session: AsyncSession,
    skill_id_or_slug: str,
) -> Skill:
    """Restore an archived skill if its slug is still available."""
    skill = await get_skill(session, skill_id_or_slug)
    if skill.archived_at is None:
        return skill

    await _ensure_active_slug_available(session, skill.slug, exclude_skill_id=skill.id)

    now = datetime.now(UTC)
    skill.archived_at = None
    skill.archived_by = None
    skill.updated_at = now
    await session.commit()
    await session.refresh(skill)
    return skill
