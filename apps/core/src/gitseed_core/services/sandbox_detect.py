from __future__ import annotations

import json
import re
import tomllib
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import col, select

from gitseed_core.models.project import ProjectRepository
from gitseed_core.models.user import User
from gitseed_core.services import git_browse
from gitseed_core.services import projects as projects_service


def _apply_project_sandbox_overrides(
    base_config: dict[str, Any],
    project_sandbox_config: dict[str, Any] | None,
) -> dict[str, Any]:
    """Overlay non-empty settings from `project.sandbox_config` onto a base run config."""
    if not isinstance(project_sandbox_config, dict) or not project_sandbox_config:
        return base_config

    merged = dict(base_config)
    has_override = False

    img = str(project_sandbox_config.get("image") or "").strip()
    if img:
        merged["image"] = img
        has_override = True

    setup_cmd = str(project_sandbox_config.get("setup_command") or "").strip()
    if setup_cmd:
        merged["setup_command"] = setup_cmd
        has_override = True

    run_cmd = str(project_sandbox_config.get("run_command") or "").strip()
    if run_cmd:
        merged["run_command"] = run_cmd
        has_override = True

    test_cmd = str(project_sandbox_config.get("test_command") or "").strip()
    if test_cmd:
        merged["test_command"] = test_cmd
        has_override = True

    raw_globs = project_sandbox_config.get("artifact_globs")
    if isinstance(raw_globs, list):
        cleaned_globs = [str(g).strip() for g in raw_globs if str(g).strip()]
        if cleaned_globs:
            merged["artifact_globs"] = cleaned_globs
            has_override = True

    if has_override:
        merged["source"] = "project_settings"
        if not test_cmd and base_config.get("source") != "run_json":
            merged["test_command"] = None
    return merged


_CODE_BLOCK_RE = re.compile(
    r"```(?:bash|sh|shell|zsh|console|terminal|cmd)?\s*\n(.*?)```",
    re.IGNORECASE | re.DOTALL,
)
_SAFE_RUN_PREFIXES = (
    "python ",
    "python3 ",
    "uv run ",
    "poetry run ",
    "npm run ",
    "npm start",
    "pnpm ",
    "yarn ",
    "node ",
    "npx ",
    "go run ",
    "cargo run",
    "./gradlew ",
    "gradle ",
    "make ",
    "sh ",
    "bash ",
)
_SAFE_TEST_PREFIXES = (
    "pytest",
    "python -m pytest",
    "python3 -m pytest",
    "python -m unittest",
    "python3 -m unittest",
    "uv run pytest",
    "poetry run pytest",
    "npm test",
    "npm run test",
    "pnpm test",
    "yarn test",
    "vitest",
    "jest",
    "go test",
    "cargo test",
    "./gradlew test",
    "make test",
)


def _extract_doc_command_hints(markdown_text: str) -> dict[str, str]:
    """Extract safe `run_command`, `test_command`, and `setup_command` hints from README.md or AGENTS.md."""
    if not markdown_text or not markdown_text.strip():
        return {}

    hints: dict[str, str] = {}
    for match in _CODE_BLOCK_RE.finditer(markdown_text):
        block = match.group(1)
        for raw_line in block.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("$ "):
                line = line[2:].strip()
            elif line.startswith("> "):
                line = line[2:].strip()
            if not line or len(line) > 160:
                continue
            # Ignore multi-statement chains with dangerous redirects or subshells
            if any(
                tok in line
                for tok in ("sudo ", "rm -rf", "curl ", "wget ", "`", "$(")
            ):
                continue

            lower_line = line.lower()
            if "setup_command" not in hints and (
                lower_line.startswith("pip install ")
                or lower_line.startswith("pip3 install ")
                or lower_line.startswith("npm install")
                or lower_line.startswith("npm ci")
                or lower_line.startswith("pnpm install")
                or lower_line.startswith("yarn install")
            ):
                hints["setup_command"] = line
                continue

            if "test_command" not in hints and any(
                lower_line.startswith(p) for p in _SAFE_TEST_PREFIXES
            ):
                hints["test_command"] = line
                continue

            if (
                "run_command" not in hints
                and any(lower_line.startswith(p) for p in _SAFE_RUN_PREFIXES)
                and not any(lower_line.startswith(p) for p in _SAFE_TEST_PREFIXES)
                and "install" not in lower_line
            ):
                hints["run_command"] = line

    return hints


def _parse_pyproject_entrypoints(toml_text: str) -> dict[str, str]:
    """Inspect `pyproject.toml` for `[project.scripts]` or `[tool.poetry.scripts]` CLI entrypoints."""
    if not toml_text or not toml_text.strip():
        return {}
    try:
        data = tomllib.loads(toml_text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}

    result: dict[str, str] = {}
    proj = data.get("project")
    if isinstance(proj, dict):
        scripts = proj.get("scripts")
        if isinstance(scripts, dict) and scripts:
            first_script, target = next(iter(scripts.items()))
            if isinstance(first_script, str) and first_script.strip():
                result["run_command"] = first_script.strip()
            elif isinstance(target, str) and ":" in target:
                mod = target.split(":", 1)[0].strip()
                if mod:
                    result["run_command"] = f"python -m {mod}"

    tool = data.get("tool")
    if isinstance(tool, dict):
        poetry = tool.get("poetry")
        if isinstance(poetry, dict) and "run_command" not in result:
            p_scripts = poetry.get("scripts")
            if isinstance(p_scripts, dict) and p_scripts:
                first_script = next(iter(p_scripts.keys()))
                if isinstance(first_script, str) and first_script.strip():
                    result["run_command"] = first_script.strip()

    return result


async def get_sandbox_run_config(
    session: AsyncSession,
    actor: User,
    project_id_or_slug: str,
    *,
    repo_id: int | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    """Resolve sandbox run config from Project Settings, `.gitseed/run.json`, or stack auto-detection."""
    project = await projects_service.get_project(session, actor, project_id_or_slug)
    proj_cfg = (
        project.sandbox_config if isinstance(project.sandbox_config, dict) else {}
    )
    repo_rows = (
        await session.execute(
            select(ProjectRepository)
            .where(ProjectRepository.project_id == project.id)
            .order_by(
                col(ProjectRepository.is_primary).desc(),
                col(ProjectRepository.id).asc(),
            )
        )
    ).scalars().all()
    if not repo_rows:
        return _apply_project_sandbox_overrides(
            {
                "source": "default",
                "ref": ref or "main",
                "image": "python:3.12-slim",
                "setup_command": None,
                "run_command": "python3 --version",
                "test_command": "pytest",
                "artifact_globs": [],
            },
            proj_cfg,
        )

    repo = next(
        (r for r in repo_rows if repo_id is not None and r.id == repo_id), repo_rows[0]
    )
    target_ref = (ref or repo.default_branch or "main").strip()

    # 1. Check for `.gitseed/run.json` on `target_ref` (falling back to default_branch if missing)
    for candidate_ref in (
        [target_ref, repo.default_branch]
        if target_ref != repo.default_branch
        else [target_ref]
    ):
        try:
            blob = await git_browse.get_repo_blob(
                session,
                actor,
                project.id,
                repo.id,  # type: ignore[arg-type]
                ref=candidate_ref,
                path=".gitseed/run.json",
            )
            content = blob.get("content")
            if isinstance(content, str) and content.strip():
                parsed = json.loads(content)
                if isinstance(parsed, dict):
                    return _apply_project_sandbox_overrides(
                        {
                            "source": "run_json",
                            "ref": candidate_ref,
                            "image": str(
                                parsed.get("image") or "python:3.12-slim"
                            ).strip(),
                            "setup_command": str(
                                parsed.get("setup_command") or ""
                            ).strip()
                            or None,
                            "run_command": str(
                                parsed.get("run_command") or parsed.get("command") or ""
                            ).strip(),
                            "test_command": str(
                                parsed.get("test_command") or ""
                            ).strip()
                            or None,
                            "artifact_globs": [
                                str(g).strip()
                                for g in (parsed.get("artifact_globs") or [])
                                if str(g).strip()
                            ],
                        },
                        proj_cfg,
                    )
        except Exception:
            pass

    # 2. Modest auto-detection from root repository tree + README/AGENTS.md hints
    effective_ref = target_ref
    entries: list[dict[str, Any]] = []
    readme_text = ""
    for candidate_ref in (
        [target_ref, repo.default_branch]
        if target_ref != repo.default_branch
        else [target_ref]
    ):
        try:
            tree = await git_browse.get_repo_tree(
                session,
                actor,
                project.id,
                repo.id,  # type: ignore[arg-type]
                ref=candidate_ref,
                path="",
            )
            entries = tree.get("entries") or []
            readme_obj = tree.get("readme")
            if isinstance(readme_obj, dict) and isinstance(
                readme_obj.get("content"), str
            ):
                readme_text = readme_obj["content"]
            effective_ref = candidate_ref
            if entries:
                break
        except Exception:
            continue

    names = {str(e.get("name") or "") for e in entries if isinstance(e, dict)}
    lower_map = {n.lower(): n for n in names}

    # Gather documentation command hints from AGENTS.md / CLAUDE.md and README.md
    doc_hints: dict[str, str] = {}
    for agent_doc_key in ("agents.md", "claude.md"):
        if agent_doc_key in lower_map:
            try:
                doc_blob = await git_browse.get_repo_blob(
                    session,
                    actor,
                    project.id,
                    repo.id,  # type: ignore[arg-type]
                    ref=effective_ref,
                    path=lower_map[agent_doc_key],
                )
                doc_hints.update(
                    _extract_doc_command_hints(str(doc_blob.get("content") or ""))
                )
            except Exception:
                pass
    if readme_text:
        for k, v in _extract_doc_command_hints(readme_text).items():
            doc_hints.setdefault(k, v)

    # Android / Gradle project
    if (
        "gradlew" in lower_map
        or "build.gradle" in lower_map
        or "build.gradle.kts" in lower_map
    ):
        return _apply_project_sandbox_overrides(
            {
                "source": "auto_detected",
                "ref": effective_ref,
                "image": "eclipse-temurin:21-jdk",
                "setup_command": "chmod +x gradlew 2>/dev/null || true",
                "run_command": doc_hints.get("run_command")
                or "./gradlew assembleDebug",
                "test_command": doc_hints.get("test_command") or "./gradlew test",
                "artifact_globs": ["**/*.apk", "**/*.aab", "**/build/libs/*.jar"],
            },
            proj_cfg,
        )

    # Node.js / TypeScript project
    if "package.json" in lower_map:
        run_cmd = doc_hints.get("run_command") or "npm start"
        test_cmd = doc_hints.get("test_command") or "npm test"
        try:
            pkg_blob = await git_browse.get_repo_blob(
                session,
                actor,
                project.id,
                repo.id,  # type: ignore[arg-type]
                ref=effective_ref,
                path=lower_map["package.json"],
            )
            pkg_json = json.loads(str(pkg_blob.get("content") or "{}"))
            scripts = pkg_json.get("scripts") if isinstance(pkg_json, dict) else {}
            if isinstance(scripts, dict):
                if "start" in scripts:
                    run_cmd = "npm start"
                elif "build" in scripts:
                    run_cmd = "npm run build"
                elif "dev" in scripts and doc_hints.get("run_command"):
                    run_cmd = doc_hints["run_command"]
                if "test" in scripts:
                    test_cmd = "npm test"
        except Exception:
            pass
        return _apply_project_sandbox_overrides(
            {
                "source": "auto_detected",
                "ref": effective_ref,
                "image": "node:22-bookworm-slim",
                "setup_command": doc_hints.get("setup_command") or "npm install",
                "run_command": run_cmd,
                "test_command": test_cmd,
                "artifact_globs": ["dist/**/*", "build/**/*", "*.tgz"],
            },
            proj_cfg,
        )

    # Python project
    py_files = sorted(
        n
        for n in names
        if n.endswith(".py")
        and not n.startswith("test_")
        and not n.endswith("_test.py")
        and n != "setup.py"
        and n != "conftest.py"
    )
    if (
        "pyproject.toml" in lower_map
        or "requirements.txt" in lower_map
        or py_files
        or str(doc_hints.get("run_command") or "").startswith(
            ("python", "uv run", "poetry run")
        )
    ):
        setup_parts: list[str] = []
        pyproject_hints: dict[str, str] = {}
        if "requirements.txt" in lower_map:
            setup_parts.append(f"pip install -q -r {lower_map['requirements.txt']}")
        if "pyproject.toml" in lower_map:
            if not setup_parts:
                setup_parts.append(
                    "pip install -q -e . pytest 2>/dev/null || pip install -q pytest"
                )
            try:
                pyp_blob = await git_browse.get_repo_blob(
                    session,
                    actor,
                    project.id,
                    repo.id,  # type: ignore[arg-type]
                    ref=effective_ref,
                    path=lower_map["pyproject.toml"],
                )
                pyproject_hints = _parse_pyproject_entrypoints(
                    str(pyp_blob.get("content") or "")
                )
            except Exception:
                pass
        elif not setup_parts and doc_hints.get("setup_command"):
            setup_parts.append(doc_hints["setup_command"])

        preferred_entry = next(
            (
                lower_map[candidate]
                for candidate in ("main.py", "app.py", "cli.py", "run.py")
                if candidate in lower_map
            ),
            py_files[0] if py_files else None,
        )
        if preferred_entry:
            run_cmd = f"python {preferred_entry}"
        elif pyproject_hints.get("run_command"):
            run_cmd = pyproject_hints["run_command"]
        elif doc_hints.get("run_command"):
            run_cmd = doc_hints["run_command"]
        else:
            run_cmd = "pytest -v"

        test_cmd = doc_hints.get("test_command") or "pytest -v"
        return _apply_project_sandbox_overrides(
            {
                "source": "auto_detected",
                "ref": effective_ref,
                "image": "python:3.12-slim",
                "setup_command": " && ".join(setup_parts) if setup_parts else None,
                "run_command": run_cmd,
                "test_command": test_cmd,
                "artifact_globs": ["dist/*", "*.whl"],
            },
            proj_cfg,
        )

    # Rust project
    if "cargo.toml" in lower_map:
        return _apply_project_sandbox_overrides(
            {
                "source": "auto_detected",
                "ref": effective_ref,
                "image": "rust:1.82-slim",
                "setup_command": None,
                "run_command": doc_hints.get("run_command") or "cargo run",
                "test_command": doc_hints.get("test_command") or "cargo test",
                "artifact_globs": ["target/release/*"],
            },
            proj_cfg,
        )

    # Go project
    if "go.mod" in lower_map:
        return _apply_project_sandbox_overrides(
            {
                "source": "auto_detected",
                "ref": effective_ref,
                "image": "golang:1.23-alpine",
                "setup_command": None,
                "run_command": doc_hints.get("run_command") or "go run .",
                "test_command": doc_hints.get("test_command") or "go test ./...",
                "artifact_globs": ["bin/*"],
            },
            proj_cfg,
        )

    if doc_hints.get("run_command") or doc_hints.get("test_command"):
        return _apply_project_sandbox_overrides(
            {
                "source": "auto_detected",
                "ref": effective_ref,
                "image": "python:3.12-slim",
                "setup_command": doc_hints.get("setup_command"),
                "run_command": doc_hints.get("run_command") or "ls -la",
                "test_command": doc_hints.get("test_command") or "pytest",
                "artifact_globs": [],
            },
            proj_cfg,
        )

    return _apply_project_sandbox_overrides(
        {
            "source": "default",
            "ref": effective_ref,
            "image": "python:3.12-slim",
            "setup_command": None,
            "run_command": "ls -la",
            "test_command": "pytest",
            "artifact_globs": [],
        },
        proj_cfg,
    )

