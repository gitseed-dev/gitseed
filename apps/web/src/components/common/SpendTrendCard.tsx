import React, { useMemo } from 'react'
import { ChevronRight, GitMerge, TrendingUp } from 'lucide-react'
import { Card } from '@heroui/react'
import { StageBadge } from './StageBadge'
import { getSeedStatusClasses, STATUS_LABELS } from '../../utils/seeds'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import { formatUsd } from '../../utils/format'
import type { Run, Seed } from '../../types'

export interface SpendTrendCardProps {
  /** Seeds used to calculate cumulative seed spend and per-seed breakdown */
  seeds?: Seed[]
  /** Agent runs used to calculate 30d run spend */
  runs?: Run[]
  /** Optional monthly budget cap in USD (e.g. on ProjectDetail) */
  monthlyBudgetUsd?: string | number | null
  /** Show top seeds by spend/tokens when activity > 0 */
  showSeedBreakdown?: boolean
  /** Callback when clicking settings/budget link */
  onConfigureBudget?: () => void
  /** Callback when clicking a seed row */
  onSelectSeed?: (seedId: string) => void
  className?: string
}

interface DayBucket {
  dateKey: string
  label: string
  spend: number
  tokens: number
  runs: number
  merges: number
}

function toLocalDateKey(isoString?: string | null): string | null {
  if (!isoString) return null
  const d = new Date(isoString)
  if (Number.isNaN(d.getTime())) return null
  const year = d.getFullYear()
  const month = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${year}-${month}-${day}`
}

function formatCompactTokens(tokens: number): string {
  if (tokens >= 1_000_000) {
    return `${(tokens / 1_000_000).toFixed(2)}M`
  }
  if (tokens >= 1_000) {
    return `${(tokens / 1_000).toFixed(1)}k`
  }
  return tokens.toLocaleString()
}

export const SpendTrendCard: React.FC<SpendTrendCardProps> = ({
  seeds: propSeeds,
  runs: propRuns,
  monthlyBudgetUsd,
  showSeedBreakdown = true,
  onConfigureBudget,
  onSelectSeed,
  className = '',
}) => {
  const { t } = useTranslation()
  const {
    seeds: contextSeeds,
    allSeeds,
    runs: contextRuns,
    agents,
    navigateToDetail,
  } = useData()

  const seeds = propSeeds ?? contextSeeds
  const rawRuns = propRuns ?? contextRuns
  const isGlobalScope = allSeeds.length > 0 && seeds.length === allSeeds.length

  const runs = useMemo(() => {
    if (isGlobalScope) return rawRuns
    const seedIdSet = new Set(seeds.map((s) => s.id))
    return rawRuns.filter((r) => seedIdSet.has(r.seed_id))
  }, [isGlobalScope, rawRuns, seeds])

  const totalSpend = useMemo(() => {
    const runSpend = runs.reduce((acc, r) => acc + (parseFloat(r.cost) || 0), 0)
    const seedSpend = seeds.reduce((acc, s) => acc + (parseFloat(s.spent) || 0), 0)
    const agentSpend = isGlobalScope
      ? agents.reduce((acc, a) => acc + (parseFloat(a.total_cost) || 0), 0)
      : 0
    return Math.max(runSpend, seedSpend, agentSpend)
  }, [agents, isGlobalScope, runs, seeds])

  const totalTokens = useMemo(
    () =>
      runs.reduce(
        (acc, r) => acc + (r.input_tokens || 0) + (r.output_tokens || 0),
        0
      ),
    [runs]
  )

  const tokensBySeed = useMemo(() => {
    const map = new Map<string, number>()
    for (const r of runs) {
      const tok = (r.input_tokens || 0) + (r.output_tokens || 0)
      if (tok > 0) {
        map.set(r.seed_id, (map.get(r.seed_id) || 0) + tok)
      }
    }
    return map
  }, [runs])

  const runsBySeed = useMemo(() => {
    const map = new Map<string, number>()
    for (const r of runs) {
      map.set(r.seed_id, (map.get(r.seed_id) || 0) + 1)
    }
    return map
  }, [runs])

  const parsedBudget = useMemo(() => {
    if (monthlyBudgetUsd === null || monthlyBudgetUsd === undefined) return 0
    const n =
      typeof monthlyBudgetUsd === 'number'
        ? monthlyBudgetUsd
        : parseFloat(monthlyBudgetUsd)
    return Number.isFinite(n) && n > 0 ? n : 0
  }, [monthlyBudgetUsd])

  const dailyBuckets = useMemo<DayBucket[]>(() => {
    const buckets: DayBucket[] = []
    const map = new Map<string, DayBucket>()
    const now = new Date()

    for (let i = 29; i >= 0; i--) {
      const d = new Date(now.getFullYear(), now.getMonth(), now.getDate() - i)
      const year = d.getFullYear()
      const month = String(d.getMonth() + 1).padStart(2, '0')
      const day = String(d.getDate()).padStart(2, '0')
      const dateKey = `${year}-${month}-${day}`
      const label = d.toLocaleDateString(undefined, {
        month: 'short',
        day: 'numeric',
      })
      const bucket: DayBucket = {
        dateKey,
        label,
        spend: 0,
        tokens: 0,
        runs: 0,
        merges: 0,
      }
      buckets.push(bucket)
      map.set(dateKey, bucket)
    }

    for (const r of runs) {
      const key = toLocalDateKey(r.started_at || r.created_at || r.finished_at)
      const bucket = key ? map.get(key) : undefined
      if (bucket) {
        bucket.spend += parseFloat(r.cost) || 0
        bucket.tokens += (r.input_tokens || 0) + (r.output_tokens || 0)
        bucket.runs += 1
      }
    }

    for (const s of seeds) {
      if (s.status === 'merged' || s.merged_at) {
        const key = toLocalDateKey(s.merged_at || s.updated_at || s.created_at)
        const bucket = key ? map.get(key) : undefined
        if (bucket) {
          bucket.merges += 1
        }
      }
    }

    return buckets
  }, [runs, seeds])

  const mergedSeedsCount = useMemo(
    () => seeds.filter((s) => s.status === 'merged' || Boolean(s.merged_at)).length,
    [seeds]
  )

  const totalRunsCount = useMemo(() => {
    const agentTotal = isGlobalScope
      ? agents.reduce((acc, a) => acc + (a.run_count || 0), 0)
      : 0
    return Math.max(runs.length, agentTotal)
  }, [agents, isGlobalScope, runs.length])

  const succeededRunsCount = useMemo(() => {
    const fromRuns = runs.filter((r) => r.status === 'succeeded').length
    const fromAgents = isGlobalScope
      ? agents.reduce((acc, a) => acc + (a.succeeded_runs || 0), 0)
      : 0
    return Math.max(fromRuns, fromAgents)
  }, [agents, isGlobalScope, runs])

  const seedsWithSpend = useMemo(() => {
    if (!showSeedBreakdown) return []
    return seeds
      .filter(
        (s) =>
          (parseFloat(s.spent) || 0) > 0 ||
          (tokensBySeed.get(s.id) || 0) > 0 ||
          (runsBySeed.get(s.id) || 0) > 0 ||
          s.status === 'merged'
      )
      .sort((a, b) => {
        const diff = (parseFloat(b.spent) || 0) - (parseFloat(a.spent) || 0)
        if (diff !== 0) return diff
        const tokDiff = (tokensBySeed.get(b.id) || 0) - (tokensBySeed.get(a.id) || 0)
        if (tokDiff !== 0) return tokDiff
        return (runsBySeed.get(b.id) || 0) - (runsBySeed.get(a.id) || 0)
      })
      .slice(0, 5)
  }, [runsBySeed, seeds, showSeedBreakdown, tokensBySeed])

  const hasActivity =
    totalRunsCount > 0 ||
    totalSpend > 0 ||
    totalTokens > 0 ||
    mergedSeedsCount > 0 ||
    seedsWithSpend.length > 0

  // Local / self-hosted models report tokens but no cost; avoid a misleading "$0.00"
  const isNoCost = totalSpend <= 0

  const chartStats = useMemo(() => {
    const maxSpend = Math.max(...dailyBuckets.map((b) => b.spend), 0)
    const maxTokens = Math.max(...dailyBuckets.map((b) => b.tokens), 0)
    const maxRuns = Math.max(...dailyBuckets.map((b) => b.runs + b.merges), 0)
    const metricMode: 'spend' | 'tokens' | 'runs' =
      maxSpend > 0 ? 'spend' : maxTokens > 0 ? 'tokens' : 'runs'
    return { maxSpend, maxTokens, maxRuns, metricMode }
  }, [dailyBuckets])

  const handleSeedClick = (seedId: string) => {
    if (onSelectSeed) {
      onSelectSeed(seedId)
    } else {
      navigateToDetail(VIEWS.SEEDS, seedId)
    }
  }

  return (
    <Card
      className={`p-5 sm:p-6 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs h-fit ${className}`}
    >
      <div className="flex items-center justify-between pb-3.5 border-b border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <TrendingUp size={16} />
          </div>
          <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
            {t('dashboard.spendTrendTitle')}
          </h3>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          <div className="flex items-baseline gap-1.5 font-mono text-xs">
            {isNoCost && totalTokens > 0 ? (
              // Local models: lead with usage, keep the "no cost" note quiet
              <span
                className="font-semibold text-zinc-700 dark:text-zinc-300"
                title="Local models (no cost recorded)"
              >
                {formatCompactTokens(totalTokens)} tok
              </span>
            ) : (
              <>
                <span className="font-semibold text-zinc-800 dark:text-zinc-200">
                  {formatUsd(totalSpend)}
                </span>
                {totalTokens > 0 && (
                  <span className="text-zinc-500 dark:text-zinc-400">
                    · {formatCompactTokens(totalTokens)} tok
                  </span>
                )}
              </>
            )}
            {parsedBudget > 0 && (
              <>
                <span className="text-zinc-300 dark:text-zinc-600 select-none">/</span>
                <span className="text-zinc-500 dark:text-zinc-400">
                  ${parsedBudget.toLocaleString()}/mo cap
                </span>
              </>
            )}
          </div>

          {onConfigureBudget && (
            <button
              type="button"
              onClick={onConfigureBudget}
              className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors flex items-center gap-1 cursor-pointer"
            >
              <span>{t('projects.actions.settings')}</span>
              <ChevronRight size={13} />
            </button>
          )}
        </div>
      </div>

      {parsedBudget > 0 && (
        <div className="mt-4 pt-1">
          <div className="flex items-center justify-between text-xs text-zinc-500 dark:text-zinc-400 mb-1.5">
            <span>{t('details.project.meta.monthlyBudget')}</span>
            <span className="font-mono font-semibold text-zinc-700 dark:text-zinc-300">
              {Math.min(100, Math.round((totalSpend / parsedBudget) * 100))}% consumed
            </span>
          </div>
          <div className="w-full h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
            <div
              className={`h-full rounded-full transition-all duration-300 ${totalSpend >= parsedBudget
                  ? 'bg-rose-500'
                  : totalSpend / parsedBudget > 0.8
                    ? 'bg-amber-500'
                    : 'bg-teal-500'
                }`}
              style={{
                width: `${Math.min(100, (totalSpend / parsedBudget) * 100)}%`,
              }}
            />
          </div>
        </div>
      )}

      {hasActivity ? (
        <div className="mt-4 flex flex-col gap-4">
          {/* 30-Day Activity & Merge Summary Strip */}
          <div className="grid grid-cols-3 gap-2.5">
            <div className="px-3 py-2 rounded-xl bg-zinc-50/90 dark:bg-zinc-800/50 border border-zinc-200/60 dark:border-zinc-800">
              <div className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                Agent Runs
              </div>
              <div className="font-mono text-xs sm:text-sm font-bold text-zinc-900 dark:text-zinc-100 mt-0.5">
                {totalRunsCount.toLocaleString()}
                <span className="text-[11px] font-normal text-zinc-500 dark:text-zinc-400 ml-1">
                  ({succeededRunsCount.toLocaleString()} ok)
                </span>
              </div>
            </div>
            <div className="px-3 py-2 rounded-xl bg-zinc-50/90 dark:bg-zinc-800/50 border border-zinc-200/60 dark:border-zinc-800">
              <div className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400 flex items-center gap-1">
                <GitMerge size={11} className="text-emerald-500" />
                <span>Seeds Merged</span>
              </div>
              <div className="font-mono text-xs sm:text-sm font-bold text-emerald-600 dark:text-emerald-400 mt-0.5">
                {mergedSeedsCount.toLocaleString()}
              </div>
            </div>
            <div className="px-3 py-2 rounded-xl bg-zinc-50/90 dark:bg-zinc-800/50 border border-zinc-200/60 dark:border-zinc-800">
              <div className="text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                Total Tokens
              </div>
              <div
                className="font-mono text-xs sm:text-sm font-bold text-zinc-900 dark:text-zinc-100 mt-0.5 truncate"
                title={totalTokens.toLocaleString()}
              >
                {formatCompactTokens(totalTokens)}
              </div>
            </div>
          </div>

          {/* 30-Day Daily Sparkline Bar Chart */}
          <div className="px-3.5 py-3 rounded-xl bg-zinc-50/60 dark:bg-zinc-800/30 border border-zinc-200/60 dark:border-zinc-800/80">
            <div className="flex items-center justify-between text-[11px] text-zinc-500 dark:text-zinc-400 mb-2.5">
              <span className="font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                {chartStats.metricMode === 'spend'
                  ? 'Daily Spend (30d)'
                  : chartStats.metricMode === 'tokens'
                    ? 'Daily Token Volume (30d)'
                    : 'Daily Run Activity (30d)'}
              </span>
              <div className="flex items-center gap-3">
                <span className="inline-flex items-center gap-1">
                  <span className="w-2 h-2 rounded-xs bg-teal-500 inline-block" />
                  <span>
                    {chartStats.metricMode === 'spend'
                      ? 'Spend'
                      : chartStats.metricMode === 'tokens'
                        ? 'Tokens'
                        : 'Runs'}
                  </span>
                </span>
                {mergedSeedsCount > 0 && (
                  <span className="inline-flex items-center gap-1">
                    <span className="w-2 h-2 rounded-full bg-emerald-500 inline-block" />
                    <span>Merged</span>
                  </span>
                )}
              </div>
            </div>

            <div className="h-20 flex items-end gap-1 pt-3">
              {dailyBuckets.map((b) => {
                const rawValue =
                  chartStats.metricMode === 'spend'
                    ? b.spend
                    : chartStats.metricMode === 'tokens'
                      ? b.tokens
                      : b.runs + b.merges
                const maxVal =
                  chartStats.metricMode === 'spend'
                    ? chartStats.maxSpend
                    : chartStats.metricMode === 'tokens'
                      ? chartStats.maxTokens
                      : chartStats.maxRuns
                const hasDayActivity = b.spend > 0 || b.tokens > 0 || b.runs > 0 || b.merges > 0
                const ratio = maxVal > 0 ? rawValue / maxVal : 0
                const heightPct = hasDayActivity ? Math.max(14, Math.round(ratio * 100)) : 6
                const tooltipParts = [
                  b.label,
                  isNoCost ? null : `${formatUsd(b.spend)}`,
                  b.tokens > 0 ? `${b.tokens.toLocaleString()} tok` : null,
                  b.runs > 0 ? `${b.runs} run${b.runs === 1 ? '' : 's'}` : null,
                  b.merges > 0 ? `${b.merges} merged` : null,
                ].filter(Boolean)

                return (
                  <div
                    key={b.dateKey}
                    title={tooltipParts.join(' · ')}
                    className="flex-1 h-full flex flex-col items-center justify-end gap-1 group cursor-default"
                  >
                    {b.merges > 0 && (
                      <span className="w-1.5 h-1.5 rounded-full bg-emerald-500 shrink-0 shadow-xs shadow-emerald-500/50" />
                    )}
                    <div
                      className={`w-full rounded-t-xs transition-all duration-200 ${hasDayActivity
                          ? b.merges > 0
                            ? 'bg-emerald-500 group-hover:bg-emerald-400'
                            : 'bg-teal-500/85 group-hover:bg-teal-400'
                          : 'bg-zinc-200/70 dark:bg-zinc-800 group-hover:bg-zinc-300 dark:group-hover:bg-zinc-700'
                        }`}
                      style={{ height: `${heightPct}%` }}
                    />
                  </div>
                )
              })}
            </div>

            <div className="flex items-center justify-between text-[11px] font-mono text-zinc-400 dark:text-zinc-500 mt-1.5">
              <span>{dailyBuckets[0]?.label}</span>
              <span>{dailyBuckets[Math.floor(dailyBuckets.length / 2)]?.label}</span>
              <span>Today</span>
            </div>
          </div>

          {/* Per-seed breakdown */}
          {showSeedBreakdown && seedsWithSpend.length > 0 && (
            <div>
              <div className="text-xs font-semibold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 mb-2">
                {isNoCost ? 'Top seeds by usage' : t('details.project.spendBySeedTitle')}
              </div>
              <div className="divide-y divide-zinc-100 dark:divide-zinc-800/70 border border-zinc-200/60 dark:border-zinc-800/60 rounded-xl overflow-hidden">
                {seedsWithSpend.map((seed) => {
                  const cap = seed.budget_cap ? parseFloat(seed.budget_cap) : 0
                  return (
                    <div
                      key={seed.id}
                      onClick={() => handleSeedClick(seed.id)}
                      className="py-2.5 px-3 flex items-center justify-between gap-3 bg-white dark:bg-zinc-900 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 cursor-pointer transition group"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        {seed.status === 'merged' || seed.status === 'closed' ? (
                          // Finished seeds: say Merged / Closed, not the last stage they sat in
                          <span
                            className={`inline-flex items-center px-2 py-0.5 rounded-md text-[11px] font-semibold border shrink-0 ${getSeedStatusClasses(seed.status)}`}
                          >
                            {STATUS_LABELS[seed.status]}
                          </span>
                        ) : (
                          <StageBadge stage={seed.stage} />
                        )}
                        <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200 truncate group-hover:text-teal-600 dark:group-hover:text-teal-400 transition-colors">
                          #{seed.number} · {seed.title}
                        </span>
                      </div>
                      <div className="font-mono text-xs font-semibold text-zinc-900 dark:text-zinc-100 shrink-0 flex items-center gap-2">
                        {(tokensBySeed.get(seed.id) || 0) > 0 && (
                          <span className="text-zinc-500 dark:text-zinc-400 font-normal">
                            {formatCompactTokens(tokensBySeed.get(seed.id) || 0)} tok
                          </span>
                        )}
                        {!isNoCost && (
                          <span>
                            {formatUsd(seed.spent)}
                            {cap > 0 && (
                              <span className="text-zinc-400 font-normal"> / ${cap.toFixed(0)}</span>
                            )}
                          </span>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      ) : (
        <div className="py-10 flex flex-col items-center justify-center text-center px-4">
          <div className="w-10 h-10 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center mb-2.5">
            <TrendingUp size={18} />
          </div>
          <h4 className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
            {t('dashboard.spendTrendsEmptyTitle')}
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
            {t('dashboard.spendTrendsEmptyDesc')}
          </p>
        </div>
      )}
    </Card>
  )
}
