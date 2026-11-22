"""Prompt builders for SDLC stage runs, live @mention steering, and output.json repair."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_STAGE_OUTPUT_EXAMPLES: dict[str, dict[str, Any]] = {
    "ideate": {
        "title": "Concise, action-oriented seed title (under 80 chars)",
        "description": "1-2 sentence executive summary of the requirement and goal (Markdown supported).",
        "specification": (
            "## Overview\nDetailed technical specification refined from the seed "
            "description and repository context...\n\n## Scope & Constraints\n- ..."
        ),
        "acceptance_criteria": [
            "**Criterion 1**: Concrete, verifiable behavior in `module.py`...",
            "**Criterion 2**: Edge case and validation handling...",
        ],
        "summary_comment": (
            "Refined the technical specification and defined testable acceptance criteria:\n"
            "- Scoped core requirements and data model changes\n"
            "- Added edge-case and test verification criteria"
        ),
    },
    "plan": {
        "plan": (
            "## File Change Manifest\n"
            "- **Create** `src/module_x.py` — Core domain model, state transitions, and validation helpers.\n"
            "- **Modify** `src/cli.py` — Wire new subcommands and entry-point flags into `main()`.\n"
            "- **Create** `tests/test_module_x.py` — Unit and edge-case tests covering happy path and invalid inputs.\n\n"
            "## Key Interfaces, Data Structures & Control Flow\n"
            "- Define `class EngineState` and `run_step(state: EngineState, action: str) -> StepResult` in `src/module_x.py`.\n"
            "- Update `src/cli.py` to parse arguments, initialize `EngineState`, and render output.\n\n"
            "## Verification & Runtime Commands\n"
            "- Run `pytest -q tests/test_module_x.py` to validate state transitions and error handling.\n"
            "- Ensure `.gitseed/run.json` configures `run_command` and `test_command`."
        ),
        "tasks": [
            {
                "title": "Implement core module_x engine, CLI wiring, and unit tests",
                "description": (
                    "- **Files**: Create `src/module_x.py`, modify `src/cli.py`, create `tests/test_module_x.py`\n"
                    "- **Changes**: Implement `EngineState` and `run_step()` in `src/module_x.py`, wire CLI dispatch in `src/cli.py`, and configure `.gitseed/run.json`\n"
                    "- **Verify**: Run `pytest -q tests/test_module_x.py` and verify CLI entry point executes cleanly"
                ),
                "target_files": [
                    "src/module_x.py",
                    "src/cli.py",
                    "tests/test_module_x.py",
                ],
            },
        ],
        "target_repositories": [],
        "suggested_follow_ups": [],
        "summary_comment": (
            "Mapped the specification to a concrete file-level implementation plan:\n"
            "- **Files to create/modify**: `src/module_x.py`, `src/cli.py`, `tests/test_module_x.py`\n"
            "- **Task 1**: Implement `EngineState` & `run_step()`, wire CLI entry point, and verify with `pytest`"
        ),
    },
    "execute": {
        "summary_comment": (
            "Implemented the active task requirements and verified checks pass:\n"
            "- Updated core implementation in `/workspace`\n"
            "- Added and ran unit tests"
        ),
        "branch_name": "gitseed/seed-1",
        "commit_sha": None,
        "pull_request_url": None,
    },
    "review": {
        "approved": True,
        "findings": [],
        "summary_comment": (
            "### Review Summary\n"
            "- Verified implementation against the approved plan and acceptance criteria\n"
            "- **Tests pass on `9f2c1e7`**: Executed `.gitseed/run.json` `test_command` with zero failures"
        ),
    },
    "merge": {
        "merged": True,
        "merge_commit_sha": None,
        "summary_comment": (
            "Verified final branch readiness and completed pre-merge checks for `gitseed/seed-1`."
        ),
    },
}


_IGNORED_TREE_DIRS = frozenset(
    {
        ".git",
        ".gitseed",
        ".gitseed-worktrees",
        "node_modules",
        ".venv",
        "venv",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        "dist",
        "build",
        ".next",
        ".turbo",
    }
)


# Large/binary artifacts that never help an agent decide what to change (and cost tokens)
_IGNORED_TREE_SUFFIXES = frozenset(
    {
        ".apk", ".aab", ".ipa", ".jar", ".war", ".zip", ".tar", ".gz", ".tgz", ".7z", ".rar",
        ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".bmp", ".tiff", ".psd",
        ".mp3", ".mp4", ".mov", ".wav", ".ogg", ".webm", ".avi",
        ".pdf", ".woff", ".woff2", ".ttf", ".otf", ".eot",
        ".so", ".dll", ".dylib", ".exe", ".bin", ".o", ".a", ".class", ".pyc", ".wasm",
        ".db", ".sqlite", ".sqlite3", ".lock",
    }
)

# How much of the tree each stage sees: (max depth, max entries). Review works from the diff.
_TREE_LIMITS: dict[str, tuple[int, int]] = {
    "ideate": (1, 40),
    "plan": (3, 120),
    "execute": (3, 120),
}

# Per-section caps (characters) so one huge field can't blow up every later stage's prompt
_SPEC_CAP = 8000
_PLAN_CAP = 8000
_DIFF_CAP = 6000
_COMMENT_CAP = 800


def _cap(text: str, limit: int, what: str) -> str:
    if len(text) <= limit:
        return text
    return text[:limit].rstrip() + f"\n\n… ({what} truncated; full text is in the seed in GitSeed)"


def _build_compact_workspace_tree(
    workspace_dir: Path | None,
    max_entries: int = 120,
    max_depth: int = 4,
) -> str:
    """Return a compact relative path listing of `workspace_dir` so agents skip initial tree discovery turns."""
    if workspace_dir is None or not workspace_dir.is_dir():
        return ""
    entries: list[str] = []
    truncated = False

    def _walk(curr: Path, depth: int) -> None:
        nonlocal truncated
        if depth > max_depth or len(entries) >= max_entries:
            if len(entries) >= max_entries:
                truncated = True
            return
        try:
            children = sorted(
                curr.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return
        for child in children:
            if child.name in _IGNORED_TREE_DIRS or child.name == "opencode.json":
                continue
            if child.suffix.lower() in _IGNORED_TREE_SUFFIXES:
                continue
            if len(entries) >= max_entries:
                truncated = True
                return
            try:
                rel = child.relative_to(workspace_dir).as_posix()
            except ValueError:
                continue
            if child.is_dir():
                entries.append(f"{rel}/")
                _walk(child, depth + 1)
            else:
                entries.append(rel)

    _walk(workspace_dir, 0)
    if not entries:
        return ""
    out = "\n".join(entries)
    if truncated:
        out += f"\n... (truncated at {max_entries} entries)"
    return out


def build_stage_prompt(
    ctx: dict[str, Any],
    output_file: Path,
    *,
    workspace_dir: Path | None = None,
    git_diff_summary: str | None = None,
) -> str:
    """Build the initial prompt for an agent stage execution."""
    stage = str(ctx.get("stage") or "ideate").lower()
    agent_handle = str(ctx.get("agent_handle") or "agent")
    agent_name = str(ctx.get("agent_name") or "Agent")
    agent_instructions = str(ctx.get("agent_instructions") or "").strip()
    project_slug = str(ctx.get("project_slug") or "project")
    seed_num = int(ctx.get("seed_number") or 1)
    title = str(ctx.get("title") or "").strip()
    description = str(ctx.get("description") or "").strip()
    orig_title = str(ctx.get("original_title") or "").strip()
    orig_description = str(ctx.get("original_description") or "").strip()
    orig_criteria = ctx.get("original_acceptance_criteria") or []
    specification = str(ctx.get("specification") or "").strip()
    criteria = ctx.get("acceptance_criteria") or []
    plan = str(ctx.get("plan") or "").strip()

    # Section sizes (chars) for the prompt-size log line; see summarize_prompt_stats()
    stats: dict[str, int] = {}

    def _track(name: str, start: int) -> None:
        stats[name] = stats.get(name, 0) + sum(len(x) + 1 for x in lines[start:])

    lines: list[str] = [
        f"# GitSeed Stage Execution — `{project_slug}#{seed_num}` ({stage.upper()})",
        f"You are **@{agent_handle}** ({agent_name}), executing the **{stage.upper()}** stage.",
    ]
    mark = len(lines)
    if agent_instructions:
        lines.extend(["", "## Agent Instructions", agent_instructions])
    stage_instructions = str(ctx.get("stage_instructions") or "").strip()
    if stage_instructions:
        # From the stage policy (org defaults, then project additions); applies to this stage only
        lines.extend(["", f"## {stage.capitalize()} Stage Instructions", stage_instructions])

    _track("instructions", mark)
    mark = len(lines)
    skills = ctx.get("skills") or []
    if isinstance(skills, list) and skills:
        lines.extend(["", "## Attached Skills"])
        for sk in skills:
            if isinstance(sk, dict):
                lines.append(f"### Skill: {sk.get('name')} (`{sk.get('slug')}`)")
                if sk.get("content"):
                    lines.append(str(sk["content"]).strip())

    _track("skills", mark)
    mark = len(lines)
    lines.extend(
        [
            "",
            "## Seed Details",
            f"- **Seed**: `{project_slug}#{seed_num}` — {title}",
            f"- **Stage**: `{stage}` (Attempt #{ctx.get('attempt', 1)})",
            f"- **Initial Description**:\n{description or '(No initial description provided)'}",
        ]
    )
    if orig_title and orig_title != title:
        lines.append(f"- **Original Submitted Title**: {orig_title}")
    if orig_description and orig_description != description:
        lines.append(f"- **Original Submitted Prompt / Summary**:\n{orig_description}")
    if (
        isinstance(orig_criteria, list)
        and orig_criteria
        and list(orig_criteria) != list(criteria)
    ):
        orig_crit_lines = "\n".join(f"  - {c}" for c in orig_criteria)
        lines.append(f"- **Original Submitted Acceptance Criteria**:\n{orig_crit_lines}")

    _track("seed", mark)
    mark = len(lines)
    if specification:
        lines.extend(["", "### Current Specification", _cap(specification, _SPEC_CAP, "specification")])
    if criteria:
        crit_lines = "\n".join(f"- {c}" for c in criteria)
        lines.extend(["", "### Current Acceptance Criteria", crit_lines])
    _track("spec", mark)
    mark = len(lines)
    # The plan matters to the stages that refine, carry out, or audit it
    if plan and stage in ("plan", "execute", "review"):
        lines.extend(["", "### Current Plan", _cap(plan, _PLAN_CAP, "plan")])
    _track("plan", mark)

    mark = len(lines)
    tasks = ctx.get("tasks") or []
    active_task_id = ctx.get("task_id")
    if isinstance(tasks, list) and tasks and stage in ("plan", "execute"):
        lines.extend(["", "### Seed Tasks"])
        for t in tasks:
            if isinstance(t, dict):
                is_active = t.get("id") == active_task_id
                marker = "👉 [ACTIVE TASK FOR THIS RUN]" if is_active else ""
                head = f"- [{t.get('status', 'pending')}] #{t.get('position', 0) + 1}: **{t.get('title')}** {marker}"
                # Full detail only for the task being executed; others are titles for orientation
                show_detail = stage == "execute" and (is_active or not active_task_id)
                lines.append(f"{head}\n  {t.get('description', '')}" if show_detail else head)
    _track("tasks", mark)

    mark = len(lines)
    attachments = [a for a in (ctx.get("attachments_local") or []) if isinstance(a, dict) and a.get("path")]
    if attachments:
        lines.extend(
            [
                "",
                "## Attached Files (open as needed)",
                "The person who created this seed attached these for reference. They are not in the "
                "repository and are never committed; copy one into the repo only if it belongs there "
                "(e.g. an icon or fixture).",
            ]
        )
        for a in attachments:
            size = int(a.get("size") or 0)
            human = f"{size / 1024 / 1024:.1f} MB" if size >= 1024 * 1024 else f"{max(1, size // 1024)} KB"
            kind = str(a.get("content_type") or "")
            lines.append(f"- `{a['path']}` ({human}{', ' + kind if kind else ''})")
    _track("attachments", mark)

    mark = len(lines)
    related_seeds = [
        r
        for r in (ctx.get("related_seeds_local") or ctx.get("related_seeds") or [])
        if isinstance(r, dict) and r.get("number")
    ]
    if related_seeds:
        lines.extend(
            [
                "",
                "## Related Seeds (open as needed)",
                "These seeds are linked to this work. Only open a file below if you need background context or prior decisions:",
            ]
        )
        for r in related_seeds:
            rel_label = {
                "follow_up_of": "follow-up of",
                "followed_up_by": "followed up by",
            }.get(str(r.get("relation") or ""), "related")
            r_branch = f" · branch `{r['branch_name']}`" if r.get("branch_name") else ""
            r_path = f" — `{r['path']}`" if r.get("path") else ""
            lines.append(
                f"- **#{r['number']} {r.get('title') or ''}** ({rel_label} · `{r.get('status') or ''}`{r_branch}){r_path}"
            )
            short_desc = (
                str(r.get("description") or "").strip().splitlines()[0]
                if r.get("description")
                else ""
            )
            if short_desc:
                lines.append(f"  {short_desc[:200]}")
    _track("related", mark)

    repos = ctx.get("repositories") or []
    if isinstance(repos, list) and repos:
        lines.extend(["", "## Checked-Out Project Repositories in `/workspace`"])
        dict_repos = [r for r in repos if isinstance(r, dict)]
        multi_repo = len(dict_repos) > 1
        for r in dict_repos:
            full_name = str(r.get("full_name") or "")
            folder_name = full_name.split("/")[-1] if full_name else "repo"
            primary_tag = " (primary)" if r.get("is_primary") else ""
            checkout_loc = (
                f" — checked out at `/workspace/{folder_name}`"
                if multi_repo
                else " — checked out at `/workspace`"
            )
            lines.append(
                f"- `{full_name}` on branch `{r.get('default_branch')}`{primary_tag}{checkout_loc}"
            )
        if multi_repo:
            lines.extend(
                [
                    "",
                    "**Multi-Repository Workspace Layout**: Your initial working directory (`CWD`) starts "
                    "inside the primary repository (`/workspace/<primary_repo>`). Sibling repositories are "
                    "located at `/workspace/<name>` (parent directory `../<name>` relative to your starting CWD). "
                    f"Stage output MUST always be written to `/workspace/.gitseed/output.json` (`{output_file.as_posix()}`), "
                    "never inside a repository subdirectory.",
                ]
            )

    mark = len(lines)
    tree_limits = _TREE_LIMITS.get(stage)
    tree_snapshot = (
        _build_compact_workspace_tree(workspace_dir, max_entries=tree_limits[1], max_depth=tree_limits[0])
        if tree_limits
        else ""
    )
    if tree_snapshot:
        lines.extend(
            [
                "",
                "## Workspace File Tree Snapshot (`/workspace`, partial)",
                "Binaries and build output are omitted. Open and read files yourself as needed; "
                "don't assume this listing is complete.",
                "```",
                tree_snapshot,
                "```",
            ]
        )
    _track("tree", mark)

    mark = len(lines)
    if git_diff_summary and git_diff_summary.strip() and stage in ("execute", "review", "merge"):
        lines.extend(
            [
                "",
                "## Workspace Git History & Branch Diff Summary",
                _cap(git_diff_summary.strip(), _DIFF_CAP, "diff summary; run `git diff` for the rest"),
            ]
        )
    _track("diff", mark)

    mark = len(lines)
    sandbox_cfg = ctx.get("sandbox_config")
    if stage == "ideate":
        sandbox_cfg = None  # specs don't depend on images/commands
    elif stage == "plan" and isinstance(sandbox_cfg, dict):
        sandbox_cfg = {k: sandbox_cfg.get(k) for k in ("run_command", "test_command", "blocked_paths")}
    if isinstance(sandbox_cfg, dict) and any(sandbox_cfg.values()):
        lines.extend(["", "## Project Sandbox & Test Configuration (from Project Settings)"])
        if sandbox_cfg.get("image"):
            lines.append(f"- **Base Image**: `{sandbox_cfg['image']}`")
        if sandbox_cfg.get("setup_command"):
            lines.append(f"- **Setup Command**: `{sandbox_cfg['setup_command']}`")
        if sandbox_cfg.get("run_command"):
            lines.append(f"- **Run Command**: `{sandbox_cfg['run_command']}`")
        if sandbox_cfg.get("test_command"):
            lines.append(f"- **Test Command**: `{sandbox_cfg['test_command']}`")
        if sandbox_cfg.get("require_tests_pass"):
            lines.append("- **Deterministic Test Gate**: `ENABLED` (supervisor will execute the test command and block completion if it exits non-zero)")
        if sandbox_cfg.get("blocked_paths"):
            lines.append(f"- **Protected / Blocked Paths (Do Not Modify)**: `{', '.join(str(p) for p in sandbox_cfg['blocked_paths'])}`")
        if sandbox_cfg.get("artifact_globs"):
            lines.append(f"- **Artifact Globs**: `{', '.join(str(g) for g in sandbox_cfg['artifact_globs'])}`")

    _track("sandbox", mark)

    mark = len(lines)
    feedback = ctx.get("approval_feedback") or []
    if isinstance(feedback, list) and feedback:
        lines.extend(["", "## Prior Stage Gate Review Feedback"])
        for fb in [f for f in feedback if isinstance(f, dict) and f.get("comment")][-5:]:
            lines.append(
                f"- **[{fb.get('stage')} · {fb.get('decision')}]**: {_cap(str(fb.get('comment')), _COMMENT_CAP, 'comment')}"
            )
    _track("feedback", mark)

    mark = len(lines)
    comments = [c for c in (ctx.get("comments") or []) if isinstance(c, dict)]
    if comments:
        # This stage's discussion, plus the latest few overall for context
        this_stage = [c for c in comments if str(c.get("stage") or "").lower() == stage]
        recent = comments[-5:]
        picked = [c for c in comments if c in this_stage[-10:] or c in recent]
        lines.extend(["", "## Seed Timeline Comments"])
        if len(picked) < len(comments):
            lines.append(f"(Showing {len(picked)} of {len(comments)}: this stage and the most recent.)")
        for c in picked:
            lines.append(
                f"- **{c.get('author_name')}** ({c.get('stage')}): {_cap(str(c.get('body') or ''), _COMMENT_CAP, 'comment')}"
            )
    _track("comments", mark)
    mark = len(lines)

    example_obj = dict(
        _STAGE_OUTPUT_EXAMPLES.get(stage, _STAGE_OUTPUT_EXAMPLES["ideate"])
    )
    seed_branch = (
        f"gitseed/{project_slug}/seed-{seed_num}"
        if project_slug
        else f"gitseed/seed-{seed_num}"
    )
    if stage == "execute":
        example_obj["branch_name"] = seed_branch
    example_json = json.dumps(example_obj, indent=2)
    schema_json = json.dumps(ctx.get("output_schema") or {}, indent=2)

    raw_min_crit = ctx.get("min_acceptance_criteria")
    raw_max_crit = ctx.get("max_acceptance_criteria")
    raw_max_tasks = ctx.get("max_tasks")
    min_crit = int(raw_min_crit) if raw_min_crit is not None else 1
    max_crit = int(raw_max_crit) if raw_max_crit is not None else 6
    max_tasks = int(raw_max_tasks) if raw_max_tasks is not None else 5

    stage_goal = {
        "ideate": (
            "Inspect the repository (if present) and the seed description/comments. "
            "Distill a crisp, action-oriented `title` and a 1-2 sentence executive `description`, "
            "and produce a clear, comprehensive technical `specification` (in Markdown) plus "
            f"between **{min_crit} and {max_crit}** concrete, testable `acceptance_criteria`. Focus on **what** the system "
            "should do (functional behavior, user/CLI/API experience, scope boundaries, and edge cases) "
            "rather than a step-by-step implementation task list, which belongs in the `plan` stage.\n\n"
            f"**Acceptance Criteria Rules ({min_crit}–{max_crit} items — quality over count)**:\n"
            f"- Produce at least **{min_crit}** and at most **{max_crit}** items in `acceptance_criteria`. "
            "Do NOT pad small seeds with filler criteria, and do NOT merge unrelated rules into long compound bullets just to stay under the maximum.\n"
            "- **Describe an outcome, not an implementation step**: State observable behavior or system state rather than how code is refactored internally.\n"
            "- **Checkable by Review on its own**: Each criterion must be independently verifiable during the `review` stage.\n"
            "- **Do not repeat the `specification`**: The `specification` sits directly above `acceptance_criteria` in the UI; avoid restating architectural prose or section summaries.\n"
            "- **Include test coverage where relevant**: When the seed involves testable code or behavior changes, dedicate one criterion to automated test verification."
        ),
        "plan": (
            "Inspect the checked-out repository in `/workspace` alongside the approved specification "
            "and acceptance criteria, and produce a concrete, code-oriented implementation `plan` "
            f"(in Markdown) plus ordered `tasks` (**at most {max_tasks} tasks**) and `target_repositories`.\n\n"
            "**CRITICAL — Do Not Rehash the Specification**: The operator can already see the "
            "`specification` and `acceptance_criteria` directly above your plan in the UI. Do NOT "
            "restate high-level product requirements or paraphrase acceptance criteria. Instead, "
            "focus on **how and where the code will change** so an engineer reviewing your plan knows "
            "exactly what will be built or modified:\n"
            "1. **File Change Manifest (`Create` / `Modify` / `Delete`)**: List the exact relative file "
            "paths in `/workspace` (e.g., `` `src/engine.py` ``, `` `src/cli.py` ``, `` `tests/test_engine.py` ``) "
            "and describe the specific additions or edits planned for each file.\n"
            "2. **Key Interfaces, Functions, Classes & Data Flow**: Name the concrete classes, functions/signatures, "
            "data structures, state transitions, CLI flags, or API routes to add or modify, and explain "
            "how the modules connect.\n"
            "3. **Verification & Runtime Execution**: Specify the exact test files, test runner commands, "
            "and entry-point commands (including `.gitseed/run.json` setup) that will verify the changes.\n\n"
            f"**Task Sizing & Policy Limit (Maximum {max_tasks} Tasks)**: Each item in `tasks` dispatches a separate, "
            "self-contained Execution stage run (repo checkout, agent session, test run, and commit). "
            f"Keep `tasks` between **1 and {max_tasks}** cohesive, milestone-level items: avoid creating separate tasks for trivial actions "
            "(such as creating directories, adding imports/types, editing a single helper, or running a formatter). "
            "Group related file changes, implementation work, and their corresponding unit tests into milestone tasks. "
            "Inside each task's `description`, explicitly list `- **Files**: ...`, `- **Changes**: ...` (specific functions/classes), and `- **Verify**: ...`, "
            "and populate `target_files` with the relative file paths touched by that task so disjoint tasks can be executed in parallel sub-worktrees.\n\n"
            f"**Splitting Oversized Seeds (`suggested_follow_ups`)**: Hitting the {max_tasks}-task limit usually means the seed is too big. "
            f"Rather than forcing a large job into {max_tasks} oversized tasks, scope `tasks` to the core foundational work (at most {max_tasks} tasks) "
            "and populate `suggested_follow_ups` (`[{ \"title\": \"...\", \"description\": \"...\", \"rationale\": \"...\" }]`) "
            "with the remaining logical pieces of work so the approver at the Plan gate can split them into linked follow-up seeds."
        ),
        "execute": (
            "Implement the active task (or seed plan) by creating or editing the actual source "
            "and test files directly inside `/workspace`, run relevant checks/tests, and then "
            f"record your summary in `{output_file}`. You MUST write real code changes to disk "
            "in `/workspace` before finishing. In addition, ensure `.gitseed/run.json` exists in "
            "the repository root with `{ \"image\": \"python:3.12-slim\", \"setup_command\": \"...\", "
            "\"run_command\": \"...\", \"test_command\": \"...\", \"artifact_globs\": [] }` tailored to "
            "this project's stack so operators can one-click Run or Test the repository in the "
            "GitSeed Sandbox UI. Do not attempt to push to remote Git; the runner "
            f"supervisor commits and pushes branch `{seed_branch}` after you exit."
        ),
        "review": (
            f"The feature branch `{seed_branch}` is checked out in `/workspace`. "
            "Inspect the repository files and git history/diff against the default branch, "
            "audit the implementation against the specification, acceptance criteria, and plan, "
            "and **actually execute the project's test suite** (using the Project Settings `test_command` "
            "if configured above, or `test_command` from `.gitseed/run.json`, or the standard stack test runner). Record the "
            "checked-out commit (`git rev-parse --short HEAD`) and explicitly state the test "
            "result in `summary_comment` (e.g. `**Tests pass on \\`<short_sha>\\`**` or `**Tests failed on \\`<short_sha>\\`**`). "
            "Set `approved` (true/false), list any `findings`, and provide a `summary_comment`."
        ),
        "merge": (
            "Verify final readiness for merge and summarize the outcome in the output file."
        ),
    }.get(stage, "Complete the stage objective and write the output JSON file.")

    lines.extend(
        [
            "",
            f"## Your Objective for `{stage.upper()}`",
            stage_goal,
            "",
            "## Markdown Formatting in Comments & Output Fields",
            "All text fields (`summary_comment`, `specification`, `plan`, `findings`, `acceptance_criteria`, and task `description`s) are rendered as **GitHub-Flavored Markdown** in the GitSeed UI:",
            "- Format `summary_comment` using Markdown (bullet points, `**bold**` key takeaways, and inline `` `code` `` for file paths, functions, and commands) so operators can scan your update at a glance.",
            "- In `review`, format each item in `findings` with inline `` `file.py` `` references and clear remediation guidance.",
            "",
            "## REQUIRED OUTPUT FILE CONTRACT",
            f"Before finishing, you **MUST** write valid JSON to `{output_file}` matching:",
            "```json",
            schema_json,
            "```",
            "",
            f"Example `{output_file}` for `{stage}`:",
            "```json",
            example_json,
            "```",
            f"Ensure `{output_file}` exists and contains valid JSON before exiting.",
        ]
    )
    _track("contract", mark)
    prompt = "\n".join(lines)
    LAST_PROMPT_STATS.clear()
    LAST_PROMPT_STATS.update({"total": len(prompt), **stats})
    return prompt


# Section sizes of the most recently built stage prompt (for the runner's prompt-size log)
LAST_PROMPT_STATS: dict[str, int] = {}


def summarize_prompt_stats(stats: dict[str, int] | None = None) -> str:
    """One log line: total size, rough token estimate, and the biggest sections."""
    s = dict(stats if stats is not None else LAST_PROMPT_STATS)
    total = s.pop("total", 0)
    if not total:
        return "prompt size unknown"
    top = sorted(((k, v) for k, v in s.items() if v > 0), key=lambda kv: kv[1], reverse=True)[:6]
    parts = " · ".join(f"{k} {v / 1000:.1f}k" for k, v in top)
    return f"{total / 1000:.1f}k chars (~{total // 4 / 1000:.1f}k tokens) · {parts}"


def build_synthesis_context(ctx: dict[str, Any]) -> str:
    """Compact seed context for converting a CLI's answer into the stage JSON (no repo dump)."""
    stage = str(ctx.get("stage") or "ideate").lower()
    criteria = ctx.get("acceptance_criteria") or []
    parts = [
        f"Seed `{ctx.get('project_slug')}#{ctx.get('seed_number')}`: {ctx.get('title') or ''}",
        f"Stage: {stage}",
    ]
    if ctx.get("description"):
        parts.append(f"Description:\n{_cap(str(ctx['description']), 2000, 'description')}")
    if criteria:
        parts.append("Acceptance criteria:\n" + "\n".join(f"- {c}" for c in criteria))
    schema = ctx.get("output_schema") or {}
    if schema:
        parts.append("Required output JSON schema:\n" + json.dumps(schema, indent=2))
    return "\n\n".join(parts)


def build_steering_continuation_prompt(
    steering_comments: list[dict[str, Any]],
    output_file: Path,
) -> str:
    """Build the continuation prompt when a Developer/Maintainer @mentions the agent mid-run."""
    comment_lines = [
        f"- **{c.get('author_name', 'Operator')}**: {c.get('body', '')}"
        for c in steering_comments
    ]
    return "\n".join(
        [
            "## LIVE OPERATOR STEERING UPDATE",
            "An operator posted new instructions mentioning you while this run was in progress:",
            *comment_lines,
            "",
            "Note: Your previous turn was interrupted to deliver this live instruction. "
            "Run `git status` or `git diff` first to inspect any partial file edits in `/workspace` before continuing.",
            "Incorporate this steering feedback into your current work and update "
            f"`{output_file}` with the revised stage output JSON before exiting.",
        ]
    )


def build_next_task_continuation_prompt(
    task: dict[str, Any],
    output_file: Path,
) -> str:
    """Build the continuation prompt to execute the next sequential task in the same warm container."""
    pos = int(task.get("position", 0)) + 1
    title = str(task.get("title") or "").strip()
    desc = str(task.get("description") or "").strip()
    return "\n".join(
        [
            f"## NEXT SEQUENTIAL TASK (#{pos}: {title})",
            "The previous task is complete in `/workspace`. Continue in this same warm workspace "
            "and implement the next planned task:",
            f"- **Task #{pos}**: **{title}**",
            desc or "(See overall seed plan for details)",
            "",
            "Write the required source and test changes directly in `/workspace`, verify tests pass, "
            f"and update `{output_file}` with a cumulative `summary_comment` covering all completed tasks.",
        ]
    )


def build_ideate_to_plan_continuation_prompt(
    ideate_output: dict[str, Any],
    output_file: Path,
    *,
    max_tasks: int | None = None,
) -> str:
    """Build the continuation prompt to fast-track `Ideate → Plan` in the same warm container & session."""
    example_json = json.dumps(_STAGE_OUTPUT_EXAMPLES["plan"], indent=2)
    spec = str(ideate_output.get("specification") or "").strip()
    criteria = ideate_output.get("acceptance_criteria") or []
    crit_lines = "\n".join(f"- {c}" for c in criteria) if isinstance(criteria, list) else ""
    eff_max_tasks = int(max_tasks) if max_tasks is not None else 5
    return "\n".join(
        [
            "## FAST-TRACK CONTINUATION: `PLAN` STAGE",
            "The `IDEATE` stage does not require a manual gate and uses your same agent profile. "
            "Continue in this warm container and session to produce the concrete `PLAN` stage output.",
            "",
            "### Finalized Specification",
            spec or "(See specification produced above)",
            "",
            "### Finalized Acceptance Criteria",
            crit_lines or "(See acceptance criteria produced above)",
            "",
            "### Objective for `PLAN`",
            "Produce a code-oriented implementation `plan` (File Change Manifest, Key Interfaces/Functions, "
            f"and Verification commands) plus ordered milestone `tasks` (**at most {eff_max_tasks} tasks**; `title`, `description`, and `target_files` "
            "listing exact relative file paths touched by each task). "
            f"If the scope genuinely exceeds {eff_max_tasks} milestone tasks, keep `tasks` to the core work (<= {eff_max_tasks}) "
            "and propose the remaining work in `suggested_follow_ups`.",
            "",
            f"Write the valid `plan` stage JSON object to `{output_file}` matching:",
            "```json",
            example_json,
            "```",
            f"Ensure `{output_file}` is written and valid before exiting.",
        ]
    )


def build_validation_repair_prompt(
    validation_error: str,
    stage: str,
    output_file: Path,
) -> str:
    """Build the single re-prompt when /workspace/.gitseed/output.json is missing or invalid."""
    example_json = json.dumps(
        _STAGE_OUTPUT_EXAMPLES.get(stage.lower(), _STAGE_OUTPUT_EXAMPLES["ideate"]),
        indent=2,
    )
    return "\n".join(
        [
            f"## OUTPUT VALIDATION ERROR (`{output_file}`)",
            f"Your output file at `{output_file}` was missing or failed Pydantic validation:",
            "```",
            validation_error,
            "```",
            f"Immediately write a valid JSON object to `{output_file}` following this structure:",
            "```json",
            example_json,
            "```",
        ]
    )
