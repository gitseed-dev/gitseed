# `web` — GitSeed Control Plane Web UI

The GitSeed Web UI (`apps/web`) is a responsive React 19 + TypeScript + Tailwind CSS v4 single-page application served on port `8421` (via Caddy in production or Vite in development).

---

## Key Capabilities

- **End-to-End Seed Workspace (Table & Kanban Board Views)**:
  - Visual 5-stage pipeline (`Ideate → Plan → Execute → Review → Merge`) with interactive Kanban board (`SeedsBoardView`) and filterable table views, locked-spec indicators, task checklists, live container run logs, unified/split Git diff viewer, file attachments, seed links, and role-gated approval banners.
- **Live `@agent` Discussion, Real-Time SSE & Voice Dictation**:
  - Real-time Server-Sent Events (`/api/v1/events`) keep stages, runs, and timelines synchronized across tabs without polling, with built-in browser speech-to-text dictation for seed prompts and comments.
- **`⌘K` Global Command Palette & `@coordinator` Assistant**:
  - Jump across projects, seeds, backends, agents, skills, and policies, or query `@coordinator` in natural language to triage blocked work, brainstorm ideas, and one-click create proposed Seeds.
- **Persistent `SessionDock` (Live Agent Sessions, `xterm.js` PTY, Monaco & VS Code Web)**:
  - Docked bottom workspace supporting multi-tab **Live Agent Sessions** (`@coder`, `@ideator`, `@planner`), interactive **Browser PTY Terminals** (`@xterm/xterm` over WebSocket), in-browser **Monaco Code Editor** with workspace Git status/commit/push, and cookie-isolated **VS Code Web (`openvscode-server` served on dedicated `:8423` `SANDBOX_ORIGIN`)**.
- **Unified Resource & Governance Management**:
  - Configure Inference backends (LiteLLM API keys, local Ollama/vLLM/LM Studio, or interactive CLI subscription auth), Git providers (GitHub, GitLab, Bitbucket, Local Folder), Compute backends (Docker, Kubernetes), custom Agents (with benchmark evaluation suites) & Skills, Stage Policies, and Organization Settings.

---

## Development & Testing

```bash
# Install dependencies (from repo root)
pnpm --filter web install

# Start Vite dev server (proxies /api to http://localhost:8420)
pnpm --filter web dev

# Lint TypeScript / React code with oxlint
pnpm --filter web lint

# Run unit & component test suite (Node.js test runner + custom TSX loader)
pnpm --filter web test

# Typecheck and build production bundle
pnpm --filter web build
```
