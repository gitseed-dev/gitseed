import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Edit2,
  Lock,
  Power,
  ShieldCheck,
  Zap,
} from 'lucide-react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { ActionMenu } from '../components/common/ActionMenu'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { StageBadge } from '../components/common/StageBadge'
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
} from '../components/layout/EntityPageLayout'
import { PolicyDrawer } from '../components/modals/PolicyDrawer'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import {
  formatRoleLabel,
  getUncoveredStages,
  ORDERED_STAGES,
  STAGE_METADATA,
} from '../utils/policies'
import type { SeedStage, StagePolicy } from '../types'

type PolicyFilterTab = 'all' | 'enforced' | 'skipped'

export const PoliciesView: React.FC = () => {
  const {
    policies,
    agents,
    activeDetailId,
    clearDetail,
  } = useData()
  const { t } = useTranslation()
  const queryClient = useQueryClient()

  const [filterTab, setFilterTab] = useState<PolicyFilterTab>('enforced')
  const [searchQuery, setSearchQuery] = useState('')
  const [selectedStage, setSelectedStage] = useState<SeedStage | null>(null)
  const [drawerOpen, setDrawerOpen] = useState(false)

  const orderedPolicies = useMemo(
    () =>
      ORDERED_STAGES.map((st) => policies.find((p) => p.stage === st)).filter(
        (p): p is StagePolicy => Boolean(p)
      ),
    [policies]
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

  const openPolicyDrawer = (stage: SeedStage) => {
    setSelectedStage(stage)
    setDrawerOpen(true)
    try {
      const sp = new URLSearchParams(window.location.search)
      sp.set('stage', stage)
      const qs = sp.toString()
      window.history.pushState(null, '', `${window.location.pathname}?${qs}`)
    } catch {
      // Ignore URL errors
    }
  }

  const closePolicyDrawer = () => {
    setDrawerOpen(false)
    setSelectedStage(null)
    clearDetail()
    try {
      const sp = new URLSearchParams(window.location.search)
      if (sp.has('stage')) {
        sp.delete('stage')
        const qs = sp.toString()
        window.history.pushState(
          null,
          '',
          qs ? `${window.location.pathname}?${qs}` : window.location.pathname
        )
      }
    } catch {
      // Ignore URL errors
    }
  }

  useEffect(() => {
    const syncFromUrl = () => {
      try {
        const sp = new URLSearchParams(window.location.search)
        const stageParam = (sp.get('stage') || activeDetailId) as SeedStage | null
        if (stageParam && ORDERED_STAGES.includes(stageParam)) {
          setSelectedStage(stageParam)
          setDrawerOpen(true)
        }
      } catch {
        // Ignore
      }
    }
    syncFromUrl()
    window.addEventListener('popstate', syncFromUrl)
    return () => window.removeEventListener('popstate', syncFromUrl)
  }, [activeDetailId])

  const enforcedCount = useMemo(
    () => orderedPolicies.filter((p) => p.enabled).length,
    [orderedPolicies]
  )
  const skippedCount = useMemo(
    () => orderedPolicies.filter((p) => !p.enabled).length,
    [orderedPolicies]
  )
  const uncoveredStages = useMemo(
    () => getUncoveredStages(orderedPolicies, agents),
    [orderedPolicies, agents]
  )

  const handleToggleStageEnabled = async (pol: StagePolicy) => {
    const meta = STAGE_METADATA[pol.stage]
    if (meta.isRequired) return
    try {
      await api.policies.updateOrg(pol.stage, { enabled: !pol.enabled })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      console.error('Failed to toggle stage policy enabled state:', err)
    }
  }

  const filteredPolicies = useMemo(() => {
    return orderedPolicies.filter((pol) => {
      if (filterTab === 'enforced' && !pol.enabled) return false
      if (filterTab === 'skipped' && pol.enabled) return false

      if (searchQuery.trim()) {
        const q = searchQuery.toLowerCase().trim()
        const meta = STAGE_METADATA[pol.stage]
        const ag = pol.agent_id ? agentsById.get(pol.agent_id) : undefined
        const matchStage =
          pol.stage.toLowerCase().includes(q) ||
          meta.label.toLowerCase().includes(q) ||
          meta.shortDescription.toLowerCase().includes(q)
        const matchAgent = ag
          ? ag.name.toLowerCase().includes(q) ||
            ag.handle.toLowerCase().includes(q)
          : false
        const matchRole = pol.min_approver_role.toLowerCase().includes(q)
        if (!matchStage && !matchAgent && !matchRole) return false
      }
      return true
    })
  }, [orderedPolicies, filterTab, searchQuery, agentsById])

  const tabItems = [
    { id: 'enforced', label: t('routes.tabs.enforced'), count: enforcedCount },
    ...(skippedCount > 0
      ? [{ id: 'skipped', label: t('routes.tabs.skipped'), count: skippedCount }]
      : []),
    { id: 'all', label: t('routes.tabs.all'), count: orderedPolicies.length },
  ]

  return (
    <>
      <EntityPageLayout
        title={t('routes.title')}
        subtitle={t('routes.subtitle')}
        count={enforcedCount}
        isGlobal
        storageKey="gitseed_view_policies"
        searchQuery={searchQuery}
        onSearchChange={setSearchQuery}
        searchPlaceholder={t('routes.searchPlaceholder')}
        isEmpty={filteredPolicies.length === 0}
        metrics={
          uncoveredStages.length > 0 ? (
            <div className="p-3.5 rounded-2xl border border-amber-500/30 bg-amber-500/5 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
              <div className="flex items-center gap-2.5 text-xs text-amber-800 dark:text-amber-300">
                <AlertTriangle size={15} className="text-amber-500 shrink-0" />
                <span>
                  <span className="font-semibold">{t('routes.coverageGapTitle')}:</span>{' '}
                  {uncoveredStages.length === 1
                    ? t('routes.coverageGapSingle')
                    : t('routes.coverageGapPlural', { count: uncoveredStages.length })}
                </span>
              </div>
              <div className="flex items-center gap-1.5 flex-wrap">
                {uncoveredStages.map((pol) => (
                  <button
                    key={pol.stage}
                    type="button"
                    onClick={() => openPolicyDrawer(pol.stage)}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white dark:bg-zinc-900 border border-amber-500/30 text-xs font-semibold text-zinc-800 dark:text-zinc-200 hover:border-amber-500 transition cursor-pointer"
                  >
                    <StageBadge stage={pol.stage} />
                    <span className="text-teal-600 dark:text-teal-400">
                      Assign →
                    </span>
                  </button>
                ))}
              </div>
            </div>
          ) : undefined
        }
        filterTabs={
          <div className="flex items-center gap-2 flex-nowrap shrink-0">
            <SegmentedControl
              items={tabItems}
              selectedId={filterTab}
              onSelect={(id) => setFilterTab(id as PolicyFilterTab)}
            />
          </div>
        }
        emptyMessage={
          <div className="flex flex-col items-center">
            <p className="font-medium">{t('routes.noMatch')}</p>
            {(filterTab !== 'enforced' || searchQuery) && (
              <button
                type="button"
                onClick={() => {
                  setFilterTab('enforced')
                  setSearchQuery('')
                }}
                className="mt-2 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                {t('routes.clearFilter')}
              </button>
            )}
          </div>
        }
        renderList={() => (
          <EntityTableCard>
            <EntityTableHeaderRow>
              <th className="py-3 px-4 font-semibold text-zinc-500 dark:text-zinc-400">
                {t('routes.table.stage')}
              </th>
              <th className="py-3 px-4 font-semibold text-zinc-500 dark:text-zinc-400">
                {t('routes.table.agent')}
              </th>
              <th className="py-3 px-4 font-semibold text-zinc-500 dark:text-zinc-400">
                {t('routes.table.approvalGate')}
              </th>
              <th className="py-3 px-4 font-semibold text-zinc-500 dark:text-zinc-400">
                {t('routes.table.limits')}
              </th>
              <th className="py-3 px-4 font-semibold text-zinc-500 dark:text-zinc-400">
                {t('routes.table.status')}
              </th>
              <th className="py-3 px-4 text-right font-semibold text-zinc-700 dark:text-zinc-300">
                {t('routes.table.actions')}
              </th>
            </EntityTableHeaderRow>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800/60">
              {filteredPolicies.map((pol) => {
                const meta = STAGE_METADATA[pol.stage]
                const agent = pol.agent_id ? agentsById.get(pol.agent_id) : null
                const isAgentMissingOrPaused =
                  pol.enabled && (!agent || !agent.enabled || Boolean(agent.archived_at))
                const isSelected = selectedStage === pol.stage

                return (
                  <tr
                    key={pol.stage}
                    onClick={() => openPolicyDrawer(pol.stage)}
                    className={`transition-colors cursor-pointer ${
                      isSelected
                        ? 'bg-teal-500/[0.06] dark:bg-teal-500/10'
                        : 'hover:bg-zinc-100/80 dark:hover:bg-zinc-800/40'
                    } ${!pol.enabled ? 'opacity-60' : ''}`}
                  >
                    {/* 1. Stage */}
                    <td className="py-3.5 px-4 max-w-[230px]">
                      <div className="flex items-center gap-3 min-w-0">
                        <span className="w-6 h-6 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200/80 dark:border-zinc-700/80 font-mono text-xs font-bold text-zinc-500 dark:text-zinc-400 flex items-center justify-center shrink-0">
                          {meta.stepNumber}
                        </span>
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <StageBadge stage={pol.stage} />
                          </div>
                          <p
                            title={meta.shortDescription}
                            className="text-xs text-zinc-500 dark:text-zinc-400 truncate mt-1"
                          >
                            {meta.shortDescription}
                          </p>
                        </div>
                      </div>
                    </td>

                    {/* 2. Assigned Stage Agent */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {!pol.enabled ? (
                        <span className="text-xs text-zinc-400 dark:text-zinc-500">
                          -
                        </span>
                      ) : agent && !agent.archived_at ? (
                        <div className="flex items-center gap-2">
                          <div className="w-7 h-7 rounded-lg bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200/80 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
                            <Bot size={14} />
                          </div>
                          <div className="flex items-center gap-1.5">
                            <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                              {agent.name}
                            </span>
                            <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                              @{agent.handle}
                            </span>
                            {!agent.enabled && (
                              <span className="px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/20">
                                Paused
                              </span>
                            )}
                          </div>
                        </div>
                      ) : (
                        <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400">
                          <AlertTriangle size={13} />
                          <span>No agent assigned</span>
                        </span>
                      )}
                    </td>

                    {/* 3. Approval Gate */}
                    <td className="py-3.5 px-4 max-w-[190px]">
                      {!pol.enabled ? (
                        <span className="text-xs text-zinc-400 dark:text-zinc-500">
                          -
                        </span>
                      ) : pol.requires_approval ? (
                        <div
                          className="flex flex-col gap-0.5 min-w-0"
                          title={`${pol.min_approvers}+ ${formatRoleLabel(pol.min_approver_role)} · ${pol.allow_self_approval ? 'Self-approval allowed' : 'Distinct approver required'}${pol.allow_auto_approve ? ' · Auto-approve in autonomous mode' : ''}`}
                        >
                          <div className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                            <ShieldCheck size={14} className="text-teal-500 shrink-0" />
                            <span className="truncate">
                              {pol.min_approvers}+ {formatRoleLabel(pol.min_approver_role)}
                            </span>
                          </div>
                          <div className="text-xs text-zinc-500 dark:text-zinc-400 pl-5 truncate">
                            {pol.allow_self_approval ? 'Self-ok' : 'Distinct approver'}
                            {pol.allow_auto_approve ? ' · Auto-ok' : ''}
                          </div>
                        </div>
                      ) : (
                        <div className="inline-flex items-center gap-1.5 text-xs font-medium text-zinc-700 dark:text-zinc-300 whitespace-nowrap">
                          <Zap size={14} className="text-amber-500 shrink-0" />
                          <span>Auto-advance</span>
                        </div>
                      )}
                    </td>

                    {/* 4. Execution Limits */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {!pol.enabled ? (
                        <span className="text-xs text-zinc-400 dark:text-zinc-500">
                          -
                        </span>
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
                          {pol.instructions && (
                            <span
                              className="ml-1 px-1.5 py-0.5 rounded text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80"
                              title="Has stage instructions"
                            >
                              Instructions
                            </span>
                          )}
                        </div>
                      )}
                    </td>

                    {/* 5. Status */}
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
                            <span
                              title="Required stage (cannot be skipped)"
                              className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200/80 dark:border-zinc-700/80"
                            >
                              <Lock size={9} />
                              <span>Required</span>
                            </span>
                          )}
                        </div>
                      )}
                    </td>

                    {/* 6. Actions */}
                    <td
                      className="py-3.5 px-4 text-right whitespace-nowrap"
                      onClick={(e) => e.stopPropagation()}
                    >
                      <ActionMenu
                        items={[
                          {
                            label: t('routes.actions.configure'),
                            icon: <Edit2 size={14} />,
                            onClick: () => openPolicyDrawer(pol.stage),
                          },
                          ...(!meta.isRequired
                            ? [
                                {
                                  label: pol.enabled
                                    ? t('routes.actions.skip')
                                    : t('routes.actions.enforce'),
                                  icon: <Power size={14} />,
                                  onClick: () => handleToggleStageEnabled(pol),
                                },
                              ]
                            : []),
                        ]}
                      />
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </EntityTableCard>
        )}
      />

      <PolicyDrawer
        isOpen={drawerOpen}
        onClose={closePolicyDrawer}
        policy={selectedPolicy}
      />
    </>
  )
}

