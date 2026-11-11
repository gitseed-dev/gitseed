# Contributing to GitSeed

Thank you for your interest in contributing to **GitSeed**!

## Development Setup

### Prerequisites

- **Python 3.12+** with [`uv`](https://docs.astral.sh/uv/)
- **Node.js 22+** with `pnpm` (`corepack enable`)
- **Docker** (optional, for running the full self-hosted stack or container sandboxes)

### Install Dependencies & Initialize Local Secrets

```bash
# Install all Python workspace packages and dev dependencies
uv sync --all-packages

# Install Web UI dependencies
pnpm --filter web install

# Generate a local .env with cryptographic secrets (required to boot gitseed-core)
./scripts/init.sh      # or .\scripts\init.ps1 on Windows PowerShell
```

## Verification & Pre-PR Checks

Before opening a Pull Request, run the verification suite from the repository root:

```bash
# 1. Python unit & integration tests
uv run python -m pytest

# 2. Python linting & type-checking
uv run ruff check .
uv run mypy packages/common apps/core apps/cli apps/mcp apps/runner

# 3. Web UI tests, linting, and production build check
pnpm --filter web lint
pnpm --filter web test
pnpm --filter web build
```

## Key Architectural Rules

1. **Database & Migrations**: Keep Alembic migrations in `apps/core/src/gitseed_core/migrations/versions/` synchronized with SQLModel table definitions in `apps/core/src/gitseed_core/models/`.
2. **Stage Contracts**: Stage outputs written to `/workspace/.gitseed/output.json` are validated against `STAGE_OUTPUT_SCHEMAS` in `packages/common/src/gitseed_common/schemas/runner_output.py`.
3. **3-Phase Broker & Secret Isolation**: Preserve the `prepare → agent → finalize` trust boundary ([`SECURITY.md`](./SECURITY.md) & [`AGENTS.md`](./AGENTS.md)). Never pass Git push credentials, `GITSEED_SECRET_KEY`, or `/gitseed-secrets` into the untrusted `agent` container, and never log decrypted backend secrets (`decrypt_secrets`) or raw PAT tokens.

## Contributor License Agreement (CLA) & License

GitSeed is licensed under the [Apache License, Version 2.0](./LICENSE). Before your first pull request can be merged, you will need to accept the **[GitSeed Individual Contributor License Agreement](./CLA.md)**. It is based on the Apache Software Foundation ICLA.

- **You keep the copyright** to your work. You grant **Trent Seed** (and any successor entity the project is assigned to) a perpetual, worldwide, irrevocable, royalty-free license to use, modify, sublicense, and distribute your contribution under the Apache License, Version 2.0 or any other license (including commercial editions).
- You confirm that the contribution is your original creation and that you have the legal right to grant this license.
- **Signing takes one comment.** When you open your first PR, the CLA Assistant bot will ask you to reply with `I have read the CLA Document and I hereby sign the CLA`. It covers all your future contributions.
- If you are contributing on behalf of your employer, make sure you have permission to do so (contact **legal@gitseed.dev** if your employer requires a Corporate CLA).

## Code of Conduct & Trademarks

- Everyone participating in the project is expected to follow our [Code of Conduct](./CODE_OF_CONDUCT.md).
- The code is open source, but the "GitSeed" name and logo are trademarks. See [TRADEMARKS.md](./TRADEMARKS.md) for what's allowed (forks and "works with GitSeed" are welcome).
