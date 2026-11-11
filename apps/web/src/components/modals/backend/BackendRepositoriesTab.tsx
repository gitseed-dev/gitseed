import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  FolderGit2,
  GitBranch,
  Globe,
  Lock,
  RefreshCw,
  Search,
} from 'lucide-react'
import { Button } from '@heroui/react'
import type { GitBackendRepoEntry } from '../../../utils/backends'

export interface BackendRepositoriesTabProps {
  isEditing: boolean
  repositories: GitBackendRepoEntry[]
  onChangeRepositories: (repos: GitBackendRepoEntry[]) => void
  isSyncing: boolean
  onSyncFromProvider?: () => Promise<void> | void
}

export const BackendRepositoriesTab: React.FC<BackendRepositoriesTabProps> = ({
  isEditing,
  repositories,
  onChangeRepositories,
  isSyncing,
  onSyncFromProvider,
}) => {
  const [search, setSearch] = useState('')
  const [justSynced, setJustSynced] = useState(false)
  const autoSyncedRef = useRef(false)

  const handleSyncClick = async () => {
    if (!onSyncFromProvider) return
    await onSyncFromProvider()
    setJustSynced(true)
    setTimeout(() => setJustSynced(false), 2000)
  }

  useEffect(() => {
    if (
      isEditing &&
      repositories.length === 0 &&
      !isSyncing &&
      onSyncFromProvider &&
      !autoSyncedRef.current
    ) {
      autoSyncedRef.current = true
      onSyncFromProvider()
    }
  }, [isEditing, repositories.length, isSyncing, onSyncFromProvider])

  const enabledCount = useMemo(
    () => repositories.filter((r) => r.enabled).length,
    [repositories]
  )

  const filteredRepos = useMemo(() => {
    const q = search.trim().toLowerCase()
    const list = q
      ? repositories.filter(
          (r) =>
            r.full_name.toLowerCase().includes(q) ||
            r.default_branch.toLowerCase().includes(q)
        )
      : [...repositories]
    return list.sort((a, b) =>
      a.full_name.localeCompare(b.full_name, undefined, { sensitivity: 'base' })
    )
  }, [repositories, search])

  const handleToggleRepo = (fullName: string) => {
    onChangeRepositories(
      repositories.map((r) =>
        r.full_name.toLowerCase() === fullName.toLowerCase()
          ? { ...r, enabled: !r.enabled }
          : r
      )
    )
  }

  const handleSetAllEnabled = (enabled: boolean) => {
    onChangeRepositories(repositories.map((r) => ({ ...r, enabled })))
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-700 dark:text-zinc-300">
            Accessible Repositories ({enabledCount}/{repositories.length} enabled)
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
            Enabled repositories appear in the repository selector when creating or configuring projects.
          </p>
        </div>

        {isEditing && onSyncFromProvider && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={handleSyncClick}
            isDisabled={isSyncing}
            className="text-xs font-semibold rounded-xl flex items-center gap-1.5 shrink-0 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
          >
            {justSynced && !isSyncing ? (
              <>
                <Check size={13} className="text-emerald-500" />
                <span>Synced</span>
              </>
            ) : (
              <>
                <RefreshCw size={13} className={isSyncing ? 'animate-spin' : ''} />
                <span>{isSyncing ? 'Syncing...' : 'Sync from Provider'}</span>
              </>
            )}
          </Button>
        )}
      </div>

      {repositories.length > 0 && (
        <div className="flex items-center justify-between gap-2">
          <div className="relative flex-1">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter repositories..."
              className="w-full pl-8 pr-3 py-1.5 rounded-xl text-xs bg-zinc-100/80 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/30"
            />
          </div>
          <div className="flex items-center gap-2 text-xs shrink-0">
            <button
              type="button"
              onClick={() => handleSetAllEnabled(true)}
              className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
            >
              Enable all
            </button>
            <span className="text-zinc-300 dark:text-zinc-700">·</span>
            <button
              type="button"
              onClick={() => handleSetAllEnabled(false)}
              className="font-semibold text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
            >
              Disable all
            </button>
          </div>
        </div>
      )}

      {repositories.length === 0 ? (
        <div className="py-10 px-4 rounded-xl border border-dashed border-zinc-200 dark:border-zinc-700/80 flex flex-col items-center justify-center text-center gap-2">
          <div className="w-9 h-9 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center">
            <FolderGit2 size={18} />
          </div>
          <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
            {isSyncing ? 'Discovering repositories...' : 'No repositories discovered yet'}
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm">
            {isEditing
              ? 'Click "Sync from Provider" to discover accessible repositories from your Git host.'
              : 'Repositories will be discovered automatically once you register this Git backend.'}
          </p>
        </div>
      ) : filteredRepos.length === 0 ? (
        <div className="py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
          No repositories match &ldquo;{search}&rdquo;
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-zinc-200/70 dark:divide-zinc-700/60 rounded-xl border border-zinc-200 dark:border-zinc-700/80 overflow-hidden">
          {filteredRepos.map((repo) => (
            <div
              key={repo.full_name}
              className={`px-3.5 py-2.5 flex items-center justify-between gap-3 transition-colors ${
                repo.enabled
                  ? 'bg-white dark:bg-zinc-900/60'
                  : 'bg-zinc-50/70 dark:bg-zinc-800/30 opacity-65'
              }`}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <FolderGit2
                  size={15}
                  className={
                    repo.enabled
                      ? 'text-teal-600 dark:text-teal-400 shrink-0'
                      : 'text-zinc-400 shrink-0'
                  }
                />
                <div className="min-w-0">
                  <div className="text-sm font-mono font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                    {repo.full_name}
                  </div>
                  <div className="flex items-center gap-2 text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                    <span className="inline-flex items-center gap-1 font-mono">
                      <GitBranch size={11} />
                      <span>{repo.default_branch}</span>
                    </span>
                    <span>·</span>
                    <span className="inline-flex items-center gap-1">
                      {repo.private ? <Lock size={11} /> : <Globe size={11} />}
                      <span>{repo.private ? 'Private' : 'Public'}</span>
                    </span>
                  </div>
                </div>
              </div>

              <button
                type="button"
                role="switch"
                aria-checked={repo.enabled}
                onClick={() => handleToggleRepo(repo.full_name)}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  repo.enabled
                    ? 'bg-teal-600 dark:bg-teal-500'
                    : 'bg-zinc-300 dark:bg-zinc-700'
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-xs transition duration-200 ease-in-out ${
                    repo.enabled ? 'translate-x-4' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
