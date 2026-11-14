import React, {
  useCallback,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
} from 'react'
import { createPortal } from 'react-dom'
import {
  ATTACHMENT_LIMITS,
  filesFromClipboard,
  formatFileSize,
  validateAttachments,
} from '../seeds/AttachmentPicker'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  AlertTriangle,
  Check,
  ChevronDown,
  GitBranch,
  Paperclip,
  Plus,
  Trash2,
  X,
  Zap,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api, ApiError } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { Modal } from '../common/Modal'
import { Tooltip } from '../common/Tooltip'
import { VoiceDictationButton } from '../common/VoiceDictationButton'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { filterStageAgentsForStage } from '../../utils/agents'
import { canUserContributeToProject } from '../../utils/projects'
import { AUTONOMY_LABELS } from '../../utils/seeds'
import type { Seed, SeedAutonomy } from '../../types'

export interface SeedModalProps {
  isOpen: boolean
  onClose: () => void
  seed?: Seed | null
  defaultProjectId?: string | null
  relatedToSeed?: Seed | null
  onSaved?: (seed: Seed) => void
}

type OpenMenuId = 'project' | 'track' | 'autonomy' | 'opinions' | 'submit' | null

const DialogPopover: React.FC<{
  anchorRef: React.RefObject<HTMLElement | null>
  align?: 'left' | 'right'
  direction?: 'down' | 'up'
  className?: string
  children: React.ReactNode
}> = ({
  anchorRef,
  align = 'left',
  direction = 'down',
  className = '',
  children,
}) => {
  const [portalState, setPortalState] = useState<{
    container: HTMLElement
    style: React.CSSProperties
  } | null>(null)

  const updatePosition = useCallback(() => {
    const triggerEl = anchorRef.current
    if (!triggerEl) return
    const dialogEl =
      (triggerEl.closest('[role="dialog"]') as HTMLElement | null) ||
      document.body
    const triggerRect = triggerEl.getBoundingClientRect()
    const dialogRect = dialogEl.getBoundingClientRect()

    const style: React.CSSProperties = {
      position: dialogEl === document.body ? 'fixed' : 'absolute',
      zIndex: 9999,
    }

    if (direction === 'up') {
      style.bottom = `${dialogRect.bottom - triggerRect.top + 6}px`
    } else {
      style.top = `${triggerRect.bottom - dialogRect.top + 6}px`
    }

    if (align === 'right') {
      style.right = `${dialogRect.right - triggerRect.right}px`
    } else {
      style.left = `${triggerRect.left - dialogRect.left}px`
    }

    setPortalState({ container: dialogEl, style })
  }, [anchorRef, align, direction])

  useLayoutEffect(() => {
    updatePosition()
  }, [updatePosition])

  useEffect(() => {
    window.addEventListener('scroll', updatePosition, true)
    window.addEventListener('resize', updatePosition)
    return () => {
      window.removeEventListener('scroll', updatePosition, true)
      window.removeEventListener('resize', updatePosition)
    }
  }, [updatePosition])

  if (!portalState) return null

  return createPortal(
    <div data-seed-menu style={portalState.style} className={className}>
      {children}
    </div>,
    portalState.container
  )
}

const WORKFLOW_TRACK_OPTIONS: Array<{
  id: 'sdlc' | 'fast_track'
  label: string
  description: string
}> = [
  {
    id: 'sdlc',
    label: 'Full SDLC',
    description: 'Ideate → Plan → Execute → Review → Merge with spec and plan gates',
  },
  {
    id: 'fast_track',
    label: 'Fast-Track',
    description: 'Skips Ideate and Plan; @coder starts coding directly in /workspace',
  },
]

const AUTONOMY_MENU_OPTIONS: Array<{
  id: SeedAutonomy
  label: string
  description: string
}> = [
  {
    id: 'manual',
    label: 'Manual',
    description: 'Pauses at every stage, even ones your policy would approve automatically',
  },
  {
    id: 'supervised',
    label: 'Supervised',
    description: 'Pauses at each gate your policy requires',
  },
  {
    id: 'autonomous',
    label: 'Autonomous',
    description: 'Skips auto-approvable gates; gates that always require a person still stop',
  },
]

function deriveTitleFromText(rawText: string): string {
  const cleaned = rawText.trim()
  if (!cleaned) return ''

  const firstLine =
    cleaned
      .split(/\r?\n/)
      .map((l) => l.trim().replace(/^#+\s*/, ''))
      .find(Boolean) || cleaned

  const sentenceMatch = firstLine.match(/^(.+?[.!?])(?:\s+|$)/)
  const candidate =
    sentenceMatch && sentenceMatch[1].length <= 90
      ? sentenceMatch[1].replace(/[.!?]+$/, '').trim()
      : firstLine

  if (candidate.length <= 78) return candidate
  const truncated = candidate.slice(0, 78)
  const lastSpace = truncated.lastIndexOf(' ')
  return `${lastSpace > 40 ? truncated.slice(0, lastSpace) : truncated}...`
}

export const SeedModal: React.FC<SeedModalProps> = ({
  isOpen,
  onClose,
  seed = null,
  defaultProjectId = null,
  relatedToSeed = null,
  onSaved,
}) => {
  const queryClient = useQueryClient()
  const { user } = useAuth()
  const {
    projects,
    projectReposByProject,
    selectedProject,
    orgSettings,
    policies,
    projectPoliciesByProject,
    agents,
  } = useData()

  const isEditing = Boolean(seed)
  const isSpecLocked = Boolean(
    isEditing &&
      seed &&
      (seed.stage !== 'ideate' ||
        seed.status === 'merged' ||
        seed.status === 'closed')
  )

  const writableProjects = useMemo(
    () =>
      projects.filter(
        (p) => !p.archived_at && canUserContributeToProject(user, p)
      ),
    [projects, user]
  )

  const initialProjectId =
    seed?.project_id ||
    relatedToSeed?.project_id ||
    defaultProjectId ||
    (selectedProject && writableProjects.some((p) => p.id === selectedProject)
      ? selectedProject
      : writableProjects[0]?.id || '')

  const [projectId, setProjectId] = useState(initialProjectId)
  const [linkedParentSeed, setLinkedParentSeed] = useState<Seed | null>(
    !seed ? relatedToSeed : null
  )
  const [includeParentSummary, setIncludeParentSummary] = useState(false)
  const [title, setTitle] = useState(seed?.title || '')
  const [description, setDescription] = useState(seed?.description || '')
  const [specification, setSpecification] = useState(seed?.specification || '')
  const [criteria, setCriteria] = useState<string[]>(
    seed?.acceptance_criteria ? [...seed.acceptance_criteria] : []
  )
  const [criterionInput, setCriterionInput] = useState('')
  const [autonomy, setAutonomy] = useState<SeedAutonomy>(
    seed?.autonomy || orgSettings?.default_autonomy || 'supervised'
  )
  const [budgetCap, setBudgetCap] = useState<string>(
    seed?.budget_cap !== null && seed?.budget_cap !== undefined
      ? String(seed.budget_cap)
      : ''
  )
  const [tags, setTags] = useState<string[]>(seed?.tags ? [...seed.tags] : [])
  const [tagDraft, setTagDraft] = useState('')
  const [extraIdeateAgentIds, setExtraIdeateAgentIds] = useState<string[]>([])
  const [fastTrack, setFastTrack] = useState<boolean>(false)
  const [showAdvanced, setShowAdvanced] = useState<boolean>(Boolean(seed))
  const [openMenu, setOpenMenu] = useState<OpenMenuId>(null)

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [files, setFiles] = useState<File[]>([])
  const [draggingFiles, setDraggingFiles] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [createdSeedForRetry, setCreatedSeedForRetry] = useState<Seed | null>(
    null
  )
  const [linkedParentForRetry, setLinkedParentForRetry] =
    useState<boolean>(false)
  const fileInputRef = useRef<HTMLInputElement | null>(null)
  const projectBtnRef = useRef<HTMLButtonElement | null>(null)
  const trackBtnRef = useRef<HTMLButtonElement | null>(null)
  const autonomyBtnRef = useRef<HTMLButtonElement | null>(null)
  const opinionsBtnRef = useRef<HTMLButtonElement | null>(null)
  const submitBtnRef = useRef<HTMLButtonElement | null>(null)

  const selectedProj = useMemo(
    () => projects.find((p) => p.id === projectId) || null,
    [projects, projectId]
  )

  const defaultStagedForProject = useMemo(() => {
    if (
      selectedProj?.sandbox_config?.stage_seeds_by_default !== undefined &&
      selectedProj?.sandbox_config?.stage_seeds_by_default !== null
    ) {
      return Boolean(selectedProj.sandbox_config.stage_seeds_by_default)
    }
    return Boolean(orgSettings?.default_stage_seeds)
  }, [selectedProj, orgSettings])

  const [staged, setStaged] = useState<boolean>(defaultStagedForProject)

  // Reset form state when modal opens or target seed/project changes
  useEffect(() => {
    if (!isOpen) {
      setOpenMenu(null)
      return
    }
    const nextProjectId =
      seed?.project_id ||
      relatedToSeed?.project_id ||
      defaultProjectId ||
      (selectedProject && writableProjects.some((p) => p.id === selectedProject)
        ? selectedProject
        : writableProjects[0]?.id || '')
    setProjectId(nextProjectId)
    setLinkedParentSeed(!seed ? relatedToSeed : null)
    setIncludeParentSummary(false)
    setTitle(seed?.title || '')
    setDescription(seed?.description || '')
    setSpecification(seed?.specification || '')
    setCriteria(seed?.acceptance_criteria ? [...seed.acceptance_criteria] : [])
    setCriterionInput('')
    setAutonomy(seed?.autonomy || orgSettings?.default_autonomy || 'supervised')
    setBudgetCap(
      seed?.budget_cap !== null && seed?.budget_cap !== undefined
        ? String(seed.budget_cap)
        : ''
    )
    setTags(seed?.tags ? [...seed.tags] : [])
    setTagDraft('')
    setExtraIdeateAgentIds([])
    setFastTrack(false)
    setShowAdvanced(Boolean(seed))
    setFiles([])
    setDraggingFiles(false)
    setError(null)
    setCreatedSeedForRetry(null)
    setLinkedParentForRetry(false)
    setOpenMenu(null)
  }, [
    isOpen,
    seed,
    relatedToSeed,
    defaultProjectId,
    selectedProject,
    writableProjects,
    orgSettings?.default_autonomy,
  ])

  useEffect(() => {
    if (!isEditing) {
      setStaged(defaultStagedForProject)
    }
  }, [defaultStagedForProject, isEditing])

  // Close open dropdown menu on outside click
  useEffect(() => {
    if (!openMenu) return
    const handlePointerDown = (e: MouseEvent) => {
      const target = e.target as HTMLElement | null
      if (!target?.closest('[data-seed-menu]')) {
        setOpenMenu(null)
      }
    }
    document.addEventListener('mousedown', handlePointerDown)
    return () => document.removeEventListener('mousedown', handlePointerDown)
  }, [openMenu])

  const addIncomingFiles = (incoming: File[]) => {
    if (incoming.length === 0) return
    const err = validateAttachments(files, incoming)
    if (err) {
      setError(err)
    } else {
      setError(null)
      setFiles((prev) => [...prev, ...incoming])
    }
  }

  const addPastedFiles = (e: React.ClipboardEvent) => {
    const pasted = filesFromClipboard(e)
    if (pasted.length === 0) return
    e.preventDefault()
    addIncomingFiles(pasted)
  }

  const ideatePolicy = useMemo(() => {
    const projPols = projectId ? projectPoliciesByProject[projectId] : undefined
    return (
      projPols?.find((p) => p.stage === 'ideate') ||
      policies.find((p) => p.stage === 'ideate') ||
      null
    )
  }, [projectId, projectPoliciesByProject, policies])

  const maxAcceptanceCriteria = useMemo(
    () => ideatePolicy?.max_acceptance_criteria ?? 6,
    [ideatePolicy]
  )

  const defaultIdeateAgent = useMemo(
    () =>
      ideatePolicy?.agent_id
        ? agents.find((a) => a.id === ideatePolicy.agent_id) || null
        : null,
    [agents, ideatePolicy]
  )

  const availableExtraIdeateAgents = useMemo(
    () =>
      filterStageAgentsForStage(
        agents.filter((a) => !a.archived_at && a.enabled && a.kind === 'stage'),
        'ideate',
        extraIdeateAgentIds
      ).filter((a) => a.id !== ideatePolicy?.agent_id),
    [agents, ideatePolicy, extraIdeateAgentIds]
  )

  // Second opinions apply to Ideate; hide when Fast-Track skips Ideate
  const canPickExtraIdeateAgents =
    !isEditing &&
    !fastTrack &&
    autonomy !== 'autonomous' &&
    ideatePolicy?.enabled !== false &&
    ideatePolicy?.allow_second_opinions !== false &&
    availableExtraIdeateAgents.length > 0

  const selectedExtraIdeateAgents = useMemo(
    () =>
      extraIdeateAgentIds
        .map((id) => availableExtraIdeateAgents.find((a) => a.id === id))
        .filter((a): a is NonNullable<typeof a> => Boolean(a)),
    [extraIdeateAgentIds, availableExtraIdeateAgents]
  )

  const toggleExtraIdeateAgent = (aid: string) => {
    setExtraIdeateAgentIds((prev) => {
      if (prev.includes(aid)) {
        return prev.filter((id) => id !== aid)
      }
      if (prev.length >= 2) {
        return prev
      }
      return [...prev, aid]
    })
  }

  const cachedProjectRepos = projectId
    ? projectReposByProject[projectId]
    : undefined

  const { data: fetchedProjectRepos } = useQuery({
    queryKey: queryKeys.projectRepos(projectId || '__none__'),
    queryFn: () => api.projects.listRepos(projectId),
    enabled: isOpen && Boolean(projectId) && cachedProjectRepos === undefined,
    staleTime: 300_000,
  })

  const resolvedProjectRepos = cachedProjectRepos ?? fetchedProjectRepos
  const selectedProjMissingRepos = useMemo(
    () =>
      Boolean(
        selectedProj &&
          resolvedProjectRepos !== undefined &&
          resolvedProjectRepos.length === 0
      ),
    [selectedProj, resolvedProjectRepos]
  )

  const budgetDefaultHint = useMemo(() => {
    if (selectedProj?.seed_budget_cap) {
      const val = parseFloat(selectedProj.seed_budget_cap)
      if (Number.isFinite(val) && val > 0) {
        return `Budget cap (default $${val.toFixed(2)})`
      }
    }
    const orgRaw = orgSettings?.default_seed_budget_cap
    if (orgRaw !== null && orgRaw !== undefined) {
      const val = parseFloat(String(orgRaw))
      if (Number.isFinite(val) && val > 0) {
        return `Budget cap (default $${val.toFixed(2)})`
      }
      return 'Budget cap ($ USD, optional)'
    }
    return 'Budget cap (default $15.00)'
  }, [selectedProj, orgSettings])

  const addTagChip = (raw: string) => {
    const cleaned = raw
      .split(',')
      .map((t) => t.trim().replace(/^#+/, ''))
      .filter(Boolean)
    if (cleaned.length === 0) return
    setTags((prev) => {
      const next = [...prev]
      for (const item of cleaned) {
        if (
          !next.some((existing) => existing.toLowerCase() === item.toLowerCase())
        ) {
          next.push(item)
        }
      }
      return next
    })
  }

  const removeTagChip = (idx: number) => {
    setTags((prev) => prev.filter((_, i) => i !== idx))
  }

  const canCreate = useMemo(() => {
    if (!projectId) return false
    return Boolean(title.trim() || description.trim() || specification.trim())
  }, [projectId, description, title, specification])

  const isDirty = useMemo(() => {
    if (!seed) return canCreate
    const origCap =
      seed.budget_cap !== null && seed.budget_cap !== undefined
        ? String(seed.budget_cap)
        : ''
    const pendingTag = tagDraft.trim().replace(/^#+/, '')
    const effectiveTags =
      pendingTag && !tags.includes(pendingTag) ? [...tags, pendingTag] : tags
    return (
      title.trim() !== seed.title ||
      description.trim() !== (seed.description || '') ||
      specification.trim() !== (seed.specification || '') ||
      JSON.stringify(criteria) !==
        JSON.stringify(seed.acceptance_criteria || []) ||
      autonomy !== seed.autonomy ||
      budgetCap.trim() !== origCap ||
      JSON.stringify(effectiveTags) !== JSON.stringify(seed.tags || []) ||
      files.length > 0
    )
  }, [
    seed,
    canCreate,
    title,
    description,
    specification,
    criteria,
    autonomy,
    budgetCap,
    tags,
    tagDraft,
    files.length,
  ])

  const hasCustomAdvancedValues = useMemo(
    () =>
      Boolean(
        title.trim() ||
          specification.trim() ||
          criteria.length > 0 ||
          criterionInput.trim() ||
          budgetCap.trim() ||
          tags.length > 0 ||
          tagDraft.trim()
      ),
    [title, specification, criteria.length, criterionInput, budgetCap, tags.length, tagDraft]
  )

  const handleAddCriterion = () => {
    const trimmed = criterionInput.trim()
    if (!trimmed) return
    setCriteria((prev) => [...prev, trimmed])
    setCriterionInput('')
  }

  const handleRemoveCriterion = (idx: number) => {
    setCriteria((prev) => prev.filter((_, i) => i !== idx))
  }

  const handleSubmit = async (
    e?: React.FormEvent,
    stagedOverride?: boolean
  ) => {
    e?.preventDefault()
    const effectiveStaged =
      stagedOverride !== undefined ? stagedOverride : staged

    const trimmedTitle = title.trim()
    const trimmedDesc = description.trim()
    const trimmedSpec = specification.trim()

    const resolvedTitle = isEditing
      ? trimmedTitle
      : trimmedTitle || deriveTitleFromText(trimmedDesc || trimmedSpec)

    if (!resolvedTitle) {
      setError(
        isEditing
          ? 'Seed title is required.'
          : 'Please describe what you want to build, fix, or change.'
      )
      return
    }
    if (!projectId) {
      setError('Please select a target project.')
      return
    }

    let parsedCap: number | null = null
    if (budgetCap.trim() !== '') {
      const num = parseFloat(budgetCap.trim())
      if (!Number.isFinite(num) || num <= 0) {
        setError('Budget cap must be a positive dollar amount.')
        return
      }
      parsedCap = num
    }

    const finalCriteria = criterionInput.trim()
      ? [...criteria, criterionInput.trim()]
      : criteria

    const pendingTag = tagDraft.trim().replace(/^#+/, '')
    const finalTags =
      pendingTag &&
      !tags.some((t) => t.toLowerCase() === pendingTag.toLowerCase())
        ? [...tags, pendingTag]
        : tags

    setIsSubmitting(true)
    setError(null)
    try {
      let saved: Seed
      const postSaveErrors: string[] = []
      if (isEditing && seed) {
        saved = await api.seeds.update(seed.id, {
          title: resolvedTitle,
          description: trimmedDesc,
          ...(isSpecLocked
            ? {}
            : {
                specification: trimmedSpec,
                acceptance_criteria: finalCriteria,
              }),
          tags: finalTags,
          autonomy,
          budget_cap: parsedCap,
        })
        if (files.length > 0) {
          const remainingFiles: File[] = []
          for (const f of files) {
            try {
              await api.seeds.uploadAttachment(saved.id, f)
            } catch (uploadErr) {
              remainingFiles.push(f)
              postSaveErrors.push(
                `Attachment "${f.name}" failed: ${
                  uploadErr instanceof ApiError
                    ? uploadErr.detail
                    : uploadErr instanceof Error
                      ? uploadErr.message
                      : 'upload failed'
                }`
              )
            }
          }
          setFiles(remainingFiles)
          await queryClient.invalidateQueries({
            queryKey: ['seedAttachments', saved.id],
          })
        }
      } else {
        const effectiveDesc =
          linkedParentSeed &&
          includeParentSummary &&
          linkedParentSeed.description
            ? `${trimmedDesc}\n\n---\nContext from follow-up parent #${linkedParentSeed.number} (${linkedParentSeed.title}):\n${linkedParentSeed.description.trim()}`
            : trimmedDesc
        const holdStart =
          effectiveStaged || files.length > 0 || Boolean(linkedParentSeed)
        if (createdSeedForRetry) {
          saved = createdSeedForRetry
        } else {
          saved = await api.seeds.create({
            project_id: projectId,
            title: resolvedTitle,
            description: effectiveDesc,
            specification: trimmedSpec,
            acceptance_criteria: finalCriteria,
            tags: finalTags,
            ...(fastTrack ? { stage: 'execute', fast_track: true } : {}),
            autonomy,
            budget_cap: parsedCap,
            staged: holdStart,
            ...(canPickExtraIdeateAgents && extraIdeateAgentIds.length > 0
              ? { ideate_agent_ids: extraIdeateAgentIds }
              : {}),
          })
          setCreatedSeedForRetry(saved)
        }
        if (linkedParentSeed && !linkedParentForRetry) {
          try {
            await api.seeds.addLink(saved.id, {
              target_seed_id: linkedParentSeed.id,
              kind: 'follow_up',
            })
            setLinkedParentForRetry(true)
            await Promise.all([
              queryClient.invalidateQueries({
                queryKey: ['seedLinks', saved.id],
              }),
              queryClient.invalidateQueries({
                queryKey: ['seedLinks', linkedParentSeed.id],
              }),
            ])
          } catch (linkErr) {
            postSaveErrors.push(
              `Linking follow-up seed #${linkedParentSeed.number} failed: ${
                linkErr instanceof ApiError
                  ? linkErr.detail
                  : linkErr instanceof Error
                    ? linkErr.message
                    : 'could not link seed'
              }`
            )
          }
        }
        if (files.length > 0) {
          const remainingFiles: File[] = []
          for (const f of files) {
            try {
              await api.seeds.uploadAttachment(saved.id, f)
            } catch (uploadErr) {
              remainingFiles.push(f)
              postSaveErrors.push(
                `Attachment "${f.name}" failed: ${
                  uploadErr instanceof ApiError
                    ? uploadErr.detail
                    : uploadErr instanceof Error
                      ? uploadErr.message
                      : 'upload failed'
                }`
              )
            }
          }
          setFiles(remainingFiles)
          await queryClient.invalidateQueries({
            queryKey: ['seedAttachments', saved.id],
          })
        }
        if (postSaveErrors.length === 0 && holdStart && !effectiveStaged) {
          saved = await api.seeds.start(saved.id)
        }
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.seeds }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.seedDetail(saved.id),
        }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])

      if (postSaveErrors.length > 0) {
        setError(postSaveErrors.join(' · '))
        return
      }

      onSaved?.(saved)
      onClose()
    } catch (err) {
      setError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to save seed.'
      )
    } finally {
      setIsSubmitting(false)
    }
  }

  const modalTitle = isEditing && seed ? (
    <span className="inline-flex items-center gap-2 flex-wrap">
      <span>Edit seed &middot; #{seed.number}</span>
      {selectedProj && (
        <span className="px-2.5 py-0.5 rounded-lg text-xs font-medium bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200/80 dark:border-zinc-700/80">
          {selectedProj.name}
        </span>
      )}
    </span>
  ) : (
    <span className="inline-flex items-center gap-2 flex-wrap">
      <span>
        {relatedToSeed
          ? `Create related seed · #${relatedToSeed.number} in`
          : 'Create a seed in'}
      </span>
      <div className="relative inline-block" data-seed-menu>
        <button
          ref={projectBtnRef}
          type="button"
          onClick={() =>
            setOpenMenu((prev) => (prev === 'project' ? null : 'project'))
          }
          aria-label="Select project"
          aria-expanded={openMenu === 'project'}
          className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-xl text-sm font-semibold bg-zinc-100 hover:bg-zinc-200/80 dark:bg-zinc-800 dark:hover:bg-zinc-700/80 text-zinc-900 dark:text-zinc-100 border border-zinc-200/80 dark:border-zinc-700/80 transition cursor-pointer"
        >
          <span className="truncate max-w-[220px]">
            {selectedProj?.name ||
              (writableProjects.length === 0
                ? 'No writable projects'
                : 'Select project')}
          </span>
          <ChevronDown size={14} className="text-zinc-400 shrink-0" />
        </button>

        {openMenu === 'project' && writableProjects.length > 0 && (
          <DialogPopover
            anchorRef={projectBtnRef}
            className="w-64 max-h-60 overflow-y-auto rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl py-1 font-normal"
          >
            {writableProjects.map((p) => {
              const active = p.id === projectId
              return (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => {
                    setProjectId(p.id)
                    setOpenMenu(null)
                  }}
                  className={`w-full px-3 py-2 text-left text-xs flex items-center justify-between gap-2 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer ${
                    active
                      ? 'text-teal-600 dark:text-teal-400 font-semibold bg-teal-500/5'
                      : 'text-zinc-800 dark:text-zinc-200'
                  }`}
                >
                  <span className="truncate">{p.name}</span>
                  {active && (
                    <Check
                      size={13}
                      className="shrink-0 text-teal-600 dark:text-teal-400"
                    />
                  )}
                </button>
              )
            })}
          </DialogPopover>
        )}
      </div>
    </span>
  )

  const primaryButtonLabel = isSubmitting
    ? 'Saving...'
    : isEditing
      ? 'Save Changes'
      : staged
        ? 'Create without starting'
        : 'Create & start'

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={modalTitle} size="3xl">
      <form onSubmit={(e) => void handleSubmit(e)} className="flex flex-col gap-4">
        {error && (
          <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
            <AlertCircle size={15} className="shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {selectedProj && selectedProjMissingRepos && (
          <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-700 dark:text-amber-300 font-medium flex items-start gap-2">
            <AlertCircle
              size={15}
              className="shrink-0 mt-0.5 text-amber-600 dark:text-amber-400"
            />
            <span>
              <strong>{selectedProj.name}</strong> has no target repository
              connected yet. This seed will remain{' '}
              <span className="font-mono font-semibold">queued</span> until a
              repository is linked to the project.
            </span>
          </div>
        )}

        {!isEditing && linkedParentSeed && (
          <div className="p-2.5 rounded-xl bg-teal-500/10 border border-teal-500/25 flex flex-wrap items-center justify-between gap-2 text-xs">
            <div className="flex items-center gap-2 min-w-0">
              <GitBranch
                size={14}
                className="text-teal-600 dark:text-teal-400 shrink-0"
              />
              <span className="font-semibold text-teal-800 dark:text-teal-200 truncate">
                Follow-up of{' '}
                <span className="font-mono">#{linkedParentSeed.number}</span>{' '}
                &middot; {linkedParentSeed.title}
              </span>
            </div>
            <div className="flex items-center gap-3 shrink-0">
              {linkedParentSeed.description && (
                <label className="inline-flex items-center gap-1.5 text-zinc-600 dark:text-zinc-300 cursor-pointer select-none">
                  <input
                    type="checkbox"
                    checked={includeParentSummary}
                    onChange={(e) => setIncludeParentSummary(e.target.checked)}
                    className="w-3.5 h-3.5 rounded border-zinc-300 dark:border-zinc-600 text-teal-600 focus:ring-teal-500/30 cursor-pointer"
                  />
                  <span>
                    Include #{linkedParentSeed.number} summary in prompt
                  </span>
                </label>
              )}
              <button
                type="button"
                onClick={() => setLinkedParentSeed(null)}
                title="Remove follow-up link"
                className="p-0.5 rounded text-zinc-400 hover:text-rose-500 cursor-pointer"
              >
                <X size={13} />
              </button>
            </div>
          </div>
        )}

        {/* Unified Prompt Box with integrated attachments (📎) and voice dictation (🎙) */}
        <div
          onDragOver={(e) => {
            e.preventDefault()
            if (!isSubmitting) setDraggingFiles(true)
          }}
          onDragLeave={() => setDraggingFiles(false)}
          onDrop={(e) => {
            e.preventDefault()
            setDraggingFiles(false)
            if (!isSubmitting) {
              addIncomingFiles(Array.from(e.dataTransfer.files || []))
            }
          }}
          className={`rounded-2xl bg-zinc-50 dark:bg-zinc-800/60 border transition flex flex-col focus-within:ring-2 focus-within:ring-teal-500/30 focus-within:border-teal-500 ${
            draggingFiles
              ? 'border-teal-500 bg-teal-500/5 border-dashed'
              : 'border-zinc-200 dark:border-zinc-700/80'
          }`}
        >
          <textarea
            rows={5}
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === 'Enter') {
                e.preventDefault()
                if (canCreate && !isSubmitting) {
                  void handleSubmit()
                }
              }
            }}
            autoFocus={!isEditing}
            onPaste={addPastedFiles}
            placeholder="Describe what you want to build, fix or change…"
            className="w-full px-4 pt-3.5 pb-2 text-sm leading-relaxed bg-transparent text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none resize-y rounded-t-2xl"
          />

          {files.length > 0 && (
            <div className="px-3.5 pb-2 flex flex-wrap items-center gap-1.5">
              {files.map((f, i) => (
                <span
                  key={`${f.name}-${i}`}
                  className="inline-flex items-center gap-1.5 max-w-[240px] pl-2 pr-1 py-0.5 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-xs text-zinc-700 dark:text-zinc-200"
                >
                  <span className="truncate">{f.name}</span>
                  <span className="text-zinc-400 shrink-0">
                    {formatFileSize(f.size)}
                  </span>
                  <button
                    type="button"
                    disabled={isSubmitting}
                    onClick={() =>
                      setFiles((prev) => prev.filter((_, j) => j !== i))
                    }
                    className="p-0.5 rounded text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                    aria-label={`Remove ${f.name}`}
                  >
                    <X size={11} />
                  </button>
                </span>
              ))}
            </div>
          )}

          <div className="px-3 pb-2.5 pt-1 flex items-center justify-between gap-2">
            <div className="flex items-center gap-1">
              <Tooltip content={`Attach files (up to ${ATTACHMENT_LIMITS.maxFiles}, or drop / paste)`}>
                <button
                  type="button"
                  disabled={isSubmitting}
                  onClick={() => fileInputRef.current?.click()}
                  aria-label="Attach files"
                  className="w-6 h-6 inline-flex items-center justify-center rounded-lg text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer disabled:opacity-50"
                >
                  <Paperclip size={13} />
                </button>
              </Tooltip>
              <VoiceDictationButton
                onTranscript={(spoken) =>
                  setDescription((prev) =>
                    prev.trim() ? `${prev.trim()} ${spoken}` : spoken
                  )
                }
              />
              <input
                ref={fileInputRef}
                type="file"
                multiple
                className="hidden"
                onChange={(e) => {
                  addIncomingFiles(Array.from(e.target.files || []))
                  e.target.value = ''
                }}
              />
            </div>
          </div>
        </div>

        {/* Single Compact Row of Defaults + Advanced Toggle */}
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-2">
            {/* Workflow Track Dropdown Chip */}
            {!isEditing && (
              <div className="relative" data-seed-menu>
                <button
                  ref={trackBtnRef}
                  type="button"
                  onClick={() =>
                    setOpenMenu((prev) => (prev === 'track' ? null : 'track'))
                  }
                  aria-label="Workflow track"
                  aria-expanded={openMenu === 'track'}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-100/90 hover:bg-zinc-200/70 dark:bg-zinc-800/80 dark:hover:bg-zinc-700/70 text-zinc-700 dark:text-zinc-200 border border-zinc-200/80 dark:border-zinc-700/80 transition cursor-pointer"
                >
                  {fastTrack && (
                    <Zap size={12} className="text-amber-500 shrink-0" />
                  )}
                  <span>{fastTrack ? 'Fast-Track' : 'Full SDLC'}</span>
                  <ChevronDown size={13} className="text-zinc-400 shrink-0" />
                </button>

                {openMenu === 'track' && (
                  <DialogPopover
                    anchorRef={trackBtnRef}
                    className="w-72 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl py-1.5"
                  >
                    {WORKFLOW_TRACK_OPTIONS.map((opt) => {
                      const active =
                        (opt.id === 'fast_track') === Boolean(fastTrack)
                      return (
                        <button
                          key={opt.id}
                          type="button"
                          onClick={() => {
                            const nextFastTrack = opt.id === 'fast_track'
                            setFastTrack(nextFastTrack)
                            if (nextFastTrack) {
                              setExtraIdeateAgentIds([])
                            }
                            setOpenMenu(null)
                          }}
                          className="w-full px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 transition flex items-start justify-between gap-2 cursor-pointer"
                        >
                          <div className="flex flex-col gap-0.5 min-w-0">
                            <span
                              className={`text-xs font-semibold flex items-center gap-1.5 ${
                                active
                                  ? 'text-teal-600 dark:text-teal-400'
                                  : 'text-zinc-900 dark:text-zinc-100'
                              }`}
                            >
                              {opt.id === 'fast_track' && (
                                <Zap size={12} className="text-amber-500" />
                              )}
                              <span>{opt.label}</span>
                            </span>
                            <span className="text-xs text-zinc-500 dark:text-zinc-400 leading-snug">
                              {opt.description}
                            </span>
                          </div>
                          {active && (
                            <Check
                              size={14}
                              className="shrink-0 mt-0.5 text-teal-600 dark:text-teal-400"
                            />
                          )}
                        </button>
                      )
                    })}
                  </DialogPopover>
                )}
              </div>
            )}

            {/* Autonomy Mode Dropdown Chip */}
            <div className="relative" data-seed-menu>
              <button
                ref={autonomyBtnRef}
                type="button"
                onClick={() =>
                  setOpenMenu((prev) =>
                    prev === 'autonomy' ? null : 'autonomy'
                  )
                }
                aria-label="Autonomy mode"
                aria-expanded={openMenu === 'autonomy'}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-100/90 hover:bg-zinc-200/70 dark:bg-zinc-800/80 dark:hover:bg-zinc-700/70 text-zinc-700 dark:text-zinc-200 border border-zinc-200/80 dark:border-zinc-700/80 transition cursor-pointer"
              >
                <span>{AUTONOMY_LABELS[autonomy]}</span>
                <ChevronDown size={13} className="text-zinc-400 shrink-0" />
              </button>

              {openMenu === 'autonomy' && (
                <DialogPopover
                  anchorRef={autonomyBtnRef}
                  className="w-72 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl py-1.5"
                >
                  {AUTONOMY_MENU_OPTIONS.map((opt) => {
                    const active = opt.id === autonomy
                    return (
                      <button
                        key={opt.id}
                        type="button"
                        onClick={() => {
                          setAutonomy(opt.id)
                          setOpenMenu(null)
                        }}
                        className="w-full px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 transition flex items-start justify-between gap-2 cursor-pointer"
                      >
                        <div className="flex flex-col gap-0.5 min-w-0">
                          <span
                            className={`text-xs font-semibold ${
                              active
                                ? 'text-teal-600 dark:text-teal-400'
                                : 'text-zinc-900 dark:text-zinc-100'
                            }`}
                          >
                            {opt.label}
                          </span>
                          <span className="text-xs text-zinc-500 dark:text-zinc-400 leading-snug">
                            {opt.description}
                          </span>
                        </div>
                        {active && (
                          <Check
                            size={14}
                            className="shrink-0 mt-0.5 text-teal-600 dark:text-teal-400"
                          />
                        )}
                      </button>
                    )
                  })}
                </DialogPopover>
              )}
            </div>

            {/* Second Opinions: Removable Chosen Agent Chips + "+ Second opinion" Picker (Hidden when Fast-Track is selected) */}
            {canPickExtraIdeateAgents && (
              <>
                {selectedExtraIdeateAgents.map((ag) => (
                  <span
                    key={ag.id}
                    className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-medium bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/30"
                  >
                    <span className="font-mono">@{ag.handle}</span>
                    <button
                      type="button"
                      onClick={() => toggleExtraIdeateAgent(ag.id)}
                      aria-label={`Remove @${ag.handle}`}
                      className="text-teal-600/70 dark:text-teal-300/70 hover:text-rose-500 transition cursor-pointer"
                    >
                      <X size={12} />
                    </button>
                  </span>
                ))}

                <div className="relative" data-seed-menu>
                  <button
                    ref={opinionsBtnRef}
                    type="button"
                    onClick={() =>
                      setOpenMenu((prev) =>
                        prev === 'opinions' ? null : 'opinions'
                      )
                    }
                    aria-label="Second opinion agents"
                    aria-expanded={openMenu === 'opinions'}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 rounded-lg text-xs font-medium bg-zinc-100/90 hover:bg-zinc-200/70 dark:bg-zinc-800/80 dark:hover:bg-zinc-700/70 text-zinc-600 dark:text-zinc-300 border border-zinc-200/80 dark:border-zinc-700/80 transition cursor-pointer"
                  >
                    <Plus size={12} className="text-zinc-400 shrink-0" />
                    <span>Second opinion</span>
                  </button>

                  {openMenu === 'opinions' && (
                    <DialogPopover
                      anchorRef={opinionsBtnRef}
                      className="w-72 max-h-64 overflow-y-auto rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl py-1.5"
                    >
                      <div className="px-3 py-1.5 border-b border-zinc-100 dark:border-zinc-800 flex items-center justify-between gap-2 text-xs text-zinc-400">
                        <span>
                          {defaultIdeateAgent
                            ? `Primary: @${defaultIdeateAgent.handle}`
                            : 'Ideate second opinions'}
                        </span>
                        <span>Up to 2</span>
                      </div>
                      {availableExtraIdeateAgents.map((ag) => {
                        const isSelected = extraIdeateAgentIds.includes(ag.id)
                        const atCap =
                          !isSelected && extraIdeateAgentIds.length >= 2
                        return (
                          <button
                            key={ag.id}
                            type="button"
                            disabled={atCap}
                            onClick={() => toggleExtraIdeateAgent(ag.id)}
                            className={`w-full px-3 py-2 text-left flex items-center justify-between gap-2 transition ${
                              atCap
                                ? 'opacity-45 cursor-not-allowed'
                                : 'hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer'
                            }`}
                          >
                            <div className="flex flex-col min-w-0">
                              <span
                                className={`text-xs font-mono font-medium truncate ${
                                  isSelected
                                    ? 'text-teal-600 dark:text-teal-400 font-semibold'
                                    : 'text-zinc-800 dark:text-zinc-200'
                                }`}
                              >
                                @{ag.handle}
                              </span>
                              <span className="text-xs text-zinc-400 truncate">
                                {ag.model}
                              </span>
                            </div>
                            {isSelected && (
                              <Check
                                size={14}
                                className="shrink-0 text-teal-600 dark:text-teal-400"
                              />
                            )}
                          </button>
                        )
                      })}
                    </DialogPopover>
                  )}
                </div>
              </>
            )}
          </div>

          {!isEditing && (
            <button
              type="button"
              onClick={() => setShowAdvanced((prev) => !prev)}
              className="text-xs font-medium text-zinc-500 hover:text-zinc-900 dark:text-zinc-400 dark:hover:text-zinc-100 transition cursor-pointer inline-flex items-center gap-1.5 ml-auto"
            >
              <span>Advanced</span>
              {hasCustomAdvancedValues && (
                <span
                  className="w-1.5 h-1.5 rounded-full bg-teal-500"
                  aria-hidden="true"
                />
              )}
            </button>
          )}
        </div>

        {/* In-Place Expanded Advanced Fields (no outer bordered boxes or uppercase labels) */}
        {(showAdvanced || isEditing) && (
          <div className="flex flex-col gap-3 pt-1">
            {/* Custom Title */}
            <div className="relative flex items-center">
              <input
                type="text"
                value={title}
                onChange={(e) => setTitle(e.target.value)}
                placeholder={
                  isEditing
                    ? 'Seed title'
                    : 'Custom title (optional, leave blank and @ideator will name it)'
                }
                autoFocus={isEditing}
                required={isEditing}
                className="w-full h-10 pl-3.5 pr-9 text-sm rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500"
              />
              <div className="absolute right-2">
                <VoiceDictationButton
                  continuous={false}
                  onTranscript={(spoken) =>
                    setTitle((prev) =>
                      prev.trim() ? `${prev.trim()} ${spoken}` : spoken
                    )
                  }
                />
              </div>
            </div>

            {/* Specification */}
            <div className="relative flex flex-col">
              <textarea
                rows={isEditing ? 7 : 4}
                value={specification}
                onChange={(e) => setSpecification(e.target.value)}
                onPaste={addPastedFiles}
                disabled={isSpecLocked}
                placeholder={
                  isSpecLocked
                    ? 'Specification (locked after Ideate approval)'
                    : 'Technical specification, architecture, or constraints (Markdown, optional)…'
                }
                className={`w-full px-3.5 py-2.5 text-xs sm:text-sm font-mono leading-relaxed rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500 resize-y ${
                  isSpecLocked ? 'opacity-65 cursor-not-allowed' : ''
                }`}
              />
              {!isSpecLocked && (
                <div className="absolute right-2 bottom-2">
                  <VoiceDictationButton
                    onTranscript={(spoken) =>
                      setSpecification((prev) =>
                        prev.trim() ? `${prev.trim()} ${spoken}` : spoken
                      )
                    }
                  />
                </div>
              )}
            </div>

            {/* Acceptance Criteria */}
            <div className="flex flex-col gap-2">
              {!isSpecLocked && (
                <div className="flex items-center gap-2">
                  <input
                    type="text"
                    value={criterionInput}
                    onChange={(e) => setCriterionInput(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        handleAddCriterion()
                      }
                    }}
                    placeholder="Add acceptance criterion and press Enter…"
                    className="flex-1 h-9 px-3.5 text-xs sm:text-sm rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                  <Button
                    type="button"
                    variant="secondary"
                    size="sm"
                    onClick={handleAddCriterion}
                    isDisabled={!criterionInput.trim()}
                    className="h-9 px-3 rounded-xl text-xs font-semibold flex items-center gap-1 shrink-0 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
                  >
                    <Plus size={13} />
                    <span>Add</span>
                  </Button>
                </div>
              )}

              {criteria.length > 0 && (
                <div className="flex flex-col divide-y divide-zinc-200/70 dark:divide-zinc-700/60 rounded-xl border border-zinc-200 dark:border-zinc-700/80 overflow-hidden bg-zinc-50/50 dark:bg-zinc-800/30">
                  {criteria.map((item, idx) => (
                    <div
                      key={`${idx}-${item}`}
                      className="px-3 py-1.5 flex items-center justify-between gap-2 text-xs sm:text-sm"
                    >
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="w-4 h-4 rounded bg-teal-500/10 text-teal-600 dark:text-teal-400 font-mono text-xs font-bold flex items-center justify-center shrink-0">
                          {idx + 1}
                        </span>
                        <span className="text-zinc-800 dark:text-zinc-200 truncate">
                          {item}
                        </span>
                      </div>
                      {!isSpecLocked && (
                        <button
                          type="button"
                          onClick={() => handleRemoveCriterion(idx)}
                          className="p-1 rounded-lg text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 transition-colors cursor-pointer shrink-0"
                          title="Remove criterion"
                        >
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  ))}
                </div>
              )}

              {criteria.length > maxAcceptanceCriteria && (
                <div className="px-3 py-2 rounded-xl bg-amber-500/10 border border-amber-500/25 text-xs text-amber-800 dark:text-amber-300 flex items-center gap-2">
                  <AlertTriangle
                    size={13}
                    className="shrink-0 text-amber-500"
                  />
                  <span>
                    {criteria.length} criteria (stage policy maximum for agents
                    is {maxAcceptanceCriteria}; people can still save more).
                  </span>
                </div>
              )}
            </div>

            {/* Budget Cap & Tags */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div className="relative flex items-center">
                <span className="absolute left-3.5 text-xs font-mono font-semibold text-zinc-400 pointer-events-none">
                  $
                </span>
                <input
                  type="number"
                  min="0"
                  step="0.5"
                  value={budgetCap}
                  onChange={(e) => setBudgetCap(e.target.value)}
                  placeholder={budgetDefaultHint}
                  className="w-full h-9 pl-7 pr-3.5 text-xs sm:text-sm font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/30 focus:border-teal-500"
                />
              </div>

              <div className="min-h-9 px-2.5 py-1 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 flex flex-wrap items-center gap-1.5 focus-within:ring-2 focus-within:ring-teal-500/30 focus-within:border-teal-500">
                {tags.map((tag, idx) => (
                  <span
                    key={tag}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs font-mono font-medium bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/25"
                  >
                    <span>#{tag}</span>
                    <button
                      type="button"
                      onClick={() => removeTagChip(idx)}
                      aria-label={`Remove tag ${tag}`}
                      className="text-teal-600/70 dark:text-teal-300/70 hover:text-rose-500 transition cursor-pointer"
                    >
                      <X size={11} />
                    </button>
                  </span>
                ))}
                <input
                  type="text"
                  value={tagDraft}
                  onChange={(e) => {
                    const val = e.target.value
                    if (val.includes(',')) {
                      addTagChip(val)
                      setTagDraft('')
                    } else {
                      setTagDraft(val)
                    }
                  }}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ',') {
                      if (tagDraft.trim()) {
                        e.preventDefault()
                        addTagChip(tagDraft)
                        setTagDraft('')
                      }
                    } else if (
                      e.key === 'Backspace' &&
                      !tagDraft &&
                      tags.length > 0
                    ) {
                      removeTagChip(tags.length - 1)
                    }
                  }}
                  onBlur={() => {
                    if (tagDraft.trim()) {
                      addTagChip(tagDraft)
                      setTagDraft('')
                    }
                  }}
                  placeholder={tags.length === 0 ? 'Tags (Enter or comma)…' : ''}
                  className="flex-1 min-w-[90px] bg-transparent text-xs sm:text-sm font-mono text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none"
                />
              </div>
            </div>
          </div>
        )}

        {/* Footer: Cancel + Split Primary Button ("Create & start ▾") */}
        <div className="flex items-center justify-end gap-2.5 pt-2">
          <Button
            type="button"
            variant="secondary"
            size="md"
            onClick={onClose}
            isDisabled={isSubmitting}
            className="rounded-xl font-semibold cursor-pointer"
          >
            Cancel
          </Button>

          {isEditing ? (
            <Tooltip content="Ctrl+Enter">
              <Button
                type="submit"
                variant="primary"
                size="md"
                isDisabled={isSubmitting || !canCreate || !isDirty}
                className="rounded-xl font-semibold shadow-md shadow-teal-600/20 flex items-center gap-1.5 cursor-pointer"
              >
                <Check size={15} />
                <span>{primaryButtonLabel}</span>
              </Button>
            </Tooltip>
          ) : (
            <div
              className="relative inline-flex items-stretch rounded-xl shadow-md shadow-teal-600/20"
              data-seed-menu
            >
              <Tooltip content="Ctrl+Enter">
                <button
                  type="submit"
                  disabled={isSubmitting || !canCreate}
                  className="h-10 px-4 rounded-l-xl bg-teal-600 hover:bg-teal-500 disabled:opacity-50 disabled:cursor-not-allowed text-white text-sm font-semibold inline-flex items-center gap-1.5 transition cursor-pointer"
                >
                  <span>{primaryButtonLabel}</span>
                </button>
              </Tooltip>
              <button
                ref={submitBtnRef}
                type="button"
                disabled={isSubmitting}
                onClick={() =>
                  setOpenMenu((prev) => (prev === 'submit' ? null : 'submit'))
                }
                aria-label="More create options"
                aria-expanded={openMenu === 'submit'}
                className="h-10 px-2.5 rounded-r-xl bg-teal-600 hover:bg-teal-500 disabled:opacity-50 disabled:cursor-not-allowed text-white border-l border-teal-500/60 inline-flex items-center justify-center transition cursor-pointer"
              >
                <ChevronDown size={14} />
              </button>

              {openMenu === 'submit' && (
                <DialogPopover
                  anchorRef={submitBtnRef}
                  align="right"
                  direction="up"
                  className="w-64 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl py-1.5"
                >
                  <button
                    type="button"
                    onClick={() => {
                      setStaged(false)
                      setOpenMenu(null)
                      if (canCreate && !isSubmitting) {
                        void handleSubmit(undefined, false)
                      }
                    }}
                    className="w-full px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 transition flex items-start justify-between gap-2 cursor-pointer"
                  >
                    <div className="flex flex-col gap-0.5">
                      <span
                        className={`text-xs font-semibold ${
                          !staged
                            ? 'text-teal-600 dark:text-teal-400'
                            : 'text-zinc-900 dark:text-zinc-100'
                        }`}
                      >
                        Create &amp; start
                      </span>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">
                        Create the seed and start stage 1 now
                      </span>
                    </div>
                    {!staged && (
                      <Check
                        size={14}
                        className="shrink-0 mt-0.5 text-teal-600 dark:text-teal-400"
                      />
                    )}
                  </button>

                  <button
                    type="button"
                    onClick={() => {
                      setStaged(true)
                      setOpenMenu(null)
                      if (canCreate && !isSubmitting) {
                        void handleSubmit(undefined, true)
                      }
                    }}
                    className="w-full px-3 py-2 text-left hover:bg-zinc-100 dark:hover:bg-zinc-800 transition flex items-start justify-between gap-2 cursor-pointer"
                  >
                    <div className="flex flex-col gap-0.5">
                      <span
                        className={`text-xs font-semibold ${
                          staged
                            ? 'text-teal-600 dark:text-teal-400'
                            : 'text-zinc-900 dark:text-zinc-100'
                        }`}
                      >
                        Create without starting
                      </span>
                      <span className="text-xs text-zinc-500 dark:text-zinc-400">
                        Stage the seed so you can start it later
                      </span>
                    </div>
                    {staged && (
                      <Check
                        size={14}
                        className="shrink-0 mt-0.5 text-teal-600 dark:text-teal-400"
                      />
                    )}
                  </button>
                </DialogPopover>
              )}
            </div>
          )}
        </div>
      </form>
    </Modal>
  )
}
