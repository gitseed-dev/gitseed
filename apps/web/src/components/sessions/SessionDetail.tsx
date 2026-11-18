import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Bot,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Code2,
  Columns2,
  Copy,
  ExternalLink,
  FlaskConical,
  GitBranch,
  Loader2,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  Package,
  PanelBottom,
  Pencil,
  Play,
  Sprout,
  Square,
  Terminal,
  Trash2,
  X,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { dockKey, useOptionalSessionDock } from '../../context/SessionDockContext'
import { useTheme } from '../../context/ThemeContext'
import { resolveVsCodeIframeProps, safeExternalUrl } from '../../utils/gitUrls'
import { resolveProjectCloneUrl } from '../../utils/seeds'
import {
  SandboxArtifactsList,
  SandboxSessionInspector,
  dedupeArtifacts,
  getSandboxStatusBadge,
} from '../details/project/SandboxSessionInspector'
import { SessionTerminal, type SessionTerminalHandle } from '../details/project/SessionTerminal'
import { SessionPushDialog } from '../details/project/SessionPushDialog'
import { ContextWindowBadge } from '../common/ContextWindowBadge'
import { SessionEditorPane } from './SessionEditorPane'
import { canStartSessions } from './SessionStartControl'
import type { AgentSession, Project, SandboxSession, SessionVsCodeInfo } from '../../types'

function clockLabel(iso?: string | null): string {
  const d = iso ? new Date(iso) : new Date()
  return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
}

export function terminalDisplayName(s: SandboxSession): string {
  if (s.title) return s.title
  if (s.kind !== 'terminal') return `Run · ${s.command}`
  return `Terminal · ${s.ref} · ${clockLabel(s.started_at)}`
}

function minutesLeft(iso?: string | null): number | null {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  return Number.isFinite(ms) ? Math.max(0, Math.round(ms / 60000)) : null
}

function formatTimeLeft(mins: number): string {
  if (mins >= 60) {
    const rem = mins % 60
    if (rem === 0 || (mins >= 60 && (rem < 5 || rem > 55))) {
      return `${Math.round(mins / 60)}h`
    }
    return `${Math.floor(mins / 60)}h ${rem}m`
  }
  return `${mins}m`
}

function isActiveStatus(status: string): boolean {
  return status === 'starting' || status === 'running' || status === 'running_turn'
}

const INACTIVE_SESSION_STATUSES = new Set([
  'sleeping',
  'stopped',
  'terminated',
  'failed',
  'completed',
  'archived',
])

function isAgentContainerActive(s: AgentSession | undefined): boolean {
  return Boolean(s?.container_id && !INACTIVE_SESSION_STATUSES.has(s.status))
}

/** Agent sessions run the agent's CLI in a terminal; status reflects its container. */
function agentStatus(s: AgentSession): { label: string; className: string; dotClass: string; spin: boolean } {
  if (s.status === 'starting')
    return {
      label: 'Starting',
      className: 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/30',
      dotClass: 'bg-teal-500 animate-pulse',
      spin: true,
    }
  if (s.status === 'failed')
    return {
      label: 'Error',
      className: 'bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30',
      dotClass: 'bg-rose-500',
      spin: false,
    }
  if (isAgentContainerActive(s))
    return {
      label: 'Running',
      className: 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
      dotClass: 'bg-emerald-500',
      spin: false,
    }
  return {
    label: 'Asleep',
    className: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700',
    dotClass: 'bg-zinc-400',
    spin: false,
  }
}

interface SessionMoreMenuProps {
  refName: string
  project: Project
  isFullscreen: boolean
  onToggleFullscreen: () => void
  onMinimize?: () => void
  onRunTests?: () => void
  vscodeUrl?: string | null
  onOpenVsCode?: () => void
  vscodeLoading?: boolean
  onDelete?: () => Promise<void>
}

/** ⋯ menu: secondary session utilities (Check out locally, Dock, Fullscreen, Run tests, Delete). */
const SessionMoreMenu: React.FC<SessionMoreMenuProps> = ({
  refName,
  project,
  isFullscreen,
  onToggleFullscreen,
  onMinimize,
  onRunTests,
  vscodeUrl,
  onOpenVsCode,
  vscodeLoading,
  onDelete,
}) => {
  const [open, setOpen] = useState(false)
  const [showCheckout, setShowCheckout] = useState(false)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [confirming, setConfirming] = useState(false)
  const [deleting, setDeleting] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  const { projectReposByProject, backends } = useData()
  const branch = (refName || 'main').trim()
  const checkoutCmd = `git fetch origin ${branch} && git checkout ${branch}`
  const cloneUrl = resolveProjectCloneUrl(
    project,
    projectReposByProject[project.id],
    backends
  )
  const cloneCmd = cloneUrl ? `git clone -b ${branch} ${cloneUrl}` : null

  const copyText = (key: string, text: string) => {
    void navigator.clipboard?.writeText(text)
    setCopiedKey(key)
    window.setTimeout(() => {
      setCopiedKey((curr) => (curr === key ? null : curr))
    }, 1800)
  }

  useEffect(() => {
    if (!open) {
      setConfirming(false)
      setShowCheckout(false)
      return
    }
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [open])

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="More actions"
        className="p-1.5 rounded-lg text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
      >
        <MoreHorizontal size={16} />
      </button>
      {open && (
        <div
          className={`absolute right-0 top-full mt-1.5 z-40 ${
            showCheckout ? 'w-96 max-w-[calc(100vw-2rem)] p-3' : 'w-60 p-1.5'
          } rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-xl text-xs flex flex-col gap-1`}
        >
          {showCheckout ? (
            <div className="flex flex-col gap-2.5">
              <div className="flex items-center justify-between gap-2">
                <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                  Check out locally
                </span>
                <button
                  type="button"
                  onClick={() => setShowCheckout(false)}
                  className="text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                >
                  ← Back
                </button>
              </div>
              <span className="text-xs text-zinc-500 dark:text-zinc-400">
                Check out <code className="font-mono text-teal-600 dark:text-teal-400">{branch}</code> in your own IDE:
              </span>

              <div className="flex flex-col gap-1">
                <span className="text-xs font-medium text-zinc-500">
                  Existing local clone
                </span>
                <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 font-mono text-xs text-zinc-800 dark:text-zinc-200">
                  <span className="truncate select-all">{checkoutCmd}</span>
                  <button
                    type="button"
                    onClick={() => copyText('checkout', checkoutCmd)}
                    className="shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-xs font-sans font-semibold text-zinc-700 dark:text-zinc-200 hover:text-teal-600 dark:hover:text-teal-300 cursor-pointer"
                  >
                    {copiedKey === 'checkout' ? <Check size={11} /> : <Copy size={11} />}
                    {copiedKey === 'checkout' ? 'Copied' : 'Copy'}
                  </button>
                </div>
              </div>

              {cloneCmd && (
                <div className="flex flex-col gap-1">
                  <span className="text-xs font-medium text-zinc-500">
                    Fresh clone
                  </span>
                  <div className="flex items-center justify-between gap-2 px-2.5 py-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-950 border border-zinc-200 dark:border-zinc-800 font-mono text-xs text-zinc-800 dark:text-zinc-200">
                    <span className="truncate select-all">{cloneCmd}</span>
                    <button
                      type="button"
                      onClick={() => copyText('clone', cloneCmd)}
                      className="shrink-0 inline-flex items-center gap-1 px-2 py-0.5 rounded bg-white dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-xs font-sans font-semibold text-zinc-700 dark:text-zinc-200 hover:text-teal-600 dark:hover:text-teal-300 cursor-pointer"
                    >
                      {copiedKey === 'clone' ? <Check size={11} /> : <Copy size={11} />}
                      {copiedKey === 'clone' ? 'Copied' : 'Copy'}
                    </button>
                  </div>
                </div>
              )}
            </div>
          ) : (
            <>
              <button
                type="button"
                onClick={() => setShowCheckout(true)}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
              >
                <GitBranch size={13} className="text-teal-600 dark:text-teal-400" />
                <span>Check out locally…</span>
              </button>
              {onRunTests && (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false)
                    onRunTests()
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
                >
                  <FlaskConical size={13} className="text-teal-600 dark:text-teal-400" />
                  <span>Run tests</span>
                </button>
              )}
              {(() => {
                const safeVsCodeHref = safeExternalUrl(vscodeUrl)
                return safeVsCodeHref ? (
                  <a
                    href={safeVsCodeHref}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => setOpen(false)}
                    className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
                  >
                    <ExternalLink size={13} className="text-teal-600 dark:text-teal-400" />
                    <span>Open VS Code in new tab</span>
                  </a>
                ) : null
              })()}
              {onOpenVsCode && (
                <button
                  type="button"
                  disabled={vscodeLoading}
                  onClick={() => {
                    setOpen(false)
                    onOpenVsCode()
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer disabled:opacity-60"
                >
                  <Code2 size={13} className="text-teal-600 dark:text-teal-400" />
                  <span>{vscodeLoading ? 'Starting VS Code…' : 'Open in VS Code'}</span>
                </button>
              )}
              {onMinimize && (
                <button
                  type="button"
                  onClick={() => {
                    setOpen(false)
                    onMinimize()
                  }}
                  className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
                >
                  <PanelBottom size={13} className="text-zinc-500" />
                  <span>Dock to bottom</span>
                </button>
              )}
              <button
                type="button"
                onClick={() => {
                  setOpen(false)
                  onToggleFullscreen()
                }}
                className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg text-zinc-700 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
              >
                {isFullscreen ? (
                  <Minimize2 size={13} className="text-zinc-500" />
                ) : (
                  <Maximize2 size={13} className="text-zinc-500" />
                )}
                <span>{isFullscreen ? 'Exit full screen' : 'Full screen'}</span>
              </button>
              {onDelete && (
                <>
                  <div className="my-1 border-t border-zinc-200 dark:border-zinc-800" />
                  {confirming ? (
                    <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/30 flex flex-col gap-2">
                      <span className="text-rose-800 dark:text-rose-200">
                        Delete this session, its container, transcript and artifacts?
                      </span>
                      <div className="flex gap-1.5">
                        <button
                          type="button"
                          disabled={deleting}
                          onClick={async () => {
                            setDeleting(true)
                            try {
                              await onDelete()
                            } finally {
                              setDeleting(false)
                              setOpen(false)
                            }
                          }}
                          className="flex-1 px-2 py-1 rounded-md bg-rose-600 hover:bg-rose-500 text-white font-semibold cursor-pointer disabled:opacity-60"
                        >
                          {deleting ? 'Deleting…' : 'Delete'}
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirming(false)}
                          className="flex-1 px-2 py-1 rounded-md border border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 font-semibold cursor-pointer"
                        >
                          Cancel
                        </button>
                      </div>
                    </div>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setConfirming(true)}
                      className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg hover:bg-rose-500/10 cursor-pointer text-rose-600 dark:text-rose-400"
                    >
                      <Trash2 size={13} />
                      <span>Delete session…</span>
                    </button>
                  )}
                </>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}

/** Shown instead of the terminal for a sleeping agent session: waking it is an explicit choice. */
export const SessionResumePrompt: React.FC<{
  agentHandle?: string | null
  error?: string | null
  onResume: () => void
  className?: string
}> = ({ agentHandle, error, onResume, className = '' }) => (
  <div className={`flex flex-col items-center justify-center gap-3 p-8 text-center bg-zinc-950 text-zinc-300 ${className}`}>
    <span className="text-sm">
      This session is asleep{error ? `: ${error}` : '.'} Its workspace and history are kept.
    </span>
    <button
      type="button"
      onClick={onResume}
      className="inline-flex items-center gap-1.5 px-3.5 py-2 rounded-xl bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold cursor-pointer"
    >
      <Play size={13} />
      {agentHandle ? `Resume @${agentHandle}` : 'Resume session'}
    </button>
  </div>
)

export interface SessionDetailProps {
  project: Project
  kind: 'chat' | 'terminal'
  sessionId: string
  /** Called after the session is deleted */
  onDeleted?: () => void
}

/**
 * One session: a shell (`terminal`) or a coding agent's own CLI (`chat` kind, shown as a
 * terminal running OpenCode / Claude Code / agy …). Conversational Q&A lives in the
 * search/ask overlay instead.
 */
export const SessionDetail: React.FC<SessionDetailProps> = ({ project, kind, sessionId, onDeleted }) => {
  const { user } = useAuth()
  const { users, navigateToDetail, setActiveView } = useData()
  const { theme } = useTheme()
  const sessionDock = useOptionalSessionDock()
  const queryClient = useQueryClient()
  const canContribute = canStartSessions(user, project)

  const [actionError, setActionError] = useState<string | null>(null)
  const [pushOpen, setPushOpen] = useState(false)
  const [pushNotice, setPushNotice] = useState<{ id: string; number: number; prUrl: string | null } | null>(null)
  const [vscodeLoading, setVscodeLoading] = useState(false)
  const [vscodeInfo, setVscodeInfo] = useState<SessionVsCodeInfo | null>(null)
  const vscodeContainerIdRef = useRef<string | null>(null)
  const [editingTitle, setEditingTitle] = useState(false)
  const [titleDraft, setTitleDraft] = useState('')
  const initialPane = new URLSearchParams(window.location.search).get('pane')
  const initialActivePane: 'code' | 'split' | 'vscode' | 'terminal' | 'artifacts' =
    initialPane === 'code'
      ? 'code'
      : initialPane === 'split'
        ? 'split'
        : initialPane === 'vscode' && canContribute
          ? 'vscode'
          : 'terminal'
  const [activePane, setActivePane] = useState<'code' | 'split' | 'vscode' | 'terminal' | 'artifacts'>(
    initialActivePane
  )
  const [splitRatio, setSplitRatio] = useState<number>(() => {
    try {
      const saved = Number(window.localStorage.getItem('gitseed.sessionDetail.splitRatio'))
      if (Number.isFinite(saved) && saved >= 25 && saved <= 75) return saved
    } catch {
      /* ignore storage errors */
    }
    return 48
  })
  const [showCodeTerminal, setShowCodeTerminal] = useState<boolean>(false)
  const [terminalMounted, setTerminalMounted] = useState<boolean>(
    initialActivePane === 'terminal' || initialActivePane === 'split'
  )
  const [isFullscreen, setIsFullscreen] = useState<boolean>(false)
  const [paneInitialized, setPaneInitialized] = useState(false)
  const [stopping, setStopping] = useState(false)
  const [detectedRoot, setDetectedRoot] = useState(false)
  // A sleeping agent session is only woken when the user asks (Resume), never on page open
  const [resumeRequested, setResumeRequested] = useState(false)
  const [, setTick] = useState(0)
  const [measuredHeight, setMeasuredHeight] = useState<number | null>(null)
  const workspaceRef = useRef<HTMLDivElement | null>(null)
  const splitDragCleanupRef = useRef<(() => void) | null>(null)
  const terminalRef = useRef<SessionTerminalHandle | null>(null)

  useEffect(() => {
    setDetectedRoot(false)
    setVscodeInfo(null)
    vscodeContainerIdRef.current = null
  }, [sessionId])

  useEffect(() => {
    try {
      window.localStorage.setItem('gitseed.sessionDetail.splitRatio', String(splitRatio))
    } catch {
      /* ignore storage errors */
    }
  }, [splitRatio])

  useEffect(() => {
    return () => {
      splitDragCleanupRef.current?.()
    }
  }, [])

  const onSplitDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    splitDragCleanupRef.current?.()
    const container = workspaceRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (rect.width <= 0) return

    const onMove = (ev: MouseEvent) => {
      const pct = ((ev.clientX - rect.left) / rect.width) * 100
      const clamped = Math.min(75, Math.max(25, Math.round(pct * 10) / 10))
      setSplitRatio(clamped)
    }
    const cleanup = () => {
      splitDragCleanupRef.current = null
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
    }
    const onUp = () => {
      cleanup()
    }
    splitDragCleanupRef.current = cleanup
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  const showCode = activePane === 'code'
  const showSplit = activePane === 'split'
  const showVsCode = activePane === 'vscode'
  const showArtifacts = activePane === 'artifacts'
  const showTerminal =
    activePane === 'terminal' || activePane === 'split' || (activePane === 'code' && showCodeTerminal)

  useEffect(() => {
    const t = window.setInterval(() => setTick((x) => x + 1), 30_000)
    return () => window.clearInterval(t)
  }, [])

  useEffect(() => {
    if (showTerminal && !terminalMounted) {
      setTerminalMounted(true)
    }
  }, [showTerminal, terminalMounted])

  useEffect(() => {
    if (!isFullscreen) return
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !editingTitle && !pushOpen) {
        setIsFullscreen(false)
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isFullscreen, editingTitle, pushOpen])

  const detailKey = ['sessionDetail', kind, project.id, sessionId]
  const { data: agentSession, isLoading: loadingAgent } = useQuery({
    queryKey: detailKey,
    queryFn: () => api.projects.getAgentSession(project.id, sessionId),
    enabled: kind === 'chat',
    refetchInterval: (q) => {
      const d = q.state.data as AgentSession | undefined
      return d && d.status === 'starting' ? 1500 : 8000
    },
  })
  const { data: terminal, isLoading: loadingTerminal } = useQuery({
    queryKey: [...detailKey, 'terminal'],
    queryFn: () => api.projects.getSandbox(project.id, sessionId),
    enabled: kind === 'terminal',
    refetchInterval: (q) => {
      const d = q.state.data as SandboxSession | undefined
      return d && isActiveStatus(d.status) ? 3000 : 10000
    },
  })

  const sessionRunningNow =
    kind === 'chat'
      ? isAgentContainerActive(agentSession)
      : Boolean(terminal && isActiveStatus(terminal.status) && !INACTIVE_SESSION_STATUSES.has(terminal.status))

  const currentContainerId =
    kind === 'chat' ? agentSession?.container_id ?? null : terminal?.container_id ?? null
  const loadedSession = kind === 'chat' ? agentSession : terminal

  const { data: workspaceFiles } = useQuery({
    queryKey: ['sessionWorkspaceFiles', kind, project.id, sessionId],
    queryFn: () => api.projects.listSessionFiles(project.id, kind, sessionId),
    enabled: canContribute && sessionRunningNow,
    refetchInterval: sessionRunningNow ? 5000 : false,
  })

  useEffect(() => {
    if (paneInitialized) return
    const t = agentSession?.title || terminal?.title || ''
    if (t) {
      setPaneInitialized(true)
      if (!initialPane && t.startsWith('Editor ·')) {
        setActivePane('code')
      }
    }
  }, [agentSession?.title, terminal?.title, paneInitialized, initialPane])
  // Once awake, forget the resume request so a later sleep shows the Resume prompt again
  const awakeNow = isAgentContainerActive(agentSession)
  useEffect(() => {
    if (awakeNow) setResumeRequested(false)
  }, [awakeNow])

  // Clear stale VS Code URL/iframe whenever the session container becomes inactive or is recreated
  useEffect(() => {
    if (!loadedSession || vscodeLoading) return
    const containerChanged =
      Boolean(vscodeContainerIdRef.current) &&
      Boolean(currentContainerId) &&
      vscodeContainerIdRef.current !== currentContainerId
    if (!sessionRunningNow || containerChanged) {
      if (vscodeInfo) {
        setVscodeInfo(null)
      }
      vscodeContainerIdRef.current = null
      if (!sessionRunningNow && activePane === 'vscode') {
        setActivePane('terminal')
      }
    }
  }, [loadedSession, vscodeLoading, sessionRunningNow, currentContainerId, vscodeInfo, activePane])
  // Only offer "Run tests" when the project explicitly configures a test command in Project Settings
  const testCommand = (project.sandbox_config?.test_command || '').trim() || null

  const refresh = useCallback(async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: ['sessionDetail', kind, project.id, sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['sessionWorkspaceFiles', kind, project.id, sessionId] }),
      queryClient.invalidateQueries({ queryKey: ['sessions'] }),
    ])
  }, [queryClient, kind, project.id, sessionId])

  const startedBy = (id: string | null | undefined) => {
    if (!id) return null
    if (id === user?.id) return 'you'
    const u = users.find((x) => x.id === id)
    return u ? u.preferred_name || u.name || u.email : null
  }

  const minimize = () => {
    if (!sessionDock) return
    setIsFullscreen(false)
    const title =
      kind === 'chat' ? agentSession?.title || 'Agent session' : terminal ? terminalDisplayName(terminal) : 'Terminal'
    sessionDock.dock({
      kind,
      id: sessionId,
      projectId: project.id,
      title,
      agentHandle: agentSession?.agent_handle,
      pane:
        activePane === 'vscode'
          ? 'vscode'
          : activePane === 'split'
            ? 'split'
            : activePane === 'code'
              ? 'code'
              : 'terminal',
      vscodeUrl: vscodeInfo?.url || null,
    })
    // Go back to wherever the user came from; the session stays open in the dock
    if (window.history.length > 1) window.history.back()
    else setActiveView(VIEWS.SESSIONS)
  }

  const deleteSession = async () => {
    try {
      if (kind === 'chat') await api.projects.deleteAgentSession(project.id, sessionId)
      else await api.projects.deleteSandbox(project.id, sessionId)
      sessionDock?.undock(dockKey(kind, sessionId))
      await queryClient.invalidateQueries({ queryKey: ['sessions'] })
      onDeleted?.()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not delete session')
    }
  }

  const stop = async () => {
    setStopping(true)
    try {
      if (kind === 'chat') await api.projects.stopAgentSession(project.id, sessionId)
      else await api.projects.stopSandbox(project.id, sessionId)
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not stop session')
    } finally {
      setStopping(false)
    }
  }

  const saveTitle = async (current: string) => {
    const trimmed = titleDraft.trim()
    setEditingTitle(false)
    if (!trimmed || trimmed === current) return
    try {
      if (kind === 'chat') await api.projects.updateAgentSession(project.id, sessionId, { title: trimmed })
      else await api.projects.updateSandbox(project.id, sessionId, { title: trimmed })
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not rename session')
    }
  }

  const runTests = async () => {
    if (!testCommand) return
    if (terminal?.kind === 'terminal') {
      if (activePane === 'code') {
        setShowCodeTerminal(true)
      } else if (activePane !== 'split') {
        setActivePane('terminal')
      }
      window.setTimeout(() => {
        terminalRef.current?.sendText(`${testCommand}\r`)
      }, 60)
      return
    }
    try {
      await api.projects.sendSandboxInput(project.id, sessionId, { input: testCommand, append_newline: true })
      await refresh()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Could not run tests')
    }
  }

  const openVsCode = useCallback(async (opts?: { popout?: boolean }) => {
    if (!canContribute) return
    setActivePane('vscode')
    if (vscodeInfo?.url && sessionRunningNow) {
      if (opts?.popout) {
        const popoutUrl = safeExternalUrl(resolveVsCodeIframeProps(vscodeInfo.url).url)
        if (popoutUrl) {
          window.open(popoutUrl, '_blank', 'noopener,noreferrer')
        }
      }
      void api.projects.openSessionVsCode(project.id, kind, sessionId, theme).catch(() => {})
      return
    }
    setVscodeLoading(true)
    setActionError(null)
    try {
      const res = await api.projects.openSessionVsCode(project.id, kind, sessionId, theme)
      await refresh()
      const freshSession =
        kind === 'chat'
          ? queryClient.getQueryData<AgentSession>(['sessionDetail', kind, project.id, sessionId])
          : queryClient.getQueryData<SandboxSession>(['sessionDetail', kind, project.id, sessionId, 'terminal'])
      vscodeContainerIdRef.current = freshSession?.container_id ?? currentContainerId
      setVscodeInfo(res)
      if (opts?.popout) {
        const popoutUrl = safeExternalUrl(resolveVsCodeIframeProps(res.url).url)
        if (popoutUrl) {
          window.open(popoutUrl, '_blank', 'noopener,noreferrer')
        }
      }
    } catch (err) {
      setActivePane('terminal')
      setActionError(err instanceof Error ? err.message : 'Could not open VS Code for this session')
    } finally {
      setVscodeLoading(false)
    }
  }, [canContribute, vscodeInfo, sessionRunningNow, project.id, kind, sessionId, theme, refresh, queryClient, currentContainerId])

  useEffect(() => {
    if (
      canContribute &&
      sessionRunningNow &&
      initialActivePane === 'vscode' &&
      activePane === 'vscode' &&
      !vscodeInfo &&
      !vscodeLoading &&
      !actionError
    ) {
      void openVsCode()
    }
  }, [canContribute, sessionRunningNow, initialActivePane, activePane, vscodeInfo, vscodeLoading, actionError, openVsCode])

  // Keep running VS Code color theme synced when GitSeed's theme toggles between dark and light
  useEffect(() => {
    if (!vscodeInfo?.url || !sessionRunningNow || !canContribute) return
    void api.projects.openSessionVsCode(project.id, kind, sessionId, theme).catch(() => {})
  }, [theme, vscodeInfo?.url, sessionRunningNow, canContribute, project.id, kind, sessionId])

  const selectCodePane = () => {
    setActivePane('code')
  }

  const selectTerminalPane = () => {
    setActivePane('terminal')
  }

  const pushedSeedId = kind === 'chat' ? agentSession?.pushed_seed_id : terminal?.pushed_seed_id

  useEffect(() => {
    if (isFullscreen) {
      setMeasuredHeight(null)
      return
    }
    const updateHeight = () => {
      const el = workspaceRef.current
      if (!el) return
      const rect = el.getBoundingClientRect()
      if (rect.top > 0 && window.innerHeight > 0) {
        setMeasuredHeight(Math.max(420, Math.floor(window.innerHeight - rect.top - 20)))
      }
    }
    updateHeight()
    const raf = window.requestAnimationFrame(updateHeight)
    window.addEventListener('resize', updateHeight)
    return () => {
      window.cancelAnimationFrame(raf)
      window.removeEventListener('resize', updateHeight)
    }
  }, [isFullscreen, pushedSeedId, actionError, pushNotice, vscodeInfo, loadingAgent, loadingTerminal])

  if ((kind === 'chat' && loadingAgent) || (kind === 'terminal' && loadingTerminal)) {
    return (
      <Card className="p-12 rounded-2xl border border-zinc-200/80 dark:border-zinc-800/80 flex items-center justify-center gap-2 text-sm text-zinc-500">
        <Loader2 size={16} className="animate-spin" />
        Loading session…
      </Card>
    )
  }

  const session = kind === 'chat' ? agentSession : terminal
  if (!session) {
    return (
      <Card className="p-12 rounded-2xl border border-zinc-200/80 dark:border-zinc-800/80 text-center text-sm text-zinc-500">
        This session no longer exists.
      </Card>
    )
  }

  const name = kind === 'chat' ? (agentSession as AgentSession).title : terminalDisplayName(terminal as SandboxSession)
  const terminalRunning = !!terminal && isActiveStatus(terminal.status) && !INACTIVE_SESSION_STATUSES.has(terminal.status)
  const agentAwake = isAgentContainerActive(agentSession)
  const isRunning = kind === 'chat' ? agentAwake : terminalRunning
  const left = terminal && terminalRunning ? minutesLeft(terminal.expires_at) : null
  const canPush = canContribute && isRunning
  // Run tests types into the shell; agent CLIs would treat it as a prompt, so shells only
  const showRunTests =
    canContribute &&
    Boolean(testCommand) &&
    kind === 'terminal' &&
    terminalRunning
  const artifactCount = terminal ? dedupeArtifacts(terminal.artifacts).length : 0

  const statusBadge =
    kind === 'chat'
      ? agentStatus(agentSession as AgentSession)
      : (() => {
          const st = (terminal as SandboxSession).status
          const b = getSandboxStatusBadge(st)
          const dotClass =
            st === 'starting'
              ? 'bg-teal-500 animate-pulse'
              : st === 'running'
                ? 'bg-emerald-500'
                : st === 'failed'
                  ? 'bg-rose-500'
                  : 'bg-zinc-400'
          return { label: b.label, className: b.className, dotClass, spin: st === 'starting' }
        })()

  const startedById = kind === 'chat' ? agentSession?.started_by : terminal?.started_by
  const isRoot = Boolean(terminal?.as_root || detectedRoot)
  const changedFilesCount = workspaceFiles?.changed_files?.length ?? 0
  const hasChanges = changedFilesCount > 0
  const isEditorSession = name.startsWith('Editor ·')
  const canDock = Boolean(
    sessionDock && (kind === 'chat' || (terminal?.kind === 'terminal' && terminalRunning))
  )

  const orderedTabs: Array<'terminal' | 'split' | 'code' | 'vscode' | 'artifacts'> = isEditorSession
    ? [
        'code',
        'split',
        'terminal',
        ...(canContribute ? (['vscode'] as const) : []),
        ...(kind === 'terminal' && artifactCount > 0 ? (['artifacts'] as const) : []),
      ]
    : [
        'terminal',
        'split',
        'code',
        ...(canContribute ? (['vscode'] as const) : []),
        ...(kind === 'terminal' && artifactCount > 0 ? (['artifacts'] as const) : []),
      ]

  const workspaceHeightClass = isFullscreen
    ? 'flex-1 min-h-0 flex flex-col overflow-hidden'
    : 'h-[calc(100vh-14.5rem)] min-h-[420px] flex flex-col overflow-hidden'
  const workspaceStyle =
    !isFullscreen && measuredHeight ? { height: `${measuredHeight}px` } : undefined
  const safePushPrUrl = safeExternalUrl(pushNotice?.prUrl)

  return (
    <div className="flex flex-col gap-3">
      {isFullscreen && (
        <div
          className="fixed inset-0 z-40 bg-black/50 backdrop-blur-xs"
          onClick={() => setIsFullscreen(false)}
        />
      )}
      {pushOpen && (
        <SessionPushDialog
          projectId={project.id}
          kind={kind}
          sessionId={sessionId}
          onClose={() => setPushOpen(false)}
          onPushed={async (result) => {
            setPushOpen(false)
            setPushNotice({ id: result.seed_id, number: result.seed_number, prUrl: result.pull_request_url })
            await refresh()
            await queryClient.invalidateQueries({ queryKey: queryKeys.seeds })
          }}
        />
      )}

      {pushNotice && (
        <div className="p-3 rounded-xl border border-emerald-500/30 bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 text-xs flex items-center justify-between gap-2">
          <span className="flex items-center gap-2 flex-wrap">
            <CheckCircle2 size={14} className="shrink-0" />
            Pushed. Seed #{pushNotice.number} is queued for Review.
            <button type="button" onClick={() => navigateToDetail(VIEWS.SEEDS, pushNotice.id)} className="font-semibold underline cursor-pointer">
              Open seed
            </button>
            {safePushPrUrl && (
              <a href={safePushPrUrl} target="_blank" rel="noopener noreferrer" className="font-semibold underline">
                View pull request
              </a>
            )}
          </span>
          <button type="button" onClick={() => setPushNotice(null)} className="cursor-pointer">
            <X size={14} />
          </button>
        </div>
      )}

      {actionError && (
        <div className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300 text-xs flex items-center justify-between gap-2">
          <span className="flex items-center gap-2">
            <AlertCircle size={14} className="shrink-0" />
            {actionError}
          </span>
          <button type="button" onClick={() => setActionError(null)} className="cursor-pointer">
            <X size={14} />
          </button>
        </div>
      )}

      <Card
        className={
          isFullscreen
            ? 'fixed inset-3 z-50 rounded-2xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-950 shadow-2xl overflow-hidden flex flex-col'
            : 'rounded-2xl border border-zinc-200/80 dark:border-zinc-800/80 bg-white dark:bg-zinc-900/70 overflow-hidden flex flex-col'
        }
      >
        {/* Header */}
        <div className="border-b border-zinc-200/80 dark:border-zinc-800 flex flex-col shrink-0">
          {/* Row 1: Identity & Status on left, Muted metadata on right */}
          <div className="px-4 py-2.5 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-2.5 min-w-0 flex-wrap">
              {kind === 'chat' ? (
                <Bot size={15} className="text-teal-600 dark:text-teal-400 shrink-0" />
              ) : isEditorSession ? (
                <Code2 size={15} className="text-teal-600 dark:text-teal-400 shrink-0" />
              ) : (
                <Terminal size={15} className="text-teal-600 dark:text-teal-400 shrink-0" />
              )}
              {editingTitle ? (
                <span className="flex items-center gap-1.5">
                  <input
                    value={titleDraft}
                    onChange={(e) => setTitleDraft(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') void saveTitle(name)
                      if (e.key === 'Escape') setEditingTitle(false)
                    }}
                    autoFocus
                    className="px-2 py-1 rounded-lg border border-teal-500 bg-white dark:bg-zinc-900 text-sm font-bold text-zinc-900 dark:text-zinc-100"
                  />
                  <button type="button" onClick={() => void saveTitle(name)} className="p-1 rounded-lg bg-teal-600 text-white hover:bg-teal-500 cursor-pointer">
                    <Check size={13} />
                  </button>
                </span>
              ) : (
                <button
                  type="button"
                  disabled={!canContribute}
                  onClick={() => {
                    setTitleDraft(name)
                    setEditingTitle(true)
                  }}
                  className="group inline-flex items-center gap-1.5 text-sm font-bold text-zinc-900 dark:text-zinc-100 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer truncate"
                >
                  <span className="truncate">{name}</span>
                  <Pencil size={11} className="opacity-0 group-hover:opacity-100 text-zinc-400" />
                </button>
              )}
              <span className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded-full text-xs font-semibold border ${statusBadge.className}`}>
                {statusBadge.spin ? (
                  <Loader2 size={10} className="animate-spin" />
                ) : (
                  <span className={`w-1.5 h-1.5 rounded-full ${statusBadge.dotClass}`} />
                )}
                {statusBadge.label}
              </span>
              {isRoot && (
                <span className="px-1.5 py-0.5 rounded text-xs font-mono font-bold bg-zinc-900 text-white dark:bg-zinc-100 dark:text-zinc-900">
                  root
                </span>
              )}
              {kind === 'chat' && agentSession?.agent_handle && (
                <span className="px-2 py-0.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-zinc-50 dark:bg-zinc-800/80 text-xs font-mono font-semibold text-teal-700 dark:text-teal-300">
                  @{agentSession.agent_handle}
                </span>
              )}
              {kind === 'chat' && agentSession?.context_window && (
                <ContextWindowBadge contextWindow={agentSession.context_window} />
              )}
            </div>

            <span className="text-xs font-mono text-zinc-500 dark:text-zinc-400 inline-flex items-center gap-1 flex-wrap">
              <GitBranch size={11} />
              {session.ref}
              {session.head_sha ? `@${session.head_sha.slice(0, 7)}` : ''}
              {terminal && <> · {terminal.image}</>}
              {left !== null && <> · stops in {formatTimeLeft(left)}</>}
              {startedBy(startedById) && <span className="font-sans"> · {startedBy(startedById)}</span>}
            </span>
          </div>

          {/* Row 2: Underlined View Tabs on left, Primary Actions + ⋯ Menu on right */}
          <div className="px-4 border-t border-zinc-200/60 dark:border-zinc-800/70 flex flex-wrap items-center justify-between gap-3">
            <div className="flex items-center gap-5 min-h-[38px]">
              {isRunning ? (
                orderedTabs.map((tab) => {
                  const isActive = activePane === tab
                  const baseTabClass = `inline-flex items-center gap-1.5 py-2 -mb-px border-b-2 text-xs font-semibold transition cursor-pointer disabled:opacity-60 ${
                    isActive
                      ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                      : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200'
                  }`
                  if (tab === 'terminal') {
                    return (
                      <button
                        key="terminal"
                        type="button"
                        onClick={selectTerminalPane}
                        className={baseTabClass}
                      >
                        <Terminal size={13} />
                        <span>Terminal</span>
                      </button>
                    )
                  }
                  if (tab === 'split') {
                    return (
                      <button
                        key="split"
                        type="button"
                        onClick={() => setActivePane('split')}
                        title="Side-by-side Terminal and Code/Diff for this session container"
                        className={baseTabClass}
                      >
                        <Columns2 size={13} />
                        <span>Split</span>
                        {hasChanges && (
                          <span className="px-1.5 rounded-full bg-teal-500/15 text-teal-700 dark:text-teal-300 text-xs font-mono">
                            {changedFilesCount}
                          </span>
                        )}
                      </button>
                    )
                  }
                  if (tab === 'code') {
                    return (
                      <button
                        key="code"
                        type="button"
                        onClick={selectCodePane}
                        className={baseTabClass}
                      >
                        <Code2 size={13} />
                        <span>Code</span>
                      </button>
                    )
                  }
                  if (tab === 'vscode') {
                    return (
                      <button
                        key="vscode"
                        type="button"
                        disabled={vscodeLoading}
                        onClick={() => void openVsCode()}
                        title="Open full VS Code workbench in this session (~0.5–1 GB RAM; container limit raised automatically)"
                        className={baseTabClass}
                      >
                        {vscodeLoading ? <Loader2 size={13} className="animate-spin" /> : <Code2 size={13} />}
                        <span>VS Code</span>
                      </button>
                    )
                  }
                  return (
                    <button
                      key="artifacts"
                      type="button"
                      onClick={() => setActivePane('artifacts')}
                      className={baseTabClass}
                    >
                      <Package size={13} />
                      <span>Artifacts</span>
                      <span className="px-1.5 rounded-full bg-zinc-200 dark:bg-zinc-800 text-xs">
                        {artifactCount}
                      </span>
                    </button>
                  )
                })
              ) : kind === 'terminal' && artifactCount > 0 ? (
                <button
                  type="button"
                  onClick={() => setActivePane('artifacts')}
                  className={`inline-flex items-center gap-1.5 py-2 -mb-px border-b-2 text-xs font-semibold transition cursor-pointer ${
                    activePane === 'artifacts'
                      ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                      : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200'
                  }`}
                >
                  <Package size={13} />
                  <span>Artifacts</span>
                  <span className="px-1.5 rounded-full bg-zinc-200 dark:bg-zinc-800 text-xs">
                    {artifactCount}
                  </span>
                </button>
              ) : null}
            </div>

            <div className="flex items-center gap-2 py-1.5">
              {canPush && (
                <span
                  title={
                    hasChanges
                      ? `Push ${changedFilesCount} changed file${changedFilesCount === 1 ? '' : 's'} to a seed`
                      : 'No changes to push yet'
                  }
                >
                  <button
                    type="button"
                    disabled={!hasChanges}
                    onClick={() => setPushOpen(true)}
                    className={`inline-flex items-center gap-1.5 px-3 py-1 rounded-xl border text-xs font-semibold transition ${
                      hasChanges
                        ? 'border-teal-600 bg-teal-600 hover:bg-teal-500 text-white shadow-2xs cursor-pointer'
                        : 'border-zinc-200 dark:border-zinc-800 bg-zinc-100/70 dark:bg-zinc-800/50 text-zinc-400 dark:text-zinc-500 cursor-not-allowed'
                    }`}
                  >
                    <Sprout size={13} />
                    <span>Push to seed</span>
                    {hasChanges && (
                      <span className="px-1.5 py-0.2 rounded-full bg-white/20 text-xs font-mono">
                        {changedFilesCount}
                      </span>
                    )}
                  </button>
                </span>
              )}
              {canContribute && isRunning && (
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  isDisabled={stopping}
                  onClick={() => void stop()}
                  className="rounded-xl text-xs font-semibold flex items-center gap-1.5 border border-zinc-200 dark:border-zinc-700 cursor-pointer"
                >
                  {stopping ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
                  Stop
                </Button>
              )}
              <SessionMoreMenu
                refName={session.ref}
                project={project}
                isFullscreen={isFullscreen}
                onToggleFullscreen={() => setIsFullscreen((v) => !v)}
                onMinimize={canDock ? minimize : undefined}
                onRunTests={showRunTests ? () => void runTests() : undefined}
                vscodeUrl={
                  canContribute && showVsCode && vscodeInfo?.url
                    ? resolveVsCodeIframeProps(vscodeInfo.url).url
                    : null
                }
                onOpenVsCode={canContribute && !isRunning && kind === 'chat' ? () => void openVsCode() : undefined}
                vscodeLoading={vscodeLoading}
                onDelete={canContribute ? deleteSession : undefined}
              />
            </div>
          </div>
        </div>

        {pushedSeedId && (
          <div className="px-4 py-2.5 text-xs bg-emerald-500/10 text-emerald-800 dark:text-emerald-300 border-b border-emerald-500/20 flex items-center gap-2 shrink-0">
            <CheckCircle2 size={14} className="shrink-0" />
            <span>
              Pushed to a seed.{' '}
              <button type="button" onClick={() => navigateToDetail(VIEWS.SEEDS, pushedSeedId)} className="font-semibold underline cursor-pointer">
                Open seed
              </button>
            </span>
          </div>
        )}

        {/* ------------------------- Agent: Unified Code / Split / VS Code + Docked Terminal ------------------------- */}
        {kind === 'chat' && agentSession && (agentAwake || resumeRequested || agentSession.status === 'starting') && (
          <div
            ref={workspaceRef}
            style={workspaceStyle}
            className={`${workspaceHeightClass} ${showSplit && !showVsCode ? '!flex-row' : ''}`}
          >
            {showVsCode && (
              <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-zinc-950">
                {vscodeLoading || !vscodeInfo?.url ? (
                  <div className="flex-1 flex items-center justify-center gap-2 text-xs text-zinc-400">
                    <Loader2 size={15} className="animate-spin text-teal-400" />
                    <span>Starting VS Code in session workspace…</span>
                  </div>
                ) : (
                  (() => {
                    const resolvedVsCode = resolveVsCodeIframeProps(vscodeInfo.url)
                    const safeHref = safeExternalUrl(resolvedVsCode.url)
                    return (
                      <>
                        {!resolvedVsCode.sandbox.includes('allow-same-origin') && (
                          <div className="px-3 py-1.5 text-xs bg-amber-500/15 text-amber-200 border-b border-amber-500/30 flex items-center justify-between gap-2 shrink-0">
                            <span>
                              Same-origin iframe sandbox active (`allow-same-origin` omitted to protect control-plane cookies). Configure dedicated port 8423 for full VS Code Web storage.
                            </span>
                            {safeHref && (
                              <a
                                href={safeHref}
                                target="_blank"
                                rel="noopener noreferrer"
                                className="font-semibold underline shrink-0"
                              >
                                Open in new tab
                              </a>
                            )}
                          </div>
                        )}
                        <iframe
                          key={`vscode-chat-${agentSession.id}`}
                          src={resolvedVsCode.url}
                          title={`VS Code · ${name}`}
                          sandbox={resolvedVsCode.sandbox}
                          allow="clipboard-read; clipboard-write;"
                          className="flex-1 min-h-0 w-full border-0 bg-zinc-950"
                        />
                      </>
                    )
                  })()
                )}
              </div>
            )}

            {!showVsCode && (showCode || showSplit) && agentAwake && (
              <div
                className={
                  showSplit
                    ? 'order-3 flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden'
                    : 'order-1 flex-1 min-h-0 flex flex-col overflow-hidden'
                }
              >
                <SessionEditorPane
                  projectId={project.id}
                  kind="chat"
                  sessionId={agentSession.id}
                  canContribute={canContribute}
                  isAgentBusy={agentSession.status === 'running_turn' || agentSession.status === 'starting'}
                  agentHandle={agentSession.agent_handle}
                  preferDiffOnInitialChange={showSplit}
                  onOpenPush={canPush ? () => setPushOpen(true) : undefined}
                  onChanged={() => void refresh()}
                  className="flex-1 min-h-0"
                />
              </div>
            )}

            {!showVsCode && showSplit && agentAwake && (
              <div
                onMouseDown={onSplitDragStart}
                onDoubleClick={() => setSplitRatio(48)}
                className="order-2 w-1.5 -mx-px cursor-col-resize bg-zinc-200/80 dark:bg-zinc-800 hover:bg-teal-500/50 transition-colors shrink-0 z-10"
                title="Drag to resize Terminal / Code split · Double-click to reset"
              />
            )}

            {!showVsCode && showCode && (
              <div className="order-2 h-8 px-3 border-t border-zinc-200 dark:border-zinc-800 bg-zinc-50/80 dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 flex items-center justify-between text-xs shrink-0">
                <button
                  type="button"
                  onClick={() => setShowCodeTerminal((v) => !v)}
                  className="inline-flex items-center gap-2 font-mono text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:text-teal-600 dark:hover:text-teal-300 cursor-pointer transition-colors"
                >
                  <Terminal size={12} className="text-teal-600 dark:text-teal-400" />
                  <span>Terminal · @{agentSession.agent_handle}</span>
                </button>
                <div className="flex items-center gap-1">
                  {showCodeTerminal ? (
                    <>
                      <button
                        type="button"
                        onClick={() => setActivePane('split')}
                        title="Split side-by-side with Code"
                        className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1 cursor-pointer transition-colors"
                      >
                        <Columns2 size={11} />
                        <span>Split</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setActivePane('terminal')}
                        title="Maximize terminal"
                        className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1 cursor-pointer transition-colors"
                      >
                        <Maximize2 size={11} />
                        <span>Maximize</span>
                      </button>
                      <button
                        type="button"
                        onClick={() => setShowCodeTerminal(false)}
                        title="Hide terminal"
                        className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1 cursor-pointer transition-colors"
                      >
                        <ChevronDown size={13} />
                        <span>Hide</span>
                      </button>
                    </>
                  ) : (
                    <button
                      type="button"
                      onClick={() => setShowCodeTerminal(true)}
                      className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-teal-600 dark:text-teal-300 hover:text-teal-700 dark:hover:text-teal-200 inline-flex items-center gap-1 font-semibold cursor-pointer transition-colors"
                    >
                      <ChevronUp size={13} />
                      <span>Show terminal</span>
                    </button>
                  )}
                </div>
              </div>
            )}

            {(terminalMounted || showTerminal) && (
              <div
                style={showSplit && agentAwake ? { width: `${splitRatio}%` } : undefined}
                className={
                  !showTerminal || showVsCode
                    ? 'hidden'
                    : showSplit && agentAwake
                      ? 'order-1 shrink-0 min-w-0 min-h-0 flex flex-col'
                      : showCode && agentAwake
                        ? 'order-3 h-[38%] min-h-[200px] max-h-[55%] shrink-0 flex flex-col border-t border-zinc-200 dark:border-zinc-800'
                        : 'order-1 flex-1 min-w-0 min-h-0 flex flex-col'
                }
              >
                <SessionTerminal
                  key={`agent-${agentSession.id}-${agentSession.agent_id}`}
                  ref={terminalRef}
                  url={api.projects.sessionPtyUrl(project.id, 'chat', agentSession.id)}
                  connectingLabel={`Starting @${agentSession.agent_handle}…`}
                  onDisconnected={() => void refresh()}
                  className="flex-1 min-h-0"
                />
              </div>
            )}
          </div>
        )}
        {kind === 'chat' && agentSession && !(agentAwake || resumeRequested || agentSession.status === 'starting') && (
          <SessionResumePrompt
            agentHandle={agentSession.agent_handle}
            error={agentSession.status === 'failed' ? agentSession.error : null}
            onResume={() => setResumeRequested(true)}
            className={workspaceHeightClass}
          />
        )}

        {/* ------------------------------ Shell terminal / Split / Code editor / VS Code ------------------------------ */}
        {kind === 'terminal' && terminal && (
          <>
            {showArtifacts ? (
              <SandboxArtifactsList projectId={project.id} session={terminal} canContribute={canContribute} onChanged={() => void refresh()} />
            ) : terminalRunning ? (
              <div
                ref={workspaceRef}
                style={workspaceStyle}
                className={`${workspaceHeightClass} ${showSplit && !showVsCode ? '!flex-row' : ''}`}
              >
                {showVsCode && (
                  <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-zinc-950">
                    {vscodeLoading || !vscodeInfo?.url ? (
                      <div className="flex-1 flex items-center justify-center gap-2 text-xs text-zinc-400">
                        <Loader2 size={15} className="animate-spin text-teal-400" />
                        <span>Starting VS Code in session workspace…</span>
                      </div>
                    ) : (
                      (() => {
                        const resolvedVsCode = resolveVsCodeIframeProps(vscodeInfo.url)
                        const safeHref = safeExternalUrl(resolvedVsCode.url)
                        return (
                          <>
                            {!resolvedVsCode.sandbox.includes('allow-same-origin') && (
                              <div className="px-3 py-1.5 text-xs bg-amber-500/15 text-amber-200 border-b border-amber-500/30 flex items-center justify-between gap-2 shrink-0">
                                <span>
                                  Same-origin iframe sandbox active (`allow-same-origin` omitted to protect control-plane cookies). Configure dedicated port 8423 for full VS Code Web storage.
                                </span>
                                {safeHref && (
                                  <a
                                    href={safeHref}
                                    target="_blank"
                                    rel="noopener noreferrer"
                                    className="font-semibold underline shrink-0"
                                  >
                                    Open in new tab
                                  </a>
                                )}
                              </div>
                            )}
                            <iframe
                              key={`vscode-terminal-${terminal.id}`}
                              src={resolvedVsCode.url}
                              title={`VS Code · ${name}`}
                              sandbox={resolvedVsCode.sandbox}
                              allow="clipboard-read; clipboard-write;"
                              className="flex-1 min-h-0 w-full border-0 bg-zinc-950"
                            />
                          </>
                        )
                      })()
                    )}
                  </div>
                )}

                {!showVsCode && (showCode || showSplit) && (
                  <div
                    className={
                      showSplit
                        ? 'order-3 flex-1 min-w-0 min-h-0 flex flex-col overflow-hidden'
                        : 'order-1 flex-1 min-h-0 flex flex-col overflow-hidden'
                    }
                  >
                    <SessionEditorPane
                      projectId={project.id}
                      kind="terminal"
                      sessionId={terminal.id}
                      canContribute={canContribute}
                      preferDiffOnInitialChange={showSplit}
                      onOpenPush={canPush ? () => setPushOpen(true) : undefined}
                      onChanged={() => void refresh()}
                      className="flex-1 min-h-0"
                    />
                  </div>
                )}

                {!showVsCode && showSplit && (
                  <div
                    onMouseDown={onSplitDragStart}
                    onDoubleClick={() => setSplitRatio(48)}
                    className="order-2 w-1.5 -mx-px cursor-col-resize bg-zinc-200/80 dark:bg-zinc-800 hover:bg-teal-500/50 transition-colors shrink-0 z-10"
                    title="Drag to resize Terminal / Code split · Double-click to reset"
                  />
                )}

                {!showVsCode && showCode && (
                  <div className="order-2 h-8 px-3 border-t border-zinc-200 dark:border-zinc-800 bg-zinc-50/80 dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 flex items-center justify-between text-xs shrink-0">
                    <button
                      type="button"
                      onClick={() => setShowCodeTerminal((v) => !v)}
                      className="inline-flex items-center gap-2 font-mono text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:text-teal-600 dark:hover:text-teal-300 cursor-pointer transition-colors"
                    >
                      <Terminal size={12} className="text-teal-600 dark:text-teal-400" />
                      <span>Terminal · /workspace</span>
                    </button>
                    <div className="flex items-center gap-1">
                      {showCodeTerminal ? (
                        <>
                          <button
                            type="button"
                            onClick={() => setActivePane('split')}
                            title="Split side-by-side with Code"
                            className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1 cursor-pointer transition-colors"
                          >
                            <Columns2 size={11} />
                            <span>Split</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => setActivePane('terminal')}
                            title="Maximize terminal"
                            className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1 cursor-pointer transition-colors"
                          >
                            <Maximize2 size={11} />
                            <span>Maximize</span>
                          </button>
                          <button
                            type="button"
                            onClick={() => setShowCodeTerminal(false)}
                            title="Hide terminal"
                            className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 inline-flex items-center gap-1 cursor-pointer transition-colors"
                          >
                            <ChevronDown size={13} />
                            <span>Hide</span>
                          </button>
                        </>
                      ) : (
                        <button
                          type="button"
                          onClick={() => setShowCodeTerminal(true)}
                          className="px-2 py-0.5 rounded hover:bg-zinc-200/70 dark:hover:bg-zinc-800 text-teal-600 dark:text-teal-300 hover:text-teal-700 dark:hover:text-teal-200 inline-flex items-center gap-1 font-semibold cursor-pointer transition-colors"
                        >
                          <ChevronUp size={13} />
                          <span>Show terminal</span>
                        </button>
                      )}
                    </div>
                  </div>
                )}

                {(terminalMounted || showTerminal) && (
                  <div
                    style={showSplit ? { width: `${splitRatio}%` } : undefined}
                    className={
                      !showTerminal || showVsCode
                        ? 'hidden'
                        : showSplit
                          ? 'order-1 shrink-0 min-w-0 min-h-0 flex flex-col'
                          : showCode
                            ? 'order-3 h-[38%] min-h-[200px] max-h-[55%] shrink-0 flex flex-col border-t border-zinc-200 dark:border-zinc-800'
                            : 'order-1 flex-1 min-w-0 min-h-0 flex flex-col'
                    }
                  >
                    {terminal.kind === 'terminal' ? (
                      <SessionTerminal
                        key={terminal.id}
                        ref={terminalRef}
                        url={api.projects.sessionPtyUrl(project.id, 'terminal', terminal.id)}
                        onDisconnected={() => void refresh()}
                        onRootDetected={() => setDetectedRoot(true)}
                        className="flex-1 min-h-0"
                      />
                    ) : (
                      <SandboxSessionInspector
                        variant="terminal"
                        projectId={project.id}
                        session={terminal}
                        canContribute={canContribute}
                        stoppingId={stopping ? terminal.id : null}
                        onStopSession={() => void stop()}
                        onSendInput={async (id, text, opts) => {
                          await api.projects.sendSandboxInput(project.id, id, {
                            input: text,
                            append_newline: !opts?.eof,
                            eof: Boolean(opts?.eof),
                          })
                          await refresh()
                        }}
                      />
                    )}
                  </div>
                )}
              </div>
            ) : terminal.kind === 'terminal' ? (
              <div className="p-8 text-center text-sm text-zinc-500 dark:text-zinc-400 flex flex-col items-center gap-2">
                <span>This terminal has ended{terminal.error ? `: ${terminal.error}` : '.'}</span>
                {artifactCount > 0 && (
                  <button type="button" onClick={() => setActivePane('artifacts')} className="text-teal-700 dark:text-teal-300 font-semibold underline cursor-pointer">
                    View artifacts ({artifactCount})
                  </button>
                )}
              </div>
            ) : (
              <SandboxSessionInspector
                variant="terminal"
                projectId={project.id}
                session={terminal}
                canContribute={canContribute}
                stoppingId={stopping ? terminal.id : null}
                onStopSession={() => void stop()}
                onSendInput={async (id, text, opts) => {
                  await api.projects.sendSandboxInput(project.id, id, {
                    input: text,
                    append_newline: !opts?.eof,
                    eof: Boolean(opts?.eof),
                  })
                  await refresh()
                }}
              />
            )}
          </>
        )}
      </Card>
    </div>
  )
}
