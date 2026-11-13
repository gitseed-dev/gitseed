from __future__ import annotations

import re
from decimal import Decimal

import typer
from gitseed_common.enums import ApprovalDecision, SeedStage, SeedStatus
from gitseed_common.schemas import (
    ApproveCheckpointRequest,
    CreateApprovalRequest,
    CreateRunRequest,
    CreateSeedCommentRequest,
)
from rich.markdown import Markdown
from rich.panel import Panel

from gitseed_cli.client import CLIError, GitSeedClient
from gitseed_cli.config import get_opts, make_client
from gitseed_cli.formatting import console, dump_json, escape, sanitize_terminal

_MENTION_RE = re.compile(r"(?:^|(?<=\s))@([a-z][a-z0-9-]{1,31})\b", re.IGNORECASE)


def _resolve_stage_agent_handle(client: GitSeedClient, project_id: str, stage: str) -> str:
    try:
        policies = client.list_project_policies(project_id)
        pol = next((p for p in policies if str(p.stage) == str(stage)), None)
        if pol and pol.agent_id:
            agent = client.get_agent(pol.agent_id)
            return agent.handle
    except Exception:
        pass
    stage_defaults = {
        "ideate": "ideator",
        "plan": "planner",
        "execute": "coder",
        "review": "reviewer",
        "merge": "merger",
    }
    return stage_defaults.get(str(stage), "agent")


def register_workflow_commands(app: typer.Typer) -> None:
    @app.command("approve")
    def approve_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        message: str | None = typer.Option(
            None, "-m", "--message", help="Optional approval comment."
        ),
        checkpoint: float | None = typer.Option(
            None,
            "--checkpoint",
            help="Raise spend checkpoint approved_through amount (USD) instead of a stage gate.",
        ),
    ) -> None:
        """Approve a seed's current stage gate (or raise its spend checkpoint)."""
        opts = get_opts(ctx)
        client = make_client(ctx)

        if checkpoint is not None:
            sd = client.approve_checkpoint(
                seed_ref,
                ApproveCheckpointRequest(approved_through=Decimal(str(checkpoint))),
                project_override=opts.project,
            )
            if opts.output == "json":
                dump_json(sd)
            else:
                amt = float(sd.checkpoint_approved_through)
                console.print(
                    f"[green]✓[/green] Approved spend checkpoint for seed "
                    f"[bold]#{sd.number}[/bold] through [bold]${amt:.2f}[/bold] "
                    f"(status: {escape(str(sd.status))})."
                )
            return

        approval = client.create_seed_approval(
            seed_ref,
            CreateApprovalRequest(
                decision=ApprovalDecision.APPROVED,
                comment=message.strip() if message and message.strip() else None,
            ),
            project_override=opts.project,
        )
        updated_seed = client.get_seed(approval.seed_id)
        if opts.output == "json":
            dump_json(
                {
                    "approval": approval.model_dump(mode="json"),
                    "seed": updated_seed.model_dump(mode="json"),
                }
            )
            return
        console.print(
            f"[green]✓[/green] Approved stage [bold]{escape(str(approval.stage))}[/bold] on seed "
            f"[bold]#{updated_seed.number}[/bold] -> "
            f"stage: [bold]{escape(str(updated_seed.stage))}[/bold], "
            f"status: [bold]{escape(str(updated_seed.status))}[/bold]."
        )

    @app.command("request-changes")
    def request_changes_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        message: str = typer.Option(
            ..., "-m", "--message", help="Feedback explaining the requested changes."
        ),
    ) -> None:
        """Request changes on a seed's current stage gate."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        approval = client.create_seed_approval(
            seed_ref,
            CreateApprovalRequest(
                decision=ApprovalDecision.CHANGES_REQUESTED,
                comment=message.strip(),
            ),
            project_override=opts.project,
        )
        updated_seed = client.get_seed(approval.seed_id)
        if opts.output == "json":
            dump_json(
                {
                    "approval": approval.model_dump(mode="json"),
                    "seed": updated_seed.model_dump(mode="json"),
                }
            )
            return
        console.print(
            f"[yellow]✓[/yellow] Requested changes on stage "
            f"[bold]{escape(str(approval.stage))}[/bold] "
            f"for seed [bold]#{updated_seed.number}[/bold] -> "
            f"stage: [bold]{escape(str(updated_seed.stage))}[/bold], "
            f"status: [bold]{escape(str(updated_seed.status))}[/bold]."
        )

    @app.command("reject")
    def reject_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        message: str = typer.Option(..., "-m", "--message", help="Reason for rejecting the seed."),
    ) -> None:
        """Reject a seed's stage gate and close the seed."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        approval = client.create_seed_approval(
            seed_ref,
            CreateApprovalRequest(
                decision=ApprovalDecision.REJECTED,
                comment=message.strip(),
            ),
            project_override=opts.project,
        )
        updated_seed = client.get_seed(approval.seed_id)
        if opts.output == "json":
            dump_json(
                {
                    "approval": approval.model_dump(mode="json"),
                    "seed": updated_seed.model_dump(mode="json"),
                }
            )
            return
        console.print(
            f"[red]✓[/red] Rejected seed [bold]#{updated_seed.number}[/bold] "
            f"at stage [bold]{escape(str(approval.stage))}[/bold] "
            f"(status: [bold]{escape(str(updated_seed.status))}[/bold])."
        )

    @app.command("comment")
    def comment_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        message: str = typer.Argument(..., help="Discussion comment text."),
    ) -> None:
        """Post a plain discussion comment on a seed."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        comment = client.create_seed_comment(
            seed_ref,
            CreateSeedCommentRequest(body=message.strip()),
            project_override=opts.project,
        )
        if opts.output == "json":
            dump_json(comment)
            return
        console.print(
            f"[green]✓[/green] Posted comment ({escape(str(comment.id))}) "
            f"on seed {escape(str(comment.seed_id))}."
        )

    @app.command("steer")
    def steer_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        message: str = typer.Argument(..., help="Steering instruction for the stage agent."),
        agent: str | None = typer.Option(
            None,
            "--agent",
            "-a",
            help="Explicit @handle to mention (defaults to active stage agent).",
        ),
    ) -> None:
        """Steer a seed's stage agent with an @mention comment."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.get_seed(seed_ref, project_override=opts.project)

        if agent:
            handle = agent.strip().lstrip("@")
        else:
            handle = _resolve_stage_agent_handle(client, seed.project_id, str(seed.stage))

        cleaned_msg = message.strip()
        existing_mentions = {m.group(1).lower() for m in _MENTION_RE.finditer(cleaned_msg)}
        if handle.lower() not in existing_mentions:
            body = f"@{handle} {cleaned_msg}"
        else:
            body = cleaned_msg

        comment = client.create_seed_comment(
            seed.id,
            CreateSeedCommentRequest(body=body),
        )
        if opts.output == "json":
            dump_json(comment)
            return
        console.print(
            f"[green]✓[/green] Sent steering directive to [bold]@{escape(handle)}[/bold] "
            f"on seed [bold]#{seed.number}[/bold] ({escape(str(comment.id))})."
        )

    @app.command("retry")
    def retry_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
    ) -> None:
        """Retry a seed that has failed, is blocked, or was stopped."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.get_seed(seed_ref, project_override=opts.project)

        if seed.status not in {SeedStatus.FAILED, SeedStatus.BLOCKED, SeedStatus.STOPPED}:
            raise CLIError(
                f"Seed #{seed.number} ({seed.id}) has status '{seed.status}'. "
                "Only 'failed' or 'blocked' (or 'stopped') seeds can be retried; "
                "normal stage runs are started by the scheduler."
            )

        run = client.create_run(CreateRunRequest(seed_id=seed.id))
        if opts.output == "json":
            dump_json(run)
            return
        console.print(
            f"[green]✓[/green] Queued retry run [bold]{escape(str(run.id))}[/bold] "
            f"(attempt #{run.attempt}) for seed [bold]#{seed.number}[/bold] "
            f"in stage [bold]{escape(str(run.stage))}[/bold]."
        )

    @app.command("start")
    def start_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
    ) -> None:
        """Start a staged seed so its stage agent begins running."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.start_seed(seed_ref, project_override=opts.project)
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[green]✓[/green] Started seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))}) "
            f"in stage [bold]{escape(str(seed.stage))}[/bold] "
            f"(status: [bold]{escape(str(seed.status))}[/bold])."
        )

    @app.command("advance")
    def advance_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        stage: SeedStage | None = typer.Option(
            None, "--stage", help="Optional target stage to advance to."
        ),
        fast_track: bool = typer.Option(
            False,
            "--fast-track",
            help="Skip Ideate and Plan and fast-track directly to Execute.",
        ),
    ) -> None:
        """Manually advance a seed to the next SDLC stage (or --fast-track to Execute)."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.advance_seed(
            seed_ref,
            stage=(
                SeedStage.EXECUTE.value
                if fast_track and stage is None
                else (stage.value if stage is not None else None)
            ),
            fast_track=fast_track,
            project_override=opts.project,
        )
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[green]✓[/green] Advanced seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))}) "
            f"to stage [bold]{escape(str(seed.stage))}[/bold] "
            f"(status: [bold]{escape(str(seed.status))}[/bold])."
        )

    @app.command("move-back")
    def move_back_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        stage: SeedStage = typer.Option(
            ..., "--stage", help="Earlier SDLC stage to return the seed to."
        ),
        message: str = typer.Option(
            "Moved back via CLI",
            "-m",
            "--message",
            help="Reason or instructions for moving back.",
        ),
    ) -> None:
        """Move a seed back to an earlier SDLC stage."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.move_seed_back(
            seed_ref,
            stage=stage.value,
            reason=message.strip() or "Moved back via CLI",
            project_override=opts.project,
        )
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[yellow]✓[/yellow] Moved seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))}) "
            f"back to stage [bold]{escape(str(seed.stage))}[/bold] "
            f"(status: [bold]{escape(str(seed.status))}[/bold])."
        )

    @app.command("stop")
    def stop_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
    ) -> None:
        """Stop an active seed and cancel its running agent execution."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.stop_seed(seed_ref, project_override=opts.project)
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[yellow]✓[/yellow] Stopped seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))}) "
            f"in stage [bold]{escape(str(seed.stage))}[/bold] "
            f"(status: [bold]{escape(str(seed.status))}[/bold])."
        )

    @app.command("resume")
    def resume_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
    ) -> None:
        """Resume a stopped seed and restart its stage agent execution."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.resume_seed(seed_ref, project_override=opts.project)
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[green]✓[/green] Resumed seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))}) "
            f"in stage [bold]{escape(str(seed.stage))}[/bold] "
            f"(status: [bold]{escape(str(seed.status))}[/bold])."
        )

    @app.command("cancel")
    def cancel_cmd(
        ctx: typer.Context,
        target: str = typer.Argument(
            ..., help="Run ID (run_...) or seed reference (<project>#<number>)."
        ),
    ) -> None:
        """Cancel an active agent run."""
        opts = get_opts(ctx)
        client = make_client(ctx)

        if target.strip().startswith("run_"):
            run_id = target.strip()
        else:
            seed = client.get_seed(target, project_override=opts.project)
            active_runs = client.list_runs(seed_id=seed.id, active_only=True, limit=1)
            if not active_runs:
                raise CLIError(f"No active run found for seed #{seed.number} ({seed.id}).")
            run_id = active_runs[0].id

        cancelled = client.cancel_run(run_id)
        if opts.output == "json":
            dump_json(cancelled)
            return
        console.print(
            f"[green]✓[/green] Cancelled run [bold]{escape(str(cancelled.id))}[/bold] "
            f"(status: {escape(str(cancelled.status))})."
        )

    @app.command("close")
    def close_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
    ) -> None:
        """Close an open seed without merging."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.close_seed(seed_ref, project_override=opts.project)
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[green]✓[/green] Closed seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))})."
        )

    @app.command("reopen")
    def reopen_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
    ) -> None:
        """Reopen a closed seed (requires project maintainer privileges)."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        seed = client.reopen_seed(seed_ref, project_override=opts.project)
        if opts.output == "json":
            dump_json(seed)
            return
        console.print(
            f"[green]✓[/green] Reopened seed [bold]#{seed.number}[/bold] ({escape(str(seed.id))}) "
            f"in stage [bold]{escape(str(seed.stage))}[/bold] "
            f"(status: {escape(str(seed.status))})."
        )

    @app.command("ask")
    def ask_cmd(
        ctx: typer.Context,
        prompt: str = typer.Argument(..., help="Question or request for @coordinator."),
        seed: str | None = typer.Option(
            None, "-s", "--seed", help="Optional seed reference for context."
        ),
        mode: str = typer.Option(
            "general",
            "--mode",
            help="Coordinator chat mode: general | brainstorm.",
        ),
        propose_seeds: bool = typer.Option(
            False,
            "--propose-seeds",
            help="Ask @coordinator to propose structured seeds.",
        ),
    ) -> None:
        """Ask @coordinator a question with live project and seed context."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        proj_id: str | None = None
        if opts.project or client.default_project:
            proj = client.get_project(opts.project or client.default_project or "")
            proj_id = proj.id
        seed_id: str | None = None
        if seed:
            sd = client.get_seed(seed, project_override=opts.project)
            seed_id = sd.id
            if proj_id is None:
                proj_id = sd.project_id

        resp = client.chat_coordinator(
            prompt,
            project_id=proj_id,
            seed_id=seed_id,
            mode=mode,
            propose_seeds=propose_seeds,
        )
        if opts.output == "json":
            dump_json(resp)
            return
        console.print(
            Panel(
                Markdown(sanitize_terminal(resp.reply)),
                title=f"@{escape(str(resp.agent_handle))} ({escape(str(resp.model))})",
                expand=False,
            )
        )
        if resp.referenced_files:
            escaped_refs = ", ".join(escape(str(rf)) for rf in resp.referenced_files)
            console.print(f"[dim]Referenced files:[/dim] {escaped_refs}")
        if resp.proposed_seeds:
            console.print("\n[bold cyan]Proposed Seeds:[/bold cyan]")
            for idx, prop in enumerate(resp.proposed_seeds, start=1):
                console.print(
                    f"  [bold]{idx}. {escape(str(prop.title))}[/bold] "
                    f"(autonomy: {escape(str(prop.autonomy))})"
                )
                if prop.description:
                    console.print(f"     {escape(str(prop.description))}")
