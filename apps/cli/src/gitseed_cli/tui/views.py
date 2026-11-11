from __future__ import annotations

from datetime import datetime
from typing import Any

from gitseed_common.enums import (
    BackendHealth,
    SeedStage,
    SeedStatus,
    TaskStatus,
)
from gitseed_common.schemas import SeedResponse
from rich import box
from rich.console import ConsoleRenderable, Group
from rich.layout import Layout
from rich.markdown import Markdown
from rich.panel import Panel
from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text

from gitseed_cli.tui.state import AUTONOMY_CHOICES, TUIState, compute_list_viewport

STAGE_ORDER: tuple[str, ...] = (
    SeedStage.IDEATE,
    SeedStage.PLAN,
    SeedStage.EXECUTE,
    SeedStage.REVIEW,
    SeedStage.MERGE,
)

STAGE_STYLES: dict[str, str] = {
    "ideate": "bright_magenta",
    "plan": "bright_blue",
    "execute": "bright_cyan",
    "review": "bright_yellow",
    "merge": "bright_green",
}

STATUS_STYLES: dict[str, str] = {
    "staged": "bright_magenta",
    "queued": "white",
    "starting": "bright_cyan",
    "running": "bold bright_cyan",
    "awaiting_approval": "bold bright_yellow",
    "stopped": "bold bright_yellow",
    "blocked": "bold bright_red",
    "failed": "bold bright_red",
    "succeeded": "bold bright_green",
    "merged": "bold bright_green",
    "closed": "white",
    "cancelled": "white",
    "timed_out": "bold bright_red",
}

INPUT_PROMPTS: dict[str, str] = {
    "approve": "Approve gate comment (optional, Enter to approve, Esc to cancel)",
    "request_changes": "Request changes feedback (Enter to submit, Esc to cancel)",
    "reject": "Reject seed reason (Enter to reject & close, Esc to cancel)",
    "steer": "Steer active stage @agent (Enter to send, Esc to cancel)",
    "comment": "Discussion comment (Enter to post, Esc to cancel)",
    "ask_coordinator": "Ask @coordinator (Enter to ask, Esc to cancel)",
    "filter": "Filter view by query (Enter to apply, Esc to cancel)",
    "new_seed_project": (
        "New Seed (1/3): Select Project (↑/↓ or 1-9, Enter to confirm, Esc to cancel)"
    ),
    "new_seed_autonomy": (
        "New Seed (2/3): Choose Autonomy Mode (↑/↓ or 1-3, Enter to confirm, Esc to cancel)"
    ),
    "new_seed_prompt": (
        "New Seed: Enter quick prompt / requirement (Enter to create, Esc to cancel)"
    ),
    "new_seed_title": (
        "New Seed: Enter quick prompt / requirement (Enter to create, Esc to cancel)"
    ),
}

NEW_SEED_MODES: frozenset[str] = frozenset(
    {"new_seed_project", "new_seed_autonomy", "new_seed_prompt", "new_seed_title"}
)


def _fmt_money(val: Any) -> str:
    if val is None:
        return "-"
    try:
        return f"${float(val):.2f}"
    except (TypeError, ValueError):
        return str(val)


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M")


def _stage_badge(stage: Any) -> Text:
    s = str(stage).lower()
    color = STAGE_STYLES.get(s, "bright_white")
    return Text(s.upper(), style=f"bold {color}")


def _status_badge(status: Any) -> Text:
    s = str(status).lower()
    style = STATUS_STYLES.get(s, "bright_white")
    return Text(s.upper(), style=style)


def render_pipeline_bar(seed: SeedResponse) -> Text:
    """Render the 5-stage visual pipeline bar for a seed."""
    cur_stage = str(seed.stage).lower()
    cur_status = str(seed.status).lower()
    try:
        cur_idx = STAGE_ORDER.index(cur_stage)
    except ValueError:
        cur_idx = 0

    bar = Text()
    for idx, stg in enumerate(STAGE_ORDER):
        label = stg.capitalize()
        if cur_status == SeedStatus.MERGED or idx < cur_idx:
            bar.append(f"[✓ {label}]", style="bold bright_green")
        elif idx == cur_idx:
            if cur_status == SeedStatus.AWAITING_APPROVAL:
                bar.append(f"[◆ {label} (Needs Approval)]", style="bold bright_yellow")
            elif cur_status == SeedStatus.STAGED:
                bar.append(f"[◌ {label} (Staged)]", style="bold bright_magenta")
            elif cur_status == SeedStatus.STOPPED:
                bar.append(f"[⏸ {label} (Stopped)]", style="bold bright_yellow")
            elif cur_status in {SeedStatus.FAILED, SeedStatus.BLOCKED}:
                bar.append(f"[✗ {label} ({cur_status.upper()})]", style="bold bright_red")
            else:
                bar.append(f"[● {label}]", style="bold bright_cyan")
        else:
            bar.append(f"[○ {label}]", style="white")

        if idx < len(STAGE_ORDER) - 1:
            bar.append(" ──▶ ", style="cyan")
    return bar


def render_header(state: TUIState) -> Panel:
    """Render top navigation bar and system telemetry summary with high-contrast text."""
    top_line = Text()
    top_line.append("◆ GitSeed TUI", style="bold bright_green")
    top_line.append("  │  API: ", style="white")
    top_line.append(f"{state.api_url}", style="bold bright_white")
    proj_label = state.project_filter or "all"
    top_line.append("  │  Project [p]: ", style="white")
    top_line.append(f"{proj_label}", style="bold bright_cyan")
    if state.search_query:
        top_line.append("  │  Filter [/]: ", style="white")
        top_line.append(f"{state.search_query}", style="bold bright_yellow")

    top_line.append("  │  ", style="white")
    top_line.append(f"Active Runs: {state.active_runs_count}", style="bold bright_cyan")
    top_line.append("  ·  ", style="white")
    top_line.append(
        f"Needs Approval: {state.awaiting_approval_count}",
        style="bold bright_yellow" if state.awaiting_approval_count > 0 else "white",
    )
    top_line.append("  ·  ", style="white")
    top_line.append(f"Spend: {_fmt_money(state.total_spend)}", style="bold bright_green")

    nav_line = Text()
    tabs = [
        ("seeds", "[1] Seeds"),
        ("projects", "[2] Projects"),
        ("runs", "[3] Runs & Sandboxes"),
        ("backends", "[4] Backends & Agents"),
    ]
    for view_key, title in tabs:
        if state.active_view == view_key:
            nav_line.append(f"▸ {title}", style="bold bright_cyan underline")
        else:
            nav_line.append(f"  {title}", style="white")
        nav_line.append("   ")

    return Panel(
        Group(top_line, nav_line),
        box=box.ROUNDED,
        border_style="bright_cyan",
        padding=(0, 1),
    )


def render_new_seed_wizard(state: TUIState) -> Panel:
    """Render the interactive 3-step New Seed Wizard panel."""
    mode = state.input_mode
    slugs = state.project_slug_by_id
    chosen_proj_slug = (
        slugs.get(state.new_seed_project_id or "", state.new_seed_project_id)
        or state.project_filter
        or "-"
    )

    step_bar = Text()
    s1_style = "bold bright_cyan underline" if mode == "new_seed_project" else "bold bright_green"
    s2_style = (
        "bold bright_cyan underline"
        if mode == "new_seed_autonomy"
        else ("bold bright_green" if mode in {"new_seed_prompt", "new_seed_title"} else "white")
    )
    s3_style = (
        "bold bright_cyan underline"
        if mode in {"new_seed_prompt", "new_seed_title"}
        else "white"
    )
    step_bar.append(f"1. Project ({chosen_proj_slug})", style=s1_style)
    step_bar.append("  ──▶  ", style="cyan")
    step_bar.append(f"2. Autonomy ({state.new_seed_autonomy})", style=s2_style)
    step_bar.append("  ──▶  ", style="cyan")
    step_bar.append("3. Quick Prompt", style=s3_style)

    parts: list[ConsoleRenderable] = [
        step_bar,
        Text("─" * 64, style="cyan"),
    ]

    if mode == "new_seed_project":
        parts.append(
            Text(
                "Select target project for your new Seed "
                "(use ↑/↓, j/k, number keys 1-9, or click):",
                style="bold bright_white",
            )
        )
        parts.append(Text(""))
        for idx, p in enumerate(state.projects):
            is_sel = idx == state.new_seed_project_idx
            line = Text()
            prefix = "▸ " if is_sel else "  "
            line.append(
                f"{prefix}[{idx + 1}] {p.slug}",
                style="bold bright_cyan" if is_sel else "bold white",
            )
            line.append(
                f" - {p.name}",
                style="bright_white" if is_sel else "white",
            )
            if p.description:
                line.append(f" ({p.description[:45]})", style="cyan")
            parts.append(line)
        parts.append(Text(""))
        parts.append(
            Text("Press Enter to confirm project, or Esc to cancel.", style="bright_yellow")
        )

    elif mode == "new_seed_autonomy":
        parts.append(
            Text(
                f"Target Project: {chosen_proj_slug} · "
                "Choose autonomy mode (↑/↓, j/k, 1-3, or click):",
                style="bold bright_white",
            )
        )
        parts.append(Text(""))
        for idx, (mode_val, label, desc) in enumerate(AUTONOMY_CHOICES):
            is_sel = idx == state.new_seed_autonomy_idx
            line = Text()
            prefix = "▸ " if is_sel else "  "
            line.append(
                f"{prefix}[{idx + 1}] {label} ({mode_val})",
                style="bold bright_cyan" if is_sel else "bold white",
            )
            line.append(f" - {desc}", style="bright_white" if is_sel else "white")
            parts.append(line)
        parts.append(Text(""))
        parts.append(
            Text("Press Enter to confirm autonomy mode, or Esc to cancel.", style="bright_yellow")
        )

    else:
        parts.append(
            Text(
                f"Project: {chosen_proj_slug}   │   Autonomy Mode: {state.new_seed_autonomy}",
                style="bold bright_green",
            )
        )
        parts.append(Text(""))
        parts.append(
            Text(
                "Describe what you want to build or fix (Quick Prompt):",
                style="bold bright_white",
            )
        )
        prompt_box = Panel(
            Text(f"{state.input_buffer}█", style="bold bright_cyan"),
            border_style="bright_yellow",
            box=box.ROUNDED,
            title="Seed Prompt",
        )
        parts.append(prompt_box)
        parts.append(
            Text("Press Enter to plant the Seed, or Esc to cancel.", style="bright_yellow")
        )

    return Panel(
        Group(*parts),
        title="New Seed Wizard",
        border_style="bright_yellow",
        box=box.ROUNDED,
    )


def render_seeds_view(state: TUIState) -> Layout:
    """Render View 1: Left pane Seeds table + Right pane Seed Inspector or New Seed Wizard."""
    layout = Layout()
    layout.split_row(
        Layout(name="seeds_list", ratio=5),
        Layout(name="seed_inspector", ratio=7),
    )

    seeds = state.filtered_seeds
    slugs = state.project_slug_by_id
    table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
        pad_edge=False,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("REF", style="bold bright_white", no_wrap=True)
    table.add_column("TITLE", style="bright_white", ratio=1, no_wrap=True)
    table.add_column("STAGE", no_wrap=True)
    table.add_column("STATUS", no_wrap=True)
    table.add_column("SPEND", style="bright_green", justify="right", no_wrap=True)

    sel_idx = min(state.selected_indices.get("seeds", 0), max(0, len(seeds) - 1))
    max_rows = max(5, state.terminal_height - 16)
    start_idx, end_idx = compute_list_viewport(len(seeds), sel_idx, max_rows=max_rows)
    state.list_scroll_offsets["seeds"] = start_idx
    for idx, s in enumerate(seeds[start_idx:end_idx], start=start_idx):
        is_sel = idx == sel_idx
        cursor = Text("▸", style="bold bright_cyan") if is_sel else Text(" ")
        proj_slug = slugs.get(s.project_id, s.project_id)
        ref_txt = Text(
            f"{proj_slug}#{s.number}",
            style="bold bright_cyan" if is_sel else "bold white",
        )
        title_raw = s.title[:38] + ("…" if len(s.title) > 38 else "")
        title_txt = Text(
            title_raw,
            style="bold bright_white underline" if is_sel else "white",
        )
        table.add_row(
            cursor,
            ref_txt,
            title_txt,
            _stage_badge(s.stage),
            _status_badge(s.status),
            _fmt_money(s.spent),
        )

    if not seeds:
        empty_msg = Text(
            "No seeds match the current filter. Press 'n' to create one.",
            style="bright_yellow",
        )
        left_body: ConsoleRenderable = Group(table, empty_msg)
    else:
        left_body = table

    layout["seeds_list"].update(
        Panel(
            left_body,
            title=f"Seeds ({len(seeds)})",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )

    if state.input_mode in NEW_SEED_MODES:
        layout["seed_inspector"].update(render_new_seed_wizard(state))
    else:
        layout["seed_inspector"].update(render_seed_inspector(state))
    return layout


def render_seed_inspector(state: TUIState) -> Panel:
    """Render the right-hand Seed Inspector with pipeline bar and 5 sub-tabs."""
    seed = state.selected_seed
    if seed is None:
        return Panel(
            Text(
                "Select a seed from the left pane (or press 'n' to create one).",
                style="bright_white",
            ),
            title="Seed Inspector",
            border_style="cyan",
            box=box.ROUNDED,
        )

    slugs = state.project_slug_by_id
    proj_slug = slugs.get(seed.project_id, seed.project_id)
    ref = f"{proj_slug}#{seed.number}"

    header_meta = Text()
    header_meta.append(f"{ref} ", style="bold bright_cyan")
    header_meta.append(seed.title, style="bold bright_white")
    header_meta.append(f"  [{seed.autonomy}]  ", style="bold bright_magenta")
    header_meta.append(
        f"Budget: {_fmt_money(seed.spent)} / {_fmt_money(seed.budget_cap)}",
        style="bold bright_green",
    )

    pipeline_bar = render_pipeline_bar(seed)

    # Sub-tabs bar
    sub_tabs = Text()
    timeline_count = len(state.seed_comments) + len(state.seed_approvals)
    tab_defs = [
        ("overview", "[o] Overview & Spec"),
        ("tasks", f"[t] Tasks ({len(state.seed_tasks)})"),
        ("logs", "[l] Live Logs"),
        ("diff", "[d] Git Diff"),
        ("timeline", f"[m] Timeline & @Steer ({timeline_count})"),
    ]
    for tab_key, tab_label in tab_defs:
        if state.inspector_tab == tab_key:
            sub_tabs.append(f"▸ {tab_label}", style="bold bright_yellow underline")
        else:
            sub_tabs.append(f"  {tab_label}", style="white")
        sub_tabs.append("   ")
    if state.detail_loading:
        sub_tabs.append("⟳ syncing…", style="bold bright_cyan")

    content_renderable = _render_inspector_tab_content(state, seed)

    parts: list[ConsoleRenderable] = [
        header_meta,
        pipeline_bar,
        Text("─" * 64, style="cyan"),
        sub_tabs,
        Text(""),
        content_renderable,
    ]

    if state.coordinator_reply:
        parts.append(
            Panel(
                Markdown(state.coordinator_reply),
                title="@coordinator",
                border_style="bright_magenta",
                box=box.ROUNDED,
            )
        )

    border = "bright_yellow" if seed.status == SeedStatus.AWAITING_APPROVAL else "bright_cyan"
    return Panel(
        Group(*parts),
        title=f"Seed Inspector: {ref}",
        border_style=border,
        box=box.ROUNDED,
    )


def _render_inspector_tab_content(state: TUIState, seed: SeedResponse) -> ConsoleRenderable:
    tab = state.inspector_tab
    max_lines = max(14, state.terminal_height - 16)

    if tab == "overview":
        md_sections: list[str] = []
        if seed.blocked_reason:
            md_sections.append(f"**Blocked Reason:** `{seed.blocked_reason}`")
        if seed.description:
            md_sections.append(f"### Summary\n{seed.description.strip()}")
        if seed.specification:
            md_sections.append(f"### Specification\n{seed.specification.strip()}")
        if seed.acceptance_criteria:
            ac_checked = str(seed.status) == "merged" or (
                str(seed.stage) == "merge" and str(seed.status) == "completed"
            )
            ac_mark = "[x]" if ac_checked else "[ ]"
            ac_lines = "\n".join(f"- {ac_mark} {item}" for item in seed.acceptance_criteria)
            md_sections.append(f"### Acceptance Criteria\n{ac_lines}")
        if seed.plan:
            md_sections.append(f"### Execution Plan\n{seed.plan.strip()}")
        if not md_sections:
            md_sections.append("_No specification or plan drafted yet._")
        full_md = "\n\n".join(md_sections)
        lines = full_md.splitlines()
        total = len(lines)
        start = min(state.scroll_offset, max(0, total - 1)) if total > 0 else 0
        window = lines[start : start + max_lines]
        rendered_md = Markdown("\n".join(window) or full_md)
        if total > max_lines or start > 0:
            end_line = min(total, start + max_lines)
            scroll_bar = Text(
                f"Lines {start + 1}–{end_line} of {total}  ·  "
                "Scroll: Mouse Wheel, J/U, or PgDn/PgUp",
                style="cyan",
            )
            return Group(scroll_bar, rendered_md)
        return rendered_md

    if tab == "tasks":
        if not state.seed_tasks:
            if state.detail_loading:
                return Text("⟳ Loading execution tasks…", style="bold bright_cyan")
            return Text("No execution tasks decomposed for this seed yet.", style="white")
        t_table = Table(
            show_header=True,
            header_style="bold bright_cyan",
            box=box.SIMPLE,
            expand=True,
        )
        t_table.add_column("#", width=4, justify="right", style="bright_white")
        t_table.add_column("STATUS", width=12)
        t_table.add_column("TASK", ratio=1, style="bright_white")
        for t in state.seed_tasks[state.scroll_offset : state.scroll_offset + max_lines]:
            st = str(t.status).lower()
            if st == TaskStatus.DONE:
                icon = "✓"
            elif st == TaskStatus.RUNNING:
                icon = "●"
            elif st == TaskStatus.FAILED:
                icon = "✗"
            else:
                icon = "○"
            t_table.add_row(
                str(t.position + 1),
                Text(f"{icon} {st.upper()}", style=STATUS_STYLES.get(st, "bright_white")),
                t.title,
            )
        return t_table

    if tab == "logs":
        log_text = state.active_run_log or "No run log output available."
        lines = log_text.splitlines()
        total = len(lines)
        start = min(state.scroll_offset, max(0, total - 1)) if total > 0 else 0
        window = lines[start : start + max_lines]
        end_line = min(total, start + max_lines)
        title_str = (
            f"Stage Container Log Tail (Lines {start + 1}–{end_line} of {total})"
            if total > max_lines or start > 0
            else "Stage Container Log Tail"
        )
        return Panel(
            Text("\n".join(window), style="bright_white"),
            title=title_str,
            border_style="cyan",
            box=box.SIMPLE,
        )

    if tab == "diff":
        changes = state.seed_changes
        if changes is None:
            if state.detail_loading:
                return Text("⟳ Loading repository diff…", style="bold bright_cyan")
            return Text("No repository diff recorded for this seed yet.", style="white")
        summary = Text()
        repo_name = changes.repository_full_name or "repo"
        head_ref = changes.head_ref or "head"
        summary.append(
            f"{repo_name} ({changes.base_ref} ... {head_ref})  ",
            style="bold bright_white",
        )
        summary.append(
            f"Commits: {changes.total_commits}  Files: {changes.total_files}  ",
            style="white",
        )
        summary.append(f"+{changes.total_additions}", style="bold bright_green")
        summary.append(" / ", style="white")
        summary.append(f"-{changes.total_deletions}", style="bold bright_red")
        if getattr(changes, "pull_request_url", None):
            summary.append(f"  ·  PR: {changes.pull_request_url}", style="bold bright_cyan")

        diff_items: list[ConsoleRenderable] = [summary]

        reverted_paths = getattr(changes, "blocked_paths_reverted", None) or []
        if reverted_paths:
            diff_items.append(
                Text(
                    f"⚠ Governance Guard reverted protected paths: {', '.join(reverted_paths)}",
                    style="bold bright_yellow",
                )
            )

        if not changes.files:
            diff_items.append(Text("No file patches recorded yet.", style="white"))
            return Group(*diff_items)

        files = changes.files
        sel_file_idx = (
            state.diff_file_index
            if 0 <= state.diff_file_index <= len(files)
            else 0
        )

        # Interactive File Picker Bar / List (0 = All Files, 1..N = individual file)
        picker_hint = Text(
            "File Picker [f/F or ←/→ to choose, 0 = All Files]  ·  "
            "Scroll Diff: Mouse Wheel, J/U, PgDn/PgUp",
            style="cyan",
        )
        diff_items.append(picker_hint)

        # Show sliding window of up to 6 file choices around sel_file_idx so large PRs stay compact
        all_choices: list[tuple[int, str, int, int, str]] = [
            (
                0,
                f"All Files ({len(files)})",
                changes.total_additions,
                changes.total_deletions,
                "ALL",
            )
        ]
        for idx, f in enumerate(files, start=1):
            st_short = (f.status or "modified")[0].upper()
            all_choices.append((idx, f.path, f.additions, f.deletions, st_short))

        max_visible_choices = 6
        if len(all_choices) <= max_visible_choices:
            visible_choices = all_choices
        else:
            half = max_visible_choices // 2
            c_start = max(0, min(sel_file_idx - half, len(all_choices) - max_visible_choices))
            visible_choices = all_choices[c_start : c_start + max_visible_choices]

        for choice_idx, label_path, adds, dels, badge in visible_choices:
            is_chosen = choice_idx == sel_file_idx
            row_txt = Text()
            prefix = "▸ " if is_chosen else "  "
            row_txt.append(
                f"{prefix}[{choice_idx}] ",
                style="bold bright_yellow" if is_chosen else "cyan",
            )
            row_txt.append(
                f"{badge} ",
                style="bold bright_cyan" if is_chosen else "white",
            )
            row_txt.append(
                f"{label_path} ",
                style="bold bright_white underline" if is_chosen else "bright_white",
            )
            row_txt.append(f"(+{adds}", style="bold bright_green")
            row_txt.append("/", style="white")
            row_txt.append(f"-{dels})", style="bold bright_red")
            diff_items.append(row_txt)

        # Build unified diff lines for the selected file (or all files combined)
        combined_patch_lines: list[str] = []
        if sel_file_idx == 0:
            active_label = f"All Files ({len(files)} files)"
            for f in files:
                combined_patch_lines.append(
                    f"diff --git a/{f.path} b/{f.path} ({f.status}, +{f.additions}/-{f.deletions})"
                )
                if f.patch:
                    combined_patch_lines.extend(f.patch.splitlines())
                else:
                    combined_patch_lines.append("@@ (binary or empty file patch) @@")
                combined_patch_lines.append("")
        else:
            chosen_f = files[sel_file_idx - 1]
            active_label = f"{chosen_f.path} ({chosen_f.status}, +{chosen_f.additions}/-{chosen_f.deletions})"
            if chosen_f.patch:
                combined_patch_lines.extend(chosen_f.patch.splitlines())
            else:
                combined_patch_lines.append("@@ (binary or empty file patch) @@")

        total_diff_lines = len(combined_patch_lines)
        viewport_height = max(8, max_lines - len(visible_choices) - 4)
        start_line = (
            min(state.scroll_offset, max(0, total_diff_lines - 1))
            if total_diff_lines > 0
            else 0
        )
        end_line = min(total_diff_lines, start_line + viewport_height)
        visible_patch = "\n".join(combined_patch_lines[start_line:end_line])

        panel_title = (
            f"Diff: {active_label} · Lines {start_line + 1}–{end_line} of {total_diff_lines}"
            if total_diff_lines > 0
            else f"Diff: {active_label}"
        )
        diff_items.append(
            Panel(
                Syntax(visible_patch or "No diff content.", "diff", theme="ansi_dark"),
                title=panel_title,
                border_style="bright_cyan",
                box=box.SIMPLE,
            )
        )
        return Group(*diff_items)

    # timeline
    items: list[ConsoleRenderable] = []
    for a in state.seed_approvals:
        decision_style = (
            "bright_green"
            if str(a.decision) == "approved"
            else ("bright_yellow" if str(a.decision) == "changes_requested" else "bright_red")
        )
        line = Text()
        line.append(f"[{_fmt_dt(a.created_at)}] ", style="cyan")
        line.append(f"GATE {str(a.stage).upper()} ", style="bold bright_white")
        line.append(f"{str(a.decision).upper()}", style=f"bold {decision_style}")
        line.append(f" by {a.decided_by or 'auto'}", style="white")
        if a.comment:
            line.append(f" - {a.comment}", style="bright_white")
        items.append(line)

    visible_comments = [c for c in state.seed_comments if not c.deleted_at]
    for c in visible_comments:
        line = Text()
        line.append(f"[{_fmt_dt(c.created_at)}] ", style="cyan")
        line.append(f"{c.author_id} ", style="bold bright_cyan")
        line.append(f"({c.stage}): ", style="white")
        line.append(c.body, style="bright_white")
        items.append(line)

    if not items:
        if state.detail_loading:
            return Text("⟳ Loading timeline & approvals…", style="bold bright_cyan")
        return Text(
            "No approvals or @agent steering comments yet. Press 'c' to steer the stage agent.",
            style="white",
        )
    return Group(*items[state.scroll_offset : state.scroll_offset + max_lines])


def render_projects_view(state: TUIState) -> Layout:
    """Render View 2: Left pane Projects table + Right pane Project Details & Stage Policies."""
    layout = Layout()
    layout.split_row(
        Layout(name="projects_list", ratio=5),
        Layout(name="project_details", ratio=7),
    )

    projects = state.filtered_projects
    table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("SLUG", style="bold bright_white")
    table.add_column("NAME", style="white", ratio=1)
    table.add_column("VISIBILITY", style="cyan")
    table.add_column("SEEDS", style="bright_white", justify="right")
    table.add_column("BUDGET", style="bright_green", justify="right")

    sel_idx = min(state.selected_indices.get("projects", 0), max(0, len(projects) - 1))
    max_rows = max(5, state.terminal_height - 16)
    start_idx, end_idx = compute_list_viewport(len(projects), sel_idx, max_rows=max_rows)
    state.list_scroll_offsets["projects"] = start_idx
    for idx, p in enumerate(projects[start_idx:end_idx], start=start_idx):
        is_sel = idx == sel_idx
        cursor = Text("▸", style="bold bright_cyan") if is_sel else Text(" ")
        slug_txt = Text(p.slug, style="bold bright_cyan underline" if is_sel else "bold white")
        name_txt = Text(p.name, style="bold bright_white" if is_sel else "white")
        table.add_row(
            cursor,
            slug_txt,
            name_txt,
            str(p.visibility),
            str(p.seed_counter),
            f"{_fmt_money(p.monthly_budget)}/mo",
        )

    layout["projects_list"].update(
        Panel(
            table,
            title=f"Projects ({len(projects)})",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )

    if state.input_mode in NEW_SEED_MODES:
        layout["project_details"].update(render_new_seed_wizard(state))
        return layout

    proj = state.selected_project
    if proj is None:
        layout["project_details"].update(
            Panel(
                Text("No project selected.", style="white"),
                title="Project Details",
                border_style="cyan",
                box=box.ROUNDED,
            )
        )
        return layout

    meta = Text()
    meta.append(f"{proj.name} ", style="bold bright_white")
    meta.append(f"({proj.slug} · {proj.id})\n", style="bold bright_cyan")
    meta.append(f"{proj.description or 'No description'}\n", style="white")
    meta.append(
        f"Role: {proj.my_role or '-'}  ·  Monthly Budget: {_fmt_money(proj.monthly_budget)}  ·  "
        f"Seed Cap: {_fmt_money(proj.seed_budget_cap)}",
        style="bright_green",
    )

    pol_table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
        title="Stage Governance Policies",
    )
    pol_table.add_column("STAGE", style="bold")
    pol_table.add_column("ENABLED", style="white")
    pol_table.add_column("AGENT", style="bold bright_cyan")
    pol_table.add_column("APPROVAL GATE")
    pol_table.add_column("MIN ROLE", style="white")
    pol_table.add_column("MAX ATTEMPTS", style="white", justify="right")

    handles = state.agent_handle_by_id
    for pol in state.policies:
        agent_label = handles.get(pol.agent_id or "", pol.agent_id or "-")
        gate_badge = (
            Text("required", style="bold bright_yellow")
            if pol.requires_approval
            else Text("auto", style="bold bright_green")
        )
        pol_table.add_row(
            _stage_badge(pol.stage),
            "yes" if pol.enabled else "no",
            agent_label,
            gate_badge,
            str(pol.min_approver_role),
            str(pol.max_attempts),
        )

    layout["project_details"].update(
        Panel(
            Group(meta, Text(""), pol_table),
            title=f"Project Inspector: {proj.slug}",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )
    return layout


def render_runs_view(state: TUIState) -> Layout:
    """Render View 3: Stage Container Runs table + Run Inspector & Developer Sandboxes."""
    layout = Layout()
    layout.split_row(
        Layout(name="runs_list", ratio=6),
        Layout(name="run_details", ratio=6),
    )

    runs = state.filtered_runs
    handles = state.agent_handle_by_id
    table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
    )
    table.add_column("", width=2, no_wrap=True)
    table.add_column("RUN ID", style="bold bright_white")
    table.add_column("SEED", style="white")
    table.add_column("STAGE")
    table.add_column("AGENT", style="bright_cyan")
    table.add_column("STATUS")
    table.add_column("TOKENS", style="white", justify="right")
    table.add_column("COST", style="bright_green", justify="right")

    sel_idx = min(state.selected_indices.get("runs", 0), max(0, len(runs) - 1))
    max_rows = max(5, state.terminal_height - 16)
    start_idx, end_idx = compute_list_viewport(len(runs), sel_idx, max_rows=max_rows)
    state.list_scroll_offsets["runs"] = start_idx
    for idx, r in enumerate(runs[start_idx:end_idx], start=start_idx):
        is_sel = idx == sel_idx
        cursor = Text("▸", style="bold bright_cyan") if is_sel else Text(" ")
        tokens = f"{r.input_tokens + r.output_tokens:,}"
        run_id_txt = Text(r.id, style="bold bright_cyan underline" if is_sel else "bold white")
        table.add_row(
            cursor,
            run_id_txt,
            r.seed_id,
            _stage_badge(r.stage),
            handles.get(r.agent_id, r.agent_id),
            _status_badge(r.status),
            tokens,
            _fmt_money(r.cost),
        )

    layout["runs_list"].update(
        Panel(
            table,
            title=f"Stage Container Runs ({len(runs)})",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )

    run = state.selected_run
    right_items: list[ConsoleRenderable] = []
    if run is not None:
        info = Text()
        info.append(f"Run {run.id} ", style="bold bright_cyan")
        info.append(f"(attempt #{run.attempt} · {run.runtime} · {run.model})\n", style="white")
        info.append(
            f"Seed: {run.seed_id}  ·  Stage: {run.stage}  ·  "
            f"Status: {run.status}  ·  Cost: {_fmt_money(run.cost)}",
            style="bright_white",
        )
        right_items.append(info)
        log_tail = (run.output or {}).get("log_tail") or run.error or "No log output captured."
        right_items.append(
            Panel(
                Text(str(log_tail), style="bright_white"),
                title="Container Log Tail",
                border_style="cyan",
                box=box.SIMPLE,
            )
        )
    else:
        right_items.append(Text("No stage run selected.", style="white"))

    sbx_table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
        title=f"Developer Sandboxes ({len(state.sandboxes)})",
    )
    sbx_table.add_column("ID", style="bold bright_white")
    sbx_table.add_column("STATUS")
    sbx_table.add_column("REF", style="white")
    sbx_table.add_column("IMAGE", style="cyan")
    sbx_table.add_column("COMMAND", style="white")
    for sbx in state.sandboxes[:6]:
        sbx_table.add_row(
            sbx.id,
            _status_badge(sbx.status),
            sbx.ref,
            sbx.image,
            sbx.command[:36],
        )
    right_items.append(sbx_table)

    layout["run_details"].update(
        Panel(
            Group(*right_items),
            title="Run Logs & Sandboxes",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )
    return layout


def render_backends_view(state: TUIState) -> Layout:
    """Render View 4: Configured Backends (Git/Inference/Compute/Tools) + Agents."""
    layout = Layout()
    layout.split_row(
        Layout(name="backends_list", ratio=6),
        Layout(name="agents_list", ratio=6),
    )

    backends = state.filtered_backends
    b_table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
    )
    b_table.add_column("", width=2, no_wrap=True)
    b_table.add_column("NAME", style="bold bright_white", no_wrap=True)
    b_table.add_column("TYPE", style="cyan", no_wrap=True)
    b_table.add_column("PROVIDER", style="white", no_wrap=True)
    b_table.add_column("HEALTH", no_wrap=True)
    b_table.add_column("ENABLED", style="white", no_wrap=True)

    sel_idx = min(state.selected_indices.get("backends", 0), max(0, len(backends) - 1))
    max_rows = max(5, state.terminal_height - 16)
    start_idx, end_idx = compute_list_viewport(len(backends), sel_idx, max_rows=max_rows)
    state.list_scroll_offsets["backends"] = start_idx
    for idx, b in enumerate(backends[start_idx:end_idx], start=start_idx):
        is_sel = idx == sel_idx
        cursor = Text("▸", style="bold bright_cyan") if is_sel else Text(" ")
        h_str = str(b.health).lower()
        if h_str == BackendHealth.HEALTHY:
            h_style = "bold bright_green"
        elif h_str == BackendHealth.DEGRADED:
            h_style = "bold bright_yellow"
        elif h_str == BackendHealth.UNREACHABLE:
            h_style = "bold bright_red"
        else:
            h_style = "white"
        name_txt = Text(b.name, style="bold bright_cyan underline" if is_sel else "bold white")
        b_table.add_row(
            cursor,
            name_txt,
            str(b.type).upper(),
            b.provider,
            Text(h_str.upper(), style=h_style),
            "yes" if b.enabled else "no",
        )

    layout["backends_list"].update(
        Panel(
            b_table,
            title=f"Backends ({len(backends)})",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )

    a_table = Table(
        show_header=True,
        header_style="bold bright_cyan",
        box=box.SIMPLE_HEAD,
        expand=True,
    )
    a_table.add_column("HANDLE", style="bold bright_cyan")
    a_table.add_column("NAME", style="bright_white")
    a_table.add_column("KIND", style="white")
    a_table.add_column("RUNTIME", style="bright_magenta")
    a_table.add_column("MODEL", style="white")
    a_table.add_column("1ST-PASS", style="bright_yellow", justify="right")
    a_table.add_column("$/SEED", style="bright_green", justify="right")
    a_table.add_column("RUNS", style="white", justify="right")
    a_table.add_column("COST", style="bright_green", justify="right")

    for a in state.agents:
        fp_str = f"{a.first_pass_rate:.0f}%" if a.first_pass_rate is not None else "-"
        a_table.add_row(
            f"@{a.handle}",
            a.name,
            str(a.kind),
            str(a.runtime),
            a.model,
            fp_str,
            _fmt_money(a.avg_cost_per_seed),
            f"{a.succeeded_runs}/{a.run_count}",
            _fmt_money(a.total_cost),
        )

    layout["agents_list"].update(
        Panel(
            a_table,
            title=f"Configured Agents ({len(state.agents)})",
            border_style="bright_cyan",
            box=box.ROUNDED,
        )
    )
    return layout


def render_help_panel() -> Panel:
    """Render keyboard and mouse shortcut reference modal."""
    help_md = (
        "### GitSeed TUI Shortcuts & Mouse Controls\n\n"
        "- **Mouse**: Click any top view tab, left table row, inspector sub-tab, or footer button; "
        "use the **mouse wheel** over the left list or right inspector to scroll.\n"
        "- **Views**: `1` Seeds · `2` Projects · `3` Runs & Sandboxes · `4` Backends & Agents\n"
        "- **Navigation**: `j`/`↓` Down · `k`/`↑` Up · `g` Top · `G` Bottom · "
        "`J`/`U` or `PgDn`/`PgUp` Scroll Inspector\n"
        "- **Git Diff File Picker (`[d]` Tab)**: `f`/`→` Next File · `F`/`←` Prev File · "
        "`0` All Files · `J`/`U` or Mouse Wheel to scroll diff lines\n"
        "- **Seed Inspector Tabs**: `Tab`/`]` Next Tab · `[` Prev Tab · `o` Overview · "
        "`t` Tasks · `l` Logs · `d` Diff · `m` Timeline\n"
        "- **SDLC Stage Gate Actions**: `a` Approve Gate · `x` Request Changes · `X` Reject Seed\n"
        "- **Agent Collaboration**: `c` Steer Stage `@agent` · `C` Post Comment · "
        "`K` Ask `@coordinator`\n"
        "- **Guided New Seed Wizard (`n`)**: Pick Project (if unscoped) → Choose Autonomy Mode "
        "(`Supervised`, `Autonomous`, `Manual`) → Enter Quick Prompt\n"
        "- **Operations**: `r` Retry Failed/Blocked Seed · `z` Cancel Active Run · "
        "`p` Cycle Project · `/` Filter · `R` Refresh · `q` Quit\n"
    )
    return Panel(
        Markdown(help_md),
        title="Help & Shortcuts (Press ? or Esc or click anywhere to close)",
        border_style="bright_magenta",
        box=box.DOUBLE,
    )


def render_footer(state: TUIState) -> Panel:
    """Render contextual keybinding bar, interactive input prompt, or status/error toast."""
    if state.input_mode is not None:
        prompt_label = INPUT_PROMPTS.get(state.input_mode, state.input_mode)
        prompt_line = Text()
        prompt_line.append(f"▸ {prompt_label}: ", style="bold bright_yellow")
        if state.input_mode not in {"new_seed_project", "new_seed_autonomy"}:
            prompt_line.append(f"{state.input_buffer}█", style="bold bright_white")
        return Panel(
            prompt_line,
            border_style="bright_yellow",
            box=box.ROUNDED,
            padding=(0, 1),
        )

    keys_line = Text()
    hints = [
        ("j/k", "Move"),
        ("1-4", "View"),
        ("Tab", "Tab"),
        ("a", "Approve"),
        ("x", "Changes"),
        ("c", "@Steer"),
        ("r", "Retry"),
        ("z", "Cancel"),
        ("n", "New"),
        ("p", "Proj"),
        ("/", "Filter"),
        ("K", "@coord"),
        ("?", "Help"),
        ("q", "Quit"),
    ]
    for k, desc in hints:
        keys_line.append(f"[{k}]", style="bold bright_cyan")
        keys_line.append(f"{desc}  ", style="bright_white")

    status_line = Text()
    if state.error_message:
        status_line.append(" ✗ Error: ", style="bold bright_red")
        status_line.append(state.error_message, style="bright_red")
    else:
        status_line.append(" ● ", style="bold bright_green")
        status_line.append(state.status_message, style="bright_white")

    return Panel(
        Group(status_line, keys_line),
        border_style="bright_red" if state.error_message else "cyan",
        box=box.ROUNDED,
        padding=(0, 1),
    )


def render_tui_frame(state: TUIState) -> Layout:
    """Build the complete Rich Layout frame for the current TUIState."""
    root = Layout(name="root")
    root.split_column(
        Layout(name="header", size=4),
        Layout(name="body", ratio=1),
        Layout(name="footer", size=4),
    )

    root["header"].update(render_header(state))

    if state.show_help:
        root["body"].update(render_help_panel())
    elif state.active_view == "projects":
        root["body"].update(render_projects_view(state))
    elif state.active_view == "runs":
        root["body"].update(render_runs_view(state))
    elif state.active_view == "backends":
        root["body"].update(render_backends_view(state))
    else:
        root["body"].update(render_seeds_view(state))

    root["footer"].update(render_footer(state))
    return root
