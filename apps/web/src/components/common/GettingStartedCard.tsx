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
  Sprout,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS, NEW_SEED_HINT } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { getMissingCoreBackends } from '../../utils/backends'
import { BackendDrawer } from '../modals/BackendDrawer'
import { ProjectModal } from '../modals/ProjectModal'
import { ProviderIcon } from './ProviderIcon'
import { Tooltip } from './Tooltip'
import type { Backend, BackendType } from '../../types'

export interface GettingStartedCardProps {
  variant?: 'overview' | 'guide'
  onNewProject?: () => void
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
    id: 'ollama',
    type: 'inference',
    provider: 'ollama',
    label: 'Local / OpenCode',
    subtitle: 'Ollama, vLLM, or LM Studio',
    defaultName: 'Local Inference',
    handlePrefix: 'open',
  },
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
    subtitle: 'API Key or CLI Login',
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
    subtitle: 'API Key (via OpenCode)',
    defaultName: 'xAI Grok',
    handlePrefix: 'grok',
  },
]

const QUICK_GIT_VENDORS: QuickVendorOption[] = [
  {
    id: 'github',
    type: 'git',
    provider: 'github-app',
    label: 'GitHub',
    subtitle: 'GitHub App, OAuth, or Token',
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
  onNewProject,
  onNewSeed,
}) => {
  const { t } = useTranslation()
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const {
    backends,
    projects,
    allSeeds,
    isInitialLoading,
    setActiveView,
  } = useData()

  const isAdmin = user?.org_role === 'admin'
  const [stepsManuallyExpanded, setStepsManuallyExpanded] = useState(false)
  const [modelSectionExpanded, setModelSectionExpanded] = useState(false)
  const [gitSectionExpanded, setGitSectionExpanded] = useState(false)
  const [isProjectModalOpen, setIsProjectModalOpen] = useState(false)
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

  const connectedModelLabel = useMemo(() => {
    if (!primaryInferenceBackend) return ''
    const p = primaryInferenceBackend.provider.toLowerCase()
    const matchedVendor = QUICK_INFERENCE_VENDORS.find((v) =>
      v.provider === 'ollama'
        ? ['ollama', 'lmstudio', 'vllm', 'custom'].includes(p)
        : v.provider.toLowerCase() === p
    )
    const baseLabel = matchedVendor?.label || primaryInferenceBackend.name
    return enabledInferenceBackends.length > 1
      ? `${baseLabel} (+${enabledInferenceBackends.length - 1} more)`
      : baseLabel
  }, [enabledInferenceBackends.length, primaryInferenceBackend])

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
          : vendor.id === 'github'
            ? ['github', 'github-app'].includes(b.provider.toLowerCase())
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
        <>
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
              onClick={() => {
                if (onNewProject) {
                  onNewProject()
                } else {
                  setIsProjectModalOpen(true)
                }
              }}
              className="rounded-xl text-sm font-semibold flex items-center gap-1.5 shrink-0 cursor-pointer"
            >
              <Plus size={14} />
              <span>{t('projects.create')}</span>
            </Button>
          </Card>
          <ProjectModal
            isOpen={isProjectModalOpen}
            onClose={() => setIsProjectModalOpen(false)}
          />
        </>
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
      desc: t('guide.steps.backendsDesc'),
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
      onClick: () => {
        if (onNewProject) {
          onNewProject()
        } else {
          setIsProjectModalOpen(true)
        }
      },
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
                className={`p-4 rounded-xl border flex flex-col justify-between gap-3 transition-all ${
                  item.ready
                    ? 'border-emerald-500/30 bg-emerald-500/[0.04] dark:bg-emerald-500/[0.07]'
                    : item.isCurrent
                      ? 'border-teal-500/50 dark:border-teal-500/50 bg-white dark:bg-zinc-900/90 ring-1 ring-teal-500/20 shadow-xs'
                      : 'border-dashed border-zinc-200 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/30 opacity-65'
                }`}
              >
                <div className="flex flex-col gap-1.5">
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
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25 shrink-0 whitespace-nowrap">
                        <span>{t('guide.readyBadge')} ✓</span>
                      </span>
                    ) : item.step === '1' && !item.ready ? (
                      <span
                        title={
                          missingBackends.length > 0
                            ? `Missing: ${missingBackends.join(', ')}`
                            : undefined
                        }
                        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/25 shrink-0 whitespace-nowrap"
                      >
                        <span className="w-1.5 h-1.5 rounded-full bg-rose-500 animate-pulse" />
                        <span>{t('guide.requiredBadge')}</span>
                      </span>
                    ) : item.isCurrent ? (
                      <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/25 shrink-0 whitespace-nowrap">
                        <span className="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse" />
                        <span>{t('guide.nextStepBadge')}</span>
                      </span>
                    ) : (
                      <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-xs font-medium bg-zinc-200/60 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-300/60 dark:border-zinc-700/70 shrink-0 whitespace-nowrap">
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

                {item.step === '1' ? null : item.isCurrent ? (
                  <Button
                    variant="primary"
                    size="sm"
                    onClick={item.onClick}
                    className="w-full justify-center rounded-xl text-sm font-semibold flex items-center gap-1.5 shadow-sm shadow-teal-600/20 cursor-pointer"
                  >
                    <span>{item.actionLabel}</span>
                    <ArrowRight size={14} />
                  </Button>
                ) : item.locked ? (
                  <button
                    type="button"
                    disabled
                    className="w-full h-9 rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-100/70 dark:bg-zinc-800/50 text-zinc-400 dark:text-zinc-500 text-xs font-medium inline-flex items-center justify-center gap-1.5 cursor-not-allowed select-none"
                  >
                    <Lock size={12} />
                    <span>{item.lockedCtaLabel}</span>
                  </button>
                ) : (
                  <button
                    type="button"
                    onClick={item.onClick}
                    className="inline-flex items-center gap-1 text-sm font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer pt-1"
                  >
                    <span>{item.actionLabel}</span>
                    <ArrowRight size={14} />
                  </button>
                )}
              </div>
            )
          })}
        </div>

        {/* Interactive Step 1 Quick Vendor Onboarding Panel */}
        {(!allStepsReady || stepsManuallyExpanded) && (
          <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-800 bg-zinc-50/70 dark:bg-zinc-950/50 p-4 sm:p-5 space-y-3">
            {/* Step 1 Main Action: Inference Providers (collapses to 1 line once connected) */}
            <div className="space-y-2.5">
              {enabledInferenceBackends.length > 0 ? (
                <div className="flex items-center gap-1.5 flex-wrap text-sm text-zinc-600 dark:text-zinc-300">
                  <Check size={14} className="text-emerald-500 shrink-0" />
                  <span className="font-medium text-zinc-800 dark:text-zinc-200">
                    {connectedModelLabel} connected
                  </span>
                  <span className="text-zinc-400 dark:text-zinc-600">·</span>
                  <span>stage agents ready</span>
                  <span className="text-zinc-400 dark:text-zinc-600">·</span>
                  <button
                    type="button"
                    aria-expanded={modelSectionExpanded}
                    onClick={() => setModelSectionExpanded((prev) => !prev)}
                    className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                  >
                    {modelSectionExpanded ? 'Hide' : 'Connect another'}
                  </button>
                </div>
              ) : (
                <div className="flex items-center gap-1.5 flex-wrap text-sm">
                  <span className="font-bold text-zinc-900 dark:text-zinc-100">
                    Inference providers
                  </span>
                  <span className="text-zinc-500 dark:text-zinc-400">
                    · Local endpoint, API key, or CLI login
                  </span>
                </div>
              )}

              {(enabledInferenceBackends.length === 0 || modelSectionExpanded) && (
                <div className="space-y-3 pt-1">
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
                          className={`group text-left p-3.5 rounded-xl border transition-all flex flex-col justify-between gap-2.5 cursor-pointer ${
                            connectedBackend
                              ? isPrimary
                                ? 'border-teal-500/60 bg-teal-500/[0.06] dark:bg-teal-500/[0.1] ring-1 ring-teal-500/25'
                                : 'border-emerald-500/40 bg-emerald-500/[0.04] dark:bg-emerald-500/[0.07]'
                              : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-teal-500/50 hover:shadow-xs'
                          }`}
                        >
                          <div className="flex items-center justify-between gap-1.5">
                            <div className="w-8 h-8 rounded-lg bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
                              <ProviderIcon
                                provider={vendor.provider}
                                type="inference"
                                size={16}
                              />
                            </div>
                            {connectedBackend ? (
                              <span
                                className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold ${
                                  isPrimary
                                    ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30'
                                    : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30'
                                }`}
                              >
                                <Check size={11} />
                                {isPrimary ? 'Primary' : 'Added'}
                              </span>
                            ) : vendor.id === 'ollama' ? (
                              <span className="inline-flex items-center px-2 py-0.5 rounded-full text-[11px] font-semibold bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/25">
                                No API key needed
                              </span>
                            ) : (
                              <span className="text-xs font-medium text-zinc-400 group-hover:text-teal-600 dark:group-hover:text-teal-400">
                                + Connect
                              </span>
                            )}
                          </div>
                          <div className="space-y-0.5">
                            <div className="text-sm font-bold text-zinc-900 dark:text-zinc-100 truncate">
                              {vendor.label}
                            </div>
                            <div className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                              {vendor.subtitle}
                            </div>
                          </div>
                        </button>
                      )
                    })}
                  </div>

                  {primaryInferenceBackend && enabledInferenceBackends.length > 1 && (
                    <div className="flex items-center justify-between gap-2 flex-wrap pt-1 text-xs">
                      <div className="flex items-center gap-1.5 flex-wrap">
                        <span className="font-semibold text-zinc-600 dark:text-zinc-400">
                          Default pipeline provider:
                        </span>
                        {enabledInferenceBackends.map((b) => {
                          const isSelected = b.id === primaryInferenceBackend.id
                          const isSwitching = switchingDefaultId === b.id
                          return (
                            <button
                              key={b.id}
                              type="button"
                              disabled={Boolean(switchingDefaultId)}
                              onClick={() => void handleSelectSdlcDefault(b)}
                              className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg font-semibold border transition cursor-pointer ${
                                isSelected
                                  ? 'bg-teal-500/15 border-teal-500/40 text-teal-800 dark:text-teal-200'
                                  : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700 text-zinc-600 dark:text-zinc-300 hover:border-teal-500/40'
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
                    </div>
                  )}
                </div>
              )}
            </div>

            {/* 1B: Optional Git Providers (collapsed by default) */}
            <div className="pt-2.5 border-t border-zinc-200/70 dark:border-zinc-800/80 space-y-2.5">
              <div className="flex items-center gap-1.5 flex-wrap text-sm text-zinc-600 dark:text-zinc-300">
                <Check size={14} className="text-emerald-500 shrink-0" />
                <span>
                  <strong className="font-medium text-zinc-800 dark:text-zinc-200">
                    Optional:
                  </strong>{' '}
                  connect GitHub, GitLab, or Bitbucket for pull requests. On-Device Local Git works without it.
                </span>
                <span className="text-zinc-400 dark:text-zinc-600">·</span>
                <button
                  type="button"
                  aria-expanded={gitSectionExpanded}
                  onClick={() => setGitSectionExpanded((prev) => !prev)}
                  className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {gitSectionExpanded ? 'Hide' : 'Connect another'}
                </button>
              </div>

              {gitSectionExpanded && (
                <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5 pt-1">
                  {QUICK_GIT_VENDORS.map((vendor) => {
                    const connectedGit = enabledGitBackends.find((b) =>
                      vendor.id === 'github'
                        ? ['github', 'github-app'].includes(b.provider.toLowerCase())
                        : b.provider.toLowerCase() === vendor.provider.toLowerCase()
                    )
                    return (
                      <button
                        key={vendor.id}
                        type="button"
                        onClick={() => openQuickVendorSetup(vendor)}
                        className={`group text-left p-3 rounded-xl border transition-all flex flex-col justify-between gap-2 cursor-pointer ${
                          connectedGit
                            ? 'border-emerald-500/40 bg-emerald-500/[0.04] dark:bg-emerald-500/[0.07]'
                            : 'border-zinc-200 dark:border-zinc-800 bg-white dark:bg-zinc-900 hover:border-teal-500/50 hover:shadow-xs'
                        }`}
                      >
                        <div className="flex items-center justify-between gap-1.5">
                          <div className="w-7 h-7 rounded-lg bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center shrink-0">
                            <ProviderIcon
                              provider={vendor.provider}
                              type="git"
                              size={15}
                            />
                          </div>
                          {connectedGit ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30">
                              <Check size={11} />
                              Ready
                            </span>
                          ) : (
                            <span className="text-xs font-medium text-zinc-400 group-hover:text-teal-600 dark:group-hover:text-teal-400">
                              + Connect
                            </span>
                          )}
                        </div>
                        <div className="space-y-0.5">
                          <div className="text-sm font-bold text-zinc-900 dark:text-zinc-100 truncate">
                            {vendor.label}
                          </div>
                          <div className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                            {vendor.subtitle}
                          </div>
                        </div>
                      </button>
                    )
                  })}
                </div>
              )}
            </div>

            {/* 1C: Compute Runtime (single compact line when ready) */}
            <div className="pt-2.5 border-t border-zinc-200/70 dark:border-zinc-800/80 flex items-center justify-between gap-3 text-sm">
              {enabledComputeBackends.length > 0 ? (
                <div className="flex items-center gap-1.5 flex-wrap text-zinc-600 dark:text-zinc-300">
                  <Check size={14} className="text-emerald-500 shrink-0" />
                  <span className="font-medium text-zinc-800 dark:text-zinc-200">
                    {enabledComputeBackends[0].name} ready
                  </span>
                  <span className="text-zinc-400 dark:text-zinc-600">·</span>
                  <button
                    type="button"
                    onClick={() =>
                      setDrawerState({
                        isOpen: true,
                        backendToEdit: enabledComputeBackends[0] || null,
                        defaultType: 'compute',
                      })
                    }
                    className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                  >
                    Configure
                  </button>
                </div>
              ) : (
                <div className="flex items-center justify-between w-full gap-3">
                  <span className="text-rose-600 dark:text-rose-400 font-medium">
                    No compute runtime connected (Docker or Kubernetes required)
                  </span>
                  <button
                    type="button"
                    onClick={() =>
                      setDrawerState({
                        isOpen: true,
                        backendToEdit: null,
                        defaultType: 'compute',
                      })
                    }
                    className="font-semibold text-teal-600 dark:text-teal-400 hover:underline shrink-0 cursor-pointer"
                  >
                    + Add compute runtime
                  </button>
                </div>
              )}
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

      <ProjectModal
        isOpen={isProjectModalOpen}
        onClose={() => setIsProjectModalOpen(false)}
      />
    </>
  )
}
