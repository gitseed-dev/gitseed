import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Cpu,
  Edit2,
  GitBranch,
  Plus,
  Power,
  ShieldAlert,
  Terminal,
  Trash2,
  Wrench,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { ActionMenu } from '../components/common/ActionMenu'
import { ConfirmDialog } from '../components/common/ConfirmDialog'
import { ProviderIcon } from '../components/common/ProviderIcon'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { Tooltip } from '../components/common/Tooltip'
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../components/layout/EntityPageLayout'
import { BackendDrawer } from '../components/modals/BackendDrawer'
import { BACKEND_TYPE_OPTIONS } from '../components/modals/backend/BackendFormSections'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { usePagination } from '../hooks/usePagination'
import { useTableSort } from '../hooks/useTableSort'
import {
  BACKEND_TYPE_ORDER,
  formatCleanEndpoint,
  formatProviderName,
  getBackendAccessSummary,
  getBackendCapacityUsage,
  getProjectsUsingBackend,
} from '../utils/backends'
import { formatRelativeTime } from '../utils/format'
import type { Backend, BackendType } from '../types'

export const BackendsView: React.FC = () => {
  const {
    backends,
    agents,
    projects,
    projectReposByProject,
    runs,
    activeDetailId,
    viewFilterParams,
    clearDetail,
  } = useData()
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const gitBackends = useMemo(() => backends.filter((b) => b.type === 'git'), [backends])
  const modelBackends = useMemo(() => backends.filter((b) => b.type === 'inference'), [backends])
  const computeBackends = useMemo(() => backends.filter((b) => b.type === 'compute'), [backends])
  const toolBackends = useMemo(() => backends.filter((b) => b.type === 'tools'), [backends])

  const coreBackendChecks = useMemo(() => {
    const items: { id: BackendType; label: string; ready: boolean }[] = [
      {
        id: 'compute',
        label: 'Compute',
        ready: computeBackends.some((b) => b.enabled),
      },
      {
        id: 'inference',
        label: 'Inference',
        ready: modelBackends.some((b) => b.enabled),
      },
      {
        id: 'git',
        label: 'Git',
        ready: gitBackends.some((b) => b.enabled),
      },
    ]
    return {
      items,
      allReady: items.every((i) => i.ready),
    }
  }, [computeBackends, modelBackends, gitBackends])

  const [selectedFilter, setSelectedFilter] = useState<'all' | BackendType>('all')
  const [searchQuery, setSearchQuery] = useState('')
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [selectedBackendId, setSelectedBackendId] = useState<string | null>(null)
  const [newBackendType, setNewBackendType] = useState<BackendType>('inference')
  const [registerMenuOpen, setRegisterMenuOpen] = useState(false)
  const registerMenuRef = useRef<HTMLDivElement>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [testStatus, setTestStatus] = useState<'idle' | 'testing' | 'succeeded' | 'failed'>('idle')
  const [testSuccessMap, setTestSuccessMap] = useState<Record<string, number>>({})
  const [deleteConfirm, setDeleteConfirm] = useState<Backend | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const [isDeleting, setIsDeleting] = useState(false)

  const selectedBackend = useMemo(
    () => (selectedBackendId ? backends.find((b) => b.id === selectedBackendId) || null : null),
    [backends, selectedBackendId]
  )

  useEffect(() => {
    if (!registerMenuOpen) return
    const handleClickOutside = (e: MouseEvent) => {
      if (registerMenuRef.current && !registerMenuRef.current.contains(e.target as Node)) {
        setRegisterMenuOpen(false)
      }
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setRegisterMenuOpen(false)
    }
    document.addEventListener('mousedown', handleClickOutside)
    window.addEventListener('keydown', handleKeyDown)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      window.removeEventListener('keydown', handleKeyDown)
    }
  }, [registerMenuOpen])

  const openBackendDrawer = (b: Backend | null, initialType?: BackendType) => {
    setSelectedBackendId(b ? b.id : null)
    if (!b && initialType) {
      setNewBackendType(initialType)
    }
    setRegisterMenuOpen(false)
    setDrawerOpen(true)
    try {
      const sp = new URLSearchParams(window.location.search)
      sp.set('open', b ? b.id : 'new')
      const qs = sp.toString()
      const newUrl = `${window.location.pathname}?${qs}`
      if (window.location.search !== `?${qs}`) {
        window.history.pushState(null, '', newUrl)
      }
    } catch {
      // Ignore URL history errors
    }
  }

  const closeBackendDrawer = () => {
    setDrawerOpen(false)
    setSelectedBackendId(null)
    clearDetail()
    try {
      const sp = new URLSearchParams(window.location.search)
        if (sp.has('open') || sp.has('type') || sp.has('provider')) {
          sp.delete('open')
          sp.delete('type')
          sp.delete('provider')
          const qs = sp.toString()
          const newUrl = qs ? `${window.location.pathname}?${qs}` : window.location.pathname
          window.history.pushState(null, '', newUrl)
        }
      } catch {
        // Ignore URL history errors
      }
    }

  // Deep link support via ?open=<id|new> or activeDetailId
  useEffect(() => {
      const syncFromUrl = () => {
        try {
          const sp = new URLSearchParams(window.location.search)
          const openParam = sp.get('open') || activeDetailId

          if (openParam === 'new') {
            setSelectedBackendId(null)
            setDrawerOpen(true)
          } else if (openParam) {
            const found = backends.find(
              (b) =>
                b.id === openParam ||
                b.name.toLowerCase().replace(/[^a-z0-9]+/g, '-') === openParam.toLowerCase()
            )
            if (found) {
              setSelectedBackendId(found.id)
              setDrawerOpen(true)
            }
          }
        } catch {
          // Ignore
        }
      }

      syncFromUrl()
      window.addEventListener('popstate', syncFromUrl)
      return () => window.removeEventListener('popstate', syncFromUrl)
    }, [backends, activeDetailId, viewFilterParams])

    const handleTestConnection = async (b: Backend) => {
      setTestingId(b.id)
      setTestStatus('testing')
      try {
        const updated = await api.backends.test(b.id)
        await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
        if (updated.health === 'healthy' && updated.health_latency_ms !== null) {
          setTestStatus('succeeded')
          setTestSuccessMap((prev) => ({ ...prev, [b.id]: updated.health_latency_ms! }))
          setTimeout(() => {
            setTestSuccessMap((prev) => {
              const next = { ...prev }
              delete next[b.id]
              return next
            })
          }, 3500)
        } else {
          setTestStatus('failed')
        }
      } catch (err) {
        console.error('Failed to test backend connection:', err)
        setTestStatus('failed')
      } finally {
        setTestingId(null)
      }
    }

    const handleToggleEnabled = async (b: Backend) => {
      try {
        await api.backends.update(b.id, { enabled: !b.enabled })
        await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      } catch (err) {
        console.error('Failed to toggle backend enabled state:', err)
      }
    }

    const handleConfirmDelete = async () => {
      if (!deleteConfirm || isDeleting) return
      setIsDeleting(true)
      setDeleteError(null)
      try {
        await api.backends.delete(deleteConfirm.id)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
          queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        ])
        setDeleteConfirm(null)
        closeBackendDrawer()
      } catch (err) {
        if (err instanceof ApiError) {
          setDeleteError(err.detail)
        } else {
          setDeleteError(err instanceof Error ? err.message : 'Unable to deregister backend')
        }
      } finally {
        setIsDeleting(false)
      }
    }

    const getTypeInfo = (type: BackendType) => {
      switch (type) {
        case 'git':
          return {
            icon: <GitBranch size={12} className="shrink-0 text-zinc-500 dark:text-zinc-400" />,
            badgeBg:
              'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-200/80 dark:border-zinc-700/60',
            label: t('backends.types.git'),
          }
        case 'inference':
          return {
            icon: <Cpu size={12} className="shrink-0 text-zinc-500 dark:text-zinc-400" />,
            badgeBg:
              'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-200/80 dark:border-zinc-700/60',
            label: t('backends.types.inference'),
          }
        case 'compute':
          return {
            icon: <Terminal size={12} className="shrink-0 text-zinc-500 dark:text-zinc-400" />,
            badgeBg:
              'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-200/80 dark:border-zinc-700/60',
            label: t('backends.types.compute'),
          }
        case 'tools':
          return {
            icon: <Wrench size={12} className="shrink-0 text-zinc-500 dark:text-zinc-400" />,
            badgeBg:
              'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-200/80 dark:border-zinc-700/60',
            label: t('backends.types.tools'),
          }
      }
    }

    const filteredBackends = useMemo(() => {
      return backends.filter((b) => {
        if (selectedFilter !== 'all' && b.type !== selectedFilter) return false
        if (searchQuery.trim()) {
          const q = searchQuery.toLowerCase().trim()
          const matchesName = b.name.toLowerCase().includes(q)
          const matchesProvider =
            b.provider.toLowerCase().includes(q) ||
            formatProviderName(b.provider).toLowerCase().includes(q)
          const matchesEndpoint = (b.endpoint || '').toLowerCase().includes(q)
          if (!matchesName && !matchesProvider && !matchesEndpoint) return false
        }
        return true
      })
    }, [backends, selectedFilter, searchQuery])

    type BackendSortKey = 'name' | 'type' | 'used_by' | 'capacity' | 'access' | 'status'

    const {
      sortedItems: sortedBackends,
      sortConfig,
      requestSort,
    } = useTableSort<Backend, BackendSortKey>({
      items: filteredBackends,
      initialKey: 'name',
      initialDirection: 'asc',
      customComparators: {
        type: (a, b) =>
          (BACKEND_TYPE_ORDER[a.type] ?? 99) - (BACKEND_TYPE_ORDER[b.type] ?? 99) ||
          a.name.localeCompare(b.name),
        name: (a, b) => a.name.localeCompare(b.name),
        used_by: (a, b) =>
          getProjectsUsingBackend(a, projects, projectReposByProject).length -
          getProjectsUsingBackend(b, projects, projectReposByProject).length,
        capacity: (a, b) =>
          (getBackendCapacityUsage(a, runs).max ?? -1) -
          (getBackendCapacityUsage(b, runs).max ?? -1),
        access: (a, b) =>
          getBackendAccessSummary(a, agents).count - getBackendAccessSummary(b, agents).count,
        status: (a, b) => (a.enabled === b.enabled ? 0 : a.enabled ? -1 : 1),
      },
    })

    const {
      paginatedItems: pagedBackends,
      paginationProps,
      totalPages,
    } = usePagination({
      items: sortedBackends,
      initialPageSize: 10,
    })

    const renderStatusCell = (b: Backend) => {
      const verifiedLatency = testSuccessMap[b.id]
      if (verifiedLatency !== undefined) {
        return (
          <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
            <span>Verified ({verifiedLatency}ms)</span>
          </span>
        )
      }

      if (!b.enabled) {
        return (
          <span className="inline-flex items-center gap-1.5 text-xs text-zinc-400 dark:text-zinc-500 cursor-default">
            <span className="w-2 h-2 rounded-full bg-zinc-400 shrink-0" />
            <span>{t('backends.status.disabled')}</span>
          </span>
        )
      }

      if (b.health === 'unreachable') {
        const tooltipText = [
          b.health_error || 'Connection unreachable',
          b.health_checked_at ? `Last check ${formatRelativeTime(b.health_checked_at)}` : null,
        ]
          .filter(Boolean)
          .join(' • ')
        return (
          <Tooltip content={tooltipText} placement="top">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-rose-600 dark:text-rose-400 cursor-default">
              <span className="w-2 h-2 rounded-full bg-rose-500 shrink-0 animate-pulse" />
              <span>{t('backends.status.unreachable')}</span>
            </span>
          </Tooltip>
        )
      }

      if (b.health === 'degraded') {
        const tooltipText = [
          b.health_error || 'Degraded response',
          b.health_latency_ms !== null ? `${b.health_latency_ms}ms latency` : null,
          b.health_checked_at ? `Checked ${formatRelativeTime(b.health_checked_at)}` : null,
        ]
          .filter(Boolean)
          .join(' • ')
        return (
          <Tooltip content={tooltipText} placement="top">
            <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400 cursor-default">
              <span className="w-2 h-2 rounded-full bg-amber-500 shrink-0" />
              <span>Degraded</span>
            </span>
          </Tooltip>
        )
      }

      const healthyDetails = [
        b.health === 'healthy' ? 'Healthy' : 'Enabled & ready',
        b.health_latency_ms !== null ? `${b.health_latency_ms}ms latency` : null,
        b.health_checked_at
          ? `Checked ${formatRelativeTime(b.health_checked_at)}`
          : b.secret_keys.length > 0
            ? `${b.secret_keys.length} secret(s) configured`
            : null,
      ]
        .filter(Boolean)
        .join(' • ')

      return (
        <Tooltip content={healthyDetails} placement="top">
          <span className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300 font-medium cursor-default">
            <span className="w-2 h-2 rounded-full bg-emerald-500 shrink-0" />
            <span>{t('backends.status.healthy')}</span>
          </span>
        </Tooltip>
      )
    }

    return (
      <EntityPageLayout
        title={t('backends.title')}
        subtitle={t('backends.subtitle')}
        count={backends.length}
        isGlobal
        primaryAction={
          <div ref={registerMenuRef} className="relative shrink-0">
            <Button
              variant="primary"
              size="md"
              aria-expanded={registerMenuOpen}
              aria-haspopup="menu"
              onClick={() => setRegisterMenuOpen((prev) => !prev)}
              className="flex items-center gap-2 font-semibold shadow-lg shadow-teal-600/20 dark:shadow-teal-500/20 shrink-0 rounded-xl cursor-pointer"
            >
              <Plus size={16} />
              <span>{t('backends.register')}</span>
              <ChevronDown
                size={14}
                className={`ml-0.5 transition-transform duration-150 ${registerMenuOpen ? 'rotate-180' : ''
                  }`}
              />
            </Button>

            {registerMenuOpen && (
              <div
                role="menu"
                className="absolute right-0 mt-2 w-72 rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 shadow-xl dark:shadow-2xl p-1.5 z-50 animate-in fade-in zoom-in-95 duration-150"
              >
                <div className="px-3 py-1.5 text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                  Select Backend Type
                </div>
                <div className="flex flex-col gap-0.5">
                  {BACKEND_TYPE_OPTIONS.map((opt) => (
                    <button
                      key={opt.id}
                      type="button"
                      role="menuitem"
                      onClick={() => openBackendDrawer(null, opt.id)}
                      className="w-full flex items-start gap-3 px-3 py-2.5 rounded-xl text-left hover:bg-zinc-100 dark:hover:bg-zinc-800/80 transition-colors cursor-pointer group"
                    >
                      <div className="mt-0.5 w-8 h-8 rounded-lg bg-zinc-100 dark:bg-zinc-800 group-hover:bg-teal-500/15 text-zinc-600 dark:text-zinc-300 group-hover:text-teal-600 dark:group-hover:text-teal-400 flex items-center justify-center shrink-0 transition-colors">
                        {opt.icon}
                      </div>
                      <div className="flex flex-col min-w-0">
                        <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-teal-600 dark:group-hover:text-teal-400 transition-colors">
                          {opt.label}
                        </span>
                        <span className="text-xs text-zinc-500 dark:text-zinc-400 leading-snug">
                          {opt.subtitle}
                        </span>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        }
        storageKey="gitseed_view_backends"
        showViewModeToggle={false}
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t('backends.searchAllPlaceholder')}
        isEmpty={filteredBackends.length === 0}
        metrics={
          !coreBackendChecks.allReady ? (
            <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-4 py-3 rounded-2xl border border-amber-500/30 bg-amber-500/5 dark:bg-amber-500/10 text-xs">
              <div className="flex items-center gap-2.5 min-w-0">
                <AlertCircle size={15} className="text-amber-600 dark:text-amber-400 shrink-0" />
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                    {t('backends.pipelineIncompleteTitle')}
                  </span>
                  <span className="text-zinc-400 hidden sm:inline">·</span>
                  <span className="text-zinc-600 dark:text-zinc-400">
                    {t('backends.pipelineIncompleteSubtitle')}
                  </span>
                </div>
              </div>
              <div className="flex items-center gap-2 flex-wrap shrink-0">
                {coreBackendChecks.items.map((item) =>
                  item.ready ? (
                    <span
                      key={item.id}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/25 font-semibold"
                    >
                      <Check size={12} className="stroke-[2.5]" />
                      <span>{item.label}</span>
                    </span>
                  ) : (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => openBackendDrawer(null, item.id)}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-white dark:bg-zinc-900 hover:bg-teal-500/10 text-amber-700 dark:text-amber-300 hover:text-teal-700 dark:hover:text-teal-300 border border-amber-500/35 hover:border-teal-500/40 font-semibold transition-colors cursor-pointer shadow-2xs"
                    >
                      <Plus size={12} />
                      <span>Add {item.label}</span>
                    </button>
                  )
                )}
              </div>
            </div>
          ) : undefined
        }
        filterTabs={
          <SegmentedControl
            items={[
              { id: 'all', label: t('backends.tabs.all'), count: backends.length },
              { id: 'git', label: t('backends.types.git'), count: gitBackends.length },
              { id: 'inference', label: t('backends.types.inference'), count: modelBackends.length },
              { id: 'compute', label: t('backends.types.compute'), count: computeBackends.length },
              { id: 'tools', label: t('backends.types.tools'), count: toolBackends.length },
            ]}
            selectedId={selectedFilter}
            onSelect={(id) => setSelectedFilter(id as 'all' | BackendType)}
            ariaLabel={t('backends.filterType')}
          />
        }
        emptyMessage={
          <div className="flex flex-col items-center gap-3">
            <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center">
              <Cpu size={20} />
            </div>
            <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
              {backends.length === 0 ? 'No backends registered yet' : t('backends.noMatch')}
            </h3>
            {backends.length === 0 && (
              <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
                Register a Git host, model provider, compute runner, or MCP tool server to get started.
              </p>
            )}
            {(selectedFilter !== 'all' || searchQuery) && (
              <button
                type="button"
                onClick={() => {
                  setSelectedFilter('all')
                  setSearchQuery('')
                }}
                className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                {t('backends.clearFilter')}
              </button>
            )}
          </div>
        }
        pagination={totalPages > 1 ? paginationProps : undefined}
        renderList={() => (
          <EntityTableCard>
            <EntityTableHeaderRow>
              <SortableTh<BackendSortKey>
                sortKey="name"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="min-w-[240px] max-w-[340px] px-4"
              >
                {t('backends.table.backend')}
              </SortableTh>
              <SortableTh<BackendSortKey>
                sortKey="type"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="w-32 whitespace-nowrap px-3"
              >
                {t('backends.table.type')}
              </SortableTh>
              <SortableTh<BackendSortKey>
                sortKey="used_by"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="w-28 whitespace-nowrap px-3"
              >
                {t('backends.table.usedBy')}
              </SortableTh>
              <SortableTh<BackendSortKey>
                sortKey="capacity"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="w-28 whitespace-nowrap px-3"
              >
                {t('backends.table.capacity')}
              </SortableTh>
              <SortableTh<BackendSortKey>
                sortKey="access"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="w-36 whitespace-nowrap px-3"
              >
                {t('backends.table.access')}
              </SortableTh>
              <SortableTh<BackendSortKey>
                sortKey="status"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="w-36 whitespace-nowrap px-3"
              >
                {t('backends.table.status')}
              </SortableTh>
              <th className="py-3 pr-4 pl-2 text-right font-semibold w-20 whitespace-nowrap">
                <span className="sr-only">{t('backends.table.actions')}</span>
              </th>
            </EntityTableHeaderRow>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800/60">
              {pagedBackends.map((b: Backend, idx: number) => {
                const typeInfo = getTypeInfo(b.type)
                const usedProjects = getProjectsUsingBackend(b, projects, projectReposByProject)
                const capacity = getBackendCapacityUsage(b, runs)
                const access = getBackendAccessSummary(b, agents, t('backends.unrestricted'))
                const isTesting = testingId === b.id
                const cleanEndpoint = formatCleanEndpoint(b)

                const isNewGroup =
                  selectedFilter === 'all' &&
                  sortConfig.key === 'type' &&
                  idx > 0 &&
                  pagedBackends[idx - 1].type !== b.type

                return (
                  <tr
                    key={b.id}
                    onClick={() => openBackendDrawer(b)}
                    title={t('backends.actions.viewDetail') || 'Click to configure backend'}
                    className={`hover:bg-zinc-100/80 dark:hover:bg-zinc-800/40 transition-colors cursor-pointer group ${isNewGroup ? 'border-t-2 border-zinc-200/90 dark:border-zinc-700/80' : ''
                      }`}
                  >
                    {/* 1. Backend: Name with provider icon, clean endpoint on line 2 */}
                    <td className="py-3 px-4 min-w-[240px] max-w-[340px]">
                      <div className="flex flex-col gap-0.5 min-w-0">
                        <div className="flex items-center gap-2 min-w-0">
                          <ProviderIcon provider={b.provider} type={b.type} size={15} />
                          <span
                            className="font-semibold text-zinc-900 dark:text-zinc-100 text-sm truncate hover:text-teal-600 dark:hover:text-teal-400 transition"
                            title={b.name}
                          >
                            {b.name}
                          </span>
                        </div>
                        <span
                          className="text-xs font-mono text-zinc-400 dark:text-zinc-500 truncate pl-[23px]"
                          title={cleanEndpoint}
                        >
                          {cleanEndpoint}
                        </span>
                      </div>
                    </td>

                    {/* 2. Type: Models, Git, Tools, Compute */}
                    <td className="py-3 px-3 whitespace-nowrap">
                      <span
                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-semibold border ${typeInfo.badgeBg}`}
                      >
                        {typeInfo.icon}
                        <span>{typeInfo.label}</span>
                      </span>
                    </td>

                    {/* 3. Used by: e.g. 3 projects with hover list */}
                    <td className="py-3 px-3 whitespace-nowrap">
                      {usedProjects.length > 0 ? (
                        <Tooltip
                          content={usedProjects.map((p) => p.name).join(', ')}
                          placement="top"
                        >
                          <span className="text-xs text-zinc-700 dark:text-zinc-300 font-medium hover:text-teal-600 dark:hover:text-teal-400 cursor-default">
                            {usedProjects.length === 1
                              ? '1 project'
                              : `${usedProjects.length} projects`}
                          </span>
                        </Tooltip>
                      ) : (
                        <span className="text-xs text-zinc-400 dark:text-zinc-600">-</span>
                      )}
                    </td>

                    {/* 4. Capacity: current / max busy for compute and models; - for git and tools */}
                    <td className="py-3 px-3 whitespace-nowrap">
                      {capacity.display === '-' ? (
                        <span className="text-xs text-zinc-400 dark:text-zinc-600">-</span>
                      ) : (
                        <span className="text-xs text-zinc-700 dark:text-zinc-300">
                          <span className="font-mono font-medium">
                            {capacity.current} / {capacity.max}
                          </span>{' '}
                          <span className="text-zinc-500 dark:text-zinc-400">busy</span>
                        </span>
                      )}
                    </td>

                    {/* 5. Access: All agents / @coder only with flagged restriction */}
                    <td className="py-3 px-3 whitespace-nowrap">
                      {access.isRestrictedToOne || access.isRestrictedEmpty ? (
                        <Tooltip content={access.tooltip} placement="top">
                          <span className="inline-flex items-center gap-1 font-mono text-xs font-semibold px-2 py-0.5 rounded-md bg-amber-500/10 text-amber-700 dark:text-amber-300 border border-amber-500/25 cursor-default">
                            <ShieldAlert
                              size={11}
                              className="shrink-0 text-amber-600 dark:text-amber-400"
                            />
                            <span>{access.label}</span>
                          </span>
                        </Tooltip>
                      ) : access.isAll ? (
                        <Tooltip content={access.tooltip} placement="top">
                          <span className="text-xs text-zinc-600 dark:text-zinc-400 font-medium cursor-default">
                            {access.label}
                          </span>
                        </Tooltip>
                      ) : (
                        <Tooltip content={access.tooltip} placement="top">
                          <span className="text-xs text-zinc-700 dark:text-zinc-300 font-medium cursor-default">
                            {access.label}
                          </span>
                        </Tooltip>
                      )}
                    </td>

                    {/* 6. Status */}
                    <td className="py-3 px-3 whitespace-nowrap">{renderStatusCell(b)}</td>

                    {/* 7. Actions & Drawer Chevron */}
                    <td className="py-3 pr-4 pl-2 text-right whitespace-nowrap">
                      <div className="inline-flex items-center justify-end gap-1">
                        <ActionMenu
                          items={[
                            {
                              label: isTesting
                                ? 'Testing...'
                                : t('backends.actions.testConnection'),
                              icon: (
                                <Activity
                                  size={13}
                                  data-test-status={testStatus}
                                  className={isTesting ? 'animate-spin text-teal-500' : ''}
                                />
                              ),
                              onClick: () => {
                                void handleTestConnection(b)
                              },
                            },
                            {
                              label: t('backends.actions.edit'),
                              icon: <Edit2 size={13} />,
                              onClick: () => openBackendDrawer(b),
                            },
                            {
                              label: b.enabled
                                ? t('backends.actions.disable')
                                : t('backends.actions.enable'),
                              icon: <Power size={13} />,
                              onClick: () => {
                                void handleToggleEnabled(b)
                              },
                            },
                            {
                              label: t('backends.actions.delete'),
                              icon: <Trash2 size={13} />,
                              variant: 'danger',
                              onClick: () => {
                                setDeleteError(null)
                                setDeleteConfirm(b)
                              },
                            },
                          ]}
                          ariaLabel={t('backends.table.actions')}
                        />
                        <span className="inline-flex items-center text-zinc-400 group-hover:text-teal-600 dark:group-hover:text-teal-400 group-hover:translate-x-1 transition-all">
                          <ChevronRight size={16} />
                        </span>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </EntityTableCard>
        )}
      >
        <BackendDrawer
          isOpen={drawerOpen}
          onClose={closeBackendDrawer}
          backendToEdit={selectedBackend}
          defaultType={newBackendType}
          onDelete={(b) => {
            setDeleteError(null)
            setDeleteConfirm(b)
          }}
        />

        <ConfirmDialog
          isOpen={Boolean(deleteConfirm)}
          title={t('backends.dialog.deleteTitle', { name: deleteConfirm?.name || '' })}
          message={
            <div className="flex flex-col gap-2">
              <span>{t('backends.dialog.deleteMessage')}</span>
              {deleteError && (
                <div className="p-2.5 rounded-lg bg-rose-500/10 border border-rose-500/25 text-rose-600 dark:text-rose-400 flex items-start gap-2 text-xs">
                  <AlertCircle size={14} className="shrink-0 mt-0.5" />
                  <span>{deleteError}</span>
                </div>
              )}
            </div>
          }
          confirmLabel={t('backends.dialog.deleteConfirm')}
          confirmVariant="danger"
          isLoading={isDeleting}
          onConfirm={() => {
            void handleConfirmDelete()
          }}
          onCancel={() => {
            setDeleteError(null)
            setDeleteConfirm(null)
          }}
        />
      </EntityPageLayout>
    )
  }

