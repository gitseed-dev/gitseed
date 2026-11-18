import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Globe,
  Lock,
  Plus,
  RefreshCw,
  Star,
  Trash2,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { ProviderIcon } from '../../common/ProviderIcon'
import { useData } from '../../../context/DataContext'
import { useTranslation } from '../../../context/I18nContext'
import {
  getBackendRepositories,
  getEnabledBackendRepositories,
} from '../../../utils/backends'
import { buildNewRepoWebUrl, safeExternalUrl } from '../../../utils/gitUrls'
import type { Project, ProjectRepository } from '../../../types'

export interface ProjectRepositoriesCardProps {
  project: Project
  repos: ProjectRepository[]
  canManage: boolean
}

export const ProjectRepositoriesCard: React.FC<
  ProjectRepositoriesCardProps
> = ({ project, repos, canManage }) => {
  const queryClient = useQueryClient()
  const { backends } = useData()
  const { t } = useTranslation()

  const gitBackends = useMemo(
    () =>
      backends.filter(
        (b) => b.type === 'git' && b.enabled && b.provider !== 'local'
      ),
    [backends]
  )
  const backendsById = useMemo(
    () => new Map(backends.map((b) => [b.id, b])),
    [backends]
  )

  const [gitBackendId, setGitBackendId] = useState<string>(
    gitBackends[0]?.id || ''
  )
  const [repoMode, setRepoMode] = useState<'existing' | 'create' | 'local'>(
    () => (gitBackends.length > 0 ? 'existing' : 'local')
  )
  const [fullName, setFullName] = useState('')
  const [localWorkspacePath, setLocalWorkspacePath] = useState('')
  const [newRepoName, setNewRepoName] = useState(project.slug || '')
  const [newRepoPrivate, setNewRepoPrivate] = useState(true)
  const [defaultBranch, setDefaultBranch] = useState('main')
  const [isPrimary, setIsPrimary] = useState(false)
  const [isAdding, setIsAdding] = useState(false)
  const [showAttachForm, setShowAttachForm] = useState(false)
  const [isSyncingBackendRepos, setIsSyncingBackendRepos] = useState(false)
  const [useCustomRepoInput, setUseCustomRepoInput] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const autoDiscoveredIdsRef = useRef<Set<string>>(new Set())

  const selectedBackendId = gitBackendId || gitBackends[0]?.id || ''
  const selectedGitBackend = backendsById.get(selectedBackendId)

  const availableBackendRepos = useMemo(() => {
    const enabled = getEnabledBackendRepositories(selectedGitBackend)
    return enabled.filter(
      (r) =>
        !repos.some(
          (attached) =>
            attached.backend_id === selectedBackendId &&
            attached.full_name.toLowerCase() === r.full_name.toLowerCase()
        )
    )
  }, [selectedGitBackend, repos, selectedBackendId])

  useEffect(() => {
    if (
      !canManage ||
      !selectedGitBackend ||
      getBackendRepositories(selectedGitBackend).length > 0 ||
      autoDiscoveredIdsRef.current.has(selectedGitBackend.id)
    ) {
      return
    }
    autoDiscoveredIdsRef.current.add(selectedGitBackend.id)
    setIsSyncingBackendRepos(true)
    api.backends
      .discoverRepos(selectedGitBackend.id)
      .then(() => {
        queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      })
      .catch(() => {
        // Ignore background auto-discovery errors
      })
      .finally(() => {
        setIsSyncingBackendRepos(false)
      })
  }, [canManage, selectedGitBackend, queryClient])

  const handleSyncBackendRepos = async () => {
    if (!selectedGitBackend) return
    setIsSyncingBackendRepos(true)
    setErrorMessage(null)
    try {
      await api.backends.discoverRepos(selectedGitBackend.id)
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      setUseCustomRepoInput(false)
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Failed to sync repositories from Git provider.'
      )
    } finally {
      setIsSyncingBackendRepos(false)
    }
  }

  const [editingBranchRepoId, setEditingBranchRepoId] = useState<number | null>(
    null
  )
  const [branchDraft, setBranchDraft] = useState('')

  const invalidateRepos = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.projectRepos(project.id),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
      queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
    ])
  }

  const handleAddRepo = async (e: React.FormEvent) => {
    e.preventDefault()
    if (repoMode === 'local') {
      setIsAdding(true)
      setErrorMessage(null)
      try {
        await api.localWorkspaces.init(project.id, {
          workspace_path: localWorkspacePath.trim() || null,
          default_branch: defaultBranch.trim() || 'main',
        })
        setLocalWorkspacePath('')
        setDefaultBranch('main')
        setShowAttachForm(false)
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
          invalidateRepos(),
        ])
      } catch (err) {
        setErrorMessage(
          err instanceof Error
            ? err.message
            : 'Failed to initialize On-Device Local Git repository.'
        )
      } finally {
        setIsAdding(false)
      }
      return
    }

    const targetBackendId = gitBackendId || gitBackends[0]?.id
    if (!targetBackendId) {
      setErrorMessage('Please select a Git provider backend.')
      return
    }

    if (repoMode === 'existing') {
      const trimmedName = fullName.trim()
      if (!trimmedName || !trimmedName.includes('/')) {
        setErrorMessage(
          'Repository must be in owner/repo format (e.g. acme/service-api).'
        )
        return
      }
    } else if (!newRepoName.trim()) {
      setErrorMessage('Please enter a name for the new repository.')
      return
    }

    setIsAdding(true)
    setErrorMessage(null)
    try {
      let targetFullName = fullName.trim()
      let targetDefaultBranch = defaultBranch.trim() || 'main'

      if (repoMode === 'create') {
        const created = await api.backends.createRepo(targetBackendId, {
          name: newRepoName.trim(),
          description: (project.description || '').trim(),
          private: newRepoPrivate,
          auto_init: true,
        })
        targetFullName = created.full_name
        targetDefaultBranch = created.default_branch || 'main'
        await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      }

      await api.projects.addRepo(project.id, {
        backend_id: targetBackendId,
        full_name: targetFullName,
        default_branch: targetDefaultBranch,
        is_primary: repos.length === 0 ? true : isPrimary,
      })
      setFullName('')
      setDefaultBranch('main')
      setIsPrimary(false)
      setShowAttachForm(false)
      await invalidateRepos()
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to attach repository.'
      )
    } finally {
      setIsAdding(false)
    }
  }

  const handlePromotePrimary = async (repoId: number) => {
    setErrorMessage(null)
    try {
      await api.projects.updateRepo(project.id, repoId, { is_primary: true })
      await invalidateRepos()
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Failed to set primary repository.'
      )
    }
  }

  const handleSaveBranch = async (repoId: number) => {
    const trimmed = branchDraft.trim()
    if (!trimmed) return
    setErrorMessage(null)
    try {
      await api.projects.updateRepo(project.id, repoId, {
        default_branch: trimmed,
      })
      setEditingBranchRepoId(null)
      await invalidateRepos()
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to update default branch.'
      )
    }
  }

  const handleRemoveRepo = async (repoId: number) => {
    setErrorMessage(null)
    try {
      await api.projects.removeRepo(project.id, repoId)
      await invalidateRepos()
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to detach repository.'
      )
    }
  }

  return (
    <div
      id="project-settings-repositories"
      className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 rounded-2xl shadow-xs overflow-hidden"
    >
      <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <FolderGit2 size={16} />
          </div>
          <div>
            <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
              {t('projects.targetRepos')}
            </h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Attach Git repositories for containerized task runners to clone, branch, and open pull requests against
            </p>
          </div>
        </div>
        <div className="flex items-center gap-3 shrink-0">
          <span className="text-xs font-mono font-semibold text-zinc-500 dark:text-zinc-400">
            {repos.length} {repos.length === 1 ? 'repository' : 'repositories'}
          </span>
          {canManage &&
            repos.length > 0 &&
            !showAttachForm && (
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => setShowAttachForm(true)}
                className="rounded-xl text-xs font-semibold flex items-center gap-1.5 border border-zinc-200 dark:border-zinc-700/80 cursor-pointer"
              >
                <Plus size={13} />
                <span>Attach Repository</span>
              </Button>
            )}
        </div>
      </div>

      <div className="p-6 flex flex-col gap-5">
        {errorMessage && (
          <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
            <AlertCircle size={15} className="shrink-0" />
            <span>{errorMessage}</span>
          </div>
        )}

        {/* Attached Repositories List */}
        {repos.length === 0 ? (
          <div className="p-5 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/30 border border-zinc-200 dark:border-zinc-700/80 text-center">
            <p className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              {t('projects.connectFirstRepoTitle')}
            </p>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
              {t('projects.connectFirstRepoDesc')}
            </p>
          </div>
        ) : (
          <div className="border border-zinc-200 dark:border-zinc-700/80 rounded-xl divide-y divide-zinc-200 dark:divide-zinc-700/80 overflow-hidden">
            {repos.map((repo) => {
              const backend = backendsById.get(repo.backend_id)
              const isLocalRepo =
                backend?.provider === 'local' ||
                repo.full_name.startsWith('local/')
              const isEditingBranch = editingBranchRepoId === repo.id

              return (
                <div
                  key={repo.id ?? repo.full_name}
                  className="px-4 py-3.5 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-white dark:bg-zinc-900/40 hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 transition"
                >
                  <div className="flex items-center gap-3 min-w-0">
                    <span className="text-zinc-600 dark:text-zinc-300 shrink-0">
                      <ProviderIcon
                        type="git"
                        provider={backend?.provider || (isLocalRepo ? 'local' : 'github')}
                        size={16}
                      />
                    </span>
                    <div className="flex flex-col min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-mono font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                          {repo.full_name}
                        </span>
                        {isLocalRepo && (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 border border-emerald-500/25">
                            On-Device Local
                          </span>
                        )}
                        {repo.is_primary ? (
                          <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                            <Star size={10} className="fill-current" />
                            Primary
                          </span>
                        ) : (
                          canManage &&
                          repo.id !== null && (
                            <button
                              type="button"
                              onClick={() => handlePromotePrimary(repo.id!)}
                              className="text-xs font-semibold text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
                            >
                              Make primary
                            </button>
                          )
                        )}
                      </div>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                        {backend
                          ? `${backend.name} (${backend.provider})`
                          : repo.backend_id}
                      </span>
                    </div>
                  </div>

                  <div className="flex items-center gap-2.5 shrink-0">
                    {isEditingBranch && repo.id !== null ? (
                      <div className="flex items-center gap-1.5">
                        <input
                          type="text"
                          value={branchDraft}
                          onChange={(e) => setBranchDraft(e.target.value)}
                          className="w-28 px-2.5 py-1 text-xs font-mono rounded-lg bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                        />
                        <button
                          type="button"
                          onClick={() => handleSaveBranch(repo.id!)}
                          className="p-1.5 rounded-lg bg-teal-500/15 text-teal-600 dark:text-teal-400 hover:bg-teal-500/25 transition cursor-pointer"
                          title="Save default branch"
                        >
                          <Check size={13} />
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        disabled={!canManage || repo.id === null}
                        onClick={() => {
                          setEditingBranchRepoId(repo.id)
                          setBranchDraft(repo.default_branch)
                        }}
                        title={
                          canManage
                            ? 'Click to change default branch'
                            : 'Default branch'
                        }
                        className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono text-zinc-700 dark:text-zinc-300 hover:border-teal-500/40 transition cursor-pointer disabled:cursor-default"
                      >
                        <GitBranch size={12} className="text-zinc-400" />
                        <span>{repo.default_branch}</span>
                      </button>
                    )}

                    {canManage && repo.id !== null && (
                      <button
                        type="button"
                        onClick={() => handleRemoveRepo(repo.id!)}
                        title="Detach repository"
                        className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-500/10 transition cursor-pointer"
                      >
                        <Trash2 size={14} />
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}

        {/* Attach New Repository Form */}
        {canManage &&
          (repos.length === 0 || showAttachForm) && (
            <form
              onSubmit={handleAddRepo}
              className="p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/30 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-3"
            >
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2.5 flex-wrap">
                  <span className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                    Attach Repository
                  </span>
                  <div className="inline-flex items-center rounded-lg p-0.5 bg-zinc-200/70 dark:bg-zinc-800 border border-zinc-300/70 dark:border-zinc-700 flex-wrap">
                    {gitBackends.length > 0 && (
                      <>
                        <button
                          type="button"
                          onClick={() => setRepoMode('existing')}
                          className={`px-2 py-0.5 rounded-md text-xs font-semibold transition cursor-pointer ${
                            repoMode === 'existing'
                              ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                              : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
                          }`}
                        >
                          Link existing
                        </button>
                        <button
                          type="button"
                          onClick={() => setRepoMode('create')}
                          className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold transition cursor-pointer ${
                            repoMode === 'create'
                              ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                              : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
                          }`}
                        >
                          <Plus size={11} />
                          Create new repo
                        </button>
                      </>
                    )}
                    <button
                      type="button"
                      onClick={() => setRepoMode('local')}
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-semibold transition cursor-pointer ${
                        repoMode === 'local'
                          ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                          : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
                      }`}
                    >
                      <FolderGit2 size={11} />
                      On-Device Local Git
                    </button>
                  </div>
                </div>
                <div className="flex items-center gap-3">
                  {selectedGitBackend &&
                    selectedGitBackend.provider !== 'local' &&
                    (repoMode === 'existing' || repoMode === 'create') && (() => {
                      const safeNewRepoUrl = safeExternalUrl(
                        buildNewRepoWebUrl(
                          selectedGitBackend.provider,
                          selectedGitBackend.endpoint
                        )
                      )
                      return (
                        <>
                          {safeNewRepoUrl && (
                            <a
                              href={safeNewRepoUrl}
                              target="_blank"
                              rel="noopener noreferrer"
                              className="text-xs font-semibold text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 flex items-center gap-1 transition"
                              title="Open provider in a new tab to create a repository manually"
                            >
                              <span>
                                New on{' '}
                                {selectedGitBackend.provider === 'gitlab'
                                  ? 'GitLab'
                                  : selectedGitBackend.provider === 'bitbucket'
                                    ? 'Bitbucket'
                                    : 'GitHub'}
                              </span>
                              <ExternalLink size={11} />
                            </a>
                          )}
                          <button
                            type="button"
                            onClick={handleSyncBackendRepos}
                            disabled={isSyncingBackendRepos}
                            className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline flex items-center gap-1 cursor-pointer disabled:opacity-50"
                          >
                            <RefreshCw
                              size={11}
                              className={isSyncingBackendRepos ? 'animate-spin' : ''}
                            />
                            <span>
                              {isSyncingBackendRepos
                                ? 'Syncing...'
                                : 'Sync repositories'}
                            </span>
                          </button>
                        </>
                      )
                    })()}
                  {repos.length > 0 && (
                    <button
                      type="button"
                      onClick={() => {
                        setShowAttachForm(false)
                        setErrorMessage(null)
                      }}
                      className="text-xs font-semibold text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                    >
                      Cancel
                    </button>
                  )}
                </div>
              </div>

            {repoMode === 'local' ? (
              <div className="grid grid-cols-1 sm:grid-cols-12 gap-2.5 items-end">
                <div className="sm:col-span-7">
                  <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                    Local Workspace Directory <span className="text-zinc-400">(optional)</span>
                  </label>
                  <input
                    type="text"
                    value={localWorkspacePath}
                    onChange={(e) => setLocalWorkspacePath(e.target.value)}
                    placeholder="/Users/you/code/my-app (or leave blank for managed repo)"
                    className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                </div>
                <div className="sm:col-span-3">
                  <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                    Default Branch
                  </label>
                  <input
                    type="text"
                    value={defaultBranch}
                    onChange={(e) => setDefaultBranch(e.target.value)}
                    placeholder="main"
                    className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                </div>
                <div className="sm:col-span-2">
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    isDisabled={isAdding}
                    className="w-full rounded-xl font-semibold flex items-center justify-center gap-1.5 py-2 cursor-pointer"
                  >
                    <FolderGit2 size={14} />
                    <span>{isAdding ? 'Linking...' : 'Init Local Git'}</span>
                  </Button>
                </div>
              </div>
            ) : repoMode === 'create' ? (
              <div className="grid grid-cols-1 sm:grid-cols-12 gap-2.5 items-end">
                <div className="sm:col-span-4">
                  <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                    Git Provider
                  </label>
                  <select
                    value={selectedBackendId}
                    onChange={(e) => setGitBackendId(e.target.value)}
                    className="w-full px-3 py-2 text-xs font-medium rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500 cursor-pointer"
                  >
                    {gitBackends.map((b) => (
                      <option key={b.id} value={b.id}>
                        {b.name} ({b.provider})
                      </option>
                    ))}
                  </select>
                </div>

                <div className="sm:col-span-4">
                  <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                    New Repository Name (or org/repo)
                  </label>
                  <input
                    type="text"
                    value={newRepoName}
                    onChange={(e) => setNewRepoName(e.target.value)}
                    placeholder={project.slug || 'my-new-repo'}
                    className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                </div>

                <div className="sm:col-span-2">
                  <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                    Visibility
                  </label>
                  <div className="grid grid-cols-2 gap-1 p-1 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80">
                    <button
                      type="button"
                      onClick={() => setNewRepoPrivate(true)}
                      className={`py-1 rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
                        newRepoPrivate
                          ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                          : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                      }`}
                    >
                      <Lock size={10} />
                      Private
                    </button>
                    <button
                      type="button"
                      onClick={() => setNewRepoPrivate(false)}
                      className={`py-1 rounded-lg text-xs font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
                        !newRepoPrivate
                          ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                          : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                      }`}
                    >
                      <Globe size={10} />
                      Public
                    </button>
                  </div>
                </div>

                <div className="sm:col-span-2">
                  <Button
                    type="submit"
                    variant="primary"
                    size="sm"
                    isDisabled={isAdding || !newRepoName.trim()}
                    className="w-full rounded-xl font-semibold flex items-center justify-center gap-1.5 py-2 cursor-pointer"
                  >
                    <Plus size={14} />
                    <span>{isAdding ? 'Creating...' : 'Create & Attach'}</span>
                  </Button>
                </div>
              </div>
            ) : (
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-2.5 items-end">
              <div className="sm:col-span-4">
                <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Git Provider
                </label>
                <select
                  value={selectedBackendId}
                  onChange={(e) => {
                    setGitBackendId(e.target.value)
                    setFullName('')
                    setDefaultBranch('main')
                    setUseCustomRepoInput(false)
                  }}
                  className="w-full px-3 py-2 text-xs font-medium rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500 cursor-pointer"
                >
                  {gitBackends.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name} ({b.provider})
                    </option>
                  ))}
                </select>
              </div>

              <div className="sm:col-span-4">
                <div className="flex items-center justify-between mb-1">
                  <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400">
                    Repository
                  </label>
                  {availableBackendRepos.length > 0 && useCustomRepoInput && (
                    <button
                      type="button"
                      onClick={() => {
                        setUseCustomRepoInput(false)
                        setFullName('')
                      }}
                      className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                    >
                      Select from list
                    </button>
                  )}
                </div>
                {availableBackendRepos.length > 0 && !useCustomRepoInput ? (
                  <select
                    value={fullName}
                    onChange={(e) => {
                      const val = e.target.value
                      if (val === '__custom__') {
                        setUseCustomRepoInput(true)
                        setFullName('')
                        return
                      }
                      setFullName(val)
                      const matched = availableBackendRepos.find(
                        (r) => r.full_name === val
                      )
                      if (matched) {
                        setDefaultBranch(matched.default_branch || 'main')
                      }
                    }}
                    className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 cursor-pointer"
                  >
                    <option value="">Select a repository...</option>
                    {availableBackendRepos.map((r) => (
                      <option key={r.full_name} value={r.full_name}>
                        {r.full_name}
                      </option>
                    ))}
                    <option value="__custom__">Enter custom owner/repo...</option>
                  </select>
                ) : (
                  <input
                    type="text"
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    placeholder="acme/core-api"
                    className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                )}
              </div>

              <div className="sm:col-span-2">
                <label className="block text-xs font-medium text-zinc-500 dark:text-zinc-400 mb-1">
                  Default Branch
                </label>
                <input
                  type="text"
                  value={defaultBranch}
                  onChange={(e) => setDefaultBranch(e.target.value)}
                  placeholder="main"
                  className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                />
              </div>

              <div className="sm:col-span-2">
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  isDisabled={isAdding || !fullName.trim()}
                  className="w-full rounded-xl font-semibold flex items-center justify-center gap-1.5 py-2 cursor-pointer"
                >
                  <Plus size={14} />
                  <span>{isAdding ? 'Adding...' : 'Attach'}</span>
                </Button>
              </div>
            </div>
            )}
          </form>
        )}
      </div>
    </div>
  )
}
