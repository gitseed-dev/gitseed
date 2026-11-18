"""Regression tests for Workstream 3 (H2 cross-project local git, H3 maintainer escalation & workspace root, GitHub App manifest_state)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from gitseed_common.enums import BackendType, OrgRole
from gitseed_core.api.v1.backends import _serialize_backend
from gitseed_core.config import settings
from gitseed_core.models.backend import Backend
from gitseed_core.services import backends as backends_service
from gitseed_core.services import local_git as local_git_service
from gitseed_core.services import projects as projects_service
from gitseed_core.services.errors import Forbidden, Invalid
from gitseed_core.services.local_git import InvalidGitInputError, validate_workspace_path

from conftest import _exec_result, _make_project, _make_session, _make_user


@pytest.mark.asyncio
async def test_h2_add_repository_blocks_cross_project_local_git(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    maintainer = _make_user("usr_maint", OrgRole.MEMBER)
    project = _make_project("prj_alpha")
    project.slug = "alpha-project"

    local_backend = Backend(
        id="bnd_local",
        name="Local Git",
        type=BackendType.GIT,
        provider="local",
        enabled=True,
    )
    monkeypatch.setattr(
        projects_service,
        "require_project_maintainer",
        AsyncMock(return_value=project),
    )
    session.get = AsyncMock(return_value=local_backend)

    with pytest.raises(Forbidden, match="local/alpha-project"):
        await projects_service.add_repository(
            session,
            maintainer,
            project.id,
            backend_id=local_backend.id,
            full_name="local/victim-private-project",
        )


def test_h3_default_workspace_root_rejects_arbitrary_home_subfolders(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOTS", "")
    home = Path.home().resolve()
    with pytest.raises(InvalidGitInputError, match="allowed workspace roots"):
        validate_workspace_path(home / "Documents" / "some-folder")


@pytest.mark.asyncio
async def test_h3_non_admin_maintainer_cannot_modify_backends_restricted_or_allowlist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = _make_session()
    maintainer = _make_user("usr_maint", OrgRole.MEMBER)
    project = _make_project("prj_rest")
    project.backends_restricted = True

    monkeypatch.setattr(
        projects_service,
        "require_project_maintainer",
        AsyncMock(return_value=project),
    )

    with pytest.raises(Forbidden, match="Only organization administrators"):
        await projects_service.update_project(
            session,
            maintainer,
            project.id,
            backends_restricted=False,
        )

    with pytest.raises(Forbidden, match="Only organization administrators"):
        await projects_service.allow_project_backend(
            session,
            maintainer,
            project.id,
            "bnd_git_1",
        )

    with pytest.raises(Forbidden, match="Only organization administrators"):
        await projects_service.disallow_project_backend(
            session,
            maintainer,
            project.id,
            "bnd_git_1",
        )


@pytest.mark.asyncio
async def test_h3_non_admin_maintainer_cannot_attach_custom_workspace_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings, "LOCAL_WORKSPACE_ROOTS", str(tmp_path))
    custom_dir = tmp_path / "other-folder"
    custom_dir.mkdir(parents=True, exist_ok=True)

    session = _make_session()
    maintainer = _make_user("usr_maint", OrgRole.MEMBER)
    project = _make_project("prj_ws")
    project.slug = "my-proj"
    project.backends_restricted = False

    local_backend = Backend(
        id="bnd_local",
        name="Local Git",
        type=BackendType.GIT,
        provider="local",
        enabled=True,
    )
    monkeypatch.setattr(
        local_git_service.projects_service,
        "require_project_maintainer",
        AsyncMock(return_value=project),
    )
    monkeypatch.setattr(
        local_git_service,
        "ensure_local_git_backend",
        AsyncMock(return_value=local_backend),
    )

    with pytest.raises(Forbidden, match="Only organization administrators"):
        await local_git_service.init_project_local_repository(
            session,
            maintainer,
            project.id,
            workspace_path=str(custom_dir),
        )


@pytest.mark.asyncio
async def test_maintainer_on_restricted_project_cannot_discover_unallowed_backend() -> None:
    session = _make_session()
    maintainer = _make_user("usr_maint", OrgRole.MEMBER)
    restricted_proj = _make_project("prj_restricted")
    restricted_proj.backends_restricted = True

    session.execute = AsyncMock(
        return_value=_exec_result(
            scalar_one_or_none=restricted_proj,
            scalars_all=[restricted_proj],
        )
    )
    session.get = AsyncMock(return_value=None)

    with pytest.raises(Forbidden, match="not allowed on any project you maintain"):
        await backends_service.discover_backend_repositories(
            session,
            "bnd_forbidden_git",
            actor=maintainer,
        )


@pytest.mark.asyncio
async def test_backend_serialization_strips_manifest_state_and_callback_validates_code() -> None:
    session = _make_session()
    backend = Backend(
        id="bnd_ghapp",
        name="GitHub App",
        type=BackendType.GIT,
        provider="github-app",
        endpoint="https://api.github.com",
        config={"manifest_state": "secret_csrf_state_123", "manifest_status": "awaiting_creation"},
        enabled=True,
    )
    serialized = await _serialize_backend(session, backend, {backend.id: []})
    assert "manifest_state" not in (serialized.config or {})

    session.execute = AsyncMock(
        return_value=_exec_result(scalars_all=[backend])
    )
    with pytest.raises(Invalid, match="Invalid GitHub App manifest conversion code"):
        await backends_service.complete_github_app_manifest_callback(
            session,
            code="../evil/path",
            state="secret_csrf_state_123",
        )

