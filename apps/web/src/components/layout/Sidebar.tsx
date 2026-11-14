import React, { useMemo, useState } from 'react'
import {
  Activity,
  BookOpen,
  Bot,
  FolderGit2,
  LayoutDashboard,
  PanelLeftOpen,
  Server,
  Settings,
  Sparkles,
  Sprout,
  SquareTerminal,
  Users,
  Workflow,
} from 'lucide-react'
import { SidebarFooter } from './SidebarFooter'
import { useQuery } from '@tanstack/react-query'
import { api } from '../../api/client'
import { SidebarNavItem, type SidebarNavItemConfig } from './SidebarNavItem'
import { SidebarProjectSwitcher } from './SidebarProjectSwitcher'
import { Tooltip } from '../common/Tooltip'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { SEED_NEEDS_ATTENTION_STATUSES, VIEWS } from '../../constants'
import { getMissingCoreBackends } from '../../utils/backends'

const getCoordinatorShortcut = () => {
  const isMac =
    typeof navigator !== 'undefined' && /Mac|iPod|iPhone|iPad/.test(navigator.platform)
  return isMac ? '⌘K' : 'Ctrl+K'
}

export const Sidebar: React.FC = () => {
  const { user } = useAuth()
  const {
    activeView,
    setActiveView,
    seeds: ideas,
    backends,
    isInitialLoading,
    showGuideInSidebar,
    openSearchModal,
  } = useData()
  const { t } = useTranslation()
  const isAdmin = user?.org_role === 'admin'
  // Count of the current user's running sessions (terminals + chats) for the Sessions badge
  const { data: myRunning = [] } = useQuery({
    queryKey: ['sessions', 'sidebar-mine-running'],
    queryFn: () => api.sessions.list({ scope: 'mine', state: 'running', limit: 100 }),
    enabled: Boolean(user),
    refetchInterval: 30_000,
    staleTime: 15_000,
  })
  const myRunningSessions = myRunning.length

  const [isCollapsed, setIsCollapsed] = useState<boolean>(() => {
    try {
      return localStorage.getItem('gitseed_sidebar_collapsed') === 'true'
    } catch {
      return false
    }
  })

  const [isProjectDropdownOpen, setIsProjectDropdownOpen] = useState(false)

  const toggleCollapse = () => {
    setIsCollapsed((prev) => {
      const next = !prev
      try {
        localStorage.setItem('gitseed_sidebar_collapsed', String(next))
      } catch {
        // Ignore localStorage errors
      }
      return next
    })
  }

  const needsYouCount = useMemo(
    () => ideas.filter((s) => SEED_NEEDS_ATTENTION_STATUSES.has(s.status)).length,
    [ideas]
  )
  const isOverviewCritical = useMemo(
    () => ideas.some((s) => s.status === 'blocked' || s.status === 'failed'),
    [ideas]
  )

  const missingBackendTypes = useMemo(
    () => (isInitialLoading ? [] : getMissingCoreBackends(backends)),
    [backends, isInitialLoading]
  )

  const hasUnhealthyBackend = useMemo(
    () =>
      backends.some(
        (b) => b.enabled && (b.health === 'unreachable' || b.health === 'degraded')
      ),
    [backends]
  )

  const showBackendsDot = !isInitialLoading && (hasUnhealthyBackend || missingBackendTypes.length > 0)
  const backendsBadgeTooltip = hasUnhealthyBackend
    ? t('backends.unhealthyNotice') || 'Backend unreachable or unhealthy'
    : missingBackendTypes.length === 1
      ? `Setup incomplete: connect a ${missingBackendTypes[0]} backend`
      : missingBackendTypes.length === 2
        ? `Setup incomplete: connect ${missingBackendTypes[0]} and ${missingBackendTypes[1]} backends`
        : missingBackendTypes.length > 2
          ? `Setup incomplete: connect ${missingBackendTypes.slice(0, -1).join(', ')}, and ${missingBackendTypes[missingBackendTypes.length - 1]} backends`
          : undefined

  const coreNavItems: SidebarNavItemConfig[] = [
    {
      id: VIEWS.DASHBOARD,
      label: t('nav.dashboard'),
      icon: LayoutDashboard,
      badgeType: needsYouCount > 0 ? 'count' : undefined,
      badgeCount: needsYouCount,
      badgeVariant: isOverviewCritical ? 'red' : 'amber',
      badgeTooltip: t('dashboard.statusLineReviewItems', { count: needsYouCount }),
    },
    { id: VIEWS.PROJECTS, label: t('nav.projects'), icon: FolderGit2 },
    { id: VIEWS.SEEDS, label: t('nav.seeds'), icon: Sprout },
    { id: VIEWS.SESSIONS, label: t('nav.sessions'), icon: SquareTerminal, badgeType: myRunningSessions > 0 ? 'count' : undefined, badgeCount: myRunningSessions, badgeVariant: 'neutral', badgeTooltip: `${myRunningSessions} running session${myRunningSessions === 1 ? '' : 's'} started by you` },
    { id: VIEWS.TELEMETRY, label: t('nav.telemetry'), icon: Activity },
  ]

  const adminNavItems: SidebarNavItemConfig[] = isAdmin
    ? [
        {
          id: VIEWS.BACKENDS,
          label: t('nav.backends'),
          icon: Server,
          badgeType: showBackendsDot ? 'dot' : undefined,
          badgeVariant: 'red',
          badgeTooltip: backendsBadgeTooltip,
        },
        {
          id: VIEWS.AGENTS,
          label: t('nav.agents'),
          icon: Bot,
        },
        {
          id: VIEWS.ROUTES,
          label: t('nav.routes'),
          icon: Workflow,
        },
        { id: VIEWS.USERS, label: t('nav.users'), icon: Users },
        { id: VIEWS.SETTINGS, label: t('nav.settings'), icon: Settings },
      ]
    : []

  const resourceNavItems: SidebarNavItemConfig[] = showGuideInSidebar
    ? [{ id: VIEWS.GUIDE, label: t('nav.guide'), icon: BookOpen }]
    : []

  return (
    <aside
      className={`${
        isCollapsed ? 'w-18' : 'w-64'
      } bg-white/95 dark:bg-[#070e19]/95 backdrop-blur-xl text-zinc-900 dark:text-zinc-100 border-r border-zinc-200/80 dark:border-zinc-700/80 flex flex-col shrink-0 h-screen sticky top-0 select-none transition-[width] duration-200 ease-in-out relative ${
        isProjectDropdownOpen ? 'z-50' : 'z-30'
      }`}
    >
      {/* Brand Header & Persistent Project Scope Switcher */}
      <SidebarProjectSwitcher
        isCollapsed={isCollapsed}
        isOpen={isProjectDropdownOpen}
        onOpenChange={setIsProjectDropdownOpen}
      />

      {/* Top Controls: Search or Ask Command Palette Launcher */}
      {isCollapsed ? (
        <div className="p-2 border-b border-zinc-200/80 dark:border-zinc-700/80 flex flex-col items-center gap-2 shrink-0">
          <Tooltip
            content={t('coordinator.searchOrAskTooltip', {
              shortcut: getCoordinatorShortcut(),
            })}
            placement="right"
          >
            <button
              type="button"
              onClick={openSearchModal}
              aria-label={t('coordinator.searchOrAsk')}
              className="w-10 h-10 flex items-center justify-center rounded-xl text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-300 bg-zinc-100/60 dark:bg-zinc-900/60 hover:bg-teal-500/10 border border-zinc-200/80 dark:border-zinc-800 hover:border-teal-500/30 cursor-pointer transition shadow-2xs group relative"
            >
              <Sparkles size={15} className="text-teal-500 group-hover:scale-110 transition-transform" />
            </button>
          </Tooltip>

          <Tooltip content={t('nav.expandSidebar')} placement="right">
            <button
              type="button"
              onClick={toggleCollapse}
              aria-label={t('nav.expandSidebar')}
              className="w-10 h-10 flex items-center justify-center rounded-xl text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-900 cursor-pointer transition"
            >
              <PanelLeftOpen size={18} />
            </button>
          </Tooltip>
        </div>
      ) : (
        <div className="p-3 border-b border-zinc-200/80 dark:border-zinc-700/80 flex flex-col gap-2 shrink-0">
          <button
            type="button"
            onClick={openSearchModal}
            className="w-full flex items-center justify-between gap-2 px-3 py-2 rounded-xl bg-zinc-50 dark:bg-zinc-900/90 hover:bg-teal-500/5 dark:hover:bg-teal-500/10 border border-zinc-200 dark:border-zinc-700/80 hover:border-teal-500/50 dark:hover:border-teal-500/50 transition-all duration-150 cursor-pointer group text-left shadow-2xs"
            aria-label={t('coordinator.searchOrAskTooltip', {
              shortcut: getCoordinatorShortcut(),
            })}
          >
            <div className="flex items-center gap-2 min-w-0">
              <Sparkles
                size={13}
                className="text-teal-500 group-hover:scale-110 transition-transform shrink-0"
              />
              <span className="text-xs text-zinc-600 dark:text-zinc-300 group-hover:text-zinc-900 dark:group-hover:text-zinc-100 font-medium truncate">
                {t('coordinator.searchOrAsk')}
              </span>
            </div>
            <kbd className="inline-flex items-center px-1.5 py-0.5 text-[10px] font-mono font-semibold rounded border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 group-hover:text-teal-600 dark:group-hover:text-teal-400 group-hover:border-teal-500/40 transition-colors shrink-0 shadow-2xs">
              {getCoordinatorShortcut()}
            </kbd>
          </button>
        </div>
      )}

      {/* Nav Menu */}
      <nav
        className={`flex-1 min-h-0 overflow-y-auto ${
          isCollapsed ? 'px-1.5 py-3' : 'px-3 py-4'
        } flex flex-col gap-1`}
      >
        {!isCollapsed && (
          <div className="pb-1 px-2 pt-0.5 shrink-0">
            <span className="text-[11px] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
              {t('nav.sectionWorkspace')}
            </span>
          </div>
        )}

        {coreNavItems.map((item) => (
          <SidebarNavItem
            key={item.id}
            item={item}
            isActive={activeView === item.id}
            isCollapsed={isCollapsed}
            onSelect={setActiveView}
          />
        ))}

        {adminNavItems.length > 0 && (
          <>
            {isCollapsed ? (
              <div className="my-2 border-t border-zinc-200 dark:border-zinc-800 mx-2 shrink-0" />
            ) : (
              <div className="pt-4 mt-2 border-t border-zinc-200/80 dark:border-zinc-800 pb-1 px-2 shrink-0">
                <span className="text-[11px] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
                  {t('nav.sectionAdmin')}
                </span>
              </div>
            )}

            {adminNavItems.map((item) => (
              <SidebarNavItem
                key={item.id}
                item={item}
                isActive={activeView === item.id}
                isCollapsed={isCollapsed}
                onSelect={setActiveView}
              />
            ))}
          </>
        )}

        {resourceNavItems.length > 0 && (
          <>
            {isCollapsed ? (
              <div className="my-2 border-t border-zinc-200 dark:border-zinc-800 mx-2 shrink-0" />
            ) : (
              <div className="pt-4 mt-2 border-t border-zinc-200/80 dark:border-zinc-800 pb-1 px-2 shrink-0">
                <span className="text-[11px] font-semibold text-zinc-500 dark:text-zinc-400 uppercase tracking-wider">
                  {t('nav.sectionResources')}
                </span>
              </div>
            )}

            {resourceNavItems.map((item) => (
              <SidebarNavItem
                key={item.id}
                item={item}
                isActive={activeView === item.id}
                isCollapsed={isCollapsed}
                onSelect={setActiveView}
              />
            ))}
          </>
        )}
      </nav>

      {/* Footer Controls */}
      <SidebarFooter isCollapsed={isCollapsed} />

      {/* Clickable Collapse/Expand Border Handle */}
      <Tooltip
        content={isCollapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
        placement="right"
        delay={300}
      >
        <div
          role="button"
          tabIndex={0}
          onClick={toggleCollapse}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') {
              e.preventDefault()
              toggleCollapse()
            }
          }}
          aria-label={isCollapsed ? t('nav.expandSidebar') : t('nav.collapseSidebar')}
          className="absolute -right-2 top-0 bottom-0 w-4 z-30 cursor-col-resize group flex items-center justify-center select-none"
        >
          <div className="w-[3px] h-full bg-transparent group-hover:bg-teal-500/50 transition-colors" />
          <div className="absolute w-1.5 h-8 rounded-full bg-zinc-300 dark:bg-zinc-700 opacity-0 group-hover:opacity-100 group-hover:bg-teal-500 transition-all shadow-xs" />
        </div>
      </Tooltip>
    </aside>
  )
}
