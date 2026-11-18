import React, { useMemo } from 'react'
import {
  Code2,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Plus,
  Settings,
  Star,
} from 'lucide-react'
import { Card } from '@heroui/react'
import { AgentsWorkingCard } from '../../common/AgentsWorkingCard'
import { OperatorReviewPanel } from '../../common/OperatorReviewPanel'
import { ProviderIcon } from '../../common/ProviderIcon'
import { RecentActivityCard } from '../../common/RecentActivityCard'
import { SdlcPipelineStrip } from '../../common/SdlcPipelineStrip'
import { SpendTrendCard } from '../../common/SpendTrendCard'
import { useData } from '../../../context/DataContext'
import { useTranslation } from '../../../context/I18nContext'
import { VIEWS } from '../../../constants'
import { buildRepoWebUrl, safeExternalUrl } from '../../../utils/gitUrls'
import type {
  ActivityEvent,
  Project,
  ProjectRepository,
  Run,
  Seed,
  SeedStage,
} from '../../../types'

export interface ProjectOverviewTabProps {
  project: Project
  projectSeeds: Seed[]
  projectRuns: Run[]
  projectEvents: ActivityEvent[]
  repos: ProjectRepository[]
  effectiveMonthlyBudget: number | null
  canManage: boolean
  onOpenSeeds: (stage?: SeedStage) => void
  onOpenCode?: (repoId?: number | null) => void
  onOpenSettings: (section?: string) => void
}

export const ProjectOverviewTab: React.FC<ProjectOverviewTabProps> = ({
  project,
  projectSeeds,
  projectRuns,
  projectEvents,
  repos,
  effectiveMonthlyBudget,
  canManage,
  onOpenSeeds,
  onOpenCode,
  onOpenSettings,
}) => {
  const { backends, setSelectedProject, setActiveView } = useData()
  const { t } = useTranslation()

  const backendsById = useMemo(
    () => new Map(backends.map((b) => [b.id, b])),
    [backends]
  )

  return (
    <div className="flex flex-col gap-6">
      {/* SDLC Pipeline Strip scoped to this project */}
      <SdlcPipelineStrip seeds={projectSeeds} onSelectStage={onOpenSeeds} />

      {/* Operator Review Panel scoped to this project */}
      <OperatorReviewPanel
        seeds={projectSeeds}
        projectId={project.id}
        onViewAll={() => onOpenSeeds()}
        showNominalState
      />

      {/* Two-Column Balanced 2x2 Operational Grid */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
        {/* Left Column: Agents Working + Spend */}
        <div className="flex flex-col gap-6">
          <AgentsWorkingCard
            seeds={projectSeeds}
            emptyReason={
              repos.length === 0
                ? 'Connect a target repository to this project before queued seeds are picked up by agents.'
                : undefined
            }
          />

          <SpendTrendCard
            seeds={projectSeeds}
            runs={projectRuns}
            monthlyBudgetUsd={effectiveMonthlyBudget}
            showSeedBreakdown
            onConfigureBudget={canManage ? () => onOpenSettings('budget') : undefined}
          />
        </div>

        {/* Right Column: Target Repositories + Recent Activity */}
        <div className="flex flex-col gap-6">
          <Card className="p-5 sm:p-6 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs h-fit">
            <div className="flex items-center justify-between pb-3.5 border-b border-zinc-100 dark:border-zinc-800">
              <div className="flex items-center gap-2.5">
                <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
                  <FolderGit2 size={16} />
                </div>
                <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
                  <span>{t('projects.targetRepos')}</span>
                  <span className="text-xs font-semibold px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/20 font-mono">
                    {repos.length}
                  </span>
                </h3>
              </div>
              {canManage && (
                <button
                  type="button"
                  onClick={() => onOpenSettings('repositories')}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 bg-zinc-100/80 dark:bg-zinc-800/70 hover:bg-teal-500/10 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer"
                >
                  <Settings size={13} />
                  <span>Manage</span>
                </button>
              )}
            </div>

            {repos.length === 0 ? (
              <div className="py-10 flex flex-col items-center justify-center text-center px-4 gap-2">
                <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center mb-0.5">
                  <FolderGit2 size={20} />
                </div>
                <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
                  {t('projects.connectFirstRepoTitle')}
                </h4>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm leading-relaxed">
                  {t('projects.connectFirstRepoDesc')}
                </p>
                {canManage && (
                  <button
                    type="button"
                    onClick={() => onOpenSettings('repositories')}
                    className="mt-1 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-teal-600 dark:text-teal-400 bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 transition cursor-pointer"
                  >
                    <Plus size={13} />
                    <span>{t('projects.connectRepo')}</span>
                  </button>
                )}
              </div>
            ) : (
              <div className="divide-y divide-zinc-200 dark:divide-zinc-700/80 pt-1">
                {repos.map((repo) => {
                  const backend = backendsById.get(repo.backend_id)
                  const isLocalRepo =
                    backend?.provider === 'local' ||
                    repo.full_name.startsWith('local/')
                  const remoteUrl = isLocalRepo
                    ? null
                    : safeExternalUrl(
                        buildRepoWebUrl(repo, backend, {
                          ref: repo.default_branch,
                        })
                      )
                  return (
                    <div
                      key={repo.id ?? repo.full_name}
                      className="py-3.5 first:pt-4 last:pb-0 flex items-center justify-between gap-3"
                    >
                      <button
                        type="button"
                        onClick={() => onOpenCode?.(repo.id)}
                        title="Browse repository files in GitSeed"
                        className="flex items-center gap-3 min-w-0 text-left group cursor-pointer"
                      >
                        <span className="text-zinc-600 dark:text-zinc-300 shrink-0">
                          <ProviderIcon
                            type="git"
                            provider={backend?.provider || (isLocalRepo ? 'local' : 'github')}
                            size={16}
                          />
                        </span>
                        <div className="flex flex-col min-w-0">
                          <div className="flex items-center gap-2 min-w-0">
                            <span className="text-sm font-mono font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-teal-600 dark:group-hover:text-teal-400 transition truncate">
                              {repo.full_name}
                            </span>
                            {isLocalRepo && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25 shrink-0">
                                On-Device Local
                              </span>
                            )}
                            {repo.is_primary && (
                              <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25 shrink-0">
                                <Star size={10} className="fill-current" />
                                Primary
                              </span>
                            )}
                          </div>
                          {backend && (
                            <span className="text-xs text-zinc-500 dark:text-zinc-400 truncate mt-0.5">
                              via {backend.name}
                            </span>
                          )}
                        </div>
                      </button>

                      <div className="flex items-center gap-2 shrink-0">
                        <button
                          type="button"
                          onClick={() => onOpenCode?.(repo.id)}
                          title="Browse code in GitSeed"
                          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-100 dark:bg-zinc-800 hover:bg-teal-500/10 hover:text-teal-600 dark:hover:text-teal-400 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono text-zinc-700 dark:text-zinc-300 transition cursor-pointer"
                        >
                          <GitBranch size={12} className="text-zinc-400" />
                          <span>{repo.default_branch}</span>
                          <Code2 size={12} className="text-teal-500 ml-0.5" />
                        </button>
                        {!isLocalRepo && remoteUrl && (
                          <a
                            href={remoteUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Open repository on remote service"
                            className="p-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 hover:bg-teal-500/10 text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 border border-zinc-200 dark:border-zinc-700/80 transition"
                          >
                            <ExternalLink size={13} />
                          </a>
                        )}
                      </div>
                    </div>
                  )
                })}
              </div>
            )}
          </Card>

          <RecentActivityCard
            events={projectEvents}
            maxItems={6}
            onViewAll={() => {
              setSelectedProject(project.id)
              setActiveView(VIEWS.TELEMETRY)
            }}
          />
        </div>
      </div>
    </div>
  )
}
