from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from gitseed_common.enums import (
    AgentRuntime,
    BackendType,
    QueueStrategy,
    RunStatus,
    SeedStage,
)
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import Project
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed
from gitseed_core.security import create_run_token, encrypt_secrets, verify_run_token
from gitseed_core.services.agents import get_runtime_model_compatibility_warning
from gitseed_core.services.gateway import (
    build_litellm_model_payloads,
    derive_litellm_virtual_key,
)
from gitseed_core.services.scheduler import _order_candidates_by_strategy
from gitseed_core.services.seeds import extract_agent_mentions
from gitseed_runner.adapters import create_runtime_adapter
from gitseed_runner.schemas import validate_stage_output_data
from pydantic import ValidationError


def test_run_token_signing_and_expiry() -> None:
    token = create_run_token("run_abc123456789", ttl_seconds=600)
    assert token.startswith("gsr_")
    assert verify_run_token(token) == "run_abc123456789"
    assert verify_run_token("gsr_tampered") is None
    assert verify_run_token("invalid") is None

    expired = create_run_token("run_expired12345", ttl_seconds=-10)
    assert verify_run_token(expired) is None


def test_soft_runtime_model_compatibility_warning() -> None:
    # Cloud providers return no warning
    assert (
        get_runtime_model_compatibility_warning(
            AgentRuntime.CLAUDE_CODE, "anthropic", "claude-sonnet-5-5"
        )
        is None
    )
    assert (
        get_runtime_model_compatibility_warning(
            AgentRuntime.CODEX, "openai", "gpt-5.4"
        )
        is None
    )

    # Local providers return a soft warning rather than blocking save
    warning_cc_ollama = get_runtime_model_compatibility_warning(
        AgentRuntime.CLAUDE_CODE, "ollama", "qwen2.5-coder:32b"
    )
    assert warning_cc_ollama is not None
    assert "LiteLLM" in warning_cc_ollama

    warning_opencode_ollama = get_runtime_model_compatibility_warning(
        AgentRuntime.OPENCODE, "ollama", "qwen2.5-coder:32b"
    )
    assert warning_opencode_ollama is not None
    assert "tool calling" in warning_opencode_ollama

    warning_gemini_ollama = get_runtime_model_compatibility_warning(
        AgentRuntime.GEMINI, "ollama", "qwen2.5-coder:32b"
    )
    assert warning_gemini_ollama is not None
    assert "LiteLLM" in warning_gemini_ollama

    warning_grok_ollama = get_runtime_model_compatibility_warning(
        AgentRuntime.GROK_BUILD, "ollama", "qwen2.5-coder:32b"
    )
    assert warning_grok_ollama is not None
    assert "LiteLLM" in warning_grok_ollama


def test_litellm_model_sync_payloads_and_virtual_key() -> None:
    backend = Backend(
        id="bnd_ollama123",
        name="Local Ollama",
        type=BackendType.INFERENCE,
        provider="ollama",
        endpoint="http://localhost:11434",
        config={
            "models": [
                {"id": "qwen2.5-coder:32b", "enabled": True},
                {"id": "nomic-embed-text", "enabled": False},
            ],
            "input_cost_per_million": 0.25,
            "output_cost_per_million": 0.75,
        },
        secrets_encrypted=encrypt_secrets({}),
        enabled=True,
    )
    payloads = build_litellm_model_payloads(backend)
    assert len(payloads) == 1
    entry = payloads[0]
    assert entry["model_name"] == "qwen2.5-coder:32b"
    assert entry["litellm_params"]["model"] == "ollama_chat/qwen2.5-coder:32b"
    assert entry["litellm_params"]["api_base"] == "http://host.docker.internal:11434"
    assert entry["litellm_params"]["input_cost_per_token"] == pytest.approx(0.25 / 1_000_000)
    assert entry["model_info"]["id"] == "bnd_ollama123:qwen2.5-coder:32b"
    assert entry["model_info"]["metadata"]["backend_id"] == "bnd_ollama123"

    gemini_backend = Backend(
        id="bnd_gemini123",
        name="Google Gemini",
        type=BackendType.INFERENCE,
        provider="gemini",
        endpoint="https://generativelanguage.googleapis.com/v1beta",
        config={
            "models": [{"id": "gemini-4-argon", "enabled": True}],
        },
        secrets_encrypted=encrypt_secrets({"api_key": "AIzaSyTestKey"}),
        enabled=True,
    )
    gemini_payloads = build_litellm_model_payloads(gemini_backend)
    assert len(gemini_payloads) == 1
    assert gemini_payloads[0]["litellm_params"]["model"] == "gemini/gemini-4-argon"
    assert gemini_payloads[0]["litellm_params"]["api_key"] == "AIzaSyTestKey"

    xai_backend = Backend(
        id="bnd_xai123",
        name="xAI Grok",
        type=BackendType.INFERENCE,
        provider="xai",
        endpoint="https://api.x.ai/v1",
        config={
            "models": [{"id": "grok-4.7", "enabled": True}],
        },
        secrets_encrypted=encrypt_secrets({"api_key": "xai-test-key"}),
        enabled=True,
    )
    xai_payloads = build_litellm_model_payloads(xai_backend)
    assert len(xai_payloads) == 1
    assert xai_payloads[0]["litellm_params"]["model"] == "openai/grok-4.7"
    assert xai_payloads[0]["litellm_params"]["api_base"] == "https://api.x.ai/v1"
    assert xai_payloads[0]["litellm_params"]["api_key"] == "xai-test-key"

    vk = derive_litellm_virtual_key("run_test999")
    assert vk.startswith("sk-gsr-run_test999-")
    assert derive_litellm_virtual_key("run_test999") == vk


def test_runner_adapter_uses_litellm_virtual_key_not_run_token(tmp_path: Path) -> None:
    ctx = {
        "runtime": "opencode",
        "model": "qwen2.5-coder:32b",
        "gateway": {
            "base_url": "http://litellm:4000/v1",
            "anthropic_base_url": "http://litellm:4000",
            "api_key": "sk-gsr-run_123-virtualkey",
            "model": "qwen2.5-coder:32b",
            "provider": "ollama",
        },
    }
    output_file = tmp_path / ".gitseed" / "output.json"
    adapter = create_runtime_adapter(
        ctx=ctx,
        working_dir=tmp_path,
        output_file=output_file,
    )
    env = adapter.prepare_workspace()
    assert env["OPENAI_API_KEY"] == "sk-gsr-run_123-virtualkey"
    assert env["OPENAI_BASE_URL"] == "http://litellm:4000/v1"
    assert "gsr_" not in env["OPENAI_API_KEY"]
    assert (tmp_path / "opencode.json").exists()

    # Verify Antigravity headless CLI adapter (`agy -p ... --dangerously-skip-permissions`)
    agy_ctx = {**ctx, "runtime": "antigravity"}
    agy_adapter = create_runtime_adapter(
        ctx=agy_ctx,
        working_dir=tmp_path,
        output_file=output_file,
    )
    assert agy_adapter.binary_name == "agy"
    assert agy_adapter.build_command("Refine spec") == [
        "agy",
        "-p",
        "Refine spec",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--model",
        "qwen2.5-coder:32b",
    ]
    assert agy_adapter.build_command("Steering update", continue_session=True) == [
        "agy",
        "-p",
        "Steering update",
        "--output-format",
        "stream-json",
        "--dangerously-skip-permissions",
        "--model",
        "qwen2.5-coder:32b",
        "--continue",
    ]

    # Verify Claude Code adapter (`claude -p`) uses ANTHROPIC_AUTH_TOKEN against LiteLLM
    cc_ctx = {**ctx, "runtime": "claude-code"}
    cc_adapter = create_runtime_adapter(
        ctx=cc_ctx,
        working_dir=tmp_path,
        output_file=output_file,
    )
    assert cc_adapter.binary_name == "claude"
    cc_env = cc_adapter.prepare_workspace()
    assert cc_env["ANTHROPIC_BASE_URL"] == "http://litellm:4000"
    assert cc_env["ANTHROPIC_AUTH_TOKEN"] == "sk-gsr-run_123-virtualkey"
    assert "ANTHROPIC_API_KEY" not in cc_env

    # Verify Codex adapter (`codex exec`)
    codex_ctx = {**ctx, "runtime": "codex"}
    codex_adapter = create_runtime_adapter(
        ctx=codex_ctx,
        working_dir=tmp_path,
        output_file=output_file,
    )
    assert codex_adapter.binary_name == "codex"
    assert codex_adapter.build_command("Implement feature") == [
        "codex",
        "exec",
        "--json",
        "--full-auto",
        "--skip-git-repo-check",
        "-m",
        "qwen2.5-coder:32b",
        "Implement feature",
    ]

    # Verify Gemini CLI adapter (`gemini -p ... --yolo`)
    gemini_ctx = {**ctx, "runtime": "gemini"}
    gemini_adapter = create_runtime_adapter(
        ctx=gemini_ctx,
        working_dir=tmp_path,
        output_file=output_file,
    )
    assert gemini_adapter.binary_name == "gemini"
    gemini_env = gemini_adapter.prepare_workspace()
    assert gemini_env["GEMINI_API_KEY"] == "sk-gsr-run_123-virtualkey"
    assert gemini_adapter.build_command("Refine spec") == [
        "gemini",
        "-p",
        "Refine spec",
        "--output-format",
        "stream-json",
        "--yolo",
        "--model",
        "qwen2.5-coder:32b",
    ]
    assert gemini_adapter.build_command("Steering update", continue_session=True) == [
        "gemini",
        "-p",
        "Steering update",
        "--output-format",
        "stream-json",
        "--yolo",
        "--model",
        "qwen2.5-coder:32b",
        "--resume",
        "latest",
    ]

    # Verify Grok Build CLI adapter (`grok -p ... --full-auto`)
    grok_ctx = {**ctx, "runtime": "grok-build"}
    grok_adapter = create_runtime_adapter(
        ctx=grok_ctx,
        working_dir=tmp_path,
        output_file=output_file,
    )
    assert grok_adapter.binary_name == "grok"
    grok_env = grok_adapter.prepare_workspace()
    assert grok_env["XAI_API_KEY"] == "sk-gsr-run_123-virtualkey"
    assert grok_env["GROK_API_KEY"] == "sk-gsr-run_123-virtualkey"
    assert grok_adapter.build_command("Implement feature") == [
        "grok",
        "-p",
        "Implement feature",
        "--full-auto",
        "--model",
        "qwen2.5-coder:32b",
    ]
    assert grok_adapter.build_command("Steering update", continue_session=True) == [
        "grok",
        "-p",
        "Steering update",
        "--full-auto",
        "--model",
        "qwen2.5-coder:32b",
        "--continue",
    ]


def test_fifo_and_fair_share_queue_ordering() -> None:
    now = datetime.now(UTC)
    proj_a = Project(id="prj_a", name="Alpha", slug="alpha")
    proj_b = Project(id="prj_b", name="Beta", slug="beta")

    seed_a1 = Seed(id="sed_a1", project_id="prj_a", number=1, title="A1")
    seed_a2 = Seed(id="sed_a2", project_id="prj_a", number=2, title="A2")
    seed_b1 = Seed(id="sed_b1", project_id="prj_b", number=1, title="B1")

    run_a1 = Run(
        id="run_a1",
        seed_id="sed_a1",
        stage=SeedStage.IDEATE,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="default",
        status=RunStatus.QUEUED,
        created_at=now - timedelta(seconds=30),
    )
    run_a2 = Run(
        id="run_a2",
        seed_id="sed_a2",
        stage=SeedStage.IDEATE,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="default",
        status=RunStatus.QUEUED,
        created_at=now - timedelta(seconds=20),
    )
    run_b1 = Run(
        id="run_b1",
        seed_id="sed_b1",
        stage=SeedStage.IDEATE,
        agent_id="agt_1",
        runtime=AgentRuntime.OPENCODE,
        model="default",
        status=RunStatus.QUEUED,
        created_at=now - timedelta(seconds=10),
    )

    candidates = [
        (run_a1, seed_a1, proj_a),
        (run_a2, seed_a2, proj_a),
        (run_b1, seed_b1, proj_b),
    ]

    # FIFO: strictly ordered by created_at (run_a1, run_a2, run_b1)
    fifo_order = _order_candidates_by_strategy(
        candidates, QueueStrategy.FIFO, {"prj_a": 1, "prj_b": 0}
    )
    assert [r.id for r, _, _ in fifo_order] == ["run_a1", "run_a2", "run_b1"]

    # Fair-Share when prj_a already has 1 active run and prj_b has 0:
    # prj_b gets prioritized first (run_b1), then prj_a (run_a1, run_a2)
    fair_order = _order_candidates_by_strategy(
        candidates, QueueStrategy.FAIR_SHARE, {"prj_a": 1, "prj_b": 0}
    )
    assert [r.id for r, _, _ in fair_order] == ["run_b1", "run_a1", "run_a2"]


def test_stage_output_json_validation() -> None:
    valid_ideate = validate_stage_output_data(
        "ideate",
        {
            "specification": "## Auth Service\nRefine OAuth and session handling.",
            "acceptance_criteria": [
                "Users can sign in with session cookies",
                "Expired tokens return 401",
            ],
            "summary_comment": "Refined specification and added 2 acceptance criteria.",
        },
    )
    assert len(valid_ideate["acceptance_criteria"]) == 2

    with pytest.raises(ValidationError):
        validate_stage_output_data(
            "ideate",
            {
                "specification": "too short",
                "acceptance_criteria": [],
                "summary_comment": "ok",
            },
        )


def test_extract_agent_mentions() -> None:
    mentions = extract_agent_mentions(
        "Hey @architect please also include rate limiting, cc @coder!"
    )
    assert mentions == {"architect", "coder"}
    assert extract_agent_mentions("Looks good to me, LGTM!") == set()
    assert Decimal("0.01") > Decimal("0")
