from datetime import datetime
from typing import Literal

from fastapi import APIRouter, status
from pydantic import BaseModel

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.services import seed_links as links_service

router = APIRouter(prefix="/seeds", tags=["Seed links"])


class SeedLinkCreateRequest(BaseModel):
    target_seed_id: str
    kind: Literal["related", "follow_up"] = "related"


class SeedLinkItemResponse(BaseModel):
    id: str
    seed_id: str
    number: int
    title: str
    status: str
    stage: str
    relation: Literal["related", "follow_up_of", "followed_up_by"]
    created_by: str | None = None
    created_at: datetime | None = None


@router.get("/{seed_id}/links", response_model=list[SeedLinkItemResponse])
async def list_seed_links(
    seed_id: str, current_user: CurrentUser, session: DBSession
) -> list[SeedLinkItemResponse]:
    """Seeds linked to this seed (related or follow-up in either direction)."""
    items = await links_service.list_links(session, current_user, seed_id)
    return [SeedLinkItemResponse(**it) for it in items]


@router.post(
    "/{seed_id}/links",
    response_model=SeedLinkItemResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_seed_link(
    seed_id: str,
    payload: SeedLinkCreateRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedLinkItemResponse:
    """Link `seed_id` to `target_seed_id` in the same project."""
    item = await links_service.add_link(
        session,
        current_user,
        seed_id,
        target_seed_id=payload.target_seed_id,
        kind=payload.kind,
    )
    return SeedLinkItemResponse(**item)


@router.delete("/{seed_id}/links/{link_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_seed_link(
    seed_id: str, link_id: str, current_user: CurrentUser, session: DBSession
) -> None:
    await links_service.delete_link(session, current_user, seed_id, link_id)

