import React from 'react'
import { Bot, ExternalLink, GitBranch, Sliders, Sparkles, User } from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'
import { SteppedSlider } from '../common/SteppedSlider'
import type { SeedAutonomy } from '../../types'

interface AutonomyDefaultsCardProps {
  defaultAutonomy: SeedAutonomy
  onChangeAutonomy: (next: SeedAutonomy) => void
  defaultStageSeeds?: boolean
  onChangeDefaultStageSeeds?: (next: boolean) => void
  onConfigurePolicies: () => void
}

export const AutonomyDefaultsCard: React.FC<AutonomyDefaultsCardProps> = ({
  defaultAutonomy,
  onChangeAutonomy,
  defaultStageSeeds = false,
  onChangeDefaultStageSeeds,
  onConfigurePolicies,
}) => {
  const { t } = useTranslation()

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-sky-500/10 text-sky-600 dark:text-sky-400 flex items-center justify-center shrink-0">
          <Sliders size={20} />
        </div>
        <div>
          <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
            {t('settings.defaults.title')}
          </h3>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
            {t('settings.defaults.description')}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 pt-2 border-t border-zinc-100 dark:border-zinc-800">
        <SteppedSlider<SeedAutonomy>
          title={t('settings.defaults.autonomy')}
          description={t('settings.defaults.autonomyDesc')}
          value={defaultAutonomy}
          onChange={onChangeAutonomy}
          trackGradientClass="bg-gradient-to-r from-zinc-400 via-indigo-500 to-emerald-500"
          steps={[
            {
              value: 'manual',
              label: t('settings.defaults.autonomyManual'),
              icon: <User size={12} />,
              headerIcon: <User size={16} className="text-zinc-600 dark:text-zinc-400 shrink-0" />,
              chipColor: 'default',
              thumbActiveClass:
                'bg-zinc-900 dark:bg-zinc-100 border-white dark:border-zinc-900 shadow-md ring-4 ring-zinc-500/20 scale-110',
              dotActiveClass: 'bg-white dark:bg-zinc-900',
              labelActiveClass: 'font-bold text-zinc-900 dark:text-zinc-100',
            },
            {
              value: 'supervised',
              label: t('settings.defaults.autonomySupervised'),
              icon: <Bot size={12} />,
              headerIcon: <Bot size={16} className="text-indigo-500 shrink-0" />,
              chipColor: 'accent',
              thumbActiveClass:
                'bg-indigo-600 border-white dark:border-zinc-900 shadow-md ring-4 ring-indigo-500/25 scale-110',
              thumbPassedClass: 'bg-indigo-500 border-white dark:border-zinc-900 shadow-xs',
              dotActiveClass: 'bg-white',
              dotPassedClass: 'bg-white/80',
              labelActiveClass: 'font-bold text-indigo-600 dark:text-indigo-400',
            },
            {
              value: 'autonomous',
              label: t('settings.defaults.autonomyFull'),
              icon: <Sparkles size={12} />,
              headerIcon: <Sparkles size={16} className="text-emerald-500 shrink-0" />,
              chipColor: 'success',
              thumbActiveClass:
                'bg-emerald-600 border-white dark:border-zinc-900 shadow-md ring-4 ring-emerald-500/25 scale-110',
              dotActiveClass: 'bg-white',
              labelActiveClass: 'font-bold text-emerald-600 dark:text-emerald-400',
            },
          ]}
        />

        {/* Stage New Seeds by Default Toggle */}
        {onChangeDefaultStageSeeds && (
          <label className="flex items-start justify-between gap-3 p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 text-xs cursor-pointer select-none">
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                Stage New Seeds by Default
              </span>
              <span className="text-zinc-500 dark:text-zinc-400 leading-relaxed">
                When enabled, newly created seeds start in Staged status and wait until a person clicks Start Work before agents pick them up.
              </span>
            </div>
            <input
              type="checkbox"
              checked={defaultStageSeeds}
              onChange={(e) => onChangeDefaultStageSeeds(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer shrink-0"
            />
          </label>
        )}

        {/* Per-Stage Retries & Timeouts Callout linking to Policies */}
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 text-xs">
          <div className="flex items-start gap-2.5 flex-1 min-w-0">
            <GitBranch
              size={15}
              className="text-teal-600 dark:text-teal-400 shrink-0 mt-0.5"
            />
            <div className="flex flex-col gap-0.5 min-w-0">
              <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                Stage Retries, Timeouts & Approval Gates
              </span>
              <span className="text-zinc-500 dark:text-zinc-400 leading-relaxed">
                Per-stage retry caps, wall-clock timeouts, and human approval gates are
                configured per SDLC stage in Policies.
              </span>
            </div>
          </div>
          <Button
            variant="outline"
            size="sm"
            onClick={onConfigurePolicies}
            className="h-7 px-2.5 text-xs font-medium text-zinc-700 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-teal-500/10 border border-zinc-200 dark:border-zinc-700 rounded-lg flex items-center gap-1 cursor-pointer shrink-0 self-end sm:self-center"
          >
            <span>{t('routes.actions.configure')}</span>
            <ExternalLink size={12} />
          </Button>
        </div>
      </div>
    </Card>
  )
}
