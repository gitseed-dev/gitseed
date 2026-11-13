# Changelog

All notable changes to **GitSeed** are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html). All packages in the monorepo (`gitseed-common`, `gitseed-core`, `gitseed-runner`, `gitseed-cli`, `gitseed-mcp`, and `web`) share a lockstep `MAJOR.MINOR.PATCH` version (`0.1.x`).

---

## [0.1.0] - 2026-11-11

### Added

- **5-Stage Governed SDLC, Fast-Track Pipeline & Signed Governance Receipts (`gitseed-core`, `gitseed-common`)**:
  - End-to-end orchestration across `Ideate → Plan → Execute → Review → Merge` with automatic specification locking once `Ideate` is approved, plus **⚡ Fast-Track to Code** (`Execute → Review → Merge`).
  - Parallel **Multi-Model Second Opinions** (up to 3 concurrent agent runs per stage) with side-by-side comparison and selection.
  - **Ed25519-Signed Governance Receipts (`gitseed.receipt.v1`)** attached to pull requests and verifiable offline via `gitseed receipt verify`, with explicit `enforced` vs. `estimated` spend classification and `run.output["trusted"]` broker verification.
  - Database-enforced PostgreSQL invariants (`CheckConstraint`s and partial indexes) across 23 tables with automated Alembic migrations on startup.
- **3-Phase Broker Container Runner & 6 CLI Runtime Adapters (`gitseed-runner`)**:
  - **Untrusted Agent, Trusted Broker (`prepare` → `agent` → `finalize`)** container split on Docker and Kubernetes (`batch/v1` `Job`): `prepare` clones repositories and strips credentials/hooks; `agent` executes coding CLIs with zero Git tokens behind an SSRF-hardened `egress-proxy`; `finalize` mounts `/workspace:ro`, stages changes into a fresh clean `.git` directory outside `/workspace`, enforces `blocked_paths` and added-line secret scanning, and pushes via `--force-with-lease`.
  - First-class adapters for `opencode`, `claude-code`, `codex`, `antigravity`, `gemini`, and `grok-build` with live `@agent` steering (`--continue`), parallel in-container `git worktree` task execution, and `synthesized_via` provenance tracking.
  - Support for owner-bound **Personal Mode CLI OAuth subscription bundles** (`cli_oauth`), cloud BYOK APIs via **LiteLLM** virtual keys with hard USD spend caps, and local open-weight models (**Ollama**, **vLLM**, **LM Studio**).
- **Web Control Plane, Docked Sessions & Isolated VS Code Web (`apps/web`, `gitseed-core`)**:
  - React 19 + TypeScript + Tailwind CSS v4 dashboard with guided 1-click onboarding, inline diff review comments, `⌘K` `@coordinator` drawer, and short-lived token-authenticated SSE streams.
  - Persistent `SessionDock` featuring `tmux`-backed `xterm.js` browser PTY terminals, Monaco file editor, and cookie-isolated **VS Code Web (`openvscode-server` on dedicated `:8423` `SANDBOX_ORIGIN`)**.
- **Multi-Repo Cloud PRs, Self-Healing CI & Local Folder Git Sync (`gitseed-core`)**:
  - Automated PR/MR creation, CI check/pipeline inspection, review comment sync, and API merge across **GitHub** (including 1-Click GitHub App Manifest flow), **GitLab**, and **Bitbucket**.
  - Inbound GitHub/GitLab webhooks for issue-to-Seed triggers, PR `@agent` comment steering, and self-healing CI failure remediation.
  - Zero-remote **Local Folder** Git Smart HTTP bare repository hosting with refname validation and sanitized `post-receive` working-tree auto-stash and fast-forward sync.
- **Developer CLI, Interactive TUI & 65-Tool MCP Server (`gitseed-cli`, `gitseed-mcp`)**:
  - `gitseed` CLI and split-pane terminal UI (`gitseed tui`) for seed management, offline Ed25519 receipt verification (`gitseed receipt verify`), log streaming, approvals, and `@coordinator` queries.
  - `gitseed-mcp` server exposing 65 annotated tools (`readOnlyHint`, `destructiveHint`, `openWorldHint`), 5 resources, 2 resource templates, and 4 prompts over `stdio`, Streamable HTTP (`POST /mcp`), and SSE.
- **Security, Cryptography & Kubernetes Production Hardening**:
  - Fail-closed startup secret validation (`make init`), `HKDF-SHA256` Fernet key derivation with `MultiFernet` zero-downtime key rotation (`GITSEED_SECRET_PREVIOUS_KEYS`), and Ed25519 receipt signing key rotation support.
  - Restricted `docker-socket-proxy` isolation, SSRF-hardened `egress-proxy` (blocking RFC 1918 private networks, loopback, and `169.254.169.254/32` cloud metadata after DNS resolution), Kubernetes `NetworkPolicy` egress isolation, `automountServiceAccountToken: false`, `PidsLimit: 512`, `CapDrop: ALL`, and `no-new-privileges`.
  - Adversarial red-team verification suite (`test_evil_agent_redteam.py`) covering 10 untrusted-agent attack vectors.

---

> **License Note**: Source code in `gitseed-dev/gitseed` (`core`, `runner`, `web`, `cli`, `mcp`, `common`) is licensed under the [Apache License, Version 2.0](./LICENSE) (see [`NOTICE`](./NOTICE) and [`TRADEMARKS.md`](./TRADEMARKS.md)).

