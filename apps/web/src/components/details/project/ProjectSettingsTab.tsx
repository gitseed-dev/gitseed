import React, { useEffect, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  DollarSign,
  FlaskConical,
  Globe,
  Lock,
  Play,
  RotateCcw,
  Sliders,
  Terminal,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { SegmentedControl } from '../../common/SegmentedControl'
import { ProjectAccessAndDangerCards } from './ProjectAccessAndDangerCards'
import { ProjectRepositoriesCard } from './ProjectRepositoriesCard'
import { ProjectWebhooksCard } from './ProjectWebhooksCard'
import { useData } from '../../../context/DataContext'
import { useTranslation } from '../../../context/I18nContext'
import type {
  Project,
  ProjectRepository,
  ProjectVisibility,
} from '../../../types'

export interface ProjectSettingsTabProps {
  project: Project
  repos: ProjectRepository[]
  canManage: boolean
  initialSection?: string | null
}

export const ProjectSettingsTab: React.FC<ProjectSettingsTabProps> = ({
  project,
  repos,
  canManage,
  initialSection = null,
}) => {
  const queryClient = useQueryClient()
  const { backends, orgSettings } = useData()
  const { t } = useTranslation()

  // General form state
  const [name, setName] = useState(project.name)
  const [slug, setSlug] = useState(project.slug)
  const [description, setDescription] = useState(project.description || '')
  const [visibility, setVisibility] = useState<ProjectVisibility>(
    project.visibility
  )
  const [tagsInput, setTagsInput] = useState((project.tags || []).join(', '))

  // Spend & Concurrency overrides
  const [monthlyBudget, setMonthlyBudget] = useState<string>(
    project.monthly_budget !== null && project.monthly_budget !== undefined
      ? String(project.monthly_budget)
      : ''
  )
  const [seedBudgetCap, setSeedBudgetCap] = useState<string>(
    project.seed_budget_cap !== null && project.seed_budget_cap !== undefined
      ? String(project.seed_budget_cap)
      : ''
  )
  const [spendCheckpoint, setSpendCheckpoint] = useState<string>(
    project.spend_checkpoint !== null && project.spend_checkpoint !== undefined
      ? String(project.spend_checkpoint)
      : ''
  )
  const [maxConcurrentRuns, setMaxConcurrentRuns] = useState<string>(
    project.max_concurrent_runs !== null &&
      project.max_concurrent_runs !== undefined
      ? String(project.max_concurrent_runs)
      : ''
  )

  // Sandbox & Test Commands (project.sandbox_config)
  const [sandboxImage, setSandboxImage] = useState<string>(
    project.sandbox_config?.image || ''
  )
  const [sandboxSetupCmd, setSandboxSetupCmd] = useState<string>(
    project.sandbox_config?.setup_command || ''
  )
  const [sandboxRunCmd, setSandboxRunCmd] = useState<string>(
    project.sandbox_config?.run_command || ''
  )
  const [sandboxTestCmd, setSandboxTestCmd] = useState<string>(
    project.sandbox_config?.test_command || ''
  )
  const [sandboxGlobsInput, setSandboxGlobsInput] = useState<string>(
    (project.sandbox_config?.artifact_globs || []).join(', ')
  )
  const [blockedPathsInput, setBlockedPathsInput] = useState<string>(
    (project.sandbox_config?.blocked_paths || []).join(', ')
  )
  const [requireTestsPass, setRequireTestsPass] = useState<boolean>(
    Boolean(project.sandbox_config?.require_tests_pass)
  )
  const [enableDependencyCache, setEnableDependencyCache] = useState<boolean>(
    project.sandbox_config?.enable_dependency_cache !== false
  )
  const [enableWarmWorkspace, setEnableWarmWorkspace] = useState<boolean>(
    Boolean(project.sandbox_config?.enable_warm_workspace)
  )
  const [batchExecuteTasks, setBatchExecuteTasks] = useState<boolean>(
    project.sandbox_config?.batch_execute_tasks !== false
  )
  const [strictCliExecution, setStrictCliExecution] = useState<boolean>(
    Boolean(project.sandbox_config?.strict_cli_execution)
  )
  const [gitCloneDepth, setGitCloneDepth] = useState<string>(
    project.sandbox_config?.git_clone_depth !== undefined &&
      project.sandbox_config?.git_clone_depth !== null
      ? String(project.sandbox_config.git_clone_depth)
      : '50'
  )
  const [autoRebaseSeedBranch, setAutoRebaseSeedBranch] = useState<boolean>(
    project.sandbox_config?.auto_rebase_seed_branch !== false
  )
  const [stageSeedsByDefault, setStageSeedsByDefault] = useState<boolean>(
    project.sandbox_config?.stage_seeds_by_default !== undefined &&
      project.sandbox_config?.stage_seeds_by_default !== null
      ? Boolean(project.sandbox_config.stage_seeds_by_default)
      : Boolean(orgSettings?.default_stage_seeds)
  )

  const [isSavingGeneral, setIsSavingGeneral] = useState(false)
  const [isSavingBudget, setIsSavingBudget] = useState(false)
  const [isSavingSandbox, setIsSavingSandbox] = useState(false)
  const [savedNotice, setSavedNotice] = useState<string | null>(null)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [confirmArchiveOpen, setConfirmArchiveOpen] = useState(false)

  const parsedTags = tagsInput
    .split(',')
    .map((tag) => tag.trim())
    .filter(Boolean)

  const isGeneralDirty =
    name.trim() !== project.name ||
    slug.trim().toLowerCase() !== project.slug ||
    description.trim() !== (project.description || '').trim() ||
    visibility !== project.visibility ||
    parsedTags.join(',') !== (project.tags || []).join(',')

  const normalizeNum = (val: string | number | null | undefined): number | null => {
    if (val === null || val === undefined) return null
    const str = String(val).trim()
    if (!str) return null
    const num = Number(str)
    return Number.isFinite(num) ? num : null
  }

  const isBudgetDirty =
    normalizeNum(monthlyBudget) !== normalizeNum(project.monthly_budget) ||
    normalizeNum(seedBudgetCap) !== normalizeNum(project.seed_budget_cap) ||
    normalizeNum(spendCheckpoint) !== normalizeNum(project.spend_checkpoint) ||
    normalizeNum(maxConcurrentRuns) !== normalizeNum(project.max_concurrent_runs)

  const parsedSandboxGlobs = sandboxGlobsInput
    .split(',')
    .map((g) => g.trim())
    .filter(Boolean)

  const parsedBlockedPaths = blockedPathsInput
    .split(',')
    .map((p) => p.trim())
    .filter(Boolean)

  const isSandboxDirty =
    sandboxImage.trim() !== (project.sandbox_config?.image || '').trim() ||
    sandboxSetupCmd.trim() !==
      (project.sandbox_config?.setup_command || '').trim() ||
    sandboxRunCmd.trim() !==
      (project.sandbox_config?.run_command || '').trim() ||
    sandboxTestCmd.trim() !==
      (project.sandbox_config?.test_command || '').trim() ||
    parsedSandboxGlobs.join(',') !==
      (project.sandbox_config?.artifact_globs || []).join(',') ||
    parsedBlockedPaths.join(',') !==
      (project.sandbox_config?.blocked_paths || []).join(',') ||
    requireTestsPass !== Boolean(project.sandbox_config?.require_tests_pass) ||
    enableDependencyCache !==
      (project.sandbox_config?.enable_dependency_cache !== false) ||
    enableWarmWorkspace !==
      Boolean(project.sandbox_config?.enable_warm_workspace) ||
    batchExecuteTasks !==
      (project.sandbox_config?.batch_execute_tasks !== false) ||
    strictCliExecution !==
      Boolean(project.sandbox_config?.strict_cli_execution) ||
    (normalizeNum(gitCloneDepth) ?? 50) !==
      (normalizeNum(project.sandbox_config?.git_clone_depth) ?? 50) ||
    autoRebaseSeedBranch !==
      (project.sandbox_config?.auto_rebase_seed_branch !== false) ||
    stageSeedsByDefault !==
      (project.sandbox_config?.stage_seeds_by_default !== undefined &&
      project.sandbox_config?.stage_seeds_by_default !== null
        ? Boolean(project.sandbox_config.stage_seeds_by_default)
        : Boolean(orgSettings?.default_stage_seeds))

  useEffect(() => {
    setName(project.name)
    setSlug(project.slug)
    setDescription(project.description || '')
    setVisibility(project.visibility)
    setTagsInput((project.tags || []).join(', '))
    setMonthlyBudget(
      project.monthly_budget !== null && project.monthly_budget !== undefined
        ? String(project.monthly_budget)
        : ''
    )
    setSeedBudgetCap(
      project.seed_budget_cap !== null && project.seed_budget_cap !== undefined
        ? String(project.seed_budget_cap)
        : ''
    )
    setSpendCheckpoint(
      project.spend_checkpoint !== null &&
        project.spend_checkpoint !== undefined
        ? String(project.spend_checkpoint)
        : ''
    )
    setMaxConcurrentRuns(
      project.max_concurrent_runs !== null &&
        project.max_concurrent_runs !== undefined
        ? String(project.max_concurrent_runs)
        : ''
    )
    setSandboxImage(project.sandbox_config?.image || '')
    setSandboxSetupCmd(project.sandbox_config?.setup_command || '')
    setSandboxRunCmd(project.sandbox_config?.run_command || '')
    setSandboxTestCmd(project.sandbox_config?.test_command || '')
    setSandboxGlobsInput(
      (project.sandbox_config?.artifact_globs || []).join(', ')
    )
    setBlockedPathsInput(
      (project.sandbox_config?.blocked_paths || []).join(', ')
    )
    setRequireTestsPass(Boolean(project.sandbox_config?.require_tests_pass))
    setEnableDependencyCache(
      project.sandbox_config?.enable_dependency_cache !== false
    )
    setEnableWarmWorkspace(
      Boolean(project.sandbox_config?.enable_warm_workspace)
    )
    setBatchExecuteTasks(
      project.sandbox_config?.batch_execute_tasks !== false
    )
    setStrictCliExecution(
      Boolean(project.sandbox_config?.strict_cli_execution)
    )
    setGitCloneDepth(
      project.sandbox_config?.git_clone_depth !== undefined &&
        project.sandbox_config?.git_clone_depth !== null
        ? String(project.sandbox_config.git_clone_depth)
        : '50'
    )
    setAutoRebaseSeedBranch(
      project.sandbox_config?.auto_rebase_seed_branch !== false
    )
    setStageSeedsByDefault(
      project.sandbox_config?.stage_seeds_by_default !== undefined &&
        project.sandbox_config?.stage_seeds_by_default !== null
        ? Boolean(project.sandbox_config.stage_seeds_by_default)
        : Boolean(orgSettings?.default_stage_seeds)
    )
  }, [project, orgSettings])

  useEffect(() => {
    if (!initialSection) return
    const el = document.getElementById(`project-settings-${initialSection}`)
    if (el) {
      el.scrollIntoView({ behavior: 'smooth', block: 'start' })
    }
  }, [initialSection])

  const flashSaved = (msg: string) => {
    setSavedNotice(msg)
    window.setTimeout(() => setSavedNotice(null), 2500)
  }

  const invalidateProject = async () => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
      queryClient.invalidateQueries({
        queryKey: queryKeys.projectDetail(project.id),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
    ])
  }

  const handleSaveGeneral = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canManage) return
    setIsSavingGeneral(true)
    setErrorMessage(null)
    try {
      const parsedTags = tagsInput
        .split(',')
        .map((tag) => tag.trim())
        .filter(Boolean)

      await api.projects.update(project.id, {
        name: name.trim(),
        slug: slug.trim().toLowerCase(),
        description: description.trim(),
        visibility,
        tags: parsedTags,
      })
      await invalidateProject()
      flashSaved('General project settings saved.')
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to save general settings.'
      )
    } finally {
      setIsSavingGeneral(false)
    }
  }

  const handleSaveBudgetOverrides = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canManage) return
    setIsSavingBudget(true)
    setErrorMessage(null)
    try {
      const mb =
        monthlyBudget.trim() === '' ? null : parseFloat(monthlyBudget.trim())
      const sbc =
        seedBudgetCap.trim() === '' ? null : parseFloat(seedBudgetCap.trim())
      const sc =
        spendCheckpoint.trim() === ''
          ? null
          : parseFloat(spendCheckpoint.trim())
      const mcr =
        maxConcurrentRuns.trim() === ''
          ? null
          : parseInt(maxConcurrentRuns.trim(), 10)

      await api.projects.update(project.id, {
        monthly_budget: mb,
        seed_budget_cap: sbc,
        spend_checkpoint: sc,
        max_concurrent_runs: mcr,
      })
      await invalidateProject()
      flashSaved('Spend and concurrency overrides saved.')
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to save spend overrides.'
      )
    } finally {
      setIsSavingBudget(false)
    }
  }

  const handleSaveSandboxConfig = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canManage) return
    setIsSavingSandbox(true)
    setErrorMessage(null)
    try {
      const parsedDepth = parseInt(gitCloneDepth.trim(), 10)
      const nextCfg: Record<string, unknown> = {
        require_tests_pass: requireTestsPass,
        enable_dependency_cache: enableDependencyCache,
        enable_warm_workspace: enableWarmWorkspace,
        batch_execute_tasks: batchExecuteTasks,
        strict_cli_execution: strictCliExecution,
        git_clone_depth: Number.isFinite(parsedDepth) && parsedDepth >= 0 ? parsedDepth : 50,
        auto_rebase_seed_branch: autoRebaseSeedBranch,
        stage_seeds_by_default: stageSeedsByDefault,
        blocked_paths: parsedBlockedPaths,
      }
      if (sandboxImage.trim()) nextCfg.image = sandboxImage.trim()
      if (sandboxSetupCmd.trim()) nextCfg.setup_command = sandboxSetupCmd.trim()
      if (sandboxRunCmd.trim()) nextCfg.run_command = sandboxRunCmd.trim()
      if (sandboxTestCmd.trim()) nextCfg.test_command = sandboxTestCmd.trim()
      if (parsedSandboxGlobs.length > 0) {
        nextCfg.artifact_globs = parsedSandboxGlobs
      }

      await api.projects.update(project.id, {
        sandbox_config: nextCfg,
      })
      await Promise.all([
        invalidateProject(),
        queryClient.invalidateQueries({
          queryKey: ['projects', project.id, 'sandboxes', 'config'],
        }),
      ])
      flashSaved('Sandbox & test commands saved.')
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Failed to save sandbox configuration.'
      )
    } finally {
      setIsSavingSandbox(false)
    }
  }

  const handleToggleBackendsRestricted = async (restricted: boolean) => {
    if (!canManage || restricted === project.backends_restricted) return
    setErrorMessage(null)
    try {
      await api.projects.update(project.id, {
        backends_restricted: restricted,
      })
      await invalidateProject()
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Failed to update backend access boundary.'
      )
    }
  }

  const handleToggleAllowedBackend = async (
    backendId: string,
    isAllowed: boolean
  ) => {
    if (!canManage) return
    setErrorMessage(null)
    try {
      if (isAllowed) {
        await api.projects.disallowBackend(project.id, backendId)
      } else {
        await api.projects.allowBackend(project.id, backendId)
      }
      await invalidateProject()
    } catch (err) {
      setErrorMessage(
        err instanceof Error ? err.message : 'Failed to update backend access.'
      )
    }
  }

  const handleArchiveOrRestore = async () => {
    if (!canManage) return
    setErrorMessage(null)
    try {
      if (project.archived_at) {
        await api.projects.restore(project.id)
      } else {
        await api.projects.archive(project.id)
      }
      await invalidateProject()
      setConfirmArchiveOpen(false)
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Failed to change project archive state.'
      )
    }
  }

  const orgMonthlyDefault = orgSettings?.default_monthly_budget || '500.00'
  const orgSeedCapDefault = orgSettings?.default_seed_budget_cap || '25.00'
  const orgCheckpointDefault = orgSettings?.default_spend_checkpoint || '10.00'
  const orgConcurrencyDefault = orgSettings?.default_project_concurrency ?? 2

  return (
    <div className="flex flex-col gap-6">
      {errorMessage && (
        <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
          <AlertCircle size={15} className="shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {savedNotice && (
        <div className="p-3.5 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-700 dark:text-emerald-300 font-semibold flex items-center gap-2">
          <Check size={15} className="shrink-0" />
          <span>{savedNotice}</span>
        </div>
      )}

      {/* 1. General & Metadata */}
      <form
        id="project-settings-general"
        onSubmit={handleSaveGeneral}
        className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 rounded-2xl shadow-xs overflow-hidden"
      >
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
              <Sliders size={16} />
            </div>
            <div>
              <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
                {t('details.project.generalTitle')}
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Project identity, URL slug, visibility, and organizational tags
              </p>
            </div>
          </div>
          {canManage && (
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isDisabled={isSavingGeneral || !isGeneralDirty || !name.trim()}
              className="rounded-xl font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSavingGeneral ? 'Saving...' : t('common.save')}
            </Button>
          )}
        </div>

        <div className="p-6 flex flex-col gap-5">
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                {t('modals.project.nameLabel')}
              </label>
              <input
                type="text"
                value={name}
                disabled={!canManage}
                onChange={(e) => setName(e.target.value)}
                className="w-full px-3.5 py-2 text-sm rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                {t('modals.project.slugLabel')}
              </label>
              <input
                type="text"
                value={slug}
                disabled={!canManage}
                onChange={(e) =>
                  setSlug(
                    e.target.value
                      .toLowerCase()
                      .replace(/[^a-z0-9-]/g, '-')
                      .slice(0, 63)
                  )
                }
                className="w-full px-3.5 py-2 text-sm font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                {t('modals.project.visibilityLabel')}
              </label>
              <SegmentedControl<ProjectVisibility>
                ariaLabel={t('modals.project.visibilityLabel')}
                selectedId={visibility}
                onSelect={(val: ProjectVisibility) =>
                  canManage && setVisibility(val)
                }
                disabled={!canManage}
                className="w-full grid grid-cols-2"
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
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                {t('modals.project.descLabel')}
              </label>
              <textarea
                rows={2}
                value={description}
                disabled={!canManage}
                onChange={(e) => setDescription(e.target.value)}
                placeholder={t('modals.project.descPlaceholder')}
                className="w-full px-3.5 py-2 text-xs rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 resize-y disabled:opacity-60"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                {t('modals.project.tagsLabel')}
              </label>
              <input
                type="text"
                value={tagsInput}
                disabled={!canManage}
                onChange={(e) => setTagsInput(e.target.value)}
                placeholder={t('modals.project.tagsPlaceholder')}
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>
          </div>
        </div>
      </form>

      {/* 2. Target Git Repositories */}
      <ProjectRepositoriesCard
        project={project}
        repos={repos}
        canManage={canManage}
      />

      {/* 3. Sandbox & Test Commands */}
      <form
        id="project-settings-sandbox"
        onSubmit={handleSaveSandboxConfig}
        className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 rounded-2xl shadow-xs overflow-hidden"
      >
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
              <Terminal size={16} />
            </div>
            <div>
              <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
                Sandbox &amp; Test Commands
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Default container image, run command, and test suite command for the Run tab and Review agent. Leave blank to fall back to <code className="font-mono text-[11px]">.gitseed/run.json</code> or stack auto-detection.
              </p>
            </div>
          </div>
          {canManage && (
            <div className="flex items-center gap-2 shrink-0">
              {(sandboxImage ||
                sandboxSetupCmd ||
                sandboxRunCmd ||
                sandboxTestCmd ||
                sandboxGlobsInput) && (
                <button
                  type="button"
                  onClick={() => {
                    setSandboxImage('')
                    setSandboxSetupCmd('')
                    setSandboxRunCmd('')
                    setSandboxTestCmd('')
                    setSandboxGlobsInput('')
                  }}
                  className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-semibold text-zinc-500 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                >
                  <RotateCcw size={12} />
                  <span>Clear All</span>
                </button>
              )}
              <Button
                type="submit"
                variant="primary"
                size="sm"
                isDisabled={isSavingSandbox || !isSandboxDirty}
                className="rounded-xl font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {isSavingSandbox ? 'Saving...' : t('common.save')}
              </Button>
            </div>
          )}
        </div>

        <div className="p-6 flex flex-col gap-4">
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 flex items-center gap-1.5">
                <Play size={12} className="text-teal-600 dark:text-teal-400" />
                <span>Default Run Command</span>
              </label>
              <input
                type="text"
                value={sandboxRunCmd}
                disabled={!canManage}
                onChange={(e) => setSandboxRunCmd(e.target.value)}
                placeholder="e.g. python main.py, npm run build, ./gradlew assembleDebug"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 flex items-center gap-1.5">
                <FlaskConical
                  size={12}
                  className="text-teal-600 dark:text-teal-400"
                />
                <span>Default Test Command (Used by Run Tab &amp; @reviewer)</span>
              </label>
              <input
                type="text"
                value={sandboxTestCmd}
                disabled={!canManage}
                onChange={(e) => setSandboxTestCmd(e.target.value)}
                placeholder="e.g. pytest -v, npm test, ./gradlew test"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Stack Container Image
              </label>
              <input
                type="text"
                list="project-settings-sandbox-images"
                value={sandboxImage}
                disabled={!canManage}
                onChange={(e) => setSandboxImage(e.target.value)}
                placeholder="e.g. python:3.12-slim, node:22-bookworm-slim"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
              <datalist id="project-settings-sandbox-images">
                <option value="python:3.12-slim">Python 3.12</option>
                <option value="node:22-bookworm-slim">Node.js 22</option>
                <option value="eclipse-temurin:21-jdk">JDK 21 / Gradle</option>
                <option value="golang:1.23-alpine">Go 1.23</option>
                <option value="rust:1.82-slim">Rust 1.82</option>
              </datalist>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Setup Command (Optional)
              </label>
              <input
                type="text"
                value={sandboxSetupCmd}
                disabled={!canManage}
                onChange={(e) => setSandboxSetupCmd(e.target.value)}
                placeholder="e.g. pip install -q -r requirements.txt"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Artifact Globs (Comma-Separated)
              </label>
              <input
                type="text"
                value={sandboxGlobsInput}
                disabled={!canManage}
                onChange={(e) => setSandboxGlobsInput(e.target.value)}
                placeholder="e.g. **/*.apk, dist/*, *.whl"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-4 pt-2 border-t border-zinc-200/70 dark:border-zinc-800">
            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
              <input
                type="checkbox"
                checked={requireTestsPass}
                disabled={!canManage}
                onChange={(e) => setRequireTestsPass(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Deterministic Supervisor Test Gate
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Enforce passing <code className="font-mono">test_command</code> in container supervisor before Execute &amp; Review succeed
                </span>
              </div>
            </label>

            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
              <input
                type="checkbox"
                checked={enableDependencyCache}
                disabled={!canManage}
                onChange={(e) => setEnableDependencyCache(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Warm Dependency Cache Volume
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Mount per-project package cache (<code className="font-mono">uv</code>, <code className="font-mono">pip</code>, <code className="font-mono">npm</code>, <code className="font-mono">pnpm</code>, <code className="font-mono">go</code>) across runs
                </span>
              </div>
            </label>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Protected / Blocked File Paths
              </label>
              <input
                type="text"
                value={blockedPathsInput}
                disabled={!canManage}
                onChange={(e) => setBlockedPathsInput(e.target.value)}
                placeholder="e.g. .github/workflows/*, *.env*, infra/**"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-4 gap-4 pt-2 border-t border-zinc-200/70 dark:border-zinc-800">
            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
              <input
                type="checkbox"
                checked={batchExecuteTasks}
                disabled={!canManage}
                onChange={(e) => setBatchExecuteTasks(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Batch Sequential Tasks
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Execute sequential pending tasks inside the same warm container &amp; agent session (<code className="font-mono">--continue</code>)
                </span>
              </div>
            </label>

            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
              <input
                type="checkbox"
                checked={enableWarmWorkspace}
                disabled={!canManage}
                onChange={(e) => setEnableWarmWorkspace(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Warm Seed Workspace Volume
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Reuse <code className="font-mono">/workspace</code> per seed with incremental <code className="font-mono">git fetch</code> instead of cold cloning
                </span>
              </div>
            </label>

            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
              <input
                type="checkbox"
                checked={strictCliExecution}
                disabled={!canManage}
                onChange={(e) => setStrictCliExecution(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Strict CLI Execution Mode
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Disable single-shot gateway synthesis fallback so CLI errors fail fast
                </span>
              </div>
            </label>

            <label className="flex items-start gap-2.5 p-3 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
              <input
                type="checkbox"
                checked={stageSeedsByDefault}
                disabled={!canManage}
                onChange={(e) => setStageSeedsByDefault(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Stage New Seeds by Default
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Create new seeds in Staged status so a human must click Start Work to initiate execution
                </span>
              </div>
            </label>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Git Clone Depth (0 = Full)
              </label>
              <input
                type="number"
                min={0}
                max={10000}
                value={gitCloneDepth}
                disabled={!canManage}
                onChange={(e) => setGitCloneDepth(e.target.value)}
                placeholder="50 (or 0 for full history)"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>
          </div>
        </div>
      </form>

      {/* 4. Git Webhooks & PR Automation */}
      <ProjectWebhooksCard project={project} canManage={canManage} />

      {/* 3. Spend & Concurrency Overrides */}
      <form
        id="project-settings-budget"
        onSubmit={handleSaveBudgetOverrides}
        className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 rounded-2xl shadow-xs overflow-hidden"
      >
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
              <DollarSign size={16} />
            </div>
            <div>
              <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
                Spend & Concurrency Overrides
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Leave blank to inherit organization-wide defaults configured in Settings
              </p>
            </div>
          </div>
          {canManage && (
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isDisabled={isSavingBudget || !isBudgetDirty}
              className="rounded-xl font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
            >
              {isSavingBudget ? 'Saving...' : t('common.save')}
            </Button>
          )}
        </div>

        <div className="divide-y divide-zinc-200 dark:divide-zinc-700/80">
          {/* Monthly Budget */}
          <div className="px-6 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Monthly Project Budget ($ USD)
                </span>
                {monthlyBudget.trim() !== '' ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                    Overridden
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80">
                    Org default: ${parseFloat(orgMonthlyDefault).toFixed(0)}/mo
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                Monthly inference spend cap across all seeds in this project
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {monthlyBudget.trim() !== '' && canManage && (
                <button
                  type="button"
                  onClick={() => setMonthlyBudget('')}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                >
                  <RotateCcw size={12} />
                  <span>Reset</span>
                </button>
              )}
              <div className="relative flex items-center">
                <span className="absolute left-3 text-xs font-mono text-zinc-400 pointer-events-none">
                  $
                </span>
                <input
                  type="number"
                  min="0"
                  step="1"
                  disabled={!canManage}
                  value={monthlyBudget}
                  onChange={(e) => setMonthlyBudget(e.target.value)}
                  placeholder={parseFloat(orgMonthlyDefault).toFixed(0)}
                  className="w-28 pl-6 pr-3 py-1.5 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                />
              </div>
            </div>
          </div>

          {/* Per-Seed Budget Cap */}
          <div className="px-6 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Per-Seed Budget Cap ($ USD)
                </span>
                {seedBudgetCap.trim() !== '' ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                    Overridden
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80">
                    Org default: ${parseFloat(orgSeedCapDefault).toFixed(2)}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                Hard circuit breaker ceiling for each individual seed unless raised
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {seedBudgetCap.trim() !== '' && canManage && (
                <button
                  type="button"
                  onClick={() => setSeedBudgetCap('')}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                >
                  <RotateCcw size={12} />
                  <span>Reset</span>
                </button>
              )}
              <div className="relative flex items-center">
                <span className="absolute left-3 text-xs font-mono text-zinc-400 pointer-events-none">
                  $
                </span>
                <input
                  type="number"
                  min="0.01"
                  step="0.5"
                  disabled={!canManage}
                  value={seedBudgetCap}
                  onChange={(e) => setSeedBudgetCap(e.target.value)}
                  placeholder={parseFloat(orgSeedCapDefault).toFixed(0)}
                  className="w-28 pl-6 pr-3 py-1.5 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                />
              </div>
            </div>
          </div>

          {/* Spend Checkpoint */}
          <div className="px-6 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Spend Checkpoint ($ USD)
                </span>
                {spendCheckpoint.trim() !== '' ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                    Overridden
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80">
                    Org default: ${parseFloat(orgCheckpointDefault).toFixed(2)}
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                Pauses autonomous execution for operator confirmation at each increment
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {spendCheckpoint.trim() !== '' && canManage && (
                <button
                  type="button"
                  onClick={() => setSpendCheckpoint('')}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                >
                  <RotateCcw size={12} />
                  <span>Reset</span>
                </button>
              )}
              <div className="relative flex items-center">
                <span className="absolute left-3 text-xs font-mono text-zinc-400 pointer-events-none">
                  $
                </span>
                <input
                  type="number"
                  min="0.01"
                  step="0.5"
                  disabled={!canManage}
                  value={spendCheckpoint}
                  onChange={(e) => setSpendCheckpoint(e.target.value)}
                  placeholder={parseFloat(orgCheckpointDefault).toFixed(0)}
                  className="w-28 pl-6 pr-3 py-1.5 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                />
              </div>
            </div>
          </div>

          {/* Max Concurrent Runs */}
          <div className="px-6 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
            <div>
              <div className="flex items-center gap-2">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Max Concurrent Runs
                </span>
                {maxConcurrentRuns.trim() !== '' ? (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                    Overridden
                  </span>
                ) : (
                  <span className="px-1.5 py-0.5 rounded text-[10px] font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700/80">
                    Org default: {orgConcurrencyDefault} slots
                  </span>
                )}
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                Maximum parallel container runners active simultaneously for this project
              </p>
            </div>
            <div className="flex items-center gap-2 shrink-0">
              {maxConcurrentRuns.trim() !== '' && canManage && (
                <button
                  type="button"
                  onClick={() => setMaxConcurrentRuns('')}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                >
                  <RotateCcw size={12} />
                  <span>Reset</span>
                </button>
              )}
              <input
                type="number"
                min="0"
                max="64"
                step="1"
                disabled={!canManage}
                value={maxConcurrentRuns}
                onChange={(e) => setMaxConcurrentRuns(e.target.value)}
                placeholder={String(orgConcurrencyDefault)}
                className="w-28 px-3 py-1.5 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
              />
            </div>
          </div>
        </div>
      </form>

      {/* 4. Backend Access Boundary & 5. Danger Zone */}
      <ProjectAccessAndDangerCards
        project={project}
        backends={backends}
        canManage={canManage}
        confirmArchiveOpen={confirmArchiveOpen}
        setConfirmArchiveOpen={setConfirmArchiveOpen}
        onToggleBackendsRestricted={handleToggleBackendsRestricted}
        onToggleAllowedBackend={handleToggleAllowedBackend}
        onArchiveOrRestore={handleArchiveOrRestore}
      />
    </div>
  )
}
