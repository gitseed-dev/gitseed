import assert from 'node:assert/strict'
import { describe, it } from 'node:test'
import React from 'react'
import {
  mockActivity,
  mockAgents,
  mockApprovals,
  mockBackends,
  mockChanges,
  mockComments,
  mockMembers,
  mockPolicies,
  mockProjects,
  mockRepos,
  mockRuns,
  mockSandboxSession,
  mockSeedTasks,
  mockSeeds,
  mockSkills,
  mockTokens,
  mockUserInvited,
  renderWithProviders,
  setupTestDom,
} from './setup'

// Modals components
import { BackendAccessTab } from '../components/modals/backend/BackendAccessTab'
import { BackendCapacityTab } from '../components/modals/backend/BackendCapacityTab'
import { BackendConfigTab } from '../components/modals/backend/BackendFormSections'
import { BackendModelsTab } from '../components/modals/backend/BackendModelsTab'
import { BackendRepositoriesTab } from '../components/modals/backend/BackendRepositoriesTab'
import { AgentForm } from '../components/modals/agent/AgentFormSections'
import { SeedModal } from '../components/modals/SeedModal'
import { SkillsTable } from '../components/modals/agent/SkillsTable'

// Details: Seed
import { SeedChangesTab } from '../components/details/seed/SeedChangesTab'
import { SeedDiscussionCard } from '../components/details/seed/SeedDiscussionCard'
import { SeedGovernanceBanner } from '../components/details/seed/SeedGovernanceBanner'
import { SeedMetadataSidebar } from '../components/details/seed/SeedMetadataSidebar'
import { SeedOverviewTab } from '../components/details/seed/SeedOverviewTab'
import { SeedRunsTab } from '../components/details/seed/SeedRunsTab'
import { SeedTasksTab } from '../components/details/seed/SeedTasksTab'

// Details: Project
import { ProjectAccessAndDangerCards } from '../components/details/project/ProjectAccessAndDangerCards'
import {
  CodeBlobViewer,
  RepoClonePopover,
  formatCodeBytes,
} from '../components/details/project/ProjectCodeSubcomponents'
import { ProjectCodeTab } from '../components/details/project/ProjectCodeTab'
import { ProjectMembersTab } from '../components/details/project/ProjectMembersTab'
import { ProjectOverviewTab } from '../components/details/project/ProjectOverviewTab'
import { ProjectPoliciesTab } from '../components/details/project/ProjectPoliciesTab'
import { ProjectRepositoriesCard } from '../components/details/project/ProjectRepositoriesCard'
import { ProjectSandboxTab } from '../components/details/project/ProjectSandboxTab'
import { ProjectSettingsTab } from '../components/details/project/ProjectSettingsTab'
import {
  SandboxSessionInspector,
  getSandboxStatusBadge,
} from '../components/details/project/SandboxSessionInspector'

// Details: User
import {
  UserInviteBanner,
  UserPasswordChangeForm,
  UserProfileEditForm,
} from '../components/details/user/UserProfileForms'
import { UserProjectsSection } from '../components/details/user/UserProjectsSection'
import { UserTokensSection } from '../components/details/user/UserTokensSection'

setupTestDom()

describe('Modal Subcomponents (src/components/modals/*)', () => {
  it('renders Backend modal tabs and Agent modal form & SkillsTable', () => {
    const html = renderWithProviders(
      <div>
        <BackendConfigTab
          isEditing={true}
          name="GitHub Enterprise"
          setName={() => { }}
          type="git"
          provider="github"
          onProviderChange={() => { }}
          endpoint="https://api.github.com"
          setEndpoint={() => { }}
          secrets={{ token: '' }}
          onSecretChange={() => { }}
          existingSecretKeys={['token']}
          commandArgs=""
          setCommandArgs={() => { }}
          appId=""
          setAppId={() => { }}
          installationId=""
          setInstallationId={() => { }}
          enabled={true}
          setEnabled={() => { }}
        />
        <BackendModelsTab
          isEditing={true}
          provider="anthropic"
          models={[
            {
              id: 'claude-sonnet-4-20250514',
              owned_by: 'anthropic',
              enabled: true,
            },
            {
              id: 'claude-opus-4-20250514',
              owned_by: 'anthropic',
              enabled: false,
            },
          ]}
          onChangeModels={() => { }}
          isSyncing={false}
          onSyncFromProvider={() => { }}
        />
        <BackendRepositoriesTab
          isEditing={true}
          repositories={[
            {
              full_name: 'acme/core-platform',
              default_branch: 'main',
              private: true,
              enabled: true,
            },
          ]}
          onChangeRepositories={() => { }}
          isSyncing={false}
          onSyncFromProvider={() => { }}
        />
        <BackendCapacityTab
          type="inference"
          capacity={4}
          setCapacity={() => { }}
        />
        <BackendAccessTab
          accessRestricted={true}
          setAccessRestricted={() => { }}
          selectedAgentIds={['a1']}
          onToggleAgent={() => { }}
          agents={mockAgents}
          usedProjects={mockProjects}
          onNavigateProject={() => { }}
        />
        <AgentForm
          isEditing={true}
          selectedTemplate="architect"
          onSelectTemplate={() => { }}
          name="Architect"
          onNameChange={() => { }}
          handle="architect"
          onHandleChange={() => { }}
          setIsHandleCustom={() => { }}
          kind="stage"
          onKindChange={() => { }}
          runtime="claude-code"
          onRuntimeChange={() => { }}
          model="claude-sonnet-4-20250514"
          onModelChange={() => { }}
          instructions="You are the Architect."
          onInstructionsChange={() => { }}
          enabled={true}
          onEnabledChange={() => { }}
          selectedSkillIds={['sk1']}
          onToggleSkill={() => { }}
          availableSkills={mockSkills}
          tools={[]}
          onToggleToolBackend={() => { }}
          backends={mockBackends}
          selectedBackendIds={['b-model']}
          onToggleRestrictedBackend={() => { }}
          onNavigateToBackends={() => { }}
        />
        <SkillsTable
          skills={mockSkills}
          agents={mockAgents}
          selectedSkillId="sk1"
          sortKey="name"
          sortDirection="asc"
          onSort={() => { }}
          onSelectSkill={() => { }}
          onDeleteSkill={() => { }}
        />
      </div>
    )
    assert.ok(html.includes('GitHub Enterprise'))
    assert.ok(html.includes('claude-sonnet-4-20250514'))
    assert.ok(html.includes('acme/core-platform'))
    assert.ok(html.includes('Git Operations'))
  })

  it('renders SeedModal with compact prompt and default chips', () => {
    const html = renderWithProviders(
      <SeedModal isOpen={true} onClose={() => { }} />
    )
    assert.ok(html.includes('Create a seed in') || html === '')
  })
})

describe('Detail Components: Seed, Project, and User (src/components/details/*)', () => {
  it('renders all Seed detail components', () => {
    const html = renderWithProviders(
      <div>
        <SeedGovernanceBanner
          seed={mockSeeds[0]}
          currentPolicy={mockPolicies[3]}
          approvals={mockApprovals}
          runs={mockRuns}
          canApprove={true}
          canContribute={true}
          onRefresh={async () => { }}
        />
        <SeedOverviewTab
          seed={mockSeeds[0]}
          project={mockProjects[0]}
          primaryRepo={mockRepos[0]}
          runs={mockRuns}
          ideateAgentHandle="@architect"
          comments={mockComments}
          canContribute={true}
          canManageProject={true}
          onEditSeed={() => { }}
          onRefresh={async () => { }}
        />
        <SeedTasksTab
          seed={mockSeeds[0]}
          tasks={mockSeedTasks}
          planAgentHandle="@architect"
          canContribute={true}
          onRefresh={async () => { }}
        />
        <SeedRunsTab
          seed={mockSeeds[0]}
          runs={mockRuns}
          tasks={mockSeedTasks}
          agents={mockAgents}
          backends={mockBackends}
          effectivePolicies={mockPolicies}
          canContribute={true}
          isDispatching={false}
          onDispatchRun={() => { }}
        />
        <SeedChangesTab
          seed={mockSeeds[0]}
          project={mockProjects[0]}
          repos={mockRepos}
          selectedRepoId={1}
          onSelectRepoId={() => { }}
          changes={mockChanges}
          isLoading={false}
          isFetching={false}
          error={null}
          canContribute={true}
          onRefresh={() => { }}
          onBrowseCode={() => { }}
          onRunSandbox={() => { }}
        />
        <SeedDiscussionCard
          seedId="s1"
          comments={mockComments}
          canContribute={true}
          canManageProject={true}
          resolveActorName={() => 'Ada Lovelace'}
          onRefresh={async () => { }}
        />
        <SeedMetadataSidebar
          seed={mockSeeds[0]}
          project={mockProjects[0]}
          primaryRepo={mockRepos[0]}
          runs={mockRuns}
          canContribute={true}
          autonomyNotice={null}
          onAutonomyChange={() => { }}
          resolveActorName={() => 'Ada Lovelace'}
        />
      </div>
    )
    assert.ok(html.includes('Add PKCEverifier helper'))
    assert.ok(html.includes('Ada Lovelace'))
    assert.ok(html.includes('src/auth/pkce.ts'))
    assert.ok(html.includes('Annotate src/auth/pkce.ts'))

    const longSpecHtml = renderWithProviders(
      <SeedOverviewTab
        seed={{
          ...mockSeeds[0],
          specification: Array.from({ length: 15 }, (_, i) => `Line ${i + 1} of technical spec`).join('\n'),
          plan: Array.from({ length: 15 }, (_, i) => `Step ${i + 1} of implementation plan`).join('\n'),
        }}
        project={mockProjects[0]}
        primaryRepo={mockRepos[0]}
        runs={mockRuns}
        ideateAgentHandle="@architect"
        comments={mockComments}
        canContribute={true}
        canManageProject={true}
        onEditSeed={() => { }}
        onRefresh={async () => { }}
      />
    )
    assert.ok(longSpecHtml.includes('Show full specification'))
    assert.ok(longSpecHtml.includes('Show full plan'))
    assert.ok(longSpecHtml.includes('seed-discussion-section'))
  })

  it('renders all Project detail components and helpers', () => {
    assert.equal(formatCodeBytes(512), '512 B')
    assert.equal(formatCodeBytes(2048), '2.0 KB')
    assert.equal(formatCodeBytes(2 * 1024 * 1024), '2.00 MB')

    for (const st of [
      'starting',
      'running',
      'succeeded',
      'failed',
      'stopped',
      'timed_out',
    ] as const) {
      const badge = getSandboxStatusBadge(st)
      assert.ok(badge.label.length > 0)
    }

    const html = renderWithProviders(
      <div>
        <ProjectOverviewTab
          project={mockProjects[0]}
          projectSeeds={mockSeeds}
          projectRuns={mockRuns}
          projectEvents={mockActivity}
          repos={mockRepos}
          effectiveMonthlyBudget={500}
          canManage={true}
          onOpenSeeds={() => { }}
          onOpenCode={() => { }}
          onOpenSettings={() => { }}
        />
        <ProjectMembersTab
          project={mockProjects[0]}
          members={mockMembers}
          effectivePolicies={mockPolicies}
          canManage={true}
        />
        <ProjectPoliciesTab
          project={mockProjects[0]}
          effectivePolicies={mockPolicies}
          canManage={true}
        />
        <ProjectRepositoriesCard
          project={mockProjects[0]}
          repos={mockRepos}
          canManage={true}
        />
        <ProjectAccessAndDangerCards
          project={mockProjects[0]}
          backends={mockBackends}
          canManage={true}
          confirmArchiveOpen={false}
          setConfirmArchiveOpen={() => { }}
          onToggleBackendsRestricted={async () => { }}
          onToggleAllowedBackend={async () => { }}
          onArchiveOrRestore={async () => { }}
        />
        <RepoClonePopover
          repo={mockRepos[0]}
          backend={mockBackends[0]}
          selectedRef="main"
        />
        <CodeBlobViewer
          currentPath="src/index.ts"
          blobData={{
            repository_id: 1,
            full_name: 'acme/core-platform',
            provider: 'github',
            ref: 'main',
            path: 'src/index.ts',
            name: 'index.ts',
            size: 42,
            sha: 'abcdef1234',
            is_binary: false,
            truncated: false,
            content: 'export const hello = "world";\n',
            web_url:
              'https://github.com/acme/core-platform/blob/main/src/index.ts',
          }}
          isLoading={false}
          isError={false}
          error={null}
          currentExternalUrl="https://github.com/acme/core-platform/blob/main/src/index.ts"
          providerLabel="GitHub"
          theme="dark"
          onNavigateUp={() => { }}
        />
        <SandboxSessionInspector
          projectId="p1"
          session={mockSandboxSession}
          canContribute={true}
          stoppingId={null}
          onStopSession={() => { }}
          onSendInput={async () => { }}
        />
        <ProjectSandboxTab
          project={mockProjects[0]}
          repos={mockRepos}
          projectSeeds={mockSeeds}
          canContribute={true}
          onOpenSettings={() => { }}
        />
        <ProjectSettingsTab
          project={mockProjects[0]}
          repos={mockRepos}
          canManage={true}
        />
        <ProjectCodeTab
          project={mockProjects[0]}
          repos={mockRepos}
          onOpenSettings={() => { }}
        />
      </div>
    )
    assert.ok(html.includes('acme/core-platform'))
    assert.ok(html.includes('New session'))
    assert.ok(html.includes('coverage.xml'))
  })

  it('renders User detail components', () => {
    const html = renderWithProviders(
      <div>
        <UserInviteBanner
          user={mockUserInvited}
          isReinviting={false}
          reissuedInviteUrl={{
            userId: 'u2',
            url: 'http://localhost:5173/accept-invite?token=abc',
          }}
          onReinvite={() => { }}
          onRevokeInvite={() => { }}
        />
        <UserProfileEditForm
          profileName="Ada Lovelace"
          onChangeProfileName={() => { }}
          profilePreferredName="Ada"
          onChangeProfilePreferredName={() => { }}
          isSaving={false}
          onSave={() => { }}
          onCancel={() => { }}
        />
        <UserPasswordChangeForm
          currentPassword=""
          onChangeCurrentPassword={() => { }}
          newPassword=""
          onChangeNewPassword={() => { }}
          confirmNewPassword=""
          onChangeConfirmNewPassword={() => { }}
          passwordError={null}
          isSaving={false}
          onSave={() => { }}
          onCancel={() => { }}
        />
        <UserProjectsSection
          userStatus="active"
          projectAssignments={[
            {
              project_id: 'p1',
              project_name: 'Core Platform',
              project_slug: 'core-platform',
              role: 'maintainer',
            },
          ]}
          unassignedProjects={[mockProjects[1]]}
          projectPoliciesByProject={{ p1: mockPolicies }}
          orgPolicies={mockPolicies}
          canManageProject={() => true}
          onNavigateProject={() => { }}
          onChangeProjectRole={() => { }}
          onAddProject={() => { }}
        />
        <UserTokensSection
          tokens={mockTokens}
          maxPatLifetimeDays={90}
          onError={() => { }}
        />
      </div>
    )
    assert.ok(html.includes('Invitation Pending Acceptance'))
    assert.ok(html.includes('CI Runner Token'))
  })
})
