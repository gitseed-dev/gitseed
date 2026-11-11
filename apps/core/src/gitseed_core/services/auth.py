from datetime import UTC, datetime, timedelta

from gitseed_common.enums import ActivityType, ActorType, UserStatus
from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import PersonalAccessToken, User
from gitseed_core.security import (
    clear_failed_logins,
    create_session,
    delete_session,
    generate_pat,
    hash_password,
    hash_token,
    is_login_rate_limited,
    record_failed_login,
    resolve_session,
    revoke_user_sessions,
    verify_password,
)
from gitseed_core.services.activity import record
from gitseed_core.services.errors import (
    Forbidden,
    Invalid,
    NotFound,
    RateLimited,
    Unauthorized,
)

# Pre-computed Argon2 hash used to equalize login timing when a user is not found
_DUMMY_PASSWORD_HASH = hash_password("gitseed-timing-equalizer-dummy-password")


async def authenticate(
    session: AsyncSession,
    email: str,
    password: str,
    *,
    client_ip: str | None = None,
) -> tuple[User, str]:
    """Verify email and password, enforce login rate limits, and create a Valkey session."""
    if await is_login_rate_limited(email, client_ip):
        raise RateLimited(
            "Too many failed login attempts. Please try again in a few minutes."
        )

    stmt = select(User).where(func.lower(User.email) == email.strip().lower())
    user = (await session.execute(stmt)).scalar_one_or_none()

    if user is None or user.password_hash is None:
        verify_password(password, _DUMMY_PASSWORD_HASH)
        await record_failed_login(email, client_ip)
        raise Unauthorized("Invalid email or password")

    if not verify_password(password, user.password_hash):
        await record_failed_login(email, client_ip)
        raise Unauthorized("Invalid email or password")

    if user.status != UserStatus.ACTIVE:
        raise Forbidden(f"User account is {str(user.status)}")

    await clear_failed_logins(email, client_ip)
    session_id = await create_session(user.id)
    user.last_active_at = datetime.now(UTC)
    await session.commit()
    return user, session_id


async def change_password(
    session: AsyncSession,
    user: User,
    *,
    current_password: str,
    new_password: str,
    current_session_id: str | None = None,
) -> None:
    """Verify current password, set new password, and revoke all other active sessions."""
    if not user.password_hash or not verify_password(
        current_password, user.password_hash
    ):
        raise Unauthorized("Current password is incorrect")

    now = datetime.now(UTC)
    user.password_hash = hash_password(new_password)
    user.updated_at = now
    await session.commit()
    await revoke_user_sessions(user.id, except_session_id=current_session_id)


async def end_session(session_id: str | None) -> None:
    """Delete a web session from Valkey if present."""
    if session_id:
        await delete_session(session_id)


async def authenticate_token(session: AsyncSession, raw_token: str) -> User:
    """Authenticate a Personal Access Token and return its active User."""
    now = datetime.now(UTC)
    token_digest = hash_token(raw_token.strip())
    stmt = (
        select(PersonalAccessToken, User)
        .join(User, col(PersonalAccessToken.user_id) == col(User.id))
        .where(PersonalAccessToken.token_hash == token_digest)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        raise Unauthorized("Invalid personal access token")

    pat, user = row
    if pat.revoked_at is not None or pat.expires_at <= now:
        raise Unauthorized("Personal access token is expired or revoked")
    if user.status != UserStatus.ACTIVE:
        raise Forbidden("User account is not active")

    pat.last_used_at = now
    user.last_active_at = now
    await session.commit()
    return user


async def authenticate_session_cookie(session: AsyncSession, session_id: str) -> User | None:
    """Resolve a Valkey session ID to an active User, or None if invalid/expired."""
    user_id = await resolve_session(session_id)
    if not user_id:
        return None
    user = await session.get(User, user_id)
    if user is None or user.status != UserStatus.ACTIVE:
        return None
    user.last_active_at = datetime.now(UTC)
    await session.commit()
    return user


async def list_tokens(session: AsyncSession, user: User) -> list[PersonalAccessToken]:
    """List personal access tokens belonging to a user, newest first."""
    stmt = (
        select(PersonalAccessToken)
        .where(PersonalAccessToken.user_id == user.id)
        .order_by(col(PersonalAccessToken.created_at).desc())
    )
    return list((await session.execute(stmt)).scalars().all())


async def create_token(
    session: AsyncSession,
    user: User,
    name: str,
    expires_in_days: int | None,
) -> tuple[PersonalAccessToken, str]:
    """Create a Personal Access Token capped by org pat_lifetime_days and record activity."""
    org = await session.get(OrgSettings, 1)
    max_days = org.pat_lifetime_days if org is not None else 365
    days = expires_in_days if expires_in_days is not None else min(30, max_days)
    if days > max_days:
        raise Invalid(f"Token lifetime can't exceed {max_days} days")

    raw, prefix, token_hash = generate_pat()
    now = datetime.now(UTC)
    pat = PersonalAccessToken(
        user_id=user.id,
        name=name.strip(),
        token_prefix=prefix,
        token_hash=token_hash,
        expires_at=now + timedelta(days=days),
        created_at=now,
    )
    session.add(pat)
    await session.flush()
    record(
        session,
        type=ActivityType.PAT_CREATED,
        actor_type=ActorType.USER,
        actor_id=user.id,
        data={"token_id": pat.id, "name": pat.name},
    )
    await session.commit()
    await session.refresh(pat)
    return pat, raw


async def revoke_token(
    session: AsyncSession,
    user: User,
    token_id: str,
) -> None:
    """Revoke a Personal Access Token owned by the user and record activity."""
    pat = await session.get(PersonalAccessToken, token_id)
    if pat is None or pat.user_id != user.id:
        raise NotFound("Personal access token not found")

    if pat.revoked_at is None:
        pat.revoked_at = datetime.now(UTC)
        pat.revoked_by = user.id
        record(
            session,
            type=ActivityType.PAT_REVOKED,
            actor_type=ActorType.USER,
            actor_id=user.id,
            data={"token_id": pat.id, "name": pat.name},
        )
        await session.commit()

