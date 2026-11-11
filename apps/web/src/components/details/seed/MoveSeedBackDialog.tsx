import React, { useEffect, useMemo, useState } from 'react'
import { Loader2, Undo2 } from 'lucide-react'
import { Button } from '@heroui/react'
import { api, ApiError } from '../../../api/client'
import { SEED_STAGE_ORDER, STAGE_LABELS } from '../../../utils/seeds'
import { Modal } from '../../common/Modal'
import type { Agent, Seed, SeedStage, StagePolicy } from '../../../types'

export interface MoveSeedBackDialogProps {
  isOpen: boolean
  onClose: () => void
  seed: Seed
  policies: StagePolicy[]
  agents: Agent[]
  onMoved: () => Promise<void> | void
}

/**
 * Send a seed back to an earlier stage with a reason. Approvals from that stage on reset,
 * and the reason is posted to the stage agent so its re-run knows what to change.
 */
export const MoveSeedBackDialog: React.FC<MoveSeedBackDialogProps> = ({
  isOpen,
  onClose,
  seed,
  policies,
  agents,
  onMoved,
}) => {
  const earlierStages = useMemo(() => {
    const idx = SEED_STAGE_ORDER.indexOf(seed.stage)
    return SEED_STAGE_ORDER.slice(0, Math.max(0, idx))
      .filter((st) => {
        const pol = policies.find((p) => p.stage === st)
        return st === 'execute' || !pol || pol.enabled
      })
      .reverse() // nearest first
  }, [seed.stage, policies])

  const [target, setTarget] = useState<SeedStage | null>(earlierStages[0] ?? null)
  const [reason, setReason] = useState('')
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!isOpen) return
    setTarget(earlierStages[0] ?? null)
    setReason('')
    setError(null)
  }, [isOpen, earlierStages])

  const targetPolicy = policies.find((p) => p.stage === target)
  const targetAgent = targetPolicy?.agent_id ? agents.find((a) => a.id === targetPolicy.agent_id) : undefined

  const submit = async () => {
    if (!target || !reason.trim()) return
    setSaving(true)
    setError(null)
    try {
      await api.seeds.moveBack(seed.id, { stage: target, reason: reason.trim() })
      await onMoved()
      onClose()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : err instanceof Error ? err.message : 'Could not move the seed back')
    } finally {
      setSaving(false)
    }
  }

  return (
    <Modal isOpen={isOpen} onClose={() => !saving && onClose()} title="Move back to an earlier stage" size="md">
      <div className="flex flex-col gap-4">
        {earlierStages.length === 0 ? (
          <p className="text-sm text-zinc-500 dark:text-zinc-400">There's no earlier enabled stage to move back to.</p>
        ) : (
          <>
            <div className="flex flex-col gap-1.5">
              <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">Move to</span>
              <div className="flex flex-wrap gap-1.5">
                {earlierStages.map((st) => (
                  <button
                    key={st}
                    type="button"
                    onClick={() => setTarget(st)}
                    className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition cursor-pointer ${
                      target === st
                        ? 'border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300'
                        : 'border-zinc-200 dark:border-zinc-700/80 text-zinc-600 dark:text-zinc-300 hover:bg-zinc-50 dark:hover:bg-zinc-800'
                    }`}
                  >
                    {STAGE_LABELS[st]}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">What should change?</label>
              <textarea
                autoFocus
                rows={4}
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                maxLength={4000}
                placeholder={
                  target === 'ideate'
                    ? 'e.g. Scope changed: also support CSV export, drop the PDF part.'
                    : target === 'plan'
                      ? 'e.g. Use the existing job queue instead of adding a new worker.'
                      : 'e.g. The retry logic should back off exponentially.'
                }
                className="w-full p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500 resize-y"
              />
            </div>

            <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
              The branch, commits, spec and plan are kept.{' '}
              {targetAgent ? `@${targetAgent.handle}` : 'The stage agent'} re-runs{' '}
              {target ? STAGE_LABELS[target] : 'the stage'} with your note, and approvals from there on reset, so each
              gate passes again.
            </p>
          </>
        )}

        {error && (
          <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs text-rose-600 dark:text-rose-400">
            {error}
          </div>
        )}

        <div className="flex items-center justify-end gap-2 pt-1">
          <Button variant="secondary" size="sm" onPress={onClose} isDisabled={saving} className="cursor-pointer">
            Cancel
          </Button>
          <Button
            variant="primary"
            size="sm"
            onPress={() => void submit()}
            isDisabled={saving || !target || !reason.trim()}
            className="cursor-pointer font-semibold flex items-center gap-1.5"
          >
            {saving ? <Loader2 size={13} className="animate-spin" /> : <Undo2 size={13} />}
            {target ? `Move to ${STAGE_LABELS[target]}` : 'Move back'}
          </Button>
        </div>
      </div>
    </Modal>
  )
}
