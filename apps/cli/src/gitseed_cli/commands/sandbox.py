from __future__ import annotations

import time

import typer
from gitseed_common.enums import SANDBOX_ACTIVE_STATUSES, AgentRuntime
from gitseed_common.schemas import (
    CreateAgentSessionRequest,
    CreateSandboxSessionRequest,
    SandboxInputRequest,
    SendAgentSessionMessageRequest,
)
from rich.markup import escape
from rich.panel import Panel
from rich.text import Text

from gitseed_cli.client import CLIError, GitSeedClient
from gitseed_cli.config import RuntimeOptions, get_opts, make_client
from gitseed_cli.formatting import (
    console,
    dump_json,
    extract_log_tail_delta,
    print_agent_sessions_table,
    print_sessions_table,
)

sandbox_app = typer.Typer(
    name="sandbox",
    help="Manage interactive project sandbox sessions.",
    no_args_is_help=True,
)

session_app = typer.Typer(
    name="session",
    help="Manage interactive coding agent and terminal sessions.",
    no_args_is_help=True,
)


def _resolve_project_ref(client: GitSeedClient, opts: RuntimeOptions) -> str:
    return client.resolve_project_ref(opts.project)


@sandbox_app.command("start")
def sandbox_start(
    ctx: typer.Context,
    seed: str | None = typer.Option(
        None, "--seed", help="Optional seed reference (<project>#<number> or sed_...)."
    ),
    command: str | None = typer.Option(
        None, "-c", "--command", help="Command to run (defaults to project run_command)."
    ),
    setup_command: str | None = typer.Option(
        None, "--setup", help="Optional setup command override."
    ),
    image: str | None = typer.Option(
        None, "--image", help="Container image override (e.g. python:3.12-slim)."
    ),
    ref: str | None = typer.Option(None, "--ref", help="Git branch, tag, or commit SHA."),
    repo_id: int | None = typer.Option(None, "--repo-id", help="Optional repository ID."),
    stdin: str | None = typer.Option(
        None, "--stdin", help="Initial stdin payload to pass on startup."
    ),
    kind: str = typer.Option(
        "job", "--kind", help="Sandbox session kind (job or terminal)."
    ),
    title: str | None = typer.Option(
        None, "--title", help="Optional display title for the sandbox session."
    ),
    as_root: bool = typer.Option(
        False, "--as-root", help="Run the sandbox container as root."
    ),
) -> None:
    """Start a new interactive sandbox session for a project or seed."""
    opts = get_opts(ctx)
    client = make_client(ctx)

    seed_id: str | None = None
    proj_ref: str

    if seed:
        sd = client.get_seed(seed, project_override=opts.project)
        seed_id = sd.id
        proj_ref = sd.project_id
    else:
        proj_ref = _resolve_project_ref(client, opts)

    norm_kind = "terminal" if kind.strip().lower() == "terminal" else "job"
    resolved_cmd = command
    resolved_image = image
    resolved_setup = setup_command
    resolved_globs: list[str] = []

    if (norm_kind == "job" and not resolved_cmd) or not resolved_image:
        cfg = client.get_sandbox_config(proj_ref, repo_id=repo_id, ref=ref)
        if norm_kind == "job":
            resolved_cmd = resolved_cmd or cfg.run_command or cfg.test_command
        resolved_image = resolved_image or cfg.image
        if resolved_setup is None:
            resolved_setup = cfg.setup_command
        resolved_globs = list(cfg.artifact_globs)

    if norm_kind == "job" and not resolved_cmd:
        raise CLIError(
            "No sandbox run command configured for this project. Pass -c/--command <cmd>."
        )

    sbx = client.create_sandbox(
        proj_ref,
        CreateSandboxSessionRequest(
            kind="terminal" if norm_kind == "terminal" else "job",
            title=title,
            seed_id=seed_id,
            repository_id=repo_id,
            ref=ref,
            image=resolved_image or "python:3.12-slim",
            setup_command=resolved_setup,
            command=resolved_cmd or "",
            artifact_globs=resolved_globs,
            initial_stdin=stdin,
            as_root=as_root,
        ),
    )

    if opts.output == "json":
        dump_json(sbx)
        return

    console.print(
        f"[green]✓[/green] Started sandbox [bold]{escape(str(sbx.id))}[/bold] "
        f"(status: [bold]{escape(str(sbx.status))}[/bold], "
        f"ref: {escape(str(sbx.ref))}, image: {escape(str(sbx.image))})"
    )


@sandbox_app.command("input")
def sandbox_input(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox session ID (sbx_...)."),
    text: str = typer.Argument(..., help="Input text to send to sandbox stdin."),
    eof: bool = typer.Option(False, "--eof", help="Close stdin after sending input."),
) -> None:
    """Send standard input to a running sandbox session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)

    sbx = client.send_sandbox_input(
        proj_ref,
        sandbox_id,
        SandboxInputRequest(input=text, append_newline=True, eof=eof),
    )

    if opts.output == "json":
        dump_json(sbx)
        return

    console.print(
        f"[green]✓[/green] Sent input to sandbox [bold]{sbx.id}[/bold] (status: {sbx.status})."
    )


@sandbox_app.command("stop")
def sandbox_stop(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox session ID (sbx_...)."),
) -> None:
    """Stop a running sandbox session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)

    sbx = client.stop_sandbox(proj_ref, sandbox_id)
    if opts.output == "json":
        dump_json(sbx)
        return

    console.print(f"[green]✓[/green] Stopped sandbox [bold]{sbx.id}[/bold] (status: {sbx.status}).")


@sandbox_app.command("logs")
def sandbox_logs(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox session ID (sbx_...)."),
    follow: bool = typer.Option(
        False, "-f", "--follow", help="Follow sandbox output until stopped."
    ),
) -> None:
    """Print or follow output from a sandbox session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)

    sbx = client.get_sandbox(proj_ref, sandbox_id)
    if opts.output == "json" and not follow:
        dump_json(sbx)
        return

    printed_text = sbx.log_tail or ""
    if printed_text:
        console.print(printed_text, end="", highlight=False, markup=False)

    if not follow:
        if printed_text and not printed_text.endswith("\n"):
            console.print()
        return

    try:
        while sbx.status in SANDBOX_ACTIVE_STATUSES:
            time.sleep(1.0)
            sbx = client.get_sandbox(proj_ref, sandbox_id)
            updated_log = sbx.log_tail or ""
            delta = extract_log_tail_delta(printed_text, updated_log)
            if delta:
                console.print(delta, end="", highlight=False, markup=False)
            if updated_log:
                printed_text = updated_log
    except KeyboardInterrupt:
        console.print("\n[dim]Stopped following sandbox output.[/dim]")


@sandbox_app.command("config")
def sandbox_config(
    ctx: typer.Context,
    ref: str | None = typer.Option(None, "--ref", help="Git ref to inspect."),
    repo_id: int | None = typer.Option(None, "--repo-id", help="Optional repository ID."),
) -> None:
    """Inspect resolved sandbox run configuration for a project."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)

    cfg = client.get_sandbox_config(proj_ref, repo_id=repo_id, ref=ref)
    if opts.output == "json":
        dump_json(cfg)
        return

    console.print(f"[bold]Source:[/bold]        {escape(str(cfg.source))} (ref: {escape(str(cfg.ref))})")
    console.print(f"[bold]Image:[/bold]         {escape(str(cfg.image))}")
    console.print(f"[bold]Setup Command:[/bold] {escape(cfg.setup_command or '-')}")
    console.print(f"[bold]Run Command:[/bold]   {escape(cfg.run_command or '-')}")
    console.print(f"[bold]Test Command:[/bold]  {escape(cfg.test_command or '-')}")


@sandbox_app.command("delete")
def sandbox_delete(
    ctx: typer.Context,
    sandbox_id: str = typer.Argument(..., help="Sandbox session ID (sbx_...)."),
) -> None:
    """Delete a sandbox session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)
    client.delete_sandbox(proj_ref, sandbox_id)
    if opts.output == "json":
        dump_json({"deleted": sandbox_id})
        return
    console.print(f"[green]✓[/green] Deleted sandbox [bold]{sandbox_id}[/bold].")


@session_app.command("list")
def session_list(
    ctx: typer.Context,
    scope: str = typer.Option(
        "everyone", "--scope", help="Filter scope (everyone or mine)."
    ),
    state: str = typer.Option(
        "all", "--state", help="Filter by state (running, ended, all)."
    ),
    all_projects: bool = typer.Option(
        False, "-A", "--all-projects", help="List sessions across all projects."
    ),
) -> None:
    """List interactive agent and terminal sessions."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    effective_project = None if all_projects else (opts.project or client.default_project)
    sessions = client.list_sessions(
        scope=scope,
        state=state,
        project_id=effective_project,
    )
    if opts.output == "json":
        dump_json(sessions)
        return
    print_sessions_table(sessions, wide=(opts.output == "wide"))


@session_app.command("start")
def session_start(
    ctx: typer.Context,
    prompt: str | None = typer.Option(
        None, "-m", "--prompt", help="Optional initial message to send to the coding agent."
    ),
    agent: str | None = typer.Option(
        None, "-a", "--agent", help="Agent @handle or ID to use for the session."
    ),
    seed: str | None = typer.Option(
        None, "-s", "--seed", help="Optional seed reference (<project>#<number> or sed_...)."
    ),
    title: str | None = typer.Option(None, "--title", help="Optional session title."),
    runtime: AgentRuntime | None = typer.Option(
        None, "--runtime", help="Optional agent runtime override."
    ),
    model: str | None = typer.Option(None, "--model", help="Optional model override."),
    ref: str | None = typer.Option(None, "--ref", help="Optional Git branch or ref."),
    repo_id: int | None = typer.Option(None, "--repo-id", help="Optional repository ID."),
    inference_backend_id: str | None = typer.Option(
        None, "--inference-backend", help="Optional inference backend ID."
    ),
) -> None:
    """Start a new interactive coding agent session."""
    opts = get_opts(ctx)
    client = make_client(ctx)

    seed_id: str | None = None
    proj_ref: str
    if seed:
        sd = client.get_seed(seed, project_override=opts.project)
        seed_id = sd.id
        proj_ref = sd.project_id
    else:
        proj_ref = _resolve_project_ref(client, opts)

    agent_id = client.resolve_agent_id(agent) if agent else None
    sess = client.create_agent_session(
        proj_ref,
        CreateAgentSessionRequest(
            title=title,
            seed_id=seed_id,
            agent_id=agent_id,
            runtime=runtime,
            model=model,
            inference_backend_id=inference_backend_id,
            repository_id=repo_id,
            ref=ref,
            initial_prompt=prompt,
        ),
    )
    if opts.output == "json":
        dump_json(sess)
        return
    console.print(
        f"[green]✓[/green] Started agent session [bold]{escape(str(sess.id))}[/bold] "
        f"(status: [bold]{escape(str(sess.status))}[/bold], "
        f"agent: @{escape(str(sess.agent_handle or sess.agent_id or 'default'))}, "
        f"model: {escape(str(sess.model))})"
    )


@session_app.command("send")
def session_send(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Agent session ID (ses_... or sbx_...)."),
    message: str = typer.Argument(..., help="Follow-up message for the coding agent."),
) -> None:
    """Send a follow-up prompt to an interactive coding agent session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)
    sess = client.send_agent_session_message(
        proj_ref,
        session_id,
        SendAgentSessionMessageRequest(content=message),
    )
    if opts.output == "json":
        dump_json(sess)
        return
    console.print(
        f"[green]✓[/green] Sent message to session [bold]{sess.id}[/bold] (status: {sess.status})."
    )


@session_app.command("show")
def session_show(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Agent session ID (ses_... or sbx_...)."),
) -> None:
    """Display details and conversation messages of an agent session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = opts.project or client.default_project
    if not proj_ref:
        item = client.get_session_item("chat", session_id)
        proj_ref = item.project_id
    sess = client.get_agent_session(proj_ref, session_id)
    if opts.output == "json":
        dump_json(sess)
        return
    print_agent_sessions_table([sess], wide=True)
    for msg in sess.messages:
        role_label = escape(msg.role.upper())
        console.print(
            Panel(Text(msg.content or ""), title=f"{role_label} ({escape(msg.id)})", expand=False)
        )


@session_app.command("stop")
def session_stop(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Agent session ID (ses_... or sbx_...)."),
) -> None:
    """Stop a running coding agent session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)
    sess = client.stop_agent_session(proj_ref, session_id)
    if opts.output == "json":
        dump_json(sess)
        return
    console.print(
        f"[green]✓[/green] Stopped agent session [bold]{sess.id}[/bold] (status: {sess.status})."
    )


@session_app.command("delete")
def session_delete(
    ctx: typer.Context,
    session_id: str = typer.Argument(..., help="Agent session ID (ses_... or sbx_...)."),
) -> None:
    """Delete an interactive coding agent session."""
    opts = get_opts(ctx)
    client = make_client(ctx)
    proj_ref = _resolve_project_ref(client, opts)
    client.delete_agent_session(proj_ref, session_id)
    if opts.output == "json":
        dump_json({"deleted": session_id})
        return
    console.print(f"[green]✓[/green] Deleted agent session [bold]{session_id}[/bold].")
