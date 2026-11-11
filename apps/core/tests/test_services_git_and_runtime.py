from __future__ import annotations

import base64
import io
import tarfile
from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    BackendHealth,
    BackendType,
    OrgRole,
    ProjectRole,
    RunStatus,
    SandboxStatus,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from gitseed_core.bootstrap import _build_default_compute_backend
from gitseed_core.models.agent import Agent
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project, ProjectRepository
from gitseed_core.models.run import Run
from gitseed_core.models.sandbox import SandboxSession
from gitseed_core.models.seed import Seed, SeedTask
from gitseed_core.models.settings import OrgSettings
from gitseed_core.security import create_run_token, encrypt_secrets
from gitseed_core.services import (
    compute_runtime as compute_svc,
)
from gitseed_core.services import (
    git_auth as git_auth_svc,
)
from gitseed_core.services import (
    git_browse as git_browse_svc,
)
from gitseed_core.services import (
    git_changes as git_changes_svc,
)
from gitseed_core.services import (
    git_merge as git_merge_svc,
)
from gitseed_core.services import (
    sandboxes as sbx_svc,
)
from gitseed_core.services import (
    scheduler as sched_svc,
)
from gitseed_core.services import (
    seeds as seeds_mod,
)
from gitseed_core.services.gateway import (
    _extract_context_stats_from_logs,
    build_context_window_info,
    merge_run_context_window,
    resolve_model_context_limit,
)

from conftest import _exec_result, _make_session, _make_user, _now


@pytest.mark.asyncio
async def test_git_auth_and_browse_services(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)

    # git_auth.py
    gh_app_backend = Backend(
        id="bnd_ghapp",
        name="GH App",
        type=BackendType.GIT,
        provider="github-app",
        config={"app_id": "123", "installation_id": "456"},
        secrets_encrypted=encrypt_secrets({"private_key": "dummy_pem"}),
    )
    monkeypatch.setattr(git_auth_svc, "create_github_app_jwt", lambda *a: "jwt_tok")
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"token": "ghs_minted123"})
    )
    monkeypatch.setattr(git_auth_svc.httpx, "AsyncClient", lambda **kw: mock_client)

    tok = await git_auth_svc.resolve_git_backend_token(
        gh_app_backend,
        {"private_key": "dummy_pem", "app_id": "123", "installation_id": "456"},
        repo_full_names=["org/repo"],
        write_access=True,
    )
    assert tok == "ghs_minted123"

    # git_browse.py URL builders & helpers
    assert (
        git_browse_svc.resolve_git_web_base("github", "https://api.github.com")
        == "https://github.com"
    )
    assert (
        git_browse_svc.resolve_git_web_base("gitlab", "https://gitlab.example.com/api/v4")
        == "https://gitlab.example.com"
    )
    assert (
        git_browse_svc.resolve_git_web_base("bitbucket", None)
        == "https://bitbucket.org"
    )
    assert (
        git_browse_svc.build_git_web_url(
            "github", None, "org/repo", ref="main", path="src/app.py", is_dir=False
        )
        == "https://github.com/org/repo/blob/main/src/app.py"
    )
    assert (
        git_browse_svc.build_git_web_url(
            "gitlab", None, "org/repo", commit_sha="abc1234"
        )
        == "https://gitlab.com/org/repo/-/commit/abc1234"
    )

    text_val, is_bin, trunc = git_browse_svc._decode_blob_bytes(b"hello world")
    assert text_val == "hello world" and not is_bin and not trunc
    _, is_bin2, _ = git_browse_svc._decode_blob_bytes(b"\x00\x01\x02")
    assert is_bin2 is True

    # list_repo_branches, get_repo_tree, get_repo_blob
    repo = ProjectRepository(
        id=1,
        project_id="prj_1",
        backend_id="bnd_gh",
        full_name="org/repo",
        default_branch="main",
        is_primary=True,
    )
    gh_backend = Backend(
        id="bnd_gh",
        name="GH",
        type=BackendType.GIT,
        provider="github",
        secrets_encrypted=encrypt_secrets({"token": "ghp_123"}),
    )
    monkeypatch.setattr(
        git_browse_svc,
        "_resolve_repo_and_backend",
        AsyncMock(return_value=(repo, gh_backend, {"token": "ghp_123"})),
    )

    b64_readme = base64.b64encode(b"# Project Readme").decode("ascii")

    async def _mock_browse_get(url: str, **kwargs: Any) -> MagicMock:
        if url.endswith("/branches?per_page=100"):
            return MagicMock(
                status_code=200,
                json=lambda: [
                    {"name": "main", "commit": {"sha": "111"}, "protected": True},
                    {"name": "gitseed/seed-1", "commit": {"sha": "222"}, "protected": False},
                ],
            )
        if url.endswith("/contents/README.md"):
            return MagicMock(
                status_code=200,
                json=lambda: {
                    "name": "README.md",
                    "path": "README.md",
                    "type": "file",
                    "size": 16,
                    "sha": "sha_readme",
                    "encoding": "base64",
                    "content": b64_readme,
                },
            )
        return MagicMock(
            status_code=200,
            json=lambda: [
                {"name": "src", "path": "src", "type": "dir", "sha": "sha_dir"},
                {"name": "README.md", "path": "README.md", "type": "file", "size": 16, "sha": "sha_readme"},
            ],
        )

    browse_client = AsyncMock()
    browse_client.__aenter__.return_value = browse_client
    browse_client.get = AsyncMock(side_effect=_mock_browse_get)
    monkeypatch.setattr(git_browse_svc.httpx, "AsyncClient", lambda **kw: browse_client)

    branches_res = await git_browse_svc.list_repo_branches(session, admin, "prj_1", 1)
    assert len(branches_res["branches"]) == 2

    tree_res = await git_browse_svc.get_repo_tree(session, admin, "prj_1", 1)
    assert len(tree_res["entries"]) == 2
    assert tree_res["readme"]["content"] == "# Project Readme"

    blob_res = await git_browse_svc.get_repo_blob(
        session, admin, "prj_1", 1, path="README.md"
    )
    assert blob_res["content"] == "# Project Readme"


@pytest.mark.asyncio
async def test_git_changes_and_merge_services(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    repo = ProjectRepository(
        id=1,
        project_id="prj_1",
        backend_id="bnd_gh",
        full_name="org/repo",
        default_branch="main",
        is_primary=True,
    )
    gh_backend = Backend(
        id="bnd_gh",
        name="GH",
        type=BackendType.GIT,
        provider="github",
        secrets_encrypted=encrypt_secrets({"token": "ghp_123"}),
    )
    seed = Seed(
        id="sed_1",
        project_id="prj_1",
        number=1,
        title="Seed 1",
        stage=SeedStage.REVIEW,
        status=SeedStatus.AWAITING_APPROVAL,
    )
    proj = Project(id="prj_1", name="Alpha", slug="alpha")
    monkeypatch.setattr(
        git_changes_svc,
        "resolve_git_backend_token",
        AsyncMock(return_value="ghp_123"),
    )
    monkeypatch.setattr(seeds_mod, "get_seed", AsyncMock(return_value=seed))
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[repo]),
            _exec_result(scalars_all=[]),
        ]
    )
    session.get = AsyncMock(
        side_effect=lambda model, ident: proj if model is Project else gh_backend
    )

    changes_client = AsyncMock()
    changes_client.__aenter__.return_value = changes_client
    changes_client.get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            json=lambda: {
                "html_url": "https://github.com/org/repo/compare/main...gitseed/alpha/seed-1",
                "commits": [
                    {
                        "sha": "abcdef1234567",
                        "commit": {
                            "message": "feat: add tests\n\ndetails",
                            "author": {"name": "Coder", "date": "2026-11-11T00:00:00Z"},
                        },
                    }
                ],
                "files": [
                    {
                        "filename": "src/main.py",
                        "status": "modified",
                        "additions": 10,
                        "deletions": 2,
                        "changes": 12,
                        "patch": "@@ -1,2 +1,10 @@\n+print('hi')",
                    }
                ],
            },
        )
    )
    monkeypatch.setattr(git_changes_svc.httpx, "AsyncClient", lambda **kw: changes_client)
    changes = await git_changes_svc.get_seed_changes(session, admin, seed.id)
    assert changes["branch_exists"] is True
    assert changes["head_ref"] == "gitseed/alpha/seed-1"
    assert changes["total_commits"] == 1
    assert changes["total_files"] == 1

    # git_merge.py
    clone_u, clean_u = git_merge_svc._build_git_urls(gh_backend, "org/repo", "ghp_123")
    assert "x-access-token:ghp_123@github.com" in clone_u
    assert clean_u == "https://github.com/org/repo.git"

    gl_b = Backend(id="b_gl", name="GL", type=BackendType.GIT, provider="gitlab")
    gl_clone, _ = git_merge_svc._build_git_urls(gl_b, "org/repo", "gl_tok")
    assert "oauth2:gl_tok@gitlab.com" in gl_clone

    bb_b = Backend(id="b_bb", name="BB", type=BackendType.GIT, provider="bitbucket")
    bb_clone, _ = git_merge_svc._build_git_urls(bb_b, "org/repo", "bb_tok")
    assert "x-token-auth:bb_tok@bitbucket.org" in bb_clone

    # issue_git_push_token
    active_run = Run(
        id="run_exec1",
        seed_id=seed.id,
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        status=RunStatus.RUNNING,
    )

    def _get_for_push(model: Any, ident: Any) -> Any:
        if model is Run:
            return active_run
        if model is Seed:
            return seed
        if model is Project:
            return proj
        if model is Backend:
            return gh_backend
        return None

    session.get = AsyncMock(side_effect=_get_for_push)
    session.execute = AsyncMock(return_value=_exec_result(scalar_one_or_none=repo))
    monkeypatch.setattr(
        git_merge_svc, "resolve_git_backend_token", AsyncMock(return_value="ghp_write")
    )
    # Agent-scoped token must be rejected for push credential issuance
    from gitseed_core.services.errors import Forbidden

    agent_run_tok = create_run_token(active_run.id, scope="agent")
    with pytest.raises(Forbidden):
        await git_merge_svc.issue_git_push_token(
            session, agent_run_tok, repository_full_name="org/repo"
        )

    valid_run_tok = create_run_token(active_run.id, scope="finalize")
    push_resp = await git_merge_svc.issue_git_push_token(
        session, valid_run_tok, repository_full_name="org/repo"
    )
    assert "ghp_write" in push_resp.push_url
    assert push_resp.push_token == "ghp_write"

    # Single-use per repo per run: second request for same repo must be rejected
    with pytest.raises(Forbidden):
        await git_merge_svc.issue_git_push_token(
            session, valid_run_tok, repository_full_name="org/repo"
        )

    # MERGE stage runs must never receive runner push tokens
    active_run.stage = SeedStage.MERGE
    active_run.output = {}
    with pytest.raises(Forbidden):
        await git_merge_svc.issue_git_push_token(
            session, valid_run_tok, repository_full_name="org/repo"
        )
    active_run.stage = SeedStage.EXECUTE

    # merge_seed_branch_into_default via GitHub API
    session.execute = AsyncMock(
        side_effect=[
            _exec_result(scalars_all=[repo]),
            _exec_result(scalars_all=[active_run]),
            _exec_result(scalar_one_or_none=active_run),
        ]
    )
    merge_client = AsyncMock()
    merge_client.__aenter__.return_value = merge_client
    merge_client.post = AsyncMock(
        return_value=MagicMock(status_code=201, json=lambda: {"sha": "merged_sha_999"})
    )
    merge_client.delete = AsyncMock(return_value=MagicMock(status_code=204))
    monkeypatch.setattr(git_merge_svc.httpx, "AsyncClient", lambda **kw: merge_client)
    merged_sha = await git_merge_svc.merge_seed_branch_into_default(session, seed)
    assert merged_sha == "merged_sha_999"


@pytest.mark.asyncio
async def test_compute_runtime_comprehensive(monkeypatch: pytest.MonkeyPatch) -> None:
    docker_b = Backend(
        id="bnd_docker",
        name="Docker TCP",
        type=BackendType.COMPUTE,
        provider="docker",
        endpoint="tcp://127.0.0.1:2375",
        config={"cpus": 2.0, "memory_mb": 2048},
    )
    k8s_b = Backend(
        id="bnd_k8s",
        name="K8s Cluster",
        type=BackendType.COMPUTE,
        provider="kubernetes",
        endpoint="https://k8s.example.com",
        config={"namespace": "gitseed", "service_account": "runner-sa"},
        secrets_encrypted=encrypt_secrets({"token": "k8s-tok"}),
    )
    proj = Project(id="prj_1", name="Alpha", slug="alpha")
    seed = Seed(id="sed_1", project_id="prj_1", number=1, title="Seed")
    run = Run(
        id="run_1",
        seed_id="sed_1",
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
    )

    # _demux_docker_logs (multiplexed frame and raw)
    payload = b"hello container\n"
    header = bytes([1, 0, 0, 0]) + len(payload).to_bytes(4, "big")
    assert compute_svc._demux_docker_logs(header + payload) == "hello container"
    assert compute_svc._demux_docker_logs(b"plain text") == "plain text"

    # _attach_run_log_tail
    compute_svc._attach_run_log_tail(run, "line 1")
    assert run.output["log_tail"] == "line 1"
    compute_svc._attach_run_log_tail(run, "line 2")
    assert run.output["log_tail"] == "line 2"

    # Mock Docker & K8s HTTP client interactions
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            content=header + payload,
            text="k8s pod logs",
            json=lambda: {
                "State": {"Running": True, "ExitCode": 0},
                "status": {"active": 1, "succeeded": 0, "failed": 0},
                "items": [{"metadata": {"name": "pod-1"}}],
            },
        )
    )
    mock_client.post = AsyncMock(
        return_value=MagicMock(status_code=200, json=lambda: {"Id": "container_xyz"})
    )
    mock_client.delete = AsyncMock(return_value=MagicMock(status_code=204))

    monkeypatch.setattr(
        compute_svc,
        "_docker_client_for_backend",
        lambda _b: (mock_client, "http://docker"),
    )
    monkeypatch.setattr(
        compute_svc,
        "_k8s_client_for_backend",
        lambda _b: (mock_client, "https://k8s.example.com", "gitseed"),
    )

    # Spawn Docker & K8s
    cid = await compute_svc.spawn_docker_runner_container(
        backend=docker_b,
        run=run,
        seed=seed,
        project=proj,
        run_token="gsr_tok",
    )
    assert cid == "container_xyz"

    job_name = await compute_svc.spawn_docker_runner_container(
        backend=k8s_b,
        run=run,
        seed=seed,
        project=proj,
        run_token="gsr_tok",
    )
    assert job_name.startswith("gitseed-run-")

    # Logs, inspect, stop/remove for both Docker and K8s
    d_logs = await compute_svc.fetch_docker_container_logs(docker_b, "container_xyz")
    assert d_logs == "hello container"
    k_logs = await compute_svc.fetch_docker_container_logs(k8s_b, job_name)
    assert k_logs == "k8s pod logs"

    d_state = await compute_svc.inspect_docker_container_state(docker_b, "container_xyz")
    assert d_state["exists"] is True and d_state["running"] is True
    k_state = await compute_svc.inspect_docker_container_state(k8s_b, job_name)
    assert k_state["exists"] is True and k_state["running"] is True

    await compute_svc.stop_and_remove_docker_container(docker_b, "container_xyz")
    await compute_svc.stop_and_remove_docker_container(k8s_b, job_name)


@pytest.mark.asyncio
async def test_sandboxes_service_comprehensive(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Any,
) -> None:
    now = _now()
    admin = _make_user("usr_admin", OrgRole.ADMIN)
    member = _make_user("usr_member", OrgRole.MEMBER)
    proj = Project(id="prj_1", name="Alpha", slug="alpha")
    repo = ProjectRepository(
        id=1,
        project_id="prj_1",
        backend_id="bnd_git",
        full_name="org/alpha",
        default_branch="main",
        is_primary=True,
    )
    git_b = Backend(
        id="bnd_git",
        name="GH",
        type=BackendType.GIT,
        provider="github",
        endpoint="https://api.github.com",
        secrets_encrypted=encrypt_secrets({"token": "ghp_1"}),
    )
    comp_b = Backend(
        id="bnd_comp",
        name="Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        endpoint="tcp://127.0.0.1:2375",
        enabled=True,
        health=BackendHealth.HEALTHY,
    )

    monkeypatch.setattr(sbx_svc.settings, "ARTIFACTS_DIR", str(tmp_path))

    # _require_project_developer
    monkeypatch.setattr(sbx_svc.projects_service, "get_project", AsyncMock(return_value=proj))
    monkeypatch.setattr(
        sbx_svc.projects_service,
        "get_member_role",
        AsyncMock(return_value=ProjectRole.DEVELOPER),
    )
    assert await sbx_svc._require_project_developer(_make_session(), admin, "prj_1") is proj
    assert await sbx_svc._require_project_developer(_make_session(), member, "prj_1") is proj

    # _select_docker_compute_backend
    db = _make_session()
    db.execute.return_value = _exec_result(scalars_all=[comp_b])
    assert (await sbx_svc._select_docker_compute_backend(db)).id == "bnd_comp"

    # Build an in-memory workspace tar
    ws_buf = io.BytesIO()
    with tarfile.open(fileobj=ws_buf, mode="w") as tf:
        info = tarfile.TarInfo(name="workspace/dist/app.whl")
        data = b"wheel-binary"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    ws_tar_bytes = ws_buf.getvalue()

    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.post = AsyncMock(
        return_value=MagicMock(status_code=200, json=lambda: {"Id": "cid_sbx1"})
    )
    mock_client.put = AsyncMock(return_value=MagicMock(status_code=200))
    mock_client.delete = AsyncMock(return_value=MagicMock(status_code=204))
    mock_client.get = AsyncMock(
        return_value=MagicMock(status_code=200, content=ws_tar_bytes)
    )
    monkeypatch.setattr(
        sbx_svc, "_docker_client_for_backend", lambda _b: (mock_client, "http://docker")
    )
    monkeypatch.setattr(
        sbx_svc.session_workspace, "_docker_client_for_backend", lambda _b: (mock_client, "http://docker")
    )
    monkeypatch.setattr(sbx_svc, "_ensure_docker_network", AsyncMock())
    monkeypatch.setattr(
        sbx_svc, "fetch_docker_container_logs", AsyncMock(return_value="sandbox output")
    )
    monkeypatch.setattr(sbx_svc, "stop_and_remove_docker_container", AsyncMock())

    sbx = SandboxSession(
        id="sbx_1",
        project_id="prj_1",
        repository_id=1,
        ref="main",
        head_sha="abc1234",
        image="python:3.12-slim",
        setup_command="pip install -e .",
        command="pytest",
        artifact_globs=["dist/*.whl"],
        status=SandboxStatus.RUNNING,
        container_id="cid_sbx1",
        compute_backend_id="bnd_comp",
        started_by=admin.id,
        started_at=now,
        expires_at=now + timedelta(minutes=30),
    )

    # _spawn_sandbox_container & _collect_container_artifacts
    spawned_cid = await sbx_svc._spawn_sandbox_container(
        compute_backend=comp_b,
        sandbox=sbx,
        workspace_tar=ws_tar_bytes,
        initial_stdin="yes",
    )
    assert spawned_cid == "cid_sbx1"

    collected = await sbx_svc._collect_container_artifacts(comp_b, sbx)
    assert len(collected) == 1
    assert collected[0]["name"] == "app.whl"

    # reconcile_sandbox_sessions (exited with code 0)
    monkeypatch.setattr(
        sbx_svc,
        "inspect_docker_container_state",
        AsyncMock(return_value={"exists": True, "running": False, "exit_code": 0}),
    )
    db = _make_session()
    db.get.return_value = comp_b
    db.execute.side_effect = [
        _exec_result(scalars_all=[sbx]),
        _exec_result(scalars_all=[]),
        _exec_result(scalars_all=[]),
    ]
    await sbx_svc.reconcile_sandbox_sessions(db)
    assert sbx.status == SandboxStatus.SUCCEEDED
    assert len(sbx.artifacts) == 1

    # resolve_sandbox_artifact_path
    monkeypatch.setattr(sbx_svc, "get_sandbox_session", AsyncMock(return_value=sbx))
    art_path, art_name = await sbx_svc.resolve_sandbox_artifact_path(
        db, admin, "prj_1", "sbx_1", "dist/app.whl"
    )
    assert art_name == "app.whl" and art_path.is_file()

    # create_sandbox_session, send_sandbox_input, stop_sandbox_session, list_sandbox_sessions
    monkeypatch.setattr(
        sbx_svc,
        "_clone_and_pack_workspace_tar",
        lambda **_kw: (ws_tar_bytes, "main", "deadbeef"),
    )
    monkeypatch.setattr(sbx_svc, "reconcile_sandbox_sessions", AsyncMock())
    db = _make_session()
    db.get.side_effect = [OrgSettings(id=1, max_sandbox_sessions=5), git_b]
    db.scalar.return_value = 0
    db.execute.side_effect = [
        _exec_result(scalars_all=[repo]),
        _exec_result(scalars_all=[comp_b]),
    ]
    created_sbx = await sbx_svc.create_sandbox_session(
        db,
        admin,
        "prj_1",
        command="pytest",
        setup_command="echo hi",
        artifact_globs=["dist/*"],
        initial_stdin="input",
    )
    assert created_sbx.container_id == "cid_sbx1"

    created_sbx.status = SandboxStatus.RUNNING
    db = _make_session()
    db.get.side_effect = [created_sbx, comp_b, created_sbx, comp_b]
    await sbx_svc.send_sandbox_input(
        db, admin, "prj_1", created_sbx.id, input_text="hello", append_newline=True, eof=True
    )
    stopped = await sbx_svc.stop_sandbox_session(db, admin, "prj_1", created_sbx.id)
    assert stopped.status == SandboxStatus.STOPPED

    db = _make_session()
    db.scalar.return_value = None
    db.execute.return_value = _exec_result(scalars_all=[stopped])
    listed = await sbx_svc.list_sandbox_sessions(db, admin, "prj_1")
    assert len(listed) == 1


@pytest.mark.asyncio
async def test_scheduler_service_comprehensive(monkeypatch: pytest.MonkeyPatch) -> None:
    now = _now()
    proj = Project(id="prj_1", name="Alpha", slug="alpha", max_concurrent_runs=2)
    seed = Seed(
        id="sed_1",
        project_id="prj_1",
        number=1,
        title="Seed",
        stage=SeedStage.EXECUTE,
        status=SeedStatus.QUEUED,
        stage_entered_at=now - timedelta(minutes=10),
    )
    agent = Agent(
        id="agt_1",
        handle="coder",
        name="Coder",
        kind=AgentKind.STAGE,
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        enabled=True,
    )
    comp_b = Backend(
        id="bnd_comp",
        name="Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        endpoint="tcp://127.0.0.1:2375",
        capacity=4,
        enabled=True,
        health=BackendHealth.HEALTHY,
    )
    run_active = Run(
        id="run_act",
        seed_id="sed_1",
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        compute_backend_id="bnd_comp",
        container_id="cid_act",
        status=RunStatus.RUNNING,
        started_at=now - timedelta(minutes=120),
        created_at=now - timedelta(minutes=120),
    )

    monkeypatch.setattr(sched_svc, "finalize_run_litellm_key", AsyncMock())
    monkeypatch.setattr(
        sched_svc, "fetch_docker_container_logs", AsyncMock(return_value="logs")
    )
    monkeypatch.setattr(sched_svc, "stop_and_remove_docker_container", AsyncMock())
    monkeypatch.setattr(
        sched_svc,
        "get_effective_stage_policy",
        AsyncMock(
            return_value=SimpleNamespace(
                timeout_minutes=30, max_attempts=3, agent_id="agt_1"
            )
        ),
    )

    # _reconcile_active_runs (timed out) & _cleanup_finished_run_containers
    db = _make_session()
    db.get.side_effect = [comp_b, seed, proj]
    db.execute.return_value = _exec_result(scalars_all=[run_active])
    await sched_svc._reconcile_active_runs(db)
    assert run_active.status == RunStatus.TIMED_OUT

    run_done = Run(
        id="run_done",
        seed_id="sed_1",
        stage=SeedStage.PLAN,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        compute_backend_id="bnd_comp",
        container_id="cid_done",
        status=RunStatus.SUCCEEDED,
    )
    db = _make_session()
    db.get.side_effect = [seed, proj, comp_b]
    db.execute.return_value = _exec_result(scalars_all=[run_done])
    await sched_svc._cleanup_finished_run_containers(db)
    assert run_done.container_id is None

    # _enqueue_runs_for_queued_seeds
    monkeypatch.setattr(
        sched_svc, "_project_has_connected_repository", AsyncMock(return_value=True)
    )
    monkeypatch.setattr(
        sched_svc,
        "_compute_remaining_budget",
        AsyncMock(return_value=(Decimal("50"), None, None)),
    )
    task = SeedTask(
        id="tsk_1", seed_id="sed_1", position=1, title="Task 1", status=TaskStatus.PENDING
    )
    db = _make_session()
    db.get.side_effect = [proj, agent]
    db.execute.side_effect = [
        _exec_result(scalars_all=[seed]),
        _exec_result(scalar_one_or_none=task),
    ]
    db.scalar.side_effect = [None, None, 0, 1]
    await sched_svc._enqueue_runs_for_queued_seeds(db)
    assert db.add.called

    # _dispatch_queued_runs
    queued_run = Run(
        id="run_q1",
        seed_id="sed_1",
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        status=RunStatus.QUEUED,
        created_at=now,
    )
    monkeypatch.setattr(sched_svc, "resolve_models_backend_for_run", AsyncMock())
    monkeypatch.setattr(
        sched_svc, "spawn_docker_runner_container", AsyncMock(return_value="cid_new")
    )
    db = _make_session()
    db.get.return_value = OrgSettings(id=1, max_concurrent_runners=4)
    db.execute.side_effect = [
        _exec_result(scalars_all=[comp_b]),
        _exec_result(all_rows=[]),
        _exec_result(all_rows=[(queued_run, seed, proj)]),
        _exec_result(scalar_one_or_none=queued_run),
    ]
    dispatched = await sched_svc._dispatch_queued_runs(db)
    assert dispatched == 1
    assert queued_run.container_id == "cid_new"

    # _dispatch_queued_runs failure recovery: cleans up partial containers and resets single-use prepare flags
    failed_spawn_run = Run(
        id="run_q2",
        seed_id="sed_1",
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="gpt-5.4",
        status=RunStatus.QUEUED,
        output={
            "_broker": {
                "prepare_context_used": True,
                "prepare_complete_used": True,
                "base_heads": {"org/repo": "sha1"},
            }
        },
        created_at=now,
    )
    stop_mock = AsyncMock()
    monkeypatch.setattr(sched_svc, "_stop_run_containers", stop_mock)
    monkeypatch.setattr(
        sched_svc,
        "spawn_docker_runner_container",
        AsyncMock(side_effect=RuntimeError("Docker daemon connection reset")),
    )
    db_fail = _make_session()
    db_fail.get.return_value = OrgSettings(id=1, max_concurrent_runners=4)
    db_fail.execute.side_effect = [
        _exec_result(scalars_all=[comp_b]),
        _exec_result(all_rows=[]),
        _exec_result(all_rows=[(failed_spawn_run, seed, proj)]),
        _exec_result(scalar_one_or_none=failed_spawn_run),
    ]
    dispatched_fail = await sched_svc._dispatch_queued_runs(db_fail)
    assert dispatched_fail == 0
    assert failed_spawn_run.status == RunStatus.QUEUED
    assert "Docker daemon connection reset" in (failed_spawn_run.error or "")
    stop_mock.assert_awaited_once_with(comp_b, None, "run_q2")
    assert "prepare_context_used" not in failed_spawn_run.output["_broker"]
    assert "prepare_complete_used" not in failed_spawn_run.output["_broker"]


@pytest.mark.asyncio
async def test_k8s_bootstrap_isolation_and_pod_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 1. Bootstrap compute backend pre-seeding (Kubernetes vs Docker)
    monkeypatch.setattr(compute_svc.settings, "GITSEED_BOOTSTRAP_COMPUTE_PROVIDER", "kubernetes")
    monkeypatch.setattr(compute_svc.settings, "K8S_API_URL", "https://kubernetes.default.svc")
    monkeypatch.setattr(compute_svc.settings, "RUNNER_K8S_NAMESPACE", "gitseed-runners")
    monkeypatch.setattr(compute_svc.settings, "SANDBOX_K8S_NAMESPACE", "gitseed-sandboxes")
    monkeypatch.setattr(compute_svc.settings, "K8S_NAMESPACE_ISOLATION", "workload")
    k8s_default = _build_default_compute_backend()
    assert k8s_default is not None
    assert k8s_default.provider == "kubernetes"
    assert k8s_default.name == "In-Cluster Kubernetes"
    assert k8s_default.config is not None
    assert k8s_default.config["namespace"] == "gitseed-runners"
    assert k8s_default.config["sandbox_namespace"] == "gitseed-sandboxes"
    assert k8s_default.config["namespace_isolation"] == "workload"

    monkeypatch.setattr(compute_svc.settings, "GITSEED_BOOTSTRAP_COMPUTE_PROVIDER", "docker")
    docker_default = _build_default_compute_backend()
    assert docker_default is not None
    assert docker_default.provider == "docker"

    monkeypatch.setattr(compute_svc.settings, "GITSEED_BOOTSTRAP_COMPUTE_PROVIDER", "none")
    assert _build_default_compute_backend() is None

    # 2. Namespace isolation resolution ("shared", "workload", "project")
    k8s_backend = Backend(
        id="bnd_k8s",
        name="K8s",
        type=BackendType.COMPUTE,
        provider="kubernetes",
        endpoint="https://kubernetes.default.svc",
        config={
            "namespace": "gitseed-runners",
            "sandbox_namespace": "gitseed-sandboxes",
            "namespace_isolation": "workload",
        },
        enabled=True,
        health=BackendHealth.HEALTHY,
    )
    assert (
        compute_svc.resolve_k8s_target_namespace(
            k8s_backend,
            "gitseed-runners",
            workload="runner",
            project_slug="alpha",
        )
        == "gitseed-runners"
    )
    assert (
        compute_svc.resolve_k8s_target_namespace(
            k8s_backend,
            "gitseed-runners",
            workload="sandbox",
            project_slug="alpha",
        )
        == "gitseed-sandboxes"
    )

    k8s_backend.config["namespace_isolation"] = "project"
    assert (
        compute_svc.resolve_k8s_target_namespace(
            k8s_backend,
            "gitseed-runners",
            workload="runner",
            project_slug="Alpha_Project",
        )
        == "gitseed-runners-alpha-project"
    )
    assert (
        compute_svc.resolve_k8s_target_namespace(
            k8s_backend,
            "gitseed-runners",
            workload="sandbox",
            project_slug="Alpha_Project",
        )
        == "gitseed-sandboxes-alpha-project"
    )

    k8s_backend.config["namespace_isolation"] = "shared"
    assert (
        compute_svc.resolve_k8s_target_namespace(
            k8s_backend,
            "gitseed-runners",
            workload="sandbox",
            project_slug="alpha",
        )
        == "gitseed-runners"
    )

    # 3. Spawning and inspecting isolated K8s sandbox Pods
    k8s_backend.config["namespace_isolation"] = "project"
    mock_client = AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get = AsyncMock(
        return_value=MagicMock(
            status_code=200,
            text="sandbox pod output",
            json=lambda: {
                "status": {
                    "phase": "Running",
                    "containerStatuses": [{"state": {"running": {}}}],
                }
            },
        )
    )
    mock_client.post = AsyncMock(return_value=MagicMock(status_code=201, json=lambda: {}))
    mock_client.delete = AsyncMock(return_value=MagicMock(status_code=200))

    monkeypatch.setattr(
        compute_svc,
        "_k8s_client_for_backend",
        lambda _b: (mock_client, "https://kubernetes.default.svc", "gitseed-runners"),
    )

    pod_ref = await compute_svc.spawn_k8s_pod(
        backend=k8s_backend,
        pod_name="gitseed-sbx-test123",
        image="gitseed/runner:dev",
        command=["/bin/sh", "-c", "sleep 3600"],
        env_map={"FOO": "bar"},
        labels={"gitseed.io/sandbox-id": "sbx_test123"},
        workload="sandbox",
        project_slug="alpha",
    )
    assert pod_ref == "gitseed-sandboxes-alpha/gitseed-sbx-test123"

    state = await compute_svc.inspect_docker_container_state(k8s_backend, pod_ref)
    assert state["exists"] is True and state["running"] is True

    logs = await compute_svc.fetch_docker_container_logs(k8s_backend, pod_ref)
    assert logs == "sandbox pod output"

    await compute_svc.stop_and_remove_docker_container(k8s_backend, pod_ref)


def test_context_window_tracking_helpers() -> None:
    """Verify per-model context window resolution, per-turn log extraction, and run output merging."""
    assert resolve_model_context_limit("claude-sonnet-4-20250514") == 200_000
    assert resolve_model_context_limit("gemini-2.5-pro") == 1_000_000
    assert resolve_model_context_limit("gpt-4o") == 128_000
    assert resolve_model_context_limit("unknown-custom-model") is None
    assert resolve_model_context_limit("default") is None

    custom_backend = Backend(
        id="b_m1",
        name="Custom",
        type=BackendType.INFERENCE,
        provider="openai_compatible",
        config={
            "discovered_models": [
                {"id": "my-local-qwen", "context_length": 65536},
            ]
        },
    )
    assert resolve_model_context_limit("my-local-qwen", backend=custom_backend) == 65536

    assert build_context_window_info(used_tokens=0, peak_tokens=0, max_tokens=200_000) is None
    assert build_context_window_info(used_tokens=10_000, peak_tokens=10_000, max_tokens=None) is None

    cw = build_context_window_info(
        used_tokens=42_000,
        peak_tokens=50_000,
        max_tokens=200_000,
        turns=4,
    )
    assert cw is not None
    assert cw["used_tokens"] == 42_000
    assert cw["peak_tokens"] == 50_000
    assert cw["max_tokens"] == 200_000
    assert cw["utilization_pct"] == 21.0
    assert cw["turns"] == 4

    # Unknown model without backend limit does not fabricate context_window
    unmerged = merge_run_context_window(
        {"summary": "done"},
        used_tokens=30_000,
        peak_tokens=30_000,
        turns=1,
        model="unrecognized-local-model",
    )
    assert "context_window" not in unmerged

    merged = merge_run_context_window(
        {"summary": "done"},
        used_tokens=30_000,
        peak_tokens=64_000,
        turns=5,
        model="claude-sonnet-4",
    )
    assert merged["summary"] == "done"
    assert merged["context_window"]["used_tokens"] == 30_000
    assert merged["context_window"]["peak_tokens"] == 64_000
    assert merged["context_window"]["max_tokens"] == 200_000
    assert merged["context_window"]["turns"] == 5

    # Verify per-turn LiteLLM spend logs extract latest turn vs peak turn (not cumulative sum)
    sample_logs = [
        {
            "request_id": "req_1",
            "api_key_alias": "run:r_123",
            "startTime": "2026-11-11T10:00:01Z",
            "prompt_tokens": 12000,
            "completion_tokens": 800,
        },
        {
            "request_id": "req_2",
            "api_key_alias": "run:r_123",
            "startTime": "2026-11-11T10:00:10Z",
            "prompt_tokens": 45000,
            "completion_tokens": 1500,
        },
        {
            "request_id": "req_3",
            "api_key_alias": "run:r_123",
            "startTime": "2026-11-11T10:00:20Z",
            "prompt_tokens": 38000,
            "completion_tokens": 600,
        },
    ]
    latest_tok, peak_tok, turn_cnt = _extract_context_stats_from_logs(
        sample_logs,
        expected_alias="run:r_123",
        virtual_key="sk-test",
        token_hashes=set(),
        meta_id_key="run_id",
        meta_id_val="r_123",
    )
    assert latest_tok == 38_600
    assert peak_tok == 46_500
    assert turn_cnt == 3


@pytest.mark.asyncio
async def test_three_container_broker_docker_and_k8s_spawning(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify prepare/agent/finalize separation in Docker containers and K8s Job specs."""
    from gitseed_common.enums import ProjectVisibility, SeedAutonomy
    from gitseed_core.security import verify_run_token

    docker_b = Backend(
        id="bnd_docker_broker",
        name="Docker",
        type=BackendType.COMPUTE,
        provider="docker",
        endpoint="http://docker:2375",
        config={"cpus": 2.0, "memory_mb": 2048},
    )
    proj = Project(
        id="prj_b1",
        slug="broker-proj",
        name="Broker Proj",
        visibility=ProjectVisibility.PRIVATE,
    )
    seed = Seed(
        id="sed_b1",
        project_id="prj_b1",
        number=1,
        title="Test",
        stage=SeedStage.EXECUTE,
        status=SeedStatus.RUNNING,
        autonomy=SeedAutonomy.SUPERVISED,
    )
    run = Run(
        id="run_b1",
        seed_id="sed_b1",
        stage=SeedStage.EXECUTE,
        attempt=1,
        agent_id="agt_1",
        runtime=AgentRuntime.CLAUDE_CODE,
        model="claude-sonnet-4",
        status=RunStatus.STARTING,
    )

    created_containers: list[tuple[str, dict[str, Any]]] = []
    docker_client = AsyncMock()
    docker_client.__aenter__.return_value = docker_client

    async def _docker_post(url: str, **kwargs: Any) -> Any:
        resp = MagicMock()
        if "/containers/create" in url:
            name = url.split("name=")[-1]
            created_containers.append((name, kwargs.get("json") or {}))
            resp.status_code = 201
            resp.json.return_value = {"Id": f"cid_{name}"}
        else:
            resp.status_code = 204
            resp.json.return_value = {}
        return resp

    docker_client.post = AsyncMock(side_effect=_docker_post)
    docker_client.get = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {}))
    docker_client.delete = AsyncMock(return_value=MagicMock(status_code=204))
    monkeypatch.setattr(
        compute_svc,
        "_docker_client_for_backend",
        lambda _b: (docker_client, "http://docker:2375"),
    )
    monkeypatch.setattr(
        compute_svc.settings, "RUNNER_EGRESS_PROXY_URL", "http://egress-proxy:3128"
    )

    cid = await compute_svc.spawn_docker_runner_container(
        backend=docker_b,
        run=run,
        seed=seed,
        project=proj,
        run_token=create_run_token(run.id, scope="agent"),
        prepare_token=create_run_token(run.id, scope="prepare"),
        finalize_token=create_run_token(run.id, scope="finalize"),
    )
    assert cid == "cid_gitseed-run-run_b1"
    assert len(created_containers) == 3
    by_role = {
        payload["Labels"]["app.gitseed.role"]: payload
        for _, payload in created_containers
    }
    assert set(by_role.keys()) == {"prepare", "agent", "finalize"}

    # Agent container: no ExtraHosts, no git-mirror mount, has proxy env vars, has agent mode
    agent_cfg = by_role["agent"]
    assert "ExtraHosts" not in agent_cfg["HostConfig"]
    agent_binds = agent_cfg["HostConfig"]["Binds"]
    assert not any("git-mirrors" in b for b in agent_binds)
    assert any("gitseed-ctl-run_b1:/gitseed-ctl" in b for b in agent_binds)
    agent_env = dict(item.split("=", 1) for item in agent_cfg["Env"])
    assert agent_env["GITSEED_RUNNER_MODE"] == "agent"
    assert agent_env["HTTPS_PROXY"] == "http://egress-proxy:3128"
    assert agent_env["HTTP_PROXY"] == "http://egress-proxy:3128"
    assert (
        verify_run_token(agent_env["GITSEED_RUN_TOKEN"], required_scope="agent")
        == run.id
    )

    # Finalize container: workspace mounted read-only (:ro)
    fin_cfg = by_role["finalize"]
    fin_binds = fin_cfg["HostConfig"]["Binds"]
    assert any(b.endswith(":/workspace:ro") for b in fin_binds)
    fin_env = dict(item.split("=", 1) for item in fin_cfg["Env"])
    assert fin_env["GITSEED_RUNNER_MODE"] == "finalize"
    assert (
        verify_run_token(fin_env["GITSEED_RUN_TOKEN"], required_scope="finalize")
        == run.id
    )



