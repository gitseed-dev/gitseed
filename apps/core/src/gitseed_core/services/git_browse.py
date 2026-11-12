import base64
import logging
import urllib.parse
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit

import httpx
from gitseed_common.enums import BackendType
from sqlalchemy.ext.asyncio import AsyncSession

from gitseed_core.models.backend import Backend
from gitseed_core.models.project import ProjectRepository
from gitseed_core.models.user import User
from gitseed_core.security import decrypt_secrets
from gitseed_core.services import projects as projects_service
from gitseed_core.services.errors import Invalid, NotFound
from gitseed_core.services.git_auth import resolve_git_backend_token

logger = logging.getLogger("uvicorn.error")

_MAX_BLOB_BYTES = 512 * 1024  # 512 KB inline preview cap


class InvalidRepoPathError(Invalid, ValueError):
    """Raised when a repository path or ref contains traversal or invalid characters."""


def normalize_repo_path(path: str | None, *, allow_empty: bool = True) -> str:
    """Validate and normalize a repository relative path.

    Rejects `..` or `.` path segments, leading `/`, `\\`, and control characters.
    """
    if path is None:
        raw = ""
    elif isinstance(path, str):
        raw = path
    else:
        raise InvalidRepoPathError("Repository path must be a string")

    if any(ord(ch) < 32 or ord(ch) == 127 for ch in raw):
        raise InvalidRepoPathError("Repository path contains control characters")
    if "\\" in raw:
        raise InvalidRepoPathError("Repository path must not contain backslashes")

    trimmed = raw.strip()
    if trimmed.startswith("/"):
        raise InvalidRepoPathError("Repository path must not start with '/'")

    cleaned = trimmed.rstrip("/")
    if not cleaned:
        if allow_empty:
            return ""
        raise InvalidRepoPathError("File path is required")

    raw_segments = cleaned.split("/")
    for seg in raw_segments:
        if not seg or seg in (".", ".."):
            raise InvalidRepoPathError("Repository path contains invalid or traversal segments")
        decoded_seg = urllib.parse.unquote(seg)
        if decoded_seg in (".", "..") or "/" in decoded_seg or "\\" in decoded_seg or "\x00" in decoded_seg:
            raise InvalidRepoPathError("Repository path contains encoded traversal segments")
    return "/".join(raw_segments)


def quote_repo_path_segments(path: str | None, *, allow_empty: bool = True) -> str:
    """Validate `path` and URL-quote each segment with `quote(seg, safe=\"\")`."""
    clean = normalize_repo_path(path, allow_empty=allow_empty)
    if not clean:
        return ""
    return "/".join(quote(seg, safe="") for seg in clean.split("/"))


def _quote_repo_full_name(full_name: str) -> str:
    clean = normalize_repo_path(full_name.strip().strip("/"), allow_empty=False)
    return "/".join(quote(seg, safe="") for seg in clean.split("/"))


def resolve_git_web_base(provider: str, endpoint: str | None) -> str:
    """Resolve the human-facing web host base URL for a Git backend."""
    p = (provider or "github").strip().lower()
    raw = (endpoint or "").strip().rstrip("/")

    if p in ("github", "github-enterprise", "github-app"):
        if not raw or raw == "https://api.github.com":
            return "https://github.com"
        if raw.endswith("/api/v3"):
            return raw[: -len("/api/v3")]
        if raw.startswith("https://api."):
            return "https://" + raw[len("https://api.") :]
        return raw

    if p == "gitlab":
        if not raw:
            return "https://gitlab.com"
        if raw.endswith("/api/v4"):
            return raw[: -len("/api/v4")]
        return raw

    if p == "bitbucket":
        if not raw or "api.bitbucket.org" in raw:
            return "https://bitbucket.org"
        if raw.endswith("/2.0"):
            return raw[: -len("/2.0")]
        return raw

    return raw or "https://github.com"


def build_git_web_url(
    provider: str,
    endpoint: str | None,
    full_name: str,
    *,
    ref: str | None = None,
    path: str | None = None,
    is_dir: bool = True,
    commit_sha: str | None = None,
) -> str:
    """Build a direct web URL to a repository, branch, commit, folder, or file on the remote host."""
    p = (provider or "github").strip().lower()
    base = resolve_git_web_base(p, endpoint)
    clean_repo = full_name.strip().strip("/")
    clean_path = (path or "").strip().strip("/")

    if commit_sha:
        if p == "gitlab":
            return f"{base}/{clean_repo}/-/commit/{commit_sha}"
        if p == "bitbucket":
            return f"{base}/{clean_repo}/commits/{commit_sha}"
        return f"{base}/{clean_repo}/commit/{commit_sha}"

    if not ref:
        return f"{base}/{clean_repo}"

    encoded_ref = "/".join(quote(seg, safe="") for seg in ref.strip().strip("/").split("/") if seg)
    encoded_path = quote_repo_path_segments(clean_path, allow_empty=True) if clean_path else ""

    if p == "gitlab":
        if not encoded_path:
            return f"{base}/{clean_repo}/-/tree/{encoded_ref}"
        kind = "tree" if is_dir else "blob"
        return f"{base}/{clean_repo}/-/{kind}/{encoded_ref}/{encoded_path}"

    if p == "bitbucket":
        if not encoded_path:
            return f"{base}/{clean_repo}/src/{encoded_ref}"
        return f"{base}/{clean_repo}/src/{encoded_ref}/{encoded_path}"

    # Default: GitHub / GHE / Gitea
    if not encoded_path:
        return f"{base}/{clean_repo}/tree/{encoded_ref}"
    kind = "tree" if is_dir else "blob"
    return f"{base}/{clean_repo}/{kind}/{encoded_ref}/{encoded_path}"


async def _resolve_repo_and_backend(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    repo_id: int,
) -> tuple[ProjectRepository, Backend, dict[str, str]]:
    project = await projects_service.get_project(session, actor, project_id_or_slug)
    repo = await session.get(ProjectRepository, repo_id)
    if repo is None or repo.project_id != project.id:
        raise NotFound("Project repository not found")

    backend = await session.get(Backend, repo.backend_id)
    if backend is None or backend.type != BackendType.GIT:
        raise NotFound("Git backend for repository not found")

    secrets_map = decrypt_secrets(backend.secrets_encrypted)
    return repo, backend, secrets_map


def _github_api_base_and_headers(
    endpoint: str | None, token: str
) -> tuple[str, dict[str, str]]:
    raw = (endpoint or "").strip().rstrip("/")
    if not raw or raw in ("https://github.com", "http://github.com", "https://api.github.com"):
        base = "https://api.github.com"
    elif "api.github.com" in raw or raw.endswith("/api/v3"):
        base = raw
    else:
        base = f"{raw}/api/v3"

    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return base, headers


def _gitlab_api_base_and_headers(
    endpoint: str | None, token: str
) -> tuple[str, dict[str, str]]:
    raw = (endpoint or "").strip().rstrip("/") or "https://gitlab.com"
    api_base = raw if raw.endswith("/api/v4") else f"{raw}/api/v4"
    headers: dict[str, str] = {}
    if token:
        headers["PRIVATE-TOKEN"] = token
    return api_base, headers


def _bitbucket_api_base_and_headers(
    endpoint: str | None, token: str
) -> tuple[str, dict[str, str]]:
    raw = (endpoint or "").strip().rstrip("/") or "https://api.bitbucket.org/2.0"
    api_base = raw if raw.endswith("/2.0") else f"{raw}/2.0"
    headers: dict[str, str] = {}
    if token:
        if ":" in token:
            b64_auth = base64.b64encode(token.encode("utf-8")).decode("ascii")
            headers["Authorization"] = f"Basic {b64_auth}"
        else:
            headers["Authorization"] = f"Bearer {token}"
    return api_base, headers


async def list_repo_branches(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    repo_id: int,
) -> dict[str, Any]:
    """List branches for a project repository across GitHub, GitLab, or Bitbucket."""
    repo, backend, secrets_map = await _resolve_repo_and_backend(
        session, actor, project_id_or_slug, repo_id
    )
    provider = backend.provider.lower()
    if provider == "local":
        from gitseed_core.services.local_git import list_local_repo_branches

        return list_local_repo_branches(repo)
    token = await resolve_git_backend_token(
        backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=False,
    )
    default_branch = repo.default_branch or "main"
    branches: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
        if provider in ("github", "github-enterprise", "github-app"):
            api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
            resp = await client.get(
                f"{api_base}/repos/{repo.full_name}/branches?per_page=100",
                headers=headers,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), list):
                for item in resp.json():
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    b_name = str(item["name"])
                    sha = (
                        str(item.get("commit", {}).get("sha", ""))
                        if isinstance(item.get("commit"), dict)
                        else None
                    )
                    branches.append(
                        {
                            "name": b_name,
                            "commit_sha": sha or None,
                            "is_default": b_name == default_branch,
                            "protected": bool(item.get("protected", False)),
                            "web_url": build_git_web_url(
                                provider,
                                backend.endpoint,
                                repo.full_name,
                                ref=b_name,
                            ),
                        }
                    )

        elif provider == "gitlab":
            api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
            encoded_repo = urllib.parse.quote(repo.full_name, safe="")
            resp = await client.get(
                f"{api_base}/projects/{encoded_repo}/repository/branches?per_page=100",
                headers=headers,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), list):
                for item in resp.json():
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    b_name = str(item["name"])
                    sha = (
                        str(item.get("commit", {}).get("id", ""))
                        if isinstance(item.get("commit"), dict)
                        else None
                    )
                    branches.append(
                        {
                            "name": b_name,
                            "commit_sha": sha or None,
                            "is_default": bool(
                                item.get("default") or b_name == default_branch
                            ),
                            "protected": bool(item.get("protected", False)),
                            "web_url": build_git_web_url(
                                provider,
                                backend.endpoint,
                                repo.full_name,
                                ref=b_name,
                            ),
                        }
                    )

        elif provider == "bitbucket":
            api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
            resp = await client.get(
                f"{api_base}/repositories/{repo.full_name}/refs/branches?pagelen=100",
                headers=headers,
            )
            if 200 <= resp.status_code < 300 and isinstance(resp.json(), dict):
                for item in resp.json().get("values", []):
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    b_name = str(item["name"])
                    sha = (
                        str(item.get("target", {}).get("hash", ""))
                        if isinstance(item.get("target"), dict)
                        else None
                    )
                    branches.append(
                        {
                            "name": b_name,
                            "commit_sha": sha or None,
                            "is_default": b_name == default_branch,
                            "protected": False,
                            "web_url": build_git_web_url(
                                provider,
                                backend.endpoint,
                                repo.full_name,
                                ref=b_name,
                            ),
                        }
                    )

    if not any(b["name"] == default_branch for b in branches):
        branches.insert(
            0,
            {
                "name": default_branch,
                "commit_sha": None,
                "is_default": True,
                "protected": False,
                "web_url": build_git_web_url(
                    provider,
                    backend.endpoint,
                    repo.full_name,
                    ref=default_branch,
                ),
            },
        )

    branches.sort(
        key=lambda b: (
            0 if b["is_default"] else (1 if str(b["name"]).startswith("gitseed/") else 2),
            str(b["name"]).lower(),
        )
    )

    return {
        "repository_id": repo.id,
        "full_name": repo.full_name,
        "provider": provider,
        "default_branch": default_branch,
        "web_url": build_git_web_url(provider, backend.endpoint, repo.full_name),
        "branches": branches,
    }


def _decode_blob_bytes(raw_bytes: bytes) -> tuple[str | None, bool, bool]:
    """Return (decoded_text, is_binary, truncated) for raw file bytes."""
    truncated = len(raw_bytes) > _MAX_BLOB_BYTES
    sample = raw_bytes[: _MAX_BLOB_BYTES]
    if b"\x00" in sample[:8000]:
        return None, True, False
    return sample.decode("utf-8", errors="replace"), False, truncated


async def get_repo_tree(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    repo_id: int,
    *,
    ref: str | None = None,
    path: str = "",
) -> dict[str, Any]:
    """Fetch a normalized directory listing (and optional README preview) for a repository path."""
    repo, backend, secrets_map = await _resolve_repo_and_backend(
        session, actor, project_id_or_slug, repo_id
    )
    provider = backend.provider.lower()
    if provider == "local":
        from gitseed_core.services.local_git import get_local_repo_tree

        return get_local_repo_tree(repo, ref=ref, path=path)
    token = await resolve_git_backend_token(
        backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=False,
    )
    effective_ref = (ref or "").strip() or repo.default_branch or "main"
    clean_path = normalize_repo_path(path, allow_empty=True)
    quoted_repo = _quote_repo_full_name(repo.full_name)

    entries: list[dict[str, Any]] = []
    readme_payload: dict[str, Any] | None = None

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        if provider in ("github", "github-enterprise", "github-app"):
            api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
            encoded_path = quote_repo_path_segments(clean_path, allow_empty=True)
            url = (
                f"{api_base}/repos/{quoted_repo}/contents/{encoded_path}"
                if encoded_path
                else f"{api_base}/repos/{quoted_repo}/contents"
            )
            resp = await client.get(
                url,
                params={"ref": effective_ref},
                headers=headers,
            )
            if resp.status_code == 404:
                raise NotFound(
                    f"Path '{clean_path or '/'}' on ref '{effective_ref}' was not found on {repo.full_name} "
                    "(if this is a private repository, verify your token has Repository 'Contents: Read' access)"
                )
            if resp.status_code in (401, 403):
                gh_msg = ""
                try:
                    gh_msg = str(resp.json().get("message") or "").strip()
                except Exception:
                    pass
                detail_suffix = f": {gh_msg}" if gh_msg else ""
                raise Invalid(
                    f"Git provider rejected authentication (HTTP {resp.status_code}{detail_suffix}) while reading {repo.full_name}. "
                    "Ensure your token has 'Contents: Read and write' (fine-grained PAT) or 'repo' scope (classic PAT)."
                )
            if resp.status_code >= 400:
                raise Invalid(
                    f"Git provider returned HTTP {resp.status_code} while listing {repo.full_name}"
                )

            data = resp.json()
            raw_list = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
            for item in raw_list:
                if not isinstance(item, dict) or not item.get("name"):
                    continue
                raw_type = str(item.get("type", "file"))
                entry_type = "dir" if raw_type == "dir" else "file"
                item_path = str(item.get("path") or item["name"])
                entries.append(
                    {
                        "name": str(item["name"]),
                        "path": item_path,
                        "type": entry_type,
                        "size": int(item.get("size") or 0) if entry_type == "file" else None,
                        "sha": item.get("sha"),
                        "web_url": item.get("html_url")
                        or build_git_web_url(
                            provider,
                            backend.endpoint,
                            repo.full_name,
                            ref=effective_ref,
                            path=item_path,
                            is_dir=(entry_type == "dir"),
                        ),
                    }
                )

            readme_entry = next(
                (
                    e
                    for e in entries
                    if e["type"] == "file"
                    and e["name"].lower() in ("readme.md", "readme.mdx", "readme.txt", "readme")
                ),
                None,
            )
            if readme_entry is not None:
                try:
                    r_url = f"{api_base}/repos/{quoted_repo}/contents/{quote_repo_path_segments(readme_entry['path'], allow_empty=False)}"
                    r_resp = await client.get(
                        r_url,
                        params={"ref": effective_ref},
                        headers=headers,
                    )
                    if 200 <= r_resp.status_code < 300 and isinstance(r_resp.json(), dict):
                        r_json = r_resp.json()
                        if r_json.get("encoding") == "base64" and r_json.get("content"):
                            raw_b = base64.b64decode(r_json["content"])
                            text_content, is_bin, _ = _decode_blob_bytes(raw_b)
                            if not is_bin and text_content is not None:
                                readme_payload = {
                                    "name": readme_entry["name"],
                                    "path": readme_entry["path"],
                                    "content": text_content,
                                }
                except Exception:
                    pass

        elif provider == "gitlab":
            api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
            encoded_repo = urllib.parse.quote(repo.full_name, safe="")
            params: dict[str, Any] = {"ref": effective_ref, "per_page": 100}
            if clean_path:
                params["path"] = clean_path
            resp = await client.get(
                f"{api_base}/projects/{encoded_repo}/repository/tree",
                params=params,
                headers=headers,
            )
            if resp.status_code == 404:
                raise NotFound(
                    f"Path '{clean_path or '/'}' on ref '{effective_ref}' was not found on {repo.full_name}"
                )
            if resp.status_code >= 400:
                raise Invalid(
                    f"GitLab returned HTTP {resp.status_code} while listing {repo.full_name}"
                )
            if isinstance(resp.json(), list):
                for item in resp.json():
                    if not isinstance(item, dict) or not item.get("name"):
                        continue
                    entry_type = "dir" if item.get("type") == "tree" else "file"
                    item_path = str(item.get("path") or item["name"])
                    entries.append(
                        {
                            "name": str(item["name"]),
                            "path": item_path,
                            "type": entry_type,
                            "size": None,
                            "sha": item.get("id"),
                            "web_url": build_git_web_url(
                                provider,
                                backend.endpoint,
                                repo.full_name,
                                ref=effective_ref,
                                path=item_path,
                                is_dir=(entry_type == "dir"),
                            ),
                        }
                    )

            readme_entry = next(
                (
                    e
                    for e in entries
                    if e["type"] == "file"
                    and e["name"].lower() in ("readme.md", "readme.txt", "readme")
                ),
                None,
            )
            if readme_entry is not None:
                try:
                    encoded_file = urllib.parse.quote(readme_entry["path"], safe="")
                    r_resp = await client.get(
                        f"{api_base}/projects/{encoded_repo}/repository/files/{encoded_file}/raw",
                        params={"ref": effective_ref},
                        headers=headers,
                    )
                    if 200 <= r_resp.status_code < 300:
                        text_content, is_bin, _ = _decode_blob_bytes(r_resp.content)
                        if not is_bin and text_content is not None:
                            readme_payload = {
                                "name": readme_entry["name"],
                                "path": readme_entry["path"],
                                "content": text_content,
                            }
                except Exception:
                    pass

        elif provider == "bitbucket":
            api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
            encoded_ref = quote(effective_ref, safe="")
            encoded_path = quote_repo_path_segments(clean_path, allow_empty=True)
            url = (
                f"{api_base}/repositories/{quoted_repo}/src/{encoded_ref}/{encoded_path}"
                if encoded_path
                else f"{api_base}/repositories/{quoted_repo}/src/{encoded_ref}/"
            )
            resp = await client.get(
                url,
                params={"pagelen": 100},
                headers=headers,
            )
            if resp.status_code == 404:
                raise NotFound(
                    f"Path '{clean_path or '/'}' on ref '{effective_ref}' was not found on {repo.full_name}"
                )
            if resp.status_code >= 400:
                raise Invalid(
                    f"Bitbucket returned HTTP {resp.status_code} while listing {repo.full_name}"
                )
            if isinstance(resp.json(), dict):
                for item in resp.json().get("values", []):
                    if not isinstance(item, dict) or not item.get("path"):
                        continue
                    item_path = str(item["path"])
                    name = item_path.rsplit("/", 1)[-1]
                    entry_type = (
                        "dir" if item.get("type") == "commit_directory" else "file"
                    )
                    entries.append(
                        {
                            "name": name,
                            "path": item_path,
                            "type": entry_type,
                            "size": item.get("size") if entry_type == "file" else None,
                            "sha": (
                                item.get("commit", {}).get("hash")
                                if isinstance(item.get("commit"), dict)
                                else None
                            ),
                            "web_url": build_git_web_url(
                                provider,
                                backend.endpoint,
                                repo.full_name,
                                ref=effective_ref,
                                path=item_path,
                                is_dir=(entry_type == "dir"),
                            ),
                        }
                    )
        else:
            raise Invalid(
                f"In-app code browsing is not supported for Git provider '{provider}'"
            )

    entries.sort(key=lambda e: (0 if e["type"] == "dir" else 1, str(e["name"]).lower()))

    return {
        "repository_id": repo.id,
        "full_name": repo.full_name,
        "provider": provider,
        "ref": effective_ref,
        "path": clean_path,
        "web_url": build_git_web_url(
            provider,
            backend.endpoint,
            repo.full_name,
            ref=effective_ref,
            path=clean_path,
            is_dir=True,
        ),
        "entries": entries,
        "readme": readme_payload,
    }


async def get_repo_blob(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    repo_id: int,
    *,
    ref: str | None = None,
    path: str,
) -> dict[str, Any]:
    """Fetch a single file's content and metadata from a project repository."""
    repo, backend, secrets_map = await _resolve_repo_and_backend(
        session, actor, project_id_or_slug, repo_id
    )
    provider = backend.provider.lower()
    if provider == "local":
        from gitseed_core.services.local_git import get_local_repo_blob

        return get_local_repo_blob(repo, ref=ref, path=path)
    token = await resolve_git_backend_token(
        backend,
        secrets_map,
        repo_full_names=[repo.full_name],
        write_access=False,
    )
    effective_ref = (ref or "").strip() or repo.default_branch or "main"
    clean_path = normalize_repo_path(path, allow_empty=False)
    quoted_repo = _quote_repo_full_name(repo.full_name)

    file_name = clean_path.rsplit("/", 1)[-1]
    web_url = build_git_web_url(
        provider,
        backend.endpoint,
        repo.full_name,
        ref=effective_ref,
        path=clean_path,
        is_dir=False,
    )

    async with httpx.AsyncClient(timeout=12.0, follow_redirects=True) as client:
        if provider in ("github", "github-enterprise", "github-app"):
            api_base, headers = _github_api_base_and_headers(backend.endpoint, token)
            encoded_path = quote_repo_path_segments(clean_path, allow_empty=False)
            resp = await client.get(
                f"{api_base}/repos/{quoted_repo}/contents/{encoded_path}",
                params={"ref": effective_ref},
                headers=headers,
            )
            if resp.status_code == 404:
                raise NotFound(
                    f"File '{clean_path}' on ref '{effective_ref}' was not found"
                )
            if resp.status_code >= 400:
                raise Invalid(
                    f"Git provider returned HTTP {resp.status_code} while reading '{clean_path}'"
                )

            data = resp.json()
            if not isinstance(data, dict) or data.get("type") == "dir":
                raise Invalid(f"Path '{clean_path}' is a directory, not a file")

            size = int(data.get("size") or 0)
            sha = data.get("sha")
            html_url = data.get("html_url") or web_url

            if data.get("encoding") == "base64" and data.get("content"):
                raw_bytes = base64.b64decode(data["content"])
                text_content, is_binary, truncated = _decode_blob_bytes(raw_bytes)
            elif data.get("download_url") and size <= _MAX_BLOB_BYTES:
                dl_resp = await client.get(str(data["download_url"]))
                text_content, is_binary, truncated = _decode_blob_bytes(dl_resp.content)
            else:
                text_content = None
                is_binary = False
                truncated = size > _MAX_BLOB_BYTES

            return {
                "repository_id": repo.id,
                "full_name": repo.full_name,
                "provider": provider,
                "ref": effective_ref,
                "path": clean_path,
                "name": str(data.get("name") or file_name),
                "size": size,
                "sha": sha,
                "is_binary": is_binary,
                "truncated": truncated,
                "content": text_content,
                "web_url": html_url,
            }

        if provider == "gitlab":
            api_base, headers = _gitlab_api_base_and_headers(backend.endpoint, token)
            encoded_repo = urllib.parse.quote(repo.full_name, safe="")
            encoded_file = urllib.parse.quote(clean_path, safe="")
            resp = await client.get(
                f"{api_base}/projects/{encoded_repo}/repository/files/{encoded_file}",
                params={"ref": effective_ref},
                headers=headers,
            )
            if resp.status_code == 404:
                raise NotFound(
                    f"File '{clean_path}' on ref '{effective_ref}' was not found"
                )
            if resp.status_code >= 400:
                raise Invalid(
                    f"GitLab returned HTTP {resp.status_code} while reading '{clean_path}'"
                )

            data = resp.json()
            size = int(data.get("size") or 0)
            sha = data.get("blob_id") or data.get("last_commit_id")
            raw_b64 = data.get("content") or ""
            raw_bytes = base64.b64decode(raw_b64) if raw_b64 else b""
            text_content, is_binary, truncated = _decode_blob_bytes(raw_bytes)

            return {
                "repository_id": repo.id,
                "full_name": repo.full_name,
                "provider": provider,
                "ref": effective_ref,
                "path": clean_path,
                "name": str(data.get("file_name") or file_name),
                "size": size,
                "sha": sha,
                "is_binary": is_binary,
                "truncated": truncated,
                "content": text_content,
                "web_url": web_url,
            }

        if provider == "bitbucket":
            api_base, headers = _bitbucket_api_base_and_headers(backend.endpoint, token)
            encoded_ref = quote(effective_ref, safe="")
            encoded_path = quote_repo_path_segments(clean_path, allow_empty=False)
            resp = await client.get(
                f"{api_base}/repositories/{quoted_repo}/src/{encoded_ref}/{encoded_path}",
                headers=headers,
            )
            if resp.status_code == 404:
                raise NotFound(
                    f"File '{clean_path}' on ref '{effective_ref}' was not found"
                )
            if resp.status_code >= 400:
                raise Invalid(
                    f"Bitbucket returned HTTP {resp.status_code} while reading '{clean_path}'"
                )

            raw_bytes = resp.content
            text_content, is_binary, truncated = _decode_blob_bytes(raw_bytes)
            return {
                "repository_id": repo.id,
                "full_name": repo.full_name,
                "provider": provider,
                "ref": effective_ref,
                "path": clean_path,
                "name": file_name,
                "size": len(raw_bytes),
                "sha": None,
                "is_binary": is_binary,
                "truncated": truncated,
                "content": text_content,
                "web_url": web_url,
            }

        if provider == "local":
            from gitseed_core.services.local_git import get_local_repo_blob

            return get_local_repo_blob(repo, ref=effective_ref, path=clean_path)

    raise Invalid(f"In-app code browsing is not supported for Git provider '{provider}'")


def _build_authenticated_git_url(
    backend: Backend, full_name: str, token: str
) -> str:
    provider = (backend.provider or "github").strip().lower()
    if provider == "local":
        from gitseed_core.services.local_git import resolve_local_bare_repo_path

        return str(resolve_local_bare_repo_path(full_name))
    web_base = resolve_git_web_base(provider, backend.endpoint).rstrip("/")
    cleaned_name = full_name.strip().lstrip("/")
    repo_suffix = (
        cleaned_name if cleaned_name.endswith(".git") else f"{cleaned_name}.git"
    )
    clean_url = f"{web_base}/{repo_suffix}"
    if not token:
        return clean_url
    parts = urlsplit(clean_url)
    user_prefix = (
        "x-access-token"
        if provider in ("github", "github-app", "github-enterprise")
        else "oauth2"
        if provider == "gitlab"
        else "x-token-auth"
        if provider == "bitbucket"
        else "git"
    )
    auth_netloc = f"{user_prefix}:{quote(token, safe='')}@{parts.netloc}"
    return urlunsplit((parts.scheme, auth_netloc, parts.path, "", ""))


from gitseed_core.services.git_changes import (  # noqa: E402, F401
    get_seed_changes,
    merge_seed_branch_into_default,
)




