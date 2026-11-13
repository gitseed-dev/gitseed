from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import unquote

import httpx
from gitseed_common.enums import SeedAutonomy

from gitseed_mcp.catalog import (
    PROMPTS_CATALOG,
    RESOURCE_TEMPLATES_CATALOG,
    RESOURCES_CATALOG,
    TOOLS,
    TOOLS_CATALOG,
    TOOLSET_BY_TOOL,
    TOOLSETS,
)
from gitseed_mcp.client import GitSeedApiClient, GitSeedApiError
from gitseed_mcp.config import MCPSettings, get_settings

__all__ = [
    "DEFAULT_PROTOCOL_VERSION",
    "JSONRPC_INTERNAL_ERROR",
    "JSONRPC_INVALID_PARAMS",
    "JSONRPC_INVALID_REQUEST",
    "JSONRPC_METHOD_NOT_FOUND",
    "JSONRPC_PARSE_ERROR",
    "PROMPTS_CATALOG",
    "RESOURCES_CATALOG",
    "RESOURCE_TEMPLATES_CATALOG",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "TOOLS",
    "TOOLS_CATALOG",
    "TOOLSETS",
    "TOOLSET_BY_TOOL",
    "TOOL_HANDLERS",
    "GitSeedMCPServer",
    "ToolHandler",
]

SUPPORTED_PROTOCOL_VERSIONS: tuple[str, ...] = (
    "2025-03-26",
    "2024-11-05",
)
DEFAULT_PROTOCOL_VERSION = "2024-11-05"

JSONRPC_PARSE_ERROR = -32700
JSONRPC_INVALID_REQUEST = -32600
JSONRPC_METHOD_NOT_FOUND = -32601
JSONRPC_INVALID_PARAMS = -32602
JSONRPC_INTERNAL_ERROR = -32603

ToolHandler = Callable[[GitSeedApiClient, dict[str, Any]], Awaitable[Any]]


def _require_str(args: dict[str, Any], key: str) -> str:
    val = args.get(key)
    if not isinstance(val, str) or not val.strip():
        raise ValueError(f"Missing or empty required string parameter '{key}'")
    return val.strip()


def _optional_str(args: dict[str, Any], key: str) -> str | None:
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, str):
        return str(val)
    stripped = val.strip()
    return stripped if stripped else None


def _optional_bool(args: dict[str, Any], key: str, default: bool | None = None) -> bool | None:
    val = args.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        return val.strip().lower() in ("1", "true", "yes")
    return bool(val)


def _optional_int(args: dict[str, Any], key: str, default: int | None = None) -> int | None:
    val = args.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        raise ValueError(f"Parameter '{key}' must be an integer, got boolean")
    try:
        return int(val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Parameter '{key}' must be an integer") from exc


def _optional_float(
    args: dict[str, Any], key: str, default: float | None = None
) -> float | None:
    val = args.get(key)
    if val is None:
        return default
    if isinstance(val, bool):
        raise ValueError(f"Parameter '{key}' must be a number, got boolean")
    try:
        return float(val)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Parameter '{key}' must be a number") from exc


def _optional_str_list(args: dict[str, Any], key: str) -> list[str] | None:
    val = args.get(key)
    if val is None:
        return None
    if not isinstance(val, list):
        raise ValueError(f"Parameter '{key}' must be a list of strings")
    return [str(item) for item in val]


def _require_id_or_int(args: dict[str, Any], key: str) -> int | str:
    val = args.get(key)
    if val is None or isinstance(val, bool):
        raise ValueError(f"Missing required parameter '{key}'")
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            raise ValueError(f"Missing or empty required parameter '{key}'")
        if stripped.isdigit():
            return int(stripped)
        return stripped
    raise ValueError(f"Invalid parameter '{key}'")


def _optional_id_or_int(args: dict[str, Any], key: str) -> int | str | None:
    val = args.get(key)
    if val is None:
        return None
    if isinstance(val, bool):
        raise ValueError(f"Parameter '{key}' must be an integer or string, got boolean")
    if isinstance(val, int):
        return val
    if isinstance(val, str):
        stripped = val.strip()
        if not stripped:
            return None
        if stripped.isdigit():
            return int(stripped)
        return stripped
    return str(val)


# ── Tool Implementations ──────────────────────────────────────────────


async def _tool_get_system_status(
    client: GitSeedApiClient, _args: dict[str, Any]
) -> dict[str, Any]:
    async def _fetch_version() -> Any:
        try:
            return await client.get_version()
        except GitSeedApiError:
            return None

    async def _fetch_user() -> tuple[Any, str | None]:
        if not client.token:
            return None, None
        try:
            return await client.get_current_user(), None
        except GitSeedApiError as exc:
            return None, exc.message

    health, version, (user, auth_error) = await asyncio.gather(
        client.get_health(),
        _fetch_version(),
        _fetch_user(),
    )

    return {
        "api_base_url": client.base_url,
        "authenticated": user is not None,
        "health": health,
        "version": version,
        "user": user,
        "auth_error": auth_error,
    }


async def _tool_list_projects(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_projects(
        include_archived=bool(_optional_bool(args, "include_archived", False)),
        tag=_optional_str(args, "tag"),
    )


async def _tool_get_project(
    client: GitSeedApiClient, args: dict[str, Any]
) -> dict[str, Any]:
    project_id = _require_str(args, "project_id")
    inc_repos = bool(_optional_bool(args, "include_repositories", True))
    inc_policies = bool(_optional_bool(args, "include_policies", False))

    coros: list[Awaitable[Any]] = [client.get_project(project_id)]
    if inc_repos:
        coros.append(client.list_project_repositories(project_id))
    if inc_policies:
        coros.append(client.get_project_policies(project_id))

    results = await asyncio.gather(*coros)
    result: dict[str, Any] = {"project": results[0]}
    idx = 1
    if inc_repos:
        result["repositories"] = results[idx]
        idx += 1
    if inc_policies:
        result["policies"] = results[idx]
    return result


async def _tool_create_project(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_project(
        name=_require_str(args, "name"),
        slug=_optional_str(args, "slug"),
        description=_optional_str(args, "description"),
        tags=_optional_str_list(args, "tags"),
        visibility=_optional_str(args, "visibility") or "private",
        monthly_budget=_optional_float(args, "monthly_budget"),
        seed_budget_cap=_optional_float(args, "seed_budget_cap"),
        spend_checkpoint=_optional_float(args, "spend_checkpoint"),
        max_concurrent_runs=_optional_int(args, "max_concurrent_runs"),
    )


async def _tool_update_project(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.update_project(
        _require_str(args, "project_id"),
        name=_optional_str(args, "name"),
        description=_optional_str(args, "description"),
        tags=_optional_str_list(args, "tags"),
        visibility=_optional_str(args, "visibility"),
        monthly_budget=_optional_float(args, "monthly_budget"),
        seed_budget_cap=_optional_float(args, "seed_budget_cap"),
        spend_checkpoint=_optional_float(args, "spend_checkpoint"),
        max_concurrent_runs=_optional_int(args, "max_concurrent_runs"),
    )


async def _tool_get_project_policies(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_project_policies(_require_str(args, "project_id"))


async def _tool_list_org_policies(
    client: GitSeedApiClient, _args: dict[str, Any]
) -> Any:
    return await client.list_org_policies()


async def _resolve_agent_id(
    client: GitSeedApiClient, agent_id: str | None
) -> str | None:
    if not agent_id:
        return None
    if agent_id.startswith("@") or not agent_id.startswith("agt_"):
        agent = await client.get_agent(agent_id.lstrip("@"))
        if isinstance(agent, dict) and agent.get("id"):
            return str(agent["id"])
    return agent_id


async def _tool_update_org_policy(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    agent_id = await _resolve_agent_id(client, _optional_str(args, "agent_id"))
    return await client.update_org_policy(
        _require_str(args, "stage"),
        enabled=_optional_bool(args, "enabled"),
        agent_id=agent_id,
        requires_approval=_optional_bool(args, "requires_approval"),
        min_approvers=_optional_int(args, "min_approvers"),
        min_approver_role=_optional_str(args, "min_approver_role"),
        allow_auto_approve=_optional_bool(args, "allow_auto_approve"),
        allow_self_approval=_optional_bool(args, "allow_self_approval"),
        max_attempts=_optional_int(args, "max_attempts"),
        timeout_minutes=_optional_int(args, "timeout_minutes"),
        min_acceptance_criteria=_optional_int(args, "min_acceptance_criteria"),
        max_acceptance_criteria=_optional_int(args, "max_acceptance_criteria"),
        max_tasks=_optional_int(args, "max_tasks"),
        instructions=_optional_str(args, "instructions"),
    )


async def _tool_update_project_policy(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    agent_id = await _resolve_agent_id(client, _optional_str(args, "agent_id"))
    return await client.update_project_policy(
        _require_str(args, "project_id"),
        _require_str(args, "stage"),
        enabled=_optional_bool(args, "enabled"),
        agent_id=agent_id,
        requires_approval=_optional_bool(args, "requires_approval"),
        min_approvers=_optional_int(args, "min_approvers"),
        min_approver_role=_optional_str(args, "min_approver_role"),
        allow_auto_approve=_optional_bool(args, "allow_auto_approve"),
        allow_self_approval=_optional_bool(args, "allow_self_approval"),
        max_attempts=_optional_int(args, "max_attempts"),
        timeout_minutes=_optional_int(args, "timeout_minutes"),
        min_acceptance_criteria=_optional_int(args, "min_acceptance_criteria"),
        max_acceptance_criteria=_optional_int(args, "max_acceptance_criteria"),
        max_tasks=_optional_int(args, "max_tasks"),
        instructions=_optional_str(args, "instructions"),
    )


_tool_set_org_policy = _tool_update_org_policy
_tool_set_project_policy = _tool_update_project_policy


async def _tool_reset_project_policy(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.reset_project_policy(
        _require_str(args, "project_id"),
        _require_str(args, "stage"),
    )


async def _tool_list_project_repositories(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_project_repositories(_require_str(args, "project_id"))


async def _tool_list_repository_branches(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_repository_branches(
        _require_str(args, "project_id"),
        _require_id_or_int(args, "repo_id"),
    )


async def _tool_get_repository_tree(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_repository_tree(
        _require_str(args, "project_id"),
        _require_id_or_int(args, "repo_id"),
        ref=_optional_str(args, "ref"),
        path=_optional_str(args, "path") or "",
    )


async def _tool_get_repository_blob(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_repository_blob(
        _require_str(args, "project_id"),
        _require_id_or_int(args, "repo_id"),
        path=_require_str(args, "path"),
        ref=_optional_str(args, "ref"),
    )


async def _tool_list_seeds(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_seeds(
        project_id=_optional_str(args, "project_id"),
        stage=_optional_str(args, "stage"),
        status=_optional_str(args, "status"),
        open_only=bool(_optional_bool(args, "open_only", False)),
        autonomy=_optional_str(args, "autonomy"),
        tag=_optional_str(args, "tag"),
        limit=_optional_int(args, "limit", 100) or 100,
    )


async def _tool_get_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_seed(_require_str(args, "seed_id"))


async def _tool_create_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_seed(
        project_id=_require_str(args, "project_id"),
        title=_require_str(args, "title"),
        description=_optional_str(args, "description"),
        specification=_optional_str(args, "specification"),
        acceptance_criteria=_optional_str_list(args, "acceptance_criteria"),
        plan=_optional_str(args, "plan"),
        tags=_optional_str_list(args, "tags"),
        stage=_optional_str(args, "stage"),
        fast_track=_optional_bool(args, "fast_track"),
        staged=_optional_bool(args, "staged"),
        autonomy=_optional_str(args, "autonomy") or SeedAutonomy.SUPERVISED.value,
        budget_cap=_optional_float(args, "budget_cap"),
    )


async def _tool_update_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.update_seed(
        _require_str(args, "seed_id"),
        title=_optional_str(args, "title"),
        description=_optional_str(args, "description"),
        specification=_optional_str(args, "specification"),
        acceptance_criteria=_optional_str_list(args, "acceptance_criteria"),
        plan=_optional_str(args, "plan"),
        tags=_optional_str_list(args, "tags"),
        autonomy=_optional_str(args, "autonomy"),
        budget_cap=_optional_float(args, "budget_cap"),
    )


async def _tool_start_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.start_seed(_require_str(args, "seed_id"))


async def _tool_advance_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.advance_seed(
        _require_str(args, "seed_id"),
        stage=_optional_str(args, "stage"),
        fast_track=_optional_bool(args, "fast_track"),
    )


async def _tool_move_seed_back(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.move_seed_back(
        _require_str(args, "seed_id"),
        stage=_require_str(args, "stage"),
        reason=_require_str(args, "reason"),
    )


async def _tool_stop_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.stop_seed(_require_str(args, "seed_id"))


async def _tool_resume_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.resume_seed(_require_str(args, "seed_id"))


async def _tool_approve_seed_checkpoint(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.approve_seed_checkpoint(
        _require_str(args, "seed_id"),
        approved_through=_optional_float(args, "approved_through"),
    )


async def _tool_close_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.close_seed(_require_str(args, "seed_id"))


async def _tool_reopen_seed(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.reopen_seed(_require_str(args, "seed_id"))


async def _tool_get_seed_changes(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_seed_changes(
        _require_str(args, "seed_id"),
        repo_id=_optional_id_or_int(args, "repo_id"),
    )


async def _tool_get_seed_receipt(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_seed_receipt(_require_str(args, "seed_id"))


async def _tool_list_seed_approvals(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_seed_approvals(_require_str(args, "seed_id"))


async def _tool_submit_seed_approval(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.submit_seed_approval(
        _require_str(args, "seed_id"),
        decision=_require_str(args, "decision"),
        comment=_optional_str(args, "comment"),
    )


async def _tool_list_seed_tasks(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_seed_tasks(_require_str(args, "seed_id"))


async def _tool_create_seed_task(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_seed_task(
        _require_str(args, "seed_id"),
        title=_require_str(args, "title"),
        description=_optional_str(args, "description"),
        position=_optional_int(args, "position"),
    )


async def _tool_update_seed_task(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.update_seed_task(
        _require_str(args, "seed_id"),
        _require_str(args, "task_id"),
        title=_optional_str(args, "title"),
        description=_optional_str(args, "description"),
        position=_optional_int(args, "position"),
        status=_optional_str(args, "status"),
    )


async def _tool_delete_seed_task(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.delete_seed_task(
        _require_str(args, "seed_id"),
        _require_str(args, "task_id"),
    )


async def _tool_list_seed_comments(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_seed_comments(_require_str(args, "seed_id"))


async def _tool_create_seed_comment(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_seed_comment(
        _require_str(args, "seed_id"),
        body=_require_str(args, "body"),
    )


async def _tool_update_seed_comment(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.update_seed_comment(
        _require_str(args, "seed_id"),
        _require_str(args, "comment_id"),
        body=_require_str(args, "body"),
    )


async def _tool_delete_seed_comment(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.delete_seed_comment(
        _require_str(args, "seed_id"),
        _require_str(args, "comment_id"),
    )


async def _tool_list_runs(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_runs(
        seed_id=_optional_str(args, "seed_id"),
        project_id=_optional_str(args, "project_id"),
        agent_id=_optional_str(args, "agent_id"),
        task_id=_optional_str(args, "task_id"),
        stage=_optional_str(args, "stage"),
        status=_optional_str(args, "status"),
        active_only=bool(_optional_bool(args, "active_only", False)),
        limit=_optional_int(args, "limit", 100) or 100,
    )


async def _tool_get_run(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_run(_require_str(args, "run_id"))


async def _tool_create_run(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_run(
        seed_id=_require_str(args, "seed_id"),
        task_id=_optional_str(args, "task_id"),
        stage=_optional_str(args, "stage"),
        agent_id=_optional_str(args, "agent_id"),
        compute_backend_id=_optional_str(args, "compute_backend_id"),
    )


async def _tool_cancel_run(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.cancel_run(_require_str(args, "run_id"))


async def _tool_get_run_logs(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_run_logs(
        _require_str(args, "run_id"),
        tail=_optional_int(args, "tail", 4000) or 4000,
    )


async def _tool_list_agents(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_agents(
        kind=_optional_str(args, "kind"),
        enabled=_optional_bool(args, "enabled"),
        include_archived=bool(_optional_bool(args, "include_archived", False)),
    )


async def _tool_get_agent(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_agent(_require_str(args, "agent_id"))


async def _tool_get_agent_benchmarks(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_agent_benchmarks(
        project_id=_optional_str(args, "project_id"),
        stage=_optional_str(args, "stage"),
    )


async def _tool_ask_coordinator(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    raw_messages = args.get("messages")
    messages: list[dict[str, str]] = []
    if isinstance(raw_messages, list) and raw_messages:
        for item in raw_messages:
            if not isinstance(item, dict):
                raise ValueError("Each entry in 'messages' must be an object with role and content")
            role = str(item.get("role", "user")).strip() or "user"
            if role not in ("user", "assistant"):
                raise ValueError("Each message 'role' must be 'user' or 'assistant'")
            content = str(item.get("content", "")).strip()
            if not content:
                raise ValueError("Each message in 'messages' must have non-empty content")
            messages.append({"role": role, "content": content})
    else:
        prompt = _require_str(args, "prompt")
        messages = [{"role": "user", "content": prompt}]

    return await client.ask_coordinator(
        messages=messages,
        project_id=_optional_str(args, "project_id"),
        seed_id=_optional_str(args, "seed_id"),
        mode=_optional_str(args, "mode") or "general",
        propose_seeds=bool(_optional_bool(args, "propose_seeds", False)),
    )


async def _tool_list_skills(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_skills(
        include_archived=bool(_optional_bool(args, "include_archived", False)),
    )


async def _tool_get_skill(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_skill(_require_str(args, "skill_id"))


async def _tool_list_backends(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_backends(
        backend_type=_optional_str(args, "type"),
        enabled=_optional_bool(args, "enabled"),
    )


async def _tool_get_backend(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_backend(_require_str(args, "backend_id"))


async def _tool_list_activity(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_activity(
        project_id=_optional_str(args, "project_id"),
        seed_id=_optional_str(args, "seed_id"),
        actor_type=_optional_str(args, "actor_type"),
        actor_id=_optional_str(args, "actor_id"),
        event_type=_optional_str(args, "event_type"),
        before_id=_optional_id_or_int(args, "before_id"),
        limit=_optional_int(args, "limit", 50) or 50,
    )


async def _tool_get_sandbox_config(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_sandbox_config(
        _require_str(args, "project_id"),
        repo_id=_optional_id_or_int(args, "repo_id"),
        ref=_optional_str(args, "ref"),
    )


async def _tool_list_sandboxes(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_sandboxes(
        _require_str(args, "project_id"),
        limit=_optional_int(args, "limit", 50) or 50,
    )


async def _tool_create_sandbox(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_sandbox(
        _require_str(args, "project_id"),
        repository_id=_optional_id_or_int(args, "repository_id"),
        seed_id=_optional_str(args, "seed_id"),
        ref=_optional_str(args, "ref"),
        image=_optional_str(args, "image"),
        setup_command=_optional_str(args, "setup_command"),
        command=_optional_str(args, "command"),
        artifact_globs=_optional_str_list(args, "artifact_globs"),
        initial_stdin=_optional_str(args, "initial_stdin"),
        kind=_optional_str(args, "kind"),
        title=_optional_str(args, "title"),
        as_root=_optional_bool(args, "as_root"),
    )


async def _tool_get_sandbox(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_sandbox(
        _require_str(args, "project_id"),
        _require_str(args, "sandbox_id"),
    )


async def _tool_send_sandbox_input(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    input_val = args.get("input", "")
    input_str = str(input_val) if input_val is not None else ""
    return await client.send_sandbox_input(
        _require_str(args, "project_id"),
        _require_str(args, "sandbox_id"),
        input_text=input_str,
        append_newline=bool(_optional_bool(args, "append_newline", True)),
        eof=bool(_optional_bool(args, "eof", False)),
    )


async def _tool_stop_sandbox(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.stop_sandbox(
        _require_str(args, "project_id"),
        _require_str(args, "sandbox_id"),
    )


async def _tool_list_sessions(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_sessions(
        scope=_optional_str(args, "scope") or "everyone",
        state=_optional_str(args, "state") or "all",
        project_id=_optional_str(args, "project_id"),
        limit=_optional_int(args, "limit", 100) or 100,
    )


async def _tool_list_agent_sessions(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.list_agent_sessions(
        _require_str(args, "project_id"),
        saved_only=bool(_optional_bool(args, "saved_only", False)),
        limit=_optional_int(args, "limit", 30) or 30,
    )


async def _tool_get_agent_session(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.get_agent_session(
        _require_str(args, "project_id"),
        _require_str(args, "session_id"),
    )


async def _tool_create_agent_session(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.create_agent_session(
        _require_str(args, "project_id"),
        agent_id=_optional_str(args, "agent_id"),
        runtime=_optional_str(args, "runtime"),
        inference_backend_id=_optional_str(args, "inference_backend_id"),
        model=_optional_str(args, "model"),
        repository_id=_optional_id_or_int(args, "repository_id"),
        seed_id=_optional_str(args, "seed_id"),
        ref=_optional_str(args, "ref"),
        title=_optional_str(args, "title"),
        is_saved=bool(_optional_bool(args, "is_saved", False)),
        initial_message=_optional_str(args, "initial_message")
        or _optional_str(args, "initial_prompt"),
        propose_seeds=bool(_optional_bool(args, "propose_seeds", False)),
    )


async def _tool_send_agent_session_message(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.send_agent_session_message(
        _require_str(args, "project_id"),
        _require_str(args, "session_id"),
        content=_require_str(args, "content"),
        propose_seeds=bool(_optional_bool(args, "propose_seeds", False)),
    )


async def _tool_stop_agent_session(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.stop_agent_session(
        _require_str(args, "project_id"),
        _require_str(args, "session_id"),
    )


async def _tool_delete_agent_session(
    client: GitSeedApiClient, args: dict[str, Any]
) -> Any:
    return await client.delete_agent_session(
        _require_str(args, "project_id"),
        _require_str(args, "session_id"),
    )


TOOL_HANDLERS: dict[str, ToolHandler] = {
    "get_system_status": _tool_get_system_status,
    "list_projects": _tool_list_projects,
    "get_project": _tool_get_project,
    "create_project": _tool_create_project,
    "update_project": _tool_update_project,
    "get_project_policies": _tool_get_project_policies,
    "list_org_policies": _tool_list_org_policies,
    "update_org_policy": _tool_update_org_policy,
    "update_project_policy": _tool_update_project_policy,
    "reset_project_policy": _tool_reset_project_policy,
    "list_project_repositories": _tool_list_project_repositories,
    "list_repository_branches": _tool_list_repository_branches,
    "get_repository_tree": _tool_get_repository_tree,
    "get_repository_blob": _tool_get_repository_blob,
    "list_seeds": _tool_list_seeds,
    "get_seed": _tool_get_seed,
    "create_seed": _tool_create_seed,
    "update_seed": _tool_update_seed,
    "start_seed": _tool_start_seed,
    "advance_seed": _tool_advance_seed,
    "move_seed_back": _tool_move_seed_back,
    "stop_seed": _tool_stop_seed,
    "resume_seed": _tool_resume_seed,
    "approve_seed_checkpoint": _tool_approve_seed_checkpoint,
    "close_seed": _tool_close_seed,
    "reopen_seed": _tool_reopen_seed,
    "get_seed_changes": _tool_get_seed_changes,
    "get_seed_receipt": _tool_get_seed_receipt,
    "list_seed_approvals": _tool_list_seed_approvals,
    "submit_seed_approval": _tool_submit_seed_approval,
    "list_seed_tasks": _tool_list_seed_tasks,
    "create_seed_task": _tool_create_seed_task,
    "update_seed_task": _tool_update_seed_task,
    "delete_seed_task": _tool_delete_seed_task,
    "list_seed_comments": _tool_list_seed_comments,
    "create_seed_comment": _tool_create_seed_comment,
    "update_seed_comment": _tool_update_seed_comment,
    "delete_seed_comment": _tool_delete_seed_comment,
    "list_runs": _tool_list_runs,
    "get_run": _tool_get_run,
    "create_run": _tool_create_run,
    "cancel_run": _tool_cancel_run,
    "get_run_logs": _tool_get_run_logs,
    "list_agents": _tool_list_agents,
    "get_agent": _tool_get_agent,
    "get_agent_benchmarks": _tool_get_agent_benchmarks,
    "ask_coordinator": _tool_ask_coordinator,
    "list_skills": _tool_list_skills,
    "get_skill": _tool_get_skill,
    "list_backends": _tool_list_backends,
    "get_backend": _tool_get_backend,
    "list_activity": _tool_list_activity,
    "get_sandbox_config": _tool_get_sandbox_config,
    "list_sandboxes": _tool_list_sandboxes,
    "create_sandbox": _tool_create_sandbox,
    "get_sandbox": _tool_get_sandbox,
    "send_sandbox_input": _tool_send_sandbox_input,
    "stop_sandbox": _tool_stop_sandbox,
    "list_sessions": _tool_list_sessions,
    "list_agent_sessions": _tool_list_agent_sessions,
    "get_agent_session": _tool_get_agent_session,
    "create_agent_session": _tool_create_agent_session,
    "send_agent_session_message": _tool_send_agent_session_message,
    "stop_agent_session": _tool_stop_agent_session,
    "delete_agent_session": _tool_delete_agent_session,
}


_TOOLS_BY_NAME: dict[str, dict[str, Any]] = {
    tool["name"]: tool for tool in TOOLS_CATALOG
}


# ── MCP Protocol Server Engine ────────────────────────────────────────


class GitSeedMCPServer:
    """Stateless JSON-RPC 2.0 handler implementing the Model Context Protocol for GitSeed."""

    def __init__(
        self,
        settings: MCPSettings | None = None,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._http_transport = http_transport
        self._http_client: httpx.AsyncClient | None = None

    def is_tool_enabled(self, tool_name: str) -> bool:
        """Return True if tool_name passes read-only and toolset filters."""
        descriptor = _TOOLS_BY_NAME.get(tool_name)
        if descriptor is None:
            return False
        if self.settings.read_only and not bool(
            descriptor.get("annotations", {}).get("readOnlyHint")
        ):
            return False
        enabled_sets = self.settings.enabled_toolsets
        if enabled_sets:
            toolset = TOOLSET_BY_TOOL.get(tool_name)
            if toolset not in enabled_sets and tool_name.lower() not in enabled_sets:
                return False
        return True

    def visible_tools(self) -> list[dict[str, Any]]:
        """Return tool descriptors permitted by active read-only and toolset settings."""
        return [
            tool for tool in TOOLS_CATALOG if self.is_tool_enabled(tool["name"])
        ]

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=self.settings.GITSEED_MCP_TIMEOUT_SECONDS,
                transport=self._http_transport,
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
        self._http_client = None

    def create_client(
        self,
        request_token: str | None = None,
        *,
        allow_env_fallback: bool = True,
    ) -> GitSeedApiClient:
        resolved_token = self.settings.resolve_token(
            request_token,
            allow_env_fallback=allow_env_fallback,
        )
        return GitSeedApiClient(
            base_url=self.settings.api_base_url,
            token=resolved_token,
            timeout=self.settings.GITSEED_MCP_TIMEOUT_SECONDS,
            transport=self._http_transport,
            http_client=self._get_http_client(),
        )

    async def handle_payload(
        self,
        payload: Any,
        *,
        request_token: str | None = None,
        allow_env_fallback: bool = True,
    ) -> dict[str, Any] | list[dict[str, Any]] | None:
        """Process a decoded JSON-RPC 2.0 message or batch and return response(s)."""
        if isinstance(payload, list):
            if not payload:
                return self._error_response(
                    None, JSONRPC_INVALID_REQUEST, "Empty JSON-RPC batch"
                )
            responses: list[dict[str, Any]] = []
            for item in payload:
                resp = await self._handle_single(
                    item,
                    request_token=request_token,
                    allow_env_fallback=allow_env_fallback,
                )
                if resp is not None:
                    responses.append(resp)
            return responses if responses else None

        return await self._handle_single(
            payload,
            request_token=request_token,
            allow_env_fallback=allow_env_fallback,
        )

    async def _handle_single(
        self,
        message: Any,
        *,
        request_token: str | None = None,
        allow_env_fallback: bool = True,
    ) -> dict[str, Any] | None:
        if not isinstance(message, dict):
            return self._error_response(
                None, JSONRPC_INVALID_REQUEST, "JSON-RPC message must be an object"
            )

        jsonrpc = message.get("jsonrpc")
        method = message.get("method")
        has_id = "id" in message
        req_id = message.get("id")

        if jsonrpc != "2.0" or not isinstance(method, str) or not method:
            return self._error_response(
                req_id if has_id else None,
                JSONRPC_INVALID_REQUEST,
                "Invalid JSON-RPC 2.0 request",
            )

        params_raw = message.get("params")
        if params_raw is None:
            params: dict[str, Any] = {}
        elif isinstance(params_raw, dict):
            params = params_raw
        else:
            if not has_id:
                return None
            return self._error_response(
                req_id, JSONRPC_INVALID_PARAMS, "JSON-RPC params must be an object"
            )

        # Notifications (no "id" field) do not produce a response
        if not has_id or method.startswith("notifications/"):
            return None

        try:
            result = await self._dispatch_method(
                method,
                params,
                request_token=request_token,
                allow_env_fallback=allow_env_fallback,
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": result,
            }
        except KeyError as exc:
            return self._error_response(
                req_id, JSONRPC_METHOD_NOT_FOUND, str(exc)
            )
        except ValueError as exc:
            return self._error_response(
                req_id, JSONRPC_INVALID_PARAMS, str(exc)
            )
        except Exception as exc:
            return self._error_response(
                req_id, JSONRPC_INTERNAL_ERROR, f"Internal server error: {exc}"
            )

    async def _dispatch_method(
        self,
        method: str,
        params: dict[str, Any],
        *,
        request_token: str | None = None,
        allow_env_fallback: bool = True,
    ) -> dict[str, Any]:
        if method == "initialize":
            requested_version = str(
                params.get("protocolVersion", DEFAULT_PROTOCOL_VERSION)
            )
            negotiated_version = (
                requested_version
                if requested_version in SUPPORTED_PROTOCOL_VERSIONS
                else SUPPORTED_PROTOCOL_VERSIONS[0]
            )
            return {
                "protocolVersion": negotiated_version,
                "capabilities": {
                    "tools": {"listChanged": False},
                    "resources": {"subscribe": False, "listChanged": False},
                    "prompts": {"listChanged": False},
                    "logging": {},
                },
                "serverInfo": {
                    "name": "gitseed-mcp",
                    "version": self.settings.GITSEED_VERSION,
                },
                "instructions": (
                    "GitSeed MCP Server exposing AI-native software delivery projects, "
                    "seeds (SDLC stages: ideate -> plan -> execute -> review -> merge), "
                    "tasks, stage-gate approvals, @agent steering comments, runs, @coordinator "
                    "chat, and ephemeral repository sandboxes."
                ),
            }

        if method in ("ping", "logging/setLevel"):
            return {}

        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": tool["name"],
                        "description": tool["description"],
                        "inputSchema": tool["inputSchema"],
                        "annotations": dict(tool["annotations"]),
                    }
                    for tool in self.visible_tools()
                ]
            }

        if method == "tools/call":
            return await self._handle_tool_call(
                params,
                request_token=request_token,
                allow_env_fallback=allow_env_fallback,
            )

        if method == "resources/list":
            return {"resources": RESOURCES_CATALOG}

        if method == "resources/templates/list":
            return {"resourceTemplates": RESOURCE_TEMPLATES_CATALOG}

        if method == "resources/read":
            return await self._handle_resource_read(
                params,
                request_token=request_token,
                allow_env_fallback=allow_env_fallback,
            )

        if method == "prompts/list":
            return {"prompts": PROMPTS_CATALOG}

        if method == "prompts/get":
            return await self._handle_prompt_get(params, request_token=request_token)

        raise KeyError(f"Method not found: {method}")

    async def _handle_tool_call(
        self,
        params: dict[str, Any],
        *,
        request_token: str | None = None,
        allow_env_fallback: bool = True,
    ) -> dict[str, Any]:
        tool_name = params.get("name")
        if not isinstance(tool_name, str) or not tool_name.strip():
            raise ValueError("Missing required tool 'name'")

        handler = TOOL_HANDLERS.get(tool_name)
        if handler is None:
            raise ValueError(f"Unknown tool: {tool_name}")

        raw_args = params.get("arguments")
        if raw_args is None:
            arguments: dict[str, Any] = {}
        elif isinstance(raw_args, dict):
            arguments = raw_args
        else:
            raise ValueError("Tool 'arguments' must be an object")

        client = self.create_client(
            request_token=request_token,
            allow_env_fallback=allow_env_fallback,
        )
        try:
            if not self.is_tool_enabled(tool_name):
                raise ValueError(
                    f"Tool '{tool_name}' is disabled by server configuration"
                )
            data = await handler(client, arguments)
            formatted = json.dumps(data, indent=2, sort_keys=False, default=str)
            return {
                "content": [
                    {
                        "type": "text",
                        "text": formatted,
                    }
                ],
                "isError": False,
            }
        except (GitSeedApiError, ValueError) as exc:
            error_payload: dict[str, Any] = {"error": str(exc)}
            if isinstance(exc, GitSeedApiError) and exc.status_code is not None:
                error_payload["status_code"] = exc.status_code
            return {
                "content": [
                    {
                        "type": "text",
                        "text": json.dumps(error_payload, indent=2),
                    }
                ],
                "isError": True,
            }

    async def _handle_resource_read(
        self,
        params: dict[str, Any],
        *,
        request_token: str | None = None,
        allow_env_fallback: bool = True,
    ) -> dict[str, Any]:
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri.strip():
            raise ValueError("Missing required resource 'uri'")
        uri = uri.strip()

        client = self.create_client(
            request_token=request_token,
            allow_env_fallback=allow_env_fallback,
        )
        try:
            data = await self._read_resource_uri(client, uri)
        except GitSeedApiError as exc:
            data = {
                "error": exc.message,
                "status_code": exc.status_code,
                "uri": uri,
            }

        return {
            "contents": [
                {
                    "uri": uri,
                    "mimeType": "application/json",
                    "text": json.dumps(data, indent=2, default=str),
                }
            ]
        }

    async def _read_resource_uri(self, client: GitSeedApiClient, uri: str) -> Any:
        if uri == "gitseed://status":
            return await _tool_get_system_status(client, {})
        if uri == "gitseed://projects":
            return await client.list_projects(include_archived=False)
        if uri == "gitseed://seeds":
            return await client.list_seeds(open_only=True, limit=100)
        if uri == "gitseed://runs/active":
            return await client.list_runs(active_only=True, limit=100)
        if uri == "gitseed://activity":
            return await client.list_activity(limit=50)

        if uri.startswith("gitseed://projects/"):
            project_id = unquote(uri.removeprefix("gitseed://projects/")).strip("/")
            if not project_id:
                raise ValueError(f"Invalid project resource URI: {uri}")
            project, repos, policies = await asyncio.gather(
                client.get_project(project_id),
                client.list_project_repositories(project_id),
                client.get_project_policies(project_id),
            )
            return {
                "project": project,
                "repositories": repos,
                "policies": policies,
            }

        if uri.startswith("gitseed://seeds/"):
            seed_id = unquote(uri.removeprefix("gitseed://seeds/")).strip("/")
            if not seed_id:
                raise ValueError(f"Invalid seed resource URI: {uri}")
            seed = await client.get_seed(seed_id)
            canonical_id = (
                str(seed.get("id", seed_id)) if isinstance(seed, dict) else seed_id
            )
            tasks, approvals, comments, runs = await asyncio.gather(
                client.list_seed_tasks(canonical_id),
                client.list_seed_approvals(canonical_id),
                client.list_seed_comments(canonical_id),
                client.list_runs(seed_id=canonical_id, limit=25),
            )
            return {
                "seed": seed,
                "tasks": tasks,
                "approvals": approvals,
                "comments": comments,
                "runs": runs,
            }

        raise ValueError(f"Unsupported resource URI: {uri}")

    async def _handle_prompt_get(
        self,
        params: dict[str, Any],
        *,
        request_token: str | None = None,
    ) -> dict[str, Any]:
        name = params.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError("Missing required prompt 'name'")
        raw_args = params.get("arguments")
        args: dict[str, Any] = raw_args if isinstance(raw_args, dict) else {}

        if name == "triage-workspace":
            project_id = _optional_str(args, "project_id")
            scope = f"project '{project_id}'" if project_id else "all visible projects"
            text = (
                f"Triage the GitSeed workspace for {scope}:\n"
                "1. Call `list_seeds` with `open_only=true`"
                + (f" and `project_id='{project_id}'`" if project_id else "")
                + ".\n"
                "2. Highlight seeds with status `awaiting_approval`, `blocked`, or `failed`.\n"
                "3. Call `list_runs` with `active_only=true` to check live agent progress.\n"
                "4. Provide a concise prioritized action list for the operator."
            )
            return {
                "description": f"Triage open seeds and active runs for {scope}.",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": text},
                    }
                ],
            }

        if name == "review-seed":
            seed_id = _require_str(args, "seed_id")
            text = (
                f"Perform a comprehensive stage-gate review of seed `{seed_id}`:\n"
                f"1. Call `get_seed` (`seed_id='{seed_id}'`) to read its specification, "
                "acceptance criteria, and plan.\n"
                f"2. Call `list_seed_tasks` and `get_seed_changes` (`seed_id='{seed_id}'`) "
                "to inspect completed tasks and code diffs.\n"
                f"3. Call `list_runs` (`seed_id='{seed_id}'`) to check the latest "
                "coder/reviewer run outcomes.\n"
                "4. Summarize whether all acceptance criteria are met and recommend an "
                "approval decision (`approved`, `changes_requested`, or `rejected`) "
                "via `submit_seed_approval`."
            )
            return {
                "description": f"Review seed {seed_id} for stage gate approval.",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": text},
                    }
                ],
            }

        if name == "plant-seed":
            project_id = _require_str(args, "project_id")
            goal = _require_str(args, "goal")
            text = (
                f"Plant a well-structured seed in project `{project_id}` "
                "for the following goal:\n\n"
                f"{goal}\n\n"
                "Steps:\n"
                f"1. Inspect the project (`get_project` with `project_id='{project_id}'`) "
                "to understand its repositories and conventions.\n"
                "2. Draft a clear `title`, `description`, `specification`, and verifiable "
                "`acceptance_criteria` list.\n"
                f"3. Call `create_seed` on `project_id='{project_id}'`."
            )
            return {
                "description": f"Plant a new seed in project {project_id}.",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": text},
                    }
                ],
            }

        if name == "steer-run":
            seed_id = _require_str(args, "seed_id")
            guidance = _require_str(args, "guidance")
            text = (
                f"Steer the active agent working on seed `{seed_id}`:\n"
                f"1. Call `get_seed` (`seed_id='{seed_id}'`) and `list_runs` "
                f"(`seed_id='{seed_id}', active_only=true`) to check current stage and agent.\n"
                f"2. Post a steering comment using `create_seed_comment` on `{seed_id}` "
                f"with `@agent {guidance}`."
            )
            return {
                "description": f"Steer active agent on seed {seed_id}.",
                "messages": [
                    {
                        "role": "user",
                        "content": {"type": "text", "text": text},
                    }
                ],
            }

        raise ValueError(f"Unknown prompt: {name}")

    @staticmethod
    def _error_response(
        req_id: Any,
        code: int,
        message: str,
        data: Any = None,
    ) -> dict[str, Any]:
        err: dict[str, Any] = {"code": code, "message": message}
        if data is not None:
            err["data"] = data
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": err,
        }
