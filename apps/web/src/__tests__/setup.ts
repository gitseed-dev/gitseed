import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { queryKeys } from '../api/queryClient'
import { I18nProvider } from '../context/I18nContext'
import { ThemeProvider } from '../context/ThemeContext'
import { StatusProvider } from '../context/StatusContext'
import { AuthProvider } from '../context/AuthContext'
import { DataProvider } from '../context/DataContext'
import { SessionDockProvider } from '../context/SessionDockContext'
import type {
  ActivityEvent,
  Agent,
  Approval,
  Backend,
  OrgSettings,
  PersonalAccessToken,
  Project,
  ProjectMember,
  ProjectRepository,
  Run,
  SandboxSession,
  Seed,
  SeedChangesResponse,
  SeedComment,
  SeedTask,
  Skill,
  StagePolicy,
  User,
} from '../types'

// Setup minimal browser globals for SSR / static rendering
const storageMap = new Map<string, string>()
export const mockStorage: Storage = {
  get length() {
    return storageMap.size
  },
  clear() {
    storageMap.clear()
  },
  getItem(key: string) {
    return storageMap.has(key) ? storageMap.get(key)! : null
  },
  key(index: number) {
    return Array.from(storageMap.keys())[index] ?? null
  },
  removeItem(key: string) {
    storageMap.delete(key)
  },
  setItem(key: string, value: string) {
    storageMap.set(key, String(value))
  },
}

const classSet = new Set<string>()
export const mockDocElement = {
  classList: {
    add: (...tokens: string[]) => tokens.forEach((t) => classSet.add(t)),
    remove: (...tokens: string[]) => tokens.forEach((t) => classSet.delete(t)),
    contains: (token: string) => classSet.has(token),
    toggle: (token: string) => {
      if (classSet.has(token)) {
        classSet.delete(token)
        return false
      }
      classSet.add(token)
      return true
    },
  },
  setAttribute: () => { },
  getAttribute: () => null,
  style: {},
}

export const mockDocument = {
  title: 'GitSeed',
  documentElement: mockDocElement,
  body: {
    appendChild: () => { },
    removeChild: () => { },
    style: {},
  },
  addEventListener: () => { },
  removeEventListener: () => { },
  getElementById: () => null,
  createElement: () => ({
    style: {},
    setAttribute: () => { },
    appendChild: () => { },
  }),
}

export const mockWindow = {
  location: {
    pathname: '/seeds',
    search: '?project=core-platform',
    hash: '',
    origin: 'http://localhost:5173',
    href: 'http://localhost:5173/seeds?project=core-platform',
    assign: () => { },
    replace: () => { },
    reload: () => { },
  },
  history: {
    state: null,
    pushState: () => { },
    replaceState: () => { },
    back: () => { },
  },
  localStorage: mockStorage,
  sessionStorage: mockStorage,
  document: mockDocument,
  navigator: {
    language: 'en-US',
    platform: 'Win32',
    clipboard: {
      writeText: async () => { },
    },
  },
  matchMedia: (query: string) => ({
    matches: query.includes('dark'),
    media: query,
    onchange: null,
    addListener: () => { },
    removeListener: () => { },
    addEventListener: () => { },
    removeEventListener: () => { },
    dispatchEvent: () => false,
  }),
  addEventListener: () => { },
  removeEventListener: () => { },
  dispatchEvent: () => true,
  getComputedStyle: () => ({
    getPropertyValue: () => '',
  }),
  requestAnimationFrame: (cb: FrameRequestCallback) =>
    setTimeout(() => cb(0), 0) as unknown as number,
  cancelAnimationFrame: (id: number) => clearTimeout(id),
  setTimeout: globalThis.setTimeout.bind(globalThis),
  clearTimeout: globalThis.clearTimeout.bind(globalThis),
  setInterval: globalThis.setInterval.bind(globalThis),
  clearInterval: globalThis.clearInterval.bind(globalThis),
  innerWidth: 1280,
  innerHeight: 800,
  screen: {
    width: 1280,
    height: 800,
    orientation: {
      angle: 0,
      type: 'landscape-primary',
      addEventListener: () => { },
      removeEventListener: () => { },
    },
  },
  scrollTo: () => { },
  HTMLElement: class HTMLElement {
    focus() { }
    blur() { }
  },
  Element: class Element { },
  Node: class Node { },
  SVGElement: class SVGElement { },
}

export function setupTestDom(): void {
  const g = globalThis as Record<string, unknown>
  g.localStorage = mockStorage
  g.sessionStorage = mockStorage
  g.window = mockWindow
  g.document = mockDocument
  g.HTMLElement = mockWindow.HTMLElement
  g.Element = mockWindow.Element
  g.Node = mockWindow.Node
  g.SVGElement = mockWindow.SVGElement
  if (!g.navigator) {
    g.navigator = mockWindow.navigator
  }
  g.ResizeObserver = class ResizeObserver {
    observe() { }
    unobserve() { }
    disconnect() { }
  }
}

// Sample Fixtures
export const mockUser: User = {
  id: 'u1',
  name: 'Ada Lovelace',
  preferred_name: 'Ada',
  email: 'ada@gitseed.dev',
  org_role: 'admin',
  status: 'active',
  preferences: { theme: 'dark', ambient_bg: true },
  last_active_at: new Date().toISOString(),
  created_at: new Date().toISOString(),
}

export const mockUserInvited: User = {
  id: 'u2',
  name: 'Grace Hopper',
  preferred_name: null,
  email: 'grace@gitseed.dev',
  org_role: 'member',
  status: 'invited',
  preferences: {},
  invite_expires_at: new Date(Date.now() + 86400000).toISOString(),
  last_active_at: null,
  created_at: new Date().toISOString(),
}

export const mockProjects: Project[] = [
  {
    id: 'p1',
    name: 'Core Platform',
    slug: 'core-platform',
    description: 'Primary autonomous platform service',
    visibility: 'private',
    tags: ['platform', 'typescript'],
    seed_counter: 102,
    monthly_budget: '500.00',
    seed_budget_cap: '25.00',
    spend_checkpoint: '10.00',
    max_concurrent_runs: 3,
    backends_restricted: true,
    allowed_backend_ids: ['b-git', 'b-model'],
    my_role: 'maintainer',
    sandbox_config: {
      image: 'python:3.12-slim',
      setup_command: 'pip install -r requirements.txt',
      run_command: 'python main.py',
      test_command: 'pytest',
      artifact_globs: ['dist/*'],
    },
    archived_at: null,
    archived_by: null,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
  {
    id: 'p2',
    name: 'Mobile Client',
    slug: 'mobile-client',
    description: 'Android & iOS app',
    visibility: 'public',
    tags: ['mobile'],
    seed_counter: 12,
    monthly_budget: null,
    seed_budget_cap: null,
    spend_checkpoint: null,
    max_concurrent_runs: null,
    backends_restricted: false,
    allowed_backend_ids: [],
    my_role: 'developer',
    sandbox_config: {},
    archived_at: '2026-11-11T00:00:00Z',
    archived_by: 'u1',
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
]

export const mockRepos: ProjectRepository[] = [
  {
    id: 1,
    project_id: 'p1',
    backend_id: 'b-git',
    full_name: 'acme/core-platform',
    default_branch: 'main',
    is_primary: true,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
  {
    id: 2,
    project_id: 'p1',
    backend_id: 'b-git',
    full_name: 'acme/core-docs',
    default_branch: 'develop',
    is_primary: false,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
]

export const mockBackends: Backend[] = [
  {
    id: 'b-git',
    name: 'GitHub Enterprise',
    type: 'git',
    provider: 'github',
    endpoint: 'https://api.github.com',
    secret_keys: ['token'],
    capacity: null,
    access_restricted: false,
    allowed_agent_ids: [],
    enabled: true,
    health: 'healthy',
    health_latency_ms: 42,
    health_checked_at: new Date().toISOString(),
    health_error: null,
    config: {
      org: 'acme',
      repositories: [
        {
          full_name: 'acme/core-platform',
          default_branch: 'main',
          private: true,
          enabled: true,
        },
        {
          full_name: 'acme/new-service',
          default_branch: 'main',
          private: false,
          enabled: true,
        },
      ],
    },
    created_by: 'u1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
  {
    id: 'b-model',
    name: 'Anthropic Claude',
    type: 'inference',
    provider: 'anthropic',
    endpoint: 'https://api.anthropic.com',
    secret_keys: ['api_key'],
    capacity: 4,
    access_restricted: false,
    allowed_agent_ids: [],
    enabled: true,
    health: 'healthy',
    health_latency_ms: 120,
    health_checked_at: new Date().toISOString(),
    health_error: null,
    config: {
      default_model: 'claude-sonnet-4-20250514',
      models: [
        { id: 'claude-sonnet-4-20250514', name: 'Claude Sonnet 4', enabled: true },
        { id: 'claude-opus-4-20250514', name: 'Claude Opus 4', enabled: false },
      ],
    },
    created_by: 'u1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
  {
    id: 'b-compute',
    name: 'Local Docker',
    type: 'compute',
    provider: 'docker',
    endpoint: 'unix:///var/run/docker.sock',
    secret_keys: [],
    capacity: 4,
    access_restricted: false,
    allowed_agent_ids: [],
    enabled: true,
    health: 'degraded',
    health_latency_ms: 15,
    health_checked_at: new Date().toISOString(),
    health_error: 'High load',
    config: {},
    created_by: 'u1',
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
]

export const mockSkills: Skill[] = [
  {
    id: 'sk1',
    slug: 'git-ops',
    name: 'Git Operations',
    description: 'Branch, commit, and open pull requests',
    content: '# Git Operations\nUse git safely.',
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
  {
    id: 'sk2',
    slug: 'custom-linter',
    name: 'Custom Linter',
    description: 'Run custom lint rules',
    content: '# Custom Linter',
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
]

export const mockAgents: Agent[] = [
  {
    id: 'a1',
    name: 'Architect',
    handle: 'architect',
    kind: 'stage',
    template: 'architect',
    instructions: 'You are the Architect.',
    runtime: 'claude-code',
    model: 'claude-sonnet-4-20250514',
    skills: ['git-ops', 'custom-linter'],
    tools: [],
    enabled: true,
    allowed_backend_ids: ['b-model'],
    run_count: 18,
    succeeded_runs: 17,
    total_cost: '14.20',
    archived_at: null,
    archived_by: null,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
  {
    id: 'a2',
    name: 'Builder',
    handle: 'builder',
    kind: 'stage',
    template: 'builder',
    instructions: 'You are the Builder.',
    runtime: 'opencode',
    model: 'claude-sonnet-4-20250514',
    skills: ['git-ops'],
    tools: [],
    enabled: true,
    allowed_backend_ids: ['b-model'],
    run_count: 30,
    succeeded_runs: 27,
    total_cost: '28.30',
    archived_at: null,
    archived_by: null,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
]

export const mockPolicies: StagePolicy[] = [
  {
    id: 'pol-ideate',
    project_id: null,
    stage: 'ideate',
    enabled: true,
    agent_id: 'a1',
    requires_approval: false,
    min_approvers: 1,
    min_approver_role: 'developer',
    allow_auto_approve: true,
    allow_self_approval: true,
    max_attempts: 2,
    timeout_minutes: 30,
    overridden_fields: [],
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
  {
    id: 'pol-plan',
    project_id: null,
    stage: 'plan',
    enabled: true,
    agent_id: 'a1',
    requires_approval: true,
    min_approvers: 1,
    min_approver_role: 'maintainer',
    allow_auto_approve: false,
    allow_self_approval: true,
    max_attempts: 2,
    timeout_minutes: 30,
    overridden_fields: [],
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
  {
    id: 'pol-execute',
    project_id: null,
    stage: 'execute',
    enabled: true,
    agent_id: 'a2',
    requires_approval: false,
    min_approvers: 1,
    min_approver_role: 'developer',
    allow_auto_approve: true,
    allow_self_approval: true,
    max_attempts: 2,
    timeout_minutes: 45,
    overridden_fields: [],
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
  {
    id: 'pol-review',
    project_id: null,
    stage: 'review',
    enabled: true,
    agent_id: 'a2',
    requires_approval: true,
    min_approvers: 1,
    min_approver_role: 'maintainer',
    allow_auto_approve: false,
    allow_self_approval: false,
    max_attempts: 1,
    timeout_minutes: 30,
    overridden_fields: [],
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
  {
    id: 'pol-merge',
    project_id: null,
    stage: 'merge',
    enabled: true,
    agent_id: 'a2',
    requires_approval: true,
    min_approvers: 1,
    min_approver_role: 'maintainer',
    allow_auto_approve: false,
    allow_self_approval: false,
    max_attempts: 1,
    timeout_minutes: 20,
    overridden_fields: [],
    updated_at: new Date().toISOString(),
    updated_by: 'u1',
  },
]

export const mockSeeds: Seed[] = [
  {
    id: 's1',
    number: 101,
    project_id: 'p1',
    title: 'Implement OAuth2 PKCE flow',
    description: 'Add PKCE verification to OAuth login callback and refresh endpoints.',
    specification: 'Detailed PKCE specification',
    acceptance_criteria: ['PKCE challenge validated', 'Unit tests added'],
    plan: 'Update auth router and add token verifier.',
    tags: ['auth', 'security'],
    stage: 'review',
    status: 'awaiting_approval',
    autonomy: 'supervised',
    stage_entered_at: new Date().toISOString(),
    blocked_reason: null,
    budget_cap: '25.00',
    spent: '8.45',
    checkpoint_approved_through: '0.00',
    merged_at: null,
    closed_at: null,
    closed_by: null,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
  {
    id: 's2',
    number: 102,
    project_id: 'p1',
    title: 'Fix rate limiter memory leak',
    description: 'Evict expired token buckets periodically.',
    specification: 'Detailed rate limiter spec',
    acceptance_criteria: ['Memory stays bounded'],
    plan: null,
    tags: ['performance'],
    stage: 'execute',
    status: 'running',
    autonomy: 'autonomous',
    stage_entered_at: new Date().toISOString(),
    blocked_reason: null,
    budget_cap: '20.00',
    spent: '3.10',
    checkpoint_approved_through: '0.00',
    merged_at: null,
    closed_at: null,
    closed_by: null,
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
]

export const mockRuns: Run[] = [
  {
    id: 'r1',
    seed_id: 's2',
    task_id: 't1',
    stage: 'execute',
    attempt: 1,
    agent_id: 'a2',
    runtime: 'opencode',
    model: 'claude-sonnet-4-20250514',
    compute_backend_id: 'b-compute',
    status: 'running',
    container_id: 'cnt-123',
    input_tokens: 14500,
    output_tokens: 3200,
    cost: '0.42',
    output: { summary: 'Running unit test suite' },
    exit_code: null,
    error: null,
    log_path: null,
    started_at: new Date().toISOString(),
    finished_at: null,
    cancelled_by: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: 'r2',
    seed_id: 's1',
    task_id: null,
    stage: 'plan',
    attempt: 1,
    agent_id: 'a1',
    runtime: 'claude-code',
    model: 'claude-sonnet-4-20250514',
    compute_backend_id: 'b-compute',
    status: 'succeeded',
    container_id: 'cnt-122',
    input_tokens: 24000,
    output_tokens: 5100,
    cost: '0.78',
    output: { summary: 'Generated implementation plan' },
    exit_code: 0,
    error: null,
    log_path: null,
    started_at: new Date(Date.now() - 86400000).toISOString(),
    finished_at: new Date(Date.now() - 86300000).toISOString(),
    cancelled_by: null,
    created_at: new Date(Date.now() - 86400000).toISOString(),
    updated_at: new Date(Date.now() - 86300000).toISOString(),
  },
]

export const mockActivity: ActivityEvent[] = [
  {
    id: 1,
    occurred_at: new Date().toISOString(),
    project_id: 'p1',
    seed_id: 's1',
    actor_type: 'agent',
    actor_id: 'a1',
    type: 'stage.changed',
    data: { from_stage: 'execute', to_stage: 'review' },
  },
  {
    id: 2,
    occurred_at: new Date().toISOString(),
    project_id: 'p1',
    seed_id: 's2',
    actor_type: 'user',
    actor_id: 'u1',
    type: 'approval.granted',
    data: { stage: 'plan', comment: 'LGTM' },
  },
]

export const mockOrgSettings: OrgSettings = {
  default_autonomy: 'supervised',
  default_monthly_budget: '500.00',
  default_seed_budget_cap: '25.00',
  default_spend_checkpoint: '10.00',
  max_concurrent_runners: 8,
  max_sandbox_sessions: 4,
  default_project_concurrency: 2,
  queue_strategy: 'fair_share',
  pat_lifetime_days: 90,
  default_theme: 'dark',
  default_ambient_bg: true,
  default_show_guide: true,
  updated_at: new Date().toISOString(),
  updated_by: 'u1',
}

export const mockSeedTasks: SeedTask[] = [
  {
    id: 't1',
    seed_id: 's1',
    position: 1,
    title: 'Add PKCEverifier helper',
    description: 'Create SHA-256 code verifier utility',
    status: 'done',
    started_at: new Date().toISOString(),
    completed_at: new Date().toISOString(),
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: 't2',
    seed_id: 's1',
    position: 2,
    title: 'Wire PKCE into OAuth callback',
    description: 'Validate code_verifier on token exchange',
    status: 'running',
    started_at: new Date().toISOString(),
    completed_at: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
]

export const mockApprovals: Approval[] = [
  {
    id: 'ap1',
    seed_id: 's1',
    stage: 'review',
    snapshot: {},
    decision: 'approved',
    comment: 'Looks good to me',
    automatic: false,
    decided_by: 'u1',
    approver_role: 'maintainer',
    created_at: new Date().toISOString(),
  },
]

export const mockComments: SeedComment[] = [
  {
    id: 'c1',
    seed_id: 's1',
    author_type: 'user',
    author_id: 'u1',
    stage: 'review',
    body: '@architect please verify RFC 7636 compliance.',
    edited_at: null,
    deleted_at: null,
    deleted_by: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
  {
    id: 'c2',
    seed_id: 's1',
    author_type: 'agent',
    author_id: 'a1',
    stage: 'review',
    body: 'Confirmed: S256 challenge method is enforced.',
    edited_at: null,
    deleted_at: null,
    deleted_by: null,
    created_at: new Date().toISOString(),
    updated_at: new Date().toISOString(),
  },
]

export const mockChanges: SeedChangesResponse = {
  seed_id: 's1',
  seed_number: 101,
  repository_id: 1,
  repository_full_name: 'acme/core-platform',
  provider: 'github',
  base_ref: 'main',
  head_ref: 'gitseed/seed-101',
  comparison_mode: 'branch',
  branch_exists: true,
  merge_commit_sha: null,
  head_commit_sha: 'abcdef1234567890',
  web_diff_url: 'https://github.com/acme/core-platform/compare/main...gitseed/seed-101',
  total_commits: 1,
  total_files: 2,
  total_additions: 48,
  total_deletions: 6,
  commits: [
    {
      sha: 'abcdef1234567890',
      short_sha: 'abcdef1',
      message: 'feat(auth): add PKCE verifier',
      author_name: 'Builder',
      committed_at: new Date().toISOString(),
      web_url: 'https://github.com/acme/core-platform/commit/abcdef1234567890',
    },
  ],
  files: [
    {
      path: 'src/auth/pkce.ts',
      previous_path: null,
      status: 'added',
      additions: 40,
      deletions: 0,
      changes: 40,
      patch: '@@ -0,0 +1,5 @@\n+export function verifyPkce() {\n+  return true\n+}',
      truncated: false,
      web_url: 'https://github.com/acme/core-platform/blob/main/src/auth/pkce.ts',
    },
    {
      path: 'src/auth/callback.ts',
      previous_path: null,
      status: 'modified',
      additions: 8,
      deletions: 6,
      changes: 14,
      patch: '@@ -10,3 +10,5 @@\n-const ok = true\n+const ok = verifyPkce()',
      truncated: false,
      web_url: 'https://github.com/acme/core-platform/blob/main/src/auth/callback.ts',
    },
  ],
}

export const mockMembers: ProjectMember[] = [
  {
    id: 1,
    project_id: 'p1',
    user_id: 'u1',
    name: 'Ada Lovelace',
    preferred_name: 'Ada',
    email: 'ada@gitseed.dev',
    role: 'maintainer',
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
  {
    id: 2,
    project_id: 'p1',
    user_id: 'u2',
    name: 'Grace Hopper',
    preferred_name: null,
    email: 'grace@gitseed.dev',
    role: 'developer',
    created_at: new Date().toISOString(),
    created_by: 'u1',
    updated_at: new Date().toISOString(),
  },
]

export const mockSandboxSession: SandboxSession = {
  id: 'sbx-1',
  project_id: 'p1',
  repository_id: 1,
  seed_id: 's1',
  ref: 'gitseed/seed-101',
  head_sha: 'abcdef1234567890',
  image: 'python:3.12-slim',
  setup_command: 'pip install -r requirements.txt',
  command: 'pytest -v',
  artifact_globs: ['reports/*'],
  status: 'succeeded',
  exit_code: 0,
  container_id: 'cnt-sbx-1',
  compute_backend_id: 'b-compute',
  log_tail: '=== Setup ===\nCollecting pytest\n=== Command ===\n2 passed in 0.42s\n',
  error: null,
  artifacts: [
    {
      name: 'coverage.xml',
      rel_path: 'reports/coverage.xml',
      size_bytes: 4096,
      sha256: '1234567890abcdef',
    },
  ],
  started_by: 'u1',
  started_at: new Date(Date.now() - 15000).toISOString(),
  expires_at: new Date(Date.now() + 600000).toISOString(),
  stopped_at: new Date().toISOString(),
}

export const mockTokens: PersonalAccessToken[] = [
  {
    id: 'tok1',
    name: 'CI Runner Token',
    token_prefix: 'gsp_1234',
    last_used_at: new Date().toISOString(),
    expires_at: new Date(Date.now() + 86400000 * 30).toISOString(),
    revoked_at: null,
    created_at: new Date().toISOString(),
  },
]

export function createPopulatedQueryClient(): QueryClient {
  const qc = new QueryClient({
    defaultOptions: {
      queries: {
        retry: false,
        staleTime: Infinity,
      },
    },
  })
  qc.setQueryData(queryKeys.projects, mockProjects)
  qc.setQueryData(queryKeys.projectDetail('p1'), mockProjects[0])
  qc.setQueryData(queryKeys.projectRepos('p1'), mockRepos)
  qc.setQueryData(queryKeys.projectMembers('p1'), mockMembers)
  qc.setQueryData(queryKeys.projectPolicies('p1'), mockPolicies)
  qc.setQueryData(queryKeys.projectSandboxes('p1'), [mockSandboxSession])
  qc.setQueryData(queryKeys.projectSandboxConfig('p1', 1, 'main'), {
    source: 'project_settings',
    ref: 'main',
    image: 'python:3.12-slim',
    setup_command: 'pip install -r requirements.txt',
    run_command: 'python main.py',
    test_command: 'pytest',
    artifact_globs: ['dist/*'],
  })
  qc.setQueryData(queryKeys.seeds, mockSeeds)
  qc.setQueryData(queryKeys.seedDetail('s1'), mockSeeds[0])
  qc.setQueryData(queryKeys.seedTasks('s1'), mockSeedTasks)
  qc.setQueryData(queryKeys.seedApprovals('s1'), mockApprovals)
  qc.setQueryData(queryKeys.seedComments('s1'), mockComments)
  qc.setQueryData(queryKeys.seedChanges('s1'), mockChanges)
  qc.setQueryData(queryKeys.runs, mockRuns)
  qc.setQueryData(queryKeys.activity, mockActivity)
  qc.setQueryData(queryKeys.backends, mockBackends)
  qc.setQueryData(queryKeys.agents, mockAgents)
  qc.setQueryData(queryKeys.skills, mockSkills)
  qc.setQueryData(queryKeys.policies, mockPolicies)
  qc.setQueryData(queryKeys.users, [mockUser, mockUserInvited])
  qc.setQueryData(queryKeys.settings, mockOrgSettings)
  qc.setQueryData(queryKeys.tokens, mockTokens)
  qc.setQueryData(['repo-branches', 'p1', 1], {
    repository_id: 1,
    full_name: 'acme/core-platform',
    provider: 'github',
    default_branch: 'main',
    web_url: 'https://github.com/acme/core-platform',
    branches: [
      {
        name: 'main',
        commit_sha: 'abcdef123456',
        is_default: true,
        protected: true,
        web_url: 'https://github.com/acme/core-platform/tree/main',
      },
      {
        name: 'gitseed/seed-101',
        commit_sha: '9876543210ab',
        is_default: false,
        protected: false,
        web_url: 'https://github.com/acme/core-platform/tree/gitseed/seed-101',
      },
    ],
  })
  qc.setQueryData(['repo-tree', 'p1', 1, 'main', ''], {
    repository_id: 1,
    full_name: 'acme/core-platform',
    provider: 'github',
    ref: 'main',
    path: '',
    web_url: 'https://github.com/acme/core-platform/tree/main',
    entries: [
      {
        name: 'src',
        path: 'src',
        type: 'dir',
        size: null,
        sha: '1111111111',
        web_url: 'https://github.com/acme/core-platform/tree/main/src',
      },
      {
        name: 'README.md',
        path: 'README.md',
        type: 'file',
        size: 1024,
        sha: '2222222222',
        web_url: 'https://github.com/acme/core-platform/blob/main/README.md',
      },
    ],
    readme: {
      name: 'README.md',
      path: 'README.md',
      content: '# Core Platform\nAutonomous SDLC service.\n',
    },
  })
  qc.setQueryData(['repo-blob', 'p1', 1, 'main', 'README.md'], {
    repository_id: 1,
    full_name: 'acme/core-platform',
    provider: 'github',
    ref: 'main',
    path: 'README.md',
    name: 'README.md',
    size: 64,
    sha: '2222222222',
    is_binary: false,
    truncated: false,
    content: '# Core Platform\nAutonomous SDLC service.\n',
    web_url: 'https://github.com/acme/core-platform/blob/main/README.md',
  })
  return qc
}

export const sharedQueryClient = createPopulatedQueryClient()

export function renderWithProviders(element: React.ReactElement): string {
  mockStorage.setItem('gitseed_auth_user', JSON.stringify(mockUser))
  return renderToStaticMarkup(
    React.createElement(
      QueryClientProvider,
      { client: sharedQueryClient },
      React.createElement(
        I18nProvider,
        null,
        React.createElement(
          StatusProvider,
          null,
          React.createElement(
            AuthProvider,
            null,
            React.createElement(
              ThemeProvider,
              null,
              React.createElement(
                DataProvider,
                null,
                React.createElement(SessionDockProvider, null, element)
              )
            )
          )
        )
      )
    )
  )
}
