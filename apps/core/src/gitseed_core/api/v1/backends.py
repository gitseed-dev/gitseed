import json
from typing import Any

from fastapi import APIRouter, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from gitseed_common.enums import BackendType
from gitseed_common.schemas import (
    BackendResponse,
    CliAuthInputRequest,
    CliAuthSessionResponse,
    CreateBackendRequest,
    CreateRemoteRepoRequest,
    CreateRemoteRepoResponse,
    GitHubAppManifestStartRequest,
    GitHubAppManifestStartResponse,
    ProbeBackendRequest,
    ProbeBackendResponse,
    UpdateBackendRequest,
)
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.api.deps import AdminUser, CurrentUser, DBSession
from gitseed_core.models.backend import Backend
from gitseed_core.services import backends as backends_service
from gitseed_core.services import cli_auth as cli_auth_service

router = APIRouter(prefix="/backends", tags=["Backends"])


async def _serialize_backend(
    session: AsyncSession,
    backend: Backend,
    allowed_map: dict[str, list[str]] | None = None,
) -> BackendResponse:
    if allowed_map is not None:
        allowed_ids = allowed_map.get(backend.id, [])
    else:
        allowed_ids = await backends_service.get_allowed_agent_ids(session, backend.id)
    return BackendResponse.model_validate(backend).model_copy(
        update={
            "secret_keys": backends_service.get_secret_keys(backend),
            "allowed_agent_ids": allowed_ids,
        }
    )


@router.get("", response_model=list[BackendResponse])
async def list_backends(
    _current_user: CurrentUser,
    session: DBSession,
    type: BackendType | None = None,
    enabled: bool | None = None,
) -> list[BackendResponse]:
    """List registered backends, optionally filtered by type or enabled state."""
    backends = await backends_service.list_backends(
        session, type=type, enabled=enabled
    )
    allowed_map = await backends_service.get_allowed_agent_ids_map(
        session, [b.id for b in backends]
    )
    return [await _serialize_backend(session, b, allowed_map) for b in backends]


@router.post(
    "",
    response_model=BackendResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_backend(
    payload: CreateBackendRequest,
    admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Register a new backend and validate required secrets for its provider."""
    backend = await backends_service.create_backend(
        session,
        admin,
        name=payload.name,
        type=payload.type,
        provider=payload.provider,
        endpoint=payload.endpoint,
        config=payload.config,
        secrets=payload.secrets,
        capacity=payload.capacity,
        access_restricted=payload.access_restricted,
        enabled=payload.enabled,
    )
    return await _serialize_backend(session, backend)


@router.post("/probe", response_model=ProbeBackendResponse)
async def probe_backend_config(
    payload: ProbeBackendRequest,
    _admin: AdminUser,
) -> ProbeBackendResponse:
    """Test connectivity for a backend configuration before registering it."""
    health, latency_ms, error_msg = await backends_service.probe_backend_config(
        type=payload.type,
        provider=payload.provider,
        endpoint=payload.endpoint,
        config=payload.config,
        secrets=payload.secrets,
    )
    return ProbeBackendResponse(
        health=health,
        latency_ms=latency_ms,
        error=error_msg,
    )


@router.post(
    "/github-app/manifest-start",
    response_model=GitHubAppManifestStartResponse,
)
async def start_github_app_manifest(
    payload: GitHubAppManifestStartRequest,
    request: Request,
    admin: AdminUser,
    session: DBSession,
) -> GitHubAppManifestStartResponse:
    """Start a 1-click GitHub App Manifest registration flow."""
    fallback_origin = f"{request.url.scheme}://{request.headers.get('host') or request.url.netloc}"
    res = await backends_service.start_github_app_manifest(
        session,
        admin,
        backend_id=payload.backend_id,
        name=payload.name,
        organization=payload.organization,
        endpoint=payload.endpoint,
        origin=payload.origin or fallback_origin,
    )
    serialized = await _serialize_backend(session, res["backend"])
    return GitHubAppManifestStartResponse(
        action_url=res["action_url"],
        manifest=res["manifest"],
        state=res["state"],
        backend=serialized,
    )


@router.get("/github-app/callback")
async def github_app_manifest_callback(
    code: str,
    state: str,
    session: DBSession,
) -> RedirectResponse:
    """Exchange GitHub's manifest `code` for credentials and redirect to the App install screen."""
    _backend, install_url = await backends_service.complete_github_app_manifest_callback(
        session, code=code, state=state
    )
    return RedirectResponse(url=install_url, status_code=status.HTTP_302_FOUND)


@router.get("/github-app/setup", response_class=HTMLResponse)
async def github_app_setup_callback(
    session: DBSession,
    installation_id: str | None = None,
    state: str | None = None,
) -> HTMLResponse:
    """Bind `installation_id` after the user installs the newly created GitHub App."""
    backend = None
    if installation_id:
        backend = await backends_service.complete_github_app_setup(
            session, installation_id=installation_id, state=state
        )
    backend_id = backend.id if backend else ""
    payload_json = json.dumps(
        {
            "type": "gitseed:github-app-connected",
            "backendId": backend_id,
            "installationId": installation_id or "",
        }
    )
    return_url = f"/backends?open={backend_id}" if backend_id else "/backends"
    html = f"""<!doctype html>
<html>
<head>
  <meta charset="utf-8" />
  <title>GitHub App Connected · GitSeed</title>
  <style>
    body {{
      margin: 0;
      min-height: 100vh;
      display: flex;
      align-items: center;
      justify-content: center;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
      background: #09090b;
      color: #f4f4f5;
    }}
    .card {{
      max-width: 400px;
      padding: 28px;
      border-radius: 16px;
      background: #18181b;
      border: 1px solid #27272a;
      text-align: center;
    }}
    h1 {{ font-size: 16px; margin: 0 0 8px; }}
    p {{ font-size: 13px; color: #a1a1aa; margin: 0 0 18px; line-height: 1.5; }}
    a {{
      display: inline-block;
      padding: 8px 16px;
      border-radius: 10px;
      background: #0d9488;
      color: #fff;
      text-decoration: none;
      font-size: 12px;
      font-weight: 600;
    }}
  </style>
</head>
<body>
  <div class="card">
    <h1>GitHub App Connected</h1>
    <p>Credentials and repository installation have been saved to GitSeed. You can close this window.</p>
    <a href="{return_url}">Return to GitSeed</a>
  </div>
  <script>
    try {{
      if (window.opener) {{
        window.opener.postMessage({payload_json}, window.location.origin);
        setTimeout(() => window.close(), 600);
      }} else {{
        setTimeout(() => {{ window.location.href = {json.dumps(return_url)}; }}, 800);
      }}
    }} catch (e) {{}}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@router.get("/{backend_id}", response_model=BackendResponse)
async def get_backend(
    backend_id: str,
    _current_user: CurrentUser,
    session: DBSession,
) -> BackendResponse:
    """Retrieve a single backend by ID."""
    backend = await backends_service.get_backend(session, backend_id)
    return await _serialize_backend(session, backend)


@router.patch("/{backend_id}", response_model=BackendResponse)
async def update_backend(
    backend_id: str,
    payload: UpdateBackendRequest,
    admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Update a backend's configuration or rotate its secrets."""
    backend = await backends_service.update_backend(
        session,
        admin,
        backend_id,
        name=payload.name,
        endpoint=payload.endpoint,
        config=payload.config,
        secrets=payload.secrets,
        capacity=payload.capacity,
        access_restricted=payload.access_restricted,
        enabled=payload.enabled,
        fields_set=payload.model_fields_set,
    )
    return await _serialize_backend(session, backend)


@router.delete("/{backend_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_backend(
    backend_id: str,
    _admin: AdminUser,
    session: DBSession,
) -> None:
    """Deregister a backend if nothing depends on it."""
    await backends_service.delete_backend(session, backend_id)


@router.post("/{backend_id}/agents/{agent_id}", response_model=BackendResponse)
async def grant_agent_access(
    backend_id: str,
    agent_id: str,
    admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Allow an agent to use a restricted backend."""
    await backends_service.grant_agent_access(session, admin, backend_id, agent_id)
    backend = await backends_service.get_backend(session, backend_id)
    return await _serialize_backend(session, backend)


@router.delete("/{backend_id}/agents/{agent_id}", response_model=BackendResponse)
async def revoke_agent_access(
    backend_id: str,
    agent_id: str,
    admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Revoke an agent's access to a restricted backend."""
    await backends_service.revoke_agent_access(session, admin, backend_id, agent_id)
    backend = await backends_service.get_backend(session, backend_id)
    return await _serialize_backend(session, backend)


@router.post("/{backend_id}/test", response_model=BackendResponse)
async def test_backend_connection(
    backend_id: str,
    _admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Execute a live connectivity probe against a backend and update its health."""
    backend = await backends_service.test_backend_connection(session, backend_id)
    return await _serialize_backend(session, backend)


@router.post("/{backend_id}/discover-repos", response_model=BackendResponse)
async def discover_backend_repositories(
    backend_id: str,
    current_user: CurrentUser,
    session: DBSession,
) -> BackendResponse:
    """Discover repositories from a Git backend and merge them into its configuration."""
    backend = await backends_service.discover_backend_repositories(
        session, backend_id, actor=current_user
    )
    return await _serialize_backend(session, backend)


@router.post(
    "/{backend_id}/create-repo",
    response_model=CreateRemoteRepoResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_backend_repository(
    backend_id: str,
    payload: CreateRemoteRepoRequest,
    current_user: CurrentUser,
    session: DBSession,
) -> CreateRemoteRepoResponse:
    """Create a new remote repository on a Git backend and merge it into its configuration."""
    backend, created = await backends_service.create_remote_repository(
        session,
        backend_id,
        actor=current_user,
        name=payload.name,
        description=payload.description,
        private=payload.private,
        auto_init=payload.auto_init,
    )
    serialized_backend = await _serialize_backend(session, backend)
    return CreateRemoteRepoResponse(
        full_name=created["full_name"],
        default_branch=created.get("default_branch") or "main",
        private=bool(created.get("private", True)),
        web_url=created.get("web_url") or None,
        backend=serialized_backend,
    )


@router.post("/{backend_id}/discover-models", response_model=BackendResponse)
async def discover_backend_models(
    backend_id: str,
    _admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Discover models from an Inference backend and merge them into its configuration."""
    backend = await backends_service.discover_backend_models(session, backend_id)
    return await _serialize_backend(session, backend)


async def _serialize_cli_auth_result(
    session: AsyncSession,
    res: dict[str, Any],
) -> CliAuthSessionResponse:
    backend_obj = res.get("backend")
    serialized_backend = (
        await _serialize_backend(session, backend_obj)
        if isinstance(backend_obj, Backend)
        else None
    )
    return CliAuthSessionResponse(
        status=str(res.get("status") or "idle"),
        auth_url=res.get("auth_url"),
        user_code=res.get("user_code"),
        log_output=str(res.get("log_output") or ""),
        authenticated=bool(res.get("authenticated", False)),
        error=res.get("error"),
        backend=serialized_backend,
    )


@router.post("/{backend_id}/cli-auth/start", response_model=CliAuthSessionResponse)
async def start_backend_cli_auth(
    backend_id: str,
    admin: AdminUser,
    session: DBSession,
) -> CliAuthSessionResponse:
    """Start an interactive CLI OAuth or GitHub Device OAuth login session."""
    backend = await backends_service.get_backend(session, backend_id)
    if backend.type == BackendType.GIT and backend.provider in ("github", "github-enterprise"):
        res = await backends_service.start_github_device_flow(
            session,
            admin,
            backend_id=backend.id,
            name=backend.name,
            provider=backend.provider,
            endpoint=backend.endpoint,
        )
        return await _serialize_cli_auth_result(session, res)
    res = await cli_auth_service.start_cli_auth_session(session, admin, backend_id)
    return await _serialize_cli_auth_result(session, res)


@router.get("/{backend_id}/cli-auth/status", response_model=CliAuthSessionResponse)
async def get_backend_cli_auth_status(
    backend_id: str,
    admin: AdminUser,
    session: DBSession,
) -> CliAuthSessionResponse:
    """Poll the status and logs of an active CLI OAuth or GitHub Device OAuth login session."""
    backend = await backends_service.get_backend(session, backend_id)
    if backend.type == BackendType.GIT and backend.provider in ("github", "github-enterprise"):
        res = await backends_service.poll_github_device_flow(session, admin, backend.id)
        return await _serialize_cli_auth_result(session, res)
    res = await cli_auth_service.get_cli_auth_status(session, admin, backend_id)
    return await _serialize_cli_auth_result(session, res)


@router.post("/{backend_id}/cli-auth/submit", response_model=CliAuthSessionResponse)
async def submit_backend_cli_auth_code(
    backend_id: str,
    payload: CliAuthInputRequest,
    admin: AdminUser,
    session: DBSession,
) -> CliAuthSessionResponse:
    """Submit a verification code, callback URL, or setup token to complete CLI OAuth login."""
    res = await cli_auth_service.submit_cli_auth_code(
        session, admin, backend_id, payload.code
    )
    return await _serialize_cli_auth_result(session, res)


@router.post("/{backend_id}/cli-auth/cancel", response_model=CliAuthSessionResponse)
async def cancel_backend_cli_auth(
    backend_id: str,
    admin: AdminUser,
    session: DBSession,
) -> CliAuthSessionResponse:
    """Cancel and clean up any active CLI OAuth login helper container."""
    res = await cli_auth_service.cancel_cli_auth_session(session, admin, backend_id)
    return await _serialize_cli_auth_result(session, res)


@router.post("/{backend_id}/github-app/sync", response_model=BackendResponse)
async def sync_github_app_backend(
    backend_id: str,
    _admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Auto-discover the GitHub App installation ID and repositories if installed."""
    backend = await backends_service.sync_github_app_installation(session, backend_id)
    return await _serialize_backend(session, backend)


@router.post("/{backend_id}/sdlc-default", response_model=BackendResponse)
async def set_sdlc_default_backend(
    backend_id: str,
    admin: AdminUser,
    session: DBSession,
) -> BackendResponse:
    """Set an Inference backend as the primary SDLC default and provision/switch the 5 stage agents."""
    backend = await backends_service.set_sdlc_default_backend(
        session, admin, backend_id
    )
    return await _serialize_backend(session, backend)





