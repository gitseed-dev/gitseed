from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from gitseed_common.enums import (
    ActivityType,
    ActorType,
    AgentKind,
    ProjectRole,
    SeedStage,
)
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.agent import Agent
from gitseed_core.models.policy import Policy
from gitseed_core.models.user import User
from gitseed_core.services.activity import record
from gitseed_core.services.errors import Invalid, NotFound
from gitseed_core.services.projects import get_project, require_project_maintainer

STAGE_ORDER: tuple[SeedStage, ...] = (
    SeedStage.IDEATE,
    SeedStage.PLAN,
    SeedStage.EXECUTE,
    SeedStage.REVIEW,
    SeedStage.MERGE,
)

POLICY_OVERRIDE_FIELDS: tuple[str, ...] = (
    "enabled",
    "agent_id",
    "requires_approval",
    "min_approvers",
    "min_approver_role",
    "allow_auto_approve",
    "allow_self_approval",
    "allow_second_opinions",
    "max_attempts",
    "timeout_minutes",
    "min_acceptance_criteria",
    "max_acceptance_criteria",
    "max_tasks",
)

DEFAULT_ORG_STAGE_POLICIES: dict[SeedStage, dict[str, Any]] = {
    SeedStage.IDEATE: {
        "enabled": True,
        "requires_approval": True,
        "min_approvers": 1,
        "min_approver_role": ProjectRole.DEVELOPER,
        "allow_auto_approve": True,
        "allow_self_approval": True,
        "allow_second_opinions": True,
        "max_attempts": 3,
        "timeout_minutes": 30,
        "min_acceptance_criteria": 1,
        "max_acceptance_criteria": 6,
    },
    SeedStage.PLAN: {
        "enabled": True,
        "requires_approval": True,
        "min_approvers": 1,
        "min_approver_role": ProjectRole.DEVELOPER,
        "allow_auto_approve": True,
        "allow_self_approval": True,
        "allow_second_opinions": True,
        "max_attempts": 3,
        "timeout_minutes": 30,
        "max_tasks": 5,
    },
    SeedStage.EXECUTE: {
        "enabled": True,
        "requires_approval": False,
        "min_approvers": 1,
        "min_approver_role": ProjectRole.DEVELOPER,
        "allow_auto_approve": True,
        "allow_self_approval": True,
        "allow_second_opinions": False,
        "max_attempts": 3,
        "timeout_minutes": 60,
    },
    SeedStage.REVIEW: {
        "enabled": True,
        "requires_approval": True,
        "min_approvers": 1,
        "min_approver_role": ProjectRole.MAINTAINER,
        "allow_auto_approve": True,
        "allow_self_approval": True,
        "allow_second_opinions": True,
        "max_attempts": 2,
        "timeout_minutes": 30,
    },
    SeedStage.MERGE: {
        "enabled": True,
        "requires_approval": True,
        "min_approvers": 1,
        "min_approver_role": ProjectRole.MAINTAINER,
        "allow_auto_approve": False,
        "allow_self_approval": True,
        "allow_second_opinions": False,
        "max_attempts": 1,
        "timeout_minutes": 15,
    },
}


@dataclass(frozen=True)
class EffectiveStagePolicy:
    id: str | None
    project_id: str | None
    stage: SeedStage
    enabled: bool
    agent_id: str | None
    requires_approval: bool
    min_approvers: int
    min_approver_role: ProjectRole
    allow_auto_approve: bool
    allow_self_approval: bool
    max_attempts: int
    timeout_minutes: int
    allow_second_opinions: bool = False
    min_acceptance_criteria: int | None = None
    max_acceptance_criteria: int | None = None
    max_tasks: int | None = None
    overridden_fields: list[str] = field(default_factory=list)
    instructions: str | None = None
    inherited_instructions: str | None = None
    updated_at: datetime | None = None
    updated_by: str | None = None

    @property
    def combined_instructions(self) -> str | None:
        """Org stage instructions followed by the project's (both optional)."""
        parts = [p.strip() for p in (self.inherited_instructions, self.instructions) if p and p.strip()]
        return "\n\n".join(parts) or None


def _validate_policy_rules(
    stage: SeedStage,
    *,
    enabled: bool | None,
    min_approver_role: ProjectRole | None,
    allow_second_opinions: bool | None = None,
    min_acceptance_criteria: int | None = None,
    max_acceptance_criteria: int | None = None,
    max_tasks: int | None = None,
) -> None:
    if stage in (SeedStage.EXECUTE, SeedStage.MERGE) and enabled is False:
        raise Invalid(f"Stage '{str(stage)}' is required and cannot be disabled")
    if stage in (SeedStage.EXECUTE, SeedStage.MERGE) and allow_second_opinions is True:
        raise Invalid(
            f"Second opinions are only supported on 'ideate', 'plan', and 'review' (not '{str(stage)}')"
        )
    if min_approver_role == ProjectRole.VIEWER:
        raise Invalid("min_approver_role must be 'maintainer' or 'developer'")
    if stage != SeedStage.IDEATE and (
        min_acceptance_criteria is not None or max_acceptance_criteria is not None
    ):
        raise Invalid(
            "min_acceptance_criteria and max_acceptance_criteria only apply to the 'ideate' stage"
        )
    if stage != SeedStage.PLAN and max_tasks is not None:
        raise Invalid("max_tasks only applies to the 'plan' stage")
    if (
        min_acceptance_criteria is not None
        and max_acceptance_criteria is not None
        and min_acceptance_criteria > max_acceptance_criteria
    ):
        raise Invalid("min_acceptance_criteria cannot exceed max_acceptance_criteria")


async def _validate_stage_agent(
    session: AsyncSession, agent_id: str | None
) -> str | None:
    if agent_id is None:
        return None
    agent = await session.get(Agent, agent_id)
    if agent is None and not agent_id.startswith("agt_"):
        clean_handle = agent_id.strip().lstrip("@").lower()
        if clean_handle:
            agent = (
                await session.execute(
                    select(Agent).where(
                        col(Agent.archived_at).is_(None),
                        col(Agent.handle) == clean_handle,
                    )
                )
            ).scalars().first()
    if agent is None or agent.archived_at is not None:
        raise NotFound("Agent not found")
    if agent.kind != AgentKind.STAGE:
        raise Invalid("Only stage agents (kind='stage') can be assigned to stage policies")
    return agent.id


async def ensure_org_policies(
    session: AsyncSession, *, commit_if_created: bool = False
) -> dict[SeedStage, Policy]:
    """Fetch all 5 org-default stage policies, creating any missing defaults."""
    rows = (
        await session.execute(select(Policy).where(col(Policy.project_id).is_(None)))
    ).scalars().all()
    by_stage: dict[SeedStage, Policy] = {r.stage: r for r in rows}

    created = False
    now = datetime.now(UTC)
    for stage in STAGE_ORDER:
        if stage not in by_stage:
            defaults = DEFAULT_ORG_STAGE_POLICIES[stage]
            pol = Policy(
                project_id=None,
                stage=stage,
                enabled=defaults["enabled"],
                agent_id=None,
                requires_approval=defaults["requires_approval"],
                min_approvers=defaults["min_approvers"],
                min_approver_role=defaults["min_approver_role"],
                allow_auto_approve=defaults["allow_auto_approve"],
                allow_self_approval=defaults["allow_self_approval"],
                allow_second_opinions=defaults["allow_second_opinions"],
                max_attempts=defaults["max_attempts"],
                timeout_minutes=defaults["timeout_minutes"],
                created_at=now,
                updated_at=now,
            )
            session.add(pol)
            by_stage[stage] = pol
            created = True
        else:
            existing_pol = by_stage[stage]
            if (
                existing_pol.updated_by is None
                and existing_pol.allow_self_approval is False
                and DEFAULT_ORG_STAGE_POLICIES[stage]["allow_self_approval"] is True
            ):
                existing_pol.allow_self_approval = True
                created = True
            if existing_pol.allow_second_opinions is None:
                existing_pol.allow_second_opinions = bool(
                    DEFAULT_ORG_STAGE_POLICIES[stage]["allow_second_opinions"]
                )
                created = True

    if created:
        if commit_if_created:
            await session.commit()
        else:
            await session.flush()

    return by_stage


def _merge_stage_policy(
    stage: SeedStage,
    org_pol: Policy,
    proj_pol: Policy | None,
    *,
    project_id: str | None = None,
) -> EffectiveStagePolicy:
    defaults = DEFAULT_ORG_STAGE_POLICIES[stage]
    overridden: list[str] = []

    def _pick(attr: str) -> Any:
        org_val = getattr(org_pol, attr)
        inherited = org_val if org_val is not None else defaults.get(attr)
        if proj_pol is not None and getattr(proj_pol, attr) is not None:
            val = getattr(proj_pol, attr)
            # Only a value that actually differs from the org default counts as an override
            if _norm(val) != _norm(inherited):
                overridden.append(attr)
            return val
        return inherited

    enabled = bool(_pick("enabled"))
    agent_id: str | None = _pick("agent_id")
    requires_approval = bool(_pick("requires_approval"))
    min_approvers = int(_pick("min_approvers"))
    min_approver_role = ProjectRole(_pick("min_approver_role"))
    allow_auto_approve = bool(_pick("allow_auto_approve"))
    allow_self_approval = bool(_pick("allow_self_approval"))
    allow_second_opinions = (
        False
        if stage in (SeedStage.EXECUTE, SeedStage.MERGE)
        else bool(_pick("allow_second_opinions"))
    )
    max_attempts = int(_pick("max_attempts"))
    timeout_minutes = int(_pick("timeout_minutes"))
    min_acceptance_criteria: int | None = (
        int(_pick("min_acceptance_criteria")) if stage == SeedStage.IDEATE else None
    )
    max_acceptance_criteria: int | None = (
        int(_pick("max_acceptance_criteria")) if stage == SeedStage.IDEATE else None
    )
    max_tasks: int | None = (
        int(_pick("max_tasks")) if stage == SeedStage.PLAN else None
    )

    target_row = proj_pol if proj_pol is not None else org_pol
    return EffectiveStagePolicy(
        id=target_row.id,
        project_id=project_id,
        stage=stage,
        enabled=enabled,
        agent_id=agent_id,
        requires_approval=requires_approval,
        min_approvers=min_approvers,
        min_approver_role=min_approver_role,
        allow_auto_approve=allow_auto_approve,
        allow_self_approval=allow_self_approval,
        allow_second_opinions=allow_second_opinions,
        max_attempts=max_attempts,
        timeout_minutes=timeout_minutes,
        min_acceptance_criteria=min_acceptance_criteria,
        max_acceptance_criteria=max_acceptance_criteria,
        max_tasks=max_tasks,
        overridden_fields=overridden,
        # Org view: the org's own text. Project view: the project's own, plus the org's as inherited.
        instructions=(proj_pol.instructions if proj_pol is not None else (None if project_id else org_pol.instructions)),
        inherited_instructions=(org_pol.instructions if project_id else None),
        updated_at=target_row.updated_at,
        updated_by=target_row.updated_by,
    )


def _norm(value: Any) -> Any:
    """Comparable form of a policy value (enums vs their string values)."""
    return getattr(value, "value", value)


async def get_effective_stage_policy(
    session: AsyncSession,
    project_id: str,
    stage: SeedStage,
) -> EffectiveStagePolicy:
    """Resolve effective stage policy for a project and stage."""
    rows = (
        await session.execute(
            select(Policy).where(
                Policy.stage == stage,
                or_(
                    col(Policy.project_id).is_(None),
                    col(Policy.project_id) == project_id,
                ),
            )
        )
    ).scalars().all()

    org_pol = next((r for r in rows if r.project_id is None), None)
    if org_pol is None:
        org_map = await ensure_org_policies(session)
        org_pol = org_map[stage]
    proj_pol = next((r for r in rows if r.project_id == project_id), None)

    return _merge_stage_policy(stage, org_pol, proj_pol, project_id=project_id)


async def next_enabled_stage(
    session: AsyncSession,
    project_id: str,
    current_stage: SeedStage,
) -> SeedStage | None:
    """Return the next enabled stage after `current_stage`, or None if at MERGE."""
    idx = STAGE_ORDER.index(current_stage)
    for candidate in STAGE_ORDER[idx + 1 :]:
        pol = await get_effective_stage_policy(session, project_id, candidate)
        if pol.enabled or candidate in (SeedStage.EXECUTE, SeedStage.MERGE):
            return candidate
    return None


async def first_enabled_stage(
    session: AsyncSession,
    project_id: str,
) -> SeedStage:
    """Return the first enabled stage in `STAGE_ORDER` for a project."""
    for candidate in STAGE_ORDER:
        pol = await get_effective_stage_policy(session, project_id, candidate)
        if pol.enabled or candidate in (SeedStage.EXECUTE, SeedStage.MERGE):
            return candidate
    return SeedStage.EXECUTE


async def list_org_policies(session: AsyncSession) -> list[EffectiveStagePolicy]:
    """List all 5 organization-default stage policies in stage order."""
    org_map = await ensure_org_policies(session, commit_if_created=True)
    return [_merge_stage_policy(s, org_map[s], None, project_id=None) for s in STAGE_ORDER]


async def update_org_policy(
    session: AsyncSession,
    admin: User,
    stage: SeedStage,
    *,
    enabled: bool | None = None,
    agent_id: str | None = None,
    requires_approval: bool | None = None,
    min_approvers: int | None = None,
    min_approver_role: ProjectRole | None = None,
    allow_auto_approve: bool | None = None,
    allow_self_approval: bool | None = None,
    allow_second_opinions: bool | None = None,
    max_attempts: int | None = None,
    timeout_minutes: int | None = None,
    min_acceptance_criteria: int | None = None,
    max_acceptance_criteria: int | None = None,
    max_tasks: int | None = None,
    instructions: str | None = None,
    fields_set: set[str] | None = None,
) -> EffectiveStagePolicy:
    """Update the organization-default policy for a stage and record policy.updated."""
    fields = fields_set or set()
    _validate_policy_rules(
        stage,
        enabled=enabled,
        min_approver_role=min_approver_role,
        allow_second_opinions=allow_second_opinions,
        min_acceptance_criteria=min_acceptance_criteria,
        max_acceptance_criteria=max_acceptance_criteria,
        max_tasks=max_tasks,
    )

    # Org policies cannot store NULL on non-agent fields (ck_policy_org_complete)
    stage_bound_fields = {"min_acceptance_criteria", "max_acceptance_criteria", "max_tasks"}
    non_nullable = set(POLICY_OVERRIDE_FIELDS) - {"agent_id"} - stage_bound_fields
    for field_name in fields & non_nullable:
        if locals().get(field_name) is None:
            raise Invalid(f"Organization default policy field '{field_name}' cannot be null")
    if stage == SeedStage.IDEATE:
        for field_name in fields & {"min_acceptance_criteria", "max_acceptance_criteria"}:
            if locals().get(field_name) is None:
                raise Invalid(f"Organization default policy field '{field_name}' cannot be null")
    if stage == SeedStage.PLAN and "max_tasks" in fields and max_tasks is None:
        raise Invalid("Organization default policy field 'max_tasks' cannot be null")

    if "agent_id" in fields:
        agent_id = await _validate_stage_agent(session, agent_id)

    org_map = await ensure_org_policies(session)
    pol = org_map[stage]

    if stage == SeedStage.IDEATE:
        defaults = DEFAULT_ORG_STAGE_POLICIES[stage]
        eff_min = (
            min_acceptance_criteria
            if min_acceptance_criteria is not None
            else (
                pol.min_acceptance_criteria
                if pol.min_acceptance_criteria is not None
                else int(defaults["min_acceptance_criteria"])
            )
        )
        eff_max = (
            max_acceptance_criteria
            if max_acceptance_criteria is not None
            else (
                pol.max_acceptance_criteria
                if pol.max_acceptance_criteria is not None
                else int(defaults["max_acceptance_criteria"])
            )
        )
        if eff_min > eff_max:
            raise Invalid("min_acceptance_criteria cannot exceed max_acceptance_criteria")

    changed: dict[str, Any] = {}
    if enabled is not None:
        pol.enabled = enabled
        changed["enabled"] = enabled
    if "agent_id" in fields:
        pol.agent_id = agent_id
        changed["agent_id"] = agent_id
    if requires_approval is not None:
        pol.requires_approval = requires_approval
        changed["requires_approval"] = requires_approval
    if min_approvers is not None:
        pol.min_approvers = min_approvers
        changed["min_approvers"] = min_approvers
    if min_approver_role is not None:
        pol.min_approver_role = min_approver_role
        changed["min_approver_role"] = str(min_approver_role)
    if allow_auto_approve is not None:
        pol.allow_auto_approve = allow_auto_approve
        changed["allow_auto_approve"] = allow_auto_approve
    if allow_self_approval is not None:
        pol.allow_self_approval = allow_self_approval
        changed["allow_self_approval"] = allow_self_approval
    if allow_second_opinions is not None:
        pol.allow_second_opinions = allow_second_opinions
        changed["allow_second_opinions"] = allow_second_opinions
    if max_attempts is not None:
        pol.max_attempts = max_attempts
        changed["max_attempts"] = max_attempts
    if timeout_minutes is not None:
        pol.timeout_minutes = timeout_minutes
        changed["timeout_minutes"] = timeout_minutes
    if min_acceptance_criteria is not None and stage == SeedStage.IDEATE:
        pol.min_acceptance_criteria = min_acceptance_criteria
        changed["min_acceptance_criteria"] = min_acceptance_criteria
    if max_acceptance_criteria is not None and stage == SeedStage.IDEATE:
        pol.max_acceptance_criteria = max_acceptance_criteria
        changed["max_acceptance_criteria"] = max_acceptance_criteria
    if max_tasks is not None and stage == SeedStage.PLAN:
        pol.max_tasks = max_tasks
        changed["max_tasks"] = max_tasks
    if "instructions" in fields:
        new_text = (instructions or "").strip() or None
        if new_text != pol.instructions:
            pol.instructions = new_text
            changed["instructions"] = "updated" if new_text else "cleared"

    now = datetime.now(UTC)
    pol.updated_at = now
    pol.updated_by = admin.id

    if changed:
        record(
            session,
            type=ActivityType.POLICY_UPDATED,
            actor_type=ActorType.USER,
            actor_id=admin.id,
            project_id=None,
            data={"stage": str(stage), "changes": changed},
        )

    await session.commit()
    await session.refresh(pol)
    return _merge_stage_policy(stage, pol, None, project_id=None)


async def list_project_policies(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
) -> list[EffectiveStagePolicy]:
    """List all 5 stage policies for a project (effective values + overridden_fields)."""
    project = await get_project(session, actor, project_id_or_slug)
    org_map = await ensure_org_policies(session, commit_if_created=True)

    proj_rows = (
        await session.execute(select(Policy).where(Policy.project_id == project.id))
    ).scalars().all()
    proj_map: dict[SeedStage, Policy] = {r.stage: r for r in proj_rows}

    return [
        _merge_stage_policy(s, org_map[s], proj_map.get(s), project_id=project.id)
        for s in STAGE_ORDER
    ]


async def update_project_policy(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    stage: SeedStage,
    *,
    enabled: bool | None = None,
    agent_id: str | None = None,
    requires_approval: bool | None = None,
    min_approvers: int | None = None,
    min_approver_role: ProjectRole | None = None,
    allow_auto_approve: bool | None = None,
    allow_self_approval: bool | None = None,
    allow_second_opinions: bool | None = None,
    max_attempts: int | None = None,
    timeout_minutes: int | None = None,
    min_acceptance_criteria: int | None = None,
    max_acceptance_criteria: int | None = None,
    max_tasks: int | None = None,
    instructions: str | None = None,
    fields_set: set[str] | None = None,
) -> EffectiveStagePolicy:
    """Create or update project-level policy overrides for a stage and record policy.updated."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    fields = fields_set or set()

    _validate_policy_rules(
        stage,
        enabled=enabled,
        min_approver_role=min_approver_role,
        allow_second_opinions=allow_second_opinions,
        min_acceptance_criteria=min_acceptance_criteria,
        max_acceptance_criteria=max_acceptance_criteria,
        max_tasks=max_tasks,
    )
    if "agent_id" in fields and agent_id is not None:
        agent_id = await _validate_stage_agent(session, agent_id)

    org_map = await ensure_org_policies(session)
    org_pol = org_map[stage]

    proj_pol = (
        await session.execute(
            select(Policy).where(
                Policy.project_id == project.id,
                Policy.stage == stage,
            )
        )
    ).scalar_one_or_none()

    now = datetime.now(UTC)
    if proj_pol is None:
        proj_pol = Policy(
            project_id=project.id,
            stage=stage,
            created_by=actor.id,
            created_at=now,
            updated_at=now,
            updated_by=actor.id,
        )
        session.add(proj_pol)

    updates: dict[str, Any] = {
        "enabled": enabled,
        "agent_id": agent_id,
        "requires_approval": requires_approval,
        "min_approvers": min_approvers,
        "min_approver_role": min_approver_role,
        "allow_auto_approve": allow_auto_approve,
        "allow_self_approval": allow_self_approval,
        "allow_second_opinions": allow_second_opinions,
        "max_attempts": max_attempts,
        "timeout_minutes": timeout_minutes,
        "min_acceptance_criteria": min_acceptance_criteria,
        "max_acceptance_criteria": max_acceptance_criteria,
        "max_tasks": max_tasks,
    }

    changed: dict[str, Any] = {}
    defaults = DEFAULT_ORG_STAGE_POLICIES[stage]
    for attr in POLICY_OVERRIDE_FIELDS:
        if attr in fields:
            val = updates[attr]
            org_val = getattr(org_pol, attr)
            inherited = org_val if org_val is not None else defaults.get(attr)
            # The form sends every field; values equal to the org default are stored as
            # "inherit" (NULL) so only real changes become overrides and later org-default
            # changes still flow through to this project.
            stored = None if val is not None and _norm(val) == _norm(inherited) else val
            if _norm(getattr(proj_pol, attr)) != _norm(stored):
                changed[attr] = str(val) if isinstance(val, ProjectRole) else val
            setattr(proj_pol, attr, stored)

    if stage == SeedStage.IDEATE:
        merged_preview = _merge_stage_policy(stage, org_pol, proj_pol, project_id=project.id)
        if (
            merged_preview.min_acceptance_criteria is not None
            and merged_preview.max_acceptance_criteria is not None
            and merged_preview.min_acceptance_criteria > merged_preview.max_acceptance_criteria
        ):
            raise Invalid("min_acceptance_criteria cannot exceed max_acceptance_criteria")

    if "instructions" in fields:
        new_text = (instructions or "").strip() or None
        if new_text != proj_pol.instructions:
            proj_pol.instructions = new_text
            changed["instructions"] = "updated" if new_text else "cleared"

    proj_pol.updated_at = now
    proj_pol.updated_by = actor.id

    # If every override field (and the instructions) on the project row is now None, remove it
    if proj_pol.instructions is None and all(
        getattr(proj_pol, attr) is None for attr in POLICY_OVERRIDE_FIELDS
    ):
        await session.delete(proj_pol)
        proj_pol = None

    if changed:
        record(
            session,
            type=ActivityType.POLICY_UPDATED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=project.id,
            data={"stage": str(stage), "changes": changed},
        )

    await session.commit()
    if proj_pol is not None:
        await session.refresh(proj_pol)

    return _merge_stage_policy(stage, org_pol, proj_pol, project_id=project.id)


async def reset_project_policy(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    stage: SeedStage,
) -> EffectiveStagePolicy:
    """Delete a project's stage policy override row so it inherits 100% from org defaults."""
    project = await require_project_maintainer(session, actor, project_id_or_slug)
    org_map = await ensure_org_policies(session)
    org_pol = org_map[stage]

    proj_pol = (
        await session.execute(
            select(Policy).where(
                Policy.project_id == project.id,
                Policy.stage == stage,
            )
        )
    ).scalar_one_or_none()

    if proj_pol is not None:
        await session.delete(proj_pol)
        record(
            session,
            type=ActivityType.POLICY_UPDATED,
            actor_type=ActorType.USER,
            actor_id=actor.id,
            project_id=project.id,
            data={"stage": str(stage), "reset_to_org_default": True},
        )
        await session.commit()

    return _merge_stage_policy(stage, org_pol, None, project_id=project.id)

