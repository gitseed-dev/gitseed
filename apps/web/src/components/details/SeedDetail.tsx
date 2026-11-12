import React, { useEffect, useMemo, useState } from 'react'
import {
  Activity,
  Archive,
  Undo2,
  Bot,
  CheckCircle2,
  ChevronRight,
  Edit3,
  GitCompare,
  Layers,
  ListChecks,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  RotateCcw,
  ShieldCheck,
  Terminal,
  Zap,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { parseActivityEvents } from '../../utils/activity'
import { formatRelativeTime } from '../../utils/format'
import { canUserContributeToProject, canUserManageProject } from '../../utils/projects'
import {
  canUserApproveSeedStage,
  canUserDecideSeedStage,
  getEffectiveSeedPolicies,
  getSeedStatusClasses,
  resolveStageRunAgent,
  SEED_STAGE_ORDER,
  STAGE_LABELS,
  STATUS_LABELS,
} from '../../utils/seeds'
import { ActivityRow } from '../common/ActivityRow'
import { PageBreadcrumb } from '../common/PageBreadcrumb'
import { SegmentedControl } from '../common/SegmentedControl'
import { SeedModal } from '../modals/SeedModal'
import { SeedGovernanceBanner } from './seed/SeedGovernanceBanner'
import { SeedChangesTab } from './seed/SeedChangesTab'
import { SeedOverviewTab } from './seed/SeedOverviewTab'
import { SeedRunsTab } from './seed/SeedRunsTab'
import { SeedTasksTab } from './seed/SeedTasksTab'
import { MoveSeedBackDialog } from './seed/MoveSeedBackDialog'
import type { ActivityEvent, Run, Seed } from '../../types'

export type SeedDetailTabId = 'overview' | 'tasks' | 'changes' | 'runs' | 'activity'

const VALID_TABS: ReadonlySet<SeedDetailTabId> = new Set([
  'overview',
  'tasks',
  'changes',
  'runs',
  'activity',
])

export interface SeedDetailProps {
  seedId: string
  onBack: () => void
}

export const SeedDetail: React.FC<SeedDetailProps> = ({ seedId, onBack }) => {
  const { user } = useAuth()
  const queryClient = useQueryClient()
  const {
    allSeeds,
    projects,
    agents,
    backends,
    users,
    runs,
    allEvents,
    policies: orgPolicies,
    projectMembersByProject,
    projectPoliciesByProject,
    projectReposByProject,
    activeDetailTab,
    navigateToDetail,
  } = useData()

  const [isEditModalOpen, setIsEditModalOpen] = useState(false)
  const [isRelatedModalOpen, setIsRelatedModalOpen] = useState(false)
  const [isMoreMenuOpen, setIsMoreMenuOpen] = useState(false)
  const [isMoveBackOpen, setIsMoveBackOpen] = useState(false)
  const [isActionBusy, setIsActionBusy] = useState(false)
  const [isDispatchingRun, setIsDispatchingRun] = useState(false)
  const [isOpeningEditor, setIsOpeningEditor] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)
  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(null)
  const [discussionExpandTrigger, setDiscussionExpandTrigger] = useState(0)

  const cachedSeed = useMemo(() => {
    const direct = allSeeds.find((s) => s.id === seedId)
    if (direct) return direct
    if (seedId.includes('#')) {
      const [slug, numStr] = seedId.split('#', 2)
      const num = parseInt(numStr, 10)
      const proj = projects.find((p) => p.slug === slug)
      if (proj && Number.isFinite(num)) {
        return allSeeds.find((s) => s.project_id === proj.id && s.number === num)
      }
    }
    return undefined
  }, [allSeeds, projects, seedId])

  const seedDetailQuery = useQuery({
    queryKey: queryKeys.seedDetail(cachedSeed?.id || seedId),
    queryFn: () => api.seeds.get(cachedSeed?.id || seedId),
    initialData: cachedSeed,
    enabled: Boolean(seedId),
    refetchInterval: (query) => {
      const current = query.state.data || cachedSeed
      return current?.status === 'queued' || current?.status === 'running' ? 2500 : 10000
    },
  })

  const seed: Seed | undefined = seedDetailQuery.data || cachedSeed
  const resolvedId = seed?.id || ''
  const isLiveSeed = seed?.status === 'queued' || seed?.status === 'running'

  const tasksQuery = useQuery({
    queryKey: queryKeys.seedTasks(resolvedId),
    queryFn: () => api.seeds.listTasks(resolvedId),
    enabled: Boolean(resolvedId),
    refetchInterval: isLiveSeed ? 3000 : 12000,
  })

  const approvalsQuery = useQuery({
    queryKey: queryKeys.seedApprovals(resolvedId),
    queryFn: () => api.seeds.listApprovals(resolvedId),
    enabled: Boolean(resolvedId),
    refetchInterval: isLiveSeed ? 4000 : 15000,
  })

  const commentsQuery = useQuery({
    queryKey: queryKeys.seedComments(resolvedId),
    queryFn: () => api.seeds.listComments(resolvedId),
    enabled: Boolean(resolvedId),
    refetchInterval: isLiveSeed ? 3000 : 10000,
  })

  const seedActivityQuery = useQuery({
    queryKey: queryKeys.seedActivity(resolvedId),
    queryFn: () => api.activity.list(200, { seed_id: resolvedId }),
    enabled: Boolean(resolvedId),
    refetchInterval: isLiveSeed ? 4000 : 15000,
  })

  const project = useMemo(
    () => (seed ? projects.find((p) => p.id === seed.project_id) || null : null),
    [projects, seed]
  )

  const members = useMemo(
    () => (project ? projectMembersByProject[project.id] || [] : []),
    [project, projectMembersByProject]
  )

  const repos = useMemo(
    () => (project ? projectReposByProject[project.id] || [] : []),
    [project, projectReposByProject]
  )

  const primaryRepo = useMemo(
    () => repos.find((r) => r.is_primary) || repos[0] || null,
    [repos]
  )

  const isAwaitingPrSync = Boolean(
    seed &&
    (seed.stage === 'review' || seed.stage === 'merge') &&
    seed.status === 'awaiting_approval'
  )

  const changesQuery = useQuery({
    queryKey: queryKeys.seedChanges(resolvedId, selectedRepoId),
    queryFn: () => api.seeds.getChanges(resolvedId, selectedRepoId),
    enabled: Boolean(resolvedId && repos.length > 0),
    refetchInterval: (query) => {
      const prCiStatus = query.state.data?.pull_request_status?.ci_status
      if (isAwaitingPrSync) return 4000
      if (prCiStatus === 'pending' || isLiveSeed) return 6000
      return 30000
    },
  })

  useEffect(() => {
    if (
      resolvedId &&
      changesQuery.data?.pull_request_status?.merged &&
      seed &&
      seed.status !== 'merged'
    ) {
      queryClient.invalidateQueries({ queryKey: queryKeys.seedDetail(resolvedId) })
      queryClient.invalidateQueries({ queryKey: queryKeys.seeds })
      queryClient.invalidateQueries({ queryKey: queryKeys.seedComments(resolvedId) })
      queryClient.invalidateQueries({ queryKey: queryKeys.seedApprovals(resolvedId) })
      queryClient.invalidateQueries({ queryKey: queryKeys.seedActivity(resolvedId) })
    }
  }, [
    changesQuery.data?.pull_request_status?.merged,
    queryClient,
    resolvedId,
    seed,
  ])

  const effectivePolicies = useMemo(
    () =>
      seed
        ? getEffectiveSeedPolicies(seed.project_id, projectPoliciesByProject, orgPolicies)
        : orgPolicies,
    [seed, projectPoliciesByProject, orgPolicies]
  )

  const currentPolicy = useMemo(
    () =>
      seed
        ? effectivePolicies.find((p) => p.stage === seed.stage) || null
        : null,
    [effectivePolicies, seed]
  )

  const canContribute = useMemo(
    () => (project ? canUserContributeToProject(user, project, members) : false),
    [user, project, members]
  )

  const canManage = useMemo(
    () => (project ? canUserManageProject(user, project, members) : false),
    [user, project, members]
  )

  const seedRunsQuery = useQuery({
    queryKey: queryKeys.seedRuns(resolvedId),
    queryFn: () => api.seeds.listRuns(resolvedId),
    enabled: Boolean(resolvedId),
    refetchInterval: (query) => {
      const list = query.state.data || []
      const hasActive =
        isLiveSeed ||
        list.some(
          (r) =>
            r.status === 'queued' ||
            r.status === 'starting' ||
            r.status === 'running'
        )
      return hasActive ? 3000 : 15000
    },
  })

  const seedRuns = useMemo(() => {
    if (!seed) return []
    const globalForSeed = runs.filter((r) => r.seed_id === seed.id)
    const fetched = seedRunsQuery.data
    if (!fetched) return globalForSeed
    const globalById = new Map(globalForSeed.map((r) => [r.id, r]))
    const seen = new Set<string>()
    const merged: Run[] = []
    for (const r of fetched) {
      seen.add(r.id)
      const globalMatch = globalById.get(r.id)
      const globalTail = globalMatch?.output?.log_tail
      const fetchedTail = r.output?.log_tail
      if (
        globalMatch &&
        typeof globalTail === 'string' &&
        globalTail.length > (typeof fetchedTail === 'string' ? fetchedTail.length : 0)
      ) {
        merged.push({
          ...r,
          output: { ...(r.output || {}), log_tail: globalTail },
        })
      } else {
        merged.push(r)
      }
    }
    for (const r of globalForSeed) {
      if (!seen.has(r.id)) {
        merged.push(r)
      }
    }
    return merged
  }, [runs, seed, seedRunsQuery.data])

  const seedEvents = useMemo(() => {
    if (!seed) return []
    const direct = seedActivityQuery.data || []
    const fallback = allEvents.filter((e) => e.seed_id === seed.id)
    const seen = new Set<number | string>()
    const merged: ActivityEvent[] = []
    for (const ev of [...direct, ...fallback]) {
      if (!seen.has(ev.id)) {
        seen.add(ev.id)
        merged.push(ev)
      }
    }

    let synthSeq = -1
    if (seed.created_at && !merged.some((e) => e.type === 'seed.created')) {
      merged.push({
        id: synthSeq--,
        occurred_at: seed.created_at,
        project_id: seed.project_id,
        seed_id: seed.id,
        actor_type: seed.created_by ? 'user' : 'system',
        actor_id: seed.created_by,
        type: 'seed.created',
        data: {
          number: seed.number,
          title: seed.title,
          autonomy: seed.autonomy,
        },
      })
    }

    for (const r of seedRuns) {
      const startTs = r.started_at || r.created_at
      if (
        startTs &&
        r.status !== 'queued' &&
        !merged.some((e) => e.type === 'run.started' && e.data?.run_id === r.id)
      ) {
        merged.push({
          id: synthSeq--,
          occurred_at: startTs,
          project_id: seed.project_id,
          seed_id: seed.id,
          actor_type: 'agent',
          actor_id: r.agent_id,
          type: 'run.started',
          data: {
            run_id: r.id,
            stage: r.stage,
            attempt: r.attempt,
          },
        })
      }
      const finishTs = r.finished_at || r.updated_at
      if (
        r.status === 'succeeded' &&
        finishTs &&
        !merged.some((e) => e.type === 'run.succeeded' && e.data?.run_id === r.id)
      ) {
        merged.push({
          id: synthSeq--,
          occurred_at: finishTs,
          project_id: seed.project_id,
          seed_id: seed.id,
          actor_type: 'agent',
          actor_id: r.agent_id,
          type: 'run.succeeded',
          data: {
            run_id: r.id,
            stage: r.stage,
            attempt: r.attempt,
            cost: r.cost,
            input_tokens: r.input_tokens,
            output_tokens: r.output_tokens,
          },
        })
      } else if (
        r.status === 'failed' &&
        finishTs &&
        !merged.some((e) => e.type === 'run.failed' && e.data?.run_id === r.id)
      ) {
        merged.push({
          id: synthSeq--,
          occurred_at: finishTs,
          project_id: seed.project_id,
          seed_id: seed.id,
          actor_type: 'agent',
          actor_id: r.agent_id,
          type: 'run.failed',
          data: {
            run_id: r.id,
            stage: r.stage,
            attempt: r.attempt,
            error: r.error,
          },
        })
      }
    }

    for (const ap of approvalsQuery.data || []) {
      if (!ap.created_at) continue
      const apType: ActivityEvent['type'] =
        ap.decision === 'approved'
          ? 'approval.granted'
          : ap.decision === 'changes_requested'
            ? 'approval.changes_requested'
            : 'approval.rejected'
      if (
        !merged.some(
          (e) =>
            e.type === apType &&
            (e.data?.approval_id === ap.id ||
              (ap.automatic && e.data?.stage === ap.stage))
        )
      ) {
        merged.push({
          id: synthSeq--,
          occurred_at: ap.created_at,
          project_id: seed.project_id,
          seed_id: seed.id,
          actor_type: ap.automatic ? 'system' : 'user',
          actor_id: ap.decided_by,
          type: apType,
          data: {
            stage: ap.stage,
            approval_id: ap.id,
            automatic: ap.automatic,
          },
        })
      }
    }

    if (
      seed.status === 'merged' &&
      seed.merged_at &&
      !merged.some((e) => e.type === 'seed.merged')
    ) {
      merged.push({
        id: synthSeq--,
        occurred_at: seed.merged_at,
        project_id: seed.project_id,
        seed_id: seed.id,
        actor_type: 'system',
        actor_id: null,
        type: 'seed.merged',
        data: {},
      })
    }

    return merged
  }, [seed, seedActivityQuery.data, allEvents, seedRuns, approvalsQuery.data])

  const codeEditorUserIds = useMemo(() => {
    const ids = new Set<string>()
    for (const ev of seedEvents) {
      if (
        ev.type === 'stage.changed' &&
        ev.data?.reason === 'code_edited' &&
        ev.actor_id
      ) {
        ids.add(ev.actor_id)
      }
    }
    return ids
  }, [seedEvents])

  const canApproveStage = useMemo(
    () =>
      seed
        ? canUserApproveSeedStage(
          user,
          project,
          seed,
          currentPolicy,
          codeEditorUserIds,
          members
        )
        : false,
    [user, project, seed, currentPolicy, codeEditorUserIds, members]
  )

  const canDecideStage = useMemo(
    () =>
      seed
        ? canUserDecideSeedStage(user, project, currentPolicy, members)
        : false,
    [user, project, seed, currentPolicy, members]
  )

  const parsedSeedEvents = useMemo(
    () =>
      parseActivityEvents(seedEvents, {
        seeds: allSeeds,
        projects,
        users,
        agents,
        backends,
        seedScoped: true,
      }),
    [seedEvents, allSeeds, projects, users, agents, backends]
  )

  const currentTab: SeedDetailTabId =
    activeDetailTab && VALID_TABS.has(activeDetailTab as SeedDetailTabId)
      ? (activeDetailTab as SeedDetailTabId)
      : 'overview'

  if (!seed) {
    return (
      <div className="p-6">
        <PageBreadcrumb
          items={[{ label: 'Seeds', onClick: onBack }]}
          currentLabel={seedId}
        />
        <div className="mt-6 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 p-8 text-center">
          <p className="text-sm text-zinc-500">
            {seedDetailQuery.isLoading ? 'Loading seed...' : 'Seed not found.'}
          </p>
        </div>
      </div>
    )
  }

  const tasks = tasksQuery.data || []
  const approvals = approvalsQuery.data || []
  const comments = commentsQuery.data || []
  const activeCommentsCount = comments.filter((c) => !c.deleted_at).length
  const currentStageIndex = SEED_STAGE_ORDER.indexOf(seed.stage)
  // Move back: only from a resting state (gate / stopped / failed / blocked), never mid-run
  const canMoveBack =
    canContribute &&
    currentStageIndex > 0 &&
    ['awaiting_approval', 'stopped', 'failed', 'blocked'].includes(seed.status)

  const handleJumpToComments = () => {
    setDiscussionExpandTrigger((prev) => prev + 1)
    if (currentTab !== 'overview') {
      navigateToDetail(VIEWS.SEEDS, seed.id, undefined)
    }
    window.setTimeout(() => {
      const target =
        document.getElementById('seed-latest-comment') ||
        document.getElementById('seed-discussion-section')
      target?.scrollIntoView({ behavior: 'smooth', block: 'end' })
    }, 60)
  }

  const invalidateSeedState = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedDetail(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedRuns(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedTasks(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedApprovals(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedComments(seed.id) }),
      queryClient.invalidateQueries({ queryKey: queryKeys.seedActivity(seed.id) }),
      queryClient.invalidateQueries({ queryKey: ['seeds', seed.id, 'changes'] }),
      queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
      queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
    ])
  }

  const handleTabChange = (tab: SeedDetailTabId) => {
    navigateToDetail(VIEWS.SEEDS, seed.id, tab === 'overview' ? undefined : tab)
  }

  const handleDispatchRun = async (taskId?: string | null) => {
    setActionError(null)
    const stageAgent = resolveStageRunAgent(seed.stage, currentPolicy, agents)

    if (!stageAgent) {
      setActionError('No enabled stage agent is configured for this stage.')
      return
    }

    const computeBackend = backends.find(
      (b) => b.type === 'compute' && b.enabled
    )

    setIsDispatchingRun(true)
    try {
      await api.runs.create({
        seed_id: seed.id,
        task_id: taskId || null,
        stage: seed.stage,
        agent_id: stageAgent.id,
        compute_backend_id: computeBackend?.id || null,
      })
      await invalidateSeedState()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to dispatch run.')
    } finally {
      setIsDispatchingRun(false)
    }
  }

  const handleStartSeed = async () => {
    setActionError(null)
    setIsActionBusy(true)
    try {
      await api.seeds.start(seed.id)
      await invalidateSeedState()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to start seed.')
    } finally {
      setIsActionBusy(false)
    }
  }

  const handleFastTrackToExecute = async () => {
    setActionError(null)
    setIsActionBusy(true)
    setIsMoreMenuOpen(false)
    try {
      await api.seeds.advance(seed.id, 'execute', true)
      await invalidateSeedState()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to fast-track seed to Execute.')
    } finally {
      setIsActionBusy(false)
    }
  }

  const handleResumeBlocked = async () => {
    setActionError(null)
    setIsActionBusy(true)
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
      await invalidateSeedState()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to resume seed.')
    } finally {
      setIsActionBusy(false)
    }
  }

  const handleCloseOrReopen = async () => {
    setActionError(null)
    setIsActionBusy(true)
    setIsMoreMenuOpen(false)
    try {
      if (seed.status === 'closed') {
        await api.seeds.reopen(seed.id)
      } else {
        await api.seeds.close(seed.id)
      }
      await invalidateSeedState()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update seed status.')
    } finally {
      setIsActionBusy(false)
    }
  }

  return (
    <div className="max-w-7xl mx-auto space-y-5 pb-16">
      <PageBreadcrumb
        project={project}
        items={[{ label: 'Seeds', onClick: onBack }]}
        currentLabel={`#${seed.number}`}
      />

      {/* Seed Header */}
      <div className="flex flex-col lg:flex-row lg:items-start justify-between gap-4">
        <div className="space-y-1.5 min-w-0">
          <div className="flex flex-wrap items-center gap-2 text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
            <span className="font-mono font-bold text-zinc-700 dark:text-zinc-300">
              #{seed.number}
            </span>
            <span>·</span>
            <span className="font-semibold text-zinc-800 dark:text-zinc-200">
              {STAGE_LABELS[seed.stage]}
            </span>
            <span>·</span>
            <span
              className={`inline-flex items-center px-2 py-0.5 rounded-md border text-xs font-semibold ${getSeedStatusClasses(
                seed.status
              )}`}
            >
              {STATUS_LABELS[seed.status]}
            </span>
          </div>

          <h1 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100 leading-snug wrap-break-word">
            {seed.title}
          </h1>
        </div>

        {/* Actions */}
        {canContribute && (
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            {seed.status === 'staged' && (
              <Button
                size="sm"
                variant="primary"
                onPress={handleStartSeed}
                isDisabled={isActionBusy}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <Play className="w-3.5 h-3.5" />
                {isActionBusy ? 'Starting...' : 'Start Work'}
              </Button>
            )}

            {seed.status === 'failed' && (
              <Button
                size="sm"
                variant="primary"
                onPress={() => handleDispatchRun(null)}
                isDisabled={isDispatchingRun || isActionBusy}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                {isDispatchingRun ? 'Retrying...' : 'Retry'}
              </Button>
            )}

            {seed.status === 'blocked' && (
              <Button
                size="sm"
                variant="primary"
                onPress={handleResumeBlocked}
                isDisabled={isActionBusy}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <Play className="w-3.5 h-3.5" />
                {isActionBusy ? 'Resuming...' : 'Resume'}
              </Button>
            )}

            {seed.status === 'closed' && canManage && (
              <Button
                size="sm"
                variant="primary"
                onPress={handleCloseOrReopen}
                isDisabled={isActionBusy}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <RotateCcw className="w-3.5 h-3.5" />
                Reopen
              </Button>
            )}

            {seed.status === 'merged' && (
              <Button
                size="sm"
                variant="secondary"
                onPress={() => setIsRelatedModalOpen(true)}
                className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <Plus className="w-3.5 h-3.5" />
                New related seed
              </Button>
            )}

            {seed.status !== 'merged' &&
              seed.status !== 'closed' &&
              (seed.stage === 'ideate' || seed.stage === 'plan') && (
                <span
                  className="inline-flex"
                  title="Skip Ideate & Plan and jump straight to Execute with @coder"
                >
                  <Button
                    size="sm"
                    variant="secondary"
                    onPress={handleFastTrackToExecute}
                    isDisabled={isActionBusy || isDispatchingRun}
                    className="rounded-xl border border-amber-500/35 bg-amber-500/10 text-amber-700 dark:text-amber-300 hover:bg-amber-500/20 text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
                  >
                    <Zap className="w-3.5 h-3.5" />
                    <span>Fast-Track to Code</span>
                  </Button>
                </span>
              )}

            {seed.status !== 'merged' && seed.status !== 'closed' && (
              <Button
                size="sm"
                variant="secondary"
                onPress={() => setIsEditModalOpen(true)}
                className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                <Edit3 className="w-3.5 h-3.5" />
                Edit
              </Button>
            )}

            {seed.status !== 'merged' && seed.status !== 'closed' && (
              <div className="relative">
                <Button
                  size="sm"
                  variant="secondary"
                  isIconOnly
                  aria-label="More seed actions"
                  onPress={() => setIsMoreMenuOpen((prev) => !prev)}
                  className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 text-zinc-600 dark:text-zinc-300 cursor-pointer"
                >
                  <MoreHorizontal className="w-4 h-4" />
                </Button>

                {isMoreMenuOpen && (
                  <>
                    <div
                      className="fixed inset-0 z-20"
                      onClick={() => setIsMoreMenuOpen(false)}
                    />
                    <div className="absolute right-0 mt-1.5 w-48 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 shadow-lg py-1 z-30">
                      <button
                        type="button"
                        disabled={isActionBusy}
                        onClick={() => {
                          setIsMoreMenuOpen(false)
                          setIsRelatedModalOpen(true)
                        }}
                        className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center gap-2 cursor-pointer"
                      >
                        <Plus className="w-3.5 h-3.5" />
                        <span>New related seed</span>
                      </button>
                      {canMoveBack && (
                        <button
                          type="button"
                          disabled={isActionBusy}
                          onClick={() => {
                            setIsMoreMenuOpen(false)
                            setIsMoveBackOpen(true)
                          }}
                          className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center gap-2 cursor-pointer"
                        >
                          <Undo2 className="w-3.5 h-3.5" />
                          <span>Move back to…</span>
                        </button>
                      )}
                      <button
                        type="button"
                        disabled={isActionBusy}
                        onClick={handleCloseOrReopen}
                        className="w-full px-3 py-2 text-left text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center gap-2 cursor-pointer"
                      >
                        <Archive className="w-3.5 h-3.5" />
                        <span>Close seed</span>
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {actionError && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-700 dark:text-rose-300">
          {actionError}
        </div>
      )}

      {/* 5-Stage SDLC Pipeline Stepper */}
      <div className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs p-4">
        <div className="flex flex-col sm:flex-row sm:items-stretch gap-2 sm:gap-1.5">
          {SEED_STAGE_ORDER.map((stage, idx) => {
            const policy = effectivePolicies.find((p) => p.stage === stage)
            const enabled = policy ? policy.enabled : true
            const stageAgent = policy?.agent_id
              ? agents.find((a) => a.id === policy.agent_id)
              : undefined
            const isCurrent = seed.stage === stage
            const isPast = idx < currentStageIndex || seed.status === 'merged'

            const stageApproval = isPast
              ? approvals
                .filter((a) => a.stage === stage && a.decision === 'approved')
                .slice(-1)[0]
              : undefined
            const approverUser = stageApproval?.decided_by
              ? users.find((u) => u.id === stageApproval.decided_by)
              : undefined
            const approverName =
              approverUser?.preferred_name ||
              approverUser?.name ||
              approverUser?.email ||
              'Admin'
            const stageApprovalTooltip = stageApproval
              ? `${
                  stageApproval.automatic
                    ? 'Auto-approved'
                    : `Approved by ${approverName}`
                }${
                  stageApproval.created_at
                    ? ` · ${formatRelativeTime(stageApproval.created_at)} (${new Date(
                        stageApproval.created_at
                      ).toLocaleString()})`
                    : ''
                }${stageApproval.comment ? `: "${stageApproval.comment}"` : ''}`
              : undefined

            return (
              <React.Fragment key={stage}>
                <div
                  title={stageApprovalTooltip}
                  className={`flex-1 min-w-0 rounded-xl border px-3.5 py-2.5 transition-colors ${isCurrent
                    ? 'border-teal-500/50 bg-teal-500/10'
                    : isPast
                      ? 'border-emerald-500/25 bg-emerald-500/5'
                      : !enabled
                        ? 'border-zinc-200/60 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-950/40 opacity-55'
                        : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-950/60'
                    }`}
                >
                  <div className="flex items-center justify-between gap-1.5">
                    <div className="flex items-center gap-1.5">
                      {isPast ? (
                        <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0" />
                      ) : (
                        <span
                          className={`w-4 h-4 rounded-full text-[11px] font-mono flex items-center justify-center ${isCurrent
                            ? 'bg-teal-500 text-white font-semibold'
                            : 'bg-zinc-200 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400'
                            }`}
                        >
                          {idx + 1}
                        </span>
                      )}
                      <span
                        className={`text-xs sm:text-[13px] font-bold ${isCurrent
                          ? 'text-teal-700 dark:text-teal-300'
                          : isPast
                            ? 'text-emerald-700 dark:text-emerald-300'
                            : 'text-zinc-800 dark:text-zinc-200'
                          }`}
                      >
                        {STAGE_LABELS[stage]}
                      </span>
                    </div>

                    {!isPast && enabled && (
                      policy?.requires_approval ? (
                        <span
                          title="Requires approval gate"
                          className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-700 dark:text-zinc-300"
                        >
                          <ShieldCheck className="w-3.5 h-3.5 text-teal-500 shrink-0" />
                          <span>Gate</span>
                        </span>
                      ) : (
                        <span
                          title="Auto-advances on stage completion"
                          className="inline-flex items-center gap-1 text-[11px] font-medium text-zinc-700 dark:text-zinc-300"
                        >
                          <Zap className="w-3.5 h-3.5 text-amber-500 shrink-0" />
                          <span>Auto</span>
                        </span>
                      )
                    )}
                  </div>

                  <div className="mt-1.5 flex items-center justify-between text-xs text-zinc-500">
                    {isPast ? (
                      !enabled ? (
                        <span>Skipped</span>
                      ) : stageApproval ? (
                        <span className="text-emerald-700/80 dark:text-emerald-300/80 font-medium truncate">
                          {stageApproval.automatic
                            ? 'auto-approved'
                            : `approved by ${approverName}`}
                        </span>
                      ) : (
                        <span className="text-emerald-700/80 dark:text-emerald-300/80 font-medium truncate">
                          {stageAgent ? `completed by @${stageAgent.handle}` : 'completed'}
                        </span>
                      )
                    ) : stageAgent ? (
                      <span className="inline-flex items-center gap-1 font-mono text-zinc-600 dark:text-zinc-400 truncate">
                        <Bot className="w-3 h-3 text-teal-500 shrink-0" />@{stageAgent.handle}
                      </span>
                    ) : (
                      <span>{enabled ? 'Default agent' : 'Skipped'}</span>
                    )}
                  </div>
                </div>
                {idx < SEED_STAGE_ORDER.length - 1 && (
                  <ChevronRight
                    size={14}
                    strokeWidth={2.25}
                    aria-hidden="true"
                    className="hidden sm:block text-zinc-400/80 dark:text-zinc-600 shrink-0 self-center select-none"
                  />
                )}
              </React.Fragment>
            )
          })}
        </div>
      </div>

      {/* Governance & Live Status Banner */}
      <SeedGovernanceBanner
        seed={seed}
        currentPolicy={currentPolicy}
        approvals={approvals}
        runs={seedRuns}
        tasks={tasks}
        pullRequestUrl={changesQuery.data?.pull_request_url || null}
        pullRequestStatus={changesQuery.data?.pull_request_status || null}
        canApprove={canApproveStage}
        canContribute={canContribute}
        canRequestChanges={canDecideStage}
        onRefresh={invalidateSeedState}
        onOpenPullRequest={() => handleTabChange('changes')}
      />

      {/* Sub-navigation Tabs */}
      <div className="flex flex-wrap items-center justify-between gap-2.5">
        <SegmentedControl<SeedDetailTabId>
          ariaLabel="Seed detail tabs"
          selectedId={currentTab}
          onSelect={handleTabChange}
          items={[
            {
              id: 'overview',
              label: 'Overview',
              icon: <Layers className="w-3.5 h-3.5" />,
            },
            {
              id: 'tasks',
              label: 'Tasks',
              count: tasks.length,
              icon: <ListChecks className="w-3.5 h-3.5" />,
            },
            {
              id: 'changes',
              label: changesQuery.data?.pull_request_status?.number
                ? `Changes · PR #${changesQuery.data.pull_request_status.number}`
                : 'Changes',
              count: changesQuery.data?.total_files || undefined,
              icon: <GitCompare className="w-3.5 h-3.5" />,
            },
            {
              id: 'runs',
              label: 'Runs',
              count: seedRuns.length,
              icon: <Terminal className="w-3.5 h-3.5" />,
            },
            {
              id: 'activity',
              label: 'Activity',
              count: seedEvents.length,
              icon: <Activity className="w-3.5 h-3.5" />,
            },
          ]}
        />

        <button
          type="button"
          onClick={handleJumpToComments}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 hover:bg-zinc-50 dark:hover:bg-zinc-800/80 text-xs font-semibold text-zinc-700 dark:text-zinc-200 transition shadow-2xs cursor-pointer"
          title="Jump to Discussion & Comments"
        >
          <MessageSquare className="w-3.5 h-3.5 text-teal-600 dark:text-teal-400" />
          <span>Comments</span>
          <span className="px-1.5 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-[11px] font-mono tabular-nums text-zinc-600 dark:text-zinc-300">
            {activeCommentsCount}
          </span>
        </button>
      </div>

      {/* Tab Content */}
      {currentTab === 'overview' && (
        <SeedOverviewTab
          seed={seed}
          project={project}
          primaryRepo={primaryRepo}
          repos={repos}
          runs={seedRuns}
          approvals={approvalsQuery.data || []}
          changes={changesQuery.data}
          seedEvents={seedEvents}
          ideateAgentHandle={
            (() => {
              const ideatePol = effectivePolicies.find((p) => p.stage === 'ideate')
              const ag = ideatePol?.agent_id
                ? agents.find((a) => a.id === ideatePol.agent_id)
                : undefined
              return ag ? `@${ag.handle}` : '@ideator'
            })()
          }
          maxAcceptanceCriteria={
            effectivePolicies.find((p) => p.stage === 'ideate')?.max_acceptance_criteria ?? 6
          }
          comments={comments}
          canContribute={canContribute}
          canManageProject={canManage}
          onEditSeed={() => setIsEditModalOpen(true)}
          onNewRelatedSeed={() => setIsRelatedModalOpen(true)}
          onRefresh={invalidateSeedState}
          onOpenPullRequest={() => handleTabChange('changes')}
          discussionExpandTrigger={discussionExpandTrigger}
        />
      )}

      {currentTab === 'tasks' && (
        <SeedTasksTab
          seed={seed}
          tasks={tasks}
          planAgentHandle={
            (() => {
              const planPol = effectivePolicies.find((p) => p.stage === 'plan')
              const ag = planPol?.agent_id
                ? agents.find((a) => a.id === planPol.agent_id)
                : undefined
              return ag ? `@${ag.handle}` : '@planner'
            })()
          }
          canContribute={canContribute}
          onRefresh={invalidateSeedState}
        />
      )}

      {currentTab === 'changes' && (
        <SeedChangesTab
          seed={seed}
          project={project}
          repos={repos}
          selectedRepoId={selectedRepoId}
          onSelectRepoId={(id) => setSelectedRepoId(id)}
          changes={changesQuery.data}
          isLoading={changesQuery.isLoading}
          isFetching={changesQuery.isFetching}
          error={changesQuery.error instanceof Error ? changesQuery.error : null}
          canContribute={canContribute}
          canRequestChanges={canDecideStage}
          onRefresh={() => {
            void changesQuery.refetch()
            void invalidateSeedState()
          }}
          onMoveBack={canMoveBack ? () => setIsMoveBackOpen(true) : undefined}
          isOpeningEditor={isOpeningEditor}
          onOpenEditor={
            project &&
            canContribute &&
            seed.status !== 'merged' &&
            seed.status !== 'closed' &&
            (seed.stage === 'execute' || seed.stage === 'review')
              ? async (branchRef) => {
                setIsOpeningEditor(true)
                setActionError(null)
                try {
                  const created = await api.projects.createSandbox(project.id, {
                    seed_id: seed.id,
                    repository_id: selectedRepoId ?? repos[0]?.id ?? null,
                    ref: branchRef,
                    kind: 'terminal',
                    title: `Editor · #${seed.number} · ${branchRef}`,
                  })
                  await Promise.all([
                    queryClient.invalidateQueries({ queryKey: ['sessions'] }),
                    queryClient.invalidateQueries({
                      queryKey: queryKeys.projectSandboxes(project.id),
                    }),
                  ])
                  navigateToDetail(VIEWS.SESSIONS, 'terminal', created.id, {
                    pane: 'code',
                  })
                } catch (err) {
                  setActionError(
                    err instanceof Error
                      ? err.message
                      : 'Could not open editor session'
                  )
                } finally {
                  setIsOpeningEditor(false)
                }
              }
              : undefined
          }
          onBrowseCode={
            project
              ? (branchRef) =>
                navigateToDetail(
                  VIEWS.PROJECTS,
                  project.slug || project.id,
                  'code',
                  { ref: branchRef }
                )
              : undefined
          }
          onRunSandbox={
            project
              ? (branchRef) =>
                navigateToDetail(
                  VIEWS.PROJECTS,
                  project.slug || project.id,
                  'run',
                  { seed_id: seed.id, ref: branchRef }
                )
              : undefined
          }
        />
      )}

      {currentTab === 'runs' && (
        <SeedRunsTab
          seed={seed}
          runs={seedRuns}
          tasks={tasks}
          agents={agents}
          backends={backends}
          effectivePolicies={effectivePolicies}
          canContribute={canContribute}
          isDispatching={isDispatchingRun}
          onDispatchRun={handleDispatchRun}
        />
      )}

      {currentTab === 'activity' && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs p-2 sm:p-3 flex flex-col">
          {parsedSeedEvents.length === 0 ? (
            <div className="p-8 text-center text-xs sm:text-sm text-zinc-500">
              No activity recorded for this seed yet.
            </div>
          ) : (
            parsedSeedEvents.map((item) => (
              <ActivityRow
                key={item.id}
                item={item}
                showProject={false}
                timeFormat="relative"
                onNavigateSeed={(sId, subTab) =>
                  navigateToDetail(VIEWS.SEEDS, sId, subTab)
                }
              />
            ))
          )}
        </div>
      )}

      <SeedModal
        isOpen={isEditModalOpen}
        onClose={() => setIsEditModalOpen(false)}
        seed={seed}
      />
      <SeedModal
        isOpen={isRelatedModalOpen}
        onClose={() => setIsRelatedModalOpen(false)}
        relatedToSeed={seed}
        onSaved={(created) => navigateToDetail(VIEWS.SEEDS, created.id)}
      />
      <MoveSeedBackDialog
        isOpen={isMoveBackOpen}
        onClose={() => setIsMoveBackOpen(false)}
        seed={seed}
        policies={effectivePolicies}
        agents={agents}
        onMoved={invalidateSeedState}
      />
    </div>
  )
}
