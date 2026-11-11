from fastapi import APIRouter, Query, status
from gitseed_common.enums import OrgRole, UserStatus
from gitseed_common.schemas.user import (
    InviteUserRequest,
    UpdateUserRequest,
    UserInviteCreatedResponse,
    UserResponse,
)

from gitseed_core.api.deps import AdminUser, CurrentUser, DBSession
from gitseed_core.services import users as users_service

router = APIRouter(prefix="/users", tags=["Users"])


@router.get("", response_model=list[UserResponse])
async def list_users(
    _current_user: CurrentUser,
    session: DBSession,
    status: UserStatus | None = None,
    org_role: OrgRole | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[UserResponse]:
    """List users in the organization, optionally filtered by status or org_role."""
    users = await users_service.list_users(
        session, status=status, org_role=org_role, limit=limit, offset=offset
    )
    return [UserResponse.model_validate(u) for u in users]


@router.post(
    "/invite",
    response_model=UserInviteCreatedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def invite_user(
    payload: InviteUserRequest,
    admin: AdminUser,
    session: DBSession,
) -> UserInviteCreatedResponse:
    """Invite a new user to the organization. The raw invite token and URL are returned once."""
    user, raw_token = await users_service.invite_user(
        session,
        admin,
        email=payload.email,
        name=payload.name,
        org_role=payload.org_role,
    )
    return UserInviteCreatedResponse.model_validate(user).model_copy(
        update={
            "invite_token": raw_token,
            "invite_url": users_service.build_invite_url(raw_token),
        }
    )


@router.post(
    "/{user_id}/reinvite",
    response_model=UserInviteCreatedResponse,
)
async def reinvite_user(
    user_id: str,
    admin: AdminUser,
    session: DBSession,
) -> UserInviteCreatedResponse:
    """Mint a fresh invite token and link for an invited user whose link was lost or expired."""
    user, raw_token = await users_service.reinvite_user(session, admin, user_id)
    return UserInviteCreatedResponse.model_validate(user).model_copy(
        update={
            "invite_token": raw_token,
            "invite_url": users_service.build_invite_url(raw_token),
        }
    )


@router.get("/{user_id}", response_model=UserResponse)
async def get_user(
    user_id: str,
    _current_user: CurrentUser,
    session: DBSession,
) -> UserResponse:
    """Retrieve a single user by ID."""
    user = await users_service.get_user(session, user_id)
    return UserResponse.model_validate(user)


@router.patch("/{user_id}", response_model=UserResponse)
async def update_user(
    user_id: str,
    payload: UpdateUserRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> UserResponse:
    """Update a user's profile, preferences (self or admin), or org_role (admin only)."""
    user = await users_service.update_user(
        session,
        current_user,
        user_id,
        name=payload.name,
        preferred_name=payload.preferred_name,
        org_role=payload.org_role,
        preferences=payload.preferences,
        fields_set=payload.model_fields_set,
    )
    return UserResponse.model_validate(user)


@router.post("/{user_id}/suspend", response_model=UserResponse)
async def suspend_user(
    user_id: str,
    admin: AdminUser,
    session: DBSession,
) -> UserResponse:
    """Suspend an active user account and revoke their active personal access tokens."""
    user = await users_service.suspend_user(session, admin, user_id)
    return UserResponse.model_validate(user)


@router.post("/{user_id}/reactivate", response_model=UserResponse)
async def reactivate_user(
    user_id: str,
    admin: AdminUser,
    session: DBSession,
) -> UserResponse:
    """Reactivate a suspended user account."""
    user = await users_service.reactivate_user(session, admin, user_id)
    return UserResponse.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_invited_user(
    user_id: str,
    admin: AdminUser,
    session: DBSession,
) -> None:
    """Revoke and delete a pending invited user who has not yet accepted their invite."""
    await users_service.delete_invited_user(session, admin, user_id)

