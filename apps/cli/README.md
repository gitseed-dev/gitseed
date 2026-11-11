# `gitseed-cli` — GitSeed Developer CLI (`gitseed`)

`gitseed-cli` provides a fast, ergonomic terminal interface (`gitseed`) modeled after `kubectl` and `gh` for operating the GitSeed control plane from local workstations or CI/CD pipelines.

---

## Installation

```bash
# From a monorepo checkout
uv sync --all-packages
uv run gitseed --help

# Or install globally as a standalone tool (once published to PyPI, or from workspace wheels)
uv tool install gitseed-cli
```

---

## Command Reference

### Authentication & Context
| Command | Description |
| :--- | :--- |
| `gitseed login [API_URL]` | Authenticate with a Personal Access Token (`gsp_...`) or email/password and save to `~/.config/gitseed/`. |
| `gitseed logout` | Remove stored credentials for the active context. |
| `gitseed whoami` | Display the authenticated user, role, and control-plane URL. |
| `gitseed status` | Show control-plane health, version, and authenticated identity. |
| `gitseed config ...` | Manage named contexts (`get-contexts`, `use-context`, `use-project`). |

### Resource Inspection & Declarative Manifests
| Command | Description |
| :--- | :--- |
| `gitseed get <resource>` | List `projects`, `seeds`, `tasks`, `runs`, `agents`, `benchmarks`, `skills`, `policies`, `backends`, `sandboxes`, `sessions`, `users`, or `tokens` (`-o table\|wide\|json\|md`). |
| `gitseed describe <resource> <ref>` | Show rich detail view for a resource (e.g., `gitseed describe seed my-proj#1`). |
| `gitseed create seed [TITLE] [-f <file>]` | Create a Seed from flags (`--summary`, `--spec`, `-c`, `--autonomy`, `--stage`, `--staged`, `--fast-track`) or a Markdown manifest (`-f seed.md`). |
| `gitseed apply -f <path>` | Declaratively create or update resources (`Seed`, `Agent`, `Skill`, `Policy`, `Project`) from a Markdown manifest file or directory. |
| `gitseed edit <resource> <ref>` | Open a resource in `$EDITOR` as a Markdown manifest and apply changes on save. |
| `gitseed archive` / `restore <resource> <ref>` | Archive or restore a `project`, `agent`, or `skill`. |
| `gitseed delete <resource> <ref>` | Delete a `token`, `backend`, `user`, `sandbox`, `session`, or reset a project `policy` override. |

### SDLC Workflow, Sessions & Sandbox Operations
| Command | Description |
| :--- | :--- |
| `gitseed start <seed>` | Start a staged Seed so its stage agent begins running. |
| `gitseed advance <seed> [--stage STAGE] [--fast-track]` | Manually advance a Seed to the next (or specified) SDLC stage, or fast-track to `execute`. |
| `gitseed move-back <seed> --stage STAGE [-m MSG]` | Return a Seed to an earlier SDLC stage and re-queue it. |
| `gitseed stop <seed>` / `resume <seed>` | Stop an active Seed's agent run or resume a stopped Seed. |
| `gitseed retry <seed>` | Retry a failed, blocked, or stopped Seed stage run. |
| `gitseed cancel <run-or-seed>` | Cancel an active stage container run. |
| `gitseed close <seed>` / `reopen <seed>` | Close an open Seed without merging, or reopen a closed Seed. |
| `gitseed approve <seed> [-m MSG] [--checkpoint USD]` | Approve an active stage gate or raise a Seed's spend checkpoint. |
| `gitseed request-changes <seed> -m <feedback>` | Request changes on a gated stage and re-trigger the stage agent. |
| `gitseed reject <seed> -m <reason>` | Reject a Seed's stage gate and close the Seed. |
| `gitseed comment <seed> <message>` | Post a timeline discussion comment on a Seed. |
| `gitseed steer <seed> <message> [-a @agent]` | Steer a Seed's active stage agent with an `@mention` directive. |
| `gitseed logs <seed-or-run> [-f] [--tail N]` | View or follow (`-f`) live container logs for a Seed or run. |
| `gitseed diff <seed>` | Render Git branch commit history, file stats, and unified diff for a Seed. |
| `gitseed receipt get <seed> [-f <file>]` | Fetch a Seed's Ed25519-signed `gitseed.receipt.v1` Governance Receipt (`-o table\|json\|md` or write JSON envelope to `-f receipt.json`). |
| `gitseed receipt verify <file-or-seed> [--offline -k <pubkey.pem>]` | Cryptographically verify a Governance Receipt against the server's active/rotated Ed25519 keys or completely `--offline` against a pinned PEM key (`-k`). |
| `gitseed events [-s SEED] [-w]` | View or watch (`-w`) the activity audit trail for a project or Seed. |
| `gitseed ask "<question>" [--mode brainstorm] [--propose-seeds]` | Ask `@coordinator` a question or brainstorm structured Seed proposals. |
| `gitseed sandbox <start\|input\|stop\|logs\|config\|delete>` | Manage interactive project job and terminal sandbox containers. |
| `gitseed session <list\|start\|send\|show\|stop\|delete>` | Manage interactive coding agent sessions and message transcripts. |
| `gitseed tui [-p PROJECT] [--once]` | Launch the interactive split-pane Terminal UI (`1` Seeds, `2` Projects, `3` Runs & Sandboxes, `4` Backends & Agents). |

---

## Interactive Terminal UI (`gitseed tui`)

Launch the `k9s`/`lazygit`-style terminal dashboard with `gitseed tui` (or `gitseed tui --once` to render a single snapshot):

- **Views (`1`–`4`)**: Switch between **Seeds**, **Projects**, **Runs & Sandboxes**, and **Backends & Agents**.
- **5-Tab Seed Inspector (`Tab` / `o` `t` `l` `d` `m`)**: Inspect **Overview & Spec**, **Tasks**, **Live Logs**, **Git Diff**, and **Timeline & `@Steer`** alongside a visual 5-stage progress bar.
- **Single-Keystroke Actions**:
  - `a` Approve active stage gate · `x` Request changes · `X` Reject seed
  - `c` Steer active `@agent` · `C` Post timeline comment · `n` Create new seed
  - `r` Retry failed/blocked seed · `z` Cancel active run · `K` Ask `@coordinator`
  - `/` Filter list · `p` Cycle project filter · `?` Help overlay · `q` Quit

---

## Testing

```bash
uv run python -m pytest apps/cli/tests
```

