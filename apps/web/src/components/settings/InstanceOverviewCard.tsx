import React from 'react'
import { ExternalLink, Globe, HardDrive, Server, Sprout } from 'lucide-react'
import { Card } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'
import { formatVersionLabel } from '../../utils/format'

interface InstanceOverviewCardProps {
  version?: string
  environment?: string
  database?: string
  redis?: string
}

export const InstanceOverviewCard: React.FC<InstanceOverviewCardProps> = ({
  version,
  environment,
  database,
  redis,
}) => {
  const { t } = useTranslation()

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-indigo-500/10 text-indigo-600 dark:text-indigo-400 flex items-center justify-center shrink-0">
          <Server size={20} />
        </div>
        <div>
          <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
            {t('settings.instance.title')}
          </h3>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
            {t('settings.instance.description')}
          </p>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-2 border-t border-zinc-100 dark:border-zinc-800">
        <div className="flex flex-col gap-1 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {t('settings.instance.versionLabel')}
          </span>
          <span className="font-mono text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {formatVersionLabel(version)}
          </span>
        </div>

        <div className="flex flex-col gap-1 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {t('settings.instance.environmentLabel')}
          </span>
          <span className="text-sm font-semibold text-emerald-600 dark:text-emerald-400 flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-emerald-500" />
            {environment || 'development'}
          </span>
        </div>

        <div className="flex flex-col gap-1 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {t('settings.instance.databaseLabel')}
          </span>
          <span className="font-mono text-sm text-zinc-800 dark:text-zinc-200 flex items-center gap-1.5">
            <HardDrive size={15} className="text-zinc-400 shrink-0" />
            PostgreSQL 16 ({database})
          </span>
        </div>

        <div className="flex flex-col gap-1 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {t('modals.status.sessionStore')}
          </span>
          <span className="font-mono text-sm text-zinc-800 dark:text-zinc-200">
            Valkey ({redis})
          </span>
        </div>

        <div className="col-span-1 sm:col-span-2 flex flex-col gap-1 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            {t('settings.instance.controlPlaneHost')}
          </span>
          <span className="font-mono text-sm text-zinc-800 dark:text-zinc-200 flex items-center gap-1.5">
            <Globe size={15} className="text-zinc-400 shrink-0" />
            {typeof window !== 'undefined' ? window.location.origin : 'http://localhost:8421'}
          </span>
        </div>
      </div>
    </Card>
  )
}

interface AboutGitSeedCardProps {
  version?: string
}

export const AboutGitSeedCard: React.FC<AboutGitSeedCardProps> = ({ version }) => {
  const versionLabel = formatVersionLabel(version)

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-start gap-3">
          <div className="w-10 h-10 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <Sprout size={20} />
          </div>
          <div className="flex flex-col gap-0.5">
            <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
              About GitSeed
            </h3>
            <p className="text-xs sm:text-sm font-mono text-zinc-500 dark:text-zinc-400">
              Version {versionLabel} · Open Source
            </p>
            <p className="text-xs text-zinc-600 dark:text-zinc-300 mt-0.5">
              Self-Hosted AI Software Engineering Control Plane
            </p>
            <p className="text-[11px] text-zinc-400 dark:text-zinc-500">
              Copyright 2026 Trent Seed · Apache-2.0 License
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-2">
          <a
            href="https://github.com/gitseed-dev/gitseed"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-zinc-700 dark:text-zinc-200 bg-zinc-100/80 dark:bg-zinc-800/70 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/70 border border-zinc-200/70 dark:border-zinc-700/70 transition-colors"
          >
            GitHub
            <ExternalLink size={12} className="text-zinc-400" />
          </a>
          <a
            href="https://github.com/gitseed-dev/gitseed#readme"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-zinc-700 dark:text-zinc-200 bg-zinc-100/80 dark:bg-zinc-800/70 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/70 border border-zinc-200/70 dark:border-zinc-700/70 transition-colors"
          >
            Documentation
            <ExternalLink size={12} className="text-zinc-400" />
          </a>
          <a
            href="https://github.com/gitseed-dev/gitseed/releases"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg text-xs font-medium text-zinc-700 dark:text-zinc-200 bg-zinc-100/80 dark:bg-zinc-800/70 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/70 border border-zinc-200/70 dark:border-zinc-700/70 transition-colors"
          >
            Release Notes
            <ExternalLink size={12} className="text-zinc-400" />
          </a>
        </div>
      </div>
    </Card>
  )
}

