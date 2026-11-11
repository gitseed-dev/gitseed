# `gitseed-core` — Control Plane API & Background Scheduler

`gitseed-core` is the central backend service for GitSeed. It exposes the FastAPI REST, SSE, and WebSocket control plane (`/api/v1/*`), manages database persistence and Alembic migrations, enforces governance policies and spend caps, routes model calls through LiteLLM, and runs the background stage scheduler and container reconciler.

---

## Core Responsibilities

- **REST, SSE & WebSocket Control Plane (`gitseed_core.api.v1`)**:
  - 22 modular routers across 20 route modules covering `auth`, `users`, `projects`, `seeds`, `seed_attachments`, `seed_links`, `receipts` (Ed25519 governance receipts & public keys), `runs`, `sandboxes` (plus PTY WebSocket), `agent_sessions` (plus PTY WebSocket), `sessions` (file tree & Git workspace ops), `vscode` (`openvscode-server` HTTP/WS proxy isolated on `:8423` `SANDBOX_ORIGIN`), `local_git` (Git Smart HTTP bare repo hosting), `webhooks` (GitHub/GitLab/Bitbucket events), `events` (short-lived token SSE), `agents`, `skills`, `backends`, `policies`, `settings`, `activity`, and internal 3-phase `runner` broker endpoints.
- **5-Stage SDLC State Machine & Signed Governance Receipts (`services/seeds.py`, `services/runner_bridge.py`, `services/receipts.py`)**:
  - Orchestrates `ideate → plan → execute → review → merge` (plus Fast-Track `execute → review → merge`), enforces spec locking after Ideate, coordinates single-use `prepare` / `agent` / `finalize` broker tokens, persists `run.output["trusted"]`, compiles Ed25519-signed `gitseed.receipt.v1` provenance receipts, and manages human approval gates and `request_changes` loops.
- **Compute Scheduler, Egress Guard, Sandboxes & Live Agent Sessions (`services/scheduler.py`, `services/compute_runtime.py`, `services/egress_proxy.py`, `services/sandboxes.py`, `services/agent_sessions.py`)**:
  - Polls queued stage runs, provisions 3-phase non-root Docker containers (via `docker-socket-proxy`) or Kubernetes Jobs behind the DNS-pinned SSRF-hardened `egress-proxy`, enforces monthly project budgets and per-seed spend checkpoints, and supervises interactive developer sandboxes, multi-turn Live Agent Sessions, browser PTY terminals (`services/session_pty.py`), and embedded VS Code Web (`services/session_vscode.py`).
- **Git, Local Folders & Pull Request Integration (`services/git_auth.py`, `services/git_browse.py`, `services/git_changes.py`, `services/git_merge.py`, `services/local_git.py`, `services/webhooks.py`)**:
  - Connects to GitHub, GitLab, Bitbucket, and built-in Local Folder bare Git repositories for repo discovery, branch diff inspection, automated PR creation/updates, remote branch merging, self-healing CI remediation, and inbound webhook synchronization.
- **Security & Secret Encryption (`security.py`, `services/cli_auth.py`)**:
  - Fail-closed startup secret validation, Argon2 password hashing, JWT session tokens, Personal Access Tokens (`gsp_*`), ephemeral phase-scoped runner tokens, `HKDF-SHA256` `MultiFernet` encryption at rest for backend secrets, Valkey/Redis login rate-limiting, and owner-bound Personal Mode OAuth capture for subscription CLIs.

---

## Running Locally

```bash
# Generate cryptographic secrets in .env (required by fail-closed startup checks)
./scripts/init.sh      # or .\scripts\init.ps1 on Windows PowerShell

# Apply Alembic database migrations
uv run --package gitseed-core alembic -c apps/core/alembic.ini upgrade head

# Start the FastAPI server on :8420
uv run --package gitseed-core uvicorn gitseed_core.main:app --reload --port 8420

# Start the background stage scheduler
uv run --package gitseed-core python -m gitseed_core.main
```

---

## Testing

```bash
uv run python -m pytest apps/core/tests
```
