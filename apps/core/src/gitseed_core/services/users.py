from datetime import UTC, datetime, timedelta
from typing import Any

from gitseed_common.enums import ActivityType, ActorType, OrgRole, UserStatus
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.user import PersonalAccessToken, User
from gitseed_core.security import (
    create_session,
    generate_invite_token,
    hash_password,
    hash_token,
    revoke_user_sessions,
)
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Conflict, Forbidden, Invalid, NotFound

_INVITE_TTL_DAYS = 7


def build_invite_url(raw_token: str) -> str:
    """Build the full web URL for accepting an invitation."""
    base = settings.PUBLIC_WEB_URL.rstrip("/")
    return f"{base}/accept-invite?token={raw_token}"


async def _count_active_admins(session: AsyncSession) -> int:
    stmt = (
        select(func.count())
        .select_from(User)
        .where(User.org_role == OrgRole.ADMIN, User.status == UserStatus.ACTIVE)
    )
    return int(await session.scalar(stmt) or 0)


async def list_users(
    session: AsyncSession,
    *,
    status: UserStatus | None = None,
    org_role: OrgRole | None = None,
    limit: int = 200,
    offset: int = 0,
) -> list[User]:
    """List users in the organization, ordered by creation time."""
    if limit < 1 or limit > 500:
        raise Invalid("Limit must be between 1 and 500")
    if offset < 0:
        raise Invalid("Offset must be non-negative")
    stmt = select(User)
    if status is not None:
        stmt = stmt.where(User.status == status)
    if org_role is not None:
        stmt = stmt.where(User.org_role == org_role)
    stmt = stmt.order_by(col(User.created_at).asc()).offset(offset).limit(limit)
    return list((await session.execute(stmt)).scalars().all())


async def get_user(session: AsyncSession, user_id: str) -> User:
    """Return a single user by ID or raise NotFound."""
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("User not found")
    return user


async def invite_user(
    session: AsyncSession,
    actor: User,
    *,
    email: str,
    name: str,
    org_role: OrgRole = OrgRole.MEMBER,
) -> tuple[User, str]:
    """Create an invited user and record user.invited activity."""
    normalized_email = email.strip().lower()
    existing = (
        await session.execute(
            select(User).where(func.lower(User.email) == normalized_email)
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise Conflict("A user with this email already exists")

    raw_token, token_hash = generate_invite_token()
    now = datetime.now(UTC)
    user = User(
        email=normalized_email,
        name=name.strip(),
        org_role=org_role,
        status=UserStatus.INVITED,
        invite_token_hash=token_hash,
        invite_expires_at=now + timedelta(days=_INVITE_TTL_DAYS),
        created_by=actor.id,
        created_at=now,
        updated_at=now,
    )
    session.add(user)
    await session.flush()

    record(
        session,
        type=ActivityType.USER_INVITED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        data={"user_id": user.id, "email": user.email, "org_role": str(user.org_role)},
    )
    await session.commit()
    await session.refresh(user)
    return user, raw_token


async def reinvite_user(
    session: AsyncSession,
    actor: User,
    user_id: str,
) -> tuple[User, str]:
    """Mint a fresh invite token for an INVITED user and extend expiry."""
    user = await get_user(session, user_id)
    if user.status != UserStatus.INVITED:
        raise Invalid("Only invited users can be reinvited")

    raw_token, token_hash = generate_invite_token()
    now = datetime.now(UTC)
    user.invite_token_hash = token_hash
    user.invite_expires_at = now + timedelta(days=_INVITE_TTL_DAYS)
    user.updated_at = now

    record(
        session,
        type=ActivityType.USER_INVITED,
        actor_type=ActorType.USER,
        actor_id=actor.id,
        data={
            "user_id": user.id,
            "email": user.email,
            "org_role": str(user.org_role),
            "reinvite": True,
        },
    )
    await session.commit()
    await session.refresh(user)
    return user, raw_token


async def get_invite_by_token(
    session: AsyncSession,
    *,
    token: str,
) -> User:
    """Resolve and validate an active invite token so the UI can preview the invite."""
    now = datetime.now(UTC)
    token_hash = hash_token(token.strip())
    stmt = select(User).where(User.invite_token_hash == token_hash)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if (
        user is None
        or user.status != UserStatus.INVITED
        or user.invite_expires_at is None
        or user.invite_expires_at <= now
    ):
        raise NotFound("Invite token is invalid or has expired")

    return user


async def accept_invite(
    session: AsyncSession,
    *,
    token: str,
    password: str,
    preferred_name: str | None = None,
) -> tuple[User, str]:
    """Accept an invite token, set password, activate account, and start a web session."""
    now = datetime.now(UTC)
    token_hash = hash_token(token.strip())
    stmt = select(User).where(User.invite_token_hash == token_hash)
    user = (await session.execute(stmt)).scalar_one_or_none()

    if (
        user is None
        or user.status != UserStatus.INVITED
        or user.invite_expires_at is None
        or user.invite_expires_at <= now
    ):
        raise Invalid("Invite token is invalid or has expired")

    user.password_hash = hash_password(password)
    user.status = UserStatus.ACTIVE
    user.invite_token_hash = None
    user.invite_expires_at = None
    if preferred_name is not None:
        user.preferred_name = preferred_name.strip() or None
    user.last_active_at = now
    user.updated_at = now

    record(
        session,
        type=ActivityType.USER_ACTIVATED,
        actor_type=ActorType.USER,
        actor_id=user.id,
        data={"user_id": user.id, "email": user.email},
    )
    session_id = await create_session(user.id)
    await session.commit()
    await session.refresh(user)
    return user, session_id


async def update_user(
    session: AsyncSession,
    actor: User,
    user_id: str,
    *,
    name: str | None = None,
    preferred_name: str | None = None,
    org_role: OrgRole | None = None,
    preferences: dict[str, Any] | None = None,
    fields_set: set[str] | None = None,
) -> User:
    """Update a user's profile, preferences, or organization role."""
    user = await get_user(session, user_id)
    is_self = actor.id == user.id
    is_admin = actor.org_role == OrgRole.ADMIN

    if not is_self and not is_admin:
        raise Forbidden("You can only update your own profile")

    if org_role is not None and org_role != user.org_role:
        if not is_admin:
            raise Forbidden("Only organization admins can change organization roles")
        if is_self:
            raise Invalid("You cannot change your own organization role")
        if user.status == UserStatus.SUSPENDED:
            raise Invalid("Cannot change organization role while user is suspended")
        if (
            user.org_role == OrgRole.ADMIN
            and org_role != OrgRole.ADMIN
            and user.status == UserStatus.ACTIVE
        ):
            if await _count_active_admins(session) <= 1:
                raise Invalid("Cannot remove admin role from the last active admin")
        user.org_role = org_role

    if name is not None:
        user.name = name.strip()
    if fields_set and "preferred_name" in fields_set:
        user.preferred_name = preferred_name.strip() if preferred_name else None
    elif preferred_name is not None:
        user.preferred_name = preferred_name.strip() or None
    if preferences is not None:
        user.preferences = preferences

    user.updated_at = datetime.now(UTC)
    await session.commit()
    await session.refresh(user)
    return user


async def suspend_user(
    session: AsyncSession,
    admin: User,
    user_id: str,
) -> User:
    """Suspend an active user, revoke their active PATs, and record user.suspended."""
    user = await get_user(session, user_id)
    if user.id == admin.id:
        raise Invalid("Cannot suspend your own account")
    if user.status == UserStatus.SUSPENDED:
        return user
    if user.status == UserStatus.INVITED:
        raise Invalid("Cannot suspend a user who has not accepted their invite yet")
    if user.org_role == OrgRole.ADMIN and await _count_active_admins(session) <= 1:
        raise Invalid("Cannot suspend the last active organization admin")

    now = datetime.now(UTC)
    user.status = UserStatus.SUSPENDED
    user.suspended_at = now
    user.suspended_by = admin.id
    user.updated_at = now

    # Revoke any active PATs belonging to this user
    pats = (
        await session.execute(
            select(PersonalAccessToken).where(
                PersonalAccessToken.user_id == user.id,
                col(PersonalAccessToken.revoked_at).is_(None),
            )
        )
    ).scalars().all()
    for pat in pats:
        pat.revoked_at = now
        pat.revoked_by = admin.id

    record(
        session,
        type=ActivityType.USER_SUSPENDED,
        actor_type=ActorType.USER,
        actor_id=admin.id,
        data={"user_id": user.id, "email": user.email},
    )
    await session.commit()
    await session.refresh(user)
    await revoke_user_sessions(user.id)
    return user


async def reactivate_user(
    session: AsyncSession,
    admin: User,
    user_id: str,
) -> User:
    """Reactivate a suspended user and record user.reactivated."""
    user = await get_user(session, user_id)
    if user.status != UserStatus.SUSPENDED:
        raise Invalid("User is not suspended")

    now = datetime.now(UTC)
    user.status = UserStatus.ACTIVE
    user.suspended_at = None
    user.suspended_by = None
    user.updated_at = now

    record(
        session,
        type=ActivityType.USER_REACTIVATED,
        actor_type=ActorType.USER,
        actor_id=admin.id,
        data={"user_id": user.id, "email": user.email},
    )
    await session.commit()
    await session.refresh(user)
    return user


async def delete_invited_user(
    session: AsyncSession,
    admin: User,
    user_id: str,
) -> None:
    """Revoke and delete a pending invited user who has not yet activated their account."""
    user = await get_user(session, user_id)
    if user.id == admin.id:
        raise Invalid("Cannot delete your own account")
    if user.status != UserStatus.INVITED:
        raise Invalid(
            "Only pending invited users can be removed; suspend active users instead"
        )

    await session.delete(user)
    await session.commit()


