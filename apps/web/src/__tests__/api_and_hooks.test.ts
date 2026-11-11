import assert from 'node:assert/strict'
import { afterEach, describe, it } from 'node:test'
import { ApiError, api, isValidUser, request, setOnUnauthorized } from '../api/client'
import { queryClient, queryKeys } from '../api/queryClient'
import { VIEWS } from '../constants'
import { buildDocumentTitle } from '../hooks/useDocumentTitle'

const originalFetch = globalThis.fetch

interface CapturedCall {
  url: string
  init?: RequestInit
}

function mockFetchJson(body: unknown = { ok: true }, status = 200, headers: Record<string, string> = {}) {
  const calls: CapturedCall[] = []
  globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init })
    return new Response(status === 204 ? null : JSON.stringify(body), {
      status,
      headers: {
        'content-type': 'application/json',
        ...headers,
      },
    })
  }) as typeof fetch
  return calls
}

describe('src/api/client.ts & src/api/queryClient.ts', () => {
  afterEach(() => {
    globalThis.fetch = originalFetch
    setOnUnauthorized(null)
  })

  it('validates user objects with isValidUser', () => {
    assert.equal(isValidUser(null), false)
    assert.equal(isValidUser('string'), false)
    assert.equal(isValidUser({ id: '', email: 'a@b.com', org_role: 'admin' }), false)
    assert.equal(isValidUser({ id: 'u1', email: 'invalid', org_role: 'admin' }), false)
    assert.equal(isValidUser({ id: 'u1', email: 'a@b.com', org_role: 'guest' }), false)
    assert.equal(isValidUser({ id: 'u1', email: 'a@b.com', org_role: 'admin' }), true)
    assert.equal(isValidUser({ id: 'u2', email: 'm@b.com', org_role: 'member' }), true)
  })

  it('handles 204 No Content, 4xx/5xx errors, unauthorized hooks, and network errors in request()', async () => {
    mockFetchJson(null, 204)
    const res204 = await request('/api/v1/empty', { method: 'DELETE', timeoutMs: 1000 })
    assert.equal(res204, undefined)

    let unauthTriggered = 0
    setOnUnauthorized(() => {
      unauthTriggered += 1
    })

    mockFetchJson({ detail: 'Invalid token' }, 401)
    await assert.rejects(
      () => request('/api/v1/protected'),
      (err: unknown) => err instanceof ApiError && err.status === 401 && err.detail === 'Invalid token'
    )
    assert.equal(unauthTriggered, 1)

    // skipUnauthorizedHook prevents callback
    await assert.rejects(
      () => request('/api/v1/protected', { skipUnauthorizedHook: true }),
      (err: unknown) => err instanceof ApiError && err.status === 401
    )
    assert.equal(unauthTriggered, 1)

    // FastAPI validation error array detail
    mockFetchJson({ detail: [{ msg: 'Field required' }] }, 422)
    await assert.rejects(
      () => request('/api/v1/validate'),
      (err: unknown) => err instanceof ApiError && err.status === 422 && err.detail === 'Field required'
    )

    // Non-JSON error response
    globalThis.fetch = (async () =>
      new Response('Bad Gateway', { status: 502, headers: { 'content-type': 'text/plain' } })) as typeof fetch
    await assert.rejects(
      () => request('/api/v1/fail'),
      (err: unknown) => err instanceof ApiError && err.status === 502 && err.detail === 'Request failed (HTTP 502)'
    )

    // Non-JSON 200 response
    globalThis.fetch = (async () =>
      new Response('<html></html>', { status: 200, headers: { 'content-type': 'text/html' } })) as typeof fetch
    await assert.rejects(
      () => request('/api/v1/html'),
      (err: unknown) =>
        err instanceof ApiError &&
        err.status === 200 &&
        err.detail === 'Expected JSON response from control plane API.'
    )

    // AbortError
    globalThis.fetch = (async () => {
      const err = new Error('The operation was aborted')
      err.name = 'AbortError'
      throw err
    }) as typeof fetch
    await assert.rejects(
      () => request('/api/v1/slow', { timeoutMs: 250 }),
      (err: unknown) => err instanceof ApiError && err.status === 0 && err.detail === 'Connection timed out (250ms)'
    )

    // Generic Error and non-Error
    globalThis.fetch = (async () => {
      throw new Error('ECONNREFUSED')
    }) as typeof fetch
    await assert.rejects(
      () => request('/api/v1/down'),
      (err: unknown) => err instanceof ApiError && err.status === 0 && err.detail === 'ECONNREFUSED'
    )

    globalThis.fetch = (async () => {
      throw 'string failure'
    }) as typeof fetch
    await assert.rejects(
      () => request('/api/v1/down'),
      (err: unknown) =>
        err instanceof ApiError &&
        err.status === 0 &&
        err.detail === 'Unable to reach GitSeed Control Plane API.'
    )
  })

  it('exercises all api.system, api.auth, and api.users endpoints', async () => {
    const validUser = { id: 'u1', email: 'admin@gitseed.dev', name: 'Admin', org_role: 'admin' }
    const calls = mockFetchJson(validUser)

    await api.system.health()
    await api.auth.me()
    await api.auth.login('admin@gitseed.dev', 'secret')
    await api.auth.logout()
    await api.auth.changePassword('old', 'new')
    await api.auth.previewInvite('tok-123')
    await api.auth.acceptInvite({ token: 'tok-123', password: 'pw', preferred_name: 'Admin' })
    await api.auth.listTokens()
    await api.auth.createToken('CI Token', 30)
    await api.auth.revokeToken('pat-1')

    await api.users.list()
    await api.users.get('u1')
    await api.users.invite({ email: 'new@gitseed.dev', name: 'New', org_role: 'member' })
    await api.users.reinvite('u1')
    await api.users.update('u1', { name: 'Updated', preferred_name: 'Up' })
    await api.users.suspend('u1')
    await api.users.reactivate('u1')
    await api.users.delete('u1')

    assert.equal(calls.length, 18)

    // Invalid user payload on me() and login()
    mockFetchJson({ invalid: true })
    await assert.rejects(() => api.auth.me(), /Invalid user payload/)
    await assert.rejects(() => api.auth.login('a@b.com', 'pw'), /Invalid response/)
  })

  it('exercises all api.projects and sandbox endpoints', async () => {
    const calls = mockFetchJson({ id: 'p1' })

    await api.projects.list()
    await api.projects.list(false)
    await api.projects.get('p1')
    await api.projects.create({ name: 'Proj 1', visibility: 'private' })
    await api.projects.update('p1', { name: 'Proj 1 Updated' })
    await api.projects.archive('p1')
    await api.projects.restore('p1')
    await api.projects.listRepos('p1')
    await api.projects.addRepo('p1', { backend_id: 'b1', full_name: 'org/repo', is_primary: true })
    await api.projects.updateRepo('p1', 10, { default_branch: 'main' })
    await api.projects.removeRepo('p1', 10)
    await api.projects.listRepoBranches('p1', 10)
    await api.projects.getRepoTree('p1', 10)
    await api.projects.getRepoTree('p1', 10, { ref: 'main', path: 'src' })
    await api.projects.getRepoBlob('p1', 10, { path: 'README.md' })
    await api.projects.getRepoBlob('p1', 10, { path: 'README.md', ref: 'dev' })
    await api.projects.listMembers('p1')
    await api.projects.addMember('p1', { user_id: 'u1', role: 'maintainer' })
    await api.projects.updateMember('p1', 'u1', { role: 'developer' })
    await api.projects.removeMember('p1', 'u1')
    await api.projects.allowBackend('p1', 'b1')
    await api.projects.disallowBackend('p1', 'b1')
    await api.projects.listPolicies('p1')
    await api.projects.getSandboxConfig('p1')
    await api.projects.getSandboxConfig('p1', { repo_id: 10, ref: 'main' })
    await api.projects.listSandboxes('p1', 10)
    await api.projects.createSandbox('p1', { command: 'pytest' })
    await api.projects.getSandbox('p1', 'sb1')
    await api.projects.sendSandboxInput('p1', 'sb1', { input: 'ls', append_newline: true })
    await api.projects.stopSandbox('p1', 'sb1')

    const dlUrl = api.projects.getSandboxArtifactDownloadUrl('p1', 'sb1', 'dist/out file.txt')
    assert.equal(dlUrl, '/api/v1/projects/p1/sandboxes/sb1/artifacts/dist/out%20file.txt')
    assert.equal(calls.length, 30)
  })

  it('exercises all api.seeds, api.runs, api.backends, api.agents, api.skills, api.policies, api.settings, and api.activity endpoints', async () => {
    const calls = mockFetchJson({ ok: true })

    await api.seeds.list()
    await api.seeds.list('p1')
    await api.seeds.get('s1')
    await api.seeds.create({ project_id: 'p1', title: 'Seed 1' })
    await api.seeds.update('s1', { title: 'Seed 1b' })
    await api.seeds.advance('s1')
    await api.seeds.advance('s1', 'execute')
    await api.seeds.approveCheckpoint('s1', 50)
    await api.seeds.close('s1')
    await api.seeds.reopen('s1')
    await api.seeds.listTasks('s1')
    await api.seeds.createTask('s1', { title: 'Task 1' })
    await api.seeds.updateTask('s1', 't1', { status: 'done' })
    await api.seeds.deleteTask('s1', 't1')
    await api.seeds.listApprovals('s1')
    await api.seeds.listApprovals('s1', 'plan')
    await api.seeds.createApproval('s1', { decision: 'approved', comment: 'LGTM' })
    await api.seeds.listComments('s1')
    await api.seeds.createComment('s1', { body: 'Hello' })
    await api.seeds.updateComment('s1', 'c1', { body: 'Edited' })
    await api.seeds.deleteComment('s1', 'c1')
    await api.seeds.getChanges('s1')
    await api.seeds.getChanges('s1', 5)

    await api.runs.list()
    await api.runs.get('r1')
    await api.runs.create({ seed_id: 's1', agent_id: 'a1' })
    await api.runs.cancel('r1')

    await api.backends.list()
    await api.backends.get('b1')
    await api.backends.create({ name: 'GH', type: 'git', provider: 'github' })
    await api.backends.update('b1', { enabled: true })
    await api.backends.delete('b1')
    await api.backends.grantAgent('b1', 'a1')
    await api.backends.revokeAgent('b1', 'a1')
    await api.backends.test('b1')
    await api.backends.discoverRepos('b1')
    await api.backends.createRepo('b1', { name: 'new-repo', private: true })
    await api.backends.discoverModels('b1')
    await api.backends.probe({ type: 'inference', provider: 'openai' })
    await api.backends.startCliAuth('b1')
    await api.backends.getCliAuthStatus('b1')
    await api.backends.submitCliAuthCode('b1', 'CODE123')
    await api.backends.cancelCliAuth('b1')

    await api.agents.list()
    await api.agents.list(true)
    await api.agents.get('a1')
    await api.agents.create({ name: 'Coder' })
    await api.agents.update('a1', { enabled: false })
    await api.agents.archive('a1')
    await api.agents.restore('a1')
    await api.agents.chatCoordinator({ messages: [{ role: 'user', content: 'hi' }] })

    await api.skills.list()
    await api.skills.list(true)
    await api.skills.get('sk1')
    await api.skills.create({ name: 'Skill 1' })
    await api.skills.update('sk1', { description: 'Updated' })
    await api.skills.archive('sk1')
    await api.skills.restore('sk1')

    await api.policies.list()
    await api.policies.updateOrg('execute', { requires_approval: true })
    await api.policies.listProject('p1')
    await api.policies.updateProject('p1', 'execute', { min_approvers: 2 })
    await api.policies.resetProject('p1', 'execute')

    await api.settings.get()
    await api.settings.update({ default_autonomy: 'supervised' })

    await api.activity.list()
    await api.activity.list(50)

    assert.equal(calls.length, 67)
  })

  it('builds deterministic queryKeys and configures queryClient', () => {
    assert.ok(queryClient)
    assert.deepEqual(queryKeys.health, ['system', 'health'])
    assert.deepEqual(queryKeys.projects, ['projects'])
    assert.deepEqual(queryKeys.projectDetail('p1'), ['projects', 'p1'])
    assert.deepEqual(queryKeys.projectRepos('p1'), ['projects', 'p1', 'repos'])
    assert.deepEqual(queryKeys.projectMembers('p1'), ['projects', 'p1', 'members'])
    assert.deepEqual(queryKeys.projectPolicies('p1'), ['projects', 'p1', 'policies'])
    assert.deepEqual(queryKeys.projectSandboxes('p1'), ['projects', 'p1', 'sandboxes'])
    assert.deepEqual(queryKeys.projectSandboxConfig('p1'), [
      'projects',
      'p1',
      'sandboxConfig',
      'primary',
      'default',
    ])
    assert.deepEqual(queryKeys.projectSandboxConfig('p1', 12, 'main'), [
      'projects',
      'p1',
      'sandboxConfig',
      12,
      'main',
    ])
    assert.deepEqual(queryKeys.seeds, ['seeds'])
    assert.deepEqual(queryKeys.seedDetail('s1'), ['seeds', 's1'])
    assert.deepEqual(queryKeys.seedTasks('s1'), ['seeds', 's1', 'tasks'])
    assert.deepEqual(queryKeys.seedApprovals('s1'), ['seeds', 's1', 'approvals'])
    assert.deepEqual(queryKeys.seedComments('s1'), ['seeds', 's1', 'comments'])
    assert.deepEqual(queryKeys.seedChanges('s1'), ['seeds', 's1', 'changes', 'primary'])
    assert.deepEqual(queryKeys.seedChanges('s1', 7), ['seeds', 's1', 'changes', 7])
    assert.deepEqual(queryKeys.runs, ['runs'])
    assert.deepEqual(queryKeys.activity, ['activity'])
    assert.deepEqual(queryKeys.backends, ['backends'])
    assert.deepEqual(queryKeys.agents, ['agents'])
    assert.deepEqual(queryKeys.skills, ['skills'])
    assert.deepEqual(queryKeys.policies, ['policies'])
    assert.deepEqual(queryKeys.users, ['users'])
    assert.deepEqual(queryKeys.settings, ['settings'])
    assert.deepEqual(queryKeys.tokens, ['auth', 'tokens'])
  })
})

describe('buildDocumentTitle hook helper', () => {
  const sampleProj = {
    id: 'p1',
    name: 'Core Platform',
    slug: 'core-platform',
    description: '',
    tags: [],
    visibility: 'private' as const,
    seed_counter: 42,
    monthly_budget: null,
    seed_budget_cap: null,
    spend_checkpoint: null,
    max_concurrent_runs: null,
    sandbox_config: {},
    backends_restricted: false,
    allowed_backend_ids: [],
    my_role: 'maintainer' as const,
    archived_at: null,
    archived_by: null,
    created_at: null,
    created_by: null,
    updated_at: null,
  }
  const sampleSeed = {
    id: 's1',
    project_id: 'p1',
    number: 42,
    title: 'Fix auth token refresh',
    description: '',
    specification: '',
    acceptance_criteria: [],
    plan: null,
    tags: [],
    stage: 'execute' as const,
    status: 'running' as const,
    autonomy: 'supervised' as const,
    stage_entered_at: null,
    blocked_reason: null,
    budget_cap: null,
    spent: '0.00',
    checkpoint_approved_through: '0.00',
    merged_at: null,
    closed_at: null,
    closed_by: null,
    created_at: null,
    created_by: null,
    updated_at: null,
  }
  const sampleUser = {
    id: 'u1',
    email: 'alice@example.com',
    name: 'Alice',
    preferred_name: null,
    org_role: 'admin' as const,
    status: 'active' as const,
    preferences: {},
    last_active_at: null,
    created_at: null,
  }
  const baseOpts = {
    pathname: '/',
    isAuthenticated: true,
    activeView: VIEWS.DASHBOARD,
    activeDetailId: null as string | null,
    activeDetailTab: null as string | null,
    selectedProjectObj: null,
    projects: [sampleProj],
    allSeeds: [sampleSeed],
    users: [sampleUser],
  }

  it('builds titles for unauthenticated, invite, and top-level views', () => {
    assert.equal(
      buildDocumentTitle({ ...baseOpts, pathname: '/accept-invite' }),
      'Accept Invite · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, isAuthenticated: false }),
      'Sign in · GitSeed'
    )
    assert.equal(buildDocumentTitle(baseOpts), 'Overview · GitSeed')
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        selectedProjectObj: sampleProj,
      }),
      'Overview · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.SEEDS }),
      'Seeds · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        activeView: VIEWS.SEEDS,
        selectedProjectObj: sampleProj,
      }),
      'Seeds · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.TELEMETRY }),
      'Activity · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.PROJECTS }),
      'Projects · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.AGENTS }),
      'Agents · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.ROUTES }),
      'Policies · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.BACKENDS }),
      'Backends · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.USERS }),
      'Users · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.SETTINGS }),
      'Settings · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.GUIDE }),
      'Guide · GitSeed'
    )
  })

  it('builds titles for detail views', () => {
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.PROJECTS, activeDetailId: 'p1' }),
      'Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        activeView: VIEWS.PROJECTS,
        activeDetailId: 'core-platform',
        activeDetailTab: 'settings',
      }),
      'Settings · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.SEEDS, activeDetailId: 's1' }),
      'Fix auth token refresh · #42 · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({
        ...baseOpts,
        activeView: VIEWS.SEEDS,
        activeDetailId: 'core-platform#42',
      }),
      'Fix auth token refresh · #42 · Core Platform · GitSeed'
    )
    assert.equal(
      buildDocumentTitle({ ...baseOpts, activeView: VIEWS.USERS, activeDetailId: 'u1' }),
      'Alice · Users · GitSeed'
    )
  })
})

