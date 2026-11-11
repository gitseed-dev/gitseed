export type ActorType = 'user' | 'agent' | 'system'

export type ActivityType =
  | 'seed.created'
  | 'seed.autonomy_changed'
  | 'seed.merged'
  | 'seed.closed'
  | 'seed.reopened'
  | 'seed.stopped'
  | 'seed.resumed'
  | 'stage.changed'
  | 'approval.granted'
  | 'approval.rejected'
  | 'approval.changes_requested'
  | 'run.started'
  | 'run.succeeded'
  | 'run.failed'
  | 'run.cancelled'
  | 'run.timed_out'
  | 'policy.updated'
  | 'budget.raised'
  | 'budget.blocked'
  | 'project.created'
  | 'project.archived'
  | 'project.restored'
  | 'user.invited'
  | 'user.activated'
  | 'user.suspended'
  | 'user.reactivated'
  | 'repository.added'
  | 'repository.removed'
  | 'backend.granted'
  | 'backend.revoked'
  | 'backend.secret_updated'
  | 'member.added'
  | 'member.role_changed'
  | 'member.removed'
  | 'settings.updated'
  | 'pat.created'
  | 'pat.revoked'
  | 'sandbox.started'

export type AgentKind = 'stage' | 'assistant'

export type AgentRuntime =
  | 'opencode'
  | 'claude-code'
  | 'codex'
  | 'antigravity'
  | 'gemini'
  | 'grok-build'

/** Which agent CLI runtimes are installed in the runner image (`available: null` = unknown). */
export interface AgentRuntimeAvailability {
  available: Partial<Record<AgentRuntime, boolean>> | null
  source: 'config' | 'probe' | 'unknown'
  image: string | null
}

export type OrgRole = 'admin' | 'member'

export type ProjectRole = 'maintainer' | 'developer' | 'viewer'

export type ProjectVisibility = 'private' | 'public'

export type UserStatus = 'invited' | 'active' | 'suspended'

export type BackendType = 'git' | 'inference' | 'compute' | 'tools'

export type BackendHealth = 'unknown' | 'healthy' | 'degraded' | 'unreachable'

export type SeedStage = 'ideate' | 'plan' | 'execute' | 'review' | 'merge'

export type SeedStatus =
  | 'staged'
  | 'queued'
  | 'running'
  | 'awaiting_approval'
  | 'blocked'
  | 'failed'
  | 'stopped'
  | 'merged'
  | 'closed'

export type SeedAutonomy = 'manual' | 'supervised' | 'autonomous'

export type TaskStatus = 'pending' | 'running' | 'done' | 'failed' | 'skipped'

export type RunStatus =
  | 'queued'
  | 'starting'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'cancelled'
  | 'timed_out'

export type SandboxStatus =
  | 'starting'
  | 'running'
  | 'succeeded'
  | 'failed'
  | 'stopped'
  | 'timed_out'

export type ApprovalDecision = 'approved' | 'rejected' | 'changes_requested'

export type QueueStrategy = 'fifo' | 'fair_share'

export type BudgetEnforcement = 'enforced' | 'estimated' | 'unpriced'

export interface InvitePreview {
  email: string
  name: string
  org_role: OrgRole
  invite_expires_at: string | null
}

export interface UserPreferences {
  theme?: 'dark' | 'light' | 'system'
  theme_override?: 'dark' | 'light' | 'system' | null
  ambient_bg?: boolean
  ambient_bg_override?: boolean | null
  [key: string]: unknown
}

export interface User {
  id: string
  email: string
  name: string
  preferred_name: string | null
  org_role: OrgRole
  status: UserStatus
  preferences: UserPreferences
  invite_expires_at?: string | null
  last_active_at: string | null
  suspended_at?: string | null
  suspended_by?: string | null
  created_at: string | null
  created_by?: string | null
  updated_at?: string | null
}

export interface UserInviteCreated extends User {
  invite_token: string
  invite_url: string
}

export interface PersonalAccessToken {
  id: string
  name: string
  token_prefix: string
  last_used_at: string | null
  expires_at: string
  revoked_at: string | null
  created_at: string | null
}

export interface PersonalAccessTokenCreated extends PersonalAccessToken {
  token: string
}

export interface OrgSettings {
  default_autonomy: SeedAutonomy
  default_monthly_budget: string
  default_seed_budget_cap: string | null
  default_spend_checkpoint: string | null
  max_concurrent_runners: number
  max_sandbox_sessions: number
  default_project_concurrency: number
  queue_strategy: QueueStrategy
  pat_lifetime_days: number
  default_theme?: 'dark' | 'light' | 'system'
  default_ambient_bg?: boolean
  default_show_guide?: boolean
  default_stage_seeds?: boolean
  enforcement?: BudgetEnforcement
  enforcement_detail?: string | null
  updated_at: string | null
  updated_by: string | null
}

export interface Backend {
  id: string
  name: string
  type: BackendType
  provider: string
  endpoint: string | null
  config: Record<string, unknown>
  secret_keys: string[]
  capacity: number | null
  access_restricted: boolean
  allowed_agent_ids: string[]
  health: BackendHealth
  health_checked_at: string | null
  health_latency_ms: number | null
  health_error: string | null
  enabled: boolean
  created_by: string | null
  created_at: string | null
  updated_at: string | null
  updated_by: string | null
}

export interface Agent {
  id: string
  handle: string
  name: string
  kind: AgentKind
  template: string | null
  instructions: string
  runtime: AgentRuntime
  model: string
  compatibility_warning?: string | null
  skills: string[]
  tools: string[]
  enabled: boolean
  allowed_backend_ids: string[]
  run_count: number
  succeeded_runs: number
  total_cost: string
  first_pass_rate?: number | null
  avg_cost_per_seed?: string
  avg_attempts?: number
  archived_at: string | null
  archived_by: string | null
  created_at: string | null
  created_by: string | null
  updated_at: string | null
}

export interface AgentBenchmarkEntry {
  agent_id: string
  agent_handle: string
  agent_name: string
  runtime: AgentRuntime
  model: string
  stage: SeedStage | null
  run_count: number
  succeeded_runs: number
  seed_count: number
  stage_instances: number
  first_pass_approved_count: number
  evaluated_gate_count: number
  first_pass_approval_rate: number | null
  technical_success_rate: number
  avg_attempts_per_stage: number
  total_cost: string
  avg_cost_per_seed: string
  avg_cost_per_stage: string
  total_tokens: number
  avg_tokens_per_run: number
  avg_duration_seconds: number | null
}

export interface AgentBenchmarksResponse {
  project_id: string | null
  stage: SeedStage | null
  entries: AgentBenchmarkEntry[]
  insights: string[]
}

export interface Skill {
  id: string
  slug: string
  name: string
  description: string
  content: string
  archived_at?: string | null
  archived_by?: string | null
  created_at: string | null
  created_by: string | null
  updated_at: string | null
}

export interface StagePolicy {
  id: string | null
  project_id: string | null
  stage: SeedStage
  enabled: boolean
  agent_id: string | null
  requires_approval: boolean
  min_approvers: number
  min_approver_role: ProjectRole
  allow_auto_approve: boolean
  allow_self_approval: boolean
  allow_second_opinions?: boolean
  max_attempts: number
  timeout_minutes: number
  min_acceptance_criteria?: number | null
  max_acceptance_criteria?: number | null
  max_tasks?: number | null
  overridden_fields: string[]
  /** This level's own stage instructions (org row, or the project's additions) */
  instructions?: string | null
  /** Project policies: the org's stage instructions, which come first */
  inherited_instructions?: string | null
  updated_at: string | null
  updated_by: string | null
}

export interface SuggestedFollowUpSeed {
  title: string
  description?: string
  rationale?: string | null
}

export interface ProjectSandboxConfig {
  image?: string | null
  setup_command?: string | null
  run_command?: string | null
  test_command?: string | null
  artifact_globs?: string[]
  require_tests_pass?: boolean
  enable_dependency_cache?: boolean
  enable_warm_workspace?: boolean
  batch_execute_tasks?: boolean
  strict_cli_execution?: boolean
  git_clone_depth?: number
  auto_rebase_seed_branch?: boolean
  auto_create_pr?: boolean
  stage_seeds_by_default?: boolean
  blocked_paths?: string[]
  webhook_config?: Record<string, unknown>
}

export interface ProjectWebhookConfig {
  project_id: string
  enabled: boolean
  has_secret: boolean
  secret_preview: string | null
  secret: string | null
  github_webhook_url: string
  gitlab_webhook_url: string
  issue_label_trigger: string
  auto_create_seed_on_issue: boolean
  sync_pr_comments: boolean
  auto_remediate_ci: boolean
  auto_create_pr: boolean
  outbound_url: string | null
  outbound_events: string[]
}

export interface WebhookTestResponse {
  delivered: boolean
  status_code: number | null
  outbound_url: string | null
  detail: string
}

export interface Project {
  id: string
  name: string
  slug: string
  description: string
  tags: string[]
  visibility: ProjectVisibility
  seed_counter: number
  monthly_budget: string | null
  seed_budget_cap: string | null
  spend_checkpoint: string | null
  enforcement?: BudgetEnforcement
  enforcement_detail?: string | null
  max_concurrent_runs: number | null
  sandbox_config: ProjectSandboxConfig
  backends_restricted: boolean
  allowed_backend_ids: string[]
  my_role: ProjectRole | null
  archived_at: string | null
  archived_by: string | null
  created_at: string | null
  created_by: string | null
  updated_at: string | null
}

export interface ProjectMember {
  id: number | null
  project_id: string
  user_id: string
  email: string
  name: string
  preferred_name: string | null
  role: ProjectRole
  created_at: string | null
  created_by: string | null
  updated_at: string | null
}

export interface ProjectRepository {
  id: number | null
  project_id: string
  backend_id: string
  full_name: string
  default_branch: string
  is_primary: boolean
  created_at: string | null
  created_by: string | null
  updated_at: string | null
}

export interface InitLocalProjectRepositoryRequest {
  workspace_path?: string | null
  default_branch?: string
}

export interface InitLocalProjectRepositoryResponse {
  repository: ProjectRepository
  backend_id: string
  bare_repo_path: string
  workspace_path: string | null
  http_clone_url: string
  initialized_git: boolean
}

export interface RepoBranchItem {
  name: string
  commit_sha: string | null
  is_default: boolean
  protected: boolean
  web_url: string
}

export interface RepoBranchesResponse {
  repository_id: number | null
  full_name: string
  provider: string
  default_branch: string
  web_url: string
  branches: RepoBranchItem[]
}

export interface RepoTreeEntry {
  name: string
  path: string
  type: 'dir' | 'file'
  size: number | null
  sha: string | null
  web_url: string
}

export interface RepoReadmePreview {
  name: string
  path: string
  content: string
}

export interface RepoTreeResponse {
  repository_id: number | null
  full_name: string
  provider: string
  ref: string
  path: string
  web_url: string
  entries: RepoTreeEntry[]
  readme: RepoReadmePreview | null
}

export interface RepoBlobResponse {
  repository_id: number | null
  full_name: string
  provider: string
  ref: string
  path: string
  name: string
  size: number
  sha: string | null
  is_binary: boolean
  truncated: boolean
  content: string | null
  web_url: string
}

export interface Seed {
  id: string
  project_id: string
  number: number
  title: string
  description: string
  specification: string
  acceptance_criteria: string[]
  plan: string | null
  tags: string[]
  stage: SeedStage
  status: SeedStatus
  autonomy: SeedAutonomy
  stage_entered_at: string | null
  blocked_reason: string | null
  budget_cap: string | null
  spent: string
  checkpoint_approved_through: string
  enforcement?: BudgetEnforcement
  enforcement_detail?: string | null
  merged_at: string | null
  closed_at: string | null
  closed_by: string | null
  created_at: string | null
  created_by: string | null
  updated_at: string | null
}

export interface Approval {
  id: string
  seed_id: string
  stage: SeedStage
  snapshot: Record<string, unknown>
  decision: ApprovalDecision
  comment: string | null
  automatic: boolean
  decided_by: string | null
  approver_role: ProjectRole | null
  created_at: string | null
}

export interface SeedTask {
  id: string
  seed_id: string
  position: number
  title: string
  description: string
  status: TaskStatus
  started_at: string | null
  completed_at: string | null
  created_at: string | null
  updated_at: string | null
}

/** A file attached to a seed; agents get it at `/workspace/.gitseed/attachments/<filename>` */
export interface SeedAttachment {
  id: string
  seed_id: string
  filename: string
  content_type: string
  size: number
  sha256: string
  created_by: string | null
  created_at: string | null
}

/** A seed linked to this seed; agents get its details at `/workspace/.gitseed/related/seed-<number>.md` */
export interface SeedLinkItem {
  id: string
  seed_id: string
  number: number
  title: string
  status: SeedStatus
  stage: SeedStage
  relation: 'related' | 'follow_up_of' | 'followed_up_by'
  created_by: string | null
  created_at: string | null
}

export interface SeedComment {
  id: string
  seed_id: string
  author_type: ActorType
  author_id: string
  stage: SeedStage
  body: string
  edited_at: string | null
  deleted_at: string | null
  deleted_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ContextWindowInfo {
  used_tokens: number
  peak_tokens: number
  max_tokens: number
  utilization_pct: number
  turns: number
}

export interface Run {
  id: string
  seed_id: string
  task_id: string | null
  stage: SeedStage
  attempt: number
  agent_id: string
  runtime: AgentRuntime
  model: string
  compute_backend_id: string | null
  status: RunStatus
  container_id: string | null
  input_tokens: number
  output_tokens: number
  cost: string
  cost_enforcement?: BudgetEnforcement
  enforcement?: BudgetEnforcement
  enforcement_detail?: string | null
  output: Record<string, unknown>
  exit_code: number | null
  error: string | null
  log_path: string | null
  started_at: string | null
  finished_at: string | null
  cancelled_by: string | null
  created_at: string | null
  updated_at: string | null
}

export interface ActivityEvent {
  id: number
  occurred_at: string
  project_id: string | null
  seed_id: string | null
  actor_type: ActorType
  actor_id: string | null
  type: ActivityType
  data: Record<string, unknown>
}

export interface SeedChangeCommit {
  sha: string
  short_sha: string
  message: string
  author_name: string | null
  committed_at: string | null
  web_url: string | null
}

export interface SeedChangeFile {
  path: string
  previous_path: string | null
  status: 'added' | 'modified' | 'removed' | 'renamed' | string
  additions: number
  deletions: number
  changes: number
  patch: string | null
  truncated: boolean
  web_url: string | null
}

export interface PullRequestCheckItem {
  id: string
  name: string
  status: 'queued' | 'running' | 'passed' | 'failed' | 'skipped' | 'cancelled' | string
  conclusion: string | null
  workflow_name?: string | null
  details_url?: string | null
  started_at?: string | null
  completed_at?: string | null
  duration_seconds?: number | null
  summary?: string | null
}

export interface PullRequestCommentItem {
  id: string
  author: string
  body: string
  kind: 'comment' | 'review' | 'inline_comment' | string
  review_state?: string | null
  path?: string | null
  line?: number | null
  created_at?: string | null
  html_url?: string | null
}

export interface PullRequestStatus {
  pull_request_url: string | null
  number: number | null
  repository_full_name: string | null
  provider: string | null
  state: 'open' | 'merged' | 'closed' | string | null
  merged: boolean
  title?: string | null
  body?: string | null
  draft?: boolean
  author?: string | null
  base_ref?: string | null
  head_ref?: string | null
  head_sha?: string | null
  created_at?: string | null
  updated_at?: string | null
  merged_at?: string | null
  merge_commit_sha: string | null
  merged_by: string | null
  has_conflicts?: boolean
  approved_by: string[]
  changes_requested_by?: string[]
  requested_reviewers?: string[]
  ci_status: 'none' | 'pending' | 'passing' | 'failing' | string
  ci_summary: string | null
  failing_checks?: string[]
  checks?: PullRequestCheckItem[]
  comments?: PullRequestCommentItem[]
  review_status: 'none' | 'approved' | 'review_required' | 'changes_requested' | string
  mergeable_state: string | null
  ready_to_merge: boolean
  block_reason: string | null
}

export interface SeedChangesResponse {
  seed_id: string
  seed_number: number
  repository_id: number | null
  repository_full_name: string | null
  provider: string | null
  base_ref: string
  head_ref: string
  comparison_mode: 'branch' | 'merged_commit' | 'execute_commit' | 'none' | string
  branch_exists: boolean
  merge_commit_sha: string | null
  head_commit_sha: string | null
  web_diff_url: string | null
  pull_request_url?: string | null
  pull_request_status?: PullRequestStatus | null
  blocked_paths_reverted?: string[]
  total_commits: number
  total_files: number
  total_additions: number
  total_deletions: number
  commits: SeedChangeCommit[]
  files: SeedChangeFile[]
}

export interface SandboxArtifact {
  name: string
  rel_path: string
  size_bytes: number
  sha256: string
  rule?: string | null
}

export interface SandboxRunConfig {
  source: 'project_settings' | 'run_json' | 'auto_detected' | 'default'
  ref: string
  image: string
  setup_command: string | null
  run_command: string
  test_command: string | null
  artifact_globs: string[]
}

export interface SandboxSession {
  id: string
  project_id: string
  seed_id: string | null
  repository_id: number | null
  ref: string
  head_sha: string
  image: string
  setup_command: string | null
  command: string
  artifact_globs: string[]
  status: SandboxStatus
  exit_code: number | null
  container_id: string | null
  compute_backend_id: string | null
  as_root?: boolean
  artifacts: SandboxArtifact[]
  log_tail: string | null
  error: string | null
  started_by: string | null
  started_at: string | null
  expires_at: string | null
  stopped_at: string | null
  kind?: 'job' | 'terminal'
  title?: string | null
  last_activity_at?: string | null
  pushed_seed_id?: string | null
  artifacts_expire_at?: string | null
}

export interface ProposedSeedDraft {
  title: string
  description: string
  specification: string
  acceptance_criteria: string[]
  tags: string[]
  autonomy: SeedAutonomy
}

export type AgentSessionStatus = 'starting' | 'idle' | 'running_turn' | 'stopped' | 'failed'

export interface AgentSessionMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  tool_log: string | null
  proposed_seeds: ProposedSeedDraft[]
  referenced_files?: string[]
  created_at: string | null
}

export interface AgentSession {
  id: string
  project_id: string
  repository_id: number | null
  ref: string
  head_sha: string
  agent_id: string | null
  agent_handle: string
  agent_name: string
  runtime: AgentRuntime
  model: string | null
  provider_backend_id: string | null
  compute_backend_id: string | null
  title: string
  is_saved: boolean
  status: AgentSessionStatus
  container_id: string | null
  live_turn_log: string | null
  error: string | null
  started_by: string | null
  created_at: string | null
  updated_at: string | null
  stopped_at: string | null
  pushed_seed_id?: string | null
  context_window?: ContextWindowInfo | null
  messages: AgentSessionMessage[]
}

export interface SessionChangedFile {
  path: string
  status: 'added' | 'modified' | 'deleted'
}

export interface SessionPushDraft {
  title: string
  summary: string
  acceptance_criteria: string[]
  changed_files: SessionChangedFile[]
  branch: string
  drafted_by: string
  linked_seed_id?: string | null
  linked_seed_number?: number | null
  linked_seed_title?: string | null
  linked_seed_branch?: string | null
}

export interface SessionPushResult {
  seed_id: string
  seed_number: number
  branch: string
  commit_sha: string
  pull_request_url: string | null
  changed_files: number
}

export interface SessionWorkspaceFileEntry {
  path: string
  size: number
  status: 'added' | 'modified' | 'deleted' | null
}

export interface SessionWorkspaceFilesResponse {
  files: SessionWorkspaceFileEntry[]
  changed_files: SessionChangedFile[]
}

export interface SessionWorkspaceFileContent {
  path: string
  content: string
  original_content: string | null
  is_binary: boolean
  status: 'added' | 'modified' | 'deleted' | 'unchanged'
}

export interface SessionVsCodeInfo {
  url: string
  mode: 'in_container' | 'sidecar'
  arch: string
  memory_mb: number
  memory_raised: boolean
  notice: string | null
}

export type SessionKind = 'chat' | 'terminal'

export interface SessionListItem {
  kind: SessionKind
  id: string
  project_id: string
  project_slug: string
  project_name: string
  name: string
  ref: string
  head_sha: string
  status: string
  is_running: boolean
  started_by: string | null
  started_at: string | null
  last_activity_at: string | null
  expires_at: string | null
  agent_id?: string | null
  agent_handle: string | null
  image: string | null
  pushed_seed_id: string | null
  context_window?: ContextWindowInfo | null
}

export interface ReceiptPublicKey {
  key_id: string
  algorithm: string
  public_key_pem: string
  previous_public_keys?: Array<{ key_id: string; public_key_pem: string }>
}

export interface GovernanceReceiptRunItem {
  id: string
  stage: string
  runtime: string
  model: string
  status: string
  cost_usd: number | string
  cost_enforcement: BudgetEnforcement
  input_tokens: number
  output_tokens: number
  started_at: string | null
  completed_at: string | null
  trusted: Record<string, unknown> | null
}

export interface GovernanceReceiptApprovalItem {
  id: string
  stage: string
  status: string
  approver_user_id: string | null
  approver_name: string | null
  comment: string | null
  decided_at: string | null
}

export interface GovernanceReceiptTaskItem {
  id: string
  title: string
  status: string
  order_index: number
}

export interface GovernanceReceiptArtifactsInfo {
  specification_sha256?: string | null
  acceptance_criteria_sha256?: string | null
  plan_sha256?: string | null
  task_count: number
  tasks: GovernanceReceiptTaskItem[]
}

export interface GovernanceReceiptPayload {
  schema_version: string
  seed: {
    id: string
    number: number
    title: string
    project_id: string
    project_slug: string
    status: string
    stage: string
    branch_name: string | null
    pr_url: string | null
  }
  policy: {
    autonomy: string
    min_approver_role: string
    prevent_self_approval: boolean
    blocked_paths: string[]
  }
  artifacts?: GovernanceReceiptArtifactsInfo | null
  runs: GovernanceReceiptRunItem[]
  approvals: GovernanceReceiptApprovalItem[]
  generated_at: string
}

export interface GovernanceReceiptEnvelope {
  receipt: GovernanceReceiptPayload
  canonical_json: string
  signature: string
  algorithm: string
  key_id: string
  public_key_pem: string
  markdown?: string | null
}

