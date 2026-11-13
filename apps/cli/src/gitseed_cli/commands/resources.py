from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
from decimal import Decimal
from pathlib import Path

import click
import typer
from gitseed_common.enums import RUN_ACTIVE_STATUSES, SeedAutonomy, SeedStage
from gitseed_common.schemas import CreateSeedRequest, RunResponse
from rich.panel import Panel

from gitseed_cli.client import CLIError, GitSeedClient
from gitseed_cli.config import get_opts, make_client
from gitseed_cli.formatting import (
    console,
    describe_seed,
    dump_json,
    err_console,
    escape,
    extract_log_tail_delta,
    print_agents_table,
    print_backends_table,
    print_benchmarks_table,
    print_events_table,
    print_policies_table,
    print_projects_table,
    print_runs_table,
    print_sandboxes_table,
    print_seeds_table,
    print_sessions_table,
    print_skills_table,
    print_tasks_table,
    print_tokens_table,
    print_users_table,
    render_seed_diff,
    sanitize_terminal,
)
from gitseed_cli.manifest import (
    ParsedManifest,
    agent_to_markdown,
    build_agent_requests,
    build_create_seed_request,
    build_policy_request,
    build_project_requests,
    build_skill_requests,
    build_update_seed_request,
    parse_markdown_manifest,
    policy_to_markdown,
    project_to_markdown,
    seed_to_markdown,
    skill_to_markdown,
)

RESOURCE_ALIASES: dict[str, str] = {
    "project": "project",
    "projects": "project",
    "proj": "project",
    "seed": "seed",
    "seeds": "seed",
    "task": "task",
    "tasks": "task",
    "run": "run",
    "runs": "run",
    "agent": "agent",
    "agents": "agent",
    "benchmark": "benchmark",
    "benchmarks": "benchmark",
    "skill": "skill",
    "skills": "skill",
    "policy": "policy",
    "policies": "policy",
    "pol": "policy",
    "backend": "backend",
    "backends": "backend",
    "sandbox": "sandbox",
    "sandboxes": "sandbox",
    "sbx": "sandbox",
    "session": "session",
    "sessions": "session",
    "user": "user",
    "users": "user",
    "token": "token",
    "tokens": "token",
}

create_app = typer.Typer(
    name="create",
    help="Create a new seed from flags or a Markdown manifest.",
    no_args_is_help=True,
)


def _split_editor_command(editor: str, *, is_windows: bool | None = None) -> list[str]:
    win = (os.name == "nt") if is_windows is None else is_windows
    parts = shlex.split(editor, posix=not win)
    if win:
        parts = [
            tok[1:-1] if len(tok) >= 2 and tok[0] == '"' and tok[-1] == '"' else tok
            for tok in parts
        ]
    return parts


def normalize_resource(raw: str) -> str:
    key = raw.strip().lower()
    if key not in RESOURCE_ALIASES:
        valid = (
            "projects (proj), seeds, tasks, runs, agents, benchmarks (bench), skills, "
            "policies (pol), backends, sandboxes (sbx), sessions, users, tokens"
        )
        raise CLIError(f"Unknown resource type '{raw}'. Supported resources: {valid}.")
    return RESOURCE_ALIASES[key]


def _agent_handle_map(client: GitSeedClient) -> dict[str, str]:
    try:
        agents = client.list_agents(include_archived=True)
        return {a.id: f"@{a.handle}" for a in agents}
    except Exception:
        return {}


def _project_slug_map(client: GitSeedClient) -> dict[str, str]:
    try:
        projects = client.list_projects(include_archived=True)
        return {p.id: p.slug for p in projects}
    except Exception:
        return {}


def _export_resource_markdown(
    client: GitSeedClient,
    res_type: str,
    target_id: str,
    *,
    project_override: str | None = None,
) -> str:
    if res_type == "seed":
        seed = client.get_seed(target_id, project_override=project_override)
        slug_map = _project_slug_map(client)
        tasks = client.list_seed_tasks(seed.id)
        return seed_to_markdown(
            seed,
            project_slug=slug_map.get(seed.project_id),
            tasks=tasks,
        )
    if res_type == "agent":
        agent = client.get_agent(target_id)
        return agent_to_markdown(agent)
    if res_type == "skill":
        skill = client.get_skill(target_id)
        return skill_to_markdown(skill)
    if res_type == "project":
        project = client.get_project(target_id)
        return project_to_markdown(project)
    if res_type == "policy":
        proj_ref = project_override or client.default_project
        policies = (
            client.list_project_policies(proj_ref) if proj_ref else client.list_org_policies()
        )
        matched = next(
            (p for p in policies if str(p.stage) == target_id.strip().lower()),
            None,
        )
        if matched is None:
            raise CLIError(f"Policy for stage '{target_id}' not found.")
        handles = _agent_handle_map(client)
        return policy_to_markdown(
            matched,
            project_slug=proj_ref,
            agent_handle=handles.get(matched.agent_id or ""),
        )
    raise CLIError(
        "Markdown output (-o md) is supported for seed, agent, skill, "
        f"project, and policy (got '{res_type}')."
    )


def apply_parsed_manifest(
    client: GitSeedClient,
    manifest: ParsedManifest,
    *,
    source: str,
    project_override: str | None = None,
) -> str:
    """Apply a single ParsedManifest against the API and return a status summary line."""
    if manifest.ignored_readonly_sections:
        sections_str = ", ".join(manifest.ignored_readonly_sections)
        err_console.print(
            f"[yellow]Note:[/yellow] {escape(str(source))}: ignored read-only "
            f"workflow sections ({escape(sections_str)})."
        )

    fm = manifest.frontmatter
    kind = manifest.kind

    if kind == "Seed":
        seed_ref = (
            fm.get("id")
            or fm.get("seed")
            or (
                f"{fm.get('project') or project_override or client.default_project}#{fm['number']}"
                if fm.get("number") is not None
                and (fm.get("project") or project_override or client.default_project)
                else None
            )
        )
        if not seed_ref:
            raise CLIError(
                f"{source}: 'gitseed apply' updates existing seeds "
                "(requires 'id' or 'seed: <project>#<number>' "
                "in frontmatter). To create a new seed from a file, run: gitseed create seed -f "
                f"{source}"
            )
        existing = client.get_seed(str(seed_ref), project_override=project_override)
        update_req = build_update_seed_request(manifest, existing_seed=existing, source=source)
        updated = client.update_seed(existing.id, update_req)
        return f"seed/{updated.id} (#{updated.number} '{updated.title}') configured"

    if kind == "Agent":
        create_ag, update_ag = build_agent_requests(manifest, source=source)
        lookup = str(fm.get("id") or fm.get("handle") or "").strip().lstrip("@")
        if lookup:
            try:
                existing_agent = client.get_agent(lookup)
                updated_agent = client.update_agent(existing_agent.id, update_ag)
                return f"agent/@{updated_agent.handle} ({updated_agent.id}) configured"
            except CLIError as exc:
                if exc.status_code != 404:
                    raise
        created_agent = client.create_agent(create_ag)
        return f"agent/@{created_agent.handle} ({created_agent.id}) created"

    if kind == "Skill":
        create_sk, update_sk = build_skill_requests(manifest, source=source)
        lookup = str(fm.get("id") or fm.get("slug") or "").strip()
        if lookup:
            try:
                existing_skill = client.get_skill(lookup)
                updated_skill = client.update_skill(existing_skill.id, update_sk)
                return f"skill/{updated_skill.slug} ({updated_skill.id}) configured"
            except CLIError as exc:
                if exc.status_code != 404:
                    raise
        created_skill = client.create_skill(create_sk)
        return f"skill/{created_skill.slug} ({created_skill.id}) created"

    if kind == "Project":
        create_pr, update_pr = build_project_requests(manifest, source=source)
        lookup = str(fm.get("id") or fm.get("slug") or "").strip()
        if lookup:
            try:
                existing_proj = client.get_project(lookup)
                updated_proj = client.update_project(existing_proj.id, update_pr)
                return f"project/{updated_proj.slug} ({updated_proj.id}) configured"
            except CLIError as exc:
                if exc.status_code != 404:
                    raise
        created_proj = client.create_project(create_pr)
        return f"project/{created_proj.slug} ({created_proj.id}) created"

    if kind == "Policy":
        raw_agent = fm.get("agent") or fm.get("agent_id")
        resolved_agent_id = client.resolve_agent_id(str(raw_agent)) if raw_agent else None
        stage, manifest_proj, org_req, proj_req = build_policy_request(
            manifest, resolved_agent_id=resolved_agent_id, source=source
        )
        target_proj = manifest_proj or project_override
        if target_proj:
            pol = client.update_project_policy(target_proj, stage.value, proj_req)
            return f"policy/{target_proj}:{pol.stage} configured"
        pol = client.update_org_policy(stage.value, org_req)
        return f"policy/org:{pol.stage} configured"

    raise CLIError(f"{source}: Unsupported manifest kind '{kind}'.")


def register_resource_commands(app: typer.Typer) -> None:
    app.add_typer(create_app, name="create")

    @app.command("get")
    def get_cmd(
        ctx: typer.Context,
        resource: str = typer.Argument(
            ...,
            help=(
                "Resource type (projects, seeds, tasks, runs, agents, benchmarks, "
                "skills, policies, backends, sandboxes, sessions, users, tokens)."
            ),
        ),
        resource_id: str | None = typer.Argument(
            default=None, help="Optional resource ID, slug, @handle, or project#number."
        ),
        seed: str | None = typer.Option(
            None, "--seed", "-s", help="Filter tasks or runs by seed reference."
        ),
        stage: str | None = typer.Option(None, "--stage", help="Filter seeds or runs by stage."),
        status: str | None = typer.Option(
            None, "--status", help="Filter seeds, runs, or users by status."
        ),
        include_archived: bool = typer.Option(
            False, "--include-archived", help="Include archived projects, agents, or skills."
        ),
        all_projects: bool = typer.Option(
            False, "-A", "--all-projects", help="List resources across all projects."
        ),
    ) -> None:
        """Display one or many resources (-o table|wide|json|md)."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        res = normalize_resource(resource)
        out_fmt = opts.output.lower()
        wide = out_fmt == "wide"
        effective_project = None if all_projects else (opts.project or client.default_project)

        if out_fmt == "md":
            if not resource_id:
                raise CLIError(f"Output format '-o md' requires a specific {res} identifier.")
            md_text = _export_resource_markdown(
                client, res, resource_id, project_override=effective_project
            )
            sys.stdout.write(sanitize_terminal(md_text))
            return

        if res == "project":
            if resource_id:
                proj = client.get_project(resource_id)
                if out_fmt == "json":
                    dump_json(proj)
                else:
                    print_projects_table([proj], wide=wide)
            else:
                projs = client.list_projects(include_archived=include_archived)
                if out_fmt == "json":
                    dump_json(projs)
                else:
                    print_projects_table(projs, wide=wide)
            return

        if res == "seed":
            if resource_id:
                sd = client.get_seed(resource_id, project_override=effective_project)
                if out_fmt == "json":
                    dump_json(sd)
                else:
                    print_seeds_table([sd], project_slugs=_project_slug_map(client), wide=wide)
            else:
                seeds = client.list_seeds(
                    project_id=effective_project,
                    stage=stage,
                    status=status,
                )
                if out_fmt == "json":
                    dump_json(seeds)
                else:
                    print_seeds_table(seeds, project_slugs=_project_slug_map(client), wide=wide)
            return

        if res == "task":
            target_seed = seed or resource_id
            if not target_seed:
                raise CLIError(
                    "Listing tasks requires a seed reference: "
                    "'gitseed get tasks --seed <project#number>'."
                )
            tasks = client.list_seed_tasks(target_seed, project_override=effective_project)
            if out_fmt == "json":
                dump_json(tasks)
            else:
                print_tasks_table(tasks, wide=wide)
            return

        if res == "run":
            if resource_id:
                run = client.get_run(resource_id)
                if out_fmt == "json":
                    dump_json(run)
                else:
                    print_runs_table([run], agent_handles=_agent_handle_map(client), wide=wide)
            else:
                resolved_seed_id = (
                    client.get_seed(seed, project_override=effective_project).id if seed else None
                )
                runs = client.list_runs(
                    seed_id=resolved_seed_id,
                    project_id=effective_project,
                    stage=stage,
                    status=status,
                )
                if out_fmt == "json":
                    dump_json(runs)
                else:
                    print_runs_table(runs, agent_handles=_agent_handle_map(client), wide=wide)
            return

        if res == "agent":
            if resource_id:
                ag = client.get_agent(resource_id)
                if out_fmt == "json":
                    dump_json(ag)
                else:
                    print_agents_table([ag], wide=wide)
            else:
                agents = client.list_agents(include_archived=include_archived)
                if out_fmt == "json":
                    dump_json(agents)
                else:
                    print_agents_table(agents, wide=wide)
            return

        if res == "benchmark":
            bench = client.get_agent_benchmarks(
                project_id=effective_project,
                stage=stage,
            )
            if out_fmt == "json":
                dump_json(bench)
            else:
                print_benchmarks_table(bench)
            return

        if res == "skill":
            if resource_id:
                sk = client.get_skill(resource_id)
                if out_fmt == "json":
                    dump_json(sk)
                else:
                    print_skills_table([sk], wide=wide)
            else:
                skills = client.list_skills(include_archived=include_archived)
                if out_fmt == "json":
                    dump_json(skills)
                else:
                    print_skills_table(skills, wide=wide)
            return

        if res == "policy":
            policies = (
                client.list_project_policies(effective_project)
                if effective_project
                else client.list_org_policies()
            )
            if resource_id:
                policies = [p for p in policies if str(p.stage) == resource_id.strip().lower()]
            if out_fmt == "json":
                dump_json(policies)
            else:
                print_policies_table(policies, agent_handles=_agent_handle_map(client), wide=wide)
            return

        if res == "backend":
            if resource_id:
                bnd = client.get_backend(resource_id)
                if out_fmt == "json":
                    dump_json(bnd)
                else:
                    print_backends_table([bnd], wide=wide)
            else:
                backends = client.list_backends()
                if out_fmt == "json":
                    dump_json(backends)
                else:
                    print_backends_table(backends, wide=wide)
            return

        if res == "sandbox":
            proj_ref = client.resolve_project_ref(effective_project)
            if resource_id:
                sbx = client.get_sandbox(proj_ref, resource_id)
                if out_fmt == "json":
                    dump_json(sbx)
                else:
                    print_sandboxes_table([sbx], wide=wide)
            else:
                sbxs = client.list_sandboxes(proj_ref)
                if out_fmt == "json":
                    dump_json(sbxs)
                else:
                    print_sandboxes_table(sbxs, wide=wide)
            return

        if res == "session":
            if resource_id:
                kind = "terminal" if resource_id.startswith("sbx_") else "chat"
                item = client.get_session_item(kind, resource_id)
                if out_fmt == "json":
                    dump_json(item)
                else:
                    print_sessions_table([item], wide=wide)
            else:
                sessions = client.list_sessions(
                    project_id=effective_project,
                    state=status if status in {"running", "ended", "all"} else "all",
                )
                if out_fmt == "json":
                    dump_json(sessions)
                else:
                    print_sessions_table(sessions, wide=wide)
            return

        if res == "user":
            if resource_id:
                usr = client.get_user(resource_id)
                if out_fmt == "json":
                    dump_json(usr)
                else:
                    print_users_table([usr], wide=wide)
            else:
                users = client.list_users(status=status)
                if out_fmt == "json":
                    dump_json(users)
                else:
                    print_users_table(users, wide=wide)
            return

        if res == "token":
            tokens = client.list_tokens()
            if resource_id:
                tokens = [t for t in tokens if t.id == resource_id]
            if out_fmt == "json":
                dump_json(tokens)
            else:
                print_tokens_table(tokens, wide=wide)
            return

    @app.command("describe")
    def describe_cmd(
        ctx: typer.Context,
        resource: str = typer.Argument(..., help="Resource type to describe."),
        resource_id: str = typer.Argument(
            ..., help="Resource ID, slug, @handle, or project#number."
        ),
    ) -> None:
        """Show detailed information about a specific resource."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        res = normalize_resource(resource)

        if res == "seed":
            sd = client.get_seed(resource_id, project_override=opts.project)
            tasks = client.list_seed_tasks(sd.id)
            approvals = client.list_seed_approvals(sd.id)
            comments = client.list_seed_comments(sd.id)
            runs = client.list_runs(seed_id=sd.id, limit=10)
            slug_map = _project_slug_map(client)
            if opts.output == "json":
                dump_json(
                    {
                        "seed": sd.model_dump(mode="json"),
                        "tasks": [t.model_dump(mode="json") for t in tasks],
                        "approvals": [a.model_dump(mode="json") for a in approvals],
                        "comments": [c.model_dump(mode="json") for c in comments],
                        "runs": [r.model_dump(mode="json") for r in runs],
                    }
                )
                return
            describe_seed(
                sd,
                project_slug=slug_map.get(sd.project_id),
                tasks=tasks,
                approvals=approvals,
                comments=comments,
                runs=runs,
            )
            return

        if res == "project":
            proj = client.get_project(resource_id)
            policies = client.list_project_policies(proj.id)
            if opts.output == "json":
                dump_json(
                    {
                        "project": proj.model_dump(mode="json"),
                        "policies": [p.model_dump(mode="json") for p in policies],
                    }
                )
                return
            lines = [
                f"[bold]Slug:[/bold]          {escape(proj.slug)} ({escape(proj.id)})",
                f"[bold]Name:[/bold]          {escape(proj.name)}",
                f"[bold]Visibility:[/bold]    {escape(str(proj.visibility))}",
                f"[bold]Role:[/bold]          {escape(str(proj.my_role or '-'))}",
                f"[bold]Seeds:[/bold]         {proj.seed_counter}",
                f"[bold]Description:[/bold]   {escape(proj.description or '-')}",
            ]
            console.print(
                Panel("\n".join(lines), title=f"Project {escape(proj.slug)}", expand=False)
            )
            console.print("\n[bold cyan]## Effective Stage Policies[/bold cyan]")
            print_policies_table(policies, agent_handles=_agent_handle_map(client), wide=True)
            return

        if res == "agent":
            ag = client.get_agent(resource_id)
            if opts.output == "json":
                dump_json(ag)
                return
            console.print(
                _export_resource_markdown(client, "agent", ag.id),
                markup=False,
                highlight=False,
            )
            return

        if res == "skill":
            sk = client.get_skill(resource_id)
            if opts.output == "json":
                dump_json(sk)
                return
            console.print(
                _export_resource_markdown(client, "skill", sk.id),
                markup=False,
                highlight=False,
            )
            return

        if res == "run":
            run = client.get_run(resource_id)
            if opts.output == "json":
                dump_json(run)
                return
            lines = [
                f"[bold]Run ID:[/bold]        {escape(run.id)}",
                f"[bold]Seed ID:[/bold]       {escape(run.seed_id)}",
                f"[bold]Stage:[/bold]         {escape(str(run.stage))} (attempt #{run.attempt})",
                f"[bold]Agent:[/bold]         {escape(run.agent_id)}",
                f"[bold]Runtime/Model:[/bold] {escape(str(run.runtime))} / {escape(run.model)}",
                f"[bold]Status:[/bold]        {escape(str(run.status))}",
                f"[bold]Tokens:[/bold]        in={run.input_tokens}, out={run.output_tokens}",
                f"[bold]Cost:[/bold]          ${float(run.cost):.4f}",
            ]
            if run.error:
                lines.append(f"[bold red]Error:[/bold red]         {escape(run.error)}")
            console.print(Panel("\n".join(lines), title=f"Run {escape(run.id)}", expand=False))
            if run.output:
                dump_json(run.output)
            return

        if res == "backend":
            bnd = client.get_backend(resource_id)
            dump_json(bnd)
            return

        if res == "user":
            usr = client.get_user(resource_id)
            dump_json(usr)
            return

        if res == "sandbox":
            proj_ref = client.resolve_project_ref(opts.project)
            sbx = client.get_sandbox(proj_ref, resource_id)
            dump_json(sbx)
            return

        if res == "session":
            if resource_id.startswith(("ses_", "asn_")):
                asn_proj_ref = opts.project or client.default_project
                if not asn_proj_ref:
                    item = client.get_session_item("chat", resource_id)
                    asn_proj_ref = item.project_id
                asn = client.get_agent_session(asn_proj_ref or "", resource_id)
                dump_json(asn)
                return
            item = client.get_session_item("terminal", resource_id)
            dump_json(item)
            return

        raise CLIError(f"Describe is not supported for resource type '{res}'.")

    @create_app.command("seed")
    def create_seed_cmd(
        ctx: typer.Context,
        title: str | None = typer.Argument(
            default=None, help="Seed title (omit when creating from -f <seed.md>)."
        ),
        file: Path | None = typer.Option(
            None, "-f", "--file", help="Path to a Seed Markdown manifest (.md)."
        ),
        summary: str = typer.Option("", "--summary", help="Concise seed summary."),
        spec: str = typer.Option("", "--spec", help="Technical specification."),
        criterion: list[str] | None = typer.Option(
            None, "--criterion", "-c", help="Acceptance criterion (repeatable)."
        ),
        autonomy: SeedAutonomy = typer.Option(
            SeedAutonomy.SUPERVISED, "--autonomy", help="Autonomy mode."
        ),
        budget_cap: float | None = typer.Option(
            None, "--budget-cap", help="Optional seed budget cap in USD."
        ),
        tag: list[str] | None = typer.Option(None, "--tag", help="Tag to attach (repeatable)."),
        stage: SeedStage | None = typer.Option(
            None, "--stage", help="Optional initial SDLC stage override."
        ),
        fast_track: bool = typer.Option(
            False,
            "--fast-track",
            help="Skip Ideate and Plan and start directly in Execute.",
        ),
        staged: bool = typer.Option(
            False,
            "--staged",
            help="Stage the seed without starting an agent run immediately.",
        ),
    ) -> None:
        """Create a new seed in a project from a title or -f seed.md."""
        opts = get_opts(ctx)
        client = make_client(ctx)

        if file is not None:
            raw_text = sys.stdin.read() if str(file) == "-" else file.read_text(encoding="utf-8")
            manifest = parse_markdown_manifest(raw_text, source=str(file))
            if manifest.kind != "Seed":
                raise CLIError(
                    f"{file}: Expected 'kind: Seed', got 'kind: {manifest.kind}'. "
                    "Use 'gitseed apply -f' for configuration manifests."
                )
            if manifest.ignored_readonly_sections:
                sections_str = ", ".join(manifest.ignored_readonly_sections)
                err_console.print(
                    f"[yellow]Note:[/yellow] {file}: ignored read-only "
                    f"workflow sections ({sections_str})."
                )
            proj_ref = client.resolve_project_ref(
                opts.project or manifest.frontmatter.get("project")
            )
            project = client.get_project(proj_ref)
            req = build_create_seed_request(manifest, project_id=project.id, source=str(file))
            if stage is not None:
                req.stage = stage
            if fast_track:
                req.fast_track = True
                req.stage = SeedStage.EXECUTE
            if staged:
                req.staged = True
        else:
            if not title or not title.strip():
                raise CLIError("Provide a seed title or pass -f <seed.md> to create a seed.")
            proj_ref = client.resolve_project_ref(opts.project)
            project = client.get_project(proj_ref)
            req = CreateSeedRequest(
                project_id=project.id,
                title=title.strip(),
                description=summary.strip(),
                specification=spec.strip(),
                acceptance_criteria=list(criterion or []),
                autonomy=autonomy,
                budget_cap=Decimal(str(budget_cap)) if budget_cap is not None else None,
                tags=list(tag or []),
                stage=SeedStage.EXECUTE if fast_track else stage,
                fast_track=fast_track,
                staged=staged,
            )

        created = client.create_seed(req)
        if opts.output == "json":
            dump_json(created)
            return
        console.print(
            f"[green]✓[/green] Created seed [bold]{escape(project.slug)}#{created.number}[/bold] "
            f"({escape(created.id)}) in stage [bold]{escape(str(created.stage))}[/bold]: "
            f"{escape(created.title)}"
        )

    @app.command("apply")
    def apply_cmd(
        ctx: typer.Context,
        file: Path = typer.Option(
            ...,
            "-f",
            "--file",
            help="Markdown manifest file (.md), directory of .md files, or '-' for stdin.",
        ),
    ) -> None:
        """Apply config manifests (Agent, Skill, Policy, Project) or update an existing Seed."""
        opts = get_opts(ctx)
        client = make_client(ctx)

        sources: list[tuple[str, str]] = []
        if str(file) == "-":
            sources.append(("<stdin>", sys.stdin.read()))
        elif file.is_dir():
            md_files = sorted(file.glob("*.md"))
            if not md_files:
                raise CLIError(f"No .md manifest files found in directory '{file}'.")
            for p in md_files:
                sources.append((str(p), p.read_text(encoding="utf-8")))
        elif file.is_file():
            sources.append((str(file), file.read_text(encoding="utf-8")))
        else:
            raise CLIError(f"Path '{file}' does not exist.")

        results: list[str] = []
        for src_name, content in sources:
            manifest = parse_markdown_manifest(content, source=src_name)
            summary = apply_parsed_manifest(
                client,
                manifest,
                source=src_name,
                project_override=opts.project,
            )
            results.append(summary)
            if opts.output != "json":
                console.print(f"[green]✓[/green] {escape(summary)}")

        if opts.output == "json":
            dump_json({"applied": results})

    @app.command("edit")
    def edit_cmd(
        ctx: typer.Context,
        resource: str = typer.Argument(
            ..., help="Resource type to edit (seed, agent, skill, project, policy)."
        ),
        resource_id: str = typer.Argument(
            ..., help="Resource ID, slug, @handle, or project#number."
        ),
    ) -> None:
        """Edit a resource in $EDITOR as a Markdown manifest and apply changes on save."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        res = normalize_resource(resource)
        original_md = _export_resource_markdown(
            client, res, resource_id, project_override=opts.project
        )

        with tempfile.NamedTemporaryFile(
            mode="w+",
            suffix=f"-{res}.md",
            encoding="utf-8",
            delete=False,
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(original_md)

        try:
            editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
            if editor:
                subprocess.run(_split_editor_command(editor) + [str(tmp_path)], check=True)
            else:
                edited_text = click.edit(original_md, extension=".md")
                if edited_text is None:
                    console.print("[dim]Edit cancelled, no changes made.[/dim]")
                    return
                tmp_path.write_text(edited_text, encoding="utf-8")

            updated_md = tmp_path.read_text(encoding="utf-8")
            if updated_md.strip() == original_md.strip():
                console.print("[dim]No changes detected.[/dim]")
                return

            manifest = parse_markdown_manifest(updated_md, source=f"{res}/{resource_id}")
            summary = apply_parsed_manifest(
                client,
                manifest,
                source=f"{res}/{resource_id}",
                project_override=opts.project,
            )
            console.print(f"[green]✓[/green] {escape(summary)}")
        finally:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass

    @app.command("archive")
    def archive_cmd(
        ctx: typer.Context,
        resource: str = typer.Argument(
            ..., help="Resource type to archive (project, agent, skill)."
        ),
        resource_id: str = typer.Argument(..., help="Resource ID, slug, or @handle."),
    ) -> None:
        """Archive a project, agent, or skill."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        res = normalize_resource(resource)

        if res == "project":
            proj = client.archive_project(resource_id)
            if opts.output == "json":
                dump_json(proj)
            else:
                console.print(
                    f"[green]✓[/green] Archived project [bold]{proj.slug}[/bold] ({proj.id})."
                )
            return
        if res == "agent":
            ag = client.archive_agent(resource_id)
            if opts.output == "json":
                dump_json(ag)
            else:
                console.print(
                    f"[green]✓[/green] Archived agent [bold]@{ag.handle}[/bold] ({ag.id})."
                )
            return
        if res == "skill":
            sk = client.archive_skill(resource_id)
            if opts.output == "json":
                dump_json(sk)
            else:
                console.print(f"[green]✓[/green] Archived skill [bold]{sk.slug}[/bold] ({sk.id}).")
            return

        raise CLIError(f"Resource '{res}' cannot be archived. Supported: project, agent, skill.")

    @app.command("restore")
    def restore_cmd(
        ctx: typer.Context,
        resource: str = typer.Argument(
            ..., help="Resource type to restore (project, agent, skill)."
        ),
        resource_id: str = typer.Argument(..., help="Resource ID, slug, or @handle."),
    ) -> None:
        """Restore an archived project, agent, or skill."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        res = normalize_resource(resource)

        if res == "project":
            proj = client.restore_project(resource_id)
            if opts.output == "json":
                dump_json(proj)
            else:
                console.print(
                    f"[green]✓[/green] Restored project [bold]{proj.slug}[/bold] ({proj.id})."
                )
            return
        if res == "agent":
            ag = client.restore_agent(resource_id)
            if opts.output == "json":
                dump_json(ag)
            else:
                console.print(
                    f"[green]✓[/green] Restored agent [bold]@{ag.handle}[/bold] ({ag.id})."
                )
            return
        if res == "skill":
            sk = client.restore_skill(resource_id)
            if opts.output == "json":
                dump_json(sk)
            else:
                console.print(f"[green]✓[/green] Restored skill [bold]{sk.slug}[/bold] ({sk.id}).")
            return

        raise CLIError(f"Resource '{res}' cannot be restored. Supported: project, agent, skill.")

    @app.command("delete")
    def delete_cmd(
        ctx: typer.Context,
        resource: str = typer.Argument(
            ...,
            help="Resource type to delete (token, backend, user, sandbox, session, policy).",
        ),
        resource_id: str = typer.Argument(..., help="Resource ID or stage to delete/reset."),
    ) -> None:
        """Delete a token, unused backend, pending user, sandbox/session, or project policy override."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        res = normalize_resource(resource)

        if res in {"project", "agent", "skill"}:
            raise CLIError(
                f"{res.capitalize()}s are archived rather than deleted. "
                f"Run: gitseed archive {res} {resource_id}"
            )
        if res == "token":
            client.delete_token(resource_id)
            console.print(f"[green]✓[/green] Deleted token [bold]{resource_id}[/bold].")
            return
        if res == "backend":
            client.delete_backend(resource_id)
            console.print(f"[green]✓[/green] Deleted backend [bold]{resource_id}[/bold].")
            return
        if res == "user":
            client.delete_invited_user(resource_id)
            console.print(f"[green]✓[/green] Deleted invited user [bold]{resource_id}[/bold].")
            return
        if res == "sandbox":
            proj_ref = client.resolve_project_ref(opts.project)
            client.delete_sandbox(proj_ref, resource_id)
            console.print(f"[green]✓[/green] Deleted sandbox [bold]{resource_id}[/bold].")
            return
        if res == "session":
            proj_ref = client.resolve_project_ref(opts.project)
            if resource_id.startswith("sbx_"):
                client.delete_sandbox(proj_ref, resource_id)
            else:
                client.delete_agent_session(proj_ref, resource_id)
            console.print(f"[green]✓[/green] Deleted session [bold]{resource_id}[/bold].")
            return
        if res == "policy":
            proj_ref = client.resolve_project_ref(opts.project)
            client.reset_project_policy(proj_ref, resource_id.strip().lower())
            console.print(
                f"[green]✓[/green] Reset project policy override for stage [bold]{resource_id}[/bold]."
            )
            return

        raise CLIError(
            f"Resource '{res}' does not support deletion. "
            "Supported: token, backend, user, sandbox, session, policy."
        )

    @app.command("logs")
    def logs_cmd(
        ctx: typer.Context,
        target: str = typer.Argument(
            ..., help="Run ID (run_...) or seed reference (<project>#<number>)."
        ),
        follow: bool = typer.Option(
            False, "-f", "--follow", help="Follow logs until the active run completes."
        ),
        tail: int | None = typer.Option(
            None, "--tail", help="Show only the last N lines of logs."
        ),
        poll_interval: float = typer.Option(
            2.0, "--poll-interval", hidden=True, help="Polling interval in seconds."
        ),
    ) -> None:
        """Print output and logs for an agent run (or the latest run of a seed)."""
        opts = get_opts(ctx)
        client = make_client(ctx)

        if target.strip().startswith("run_"):
            run = client.get_run(target.strip())
        else:
            sd = client.get_seed(target, project_override=opts.project)
            runs = client.list_runs(seed_id=sd.id, limit=1)
            if not runs:
                raise CLIError(f"No runs found for seed {target} ({sd.id}).")
            run = runs[0]

        printed_text = ""

        while True:
            current_chunk = _fetch_run_log_output(client, run, tail=tail)
            new_part = extract_log_tail_delta(printed_text, current_chunk)
            if new_part:
                console.print(sanitize_terminal(new_part), end="", markup=False, highlight=False)
            if current_chunk:
                printed_text = current_chunk

            if not follow or run.status not in RUN_ACTIVE_STATUSES:
                break
            time.sleep(poll_interval)
            run = client.get_run(run.id)

        if not printed_text:
            console.print(
                f"[dim]Run {escape(run.id)} ({escape(str(run.stage))}) status: "
                f"{escape(str(run.status))} (no log output recorded).[/dim]"
            )

    @app.command("diff")
    def diff_cmd(
        ctx: typer.Context,
        seed_ref: str = typer.Argument(..., help="Seed reference (<project>#<number> or sed_...)."),
        repo_id: int | None = typer.Option(None, "--repo-id", help="Optional repository ID."),
    ) -> None:
        """Show Git branch commit history, file stats, and unified diff for a seed."""
        opts = get_opts(ctx)
        client = make_client(ctx)
        changes = client.get_seed_changes(seed_ref, repo_id=repo_id, project_override=opts.project)
        if opts.output == "json":
            dump_json(changes)
            return
        render_seed_diff(changes)

    @app.command("events")
    def events_cmd(
        ctx: typer.Context,
        seed: str | None = typer.Option(
            None, "-s", "--seed", help="Filter activity events by seed reference."
        ),
        limit: int = typer.Option(50, "--limit", "-n", help="Maximum events to show."),
        watch: bool = typer.Option(False, "-w", "--watch", help="Watch for new activity events."),
        poll_interval: float = typer.Option(
            2.5, "--poll-interval", hidden=True, help="Polling interval in seconds."
        ),
    ) -> None:
        """View the activity audit trail for a project or seed."""
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

        events = client.list_activity(project_id=proj_id, seed_id=seed_id, limit=limit)
        if opts.output == "json":
            dump_json(events)
            return

        print_events_table(events)
        seen_ids = {e.id for e in events}

        while watch:
            time.sleep(poll_interval)
            latest = client.list_activity(project_id=proj_id, seed_id=seed_id, limit=20)
            new_events = [e for e in reversed(latest) if e.id not in seen_ids]
            if new_events:
                print_events_table(new_events)
                seen_ids.update(e.id for e in new_events)


def _fetch_run_log_output(
    client: GitSeedClient, run: object, *, tail: int | None = None
) -> str:
    if not isinstance(run, RunResponse):
        return ""
    try:
        clamped_tail = max(50, min(5000, tail if tail is not None else 4000))
        log_data = client.get_run_logs(run.id, tail=clamped_tail)
        content = log_data.log_tail
        if isinstance(content, str) and content.strip():
            text = content.rstrip() + "\n"
            if run.error and f"ERROR: {run.error}" not in text:
                text += f"ERROR: {run.error}\n"
            return text
    except CLIError:
        pass
    return _format_run_log_output(run)


def _format_run_log_output(run: object) -> str:
    if not isinstance(run, RunResponse):
        return ""
    lines: list[str] = []
    out = run.output or {}
    log_tail = out.get("log_tail")
    summary = out.get("summary_comment")
    if isinstance(log_tail, str) and log_tail.strip():
        lines.append(log_tail.rstrip() + "\n")
    elif isinstance(summary, str) and summary.strip():
        lines.append(summary.rstrip() + "\n")
    if run.error:
        lines.append(f"ERROR: {run.error}\n")
    return "".join(lines)
