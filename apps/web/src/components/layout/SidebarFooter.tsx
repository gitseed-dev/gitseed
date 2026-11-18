import React from 'react'
import { LogOut, Moon, Sun } from 'lucide-react'
import { Tooltip } from '../common/Tooltip'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useControlPlaneStatus } from '../../context/StatusContext'
import { useTheme } from '../../context/ThemeContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import { formatVersionLabel } from '../../utils/format'

export interface SidebarFooterProps {
  isCollapsed: boolean
}

export const SidebarFooter: React.FC<SidebarFooterProps> = ({ isCollapsed }) => {
  const { user, logout } = useAuth()
  const {
    activeView,
    activeDetailId,
    isProfileDrawerOpen,
    openProfileDrawer,
    navigateToDetail,
  } = useData()
  const { health, isChecking, checkHealth, openStatusModal } = useControlPlaneStatus()
  const { theme, toggleTheme } = useTheme()
  const { t } = useTranslation()

  const userDisplayName = user?.preferred_name || user?.name || user?.email || 'Admin'
  const userRole = user?.org_role || 'admin'
  const userInitial = userDisplayName.charAt(0).toUpperCase()

  const isProfileActive =
    isProfileDrawerOpen ||
    (activeView === VIEWS.USERS && Boolean(activeDetailId && activeDetailId === user?.id))

  const handleViewProfile = () => {
    if (activeView === VIEWS.USERS && user?.id && user.org_role === 'admin') {
      navigateToDetail(VIEWS.USERS, user.id)
    } else {
      openProfileDrawer()
    }
  }

  const versionLabel = formatVersionLabel(health.version)
  const isOnline = health.status === 'online'
  const isOffline = health.status === 'offline'
  const isReconnecting =
    health.status === 'checking' || health.status === 'degraded' || (isOffline && isChecking)

  const statusTooltipContent = isOnline
    ? `${t('common.connected')} · ${versionLabel} (${health.latencyMs ?? 0}ms) · ${t('common.controlPlaneStatus')}`
    : isReconnecting
      ? `Reconnecting… · ${t('common.controlPlaneStatus')}`
      : `${t('common.disconnected')} · ${t('common.controlPlaneStatus')}`

  const statusDotClass = isOnline
    ? 'bg-emerald-500'
    : isReconnecting
      ? 'bg-amber-500 animate-pulse'
      : 'bg-rose-500'

  return (
    <div
      className={`border-t border-zinc-200/80 dark:border-zinc-700/80 ${
        isCollapsed ? 'px-2.5 pt-3 pb-4 gap-3.5' : 'px-3 pt-3 pb-4 gap-3'
      } flex flex-col shrink-0 bg-zinc-50/50 dark:bg-zinc-900/20`}
    >
      {isCollapsed ? (
        <Tooltip content={statusTooltipContent} placement="right">
          <button
            type="button"
            onClick={openStatusModal}
            aria-label={t('common.controlPlaneStatus')}
            className="w-8 h-8 mx-auto flex items-center justify-center rounded-xl bg-transparent hover:bg-zinc-100 dark:hover:bg-zinc-800/60 cursor-pointer transition"
          >
            <span className={`w-2 h-2 rounded-full ${statusDotClass}`} />
          </button>
        </Tooltip>
      ) : isOffline && !isReconnecting ? (
        <div className="w-full flex items-center justify-between px-3 py-1.5 rounded-lg text-xs font-medium text-rose-600 dark:text-rose-400">
          <button
            type="button"
            onClick={openStatusModal}
            className="inline-flex items-center gap-2 min-w-0 hover:underline cursor-pointer"
          >
            <span className={`w-2 h-2 rounded-full shrink-0 ${statusDotClass}`} />
            <span className="truncate">{t('common.disconnected')}</span>
          </button>
          <button
            type="button"
            onClick={() => checkHealth()}
            disabled={isChecking}
            className="shrink-0 font-medium text-rose-600 dark:text-rose-400 hover:text-rose-700 dark:hover:text-rose-300 hover:underline cursor-pointer disabled:opacity-60"
          >
            Retry
          </button>
        </div>
      ) : (
        <Tooltip content={statusTooltipContent} placement="top">
          <button
            type="button"
            onClick={openStatusModal}
            aria-label={t('common.controlPlaneStatus')}
            className={`w-full flex items-center px-3 py-1.5 rounded-lg bg-transparent hover:bg-zinc-100/80 dark:hover:bg-zinc-800/50 transition cursor-pointer ${
              isReconnecting
                ? 'text-xs font-medium text-amber-600 dark:text-amber-400'
                : 'text-xs font-normal text-zinc-500 dark:text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-300'
            }`}
          >
            <div className="flex items-center gap-2 min-w-0">
              <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDotClass}`} />
              <div className="flex items-center gap-1.5 truncate leading-none">
                <span>
                  {isReconnecting ? 'Reconnecting…' : t('common.connected')}
                </span>
                {isOnline && (
                  <span className="font-mono leading-none">
                    · {versionLabel}
                  </span>
                )}
              </div>
            </div>
          </button>
        </Tooltip>
      )}

      {user &&
        (isCollapsed ? (
          <div className="flex flex-col items-center gap-2.5 pt-0.5">
            <Tooltip
              content={`${userDisplayName} (${userRole}) · ${t('topbar.viewProfile')}`}
              placement="right"
            >
              <button
                type="button"
                onClick={handleViewProfile}
                className={`w-8 h-8 rounded-xl bg-teal-600 hover:bg-teal-500 text-white flex items-center justify-center font-bold text-xs cursor-pointer select-none ring-1 transition shadow-2xs ${
                  isProfileActive
                    ? 'ring-2 ring-teal-400 ring-offset-2 ring-offset-white dark:ring-offset-zinc-950 shadow-md shadow-teal-600/30'
                    : 'ring-teal-500/30'
                }`}
                aria-label={`${userDisplayName} - ${t('topbar.viewProfile')}`}
              >
                {userInitial}
              </button>
            </Tooltip>

            <Tooltip
              content={theme === 'dark' ? t('topbar.switchToLight') : t('topbar.switchToDark')}
              placement="right"
            >
              <button
                type="button"
                onClick={toggleTheme}
                aria-label={
                  theme === 'dark' ? t('topbar.switchToLight') : t('topbar.switchToDark')
                }
                className="w-8 h-8 flex items-center justify-center rounded-lg text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 cursor-pointer transition"
              >
                {theme === 'dark' ? (
                  <Sun size={15} aria-hidden="true" />
                ) : (
                  <Moon size={15} aria-hidden="true" />
                )}
              </button>
            </Tooltip>

            <Tooltip content={t('topbar.logout')} placement="right">
              <button
                type="button"
                onClick={logout}
                aria-label={t('topbar.logout')}
                className="w-8 h-8 flex items-center justify-center rounded-lg text-zinc-400 hover:text-rose-500 hover:bg-rose-500/10 cursor-pointer transition"
              >
                <LogOut size={15} />
              </button>
            </Tooltip>
          </div>
        ) : (
          <div className="flex items-center justify-between gap-2 pl-3 pr-1 pt-1 pb-0.5">
            <Tooltip content={t('topbar.viewProfile')} placement="top">
              <button
                type="button"
                onClick={handleViewProfile}
                className={`flex items-center gap-2.5 min-w-0 py-1 pl-1.5 pr-3 -my-1 -ml-1.5 rounded-lg transition cursor-pointer group text-left ${
                  isProfileActive
                    ? 'bg-zinc-100 dark:bg-zinc-800 ring-1 ring-teal-500/30'
                    : 'hover:bg-zinc-100 dark:hover:bg-zinc-800/60'
                }`}
                aria-label={t('topbar.viewProfile')}
              >
                <div
                  className="w-7 h-7 rounded-lg bg-teal-600 group-hover:bg-teal-500 text-white flex items-center justify-center font-bold text-xs shrink-0 select-none transition"
                  aria-hidden="true"
                >
                  {userInitial}
                </div>
                <div className="flex flex-col min-w-0 text-left">
                  <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-teal-600 dark:group-hover:text-teal-400 truncate leading-tight transition">
                    {userDisplayName}
                  </span>
                  <span className="text-xs text-zinc-500 dark:text-zinc-400 truncate capitalize">
                    {userRole}
                  </span>
                </div>
              </button>
            </Tooltip>

            <div className="flex items-center gap-1 shrink-0">
              <Tooltip
                content={theme === 'dark' ? t('topbar.switchToLight') : t('topbar.switchToDark')}
                placement="top"
              >
                <button
                  type="button"
                  onClick={toggleTheme}
                  aria-label={
                    theme === 'dark' ? t('topbar.switchToLight') : t('topbar.switchToDark')
                  }
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 cursor-pointer transition"
                >
                  {theme === 'dark' ? (
                    <Sun size={14} aria-hidden="true" />
                  ) : (
                    <Moon size={14} aria-hidden="true" />
                  )}
                </button>
              </Tooltip>

              <Tooltip content={t('topbar.logout')} placement="top">
                <button
                  type="button"
                  onClick={logout}
                  aria-label={t('topbar.logout')}
                  className="w-7 h-7 flex items-center justify-center rounded-lg text-zinc-400 hover:text-rose-500 hover:bg-rose-500/10 cursor-pointer transition"
                >
                  <LogOut size={14} />
                </button>
              </Tooltip>
            </div>
          </div>
        ))}
    </div>
  )
}

