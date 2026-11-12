import React, { useCallback, useEffect, useMemo, useState } from 'react'
import { Tooltip } from '../components/common/Tooltip'
import {
  AlertTriangle,
  Archive,
  Edit2,
  ExternalLink,
  FolderKanban,
  Lightbulb,
  Plus,
  RotateCcw,
  Sprout,
  X,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { ActionMenu } from '../components/common/ActionMenu'
import { SdlcPipelineStrip } from '../components/common/SdlcPipelineStrip'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { StageBadge } from '../components/common/StageBadge'
import { SeedDetail } from '../components/details/SeedDetail'
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../components/layout/EntityPageLayout'
import { SeedModal } from '../components/modals/SeedModal'
import { SeedsBoardView } from '../components/seeds/SeedsBoardView'
import { VIEWS, NEW_SEED_HINT } from '../constants'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { usePagination } from '../hooks/usePagination'
import { useTableSort } from '../hooks/useTableSort'
import {
  formatRelativeTime,
  formatUsd,
  getSeedAttentionReason,
  seedNeedsAttention,
} from '../utils/format'
import { canUserContributeToProject } from '../utils/projects'
import {
  AUTONOMY_LABELS,
  getSeedStatusClasses,
  isSeedOpen,
  SEED_STAGE_ORDER,
  STATUS_LABELS,
} from '../utils/seeds'
import type { Seed, SeedStage } from '../types'

type SeedStatusFilter =
  | 'open'
  | 'needs_you'
  | 'running'
  | 'queued'
  | 'blocked'
  | 'stopped'
  | 'done'
  | 'all'

/** One predicate per chip, shared by counts, filtering and stage auto-selection. */
const STATUS_FILTER_MATCHERS: Record<Exclude<SeedStatusFilter, 'open' | 'needs_you' | 'all'>, (s: Seed) => boolean> = {
  running: (s) => s.status === 'running',
  // Waiting to be picked up: queued for a runner, or staged until someone starts it
  queued: (s) => s.status === 'queued' || s.status === 'staged',
  blocked: (s) => s.status === 'blocked' || s.status === 'failed',
  stopped: (s) => s.status === 'stopped',
  done: (s) => s.status === 'merged' || s.status === 'closed',
}

type SeedSortKey =
  | 'number'
  | 'project'
  | 'stage'
  | 'status'
  | 'autonomy'
  | 'spent'
  | 'updated'

export interface SeedsViewProps {
  embeddedProjectId?: string
  initialStageFilter?: 'all' | SeedStage
}

export const SeedsView: React.FC<SeedsViewProps> = ({
  embeddedProjectId,
  initialStageFilter,
}) => {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const {
    seeds: contextSeeds,
    allSeeds,
    projects,
    projectMembersByProject,
    selectedProject,
    activeDetailId,
    viewFilterParams,
    navigateToDetail,
    setActiveView,
    openSearchModal,
  } = useData()

  const seeds = useMemo(
    () =>
      embeddedProjectId
        ? allSeeds.filter((s) => s.project_id === embeddedProjectId)
        : contextSeeds,
    [embeddedProjectId, allSeeds, contextSeeds]
  )
  const isProjectScoped = Boolean(embeddedProjectId || selectedProject)

  const [stageFilter, setStageFilter] = useState<'all' | SeedStage>(() => {
    if (initialStageFilter) return initialStageFilter
    const paramStage = viewFilterParams?.stage as SeedStage | undefined
    if (paramStage && SEED_STAGE_ORDER.includes(paramStage)) {
      return paramStage
    }
    return 'all'
  })
  const [actionError, setActionError] = useState<string | null>(null)
  const [statusFilter, setStatusFilter] = useState<SeedStatusFilter>(() => {
    const initialStage =
      initialStageFilter || (viewFilterParams?.stage as SeedStage | undefined)
    if (initialStage === 'merge') return 'all'
    return 'open'
  })
  const [searchQuery, setSearchQuery] = useState('')
  const [isCreateModalOpen, setIsCreateModalOpen] = useState(false)
  const [editingSeed, setEditingSeed] = useState<Seed | null>(null)
  const [hideMergeColumn, setHideMergeColumn] = useState<boolean>(() => {
    try {
      return localStorage.getItem('gitseed_seeds_board_hide_merge') === 'true'
    } catch {
      return false
    }
  })

  const toggleHideMergeColumn = () => {
    setHideMergeColumn((prev) => {
      const next = !prev
      try {
        localStorage.setItem('gitseed_seeds_board_hide_merge', String(next))
      } catch {
        // Ignore localStorage errors
      }
      return next
    })
  }

  const matchesStatusFilter = useCallback(
    (seed: Seed, status: SeedStatusFilter): boolean => {
      if (status === 'open') return isSeedOpen(seed)
      if (status === 'needs_you') return seedNeedsAttention(seed)
      if (status === 'all') return true
      return STATUS_FILTER_MATCHERS[status](seed)
    },
    []
  )

  const applyStageSelection = useCallback(
    (nextStage: 'all' | SeedStage) => {
      setStageFilter(nextStage)
      if (nextStage === 'all') {
        if (statusFilter === 'all' && seeds.some((s) => isSeedOpen(s))) {
          setStatusFilter('open')
        }
        return
      }
      if (nextStage === 'merge') {
        const hasCurrentMatches = seeds.some(
          (s) =>
            (s.stage === 'merge' || s.status === 'merged') &&
            matchesStatusFilter(s, statusFilter)
        )
        if (!hasCurrentMatches) {
          const hasOpenMerge = seeds.some(
            (s) => s.stage === 'merge' && isSeedOpen(s)
          )
          setStatusFilter(hasOpenMerge ? 'all' : 'done')
        }
        return
      }
      const hasStageMatches = seeds.some(
        (s) => s.stage === nextStage && matchesStatusFilter(s, statusFilter)
      )
      if (!hasStageMatches) {
        const hasOpenStageMatches = seeds.some(
          (s) => s.stage === nextStage && isSeedOpen(s)
        )
        setStatusFilter(hasOpenStageMatches ? 'open' : 'all')
      }
    },
    [matchesStatusFilter, seeds, statusFilter]
  )

  useEffect(() => {
    if (initialStageFilter) {
      applyStageSelection(initialStageFilter)
    }
  }, [initialStageFilter, applyStageSelection])

  useEffect(() => {
    if (embeddedProjectId) return
    const paramStage = viewFilterParams?.stage as SeedStage | undefined
    if (paramStage && SEED_STAGE_ORDER.includes(paramStage)) {
      applyStageSelection(paramStage)
    }
  }, [embeddedProjectId, viewFilterParams, applyStageSelection])

  const projectById = useMemo(() => {
    const map = new Map<string, (typeof projects)[number]>()
    for (const p of projects) map.set(p.id, p)
    return map
  }, [projects])

  const statusCounts = useMemo(() => {
    return {
      open: seeds.filter((s) => isSeedOpen(s)).length,
      needs_you: seeds.filter((s) => seedNeedsAttention(s)).length,
      running: seeds.filter(STATUS_FILTER_MATCHERS.running).length,
      queued: seeds.filter(STATUS_FILTER_MATCHERS.queued).length,
      blocked: seeds.filter(STATUS_FILTER_MATCHERS.blocked).length,
      stopped: seeds.filter(STATUS_FILTER_MATCHERS.stopped).length,
      done: seeds.filter(STATUS_FILTER_MATCHERS.done).length,
      all: seeds.length,
    }
  }, [seeds])

  const filteredSeeds = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return seeds.filter((seed) => {
      if (
        stageFilter !== 'all' &&
        (stageFilter === 'merge'
          ? seed.stage !== 'merge' && seed.status !== 'merged'
          : seed.stage !== stageFilter)
      ) {
        return false
      }

      if (!matchesStatusFilter(seed, statusFilter)) return false

      if (q) {
        const proj = projectById.get(seed.project_id)
        const refStr = proj ? `${proj.slug}#${seed.number}` : `#${seed.number}`
        const matches =
          refStr.toLowerCase().includes(q) ||
          `#${seed.number}`.includes(q) ||
          seed.title.toLowerCase().includes(q) ||
          (seed.description || '').toLowerCase().includes(q) ||
          seed.tags.some((t) => t.toLowerCase().includes(q))
        if (!matches) return false
      }

      return true
    })
  }, [seeds, stageFilter, statusFilter, searchQuery, projectById, matchesStatusFilter])

  const customComparators = useMemo<
    Record<SeedSortKey, (a: Seed, b: Seed) => number>
  >(
    () => ({
      number: (a, b) => a.number - b.number,
      project: (a, b) => {
        const pa = projectById.get(a.project_id)?.slug || ''
        const pb = projectById.get(b.project_id)?.slug || ''
        return pa.localeCompare(pb)
      },
      stage: (a, b) =>
        SEED_STAGE_ORDER.indexOf(a.stage) - SEED_STAGE_ORDER.indexOf(b.stage),
      status: (a, b) => a.status.localeCompare(b.status),
      autonomy: (a, b) => a.autonomy.localeCompare(b.autonomy),
      spent: (a, b) => parseFloat(a.spent || '0') - parseFloat(b.spent || '0'),
      updated: (a, b) =>
        new Date(a.updated_at || a.created_at || 0).getTime() -
        new Date(b.updated_at || b.created_at || 0).getTime(),
    }),
    [projectById]
  )

  const { sortedItems, sortConfig, requestSort } = useTableSort<
    Seed,
    SeedSortKey
  >({
    items: filteredSeeds,
    initialKey: 'updated',
    initialDirection: 'desc',
    customComparators,
  })

  const { paginatedItems, paginationProps } = usePagination({
    items: sortedItems,
    initialPageSize: 15,
  })

  const mergeColumnSeeds = useMemo(() => {
    if (stageFilter !== 'all' && stageFilter !== 'merge') return []
    const q = searchQuery.trim().toLowerCase()
    const base =
      statusFilter === 'open'
        ? seeds.filter(
            (s) => (s.stage === 'merge' && isSeedOpen(s)) || s.status === 'merged'
          )
        : sortedItems.filter((s) => s.stage === 'merge' || s.status === 'merged')
    const matched = q
      ? base.filter((seed) => {
          const proj = projectById.get(seed.project_id)
          const refStr = proj ? `${proj.slug}#${seed.number}` : `#${seed.number}`
          return (
            refStr.toLowerCase().includes(q) ||
            `#${seed.number}`.includes(q) ||
            seed.title.toLowerCase().includes(q) ||
            (seed.description || '').toLowerCase().includes(q) ||
            seed.tags.some((t) => t.toLowerCase().includes(q))
          )
        })
      : base
    return [...matched].sort(
      (a, b) =>
        new Date(b.updated_at || b.created_at || 0).getTime() -
        new Date(a.updated_at || a.created_at || 0).getTime()
    )
  }, [seeds, sortedItems, statusFilter, stageFilter, searchQuery, projectById])

  if (!embeddedProjectId && activeDetailId) {
    return (
      <SeedDetail
        seedId={activeDetailId}
        onBack={() => setActiveView(VIEWS.SEEDS)}
      />
    )
  }

  const handleOpenSeed = (seed: Seed) => {
    navigateToDetail(VIEWS.SEEDS, seed.id)
  }

  const handleCloseOrReopen = async (seed: Seed) => {
    setActionError(null)
    try {
      if (seed.status === 'closed') {
        await api.seeds.reopen(seed.id)
      } else {
        await api.seeds.close(seed.id)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({ queryKey: queryKeys.seedDetail(seed.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      setActionError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : `Failed to ${seed.status === 'closed' ? 'reopen' : 'close'} seed #${seed.number}.`
      )
    }
  }

  const hasActiveProjects = projects.some((p) => !p.archived_at)
  const hasFiltersActive =
    Boolean(searchQuery.trim()) ||
    statusFilter !== 'open' ||
    stageFilter !== 'all'

  return (
    <>
      {actionError && (
        <div
          role="alert"
          className="mb-4 p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start justify-between gap-2 text-xs text-rose-700 dark:text-rose-300"
        >
          <div className="flex items-start gap-2 min-w-0">
            <AlertTriangle size={14} className="shrink-0 mt-0.5 text-rose-500" />
            <span className="break-words">{actionError}</span>
          </div>
          <button
            type="button"
            onClick={() => setActionError(null)}
            aria-label="Dismiss error"
            className="text-rose-500 hover:text-rose-700 dark:hover:text-rose-200 cursor-pointer shrink-0"
          >
            <X size={14} />
          </button>
        </div>
      )}
      <EntityPageLayout
        embedded={Boolean(embeddedProjectId)}
        title={t('seeds.title')}
        subtitle={t('seeds.subtitle')}
        count={statusCounts.open}
        storageKey="gitseed_seeds_view_mode"
        showViewModeToggle
        primaryAction={
          embeddedProjectId ? undefined : (
            <div className="flex items-center gap-2.5 shrink-0">
              <Button
                variant="secondary"
                size="md"
                onClick={() => openSearchModal('brainstorm', selectedProject)}
                className="flex items-center gap-2 font-semibold shrink-0 rounded-xl border border-zinc-200 dark:border-zinc-700/80 cursor-pointer"
              >
                <Lightbulb size={16} />
                <span>Brainstorm</span>
              </Button>
              <Tooltip content={NEW_SEED_HINT} placement="bottom">
                <Button
                  variant="primary"
                  size="md"
                  onClick={() => {
                    setEditingSeed(null)
                    setIsCreateModalOpen(true)
                  }}
                  className="flex items-center gap-2 font-semibold shadow-lg shadow-teal-600/20 dark:shadow-teal-500/20 shrink-0 rounded-xl cursor-pointer"
                >
                  <Plus size={16} />
                  <span>{t('seeds.create')}</span>
                </Button>
              </Tooltip>
            </div>
          )
        }
        metrics={
          <SdlcPipelineStrip
            seeds={seeds}
            selectedStage={stageFilter === 'all' ? null : stageFilter}
            onSelectStage={(stage) =>
              applyStageSelection(stageFilter === stage ? 'all' : stage)
            }
          />
        }
        filterTabs={
          <SegmentedControl<SeedStatusFilter>
            ariaLabel="Filter seeds by status"
            selectedId={statusFilter}
            onSelect={setStatusFilter}
            items={[
              { id: 'open', label: t('seeds.filters.open'), count: statusCounts.open },
              {
                id: 'needs_you',
                label: t('seeds.filters.needsYou'),
                count: statusCounts.needs_you,
              },
              { id: 'running', label: t('seeds.filters.running'), count: statusCounts.running },
              { id: 'queued', label: t('seeds.filters.queued'), count: statusCounts.queued },
              { id: 'blocked', label: t('seeds.filters.blocked'), count: statusCounts.blocked },
              // Only when something is actually stopped, to keep the row short
              ...(statusCounts.stopped > 0 || statusFilter === 'stopped'
                ? [{ id: 'stopped' as const, label: t('seeds.filters.stopped'), count: statusCounts.stopped }]
                : []),
              // Merged + closed in one chip (finished work either way)
              { id: 'done', label: t('seeds.filters.done'), count: statusCounts.done },
              { id: 'all', label: t('seeds.filters.all'), count: statusCounts.all },
            ]}
          />
        }
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t('seeds.searchPlaceholder')}
        isEmpty={sortedItems.length === 0}
        emptyMessage={
          seeds.length === 0 ? (
            <div className="flex flex-col items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center">
                <Sprout size={20} />
              </div>
              <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
                {t('seeds.emptyTitle')}
              </h3>
              <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
                {t('seeds.emptySubtitle')}
              </p>
              {hasActiveProjects && (
                <Button
                  variant="primary"
                  size="sm"
                  onClick={() => {
                    setEditingSeed(null)
                    setIsCreateModalOpen(true)
                  }}
                  className="mt-1 rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-teal-600/20 cursor-pointer"
                >
                  <Plus size={14} />
                  <span>{t('seeds.createFirst')}</span>
                </Button>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center gap-3">
              <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
                <Sprout size={20} />
              </div>
              <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
                {t('seeds.noMatch')}
              </h3>
              {hasFiltersActive && (
                <button
                  type="button"
                  onClick={() => {
                    setSearchQuery('')
                    setStatusFilter('open')
                    setStageFilter('all')
                  }}
                  className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {t('seeds.clearFilters')}
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
                sortKey="number"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="w-full"
              >
                {t('seeds.table.seed')}
              </SortableTh>
              {!isProjectScoped && (
                <SortableTh
                  sortKey="project"
                  currentSortKey={sortConfig.key}
                  currentSortDirection={sortConfig.direction}
                  onSort={requestSort}
                >
                  {t('seeds.table.project')}
                </SortableTh>
              )}
              <SortableTh
                sortKey="stage"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('seeds.table.stage')}
              </SortableTh>
              <SortableTh
                sortKey="status"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('seeds.table.status')}
              </SortableTh>
              <SortableTh
                sortKey="autonomy"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('seeds.table.autonomy')}
              </SortableTh>
              <SortableTh
                sortKey="spent"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('seeds.table.spend')}
              </SortableTh>
              <SortableTh
                sortKey="updated"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('seeds.table.updated')}
              </SortableTh>
              <th className="py-3 px-4 text-right font-semibold">
                {t('seeds.table.actions')}
              </th>
            </EntityTableHeaderRow>

            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-700/80">
              {paginatedItems.map((seed) => {
                const proj = projectById.get(seed.project_id)
                const members = proj
                  ? projectMembersByProject[proj.id] || []
                  : []
                const canContribute = proj
                  ? canUserContributeToProject(user, proj, members)
                  : false
                const needsYou = seedNeedsAttention(seed)

                return (
                  <tr
                    key={seed.id}
                    onClick={() => handleOpenSeed(seed)}
                    className="hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40 transition-colors cursor-pointer"
                  >
                    {/* 1. Seed Identity */}
                    <td className="py-3.5 px-4 w-full max-w-0 min-w-[200px] sm:min-w-[260px]">
                      <div className="flex flex-col min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <span className="font-mono font-bold text-xs text-zinc-500 dark:text-zinc-400 shrink-0">
                            #{seed.number}
                          </span>
                          <span
                            title={seed.title}
                            className="font-semibold text-sm text-zinc-900 dark:text-zinc-100 truncate"
                          >
                            {seed.title}
                          </span>
                        </div>
                        {(needsYou || seed.tags.length > 0) && (
                          <div className="flex items-center gap-2 flex-wrap mt-1">
                            {needsYou && (
                              <span className="inline-flex items-center gap-1 text-[11px] font-medium text-amber-600 dark:text-amber-400">
                                <AlertTriangle size={11} />
                                <span>{getSeedAttentionReason(seed)}</span>
                              </span>
                            )}
                            {seed.tags.slice(0, 3).map((tag) => (
                              <span
                                key={tag}
                                className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[10px] font-mono text-zinc-600 dark:text-zinc-400 border border-zinc-200/70 dark:border-zinc-700/70"
                              >
                                #{tag}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    </td>

                    {/* 2. Project */}
                    {!isProjectScoped && (
                      <td className="py-3.5 px-4 whitespace-nowrap">
                        {proj ? (
                          <span className="inline-flex items-center gap-1.5 font-mono text-xs font-medium text-zinc-700 dark:text-zinc-300">
                            <FolderKanban size={13} className="text-zinc-400" />
                            <span>{proj.slug}</span>
                          </span>
                        ) : (
                          '-'
                        )}
                      </td>
                    )}

                    {/* 3. Stage */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <StageBadge
                        stage={seed.stage}
                        isClosed={seed.status === 'closed'}
                      />
                    </td>

                    {/* 4. Status */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center px-2 py-0.5 rounded-md border text-[11px] font-semibold ${getSeedStatusClasses(
                          seed.status
                        )}`}
                      >
                        {STATUS_LABELS[seed.status]}
                      </span>
                    </td>

                    {/* 5. Autonomy */}
                    <td className="py-3.5 px-4 whitespace-nowrap text-xs font-medium text-zinc-600 dark:text-zinc-400">
                      {AUTONOMY_LABELS[seed.autonomy]}
                    </td>

                    {/* 6. Spend */}
                    <td className="py-3.5 px-4 whitespace-nowrap font-mono text-xs text-zinc-700 dark:text-zinc-300">
                      <span className="font-semibold">
                        {formatUsd(seed.spent)}
                      </span>
                      {seed.budget_cap && (
                        <span className="text-zinc-400">
                          {' '}
                          / {formatUsd(seed.budget_cap)}
                        </span>
                      )}
                    </td>

                    {/* 7. Updated */}
                    <td className="py-3.5 px-4 whitespace-nowrap font-mono text-xs text-zinc-500 dark:text-zinc-400">
                      {formatRelativeTime(seed.updated_at)}
                    </td>

                    {/* 8. Actions */}
                    <td
                      className="py-3.5 px-4 text-right whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ActionMenu
                        items={[
                          {
                            label: t('seeds.actions.open'),
                            icon: <ExternalLink size={14} />,
                            onClick: () => handleOpenSeed(seed),
                          },
                          ...(canContribute
                            ? [
                                {
                                  label: t('seeds.actions.edit'),
                                  icon: <Edit2 size={14} />,
                                  onClick: () => {
                                    setEditingSeed(seed)
                                    setIsCreateModalOpen(true)
                                  },
                                },
                                ...(seed.status !== 'merged'
                                  ? [
                                      {
                                        label:
                                          seed.status === 'closed'
                                            ? t('seeds.actions.reopen')
                                            : t('seeds.actions.close'),
                                        icon:
                                          seed.status === 'closed' ? (
                                            <RotateCcw size={14} />
                                          ) : (
                                            <Archive size={14} />
                                          ),
                                        variant:
                                          seed.status === 'closed'
                                            ? ('default' as const)
                                            : ('danger' as const),
                                        onClick: () => {
                                          void handleCloseOrReopen(seed)
                                        },
                                      },
                                    ]
                                  : []),
                              ]
                            : []),
                        ]}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </EntityTableCard>
        )}
        renderBoard={() => (
          <SeedsBoardView
            sortedItems={sortedItems}
            mergeColumnSeeds={mergeColumnSeeds}
            projectById={projectById}
            isProjectScoped={isProjectScoped}
            hideMergeColumn={hideMergeColumn}
            onToggleHideMergeColumn={toggleHideMergeColumn}
            onOpenSeed={handleOpenSeed}
          />
        )}
      />

      <SeedModal
        isOpen={isCreateModalOpen}
        onClose={() => {
          setIsCreateModalOpen(false)
          setEditingSeed(null)
        }}
        seed={editingSeed}
        defaultProjectId={embeddedProjectId || selectedProject || null}
        onSaved={(saved) => {
          if (!editingSeed) {
            navigateToDetail(VIEWS.SEEDS, saved.id)
          }
        }}
      />
    </>
  )
}
