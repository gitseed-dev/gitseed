# `AGENTS.md` — Coding Agent Guide for the GitSeed Monorepo

This file provides repository context and verification commands for AI coding agents (`@ideator`, `@planner`, `@coder`, `@reviewer`, `@merger`, `@coordinator`, OpenCode, Claude Code, Codex, Antigravity, Gemini CLI, and Grok Build) working inside the **GitSeed** monorepo.

---

## 1. Architecture Overview

GitSeed is a self-hosted control plane that orchestrates AI coding agents across a 5-stage SDLC (`ideate → plan → execute → review → merge`):

- **`packages/common` (`gitseed_common`)**: Zero-I/O shared enums (`enums.py`), stage output validation schemas (`schemas/runner_output.py`), and API DTOs.
- **`apps/core` (`gitseed_core`)**: FastAPI REST/SSE/WebSocket server (`api/v1/`), SQLModel + PostgreSQL models (`models/`), Alembic migrations (`migrations/`), and domain services (`services/`).
- **`apps/runner` (`gitseed_runner`)**: 3-phase containerized supervisor (`broker_modes.py`, `main.py`, `adapters.py`, `gitsafe.py`) split across `prepare` (clones repositories, neutralizes `.git/hooks` & `.git/config`, strips Git credentials), `agent` (executes `opencode`, `claude-code`, `codex`, `antigravity`, `gemini`, or `grok-build` with zero Git tokens, polls `@agent` steering, and validates `/workspace/.gitseed/output.json`), and `finalize` (mounts `/workspace:ro`, enforces `blocked_paths` & added-line secret scans in a clean `.git` directory, and pushes commits).
- **`apps/cli` (`gitseed_cli`)**: Typer + Rich terminal CLI (`commands/`) and interactive split-pane TUI (`tui/`).
- **`apps/mcp` (`gitseed_mcp`)**: 65-tool MCP server (`catalog.py`, `server.py`) supporting `stdio`, Streamable HTTP (`POST /mcp`), and SSE.
- **`apps/web`**: React 19 + TypeScript + Vite + Tailwind CSS v4 frontend (`src/`).

---

## 2. Key Invariants & Conventions

1. **Database & Migrations**:
   - SQLModel models in `apps/core/src/gitseed_core/models/` enforce invariants via PostgreSQL `CheckConstraint`s and partial indexes.
   - Keep Alembic migrations in `apps/core/src/gitseed_core/migrations/versions/` synchronized with SQLModel table definitions.
2. **Stage Contracts**:
   - Stage outputs written to `/workspace/.gitseed/output.json` are validated against `STAGE_OUTPUT_SCHEMAS` in `packages/common/src/gitseed_common/schemas/runner_output.py`.
   - Once `ideate` is approved, `seed.specification` is locked for downstream stages.
3. **Security & 3-Phase Broker Boundary**:
   - Never pass Git push credentials, `GITSEED_SECRET_KEY`, `LITELLM_MASTER_KEY`, or `/gitseed-secrets` into the untrusted `agent` container phase (`SECURITY.md`).
   - Never log or expose decrypted backend secrets (`decrypt_secrets`) or raw PAT tokens in API responses or container logs.

---

## 3. Verification Commands

Run these commands from the repository root to verify changes:

```bash
# Python unit & integration tests
uv run python -m pytest

# Python linting & type-checking
uv run ruff check .
uv run mypy packages/common apps/core apps/cli apps/mcp apps/runner

# Web UI linting, tests & production build check
pnpm --filter web lint
pnpm --filter web test
pnpm --filter web build
```

