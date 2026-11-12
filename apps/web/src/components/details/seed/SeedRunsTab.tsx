import React, { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Code2,
  Copy,
  Cpu,
  ExternalLink,
  FileCode2,
  FileText,
  GitBranch,
  GitCommit,
  ListTree,
  Loader2,
  RotateCcw,
  Search,
  Sparkles,
  Square,
  Terminal,
  Wrench,
  XCircle,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { VIEWS } from '../../../constants'
import { useData } from '../../../context/DataContext'
import { ContextWindowBadge } from '../../common/ContextWindowBadge'
import {
  formatRelativeTime,
  formatTokens,
  formatUsd,
  resolveRunContextWindow,
  stripAnsi,
} from '../../../utils/format'
import { buildRepoWebUrl } from '../../../utils/gitUrls'
import {
  parseRunLogStream,
  type ParsedRunLogEntry,
  type ToolCallCategory,
} from '../../../utils/runLogParser'
import { resolveStageRunAgent, STAGE_LABELS } from '../../../utils/seeds'
import type { Agent, Backend, Run, Seed, SeedTask, StagePolicy } from '../../../types'

export interface SeedRunsTabProps {
  seed: Seed
  runs: Run[]
  tasks: SeedTask[]
  agents: Agent[]
  backends: Backend[]
  effectivePolicies: StagePolicy[]
  canContribute: boolean
  isDispatching: boolean
  onDispatchRun: (taskId?: string | null) => void
}

const ACTIVE_RUN_STATUSES = new Set(['queued', 'starting', 'running'])

function getRunStatusMeta(status: Run['status']): {
  label: string
  classes: string
  icon: React.ReactNode
} {
  switch (status) {
    case 'succeeded':
      return {
        label: 'Succeeded',
        classes:
          'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25',
        icon: <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />,
      }
    case 'running':
    case 'starting':
      return {
        label: status === 'starting' ? 'Starting' : 'Running',
        classes: 'bg-teal-500/10 text-teal-700 dark:text-teal-300 border-teal-500/25',
        icon: <Loader2 className="w-3.5 h-3.5 text-teal-500 animate-spin" />,
      }
    case 'queued':
      return {
        label: 'Queued',
        classes: 'bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-500/25',
        icon: <Clock className="w-3.5 h-3.5 text-sky-500" />,
      }
    case 'failed':
    case 'timed_out':
      return {
        label: status === 'timed_out' ? 'Timed Out' : 'Failed',
        classes: 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/25',
        icon: <XCircle className="w-3.5 h-3.5 text-rose-500" />,
      }
    case 'cancelled':
      return {
        label: 'Cancelled',
        classes: 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/25',
        icon: <AlertTriangle className="w-3.5 h-3.5 text-amber-500" />,
      }
    default:
      return {
        label: status,
        classes: 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-300 border-zinc-500/20',
        icon: <Clock className="w-3.5 h-3.5 text-zinc-400" />,
      }
  }
}

function getToolCategoryVisuals(category: ToolCallCategory | undefined): {
  icon: React.ReactNode
  badgeClasses: string
} {
  switch (category) {
    case 'read':
      return {
        icon: <FileText className="w-3.5 h-3.5 shrink-0" />,
        badgeClasses:
          'bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-500/25',
      }
    case 'edit':
      return {
        icon: <FileCode2 className="w-3.5 h-3.5 shrink-0" />,
        badgeClasses:
          'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/25',
      }
    case 'bash':
      return {
        icon: <Terminal className="w-3.5 h-3.5 shrink-0" />,
        badgeClasses:
          'bg-violet-500/10 text-violet-700 dark:text-violet-300 border-violet-500/25',
      }
    case 'search':
      return {
        icon: <Search className="w-3.5 h-3.5 shrink-0" />,
        badgeClasses:
          'bg-indigo-500/10 text-indigo-700 dark:text-indigo-300 border-indigo-500/25',
      }
    case 'git':
      return {
        icon: <GitBranch className="w-3.5 h-3.5 shrink-0" />,
        badgeClasses:
          'bg-teal-500/10 text-teal-700 dark:text-teal-300 border-teal-500/25',
      }
    default:
      return {
        icon: <Wrench className="w-3.5 h-3.5 shrink-0" />,
        badgeClasses:
          'bg-zinc-500/10 text-zinc-700 dark:text-zinc-300 border-zinc-500/25',
      }
  }
}

export const SeedRunsTab: React.FC<SeedRunsTabProps> = ({
  seed,
  runs,
  tasks,
  agents,
  backends,
  effectivePolicies,
  canContribute,
  isDispatching,
  onDispatchRun,
}) => {
  const queryClient = useQueryClient()
  const { projects, projectReposByProject, navigateToDetail } = useData()
  const [cancellingId, setCancellingId] = useState<string | null>(null)
  const [expandedLogs, setExpandedLogs] = useState<Record<string, boolean>>({})
  const [liveLogsByRunId, setLiveLogsByRunId] = useState<Record<string, string>>({})
  const [rawModeByRunId, setRawModeByRunId] = useState<Record<string, boolean>>({})
  const [expandedStepsByKey, setExpandedStepsByKey] = useState<Record<string, boolean>>({})
  const [copiedRunId, setCopiedRunId] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const logPreRefs = useRef<Record<string, HTMLElement | null>>({})

  const project = projects.find((p) => p.id === seed.project_id) || null
  const projectRepos = projectReposByProject[seed.project_id] || []
  const primaryRepo =
    projectRepos.find((r) => r.is_primary) || projectRepos[0] || null
  const primaryGitBackend = primaryRepo
    ? backends.find((b) => b.id === primaryRepo.backend_id) || null
    : null

  const isRunLogsOpen = (run: Run, _hasLogs?: boolean): boolean => {
    if (run.id in expandedLogs) {
      return Boolean(expandedLogs[run.id])
    }
    return run.status === 'starting' || run.status === 'running'
  }

  const toggleRunLogs = (run: Run, hasLogs: boolean) => {
    const currentlyOpen = isRunLogsOpen(run, hasLogs)
    setExpandedLogs((prev) => ({ ...prev, [run.id]: !currentlyOpen }))
  }

  // Poll live container logs every 2.5s for any active (starting/running) run whose log drawer is open
  useEffect(() => {
    const activeOpenRuns = runs.filter((r) => {
      const isLive = r.status === 'starting' || r.status === 'running'
      if (!isLive) return false
      const out = (r.output || {}) as Record<string, unknown>
      const hasLogs = Boolean(
        (typeof out.log_tail === 'string' && out.log_tail.trim()) ||
          liveLogsByRunId[r.id]
      )
      return isRunLogsOpen(r, hasLogs)
    })

    if (activeOpenRuns.length === 0) return

    let cancelled = false
    const pollLogs = async () => {
      await Promise.all(
        activeOpenRuns.map(async (r) => {
          try {
            const res = await api.runs.getLogs(r.id, 800)
            if (cancelled) return
            const text = (res.log_tail || '').trim()
            if (text) {
              setLiveLogsByRunId((prev) =>
                prev[r.id] === text ? prev : { ...prev, [r.id]: text }
              )
              const el = logPreRefs.current[r.id]
              if (el) {
                const isNearBottom =
                  el.scrollHeight - el.scrollTop - el.clientHeight < 80
                if (isNearBottom) {
                  window.requestAnimationFrame(() => {
                    el.scrollTop = el.scrollHeight
                  })
                }
              }
            }
          } catch {
            // Ignore transient polling errors while container is booting
          }
        })
      )
    }

    void pollLogs()
    const timer = window.setInterval(() => {
      void pollLogs()
    }, 2500)

    return () => {
      cancelled = true
      window.clearInterval(timer)
    }
  }, [runs, expandedLogs])

  const handleCopyLogs = (runId: string, logText: string) => {
    navigator.clipboard?.writeText(logText)
    setCopiedRunId(runId)
    window.setTimeout(() => {
      setCopiedRunId((prev) => (prev === runId ? null : prev))
    }, 2000)
  }

  const sortedRuns = [...runs].sort(
    (a, b) =>
      new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime()
  )

  const stagePolicy = effectivePolicies.find((p) => p.stage === seed.stage)
  const stageAgent = resolveStageRunAgent(seed.stage, stagePolicy, agents)
  const stageAgentHandle = stageAgent ? `@${stageAgent.handle}` : ({ ideate: '@ideator', plan: '@planner', execute: '@coder', review: '@reviewer', merge: '@merger' }[seed.stage] || '@coder')

  const totalRunCost = runs.reduce((acc, r) => acc + parseFloat(r.cost || '0'), 0)
  const totalInputTokens = runs.reduce((acc, r) => acc + (r.input_tokens || 0), 0)
  const totalOutputTokens = runs.reduce((acc, r) => acc + (r.output_tokens || 0), 0)
  const totalTokens = totalInputTokens + totalOutputTokens
  const peakContextWindow = runs.reduce<ReturnType<typeof resolveRunContextWindow>>(
    (best, r) => {
      const cw = resolveRunContextWindow(r)
      if (!cw || (cw.peak_tokens <= 0 && cw.used_tokens <= 0)) return best
      if (!best) return cw
      const cwPeakPct = (Math.max(cw.peak_tokens, cw.used_tokens) / cw.max_tokens) * 100
      const bestPeakPct =
        (Math.max(best.peak_tokens, best.used_tokens) / best.max_tokens) * 100
      return cwPeakPct > bestPeakPct ? cw : best
    },
    null
  )

  const handleCancelRun = async (runId: string) => {
    setCancellingId(runId)
    setError(null)
    try {
      await api.runs.cancel(runId)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.runs }),
        queryClient.invalidateQueries({ queryKey: queryKeys.seedRuns(seed.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({ queryKey: queryKeys.seedDetail(seed.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to cancel run.')
    } finally {
      setCancellingId(null)
    }
  }

  return (
    <div className="space-y-4">
      {sortedRuns.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs p-4 sm:p-5 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex flex-wrap items-center gap-4 text-xs sm:text-sm">
            <div>
              <span className="text-zinc-500">Total Runs:</span>{' '}
              <span className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">
                {runs.length}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Total Spend:</span>{' '}
              <span className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">
                {formatUsd(totalRunCost)}
              </span>
            </div>
            <div>
              <span className="text-zinc-500">Tokens Used:</span>{' '}
              <span className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">
                {totalTokens.toLocaleString()}
              </span>
              {totalTokens > 0 && (
                <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400 ml-1.5">
                  (In: {totalInputTokens.toLocaleString()} · Out:{' '}
                  {totalOutputTokens.toLocaleString()})
                </span>
              )}
            </div>
            {peakContextWindow && (
              <div className="flex items-center gap-1.5">
                <span className="text-zinc-500">Peak Context:</span>
                <ContextWindowBadge
                  contextWindow={peakContextWindow}
                  usePeak
                />
              </div>
            )}
          </div>

          {canContribute && seed.status === 'failed' && (
            <Button
              size="sm"
              variant="primary"
              onPress={() => onDispatchRun(null)}
              isDisabled={isDispatching || !stageAgent}
              className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              {isDispatching ? 'Retrying...' : 'Retry'}
            </Button>
          )}
        </div>
      )}

      {error && (
        <div className="rounded-xl border border-rose-500/30 bg-rose-500/10 px-3.5 py-2.5 text-xs text-rose-700 dark:text-rose-300">
          {error}
        </div>
      )}

      {/* Runs List */}
      {sortedRuns.length === 0 ? (
        <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs py-10 px-4 text-center flex flex-col items-center gap-2">
          <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
            <Terminal size={20} />
          </div>
          <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
            No runs yet
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
            {stageAgentHandle} starts when a runner is free.
          </p>
          {canContribute && seed.status === 'failed' && (
            <Button
              size="sm"
              variant="primary"
              onPress={() => onDispatchRun(null)}
              isDisabled={isDispatching || !stageAgent}
              className="mt-1 rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-teal-600/20 cursor-pointer"
            >
              <RotateCcw className="w-3.5 h-3.5" />
              {isDispatching ? 'Retrying...' : 'Retry'}
            </Button>
          )}
        </div>
      ) : (
        <div className="space-y-2.5">
          {sortedRuns.map((run) => {
            const statusMeta = getRunStatusMeta(run.status)
            const agent = agents.find((a) => a.id === run.agent_id)
            const compute = backends.find((b) => b.id === run.compute_backend_id)
            const task = run.task_id ? tasks.find((t) => t.id === run.task_id) : null
            const isActive = ACTIVE_RUN_STATUSES.has(run.status)
            const isStreamingLogs =
              run.status === 'starting' || run.status === 'running'
            const out = (run.output || {}) as Record<string, unknown>
            const storedLogTail =
              typeof out.log_tail === 'string' ? out.log_tail.trim() : ''
            const liveLogTail = (liveLogsByRunId[run.id] || '').trim()
            // Strip terminal color codes ("[0m" etc.) for both raw and structured views
            const logTail = stripAnsi(
              liveLogTail.length >= storedLogTail.length
                ? liveLogTail
                : storedLogTail
            )
            const canToggleLogs = Boolean(logTail) || isActive
            const branchName =
              typeof out.branch_name === 'string' ? out.branch_name.trim() : ''
            const commitSha =
              typeof out.commit_sha === 'string'
                ? out.commit_sha.trim()
                : typeof out.merge_commit_sha === 'string'
                  ? out.merge_commit_sha.trim()
                  : ''
            const prUrl =
              typeof out.pull_request_url === 'string'
                ? out.pull_request_url.trim()
                : ''
            const reviewApproved =
              typeof out.approved === 'boolean' ? out.approved : null
            const isLogsOpen = isRunLogsOpen(run, Boolean(logTail))

            return (
              <div
                key={run.id}
                className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs p-4 space-y-2.5"
              >
                <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-2.5">
                  <div className="flex flex-wrap items-center gap-2 min-w-0">
                    <span
                      className={`inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-md border text-xs font-semibold ${statusMeta.classes}`}
                    >
                      {statusMeta.icon}
                      {statusMeta.label}
                    </span>

                    <span className="inline-flex items-center px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-xs font-mono text-zinc-700 dark:text-zinc-300">
                      {STAGE_LABELS[run.stage]} · Attempt #{run.attempt}
                    </span>

                    {agent && (
                      <span className="inline-flex items-center gap-1 text-xs font-mono font-medium text-teal-600 dark:text-teal-400">
                        <Bot className="w-3.5 h-3.5" />@{agent.handle}
                      </span>
                    )}

                    {task && (
                      <span className="inline-flex items-center px-2 py-0.5 rounded-md border border-zinc-200 dark:border-zinc-700/80 text-xs text-zinc-600 dark:text-zinc-300">
                        Task: {task.title}
                      </span>
                    )}

                    {branchName &&
                      (primaryRepo ? (
                        <span className="inline-flex items-center gap-1">
                          <button
                            type="button"
                            onClick={() =>
                              navigateToDetail(
                                VIEWS.PROJECTS,
                                project?.slug || seed.project_id,
                                'code',
                                {
                                  repo: String(primaryRepo.id),
                                  ref: branchName,
                                }
                              )
                            }
                            title={`Browse branch ${branchName} in Code viewer`}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/20 text-xs font-mono hover:bg-teal-500/20 transition cursor-pointer"
                          >
                            <GitBranch className="w-3 h-3" />
                            <span>{branchName}</span>
                          </button>
                          <a
                            href={buildRepoWebUrl(
                              primaryRepo,
                              primaryGitBackend,
                              {
                                ref: branchName,
                              }
                            )}
                            target="_blank"
                            rel="noopener noreferrer"
                            title={`Open branch ${branchName} on remote repository`}
                            className="p-0.5 text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 transition"
                          >
                            <ExternalLink className="w-3 h-3" />
                          </a>
                        </span>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/20 text-xs font-mono">
                          <GitBranch className="w-3 h-3" />
                          {branchName}
                        </span>
                      ))}

                    {commitSha &&
                      (primaryRepo ? (
                        <a
                          href={buildRepoWebUrl(primaryRepo, primaryGitBackend, {
                            commitSha,
                          })}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`Open commit ${commitSha} on remote repository`}
                          className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/80 text-zinc-700 dark:text-zinc-300 text-xs font-mono transition"
                        >
                          <GitCommit className="w-3 h-3" />
                          <span>{commitSha.slice(0, 7)}</span>
                          <ExternalLink className="w-2.5 h-2.5 opacity-75" />
                        </a>
                      ) : (
                        <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 text-xs font-mono">
                          <GitCommit className="w-3 h-3" />
                          {commitSha.slice(0, 7)}
                        </span>
                      ))}

                    {prUrl && (
                      <a
                        href={prUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-sky-500/10 text-sky-700 dark:text-sky-300 border border-sky-500/20 text-xs font-medium hover:underline"
                      >
                        <ExternalLink className="w-3 h-3" />
                        PR
                      </a>
                    )}

                    {reviewApproved !== null && (
                      <span
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold border ${
                          reviewApproved
                            ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/20'
                            : 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/20'
                        }`}
                      >
                        {reviewApproved ? 'Review Approved' : 'Changes Requested by Agent'}
                      </span>
                    )}
                  </div>

                  <div className="flex items-center gap-3 text-xs text-zinc-500 shrink-0 flex-wrap sm:justify-end">
                    <ContextWindowBadge
                      contextWindow={resolveRunContextWindow(run)}
                      usePeak={run.status !== 'running' && run.status !== 'starting'}
                      showTokens={false}
                    />
                    <span className="font-mono font-semibold text-zinc-800 dark:text-zinc-200 whitespace-nowrap">
                      {formatUsd(run.cost)}
                    </span>
                    <span
                      className="font-mono whitespace-nowrap"
                      title={`Cumulative run tokens · Input: ${(run.input_tokens || 0).toLocaleString()} · Output: ${(run.output_tokens || 0).toLocaleString()}`}
                    >
                      {formatTokens((run.input_tokens || 0) + (run.output_tokens || 0))} tok
                    </span>
                    <span className="whitespace-nowrap">
                      {formatRelativeTime(run.started_at || run.created_at)}
                    </span>

                    {canToggleLogs && (
                      <button
                        type="button"
                        onClick={() => toggleRunLogs(run, Boolean(logTail))}
                        className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-lg border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-800/80 text-xs font-medium text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer whitespace-nowrap shrink-0"
                      >
                        {isStreamingLogs ? (
                          <span className="w-2 h-2 rounded-full bg-teal-500 animate-pulse shrink-0" />
                        ) : (
                          <Terminal className="w-3.5 h-3.5 text-teal-500 shrink-0" />
                        )}
                        <span>
                          {isLogsOpen
                            ? 'Hide Logs'
                            : isStreamingLogs
                              ? 'Live Logs'
                              : 'View Logs'}
                        </span>
                        {isLogsOpen ? (
                          <ChevronDown className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
                        ) : (
                          <ChevronRight className="w-3.5 h-3.5 text-zinc-400 shrink-0" />
                        )}
                      </button>
                    )}

                    {canContribute && isActive && (
                      <Button
                        size="sm"
                        variant="secondary"
                        onPress={() => handleCancelRun(run.id)}
                        isDisabled={cancellingId === run.id}
                        className="h-7 px-2.5 rounded-lg border border-rose-500/30 bg-rose-500/10 text-rose-600 dark:text-rose-400 text-xs font-semibold flex items-center gap-1 whitespace-nowrap shrink-0"
                      >
                        <Square className="w-3 h-3 shrink-0" />
                        {cancellingId === run.id ? 'Cancelling...' : 'Cancel'}
                      </Button>
                    )}
                  </div>
                </div>

                {/* Runtime / Compute details */}
                <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-500">
                  {run.runtime && (
                    <span className="font-mono">Runtime: {run.runtime}</span>
                  )}
                  {run.model && <span className="font-mono">Model: {run.model}</span>}
                  {compute && (
                    <span className="inline-flex items-center gap-1">
                      <Cpu className="w-3.5 h-3.5" />
                      {compute.name}
                    </span>
                  )}
                  <span className="font-mono">
                    In: {run.input_tokens.toLocaleString()} / Out:{' '}
                    {run.output_tokens.toLocaleString()}
                  </span>
                </div>

                {run.error && (
                  <div className="rounded-lg border border-rose-500/25 bg-rose-500/5 px-3 py-2 text-xs font-mono text-rose-700 dark:text-rose-300 whitespace-pre-wrap">
                    {run.error}
                  </div>
                )}

                {canToggleLogs && isLogsOpen && (() => {
                  const isRawMode = Boolean(rawModeByRunId[run.id])
                  const parsedEntries: ParsedRunLogEntry[] =
                    !isRawMode && logTail
                      ? parseRunLogStream(logTail, isStreamingLogs)
                      : []
                  const toolCallCount = parsedEntries.filter(
                    (e) => e.kind === 'tool'
                  ).length

                  return (
                    <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 text-zinc-800 dark:text-zinc-200 overflow-hidden">
                      <div className="flex flex-wrap items-center justify-between gap-2 px-3.5 py-2 border-b border-zinc-200 dark:border-zinc-800 bg-zinc-100/90 dark:bg-zinc-900/90 text-xs font-mono text-zinc-600 dark:text-zinc-400">
                        <span className="flex items-center gap-2 min-w-0">
                          <Terminal className="w-3.5 h-3.5 text-teal-600 dark:text-teal-400 shrink-0" />
                          <span className="truncate font-medium">
                            Runner &amp; Agent Output ({run.id})
                          </span>
                          {toolCallCount > 0 && !isRawMode && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-zinc-200/80 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 text-xs font-semibold shrink-0">
                              <Wrench className="w-3 h-3" />
                              {toolCallCount} {toolCallCount === 1 ? 'tool' : 'tools'}
                            </span>
                          )}
                          {isStreamingLogs && (
                            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-teal-500/15 text-teal-700 dark:text-teal-300 text-xs font-semibold shrink-0">
                              <span className="w-1.5 h-1.5 rounded-full bg-teal-500 animate-pulse" />
                              LIVE
                            </span>
                          )}
                        </span>

                        <div className="flex items-center gap-2 shrink-0">
                          {logTail && (
                            <div className="inline-flex items-center rounded-lg border border-zinc-200/90 dark:border-zinc-700/80 bg-white/70 dark:bg-zinc-950/70 p-0.5">
                              <button
                                type="button"
                                onClick={() =>
                                  setRawModeByRunId((prev) => ({
                                    ...prev,
                                    [run.id]: false,
                                  }))
                                }
                                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors cursor-pointer ${
                                  !isRawMode
                                    ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                                    : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                                }`}
                              >
                                <ListTree className="w-3.5 h-3.5" />
                                <span>Structured</span>
                              </button>
                              <button
                                type="button"
                                onClick={() =>
                                  setRawModeByRunId((prev) => ({
                                    ...prev,
                                    [run.id]: true,
                                  }))
                                }
                                className={`inline-flex items-center gap-1.5 px-2.5 py-1 rounded-md text-xs font-semibold transition-colors cursor-pointer ${
                                  isRawMode
                                    ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                                    : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                                }`}
                              >
                                <Code2 className="w-3.5 h-3.5" />
                                <span>Raw</span>
                              </button>
                            </div>
                          )}

                          {logTail && (
                            <button
                              type="button"
                              onClick={() => handleCopyLogs(run.id, logTail)}
                              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-zinc-200/80 dark:border-zinc-700/80 bg-white/70 dark:bg-zinc-950/70 hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-xs font-semibold text-zinc-600 dark:text-zinc-300 transition-colors cursor-pointer"
                            >
                              {copiedRunId === run.id ? (
                                <>
                                  <Check className="w-3.5 h-3.5 text-emerald-600 dark:text-emerald-400" />
                                  <span className="text-emerald-600 dark:text-emerald-400">
                                    Copied
                                  </span>
                                </>
                              ) : (
                                <>
                                  <Copy className="w-3.5 h-3.5" />
                                  <span>Copy</span>
                                </>
                              )}
                            </button>
                          )}
                        </div>
                      </div>

                      {!logTail ? (
                        <div className="p-3.5 text-xs font-mono text-zinc-500 dark:text-zinc-400">
                          {run.status === 'queued'
                            ? 'Run is queued and waiting for an available compute runner...'
                            : 'Waiting for container stdout/stderr stream...'}
                        </div>
                      ) : isRawMode || parsedEntries.length === 0 ? (
                        <pre
                          ref={(el) => {
                            logPreRefs.current[run.id] = el
                          }}
                          className="p-3.5 text-xs font-mono leading-relaxed whitespace-pre-wrap break-words max-h-96 overflow-y-auto"
                        >
                          {logTail}
                        </pre>
                      ) : (
                        <div
                          ref={(el) => {
                            logPreRefs.current[run.id] = el
                          }}
                          className="p-3 space-y-2 max-h-[28rem] overflow-y-auto text-xs"
                        >
                          {parsedEntries.map((entry) => {
                            const stepKey = `${run.id}:${entry.id}`
                            const isStepExpanded = Boolean(
                              expandedStepsByKey[stepKey]
                            )
                            const hasDetail = Boolean(entry.detail)

                            if (entry.kind === 'tool') {
                              const catVisual = getToolCategoryVisuals(
                                entry.toolCategory
                              )
                              return (
                                <div
                                  key={entry.id}
                                  className="rounded-lg border border-zinc-200/80 dark:border-zinc-800/90 bg-white/90 dark:bg-zinc-900/70 overflow-hidden"
                                >
                                  <button
                                    type="button"
                                    onClick={() => {
                                      if (!hasDetail) return
                                      setExpandedStepsByKey((prev) => ({
                                        ...prev,
                                        [stepKey]: !prev[stepKey],
                                      }))
                                    }}
                                    className={`w-full flex items-center justify-between gap-2.5 px-3 py-2 text-left transition-colors ${
                                      hasDetail
                                        ? 'hover:bg-zinc-100/80 dark:hover:bg-zinc-800/60 cursor-pointer'
                                        : 'cursor-default'
                                    }`}
                                  >
                                    <div className="flex items-center gap-2 min-w-0 flex-1">
                                      <span
                                        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-md border text-xs font-mono font-semibold shrink-0 ${catVisual.badgeClasses}`}
                                      >
                                        {catVisual.icon}
                                        <span>{entry.toolName || entry.title}</span>
                                      </span>
                                      {entry.subtitle && (
                                        <span className="font-mono text-xs text-zinc-700 dark:text-zinc-300 truncate">
                                          {entry.subtitle}
                                        </span>
                                      )}
                                    </div>

                                    <div className="flex items-center gap-1.5 shrink-0">
                                      {entry.toolStatus === 'running' ? (
                                        <span className="inline-flex items-center gap-1 text-xs font-mono text-teal-600 dark:text-teal-400">
                                          <Loader2 className="w-3.5 h-3.5 animate-spin" />
                                          <span>running</span>
                                        </span>
                                      ) : entry.toolStatus === 'error' ? (
                                        <span className="inline-flex items-center gap-1 text-xs font-mono text-rose-600 dark:text-rose-400">
                                          <XCircle className="w-3.5 h-3.5" />
                                          <span>error</span>
                                        </span>
                                      ) : (
                                        <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500/90" />
                                      )}
                                      {hasDetail &&
                                        (isStepExpanded ? (
                                          <ChevronDown className="w-3.5 h-3.5 text-zinc-400" />
                                        ) : (
                                          <ChevronRight className="w-3.5 h-3.5 text-zinc-400" />
                                        ))}
                                    </div>
                                  </button>

                                  {isStepExpanded && entry.detail && (
                                    <pre className="px-3 py-2.5 border-t border-zinc-200/70 dark:border-zinc-800 bg-zinc-50/90 dark:bg-zinc-950/90 text-xs font-mono text-zinc-600 dark:text-zinc-300 whitespace-pre-wrap break-words max-h-60 overflow-y-auto leading-relaxed">
                                      {entry.detail}
                                    </pre>
                                  )}
                                </div>
                              )
                            }

                            if (entry.kind === 'thinking') {
                              return (
                                <div
                                  key={entry.id}
                                  className="rounded-lg border border-zinc-200/60 dark:border-zinc-800/70 bg-zinc-100/50 dark:bg-zinc-900/40 px-3 py-2"
                                >
                                  <div
                                    onClick={() => {
                                      if (!hasDetail) return
                                      setExpandedStepsByKey((prev) => ({
                                        ...prev,
                                        [stepKey]: !prev[stepKey],
                                      }))
                                    }}
                                    className={`flex items-start gap-2 ${
                                      hasDetail ? 'cursor-pointer' : ''
                                    }`}
                                  >
                                    <Sparkles className="w-3.5 h-3.5 text-amber-500/80 shrink-0 mt-0.5" />
                                    <div className="min-w-0 flex-1 text-xs italic text-zinc-600 dark:text-zinc-400 leading-relaxed whitespace-pre-wrap break-words">
                                      {isStepExpanded && entry.detail
                                        ? entry.detail
                                        : entry.subtitle || entry.title}
                                    </div>
                                    {hasDetail && (
                                      <span className="text-xs font-mono font-medium text-teal-600 dark:text-teal-400 shrink-0">
                                        {isStepExpanded ? 'Less' : 'More'}
                                      </span>
                                    )}
                                  </div>
                                </div>
                              )
                            }

                            if (entry.kind === 'stage_output') {
                              return (
                                <div
                                  key={entry.id}
                                  className="rounded-lg border border-teal-500/30 bg-teal-500/5 dark:bg-teal-500/10 px-3 py-2.5 space-y-1.5"
                                >
                                  <div className="flex items-center justify-between gap-2">
                                    <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-teal-700 dark:text-teal-300">
                                      <CheckCircle2 className="w-3.5 h-3.5 text-teal-500 shrink-0" />
                                      <span>{entry.title}</span>
                                    </span>
                                    {hasDetail && (
                                      <button
                                        type="button"
                                        onClick={() =>
                                          setExpandedStepsByKey((prev) => ({
                                            ...prev,
                                            [stepKey]: !prev[stepKey],
                                          }))
                                        }
                                        className="text-xs font-mono font-medium text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                                      >
                                        {isStepExpanded ? 'Hide JSON' : 'View JSON'}
                                      </button>
                                    )}
                                  </div>
                                  {entry.subtitle && (
                                    <p className="text-xs sm:text-sm text-zinc-700 dark:text-zinc-200 whitespace-pre-wrap leading-relaxed">
                                      {entry.subtitle}
                                    </p>
                                  )}
                                  {isStepExpanded && entry.detail && (
                                    <pre className="p-2.5 rounded border border-teal-500/20 bg-white/80 dark:bg-zinc-950/80 text-xs font-mono text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap break-words max-h-60 overflow-y-auto leading-relaxed">
                                      {entry.detail}
                                    </pre>
                                  )}
                                </div>
                              )
                            }

                            if (entry.kind === 'error') {
                              return (
                                <div
                                  key={entry.id}
                                  className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 flex items-start gap-2 text-rose-700 dark:text-rose-300 font-mono text-xs"
                                >
                                  <XCircle className="w-3.5 h-3.5 text-rose-500 shrink-0 mt-0.5" />
                                  <span className="whitespace-pre-wrap break-words flex-1">
                                    {entry.title}
                                  </span>
                                </div>
                              )
                            }

                            if (entry.kind === 'system') {
                              return (
                                <div
                                  key={entry.id}
                                  className="flex items-center gap-2 px-2.5 py-1 text-xs font-mono text-zinc-500 dark:text-zinc-400"
                                >
                                  <span className="w-1.5 h-1.5 rounded-full bg-zinc-400 dark:bg-zinc-600 shrink-0" />
                                  <span className="truncate flex-1">{entry.title}</span>
                                  {entry.timestamp && (
                                    <span className="text-xs text-zinc-400 dark:text-zinc-500 shrink-0">
                                      {entry.timestamp}
                                    </span>
                                  )}
                                </div>
                              )
                            }

                            return (
                              <div
                                key={entry.id}
                                className="rounded-lg border border-zinc-200/70 dark:border-zinc-800/80 bg-white/75 dark:bg-zinc-900/50 px-3 py-2 flex items-start gap-2"
                              >
                                <Bot className="w-3.5 h-3.5 text-teal-600 dark:text-teal-400 shrink-0 mt-0.5" />
                                <div className="min-w-0 flex-1 text-xs sm:text-sm text-zinc-800 dark:text-zinc-200 whitespace-pre-wrap break-words leading-relaxed">
                                  {isStepExpanded && entry.detail
                                    ? entry.detail
                                    : entry.title}
                                  {hasDetail && (
                                    <button
                                      type="button"
                                      onClick={() =>
                                        setExpandedStepsByKey((prev) => ({
                                          ...prev,
                                          [stepKey]: !prev[stepKey],
                                        }))
                                      }
                                      className="ml-2 text-xs font-mono font-medium text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                                    >
                                      {isStepExpanded ? 'Show less' : 'Show more'}
                                    </button>
                                  )}
                                </div>
                              </div>
                            )
                          })}
                        </div>
                      )}
                    </div>
                  )
                })()}
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
