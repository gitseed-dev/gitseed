from __future__ import annotations

import inspect
import json
import logging
import os
import re
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from gitseed_common.enums import (
    RUN_ACTIVE_STATUSES,
    ActivityType,
    ActorType,
    OrgRole,
    ProjectRole,
    RunStatus,
    SeedAutonomy,
    SeedStage,
    SeedStatus,
    TaskStatus,
    UserStatus,
)
from gitseed_common.schemas.runner_output import check_stage_policy_bounds
from pydantic import ValidationError
from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.config import settings
from gitseed_core.models.activity import Activity
from gitseed_core.models.agent import Agent
from gitseed_core.models.approval import Approval
from gitseed_core.models.backend import Backend
from gitseed_core.models.project import (
    Project,
    ProjectMember,
)
from gitseed_core.models.run import Run
from gitseed_core.models.seed import Seed, SeedComment, SeedTask
from gitseed_core.models.seed_attachment import SeedAttachment
from gitseed_core.models.skill import Skill
from gitseed_core.models.user import User
from gitseed_core.schemas.runner import (
    ExecuteStageOutput,
    IdeateStageOutput,
    MergeStageOutput,
    PlanStageOutput,
    ReviewStageOutput,
    RunnerApprovalFeedbackItem,
    RunnerAttachmentItem,
    RunnerCliAuthRefreshRequest,
    RunnerCliAuthRefreshResponse,
    RunnerCommentItem,
    RunnerCompleteRequest,
    RunnerContextResponse,
    RunnerFinalizeContextResponse,
    RunnerGatewayConfig,
    RunnerPrepareCompleteRequest,
    RunnerPrepareCompleteResponse,
    RunnerRelatedSeedItem,
    RunnerSkillItem,
    RunnerSteeringResponse,
    RunnerTaskItem,
)
from gitseed_core.security import (
    decrypt_secrets,
    encrypt_secrets,
    verify_run_token,
    verify_scoped_run_token,
)
from gitseed_core.services import events as events_service
from gitseed_core.services.activity import record
from gitseed_core.services.cli_auth import (
    _NON_JSON_CLI_AUTH_PATHS,
    validate_and_filter_cli_auth_bundle,
)
from gitseed_core.services.compute_runtime import stop_runner_agent_phase
from gitseed_core.services.errors import Conflict, Forbidden, Invalid, NotFound, Unauthorized
from gitseed_core.services.gateway import (
    _compute_remaining_budget,
    create_run_virtual_key,
    estimate_model_token_cost,
    finalize_run_litellm_key,
    merge_run_context_window,
    resolve_models_backend_for_run,
    revoke_run_virtual_key,
)
from gitseed_core.services.git_merge import (
    _build_git_urls,
    _find_plan_target_repositories,
    _merge_seed_branch_via_cli,
    _resolve_runner_mcp_servers,
    _resolve_runner_repositories,
    ensure_seed_pull_request,
    is_auto_create_pr_enabled,
    issue_git_push_token,
    merge_seed_branch_into_default,
)
from gitseed_core.services.policies import get_effective_stage_policy
from gitseed_core.services.runs import advance_seed_after_succeeded_run
from gitseed_core.services.seed_attachments import attachment_path, list_attachments_for_seed
from gitseed_core.services.seed_links import list_runner_related_seeds
from gitseed_core.services.seeds import (
    MULTI_OPINION_STAGES,
    extract_agent_mentions,
    list_stage_cycle_runs,
    reset_orphaned_running_tasks,
)

logger = logging.getLogger("uvicorn.error")

__all__ = [
    "_build_git_urls",
    "_find_plan_target_repositories",
    "_merge_seed_branch_via_cli",
    "complete_runner_execution",
    "complete_runner_prepare",
    "get_runner_context",
    "get_runner_finalize_context",
    "get_trusted_run_result",
    "issue_git_push_token",
    "merge_seed_branch_into_default",
    "poll_runner_steering",
    "refresh_runner_cli_auth",
    "refresh_runner_cli_auth_bundle",
    "write_agent_context_file",
]

MAX_CLI_AUTH_BUNDLE_BYTES = 65_536

_NON_SECRET_OAUTH_LITERALS: frozenset[str] = frozenset(
    {
        "authorized_user",
        "oauth-personal",
        "service_account",
        "application/json",
        "external_account",
        "cli_oauth",
    }
)

_PRESERVED_RUN_OUTPUT_KEYS: tuple[str, ...] = (
    "auth_mode",
    "cost_enforcement",
    "budget_enforcement",
    "enforcement",
    "enforcement_detail",
    "models_backend_id",
    "gateway_model",
    "webhook_triggered",
    "actor_user_id",
    "_actor_user_id",
)

_DISALLOWED_CLI_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "hooks",
        "mcpservers",
        "apikeyhelper",
        "awsauthrefresh",
        "awscredentialexport",
        "env",
        "command",
        "args",
        "scripts",
        "shell",
        "permissions",
        "allowedtools",
    }
)

_ALLOWED_FLAT_CREDENTIAL_KEYS: frozenset[str] = frozenset(
    {
        "access_token",
        "refresh_token",
        "id_token",
        "token_type",
        "scope",
        "scopes",
        "expiry_date",
        "expires_at",
        "expires_in",
        "account_id",
        "client_id",
        "oauth_token",
        "accesstoken",
        "refreshtoken",
        "expiresat",
        "subscriptiontype",
        "ratelimittier",
        "email",
        "project_id",
    }
)

_ALLOWED_CREDENTIAL_WRAPPER_KEYS: frozenset[str] = frozenset(
    {
        "claudeaioauth",
        "oauthaccount",
        "mcpoauth",
        "tokens",
        "security",
        "auth",
        "projects",
        "openai",
        "anthropic",
        "google",
        "gemini",
        "antigravity",
        "xai",
        "openrouter",
        "github",
        "github-copilot",
        "copilot",
        "groq",
        "mistral",
        "deepseek",
    }
)

_SAFE_CREDENTIAL_FIELD_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")


def _extract_cli_auth_secrets(obj: Any) -> list[str]:
    """Recursively extract non-empty secret/token strings (>8 chars) from `cli_auth_bundle`."""
    collected: list[str] = []
    seen: set[str] = set()

    def _visit(node: Any, key_hint: str = "") -> None:
        if key_hint.lower() == "provider":
            return
        if isinstance(node, dict):
            for k, v in node.items():
                _visit(v, str(k))
            return
        if isinstance(node, list | tuple | set):
            for item in node:
                _visit(item, key_hint)
            return
        if not isinstance(node, str):
            return
        stripped = node.strip()
        if not stripped:
            return
        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                parsed = json.loads(stripped)
            except Exception:
                parsed = None
            if isinstance(parsed, dict | list):
                _visit(parsed, key_hint)
                return
        if (
            len(stripped) > 8
            and not stripped.startswith(("http://", "https://"))
            and stripped.lower() not in _NON_SECRET_OAUTH_LITERALS
            and stripped not in seen
        ):
            seen.add(stripped)
            collected.append(stripped)

    _visit(obj)
    return collected


def write_agent_context_file(target_path: Path | str, context_payload: dict[str, Any]) -> Path:
    """Write `agent-context.json` and restrict file permissions to `0o600`."""
    path_obj = Path(target_path)
    if path_obj.exists() and path_obj.is_dir():
        path_obj = path_obj / "agent-context.json"
    path_obj.parent.mkdir(parents=True, exist_ok=True)
    path_obj.write_text(json.dumps(context_payload, indent=2), encoding="utf-8")
    os.chmod(path_obj, 0o600)
    return path_obj


def _is_primitive_or_str_list(val: Any) -> bool:
    if val is None or isinstance(val, str | int | float | bool):
        return True
    if isinstance(val, list) and all(isinstance(item, str) for item in val):
        return True
    return False


def _validate_credential_json_fields(
    source_name: str,
    obj: dict[str, Any],
    *,
    depth: int = 0,
    allow_dynamic_children: bool = False,
) -> None:
    if depth > 2:
        raise Invalid(f"Credential payload '{source_name}' contains unexpected nested objects")
    for raw_k, val in obj.items():
        if not isinstance(raw_k, str) or not _SAFE_CREDENTIAL_FIELD_RE.match(raw_k):
            raise Invalid(f"Invalid credential field '{raw_k}' in '{source_name}'")
        lower_k = raw_k.lower()
        if lower_k in _DISALLOWED_CLI_CREDENTIAL_KEYS:
            raise Invalid(f"Disallowed credential field '{raw_k}' in '{source_name}'")
        if _is_primitive_or_str_list(val):
            continue
        if isinstance(val, dict) and depth < 2:
            if lower_k in _ALLOWED_CREDENTIAL_WRAPPER_KEYS:
                _validate_credential_json_fields(
                    f"{source_name}.{raw_k}",
                    val,
                    depth=depth + 1,
                    allow_dynamic_children=lower_k in ("mcpoauth", "projects"),
                )
                continue
            if allow_dynamic_children and depth == 1:
                _validate_credential_json_fields(
                    f"{source_name}.{raw_k}",
                    val,
                    depth=depth + 1,
                    allow_dynamic_children=False,
                )
                continue
        raise Invalid(
            f"Credential field '{raw_k}' in '{source_name}' must be a string or primitive value"
        )


def _validate_and_merge_refreshed_cli_auth_bundle(
    raw_bundle: Any,
    *,
    runtime: str,
    existing_raw_json: str | None,
    backend_provider: str | None,
) -> tuple[dict[str, Any], list[str]]:
    """Enforce <=65,536-byte cap, dict type, provider preservation, and primitive credential fields."""
    if not isinstance(raw_bundle, dict):
        raise Invalid("cli_auth_bundle must be a JSON object")
    try:
        serialized_in = json.dumps(raw_bundle)
    except (TypeError, ValueError) as exc:
        raise Invalid("cli_auth_bundle must be JSON-serializable") from exc
    if len(serialized_in.encode("utf-8")) > MAX_CLI_AUTH_BUNDLE_BYTES:
        raise Invalid(
            f"cli_auth_bundle exceeds maximum size of {MAX_CLI_AUTH_BUNDLE_BYTES} bytes"
        )

    prev_b: dict[str, Any] = {}
    if existing_raw_json:
        try:
            loaded_prev = json.loads(existing_raw_json)
            if isinstance(loaded_prev, dict):
                prev_b = loaded_prev
        except Exception:
            prev_b = {}

    prev_provider_raw = (
        prev_b["provider"].strip()
        if isinstance(prev_b.get("provider"), str) and prev_b["provider"].strip()
        else None
    )
    expected_provider = (prev_provider_raw or (backend_provider or "")).strip()

    if "provider" in raw_bundle:
        inc_prov = raw_bundle["provider"]
        if not isinstance(inc_prov, str) or not inc_prov.strip():
            raise Invalid("cli_auth_bundle 'provider' must be a non-empty string")
        if expected_provider and inc_prov.strip().lower() != expected_provider.lower():
            raise Invalid(
                f"cli_auth_bundle provider '{inc_prov}' does not match existing provider '{expected_provider}'"
            )

    flat_updates: dict[str, Any] = {}
    path_candidates: dict[str, Any] = {}
    for k, v in raw_bundle.items():
        if not isinstance(k, str):
            raise Invalid("cli_auth_bundle keys must be strings")
        if k == "provider":
            continue
        lower_k = k.strip().lower()
        if lower_k in _DISALLOWED_CLI_CREDENTIAL_KEYS:
            raise Invalid(f"Disallowed credential field '{k}' in cli_auth_bundle")
        if lower_k in _ALLOWED_FLAT_CREDENTIAL_KEYS:
            if not _is_primitive_or_str_list(v):
                raise Invalid(
                    f"Credential field '{k}' in cli_auth_bundle must be a string or primitive value"
                )
            flat_updates[k] = v
        else:
            path_candidates[k] = v

    validated_paths: dict[str, str] = {}
    if path_candidates:
        validated_paths = validate_and_filter_cli_auth_bundle(
            path_candidates,
            runtime=runtime,
            strict=True,
        )
        for norm_path, content in validated_paths.items():
            if norm_path not in _NON_JSON_CLI_AUTH_PATHS:
                parsed_doc = json.loads(content)
                if not isinstance(parsed_doc, dict):
                    raise Invalid(f"Credential file '{norm_path}' must contain a JSON object")
                _validate_credential_json_fields(norm_path, parsed_doc)

    merged_bundle: dict[str, Any] = {}
    if prev_b:
        merged_bundle.update(
            validate_and_filter_cli_auth_bundle(
                prev_b,
                provider=backend_provider,
                strict=False,
            )
        )
        for pk, pv in prev_b.items():
            if (
                isinstance(pk, str)
                and pk.strip().lower() in _ALLOWED_FLAT_CREDENTIAL_KEYS
                and _is_primitive_or_str_list(pv)
            ):
                merged_bundle[pk] = pv

    if prev_provider_raw is not None:
        merged_bundle["provider"] = prev_provider_raw
    elif "provider" in raw_bundle and isinstance(raw_bundle["provider"], str):
        merged_bundle["provider"] = raw_bundle["provider"].strip()

    merged_bundle.update(flat_updates)
    merged_bundle.update(validated_paths)

    if len(json.dumps(merged_bundle).encode("utf-8")) > MAX_CLI_AUTH_BUNDLE_BYTES:
        raise Invalid(
            f"cli_auth_bundle exceeds maximum size of {MAX_CLI_AUTH_BUNDLE_BYTES} bytes"
        )

    updated_keys = sorted([*validated_paths.keys(), *flat_updates.keys()])
    return merged_bundle, updated_keys


def _default_seed_branch(project_slug: str | None, seed_number: int) -> str:
    slug = (project_slug or "").strip().strip("/")
    if slug:
        return f"gitseed/{slug}/seed-{seed_number}"
    return f"gitseed/seed-{seed_number}"


def _get_broker_state(run: Run) -> dict[str, Any]:
    if isinstance(run.output, dict) and isinstance(run.output.get("_broker"), dict):
        return dict(run.output["_broker"])
    return {}


def _set_broker_state(run: Run, broker_state: dict[str, Any]) -> None:
    base = dict(run.output) if isinstance(run.output, dict) else {}
    base["_broker"] = broker_state
    run.output = base


def _normalize_trusted_result(
    *,
    run: Run,
    seed: Seed,
    project: Project,
    raw_trusted: dict[str, Any] | None,
    broker_state: dict[str, Any],
    now: datetime,
) -> dict[str, Any]:
    t = dict(raw_trusted) if isinstance(raw_trusted, dict) else {}
    default_branch = str(
        broker_state.get("seed_branch")
        or _default_seed_branch(project.slug, seed.number)
    )
    stored_base_heads = (
        dict(broker_state["base_heads"])
        if isinstance(broker_state.get("base_heads"), dict)
        else (
            {str(k): str(v) for k, v in t["base_heads"].items() if k and v}
            if isinstance(t.get("base_heads"), dict)
            else {}
        )
    )
    commit_sha = (
        str(t["commit_sha"]).strip()
        if isinstance(t.get("commit_sha"), str) and t["commit_sha"].strip()
        else None
    )
    branch = (
        str(t["branch"]).strip()
        if isinstance(t.get("branch"), str) and t["branch"].strip()
        else default_branch
    )
    pushed = bool(t.get("pushed", False))
    violations = (
        [str(p) for p in t["blocked_paths_violations"] if p]
        if isinstance(t.get("blocked_paths_violations"), list)
        else []
    )
    raw_findings = (
        t["secret_scan_findings"]
        if isinstance(t.get("secret_scan_findings"), list)
        else []
    )
    sanitized_findings: list[dict[str, Any]] = []
    for item in raw_findings:
        if isinstance(item, dict):
            sanitized_findings.append(
                {
                    "path": str(item.get("path") or "<unknown>"),
                    "line": int(item.get("line") or 1),
                    "rule": str(item.get("rule") or "secret"),
                }
            )
    raw_stats = t.get("diff_stats") if isinstance(t.get("diff_stats"), dict) else {}
    diff_stats = {
        "files": int(raw_stats.get("files") or 0),
        "insertions": int(raw_stats.get("insertions") or 0),
        "deletions": int(raw_stats.get("deletions") or 0),
    }
    raw_ver_obj = t.get("verification")
    raw_ver: dict[str, Any] = dict(raw_ver_obj) if isinstance(raw_ver_obj, dict) else {}
    verification = {**raw_ver, "source": "agent_reported"}
    finalized_at = (
        str(t["finalized_at"])
        if isinstance(t.get("finalized_at"), str) and t["finalized_at"]
        else now.isoformat()
    )
    return {
        "commit_sha": commit_sha,
        "branch": branch,
        "pushed": pushed,
        "base_heads": stored_base_heads,
        "blocked_paths_violations": violations,
        "secret_scan_findings": sanitized_findings,
        "diff_stats": diff_stats,
        "verification": verification,
        "finalized_at": finalized_at,
    }


def get_trusted_run_result(run: Run) -> dict[str, Any]:
    """Return the trusted finalize broker result dict stored on `run.output['trusted']`."""
    out = run.output if isinstance(run.output, dict) else {}
    existing = out.get("trusted")
    if isinstance(existing, dict):
        ver = (
            dict(existing["verification"])
            if isinstance(existing.get("verification"), dict)
            else {}
        )
        ver["source"] = "agent_reported"
        stats = (
            dict(existing["diff_stats"])
            if isinstance(existing.get("diff_stats"), dict)
            else {}
        )
        return {
            "commit_sha": existing.get("commit_sha"),
            "branch": str(existing.get("branch") or ""),
            "pushed": bool(existing.get("pushed", False)),
            "base_heads": dict(existing.get("base_heads") or {}),
            "blocked_paths_violations": list(
                existing.get("blocked_paths_violations") or []
            ),
            "secret_scan_findings": [
                {
                    "path": str(f.get("path") or "<unknown>"),
                    "line": int(f.get("line") or 1),
                    "rule": str(f.get("rule") or "secret"),
                }
                for f in (existing.get("secret_scan_findings") or [])
                if isinstance(f, dict)
            ],
            "diff_stats": {
                "files": int(stats.get("files") or 0),
                "insertions": int(stats.get("insertions") or 0),
                "deletions": int(stats.get("deletions") or 0),
            },
            "verification": ver,
            "finalized_at": str(
                existing.get("finalized_at")
                or (run.finished_at.isoformat() if run.finished_at else "")
            ),
        }
    broker = out.get("_broker") if isinstance(out.get("_broker"), dict) else {}
    return {
        "commit_sha": None,
        "branch": str(broker.get("seed_branch") or ""),
        "pushed": False,
        "base_heads": dict(broker.get("base_heads") or {}),
        "blocked_paths_violations": [],
        "secret_scan_findings": [],
        "diff_stats": {"files": 0, "insertions": 0, "deletions": 0},
        "verification": {"source": "agent_reported"},
        "finalized_at": run.finished_at.isoformat() if run.finished_at else "",
    }


def _enforce_endpoint_token_scope(
    raw_token: str | None,
    allowed_scopes: set[str] | frozenset[str],
) -> str | None:
    """When `raw_token` is a signed `gsr_` token, verify its scope is in `allowed_scopes`."""
    if not raw_token:
        return None
    cleaned = raw_token.strip()
    if not cleaned.startswith("gsr_") or "." not in cleaned:
        return None
    scoped = verify_scoped_run_token(cleaned)
    if scoped is None:
        raise Unauthorized("Invalid or expired run token")
    _, token_scope = scoped
    if token_scope not in allowed_scopes:
        raise Forbidden(
            f"Run token scope '{token_scope}' is not authorized for this endpoint "
            f"(required: {', '.join(sorted(allowed_scopes))})"
        )
    return token_scope


async def _try_lock_run_and_seed(
    session: AsyncSession, run_id: str, seed_id: str
) -> None:
    """Acquire SELECT ... FOR UPDATE row locks on Run and Seed and verify Run is still active."""
    locked_run: Run | None = None
    try:
        res_run = await session.execute(
            select(Run)
            .where(Run.id == run_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if inspect.iscoroutine(res_run):
            res_run.close()
        else:
            cand_fn = getattr(res_run, "scalar_one_or_none", None)
            if callable(cand_fn) and not inspect.iscoroutinefunction(cand_fn):
                cand = cand_fn()
                if inspect.iscoroutine(cand):
                    cand.close()
                elif isinstance(cand, Run):
                    locked_run = cand
        res_seed = await session.execute(
            select(Seed)
            .where(Seed.id == seed_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if inspect.iscoroutine(res_seed):
            res_seed.close()
    except Exception:
        return
    if locked_run is not None and locked_run.status not in (
        RunStatus.STARTING,
        RunStatus.RUNNING,
    ):
        raise Conflict(
            f"Run '{run_id}' is no longer active after lock acquisition (status={locked_run.status})"
        )


def _extract_task_target_files(description: str) -> list[str]:
    """Extract relative file paths from a task description (e.g. `- **Files**: ...` or backticked paths)."""
    if not description:
        return []
    candidates: list[str] = []
    for line in description.splitlines():
        if "**Files**" in line or "target_files" in line.lower():
            for match in re.findall(r"`([^`\s]+\.[A-Za-z0-9_]+)`", line):
                cleaned = match.strip().removeprefix("./").lstrip("/")
                if cleaned and cleaned not in candidates:
                    candidates.append(cleaned)
    if not candidates:
        for match in re.findall(r"`([A-Za-z0-9_./-]+\.[A-Za-z0-9_]+)`", description):
            cleaned = match.strip().removeprefix("./").lstrip("/")
            if cleaned and "/" in cleaned and cleaned not in candidates:
                candidates.append(cleaned)
    return candidates


def _format_task_description_with_files(description: str, target_files: list[str]) -> str:
    desc = (description or "").strip()
    cleaned_files = [
        str(f).strip().removeprefix("./").lstrip("/")
        for f in (target_files or [])
        if str(f).strip()
    ]
    if not cleaned_files or "**Files**" in desc:
        return desc
    files_line = "- **Files**: " + ", ".join(f"`{f}`" for f in cleaned_files)
    return f"{files_line}\n{desc}" if desc else files_line


def _stage_output_schema(
    stage: SeedStage,
    *,
    min_acceptance_criteria: int | None = None,
    max_acceptance_criteria: int | None = None,
    max_tasks: int | None = None,
) -> dict[str, Any]:
    if stage == SeedStage.IDEATE:
        schema = IdeateStageOutput.model_json_schema()
        props = schema.get("properties")
        if isinstance(props, dict) and isinstance(props.get("acceptance_criteria"), dict):
            if min_acceptance_criteria is not None:
                props["acceptance_criteria"]["minItems"] = min_acceptance_criteria
            if max_acceptance_criteria is not None:
                props["acceptance_criteria"]["maxItems"] = max_acceptance_criteria
        return schema
    if stage == SeedStage.PLAN:
        schema = PlanStageOutput.model_json_schema()
        props = schema.get("properties")
        if isinstance(props, dict) and isinstance(props.get("tasks"), dict):
            if max_tasks is not None:
                props["tasks"]["maxItems"] = max_tasks
        return schema
    if stage == SeedStage.EXECUTE:
        return ExecuteStageOutput.model_json_schema()
    if stage == SeedStage.REVIEW:
        return ReviewStageOutput.model_json_schema()
    return MergeStageOutput.model_json_schema()


async def _authenticate_active_run(
    session: AsyncSession, raw_token: str | None
) -> tuple[Run, Seed, Project, Agent]:
    if not raw_token:
        raise Unauthorized("Missing run token")
    run_id = verify_run_token(raw_token)
    if not run_id:
        raise Unauthorized("Invalid or expired run token")

    run = await session.get(Run, run_id)
    if run is None or run.status not in (RunStatus.STARTING, RunStatus.RUNNING):
        raise Unauthorized(
            f"Run '{run_id}' is not active (status={run.status if run else 'missing'})"
        )
    seed = await session.get(Seed, run.seed_id)
    if seed is None:
        raise NotFound("Seed not found")
    project = await session.get(Project, seed.project_id)
    if project is None:
        raise NotFound("Project not found")
    agent = await session.get(Agent, run.agent_id)
    if agent is None:
        raise NotFound("Agent not found")
    return run, seed, project, agent


async def get_runner_context(
    session: AsyncSession,
    raw_token: str | None,
) -> RunnerContextResponse:
    """Transition the Run to 'running' and return its complete execution context (prepare scope, single-use)."""
    _enforce_endpoint_token_scope(raw_token, {"prepare"})
    run, seed, project, agent = await _authenticate_active_run(session, raw_token)
    broker_state = _get_broker_state(run)
    if broker_state.get("prepare_context_used"):
        raise Forbidden("Runner execution context has already been fetched for this run")
    now = datetime.now(UTC)

    if run.status == RunStatus.STARTING:
        run.status = RunStatus.RUNNING
        if run.started_at is None:
            run.started_at = now
        run.updated_at = now

        seed.status = SeedStatus.RUNNING
        seed.blocked_reason = None
        seed.updated_at = now

        if run.task_id is not None:
            task_row = await session.get(SeedTask, run.task_id)
            if task_row is not None and task_row.seed_id == seed.id:
                task_row.status = TaskStatus.RUNNING
                if task_row.started_at is None:
                    task_row.started_at = now
                task_row.updated_at = now

        record(
            session,
            type=ActivityType.RUN_STARTED,
            actor_type=ActorType.AGENT,
            actor_id=agent.id,
            project_id=project.id,
            seed_id=seed.id,
            data={
                "run_id": run.id,
                "stage": str(run.stage),
                "attempt": run.attempt,
            },
        )
        await session.commit()
        await session.refresh(run)
        await session.refresh(seed)

    pol = await get_effective_stage_policy(session, project.id, run.stage)
    ideate_pol = (
        pol
        if run.stage == SeedStage.IDEATE
        else await get_effective_stage_policy(session, project.id, SeedStage.IDEATE)
    )
    plan_pol = (
        pol
        if run.stage == SeedStage.PLAN
        else await get_effective_stage_policy(session, project.id, SeedStage.PLAN)
    )

    # Resolve attached skills
    skill_items: list[RunnerSkillItem] = []
    if agent.skills:
        skill_rows = (
            await session.execute(
                select(Skill).where(
                    col(Skill.archived_at).is_(None),
                    (col(Skill.id).in_(agent.skills))
                    | (col(Skill.slug).in_(agent.skills)),
                )
            )
        ).scalars().all()
        for sk in skill_rows:
            skill_items.append(
                RunnerSkillItem(
                    id=sk.id,
                    name=sk.name,
                    slug=sk.slug,
                    description=sk.description,
                    content=sk.content,
                )
            )

    # Resolve seed tasks
    task_rows = (
        await session.execute(
            select(SeedTask)
            .where(SeedTask.seed_id == seed.id)
            .order_by(col(SeedTask.position).asc())
        )
    ).scalars().all()
    task_items = [
        RunnerTaskItem(
            id=t.id,
            position=t.position,
            title=t.title,
            description=t.description,
            status=str(t.status),
            target_files=_extract_task_target_files(t.description),
        )
        for t in task_rows
    ]

    # Resolve seed comments (non-deleted)
    comment_rows = (
        await session.execute(
            select(SeedComment)
            .where(
                SeedComment.seed_id == seed.id,
                col(SeedComment.deleted_at).is_(None),
            )
            .order_by(col(SeedComment.created_at).asc())
        )
    ).scalars().all()

    user_ids = {c.author_id for c in comment_rows if c.author_type == ActorType.USER}
    agent_ids = {c.author_id for c in comment_rows if c.author_type == ActorType.AGENT}
    user_names: dict[str, str] = {}
    agent_names: dict[str, str] = {}
    if user_ids:
        for u in (
            await session.execute(select(User).where(col(User.id).in_(user_ids)))
        ).scalars().all():
            user_names[u.id] = u.preferred_name or u.name
    if agent_ids:
        for a in (
            await session.execute(select(Agent).where(col(Agent.id).in_(agent_ids)))
        ).scalars().all():
            agent_names[a.id] = f"@{a.handle}"

    comment_items = [
        RunnerCommentItem(
            id=c.id,
            author_type=str(c.author_type),
            author_name=(
                user_names.get(c.author_id, "Operator")
                if c.author_type == ActorType.USER
                else agent_names.get(c.author_id, "@agent")
            ),
            stage=c.stage,
            body=c.body,
            created_at=c.created_at or now,
        )
        for c in comment_rows
    ]

    # Resolve approval feedback (especially changes_requested)
    approval_rows = (
        await session.execute(
            select(Approval)
            .where(Approval.seed_id == seed.id)
            .order_by(col(Approval.created_at).asc())
        )
    ).scalars().all()
    approval_items = [
        RunnerApprovalFeedbackItem(
            id=ap.id,
            stage=ap.stage,
            decision=str(ap.decision),
            comment=ap.comment,
            created_at=ap.created_at or now,
        )
        for ap in approval_rows
        if ap.comment
    ]

    # Resolve repositories to clone and accessible Tools (MCP) backends
    repo_items = await _resolve_runner_repositories(session, project, seed, run)
    mcp_items = await _resolve_runner_mcp_servers(session, project, run)

    # Resolve LiteLLM Proxy config and mint per-run virtual key (sk-gsr-...)
    requested_cli_model = (run.model or "default").strip()
    models_backend, resolved_model = await resolve_models_backend_for_run(
        session, run, project
    )
    runtime_str = str(run.runtime or "opencode").strip().lower()
    native_tier_models = {"default", "pro", "flash", "flash_lite", "inherit"}
    preferred_provider_by_runtime: dict[str, set[str]] = {
        "claude-code": {"anthropic"},
        "codex": {"openai"},
        "antigravity": {"antigravity", "gemini"},
        "gemini": {"gemini", "antigravity"},
        "grok-build": {"xai"},
    }
    expected_providers = preferred_provider_by_runtime.get(runtime_str)
    backend_auth_mode = str((models_backend.config or {}).get("auth_mode") or "api_key").strip().lower()
    preserve_native_cli_model = (
        runtime_str != "opencode"
        and requested_cli_model in native_tier_models
        and (
            backend_auth_mode == "cli_oauth"
            or (
                expected_providers is not None
                and models_backend.provider.lower() not in (expected_providers | {"litellm"})
            )
        )
    )
    cli_model = requested_cli_model if preserve_native_cli_model else resolved_model

    if run.model != cli_model:
        run.model = cli_model
        run.updated_at = now
        await session.commit()
        await session.refresh(run)

    remaining_budget, _, _ = await _compute_remaining_budget(session, seed, project)
    if remaining_budget is not None and remaining_budget > Decimal("0"):
        try:
            active_sibling_raw = await session.scalar(
                select(func.count())
                .select_from(Run)
                .where(
                    Run.seed_id == seed.id,
                    col(Run.status).in_([s.value for s in RUN_ACTIVE_STATUSES]),
                )
            )
            active_siblings = (
                int(active_sibling_raw)
                if isinstance(active_sibling_raw, int) and active_sibling_raw > 1
                else 1
            )
            if active_siblings > 1:
                remaining_budget = (remaining_budget / Decimal(active_siblings)).quantize(
                    Decimal("0.0001")
                )
        except Exception:
            pass
    ttl_seconds = max(3600, pol.timeout_minutes * 60 + 600)

    models_secrets = decrypt_secrets(models_backend.secrets_encrypted)
    auth_mode = str((models_backend.config or {}).get("auth_mode") or "api_key").strip().lower()
    oauth_token = models_secrets.get("oauth_token")
    cli_auth_bundle: dict[str, str] = {}
    if models_secrets.get("cli_auth_bundle"):
        try:
            parsed_bundle = json.loads(models_secrets["cli_auth_bundle"])
            if isinstance(parsed_bundle, dict):
                cli_auth_bundle = validate_and_filter_cli_auth_bundle(
                    parsed_bundle,
                    runtime=runtime_str,
                    provider=models_backend.provider,
                    strict=False,
                )
        except Exception:
            pass

    if auth_mode != "cli_oauth" and not models_secrets.get("api_key") and (oauth_token or cli_auth_bundle):
        auth_mode = "cli_oauth"

    virtual_key = await create_run_virtual_key(
        run=run,
        seed=seed,
        project=project,
        backend=models_backend,
        resolved_model=resolved_model,
        remaining_budget=remaining_budget,
        ttl_seconds=ttl_seconds,
    )
    issued_secrets_list: list[str] = []
    if virtual_key and len(virtual_key) >= 8:
        issued_secrets_list.append(virtual_key)
    if oauth_token and len(oauth_token) >= 8 and oauth_token not in issued_secrets_list:
        issued_secrets_list.append(oauth_token)
    for bundle_secret in _extract_cli_auth_secrets(cli_auth_bundle):
        if bundle_secret not in issued_secrets_list:
            issued_secrets_list.append(bundle_secret)
    for mcp_srv in mcp_items:
        for hdr_val in (mcp_srv.headers or {}).values():
            cleaned_hdr = str(hdr_val or "").strip()
            if cleaned_hdr.lower().startswith("bearer "):
                cleaned_hdr = cleaned_hdr[7:].strip()
            if len(cleaned_hdr) >= 8 and cleaned_hdr not in issued_secrets_list:
                issued_secrets_list.append(cleaned_hdr)
        for env_val in (mcp_srv.env or {}).values():
            cleaned_env = str(env_val or "").strip()
            if len(cleaned_env) >= 8 and cleaned_env not in issued_secrets_list:
                issued_secrets_list.append(cleaned_env)
    broker_state = _get_broker_state(run)
    broker_state["prepare_context_used"] = True
    broker_state["issued_secrets"] = issued_secrets_list
    _set_broker_state(run, broker_state)
    await session.commit()

    gw_base = settings.RUNNER_GATEWAY_URL.rstrip("/")
    gw_anthropic_base = gw_base.removesuffix("/v1")

    # Resolve immutable original submission from seed.created activity log if available
    created_ev = (
        await session.execute(
            select(Activity)
            .where(
                Activity.seed_id == seed.id,
                Activity.type == ActivityType.SEED_CREATED,
            )
            .order_by(col(Activity.id).asc())
            .limit(1)
        )
    ).scalars().first()
    created_data = (created_ev.data if created_ev and isinstance(created_ev.data, dict) else {})
    orig_title = str(created_data.get("title") or "").strip() or None
    orig_desc = str(created_data.get("description") or "").strip() or None
    raw_orig_crit = created_data.get("acceptance_criteria")
    orig_criteria = (
        [str(c).strip() for c in raw_orig_crit if str(c).strip()]
        if isinstance(raw_orig_crit, list)
        else []
    )

    sanitized_sbx: dict[str, Any] = {
        k: (
            {wk: wv for wk, wv in v.items() if wk != "secret_encrypted"}
            if k == "webhook_config" and isinstance(v, dict)
            else v
        )
        for k, v in (project.sandbox_config or {}).items()
    }
    if "max_turns" not in sanitized_sbx and getattr(settings, "RUNNER_MAX_TURNS", 0) > 0:
        sanitized_sbx["max_turns"] = int(settings.RUNNER_MAX_TURNS)
    is_second_opinion = isinstance(run.output, dict) and bool(
        run.output.get("second_opinion") or run.output.get("multi_opinion")
    )
    if (
        run.stage == SeedStage.IDEATE
        and not is_second_opinion
        and sanitized_sbx.get("fast_track_ideate_to_plan") is not False
    ):
        ideate_needs_gate = (
            seed.autonomy == SeedAutonomy.MANUAL
            or (seed.autonomy == SeedAutonomy.SUPERVISED and pol.requires_approval)
            or (
                seed.autonomy == SeedAutonomy.AUTONOMOUS
                and pol.requires_approval
                and not pol.allow_auto_approve
            )
        )
        if not ideate_needs_gate:
            if plan_pol.enabled and (plan_pol.agent_id is None or plan_pol.agent_id == run.agent_id):
                sanitized_sbx["fast_track_ideate_to_plan"] = True

    return RunnerContextResponse(
        run_id=run.id,
        stage=run.stage,
        attempt=run.attempt,
        timeout_minutes=pol.timeout_minutes,
        runtime=run.runtime,
        model=cli_model,
        task_id=run.task_id,
        seed_id=seed.id,
        seed_number=seed.number,
        project_id=project.id,
        project_slug=project.slug,
        project_name=project.name,
        title=seed.title,
        description=seed.description,
        specification=seed.specification,
        acceptance_criteria=list(seed.acceptance_criteria or []),
        original_title=orig_title,
        original_description=orig_desc,
        original_acceptance_criteria=orig_criteria,
        plan=seed.plan,
        autonomy=seed.autonomy,
        agent_id=agent.id,
        agent_handle=agent.handle,
        agent_name=agent.name,
        agent_instructions=agent.instructions,
        stage_instructions=pol.combined_instructions,
        min_acceptance_criteria=ideate_pol.min_acceptance_criteria,
        max_acceptance_criteria=ideate_pol.max_acceptance_criteria,
        max_tasks=plan_pol.max_tasks,
        skills=skill_items,
        tasks=task_items,
        comments=comment_items,
        approval_feedback=approval_items,
        repositories=repo_items,
        mcp_servers=mcp_items,
        attachments=[
            RunnerAttachmentItem(id=a.id, filename=a.filename, content_type=a.content_type, size=a.size)
            for a in await list_attachments_for_seed(session, seed.id)
        ],
        related_seeds=[
            RunnerRelatedSeedItem(
                id=str(r["seed_id"]),
                number=int(r["number"]),
                title=str(r["title"]),
                status=str(r["status"]),
                stage=str(r["stage"]),
                relation=str(r["relation"]),
                branch_name=str(r["branch_name"]),
                description=r.get("description"),
                specification=r.get("specification"),
                plan=r.get("plan"),
                summary_comment=r.get("summary_comment"),
            )
            for r in await list_runner_related_seeds(session, seed.id, project.slug)
        ],
        sandbox_config=sanitized_sbx,
        gateway=RunnerGatewayConfig(
            base_url=gw_base,
            anthropic_base_url=gw_anthropic_base,
            api_key=virtual_key,
            model=resolved_model,
            provider=models_backend.provider,
            auth_mode=auth_mode,
            oauth_token=oauth_token,
            cli_auth_bundle=cli_auth_bundle,
        ),
        output_schema=_stage_output_schema(
            run.stage,
            min_acceptance_criteria=ideate_pol.min_acceptance_criteria,
            max_acceptance_criteria=ideate_pol.max_acceptance_criteria,
            max_tasks=plan_pol.max_tasks,
        ),
    )


async def resolve_runner_attachment(
    session: AsyncSession, raw_token: str | None, attachment_id: str
) -> tuple[str, str, str]:
    """(path, filename, content_type) of a seed attachment, for the run that owns the seed."""
    if not raw_token:
        raise Unauthorized("Missing run token")
    _enforce_endpoint_token_scope(raw_token, {"prepare", "agent"})
    run_id = verify_run_token(raw_token)
    if not run_id:
        raise Unauthorized("Invalid or expired run token")
    run = await session.get(Run, run_id)
    att = await session.get(SeedAttachment, attachment_id)
    if run is None or att is None or att.seed_id != run.seed_id:
        raise NotFound("Attachment not found")
    path = attachment_path(att)
    if not path.is_file():
        raise NotFound("Attachment file is missing on the server")
    return str(path), att.filename, att.content_type


async def complete_runner_prepare(
    session: AsyncSession,
    raw_token: str | None,
    payload: RunnerPrepareCompleteRequest,
) -> RunnerPrepareCompleteResponse:
    """Record trusted repository base SHAs and remote branch heads from the prepare container."""
    _enforce_endpoint_token_scope(raw_token, {"prepare"})
    run, seed, project, _ = await _authenticate_active_run(session, raw_token)
    broker_state = _get_broker_state(run)
    if broker_state.get("prepare_complete_used"):
        raise Forbidden("Prepare completion has already been recorded for this run")
    clean_base_heads = {
        str(k).strip(): str(v).strip()
        for k, v in (payload.base_heads or {}).items()
        if str(k).strip() and str(v).strip()
    }
    clean_remote_heads = {
        str(k).strip(): (str(v).strip() if v is not None else "")
        for k, v in (payload.remote_seed_heads or {}).items()
        if str(k).strip()
    }
    seed_branch = (
        payload.seed_branch.strip()
        if payload.seed_branch and payload.seed_branch.strip()
        else _default_seed_branch(project.slug, seed.number)
    )
    broker_state["prepare_complete_used"] = True
    broker_state["base_heads"] = clean_base_heads
    broker_state["remote_seed_heads"] = clean_remote_heads
    broker_state["seed_branch"] = seed_branch
    if payload.error:
        broker_state["prepare_error"] = str(payload.error)
    _set_broker_state(run, broker_state)
    out = dict(run.output or {})
    existing_trusted = (
        dict(out["trusted"]) if isinstance(out.get("trusted"), dict) else {}
    )
    existing_trusted["base_heads"] = clean_base_heads
    existing_trusted["branch"] = seed_branch
    out["trusted"] = existing_trusted
    run.output = out
    run.updated_at = datetime.now(UTC)
    await session.commit()
    return RunnerPrepareCompleteResponse(
        ok=True,
        run_id=run.id,
        base_heads=clean_base_heads,
    )


async def get_runner_finalize_context(
    session: AsyncSession,
    raw_token: str | None,
) -> RunnerFinalizeContextResponse:
    """Return trusted finalize context (base SHAs from Core, blocked_paths, repositories) for the finalize container."""
    _enforce_endpoint_token_scope(raw_token, {"finalize"})
    run, seed, project, _ = await _authenticate_active_run(session, raw_token)
    if run.compute_backend_id:
        comp_backend = await session.get(Backend, run.compute_backend_id)
        if isinstance(comp_backend, Backend):
            try:
                await stop_runner_agent_phase(comp_backend, run.id, run.container_id)
            except RuntimeError as exc:
                raise Conflict(str(exc)) from exc
    broker_state = _get_broker_state(run)
    pol = await get_effective_stage_policy(session, project.id, run.stage)
    ideate_pol = (
        pol
        if run.stage == SeedStage.IDEATE
        else await get_effective_stage_policy(session, project.id, SeedStage.IDEATE)
    )
    plan_pol = (
        pol
        if run.stage == SeedStage.PLAN
        else await get_effective_stage_policy(session, project.id, SeedStage.PLAN)
    )
    repo_items = await _resolve_runner_repositories(session, project, seed, run)
    sbx = dict(project.sandbox_config or {})
    raw_blocked = sbx.get("blocked_paths")
    blocked_paths = (
        [str(p).strip() for p in raw_blocked if str(p).strip()]
        if isinstance(raw_blocked, list)
        else []
    )
    blocked_action = str(
        sbx.get("blocked_paths_action")
        or getattr(settings, "RUNNER_BLOCKED_PATHS_ACTION", "fail")
        or "fail"
    ).lower()
    if blocked_action not in ("fail", "revert"):
        blocked_action = "fail"
    raw_max_file = sbx.get("max_file_bytes") or settings.RUNNER_MAX_FILE_BYTES
    max_file_bytes = int(raw_max_file) if isinstance(raw_max_file, (int, str)) else 5 * 1024 * 1024
    raw_max_diff = sbx.get("max_diff_bytes") or settings.RUNNER_MAX_DIFF_BYTES
    max_diff_bytes = int(raw_max_diff) if isinstance(raw_max_diff, (int, str)) else 20 * 1024 * 1024
    base_heads = (
        {str(k): str(v) for k, v in broker_state["base_heads"].items() if k and v}
        if isinstance(broker_state.get("base_heads"), dict)
        else {}
    )
    remote_seed_heads = (
        {str(k): str(v) for k, v in broker_state["remote_seed_heads"].items() if k}
        if isinstance(broker_state.get("remote_seed_heads"), dict)
        else {}
    )
    seed_branch = str(
        broker_state.get("seed_branch")
        or _default_seed_branch(project.slug, seed.number)
    )
    issued_secrets = (
        [str(s) for s in broker_state["issued_secrets"] if isinstance(s, str) and s]
        if isinstance(broker_state.get("issued_secrets"), list)
        else []
    )
    return RunnerFinalizeContextResponse(
        run_id=run.id,
        seed_id=seed.id,
        seed_number=seed.number,
        project_id=project.id,
        project_slug=project.slug,
        stage=run.stage,
        runtime=run.runtime,
        model=run.model,
        seed_branch=seed_branch,
        base_heads=base_heads,
        remote_seed_heads=remote_seed_heads,
        blocked_paths=blocked_paths,
        blocked_paths_action=blocked_action,
        max_file_bytes=max_file_bytes,
        max_diff_bytes=max_diff_bytes,
        issued_secrets=issued_secrets,
        repositories=repo_items,
        min_acceptance_criteria=ideate_pol.min_acceptance_criteria,
        max_acceptance_criteria=ideate_pol.max_acceptance_criteria,
        max_tasks=plan_pol.max_tasks,
        output_schema=_stage_output_schema(
            run.stage,
            min_acceptance_criteria=ideate_pol.min_acceptance_criteria,
            max_acceptance_criteria=ideate_pol.max_acceptance_criteria,
            max_tasks=plan_pol.max_tasks,
        ),
    )


async def refresh_runner_cli_auth(
    session: AsyncSession,
    raw_token: str | None,
    payload: RunnerCliAuthRefreshRequest | dict[str, Any],
) -> RunnerCliAuthRefreshResponse:
    """Validate and merge refreshed CLI OAuth credentials from an agent container using strict per-runtime allowlist."""
    raw_bundle: Any = (
        payload.cli_auth_bundle
        if isinstance(payload, RunnerCliAuthRefreshRequest)
        else (
            payload.get("cli_auth_bundle")
            if isinstance(payload, dict) and "cli_auth_bundle" in payload and isinstance(payload.get("cli_auth_bundle"), dict)
            else payload
        )
    )
    if not isinstance(raw_bundle, dict):
        raise Invalid("cli_auth_bundle must be a JSON object")
    try:
        serialized_in = json.dumps(raw_bundle)
    except (TypeError, ValueError) as exc:
        raise Invalid("cli_auth_bundle must be JSON-serializable") from exc
    if len(serialized_in.encode("utf-8")) > MAX_CLI_AUTH_BUNDLE_BYTES:
        raise Invalid(
            f"cli_auth_bundle exceeds maximum size of {MAX_CLI_AUTH_BUNDLE_BYTES} bytes"
        )

    _enforce_endpoint_token_scope(raw_token, {"agent"})
    run, _, project, _ = await _authenticate_active_run(session, raw_token)
    runtime_str = str(run.runtime or "").strip().lower()

    models_backend, _ = await resolve_models_backend_for_run(session, run, project)
    m_secrets = (
        decrypt_secrets(models_backend.secrets_encrypted)
        if models_backend is not None
        else {}
    )
    merged_bundle, updated_keys = _validate_and_merge_refreshed_cli_auth_bundle(
        raw_bundle,
        runtime=runtime_str,
        existing_raw_json=m_secrets.get("cli_auth_bundle"),
        backend_provider=models_backend.provider if models_backend is not None else None,
    )
    if not updated_keys:
        return RunnerCliAuthRefreshResponse(ok=True, updated_paths=[])

    refreshed_secrets = _extract_cli_auth_secrets(merged_bundle)
    if refreshed_secrets:
        broker_state = _get_broker_state(run)
        cur_issued = [
            str(s)
            for s in (broker_state.get("issued_secrets") or [])
            if isinstance(s, str) and s
        ]
        for rs in refreshed_secrets:
            if len(rs) >= 8 and rs not in cur_issued:
                cur_issued.append(rs)
        broker_state["issued_secrets"] = cur_issued
        _set_broker_state(run, broker_state)
        run.updated_at = datetime.now(UTC)

    if models_backend is not None:
        m_secrets["cli_auth_bundle"] = json.dumps(merged_bundle)
        models_backend.secrets_encrypted = encrypt_secrets(m_secrets)
        models_backend.updated_at = datetime.now(UTC)
    await session.commit()

    return RunnerCliAuthRefreshResponse(
        ok=True,
        updated_paths=updated_keys,
    )


refresh_runner_cli_auth_bundle = refresh_runner_cli_auth


async def poll_runner_steering(
    session: AsyncSession,
    raw_token: str | None,
    *,
    since: datetime | None = None,
    log_tail: str | None = None,
    context_tokens: int | None = None,
    peak_context_tokens: int | None = None,
    turn_count: int | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
) -> RunnerSteeringResponse:
    """Check if the run should abort or if new @agent steering comments arrived."""
    if not raw_token:
        return RunnerSteeringResponse(should_abort=True, abort_reason="Missing run token")
    _enforce_endpoint_token_scope(raw_token, {"agent"})
    run_id = verify_run_token(raw_token)
    if not run_id:
        return RunnerSteeringResponse(should_abort=True, abort_reason="Expired run token")

    run = await session.get(Run, run_id)
    if run is None:
        return RunnerSteeringResponse(should_abort=True, abort_reason="Run not found")
    if run.status not in (RunStatus.STARTING, RunStatus.RUNNING):
        return RunnerSteeringResponse(
            should_abort=True,
            abort_reason=f"Run transitioned to '{str(run.status)}'",
        )

    seed = await session.get(Seed, run.seed_id)
    if seed is None:
        return RunnerSteeringResponse(should_abort=True, abort_reason="Seed not found")
    if seed.status == SeedStatus.BLOCKED:
        return RunnerSteeringResponse(
            should_abort=True,
            abort_reason=seed.blocked_reason or "Seed blocked by budget or checkpoint",
        )

    dirty = False
    if input_tokens and input_tokens > 0:
        new_in = max(int(run.input_tokens or 0), int(input_tokens))
        if new_in != run.input_tokens:
            run.input_tokens = new_in
            dirty = True
    if output_tokens and output_tokens > 0:
        new_out = max(int(run.output_tokens or 0), int(output_tokens))
        if new_out != run.output_tokens:
            run.output_tokens = new_out
            dirty = True

    existing_output = dict(run.output or {})
    if (context_tokens and context_tokens > 0) or (peak_context_tokens and peak_context_tokens > 0):
        project_obj = await session.get(Project, seed.project_id)
        models_backend = None
        if project_obj is not None:
            models_backend, _ = await resolve_models_backend_for_run(session, run, project_obj)
        existing_output = merge_run_context_window(
            existing_output,
            used_tokens=int(context_tokens or 0),
            peak_tokens=int(peak_context_tokens or 0),
            turns=int(turn_count or 0),
            model=run.model,
            runtime=str(run.runtime or ""),
            backend=models_backend,
        )
        run.output = existing_output
        dirty = True

    if log_tail is not None and log_tail.strip():
        trimmed_log = log_tail[-50000:]
        if existing_output.get("log_tail") != trimmed_log:
            existing_output["log_tail"] = trimmed_log
            run.output = existing_output
            dirty = True
            events_service.emit_from_sync(
                type="run.log",
                actor_type="agent",
                actor_id=run.agent_id,
                project_id=seed.project_id,
                seed_id=seed.id,
                data={
                    "run_id": run.id,
                    "stage": str(run.stage),
                    "log_tail": trimmed_log,
                },
            )

    if dirty:
        run.updated_at = datetime.now(UTC)
        await session.commit()

    agent = await session.get(Agent, run.agent_id)
    target_handle = agent.handle.lower() if agent else ""

    cutoff = since or run.started_at or run.created_at
    stmt = select(SeedComment).where(
        SeedComment.seed_id == seed.id,
        SeedComment.author_type == ActorType.USER,
        col(SeedComment.deleted_at).is_(None),
    )
    if cutoff is not None:
        stmt = stmt.where(col(SeedComment.created_at) > cutoff)
    stmt = stmt.order_by(col(SeedComment.created_at).asc())

    candidate_comments = list((await session.execute(stmt)).scalars().all())
    if not candidate_comments:
        return RunnerSteeringResponse(should_abort=False, steering_comments=[])

    steering_items: list[RunnerCommentItem] = []
    for c in candidate_comments:
        mentions = extract_agent_mentions(c.body)
        if not (
            (target_handle and target_handle in mentions)
            or "agent" in mentions
            or "stage" in mentions
        ):
            continue

        # Verify author is an active Org Admin or Project Developer / Maintainer (never Viewer)
        user = await session.get(User, c.author_id)
        if user is None or user.status != UserStatus.ACTIVE:
            continue
        is_allowed = user.org_role == OrgRole.ADMIN
        if not is_allowed:
            member = (
                await session.execute(
                    select(ProjectMember).where(
                        ProjectMember.project_id == seed.project_id,
                        ProjectMember.user_id == user.id,
                    )
                )
            ).scalar_one_or_none()
            if member is not None and member.role in (
                ProjectRole.MAINTAINER,
                ProjectRole.DEVELOPER,
            ):
                is_allowed = True
        if not is_allowed:
            continue

        steering_items.append(
            RunnerCommentItem(
                id=c.id,
                author_type=str(c.author_type),
                author_name=user.preferred_name or user.name,
                stage=c.stage,
                body=c.body,
                created_at=c.created_at or datetime.now(UTC),
            )
        )

    return RunnerSteeringResponse(
        should_abort=False,
        steering_comments=steering_items,
    )


async def complete_runner_execution(
    session: AsyncSession,
    raw_token: str | None,
    payload: RunnerCompleteRequest,
) -> Run:
    """Validate stage output from /workspace/.gitseed/output.json and finalize Run and Seed (finalize scope, single-use)."""
    _enforce_endpoint_token_scope(raw_token, {"finalize"})
    run, seed, project, agent = await _authenticate_active_run(session, raw_token)
    await _try_lock_run_and_seed(session, run.id, seed.id)
    preserved_run_meta: dict[str, Any] = (
        {
            k: run.output[k]
            for k in _PRESERVED_RUN_OUTPUT_KEYS
            if k in run.output and run.output[k] is not None
        }
        if isinstance(run.output, dict)
        else {}
    )
    broker_state = _get_broker_state(run)
    if broker_state.get("finalize_complete_used"):
        raise Conflict("Run completion has already been reported for this run")
    broker_state["finalize_complete_used"] = True
    _set_broker_state(run, broker_state)
    now = datetime.now(UTC)
    trusted_result = _normalize_trusted_result(
        run=run,
        seed=seed,
        project=project,
        raw_trusted=payload.trusted,
        broker_state=broker_state,
        now=now,
    )
    was_second_opinion = isinstance(run.output, dict) and bool(
        run.output.get("second_opinion")
    )
    was_multi_opinion = was_second_opinion or (
        isinstance(run.output, dict) and bool(run.output.get("multi_opinion"))
    )

    pol = await get_effective_stage_policy(session, project.id, run.stage)
    ideate_pol = (
        pol
        if run.stage == SeedStage.IDEATE
        else await get_effective_stage_policy(session, project.id, SeedStage.IDEATE)
    )
    plan_pol = (
        pol
        if run.stage == SeedStage.PLAN
        else await get_effective_stage_policy(session, project.id, SeedStage.PLAN)
    )
    if payload.input_tokens and payload.input_tokens > 0:
        run.input_tokens = max(int(run.input_tokens or 0), int(payload.input_tokens))
    if payload.output_tokens and payload.output_tokens > 0:
        run.output_tokens = max(int(run.output_tokens or 0), int(payload.output_tokens))
    if payload.context_tokens > 0 or payload.peak_context_tokens > 0:
        ctx_models_backend: Any = None
        try:
            ctx_models_backend, _ = await resolve_models_backend_for_run(
                session, run, project, allow_disabled=True
            )
        except TypeError:
            ctx_models_backend, _ = await resolve_models_backend_for_run(
                session, run, project
            )
        except Exception:
            ctx_models_backend = None
        run.output = merge_run_context_window(
            run.output,
            used_tokens=payload.context_tokens,
            peak_tokens=payload.peak_context_tokens,
            turns=payload.turn_count,
            model=run.model,
            runtime=str(run.runtime or ""),
            backend=ctx_models_backend,
        )
    await finalize_run_litellm_key(session, run, seed, project, delete_key=False)
    if isinstance(run.output, dict):
        for k in _PRESERVED_RUN_OUTPUT_KEYS:
            if k in run.output and run.output[k] is not None:
                preserved_run_meta[k] = run.output[k]

    eff_auth_mode = str(preserved_run_meta.get("auth_mode") or "").strip().lower()
    total_tokens = int(run.input_tokens or 0) + int(run.output_tokens or 0)
    if eff_auth_mode == "cli_oauth":
        preserved_run_meta.setdefault("cost_enforcement", "estimated")
        preserved_run_meta.setdefault("budget_enforcement", "estimated")
        preserved_run_meta.setdefault("enforcement", "estimated")
        if Decimal(str(run.cost or 0)) == Decimal("0") and total_tokens > 0:
            est_backend = None
            try:
                est_backend, _ = await resolve_models_backend_for_run(
                    session, run, project, allow_disabled=True
                )
            except TypeError:
                try:
                    est_backend, _ = await resolve_models_backend_for_run(
                        session, run, project
                    )
                except Exception:
                    est_backend = None
            except Exception:
                est_backend = None
            model_for_est = str(
                preserved_run_meta.get("gateway_model") or run.model or ""
            )
            estimated_usd = estimate_model_token_cost(
                model_for_est,
                int(run.input_tokens or 0),
                int(run.output_tokens or 0),
                backend=est_backend,
            )
            if estimated_usd > Decimal("0"):
                run.cost = estimated_usd
                prev_seed_spent = Decimal(str(seed.spent or 0))
                try:
                    await session.execute(
                        update(Seed)
                        .where(col(Seed.id) == seed.id)
                        .values(
                            spent=col(Seed.spent) + estimated_usd,
                            updated_at=now,
                        )
                    )
                    await session.flush()
                    await session.refresh(seed)
                except Exception:
                    pass
                if Decimal(str(seed.spent or 0)) == prev_seed_spent:
                    seed.spent = prev_seed_spent + estimated_usd
            preserved_run_meta["cost_enforcement"] = "estimated"
            preserved_run_meta["budget_enforcement"] = "estimated"
            preserved_run_meta["enforcement"] = "estimated"

    raw_ctx_win = (run.output or {}).get("context_window")
    preserved_context_window = (
        dict(raw_ctx_win) if isinstance(raw_ctx_win, dict) else None
    )

    if payload.updated_cli_auth_bundle:
        try:
            try:
                models_backend, _ = await resolve_models_backend_for_run(
                    session, run, project, allow_disabled=True
                )
            except TypeError:
                models_backend, _ = await resolve_models_backend_for_run(
                    session, run, project
                )
            if models_backend is not None:
                m_secrets = decrypt_secrets(models_backend.secrets_encrypted)
                merged_bundle, updated_keys = _validate_and_merge_refreshed_cli_auth_bundle(
                    payload.updated_cli_auth_bundle,
                    runtime=str(run.runtime or ""),
                    existing_raw_json=m_secrets.get("cli_auth_bundle"),
                    backend_provider=models_backend.provider,
                )
                if updated_keys:
                    m_secrets["cli_auth_bundle"] = json.dumps(merged_bundle)
                    models_backend.secrets_encrypted = encrypt_secrets(m_secrets)
                    models_backend.updated_at = now
        except Invalid:
            raise
        except Exception as exc:
            logger.debug("Skipping CLI auth bundle refresh on run completion: %s", exc)

    # Strip any trusted-looking fields from untrusted agent stage_output
    sanitized_stage_output: dict[str, Any] = {}
    if isinstance(payload.stage_output, dict):
        sanitized_stage_output = {
            k: v
            for k, v in payload.stage_output.items()
            if k not in ("commit_sha", "branch_name", "pushed", "trusted", "verification")
        }

    run.exit_code = payload.exit_code
    run.error = payload.error
    run.updated_at = now
    if run.started_at is None:
        run.started_at = now

    if payload.status != RunStatus.SUCCEEDED:
        run.status = (
            payload.status
            if payload.status in (RunStatus.FAILED, RunStatus.TIMED_OUT, RunStatus.CANCELLED)
            else RunStatus.FAILED
        )
        run.finished_at = now
        out_dict = {**preserved_run_meta, **sanitized_stage_output}
        out_dict["_broker"] = broker_state
        out_dict["trusted"] = trusted_result
        if was_second_opinion:
            out_dict["second_opinion"] = True
        if was_multi_opinion:
            out_dict["multi_opinion"] = True
        if payload.log_tail and payload.log_tail.strip():
            out_dict["log_tail"] = payload.log_tail.strip()[-50000:]
        if preserved_context_window is not None:
            out_dict["context_window"] = preserved_context_window
        run.output = out_dict

        if run.task_id is not None:
            task_row = await session.get(SeedTask, run.task_id)
            if task_row is not None and task_row.seed_id == seed.id:
                task_row.status = TaskStatus.FAILED
                task_row.completed_at = now
                task_row.updated_at = now
        elif run.stage == SeedStage.EXECUTE:
            await reset_orphaned_running_tasks(
                session, seed.id, exclude_run_id=run.id, now=now
            )

        # Reconcile seed status across parallel opinions or retry automatically
        if seed.status != SeedStatus.BLOCKED:
            handled_by_sibling = False
            if was_multi_opinion and run.stage in MULTI_OPINION_STAGES:
                cycle_runs = await list_stage_cycle_runs(session, seed, run.stage)
                has_active_sibling = any(
                    r.id != run.id and r.status in RUN_ACTIVE_STATUSES
                    for r in cycle_runs
                )
                if has_active_sibling:
                    seed.status = SeedStatus.RUNNING
                    seed.blocked_reason = None
                    seed.updated_at = now
                    handled_by_sibling = True
                else:
                    succeeded_siblings = [
                        r
                        for r in cycle_runs
                        if r.id != run.id and r.status == RunStatus.SUCCEEDED
                    ]
                    if succeeded_siblings:
                        await advance_seed_after_succeeded_run(
                            session, seed, succeeded_siblings[-1], pol, now
                        )
                        handled_by_sibling = True

            if not handled_by_sibling:
                if not was_second_opinion and run.attempt < pol.max_attempts:
                    seed.status = SeedStatus.QUEUED
                    seed.blocked_reason = None
                    seed.updated_at = now
                    if run.task_id is not None:
                        task_row = await session.get(SeedTask, run.task_id)
                        if task_row is not None and task_row.seed_id == seed.id:
                            task_row.status = TaskStatus.PENDING
                            task_row.completed_at = None
                            task_row.updated_at = now
                else:
                    seed.status = SeedStatus.FAILED
                    seed.blocked_reason = None
                    seed.updated_at = now

        record(
            session,
            type=(
                ActivityType.RUN_TIMED_OUT
                if run.status == RunStatus.TIMED_OUT
                else ActivityType.RUN_FAILED
            ),
            actor_type=ActorType.AGENT,
            actor_id=agent.id,
            project_id=project.id,
            seed_id=seed.id,
            data={
                "run_id": run.id,
                "stage": str(run.stage),
                "attempt": run.attempt,
                "error": run.error,
            },
        )
        await session.commit()
        await revoke_run_virtual_key(run.id)
        await session.refresh(run)
        return run

    # Validate stage_output against stage-specific Pydantic contract
    summary_comment: str | None = None
    fast_tracked_plan_out: PlanStageOutput | None = None
    try:
        if run.stage == SeedStage.IDEATE:
            ideate_out = IdeateStageOutput.model_validate(payload.stage_output)
            if isinstance(ideate_out.fast_tracked_plan, dict):
                try:
                    fast_tracked_plan_out = PlanStageOutput.model_validate(
                        ideate_out.fast_tracked_plan
                    )
                except ValidationError:
                    fast_tracked_plan_out = None
            prev_title = seed.title
            prev_description = seed.description
            prev_specification = seed.specification
            prev_criteria = list(seed.acceptance_criteria or [])

            # Backfill seed.created activity snapshot if description wasn't captured at creation
            ev_res = await session.execute(
                select(Activity)
                .where(
                    Activity.seed_id == seed.id,
                    Activity.type == ActivityType.SEED_CREATED,
                )
                .order_by(col(Activity.id).asc())
                .limit(1)
            )
            created_ev = None
            ev_scalars_fn = getattr(ev_res, "scalars", None)
            if callable(ev_scalars_fn):
                ev_scalars = ev_scalars_fn()
                if inspect.iscoroutine(ev_scalars):
                    ev_scalars.close()
                else:
                    first_fn = getattr(ev_scalars, "first", None)
                    if callable(first_fn):
                        maybe_ev = first_fn()
                        if inspect.iscoroutine(maybe_ev):
                            maybe_ev.close()
                        elif isinstance(maybe_ev, Activity):
                            created_ev = maybe_ev
            if created_ev is not None and isinstance(getattr(created_ev, "data", None), dict):
                ev_data = dict(created_ev.data)
                if "description" not in ev_data:
                    ev_data["title"] = ev_data.get("title") or prev_title
                    ev_data["description"] = prev_description
                    ev_data["specification"] = prev_specification
                    ev_data["acceptance_criteria"] = prev_criteria
                    created_ev.data = ev_data

            cycle_runs = (
                await list_stage_cycle_runs(session, seed, SeedStage.IDEATE)
                if was_multi_opinion
                else []
            )
            other_succeeded = [
                r for r in cycle_runs if r.id != run.id and r.status == RunStatus.SUCCEEDED
            ]
            user_already_picked = any(
                isinstance(r.output, dict) and r.output.get("selected_by_user")
                for r in other_succeeded
            )
            should_apply_to_seed = (
                not other_succeeded
                or (
                    not user_already_picked
                    and run.agent_id == pol.agent_id
                    and not was_second_opinion
                )
            )
            if should_apply_to_seed:
                if ideate_out.title and ideate_out.title.strip():
                    seed.title = ideate_out.title.strip()[:200]
                if ideate_out.description and ideate_out.description.strip():
                    seed.description = ideate_out.description.strip()
                seed.specification = ideate_out.specification.strip()
                seed.acceptance_criteria = ideate_out.acceptance_criteria
                for prev_r in other_succeeded:
                    if isinstance(prev_r.output, dict) and prev_r.output.get("selected"):
                        prev_r.output = {**prev_r.output, "selected": False}

            summary_comment = ideate_out.summary_comment.strip()
            out_dump = ideate_out.model_dump()
            out_dump["previous_title"] = prev_title
            out_dump["previous_description"] = prev_description
            out_dump["previous_acceptance_criteria"] = prev_criteria
            out_dump["selected"] = should_apply_to_seed
            if was_second_opinion:
                out_dump["second_opinion"] = True
            if was_multi_opinion:
                out_dump["multi_opinion"] = True
            ideate_warn = check_stage_policy_bounds(
                "ideate",
                out_dump,
                min_acceptance_criteria=ideate_pol.min_acceptance_criteria,
                max_acceptance_criteria=ideate_pol.max_acceptance_criteria,
            )
            if ideate_warn is not None:
                out_dump["policy_limit_warning"] = ideate_warn
            run.output = out_dump

        elif run.stage == SeedStage.PLAN:
            plan_out = PlanStageOutput.model_validate(payload.stage_output)
            summary_comment = plan_out.summary_comment.strip()
            cycle_runs = (
                await list_stage_cycle_runs(session, seed, SeedStage.PLAN)
                if was_multi_opinion
                else []
            )
            other_succeeded = [
                r for r in cycle_runs if r.id != run.id and r.status == RunStatus.SUCCEEDED
            ]
            user_already_picked = any(
                isinstance(r.output, dict) and r.output.get("selected_by_user")
                for r in other_succeeded
            )
            should_apply_to_seed = (
                not other_succeeded
                or (
                    not user_already_picked
                    and run.agent_id == pol.agent_id
                    and not was_second_opinion
                )
            )
            plan_dump = plan_out.model_dump()
            plan_dump["selected"] = should_apply_to_seed
            if was_second_opinion:
                plan_dump["second_opinion"] = True
            if was_multi_opinion:
                plan_dump["multi_opinion"] = True
            plan_warn = check_stage_policy_bounds(
                "plan",
                plan_dump,
                max_tasks=plan_pol.max_tasks,
            )
            if plan_warn is not None:
                plan_dump["policy_limit_warning"] = plan_warn
            run.output = plan_dump

            if should_apply_to_seed:
                seed.plan = plan_out.plan.strip()
                for prev_r in other_succeeded:
                    if isinstance(prev_r.output, dict) and prev_r.output.get("selected"):
                        prev_r.output = {**prev_r.output, "selected": False}
                # Replace pending tasks with the newly synthesized plan tasks in order
                await session.execute(
                    delete(SeedTask).where(col(SeedTask.seed_id) == seed.id)
                )
                await session.flush()
                for idx, item in enumerate(plan_out.tasks):
                    session.add(
                        SeedTask(
                            seed_id=seed.id,
                            position=idx,
                            title=item.title.strip(),
                            description=_format_task_description_with_files(
                                item.description, item.target_files
                            ),
                            status=TaskStatus.PENDING,
                            created_at=now,
                            updated_at=now,
                        )
                    )

        elif run.stage == SeedStage.EXECUTE:
            exec_out = ExecuteStageOutput.model_validate(sanitized_stage_output)
            summary_comment = exec_out.summary_comment.strip()
            out_data = exec_out.model_dump()
            # Populate commit_sha and branch_name exclusively from trusted finalize results
            out_data["commit_sha"] = trusted_result["commit_sha"]
            out_data["branch_name"] = (
                trusted_result["branch"]
                if trusted_result["commit_sha"] or trusted_result["pushed"]
                else None
            )
            if trusted_result["blocked_paths_violations"]:
                out_data["blocked_paths_reverted"] = trusted_result[
                    "blocked_paths_violations"
                ]
            run.output = out_data
            done_task_ids: list[str] = []
            if run.task_id is not None:
                done_task_ids.append(run.task_id)
            for tid in exec_out.completed_task_ids or []:
                cleaned_tid = str(tid).strip()
                if cleaned_tid and cleaned_tid not in done_task_ids:
                    done_task_ids.append(cleaned_tid)
            for tid in done_task_ids:
                task_row = await session.get(SeedTask, tid)
                if task_row is not None and task_row.seed_id == seed.id:
                    task_row.status = TaskStatus.DONE
                    if task_row.started_at is None:
                        task_row.started_at = now
                    task_row.completed_at = now
                    task_row.updated_at = now

        elif run.stage == SeedStage.REVIEW:
            rev_out = ReviewStageOutput.model_validate(sanitized_stage_output)
            summary_comment = rev_out.summary_comment.strip()
            cleaned_findings = [
                f.strip() for f in (rev_out.findings or []) if f and f.strip()
            ]
            if cleaned_findings:
                missing_findings = [
                    f for f in cleaned_findings if f not in summary_comment
                ]
                if missing_findings:
                    findings_md = "\n".join(f"- {f}" for f in missing_findings)
                    summary_comment = f"{summary_comment}\n\n**Findings:**\n{findings_md}"
            rev_dump = rev_out.model_dump()
            if was_second_opinion:
                rev_dump["second_opinion"] = True
            if was_multi_opinion:
                rev_dump["multi_opinion"] = True
            if is_auto_create_pr_enabled(project):
                pr_url = await ensure_seed_pull_request(
                    session,
                    seed,
                    project,
                    review_comment=summary_comment,
                )
                if pr_url:
                    rev_dump["pull_request_url"] = pr_url
            run.output = rev_dump

        elif run.stage == SeedStage.MERGE:
            merge_out = MergeStageOutput.model_validate(sanitized_stage_output)
            summary_comment = merge_out.summary_comment.strip()
            run.output = merge_out.model_dump()

    except ValidationError as exc:
        raise Invalid(
            f"Invalid /workspace/.gitseed/output.json for stage '{str(run.stage)}': {exc}"
        ) from exc

    await revoke_run_virtual_key(run.id)

    run.output = {
        **preserved_run_meta,
        **(run.output or {}),
        "_broker": broker_state,
        "trusted": trusted_result,
    }
    if payload.log_tail and payload.log_tail.strip():
        run.output = {
            **(run.output or {}),
            "log_tail": payload.log_tail.strip()[-50000:],
        }
    if preserved_context_window is not None:
        run.output = {
            **(run.output or {}),
            "context_window": preserved_context_window,
        }
    if isinstance(payload.stage_output, dict) and payload.stage_output.get("synthesized_via"):
        run.output = {
            **(run.output or {}),
            "synthesized_via": str(payload.stage_output["synthesized_via"]),
        }

    if summary_comment:
        session.add(
            SeedComment(
                seed_id=seed.id,
                author_type=ActorType.AGENT,
                author_id=agent.id,
                stage=run.stage,
                body=summary_comment,
                created_at=now,
                updated_at=now,
            )
        )

    run.status = RunStatus.SUCCEEDED
    run.finished_at = now
    record(
        session,
        type=ActivityType.RUN_SUCCEEDED,
        actor_type=ActorType.AGENT,
        actor_id=agent.id,
        project_id=project.id,
        seed_id=seed.id,
        data={
            "run_id": run.id,
            "stage": str(run.stage),
            "attempt": run.attempt,
            "cost": str(run.cost),
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
        },
    )

    await session.flush()

    # If the seed was blocked by budget/checkpoint during the final call, keep it blocked
    if seed.status == SeedStatus.BLOCKED:
        await session.commit()
        await revoke_run_virtual_key(run.id)
        await session.refresh(run)
        return run

    # During Execute, if more PENDING tasks remain on the seed, queue the seed for the next task
    if run.stage == SeedStage.EXECUTE:
        remaining_pending = (
            await session.execute(
                select(SeedTask.id)
                .where(
                    SeedTask.seed_id == seed.id,
                    SeedTask.status == TaskStatus.PENDING,
                )
                .limit(1)
            )
        ).first()
        if remaining_pending is not None:
            seed.status = SeedStatus.QUEUED
            seed.blocked_reason = None
            seed.updated_at = now
            await session.commit()
            await session.refresh(run)
            return run

        # All Execute tasks are complete: ensure PR/MR is open before entering Review
        if is_auto_create_pr_enabled(project) and not (run.output or {}).get("pull_request_url"):
            pr_url = await ensure_seed_pull_request(
                session,
                seed,
                project,
                (run.output or {}).get("branch_name"),
            )
            if pr_url:
                run.output = {**(run.output or {}), "pull_request_url": pr_url}

    # Determine whether stage requires human approval or auto-advances
    await advance_seed_after_succeeded_run(session, seed, run, pol, now)

    # If Ideate fast-tracked Plan in the same warm container & session, apply Plan output and advance
    if (
        fast_tracked_plan_out is not None
        and seed.stage == SeedStage.PLAN
        and seed.status == SeedStatus.QUEUED
    ):
        seed.plan = fast_tracked_plan_out.plan.strip()
        ft_dump = fast_tracked_plan_out.model_dump()
        ft_warn = check_stage_policy_bounds(
            "plan",
            ft_dump,
            max_tasks=plan_pol.max_tasks,
        )
        merged_out = dict(run.output or {})
        if ft_dump.get("suggested_follow_ups"):
            merged_out["suggested_follow_ups"] = ft_dump["suggested_follow_ups"]
        if ft_warn is not None:
            merged_out["policy_limit_warning"] = ft_warn
        run.output = merged_out
        await session.execute(
            delete(SeedTask).where(col(SeedTask.seed_id) == seed.id)
        )
        await session.flush()
        for idx, item in enumerate(fast_tracked_plan_out.tasks):
            session.add(
                SeedTask(
                    seed_id=seed.id,
                    position=idx,
                    title=item.title.strip(),
                    description=_format_task_description_with_files(
                        item.description, item.target_files
                    ),
                    status=TaskStatus.PENDING,
                    created_at=now,
                    updated_at=now,
                )
            )
        plan_comment = fast_tracked_plan_out.summary_comment.strip()
        if plan_comment:
            session.add(
                SeedComment(
                    seed_id=seed.id,
                    author_type=ActorType.AGENT,
                    author_id=agent.id,
                    stage=SeedStage.PLAN,
                    body=plan_comment,
                    created_at=now,
                    updated_at=now,
                )
            )
        await session.flush()
        plan_pol = await get_effective_stage_policy(
            session, project.id, SeedStage.PLAN
        )
        await advance_seed_after_succeeded_run(
            session,
            seed,
            run,
            plan_pol,
            now,
            completed_stage_override=SeedStage.PLAN,
        )

    await session.commit()
    await session.refresh(run)
    return run
