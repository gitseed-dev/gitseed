import React, { useMemo, useState } from 'react'
import {
  Check,
  Copy,
  Download,
  ExternalLink,
  FileCheck2,
  FolderGit2,
  GitBranch,
  GitPullRequest,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
  Terminal,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api } from '../../../api/client'
import { VIEWS } from '../../../constants'
import { useData } from '../../../context/DataContext'
import { formatRelativeTime, formatTokens, formatUsd } from '../../../utils/format'
import { buildRepoWebUrl } from '../../../utils/gitUrls'
import {
  AUTONOMY_DESCRIPTIONS,
  AUTONOMY_LABELS,
} from '../../../utils/seeds'
import { Modal } from '../../common/Modal'
import type {
  ActivityEvent,
  BudgetEnforcement,
  GovernanceReceiptEnvelope,
  Project,
  ProjectRepository,
  Run,
  Seed,
  SeedAutonomy,
  SeedChangesResponse,
} from '../../../types'

export interface SeedMetadataSidebarProps {
  seed: Seed
  project: Project | null
  primaryRepo: ProjectRepository | null
  repos?: ProjectRepository[]
  runs: Run[]
  changes?: SeedChangesResponse | null
  seedEvents?: ActivityEvent[]
  canContribute: boolean
  autonomyNotice: string | null
  onAutonomyChange: (nextAutonomy: SeedAutonomy) => void
  resolveActorName: (actorType: string, actorId: string | null) => string
  onOpenPullRequest?: () => void
}

const formatPrLabel = (url: string, showRepoPrefix: boolean): string => {
  try {
    const parsed = new URL(url)
    const ghMatch = parsed.pathname.match(
      /^\/([^/]+\/[^/]+)\/pulls?\/(\d+)(?:\/|$)/i
    )
    if (ghMatch) {
      const repoShort = ghMatch[1].split('/').pop() || ghMatch[1]
      return showRepoPrefix ? `${repoShort}#${ghMatch[2]}` : `PR #${ghMatch[2]}`
    }
    const glMatch = parsed.pathname.match(
      /^\/(.+?)(?:\/-)?\/merge_requests\/(\d+)(?:\/|$)/i
    )
    if (glMatch) {
      const repoShort = glMatch[1].split('/').pop() || glMatch[1]
      return showRepoPrefix ? `${repoShort}!${glMatch[2]}` : `MR !${glMatch[2]}`
    }
  } catch {
    // Fallback below
  }
  const simplePr = url.match(/\/pulls?\/(\d+)/i)
  if (simplePr) return `PR #${simplePr[1]}`
  const simpleMr = url.match(/\/merge_requests\/(\d+)/i)
  if (simpleMr) return `MR !${simpleMr[1]}`
  return 'Open PR / MR'
}

export const SeedMetadataSidebar: React.FC<SeedMetadataSidebarProps> = ({
  seed,
  project,
  primaryRepo,
  repos = [],
  runs,
  changes = null,
  seedEvents = [],
  canContribute,
  autonomyNotice,
  onAutonomyChange,
  resolveActorName,
  onOpenPullRequest,
}) => {
  const { backends, navigateToDetail } = useData()
  const [receiptEnvelope, setReceiptEnvelope] =
    useState<GovernanceReceiptEnvelope | null>(null)
  const [isReceiptModalOpen, setIsReceiptModalOpen] = useState(false)
  const [isLoadingReceipt, setIsLoadingReceipt] = useState(false)
  const [receiptError, setReceiptError] = useState<string | null>(null)
  const [copiedReceipt, setCopiedReceipt] = useState(false)
  const [copiedVerifyCmd, setCopiedVerifyCmd] = useState(false)

  const spendEnforcement: BudgetEnforcement = useMemo(() => {
    if (seed.enforcement === 'unpriced') return 'unpriced'
    if (seed.enforcement === 'estimated') return 'estimated'
    const anyUnpricedRun = runs.some((r) => {
      const out = (r.output || {}) as Record<string, unknown>
      return (
        r.cost_enforcement === 'unpriced' ||
        r.enforcement === 'unpriced' ||
        out.cost_enforcement === 'unpriced' ||
        out.enforcement === 'unpriced'
      )
    })
    if (anyUnpricedRun) return 'unpriced'
    const anyEstimatedRun = runs.some((r) => {
      const out = (r.output || {}) as Record<string, unknown>
      return (
        r.cost_enforcement === 'estimated' ||
        r.enforcement === 'estimated' ||
        out.cost_enforcement === 'estimated' ||
        out.enforcement === 'estimated' ||
        out.auth_mode === 'cli_oauth'
      )
    })
    if (anyEstimatedRun) return 'estimated'
    const anyCliOAuthBackend = backends.some(
      (b) =>
        b.type === 'inference' &&
        b.enabled &&
        (b.config?.auth_mode === 'cli_oauth' ||
          b.secret_keys.includes('cli_auth_bundle') ||
          b.secret_keys.includes('oauth_token'))
    )
    return anyCliOAuthBackend ? 'estimated' : 'enforced'
  }, [backends, runs, seed.enforcement])

  const spendEnforcementDetail = useMemo(() => {
    if (seed.enforcement_detail) return seed.enforcement_detail
    for (const r of runs) {
      if (r.enforcement_detail) return r.enforcement_detail
      const out = (r.output || {}) as Record<string, unknown>
      if (typeof out.enforcement_detail === 'string' && out.enforcement_detail) {
        return out.enforcement_detail
      }
    }
    return null
  }, [runs, seed.enforcement_detail])

  const receiptFilename = `gitseed-receipt-${seed.id}.json`
  const verifyCommand = `gitseed receipt verify ${receiptFilename} --offline --public-key-file receipt-pub.pem`

  const handleFetchReceipt = async (openModal = true) => {
    setIsLoadingReceipt(true)
    setReceiptError(null)
    try {
      const res = await api.seeds.getReceipt(seed.id)
      setReceiptEnvelope(res)
      if (openModal) {
        setIsReceiptModalOpen(true)
      }
    } catch (err) {
      setReceiptError(
        err instanceof Error ? err.message : 'Failed to load governance receipt'
      )
    } finally {
      setIsLoadingReceipt(false)
    }
  }

  const handleCopyReceipt = async () => {
    if (!receiptEnvelope) return
    const { markdown: _md, ...cleanEnvelope } = receiptEnvelope
    try {
      await navigator.clipboard.writeText(JSON.stringify(cleanEnvelope, null, 2))
      setCopiedReceipt(true)
      window.setTimeout(() => setCopiedReceipt(false), 2000)
    } catch {
      // Ignore clipboard failure
    }
  }

  const handleCopyVerifyCommand = async () => {
    try {
      await navigator.clipboard.writeText(verifyCommand)
      setCopiedVerifyCmd(true)
      window.setTimeout(() => setCopiedVerifyCmd(false), 2000)
    } catch {
      // Ignore clipboard failure
    }
  }

  const handleDownloadReceipt = () => {
    if (!receiptEnvelope) return
    const { markdown: _md, ...cleanEnvelope } = receiptEnvelope
    const blob = new Blob([JSON.stringify(cleanEnvelope, null, 2) + '\n'], {
      type: 'application/json',
    })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = receiptFilename
    a.click()
    URL.revokeObjectURL(url)
  }

  const renderEnforcementBadge = (mode: BudgetEnforcement | string) => {
    if (mode === 'unpriced') {
      return (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border bg-zinc-500/15 text-zinc-700 dark:text-zinc-300 border-zinc-400/30">
          Unpriced
        </span>
      )
    }
    if (mode === 'estimated') {
      return (
        <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border bg-amber-500/15 text-amber-800 dark:text-amber-300 border-amber-500/30">
          Estimated
        </span>
      )
    }
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30">
        Enforced
      </span>
    )
  }

  const primaryGitBackend = useMemo(
    () =>
      primaryRepo
        ? backends.find((b) => b.id === primaryRepo.backend_id) || null
        : null,
    [primaryRepo, backends]
  )

  const seedBranch = useMemo(() => {
    const sorted = [...runs].sort(
      (a, b) =>
        new Date(b.created_at || 0).getTime() -
        new Date(a.created_at || 0).getTime()
    )
    for (const r of sorted) {
      const out = (r.output || {}) as Record<string, unknown>
      if (typeof out.branch_name === 'string' && out.branch_name.trim()) {
        return out.branch_name.trim()
      }
    }
    const hasReachedCodeStage =
      seed.stage === 'review' ||
      seed.stage === 'merge' ||
      seed.status === 'merged' ||
      (seed.stage === 'execute' &&
        runs.some((r) => r.stage === 'execute' && r.status === 'succeeded'))
    if (hasReachedCodeStage) {
      return project?.slug
        ? `gitseed/${project.slug}/seed-${seed.number}`
        : `gitseed/seed-${seed.number}`
    }
    return null
  }, [runs, project?.slug, seed.stage, seed.status, seed.number])

  const pullRequestUrls = useMemo(() => {
    const seen = new Set<string>()
    const addUrl = (val: unknown) => {
      if (typeof val === 'string' && val.trim()) {
        seen.add(val.trim())
      }
    }

    if (changes?.pull_request_url) {
      addUrl(changes.pull_request_url)
    }

    const sorted = [...runs].sort(
      (a, b) =>
        new Date(b.created_at || 0).getTime() -
        new Date(a.created_at || 0).getTime()
    )
    for (const r of sorted) {
      const out = (r.output || {}) as Record<string, unknown>
      addUrl(out.pull_request_url)
      if (Array.isArray(out.pull_request_urls)) {
        for (const item of out.pull_request_urls) {
          addUrl(item)
        }
      }
    }

    for (const ev of seedEvents) {
      const d = (ev.data || {}) as Record<string, unknown>
      addUrl(d.pull_request_url)
      addUrl(d.pr_url)
    }

    return Array.from(seen)
  }, [runs, changes?.pull_request_url, seedEvents])

  const openProjectCodeAtRef = (ref: string) => {
    const targetProjectSlug = project?.slug || seed.project_id
    const query: Record<string, string> = { ref }
    if (primaryRepo?.id) {
      query.repo = String(primaryRepo.id)
    }
    navigateToDetail(VIEWS.PROJECTS, targetProjectSlug, 'code', query)
  }

  return (
    <Card className="p-5 sm:p-6 min-w-0 overflow-hidden rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-4">
      <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 pb-2.5 border-b border-zinc-100 dark:border-zinc-800">
        Seed Details
      </h3>

      <dl className="flex flex-col gap-3 text-xs sm:text-[13px]">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
            Project
          </dt>
          <dd>
            {project ? (
              <button
                type="button"
                onClick={() =>
                  navigateToDetail(
                    VIEWS.PROJECTS,
                    project.slug || project.id
                  )
                }
                className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                {project.name}
              </button>
            ) : (
              <span className="font-mono text-zinc-700 dark:text-zinc-300">
                {seed.project_id}
              </span>
            )}
          </dd>
        </div>

        {primaryRepo && (
          <>
            <div className="flex items-center justify-between gap-2">
              <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
                Repository
              </dt>
              <dd className="inline-flex items-center gap-1.5 font-mono text-zinc-800 dark:text-zinc-200 min-w-0">
                <button
                  type="button"
                  onClick={() => openProjectCodeAtRef(primaryRepo.default_branch)}
                  title={`Browse ${primaryRepo.full_name} (${primaryRepo.default_branch}) in Code viewer`}
                  className="inline-flex items-center gap-1.5 text-teal-600 dark:text-teal-400 hover:underline truncate cursor-pointer"
                >
                  <FolderGit2 size={13} className="text-teal-500 shrink-0" />
                  <span className="truncate">{primaryRepo.full_name}</span>
                </button>
                <button
                  type="button"
                  onClick={() => openProjectCodeAtRef(primaryRepo.default_branch)}
                  title={`Browse default branch ${primaryRepo.default_branch} in Code viewer`}
                  className="inline-flex items-center gap-0.5 px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-[11px] text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer shrink-0"
                >
                  <GitBranch size={11} />
                  <span>{primaryRepo.default_branch}</span>
                </button>
                <a
                  href={buildRepoWebUrl(primaryRepo, primaryGitBackend, {
                    ref: primaryRepo.default_branch,
                  })}
                  target="_blank"
                  rel="noopener noreferrer"
                  title="Open repository on remote Git provider"
                  className="text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 transition shrink-0"
                >
                  <ExternalLink size={12} />
                </a>
              </dd>
            </div>

            {seedBranch && seedBranch !== primaryRepo.default_branch && (
              <div className="flex items-center justify-between gap-2">
                <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
                  Seed Branch
                </dt>
                <dd className="inline-flex items-center gap-1.5 min-w-0">
                  <button
                    type="button"
                    onClick={() => openProjectCodeAtRef(seedBranch)}
                    title={`Browse seed branch ${seedBranch} in Code viewer (falls back to ${primaryRepo.default_branch} if cleaned up)`}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/20 text-xs font-mono hover:bg-teal-500/20 transition truncate cursor-pointer"
                  >
                    <GitBranch size={11} className="shrink-0" />
                    <span className="truncate">{seedBranch}</span>
                  </button>
                  <a
                    href={buildRepoWebUrl(primaryRepo, primaryGitBackend, {
                      ref: seedBranch,
                    })}
                    target="_blank"
                    rel="noopener noreferrer"
                    title={`Open branch ${seedBranch} on remote Git provider`}
                    className="text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 transition shrink-0"
                  >
                    <ExternalLink size={12} />
                  </a>
                </dd>
              </div>
            )}

            {pullRequestUrls.length > 0 && (
              <div className="flex items-start justify-between gap-2">
                <dt className="text-zinc-500 dark:text-zinc-400 font-medium pt-0.5">
                  {pullRequestUrls.length > 1 ? 'Pull Requests' : 'Pull Request'}
                </dt>
                <dd className="flex flex-wrap items-center justify-end gap-1.5 min-w-0">
                  {pullRequestUrls.map((prUrl) => {
                    const label = formatPrLabel(
                      prUrl,
                      pullRequestUrls.length > 1 || repos.length > 1
                    )
                    return (
                      <span
                        key={prUrl}
                        className="inline-flex items-center gap-1 min-w-0"
                      >
                        {onOpenPullRequest ? (
                          <button
                            type="button"
                            onClick={onOpenPullRequest}
                            title={`Inspect ${label} in-app (diff, CI checks, and comments)`}
                            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/25 text-xs font-semibold hover:bg-emerald-500/20 transition truncate cursor-pointer"
                          >
                            <GitPullRequest size={11} className="shrink-0" />
                            <span className="truncate">{label}</span>
                          </button>
                        ) : null}
                        <a
                          href={prUrl}
                          target="_blank"
                          rel="noopener noreferrer"
                          title={`Open ${label} on remote Git provider`}
                          className={
                            onOpenPullRequest
                              ? 'text-zinc-400 hover:text-emerald-600 dark:hover:text-emerald-400 transition shrink-0'
                              : 'inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border border-emerald-500/25 text-xs font-semibold hover:bg-emerald-500/20 transition truncate'
                          }
                        >
                          {!onOpenPullRequest && (
                            <>
                              <GitPullRequest size={11} className="shrink-0" />
                              <span className="truncate">{label}</span>
                            </>
                          )}
                          <ExternalLink size={12} className="shrink-0" />
                        </a>
                      </span>
                    )
                  })}
                </dd>
              </div>
            )}
          </>
        )}

        <div className="flex flex-col gap-1.5">
          <div className="flex items-center justify-between gap-2">
            <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
              Autonomy
            </dt>
            <dd>
              {canContribute &&
              seed.status !== 'merged' &&
              seed.status !== 'closed' ? (
                <select
                  value={seed.autonomy}
                  onChange={(e) =>
                    onAutonomyChange(e.target.value as SeedAutonomy)
                  }
                  title={AUTONOMY_DESCRIPTIONS[seed.autonomy]}
                  className="px-2.5 py-1 rounded-lg text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-800 dark:text-zinc-200 cursor-pointer focus:outline-none focus:border-teal-500"
                >
                  <option value="manual">Manual</option>
                  <option value="supervised">Supervised</option>
                  <option value="autonomous">Autonomous</option>
                </select>
              ) : (
                <span className="font-semibold text-zinc-800 dark:text-zinc-200">
                  {AUTONOMY_LABELS[seed.autonomy]}
                </span>
              )}
            </dd>
          </div>

          {(autonomyNotice || seed.autonomy === 'autonomous') && (
            <div className="px-2.5 py-2 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 text-[11px] text-zinc-600 dark:text-zinc-300 leading-relaxed flex flex-col gap-0.5">
              {autonomyNotice && (
                <span className="font-medium text-teal-700 dark:text-teal-300">
                  {autonomyNotice}
                </span>
              )}
              {seed.autonomy === 'autonomous' && (
                <span>
                  Gates that allow auto-approval will pass without review.
                </span>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-2">
          <dt className="text-zinc-500 dark:text-zinc-400 font-medium flex items-center gap-1.5">
            <span>Inference Spend</span>
            <span
              data-testid="spend-enforcement-badge"
              title={
                spendEnforcementDetail ||
                (spendEnforcement === 'unpriced'
                  ? 'Custom or unpriced model without a known pricing catalog entry'
                  : spendEnforcement === 'estimated'
                    ? 'Estimated from CLI token reports in Personal Mode (bounded by timeout and max turns)'
                    : 'Enforced in real time via LiteLLM per-run virtual key max_budget')
              }
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase tracking-wide border ${
                spendEnforcement === 'unpriced'
                  ? 'bg-zinc-500/15 text-zinc-700 dark:text-zinc-300 border-zinc-400/30'
                  : spendEnforcement === 'estimated'
                    ? 'bg-amber-500/15 text-amber-800 dark:text-amber-300 border-amber-500/30'
                    : 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30'
              }`}
            >
              {spendEnforcement === 'unpriced'
                ? 'Unpriced'
                : spendEnforcement === 'estimated'
                  ? 'Estimated'
                  : 'Enforced'}
            </span>
          </dt>
          <dd className="font-mono font-semibold text-zinc-900 dark:text-zinc-100">
            {formatUsd(seed.spent)}
            {seed.budget_cap ? (
              <span className="text-zinc-400 font-normal">
                {' '}
                / {formatUsd(seed.budget_cap)}
              </span>
            ) : null}
          </dd>
        </div>

        {runs.length > 0 &&
          (() => {
            const totalIn = runs.reduce((acc, r) => acc + (r.input_tokens || 0), 0)
            const totalOut = runs.reduce(
              (acc, r) => acc + (r.output_tokens || 0),
              0
            )
            const totalTok = totalIn + totalOut
            return (
              <div className="flex items-center justify-between gap-2">
                <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
                  Token Usage
                </dt>
                <dd
                  title={`Input: ${totalIn.toLocaleString()} tok · Output: ${totalOut.toLocaleString()} tok`}
                  className="font-mono text-zinc-800 dark:text-zinc-200 text-right"
                >
                  <span className="font-semibold">
                    {totalTok.toLocaleString()} tok
                  </span>
                  {totalTok > 0 && (
                    <span className="text-zinc-400 text-[11px] ml-1.5">
                      ({totalIn.toLocaleString()} in · {totalOut.toLocaleString()}{' '}
                      out)
                    </span>
                  )}
                </dd>
              </div>
            )
          })()}

        <div className="flex items-center justify-between gap-2">
          <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
            Created By
          </dt>
          <dd className="text-zinc-800 dark:text-zinc-200 font-medium">
            {resolveActorName('user', seed.created_by)} ·{' '}
            <span className="text-zinc-500 dark:text-zinc-400 font-normal">
              {formatRelativeTime(seed.created_at)}
            </span>
          </dd>
        </div>

        <div className="flex items-center justify-between gap-2">
          <dt className="text-zinc-500 dark:text-zinc-400 font-medium">
            Last Updated
          </dt>
          <dd className="text-zinc-700 dark:text-zinc-300">
            {formatRelativeTime(seed.updated_at)}
          </dd>
        </div>

        {seed.tags.length > 0 && (
          <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800 flex flex-col gap-1.5">
            <dt className="text-zinc-500 dark:text-zinc-400 font-medium">Tags</dt>
            <dd className="flex items-center gap-1.5 flex-wrap">
              {seed.tags.map((tag) => (
                <span
                  key={tag}
                  className="px-2 py-0.5 rounded-md text-[11px] font-mono bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80"
                >
                  {tag}
                </span>
              ))}
            </dd>
          </div>
        )}

        <div
          data-testid="governance-receipt-card"
          className="pt-3 border-t border-zinc-100 dark:border-zinc-800 flex flex-col gap-2"
        >
          <div className="flex items-center justify-between gap-2">
            <span className="text-zinc-700 dark:text-zinc-200 font-semibold flex items-center gap-1.5">
              <ShieldCheck size={14} className="text-teal-600 dark:text-teal-400" />
              <span>Governance Receipt</span>
            </span>
            <button
              type="button"
              data-testid="fetch-governance-receipt-btn"
              onClick={() => {
                if (receiptEnvelope) {
                  setIsReceiptModalOpen(true)
                } else {
                  void handleFetchReceipt(true)
                }
              }}
              disabled={isLoadingReceipt}
              className="px-2.5 py-1 rounded-lg text-[11px] font-semibold bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 text-zinc-800 dark:text-zinc-200 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer disabled:opacity-50 inline-flex items-center gap-1"
            >
              {isLoadingReceipt ? (
                <>
                  <Loader2 size={11} className="animate-spin" />
                  <span>Signing…</span>
                </>
              ) : (
                <span>View Signed Receipt</span>
              )}
            </button>
          </div>

          {receiptError && (
            <p className="text-[11px] text-rose-600 dark:text-rose-400">{receiptError}</p>
          )}

          {receiptEnvelope && (
            <div className="p-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200/80 dark:border-zinc-700/80 flex flex-col gap-2 text-[11px]">
              <div className="flex items-center justify-between gap-2 flex-wrap">
                <span className="inline-flex items-center gap-1 font-semibold text-emerald-700 dark:text-emerald-300">
                  <Check size={12} />
                  <span>{receiptEnvelope.algorithm} Signed</span>
                </span>
                <span className="font-mono text-zinc-500 dark:text-zinc-400">
                  key:{receiptEnvelope.key_id}
                </span>
              </div>
              <div className="flex items-center justify-between gap-2 text-zinc-600 dark:text-zinc-300">
                <span>Spend Enforcement</span>
                <span className="font-mono font-semibold uppercase">
                  {spendEnforcement}
                </span>
              </div>
              {receiptEnvelope.receipt.artifacts && (
                <div className="flex items-center justify-between gap-2 text-zinc-600 dark:text-zinc-300">
                  <span>Locked Artifacts</span>
                  <span className="font-mono text-[10px]">
                    {[
                      receiptEnvelope.receipt.artifacts.specification_sha256 && 'spec',
                      receiptEnvelope.receipt.artifacts.acceptance_criteria_sha256 && 'criteria',
                      receiptEnvelope.receipt.artifacts.plan_sha256 && 'plan',
                    ]
                      .filter(Boolean)
                      .join(' · ') || 'none'}{' '}
                    ({receiptEnvelope.receipt.artifacts.task_count} tasks)
                  </span>
                </div>
              )}
              <div className="flex items-center gap-1.5 pt-1">
                <button
                  type="button"
                  onClick={() => setIsReceiptModalOpen(true)}
                  className="flex-1 px-2 py-1 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-200 font-semibold inline-flex items-center justify-center gap-1 hover:border-teal-500/40 transition cursor-pointer"
                >
                  <FileCheck2 size={11} />
                  <span>Inspect</span>
                </button>
                <button
                  type="button"
                  onClick={handleDownloadReceipt}
                  className="flex-1 px-2 py-1 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-200 font-semibold inline-flex items-center justify-center gap-1 hover:border-teal-500/40 transition cursor-pointer"
                >
                  <Download size={11} />
                  <span>Download</span>
                </button>
              </div>
            </div>
          )}
        </div>
      </dl>

      <Modal
        isOpen={isReceiptModalOpen && Boolean(receiptEnvelope)}
        onClose={() => setIsReceiptModalOpen(false)}
        size="3xl"
        title={
          <span className="inline-flex items-center gap-2">
            <ShieldCheck size={18} className="text-teal-600 dark:text-teal-400 shrink-0" />
            <span>Signed Governance Receipt · Seed #{seed.number}</span>
          </span>
        }
      >
        {receiptEnvelope && (
          <div
            data-testid="governance-receipt-modal"
            className="flex flex-col gap-5 text-xs"
          >
            {/* Cryptographic Signature Summary */}
            <div className="p-4 rounded-xl bg-emerald-500/[0.06] dark:bg-emerald-500/[0.1] border border-emerald-500/25 flex flex-col gap-2.5">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="inline-flex items-center gap-2 font-semibold text-sm text-emerald-800 dark:text-emerald-200">
                  <ShieldCheck size={16} className="text-emerald-600 dark:text-emerald-400" />
                  <span>{receiptEnvelope.algorithm} Cryptographic Attestation</span>
                </div>
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/80 dark:bg-zinc-900/80 border border-emerald-500/25 font-mono text-[11px] text-zinc-700 dark:text-zinc-200">
                    <KeyRound size={11} className="text-teal-600 dark:text-teal-400" />
                    <span>key_id: {receiptEnvelope.key_id}</span>
                  </span>
                  <button
                    type="button"
                    onClick={() => void handleFetchReceipt(false)}
                    disabled={isLoadingReceipt}
                    className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md bg-white/80 dark:bg-zinc-900/80 border border-zinc-200 dark:border-zinc-700 font-semibold text-[11px] text-zinc-700 dark:text-zinc-200 hover:border-teal-500/40 transition cursor-pointer disabled:opacity-50"
                  >
                    <RefreshCw size={11} className={isLoadingReceipt ? 'animate-spin' : ''} />
                    <span>Refresh</span>
                  </button>
                </div>
              </div>

              <div className="grid grid-cols-1 sm:grid-cols-2 gap-2 text-[11px] text-zinc-600 dark:text-zinc-300">
                <div>
                  <span className="text-zinc-500 dark:text-zinc-400">Schema:</span>{' '}
                  <code className="font-mono font-semibold text-zinc-800 dark:text-zinc-200">
                    {receiptEnvelope.receipt.schema_version}
                  </code>
                </div>
                <div>
                  <span className="text-zinc-500 dark:text-zinc-400">Generated:</span>{' '}
                  <span className="font-mono text-zinc-800 dark:text-zinc-200">
                    {receiptEnvelope.receipt.generated_at}
                  </span>
                </div>
              </div>

              <div className="flex flex-col gap-1">
                <span className="text-[11px] font-semibold text-zinc-600 dark:text-zinc-300">
                  Signature Preview (Base64URL)
                </span>
                <code
                  title={receiptEnvelope.signature}
                  className="px-2.5 py-1.5 rounded-lg bg-white/90 dark:bg-zinc-950/70 border border-zinc-200/80 dark:border-zinc-800 font-mono text-[11px] text-zinc-700 dark:text-zinc-300 break-all"
                >
                  {receiptEnvelope.signature.length > 64
                    ? `${receiptEnvelope.signature.slice(0, 36)}…${receiptEnvelope.signature.slice(-24)}`
                    : receiptEnvelope.signature}
                </code>
              </div>
            </div>

            {/* Locked Artifact SHA-256 Digests */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-600 dark:text-zinc-300">
                  Locked Artifact Digests (SHA-256)
                </h4>
                <span className="font-mono text-[11px] text-zinc-500 dark:text-zinc-400">
                  {receiptEnvelope.receipt.artifacts?.task_count ?? 0} planned{' '}
                  {(receiptEnvelope.receipt.artifacts?.task_count ?? 0) === 1 ? 'task' : 'tasks'}
                </span>
              </div>

              <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 divide-y divide-zinc-200 dark:divide-zinc-800 overflow-hidden bg-zinc-50/60 dark:bg-zinc-900/50">
                {[
                  {
                    label: 'Specification (spec)',
                    digest: receiptEnvelope.receipt.artifacts?.specification_sha256,
                  },
                  {
                    label: 'Acceptance Criteria',
                    digest:
                      receiptEnvelope.receipt.artifacts?.acceptance_criteria_sha256,
                  },
                  {
                    label: 'Implementation Plan',
                    digest: receiptEnvelope.receipt.artifacts?.plan_sha256,
                  },
                ].map((item) => (
                  <div
                    key={item.label}
                    className="px-3.5 py-2.5 flex flex-col sm:flex-row sm:items-center justify-between gap-1.5"
                  >
                    <span className="font-semibold text-zinc-700 dark:text-zinc-200">
                      {item.label}
                    </span>
                    {item.digest ? (
                      <code className="font-mono text-[11px] text-teal-700 dark:text-teal-300 break-all">
                        sha256:{item.digest}
                      </code>
                    ) : (
                      <span className="font-mono text-[11px] text-zinc-400 dark:text-zinc-500">
                        - (empty / not yet locked)
                      </span>
                    )}
                  </div>
                ))}
              </div>

              {(receiptEnvelope.receipt.artifacts?.tasks?.length || 0) > 0 && (
                <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 p-3 bg-white dark:bg-zinc-900/40 flex flex-col gap-1.5 max-h-36 overflow-y-auto">
                  <span className="text-[11px] font-semibold text-zinc-500 dark:text-zinc-400">
                    Recorded Execution Tasks ({receiptEnvelope.receipt.artifacts?.tasks.length})
                  </span>
                  {receiptEnvelope.receipt.artifacts?.tasks.map((task) => (
                    <div
                      key={task.id}
                      className="flex items-center justify-between gap-2 text-[11px]"
                    >
                      <span className="truncate text-zinc-800 dark:text-zinc-200">
                        <span className="font-mono text-zinc-400 mr-1.5">
                          #{task.order_index + 1}
                        </span>
                        {task.title}
                      </span>
                      <span className="font-mono uppercase text-[10px] px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 shrink-0">
                        {task.status}
                      </span>
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Participating Stage Runs */}
            <div className="flex flex-col gap-2">
              <div className="flex items-center justify-between gap-2">
                <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-600 dark:text-zinc-300">
                  Participating Stage Runs ({receiptEnvelope.receipt.runs.length})
                </h4>
              </div>

              {receiptEnvelope.receipt.runs.length === 0 ? (
                <div className="p-3 rounded-xl border border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400">
                  No stage runs recorded yet.
                </div>
              ) : (
                <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 overflow-x-auto">
                  <table className="w-full text-left border-collapse text-[11px]">
                    <thead>
                      <tr className="bg-zinc-50 dark:bg-zinc-800/70 text-zinc-500 dark:text-zinc-400 border-b border-zinc-200 dark:border-zinc-800">
                        <th className="py-2 px-3 font-semibold">Stage</th>
                        <th className="py-2 px-3 font-semibold">Runtime · Model</th>
                        <th className="py-2 px-3 font-semibold">Status</th>
                        <th className="py-2 px-3 font-semibold text-right">Tokens</th>
                        <th className="py-2 px-3 font-semibold text-right">Cost</th>
                        <th className="py-2 px-3 font-semibold text-right">Enforcement</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800">
                      {receiptEnvelope.receipt.runs.map((r) => (
                        <tr key={r.id} className="bg-white dark:bg-zinc-900/40">
                          <td className="py-2 px-3 font-mono font-semibold uppercase text-zinc-800 dark:text-zinc-200">
                            {r.stage}
                          </td>
                          <td className="py-2 px-3 font-mono text-zinc-700 dark:text-zinc-300">
                            <span className="font-semibold">{r.runtime}</span>
                            {r.model ? (
                              <span className="text-zinc-500 dark:text-zinc-400">
                                {' '}
                                · {r.model}
                              </span>
                            ) : null}
                          </td>
                          <td className="py-2 px-3 font-mono text-zinc-600 dark:text-zinc-300">
                            {r.status}
                          </td>
                          <td className="py-2 px-3 font-mono text-right text-zinc-600 dark:text-zinc-300">
                            {formatTokens((r.input_tokens || 0) + (r.output_tokens || 0))}
                          </td>
                          <td className="py-2 px-3 font-mono font-semibold text-right text-zinc-900 dark:text-zinc-100">
                            {formatUsd(r.cost_usd)}
                          </td>
                          <td className="py-2 px-3 text-right">
                            {renderEnforcementBadge(r.cost_enforcement)}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>

            {/* Stage Gate Approvals */}
            <div className="flex flex-col gap-2">
              <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-600 dark:text-zinc-300">
                Stage Gate Approvals ({receiptEnvelope.receipt.approvals.length})
              </h4>

              {receiptEnvelope.receipt.approvals.length === 0 ? (
                <div className="p-3 rounded-xl border border-zinc-200 dark:border-zinc-800 text-zinc-500 dark:text-zinc-400">
                  No stage gate approvals recorded yet.
                </div>
              ) : (
                <div className="rounded-xl border border-zinc-200 dark:border-zinc-800 divide-y divide-zinc-200 dark:divide-zinc-800 overflow-hidden">
                  {receiptEnvelope.receipt.approvals.map((app) => (
                    <div
                      key={app.id}
                      className="px-3.5 py-2.5 bg-white dark:bg-zinc-900/40 flex flex-col gap-1"
                    >
                      <div className="flex items-center justify-between gap-2 flex-wrap">
                        <div className="flex items-center gap-2">
                          <span className="font-mono font-semibold uppercase text-zinc-800 dark:text-zinc-200">
                            {app.stage}
                          </span>
                          <span
                            className={`px-1.5 py-0.5 rounded text-[10px] font-semibold uppercase ${
                              app.status === 'approved'
                                ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
                                : app.status === 'rejected'
                                  ? 'bg-rose-500/15 text-rose-700 dark:text-rose-300'
                                  : 'bg-amber-500/15 text-amber-800 dark:text-amber-300'
                            }`}
                          >
                            {app.status.replace('_', ' ')}
                          </span>
                        </div>
                        <div className="text-[11px] text-zinc-600 dark:text-zinc-300">
                          <span className="font-semibold">
                            {app.approver_name || app.approver_user_id || 'Automated Policy Gate'}
                          </span>
                          {app.decided_at && (
                            <span
                              title={app.decided_at}
                              className="text-zinc-400 dark:text-zinc-500 ml-1.5 font-mono"
                            >
                              · {formatRelativeTime(app.decided_at)}
                            </span>
                          )}
                        </div>
                      </div>
                      {app.comment && (
                        <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
                          {app.comment}
                        </p>
                      )}
                    </div>
                  ))}
                </div>
              )}
            </div>

            {/* Offline CLI Verification Command */}
            <div className="flex flex-col gap-1.5 p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-950/60 border border-zinc-200 dark:border-zinc-800">
              <div className="flex items-center justify-between gap-2">
                <span className="inline-flex items-center gap-1.5 text-[11px] font-bold text-zinc-700 dark:text-zinc-200">
                  <Terminal size={13} className="text-teal-600 dark:text-teal-400" />
                  <span>Independent CLI Verification</span>
                </span>
                <button
                  type="button"
                  onClick={() => void handleCopyVerifyCommand()}
                  className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {copiedVerifyCmd ? <Check size={11} /> : <Copy size={11} />}
                  <span>{copiedVerifyCmd ? 'Copied!' : 'Copy command'}</span>
                </button>
              </div>
              <code className="px-2.5 py-2 rounded-lg bg-zinc-900 text-zinc-100 font-mono text-[11px] overflow-x-auto">
                {verifyCommand}
              </code>
            </div>

            {/* Footer Actions */}
            <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-zinc-200 dark:border-zinc-800 flex-wrap">
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={() => void handleCopyReceipt()}
                className="rounded-xl text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
              >
                {copiedReceipt ? (
                  <Check size={13} className="text-emerald-500" />
                ) : (
                  <Copy size={13} />
                )}
                <span>{copiedReceipt ? 'Copied JSON' : 'Copy JSON'}</span>
              </Button>
              <Button
                type="button"
                variant="primary"
                size="sm"
                onClick={handleDownloadReceipt}
                className="rounded-xl text-xs font-semibold inline-flex items-center gap-1.5 cursor-pointer"
              >
                <Download size={13} />
                <span>Download receipt.json</span>
              </Button>
            </div>
          </div>
        )}
      </Modal>
    </Card>
  )
}
