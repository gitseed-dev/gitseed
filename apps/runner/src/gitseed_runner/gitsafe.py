"""Hardened Git wrapper, hostile repo neutralization, clean commit builder, and diff scanner."""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import logging
import os
import re
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any
from urllib.parse import urlsplit, urlunsplit

logger = logging.getLogger("gitseed_runner")

SYSTEM_BIN_DIRS: tuple[str, ...] = ("/usr/local/bin", "/usr/bin", "/bin")

SAFE_GIT_CONFIG_ARGS: tuple[str, ...] = (
    "-c",
    "core.hooksPath=/dev/null",
    "-c",
    "core.fsmonitor=false",
    "-c",
    "protocol.file.allow=never",
    "-c",
    "protocol.ext.allow=never",
    "-c",
    "core.sshCommand=ssh",
    "-c",
    "core.quotePath=false",
    "-c",
    "core.autocrlf=input",
)

SAFE_GIT_ENV: dict[str, str] = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": "/dev/null",
    "GIT_TERMINAL_PROMPT": "0",
}

DEFAULT_MAX_FILE_BYTES = 5 * 1024 * 1024  # 5 MiB
DEFAULT_MAX_DIFF_BYTES = 20 * 1024 * 1024  # 20 MiB

PRESERVED_GITSEED_PATHS: tuple[str, ...] = (".gitseed/run.json",)

INTERNAL_EXCLUDED_PATHS: tuple[str, ...] = (
    ".gitseed/output.json",
    ".gitseed/deps.sha256",
    ".gitseed/attachments",
    ".gitseed/related",
    "opencode.json",
    ".gitseed-worktrees",
)

_DANGEROUS_GIT_CONFIG_SECTIONS: frozenset[str] = frozenset(
    {
        "alias",
        "filter",
        "credential",
        "include",
        "includeif",
        "fsmonitor",
        "pager",
        "sendemail",
        "man",
        "guitool",
        "difftool",
        "mergetool",
    }
)

_DANGEROUS_GIT_CONFIG_KEYS_BY_SECTION: dict[str, frozenset[str]] = {
    "core": frozenset(
        {
            "sshcommand",
            "askpass",
            "fsmonitor",
            "fsmonitorhookversion",
            "pager",
            "editor",
            "hookspath",
            "gitproxy",
            "attributesfile",
            "excludesfile",
        }
    ),
    "diff": frozenset({"external", "textconv", "command"}),
    "merge": frozenset({"driver"}),
    "gpg": frozenset({"program"}),
    "interactive": frozenset({"difffilter"}),
    "sequence": frozenset({"editor"}),
    "remote": frozenset({"uploadpack", "receivepack", "proxycommand", "vcs"}),
}

HARDCODED_BLOCKED_PATTERNS: tuple[str, ...] = (
    ".vscode/**",
    ".git/**",
    "**/.git/**",
    ".env",
    ".env.local",
    ".env.production",
    ".env.development",
    ".env.staging",
    "*id_rsa*",
    "*id_dsa*",
    "*id_ecdsa*",
    "*id_ed25519*",
    "*.pem",
    "*.p12",
    "*.pfx",
)

SECRET_SCAN_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("aws_sts_key_id", re.compile(r"\bASIA[0-9A-Z]{16}\b")),
    (
        "github_token",
        re.compile(
            r"\b(?:ghp|gho|ghs|ghu|ghr)_[A-Za-z0-9_]{20,255}\b"
            r"|\bgithub_pat_[A-Za-z0-9_]{20,255}\b"
        ),
    ),
    ("gitlab_pat", re.compile(r"\bglpat-[A-Za-z0-9_\-=]{20,255}\b")),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,255}\b")),
    (
        "private_key_pem",
        re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |ENCRYPTED |)?PRIVATE KEY-----"),
    ),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("google_oauth_token", re.compile(r"\bya29\.[0-9A-Za-z_-]{20,}\b")),
    ("xai_api_key", re.compile(r"\bxai-[A-Za-z0-9]{20,}\b")),
    ("gitseed_token", re.compile(r"(?<![A-Za-z0-9_])gs(?:p|sse|r)_[A-Za-z0-9_.-]{16,}")),
    ("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{16,255}\b")),
    ("openai_api_key", re.compile(r"\bsk-(?:proj-|live-|svcacct-)?[A-Za-z0-9_\-]{20,255}\b")),
)

SECRET_SCAN_PATTERNS = SECRET_SCAN_RULES


def strip_url_credentials(url: str) -> str:
    """Remove any embedded username:password@ credentials from an HTTP(S) Git URL."""
    if not url:
        return url
    try:
        parts = urlsplit(url)
        if not parts.scheme or "@" not in parts.netloc:
            return url
        host_part = parts.netloc.rsplit("@", 1)[-1]
        return urlunsplit((parts.scheme, host_part, parts.path, parts.query, parts.fragment))
    except Exception:
        return re.sub(r"://[^/@]+@", "://", url)


def is_local_repo_path(url: str) -> bool:
    """Return True if url is an existing local directory (not file:// or remote URL)."""
    cleaned = (url or "").strip()
    if not cleaned or "://" in cleaned or cleaned.startswith("git@"):
        return False
    with contextlib.suppress(OSError):
        return Path(cleaned).exists()
    return False


def build_safe_git_env(
    *,
    extra_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """Construct a hardened Git environment where system binary dirs precede all other paths."""
    env = os.environ.copy()
    env.update(SAFE_GIT_ENV)

    ws_prefixes: list[str] = ["/workspace"]
    env_ws = os.environ.get("GITSEED_WORKSPACE", "").strip()
    if env_ws:
        ws_prefixes.append(env_ws.rstrip("/\\"))
        with contextlib.suppress(OSError):
            ws_prefixes.append(str(Path(env_ws).resolve()).rstrip("/\\"))

    raw_path = env.get("PATH", "")
    ordered_paths: list[str] = list(SYSTEM_BIN_DIRS)
    for segment in raw_path.split(os.pathsep):
        cleaned = segment.strip()
        if not cleaned or cleaned == "." or cleaned in ordered_paths:
            continue
        trimmed = cleaned.rstrip("/\\")
        if any(
            trimmed == wp or trimmed.startswith(f"{wp}/") or trimmed.startswith(f"{wp}\\")
            for wp in ws_prefixes
            if wp
        ):
            continue
        ordered_paths.append(cleaned)
    env["PATH"] = os.pathsep.join(ordered_paths)

    if extra_env:
        env.update(extra_env)
    return env


def run_safe_git(
    args: list[str],
    cwd: Path,
    *,
    extra_env: dict[str, str] | None = None,
    timeout: int = 120,
) -> tuple[int, str, str]:
    """Execute a git command with mandatory safe config flags and isolated environment."""
    env = build_safe_git_env(extra_env=extra_env)
    cmd = ["git", *SAFE_GIT_CONFIG_ARGS, *args]
    proc = subprocess.run(
        cmd,
        cwd=str(cwd),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )
    return proc.returncode, proc.stdout.strip(), proc.stderr.strip()


_git = run_safe_git


def build_minimal_git_config(
    *,
    clean_remote_url: str | None = None,
    default_branch: str = "main",
) -> str:
    """Construct a known-good minimal .git/config without hooks, fsmonitor, or credentials."""
    lines = [
        "[core]",
        "\trepositoryformatversion = 0",
        "\tfilemode = true",
        "\tbare = false",
        "\tlogallrefupdates = true",
        "\thooksPath = /dev/null",
        "\tfsmonitor = false",
        "\tsymlinks = true",
    ]
    if clean_remote_url:
        stripped = strip_url_credentials(clean_remote_url)
        lines.extend(
            [
                '[remote "origin"]',
                f"\turl = {stripped}",
                "\tfetch = +refs/heads/*:refs/remotes/origin/*",
            ]
        )
    if default_branch:
        lines.extend(
            [
                f'[branch "{default_branch}"]',
                "\tremote = origin",
                f"\tmerge = refs/heads/{default_branch}",
            ]
        )
    return "\n".join(lines) + "\n"


_SECTION_HEADER_RE = re.compile(
    r'^\s*(\[\s*([A-Za-z0-9-]+)(?:\.[A-Za-z0-9._-]+|\s+"(?:\\.|[^"\\])*")?\s*\])(.*)$'
)
_CONFIG_KEY_RE = re.compile(r"^\s*([A-Za-z0-9-]+)\s*(?:=|$)")


def _sanitize_git_config_text(raw_config: str) -> str:
    """Strip dangerous executable directives and sections from a .git/config payload."""
    physical_lines = raw_config.splitlines()
    logical_lines: list[str] = []
    idx = 0
    while idx < len(physical_lines):
        curr = physical_lines[idx]
        stripped_lead = curr.lstrip()
        if not stripped_lead.startswith(("#", ";")):
            while curr.endswith("\\") and idx + 1 < len(physical_lines):
                idx += 1
                curr = curr[:-1] + physical_lines[idx]
        logical_lines.append(curr)
        idx += 1

    output_lines: list[str] = []
    current_section = ""
    skip_section = False
    saw_core_section = False

    def _emit_kv_if_safe(sec: str, kv_line: str) -> None:
        s = kv_line.strip()
        if not s or s.startswith(("#", ";")):
            return
        km = _CONFIG_KEY_RE.match(s)
        if not km:
            return
        key_lower = km.group(1).lower()
        blocked_keys = _DANGEROUS_GIT_CONFIG_KEYS_BY_SECTION.get(sec, frozenset())
        if key_lower in blocked_keys:
            return
        output_lines.append(f"\t{s}")

    for line in logical_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", ";")):
            if not skip_section:
                output_lines.append(line)
            continue

        sec_match = _SECTION_HEADER_RE.match(line)
        if sec_match:
            header_text = sec_match.group(1).strip()
            sec_name = sec_match.group(2).lower()
            remainder = sec_match.group(3)
            if sec_name in _DANGEROUS_GIT_CONFIG_SECTIONS:
                skip_section = True
                current_section = sec_name
                continue
            skip_section = False
            current_section = sec_name
            output_lines.append(header_text)
            if sec_name == "core" and not saw_core_section:
                saw_core_section = True
                output_lines.append("\thooksPath = /dev/null")
                output_lines.append("\tfsmonitor = false")
            if remainder and remainder.strip():
                _emit_kv_if_safe(current_section, remainder)
            continue

        if stripped.startswith("["):
            skip_section = True
            continue

        if skip_section or not current_section:
            continue
        _emit_kv_if_safe(current_section, line)

    if not saw_core_section:
        output_lines.insert(0, "\tfsmonitor = false")
        output_lines.insert(0, "\thooksPath = /dev/null")
        output_lines.insert(0, "[core]")

    return "\n".join(output_lines).rstrip() + "\n"


def _sanitize_single_git_dir(git_dir: Path) -> None:
    """Remove hooks, locks, attributes/grafts, and unsafe config keys in a .git dir."""
    if git_dir.is_symlink():
        git_dir.unlink(missing_ok=True)
        return
    if not git_dir.is_dir():
        return

    # 1. Remove stale lock files
    for lock_file in git_dir.rglob("*.lock"):
        with contextlib.suppress(OSError):
            if lock_file.is_symlink() or lock_file.is_file():
                lock_file.unlink(missing_ok=True)

    # 2. Delete all files/symlinks in .git/hooks/* (and any .git/worktrees/*/hooks/*)
    hook_dirs = [git_dir / "hooks"]
    wt_admin = git_dir / "worktrees"
    if wt_admin.is_dir() and not wt_admin.is_symlink():
        for child in wt_admin.iterdir():
            if child.is_dir() and not child.is_symlink():
                hook_dirs.append(child / "hooks")

    for hooks_dir in hook_dirs:
        if hooks_dir.is_symlink() or (hooks_dir.exists() and not hooks_dir.is_dir()):
            with contextlib.suppress(OSError):
                hooks_dir.unlink(missing_ok=True)
        if hooks_dir.is_dir():
            for entry in list(hooks_dir.iterdir()):
                with contextlib.suppress(OSError):
                    if entry.is_dir() and not entry.is_symlink():
                        shutil.rmtree(entry, ignore_errors=True)
                    else:
                        entry.unlink(missing_ok=True)
        else:
            with contextlib.suppress(OSError):
                hooks_dir.mkdir(parents=True, exist_ok=True)

    # 3. Remove .git/info/attributes and .git/info/grafts that could trigger filter
    #    drivers or spoof parent commit history.
    info_dir = git_dir / "info"
    if info_dir.is_symlink():
        with contextlib.suppress(OSError):
            info_dir.unlink(missing_ok=True)
    elif info_dir.is_dir():
        for name in ("attributes", "grafts"):
            target = info_dir / name
            if target.exists() or target.is_symlink():
                with contextlib.suppress(OSError):
                    target.unlink(missing_ok=True)

    # 4. Sanitize .git/config in place without invoking git before config is safe
    cfg_path = git_dir / "config"
    if cfg_path.is_symlink():
        with contextlib.suppress(OSError):
            cfg_path.unlink(missing_ok=True)
        cfg_path.write_text(build_minimal_git_config(), encoding="utf-8")
    elif cfg_path.is_file():
        with contextlib.suppress(OSError):
            raw_cfg = cfg_path.read_text(encoding="utf-8", errors="replace")
            sanitized_cfg = _sanitize_git_config_text(raw_cfg)
            if sanitized_cfg != raw_cfg:
                cfg_path.write_text(sanitized_cfg, encoding="utf-8")


def sanitize_git_dir(workspace: Path) -> None:
    """Neutralize .git/hooks/* and dangerous .git/config keys in workspace repos."""
    if not workspace.exists() and not workspace.is_symlink():
        return
    if workspace.is_symlink():
        return
    if workspace.name == ".git" and workspace.is_dir():
        _sanitize_single_git_dir(workspace)
        return

    git_entry = workspace / ".git"
    if git_entry.is_symlink():
        with contextlib.suppress(OSError):
            git_entry.unlink(missing_ok=True)
    elif git_entry.is_dir():
        _sanitize_single_git_dir(git_entry)

    # Support multi-repo workspaces (/workspace/<repo>/.git)
    if workspace.is_dir():
        with contextlib.suppress(OSError):
            for child in workspace.iterdir():
                if child.name.startswith(".") or child.is_symlink() or not child.is_dir():
                    continue
                child_git = child / ".git"
                if child_git.is_symlink():
                    with contextlib.suppress(OSError):
                        child_git.unlink(missing_ok=True)
                elif child_git.is_dir():
                    _sanitize_single_git_dir(child_git)


def neutralize_git_repo(
    repo_dir: Path,
    *,
    clean_remote_url: str | None = None,
    default_branch: str = "main",
) -> None:
    """Neutralize a pre-existing (warm, potentially hostile) Git repo BEFORE running git."""
    git_entry = repo_dir / ".git"
    if git_entry.is_symlink() or (git_entry.exists() and not git_entry.is_dir()):
        git_entry.unlink(missing_ok=True)
        return
    if not git_entry.is_dir():
        return

    _sanitize_single_git_dir(git_entry)

    # Rewrite .git/config to a known-good minimal config
    cfg_path = git_entry / "config"
    if cfg_path.is_symlink() or cfg_path.exists():
        cfg_path.unlink(missing_ok=True)
    cfg_path.write_text(
        build_minimal_git_config(
            clean_remote_url=clean_remote_url,
            default_branch=default_branch,
        ),
        encoding="utf-8",
    )


def is_internal_excluded_path(rel_path: str) -> bool:
    """Return True if rel_path is an internal runner artifact (preserving .gitseed/run.json)."""
    norm = rel_path.strip().replace("\\", "/").removeprefix("./").lstrip("/").rstrip("/")
    if not norm:
        return False
    if norm in PRESERVED_GITSEED_PATHS:
        return False
    if norm == ".gitseed" or norm.startswith(".gitseed/"):
        return True
    if norm == "opencode.json":
        return True
    return norm == ".gitseed-worktrees" or norm.startswith(".gitseed-worktrees/")


def _exclude_internal_paths(
    repo_dir: Path,
    *,
    base_sha: str | None = None,
    git_dir: Path | None = None,
    remove_worktree_files: bool = True,
) -> list[str]:
    """Unstage/remove internal .gitseed/* scratch files while keeping .gitseed/run.json."""
    effective_git_dir = git_dir or (repo_dir / ".git")
    candidates: set[str] = set()

    if effective_git_dir.exists():
        rc_ls, ls_out, _ = run_safe_git(
            ["-c", "core.quotePath=false", f"--git-dir={effective_git_dir}", "ls-files"],
            repo_dir,
        )
        if rc_ls == 0 and ls_out:
            for line in ls_out.splitlines():
                if line.strip():
                    candidates.add(line.strip())

        ref_to_check = base_sha
        if not ref_to_check:
            rc_head, head_out, _ = run_safe_git(
                [f"--git-dir={effective_git_dir}", "rev-parse", "--verify", "HEAD"],
                repo_dir,
            )
            if rc_head == 0 and head_out.strip():
                ref_to_check = head_out.strip()

        if ref_to_check:
            rc_diff, diff_out, _ = run_safe_git(
                [
                    "-c",
                    "core.quotePath=false",
                    f"--git-dir={effective_git_dir}",
                    "diff",
                    "--cached",
                    "--name-only",
                    ref_to_check,
                ],
                repo_dir,
            )
            if rc_diff == 0 and diff_out:
                for line in diff_out.splitlines():
                    if line.strip():
                        candidates.add(line.strip())
    else:
        ref_to_check = None

    # Also scan worktree for internal files when operating directly on a repo worktree
    if git_dir is None and repo_dir.is_dir():
        gitseed_dir = repo_dir / ".gitseed"
        if gitseed_dir.is_dir() and not gitseed_dir.is_symlink():
            for p in gitseed_dir.rglob("*"):
                if p.is_file() or p.is_symlink():
                    with contextlib.suppress(ValueError):
                        candidates.add(p.relative_to(repo_dir).as_posix())
        if (repo_dir / "opencode.json").exists() or (repo_dir / "opencode.json").is_symlink():
            candidates.add("opencode.json")
        wt_dir = repo_dir / ".gitseed-worktrees"
        if wt_dir.exists() or wt_dir.is_symlink():
            candidates.add(".gitseed-worktrees")

    to_exclude = sorted(p for p in candidates if is_internal_excluded_path(p))
    if not to_exclude:
        return []

    if effective_git_dir.exists():
        tracked_in_base: set[str] = set()
        if ref_to_check:
            rc_tree, tree_out, _ = run_safe_git(
                [
                    "-c",
                    "core.quotePath=false",
                    f"--git-dir={effective_git_dir}",
                    "ls-tree",
                    "-r",
                    "--name-only",
                    ref_to_check,
                    "--",
                    *to_exclude,
                ],
                repo_dir,
            )
            if rc_tree == 0 and tree_out:
                tracked_in_base = {p.strip() for p in tree_out.splitlines() if p.strip()}

        tracked_paths = [p for p in to_exclude if p in tracked_in_base]
        untracked_paths = [p for p in to_exclude if p not in tracked_in_base]

        if tracked_paths and ref_to_check:
            run_safe_git(
                [
                    f"--git-dir={effective_git_dir}",
                    "reset",
                    "-q",
                    ref_to_check,
                    "--",
                    *tracked_paths,
                ],
                repo_dir,
            )
        if untracked_paths:
            run_safe_git(
                [
                    f"--git-dir={effective_git_dir}",
                    "rm",
                    "--cached",
                    "-r",
                    "-f",
                    "--ignore-unmatch",
                    "--",
                    *untracked_paths,
                ],
                repo_dir,
            )
    else:
        untracked_paths = list(to_exclude)

    if remove_worktree_files and git_dir is None and repo_dir.is_dir():
        root_resolved = repo_dir.resolve()
        for rel_p in untracked_paths:
            raw_target = repo_dir / rel_p
            with contextlib.suppress(OSError):
                if raw_target.is_symlink():
                    raw_target.unlink(missing_ok=True)
                    continue
                resolved = raw_target.resolve()
                if not resolved.is_relative_to(root_resolved) or resolved == root_resolved:
                    continue
                if resolved.is_dir():
                    shutil.rmtree(resolved, ignore_errors=True)
                elif resolved.exists():
                    resolved.unlink(missing_ok=True)

    return to_exclude


def _copy_workspace_into_staging(
    workspace_repo_dir: Path,
    staging_dir: Path,
    *,
    allowed_gitlinks: set[str] | None = None,
) -> list[str]:
    """Copy workspace files into an isolated staging directory without dereferencing symlinks.

    Returns a sorted list of repo-relative paths for any symlinks that escape `workspace_repo_dir`.
    """
    ws_root = workspace_repo_dir.resolve()
    staging_dir.mkdir(parents=True, exist_ok=True)
    gitlinks = allowed_gitlinks or set()
    escaped_symlinks: list[str] = []

    def _is_safe_in_repo_symlink(link_path: Path) -> bool:
        try:
            raw_target = os.readlink(link_path)
        except OSError:
            return False
        if not raw_target or "\x00" in raw_target:
            return False
        target_p = Path(raw_target)
        if target_p.is_absolute() or (os.name == "nt" and target_p.drive):
            return False
        try:
            resolved_target = (link_path.parent / target_p).resolve()
        except OSError:
            return False
        return resolved_target == ws_root or resolved_target.is_relative_to(ws_root)

    for dirpath, dirnames, filenames in os.walk(ws_root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, ws_root)
        rel_dir_posix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")

        if rel_dir_posix == "":
            for skip in (".git", ".gitseed-worktrees"):
                if skip in dirnames:
                    dirnames.remove(skip)
        elif rel_dir_posix in gitlinks:
            # Authorized submodule gitlink directory: keep placeholder directory without staging nested .git
            (staging_dir / rel_dir_posix).mkdir(parents=True, exist_ok=True)
            dirnames.clear()
            continue

        # Handle directory symlinks without descending into them or dereferencing targets
        for dname in list(dirnames):
            src_dir = Path(dirpath) / dname
            rel_d_posix = f"{rel_dir_posix}/{dname}" if rel_dir_posix else dname
            if src_dir.is_symlink():
                dirnames.remove(dname)
                if not _is_safe_in_repo_symlink(src_dir):
                    escaped_symlinks.append(rel_d_posix)
                    continue
                dst_dir = staging_dir / rel_d_posix
                dst_dir.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(src_dir, dst_dir, follow_symlinks=False)
                except OSError as exc:
                    logger.warning("Skipping workspace directory symlink %s: %s", rel_d_posix, exc)

        if rel_dir_posix:
            (staging_dir / rel_dir_posix).mkdir(parents=True, exist_ok=True)

        for fname in filenames:
            if rel_dir_posix == "" and fname == ".git":
                continue
            rel_f_posix = f"{rel_dir_posix}/{fname}" if rel_dir_posix else fname
            src_file = Path(dirpath) / fname
            if src_file.is_symlink() and not _is_safe_in_repo_symlink(src_file):
                escaped_symlinks.append(rel_f_posix)
                continue
            dst_file = staging_dir / rel_f_posix
            dst_file.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src_file, dst_file, follow_symlinks=False)
                if dst_file.is_symlink() and not _is_safe_in_repo_symlink(src_file):
                    dst_file.unlink(missing_ok=True)
                    escaped_symlinks.append(rel_f_posix)
            except OSError as exc:
                logger.warning("Skipping unreadable/swapped workspace entry %s: %s", rel_f_posix, exc)

    return sorted(set(escaped_symlinks))


def matches_blocked_pattern(rel_path: str, pattern: str) -> bool:
    """Return True if a normalized repo-relative POSIX path matches a blocked_paths pattern (case-insensitive)."""
    norm_path = rel_path.strip().replace("\\", "/").removeprefix("./").lstrip("/").lower()
    norm_pat = pattern.strip().replace("\\", "/").removeprefix("./").lstrip("/").lower()
    if not norm_path or not norm_pat:
        return False
    if norm_pat.endswith("/"):
        prefix = norm_pat.rstrip("/")
        if norm_path == prefix or norm_path.startswith(f"{prefix}/"):
            return True
    if fnmatch.fnmatchcase(norm_path, norm_pat):
        return True
    if "/" not in norm_pat and fnmatch.fnmatchcase(PurePosixPath(norm_path).name, norm_pat):
        return True
    if "**" not in norm_pat:
        return False
    regex_pat = (
        "^"
        + re.escape(norm_pat)
        .replace(r"\*\*/", "(?:.+/)?")
        .replace(r"\*\*", ".*")
        .replace(r"\*", "[^/]*")
        .replace(r"\?", ".")
        + "$"
    )
    return re.match(regex_pat, norm_path) is not None


def find_blocked_path_violations(
    changed_paths: list[str],
    blocked_paths: list[str] | None = None,
    *,
    include_hardcoded: bool = True,
) -> list[str]:
    """Return sorted list of changed paths matching HARDCODED_BLOCKED_PATTERNS or blocked_paths."""
    if not changed_paths:
        return []
    effective_patterns: list[str] = []
    if include_hardcoded:
        effective_patterns.extend(HARDCODED_BLOCKED_PATTERNS)
    if blocked_paths:
        effective_patterns.extend(blocked_paths)
    if not effective_patterns:
        return []
    violations: list[str] = []
    for path in changed_paths:
        norm = path.strip().lstrip("/")
        if not norm:
            continue
        if any(
            matches_blocked_pattern(norm, pat)
            for pat in effective_patterns
            if pat and pat.strip()
        ):
            violations.append(norm)
    return sorted(set(violations))


@dataclass
class PathPolicyResult:
    """Structured result of enforcing blocked_paths policy against a repository."""

    ok: bool
    violations: list[str] = field(default_factory=list)
    reverted: list[str] = field(default_factory=list)
    error: str | None = None


def apply_git_path_policy(
    repo_dir: Path,
    *,
    base_sha: str = "HEAD",
    blocked_paths: list[str] | None = None,
    blocked_paths_action: str = "fail",
    changed_paths: list[str] | None = None,
    git_dir: Path | None = None,
    work_tree_dir: Path | None = None,
    update_worktree: bool = True,
) -> PathPolicyResult:
    """Enforce blocked_paths policy (`fail` or `revert`) while preserving tracked files."""
    if git_dir is None and (repo_dir / ".git").is_dir() and os.access(repo_dir / ".git", os.W_OK):
        sanitize_git_dir(repo_dir)

    effective_git_dir = git_dir or (repo_dir / ".git")
    effective_work_tree = work_tree_dir or repo_dir

    if changed_paths is not None:
        candidate_paths = list(changed_paths)
    else:
        detected: set[str] = set()
        rc_cached, cached_out, _ = run_safe_git(
            [
                "-c",
                "core.quotePath=false",
                f"--git-dir={effective_git_dir}",
                "diff",
                "--cached",
                "--name-only",
                base_sha,
            ],
            repo_dir,
        )
        if rc_cached == 0 and cached_out:
            for line in cached_out.splitlines():
                if line.strip():
                    detected.add(line.strip())

        if update_worktree and effective_work_tree.is_dir():
            rc_wt, wt_out, _ = run_safe_git(
                [
                    "-c",
                    "core.quotePath=false",
                    f"--git-dir={effective_git_dir}",
                    f"--work-tree={effective_work_tree}",
                    "diff",
                    "--name-only",
                    base_sha,
                ],
                repo_dir,
            )
            if rc_wt == 0 and wt_out:
                for line in wt_out.splitlines():
                    if line.strip():
                        detected.add(line.strip())

            rc_st, st_out, _ = run_safe_git(
                [
                    "-c",
                    "core.quotePath=false",
                    f"--git-dir={effective_git_dir}",
                    f"--work-tree={effective_work_tree}",
                    "status",
                    "--porcelain",
                    "-uall",
                ],
                repo_dir,
            )
            if rc_st == 0 and st_out:
                for line in st_out.splitlines():
                    if len(line) > 3:
                        raw_p = line[3:].strip()
                        if " -> " in raw_p:
                            old_p, new_p = raw_p.split(" -> ", 1)
                            if old_p.strip():
                                detected.add(old_p.strip().strip('"'))
                            raw_p = new_p.strip()
                        if raw_p:
                            detected.add(raw_p.strip('"'))
        candidate_paths = sorted(detected)

    violations = find_blocked_path_violations(candidate_paths, blocked_paths or [])
    if not violations:
        return PathPolicyResult(ok=True)

    action = (blocked_paths_action or "fail").strip().lower()
    if action != "revert":
        return PathPolicyResult(
            ok=False,
            violations=violations,
            error=(
                "Governance policy violation: modifications to blocked_paths are forbidden: "
                + ", ".join(violations)
            ),
        )

    logger.warning(
        "Reverting modifications to blocked paths per policy: %s",
        ", ".join(violations),
    )
    rc_ls, ls_out, _ = run_safe_git(
        [
            "-c",
            "core.quotePath=false",
            f"--git-dir={effective_git_dir}",
            "ls-tree",
            "-r",
            "--name-only",
            base_sha,
            "--",
            *violations,
        ],
        repo_dir,
    )
    tracked_in_base = (
        {p.strip() for p in ls_out.splitlines() if p.strip()}
        if rc_ls == 0 and ls_out
        else set()
    )
    tracked_paths = [p for p in violations if p in tracked_in_base]
    new_paths = [p for p in violations if p not in tracked_in_base]

    wt_writable = (
        update_worktree
        and effective_work_tree.is_dir()
        and os.access(effective_work_tree, os.W_OK)
    )

    if tracked_paths:
        run_safe_git(
            [
                f"--git-dir={effective_git_dir}",
                "reset",
                "-q",
                base_sha,
                "--",
                *tracked_paths,
            ],
            repo_dir,
        )
        if wt_writable:
            run_safe_git(
                [
                    f"--git-dir={effective_git_dir}",
                    f"--work-tree={effective_work_tree}",
                    "checkout",
                    base_sha,
                    "--",
                    *tracked_paths,
                ],
                repo_dir,
            )

    if new_paths:
        run_safe_git(
            [
                f"--git-dir={effective_git_dir}",
                "rm",
                "-r",
                "-f",
                "--cached",
                "--ignore-unmatch",
                "--",
                *new_paths,
            ],
            repo_dir,
        )
        if wt_writable:
            wt_root_resolved = effective_work_tree.resolve()
            for rel_p in new_paths:
                raw_target = effective_work_tree / rel_p
                with contextlib.suppress(OSError):
                    if raw_target.is_symlink():
                        raw_target.unlink(missing_ok=True)
                        continue
                    resolved = raw_target.resolve()
                    if (
                        not resolved.is_relative_to(wt_root_resolved)
                        or resolved == wt_root_resolved
                    ):
                        continue
                    if resolved.is_dir():
                        shutil.rmtree(resolved, ignore_errors=True)
                    elif resolved.exists():
                        resolved.unlink(missing_ok=True)

    return PathPolicyResult(
        ok=True,
        violations=violations,
        reverted=list(violations),
    )


def _expand_secret_needles(
    extra_secrets: list[str] | set[str] | tuple[str, ...] | None,
) -> list[str]:
    """Build literal + Base64-encoded needles for issued run credentials."""
    needles: list[str] = []
    seen: set[str] = set()
    for raw in extra_secrets or ():
        if not raw:
            continue
        cleaned = raw.strip()
        if len(cleaned) >= 8 and cleaned not in seen:
            seen.add(cleaned)
            needles.append(cleaned)
        if len(cleaned) >= 16:
            raw_bytes = cleaned.encode("utf-8")
            for b64_variant in (
                base64.b64encode(raw_bytes).decode("ascii"),
                base64.urlsafe_b64encode(raw_bytes).decode("ascii"),
            ):
                for candidate in (b64_variant, b64_variant.rstrip("=")):
                    if len(candidate) >= 16 and candidate not in seen:
                        seen.add(candidate)
                        needles.append(candidate)
    return needles


def scan_diff_for_secrets(
    diff_text: str,
    *,
    extra_secrets: list[str] | set[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Scan added lines in a unified git diff for high-confidence secrets and run credentials.

    Returns a list of {"path": str, "line": int, "rule": str}. NEVER includes the secret value.
    """
    findings: list[dict[str, Any]] = []
    if not diff_text:
        return findings

    secret_needles = _expand_secret_needles(extra_secrets)

    in_hunk = False
    current_file = "<unknown>"
    current_new_line = 0
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")

    for raw_line in diff_text.splitlines():
        if raw_line.startswith("diff --git "):
            in_hunk = False
            continue
        if not in_hunk:
            if raw_line.startswith("+++ b/"):
                current_file = raw_line[6:].strip()
                continue
            if raw_line.startswith("+++ ") and raw_line != "+++ /dev/null":
                current_file = raw_line[4:].strip()
                continue
            if raw_line.startswith(("+++ ", "--- ")):
                continue
        if raw_line.startswith("@@ "):
            m = hunk_re.match(raw_line)
            if m:
                in_hunk = True
                current_new_line = int(m.group(1)) - 1
                continue
        if not in_hunk:
            continue
        if raw_line.startswith("+"):
            current_new_line += 1
            added_content = raw_line[1:]
            matched_rule: str | None = None
            for secret_val in secret_needles:
                if secret_val in added_content:
                    matched_rule = "issued_run_credential"
                    break
            if matched_rule is None:
                for rule_name, pattern in SECRET_SCAN_RULES:
                    if pattern.search(added_content):
                        matched_rule = rule_name
                        break
            if matched_rule is not None:
                findings.append(
                    {
                        "path": current_file,
                        "line": max(1, current_new_line),
                        "rule": matched_rule,
                    }
                )
        elif raw_line.startswith(("-", "\\")):
            continue
        else:
            current_new_line += 1

    return findings


scan_added_lines_for_secrets = scan_diff_for_secrets


def scan_staged_diff_for_secrets(
    target: str | Path,
    *,
    base_sha: str | None = None,
    extra_secrets: list[str] | set[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Scan either a unified diff string or a repository's staged diff for secrets."""
    if isinstance(target, Path):
        if (target / ".git").is_dir() and os.access(target / ".git", os.W_OK):
            sanitize_git_dir(target)
        diff_args = [
            "-c",
            "core.quotePath=false",
            "diff",
            "--cached",
            "--text",
            "--no-color",
            "-U0",
        ]
        if base_sha:
            diff_args.append(base_sha)
        rc, diff_text, _ = run_safe_git(diff_args, target)
        if rc != 0:
            return []
        return scan_diff_for_secrets(diff_text, extra_secrets=extra_secrets)
    return scan_diff_for_secrets(target, extra_secrets=extra_secrets)


def _map_base_gitlinks(clean_repo_dir: Path, base_sha: str) -> dict[str, str]:
    """Return `{path: commit_sha}` for submodule/gitlink entries (mode 160000) in `base_sha`."""
    rc, out, _ = run_safe_git(["ls-tree", "-r", base_sha], clean_repo_dir)
    if rc != 0 or not out:
        return {}
    gitlinks: dict[str, str] = {}
    for line in out.splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2 and parts[0].startswith("160000 "):
            meta_tokens = parts[0].split()
            sha = meta_tokens[2] if len(meta_tokens) >= 3 else ""
            gitlinks[parts[1].strip()] = sha.strip()
    return gitlinks


def _list_base_gitlinks(clean_repo_dir: Path, base_sha: str) -> set[str]:
    """Return the set of submodule/gitlink paths (mode 160000) present in base_sha."""
    return set(_map_base_gitlinks(clean_repo_dir, base_sha).keys())


def detect_unauthorized_nested_git(
    workspace_repo_dir: Path,
    allowed_gitlinks: set[str],
    *,
    base_gitlink_shas: dict[str, str] | None = None,
) -> list[str]:
    """Scan workspace_repo_dir for unauthorized nested .git dirs/files or repointed gitlinks."""
    unauthorized: list[str] = []
    root = workspace_repo_dir.resolve()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = os.path.relpath(dirpath, root)
        rel_dir_posix = "" if rel_dir == "." else rel_dir.replace(os.sep, "/")

        if rel_dir_posix == "":
            for skip in (".git", ".gitseed", ".gitseed-worktrees"):
                if skip in dirnames:
                    dirnames.remove(skip)
        else:
            if ".git" in dirnames or ".git" in filenames:
                if rel_dir_posix not in allowed_gitlinks:
                    unauthorized.append(rel_dir_posix)
                elif base_gitlink_shas is not None and rel_dir_posix in base_gitlink_shas:
                    rc_sub, sub_sha, _ = run_safe_git(["rev-parse", "HEAD"], Path(dirpath))
                    if rc_sub != 0 or sub_sha.strip() != base_gitlink_shas[rel_dir_posix]:
                        unauthorized.append(rel_dir_posix)
                if ".git" in dirnames:
                    dirnames.remove(".git")
    return sorted(set(unauthorized))


@dataclass
class CleanCommitResult:
    """Structured result of constructing and validating a clean commit in finalize mode."""

    ok: bool
    has_changes: bool = False
    commit_sha: str | None = None
    branch: str = ""
    pushed: bool = False
    error: str | None = None
    blocked_paths_violations: list[str] = field(default_factory=list)
    secret_scan_findings: list[dict[str, Any]] = field(default_factory=list)
    diff_stats: dict[str, int] = field(
        default_factory=lambda: {"files": 0, "insertions": 0, "deletions": 0}
    )


def construct_clean_commit_and_verify_policy(
    *,
    clean_repo_dir: Path,
    workspace_repo_dir: Path,
    base_sha: str,
    seed_branch: str,
    commit_message: str,
    trailers: dict[str, str] | None = None,
    blocked_paths: list[str] | None = None,
    blocked_paths_action: str = "fail",
    extra_secrets: list[str] | set[str] | tuple[str, ...] | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
    max_diff_bytes: int = DEFAULT_MAX_DIFF_BYTES,
    allow_submodules: bool = False,
) -> CleanCommitResult:
    """Build one clean commit on base_sha from workspace_repo_dir and verify policy."""
    result = CleanCommitResult(ok=False, branch=seed_branch)
    clean_git_dir = clean_repo_dir / ".git"
    if not clean_git_dir.is_dir():
        result.error = f"Clean repository .git directory missing at {clean_git_dir}"
        return result
    if not workspace_repo_dir.is_dir():
        result.error = f"Workspace repository directory missing at {workspace_repo_dir}"
        return result

    sanitize_git_dir(clean_repo_dir)
    if (workspace_repo_dir / ".git").exists() and os.access(workspace_repo_dir, os.W_OK):
        with contextlib.suppress(OSError):
            sanitize_git_dir(workspace_repo_dir)

    # 1. Verify base_sha is checked out as seed_branch in clean_repo_dir
    rc, _, err = run_safe_git(
        ["checkout", "-B", seed_branch, base_sha],
        clean_repo_dir,
    )
    if rc != 0:
        result.error = f"Failed to checkout base SHA {base_sha} in clean repo: {err}"
        return result

    # 2. Reject nested .git directories, new gitlinks, or repointed submodules
    base_gitlink_map = _map_base_gitlinks(clean_repo_dir, base_sha)
    base_gitlinks = set(base_gitlink_map.keys())
    if not allow_submodules:
        unauthorized_nested = detect_unauthorized_nested_git(
            workspace_repo_dir,
            base_gitlinks,
            base_gitlink_shas=base_gitlink_map,
        )
        if unauthorized_nested:
            result.error = (
                "Governance violation: unauthorized nested .git directory or submodule repointing detected at: "
                + ", ".join(unauthorized_nested)
            )
            return result

    # 3. Copy workspace into an isolated staging directory without dereferencing symlinks
    stage_parent: str | None = None
    if clean_repo_dir.parent.is_dir() and os.access(clean_repo_dir.parent, os.W_OK):
        stage_parent = str(clean_repo_dir.parent)
    staging_dir = Path(tempfile.mkdtemp(prefix=".gitseed-stage-", dir=stage_parent))
    try:
        escaped_symlinks = _copy_workspace_into_staging(
            workspace_repo_dir,
            staging_dir,
            allowed_gitlinks=base_gitlinks,
        )
        if escaped_symlinks:
            result.error = (
                "Governance violation: workspace symlink escapes repository root: "
                + ", ".join(escaped_symlinks)
            )
            return result

        rc, _, err = run_safe_git(
            [
                f"--git-dir={clean_git_dir}",
                f"--work-tree={staging_dir}",
                "add",
                "-A",
            ],
            clean_repo_dir,
        )
        if rc != 0:
            result.error = f"Failed to stage working tree from {workspace_repo_dir}: {err}"
            return result

        # Unstage internal GitSeed scratch paths while preserving .gitseed/run.json
        _exclude_internal_paths(
            clean_repo_dir,
            base_sha=base_sha,
            git_dir=clean_git_dir,
            remove_worktree_files=False,
        )

        # Ensure no unauthorized 160000 gitlink entries or gitlink SHA repointings were staged
        if not allow_submodules:
            rc_idx, idx_out, _ = run_safe_git(
                [f"--git-dir={clean_git_dir}", "ls-files", "--stage"],
                clean_repo_dir,
            )
            if rc_idx == 0 and idx_out:
                for line in idx_out.splitlines():
                    parts = line.split("\t", 1)
                    if len(parts) == 2 and parts[0].startswith("160000 "):
                        gitlink_path = parts[1].strip()
                        meta_cols = parts[0].split()
                        staged_sha = meta_cols[1].strip() if len(meta_cols) >= 2 else ""
                        if gitlink_path not in base_gitlink_map:
                            result.error = (
                                f"Governance violation: unauthorized gitlink staged at {gitlink_path}"
                            )
                            return result
                        if base_gitlink_map[gitlink_path] and staged_sha != base_gitlink_map[gitlink_path]:
                            result.error = (
                                f"Governance violation: unauthorized submodule gitlink repointing at {gitlink_path}"
                            )
                            return result

        # 4. Determine changed files between base_sha and staged index
        rc, names_out, err = run_safe_git(
            [
                "-c",
                "core.quotePath=false",
                f"--git-dir={clean_git_dir}",
                "diff",
                "--cached",
                "--name-only",
                base_sha,
            ],
            clean_repo_dir,
        )
        if rc != 0:
            result.error = f"Failed to inspect staged diff names: {err}"
            return result

        changed_paths = [p.strip() for p in names_out.splitlines() if p.strip()]
        if not changed_paths:
            result.ok = True
            result.has_changes = False
            return result

        if not allow_submodules:
            gitmodules_mods = [
                p
                for p in changed_paths
                if p.lower() == ".gitmodules" or p.lower().endswith("/.gitmodules")
            ]
            if gitmodules_mods:
                result.error = (
                    "Governance violation: unauthorized .gitmodules modification detected at: "
                    + ", ".join(gitmodules_mods)
                )
                return result

        # 5. Enforce blocked_paths policy
        policy_res = apply_git_path_policy(
            clean_repo_dir,
            base_sha=base_sha,
            blocked_paths=blocked_paths,
            blocked_paths_action=blocked_paths_action,
            changed_paths=changed_paths,
            git_dir=clean_git_dir,
            work_tree_dir=staging_dir,
            update_worktree=False,
        )
        if policy_res.violations:
            result.blocked_paths_violations = policy_res.violations
        if not policy_res.ok:
            result.error = policy_res.error
            return result
        if policy_res.reverted:
            _, names_out2, _ = run_safe_git(
                [
                    "-c",
                    "core.quotePath=false",
                    f"--git-dir={clean_git_dir}",
                    "diff",
                    "--cached",
                    "--name-only",
                    base_sha,
                ],
                clean_repo_dir,
            )
            changed_paths = [p.strip() for p in names_out2.splitlines() if p.strip()]
            if not changed_paths:
                result.ok = True
                result.has_changes = False
                return result

        # 6. Enforce per-file size cap on staged blobs
        if max_file_bytes > 0:
            for rel_path in changed_paths:
                rc_sz, sz_out, _ = run_safe_git(
                    [f"--git-dir={clean_git_dir}", "cat-file", "-s", f":{rel_path}"],
                    clean_repo_dir,
                )
                blob_size: int | None = None
                if rc_sz == 0 and sz_out.isdigit():
                    blob_size = int(sz_out)
                else:
                    staged_file = staging_dir / rel_path
                    if (
                        not staged_file.is_symlink()
                        and staged_file.exists()
                        and staged_file.is_file()
                    ):
                        with contextlib.suppress(OSError):
                            blob_size = staged_file.stat().st_size
                if blob_size is not None and blob_size > max_file_bytes:
                    result.error = (
                        f"Governance policy violation: file '{rel_path}' ({blob_size} bytes) "
                        f"exceeds max_file_bytes cap ({max_file_bytes} bytes)"
                    )
                    return result

        # 7. Inspect full staged diff for total size cap and secret scanning
        rc_diff, diff_text, err_diff = run_safe_git(
            [
                "-c",
                "core.quotePath=false",
                f"--git-dir={clean_git_dir}",
                "diff",
                "--cached",
                "--text",
                "--no-color",
                "-U0",
                base_sha,
            ],
            clean_repo_dir,
        )
        if rc_diff != 0:
            result.error = f"Failed to generate staged diff: {err_diff}"
            return result

        diff_byte_len = len(diff_text.encode("utf-8"))
        if max_diff_bytes > 0 and diff_byte_len > max_diff_bytes:
            result.error = (
                f"Governance policy violation: total diff size ({diff_byte_len} bytes) "
                f"exceeds max_diff_bytes cap ({max_diff_bytes} bytes)"
            )
            return result

        secret_findings = scan_diff_for_secrets(diff_text, extra_secrets=extra_secrets)
        if secret_findings:
            result.secret_scan_findings = secret_findings
            summary = ", ".join(
                f"{item['path']}:{item['line']} ({item['rule']})"
                for item in secret_findings[:10]
            )
            result.error = (
                f"Secret scan failed: detected {len(secret_findings)} potential secret(s) "
                f"in added lines: {summary}"
            )
            return result

        # 8. Compute diff_stats (--numstat)
        rc_stat, numstat_out, _ = run_safe_git(
            [
                f"--git-dir={clean_git_dir}",
                "diff",
                "--cached",
                "--numstat",
                base_sha,
            ],
            clean_repo_dir,
        )
        files_count = len(changed_paths)
        insertions = 0
        deletions = 0
        if rc_stat == 0 and numstat_out:
            for line in numstat_out.splitlines():
                cols = line.split("\t")
                if len(cols) >= 3:
                    if cols[0].isdigit():
                        insertions += int(cols[0])
                    if cols[1].isdigit():
                        deletions += int(cols[1])
        result.diff_stats = {
            "files": files_count,
            "insertions": insertions,
            "deletions": deletions,
        }

        # 9. Construct commit message with trailers and commit using GitSeed identity
        full_message = commit_message.strip() or "feat(gitseed): automated seed implementation"
        if trailers:
            trailer_lines = [
                f"{k}: {v}" for k, v in trailers.items() if k and str(v).strip()
            ]
            if trailer_lines:
                full_message = f"{full_message}\n\n" + "\n".join(trailer_lines)

        commit_env = {
            "GIT_AUTHOR_NAME": "GitSeed",
            "GIT_AUTHOR_EMAIL": "gitseed@gitseed.local",
            "GIT_COMMITTER_NAME": "GitSeed",
            "GIT_COMMITTER_EMAIL": "gitseed@gitseed.local",
        }
        rc_commit, _, err_commit = run_safe_git(
            [
                f"--git-dir={clean_git_dir}",
                "commit",
                "-m",
                full_message,
            ],
            clean_repo_dir,
            extra_env=commit_env,
        )
        if rc_commit != 0:
            result.error = f"git commit failed in clean repository: {err_commit}"
            return result

        rc_sha, commit_sha, err_sha = run_safe_git(
            [f"--git-dir={clean_git_dir}", "rev-parse", "HEAD"],
            clean_repo_dir,
        )
        if rc_sha != 0 or not commit_sha:
            result.error = f"Failed to resolve committed SHA: {err_sha}"
            return result

        result.ok = True
        result.has_changes = True
        result.commit_sha = commit_sha.strip()
        return result
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


enforce_clean_room_git_policy = construct_clean_commit_and_verify_policy


def push_with_askpass(
    *,
    clean_repo_dir: Path,
    remote_url: str,
    branch: str,
    expected_remote_sha: str | None = None,
    username: str = "x-access-token",
    token: str = "",
    timeout: int = 120,
) -> tuple[bool, str]:
    """Push committed branch from clean_repo_dir using GIT_ASKPASS and --force-with-lease."""
    sanitize_git_dir(clean_repo_dir)
    clean_url = strip_url_credentials(remote_url)
    lease_arg = (
        f"--force-with-lease=refs/heads/{branch}:{expected_remote_sha}"
        if expected_remote_sha is not None
        else f"--force-with-lease=refs/heads/{branch}"
    )

    with tempfile.TemporaryDirectory(prefix="gitseed-askpass-") as tmp_dir:
        askpass_path = Path(tmp_dir) / "git-askpass.sh"
        askpass_script = (
            "#!/bin/sh\n"
            'case "$1" in\n'
            '  *Username*|*username*) printf "%s\\n" "$GITSEED_GIT_USERNAME" ;;\n'
            '  *) printf "%s\\n" "$GITSEED_GIT_PASSWORD" ;;\n'
            "esac\n"
        )
        askpass_path.write_text(askpass_script, encoding="utf-8")
        os.chmod(askpass_path, stat.S_IRWXU)

        extra_env: dict[str, str] = {}
        if token:
            extra_env = {
                "GIT_ASKPASS": str(askpass_path),
                "GITSEED_GIT_USERNAME": username or "x-access-token",
                "GITSEED_GIT_PASSWORD": token,
            }

        proto_override = (
            ["-c", "protocol.file.allow=always"] if is_local_repo_path(clean_url) else []
        )
        rc, out, err = run_safe_git(
            [
                *proto_override,
                "push",
                lease_arg,
                clean_url,
                f"HEAD:refs/heads/{branch}",
            ],
            clean_repo_dir,
            extra_env=extra_env,
            timeout=timeout,
        )
        if rc == 0:
            return True, out
        return False, err or out
