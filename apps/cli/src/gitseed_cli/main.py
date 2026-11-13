from __future__ import annotations

import sys
from typing import Any

import typer
from gitseed_common import __version__
from typer.core import TyperGroup

from gitseed_cli.client import CLIError
from gitseed_cli.commands.auth import register_auth_commands
from gitseed_cli.commands.receipt import receipt_app
from gitseed_cli.commands.resources import register_resource_commands
from gitseed_cli.commands.sandbox import sandbox_app, session_app
from gitseed_cli.commands.workflow import register_workflow_commands
from gitseed_cli.config import RuntimeOptions, get_opts, make_client
from gitseed_cli.formatting import err_console, escape
from gitseed_cli.tui import VIEWS, run_tui

_GLOBAL_VALUE_FLAGS = {
    "-p",
    "--project",
    "-o",
    "--output",
    "--context",
    "--api-url",
    "--token",
}
_GLOBAL_EQ_PREFIXES = (
    "--project=",
    "--output=",
    "--context=",
    "--api-url=",
    "--token=",
)
_BOOLEAN_FLAGS = {
    "-h",
    "--help",
    "-V",
    "--version",
    "--once",
    "--include-archived",
    "-A",
    "--all-projects",
    "--staged",
    "--follow",
    "-w",
    "--watch",
    "--propose-seeds",
    "--as-root",
    "--eof",
    "--fast-track",
    "--yes",
    "-y",
    "--force",
    "--dry-run",
    "--wait",
    "--no-wait",
    "--offline",
}


def _is_logs_command(args: list[str]) -> bool:
    """Return True only when the command tokens resolve to `logs` or `sandbox logs`."""
    cmd_tokens: list[str] = []
    value_flags = _GLOBAL_VALUE_FLAGS | {"-f", "--file", "--format"}
    j = 0
    while j < len(args) and len(cmd_tokens) < 2:
        tok = args[j]
        if tok == "--":
            break
        if tok.startswith("-") and "=" in tok:
            j += 1
            continue
        if tok in value_flags:
            j += 2
            continue
        if tok.startswith("-"):
            j += 1 if tok in _BOOLEAN_FLAGS else 2
            continue
        cmd_tokens.append(tok)
        if len(cmd_tokens) == 1:
            if cmd_tokens[0] == "logs":
                return True
            if cmd_tokens[0] != "sandbox":
                return False
        j += 1
    return tuple(cmd_tokens[:2]) == ("sandbox", "logs")


class GlobalOptionsGroup(TyperGroup):
    """Allow kubectl-style global flags (-p, -o, --context, --api-url, --token) anywhere in argv."""

    def parse_args(self, ctx: Any, args: list[str]) -> list[str]:
        hoisted: list[str] = []
        rest: list[str] = []
        is_logs_cmd = _is_logs_command(args)
        i = 0
        while i < len(args):
            arg = args[i]
            if arg == "--":
                rest.extend(args[i:])
                break
            if arg in _GLOBAL_VALUE_FLAGS and i + 1 < len(args):
                hoisted.extend([arg, args[i + 1]])
                i += 2
                continue
            if any(arg.startswith(p) for p in _GLOBAL_EQ_PREFIXES):
                hoisted.append(arg)
                i += 1
                continue
            if arg.startswith(("-p=", "-o=")):
                hoisted.extend([arg[:2], arg[3:]])
                i += 1
                continue
            if (
                arg.startswith("-")
                and "=" not in arg
                and arg not in _BOOLEAN_FLAGS
                and not (arg == "-f" and is_logs_cmd)
                and i + 1 < len(args)
            ):
                rest.extend([arg, args[i + 1]])
                i += 2
                continue
            rest.append(arg)
            i += 1
        args[:] = hoisted + rest
        return super().parse_args(ctx, args)

    def invoke(self, ctx: Any) -> object:
        try:
            return super().invoke(ctx)
        except CLIError as exc:
            prefix = (
                f"[red]Error ({exc.status_code}):[/red]" if exc.status_code else "[red]Error:[/red]"
            )
            err_console.print(f"{prefix} {escape(exc.message)}")
            raise typer.Exit(1) from exc


app = typer.Typer(
    name="gitseed",
    cls=GlobalOptionsGroup,
    help=(
        "GitSeed CLI: kubectl-inspired command-line interface for "
        "GitSeed SDLC orchestration, stage gates, and Markdown manifests."
    ),
    no_args_is_help=True,
    add_completion=False,
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"gitseed {__version__}")
        raise typer.Exit(0)


@app.callback()
def main_callback(
    ctx: typer.Context,
    project: str | None = typer.Option(
        None,
        "-p",
        "--project",
        envvar="GITSEED_PROJECT",
        help="Target project slug or ID (overrides context default).",
    ),
    output: str = typer.Option(
        "table",
        "-o",
        "--output",
        help="Output format: table | wide | json | md.",
    ),
    context: str | None = typer.Option(
        None,
        "--context",
        envvar="GITSEED_CONTEXT",
        help="Named CLI context to use.",
    ),
    api_url: str | None = typer.Option(
        None,
        "--api-url",
        envvar="GITSEED_API_URL",
        help="GitSeed Core API base URL.",
    ),
    token: str | None = typer.Option(
        None,
        "--token",
        envvar="GITSEED_TOKEN",
        help="GitSeed personal access token (gsp_...).",
    ),
    version: bool | None = typer.Option(
        None,
        "--version",
        "-V",
        callback=_version_callback,
        is_eager=True,
        help="Show GitSeed CLI version and exit.",
    ),
) -> None:
    valid_outputs = {"table", "wide", "json", "md"}
    fmt = output.strip().lower()
    if fmt not in valid_outputs:
        err_console.print(
            f"[red]Error:[/red] Unsupported output format '{output}'. "
            f"Choose one of: {', '.join(sorted(valid_outputs))}."
        )
        raise typer.Exit(2)

    ctx.obj = RuntimeOptions(
        context=context,
        api_url=api_url,
        token=token,
        project=project,
        output=fmt,
    )


register_resource_commands(app)
register_workflow_commands(app)
register_auth_commands(app)
app.add_typer(receipt_app, name="receipt")
app.add_typer(sandbox_app, name="sandbox")
app.add_typer(session_app, name="session")


@app.command("tui")
@app.command("dash", hidden=True)
def tui_cmd(
    ctx: typer.Context,
    project: str | None = typer.Option(
        None,
        "-p",
        "--project",
        help="Initial project filter (slug or ID).",
    ),
    view: str = typer.Option(
        "seeds",
        "--view",
        "-v",
        help="Initial view: seeds | projects | runs | backends.",
    ),
    refresh: float = typer.Option(
        3.0,
        "--refresh",
        help="Auto-refresh interval in seconds.",
    ),
    once: bool = typer.Option(
        False,
        "--once",
        help="Render a single dashboard frame and exit.",
    ),
) -> None:
    """Launch the interactive GitSeed Terminal UI (TUI) dashboard."""
    chosen_view = view.strip().lower()
    if chosen_view not in VIEWS:
        err_console.print(
            f"[red]Error:[/red] Unknown TUI view '{view}'. Choose one of: {', '.join(VIEWS)}."
        )
        raise typer.Exit(2)

    opts = get_opts(ctx)
    client = make_client(ctx)
    target_project = project or opts.project or client.default_project
    run_tui(
        client,
        initial_project=target_project,
        initial_view=chosen_view,
        refresh_interval=refresh,
        once=once,
    )


def cli() -> None:
    """Entrypoint wrapper that renders CLIError cleanly without raw tracebacks."""
    try:
        app()
    except CLIError as exc:
        prefix = (
            f"[red]Error ({exc.status_code}):[/red]" if exc.status_code else "[red]Error:[/red]"
        )
        err_console.print(f"{prefix} {escape(exc.message)}")
        sys.exit(1)


if __name__ == "__main__":
    cli()
