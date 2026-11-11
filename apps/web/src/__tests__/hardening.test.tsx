import { describe, it } from 'node:test'
import assert from 'node:assert/strict'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { api } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { SEEDS_LIST_LIMIT, DataProvider } from '../context/DataContext'
import { AuthProvider } from '../context/AuthContext'
import { I18nProvider } from '../context/I18nContext'
import { StatusProvider } from '../context/StatusContext'
import { ThemeProvider } from '../context/ThemeContext'
import { SessionDockProvider } from '../context/SessionDockContext'
import { parseUnifiedDiffLines } from '../components/common/MonacoCodeEditor'
import { GettingStartedCard } from '../components/common/GettingStartedCard'
import { SeedMetadataSidebar } from '../components/details/seed/SeedMetadataSidebar'
import { ErrorBoundary } from '../components/ErrorBoundary'
import {
  mockStorage,
  mockUser,
  setupTestDom,
} from './setup'
import type { Backend, Seed } from '../types'

setupTestDom()

describe('Unified Diff Real File Line Numbers (parseUnifiedDiffLines)', () => {
  it('maps @@ -10,4 +25,5 @@ hunks to real target and original file line numbers', () => {
    const patch = [
      'diff --git a/src/auth.ts b/src/auth.ts',
      'index 1111111..2222222 100644',
      '--- a/src/auth.ts',
      '+++ b/src/auth.ts',
      '@@ -10,4 +25,5 @@ export function verifyToken() {',
      ' const header = req.headers.authorization',
      '-const token = header',
      '+const bearer = header?.replace(/^Bearer\\s+/i, "")',
      '+const token = bearer || null',
      ' if (!token) throw new Error("Unauthorized")',
      ' return token',
      '',
    ].join('\n')

    const parsed = parseUnifiedDiffLines(patch)

    // Hunk header occupies row 1 with an empty gutter number
    assert.equal(parsed.lineMap[1], '')
    assert.equal(parsed.originalLineMap[1], '')
    assert.equal(parsed.resolveModifiedLineNumber(1), null)
    assert.equal(parsed.resolveOriginalLineNumber(1), null)

    // Modified lines map directly to real new-file line numbers 25..29
    assert.equal(parsed.lineMap[2], '25')
    assert.equal(parsed.lineMap[3], '26')
    assert.equal(parsed.lineMap[4], '27')
    assert.equal(parsed.lineMap[5], '28')
    assert.equal(parsed.lineMap[6], '29')

    assert.equal(parsed.resolveModifiedLineNumber(2), 25)
    assert.equal(parsed.resolveModifiedLineNumber(3), 26)
    assert.equal(parsed.resolveModifiedLineNumber(4), 27)
    assert.equal(parsed.resolveModifiedLineNumber(5), 28)
    assert.equal(parsed.resolveModifiedLineNumber(6), 29)

    // Original lines map directly to real old-file line numbers 10..13
    assert.equal(parsed.originalLineMap[2], '10')
    assert.equal(parsed.originalLineMap[3], '11')
    assert.equal(parsed.originalLineMap[4], '12')
    assert.equal(parsed.originalLineMap[5], '13')
    assert.equal(parsed.resolveOriginalLineNumber(3), 11)
  })

  it('tracks real line numbers accurately across multiple hunks', () => {
    const multiHunkPatch = [
      '@@ -3,2 +3,2 @@',
      ' const a = 1',
      '-const b = 2',
      '+const b = 20',
      '@@ -40,2 +56,3 @@',
      ' export function run() {',
      '+  log("start")',
      '   return true',
    ].join('\n')

    const parsed = parseUnifiedDiffLines(multiHunkPatch)

    // First hunk: row 1 is hunk banner, rows 2..3 are lines 3..4
    assert.equal(parsed.lineMap[1], '')
    assert.equal(parsed.lineMap[2], '3')
    assert.equal(parsed.lineMap[3], '4')

    // Second hunk: row 4 is hunk banner, rows 5..7 are lines 56..58
    assert.equal(parsed.lineMap[4], '')
    assert.equal(parsed.lineMap[5], '56')
    assert.equal(parsed.lineMap[6], '57')
    assert.equal(parsed.lineMap[7], '58')
    assert.equal(parsed.resolveModifiedLineNumber(6), 57)
  })
})

describe('DataContext Seed Pagination Limit & Local Workspaces Client', () => {
  it('fetches seeds with limit <= 200 so backend validation never returns HTTP 422', async () => {
    assert.ok(
      SEEDS_LIST_LIMIT >= 1 && SEEDS_LIST_LIMIT <= 200,
      `Expected SEEDS_LIST_LIMIT (${SEEDS_LIST_LIMIT}) to be within 1..200`
    )

    const requestedUrls: string[] = []
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      requestedUrls.push(url)
      return new Response(JSON.stringify([]), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      })
    }) as typeof fetch

    try {
      const qc = new QueryClient({
        defaultOptions: { queries: { retry: false } },
      })
      mockStorage.setItem('gitseed_auth_user', JSON.stringify(mockUser))

      renderToStaticMarkup(
        React.createElement(
          QueryClientProvider,
          { client: qc },
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
                    React.createElement('div', null, 'probe')
                  )
                )
              )
            )
          )
        )
      )

      const seedsQuery = qc.getQueryCache().find({ queryKey: queryKeys.seeds })
      assert.ok(seedsQuery, 'Expected DataContext to register seeds query')
      const queryFn = seedsQuery.options.queryFn as (() => Promise<unknown>) | undefined
      assert.equal(typeof queryFn, 'function')
      await queryFn!()

      const seedCall = requestedUrls.find((u) => u.startsWith('/api/v1/seeds?'))
      assert.ok(seedCall, 'Expected GET /api/v1/seeds request')
      const parsedUrl = new URL(seedCall, 'http://localhost:8421')
      const limitParam = Number(parsedUrl.searchParams.get('limit'))
      assert.equal(limitParam, 200)
      assert.ok(limitParam <= 200)
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('calls POST /api/v1/projects/:id/repositories/init-local via api.localWorkspaces', async () => {
    let capturedUrl = ''
    let capturedMethod = ''
    let capturedBody: Record<string, unknown> = {}
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async (input: RequestInfo | URL, init?: RequestInit) => {
      capturedUrl = typeof input === 'string' ? input : input.toString()
      capturedMethod = init?.method || 'GET'
      capturedBody = init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {}
      return new Response(
        JSON.stringify({
          repository: {
            id: 1,
            project_id: 'proj-local',
            backend_id: 'b-local',
            full_name: 'local/my-app',
            default_branch: 'main',
            is_primary: true,
            created_at: null,
            created_by: 'u1',
            updated_at: null,
          },
          backend_id: 'b-local',
          bare_repo_path: '/var/lib/gitseed/repos/my-app.git',
          workspace_path: '/Users/dev/my-app',
          http_clone_url: 'http://localhost:8421/api/v1/local-git/my-app.git',
          initialized_git: true,
        }),
        { status: 201, headers: { 'Content-Type': 'application/json' } }
      )
    }) as typeof fetch

    try {
      const res = await api.localWorkspaces.linkDirectory(
        'proj-local',
        '/Users/dev/my-app',
        'main'
      )
      assert.equal(
        capturedUrl,
        '/api/v1/projects/proj-local/repositories/init-local'
      )
      assert.equal(capturedMethod, 'POST')
      assert.equal(capturedBody.workspace_path, '/Users/dev/my-app')
      assert.equal(capturedBody.default_branch, 'main')
      assert.equal(res.repository.full_name, 'local/my-app')
    } finally {
      globalThis.fetch = originalFetch
    }
  })

  it('unlocks GettingStartedCard Step 2 without requiring a cloud GitHub/GitLab backend', () => {
    const computeAndInferenceOnly: Backend[] = [
      {
        id: 'b-comp',
        name: 'Local Docker',
        type: 'compute',
        provider: 'docker',
        endpoint: 'unix:///var/run/docker.sock',
        config: {},
        secret_keys: [],
        capacity: 4,
        enabled: true,
        access_restricted: false,
        allowed_agent_ids: [],
        health: 'healthy',
        health_checked_at: null,
        health_latency_ms: null,
        health_error: null,
        created_by: null,
        created_at: null,
        updated_at: null,
        updated_by: null,
      },
      {
        id: 'b-inf',
        name: 'Anthropic',
        type: 'inference',
        provider: 'anthropic',
        endpoint: 'https://api.anthropic.com',
        config: { is_sdlc_default: true },
        secret_keys: ['api_key'],
        capacity: 4,
        enabled: true,
        access_restricted: false,
        allowed_agent_ids: [],
        health: 'healthy',
        health_checked_at: null,
        health_latency_ms: null,
        health_error: null,
        created_by: null,
        created_at: null,
        updated_at: null,
        updated_by: null,
      },
    ]

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    })
    qc.setQueryData(queryKeys.backends, computeAndInferenceOnly)
    qc.setQueryData(queryKeys.projects, [])
    qc.setQueryData(queryKeys.seeds, [])
    qc.setQueryData(queryKeys.agents, [])
    qc.setQueryData(queryKeys.policies, [])
    mockStorage.setItem('gitseed_auth_user', JSON.stringify(mockUser))

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
        React.createElement(
          I18nProvider,
          null,
          React.createElement(
            StatusProvider,
            null,
            React.createElement(
              AuthProvider,
              { initialUser: mockUser },
              React.createElement(
                ThemeProvider,
                null,
                React.createElement(
                  DataProvider,
                  null,
                  React.createElement(
                    SessionDockProvider,
                    null,
                    React.createElement(GettingStartedCard, { variant: 'guide' })
                  )
                )
              )
            )
          )
        )
      )
    )

    assert.ok(
      html.includes('On-Device Local Git'),
      'Expected GettingStartedCard to note On-Device Local Git'
    )
    assert.ok(
      html.includes('1/3'),
      'Expected Step 1 to be marked complete (1/3) so Step 2 is unlocked'
    )
  })
})

describe('ErrorBoundary & Unpriced Spend Badge', () => {
  it('renders Reload View recovery fallback when ErrorBoundary catches an error', () => {
    const okHtml = renderToStaticMarkup(
      React.createElement(
        ErrorBoundary,
        null,
        React.createElement('div', null, 'Healthy View Content')
      )
    )
    assert.ok(okHtml.includes('Healthy View Content'))

    const derived = ErrorBoundary.getDerivedStateFromError(
      new Error('Simulated route render crash')
    )
    assert.equal(derived.hasError, true)
    assert.equal(derived.error?.message, 'Simulated route render crash')

    let resetCalled = false
    const boundary = new ErrorBoundary({
      children: React.createElement('div', null, 'Healthy'),
      onReset: () => {
        resetCalled = true
      },
    })
    boundary.state = derived
    const fallbackElement = boundary.render() as React.ReactElement
    const fallbackHtml = renderToStaticMarkup(fallbackElement)

    assert.ok(fallbackHtml.includes('Reload View'))
    assert.ok(fallbackHtml.includes('Simulated route render crash'))

    // Verify handleReset invokes onReset callback
    boundary.setState = ((updater: unknown) => {
      if (typeof updater === 'object' && updater !== null) {
        boundary.state = { ...boundary.state, ...(updater as typeof boundary.state) }
      }
    }) as typeof boundary.setState
    boundary.handleReset()
    assert.equal(boundary.state.hasError, false)
    assert.equal(boundary.state.error, null)
    assert.equal(resetCalled, true)
  })

  it('renders Unpriced spend enforcement badge on SeedMetadataSidebar when seed enforcement is unpriced', () => {
    const unpricedSeed: Seed = {
      id: 'seed-unpriced',
      project_id: 'p1',
      number: 42,
      title: 'Custom Model Seed',
      description: 'Test unpriced model enforcement',
      specification: 'Spec',
      acceptance_criteria: [],
      plan: null,
      tags: [],
      stage: 'execute',
      status: 'running',
      autonomy: 'supervised',
      stage_entered_at: null,
      blocked_reason: null,
      budget_cap: '25.00',
      spent: '0.00',
      checkpoint_approved_through: '0.00',
      enforcement: 'unpriced',
      merged_at: null,
      closed_at: null,
      closed_by: null,
      created_at: new Date().toISOString(),
      created_by: 'u1',
      updated_at: new Date().toISOString(),
    }

    const qc = new QueryClient({
      defaultOptions: { queries: { retry: false, staleTime: Infinity } },
    })
    qc.setQueryData(queryKeys.backends, [])
    qc.setQueryData(queryKeys.projects, [])
    qc.setQueryData(queryKeys.seeds, [unpricedSeed])
    mockStorage.setItem('gitseed_auth_user', JSON.stringify(mockUser))

    const html = renderToStaticMarkup(
      React.createElement(
        QueryClientProvider,
        { client: qc },
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
                  React.createElement(SeedMetadataSidebar, {
                    seed: unpricedSeed,
                    project: null,
                    primaryRepo: null,
                    runs: [],
                    canContribute: true,
                    autonomyNotice: null,
                    onAutonomyChange: () => {},
                    resolveActorName: () => 'Ada',
                  })
                )
              )
            )
          )
        )
      )
    )

    assert.ok(html.includes('Unpriced'))
    assert.ok(html.includes('View Signed Receipt'))
  })
})
