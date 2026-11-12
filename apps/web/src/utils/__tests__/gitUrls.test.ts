/// <reference types="node" />
import assert from 'node:assert/strict'
import { describe, it } from 'node:test'

import {
  buildGitWebUrl,
  buildNewRepoWebUrl,
  buildRepoCloneUrls,
  buildRepoWebUrl,
  resolveGitWebBase,
} from '../gitUrls'

describe('utils/gitUrls hardening', () => {
  it('rejects javascript:, data:, file:, and malformed endpoints in resolveGitWebBase', () => {
    assert.equal(resolveGitWebBase('github', 'javascript:alert(1)'), null)
    assert.equal(resolveGitWebBase('github-enterprise', 'javascript:alert(document.cookie)'), null)
    assert.equal(resolveGitWebBase('github-app', 'javascript:void(0)'), null)
    assert.equal(resolveGitWebBase('gitlab', 'data:text/html,<script>alert(1)</script>'), null)
    assert.equal(resolveGitWebBase('bitbucket', 'vbscript:msgbox(1)'), null)
    assert.equal(resolveGitWebBase('git', 'file:///etc/passwd'), null)
    assert.equal(resolveGitWebBase('github', 'not-a-valid-url'), null)
  })

  it('returns null for local provider in resolveGitWebBase and buildNewRepoWebUrl', () => {
    assert.equal(resolveGitWebBase('local', ''), null)
    assert.equal(resolveGitWebBase('local', 'https://github.com'), null)
    assert.equal(resolveGitWebBase(' LOCAL ', null), null)
    assert.equal(buildNewRepoWebUrl('local', ''), null)
    assert.equal(buildNewRepoWebUrl('github-enterprise', 'javascript:alert(1)'), null)
  })

  it('rejects javascript: endpoints and local/ repositories in buildRepoWebUrl and buildGitWebUrl', () => {
    const normalRepo = { full_name: 'acme/platform', default_branch: 'main' }
    const localRepo = { full_name: 'local/my-workspace', default_branch: 'main' }

    assert.equal(
      buildRepoWebUrl(normalRepo, {
        provider: 'github-enterprise',
        endpoint: 'javascript:alert(1)',
      }),
      null
    )
    assert.equal(
      buildRepoWebUrl(
        normalRepo,
        { provider: 'gitlab', endpoint: 'data:text/html,xss' },
        { ref: 'main', path: 'README.md', isDir: false }
      ),
      null
    )
    assert.equal(
      buildRepoWebUrl(normalRepo, { provider: 'local', endpoint: '' }),
      null
    )
    assert.equal(
      buildRepoWebUrl(localRepo, {
        provider: 'github',
        endpoint: 'https://api.github.com',
      }),
      null
    )
    assert.equal(
      buildRepoWebUrl(localRepo, null, { ref: 'main' }),
      null
    )
    assert.equal(
      buildGitWebUrl({
        provider: 'github',
        endpoint: 'https://api.github.com',
        fullName: 'local/project-alpha',
        ref: 'main',
      }),
      null
    )
  })

  it('returns empty clone URLs for local repositories or unsafe endpoints', () => {
    assert.deepEqual(
      buildRepoCloneUrls(
        { full_name: 'local/my-workspace', default_branch: 'main' },
        { provider: 'local', endpoint: '' },
        'main'
      ),
      { httpsUrl: '', sshUrl: '', gitCliCommand: '' }
    )
    assert.deepEqual(
      buildRepoCloneUrls(
        { full_name: 'acme/platform', default_branch: 'main' },
        { provider: 'github-enterprise', endpoint: 'javascript:alert(1)' },
        'main'
      ),
      { httpsUrl: '', sshUrl: '', gitCliCommand: '' }
    )
  })
})
