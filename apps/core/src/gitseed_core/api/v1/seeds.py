from fastapi import APIRouter, Query, status
from gitseed_common.enums import SeedAutonomy, SeedStage, SeedStatus
from gitseed_common.schemas.run import RunResponse
from gitseed_common.schemas.seed import (
    AdvanceSeedRequest,
    ApprovalResponse,
    ApproveCheckpointRequest,
    CreateApprovalRequest,
    CreatePullRequestCommentRequest,
    CreateSeedCommentRequest,
    CreateSeedRequest,
    CreateSeedTaskRequest,
    PullRequestCommentItem,
    PullRequestStatusResponse,
    SecondOpinionRequest,
    SeedChangesResponse,
    SeedCommentResponse,
    SeedResponse,
    SeedTaskResponse,
    UpdateSeedCommentRequest,
    UpdateSeedRequest,
    UpdateSeedTaskRequest,
)
from pydantic import BaseModel, Field

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.models.seed import SeedComment
from gitseed_core.services import git_browse as git_browse_service
from gitseed_core.services import git_merge as git_merge_service
from gitseed_core.services import seeds as seeds_service

router = APIRouter(prefix="/seeds", tags=["Seeds"])


def _serialize_comment(comment: SeedComment) -> SeedCommentResponse:
    resp = SeedCommentResponse.model_validate(comment)
    if comment.deleted_at is not None:
        return resp.model_copy(update={"body": ""})
    return resp


@router.get("", response_model=list[SeedResponse])
async def list_seeds(
    current_user: CurrentUser,
    session: DBSession,
    project_id: str | None = None,
    stage: SeedStage | None = None,
    status: SeedStatus | None = None,
    open_only: bool = False,
    autonomy: SeedAutonomy | None = None,
    tag: str | None = None,
    created_by: str | None = None,
    limit: int = Query(default=100, ge=1, le=1000),
    offset: int = Query(default=0, ge=0),
) -> list[SeedResponse]:
    """List seeds across projects visible to the current user."""
    seeds = await seeds_service.list_seeds(
        session,
        current_user,
        project_id=project_id,
        stage=stage,
        status=status,
        open_only=open_only,
        autonomy=autonomy,
        tag=tag,
        created_by=created_by,
        limit=limit,
        offset=offset,
    )
    return [SeedResponse.model_validate(s) for s in seeds]


@router.post(
    "",
    response_model=SeedResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_seed(
    payload: CreateSeedRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Create a new seed in a project."""
    seed = await seeds_service.create_seed(
        session,
        current_user,
        project_id=payload.project_id,
        title=payload.title,
        description=payload.description,
        specification=payload.specification,
        acceptance_criteria=payload.acceptance_criteria,
        plan=payload.plan,
        tags=payload.tags,
        stage=payload.stage,
        fast_track=payload.fast_track,
        autonomy=payload.autonomy,
        budget_cap=payload.budget_cap,
        staged=payload.staged,
        ideate_agent_ids=payload.ideate_agent_ids,
    )
    return SeedResponse.model_validate(seed)


@router.get("/{seed_id}", response_model=SeedResponse)
async def get_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Retrieve a single seed by ID."""
    from gitseed_core.services.gateway import resolve_org_or_seed_budget_enforcement

    seed = await seeds_service.get_seed(session, current_user, seed_id)
    enf, detail = await resolve_org_or_seed_budget_enforcement(session, seed_id=seed.id)
    resp = SeedResponse.model_validate(seed)
    return resp.model_copy(update={"enforcement": enf, "enforcement_detail": detail})


@router.patch("/{seed_id}", response_model=SeedResponse)
async def update_seed(
    seed_id: str,
    payload: UpdateSeedRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Update a seed's fields, autonomy mode, or budget cap."""
    fields_set = set(payload.model_fields_set)
    seed = await seeds_service.update_seed(
        session,
        current_user,
        seed_id,
        title=payload.title,
        description=payload.description,
        specification=payload.specification,
        acceptance_criteria=(
            payload.acceptance_criteria
            if "acceptance_criteria" in fields_set
            else None
        ),
        plan=payload.plan,
        tags=payload.tags if "tags" in fields_set else None,
        autonomy=payload.autonomy,
        budget_cap=payload.budget_cap,
        fields_set=fields_set,
    )
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/start", response_model=SeedResponse)
async def start_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Start work on a staged seed by moving it from staged to queued."""
    seed = await seeds_service.start_seed(session, current_user, seed_id)
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/advance", response_model=SeedResponse)
async def advance_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
    payload: AdvanceSeedRequest | None = None,
) -> SeedResponse:
    """Advance or move an open seed to a target stage (or the next enabled stage)."""
    seed = await seeds_service.advance_seed(
        session,
        current_user,
        seed_id,
        target_stage=payload.stage if payload else None,
        fast_track=payload.fast_track if payload else False,
    )
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/approve-checkpoint", response_model=SeedResponse)
async def approve_seed_checkpoint(
    seed_id: str,
    payload: ApproveCheckpointRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Raise checkpoint_approved_through to unblock a spend checkpoint."""
    seed = await seeds_service.approve_checkpoint(
        session,
        current_user,
        seed_id,
        approved_through=payload.approved_through,
    )
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/close", response_model=SeedResponse)
async def close_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Close/abandon an open seed without merging."""
    seed = await seeds_service.close_seed(session, current_user, seed_id)
    return SeedResponse.model_validate(seed)


class MoveSeedBackRequest(BaseModel):
    stage: SeedStage
    reason: str = Field(min_length=1, max_length=4000)


@router.post("/{seed_id}/move-back", response_model=SeedResponse)
async def move_seed_back(
    seed_id: str,
    payload: MoveSeedBackRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Send a seed back to an earlier stage (resets approvals from there on) and re-queue it."""
    seed = await seeds_service.move_seed_back(
        session, current_user, seed_id, target_stage=payload.stage, reason=payload.reason
    )
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/stop", response_model=SeedResponse)
async def stop_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Stop the agent working on this seed (cancels its run); the seed waits as 'stopped'."""
    seed = await seeds_service.stop_seed(session, current_user, seed_id)
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/resume", response_model=SeedResponse)
async def resume_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Resume a stopped seed: re-queue its current stage."""
    seed = await seeds_service.resume_seed(session, current_user, seed_id)
    return SeedResponse.model_validate(seed)


@router.post("/{seed_id}/reopen", response_model=SeedResponse)
async def reopen_seed(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Reopen a closed seed back to queued in its current stage."""
    seed = await seeds_service.reopen_seed(session, current_user, seed_id)
    return SeedResponse.model_validate(seed)


@router.post(
    "/{seed_id}/second-opinion",
    response_model=RunResponse,
    status_code=status.HTTP_201_CREATED,
)
async def request_seed_second_opinion(
    seed_id: str,
    payload: SecondOpinionRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> RunResponse:
    """Request an additional stage agent run (second opinion) for Ideate, Plan, or Review."""
    run = await seeds_service.request_second_opinion(
        session,
        current_user,
        seed_id,
        agent_id=payload.agent_id,
        compute_backend_id=payload.compute_backend_id,
    )
    return RunResponse.model_validate(run)


@router.post(
    "/{seed_id}/select-run/{run_id}",
    response_model=SeedResponse,
)
async def select_seed_run(
    seed_id: str,
    run_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedResponse:
    """Select which agent run's draft (or 'original') is active on the seed for Ideate or Plan."""
    seed = await seeds_service.select_seed_run(
        session,
        current_user,
        seed_id,
        run_id,
    )
    return SeedResponse.model_validate(seed)


# ---------------------------------------------------------------------------
# Stage Gate Approvals
# ---------------------------------------------------------------------------


@router.get("/{seed_id}/approvals", response_model=list[ApprovalResponse])
async def list_seed_approvals(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[ApprovalResponse]:
    """List stage gate approval decisions on a seed."""
    approvals = await seeds_service.list_approvals(session, current_user, seed_id)
    return [ApprovalResponse.model_validate(a) for a in approvals]


@router.post(
    "/{seed_id}/approvals",
    response_model=ApprovalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_seed_approval(
    seed_id: str,
    payload: CreateApprovalRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ApprovalResponse:
    """Submit a stage gate decision (approved, changes_requested, or rejected)."""
    approval = await seeds_service.create_approval(
        session,
        current_user,
        seed_id,
        decision=payload.decision,
        comment=payload.comment,
    )
    return ApprovalResponse.model_validate(approval)


# ---------------------------------------------------------------------------
# Seed Tasks
# ---------------------------------------------------------------------------


@router.get("/{seed_id}/tasks", response_model=list[SeedTaskResponse])
async def list_seed_tasks(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[SeedTaskResponse]:
    """List plan tasks for a seed ordered by position."""
    tasks = await seeds_service.list_tasks(session, current_user, seed_id)
    return [SeedTaskResponse.model_validate(t) for t in tasks]


@router.post(
    "/{seed_id}/tasks",
    response_model=SeedTaskResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_seed_task(
    seed_id: str,
    payload: CreateSeedTaskRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedTaskResponse:
    """Add a task to a seed's plan."""
    task = await seeds_service.create_task(
        session,
        current_user,
        seed_id,
        title=payload.title,
        description=payload.description,
        position=payload.position,
    )
    return SeedTaskResponse.model_validate(task)


@router.patch("/{seed_id}/tasks/{task_id}", response_model=SeedTaskResponse)
async def update_seed_task(
    seed_id: str,
    task_id: str,
    payload: UpdateSeedTaskRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedTaskResponse:
    """Update a seed task's title, description, position, or status."""
    task = await seeds_service.update_task(
        session,
        current_user,
        seed_id,
        task_id,
        title=payload.title,
        description=payload.description,
        position=payload.position,
        status=payload.status,
    )
    return SeedTaskResponse.model_validate(task)


@router.delete(
    "/{seed_id}/tasks/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_seed_task(
    seed_id: str,
    task_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> None:
    """Delete a task from a seed and compact remaining task positions."""
    await seeds_service.delete_task(session, current_user, seed_id, task_id)


# ---------------------------------------------------------------------------
# Seed Comments
# ---------------------------------------------------------------------------


@router.get("/{seed_id}/comments", response_model=list[SeedCommentResponse])
async def list_seed_comments(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[SeedCommentResponse]:
    """List comments on a seed."""
    comments = await seeds_service.list_comments(session, current_user, seed_id)
    return [_serialize_comment(c) for c in comments]


@router.post(
    "/{seed_id}/comments",
    response_model=SeedCommentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_seed_comment(
    seed_id: str,
    payload: CreateSeedCommentRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedCommentResponse:
    """Post a comment on a seed."""
    comment = await seeds_service.create_comment(
        session,
        current_user,
        seed_id,
        body=payload.body,
    )
    return _serialize_comment(comment)


@router.patch(
    "/{seed_id}/comments/{comment_id}",
    response_model=SeedCommentResponse,
)
async def update_seed_comment(
    seed_id: str,
    comment_id: str,
    payload: UpdateSeedCommentRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedCommentResponse:
    """Edit a comment on a seed."""
    comment = await seeds_service.update_comment(
        session,
        current_user,
        seed_id,
        comment_id,
        body=payload.body,
    )
    return _serialize_comment(comment)


@router.delete(
    "/{seed_id}/comments/{comment_id}",
    response_model=SeedCommentResponse,
)
async def delete_seed_comment(
    seed_id: str,
    comment_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> SeedCommentResponse:
    """Soft-delete a comment on a seed."""
    comment = await seeds_service.delete_comment(
        session, current_user, seed_id, comment_id
    )
    return _serialize_comment(comment)


@router.get(
    "/{seed_id}/changes",
    response_model=SeedChangesResponse,
)
async def get_seed_changes(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
    repo_id: int | None = None,
) -> SeedChangesResponse:
    """Retrieve the commits and file diffs attributed to a seed (live branch or post-merge)."""
    result = await git_browse_service.get_seed_changes(
        session,
        current_user,
        seed_id,
        repo_id=repo_id,
    )
    return SeedChangesResponse.model_validate(result)


@router.post(
    "/{seed_id}/pull-request",
    response_model=PullRequestStatusResponse,
)
async def create_or_sync_seed_pull_request(
    seed_id: str,
    current_user: CurrentUser,
    session: DBSession,
    repo_id: int | None = None,
) -> PullRequestStatusResponse:
    """Ensure a Pull Request / Merge Request exists for this seed's branch and return its live status."""
    result = await git_merge_service.create_or_sync_seed_pull_request(
        session,
        current_user,
        seed_id,
        repo_id=repo_id,
    )
    return PullRequestStatusResponse.model_validate(result)


@router.post(
    "/{seed_id}/pull-request/comments",
    response_model=PullRequestCommentItem,
    status_code=status.HTTP_201_CREATED,
)
async def create_seed_pull_request_comment(
    seed_id: str,
    payload: CreatePullRequestCommentRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> PullRequestCommentItem:
    """Post a comment directly to the seed's Pull Request / Merge Request."""
    result = await git_merge_service.post_seed_pull_request_comment(
        session,
        current_user,
        seed_id,
        body=payload.body,
        repo_id=payload.repo_id,
        mirror_to_seed=payload.mirror_to_seed,
    )
    return PullRequestCommentItem.model_validate(result)


