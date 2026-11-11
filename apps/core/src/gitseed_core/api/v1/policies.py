from fastapi import APIRouter
from gitseed_common.enums import SeedStage
from gitseed_common.schemas.policy import (
    StagePolicyResponse,
    UpdateOrgPolicyRequest,
    UpdateProjectPolicyRequest,
)

from gitseed_core.api.deps import AdminUser, CurrentUser, DBSession
from gitseed_core.services import policies as policies_service

router = APIRouter(prefix="/policies", tags=["Policies"])


@router.get("", response_model=list[StagePolicyResponse])
async def list_org_policies(
    _current_user: CurrentUser,
    session: DBSession,
) -> list[StagePolicyResponse]:
    """List the 5 organization-default stage policies."""
    policies = await policies_service.list_org_policies(session)
    return [StagePolicyResponse.model_validate(p) for p in policies]


@router.patch("/{stage}", response_model=StagePolicyResponse)
async def update_org_policy(
    stage: SeedStage,
    payload: UpdateOrgPolicyRequest,
    admin: AdminUser,
    session: DBSession,
) -> StagePolicyResponse:
    """Update the organization-default policy for a stage."""
    policy = await policies_service.update_org_policy(
        session,
        admin,
        stage,
        enabled=payload.enabled,
        agent_id=payload.agent_id,
        requires_approval=payload.requires_approval,
        min_approvers=payload.min_approvers,
        min_approver_role=payload.min_approver_role,
        allow_auto_approve=payload.allow_auto_approve,
        allow_self_approval=payload.allow_self_approval,
        allow_second_opinions=payload.allow_second_opinions,
        max_attempts=payload.max_attempts,
        timeout_minutes=payload.timeout_minutes,
        min_acceptance_criteria=payload.min_acceptance_criteria,
        max_acceptance_criteria=payload.max_acceptance_criteria,
        max_tasks=payload.max_tasks,
        instructions=payload.instructions,
        fields_set=payload.model_fields_set,
    )
    return StagePolicyResponse.model_validate(policy)


@router.get(
    "/projects/{project_id}",
    response_model=list[StagePolicyResponse],
)
async def list_project_policies(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[StagePolicyResponse]:
    """List effective stage policies for a project along with which fields are overridden."""
    policies = await policies_service.list_project_policies(
        session, current_user, project_id
    )
    return [StagePolicyResponse.model_validate(p) for p in policies]


@router.patch(
    "/projects/{project_id}/{stage}",
    response_model=StagePolicyResponse,
)
async def update_project_policy(
    project_id: str,
    stage: SeedStage,
    payload: UpdateProjectPolicyRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> StagePolicyResponse:
    """Set or clear (via explicit null) project-level policy overrides for a stage."""
    policy = await policies_service.update_project_policy(
        session,
        current_user,
        project_id,
        stage,
        enabled=payload.enabled,
        agent_id=payload.agent_id,
        requires_approval=payload.requires_approval,
        min_approvers=payload.min_approvers,
        min_approver_role=payload.min_approver_role,
        allow_auto_approve=payload.allow_auto_approve,
        allow_self_approval=payload.allow_self_approval,
        allow_second_opinions=payload.allow_second_opinions,
        max_attempts=payload.max_attempts,
        timeout_minutes=payload.timeout_minutes,
        min_acceptance_criteria=payload.min_acceptance_criteria,
        max_acceptance_criteria=payload.max_acceptance_criteria,
        max_tasks=payload.max_tasks,
        instructions=payload.instructions,
        fields_set=payload.model_fields_set,
    )
    return StagePolicyResponse.model_validate(policy)


@router.delete(
    "/projects/{project_id}/{stage}",
    response_model=StagePolicyResponse,
)
async def reset_project_policy(
    project_id: str,
    stage: SeedStage,
    current_user: CurrentUser,
    session: DBSession,
) -> StagePolicyResponse:
    """Reset a project's stage policy to inherit all organization defaults."""
    policy = await policies_service.reset_project_policy(
        session, current_user, project_id, stage
    )
    return StagePolicyResponse.model_validate(policy)

