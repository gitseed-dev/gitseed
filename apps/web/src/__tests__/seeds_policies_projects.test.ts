import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type {
  Agent,
  Backend,
  Project,
  ProjectMember,
  Run,
  Seed,
  StagePolicy,
  User,
} from '../types'
import {
  getAgentActiveRuns,
  getAgentAssignedStages,
  getAgentFirstPassRate,
  getCompatibleModelBackends,
  getConfiguredModelOptions,
  getNativeProviderBackends,
  getRuntimeDefinition,
  slugifyAgentHandle,
} from '../utils/agents'
import {
  formatRoleLabel,
  getUncoveredStages,
  ORDERED_STAGES,
  STAGE_METADATA,
} from '../utils/policies'
import {
  canUserContributeToProject,
  canUserManageProject,
  computeProjectSummary,
  deriveProjectSlug,
  getProjectInitials,
} from '../utils/projects'
import {
  AUTONOMY_LABELS,
  canUserApproveSeedStage,
  getEffectiveSeedPolicies,
  getSeedStatusClasses,
  getTaskStatusClasses,
  isSeedOpen,
  SEED_STAGE_ORDER,
  STAGE_LABELS,
  STATUS_LABELS,
} from '../utils/seeds'
import {
  canRoleApprovePolicy,
  deriveProjectStageApprovals,
  deriveUserStageApprovals,
  getUserProjectAssignments,
  getUserRoleSummary,
} from '../utils/users'

function makePolicy(overrides: Partial<StagePolicy> = {}): StagePolicy {
  return {
    id: 'pol-1',
    project_id: null,
    stage: 'plan',
    enabled: true,
    agent_id: 'ag-1',
    requires_approval: true,
    min_approvers: 1,
    min_approver_role: 'developer',
    allow_auto_approve: true,
    allow_self_approval: true,
    max_attempts: 2,
    timeout_minutes: 30,
    overridden_fields: [],
    updated_at: null,
    updated_by: null,
    ...overrides,
  }
}

function makeUser(overrides: Partial<User> = {}): User {
  return {
    id: 'u-1',
    email: 'dev@example.com',
    name: 'Dev User',
    preferred_name: 'Dev',
    org_role: 'member',
    status: 'active',
    preferences: {},
    last_active_at: null,
    created_at: null,
    ...overrides,
  }
}

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'p-1',
    name: 'GitSeed Core',
    slug: 'gitseed-core',
    description: '',
    tags: [],
    visibility: 'private',
    seed_counter: 5,
    monthly_budget: '250.00',
    seed_budget_cap: null,
    spend_checkpoint: null,
    max_concurrent_runs: null,
    sandbox_config: {},
    backends_restricted: false,
    allowed_backend_ids: [],
    my_role: 'developer',
    archived_at: null,
    archived_by: null,
    created_at: '2026-11-11T00:00:00Z',
    created_by: 'u-1',
    updated_at: '2026-11-11T01:00:00Z',
    ...overrides,
  }
}

function makeSeed(overrides: Partial<Seed> = {}): Seed {
  return {
    id: 's-1',
    project_id: 'p-1',
    number: 1,
    title: 'Implement OAuth',
    description: '',
    specification: '',
    acceptance_criteria: [],
    plan: null,
    tags: [],
    stage: 'plan',
    status: 'awaiting_approval',
    autonomy: 'supervised',
    stage_entered_at: null,
    blocked_reason: null,
    budget_cap: null,
    spent: '12.50',
    checkpoint_approved_through: '0.00',
    merged_at: null,
    closed_at: null,
    closed_by: null,
    created_at: '2026-11-11T02:00:00Z',
    created_by: 'u-1',
    updated_at: '2026-11-11T03:00:00Z',
    ...overrides,
  }
}

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'ag-1',
    handle: 'architect',
    name: 'Architect',
    kind: 'stage',
    template: 'architect',
    instructions: '',
    runtime: 'opencode',
    model: 'default',
    skills: [],
    tools: [],
    enabled: true,
    allowed_backend_ids: [],
    run_count: 10,
    succeeded_runs: 8,
    total_cost: '4.20',
    archived_at: null,
    archived_by: null,
    created_at: null,
    created_by: null,
    updated_at: null,
    ...overrides,
  }
}

describe('utils/seeds', () => {
  it('defines canonical stage order, labels, and status helpers', () => {
    assert.deepEqual(SEED_STAGE_ORDER, [
      'ideate',
      'plan',
      'execute',
      'review',
      'merge',
    ])
    assert.equal(STAGE_LABELS.execute, 'Execute')
    assert.equal(STATUS_LABELS.awaiting_approval, 'Awaiting Approval')
    assert.equal(AUTONOMY_LABELS.autonomous, 'Autonomous')

    assert.ok(getSeedStatusClasses('running').includes('sky'))
    assert.ok(getSeedStatusClasses('merged').includes('emerald'))
    assert.ok(getTaskStatusClasses('done').includes('emerald'))
    assert.ok(getTaskStatusClasses('failed').includes('rose'))

    assert.equal(isSeedOpen(makeSeed({ status: 'running' })), true)
    assert.equal(isSeedOpen(makeSeed({ status: 'merged' })), false)
    assert.equal(isSeedOpen(makeSeed({ status: 'closed' })), false)
  })

  it('resolves effective project vs org policies and stage approval permissions', () => {
    const orgPol = [makePolicy({ id: 'org-plan', stage: 'plan' })]
    const projPol = [makePolicy({ id: 'proj-plan', project_id: 'p-1', stage: 'plan' })]

    assert.deepEqual(
      getEffectiveSeedPolicies('p-1', { 'p-1': projPol }, orgPol),
      projPol
    )
    assert.deepEqual(getEffectiveSeedPolicies('p-2', { 'p-1': projPol }, orgPol), orgPol)

    const devUser = makeUser({ id: 'u-1', org_role: 'member' })
    const project = makeProject({ my_role: 'developer' })
    const seedByDev = makeSeed({ created_by: 'u-1' })

    // Developer can approve developer-gated policy when self-approval is allowed
    assert.equal(
      canUserApproveSeedStage(
        devUser,
        project,
        seedByDev,
        makePolicy({ min_approver_role: 'developer', allow_self_approval: true })
      ),
      true
    )

    // Blocked when self-approval is disallowed and user created the seed
    assert.equal(
      canUserApproveSeedStage(
        devUser,
        project,
        seedByDev,
        makePolicy({ min_approver_role: 'developer', allow_self_approval: false })
      ),
      false
    )

    // Blocked when policy requires maintainer but user is developer
    assert.equal(
      canUserApproveSeedStage(
        devUser,
        project,
        seedByDev,
        makePolicy({ min_approver_role: 'maintainer', allow_self_approval: true })
      ),
      false
    )
  })
})

describe('utils/policies', () => {
  it('exposes stage metadata and detects uncovered enabled stages', () => {
    assert.equal(ORDERED_STAGES.length, 5)
    assert.equal(STAGE_METADATA.execute.isRequired, true)
    assert.equal(STAGE_METADATA.merge.isRequired, true)
    assert.equal(STAGE_METADATA.ideate.isRequired, false)
    assert.equal(formatRoleLabel('maintainer'), 'Maintainer')

    const activeAgent = makeAgent({ id: 'ag-active', enabled: true, kind: 'stage' })
    const disabledAgent = makeAgent({ id: 'ag-disabled', enabled: false, kind: 'stage' })

    const policies: StagePolicy[] = [
      makePolicy({ stage: 'ideate', enabled: false, agent_id: null }),
      makePolicy({ stage: 'plan', enabled: true, agent_id: 'ag-active' }),
      makePolicy({ stage: 'execute', enabled: true, agent_id: 'ag-disabled' }),
      makePolicy({ stage: 'review', enabled: true, agent_id: null }),
    ]

    const uncovered = getUncoveredStages(policies, [activeAgent, disabledAgent])
    assert.deepEqual(
      uncovered.map((p) => p.stage),
      ['execute', 'review']
    )
  })
})

describe('utils/projects', () => {
  it('derives slugs, initials, permissions, and project operational summaries', () => {
    assert.equal(deriveProjectSlug('  GitSeed Alpha -- Web!! '), 'gitseed-alpha-web')
    assert.equal(getProjectInitials({ name: 'GitSeed Platform', slug: 'gp' }), 'GP')
    assert.equal(getProjectInitials({ name: 'Monolith', slug: 'mono' }), 'MO')

    const admin = makeUser({ org_role: 'admin' })
    const viewer = makeUser({ id: 'u-viewer', org_role: 'member' })
    const viewerProject = makeProject({ my_role: 'viewer' })

    assert.equal(canUserManageProject(admin, viewerProject), true)
    assert.equal(canUserManageProject(viewer, viewerProject), false)
    assert.equal(canUserContributeToProject(viewer, viewerProject), false)

    const seeds: Seed[] = [
      makeSeed({ id: 's1', status: 'awaiting_approval', stage: 'plan', spent: '10.00' }),
      makeSeed({ id: 's2', status: 'running', stage: 'execute', spent: '5.50' }),
      makeSeed({
        id: 's3',
        status: 'merged',
        stage: 'merge',
        spent: '4.50',
        merged_at: new Date().toISOString(),
      }),
    ]
    const summary = computeProjectSummary(makeProject({ id: 'p-1' }), seeds)
    assert.equal(summary.status, 'needs_action')
    assert.equal(summary.openSeeds.length, 2)
    assert.equal(summary.needsYouSeeds.length, 1)
    assert.equal(summary.runningSeeds.length, 1)
    assert.equal(summary.merged30dCount, 1)
    assert.equal(summary.totalSpend, 20)
    assert.equal(summary.effectiveMonthlyBudget, 250)
    assert.equal(summary.isBudgetOverridden, true)
  })
})

describe('utils/agents & utils/users', () => {
  it('slugifies agent handles, computes pass rates, and lists model options', () => {
    assert.equal(slugifyAgentHandle('Security Auditor!'), 'security-auditor')
    assert.equal(slugifyAgentHandle('123 Bot'), 'agent-123-bot')
    assert.equal(slugifyAgentHandle('x'), 'x-agent')
    assert.equal(getRuntimeDefinition('claude-code').nativeProvider, 'anthropic')

    const ag = makeAgent({ run_count: 8, succeeded_runs: 6 })
    assert.equal(getAgentFirstPassRate(ag), 75)
    assert.equal(getAgentFirstPassRate(makeAgent({ run_count: 0 })), null)

    const assigned = getAgentAssignedStages('ag-1', [
      makePolicy({ stage: 'review', agent_id: 'ag-1' }),
      makePolicy({ stage: 'ideate', agent_id: 'ag-1' }),
    ])
    assert.deepEqual(assigned, ['ideate', 'review'])

    const runs = [
      { id: 'r1', agent_id: 'ag-1', status: 'running' } as Run,
      { id: 'r2', agent_id: 'ag-1', status: 'succeeded' } as Run,
    ]
    assert.equal(getAgentActiveRuns('ag-1', runs).length, 1)

    const anthropicBackend: Backend = {
      id: 'b-anth',
      name: 'Anthropic Prod',
      type: 'inference',
      provider: 'anthropic',
      endpoint: null,
      config: { models: [{ id: 'claude-sonnet-4-6', enabled: true }] },
      secret_keys: ['api_key'],
      capacity: 5,
      access_restricted: false,
      allowed_agent_ids: [],
      health: 'healthy',
      health_checked_at: null,
      health_latency_ms: 10,
      health_error: null,
      enabled: true,
      created_by: null,
      created_at: null,
      updated_at: null,
      updated_by: null,
    }
    assert.equal(getNativeProviderBackends('claude-code', [anthropicBackend]).length, 1)
    assert.equal(getCompatibleModelBackends('opencode', [anthropicBackend]).length, 1)
    const opencodeOptions = getConfiguredModelOptions('opencode', [anthropicBackend])
    assert.ok(opencodeOptions.some((o) => o.value === 'claude-sonnet-4-6'))
  })

  it('derives user project assignments, stage approval capabilities, and role summaries', () => {
    const user = makeUser({ id: 'u-1', org_role: 'admin', status: 'active' })
    const projects = [
      makeProject({ id: 'p-1', name: 'Alpha', slug: 'alpha' }),
      makeProject({ id: 'p-2', name: 'Beta', slug: 'beta' }),
    ]
    const membersByProject: Record<string, ProjectMember[]> = {
      'p-1': [
        {
          id: 1,
          project_id: 'p-1',
          user_id: 'u-1',
          email: user.email,
          name: user.name,
          preferred_name: null,
          role: 'maintainer',
          created_at: null,
          created_by: null,
          updated_at: null,
        },
      ],
      'p-2': [
        {
          id: 2,
          project_id: 'p-2',
          user_id: 'u-1',
          email: user.email,
          name: user.name,
          preferred_name: null,
          role: 'developer',
          created_at: null,
          created_by: null,
          updated_at: null,
        },
      ],
    }

    const assignments = getUserProjectAssignments('u-1', projects, membersByProject)
    assert.equal(assignments.length, 2)

    const mergePolicy = makePolicy({
      stage: 'merge',
      requires_approval: true,
      min_approver_role: 'maintainer',
      min_approvers: 2,
    })
    assert.equal(canRoleApprovePolicy('developer', mergePolicy), false)
    assert.equal(canRoleApprovePolicy('maintainer', mergePolicy), true)

    const projApprovals = deriveProjectStageApprovals('active', 'maintainer', [mergePolicy])
    assert.deepEqual(projApprovals.stages, ['merge'])
    assert.equal(projApprovals.requiresMultiApprover, true)

    const userApprovals = deriveUserStageApprovals(
      user,
      projects,
      membersByProject,
      {},
      [mergePolicy]
    )
    assert.deepEqual(userApprovals.stages, ['merge'])

    const roleSummary = getUserRoleSummary(user, projects, membersByProject)
    assert.equal(roleSummary.label, 'Admin · Maintainer on 1 · Dev on 1')
  })
})
