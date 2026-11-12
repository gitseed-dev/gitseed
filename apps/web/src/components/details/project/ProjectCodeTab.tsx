import React, { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import {
  AlertCircle,
  ArrowLeft,
  BookOpen,
  ChevronRight,
  ExternalLink,
  FileCode,
  Folder,
  FolderGit2,
  GitBranch,
  Loader2,
  Plus,
  RefreshCw,
  Search,
  Settings,
  Star,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api } from '../../../api/client'
import { MarkdownContent } from '../../common/MarkdownContent'
import { ProviderIcon } from '../../common/ProviderIcon'
import {
  EntityTableCard,
  EntityTableHeaderRow,
} from '../../layout/EntityPageLayout'
import { useAuth } from '../../../context/AuthContext'
import { useData } from '../../../context/DataContext'
import { useTheme } from '../../../context/ThemeContext'
import { formatProviderName } from '../../../utils/backends'
import { buildRepoWebUrl, safeExternalUrl } from '../../../utils/gitUrls'
import {
  CodeBlobViewer,
  RepoClonePopover,
  formatCodeBytes,
} from './ProjectCodeSubcomponents'
import type { Project, ProjectRepository } from '../../../types'

export interface ProjectCodeTabProps {
  project: Project
  repos: ProjectRepository[]
  initialRepoId?: number | null
  initialRef?: string | null
  initialPath?: string | null
  onOpenSettings: (section?: string) => void
}

export const ProjectCodeTab: React.FC<ProjectCodeTabProps> = ({
  project,
  repos,
  initialRepoId,
  initialRef,
  initialPath,
  onOpenSettings,
}) => {
  const { user } = useAuth()
  const { backends } = useData()
  const { theme } = useTheme()
  const canManage =
    user?.org_role === 'admin' || project.my_role === 'maintainer'

  const backendsById = useMemo(
    () => new Map(backends.map((b) => [b.id, b])),
    [backends]
  )

  const defaultRepo = useMemo(() => {
    if (initialRepoId) {
      const matched = repos.find((r) => r.id === initialRepoId)
      if (matched) return matched
    }
    return repos.find((r) => r.is_primary) || repos[0] || null
  }, [repos, initialRepoId])

  const initialUrlState = useMemo(() => {
    const params = new URLSearchParams(window.location.search)
    const repoParam = params.get('repo')
    const parsedRepoId = repoParam ? Number(repoParam) : null
    const refParam = params.get('ref')
    const pathParam = params.get('path')
    const modeParam = params.get('mode')
    return {
      repoId:
        parsedRepoId && !Number.isNaN(parsedRepoId) ? parsedRepoId : null,
      ref: refParam || null,
      path: pathParam || null,
      mode: (modeParam === 'blob' ? 'blob' : 'tree') as 'tree' | 'blob',
    }
  }, [])

  const [selectedRepoId, setSelectedRepoId] = useState<number | null>(
    initialUrlState.repoId ?? defaultRepo?.id ?? null
  )
  const [selectedRef, setSelectedRef] = useState<string>(
    initialUrlState.ref || initialRef || defaultRepo?.default_branch || 'main'
  )
  const [currentPath, setCurrentPath] = useState<string>(
    initialUrlState.path ?? initialPath ?? ''
  )
  const [viewMode, setViewMode] = useState<'tree' | 'blob'>(
    initialUrlState.mode
  )
  const [filterText, setFilterText] = useState<string>('')
  const [fallbackNotice, setFallbackNotice] = useState<{
    missingRef: string
    resolvedRef: string
  } | null>(null)

  const pushCodeHistory = (next: {
    repoId: number | null
    ref: string
    path: string
    mode: 'tree' | 'blob'
  }) => {
    const params = new URLSearchParams()
    if (next.repoId) params.set('repo', String(next.repoId))
    if (next.ref) params.set('ref', next.ref)
    if (next.path) params.set('path', next.path)
    if (next.mode === 'blob') params.set('mode', 'blob')
    const qs = params.toString()
    const targetUrl = `${window.location.pathname}${qs ? `?${qs}` : ''}`
    if (`${window.location.pathname}${window.location.search}` !== targetUrl) {
      window.history.pushState(
        { codeNav: next },
        '',
        targetUrl
      )
    }
  }

  useEffect(() => {
    const handlePopState = (event: PopStateEvent) => {
      const stateNav = (
        event.state as {
          codeNav?: {
            repoId: number | null
            ref: string
            path: string
            mode: 'tree' | 'blob'
          }
        } | null
      )?.codeNav
      if (stateNav) {
        if (stateNav.repoId !== null) setSelectedRepoId(stateNav.repoId)
        setSelectedRef(stateNav.ref)
        setCurrentPath(stateNav.path)
        setViewMode(stateNav.mode)
        setFilterText('')
        return
      }

      const params = new URLSearchParams(window.location.search)
      const repoParam = params.get('repo')
      const parsedRepoId = repoParam ? Number(repoParam) : null
      const fallbackRepo =
        (parsedRepoId
          ? repos.find((r) => r.id === parsedRepoId)
          : null) ||
        repos.find((r) => r.is_primary) ||
        repos[0] ||
        null

      if (fallbackRepo?.id) {
        setSelectedRepoId(fallbackRepo.id)
      }
      setSelectedRef(
        params.get('ref') || fallbackRepo?.default_branch || 'main'
      )
      setCurrentPath(params.get('path') || '')
      setViewMode(params.get('mode') === 'blob' ? 'blob' : 'tree')
      setFilterText('')
    }

    window.addEventListener('popstate', handlePopState)
    return () => window.removeEventListener('popstate', handlePopState)
  }, [repos])

  useEffect(() => {
    if (initialRepoId && repos.some((r) => r.id === initialRepoId)) {
      setSelectedRepoId(initialRepoId)
    } else if (
      selectedRepoId === null ||
      !repos.some((r) => r.id === selectedRepoId)
    ) {
      const next = repos.find((r) => r.is_primary) || repos[0] || null
      setSelectedRepoId(next?.id ?? null)
      if (next && !initialUrlState.ref) {
        setSelectedRef(initialRef || next.default_branch || 'main')
      }
    }
  }, [repos, initialRepoId, initialRef, selectedRepoId, initialUrlState.ref])

  const activeRepo = useMemo(
    () => repos.find((r) => r.id === selectedRepoId) || defaultRepo,
    [repos, selectedRepoId, defaultRepo]
  )

  const activeBackend = useMemo(
    () => (activeRepo ? backendsById.get(activeRepo.backend_id) || null : null),
    [activeRepo, backendsById]
  )

  const handleSelectRepo = (repo: ProjectRepository) => {
    const nextRef = repo.default_branch || 'main'
    setFallbackNotice(null)
    setSelectedRepoId(repo.id)
    setSelectedRef(nextRef)
    setCurrentPath('')
    setViewMode('tree')
    setFilterText('')
    pushCodeHistory({
      repoId: repo.id,
      ref: nextRef,
      path: '',
      mode: 'tree',
    })
  }

  const handleSelectRef = (nextRef: string) => {
    setFallbackNotice(null)
    setSelectedRef(nextRef)
    setFilterText('')
    pushCodeHistory({
      repoId: activeRepo?.id ?? selectedRepoId,
      ref: nextRef,
      path: currentPath,
      mode: viewMode,
    })
  }

  const branchesQuery = useQuery({
    queryKey: ['repo-branches', project.id, activeRepo?.id],
    queryFn: () => api.projects.listRepoBranches(project.id, activeRepo!.id!),
    enabled: Boolean(activeRepo?.id),
    staleTime: 30_000,
  })

  const treeDirectoryPath = useMemo(() => {
    if (viewMode === 'tree') return currentPath
    const lastSlash = currentPath.lastIndexOf('/')
    return lastSlash >= 0 ? currentPath.slice(0, lastSlash) : ''
  }, [viewMode, currentPath])

  const treeQuery = useQuery({
    queryKey: [
      'repo-tree',
      project.id,
      activeRepo?.id,
      selectedRef,
      treeDirectoryPath,
    ],
    queryFn: () =>
      api.projects.getRepoTree(project.id, activeRepo!.id!, {
        ref: selectedRef,
        path: treeDirectoryPath,
      }),
    enabled: Boolean(activeRepo?.id && selectedRef && viewMode === 'tree'),
    staleTime: 20_000,
    retry: 1,
  })

  const blobQuery = useQuery({
    queryKey: ['repo-blob', project.id, activeRepo?.id, selectedRef, currentPath],
    queryFn: () =>
      api.projects.getRepoBlob(project.id, activeRepo!.id!, {
        ref: selectedRef,
        path: currentPath,
      }),
    enabled: Boolean(
      activeRepo?.id && selectedRef && viewMode === 'blob' && currentPath
    ),
    staleTime: 20_000,
    retry: 1,
  })

  const branches = useMemo(() => {
    const list = branchesQuery.data?.branches || []
    if (list.length > 0) return list
    if (!activeRepo) return []
    return [
      {
        name: activeRepo.default_branch || 'main',
        commit_sha: null,
        is_default: true,
        protected: false,
        web_url:
          buildRepoWebUrl(activeRepo, activeBackend, {
            ref: activeRepo.default_branch || 'main',
          }) || '',
      },
    ]
  }, [branchesQuery.data, activeRepo, activeBackend])

  useEffect(() => {
    if (!activeRepo) return
    const defaultBranch =
      activeRepo.default_branch ||
      branchesQuery.data?.default_branch ||
      'main'
    if (!selectedRef || selectedRef === defaultBranch) return

    const remoteBranches = branchesQuery.data?.branches
    const isLikelyCommitSha = /^[0-9a-f]{7,40}$/i.test(selectedRef)
    const missingFromBranchList =
      Array.isArray(remoteBranches) &&
      remoteBranches.length > 0 &&
      !isLikelyCommitSha &&
      !remoteBranches.some((b) => b.name === selectedRef)

    if (missingFromBranchList || treeQuery.isError) {
      setFallbackNotice({
        missingRef: selectedRef,
        resolvedRef: defaultBranch,
      })
      setSelectedRef(defaultBranch)
      const params = new URLSearchParams(window.location.search)
      params.set('ref', defaultBranch)
      const qs = params.toString()
      window.history.replaceState(
        {
          codeNav: {
            repoId: activeRepo.id ?? selectedRepoId,
            ref: defaultBranch,
            path: currentPath,
            mode: viewMode,
          },
        },
        '',
        `${window.location.pathname}${qs ? `?${qs}` : ''}`
      )
    }
  }, [
    activeRepo,
    branchesQuery.data,
    selectedRef,
    treeQuery.isError,
    selectedRepoId,
    currentPath,
    viewMode,
  ])

  const pathSegments = useMemo(
    () => (currentPath ? currentPath.split('/').filter(Boolean) : []),
    [currentPath]
  )

  const filteredEntries = useMemo(() => {
    const entries = treeQuery.data?.entries || []
    const q = filterText.trim().toLowerCase()
    if (!q) return entries
    return entries.filter((e) => e.name.toLowerCase().includes(q))
  }, [treeQuery.data, filterText])

  const currentExternalUrl = useMemo<string | null>(() => {
    if (
      !activeRepo ||
      activeBackend?.provider === 'local' ||
      activeRepo.full_name.startsWith('local/')
    ) {
      return null
    }
    if (viewMode === 'blob' && blobQuery.data?.web_url) {
      const safeBlobUrl = safeExternalUrl(blobQuery.data.web_url)
      if (safeBlobUrl) return safeBlobUrl
    }
    if (viewMode === 'tree' && treeQuery.data?.web_url) {
      const safeTreeUrl = safeExternalUrl(treeQuery.data.web_url)
      if (safeTreeUrl) return safeTreeUrl
    }
    return safeExternalUrl(
      buildRepoWebUrl(activeRepo, activeBackend, {
        ref: selectedRef,
        path: currentPath,
        isDir: viewMode === 'tree',
      })
    )
  }, [
    activeRepo,
    activeBackend,
    viewMode,
    blobQuery.data,
    treeQuery.data,
    selectedRef,
    currentPath,
  ])

  const navigateToFolder = (folderPath: string) => {
    setCurrentPath(folderPath)
    setViewMode('tree')
    setFilterText('')
    pushCodeHistory({
      repoId: activeRepo?.id ?? selectedRepoId,
      ref: selectedRef,
      path: folderPath,
      mode: 'tree',
    })
  }

  const navigateToFile = (filePath: string) => {
    setCurrentPath(filePath)
    setViewMode('blob')
    pushCodeHistory({
      repoId: activeRepo?.id ?? selectedRepoId,
      ref: selectedRef,
      path: filePath,
      mode: 'blob',
    })
  }

  const navigateUp = () => {
    const idx = currentPath.lastIndexOf('/')
    const parentPath = idx >= 0 ? currentPath.slice(0, idx) : ''
    setCurrentPath(parentPath)
    setViewMode('tree')
    setFilterText('')
    pushCodeHistory({
      repoId: activeRepo?.id ?? selectedRepoId,
      ref: selectedRef,
      path: parentPath,
      mode: 'tree',
    })
  }

  const handleRefresh = () => {
    void branchesQuery.refetch()
    if (viewMode === 'tree') {
      void treeQuery.refetch()
    } else {
      void blobQuery.refetch()
    }
  }

  if (repos.length === 0 || !activeRepo) {
    return (
      <Card className="p-8 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs text-center flex flex-col items-center gap-3 dark:ring-1 dark:ring-white/[0.04]">
        <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center">
          <FolderGit2 size={20} />
        </div>
        <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
          No Linked Repositories
        </h3>
        <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
          Attach a GitHub, GitLab, or Bitbucket repository in Project Settings to
          browse branches, directory trees, and source files directly inside
          GitSeed.
        </p>
        {canManage && (
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={() => onOpenSettings('repositories')}
            className="mt-1 rounded-xl text-xs font-semibold flex items-center gap-1.5 shadow-md shadow-teal-600/20 cursor-pointer"
          >
            <Plus size={14} />
            <span>Connect Repository</span>
          </Button>
        )}
      </Card>
    )
  }

  const providerLabel = formatProviderName(activeBackend?.provider || 'github')

  return (
    <div className="flex flex-col gap-4 font-sans">
      {fallbackNotice && (
        <div className="px-4 py-2.5 rounded-xl border border-amber-500/30 bg-amber-500/10 text-amber-800 dark:text-amber-300 text-xs flex items-center justify-between gap-3">
          <span>
            Branch{' '}
            <code className="font-mono font-semibold px-1 py-0.5 rounded bg-amber-500/15">
              {fallbackNotice.missingRef}
            </code>{' '}
            was not found on the remote (it may not be pushed yet or was cleaned
            up after merge). Showing default branch{' '}
            <code className="font-mono font-semibold px-1 py-0.5 rounded bg-amber-500/15">
              {fallbackNotice.resolvedRef}
            </code>
            .
          </span>
          <button
            type="button"
            onClick={() => setFallbackNotice(null)}
            className="text-xs font-semibold underline hover:opacity-80 shrink-0 cursor-pointer"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Top Repository & Branch Control Strip */}
      <Card className="relative z-30 overflow-visible p-4 sm:p-5 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-3.5">
        <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
          {/* Left: Repo Switcher + Branch Selector */}
          <div className="flex flex-wrap items-center gap-2.5">
            {/* Repository Selector */}
            {repos.length > 1 ? (
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl bg-zinc-100/90 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80">
                <ProviderIcon
                  type="git"
                  provider={activeBackend?.provider || 'github'}
                  size={15}
                />
                <select
                  aria-label="Select repository"
                  value={activeRepo.id ?? ''}
                  onChange={(e) => {
                    const nextId = Number(e.target.value)
                    const found = repos.find((r) => r.id === nextId)
                    if (found) handleSelectRepo(found)
                  }}
                  className="bg-transparent font-sans text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100 focus:outline-none cursor-pointer"
                >
                  {repos.map((r) => (
                    <option
                      key={r.id ?? r.full_name}
                      value={r.id ?? ''}
                      className="bg-white dark:bg-zinc-900 font-sans text-zinc-900 dark:text-zinc-100"
                    >
                      {r.full_name}
                      {r.is_primary ? ' (Primary)' : ''}
                    </option>
                  ))}
                </select>
              </div>
            ) : (
              <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-xl bg-zinc-100/90 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80">
                <ProviderIcon
                  type="git"
                  provider={activeBackend?.provider || 'github'}
                  size={15}
                />
                <span className="text-xs sm:text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {activeRepo.full_name}
                </span>
                {activeRepo.is_primary && (
                  <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-[10px] font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                    <Star size={9} className="fill-current" />
                    Primary
                  </span>
                )}
              </div>
            )}

            {/* Branch Selector */}
            <div className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-white/60 dark:bg-zinc-900/60 border border-zinc-200 dark:border-zinc-700/80 hover:border-zinc-300 dark:hover:border-zinc-600 transition">
              <GitBranch
                size={13}
                className="text-zinc-400 dark:text-zinc-500 shrink-0"
              />
              <select
                aria-label="Select branch"
                value={selectedRef}
                onChange={(e) => handleSelectRef(e.target.value)}
                className="bg-transparent font-mono text-xs font-medium text-zinc-700 dark:text-zinc-300 focus:outline-none cursor-pointer max-w-[240px]"
              >
                {!branches.some((b) => b.name === selectedRef) && selectedRef && (
                  <option
                    value={selectedRef}
                    className="bg-white dark:bg-zinc-900 font-sans text-zinc-900 dark:text-zinc-100"
                  >
                    {/^[0-9a-f]{7,40}$/i.test(selectedRef)
                      ? `Commit ${selectedRef.slice(0, 7)}`
                      : selectedRef}
                  </option>
                )}
                {branches.map((b) => (
                  <option
                    key={b.name}
                    value={b.name}
                    className="bg-white dark:bg-zinc-900 font-sans text-zinc-900 dark:text-zinc-100"
                  >
                    {b.name}
                    {b.is_default ? ' (default)' : ''}
                  </option>
                ))}
              </select>
            </div>

            {branchesQuery.data && (
              <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
                {branches.length} {branches.length === 1 ? 'branch' : 'branches'}
              </span>
            )}
          </div>

          {/* Right: Filter + Refresh + Open in Remote */}
          <div className="flex items-center gap-2 flex-wrap">
            {viewMode === 'tree' && (
              <div className="relative">
                <Search
                  size={13}
                  className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
                />
                <input
                  type="text"
                  value={filterText}
                  onChange={(e) => setFilterText(e.target.value)}
                  placeholder="Filter files..."
                  className="pl-8 pr-3 py-1.5 rounded-xl text-xs bg-zinc-100/80 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 placeholder:text-zinc-400 focus:outline-none focus:border-teal-500/50 w-40 sm:w-48"
                />
              </div>
            )}

            <button
              type="button"
              onClick={handleRefresh}
              title="Refresh repository contents"
              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold text-zinc-600 dark:text-zinc-300 bg-zinc-100/80 dark:bg-zinc-800/80 hover:bg-zinc-200/70 dark:hover:bg-zinc-700/70 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer"
            >
              <RefreshCw
                size={13}
                className={
                  treeQuery.isFetching || blobQuery.isFetching ? 'animate-spin' : ''
                }
              />
              <span className="hidden sm:inline">Refresh</span>
            </button>

            <RepoClonePopover
              repo={activeRepo}
              backend={activeBackend}
              selectedRef={selectedRef}
            />

            {currentExternalUrl && (
              <a
                href={currentExternalUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-teal-700 dark:text-teal-300 bg-teal-500/10 hover:bg-teal-500/20 border border-teal-500/30 transition whitespace-nowrap"
              >
                <span>Open in {providerLabel}</span>
                <ExternalLink size={12} />
              </a>
            )}

            {canManage && (
              <button
                type="button"
                onClick={() => onOpenSettings('repositories')}
                title="Manage linked repositories"
                className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-xl text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200 bg-zinc-100/80 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer"
              >
                <Settings size={13} />
              </button>
            )}
          </div>
        </div>

        {/* Path Breadcrumbs Bar */}
        <div className="flex items-center gap-1.5 flex-wrap pt-2.5 border-t border-zinc-100 dark:border-zinc-800/80 text-xs">
          <button
            type="button"
            onClick={() => navigateToFolder('')}
            className={`px-2 py-0.5 rounded-md font-semibold transition cursor-pointer ${
              pathSegments.length === 0
                ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                : 'text-zinc-600 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800'
            }`}
          >
            {activeRepo.full_name.split('/').pop() || activeRepo.full_name}
          </button>

          {pathSegments.map((segment, idx) => {
            const cumulativePath = pathSegments.slice(0, idx + 1).join('/')
            const isLast = idx === pathSegments.length - 1
            return (
              <React.Fragment key={cumulativePath}>
                <ChevronRight size={13} className="text-zinc-400 shrink-0" />
                {isLast ? (
                  <span className="px-2 py-0.5 rounded-md font-semibold bg-teal-500/15 text-teal-700 dark:text-teal-300">
                    {segment}
                  </span>
                ) : (
                  <button
                    type="button"
                    onClick={() => navigateToFolder(cumulativePath)}
                    className="px-2 py-0.5 rounded-md font-medium text-zinc-600 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                  >
                    {segment}
                  </button>
                )}
              </React.Fragment>
            )
          })}
        </div>
      </Card>

      {/* Main Content Area: Tree Explorer or File Viewer */}
      {viewMode === 'tree' ? (
        <>
          <EntityTableCard>
            <EntityTableHeaderRow>
              <th className="py-3 px-4">Name</th>
              <th className="py-3 px-4 text-right w-28">Size</th>
              <th className="py-3 px-4 text-right w-28 hidden sm:table-cell">
                Object
              </th>
              <th className="py-3 px-4 text-right w-20">Remote</th>
            </EntityTableHeaderRow>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-700/80 text-sm">
              {treeQuery.isLoading ? (
                <tr>
                  <td colSpan={4} className="py-16 px-4 text-center">
                    <div className="flex flex-col items-center justify-center gap-2.5 text-zinc-500 dark:text-zinc-400">
                      <Loader2
                        size={22}
                        className="animate-spin text-teal-500"
                      />
                      <span className="text-xs font-medium">
                        Loading {activeRepo.full_name} ({selectedRef})...
                      </span>
                    </div>
                  </td>
                </tr>
              ) : treeQuery.isError ? (
                <tr>
                  <td colSpan={4} className="p-8 text-center">
                    <div className="flex flex-col items-center justify-center gap-3">
                      <div className="w-10 h-10 rounded-xl bg-rose-500/10 text-rose-500 flex items-center justify-center">
                        <AlertCircle size={20} />
                      </div>
                      <div className="flex flex-col gap-1 max-w-md">
                        <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
                          Unable to Load Repository Directory
                        </h4>
                        <p className="text-xs text-zinc-500 dark:text-zinc-400">
                          {treeQuery.error instanceof Error
                            ? treeQuery.error.message
                            : 'Could not fetch directory contents from the remote Git provider.'}
                        </p>
                      </div>
                      <div className="flex items-center gap-2 mt-1">
                        {currentPath && (
                          <button
                            type="button"
                            onClick={() => navigateToFolder('')}
                            className="px-3 py-1.5 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700 cursor-pointer"
                          >
                            Back to Root
                          </button>
                        )}
                        {currentExternalUrl && (
                          <a
                            href={currentExternalUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-teal-700 dark:text-teal-300 bg-teal-500/10 border border-teal-500/30"
                          >
                            <span>View on {providerLabel}</span>
                            <ExternalLink size={12} />
                          </a>
                        )}
                      </div>
                    </div>
                  </td>
                </tr>
              ) : (
                <>
                  {/* Parent Directory Row */}
                  {currentPath && (
                    <tr className="hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 transition">
                      <td colSpan={4} className="py-2.5 px-4">
                        <button
                          type="button"
                          onClick={navigateUp}
                          className="w-full flex items-center gap-2.5 text-left text-xs font-medium text-zinc-600 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
                        >
                          <ArrowLeft size={14} className="text-zinc-400" />
                          <span className="font-semibold">..</span>
                          <span className="text-zinc-400">
                            Parent directory
                          </span>
                        </button>
                      </td>
                    </tr>
                  )}

                  {filteredEntries.length === 0 ? (
                    <tr>
                      <td
                        colSpan={4}
                        className="py-12 px-4 text-center text-xs text-zinc-500 dark:text-zinc-400"
                      >
                        {filterText
                          ? `No files matching "${filterText}" in this directory.`
                          : 'This directory is empty.'}
                      </td>
                    </tr>
                  ) : (
                    filteredEntries.map((entry) => {
                      const isDir = entry.type === 'dir'
                      const safeEntryWebUrl = safeExternalUrl(entry.web_url)
                      return (
                        <tr
                          key={entry.path}
                          className="hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 transition group"
                        >
                          <td className="py-2.5 px-4">
                            <button
                              type="button"
                              onClick={() =>
                                isDir
                                  ? navigateToFolder(entry.path)
                                  : navigateToFile(entry.path)
                              }
                              className="flex items-center gap-2.5 min-w-0 w-full text-left cursor-pointer"
                            >
                              {isDir ? (
                                <Folder
                                  size={15}
                                  className="text-teal-600 dark:text-teal-400 fill-teal-500/20 shrink-0"
                                />
                              ) : (
                                <FileCode
                                  size={15}
                                  className="text-zinc-400 dark:text-zinc-500 shrink-0"
                                />
                              )}
                              <span
                                className={`text-sm truncate transition-colors ${
                                  isDir
                                    ? 'font-semibold text-zinc-900 dark:text-zinc-100 group-hover:text-teal-600 dark:group-hover:text-teal-400'
                                    : 'font-medium text-zinc-700 dark:text-zinc-300 group-hover:text-teal-600 dark:group-hover:text-teal-400'
                                }`}
                              >
                                {entry.name}
                              </span>
                            </button>
                          </td>

                          <td className="py-2.5 px-4 text-right font-mono text-xs text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                            {!isDir && entry.size !== null
                              ? formatCodeBytes(entry.size)
                              : '-'}
                          </td>

                          <td className="py-2.5 px-4 text-right font-mono text-xs text-zinc-400 dark:text-zinc-500 hidden sm:table-cell whitespace-nowrap">
                            {entry.sha ? entry.sha.slice(0, 7) : '-'}
                          </td>

                          <td className="py-2.5 px-4 text-right">
                            {safeEntryWebUrl ? (
                              <a
                                href={safeEntryWebUrl}
                                target="_blank"
                                rel="noopener noreferrer"
                                title={`Open ${entry.name} on ${providerLabel}`}
                                className="inline-flex items-center justify-center p-1.5 rounded-lg text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition"
                              >
                                <ExternalLink size={13} />
                              </a>
                            ) : (
                              <span className="text-xs text-zinc-400">-</span>
                            )}
                          </td>
                        </tr>
                      )
                    })
                  )}
                </>
              )}
            </tbody>
          </EntityTableCard>

          {/* Automatic README Preview */}
          {treeQuery.data?.readme && (
            <Card className="p-5 sm:p-6 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs">
              <div className="flex items-center justify-between pb-3.5 mb-4 border-b border-zinc-200/80 dark:border-zinc-800">
                <div className="flex items-center gap-2 text-sm font-heading font-bold text-zinc-900 dark:text-zinc-100">
                  <BookOpen
                    size={15}
                    className="text-teal-600 dark:text-teal-400"
                  />
                  <span>{treeQuery.data.readme.name}</span>
                </div>
                <button
                  type="button"
                  onClick={() => navigateToFile(treeQuery.data!.readme!.path)}
                  className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  View raw file
                </button>
              </div>
              <div className="prose prose-sm dark:prose-invert max-w-none">
                <MarkdownContent content={treeQuery.data.readme.content} />
              </div>
            </Card>
          )}
        </>
      ) : (
        <CodeBlobViewer
          currentPath={currentPath}
          blobData={blobQuery.data}
          isLoading={blobQuery.isLoading}
          isError={blobQuery.isError}
          error={blobQuery.error}
          currentExternalUrl={currentExternalUrl}
          providerLabel={providerLabel}
          theme={theme}
          onNavigateUp={navigateUp}
        />
      )}
    </div>
  )
}
