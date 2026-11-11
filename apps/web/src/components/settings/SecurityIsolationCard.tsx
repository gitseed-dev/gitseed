import React from 'react'
import { Shield } from 'lucide-react'
import { Card, Chip } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'

interface SecurityIsolationCardProps {
  patLifetimeDays: number
  onChangePatLifetimeDays: (next: number) => void
}

export const SecurityIsolationCard: React.FC<SecurityIsolationCardProps> = ({
  patLifetimeDays,
  onChangePatLifetimeDays,
}) => {
  const { t } = useTranslation()

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
          <Shield size={20} />
        </div>
        <div>
          <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
            {t('settings.security.title')}
          </h3>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
            {t('settings.security.description')}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 pt-2 border-t border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex flex-col min-w-0 flex-1">
            <span
              className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
              title={t('settings.security.patLifetime')}
            >
              {t('settings.security.patLifetime')}
            </span>
            <span
              className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
              title={t('settings.security.patLifetimeDesc')}
            >
              {t('settings.security.patLifetimeDesc')}
            </span>
          </div>
          <input
            type="number"
            min={30}
            max={365}
            value={patLifetimeDays}
            onChange={(e) =>
              onChangePatLifetimeDays(Math.min(365, Math.max(30, Number(e.target.value) || 30)))
            }
            className="w-20 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs shrink-0"
          />
        </div>

        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex flex-col min-w-0 flex-1">
            <span
              className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
              title={t('settings.security.runnerSandbox')}
            >
              {t('settings.security.runnerSandbox')}
            </span>
            <span
              className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
              title={t('settings.security.runnerSandboxDesc')}
            >
              {t('settings.security.runnerSandboxDesc')}
            </span>
          </div>
          <Chip
            size="sm"
            variant="soft"
            color="success"
            className="text-xs font-semibold shrink-0"
          >
            Isolated
          </Chip>
        </div>
      </div>
    </Card>
  )
}

