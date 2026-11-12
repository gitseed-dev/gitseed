from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Any

from gitseed_common.schemas import (
    ActivityResponse,
    AgentBenchmarksResponse,
    AgentResponse,
    AgentSessionResponse,
    ApprovalResponse,
    BackendResponse,
    ProjectResponse,
    RunResponse,
    SandboxSessionResponse,
    SeedChangesResponse,
    SeedCommentResponse,
    SeedResponse,
    SeedTaskResponse,
    SessionListItem,
    SkillResponse,
    StagePolicyResponse,
    TokenResponse,
    UserResponse,
)
from pydantic import BaseModel
from rich.console import Console
from rich.markup import escape
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table

console = Console()
err_console = Console(stderr=True)


def _add_plain_row(table: Table, *cells: str) -> None:
    table.add_row(*(escape(c) for c in cells))


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def _fmt_money(val: Any) -> str:
    if val is None:
        return "-"
    try:
        return f"${float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def dump_json(obj: BaseModel | Sequence[Any] | dict[str, Any]) -> None:
    if isinstance(obj, BaseModel):
        payload: Any = obj.model_dump(mode="json")
    elif isinstance(obj, Sequence):
        payload = [x.model_dump(mode="json") if isinstance(x, BaseModel) else x for x in obj]
    else:
        payload = obj
    print(json.dumps(payload, indent=2, default=str))


def extract_log_tail_delta(previous_tail: str, current_tail: str) -> str:
    """Return only newly appended log text between two sliding tail snapshots."""
    if not current_tail or current_tail == previous_tail:
        return ""
    if not previous_tail:
        return current_tail
    if current_tail.startswith(previous_tail):
        return current_tail[len(previous_tail) :]

    # Search line-by-line from the longest suffix of previous_tail that prefixes current_tail
    search_pos = 0
    while True:
        nl = previous_tail.find("\n", search_pos)
        if nl == -1:
            break
        suffix = previous_tail[nl + 1 :]
        if suffix and current_tail.startswith(suffix):
            return current_tail[len(suffix) :]
        search_pos = nl + 1

    # Fallback for character-capped tail windows where current_tail starts mid-line
    first_nl = current_tail.find("\n")
    if first_nl != -1:
        anchor = current_tail[: first_nl + 1]
        pos = previous_tail.find(anchor)
        while pos != -1:
            suffix = previous_tail[pos:]
            if current_tail.startswith(suffix):
                return current_tail[len(suffix) :]
            pos = previous_tail.find(anchor, pos + 1)

    return current_tail


def print_projects_table(projects: Sequence[ProjectResponse], *, wide: bool = False) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("SLUG", style="bold")
    table.add_column("NAME")
    table.add_column("VISIBILITY")
    table.add_column("ROLE")
    table.add_column("SEEDS", justify="right")
    if wide:
        table.add_column("ID", style="dim")
        table.add_column("MONTHLY BUDGET", justify="right")
        table.add_column("SEED CAP", justify="right")
        table.add_column("STATUS")
    for p in projects:
        row = [
            p.slug,
            p.name,
            str(p.visibility),
            str(p.my_role or "-"),
            str(p.seed_counter),
        ]
        if wide:
            row.extend(
                [
                    p.id,
                    _fmt_money(p.monthly_budget),
                    _fmt_money(p.seed_budget_cap),
                    "archived" if p.archived_at else "active",
                ]
            )
        _add_plain_row(*((table,) + tuple(row)))
    console.print(table)


def print_seeds_table(
    seeds: Sequence[SeedResponse],
    *,
    project_slugs: dict[str, str] | None = None,
    wide: bool = False,
) -> None:
    slugs = project_slugs or {}
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("REF", style="bold")
    table.add_column("TITLE")
    table.add_column("STAGE")
    table.add_column("STATUS")
    table.add_column("AUTONOMY")
    table.add_column("SPENT / CAP", justify="right")
    if wide:
        table.add_column("ID", style="dim")
        table.add_column("TAGS")
        table.add_column("UPDATED")
    for s in seeds:
        proj_label = slugs.get(s.project_id, s.project_id)
        ref = f"{proj_label}#{s.number}"
        budget = f"{_fmt_money(s.spent)} / {_fmt_money(s.budget_cap)}"
        row = [
            ref,
            s.title[:56] + ("..." if len(s.title) > 56 and not wide else ""),
            str(s.stage),
            str(s.status),
            str(s.autonomy),
            budget,
        ]
        if wide:
            row.extend(
                [
                    s.id,
                    ", ".join(s.tags) if s.tags else "-",
                    _fmt_dt(s.updated_at),
                ]
            )
        _add_plain_row(table, *row)
    console.print(table)


def print_tasks_table(tasks: Sequence[SeedTaskResponse], *, wide: bool = False) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("#", justify="right")
    table.add_column("ID", style="dim")
    table.add_column("STATUS")
    table.add_column("TITLE")
    if wide:
        table.add_column("STARTED")
        table.add_column("COMPLETED")
    for t in tasks:
        row = [
            str(t.position + 1),
            t.id,
            str(t.status),
            t.title,
        ]
        if wide:
            row.extend([_fmt_dt(t.started_at), _fmt_dt(t.completed_at)])
        _add_plain_row(table, *row)
    console.print(table)


def print_runs_table(
    runs: Sequence[RunResponse],
    *,
    agent_handles: dict[str, str] | None = None,
    wide: bool = False,
) -> None:
    handles = agent_handles or {}
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("SEED")
    table.add_column("STAGE")
    table.add_column("AGENT")
    table.add_column("STATUS")
    table.add_column("ATTEMPT", justify="right")
    table.add_column("COST", justify="right")
    if wide:
        table.add_column("RUNTIME")
        table.add_column("MODEL")
        table.add_column("STARTED")
    for r in runs:
        agent_label = handles.get(r.agent_id, r.agent_id)
        row = [
            r.id,
            r.seed_id,
            str(r.stage),
            agent_label,
            str(r.status),
            str(r.attempt),
            _fmt_money(r.cost),
        ]
        if wide:
            row.extend([str(r.runtime), r.model, _fmt_dt(r.started_at)])
        _add_plain_row(table, *row)
    console.print(table)


def print_agents_table(agents: Sequence[AgentResponse], *, wide: bool = False) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("HANDLE", style="bold")
    table.add_column("NAME")
    table.add_column("KIND")
    table.add_column("RUNTIME")
    table.add_column("MODEL")
    table.add_column("STATUS")
    if wide:
        table.add_column("ID", style="dim")
        table.add_column("SKILLS")
        table.add_column("RUNS", justify="right")
        table.add_column("1ST-PASS", justify="right")
        table.add_column("$/SEED", justify="right")
        table.add_column("COST", justify="right")
    for a in agents:
        status_label = "archived" if a.archived_at else ("enabled" if a.enabled else "paused")
        row = [
            f"@{a.handle}",
            a.name,
            str(a.kind),
            str(a.runtime),
            a.model,
            status_label,
        ]
        if wide:
            fp_str = f"{a.first_pass_rate:.0f}%" if a.first_pass_rate is not None else "-"
            row.extend(
                [
                    a.id,
                    ", ".join(a.skills) if a.skills else "-",
                    f"{a.succeeded_runs}/{a.run_count}",
                    fp_str,
                    _fmt_money(a.avg_cost_per_seed),
                    _fmt_money(a.total_cost),
                ]
            )
        _add_plain_row(table, *row)
    console.print(table)


def print_benchmarks_table(benchmarks: AgentBenchmarksResponse) -> None:
    if benchmarks.insights:
        console.print(
            Panel(
                "\n".join(f"• {escape(line)}" for line in benchmarks.insights),
                title="Empirical Agent / Model Insights",
                border_style="cyan",
            )
        )
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("AGENT", style="bold")
    table.add_column("RUNTIME")
    table.add_column("MODEL")
    table.add_column("STAGE")
    table.add_column("1ST-PASS APPROVAL", justify="right")
    table.add_column("ATTEMPTS", justify="right")
    table.add_column("$/SEED", justify="right")
    table.add_column("RUNS", justify="right")
    table.add_column("TOTAL COST", justify="right")
    for e in benchmarks.entries:
        fp = (
            f"{e.first_pass_approval_rate:.0f}% ({e.first_pass_approved_count}/{e.evaluated_gate_count})"
            if e.first_pass_approval_rate is not None
            else "-"
        )
        _add_plain_row(
            table,
            f"@{e.agent_handle}",
            str(e.runtime),
            e.model,
            str(e.stage or "all"),
            fp,
            f"{e.avg_attempts_per_stage:.2f}x",
            _fmt_money(e.avg_cost_per_seed),
            f"{e.succeeded_runs}/{e.run_count}",
            _fmt_money(e.total_cost),
        )
    console.print(table)


def print_skills_table(skills: Sequence[SkillResponse], *, wide: bool = False) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("SLUG", style="bold")
    table.add_column("NAME")
    table.add_column("DESCRIPTION")
    table.add_column("STATUS")
    if wide:
        table.add_column("ID", style="dim")
        table.add_column("UPDATED")
    for s in skills:
        row = [
            s.slug,
            s.name,
            (s.description or "-")[:60],
            "archived" if s.archived_at else "active",
        ]
        if wide:
            row.extend([s.id, _fmt_dt(s.updated_at)])
        _add_plain_row(table, *row)
    console.print(table)


def print_policies_table(
    policies: Sequence[StagePolicyResponse],
    *,
    agent_handles: dict[str, str] | None = None,
    wide: bool = False,
) -> None:
    handles = agent_handles or {}
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("STAGE", style="bold")
    table.add_column("ENABLED")
    table.add_column("AGENT")
    table.add_column("APPROVAL")
    table.add_column("MIN APPROVERS", justify="right")
    table.add_column("MIN ROLE")
    if wide:
        table.add_column("AUTO APPROVE")
        table.add_column("SELF APPROVE")
        table.add_column("MAX ATTEMPTS", justify="right")
        table.add_column("TIMEOUT (m)", justify="right")
    for p in policies:
        agent_label = handles.get(p.agent_id, p.agent_id) if p.agent_id else "-"
        row = [
            str(p.stage),
            "yes" if p.enabled else "no",
            agent_label,
            "required" if p.requires_approval else "none",
            str(p.min_approvers),
            str(p.min_approver_role),
        ]
        if wide:
            row.extend(
                [
                    "yes" if p.allow_auto_approve else "no",
                    "yes" if p.allow_self_approval else "no",
                    str(p.max_attempts),
                    str(p.timeout_minutes),
                ]
            )
        _add_plain_row(table, *row)
    console.print(table)


def print_backends_table(backends: Sequence[BackendResponse], *, wide: bool = False) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("NAME")
    table.add_column("TYPE")
    table.add_column("PROVIDER")
    table.add_column("HEALTH")
    table.add_column("ENABLED")
    if wide:
        table.add_column("ENDPOINT")
        table.add_column("CAPACITY", justify="right")
    for b in backends:
        row = [
            b.id,
            b.name,
            str(b.type),
            b.provider,
            str(b.health),
            "yes" if b.enabled else "no",
        ]
        if wide:
            row.extend([b.endpoint or "-", str(b.capacity or "-")])
        _add_plain_row(table, *row)
    console.print(table)


def print_sandboxes_table(
    sandboxes: Sequence[SandboxSessionResponse], *, wide: bool = False
) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("KIND")
    table.add_column("STATUS")
    table.add_column("REF")
    table.add_column("IMAGE")
    table.add_column("COMMAND")
    if wide:
        table.add_column("EXIT CODE", justify="right")
        table.add_column("STARTED")
    for sbx in sandboxes:
        row = [
            sbx.id,
            str(sbx.kind),
            str(sbx.status),
            sbx.ref,
            sbx.image,
            sbx.command[:48],
        ]
        if wide:
            row.extend(
                [
                    str(sbx.exit_code) if sbx.exit_code is not None else "-",
                    _fmt_dt(sbx.started_at),
                ]
            )
        _add_plain_row(table, *row)
    console.print(table)


def print_sessions_table(
    sessions: Sequence[SessionListItem], *, wide: bool = False
) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("KIND")
    table.add_column("STATUS")
    table.add_column("NAME")
    table.add_column("PROJECT")
    table.add_column("TARGET")
    if wide:
        table.add_column("REF")
        table.add_column("LAST ACTIVE")
    for s in sessions:
        target = (
            f"@{s.agent_handle}"
            if s.agent_handle
            else (s.image or "-")
        )
        row = [
            s.id,
            s.kind,
            s.status,
            (s.name or "-")[:40],
            s.project_slug or s.project_id,
            target,
        ]
        if wide:
            row.extend([s.ref or "-", _fmt_dt(s.last_activity_at or s.started_at)])
        _add_plain_row(table, *row)
    console.print(table)


def print_agent_sessions_table(
    sessions: Sequence[AgentSessionResponse], *, wide: bool = False
) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("STATUS")
    table.add_column("AGENT")
    table.add_column("MODEL")
    table.add_column("TITLE")
    if wide:
        table.add_column("REF")
        table.add_column("COST", justify="right")
        table.add_column("UPDATED")
    for s in sessions:
        row = [
            s.id,
            str(s.status),
            f"@{s.agent_handle}" if s.agent_handle else (s.agent_id or "-"),
            s.model,
            (s.title or "-")[:40],
        ]
        if wide:
            row.extend(
                [
                    s.ref or "-",
                    _fmt_money(s.total_cost),
                    _fmt_dt(s.updated_at),
                ]
            )
        _add_plain_row(table, *row)
    console.print(table)


def print_users_table(users: Sequence[UserResponse], *, wide: bool = False) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("EMAIL")
    table.add_column("NAME")
    table.add_column("ORG ROLE")
    table.add_column("STATUS")
    if wide:
        table.add_column("LAST ACTIVE")
        table.add_column("CREATED")
    for u in users:
        row = [
            u.id,
            u.email,
            u.preferred_name or u.name,
            str(u.org_role),
            str(u.status),
        ]
        if wide:
            row.extend([_fmt_dt(u.last_active_at), _fmt_dt(u.created_at)])
        _add_plain_row(table, *row)
    console.print(table)


def print_tokens_table(tokens: Sequence[TokenResponse], *, wide: bool = False) -> None:
    """Print personal access tokens metadata without ever printing secret values."""
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("ID", style="bold")
    table.add_column("NAME")
    table.add_column("PREFIX", style="dim")
    table.add_column("EXPIRES")
    table.add_column("LAST USED")
    if wide:
        table.add_column("STATUS")
        table.add_column("CREATED")
    for t in tokens:
        row = [
            t.id,
            t.name,
            f"{t.token_prefix}...",
            _fmt_dt(t.expires_at),
            _fmt_dt(t.last_used_at),
        ]
        if wide:
            row.extend(
                [
                    "revoked" if t.revoked_at else "active",
                    _fmt_dt(t.created_at),
                ]
            )
        _add_plain_row(table, *row)
    console.print(table)


def print_events_table(events: Sequence[ActivityResponse]) -> None:
    table = Table(show_header=True, header_style="bold cyan", box=None)
    table.add_column("TIME", style="dim")
    table.add_column("TYPE", style="bold")
    table.add_column("ACTOR")
    table.add_column("SEED")
    table.add_column("DETAILS")
    for ev in events:
        actor = f"{ev.actor_type}:{ev.actor_id or 'system'}"
        details = ", ".join(f"{k}={v}" for k, v in (ev.data or {}).items())
        _add_plain_row(
            table,
            _fmt_dt(ev.occurred_at),
            str(ev.type),
            actor,
            ev.seed_id or "-",
            details[:72],
        )
    console.print(table)


# ── Describe Formatters ───────────────────────────────────────────────


def describe_seed(
    seed: SeedResponse,
    *,
    project_slug: str | None = None,
    tasks: Sequence[SeedTaskResponse] = (),
    approvals: Sequence[ApprovalResponse] = (),
    comments: Sequence[SeedCommentResponse] = (),
    runs: Sequence[RunResponse] = (),
) -> None:
    ref = f"{project_slug or seed.project_id}#{seed.number}"
    esc_ref = escape(ref)
    blocked_suffix = f" ({escape(str(seed.blocked_reason))})" if seed.blocked_reason else ""
    tags_str = escape(", ".join(seed.tags)) if seed.tags else "-"
    meta_lines = [
        f"[bold]Reference:[/bold]    {esc_ref} ({escape(seed.id)})",
        f"[bold]Title:[/bold]        {escape(seed.title)}",
        f"[bold]Stage:[/bold]        {escape(str(seed.stage))}",
        f"[bold]Status:[/bold]       {escape(str(seed.status))}{blocked_suffix}",
        f"[bold]Autonomy:[/bold]     {escape(str(seed.autonomy))}",
        (
            f"[bold]Budget:[/bold]       "
            f"{_fmt_money(seed.spent)} spent / {_fmt_money(seed.budget_cap)} cap"
        ),
        f"[bold]Tags:[/bold]         {tags_str}",
        f"[bold]Updated:[/bold]      {_fmt_dt(seed.updated_at)}",
    ]
    console.print(Panel("\n".join(meta_lines), title=f"Seed {esc_ref}", expand=False))

    if seed.description:
        console.print("\n[bold cyan]## Summary[/bold cyan]")
        console.print(seed.description.strip(), markup=False, highlight=False)

    if seed.specification:
        console.print("\n[bold cyan]## Specification[/bold cyan]")
        console.print(seed.specification.strip(), markup=False, highlight=False)

    if seed.acceptance_criteria:
        console.print("\n[bold cyan]## Acceptance Criteria[/bold cyan]")
        for item in seed.acceptance_criteria:
            console.print(f"  - {escape(str(item))}")

    if seed.plan:
        console.print("\n[bold cyan]## Plan[/bold cyan]")
        console.print(seed.plan.strip(), markup=False, highlight=False)

    if tasks:
        console.print(f"\n[bold cyan]## Tasks ({len(tasks)})[/bold cyan]")
        print_tasks_table(tasks)

    if approvals:
        console.print(f"\n[bold cyan]## Approvals ({len(approvals)})[/bold cyan]")
        for a in approvals:
            comment_str = f" - {escape(a.comment)}" if a.comment else ""
            console.print(
                f"  • \\[{_fmt_dt(a.created_at)}] [bold]{escape(str(a.stage))}[/bold]: "
                f"{escape(str(a.decision))} by {escape(str(a.decided_by or 'auto'))}{comment_str}"
            )

    if runs:
        console.print(f"\n[bold cyan]## Recent Runs ({len(runs)})[/bold cyan]")
        print_runs_table(runs[:10])

    visible_comments = [c for c in comments if not c.deleted_at]
    if visible_comments:
        console.print(f"\n[bold cyan]## Comments ({len(visible_comments)})[/bold cyan]")
        for c in visible_comments[-10:]:
            console.print(
                f"  • \\[{_fmt_dt(c.created_at)}] [bold]{escape(str(c.author_id))}[/bold] "
                f"({escape(str(c.stage))}): {escape(c.body)}"
            )


def render_seed_diff(changes: SeedChangesResponse) -> None:
    repo_label = escape(changes.repository_full_name or "repository")
    base_label = escape(changes.base_ref)
    head_label = escape(changes.head_ref or "head")
    header = (
        f"[bold]Seed #{changes.seed_number}[/bold] · "
        f"{repo_label} "
        f"({base_label} ... {head_label})\n"
        f"Commits: {changes.total_commits} · Files: {changes.total_files} · "
        f"[green]+{changes.total_additions}[/green] / [red]-{changes.total_deletions}[/red]"
    )
    console.print(Panel(header, title="Seed Diff", expand=False))

    if changes.commits:
        console.print("\n[bold cyan]Commits[/bold cyan]")
        for c in changes.commits:
            console.print(
                f"  [yellow]{escape(c.short_sha)}[/yellow] {escape(c.message)} "
                f"[dim]({escape(c.author_name or 'agent')})[/dim]"
            )

    if not changes.files:
        console.print("\n[dim]No file changes recorded yet.[/dim]")
        return

    console.print("\n[bold cyan]Files Changed[/bold cyan]")
    for f in changes.files:
        console.print(
            f"  {escape(f'{f.status:10}')} [bold]{escape(f.path)}[/bold] "
            f"([green]+{f.additions}[/green]/[red]-{f.deletions}[/red])"
        )
        if f.patch:
            console.print(Syntax(f.patch, "diff", theme="ansi_dark"))
