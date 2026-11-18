import React, { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Archive,
  Bot,
  CheckCircle2,
  Crosshair,
  Edit2,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Globe,
  Lock,
  Plus,
  RotateCcw,
  Settings,
  Sprout,
  Terminal,
  Users,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { ActionMenu, type ActionMenuItem } from '../components/common/ActionMenu'
import { ConfirmDialog } from '../components/common/ConfirmDialog'
import { AgentProviderIcon, ProviderIcon } from '../components/common/ProviderIcon'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { ProjectDetail } from '../components/details/ProjectDetail'
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../components/layout/EntityPageLayout'
import { ProjectModal } from '../components/modals/ProjectModal'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { VIEWS } from '../constants'
import { usePagination } from '../hooks/usePagination'
import { useTableSort } from '../hooks/useTableSort'
import { formatRelativeTime, formatUsd } from '../utils/format'
import {
  canUserContributeToProject,
  canUserCreateProject,
  canUserManageProject,
  computeProjectSummary,
  getProjectInitials,
  type ProjectSummary,
} from '../utils/projects'
import type { Agent, AgentRuntime, Project } from '../types'

type ProjectFilterTab = 'active' | 'needs_action' | 'blocked' | 'all' | 'archived'
type ProjectSortKey =
  | 'name'
  | 'openSeeds'
  | 'needsYou'
  | 'spend'
  | 'members'
  | 'lastActivity'

export const ProjectsView: React.FC = () => {
  const { user } = useAuth()
  const {
    projects,
    allSeeds,
    allEvents,
    backends,
    agents,
    policies,
    projectPoliciesByProject,
    orgSettings,
    projectMembersByProject,
    projectReposByProject,
    selectedProject,
    setSelectedProject,
    activeDetailId,
    navigateToDetail,
    clearDetail,
  } = useData()
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [filterTab, setFilterTab] = useState<ProjectFilterTab>('active')
  const [searchQuery, setSearchQuery] = useState('')
  const [modalOpen, setModalOpen] = useState(false)
  const [editingProject, setEditingProject] = useState<Project | null>(null)
  const [archiveConfirmProject, setArchiveConfirmProject] =
    useState<Project | null>(null)
  const [startingSessionKey, setStartingSessionKey] = useState<string | null>(
    null
  )

  const backendsById = useMemo(
    () => new Map(backends.map((b) => [b.id, b])),
    [backends]
  )

  // Resolve detail project if activeDetailId is present
  const detailProject = useMemo(() => {
    if (!activeDetailId) return null
    return (
      projects.find(
        (p) => p.id === activeDetailId || p.slug === activeDetailId
      ) || null
    )
  }, [projects, activeDetailId])

  const projectSummaries = useMemo<ProjectSummary[]>(() => {
    return projects.map((project) =>
      computeProjectSummary(
        project,
        allSeeds,
        projectReposByProject[project.id] || [],
        projectMembersByProject[project.id] || [],
        allEvents,
        orgSettings
      )
    )
  }, [
    projects,
    allSeeds,
    projectReposByProject,
    projectMembersByProject,
    allEvents,
    orgSettings,
  ])

  const nonArchivedSummaries = useMemo(
    () => projectSummaries.filter((s) => !s.project.archived_at),
    [projectSummaries]
  )
  const archivedSummaries = useMemo(
    () => projectSummaries.filter((s) => Boolean(s.project.archived_at)),
    [projectSummaries]
  )
  const needsActionCount = useMemo(
    () =>
      nonArchivedSummaries.filter((s) => s.needsYouSeeds.length > 0).length,
    [nonArchivedSummaries]
  )
  const blockedCount = useMemo(
    () => nonArchivedSummaries.filter((s) => s.blockedSeeds.length > 0).length,
    [nonArchivedSummaries]
  )

  const filteredSummaries = useMemo(() => {
    return projectSummaries.filter((summary) => {
      const { project } = summary
      const isArchived = Boolean(project.archived_at)

      if (filterTab === 'archived') {
        if (!isArchived) return false
      } else if (filterTab === 'active') {
        if (isArchived) return false
      } else if (filterTab === 'needs_action') {
        if (isArchived || summary.needsYouSeeds.length === 0) return false
      } else if (filterTab === 'blocked') {
        if (isArchived || summary.blockedSeeds.length === 0) return false
      }

      if (searchQuery.trim()) {
        const q = searchQuery.trim().toLowerCase()
        const repoMatch = summary.repos.some((r) =>
          r.full_name.toLowerCase().includes(q)
        )
        const tagMatch = (project.tags || []).some((tag) =>
          tag.toLowerCase().includes(q)
        )
        return (
          project.name.toLowerCase().includes(q) ||
          project.slug.toLowerCase().includes(q) ||
          (project.description || '').toLowerCase().includes(q) ||
          repoMatch ||
          tagMatch
        )
      }

      return true
    })
  }, [projectSummaries, filterTab, searchQuery])

  const customComparators = useMemo<
    Record<ProjectSortKey, (a: ProjectSummary, b: ProjectSummary) => number>
  >(
    () => ({
      name: (a, b) => a.project.name.localeCompare(b.project.name),
      openSeeds: (a, b) => a.openSeeds.length - b.openSeeds.length,
      needsYou: (a, b) => a.needsYouSeeds.length - b.needsYouSeeds.length,
      spend: (a, b) => a.totalSpend - b.totalSpend,
      members: (a, b) => a.members.length - b.members.length,
      lastActivity: (a, b) => {
        const ta = a.lastActivityAt ? new Date(a.lastActivityAt).getTime() : 0
        const tb = b.lastActivityAt ? new Date(b.lastActivityAt).getTime() : 0
        return ta - tb
      },
    }),
    []
  )

  const { sortedItems, sortConfig, requestSort } = useTableSort<
    ProjectSummary,
    ProjectSortKey
  >({
    items: filteredSummaries,
    initialKey: 'name',
    initialDirection: 'asc',
    customComparators,
  })

  const { paginatedItems, paginationProps } = usePagination({
    items: sortedItems,
    initialPageSize: 15,
  })

  const handleArchiveOrRestore = async (project: Project) => {
    try {
      if (project.archived_at) {
        await api.projects.restore(project.id)
      } else {
        await api.projects.archive(project.id)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectDetail(project.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      setArchiveConfirmProject(null)
    } catch (err) {
      console.error('Failed to archive or restore project:', err)
    }
  }

  const resolveDefaultAgent = (projectId: string): Agent | null => {
    const list = agents
      .filter((a) => a.enabled && !a.archived_at)
      .sort(
        (a, b) => Number(a.kind === 'assistant') - Number(b.kind === 'assistant')
      )
    const effective =
      (projectPoliciesByProject[projectId]?.length
        ? projectPoliciesByProject[projectId]
        : policies) || []
    const executeAgentId = effective.find(
      (p) => p.stage === 'execute'
    )?.agent_id
    const planAgentId = effective.find((p) => p.stage === 'plan')?.agent_id
    return (
      list.find((a) => a.id === executeAgentId) ||
      list.find((a) => a.handle === 'coder') ||
      list.find((a) => a.handle.endsWith('-coder')) ||
      list.find((a) => a.id === planAgentId) ||
      list.find((a) => a.kind !== 'assistant') ||
      list[0] ||
      null
    )
  }

  const handleStartCodingSession = async (
    project: Project,
    agent: Agent | null
  ) => {
    const key = `${project.id}:chat`
    if (startingSessionKey) return
    setStartingSessionKey(key)
    try {
      try {
        localStorage.setItem('gitseed_last_session_project', project.id)
      } catch {
        /* ignore */
      }
      const clock = new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      })
      const created = await api.projects.createAgentSession(project.id, {
        ref: null,
        agent_id: agent?.id ?? null,
        runtime: (agent?.runtime ?? 'opencode') as AgentRuntime,
        model: null,
        title: `Agent · @${agent?.handle || 'agent'} · ${clock}`,
        is_saved: false,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sessions'] }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectAgentSessions(project.id),
        }),
      ])
      navigateToDetail(VIEWS.SESSIONS, 'chat', created.id)
    } catch (err) {
      console.error('Failed to start coding session:', err)
    } finally {
      setStartingSessionKey(null)
    }
  }

  const handleStartTerminal = async (summary: ProjectSummary) => {
    const { project, primaryRepo } = summary
    const key = `${project.id}:terminal`
    if (startingSessionKey) return
    setStartingSessionKey(key)
    try {
      try {
        localStorage.setItem('gitseed_last_session_project', project.id)
      } catch {
        /* ignore */
      }
      const clock = new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        hourCycle: 'h23',
      })
      const branch = primaryRepo?.default_branch || null
      const created = await api.projects.createSandbox(project.id, {
        ref: null,
        image: '',
        setup_command: null,
        kind: 'terminal',
        title: branch ? `Terminal · ${branch} · ${clock}` : `Terminal · ${clock}`,
        as_root: false,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['sessions'] }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectSandboxes(project.id),
        }),
      ])
      navigateToDetail(VIEWS.SESSIONS, 'terminal', created.id)
    } catch (err) {
      console.error('Failed to start terminal session:', err)
    } finally {
      setStartingSessionKey(null)
    }
  }

  const buildProjectActionItems = (
    summary: ProjectSummary
  ): ActionMenuItem[] => {
    const { project } = summary
    const isFocused = selectedProject === project.id
    const canManage = canUserManageProject(user, project, summary.members)
    const canContribute =
      !project.archived_at &&
      canUserContributeToProject(user, project, summary.members)
    const defaultAgent = canContribute ? resolveDefaultAgent(project.id) : null

    return [
      {
        label: t('projects.actions.open'),
        icon: <ExternalLink size={14} />,
        onClick: () =>
          navigateToDetail(VIEWS.PROJECTS, project.slug || project.id),
      },
      ...(!project.archived_at
        ? [
            {
              label: isFocused
                ? t('projects.actions.unfocus')
                : t('projects.actions.focus'),
              icon: <Crosshair size={14} />,
              onClick: () =>
                setSelectedProject(isFocused ? null : project.id),
            },
          ]
        : []),
      ...(canContribute
        ? [
            {
              label: defaultAgent
                ? `Code with @${defaultAgent.handle}`
                : 'New coding session',
              icon: defaultAgent ? (
                <AgentProviderIcon agent={defaultAgent} size={14} />
              ) : (
                <Bot size={14} />
              ),
              dividerBefore: true,
              disabled: Boolean(startingSessionKey),
              onClick: () => {
                void handleStartCodingSession(project, defaultAgent)
              },
            },
            {
              label: 'Open terminal',
              icon: <Terminal size={14} />,
              disabled: Boolean(startingSessionKey),
              onClick: () => {
                void handleStartTerminal(summary)
              },
            },
          ]
        : []),
      ...(canManage
        ? [
            {
              label: t('projects.actions.edit'),
              icon: <Edit2 size={14} />,
              dividerBefore: true,
              onClick: () => {
                setEditingProject(project)
                setModalOpen(true)
              },
            },
            {
              label: t('projects.actions.settings'),
              icon: <Settings size={14} />,
              onClick: () =>
                navigateToDetail(
                  VIEWS.PROJECTS,
                  project.slug || project.id,
                  'settings'
                ),
            },
            {
              label: project.archived_at
                ? t('projects.actions.unarchive')
                : t('projects.actions.archive'),
              icon: project.archived_at ? (
                <RotateCcw size={14} />
              ) : (
                <Archive size={14} />
              ),
              variant: project.archived_at
                ? ('default' as const)
                : ('danger' as const),
              onClick: () => setArchiveConfirmProject(project),
            },
          ]
        : []),
    ]
  }

  // Render ProjectDetail when a project is selected in URL
  if (detailProject) {
    return <ProjectDetail project={detailProject} onBack={clearDetail} />
  }

  const renderStatusPill = (summary: ProjectSummary) => {
    if (summary.project.archived_at) {
      return (
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80">
          <Archive size={11} />
          <span>Archived</span>
        </span>
      )
    }
    if (summary.blockedSeeds.length > 0) {
      return (
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-semibold bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/25">
          <AlertTriangle size={11} />
          <span>
            {t('projects.blockedCount', {
              count: summary.blockedSeeds.length,
            })}
          </span>
        </span>
      )
    }
    if (summary.needsYouSeeds.length > 0) {
      return (
        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/25">
          <span className="w-1.5 h-1.5 rounded-full bg-amber-500 animate-pulse" />
          <span>
            {summary.needsYouSeeds.length === 1
              ? '1 gate waiting'
              : `${summary.needsYouSeeds.length} gates waiting`}
          </span>
        </span>
      )
    }
    return (
      <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
        <CheckCircle2 size={13} className="text-emerald-500 shrink-0" />
        <span>{t('projects.status.nominal')}</span>
      </span>
    )
  }

  const canCreateProject = useMemo(
    () => canUserCreateProject(user, projects, projectMembersByProject),
    [user, projects, projectMembersByProject]
  )

  return (
    <>
      <EntityPageLayout
        title={t('projects.title')}
        subtitle={t('projects.subtitle')}
        count={nonArchivedSummaries.length}
        isGlobal
        hideOrgScopeBanner
        storageKey="gitseed_projects_view_mode"
        showViewModeToggle
        primaryAction={
          canCreateProject ? (
            <Button
              type="button"
              variant="primary"
              size="sm"
              onClick={() => {
                setEditingProject(null)
                setModalOpen(true)
              }}
              className="rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm shadow-teal-600/20 cursor-pointer"
            >
              <Plus size={15} />
              <span>{t('projects.create')}</span>
            </Button>
          ) : undefined
        }
        filterTabs={
          <SegmentedControl<ProjectFilterTab>
            ariaLabel="Filter projects"
            selectedId={filterTab}
            onSelect={setFilterTab}
            items={[
              {
                id: 'active',
                label: t('projects.filters.active'),
                count: nonArchivedSummaries.length,
              },
              {
                id: 'needs_action',
                label: t('projects.filters.needsAction'),
                count: needsActionCount,
              },
              {
                id: 'blocked',
                label: t('projects.filters.blocked'),
                count: blockedCount,
              },
              {
                id: 'all',
                label: t('projects.filters.all'),
                count: projectSummaries.length,
              },
              ...(archivedSummaries.length > 0
                ? [
                    {
                      id: 'archived' as const,
                      label: t('projects.filters.archived'),
                      count: archivedSummaries.length,
                    },
                  ]
                : []),
            ]}
          />
        }
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t('projects.searchPlaceholder')}
        isEmpty={sortedItems.length === 0}
        emptyMessage={
          projects.length === 0 ? (
            <div className="flex flex-col items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center">
                <FolderGit2 size={20} />
              </div>
              <p className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
                {t('projects.emptyTitle')}
              </p>
              <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
                {t('projects.emptySubtitle')}
              </p>
              {canCreateProject && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    setEditingProject(null)
                    setModalOpen(true)
                  }}
                  className="mt-1 rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
                >
                  <Plus size={14} />
                  <span>{t('projects.createFirst')}</span>
                </Button>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
                <FolderGit2 size={20} />
              </div>
              <p className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
                {t('projects.noMatch')}
              </p>
              {(searchQuery || filterTab !== 'active') && (
                <button
                  type="button"
                  onClick={() => {
                    setSearchQuery('')
                    setFilterTab('active')
                  }}
                  className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {t('projects.clearFilter')}
                </button>
              )}
            </div>
          )
        }
        pagination={paginationProps}
        renderList={() => (
          <EntityTableCard>
            <EntityTableHeaderRow>
              <SortableTh
                sortKey="name"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('projects.table.project')}
              </SortableTh>
              <th className="py-3 px-4 font-semibold">
                {t('projects.table.repository')}
              </th>
              <SortableTh
                sortKey="openSeeds"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('projects.table.pipeline')}
              </SortableTh>
              <SortableTh
                sortKey="needsYou"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('projects.table.needsYou')}
              </SortableTh>
              <SortableTh
                sortKey="spend"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('projects.table.spend')}
              </SortableTh>
              <SortableTh
                sortKey="members"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('projects.table.members')}
              </SortableTh>
              <SortableTh
                sortKey="lastActivity"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('projects.table.lastActivity')}
              </SortableTh>
              <th className="py-3 px-4 text-right font-semibold">
                {t('projects.table.actions')}
              </th>
            </EntityTableHeaderRow>

            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-700/80">
              {paginatedItems.map((summary) => {
                const { project, primaryRepo } = summary
                const isFocused = selectedProject === project.id
                const primaryBackend = primaryRepo
                  ? backendsById.get(primaryRepo.backend_id)
                  : null
                const budgetPct =
                  summary.effectiveMonthlyBudget &&
                  summary.effectiveMonthlyBudget > 0
                    ? Math.min(
                        100,
                        Math.round(
                          (summary.totalSpend /
                            summary.effectiveMonthlyBudget) *
                            100
                        )
                      )
                    : 0

                return (
                  <tr
                    key={project.id}
                    onClick={() =>
                      navigateToDetail(
                        VIEWS.PROJECTS,
                        project.slug || project.id
                      )
                    }
                    className={`transition-colors cursor-pointer ${
                      isFocused
                        ? 'bg-teal-500/[0.04] dark:bg-teal-500/[0.08] hover:bg-teal-500/[0.08] dark:hover:bg-teal-500/[0.12]'
                        : 'hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40'
                    } ${project.archived_at ? 'opacity-65' : ''}`}
                  >
                    {/* 1. Project Identity */}
                    <td className="py-3.5 px-4">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-8 h-8 rounded-xl bg-teal-500/15 border border-teal-500/30 text-teal-700 dark:text-teal-300 flex items-center justify-center font-mono font-bold text-xs uppercase shrink-0">
                          {getProjectInitials(project)}
                        </div>
                        <div className="flex flex-col min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-sm font-bold text-zinc-900 dark:text-zinc-100 truncate">
                              {project.name}
                            </span>
                            {project.visibility === 'public' ? (
                              <Globe
                                size={12}
                                className="text-sky-500 shrink-0"
                                aria-label="Public project"
                              />
                            ) : (
                              <Lock
                                size={12}
                                className="text-zinc-400 shrink-0"
                                aria-label="Private project"
                              />
                            )}
                            {isFocused && (
                              <span className="px-1.5 py-0.5 rounded text-xs font-bold bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30">
                                {t('projects.focusedBadge')}
                              </span>
                            )}
                          </div>
                          {project.description && (
                            <p className="text-xs text-zinc-500 dark:text-zinc-400 truncate max-w-xs mt-0.5">
                              {project.description}
                            </p>
                          )}
                        </div>
                      </div>
                    </td>

                    {/* 2. Primary Repository */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {primaryRepo ? (
                        <div className="flex items-center gap-2">
                          <span className="text-zinc-600 dark:text-zinc-300 shrink-0">
                            <ProviderIcon
                              type="git"
                              provider={primaryBackend?.provider || 'github'}
                              size={14}
                            />
                          </span>
                          <div className="flex flex-col">
                            <span className="font-mono text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                              {primaryRepo.full_name}
                            </span>
                            <span className="inline-flex items-center gap-1 text-xs font-mono text-zinc-400">
                              <GitBranch size={10} />
                              {primaryRepo.default_branch}
                              {summary.repos.length > 1 &&
                                ` (+${summary.repos.length - 1})`}
                            </span>
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-zinc-400 dark:text-zinc-500">
                          No repo attached
                        </span>
                      )}
                    </td>

                    {/* 3. Pipeline */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {summary.openSeeds.length > 0 ? (
                        <div className="flex flex-col gap-0.5">
                          <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                            <Sprout size={13} className="text-teal-500" />
                            <span>
                              {t('projects.pipelineOpen', {
                                count: summary.openSeeds.length,
                              })}
                            </span>
                          </div>
                          <span className="text-xs text-zinc-500 dark:text-zinc-400">
                            {summary.runningSeeds.length > 0
                              ? `${summary.runningSeeds.length} running`
                              : `${summary.merged30dCount} merged (30d)`}
                          </span>
                        </div>
                      ) : (
                        <span className="text-xs text-zinc-400 dark:text-zinc-500">
                          {summary.merged30dCount > 0
                            ? `${summary.merged30dCount} merged (30d)`
                            : t('projects.idle')}
                        </span>
                      )}
                    </td>

                    {/* 4. Needs You / Status */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {renderStatusPill(summary)}
                    </td>

                    {/* 5. Spend */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <div className="flex flex-col gap-1 w-28">
                        <div className="flex items-baseline justify-between gap-1 font-mono text-xs">
                          <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                            {formatUsd(summary.totalSpend)}
                          </span>
                          {summary.effectiveMonthlyBudget !== null && (
                            <span className="text-xs text-zinc-400">
                              / ${summary.effectiveMonthlyBudget.toFixed(0)}
                            </span>
                          )}
                        </div>
                        {summary.effectiveMonthlyBudget !== null && (
                          <div className="w-full h-1.5 rounded-full bg-zinc-200 dark:bg-zinc-800 overflow-hidden">
                            <div
                              className={`h-full rounded-full transition-all ${
                                budgetPct >= 90
                                  ? 'bg-rose-500'
                                  : budgetPct >= 75
                                    ? 'bg-amber-500'
                                    : 'bg-teal-500'
                              }`}
                              style={{ width: `${budgetPct}%` }}
                            />
                          </div>
                        )}
                      </div>
                    </td>

                    {/* 6. Members */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-300">
                        <Users size={13} className="text-zinc-400" />
                        <span>{summary.members.length}</span>
                      </span>
                    </td>

                    {/* 7. Last Activity */}
                    <td className="py-3.5 px-4 whitespace-nowrap font-mono text-xs text-zinc-500 dark:text-zinc-400">
                      {formatRelativeTime(summary.lastActivityAt)}
                    </td>

                    {/* 8. Actions */}
                    <td
                      className="py-3.5 px-4 text-right whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ActionMenu items={buildProjectActionItems(summary)} />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </EntityTableCard>
        )}
        renderGrid={() => (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-4">
            {paginatedItems.map((summary) => {
              const { project, primaryRepo } = summary
              const isFocused = selectedProject === project.id
              const primaryBackend = primaryRepo
                ? backendsById.get(primaryRepo.backend_id)
                : null

              return (
                <div
                  key={project.id}
                  onClick={() =>
                    navigateToDetail(
                      VIEWS.PROJECTS,
                      project.slug || project.id
                    )
                  }
                  className={`p-5 rounded-2xl bg-white/95 dark:bg-zinc-900/80 border transition-all cursor-pointer flex flex-col justify-between gap-4 shadow-sm hover:shadow-md ${
                    isFocused
                      ? 'border-teal-500/50 ring-1 ring-teal-500/20'
                      : 'border-zinc-200 dark:border-zinc-700/80 hover:border-teal-500/40'
                  }`}
                >
                  <div className="flex flex-col gap-3">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex items-center gap-3 min-w-0">
                        <div className="w-9 h-9 rounded-xl bg-teal-500/15 border border-teal-500/30 text-teal-700 dark:text-teal-300 flex items-center justify-center font-mono font-bold text-xs uppercase shrink-0">
                          {getProjectInitials(project)}
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <h3 className="text-sm font-bold text-zinc-900 dark:text-zinc-100 truncate">
                              {project.name}
                            </h3>
                            {isFocused && (
                              <span className="px-1.5 py-0.5 rounded text-xs font-bold bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30">
                                {t('projects.focusedBadge')}
                              </span>
                            )}
                          </div>
                          <span className="text-xs font-mono text-zinc-400">
                            {project.slug}
                          </span>
                        </div>
                      </div>
                      <div
                        className="flex items-center gap-1.5 shrink-0"
                        onClick={(e) => e.stopPropagation()}
                      >
                        {renderStatusPill(summary)}
                        <ActionMenu items={buildProjectActionItems(summary)} />
                      </div>
                    </div>

                    <p className="text-xs text-zinc-500 dark:text-zinc-400 line-clamp-2 min-h-[2rem]">
                      {project.description || t('projects.noDescription')}
                    </p>

                    {primaryRepo && (
                      <div className="inline-flex items-center gap-2 px-2.5 py-1.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono text-zinc-700 dark:text-zinc-300">
                        <ProviderIcon
                          type="git"
                          provider={primaryBackend?.provider || 'github'}
                          size={13}
                        />
                        <span className="truncate">{primaryRepo.full_name}</span>
                        <span className="text-zinc-400 ml-auto shrink-0">
                          {primaryRepo.default_branch}
                        </span>
                      </div>
                    )}
                  </div>

                  <div className="pt-3 border-t border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400">
                    <div className="flex items-center gap-3">
                      <span className="font-semibold text-zinc-800 dark:text-zinc-200">
                        {summary.openSeeds.length} open
                      </span>
                      <span className="font-mono">
                        {formatUsd(summary.totalSpend)}
                      </span>
                      <span className="inline-flex items-center gap-1">
                        <Users size={12} />
                        {summary.members.length}
                      </span>
                    </div>
                    <span className="font-mono text-xs">
                      {formatRelativeTime(summary.lastActivityAt)}
                    </span>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      />

      <ProjectModal
        isOpen={modalOpen}
        onClose={() => {
          setModalOpen(false)
          setEditingProject(null)
        }}
        project={editingProject}
        onSaved={(saved) => {
          if (!editingProject) {
            navigateToDetail(VIEWS.PROJECTS, saved.slug || saved.id)
          }
        }}
      />

      <ConfirmDialog
        isOpen={Boolean(archiveConfirmProject)}
        onCancel={() => setArchiveConfirmProject(null)}
        onConfirm={() => {
          if (archiveConfirmProject) {
            void handleArchiveOrRestore(archiveConfirmProject)
          }
        }}
        title={
          archiveConfirmProject?.archived_at
            ? `Restore ${archiveConfirmProject.name}?`
            : t('projects.dialog.archiveTitle')
        }
        message={
          archiveConfirmProject?.archived_at
            ? `Are you sure you want to restore ${archiveConfirmProject.name} to active status?`
            : t('projects.dialog.archiveMessage', {
                name: archiveConfirmProject?.name || '',
              })
        }
        confirmText={
          archiveConfirmProject?.archived_at
            ? t('projects.actions.unarchive')
            : t('projects.dialog.archiveConfirm')
        }
        confirmVariant={archiveConfirmProject?.archived_at ? 'primary' : 'danger'}
      />
    </>
  )
}
