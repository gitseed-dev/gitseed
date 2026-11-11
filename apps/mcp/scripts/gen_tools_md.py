#!/usr/bin/env python3
"""Generate or verify the MCP tool catalog Markdown table in apps/mcp/README.md."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

MCP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_ROOT.parents[1]
for p in (MCP_ROOT / "src", REPO_ROOT / "packages" / "common" / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from gitseed_mcp.catalog import TOOLS_CATALOG  # noqa: E402

DOMAIN_GROUPS: list[tuple[str, tuple[str, ...]]] = [
    (
        "Control Plane & Coordinator",
        (
            "get_system_status",
            "ask_coordinator",
            "list_activity",
        ),
    ),
    (
        "Projects & Repositories",
        (
            "list_projects",
            "get_project",
            "create_project",
            "update_project",
            "list_project_repositories",
            "get_project_policies",
            "list_repository_branches",
            "get_repository_tree",
            "get_repository_blob",
        ),
    ),
    (
        "Seeds & SDLC Workflow",
        (
            "list_seeds",
            "get_seed",
            "create_seed",
            "update_seed",
            "start_seed",
            "advance_seed",
            "move_seed_back",
            "stop_seed",
            "resume_seed",
            "approve_seed_checkpoint",
            "close_seed",
            "reopen_seed",
            "get_seed_changes",
            "list_seed_approvals",
            "submit_seed_approval",
        ),
    ),
    (
        "Tasks & Comments",
        (
            "list_seed_tasks",
            "create_seed_task",
            "update_seed_task",
            "delete_seed_task",
            "list_seed_comments",
            "create_seed_comment",
            "update_seed_comment",
            "delete_seed_comment",
        ),
    ),
    (
        "Runs, Sandboxes & Interactive Sessions",
        (
            "list_runs",
            "get_run",
            "create_run",
            "cancel_run",
            "get_run_logs",
            "get_sandbox_config",
            "list_sandboxes",
            "create_sandbox",
            "get_sandbox",
            "send_sandbox_input",
            "stop_sandbox",
            "list_sessions",
            "list_agent_sessions",
            "get_agent_session",
            "create_agent_session",
            "send_agent_session_message",
            "stop_agent_session",
            "delete_agent_session",
        ),
    ),
    (
        "Agents, Skills, Policies & Backends",
        (
            "list_agents",
            "get_agent",
            "get_agent_benchmarks",
            "list_skills",
            "get_skill",
            "list_org_policies",
            "update_org_policy",
            "update_project_policy",
            "reset_project_policy",
            "list_backends",
            "get_backend",
        ),
    ),
]


def render_tools_section() -> str:
    catalog_names = {t["name"] for t in TOOLS_CATALOG}
    grouped_names = [name for _, names in DOMAIN_GROUPS for name in names]
    if set(grouped_names) != catalog_names or len(grouped_names) != len(TOOLS_CATALOG):
        missing = sorted(catalog_names - set(grouped_names))
        extra = sorted(set(grouped_names) - catalog_names)
        raise RuntimeError(
            f"DOMAIN_GROUPS out of sync with TOOLS_CATALOG (missing={missing}, extra={extra})"
        )

    lines = [
        f"## Exposed Tool Catalog ({len(TOOLS_CATALOG)} Tools)",
        "",
        "| Domain | Tools |",
        "| :--- | :--- |",
    ]
    for domain, names in DOMAIN_GROUPS:
        formatted_tools = ", ".join(f"`{name}`" for name in names)
        lines.append(f"| **{domain}** | {formatted_tools} |")
    return "\n".join(lines)


def extract_readme_tool_names(readme_text: str) -> list[str]:
    match = re.search(
        r"## Exposed Tool Catalog.*?\n(.*?)(?:\n---|\Z)",
        readme_text,
        flags=re.DOTALL,
    )
    section = match.group(1) if match else readme_text
    return re.findall(r"`([a-z][a-z0-9_]+)`", section)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--write",
        action="store_true",
        help="Update apps/mcp/README.md in-place with the generated catalog section.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify apps/mcp/README.md matches TOOLS_CATALOG 1:1.",
    )
    args = parser.parse_args()

    readme_path = MCP_ROOT / "README.md"
    section = render_tools_section()

    if args.write:
        text = readme_path.read_text(encoding="utf-8")
        updated = re.sub(
            r"## Exposed Tool Catalog \(\d+ Tools\)\n.*?(?=\n---)",
            section + "\n",
            text,
            flags=re.DOTALL,
        )
        readme_path.write_text(updated, encoding="utf-8")
        return 0

    if args.check:
        text = readme_path.read_text(encoding="utf-8")
        readme_tools = set(extract_readme_tool_names(text))
        catalog_tools = {t["name"] for t in TOOLS_CATALOG}
        if readme_tools != catalog_tools:
            phantom = sorted(readme_tools - catalog_tools)
            undocumented = sorted(catalog_tools - readme_tools)
            print(
                f"README drift detected! phantom={phantom}, undocumented={undocumented}",
                file=sys.stderr,
            )
            return 1
        return 0

    print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
