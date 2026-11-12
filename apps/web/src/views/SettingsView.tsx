import React, { useEffect, useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { CheckCircle2, RotateCcw, Save, X } from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { PageBreadcrumb } from '../components/common/PageBreadcrumb'
import { OrgScopeBanner } from '../components/layout/EntityPageLayout'
import { AppearanceCanvasCard } from '../components/settings/AppearanceCanvasCard'
import { AutonomyDefaultsCard } from '../components/settings/AutonomyDefaultsCard'
import { AboutGitSeedCard, InstanceOverviewCard } from '../components/settings/InstanceOverviewCard'
import { RunnersConcurrencyCard } from '../components/settings/RunnersConcurrencyCard'
import { SecurityIsolationCard } from '../components/settings/SecurityIsolationCard'
import { SpendControlsCard } from '../components/settings/SpendControlsCard'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { useControlPlaneStatus } from '../context/StatusContext'
import { useTheme, type ThemeMode } from '../context/ThemeContext'
import { VIEWS } from '../constants'
import type { QueueStrategy, SeedAutonomy } from '../types'

interface OrgSettingsFormState {
  defaultAutonomy: SeedAutonomy
  defaultMonthlyBudget: number
  seedBudgetCap: string
  spendCheckpoint: string
  maxConcurrentRunners: number
  maxSandboxSessions: number
  defaultProjectConcurrency: number
  queueStrategy: QueueStrategy
  patLifetimeDays: number
  defaultTheme: ThemeMode
  defaultAmbientBg: boolean
  defaultShowGuide: boolean
  defaultStageSeeds: boolean
}

function formatDecimalInput(val: string | number | null | undefined, fallback = ''): string {
  if (val === null || val === undefined || val === '') return fallback
  const num = Number(val)
  if (Number.isNaN(num)) return fallback
  return String(num)
}

function deriveFormFromOrgSettings(
  orgSettings: {
    default_autonomy: SeedAutonomy
    default_monthly_budget: string
    default_seed_budget_cap: string | null
    default_spend_checkpoint: string | null
    max_concurrent_runners: number
    max_sandbox_sessions?: number
    default_project_concurrency: number
    queue_strategy: QueueStrategy
    pat_lifetime_days: number
    default_theme?: ThemeMode
    default_ambient_bg?: boolean
    default_show_guide?: boolean
    default_stage_seeds?: boolean
  } | null,
  fallbackTheme: ThemeMode
): OrgSettingsFormState {
  if (!orgSettings) {
    return {
      defaultAutonomy: 'supervised',
      defaultMonthlyBudget: 100,
      seedBudgetCap: '15',
      spendCheckpoint: '50',
      maxConcurrentRunners: 2,
      maxSandboxSessions: 2,
      defaultProjectConcurrency: 0,
      queueStrategy: 'fifo',
      patLifetimeDays: 365,
      defaultTheme: fallbackTheme,
      defaultAmbientBg: true,
      defaultShowGuide: true,
      defaultStageSeeds: false,
    }
  }

  return {
    defaultAutonomy: orgSettings.default_autonomy || 'supervised',
    defaultMonthlyBudget: Math.max(0, Number(orgSettings.default_monthly_budget) || 0),
    seedBudgetCap: formatDecimalInput(orgSettings.default_seed_budget_cap, ''),
    spendCheckpoint: formatDecimalInput(orgSettings.default_spend_checkpoint, ''),
    maxConcurrentRunners: Math.max(1, orgSettings.max_concurrent_runners || 2),
    maxSandboxSessions: Math.max(1, orgSettings.max_sandbox_sessions || 2),
    defaultProjectConcurrency: Math.max(0, orgSettings.default_project_concurrency ?? 0),
    queueStrategy: orgSettings.queue_strategy === 'fair_share' ? 'fair_share' : 'fifo',
    patLifetimeDays: Math.min(365, Math.max(30, orgSettings.pat_lifetime_days || 365)),
    defaultTheme:
      orgSettings.default_theme === 'dark' ||
      orgSettings.default_theme === 'light' ||
      orgSettings.default_theme === 'system'
        ? orgSettings.default_theme
        : fallbackTheme,
    defaultAmbientBg:
      typeof orgSettings.default_ambient_bg === 'boolean'
        ? orgSettings.default_ambient_bg
        : true,
    defaultShowGuide:
      typeof orgSettings.default_show_guide === 'boolean'
        ? orgSettings.default_show_guide
        : true,
    defaultStageSeeds:
      typeof orgSettings.default_stage_seeds === 'boolean'
        ? orgSettings.default_stage_seeds
        : false,
  }
}

export const SettingsView: React.FC = () => {
  const queryClient = useQueryClient()
  const { t } = useTranslation()
  const {
    orgSettings,
    setActiveView,
    setShowGuideInSidebar,
  } = useData()
  const { health } = useControlPlaneStatus()
  const {
    userThemeOverride,
    userAmbientOverride,
    setOrgDefaultTheme,
    setOrgDefaultAmbientBg,
    clearThemeOverride,
    clearAmbientOverride,
  } = useTheme()

  const baselineOrg = useMemo(
    () => deriveFormFromOrgSettings(orgSettings, 'system'),
    [orgSettings]
  )

  const [defaultAutonomy, setDefaultAutonomy] = useState<SeedAutonomy>(
    baselineOrg.defaultAutonomy
  )
  const [defaultMonthlyBudget, setDefaultMonthlyBudget] = useState<number>(
    baselineOrg.defaultMonthlyBudget
  )
  const [seedBudgetCap, setSeedBudgetCap] = useState<string>(baselineOrg.seedBudgetCap)
  const [spendCheckpoint, setSpendCheckpoint] = useState<string>(baselineOrg.spendCheckpoint)
  const [maxConcurrentRunners, setMaxConcurrentRunners] = useState<number>(
    baselineOrg.maxConcurrentRunners
  )
  const [maxSandboxSessions, setMaxSandboxSessions] = useState<number>(
    baselineOrg.maxSandboxSessions
  )
  const [defaultProjectConcurrency, setDefaultProjectConcurrency] = useState<number>(
    baselineOrg.defaultProjectConcurrency
  )
  const [queueStrategy, setQueueStrategy] = useState<QueueStrategy>(baselineOrg.queueStrategy)
  const [patLifetimeDays, setPatLifetimeDays] = useState<number>(baselineOrg.patLifetimeDays)
  const [defaultTheme, setDefaultTheme] = useState<ThemeMode>(baselineOrg.defaultTheme)
  const [defaultAmbientBg, setDefaultAmbientBg] = useState<boolean>(
    baselineOrg.defaultAmbientBg
  )
  const [defaultShowGuide, setDefaultShowGuide] = useState<boolean>(
    baselineOrg.defaultShowGuide
  )
  const [defaultStageSeeds, setDefaultStageSeeds] = useState<boolean>(
    baselineOrg.defaultStageSeeds
  )

  const [isSaving, setIsSaving] = useState(false)
  const [isSaved, setIsSaved] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  useEffect(() => {
    setDefaultAutonomy(baselineOrg.defaultAutonomy)
    setDefaultMonthlyBudget(baselineOrg.defaultMonthlyBudget)
    setSeedBudgetCap(baselineOrg.seedBudgetCap)
    setSpendCheckpoint(baselineOrg.spendCheckpoint)
    setMaxConcurrentRunners(baselineOrg.maxConcurrentRunners)
    setMaxSandboxSessions(baselineOrg.maxSandboxSessions)
    setDefaultProjectConcurrency(baselineOrg.defaultProjectConcurrency)
    setQueueStrategy(baselineOrg.queueStrategy)
    setPatLifetimeDays(baselineOrg.patLifetimeDays)
    setDefaultTheme(baselineOrg.defaultTheme)
    setDefaultAmbientBg(baselineOrg.defaultAmbientBg)
    setDefaultShowGuide(baselineOrg.defaultShowGuide)
    setDefaultStageSeeds(baselineOrg.defaultStageSeeds)
  }, [baselineOrg])

  const trimmedCap = seedBudgetCap.trim()
  const trimmedCheckpoint = spendCheckpoint.trim()
  const parsedCap = trimmedCap === '' ? null : Number(trimmedCap)
  const parsedCheckpoint = trimmedCheckpoint === '' ? null : Number(trimmedCheckpoint)

  const isCheckpointMissingWhenUnbounded =
    trimmedCap === '' &&
    (parsedCheckpoint === null || Number.isNaN(parsedCheckpoint) || parsedCheckpoint <= 0)

  const isCapInvalid =
    trimmedCap !== '' && (parsedCap === null || Number.isNaN(parsedCap) || parsedCap <= 0)

  const isCheckpointInvalid =
    trimmedCheckpoint !== '' &&
    (parsedCheckpoint === null || Number.isNaN(parsedCheckpoint) || parsedCheckpoint <= 0)

  const isOrgDirty = useMemo(() => {
    return (
      defaultAutonomy !== baselineOrg.defaultAutonomy ||
      defaultMonthlyBudget !== baselineOrg.defaultMonthlyBudget ||
      trimmedCap !== baselineOrg.seedBudgetCap.trim() ||
      trimmedCheckpoint !== baselineOrg.spendCheckpoint.trim() ||
      maxConcurrentRunners !== baselineOrg.maxConcurrentRunners ||
      maxSandboxSessions !== baselineOrg.maxSandboxSessions ||
      defaultProjectConcurrency !== baselineOrg.defaultProjectConcurrency ||
      queueStrategy !== baselineOrg.queueStrategy ||
      patLifetimeDays !== baselineOrg.patLifetimeDays ||
      defaultTheme !== baselineOrg.defaultTheme ||
      defaultAmbientBg !== baselineOrg.defaultAmbientBg ||
      defaultShowGuide !== baselineOrg.defaultShowGuide ||
      defaultStageSeeds !== baselineOrg.defaultStageSeeds
    )
  }, [
    baselineOrg,
    defaultAutonomy,
    defaultMonthlyBudget,
    trimmedCap,
    trimmedCheckpoint,
    maxConcurrentRunners,
    maxSandboxSessions,
    defaultProjectConcurrency,
    queueStrategy,
    patLifetimeDays,
    defaultTheme,
    defaultAmbientBg,
    defaultShowGuide,
    defaultStageSeeds,
  ])

  const isDirty = isOrgDirty

  const handleSave = async () => {
    if (!isDirty || isCheckpointMissingWhenUnbounded || isCapInvalid || isCheckpointInvalid) {
      return
    }

    setSaveError(null)
    setIsSaving(true)
    try {
      await api.settings.update({
        default_autonomy: defaultAutonomy,
        default_monthly_budget: String(Math.max(0, defaultMonthlyBudget)),
        default_seed_budget_cap: parsedCap !== null ? String(parsedCap) : null,
        default_spend_checkpoint: parsedCheckpoint !== null ? String(parsedCheckpoint) : null,
        max_concurrent_runners: Math.max(1, maxConcurrentRunners),
        max_sandbox_sessions: Math.max(1, maxSandboxSessions),
        default_project_concurrency: Math.max(0, defaultProjectConcurrency),
        queue_strategy: queueStrategy,
        pat_lifetime_days: Math.min(365, Math.max(30, patLifetimeDays)),
        default_theme: defaultTheme,
        default_ambient_bg: defaultAmbientBg,
        default_show_guide: defaultShowGuide,
        default_stage_seeds: defaultStageSeeds,
      })
      setOrgDefaultTheme(defaultTheme)
      setOrgDefaultAmbientBg(defaultAmbientBg)
      if (defaultTheme !== baselineOrg.defaultTheme && userThemeOverride !== null) {
        clearThemeOverride()
      }
      if (defaultAmbientBg !== baselineOrg.defaultAmbientBg && userAmbientOverride !== null) {
        clearAmbientOverride()
      }
      setShowGuideInSidebar(defaultShowGuide)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.settings }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])

      setIsSaved(true)
      window.setTimeout(() => setIsSaved(false), 3000)
    } catch (err) {
      setSaveError(err instanceof Error ? err.message : 'Failed to save organization settings.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDiscard = () => {
    setSaveError(null)
    setDefaultAutonomy(baselineOrg.defaultAutonomy)
    setDefaultMonthlyBudget(baselineOrg.defaultMonthlyBudget)
    setSeedBudgetCap(baselineOrg.seedBudgetCap)
    setSpendCheckpoint(baselineOrg.spendCheckpoint)
    setMaxConcurrentRunners(baselineOrg.maxConcurrentRunners)
    setMaxSandboxSessions(baselineOrg.maxSandboxSessions)
    setDefaultProjectConcurrency(baselineOrg.defaultProjectConcurrency)
    setQueueStrategy(baselineOrg.queueStrategy)
    setPatLifetimeDays(baselineOrg.patLifetimeDays)
    setDefaultTheme(baselineOrg.defaultTheme)
    setOrgDefaultTheme(baselineOrg.defaultTheme)
    setDefaultAmbientBg(baselineOrg.defaultAmbientBg)
    setOrgDefaultAmbientBg(baselineOrg.defaultAmbientBg)
    setDefaultShowGuide(baselineOrg.defaultShowGuide)
    setShowGuideInSidebar(baselineOrg.defaultShowGuide)
    setDefaultStageSeeds(baselineOrg.defaultStageSeeds)
  }

  return (
    <div className="flex flex-col gap-6 pb-16">
      {/* Header bar */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <PageBreadcrumb currentLabel={t('settings.title')} hideProject className="mb-1.5" />

          <h2 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100">
            {t('settings.title')}
          </h2>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            {t('settings.subtitle')}
          </p>
        </div>

        <div className="flex items-center gap-2.5 shrink-0">
          {isDirty && (
            <Button
              variant="ghost"
              size="md"
              onClick={handleDiscard}
              className="flex items-center gap-1.5 font-medium text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 cursor-pointer"
            >
              <RotateCcw size={15} />
              <span>{t('settings.discardChanges')}</span>
            </Button>
          )}

          <Button
            variant="primary"
            size="md"
            onClick={handleSave}
            isDisabled={
              isSaving ||
              (!isDirty && !isSaved) ||
              isCheckpointMissingWhenUnbounded ||
              isCapInvalid ||
              isCheckpointInvalid
            }
            className={`flex items-center gap-2 font-semibold rounded-xl transition-all ${
              (isDirty || isSaved) &&
              !isCheckpointMissingWhenUnbounded &&
              !isCapInvalid &&
              !isCheckpointInvalid
                ? 'shadow-lg shadow-teal-600/20 dark:shadow-teal-500/20 cursor-pointer opacity-100'
                : 'opacity-40 cursor-not-allowed shadow-none'
            }`}
          >
            {isSaved ? <CheckCircle2 size={16} /> : <Save size={16} />}
            <span>
              {isSaving
                ? 'Saving...'
                : isSaved
                  ? t('settings.savedSuccess')
                  : t('settings.saveChanges')}
            </span>
          </Button>
        </div>
      </div>

      {saveError && (
        <div className="px-4 py-3 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs font-medium text-rose-600 dark:text-rose-400 flex items-center justify-between gap-2">
          <span>{saveError}</span>
          <button
            type="button"
            onClick={() => setSaveError(null)}
            className="text-rose-500 hover:text-rose-700 cursor-pointer shrink-0"
          >
            <X size={14} />
          </button>
        </div>
      )}

      <OrgScopeBanner title={t('settings.title')} />

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <AutonomyDefaultsCard
          defaultAutonomy={defaultAutonomy}
          onChangeAutonomy={setDefaultAutonomy}
          defaultStageSeeds={defaultStageSeeds}
          onChangeDefaultStageSeeds={setDefaultStageSeeds}
          onConfigurePolicies={() => setActiveView(VIEWS.ROUTES)}
        />

        <SpendControlsCard
          defaultMonthlyBudget={defaultMonthlyBudget}
          onChangeMonthlyBudget={setDefaultMonthlyBudget}
          seedBudgetCap={seedBudgetCap}
          onChangeSeedBudgetCap={setSeedBudgetCap}
          spendCheckpoint={spendCheckpoint}
          onChangeSpendCheckpoint={setSpendCheckpoint}
          isCheckpointMissingWhenUnbounded={isCheckpointMissingWhenUnbounded}
          enforcement={orgSettings?.enforcement}
          enforcementDetail={orgSettings?.enforcement_detail}
        />

        <RunnersConcurrencyCard
          maxConcurrentRunners={maxConcurrentRunners}
          onChangeMaxConcurrentRunners={setMaxConcurrentRunners}
          maxSandboxSessions={maxSandboxSessions}
          onChangeMaxSandboxSessions={setMaxSandboxSessions}
          defaultProjectConcurrency={defaultProjectConcurrency}
          onChangeDefaultProjectConcurrency={setDefaultProjectConcurrency}
          queueStrategy={queueStrategy}
          onChangeQueueStrategy={setQueueStrategy}
          onManageBackends={() => setActiveView(VIEWS.BACKENDS)}
        />

        <AppearanceCanvasCard
          themeMode={defaultTheme}
          onChangeThemeMode={(next) => {
            setDefaultTheme(next)

          }}
          ambientBg={defaultAmbientBg}
          onChangeAmbientBg={(next) => {
            setDefaultAmbientBg(next)

          }}
          showGuideInSidebar={defaultShowGuide}
          onChangeShowGuideInSidebar={(next) => {
            setDefaultShowGuide(next)

          }}
        />

        <SecurityIsolationCard
          patLifetimeDays={patLifetimeDays}
          onChangePatLifetimeDays={setPatLifetimeDays}
        />

        <InstanceOverviewCard
          version={health.version}
          environment={health.environment}
          database={health.database}
          redis={health.redis}
        />

        <AboutGitSeedCard version={health.version} />
      </div>
    </div>
  )
}
