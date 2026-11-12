from __future__ import annotations

import typer
from rich.markup import escape
from rich.panel import Panel
from rich.table import Table

from gitseed_cli.client import GitSeedClient
from gitseed_cli.config import (
    ContextConfig,
    default_token_name,
    delete_token,
    get_opts,
    load_config,
    load_token,
    resolve_connection,
    save_config,
    store_token,
)
from gitseed_cli.formatting import console, dump_json

config_app = typer.Typer(
    name="config",
    help="Manage GitSeed server contexts and default project.",
    no_args_is_help=True,
)


def register_auth_commands(app: typer.Typer) -> None:
    app.add_typer(config_app, name="config")

    @app.command("login")
    def login_cmd(
        ctx: typer.Context,
        url: str | None = typer.Argument(
            default=None, help="GitSeed Control Plane URL (e.g. http://localhost:8420)."
        ),
        email: str | None = typer.Option(None, "--email", "-e", help="Account email address."),
        password: str | None = typer.Option(None, "--password", help="Account password."),
        token: str | None = typer.Option(
            None, "--token", "-t", help="Existing personal access token (gsp_...)."
        ),
        token_name: str | None = typer.Option(
            None,
            "--name",
            help="Name for the minted token (defaults to 'CLI on <hostname>').",
        ),
        context_name: str | None = typer.Option(
            None, "--context", help="Context name to save credentials under."
        ),
    ) -> None:
        """Authenticate with a GitSeed Control Plane and store a token securely."""
        opts = get_opts(ctx)
        cfg = load_config()
        target_ctx = context_name or opts.context or cfg.current_context
        existing_ctx = cfg.contexts.get(target_ctx, ContextConfig())
        resolved_url = (url or opts.api_url or existing_ctx.api_url).rstrip("/")

        effective_token = token or opts.token
        if effective_token:
            client = GitSeedClient(resolved_url, token=effective_token.strip())
            user = client.get_me()
            backend_used = store_token(target_ctx, effective_token.strip())
        else:
            prompt_email = email or typer.prompt("Email")
            prompt_password = password or typer.prompt("Password", hide_input=True)
            pat_label = (token_name or default_token_name()).strip()
            anon_client = GitSeedClient(resolved_url)
            user, minted = anon_client.login_and_mint_token(
                email=prompt_email,
                password=prompt_password,
                token_name=pat_label,
            )
            backend_used = store_token(target_ctx, minted.token)

        existing_ctx.api_url = resolved_url
        if opts.project:
            existing_ctx.project = opts.project
        cfg.contexts[target_ctx] = existing_ctx
        cfg.current_context = target_ctx
        save_config(cfg)

        console.print(
            f"[green]✓[/green] Logged in as [bold]{escape(str(user.email))}[/bold] "
            f"({escape(str(user.org_role))}) on [bold]{escape(str(resolved_url))}[/bold] "
            f"[dim](context '{escape(str(target_ctx))}', "
            f"credentials stored in {escape(str(backend_used))})[/dim]"
        )

    @app.command("logout")
    def logout_cmd(
        ctx: typer.Context,
        context_name: str | None = typer.Option(
            None, "--context", help="Context to remove credentials from."
        ),
    ) -> None:
        """Remove stored credentials for the active or specified context."""
        opts = get_opts(ctx)
        cfg = load_config()
        target_ctx = context_name or opts.context or cfg.current_context
        delete_token(target_ctx)
        console.print(
            f"[green]✓[/green] Removed stored credentials for context "
            f"[bold]{escape(str(target_ctx))}[/bold]."
        )

    @app.command("whoami")
    def whoami_cmd(ctx: typer.Context) -> None:
        """Display the currently authenticated user and active context."""
        opts = get_opts(ctx)
        ctx_name, api_url, token, project = resolve_connection(opts)
        client = GitSeedClient(api_url, token=token, default_project=project)
        user = client.get_me()
        if opts.output == "json":
            dump_json(
                {
                    "user": user.model_dump(mode="json"),
                    "context": ctx_name,
                    "api_url": api_url,
                    "project": project,
                }
            )
            return

        lines = [
            (
                f"[bold]User:[/bold]      {escape(str(user.name))} "
                f"<{escape(str(user.email))}> ({escape(str(user.id))})"
            ),
            f"[bold]Org Role:[/bold]  {escape(str(user.org_role))}",
            f"[bold]Context:[/bold]   {escape(str(ctx_name))}",
            f"[bold]Server:[/bold]    {escape(str(api_url))}",
            f"[bold]Project:[/bold]   {escape(str(project or '(none)'))}",
        ]
        console.print(Panel("\n".join(lines), title="GitSeed Identity", expand=False))

    @app.command("status")
    def status_cmd(ctx: typer.Context) -> None:
        """Check Control Plane health, version, active runs, and pending approvals."""
        opts = get_opts(ctx)
        ctx_name, api_url, token, project = resolve_connection(opts)
        client = GitSeedClient(api_url, token=token, default_project=project)
        health = client.get_health()
        version = client.get_version()

        active_runs_count: int | None = None
        awaiting_count: int | None = None
        if token:
            try:
                active_runs = client.list_runs(project_id=project, active_only=True)
                awaiting = client.list_seeds(project_id=project, status="awaiting_approval")
                active_runs_count = len(active_runs)
                awaiting_count = len(awaiting)
            except Exception:
                pass

        if opts.output == "json":
            dump_json(
                {
                    "context": ctx_name,
                    "api_url": api_url,
                    "project": project,
                    "health": health.model_dump(mode="json"),
                    "version": version.model_dump(mode="json"),
                    "active_runs": active_runs_count,
                    "awaiting_approval": awaiting_count,
                }
            )
            return

        lines = [
            f"[bold]Server:[/bold]             {escape(str(api_url))} (v{escape(str(version.version))})",
            (
                f"[bold]Status:[/bold]             {escape(str(health.status))} "
                f"(db={escape(str(health.database))}, redis={escape(str(health.redis))})"
            ),
            (
                f"[bold]Context:[/bold]            {escape(str(ctx_name))} "
                f"(project={escape(str(project or 'all'))})"
            ),
        ]
        if active_runs_count is not None:
            lines.append(f"[bold]Active Runs:[/bold]        {active_runs_count}")
        if awaiting_count is not None:
            lines.append(f"[bold]Awaiting Approval:[/bold]  {awaiting_count}")
        console.print(Panel("\n".join(lines), title="GitSeed Status", expand=False))


@config_app.command("get-contexts")
def config_get_contexts(ctx: typer.Context) -> None:
    """List configured contexts without exposing any tokens."""
    opts = get_opts(ctx)
    cfg = load_config()
    if opts.output == "json":
        rows = [
            {
                "name": name,
                "current": name == cfg.current_context,
                "api_url": c.api_url,
                "project": c.project,
                "authenticated": bool(load_token(name)),
            }
            for name, c in cfg.contexts.items()
        ]
        dump_json(rows)
        return

    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("CURRENT")
    table.add_column("NAME", style="bold")
    table.add_column("SERVER")
    table.add_column("PROJECT")
    table.add_column("AUTH")
    for name, c in cfg.contexts.items():
        has_auth = "authenticated" if load_token(name) else "none"
        table.add_row(
            "*" if name == cfg.current_context else "",
            escape(str(name)),
            escape(str(c.api_url)),
            escape(str(c.project or "-")),
            has_auth,
        )
    console.print(table)


@config_app.command("use-context")
def config_use_context(
    ctx: typer.Context,
    name: str = typer.Argument(..., help="Context name to activate."),
    api_url: str | None = typer.Option(
        None, "--api-url", help="Create or update the context server URL."
    ),
) -> None:
    """Switch the active context (creating it if --api-url is provided)."""
    opts = get_opts(ctx)
    effective_url = api_url or opts.api_url
    cfg = load_config()
    if name not in cfg.contexts:
        if not effective_url:
            raise typer.BadParameter(
                f"Context '{name}' does not exist. Pass --api-url to create it."
            )
        cfg.contexts[name] = ContextConfig(api_url=effective_url.rstrip("/"))
    elif effective_url:
        cfg.contexts[name].api_url = effective_url.rstrip("/")

    cfg.current_context = name
    save_config(cfg)
    console.print(f"[green]✓[/green] Switched to context [bold]{escape(str(name))}[/bold].")


@config_app.command("use-project")
def config_use_project(
    ctx: typer.Context,
    project: str = typer.Argument(
        ..., help="Default project slug for the active context (pass '-' to clear)."
    ),
) -> None:
    """Set the default project for the current context."""
    opts = get_opts(ctx)
    cfg = load_config()
    target_ctx = opts.context or cfg.current_context
    ctx_cfg = cfg.contexts.get(target_ctx, ContextConfig())
    cleaned = None if project.strip() in {"-", "none", ""} else project.strip()
    ctx_cfg.project = cleaned
    cfg.contexts[target_ctx] = ctx_cfg
    save_config(cfg)
    if cleaned:
        console.print(
            f"[green]✓[/green] Default project for context [bold]{escape(str(target_ctx))}[/bold] "
            f"set to [bold]{escape(str(cleaned))}[/bold]."
        )
    else:
        console.print(
            f"[green]✓[/green] Cleared default project for context "
            f"[bold]{escape(str(target_ctx))}[/bold]."
        )
