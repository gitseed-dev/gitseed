import React, { useMemo, useState } from 'react'
import {
  Archive,
  Code2,
  Crosshair,
  ExternalLink,
  GitBranch,
  Globe,
  LayoutDashboard,
  Lock,
  Settings,
  Sprout,
  Terminal,
  Users,
  Workflow,
} from 'lucide-react'
import { PageBreadcrumb } from '../common/PageBreadcrumb'
import { ProviderIcon } from '../common/ProviderIcon'
import { SegmentedControl } from '../common/SegmentedControl'
import { ProjectCodeTab } from './project/ProjectCodeTab'
import { ProjectMembersTab } from './project/ProjectMembersTab'
import { ProjectOverviewTab } from './project/ProjectOverviewTab'
import { ProjectPoliciesTab } from './project/ProjectPoliciesTab'
import { ProjectSandboxTab } from './project/ProjectSandboxTab'
import { SessionStartControl } from '../sessions/SessionStartControl'
import { ProjectSettingsTab } from './project/ProjectSettingsTab'
import { SeedModal } from '../modals/SeedModal'
import { SeedsView } from '../../views/SeedsView'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import { formatUsd } from '../../utils/format'
import { buildRepoWebUrl, safeExternalUrl } from '../../utils/gitUrls'
import {
  canUserContributeToProject,
  canUserManageProject,
  computeProjectSummary,
  getProjectInitials,
} from '../../utils/projects'
import type { Project, SeedStage } from '../../types'

export type ProjectDetailTabId =
  | 'overview'
  | 'seeds'
  | 'code'
  | 'run'
  | 'members'
  | 'policies'
  | 'settings'

export interface ProjectDetailProps {
  project: Project
  onBack: () => void
}

const VALID_TABS: ReadonlySet<ProjectDetailTabId> = new Set([
  'overview',
  'seeds',
  'code',
  'run',
  'members',
  'policies',
  'settings',
])

export const ProjectDetail: React.FC<ProjectDetailProps> = ({
  project,
  onBack,
}) => {
  const { user } = useAuth()
  const {
    allSeeds,
    runs,
    allEvents,
    backends,
    policies: orgPolicies,
    orgSettings,
    projectMembersByProject,
    projectPoliciesByProject,
    projectReposByProject,
    selectedProject,
    setSelectedProject,
    activeDetailTab,
    navigateToDetail,
  } = useData()
  const { t } = useTranslation()

  const [settingsSection, setSettingsSection] = useState<string | null>(null)
  const [seedsStageFilter, setSeedsStageFilter] = useState<'all' | SeedStage>('all')
  const [selectedCodeRepoId, setSelectedCodeRepoId] = useState<number | null>(null)
  const [isSeedModalOpen, setIsSeedModalOpen] = useState(false)

  const members = useMemo(
    () => projectMembersByProject[project.id] || [],
    [projectMembersByProject, project.id]
  )
  const repos = useMemo(
    () => projectReposByProject[project.id] || [],
    [projectReposByProject, project.id]
  )
  const effectivePolicies = useMemo(
    () =>
      projectPoliciesByProject[project.id]?.length
        ? projectPoliciesByProject[project.id]
        : orgPolicies,
    [projectPoliciesByProject, project.id, orgPolicies]
  )

  const summary = useMemo(
    () =>
      computeProjectSummary(
        project,
        allSeeds,
        repos,
        members,
        allEvents,
        orgSettings
      ),
    [project, allSeeds, repos, members, allEvents, orgSettings]
  )

  const projectSeedIds = useMemo(
    () => new Set(summary.allProjectSeeds.map((s) => s.id)),
    [summary.allProjectSeeds]
  )

  const projectRuns = useMemo(
    () => runs.filter((r) => projectSeedIds.has(r.seed_id)),
    [runs, projectSeedIds]
  )

  const projectTokens = useMemo(
    () =>
      projectRuns.reduce(
        (acc, r) => acc + (r.input_tokens || 0) + (r.output_tokens || 0),
        0
      ),
    [projectRuns]
  )

  const projectEvents = useMemo(
    () => allEvents.filter((e) => e.project_id === project.id),
    [allEvents, project.id]
  )

  const canManage = useMemo(
    () => canUserManageProject(user, project, members),
    [user, project, members]
  )

  const canContribute = useMemo(
    () => canUserContributeToProject(user, project, members),
    [user, project, members]
  )

  const rawTab: ProjectDetailTabId =
    activeDetailTab && VALID_TABS.has(activeDetailTab as ProjectDetailTabId)
      ? (activeDetailTab as ProjectDetailTabId)
      : 'overview'

  const currentTab: ProjectDetailTabId =
    rawTab === 'settings' && !canManage ? 'overview' : rawTab

  const handleTabChange = (nextTab: ProjectDetailTabId) => {
    if (nextTab === 'settings' && !canManage) return
    setSettingsSection(null)
    if (nextTab !== 'seeds') {
      setSeedsStageFilter('all')
    }
    navigateToDetail(
      VIEWS.PROJECTS,
      project.slug || project.id,
      nextTab === 'overview' ? undefined : nextTab
    )
  }

  const handleOpenSeeds = (stage?: SeedStage) => {
    setSeedsStageFilter(stage || 'all')
    navigateToDetail(VIEWS.PROJECTS, project.slug || project.id, 'seeds')
  }

  const handleOpenSettingsSection = (section?: string) => {
    if (!canManage) return
    setSettingsSection(section || null)
    navigateToDetail(VIEWS.PROJECTS, project.slug || project.id, 'settings')
  }

  const handleOpenCode = (repoId?: number | null) => {
    if (repoId !== undefined) {
      setSelectedCodeRepoId(repoId)
    }
    navigateToDetail(VIEWS.PROJECTS, project.slug || project.id, 'code')
  }

  const isFocused = selectedProject === project.id
  const primaryBackend = summary.primaryRepo
    ? backends.find((b) => b.id === summary.primaryRepo?.backend_id)
    : null
  const isPrimaryRepoLocal = Boolean(
    summary.primaryRepo &&
      (primaryBackend?.provider === 'local' ||
        summary.primaryRepo.full_name.startsWith('local/'))
  )
  const primaryRepoExternalUrl =
    summary.primaryRepo && !isPrimaryRepoLocal
      ? safeExternalUrl(
          buildRepoWebUrl(summary.primaryRepo, primaryBackend, {
            ref: summary.primaryRepo.default_branch,
          })
        )
      : null

  const overridesCount = useMemo(
    () =>
      effectivePolicies.filter((p) => (p.overridden_fields || []).length > 0)
        .length,
    [effectivePolicies]
  )

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto pb-16">
      {/* Header */}
      <div className="flex flex-col gap-4 pb-4 border-b border-zinc-200 dark:border-zinc-700/80">
        <PageBreadcrumb
          items={[{ label: t('nav.projects'), onClick: onBack }]}
          currentLabel={project.name}
        />

        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-4">
          <div className="flex items-start gap-3.5 min-w-0">
            <div className="w-11 h-11 rounded-2xl bg-teal-500/15 border border-teal-500/30 text-teal-700 dark:text-teal-300 flex items-center justify-center font-mono font-extrabold text-sm uppercase shrink-0 shadow-2xs">
              {getProjectInitials(project)}
            </div>

            <div className="flex flex-col min-w-0">
              <div className="flex items-center gap-2.5 flex-wrap">
                <h2 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100 truncate">
                  {project.name}
                </h2>
                <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-600 dark:text-zinc-300">
                  {project.visibility === 'public' ? (
                    <>
                      <Globe size={11} className="text-sky-500" />
                      <span>{t('projects.public')}</span>
                    </>
                  ) : (
                    <>
                      <Lock size={11} className="text-zinc-400" />
                      <span>{t('projects.private')}</span>
                    </>
                  )}
                </span>
                {/* Focus is a view setting: small toggle next to the name */}
                {!project.archived_at && (
                  <button
                    type="button"
                    onClick={() => setSelectedProject(isFocused ? null : project.id)}
                    title={
                      isFocused
                        ? t('details.project.actions.unfocus')
                        : t('details.project.actions.focus')
                    }
                    aria-pressed={isFocused}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-xs font-semibold border transition cursor-pointer ${
                      isFocused
                        ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/30'
                        : 'text-zinc-400 border-transparent hover:text-teal-600 dark:hover:text-teal-400 hover:border-zinc-200 dark:hover:border-zinc-700'
                    }`}
                  >
                    <Crosshair size={11} />
                    {isFocused && <span>{t('projects.focusedBadge')}</span>}
                  </button>
                )}
                {project.archived_at && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-xs font-bold bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30">
                    <Archive size={11} />
                    <span>Archived</span>
                  </span>
                )}
              </div>

              <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1 line-clamp-2">
                {project.description || t('projects.noDescription')}
              </p>

              {/* Metadata chips */}
              <div className="flex items-center gap-3 flex-wrap mt-2.5 text-xs text-zinc-500 dark:text-zinc-400">
                {summary.primaryRepo && (
                  <span className="inline-flex items-center gap-1.5 font-mono text-zinc-700 dark:text-zinc-300">
                    <button
                      type="button"
                      onClick={() => handleOpenCode(summary.primaryRepo?.id)}
                      title="Browse repository code in GitSeed"
                      className="inline-flex items-center gap-1.5 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
                    >
                      <ProviderIcon
                        type="git"
                        provider={
                          primaryBackend?.provider ||
                          (isPrimaryRepoLocal ? 'local' : 'github')
                        }
                        size={13}
                      />
                      <span className="font-semibold">
                        {summary.primaryRepo.full_name}
                      </span>
                      <span className="inline-flex items-center gap-0.5 text-zinc-400">
                        <GitBranch size={11} />
                        {summary.primaryRepo.default_branch}
                      </span>
                    </button>
                    {primaryRepoExternalUrl && (
                      <a
                        href={primaryRepoExternalUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        title="Open repository on remote host"
                        className="text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 transition"
                      >
                        <ExternalLink size={11} />
                      </a>
                    )}
                  </span>
                )}

                {summary.totalSpend <= 0 && projectTokens > 0 ? (
                  // Local models: show usage instead of a misleading $0.00
                  <span title="Local models (no cost recorded)">
                    Usage:{' '}
                    <strong className="font-mono text-zinc-800 dark:text-zinc-200">
                      {projectTokens >= 1_000_000
                        ? `${(projectTokens / 1_000_000).toFixed(1)}M`
                        : projectTokens >= 1_000
                          ? `${(projectTokens / 1_000).toFixed(1)}k`
                          : projectTokens}{' '}
                      tok
                    </strong>
                  </span>
                ) : (
                <span>
                  Spend:{' '}
                  <strong className="font-mono text-zinc-800 dark:text-zinc-200">
                    {formatUsd(summary.totalSpend)}
                  </strong>
                  {summary.effectiveMonthlyBudget !== null && (
                    <span className="font-mono text-zinc-400">
                      {' '}
                      / ${summary.effectiveMonthlyBudget.toFixed(0)}
                    </span>
                  )}
                </span>
                )}

                {(project.tags || []).length > 0 && (
                  <div className="flex items-center gap-1.5 flex-wrap">
                    {project.tags.map((tag) => (
                      <span
                        key={tag}
                        className="px-2 py-0.5 rounded-md text-xs font-mono bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-600 dark:text-zinc-400"
                      >
                        #{tag}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          </div>

          {/* Header Actions: Consolidated "+ New" menu with New Seed + coding agents & terminals */}
          <div className="flex items-center gap-2.5 shrink-0">
            {!project.archived_at && canContribute && (
              <SessionStartControl
                projectId={project.id}
                tone="primary"
                size="md"
                onNewSeed={() => setIsSeedModalOpen(true)}
              />
            )}
          </div>
        </div>

        {/* Sub-navigation Tabs */}
        <div className="pt-1 flex items-center justify-between gap-4 overflow-x-auto">
          <SegmentedControl<ProjectDetailTabId>
            ariaLabel="Project detail tabs"
            selectedId={currentTab}
            onSelect={handleTabChange}
            items={[
              {
                id: 'overview',
                label: t('details.project.overview'),
                icon: <LayoutDashboard size={14} />,
              },
              {
                id: 'seeds',
                label: t('nav.seeds'),
                count: summary.openSeeds.length,
                icon: <Sprout size={14} />,
              },
              {
                id: 'code',
                label: t('details.project.code'),
                count: repos.length > 0 ? repos.length : undefined,
                icon: <Code2 size={14} />,
              },
              {
                id: 'run',
                label: t('details.project.run'),
                icon: <Terminal size={14} />,
              },
              {
                id: 'members',
                label: t('details.project.members'),
                count: members.length,
                icon: <Users size={14} />,
              },
              {
                id: 'policies',
                label: t('nav.routes'),
                count: overridesCount > 0 ? overridesCount : undefined,
                icon: <Workflow size={14} />,
              },
              ...(canManage
                ? [
                    {
                      id: 'settings' as const,
                      label: t('details.project.settings'),
                      icon: <Settings size={14} />,
                    },
                  ]
                : []),
            ]}
          />
        </div>
      </div>

      {/* Active Tab Content */}
      {currentTab === 'overview' && (
        <ProjectOverviewTab
          project={project}
          projectSeeds={summary.allProjectSeeds}
          projectRuns={projectRuns}
          projectEvents={projectEvents}
          repos={repos}
          effectiveMonthlyBudget={summary.effectiveMonthlyBudget}
          canManage={canManage}
          onOpenSeeds={handleOpenSeeds}
          onOpenCode={handleOpenCode}
          onOpenSettings={handleOpenSettingsSection}
        />
      )}

      {currentTab === 'seeds' && (
        <SeedsView
          embeddedProjectId={project.id}
          initialStageFilter={seedsStageFilter}
        />
      )}

      {currentTab === 'code' && (
        <ProjectCodeTab
          project={project}
          repos={repos}
          initialRepoId={selectedCodeRepoId}
          onOpenSettings={handleOpenSettingsSection}
        />
      )}

      {currentTab === 'run' && (
        <ProjectSandboxTab
          project={project}
          repos={repos}
          projectSeeds={summary.allProjectSeeds}
          canContribute={canContribute}
          onOpenSettings={handleOpenSettingsSection}
        />
      )}

      {currentTab === 'members' && (
        <ProjectMembersTab
          project={project}
          members={members}
          effectivePolicies={effectivePolicies}
          canManage={canManage}
        />
      )}

      {currentTab === 'policies' && (
        <ProjectPoliciesTab
          project={project}
          effectivePolicies={effectivePolicies}
          canManage={canManage}
        />
      )}

      {currentTab === 'settings' && (
        <ProjectSettingsTab
          project={project}
          repos={repos}
          canManage={canManage}
          initialSection={settingsSection}
        />
      )}

      <SeedModal
        isOpen={isSeedModalOpen}
        onClose={() => setIsSeedModalOpen(false)}
        defaultProjectId={project.id}
        onSaved={(created) => navigateToDetail(VIEWS.SEEDS, created.id)}
      />
    </div>
  )
}
