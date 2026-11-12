"""GitSeed Runner Container Supervisor.

Orchestrates:
1. Context acquisition from Control Plane (`GET /api/v1/runner/context`).
2. Shallow repository cloning (`--depth 1 --filter=blob:none`) & credential stripping.
3. Agent CLI execution (`opencode`, `claude-code`, `codex`) with 10s-debounced `@agent` steering
   and session continuation (`--continue`).
4. Pydantic validation of `/workspace/.gitseed/output.json` (with 1x repair re-prompt on failure).
5. Post-agent Git branch push during `execute` (`POST /api/v1/runner/git-push-token`).
6. Stage completion reporting (`POST /api/v1/runner/complete`).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import sys
import tempfile
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import ValidationError

from gitseed_runner.adapters import (
    RuntimeAdapter,
    create_runtime_adapter,
    detect_fatal_agent_error,
    extract_json_object,
    synthesize_output_via_gateway,
)
from gitseed_runner.broker_modes import (
    get_ctl_dir,
    post_cli_auth_refresh,
    resolve_existing_repo_dirs,
    run_agent_phase,
    run_finalize_mode,
    run_prepare_mode,
    write_agent_done_marker,
)
from gitseed_runner.git_ops import (
    _run_git,
    cleanup_git_locks,
    cleanup_task_worktree,
    clone_project_repositories,
    collect_workspace_git_summary,
    create_task_worktree,
    format_seed_branch,
    get_repo_heads,
    has_remote_seed_branch,
    has_workspace_code_changes,
    merge_and_remove_task_worktree,
    push_execute_changes_after_agent_exit,
    tasks_have_disjoint_targets,
)
from gitseed_runner.gitsafe import _exclude_internal_paths
from gitseed_runner.prompts import (
    build_ideate_to_plan_continuation_prompt,
    build_next_task_continuation_prompt,
    build_stage_prompt,
    build_steering_continuation_prompt,
    build_synthesis_context,
    build_validation_repair_prompt,
    summarize_prompt_stats,
)
from gitseed_runner.schemas import validate_stage_output_data

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("gitseed_runner")

_SUPERVISOR_LOG_LINES: list[str] = []


class _SupervisorLogBufferHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            _SUPERVISOR_LOG_LINES.append(f"{msg}\n")
            if len(_SUPERVISOR_LOG_LINES) > 500:
                del _SUPERVISOR_LOG_LINES[: len(_SUPERVISOR_LOG_LINES) - 500]
        except Exception:
            pass


_buf_handler = _SupervisorLogBufferHandler()
_buf_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logger.addHandler(_buf_handler)


def _format_tool_args_preview(tool_args: Any) -> str:
    if isinstance(tool_args, str):
        stripped_args = tool_args.strip()
        if stripped_args.startswith("{") and stripped_args.endswith("}"):
            try:
                tool_args = json.loads(stripped_args)
            except Exception:
                return f" {stripped_args[:140]}"
        elif stripped_args:
            return f" {stripped_args[:140]}"
    if isinstance(tool_args, dict):
        for k in (
            "path",
            "file_path",
            "Path",
            "target_file",
            "command",
            "CommandLine",
            "cmd",
            "pattern",
            "query",
            "url",
        ):
            val = tool_args.get(k)
            if val is not None and str(val).strip():
                clean_val = str(val).strip().replace("\n", " ")[:140]
                return f" {k}={clean_val}"
        if tool_args:
            compact = ", ".join(
                f"{k}={str(v).strip().replace(chr(10), ' ')[:60]}"
                for k, v in list(tool_args.items())[:3]
                if v is not None and str(v).strip()
            )
            if compact:
                return f" {compact[:140]}"
    return ""


def _extract_tool_result_text(raw_output: Any) -> str:
    if isinstance(raw_output, str):
        return raw_output.strip().replace("\n", " ")[:180]
    if isinstance(raw_output, list):
        texts: list[str] = []
        for item in raw_output:
            if isinstance(item, dict) and item.get("text"):
                texts.append(str(item["text"]).strip())
            elif isinstance(item, str):
                texts.append(item.strip())
        return " ".join(texts).replace("\n", " ")[:180]
    if isinstance(raw_output, dict):
        for k in ("output", "stdout", "result", "content", "text", "error"):
            if raw_output.get(k):
                return str(raw_output[k]).strip().replace("\n", " ")[:180]
    return ""


def _format_cli_stream_line(raw_line: str, adapter: RuntimeAdapter) -> str:
    """Format NDJSON stream events from agent CLIs into readable real-time logs."""
    adapter_parsed = adapter.parse_stream_line(raw_line)
    if adapter_parsed is not None:
        return adapter_parsed

    stripped = raw_line.strip()
    if not stripped.startswith("{") or not stripped.endswith("}"):
        return raw_line
    try:
        ev = json.loads(stripped)
    except Exception:
        return raw_line
    if not isinstance(ev, dict):
        return raw_line

    # Extract token usage if reported in stream event (top-level or nested under message/turn)
    msg_dict = ev.get("message") if isinstance(ev.get("message"), dict) else {}
    usage = (
        ev.get("usage")
        or ev.get("stats")
        or (msg_dict.get("usage") if isinstance(msg_dict, dict) else None)
    )
    if isinstance(usage, dict):
        in_tok = int(
            usage.get("input_tokens")
            or usage.get("prompt_tokens")
            or usage.get("inputTokens")
            or 0
        )
        cache_tok = int(
            usage.get("cache_read_input_tokens")
            or usage.get("cache_creation_input_tokens")
            or usage.get("cached_tokens")
            or 0
        )
        out_tok = int(
            usage.get("output_tokens")
            or usage.get("completion_tokens")
            or usage.get("outputTokens")
            or 0
        )
        total_in = in_tok + cache_tok
        if total_in > 0:
            adapter.input_tokens = max(adapter.input_tokens, total_in)
        if out_tok > 0:
            adapter.output_tokens = max(adapter.output_tokens, out_tok)
        turn_ctx = total_in + out_tok
        if turn_ctx > 0:
            adapter.context_tokens = turn_ctx
            adapter.peak_context_tokens = max(adapter.peak_context_tokens, turn_ctx)
            adapter.turn_count += 1

    ev_type = str(ev.get("type") or ev.get("event") or "").lower()
    role = str(ev.get("role") or (msg_dict.get("role") if isinstance(msg_dict, dict) else "") or "").lower()

    if ev_type in (
        "ping",
        "rate_limit_event",
        "step_start",
        "step_finish",
        "thread.started",
        "turn.started",
        "turn.completed",
    ):
        return ""

    if ev_type in ("init", "session", "system"):
        subtype = str(ev.get("subtype") or "").lower()
        if subtype and subtype not in ("init", "session"):
            return ""
        model_info = ev.get("model") or adapter.model
        return f"[agent] Session initialized (runtime={adapter.binary_name}, model={model_info})\n"

    # Codex JSONL item events (`item.started`, `item.completed`)
    if ev_type in ("item.started", "item.completed", "item.updated"):
        item = ev.get("item")
        if not isinstance(item, dict):
            return ""
        itype = str(item.get("type") or "").lower()
        if itype in ("reasoning", "thinking"):
            if ev_type == "item.completed":
                thought_txt = str(item.get("text") or item.get("summary") or "").strip()
                return f"[thinking] {thought_txt}\n" if thought_txt else ""
            return ""
        if itype == "command_execution":
            cmd_str = str(item.get("command") or "").strip().replace("\n", " ")[:140]
            if ev_type == "item.started":
                return f"[tool] bash command={cmd_str}\n"
            if ev_type == "item.completed":
                exit_code = item.get("exit_code")
                status_str = "error" if (exit_code is not None and exit_code != 0) else "ok"
                out_prev = _extract_tool_result_text(item.get("aggregated_output") or item.get("output"))
                return f"[tool:{status_str}] bash" + (f" -> {out_prev}\n" if out_prev else "\n")
            return ""
        if itype == "file_change" and ev_type == "item.completed":
            changes = item.get("changes")
            if isinstance(changes, list) and changes:
                paths = ", ".join(str(c.get("path") or "") for c in changes[:3] if isinstance(c, dict))
                return f"[tool:ok] file_change path={paths}\n"
            return "[tool:ok] file_change\n"
        if itype in ("agent_message", "message") and ev_type == "item.completed":
            txt = str(item.get("text") or item.get("content") or "").strip()
            return f"{txt}\n" if txt else ""
        return ""

    if ev_type in ("thinking", "thought") or "thinking" in ev:
        thought = str(ev.get("thinking") or ev.get("thought") or ev.get("content") or "").strip()
        if thought:
            return f"[thinking] {thought}\n"
        return ""

    if ev_type in ("tool_use", "tool_call", "function_call", "functioncall"):
        tool_name = str(ev.get("name") or ev.get("tool_name") or ev.get("tool") or "tool")
        tool_args = ev.get("input") or ev.get("parameters") or ev.get("arguments") or ev.get("args") or {}
        arg_preview = _format_tool_args_preview(tool_args)
        return f"[tool] {tool_name}{arg_preview}\n"

    if ev_type in ("tool_result", "function_response", "functionresponse"):
        status_str = "error" if ev.get("is_error") or ev.get("status") == "error" else "ok"
        tool_id = str(ev.get("tool_name") or ev.get("name") or ev.get("tool_use_id") or "tool")
        out_preview = _extract_tool_result_text(ev.get("output") or ev.get("content") or ev.get("response"))
        return f"[tool:{status_str}] {tool_id}" + (f" -> {out_preview}\n" if out_preview else "\n")

    # Handle Claude Code / Gemini `user` events that wrap `tool_result` content blocks
    if ev_type == "user" or role == "user":
        user_content = ev.get("content") or (msg_dict.get("content") if isinstance(msg_dict, dict) else None)
        if isinstance(user_content, list):
            res_parts: list[str] = []
            for block in user_content:
                if isinstance(block, dict):
                    btype = str(block.get("type") or "").lower()
                    if btype in ("tool_result", "function_response"):
                        status_str = "error" if block.get("is_error") or block.get("status") == "error" else "ok"
                        t_id = str(block.get("tool_name") or block.get("name") or block.get("tool_use_id") or "tool")
                        out_prev = _extract_tool_result_text(block.get("content") or block.get("output"))
                        res_parts.append(
                            f"[tool:{status_str}] {t_id}" + (f" -> {out_prev}\n" if out_prev else "\n")
                        )
            return "".join(res_parts)
        return ""

    if ev_type in ("message", "assistant", "content", "text", "delta") or role == "assistant":
        content = (
            ev.get("content")
            or ev.get("text")
            or ev.get("delta")
            or (msg_dict.get("content") if isinstance(msg_dict, dict) else None)
        )
        if isinstance(content, str) and content.strip():
            return content if content.endswith("\n") else f"{content}\n"
        if isinstance(content, list):
            parts: list[str] = []
            for block in content:
                if isinstance(block, dict):
                    btype = str(block.get("type") or "").lower()
                    if btype in ("thinking", "thought") and block.get("thinking"):
                        parts.append(f"[thinking] {str(block['thinking']).strip()}\n")
                    elif btype in ("tool_use", "tool_call", "function_call"):
                        tname = str(block.get("name") or block.get("tool_name") or "tool")
                        tinput = block.get("input") or block.get("arguments") or block.get("args") or {}
                        parts.append(f"[tool] {tname}{_format_tool_args_preview(tinput)}\n")
                    elif btype in ("tool_result", "function_response"):
                        status_str = "error" if block.get("is_error") else "ok"
                        tname = str(block.get("tool_name") or block.get("name") or block.get("tool_use_id") or "tool")
                        out_prev = _extract_tool_result_text(block.get("content") or block.get("output"))
                        parts.append(
                            f"[tool:{status_str}] {tname}" + (f" -> {out_prev}\n" if out_prev else "\n")
                        )
                    elif block.get("text"):
                        txt = str(block["text"])
                        parts.append(txt if txt.endswith("\n") else f"{txt}\n")
            return "".join(parts)
        return ""

    if ev_type == "result":
        res_text = str(ev.get("result") or ev.get("response") or ev.get("output") or "").strip()
        if res_text:
            return f"{res_text}\n"
        return "[agent] Turn completed.\n"

    if ev_type == "error":
        err_text = str(ev.get("error") or ev.get("message") or stripped)
        return f"[agent:error] {err_text}\n"

    if not ev_type and not role:
        return raw_line
    return ""

_POLL_INTERVAL_SECONDS = 3.0
_STEERING_DEBOUNCE_SECONDS = 10.0
_SUBPROCESS_STREAM_LIMIT = 16 * 1024 * 1024  # 16 MiB
_STREAM_DRAIN_CHUNK_BYTES = 65536
_MAX_TRUNCATED_PREVIEW_BYTES = 4096


def _get_worktree_homes_root() -> Path:
    """Return container-ephemeral directory for isolated parallel worktree $HOME dirs."""
    override = os.environ.get("GITSEED_WORKTREE_HOMES_DIR", "").strip()
    if override:
        return Path(override)
    if os.name != "nt":
        return Path("/tmp/gitseed-worktree-homes")
    return Path(tempfile.gettempdir()) / "gitseed-worktree-homes"


async def _stream_pipe(
    stream: asyncio.StreamReader,
    chunks: list[str],
    adapter: RuntimeAdapter | None = None,
    *,
    echo_stdout: bool = True,
) -> None:
    """Read lines from `stream`, safely draining oversized lines so OS pipes never deadlock."""
    leftover = b""
    hit_eof = False

    def _emit_decoded(decoded: str) -> None:
        formatted = (
            _format_cli_stream_line(decoded, adapter) if adapter is not None else decoded
        )
        if not formatted:
            return
        chunks.append(formatted)
        if echo_stdout:
            sys.stdout.write(formatted)
            sys.stdout.flush()

    while True:
        if b"\n" in leftover:
            seg, leftover = leftover.split(b"\n", 1)
            _emit_decoded((seg + b"\n").decode("utf-8", errors="replace"))
            continue
        if hit_eof:
            if leftover:
                tail = leftover
                leftover = b""
                _emit_decoded(tail.decode("utf-8", errors="replace"))
            break

        try:
            line = await stream.readline()
        except (ValueError, asyncio.LimitOverrunError) as exc:
            preview_buf = bytearray(leftover[:_MAX_TRUNCATED_PREVIEW_BYTES])
            drained_bytes = len(leftover)
            leftover = b""
            # CPython StreamReader.readline() deletes up to `\n` from _buffer before raising
            # ValueError("Separator is found, but chunk is longer than limit"). Only drain
            # via stream.read() when `\n` has NOT yet been consumed from the stream.
            already_consumed_newline = (
                isinstance(exc, ValueError) and "Separator is found" in str(exc)
            )
            if not already_consumed_newline:
                while True:
                    chunk = await stream.read(_STREAM_DRAIN_CHUNK_BYTES)
                    if not chunk:
                        hit_eof = True
                        break
                    nl_pos = chunk.find(b"\n")
                    if nl_pos == -1:
                        drained_bytes += len(chunk)
                        if len(preview_buf) < _MAX_TRUNCATED_PREVIEW_BYTES:
                            need = _MAX_TRUNCATED_PREVIEW_BYTES - len(preview_buf)
                            preview_buf.extend(chunk[:need])
                    else:
                        consumed_part = chunk[: nl_pos + 1]
                        drained_bytes += len(consumed_part)
                        if len(preview_buf) < _MAX_TRUNCATED_PREVIEW_BYTES:
                            need = _MAX_TRUNCATED_PREVIEW_BYTES - len(preview_buf)
                            preview_buf.extend(consumed_part[:need])
                        leftover = chunk[nl_pos + 1 :]
                        break
            preview_str = (
                bytes(preview_buf).decode("utf-8", errors="replace").rstrip("\r\n")
            )
            prefix = f"{preview_str}... " if preview_str else ""
            _emit_decoded(
                f"{prefix}[line truncated after exceeding stream limit ({drained_bytes} bytes)]\n"
            )
            continue

        if not line:
            if leftover:
                tail = leftover
                leftover = b""
                _emit_decoded(tail.decode("utf-8", errors="replace"))
            break

        if leftover:
            line = leftover + line
            leftover = b""
        _emit_decoded(line.decode("utf-8", errors="replace"))


async def _fetch_context(api_url: str, run_token: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(
            f"{api_url.rstrip('/')}/api/v1/runner/context",
            headers={"Authorization": f"Bearer {run_token}"},
        )
        resp.raise_for_status()
        data = resp.json()
        assert isinstance(data, dict)
        return data


async def _download_attachments(
    api_url: str, run_token: str, attachments: list[dict[str, Any]], dest_dir: Path
) -> list[dict[str, Any]]:
    """Fetch the seed's attached files into `dest_dir`; returns entries with a local `path`.

    Failures are logged and skipped: a missing reference file shouldn't fail the run.
    """
    if not attachments:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    used_names: set[str] = set()
    async with httpx.AsyncClient(timeout=60.0) as client:
        for att in attachments:
            att_id = str(att.get("id") or "")
            name = Path(str(att.get("filename") or att_id)).name or att_id
            # De-duplicate names ("spec.pdf", "spec (2).pdf")
            stem, suffix = Path(name).stem, Path(name).suffix
            candidate, n = name, 2
            while candidate in used_names:
                candidate, n = f"{stem} ({n}){suffix}", n + 1
            used_names.add(candidate)
            try:
                resp = await client.get(
                    f"{api_url.rstrip('/')}/api/v1/runner/attachments/{att_id}",
                    headers={"Authorization": f"Bearer {run_token}"},
                )
                resp.raise_for_status()
                target = dest_dir / candidate
                target.write_bytes(resp.content)
                saved.append({**att, "filename": candidate, "path": str(target)})
            except Exception as exc:
                logger.warning("Could not download attachment %s (%s): %s", name, att_id, exc)
    if saved:
        logger.info("Downloaded %d seed attachment(s) into %s", len(saved), dest_dir)
    return saved


def _write_related_seeds(
    related_seeds: list[dict[str, Any]], dest_dir: Path
) -> list[dict[str, Any]]:
    """Write full context for each linked seed to `.gitseed/related/seed-<n>.md`."""
    if not related_seeds:
        return []
    dest_dir.mkdir(parents=True, exist_ok=True)
    saved: list[dict[str, Any]] = []
    for rel in related_seeds:
        if not isinstance(rel, dict):
            continue
        num = int(rel.get("number") or 0)
        if num <= 0:
            continue
        title = str(rel.get("title") or "").strip()
        status = str(rel.get("status") or "").strip()
        stage = str(rel.get("stage") or "").strip()
        relation = str(rel.get("relation") or "related").strip()
        branch = str(rel.get("branch_name") or "").strip()
        desc = str(rel.get("description") or "").strip()
        spec = str(rel.get("specification") or "").strip()
        plan = str(rel.get("plan") or "").strip()
        summary = str(rel.get("summary_comment") or "").strip()

        md_path = dest_dir / f"seed-{num}.md"
        md_lines = [
            f"# Seed #{num}: {title}",
            f"- **Relation**: `{relation}`",
            f"- **Status**: `{status}` (stage: `{stage}`)",
        ]
        if branch:
            md_lines.append(f"- **Branch**: `{branch}`")
        if desc:
            md_lines.extend(["", "## Summary", desc])
        if spec:
            md_lines.extend(["", "## Specification", spec])
        if plan:
            md_lines.extend(["", "## Plan", plan])
        if summary:
            md_lines.extend(["", "## Latest Agent Summary", summary])

        md_path.write_text("\n".join(md_lines) + "\n", encoding="utf-8")
        saved.append({**rel, "path": str(md_path)})
    return saved


async def _poll_steering(
    api_url: str,
    run_token: str,
    since_iso: str,
    log_tail: str | None = None,
    *,
    context_tokens: int = 0,
    peak_context_tokens: int = 0,
    turn_count: int = 0,
    input_tokens: int = 0,
    output_tokens: int = 0,
) -> dict[str, Any]:
    params: dict[str, str | int] = {"since": since_iso}
    if log_tail and log_tail.strip():
        params["log_tail"] = log_tail[-4000:]
    if context_tokens > 0:
        params["context_tokens"] = int(context_tokens)
    if peak_context_tokens > 0:
        params["peak_context_tokens"] = int(peak_context_tokens)
    if turn_count > 0:
        params["turn_count"] = int(turn_count)
    if input_tokens > 0:
        params["input_tokens"] = int(input_tokens)
    if output_tokens > 0:
        params["output_tokens"] = int(output_tokens)
    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.get(
            f"{api_url.rstrip('/')}/api/v1/runner/steering",
            params=params,
            headers={"Authorization": f"Bearer {run_token}"},
        )
        if resp.status_code == 200:
            data = resp.json()
            if isinstance(data, dict):
                return data
    return {"should_abort": False, "steering_comments": []}


async def _report_complete(
    api_url: str,
    run_token: str,
    *,
    status: str,
    exit_code: int,
    error: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    context_tokens: int = 0,
    peak_context_tokens: int = 0,
    turn_count: int = 0,
    stage_output: dict[str, Any] | None = None,
    log_tail: str | None = None,
    updated_cli_auth_bundle: dict[str, str] | None = None,
    trusted: dict[str, Any] | None = None,
) -> None:
    payload: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "error": error,
        "input_tokens": max(0, int(input_tokens)),
        "output_tokens": max(0, int(output_tokens)),
        "context_tokens": max(0, int(context_tokens)),
        "peak_context_tokens": max(0, int(peak_context_tokens)),
        "turn_count": max(0, int(turn_count)),
        "stage_output": stage_output or {},
        "log_tail": ("".join(_SUPERVISOR_LOG_LINES) + ("\n" + log_tail if log_tail else ""))[-50000:].strip() or None,
    }
    if updated_cli_auth_bundle:
        payload["updated_cli_auth_bundle"] = updated_cli_auth_bundle
    if trusted is not None:
        payload["trusted"] = trusted
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{api_url.rstrip('/')}/api/v1/runner/complete",
            headers={"Authorization": f"Bearer {run_token}"},
            json=payload,
        )
        resp.raise_for_status()


async def _terminate_process(proc: asyncio.subprocess.Process) -> None:
    if proc.returncode is not None:
        return
    with suppress(ProcessLookupError):
        proc.terminate()
    try:
        await asyncio.wait_for(proc.wait(), timeout=5.0)
    except TimeoutError:
        with suppress(ProcessLookupError):
            proc.kill()
        with suppress(Exception):
            await proc.wait()


def _is_gateway_fallback_allowed(
    sandbox_cfg: dict[str, Any], stage: str | None = None
) -> bool:
    """Return False when strict CLI execution is enabled or gateway synthesis fallback is disabled.

    Code-mutating stages (`execute`, `merge`) require a real CLI binary by default unless
    `allow_gateway_fallback: True` is explicitly configured in `sandbox_config`.
    """
    env_strict = os.environ.get("GITSEED_STRICT_CLI_EXECUTION", "").strip().lower()
    if env_strict in ("1", "true", "yes"):
        return False
    if bool(sandbox_cfg.get("strict_cli_execution", False)):
        return False
    if "allow_gateway_fallback" in sandbox_cfg:
        return bool(sandbox_cfg["allow_gateway_fallback"])
    if stage and stage.strip().lower() in ("execute", "merge"):
        return False
    return True


def _has_active_workspace_writes(working_dir: Path, window_seconds: float = 2.0) -> bool:
    """Return True if a git lockfile exists or a workspace file was modified within `window_seconds`."""
    try:
        git_dir = working_dir / ".git"
        if git_dir.is_dir() and any(git_dir.rglob("*.lock")):
            return True
        cutoff = datetime.now(UTC).timestamp() - window_seconds
        for root, dirs, files in os.walk(working_dir):
            dirs[:] = [
                d
                for d in dirs
                if d not in (".git", ".gitseed", "node_modules", ".venv", "__pycache__")
            ]
            for fname in files:
                fpath = Path(root) / fname
                with suppress(OSError):
                    if fpath.stat().st_mtime >= cutoff:
                        return True
    except Exception:
        return False
    return False


async def _wait_for_workspace_quiescence(
    proc: asyncio.subprocess.Process,
    working_dir: Path,
    max_wait_seconds: float = 3.0,
) -> None:
    """Wait briefly for in-flight file writes or git lockfiles to settle before interrupting."""
    deadline = asyncio.get_running_loop().time() + max_wait_seconds
    while proc.returncode is None and asyncio.get_running_loop().time() < deadline:
        if not _has_active_workspace_writes(working_dir):
            break
        await asyncio.sleep(0.5)


async def _run_cli_with_steering(
    *,
    adapter: RuntimeAdapter,
    env: dict[str, str],
    initial_prompt: str,
    api_url: str,
    run_token: str,
    continue_session: bool = False,
    allow_gateway_fallback: bool = True,
    since_iso: str | None = None,
) -> tuple[int, str, str | None]:
    """Execute the agent CLI and resume with `--continue` if @mention steering arrives."""
    if not adapter.is_binary_available():
        if not allow_gateway_fallback:
            err_msg = (
                f"Runtime binary '{adapter.binary_name}' is not installed in this runner image. "
                "opencode, codex, and gemini are always included. claude-code is only included in "
                "self-built images, and antigravity (agy) and grok-build (grok) are "
                "bring-your-own. Pick an installed runtime, or extend the runner image "
                "(see apps/runner/README.md#third-party-agent-clis)."
            )
            logger.error(err_msg)
            return 127, "", err_msg
        logger.info(
            "CLI binary '%s' not found on PATH; synthesizing output via Model Gateway",
            adapter.binary_name,
        )
        ok = await synthesize_output_via_gateway(
            adapter=adapter, prompt=initial_prompt
        )
        err = None if ok else f"Binary '{adapter.binary_name}' not installed"
        return (0 if ok else 1), "", err

    current_prompt = initial_prompt
    collected_logs: list[str] = []
    last_checked_iso = since_iso or datetime.now(UTC).isoformat()

    while True:
        cmd = adapter.build_command(current_prompt, continue_session=continue_session)
        logger.info(
            "Starting agent subprocess (%s, continue=%s)",
            adapter.binary_name,
            continue_session,
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=str(adapter.working_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            limit=_SUBPROCESS_STREAM_LIMIT,
        )

        stdout_chunks: list[str] = []
        assert proc.stdout is not None
        reader_task = asyncio.create_task(
            _stream_pipe(proc.stdout, stdout_chunks, adapter)
        )
        steered_comments: list[dict[str, Any]] = []
        abort_reason: str | None = None

        try:
            while proc.returncode is None:
                try:
                    await asyncio.wait_for(proc.wait(), timeout=_POLL_INTERVAL_SECONDS)
                    break
                except TimeoutError:
                    pass

                # Poll Control Plane for abort or @agent steering comments + stream live stdout tail
                try:
                    sup_prefix = "".join(_SUPERVISOR_LOG_LINES)
                    live_tail = (sup_prefix + "".join(collected_logs) + "".join(stdout_chunks))[-8000:]
                    poll_data = await _poll_steering(
                        api_url,
                        run_token,
                        last_checked_iso,
                        log_tail=live_tail,
                        context_tokens=adapter.context_tokens,
                        peak_context_tokens=adapter.peak_context_tokens,
                        turn_count=adapter.turn_count,
                        input_tokens=adapter.input_tokens,
                        output_tokens=adapter.output_tokens,
                    )
                except Exception:
                    continue

                if poll_data.get("should_abort"):
                    abort_reason = str(
                        poll_data.get("abort_reason") or "Run aborted by control plane"
                    )
                    logger.warning("Aborting agent subprocess: %s", abort_reason)
                    await _terminate_process(proc)
                    break

                new_comments = poll_data.get("steering_comments") or []
                if isinstance(new_comments, list) and new_comments:
                    logger.info(
                        "Received @agent steering comment(s); waiting %.0fs debounce window...",
                        _STEERING_DEBOUNCE_SECONDS,
                    )
                    steered_comments.extend(new_comments)
                    last_checked_iso = str(
                        new_comments[-1].get("created_at")
                        or datetime.now(UTC).isoformat()
                    )

                    # Wait 10 seconds so rapid follow-up messages batch into one instruction
                    await asyncio.sleep(_STEERING_DEBOUNCE_SECONDS)
                    try:
                        more_data = await _poll_steering(
                            api_url, run_token, last_checked_iso
                        )
                        more_comments = more_data.get("steering_comments") or []
                        if isinstance(more_comments, list) and more_comments:
                            steered_comments.extend(more_comments)
                            last_checked_iso = str(
                                more_comments[-1].get("created_at")
                                or datetime.now(UTC).isoformat()
                            )
                    except Exception:
                        pass

                    if proc.returncode is None:
                        logger.info(
                            "Stopping CLI pass to resume session with %d steering comment(s)",
                            len(steered_comments),
                        )
                        await _wait_for_workspace_quiescence(proc, adapter.working_dir)
                        await _terminate_process(proc)
                        cleanup_git_locks({"workspace": adapter.working_dir})
                    break
        finally:
            with suppress(Exception):
                await reader_task

        collected_logs.extend(stdout_chunks)

        if abort_reason is not None:
            cleanup_git_locks({"workspace": adapter.working_dir})
            return 130, "".join(collected_logs), abort_reason

        if steered_comments:
            current_prompt = build_steering_continuation_prompt(
                steered_comments, adapter.output_file
            )
            continue_session = True
            continue

        return int(proc.returncode or 0), "".join(collected_logs), None


def _resolve_verification_commands(
    primary_dir: Path,
    sandbox_cfg: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Resolve optional setup_command and test_command from project sandbox_config or .gitseed/run.json."""
    setup_cmd = str(sandbox_cfg.get("setup_command") or "").strip() or None
    test_cmd = str(sandbox_cfg.get("test_command") or "").strip() or None
    if not test_cmd:
        run_json_path = primary_dir / ".gitseed" / "run.json"
        if run_json_path.is_file():
            try:
                loaded = json.loads(run_json_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    if not setup_cmd and loaded.get("setup_command"):
                        setup_cmd = str(loaded["setup_command"]).strip() or None
                    if loaded.get("test_command"):
                        test_cmd = str(loaded["test_command"]).strip() or None
            except Exception:
                pass
    return setup_cmd, test_cmd


_DEPENDENCY_MANIFEST_FILES = (
    "pyproject.toml",
    "uv.lock",
    "poetry.lock",
    "requirements.txt",
    "requirements-dev.txt",
    "package.json",
    "package-lock.json",
    "pnpm-lock.yaml",
    "yarn.lock",
    "bun.lockb",
    "bun.lock",
    "Cargo.toml",
    "Cargo.lock",
    "go.mod",
    "go.sum",
    "Gemfile",
    "Gemfile.lock",
)


def _compute_dependency_fingerprint(
    primary_dir: Path,
    setup_command: str | None,
) -> str:
    """Compute a SHA-256 fingerprint of `setup_command` and lockfiles in `primary_dir`."""
    if not setup_command or not primary_dir.is_dir():
        return ""
    hasher = hashlib.sha256()
    hasher.update(setup_command.strip().encode("utf-8"))
    found_manifest = False
    for rel_name in _DEPENDENCY_MANIFEST_FILES:
        f_path = primary_dir / rel_name
        if f_path.is_file():
            try:
                content = f_path.read_bytes()
                hasher.update(f"\n---{rel_name}---\n".encode())
                hasher.update(content)
                found_manifest = True
            except OSError:
                continue
    return hasher.hexdigest() if found_manifest else ""


async def _run_deterministic_verification_gate(
    *,
    primary_dir: Path,
    env: dict[str, str],
    setup_command: str | None,
    test_command: str,
    timeout_seconds: float = 300.0,
) -> tuple[bool, int, str]:
    """Execute the deterministic supervisor test gate and return (passed, exit_code, combined_output).

    Caches lockfile fingerprints in `.gitseed/deps.sha256` on warm workspaces so unchanged
    dependencies skip re-running `setup_command` (with automatic fallback if `test_command` fails).
    """
    stamp_file = primary_dir / ".gitseed" / "deps.sha256"
    current_fp = _compute_dependency_fingerprint(primary_dir, setup_command)
    cached_fp = ""
    if current_fp and stamp_file.is_file():
        with suppress(OSError):
            cached_fp = stamp_file.read_text(encoding="utf-8").strip()

    skip_setup = bool(setup_command and current_fp and cached_fp == current_fp)
    if skip_setup:
        logger.info(
            "Lockfile fingerprint unchanged (%s); skipping setup_command on warm workspace",
            current_fp[:12],
        )

    async def _exec_shell(cmd_str: str) -> tuple[bool, int, str]:
        proc = await asyncio.create_subprocess_shell(
            cmd_str,
            cwd=str(primary_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            stdout_bytes, _ = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_seconds
            )
        except TimeoutError:
            await _terminate_process(proc)
            return (
                False,
                124,
                f"Deterministic verification gate timed out after {int(timeout_seconds)}s: {test_command}",
            )
        out_text = (stdout_bytes or b"").decode("utf-8", errors="replace")
        code = int(proc.returncode or 0)
        return (code == 0), code, out_text

    if skip_setup:
        logger.info("Running deterministic supervisor verification gate: %s", test_command)
        passed, exit_code, output_text = await _exec_shell(test_command)
        if passed:
            return True, exit_code, output_text
        logger.info(
            "Verification gate without setup_command exited %d; retrying with setup_command...",
            exit_code,
        )

    full_cmd = (
        f"{setup_command} && {test_command}"
        if setup_command
        else test_command
    )
    logger.info("Running deterministic supervisor verification gate: %s", test_command)
    passed, exit_code, output_text = await _exec_shell(full_cmd)
    if passed and current_fp:
        with suppress(OSError):
            stamp_file.parent.mkdir(parents=True, exist_ok=True)
            stamp_file.write_text(current_fp, encoding="utf-8")
    return passed, exit_code, output_text


def _load_and_validate_output_file(
    stage: str,
    output_file: Path,
    *,
    min_acceptance_criteria: int | None = None,
    max_acceptance_criteria: int | None = None,
    max_tasks: int | None = None,
    enforce_bounds: bool = False,
) -> tuple[dict[str, Any] | None, str | None]:
    if not output_file.exists():
        return None, f"Output file {output_file} was not created."
    try:
        raw_text = output_file.read_text(encoding="utf-8")
        raw_obj = json.loads(raw_text)
        if not isinstance(raw_obj, dict):
            return None, "Output file root must be a JSON object."
        validated = validate_stage_output_data(
            stage,
            raw_obj,
            min_acceptance_criteria=min_acceptance_criteria,
            max_acceptance_criteria=max_acceptance_criteria,
            max_tasks=max_tasks,
            enforce_bounds=enforce_bounds,
        )
        return validated, None
    except (json.JSONDecodeError, ValidationError, ValueError) as exc:
        return None, str(exc)


async def _finish_agent_or_report(
    *,
    is_broker_agent_mode: bool,
    run_id: str,
    api_url: str,
    run_token: str,
    status: str,
    exit_code: int,
    error: str | None = None,
    input_tokens: int = 0,
    output_tokens: int = 0,
    context_tokens: int = 0,
    peak_context_tokens: int = 0,
    turn_count: int = 0,
    stage_output: dict[str, Any] | None = None,
    log_tail: str | None = None,
    updated_cli_auth_bundle: dict[str, str] | None = None,
    verification: dict[str, Any] | None = None,
) -> None:
    """In `agent` broker mode, write result & marker to /gitseed-ctl (and refresh CLI auth if needed); otherwise call _report_complete."""
    if not is_broker_agent_mode:
        await _report_complete(
            api_url,
            run_token,
            status=status,
            exit_code=exit_code,
            error=error,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            context_tokens=context_tokens,
            peak_context_tokens=peak_context_tokens,
            turn_count=turn_count,
            stage_output=stage_output,
            log_tail=log_tail,
            updated_cli_auth_bundle=updated_cli_auth_bundle,
        )
        return

    if updated_cli_auth_bundle and run_token:
        with suppress(Exception):
            await post_cli_auth_refresh(api_url, run_token, updated_cli_auth_bundle)

    ctl_dir = get_ctl_dir()
    ctl_dir.mkdir(parents=True, exist_ok=True)
    combined_logs = (
        ("".join(_SUPERVISOR_LOG_LINES) + ("\n" + log_tail if log_tail else ""))[-50000:].strip()
        or None
    )
    if stage_output:
        (ctl_dir / "output.json").write_text(
            json.dumps(stage_output, indent=2), encoding="utf-8"
        )
    result_payload: dict[str, Any] = {
        "status": status,
        "exit_code": exit_code,
        "error": error,
        "input_tokens": max(0, int(input_tokens)),
        "output_tokens": max(0, int(output_tokens)),
        "context_tokens": max(0, int(context_tokens)),
        "peak_context_tokens": max(0, int(peak_context_tokens)),
        "turn_count": max(0, int(turn_count)),
        "stage_output": stage_output or {},
        "verification": verification or {"source": "agent_reported"},
        "log_tail": combined_logs,
    }
    (ctl_dir / "agent-result.json").write_text(
        json.dumps(result_payload, indent=2), encoding="utf-8"
    )
    effective_id = run_id or os.environ.get("GITSEED_RUN_ID", "run").strip() or "run"
    write_agent_done_marker(ctl_dir, effective_id, exit_code, error=error)


async def run_container_job() -> int:
    _SUPERVISOR_LOG_LINES.clear()
    runner_mode = os.environ.get("GITSEED_RUNNER_MODE", "").strip().lower()
    if runner_mode == "prepare":
        return await run_prepare_mode(
            fetch_context_fn=_fetch_context,
            download_attachments_fn=_download_attachments,
            write_related_seeds_fn=_write_related_seeds,
        )
    if runner_mode == "finalize":
        return await run_finalize_mode(
            report_complete_fn=_report_complete,
        )
    if runner_mode == "agent":
        return await run_agent_phase(
            lambda: _run_agent_or_standalone_job(is_broker_agent_mode=True)
        )
    return await _run_agent_or_standalone_job(is_broker_agent_mode=False)


async def _run_agent_or_standalone_job(*, is_broker_agent_mode: bool) -> int:
    api_url = os.environ.get("GITSEED_API_URL", "http://api:8420").strip()
    run_token = os.environ.get("GITSEED_RUN_TOKEN", "").strip()
    if not run_token and not is_broker_agent_mode:
        logger.error("GITSEED_RUN_TOKEN environment variable is required")
        return 2

    # Scrub token from os.environ so child agent processes only see harness-specific vars
    os.environ.pop("GITSEED_RUN_TOKEN", None)

    context_loaded_iso = datetime.now(UTC).isoformat()
    ctl_dir = get_ctl_dir()
    run_id_env = os.environ.get("GITSEED_RUN_ID", "").strip()

    if is_broker_agent_mode:
        wait_secs = float(os.environ.get("GITSEED_PREPARE_WAIT_SECONDS", "300"))
        ctx_file = ctl_dir / "agent-context.json"
        for _ in range(int(max(1.0, wait_secs) * 4)):
            if ctx_file.is_file() and (
                (run_id_env and (ctl_dir / f"{run_id_env}.prepared").exists())
                or list(ctl_dir.glob("*.prepared"))
            ):
                break
            await asyncio.sleep(0.25)
        if not ctx_file.is_file():
            err_msg = "Agent mode timed out waiting for prepare container context (/gitseed-ctl/agent-context.json)"
            logger.error(err_msg)
            await _finish_agent_or_report(
                is_broker_agent_mode=True,
                run_id=run_id_env or "run",
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=1,
                error=err_msg,
                log_tail=err_msg,
            )
            return 1
        ctx = json.loads(ctx_file.read_text(encoding="utf-8"))
    else:
        logger.info("Fetching run context from Control Plane...")
        ctx = await _fetch_context(api_url, run_token)

    run_id = str(ctx.get("run_id") or run_id_env or "run").strip()
    stage = str(ctx.get("stage") or "ideate").lower()
    seed_number = int(ctx.get("seed_number") or 1)
    project_slug = str(
        ctx.get("project_slug") or os.environ.get("GITSEED_PROJECT_SLUG") or ""
    ).strip()
    logger.info(
        "Loaded context for Seed #%d (stage=%s, agent=@%s, runtime=%s, model=%s)",
        seed_number,
        stage,
        ctx.get("agent_handle") or "agent",
        ctx.get("runtime") or "opencode",
        ctx.get("model") or "default",
    )
    repositories = ctx.get("repositories") or []
    raw_sbx = ctx.get("sandbox_config")
    sandbox_cfg: dict[str, Any] = raw_sbx if isinstance(raw_sbx, dict) else {}
    allow_gateway_fallback = _is_gateway_fallback_allowed(sandbox_cfg, stage=stage)

    raw_depth = sandbox_cfg.get("git_clone_depth")
    try:
        clone_depth = int(raw_depth) if raw_depth is not None else 50
    except (TypeError, ValueError):
        clone_depth = 50
    auto_rebase = bool(sandbox_cfg.get("auto_rebase_seed_branch", True))
    warm_workspace = bool(sandbox_cfg.get("enable_warm_workspace", True))

    workspace_root = Path(os.environ.get("GITSEED_WORKSPACE", "/workspace"))
    repo_list = repositories if isinstance(repositories, list) else []

    if is_broker_agent_mode:
        if ctx.get("_prepare_error"):
            prep_err = str(ctx["_prepare_error"])
            logger.error(prep_err)
            await _finish_agent_or_report(
                is_broker_agent_mode=True,
                run_id=run_id,
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=1,
                error=prep_err,
                log_tail=prep_err,
            )
            return 1
        primary_dir, repo_dirs = resolve_existing_repo_dirs(workspace_root, repo_list)
    else:
        clone_kwargs: dict[str, Any] = {
            "stage": stage,
            "seed_number": seed_number,
            "project_slug": project_slug,
        }
        if clone_depth != 50:
            clone_kwargs["clone_depth"] = clone_depth
        if not auto_rebase:
            clone_kwargs["auto_rebase"] = False
        if not warm_workspace:
            clone_kwargs["warm_workspace"] = False

        primary_dir, repo_dirs = clone_project_repositories(
            workspace_root,
            repo_list,
            **clone_kwargs,
        )
        if repo_list and not repo_dirs and stage in ("execute", "review", "merge"):
            err_msg = (
                "Failed to clone project repository into /workspace. "
                "Verify the repository URL and Git backend token permissions."
            )
            logger.error(err_msg)
            await _finish_agent_or_report(
                is_broker_agent_mode=is_broker_agent_mode,
                run_id=run_id,
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=1,
                error=err_msg,
                log_tail=err_msg,
            )
            return 1

        if (
            stage in ("review", "merge")
            and repo_dirs
            and not has_remote_seed_branch(repo_dirs, project_slug, seed_number)
        ):
            seed_branch = format_seed_branch(project_slug, seed_number)
            err_msg = (
                f"Seed branch '{seed_branch}' does not exist on the remote repository. "
                "Move the seed back to Execute to push the branch before reviewing."
            )
            logger.error(err_msg)
            await _finish_agent_or_report(
                is_broker_agent_mode=is_broker_agent_mode,
                run_id=run_id,
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=1,
                error=err_msg,
                log_tail=err_msg,
            )
            return 1

    initial_heads = get_repo_heads(repo_dirs)

    output_file = workspace_root / ".gitseed" / "output.json"
    output_file.parent.mkdir(parents=True, exist_ok=True)
    if output_file.exists():
        output_file.unlink()

    if not is_broker_agent_mode or not ctx.get("attachments_local"):
        # Seed attachments: reference files the agent opens as needed (listed, not inlined, in the prompt)
        ctx["attachments_local"] = await _download_attachments(
            api_url,
            run_token,
            [a for a in (ctx.get("attachments") or []) if isinstance(a, dict)],
            workspace_root / ".gitseed" / "attachments",
        )
    if not is_broker_agent_mode or not ctx.get("related_seeds_local"):
        # Related seeds: full spec/plan written to disk so the prompt only lists a compact pointer
        ctx["related_seeds_local"] = _write_related_seeds(
            [r for r in (ctx.get("related_seeds") or []) if isinstance(r, dict)],
            workspace_root / ".gitseed" / "related",
        )

    adapter = create_runtime_adapter(
        ctx=ctx,
        working_dir=primary_dir,
        output_file=output_file,
    )
    child_env = adapter.prepare_workspace()
    child_env.pop("GITSEED_RUN_TOKEN", None)
    try:
        return await _run_prepared_stage_session(
            ctx=ctx,
            adapter=adapter,
            child_env=child_env,
            is_broker_agent_mode=is_broker_agent_mode,
            run_id=run_id,
            api_url=api_url,
            run_token=run_token,
            stage=stage,
            seed_number=seed_number,
            project_slug=project_slug,
            workspace_root=workspace_root,
            primary_dir=primary_dir,
            repo_dirs=repo_dirs,
            repo_list=repo_list,
            initial_heads=initial_heads,
            output_file=output_file,
            sandbox_cfg=sandbox_cfg,
            allow_gateway_fallback=allow_gateway_fallback,
            context_loaded_iso=context_loaded_iso,
        )
    finally:
        adapter.cleanup_workspace()


async def _run_prepared_stage_session(
    *,
    ctx: dict[str, Any],
    adapter: RuntimeAdapter,
    child_env: dict[str, str],
    is_broker_agent_mode: bool,
    run_id: str,
    api_url: str,
    run_token: str,
    stage: str,
    seed_number: int,
    project_slug: str,
    workspace_root: Path,
    primary_dir: Path,
    repo_dirs: dict[str, Path],
    repo_list: list[Any],
    initial_heads: dict[str, str],
    output_file: Path,
    sandbox_cfg: dict[str, Any],
    allow_gateway_fallback: bool,
    context_loaded_iso: str,
) -> int:

    git_diff_summary = (
        collect_workspace_git_summary(repo_dirs, repo_list)
        if stage in ("execute", "review", "merge") and repo_dirs
        else None
    )
    initial_prompt = build_stage_prompt(
        ctx,
        output_file,
        workspace_dir=(workspace_root if len(repo_dirs) > 1 else primary_dir)
        if repo_dirs
        else None,
        git_diff_summary=git_diff_summary,
    )
    logger.info("Stage prompt: %s", summarize_prompt_stats())
    cli_kwargs: dict[str, Any] = {"since_iso": context_loaded_iso}
    if not allow_gateway_fallback:
        cli_kwargs["allow_gateway_fallback"] = False

    exit_code, logs, abort_err = await _run_cli_with_steering(
        adapter=adapter,
        env=child_env,
        initial_prompt=initial_prompt,
        api_url=api_url,
        run_token=run_token,
        **cli_kwargs,
    )

    if abort_err is not None:
        with suppress(Exception):
            await _finish_agent_or_report(
                is_broker_agent_mode=is_broker_agent_mode,
                run_id=run_id,
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=exit_code,
                error=abort_err,
                log_tail=logs,
            )
        return 1

    raw_min_crit = ctx.get("min_acceptance_criteria")
    raw_max_crit = ctx.get("max_acceptance_criteria")
    raw_max_tasks = ctx.get("max_tasks")
    policy_bounds_kwargs: dict[str, Any] = {
        "min_acceptance_criteria": int(raw_min_crit) if raw_min_crit is not None else None,
        "max_acceptance_criteria": int(raw_max_crit) if raw_max_crit is not None else None,
        "max_tasks": int(raw_max_tasks) if raw_max_tasks is not None else None,
    }

    validated_output, val_err = _load_and_validate_output_file(
        stage, output_file, **policy_bounds_kwargs, enforce_bounds=True
    )

    # The agent CLI stopped on a provider error (quota, auth, rate limit): fail clearly,
    # don't re-prompt or "synthesize" a stage result out of the error text.
    fatal_err = detect_fatal_agent_error(logs) if validated_output is None else None
    if fatal_err:
        runtime_label = str(ctx.get("runtime") or "agent")
        err_msg = f"{runtime_label}: {fatal_err}"
        logger.error("Agent CLI stopped on a provider error: %s", fatal_err)
        await _finish_agent_or_report(
            is_broker_agent_mode=is_broker_agent_mode,
            run_id=run_id,
            api_url=api_url,
            run_token=run_token,
            status="failed",
            exit_code=exit_code or 1,
            error=err_msg,
            log_tail=logs,
        )
        return 1

    # Check if the CLI printed a valid stage JSON object directly to stdout
    if validated_output is None and logs.strip():
        stdout_json = extract_json_object(logs)
        if stdout_json is not None:
            with suppress(Exception):
                validated_output = validate_stage_output_data(
                    stage,
                    stdout_json,
                    **policy_bounds_kwargs,
                    enforce_bounds=True,
                )
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_text(
                    json.dumps(validated_output, indent=2), encoding="utf-8"
                )
                val_err = None

    # Re-prompt once if output.json is missing, failed Pydantic validation, or exceeded policy bounds
    if validated_output is None:
        logger.warning(
            "Stage output validation failed on first pass (%s); attempting recovery...",
            val_err,
        )
        repair_prompt = build_validation_repair_prompt(
            val_err or "Missing output.json", stage, output_file
        )
        if exit_code == 0 and adapter.is_binary_available():
            r_code, r_logs, _ = await _run_cli_with_steering(
                adapter=adapter,
                env=child_env,
                initial_prompt=repair_prompt,
                api_url=api_url,
                run_token=run_token,
                continue_session=True,
                **cli_kwargs,
            )
            logs += "\n" + r_logs
            exit_code = r_code
            # Soft fallback after repair: accept structurally valid JSON even if count still exceeds bounds
            validated_output, val_err = _load_and_validate_output_file(
                stage, output_file, **policy_bounds_kwargs, enforce_bounds=False
            )
            if validated_output is not None and validated_output.get("policy_limit_warning"):
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_text(
                    json.dumps(validated_output, indent=2), encoding="utf-8"
                )

        # If the CLI still didn't write a valid file, synthesize via LiteLLM Gateway (unless strict mode)
        if validated_output is None and allow_gateway_fallback:
            need_files = stage == "execute" and bool(repo_dirs) and not has_workspace_code_changes(
                repo_dirs, initial_heads
            )
            await synthesize_output_via_gateway(
                adapter=adapter,
                # Compact context + the CLI's own answer: re-sending the whole stage prompt
                # (repo tree, diff, comments…) to reformat an answer wastes quota.
                prompt=f"{build_synthesis_context(ctx)}\n\n{repair_prompt}",
                cli_stdout=logs,
                stage=stage,
                require_files=need_files,
            )
            validated_output, val_err = _load_and_validate_output_file(
                stage, output_file, **policy_bounds_kwargs, enforce_bounds=False
            )

    # In execute stage, ensure the agent actually created or modified files in /workspace
    if (
        stage == "execute"
        and validated_output is not None
        and repo_dirs
        and not has_workspace_code_changes(repo_dirs, initial_heads)
    ):
        logger.warning(
            "Execute stage wrote output.json without modifying any repository files; requesting implementation files..."
        )
        code_prompt = (
            f"{initial_prompt}\n\n"
            "## MISSING WORKSPACE IMPLEMENTATION FILES\n"
            "You wrote `output.json`, but no source or test files in `/workspace` were created or modified. "
            "You MUST write the actual implementation files for the active task into `/workspace` now."
        )
        if adapter.is_binary_available():
            r_code, r_logs, _ = await _run_cli_with_steering(
                adapter=adapter,
                env=child_env,
                initial_prompt=code_prompt,
                api_url=api_url,
                run_token=run_token,
                continue_session=True,
                **cli_kwargs,
            )
            logs += "\n" + r_logs
            exit_code = r_code
            re_out, _ = _load_and_validate_output_file(stage, output_file)
            if re_out is not None:
                validated_output = re_out

        if not has_workspace_code_changes(repo_dirs, initial_heads):
            if allow_gateway_fallback:
                await synthesize_output_via_gateway(
                    adapter=adapter,
                    prompt=code_prompt,
                    cli_stdout=logs,
                    stage=stage,
                    require_files=True,
                )
                re_out, _ = _load_and_validate_output_file(stage, output_file)
                if re_out is not None:
                    validated_output = re_out
            else:
                err_msg = (
                    "Strict CLI execution enabled: Execute stage completed without modifying "
                    "any repository files in /workspace."
                )
                logger.error(err_msg)
                await _finish_agent_or_report(
                    is_broker_agent_mode=is_broker_agent_mode,
                    run_id=run_id,
                    api_url=api_url,
                    run_token=run_token,
                    status="failed",
                    exit_code=exit_code or 1,
                    error=err_msg,
                    log_tail=logs,
                )
                return 1

    if validated_output is None:
        late_fatal = detect_fatal_agent_error(logs)
        err_msg = (
            f"{ctx.get('runtime') or 'agent'}: {late_fatal}"
            if late_fatal
            else f"Agent failed to produce valid {output_file}: {val_err}"
        )
        logger.error(err_msg)
        await _finish_agent_or_report(
            is_broker_agent_mode=is_broker_agent_mode,
            run_id=run_id,
            api_url=api_url,
            run_token=run_token,
            status="failed",
            exit_code=exit_code or 1,
            error=err_msg,
            log_tail=logs,
        )
        return 1

    # Fast-track Ideate -> Plan inside the same warm container & agent session when eligible
    if (
        stage == "ideate"
        and bool(sandbox_cfg.get("fast_track_ideate_to_plan", False))
        and adapter.is_binary_available()
    ):
        should_skip_fast_track = False
        try:
            poll_check = await _poll_steering(
                api_url, run_token, datetime.now(UTC).isoformat()
            )
            if poll_check.get("should_abort"):
                should_skip_fast_track = True
        except Exception:
            pass
        if not should_skip_fast_track:
            logger.info(
                "Fast-tracking IDEATE -> PLAN in warm container & agent session..."
            )
            if output_file.exists():
                output_file.unlink()
            plan_prompt = build_ideate_to_plan_continuation_prompt(
                validated_output,
                output_file,
                max_tasks=policy_bounds_kwargs.get("max_tasks"),
            )
            p_code, p_logs, p_abort = await _run_cli_with_steering(
                adapter=adapter,
                env=child_env,
                initial_prompt=plan_prompt,
                api_url=api_url,
                run_token=run_token,
                continue_session=True,
                **cli_kwargs,
            )
            logs += "\n" + p_logs
            if p_abort is None and p_code == 0:
                plan_out, _ = _load_and_validate_output_file(
                    "plan", output_file, **policy_bounds_kwargs, enforce_bounds=False
                )
                if plan_out is None and p_logs.strip():
                    stdout_plan = extract_json_object(p_logs)
                    if stdout_plan is not None:
                        with suppress(Exception):
                            plan_out = validate_stage_output_data(
                                "plan",
                                stdout_plan,
                                **policy_bounds_kwargs,
                                enforce_bounds=False,
                            )
                if plan_out is not None:
                    validated_output["fast_tracked_plan"] = plan_out
                    logger.info(
                        "Fast-tracked PLAN completed with %d task(s).",
                        len(plan_out.get("tasks") or []),
                    )
            output_file.parent.mkdir(parents=True, exist_ok=True)
            output_file.write_text(
                json.dumps(validated_output, indent=2), encoding="utf-8"
            )

    # Continuous multi-task batch execution inside the same warm container & agent session
    active_task_id = str(ctx.get("task_id") or "").strip() or None
    completed_task_ids: list[str] = [active_task_id] if active_task_id else []
    batch_execute = bool(sandbox_cfg.get("batch_execute_tasks", True))
    parallel_execute = bool(sandbox_cfg.get("parallel_execute_tasks", True))
    raw_tasks = ctx.get("tasks")
    if (
        stage == "execute"
        and batch_execute
        and active_task_id
        and isinstance(raw_tasks, list)
        and adapter.is_binary_available()
    ):
        remaining_tasks = [
            t
            for t in raw_tasks
            if isinstance(t, dict)
            and str(t.get("id") or "").strip()
            and str(t.get("id")) != active_task_id
            and str(t.get("status") or "pending").lower() == "pending"
        ]
        remaining_tasks.sort(key=lambda t: int(t.get("position", 0)))
        idx = 0
        while idx < len(remaining_tasks):
            # Check for abort or budget block before starting next task(s) in batch
            try:
                poll_check = await _poll_steering(
                    api_url, run_token, datetime.now(UTC).isoformat()
                )
                if poll_check.get("should_abort"):
                    break
            except Exception:
                pass

            # Group consecutive pending tasks whose target_files are mutually disjoint (up to 3)
            parallel_group: list[dict[str, Any]] = [remaining_tasks[idx]]
            if parallel_execute and (primary_dir / ".git").exists():
                for candidate in remaining_tasks[idx + 1 : idx + 3]:
                    if all(
                        tasks_have_disjoint_targets(existing, candidate)
                        for existing in parallel_group
                    ):
                        parallel_group.append(candidate)
                    else:
                        break

            if len(parallel_group) > 1:
                logger.info(
                    "Executing %d disjoint tasks in parallel git sub-worktrees: %s",
                    len(parallel_group),
                    ", ".join(str(t.get("title") or t.get("id")) for t in parallel_group),
                )
                # Commit any uncommitted primary_dir work so sub-worktrees branch from a clean HEAD
                cleanup_git_locks(repo_dirs)
                _run_git(["add", "-A"], primary_dir)
                _exclude_internal_paths(primary_dir, remove_worktree_files=False)
                if _run_git(["diff", "--cached", "--quiet"], primary_dir).returncode != 0:
                    _run_git(
                        [
                            "commit",
                            "-m",
                            f"feat(seed-{seed_number}): checkpoint before parallel tasks",
                        ],
                        primary_dir,
                    )

                wt_root = workspace_root / ".gitseed-worktrees"
                wt_homes_root = _get_worktree_homes_root()
                wt_homes_root.mkdir(parents=True, mode=0o700, exist_ok=True)
                with suppress(OSError):
                    os.chmod(wt_homes_root, 0o700)
                base_home = Path.home()
                created_wt_homes: list[Path] = []
                wt_specs: list[
                    tuple[
                        dict[str, Any],
                        Path,
                        str,
                        Path,
                        Path,
                        RuntimeAdapter,
                        dict[str, str],
                    ]
                ] = []
                try:
                    for wt_t in parallel_group:
                        tid = str(wt_t["id"])
                        safe_tid = "".join(c for c in tid if c.isalnum())[:10] or "task"
                        wt_dir = wt_root / f"wt-{safe_tid}"
                        wt_branch = f"gitseed-wt/{seed_number}-{safe_tid}"
                        wt_out_file = wt_dir / ".gitseed" / "output.json"
                        if create_task_worktree(primary_dir, wt_dir, wt_branch):
                            wt_out_file.parent.mkdir(parents=True, exist_ok=True)
                            wt_home = wt_homes_root / f"home-{safe_tid}"
                            if wt_home.exists():
                                shutil.rmtree(wt_home, ignore_errors=True)
                            wt_home.mkdir(parents=True, mode=0o700, exist_ok=True)
                            with suppress(OSError):
                                os.chmod(wt_home, 0o700)
                            created_wt_homes.append(wt_home)
                            for dot_name in (
                                ".claude",
                                ".codex",
                                ".gemini",
                                ".grok",
                                ".config",
                                ".local",
                            ):
                                src_dot = base_home / dot_name
                                dst_dot = wt_home / dot_name
                                if src_dot.is_dir() and not dst_dot.exists():
                                    with suppress(OSError):
                                        shutil.copytree(src_dot, dst_dot, dirs_exist_ok=True)
                            wt_adapter = create_runtime_adapter(
                                ctx=ctx,
                                working_dir=wt_dir,
                                output_file=wt_out_file,
                            )
                            prev_home = os.environ.get("HOME")
                            prev_userprofile = os.environ.get("USERPROFILE")
                            try:
                                os.environ["HOME"] = str(wt_home)
                                os.environ["USERPROFILE"] = str(wt_home)
                                wt_env = dict(wt_adapter.prepare_workspace())
                            finally:
                                if prev_home is None:
                                    os.environ.pop("HOME", None)
                                else:
                                    os.environ["HOME"] = prev_home
                                if prev_userprofile is None:
                                    os.environ.pop("USERPROFILE", None)
                                else:
                                    os.environ["USERPROFILE"] = prev_userprofile
                            wt_env["HOME"] = str(wt_home)
                            wt_env["USERPROFILE"] = str(wt_home)
                            wt_env.pop("GITSEED_RUN_TOKEN", None)
                            wt_specs.append(
                                (
                                    wt_t,
                                    wt_dir,
                                    wt_branch,
                                    wt_out_file,
                                    wt_home,
                                    wt_adapter,
                                    wt_env,
                                )
                            )

                    if len(wt_specs) == 1:
                        wt_specs[0][5].cleanup_workspace()
                        cleanup_task_worktree(primary_dir, wt_specs[0][1], wt_specs[0][2])

                    if len(wt_specs) > 1:
                        async def _run_worktree_task(
                            t_item: dict[str, Any],
                            w_out: Path,
                            w_home: Path,
                            w_adapter: RuntimeAdapter,
                            w_env: dict[str, str],
                        ) -> tuple[int, str, str | None, dict[str, Any] | None]:
                            try:
                                w_prompt = build_next_task_continuation_prompt(t_item, w_out)
                                c_code, c_logs, c_abort = await _run_cli_with_steering(
                                    adapter=w_adapter,
                                    env=w_env,
                                    initial_prompt=w_prompt,
                                    api_url=api_url,
                                    run_token=run_token,
                                    continue_session=False,
                                    **cli_kwargs,
                                )
                                w_validated, _ = _load_and_validate_output_file(stage, w_out)
                                return c_code, c_logs, c_abort, w_validated
                            finally:
                                w_adapter.cleanup_workspace()
                                shutil.rmtree(w_home, ignore_errors=True)

                        wt_results = await asyncio.gather(
                            *[
                                _run_worktree_task(t_item, w_out, w_home, w_adapter, w_env)
                                for (
                                    t_item,
                                    _,
                                    _,
                                    w_out,
                                    w_home,
                                    w_adapter,
                                    w_env,
                                ) in wt_specs
                            ]
                        )
                        any_failed = False
                        for (t_item, wt_dir, wt_branch, _, _, wt_adapter, _), (
                            c_code,
                            c_logs,
                            c_abort,
                            w_validated,
                        ) in zip(wt_specs, wt_results, strict=False):
                            tid = str(t_item["id"])
                            logs += f"\n[Parallel Worktree `{wt_branch}`]\n" + c_logs
                            adapter.input_tokens += wt_adapter.input_tokens
                            adapter.output_tokens += wt_adapter.output_tokens
                            adapter.cost = round(adapter.cost + wt_adapter.cost, 10)
                            if c_abort is not None or c_code != 0:
                                any_failed = True
                                cleanup_task_worktree(primary_dir, wt_dir, wt_branch)
                                continue
                            merged_ok = merge_and_remove_task_worktree(
                                primary_dir,
                                wt_dir,
                                wt_branch,
                                commit_message=(
                                    f"feat(seed-{seed_number}): {t_item.get('title') or tid}"
                                ),
                            )
                            if merged_ok:
                                completed_task_ids.append(tid)
                                if w_validated and w_validated.get("summary_comment"):
                                    prev_sum = str(
                                        validated_output.get("summary_comment") or ""
                                    ).strip()
                                    new_sum = str(w_validated["summary_comment"]).strip()
                                    if new_sum and new_sum not in prev_sum:
                                        validated_output["summary_comment"] = (
                                            f"{prev_sum}\n\n{new_sum}".strip()
                                        )
                            else:
                                any_failed = True
                        idx += len(parallel_group)
                        if any_failed:
                            break
                        continue
                finally:
                    for _, _, _, _, _, wt_adp, _ in wt_specs:
                        wt_adp.cleanup_workspace()
                    for wt_h in created_wt_homes:
                        shutil.rmtree(wt_h, ignore_errors=True)

            next_task = remaining_tasks[idx]
            next_tid = str(next_task["id"])
            logger.info(
                "Batch-executing next sequential task in warm container: %s (%s)",
                next_tid,
                next_task.get("title"),
            )
            next_prompt = build_next_task_continuation_prompt(next_task, output_file)
            t_code, t_logs, t_abort = await _run_cli_with_steering(
                adapter=adapter,
                env=child_env,
                initial_prompt=next_prompt,
                api_url=api_url,
                run_token=run_token,
                continue_session=True,
                **cli_kwargs,
            )
            logs += "\n" + t_logs
            if t_abort is not None or t_code != 0:
                logger.warning(
                    "Stopping batch task continuation at task %s (exit=%d, abort=%s)",
                    next_tid,
                    t_code,
                    t_abort,
                )
                break
            re_out, _ = _load_and_validate_output_file(stage, output_file)
            if re_out is not None:
                validated_output = re_out
            completed_task_ids.append(next_tid)
            idx += 1

    if stage == "execute" and completed_task_ids:
        validated_output["completed_task_ids"] = completed_task_ids

    raw_blocked = sandbox_cfg.get("blocked_paths")
    blocked_paths: list[str] = (
        [str(p).strip() for p in raw_blocked if str(p).strip()]
        if isinstance(raw_blocked, list)
        else []
    )
    require_tests_pass = bool(sandbox_cfg.get("require_tests_pass", False))
    setup_cmd, test_cmd = _resolve_verification_commands(primary_dir, sandbox_cfg)
    verification_info: dict[str, Any] = {"source": "agent_reported"}

    # Deterministic supervisor test verification gate for Execute & Review stages
    if stage in ("execute", "review") and require_tests_pass and test_cmd:
        passed, t_code, t_output = await _run_deterministic_verification_gate(
            primary_dir=primary_dir,
            env=child_env,
            setup_command=setup_cmd,
            test_command=test_cmd,
        )
        logs += f"\n\n[Deterministic Verification Gate: `{test_cmd}` -> exit {t_code}]\n{t_output}"

        if not passed and stage == "execute" and adapter.is_binary_available():
            logger.warning(
                "Deterministic test gate failed on first pass (exit %d); re-prompting agent to fix...",
                t_code,
            )
            fix_test_prompt = (
                "## DETERMINISTIC SUPERVISOR TEST GATE FAILED\n"
                f"The runner supervisor executed `{test_cmd}` (exit code {t_code}) and it failed:\n"
                f"```\n{t_output[-4000:]}\n```\n"
                f"Fix the failing implementation or tests in `/workspace`, verify `{test_cmd}` passes, "
                f"and update `{output_file}` before exiting."
            )
            r_code, r_logs, _ = await _run_cli_with_steering(
                adapter=adapter,
                env=child_env,
                initial_prompt=fix_test_prompt,
                api_url=api_url,
                run_token=run_token,
                continue_session=True,
            )
            logs += "\n" + r_logs
            exit_code = r_code
            re_out, _ = _load_and_validate_output_file(stage, output_file)
            if re_out is not None:
                validated_output = re_out

            passed, t_code, t_output = await _run_deterministic_verification_gate(
                primary_dir=primary_dir,
                env=child_env,
                setup_command=setup_cmd,
                test_command=test_cmd,
            )
            logs += f"\n\n[Deterministic Verification Retry: `{test_cmd}` -> exit {t_code}]\n{t_output}"

        verification_info = {
            "source": "agent_reported",
            "passed": passed,
            "exit_code": t_code,
            "command": test_cmd,
        }

        if not passed and stage == "execute":
            err_msg = f"Deterministic test verification gate failed (exit code {t_code}): {test_cmd}"
            logger.error(err_msg)
            await _finish_agent_or_report(
                is_broker_agent_mode=is_broker_agent_mode,
                run_id=run_id,
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=t_code or 1,
                error=err_msg,
                stage_output=validated_output,
                log_tail=logs,
                verification=verification_info,
            )
            return 1

        if stage == "review":
            if not passed:
                validated_output["approved"] = False
                existing_findings = list(validated_output.get("findings") or [])
                existing_findings.append(
                    f"**Deterministic Test Gate Failed** (`{test_cmd}`, exit code `{t_code}`): "
                    f"`{t_output.strip().splitlines()[-1] if t_output.strip() else 'non-zero exit'}`"
                )
                validated_output["findings"] = existing_findings
            else:
                summary_str = str(validated_output.get("summary_comment") or "").strip()
                gate_badge = f"\n\n- **Supervisor Verification Gate**: `{test_cmd}` passed (`exit 0`)"
                if gate_badge not in summary_str:
                    validated_output["summary_comment"] = f"{summary_str}{gate_badge}".strip()

    # Restore or remove temporary opencode.json before post-agent finalize
    adapter.cleanup_workspace()

    # In 3-container broker mode (`agent`), git commit/push and blocked_paths enforcement
    # are performed exclusively by the trusted `finalize` container.
    if stage == "execute" and not is_broker_agent_mode:
        validated_output = await push_execute_changes_after_agent_exit(
            repo_dirs=repo_dirs,
            seed_number=seed_number,
            project_slug=project_slug,
            stage_output=validated_output,
            api_url=api_url,
            run_token=run_token,
            initial_heads=initial_heads,
            blocked_paths=blocked_paths,
        )
        push_errors = validated_output.pop("push_errors", None) if isinstance(validated_output, dict) else None
        if push_errors:
            err_msg = "Couldn't push the seed branch: " + "; ".join(str(e) for e in push_errors)
            logger.error(err_msg)
            await _finish_agent_or_report(
                is_broker_agent_mode=is_broker_agent_mode,
                run_id=run_id,
                api_url=api_url,
                run_token=run_token,
                status="failed",
                exit_code=1,
                error=err_msg[:1000],
                log_tail=logs,
                verification=verification_info,
            )
            return 1

    if adapter.input_tokens == 0 and adapter.output_tokens == 0:
        out_str = json.dumps(validated_output or {})
        adapter.input_tokens = max(1, len(initial_prompt) // 4)
        adapter.output_tokens = max(1, (len(logs) + len(out_str)) // 4)

    if adapter.synthesized_via and isinstance(validated_output, dict):
        validated_output["synthesized_via"] = adapter.synthesized_via

    updated_bundle = adapter.collect_updated_cli_auth_bundle(
        strict_runtime=is_broker_agent_mode
    )
    await _finish_agent_or_report(
        is_broker_agent_mode=is_broker_agent_mode,
        run_id=run_id,
        api_url=api_url,
        run_token=run_token,
        status="succeeded",
        exit_code=0,
        input_tokens=adapter.input_tokens,
        output_tokens=adapter.output_tokens,
        context_tokens=adapter.context_tokens,
        peak_context_tokens=adapter.peak_context_tokens,
        turn_count=adapter.turn_count,
        stage_output=validated_output,
        log_tail=logs,
        updated_cli_auth_bundle=updated_bundle,
        verification=verification_info,
    )
    logger.info("Stage '%s' completed and reported successfully.", stage)
    return 0


def main() -> None:
    try:
        code = asyncio.run(run_container_job())
    except Exception as exc:
        logger.exception("Unhandled runner error: %s", exc)
        code = 1
    sys.exit(code)


if __name__ == "__main__":
    main()
