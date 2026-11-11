import React, { useEffect, useMemo, useState } from 'react'
import {
  Archive,
  BookOpen,
  Bot,
  Edit2,
  Plus,
  Power,
  RotateCcw,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { ActionMenu } from '../components/common/ActionMenu'
import { ConfirmDialog } from '../components/common/ConfirmDialog'
import { RuntimeIcon } from '../components/common/ProviderIcon'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { StageBadge } from '../components/common/StageBadge'
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../components/layout/EntityPageLayout'
import { AgentDrawer } from '../components/modals/AgentDrawer'
import { SkillDrawer } from '../components/modals/SkillDrawer'
import { AgentBenchmarksPanel } from '../components/modals/agent/AgentBenchmarksPanel'
import { SkillsTable, type SkillSortKey } from '../components/modals/agent/SkillsTable'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { usePagination } from '../hooks/usePagination'
import { useTableSort } from '../hooks/useTableSort'
import {
  getAgentActiveRuns,
  getAgentAssignedStages,
  getRuntimeDefinition,
} from '../utils/agents'
import type {
  Agent,
  AgentKind,
  Skill,
} from '../types'

type AgentsPrimaryTab = 'agents' | 'skills' | 'benchmarks'
type AgentStatusFilter = 'enabled' | 'paused' | 'archived' | 'all'

export const AgentsView: React.FC = () => {
  const {
    agents,
    skills,
    policies,
    projects,
    runs,
    activeDetailId,
    clearDetail,
  } = useData()
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [primaryTab, setPrimaryTab] = useState<AgentsPrimaryTab>(() => {
    try {
      const sp = new URLSearchParams(window.location.search)
      const tabParam = sp.get('tab')
      if (tabParam === 'skills') return 'skills'
      if (tabParam === 'benchmarks') return 'benchmarks'
      return 'agents'
    } catch {
      return 'agents'
    }
  })
  const [statusFilter, setStatusFilter] = useState<AgentStatusFilter>('enabled')
  const [searchQuery, setSearchQuery] = useState('')

  // Agent Drawer state
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [selectedAgentId, setSelectedAgentId] = useState<string | null>(null)
  const [defaultNewKind, setDefaultNewKind] = useState<AgentKind>('stage')

  // Skill Drawer state
  const [skillDrawerOpen, setSkillDrawerOpen] = useState(false)
  const [selectedSkillId, setSelectedSkillId] = useState<string | null>(null)

  // Archive Agent confirmation
  const [archiveTarget, setArchiveTarget] = useState<Agent | null>(null)
  const [archiveError, setArchiveError] = useState<string | null>(null)
  const [isArchiving, setIsArchiving] = useState(false)

  // Delete Skill confirmation
  const [deleteSkillTarget, setDeleteSkillTarget] = useState<Skill | null>(null)
  const [deleteSkillError, setDeleteSkillError] = useState<string | null>(null)
  const [isDeletingSkill, setIsDeletingSkill] = useState(false)

  const selectedAgent = useMemo(
    () => (selectedAgentId ? agents.find((a) => a.id === selectedAgentId) || null : null),
    [agents, selectedAgentId]
  )

  const selectedSkill = useMemo(
    () => (selectedSkillId ? skills.find((s) => s.id === selectedSkillId) || null : null),
    [skills, selectedSkillId]
  )

  const handleSelectPrimaryTab = (tab: AgentsPrimaryTab) => {
    setPrimaryTab(tab)
    setSearchQuery('')
    try {
      const sp = new URLSearchParams(window.location.search)
      if (tab === 'skills' || tab === 'benchmarks') {
        sp.set('tab', tab)
      } else {
        sp.delete('tab')
      }
      const qs = sp.toString()
      window.history.pushState(
        null,
        '',
        qs ? `${window.location.pathname}?${qs}` : window.location.pathname
      )
    } catch {
      // Ignore URL errors
    }
  }

  const openAgentDrawer = (
    agent: Agent | null,
    kind: AgentKind = 'stage'
  ) => {
    setSelectedAgentId(agent ? agent.id : null)
    setDefaultNewKind(kind)
    setDrawerOpen(true)
    try {
      const sp = new URLSearchParams(window.location.search)
      sp.set('open', agent ? agent.handle : 'new')
      const qs = sp.toString()
      window.history.pushState(null, '', `${window.location.pathname}?${qs}`)
    } catch {
      // Ignore URL history errors
    }
  }

  const closeAgentDrawer = () => {
    setDrawerOpen(false)
    setSelectedAgentId(null)
    clearDetail()
    try {
      const sp = new URLSearchParams(window.location.search)
      if (sp.has('open')) {
        sp.delete('open')
        const qs = sp.toString()
        window.history.pushState(
          null,
          '',
          qs ? `${window.location.pathname}?${qs}` : window.location.pathname
        )
      }
    } catch {
      // Ignore URL history errors
    }
  }

  const openSkillDrawer = (skill: Skill | null) => {
    setSelectedSkillId(skill ? skill.id : null)
    setSkillDrawerOpen(true)
  }

  const closeSkillDrawer = () => {
    setSkillDrawerOpen(false)
    setSelectedSkillId(null)
  }

  useEffect(() => {
    const syncFromUrl = () => {
      try {
        const sp = new URLSearchParams(window.location.search)
        if (sp.get('tab') === 'skills') {
          setPrimaryTab('skills')
        }
        const openParam = sp.get('open') || activeDetailId
        if (openParam === 'new') {
          setSelectedAgentId(null)
          setDrawerOpen(true)
        } else if (openParam) {
          const clean = openParam.replace(/^@/, '').toLowerCase()
          const found = agents.find(
            (a) => a.id === openParam || a.handle.toLowerCase() === clean
          )
          if (found) {
            setSelectedAgentId(found.id)
            setDrawerOpen(true)
          }
        }
      } catch {
        // Ignore
      }
    }
    syncFromUrl()
    window.addEventListener('popstate', syncFromUrl)
    return () => window.removeEventListener('popstate', syncFromUrl)
  }, [agents, activeDetailId])

  const activeAgents = useMemo(
    () => agents.filter((a) => !a.archived_at),
    [agents]
  )
  const enabledCount = useMemo(
    () => activeAgents.filter((a) => a.enabled).length,
    [activeAgents]
  )
  const pausedCount = useMemo(
    () => activeAgents.filter((a) => !a.enabled).length,
    [activeAgents]
  )
  const archivedCount = useMemo(
    () => agents.filter((a) => Boolean(a.archived_at)).length,
    [agents]
  )

  const handleToggleEnabled = async (agent: Agent) => {
    try {
      await api.agents.update(agent.id, { enabled: !agent.enabled })
      await queryClient.invalidateQueries({ queryKey: queryKeys.agents })
    } catch (err) {
      console.error('Failed to toggle agent status:', err)
    }
  }

  const handleRestoreAgent = async (agent: Agent) => {
    try {
      await api.agents.restore(agent.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      closeAgentDrawer()
    } catch (err) {
      console.error('Failed to restore agent:', err)
    }
  }

  const handleConfirmArchive = async () => {
    if (!archiveTarget || isArchiving) return
    setIsArchiving(true)
    setArchiveError(null)
    try {
      await api.agents.archive(archiveTarget.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      setArchiveTarget(null)
      closeAgentDrawer()
    } catch (err) {
      if (err instanceof ApiError) {
        setArchiveError(err.detail)
      } else {
        setArchiveError(err instanceof Error ? err.message : 'Unable to archive agent')
      }
    } finally {
      setIsArchiving(false)
    }
  }

  const handleConfirmDeleteSkill = async () => {
    if (!deleteSkillTarget || isDeletingSkill) return
    setIsDeletingSkill(true)
    setDeleteSkillError(null)
    try {
      await api.skills.archive(deleteSkillTarget.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.skills }),
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
      ])
      setDeleteSkillTarget(null)
      closeSkillDrawer()
    } catch (err) {
      if (err instanceof ApiError) {
        setDeleteSkillError(err.detail)
      } else {
        setDeleteSkillError(err instanceof Error ? err.message : 'Unable to archive skill')
      }
    } finally {
      setIsDeletingSkill(false)
    }
  }

  const filteredAgents = useMemo(() => {
    return agents.filter((a) => {
      const isArchived = Boolean(a.archived_at)
      if (statusFilter === 'enabled' && (isArchived || !a.enabled)) return false
      if (statusFilter === 'paused' && (isArchived || a.enabled)) return false
      if (statusFilter === 'archived' && !isArchived) return false

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim()
        const matchName = a.name.toLowerCase().includes(q)
        const matchHandle = a.handle.toLowerCase().includes(q)
        const matchModel = a.model.toLowerCase().includes(q)
        const matchRuntime = a.runtime.toLowerCase().includes(q)
        if (!matchName && !matchHandle && !matchModel && !matchRuntime) {
          return false
        }
      }
      return true
    })
  }, [agents, statusFilter, searchQuery])

  const filteredSkills = useMemo(() => {
    if (!searchQuery.trim()) return skills
    const q = searchQuery.toLowerCase().trim()
    return skills.filter(
      (s) =>
        s.name.toLowerCase().includes(q) ||
        s.slug.toLowerCase().includes(q) ||
        (s.description || '').toLowerCase().includes(q)
    )
  }, [skills, searchQuery])

  type AgentSortKey = 'name' | 'stages' | 'runtime' | 'status'

  const {
    sortedItems: sortedAgents,
    sortConfig: agentSortConfig,
    requestSort: requestAgentSort,
  } = useTableSort<Agent, AgentSortKey>({
    items: filteredAgents,
    initialKey: 'name',
    initialDirection: 'asc',
    customComparators: {
      name: (a, b) => a.name.localeCompare(b.name),
      stages: (a, b) =>
        getAgentAssignedStages(b.id, policies).length -
        getAgentAssignedStages(a.id, policies).length,
      runtime: (a, b) =>
        `${a.runtime}:${a.model}`.localeCompare(`${b.runtime}:${b.model}`),
      status: (a, b) => (a.enabled === b.enabled ? 0 : a.enabled ? -1 : 1),
    },
  })

  const {
    sortedItems: sortedSkills,
    sortConfig: skillSortConfig,
    requestSort: requestSkillSort,
  } = useTableSort<Skill, SkillSortKey>({
    items: filteredSkills,
    initialKey: 'name',
    initialDirection: 'asc',
    customComparators: {
      name: (a, b) => a.name.localeCompare(b.name),
      agents: (a, b) => {
        const countA = activeAgents.filter(
          (ag) => ag.skills.includes(a.id) || ag.skills.includes(a.slug)
        ).length
        const countB = activeAgents.filter(
          (ag) => ag.skills.includes(b.id) || ag.skills.includes(b.slug)
        ).length
        return countB - countA
      },
    },
  })

  const { paginatedItems: pagedAgents, paginationProps: agentsPagination } =
    usePagination({
      items: sortedAgents,
      initialPageSize: 10,
    })

  const { paginatedItems: pagedSkills, paginationProps: skillsPagination } =
    usePagination({
      items: sortedSkills,
      initialPageSize: 10,
    })

  const primaryTabItems = [
    { id: 'agents', label: t('agents.title'), count: enabledCount },
    { id: 'skills', label: t('agents.skills.title'), count: skills.length },
    { id: 'benchmarks', label: 'Benchmarks' },
  ]

  const statusTabItems = [
    { id: 'enabled', label: t('agents.tabs.enabled'), count: enabledCount },
    { id: 'paused', label: t('agents.tabs.paused'), count: pausedCount },
    ...(archivedCount > 0
      ? [{ id: 'archived', label: t('agents.tabs.archived'), count: archivedCount }]
      : []),
    { id: 'all', label: t('agents.tabs.all'), count: agents.length },
  ]

  const isSkillsTab = primaryTab === 'skills'
  const isBenchmarksTab = primaryTab === 'benchmarks'

  return (
    <>
      <EntityPageLayout
        title={t('agents.title')}
        subtitle={
          isSkillsTab
            ? t('agents.skills.subtitle')
            : isBenchmarksTab
              ? 'Empirical first-pass approval rate, rework multiplier, and cost-per-seed across agents, runtimes, models, and stages.'
              : t('agents.subtitle')
        }
        count={enabledCount}
        isGlobal
        primaryAction={
          isBenchmarksTab
            ? undefined
            : {
                label: isSkillsTab ? t('agents.skills.create') : t('agents.register'),
                onClick: () =>
                  isSkillsTab ? openSkillDrawer(null) : openAgentDrawer(null, 'stage'),
              }
        }
        storageKey="gitseed_view_agents"
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={
          isSkillsTab
            ? t('agents.skills.searchPlaceholder')
            : isBenchmarksTab
              ? 'Filter benchmarks by @agent, model, runtime, or stage...'
              : t('agents.searchPlaceholder')
        }
        isEmpty={
          isBenchmarksTab
            ? false
            : isSkillsTab
              ? filteredSkills.length === 0
              : filteredAgents.length === 0
        }
        filterTabs={
          <div className="flex items-center gap-3 flex-nowrap shrink-0">
            <SegmentedControl
              items={primaryTabItems}
              selectedId={primaryTab}
              onSelect={(id) => handleSelectPrimaryTab(id as AgentsPrimaryTab)}
            />
            {primaryTab === 'agents' && (
              <>
                <div className="h-5 w-px bg-zinc-200 dark:bg-zinc-800 shrink-0" />
                <SegmentedControl
                  items={statusTabItems}
                  selectedId={statusFilter}
                  onSelect={(id) => setStatusFilter(id as AgentStatusFilter)}
                />
              </>
            )}
          </div>
        }
        emptyMessage={
          isSkillsTab ? (
            <div className="flex flex-col items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center">
                <BookOpen size={20} />
              </div>
              <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
                {searchQuery
                  ? 'No skills match the current search'
                  : 'No skills defined yet'}
              </h3>
              <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
                Create a reusable Markdown skill playbook and attach it to any agent.
              </p>
              {searchQuery ? (
                <button
                  type="button"
                  onClick={() => setSearchQuery('')}
                  className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  Clear search
                </button>
              ) : (
                <Button
                  type="button"
                  variant="primary"
                  size="sm"
                  onClick={() => openSkillDrawer(null)}
                  className="mt-1 rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-teal-600/20 cursor-pointer"
                >
                  <Plus size={14} />
                  <span>Create your first Skill</span>
                </Button>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
                <Bot size={20} />
              </div>
              <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
                {t('agents.noMatch')}
              </h3>
              {(statusFilter !== 'enabled' || searchQuery) && (
                <button
                  type="button"
                  onClick={() => {
                    setStatusFilter('enabled')
                    setSearchQuery('')
                  }}
                  className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {t('agents.clearFilter')}
                </button>
              )}
            </div>
          )
        }
        pagination={
          isBenchmarksTab
            ? undefined
            : isSkillsTab
              ? skillsPagination
              : agentsPagination
        }
        renderList={() =>
          isBenchmarksTab ? (
            <AgentBenchmarksPanel
              projects={projects}
              searchQuery={searchQuery}
            />
          ) : isSkillsTab ? (
            <SkillsTable
              skills={pagedSkills}
              agents={agents}
              selectedSkillId={selectedSkillId}
              sortKey={skillSortConfig.key}
              sortDirection={skillSortConfig.direction}
              onSort={requestSkillSort}
              onSelectSkill={(skill) => openSkillDrawer(skill)}
              onDeleteSkill={(skill) => {
                setDeleteSkillError(null)
                setDeleteSkillTarget(skill)
              }}
            />
          ) : (
            <EntityTableCard>
              <EntityTableHeaderRow>
                <SortableTh<AgentSortKey>
                  sortKey="name"
                  currentSortKey={agentSortConfig.key}
                  currentSortDirection={agentSortConfig.direction}
                  onSort={requestAgentSort}
                >
                  {t('agents.table.agent')}
                </SortableTh>
                <SortableTh<AgentSortKey>
                  sortKey="stages"
                  currentSortKey={agentSortConfig.key}
                  currentSortDirection={agentSortConfig.direction}
                  onSort={requestAgentSort}
                >
                  {t('agents.table.usedIn')}
                </SortableTh>
                <SortableTh<AgentSortKey>
                  sortKey="runtime"
                  currentSortKey={agentSortConfig.key}
                  currentSortDirection={agentSortConfig.direction}
                  onSort={requestAgentSort}
                >
                  {t('agents.table.runtimeModel')}
                </SortableTh>
                <th className="py-3 px-4 text-left font-semibold text-zinc-700 dark:text-zinc-300">
                  First-Pass · $/Seed
                </th>
                <SortableTh<AgentSortKey>
                  sortKey="status"
                  currentSortKey={agentSortConfig.key}
                  currentSortDirection={agentSortConfig.direction}
                  onSort={requestAgentSort}
                >
                  {t('agents.table.status')}
                </SortableTh>
                <th className="py-3 px-4 text-right font-semibold text-zinc-700 dark:text-zinc-300">
                  {t('agents.table.actions')}
                </th>
              </EntityTableHeaderRow>
              <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800/60">
                {pagedAgents.map((agent) => {
                  const assignedStages = getAgentAssignedStages(agent.id, policies)
                  const activeRuns = getAgentActiveRuns(agent.id, runs)
                  const runtimeDef = getRuntimeDefinition(agent.runtime)
                  const isArchived = Boolean(agent.archived_at)
                  const isSelected = selectedAgentId === agent.id

                  return (
                    <tr
                      key={agent.id}
                      onClick={() => openAgentDrawer(agent)}
                      className={`transition-colors cursor-pointer ${isSelected
                          ? 'bg-teal-500/[0.06] dark:bg-teal-500/10'
                          : 'hover:bg-zinc-100/80 dark:hover:bg-zinc-800/40'
                        } ${!agent.enabled || isArchived ? 'opacity-60' : ''}`}
                    >
                      <td className="py-3.5 px-4">
                        <div className="flex items-center gap-3 min-w-0">
                          <div className="w-8 h-8 rounded-xl bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200/80 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
                            <Bot size={15} />
                          </div>
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="font-semibold text-xs text-zinc-900 dark:text-zinc-100 truncate">
                              {agent.name}
                            </span>
                            <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                              @{agent.handle}
                            </span>
                          </div>
                        </div>
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {agent.kind === 'assistant' ? (
                          <span className="text-xs text-zinc-600 dark:text-zinc-300">
                            {t('agents.systemAssistant.badge')}
                          </span>
                        ) : assignedStages.length > 0 ? (
                          <div className="flex items-center gap-1.5 flex-wrap">
                            {assignedStages.map((st) => (
                              <StageBadge key={st} stage={st} />
                            ))}
                          </div>
                        ) : (
                          <span className="text-xs text-zinc-400 dark:text-zinc-500">
                            Unassigned
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        <div className="flex items-center gap-1.5 text-xs">
                          <RuntimeIcon runtime={agent.runtime} size={13} />
                          <span className="font-medium text-zinc-800 dark:text-zinc-200">
                            {runtimeDef.shortLabel}
                          </span>
                          <span className="text-zinc-400">·</span>
                          <span className="font-mono text-zinc-500 dark:text-zinc-400">
                            {agent.model}
                          </span>
                        </div>
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {agent.run_count > 0 ? (
                          <div className="flex items-center gap-2 text-xs font-mono">
                            <span
                              className={
                                (agent.first_pass_rate ?? 0) >= 80
                                  ? 'font-semibold text-emerald-600 dark:text-emerald-400'
                                  : (agent.first_pass_rate ?? 0) >= 50
                                    ? 'font-semibold text-amber-600 dark:text-amber-400'
                                    : 'font-semibold text-rose-600 dark:text-rose-400'
                              }
                            >
                              {agent.first_pass_rate != null
                                ? `${agent.first_pass_rate.toFixed(0)}%`
                                : '-'}
                            </span>
                            <span className="text-zinc-400">·</span>
                            <span className="text-zinc-700 dark:text-zinc-300">
                              ${Number(agent.avg_cost_per_seed || 0).toFixed(2)}/seed
                            </span>
                          </div>
                        ) : (
                          <span className="text-xs text-zinc-400 dark:text-zinc-500">
                            No runs yet
                          </span>
                        )}
                      </td>

                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {activeRuns.length > 0 ? (
                          <span className="inline-flex items-center gap-1.5 text-xs font-medium text-sky-600 dark:text-sky-400">
                            <span className="w-2 h-2 rounded-full bg-sky-500 animate-pulse" />
                            <span>Running ({activeRuns.length})</span>
                          </span>
                        ) : isArchived ? (
                          <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400">
                            <span className="w-2 h-2 rounded-full bg-zinc-400" />
                            <span>Archived</span>
                          </span>
                        ) : agent.enabled ? (
                          <span className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
                            <span className="w-2 h-2 rounded-full bg-emerald-500" />
                            <span>{t('agents.status.enabled')}</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
                            <span className="w-2 h-2 rounded-full bg-zinc-400" />
                            <span>{t('agents.status.paused')}</span>
                          </span>
                        )}
                      </td>

                      <td
                        className="py-3.5 px-4 text-right whitespace-nowrap"
                        onClick={(e) => e.stopPropagation()}
                      >
                        <ActionMenu
                          items={[
                            {
                              label: t('agents.actions.edit'),
                              icon: <Edit2 size={14} />,
                              onClick: () => openAgentDrawer(agent),
                            },
                            ...(!isArchived
                              ? [
                                {
                                  label: agent.enabled
                                    ? t('agents.actions.pause')
                                    : t('agents.actions.enable'),
                                  icon: <Power size={14} />,
                                  onClick: () => handleToggleEnabled(agent),
                                },
                                {
                                  label: 'Archive Agent',
                                  icon: <Archive size={14} />,
                                  variant: 'danger' as const,
                                  onClick: () => {
                                    setArchiveError(null)
                                    setArchiveTarget(agent)
                                  },
                                },
                              ]
                              : [
                                {
                                  label: 'Restore Agent',
                                  icon: <RotateCcw size={14} />,
                                  onClick: () => handleRestoreAgent(agent),
                                },
                              ]),
                          ]}
                        />
                      </td>
                    </tr>
                  )
                })}
              </tbody>
            </EntityTableCard>
          )
        }
      />

      <AgentDrawer
        isOpen={drawerOpen}
        onClose={closeAgentDrawer}
        agentToEdit={selectedAgent}
        defaultKind={defaultNewKind}
        onArchive={(agent) => {
          setArchiveError(null)
          setArchiveTarget(agent)
        }}
        onRestore={handleRestoreAgent}
        onNavigateToSkills={() => {
          handleSelectPrimaryTab('skills')
          openSkillDrawer(null)
        }}
      />

      <SkillDrawer
        isOpen={skillDrawerOpen}
        onClose={closeSkillDrawer}
        skillToEdit={selectedSkill}
        onDelete={(skill) => {
          setDeleteSkillError(null)
          setDeleteSkillTarget(skill)
        }}
      />

      <ConfirmDialog
        isOpen={Boolean(archiveTarget)}
        onCancel={() => {
          if (!isArchiving) {
            setArchiveTarget(null)
            setArchiveError(null)
          }
        }}
        onConfirm={handleConfirmArchive}
        title={
          archiveTarget
            ? `Archive @${archiveTarget.handle}?`
            : 'Archive Agent?'
        }
        message={
          archiveError ||
          (archiveTarget
            ? `This will archive ${archiveTarget.name} (@${archiveTarget.handle}) and remove it from active dispatch. You can restore it later from the Archived tab.`
            : '')
        }
        confirmText={isArchiving ? 'Archiving...' : 'Archive Agent'}
        confirmVariant="danger"
        isLoading={isArchiving}
      />

      <ConfirmDialog
        isOpen={Boolean(deleteSkillTarget)}
        onCancel={() => {
          if (!isDeletingSkill) {
            setDeleteSkillTarget(null)
            setDeleteSkillError(null)
          }
        }}
        onConfirm={handleConfirmDeleteSkill}
        title={
          deleteSkillTarget
            ? `Archive skill "${deleteSkillTarget.name}"?`
            : 'Archive Skill?'
        }
        message={
          deleteSkillError ||
          (deleteSkillTarget
            ? `This will archive the skill "${deleteSkillTarget.name}" (${deleteSkillTarget.slug}). Skills attached to an active agent cannot be archived until detached.`
            : '')
        }
        confirmText={isDeletingSkill ? 'Archiving...' : 'Archive Skill'}
        confirmVariant="danger"
        isLoading={isDeletingSkill}
      />
    </>
  )
}
