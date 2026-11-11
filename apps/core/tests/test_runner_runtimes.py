from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from gitseed_common.enums import BackendType
from gitseed_core.config import settings
from gitseed_core.services import runner_runtimes

from conftest import _exec_result, _make_backend, _make_session


@pytest.fixture(autouse=True)
def _reset_probe_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    runner_runtimes.clear_runtime_probe_cache()
    monkeypatch.setattr(settings, "RUNNER_RUNTIMES", "")


def _docker_backend() -> Any:
    backend = _make_backend("bnd_docker12345", BackendType.COMPUTE)
    backend.provider = "docker"
    backend.endpoint = "unix:///var/run/docker.sock"
    backend.config = {"runner_image": "gitseed/runner:test"}
    return backend


def test_parse_runtime_list() -> None:
    assert runner_runtimes.parse_runtime_list("") is None
    assert runner_runtimes.parse_runtime_list(" , ") is None
    parsed = runner_runtimes.parse_runtime_list("opencode, Codex ,gemini")
    assert parsed is not None
    assert parsed["opencode"] is True
    assert parsed["codex"] is True
    assert parsed["gemini"] is True
    assert parsed["claude-code"] is False
    assert parsed["antigravity"] is False
    assert parsed["grok-build"] is False


def test_availability_from_binaries_covers_every_runtime() -> None:
    result = runner_runtimes._availability_from_binaries({"opencode", "claude", "grok"})
    assert set(result) == set(runner_runtimes.RUNTIME_BINARIES)
    assert result["opencode"] and result["claude-code"] and result["grok-build"]
    assert not result["codex"] and not result["antigravity"] and not result["gemini"]


def test_probe_script_checks_all_binaries() -> None:
    script = runner_runtimes._probe_script()
    for binary in ("opencode", "claude", "codex", "agy", "gemini", "grok"):
        assert binary in script


@pytest.mark.asyncio
async def test_config_override_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RUNNER_RUNTIMES", "opencode,claude-code")
    session = _make_session()
    result = await runner_runtimes.get_runtime_availability(session)
    assert result["source"] == "config"
    assert result["available"]["claude-code"] is True
    assert result["available"]["grok-build"] is False
    session.execute.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_without_docker_backend() -> None:
    k8s = _make_backend("bnd_k8s1234567", BackendType.COMPUTE)
    k8s.provider = "kubernetes"
    session = _make_session()
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[k8s]))
    result = await runner_runtimes.get_runtime_availability(session)
    assert result == {"available": None, "source": "unknown", "image": None}


@pytest.mark.asyncio
async def test_docker_probe_result_and_failure_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    session = _make_session()
    session.execute = AsyncMock(return_value=_exec_result(scalars_all=[_docker_backend()]))

    probe = AsyncMock(
        return_value=runner_runtimes._availability_from_binaries({"opencode", "codex", "gemini"})
    )
    monkeypatch.setattr(runner_runtimes, "_probe_docker_image", probe)
    result = await runner_runtimes.get_runtime_availability(session)
    assert result["source"] == "probe"
    assert result["image"] == "gitseed/runner:test"
    assert result["available"]["gemini"] is True
    assert result["available"]["claude-code"] is False
    assert result["available"]["grok-build"] is False

    # A failing probe reports unknown and is not retried immediately.
    failing = AsyncMock(side_effect=httpx.ConnectError("docker down"))
    monkeypatch.setattr(runner_runtimes, "_probe_docker_image", failing)
    runner_runtimes.clear_runtime_probe_cache()
    first = await runner_runtimes.get_runtime_availability(session)
    second = await runner_runtimes.get_runtime_availability(session)
    assert first["available"] is None and first["source"] == "unknown"
    assert second["available"] is None
    assert failing.await_count == 1


@pytest.mark.asyncio
async def test_runtime_availability_route(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        runner_runtimes,
        "get_runtime_availability",
        AsyncMock(
            return_value={
                "available": {"opencode": True, "grok-build": False},
                "source": "probe",
                "image": "gitseed/runner:dev",
            }
        ),
    )
    resp = await client.get("/api/v1/agents/runtimes/availability")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "probe"
    assert body["available"]["grok-build"] is False

