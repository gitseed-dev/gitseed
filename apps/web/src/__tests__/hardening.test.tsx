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
import {
  computeSimpleDiff,
  parseUnifiedDiffLines,
} from '../components/common/MonacoCodeEditor'
import { GettingStartedCard } from '../components/common/GettingStartedCard'
import { SeedMetadataSidebar } from '../components/details/seed/SeedMetadataSidebar'
import { ErrorBoundary } from '../components/ErrorBoundary'
import {
  resolveVsCodeIframeProps,
  safeExternalUrl,
} from '../utils/gitUrls'
import { verifyGovernanceReceiptClientSide } from '../utils/receiptVerification'
import {
  mockStorage,
  mockUser,
  setupTestDom,
} from './setup'
import type {
  Backend,
  GovernanceReceiptEnvelope,
  GovernanceReceiptPayload,
  Seed,
} from '../types'

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
  it('fetches seeds with limit = 1000 (<= 1000 API max) and supplements open_only when full', async () => {
    assert.equal(SEEDS_LIST_LIMIT, 1000)
    assert.ok(
      SEEDS_LIST_LIMIT >= 1 && SEEDS_LIST_LIMIT <= 1000,
      `Expected SEEDS_LIST_LIMIT (${SEEDS_LIST_LIMIT}) to be within 1..1000`
    )

    const requestedUrls: string[] = []
    let simulateFullPage = false
    const originalFetch = globalThis.fetch
    globalThis.fetch = (async (input: RequestInfo | URL) => {
      const url = typeof input === 'string' ? input : input.toString()
      requestedUrls.push(url)
      if (simulateFullPage && url.startsWith('/api/v1/seeds?')) {
        const parsed = new URL(url, 'http://localhost:8421')
        if (parsed.searchParams.get('open_only') === 'true') {
          return new Response(
            JSON.stringify([
              {
                id: 'open-seed-1001',
                project_id: 'p1',
                number: 1,
                title: 'Older Open Seed',
                status: 'running',
                stage: 'execute',
                created_at: '2026-01-01T00:00:00Z',
              },
            ]),
            { status: 200, headers: { 'Content-Type': 'application/json' } }
          )
        }
        const closedBatch = Array.from({ length: SEEDS_LIST_LIMIT }, (_, idx) => ({
          id: `closed-${idx}`,
          project_id: 'p1',
          number: idx + 2,
          title: `Closed ${idx}`,
          status: 'merged',
          stage: 'merge',
          created_at: '2026-02-01T00:00:00Z',
        }))
        return new Response(JSON.stringify(closedBatch), {
          status: 200,
          headers: { 'Content-Type': 'application/json' },
        })
      }
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
      const queryFn = seedsQuery.options.queryFn as (() => Promise<Seed[]>) | undefined
      assert.equal(typeof queryFn, 'function')
      await queryFn!()

      const seedCall = requestedUrls.find((u) => u.startsWith('/api/v1/seeds?'))
      assert.ok(seedCall, 'Expected GET /api/v1/seeds request')
      const parsedUrl = new URL(seedCall, 'http://localhost:8421')
      const limitParam = Number(parsedUrl.searchParams.get('limit'))
      assert.equal(limitParam, 1000)
      assert.ok(limitParam <= 1000)

      // Verify crowd-out protection when global page hits SEEDS_LIST_LIMIT
      requestedUrls.length = 0
      simulateFullPage = true
      const mergedResult = await queryFn!()
      assert.equal(mergedResult.length, 1001)
      assert.ok(mergedResult.some((s) => s.id === 'open-seed-1001'))
      assert.ok(
        requestedUrls.some((u) => u.includes('open_only=true')),
        'Expected supplemental open_only=true query when global list reaches limit'
      )
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

describe('External URL Sanitization, VS Code Iframe Sandbox, LCS Diff & Ed25519 Receipt Verification', () => {
  it('sanitizes external URLs via safeExternalUrl (allows http/https, rejects javascript/data/vbscript)', () => {
    assert.equal(
      safeExternalUrl('https://github.com/org/repo/pull/42'),
      'https://github.com/org/repo/pull/42'
    )
    assert.equal(
      safeExternalUrl('  http://192.168.1.10:3000/org/repo/pulls/7  '),
      'http://192.168.1.10:3000/org/repo/pulls/7'
    )
    assert.equal(safeExternalUrl('javascript:alert(1)'), null)
    assert.equal(safeExternalUrl('  JAVASCRIPT:alert(document.cookie)  '), null)
    assert.equal(safeExternalUrl('data:text/html,<script>alert(1)</script>'), null)
    assert.equal(safeExternalUrl('vbscript:msgbox(1)'), null)
    assert.equal(safeExternalUrl('file:///etc/passwd'), null)
    assert.equal(safeExternalUrl('local://my-repo'), null)
    assert.equal(safeExternalUrl('/relative/path'), null)
    assert.equal(safeExternalUrl(''), null)
    assert.equal(safeExternalUrl(null), null)
    assert.equal(safeExternalUrl(undefined), null)
  })

  it('rewrites loopback VS Code URLs on LAN/Tailscale hosts and strips allow-same-origin when same-origin', () => {
    // 1. LAN / Tailscale host rewrites 127.0.0.1:8423 -> <lan-host>:8423 (cross-origin)
    const lanResolved = resolveVsCodeIframeProps(
      'http://127.0.0.1:8423/api/v1/vscode/tok123/?folder=/workspace',
      'http://192.168.1.50:8421'
    )
    assert.equal(
      lanResolved.url,
      'http://192.168.1.50:8423/api/v1/vscode/tok123/?folder=/workspace'
    )
    assert.ok(lanResolved.sandbox.includes('allow-scripts'))
    assert.ok(lanResolved.sandbox.includes('allow-same-origin'))

    // 2. HTTPS Tailscale host upgrades protocol + hostname while preserving dedicated port 8423
    const httpsTailscale = resolveVsCodeIframeProps(
      'http://localhost:8423/api/v1/vscode/tok456/',
      'https://devbox.tailnet.ts.net:8443'
    )
    assert.equal(
      httpsTailscale.url,
      'https://devbox.tailnet.ts.net:8423/api/v1/vscode/tok456/'
    )
    assert.ok(httpsTailscale.sandbox.includes('allow-same-origin'))

    // 3. Localhost browser preserves 127.0.0.1:8423 cross-origin isolation
    const localResolved = resolveVsCodeIframeProps(
      'http://127.0.0.1:8423/api/v1/vscode/tok789/',
      'http://localhost:8421'
    )
    assert.equal(
      localResolved.url,
      'http://127.0.0.1:8423/api/v1/vscode/tok789/'
    )
    assert.ok(localResolved.sandbox.includes('allow-same-origin'))

    // 4. Same-origin fallback omits allow-same-origin to prevent parent DOM/cookie escape
    const sameOriginResolved = resolveVsCodeIframeProps(
      '/api/v1/vscode/tok-same/',
      'http://192.168.1.50:8421'
    )
    assert.equal(
      sameOriginResolved.url,
      'http://192.168.1.50:8421/api/v1/vscode/tok-same/'
    )
    assert.ok(sameOriginResolved.sandbox.includes('allow-scripts'))
    assert.ok(!sameOriginResolved.sandbox.includes('allow-same-origin'))
  })

  it('aligns unchanged lines properly on single-line insertions and deletions in computeSimpleDiff', () => {
    const original = ['line 1', 'line 2', 'line 3', 'line 4'].join('\n')
    const insertedTop = ['inserted header', 'line 1', 'line 2', 'line 3', 'line 4'].join('\n')

    const topDiff = computeSimpleDiff(original, insertedTop)
    assert.equal(topDiff.length, 5)
    assert.deepEqual(topDiff[0], {
      type: 'add',
      oldLine: null,
      newLine: 1,
      text: 'inserted header',
    })
    assert.equal(topDiff.filter((d) => d.type === 'equal').length, 4)
    assert.deepEqual(topDiff[1], {
      type: 'equal',
      oldLine: 1,
      newLine: 2,
      text: 'line 1',
    })
    assert.deepEqual(topDiff[4], {
      type: 'equal',
      oldLine: 4,
      newLine: 5,
      text: 'line 4',
    })

    // Single-line deletion in middle
    const deletedMiddle = ['line 1', 'line 3', 'line 4'].join('\n')
    const delDiff = computeSimpleDiff(original, deletedMiddle)
    assert.equal(delDiff.length, 4)
    assert.equal(delDiff.filter((d) => d.type === 'del').length, 1)
    assert.deepEqual(delDiff[1], {
      type: 'del',
      oldLine: 2,
      newLine: null,
      text: 'line 2',
    })
    assert.deepEqual(delDiff[2], {
      type: 'equal',
      oldLine: 3,
      newLine: 2,
      text: 'line 3',
    })

    // Multi-edit alignment (edit at top and bottom with shared middle)
    const multiOld = ['alpha', 'shared A', 'shared B', 'omega'].join('\n')
    const multiNew = ['alpha-v2', 'shared A', 'shared B', 'omega-v2'].join('\n')
    const multiDiff = computeSimpleDiff(multiOld, multiNew)
    const equalTexts = multiDiff.filter((d) => d.type === 'equal').map((d) => d.text)
    assert.deepEqual(equalTexts, ['shared A', 'shared B'])
  })

  it('verifies Ed25519 governance receipts via WebCrypto and rejects tampered payloads', async () => {
    const keyPair = (await globalThis.crypto.subtle.generateKey(
      { name: 'Ed25519' },
      true,
      ['sign', 'verify']
    )) as CryptoKeyPair
    const spkiBuffer = await globalThis.crypto.subtle.exportKey(
      'spki',
      keyPair.publicKey
    )
    const b64Spki = Buffer.from(spkiBuffer).toString('base64')
    const pem = `-----BEGIN PUBLIC KEY-----\n${b64Spki}\n-----END PUBLIC KEY-----\n`

    const receiptPayload: GovernanceReceiptPayload = {
      schema_version: 'gitseed.receipt.v1',
      seed: {
        id: 'seed-1',
        number: 1,
        title: 'Test Receipt',
        project_id: 'proj-1',
        project_slug: 'demo',
        status: 'merged',
        stage: 'merge',
        branch_name: 'gitseed/demo/seed-1',
        pr_url: 'https://github.com/org/repo/pull/1',
      },
      policy: {
        autonomy: 'supervised',
        min_approver_role: 'maintainer',
        prevent_self_approval: true,
        blocked_paths: [],
      },
      artifacts: null,
      runs: [],
      approvals: [],
      generated_at: '2026-11-11T00:00:00Z',
    }
    const canonicalJson = JSON.stringify(receiptPayload)
    const sigBuffer = await globalThis.crypto.subtle.sign(
      { name: 'Ed25519' },
      keyPair.privateKey,
      new TextEncoder().encode(canonicalJson)
    )
    const signature = Buffer.from(sigBuffer)
      .toString('base64')
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/g, '')

    const envelope: GovernanceReceiptEnvelope = {
      receipt: receiptPayload,
      canonical_json: canonicalJson,
      signature,
      algorithm: 'Ed25519',
      key_id: 'abcd1234ef567890',
      public_key_pem: pem,
    }

    const okResult = await verifyGovernanceReceiptClientSide(envelope, {
      key_id: 'abcd1234ef567890',
      algorithm: 'Ed25519',
      public_key_pem: pem,
    })
    assert.equal(okResult.status, 'verified')
    assert.equal(okResult.label, 'Verified (Ed25519)')

    // Tampering with receipt field while keeping canonical_json/signature fails verification
    const tamperedReceipt: GovernanceReceiptEnvelope = {
      ...envelope,
      receipt: {
        ...receiptPayload,
        seed: { ...receiptPayload.seed, status: 'open' },
      },
    }
    const tamperedResult = await verifyGovernanceReceiptClientSide(
      tamperedReceipt,
      {
        key_id: 'abcd1234ef567890',
        algorithm: 'Ed25519',
        public_key_pem: pem,
      }
    )
    assert.equal(tamperedResult.status, 'invalid')
    assert.equal(tamperedResult.label, 'Signature Invalid / Tampered')

    // Untrusted key_id fails verification even if envelope carries its own PEM
    const untrustedKeyResult = await verifyGovernanceReceiptClientSide(envelope, {
      key_id: '9999999999999999',
      algorithm: 'Ed25519',
      public_key_pem: pem,
    })
    assert.equal(untrustedKeyResult.status, 'invalid')
  })
})
