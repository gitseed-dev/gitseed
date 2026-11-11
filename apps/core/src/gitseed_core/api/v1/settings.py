from fastapi import APIRouter
from gitseed_common.schemas.settings import OrgSettingsResponse, UpdateOrgSettingsRequest

from gitseed_core.api.deps import AdminUser, CurrentUser, DBSession
from gitseed_core.services import settings as settings_service

router = APIRouter(prefix="/settings", tags=["Settings"])


@router.get("", response_model=OrgSettingsResponse)
async def get_settings(
    _current_user: CurrentUser,
    session: DBSession,
) -> OrgSettingsResponse:
    """Retrieve the organization-wide settings."""
    from gitseed_core.services.gateway import resolve_org_or_seed_budget_enforcement

    org_settings = await settings_service.get_org_settings(session)
    enf, detail = await resolve_org_or_seed_budget_enforcement(session)
    resp = OrgSettingsResponse.model_validate(org_settings)
    return resp.model_copy(update={"enforcement": enf, "enforcement_detail": detail})


@router.patch("", response_model=OrgSettingsResponse)
async def update_settings(
    payload: UpdateOrgSettingsRequest,
    admin: AdminUser,
    session: DBSession,
) -> OrgSettingsResponse:
    """Update organization-wide settings (admin only)."""
    from gitseed_core.services.gateway import resolve_org_or_seed_budget_enforcement

    org_settings = await settings_service.update_org_settings(
        session,
        admin,
        default_autonomy=payload.default_autonomy,
        default_monthly_budget=payload.default_monthly_budget,
        default_seed_budget_cap=payload.default_seed_budget_cap,
        default_spend_checkpoint=payload.default_spend_checkpoint,
        max_concurrent_runners=payload.max_concurrent_runners,
        max_sandbox_sessions=payload.max_sandbox_sessions,
        default_project_concurrency=payload.default_project_concurrency,
        queue_strategy=payload.queue_strategy,
        pat_lifetime_days=payload.pat_lifetime_days,
        default_theme=payload.default_theme,
        default_ambient_bg=payload.default_ambient_bg,
        default_show_guide=payload.default_show_guide,
        default_stage_seeds=payload.default_stage_seeds,
        fields_set=payload.model_fields_set,
    )
    enf, detail = await resolve_org_or_seed_budget_enforcement(session)
    resp = OrgSettingsResponse.model_validate(org_settings)
    return resp.model_copy(update={"enforcement": enf, "enforcement_detail": detail})

