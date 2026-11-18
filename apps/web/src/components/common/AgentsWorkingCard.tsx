import React, { useMemo } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, ChevronRight, Terminal } from 'lucide-react'
import { Card } from '@heroui/react'
import { api } from '../../api/client'
import { ContextWindowBadge } from './ContextWindowBadge'
import { StageBadge } from './StageBadge'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import {
  formatRelativeTime,
  formatUsd,
  resolveRunContextWindow,
} from '../../utils/format'
import type { Seed } from '../../types'

export interface AgentsWorkingCardProps {
  /** Seeds to inspect for running/queued work (defaults to DataContext scoped seeds) */
  seeds?: Seed[]
  /** Optional custom empty state message */
  emptyReason?: string
  /** Hide project slug on rows */
  hideProjectBadge?: boolean
  /** Callback when clicking a seed row */
  onSelectSeed?: (seedId: string) => void
  /** Callback when clicking View All */
  onViewAll?: () => void
  /** Maximum running seeds to show (default: 5) */
  maxItems?: number
  /** Also list the current user's running sessions (terminals / coding agents) */
  showMySessions?: boolean
  /** Collapse to a single line when nothing is running (full-width overview layout) */
  compactWhenIdle?: boolean
  className?: string
}

const MAX_SESSIONS = 3

function runningFor(iso: string | null | undefined): string {
  if (!iso) return 'just started'
  const mins = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 60000))
  if (mins < 1) return 'just started'
  if (mins < 60) return `${mins}m`
  const h = Math.floor(mins / 60)
  return mins % 60 ? `${h}h ${mins % 60}m` : `${h}h`
}

export const AgentsWorkingCard: React.FC<AgentsWorkingCardProps> = ({
  seeds: propSeeds,
  emptyReason,
  hideProjectBadge,
  onSelectSeed,
  onViewAll,
  maxItems = 5,
  showMySessions = false,
  compactWhenIdle = false,
  className = '',
}) => {
  const { t } = useTranslation()
  const {
    seeds: contextSeeds,
    runs,
    agents,
    projects,
    selectedProject,
    navigateToDetail,
    setActiveView,
  } = useData()

  const sourceSeeds = propSeeds ?? contextSeeds
  const shouldHideProject = hideProjectBadge ?? Boolean(selectedProject)

  const projectById = useMemo(
    () => new Map(projects.map((p) => [p.id, p])),
    [projects]
  )

  const workingSeeds = useMemo(
    () => sourceSeeds.filter((s) => s.status === 'running'),
    [sourceSeeds]
  )

  const queuedSeeds = useMemo(
    () => sourceSeeds.filter((s) => s.status === 'queued'),
    [sourceSeeds]
  )

  // Your running sessions: agent work too, shown under the seed runs (nothing when none)
  const { data: mySessions = [] } = useQuery({
    queryKey: ['sessions', 'list', 'mine', 'running', selectedProject || 'all'],
    queryFn: () =>
      api.sessions.list({ scope: 'mine', state: 'running', project_id: selectedProject || null, limit: 10 }),
    enabled: showMySessions,
    refetchInterval: 30_000,
  })
  const visibleSessions = showMySessions ? mySessions.slice(0, MAX_SESSIONS) : []

  // Active run per seed: which agent, and since when
  const activeRunBySeed = useMemo(() => {
    const map = new Map<string, (typeof runs)[number]>()
    for (const r of runs) {
      if (r.status === 'running' || r.status === 'starting') map.set(r.seed_id, r)
    }
    return map
  }, [runs])
  const agentById = useMemo(() => new Map(agents.map((a) => [a.id, a])), [agents])

  const handleSeedClick = (seedId: string) => {
    if (onSelectSeed) {
      onSelectSeed(seedId)
    } else {
      navigateToDetail(VIEWS.SEEDS, seedId)
    }
  }

  if (compactWhenIdle && workingSeeds.length === 0 && visibleSessions.length === 0) {
    return (
      <Card
        className={`px-5 py-3.5 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs ${className}`}
      >
        <div className="flex items-center justify-between gap-3">
          <div className="flex items-center gap-2.5 min-w-0 text-sm">
            <div className="w-7 h-7 rounded-lg bg-zinc-100 dark:bg-zinc-800 text-zinc-400 flex items-center justify-center shrink-0">
              <Bot size={14} />
            </div>
            <span className="font-semibold text-zinc-900 dark:text-zinc-100 shrink-0">
              {t('dashboard.agentsWorkingTitle')}
            </span>
            <span className="text-zinc-500 dark:text-zinc-400 truncate">
              {`· No agents running${queuedSeeds.length ? ` · ${queuedSeeds.length} queued` : ''}`}
            </span>
          </div>
          {onViewAll && queuedSeeds.length > 0 && (
            <button
              type="button"
              onClick={onViewAll}
              className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 flex items-center gap-1 cursor-pointer shrink-0"
            >
              <span>{t('common.viewAll')}</span>
              <ChevronRight size={13} />
            </button>
          )}
        </div>
      </Card>
    )
  }

  return (
    <Card
      className={`p-5 sm:p-6 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs h-fit ${className}`}
    >
      <div className="flex items-center justify-between pb-3.5 border-b border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-sky-500/10 text-sky-600 dark:text-sky-400 flex items-center justify-center shrink-0">
            <Bot size={16} />
          </div>
          <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
            <span>{t('dashboard.agentsWorkingTitle')}</span>
            {workingSeeds.length > 0 && (
              <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20 font-mono">
                {workingSeeds.length}
              </span>
            )}
          </h3>
        </div>

        <div className="flex items-center gap-3 shrink-0">
          {queuedSeeds.length > 0 && (
            <span className="text-xs font-mono text-zinc-500 dark:text-zinc-400">
              {t('dashboard.queuedSummaryCount', { count: queuedSeeds.length })}
            </span>
          )}
          {onViewAll && (workingSeeds.length > 0 || queuedSeeds.length > 0) && (
            <button
              type="button"
              onClick={onViewAll}
              className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors flex items-center gap-1 cursor-pointer"
            >
              <span>{t('common.viewAll')}</span>
              <ChevronRight size={13} />
            </button>
          )}
        </div>
      </div>

      {workingSeeds.length === 0 && visibleSessions.length === 0 ? (
        <div className="py-10 flex flex-col items-center justify-center text-center px-4">
          <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 flex items-center justify-center text-zinc-400 mb-2">
            <Bot size={20} />
          </div>
          <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
            {t('dashboard.agentsWorkingEmpty')}
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm mt-1 leading-relaxed">
            {emptyReason || t('dashboard.agentsWorkingEmptyDesc')}
          </p>
        </div>
      ) : (
        <div className="mt-2.5 flex flex-col divide-y divide-zinc-100 dark:divide-zinc-800/70">
          {workingSeeds.slice(0, maxItems).map((seed) => {
            const proj = projectById.get(seed.project_id)
            return (
              <div
                key={seed.id}
                onClick={() => handleSeedClick(seed.id)}
                className="py-3 px-2.5 rounded-xl flex items-center justify-between gap-3 hover:bg-zinc-100/80 dark:hover:bg-zinc-800/60 transition cursor-pointer group"
              >
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2.5 min-w-0">
                    <span className="relative flex h-2 w-2 shrink-0">
                      <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-sky-400 opacity-75" />
                      <span className="relative inline-flex rounded-full h-2 w-2 bg-sky-500" />
                    </span>
                    <span className="flex items-baseline gap-1.5 text-sm font-medium text-zinc-900 dark:text-zinc-100 group-hover:text-sky-600 dark:group-hover:text-sky-400 transition-colors min-w-0">
                      <span className="font-mono tabular-nums text-zinc-500 dark:text-zinc-400 group-hover:text-sky-600 dark:group-hover:text-sky-400 shrink-0">
                        #{seed.number}
                      </span>
                      <span className="text-zinc-300 dark:text-zinc-600 select-none shrink-0">
                        ·
                      </span>
                      <span className="truncate">{seed.title}</span>
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400 pl-4.5 mt-0.5 truncate">
                    {!shouldHideProject && proj && (
                      <>
                        <span className="font-mono text-zinc-600 dark:text-zinc-300 shrink-0">
                          {proj.slug}
                        </span>
                        <span className="text-zinc-300 dark:text-zinc-600 select-none">·</span>
                      </>
                    )}
                    {(() => {
                      const run = activeRunBySeed.get(seed.id)
                      const agent = run?.agent_id ? agentById.get(run.agent_id) : undefined
                      const ctxWin = resolveRunContextWindow(run)
                      return (
                        <>
                          {agent && (
                            <>
                              <span className="font-mono text-teal-700 dark:text-teal-300 shrink-0">@{agent.handle}</span>
                              <span className="text-zinc-300 dark:text-zinc-600 select-none">·</span>
                            </>
                          )}
                          <span className="font-mono">
                            {run?.started_at
                              ? `Running ${runningFor(run.started_at)}`
                              : `Updated ${formatRelativeTime(seed.updated_at)}`}
                          </span>
                          {ctxWin && ctxWin.used_tokens > 0 && (
                            <>
                              <span className="text-zinc-300 dark:text-zinc-600 select-none">·</span>
                              <ContextWindowBadge
                                contextWindow={ctxWin}
                                variant="inline"
                              />
                            </>
                          )}
                        </>
                      )
                    })()}
                  </div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <StageBadge
                    stage={seed.stage}
                    className="w-[4.5rem] shrink-0 text-center"
                  />
                  <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                    {formatUsd(seed.spent)}
                  </span>
                </div>
              </div>
            )
          })}

          {visibleSessions.length > 0 && (
            <div className="pt-2.5 mt-1">
              <div className="flex items-center justify-between px-2.5 pb-1">
                <span className="text-xs font-semibold uppercase tracking-wider text-zinc-400">
                  Your sessions
                </span>
                {mySessions.length > MAX_SESSIONS && (
                  <button
                    type="button"
                    onClick={() => setActiveView(VIEWS.SESSIONS)}
                    className="text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 flex items-center gap-1 cursor-pointer"
                  >
                    <span>View all ({mySessions.length})</span>
                    <ChevronRight size={13} />
                  </button>
                )}
              </div>
              {visibleSessions.map((s) => (
                <div
                  key={`${s.kind}-${s.id}`}
                  onClick={() => navigateToDetail(VIEWS.SESSIONS, s.kind, s.id)}
                  className="py-2.5 px-2.5 rounded-xl flex items-center justify-between gap-3 hover:bg-zinc-100/80 dark:hover:bg-zinc-800/60 transition cursor-pointer group"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    {s.kind === 'chat' ? (
                      <Bot size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                    ) : (
                      <Terminal size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                    )}
                    <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100 group-hover:text-teal-600 dark:group-hover:text-teal-400 truncate">
                      {s.name}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400 shrink-0">
                    {s.kind === 'chat' && s.context_window && s.context_window.used_tokens > 0 && (
                      <>
                        <ContextWindowBadge
                          contextWindow={s.context_window}
                          variant="inline"
                        />
                        <span className="text-zinc-300 dark:text-zinc-600">·</span>
                      </>
                    )}
                    {!shouldHideProject && <span className="font-mono">{s.project_slug}</span>}
                    {!shouldHideProject && <span className="text-zinc-300 dark:text-zinc-600">·</span>}
                    <span className="font-mono">{runningFor(s.started_at)}</span>
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}
