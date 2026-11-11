import React from 'react'
import { Cpu, ExternalLink, Info, Layers, Terminal } from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'
import type { QueueStrategy } from '../../types'

interface RunnersConcurrencyCardProps {
  maxConcurrentRunners: number
  onChangeMaxConcurrentRunners: (next: number) => void
  maxSandboxSessions: number
  onChangeMaxSandboxSessions: (next: number) => void
  defaultProjectConcurrency: number
  onChangeDefaultProjectConcurrency: (next: number) => void
  queueStrategy: QueueStrategy
  onChangeQueueStrategy: (next: QueueStrategy) => void
  onManageBackends: () => void
}

export const RunnersConcurrencyCard: React.FC<RunnersConcurrencyCardProps> = ({
  maxConcurrentRunners,
  onChangeMaxConcurrentRunners,
  maxSandboxSessions,
  onChangeMaxSandboxSessions,
  defaultProjectConcurrency,
  onChangeDefaultProjectConcurrency,
  queueStrategy,
  onChangeQueueStrategy,
  onManageBackends,
}) => {
  const { t } = useTranslation()

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-sky-500/10 text-sky-600 dark:text-sky-400 flex items-center justify-center shrink-0">
          <Cpu size={20} />
        </div>
        <div>
          <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
            {t('settings.runners.title')}
          </h3>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
            {t('settings.runners.description')}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 pt-2 border-t border-zinc-100 dark:border-zinc-800">
        {/* 1. Global Max Concurrent Runners */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <Cpu size={16} className="text-zinc-400 shrink-0" />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.runners.globalMaxRunners')}
              >
                {t('settings.runners.globalMaxRunners')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.runners.globalMaxRunnersDesc')}
              >
                {t('settings.runners.globalMaxRunnersDesc')}
              </span>
            </div>
          </div>
          <input
            type="number"
            min={1}
            max={32}
            value={maxConcurrentRunners}
            onChange={(e) => onChangeMaxConcurrentRunners(Math.max(1, Number(e.target.value)))}
            className="w-20 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs shrink-0"
          />
        </div>

        {/* 1b. Max Concurrent Sandbox Sessions */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <Terminal size={16} className="text-zinc-400 shrink-0" />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.runners.maxSandboxes')}
              >
                {t('settings.runners.maxSandboxes')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.runners.maxSandboxesDesc')}
              >
                {t('settings.runners.maxSandboxesDesc')}
              </span>
            </div>
          </div>
          <input
            type="number"
            min={1}
            max={16}
            value={maxSandboxSessions}
            onChange={(e) => onChangeMaxSandboxSessions(Math.max(1, Number(e.target.value)))}
            className="w-20 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs shrink-0"
          />
        </div>

        {/* 2. Default Per-Project Runner Ceiling */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <Layers size={16} className="text-zinc-400 shrink-0" />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.runners.maxRunnersPerProject')}
              >
                {t('settings.runners.maxRunnersPerProject')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.runners.maxRunnersPerProjectDesc')}
              >
                {t('settings.runners.maxRunnersPerProjectDesc')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1 font-mono text-sm font-semibold text-zinc-800 dark:text-zinc-200 shrink-0">
            <input
              type="number"
              min={0}
              max={16}
              value={defaultProjectConcurrency}
              onChange={(e) =>
                onChangeDefaultProjectConcurrency(Math.max(0, Number(e.target.value)))
              }
              className="w-20 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs"
            />
          </div>
        </div>

        {/* 3. Queue Dispatch Policy */}
        <div className="flex flex-col gap-2 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center justify-between gap-2 min-w-0">
            <div className="flex flex-col min-w-0 flex-1">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.runners.queuePolicy')}
              >
                {t('settings.runners.queuePolicy')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.runners.queuePolicyDesc')}
              >
                {t('settings.runners.queuePolicyDesc')}
              </span>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-1.5 pt-1">
            <button
              type="button"
              onClick={() => onChangeQueueStrategy('fifo')}
              className={`py-1.5 px-2 text-xs rounded-lg font-medium border text-center transition-all cursor-pointer ${
                queueStrategy === 'fifo'
                  ? 'bg-teal-500/10 text-teal-700 dark:text-teal-300 border-teal-500/40 shadow-2xs font-semibold'
                  : 'bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600'
              }`}
            >
              {t('settings.runners.policyFifo')}
            </button>
            <button
              type="button"
              onClick={() => onChangeQueueStrategy('fair_share')}
              className={`py-1.5 px-2 text-xs rounded-lg font-medium border text-center transition-all cursor-pointer ${
                queueStrategy === 'fair_share'
                  ? 'bg-teal-500/10 text-teal-700 dark:text-teal-300 border-teal-500/40 shadow-2xs font-semibold'
                  : 'bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700 hover:border-zinc-300 dark:hover:border-zinc-600'
              }`}
            >
              {t('settings.runners.policyFairShare')}
            </button>
          </div>
        </div>

        {/* 4. Per-Backend Concurrency Callout */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 text-xs">
          <div className="flex items-start gap-2 flex-1 min-w-0">
            <Info size={14} className="text-sky-500 shrink-0 mt-0.5" />
            <span className="text-zinc-600 dark:text-zinc-400 text-xs leading-relaxed">
              {t('settings.runners.backendsCallout')}
            </span>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onManageBackends}
            className="h-7 px-2.5 text-xs font-medium text-zinc-700 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-teal-500/10 border border-zinc-200 dark:border-zinc-700 rounded-lg flex items-center gap-1 cursor-pointer shrink-0 self-end sm:self-center"
          >
            <span>{t('settings.runners.manageBackends')}</span>
            <ExternalLink size={12} />
          </Button>
        </div>
      </div>
    </Card>
  )
}

