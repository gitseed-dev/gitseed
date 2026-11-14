import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import {
  ArrowDown,
  ArrowRight,
  ArrowUp,
  ArrowUpDown,
  Kanban,
  LayoutGrid,
  List,
  Plus,
  Search,
  X,
} from 'lucide-react'
import { Button, Card, Chip } from '@heroui/react'
import { PageBreadcrumb } from '../common/PageBreadcrumb'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import type { SortDirection } from '../../hooks/useTableSort'
import { Pagination, type PaginationProps } from '../common/Pagination'

export { Pagination, type PaginationProps } from '../common/Pagination'

export interface PrimaryActionConfig {
  label: string
  onClick: () => void
  icon?: React.ReactNode
}

export interface EntityPageLayoutProps {
  embedded?: boolean
  title: string
  subtitle: string
  count: number
  isGlobal?: boolean
  hideOrgScopeBanner?: boolean
  primaryAction?: PrimaryActionConfig | React.ReactNode
  metrics?: React.ReactNode
  filterTabs?: React.ReactNode
  searchQuery?: string
  onSearchChange?: (query: string) => void
  searchPlaceholder?: string
  storageKey: string
  viewMode?: 'list' | 'board' | 'grid'
  onViewModeChange?: (mode: 'list' | 'board' | 'grid') => void
  showViewModeToggle?: boolean
  isEmpty: boolean
  emptyMessage?: React.ReactNode
  renderBoard?: () => React.ReactNode
  renderGrid?: () => React.ReactNode
  renderList: () => React.ReactNode
  pagination?: PaginationProps
  children?: React.ReactNode
}

export const OrgScopeBanner: React.FC<{ title: string }> = ({ title }) => {
  const { t } = useTranslation()
  const { selectedProjectObj, navigateToDetail } = useData()

  if (!selectedProjectObj) return null

  return (
    <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 px-3.5 py-2 rounded-xl bg-zinc-100/80 dark:bg-zinc-800/60 border border-zinc-200/70 dark:border-zinc-700/60 text-xs shadow-2xs">
      <div className="flex items-center gap-2 min-w-0">
        <span className="w-1.5 h-1.5 rounded-full bg-teal-500 shrink-0" />
        <span className="font-medium text-zinc-700 dark:text-zinc-300">
          {t('common.adminOrgWidePrefix', { title })}
        </span>
      </div>
      <button
        type="button"
        onClick={() => navigateToDetail(VIEWS.PROJECTS, selectedProjectObj.id)}
        className="font-medium text-teal-600 dark:text-teal-400 hover:text-teal-700 dark:hover:text-teal-300 hover:underline shrink-0 flex items-center gap-1 cursor-pointer transition-colors"
      >
        <span>{t('common.projectOverridesLink', { project: selectedProjectObj.name })}</span>
        <ArrowRight size={12} />
      </button>
    </div>
  )
}

export const EntityTableCard: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <Card
    className={`border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl rounded-2xl shadow-xs overflow-hidden p-0 dark:ring-1 dark:ring-white/[0.04] ${className}`}
  >
    <div className="overflow-x-auto">
      <table className="w-full text-left border-collapse text-xs">{children}</table>
    </div>
  </Card>
)

export const EntityTableHeaderRow: React.FC<{ children: React.ReactNode; className?: string }> = ({
  children,
  className = '',
}) => (
  <thead>
    <tr
      className={`bg-zinc-100/70 dark:bg-zinc-800/60 border-b border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400 font-semibold uppercase tracking-wider text-xs h-11 ${className}`}
    >
      {children}
    </tr>
  </thead>
)

export interface SortableThProps<K extends string = string> {
  sortKey: K
  currentSortKey?: K
  currentSortDirection?: SortDirection
  onSort: (key: K) => void
  children: React.ReactNode
  className?: string
  align?: 'left' | 'right' | 'center'
}

export const SortableTh = <K extends string>({
  sortKey,
  currentSortKey,
  currentSortDirection = 'asc',
  onSort,
  children,
  className = '',
  align = 'left',
}: SortableThProps<K>) => {
  const isSorted = currentSortKey === sortKey
  const isAsc = isSorted && currentSortDirection === 'asc'

  return (
    <th
      className={`py-3 ${className.includes('px-') ? '' : 'px-4'} font-semibold select-none cursor-pointer group hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors ${
        align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left'
      } ${className}`}
      onClick={() => onSort(sortKey)}
    >
      <div
        className={`inline-flex items-center gap-1.5 ${
          align === 'right' ? 'justify-end' : align === 'center' ? 'justify-center' : 'justify-start'
        }`}
      >
        <span>{children}</span>
        <span
          className={`inline-flex items-center transition-all ${
            isSorted
              ? 'opacity-100 text-teal-500 scale-100'
              : 'opacity-0 group-hover:opacity-40 text-zinc-400 scale-90 group-hover:scale-100'
          }`}
        >
          {isSorted ? (
            isAsc ? (
              <ArrowUp size={12} className="stroke-[2.5]" />
            ) : (
              <ArrowDown size={12} className="stroke-[2.5]" />
            )
          ) : (
            <ArrowUpDown size={12} />
          )}
        </span>
      </div>
    </th>
  )
}

export const EntityPageLayout: React.FC<EntityPageLayoutProps> = ({
  embedded = false,
  title,
  subtitle,
  count,
  isGlobal = false,
  hideOrgScopeBanner = false,
  primaryAction,
  metrics,
  filterTabs,
  searchQuery,
  onSearchChange,
  searchPlaceholder,
  storageKey,
  viewMode: controlledViewMode,
  onViewModeChange: controlledOnViewModeChange,
  showViewModeToggle = true,
  isEmpty,
  emptyMessage,
  renderBoard,
  renderGrid,
  renderList,
  pagination,
  children,
}) => {
  const { t } = useTranslation()

  const [internalViewMode, setInternalViewMode] = useState<'list' | 'board' | 'grid'>(() => {
    if (typeof window !== 'undefined') {
      const saved = localStorage.getItem(storageKey)
      if (saved === 'board' || saved === 'list' || saved === 'grid') {
        return saved
      }
    }
    return 'list'
  })

  const [isCompactSearch, setIsCompactSearch] = useState(false)
  const [isSearchExpanded, setIsSearchExpanded] = useState(false)
  const toolbarRowRef = useRef<HTMLDivElement>(null)
  const filterTabsRef = useRef<HTMLDivElement>(null)
  const lastTabsWidthRef = useRef<number>(0)
  const narrowSearchInputRef = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (isSearchExpanded) {
      narrowSearchInputRef.current?.focus()
    }
  }, [isSearchExpanded])

  const hasActiveSearch = Boolean(searchQuery?.trim())

  const hasBoardSupport = Boolean(renderBoard) && showViewModeToggle
  const hasGridSupport = Boolean(renderGrid) && showViewModeToggle

  useLayoutEffect(() => {
    if (!filterTabs || !onSearchChange) {
      setIsCompactSearch(false)
      return
    }
    const rowEl = toolbarRowRef.current
    if (!rowEl) return

    const updateCompact = () => {
      const tabsEl = filterTabsRef.current
      if (tabsEl && tabsEl.scrollWidth > 0) {
        lastTabsWidthRef.current = tabsEl.scrollWidth
      }
      const tabsWidth = lastTabsWidthRef.current
      const inlineSearchWidth = 200
      const toggleWidth = hasBoardSupport || hasGridSupport ? 74 : 0
      const embeddedActionWidth = embedded && primaryAction ? 110 : 0
      const safetyBuffer = 28

      const requiredWidth =
        tabsWidth +
        inlineSearchWidth +
        toggleWidth +
        embeddedActionWidth +
        safetyBuffer

      const nextCompact = rowEl.clientWidth < requiredWidth
      setIsCompactSearch(nextCompact)
      if (!nextCompact) {
        setIsSearchExpanded(false)
      }
    }

    updateCompact()

    const observer = new ResizeObserver(updateCompact)
    observer.observe(rowEl)
    if (filterTabsRef.current) {
      observer.observe(filterTabsRef.current)
    }
    return () => observer.disconnect()
  }, [filterTabs, onSearchChange, hasBoardSupport, hasGridSupport, embedded, primaryAction])
  const currentMode =
    hasBoardSupport || hasGridSupport ? (controlledViewMode ?? internalViewMode) : 'list'

  const handleModeChange = (mode: 'list' | 'board' | 'grid') => {
    if (controlledOnViewModeChange) {
      controlledOnViewModeChange(mode)
    } else {
      setInternalViewMode(mode)
    }
    try {
      localStorage.setItem(storageKey, mode)
    } catch {
      // Ignore storage errors
    }
  }

  const renderPrimaryAction = () => {
    if (!primaryAction) return null

    if (React.isValidElement(primaryAction)) {
      return primaryAction
    }

    const config = primaryAction as PrimaryActionConfig
    return (
      <Button
        variant="primary"
        size={embedded ? 'sm' : 'md'}
        onClick={config.onClick}
        className={`flex items-center gap-2 font-semibold shadow-lg shadow-teal-600/20 dark:shadow-teal-500/20 shrink-0 rounded-xl cursor-pointer ${
          embedded ? 'h-[34px] text-xs px-3' : ''
        }`}
      >
        {config.icon || <Plus size={embedded ? 14 : 16} />}
        <span>{config.label}</span>
      </Button>
    )
  }

  return (
    <div className={`flex flex-col ${embedded ? 'gap-4' : 'gap-6'}`}>
      {!embedded && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
          <div>
            <PageBreadcrumb
              currentLabel={title}
              hideProject={isGlobal}
              className="mb-1.5"
            />

            <div className="flex items-center gap-2.5">
              <h2 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100">
                {title}
              </h2>
              <Chip
                size="sm"
                variant="soft"
                className="font-mono text-xs font-bold bg-zinc-200 dark:bg-zinc-800 text-zinc-900 dark:text-zinc-100 border border-zinc-300 dark:border-zinc-700 shadow-2xs px-2"
              >
                {count}
              </Chip>
            </div>
            <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1">{subtitle}</p>
          </div>
          {renderPrimaryAction()}
        </div>
      )}

      {!embedded && isGlobal && !hideOrgScopeBanner && <OrgScopeBanner title={title} />}

      {metrics && <div>{metrics}</div>}

      {(filterTabs ||
        onSearchChange ||
        hasGridSupport ||
        (embedded && primaryAction)) && (
        <Card className="relative z-20 overflow-visible p-2.5 sm:p-3.5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl rounded-2xl shadow-xs dark:ring-1 dark:ring-white/[0.04]">
          <div
            ref={toolbarRowRef}
            className="flex items-center justify-between gap-2 sm:gap-3 min-w-0 w-full"
          >
            {filterTabs && (
              <div
                ref={filterTabsRef}
                className={`${
                  isCompactSearch && isSearchExpanded ? 'hidden' : 'flex'
                } items-center gap-1.5 relative z-10 min-w-0 max-w-full overflow-x-auto scrollbar-none`}
              >
                {filterTabs}
              </div>
            )}

            <div
              className={`flex items-center gap-2 relative z-10 justify-end ${
                !filterTabs || (isCompactSearch && isSearchExpanded)
                  ? 'flex-1'
                  : 'shrink-0'
              }`}
            >
              {embedded && renderPrimaryAction()}
              {onSearchChange && (
                <>
                  {!isCompactSearch ? (
                    <div
                      className={`${
                        filterTabs ? 'w-48 xl:w-56' : 'flex-1 sm:flex-initial sm:w-56'
                      } relative shrink-0`}
                    >
                      <Search
                        size={14}
                        className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
                      />
                      <input
                        type="text"
                        className="w-full h-[34px] bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-8 pr-7 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50"
                        placeholder={searchPlaceholder}
                        value={searchQuery || ''}
                        onChange={(e) => onSearchChange(e.target.value)}
                      />
                      {searchQuery && (
                        <button
                          type="button"
                          onClick={() => onSearchChange('')}
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
                        className="w-full h-[34px] bg-zinc-100 dark:bg-zinc-800 border border-teal-500/50 dark:border-teal-500/50 rounded-xl pl-8 pr-8 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50"
                        placeholder={searchPlaceholder}
                        value={searchQuery || ''}
                        onChange={(e) => onSearchChange(e.target.value)}
                        onKeyDown={(e) => {
                          if (e.key === 'Escape' || e.key === 'Enter') {
                            setIsSearchExpanded(false)
                          }
                        }}
                        onBlur={() => {
                          setIsSearchExpanded(false)
                        }}
                      />
                      <button
                        type="button"
                        onMouseDown={(e) => e.preventDefault()}
                        onClick={() => {
                          if (searchQuery) {
                            onSearchChange('')
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
                          ? `${searchPlaceholder || 'Search'}: "${searchQuery}"`
                          : searchPlaceholder || 'Search'
                      }
                      aria-label={searchPlaceholder || 'Search'}
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
                </>
              )}

              {hasBoardSupport && (
                <div className="flex items-center h-[34px] p-0.5 rounded-xl border border-zinc-200 dark:border-zinc-700/70 bg-zinc-100/70 dark:bg-zinc-800/60 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleModeChange('list')}
                    title={t('common.listView')}
                    aria-label={t('common.listView')}
                    className={`h-full px-2 rounded-lg flex items-center justify-center transition cursor-pointer ${
                      currentMode === 'list'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs font-semibold'
                        : 'text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200'
                    }`}
                  >
                    <List size={15} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleModeChange('board')}
                    title={t('common.boardView')}
                    aria-label={t('common.boardView')}
                    className={`h-full px-2 rounded-lg flex items-center justify-center transition cursor-pointer ${
                      currentMode === 'board'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs font-semibold'
                        : 'text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200'
                    }`}
                  >
                    <Kanban size={15} />
                  </button>
                </div>
              )}

              {!hasBoardSupport && hasGridSupport && (
                <div className="flex items-center h-[34px] p-0.5 rounded-xl border border-zinc-200 dark:border-zinc-700/70 bg-zinc-100/70 dark:bg-zinc-800/60 shrink-0">
                  <button
                    type="button"
                    onClick={() => handleModeChange('list')}
                    title={t('common.listView')}
                    aria-label={t('common.listView')}
                    className={`h-full px-2 rounded-lg flex items-center justify-center transition cursor-pointer ${
                      currentMode === 'list'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs font-semibold'
                        : 'text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200'
                    }`}
                  >
                    <List size={15} />
                  </button>
                  <button
                    type="button"
                    onClick={() => handleModeChange('grid')}
                    title={t('common.cardsView')}
                    aria-label={t('common.cardsView')}
                    className={`h-full px-2 rounded-lg flex items-center justify-center transition cursor-pointer ${
                      currentMode === 'grid'
                        ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs font-semibold'
                        : 'text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200'
                    }`}
                  >
                    <LayoutGrid size={15} />
                  </button>
                </div>
              )}
            </div>
          </div>
        </Card>
      )}

      {/* Main Content Area */}
      <div className="relative z-10 flex flex-col gap-4">
        {currentMode === 'board' && renderBoard ? (
          renderBoard()
        ) : isEmpty ? (
          <Card className="p-8 text-center border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl rounded-2xl shadow-xs text-zinc-500 text-sm dark:ring-1 dark:ring-white/[0.04]">
            {emptyMessage}
          </Card>
        ) : currentMode === 'grid' && renderGrid ? (
          renderGrid()
        ) : (
          renderList()
        )}

        {pagination &&
          currentMode !== 'board' &&
          pagination.totalItems > 0 &&
          Math.ceil(pagination.totalItems / pagination.pageSize) > 1 && (
            <div className="px-1 py-1">
              <Pagination {...pagination} />
            </div>
          )}
      </div>

      {/* Children for Modals / Dialogs */}
      {children}
    </div>
  )
}

