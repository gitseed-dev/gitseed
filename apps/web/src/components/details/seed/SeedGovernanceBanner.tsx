import React, { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  AlertTriangle,
  Bot,
  Check,
  ChevronDown,
  Clock,
  DollarSign,
  ExternalLink,
  GitBranch,
  GitPullRequest,
  Loader2,
  MessageSquare,
  MessageSquareWarning,
  Play,
  Plus,
  Send,
  ShieldCheck,
  Square,
  Users,
  XCircle,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api, ApiError } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { VoiceDictationButton } from '../../common/VoiceDictationButton'
import { ContextWindowBadge } from '../../common/ContextWindowBadge'
import { useAuth } from '../../../context/AuthContext'
import { useData } from '../../../context/DataContext'
import {
  formatUsd,
  getSeedAttentionReason,
  resolveRunContextWindow,
} from '../../../utils/format'
import { safeExternalUrl } from '../../../utils/gitUrls'
import { filterStageAgentsForStage } from '../../../utils/agents'
import { STAGE_LABELS } from '../../../utils/seeds'
import type {
  Approval,
  ApprovalDecision,
  PullRequestStatus,
  Run,
  Seed,
  SeedTask,
  StagePolicy,
  SuggestedFollowUpSeed,
} from '../../../types'

export interface SeedGovernanceBannerProps {
  seed: Seed
  currentPolicy: StagePolicy | null
  approvals: Approval[]
  runs?: Run[]
  tasks?: SeedTask[]
  pullRequestUrl?: string | null
  pullRequestStatus?: PullRequestStatus | null
  canApprove: boolean
  canContribute: boolean
  canRequestChanges?: boolean
  onRefresh: () => Promise<void>
  onOpenPullRequest?: () => void
}

function formatOrdinal(n: number): string {
  const mod100 = n % 100
  if (mod100 >= 11 && mod100 <= 13) return `${n}th`
  switch (n % 10) {
    case 1:
      return `${n}st`
    case 2:
      return `${n}nd`
    case 3:
      return `${n}rd`
    default:
      return `${n}th`
  }
}

function formatElapsedDuration(iso: string | null | undefined): string {
  if (!iso) return '<1m'
  const diffSec = Math.max(
    0,
    Math.floor((Date.now() - new Date(iso).getTime()) / 1000)
  )
  if (diffSec < 60) return '<1m'
  const mins = Math.floor(diffSec / 60)
  if (mins < 60) return `${mins}m`
  const hrs = Math.floor(mins / 60)
  const remMins = mins % 60
  return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`
}

export const SeedGovernanceBanner: React.FC<SeedGovernanceBannerProps> = ({
  seed,
  currentPolicy,
  approvals,
  runs = [],
  tasks = [],
  pullRequestUrl = null,
  pullRequestStatus = null,
  canApprove,
  canContribute,
  canRequestChanges,
  onRefresh,
  onOpenPullRequest,
}) => {
  const effectiveCanDecide =
    canRequestChanges ??
    (canApprove || (canContribute && currentPolicy?.min_approver_role !== 'maintainer'))
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const { allSeeds, agents } = useData()

  const [activePrompt, setActivePrompt] = useState<
    'approved' | 'changes_requested' | 'rejected' | null
  >(null)
  const [comment, setComment] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [creatingFollowUpIdx, setCreatingFollowUpIdx] = useState<number | null>(null)
  const [createdFollowUps, setCreatedFollowUps] = useState<Record<number, { id: string; number: number }>>({})
  // Working-agent controls: message (steer via @mention comment) and stop / resume
  const [messageOpen, setMessageOpen] = useState(false)
  const [messageText, setMessageText] = useState('')
  const [messageNotice, setMessageNotice] = useState<string | null>(null)
  const [confirmStop, setConfirmStop] = useState(false)
  // Second opinion picker state
  const [secondOpinionOpen, setSecondOpinionOpen] = useState(false)
  const secondOpinionMenuRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!secondOpinionOpen) return
    const handleOutsideClick = (e: MouseEvent) => {
      if (
        secondOpinionMenuRef.current &&
        !secondOpinionMenuRef.current.contains(e.target as Node)
      ) {
        setSecondOpinionOpen(false)
      }
    }
    document.addEventListener('mousedown', handleOutsideClick)
    return () => document.removeEventListener('mousedown', handleOutsideClick)
  }, [secondOpinionOpen])

  // The banner pins to the top while scrolling; publish its height so other pinned
  // elements (e.g. the Changes file list) can sit below it instead of under it.
  const bannerRef = useRef<HTMLDivElement | null>(null)
  const isPinned = seed.status !== 'merged' && seed.status !== 'closed'
  useEffect(() => {
    const root = document.documentElement
    const el = bannerRef.current
    if (!el || !isPinned) {
      root.style.setProperty('--seed-banner-offset', '0px')
      return
    }
    const update = () => root.style.setProperty('--seed-banner-offset', `${el.offsetHeight}px`)
    update()
    const observer = new ResizeObserver(update)
    observer.observe(el)
    return () => {
      observer.disconnect()
      root.style.setProperty('--seed-banner-offset', '0px')
    }
  }, [isPinned])

  const isStaged = seed.status === 'staged'
  const isQueued = seed.status === 'queued'
  const isRunning = seed.status === 'running'
  const isStopped = seed.status === 'stopped'
  const isAwaitingApproval = seed.status === 'awaiting_approval'
  const isBlocked = seed.status === 'blocked'
  const isFailed = seed.status === 'failed'
  const isMergedOrClosed = seed.status === 'merged' || seed.status === 'closed'

  if (isMergedOrClosed) {
    return null
  }

  const stageLabel = STAGE_LABELS[seed.stage] || seed.stage
  const minApprovers = currentPolicy?.min_approvers ?? 1

  const stageAgent = currentPolicy?.agent_id
    ? agents.find((a) => a.id === currentPolicy.agent_id)
    : undefined

  // Compute current stage cycle start (since stage_entered_at or latest changes_requested in this stage)
  const stageEnteredMs = seed.stage_entered_at
    ? new Date(seed.stage_entered_at).getTime()
    : 0
  const latestChangesReqMs = Math.max(
    0,
    ...approvals
      .filter(
        (a) =>
          a.stage === seed.stage &&
          a.decision === 'changes_requested' &&
          Boolean(a.created_at)
      )
      .map((a) => new Date(a.created_at!).getTime())
      .filter((ms) => Number.isFinite(ms))
  )
  const cycleStartMs = Math.max(stageEnteredMs, latestChangesReqMs)

  const currentCycleRuns = [...runs]
    .filter((r) => {
      if (r.stage !== seed.stage) return false
      if (cycleStartMs && r.created_at) {
        const createdMs = new Date(r.created_at).getTime()
        if (Number.isFinite(createdMs) && createdMs < cycleStartMs - 1000) {
          return false
        }
      }
      return true
    })
    .sort(
      (a, b) =>
        new Date(a.created_at || 0).getTime() -
        new Date(b.created_at || 0).getTime()
    )

  // Compute queue position when seed is queued
  const queuedSeeds = allSeeds
    .filter((s) => s.status === 'queued')
    .sort(
      (a, b) =>
        new Date(a.stage_entered_at || a.created_at || 0).getTime() -
        new Date(b.stage_entered_at || b.created_at || 0).getTime()
    )
  const queueIndex = queuedSeeds.findIndex((s) => s.id === seed.id)
  const queueOrdinal = formatOrdinal(queueIndex >= 0 ? queueIndex + 1 : 1)

  // Compute active agent(s) & elapsed time when seed is running
  const activeStageRuns = (currentCycleRuns.length > 0 ? currentCycleRuns : runs).filter(
    (r) =>
      r.stage === seed.stage &&
      (r.status === 'running' ||
        r.status === 'starting' ||
        r.status === 'queued')
  )
  const activeRun = activeStageRuns[0]
  const runningAgent = activeRun?.agent_id
    ? agents.find((a) => a.id === activeRun.agent_id)
    : stageAgent
  const activeHandles = Array.from(
    new Set(
      activeStageRuns
        .map((r) => {
          const ag = r.agent_id ? agents.find((a) => a.id === r.agent_id) : undefined
          return ag ? `@${ag.handle}` : null
        })
        .filter((h): h is string => Boolean(h))
    )
  )
  const runningAgentLabel =
    activeHandles.length > 1
      ? activeHandles.join(' & ')
      : activeHandles[0] || (runningAgent ? `@${runningAgent.handle}` : 'Stage agent')
  const runningVerb = activeHandles.length > 1 ? 'are' : 'is'
  const runningFinishVerb = activeHandles.length > 1 ? 'finish' : 'finishes'
  const runningElapsed = formatElapsedDuration(
    activeRun?.started_at || seed.stage_entered_at
  )

  // Count approvals in the current stage since stage_entered_at (stale-approval rule)
  // and after the last rejection/changes_requested
  const currentStageApprovals = approvals.filter((a) => {
    if (a.stage !== seed.stage) return false
    if (stageEnteredMs && a.created_at) {
      const createdMs = new Date(a.created_at).getTime()
      if (Number.isFinite(createdMs) && createdMs < stageEnteredMs) {
        return false
      }
    }
    return true
  })
  const activeApprovers = new Set<string>()
  for (const a of currentStageApprovals) {
    if (a.decision === 'changes_requested' || a.decision === 'rejected') {
      activeApprovers.clear()
    } else if (a.decision === 'approved' && a.decided_by) {
      activeApprovers.add(a.decided_by)
    }
  }

  // Check if any succeeded Review run in the current cycle flagged issues (approved === false)
  const latestReviewRun = [...runs]
    .filter((r) => r.stage === 'review' && r.status === 'succeeded')
    .sort(
      (a, b) =>
        new Date(b.created_at || 0).getTime() -
        new Date(a.created_at || 0).getTime()
    )[0]
  const succeededCycleReviewRuns =
    seed.stage === 'review'
      ? currentCycleRuns.filter((r) => r.status === 'succeeded')
      : []
  const reviewRunsToInspect =
    succeededCycleReviewRuns.length > 0
      ? succeededCycleReviewRuns
      : latestReviewRun
        ? [latestReviewRun]
        : []
  const rejectingReviewRuns = reviewRunsToInspect.filter(
    (r) => ((r.output || {}) as Record<string, unknown>).approved === false
  )

  const latestStageRun = [...runs]
    .filter(
      (r) =>
        (r.stage === seed.stage ||
          (seed.stage === 'plan' && r.stage === 'ideate')) &&
        r.status === 'succeeded'
    )
    .sort(
      (a, b) =>
        new Date(b.created_at || 0).getTime() -
        new Date(a.created_at || 0).getTime()
    )[0]
  const latestStageOut = (latestStageRun?.output || {}) as Record<string, unknown>

  const policyLimitWarningMessage = (() => {
    const rawWarn = latestStageOut.policy_limit_warning as
      | Record<string, unknown>
      | undefined
    if (seed.stage === 'ideate') {
      const count = seed.acceptance_criteria.length
      const minC = currentPolicy?.min_acceptance_criteria
      const maxC = currentPolicy?.max_acceptance_criteria
      if (typeof minC === 'number' && count < minC) {
        return `${count} ${count === 1 ? 'criterion' : 'criteria'}, policy minimum is ${minC}`
      }
      if (typeof maxC === 'number' && count > maxC) {
        return `${count} criteria, policy maximum is ${maxC}`
      }
      if (
        rawWarn?.field === 'acceptance_criteria' &&
        typeof rawWarn.message === 'string' &&
        typeof maxC === 'number' &&
        count > maxC
      ) {
        return rawWarn.message
      }
    } else if (seed.stage === 'plan') {
      const count = tasks.length
      const maxT = currentPolicy?.max_tasks
      if (typeof maxT === 'number' && count > maxT) {
        return `${count} tasks, policy maximum is ${maxT}`
      }
      if (
        rawWarn?.field === 'tasks' &&
        typeof rawWarn.message === 'string' &&
        typeof maxT === 'number' &&
        count > maxT
      ) {
        return rawWarn.message
      }
    }
    return null
  })()

  const suggestedFollowUps: SuggestedFollowUpSeed[] = (() => {
    if (seed.stage !== 'plan') return []
    const rawList =
      (Array.isArray(latestStageOut.suggested_follow_ups)
        ? latestStageOut.suggested_follow_ups
        : Array.isArray(
              (latestStageOut.fast_tracked_plan as Record<string, unknown> | undefined)
                ?.suggested_follow_ups
            )
          ? (latestStageOut.fast_tracked_plan as Record<string, unknown>)
              .suggested_follow_ups
          : []) as unknown[]
    return rawList
      .filter(
        (item): item is Record<string, unknown> =>
          Boolean(item && typeof item === 'object' && typeof (item as Record<string, unknown>).title === 'string')
      )
      .map((item) => ({
        title: String(item.title || '').trim(),
        description: String(item.description || '').trim(),
        rationale: item.rationale ? String(item.rationale).trim() : null,
      }))
      .filter((item) => Boolean(item.title))
  })()

  const handleCreateFollowUp = async (item: SuggestedFollowUpSeed, idx: number) => {
    if (creatingFollowUpIdx !== null || createdFollowUps[idx]) return
    setCreatingFollowUpIdx(idx)
    setError(null)
    try {
      const descParts = [item.description || ''].filter(Boolean)
      if (item.rationale) {
        descParts.push(`_Split from #${seed.number}: ${item.rationale}_`)
      }
      const created = await api.seeds.create({
        project_id: seed.project_id,
        title: item.title,
        description: descParts.join('\n\n'),
        autonomy: seed.autonomy,
        tags: seed.tags,
        staged: true,
      })
      await api.seeds.addLink(created.id, {
        target_seed_id: seed.id,
        kind: 'follow_up',
      })
      setCreatedFollowUps((prev) => ({
        ...prev,
        [idx]: { id: created.id, number: created.number },
      }))
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({ queryKey: ['seedLinks', seed.id] }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      setError(errorText(err, 'Failed to create follow-up seed.'))
    } finally {
      setCreatingFollowUpIdx(null)
    }
  }

  const reviewRejectedByAgent =
    seed.stage === 'review' && rejectingReviewRuns.length > 0
  const rejectingReviewerHandles = Array.from(
    new Set(
      rejectingReviewRuns.map((r) => {
        const ag = r.agent_id ? agents.find((a) => a.id === r.agent_id) : undefined
        return ag ? `@${ag.handle}` : '@reviewer'
      })
    )
  )
  const reviewFindings = rejectingReviewRuns.flatMap((r) => {
    const out = (r.output || {}) as Record<string, unknown>
    return Array.isArray(out.findings)
      ? out.findings.filter((f): f is string => typeof f === 'string' && Boolean(f.trim()))
      : []
  })
  const reviewSummaryComment = rejectingReviewRuns
    .map((r) => {
      const out = (r.output || {}) as Record<string, unknown>
      return typeof out.summary_comment === 'string' ? out.summary_comment.trim() : ''
    })
    .filter(Boolean)
    .join(' | ')
  const defaultReviewRemediationComment =
    reviewSummaryComment || reviewFindings.join('; ') || ''
  const returnsToExecuteOnChanges =
    seed.stage === 'review' || seed.stage === 'merge'

  // Second opinion eligibility (Ideate, Plan, Review only; max 3 runs per cycle)
  const isMultiOpinionStage =
    seed.stage === 'ideate' || seed.stage === 'plan' || seed.stage === 'review'
  const nonCancelledCycleRuns = currentCycleRuns.filter(
    (r) => r.status !== 'cancelled'
  )
  const usedCycleAgentIds = new Set(
    nonCancelledCycleRuns
      .filter(
        (r) =>
          r.status === 'queued' ||
          r.status === 'starting' ||
          r.status === 'running' ||
          r.status === 'succeeded'
      )
      .map((r) => r.agent_id)
      .filter((id): id is string => Boolean(id))
  )
  const eligibleSecondOpinionAgents = filterStageAgentsForStage(
    agents.filter((a) => !a.archived_at && a.enabled && a.kind === 'stage'),
    seed.stage
  ).filter((a) => !usedCycleAgentIds.has(a.id))
  const canRequestSecondOpinion =
    isAwaitingApproval &&
    isMultiOpinionStage &&
    canContribute &&
    currentPolicy?.allow_second_opinions !== false &&
    nonCancelledCycleRuns.length < 3 &&
    eligibleSecondOpinionAgents.length > 0

  const handleRequestSecondOpinion = async (agentId: string) => {
    setIsSubmitting(true)
    setError(null)
    setSecondOpinionOpen(false)
    try {
      await api.seeds.requestSecondOpinion(seed.id, { agent_id: agentId })
      await refreshSeedQueries()
    } catch (err) {
      setError(errorText(err, 'Failed to request second opinion.'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const resolvedPrUrl = (() => {
    const statusUrl = safeExternalUrl(pullRequestStatus?.pull_request_url)
    if (statusUrl) {
      return statusUrl
    }
    const directUrl = safeExternalUrl(pullRequestUrl)
    if (directUrl) return directUrl
    for (const r of runs) {
      const out = (r.output || {}) as Record<string, unknown>
      const runPrUrl = safeExternalUrl(
        typeof out.pull_request_url === 'string' ? out.pull_request_url : null
      )
      if (runPrUrl) {
        return runPrUrl
      }
    }
    return null
  })()

  const isPrMergeBlocked =
    seed.stage === 'merge' &&
    pullRequestStatus?.state === 'open' &&
    pullRequestStatus?.ready_to_merge === false

  const handleDecision = async (decision: ApprovalDecision) => {
    const effectiveComment =
      comment.trim() ||
      (decision === 'changes_requested' ? defaultReviewRemediationComment : '')
    if (
      (decision === 'changes_requested' || decision === 'rejected') &&
      !effectiveComment
    ) {
      setError('Please provide a comment explaining your decision.')
      return
    }
    setIsSubmitting(true)
    setError(null)
    try {
      await api.seeds.createApproval(seed.id, {
        decision,
        comment: effectiveComment || null,
      })
      setComment('')
      setActivePrompt(null)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedTasks(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedApprovals(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedActivity(seed.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      await onRefresh()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to submit decision.'
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleUnblockBudget = async () => {
    setIsSubmitting(true)
    setError(null)
    try {
      const currentSpent = parseFloat(seed.spent || '0')
      const currentCap = seed.budget_cap ? parseFloat(seed.budget_cap) : null
      const isCheckpointBlock =
        seed.blocked_reason?.toLowerCase().includes('checkpoint') ?? false

      if (isCheckpointBlock) {
        const prevApproved = parseFloat(
          seed.checkpoint_approved_through || '0'
        )
        const nextThreshold = Math.max(currentSpent, prevApproved) + 25
        await api.seeds.approveCheckpoint(seed.id, nextThreshold)
      } else {
        const nextCap = Math.max(currentSpent, currentCap || 0) + 25
        await api.seeds.update(seed.id, { budget_cap: nextCap })
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedActivity(seed.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      await onRefresh()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to unblock seed.'
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleStartSeed = async () => {
    setIsSubmitting(true)
    setError(null)
    try {
      await api.seeds.start(seed.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedActivity(seed.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      await onRefresh()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to start seed.'
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const refreshSeedQueries = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedDetail(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedActivity(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
      queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
    ])
    await onRefresh()
  }

  const errorText = (err: unknown, fallback: string) =>
    err instanceof ApiError ? err.detail : err instanceof Error ? err.message : fallback

  const handleStopSeed = async () => {
    setIsSubmitting(true)
    setError(null)
    try {
      await api.seeds.stop(seed.id)
      setConfirmStop(false)
      setMessageOpen(false)
      await refreshSeedQueries()
    } catch (err) {
      setError(errorText(err, 'Failed to stop the agent.'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleResumeSeed = async () => {
    setIsSubmitting(true)
    setError(null)
    try {
      await api.seeds.resume(seed.id)
      await refreshSeedQueries()
    } catch (err) {
      setError(errorText(err, 'Failed to resume.'))
    } finally {
      setIsSubmitting(false)
    }
  }

  /** Steer the working agent: an @mention comment it picks up on its next check-in. */
  const handleSendMessage = async () => {
    const text = messageText.trim()
    if (!text) return
    const handle = runningAgent?.handle
    const body = handle && !text.toLowerCase().includes(`@${handle.toLowerCase()}`) ? `@${handle} ${text}` : text
    setIsSubmitting(true)
    setError(null)
    try {
      await api.seeds.createComment(seed.id, { body })
      setMessageText('')
      setMessageOpen(false)
      setMessageNotice(`Sent. ${handle ? `@${handle}` : 'The agent'} will see it within a few seconds.`)
      window.setTimeout(() => setMessageNotice(null), 5000)
      await queryClient.invalidateQueries({ queryKey: queryKeys.seedActivity(seed.id) })
    } catch (err) {
      setError(errorText(err, 'Failed to send message.'))
    } finally {
      setIsSubmitting(false)
    }
  }

  const isErrorTone = isBlocked || isFailed
  const isGateTone = isAwaitingApproval
  const isWorkingTone = isRunning
  const isStagedTone = isStaged

  const containerClasses = isErrorTone
    ? 'bg-rose-50/95 dark:bg-rose-950/90 border-rose-500/35 shadow-sm'
    : isGateTone
      ? 'bg-amber-50/95 dark:bg-amber-950/90 border-amber-500/35 shadow-sm'
      : isWorkingTone
        ? 'bg-sky-50/95 dark:bg-sky-950/90 border-sky-500/35 shadow-sm'
        : isStagedTone
          ? 'bg-indigo-50/95 dark:bg-indigo-950/90 border-indigo-500/35 shadow-sm'
          : 'bg-white/92 dark:bg-zinc-900/92 border-zinc-200/80 dark:border-zinc-700/80 shadow-xs'

  const stickyClasses = !isMergedOrClosed
    ? 'sticky top-0 z-20 dark:ring-1 dark:ring-white/[0.04]'
    : ''

  return (
    <div
      ref={bannerRef}
      className={`p-4 sm:p-5 rounded-2xl border backdrop-blur-xl flex flex-col gap-3.5 ${stickyClasses} ${containerClasses}`}
    >
      {!isMergedOrClosed && (
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-start gap-3">
            <div
              className={`w-9 h-9 rounded-xl flex items-center justify-center shrink-0 mt-0.5 ${
                isErrorTone
                  ? 'bg-rose-500/15 text-rose-600 dark:text-rose-400'
                  : isGateTone
                    ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                    : isWorkingTone
                      ? 'bg-sky-500/15 text-sky-600 dark:text-sky-400'
                      : isStagedTone
                        ? 'bg-indigo-500/15 text-indigo-600 dark:text-indigo-400'
                        : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300'
              }`}
            >
              {isErrorTone ? (
                <AlertTriangle size={18} />
              ) : isGateTone ? (
                <ShieldCheck size={18} />
              ) : isWorkingTone ? (
                <Loader2 size={18} className="animate-spin" />
              ) : isStagedTone ? (
                <Play size={18} />
              ) : isStopped ? (
                <Square size={16} />
              ) : (
                <Clock size={18} />
              )}
            </div>

            <div>
              <div className="flex items-center gap-2 flex-wrap">
                <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
                  {isBlocked || isFailed
                    ? getSeedAttentionReason(seed)
                    : isAwaitingApproval
                      ? `${stageLabel} Stage Gate`
                      : isRunning
                        ? `${runningAgentLabel} ${runningVerb} working · ${runningElapsed}`
                        : isStaged
                          ? `Staged in ${stageLabel} · Ready to Start`
                          : isStopped
                            ? `Stopped in ${stageLabel}`
                            : `Waiting for a runner · ${queueOrdinal} in queue`}
                </h3>

                {isRunning && activeRun && (
                  <ContextWindowBadge
                    contextWindow={resolveRunContextWindow(activeRun)}
                  />
                )}

                {isAwaitingApproval && (
                  <>
                    <span className="px-2 py-0.5 rounded-md text-xs font-mono font-semibold bg-white/80 dark:bg-zinc-900/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-700 dark:text-zinc-300">
                      {activeApprovers.size} / {minApprovers} approvals
                    </span>
                    {currentPolicy?.min_approver_role === 'maintainer' && (
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">
                        · Maintainer required
                      </span>
                    )}
                    {!canApprove &&
                      canContribute &&
                      currentPolicy?.allow_self_approval === false &&
                      currentPolicy?.min_approver_role !== 'maintainer' && (
                        <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                          {seed.created_by === user?.id
                            ? '· Self-approval disabled in Policies'
                            : '· You edited code on this seed (self-approval disabled)'}
                        </span>
                      )}
                  </>
                )}

                {isAwaitingApproval && reviewRejectedByAgent && (
                  <span className="px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-500/15 border border-amber-500/30 text-amber-700 dark:text-amber-300">
                    Changes requested by {rejectingReviewerHandles.join(', ')}
                  </span>
                )}

                {isAwaitingApproval && policyLimitWarningMessage && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-500/15 border border-amber-500/30 text-amber-800 dark:text-amber-300">
                    <AlertTriangle size={11} className="shrink-0" />
                    <span>{policyLimitWarningMessage}</span>
                  </span>
                )}

                {pullRequestStatus &&
                  pullRequestStatus.state === 'open' &&
                  (seed.stage === 'review' || seed.stage === 'merge') && (
                    <>
                      {pullRequestStatus.ci_status === 'pending' && (
                        <span className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md text-xs font-semibold bg-sky-500/15 border border-sky-500/30 text-sky-700 dark:text-sky-300">
                          <Loader2 size={11} className="animate-spin shrink-0" />
                          <span>
                            {pullRequestStatus.ci_summary
                              ? `CI Running (${pullRequestStatus.ci_summary})`
                              : 'CI Pipeline Running'}
                          </span>
                        </span>
                      )}
                      {pullRequestStatus.ci_status === 'failing' && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-rose-500/15 border border-rose-500/30 text-rose-700 dark:text-rose-300">
                          <XCircle size={11} className="shrink-0" />
                          <span>
                            {pullRequestStatus.ci_summary
                              ? `CI Failing (${pullRequestStatus.ci_summary})`
                              : 'CI Pipeline Failing'}
                          </span>
                        </span>
                      )}
                      {pullRequestStatus.review_status === 'changes_requested' && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-rose-500/15 border border-rose-500/30 text-rose-700 dark:text-rose-300">
                          <AlertCircle size={11} className="shrink-0" />
                          <span>PR Changes Requested</span>
                        </span>
                      )}
                      {pullRequestStatus.review_status === 'review_required' && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-amber-500/15 border border-amber-500/30 text-amber-700 dark:text-amber-300">
                          <Clock size={11} className="shrink-0" />
                          <span>Awaiting PR Approval</span>
                        </span>
                      )}
                      {pullRequestStatus.ready_to_merge && (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-500/15 border border-emerald-500/30 text-emerald-700 dark:text-emerald-300">
                          <Check size={11} className="shrink-0" />
                          <span>
                            {pullRequestStatus.ci_status === 'passing'
                              ? `CI Passing · Ready to Merge`
                              : 'Ready to Merge'}
                          </span>
                        </span>
                      )}
                    </>
                  )}

                {isQueued && stageAgent && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-mono bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-700 dark:text-zinc-300">
                    <Bot size={12} className="text-teal-500" />@{stageAgent.handle}
                  </span>
                )}
              </div>

              <p className="text-xs sm:text-sm text-zinc-600 dark:text-zinc-400 mt-0.5">
                {isBlocked
                  ? `Spent ${formatUsd(seed.spent)}${
                      seed.budget_cap ? ` of ${formatUsd(seed.budget_cap)} cap` : ''
                    }. Raise the budget limit or approve the next spend checkpoint to resume.`
                  : isFailed
                    ? 'The latest stage run encountered an error. Inspect the Runs tab or click Retry in the header when ready.'
                    : isAwaitingApproval
                      ? seed.stage === 'merge'
                        ? isPrMergeBlocked && pullRequestStatus?.block_reason
                          ? `${pullRequestStatus.block_reason} Merge will unlock automatically as soon as PR checks and approvals pass.`
                          : 'PR is ready to merge. Approve to finalize this seed and merge into the default branch, or Request Changes to send back to Execute.'
                        : reviewRejectedByAgent
                          ? 'A reviewer flagged blocking issues that should be fixed before merge. Click Request Changes to return this seed to Execute (@coder).'
                          : `Approve to advance this seed from ${stageLabel} to the next stage.`
                      : isRunning
                        ? currentPolicy?.requires_approval === false
                          ? `Advances automatically when ${runningAgentLabel} ${runningFinishVerb}.`
                          : `Approval opens when ${runningAgentLabel} ${runningFinishVerb}.`
                        : isStaged
                          ? `This seed is staged and will not be picked up by agents until someone clicks Start Work.`
                          : isStopped
                            ? `The agent was stopped. Resume restarts the ${stageLabel} stage; the branch and anything already pushed are kept, and so are your messages.`
                            : currentPolicy?.requires_approval === false
                            ? `Advances automatically when ${stageAgent ? `@${stageAgent.handle}` : '@architect'} finishes.`
                            : `Approval opens when ${stageAgent ? `@${stageAgent.handle}` : '@architect'} finishes.`}
              </p>
            </div>
          </div>

          <div className="flex items-center gap-2 flex-wrap shrink-0">
            {isStaged && canContribute && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleStartSeed}
                isDisabled={isSubmitting}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <Play size={14} />
                <span>{isSubmitting ? 'Starting...' : 'Start Work'}</span>
              </Button>
            )}
            {isRunning && canContribute && !confirmStop && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setMessageOpen((v) => !v)}
                isDisabled={isSubmitting}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
              >
                <MessageSquare size={14} />
                <span>Message</span>
              </Button>
            )}
            {(isRunning || isQueued) && canContribute && !confirmStop && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setConfirmStop(true)}
                isDisabled={isSubmitting}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
              >
                <Square size={12} />
                <span>Stop</span>
              </Button>
            )}
            {(isRunning || isQueued) && canContribute && confirmStop && (
              <div className="flex items-center gap-2 text-xs">
                <span className="text-zinc-700 dark:text-zinc-300 font-medium">
                  Stop {isRunning ? runningAgentLabel : 'this seed'}?
                </span>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => void handleStopSeed()}
                  isDisabled={isSubmitting}
                  className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer bg-rose-600 hover:bg-rose-500 text-white"
                >
                  {isSubmitting ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
                  <span>Stop</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setConfirmStop(false)}
                  isDisabled={isSubmitting}
                  className="rounded-xl text-xs font-semibold cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
                >
                  Cancel
                </Button>
              </div>
            )}
            {isStopped && canContribute && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() => void handleResumeSeed()}
                isDisabled={isSubmitting}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                {isSubmitting ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
                <span>Resume</span>
              </Button>
            )}
            {resolvedPrUrl && (seed.stage === 'review' || seed.stage === 'merge') && (
              <div className="inline-flex items-center gap-1">
                {onOpenPullRequest ? (
                  <button
                    type="button"
                    onClick={onOpenPullRequest}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-white/90 dark:bg-zinc-900/90 hover:bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/30 transition cursor-pointer"
                    title="Inspect Pull Request, CI checks, comments, and diff inside GitSeed"
                  >
                    <GitPullRequest size={13} />
                    <span>
                      {pullRequestStatus?.number
                        ? `PR #${pullRequestStatus.number}`
                        : 'Pull Request'}
                    </span>
                  </button>
                ) : null}
                <a
                  href={resolvedPrUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open Pull Request on Git host in a new tab"
                  className={
                    onOpenPullRequest
                      ? 'inline-flex items-center justify-center p-1.5 rounded-xl text-xs bg-white/90 dark:bg-zinc-900/90 hover:bg-teal-500/10 text-zinc-500 hover:text-teal-600 dark:hover:text-teal-400 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer'
                      : 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-white/90 dark:bg-zinc-900/90 hover:bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/30 transition cursor-pointer'
                  }
                >
                  {!onOpenPullRequest && (
                    <>
                      <GitPullRequest size={13} />
                      <span>View Pull Request</span>
                    </>
                  )}
                  <ExternalLink size={12} />
                </a>
              </div>
            )}
            {isBlocked && canContribute && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleUnblockBudget}
                isDisabled={isSubmitting}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <DollarSign size={14} />
                <span>Add $25 &amp; Unblock</span>
              </Button>
            )}

            {isAwaitingApproval && canApprove && (
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={() =>
                  setActivePrompt(
                    activePrompt === 'approved' ? null : 'approved'
                  )
                }
                isDisabled={isSubmitting || isPrMergeBlocked}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                <Check size={14} />
                <span>
                  {seed.stage === 'merge'
                    ? isPrMergeBlocked
                      ? 'Merge Locked (PR Not Ready)'
                      : 'Approve & Merge'
                    : `Approve ${stageLabel}`}
                </span>
              </Button>
            )}

            {canRequestSecondOpinion && (
              <div ref={secondOpinionMenuRef} className="relative">
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() => setSecondOpinionOpen((v) => !v)}
                  isDisabled={isSubmitting}
                  className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
                >
                  <Users size={13} />
                  <span>Second opinion</span>
                  <ChevronDown
                    size={12}
                    className={`transition-transform ${secondOpinionOpen ? 'rotate-180' : ''}`}
                  />
                </Button>
                {secondOpinionOpen && (
                  <div className="absolute right-0 mt-1.5 w-72 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl z-30 p-2 flex flex-col gap-1.5">
                    <div className="px-2 py-1 border-b border-zinc-100 dark:border-zinc-800">
                      <div className="flex items-center justify-between gap-2">
                        <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                          Run another agent on {stageLabel}
                        </span>
                        <span className="text-xs font-mono text-zinc-500">
                          {nonCancelledCycleRuns.length}/3 runs
                        </span>
                      </div>
                    </div>
                    <div className="max-h-56 overflow-y-auto flex flex-col gap-1">
                      {eligibleSecondOpinionAgents.map((ag) => (
                        <button
                          key={ag.id}
                          type="button"
                          onClick={() => void handleRequestSecondOpinion(ag.id)}
                          className="w-full px-2.5 py-2 rounded-lg text-left hover:bg-teal-500/10 transition flex items-center justify-between gap-2 cursor-pointer group"
                        >
                          <div className="min-w-0">
                            <div className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-teal-600 dark:group-hover:text-teal-400 truncate">
                              @{ag.handle}
                            </div>
                            <div className="text-xs font-mono text-zinc-500 dark:text-zinc-400 truncate">
                              {ag.model} · {ag.runtime}
                            </div>
                          </div>
                        </button>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            )}

            {isAwaitingApproval && effectiveCanDecide && (
              <>
                <Button
                  type="button"
                  variant={reviewRejectedByAgent ? 'primary' : 'secondary'}
                  size="sm"
                  onClick={() => {
                    const next =
                      activePrompt === 'changes_requested'
                        ? null
                        : 'changes_requested'
                    setActivePrompt(next)
                    if (
                      next === 'changes_requested' &&
                      !comment.trim() &&
                      defaultReviewRemediationComment
                    ) {
                      setComment(
                        `Fix the issues identified during review: ${defaultReviewRemediationComment}`
                      )
                    }
                  }}
                  isDisabled={isSubmitting}
                  className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
                >
                  <MessageSquareWarning size={14} />
                  <span>Request Changes</span>
                </Button>
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  onClick={() =>
                    setActivePrompt(
                      activePrompt === 'rejected' ? null : 'rejected'
                    )
                  }
                  isDisabled={isSubmitting}
                  className="rounded-xl text-xs font-semibold text-rose-600 dark:text-rose-400 flex items-center gap-1.5 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
                >
                  <XCircle size={14} />
                  <span>Reject &amp; close</span>
                </Button>
              </>
            )}
          </div>
        </div>
      )}

      {messageOpen && isRunning && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void handleSendMessage()
          }}
          className="flex items-center gap-1.5 pl-3 pr-1.5 py-1.5 rounded-xl bg-white/90 dark:bg-zinc-900/90 border border-zinc-200 dark:border-zinc-700/80 focus-within:border-teal-500 transition"
        >
          <input
            autoFocus
            value={messageText}
            onChange={(e) => setMessageText(e.target.value)}
            onKeyDown={(e) => e.key === 'Escape' && setMessageOpen(false)}
            placeholder={`Tell ${runningAgentLabel} something while it works…`}
            className="flex-1 min-w-0 bg-transparent text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none"
          />
          <button
            type="submit"
            disabled={!messageText.trim() || isSubmitting}
            title="Send"
            className="h-8 w-8 rounded-lg bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white flex items-center justify-center cursor-pointer shrink-0"
          >
            {isSubmitting ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
          </button>
        </form>
      )}

      {messageNotice && (
        <div className="text-xs text-emerald-700 dark:text-emerald-300 flex items-center gap-1.5">
          <Check size={13} className="shrink-0" />
          {messageNotice}
        </div>
      )}

      {error && (
        <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs text-rose-600 dark:text-rose-400 flex items-center gap-2">
          <AlertCircle size={14} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {isAwaitingApproval && seed.stage === 'plan' && suggestedFollowUps.length > 0 && (
        <div className="p-3 rounded-xl bg-white/85 dark:bg-zinc-900/85 border border-amber-500/30 flex flex-col gap-2">
          <div className="flex items-center justify-between gap-2">
            <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
              <GitBranch size={13} className="text-teal-600 dark:text-teal-400 shrink-0" />
              <span>
                Suggested Follow-Up Seeds ({suggestedFollowUps.length})
              </span>
            </span>
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              Split out to keep this seed within {currentPolicy?.max_tasks ?? 5} tasks
            </span>
          </div>
          <div className="flex flex-col gap-1.5">
            {suggestedFollowUps.map((item, idx) => {
              const created = createdFollowUps[idx]
              const isCreating = creatingFollowUpIdx === idx
              return (
                <div
                  key={`${idx}-${item.title}`}
                  className="px-3 py-2 rounded-lg bg-zinc-50/90 dark:bg-zinc-800/60 border border-zinc-200/80 dark:border-zinc-700/70 flex flex-col sm:flex-row sm:items-center justify-between gap-2 text-xs"
                >
                  <div className="min-w-0">
                    <div className="font-semibold text-zinc-900 dark:text-zinc-100">
                      {item.title}
                    </div>
                    {(item.rationale || item.description) && (
                      <div className="text-zinc-600 dark:text-zinc-400 mt-0.5 line-clamp-2">
                        {item.rationale || item.description}
                      </div>
                    )}
                  </div>
                  {canContribute && (
                    <div className="shrink-0">
                      {created ? (
                        <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-emerald-500/10 border border-emerald-500/25 text-emerald-700 dark:text-emerald-300 font-semibold">
                          <Check size={12} />
                          <span>Created #{created.number} (staged)</span>
                        </span>
                      ) : (
                        <button
                          type="button"
                          disabled={creatingFollowUpIdx !== null}
                          onClick={() => void handleCreateFollowUp(item, idx)}
                          className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white font-semibold transition cursor-pointer"
                        >
                          {isCreating ? (
                            <Loader2 size={12} className="animate-spin" />
                          ) : (
                            <Plus size={12} />
                          )}
                          <span>Split into Follow-Up Seed</span>
                        </button>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {activePrompt && isAwaitingApproval && (
        <div className="pt-3 border-t border-zinc-200/70 dark:border-zinc-700/70 flex flex-col gap-2.5">
          <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
            {activePrompt === 'approved'
              ? seed.stage === 'merge'
                ? 'Confirm final approval to merge and finalize this seed (optional approval note):'
                : `Confirm approval to advance ${stageLabel} to the next stage (optional approval note):`
              : activePrompt === 'changes_requested'
                ? returnsToExecuteOnChanges
                  ? 'Describe the fixes needed (creates a remediation task and returns this seed to Execute):'
                  : 'Describe the changes needed before advancing:'
                : 'Provide a reason for rejecting and closing this seed:'}
          </label>
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1 flex">
              <input
                type="text"
                value={comment}
                onChange={(e) => setComment(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') {
                    e.preventDefault()
                    handleDecision(activePrompt)
                  }
                }}
                placeholder={
                  activePrompt === 'approved'
                    ? 'Optional approval note (or click Confirm to approve)...'
                    : activePrompt === 'changes_requested'
                      ? 'e.g. Add test coverage for token bucket expiration edge cases...'
                      : 'e.g. Superseded by gateway-level rate limiting...'
                }
                autoFocus
                className="w-full h-10 px-3.5 pr-9 text-sm rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
              />
              <VoiceDictationButton
                className="absolute right-2 top-1/2 -translate-y-1/2"
                onTranscript={(spoken) =>
                  setComment((prev) =>
                    prev.trim() ? `${prev.trim()} ${spoken}` : spoken
                  )
                }
              />
            </div>
            <div className="flex items-center gap-2 shrink-0">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => {
                  setActivePrompt(null)
                  setComment('')
                }}
                className="rounded-xl text-xs font-semibold cursor-pointer"
              >
                Cancel
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                isDisabled={
                  isSubmitting ||
                  (activePrompt !== 'approved' &&
                    !comment.trim() &&
                    !(
                      activePrompt === 'changes_requested' &&
                      defaultReviewRemediationComment
                    ))
                }
                onClick={() => handleDecision(activePrompt)}
                className={`rounded-xl text-xs font-semibold cursor-pointer ${
                  activePrompt === 'rejected'
                    ? 'bg-rose-600 hover:bg-rose-500 text-white'
                    : ''
                }`}
              >
                {activePrompt === 'approved'
                  ? seed.stage === 'merge'
                    ? 'Confirm Approve & Merge'
                    : `Confirm Approve ${stageLabel}`
                  : activePrompt === 'changes_requested'
                    ? returnsToExecuteOnChanges
                      ? 'Send Back to Execute'
                      : 'Submit Changes Request'
                    : 'Confirm Reject & Close'}
              </Button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
