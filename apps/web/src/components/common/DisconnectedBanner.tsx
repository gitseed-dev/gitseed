import React from 'react'
import { AlertOctagon, RefreshCw } from 'lucide-react'
import { useControlPlaneStatus } from '../../context/StatusContext'
import { useTranslation } from '../../context/I18nContext'

export const DisconnectedBanner: React.FC = () => {
  const { health, isChecking, checkHealth, openStatusModal } = useControlPlaneStatus()
  const { t } = useTranslation()

  if (health.status !== 'offline') {
    return null
  }

  return (
    <div
      role="alert"
      className="w-full bg-rose-500/15 dark:bg-rose-950/80 border-b border-rose-500/30 px-4 py-2.5 flex items-center justify-between gap-3 text-rose-900 dark:text-rose-200 z-50 backdrop-blur-md shrink-0 shadow-xs"
    >
      <div className="flex items-center gap-2.5 min-w-0">
        <span className="relative flex h-2.5 w-2.5 shrink-0">
          <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-rose-400 opacity-75" />
          <span className="relative inline-flex rounded-full h-2.5 w-2.5 bg-rose-500" />
        </span>
        <AlertOctagon size={16} className="text-rose-600 dark:text-rose-400 shrink-0" />
        <span className="text-xs sm:text-sm font-semibold truncate">
          {t('dashboard.disconnectedBannerTitle')}
        </span>
      </div>

      <div className="flex items-center gap-2 shrink-0">
        <button
          type="button"
          onClick={() => checkHealth()}
          disabled={isChecking}
          className="px-2.5 py-1 text-xs font-semibold rounded-lg bg-rose-600 hover:bg-rose-500 text-white shadow-2xs transition flex items-center gap-1.5 cursor-pointer disabled:opacity-60"
        >
          <RefreshCw size={12} className={isChecking ? 'animate-spin' : ''} />
          <span>{isChecking ? t('dashboard.disconnectedRetrying') : t('dashboard.disconnectedRetry')}</span>
        </button>
        <button
          type="button"
          onClick={openStatusModal}
          className="px-2.5 py-1 text-xs font-semibold rounded-lg border border-rose-500/30 hover:bg-rose-500/10 text-rose-800 dark:text-rose-200 transition cursor-pointer"
        >
          <span>{t('dashboard.disconnectedStatus')}</span>
        </button>
      </div>
    </div>
  )
}
