import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  Bot,
  Check,
  Clock,
  Lock,
  RotateCcw,
  ScrollText,
  ShieldCheck,
  X,
  Zap,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS } from '../../constants'
import { StageBadge } from '../common/StageBadge'
import { useData } from '../../context/DataContext'
import { getRuntimeDefinition } from '../../utils/agents'
import { STAGE_METADATA } from '../../utils/policies'
import type {
  ProjectRole,
  StagePolicy,
} from '../../types'

export interface PolicyDrawerProps {
  isOpen: boolean
  onClose: () => void
  policy: StagePolicy | null
  projectId?: string | null
  projectName?: string | null
}

const TIMEOUT_PRESETS = [15, 30, 60, 120]

export const PolicyDrawer: React.FC<PolicyDrawerProps> = ({
  isOpen,
  onClose,
  policy,
  projectId = null,
  projectName = null,
}) => {
  const { agents, setActiveView } = useData()
  const queryClient = useQueryClient()

  const [enabled, setEnabled] = useState(true)
  const [agentId, setAgentId] = useState<string>('')
  const [requiresApproval, setRequiresApproval] = useState(true)
  const [minApprovers, setMinApprovers] = useState(1)
  const [minApproverRole, setMinApproverRole] = useState<ProjectRole>('developer')
  const [allowSelfApproval, setAllowSelfApproval] = useState(true)
  const [allowAutoApprove, setAllowAutoApprove] = useState(false)
  const [allowSecondOpinions, setAllowSecondOpinions] = useState(true)
  const [maxAttempts, setMaxAttempts] = useState(3)
  const [timeoutMinutes, setTimeoutMinutes] = useState(30)
  const [minAcceptanceCriteria, setMinAcceptanceCriteria] = useState(1)
  const [maxAcceptanceCriteria, setMaxAcceptanceCriteria] = useState(6)
  const [maxTasks, setMaxTasks] = useState(5)
  const [instructions, setInstructions] = useState('')

  const [isSaving, setIsSaving] = useState(false)
  const [isResetting, setIsResetting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)

  const stageAgents = useMemo(
    () => agents.filter((a) => !a.archived_at && a.kind === 'stage'),
    [agents]
  )

  useEffect(() => {
    if (!isOpen || !policy) return
    setErrorMessage(null)
    setEnabled(policy.enabled)
    setAgentId(policy.agent_id || '')
    setRequiresApproval(policy.requires_approval)
    setMinApprovers(policy.min_approvers)
    setMinApproverRole(policy.min_approver_role)
    setAllowSelfApproval(policy.allow_self_approval)
    setAllowAutoApprove(policy.allow_auto_approve)
    setAllowSecondOpinions(
      policy.allow_second_opinions ??
        ['ideate', 'plan', 'review'].includes(policy.stage)
    )
    setMaxAttempts(policy.max_attempts)
    setTimeoutMinutes(policy.timeout_minutes)
    setMinAcceptanceCriteria(policy.min_acceptance_criteria ?? 1)
    setMaxAcceptanceCriteria(policy.max_acceptance_criteria ?? 6)
    setMaxTasks(policy.max_tasks ?? 5)
    setInstructions(policy.instructions || '')
  }, [isOpen, policy])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !isSaving && !isResetting) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isResetting, isSaving, onClose])

  const isDirty = useMemo(() => {
    if (!policy) return false
    if (enabled !== policy.enabled) return true
    if ((agentId || null) !== (policy.agent_id || null)) return true
    if (requiresApproval !== policy.requires_approval) return true
    if (minApprovers !== policy.min_approvers) return true
    if (minApproverRole !== policy.min_approver_role) return true
    if (allowSelfApproval !== policy.allow_self_approval) return true
    if (allowAutoApprove !== policy.allow_auto_approve) return true
    if (
      ['ideate', 'plan', 'review'].includes(policy.stage) &&
      allowSecondOpinions !== (policy.allow_second_opinions ?? true)
    ) {
      return true
    }
    if (maxAttempts !== policy.max_attempts) return true
    if (timeoutMinutes !== policy.timeout_minutes) return true
    if (
      policy.stage === 'ideate' &&
      (minAcceptanceCriteria !== (policy.min_acceptance_criteria ?? 1) ||
        maxAcceptanceCriteria !== (policy.max_acceptance_criteria ?? 6))
    ) {
      return true
    }
    if (policy.stage === 'plan' && maxTasks !== (policy.max_tasks ?? 5)) {
      return true
    }
    if (instructions.trim() !== (policy.instructions || '').trim()) return true
    return false
  }, [
    policy,
    enabled,
    agentId,
    requiresApproval,
    minApprovers,
    minApproverRole,
    allowSelfApproval,
    allowAutoApprove,
    allowSecondOpinions,
    maxAttempts,
    timeoutMinutes,
    minAcceptanceCriteria,
    maxAcceptanceCriteria,
    maxTasks,
    instructions,
  ])

  if (!isOpen || !policy) return null

  const meta = STAGE_METADATA[policy.stage]
  const hasProjectOverrides =
    Boolean(projectId) &&
    ((policy.overridden_fields?.length || 0) > 0 || Boolean(policy.instructions))
  const inheritedInstructions = projectId ? (policy.inherited_instructions || '').trim() : ''

  const handleSave = async () => {
    if (!isDirty || isSaving) return
    setIsSaving(true)
    setErrorMessage(null)

    try {
      const safeMinCriteria = Math.min(20, Math.max(1, minAcceptanceCriteria))
      const safeMaxCriteria = Math.min(
        20,
        Math.max(safeMinCriteria, maxAcceptanceCriteria)
      )
      const safeMaxTasks = Math.min(20, Math.max(1, maxTasks))

      const payload: Partial<StagePolicy> = {
        enabled: meta.isRequired ? true : enabled,
        agent_id: agentId || null,
        requires_approval: requiresApproval,
        min_approvers: Math.min(5, Math.max(1, minApprovers)),
        min_approver_role: minApproverRole,
        allow_self_approval: allowSelfApproval,
        allow_auto_approve: allowAutoApprove,
        ...(['ideate', 'plan', 'review'].includes(policy.stage)
          ? { allow_second_opinions: allowSecondOpinions }
          : {}),
        max_attempts: Math.min(10, Math.max(1, maxAttempts)),
        timeout_minutes: Math.min(1440, Math.max(1, timeoutMinutes)),
        ...(policy.stage === 'ideate'
          ? {
            min_acceptance_criteria: safeMinCriteria,
            max_acceptance_criteria: safeMaxCriteria,
          }
          : {}),
        ...(policy.stage === 'plan' ? { max_tasks: safeMaxTasks } : {}),
        instructions: instructions.trim() || null,
      }

      if (projectId) {
        await api.policies.updateProject(projectId, policy.stage, payload)
        await queryClient.invalidateQueries({
          queryKey: queryKeys.projectPolicies(projectId),
        })
      } else {
        await api.policies.updateOrg(policy.stage, payload)
        await queryClient.invalidateQueries({ queryKey: queryKeys.policies })
      }
      await queryClient.invalidateQueries({ queryKey: queryKeys.activity })
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.detail)
      } else {
        setErrorMessage(
          err instanceof Error ? err.message : 'Failed to update stage policy'
        )
      }
    } finally {
      setIsSaving(false)
    }
  }

  const handleResetProjectOverrides = async () => {
    if (!projectId || isResetting) return
    setIsResetting(true)
    setErrorMessage(null)
    try {
      await api.policies.resetProject(projectId, policy.stage)
      await Promise.all([
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectPolicies(projectId),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.detail)
      } else {
        setErrorMessage(
          err instanceof Error ? err.message : 'Failed to reset stage overrides'
        )
      }
    } finally {
      setIsResetting(false)
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-xs transition-opacity"
      onClick={() => {
        if (!isSaving && !isResetting) onClose()
      }}
    >
      <div
        className="w-full max-w-xl bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-700/80 h-full flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-4 shrink-0 bg-zinc-50/50 dark:bg-zinc-900/50">
          <div className="min-w-0">
            <div className="flex items-center gap-2.5">
              <span className="w-6 h-6 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 font-mono text-xs font-bold text-zinc-600 dark:text-zinc-300 flex items-center justify-center shrink-0">
                {meta.stepNumber}
              </span>
              <StageBadge stage={policy.stage} />
              <h2 className="text-base font-bold font-heading text-zinc-900 dark:text-zinc-100 truncate">
                Stage Policy
              </h2>
              {projectName && (
                <span className="px-2 py-0.5 rounded-md text-[11px] font-medium bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/20">
                  {projectName}
                </span>
              )}
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
              {meta.detailDescription}
            </p>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-xl text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-5">
          {errorMessage && (
            <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs flex items-start gap-2.5">
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <span className="leading-relaxed">{errorMessage}</span>
            </div>
          )}

          {/* 1. Stage Enablement & Assigned Stage Agent */}
          <div className="p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-4">
            <div className="flex items-center justify-between gap-4">
              <div>
                <div className="text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                  <span>Stage Execution</span>
                  {meta.isRequired && (
                    <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-semibold bg-zinc-200/80 dark:bg-zinc-700/80 text-zinc-600 dark:text-zinc-300">
                      <Lock size={10} />
                      <span>Required</span>
                    </span>
                  )}
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                  {meta.isRequired
                    ? `${meta.label} is a core pipeline stage and cannot be skipped.`
                    : `When skipped, seeds bypass ${meta.label} and advance directly to the next enabled stage.`}
                </p>
              </div>

              {!meta.isRequired && (
                <button
                  type="button"
                  onClick={() => setEnabled((prev) => !prev)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition cursor-pointer shrink-0 flex items-center gap-2 ${enabled
                      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30'
                      : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700/80'
                    }`}
                >
                  <span
                    className={`w-2 h-2 rounded-full ${enabled ? 'bg-emerald-500' : 'bg-zinc-400'
                      }`}
                  />
                  <span>{enabled ? 'Enforced' : 'Skipped'}</span>
                </button>
              )}
            </div>

            {/* Assigned Stage Agent */}
            <div className={!enabled ? 'opacity-50 pointer-events-none' : ''}>
              <div className="flex items-center justify-between mb-1.5">
                <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                  <Bot size={13} className="text-zinc-400" />
                  <span>Assigned Stage Agent</span>
                </label>
                <button
                  type="button"
                  onClick={() => {
                    onClose()
                    setActiveView(VIEWS.AGENTS)
                  }}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  <span>Manage Agents</span>
                  <ArrowRight size={11} />
                </button>
              </div>
              <select
                value={agentId}
                onChange={(e) => setAgentId(e.target.value)}
                className="w-full h-10 px-3.5 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition cursor-pointer"
              >
                <option value="">Unassigned (No agent)</option>
                {stageAgents.map((ag) => {
                  const rt = getRuntimeDefinition(ag.runtime)
                  return (
                    <option key={ag.id} value={ag.id}>
                      {ag.name} (@{ag.handle}) · {rt.shortLabel} · {ag.model}
                      {!ag.enabled ? ' [Paused]' : ''}
                    </option>
                  )
                })}
              </select>
            </div>

            {['ideate', 'plan', 'review'].includes(policy.stage) && (
              <div
                className={`pt-3 border-t border-zinc-200 dark:border-zinc-700/80 ${
                  !enabled ? 'opacity-50 pointer-events-none' : ''
                }`}
              >
                <button
                  type="button"
                  onClick={() => setAllowSecondOpinions((prev) => !prev)}
                  className="w-full flex items-center justify-between gap-3 py-1 text-left cursor-pointer"
                >
                  <div>
                    <div className="text-xs sm:text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                      Allow Second Opinions (Multi-Agent Runs)
                    </div>
                    <div className="text-xs text-zinc-500 dark:text-zinc-400">
                      Allow running up to 2 extra stage agents in parallel to compare outputs (capped at 3 runs per stage)
                    </div>
                  </div>
                  <div
                    className={`w-4 h-4 rounded-md border flex items-center justify-center shrink-0 ${
                      allowSecondOpinions
                        ? 'bg-teal-600 border-teal-600 text-white'
                        : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700/80'
                    }`}
                  >
                    {allowSecondOpinions && <Check size={11} />}
                  </div>
                </button>
              </div>
            )}
          </div>

          {/* Stage instructions: extra guidance for the stage agent (additive org -> project) */}
          <div className={`flex flex-col gap-2 ${!enabled ? 'opacity-50 pointer-events-none' : ''}`}>
            <div>
              <label className="text-xs sm:text-sm font-semibold text-zinc-800 dark:text-zinc-200 flex items-center gap-1.5">
                <ScrollText size={14} className="text-zinc-400" />
                <span>Stage Instructions</span>
                <span className="text-xs font-normal text-zinc-400">(optional)</span>
              </label>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                {projectId
                  ? `Added after the organization's ${meta.label} instructions, only for this project. The agent's own instructions still apply.`
                  : `Given to the agent on every ${meta.label} run, in addition to the agent's own instructions. Projects can add more.`}
              </p>
            </div>
            {inheritedInstructions && (
              <div className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/70 dark:bg-zinc-800/40">
                <div className="px-3 pt-2 text-[11px] font-semibold uppercase tracking-wider text-zinc-400">
                  From organization defaults
                </div>
                <div className="px-3 pb-2.5 pt-1 text-xs text-zinc-600 dark:text-zinc-300 whitespace-pre-wrap max-h-32 overflow-y-auto">
                  {inheritedInstructions}
                </div>
              </div>
            )}
            <textarea
              rows={5}
              value={instructions}
              onChange={(e) => setInstructions(e.target.value)}
              maxLength={20000}
              placeholder={
                policy.stage === 'plan'
                  ? 'e.g. Every plan must include a migration and rollback section.'
                  : policy.stage === 'review'
                    ? 'e.g. Check accessibility (labels, contrast, keyboard) on any UI change.'
                    : policy.stage === 'execute'
                      ? 'e.g. Add or update unit tests for every behavior change.'
                      : 'Markdown guidance for the agent running this stage…'
              }
              className="w-full p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500 resize-y"
            />
          </div>

          {/* 2. Approval Gate */}
          <div
            className={`flex flex-col gap-3.5 ${!enabled ? 'opacity-50 pointer-events-none' : ''
              }`}
          >
            <label className="text-xs sm:text-sm font-semibold text-zinc-800 dark:text-zinc-200">
              Exit Gate & Approvals
            </label>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => setRequiresApproval(true)}
                className={`p-3.5 rounded-xl border text-left transition cursor-pointer flex flex-col gap-1 ${requiresApproval
                    ? 'border-teal-500 bg-teal-500/10'
                    : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/40 hover:border-zinc-300 dark:hover:border-zinc-600'
                  }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                    <ShieldCheck size={15} className="text-teal-500" />
                    <span>Human Approval</span>
                  </span>
                  {requiresApproval && (
                    <Check size={14} className="text-teal-600 dark:text-teal-400" />
                  )}
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  Pause after stage run and wait for human sign-off before advancing.
                </p>
              </button>

              <button
                type="button"
                onClick={() => setRequiresApproval(false)}
                className={`p-3.5 rounded-xl border text-left transition cursor-pointer flex flex-col gap-1 ${!requiresApproval
                    ? 'border-teal-500 bg-teal-500/10'
                    : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/40 hover:border-zinc-300 dark:hover:border-zinc-600'
                  }`}
              >
                <div className="flex items-center justify-between">
                  <span className="text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                    <Zap size={15} className="text-amber-500" />
                    <span>Auto-Advance</span>
                  </span>
                  {!requiresApproval && (
                    <Check size={14} className="text-teal-600 dark:text-teal-400" />
                  )}
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                  Advance immediately to the next stage when the agent run succeeds.
                </p>
              </button>
            </div>

            {requiresApproval && (
              <div className="p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-4">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                  <div>
                    <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                      Minimum Approvers
                    </label>
                    <select
                      value={minApprovers}
                      onChange={(e) => setMinApprovers(Number(e.target.value))}
                      className="w-full h-10 px-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 cursor-pointer"
                    >
                      {[1, 2, 3, 4, 5].map((n) => (
                        <option key={n} value={n}>
                          {n} {n === 1 ? 'approver' : 'approvers'}
                        </option>
                      ))}
                    </select>
                  </div>

                  <div>
                    <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                      Minimum Project Role
                    </label>
                    <select
                      value={minApproverRole}
                      onChange={(e) =>
                        setMinApproverRole(e.target.value as ProjectRole)
                      }
                      className="w-full h-10 px-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 cursor-pointer"
                    >
                      <option value="developer">Developer or Maintainer</option>
                      <option value="maintainer">Maintainer only</option>
                    </select>
                  </div>
                </div>

                <div className="flex flex-col gap-2 pt-1 border-t border-zinc-200 dark:border-zinc-700/80">
                  <button
                    type="button"
                    onClick={() => setAllowSelfApproval((prev) => !prev)}
                    className="flex items-center justify-between gap-3 py-1.5 text-left cursor-pointer"
                  >
                    <div>
                      <div className="text-xs sm:text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                        Allow Self-Approval
                      </div>
                      <div className="text-xs text-zinc-500 dark:text-zinc-400">
                        The user who created the seed can count toward required approvers
                      </div>
                    </div>
                    <div
                      className={`w-4 h-4 rounded-md border flex items-center justify-center shrink-0 ${allowSelfApproval
                          ? 'bg-teal-600 border-teal-600 text-white'
                          : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700/80'
                        }`}
                    >
                      {allowSelfApproval && <Check size={11} />}
                    </div>
                  </button>

                  <button
                    type="button"
                    onClick={() => setAllowAutoApprove((prev) => !prev)}
                    className="flex items-center justify-between gap-3 py-1.5 text-left cursor-pointer"
                  >
                    <div>
                      <div className="text-xs sm:text-sm font-semibold text-zinc-800 dark:text-zinc-200">
                        Allow Auto-Approval in Autonomous Mode
                      </div>
                      <div className="text-xs text-zinc-500 dark:text-zinc-400">
                        Seeds explicitly set to Autonomous mode can auto-approve this stage
                      </div>
                    </div>
                    <div
                      className={`w-4 h-4 rounded-md border flex items-center justify-center shrink-0 ${allowAutoApprove
                          ? 'bg-teal-600 border-teal-600 text-white'
                          : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700/80'
                        }`}
                    >
                      {allowAutoApprove && <Check size={11} />}
                    </div>
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* 3. Execution Limits (Retries & Timeout) */}
          <div
            className={`p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-4 ${!enabled ? 'opacity-50 pointer-events-none' : ''
              }`}
          >
            <div className="text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
              <Clock size={14} className="text-zinc-400" />
              <span>Execution Limits</span>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
              <div>
                <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                  Max Attempts (1–10)
                </label>
                <input
                  type="number"
                  min={1}
                  max={10}
                  value={maxAttempts}
                  onChange={(e) =>
                    setMaxAttempts(
                      Math.min(10, Math.max(1, Number(e.target.value) || 1))
                    )
                  }
                  className="w-full h-10 px-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                />
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1.5">
                  Automatic retries before halting the seed
                </p>
              </div>

              <div>
                <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                  Timeout (minutes)
                </label>
                <div className="relative">
                  <input
                    type="number"
                    min={1}
                    max={1440}
                    value={timeoutMinutes}
                    onChange={(e) =>
                      setTimeoutMinutes(
                        Math.min(1440, Math.max(1, Number(e.target.value) || 1))
                      )
                    }
                    className="w-full h-10 pl-3 pr-36 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 [appearance:textfield] [&::-webkit-outer-spin-button]:appearance-none [&::-webkit-inner-spin-button]:appearance-none"
                  />
                  <div className="absolute right-1.5 top-1/2 -translate-y-1/2 flex items-center gap-0.5">
                    {TIMEOUT_PRESETS.map((preset) => (
                      <button
                        key={preset}
                        type="button"
                        onClick={() => setTimeoutMinutes(preset)}
                        className={`px-1.5 py-1 rounded-lg text-[11px] font-mono font-semibold transition cursor-pointer ${timeoutMinutes === preset
                            ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30'
                            : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                          }`}
                      >
                        {preset}m
                      </button>
                    ))}
                  </div>
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1.5">
                  Per-run container execution time limit
                </p>
              </div>
            </div>

            {policy.stage === 'ideate' && (
              <div className="pt-3 border-t border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-2.5">
                <div className="grid grid-cols-1 sm:grid-cols-2 gap-3 items-start">
                  <div>
                    <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                      Min Acceptance Criteria
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={minAcceptanceCriteria}
                      onChange={(e) => {
                        const nextMin = Math.min(
                          20,
                          Math.max(1, Number(e.target.value) || 1)
                        )
                        setMinAcceptanceCriteria(nextMin)
                        if (nextMin > maxAcceptanceCriteria) {
                          setMaxAcceptanceCriteria(nextMin)
                        }
                      }}
                      className="w-full h-10 px-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                    />
                  </div>
                  <div>
                    <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                      Max Acceptance Criteria
                    </label>
                    <input
                      type="number"
                      min={1}
                      max={20}
                      value={maxAcceptanceCriteria}
                      onChange={(e) => {
                        const nextMax = Math.min(
                          20,
                          Math.max(1, Number(e.target.value) || 1)
                        )
                        setMaxAcceptanceCriteria(nextMax)
                        if (nextMax < minAcceptanceCriteria) {
                          setMinAcceptanceCriteria(nextMax)
                        }
                      }}
                      className="w-full h-10 px-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                    />
                  </div>
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Bounds the number of acceptance criteria generated by agents in Ideate and Brainstorm (applies to agents, not people).
                </p>
              </div>
            )}

            {policy.stage === 'plan' && (
              <div className="pt-3 border-t border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-1.5">
                <div className="max-w-[240px]">
                  <label className="flex items-center h-5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                    Max Tasks (1–20)
                  </label>
                  <input
                    type="number"
                    min={1}
                    max={20}
                    value={maxTasks}
                    onChange={(e) =>
                      setMaxTasks(
                        Math.min(20, Math.max(1, Number(e.target.value) || 1))
                      )
                    }
                    className="w-full h-10 px-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                  />
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Caps milestone tasks generated during Plan; larger seeds propose follow-up seeds at the gate.
                </p>
              </div>
            )}
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/50 dark:bg-zinc-900/50 flex items-center justify-between gap-3 shrink-0">
          <div>
            {hasProjectOverrides && (
              <button
                type="button"
                onClick={handleResetProjectOverrides}
                disabled={isResetting || isSaving}
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-amber-600 dark:text-amber-400 hover:underline cursor-pointer"
              >
                <RotateCcw size={13} />
                <span>
                  {isResetting ? 'Resetting...' : 'Reset to Org Defaults'}
                </span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={onClose}
              isDisabled={isSaving || isResetting}
              className="cursor-pointer font-medium"
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isDisabled={!isDirty || isSaving || isResetting}
              className="cursor-pointer font-semibold shadow-sm shadow-teal-600/20"
            >
              {isSaving ? 'Saving...' : 'Save Policy'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}

