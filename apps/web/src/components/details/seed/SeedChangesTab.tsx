import React, { useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowRight,
  Check,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  Clock,
  Code2,
  Columns2,
  Download,
  ExternalLink,
  FileCode,
  FileMinus,
  FilePlus,
  FileText,
  GitBranch,
  GitCommit,
  GitMerge,
  GitPullRequest,
  Loader2,
  MessageSquare,
  MoreHorizontal,
  Play,
  Plus,
  RefreshCw,
  Rows2,
  Search,
  Send,
  ShieldAlert,
  Undo2,
  XCircle,
} from 'lucide-react'
import type {
  Project,
  ProjectRepository,
  PullRequestCheckItem,
  PullRequestCommentItem,
  Seed,
  SeedChangeFile,
  SeedChangesResponse,
} from '../../../types'
import { api, ApiError } from '../../../api/client'
import { MarkdownContent } from '../../common/MarkdownContent'
import { STAGE_LABELS } from '../../../utils/seeds'
import { formatRelativeTime } from '../../../utils/format'
import {
  MonacoCodeEditor,
  parseUnifiedDiffLines,
} from '../../common/MonacoCodeEditor'

export interface SeedChangesTabProps {
  seed: Seed
  project: Project | null
  repos: ProjectRepository[]
  selectedRepoId: number | null
  onSelectRepoId: (repoId: number) => void
  changes: SeedChangesResponse | undefined
  isLoading: boolean
  isFetching: boolean
  error: Error | null
  canContribute?: boolean
  onRefresh: () => void
  onBrowseCode?: (branchRef: string) => void
  onRunSandbox?: (branchRef: string) => void
  onOpenEditor?: (branchRef: string) => void
  isOpeningEditor?: boolean
  onMoveBack?: () => void
}

function formatDurationSeconds(sec?: number | null): string | null {
  if (sec == null || !Number.isFinite(sec) || sec < 0) return null
  if (sec < 60) return `${Math.round(sec)}s`
  const mins = Math.floor(sec / 60)
  const rem = Math.round(sec % 60)
  return rem > 0 ? `${mins}m ${rem}s` : `${mins}m`
}

function renderCheckIcon(status: PullRequestCheckItem['status']) {
  switch (status) {
    case 'passed':
      return <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
    case 'failed':
      return <XCircle size={14} className="text-rose-500 shrink-0" />
    case 'running':
      return <Loader2 size={14} className="text-sky-500 animate-spin shrink-0" />
    case 'queued':
      return <Clock size={14} className="text-amber-500 shrink-0" />
    default:
      return <Clock size={14} className="text-zinc-400 shrink-0" />
  }
}

function getFileStatusMeta(status: SeedChangeFile['status']) {
  switch (status) {
    case 'added':
      return {
        short: 'A',
        label: 'Added',
        badgeClass:
          'bg-emerald-500/12 text-emerald-700 dark:text-emerald-300 border-emerald-500/25',
        icon: <FilePlus size={14} className="text-emerald-500 shrink-0" />,
      }
    case 'removed':
      return {
        short: 'D',
        label: 'Deleted',
        badgeClass:
          'bg-rose-500/12 text-rose-700 dark:text-rose-300 border-rose-500/25',
        icon: <FileMinus size={14} className="text-rose-500 shrink-0" />,
      }
    case 'renamed':
      return {
        short: 'R',
        label: 'Renamed',
        badgeClass:
          'bg-amber-500/12 text-amber-700 dark:text-amber-300 border-amber-500/25',
        icon: <FileCode size={14} className="text-amber-500 shrink-0" />,
      }
    default:
      return {
        short: 'M',
        label: 'Modified',
        badgeClass:
          'bg-sky-500/12 text-sky-700 dark:text-sky-300 border-sky-500/25',
        icon: <FileText size={14} className="text-sky-500 shrink-0" />,
      }
  }
}

export const SeedChangesTab: React.FC<SeedChangesTabProps> = ({
  seed,
  project,
  repos,
  selectedRepoId,
  onSelectRepoId,
  changes,
  isLoading,
  isFetching,
  error,
  canContribute = false,
  onRefresh,
  onBrowseCode,
  onRunSandbox,
  onOpenEditor,
  isOpeningEditor = false,
  onMoveBack,
}) => {
  const [fileFilter, setFileFilter] = useState('')
  const [collapsedPaths, setCollapsedPaths] = useState<Record<string, boolean>>({})
  const [showCommits, setShowCommits] = useState(false)
  const [showChecks, setShowChecks] = useState(false)
  const [showPrConversation, setShowPrConversation] = useState(false)
  const [showPrBody, setShowPrBody] = useState(false)
  const [showHeaderMoreMenu, setShowHeaderMoreMenu] = useState(false)
  const [prCommentText, setPrCommentText] = useState('')
  const [mirrorPrCommentToSeed, setMirrorPrCommentToSeed] = useState(true)
  const [isPostingPrComment, setIsPostingPrComment] = useState(false)
  const [isCreatingPr, setIsCreatingPr] = useState(false)
  const [prActionError, setPrActionError] = useState<string | null>(null)
  const [sideBySideDiff, setSideBySideDiff] = useState(false)
  const [downloadingPath, setDownloadingPath] = useState<string | null>(null)
  const [downloadError, setDownloadError] = useState<string | null>(null)
  const [draftDiffNotes, setDraftDiffNotes] = useState<
    Array<{ id: string; path: string; line: number | null; body: string }>
  >([])
  const [annotatingFilePath, setAnnotatingFilePath] = useState<string | null>(null)
  const [draftNoteLine, setDraftNoteLine] = useState('')
  const [draftNoteBody, setDraftNoteBody] = useState('')
  const [reviewSummaryNote, setReviewSummaryNote] = useState('')
  const [postReviewToRemotePr, setPostReviewToRemotePr] = useState(false)
  const [isSubmittingDiffReview, setIsSubmittingDiffReview] = useState(false)
  const [diffReviewNotice, setDiffReviewNotice] = useState<string | null>(null)

  const files = changes?.files || []
  const commits = changes?.commits || []
  const prStatus = changes?.pull_request_status || null
  const prChecks = prStatus?.checks || []
  const prComments = prStatus?.comments || []
  const resolvedPrUrl = prStatus?.pull_request_url || changes?.pull_request_url || null

  const inlineCommentsByPath = useMemo(() => {
    const map: Record<string, PullRequestCommentItem[]> = {}
    for (const item of prComments) {
      if (item.path) {
        if (!map[item.path]) map[item.path] = []
        map[item.path].push(item)
      }
    }
    return map
  }, [prComments])

  const draftNotesByPath = useMemo(() => {
    const map: Record<
      string,
      Array<{ id: string; path: string; line: number | null; body: string }>
    > = {}
    for (const note of draftDiffNotes) {
      if (!map[note.path]) map[note.path] = []
      map[note.path].push(note)
    }
    return map
  }, [draftDiffNotes])

  const canAnnotateDiff =
    canContribute && seed.status !== 'merged' && seed.status !== 'closed'
  const canRequestChangesViaApproval =
    seed.status === 'awaiting_approval' &&
    (seed.stage === 'review' || seed.stage === 'merge')

  const handleAddDraftDiffNote = (e: React.FormEvent, filePath: string) => {
    e.preventDefault()
    const body = draftNoteBody.trim()
    if (!body) return
    const parsedLine = Number.parseInt(draftNoteLine.trim(), 10)
    const line =
      Number.isFinite(parsedLine) && parsedLine > 0 ? parsedLine : null
    setDraftDiffNotes((prev) => [
      ...prev,
      {
        id: `${filePath}-${Date.now()}-${prev.length}`,
        path: filePath,
        line,
        body,
      },
    ])
    setDraftNoteLine('')
    setDraftNoteBody('')
    setAnnotatingFilePath(null)
    setDiffReviewNotice(null)
  }

  const handleRemoveDraftDiffNote = (id: string) => {
    setDraftDiffNotes((prev) => prev.filter((n) => n.id !== id))
  }

  const handleSubmitDiffReview = async () => {
    if (draftDiffNotes.length === 0) return
    setIsSubmittingDiffReview(true)
    setPrActionError(null)
    setDiffReviewNotice(null)
    try {
      const header = reviewSummaryNote.trim()
        ? `@coder ${reviewSummaryNote.trim()}`
        : '@coder Please address the following inline diff review notes:'
      const bullets = draftDiffNotes.map((n) =>
        n.line
          ? `- \`${n.path}:${n.line}\`: ${n.body}`
          : `- \`${n.path}\`: ${n.body}`
      )
      const formattedMarkdown = `${header}\n\n### Inline Diff Review Notes (${draftDiffNotes.length})\n${bullets.join('\n')}`

      if (canRequestChangesViaApproval) {
        await api.seeds.createApproval(seed.id, {
          decision: 'changes_requested',
          comment: formattedMarkdown,
        })
      } else {
        await api.seeds.createComment(seed.id, {
          body: formattedMarkdown,
        })
      }

      if (postReviewToRemotePr && prStatus?.number) {
        await api.seeds.createPullRequestComment(seed.id, {
          body: formattedMarkdown,
          repo_id: selectedRepoId,
          mirror_to_seed: false,
        })
      }

      const count = draftDiffNotes.length
      setDraftDiffNotes([])
      setReviewSummaryNote('')
      setAnnotatingFilePath(null)
      setDiffReviewNotice(
        canRequestChangesViaApproval
          ? `Sent ${count} inline diff note${count === 1 ? '' : 's'} to @coder and returned seed to Execute.`
          : `Sent ${count} inline diff note${count === 1 ? '' : 's'} to @coder.`
      )
      onRefresh()
    } catch (err) {
      setPrActionError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Unable to submit inline diff review.'
      )
    } finally {
      setIsSubmittingDiffReview(false)
    }
  }

  const handleCreateOrSyncPr = async () => {
    setIsCreatingPr(true)
    setPrActionError(null)
    try {
      await api.seeds.createPullRequest(seed.id, selectedRepoId)
      onRefresh()
    } catch (err) {
      setPrActionError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Unable to create Pull Request.'
      )
    } finally {
      setIsCreatingPr(false)
    }
  }

  const handlePostPrComment = async (e: React.FormEvent) => {
    e.preventDefault()
    const body = prCommentText.trim()
    if (!body) return
    setIsPostingPrComment(true)
    setPrActionError(null)
    try {
      await api.seeds.createPullRequestComment(seed.id, {
        body,
        repo_id: selectedRepoId,
        mirror_to_seed: mirrorPrCommentToSeed,
      })
      setPrCommentText('')
      onRefresh()
    } catch (err) {
      setPrActionError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to post comment to Pull Request.'
      )
    } finally {
      setIsPostingPrComment(false)
    }
  }

  const filteredFiles = useMemo(() => {
    const q = fileFilter.trim().toLowerCase()
    if (!q) return files
    return files.filter(
      (f) =>
        f.path.toLowerCase().includes(q) ||
        (f.previous_path && f.previous_path.toLowerCase().includes(q))
    )
  }, [files, fileFilter])

  const toggleFileCollapsed = (path: string) => {
    setCollapsedPaths((prev) => ({
      ...prev,
      [path]: !prev[path],
    }))
  }

  const expandAll = () => setCollapsedPaths({})
  const collapseAll = () => {
    const next: Record<string, boolean> = {}
    for (const f of files) {
      next[f.path] = true
    }
    setCollapsedPaths(next)
  }

  if (repos.length === 0) {
    return (
      <div className="p-8 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs text-center flex flex-col items-center gap-3">
        <div className="w-11 h-11 rounded-2xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center">
          <Code2 size={20} />
        </div>
        <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
          No Repository Linked
        </h3>
        <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
          Attach a Git repository to this project to inspect branch diffs and code changes attributed to this seed.
        </p>
      </div>
    )
  }

  if (isLoading && !changes) {
    return (
      <div className="p-8 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col items-center justify-center gap-2.5 text-zinc-500 dark:text-zinc-400">
        <Loader2 size={22} className="animate-spin text-teal-500" />
        <span className="text-xs font-medium">
          Loading diff and commits for seed #{seed.number}...
        </span>
      </div>
    )
  }

  if (error && !changes) {
    return (
      <div className="p-6 rounded-2xl border border-rose-500/30 bg-rose-500/5 text-center space-y-3">
        <p className="text-xs font-medium text-rose-600 dark:text-rose-400">
          {error.message || 'Unable to load changes from Git provider.'}
        </p>
        <button
          type="button"
          onClick={onRefresh}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-white dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-200 hover:border-teal-500/40 cursor-pointer"
        >
          <RefreshCw size={13} />
          <span>Retry</span>
        </button>
      </div>
    )
  }

  const hasChanges = files.length > 0 || commits.length > 0
  const defaultSeedBranch = project?.slug
    ? `gitseed/${project.slug}/seed-${seed.number}`
    : `gitseed/seed-${seed.number}`
  const expectedBranchRef =
    changes?.head_ref && changes.head_ref.startsWith('gitseed/')
      ? changes.head_ref
      : defaultSeedBranch
  const browseTargetRef = changes?.branch_exists
    ? expectedBranchRef
    : changes?.base_ref || 'main'
  const commitShaRef =
    changes?.head_commit_sha || prStatus?.head_sha || commits[0]?.sha || null
  const commitBrowseRef = commitShaRef || browseTargetRef
  const activeRepoObj =
    repos.find((r) => r.id === (selectedRepoId ?? repos[0]?.id)) || repos[0]
  const isLocalProvider =
    prStatus?.provider === 'local' ||
    changes?.provider === 'local' ||
    Boolean(activeRepoObj?.full_name.startsWith('local/'))
  const canShowOpenEditor = Boolean(
    onOpenEditor &&
      canContribute &&
      seed.status !== 'merged' &&
      seed.status !== 'closed' &&
      (seed.stage === 'execute' || seed.stage === 'review')
  )
  const canCreatePr = Boolean(
    !isLocalProvider &&
      !resolvedPrUrl &&
      changes?.branch_exists &&
      canContribute &&
      seed.status !== 'merged' &&
      seed.status !== 'closed' &&
      (seed.stage === 'execute' || seed.stage === 'review' || seed.stage === 'merge')
  )
  const isMergedState = Boolean(
    prStatus?.merged ||
      prStatus?.state === 'merged' ||
      seed.status === 'merged' ||
      changes?.comparison_mode === 'merged_commit'
  )
  const fullHeadRef = prStatus?.head_ref || changes?.head_ref || expectedBranchRef
  const shortHeadRef = fullHeadRef.replace(/^gitseed\/(?:[^/]+\/)?/, '')
  const baseBranchRef = prStatus?.base_ref || changes?.base_ref || 'main'
  const rawExternalGitUrl = resolvedPrUrl || changes?.web_diff_url || null
  const externalGitUrl =
    isLocalProvider || (rawExternalGitUrl && rawExternalGitUrl.startsWith('local://'))
      ? null
      : rawExternalGitUrl
  const gitHostLabel =
    prStatus?.provider === 'gitlab'
      ? 'GitLab'
      : prStatus?.provider === 'bitbucket'
        ? 'Bitbucket'
        : 'GitHub'
  const totalFilesCount = changes?.total_files || files.length

  /** Save the changed file's current contents (at the seed branch) to disk. */
  const downloadFile = async (file: SeedChangeFile) => {
    const repoId = selectedRepoId ?? repos[0]?.id
    if (!project || repoId == null) return
    setDownloadingPath(file.path)
    setDownloadError(null)
    try {
      const blob = await api.projects.getRepoBlob(project.id, repoId, { path: file.path, ref: browseTargetRef })
      if (blob.is_binary || blob.truncated || blob.content == null) {
        // Too large or binary for the inline API: hand off to the Git host instead
        if (file.web_url) window.open(file.web_url, '_blank', 'noreferrer')
        else setDownloadError(`${file.path} is binary or too large to download here.`)
        return
      }
      const url = URL.createObjectURL(new Blob([blob.content], { type: 'text/plain;charset=utf-8' }))
      const a = document.createElement('a')
      a.href = url
      a.download = blob.name || file.path.split('/').pop() || 'file'
      document.body.appendChild(a)
      a.click()
      a.remove()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    } catch (err) {
      setDownloadError(err instanceof Error ? err.message : `Could not download ${file.path}`)
    } finally {
      setDownloadingPath(null)
    }
  }

  return (
    <div className="space-y-4">
      {/* Unified Header Card */}
      <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/92 dark:bg-zinc-900/92 backdrop-blur-xl shadow-xs overflow-visible">
        <div className="p-4 flex flex-col gap-3">
          <div className="flex flex-col lg:flex-row lg:items-center justify-between gap-3">
            {/* Left: Title + single status + branch flow + file/line stats */}
            <div className="min-w-0 flex-1 space-y-1.5">
              {/* Line 1: Repo selector, [project#N] Title, #PR · Status · branch → base · N commits */}
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1 text-xs">
                {repos.length > 1 && (
                  <select
                    value={selectedRepoId ?? repos[0]?.id ?? ''}
                    onChange={(e) => onSelectRepoId(Number(e.target.value))}
                    className="h-7 px-2 rounded-lg text-xs font-mono bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 focus:outline-none focus:border-teal-500"
                  >
                    {repos.map((r) => (
                      <option key={r.id ?? r.full_name} value={r.id ?? ''}>
                        {r.full_name}
                      </option>
                    ))}
                  </select>
                )}

                <span className="font-mono text-xs font-semibold text-zinc-500 dark:text-zinc-400 shrink-0">
                  [{project?.slug || 'seed'}#{seed.number}]
                </span>
                <span className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100 truncate max-w-md">
                  {prStatus?.title || seed.title}
                </span>

                {prStatus?.number && (
                  <>
                    <span className="text-zinc-300 dark:text-zinc-600">·</span>
                    <span className="font-mono font-semibold text-zinc-700 dark:text-zinc-300">
                      {prStatus.provider === 'gitlab'
                        ? `!${prStatus.number}`
                        : `#${prStatus.number}`}
                    </span>
                  </>
                )}

                {(isMergedState || prStatus?.state) && (
                  <>
                    <span className="text-zinc-300 dark:text-zinc-600">·</span>
                    <span
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border ${
                        isMergedState
                          ? 'bg-violet-500/12 text-violet-700 dark:text-violet-300 border-violet-500/25'
                          : prStatus?.state === 'closed'
                            ? 'bg-rose-500/12 text-rose-700 dark:text-rose-300 border-rose-500/25'
                            : prStatus?.draft
                              ? 'bg-zinc-500/12 text-zinc-700 dark:text-zinc-300 border-zinc-500/25'
                              : 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-300 border-emerald-500/25'
                      }`}
                    >
                      {isMergedState ? (
                        <GitMerge size={11} />
                      ) : (
                        <GitPullRequest size={11} />
                      )}
                      <span className="capitalize">
                        {isMergedState
                          ? 'Merged'
                          : isLocalProvider
                            ? 'Local Branch'
                            : prStatus?.draft
                              ? 'Draft'
                              : prStatus?.state}
                      </span>
                    </span>
                  </>
                )}

                <span className="text-zinc-300 dark:text-zinc-600">·</span>
                <span
                  className="inline-flex items-center gap-1 font-mono text-zinc-600 dark:text-zinc-300"
                  title={`${fullHeadRef} → ${baseBranchRef}`}
                >
                  <span>{shortHeadRef}</span>
                  <ArrowRight size={11} className="opacity-60" />
                  <span>{baseBranchRef}</span>
                </span>

                {commits.length > 0 && (
                  <>
                    <span className="text-zinc-300 dark:text-zinc-600">·</span>
                    <button
                      type="button"
                      onClick={() => setShowCommits((prev) => !prev)}
                      className="inline-flex items-center gap-1 font-mono text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
                    >
                      <span>
                        {commits.length}{' '}
                        {commits.length === 1 ? 'commit' : 'commits'}
                      </span>
                      {showCommits ? (
                        <ChevronDown size={11} />
                      ) : (
                        <ChevronRight size={11} />
                      )}
                    </button>
                  </>
                )}
              </div>

              {/* Line 2: 4 files · +4565 −4 + CI checks pill + review/conflict indicators */}
              <div className="flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-zinc-500 dark:text-zinc-400">
                {hasChanges && (
                  <div className="inline-flex items-center gap-1.5">
                    <span className="font-semibold text-zinc-700 dark:text-zinc-300">
                      {totalFilesCount} {totalFilesCount === 1 ? 'file' : 'files'}
                    </span>
                    <span>·</span>
                    <span className="font-mono font-semibold text-emerald-600 dark:text-emerald-400">
                      +{changes?.total_additions || 0}
                    </span>
                    <span className="font-mono font-semibold text-rose-600 dark:text-rose-400">
                      −{changes?.total_deletions || 0}
                    </span>
                  </div>
                )}

                {prStatus &&
                  (prStatus.ci_status !== 'none' || prChecks.length > 0) && (
                    <button
                      type="button"
                      onClick={() => setShowChecks((v) => !v)}
                      className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold border transition cursor-pointer ${
                        prStatus.ci_status === 'passing'
                          ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25 hover:bg-emerald-500/20'
                          : prStatus.ci_status === 'failing'
                            ? 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/25 hover:bg-rose-500/20'
                            : 'bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-500/25 hover:bg-sky-500/20'
                      }`}
                    >
                      {prStatus.ci_status === 'passing' ? (
                        <CheckCircle2 size={11} />
                      ) : prStatus.ci_status === 'failing' ? (
                        <XCircle size={11} />
                      ) : (
                        <Loader2 size={11} className="animate-spin" />
                      )}
                      <span>
                        {prStatus.ci_summary
                          ? `Checks: ${prStatus.ci_summary}`
                          : prStatus.ci_status === 'passing'
                            ? 'Checks passing'
                            : prStatus.ci_status === 'failing'
                              ? 'Checks failing'
                              : 'Checks running'}
                      </span>
                      {showChecks ? (
                        <ChevronDown size={11} />
                      ) : (
                        <ChevronRight size={11} />
                      )}
                    </button>
                  )}

                {prStatus?.has_conflicts && (
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-semibold bg-rose-500/12 text-rose-700 dark:text-rose-300 border border-rose-500/25">
                    <AlertCircle size={11} />
                    <span>Merge conflicts</span>
                  </span>
                )}

                {prStatus?.approved_by && prStatus.approved_by.length > 0 && (
                  <span className="inline-flex items-center gap-1 text-emerald-600 dark:text-emerald-400 font-medium">
                    <Check size={12} />
                    <span>
                      Approved by {prStatus.approved_by.map((u) => `@${u}`).join(', ')}
                    </span>
                  </span>
                )}

                {prStatus?.changes_requested_by &&
                  prStatus.changes_requested_by.length > 0 && (
                    <span className="inline-flex items-center gap-1 text-rose-600 dark:text-rose-400 font-medium">
                      <AlertCircle size={12} />
                      <span>
                        Changes requested by{' '}
                        {prStatus.changes_requested_by.map((u) => `@${u}`).join(', ')}
                      </span>
                    </span>
                  )}
              </div>
            </div>

            {/* Right: Contextual Actions + single Git host link + ⋯ menu */}
            <div className="flex items-center gap-2 flex-wrap shrink-0">
              {canShowOpenEditor && onOpenEditor && (
                <button
                  type="button"
                  disabled={isOpeningEditor}
                  onClick={() => onOpenEditor(browseTargetRef)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white transition cursor-pointer disabled:opacity-60"
                >
                  {isOpeningEditor ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <FileCode size={13} />
                  )}
                  <span>
                    {isOpeningEditor ? 'Opening editor…' : 'Open in editor'}
                  </span>
                </button>
              )}

              {canCreatePr && (
                <button
                  type="button"
                  disabled={isCreatingPr}
                  onClick={() => void handleCreateOrSyncPr()}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white transition cursor-pointer disabled:opacity-60"
                >
                  {isCreatingPr ? (
                    <Loader2 size={12} className="animate-spin" />
                  ) : (
                    <Plus size={13} />
                  )}
                  <span>
                    {isCreatingPr ? 'Creating PR…' : 'Create Pull Request'}
                  </span>
                </button>
              )}

              {onRunSandbox && (
                <button
                  type="button"
                  onClick={() => onRunSandbox(browseTargetRef)}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-teal-500/15 hover:bg-teal-500/25 text-teal-700 dark:text-teal-300 border border-teal-500/30 transition cursor-pointer"
                >
                  <Play size={12} />
                  <span>Run in sandbox</span>
                </button>
              )}

              {externalGitUrl && (
                <a
                  href={externalGitUrl}
                  target="_blank"
                  rel="noreferrer"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/80 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-zinc-700/80 transition"
                >
                  <span>Open on {gitHostLabel}</span>
                  <ExternalLink size={12} />
                </a>
              )}

              {/* ⋯ More Menu */}
              <div className="relative">
                <button
                  type="button"
                  onClick={() => setShowHeaderMoreMenu((v) => !v)}
                  aria-label="More change actions"
                  className="inline-flex items-center justify-center h-8 w-8 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/80 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer"
                >
                  <MoreHorizontal size={15} />
                </button>

                {showHeaderMoreMenu && (
                  <>
                    <div
                      className="fixed inset-0 z-20"
                      onClick={() => setShowHeaderMoreMenu(false)}
                    />
                    <div className="absolute right-0 mt-1.5 w-60 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 shadow-lg py-1 z-30">
                      {onBrowseCode && (
                        <button
                          type="button"
                          onClick={() => {
                            setShowHeaderMoreMenu(false)
                            onBrowseCode(commitBrowseRef)
                          }}
                          className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center justify-between gap-2 cursor-pointer"
                        >
                          <span className="inline-flex items-center gap-2">
                            <Code2 size={13} className="text-teal-500 shrink-0" />
                            <span>Browse code at this commit</span>
                          </span>
                          {commitShaRef && (
                            <span className="font-mono text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
                              {commitShaRef.slice(0, 7)}
                            </span>
                          )}
                        </button>
                      )}

                      {prStatus?.body && (
                        <button
                          type="button"
                          onClick={() => {
                            setShowHeaderMoreMenu(false)
                            setShowPrBody((v) => !v)
                          }}
                          className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center justify-between gap-2 cursor-pointer"
                        >
                          <span className="inline-flex items-center gap-2">
                            <FileText size={13} className="text-zinc-400 shrink-0" />
                            <span>PR description</span>
                          </span>
                          {showPrBody && (
                            <Check size={12} className="text-teal-500 shrink-0" />
                          )}
                        </button>
                      )}

                      {(prStatus?.number || prComments.length > 0) && (
                        <button
                          type="button"
                          onClick={() => {
                            setShowHeaderMoreMenu(false)
                            setShowPrConversation((v) => !v)
                          }}
                          className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center justify-between gap-2 cursor-pointer"
                        >
                          <span className="inline-flex items-center gap-2">
                            <MessageSquare size={13} className="text-teal-500 shrink-0" />
                            <span>PR comments ({prComments.length})</span>
                          </span>
                          {showPrConversation && (
                            <Check size={12} className="text-teal-500 shrink-0" />
                          )}
                        </button>
                      )}

                      {prChecks.length > 0 && (
                        <button
                          type="button"
                          onClick={() => {
                            setShowHeaderMoreMenu(false)
                            setShowChecks((v) => !v)
                          }}
                          className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center justify-between gap-2 cursor-pointer"
                        >
                          <span className="inline-flex items-center gap-2">
                            <CheckCircle2 size={13} className="text-emerald-500 shrink-0" />
                            <span>CI checks ({prChecks.length})</span>
                          </span>
                          {showChecks && (
                            <Check size={12} className="text-teal-500 shrink-0" />
                          )}
                        </button>
                      )}

                      {commits.length > 0 && (
                        <button
                          type="button"
                          onClick={() => {
                            setShowHeaderMoreMenu(false)
                            setShowCommits((v) => !v)
                          }}
                          className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center justify-between gap-2 cursor-pointer"
                        >
                          <span className="inline-flex items-center gap-2">
                            <GitCommit size={13} className="text-teal-500 shrink-0" />
                            <span>Commits ({commits.length})</span>
                          </span>
                          {showCommits && (
                            <Check size={12} className="text-teal-500 shrink-0" />
                          )}
                        </button>
                      )}

                      <button
                        type="button"
                        disabled={isFetching}
                        onClick={() => {
                          setShowHeaderMoreMenu(false)
                          onRefresh()
                        }}
                        className="w-full px-3 py-2 text-left text-xs font-semibold text-zinc-700 dark:text-zinc-200 hover:bg-zinc-50 dark:hover:bg-zinc-800 flex items-center gap-2 cursor-pointer disabled:opacity-50"
                      >
                        <RefreshCw
                          size={13}
                          className={isFetching ? 'animate-spin text-teal-500' : 'text-zinc-400'}
                        />
                        <span>Refresh</span>
                      </button>
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          {prActionError && (
            <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs text-rose-600 dark:text-rose-400 flex items-center justify-between gap-2">
              <span>{prActionError}</span>
              <button
                type="button"
                onClick={() => setPrActionError(null)}
                className="font-semibold cursor-pointer"
              >
                Dismiss
              </button>
            </div>
          )}
        </div>

        {/* Expandable PR Description */}
        {showPrBody && prStatus?.body && (
          <div className="px-4 py-3 border-t border-zinc-200/70 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-950/40 text-xs">
            <div className="flex items-center justify-between mb-2">
              <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Pull Request Description
              </span>
              <button
                type="button"
                onClick={() => setShowPrBody(false)}
                className="text-xs text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
              >
                Hide
              </button>
            </div>
            <MarkdownContent content={prStatus.body} />
          </div>
        )}

        {/* Expandable CI / Pipeline Checks Drawer */}
        {(showChecks || prStatus?.ci_status === 'failing') &&
          prChecks.length > 0 && (
            <div className="border-t border-zinc-200/70 dark:border-zinc-800 divide-y divide-zinc-200/60 dark:divide-zinc-800/70">
              <div className="px-4 py-2 bg-zinc-50 dark:bg-zinc-800/60 text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 flex items-center justify-between">
                <span>
                  CI / Pipeline Checks ({prChecks.length})
                  {prStatus?.ci_status === 'pending' && ' · Live updating'}
                </span>
                {showChecks && (
                  <button
                    type="button"
                    onClick={() => setShowChecks(false)}
                    className="text-xs font-normal text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                  >
                    Hide
                  </button>
                )}
              </div>
              {prChecks.map((chk) => {
                const dur = formatDurationSeconds(chk.duration_seconds)
                return (
                  <div
                    key={chk.id}
                    className="px-4 py-2.5 flex flex-col gap-1 text-xs hover:bg-zinc-50/70 dark:hover:bg-zinc-800/40"
                  >
                    <div className="flex items-center justify-between gap-3">
                      <div className="flex items-center gap-2 min-w-0">
                        {renderCheckIcon(chk.status)}
                        <span className="font-semibold text-zinc-800 dark:text-zinc-200 truncate">
                          {chk.name}
                        </span>
                        {chk.workflow_name && (
                          <span className="px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[10px] font-mono text-zinc-500 dark:text-zinc-400 shrink-0">
                            {chk.workflow_name}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2.5 shrink-0 text-zinc-400">
                        {dur && (
                          <span className="font-mono text-[11px]">{dur}</span>
                        )}
                        <span
                          className={`text-[11px] font-medium capitalize ${
                            chk.status === 'passed'
                              ? 'text-emerald-600 dark:text-emerald-400'
                              : chk.status === 'failed'
                                ? 'text-rose-600 dark:text-rose-400'
                                : chk.status === 'running'
                                  ? 'text-sky-600 dark:text-sky-400'
                                  : 'text-zinc-500'
                          }`}
                        >
                          {chk.conclusion || chk.status}
                        </span>
                        {chk.details_url && (
                          <a
                            href={chk.details_url}
                            target="_blank"
                            rel="noreferrer"
                            className="text-zinc-400 hover:text-teal-500 transition"
                            title="Open check logs on Git host"
                          >
                            <ExternalLink size={12} />
                          </a>
                        )}
                      </div>
                    </div>
                    {chk.summary && chk.status === 'failed' && (
                      <pre className="mt-1 p-2 rounded-lg bg-rose-500/5 border border-rose-500/20 text-[11px] font-mono text-rose-700 dark:text-rose-300 whitespace-pre-wrap overflow-x-auto max-h-36">
                        {chk.summary}
                      </pre>
                    )}
                  </div>
                )
              })}
            </div>
          )}

        {/* Expandable PR Conversation / Reviews & Comment Composer */}
        {showPrConversation && (
          <div className="border-t border-zinc-200/70 dark:border-zinc-800 bg-zinc-50/40 dark:bg-zinc-950/30 p-4 space-y-3">
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Pull Request Conversation &amp; Reviews ({prComments.length})
              </span>
              <button
                type="button"
                onClick={() => setShowPrConversation(false)}
                className="text-xs text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
              >
                Hide
              </button>
            </div>

            {prComments.length === 0 ? (
              <p className="text-xs text-zinc-500 dark:text-zinc-400 italic">
                No comments or reviews on this Pull Request yet.
              </p>
            ) : (
              <div className="space-y-2.5 max-h-80 overflow-y-auto pr-1">
                {prComments.map((cm) => (
                  <div
                    key={cm.id}
                    className="p-3 rounded-xl border border-zinc-200/80 dark:border-zinc-800 bg-white dark:bg-zinc-900 space-y-1.5 text-xs"
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                          @{cm.author}
                        </span>
                        {cm.review_state && (
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                              cm.review_state === 'approved'
                                ? 'bg-emerald-500/12 text-emerald-700 dark:text-emerald-300'
                                : cm.review_state === 'changes_requested'
                                  ? 'bg-rose-500/12 text-rose-700 dark:text-rose-300'
                                  : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400'
                            }`}
                          >
                            {cm.review_state.replace('_', ' ')}
                          </span>
                        )}
                        {cm.path && (
                          <span className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300">
                            {cm.path}
                            {cm.line ? `:${cm.line}` : ''}
                          </span>
                        )}
                      </div>
                      <div className="flex items-center gap-2 text-zinc-400">
                        {cm.created_at && (
                          <span>{formatRelativeTime(cm.created_at)}</span>
                        )}
                        {cm.html_url && (
                          <a
                            href={cm.html_url}
                            target="_blank"
                            rel="noreferrer"
                            className="hover:text-teal-500"
                          >
                            <ExternalLink size={11} />
                          </a>
                        )}
                      </div>
                    </div>
                    <MarkdownContent content={cm.body} />
                  </div>
                ))}
              </div>
            )}

            {canContribute && prStatus?.number && (
              <form
                onSubmit={handlePostPrComment}
                className="pt-2 border-t border-zinc-200/70 dark:border-zinc-800 space-y-2"
              >
                <textarea
                  rows={2}
                  value={prCommentText}
                  onChange={(e) => setPrCommentText(e.target.value)}
                  placeholder={`Comment directly on PR #${prStatus.number}...`}
                  className="w-full p-2.5 text-xs rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                />
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <label className="inline-flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-400 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={mirrorPrCommentToSeed}
                      onChange={(e) => setMirrorPrCommentToSeed(e.target.checked)}
                      className="rounded border-zinc-300 text-teal-600 focus:ring-teal-500"
                    />
                    <span>Also mirror comment to Seed discussion</span>
                  </label>
                  <button
                    type="submit"
                    disabled={!prCommentText.trim() || isPostingPrComment}
                    className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white transition cursor-pointer"
                  >
                    {isPostingPrComment ? (
                      <Loader2 size={12} className="animate-spin" />
                    ) : (
                      <Send size={12} />
                    )}
                    <span>Post to PR</span>
                  </button>
                </div>
              </form>
            )}
          </div>
        )}
      </div>

      {/* Governance Guard Banner if blocked paths were reverted */}
      {changes?.blocked_paths_reverted &&
        changes.blocked_paths_reverted.length > 0 && (
          <div className="p-3.5 rounded-2xl border border-amber-500/30 bg-amber-500/10 text-xs text-amber-800 dark:text-amber-200 flex items-start gap-2.5">
            <ShieldAlert size={16} className="text-amber-500 shrink-0 mt-0.5" />
            <div>
              <span className="font-bold">Governance Guard: </span>
              <span>
                Reverted unauthorized agent modifications to protected project paths before pushing:{' '}
              </span>
              <code className="font-mono text-[11px]">
                {changes.blocked_paths_reverted.join(', ')}
              </code>
            </div>
          </div>
        )}

      {/* Collapsible Commits Drawer */}
      {showCommits && commits.length > 0 && (
        <div className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 overflow-hidden divide-y divide-zinc-200/70 dark:divide-zinc-800">
          <div className="px-4 py-2.5 bg-zinc-50 dark:bg-zinc-800/60 text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 flex items-center justify-between">
            <span>Commits ({commits.length})</span>
            <button
              type="button"
              onClick={() => setShowCommits(false)}
              className="text-xs font-normal text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
            >
              Hide
            </button>
          </div>
          {commits.map((c) => (
            <div
              key={c.sha}
              className="px-4 py-2.5 flex items-center justify-between gap-3 text-xs hover:bg-zinc-50/80 dark:hover:bg-zinc-800/40"
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <GitCommit size={14} className="text-teal-500 shrink-0" />
                <span className="font-mono text-[11px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 shrink-0">
                  {c.short_sha}
                </span>
                <span className="font-medium text-zinc-800 dark:text-zinc-200 truncate">
                  {c.message}
                </span>
              </div>
              <div className="flex items-center gap-2.5 shrink-0 text-zinc-400">
                {c.author_name && <span>{c.author_name}</span>}
                {c.committed_at && (
                  <span>{formatRelativeTime(c.committed_at)}</span>
                )}
                {c.web_url && (
                  <a
                    href={c.web_url}
                    target="_blank"
                    rel="noreferrer"
                    className="text-zinc-400 hover:text-teal-500 transition"
                    title="Open commit"
                  >
                    <ExternalLink size={12} />
                  </a>
                )}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Empty State if no changes exist yet */}
      {!hasChanges ? (
        <div className="py-10 px-4 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs text-center flex flex-col items-center gap-2">
          <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
            <GitBranch size={20} />
          </div>
          <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
            No code changes yet
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
            {seed.stage === 'ideate' || seed.stage === 'plan'
              ? `This seed is currently in the ${STAGE_LABELS[seed.stage] || seed.stage} stage. Once @coder executes the plan and pushes commits to ${expectedBranchRef}, the file diffs will appear here.`
              : `No commits or file diffs were found between ${changes?.base_ref || 'main'} and ${expectedBranchRef}.`}
          </p>
          {onMoveBack && (seed.stage === 'review' || seed.stage === 'merge') && (
            <button
              type="button"
              onClick={onMoveBack}
              className="mt-1 inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-teal-500/15 hover:bg-teal-500/25 text-teal-700 dark:text-teal-300 border border-teal-500/30 transition cursor-pointer"
            >
              <Undo2 size={13} />
              <span>Move back to…</span>
            </button>
          )}
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-12 gap-4 items-start">
          {/* Left Column: File List */}
          {/* Pins below the seed's status banner (which publishes --seed-banner-offset) */}
        <div className="lg:col-span-4 xl:col-span-3 rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 overflow-hidden lg:sticky lg:top-[calc(var(--seed-banner-offset,0px)+1rem)]">
            <div className="p-2.5 border-b border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/70 dark:bg-zinc-800/50 space-y-2">
              <div className="relative">
                <Search
                  size={13}
                  className="absolute left-2.5 top-1/2 -translate-y-1/2 text-zinc-400"
                />
                <input
                  type="text"
                  value={fileFilter}
                  onChange={(e) => setFileFilter(e.target.value)}
                  placeholder="Filter changed files..."
                  className="w-full h-8 pl-8 pr-2.5 text-xs rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                />
              </div>
              <div className="flex items-center justify-between px-1 text-[11px] text-zinc-500 dark:text-zinc-400">
                <span>
                  Showing {filteredFiles.length} of {files.length}
                </span>
                <div className="flex items-center gap-2">
                  <button
                    type="button"
                    onClick={expandAll}
                    className="hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                  >
                    Expand all
                  </button>
                  <span>·</span>
                  <button
                    type="button"
                    onClick={collapseAll}
                    className="hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer"
                  >
                    Collapse all
                  </button>
                  <div
                    role="group"
                    aria-label="Diff view mode"
                    className="ml-0.5 inline-flex items-center rounded-lg p-0.5 bg-zinc-200/70 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80"
                  >
                    <button
                      type="button"
                      onClick={() => setSideBySideDiff(false)}
                      title="Unified diff"
                      aria-label="Unified diff"
                      className={`p-1 rounded-md transition cursor-pointer ${
                        !sideBySideDiff
                          ? 'bg-white dark:bg-zinc-900 text-teal-600 dark:text-teal-400 shadow-2xs'
                          : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                      }`}
                    >
                      <Rows2 size={11} />
                    </button>
                    <button
                      type="button"
                      onClick={() => setSideBySideDiff(true)}
                      title="Split diff"
                      aria-label="Split diff"
                      className={`p-1 rounded-md transition cursor-pointer ${
                        sideBySideDiff
                          ? 'bg-white dark:bg-zinc-900 text-teal-600 dark:text-teal-400 shadow-2xs'
                          : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                      }`}
                    >
                      <Columns2 size={11} />
                    </button>
                  </div>
                </div>
              </div>
            </div>

            <div className="max-h-[min(65vh,calc(100vh-var(--seed-banner-offset,0px)-11rem))] overflow-y-auto divide-y divide-zinc-100 dark:divide-zinc-800/60">
              {filteredFiles.map((file) => {
                const meta = getFileStatusMeta(file.status)
                return (
                  <button
                    key={file.path}
                    type="button"
                    onClick={() => {
                      setCollapsedPaths((prev) => ({
                        ...prev,
                        [file.path]: false,
                      }))
                      const el = document.getElementById(
                        `seed-diff-file-${file.path}`
                      )
                      el?.scrollIntoView({ behavior: 'smooth', block: 'start' })
                    }}
                    className="w-full px-3 py-2 text-left hover:bg-zinc-50 dark:hover:bg-zinc-800/60 flex items-center justify-between gap-2 transition cursor-pointer"
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className={`w-4 h-4 rounded text-[10px] font-mono font-bold flex items-center justify-center border shrink-0 ${meta.badgeClass}`}
                      >
                        {meta.short}
                      </span>
                      <span
                        className="font-mono text-xs text-zinc-800 dark:text-zinc-200 truncate"
                        title={file.path}
                      >
                        {file.path}
                      </span>
                    </div>
                    <div className="flex items-center gap-1 font-mono text-[10px] shrink-0">
                      {file.additions > 0 && (
                        <span className="text-emerald-600 dark:text-emerald-400">
                          +{file.additions}
                        </span>
                      )}
                      {file.deletions > 0 && (
                        <span className="text-rose-600 dark:text-rose-400">
                          -{file.deletions}
                        </span>
                      )}
                    </div>
                  </button>
                )
              })}
            </div>
          </div>

          {/* Right Column: File Diffs */}
          <div className="lg:col-span-8 xl:col-span-9 space-y-4">
            {downloadError && (
              <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs text-rose-600 dark:text-rose-400 flex items-center justify-between gap-2">
                <span>{downloadError}</span>
                <button type="button" onClick={() => setDownloadError(null)} className="font-semibold cursor-pointer">
                  Dismiss
                </button>
              </div>
            )}
            {diffReviewNotice && (
              <div className="p-2.5 rounded-xl bg-emerald-500/10 border border-emerald-500/25 text-xs text-emerald-700 dark:text-emerald-300 flex items-center justify-between gap-2">
                <span className="flex items-center gap-1.5 font-medium">
                  <CheckCircle2 size={14} className="text-emerald-500 shrink-0" />
                  {diffReviewNotice}
                </span>
                <button
                  type="button"
                  onClick={() => setDiffReviewNotice(null)}
                  className="font-semibold cursor-pointer"
                >
                  Dismiss
                </button>
              </div>
            )}
            {draftDiffNotes.length > 0 && (
              <div className="p-3.5 rounded-2xl border border-teal-500/35 bg-teal-500/5 dark:bg-teal-500/10 space-y-2.5 shadow-2xs">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2 text-xs font-bold text-teal-800 dark:text-teal-200">
                    <MessageSquare size={14} className="text-teal-500 shrink-0" />
                    <span>
                      Pending Inline Diff Review ({draftDiffNotes.length}{' '}
                      {draftDiffNotes.length === 1 ? 'note' : 'notes'})
                    </span>
                  </div>
                  <button
                    type="button"
                    onClick={() => setDraftDiffNotes([])}
                    className="text-[11px] font-medium text-zinc-500 hover:text-rose-600 dark:hover:text-rose-400 cursor-pointer"
                  >
                    Clear all
                  </button>
                </div>
                <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2">
                  <input
                    type="text"
                    value={reviewSummaryNote}
                    onChange={(e) => setReviewSummaryNote(e.target.value)}
                    placeholder="Optional overall instructions for @coder..."
                    aria-label="Overall review summary for @coder"
                    className="flex-1 h-8 px-2.5 text-xs rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                  />
                  {prStatus?.number != null && (
                    <label className="inline-flex items-center gap-1.5 text-[11px] text-zinc-600 dark:text-zinc-300 select-none cursor-pointer shrink-0">
                      <input
                        type="checkbox"
                        checked={postReviewToRemotePr}
                        onChange={(e) => setPostReviewToRemotePr(e.target.checked)}
                        className="rounded border-zinc-300 text-teal-600 focus:ring-teal-500"
                      />
                      <span>Also post to PR #{prStatus.number}</span>
                    </label>
                  )}
                  <button
                    type="button"
                    disabled={isSubmittingDiffReview}
                    onClick={() => void handleSubmitDiffReview()}
                    className="inline-flex items-center justify-center gap-1.5 h-8 px-3 rounded-xl text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white shadow-2xs transition cursor-pointer disabled:opacity-60 shrink-0"
                  >
                    {isSubmittingDiffReview ? (
                      <Loader2 size={13} className="animate-spin" />
                    ) : (
                      <Send size={13} />
                    )}
                    <span>
                      {canRequestChangesViaApproval
                        ? `Request Changes & Dispatch @coder (${draftDiffNotes.length})`
                        : `Send Diff Review to @coder (${draftDiffNotes.length})`}
                    </span>
                  </button>
                </div>
              </div>
            )}
            {filteredFiles.map((file) => {
              const meta = getFileStatusMeta(file.status)
              const isCollapsed = Boolean(collapsedPaths[file.path])
              const parsedDiff =
                !isCollapsed && file.patch
                  ? parseUnifiedDiffLines(file.patch)
                  : null

              return (
                <div
                  key={file.path}
                  id={`seed-diff-file-${file.path}`}
                  className="rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 overflow-hidden shadow-2xs"
                >
                  {/* File Header */}
                  <div className="px-3.5 py-2.5 bg-zinc-50/90 dark:bg-zinc-800/70 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3">
                    <button
                      type="button"
                      onClick={() => toggleFileCollapsed(file.path)}
                      className="flex items-center gap-2 min-w-0 text-left cursor-pointer"
                    >
                      {isCollapsed ? (
                        <ChevronRight size={14} className="text-zinc-400 shrink-0" />
                      ) : (
                        <ChevronDown size={14} className="text-zinc-400 shrink-0" />
                      )}
                      {meta.icon}
                      <span className="font-mono text-xs font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                        {file.previous_path
                          ? `${file.previous_path} → ${file.path}`
                          : file.path}
                      </span>
                      <span
                        className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border shrink-0 ${meta.badgeClass}`}
                      >
                        {meta.label}
                      </span>
                    </button>

                    <div className="flex items-center gap-2.5 shrink-0">
                      <div className="flex items-center gap-1.5 font-mono text-xs">
                        <span className="text-emerald-600 dark:text-emerald-400 font-semibold">
                          +{file.additions}
                        </span>
                        <span className="text-rose-600 dark:text-rose-400 font-semibold">
                          -{file.deletions}
                        </span>
                      </div>
                      {canAnnotateDiff && (
                        <button
                          type="button"
                          onClick={() => {
                            setCollapsedPaths((prev) => ({
                              ...prev,
                              [file.path]: false,
                            }))
                            if (annotatingFilePath === file.path) {
                              setAnnotatingFilePath(null)
                            } else {
                              setAnnotatingFilePath(file.path)
                              setDraftNoteLine('')
                              setDraftNoteBody('')
                            }
                          }}
                          className={`inline-flex items-center gap-1 px-2 py-1 rounded-lg text-[11px] font-semibold border transition cursor-pointer ${
                            annotatingFilePath === file.path ||
                            (draftNotesByPath[file.path]?.length ?? 0) > 0
                              ? 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/35'
                              : 'bg-white dark:bg-zinc-900 text-zinc-600 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700 hover:border-teal-500/40 hover:text-teal-600 dark:hover:text-teal-400'
                          }`}
                          title={`Add inline review note on ${file.path} for @coder`}
                          aria-label={`Annotate ${file.path}`}
                        >
                          <MessageSquare size={12} />
                          <span>
                            Annotate
                            {draftNotesByPath[file.path]?.length
                              ? ` (${draftNotesByPath[file.path].length})`
                              : ''}
                          </span>
                        </button>
                      )}
                      {file.status !== 'removed' && project && (selectedRepoId ?? repos[0]?.id) != null && (
                        <button
                          type="button"
                          onClick={() => void downloadFile(file)}
                          disabled={downloadingPath === file.path}
                          className="p-1 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-700/60 transition cursor-pointer disabled:opacity-60"
                          title={`Download ${file.path.split('/').pop()} (${browseTargetRef})`}
                        >
                          {downloadingPath === file.path ? (
                            <Loader2 size={13} className="animate-spin" />
                          ) : (
                            <Download size={13} />
                          )}
                        </button>
                      )}
                      {file.web_url && (
                        <a
                          href={file.web_url}
                          target="_blank"
                          rel="noreferrer"
                          className="p-1 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-700/60 transition"
                          title="Open file on Git host"
                        >
                          <ExternalLink size={13} />
                        </a>
                      )}
                    </div>
                  </div>

                  {/* Read-only Monaco DiffEditor Body */}
                  {!isCollapsed && (
                    <div className="overflow-hidden bg-white dark:bg-zinc-950">
                      {parsedDiff && file.patch ? (
                        <MonacoCodeEditor
                          mode="diff"
                          filePath={file.path}
                          originalValue={parsedDiff.original}
                          value={parsedDiff.modified}
                          originalLineNumbers={parsedDiff.originalLineMap}
                          modifiedLineNumbers={parsedDiff.modifiedLineMap}
                          onLineSelect={
                            canAnnotateDiff
                              ? (realLineNumber) => {
                                  if (annotatingFilePath !== file.path) {
                                    setAnnotatingFilePath(file.path)
                                  }
                                  setDraftNoteLine(String(realLineNumber))
                                }
                              : undefined
                          }
                          readOnly
                          sideBySide={sideBySideDiff}
                          autoHeight
                          minHeight={110}
                          maxHeight={520}
                        />
                      ) : (
                        <div className="p-4 text-xs text-zinc-500 dark:text-zinc-400 italic">
                          {file.status === 'removed'
                            ? 'File was deleted.'
                            : 'Binary file or large diff not shown inline.'}
                        </div>
                      )}
                      {file.truncated && (
                        <div className="px-3.5 py-2 border-t border-zinc-200 dark:border-zinc-800 bg-amber-500/10 text-xs text-amber-700 dark:text-amber-300">
                          Diff truncated due to size. Open the file on your Git host to view the full patch.
                        </div>
                      )}
                      {annotatingFilePath === file.path && (
                        <form
                          onSubmit={(e) => handleAddDraftDiffNote(e, file.path)}
                          className="p-3 border-t border-teal-500/30 bg-teal-500/5 dark:bg-teal-500/10 flex flex-col sm:flex-row items-stretch sm:items-center gap-2"
                        >
                          <input
                            type="number"
                            min={1}
                            value={draftNoteLine}
                            onChange={(e) => setDraftNoteLine(e.target.value)}
                            placeholder="Line #"
                            aria-label={`Line number in ${file.path}`}
                            className="w-full sm:w-24 h-8 px-2.5 text-xs font-mono rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                          />
                          <input
                            type="text"
                            value={draftNoteBody}
                            onChange={(e) => setDraftNoteBody(e.target.value)}
                            placeholder={`Review note on ${file.path} for @coder...`}
                            aria-label={`Review note on ${file.path}`}
                            autoFocus
                            className="flex-1 h-8 px-2.5 text-xs rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
                          />
                          <div className="flex items-center gap-1.5 shrink-0">
                            <button
                              type="submit"
                              disabled={!draftNoteBody.trim()}
                              className="inline-flex items-center gap-1 h-8 px-2.5 rounded-xl text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white transition cursor-pointer disabled:opacity-50"
                            >
                              <Plus size={12} />
                              <span>Add Note</span>
                            </button>
                            <button
                              type="button"
                              onClick={() => setAnnotatingFilePath(null)}
                              className="h-8 px-2.5 rounded-xl text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                            >
                              Cancel
                            </button>
                          </div>
                        </form>
                      )}
                      {draftNotesByPath[file.path] &&
                        draftNotesByPath[file.path].length > 0 && (
                          <div className="p-3 border-t border-teal-500/25 bg-teal-500/5 dark:bg-teal-950/20 space-y-2">
                            <div className="text-[11px] font-bold uppercase tracking-wider text-teal-700 dark:text-teal-300 flex items-center gap-1.5">
                              <MessageSquare size={12} className="text-teal-500" />
                              <span>
                                Queued Notes for @coder on {file.path} (
                                {draftNotesByPath[file.path].length})
                              </span>
                            </div>
                            {draftNotesByPath[file.path].map((note) => (
                              <div
                                key={note.id}
                                className="p-2.5 rounded-xl border border-teal-500/25 bg-white dark:bg-zinc-900 text-xs flex items-start justify-between gap-2"
                              >
                                <div className="space-y-0.5 min-w-0">
                                  <div className="flex items-center gap-1.5 font-mono text-[11px] text-teal-700 dark:text-teal-300 font-semibold">
                                    <span>
                                      {note.line
                                        ? `${note.path}:${note.line}`
                                        : note.path}
                                    </span>
                                  </div>
                                  <p className="text-zinc-800 dark:text-zinc-200 break-words">
                                    {note.body}
                                  </p>
                                </div>
                                <button
                                  type="button"
                                  onClick={() => handleRemoveDraftDiffNote(note.id)}
                                  className="text-zinc-400 hover:text-rose-500 text-xs font-semibold px-1 cursor-pointer shrink-0"
                                  title="Remove note"
                                  aria-label={`Remove note on ${note.path}`}
                                >
                                  ×
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      {inlineCommentsByPath[file.path] &&
                        inlineCommentsByPath[file.path].length > 0 && (
                          <div className="p-3 border-t border-zinc-200 dark:border-zinc-800 bg-zinc-50/70 dark:bg-zinc-900/70 space-y-2">
                            <div className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 flex items-center gap-1.5">
                              <MessageSquare size={12} className="text-teal-500" />
                              <span>
                                PR Review Comments on {file.path} (
                                {inlineCommentsByPath[file.path].length})
                              </span>
                            </div>
                            {inlineCommentsByPath[file.path].map((ic) => (
                              <div
                                key={ic.id}
                                className="p-2.5 rounded-xl border border-zinc-200/80 dark:border-zinc-800 bg-white dark:bg-zinc-900 text-xs space-y-1"
                              >
                                <div className="flex items-center justify-between gap-2">
                                  <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                                    @{ic.author}
                                    {ic.line ? (
                                      <span className="ml-1.5 font-mono text-[11px] text-zinc-500">
                                        line {ic.line}
                                      </span>
                                    ) : null}
                                  </span>
                                  {ic.created_at && (
                                    <span className="text-zinc-400">
                                      {formatRelativeTime(ic.created_at)}
                                    </span>
                                  )}
                                </div>
                                <MarkdownContent content={ic.body} />
                              </div>
                            ))}
                          </div>
                        )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}
    </div>
  )
}
