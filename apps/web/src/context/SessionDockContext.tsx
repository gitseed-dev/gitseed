import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react'
import { useAuth } from './AuthContext'

/** A session minimized to the bottom dock, so it stays at hand across pages. */
export interface DockedSession {
  kind: 'chat' | 'terminal'
  id: string
  projectId: string
  title: string
  agentHandle?: string | null
  pane?: 'terminal' | 'code' | 'vscode'
  vscodeUrl?: string | null
}

interface DockState {
  items: DockedSession[]
  expandedKey: string | null
}

interface SessionDockContextValue extends DockState {
  /** Add (or refresh) a session in the dock; expands it by default. */
  dock: (item: DockedSession, opts?: { expand?: boolean }) => void
  undock: (key: string) => void
  /** Expand one docked session (null collapses the drawer). */
  setExpanded: (key: string | null) => void
  updateTitle: (key: string, title: string) => void
  updatePane: (key: string, pane: 'terminal' | 'code' | 'vscode', vscodeUrl?: string | null) => void
  isDocked: (key: string) => boolean
}

/** Keep the dock calm: a few sessions at most; docking another drops the oldest. */
export const MAX_DOCKED_SESSIONS = 3

export const dockKey = (kind: string, id: string) => `${kind}:${id}`

const EMPTY: DockState = { items: [], expandedKey: null }

const SessionDockContext = createContext<SessionDockContextValue | null>(null)

function load(storageKey: string): DockState {
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw) as Partial<DockState>
    const items = Array.isArray(parsed.items)
      ? parsed.items.filter((i): i is DockedSession => !!i && typeof i.id === 'string' && (i.kind === 'chat' || i.kind === 'terminal'))
      : []
    const expandedKey =
      typeof parsed.expandedKey === 'string' && items.some((i) => dockKey(i.kind, i.id) === parsed.expandedKey)
        ? parsed.expandedKey
        : null
    return { items: items.slice(-MAX_DOCKED_SESSIONS), expandedKey }
  } catch {
    return EMPTY
  }
}

export const SessionDockProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useAuth()
  const storageKey = `gitseed.sessionDock.${user?.id || 'anon'}`
  const [state, setState] = useState<DockState>(() => load(storageKey))

  // Different user (or first auth load): read their dock
  useEffect(() => {
    setState(load(storageKey))
  }, [storageKey])

  useEffect(() => {
    try {
      window.localStorage.setItem(storageKey, JSON.stringify(state))
    } catch {
      /* storage full / disabled */
    }
  }, [state, storageKey])

  const dock = useCallback((item: DockedSession, opts?: { expand?: boolean }) => {
    const key = dockKey(item.kind, item.id)
    setState((prev) => {
      const rest = prev.items.filter((i) => dockKey(i.kind, i.id) !== key)
      const items = [...rest, item].slice(-MAX_DOCKED_SESSIONS)
      return { items, expandedKey: opts?.expand === false ? prev.expandedKey : key }
    })
  }, [])

  const undock = useCallback((key: string) => {
    setState((prev) => ({
      items: prev.items.filter((i) => dockKey(i.kind, i.id) !== key),
      expandedKey: prev.expandedKey === key ? null : prev.expandedKey,
    }))
  }, [])

  const setExpanded = useCallback((key: string | null) => {
    setState((prev) => ({ ...prev, expandedKey: key }))
  }, [])

  const updateTitle = useCallback((key: string, title: string) => {
    setState((prev) => {
      const target = prev.items.find((i) => dockKey(i.kind, i.id) === key)
      if (!target || target.title === title) return prev
      return { ...prev, items: prev.items.map((i) => (i === target ? { ...i, title } : i)) }
    })
  }, [])

  const updatePane = useCallback((key: string, pane: 'terminal' | 'code' | 'vscode', vscodeUrl?: string | null) => {
    setState((prev) => {
      const target = prev.items.find((i) => dockKey(i.kind, i.id) === key)
      if (!target || (target.pane === pane && (vscodeUrl === undefined || target.vscodeUrl === vscodeUrl))) return prev
      return {
        ...prev,
        items: prev.items.map((i) =>
          i === target ? { ...i, pane, ...(vscodeUrl !== undefined ? { vscodeUrl } : {}) } : i
        ),
      }
    })
  }, [])

  const value = useMemo<SessionDockContextValue>(
    () => ({
      ...state,
      dock,
      undock,
      setExpanded,
      updateTitle,
      updatePane,
      isDocked: (key: string) => state.items.some((i) => dockKey(i.kind, i.id) === key),
    }),
    [state, dock, undock, setExpanded, updateTitle, updatePane]
  )

  return <SessionDockContext.Provider value={value}>{children}</SessionDockContext.Provider>
}

export function useSessionDock(): SessionDockContextValue {
  const ctx = useContext(SessionDockContext)
  if (!ctx) throw new Error('useSessionDock must be used within SessionDockProvider')
  return ctx
}

/** Optional variant for components that may render outside the provider (tests, isolated views). */
export function useOptionalSessionDock(): SessionDockContextValue | null {
  return useContext(SessionDockContext)
}

