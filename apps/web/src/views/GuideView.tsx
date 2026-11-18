import React, { useState } from 'react'
import { Button, Card } from '@heroui/react'
import {
  ArrowRight,
  Bot,
  ChevronRight,
  Cpu,
  FolderGit2,
  GitMerge,
  Layers,
  Lightbulb,
  Server,
  ShieldCheck,
  Sliders,
  Workflow,
} from 'lucide-react'
import { GettingStartedCard } from '../components/common/GettingStartedCard'
import { PageBreadcrumb } from '../components/common/PageBreadcrumb'
import { StageBadge } from '../components/common/StageBadge'
import { SEED_STAGES, VIEWS } from '../constants'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { AUTONOMY_DESCRIPTIONS, AUTONOMY_LABELS } from '../utils/seeds'
import type { SeedAutonomy, SeedStage } from '../types'

interface StageGuideItem {
  id: SeedStage
  number: string
  label: string
  stageKey: SeedStage
  icon: React.FC<{ size?: number; className?: string }>
  accentColor: string
  bulletColor: string
  summary: string
  agentRole: string
  humanGate: string
  outputs: string[]
}

export const GuideView: React.FC = () => {
  const { t } = useTranslation()
  const { user } = useAuth()
  const { setActiveView } = useData()

  const isAdmin = user?.org_role === 'admin'
  const [activeStageTab, setActiveStageTab] = useState<SeedStage>('ideate')

  const sdlcStages: StageGuideItem[] = [
    {
      id: 'ideate',
      number: '1',
      label: 'Ideate',
      stageKey: SEED_STAGES.IDEATE,
      icon: Lightbulb,
      accentColor:
        'text-amber-600 dark:text-amber-400 bg-amber-500/10 border border-amber-500/25',
      bulletColor: 'bg-amber-500',
      summary: t('guide.stages.ideateDesc'),
      agentRole: t('guide.stages.ideateAgent'),
      humanGate: t('guide.stages.ideateGate'),
      outputs: [
        t('guide.stages.ideateOut1'),
        t('guide.stages.ideateOut2'),
      ],
    },
    {
      id: 'plan',
      number: '2',
      label: 'Plan',
      stageKey: SEED_STAGES.PLAN,
      icon: Layers,
      accentColor:
        'text-purple-600 dark:text-purple-400 bg-purple-500/10 border border-purple-500/25',
      bulletColor: 'bg-purple-500',
      summary: t('guide.stages.planDesc'),
      agentRole: t('guide.stages.planAgent'),
      humanGate: t('guide.stages.planGate'),
      outputs: [
        t('guide.stages.planOut1'),
        t('guide.stages.planOut2'),
      ],
    },
    {
      id: 'execute',
      number: '3',
      label: 'Execute',
      stageKey: SEED_STAGES.EXECUTE,
      icon: Cpu,
      accentColor:
        'text-sky-600 dark:text-sky-400 bg-sky-500/10 border border-sky-500/25',
      bulletColor: 'bg-sky-500',
      summary: t('guide.stages.executeDesc'),
      agentRole: t('guide.stages.executeAgent'),
      humanGate: t('guide.stages.executeGate'),
      outputs: [
        t('guide.stages.executeOut1'),
        t('guide.stages.executeOut2'),
      ],
    },
    {
      id: 'review',
      number: '4',
      label: 'Review',
      stageKey: SEED_STAGES.REVIEW,
      icon: ShieldCheck,
      accentColor:
        'text-blue-600 dark:text-blue-400 bg-blue-500/10 border border-blue-500/25',
      bulletColor: 'bg-blue-500',
      summary: t('guide.stages.reviewDesc'),
      agentRole: t('guide.stages.reviewAgent'),
      humanGate: t('guide.stages.reviewGate'),
      outputs: [
        t('guide.stages.reviewOut1'),
        t('guide.stages.reviewOut2'),
      ],
    },
    {
      id: 'merge',
      number: '5',
      label: 'Merge',
      stageKey: SEED_STAGES.MERGE,
      icon: GitMerge,
      accentColor:
        'text-emerald-600 dark:text-emerald-400 bg-emerald-500/10 border border-emerald-500/25',
      bulletColor: 'bg-emerald-500',
      summary: t('guide.stages.mergeDesc'),
      agentRole: t('guide.stages.mergeAgent'),
      humanGate: t('guide.stages.mergeGate'),
      outputs: [
        t('guide.stages.mergeOut1'),
        t('guide.stages.mergeOut2'),
      ],
    },
  ]

  // Core mental model cards (project.sandbox_config.blocked_paths for clean-room paths; Stage Policies pol_... for autonomy, approvals, min approver role, and self-approval)
  const mentalModelCards = [
    {
      icon: FolderGit2,
      title: t('guide.model.projectsTitle'),
      subtitle: t('guide.model.projectsSubtitle'),
      desc: t('guide.model.projectsDesc'),
      iconStyle:
        'bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/20',
      targetView: VIEWS.PROJECTS,
      actionLabel: t('nav.projects'),
      adminOnly: false,
    },
    {
      icon: Bot,
      title: t('guide.model.agentsTitle'),
      subtitle: t('guide.model.agentsSubtitle'),
      desc: t('guide.model.agentsDesc'),
      iconStyle:
        'bg-purple-500/10 text-purple-600 dark:text-purple-400 border border-purple-500/20',
      targetView: VIEWS.AGENTS,
      actionLabel: t('nav.agents'),
      adminOnly: true,
    },
    {
      icon: Sliders,
      title: t('guide.model.policiesTitle'),
      subtitle: t('guide.model.policiesSubtitle'),
      desc: t('guide.model.policiesDesc'),
      iconStyle:
        'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20',
      targetView: VIEWS.ROUTES,
      actionLabel: t('nav.routes'),
      adminOnly: true,
    },
    {
      icon: Server,
      title: t('guide.model.backendsTitle'),
      subtitle: t('guide.model.backendsSubtitle'),
      desc: t('guide.model.backendsDesc'),
      iconStyle:
        'bg-sky-500/10 text-sky-600 dark:text-sky-400 border border-sky-500/20',
      targetView: VIEWS.BACKENDS,
      actionLabel: t('nav.backends'),
      adminOnly: true,
    },
  ]

  const autonomyModes: SeedAutonomy[] = ['manual', 'supervised', 'autonomous']
  const currentStage =
    sdlcStages.find((s) => s.id === activeStageTab) || sdlcStages[0]
  const StageIcon = currentStage.icon

  return (
    <div className="flex flex-col gap-6 pb-14">
      {/* Page Header */}
      <div>
        <PageBreadcrumb
          currentLabel={t('nav.guide')}
          hideProject
          className="mb-1.5"
        />
        <div className="max-w-3xl">
          <h1 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100">
            {t('guide.title')}
          </h1>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-1 leading-relaxed">
            {t('guide.subtitle')}
          </p>
        </div>
      </div>

      {/* Interactive Setup Checklist */}
      <GettingStartedCard variant="guide" />

      {/* 1. What is a Seed & The 5-Stage Pipeline */}
      <Card className="p-6 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-5 dark:ring-1 dark:ring-white/[0.04]">
        <div className="pb-3 border-b border-zinc-100 dark:border-zinc-800">
          <h2 className="text-lg font-bold font-heading text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
            <Workflow size={18} className="text-teal-600 dark:text-teal-400" />
            <span>{t('guide.sdlcTitle')}</span>
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
            {t('guide.sdlcSubtitle')}
          </p>
        </div>

        {/* 5-Stage Selector Strip */}
        <div className="grid grid-cols-2 sm:grid-cols-5 gap-2.5">
          {sdlcStages.map((stage) => {
            const isSelected = activeStageTab === stage.id
            const Icon = stage.icon
            return (
              <button
                key={stage.id}
                type="button"
                onClick={() => setActiveStageTab(stage.id)}
                className={`p-3.5 rounded-xl border text-left flex flex-col gap-1.5 transition-all cursor-pointer ${
                  isSelected
                    ? 'border-teal-500 bg-teal-500/10 dark:bg-teal-500/15 shadow-xs'
                    : 'border-zinc-200/80 dark:border-zinc-700/70 bg-zinc-50 dark:bg-zinc-800/40 hover:border-zinc-300 dark:hover:border-zinc-600'
                }`}
              >
                <div className="flex items-center justify-between">
                  <span
                    className={`w-6 h-6 rounded-full text-xs font-bold flex items-center justify-center ${
                      isSelected
                        ? 'bg-teal-500 text-white dark:bg-teal-400 dark:text-zinc-950'
                        : 'bg-zinc-200 dark:bg-zinc-700 text-zinc-700 dark:text-zinc-200'
                    }`}
                  >
                    {stage.number}
                  </span>
                  <Icon
                    size={16}
                    className={
                      isSelected
                        ? 'text-teal-600 dark:text-teal-400'
                        : 'text-zinc-400'
                    }
                  />
                </div>
                <span className="text-base font-bold font-heading mt-0.5 text-zinc-900 dark:text-zinc-100">
                  {stage.label}
                </span>
              </button>
            )
          })}
        </div>

        {/* Selected Stage Detail Panel */}
        <div className="p-5 rounded-xl border border-teal-500/35 dark:border-teal-500/40 bg-gradient-to-br from-teal-500/[0.05] via-emerald-500/[0.02] to-transparent flex flex-col gap-4">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div className="flex items-center gap-3.5">
              <div
                className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${currentStage.accentColor}`}
              >
                <StageIcon size={20} />
              </div>
              <div>
                <div className="flex items-center gap-2">
                  <h3 className="font-heading font-bold text-base text-zinc-900 dark:text-zinc-100">
                    {currentStage.number}. {currentStage.label}
                  </h3>
                  <StageBadge stage={currentStage.stageKey} />
                </div>
                <p className="text-sm text-zinc-600 dark:text-zinc-300 mt-0.5">
                  {currentStage.summary}
                </p>
              </div>
            </div>

            <Button
              variant="secondary"
              size="sm"
              onClick={() =>
                setActiveView(VIEWS.SEEDS, { stage: currentStage.id })
              }
              className="flex items-center gap-1 text-xs font-semibold rounded-xl shrink-0 cursor-pointer"
            >
              <span>
                {t('guide.viewStageSeeds', { stage: currentStage.label })}
              </span>
              <ChevronRight size={14} />
            </Button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-3.5 pt-3 border-t border-zinc-200/70 dark:border-zinc-700/70">
            <div className="p-4 rounded-xl bg-white/80 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70">
              <span className="font-bold uppercase tracking-wider text-xs text-zinc-500 dark:text-zinc-400">
                {t('guide.stageAgentRoleLabel')}
              </span>
              <p className="text-sm text-zinc-700 dark:text-zinc-300 mt-1.5 leading-relaxed">
                {currentStage.agentRole}
              </p>
            </div>

            <div className="p-4 rounded-xl bg-white/80 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70">
              <span className="font-bold uppercase tracking-wider text-xs text-zinc-500 dark:text-zinc-400">
                {t('guide.stageHumanGateLabel')}
              </span>
              <p className="text-sm text-zinc-700 dark:text-zinc-300 mt-1.5 leading-relaxed">
                {currentStage.humanGate}
              </p>
            </div>

            <div className="p-4 rounded-xl bg-white/80 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70">
              <span className="font-bold uppercase tracking-wider text-xs text-zinc-500 dark:text-zinc-400">
                {t('guide.stageOutputsLabel')}
              </span>
              <ul className="mt-1.5 flex flex-col gap-1.5 text-sm text-zinc-700 dark:text-zinc-300">
                {currentStage.outputs.map((out, idx) => (
                  <li key={idx} className="flex items-start gap-2">
                    <span
                      className={`w-1.5 h-1.5 rounded-full mt-2 shrink-0 ${currentStage.bulletColor}`}
                    />
                    <span>{out}</span>
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </div>

        {/* Autonomy Modes Row */}
        <div className="flex flex-col gap-3 pt-1">
          <span className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
            {t('guide.autonomyTitle')}
          </span>
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-3.5">
            {autonomyModes.map((mode) => (
              <div
                key={mode}
                className="p-4 rounded-xl border border-zinc-200/80 dark:border-zinc-700/70 bg-zinc-50 dark:bg-zinc-800/40 flex flex-col gap-1"
              >
                <span className="text-sm font-bold text-zinc-900 dark:text-zinc-100">
                  {AUTONOMY_LABELS[mode]}
                </span>
                <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">
                  {AUTONOMY_DESCRIPTIONS[mode]}
                </p>
              </div>
            ))}
          </div>
        </div>
      </Card>

      {/* 3. How the Pieces Fit Together */}
      <div className="flex flex-col gap-3.5">
        <div>
          <h2 className="text-lg font-bold font-heading text-zinc-900 dark:text-zinc-100">
            {t('guide.modelTitle')}
          </h2>
          <p className="text-sm text-zinc-500 dark:text-zinc-400 mt-0.5">
            {t('guide.modelSubtitle')}
          </p>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {mentalModelCards.map((card) => {
            const Icon = card.icon
            const canNavigate = !card.adminOnly || isAdmin
            return (
              <Card
                key={card.title}
                className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col justify-between gap-4 dark:ring-1 dark:ring-white/[0.04]"
              >
                <div className="flex items-start gap-3.5">
                  <div
                    className={`w-10 h-10 rounded-xl flex items-center justify-center shrink-0 ${card.iconStyle}`}
                  >
                    <Icon size={20} />
                  </div>
                  <div className="flex flex-col gap-1 min-w-0">
                    <div className="flex items-center gap-2 flex-wrap">
                      <h3 className="font-heading font-bold text-base text-zinc-900 dark:text-zinc-100">
                        {card.title}
                      </h3>
                      <span className="text-xs font-mono text-zinc-400 dark:text-zinc-500">
                        · {card.subtitle}
                      </span>
                    </div>
                    <p className="text-sm text-zinc-600 dark:text-zinc-400 leading-relaxed">
                      {card.desc}
                    </p>
                  </div>
                </div>

                {canNavigate && (
                  <div className="flex justify-end pt-2 border-t border-zinc-100 dark:border-zinc-800">
                    <button
                      type="button"
                      onClick={() => setActiveView(card.targetView)}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                    >
                      <span>{card.actionLabel}</span>
                      <ArrowRight size={13} />
                    </button>
                  </div>
                )}
              </Card>
            )
          })}
        </div>
      </div>
    </div>
  )
}
