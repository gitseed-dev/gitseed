import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import React from 'react'
import { renderToStaticMarkup } from 'react-dom/server'
import {
  mockActivity,
  mockAgents,
  mockBackends,
  mockProjects,
  mockRuns,
  mockSeeds,
  mockUser,
  mockUserInvited,
  renderWithProviders,
  setupTestDom,
} from './setup'
import { parseActivityEvents } from '../utils/activity'
import type { AgentRuntime } from '../types'

// Common components
import { ActionMenu } from '../components/common/ActionMenu'
import { ActivityRow } from '../components/common/ActivityRow'
import { AgentsWorkingCard } from '../components/common/AgentsWorkingCard'
import { ConfirmDialog } from '../components/common/ConfirmDialog'
import { DisconnectedBanner } from '../components/common/DisconnectedBanner'
import { ErrorBoundary } from '../components/common/ErrorBoundary'
import { GettingStartedCard } from '../components/common/GettingStartedCard'
import { MarkdownContent } from '../components/common/MarkdownContent'
import { Modal } from '../components/common/Modal'
import { OperatorReviewPanel } from '../components/common/OperatorReviewPanel'
import { PageBreadcrumb } from '../components/common/PageBreadcrumb'
import { Pagination } from '../components/common/Pagination'
import { ProviderIcon, RuntimeIcon } from '../components/common/ProviderIcon'
import { RecentActivityCard } from '../components/common/RecentActivityCard'
import { SdlcPipelineStrip } from '../components/common/SdlcPipelineStrip'
import { SdlcStreamAnimation } from '../components/common/SdlcStreamAnimation'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { SpendTrendCard } from '../components/common/SpendTrendCard'
import { StageBadge } from '../components/common/StageBadge'
import { SteppedSlider } from '../components/common/SteppedSlider'
import { ToggleSwitch } from '../components/common/ToggleSwitch'
import { Tooltip } from '../components/common/Tooltip'
import { VoiceDictationButton } from '../components/common/VoiceDictationButton'

// Layout components
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
  OrgScopeBanner,
  SortableTh,
} from '../components/layout/EntityPageLayout'
import { Sidebar } from '../components/layout/Sidebar'

// Settings components
import { AppearanceCanvasCard } from '../components/settings/AppearanceCanvasCard'
import { AutonomyDefaultsCard } from '../components/settings/AutonomyDefaultsCard'
import { InstanceOverviewCard } from '../components/settings/InstanceOverviewCard'
import { RunnersConcurrencyCard } from '../components/settings/RunnersConcurrencyCard'
import { SecurityIsolationCard } from '../components/settings/SecurityIsolationCard'
import { SpendControlsCard } from '../components/settings/SpendControlsCard'

setupTestDom()

describe('Common Components (src/components/common/*)', () => {
  it('renders StageBadge, ToggleSwitch, SteppedSlider, SegmentedControl, Pagination', () => {
    const html = renderWithProviders(
      <div>
        <StageBadge stage="ideate" />
        <StageBadge stage="plan" />
        <StageBadge stage="execute" />
        <StageBadge stage="review" />
        <StageBadge stage="merge" />
        <StageBadge stage="merge" isClosed />
        <ToggleSwitch
          checked={true}
          onChange={() => {}}
          label="Enable feature"
          size="sm"
          color="teal"
        />
        <ToggleSwitch
          checked={false}
          onChange={() => {}}
          disabled
          size="md"
          color="indigo"
        />
        <ToggleSwitch checked={true} onChange={() => {}} color="emerald" />
        <SteppedSlider<'manual' | 'supervised' | 'autonomous'>
          title="Ideate Stage"
          description="Brainstorm specification"
          value="supervised"
          steps={[
            {
              value: 'manual',
              label: 'Manual',
              icon: <span>M</span>,
              headerIcon: <span>M</span>,
              chipColor: 'default',
              thumbActiveClass: 'bg-zinc-500',
              labelActiveClass: 'text-zinc-900',
            },
            {
              value: 'supervised',
              label: 'Supervised',
              icon: <span>S</span>,
              headerIcon: <span>S</span>,
              chipColor: 'accent',
              thumbActiveClass: 'bg-indigo-500',
              labelActiveClass: 'text-indigo-600',
            },
            {
              value: 'autonomous',
              label: 'Autonomous',
              icon: <span>A</span>,
              headerIcon: <span>A</span>,
              chipColor: 'success',
              thumbActiveClass: 'bg-emerald-500',
              labelActiveClass: 'text-emerald-600',
            },
          ]}
          onChange={() => {}}
          trackGradientClass="from-teal-500 to-emerald-500"
        />
        <SegmentedControl
          selectedId="all"
          onSelect={() => {}}
          items={[
            { id: 'all', label: 'All', count: 12 },
            { id: 'active', label: 'Active', count: 4 },
          ]}
        />
        <Pagination
          page={2}
          pageSize={10}
          totalItems={95}
          onPageChange={() => {}}
          onPageSizeChange={() => {}}
        />
        <Pagination
          page={1}
          pageSize={10}
          totalItems={25}
          onPageChange={() => {}}
          compact
        />
      </div>
    )
    assert.ok(html.includes('Ideate'))
    assert.ok(html.includes('Merge'))
    assert.ok(html.includes('Ideate Stage'))
    assert.ok(html.includes('95'))
  })

  it('renders ProviderIcon and RuntimeIcon for all providers and runtimes', () => {
    const providers = [
      'github',
      'gitlab',
      'bitbucket',
      'anthropic',
      'openai',
      'antigravity',
      'gemini',
      'grok',
      'docker',
      'kubernetes',
      'litellm',
      'ollama',
      'unknown',
    ]
    const types: Array<'git' | 'inference' | 'compute' | 'tools'> = [
      'git',
      'inference',
      'compute',
      'tools',
    ]
    const runtimes: AgentRuntime[] = [
      'opencode',
      'claude-code',
      'codex',
      'antigravity',
      'gemini',
      'grok-build',
    ]

    const html = renderToStaticMarkup(
      <div>
        {providers.map((p, idx) => (
          <ProviderIcon
            key={p}
            provider={p}
            type={types[idx % types.length]}
            size={16}
          />
        ))}
        {runtimes.map((rt) => (
          <RuntimeIcon key={rt} runtime={rt} size={16} />
        ))}
      </div>
    )
    assert.ok(html.includes('<svg'))
  })

  it('renders MarkdownContent across headings, lists, tables, blockquotes, and code blocks', () => {
    const sampleMd = [
      '# Heading 1',
      '## Heading 2',
      '### Heading 3',
      '#### Heading 4',
      '',
      '> Important architectural note with **bold**, *italic*, `inlineCode()`, @architect, and [Docs](https://gitseed.dev).',
      '',
      '- Bullet one',
      '- [x] Completed task item',
      '- [ ] Pending task item',
      '',
      '1. First step',
      '2. Second step',
      '',
      '| Stage | Agent | Budget |',
      '| :--- | :---: | ---: |',
      '| Ideate | @architect | $5.00 |',
      '| Execute | @builder | $15.00 |',
      '',
      '---',
      '',
      '```typescript',
      'export const answer = 42;',
      '```',
    ].join('\n')

    const html = renderWithProviders(<MarkdownContent content={sampleMd} />)
    assert.ok(html.includes('Heading 1'))
    assert.ok(html.includes('inlineCode()'))
    assert.ok(html.includes('Completed task item'))
    assert.ok(html.includes('answer'))
    assert.ok(html.includes('<table'))
  })

  it('renders Modal, ConfirmDialog, DisconnectedBanner, ErrorBoundary, Tooltip, ActionMenu, VoiceDictationButton, PageBreadcrumb, SdlcStreamAnimation', () => {
    const html = renderWithProviders(
      <div>
        <Modal isOpen={false} onClose={() => {}} title="Hidden Modal">
          <div>Body</div>
        </Modal>
        <ConfirmDialog
          isOpen={false}
          title="Confirm Delete"
          message="Are you sure?"
          onConfirm={() => {}}
          onCancel={() => {}}
        />
        <DisconnectedBanner />
        <ErrorBoundary>
          <div>Safe child content</div>
        </ErrorBoundary>
        <Tooltip content="Helpful tip" placement="top">
          <button type="button">Hover me</button>
        </Tooltip>
        <ActionMenu
          items={[
            { label: 'Edit Item', onClick: () => {} },
            { label: 'Delete Item', variant: 'danger', onClick: () => {} },
          ]}
        />
        <VoiceDictationButton onTranscript={() => {}} />
        <PageBreadcrumb
          currentLabel="Seed #101"
          items={[{ label: 'Seeds', onClick: () => {} }]}
        />
        <SdlcStreamAnimation />
      </div>
    )
    assert.ok(html.includes('Safe child content'))
    assert.ok(html.includes('Seed #101'))
  })

  it('renders dashboard and pipeline cards (SdlcPipelineStrip, SpendTrendCard, RecentActivityCard, AgentsWorkingCard, GettingStartedCard, OperatorReviewPanel, ActivityRow)', () => {
    const parsedEvents = parseActivityEvents(mockActivity, {
      seeds: mockSeeds,
      projects: mockProjects,
      users: [mockUser, mockUserInvited],
      agents: mockAgents,
      backends: mockBackends,
    })

    const html = renderWithProviders(
      <div>
        <SdlcPipelineStrip
          seeds={mockSeeds}
          selectedStage="review"
          onSelectStage={() => {}}
        />
        <SpendTrendCard
          seeds={mockSeeds}
          runs={mockRuns}
          monthlyBudgetUsd={500}
          showSeedBreakdown={true}
          onConfigureBudget={() => {}}
          onSelectSeed={() => {}}
        />
        <RecentActivityCard
          events={mockActivity}
          onViewAll={() => {}}
          onSelectSeed={() => {}}
        />
        <AgentsWorkingCard
          seeds={mockSeeds}
          onSelectSeed={() => {}}
          onViewAll={() => {}}
        />
        <GettingStartedCard variant="guide" onNewSeed={() => {}} />
        <OperatorReviewPanel
          seeds={mockSeeds}
          onViewAll={() => {}}
          onSelectSeed={() => {}}
        />
        {parsedEvents[0] && (
          <ActivityRow
            item={parsedEvents[0]}
            onNavigateSeed={() => {}}
            showProject={true}
          />
        )}
      </div>
    )
    assert.ok(html.includes('Implement OAuth2 PKCE flow'))
    assert.ok(html.includes('Fix rate limiter memory leak'))
  })
})

describe('Layout Components (src/components/layout/*)', () => {
  it('renders OrgScopeBanner, EntityPageLayout, EntityTableCard, SortableTh, and Sidebar', () => {
    const html = renderWithProviders(
      <div>
        <OrgScopeBanner title="Backends" />
        <EntityPageLayout
          title="Seeds"
          subtitle="Track autonomous work items"
          count={2}
          storageKey="test-seeds-view"
          searchQuery="oauth"
          onSearchChange={() => {}}
          primaryAction={{ label: 'New Seed', onClick: () => {} }}
          isEmpty={false}
          renderBoard={() => <div>Board View</div>}
          renderList={() => (
            <EntityTableCard>
              <EntityTableHeaderRow>
                <SortableTh
                  sortKey="title"
                  currentSortKey="title"
                  currentSortDirection="asc"
                  onSort={() => {}}
                >
                  Title
                </SortableTh>
              </EntityTableHeaderRow>
              <tbody>
                <tr>
                  <td>Implement OAuth2 PKCE flow</td>
                </tr>
              </tbody>
            </EntityTableCard>
          )}
          pagination={{
            page: 1,
            pageSize: 10,
            totalItems: 25,
            onPageChange: () => {},
          }}
        />
        <Sidebar />
      </div>
    )
    assert.ok(html.includes('Track autonomous work items'))
    assert.ok(html.includes('Implement OAuth2 PKCE flow'))
  })
})

describe('Settings Components (src/components/settings/*)', () => {
  it('renders all 6 settings cards', () => {
    const html = renderWithProviders(
      <div>
        <AppearanceCanvasCard
          themeMode="dark"
          onChangeThemeMode={() => {}}
          ambientBg={true}
          onChangeAmbientBg={() => {}}
          showGuideInSidebar={true}
          onChangeShowGuideInSidebar={() => {}}
        />
        <AutonomyDefaultsCard
          defaultAutonomy="supervised"
          onChangeAutonomy={() => {}}
          onConfigurePolicies={() => {}}
        />
        <InstanceOverviewCard
          version="0.1.0"
          environment="production"
          database="connected"
          redis="connected"
        />
        <RunnersConcurrencyCard
          maxConcurrentRunners={8}
          onChangeMaxConcurrentRunners={() => {}}
          maxSandboxSessions={4}
          onChangeMaxSandboxSessions={() => {}}
          defaultProjectConcurrency={2}
          onChangeDefaultProjectConcurrency={() => {}}
          queueStrategy="fair_share"
          onChangeQueueStrategy={() => {}}
          onManageBackends={() => {}}
        />
        <SecurityIsolationCard
          patLifetimeDays={90}
          onChangePatLifetimeDays={() => {}}
        />
        <SpendControlsCard
          defaultMonthlyBudget={500}
          onChangeMonthlyBudget={() => {}}
          seedBudgetCap="25.00"
          onChangeSeedBudgetCap={() => {}}
          spendCheckpoint="10.00"
          onChangeSpendCheckpoint={() => {}}
          isCheckpointMissingWhenUnbounded={false}
        />
      </div>
    )
    assert.ok(html.includes('0.1.0'))
    assert.ok(html.includes('500'))
  })
})
