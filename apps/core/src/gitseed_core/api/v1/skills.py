from fastapi import APIRouter, status
from gitseed_common.schemas.skill import (
    CreateSkillRequest,
    SkillResponse,
    UpdateSkillRequest,
)

from gitseed_core.api.deps import AdminUser, CurrentUser, DBSession
from gitseed_core.services import skills as skills_service

router = APIRouter(prefix="/skills", tags=["Skills"])


@router.get("", response_model=list[SkillResponse])
async def list_skills(
    _current_user: CurrentUser,
    session: DBSession,
    include_archived: bool = False,
) -> list[SkillResponse]:
    """List reusable Markdown skills."""
    skills = await skills_service.list_skills(session, include_archived=include_archived)
    return [SkillResponse.model_validate(s) for s in skills]


@router.post(
    "",
    response_model=SkillResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_skill(
    payload: CreateSkillRequest,
    admin: AdminUser,
    session: DBSession,
) -> SkillResponse:
    """Create a new reusable Markdown skill (admin only)."""
    skill = await skills_service.create_skill(
        session,
        admin,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        content=payload.content,
    )
    return SkillResponse.model_validate(skill)


@router.get("/{skill_id}", response_model=SkillResponse)
async def get_skill(
    skill_id: str,
    _current_user: CurrentUser,
    session: DBSession,
) -> SkillResponse:
    """Retrieve a single skill by ID or slug."""
    skill = await skills_service.get_skill(session, skill_id)
    return SkillResponse.model_validate(skill)


@router.patch("/{skill_id}", response_model=SkillResponse)
async def update_skill(
    skill_id: str,
    payload: UpdateSkillRequest,
    _admin: AdminUser,
    session: DBSession,
) -> SkillResponse:
    """Update a skill's name, slug, description, or Markdown content (admin only)."""
    skill = await skills_service.update_skill(
        session,
        skill_id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        content=payload.content,
    )
    return SkillResponse.model_validate(skill)


@router.post("/{skill_id}/archive", response_model=SkillResponse)
async def archive_skill(
    skill_id: str,
    admin: AdminUser,
    session: DBSession,
) -> SkillResponse:
    """Archive a skill if no active agent depends on it (admin only)."""
    skill = await skills_service.archive_skill(session, admin, skill_id)
    return SkillResponse.model_validate(skill)


@router.post("/{skill_id}/restore", response_model=SkillResponse)
async def restore_skill(
    skill_id: str,
    _admin: AdminUser,
    session: DBSession,
) -> SkillResponse:
    """Restore an archived skill (admin only)."""
    skill = await skills_service.restore_skill(session, skill_id)
    return SkillResponse.model_validate(skill)
