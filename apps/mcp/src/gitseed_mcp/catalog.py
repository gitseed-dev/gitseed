from __future__ import annotations

from typing import Any

from gitseed_common.enums import (
    AgentKind,
    AgentRuntime,
    ApprovalDecision,
    BackendType,
    ProjectRole,
    ProjectVisibility,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
)

# ── Tool Schemas ──────────────────────────────────────────────────────

TOOLS_CATALOG: list[dict[str, Any]] = [
    {
        "name": "get_system_status",
        "annotations": {
            "title": "Get System Status",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get GitSeed Control Plane health, version, and current authenticated user profile."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "list_projects",
        "annotations": {
            "title": "List Projects",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List accessible GitSeed projects.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {
                    "type": "boolean",
                    "description": "Include archived projects in results.",
                    "default": False,
                },
                "tag": {
                    "type": "string",
                    "description": "Filter projects by tag.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_project",
        "annotations": {
            "title": "Get Project",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get a project by ID (prj_...) or slug, optionally including attached repositories "
            "and effective SDLC stage policies."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "include_repositories": {
                    "type": "boolean",
                    "description": "Also fetch repositories attached to the project.",
                    "default": True,
                },
                "include_policies": {
                    "type": "boolean",
                    "description": "Also fetch effective stage policies for the project.",
                    "default": False,
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_project",
        "annotations": {
            "title": "Create Project",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": "Create a new GitSeed project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Display name of the project."},
                "slug": {
                    "type": "string",
                    "description": "Optional URL slug (auto-generated from name if omitted).",
                },
                "description": {"type": "string", "description": "Project description."},
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Project tags.",
                },
                "visibility": {
                    "type": "string",
                    "enum": [v.value for v in ProjectVisibility],
                    "default": ProjectVisibility.PRIVATE.value,
                },
                "monthly_budget": {
                    "type": "number",
                    "description": "Optional monthly spend cap in USD.",
                },
                "seed_budget_cap": {
                    "type": "number",
                    "description": "Optional default per-seed spend cap in USD.",
                },
                "spend_checkpoint": {
                    "type": "number",
                    "description": "Optional spend increment requiring human checkpoint approval.",
                },
                "max_concurrent_runs": {
                    "type": "integer",
                    "description": "Maximum concurrent agent runs allowed for this project.",
                },
            },
            "required": ["name"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_project",
        "annotations": {
            "title": "Update Project",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Update an existing GitSeed project's metadata, visibility, or budgets.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "name": {"type": "string"},
                "description": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "visibility": {
                    "type": "string",
                    "enum": [v.value for v in ProjectVisibility],
                },
                "monthly_budget": {"type": "number"},
                "seed_budget_cap": {"type": "number"},
                "spend_checkpoint": {"type": "number"},
                "max_concurrent_runs": {"type": "integer"},
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_project_policies",
        "annotations": {
            "title": "Get Project Policies",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get effective SDLC stage policies (enabled, requires_approval, min_approvers, "
            "min_approver_role, agent_id, instructions) for a project."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_org_policies",
        "annotations": {
            "title": "List Organization Policies",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List organization-wide default SDLC stage policies.",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    },
    {
        "name": "update_org_policy",
        "annotations": {
            "title": "Update Organization Policy",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Update an organization-wide default SDLC stage policy.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": "SDLC stage to configure.",
                },
                "enabled": {"type": "boolean"},
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID (agt_...) assigned to this stage.",
                },
                "requires_approval": {"type": "boolean"},
                "min_approvers": {"type": "integer"},
                "min_approver_role": {
                    "type": "string",
                    "enum": [r.value for r in ProjectRole],
                },
                "allow_auto_approve": {"type": "boolean"},
                "allow_self_approval": {"type": "boolean"},
                "allow_second_opinions": {"type": "boolean"},
                "max_attempts": {"type": "integer"},
                "timeout_minutes": {"type": "integer"},
                "min_acceptance_criteria": {"type": "integer"},
                "max_acceptance_criteria": {"type": "integer"},
                "max_tasks": {"type": "integer"},
                "instructions": {
                    "type": "string",
                    "description": "Stage-specific agent instructions.",
                },
            },
            "required": ["stage"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_project_policy",
        "annotations": {
            "title": "Update Project Policy",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Update or override an SDLC stage policy for a specific project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": "SDLC stage to configure.",
                },
                "enabled": {"type": "boolean"},
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID (agt_...) assigned to this stage.",
                },
                "requires_approval": {"type": "boolean"},
                "min_approvers": {"type": "integer"},
                "min_approver_role": {
                    "type": "string",
                    "enum": [r.value for r in ProjectRole],
                },
                "allow_auto_approve": {"type": "boolean"},
                "allow_self_approval": {"type": "boolean"},
                "allow_second_opinions": {"type": "boolean"},
                "max_attempts": {"type": "integer"},
                "timeout_minutes": {"type": "integer"},
                "min_acceptance_criteria": {"type": "integer"},
                "max_acceptance_criteria": {"type": "integer"},
                "max_tasks": {"type": "integer"},
                "instructions": {
                    "type": "string",
                    "description": "Stage-specific agent instructions.",
                },
            },
            "required": ["project_id", "stage"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reset_project_policy",
        "annotations": {
            "title": "Reset Project Policy",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Reset a project's stage policy override back to organization defaults.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                },
            },
            "required": ["project_id", "stage"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_project_repositories",
        "annotations": {
            "title": "List Project Repositories",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List git repositories attached to a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_repository_branches",
        "annotations": {
            "title": "List Repository Branches",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List git branches and HEAD commit metadata in a project repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "repo_id": {
                    "type": ["integer", "string"],
                    "description": "Numeric repository ID.",
                },
            },
            "required": ["project_id", "repo_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_repository_tree",
        "annotations": {
            "title": "Get Repository Tree",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Browse files and directories in a project repository at a git ref and path."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "repo_id": {
                    "type": ["integer", "string"],
                    "description": "Numeric repository ID.",
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Branch, tag, or commit SHA (defaults to repository default branch)."
                    ),
                },
                "path": {
                    "type": "string",
                    "description": (
                        "Relative directory path inside the repository (empty for root)."
                    ),
                    "default": "",
                },
            },
            "required": ["project_id", "repo_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_repository_blob",
        "annotations": {
            "title": "Get Repository Blob",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Read a file's contents from a project repository at a given git ref.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "repo_id": {
                    "type": ["integer", "string"],
                    "description": "Numeric repository ID.",
                },
                "path": {
                    "type": "string",
                    "description": "Relative file path in the repository.",
                },
                "ref": {
                    "type": "string",
                    "description": (
                        "Branch, tag, or commit SHA (defaults to repository default branch)."
                    ),
                },
            },
            "required": ["project_id", "repo_id", "path"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_seeds",
        "annotations": {
            "title": "List Seeds",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "List seeds (work items) filtered by project, SDLC stage, status, autonomy, or tag."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Filter by project ID (prj_...) or slug.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": "Filter by SDLC stage.",
                },
                "status": {
                    "type": "string",
                    "enum": [s.value for s in SeedStatus],
                    "description": "Filter by operational status.",
                },
                "open_only": {
                    "type": "boolean",
                    "description": "Exclude merged and closed seeds.",
                    "default": False,
                },
                "autonomy": {
                    "type": "string",
                    "enum": [a.value for a in SeedAutonomy],
                    "description": "Filter by seed autonomy tier.",
                },
                "tag": {
                    "type": "string",
                    "description": "Filter by tag.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of seeds to return (1-1000).",
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_seed",
        "annotations": {
            "title": "Get Seed",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get full details of a seed by ID (sed_...) or project reference (e.g. 'gitseed#12')."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": (
                        "Seed ID (sed_...) or slug#number reference (e.g. 'gitseed#12')."
                    ),
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_seed",
        "annotations": {
            "title": "Create Seed",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": (
            "Plant a new seed (work item) in a project with optional specification, acceptance "
            "criteria, implementation plan, initial SDLC stage, and autonomy level."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "title": {
                    "type": "string",
                    "description": "Concise title of the seed.",
                },
                "description": {
                    "type": "string",
                    "description": "Problem statement, background context, or goal.",
                },
                "specification": {
                    "type": "string",
                    "description": "Technical specification markdown.",
                },
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "List of verifiable acceptance criteria.",
                },
                "plan": {
                    "type": "string",
                    "description": "Implementation plan markdown.",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Labels/tags for categorization.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": "Initial SDLC stage (defaults to 'ideate').",
                },
                "fast_track": {
                    "type": "boolean",
                    "description": (
                        "If true, skip Ideate and Plan and start the seed directly in Execute "
                        "with an auto-populated specification and initial task."
                    ),
                },
                "staged": {
                    "type": "boolean",
                    "description": (
                        "If true, create the seed in 'staged' status without immediately "
                        "queuing a stage run."
                    ),
                },
                "autonomy": {
                    "type": "string",
                    "enum": [a.value for a in SeedAutonomy],
                    "default": SeedAutonomy.SUPERVISED.value,
                    "description": "Autonomy tier governing stage gate approvals.",
                },
                "budget_cap": {
                    "type": "number",
                    "description": "Optional per-seed spend limit in USD.",
                },
                "ideate_agent_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": (
                        "Optional list of 2-3 stage Agent IDs for parallel multi-opinion ideation."
                    ),
                },
            },
            "required": ["project_id", "title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_seed",
        "annotations": {
            "title": "Update Seed",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Update an existing seed's title, description, specification, acceptance criteria, "
            "plan, tags, autonomy, or budget cap."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "title": {"type": "string"},
                "description": {"type": "string"},
                "specification": {"type": "string"},
                "acceptance_criteria": {
                    "type": "array",
                    "items": {"type": "string"},
                },
                "plan": {"type": "string"},
                "tags": {"type": "array", "items": {"type": "string"}},
                "autonomy": {
                    "type": "string",
                    "enum": [a.value for a in SeedAutonomy],
                },
                "budget_cap": {"type": "number"},
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "start_seed",
        "annotations": {
            "title": "Start Seed",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        "description": "Start work on a staged seed by moving it from 'staged' to 'queued'.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "advance_seed",
        "annotations": {
            "title": "Advance Seed",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": (
            "Advance a seed to the next SDLC stage "
            "(ideate -> plan -> execute -> review -> merge) "
            "or transition to an explicit stage."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": (
                        "Optional target stage (defaults to the next stage in sequence)."
                    ),
                },
                "fast_track": {
                    "type": "boolean",
                    "description": (
                        "If true, skip Ideate and Plan and fast-track the seed directly to Execute."
                    ),
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "move_seed_back",
        "annotations": {
            "title": "Move Seed Back",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": (
            "Send a seed back to an earlier SDLC stage with a reason and re-queue it."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": "Target earlier stage.",
                },
                "reason": {
                    "type": "string",
                    "description": "Reason for moving the seed back.",
                },
            },
            "required": ["seed_id", "stage", "reason"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stop_seed",
        "annotations": {
            "title": "Stop Seed",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Stop active agent execution on a seed and hold the seed in 'stopped' status."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "resume_seed",
        "annotations": {
            "title": "Resume Seed",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": "Resume a stopped seed by re-queuing its current SDLC stage.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "approve_seed_checkpoint",
        "annotations": {
            "title": "Approve Seed Spend Checkpoint",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": True,
        },
        "description": (
            "Approve a spend checkpoint on a blocked seed so agent execution can continue."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "approved_through": {
                    "type": "number",
                    "description": "Cumulative USD threshold approved through.",
                },
            },
            "required": ["seed_id", "approved_through"],
            "additionalProperties": False,
        },
    },
    {
        "name": "close_seed",
        "annotations": {
            "title": "Close Seed",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Close a seed without harvesting its changes.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "reopen_seed",
        "annotations": {
            "title": "Reopen Seed",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Reopen a closed or harvested seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_seed_changes",
        "annotations": {
            "title": "Get Seed Changes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get the git branch comparison, commits, changed files, and unified diff "
            "for a seed's worktree branch."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "repo_id": {
                    "type": ["integer", "string"],
                    "description": (
                        "Optional numeric repository ID if the project has multiple repos."
                    ),
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_seed_receipt",
        "annotations": {
            "title": "Get Seed Governance Receipt",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get the Ed25519-signed Governance Receipt (gitseed.receipt.v1) for a seed, "
            "attesting to locked specification hashes, stage runs, policy checks, and approvals."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_seed_approvals",
        "annotations": {
            "title": "List Seed Approvals",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List stage gate approval records for a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "submit_seed_approval",
        "annotations": {
            "title": "Submit Seed Approval",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": (
            "Submit a stage gate decision ('approved', 'changes_requested', or 'rejected') "
            "on a seed. When 'approved' satisfies the stage policy, the seed automatically "
            "advances."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "decision": {
                    "type": "string",
                    "enum": [d.value for d in ApprovalDecision],
                    "description": "Gate decision.",
                },
                "comment": {
                    "type": "string",
                    "description": "Optional review rationale or requested modifications.",
                },
            },
            "required": ["seed_id", "decision"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_seed_tasks",
        "annotations": {
            "title": "List Seed Tasks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List decomposed implementation tasks for a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_seed_task",
        "annotations": {
            "title": "Create Seed Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": "Create a new decomposed task on a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "title": {
                    "type": "string",
                    "description": "Task title.",
                },
                "description": {
                    "type": "string",
                    "description": "Detailed task instructions or scope.",
                },
                "position": {
                    "type": "integer",
                    "description": "Optional 1-based ordering position.",
                },
            },
            "required": ["seed_id", "title"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_seed_task",
        "annotations": {
            "title": "Update Seed Task",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Update a seed task's title, description, position, or execution status.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (tsk_...).",
                },
                "title": {"type": "string"},
                "description": {"type": "string"},
                "position": {"type": "integer"},
                "status": {
                    "type": "string",
                    "enum": [s.value for s in TaskStatus],
                },
            },
            "required": ["seed_id", "task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_seed_task",
        "annotations": {
            "title": "Delete Seed Task",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Delete a task from a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Task ID (tsk_...).",
                },
            },
            "required": ["seed_id", "task_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_seed_comments",
        "annotations": {
            "title": "List Seed Comments",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List comments and agent steering messages on a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_seed_comment",
        "annotations": {
            "title": "Create Seed Comment",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": False,
        },
        "description": (
            "Post a comment on a seed. Including '@agent', '@ideator', '@planner', '@coder', or '@reviewer' "
            "steers any currently running agent on the seed or queues a new run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "body": {
                    "type": "string",
                    "description": "Markdown comment body (include @agent to steer active runs).",
                },
            },
            "required": ["seed_id", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update_seed_comment",
        "annotations": {
            "title": "Update Seed Comment",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Update an existing comment on a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "comment_id": {
                    "type": "string",
                    "description": "Comment ID (cmt_...).",
                },
                "body": {
                    "type": "string",
                    "description": "Updated markdown body.",
                },
            },
            "required": ["seed_id", "comment_id", "body"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_seed_comment",
        "annotations": {
            "title": "Delete Seed Comment",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Delete a comment from a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "comment_id": {
                    "type": "string",
                    "description": "Comment ID (cmt_...).",
                },
            },
            "required": ["seed_id", "comment_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_runs",
        "annotations": {
            "title": "List Runs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "List agent runs filtered by seed, project, agent, task, stage, status, or active-only."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Filter by seed ID (sed_...).",
                },
                "project_id": {
                    "type": "string",
                    "description": "Filter by project ID (prj_...).",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Filter by agent ID (agt_...).",
                },
                "task_id": {
                    "type": "string",
                    "description": "Filter by seed task ID (tsk_...).",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                },
                "status": {
                    "type": "string",
                    "enum": [s.value for s in RunStatus],
                },
                "active_only": {
                    "type": "boolean",
                    "description": "Only return queued, starting, or running runs.",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_run",
        "annotations": {
            "title": "Get Run",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get full execution details, logs, tool calls, token usage, and cost for an agent run."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID (run_...).",
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_run",
        "annotations": {
            "title": "Create Run",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Queue and trigger a new agent run on a seed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "seed_id": {
                    "type": "string",
                    "description": "Seed ID (sed_...) or slug#number reference.",
                },
                "task_id": {
                    "type": "string",
                    "description": "Optional specific seed task ID (tsk_...) to execute.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": (
                        "Optional stage override (defaults to the seed's current stage)."
                    ),
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID (agt_...) override.",
                },
                "compute_backend_id": {
                    "type": "string",
                    "description": "Optional compute backend ID (bnd_...) override.",
                },
            },
            "required": ["seed_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "cancel_run",
        "annotations": {
            "title": "Cancel Run",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Cancel a queued or running agent execution.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID (run_...).",
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_run_logs",
        "annotations": {
            "title": "Get Run Logs",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get live or persisted execution logs for an agent run.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {
                    "type": "string",
                    "description": "Run ID (run_...).",
                },
                "tail": {
                    "type": "integer",
                    "description": "Maximum number of log lines to return (50-5000).",
                    "default": 4000,
                },
            },
            "required": ["run_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_agents",
        "annotations": {
            "title": "List Agents",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List configured AI agents in GitSeed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "kind": {
                    "type": "string",
                    "enum": [k.value for k in AgentKind],
                    "description": "Filter by agent kind ('stage' or 'assistant').",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Filter by enabled state.",
                },
                "include_archived": {
                    "type": "boolean",
                    "default": False,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_agent",
        "annotations": {
            "title": "Get Agent",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get configuration, prompt, model, and skills for a specific agent.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "Agent ID (agt_...) or slug.",
                },
            },
            "required": ["agent_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_agent_benchmarks",
        "annotations": {
            "title": "Get Agent Benchmarks",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "Get empirical agent and model benchmark leaderboards across SDLC stages."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Optional project ID (prj_...) or slug filter.",
                },
                "stage": {
                    "type": "string",
                    "enum": [s.value for s in SeedStage],
                    "description": "Optional SDLC stage filter.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_coordinator",
        "annotations": {
            "title": "Ask Coordinator",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": (
            "Chat with the GitSeed @coordinator agent with live project and seed context. "
            "Provide either a single 'prompt' string or a 'messages' conversation array."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "User question or command for @coordinator.",
                },
                "messages": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "role": {
                                "type": "string",
                                "enum": ["user", "assistant"],
                            },
                            "content": {"type": "string"},
                        },
                        "required": ["role", "content"],
                    },
                    "description": "Optional multi-turn message history.",
                },
                "project_id": {
                    "type": "string",
                    "description": "Optional project ID (prj_...) or slug for context.",
                },
                "seed_id": {
                    "type": "string",
                    "description": "Optional seed ID (sed_...) for context.",
                },
                "mode": {
                    "type": "string",
                    "enum": ["general", "brainstorm"],
                    "default": "general",
                    "description": "Chat mode ('general' or 'brainstorm').",
                },
                "propose_seeds": {
                    "type": "boolean",
                    "default": False,
                    "description": "Ask @coordinator to return structured proposed seed drafts.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_skills",
        "annotations": {
            "title": "List Skills",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List reusable agent skills configured in GitSeed.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "include_archived": {
                    "type": "boolean",
                    "default": False,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_skill",
        "annotations": {
            "title": "Get Skill",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get details and markdown instructions for a specific skill.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "skill_id": {
                    "type": "string",
                    "description": "Skill ID (skl_...) or slug.",
                },
            },
            "required": ["skill_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_backends",
        "annotations": {
            "title": "List Backends",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "List configured infrastructure backends (git, inference, compute, tools)."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "type": {
                    "type": "string",
                    "enum": [b.value for b in BackendType],
                    "description": "Filter by backend type ('git', 'inference', 'compute', 'tools').",
                },
                "enabled": {
                    "type": "boolean",
                    "description": "Filter by enabled state.",
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_backend",
        "annotations": {
            "title": "Get Backend",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get details, capabilities, and health for a specific backend.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "backend_id": {
                    "type": "string",
                    "description": "Backend ID (bnd_...).",
                },
            },
            "required": ["backend_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_activity",
        "annotations": {
            "title": "List Activity",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List recent platform activity and audit log events.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {"type": "string", "description": "Filter by project ID."},
                "seed_id": {"type": "string", "description": "Filter by seed ID."},
                "actor_type": {
                    "type": "string",
                    "description": "Filter by actor type (e.g. 'user', 'agent', 'system').",
                },
                "actor_id": {"type": "string", "description": "Filter by actor ID."},
                "event_type": {
                    "type": "string",
                    "description": "Filter by event type (e.g. 'seed.created', 'run.completed').",
                },
                "before_id": {
                    "type": ["integer", "string"],
                    "description": "Cursor pagination: return events older than this activity ID.",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "get_sandbox_config",
        "annotations": {
            "title": "Get Sandbox Config",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get default sandbox container configuration for a project repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "repo_id": {
                    "type": ["integer", "string"],
                    "description": "Optional numeric repository ID.",
                },
                "ref": {
                    "type": "string",
                    "description": "Optional git ref (branch/tag/SHA).",
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_sandboxes",
        "annotations": {
            "title": "List Sandboxes",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List ephemeral execution sandboxes for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "limit": {
                    "type": "integer",
                    "default": 50,
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_sandbox",
        "annotations": {
            "title": "Create Sandbox",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Launch an isolated ephemeral sandbox container for a project repository.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "repository_id": {
                    "type": ["integer", "string"],
                    "description": "Optional numeric repository ID.",
                },
                "seed_id": {
                    "type": "string",
                    "description": (
                        "Optional seed ID (sed_...) to checkout the seed's worktree branch."
                    ),
                },
                "ref": {
                    "type": "string",
                    "description": "Optional git ref override.",
                },
                "image": {
                    "type": "string",
                    "description": "Optional container image override.",
                },
                "setup_command": {
                    "type": "string",
                    "description": "Optional setup command run before main command.",
                },
                "command": {
                    "type": "string",
                    "description": "Command to execute inside the sandbox (required for 'job').",
                },
                "artifact_globs": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Optional file globs to collect as artifacts.",
                },
                "initial_stdin": {
                    "type": "string",
                    "description": "Optional initial stdin payload.",
                },
                "kind": {
                    "type": "string",
                    "enum": ["job", "terminal"],
                    "default": "job",
                    "description": "Sandbox mode ('job' for command execution, 'terminal' for interactive shell).",
                },
                "title": {
                    "type": "string",
                    "description": "Optional display title for the sandbox session.",
                },
                "as_root": {
                    "type": "boolean",
                    "default": False,
                    "description": "Run the sandbox container as root.",
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_sandbox",
        "annotations": {
            "title": "Get Sandbox",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get status, stdout/stderr logs, exit code, and artifacts for a sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "sandbox_id": {
                    "type": "string",
                    "description": "Sandbox ID (sbx_...).",
                },
            },
            "required": ["project_id", "sandbox_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_sandbox_input",
        "annotations": {
            "title": "Send Sandbox Input",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Send stdin input or EOF to a running sandbox process.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "sandbox_id": {
                    "type": "string",
                    "description": "Sandbox ID (sbx_...).",
                },
                "input": {
                    "type": "string",
                    "description": "Text to write to sandbox stdin.",
                    "default": "",
                },
                "append_newline": {
                    "type": "boolean",
                    "default": True,
                },
                "eof": {
                    "type": "boolean",
                    "default": False,
                },
            },
            "required": ["project_id", "sandbox_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stop_sandbox",
        "annotations": {
            "title": "Stop Sandbox",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Stop and terminate a running sandbox.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "sandbox_id": {
                    "type": "string",
                    "description": "Sandbox ID (sbx_...).",
                },
            },
            "required": ["project_id", "sandbox_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "list_sessions",
        "annotations": {
            "title": "List Sessions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": (
            "List interactive sessions (coding agent sessions and terminal sandboxes) "
            "across accessible projects."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "scope": {
                    "type": "string",
                    "enum": ["mine", "everyone"],
                    "default": "everyone",
                },
                "state": {
                    "type": "string",
                    "enum": ["running", "ended", "all"],
                    "default": "all",
                },
                "project_id": {
                    "type": "string",
                    "description": "Optional project ID (prj_...) or slug filter.",
                },
                "limit": {
                    "type": "integer",
                    "default": 100,
                },
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "list_agent_sessions",
        "annotations": {
            "title": "List Agent Sessions",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "List interactive coding agent sessions for a project.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "saved_only": {
                    "type": "boolean",
                    "default": False,
                },
                "limit": {
                    "type": "integer",
                    "default": 30,
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "get_agent_session",
        "annotations": {
            "title": "Get Agent Session",
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Get details, message history, and live turn logs for an agent session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Agent session ID (ses_...).",
                },
            },
            "required": ["project_id", "session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "create_agent_session",
        "annotations": {
            "title": "Create Agent Session",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Start a new interactive coding agent session in a project workspace.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional agent ID (agt_...) or @handle.",
                },
                "runtime": {
                    "type": "string",
                    "enum": [r.value for r in AgentRuntime],
                    "description": "Optional runtime override.",
                },
                "inference_backend_id": {
                    "type": "string",
                    "description": "Optional inference backend ID (bnd_...).",
                },
                "model": {
                    "type": "string",
                    "description": "Optional model identifier override.",
                },
                "repository_id": {
                    "type": ["integer", "string"],
                    "description": "Optional numeric repository ID.",
                },
                "seed_id": {
                    "type": "string",
                    "description": "Optional seed ID (sed_...) to attach workspace to.",
                },
                "ref": {
                    "type": "string",
                    "description": "Optional git branch, tag, or SHA.",
                },
                "title": {
                    "type": "string",
                    "description": "Optional session title.",
                },
                "is_saved": {
                    "type": "boolean",
                    "default": False,
                },
                "initial_message": {
                    "type": "string",
                    "description": "Optional initial message/prompt to send immediately.",
                },
                "propose_seeds": {
                    "type": "boolean",
                    "default": False,
                },
            },
            "required": ["project_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "send_agent_session_message",
        "annotations": {
            "title": "Send Agent Session Message",
            "readOnlyHint": False,
            "destructiveHint": False,
            "idempotentHint": False,
            "openWorldHint": True,
        },
        "description": "Send a follow-up message or instruction to an interactive agent session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Agent session ID (ses_...).",
                },
                "content": {
                    "type": "string",
                    "description": "Message content for the coding agent.",
                },
                "propose_seeds": {
                    "type": "boolean",
                    "default": False,
                },
            },
            "required": ["project_id", "session_id", "content"],
            "additionalProperties": False,
        },
    },
    {
        "name": "stop_agent_session",
        "annotations": {
            "title": "Stop Agent Session",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Stop an active coding agent session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Agent session ID (ses_...).",
                },
            },
            "required": ["project_id", "session_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete_agent_session",
        "annotations": {
            "title": "Delete Agent Session",
            "readOnlyHint": False,
            "destructiveHint": True,
            "idempotentHint": True,
            "openWorldHint": False,
        },
        "description": "Delete an interactive coding agent session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "string",
                    "description": "Project ID (prj_...) or slug.",
                },
                "session_id": {
                    "type": "string",
                    "description": "Agent session ID (ses_...).",
                },
            },
            "required": ["project_id", "session_id"],
            "additionalProperties": False,
        },
    },
]

TOOLS = TOOLS_CATALOG

TOOLSETS: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            "get_system_status",
            "ask_coordinator",
            "list_activity",
        }
    ),
    "projects": frozenset(
        {
            "list_projects",
            "get_project",
            "create_project",
            "update_project",
            "list_project_repositories",
            "get_project_policies",
            "list_repository_branches",
            "get_repository_tree",
            "get_repository_blob",
        }
    ),
    "seeds": frozenset(
        {
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
            "get_seed_receipt",
            "list_seed_approvals",
            "submit_seed_approval",
        }
    ),
    "tasks": frozenset(
        {
            "list_seed_tasks",
            "create_seed_task",
            "update_seed_task",
            "delete_seed_task",
            "list_seed_comments",
            "create_seed_comment",
            "update_seed_comment",
            "delete_seed_comment",
        }
    ),
    "runs": frozenset(
        {
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
        }
    ),
    "sessions": frozenset(
        {
            "list_sessions",
            "list_agent_sessions",
            "get_agent_session",
            "create_agent_session",
            "send_agent_session_message",
            "stop_agent_session",
            "delete_agent_session",
        }
    ),
    "governance": frozenset(
        {
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
        }
    ),
}

TOOLSET_BY_TOOL: dict[str, str] = {
    tool_name: toolset_name
    for toolset_name, members in TOOLSETS.items()
    for tool_name in members
}

RESOURCES_CATALOG: list[dict[str, Any]] = [
    {
        "uri": "gitseed://status",
        "name": "GitSeed Platform Status",
        "description": "Control Plane health, version, and current authenticated user identity.",
        "mimeType": "application/json",
    },
    {
        "uri": "gitseed://projects",
        "name": "Visible Projects",
        "description": "All non-archived GitSeed projects accessible to the caller.",
        "mimeType": "application/json",
    },
    {
        "uri": "gitseed://seeds",
        "name": "Open Seeds",
        "description": "All open seeds across visible projects.",
        "mimeType": "application/json",
    },
    {
        "uri": "gitseed://runs/active",
        "name": "Active Agent Runs",
        "description": "Currently queued, running, or awaiting_approval agent runs.",
        "mimeType": "application/json",
    },
    {
        "uri": "gitseed://activity",
        "name": "Recent Activity Feed",
        "description": "Recent audit and lifecycle events across the workspace.",
        "mimeType": "application/json",
    },
]

RESOURCE_TEMPLATES_CATALOG: list[dict[str, Any]] = [
    {
        "uriTemplate": "gitseed://projects/{project_id}",
        "name": "Project Detail & Policies",
        "description": "Full project metadata, attached repositories, and SDLC stage policies.",
        "mimeType": "application/json",
    },
    {
        "uriTemplate": "gitseed://seeds/{seed_id}",
        "name": "Seed Workspace Context",
        "description": (
            "Comprehensive seed snapshot including specification, plan, tasks, approvals, "
            "comments, and recent agent runs."
        ),
        "mimeType": "application/json",
    },
]

PROMPTS_CATALOG: list[dict[str, Any]] = [
    {
        "name": "triage-workspace",
        "description": (
            "Inspect open seeds and active runs to identify items awaiting human approval, "
            "blocked on spend checkpoints, or failed."
        ),
        "arguments": [
            {
                "name": "project_id",
                "description": "Optional project ID or slug to scope the triage.",
                "required": False,
            }
        ],
    },
    {
        "name": "review-seed",
        "description": (
            "Review a seed's specification, task progress, code changes/diff, and latest agent "
            "runs to prepare a stage gate approval decision."
        ),
        "arguments": [
            {
                "name": "seed_id",
                "description": "Seed ID (sed_...) or slug#number reference to review.",
                "required": True,
            }
        ],
    },
    {
        "name": "plant-seed",
        "description": (
            "Draft and plant a well-structured seed with specification and verifiable "
            "acceptance criteria."
        ),
        "arguments": [
            {
                "name": "project_id",
                "description": "Target project ID (prj_...) or slug.",
                "required": True,
            },
            {
                "name": "goal",
                "description": "High-level feature request, bug report, or objective.",
                "required": True,
            },
        ],
    },
    {
        "name": "steer-run",
        "description": (
            "Inspect an active seed run and craft an @agent comment to steer "
            "the agent's implementation."
        ),
        "arguments": [
            {
                "name": "seed_id",
                "description": "Seed ID (sed_...) or slug#number reference.",
                "required": True,
            },
            {
                "name": "guidance",
                "description": "Steering instructions for the running agent.",
                "required": True,
            },
        ],
    },
]
