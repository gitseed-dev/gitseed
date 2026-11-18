import React, { useEffect, useState } from 'react'
import { AlertCircle, GitBranch, Loader2, Plus, Sprout, Trash2, X } from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import type { SessionPushDraft, SessionPushResult } from '../../../types'

export interface SessionPushDialogProps {
  projectId: string
  kind: 'chat' | 'terminal'
  sessionId: string
  onClose: () => void
  onPushed: (result: SessionPushResult) => void
}

const STATUS_STYLES: Record<string, string> = {
  added: 'text-emerald-600 dark:text-emerald-400',
  modified: 'text-amber-600 dark:text-amber-400',
  deleted: 'text-rose-600 dark:text-rose-400',
}

/** Push → seed: review the drafted summary and acceptance criteria, then open a seed at Review. */
export const SessionPushDialog: React.FC<SessionPushDialogProps> = ({
  projectId,
  kind,
  sessionId,
  onClose,
  onPushed,
}) => {
  const [draft, setDraft] = useState<SessionPushDraft | null>(null)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [pushMode, setPushMode] = useState<'existing' | 'new'>('new')
  const [title, setTitle] = useState('')
  const [summary, setSummary] = useState('')
  const [criteria, setCriteria] = useState<string[]>([])
  const [pushing, setPushing] = useState(false)
  const [pushError, setPushError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    api.projects
      .draftSessionPush(projectId, kind, sessionId)
      .then((d) => {
        if (cancelled) return
        setDraft(d)
        setTitle(d.title)
        setSummary(d.summary)
        setCriteria(d.acceptance_criteria.length ? d.acceptance_criteria : [''])
        if (d.linked_seed_id) {
          setPushMode('existing')
        }
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof Error ? err.message : 'Could not read session changes')
      })
    return () => {
      cancelled = true
    }
  }, [projectId, kind, sessionId])

  const isExistingSeed = Boolean(draft?.linked_seed_id && pushMode === 'existing')
  const cleanCriteria = criteria.map((c) => c.trim()).filter(Boolean)
  const canSubmit = Boolean(
    draft &&
      title.trim() &&
      (isExistingSeed || cleanCriteria.length > 0) &&
      !pushing
  )

  const submit = async () => {
    if (!canSubmit) return
    setPushing(true)
    setPushError(null)
    try {
      const result = await api.projects.pushSession(projectId, kind, sessionId, {
        title: title.trim(),
        summary: summary.trim(),
        acceptance_criteria: cleanCriteria,
        target_seed_id: isExistingSeed ? draft?.linked_seed_id : null,
      })
      onPushed(result)
    } catch (err) {
      setPushError(err instanceof Error ? err.message : 'Push failed')
    } finally {
      setPushing(false)
    }
  }

  return (
    <div className="fixed inset-0 z-[60] bg-black/40 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-2xl max-h-[90vh] overflow-y-auto rounded-2xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-xl flex flex-col">
        <div className="px-5 py-4 border-b border-zinc-200 dark:border-zinc-800 flex items-start justify-between gap-3">
          <div>
            <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <Sprout size={16} className="text-teal-600" />
              Push to a branch and open a seed
            </h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
              {isExistingSeed
                ? 'Pushing session changes auto-fast-tracks the seed to Review so @reviewer audits the updated diff.'
                : 'The new seed starts directly at Review, skipping Ideate/Plan/Execute so Review and Merge gates apply.'}
            </p>
          </div>
          <button type="button" onClick={onClose} className="text-zinc-400 hover:text-zinc-700 cursor-pointer">
            <X size={16} />
          </button>
        </div>

        <div className="p-5 flex flex-col gap-4">
          {!draft && !loadError && (
            <div className="py-10 flex items-center justify-center gap-2 text-sm text-zinc-500">
              <Loader2 size={16} className="animate-spin" />
              Reading changes and drafting a summary…
            </div>
          )}
          {loadError && (
            <div className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300 text-xs flex items-center gap-2">
              <AlertCircle size={14} className="shrink-0" />
              {loadError}
            </div>
          )}

          {draft && (
            <>
              {draft.linked_seed_id && (
                <div className="inline-flex self-start rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 text-xs">
                  <button
                    type="button"
                    onClick={() => setPushMode('existing')}
                    className={`px-3 py-1 rounded-md font-semibold cursor-pointer ${
                      pushMode === 'existing'
                        ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                        : 'text-zinc-500'
                    }`}
                  >
                    Update Seed #{draft.linked_seed_number}
                  </button>
                  <button
                    type="button"
                    onClick={() => setPushMode('new')}
                    className={`px-3 py-1 rounded-md font-semibold cursor-pointer ${
                      pushMode === 'new'
                        ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                        : 'text-zinc-500'
                    }`}
                  >
                    Open new seed at Review
                  </button>
                </div>
              )}

              <div className="text-xs text-zinc-600 dark:text-zinc-300 flex flex-col gap-1.5">
                <span className="inline-flex items-center gap-1.5 font-mono">
                  <GitBranch size={12} />
                  {isExistingSeed ? draft.linked_seed_branch || draft.branch : draft.branch}
                  <span className="text-zinc-400 font-sans">· {draft.changed_files.length} changed file(s)</span>
                </span>
                <div className="max-h-32 overflow-y-auto rounded-lg border border-zinc-200 dark:border-zinc-800 bg-zinc-50 dark:bg-zinc-950 p-2 font-mono text-xs">
                  {draft.changed_files.length === 0 ? (
                    <span className="text-zinc-500">No changes found in the workspace.</span>
                  ) : (
                    draft.changed_files.map((f) => (
                      <div key={f.path} className="flex gap-2">
                        <span className={`w-16 shrink-0 ${STATUS_STYLES[f.status] || ''}`}>{f.status}</span>
                        <span className="truncate">{f.path}</span>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <label className="flex flex-col gap-1 text-xs">
                <span className="font-semibold text-zinc-600 dark:text-zinc-400">
                  {isExistingSeed ? 'Commit message' : 'Title'}
                </span>
                <input
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="px-3 py-2 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-sm text-zinc-900 dark:text-zinc-100"
                />
              </label>

              <label className="flex flex-col gap-1 text-xs">
                <span className="font-semibold text-zinc-600 dark:text-zinc-400">
                  {isExistingSeed ? 'Details (optional)' : 'Summary'}
                </span>
                <textarea
                  rows={isExistingSeed ? 3 : 5}
                  value={summary}
                  onChange={(e) => setSummary(e.target.value)}
                  className="px-3 py-2 rounded-xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-sm text-zinc-900 dark:text-zinc-100"
                />
              </label>

              {!isExistingSeed && (
                <div className="flex flex-col gap-1.5 text-xs">
                  <span className="font-semibold text-zinc-600 dark:text-zinc-400">
                    Acceptance criteria <span className="font-normal text-zinc-400">(at least one; Review checks against these)</span>
                  </span>
                  {criteria.map((c, i) => (
                    <div key={i} className="flex items-center gap-2">
                      <input
                        value={c}
                        onChange={(e) =>
                          setCriteria((prev) => prev.map((p, idx) => (idx === i ? e.target.value : p)))
                        }
                        className="flex-1 px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-sm text-zinc-900 dark:text-zinc-100"
                      />
                      <button
                        type="button"
                        onClick={() => setCriteria((prev) => prev.filter((_, idx) => idx !== i))}
                        className="p-1 text-zinc-400 hover:text-rose-600 cursor-pointer"
                        title="Remove"
                      >
                        <Trash2 size={13} />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={() => setCriteria((prev) => [...prev, ''])}
                    className="self-start inline-flex items-center gap-1 text-teal-700 dark:text-teal-300 font-semibold cursor-pointer"
                  >
                    <Plus size={12} />
                    Add criterion
                  </button>
                </div>
              )}

              {pushError && (
                <div className="p-3 rounded-xl border border-rose-500/30 bg-rose-500/10 text-rose-700 dark:text-rose-300 text-xs flex items-center gap-2">
                  <AlertCircle size={14} className="shrink-0" />
                  {pushError}
                </div>
              )}
            </>
          )}
        </div>

        <div className="px-5 py-3 border-t border-zinc-200 dark:border-zinc-800 flex items-center justify-end gap-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            onClick={onClose}
            className="rounded-xl text-xs font-semibold border border-zinc-200 dark:border-zinc-700 cursor-pointer"
          >
            Cancel
          </Button>
          <Button
            type="button"
            size="sm"
            variant="primary"
            isDisabled={!canSubmit || (draft?.changed_files.length ?? 0) === 0}
            onClick={() => void submit()}
            className="rounded-xl bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
          >
            {pushing ? <Loader2 size={13} className="animate-spin" /> : <Sprout size={13} />}
            {isExistingSeed
              ? `Push to Seed #${draft?.linked_seed_number}`
              : 'Push and open seed'}
          </Button>
        </div>
      </div>
    </div>
  )
}

