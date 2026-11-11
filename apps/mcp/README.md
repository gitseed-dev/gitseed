# `gitseed-mcp` — Model Context Protocol Server

`gitseed-mcp` exposes the full GitSeed control plane to MCP-compatible AI coding clients (**Claude Code**, **Cursor**, **Windsurf**, **Antigravity**, and custom MCP hosts) over both **`stdio`** and **Streamable HTTP / SSE** transports.

---

## Connecting Your IDE

### Option 1: `stdio` Transport (Local Binary)

```bash
claude mcp add gitseed \
  -e GITSEED_API_URL=http://localhost:8420 \
  -e GITSEED_TOKEN=gsp_your_personal_access_token \
  -- gitseed-mcp
```

Or in `.cursor/mcp.json` / `mcp_config.json`:

```json
{
  "mcpServers": {
    "gitseed": {
      "command": "gitseed-mcp",
      "env": {
        "GITSEED_API_URL": "http://localhost:8420",
        "GITSEED_TOKEN": "gsp_your_personal_access_token"
      }
    }
  }
}
```

### Option 2: Streamable HTTP Transport (Docker Compose `:8422`)

When running via `docker compose up -d --build`, the MCP server binds exclusively to loopback on `http://127.0.0.1:8422/mcp` (and `/sse`):

```json
{
  "mcpServers": {
    "gitseed": {
      "url": "http://127.0.0.1:8422/mcp",
      "headers": {
        "Authorization": "Bearer gsp_your_personal_access_token"
      }
    }
  }
}
```

---

## Exposed Tool Catalog (64 Tools)

| Domain | Tools |
| :--- | :--- |
| **Control Plane & Coordinator** | `get_system_status`, `ask_coordinator`, `list_activity` |
| **Projects & Repositories** | `list_projects`, `get_project`, `create_project`, `update_project`, `list_project_repositories`, `get_project_policies`, `list_repository_branches`, `get_repository_tree`, `get_repository_blob` |
| **Seeds & SDLC Workflow** | `list_seeds`, `get_seed`, `create_seed`, `update_seed`, `start_seed`, `advance_seed`, `move_seed_back`, `stop_seed`, `resume_seed`, `approve_seed_checkpoint`, `close_seed`, `reopen_seed`, `get_seed_changes`, `list_seed_approvals`, `submit_seed_approval` |
| **Tasks & Comments** | `list_seed_tasks`, `create_seed_task`, `update_seed_task`, `delete_seed_task`, `list_seed_comments`, `create_seed_comment`, `update_seed_comment`, `delete_seed_comment` |
| **Runs, Sandboxes & Interactive Sessions** | `list_runs`, `get_run`, `create_run`, `cancel_run`, `get_run_logs`, `get_sandbox_config`, `list_sandboxes`, `create_sandbox`, `get_sandbox`, `send_sandbox_input`, `stop_sandbox`, `list_sessions`, `list_agent_sessions`, `get_agent_session`, `create_agent_session`, `send_agent_session_message`, `stop_agent_session`, `delete_agent_session` |
| **Agents, Skills, Policies & Backends** | `list_agents`, `get_agent`, `get_agent_benchmarks`, `list_skills`, `get_skill`, `list_org_policies`, `update_org_policy`, `update_project_policy`, `reset_project_policy`, `list_backends`, `get_backend` |

---

## Exposed MCP Resources & Resource Templates

| URI / Template | Name | Description |
| :--- | :--- | :--- |
| `gitseed://status` | GitSeed Platform Status | Control Plane health, version, and authenticated user identity (`application/json`). |
| `gitseed://projects` | Visible Projects | All non-archived projects accessible to the caller (`application/json`). |
| `gitseed://seeds` | Open Seeds | All open Seeds across visible projects (`application/json`). |
| `gitseed://runs/active` | Active Agent Runs | Currently `queued`, `running`, or `awaiting_approval` stage runs (`application/json`). |
| `gitseed://activity` | Recent Activity Feed | Recent audit and lifecycle events across the workspace (`application/json`). |
| `gitseed://projects/{project_id}` | Project Detail & Policies *(Template)* | Full project metadata, attached repositories, and SDLC stage policies (`application/json`). |
| `gitseed://seeds/{seed_id}` | Seed Workspace Context *(Template)* | Snapshot of specification, plan, tasks, approvals, comments, and recent runs (`application/json`). |

---

## Exposed Workflow Prompts (4 Prompts)

| Prompt | Arguments | Description |
| :--- | :--- | :--- |
| `triage-workspace` | `project_id?` | Inspect open Seeds and active runs to identify items awaiting approval, blocked on spend checkpoints, or failed. |
| `review-seed` | `seed_id` *(required)* | Review a Seed's locked spec, task checklist, branch diff, and latest agent runs to prepare a gate decision. |
| `plant-seed` | `project_id`, `goal` *(required)* | Draft and plant a well-structured Seed with technical specification and verifiable acceptance criteria. |
| `steer-run` | `seed_id`, `guidance` *(required)* | Inspect an active Seed run and post an `@agent` steering comment to guide implementation in place. |

---

## Testing

```bash
uv run python -m pytest apps/mcp/tests
```

