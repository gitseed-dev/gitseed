import React from 'react'
import {
  AlertCircle,
  CheckCircle2,
  Clock,
  Database,
  ExternalLink,
  Layers,
  RefreshCw,
  Server,
  Zap,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { Modal } from '../common/Modal'
import { useControlPlaneStatus } from '../../context/StatusContext'
import { useTranslation } from '../../context/I18nContext'
import { formatVersionLabel } from '../../utils/format'

interface ControlPlaneStatusModalProps {
  isOpen: boolean
  onClose: () => void
}

export const ControlPlaneStatusModal: React.FC<ControlPlaneStatusModalProps> = ({
  isOpen,
  onClose,
}) => {
  const { health, isChecking, checkHealth } = useControlPlaneStatus()
  const { t } = useTranslation()

  const isOnline = health.status === 'online'
  const isDegraded = health.status === 'degraded'
  const isOffline = health.status === 'offline'

  const formattedTime = health.lastChecked
    ? health.lastChecked.toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
        second: '2-digit',
      })
    : t('modals.status.justNow')

  const renderSubsystemStatus = (
    status: string,
    options: {
      connectedLabel?: string
      disabledLabel?: string
      unreachableLabel?: string
    } = {}
  ) => {
    const {
      connectedLabel = t('common.connected'),
      disabledLabel = t('common.disabled'),
      unreachableLabel = t('modals.status.unreachable'),
    } = options

    const s = (status || '').toLowerCase().trim()

    if (isOffline || s.includes('unreachable') || s.includes('error') || s.includes('disconnected')) {
      return (
        <span className="font-mono font-medium text-xs px-2.5 py-0.5 rounded-full bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 inline-flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
          {unreachableLabel}
        </span>
      )
    }

    if (s === 'disabled' || s === 'not configured') {
      return (
        <span className="font-mono font-medium text-xs px-2.5 py-0.5 rounded-full bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border border-zinc-500/20 inline-flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-zinc-400" />
          {disabledLabel}
        </span>
      )
    }

    if (s === 'connected' || s === 'ok' || s === 'ready' || isOnline) {
      return (
        <span className="font-mono font-medium text-xs px-2.5 py-0.5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/20 inline-flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />
          {connectedLabel}
        </span>
      )
    }

    return (
      <span className="font-mono font-medium text-xs px-2.5 py-0.5 rounded-full bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20 inline-flex items-center gap-1.5">
        <span className="w-1.5 h-1.5 rounded-full bg-rose-500" />
        {unreachableLabel}
      </span>
    )
  }

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('modals.status.title')}>
      <div className="flex flex-col gap-5">
        {isOnline && (
          <div className="p-4 rounded-2xl bg-emerald-500/10 border border-emerald-500/30 flex items-start gap-3.5 transition-colors">
            <div className="p-2 rounded-xl bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 shrink-0">
              <CheckCircle2 size={22} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-bold text-sm text-emerald-800 dark:text-emerald-300">
                  {t('modals.status.operational')}
                </span>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono font-semibold bg-emerald-500/20 text-emerald-700 dark:text-emerald-300">
                  HTTP 200 OK
                </span>
              </div>
              <p className="text-xs text-emerald-700/80 dark:text-emerald-400/80 mt-1 leading-relaxed">
                {t('modals.status.operationalDesc')}
              </p>
            </div>
          </div>
        )}

        {isDegraded && (
          <div className="p-4 rounded-2xl bg-amber-500/10 border border-amber-500/30 flex items-start gap-3.5 transition-colors">
            <div className="p-2 rounded-xl bg-amber-500/20 text-amber-600 dark:text-amber-400 shrink-0">
              <AlertCircle size={22} />
            </div>
            <div className="flex-1 min-w-0">
              <span className="font-bold text-sm text-amber-800 dark:text-amber-300">
                {t('modals.status.degraded')}
              </span>
              <p className="text-xs text-amber-700/80 dark:text-amber-400/80 mt-1 leading-relaxed">
                {t('modals.status.degradedDesc')}
              </p>
            </div>
          </div>
        )}

        {isOffline && (
          <div className="p-4 rounded-2xl bg-rose-500/10 border border-rose-500/30 flex items-start gap-3.5 transition-colors">
            <div className="p-2 rounded-xl bg-rose-500/20 text-rose-600 dark:text-rose-400 shrink-0">
              <AlertCircle size={22} />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="font-bold text-sm text-rose-800 dark:text-rose-300">
                  {t('modals.status.offline')}
                </span>
                <span className="inline-flex items-center px-2 py-0.5 rounded-full text-xs font-mono font-semibold bg-rose-500/20 text-rose-700 dark:text-rose-300">
                  UNREACHABLE
                </span>
              </div>
              <p className="text-xs text-rose-700/80 dark:text-rose-400/80 mt-1 leading-relaxed">
                {t('modals.status.offlineDesc')}
              </p>
              {health.error && (
                <div className="mt-2.5 p-2.5 rounded-xl bg-rose-950/20 border border-rose-500/20 font-mono text-xs text-rose-600 dark:text-rose-300 break-all">
                  {health.error}
                </div>
              )}
            </div>
          </div>
        )}

        <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
          <div className="p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider block mb-1">
              {t('modals.status.connection')}
            </span>
            <div className="flex items-center gap-1.5 font-semibold text-xs text-zinc-900 dark:text-zinc-100">
              <span
                className={`w-2 h-2 rounded-full ${
                  isOnline
                    ? 'bg-emerald-500'
                    : isDegraded
                      ? 'bg-amber-500'
                      : 'bg-rose-500'
                }`}
              />
              <span>{isOnline ? t('modals.status.online') : isDegraded ? t('modals.status.degraded') : t('modals.status.offline')}</span>
            </div>
          </div>

          <div className="p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider block mb-1">
              {t('modals.status.latency')}
            </span>
            <div className="flex items-center gap-1 font-semibold text-xs text-zinc-900 dark:text-zinc-100 font-mono">
              <Zap size={13} className="text-amber-500" />
              <span>{health.latencyMs !== null ? `${health.latencyMs} ms` : '-'}</span>
            </div>
          </div>

          <div className="p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider block mb-1">
              {t('modals.status.version')}
            </span>
            <div className="font-semibold text-xs text-zinc-900 dark:text-zinc-100 font-mono">
              {formatVersionLabel(health.version)}
            </div>
          </div>

          <div className="p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80">
            <span className="text-xs font-medium text-zinc-500 uppercase tracking-wider block mb-1">
              {t('modals.status.lastHeartbeat')}
            </span>
            <div className="flex items-center gap-1 font-medium text-xs text-zinc-700 dark:text-zinc-300 font-mono">
              <Clock size={12} className="text-zinc-400" />
              <span className="truncate">{formattedTime}</span>
            </div>
          </div>
        </div>

        <div className="flex flex-col gap-2">
          <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
            {t('modals.status.componentBreakdown')}
          </span>
          <div className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 divide-y divide-zinc-200 dark:divide-zinc-700/80 overflow-hidden text-xs">
            <div className="p-3 flex items-center justify-between bg-white dark:bg-zinc-900/60">
              <div className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300">
                <Server size={14} className="text-zinc-400" />
                <span>{t('modals.status.restEngine')}</span>
              </div>
              {renderSubsystemStatus(isOffline ? 'unreachable' : 'connected')}
            </div>

            <div className="p-3 flex items-center justify-between bg-white dark:bg-zinc-900/60">
              <div className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300">
                <Database size={14} className="text-zinc-400" />
                <span>{t('modals.status.database')}</span>
              </div>
              {renderSubsystemStatus(isOffline ? 'unreachable' : health.database)}
            </div>

            <div className="p-3 flex items-center justify-between bg-white dark:bg-zinc-900/60">
              <div className="flex items-center gap-2 text-zinc-700 dark:text-zinc-300">
                <Layers size={14} className="text-zinc-400" />
                <span>{t('modals.status.sessionStore')}</span>
              </div>
              {renderSubsystemStatus(isOffline ? 'unreachable' : health.redis)}
            </div>
          </div>
        </div>

        <div className="flex items-center justify-between text-xs text-zinc-500 pt-1">
          <span>{t('modals.status.endpointUrl')}:</span>
          <a
            href="/healthz"
            target="_blank"
            rel="noopener noreferrer"
            className="font-mono inline-flex items-center gap-1 hover:text-teal-500 transition-colors"
          >
            <span>/healthz</span>
            <ExternalLink size={12} />
          </a>
        </div>

        <div className="flex items-center justify-end gap-3 pt-3 border-t border-zinc-200 dark:border-zinc-700/80">
          <Button
            variant="secondary"
            size="sm"
            onClick={onClose}
            className="cursor-pointer font-medium"
          >
            {t('modals.status.closeButton')}
          </Button>
          <Button
            variant="primary"
            size="sm"
            onClick={() => checkHealth()}
            isDisabled={isChecking}
            className="cursor-pointer font-semibold flex items-center gap-1.5 shadow-sm shadow-teal-600/20"
          >
            <RefreshCw size={13} className={isChecking ? 'animate-spin' : ''} />
            <span>
              {isChecking ? t('modals.status.checkingButton') : t('modals.status.refreshButton')}
            </span>
          </Button>
        </div>
      </div>
    </Modal>
  )
}
