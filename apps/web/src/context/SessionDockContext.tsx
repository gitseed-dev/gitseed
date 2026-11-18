import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useAuth } from './AuthContext'

export type DockedPaneMode = 'terminal' | 'split' | 'code' | 'vscode'

/** A session minimized to the bottom dock, so it stays at hand across pages. */
export interface DockedSession {
  kind: 'chat' | 'terminal'
  id: string
  projectId: string
  title: string
  agentHandle?: string | null
  pane?: DockedPaneMode
  vscodeUrl?: string | null
}

interface DockState {
  items: DockedSession[]
  expandedKeys: string[]
  expandedKey: string | null
}

interface SessionDockContextValue extends DockState {
  /** Add (or refresh) a session in the dock; expands it by default (or adds side-by-side when split: true). */
  dock: (item: DockedSession, opts?: { expand?: boolean; split?: boolean }) => void
  undock: (key: string) => void
  /** Expand a single docked session (null collapses all panes). */
  setExpanded: (key: string | null) => void
  /** Toggle a docked session into or out of the side-by-side split view. */
  toggleSplit: (key: string) => void
  /** Collapse a single pane from the expanded view while keeping any other split panes open. */
  collapsePane: (key: string) => void
  /** Collapse all expanded panes back to the dock bar. */
  collapseAll: () => void
  updateTitle: (key: string, title: string) => void
  updatePane: (key: string, pane: DockedPaneMode, vscodeUrl?: string | null) => void
  isDocked: (key: string) => boolean
  isExpanded: (key: string) => boolean
}

/** Keep the dock calm: up to 4 docked sessions and up to 3 side-by-side split panes. */
export const MAX_DOCKED_SESSIONS = 4
export const MAX_SPLIT_PANES = 3

export const dockKey = (kind: string, id: string) => `${kind}:${id}`

const EMPTY: DockState = { items: [], expandedKeys: [], expandedKey: null }

const SessionDockContext = createContext<SessionDockContextValue | null>(null)

function stripVsCodeUrl(item: DockedSession): DockedSession {
  if (!('vscodeUrl' in item)) return item
  const copy: DockedSession = { ...item }
  delete copy.vscodeUrl
  return copy
}

function normalizeExpandedKeys(rawKeys: string[], items: DockedSession[]): DockState {
  const validSet = new Set(items.map((i) => dockKey(i.kind, i.id)))
  const seen = new Set<string>()
  const expandedKeys: string[] = []
  for (const k of rawKeys) {
    if (typeof k === 'string' && validSet.has(k) && !seen.has(k)) {
      seen.add(k)
      expandedKeys.push(k)
    }
  }
  const capped = expandedKeys.slice(-MAX_SPLIT_PANES)
  return {
    items,
    expandedKeys: capped,
    expandedKey: capped[0] ?? null,
  }
}

function load(storageKey: string): DockState {
  try {
    const raw = window.localStorage.getItem(storageKey)
    if (!raw) return EMPTY
    const parsed = JSON.parse(raw) as Partial<DockState>
    const items = Array.isArray(parsed.items)
      ? parsed.items
          .filter(
            (i): i is DockedSession =>
              !!i && typeof i.id === 'string' && (i.kind === 'chat' || i.kind === 'terminal')
          )
          .map(stripVsCodeUrl)
          .slice(-MAX_DOCKED_SESSIONS)
      : []
    const rawExpandedKeys = Array.isArray(parsed.expandedKeys)
      ? parsed.expandedKeys
      : typeof parsed.expandedKey === 'string'
        ? [parsed.expandedKey]
        : []
    return normalizeExpandedKeys(rawExpandedKeys, items)
  } catch {
    return EMPTY
  }
}

export const SessionDockProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user } = useAuth()
  const storageKey = `gitseed.sessionDock.${user?.id || 'anon'}`
  const loadedKeyRef = useRef(storageKey)
  const [state, setState] = useState<DockState>(() => load(storageKey))

  // Load new user's dock on user switch without leaking previous user's state across keys,
  // and strip ephemeral vscodeUrl capability tokens before persisting to localStorage.
  useEffect(() => {
    if (loadedKeyRef.current !== storageKey) {
      loadedKeyRef.current = storageKey
      setState(load(storageKey))
      return
    }
    try {
      const persisted: DockState = {
        items: state.items.map(stripVsCodeUrl),
        expandedKeys: state.expandedKeys,
        expandedKey: state.expandedKey,
      }
      window.localStorage.setItem(storageKey, JSON.stringify(persisted))
    } catch {
      /* storage full / disabled */
    }
  }, [state, storageKey])

  const dock = useCallback((item: DockedSession, opts?: { expand?: boolean; split?: boolean }) => {
    const key = dockKey(item.kind, item.id)
    setState((prev) => {
      const rest = prev.items.filter((i) => dockKey(i.kind, i.id) !== key)
      const nextItems = [...rest, item].slice(-MAX_DOCKED_SESSIONS)
      if (opts?.expand === false) {
        return normalizeExpandedKeys(prev.expandedKeys, nextItems)
      }
      if (opts?.split) {
        const nextKeys = prev.expandedKeys.includes(key)
          ? prev.expandedKeys
          : [...prev.expandedKeys.slice(-(MAX_SPLIT_PANES - 1)), key]
        return normalizeExpandedKeys(nextKeys, nextItems)
      }
      if (prev.expandedKeys.includes(key)) {
        return normalizeExpandedKeys(prev.expandedKeys, nextItems)
      }
      const nextKeys =
        prev.expandedKeys.length <= 1
          ? [key]
          : [...prev.expandedKeys.slice(0, -1), key]
      return normalizeExpandedKeys(nextKeys, nextItems)
    })
  }, [])

  const undock = useCallback((key: string) => {
    setState((prev) => {
      const nextItems = prev.items.filter((i) => dockKey(i.kind, i.id) !== key)
      const nextKeys = prev.expandedKeys.filter((k) => k !== key)
      return normalizeExpandedKeys(nextKeys, nextItems)
    })
  }, [])

  const setExpanded = useCallback((key: string | null) => {
    setState((prev) => normalizeExpandedKeys(key ? [key] : [], prev.items))
  }, [])

  const toggleSplit = useCallback((key: string) => {
    setState((prev) => {
      if (prev.expandedKeys.includes(key)) {
        return normalizeExpandedKeys(
          prev.expandedKeys.filter((k) => k !== key),
          prev.items
        )
      }
      const nextKeys =
        prev.expandedKeys.length >= MAX_SPLIT_PANES
          ? [...prev.expandedKeys.slice(0, MAX_SPLIT_PANES - 1), key]
          : [...prev.expandedKeys, key]
      return normalizeExpandedKeys(nextKeys, prev.items)
    })
  }, [])

  const collapsePane = useCallback((key: string) => {
    setState((prev) =>
      normalizeExpandedKeys(
        prev.expandedKeys.filter((k) => k !== key),
        prev.items
      )
    )
  }, [])

  const collapseAll = useCallback(() => {
    setState((prev) => normalizeExpandedKeys([], prev.items))
  }, [])

  const updateTitle = useCallback((key: string, title: string) => {
    setState((prev) => {
      const target = prev.items.find((i) => dockKey(i.kind, i.id) === key)
      if (!target || target.title === title) return prev
      return { ...prev, items: prev.items.map((i) => (i === target ? { ...i, title } : i)) }
    })
  }, [])

  const updatePane = useCallback((key: string, pane: DockedPaneMode, vscodeUrl?: string | null) => {
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
      toggleSplit,
      collapsePane,
      collapseAll,
      updateTitle,
      updatePane,
      isDocked: (key: string) => state.items.some((i) => dockKey(i.kind, i.id) === key),
      isExpanded: (key: string) => state.expandedKeys.includes(key),
    }),
    [state, dock, undock, setExpanded, toggleSplit, collapsePane, collapseAll, updateTitle, updatePane]
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
