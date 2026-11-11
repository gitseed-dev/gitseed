import React, { useMemo, useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronRight,
  ShieldCheck,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { StageBadge } from './StageBadge'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import {
  formatUsd,
  getSeedAttentionReason,
  seedNeedsAttention,
} from '../../utils/format'
import type { Seed, SeedStatus } from '../../types'

type AttentionFilter = 'all' | 'awaiting_approval' | 'blocked' | 'failed'

export interface OperatorReviewPanelProps {
  /** Seeds to evaluate for operator attention (defaults to DataContext scoped seeds) */
  seeds?: Seed[]
  /** Optional project ID scope when used inside ProjectDetail */
  projectId?: string
  /** Whether to render the "All nominal" card when 0 items need attention (default: true) */
  showNominalState?: boolean
  /** Hide the project slug badge on rows (auto-hidden when projectId or selectedProject is set) */
  hideProjectBadge?: boolean
  /** Callback when clicking "View all" */
  onViewAll?: () => void
  /** Callback when clicking a seed row */
  onSelectSeed?: (seedId: string) => void
  /** Maximum rows to display before showing footer link (default: 10) */
  maxItems?: number
  className?: string
}

export const OperatorReviewPanel: React.FC<OperatorReviewPanelProps> = ({
  seeds: propSeeds,
  projectId,
  showNominalState = true,
  hideProjectBadge,
  onViewAll,
  onSelectSeed,
  maxItems = 10,
  className = '',
}) => {
  const { t } = useTranslation()
  const {
    seeds: contextSeeds,
    allSeeds,
    projects,
    selectedProject,
    isInitialLoading,
    setActiveView,
    navigateToDetail,
  } = useData()

  const [filter, setFilter] = useState<AttentionFilter>('all')

  const sourceSeeds = useMemo(() => {
    if (propSeeds) return propSeeds
    if (projectId) return allSeeds.filter((s) => s.project_id === projectId)
    return contextSeeds
  }, [propSeeds, projectId, allSeeds, contextSeeds])

  const projectById = useMemo(
    () => new Map(projects.map((p) => [p.id, p])),
    [projects]
  )

  const shouldHideProject =
    hideProjectBadge ?? Boolean(projectId || selectedProject)

  const attentionSeeds = useMemo(() => {
    const priorityOrder: Record<SeedStatus, number> = {
      blocked: 0,
      failed: 1,
      awaiting_approval: 2,
      stopped: 3,
      running: 4,
      queued: 5,
      staged: 6,
      merged: 7,
      closed: 8,
    }
    return sourceSeeds
      .filter(seedNeedsAttention)
      .sort((a, b) => {
        const pDiff = (priorityOrder[a.status] ?? 9) - (priorityOrder[b.status] ?? 9)
        if (pDiff !== 0) return pDiff
        const timeA = new Date(a.updated_at || a.created_at || 0).getTime()
        const timeB = new Date(b.updated_at || b.created_at || 0).getTime()
        return timeA - timeB
      })
  }, [sourceSeeds])

  const counts = useMemo(() => {
    let awaitingApproval = 0
    let blocked = 0
    let failed = 0
    for (const s of attentionSeeds) {
      if (s.status === 'awaiting_approval') awaitingApproval += 1
      else if (s.status === 'blocked') blocked += 1
      else if (s.status === 'failed') failed += 1
    }
    return {
      all: attentionSeeds.length,
      awaiting_approval: awaitingApproval,
      blocked: blocked,
      failed,
    }
  }, [attentionSeeds])

  const filteredSeeds = useMemo(() => {
    if (filter === 'all') return attentionSeeds
    return attentionSeeds.filter((s) => s.status === filter)
  }, [attentionSeeds, filter])

  const filterTabs = useMemo(() => {
    const tabs: { id: AttentionFilter; label: string; count: number }[] = [
      {
        id: 'all',
        label: t('common.operatorReview.filterAll', { count: counts.all }),
        count: counts.all,
      },
    ]
    if (counts.blocked > 0) {
      tabs.push({
        id: 'blocked',
        label: t('common.operatorReview.filterBlocked', { count: counts.blocked }),
        count: counts.blocked,
      })
    }
    if (counts.awaiting_approval > 0) {
      tabs.push({
        id: 'awaiting_approval',
        label: t('common.operatorReview.filterApprovals', {
          count: counts.awaiting_approval,
        }),
        count: counts.awaiting_approval,
      })
    }
    if (counts.failed > 0) {
      tabs.push({
        id: 'failed',
        label: t('common.operatorReview.filterCiFailed', { count: counts.failed }),
        count: counts.failed,
      })
    }
    return tabs
  }, [counts, t])

  if (attentionSeeds.length === 0 && !showNominalState) {
    return null
  }

  const handleSeedClick = (seedId: string) => {
    if (onSelectSeed) {
      onSelectSeed(seedId)
    } else {
      navigateToDetail(VIEWS.SEEDS, seedId)
    }
  }

  const handleViewAll = () => {
    if (onViewAll) {
      onViewAll()
    } else {
      setActiveView(VIEWS.SEEDS)
    }
  }

  const hasHeaderAttention = attentionSeeds.length > 0

  // Nothing waiting: one compact row (same rule as "Agents working"), no count badge
  if (!hasHeaderAttention && !isInitialLoading) {
    return (
      <Card
        className={`px-5 py-3.5 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs ${className}`}
      >
        <div className="flex items-center gap-2.5 min-w-0 text-sm">
          <div className="w-7 h-7 rounded-lg bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
            <Check size={14} className="stroke-[2.5]" />
          </div>
          <span className="font-semibold text-zinc-900 dark:text-zinc-100 shrink-0">
            {t('dashboard.attentionTitle')}
          </span>
          <span className="text-zinc-500 dark:text-zinc-400 truncate">· You're all caught up</span>
        </div>
      </Card>
    )
  }

  return (
    <Card
      className={`p-5 sm:p-6 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs ${className}`}
    >
      <div className="flex items-center justify-between pb-3.5 border-b border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center gap-2.5">
          <div
            className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${hasHeaderAttention
                ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                : 'bg-emerald-500/10 text-emerald-600 dark:text-emerald-400'
              }`}
          >
            {hasHeaderAttention ? (
              <AlertTriangle size={16} />
            ) : (
              <ShieldCheck size={16} />
            )}
          </div>
          <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
            <span>{t('dashboard.attentionTitle')}</span>
            {!isInitialLoading && hasHeaderAttention && (
              <span
                className={`text-xs font-semibold px-2 py-0.5 rounded-full font-mono border ${hasHeaderAttention
                    ? 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/25'
                    : 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20'
                  }`}
              >
                {attentionSeeds.length}
              </span>
            )}
          </h3>
        </div>

        {attentionSeeds.length > 0 && (
          <button
            type="button"
            onClick={handleViewAll}
            className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors flex items-center gap-1 cursor-pointer"
          >
            <span>{t('common.viewAll')}</span>
            <ChevronRight size={13} />
          </button>
        )}
      </div>

      {attentionSeeds.length === 0 ? (
        isInitialLoading ? (
          <div className="pt-3.5 pb-1 flex items-center justify-between gap-3 px-1">
            <div className="flex items-center gap-2.5">
              <div className="w-6 h-6 rounded-lg bg-zinc-200/70 dark:bg-zinc-800 animate-pulse shrink-0" />
              <div className="h-4 w-64 sm:w-96 rounded-md bg-zinc-200/70 dark:bg-zinc-800 animate-pulse" />
            </div>
          </div>
        ) : (
          <div className="pt-3.5 pb-1 flex items-center gap-2.5 px-1 text-sm">
            <div className="w-5 h-5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
              <Check size={12} className="stroke-[2.5]" />
            </div>
            <span className="font-semibold text-zinc-900 dark:text-zinc-100">
              {t('dashboard.allNominal')}
            </span>
            <span className="text-zinc-300 dark:text-zinc-600">·</span>
            <span className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
              {t('dashboard.allNominalDesc')}
            </span>
          </div>
        )
      ) : (
        <>
          {filterTabs.length > 2 && (
            <div className="flex items-center gap-1.5 overflow-x-auto pt-3 pb-1 scrollbar-none">
              {filterTabs.map((tab) => {
                const isActive = filter === tab.id
                return (
                  <button
                    key={tab.id}
                    type="button"
                    aria-pressed={isActive}
                    onClick={() => setFilter(tab.id)}
                    className={`px-3 py-1 rounded-xl text-xs transition-all whitespace-nowrap cursor-pointer shrink-0 border ${isActive
                        ? 'bg-zinc-200/85 dark:bg-zinc-700/75 text-zinc-900 dark:text-zinc-100 border-zinc-300 dark:border-zinc-600 font-semibold shadow-2xs'
                        : 'bg-zinc-100/70 dark:bg-zinc-800/60 text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 border-zinc-200/70 dark:border-zinc-700/60 font-medium'
                      }`}
                  >
                    {tab.label}
                  </button>
                )
              })}
            </div>
          )}

          <div className="mt-2.5 flex flex-col divide-y divide-zinc-100 dark:divide-zinc-800/70">
            {filteredSeeds.slice(0, maxItems).map((seed) => {
              const proj = projectById.get(seed.project_id)
              const reason = getSeedAttentionReason(seed)
              const isErrorTone = seed.status === 'blocked' || seed.status === 'failed'

              return (
                <div
                  key={seed.id}
                  onClick={() => handleSeedClick(seed.id)}
                  className="py-3 px-2.5 rounded-xl flex items-center justify-between gap-3 hover:bg-zinc-100/80 dark:hover:bg-zinc-800/60 transition cursor-pointer group"
                >
                  <div className="flex items-center gap-3.5 min-w-0">
                    <StageBadge
                      stage={seed.stage}
                      className="w-[4.5rem] shrink-0 text-center"
                    />
                    <div className="min-w-0">
                      <div className="flex items-baseline gap-1.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-amber-600 dark:group-hover:text-amber-400 transition-colors min-w-0">
                        <span className="font-mono tabular-nums text-zinc-500 dark:text-zinc-400 group-hover:text-amber-600 dark:group-hover:text-amber-400 shrink-0">
                          #{seed.number}
                        </span>
                        <span className="text-zinc-300 dark:text-zinc-600 select-none shrink-0">
                          ·
                        </span>
                        <span className="truncate">{seed.title}</span>
                      </div>
                      <div className="flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400 truncate mt-0.5">
                        {!shouldHideProject && proj && (
                          <>
                            <span className="font-mono text-zinc-600 dark:text-zinc-300 shrink-0">
                              {proj.slug}
                            </span>
                            <span className="text-zinc-300 dark:text-zinc-600 select-none">
                              ·
                            </span>
                          </>
                        )}
                        <span
                          className={`truncate font-medium ${isErrorTone
                              ? 'text-rose-600 dark:text-rose-400'
                              : 'text-amber-600 dark:text-amber-400'
                            }`}
                        >
                          {reason}
                        </span>
                        <span className="text-zinc-300 dark:text-zinc-600 select-none shrink-0">
                          ·
                        </span>
                        <span className="font-mono text-zinc-500 dark:text-zinc-400 shrink-0">
                          {formatUsd(seed.spent)}
                        </span>
                      </div>
                    </div>
                  </div>
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={(e) => {
                      e.stopPropagation()
                      handleSeedClick(seed.id)
                    }}
                    className="text-xs font-semibold rounded-xl shrink-0 cursor-pointer"
                  >
                    <span>{t('common.review')}</span>
                    <ArrowRight size={12} />
                  </Button>
                </div>
              )
            })}
          </div>
        </>
      )}
    </Card>
  )
}
