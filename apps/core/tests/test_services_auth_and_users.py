from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from gitseed_common.enums import (
    ActivityType,
    ActorType,
    OrgRole,
    QueueStrategy,
    SeedAutonomy,
    UserStatus,
)
from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import PersonalAccessToken
from gitseed_core.security import hash_token
from gitseed_core.services import (
    activity as activity_svc,
)
from gitseed_core.services import (
    auth as auth_svc,
)
from gitseed_core.services import (
    settings as settings_svc,
)
from gitseed_core.services import (
    users as users_svc,
)
from gitseed_core.services.errors import (
    Forbidden,
    Invalid,
    NotFound,
    RateLimited,
    Unauthorized,
)

from conftest import _exec_result, _make_session, _make_user, _now


@pytest.mark.asyncio
async def test_auth_service_authenticate_and_sessions(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    user = _make_user()

    # Rate limited login
    monkeypatch.setattr(auth_svc, "is_login_rate_limited", AsyncMock(return_value=True))
    with pytest.raises(RateLimited):
        await auth_svc.authenticate(session, user.email, "ValidPass123!")

    # User not found -> Unauthorized
    monkeypatch.setattr(auth_svc, "is_login_rate_limited", AsyncMock(return_value=False))
    monkeypatch.setattr(auth_svc, "record_failed_login", AsyncMock())
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    with pytest.raises(Unauthorized):
        await auth_svc.authenticate(session, "missing@gitseed.local", "ValidPass123!")

    # Wrong password -> Unauthorized
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=user))
    with pytest.raises(Unauthorized):
        await auth_svc.authenticate(session, user.email, "WrongPass!")

    # Suspended user -> Forbidden
    suspended = _make_user("usr_sus", status=UserStatus.SUSPENDED)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=suspended))
    with pytest.raises(Forbidden):
        await auth_svc.authenticate(session, suspended.email, "ValidPass123!")

    # Successful authenticate
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=user))
    monkeypatch.setattr(auth_svc, "clear_failed_logins", AsyncMock())
    monkeypatch.setattr(auth_svc, "create_session", AsyncMock(return_value="sess_123"))
    authed_user, sess_id = await auth_svc.authenticate(
        session, user.email, "ValidPass123!", client_ip="127.0.0.1"
    )
    assert authed_user.id == user.id
    assert sess_id == "sess_123"

    # change_password
    monkeypatch.setattr(auth_svc, "revoke_user_sessions", AsyncMock())
    with pytest.raises(Unauthorized):
        await auth_svc.change_password(
            session, user, current_password="bad", new_password="NewPassword123!"
        )
    await auth_svc.change_password(
        session,
        user,
        current_password="ValidPass123!",
        new_password="NewPassword123!",
        current_session_id="sess_123",
    )

    # end_session
    del_mock = AsyncMock()
    monkeypatch.setattr(auth_svc, "delete_session", del_mock)
    await auth_svc.end_session("sess_123")
    del_mock.assert_awaited_once_with("sess_123")


@pytest.mark.asyncio
async def test_auth_service_tokens_and_cookies(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    user = _make_user()

    # authenticate_token
    now = _now()
    pat = PersonalAccessToken(
        id="pat_1",
        user_id=user.id,
        name="Test PAT",
        token_prefix="gsp_1234",
        token_hash=hash_token("gsp_raw_token"),
        expires_at=now + timedelta(days=10),
        created_at=now,
    )
    session.execute = AsyncMock(return_value=_exec_result(first=(pat, user)))
    tok_user = await auth_svc.authenticate_token(session, "gsp_raw_token")
    assert tok_user.id == user.id

    # Expired PAT
    pat_exp = PersonalAccessToken(
        id="pat_2",
        user_id=user.id,
        name="Exp",
        token_prefix="gsp_exp",
        token_hash="h",
        expires_at=now - timedelta(days=1),
        created_at=now - timedelta(days=10),
    )
    session.execute = AsyncMock(return_value=_exec_result(first=(pat_exp, user)))
    with pytest.raises(Unauthorized):
        await auth_svc.authenticate_token(session, "gsp_exp")

    # authenticate_session_cookie
    monkeypatch.setattr(auth_svc, "resolve_session", AsyncMock(return_value=user.id))
    session.get = AsyncMock(return_value=user)
    cookie_user = await auth_svc.authenticate_session_cookie(session, "sess_123")
    assert cookie_user is not None and cookie_user.id == user.id

    # list_tokens, create_token, revoke_token
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[pat]))
    tokens = await auth_svc.list_tokens(session, user)
    assert len(tokens) == 1

    session.get = AsyncMock(return_value=OrgSettings(id=1, pat_lifetime_days=90))
    with pytest.raises(Invalid):
        await auth_svc.create_token(session, user, "Too Long", 120)

    created_pat, raw_pat = await auth_svc.create_token(session, user, "CLI", 30)
    assert created_pat.name == "CLI"
    assert raw_pat.startswith("gsp_")

    session.get = AsyncMock(return_value=pat)
    await auth_svc.revoke_token(session, user, pat.id)
    assert pat.revoked_at is not None


@pytest.mark.asyncio
async def test_users_service_list_get_and_invites(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    member = _make_user("usr_member", OrgRole.MEMBER)

    assert "accept-invite?token=tok1" in users_svc.build_invite_url("tok1")

    # list_users & get_user
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[admin, member]))
    users = await users_svc.list_users(
        session, status=UserStatus.ACTIVE, org_role=OrgRole.ADMIN
    )
    assert len(users) == 2

    session.get = AsyncMock(return_value=member)
    assert (await users_svc.get_user(session, member.id)).id == member.id
    session.get = AsyncMock(return_value=None)
    with pytest.raises(NotFound):
        await users_svc.get_user(session, "usr_missing")

    # invite_user & reinvite_user
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=None))
    inv_user, raw_tok = await users_svc.invite_user(
        session, admin, email="new@gitseed.local", name="Newbie", org_role=OrgRole.MEMBER
    )
    assert inv_user.status == UserStatus.INVITED
    assert raw_tok.startswith("gsi_")

    session.get = AsyncMock(return_value=inv_user)
    reinv_user, raw_tok2 = await users_svc.reinvite_user(session, admin, inv_user.id)
    assert reinv_user.id == inv_user.id
    assert raw_tok2 != raw_tok

    # get_invite_by_token & accept_invite
    inv_user.invite_token_hash = hash_token(raw_tok2)
    inv_user.invite_expires_at = _now() + timedelta(days=5)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=inv_user))
    preview = await users_svc.get_invite_by_token(session, token=raw_tok2)
    assert preview.id == inv_user.id

    monkeypatch.setattr(users_svc, "create_session", AsyncMock(return_value="sess_inv"))
    accepted_user, sess_id = await users_svc.accept_invite(
        session, token=raw_tok2, password="StrongPassword123!", preferred_name="New"
    )
    assert accepted_user.status == UserStatus.ACTIVE
    assert sess_id == "sess_inv"


@pytest.mark.asyncio
async def test_users_service_update_suspend_reactivate_delete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    member = _make_user("usr_member", OrgRole.MEMBER)

    # update_user
    session.get = AsyncMock(return_value=member)
    session.scalar = AsyncMock(return_value=2)
    updated = await users_svc.update_user(
        session,
        admin,
        member.id,
        name="Member Updated",
        preferred_name="Mem",
        org_role=OrgRole.ADMIN,
        preferences={"theme": "light"},
        fields_set={"name", "preferred_name", "org_role", "preferences"},
    )
    assert updated.org_role == OrgRole.ADMIN

    # suspend_user & reactivate_user & delete_invited_user
    target = _make_user("usr_target", OrgRole.MEMBER, UserStatus.ACTIVE)
    session.get = AsyncMock(return_value=target)
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[]))
    monkeypatch.setattr(users_svc, "revoke_user_sessions", AsyncMock())
    suspended = await users_svc.suspend_user(session, admin, target.id)
    assert suspended.status == UserStatus.SUSPENDED

    reactivated = await users_svc.reactivate_user(session, admin, target.id)
    assert reactivated.status == UserStatus.ACTIVE

    invited_only = _make_user("usr_pending", OrgRole.MEMBER, UserStatus.INVITED)
    session.get = AsyncMock(return_value=invited_only)
    await users_svc.delete_invited_user(session, admin, invited_only.id)
    session.delete.assert_awaited_with(invited_only)


@pytest.mark.asyncio
async def test_settings_and_activity_services() -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)

    # settings.py
    org_row = OrgSettings(id=1)
    session.get = AsyncMock(return_value=org_row)
    updated_settings = await settings_svc.update_org_settings(
        session,
        admin,
        default_autonomy=SeedAutonomy.AUTONOMOUS,
        default_monthly_budget=Decimal("500"),
        max_concurrent_runners=4,
        queue_strategy=QueueStrategy.FAIR_SHARE,
        default_theme="dark",
        fields_set={
            "default_autonomy",
            "default_monthly_budget",
            "max_concurrent_runners",
            "queue_strategy",
            "default_theme",
        },
    )
    assert updated_settings.max_concurrent_runners == 4

    # activity.py
    act = activity_svc.record(
        session,
        type=ActivityType.SEED_CREATED,
        actor_type=ActorType.USER,
        actor_id=admin.id,
        project_id="prj_1",
    )
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[act]))
    events = await activity_svc.list_activity(session, admin, limit=10)
    assert len(events) == 1
