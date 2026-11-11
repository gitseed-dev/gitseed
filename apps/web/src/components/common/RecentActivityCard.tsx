import React, { useMemo } from 'react'
import { Activity, ChevronRight } from 'lucide-react'
import { Card } from '@heroui/react'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import { parseActivityEvents } from '../../utils/activity'
import { ActivityRow } from './ActivityRow'
import type { ActivityEvent } from '../../types'

export interface RecentActivityCardProps {
  /** Activity events to display (defaults to DataContext scoped events) */
  events?: ActivityEvent[]
  /** Maximum number of events to render (default: 6) */
  maxItems?: number
  /** Callback when clicking View All (defaults to navigating to Activity/Telemetry view) */
  onViewAll?: () => void
  /** Hide the View All button */
  hideViewAll?: boolean
  /** Callback when clicking an event row linked to a seed */
  onSelectSeed?: (seedId: string) => void
  className?: string
}

export const RecentActivityCard: React.FC<RecentActivityCardProps> = ({
  events: propEvents,
  maxItems = 6,
  onViewAll,
  hideViewAll = false,
  onSelectSeed,
  className = '',
}) => {
  const { t } = useTranslation()
  const {
    events: contextEvents,
    allSeeds,
    projects,
    users,
    agents,
    backends,
    selectedProject,
    setActiveView,
    navigateToDetail,
  } = useData()

  const parsedItems = useMemo(() => {
    // Parse a few extra so collapsing repeats still fills the card
    const raw = (propEvents ?? contextEvents).slice(0, maxItems * 3)
    const parsed = parseActivityEvents(raw, {
      seeds: allSeeds,
      projects,
      users,
      agents,
      backends,
    })
    // Combine back-to-back identical events ("…updated credentials ×2")
    const collapsed: typeof parsed = []
    const counts: number[] = []
    for (const item of parsed) {
      const prev = collapsed[collapsed.length - 1]
      if (prev && prev.fullText === item.fullText && prev.event_type === item.event_type) {
        counts[counts.length - 1] += 1
        continue
      }
      collapsed.push(item)
      counts.push(1)
    }
    return collapsed.slice(0, maxItems).map((item, i) => {
      const n = counts[i]
      if (n < 2) return item
      const tag = ` ×${n}`
      return item.seedTitle
        ? { ...item, suffix: `${item.suffix || ''}${tag}`, fullText: `${item.fullText}${tag}` }
        : { ...item, prefix: `${item.prefix}${tag}`, fullText: `${item.fullText}${tag}` }
    })
  }, [propEvents, contextEvents, maxItems, allSeeds, projects, users, agents, backends])

  const handleViewAll = () => {
    if (onViewAll) {
      onViewAll()
    } else {
      setActiveView(VIEWS.TELEMETRY)
    }
  }

  const handleSeedClick = (seedId: string, subTab?: string) => {
    if (onSelectSeed) {
      onSelectSeed(seedId)
    } else {
      navigateToDetail(VIEWS.SEEDS, seedId, subTab)
    }
  }

  return (
    <Card
      className={`p-5 sm:p-6 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs h-fit ${className}`}
    >
      <div className="flex items-center justify-between pb-3.5 border-b border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <Activity size={16} />
          </div>
          <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
            {t('dashboard.activityTitle')}
          </h3>
        </div>

        {!hideViewAll && (
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

      <div className="mt-2.5 flex flex-col">
        {parsedItems.length === 0 ? (
          <div className="py-10 flex flex-col items-center justify-center text-center px-4 gap-2">
            <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 flex items-center justify-center text-zinc-400 mb-0.5">
              <Activity size={20} />
            </div>
            <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
              {t('dashboard.activityEmpty')}
            </h4>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm leading-relaxed">
              {t('telemetry.emptyFeedDesc')}
            </p>
          </div>
        ) : (
          parsedItems.map((item) => (
            <ActivityRow
              key={item.id}
              item={item}
              onNavigateSeed={handleSeedClick}
              showProject={!selectedProject}
              timeFormat="relative"
            />
          ))
        )}
      </div>
    </Card>
  )
}
