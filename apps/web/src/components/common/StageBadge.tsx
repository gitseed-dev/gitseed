import React from 'react'
import { SEED_STAGES } from '../../constants'
import type { SeedStage } from '../../types'

interface StageBadgeProps {
  stage: SeedStage
  isClosed?: boolean
  className?: string
}

const STAGE_LABELS: Record<SeedStage, string> = {
  ideate: 'Ideate',
  plan: 'Plan',
  execute: 'Execute',
  review: 'Review',
  merge: 'Merge',
}

export const StageBadge: React.FC<StageBadgeProps> = ({
  stage,
  isClosed,
  className = '',
}) => {
  const getStageStyle = (s: SeedStage) => {
    if (isClosed) {
      return 'border-zinc-300 dark:border-zinc-700/80 bg-zinc-100/60 dark:bg-zinc-800/40 text-zinc-500 dark:text-zinc-400'
    }
    switch (s) {
      case SEED_STAGES.IDEATE:
        return 'border-pink-500/40 dark:border-pink-400/50 bg-pink-50 dark:bg-pink-950/60 text-pink-800 dark:text-pink-200'
      case SEED_STAGES.PLAN:
        return 'border-purple-500/40 dark:border-purple-400/50 bg-purple-50 dark:bg-purple-950/60 text-purple-800 dark:text-purple-200'
      case SEED_STAGES.EXECUTE:
        return 'border-blue-500/40 dark:border-blue-400/50 bg-blue-50 dark:bg-blue-950/60 text-blue-800 dark:text-blue-200'
      case SEED_STAGES.REVIEW:
        return 'border-cyan-500/40 dark:border-cyan-400/50 bg-cyan-50 dark:bg-cyan-950/60 text-cyan-800 dark:text-cyan-200'
      case SEED_STAGES.MERGE:
        return 'border-emerald-500/40 dark:border-emerald-400/50 bg-emerald-50 dark:bg-emerald-950/60 text-emerald-800 dark:text-emerald-200'
      default:
        return 'border-zinc-300 dark:border-zinc-700 bg-zinc-100 dark:bg-zinc-800/80 text-zinc-800 dark:text-zinc-200'
    }
  }

  return (
    <span
      className={`inline-flex items-center justify-center px-2.5 py-0.5 rounded-lg border font-semibold text-xs whitespace-nowrap ${getStageStyle(
        stage
      )} ${className}`.trim()}
    >
      <span>{STAGE_LABELS[stage] || stage}</span>
    </span>
  )
}
