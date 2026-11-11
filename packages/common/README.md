# `gitseed-common` — Shared Schemas & Domain Contracts

`gitseed-common` is the foundational Python package shared across `gitseed-core`, `gitseed-runner`, `gitseed-cli`, and `gitseed-mcp`. It holds zero-I/O Pydantic v2 schemas, stage output validation contracts, and domain enums so every surface speaks an identical protocol.

---

## Package Structure

- **[`gitseed_common.enums`](./src/gitseed_common/enums.py)**:
  - `SeedStage` (`ideate`, `plan`, `execute`, `review`, `merge`)
  - `SeedStatus` (`staged`, `queued`, `running`, `awaiting_approval`, `blocked`, `failed`, `stopped`, `merged`, `closed`)
  - `SeedAutonomy` (`manual`, `supervised`, `autonomous`)
  - `RunStatus`, `ApprovalDecision`, `TaskStatus`, `SandboxStatus`, `AgentSessionStatus`
  - `AgentRuntime` (`opencode`, `claude-code`, `codex`, `antigravity`, `gemini`, `grok-build`)
  - `BackendType` (`git`, `inference`, `compute`, `tools`), `BackendHealth`, `ProjectRole`, `OrgRole`, `UserStatus`, `QueueStrategy`, and all `ActivityType` variants.
- **[`gitseed_common.schemas.runner_output`](./src/gitseed_common/schemas/runner_output.py)**:
  - Canonical Pydantic v2 schemas for `/workspace/.gitseed/output.json` produced by stage runners and validated by the control plane:
    - `IdeateStageOutput` (`title`, `description`, `specification`, `acceptance_criteria`, `summary_comment`)
    - `PlanStageOutput` (`plan`, `tasks: list[PlanTaskItem]`, `target_repositories`, `summary_comment`)
    - `ExecuteStageOutput` (`summary_comment`, `branch_name`, `commit_sha`, `pull_request_url`)
    - `ReviewStageOutput` (`approved`, `findings`, `summary_comment`)
    - `MergeStageOutput` (`merged`, `merge_commit_sha`, `summary_comment`)
- **[`gitseed_common.schemas`](./src/gitseed_common/schemas/)**:
  - Request/response DTOs for `auth`, `user`, `project`, `seed`, `receipt` (`GovernanceReceipt`, `SignedGovernanceReceiptEnvelope` for `gitseed.receipt.v1`), `run`, `sandbox`, `agent_session`, `agent`, `skill`, `backend`, `policy`, `settings`, `activity`, and `system`.

---

## Testing

```bash
uv run python -m pytest packages/common/tests
```
