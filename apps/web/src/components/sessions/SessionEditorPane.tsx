import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  ChevronDown,
  ChevronRight,
  Code2,
  Columns2,
  Diff,
  FileCode,
  FilePlus,
  FileText,
  Folder,
  FolderOpen,
  Loader2,
  Lock,
  RefreshCw,
  RotateCcw,
  Rows2,
  Save,
  Search,
  Sprout,
  Trash2,
  X,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../api/client'
import { detectLanguageFromPath } from '../../utils/codeLanguage'
import { formatBytes } from '../../utils/format'
import { MonacoCodeEditor } from '../common/MonacoCodeEditor'
import type {
  SessionWorkspaceFileContent,
  SessionWorkspaceFileEntry,
} from '../../types'

/** Files larger than 1 MB are opened in read-only mode in Monaco. */
const MAX_EDITABLE_BYTES = 1_000_000

const BINARY_EXTENSIONS = new Set([
  'apk',
  'aab',
  'ipa',
  'exe',
  'dll',
  'so',
  'dylib',
  'whl',
  'zip',
  'tar',
  'gz',
  'tgz',
  'bz2',
  'xz',
  '7z',
  'rar',
  'jar',
  'war',
  'class',
  'pyc',
  'pyo',
  'pyd',
  'wasm',
  'png',
  'jpg',
  'jpeg',
  'gif',
  'webp',
  'ico',
  'bmp',
  'tiff',
  'mp3',
  'mp4',
  'mov',
  'avi',
  'mkv',
  'wav',
  'ogg',
  'flac',
  'pdf',
  'woff',
  'woff2',
  'ttf',
  'otf',
  'eot',
  'sqlite',
  'db',
])

function isBinaryFilePath(filePath: string | null | undefined): boolean {
  if (!filePath) return false
  const name = (filePath.split('/').pop() || filePath).toLowerCase()
  const dot = name.lastIndexOf('.')
  if (dot === -1 || dot === name.length - 1) return false
  return BINARY_EXTENSIONS.has(name.slice(dot + 1))
}

export interface SessionEditorPaneProps {
  projectId: string
  kind: 'chat' | 'terminal'
  sessionId: string
  canContribute: boolean
  /** True while a coding agent is actively writing / running a turn in the container */
  isAgentBusy?: boolean
  agentHandle?: string | null
  onOpenPush?: () => void
  onChanged?: () => void
  className?: string
}

interface TreeNode {
  name: string
  path: string
  isDir: boolean
  entry?: SessionWorkspaceFileEntry
  children: TreeNode[]
  hasChanges: boolean
}

function buildFileTree(files: SessionWorkspaceFileEntry[]): TreeNode[] {
  const root: TreeNode = {
    name: '',
    path: '',
    isDir: true,
    children: [],
    hasChanges: false,
  }

  for (const entry of files) {
    const parts = entry.path.split('/').filter(Boolean)
    let curr = root
    for (let i = 0; i < parts.length; i++) {
      const part = parts[i]
      const isLast = i === parts.length - 1
      const subPath = parts.slice(0, i + 1).join('/')
      if (isLast) {
        curr.children.push({
          name: part,
          path: subPath,
          isDir: false,
          entry,
          children: [],
          hasChanges: Boolean(entry.status),
        })
      } else {
        let next = curr.children.find((c) => c.isDir && c.name === part)
        if (!next) {
          next = {
            name: part,
            path: subPath,
            isDir: true,
            children: [],
            hasChanges: false,
          }
          curr.children.push(next)
        }
        curr = next
      }
    }
  }

  const sortAndMark = (node: TreeNode): boolean => {
    let anyChanged = Boolean(node.entry?.status)
    for (const child of node.children) {
      if (sortAndMark(child)) anyChanged = true
    }
    node.hasChanges = anyChanged
    node.children.sort((a, b) => {
      if (a.isDir !== b.isDir) return a.isDir ? -1 : 1
      return a.name.localeCompare(b.name)
    })
    return anyChanged
  }

  sortAndMark(root)
  return root.children
}

function computeLineDiffStats(
  oldText: string,
  newText: string
): { adds: number; dels: number } {
  if (oldText === newText) return { adds: 0, dels: 0 }
  const oldLines = oldText ? oldText.split('\n') : []
  const newLines = newText ? newText.split('\n') : []

  let start = 0
  while (
    start < oldLines.length &&
    start < newLines.length &&
    oldLines[start] === newLines[start]
  ) {
    start++
  }
  let oldEnd = oldLines.length - 1
  let newEnd = newLines.length - 1
  while (
    oldEnd >= start &&
    newEnd >= start &&
    oldLines[oldEnd] === newLines[newEnd]
  ) {
    oldEnd--
    newEnd--
  }

  const midOld = oldLines.slice(start, oldEnd + 1)
  const midNew = newLines.slice(start, newEnd + 1)
  const m = midOld.length
  const n = midNew.length
  if (m * n > 250_000) {
    return { adds: n, dels: m }
  }
  const dp = new Uint16Array((m + 1) * (n + 1))
  const width = n + 1
  for (let i = m - 1; i >= 0; i--) {
    for (let j = n - 1; j >= 0; j--) {
      if (midOld[i] === midNew[j]) {
        dp[i * width + j] = dp[(i + 1) * width + (j + 1)] + 1
      } else {
        const a = dp[(i + 1) * width + j]
        const b = dp[i * width + (j + 1)]
        dp[i * width + j] = a >= b ? a : b
      }
    }
  }
  const lcs = dp[0]
  return { adds: Math.max(0, n - lcs), dels: Math.max(0, m - lcs) }
}

const STATUS_BADGE: Record<
  'added' | 'modified' | 'deleted',
  { short: string; label: string; className: string }
> = {
  added: {
    short: 'A',
    label: 'Added',
    className:
      'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
  },
  modified: {
    short: 'M',
    label: 'Modified',
    className:
      'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30',
  },
  deleted: {
    short: 'D',
    label: 'Deleted',
    className:
      'bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30',
  },
}

export const SessionEditorPane: React.FC<SessionEditorPaneProps> = ({
  projectId,
  kind,
  sessionId,
  canContribute,
  isAgentBusy = false,
  agentHandle,
  onOpenPush,
  onChanged,
  className = 'h-[calc(100vh-13rem)] min-h-[580px]',
}) => {
  const queryClient = useQueryClient()

  const [filterText, setFilterText] = useState('')
  const [changedOnly, setChangedOnly] = useState(false)
  const [useTreeView, setUseTreeView] = useState(true)
  const [expandedDirs, setExpandedDirs] = useState<Record<string, boolean>>({})
  const [openTabs, setOpenTabs] = useState<string[]>([])
  const [activePath, setActivePath] = useState<string | null>(null)
  const [viewMode, setViewMode] = useState<'edit' | 'diff'>('edit')
  const [sideBySideDiff, setSideBySideDiff] = useState<boolean>(false)

  // Unsaved buffer per file path
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  const [savingPath, setSavingPath] = useState<string | null>(null)
  const [justSavedPath, setJustSavedPath] = useState<string | null>(null)
  const [discarding, setDiscarding] = useState(false)
  const [confirmDiscardAll, setConfirmDiscardAll] = useState(false)
  const [confirmDeleteFile, setConfirmDeleteFile] = useState(false)
  const [newFileOpen, setNewFileOpen] = useState(false)
  const [newFilePath, setNewFilePath] = useState('')
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  const filesKey = useMemo(
    () => ['sessionWorkspaceFiles', kind, projectId, sessionId],
    [kind, projectId, sessionId]
  )

  const filesQuery = useQuery({
    queryKey: filesKey,
    queryFn: () => api.projects.listSessionFiles(projectId, kind, sessionId),
    refetchInterval: isAgentBusy ? 3_500 : 10_000,
  })

  const allFiles = useMemo(() => filesQuery.data?.files ?? [], [filesQuery.data])
  const changedFiles = useMemo(
    () => filesQuery.data?.changed_files ?? [],
    [filesQuery.data]
  )

  // Auto-select first changed file or README / first source file on initial load
  useEffect(() => {
    if (activePath || allFiles.length === 0) return
    const firstChanged = changedFiles.find((f) => f.status !== 'deleted')?.path
    const readme = allFiles.find((f) =>
      /^readme(\.md|\.txt)?$/i.test(f.path.split('/').pop() || '')
    )?.path
    const initial = firstChanged || readme || allFiles[0]?.path
    if (initial) {
      setActivePath(initial)
      setOpenTabs((prev) => (prev.includes(initial) ? prev : [...prev, initial]))
      const parts = initial.split('/')
      if (parts.length > 1) {
        const nextDirs: Record<string, boolean> = {}
        for (let i = 1; i < parts.length; i++) {
          nextDirs[parts.slice(0, i).join('/')] = true
        }
        setExpandedDirs((prev) => ({ ...nextDirs, ...prev }))
      }
    }
  }, [activePath, allFiles, changedFiles])

  const fileContentKey = useMemo(
    () => ['sessionWorkspaceFile', kind, projectId, sessionId, activePath],
    [kind, projectId, sessionId, activePath]
  )

  const fileQuery = useQuery({
    queryKey: fileContentKey,
    queryFn: () =>
      api.projects.getSessionFile(projectId, kind, sessionId, activePath!),
    enabled: Boolean(activePath),
  })

  // When an agent finishes a turn, refresh the workspace file tree and active file
  const prevBusyRef = useRef(isAgentBusy)
  useEffect(() => {
    if (prevBusyRef.current && !isAgentBusy) {
      void queryClient.invalidateQueries({ queryKey: filesKey })
      if (activePath) {
        void queryClient.invalidateQueries({ queryKey: fileContentKey })
      }
    }
    prevBusyRef.current = isAgentBusy
  }, [isAgentBusy, activePath, fileContentKey, filesKey, queryClient])

  const fileData: SessionWorkspaceFileContent | undefined = fileQuery.data
  const activeEntry = useMemo(
    () => allFiles.find((f) => f.path === activePath),
    [allFiles, activePath]
  )
  const currentContent =
    activePath && drafts[activePath] !== undefined
      ? drafts[activePath]
      : (fileData?.content ?? '')
  const isDirty = Boolean(
    activePath &&
      fileData &&
      drafts[activePath] !== undefined &&
      drafts[activePath] !== fileData.content
  )

  const byteSize = useMemo(
    () => activeEntry?.size ?? new Blob([currentContent]).size,
    [activeEntry?.size, currentContent]
  )
  const isBinary = Boolean(fileData?.is_binary || isBinaryFilePath(activePath))
  const isLargeFile = byteSize > MAX_EDITABLE_BYTES
  const effectiveReadOnly =
    !canContribute || isAgentBusy || isLargeFile || isBinary

  const selectFile = useCallback((path: string) => {
    setActivePath(path)
    setConfirmDeleteFile(false)
    setOpenTabs((prev) => (prev.includes(path) ? prev : [...prev, path]))
    const parts = path.split('/')
    if (parts.length > 1) {
      setExpandedDirs((prev) => {
        const next = { ...prev }
        for (let i = 1; i < parts.length; i++) {
          next[parts.slice(0, i).join('/')] = true
        }
        return next
      })
    }
  }, [])

  const isPathDirty = useCallback(
    (path: string): boolean => {
      if (drafts[path] === undefined) return false
      const cached = queryClient.getQueryData<SessionWorkspaceFileContent>([
        'sessionWorkspaceFile',
        kind,
        projectId,
        sessionId,
        path,
      ])
      return cached ? drafts[path] !== cached.content : true
    },
    [drafts, kind, projectId, queryClient, sessionId]
  )

  const hasAnyDirtyDraft = useMemo(
    () => Object.keys(drafts).some((p) => isPathDirty(p)),
    [drafts, isPathDirty]
  )

  useEffect(() => {
    if (!hasAnyDirtyDraft) return
    const handleBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault()
      e.returnValue = ''
    }
    window.addEventListener('beforeunload', handleBeforeUnload)
    return () => window.removeEventListener('beforeunload', handleBeforeUnload)
  }, [hasAnyDirtyDraft])

  const closeTab = (path: string, e: React.MouseEvent) => {
    e.stopPropagation()
    if (isPathDirty(path)) {
      if (!window.confirm(`Discard unsaved changes to ${path}?`)) {
        return
      }
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[path]
        return next
      })
    }
    setOpenTabs((prev) => {
      const next = prev.filter((p) => p !== path)
      if (activePath === path) {
        setActivePath(next[next.length - 1] ?? null)
      }
      return next
    })
  }

  const saveCurrentFile = useCallback(async () => {
    if (!activePath || effectiveReadOnly) return
    const textToSave =
      drafts[activePath] !== undefined
        ? drafts[activePath]
        : (fileData?.content ?? '')
    setSavingPath(activePath)
    setErrorMsg(null)
    try {
      const saved = await api.projects.saveSessionFile(
        projectId,
        kind,
        sessionId,
        {
          path: activePath,
          content: textToSave,
        }
      )
      queryClient.setQueryData(fileContentKey, saved)
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[activePath]
        return next
      })
      setJustSavedPath(activePath)
      window.setTimeout(() => {
        setJustSavedPath((curr) => (curr === activePath ? null : curr))
      }, 1600)
      await queryClient.invalidateQueries({ queryKey: filesKey })
      onChanged?.()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Could not save file')
    } finally {
      setSavingPath(null)
    }
  }, [
    activePath,
    drafts,
    effectiveReadOnly,
    fileContentKey,
    fileData?.content,
    filesKey,
    kind,
    onChanged,
    projectId,
    queryClient,
    sessionId,
  ])

  const handleCreateNewFile = async (e: React.FormEvent) => {
    e.preventDefault()
    const clean = newFilePath.trim().replace(/^\/+/, '')
    if (!clean || !canContribute || isAgentBusy) return
    setSavingPath(clean)
    setErrorMsg(null)
    try {
      const created = await api.projects.saveSessionFile(
        projectId,
        kind,
        sessionId,
        {
          path: clean,
          content: '',
        }
      )
      setNewFilePath('')
      setNewFileOpen(false)
      await queryClient.invalidateQueries({ queryKey: filesKey })
      queryClient.setQueryData(
        ['sessionWorkspaceFile', kind, projectId, sessionId, created.path],
        created
      )
      selectFile(created.path)
      setViewMode('edit')
      onChanged?.()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Could not create file')
    } finally {
      setSavingPath(null)
    }
  }

  const handleDiscardFile = async (path: string) => {
    if (!canContribute || isAgentBusy) return
    const entry = allFiles.find((f) => f.path === path)
    if (!entry?.status && drafts[path] !== undefined) {
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[path]
        return next
      })
      return
    }
    setDiscarding(true)
    setErrorMsg(null)
    try {
      const updated = await api.projects.discardSessionChanges(
        projectId,
        kind,
        sessionId,
        path
      )
      queryClient.setQueryData(filesKey, updated)
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[path]
        return next
      })
      const stillExists = updated.files.some((f) => f.path === path)
      if (stillExists) {
        await queryClient.invalidateQueries({
          queryKey: ['sessionWorkspaceFile', kind, projectId, sessionId, path],
        })
      } else {
        setOpenTabs((prev) => prev.filter((p) => p !== path))
        if (activePath === path) {
          setActivePath(updated.files[0]?.path ?? null)
        }
      }
      onChanged?.()
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Could not discard file changes'
      )
    } finally {
      setDiscarding(false)
    }
  }

  const handleDiscardAll = async () => {
    if (!canContribute || isAgentBusy) return
    setDiscarding(true)
    setErrorMsg(null)
    try {
      const updated = await api.projects.discardSessionChanges(
        projectId,
        kind,
        sessionId,
        null
      )
      queryClient.setQueryData(filesKey, updated)
      setDrafts({})
      setConfirmDiscardAll(false)
      await queryClient.invalidateQueries({
        queryKey: ['sessionWorkspaceFile', kind, projectId, sessionId],
      })
      onChanged?.()
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Could not discard changes'
      )
    } finally {
      setDiscarding(false)
    }
  }

  const handleDeleteFile = async () => {
    if (!activePath || !canContribute || isAgentBusy) return
    setDiscarding(true)
    setErrorMsg(null)
    try {
      await api.projects.deleteSessionFile(
        projectId,
        kind,
        sessionId,
        activePath
      )
      setConfirmDeleteFile(false)
      setDrafts((prev) => {
        const next = { ...prev }
        delete next[activePath]
        return next
      })
      await queryClient.invalidateQueries({ queryKey: filesKey })
      await queryClient.invalidateQueries({ queryKey: fileContentKey })
      onChanged?.()
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : 'Could not delete file')
    } finally {
      setDiscarding(false)
    }
  }

  const filteredFiles = useMemo(() => {
    const q = filterText.trim().toLowerCase()
    return allFiles.filter((f) => {
      if (changedOnly && !f.status && drafts[f.path] === undefined) return false
      if (q && !f.path.toLowerCase().includes(q)) return false
      return true
    })
  }, [allFiles, changedOnly, drafts, filterText])

  const treeNodes = useMemo(() => buildFileTree(filteredFiles), [filteredFiles])

  const allDirPaths = useMemo(() => {
    const dirs = new Set<string>()
    for (const f of filteredFiles) {
      const parts = f.path.split('/').filter(Boolean)
      for (let i = 1; i < parts.length; i++) {
        dirs.add(parts.slice(0, i).join('/'))
      }
    }
    return Array.from(dirs)
  }, [filteredFiles])

  const language = useMemo(
    () => detectLanguageFromPath(activePath || 'file.txt'),
    [activePath]
  )

  const diffStats = useMemo(() => {
    if (viewMode !== 'diff' || !fileData) return { adds: 0, dels: 0 }
    return computeLineDiffStats(fileData.original_content ?? '', currentContent)
  }, [viewMode, fileData, currentContent])

  const renderTree = (nodes: TreeNode[], depth = 0): React.ReactNode =>
    nodes.map((node) => {
      if (node.isDir) {
        const isOpen =
          expandedDirs[node.path] ??
          (depth === 0 ||
            node.hasChanges ||
            Boolean(filterText.trim()) ||
            changedOnly)
        return (
          <div key={node.path}>
            <button
              type="button"
              onClick={() =>
                setExpandedDirs((prev) => ({ ...prev, [node.path]: !isOpen }))
              }
              style={{ paddingLeft: `${depth * 12 + 8}px` }}
              className="w-full flex items-center gap-1.5 py-1 pr-2 rounded-md text-left text-xs text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800/70 cursor-pointer select-none"
            >
              {isOpen ? (
                <ChevronDown size={12} className="text-zinc-400 shrink-0" />
              ) : (
                <ChevronRight size={12} className="text-zinc-400 shrink-0" />
              )}
              {isOpen ? (
                <FolderOpen
                  size={13}
                  className="text-teal-600 dark:text-teal-400 shrink-0"
                />
              ) : (
                <Folder
                  size={13}
                  className="text-teal-600 dark:text-teal-400 shrink-0"
                />
              )}
              <span className="truncate font-medium">{node.name}</span>
              {node.hasChanges && (
                <span className="ml-auto w-1.5 h-1.5 rounded-full bg-amber-500 shrink-0" />
              )}
            </button>
            {isOpen && renderTree(node.children, depth + 1)}
          </div>
        )
      }

      const st = node.entry?.status
      const badge = st ? STATUS_BADGE[st] : null
      const hasUnsaved = isPathDirty(node.path)
      const isSelected = activePath === node.path

      return (
        <button
          key={node.path}
          type="button"
          onClick={() => {
            selectFile(node.path)
            if (changedOnly) {
              setViewMode('diff')
            }
          }}
          style={{ paddingLeft: `${depth * 12 + 20}px` }}
          className={`w-full flex items-center justify-between gap-1.5 py-1 pr-2 rounded-md text-left text-xs font-mono cursor-pointer transition ${
            isSelected
              ? 'bg-teal-500/15 text-teal-800 dark:text-teal-200 font-semibold'
              : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800/70'
          }`}
        >
          <span className="flex items-center gap-1.5 min-w-0">
            <FileCode
              size={12}
              className={
                isSelected
                  ? 'text-teal-600 dark:text-teal-400 shrink-0'
                  : 'text-zinc-400 shrink-0'
              }
            />
            <span
              className={`truncate ${st === 'deleted' ? 'line-through opacity-70' : ''}`}
            >
              {node.name}
            </span>
          </span>
          <span className="flex items-center gap-1 shrink-0">
            {hasUnsaved && (
              <span
                title="Unsaved changes"
                className="w-2 h-2 rounded-full bg-teal-500"
              />
            )}
            {badge && (
              <span
                className={`px-1.5 py-0.5 rounded text-[11px] font-bold border ${badge.className}`}
              >
                {badge.short}
              </span>
            )}
          </span>
        </button>
      )
    })

  return (
    <div className={`flex flex-col bg-white dark:bg-zinc-950 ${className}`}>
      {errorMsg && (
        <div className="px-4 py-2 bg-rose-500/10 border-b border-rose-500/30 text-rose-700 dark:text-rose-300 text-xs flex items-center justify-between gap-2">
          <span className="flex items-center gap-1.5">
            <AlertCircle size={13} className="shrink-0" />
            {errorMsg}
          </span>
          <button
            type="button"
            onClick={() => setErrorMsg(null)}
            className="cursor-pointer"
          >
            <X size={13} />
          </button>
        </div>
      )}

      {isAgentBusy && (
        <div className="px-4 py-1.5 bg-amber-500/10 border-b border-amber-500/30 text-amber-800 dark:text-amber-300 text-xs flex items-center gap-2 shrink-0">
          <Lock size={12} className="shrink-0" />
          <span>
            Files are locked while{' '}
            <strong className="font-mono">
              {agentHandle ? `@${agentHandle}` : 'the agent'}
            </strong>{' '}
            is writing so your edits and the agent’s changes don’t overwrite each other.
          </span>
        </div>
      )}

      <div className="flex-1 grid grid-cols-1 md:grid-cols-[260px_1fr] min-h-0 overflow-hidden">
        {/* Left Sidebar: File Tree & Workspace Changes */}
        <div className="border-r border-zinc-200 dark:border-zinc-800 flex flex-col min-h-0 bg-zinc-50/60 dark:bg-zinc-900/40">
          <div className="p-2.5 border-b border-zinc-200 dark:border-zinc-800 flex flex-col gap-2">
            <div className="flex items-center gap-1.5">
              <div className="relative flex-1">
                <Search
                  size={13}
                  className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400"
                />
                <input
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  placeholder="Filter files…"
                  className="w-full pl-7 pr-2 py-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 text-xs text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500"
                />
              </div>
              {canContribute && !isAgentBusy && (
                <button
                  type="button"
                  onClick={() => setNewFileOpen((v) => !v)}
                  title="New file"
                  className="p-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                >
                  <FilePlus size={14} />
                </button>
              )}
              <button
                type="button"
                onClick={() => void filesQuery.refetch()}
                title="Refresh workspace files"
                className="p-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
              >
                <RefreshCw
                  size={14}
                  className={filesQuery.isFetching ? 'animate-spin' : ''}
                />
              </button>
            </div>

            <div className="flex items-center justify-between gap-1 text-xs">
              <div className="inline-flex rounded-lg p-0.5 bg-zinc-200/70 dark:bg-zinc-800">
                <button
                  type="button"
                  onClick={() => setChangedOnly(false)}
                  className={`px-2 py-0.5 rounded-md font-semibold cursor-pointer ${
                    !changedOnly
                      ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs'
                      : 'text-zinc-500'
                  }`}
                >
                  All ({allFiles.length})
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setChangedOnly(true)
                    setViewMode('diff')
                  }}
                  className={`px-2 py-0.5 rounded-md font-semibold cursor-pointer ${
                    changedOnly
                      ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                      : 'text-zinc-500'
                  }`}
                >
                  Changed ({changedFiles.length})
                </button>
              </div>

              <div className="flex items-center gap-1">
                <button
                  type="button"
                  onClick={() => setUseTreeView((v) => !v)}
                  title={useTreeView ? 'Switch to flat file list' : 'Switch to folder tree'}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border cursor-pointer ${
                    useTreeView
                      ? 'bg-teal-500/12 border-teal-500/30 text-teal-700 dark:text-teal-300'
                      : 'bg-zinc-200/60 dark:bg-zinc-800 border-transparent text-zinc-500'
                  }`}
                >
                  <Folder size={11} />
                  <span>{useTreeView ? 'Tree' : 'Flat'}</span>
                </button>
                {useTreeView && allDirPaths.length > 0 && (
                  <button
                    type="button"
                    onClick={() => {
                      const anyOpen = allDirPaths.some(
                        (d) => expandedDirs[d] ?? !d.includes('/')
                      )
                      const next: Record<string, boolean> = {}
                      for (const d of allDirPaths) {
                        next[d] = !anyOpen
                      }
                      setExpandedDirs(next)
                    }}
                    title="Expand / collapse all folders"
                    className="p-1 rounded-md text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 cursor-pointer"
                  >
                    <ChevronDown size={12} />
                  </button>
                )}
              </div>
            </div>

            {newFileOpen && (
              <form
                onSubmit={(e) => void handleCreateNewFile(e)}
                className="flex items-center gap-1 pt-0.5"
              >
                <input
                  value={newFilePath}
                  onChange={(e) => setNewFilePath(e.target.value)}
                  placeholder="path/to/file.ts"
                  autoFocus
                  className="flex-1 min-w-0 px-2 py-1 rounded-lg border border-teal-500 bg-white dark:bg-zinc-900 text-xs font-mono text-zinc-900 dark:text-zinc-100"
                />
                <button
                  type="submit"
                  className="px-2.5 py-1 rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold cursor-pointer"
                >
                  Add
                </button>
                <button
                  type="button"
                  onClick={() => {
                    setNewFileOpen(false)
                    setNewFilePath('')
                  }}
                  className="p-1 text-zinc-400 hover:text-zinc-600 cursor-pointer"
                >
                  <X size={13} />
                </button>
              </form>
            )}
          </div>

          <div className="flex-1 overflow-y-auto p-1.5 space-y-0.5">
            {filesQuery.isLoading ? (
              <div className="py-8 flex items-center justify-center gap-2 text-xs text-zinc-500">
                <Loader2 size={14} className="animate-spin" />
                Loading workspace…
              </div>
            ) : treeNodes.length === 0 ? (
              <div className="py-8 px-3 text-center text-xs text-zinc-500">
                {changedOnly
                  ? 'No modified files in the workspace yet.'
                  : 'No matching files found.'}
              </div>
            ) : useTreeView ? (
              renderTree(treeNodes)
            ) : (
              filteredFiles.map((entry) => {
                const st = entry.status
                const badge = st ? STATUS_BADGE[st] : null
                const hasUnsaved = drafts[entry.path] !== undefined
                const isSelected = activePath === entry.path
                return (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => {
                      selectFile(entry.path)
                      if (changedOnly) {
                        setViewMode('diff')
                      }
                    }}
                    className={`w-full flex items-center justify-between gap-1.5 px-2.5 py-1 rounded-md text-left text-xs font-mono cursor-pointer transition ${
                      isSelected
                        ? 'bg-teal-500/15 text-teal-800 dark:text-teal-200 font-semibold'
                        : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800/70'
                    }`}
                  >
                    <span className="flex items-center gap-1.5 min-w-0">
                      <FileCode
                        size={12}
                        className={
                          isSelected
                            ? 'text-teal-600 dark:text-teal-400 shrink-0'
                            : 'text-zinc-400 shrink-0'
                        }
                      />
                      <span
                        className={`truncate ${st === 'deleted' ? 'line-through opacity-70' : ''}`}
                      >
                        {entry.path}
                      </span>
                    </span>
                    <span className="flex items-center gap-1 shrink-0">
                      {hasUnsaved && (
                        <span
                          title="Unsaved changes"
                          className="w-2 h-2 rounded-full bg-teal-500"
                        />
                      )}
                      {badge && (
                        <span
                          className={`px-1.5 py-0.5 rounded text-[11px] font-bold border ${badge.className}`}
                        >
                          {badge.short}
                        </span>
                      )}
                    </span>
                  </button>
                )
              })
            )}
          </div>

          {/* Sidebar Footer: Workspace Changes Actions */}
          {canContribute && changedFiles.length > 0 && (
            <div className="p-2.5 border-t border-zinc-200 dark:border-zinc-800 bg-white/80 dark:bg-zinc-900/80 flex flex-col gap-2">
              {confirmDiscardAll ? (
                <div className="p-2 rounded-lg bg-rose-500/10 border border-rose-500/30 flex flex-col gap-1.5 text-xs">
                  <span className="text-rose-800 dark:text-rose-200 font-medium">
                    Revert all {changedFiles.length} changed file(s) to baseline?
                  </span>
                  <div className="flex gap-1.5">
                    <button
                      type="button"
                      disabled={discarding || isAgentBusy}
                      onClick={() => void handleDiscardAll()}
                      className="flex-1 py-1 rounded-md bg-rose-600 hover:bg-rose-500 text-white font-semibold cursor-pointer disabled:opacity-60"
                    >
                      {discarding ? 'Discarding…' : 'Discard all'}
                    </button>
                    <button
                      type="button"
                      onClick={() => setConfirmDiscardAll(false)}
                      className="flex-1 py-1 rounded-md border border-zinc-300 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 font-semibold cursor-pointer"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              ) : (
                <div className="flex items-center justify-between gap-1.5">
                  <button
                    type="button"
                    disabled={isAgentBusy}
                    onClick={() => setConfirmDiscardAll(true)}
                    className="inline-flex items-center gap-1 px-2 py-1 rounded-lg text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 cursor-pointer disabled:opacity-50"
                  >
                    <RotateCcw size={12} />
                    Discard all ({changedFiles.length})
                  </button>
                  {onOpenPush && (
                    <button
                      type="button"
                      disabled={isAgentBusy}
                      onClick={onOpenPush}
                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white cursor-pointer disabled:opacity-50"
                    >
                      <Sprout size={12} />
                      Push to seed
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right Area: Open File Tabs + Editor / Diff View */}
        <div className="flex flex-col min-h-0 min-w-0 overflow-hidden">
          {/* Open File Tabs */}
          {openTabs.length > 0 && (
            <div className="flex items-center overflow-x-auto border-b border-zinc-200 dark:border-zinc-800 bg-zinc-100/70 dark:bg-zinc-900/60">
              {openTabs.map((tabPath) => {
                const fileName = tabPath.split('/').pop() || tabPath
                const entry = allFiles.find((f) => f.path === tabPath)
                const tabDirty = isPathDirty(tabPath)
                const active = activePath === tabPath
                return (
                  <div
                    key={tabPath}
                    onClick={() => selectFile(tabPath)}
                    title={tabPath}
                    className={`group flex items-center gap-1.5 px-3 py-1.5 border-r border-zinc-200 dark:border-zinc-800 text-xs font-mono cursor-pointer shrink-0 select-none ${
                      active
                        ? 'bg-white dark:bg-zinc-950 text-teal-700 dark:text-teal-300 font-semibold border-b-2 border-b-teal-500'
                        : 'text-zinc-600 dark:text-zinc-400 hover:bg-white/60 dark:hover:bg-zinc-900'
                    }`}
                  >
                    <span>{fileName}</span>
                    {tabDirty ? (
                      <span className="w-2 h-2 rounded-full bg-teal-500" />
                    ) : entry?.status ? (
                      <span
                        className={`text-xs font-bold ${
                          entry.status === 'added'
                            ? 'text-emerald-500'
                            : entry.status === 'deleted'
                              ? 'text-rose-500'
                              : 'text-amber-500'
                        }`}
                      >
                        {STATUS_BADGE[entry.status].short}
                      </span>
                    ) : null}
                    <button
                      type="button"
                      onClick={(e) => closeTab(tabPath, e)}
                      className="p-0.5 rounded hover:bg-zinc-200 dark:hover:bg-zinc-800 text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                    >
                      <X size={12} />
                    </button>
                  </div>
                )
              })}
            </div>
          )}

          {!activePath ? (
            <div className="flex-1 flex flex-col items-center justify-center gap-2 p-8 text-center text-xs text-zinc-500">
              <Code2 size={24} className="text-zinc-400" />
              <span>Select a file on the left to inspect or edit it.</span>
            </div>
          ) : fileQuery.isLoading ? (
            <div className="flex-1 flex items-center justify-center gap-2 text-xs text-zinc-500">
              <Loader2 size={15} className="animate-spin" />
              Loading {activePath}…
            </div>
          ) : !fileData ? (
            <div className="flex-1 flex items-center justify-center p-8 text-xs text-zinc-500">
              Could not load {activePath}.
            </div>
          ) : (
            <>
              {/* Active File Toolbar */}
              <div className="px-3.5 py-2 border-b border-zinc-200 dark:border-zinc-800 flex flex-wrap items-center justify-between gap-2 bg-white dark:bg-zinc-950 shrink-0">
                <div className="flex items-center gap-2 min-w-0 flex-wrap">
                  <FileText size={14} className="text-teal-500 shrink-0" />
                  <span className="text-xs font-mono font-semibold text-zinc-800 dark:text-zinc-200 truncate">
                    {fileData.path}
                  </span>
                  {fileData.status !== 'unchanged' && (
                    <span
                      className={`px-1.5 py-0.5 rounded text-xs font-semibold border ${
                        STATUS_BADGE[fileData.status].className
                      }`}
                    >
                      {STATUS_BADGE[fileData.status].label}
                    </span>
                  )}
                  {isDirty && (
                    <span className="px-1.5 py-0.5 rounded text-xs font-semibold bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/30">
                      Unsaved
                    </span>
                  )}
                  {isLargeFile && !isBinary && (
                    <span className="px-1.5 py-0.5 rounded text-xs font-semibold bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30">
                      Read-only (&gt; 1 MB)
                    </span>
                  )}
                  <span className="text-xs font-mono text-zinc-400">
                    {language} · {formatBytes(byteSize)}
                  </span>
                  {viewMode === 'diff' && !isBinary && (
                    <span className="text-xs font-mono inline-flex items-center gap-1.5">
                      <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                        +{diffStats.adds}
                      </span>
                      <span className="text-rose-600 dark:text-rose-400 font-semibold">
                        -{diffStats.dels}
                      </span>
                    </span>
                  )}
                </div>

                <div className="flex items-center gap-1.5 flex-wrap">
                  <div className="inline-flex rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 text-xs">
                    <button
                      type="button"
                      onClick={() => setViewMode('edit')}
                      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md font-semibold cursor-pointer ${
                        viewMode === 'edit'
                          ? 'bg-white dark:bg-zinc-800 text-teal-700 dark:text-teal-300 shadow-2xs'
                          : 'text-zinc-500'
                      }`}
                    >
                      <Code2 size={12} />
                      Edit
                    </button>
                    <button
                      type="button"
                      onClick={() => setViewMode('diff')}
                      className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-md font-semibold cursor-pointer ${
                        viewMode === 'diff'
                          ? 'bg-white dark:bg-zinc-800 text-teal-700 dark:text-teal-300 shadow-2xs'
                          : 'text-zinc-500'
                      }`}
                    >
                      <Diff size={12} />
                      Diff
                    </button>
                  </div>

                  {viewMode === 'diff' && !isBinary && (
                    <button
                      type="button"
                      onClick={() => setSideBySideDiff((v) => !v)}
                      title={
                        sideBySideDiff
                          ? 'Switch to inline diff'
                          : 'Switch to side-by-side diff'
                      }
                      className="inline-flex items-center gap-1 px-2 py-1 rounded-lg border border-zinc-200 dark:border-zinc-800 text-xs text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-900 cursor-pointer"
                    >
                      {sideBySideDiff ? (
                        <>
                          <Rows2 size={12} />
                          <span>Inline</span>
                        </>
                      ) : (
                        <>
                          <Columns2 size={12} />
                          <span>Split</span>
                        </>
                      )}
                    </button>
                  )}

                  {canContribute &&
                    !isAgentBusy &&
                    (fileData.status !== 'unchanged' || isDirty) && (
                      <Button
                        type="button"
                        size="sm"
                        variant="secondary"
                        isDisabled={discarding}
                        onClick={() => void handleDiscardFile(fileData.path)}
                        aria-label="Revert this file back to the session baseline"
                        className="rounded-lg text-xs font-semibold flex items-center gap-1 border border-zinc-200 dark:border-zinc-700 cursor-pointer"
                      >
                        <RotateCcw size={12} />
                        Discard file
                      </Button>
                    )}

                  {canContribute &&
                    !isAgentBusy &&
                    fileData.status !== 'deleted' &&
                    (confirmDeleteFile ? (
                      <span className="inline-flex items-center gap-1">
                        <button
                          type="button"
                          onClick={() => void handleDeleteFile()}
                          className="px-2 py-1 rounded-lg bg-rose-600 text-white text-xs font-semibold cursor-pointer"
                        >
                          Confirm delete
                        </button>
                        <button
                          type="button"
                          onClick={() => setConfirmDeleteFile(false)}
                          className="px-2 py-1 rounded-lg border border-zinc-300 dark:border-zinc-700 text-xs cursor-pointer"
                        >
                          Cancel
                        </button>
                      </span>
                    ) : (
                      <button
                        type="button"
                        onClick={() => setConfirmDeleteFile(true)}
                        title="Delete file from workspace"
                        className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-600 hover:bg-rose-500/10 cursor-pointer"
                      >
                        <Trash2 size={13} />
                      </button>
                    ))}

                  {canContribute &&
                    !effectiveReadOnly &&
                    fileData.status !== 'deleted' && (
                      <Button
                        type="button"
                        size="sm"
                        variant="primary"
                        isDisabled={savingPath === fileData.path || !isDirty}
                        onClick={() => void saveCurrentFile()}
                        aria-label="Save file to container workspace (Ctrl+S)"
                        className="rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold flex items-center gap-1.5 cursor-pointer disabled:opacity-50"
                      >
                        {savingPath === fileData.path ? (
                          <Loader2 size={12} className="animate-spin" />
                        ) : justSavedPath === fileData.path ? (
                          <Check size={12} />
                        ) : (
                          <Save size={12} />
                        )}
                        {justSavedPath === fileData.path ? 'Saved' : 'Save'}
                      </Button>
                    )}
                </div>
              </div>

              {/* Editor or Diff Body */}
              {isBinary ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-1.5 p-8 text-xs text-zinc-500">
                  <span className="font-semibold text-zinc-700 dark:text-zinc-300">
                    Binary file ({formatBytes(byteSize)})
                  </span>
                  <span>
                    Binary files (such as archives, images, or compiled packages) are not opened in the code editor.
                  </span>
                </div>
              ) : fileData.status === 'deleted' && viewMode === 'edit' ? (
                <div className="flex-1 flex flex-col items-center justify-center gap-3 p-8 text-xs text-zinc-500">
                  <span>
                    <code>{fileData.path}</code> was deleted in this session.
                  </span>
                  <div className="flex items-center gap-2">
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      onClick={() => setViewMode('diff')}
                      className="rounded-xl text-xs font-semibold border border-zinc-200 dark:border-zinc-700 cursor-pointer"
                    >
                      View deleted lines
                    </Button>
                    {canContribute && !isAgentBusy && (
                      <Button
                        type="button"
                        size="sm"
                        variant="primary"
                        onClick={() => void handleDiscardFile(fileData.path)}
                        className="rounded-xl bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
                      >
                        <RotateCcw size={12} />
                        Restore file
                      </Button>
                    )}
                  </div>
                </div>
              ) : (
                <div className="flex-1 flex min-h-0 relative overflow-hidden">
                  <MonacoCodeEditor
                    mode={viewMode}
                    filePath={fileData.path}
                    language={language}
                    value={currentContent}
                    originalValue={fileData.original_content ?? ''}
                    readOnly={effectiveReadOnly || fileData.status === 'deleted'}
                    sideBySide={sideBySideDiff}
                    onChange={(nextVal) => {
                      if (activePath && !effectiveReadOnly) {
                        setDrafts((prev) => ({ ...prev, [activePath]: nextVal }))
                      }
                    }}
                    onSave={() => void saveCurrentFile()}
                  />
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
