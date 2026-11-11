import React, { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  FolderGit2,
  Loader2,
  Lock,
  Plus,
  Server,
  Sparkles,
  Sprout,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS, NEW_SEED_HINT } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { getProviderSdlcStageHandles } from '../../utils/agents'
import { getMissingCoreBackends } from '../../utils/backends'
import { BackendDrawer } from '../modals/BackendDrawer'
import { ProviderIcon } from './ProviderIcon'
import { Tooltip } from './Tooltip'
import type { Backend, BackendType } from '../../types'

export interface GettingStartedCardProps {
  variant?: 'overview' | 'guide'
  onNewSeed?: () => void
}

interface QuickVendorOption {
  id: string
  type: BackendType
  provider: string
  label: string
  subtitle: string
  defaultName: string
  handlePrefix?: string
}

const QUICK_INFERENCE_VENDORS: QuickVendorOption[] = [
  {
    id: 'anthropic',
    type: 'inference',
    provider: 'anthropic',
    label: 'Claude Code',
    subtitle: 'API Key or CLI Login',
    defaultName: 'Claude Code',
    handlePrefix: 'claude',
  },
  {
    id: 'antigravity',
    type: 'inference',
    provider: 'antigravity',
    label: 'Antigravity',
    subtitle: 'Google CLI Login or Key',
    defaultName: 'Antigravity',
    handlePrefix: 'agy',
  },
  {
    id: 'openai',
    type: 'inference',
    provider: 'openai',
    label: 'OpenAI Codex',
    subtitle: 'API Key or CLI Login',
    defaultName: 'OpenAI Codex',
    handlePrefix: 'open',
  },
  {
    id: 'gemini',
    type: 'inference',
    provider: 'gemini',
    label: 'Google Gemini',
    subtitle: 'API Key or CLI Login',
    defaultName: 'Google Gemini',
    handlePrefix: 'gemini',
  },
  {
    id: 'xai',
    type: 'inference',
    provider: 'xai',
    label: 'xAI Grok',
    subtitle: 'API Key or CLI Login',
    defaultName: 'xAI Grok',
    handlePrefix: 'grok',
  },
  {
    id: 'ollama',
    type: 'inference',
    provider: 'ollama',
    label: 'Local / OpenCode',
    subtitle: 'Ollama · LM Studio · vLLM',
    defaultName: 'Local Models',
    handlePrefix: 'open',
  },
]

const QUICK_GIT_VENDORS: QuickVendorOption[] = [
  {
    id: 'github-app',
    type: 'git',
    provider: 'github-app',
    label: 'GitHub App',
    subtitle: '1-Click Manifest Setup',
    defaultName: 'GitHub App',
  },
  {
    id: 'github',
    type: 'git',
    provider: 'github',
    label: 'GitHub',
    subtitle: 'Browser OAuth or Token',
    defaultName: 'GitHub',
  },
  {
    id: 'gitlab',
    type: 'git',
    provider: 'gitlab',
    label: 'GitLab',
    subtitle: 'Access Token',
    defaultName: 'GitLab',
  },
  {
    id: 'bitbucket',
    type: 'git',
    provider: 'bitbucket',
    label: 'Bitbucket',
    subtitle: 'Workspace / Repo Token',
    defaultName: 'Bitbucket',
  },
]

export const GettingStartedCard: React.FC<GettingStartedCardProps> = ({
  variant = 'guide',
  onNewSeed,
}) => {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const {
    backends,
    agents,
    policies,
    projects,
    allSeeds,
    isInitialLoading,
    setActiveView,
  } = useData()

  const isAdmin = user?.org_role === 'admin'
  const [stepsManuallyExpanded, setStepsManuallyExpanded] = useState(false)
  const [drawerState, setDrawerState] = useState<{
    isOpen: boolean
    backendToEdit: Backend | null
    defaultType: BackendType
    defaultProvider?: string
    defaultName?: string
  }>({
    isOpen: false,
    backendToEdit: null,
    defaultType: 'inference',
  })
  const [switchingDefaultId, setSwitchingDefaultId] = useState<string | null>(null)

  const enabledInferenceBackends = useMemo(
    () => backends.filter((b) => b.type === 'inference' && b.enabled),
    [backends]
  )
  const enabledGitBackends = useMemo(
    () => backends.filter((b) => b.type === 'git' && b.enabled),
    [backends]
  )
  const enabledComputeBackends = useMemo(
    () => backends.filter((b) => b.type === 'compute' && b.enabled),
    [backends]
  )

  const primaryInferenceBackend = useMemo(() => {
    if (enabledInferenceBackends.length === 0) return null
    return (
      enabledInferenceBackends.find((b) => Boolean(b.config?.is_sdlc_default)) ||
      enabledInferenceBackends[0]
    )
  }, [enabledInferenceBackends])

  const stageHandlesPreview = useMemo(() => {
    if (!primaryInferenceBackend) return []
    const assignedStages = getProviderSdlcStageHandles(primaryInferenceBackend.provider)
    return assignedStages.map((item) => {
      const pol = policies.find((p) => p.stage === item.stage)
      const assignedAgent = pol?.agent_id
        ? agents.find((a) => a.id === pol.agent_id)
        : undefined
      return {
        ...item,
        handle: assignedAgent ? `@${assignedAgent.handle}` : item.handle,
      }
    })
  }, [agents, policies, primaryInferenceBackend])

  if (variant === 'overview' && isInitialLoading) {
    return null
  }

  const missingBackends = getMissingCoreBackends(backends).filter(
    (label) => label !== 'Git'
  )
  const backendsReady =
    enabledComputeBackends.length > 0 && enabledInferenceBackends.length > 0
  const projectsReady = projects.some((p) => !p.archived_at)
  const seedsReady = allSeeds.length > 0

  const completedStepsCount = [
    backendsReady,
    projectsReady,
    seedsReady,
  ].filter(Boolean).length
  const allStepsReady = completedStepsCount === 3

  if (allStepsReady && variant === 'overview') {
    return null
  }

  const openQuickVendorSetup = (vendor: QuickVendorOption) => {
    const existing = backends.find(
      (b) =>
        b.type === vendor.type &&
        (vendor.provider === 'ollama'
          ? ['ollama', 'lmstudio', 'vllm', 'custom'].includes(b.provider.toLowerCase())
          : b.provider.toLowerCase() === vendor.provider.toLowerCase())
    )
    setDrawerState({
      isOpen: true,
      backendToEdit: existing || null,
      defaultType: vendor.type,
      defaultProvider: vendor.provider,
      defaultName: vendor.defaultName,
    })
  }

  const handleSelectSdlcDefault = async (backend: Backend) => {
    if (switchingDefaultId || backend.id === primaryInferenceBackend?.id) return
    setSwitchingDefaultId(backend.id)
    try {
      await api.backends.setSdlcDefault(backend.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
      ])
    } finally {
      setSwitchingDefaultId(null)
    }
  }

  if (!isAdmin) {
    if (allStepsReady) {
      return (
        <div className="px-4 py-3 rounded-2xl border border-emerald-500/30 bg-emerald-500/[0.05] dark:bg-emerald-500/[0.08] dark:ring-1 dark:ring-white/[0.04] flex items-center gap-2 text-sm text-emerald-700 dark:text-emerald-300 font-semibold">
          <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />
          <span>{t('guide.setupCompleteBanner')}</span>
        </div>
      )
    }

    if (!backendsReady) {
      return (
        <div className="px-4 py-3 rounded-2xl border border-amber-500/30 bg-amber-500/[0.05] dark:bg-amber-500/[0.08] dark:ring-1 dark:ring-white/[0.04] flex items-center gap-2.5 text-sm text-zinc-800 dark:text-zinc-200">
          <AlertCircle size={16} className="text-amber-500 shrink-0" />
          <span>{t('guide.memberWaitForAdminBackends')}</span>
        </div>
      )
    }

    if (!projectsReady) {
      return (
        <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-4 dark:ring-1 dark:ring-white/[0.04]">
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
              <FolderGit2 size={18} />
            </div>
            <div>
              <h3 className="text-base font-bold font-heading text-zinc-900 dark:text-zinc-100">
                {t('guide.steps.projectsTitle')}
              </h3>
              <p className="text-sm text-zinc-600 dark:text-zinc-400">
                {t('guide.steps.projectsDesc')}
              </p>
            </div>
          </div>
          <Button
            variant="primary"
            size="sm"
            onClick={() => setActiveView(VIEWS.PROJECTS)}
            className="rounded-xl text-xs font-semibold flex items-center gap-1.5 shrink-0 cursor-pointer"
          >
            <Plus size={14} />
            <span>{t('projects.create')}</span>
          </Button>
        </Card>
      )
    }

    return (
      <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col sm:flex-row sm:items-center justify-between gap-4 dark:ring-1 dark:ring-white/[0.04]">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <Sprout size={18} />
          </div>
          <div>
            <h3 className="text-base font-bold font-heading text-zinc-900 dark:text-zinc-100">
              {t('guide.memberCreateFirstSeedTitle')}
            </h3>
            <p className="text-sm text-zinc-600 dark:text-zinc-400">
              {t('guide.steps.seedsDesc')}
            </p>
          </div>
        </div>
        <Tooltip content={NEW_SEED_HINT} placement="bottom">
          <Button
            variant="primary"
            size="sm"
            onClick={() => {
              if (onNewSeed) {
                onNewSeed()
              } else {
                setActiveView(VIEWS.SEEDS)
              }
            }}
            className="rounded-xl text-xs font-semibold flex items-center gap-1.5 shrink-0 cursor-pointer"
          >
            <Plus size={14} />
            <span>{t('seeds.create')}</span>
          </Button>
        </Tooltip>
      </Card>
    )
  }

  const showFullSteps = !allStepsReady || stepsManuallyExpanded

  if (allStepsReady && !showFullSteps) {
    return (
      <div className="px-4 py-3 rounded-2xl border border-emerald-500/30 bg-emerald-500/[0.05] dark:bg-emerald-500/[0.08] dark:ring-1 dark:ring-white/[0.04] flex items-center justify-between gap-3 text-sm">
        <div className="flex items-center gap-2 text-emerald-700 dark:text-emerald-300 font-semibold">
          <CheckCircle2 size={16} className="text-emerald-500 shrink-0" />
          <span>{t('guide.setupCompleteBanner')}</span>
        </div>
        <button
          type="button"
          onClick={() => setStepsManuallyExpanded(true)}
          className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 transition-colors cursor-pointer"
        >
          <span>{t('guide.showSteps')}</span>
          <ChevronDown size={14} />
        </button>
      </div>
    )
  }

  const steps = [
    {
      step: '1',
      title: t('guide.steps.backendsTitle'),
      desc: 'Connect an AI inference provider and compute runtime. Connect GitHub/GitLab or use On-Device Local Git.',
      ready: backendsReady,
      isCurrent: !backendsReady,
      locked: false,
      lockedBadge: '',
      lockedCtaLabel: '',
      actionLabel: t('guide.steps.backendsAction'),
      onClick: () => setActiveView(VIEWS.BACKENDS),
      icon: Server,
    },
    {
      step: '2',
      title: t('guide.steps.projectsTitle'),
      desc: t('guide.steps.projectsDesc'),
      ready: projectsReady,
      isCurrent: backendsReady && !projectsReady,
      locked: !backendsReady,
      lockedBadge: t('guide.lockedStep1Badge'),
      lockedCtaLabel: t('guide.completeStep1First'),
      actionLabel: t('projects.create'),
      onClick: () => setActiveView(VIEWS.PROJECTS),
      icon: FolderGit2,
    },
    {
      step: '3',
      title: t('guide.steps.seedsTitle'),
      desc: t('guide.steps.seedsDesc'),
      ready: seedsReady,
      isCurrent: backendsReady && projectsReady && !seedsReady,
      locked: !backendsReady || !projectsReady,
      lockedBadge: t('guide.lockedStep2Badge'),
      lockedCtaLabel: t('guide.completeStep2First'),
      actionLabel: onNewSeed ? t('seeds.create') : t('guide.steps.seedsAction'),
      onClick: () => {
        if (onNewSeed) {
          onNewSeed()
        } else {
          setActiveView(VIEWS.SEEDS)
        }
      },
      icon: Sprout,
    },
  ]

  return (
    <>
      <Card className="p-6 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-5 dark:ring-1 dark:ring-white/[0.04]">
        <div className="flex items-center justify-between gap-3 flex-wrap pb-3 border-b border-zinc-100 dark:border-zinc-800">
          <div>
            <h2 className="text-lg font-bold font-heading text-zinc-900 dark:text-zinc-100">
              {t('guide.gettingStartedTitle')}
            </h2>
            <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5">
              {t('guide.gettingStartedSubtitle')}
            </p>
          </div>
          <div className="flex items-center gap-3">
            <span className="text-xs font-mono font-semibold px-2.5 py-1 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200/70 dark:border-zinc-700/70 text-zinc-600 dark:text-zinc-300">
              {completedStepsCount}/3 {t('guide.completedLabel')}
            </span>
            {allStepsReady && (
              <button
                type="button"
                onClick={() => setStepsManuallyExpanded(false)}
                className="text-xs font-semibold text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
              >
                {t('guide.hideSteps')}
              </button>
            )}
          </div>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {steps.map((item) => {
            const Icon = item.icon
            return (
              <div
                key={item.step}
                className={`p-4 rounded-xl border flex flex-col justify-between gap-4 transition-all ${
                  item.ready
                    ? 'border-emerald-500/30 bg-emerald-500/[0.04] dark:bg-emerald-500/[0.07]'
                    : item.isCurrent
                      ? 'border-teal-500/50 dark:border-teal-500/50 bg-white dark:bg-zinc-900/90 ring-1 ring-teal-500/20 shadow-xs'
                      : 'border-dashed border-zinc-200 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/30 opacity-65'
                }`}
              >
                <div className="flex flex-col gap-2">
                  <div className="flex items-center justify-between gap-2">
                    <span
                      className={`inline-flex items-center gap-1.5 text-xs font-bold uppercase tracking-wider ${
                        item.locked
                          ? 'text-zinc-400 dark:text-zinc-500'
                          : 'text-zinc-500 dark:text-zinc-400'
                      }`}
                    >
                      <Icon
                        size={14}
                        className={
                          item.locked
                            ? 'text-zinc-400 dark:text-zinc-500'
                            : 'text-teal-600 dark:text-teal-400'
                        }
                      />
                      <span>
                        {t('guide.stepLabel')} {item.step}
                      </span>
                    </span>

                    {item.ready ? (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25 shrink-0 whitespace-nowrap">
                        <CheckCircle2 size={12} />
                        <span>{t('guide.readyBadge')}</span>
                      </span>
                    ) : item.step === '1' && !item.ready ? (
                      <span
                        title={
                          missingBackends.length > 0
                            ? `Missing: ${missingBackends.join(', ')}`
                            : undefined
                        }
                        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/25 shrink-0 whitespace-nowrap"
                      >
                        <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse" />
                        <span>{t('guide.requiredBadge')}</span>
                      </span>
                    ) : item.isCurrent ? (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/25 shrink-0 whitespace-nowrap">
                        <span className="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse" />
                        <span>{t('guide.nextStepBadge')}</span>
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-medium bg-zinc-200/60 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-300/60 dark:border-zinc-700/70 shrink-0 whitespace-nowrap">
                        <Lock size={11} />
                        <span>{item.lockedBadge}</span>
                      </span>
                    )}
                  </div>

                  <h3
                    className={`text-base font-bold font-heading ${
                      item.locked
                        ? 'text-zinc-600 dark:text-zinc-400'
                        : 'text-zinc-900 dark:text-zinc-100'
                    }`}
                  >
                    {item.title}
                  </h3>
                  <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">
                    {item.desc}
                  </p>
                </div>

                {item.isCurrent ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={item.onClick}
                    className="w-full justify-center rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-sm shadow-teal-600/20 cursor-pointer"
                  >
                    <span>{item.actionLabel}</span>
                    <ArrowRight size={13} />
                  </Button>
                ) : item.locked ? (
                  <button
                    type="button"
                    disabled
                    className="w-full h-8 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/70 dark:bg-zinc-800/50 text-zinc-400 dark:text-zinc-500 text-xs font-medium inline-flex items-center justify-center gap-1.5 cursor-not-allowed select-none"
                  >
                    <Lock size={12} />
                    <span>{item.lockedCtaLabel}</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={item.onClick}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer pt-1"
                  >
                    <span>{item.actionLabel}</span>
                    <ArrowRight size={13} />
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {/* Interactive Step 1 Quick Vendor Onboarding Panel */}
        {(!backendsReady || (!projectsReady && enabledGitBackends.length === 0) || stepsManuallyExpanded) && (
          <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-800 bg-zinc-50/70 dark:bg-zinc-950/50 p-4 sm:p-5 space-y-5">
            {/* 1A: AI / Inference Providers */}
            <div className="space-y-3">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div>
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-bold uppercase tracking-wider text-teal-700 dark:text-teal-300">
                      1A · Connect Inference Providers
                    </span>
                    {enabledInferenceBackends.length > 0 && (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25">
                        <Check size={11} />
                        {enabledInferenceBackends.length} connected
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                    Click a provider below to enter an API key or sign in via CLI OAuth. Connecting your first provider automatically seeds the 5 stage-dedicated SDLC agents.
                  </p>
                </div>
              </div>

              <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-2.5">
                {QUICK_INFERENCE_VENDORS.map((vendor) => {
                  const connectedBackend = enabledInferenceBackends.find((b) =>
                    vendor.provider === 'ollama'
                      ? ['ollama', 'lmstudio', 'vllm', 'custom'].includes(
                          b.provider.toLowerCase()
                        )
                      : b.provider.toLowerCase() === vendor.provider.toLowerCase()
                  )
                  const isPrimary =
                    Boolean(connectedBackend) &&
                    primaryInferenceBackend?.id === connectedBackend?.id

                  return (
                    <button
                      key={vendor.id}
                      type="button"
                      onClick={() => openQuickVendorSetup(vendor)}
                      className={`group text-left p-3 rounded-xl border transition-all flex flex-col justify-between gap-2 cursor-pointer ${
                        connectedBackend
                          ? isPrimary
                            ? 'border-teal-500/60 bg-teal-500/[0.06] dark:bg-teal-500/[0.1] ring-1 ring-teal-500/25'
                            : 'border-emerald-500/40 bg-emerald-500/[0.04] dark:bg-emerald-500/[0.07]'
                          : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-teal-500/50 hover:shadow-xs'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <div className="w-7 h-7 rounded-lg bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
                          <ProviderIcon
                            provider={vendor.provider}
                            type="inference"
                            size={15}
                          />
                        </div>
                        {connectedBackend ? (
                          <span
                            className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold ${
                              isPrimary
                                ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30'
                                : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30'
                            }`}
                          >
                            <Check size={10} />
                            {isPrimary ? 'Primary' : 'Added'}
                          </span>
                        ) : (
                          <span className="text-[10px] font-medium text-zinc-400 group-hover:text-teal-600 dark:group-hover:text-teal-400">
                            + Connect
                          </span>
                        )}
                      </div>
                      <div>
                        <div className="text-xs font-bold text-zinc-900 dark:text-zinc-100 truncate">
                          {vendor.label}
                        </div>
                        <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                          {vendor.subtitle}
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>

              {/* Primary SDLC Pipeline Provider Selector & Stage Agents Preview */}
              {primaryInferenceBackend && (
                <div className="rounded-xl border border-teal-500/25 bg-white/90 dark:bg-zinc-900/90 p-3.5 space-y-2.5">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5">
                    <div className="flex items-center gap-2 flex-wrap">
                      <Sparkles size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                      <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                        Default SDLC Pipeline Provider:
                      </span>
                      {enabledInferenceBackends.length === 1 ? (
                        <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-lg bg-teal-500/10 border border-teal-500/25 text-xs font-semibold text-teal-700 dark:text-teal-300">
                          <ProviderIcon
                            provider={primaryInferenceBackend.provider}
                            type="inference"
                            size={12}
                          />
                          {primaryInferenceBackend.name}
                        </span>
                      ) : (
                        <div className="flex items-center gap-1.5 flex-wrap">
                          {enabledInferenceBackends.map((b) => {
                            const isSelected = b.id === primaryInferenceBackend.id
                            const isSwitching = switchingDefaultId === b.id
                            return (
                              <button
                                key={b.id}
                                type="button"
                                disabled={Boolean(switchingDefaultId)}
                                onClick={() => void handleSelectSdlcDefault(b)}
                                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold border transition cursor-pointer ${
                                  isSelected
                                    ? 'bg-teal-500/15 border-teal-500/40 text-teal-800 dark:text-teal-200 shadow-2xs'
                                    : 'bg-zinc-50 dark:bg-zinc-800/80 border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:border-teal-500/40'
                                }`}
                              >
                                {isSwitching ? (
                                  <Loader2 size={11} className="animate-spin" />
                                ) : (
                                  <ProviderIcon
                                    provider={b.provider}
                                    type="inference"
                                    size={12}
                                  />
                                )}
                                <span>{b.name}</span>
                                {isSelected && <Check size={11} />}
                              </button>
                            )
                          })}
                        </div>
                      )}
                    </div>

                    <div className="flex items-center gap-1.5 flex-wrap text-[11px] font-mono">
                      {stageHandlesPreview.map((st) => (
                        <span
                          key={st.stage}
                          className="px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 border border-zinc-200/80 dark:border-zinc-700/80 text-zinc-700 dark:text-zinc-300"
                          title={`${st.stageLabel} stage default agent`}
                        >
                          <span className="text-zinc-400 dark:text-zinc-500">
                            {st.stageLabel}:
                          </span>{' '}
                          <span className="text-teal-700 dark:text-teal-300 font-semibold">
                            {st.handle}
                          </span>
                        </span>
                      ))}
                    </div>
                  </div>

                  <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                    The 5 stage agents above (plus <code className="font-mono">@coordinator</code>) are wired as your default SDLC pipeline for the selected primary provider. You can customize or mix agents per stage anytime in{' '}
                    <button
                      type="button"
                      onClick={() => setActiveView(VIEWS.ROUTES)}
                      className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                    >
                      Policies
                    </button>{' '}
                    or override them per project.
                  </p>
                </div>
              )}
            </div>

            {/* 1B: Git Providers */}
            <div className="pt-3 border-t border-zinc-200/70 dark:border-zinc-800/80 space-y-2.5">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="text-xs font-bold uppercase tracking-wider text-teal-700 dark:text-teal-300">
                    1B · Connect GitHub/GitLab or Use On-Device Local Git
                  </span>
                  {enabledGitBackends.length > 0 ? (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25">
                      <Check size={11} />
                      {enabledGitBackends.length} connected
                    </span>
                  ) : (
                    <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/25">
                      <Check size={11} />
                      On-Device Local Git built-in
                    </span>
                  )}
                </div>
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Connect GitHub, GitLab, or Bitbucket for cloud pull requests, or skip cloud setup and link a local directory with On-Device Local Git directly in Step 2.
              </p>
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2.5">
                {QUICK_GIT_VENDORS.map((vendor) => {
                  const connectedGit = enabledGitBackends.find(
                    (b) => b.provider.toLowerCase() === vendor.provider.toLowerCase()
                  )
                  return (
                    <button
                      key={vendor.id}
                      type="button"
                      onClick={() => openQuickVendorSetup(vendor)}
                      className={`group text-left p-2.5 rounded-xl border transition-all flex flex-col justify-between gap-1.5 cursor-pointer ${
                        connectedGit
                          ? 'border-emerald-500/40 bg-emerald-500/[0.04] dark:bg-emerald-500/[0.07]'
                          : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-teal-500/50 hover:shadow-xs'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-1.5">
                        <div className="w-6 h-6 rounded-lg bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
                          <ProviderIcon
                            provider={vendor.provider}
                            type="git"
                            size={14}
                          />
                        </div>
                        {connectedGit ? (
                          <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-full text-[10px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
                            <Check size={10} />
                            Ready
                          </span>
                        ) : (
                          <span className="text-[10px] font-medium text-zinc-400 group-hover:text-teal-600 dark:group-hover:text-teal-400">
                            + Connect
                          </span>
                        )}
                      </div>
                      <div>
                        <div className="text-xs font-bold text-zinc-900 dark:text-zinc-100 truncate">
                          {vendor.label}
                        </div>
                        <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                          {vendor.subtitle}
                        </div>
                      </div>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* 1C: Compute Runtime */}
            <div className="pt-3 border-t border-zinc-200/70 dark:border-zinc-800/80 space-y-2.5">
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold uppercase tracking-wider text-teal-700 dark:text-teal-300">
                  1C · Compute Runtime
                </span>
                {enabledComputeBackends.length > 0 && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25">
                    <Check size={11} />
                    Ready
                  </span>
                )}
              </div>
              <div className="p-3 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 flex items-center justify-between gap-3">
                <div className="flex items-center gap-2.5 min-w-0">
                  <div className="w-7 h-7 rounded-lg bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
                    <ProviderIcon
                      provider={enabledComputeBackends[0]?.provider || 'docker'}
                      type="compute"
                      size={15}
                    />
                  </div>
                  <div className="min-w-0">
                    <div className="text-xs font-bold text-zinc-900 dark:text-zinc-100 truncate">
                      {enabledComputeBackends[0]?.name || 'Compute Backend'}
                    </div>
                    <div className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate">
                      {enabledComputeBackends.length > 0
                        ? 'Pre-seeded & active for SDLC runner jobs and sandboxes'
                        : 'Configure Docker Engine or Kubernetes'}
                    </div>
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    setDrawerState({
                      isOpen: true,
                      backendToEdit: enabledComputeBackends[0] || null,
                      defaultType: 'compute',
                    })
                  }
                  className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline shrink-0 cursor-pointer"
                >
                  {enabledComputeBackends.length > 0 ? 'Configure' : '+ Add'}
                </button>
              </div>
            </div>
          </div>
        )}
      </Card>

      <BackendDrawer
        isOpen={drawerState.isOpen}
        onClose={() =>
          setDrawerState((prev) => ({
            ...prev,
            isOpen: false,
          }))
        }
        backendToEdit={drawerState.backendToEdit}
        defaultType={drawerState.defaultType}
        defaultProvider={drawerState.defaultProvider}
        defaultName={drawerState.defaultName}
      />
    </>
  )
}
