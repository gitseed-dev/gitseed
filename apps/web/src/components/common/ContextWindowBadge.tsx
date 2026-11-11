import React from 'react'
import { formatTokens } from '../../utils/format'
import type { ContextWindowInfo } from '../../types'

export interface ContextWindowBadgeProps {
  contextWindow?: ContextWindowInfo | null
  /** 'pill' shows a bordered badge with micro bar + token ratio; 'inline' shows a subtle compact meter */
  variant?: 'pill' | 'inline'
  /** Show `used / max` token counts alongside percentage in pill mode (default: true) */
  showTokens?: boolean
  /** Use peak tokens instead of latest turn tokens (useful for completed runs) */
  usePeak?: boolean
  className?: string
}

export const ContextWindowBadge: React.FC<ContextWindowBadgeProps> = ({
  contextWindow,
  variant = 'pill',
  showTokens = true,
  usePeak = false,
  className = '',
}) => {
  if (!contextWindow || contextWindow.max_tokens <= 0) return null
  const activeTokens = usePeak
    ? Math.max(contextWindow.peak_tokens || 0, contextWindow.used_tokens || 0)
    : contextWindow.used_tokens || contextWindow.peak_tokens || 0

  if (activeTokens <= 0) return null

  const maxTokens = contextWindow.max_tokens
  const rawPct = (activeTokens / maxTokens) * 100
  const pct = Math.min(100, Math.max(0, rawPct))
  const displayPct = pct < 1 && pct > 0 ? '<1' : `${Math.round(pct)}`

  const isCritical = pct >= 85
  const isWarning = !isCritical && pct >= 65

  const barFillClass = isCritical
    ? 'bg-rose-500 dark:bg-rose-400'
    : isWarning
      ? 'bg-amber-500 dark:bg-amber-400'
      : 'bg-sky-500 dark:bg-sky-400'

  const textToneClass = isCritical
    ? 'text-rose-700 dark:text-rose-300'
    : isWarning
      ? 'text-amber-700 dark:text-amber-300'
      : 'text-zinc-600 dark:text-zinc-300'

  const pillBorderBgClass = isCritical
    ? 'bg-rose-500/10 border-rose-500/30'
    : isWarning
      ? 'bg-amber-500/10 border-amber-500/30'
      : 'bg-white/75 dark:bg-zinc-900/75 border-zinc-200/80 dark:border-zinc-700/80'

  const tooltipParts = [
    `Context window: ${activeTokens.toLocaleString()} / ${maxTokens.toLocaleString()} tokens (${pct.toFixed(1)}%)`,
  ]
  if (
    contextWindow.peak_tokens > 0 &&
    contextWindow.peak_tokens !== contextWindow.used_tokens
  ) {
    tooltipParts.push(`Peak: ${contextWindow.peak_tokens.toLocaleString()} tok`)
  }
  if (contextWindow.turns > 0) {
    tooltipParts.push(
      `${contextWindow.turns} ${contextWindow.turns === 1 ? 'turn' : 'turns'}`
    )
  }
  const title = tooltipParts.join(' · ')

  if (variant === 'inline') {
    return (
      <span
        title={title}
        className={`inline-flex items-center gap-1.5 font-mono text-[11px] tabular-nums ${textToneClass} ${className}`}
      >
        <span className="w-5 h-1 rounded-full bg-zinc-200/90 dark:bg-zinc-700/90 overflow-hidden shrink-0">
          <span
            className={`block h-full rounded-full transition-all duration-300 ${barFillClass}`}
            style={{ width: `${Math.max(6, Math.min(100, pct))}%` }}
          />
        </span>
        <span>{displayPct}% ctx</span>
      </span>
    )
  }

  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-[11px] font-mono tabular-nums shrink-0 ${pillBorderBgClass} ${textToneClass} ${className}`}
    >
      <span className="w-7 h-1.5 rounded-full bg-zinc-200/90 dark:bg-zinc-700/90 overflow-hidden shrink-0">
        <span
          className={`block h-full rounded-full transition-all duration-300 ${barFillClass}`}
          style={{ width: `${Math.max(5, Math.min(100, pct))}%` }}
        />
      </span>
      <span className="font-semibold">{displayPct}% ctx</span>
      {showTokens && (
        <span className="text-zinc-400 dark:text-zinc-500">
          · {formatTokens(activeTokens)}/{formatTokens(maxTokens)}
        </span>
      )}
    </span>
  )
}

