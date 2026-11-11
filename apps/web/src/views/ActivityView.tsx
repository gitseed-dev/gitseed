import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { Activity, ChevronDown, Download, Search, X } from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { VIEWS } from '../constants'
import {
  ACTIVITY_EVENT_TYPE_GROUPS,
  parseActivityEvents,
  type ActivityCategory,
  type ParsedActivity,
} from '../utils/activity'
import { ActivityRow } from '../components/common/ActivityRow'
import { PageBreadcrumb } from '../components/common/PageBreadcrumb'
import { ActivityExportModal } from '../components/modals/ActivityExportModal'

type CategoryFilter = 'all' | ActivityCategory

export const ActivityView: React.FC = () => {
  const { t } = useTranslation()
  const {
    events,
    allSeeds,
    projects,
    users,
    agents,
    backends,
    selectedProject,
    navigateToDetail,
  } = useData()

  const [categoryFilter, setCategoryFilter] = useState<CategoryFilter>('all')
  const [eventTypeFilter, setEventTypeFilter] = useState<string>('all')
  const [projectFilter, setProjectFilter] = useState<string>('all')
  const [searchQuery, setSearchQuery] = useState<string>('')
  const [isExportModalOpen, setIsExportModalOpen] = useState(false)
  const [isCompactSearch, setIsCompactSearch] = useState(false)
  const [isSearchExpanded, setIsSearchExpanded] = useState(false)
  const filterRowRef = useRef<HTMLDivElement>(null)
  const pillsRef = useRef<HTMLDivElement>(null)
  const lastPillsWidthRef = useRef<number>(0)
  const narrowSearchInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isSearchExpanded) {
      narrowSearchInputRef.current?.focus()
    }
  }, [isSearchExpanded])

  const hasActiveSearch = Boolean(searchQuery.trim())

  useLayoutEffect(() => {
    const rowEl = filterRowRef.current
    if (!rowEl) return

    const updateCompact = () => {
      const pillsEl = pillsRef.current
      if (pillsEl && pillsEl.scrollWidth > 0) {
        lastPillsWidthRef.current = pillsEl.scrollWidth
      }
      const pillsWidth = lastPillsWidthRef.current
      const inlineSearchWidth = 200
      const projectSelectWidth = !selectedProject ? 185 : 0
      const safetyBuffer = 28

      const nextCompact =
        rowEl.clientWidth <
        pillsWidth + inlineSearchWidth + projectSelectWidth + safetyBuffer
      setIsCompactSearch(nextCompact)
      if (!nextCompact) {
        setIsSearchExpanded(false)
      }
    }

    updateCompact()

    const observer = new ResizeObserver(updateCompact)
    observer.observe(rowEl)
    if (pillsRef.current) {
      observer.observe(pillsRef.current)
    }
    return () => observer.disconnect()
  }, [selectedProject])

  const activeProjects = useMemo(
    () =>
      projects
        .filter((p) => !p.archived_at)
        .sort((a, b) => a.name.localeCompare(b.name)),
    [projects]
  )

  const parsedActivities = useMemo(
    () =>
      parseActivityEvents(events, {
        seeds: allSeeds,
        projects,
        users,
        agents,
        backends,
      }),
    [events, allSeeds, projects, users, agents, backends]
  )

  const effectiveProjectFilter = selectedProject || projectFilter

  // Base list filtered by project + search (used for category counts)
  const projectAndSearchFiltered = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    return parsedActivities.filter((item) => {
      if (effectiveProjectFilter !== 'all' && item.projectId !== effectiveProjectFilter) {
        return false
      }
      if (q) {
        const matchesSummary = item.fullText.toLowerCase().includes(q)
        const matchesEvent = item.event_type.toLowerCase().includes(q)
        const matchesProject = (item.projectName || '').toLowerCase().includes(q)
        const matchesSeed = (item.seedTitle || '').toLowerCase().includes(q)
        if (!matchesSummary && !matchesEvent && !matchesProject && !matchesSeed) {
          return false
        }
      }
      return true
    })
  }, [parsedActivities, effectiveProjectFilter, searchQuery])

  const categoryCounts = useMemo(() => {
    const counts: Record<CategoryFilter, number> = {
      all: projectAndSearchFiltered.length,
      approvals: 0,
      stages: 0,
      agents: 0,
      admin: 0,
    }
    for (const item of projectAndSearchFiltered) {
      counts[item.category] = (counts[item.category] || 0) + 1
    }
    return counts
  }, [projectAndSearchFiltered])

  const eventTypeCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    for (const item of projectAndSearchFiltered) {
      if (categoryFilter === 'all' || item.category === categoryFilter) {
        counts[item.event_type] = (counts[item.event_type] || 0) + 1
      }
    }
    return counts
  }, [projectAndSearchFiltered, categoryFilter])

  const visibleEventTypeGroups = useMemo(() => {
    if (categoryFilter === 'all') return ACTIVITY_EVENT_TYPE_GROUPS
    return ACTIVITY_EVENT_TYPE_GROUPS.filter((g) => g.category === categoryFilter)
  }, [categoryFilter])

  const filteredActivities = useMemo(() => {
    return projectAndSearchFiltered.filter((item) => {
      if (categoryFilter !== 'all' && item.category !== categoryFilter) {
        return false
      }
      if (eventTypeFilter !== 'all' && item.event_type !== eventTypeFilter) {
        return false
      }
      return true
    })
  }, [projectAndSearchFiltered, categoryFilter, eventTypeFilter])

  const groupedByDay = useMemo(() => {
    const groups: Array<{ day: string; items: ParsedActivity[] }> = []
    const map = new Map<string, ParsedActivity[]>()

    for (const item of filteredActivities) {
      let arr = map.get(item.dayGroup)
      if (!arr) {
        arr = []
        map.set(item.dayGroup, arr)
        groups.push({ day: item.dayGroup, items: arr })
      }
      arr.push(item)
    }

    return groups
  }, [filteredActivities])

  const categoryPills: Array<{ key: CategoryFilter; label: string }> = [
    { key: 'all', label: t('telemetry.categories.all') },
    { key: 'approvals', label: t('telemetry.categories.approvals') },
    { key: 'stages', label: t('telemetry.categories.stages') },
    { key: 'agents', label: t('telemetry.categories.agents') },
    { key: 'admin', label: t('telemetry.categories.admin') },
  ]

  const handleNavigateSeed = (seedId: string, subTab?: string) => {
    navigateToDetail(VIEWS.SEEDS, seedId, subTab)
  }

  const hasActiveFilters =
    categoryFilter !== 'all' ||
    eventTypeFilter !== 'all' ||
    (!selectedProject && projectFilter !== 'all') ||
    searchQuery.trim().length > 0

  const handleClearFilters = () => {
    setCategoryFilter('all')
    setEventTypeFilter('all')
    setProjectFilter('all')
    setSearchQuery('')
  }

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto pb-16">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <PageBreadcrumb currentLabel={t('telemetry.title')} className="mb-1.5" />
          <h2 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100">
            {t('telemetry.title')}
          </h2>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            {t('telemetry.subtitle')}
          </p>
        </div>

        <Button
          size="md"
          variant="secondary"
          onPress={() => setIsExportModalOpen(true)}
          className="flex items-center gap-2 font-semibold rounded-xl border border-zinc-200 dark:border-zinc-700/80 cursor-pointer self-start sm:self-auto shrink-0"
        >
          <Download size={16} />
          <span>{t('telemetry.exportLog')}</span>
        </Button>
      </div>

      {/* Filter Bar */}
      <Card className="p-2.5 sm:p-3.5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl rounded-2xl shadow-xs dark:ring-1 dark:ring-white/[0.04]">
        <div
          ref={filterRowRef}
          className="flex items-center justify-between gap-2.5 min-w-0 w-full"
        >
          {/* Category Chips */}
          <div
            ref={pillsRef}
            className={`${
              isCompactSearch && isSearchExpanded ? 'hidden' : 'flex'
            } items-center gap-1.5 overflow-x-auto scrollbar-none flex-nowrap min-w-0`}
          >
            {/* Same segmented control as Seeds / Sessions, not custom chips */}
            <SegmentedControl<CategoryFilter>
              ariaLabel="Filter activity by category"
              selectedId={categoryFilter}
              onSelect={(nextCat) => {
                setCategoryFilter(nextCat)
                if (nextCat !== 'all' && eventTypeFilter !== 'all') {
                  const belongs = ACTIVITY_EVENT_TYPE_GROUPS.some(
                    (g) =>
                      g.category === nextCat &&
                      g.options.some((opt) => opt.type === eventTypeFilter)
                  )
                  if (!belongs) setEventTypeFilter('all')
                }
              }}
              items={categoryPills.map((pill) => ({
                id: pill.key,
                label: pill.label,
                count: categoryCounts[pill.key],
              }))}
            />
          </div>

          {/* Search + Event Type + Project Dropdown */}
          <div
            className={`flex items-center gap-2 ${
              isCompactSearch && isSearchExpanded ? 'flex-1' : 'shrink-0'
            }`}
          >
            {/* Specific Event Type Dropdown (Grouped & Alphabetically Sorted) */}
            <div className="relative shrink-0">
              <select
                value={eventTypeFilter}
                onChange={(e) => setEventTypeFilter(e.target.value)}
                aria-label="Filter by event type"
                className={`border rounded-xl pl-3 pr-7 h-[34px] text-xs font-medium focus:outline-none focus:ring-2 focus:ring-teal-500/40 max-w-[165px] sm:max-w-[215px] truncate appearance-none cursor-pointer ${
                  eventTypeFilter !== 'all'
                    ? 'bg-teal-500/10 border-teal-500/40 text-teal-700 dark:text-teal-300'
                    : 'bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200'
                }`}
              >
                <option value="all">All Event Types</option>
                {visibleEventTypeGroups.map((group) => (
                  <optgroup key={group.category} label={group.label}>
                    {group.options.map((opt) => {
                      const count = eventTypeCounts[opt.type] || 0
                      return (
                        <option key={opt.type} value={opt.type}>
                          {opt.type} · {opt.label}
                          {count > 0 ? ` (${count})` : ''}
                        </option>
                      )
                    })}
                  </optgroup>
                ))}
              </select>
              <ChevronDown
                size={13}
                className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
              />
            </div>
            {!isCompactSearch ? (
              <div className="relative w-48 xl:w-56 shrink-0">
                <Search
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
                />
                <input
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder={t('telemetry.searchPlaceholder')}
                  className="w-full h-[34px] bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-8 pr-7 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50"
                />
                {searchQuery && (
                  <button
                    type="button"
                    onClick={() => setSearchQuery('')}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                    aria-label="Clear search"
                  >
                    <X size={13} />
                  </button>
                )}
              </div>
            ) : isSearchExpanded ? (
              <div className="flex relative flex-1 min-w-0">
                <Search
                  size={14}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
                />
                <input
                  ref={narrowSearchInputRef}
                  type="text"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === 'Escape' || e.key === 'Enter') {
                      setIsSearchExpanded(false)
                    }
                  }}
                  onBlur={() => {
                    setIsSearchExpanded(false)
                  }}
                  placeholder={t('telemetry.searchPlaceholder')}
                  className="w-full h-[34px] bg-zinc-100 dark:bg-zinc-800 border border-teal-500/50 dark:border-teal-500/50 rounded-xl pl-8 pr-8 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50"
                />
                <button
                  type="button"
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    if (searchQuery) {
                      setSearchQuery('')
                    }
                    setIsSearchExpanded(false)
                  }}
                  className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                  aria-label={searchQuery ? 'Clear search' : 'Close search'}
                >
                  <X size={13} />
                </button>
              </div>
            ) : (
              <button
                type="button"
                onClick={() => setIsSearchExpanded(true)}
                title={
                  hasActiveSearch
                    ? `${t('telemetry.searchPlaceholder')}: "${searchQuery}"`
                    : t('telemetry.searchPlaceholder')
                }
                aria-label={t('telemetry.searchPlaceholder')}
                className={`relative h-[34px] ${
                  hasActiveSearch ? 'px-2.5 gap-1.5' : 'w-[34px]'
                } rounded-xl border flex items-center justify-center transition cursor-pointer shrink-0 ${
                  hasActiveSearch
                    ? 'border-teal-500/40 bg-teal-500/10 text-teal-700 dark:text-teal-300'
                    : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                <Search size={14} className="shrink-0" />
                {hasActiveSearch && (
                  <span className="text-xs font-medium max-w-[80px] truncate">
                    {searchQuery}
                  </span>
                )}
              </button>
            )}

            {!selectedProject && (
              <div className="flex items-center gap-2 shrink-0">
                <div className="relative">
                  <select
                    value={projectFilter}
                    onChange={(e) => setProjectFilter(e.target.value)}
                    aria-label={t('telemetry.projectFilterLabel')}
                    className="bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-3 pr-7 h-[34px] text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 max-w-[150px] sm:max-w-[200px] truncate appearance-none cursor-pointer"
                  >
                    <option value="all">{t('telemetry.allProjects')}</option>
                    {activeProjects.map((proj) => (
                      <option key={proj.id} value={proj.id}>
                        {proj.name}
                      </option>
                    ))}
                  </select>
                  <ChevronDown
                    size={13}
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
                  />
                </div>
              </div>
            )}
          </div>
        </div>
      </Card>

      {/* Human-Readable Activity Feed Grouped by Day */}
      <Card className="p-5 sm:p-6 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl rounded-2xl shadow-xs flex flex-col gap-6 dark:ring-1 dark:ring-white/[0.04]">
        {groupedByDay.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 px-4 text-center">
            <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 flex items-center justify-center text-zinc-400 mb-3">
              <Activity size={22} />
            </div>
            <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
              {t('telemetry.emptyFeedTitle')}
            </h3>
            <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1 max-w-md leading-relaxed">
              {t('telemetry.emptyFeedDesc')}
            </p>
            {hasActiveFilters && (
              <button
                type="button"
                onClick={handleClearFilters}
                className="mt-3 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                {t('common.clearFilters')}
              </button>
            )}
          </div>
        ) : (
          groupedByDay.map((group) => (
            <div key={group.day} className="flex flex-col gap-1.5">
              <div className="text-xs font-semibold text-zinc-400 dark:text-zinc-500 uppercase tracking-wider px-3 pb-1 border-b border-zinc-100 dark:border-zinc-800/60">
                {group.day}
              </div>

              <div className="divide-y divide-zinc-100 dark:divide-zinc-800/70">
                {group.items.map((item) => (
                  <ActivityRow
                    key={item.id}
                    item={item}
                    onNavigateSeed={handleNavigateSeed}
                    showProject={effectiveProjectFilter === 'all'}
                    timeFormat="clock"
                  />
                ))}
              </div>
            </div>
          ))
        )}
      </Card>

      <ActivityExportModal
        isOpen={isExportModalOpen}
        onClose={() => setIsExportModalOpen(false)}
        activities={filteredActivities}
        activeCategory={categoryFilter}
        activeProjectName={
          effectiveProjectFilter === 'all'
            ? null
            : projects.find((p) => p.id === effectiveProjectFilter)?.name || null
        }
      />
    </div>
  )
}
