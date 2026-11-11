from fastapi import APIRouter, Query, status
from gitseed_common.enums import OrgRole, ProjectRole
from gitseed_common.schemas.project import (
    AddProjectMemberRequest,
    AddProjectRepositoryRequest,
    CreateProjectRequest,
    InitLocalProjectRepositoryRequest,
    InitLocalProjectRepositoryResponse,
    ProjectMemberResponse,
    ProjectRepositoryResponse,
    ProjectResponse,
    ProjectWebhookConfigResponse,
    RepoBlobResponse,
    RepoBranchesResponse,
    RepoTreeResponse,
    UpdateProjectMemberRequest,
    UpdateProjectRepositoryRequest,
    UpdateProjectRequest,
    UpdateProjectWebhookConfigRequest,
    WebhookTestResponse,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.api.deps import CurrentUser, DBSession
from gitseed_core.models.project import Project, ProjectMember
from gitseed_core.models.user import User
from gitseed_core.services import git_browse
from gitseed_core.services import local_git as local_git_service
from gitseed_core.services import projects as projects_service
from gitseed_core.services import webhooks as webhooks_service

router = APIRouter(prefix="/projects", tags=["Projects"])


async def _to_project_response(
    session: AsyncSession,
    project: Project,
    actor: User,
    backends_map: dict[str, list[str]] | None = None,
    roles_map: dict[str, ProjectRole] | None = None,
) -> ProjectResponse:
    if backends_map is not None:
        allowed_ids = backends_map.get(project.id, [])
    else:
        allowed_ids = await projects_service.get_allowed_backend_ids(
            session, project.id
        )
    if roles_map is not None:
        my_role = roles_map.get(project.id)
    else:
        my_role = await projects_service.get_member_role(session, project.id, actor.id)
    if actor.org_role == OrgRole.ADMIN:
        my_role = ProjectRole.MAINTAINER
    return ProjectResponse.model_validate(project).model_copy(
        update={
            "allowed_backend_ids": allowed_ids,
            "my_role": my_role,
            "sandbox_config": webhooks_service.sanitize_sandbox_config_for_response(
                project.sandbox_config
            ),
        }
    )


def _to_member_response(member: ProjectMember, user: User) -> ProjectMemberResponse:
    return ProjectMemberResponse.model_validate(member).model_copy(
        update={
            "email": user.email,
            "name": user.name,
            "preferred_name": user.preferred_name,
        }
    )


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: CurrentUser,
    session: DBSession,
    include_archived: bool = False,
    tag: str | None = None,
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
) -> list[ProjectResponse]:
    """List projects visible to the current user."""
    projects = await projects_service.list_projects(
        session,
        current_user,
        include_archived=include_archived,
        tag=tag,
        limit=limit,
        offset=offset,
    )
    project_ids = [p.id for p in projects]
    backends_map = await projects_service.get_allowed_backend_ids_map(
        session, project_ids
    )
    roles_map = await projects_service.get_member_roles_map(
        session, project_ids, current_user.id
    )
    return [
        await _to_project_response(session, p, current_user, backends_map, roles_map)
        for p in projects
    ]


@router.post(
    "",
    response_model=ProjectResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_project(
    payload: CreateProjectRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Create a new project and add the creator as a project maintainer."""
    project = await projects_service.create_project(
        session,
        current_user,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        tags=payload.tags,
        visibility=payload.visibility,
        monthly_budget=payload.monthly_budget,
        seed_budget_cap=payload.seed_budget_cap,
        spend_checkpoint=payload.spend_checkpoint,
        max_concurrent_runs=payload.max_concurrent_runs,
        backends_restricted=payload.backends_restricted,
    )
    return await _to_project_response(session, project, current_user)


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Retrieve a project by ID or slug."""
    project = await projects_service.get_project(session, current_user, project_id)
    return await _to_project_response(session, project, current_user)


@router.patch("/{project_id}", response_model=ProjectResponse)
async def update_project(
    project_id: str,
    payload: UpdateProjectRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Update a project's metadata, visibility, or budget/concurrency overrides."""
    project = await projects_service.update_project(
        session,
        current_user,
        project_id,
        name=payload.name,
        slug=payload.slug,
        description=payload.description,
        tags=payload.tags,
        visibility=payload.visibility,
        monthly_budget=payload.monthly_budget,
        seed_budget_cap=payload.seed_budget_cap,
        spend_checkpoint=payload.spend_checkpoint,
        max_concurrent_runs=payload.max_concurrent_runs,
        backends_restricted=payload.backends_restricted,
        sandbox_config=payload.sandbox_config,
        fields_set=payload.model_fields_set,
    )
    return await _to_project_response(session, project, current_user)


@router.post("/{project_id}/archive", response_model=ProjectResponse)
async def archive_project(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Archive a project."""
    project = await projects_service.archive_project(session, current_user, project_id)
    return await _to_project_response(session, project, current_user)


@router.post("/{project_id}/restore", response_model=ProjectResponse)
async def restore_project(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Restore an archived project."""
    project = await projects_service.restore_project(session, current_user, project_id)
    return await _to_project_response(session, project, current_user)


@router.get("/{project_id}/members", response_model=list[ProjectMemberResponse])
async def list_project_members(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[ProjectMemberResponse]:
    """List members of a project."""
    rows = await projects_service.list_members(session, current_user, project_id)
    return [_to_member_response(member, user) for member, user in rows]


@router.post(
    "/{project_id}/members",
    response_model=ProjectMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_member(
    project_id: str,
    payload: AddProjectMemberRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectMemberResponse:
    """Add a user to a project with a role."""
    member, user = await projects_service.add_member(
        session,
        current_user,
        project_id,
        user_id=payload.user_id,
        role=payload.role,
    )
    return _to_member_response(member, user)


@router.patch(
    "/{project_id}/members/{user_id}",
    response_model=ProjectMemberResponse,
)
async def update_project_member_role(
    project_id: str,
    user_id: str,
    payload: UpdateProjectMemberRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectMemberResponse:
    """Change a project member's role."""
    member, user = await projects_service.update_member_role(
        session,
        current_user,
        project_id,
        user_id,
        role=payload.role,
    )
    return _to_member_response(member, user)


@router.delete(
    "/{project_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_project_member(
    project_id: str,
    user_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> None:
    """Remove a user from a project."""
    await projects_service.remove_member(session, current_user, project_id, user_id)


@router.get(
    "/{project_id}/repositories",
    response_model=list[ProjectRepositoryResponse],
)
async def list_project_repositories(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> list[ProjectRepositoryResponse]:
    """List Git repositories attached to a project."""
    repos = await projects_service.list_repositories(session, current_user, project_id)
    return [ProjectRepositoryResponse.model_validate(r) for r in repos]


@router.post(
    "/{project_id}/repositories",
    response_model=ProjectRepositoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def add_project_repository(
    project_id: str,
    payload: AddProjectRepositoryRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectRepositoryResponse:
    """Attach a Git repository to a project."""
    repo = await projects_service.add_repository(
        session,
        current_user,
        project_id,
        backend_id=payload.backend_id,
        full_name=payload.full_name,
        default_branch=payload.default_branch,
        is_primary=payload.is_primary,
    )
    return ProjectRepositoryResponse.model_validate(repo)


@router.post(
    "/{project_id}/repositories/init-local",
    response_model=InitLocalProjectRepositoryResponse,
    status_code=status.HTTP_201_CREATED,
)
async def init_local_project_repository(
    project_id: str,
    payload: InitLocalProjectRepositoryRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> InitLocalProjectRepositoryResponse:
    """Initialize a built-in Local Git bare repository (and optional local folder) and attach it to the project."""
    res = await local_git_service.init_project_local_repository(
        session,
        current_user,
        project_id,
        workspace_path=payload.workspace_path,
        default_branch=payload.default_branch,
    )
    return InitLocalProjectRepositoryResponse(
        repository=ProjectRepositoryResponse.model_validate(res["repository"]),
        backend_id=res["backend_id"],
        bare_repo_path=res["bare_repo_path"],
        workspace_path=res["workspace_path"],
        http_clone_url=res["http_clone_url"],
        initialized_git=bool(res["initialized_git"]),
    )



@router.patch(
    "/{project_id}/repositories/{repo_id}",
    response_model=ProjectRepositoryResponse,
)
async def update_project_repository(
    project_id: str,
    repo_id: int,
    payload: UpdateProjectRepositoryRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectRepositoryResponse:
    """Update a repository's default branch or promote it to primary."""
    repo = await projects_service.update_repository(
        session,
        current_user,
        project_id,
        repo_id,
        default_branch=payload.default_branch,
        is_primary=payload.is_primary,
    )
    return ProjectRepositoryResponse.model_validate(repo)


@router.delete(
    "/{project_id}/repositories/{repo_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_project_repository(
    project_id: str,
    repo_id: int,
    current_user: CurrentUser,
    session: DBSession,
) -> None:
    """Detach a Git repository from a project."""
    await projects_service.remove_repository(session, current_user, project_id, repo_id)


@router.get(
    "/{project_id}/repositories/{repo_id}/branches",
    response_model=RepoBranchesResponse,
)
async def list_project_repository_branches(
    project_id: str,
    repo_id: int,
    current_user: CurrentUser,
    session: DBSession,
) -> RepoBranchesResponse:
    """List branches for a linked project repository."""
    data = await git_browse.list_repo_branches(
        session, current_user, project_id, repo_id
    )
    return RepoBranchesResponse.model_validate(data)


@router.get(
    "/{project_id}/repositories/{repo_id}/tree",
    response_model=RepoTreeResponse,
)
async def get_project_repository_tree(
    project_id: str,
    repo_id: int,
    current_user: CurrentUser,
    session: DBSession,
    ref: str | None = None,
    path: str = "",
) -> RepoTreeResponse:
    """List files and folders at a given path and branch/ref in a project repository."""
    data = await git_browse.get_repo_tree(
        session, current_user, project_id, repo_id, ref=ref, path=path
    )
    return RepoTreeResponse.model_validate(data)


@router.get(
    "/{project_id}/repositories/{repo_id}/blob",
    response_model=RepoBlobResponse,
)
async def get_project_repository_blob(
    project_id: str,
    repo_id: int,
    path: str,
    current_user: CurrentUser,
    session: DBSession,
    ref: str | None = None,
) -> RepoBlobResponse:
    """Fetch a single file's content at a given path and branch/ref in a project repository."""
    data = await git_browse.get_repo_blob(
        session, current_user, project_id, repo_id, ref=ref, path=path
    )
    return RepoBlobResponse.model_validate(data)



@router.post(
    "/{project_id}/backends/{backend_id}",
    response_model=ProjectResponse,
)
async def allow_project_backend(
    project_id: str,
    backend_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Allow a project to use a specific backend when backends_restricted is True."""
    await projects_service.allow_project_backend(
        session, current_user, project_id, backend_id
    )
    project = await projects_service.get_project(session, current_user, project_id)
    return await _to_project_response(session, project, current_user)


@router.delete(
    "/{project_id}/backends/{backend_id}",
    response_model=ProjectResponse,
)
async def disallow_project_backend(
    project_id: str,
    backend_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectResponse:
    """Remove a backend from a project's allowed backend list."""
    await projects_service.disallow_project_backend(
        session, current_user, project_id, backend_id
    )
    project = await projects_service.get_project(session, current_user, project_id)
    return await _to_project_response(session, project, current_user)


@router.get(
    "/{project_id}/webhooks",
    response_model=ProjectWebhookConfigResponse,
)
async def get_project_webhooks(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectWebhookConfigResponse:
    """Retrieve webhook and PR automation settings for a project."""
    return await webhooks_service.get_project_webhook_config(
        session, current_user, project_id
    )


@router.patch(
    "/{project_id}/webhooks",
    response_model=ProjectWebhookConfigResponse,
)
async def update_project_webhooks(
    project_id: str,
    payload: UpdateProjectWebhookConfigRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectWebhookConfigResponse:
    """Update webhook and PR automation settings for a project."""
    return await webhooks_service.update_project_webhook_config(
        session, current_user, project_id, payload
    )


@router.post(
    "/{project_id}/webhooks/rotate-secret",
    response_model=ProjectWebhookConfigResponse,
)
async def rotate_project_webhook_secret(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> ProjectWebhookConfigResponse:
    """Generate and rotate the HMAC webhook signing secret for a project."""
    return await webhooks_service.rotate_project_webhook_secret(
        session, current_user, project_id
    )


@router.post(
    "/{project_id}/webhooks/test",
    response_model=WebhookTestResponse,
)
async def test_project_outbound_webhook(
    project_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> WebhookTestResponse:
    """Send a signed test ping payload to the project's outbound webhook URL."""
    return await webhooks_service.send_test_outbound_webhook(
        session, current_user, project_id
    )

