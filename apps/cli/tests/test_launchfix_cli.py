from __future__ import annotations

import pytest
from gitseed_cli import config as cli_config
from gitseed_cli.tui.state import TUIState
from gitseed_cli.tui.views import _render_inspector_tab_content
from rich.console import Console


def test_resolve_connection_does_not_leak_stored_token_to_different_origin(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        cli_config,
        "load_config",
        lambda: cli_config.CLIConfigFile(
            current_context="default",
            contexts={
                "default": cli_config.ContextConfig(
                    api_url="http://localhost:8420", project="alpha"
                )
            },
        ),
    )
    monkeypatch.setattr(cli_config, "load_token", lambda _ctx: "gsp_stored_secret")
    monkeypatch.delenv("GITSEED_API_URL", raising=False)
    monkeypatch.delenv("GITSEED_API_TOKEN", raising=False)
    monkeypatch.delenv("GITSEED_TOKEN", raising=False)

    # Same origin -> uses stored token
    _, url1, tok1, _ = cli_config.resolve_connection(
        cli_config.RuntimeOptions(api_url="http://localhost:8420/")
    )
    assert url1 == "http://localhost:8420"
    assert tok1 == "gsp_stored_secret"

    # Different origin -> does not leak stored token
    _, url2, tok2, _ = cli_config.resolve_connection(
        cli_config.RuntimeOptions(api_url="https://evil.example.com")
    )
    assert url2 == "https://evil.example.com"
    assert tok2 is None


def test_tui_logs_tab_sanitizes_terminal_escape_sequences() -> None:
    state = TUIState()
    state.inspector_tab = "logs"
    state.active_run_log = "normal line\n\x1b]52;c;ZWNobyBpwg==\x07injected\x1b[2J"
    panel = _render_inspector_tab_content(state, None)  # type: ignore[arg-type]
    c = Console(width=80, record=True)
    c.print(panel)
    rendered = c.export_text()
    assert "\x1b]52" not in rendered
    assert "normal line" in rendered
    assert "injected" in rendered
