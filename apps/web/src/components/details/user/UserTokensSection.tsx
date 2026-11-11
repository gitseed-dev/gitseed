import React, { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Check, Copy, Plus, Trash2 } from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { ConfirmDialog } from '../../common/ConfirmDialog'
import { useTranslation } from '../../../context/I18nContext'
import { formatRelativeTime } from '../../../utils/format'
import type { PersonalAccessToken, PersonalAccessTokenCreated } from '../../../types'

interface UserTokensSectionProps {
  tokens: PersonalAccessToken[]
  maxPatLifetimeDays: number
  onError: (msg: string | null) => void
  onDialogOpenChange?: (isOpen: boolean) => void
}

export const UserTokensSection: React.FC<UserTokensSectionProps> = ({
  tokens,
  maxPatLifetimeDays,
  onError,
  onDialogOpenChange,
}) => {
  const queryClient = useQueryClient()
  const { t } = useTranslation()

  const [isCreatingToken, setIsCreatingToken] = useState(false)
  const [newTokenName, setNewTokenName] = useState('')
  const [newTokenDays, setNewTokenDays] = useState<number>(90)
  const [createdPat, setCreatedPat] = useState<PersonalAccessTokenCreated | null>(null)
  const [copiedPat, setCopiedPat] = useState(false)
  const [tokenToRevoke, setTokenToRevoke] = useState<PersonalAccessToken | null>(null)

  const patDayOptions = Array.from(
    new Set(
      [30, 90, 180, 365]
        .filter((d) => d <= maxPatLifetimeDays)
        .concat(maxPatLifetimeDays)
    )
  ).sort((a, b) => a - b)

  const effectiveTokenDays = patDayOptions.includes(newTokenDays)
    ? newTokenDays
    : patDayOptions[patDayOptions.length - 1] || maxPatLifetimeDays

  const activeTokens = tokens.filter((pat) => !pat.revoked_at)

  const handleOpenRevokeConfirm = (pat: PersonalAccessToken) => {
    setTokenToRevoke(pat)
    onDialogOpenChange?.(true)
  }

  const handleCloseRevokeConfirm = () => {
    setTokenToRevoke(null)
    onDialogOpenChange?.(false)
  }

  const handleCreateToken = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTokenName.trim()) return
    onError(null)
    try {
      const created = await api.auth.createToken(newTokenName.trim(), effectiveTokenDays)
      setCreatedPat(created)
      setNewTokenName('')
      setIsCreatingToken(false)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.tokens }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to create access token.')
    }
  }

  const handleConfirmRevokeToken = async () => {
    if (!tokenToRevoke) return
    const targetId = tokenToRevoke.id
    handleCloseRevokeConfirm()
    onError(null)
    try {
      await api.auth.revokeToken(targetId)
      if (createdPat?.id === targetId) {
        setCreatedPat(null)
      }
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.tokens }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
    } catch (err) {
      onError(err instanceof Error ? err.message : 'Failed to revoke access token.')
    }
  }

  return (
    <div className="pt-4 border-t border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Personal Access Tokens (CLI & API)
          </h3>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
            Authenticate the <code className="font-mono">gitseed</code> CLI and automation scripts.
          </p>
        </div>
        {!isCreatingToken && (
          <button
            type="button"
            onClick={() => {
              setCreatedPat(null)
              setIsCreatingToken(true)
            }}
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:text-teal-700 dark:hover:text-teal-300 cursor-pointer shrink-0"
          >
            <Plus size={14} />
            <span>New token</span>
          </button>
        )}
      </div>

      {createdPat && (
        <div className="p-3.5 rounded-xl border border-emerald-500/30 bg-emerald-500/5 flex flex-col gap-2">
          <div className="text-xs font-medium text-emerald-700 dark:text-emerald-300">
            Copy your new token now (it will not be shown again):
          </div>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              value={createdPat.token}
              onFocus={(e) => e.currentTarget.select()}
              className="flex-1 font-mono text-xs bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 py-2 text-zinc-800 dark:text-zinc-200 focus:outline-none"
            />
            <Button
              variant="primary"
              size="sm"
              type="button"
              onClick={async () => {
                await navigator.clipboard?.writeText(createdPat.token)
                setCopiedPat(true)
                window.setTimeout(() => setCopiedPat(false), 2000)
              }}
              className="shrink-0 flex items-center gap-1 rounded-lg text-xs font-semibold cursor-pointer"
            >
              {copiedPat ? <Check size={13} /> : <Copy size={13} />}
              <span>{copiedPat ? 'Copied' : 'Copy'}</span>
            </Button>
          </div>
        </div>
      )}

      {isCreatingToken && (
        <form
          onSubmit={handleCreateToken}
          className="p-3.5 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/70 dark:bg-zinc-800/40 flex flex-col gap-3"
        >
          <div className="grid grid-cols-1 sm:grid-cols-3 gap-2.5">
            <input
              type="text"
              required
              value={newTokenName}
              onChange={(e) => setNewTokenName(e.target.value)}
              placeholder="Token name (e.g. Workstation CLI)"
              className="sm:col-span-2 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
            />
            <select
              value={effectiveTokenDays}
              onChange={(e) => setNewTokenDays(Number(e.target.value))}
              className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none cursor-pointer"
            >
              {patDayOptions.map((days) => (
                <option key={days} value={days}>
                  {days} days
                  {days === maxPatLifetimeDays && maxPatLifetimeDays < 365 ? ' (org max)' : ''}
                </option>
              ))}
            </select>
          </div>
          <div className="flex items-center justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              type="button"
              onClick={() => setIsCreatingToken(false)}
              className="text-xs font-semibold"
            >
              {t('common.cancel')}
            </Button>
            <Button variant="primary" size="sm" type="submit" className="text-xs font-semibold">
              Generate Token
            </Button>
          </div>
        </form>
      )}

      <div className="border border-zinc-200 dark:border-zinc-700/80 rounded-xl divide-y divide-zinc-200 dark:divide-zinc-700/80 overflow-hidden bg-white dark:bg-zinc-900/60">
        {activeTokens.map((pat) => (
          <div key={pat.id} className="flex items-center justify-between gap-3 px-4 py-3">
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <span className="text-sm font-medium text-zinc-900 dark:text-zinc-100 truncate">
                  {pat.name}
                </span>
                <span className="font-mono text-xs text-zinc-500 bg-zinc-100 dark:bg-zinc-800 px-2 py-0.5 rounded">
                  {pat.token_prefix}…
                </span>
              </div>
              <div className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
                {pat.last_used_at
                  ? `Last used ${formatRelativeTime(pat.last_used_at)}`
                  : 'Never used'}
                {pat.expires_at
                  ? ` · Expires ${new Date(pat.expires_at).toLocaleDateString()}`
                  : ''}
              </div>
            </div>
            <button
              type="button"
              onClick={() => handleOpenRevokeConfirm(pat)}
              title="Revoke token"
              className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30 transition-colors cursor-pointer"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}

        {activeTokens.length === 0 && (
          <div className="px-4 py-4 text-center text-xs text-zinc-400 dark:text-zinc-500 italic">
            No active personal access tokens
          </div>
        )}
      </div>

      <ConfirmDialog
        isOpen={Boolean(tokenToRevoke)}
        title="Revoke Personal Access Token?"
        message={
          tokenToRevoke ? (
            <>
              Are you sure you want to revoke{' '}
              <strong className="text-zinc-900 dark:text-zinc-100 font-semibold">
                {tokenToRevoke.name}
              </strong>{' '}
              (<code className="font-mono text-xs">{tokenToRevoke.token_prefix}…</code>)? Any CLI
              sessions or scripts using this token will lose access immediately.
            </>
          ) : (
            ''
          )
        }
        confirmText="Revoke Token"
        onConfirm={handleConfirmRevokeToken}
        onCancel={handleCloseRevokeConfirm}
        danger
      />
    </div>
  )
}

