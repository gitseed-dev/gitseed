import React, { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  Copy,
  GitPullRequest,
  KeyRound,
  RefreshCw,
  Send,
  Webhook,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import type { Project, ProjectWebhookConfig } from '../../../types'

export interface ProjectWebhooksCardProps {
  project: Project
  canManage: boolean
}

const OUTBOUND_EVENT_OPTIONS: Array<{ id: string; label: string }> = [
  { id: 'seed.created', label: 'Seed Created' },
  { id: 'seed.awaiting_approval', label: 'Seed Awaiting Approval' },
  { id: 'seed.merged', label: 'Seed Merged' },
  { id: 'run.failed', label: 'Run Failed' },
]

export const ProjectWebhooksCard: React.FC<ProjectWebhooksCardProps> = ({
  project,
  canManage,
}) => {
  const queryClient = useQueryClient()
  const { data: webhookCfg, isLoading } = useQuery<ProjectWebhookConfig>({
    queryKey: ['projects', project.id, 'webhooks'],
    queryFn: () => api.projects.getWebhooks(project.id),
  })

  const [enabled, setEnabled] = useState(false)
  const [issueLabelTrigger, setIssueLabelTrigger] = useState('gitseed')
  const [autoCreateSeedOnIssue, setAutoCreateSeedOnIssue] = useState(true)
  const [syncPrComments, setSyncPrComments] = useState(true)
  const [autoRemediateCi, setAutoRemediateCi] = useState(true)
  const [autoCreatePr, setAutoCreatePr] = useState(true)
  const [outboundUrl, setOutboundUrl] = useState('')
  const [outboundEvents, setOutboundEvents] = useState<string[]>([
    'seed.created',
    'seed.awaiting_approval',
    'seed.merged',
    'run.failed',
  ])

  const [revealedSecret, setRevealedSecret] = useState<string | null>(null)
  const [copiedKey, setCopiedKey] = useState<string | null>(null)
  const [isSaving, setIsSaving] = useState(false)
  const [isRotating, setIsRotating] = useState(false)
  const [isTesting, setIsTesting] = useState(false)
  const [statusNotice, setStatusNotice] = useState<string | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)

  useEffect(() => {
    if (!webhookCfg) return
    setEnabled(webhookCfg.enabled)
    setIssueLabelTrigger(webhookCfg.issue_label_trigger || 'gitseed')
    setAutoCreateSeedOnIssue(webhookCfg.auto_create_seed_on_issue)
    setSyncPrComments(webhookCfg.sync_pr_comments)
    setAutoRemediateCi(webhookCfg.auto_remediate_ci)
    setAutoCreatePr(webhookCfg.auto_create_pr)
    setOutboundUrl(webhookCfg.outbound_url || '')
    setOutboundEvents(webhookCfg.outbound_events || [])
  }, [webhookCfg])

  const copyText = (key: string, value: string) => {
    navigator.clipboard.writeText(value).catch(() => {})
    setCopiedKey(key)
    window.setTimeout(() => setCopiedKey(null), 2000)
  }

  const flashNotice = (msg: string) => {
    setStatusNotice(msg)
    window.setTimeout(() => setStatusNotice(null), 3500)
  }

  const isDirty =
    !!webhookCfg &&
    (enabled !== webhookCfg.enabled ||
      issueLabelTrigger.trim() !== (webhookCfg.issue_label_trigger || 'gitseed') ||
      autoCreateSeedOnIssue !== webhookCfg.auto_create_seed_on_issue ||
      syncPrComments !== webhookCfg.sync_pr_comments ||
      autoRemediateCi !== webhookCfg.auto_remediate_ci ||
      autoCreatePr !== webhookCfg.auto_create_pr ||
      outboundUrl.trim() !== (webhookCfg.outbound_url || '') ||
      [...outboundEvents].sort().join(',') !==
        [...(webhookCfg.outbound_events || [])].sort().join(','))

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!canManage) return
    setIsSaving(true)
    setErrorMsg(null)
    try {
      const updated = await api.projects.updateWebhooks(project.id, {
        enabled,
        issue_label_trigger: issueLabelTrigger.trim() || 'gitseed',
        auto_create_seed_on_issue: autoCreateSeedOnIssue,
        sync_pr_comments: syncPrComments,
        auto_remediate_ci: autoRemediateCi,
        auto_create_pr: autoCreatePr,
        outbound_url: outboundUrl.trim() || null,
        outbound_events: outboundEvents,
      })
      if (updated.secret) {
        setRevealedSecret(updated.secret)
      }
      queryClient.setQueryData(['projects', project.id, 'webhooks'], updated)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
        queryClient.invalidateQueries({
          queryKey: queryKeys.projectDetail(project.id),
        }),
      ])
      flashNotice('Webhook & PR automation settings saved.')
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Failed to save webhook settings.'
      )
    } finally {
      setIsSaving(false)
    }
  }

  const handleRotateSecret = async () => {
    if (!canManage) return
    setIsRotating(true)
    setErrorMsg(null)
    try {
      const updated = await api.projects.rotateWebhookSecret(project.id)
      if (updated.secret) {
        setRevealedSecret(updated.secret)
      }
      queryClient.setQueryData(['projects', project.id, 'webhooks'], updated)
      flashNotice('New HMAC webhook signing secret generated.')
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Failed to rotate webhook secret.'
      )
    } finally {
      setIsRotating(false)
    }
  }

  const handleTestOutbound = async () => {
    if (!canManage) return
    setIsTesting(true)
    setErrorMsg(null)
    try {
      const res = await api.projects.testWebhook(project.id)
      if (res.delivered) {
        flashNotice(`Test webhook delivered (${res.detail}).`)
      } else {
        setErrorMsg(`Webhook test failed: ${res.detail}`)
      }
    } catch (err) {
      setErrorMsg(
        err instanceof Error ? err.message : 'Failed to send test webhook.'
      )
    } finally {
      setIsTesting(false)
    }
  }

  const toggleOutboundEvent = (eventId: string) => {
    if (!canManage) return
    setOutboundEvents((prev) =>
      prev.includes(eventId)
        ? prev.filter((id) => id !== eventId)
        : [...prev, eventId]
    )
  }

  const githubUrl =
    webhookCfg?.github_webhook_url ||
    `${window.location.origin}/api/v1/webhooks/github/${project.slug}`
  const gitlabUrl =
    webhookCfg?.gitlab_webhook_url ||
    `${window.location.origin}/api/v1/webhooks/gitlab/${project.slug}`

  return (
    <form
      id="project-settings-webhooks"
      onSubmit={handleSave}
      className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 rounded-2xl shadow-xs overflow-hidden"
    >
      <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <Webhook size={16} />
          </div>
          <div>
            <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
              Git Webhooks &amp; PR Automation
            </h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Automate issue-to-Seed intake, live PR comment steering, CI failure self-healing, and Pull Request creation
            </p>
          </div>
        </div>
        {canManage && (
          <Button
            type="submit"
            variant="primary"
            size="sm"
            isDisabled={isSaving || isLoading || !isDirty}
            className="rounded-xl font-semibold cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
          >
            {isSaving ? 'Saving...' : 'Save'}
          </Button>
        )}
      </div>

      <div className="p-6 flex flex-col gap-5">
        {errorMsg && (
          <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
            <AlertCircle size={14} className="shrink-0" />
            <span>{errorMsg}</span>
          </div>
        )}

        {statusNotice && (
          <div className="p-3 rounded-xl bg-emerald-500/10 border border-emerald-500/30 text-xs text-emerald-700 dark:text-emerald-300 font-semibold flex items-center gap-2">
            <Check size={14} className="shrink-0" />
            <span>{statusNotice}</span>
          </div>
        )}

        {/* Enable Inbound/Outbound Webhooks & Auto-PR Toggles */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
          <label className="flex items-start gap-3 p-3.5 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
            <input
              type="checkbox"
              checked={enabled}
              disabled={!canManage}
              onChange={(e) => setEnabled(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
            />
            <div className="flex flex-col">
              <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                Enable Project Webhooks
              </span>
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Accept signed GitHub/GitLab webhook events and deliver outbound notifications
              </span>
            </div>
          </label>

          <label className="flex items-start gap-3 p-3.5 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 cursor-pointer">
            <input
              type="checkbox"
              checked={autoCreatePr}
              disabled={!canManage}
              onChange={(e) => setAutoCreatePr(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
            />
            <div className="flex flex-col">
              <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                <GitPullRequest size={12} className="text-teal-500" />
                <span>Auto-Open Pull Request / Merge Request on Review</span>
              </span>
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Automatically open a GitHub PR or GitLab MR when entering Review, post @reviewer findings, and reconcile external PR merges
              </span>
            </div>
          </label>
        </div>

        {/* Receiver Endpoints & Signing Secret */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              GitHub Webhook Payload URL
            </label>
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                readOnly
                value={githubUrl}
                className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-zinc-100/80 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-700 dark:text-zinc-300"
              />
              <button
                type="button"
                onClick={() => copyText('gh', githubUrl)}
                className="px-2.5 py-2 rounded-xl bg-zinc-100 dark:bg-zinc-800 hover:bg-teal-500/15 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 cursor-pointer"
                title="Copy GitHub Webhook URL"
              >
                {copiedKey === 'gh' ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
              </button>
            </div>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              GitLab Webhook Payload URL
            </label>
            <div className="flex items-center gap-1.5">
              <input
                type="text"
                readOnly
                value={gitlabUrl}
                className="w-full px-3 py-2 text-xs font-mono rounded-xl bg-zinc-100/80 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-700 dark:text-zinc-300"
              />
              <button
                type="button"
                onClick={() => copyText('gl', gitlabUrl)}
                className="px-2.5 py-2 rounded-xl bg-zinc-100 dark:bg-zinc-800 hover:bg-teal-500/15 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 cursor-pointer"
                title="Copy GitLab Webhook URL"
              >
                {copiedKey === 'gl' ? <Check size={13} className="text-emerald-500" /> : <Copy size={13} />}
              </button>
            </div>
          </div>
        </div>

        {/* HMAC Signing Secret */}
        <div className="p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex flex-col gap-0.5">
            <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
              <KeyRound size={13} className="text-teal-500" />
              <span>Webhook HMAC Signing Secret</span>
            </span>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              Used to verify <code className="font-mono">X-Hub-Signature-256</code> (GitHub), <code className="font-mono">X-Gitlab-Token</code> (GitLab), and sign outbound payloads.
            </span>
          </div>

          <div className="flex items-center gap-2 shrink-0">
            {revealedSecret ? (
              <div className="flex items-center gap-1.5">
                <code className="px-2.5 py-1.5 rounded-lg bg-emerald-500/10 border border-emerald-500/30 text-emerald-700 dark:text-emerald-300 text-xs font-mono">
                  {revealedSecret}
                </code>
                <button
                  type="button"
                  onClick={() => copyText('sec', revealedSecret)}
                  className="px-2.5 py-1.5 rounded-lg bg-zinc-100 dark:bg-zinc-800 text-xs font-semibold text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-zinc-700 cursor-pointer"
                >
                  {copiedKey === 'sec' ? 'Copied' : 'Copy'}
                </button>
              </div>
            ) : (
              <span className="px-2.5 py-1 rounded-lg bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-xs font-mono text-zinc-600 dark:text-zinc-300">
                {webhookCfg?.has_secret
                  ? webhookCfg.secret_preview || '••••••••'
                  : 'Generated on save'}
              </span>
            )}
            {canManage && (
              <button
                type="button"
                disabled={isRotating}
                onClick={handleRotateSecret}
                className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-semibold bg-white dark:bg-zinc-800 hover:bg-teal-500/10 text-zinc-700 dark:text-zinc-200 border border-zinc-200 dark:border-zinc-700 cursor-pointer disabled:opacity-50"
              >
                <RefreshCw size={12} className={isRotating ? 'animate-spin' : ''} />
                <span>{webhookCfg?.has_secret ? 'Rotate Secret' : 'Generate Secret'}</span>
              </button>
            )}
          </div>
        </div>

        {/* Inbound Event Triggers */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-3">
          <div className="p-3.5 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 flex flex-col justify-between gap-2.5">
            <label className="flex items-start gap-2.5 cursor-pointer">
              <input
                type="checkbox"
                checked={autoCreateSeedOnIssue}
                disabled={!canManage}
                onChange={(e) => setAutoCreateSeedOnIssue(e.target.checked)}
                className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
              />
              <div className="flex flex-col">
                <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                  Issue → Seed Intake
                </span>
                <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                  Create a Seed when an issue is labeled with trigger tag
                </span>
              </div>
            </label>
            <div className="flex items-center gap-1.5 pt-1">
              <span className="text-[11px] font-semibold text-zinc-500 dark:text-zinc-400">
                Label:
              </span>
              <input
                type="text"
                value={issueLabelTrigger}
                disabled={!canManage || !autoCreateSeedOnIssue}
                onChange={(e) => setIssueLabelTrigger(e.target.value)}
                placeholder="gitseed"
                className="w-full px-2.5 py-1 text-xs font-mono rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200"
              />
            </div>
          </div>

          <label className="p-3.5 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 flex items-start gap-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={syncPrComments}
              disabled={!canManage}
              onChange={(e) => setSyncPrComments(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
            />
            <div className="flex flex-col">
              <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                PR Comment → Live Steering
              </span>
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Sync PR/MR comments to the Seed thread and steer active <code className="font-mono">@coder</code> / <code className="font-mono">@reviewer</code> runs
              </span>
            </div>
          </label>

          <label className="p-3.5 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200/80 dark:border-zinc-700/70 flex items-start gap-2.5 cursor-pointer">
            <input
              type="checkbox"
              checked={autoRemediateCi}
              disabled={!canManage}
              onChange={(e) => setAutoRemediateCi(e.target.checked)}
              className="mt-0.5 h-4 w-4 rounded accent-teal-600 cursor-pointer"
            />
            <div className="flex flex-col">
              <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100">
                CI Failure → Self-Healing
              </span>
              <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
                Automatically queue a remediation task in Execute when GitHub Actions or GitLab CI fails on a seed branch
              </span>
            </div>
          </label>
        </div>

        {/* Outbound Webhook Notifications */}
        <div className="pt-2 border-t border-zinc-200/70 dark:border-zinc-800 flex flex-col gap-3">
          <div className="flex flex-col sm:flex-row sm:items-end gap-3">
            <div className="flex-1 flex flex-col gap-1.5">
              <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
                Outbound Notification Webhook URL (Optional)
              </label>
              <input
                type="url"
                value={outboundUrl}
                disabled={!canManage}
                onChange={(e) => setOutboundUrl(e.target.value)}
                placeholder="https://hooks.slack.com/services/... or https://example.com/webhooks/gitseed"
                className="w-full px-3.5 py-2 text-xs font-mono rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 disabled:opacity-60"
              />
            </div>
            {canManage && outboundUrl.trim() && (
              <button
                type="button"
                disabled={isTesting || isDirty}
                onClick={handleTestOutbound}
                title={
                  isDirty
                    ? 'Save webhook settings first before testing'
                    : 'Send signed test event'
                }
                className="inline-flex items-center gap-1.5 px-3 py-2 rounded-xl text-xs font-semibold bg-teal-500/15 hover:bg-teal-500/25 text-teal-700 dark:text-teal-300 border border-teal-500/30 cursor-pointer disabled:opacity-50 shrink-0"
              >
                <Send size={12} />
                <span>{isTesting ? 'Sending...' : 'Send Test Ping'}</span>
              </button>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-3">
            <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-400">
              Outbound Events:
            </span>
            {OUTBOUND_EVENT_OPTIONS.map((opt) => (
              <label
                key={opt.id}
                className="inline-flex items-center gap-1.5 text-xs text-zinc-700 dark:text-zinc-300 cursor-pointer"
              >
                <input
                  type="checkbox"
                  checked={outboundEvents.includes(opt.id)}
                  disabled={!canManage}
                  onChange={() => toggleOutboundEvent(opt.id)}
                  className="h-3.5 w-3.5 rounded accent-teal-600 cursor-pointer"
                />
                <span>{opt.label}</span>
              </label>
            ))}
          </div>
        </div>
      </div>
    </form>
  )
}

