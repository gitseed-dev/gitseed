import React, { useMemo } from 'react'
import { AlertTriangle, EyeOff } from 'lucide-react'
import { ContextWindowBadge } from '../common/ContextWindowBadge'
import { SDLC_STAGES } from '../common/SdlcPipelineStrip'
import { useData } from '../../context/DataContext'
import {
  formatRelativeTime,
  formatUsd,
  getSeedAttentionReason,
  resolveRunContextWindow,
  seedNeedsAttention,
} from '../../utils/format'
import { getSeedStatusClasses, STATUS_LABELS } from '../../utils/seeds'
import type { Project, Seed } from '../../types'

export interface SeedsBoardViewProps {
  sortedItems: Seed[]
  mergeColumnSeeds: Seed[]
  projectById: Map<string, Project>
  isProjectScoped: boolean
  hideMergeColumn: boolean
  onToggleHideMergeColumn: () => void
  onOpenSeed: (seed: Seed) => void
}

export const SeedsBoardView: React.FC<SeedsBoardViewProps> = ({
  sortedItems,
  mergeColumnSeeds,
  projectById,
  isProjectScoped,
  hideMergeColumn,
  onToggleHideMergeColumn,
  onOpenSeed,
}) => {
  const { runs } = useData()
  const activeRunBySeed = useMemo(() => {
    const map = new Map<string, (typeof runs)[number]>()
    for (const r of runs) {
      if (r.status === 'running' || r.status === 'starting') {
        map.set(r.seed_id, r)
      }
    }
    return map
  }, [runs])
  const visibleStages = hideMergeColumn
    ? SDLC_STAGES.filter((s) => s.id !== 'merge')
    : SDLC_STAGES

  return (
    <div
      className={`grid grid-cols-1 md:grid-cols-2 ${
        hideMergeColumn ? 'xl:grid-cols-4' : 'xl:grid-cols-5'
      } gap-3.5 items-start`}
    >
      {visibleStages.map((stageMeta) => {
        const stageSeeds =
          stageMeta.id === 'merge'
            ? mergeColumnSeeds
            : sortedItems.filter((s) => s.stage === stageMeta.id)

        return (
          <div
            key={stageMeta.id}
            className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/75 dark:bg-zinc-900/60 p-3 space-y-2.5 min-h-[320px] shadow-xs"
          >
            {/* Column Header */}
            <div className="flex items-center justify-between gap-2 px-1.5 py-1">
              <div
                className={`flex items-center gap-2 min-w-0 ${
                  stageSeeds.length === 0 ? 'opacity-55' : ''
                }`}
              >
                <span
                  className={`w-2 h-2 rounded-full shrink-0 ${stageMeta.dotClass} ${
                    stageSeeds.length === 0 ? 'opacity-50' : ''
                  }`}
                />
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100 truncate">
                  {stageMeta.name}
                </span>
                <span
                  className={`font-mono text-[11px] px-1.5 py-0.5 rounded-full border shrink-0 ${
                    stageSeeds.length > 0
                      ? `font-bold ${stageMeta.textClass} bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700/80`
                      : 'font-medium text-zinc-400 dark:text-zinc-500 bg-zinc-100/60 dark:bg-zinc-800/50 border-zinc-200/60 dark:border-zinc-700/50'
                  }`}
                >
                  {stageSeeds.length}
                </span>
              </div>

              {stageMeta.id === 'merge' && (
                <button
                  type="button"
                  onClick={onToggleHideMergeColumn}
                  title="Hide Merge column"
                  className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer shrink-0"
                >
                  <EyeOff size={12} />
                  <span>Hide</span>
                </button>
              )}

              {stageMeta.id === 'review' && hideMergeColumn && (
                <button
                  type="button"
                  onClick={onToggleHideMergeColumn}
                  title="Show Merge column"
                  className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-lg text-[11px] font-semibold text-zinc-600 dark:text-zinc-300 bg-zinc-100/90 dark:bg-zinc-800/90 hover:bg-teal-500/10 hover:text-teal-600 dark:hover:text-teal-400 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer shrink-0"
                >
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
                  <span>Show Merge</span>
                  <span className="font-mono text-[10px] opacity-80">
                    ({mergeColumnSeeds.length})
                  </span>
                </button>
              )}
            </div>

            {/* Seed Cards */}
            {stageSeeds.length === 0 ? (
              <div className="rounded-xl border border-zinc-200/70 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-950/30 py-8 px-3 text-center text-xs text-zinc-400">
                No seeds in {stageMeta.name}
              </div>
            ) : (
              <div className="space-y-2.5">
                {stageSeeds.map((seed) => {
                  const proj = projectById.get(seed.project_id)
                  const needsYou = seedNeedsAttention(seed)

                  return (
                    <button
                      key={seed.id}
                      type="button"
                      onClick={() => onOpenSeed(seed)}
                      className={`w-full text-left rounded-xl border p-3.5 transition-all bg-white dark:bg-zinc-900 hover:border-teal-500/50 hover:shadow-sm space-y-2 cursor-pointer ${
                        needsYou
                          ? 'border-amber-500/40 dark:border-amber-500/40'
                          : 'border-zinc-200 dark:border-zinc-700/80'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-mono text-[11px] font-semibold text-zinc-500 truncate">
                          {!isProjectScoped && proj
                            ? `${proj.slug}#${seed.number}`
                            : `#${seed.number}`}
                        </span>
                        <span
                          className={`inline-flex items-center px-1.5 py-0.5 rounded-md text-[10px] font-semibold border ${getSeedStatusClasses(
                            seed.status
                          )}`}
                        >
                          {STATUS_LABELS[seed.status]}
                        </span>
                      </div>

                      <p className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 line-clamp-2 leading-snug">
                        {seed.title}
                      </p>

                      {needsYou && (
                        <div className="rounded-lg bg-amber-500/10 border border-amber-500/25 px-2 py-1 text-[11px] text-amber-700 dark:text-amber-300 flex items-center gap-1.5">
                          <AlertTriangle className="w-3 h-3 shrink-0" />
                          <span className="truncate">
                            {getSeedAttentionReason(seed)}
                          </span>
                        </div>
                      )}

                      {seed.tags.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {seed.tags.slice(0, 3).map((tag) => (
                            <span
                              key={tag}
                              className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[10px] font-mono text-zinc-600 dark:text-zinc-400"
                            >
                              #{tag}
                            </span>
                          ))}
                        </div>
                      )}

                      <div className="pt-1.5 border-t border-zinc-100 dark:border-zinc-800/80 flex items-center justify-between gap-2 text-[11px] text-zinc-400">
                        <span className="font-mono text-zinc-600 dark:text-zinc-300">
                          {formatUsd(seed.spent)}
                          {seed.budget_cap
                            ? ` / ${formatUsd(seed.budget_cap)}`
                            : ''}
                        </span>
                        <div className="flex items-center gap-1.5 shrink-0">
                          {seed.status === 'running' && (
                            <ContextWindowBadge
                              contextWindow={resolveRunContextWindow(
                                activeRunBySeed.get(seed.id)
                              )}
                              variant="inline"
                            />
                          )}
                          <span className="font-mono">
                            {formatRelativeTime(seed.updated_at)}
                          </span>
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

