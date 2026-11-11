import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import type {
  ActivityEvent,
  ActivityType,
  Agent,
  Backend,
  Project,
  Seed,
  User,
} from '../types'
import {
  type ActivityCategory,
  formatStageName,
  formatTimeOfDay,
  getActivityCategory,
  getActivityDayGroup,
  parseActivityEvent,
  parseActivityEvents,
} from '../utils/activity'

const ALL_ACTIVITY_TYPES: Record<ActivityType, ActivityCategory> = {
  'approval.granted': 'approvals',
  'approval.rejected': 'approvals',
  'approval.changes_requested': 'approvals',
  'seed.created': 'stages',
  'seed.autonomy_changed': 'stages',
  'seed.merged': 'stages',
  'seed.closed': 'stages',
  'seed.reopened': 'stages',
  'seed.stopped': 'stages',
  'seed.resumed': 'stages',
  'stage.changed': 'stages',
  'run.started': 'agents',
  'run.succeeded': 'agents',
  'run.failed': 'agents',
  'run.cancelled': 'agents',
  'run.timed_out': 'agents',
  'policy.updated': 'admin',
  'budget.raised': 'admin',
  'budget.blocked': 'admin',
  'project.created': 'admin',
  'project.archived': 'admin',
  'project.restored': 'admin',
  'user.invited': 'admin',
  'user.activated': 'admin',
  'user.suspended': 'admin',
  'user.reactivated': 'admin',
  'repository.added': 'admin',
  'repository.removed': 'admin',
  'backend.granted': 'admin',
  'backend.revoked': 'admin',
  'backend.secret_updated': 'admin',
  'member.added': 'admin',
  'member.role_changed': 'admin',
  'member.removed': 'admin',
  'settings.updated': 'admin',
  'pat.created': 'admin',
  'pat.revoked': 'admin',
  'sandbox.started': 'admin',
}

function makeEvent(
  type: ActivityType,
  data: Record<string, unknown> = {},
  overrides: Partial<ActivityEvent> = {}
): ActivityEvent {
  return {
    id: 1,
    occurred_at: new Date().toISOString(),
    project_id: 'proj-1',
    seed_id: 'seed-1',
    actor_type: 'user',
    actor_id: 'u-1',
    type,
    data,
    ...overrides,
  }
}

const CONTEXT = {
  users: [
    {
      id: 'u-1',
      email: 'ada@example.com',
      name: 'Ada Lovelace',
      preferred_name: 'Ada',
      org_role: 'admin',
      status: 'active',
      preferences: {},
      last_active_at: null,
      created_at: null,
    } satisfies User,
  ],
  agents: [
    {
      id: 'ag-1',
      handle: 'coder',
      name: 'Coder',
      kind: 'stage',
      template: 'coder',
      instructions: '',
      runtime: 'opencode',
      model: 'default',
      skills: [],
      tools: [],
      enabled: true,
      allowed_backend_ids: [],
      run_count: 1,
      succeeded_runs: 1,
      total_cost: '0.10',
      archived_at: null,
      archived_by: null,
      created_at: null,
      created_by: null,
      updated_at: null,
    } satisfies Agent,
  ],
  projects: [
    {
      id: 'proj-1',
      name: 'Core Platform',
      slug: 'core-platform',
      description: '',
      tags: [],
      visibility: 'private',
      seed_counter: 12,
      monthly_budget: null,
      seed_budget_cap: null,
      spend_checkpoint: null,
      max_concurrent_runs: null,
      sandbox_config: {},
      backends_restricted: false,
      allowed_backend_ids: [],
      my_role: 'maintainer',
      archived_at: null,
      archived_by: null,
      created_at: null,
      created_by: null,
      updated_at: null,
    } satisfies Project,
  ],
  seeds: [
    {
      id: 'seed-1',
      project_id: 'proj-1',
      number: 12,
      title: 'Add SSO flow',
      description: '',
      specification: '',
      acceptance_criteria: [],
      plan: null,
      tags: [],
      stage: 'plan',
      status: 'running',
      autonomy: 'supervised',
      stage_entered_at: null,
      blocked_reason: null,
      budget_cap: null,
      spent: '0.00',
      checkpoint_approved_through: '0.00',
      merged_at: null,
      closed_at: null,
      closed_by: null,
      created_at: null,
      created_by: 'u-1',
      updated_at: null,
    } satisfies Seed,
  ],
  backends: [
    {
      id: 'b-1',
      name: 'Anthropic Gateway',
      type: 'inference',
      provider: 'anthropic',
      endpoint: null,
      config: {},
      secret_keys: [],
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
    } satisfies Backend,
  ],
}

describe('utils/activity', () => {
  it('maps all 38 ActivityType values to their expected category', () => {
    const entries = Object.entries(ALL_ACTIVITY_TYPES) as [
      ActivityType,
      ActivityCategory,
    ][]
    assert.equal(entries.length, 38)
    for (const [type, expectedCategory] of entries) {
      assert.equal(
        getActivityCategory(type),
        expectedCategory,
        `Expected ${type} to map to ${expectedCategory}`
      )
    }
  })

  it('formats stage names, day groups, and time of day', () => {
    assert.equal(formatStageName('ideating'), 'Ideate')
    assert.equal(formatStageName('planning'), 'Plan')
    assert.equal(formatStageName('executing'), 'Execute')
    assert.equal(formatStageName('reviewing'), 'Review')
    assert.equal(formatStageName('completed'), 'Merge')
    assert.equal(formatStageName('rejected'), 'Closed')
    assert.equal(formatStageName(null), '')

    assert.equal(getActivityDayGroup(new Date()), 'Today')
    assert.equal(
      getActivityDayGroup(new Date(Date.now() - 24 * 3600 * 1000)),
      'Yesterday'
    )
    assert.equal(getActivityDayGroup(null), 'Earlier')
    assert.equal(formatTimeOfDay('invalid-date'), '')
    assert.ok(formatTimeOfDay(new Date()).length > 0)
  })

  it('formats human-readable summaries for seed, stage, approval, and run events', () => {
    assert.equal(
      parseActivityEvent(makeEvent('seed.created'), CONTEXT).fullText,
      'Ada created seed #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('seed.autonomy_changed', { from: 'manual', to: 'autonomous' }),
        CONTEXT
      ).fullText,
      'Ada changed autonomy (manual → autonomous) on #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(makeEvent('seed.merged'), CONTEXT).fullText,
      'Ada merged #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('seed.closed', { reason: 'approval_rejected', stage: 'plan' }),
        CONTEXT
      ).fullText,
      'Ada closed after rejection #12 Add SSO flow (Plan)'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('seed.reopened', { stage: 'plan' }),
        CONTEXT
      ).fullText,
      'Ada reopened #12 Add SSO flow in Plan'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('stage.changed', { from: 'plan', to: 'execute' }),
        CONTEXT
      ).fullText,
      '#12 Add SSO flow moved Plan → Execute'
    )

    // Approvals (manual, auto, rejected, changes_requested)
    assert.equal(
      parseActivityEvent(
        makeEvent('approval.granted', { stage: 'plan' }),
        CONTEXT
      ).fullText,
      'Ada approved the plan for #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'approval.granted',
          { stage: 'execute', automatic: true },
          { actor_type: 'system', actor_id: null }
        ),
        CONTEXT
      ).fullText,
      'Execution auto-approved for #12 Add SSO flow (autonomous policy)'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('approval.rejected', { stage: 'review' }),
        CONTEXT
      ).fullText,
      'Ada rejected the merge for #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('approval.changes_requested', { stage: 'plan' }),
        CONTEXT
      ).fullText,
      'Ada requested changes to the plan for #12 Add SSO flow'
    )

    // Runs
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'run.started',
          { stage: 'execute', attempt: 2 },
          { actor_type: 'agent', actor_id: 'ag-1' }
        ),
        CONTEXT
      ).fullText,
      '@coder started Execute run (attempt #2) on #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'run.succeeded',
          { stage: 'execute', cost: '0.45', input_tokens: 1000, output_tokens: 500 },
          { actor_type: 'agent', actor_id: 'ag-1' }
        ),
        CONTEXT
      ).fullText,
      `@coder completed Execute run ($0.45 · ${(1500).toLocaleString()} tok) for #12 Add SSO flow`
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'run.failed',
          { stage: 'execute', attempt: 1 },
          { actor_type: 'agent', actor_id: 'ag-1' }
        ),
        CONTEXT
      ).fullText,
      '@coder failed Execute run (attempt #1) on #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('run.cancelled', { stage: 'execute' }),
        CONTEXT
      ).fullText,
      'Ada cancelled Execute run on #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('run.timed_out', { stage: 'execute' }),
        CONTEXT
      ).fullText,
      'Execute run timed out on #12 Add SSO flow'
    )
  })

  it('formats human-readable summaries for policy, budget, project, repo, backend, member, user, settings, PAT, and sandbox events', () => {
    assert.equal(
      parseActivityEvent(
        makeEvent('policy.updated', { stage: 'execute' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada updated Execute policy override on "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('budget.raised', { budget_cap: '50.00' }),
        CONTEXT
      ).fullText,
      'Ada raised seed budget cap to $50.00 on #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('budget.blocked', {
          reason: 'spend_checkpoint',
          spend_checkpoint: '25.00',
        }),
        CONTEXT
      ).fullText,
      'Paused at spend checkpoint ($25.00) on #12 Add SSO flow'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('project.created', { slug: 'core-platform' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada created project "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('project.archived', {}, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada archived project "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('project.restored', {}, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada restored project "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('repository.added', { full_name: 'acme/core' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada connected repository acme/core to "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('repository.removed', { full_name: 'acme/core' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada detached repository acme/core from "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'backend.granted',
          { backend_id: 'b-1', agent_id: 'ag-1' },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada granted backend Anthropic Gateway to @coder'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'backend.revoked',
          { backend_id: 'b-1', agent_id: 'ag-1' },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada revoked backend Anthropic Gateway from @coder'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('backend.secret_updated', { backend_id: 'b-1' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada updated credentials on backend Anthropic Gateway'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'member.added',
          { user_id: 'u-1', email: 'grace@example.com', role: 'developer' },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada added grace@example.com (developer) to "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'member.role_changed',
          { user_id: 'u-1', from: 'developer', to: 'maintainer' },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada changed project role (developer → maintainer) for Ada on "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('member.removed', { user_id: 'u-1' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada removed Ada from "Core Platform"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'user.invited',
          { email: 'linus@example.com', org_role: 'member' },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada invited linus@example.com as member'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('user.activated', { email: 'linus@example.com' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada activated their account'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('user.suspended', { email: 'linus@example.com' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada suspended account for linus@example.com'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('user.reactivated', { email: 'linus@example.com' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada reactivated account for linus@example.com'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'settings.updated',
          { changes: { default_autonomy: 'autonomous', max_concurrent_runners: 8 } },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada updated organization settings (default_autonomy, max_concurrent_runners)'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('pat.created', { name: 'CI Token' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada created personal access token "CI Token"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent('pat.revoked', { name: 'CI Token' }, { seed_id: null }),
        CONTEXT
      ).fullText,
      'Ada revoked personal access token "CI Token"'
    )
    assert.equal(
      parseActivityEvent(
        makeEvent(
          'sandbox.started',
          { ref: 'seed/12', head_sha: 'abc1234', command: 'npm test' },
          { seed_id: null }
        ),
        CONTEXT
      ).fullText,
      'Ada started sandbox on seed/12 @ abc1234 (npm test)'
    )
  })

  it('supports seedScoped formatting and sorts parseActivityEvents descending by id', () => {
    const e1 = makeEvent('seed.created', {}, { id: 10 })
    const e2 = makeEvent('seed.merged', {}, { id: 25 })
    const parsed = parseActivityEvents([e1, e2], { ...CONTEXT, seedScoped: true })
    assert.equal(parsed.length, 2)
    assert.equal(parsed[0].id, 25)
    assert.equal(parsed[0].fullText, 'Seed merged · Ada')
    assert.equal(parsed[1].id, 10)
    assert.equal(parsed[1].fullText, 'Seed created · Ada')
  })
})
