import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  BACKEND_PROVIDER_DEFINITIONS,
  BACKEND_TYPE_ORDER,
  CORE_BACKEND_REQUIREMENTS,
  DEFAULT_CLOUD_MODELS,
} from '../constants/backends'
import type { Agent, Backend, Project, ProjectRepository, Run } from '../types'
import {
  formatCleanEndpoint,
  formatProviderName,
  getBackendAccessSummary,
  getBackendCapacityUsage,
  getBackendDefaultModel,
  getBackendModels,
  getBackendRepositories,
  getEnabledBackendModels,
  getEnabledBackendRepositories,
  getMissingCoreBackends,
  getProjectsUsingBackend,
  getProviderDefinition,
} from '../utils/backends'

function makeBackend(overrides: Partial<Backend> = {}): Backend {
  return {
    id: 'backend-1',
    name: 'Primary Backend',
    type: 'compute',
    provider: 'docker',
    endpoint: 'unix:///var/run/docker.sock',
    config: {},
    secret_keys: [],
    capacity: 4,
    access_restricted: false,
    allowed_agent_ids: [],
    health: 'healthy',
    health_checked_at: null,
    health_latency_ms: 5,
    health_error: null,
    enabled: true,
    created_by: null,
    created_at: null,
    updated_at: null,
    updated_by: null,
    ...overrides,
  }
}

function makeRun(overrides: Partial<Run> = {}): Run {
  return {
    id: 'run-1',
    seed_id: 'seed-1',
    task_id: null,
    stage: 'execute',
    attempt: 1,
    agent_id: 'agent-1',
    runtime: 'opencode',
    model: 'claude-sonnet-4-6',
    compute_backend_id: 'backend-compute-1',
    status: 'running',
    container_id: null,
    input_tokens: 100,
    output_tokens: 50,
    cost: '0.01',
    output: {},
    exit_code: null,
    error: null,
    log_path: null,
    started_at: null,
    finished_at: null,
    cancelled_by: null,
    created_at: null,
    updated_at: null,
    ...overrides,
  }
}

function makeProject(overrides: Partial<Project> = {}): Project {
  return {
    id: 'proj-1',
    name: 'Core Platform',
    slug: 'core-platform',
    description: '',
    tags: [],
    visibility: 'private',
    seed_counter: 1,
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
    ...overrides,
  }
}

function makeAgent(overrides: Partial<Agent> = {}): Agent {
  return {
    id: 'agent-1',
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
    run_count: 5,
    succeeded_runs: 4,
    total_cost: '1.25',
    archived_at: null,
    archived_by: null,
    created_at: null,
    created_by: null,
    updated_at: null,
    ...overrides,
  }
}

describe('utils/backends & constants/backends', () => {
  it('exposes provider definitions and orders backend types', () => {
    assert.deepEqual(BACKEND_TYPE_ORDER, {
      git: 1,
      inference: 2,
      compute: 3,
      tools: 4,
    })
    assert.equal(CORE_BACKEND_REQUIREMENTS.length, 3)
    assert.ok(DEFAULT_CLOUD_MODELS.anthropic.includes('claude-sonnet-4-6'))
    assert.ok(DEFAULT_CLOUD_MODELS.gemini.includes('gemini-4-hydrogen'))
    assert.ok(DEFAULT_CLOUD_MODELS.antigravity.includes('gemini-4-hydrogen'))
    assert.ok(DEFAULT_CLOUD_MODELS.xai.includes('grok-4.20-reasoning'))
    assert.ok(BACKEND_PROVIDER_DEFINITIONS.git.some((p) => p.value === 'github'))

    const anthropicDef = getProviderDefinition('inference', 'ANTHROPIC')
    assert.equal(anthropicDef.value, 'anthropic')
    assert.equal(anthropicDef.supportsCliOAuth, true)
    assert.equal(anthropicDef.cliBinary, 'claude')

    const xaiDef = getProviderDefinition('inference', 'xai')
    assert.equal(xaiDef.supportsCliOAuth, false)

    const fallbackDef = getProviderDefinition('inference', 'unknown-provider')
    assert.equal(fallbackDef.value, BACKEND_PROVIDER_DEFINITIONS.inference[0].value)
  })

  it('formats provider display names and clean endpoints', () => {
    assert.equal(formatProviderName('github-app'), 'GitHub App')
    assert.equal(formatProviderName('anthropic'), 'Anthropic')
    assert.equal(formatProviderName('xai'), 'xAI (Grok)')
    assert.equal(formatProviderName('k8s'), 'Kubernetes')
    assert.equal(formatProviderName('mcp-stdio'), 'MCP (stdio)')
    assert.equal(formatProviderName('my-custom'), 'my-custom')

    // CLI OAuth model backend
    const cliBackend = makeBackend({
      type: 'inference',
      provider: 'anthropic',
      config: { auth_mode: 'cli_oauth' },
    })
    assert.equal(formatCleanEndpoint(cliBackend), 'Anthropic CLI (OAuth)')

    // Stripping protocol prefixes
    assert.equal(
      formatCleanEndpoint(
        makeBackend({ type: 'inference', provider: 'openai', endpoint: 'https://api.openai.com/v1' })
      ),
      'api.openai.com/v1'
    )
    assert.equal(
      formatCleanEndpoint(
        makeBackend({ type: 'compute', provider: 'docker', endpoint: 'unix:///var/run/docker.sock' })
      ),
      '/var/run/docker.sock'
    )

    // Empty endpoint fallbacks
    assert.equal(
      formatCleanEndpoint(makeBackend({ type: 'git', provider: 'github-app', endpoint: null })),
      'GitHub App'
    )
    assert.equal(
      formatCleanEndpoint(
        makeBackend({
          type: 'tools',
          provider: 'mcp-stdio',
          endpoint: '',
          config: { command: 'npx @mcp/server' },
        })
      ),
      'npx @mcp/server'
    )
  })

  it('computes capacity usage for compute, models, git, and tools backends', () => {
    const computeBackend = makeBackend({
      id: 'compute-1',
      type: 'compute',
      provider: 'docker',
      capacity: 4,
    })
    const runs: Run[] = [
      makeRun({ id: 'r1', status: 'running', compute_backend_id: 'compute-1' }),
      makeRun({ id: 'r2', status: 'starting', compute_backend_id: 'compute-1' }),
      makeRun({ id: 'r3', status: 'succeeded', compute_backend_id: 'compute-1' }),
      makeRun({ id: 'r4', status: 'running', compute_backend_id: 'compute-other' }),
    ]

    const computeUsage = getBackendCapacityUsage(computeBackend, runs)
    assert.deepEqual(computeUsage, {
      current: 2,
      max: 4,
      display: '2 / 4 busy',
    })

    // Inference backend with agent restriction and model matching
    const modelsBackend = makeBackend({
      id: 'models-anthropic',
      type: 'inference',
      provider: 'anthropic',
      capacity: 3,
      access_restricted: true,
      allowed_agent_ids: ['agent-allowed'],
      config: {
        models: [{ id: 'claude-sonnet-4-6', enabled: true }],
      },
    })
    const modelRuns: Run[] = [
      makeRun({
        id: 'mr1',
        status: 'running',
        agent_id: 'agent-allowed',
        model: 'claude-sonnet-4-6',
      }),
      makeRun({
        id: 'mr2',
        status: 'queued',
        agent_id: 'agent-allowed',
        model: 'anthropic/claude-opus-4-6',
      }),
      makeRun({
        id: 'mr3',
        status: 'running',
        agent_id: 'agent-blocked',
        model: 'claude-sonnet-4-6',
      }),
      makeRun({
        id: 'mr4',
        status: 'running',
        agent_id: 'agent-allowed',
        model: 'gpt-5.2',
      }),
    ]
    const modelsUsage = getBackendCapacityUsage(modelsBackend, modelRuns)
    assert.deepEqual(modelsUsage, {
      current: 2,
      max: 3,
      display: '2 / 3 busy',
    })

    // Git and tools backends always return dash
    const gitBackend = makeBackend({ type: 'git', provider: 'github', capacity: 10 })
    assert.deepEqual(getBackendCapacityUsage(gitBackend, runs), { display: '-' })
    const toolsBackend = makeBackend({ type: 'tools', provider: 'mcp-sse', capacity: 10 })
    assert.deepEqual(getBackendCapacityUsage(toolsBackend, runs), { display: '-' })
  })

  it('determines which active projects use a backend', () => {
    const gitBackend = makeBackend({ id: 'git-1', type: 'git', provider: 'github' })
    const computeBackend = makeBackend({ id: 'comp-1', type: 'compute', provider: 'docker' })

    const pUnrestrictedWithRepo = makeProject({ id: 'p1', name: 'P1', backends_restricted: false })
    const pUnrestrictedOtherRepo = makeProject({ id: 'p2', name: 'P2', backends_restricted: false })
    const pRestrictedAllowed = makeProject({
      id: 'p3',
      name: 'P3',
      backends_restricted: true,
      allowed_backend_ids: ['git-1', 'comp-1'],
    })
    const pRestrictedDenied = makeProject({
      id: 'p4',
      name: 'P4',
      backends_restricted: true,
      allowed_backend_ids: ['other'],
    })
    const pArchived = makeProject({
      id: 'p5',
      name: 'P5',
      archived_at: '2026-11-11T00:00:00Z',
    })

    const reposByProject: Record<string, ProjectRepository[]> = {
      p1: [
        {
          id: 1,
          project_id: 'p1',
          backend_id: 'git-1',
          full_name: 'acme/p1',
          default_branch: 'main',
          is_primary: true,
          created_at: null,
          created_by: null,
          updated_at: null,
        },
      ],
      p2: [
        {
          id: 2,
          project_id: 'p2',
          backend_id: 'git-other',
          full_name: 'acme/p2',
          default_branch: 'main',
          is_primary: true,
          created_at: null,
          created_by: null,
          updated_at: null,
        },
      ],
    }

    const projects = [
      pUnrestrictedWithRepo,
      pUnrestrictedOtherRepo,
      pRestrictedAllowed,
      pRestrictedDenied,
      pArchived,
    ]

    const usingGit = getProjectsUsingBackend(gitBackend, projects, reposByProject)
    assert.deepEqual(
      usingGit.map((p) => p.id),
      ['p1', 'p3']
    )

    const usingCompute = getProjectsUsingBackend(computeBackend, projects, reposByProject)
    assert.deepEqual(
      usingCompute.map((p) => p.id),
      ['p1', 'p2', 'p3']
    )
  })

  it('summarizes backend agent access rules', () => {
    const agents = [
      makeAgent({ id: 'a1', handle: 'architect' }),
      makeAgent({ id: 'a2', handle: 'coder' }),
    ]

    const unrestricted = getBackendAccessSummary(
      makeBackend({ access_restricted: false }),
      agents
    )
    assert.equal(unrestricted.isAll, true)
    assert.equal(unrestricted.label, 'All agents')
    assert.equal(unrestricted.count, 2)

    const singleAgent = getBackendAccessSummary(
      makeBackend({ access_restricted: true, allowed_agent_ids: ['a1'] }),
      agents
    )
    assert.equal(singleAgent.isRestrictedToOne, true)
    assert.equal(singleAgent.label, '@architect only')
    assert.equal(singleAgent.tooltip, '@architect')

    const multiAgents = getBackendAccessSummary(
      makeBackend({ access_restricted: true, allowed_agent_ids: ['a1', 'a2'] }),
      agents
    )
    assert.equal(multiAgents.label, '2 agents')
    assert.equal(multiAgents.tooltip, '@architect, @coder')

    const noAgents = getBackendAccessSummary(
      makeBackend({ access_restricted: true, allowed_agent_ids: [] }),
      agents
    )
    assert.equal(noAgents.isRestrictedEmpty, true)
    assert.equal(noAgents.label, 'No agents')
  })

  it('extracts repositories, models, and missing core backends', () => {
    const gitBackend = makeBackend({
      type: 'git',
      provider: 'github',
      config: {
        repositories: [
          { full_name: 'acme/zebra', default_branch: 'develop', enabled: true },
          { full_name: 'acme/alpha', default_branch: '', enabled: false },
          { full_name: 'ACME/ZEBRA', default_branch: 'main', enabled: true },
        ],
      },
    })
    const allRepos = getBackendRepositories(gitBackend)
    assert.equal(allRepos.length, 2)
    assert.equal(allRepos[0].full_name, 'acme/alpha')
    assert.equal(allRepos[0].default_branch, 'main')
    assert.equal(getEnabledBackendRepositories(gitBackend).length, 1)

    const modelBackend = makeBackend({
      type: 'inference',
      provider: 'openai',
      config: {
        models: [
          { id: 'gpt-5.4', enabled: true },
          { id: 'gpt-5.3-codex', enabled: true, is_default: true },
          { id: 'gpt-5.1', enabled: false },
        ],
      },
    })
    assert.equal(getBackendModels(modelBackend).length, 3)
    assert.equal(getEnabledBackendModels(modelBackend).length, 2)
    assert.equal(getBackendDefaultModel(modelBackend), 'gpt-5.3-codex')
    assert.equal(getEnabledBackendModels(modelBackend)[0].id, 'gpt-5.3-codex')

    assert.deepEqual(getMissingCoreBackends([gitBackend]), ['Compute', 'Inference'])
  })
})
