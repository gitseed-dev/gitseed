# `gitseed-runner` — Ephemeral Agent Sandbox Supervisor

`gitseed-runner` executes inside an isolated, non-root container (`gitseed/runner`) spawned by the GitSeed scheduler for each SDLC stage run (`ideate`, `plan`, `execute`, `review`, `merge`).

---

## 3-Phase Broker Execution Lifecycle (`prepare` → `agent` → `finalize`)

1. **Phase 1 — `prepare` (`GITSEED_RUNNER_MODE=prepare`, single-use `prepare` token, [`broker_modes.py`](./src/gitseed_runner/broker_modes.py))**:
   - Fetches the stage execution bundle (`GET /api/v1/runner/context`).
   - Clones or updates project repositories into `/workspace`, neutralizes `.git/hooks` and dangerous `.git/config` directives (`gitsafe.py`), strips credentials from `.git/config`, records trusted `base_heads` with `POST /api/v1/runner/prepare-complete`, and writes scrubbed agent context to `/gitseed-ctl/agent-context.json`.
2. **Phase 2 — `agent` (`GITSEED_RUNNER_MODE=agent`, scoped `agent` token, [`main.py`](./src/gitseed_runner/main.py) & [`adapters.py`](./src/gitseed_runner/adapters.py))**:
   - Executes **OpenCode** (`opencode`), **Claude Code** (`claude`), **Codex** (`codex`), **Gemini CLI** (`gemini`), **Antigravity** (`agy`), or **Grok CLI** (`grok`) with zero Git tokens.
   - Polls `POST /api/v1/runner/steering` every 3 seconds for cancellation, budget blocks, or live `@<agent_handle>` steering comments (resuming the CLI session with `--continue` / `--resume`).
   - Validates `/workspace/.gitseed/output.json` against the stage's Pydantic v2 schema (`IdeateStageOutput`, `PlanStageOutput`, `ExecuteStageOutput`, `ReviewStageOutput`, `MergeStageOutput`) and writes `/gitseed-ctl/agent-result.json`.
3. **Phase 3 — `finalize` (`GITSEED_RUNNER_MODE=finalize`, single-use `finalize` token, `:ro` `/workspace` mount, [`broker_modes.py`](./src/gitseed_runner/broker_modes.py) & [`gitsafe.py`](./src/gitseed_runner/gitsafe.py))**:
   - Mounts `/workspace` and `/gitseed-ctl` read-only (`:ro`) and fetches trusted finalize metadata (`GET /api/v1/runner/finalize-context`).
   - During `execute`, clones a clean repository into a private temporary directory outside `/workspace`, stages changes via `git --git-dir=<clean>/.git --work-tree=/workspace/<repo> add -A`, enforces `blocked_paths` and added-line secret scanning, pushes `gitseed/<project>/seed-<N>` via single-use `POST /api/v1/runner/git-push-token` and `--force-with-lease`, and reports the authoritative `trusted` result to `POST /api/v1/runner/complete`.

---

## Building the Runner Image

```bash
docker build -t gitseed/runner:dev ./apps/runner
```

### Third-party agent CLIs

| Runtime | Binary | License | In the image? |
| :--- | :--- | :--- | :--- |
| `opencode` | `opencode` | MIT | ✅ Pre-installed out of the box |
| `codex` | `codex` | Apache-2.0 | ✅ Pre-installed out of the box |
| `gemini` | `gemini` | Apache-2.0 | ✅ Pre-installed out of the box |
| `claude-code` | `claude` | Proprietary (Anthropic) | ✅ Pre-installed out of the box in local builds (`INCLUDE_PROPRIETARY_CLIS=true`, default); omitted in redistributed images |
| `antigravity` | `agy` | Proprietary (Google) | First-class adapter; install via `--build-arg ANTIGRAVITY_INSTALL_URL=<url>` or custom binary layer (falls back to `gemini` in OAuth mode if `agy` is absent) |
| `grok-build` | `grok` | Proprietary (xAI) | First-class adapter; install via `--build-arg GROK_CLI_INSTALL_URL=<url>` or custom binary layer |

The agent editor in the web UI checks which binaries your runner image actually contains and marks missing runtimes as **Not installed**. On Kubernetes, where the image can't be probed, set `RUNNER_RUNTIMES` on the API (e.g. `RUNNER_RUNTIMES=opencode,codex,gemini,claude-code`) to declare what your image contains.

**`opencode`, `codex`, `gemini`, and `claude-code` are pre-installed out of the box** in standard self-built images (`docker compose up -d` or `make build`, where `INCLUDE_PROPRIETARY_CLIS=true` is the default). **`antigravity` (`agy`) and `grok-build` (`grok`) have first-class runtime adapters** and support custom binary installation via build-time installer URLs (`ANTIGRAVITY_INSTALL_URL` / `GROK_CLI_INSTALL_URL`) or a derived Dockerfile.

Images published for third-party redistribution must omit proprietary vendor CLIs:

```bash
docker build --build-arg INCLUDE_PROPRIETARY_CLIS=false -t gitseed/runner ./apps/runner
```

To install `agy` / `grok` via custom installer URLs at build time:

```bash
docker build \
  --build-arg ANTIGRAVITY_INSTALL_URL="https://example.internal/install-agy.sh" \
  --build-arg GROK_CLI_INSTALL_URL="https://example.internal/install-grok.sh" \
  -t gitseed/runner:custom ./apps/runner
```

Or layer Claude Code, Antigravity, or Grok Build onto a published image:

```dockerfile
FROM gitseed/runner
USER root
RUN npm install -g @anthropic-ai/claude-code && npm cache clean --force
# Copy or install `agy` / `grok` into /usr/local/bin using the vendor's official distribution.
USER 1000:1000
```

Point GitSeed at your image with `RUNNER_IMAGE=your-org/gitseed-runner:tag`.

> You are responsible for complying with the terms of service of any model provider or agent CLI you connect to GitSeed (including restrictions on using consumer subscriptions such as Claude Pro/Max or ChatGPT Plus/Pro with third-party tools). Consumer subscription logins should belong to one person and be used for that person's own work. Use API keys for team-wide or unattended pipelines.

---

## Testing

```bash
uv run python -m pytest apps/runner/tests
```

