import React, { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  Bot,
  Edit2,
  Lock,
  RotateCcw,
  ShieldCheck,
  Zap,
} from 'lucide-react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { ActionMenu } from '../../common/ActionMenu'
import { StageBadge } from '../../common/StageBadge'
import {
  EntityTableCard,
  EntityTableHeaderRow,
} from '../../layout/EntityPageLayout'
import { PolicyDrawer } from '../../modals/PolicyDrawer'
import { useData } from '../../../context/DataContext'
import { useTranslation } from '../../../context/I18nContext'
import {
  formatRoleLabel,
  ORDERED_STAGES,
  STAGE_METADATA,
} from '../../../utils/policies'
import type { Project, SeedStage, StagePolicy } from '../../../types'

export interface ProjectPoliciesTabProps {
  project: Project
  effectivePolicies: StagePolicy[]
  canManage: boolean
}

export const ProjectPoliciesTab: React.FC<ProjectPoliciesTabProps> = ({
  project,
  effectivePolicies,
  canManage,
}) => {
  const queryClient = useQueryClient()
  const { agents } = useData()
  const { t } = useTranslation()

  const [selectedStage, setSelectedStage] = useState<SeedStage | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const orderedPolicies = useMemo(
    () =>
      ORDERED_STAGES.map((st) =>
        effectivePolicies.find((p) => p.stage === st)
      ).filter((p): p is StagePolicy => Boolean(p)),
    [effectivePolicies]
  )

  const agentsById = useMemo(
    () => new Map(agents.map((a) => [a.id, a])),
    [agents]
  )

  const selectedPolicy = useMemo(
    () =>
      selectedStage
        ? orderedPolicies.find((p) => p.stage === selectedStage) || null
        : null,
    [orderedPolicies, selectedStage]
  )

  const overriddenStagesCount = useMemo(
    () =>
      orderedPolicies.filter(
        (p) => (p.overridden_fields || []).length > 0
      ).length,
    [orderedPolicies]
  )

  const handleResetStage = async (stage: SeedStage) => {
    try {
      await api.policies.resetProject(project.id, stage)
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectPolicies(project.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      console.error('Failed to reset stage policy:', err)
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Info Banner */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 px-4 py-3 rounded-2xl bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 shadow-xs">
        <div className="flex items-center gap-2.5 text-xs text-zinc-600 dark:text-zinc-300">
          <ShieldCheck size={16} className="text-teal-500 shrink-0" />
          <span>
            Stages inherit organization policies unless overridden for{' '}
            <strong className="font-semibold text-zinc-900 dark:text-zinc-100">
              {project.name}
            </strong>
            .
          </span>
        </div>
        <span className="text-xs font-mono font-semibold text-zinc-500 dark:text-zinc-400">
          {overriddenStagesCount === 0
            ? t('details.project.settingsConfig.inheritsOrgPolicy')
            : overriddenStagesCount === 1
              ? '1 stage overridden'
              : `${overriddenStagesCount} stages overridden`}
        </span>
      </div>

      {/* Policies Table */}
      <EntityTableCard>
        <EntityTableHeaderRow>
          <th className="py-3 px-4">{t('routes.table.stage')}</th>
          <th className="py-3 px-4">{t('routes.table.agent')}</th>
          <th className="py-3 px-4">Approval Gate</th>
          <th className="py-3 px-4">{t('routes.table.limits')}</th>
          <th className="py-3 px-4">{t('routes.table.status')}</th>
          {canManage && (
            <th className="py-3 px-4 text-right">
              {t('routes.table.actions')}
            </th>
          )}
        </EntityTableHeaderRow>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-700/80">
            {orderedPolicies.map((pol) => {
              const meta = STAGE_METADATA[pol.stage]
              const agent = pol.agent_id ? agentsById.get(pol.agent_id) : null
              const overrideCount = (pol.overridden_fields || []).length
              const isOverridden = overrideCount > 0
              const isAgentMissingOrPaused =
                pol.enabled &&
                (!agent || !agent.enabled || Boolean(agent.archived_at))

              return (
                <tr
                  key={pol.stage}
                  onClick={() => {
                    if (canManage) {
                      setSelectedStage(pol.stage)
                      setDrawerOpen(true)
                    }
                  }}
                  className={`transition-colors ${
                    canManage
                      ? 'cursor-pointer hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40'
                      : ''
                  } ${!pol.enabled ? 'opacity-60' : ''}`}
                >
                  {/* 1. Stage */}
                  <td className="py-3.5 px-4 max-w-[220px]">
                    <div className="flex items-center gap-3 min-w-0">
                      <span className="w-6 h-6 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 font-mono text-xs font-bold text-zinc-500 dark:text-zinc-400 flex items-center justify-center shrink-0">
                        {meta.stepNumber}
                      </span>
                      <div className="min-w-0">
                        <div className="flex items-center gap-2">
                          <StageBadge stage={pol.stage} />
                          {isOverridden ? (
                            <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25 shrink-0">
                              {overrideCount === 1
                                ? t('details.project.settingsConfig.oneOverride')
                                : t('details.project.settingsConfig.overridesCount', {
                                    count: overrideCount,
                                  })}
                            </span>
                          ) : (
                            <span className="inline-flex items-center px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80 shrink-0">
                              {t('details.project.settingsConfig.inherited')}
                            </span>
                          )}
                        </div>
                        <p
                          title={meta.shortDescription}
                          className="text-[11px] text-zinc-500 dark:text-zinc-400 truncate mt-1"
                        >
                          {meta.shortDescription}
                        </p>
                      </div>
                    </div>
                  </td>

                  {/* 2. Agent */}
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {!pol.enabled ? (
                      <span className="text-xs text-zinc-400">-</span>
                    ) : agent && !agent.archived_at ? (
                      <div className="flex items-center gap-2">
                        <div className="w-7 h-7 rounded-lg bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
                          <Bot size={14} />
                        </div>
                        <div className="flex items-center gap-1.5">
                          <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                            {agent.name}
                          </span>
                          <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                            @{agent.handle}
                          </span>
                        </div>
                      </div>
                    ) : (
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400">
                        <AlertTriangle size={13} />
                        <span>No agent assigned</span>
                      </span>
                    )}
                  </td>

                  {/* 4. Approval Gate */}
                  <td className="py-3.5 px-4 max-w-[190px]">
                    {!pol.enabled ? (
                      <span className="text-xs text-zinc-400">-</span>
                    ) : pol.requires_approval ? (
                      <div
                        className="flex flex-col gap-0.5 min-w-0"
                        title={`${pol.min_approvers}+ ${formatRoleLabel(pol.min_approver_role)} · ${pol.allow_self_approval ? 'Self-approval allowed' : 'Distinct approver required'}${pol.allow_auto_approve ? ' · Auto-approve in autonomous mode' : ''}`}
                      >
                        <div className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                          <ShieldCheck
                            size={14}
                            className="text-teal-500 shrink-0"
                          />
                          <span className="truncate">
                            {pol.min_approvers}+{' '}
                            {formatRoleLabel(pol.min_approver_role)}
                          </span>
                        </div>
                        <div className="text-[11px] text-zinc-500 dark:text-zinc-400 pl-5 truncate">
                          {pol.allow_self_approval
                            ? 'Self-ok'
                            : 'Distinct approver'}
                          {pol.allow_auto_approve
                            ? ' · Auto-ok'
                            : ''}
                        </div>
                      </div>
                    ) : (
                      <div className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-300 whitespace-nowrap">
                        <Zap size={14} className="text-amber-500 shrink-0" />
                        <span>Auto-advance</span>
                      </div>
                    )}
                  </td>

                  {/* 5. Limits */}
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {!pol.enabled ? (
                      <span className="text-xs text-zinc-400">-</span>
                    ) : (
                      <div className="flex items-center gap-1.5 font-mono text-xs text-zinc-600 dark:text-zinc-300">
                        <span>
                          {pol.max_attempts}{' '}
                          {pol.max_attempts === 1 ? 'attempt' : 'attempts'}
                        </span>
                        <span className="text-zinc-400">·</span>
                        <span>{pol.timeout_minutes}m</span>
                        {pol.stage === 'ideate' && (
                          <>
                            <span className="text-zinc-400">·</span>
                            <span>
                              {pol.min_acceptance_criteria ?? 1}–{pol.max_acceptance_criteria ?? 6} criteria
                            </span>
                          </>
                        )}
                        {pol.stage === 'plan' && (
                          <>
                            <span className="text-zinc-400">·</span>
                            <span>≤ {pol.max_tasks ?? 5} tasks</span>
                          </>
                        )}
                        {(pol.instructions || pol.inherited_instructions) && (
                          <span
                            className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80"
                            title={pol.instructions ? 'Has project stage instructions' : 'Uses organization stage instructions'}
                          >
                            Instructions
                          </span>
                        )}
                      </div>
                    )}
                  </td>

                  {/* 6. Status */}
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {!pol.enabled ? (
                      <span className="inline-flex items-center gap-1.5 text-xs text-zinc-500 dark:text-zinc-400">
                        <span className="w-2 h-2 rounded-full bg-zinc-400" />
                        <span>{t('routes.table.skipped')}</span>
                      </span>
                    ) : isAgentMissingOrPaused ? (
                      <span className="inline-flex items-center gap-1.5 text-xs font-medium text-amber-600 dark:text-amber-400">
                        <span className="w-2 h-2 rounded-full bg-amber-500" />
                        <span>Needs Agent</span>
                      </span>
                    ) : (
                      <div className="inline-flex items-center gap-2">
                        <span className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300">
                          <span className="w-2 h-2 rounded-full bg-emerald-500" />
                          <span>{t('routes.table.enforced')}</span>
                        </span>
                        {meta.isRequired && (
                          <span className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80">
                            <Lock size={9} />
                            <span>Required</span>
                          </span>
                        )}
                      </div>
                    )}
                  </td>

                  {/* 7. Actions */}
                  {canManage && (
                    <td
                      className="py-3.5 px-4 text-right whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ActionMenu
                        items={[
                          {
                            label: isOverridden
                              ? 'Edit Override'
                              : 'Override Stage Policy',
                            icon: <Edit2 size={14} />,
                            onClick: () => {
                              setSelectedStage(pol.stage)
                              setDrawerOpen(true)
                            },
                          },
                          ...(isOverridden
                            ? [
                                {
                                  label: t(
                                    'details.project.settingsConfig.resetToDefault'
                                  ),
                                  icon: <RotateCcw size={14} />,
                                  onClick: () => handleResetStage(pol.stage),
                                },
                              ]
                            : []),
                        ]}
                      />
                    </td>
                  )}
                </tr>
              )
            })}
          </tbody>
      </EntityTableCard>

      <PolicyDrawer
        isOpen={drawerOpen}
        onClose={() => {
          setDrawerOpen(false)
          setSelectedStage(null)
        }}
        policy={selectedPolicy}
        projectId={project.id}
        projectName={project.name}
      />
    </div>
  )
}
