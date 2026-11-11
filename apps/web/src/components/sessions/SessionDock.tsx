import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import { Bot, ChevronDown, ChevronUp, Code2, ExternalLink, Loader2, Maximize2, Square, Terminal, X } from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { VIEWS } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { dockKey, useSessionDock, type DockedSession } from '../../context/SessionDockContext'
import { useTheme } from '../../context/ThemeContext'
import { AgentProviderIcon } from '../common/ProviderIcon'
import { ContextWindowBadge } from '../common/ContextWindowBadge'
import { SessionTerminal } from '../details/project/SessionTerminal'
import { SessionResumePrompt } from './SessionDetail'
import { SessionEditorPane } from './SessionEditorPane'
import { canStartSessions } from './SessionStartControl'
import type { SessionListItem } from '../../types'

const HEIGHT_KEY = 'gitseed.sessionDock.height'
const MIN_HEIGHT = 200

function initialHeight(): number {
  const saved = Number(window.localStorage.getItem(HEIGHT_KEY))
  if (Number.isFinite(saved) && saved >= MIN_HEIGHT) return saved
  return Math.round(window.innerHeight * 0.4)
}

function statusDot(item: SessionListItem | undefined): string {
  if (!item) return 'bg-zinc-400'
  if (item.status === 'starting') return 'bg-teal-400 animate-pulse'
  if (item.status === 'failed') return 'bg-rose-500'
  return item.is_running ? 'bg-emerald-500' : 'bg-zinc-400'
}

/**
 * Bottom dock for minimized sessions: slim chips that persist across pages; one expands into
 * a resizable drawer with the live terminal. The terminal only connects while expanded, and
 * the server keeps the process running, so collapsing/expanding resumes where it was.
 */
export const SessionDock: React.FC = () => {
  const { user } = useAuth()
  const { theme } = useTheme()
  const { items, expandedKey, setExpanded, undock, updateTitle, updatePane } = useSessionDock()
  const { projects, agents, backends, activeView, activeDetailId, activeDetailTab, navigateToDetail } = useData()
  const [height, setHeight] = useState<number>(initialHeight)
  const [notice, setNotice] = useState<string | null>(null)
  const [vscodeStartingKey, setVscodeStartingKey] = useState<string | null>(null)
  // Sleeping agent sessions the user explicitly chose to wake (never woken just by expanding)
  const [resumed, setResumed] = useState<Set<string>>(() => new Set())
  const dragRef = useRef<{ startY: number; startH: number } | null>(null)

  // The session whose own page is open is shown there, not duplicated in the dock
  const pageKey =
    activeView === VIEWS.SESSIONS && (activeDetailId === 'chat' || activeDetailId === 'terminal') && activeDetailTab
      ? dockKey(activeDetailId, activeDetailTab)
      : null

  const statuses = useQueries({
    queries: items.map((it) => ({
      queryKey: ['sessions', 'item', it.kind, it.id],
      queryFn: () => api.sessions.get(it.kind, it.id),
      refetchInterval: 15_000,
      retry: (count: number, err: unknown) => !(err instanceof ApiError && err.status === 404) && count < 2,
    })),
  })

  // Drop sessions that were deleted or (terminals) have ended; keep titles fresh
  useEffect(() => {
    items.forEach((it, idx) => {
      const q = statuses[idx]
      const key = dockKey(it.kind, it.id)
      if (q?.error instanceof ApiError && (q.error.status === 404 || q.error.status === 403)) {
        undock(key)
        setNotice(`“${it.title}” is no longer available`)
        return
      }
      const data = q?.data
      if (!data) return
      if (it.kind === 'terminal' && !data.is_running && data.status !== 'starting') {
        undock(key)
        setNotice(`“${data.name}” ended`)
        return
      }
      if (data.name && data.name !== it.title) updateTitle(key, data.name)
      // Awake again: a later sleep should ask before waking, so forget the resume request
      if (data.is_running)
        setResumed((s) => {
          if (!s.has(key)) return s
          const next = new Set(s)
          next.delete(key)
          return next
        })
    })
  }, [items, statuses, undock, updateTitle])

  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), 4000)
    return () => window.clearTimeout(t)
  }, [notice])

  // Ctrl+` toggles the drawer (most recently docked session)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey && e.key === '`')) return
      const candidates = items.filter((i) => dockKey(i.kind, i.id) !== pageKey)
      if (candidates.length === 0) return
      e.preventDefault()
      const last = candidates[candidates.length - 1]
      setExpanded(expandedKey ? null : dockKey(last.kind, last.id))
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, expandedKey, pageKey, setExpanded])

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      dragRef.current = { startY: e.clientY, startH: height }
      const onMove = (ev: MouseEvent) => {
        if (!dragRef.current) return
        const max = Math.round(window.innerHeight * 0.85)
        const next = Math.min(max, Math.max(MIN_HEIGHT, dragRef.current.startH + (dragRef.current.startY - ev.clientY)))
        setHeight(next)
      }
      const onUp = () => {
        dragRef.current = null
        document.removeEventListener('mousemove', onMove)
        document.removeEventListener('mouseup', onUp)
        document.body.style.userSelect = ''
      }
      document.body.style.userSelect = 'none'
      document.addEventListener('mousemove', onMove)
      document.addEventListener('mouseup', onUp)
    },
    [height]
  )

  useEffect(() => {
    window.localStorage.setItem(HEIGHT_KEY, String(height))
  }, [height])

  const visible = items.filter((i) => dockKey(i.kind, i.id) !== pageKey)
  if (visible.length === 0 && !notice) return null

  const expanded = visible.find((i) => dockKey(i.kind, i.id) === expandedKey) || null
  const statusFor = (it: DockedSession) => statuses[items.indexOf(it)]?.data as SessionListItem | undefined
  const resolvePane = (it: DockedSession): 'terminal' | 'code' | 'vscode' =>
    it.pane || (it.title.startsWith('Editor ·') ? 'code' : 'terminal')
  const expandedPane = expanded ? resolvePane(expanded) : 'terminal'
  const expandedProject = expanded ? projects.find((p) => p.id === expanded.projectId) || null : null
  const canContributeExpanded = canStartSessions(user, expandedProject)

  const selectDockPane = async (it: DockedSession, pane: 'terminal' | 'code' | 'vscode') => {
    const key = dockKey(it.kind, it.id)
    if (pane !== 'vscode' || it.vscodeUrl) {
      updatePane(key, pane)
      return
    }
    setVscodeStartingKey(key)
    try {
      const res = await api.projects.openSessionVsCode(it.projectId, it.kind, it.id, theme)
      updatePane(key, 'vscode', res.url)
    } catch (err) {
      setNotice(err instanceof Error ? err.message : 'Could not start VS Code')
    } finally {
      setVscodeStartingKey(null)
    }
  }

  const openFull = (it: DockedSession) => {
    const pane = resolvePane(it)
    setExpanded(null)
    navigateToDetail(
      VIEWS.SESSIONS,
      it.kind,
      it.id,
      pane === 'code' || pane === 'vscode' ? { pane } : undefined
    )
  }

  return (
    <div className="hidden md:flex flex-col shrink-0 border-t border-zinc-200 dark:border-zinc-800 bg-white/90 dark:bg-zinc-950/90 backdrop-blur relative z-20">
      {expanded && (
        <div className="flex flex-col" style={{ height }}>
          <div
            onMouseDown={onDragStart}
            className="h-1.5 -mt-px cursor-row-resize hover:bg-teal-500/40 transition-colors"
            title="Drag to resize"
          />
          <div className="px-3 py-1.5 flex items-center justify-between gap-2 border-b border-zinc-200/80 dark:border-zinc-800">
            <div className="flex items-center gap-2 min-w-0 text-xs">
              <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(statusFor(expanded))}`} />
              <span className="font-semibold text-zinc-900 dark:text-zinc-100 truncate">{expanded.title}</span>
              {statusFor(expanded)?.project_name && (
                <span className="text-zinc-500 dark:text-zinc-400 truncate">· {statusFor(expanded)?.project_name}</span>
              )}
              {expanded.kind === 'chat' && statusFor(expanded)?.context_window && (
                <ContextWindowBadge
                  contextWindow={statusFor(expanded)?.context_window}
                  variant="inline"
                />
              )}
              {statusFor(expanded)?.is_running && (
                <div className="ml-2 inline-flex rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800">
                  {(['terminal', 'code', 'vscode'] as const).map((p) => {
                    const isStarting = p === 'vscode' && vscodeStartingKey === dockKey(expanded.kind, expanded.id)
                    return (
                      <button
                        key={p}
                        type="button"
                        disabled={isStarting}
                        onClick={() => void selectDockPane(expanded, p)}
                        className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold cursor-pointer ${
                          expandedPane === p
                            ? 'bg-white dark:bg-zinc-800 text-teal-700 dark:text-teal-300 shadow-2xs'
                            : 'text-zinc-500'
                        }`}
                      >
                        {isStarting ? (
                          <Loader2 size={11} className="animate-spin" />
                        ) : p === 'terminal' ? (
                          <Terminal size={11} />
                        ) : (
                          <Code2 size={11} />
                        )}
                        {p === 'code' ? 'Code' : p === 'vscode' ? 'VS Code' : 'Terminal'}
                      </button>
                    )
                  })}
                </div>
              )}
              {expandedPane === 'vscode' && expanded.vscodeUrl && (
                <a
                  href={expanded.vscodeUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open VS Code in a separate browser tab"
                  className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-[11px] font-semibold text-zinc-500 hover:text-teal-600 dark:hover:text-teal-300"
                >
                  <ExternalLink size={11} />
                </a>
              )}
            </div>
            <div className="flex items-center gap-0.5">
              <DockIconButton title="Open full page" onClick={() => openFull(expanded)}>
                <Maximize2 size={13} />
              </DockIconButton>
              <DockIconButton title="Collapse (Ctrl+`)" onClick={() => setExpanded(null)}>
                <ChevronDown size={14} />
              </DockIconButton>
              <DockCloseMenu
                item={expanded}
                running={!!statusFor(expanded)?.is_running}
                placement="below"
                onRemove={() => undock(dockKey(expanded.kind, expanded.id))}
                onStopped={() => {
                  undock(dockKey(expanded.kind, expanded.id))
                  setNotice(`Stopped “${expanded.title}”`)
                }}
                onError={setNotice}
              />
            </div>
          </div>
          {expanded.kind === 'chat' &&
          statusFor(expanded) &&
          !statusFor(expanded)?.is_running &&
          !resumed.has(dockKey(expanded.kind, expanded.id)) ? (
            <SessionResumePrompt
              agentHandle={expanded.agentHandle}
              onResume={() => setResumed((s) => new Set(s).add(dockKey(expanded.kind, expanded.id)))}
              className="flex-1 min-h-0"
            />
          ) : statusFor(expanded)?.is_running && expandedPane === 'vscode' && expanded.vscodeUrl ? (
            <iframe
              key={`vscode-${dockKey(expanded.kind, expanded.id)}`}
              src={expanded.vscodeUrl}
              title={`VS Code · ${expanded.title}`}
              sandbox="allow-scripts allow-same-origin allow-forms allow-modals allow-popups allow-downloads"
              allow="clipboard-read; clipboard-write;"
              className="flex-1 min-h-0 w-full border-0 bg-zinc-950"
            />
          ) : statusFor(expanded)?.is_running && expandedPane === 'code' ? (
            <SessionEditorPane
              key={`editor-${dockKey(expanded.kind, expanded.id)}`}
              projectId={expanded.projectId}
              kind={expanded.kind}
              sessionId={expanded.id}
              canContribute={canContributeExpanded}
              onOpenPush={() => openFull(expanded)}
              className="flex-1 min-h-0"
            />
          ) : statusFor(expanded) ? (
            <SessionTerminal
              key={dockKey(expanded.kind, expanded.id)}
              url={api.projects.sessionPtyUrl(expanded.projectId, expanded.kind, expanded.id)}
              connectingLabel={expanded.kind === 'chat' && expanded.agentHandle ? `Resuming @${expanded.agentHandle}…` : undefined}
              className="flex-1 min-h-0"
            />
          ) : (
            <div className="flex-1 min-h-0 bg-zinc-950" />
          )}
        </div>
      )}

      {/* No overflow clipping here: the close menu pops up above the chips (max 3 fit easily) */}
      <div className="h-9 px-2 flex items-center gap-1.5 min-w-0">
        {visible.map((it) => {
          const key = dockKey(it.kind, it.id)
          const isOpen = key === expandedKey
          const itemPane = resolvePane(it)
          const liveItem = statusFor(it)
          const matchedAgent =
            it.kind === 'chat'
              ? agents.find(
                  (a) =>
                    (it.agentHandle && a.handle.toLowerCase() === it.agentHandle.toLowerCase()) ||
                    (liveItem?.agent_handle && a.handle.toLowerCase() === liveItem.agent_handle.toLowerCase())
                ) || null
              : null
          return (
            <div
              key={key}
              className={`group shrink-0 max-w-[260px] h-7 pl-2 pr-1 flex items-center gap-1.5 rounded-lg border text-xs transition ${
                isOpen
                  ? 'border-teal-500/50 bg-teal-500/10 text-teal-800 dark:text-teal-200'
                  : 'border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-900'
              }`}
            >
              <button
                type="button"
                onClick={() => setExpanded(isOpen ? null : key)}
                className="flex items-center gap-1.5 min-w-0 cursor-pointer"
                title={isOpen ? 'Collapse' : 'Expand'}
              >
                <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDot(liveItem)}`} />
                {it.kind === 'chat' ? (
                  matchedAgent ? (
                    <AgentProviderIcon agent={matchedAgent} backends={backends} size={12} />
                  ) : (
                    <Bot size={12} className="shrink-0" />
                  )
                ) : itemPane === 'code' ? (
                  <Code2 size={12} className="shrink-0" />
                ) : (
                  <Terminal size={12} className="shrink-0" />
                )}
                <span className="truncate font-medium">{it.title}</span>
                {isOpen ? <ChevronDown size={12} className="shrink-0 opacity-60" /> : <ChevronUp size={12} className="shrink-0 opacity-60" />}
              </button>
              <DockCloseMenu
                item={it}
                running={!!statusFor(it)?.is_running}
                placement="above"
                size={11}
                onRemove={() => undock(key)}
                onStopped={() => {
                  undock(key)
                  setNotice(`Stopped “${it.title}”`)
                }}
                onError={setNotice}
              />
            </div>
          )
        })}
        {notice && <span className="ml-auto pr-2 text-[11px] text-zinc-500 dark:text-zinc-400 shrink-0">{notice}</span>}
      </div>
    </div>
  )
}

/**
 * × on a docked session: choose between just removing it from the dock (keeps running)
 * and stopping the session. Stop is only offered while the session is actually running.
 */
const DockCloseMenu: React.FC<{
  item: DockedSession
  running: boolean
  placement: 'above' | 'below'
  size?: number
  onRemove: () => void
  onStopped: () => void
  onError: (message: string) => void
}> = ({ item, running, placement, size = 14, onRemove, onStopped, onError }) => {
  const queryClient = useQueryClient()
  const [open, setOpen] = useState(false)
  const [stopping, setStopping] = useState(false)
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) return
    const onDoc = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') { e.stopPropagation(); setOpen(false) } }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onEsc)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onEsc)
    }
  }, [open])

  const stop = async () => {
    setStopping(true)
    try {
      if (item.kind === 'chat') await api.projects.stopAgentSession(item.projectId, item.id)
      else await api.projects.stopSandbox(item.projectId, item.id)
      await queryClient.invalidateQueries({ queryKey: ['sessions'] })
      onStopped()
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Could not stop session')
    } finally {
      setStopping(false)
      setOpen(false)
    }
  }

  return (
    <div ref={rootRef} className="relative flex">
      <button
        type="button"
        onClick={() => (running ? setOpen((v) => !v) : onRemove())}
        title={running ? 'Close…' : 'Remove from dock'}
        className="p-1 rounded-md text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-200 dark:hover:bg-zinc-800 cursor-pointer"
      >
        <X size={size} />
      </button>
      {open && (
        <div
          className={`absolute z-50 w-56 p-1 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg text-xs ${
            placement === 'above' ? 'left-0 bottom-full mb-1.5' : 'right-0 top-full mt-1.5'
          }`}
        >
          <button
            type="button"
            onClick={() => {
              setOpen(false)
              onRemove()
            }}
            className="w-full flex flex-col items-start px-2.5 py-2 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer text-left"
          >
            <span className="font-semibold text-zinc-900 dark:text-zinc-100">Remove from dock</span>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">Keeps running; reopen it from Sessions</span>
          </button>
          <button
            type="button"
            disabled={stopping}
            onClick={() => void stop()}
            className="w-full flex flex-col items-start px-2.5 py-2 rounded-lg hover:bg-rose-500/10 cursor-pointer text-left disabled:opacity-60"
          >
            <span className="font-semibold text-rose-600 dark:text-rose-400 inline-flex items-center gap-1.5">
              {stopping ? <Loader2 size={12} className="animate-spin" /> : <Square size={11} />}
              {stopping ? 'Stopping…' : 'Stop session'}
            </span>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              {item.kind === 'chat' ? 'Ends the agent and puts the workspace to sleep' : 'Ends the terminal and collects artifacts'}
            </span>
          </button>
        </div>
      )}
    </div>
  )
}

const DockIconButton: React.FC<{ title: string; onClick: () => void; children: React.ReactNode }> = ({ title, onClick, children }) => (
  <button
    type="button"
    title={title}
    onClick={onClick}
    className="p-1.5 rounded-md text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
  >
    {children}
  </button>
)
