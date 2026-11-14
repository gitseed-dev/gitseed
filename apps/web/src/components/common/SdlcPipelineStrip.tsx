import React, { useCallback, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { ChevronLeft, ChevronRight, TrendingUp } from 'lucide-react'
import { Tooltip } from './Tooltip'
import { SEED_OPEN_STATUSES, VIEWS } from '../../constants'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import type { Seed, SeedStage } from '../../types'

export interface SdlcStageMeta {
  id: SeedStage
  step: number
  name: string
  dotClass: string
  glowClass: string
  pulseClass: string
  textClass: string
}

export const SDLC_STAGES: SdlcStageMeta[] = [
  {
    id: 'ideate',
    step: 1,
    name: 'Ideate',
    dotClass: 'bg-pink-500',
    glowClass:
      'group-hover:shadow-[0_0_8px_rgba(236,72,153,0.8)] group-hover:ring-2 group-hover:ring-pink-400/30',
    pulseClass: 'bg-pink-400/50',
    textClass: 'text-pink-600 dark:text-pink-400',
  },
  {
    id: 'plan',
    step: 2,
    name: 'Plan',
    dotClass: 'bg-purple-500',
    glowClass:
      'group-hover:shadow-[0_0_8px_rgba(168,85,247,0.8)] group-hover:ring-2 group-hover:ring-purple-400/30',
    pulseClass: 'bg-purple-400/50',
    textClass: 'text-purple-600 dark:text-purple-400',
  },
  {
    id: 'execute',
    step: 3,
    name: 'Execute',
    dotClass: 'bg-blue-500',
    glowClass:
      'group-hover:shadow-[0_0_8px_rgba(59,130,246,0.8)] group-hover:ring-2 group-hover:ring-blue-400/30',
    pulseClass: 'bg-blue-400/50',
    textClass: 'text-blue-600 dark:text-blue-400',
  },
  {
    id: 'review',
    step: 4,
    name: 'Review',
    dotClass: 'bg-cyan-400',
    glowClass:
      'group-hover:shadow-[0_0_8px_rgba(34,211,238,0.8)] group-hover:ring-2 group-hover:ring-cyan-400/30',
    pulseClass: 'bg-cyan-400/50',
    textClass: 'text-cyan-600 dark:text-cyan-400',
  },
  {
    id: 'merge',
    step: 5,
    name: 'Merge',
    dotClass: 'bg-emerald-500',
    glowClass:
      'group-hover:shadow-[0_0_8px_rgba(16,185,129,0.8)] group-hover:ring-2 group-hover:ring-emerald-400/30',
    pulseClass: 'bg-emerald-400/50',
    textClass: 'text-emerald-600 dark:text-emerald-400',
  },
]

export interface SdlcPipelineStripProps {
  seeds: Seed[]
  selectedStage?: SeedStage | null
  onSelectStage?: (stage: SeedStage) => void
  className?: string
}

export const SdlcPipelineStrip: React.FC<SdlcPipelineStripProps> = ({
  seeds,
  selectedStage = null,
  onSelectStage,
  className = '',
}) => {
  const { setActiveView } = useData()
  const { t } = useTranslation()

  const containerRef = useRef<HTMLDivElement>(null)
  const stagesRef = useRef<HTMLDivElement>(null)
  const [flowMode, setFlowMode] = useState<'full' | 'compact' | 'hidden'>('full')
  const [canScrollLeft, setCanScrollLeft] = useState(false)
  const [canScrollRight, setCanScrollRight] = useState(false)

  const oneWeekAgo = useMemo(() => Date.now() - 7 * 24 * 60 * 60 * 1000, [])

  const createdThisWeekCount = useMemo(
    () =>
      seeds.filter((s) => s.created_at && new Date(s.created_at).getTime() >= oneWeekAgo).length,
    [seeds, oneWeekAgo]
  )

  const mergedThisWeekCount = useMemo(
    () =>
      seeds.filter((s) => {
        if (s.status !== 'merged') return false
        const ts = s.merged_at || s.updated_at
        return Boolean(ts && new Date(ts).getTime() >= oneWeekAgo)
      }).length,
    [seeds, oneWeekAgo]
  )

  const updateScrollAffordance = useCallback(() => {
    const el = stagesRef.current
    if (!el) return
    setCanScrollLeft(el.scrollLeft > 4)
    setCanScrollRight(el.scrollLeft + el.clientWidth < el.scrollWidth - 4)
  }, [])

  useLayoutEffect(() => {
    const container = containerRef.current
    const stagesEl = stagesRef.current
    if (!container || !stagesEl) return

    const updateMode = () => {
      const style = window.getComputedStyle(container)
      const padX =
        (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0)
      const innerWidth = container.clientWidth - padX

      const buttons = stagesEl.querySelectorAll('button[data-stage-pill="true"]')
      let buttonsWidth = 0
      buttons.forEach((btn) => {
        buttonsWidth += (btn as HTMLElement).offsetWidth
      })
      const baseStagesWidth =
        buttonsWidth + Math.max(0, buttons.length - 1) * 20

      // Full mode needs room for full weekly text (~210px) + safety gap (28px)
      if (innerWidth >= baseStagesWidth + 238) {
        setFlowMode('full')
      } else if (innerWidth >= baseStagesWidth + 175) {
        // Compact mode needs room for "+X created · Y merged" (~145px) + safety gap (30px)
        setFlowMode('compact')
      } else {
        setFlowMode('hidden')
      }

      updateScrollAffordance()
    }

    updateMode()

    const observer = new ResizeObserver(updateMode)
    observer.observe(container)
    observer.observe(stagesEl)
    stagesEl.addEventListener('scroll', updateScrollAffordance, { passive: true })
    return () => {
      observer.disconnect()
      stagesEl.removeEventListener('scroll', updateScrollAffordance)
    }
  }, [seeds, createdThisWeekCount, mergedThisWeekCount, updateScrollAffordance])

  const handleStageClick = (stage: SeedStage) => {
    if (onSelectStage) {
      onSelectStage(stage)
      return
    }
    setActiveView(VIEWS.SEEDS, { stage })
  }

  const scrollStagesBy = (delta: number) => {
    stagesRef.current?.scrollBy({ left: delta, behavior: 'smooth' })
  }

  return (
    <div
      ref={containerRef}
      className={`flex items-center justify-between gap-2 sm:gap-3 p-2 sm:px-3.5 py-2 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/92 dark:bg-zinc-900/92 backdrop-blur-xl shadow-xs dark:ring-1 dark:ring-white/[0.04] ${className}`}
    >
      <div className="relative flex items-center min-w-0">
        {canScrollLeft && (
          <div className="absolute left-0 inset-y-0 z-10 flex items-center pl-1 pr-3 rounded-l-xl bg-gradient-to-r from-zinc-100 via-zinc-100/90 to-transparent dark:from-zinc-800 dark:via-zinc-800/90 pointer-events-none">
            <button
              type="button"
              onClick={() => scrollStagesBy(-160)}
              aria-label="Scroll stages left"
              className="pointer-events-auto w-6 h-6 rounded-full bg-white/95 dark:bg-zinc-900/95 border border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 shadow-xs flex items-center justify-center cursor-pointer transition"
            >
              <ChevronLeft size={13} strokeWidth={2.5} />
            </button>
          </div>
        )}

        <div
          ref={stagesRef}
          onScroll={updateScrollAffordance}
          className="flex items-center gap-1 p-1 rounded-xl bg-zinc-100/80 dark:bg-zinc-800/70 border border-zinc-200/70 dark:border-zinc-700/60 flex-nowrap min-w-0 overflow-x-auto scrollbar-none"
        >
          {SDLC_STAGES.map((stage, idx) => {
            const stageCount = seeds.filter((s) =>
              stage.id === 'merge'
                ? (s.stage === 'merge' && SEED_OPEN_STATUSES.has(s.status)) ||
                  s.status === 'merged'
                : s.stage === stage.id && SEED_OPEN_STATUSES.has(s.status)
            ).length
            const hasItems = stageCount > 0
            const isStageActive = seeds.some(
              (s) => s.stage === stage.id && s.status === 'running'
            )
            const isSelected = selectedStage === stage.id

            return (
              <React.Fragment key={stage.id}>
                <button
                  type="button"
                  data-stage-pill="true"
                  onClick={() => handleStageClick(stage.id)}
                  title={t('details.project.viewStageSeeds', { stage: stage.name })}
                  className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs transition-all duration-150 cursor-pointer group border shrink-0 whitespace-nowrap ${
                    isSelected
                      ? 'font-bold bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 border-teal-500/40 shadow-2xs opacity-100'
                      : hasItems
                        ? 'font-semibold text-zinc-800 dark:text-zinc-100 hover:text-zinc-950 dark:hover:text-white bg-white/50 dark:bg-zinc-900/40 hover:bg-white/90 dark:hover:bg-zinc-900/80 border-zinc-200/60 dark:border-zinc-700/50 hover:border-zinc-300/80 dark:hover:border-zinc-600/80 opacity-100'
                        : 'font-medium text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-white/80 dark:hover:bg-zinc-900/70 border-transparent hover:border-zinc-200/70 dark:hover:border-zinc-700/70 opacity-55 hover:opacity-100'
                  }`}
                >
                  <span className="relative flex h-2 w-2 items-center justify-center shrink-0">
                    {isStageActive && (
                      <span
                        className={`absolute -inset-0.5 rounded-full ${stage.pulseClass} animate-pulse`}
                      />
                    )}
                    <span
                      className={`relative w-2 h-2 rounded-full ${stage.dotClass} ${
                        hasItems || isSelected
                          ? 'opacity-100'
                          : 'opacity-45 group-hover:opacity-100'
                      } transition-all duration-200 group-hover:scale-125 ${stage.glowClass}`}
                    />
                  </span>
                  <span>{stage.name}</span>
                  <span
                    className={`font-mono text-xs px-1.5 py-0.5 rounded-full border transition-colors ml-0.5 min-w-[20px] text-center leading-none ${
                      isSelected
                        ? 'font-bold text-teal-700 dark:text-teal-300 bg-teal-500/15 border-teal-500/30'
                        : hasItems
                          ? `font-bold ${stage.textClass} bg-white/95 dark:bg-zinc-900/95 border-zinc-200/90 dark:border-zinc-700/90 shadow-2xs group-hover:border-zinc-300 dark:group-hover:border-zinc-600`
                          : 'font-medium text-zinc-400 dark:text-zinc-500 bg-zinc-200/50 dark:bg-zinc-800/50 border-zinc-200/60 dark:border-zinc-700/50 group-hover:text-zinc-600 dark:group-hover:text-zinc-300'
                    }`}
                  >
                    {stageCount}
                  </span>
                </button>
                {idx < SDLC_STAGES.length - 1 && (
                  <ChevronRight
                    size={13}
                    strokeWidth={2.25}
                    aria-hidden="true"
                    className="text-zinc-400/90 dark:text-zinc-500 shrink-0 select-none"
                  />
                )}
              </React.Fragment>
            )
          })}
        </div>

        {canScrollRight && (
          <div className="absolute right-0 inset-y-0 z-10 flex items-center pr-1 pl-3 rounded-r-xl bg-gradient-to-l from-zinc-100 via-zinc-100/90 to-transparent dark:from-zinc-800 dark:via-zinc-800/90 pointer-events-none">
            <button
              type="button"
              onClick={() => scrollStagesBy(160)}
              aria-label="Scroll stages right"
              className="pointer-events-auto w-6 h-6 rounded-full bg-white/95 dark:bg-zinc-900/95 border border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 shadow-xs flex items-center justify-center cursor-pointer transition"
            >
              <ChevronRight size={13} strokeWidth={2.5} />
            </button>
          </div>
        )}
      </div>

      {flowMode !== 'hidden' && (
        <div className="flex items-center shrink-0">
          <Tooltip
            content={`Weekly seed flow: +${createdThisWeekCount} ${t('dashboard.createdLabel')} · ${mergedThisWeekCount} ${t('dashboard.mergedThisWeekLabel')}`}
            placement="bottom"
          >
            <div className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl bg-zinc-100/70 dark:bg-zinc-800/60 border border-zinc-200/70 dark:border-zinc-700/60 cursor-default whitespace-nowrap">
              <span className="font-mono font-bold text-xs text-zinc-800 dark:text-zinc-200">
                +{createdThisWeekCount}
              </span>
              <span className="text-xs text-zinc-500 dark:text-zinc-400 font-medium">
                {t('dashboard.createdLabel')}
              </span>
              <span className="text-zinc-300 dark:text-zinc-700 select-none">·</span>
              <span
                className={`font-mono font-bold text-xs ${
                  mergedThisWeekCount > 0
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : 'text-zinc-700 dark:text-zinc-300'
                }`}
              >
                {mergedThisWeekCount}
              </span>
              <span className="text-xs text-zinc-500 dark:text-zinc-400 font-medium">
                {flowMode === 'compact'
                  ? t('dashboard.mergedLabel')
                  : t('dashboard.mergedThisWeekLabel')}
              </span>
              {mergedThisWeekCount > 0 && (
                <TrendingUp size={13} className="text-emerald-500 dark:text-emerald-400 shrink-0" />
              )}
            </div>
          </Tooltip>
        </div>
      )}
    </div>
  )
}
