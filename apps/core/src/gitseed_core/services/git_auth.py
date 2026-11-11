from __future__ import annotations

import logging
from typing import Any

import httpx

from gitseed_core.models.backend import Backend
from gitseed_core.security import create_github_app_jwt, create_run_token, decrypt_secrets
from gitseed_core.services.errors import Invalid
from gitseed_core.services.local_git import extract_repo_slug


class UnscopedLocalGitTokenError(Invalid, ValueError):
    """Raised when attempting to mint a Local Git token without explicit repository slugs."""

logger = logging.getLogger("uvicorn.error")


async def mint_github_app_installation_token(
    backend: Backend,
    secrets_map: dict[str, str],
    repo_full_names: list[str] | None = None,
    *,
    write_access: bool = False,
) -> str | None:
    """Mint a short-lived GitHub App installation token scoped to specific repos and permissions."""
    if secrets_map.get("token") and not secrets_map.get("private_key"):
        return secrets_map["token"]

    private_key = secrets_map.get("private_key", "")
    cfg = backend.config or {}
    app_id = str(cfg.get("app_id") or secrets_map.get("app_id") or "").strip()
    installation_id = str(
        cfg.get("installation_id") or secrets_map.get("installation_id") or ""
    ).strip()
    if not private_key or not app_id:
        return secrets_map.get("token")

    api_base = (backend.endpoint or "https://api.github.com").strip().rstrip("/")
    try:
        jwt_token = create_github_app_jwt(app_id, private_key)
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jwt_token}",
            "X-GitHub-Api-Version": "2022-11-28",
        }
        async with httpx.AsyncClient(timeout=8.0) as client:
            if not installation_id:
                inst_resp = await client.get(
                    f"{api_base}/app/installations", headers=headers
                )
                if 200 <= inst_resp.status_code < 300 and isinstance(
                    inst_resp.json(), list
                ):
                    installations = inst_resp.json()
                    if installations and isinstance(installations[0], dict):
                        installation_id = str(installations[0].get("id") or "")
            if not installation_id:
                return secrets_map.get("token")

            repo_names = [
                fn.split("/", 1)[1] if "/" in fn else fn
                for fn in (repo_full_names or [])
                if fn and fn.strip()
            ]
            permissions = (
                {"contents": "write", "pull_requests": "write", "metadata": "read"}
                if write_access
                else {"contents": "read", "metadata": "read"}
            )
            body: dict[str, Any] = {"permissions": permissions}
            if repo_names:
                body["repositories"] = repo_names

            tok_resp = await client.post(
                f"{api_base}/app/installations/{installation_id}/access_tokens",
                headers=headers,
                json=body,
            )
            if 200 <= tok_resp.status_code < 300 and isinstance(tok_resp.json(), dict):
                tok = tok_resp.json().get("token")
                if isinstance(tok, str) and tok:
                    return tok
    except Exception as exc:
        logger.warning("Failed to mint GitHub App installation token: %s", exc)

    return secrets_map.get("token")


async def resolve_git_backend_token(
    backend: Backend | None,
    secrets_map: dict[str, str] | None = None,
    repo_full_names: list[str] | None = None,
    *,
    write_access: bool = False,
) -> str:
    """Resolve an access token for any Git backend, minting an installation token for `github-app`."""
    if backend is None:
        return ""
    resolved_secrets = (
        secrets_map
        if secrets_map is not None
        else decrypt_secrets(backend.secrets_encrypted)
    )
    provider = (backend.provider or "").strip().lower()
    if provider == "local":
        base_scope = "local-git:write" if write_access else "local-git:read"
        slugs = sorted(
            {
                extract_repo_slug(r)
                for r in (repo_full_names or [])
                if r and r.strip() and r.strip().strip("/") not in ("", ".git")
            }
        )
        if not slugs:
            raise UnscopedLocalGitTokenError(
                "Local Git tokens must be scoped to at least one explicit project repository slug"
            )
        return create_run_token(f"{base_scope}:{','.join(slugs)}")
    if provider == "github-app":
        tok = await mint_github_app_installation_token(
            backend,
            resolved_secrets,
            repo_full_names=repo_full_names,
            write_access=write_access,
        )
        return (tok or "").strip()
    return (resolved_secrets.get("token") or "").strip()

