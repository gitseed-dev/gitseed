import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@heroui/react'
import {
  AlertCircle,
  Ban,
  Bot,
  ExternalLink,
  FolderGit2,
  GitBranch,
  Globe,
  Lock,
  Plus,
  RefreshCw,
  RotateCcw,
} from 'lucide-react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { Modal } from '../common/Modal'
import { ProviderIcon } from '../common/ProviderIcon'
import { SegmentedControl } from '../common/SegmentedControl'
import { StageBadge } from '../common/StageBadge'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import {
  getBackendRepositories,
  getEnabledBackendRepositories,
} from '../../utils/backends'
import { buildNewRepoWebUrl } from '../../utils/gitUrls'
import { ORDERED_STAGES, STAGE_METADATA } from '../../utils/policies'
import { deriveProjectSlug } from '../../utils/projects'
import type { Project, ProjectVisibility, SeedStage } from '../../types'

export type ProjectRepoSetupMode = 'existing' | 'create' | 'local' | 'none'

export interface ProjectModalProps {
  isOpen: boolean
  onClose: () => void
  project?: Project | null
  initialRepoMode?: ProjectRepoSetupMode
  onSaved?: (project: Project) => void
}

const ProjectModalForm: React.FC<{
  onClose: () => void
  project?: Project | null
  initialRepoMode?: ProjectRepoSetupMode
  onSaved?: (project: Project) => void
}> = ({ onClose, project = null, initialRepoMode, onSaved }) => {
  const queryClient = useQueryClient()
  const { backends, agents, policies, orgSettings } = useData()
  const { t } = useTranslation()

  const isEditing = Boolean(project)
  const gitBackends = useMemo(
    () =>
      backends.filter(
        (b) => b.type === 'git' && b.enabled && b.provider !== 'local'
      ),
    [backends]
  )
  const stageAgents = useMemo(
    () => agents.filter((a) => !a.archived_at && a.kind === 'stage'),
    [agents]
  )

  const orgDefaultByStage = useMemo(() => {
    const validAgentIds = new Set(stageAgents.map((a) => a.id))
    const map: Record<SeedStage, string> = {
      ideate: '',
      plan: '',
      execute: '',
      review: '',
      merge: '',
    }
    for (const st of ORDERED_STAGES) {
      const pol = policies.find((p) => p.stage === st)
      const meta = STAGE_METADATA[st]
      if (pol && !pol.enabled && !meta.isRequired) {
        map[st] = '__skip__'
      } else if (pol?.agent_id && validAgentIds.has(pol.agent_id)) {
        map[st] = pol.agent_id
      } else {
        map[st] = ''
      }
    }
    return map
  }, [policies, stageAgents])

  const [stageSelections, setStageSelections] = useState<Record<SeedStage, string>>(
    () => ({ ...orgDefaultByStage })
  )
  const [stageSelectionsTouched, setStageSelectionsTouched] = useState(false)

  useEffect(() => {
    if (!stageSelectionsTouched) {
      setStageSelections({ ...orgDefaultByStage })
    }
  }, [orgDefaultByStage, stageSelectionsTouched])

  const customizedStages = useMemo(
    () =>
      ORDERED_STAGES.filter(
        (st) => stageSelections[st] !== orgDefaultByStage[st]
      ),
    [stageSelections, orgDefaultByStage]
  )

  const handleStageSelectionChange = (stage: SeedStage, value: string) => {
    setStageSelectionsTouched(true)
    setStageSelections((prev) => ({
      ...prev,
      [stage]: value,
    }))
  }

  const handleResetStageAgents = () => {
    setStageSelections({ ...orgDefaultByStage })
    setStageSelectionsTouched(false)
  }

  const [name, setName] = useState(project?.name || '')
  const [slug, setSlug] = useState(project?.slug || '')
  const [slugTouched, setSlugTouched] = useState(Boolean(project))
  const [editingSlug, setEditingSlug] = useState(false)

  const [visibility, setVisibility] = useState<ProjectVisibility>(
    project?.visibility || 'private'
  )
  const [monthlyBudget, setMonthlyBudget] = useState<string>(
    project?.monthly_budget !== null && project?.monthly_budget !== undefined
      ? String(project.monthly_budget)
      : ''
  )
  const [description, setDescription] = useState(project?.description || '')
  const [tagsInput, setTagsInput] = useState((project?.tags || []).join(', '))

  // Optional initial repository attachment when creating a project
  const [gitBackendId, setGitBackendId] = useState<string>(
    gitBackends[0]?.id || ''
  )
  const [repoMode, setRepoMode] = useState<ProjectRepoSetupMode>(() => {
    if (initialRepoMode) return initialRepoMode
    return gitBackends.length > 0 ? 'existing' : 'local'
  })
  const [repoFullName, setRepoFullName] = useState('')
  const [localWorkspacePath, setLocalWorkspacePath] = useState('')
  const [defaultBranch, setDefaultBranch] = useState('main')
  const [useCustomRepoInput, setUseCustomRepoInput] = useState(false)
  const [newRepoName, setNewRepoName] = useState('')
  const [newRepoNameTouched, setNewRepoNameTouched] = useState(false)
  const [newRepoPrivate, setNewRepoPrivate] = useState(true)
  const [isSyncingBackendRepos, setIsSyncingBackendRepos] = useState(false)
  const autoDiscoveredIdsRef = useRef<Set<string>>(new Set())

  const selectedGitBackend = useMemo(
    () => gitBackends.find((b) => b.id === (gitBackendId || gitBackends[0]?.id)),
    [gitBackends, gitBackendId]
  )
  const availableBackendRepos = useMemo(
    () => getEnabledBackendRepositories(selectedGitBackend),
    [selectedGitBackend]
  )

  useEffect(() => {
    if (
      isEditing ||
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
  }, [isEditing, selectedGitBackend, queryClient])

  const handleSyncBackendRepos = async () => {
    if (!selectedGitBackend) return
    setIsSyncingBackendRepos(true)
    setError(null)
    try {
      await api.backends.discoverRepos(selectedGitBackend.id)
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      setUseCustomRepoInput(false)
    } catch (err) {
      setError(
        err instanceof Error
          ? err.message
          : 'Failed to sync repositories from Git provider.'
      )
    } finally {
      setIsSyncingBackendRepos(false)
    }
  }

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [createdDraftProject, setCreatedDraftProject] = useState<Project | null>(null)
  const [createdRemoteRepo, setCreatedRemoteRepo] = useState<{
    backendId: string
    name: string
    fullName: string
    defaultBranch: string
  } | null>(null)
  const [attachedRepoKey, setAttachedRepoKey] = useState<string | null>(null)

  useEffect(() => {
    setCreatedDraftProject(null)
    setCreatedRemoteRepo(null)
    setAttachedRepoKey(null)
  }, [project])

  const effectiveSlug = slugTouched ? slug : deriveProjectSlug(name)
  const orgDefaultBudget = orgSettings?.default_monthly_budget
    ? parseFloat(orgSettings.default_monthly_budget).toFixed(0)
    : '500'

  const handleNameChange = (val: string) => {
    setName(val)
    const derived = deriveProjectSlug(val)
    if (!slugTouched) {
      setSlug(derived)
    }
    if (!newRepoNameTouched) {
      setNewRepoName(derived)
    }
  }

  const effectiveNewRepoName = (
    newRepoNameTouched ? newRepoName : effectiveSlug
  ).trim()

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const finalSlug = effectiveSlug.trim().toLowerCase()

    if (!trimmedName) {
      setError('Project name is required.')
      return
    }
    if (finalSlug.length < 2) {
      setError('Project slug must be at least 2 characters.')
      return
    }

    const trimmedRepo = repoFullName.trim()
    if (
      !isEditing &&
      repoMode === 'existing' &&
      trimmedRepo &&
      (!trimmedRepo.includes('/') || trimmedRepo.length < 3)
    ) {
      setError('Repository must be in owner/repo format (e.g. acme/payments-api).')
      return
    }
    if (!isEditing && repoMode === 'create' && !effectiveNewRepoName) {
      setError('Enter a name for the new repository.')
      return
    }

    let parsedBudget: number | null = null
    if (monthlyBudget.trim() !== '') {
      const num = parseFloat(monthlyBudget.trim())
      if (!Number.isFinite(num) || num < 0) {
        setError('Monthly budget must be a valid non-negative amount.')
        return
      }
      parsedBudget = num
    }

    const parsedTags = tagsInput
      .split(',')
      .map((tag) => tag.trim())
      .filter(Boolean)

    setIsSubmitting(true)
    setError(null)

    try {
      let saved: Project
      if (isEditing && project) {
        saved = await api.projects.update(project.id, {
          name: trimmedName,
          slug: finalSlug,
          description: description.trim(),
          tags: parsedTags,
          visibility,
          monthly_budget: parsedBudget,
        })
      } else {
        let targetRepoFullName = trimmedRepo
        let targetDefaultBranch = defaultBranch.trim() || 'main'

        if (createdDraftProject) {
          saved = await api.projects.update(createdDraftProject.id, {
            name: trimmedName,
            slug: finalSlug,
            description: description.trim(),
            tags: parsedTags,
            visibility,
            monthly_budget: parsedBudget,
          })
          setCreatedDraftProject(saved)
        } else {
          saved = await api.projects.create({
            name: trimmedName,
            slug: finalSlug,
            description: description.trim(),
            tags: parsedTags,
            visibility,
            monthly_budget: parsedBudget,
          })
          setCreatedDraftProject(saved)
        }

        if (repoMode === 'local') {
          const localKey = `local:${localWorkspacePath.trim()}:${targetDefaultBranch}`
          if (attachedRepoKey !== localKey) {
            await api.localWorkspaces.init(saved.id, {
              workspace_path: localWorkspacePath.trim() || null,
              default_branch: targetDefaultBranch,
            })
            setAttachedRepoKey(localKey)
            await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
          }
        } else if (repoMode === 'create' && gitBackendId && effectiveNewRepoName) {
          if (
            createdRemoteRepo &&
            createdRemoteRepo.backendId === gitBackendId &&
            createdRemoteRepo.name === effectiveNewRepoName
          ) {
            targetRepoFullName = createdRemoteRepo.fullName
            targetDefaultBranch = createdRemoteRepo.defaultBranch
          } else {
            const createdRemote = await api.backends.createRepo(gitBackendId, {
              name: effectiveNewRepoName,
              description: description.trim(),
              private: newRepoPrivate,
              auto_init: true,
            })
            targetRepoFullName = createdRemote.full_name
            targetDefaultBranch = createdRemote.default_branch || 'main'
            setCreatedRemoteRepo({
              backendId: gitBackendId,
              name: effectiveNewRepoName,
              fullName: targetRepoFullName,
              defaultBranch: targetDefaultBranch,
            })
            await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
          }
          const attachKey = `${gitBackendId}:${targetRepoFullName}`
          if (targetRepoFullName && attachedRepoKey !== attachKey) {
            await api.projects.addRepo(saved.id, {
              backend_id: gitBackendId,
              full_name: targetRepoFullName,
              default_branch: targetDefaultBranch,
              is_primary: true,
            })
            setAttachedRepoKey(attachKey)
          }
        } else if (repoMode === 'existing' && targetRepoFullName && gitBackendId) {
          const attachKey = `${gitBackendId}:${targetRepoFullName}`
          if (attachedRepoKey !== attachKey) {
            await api.projects.addRepo(saved.id, {
              backend_id: gitBackendId,
              full_name: targetRepoFullName,
              default_branch: targetDefaultBranch,
              is_primary: true,
            })
            setAttachedRepoKey(attachKey)
          }
        }

        if (customizedStages.length > 0) {
          await Promise.all(
            customizedStages.map((stage) => {
              const val = stageSelections[stage]
              const orgVal = orgDefaultByStage[stage]
              if (val === '__skip__') {
                return api.policies.updateProject(saved.id, stage, {
                  enabled: false,
                })
              }
              return api.policies.updateProject(saved.id, stage, {
                ...(orgVal === '__skip__' ? { enabled: true } : {}),
                agent_id: val || null,
              })
            })
          )
        }
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projectDetail(saved.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projectRepos(saved.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projectMembers(saved.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projectPolicies(saved.id) }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])

      onSaved?.(saved)
      onClose()
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to save project.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5">
      {error && (
        <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
          <AlertCircle size={15} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {/* Name + Slug */}
      <div className="flex flex-col gap-2">
        <div className="flex items-center justify-between gap-2">
          <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
            {t('modals.project.nameLabel')} *
          </label>
          <div className="flex items-center gap-1.5 text-xs font-mono">
            <span className="text-zinc-400 dark:text-zinc-500">
              {t('modals.project.slugLabel').toLowerCase()}:
            </span>
            {editingSlug ? (
              <div className="flex items-center gap-1.5">
                <input
                  type="text"
                  value={slug}
                  onChange={(e) => {
                    setSlugTouched(true)
                    setSlug(
                      e.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9-]/g, '-')
                        .slice(0, 63)
                    )
                  }}
                  placeholder={t('modals.project.slugPlaceholder')}
                  className="px-2.5 py-0.5 text-xs font-mono rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-teal-600 dark:text-teal-400 focus:outline-none focus:border-teal-500 w-44"
                />
                <button
                  type="button"
                  onClick={() => setEditingSlug(false)}
                  className="text-[11px] font-sans font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {t('modals.project.slugDone')}
                </button>
              </div>
            ) : (
              <div className="flex items-center gap-1.5">
                <span className="text-teal-600 dark:text-teal-400 font-semibold">
                  {effectiveSlug || 'project-slug'}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    if (!slugTouched) setSlug(effectiveSlug)
                    setEditingSlug(true)
                  }}
                  className="text-[11px] font-sans font-semibold text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                >
                  [{t('modals.project.slugEdit')}]
                </button>
              </div>
            )}
          </div>
        </div>
        <input
          type="text"
          value={name}
          onChange={(e) => handleNameChange(e.target.value)}
          placeholder={t('modals.project.namePlaceholder')}
          autoFocus
          required
          className="w-full px-3.5 py-2.5 text-sm rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500"
        />
      </div>

      {/* Description & Tags */}
      <div className="grid grid-cols-1 gap-4">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
            {t('modals.project.descLabel')}
          </label>
          <textarea
            rows={2}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            placeholder={t('modals.project.descPlaceholder')}
            className="w-full px-3.5 py-2.5 text-xs rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500 resize-y"
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
            {t('modals.project.tagsLabel')}
          </label>
          <input
            type="text"
            value={tagsInput}
            onChange={(e) => setTagsInput(e.target.value)}
            placeholder={t('modals.project.tagsPlaceholder')}
            className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500"
          />
        </div>
      </div>

      {/* Primary Repository (Create mode only) */}
      {!isEditing && (
        <div className="flex flex-col gap-3 p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/30 border border-zinc-200 dark:border-zinc-700/80">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2.5 flex-wrap">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                {t('modals.project.repositoryLabel')}{' '}
                <span className="font-normal lowercase text-zinc-400">(optional)</span>
              </label>
              <div className="inline-flex items-center rounded-lg p-0.5 bg-zinc-200/70 dark:bg-zinc-800 border border-zinc-300/70 dark:border-zinc-700 flex-wrap">
                {gitBackends.length > 0 && (
                  <>
                    <button
                      type="button"
                      onClick={() => setRepoMode('existing')}
                      className={`px-2 py-0.5 rounded-md text-[11px] font-semibold transition cursor-pointer ${
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
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold transition cursor-pointer ${
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
                  data-testid="project-repo-mode-local"
                  onClick={() => setRepoMode('local')}
                  className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold transition cursor-pointer ${
                    repoMode === 'local'
                      ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                      : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
                  }`}
                >
                  <FolderGit2 size={11} />
                  On-Device Local Git
                </button>
                {gitBackends.length === 0 && (
                  <button
                    type="button"
                    onClick={() => setRepoMode('none')}
                    className={`px-2 py-0.5 rounded-md text-[11px] font-semibold transition cursor-pointer ${
                      repoMode === 'none'
                        ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                        : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
                    }`}
                  >
                    Skip for now
                  </button>
                )}
              </div>
            </div>

            {selectedGitBackend &&
              (repoMode === 'existing' || repoMode === 'create') && (
              <div className="flex items-center gap-3">
                <a
                  href={buildNewRepoWebUrl(
                    selectedGitBackend.provider,
                    selectedGitBackend.endpoint
                  )}
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
              </div>
            )}
          </div>

          {repoMode === 'local' ? (
            <div className="flex flex-col gap-2.5">
              <div className="grid grid-cols-1 sm:grid-cols-12 gap-3">
                <div className="sm:col-span-9">
                  <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                    Local Workspace Directory <span className="text-zinc-400">(optional)</span>
                  </label>
                  <div className="relative flex items-center">
                    <FolderGit2
                      size={14}
                      className="absolute left-3 text-teal-500 pointer-events-none"
                    />
                    <input
                      type="text"
                      data-testid="local-workspace-path-input"
                      value={localWorkspacePath}
                      onChange={(e) => setLocalWorkspacePath(e.target.value)}
                      placeholder="/Users/you/code/my-app (or leave blank for managed repo)"
                      className="w-full h-9 pl-8 pr-3 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                    />
                  </div>
                </div>
                <div className="sm:col-span-3">
                  <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                    Default Branch
                  </label>
                  <div className="relative flex items-center">
                    <GitBranch
                      size={13}
                      className="absolute left-2.5 text-zinc-400 pointer-events-none"
                    />
                    <input
                      type="text"
                      value={defaultBranch}
                      onChange={(e) => setDefaultBranch(e.target.value)}
                      placeholder="main"
                      className="w-full h-9 pl-7 pr-2.5 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                    />
                  </div>
                </div>
              </div>
              <p className="text-[11px] text-zinc-500 dark:text-zinc-400 leading-relaxed">
                Initializes an On-Device Local Git repository served over Git Smart HTTP (<code className="font-mono">/api/v1/local-git/{effectiveSlug || 'project'}.git</code>) with no GitHub or GitLab account required. Supply a local folder path to link or initialize existing source files.
              </p>
            </div>
          ) : repoMode === 'none' || gitBackends.length === 0 ? (
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              No repository will be attached yet. You can initialize On-Device Local Git or connect GitHub/GitLab anytime in Project Settings.
            </p>
          ) : repoMode === 'create' ? (
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-3">
              <div className="sm:col-span-4">
                <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                  Git Provider
                </label>
                <select
                  value={gitBackendId}
                  onChange={(e) => setGitBackendId(e.target.value)}
                  className="w-full h-9 px-3 text-xs font-medium rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500 cursor-pointer"
                >
                  {gitBackends.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name} ({b.provider})
                    </option>
                  ))}
                </select>
              </div>
              <div className="sm:col-span-5">
                <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                  New Repository Name (or org/repo)
                </label>
                <div className="relative flex items-center">
                  {selectedGitBackend && (
                    <span className="absolute left-3 text-zinc-400 pointer-events-none">
                      <ProviderIcon
                        type="git"
                        provider={selectedGitBackend.provider || 'github'}
                        size={14}
                      />
                    </span>
                  )}
                  <input
                    type="text"
                    value={effectiveNewRepoName}
                    onChange={(e) => {
                      setNewRepoNameTouched(true)
                      setNewRepoName(e.target.value)
                    }}
                    placeholder={effectiveSlug || 'my-new-repo'}
                    className="w-full h-9 pl-8 pr-3 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                </div>
              </div>
              <div className="sm:col-span-3">
                <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                  Repo Visibility
                </label>
                <div className="grid grid-cols-2 gap-1 h-9 p-1 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80">
                  <button
                    type="button"
                    onClick={() => setNewRepoPrivate(true)}
                    className={`rounded-lg text-[11px] font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
                      newRepoPrivate
                        ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                        : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                    }`}
                  >
                    <Lock size={11} />
                    Private
                  </button>
                  <button
                    type="button"
                    onClick={() => setNewRepoPrivate(false)}
                    className={`rounded-lg text-[11px] font-semibold flex items-center justify-center gap-1 transition cursor-pointer ${
                      !newRepoPrivate
                        ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300'
                        : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                    }`}
                  >
                    <Globe size={11} />
                    Public
                  </button>
                </div>
              </div>
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-12 gap-3">
              <div className="sm:col-span-4">
                <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                  Git Provider
                </label>
                <select
                  value={gitBackendId}
                  onChange={(e) => {
                    setGitBackendId(e.target.value)
                    setRepoFullName('')
                    setDefaultBranch('main')
                    setUseCustomRepoInput(false)
                  }}
                  className="w-full h-9 px-3 text-xs font-medium rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500 cursor-pointer"
                >
                  {gitBackends.map((b) => (
                    <option key={b.id} value={b.id}>
                      {b.name} ({b.provider})
                    </option>
                  ))}
                </select>
              </div>
              <div className="sm:col-span-5">
                <div className="flex items-center justify-between mb-1.5">
                  <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400">
                    Repository
                  </label>
                  {availableBackendRepos.length > 0 && useCustomRepoInput && (
                    <button
                      type="button"
                      onClick={() => {
                        setUseCustomRepoInput(false)
                        setRepoFullName('')
                      }}
                      className="text-[11px] font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                    >
                      Select from list
                    </button>
                  )}
                </div>
                {availableBackendRepos.length > 0 && !useCustomRepoInput ? (
                  <select
                    value={repoFullName}
                    onChange={(e) => {
                      const val = e.target.value
                      if (val === '__custom__') {
                        setUseCustomRepoInput(true)
                        setRepoFullName('')
                        return
                      }
                      setRepoFullName(val)
                      const matched = availableBackendRepos.find(
                        (r) => r.full_name === val
                      )
                      if (matched) {
                        setDefaultBranch(matched.default_branch || 'main')
                      }
                    }}
                    className="w-full h-9 px-3 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 cursor-pointer"
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
                  <div className="relative flex items-center">
                    {selectedGitBackend && (
                      <span className="absolute left-3 text-zinc-400 pointer-events-none">
                        <ProviderIcon
                          type="git"
                          provider={selectedGitBackend.provider || 'github'}
                          size={14}
                        />
                      </span>
                    )}
                    <input
                      type="text"
                      value={repoFullName}
                      onChange={(e) => setRepoFullName(e.target.value)}
                      placeholder="acme/core-api"
                      className="w-full h-9 pl-8 pr-3 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                    />
                  </div>
                )}
              </div>
              <div className="sm:col-span-3">
                <label className="block text-[11px] font-medium text-zinc-500 dark:text-zinc-400 mb-1.5">
                  Default Branch
                </label>
                <div className="relative flex items-center">
                  <GitBranch
                    size={13}
                    className="absolute left-2.5 text-zinc-400 pointer-events-none"
                  />
                  <input
                    type="text"
                    value={defaultBranch}
                    onChange={(e) => setDefaultBranch(e.target.value)}
                    placeholder="main"
                    className="w-full h-9 pl-7 pr-2.5 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Stage Agents (Create mode only — preset to org defaults) */}
      {!isEditing && (
        <div className="flex flex-col gap-3 p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/30 border border-zinc-200 dark:border-zinc-700/80">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex items-center gap-2 flex-wrap">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Stage Agents
              </label>
              {customizedStages.length > 0 ? (
                <span className="px-2 py-0.5 rounded-md text-[11px] font-semibold bg-teal-500/15 text-teal-700 dark:text-teal-300 border border-teal-500/25">
                  {customizedStages.length === 1
                    ? '1 stage customized'
                    : `${customizedStages.length} stages customized`}
                </span>
              ) : (
                <span className="px-2 py-0.5 rounded-md text-[11px] font-medium bg-zinc-200/70 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400">
                  Org default policy
                </span>
              )}
            </div>

            {customizedStages.length > 0 ? (
              <button
                type="button"
                onClick={handleResetStageAgents}
                className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                <RotateCcw size={11} />
                <span>Reset to org defaults</span>
              </button>
            ) : (
              <span className="text-[11px] text-zinc-400 dark:text-zinc-500">
                Preset to organization defaults
              </span>
            )}
          </div>

          <div className="flex flex-col gap-1.5">
            {ORDERED_STAGES.map((stage) => {
              const meta = STAGE_METADATA[stage]
              const selectedVal = stageSelections[stage] ?? ''
              const orgDefaultVal = orgDefaultByStage[stage] ?? ''
              const isCustomized = selectedVal !== orgDefaultVal
              const isSkipped = selectedVal === '__skip__'

              return (
                <div
                  key={stage}
                  className={`flex items-center justify-between gap-3 px-2.5 py-1.5 rounded-lg bg-white dark:bg-zinc-900 border transition ${
                    isCustomized
                      ? 'border-teal-500/45 dark:border-teal-500/45'
                      : 'border-zinc-200/80 dark:border-zinc-700/70'
                  } ${isSkipped ? 'opacity-65' : ''}`}
                >
                  <div className="flex items-center gap-2 min-w-[125px] sm:min-w-[150px] shrink-0">
                    <span className="w-5 h-5 rounded-md bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 font-mono text-[11px] font-bold text-zinc-500 dark:text-zinc-400 flex items-center justify-center shrink-0">
                      {meta.stepNumber}
                    </span>
                    <StageBadge stage={stage} />
                    {meta.isRequired && (
                      <span
                        title="Required stage"
                        className="text-zinc-400 dark:text-zinc-500"
                      >
                        <Lock size={10} />
                      </span>
                    )}
                  </div>

                  <div className="flex-1 min-w-0 flex items-center gap-1.5">
                    <div className="relative flex-1 min-w-0 flex items-center">
                      <span className="absolute left-2.5 text-zinc-400 pointer-events-none">
                        {isSkipped ? <Ban size={13} /> : <Bot size={13} />}
                      </span>
                      <select
                        aria-label={`${meta.label} stage agent`}
                        value={selectedVal}
                        onChange={(e) =>
                          handleStageSelectionChange(stage, e.target.value)
                        }
                        className="w-full h-8 pl-7 pr-6 text-xs font-medium rounded-lg bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500 cursor-pointer truncate"
                      >
                        {(orgDefaultVal === '' || selectedVal === '') && (
                          <option value="">
                            No agent assigned
                            {orgDefaultVal === '' ? ' (Org default)' : ''}
                          </option>
                        )}
                        {stageAgents.map((agent) => (
                          <option key={agent.id} value={agent.id}>
                            {agent.name} (@{agent.handle})
                            {agent.model ? ` · ${agent.model}` : ''}
                            {!agent.enabled ? ' [Paused]' : ''}
                            {orgDefaultVal === agent.id ? ' (Org default)' : ''}
                          </option>
                        ))}
                        {!meta.isRequired && (
                          <option value="__skip__">
                            Skip stage (disabled)
                            {orgDefaultVal === '__skip__' ? ' (Org default)' : ''}
                          </option>
                        )}
                      </select>
                    </div>

                    {isCustomized && (
                      <button
                        type="button"
                        onClick={() =>
                          handleStageSelectionChange(stage, orgDefaultVal)
                        }
                        title="Reset stage to organization default"
                        className="p-1.5 rounded-md text-teal-600 dark:text-teal-400 hover:bg-teal-500/10 transition cursor-pointer shrink-0"
                      >
                        <RotateCcw size={12} />
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Visibility & Monthly Budget */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 items-start">
        <div className="flex flex-col gap-1.5">
          <div className="h-5 flex items-center">
            <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              {t('modals.project.visibilityLabel')}
            </label>
          </div>
          <SegmentedControl<ProjectVisibility>
            ariaLabel={t('modals.project.visibilityLabel')}
            selectedId={visibility}
            onSelect={setVisibility}
            className="w-full h-10"
            items={[
              {
                id: 'private',
                label: t('modals.project.visibilityOptions.private'),
                icon: <Lock size={13} />,
              },
              {
                id: 'public',
                label: t('modals.project.visibilityOptions.public'),
                icon: <Globe size={13} />,
              },
            ]}
          />
        </div>

        <div className="flex flex-col gap-1.5">
          <div className="h-5 flex items-center justify-between">
            <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              {t('modals.project.budgetLabel')}
            </label>
            {monthlyBudget.trim() !== '' ? (
              <button
                type="button"
                onClick={() => setMonthlyBudget('')}
                className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                <RotateCcw size={11} />
                <span>{t('modals.project.budgetReset')}</span>
              </button>
            ) : (
              <span className="text-[11px] text-zinc-400 font-mono">
                ${orgDefaultBudget} {t('modals.project.budgetOrgDefault')}
              </span>
            )}
          </div>
          <div className="relative flex items-center">
            <span className="absolute left-3.5 text-xs font-mono font-semibold text-zinc-400 pointer-events-none">
              $
            </span>
            <input
              type="number"
              min="0"
              step="1"
              value={monthlyBudget}
              onChange={(e) => setMonthlyBudget(e.target.value)}
              placeholder={`${orgDefaultBudget} (${t('modals.project.budgetOrgDefault')})`}
              className="w-full h-10 pl-7 pr-12 text-sm font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500"
            />
            <span className="absolute right-3 text-[11px] font-mono text-zinc-400 pointer-events-none">
              / mo
            </span>
          </div>
        </div>
      </div>

      {/* Footer */}
      <div className="flex items-center justify-end gap-2.5 pt-4 border-t border-zinc-200 dark:border-zinc-700/80">
        <Button
          type="button"
          variant="secondary"
          size="md"
          onClick={onClose}
          isDisabled={isSubmitting}
          className="rounded-xl font-semibold cursor-pointer"
        >
          {t('common.cancel')}
        </Button>
        <Button
          type="submit"
          variant="primary"
          size="md"
          isDisabled={isSubmitting || !name.trim()}
          className="rounded-xl font-semibold shadow-md shadow-teal-600/20 cursor-pointer"
        >
          {isSubmitting
            ? repoMode === 'create'
              ? 'Creating repo & project...'
              : repoMode === 'local'
                ? 'Initializing local Git & project...'
                : 'Saving...'
            : isEditing
              ? t('modals.project.saveButton')
              : t('modals.project.createButton')}
        </Button>
      </div>
    </form>
  )
}

export const ProjectModal: React.FC<ProjectModalProps> = ({
  isOpen,
  onClose,
  project = null,
  initialRepoMode,
  onSaved,
}) => {
  const { t } = useTranslation()

  return (
    <Modal
      isOpen={isOpen}
      onClose={onClose}
      title={
        project ? t('modals.project.editTitle') : t('modals.project.createTitle')
      }
      size="2xl"
    >
      {isOpen && (
        <ProjectModalForm
          key={`${project?.id || 'create'}:${initialRepoMode || 'default'}`}
          onClose={onClose}
          project={project}
          initialRepoMode={initialRepoMode}
          onSaved={onSaved}
        />
      )}
    </Modal>
  )
}
