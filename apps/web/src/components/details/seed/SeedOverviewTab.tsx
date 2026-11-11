import React, { useEffect, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  AlertTriangle,
  ArrowDown,
  ArrowUp,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Code2,
  Columns2,
  Edit3,
  Eye,
  FileText,
  GripVertical,
  History,
  Layers,
  ListChecks,
  Loader2,
  Plus,
  RotateCcw,
  ShieldCheck,
  X,
  XCircle,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { MarkdownContent } from '../../common/MarkdownContent'
import { Modal } from '../../common/Modal'
import { useData } from '../../../context/DataContext'
import { formatDurationMs, formatTokens, formatUsd } from '../../../utils/format'
import { AUTONOMY_LABELS, STAGE_LABELS } from '../../../utils/seeds'
import { SeedDiscussionCard } from './SeedDiscussionCard'
import { SeedMetadataSidebar } from './SeedMetadataSidebar'
import { SeedAttachmentsCard } from './SeedAttachmentsCard'
import { SeedRelatedCard } from './SeedRelatedCard'
import type {
  ActivityEvent,
  Approval,
  Project,
  ProjectRepository,
  Run,
  Seed,
  SeedAutonomy,
  SeedChangesResponse,
  SeedComment,
  SeedStage,
} from '../../../types'

export interface SeedOverviewTabProps {
  seed: Seed
  project: Project | null
  primaryRepo: ProjectRepository | null
  repos?: ProjectRepository[]
  runs?: Run[]
  approvals?: Approval[]
  changes?: SeedChangesResponse | null
  seedEvents?: ActivityEvent[]
  ideateAgentHandle?: string
  maxAcceptanceCriteria?: number
  comments: SeedComment[]
  canContribute: boolean
  canManageProject: boolean
  onEditSeed: () => void
  onNewRelatedSeed?: () => void
  onRefresh: () => Promise<void>
  onOpenPullRequest?: () => void
  discussionExpandTrigger?: number
}

/** Editor height that fits the text (12–32 lines) instead of a cramped fixed 7 rows. */
const editorRows = (text: string): number => {
  const lines = (text || '').split('\n').reduce((n, ln) => n + Math.max(1, Math.ceil(ln.length / 100)), 0)
  return Math.min(32, Math.max(12, lines + 2))
}

const isLongMarkdown = (text: string | null | undefined): boolean => {
  if (!text) return false
  return text.length > 500 || text.split('\n').length >= 10
}

/** Card chrome: a soft sky ring while an agent is writing into the section. */
const sectionCardClass = (working: boolean) =>
  `p-5 sm:p-6 min-w-0 overflow-hidden rounded-2xl border bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-4 transition-[box-shadow,border-color] duration-500 ${
    working
      ? 'border-sky-500/40 ring-2 ring-sky-500/15 dark:ring-sky-400/15'
      : 'border-zinc-200 dark:border-zinc-700/80 ring-0 ring-transparent'
  }`

/**
 * After a run finishes, briefly say who updated the section (only if its content changed).
 * Returns the handle to show, or null.
 */
function useJustUpdated(working: boolean, content: string, handle: string): string | null {
  const [updatedBy, setUpdatedBy] = useState<string | null>(null)
  const prev = useRef({ working, content, handle })
  useEffect(() => {
    const was = prev.current
    if (was.working && !working && was.content !== content) {
      setUpdatedBy(was.handle)
      const t = window.setTimeout(() => setUpdatedBy(null), 6000)
      prev.current = { working, content, handle }
      return () => window.clearTimeout(t)
    }
    // Keep the pre-run snapshot while working so the comparison spans the whole run
    if (!working || !was.working) prev.current = { working, content, handle }
    else prev.current = { ...was, handle }
  }, [working, content, handle])
  return updatedBy
}

/** Header status: "@architect is drafting…" while working, "Updated by @architect just now" after. */
const SectionAgentStatus: React.FC<{ working: boolean; handle: string; verb: string; updatedBy: string | null }> = ({
  working,
  handle,
  verb,
  updatedBy,
}) => {
  if (working)
    return (
      <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-sky-700 dark:text-sky-300">
        <span className="relative flex w-1.5 h-1.5">
          <span className="absolute inline-flex w-full h-full rounded-full bg-sky-400 opacity-60 animate-ping" />
          <span className="relative inline-flex w-1.5 h-1.5 rounded-full bg-sky-500" />
        </span>
        {handle} is {verb}…
      </span>
    )
  if (updatedBy)
    return (
      <span className="inline-flex items-center gap-1 text-[11px] font-medium text-emerald-700 dark:text-emerald-300 animate-fadeIn">
        <Check size={11} />
        Updated by {updatedBy} just now
      </span>
    )
  return null
}

/** Quiet heads-up when editing a section the agent is still writing. */
const AgentEditingNote: React.FC<{ handle: string }> = ({ handle }) => (
  <div className="text-xs text-sky-800 dark:text-sky-200 bg-sky-500/10 border border-sky-500/25 rounded-xl px-3 py-2">
    {handle} is still working on this section; your edits may be overwritten when it finishes.
  </div>
)

export const SeedOverviewTab: React.FC<SeedOverviewTabProps> = ({
  seed,
  project,
  primaryRepo,
  repos = [],
  runs = [],
  approvals = [],
  changes = null,
  seedEvents = [],
  ideateAgentHandle = '@ideator',
  maxAcceptanceCriteria = 6,
  comments,
  canContribute,
  canManageProject,
  onNewRelatedSeed,
  onRefresh,
  onOpenPullRequest,
  discussionExpandTrigger,
}) => {
  const queryClient = useQueryClient()
  const { users, agents } = useData()

  const isSeedOpen = seed.status !== 'merged' && seed.status !== 'closed'
  const canEditSpec = canContribute && isSeedOpen && seed.stage === 'ideate'
  const canEditPlan =
    canContribute &&
    isSeedOpen &&
    (seed.stage === 'ideate' || seed.stage === 'plan')

  const [isEditingSpec, setIsEditingSpec] = useState(false)
  const [specDraft, setSpecDraft] = useState(seed.specification || '')
  const [isSavingSpec, setIsSavingSpec] = useState(false)
  const [isEditingCriteria, setIsEditingCriteria] = useState(false)
  const [criteriaDraft, setCriteriaDraft] = useState<string[]>(
    seed.acceptance_criteria
  )
  const [isSavingCriteria, setIsSavingCriteria] = useState(false)
  const [draggedCriterionIdx, setDraggedCriterionIdx] = useState<number | null>(
    null
  )
  const [isEditingPlan, setIsEditingPlan] = useState(false)
  const [planDraft, setPlanDraft] = useState(seed.plan || '')
  const [isSavingPlan, setIsSavingPlan] = useState(false)
  const [showRawSpec, setShowRawSpec] = useState(false)
  const [showRawPlan, setShowRawPlan] = useState(false)
  const [isSpecSectionCollapsed, setIsSpecSectionCollapsed] = useState(false)
  const [isTechSpecCollapsed, setIsTechSpecCollapsed] = useState(false)
  const [isCriteriaCollapsed, setIsCriteriaCollapsed] = useState(false)
  const [isPlanCollapsed, setIsPlanCollapsed] = useState(false)
  const [isReviewCollapsed, setIsReviewCollapsed] = useState(false)
  const [isSpecExpandedFull, setIsSpecExpandedFull] = useState(false)
  const [isPlanExpandedFull, setIsPlanExpandedFull] = useState(false)
  const [showOriginalSubmission, setShowOriginalSubmission] = useState(false)
  const [isRestoringOriginal, setIsRestoringOriginal] = useState(false)
  const [selectingRunId, setSelectingRunId] = useState<string | null>(null)
  const [autonomyNotice, setAutonomyNotice] = useState<string | null>(null)
  const [compareStage, setCompareStage] = useState<SeedStage | null>(null)
  const [compareRunAId, setCompareRunAId] = useState<string>('')
  const [compareRunBId, setCompareRunBId] = useState<string>('')

  const specIsLong = isLongMarkdown(seed.specification)
  const planIsLong = isLongMarkdown(seed.plan)

  // All non-cancelled runs per stage (persists across stage approvals for side-by-side comparison)
  const getAllStageRuns = (targetStage: SeedStage): Run[] =>
    [...runs]
      .filter((r) => r.stage === targetStage && r.status !== 'cancelled')
      .sort(
        (a, b) =>
          new Date(a.created_at || 0).getTime() -
          new Date(b.created_at || 0).getTime()
      )

  const ideateAllRuns = getAllStageRuns('ideate')
  const planAllRuns = getAllStageRuns('plan')
  const executeAllRuns = getAllStageRuns('execute')
  const reviewAllRuns = getAllStageRuns('review')

  const openCompareModal = (targetStage: SeedStage) => {
    const stageRuns = getAllStageRuns(targetStage)
    if (stageRuns.length < 2) return
    const runA = stageRuns[stageRuns.length - 2]
    const runB = stageRuns[stageRuns.length - 1]
    setCompareStage(targetStage)
    setCompareRunAId(runA.id)
    setCompareRunBId(runB.id)
  }

  // Compute runs belonging to the latest stage cycle for a given stage
  const getStageCycleRuns = (targetStage: SeedStage): Run[] => {
    const stageRuns = [...runs]
      .filter((r) => r.stage === targetStage && r.status !== 'cancelled')
      .sort(
        (a, b) =>
          new Date(a.created_at || 0).getTime() -
          new Date(b.created_at || 0).getTime()
      )
    if (stageRuns.length === 0) return []

    const stageEnteredMs =
      seed.stage === targetStage && seed.stage_entered_at
        ? new Date(seed.stage_entered_at).getTime()
        : 0
    const latestChangesReqMs = Math.max(
      0,
      ...approvals
        .filter(
          (a) =>
            a.stage === targetStage &&
            a.decision === 'changes_requested' &&
            Boolean(a.created_at)
        )
        .map((a) => new Date(a.created_at!).getTime())
        .filter((ms) => Number.isFinite(ms))
    )
    const cycleStartMs = Math.max(stageEnteredMs, latestChangesReqMs)
    if (!cycleStartMs) return stageRuns

    const filtered = stageRuns.filter((r) => {
      if (!r.created_at) return true
      const createdMs = new Date(r.created_at).getTime()
      return !Number.isFinite(createdMs) || createdMs >= cycleStartMs - 1000
    })
    return filtered.length > 0 ? filtered : stageRuns
  }

  const ideateCycleRuns = getStageCycleRuns('ideate')
  const planCycleRuns = getStageCycleRuns('plan')
  const reviewCycleRuns = getStageCycleRuns('review')

  // While Ideate / Plan runs, ring the section the agent is writing into
  const activeRun = runs.find(
    (r) => r.stage === seed.stage && (r.status === 'running' || r.status === 'starting')
  )
  const activeAgent = activeRun?.agent_id ? agents.find((a) => a.id === activeRun.agent_id) : undefined
  const workingHandle = activeAgent ? `@${activeAgent.handle}` : ideateAgentHandle
  const isSpecWorking = seed.status === 'running' && seed.stage === 'ideate'
  const isPlanWorking = seed.status === 'running' && seed.stage === 'plan'
  const isReviewWorking = seed.status === 'running' && seed.stage === 'review'
  const specUpdatedBy = useJustUpdated(
    isSpecWorking,
    `${seed.title}\n${seed.description || ''}\n${seed.specification || ''}\n${seed.acceptance_criteria.join('\n')}`,
    workingHandle
  )
  const planUpdatedBy = useJustUpdated(isPlanWorking, seed.plan || '', workingHandle)

  // Resolve immutable original submission from seed.created activity or earliest ideate run
  const createdEvent = seedEvents.find((ev) => ev.type === 'seed.created')
  const earliestIdeateRun = [...runs]
    .filter((r) => r.stage === 'ideate' && r.output)
    .sort((a, b) => a.attempt - b.attempt)[0]
  const ideateRunOutput = (earliestIdeateRun?.output || {}) as Record<string, unknown>

  const originalTitle =
    (typeof createdEvent?.data?.title === 'string' && createdEvent.data.title.trim()) ||
    (typeof ideateRunOutput.previous_title === 'string' && ideateRunOutput.previous_title.trim()) ||
    null
  const originalDescription =
    (typeof createdEvent?.data?.description === 'string' &&
      createdEvent.data.description.trim()) ||
    (typeof ideateRunOutput.previous_description === 'string' &&
      ideateRunOutput.previous_description.trim()) ||
    null
  const rawOriginalCriteria =
    (Array.isArray(createdEvent?.data?.acceptance_criteria)
      ? createdEvent?.data?.acceptance_criteria
      : null) ??
    (Array.isArray(ideateRunOutput.previous_acceptance_criteria)
      ? ideateRunOutput.previous_acceptance_criteria
      : [])
  const originalCriteria = (rawOriginalCriteria as unknown[])
    .map((item) => String(item || '').trim())
    .filter(Boolean)

  const wasTitleRefined = Boolean(originalTitle && originalTitle !== seed.title.trim())
  const wasDescriptionRefined = Boolean(
    originalDescription && originalDescription !== (seed.description || '').trim()
  )
  const wasCriteriaRefined =
    originalCriteria.length > 0 &&
    JSON.stringify(originalCriteria) !== JSON.stringify(seed.acceptance_criteria)
  const hasOriginalRefinementDiff =
    wasTitleRefined || wasDescriptionRefined || wasCriteriaRefined
  const hasUserDraft = Boolean(
    originalTitle || originalDescription || originalCriteria.length > 0
  )

  // Determine which Ideate run / "Your draft" is currently selected
  const succeededIdeateRuns = ideateCycleRuns.filter(
    (r) => r.status === 'succeeded'
  )
  const explicitlySelectedIdeateRun = succeededIdeateRuns.find(
    (r) => ((r.output || {}) as Record<string, unknown>).selected === true
  )
  const allIdeateRunsExplicitlyUnselected =
    succeededIdeateRuns.length > 0 &&
    succeededIdeateRuns.every(
      (r) => ((r.output || {}) as Record<string, unknown>).selected === false
    )
  const isYourDraftSelected =
    allIdeateRunsExplicitlyUnselected ||
    (succeededIdeateRuns.length > 0 &&
      !explicitlySelectedIdeateRun &&
      !seed.specification)
  const activeIdeateRunId = isYourDraftSelected
    ? 'original'
    : explicitlySelectedIdeateRun?.id ||
      succeededIdeateRuns
        .slice()
        .reverse()
        .find((r) => {
          const out = (r.output || {}) as Record<string, unknown>
          return (
            typeof out.specification === 'string' &&
            out.specification.trim() === (seed.specification || '').trim()
          )
        })?.id ||
      succeededIdeateRuns[succeededIdeateRuns.length - 1]?.id ||
      null

  // Determine which Plan run is currently selected
  const succeededPlanRuns = planCycleRuns.filter((r) => r.status === 'succeeded')
  const explicitlySelectedPlanRun = succeededPlanRuns.find(
    (r) => ((r.output || {}) as Record<string, unknown>).selected === true
  )
  const activePlanRunId =
    explicitlySelectedPlanRun?.id ||
    succeededPlanRuns
      .slice()
      .reverse()
      .find((r) => {
        const out = (r.output || {}) as Record<string, unknown>
        return (
          typeof out.plan === 'string' &&
          out.plan.trim() === (seed.plan || '').trim()
        )
      })?.id ||
    succeededPlanRuns[succeededPlanRuns.length - 1]?.id ||
    null

  const handleSelectDraftRun = async (
    runIdOrOriginal: string,
    stage: 'ideate' | 'plan'
  ) => {
    if (selectingRunId) return
    if (stage === 'ideate' && !canEditSpec) {
      if (runIdOrOriginal === 'original') {
        setShowOriginalSubmission((prev) => !prev)
      }
      return
    }
    if (stage === 'plan' && !canEditPlan) return

    setSelectingRunId(runIdOrOriginal)
    try {
      const updated = await api.seeds.selectRun(seed.id, runIdOrOriginal)
      if (stage === 'ideate') {
        setSpecDraft(updated.specification || '')
        setCriteriaDraft(
          updated.acceptance_criteria.length > 0
            ? [...updated.acceptance_criteria]
            : ['']
        )
        setShowOriginalSubmission(runIdOrOriginal === 'original')
      } else if (stage === 'plan') {
        setPlanDraft(updated.plan || '')
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedTasks(seed.id),
        }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedActivity(seed.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
      ])
      await onRefresh()
    } catch (err) {
      console.error('Failed to select draft run:', err)
    } finally {
      setSelectingRunId(null)
    }
  }

  const handleRestoreOriginalSubmission = async () => {
    if (!canEditSpec || isRestoringOriginal) return
    setIsRestoringOriginal(true)
    try {
      await handleSelectDraftRun('original', 'ideate')
    } finally {
      setIsRestoringOriginal(false)
    }
  }

  const resolveActorName = (actorType: string, actorId: string | null) => {
    if (!actorId) return 'System'
    if (actorType === 'agent') {
      const a = agents.find((ag) => ag.id === actorId)
      return a ? `@${a.handle}` : actorId
    }
    const u = users.find((usr) => usr.id === actorId)
    return u ? u.preferred_name || u.name || u.email : actorId
  }

  const resolveRunAgentHandle = (run: Run): string => {
    if (run.agent_id) {
      const a = agents.find((ag) => ag.id === run.agent_id)
      if (a) return `@${a.handle}`
    }
    return '@agent'
  }

  const hasActiveTextSelection = (): boolean => {
    if (typeof window === 'undefined') return false
    const sel = window.getSelection()
    return Boolean(sel && sel.toString().trim().length > 0)
  }

  const handleStartEditSpec = () => {
    if (!canEditSpec) return
    setSpecDraft(seed.specification || '')
    setIsTechSpecCollapsed(false)
    setIsEditingSpec(true)
  }

  const handleSaveSpec = async () => {
    if (!canEditSpec || isSavingSpec) return
    setIsSavingSpec(true)
    try {
      await api.seeds.update(seed.id, {
        specification: specDraft.trim(),
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
      ])
      setIsEditingSpec(false)
      await onRefresh()
    } catch (err) {
      console.error('Failed to save specification:', err)
    } finally {
      setIsSavingSpec(false)
    }
  }

  const handleStartEditCriteria = () => {
    if (!canEditSpec) return
    setCriteriaDraft(
      seed.acceptance_criteria.length > 0
        ? [...seed.acceptance_criteria]
        : ['']
    )
    setIsCriteriaCollapsed(false)
    setIsEditingCriteria(true)
  }

  const handleMoveCriterion = (index: number, direction: -1 | 1) => {
    setCriteriaDraft((prev) => {
      const targetIndex = index + direction
      if (targetIndex < 0 || targetIndex >= prev.length) return prev
      const next = [...prev]
      const [moved] = next.splice(index, 1)
      next.splice(targetIndex, 0, moved)
      return next
    })
  }

  const handleSaveCriteria = async () => {
    if (!canEditSpec || isSavingCriteria) return
    setIsSavingCriteria(true)
    try {
      const cleaned = criteriaDraft.map((c) => c.trim()).filter(Boolean)
      await api.seeds.update(seed.id, {
        acceptance_criteria: cleaned,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
      ])
      setIsEditingCriteria(false)
      await onRefresh()
    } catch (err) {
      console.error('Failed to save acceptance criteria:', err)
    } finally {
      setIsSavingCriteria(false)
    }
  }

  const handleStartEditPlan = () => {
    if (!canEditPlan) return
    setPlanDraft(seed.plan || '')
    setIsPlanCollapsed(false)
    setIsEditingPlan(true)
  }

  const handleSavePlan = async () => {
    setIsSavingPlan(true)
    try {
      await api.seeds.update(seed.id, {
        plan: planDraft.trim() || null,
      })
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
      ])
      setIsEditingPlan(false)
      await onRefresh()
    } catch (err) {
      console.error('Failed to save plan:', err)
    } finally {
      setIsSavingPlan(false)
    }
  }

  const handleAutonomyChange = async (nextAutonomy: SeedAutonomy) => {
    if (nextAutonomy === seed.autonomy || !canContribute) return
    const previousAutonomy = seed.autonomy
    try {
      await api.seeds.update(seed.id, { autonomy: nextAutonomy })
      setAutonomyNotice(
        `Changed from ${AUTONOMY_LABELS[previousAutonomy]} to ${AUTONOMY_LABELS[nextAutonomy]}.`
      )
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(seed.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      await onRefresh()
    } catch (err) {
      console.error('Failed to update seed autonomy:', err)
    }
  }

  const showIdeateDraftPills =
    ideateCycleRuns.length > 1 ||
    (ideateCycleRuns.length === 1 && hasUserDraft && hasOriginalRefinementDiff)
  const showPlanDraftPills = planCycleRuns.length > 1
  const showReviewSection =
    reviewCycleRuns.length > 0 &&
    (seed.stage === 'review' ||
      seed.stage === 'merge' ||
      seed.status === 'merged')
  const anyReviewHasBlockingFindings = reviewCycleRuns.some(
    (r) =>
      r.status === 'succeeded' &&
      ((r.output || {}) as Record<string, unknown>).approved === false
  )

  return (
    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
      {/* Left Column: Spec, Criteria, Plan, Review Verdicts, Comments */}
      <div className="lg:col-span-8 min-w-0 flex flex-col gap-6">
        {/* Summary & Specification */}
        <Card className={sectionCardClass(isSpecWorking)}>
          <div
            className={`flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 ${
              isSpecSectionCollapsed
                ? ''
                : 'pb-3 border-b border-zinc-100 dark:border-zinc-800'
            }`}
          >
            <button
              type="button"
              onClick={() => setIsSpecSectionCollapsed((prev) => !prev)}
              className="flex items-center gap-2.5 text-left cursor-pointer group"
            >
              <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
                <FileText size={16} />
              </div>
              <div>
                <div className="flex items-center gap-1.5">
                  <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
                    Specification &amp; Requirements
                  </h3>
                  <span className="text-zinc-400 group-hover:text-zinc-700 dark:group-hover:text-zinc-200 transition">
                    {isSpecSectionCollapsed ? (
                      <ChevronRight size={16} />
                    ) : (
                      <ChevronDown size={16} />
                    )}
                  </span>
                </div>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  Problem summary, technical architecture, and scope constraints
                </p>
              </div>
            </button>

            <div className="flex items-center gap-2 flex-wrap">
              {showIdeateDraftPills && (
                <div
                  className="inline-flex items-center gap-1 p-1 rounded-xl bg-zinc-100/90 dark:bg-zinc-800/80 border border-zinc-200/80 dark:border-zinc-700/80"
                  title={
                    canEditSpec
                      ? 'Pick a draft as the starting point, then edit before approving'
                      : 'Ideate drafts'
                  }
                >
                  {ideateCycleRuns.map((r) => {
                    const handle = resolveRunAgentHandle(r)
                    const isSucceeded = r.status === 'succeeded'
                    const isActive =
                      r.status === 'running' ||
                      r.status === 'starting' ||
                      r.status === 'queued'
                    const isFailed =
                      r.status === 'failed' || r.status === 'timed_out'
                    const isSelected =
                      isSucceeded && activeIdeateRunId === r.id
                    const isSelecting = selectingRunId === r.id

                    if (isFailed) {
                      return (
                        <span
                          key={r.id}
                          title={r.error || `${handle} run failed`}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-mono font-medium bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20"
                        >
                          <XCircle size={11} />
                          <span>{handle}</span>
                          <span className="text-[10px]">Failed</span>
                        </span>
                      )
                    }

                    if (isActive) {
                      return (
                        <span
                          key={r.id}
                          className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-mono font-medium bg-sky-500/10 text-sky-700 dark:text-sky-300 border border-sky-500/20"
                        >
                          <Loader2 size={11} className="animate-spin" />
                          <span>{handle}</span>
                        </span>
                      )
                    }

                    return (
                      <button
                        key={r.id}
                        type="button"
                        disabled={!canEditSpec || Boolean(selectingRunId)}
                        onClick={() => void handleSelectDraftRun(r.id, 'ideate')}
                        className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-mono font-semibold transition ${
                          isSelected
                            ? 'bg-white dark:bg-zinc-900 text-teal-600 dark:text-teal-400 shadow-2xs border border-teal-500/30'
                            : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100'
                        } ${canEditSpec ? 'cursor-pointer' : 'cursor-default'}`}
                      >
                        {isSelecting ? (
                          <Loader2 size={11} className="animate-spin" />
                        ) : isSelected ? (
                          <Check size={11} className="text-teal-500" />
                        ) : null}
                        <span>{handle}</span>
                      </button>
                    )
                  })}

                  {hasUserDraft && (
                    <button
                      type="button"
                      disabled={Boolean(selectingRunId)}
                      onClick={() =>
                        void handleSelectDraftRun('original', 'ideate')
                      }
                      title={
                        canEditSpec
                          ? 'Use your draft from before Ideate as the starting point'
                          : 'Toggle viewing your draft from before Ideate'
                      }
                      className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold transition cursor-pointer ${
                        activeIdeateRunId === 'original' ||
                        (!canEditSpec && showOriginalSubmission)
                          ? 'bg-white dark:bg-zinc-900 text-teal-600 dark:text-teal-400 shadow-2xs border border-teal-500/30'
                          : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100'
                      }`}
                    >
                      {selectingRunId === 'original' ? (
                        <Loader2 size={11} className="animate-spin" />
                      ) : activeIdeateRunId === 'original' ? (
                        <Check size={11} className="text-teal-500" />
                      ) : null}
                      <span>Your draft</span>
                    </button>
                  )}
                </div>
              )}

              {ideateAllRuns.length >= 2 && (
                <button
                  type="button"
                  onClick={() => openCompareModal('ideate')}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-semibold bg-teal-500/10 hover:bg-teal-500/20 text-teal-700 dark:text-teal-300 border border-teal-500/25 transition cursor-pointer"
                  title="Compare Ideate drafts side-by-side without changing the active draft"
                >
                  <Columns2 size={13} />
                  <span>Compare Drafts ({ideateAllRuns.length})</span>
                </button>
              )}

              <SectionAgentStatus
                working={isSpecWorking}
                handle={workingHandle}
                verb="drafting"
                updatedBy={specUpdatedBy}
              />
            </div>
          </div>

          {isSpecWorking && (isEditingSpec || isEditingCriteria) && <AgentEditingNote handle={workingHandle} />}

          {!isSpecSectionCollapsed && (
            <>
              {(seed.description || hasOriginalRefinementDiff) && (
                <div className="flex flex-col gap-1.5 min-w-0">
                  <div className="flex items-center justify-between gap-2 flex-wrap">
                    <span className="text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                      Summary
                    </span>
                    {hasOriginalRefinementDiff && !showIdeateDraftPills && (
                      <button
                        type="button"
                        onClick={() => setShowOriginalSubmission((prev) => !prev)}
                        className="inline-flex items-center gap-1.5 px-2 py-0.5 rounded-lg text-[11px] font-medium bg-zinc-100/80 dark:bg-zinc-800/80 text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 border border-zinc-200/70 dark:border-zinc-700/70 transition cursor-pointer"
                      >
                        <History size={11} />
                        <span>
                          Refined by {ideateAgentHandle} ·{' '}
                          {showOriginalSubmission ? 'Hide your draft' : 'View your draft'}
                        </span>
                      </button>
                    )}
                  </div>
                  {seed.description && (
                    <MarkdownContent content={seed.description} className="text-sm" />
                  )}
                  {hasOriginalRefinementDiff && showOriginalSubmission && (
                    <div className="mt-1 p-3.5 rounded-xl bg-zinc-50/90 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/80 flex flex-col gap-2.5 text-xs">
                      <div className="flex items-center justify-between gap-2">
                        <span className="font-semibold text-zinc-600 dark:text-zinc-300 flex items-center gap-1.5">
                          <History size={12} className="text-teal-600 dark:text-teal-400" />
                          <span>Your Draft (Before Ideate)</span>
                        </span>
                        {canEditSpec && (
                          <button
                            type="button"
                            disabled={isRestoringOriginal}
                            onClick={handleRestoreOriginalSubmission}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold text-teal-600 dark:text-teal-400 hover:bg-teal-500/10 transition cursor-pointer disabled:opacity-50"
                          >
                            <RotateCcw size={11} />
                            <span>
                              {isRestoringOriginal ? 'Restoring…' : 'Use your draft'}
                            </span>
                          </button>
                        )}
                      </div>
                      {wasTitleRefined && originalTitle && (
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                            Initial Title
                          </span>
                          <p className="text-zinc-700 dark:text-zinc-200 font-medium">
                            {originalTitle}
                          </p>
                        </div>
                      )}
                      {originalDescription && (
                        <div className="flex flex-col gap-0.5">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                            Initial Summary
                          </span>
                          <MarkdownContent content={originalDescription} className="text-zinc-700 dark:text-zinc-300" />
                        </div>
                      )}
                      {wasCriteriaRefined && originalCriteria.length > 0 && (
                        <div className="flex flex-col gap-1">
                          <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                            Initial Acceptance Criteria ({originalCriteria.length})
                          </span>
                          <ul className="list-disc list-inside space-y-0.5 text-zinc-700 dark:text-zinc-300">
                            {originalCriteria.map((item, idx) => (
                              <li key={`${idx}-${item}`}>{item}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )}

              <div className="flex flex-col gap-1.5 min-w-0">
                <div className="flex items-center justify-between gap-2">
                  <button
                    type="button"
                    onClick={() => setIsTechSpecCollapsed((prev) => !prev)}
                    className="inline-flex items-center gap-1 text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition cursor-pointer"
                  >
                    {isTechSpecCollapsed && !isEditingSpec ? (
                      <ChevronRight size={13} />
                    ) : (
                      <ChevronDown size={13} />
                    )}
                    <span>Technical Specification</span>
                  </button>
                  <div className="flex items-center gap-1.5">
                    {seed.specification && !isTechSpecCollapsed && !isEditingSpec && (
                      <button
                        type="button"
                        onClick={() => setShowRawSpec((prev) => !prev)}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                        title={
                          showRawSpec
                            ? 'Switch to rendered Markdown'
                            : 'Switch to raw Markdown'
                        }
                      >
                        {showRawSpec ? <Eye size={12} /> : <Code2 size={12} />}
                        <span>{showRawSpec ? 'Rendered' : 'Raw'}</span>
                      </button>
                    )}
                    {canEditSpec && !isEditingSpec && (
                      <button
                        type="button"
                        onClick={handleStartEditSpec}
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                      >
                        <Edit3 size={12} />
                        <span>Edit</span>
                      </button>
                    )}
                  </div>
                </div>
                {(!isTechSpecCollapsed || (isEditingSpec && canEditSpec)) &&
                  (isEditingSpec && canEditSpec ? (
                    <div className="flex flex-col gap-2.5">
                      <textarea
                        rows={editorRows(specDraft)}
                        value={specDraft}
                        onChange={(e) => setSpecDraft(e.target.value)}
                        placeholder="Describe technical architecture, requirements, and scope constraints (Markdown supported)..."
                        autoFocus
                        className="w-full p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-xs sm:text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 resize-y"
                      />
                      <div className="flex items-center justify-end gap-2">
                        <Button
                          type="button"
                          variant="secondary"
                          size="sm"
                          onClick={() => setIsEditingSpec(false)}
                          className="rounded-xl text-xs font-semibold cursor-pointer"
                        >
                          Cancel
                        </Button>
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          isDisabled={
                            isSavingSpec ||
                            specDraft.trim() === (seed.specification || '').trim()
                          }
                          onClick={handleSaveSpec}
                          className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
                        >
                          <Check size={13} />
                          <span>{isSavingSpec ? 'Saving...' : 'Save'}</span>
                        </Button>
                      </div>
                    </div>
                  ) : seed.specification ? (
                    <div
                      onClick={(e) => {
                        if (!canEditSpec || hasActiveTextSelection()) return
                        const target = e.target as HTMLElement
                        if (target.closest('a, button')) return
                        handleStartEditSpec()
                      }}
                      title={canEditSpec ? 'Click to edit specification' : undefined}
                      className={`flex flex-col min-w-0 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 overflow-hidden ${
                        canEditSpec
                          ? 'cursor-text hover:border-teal-500/40 transition-colors'
                          : ''
                      }`}
                    >
                      <div
                        className={`relative p-4 min-w-0 overflow-hidden ${
                          specIsLong && !isSpecExpandedFull ? 'max-h-60' : ''
                        }`}
                      >
                        {showRawSpec ? (
                          <pre className="text-xs sm:text-sm font-mono text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap wrap-break-word overflow-x-auto max-w-full leading-relaxed">
                            {seed.specification}
                          </pre>
                        ) : (
                          <MarkdownContent content={seed.specification} />
                        )}
                        {specIsLong && !isSpecExpandedFull && (
                          <div
                            aria-hidden="true"
                            className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-linear-to-t from-zinc-50 via-zinc-50/90 to-transparent dark:from-zinc-900 dark:via-zinc-900/90 dark:to-transparent"
                          />
                        )}
                      </div>
                      {specIsLong && (
                        <button
                          type="button"
                          onClick={() => setIsSpecExpandedFull((prev) => !prev)}
                          className="w-full py-2 px-4 border-t border-zinc-200/80 dark:border-zinc-700/70 bg-zinc-100/70 dark:bg-zinc-800/70 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 text-xs font-semibold text-teal-600 dark:text-teal-400 flex items-center justify-center gap-1.5 transition cursor-pointer"
                        >
                          {isSpecExpandedFull ? (
                            <>
                              <ChevronUp size={14} />
                              <span>Show less</span>
                            </>
                          ) : (
                            <>
                              <ChevronDown size={14} />
                              <span>Show full specification</span>
                            </>
                          )}
                        </button>
                      )}
                    </div>
                  ) : (
                    <p
                      onClick={() => {
                        if (canEditSpec) handleStartEditSpec()
                      }}
                      className={`text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 italic ${
                        canEditSpec
                          ? 'cursor-pointer hover:text-teal-600 dark:hover:text-teal-400 transition'
                          : ''
                      }`}
                    >
                      No technical specification yet. {ideateAgentHandle} will draft one
                      during Ideate{canEditSpec ? ', or click here to write it.' : '.'}
                    </p>
                  ))}
              </div>

              {/* Acceptance Criteria */}
              <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800 flex flex-col gap-2.5 min-w-0">
                <div className="flex items-center justify-between">
                  <button
                    type="button"
                    onClick={() => setIsCriteriaCollapsed((prev) => !prev)}
                    className="text-xs font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500 hover:text-zinc-700 dark:hover:text-zinc-300 transition flex items-center gap-1.5 cursor-pointer"
                  >
                    {isCriteriaCollapsed && !isEditingCriteria ? (
                      <ChevronRight size={13} />
                    ) : (
                      <ChevronDown size={13} />
                    )}
                    <ListChecks size={13} />
                    <span>
                      Acceptance Criteria ({seed.acceptance_criteria.length})
                    </span>
                  </button>
                  {canEditSpec && !isEditingCriteria && (
                    <button
                      type="button"
                      onClick={handleStartEditCriteria}
                      className="inline-flex items-center gap-1 px-2 py-0.5 rounded-lg text-[11px] font-medium text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                    >
                      <Edit3 size={12} />
                      <span>Edit</span>
                    </button>
                  )}
                </div>

                {(!isCriteriaCollapsed || (isEditingCriteria && canEditSpec)) &&
                  (isEditingCriteria && canEditSpec ? (
                    <div className="flex flex-col gap-2 min-w-0">
                      {criteriaDraft.map((criterion, idx) => (
                        <div
                          key={idx}
                          draggable
                          onDragStart={() => setDraggedCriterionIdx(idx)}
                          onDragOver={(e) => e.preventDefault()}
                          onDrop={() => {
                            if (
                              draggedCriterionIdx === null ||
                              draggedCriterionIdx === idx
                            ) {
                              setDraggedCriterionIdx(null)
                              return
                            }
                            setCriteriaDraft((prev) => {
                              const next = [...prev]
                              const [moved] = next.splice(draggedCriterionIdx, 1)
                              next.splice(idx, 0, moved)
                              return next
                            })
                            setDraggedCriterionIdx(null)
                          }}
                          onDragEnd={() => setDraggedCriterionIdx(null)}
                          className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border transition ${
                            draggedCriterionIdx === idx
                              ? 'border-teal-500 opacity-60'
                              : 'border-zinc-200 dark:border-zinc-700/80'
                          }`}
                        >
                          <span
                            className="text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 cursor-grab active:cursor-grabbing shrink-0"
                            title="Drag to reorder"
                          >
                            <GripVertical size={14} />
                          </span>
                          <div className="flex items-center gap-0.5 shrink-0">
                            <button
                              type="button"
                              disabled={idx === 0}
                              onClick={() => handleMoveCriterion(idx, -1)}
                              title="Move up"
                              className="p-1 rounded-md text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-700/60 disabled:opacity-30 cursor-pointer disabled:cursor-default"
                            >
                              <ArrowUp size={12} />
                            </button>
                            <button
                              type="button"
                              disabled={idx === criteriaDraft.length - 1}
                              onClick={() => handleMoveCriterion(idx, 1)}
                              title="Move down"
                              className="p-1 rounded-md text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-700/60 disabled:opacity-30 cursor-pointer disabled:cursor-default"
                            >
                              <ArrowDown size={12} />
                            </button>
                          </div>
                          <input
                            type="text"
                            value={criterion}
                            onChange={(e) => {
                              const val = e.target.value
                              setCriteriaDraft((prev) =>
                                prev.map((item, i) => (i === idx ? val : item))
                              )
                            }}
                            onKeyDown={(e) => {
                              if (e.key === 'Enter') {
                                e.preventDefault()
                                setCriteriaDraft((prev) => {
                                  const next = [...prev]
                                  next.splice(idx + 1, 0, '')
                                  return next
                                })
                              }
                            }}
                            placeholder={`Criterion ${idx + 1}...`}
                            className="flex-1 min-w-0 bg-transparent text-xs sm:text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none py-1"
                          />
                          <button
                            type="button"
                            onClick={() =>
                              setCriteriaDraft((prev) =>
                                prev.filter((_, i) => i !== idx)
                              )
                            }
                            title="Remove criterion"
                            className="p-1 rounded-md text-zinc-400 hover:text-rose-500 hover:bg-rose-500/10 transition cursor-pointer shrink-0"
                          >
                            <X size={13} />
                          </button>
                        </div>
                      ))}

                      {criteriaDraft.filter((c) => c.trim()).length > maxAcceptanceCriteria && (
                        <div className="px-3 py-2 rounded-xl bg-amber-500/10 border border-amber-500/25 text-xs text-amber-800 dark:text-amber-300 flex items-center gap-2">
                          <AlertTriangle size={13} className="shrink-0 text-amber-500" />
                          <span>
                            {criteriaDraft.filter((c) => c.trim()).length} criteria (stage policy maximum for agents is {maxAcceptanceCriteria}; people can still save more).
                          </span>
                        </div>
                      )}

                      <div className="flex items-center justify-between gap-2 pt-1">
                        <button
                          type="button"
                          onClick={() =>
                            setCriteriaDraft((prev) => [...prev, ''])
                          }
                          className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-xs font-semibold text-teal-600 dark:text-teal-400 hover:bg-teal-500/10 transition cursor-pointer"
                        >
                          <Plus size={13} />
                          <span>Add criterion</span>
                        </button>
                        <div className="flex items-center gap-2">
                          <Button
                            type="button"
                            variant="secondary"
                            size="sm"
                            onClick={() => setIsEditingCriteria(false)}
                            className="rounded-xl text-xs font-semibold cursor-pointer"
                          >
                            Cancel
                          </Button>
                          <Button
                            type="button"
                            variant="primary"
                            size="sm"
                            isDisabled={
                              isSavingCriteria ||
                              JSON.stringify(
                                criteriaDraft.map((c) => c.trim()).filter(Boolean)
                              ) === JSON.stringify(seed.acceptance_criteria)
                            }
                            onClick={handleSaveCriteria}
                            className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
                          >
                            <Check size={13} />
                            <span>{isSavingCriteria ? 'Saving...' : 'Save'}</span>
                          </Button>
                        </div>
                      </div>
                    </div>
                  ) : seed.acceptance_criteria.length === 0 ? (
                    <p
                      onClick={() => {
                        if (canEditSpec) handleStartEditCriteria()
                      }}
                      className={`text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 italic ${
                        canEditSpec
                          ? 'cursor-pointer hover:text-teal-600 dark:hover:text-teal-400 transition'
                          : ''
                      }`}
                    >
                      No acceptance criteria defined yet
                      {canEditSpec ? '. Click to add criteria.' : '.'}
                    </p>
                  ) : (
                    <div
                      onClick={(e) => {
                        if (!canEditSpec || hasActiveTextSelection()) return
                        const target = e.target as HTMLElement
                        if (target.closest('a, button')) return
                        handleStartEditCriteria()
                      }}
                      title={
                        canEditSpec
                          ? 'Click to edit or reorder acceptance criteria'
                          : undefined
                      }
                      className="flex flex-col gap-1.5 min-w-0"
                    >
                      {seed.acceptance_criteria.map((criterion, idx) => (
                        <div
                          key={`${idx}-${criterion}`}
                          className={`px-3.5 py-2.5 min-w-0 overflow-hidden rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex items-start gap-2.5 text-xs sm:text-sm text-zinc-800 dark:text-zinc-200 ${
                            canEditSpec
                              ? 'cursor-text hover:border-teal-500/40 transition-colors'
                              : ''
                          }`}
                        >
                          <CheckCircle2
                            size={15}
                            className={`shrink-0 mt-0.5 ${
                              seed.status === 'merged'
                                ? 'text-emerald-500'
                                : 'text-teal-600 dark:text-teal-400'
                            }`}
                          />
                          <div className="min-w-0 flex-1 wrap-break-word">
                            <MarkdownContent content={criterion} />
                          </div>
                        </div>
                      ))}
                    </div>
                  ))}
              </div>
            </>
          )}
        </Card>

        {/* Implementation Plan (shown once the seed reaches Plan or has a plan) */}
        {(seed.stage !== 'ideate' || Boolean(seed.plan)) && (
          <Card className={sectionCardClass(isPlanWorking)}>
            <div
              className={`flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 ${
                isPlanCollapsed && !isEditingPlan
                  ? ''
                  : 'pb-3 border-b border-zinc-100 dark:border-zinc-800'
              }`}
            >
              <button
                type="button"
                onClick={() => setIsPlanCollapsed((prev) => !prev)}
                className="flex items-center gap-2.5 text-left cursor-pointer group"
              >
                <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
                  <Layers size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
                      Implementation Plan
                    </h3>
                    <span className="text-zinc-400 group-hover:text-zinc-700 dark:group-hover:text-zinc-200 transition">
                      {isPlanCollapsed && !isEditingPlan ? (
                        <ChevronRight size={16} />
                      ) : (
                        <ChevronDown size={16} />
                      )}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">
                    Step-by-step execution blueprint produced during the Plan stage
                  </p>
                </div>
              </button>
              <div className="flex items-center gap-2 flex-wrap">
                {showPlanDraftPills && (
                  <div
                    className="inline-flex items-center gap-1 p-1 rounded-xl bg-zinc-100/90 dark:bg-zinc-800/80 border border-zinc-200/80 dark:border-zinc-700/80"
                    title={
                      canEditPlan
                        ? 'Pick a plan draft as the starting point, then edit before approving'
                        : 'Plan drafts'
                    }
                  >
                    {planCycleRuns.map((r) => {
                      const handle = resolveRunAgentHandle(r)
                      const isSucceeded = r.status === 'succeeded'
                      const isActive =
                        r.status === 'running' ||
                        r.status === 'starting' ||
                        r.status === 'queued'
                      const isFailed =
                        r.status === 'failed' || r.status === 'timed_out'
                      const isSelected =
                        isSucceeded && activePlanRunId === r.id
                      const isSelecting = selectingRunId === r.id

                      if (isFailed) {
                        return (
                          <span
                            key={r.id}
                            title={r.error || `${handle} run failed`}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-mono font-medium bg-rose-500/10 text-rose-600 dark:text-rose-400 border border-rose-500/20"
                          >
                            <XCircle size={11} />
                            <span>{handle}</span>
                            <span className="text-[10px]">Failed</span>
                          </span>
                        )
                      }

                      if (isActive) {
                        return (
                          <span
                            key={r.id}
                            className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-mono font-medium bg-sky-500/10 text-sky-700 dark:text-sky-300 border border-sky-500/20"
                          >
                            <Loader2 size={11} className="animate-spin" />
                            <span>{handle}</span>
                          </span>
                        )
                      }

                      return (
                        <button
                          key={r.id}
                          type="button"
                          disabled={!canEditPlan || Boolean(selectingRunId)}
                          onClick={() => void handleSelectDraftRun(r.id, 'plan')}
                          className={`inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-mono font-semibold transition ${
                            isSelected
                              ? 'bg-white dark:bg-zinc-900 text-teal-600 dark:text-teal-400 shadow-2xs border border-teal-500/30'
                              : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100'
                          } ${canEditPlan ? 'cursor-pointer' : 'cursor-default'}`}
                        >
                          {isSelecting ? (
                            <Loader2 size={11} className="animate-spin" />
                          ) : isSelected ? (
                            <Check size={11} className="text-teal-500" />
                          ) : null}
                          <span>{handle}</span>
                        </button>
                      )
                    })}
                  </div>
                )}

                {planAllRuns.length >= 2 && (
                  <button
                    type="button"
                    onClick={() => openCompareModal('plan')}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-semibold bg-teal-500/10 hover:bg-teal-500/20 text-teal-700 dark:text-teal-300 border border-teal-500/25 transition cursor-pointer"
                    title="Compare Plan drafts side-by-side without changing the active plan"
                  >
                    <Columns2 size={13} />
                    <span>Compare Drafts ({planAllRuns.length})</span>
                  </button>
                )}

                {executeAllRuns.length >= 2 && (
                  <button
                    type="button"
                    onClick={() => openCompareModal('execute')}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/80 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-zinc-700 transition cursor-pointer"
                    title="Compare Execute runs side-by-side"
                  >
                    <Columns2 size={13} />
                    <span>Compare Execute ({executeAllRuns.length})</span>
                  </button>
                )}

                <SectionAgentStatus working={isPlanWorking} handle={workingHandle} verb="planning" updatedBy={planUpdatedBy} />
                {seed.plan && !isEditingPlan && !isPlanCollapsed && (
                  <button
                    type="button"
                    onClick={() => setShowRawPlan((prev) => !prev)}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-xl text-xs font-medium text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                    title={
                      showRawPlan
                        ? 'Switch to rendered Markdown'
                        : 'Switch to raw Markdown'
                    }
                  >
                    {showRawPlan ? <Eye size={13} /> : <Code2 size={13} />}
                    <span>{showRawPlan ? 'Rendered' : 'Raw'}</span>
                  </button>
                )}
                {canEditPlan && !isEditingPlan && (
                  <button
                    type="button"
                    onClick={handleStartEditPlan}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-xl text-xs font-medium text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                  >
                    <Edit3 size={13} />
                    <span>Edit</span>
                  </button>
                )}
              </div>
            </div>

            {isPlanWorking && isEditingPlan && <AgentEditingNote handle={workingHandle} />}

            {(!isPlanCollapsed || (isEditingPlan && canEditPlan)) &&
              (isEditingPlan && canEditPlan ? (
                <div className="flex flex-col gap-3">
                  <textarea
                    rows={editorRows(planDraft)}
                    value={planDraft}
                    onChange={(e) => setPlanDraft(e.target.value)}
                    placeholder="Outline files to modify, test strategy, and rollout sequence..."
                    autoFocus
                    className="w-full p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 resize-y"
                  />
                  <div className="flex items-center justify-end gap-2">
                    <Button
                      type="button"
                      variant="secondary"
                      size="sm"
                      onClick={() => setIsEditingPlan(false)}
                      className="rounded-xl text-xs font-semibold cursor-pointer"
                    >
                      Cancel
                    </Button>
                    <Button
                      type="button"
                      variant="primary"
                      size="sm"
                      isDisabled={
                        isSavingPlan || planDraft.trim() === (seed.plan || '').trim()
                      }
                      onClick={handleSavePlan}
                      className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
                    >
                      <Check size={13} />
                      <span>{isSavingPlan ? 'Saving...' : 'Save Plan'}</span>
                    </Button>
                  </div>
                </div>
              ) : seed.plan ? (
                <div
                  onClick={(e) => {
                    if (!canEditPlan || hasActiveTextSelection()) return
                    const target = e.target as HTMLElement
                    if (target.closest('a, button')) return
                    handleStartEditPlan()
                  }}
                  title={canEditPlan ? 'Click to edit implementation plan' : undefined}
                  className={`flex flex-col min-w-0 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 overflow-hidden ${
                    canEditPlan
                      ? 'cursor-text hover:border-teal-500/40 transition-colors'
                      : ''
                  }`}
                >
                  <div
                    className={`relative p-4 min-w-0 overflow-hidden ${
                      planIsLong && !isPlanExpandedFull ? 'max-h-60' : ''
                    }`}
                  >
                    {showRawPlan ? (
                      <pre className="text-xs sm:text-sm font-mono text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap wrap-break-word overflow-x-auto max-w-full leading-relaxed">
                        {seed.plan}
                      </pre>
                    ) : (
                      <MarkdownContent content={seed.plan} />
                    )}
                    {planIsLong && !isPlanExpandedFull && (
                      <div
                        aria-hidden="true"
                        className="pointer-events-none absolute inset-x-0 bottom-0 h-16 bg-linear-to-t from-zinc-50 via-zinc-50/90 to-transparent dark:from-zinc-900 dark:via-zinc-900/90 dark:to-transparent"
                      />
                    )}
                  </div>
                  {planIsLong && (
                    <button
                      type="button"
                      onClick={() => setIsPlanExpandedFull((prev) => !prev)}
                      className="w-full py-2 px-4 border-t border-zinc-200/80 dark:border-zinc-700/70 bg-zinc-100/70 dark:bg-zinc-800/70 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 text-xs font-semibold text-teal-600 dark:text-teal-400 flex items-center justify-center gap-1.5 transition cursor-pointer"
                    >
                      {isPlanExpandedFull ? (
                        <>
                          <ChevronUp size={14} />
                          <span>Show less</span>
                        </>
                      ) : (
                        <>
                          <ChevronDown size={14} />
                          <span>Show full plan</span>
                        </>
                      )}
                    </button>
                  )}
                </div>
              ) : (
                <p
                  onClick={() => {
                    if (canEditPlan) handleStartEditPlan()
                  }}
                  className={`text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 italic ${
                    canEditPlan
                      ? 'cursor-pointer hover:text-teal-600 dark:hover:text-teal-400 transition'
                      : ''
                  }`}
                >
                  The Plan stage agent will generate the implementation blueprint here
                  {canEditPlan ? ', or click to write it.' : '.'}
                </p>
              ))}
          </Card>
        )}

        {/* Code Review Verdicts (all review opinions shown together without cherry-picking) */}
        {showReviewSection && (
          <Card className={sectionCardClass(isReviewWorking)}>
            <div
              className={`flex items-center justify-between gap-2 ${
                isReviewCollapsed
                  ? ''
                  : 'pb-3 border-b border-zinc-100 dark:border-zinc-800'
              }`}
            >
              <button
                type="button"
                onClick={() => setIsReviewCollapsed((prev) => !prev)}
                className="flex items-center gap-2.5 text-left cursor-pointer group"
              >
                <div
                  className={`w-8 h-8 rounded-xl flex items-center justify-center shrink-0 ${
                    anyReviewHasBlockingFindings
                      ? 'bg-amber-500/15 text-amber-600 dark:text-amber-400'
                      : 'bg-teal-500/10 text-teal-600 dark:text-teal-400'
                  }`}
                >
                  <ShieldCheck size={16} />
                </div>
                <div>
                  <div className="flex items-center gap-1.5">
                    <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
                      Code Review Verdicts ({reviewCycleRuns.length})
                    </h3>
                    <span className="text-zinc-400 group-hover:text-zinc-700 dark:group-hover:text-zinc-200 transition">
                      {isReviewCollapsed ? (
                        <ChevronRight size={16} />
                      ) : (
                        <ChevronDown size={16} />
                      )}
                    </span>
                  </div>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400">
                    All reviewer opinions are shown together and recorded in the approval snapshot
                  </p>
                </div>
              </button>

              <div className="flex items-center gap-2 flex-wrap">
                {reviewAllRuns.length >= 2 && (
                  <button
                    type="button"
                    onClick={() => openCompareModal('review')}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-xs font-semibold bg-teal-500/10 hover:bg-teal-500/20 text-teal-700 dark:text-teal-300 border border-teal-500/25 transition cursor-pointer"
                    title="Compare Review verdicts side-by-side"
                  >
                    <Columns2 size={13} />
                    <span>Compare Drafts ({reviewAllRuns.length})</span>
                  </button>
                )}
                {anyReviewHasBlockingFindings && (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-amber-500/15 border border-amber-500/30 text-amber-700 dark:text-amber-300">
                    <AlertTriangle size={12} />
                    <span>Blocking findings reported</span>
                  </span>
                )}
              </div>
            </div>

            {!isReviewCollapsed && (
              <div className="flex flex-col gap-3">
                {reviewCycleRuns.map((r) => {
                  const ag = r.agent_id
                    ? agents.find((a) => a.id === r.agent_id)
                    : undefined
                  const handle = ag ? `@${ag.handle}` : '@reviewer'
                  const out = (r.output || {}) as Record<string, unknown>
                  const isSucceeded = r.status === 'succeeded'
                  const isActive =
                    r.status === 'running' ||
                    r.status === 'starting' ||
                    r.status === 'queued'
                  const isFailed =
                    r.status === 'failed' || r.status === 'timed_out'
                  const approved = isSucceeded ? out.approved !== false : null
                  const summaryComment =
                    typeof out.summary_comment === 'string'
                      ? out.summary_comment.trim()
                      : ''
                  const findings = Array.isArray(out.findings)
                    ? out.findings.filter(
                        (f): f is string =>
                          typeof f === 'string' && Boolean(f.trim())
                      )
                    : []

                  return (
                    <div
                      key={r.id}
                      className={`p-4 rounded-xl border flex flex-col gap-2.5 ${
                        approved === false
                          ? 'bg-amber-50/60 dark:bg-amber-950/30 border-amber-500/30'
                          : isFailed
                            ? 'bg-rose-50/60 dark:bg-rose-950/30 border-rose-500/25'
                            : 'bg-zinc-50/80 dark:bg-zinc-800/40 border-zinc-200 dark:border-zinc-700/80'
                      }`}
                    >
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2">
                          <span className="text-xs font-mono font-bold text-zinc-900 dark:text-zinc-100">
                            {handle}
                          </span>
                          {ag && (
                            <span className="text-[11px] font-mono text-zinc-500 dark:text-zinc-400">
                              {ag.model} · {ag.runtime}
                            </span>
                          )}
                        </div>

                        {isActive ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-sky-500/15 text-sky-700 dark:text-sky-300 border border-sky-500/25">
                            <Loader2 size={11} className="animate-spin" />
                            <span>Reviewing…</span>
                          </span>
                        ) : isFailed ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-rose-500/15 text-rose-700 dark:text-rose-300 border border-rose-500/25">
                            <XCircle size={11} />
                            <span>Failed</span>
                          </span>
                        ) : approved ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/25">
                            <Check size={11} />
                            <span>Approved</span>
                          </span>
                        ) : (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-amber-500/15 text-amber-800 dark:text-amber-300 border border-amber-500/30">
                            <AlertCircle size={11} />
                            <span>Changes Requested</span>
                          </span>
                        )}
                      </div>

                      {isFailed && r.error && (
                        <p className="text-xs text-rose-600 dark:text-rose-400 font-mono">
                          {r.error}
                        </p>
                      )}

                      {summaryComment && (
                        <div className="text-xs sm:text-sm text-zinc-800 dark:text-zinc-200">
                          <MarkdownContent content={summaryComment} />
                        </div>
                      )}

                      {findings.length > 0 && (
                        <div className="pt-2 border-t border-zinc-200/70 dark:border-zinc-700/70 flex flex-col gap-1.5">
                          <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                            Findings ({findings.length})
                          </span>
                          <ul className="space-y-1 text-xs text-zinc-800 dark:text-zinc-200">
                            {findings.map((finding, idx) => (
                              <li
                                key={`${idx}-${finding}`}
                                className="flex items-start gap-2"
                              >
                                <span
                                  className={`mt-1 w-1.5 h-1.5 rounded-full shrink-0 ${
                                    approved === false
                                      ? 'bg-amber-500'
                                      : 'bg-teal-500'
                                  }`}
                                />
                                <span className="min-w-0 flex-1">
                                  {finding}
                                </span>
                              </li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )
                })}
              </div>
            )}
          </Card>
        )}

        {/* Discussion & Comments */}
        <SeedDiscussionCard
          seedId={seed.id}
          comments={comments}
          canContribute={canContribute}
          canManageProject={canManageProject}
          resolveActorName={resolveActorName}
          onRefresh={onRefresh}
          expandTrigger={discussionExpandTrigger}
        />
      </div>

      {/* Right Column: Metadata & Approvals */}
      <div className="lg:col-span-4 min-w-0 flex flex-col gap-6">
        <SeedMetadataSidebar
          seed={seed}
          project={project}
          primaryRepo={primaryRepo}
          repos={repos}
          runs={runs}
          changes={changes}
          seedEvents={seedEvents}
          canContribute={canContribute}
          autonomyNotice={autonomyNotice}
          onAutonomyChange={handleAutonomyChange}
          resolveActorName={resolveActorName}
          onOpenPullRequest={onOpenPullRequest}
        />
        <SeedAttachmentsCard seed={seed} canContribute={canContribute} />
        <SeedRelatedCard
          seed={seed}
          canContribute={canContribute}
          onNewRelatedSeed={onNewRelatedSeed}
        />
      </div>

      {/* Read-Only Side-by-Side Second Opinion Comparison Modal */}
      {compareStage && (
        <Modal
          isOpen={compareStage !== null}
          onClose={() => setCompareStage(null)}
          size="5xl"
          title={
            <span className="flex items-center gap-2">
              <Columns2 size={18} className="text-teal-500 shrink-0" />
              <span>
                Compare {STAGE_LABELS[compareStage]} Drafts
              </span>
            </span>
          }
        >
          {(() => {
            const stageRuns = getAllStageRuns(compareStage)
            const comparableStages = (
              ['ideate', 'plan', 'execute', 'review'] as SeedStage[]
            ).filter((st) => getAllStageRuns(st).length >= 2)
            const runA =
              stageRuns.find((r) => r.id === compareRunAId) || stageRuns[0] || null
            const runB =
              stageRuns.find((r) => r.id === compareRunBId) ||
              stageRuns[stageRuns.length - 1] ||
              null
            const stageUnlocked =
              (compareStage === 'ideate' && canEditSpec) ||
              (compareStage === 'plan' && canEditPlan)

            const renderRunColumn = (
              slotLabel: string,
              selectedRun: Run | null,
              onSelectId: (nextId: string) => void
            ) => {
              if (!selectedRun) {
                return (
                  <div className="p-4 rounded-xl border border-zinc-200 dark:border-zinc-800 text-xs text-zinc-500">
                    No run available.
                  </div>
                )
              }
              const handle = resolveRunAgentHandle(selectedRun)
              const ag = selectedRun.agent_id
                ? agents.find((a) => a.id === selectedRun.agent_id)
                : undefined
              const runtimeName = selectedRun.runtime || ag?.runtime || 'cli'
              const modelName = selectedRun.model || ag?.model || 'default'
              const out = (selectedRun.output || {}) as Record<string, unknown>
              const durationMs =
                selectedRun.started_at && selectedRun.finished_at
                  ? Math.max(
                      0,
                      new Date(selectedRun.finished_at).getTime() -
                        new Date(selectedRun.started_at).getTime()
                    )
                  : null
              const isCurrentDraft =
                (compareStage === 'ideate' && activeIdeateRunId === selectedRun.id) ||
                (compareStage === 'plan' && activePlanRunId === selectedRun.id)

              const criteriaList = Array.isArray(out.acceptance_criteria)
                ? out.acceptance_criteria.filter(
                    (c): c is string => typeof c === 'string' && Boolean(c.trim())
                  )
                : []
              const planTasks = Array.isArray(out.tasks)
                ? out.tasks.filter(
                    (t): t is Record<string, unknown> =>
                      Boolean(t) && typeof t === 'object'
                  )
                : []
              const execFiles = Array.isArray(out.files_modified)
                ? out.files_modified.filter((f): f is string => typeof f === 'string')
                : Array.isArray(out.files_changed)
                  ? out.files_changed.filter((f): f is string => typeof f === 'string')
                  : []
              const reviewFindings = Array.isArray(out.findings)
                ? out.findings.filter(
                    (f): f is string => typeof f === 'string' && Boolean(f.trim())
                  )
                : []

              return (
                <div className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-950/60 p-4 flex flex-col gap-3 min-w-0">
                  <div className="flex flex-col gap-2 pb-3 border-b border-zinc-200/80 dark:border-zinc-800">
                    <div className="flex items-center justify-between gap-2">
                      <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                        {slotLabel}
                      </span>
                      <div className="flex items-center gap-1.5">
                        {isCurrentDraft && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[10px] font-semibold bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30">
                            <Check size={10} />
                            <span>Active Draft</span>
                          </span>
                        )}
                        {stageUnlocked &&
                          selectedRun.status === 'succeeded' &&
                          (compareStage === 'ideate' || compareStage === 'plan') && (
                            <button
                              type="button"
                              disabled={Boolean(selectingRunId) || isCurrentDraft}
                              onClick={() =>
                                void handleSelectDraftRun(
                                  selectedRun.id,
                                  compareStage
                                )
                              }
                              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white transition cursor-pointer"
                            >
                              {selectingRunId === selectedRun.id ? (
                                <Loader2 size={11} className="animate-spin" />
                              ) : (
                                <Check size={11} />
                              )}
                              <span>
                                {isCurrentDraft ? 'Selected' : 'Select This Draft'}
                              </span>
                            </button>
                          )}
                      </div>
                    </div>

                    <select
                      aria-label={`Select ${slotLabel}`}
                      value={selectedRun.id}
                      onChange={(e) => onSelectId(e.target.value)}
                      className="w-full h-9 px-2.5 rounded-xl text-xs font-mono bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                    >
                      {stageRuns.map((r) => {
                        const rHandle = resolveRunAgentHandle(r)
                        const rModel =
                          r.model ||
                          agents.find((a) => a.id === r.agent_id)?.model ||
                          'default'
                        return (
                          <option key={r.id} value={r.id}>
                            Attempt #{r.attempt} · {rHandle} · {rModel} ({r.status})
                          </option>
                        )
                      })}
                    </select>

                    <div className="flex flex-wrap items-center gap-2 text-[11px] font-mono text-zinc-600 dark:text-zinc-400">
                      <span className="px-2 py-0.5 rounded-md bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800">
                        {handle} · {runtimeName}
                      </span>
                      <span className="px-2 py-0.5 rounded-md bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800">
                        {modelName}
                      </span>
                      <span className="px-2 py-0.5 rounded-md bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800">
                        {formatUsd(selectedRun.cost)} ·{' '}
                        {formatTokens(
                          (selectedRun.input_tokens || 0) +
                            (selectedRun.output_tokens || 0)
                        )}{' '}
                        tok
                      </span>
                      <span className="px-2 py-0.5 rounded-md bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800">
                        {formatDurationMs(durationMs)}
                      </span>
                    </div>
                  </div>

                  {selectedRun.error && (
                    <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs font-mono text-rose-700 dark:text-rose-300">
                      {selectedRun.error}
                    </div>
                  )}

                  {/* Stage-Specific Structured Output */}
                  {compareStage === 'ideate' && (
                    <div className="flex flex-col gap-3 text-xs">
                      {typeof out.title === 'string' && out.title.trim() && (
                        <div>
                          <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-0.5">
                            Proposed Title
                          </div>
                          <div className="font-semibold text-zinc-900 dark:text-zinc-100">
                            {out.title}
                          </div>
                        </div>
                      )}
                      {typeof out.description === 'string' &&
                        out.description.trim() && (
                          <div>
                            <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                              Summary
                            </div>
                            <MarkdownContent content={out.description} />
                          </div>
                        )}
                      <div>
                        <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                          Specification
                        </div>
                        {typeof out.specification === 'string' &&
                        out.specification.trim() ? (
                          <div className="p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800 max-h-80 overflow-y-auto">
                            <MarkdownContent content={out.specification} />
                          </div>
                        ) : (
                          <p className="italic text-zinc-400">
                            No specification output recorded on this run.
                          </p>
                        )}
                      </div>
                      {criteriaList.length > 0 && (
                        <div>
                          <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                            Acceptance Criteria ({criteriaList.length})
                          </div>
                          <ul className="list-disc list-inside space-y-1 text-zinc-800 dark:text-zinc-200">
                            {criteriaList.map((c, idx) => (
                              <li key={`${idx}-${c}`}>{c}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}

                  {compareStage === 'plan' && (
                    <div className="flex flex-col gap-3 text-xs">
                      <div>
                        <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                          Implementation Plan
                        </div>
                        {typeof out.plan === 'string' && out.plan.trim() ? (
                          <div className="p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800 max-h-80 overflow-y-auto">
                            <MarkdownContent content={out.plan} />
                          </div>
                        ) : (
                          <p className="italic text-zinc-400">
                            No implementation plan recorded on this run.
                          </p>
                        )}
                      </div>
                      {planTasks.length > 0 && (
                        <div>
                          <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                            Proposed Tasks ({planTasks.length})
                          </div>
                          <ol className="list-decimal list-inside space-y-1 text-zinc-800 dark:text-zinc-200">
                            {planTasks.map((t, idx) => (
                              <li key={idx}>
                                <span className="font-medium">
                                  {String(t.title || `Task ${idx + 1}`)}
                                </span>
                              </li>
                            ))}
                          </ol>
                        </div>
                      )}
                    </div>
                  )}

                  {compareStage === 'execute' && (
                    <div className="flex flex-col gap-3 text-xs">
                      <div>
                        <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                          Execution Summary
                        </div>
                        {typeof out.summary === 'string' && out.summary.trim() ? (
                          <div className="p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800 max-h-80 overflow-y-auto">
                            <MarkdownContent content={out.summary} />
                          </div>
                        ) : (
                          <p className="italic text-zinc-400">
                            No execution summary recorded on this run.
                          </p>
                        )}
                      </div>
                      {execFiles.length > 0 && (
                        <div>
                          <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                            Files Modified ({execFiles.length})
                          </div>
                          <ul className="space-y-0.5 font-mono text-[11px] text-zinc-700 dark:text-zinc-300">
                            {execFiles.map((fp) => (
                              <li key={fp}>{fp}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}

                  {compareStage === 'review' && (
                    <div className="flex flex-col gap-3 text-xs">
                      <div className="flex items-center gap-2">
                        <span className="text-[10px] font-bold uppercase tracking-wider text-zinc-400">
                          Verdict:
                        </span>
                        {selectedRun.status === 'succeeded' ? (
                          out.approved !== false ? (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300">
                              <Check size={11} />
                              <span>Approved</span>
                            </span>
                          ) : (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-amber-500/15 text-amber-800 dark:text-amber-300">
                              <AlertCircle size={11} />
                              <span>Changes Requested</span>
                            </span>
                          )
                        ) : (
                          <span className="text-zinc-500">{selectedRun.status}</span>
                        )}
                      </div>
                      {typeof out.summary_comment === 'string' &&
                        out.summary_comment.trim() && (
                          <div className="p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-800 max-h-72 overflow-y-auto">
                            <MarkdownContent content={out.summary_comment} />
                          </div>
                        )}
                      {reviewFindings.length > 0 && (
                        <div>
                          <div className="text-[10px] font-bold uppercase tracking-wider text-zinc-400 mb-1">
                            Findings ({reviewFindings.length})
                          </div>
                          <ul className="list-disc list-inside space-y-1 text-zinc-800 dark:text-zinc-200">
                            {reviewFindings.map((f, idx) => (
                              <li key={`${idx}-${f}`}>{f}</li>
                            ))}
                          </ul>
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            }

            return (
              <div className="flex flex-col gap-4">
                <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-zinc-500 dark:text-zinc-400">
                  <span>
                    Read-only comparison of parallel or sequential second-opinion runs.
                    Switching dropdowns does not modify the Seed.
                  </span>
                  {comparableStages.length > 1 && (
                    <div className="inline-flex items-center gap-1 p-1 rounded-xl bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700">
                      {comparableStages.map((st) => (
                        <button
                          key={st}
                          type="button"
                          onClick={() => openCompareModal(st)}
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold transition cursor-pointer ${
                            compareStage === st
                              ? 'bg-white dark:bg-zinc-900 text-teal-600 dark:text-teal-400 shadow-2xs'
                              : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100'
                          }`}
                        >
                          {STAGE_LABELS[st]} ({getAllStageRuns(st).length})
                        </button>
                      ))}
                    </div>
                  )}
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-start">
                  {renderRunColumn('Run A', runA, setCompareRunAId)}
                  {renderRunColumn('Run B', runB, setCompareRunBId)}
                </div>
              </div>
            )
          })()}
        </Modal>
      )}
    </div>
  )
}
