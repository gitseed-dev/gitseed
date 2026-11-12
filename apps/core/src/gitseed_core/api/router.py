from fastapi import APIRouter, Depends

from gitseed_core.api.deps import get_current_user
from gitseed_core.api.v1.activity import router as activity_router
from gitseed_core.api.v1.agent_sessions import pty_router as agent_sessions_pty_router
from gitseed_core.api.v1.agent_sessions import router as agent_sessions_router
from gitseed_core.api.v1.agents import router as agents_router
from gitseed_core.api.v1.auth import router as auth_router
from gitseed_core.api.v1.backends import public_router as backends_public_router
from gitseed_core.api.v1.backends import router as backends_router
from gitseed_core.api.v1.events import router as events_router
from gitseed_core.api.v1.local_git import router as local_git_router
from gitseed_core.api.v1.policies import router as policies_router
from gitseed_core.api.v1.projects import router as projects_router
from gitseed_core.api.v1.receipts import router as receipts_router
from gitseed_core.api.v1.runner import router as runner_router
from gitseed_core.api.v1.runs import router as runs_router
from gitseed_core.api.v1.sandboxes import pty_router as sandboxes_pty_router
from gitseed_core.api.v1.sandboxes import router as sandboxes_router
from gitseed_core.api.v1.seed_attachments import router as seed_attachments_router
from gitseed_core.api.v1.seed_links import router as seed_links_router
from gitseed_core.api.v1.seeds import router as seeds_router
from gitseed_core.api.v1.sessions import router as sessions_router
from gitseed_core.api.v1.settings import router as settings_router
from gitseed_core.api.v1.skills import router as skills_router
from gitseed_core.api.v1.users import router as users_router
from gitseed_core.api.v1.vscode import router as vscode_router
from gitseed_core.api.v1.webhooks import router as webhooks_router

api_v1_router = APIRouter(prefix="/api/v1")

# Auth router handles its own public (/login) vs protected (/logout, /me, /tokens) routes
api_v1_router.include_router(auth_router)

# Runner bridge authenticates via signed per-run tokens (gsr_...)
api_v1_router.include_router(runner_router)

# Public webhook receivers verify HMAC / token signatures per-project
api_v1_router.include_router(webhooks_router)

# SSE stream authenticates via cookie, Bearer header, or ?token= query param
api_v1_router.include_router(events_router)

# Interactive terminal WebSockets authenticate during the handshake themselves
# (router-level HTTP dependencies can't run on WebSocket connections)
api_v1_router.include_router(sandboxes_pty_router)
api_v1_router.include_router(agent_sessions_pty_router)
# On-demand VS Code Web proxy gates HTTP + WebSockets with a per-session cryptographic token
api_v1_router.include_router(vscode_router)
# Local Git Smart HTTP endpoints for container & desktop git clone/fetch/push
api_v1_router.include_router(local_git_router)
# Governance receipts (/receipts/public-key is public; /seeds/{seed_id}/receipt requires CurrentUser)
api_v1_router.include_router(receipts_router)
# GitHub App manifest & setup browser redirects verify cryptographic state / App JWT ownership
api_v1_router.include_router(backends_public_router)

# All other v1 routers require authentication on every request
_auth_required = [Depends(get_current_user)]
api_v1_router.include_router(activity_router, dependencies=_auth_required)
api_v1_router.include_router(agent_sessions_router, dependencies=_auth_required)
api_v1_router.include_router(agents_router, dependencies=_auth_required)
api_v1_router.include_router(backends_router, dependencies=_auth_required)
api_v1_router.include_router(policies_router, dependencies=_auth_required)
api_v1_router.include_router(projects_router, dependencies=_auth_required)
api_v1_router.include_router(runs_router, dependencies=_auth_required)
api_v1_router.include_router(sandboxes_router, dependencies=_auth_required)
api_v1_router.include_router(seeds_router, dependencies=_auth_required)
api_v1_router.include_router(seed_attachments_router, dependencies=_auth_required)
api_v1_router.include_router(seed_links_router, dependencies=_auth_required)
api_v1_router.include_router(sessions_router, dependencies=_auth_required)
api_v1_router.include_router(settings_router, dependencies=_auth_required)
api_v1_router.include_router(skills_router, dependencies=_auth_required)
api_v1_router.include_router(users_router, dependencies=_auth_required)
