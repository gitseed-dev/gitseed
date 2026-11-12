import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import { buildPath, parsePath, VIEWS } from '../constants'
import type { Agent, Seed, User } from '../types'
import {
  detectLanguageFromPath,
  normalizeCodeLanguage,
} from '../utils/codeLanguage'
import {
  formatActorLabel,
  formatBytes,
  formatDurationMs,
  formatRelativeTime,
  formatTokens,
  formatUsd,
  formatVersionLabel,
  getSeedAttentionReason,
  seedNeedsAttention,
} from '../utils/format'
import {
  buildGitWebUrl,
  buildNewRepoWebUrl,
  buildRepoCloneUrls,
  buildRepoWebUrl,
  resolveGitWebBase,
} from '../utils/gitUrls'

function makeSeed(overrides: Partial<Seed> = {}): Seed {
  return {
    id: 'seed-1',
    project_id: 'proj-1',
    number: 1,
    title: 'Test Seed',
    description: 'Description',
    specification: 'Spec',
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
    created_by: 'user-1',
    updated_at: null,
    ...overrides,
  }
}

describe('utils/format', () => {
  it('formats USD amounts accurately', () => {
    assert.equal(formatUsd(12.5), '$12.50')
    assert.equal(formatUsd('42.198'), '$42.20')
    assert.equal(formatUsd(0), '$0.00')
    assert.equal(formatUsd(null), '$0.00')
    assert.equal(formatUsd(undefined), '$0.00')
    assert.equal(formatUsd('not-a-number'), '$0.00')
  })

  it('formats token counts and durations in milliseconds', () => {
    assert.equal(formatTokens(null), '0')
    assert.equal(formatTokens(undefined), '0')
    assert.equal(formatTokens(0), '0')
    assert.equal(formatTokens(450), '450')
    assert.equal(formatTokens(1500), '1.5k')
    assert.equal(formatTokens(2000), '2k')
    assert.equal(formatTokens(1_250_000), '1.25M')

    assert.equal(formatDurationMs(null), '-')
    assert.equal(formatDurationMs(undefined), '-')
    assert.equal(formatDurationMs(-5), '-')
    assert.equal(formatDurationMs(250), '250ms')
    assert.equal(formatDurationMs(45_000), '45s')
    assert.equal(formatDurationMs(125_000), '2m 5s')
    assert.equal(formatDurationMs(120_000), '2m')
    assert.equal(formatDurationMs(3_900_000), '1h 5m')
  })

  it('formats byte sizes across B, KB, MB, and nullish inputs', () => {
    assert.equal(formatBytes(null), '-')
    assert.equal(formatBytes(undefined), '-')
    assert.equal(formatBytes(Number.NaN), '-')
    assert.equal(formatBytes(0), '0 B')
    assert.equal(formatBytes(-10), '0 B')
    assert.equal(formatBytes(512), '512 B')
    assert.equal(formatBytes(1024), '1.0 KB')
    assert.equal(formatBytes(1536), '1.5 KB')
    assert.equal(formatBytes(1024 * 1024), '1.00 MB')
    assert.equal(formatBytes(2.5 * 1024 * 1024), '2.50 MB')
  })

  it('formats relative time strings across seconds, minutes, hours, and days', () => {
    assert.equal(formatRelativeTime(null), 'just now')
    assert.equal(formatRelativeTime(undefined), 'just now')

    const now = Date.now()
    assert.equal(
      formatRelativeTime(new Date(now - 15 * 1000).toISOString()),
      'just now'
    )
    assert.equal(
      formatRelativeTime(new Date(now + 60 * 1000).toISOString()),
      'just now'
    )
    assert.equal(
      formatRelativeTime(new Date(now - 5 * 60 * 1000).toISOString()),
      '5m ago'
    )
    assert.equal(
      formatRelativeTime(new Date(now - 3 * 3600 * 1000).toISOString()),
      '3h ago'
    )
    assert.equal(
      formatRelativeTime(new Date(now - 4 * 86400 * 1000).toISOString()),
      '4d ago'
    )
  })

  it('normalizes version labels', () => {
    assert.equal(formatVersionLabel('0.2.1'), 'v0.2.1')
    assert.equal(formatVersionLabel('v1.0.0'), 'v1.0.0')
    assert.equal(formatVersionLabel('dev'), 'dev')
    assert.equal(formatVersionLabel('DEVELOPMENT'), 'dev')
    assert.equal(formatVersionLabel(null), 'v0.1.0')
  })

  it('determines seed attention status and reason', () => {
    const running = makeSeed({ status: 'running', stage: 'execute' })
    assert.equal(seedNeedsAttention(running), false)
    assert.equal(getSeedAttentionReason(running), 'Status: running')

    const awaitingPlan = makeSeed({ status: 'awaiting_approval', stage: 'plan' })
    assert.equal(seedNeedsAttention(awaitingPlan), true)
    assert.equal(getSeedAttentionReason(awaitingPlan), 'Awaiting plan approval')

    const awaitingExec = makeSeed({
      status: 'awaiting_approval',
      stage: 'execute',
    })
    assert.equal(
      getSeedAttentionReason(awaitingExec),
      'Awaiting execution approval'
    )

    const awaitingMerge = makeSeed({
      status: 'awaiting_approval',
      stage: 'review',
    })
    assert.equal(
      getSeedAttentionReason(awaitingMerge),
      'Awaiting merge approval'
    )

    const awaitingIdea = makeSeed({
      status: 'awaiting_approval',
      stage: 'ideate',
    })
    assert.equal(getSeedAttentionReason(awaitingIdea), 'Awaiting idea approval')

    const blockedCustom = makeSeed({
      status: 'blocked',
      blocked_reason: 'Spend checkpoint reached',
    })
    assert.equal(seedNeedsAttention(blockedCustom), true)
    assert.equal(
      getSeedAttentionReason(blockedCustom),
      'Spend checkpoint reached'
    )

    const blockedDefault = makeSeed({ status: 'blocked', blocked_reason: null })
    assert.equal(
      getSeedAttentionReason(blockedDefault),
      'Blocked · Operator action required'
    )

    const failed = makeSeed({ status: 'failed' })
    assert.equal(seedNeedsAttention(failed), true)
    assert.equal(
      getSeedAttentionReason(failed),
      'Run failed · Needs investigation'
    )
  })

  it('formats actor labels for system, agents, and users', () => {
    const users: User[] = [
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
      },
    ]
    const agents: Agent[] = [
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
      },
    ]

    assert.equal(
      formatActorLabel({ actor_type: 'system', actor_id: null }),
      'System'
    )
    assert.equal(
      formatActorLabel({ actor_type: 'agent', actor_id: 'ag-1' }, { agents }),
      '@coder'
    )
    assert.equal(
      formatActorLabel({ actor_type: 'agent', actor_id: 'ag-unknown' }, { agents }),
      'ag-unknown'
    )
    assert.equal(
      formatActorLabel({ actor_type: 'user', actor_id: 'u-1' }, { users }),
      'Ada'
    )
    assert.equal(
      formatActorLabel({ actor_type: 'user', actor_id: null }, { users }),
      'User'
    )
  })
})

describe('utils/gitUrls', () => {
  it('resolves web base URLs across GitHub, GitLab, Bitbucket, and self-hosted endpoints', () => {
    assert.equal(resolveGitWebBase('github', 'https://api.github.com'), 'https://github.com')
    assert.equal(
      resolveGitWebBase('github-enterprise', 'https://ghe.internal.corp/api/v3/'),
      'https://ghe.internal.corp'
    )
    assert.equal(resolveGitWebBase('gitlab', 'https://gitlab.com/api/v4'), 'https://gitlab.com')
    assert.equal(
      resolveGitWebBase('gitlab', 'https://gitlab.internal.corp/api/v4'),
      'https://gitlab.internal.corp'
    )
    assert.equal(
      resolveGitWebBase('bitbucket', 'https://api.bitbucket.org/2.0'),
      'https://bitbucket.org'
    )
    assert.equal(
      resolveGitWebBase('git', 'https://gitea.internal.corp/'),
      'https://gitea.internal.corp'
    )
  })

  it('builds repository web URLs for branches, paths, and commits across providers', () => {
    const repo = { full_name: 'acme/platform', default_branch: 'main' }

    // GitHub
    assert.equal(
      buildRepoWebUrl(repo, { provider: 'github', endpoint: 'https://api.github.com' }),
      'https://github.com/acme/platform'
    )
    assert.equal(
      buildRepoWebUrl(
        repo,
        { provider: 'github', endpoint: 'https://api.github.com' },
        { ref: 'feature/auth', path: 'src/index.ts', isDir: false }
      ),
      'https://github.com/acme/platform/blob/feature/auth/src/index.ts'
    )
    assert.equal(
      buildRepoWebUrl(
        repo,
        { provider: 'github', endpoint: 'https://api.github.com' },
        { commitSha: 'abc1234' }
      ),
      'https://github.com/acme/platform/commit/abc1234'
    )

    // GitLab
    assert.equal(
      buildRepoWebUrl(
        repo,
        { provider: 'gitlab', endpoint: 'https://gitlab.com/api/v4' },
        { ref: 'main', path: 'apps/web', isDir: true }
      ),
      'https://gitlab.com/acme/platform/-/tree/main/apps/web'
    )
    assert.equal(
      buildRepoWebUrl(
        repo,
        { provider: 'gitlab', endpoint: 'https://gitlab.com/api/v4' },
        { commitSha: 'def5678' }
      ),
      'https://gitlab.com/acme/platform/-/commit/def5678'
    )

    // Bitbucket
    assert.equal(
      buildRepoWebUrl(
        repo,
        { provider: 'bitbucket', endpoint: 'https://api.bitbucket.org/2.0' },
        { ref: 'main', path: 'README.md', isDir: false }
      ),
      'https://bitbucket.org/acme/platform/src/main/README.md'
    )
    assert.equal(
      buildRepoWebUrl(
        repo,
        { provider: 'bitbucket', endpoint: 'https://api.bitbucket.org/2.0' },
        { commitSha: '789abcd' }
      ),
      'https://bitbucket.org/acme/platform/commits/789abcd'
    )

    // Self-hosted / Gitea
    assert.equal(
      buildGitWebUrl({
        provider: 'git',
        endpoint: 'https://git.example.org',
        fullName: 'org/service',
        ref: 'main',
      }),
      'https://git.example.org/org/service/tree/main'
    )

    // New repo URLs
    assert.equal(buildNewRepoWebUrl('github', 'https://api.github.com'), 'https://github.com/new')
    assert.equal(
      buildNewRepoWebUrl('gitlab', 'https://gitlab.com/api/v4'),
      'https://gitlab.com/projects/new'
    )
    assert.equal(
      buildNewRepoWebUrl('bitbucket', 'https://api.bitbucket.org/2.0'),
      'https://bitbucket.org/repo/create'
    )
  })

  it('builds clone URLs and git CLI commands for default and non-default branches', () => {
    const repo = { full_name: 'acme/platform', default_branch: 'main' }
    const githubBackend = { provider: 'github', endpoint: 'https://api.github.com' }

    const defaultClone = buildRepoCloneUrls(repo, githubBackend, 'main')
    assert.equal(defaultClone.httpsUrl, 'https://github.com/acme/platform.git')
    assert.equal(defaultClone.sshUrl, 'git@github.com:acme/platform.git')
    assert.equal(
      defaultClone.gitCliCommand,
      'git clone https://github.com/acme/platform.git'
    )

    const branchClone = buildRepoCloneUrls(
      repo,
      { provider: 'gitlab', endpoint: 'https://gitlab.example.com/api/v4' },
      'seed/42-oauth'
    )
    assert.equal(
      branchClone.httpsUrl,
      'https://gitlab.example.com/acme/platform.git'
    )
    assert.equal(branchClone.sshUrl, 'git@gitlab.example.com:acme/platform.git')
    assert.equal(
      branchClone.gitCliCommand,
      'git clone -b seed/42-oauth https://gitlab.example.com/acme/platform.git'
    )
  })
})

describe('utils/codeLanguage & constants/index', () => {
  it('detects syntax highlighting language from file paths and special filenames', () => {
    assert.equal(detectLanguageFromPath('Dockerfile'), 'docker')
    assert.equal(detectLanguageFromPath('infra/Dockerfile.runner'), 'docker')
    assert.equal(detectLanguageFromPath('build/app.dockerfile'), 'docker')
    assert.equal(detectLanguageFromPath('Makefile'), 'makefile')
    assert.equal(detectLanguageFromPath('GNUmakefile'), 'makefile')
    assert.equal(detectLanguageFromPath('.env.example'), 'bash')
    assert.equal(detectLanguageFromPath('.gitignore'), 'ini')
    assert.equal(detectLanguageFromPath('Cargo.lock'), 'toml')
    assert.equal(detectLanguageFromPath('Gemfile'), 'ruby')
    assert.equal(detectLanguageFromPath('services/api/app/main.py'), 'python')
    assert.equal(detectLanguageFromPath('apps/web/src/App.tsx'), 'tsx')
    assert.equal(detectLanguageFromPath('apps/web/src/index.ts'), 'typescript')
    assert.equal(detectLanguageFromPath('README.md'), 'markdown')
    assert.equal(detectLanguageFromPath('unknown_file_without_ext'), 'plain')
    assert.equal(detectLanguageFromPath(null), 'plain')
  })

  it('normalizes language aliases', () => {
    assert.equal(normalizeCodeLanguage('Python3'), 'python')
    assert.equal(normalizeCodeLanguage('sh'), 'bash')
    assert.equal(normalizeCodeLanguage('terraform'), 'hcl')
    assert.equal(normalizeCodeLanguage('c++'), 'cpp')
    assert.equal(normalizeCodeLanguage(''), 'plain')
    assert.equal(normalizeCodeLanguage(undefined), 'plain')
  })

  it('parses and builds navigation paths', () => {
    assert.deepEqual(parsePath('/'), {
      view: VIEWS.DASHBOARD,
      detailId: null,
      subTab: null,
    })
    assert.deepEqual(parsePath('/seeds/seed-42/changes'), {
      view: VIEWS.SEEDS,
      detailId: 'seed-42',
      subTab: 'changes',
    })
    assert.deepEqual(parsePath('/login'), {
      view: null,
      detailId: null,
      subTab: null,
    })
    assert.equal(buildPath(VIEWS.SEEDS, 'seed-42', 'runs'), '/seeds/seed-42/runs')
    assert.equal(buildPath(VIEWS.PROJECTS, 'proj-1', 'overview'), '/projects/proj-1')
  })
})

import '../utils/__tests__/gitUrls.test'

