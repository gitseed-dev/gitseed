import assert from 'node:assert/strict'
import { afterEach, describe, it } from 'node:test'
import React from 'react'
import { mockBackends, mockSkills, renderWithProviders, setupTestDom, sharedQueryClient } from './setup'
import { queryKeys } from '../api/queryClient'
import { AgentForm } from '../components/modals/agent/AgentFormSections'
import { BackendConfigTab } from '../components/modals/backend/BackendFormSections'
import type { AgentRuntime, AgentRuntimeAvailability } from '../types'

setupTestDom()

function renderAgentForm(runtime: AgentRuntime): string {
  return renderWithProviders(
    <AgentForm
      isEditing={false}
      selectedTemplate={null}
      onSelectTemplate={() => {}}
      name="Builder"
      onNameChange={() => {}}
      handle="builder"
      onHandleChange={() => {}}
      setIsHandleCustom={() => {}}
      kind="stage"
      onKindChange={() => {}}
      runtime={runtime}
      onRuntimeChange={() => {}}
      model="default"
      onModelChange={() => {}}
      instructions=""
      onInstructionsChange={() => {}}
      enabled={true}
      onEnabledChange={() => {}}
      selectedSkillIds={[]}
      onToggleSkill={() => {}}
      availableSkills={mockSkills}
      tools={[]}
      onToggleToolBackend={() => {}}
      backends={mockBackends}
      selectedBackendIds={[]}
      onToggleRestrictedBackend={() => {}}
      onNavigateToBackends={() => {}}
    />
  )
}

function renderInferenceBackend(authMode: 'api_key' | 'cli_oauth'): string {
  return renderWithProviders(
    <BackendConfigTab
      isEditing={false}
      name="Claude"
      setName={() => {}}
      type="inference"
      provider="anthropic"
      onProviderChange={() => {}}
      endpoint="https://api.anthropic.com"
      setEndpoint={() => {}}
      secrets={{}}
      onSecretChange={() => {}}
      existingSecretKeys={[]}
      commandArgs=""
      setCommandArgs={() => {}}
      appId=""
      setAppId={() => {}}
      installationId=""
      setInstallationId={() => {}}
      enabled={true}
      setEnabled={() => {}}
      authMode={authMode}
      onAuthModeChange={() => {}}
    />
  )
}

describe('Runtime availability badges (AgentForm)', () => {
  afterEach(() => {
    sharedQueryClient.removeQueries({ queryKey: queryKeys.runtimeAvailability })
  })

  it('marks runtimes missing from the runner image and warns when one is selected', () => {
    const availability: AgentRuntimeAvailability = {
      available: {
        opencode: true,
        codex: true,
        gemini: true,
        'claude-code': true,
        antigravity: false,
        'grok-build': false,
      },
      source: 'probe',
      image: 'gitseed/runner:dev',
    }
    sharedQueryClient.setQueryData(queryKeys.runtimeAvailability, availability)

    const html = renderAgentForm('grok-build')
    assert.equal(html.split('Not installed').length - 1, 2)
    assert.ok(html.includes('runtime-not-installed-warning'))
    assert.ok(html.includes('gitseed/runner:dev'))

    const installedHtml = renderAgentForm('opencode')
    assert.ok(!installedHtml.includes('runtime-not-installed-warning'))
  })

  it('shows no badges when availability is unknown', () => {
    sharedQueryClient.setQueryData(queryKeys.runtimeAvailability, {
      available: null,
      source: 'unknown',
      image: null,
    } satisfies AgentRuntimeAvailability)
    const html = renderAgentForm('grok-build')
    assert.ok(!html.includes('Not installed'))
    assert.ok(!html.includes('runtime-not-installed-warning'))
  })
})

describe('Subscription login terms warning (BackendConfigTab)', () => {
  it('warns against sharing consumer subscriptions only in CLI login mode', () => {
    const oauthHtml = renderInferenceBackend('cli_oauth')
    assert.ok(oauthHtml.includes('subscription-terms-warning'))
    assert.ok(oauthHtml.includes('Consumer subscriptions are for one person'))
    assert.ok(oauthHtml.includes('Personal Mode (Single-User Subscription)'))
    assert.ok(oauthHtml.includes('personal-mode-acknowledged-checkbox'))

    const apiKeyHtml = renderInferenceBackend('api_key')
    assert.ok(!apiKeyHtml.includes('subscription-terms-warning'))
  })
})

