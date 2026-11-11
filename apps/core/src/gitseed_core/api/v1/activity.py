from fastapi import APIRouter, Query
from gitseed_common.enums import ActivityType, ActorType
from gitseed_common.schemas.activity import ActivityResponse

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.services import activity as activity_service

router = APIRouter(prefix="/activity", tags=["Activity"])


@router.get("", response_model=list[ActivityResponse])
async def list_activity(
    current_user: CurrentUser,
    session: DBSession,
    project_id: str | None = None,
    seed_id: str | None = None,
    actor_type: ActorType | None = None,
    actor_id: str | None = None,
    type: ActivityType | None = None,
    before_id: int | None = None,
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ActivityResponse]:
    """List activity events visible to the current user, newest first."""
    events = await activity_service.list_activity(
        session,
        current_user,
        project_id=project_id,
        seed_id=seed_id,
        actor_type=actor_type,
        actor_id=actor_id,
        type=type,
        before_id=before_id,
        limit=limit,
        offset=offset,
    )
    results: list[ActivityResponse] = []
    for e in events:
        try:
            if getattr(e, "data", None) is None:
                e.data = {}
            results.append(ActivityResponse.model_validate(e))
        except Exception:
            continue
    return results
