import React from 'react'
import { AlertTriangle, CheckCircle2, DollarSign, ShieldCheck } from 'lucide-react'
import { Card } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'
import type { BudgetEnforcement } from '../../types'

interface SpendControlsCardProps {
  defaultMonthlyBudget: number
  onChangeMonthlyBudget: (next: number) => void
  seedBudgetCap: string
  onChangeSeedBudgetCap: (next: string) => void
  spendCheckpoint: string
  onChangeSpendCheckpoint: (next: string) => void
  isCheckpointMissingWhenUnbounded: boolean
  enforcement?: BudgetEnforcement
  enforcementDetail?: string | null
}

export const SpendControlsCard: React.FC<SpendControlsCardProps> = ({
  defaultMonthlyBudget,
  onChangeMonthlyBudget,
  seedBudgetCap,
  onChangeSeedBudgetCap,
  spendCheckpoint,
  onChangeSpendCheckpoint,
  isCheckpointMissingWhenUnbounded,
  enforcement = 'enforced',
  enforcementDetail,
}) => {
  const { t } = useTranslation()
  const trimmedCap = seedBudgetCap.trim()
  const resolvedTooltip =
    enforcementDetail ||
    (enforcement === 'unpriced'
      ? 'Custom or unpriced model without a known pricing catalog entry'
      : enforcement === 'estimated'
        ? 'Estimated from CLI token reports in Personal Mode (bounded by timeout and max turns)'
        : 'Enforced in real time via LiteLLM per-run virtual key max_budget')

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex items-center justify-between gap-3 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
            <DollarSign size={20} />
          </div>
          <div>
            <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
              {t('settings.spend.title')}
            </h3>
            <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
              {t('settings.spend.description')}
            </p>
          </div>
        </div>
        <span
          data-testid="settings-spend-enforcement-badge"
          title={resolvedTooltip}
          className={`px-2.5 py-1 rounded-full text-xs font-semibold border ${
            enforcement === 'unpriced'
              ? 'bg-zinc-500/15 text-zinc-700 dark:text-zinc-300 border-zinc-400/30'
              : enforcement === 'estimated'
                ? 'bg-amber-500/15 text-amber-800 dark:text-amber-300 border-amber-500/30'
                : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30'
          }`}
        >
          {enforcement === 'unpriced'
            ? t('settings.spend.enforcementUnpriced')
            : enforcement === 'estimated'
              ? t('settings.spend.enforcementEstimated')
              : t('settings.spend.enforcementEnforced')}
        </span>
      </div>

      <div className="flex flex-col gap-3 pt-2 border-t border-zinc-100 dark:border-zinc-800">
        {/* 1. Default Project Monthly Budget (Org Default) */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <DollarSign size={16} className="text-zinc-400 shrink-0" />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.spend.defaultProjectMonthlyBudget')}
              >
                {t('settings.spend.defaultProjectMonthlyBudget')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.spend.defaultProjectMonthlyBudgetDesc')}
              >
                {t('settings.spend.defaultProjectMonthlyBudgetDesc')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 font-mono text-sm font-semibold text-zinc-800 dark:text-zinc-200 shrink-0">
            <span>$</span>
            <input
              type="number"
              min={0}
              step={5}
              value={defaultMonthlyBudget}
              onChange={(e) => onChangeMonthlyBudget(Math.max(0, Number(e.target.value)))}
              placeholder={t('settings.spend.defaultProjectMonthlyBudgetPlaceholder')}
              className="w-28 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs placeholder-zinc-400"
            />
          </div>
        </div>

        {/* 2. Per-Seed Hard Cap */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <ShieldCheck size={16} className="text-zinc-400 shrink-0" />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.spend.maxBudget')}
              >
                {t('settings.spend.maxBudget')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.spend.maxBudgetDesc')}
              >
                {t('settings.spend.maxBudgetDesc')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 font-mono text-sm font-semibold text-zinc-800 dark:text-zinc-200 shrink-0">
            <span>$</span>
            <input
              type="text"
              placeholder={t('settings.spend.maxBudgetPlaceholder')}
              value={seedBudgetCap}
              onChange={(e) => onChangeSeedBudgetCap(e.target.value)}
              className="w-28 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs placeholder-zinc-400"
            />
          </div>
        </div>

        {/* 3. Spend Checkpoint Soft Gate */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <CheckCircle2 size={16} className="text-amber-500 shrink-0" />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.spend.spendCheckpoint')}
              >
                {t('settings.spend.spendCheckpoint')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.spend.spendCheckpointDesc')}
              >
                {t('settings.spend.spendCheckpointDesc')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-1.5 font-mono text-sm font-semibold text-zinc-800 dark:text-zinc-200 shrink-0">
            <span>$</span>
            <input
              type="number"
              min={1}
              step={5}
              value={spendCheckpoint}
              onChange={(e) => onChangeSpendCheckpoint(e.target.value)}
              placeholder="Optional"
              className="w-28 px-2.5 py-1.5 text-center font-mono text-sm font-semibold rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/40 shadow-2xs placeholder-zinc-400"
            />
          </div>
        </div>

        {/* Validation Rule Alert or Active Protection Reassurance */}
        {isCheckpointMissingWhenUnbounded ? (
          <div className="flex items-start gap-2 p-2.5 rounded-xl bg-rose-50/80 dark:bg-rose-950/30 border border-rose-200 dark:border-rose-900/60 text-rose-700 dark:text-rose-300 text-xs">
            <AlertTriangle size={15} className="text-rose-500 shrink-0 mt-0.5" />
            <span className="leading-snug">{t('settings.spend.checkpointRequiredWarning')}</span>
          </div>
        ) : trimmedCap === '' ? (
          <div className="flex items-center gap-2 p-2.5 rounded-xl bg-emerald-50/70 dark:bg-emerald-950/20 border border-emerald-200/60 dark:border-emerald-800/40 text-emerald-800 dark:text-emerald-300 text-xs">
            <ShieldCheck size={15} className="text-emerald-500 shrink-0" />
            <span>{t('settings.spend.unboundedSafeBadge')}</span>
          </div>
        ) : null}
      </div>
    </Card>
  )
}

