from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from gitseed_common.enums import ActivityType, ActorType, QueueStrategy, SeedAutonomy
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.models.settings import OrgSettings
from gitseed_core.models.user import User
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Invalid

_NON_NULLABLE_FIELDS = frozenset(
    {
        "default_autonomy",
        "default_monthly_budget",
        "max_concurrent_runners",
        "max_sandbox_sessions",
        "default_project_concurrency",
        "queue_strategy",
        "pat_lifetime_days",
        "default_theme",
        "default_ambient_bg",
        "default_show_guide",
        "default_stage_seeds",
    }
)


async def get_org_settings(session: AsyncSession) -> OrgSettings:
    """Return the singleton OrgSettings row (id=1), creating it if missing."""
    settings_row = await session.get(OrgSettings, 1)
    if settings_row is None:
        settings_row = OrgSettings(id=1)
        session.add(settings_row)
        await session.commit()
        await session.refresh(settings_row)
    return settings_row


async def update_org_settings(
    session: AsyncSession,
    admin: User,
    *,
    default_autonomy: SeedAutonomy | None = None,
    default_monthly_budget: Decimal | None = None,
    default_seed_budget_cap: Decimal | None = None,
    default_spend_checkpoint: Decimal | None = None,
    max_concurrent_runners: int | None = None,
    max_sandbox_sessions: int | None = None,
    default_project_concurrency: int | None = None,
    queue_strategy: QueueStrategy | None = None,
    pat_lifetime_days: int | None = None,
    default_theme: str | None = None,
    default_ambient_bg: bool | None = None,
    default_show_guide: bool | None = None,
    default_stage_seeds: bool | None = None,
    fields_set: set[str] | None = None,
) -> OrgSettings:
    """Update organization settings and record settings.updated activity."""
    fields = fields_set or set()

    updates: dict[str, Any] = {
        "default_autonomy": default_autonomy,
        "default_monthly_budget": default_monthly_budget,
        "default_seed_budget_cap": default_seed_budget_cap,
        "default_spend_checkpoint": default_spend_checkpoint,
        "max_concurrent_runners": max_concurrent_runners,
        "max_sandbox_sessions": max_sandbox_sessions,
        "default_project_concurrency": default_project_concurrency,
        "queue_strategy": queue_strategy,
        "pat_lifetime_days": pat_lifetime_days,
        "default_theme": default_theme,
        "default_ambient_bg": default_ambient_bg,
        "default_show_guide": default_show_guide,
        "default_stage_seeds": default_stage_seeds,
    }

    for field_name in fields & _NON_NULLABLE_FIELDS:
        if updates[field_name] is None:
            raise Invalid(f"Setting '{field_name}' cannot be null")

    settings_row = await get_org_settings(session)
    changed: dict[str, Any] = {}

    if default_autonomy is not None and default_autonomy != settings_row.default_autonomy:
        settings_row.default_autonomy = default_autonomy
        changed["default_autonomy"] = str(default_autonomy)

    if (
        default_monthly_budget is not None
        and default_monthly_budget != settings_row.default_monthly_budget
    ):
        settings_row.default_monthly_budget = default_monthly_budget
        changed["default_monthly_budget"] = str(default_monthly_budget)

    if (
        "default_seed_budget_cap" in fields
        and default_seed_budget_cap != settings_row.default_seed_budget_cap
    ):
        settings_row.default_seed_budget_cap = default_seed_budget_cap
        changed["default_seed_budget_cap"] = (
            str(default_seed_budget_cap) if default_seed_budget_cap is not None else None
        )

    if (
        "default_spend_checkpoint" in fields
        and default_spend_checkpoint != settings_row.default_spend_checkpoint
    ):
        settings_row.default_spend_checkpoint = default_spend_checkpoint
        changed["default_spend_checkpoint"] = (
            str(default_spend_checkpoint) if default_spend_checkpoint is not None else None
        )

    if (
        max_concurrent_runners is not None
        and max_concurrent_runners != settings_row.max_concurrent_runners
    ):
        settings_row.max_concurrent_runners = max_concurrent_runners
        changed["max_concurrent_runners"] = max_concurrent_runners

    if (
        max_sandbox_sessions is not None
        and max_sandbox_sessions != settings_row.max_sandbox_sessions
    ):
        settings_row.max_sandbox_sessions = max_sandbox_sessions
        changed["max_sandbox_sessions"] = max_sandbox_sessions

    if (
        default_project_concurrency is not None
        and default_project_concurrency != settings_row.default_project_concurrency
    ):
        settings_row.default_project_concurrency = default_project_concurrency
        changed["default_project_concurrency"] = default_project_concurrency

    if queue_strategy is not None and queue_strategy != settings_row.queue_strategy:
        settings_row.queue_strategy = queue_strategy
        changed["queue_strategy"] = str(queue_strategy)

    if pat_lifetime_days is not None and pat_lifetime_days != settings_row.pat_lifetime_days:
        settings_row.pat_lifetime_days = pat_lifetime_days
        changed["pat_lifetime_days"] = pat_lifetime_days

    if default_theme is not None and default_theme != settings_row.default_theme:
        if default_theme not in ("dark", "light", "system"):
            raise Invalid("Setting 'default_theme' must be 'dark', 'light', or 'system'")
        settings_row.default_theme = default_theme
        changed["default_theme"] = default_theme

    if (
        default_ambient_bg is not None
        and default_ambient_bg != settings_row.default_ambient_bg
    ):
        settings_row.default_ambient_bg = default_ambient_bg
        changed["default_ambient_bg"] = default_ambient_bg

    if (
        default_show_guide is not None
        and default_show_guide != settings_row.default_show_guide
    ):
        settings_row.default_show_guide = default_show_guide
        changed["default_show_guide"] = default_show_guide

    if (
        default_stage_seeds is not None
        and default_stage_seeds != settings_row.default_stage_seeds
    ):
        settings_row.default_stage_seeds = default_stage_seeds
        changed["default_stage_seeds"] = default_stage_seeds

    if changed:
        settings_row.updated_at = datetime.now(UTC)
        settings_row.updated_by = admin.id
        record(
            session,
            type=ActivityType.SETTINGS_UPDATED,
            actor_type=ActorType.USER,
            actor_id=admin.id,
            data={"changes": changed},
        )
        await session.commit()
        await session.refresh(settings_row)

    return settings_row

