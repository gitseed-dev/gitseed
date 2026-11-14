import React, { useCallback, useEffect, useRef, useState } from 'react'
import { useQueries, useQueryClient } from '@tanstack/react-query'
import {
  Bot,
  ChevronDown,
  ChevronUp,
  Code2,
  Columns2,
  ExternalLink,
  Loader2,
  Maximize2,
  Square,
  Terminal,
  X,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { VIEWS } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import {
  dockKey,
  MAX_SPLIT_PANES,
  useSessionDock,
  type DockedPaneMode,
  type DockedSession,
} from '../../context/SessionDockContext'
import { useTheme } from '../../context/ThemeContext'
import { AgentProviderIcon } from '../common/ProviderIcon'
import { ContextWindowBadge } from '../common/ContextWindowBadge'
import { SessionTerminal } from '../details/project/SessionTerminal'
import { SessionResumePrompt } from './SessionDetail'
import { SessionEditorPane } from './SessionEditorPane'
import { canStartSessions } from './SessionStartControl'
import { resolveVsCodeIframeProps, safeExternalUrl } from '../../utils/gitUrls'
import type { SessionListItem } from '../../types'

const HEIGHT_KEY = 'gitseed.sessionDock.height'
const SPLIT_RATIO_KEY = 'gitseed.sessionDock.splitRatio'
const MIN_HEIGHT = 200
const INACTIVE_DOCK_STATUSES = new Set([
  'sleeping',
  'stopped',
  'terminated',
  'failed',
  'completed',
  'archived',
])

function initialHeight(): number {
  const saved = Number(window.localStorage.getItem(HEIGHT_KEY))
  if (Number.isFinite(saved) && saved >= MIN_HEIGHT) return saved
  return Math.round(window.innerHeight * 0.4)
}

function initialSplitRatio(): number {
  const saved = Number(window.localStorage.getItem(SPLIT_RATIO_KEY))
  if (Number.isFinite(saved) && saved >= 25 && saved <= 75) return saved
  return 50
}

function statusDot(item: SessionListItem | undefined): string {
  if (!item) return 'bg-zinc-400'
  if (item.status === 'starting') return 'bg-teal-400 animate-pulse'
  if (item.status === 'failed') return 'bg-rose-500'
  return item.is_running ? 'bg-emerald-500' : 'bg-zinc-400'
}

/**
 * Bottom dock for minimized sessions: slim chips that persist across pages; one or more
 * expand into a resizable drawer (supporting side-by-side split view for up to 3 sessions).
 * Terminals only connect while expanded, and the server keeps the process running.
 */
export const SessionDock: React.FC = () => {
  const { user } = useAuth()
  const { theme } = useTheme()
  const {
    items,
    expandedKeys,
    setExpanded,
    toggleSplit,
    collapsePane,
    collapseAll,
    undock,
    updateTitle,
    updatePane,
  } = useSessionDock()
  const { projects, agents, backends, activeView, activeDetailId, activeDetailTab, navigateToDetail } = useData()
  const [height, setHeight] = useState<number>(initialHeight)
  const [splitRatio, setSplitRatio] = useState<number>(initialSplitRatio)
  const [notice, setNotice] = useState<string | null>(null)
  const [vscodeStartingKey, setVscodeStartingKey] = useState<string | null>(null)
  const [splitPickerOpen, setSplitPickerOpen] = useState(false)
  // Sleeping agent sessions the user explicitly chose to wake (never woken just by expanding)
  const [resumed, setResumed] = useState<Set<string>>(() => new Set())
  const dragRef = useRef<{ startY: number; startH: number } | null>(null)
  const dragCleanupRef = useRef<(() => void) | null>(null)
  const colDragCleanupRef = useRef<(() => void) | null>(null)
  const panesContainerRef = useRef<HTMLDivElement | null>(null)
  const splitPickerRef = useRef<HTMLDivElement | null>(null)

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
      const isActiveContainer =
        data.is_running && !INACTIVE_DOCK_STATUSES.has(data.status)
      if (!isActiveContainer && data.status !== 'starting' && vscodeStartingKey !== key) {
        if (it.vscodeUrl || it.pane === 'vscode') {
          updatePane(key, it.pane === 'vscode' ? 'terminal' : it.pane || 'terminal', null)
        }
      }
      // Awake again: a later sleep should ask before waking, so forget the resume request
      if (isActiveContainer)
        setResumed((s) => {
          if (!s.has(key)) return s
          const next = new Set(s)
          next.delete(key)
          return next
        })
    })
  }, [items, statuses, undock, updateTitle, updatePane, vscodeStartingKey])

  useEffect(() => {
    if (!notice) return
    const t = window.setTimeout(() => setNotice(null), 4000)
    return () => window.clearTimeout(t)
  }, [notice])

  useEffect(() => {
    if (!splitPickerOpen) return
    const onDoc = (e: MouseEvent) => {
      if (splitPickerRef.current && !splitPickerRef.current.contains(e.target as Node)) {
        setSplitPickerOpen(false)
      }
    }
    document.addEventListener('mousedown', onDoc)
    return () => document.removeEventListener('mousedown', onDoc)
  }, [splitPickerOpen])

  // Ctrl+` toggles the drawer (most recently docked session or collapses all open panes)
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (!(e.ctrlKey && e.key === '`')) return
      const candidates = items.filter((i) => dockKey(i.kind, i.id) !== pageKey)
      if (candidates.length === 0) return
      e.preventDefault()
      const hasVisibleExpanded = candidates.some((i) =>
        expandedKeys.includes(dockKey(i.kind, i.id))
      )
      if (hasVisibleExpanded) {
        collapseAll()
      } else {
        const last = candidates[candidates.length - 1]
        setExpanded(dockKey(last.kind, last.id))
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [items, expandedKeys, pageKey, setExpanded, collapseAll])

  const onDragStart = useCallback(
    (e: React.MouseEvent) => {
      e.preventDefault()
      dragCleanupRef.current?.()
      dragRef.current = { startY: e.clientY, startH: height }
      const onMove = (ev: MouseEvent) => {
        if (!dragRef.current) return
        const max = Math.round(window.innerHeight * 0.85)
        const next = Math.min(max, Math.max(MIN_HEIGHT, dragRef.current.startH + (dragRef.current.startY - ev.clientY)))
        setHeight(next)
      }
      const cleanup = () => {
        dragRef.current = null
        dragCleanupRef.current = null
        document.removeEventListener('mousemove', onMove)
        document.removeEventListener('mouseup', onUp)
        document.body.style.userSelect = ''
      }
      const onUp = () => {
        cleanup()
      }
      dragCleanupRef.current = cleanup
      document.body.style.userSelect = 'none'
      document.addEventListener('mousemove', onMove)
      document.addEventListener('mouseup', onUp)
    },
    [height]
  )

  const onColDragStart = useCallback((e: React.MouseEvent) => {
    e.preventDefault()
    colDragCleanupRef.current?.()
    const container = panesContainerRef.current
    if (!container) return
    const rect = container.getBoundingClientRect()
    if (rect.width <= 0) return

    const onMove = (ev: MouseEvent) => {
      const pct = ((ev.clientX - rect.left) / rect.width) * 100
      const clamped = Math.min(75, Math.max(25, Math.round(pct * 10) / 10))
      setSplitRatio(clamped)
    }
    const cleanup = () => {
      colDragCleanupRef.current = null
      document.removeEventListener('mousemove', onMove)
      document.removeEventListener('mouseup', onUp)
      document.body.style.userSelect = ''
    }
    const onUp = () => {
      cleanup()
    }
    colDragCleanupRef.current = cleanup
    document.body.style.userSelect = 'none'
    document.addEventListener('mousemove', onMove)
    document.addEventListener('mouseup', onUp)
  }, [])

  useEffect(() => {
    return () => {
      dragCleanupRef.current?.()
      colDragCleanupRef.current?.()
    }
  }, [])

  useEffect(() => {
    window.localStorage.setItem(HEIGHT_KEY, String(height))
  }, [height])

  useEffect(() => {
    window.localStorage.setItem(SPLIT_RATIO_KEY, String(splitRatio))
  }, [splitRatio])

  const visible = items.filter((i) => dockKey(i.kind, i.id) !== pageKey)
  const expandedItems = expandedKeys
    .map((k) => visible.find((i) => dockKey(i.kind, i.id) === k))
    .filter((i): i is DockedSession => Boolean(i))
  const unexpandedItems = visible.filter((i) => !expandedItems.includes(i))

  useEffect(() => {
    if (expandedItems.length === 0) {
      dragCleanupRef.current?.()
      colDragCleanupRef.current?.()
      setSplitPickerOpen(false)
    }
  }, [expandedItems.length])

  if (visible.length === 0 && !notice) return null

  const statusFor = (it: DockedSession) => statuses[items.indexOf(it)]?.data as SessionListItem | undefined
  const resolvePane = (it: DockedSession): DockedPaneMode =>
    it.pane || (it.title.startsWith('Editor ·') ? 'code' : 'terminal')

  const selectDockPane = async (it: DockedSession, pane: DockedPaneMode) => {
    const key = dockKey(it.kind, it.id)
    const proj = projects.find((p) => p.id === it.projectId) || null
    if (pane === 'vscode' && !canStartSessions(user, proj)) {
      return
    }
    const liveStatus = statusFor(it)
    const isActivelyRunning = Boolean(
      liveStatus?.is_running && !INACTIVE_DOCK_STATUSES.has(liveStatus.status)
    )
    if (pane !== 'vscode' || (it.vscodeUrl && isActivelyRunning)) {
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
    const proj = projects.find((p) => p.id === it.projectId) || null
    const canManageItem = canStartSessions(user, proj)
    collapseAll()
    navigateToDetail(
      VIEWS.SESSIONS,
      it.kind,
      it.id,
      pane === 'code' || pane === 'split' || (pane === 'vscode' && canManageItem) ? { pane } : undefined
    )
  }

  const isSplitMode = expandedItems.length > 1

  return (
    <div className="hidden md:flex flex-col shrink-0 border-t border-zinc-200 dark:border-zinc-800 bg-white/90 dark:bg-zinc-950/90 backdrop-blur relative z-20">
      {expandedItems.length > 0 && (
        <div className="flex flex-col" style={{ height }}>
          <div
            onMouseDown={onDragStart}
            className="h-1.5 -mt-px cursor-row-resize hover:bg-teal-500/40 transition-colors shrink-0"
            title="Drag to resize height"
          />

          <div
            ref={panesContainerRef}
            className="flex-1 min-h-0 flex flex-row overflow-hidden"
          >
            {expandedItems.map((paneItem, paneIdx) => {
              const key = dockKey(paneItem.kind, paneItem.id)
              const liveStatus = statusFor(paneItem)
              const activePane = resolvePane(paneItem)
              const paneProject = projects.find((p) => p.id === paneItem.projectId) || null
              const canContributePane = canStartSessions(user, paneProject)
              const resolvedVsCode = paneItem.vscodeUrl
                ? resolveVsCodeIframeProps(paneItem.vscodeUrl)
                : null
              const safeVsCodeHref = safeExternalUrl(resolvedVsCode?.url)
              const isLastPane = paneIdx === expandedItems.length - 1
              const canAddSplit =
                isLastPane &&
                unexpandedItems.length > 0 &&
                expandedItems.length < MAX_SPLIT_PANES

              const widthStyle =
                expandedItems.length === 2
                  ? { width: `${paneIdx === 0 ? splitRatio : 100 - splitRatio}%` }
                  : undefined

              return (
                <React.Fragment key={key}>
                  {paneIdx > 0 && expandedItems.length === 2 && (
                    <div
                      onMouseDown={onColDragStart}
                      onDoubleClick={() => setSplitRatio(50)}
                      className="w-1.5 -mx-px cursor-col-resize bg-zinc-200/80 dark:bg-zinc-800 hover:bg-teal-500/50 transition-colors shrink-0 z-10"
                      title="Drag to resize split · Double-click to reset 50/50"
                    />
                  )}
                  {paneIdx > 0 && expandedItems.length !== 2 && (
                    <div className="w-px bg-zinc-200 dark:bg-zinc-800 shrink-0" />
                  )}

                  <div
                    style={widthStyle}
                    className={`flex flex-col min-w-0 min-h-0 ${
                      expandedItems.length === 2 ? 'shrink-0' : 'flex-1'
                    }`}
                  >
                    {/* Per-Pane Header */}
                    <div className="px-3 py-1.5 flex items-center justify-between gap-2 border-b border-zinc-200/80 dark:border-zinc-800 shrink-0">
                      <div className="flex items-center gap-2 min-w-0 text-xs">
                        {isSplitMode && (
                          <span className="w-4 h-4 rounded bg-teal-500/15 border border-teal-500/30 text-xs font-mono font-bold text-teal-700 dark:text-teal-300 flex items-center justify-center shrink-0">
                            {paneIdx + 1}
                          </span>
                        )}
                        <span className={`w-2 h-2 rounded-full shrink-0 ${statusDot(liveStatus)}`} />
                        <span className="font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                          {paneItem.title}
                        </span>
                        {liveStatus?.project_name && (
                          <span className="text-zinc-500 dark:text-zinc-400 truncate hidden sm:inline">
                            · {liveStatus.project_name}
                          </span>
                        )}
                        {paneItem.kind === 'chat' && liveStatus?.context_window && (
                          <ContextWindowBadge
                            contextWindow={liveStatus.context_window}
                            variant="inline"
                          />
                        )}
                        {liveStatus?.is_running && (
                          <div className="ml-1 inline-flex rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 shrink-0">
                            {(canContributePane
                              ? (['terminal', 'split', 'code', 'vscode'] as const)
                              : (['terminal', 'split', 'code'] as const)
                            ).map((p) => {
                              const isStarting = p === 'vscode' && vscodeStartingKey === key
                              return (
                                <button
                                  key={p}
                                  type="button"
                                  disabled={isStarting}
                                  onClick={() => void selectDockPane(paneItem, p)}
                                  title={
                                    p === 'split'
                                      ? 'Side-by-side Terminal and Code/Diff for this session container'
                                      : undefined
                                  }
                                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold cursor-pointer ${
                                    activePane === p
                                      ? 'bg-white dark:bg-zinc-800 text-teal-700 dark:text-teal-300 shadow-2xs'
                                      : 'text-zinc-500'
                                  }`}
                                >
                                  {isStarting ? (
                                    <Loader2 size={11} className="animate-spin" />
                                  ) : p === 'terminal' ? (
                                    <Terminal size={11} />
                                  ) : p === 'split' ? (
                                    <Columns2 size={11} />
                                  ) : (
                                    <Code2 size={11} />
                                  )}
                                  {p === 'code'
                                    ? 'Code'
                                    : p === 'split'
                                      ? 'Split'
                                      : p === 'vscode'
                                        ? 'VS Code'
                                        : 'Terminal'}
                                </button>
                              )
                            })}
                          </div>
                        )}
                        {canContributePane && activePane === 'vscode' && safeVsCodeHref && (
                          <a
                            href={safeVsCodeHref}
                            target="_blank"
                            rel="noopener noreferrer"
                            title="Open VS Code in a separate browser tab"
                            className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold text-zinc-500 hover:text-teal-600 dark:hover:text-teal-300 shrink-0"
                          >
                            <ExternalLink size={11} />
                          </a>
                        )}
                      </div>

                      <div className="flex items-center gap-0.5 shrink-0">
                        {canAddSplit && (
                          <div ref={splitPickerRef} className="relative flex">
                            <button
                              type="button"
                              onClick={() => {
                                setSplitPickerOpen((v) => !v)
                              }}
                              title="Split Terminal & Code in this session, or open another docked session side-by-side"
                              className="inline-flex items-center gap-1 px-2 py-1 rounded-md text-xs font-semibold text-teal-700 dark:text-teal-300 hover:bg-teal-500/10 cursor-pointer"
                            >
                              <Columns2 size={13} />
                              <span className="hidden lg:inline">Split</span>
                            </button>
                            {splitPickerOpen && (
                              <div className="absolute right-0 top-full mt-1.5 z-50 w-64 p-1 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-lg text-xs">
                                {liveStatus?.is_running && (
                                  <>
                                    <div className="px-2.5 py-1.5 text-xs font-semibold text-zinc-400">
                                      This session container
                                    </div>
                                    <button
                                      type="button"
                                      onClick={() => {
                                        setSplitPickerOpen(false)
                                        void selectDockPane(
                                          paneItem,
                                          activePane === 'split' ? 'terminal' : 'split'
                                        )
                                      }}
                                      className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer text-left"
                                    >
                                      <Code2 size={12} className="text-teal-600 dark:text-teal-400 shrink-0" />
                                      <span className="font-medium text-zinc-900 dark:text-zinc-100 truncate">
                                        {activePane === 'split'
                                          ? 'Exit Terminal + Code split'
                                          : 'Terminal + Code / Diff'}
                                      </span>
                                    </button>
                                    <div className="my-1 border-t border-zinc-100 dark:border-zinc-800" />
                                  </>
                                )}
                                <div className="px-2.5 py-1.5 text-xs font-semibold text-zinc-400">
                                  Another docked session
                                </div>
                                {unexpandedItems.map((cand) => {
                                  const candKey = dockKey(cand.kind, cand.id)
                                  return (
                                    <button
                                      key={candKey}
                                      type="button"
                                      onClick={() => {
                                        setSplitPickerOpen(false)
                                        toggleSplit(candKey)
                                      }}
                                      className="w-full flex items-center gap-2 px-2.5 py-2 rounded-lg hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer text-left"
                                    >
                                      <Columns2 size={12} className="text-teal-600 dark:text-teal-400 shrink-0" />
                                      <span className="font-medium text-zinc-900 dark:text-zinc-100 truncate">
                                        {cand.title}
                                      </span>
                                    </button>
                                  )
                                })}
                              </div>
                            )}
                          </div>
                        )}
                        <DockIconButton title="Open full page" onClick={() => openFull(paneItem)}>
                          <Maximize2 size={13} />
                        </DockIconButton>
                        <DockIconButton
                          title={isSplitMode ? 'Collapse this pane' : 'Collapse (Ctrl+`)'}
                          onClick={() => collapsePane(key)}
                        >
                          <ChevronDown size={14} />
                        </DockIconButton>
                        <DockCloseMenu
                          item={paneItem}
                          running={!!liveStatus?.is_running}
                          placement="below"
                          onRemove={() => undock(key)}
                          onStopped={() => {
                            undock(key)
                            setNotice(`Stopped “${paneItem.title}”`)
                          }}
                          onError={setNotice}
                        />
                      </div>
                    </div>

                    {/* Per-Pane Body */}
                    {paneItem.kind === 'chat' &&
                    liveStatus &&
                    !liveStatus.is_running &&
                    !resumed.has(key) ? (
                      <SessionResumePrompt
                        agentHandle={paneItem.agentHandle}
                        onResume={() => setResumed((s) => new Set(s).add(key))}
                        className="flex-1 min-h-0"
                      />
                    ) : liveStatus?.is_running && activePane === 'vscode' && resolvedVsCode ? (
                      <div className="flex-1 min-h-0 flex flex-col overflow-hidden bg-zinc-950">
                        {!resolvedVsCode.sandbox.includes('allow-same-origin') && (
                          <div className="px-3 py-1.5 text-xs bg-amber-500/15 text-amber-200 border-b border-amber-500/30 flex items-center justify-between gap-2 shrink-0">
                            <span>
                              Same-origin iframe sandbox active (`allow-same-origin` omitted to protect control-plane cookies). Configure dedicated port 8423 for full VS Code Web storage.
                            </span>
                            {safeVsCodeHref && (
                              <a
                                href={safeVsCodeHref}
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
                          key={`vscode-${key}`}
                          src={resolvedVsCode.url}
                          title={`VS Code · ${paneItem.title}`}
                          sandbox={resolvedVsCode.sandbox}
                          allow="clipboard-read; clipboard-write;"
                          className="flex-1 min-h-0 w-full border-0 bg-zinc-950"
                        />
                      </div>
                    ) : liveStatus ? (
                      <div className="flex-1 min-h-0 flex flex-row overflow-hidden">
                        {activePane !== 'code' && (
                          <div
                            className={
                              activePane === 'split' && liveStatus.is_running
                                ? 'w-1/2 min-w-0 min-h-0 flex flex-col border-r border-zinc-200 dark:border-zinc-800 shrink-0'
                                : 'flex-1 min-w-0 min-h-0 flex flex-col'
                            }
                          >
                            <SessionTerminal
                              key={key}
                              url={api.projects.sessionPtyUrl(paneItem.projectId, paneItem.kind, paneItem.id)}
                              connectingLabel={
                                paneItem.kind === 'chat' && paneItem.agentHandle
                                  ? `Resuming @${paneItem.agentHandle}…`
                                  : undefined
                              }
                              className="flex-1 min-h-0"
                            />
                          </div>
                        )}
                        {liveStatus.is_running && (activePane === 'code' || activePane === 'split') && (
                          <SessionEditorPane
                            key={`editor-${key}`}
                            projectId={paneItem.projectId}
                            kind={paneItem.kind}
                            sessionId={paneItem.id}
                            canContribute={canContributePane}
                            preferDiffOnInitialChange={activePane === 'split'}
                            onOpenPush={() => openFull(paneItem)}
                            className="flex-1 min-w-0 min-h-0"
                          />
                        )}
                      </div>
                    ) : (
                      <div className="flex-1 min-h-0 bg-zinc-950" />
                    )}
                  </div>
                </React.Fragment>
              )
            })}
          </div>
        </div>
      )}

      {/* No overflow clipping here: the close menu pops up above the chips */}
      <div className="h-9 px-2 flex items-center gap-1.5 min-w-0">
        {visible.map((it) => {
          const key = dockKey(it.kind, it.id)
          const splitIndex = expandedItems.findIndex((e) => dockKey(e.kind, e.id) === key)
          const isOpen = splitIndex !== -1
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
          const canToggleSplit =
            !isOpen &&
            expandedItems.length > 0 &&
            expandedItems.length < MAX_SPLIT_PANES

          return (
            <div
              key={key}
              className={`group shrink-0 max-w-[280px] h-7 pl-2 pr-1 flex items-center gap-1 rounded-lg border text-xs transition ${
                isOpen
                  ? 'border-teal-500/50 bg-teal-500/10 text-teal-800 dark:text-teal-200'
                  : 'border-zinc-200 dark:border-zinc-800 text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-900'
              }`}
            >
              <button
                type="button"
                onClick={(e) => {
                  if (e.shiftKey || e.metaKey || e.ctrlKey) {
                    toggleSplit(key)
                    return
                  }
                  if (isOpen) {
                    collapsePane(key)
                  } else {
                    setExpanded(key)
                  }
                }}
                className="flex items-center gap-1.5 min-w-0 cursor-pointer"
                title={
                  isOpen
                    ? 'Collapse pane'
                    : expandedItems.length > 0
                      ? 'Switch to session (Shift+Click to open side-by-side)'
                      : 'Expand session'
                }
              >
                {isSplitMode && isOpen && (
                  <span className="w-3.5 h-3.5 rounded bg-teal-500/20 text-xs font-mono font-bold text-teal-700 dark:text-teal-300 flex items-center justify-center shrink-0">
                    {splitIndex + 1}
                  </span>
                )}
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
                {isOpen ? (
                  <ChevronDown size={12} className="shrink-0 opacity-60" />
                ) : (
                  <ChevronUp size={12} className="shrink-0 opacity-60" />
                )}
              </button>

              {canToggleSplit && (
                <button
                  type="button"
                  onClick={() => toggleSplit(key)}
                  title="Open side-by-side"
                  aria-label={`Open ${it.title} side-by-side`}
                  className="p-1 rounded-md text-zinc-400 hover:text-teal-600 dark:hover:text-teal-300 hover:bg-teal-500/10 cursor-pointer shrink-0"
                >
                  <Columns2 size={11} />
                </button>
              )}

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

        {isSplitMode && (
          <button
            type="button"
            onClick={collapseAll}
            title="Collapse all split panes (Ctrl+`)"
            className="ml-1 px-2 py-1 rounded-md text-xs font-semibold text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-900 cursor-pointer shrink-0"
          >
            Collapse all
          </button>
        )}

        {notice && (
          <span className="ml-auto pr-2 text-xs text-zinc-500 dark:text-zinc-400 shrink-0">
            {notice}
          </span>
        )}
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
    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        e.stopPropagation()
        setOpen(false)
      }
    }
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
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              Keeps running; reopen it from Sessions
            </span>
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
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              {item.kind === 'chat'
                ? 'Ends the agent and puts the workspace to sleep'
                : 'Ends the terminal and collects artifacts'}
            </span>
          </button>
        </div>
      )}
    </div>
  )
}

const DockIconButton: React.FC<{ title: string; onClick: () => void; children: React.ReactNode }> = ({
  title,
  onClick,
  children,
}) => (
  <button
    type="button"
    title={title}
    onClick={onClick}
    className="p-1.5 rounded-md text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
  >
    {children}
  </button>
)
