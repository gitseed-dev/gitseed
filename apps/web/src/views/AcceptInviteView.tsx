import React, { useEffect, useState } from 'react'
import { AlertCircle, ArrowRight, CheckCircle2, Lock, User as UserIcon } from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { ApiError, api } from '../api/client'
import { queryClient } from '../api/queryClient'
import { SdlcStreamAnimation } from '../components/common/SdlcStreamAnimation'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { VIEWS } from '../constants'
import type { InvitePreview } from '../types'

export const AcceptInviteView: React.FC = () => {
  const { t } = useTranslation()
  const { user, refreshUser } = useAuth()
  const { setActiveView, clearDetail } = useData()

  const token = new URLSearchParams(window.location.search).get('token') || ''

  const [preview, setPreview] = useState<InvitePreview | null>(null)
  const [preferredName, setPreferredName] = useState('')
  const [password, setPassword] = useState('')
  const [confirmPassword, setConfirmPassword] = useState('')
  const [isLoadingPreview, setIsLoadingPreview] = useState(true)
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!token) {
      setError(t('auth.invite.missingToken'))
      setIsLoadingPreview(false)
      return
    }

    api.auth
      .previewInvite(token)
      .then((data) => {
        setPreview(data)
        setPreferredName(data.name.split(' ')[0] || '')
      })
      .catch((err: unknown) => {
        setError(
          err instanceof ApiError ? err.detail : t('auth.invite.invalidOrExpired')
        )
      })
      .finally(() => {
        setIsLoadingPreview(false)
      })
  }, [token, t])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setError('')

    if (password.length < 8) {
      setError(t('auth.invite.passwordTooShort'))
      return
    }
    if (password !== confirmPassword) {
      setError(t('auth.invite.passwordMismatch'))
      return
    }

    setIsSubmitting(true)
    try {
      await api.auth.acceptInvite({
        token,
        password,
        preferred_name: preferredName.trim() || null,
      })
      queryClient.clear()
      await refreshUser()
      clearDetail()
      setActiveView(VIEWS.DASHBOARD)
      window.history.replaceState(null, '', '/overview')
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : t('auth.invite.acceptFailed'))
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center py-10 sm:py-14 px-6 relative text-zinc-900 dark:text-zinc-100 overflow-hidden bg-gradient-to-b from-[#f8fafc] via-[#f1f5f9] to-[#e2e8f0] dark:from-[#0e1a2b] dark:via-[#08101a] dark:to-[#03060a]">
      <SdlcStreamAnimation />

      <div className="w-full max-w-md relative z-10 my-4 sm:my-6">
        <div
          aria-hidden="true"
          className="absolute -inset-2 sm:-inset-3 rounded-[32px] bg-gradient-to-b from-teal-500/15 via-teal-600/10 to-transparent dark:from-teal-400/20 dark:via-teal-600/10 dark:to-transparent blur-2xl -z-10 pointer-events-none"
        />

        <Card className="px-8 sm:px-10 pt-12 pb-12 sm:pt-14 sm:pb-14 border border-slate-200/90 dark:border-slate-700/80 bg-white/94 dark:bg-[#0b1626]/94 backdrop-blur-2xl shadow-2xl rounded-3xl flex flex-col gap-6">
          <div className="flex flex-col items-center text-center gap-2">
            <div className="w-10 h-10 rounded-xl overflow-hidden shadow-[0_0_16px_rgba(20,184,166,0.22)] border border-teal-500/30 flex items-center justify-center shrink-0">
              <img src="/gitseed-logo.svg" alt="GitSeed" className="w-full h-full object-cover" />
            </div>
            <h1 className="text-xl font-extrabold font-heading text-zinc-900 dark:text-zinc-100">
              {t('auth.invite.title')}
            </h1>
            {preview && (
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                {t('auth.invite.joiningAs')}{' '}
                <strong className="text-zinc-800 dark:text-zinc-200">{preview.email}</strong> ({preview.org_role})
              </p>
            )}
          </div>

          {user && preview && user.email.toLowerCase() !== preview.email.toLowerCase() && (
            <div className="p-3 rounded-xl bg-amber-500/10 border border-amber-500/25 text-xs text-amber-700 dark:text-amber-300 leading-relaxed">
              {t('auth.invite.sessionSwitchNotice', {
                currentEmail: user.email,
                targetEmail: preview.email,
              })}
            </div>
          )}

          {error && (
            <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-center gap-2.5 text-xs text-rose-600 dark:text-rose-300">
              <AlertCircle size={16} className="shrink-0 text-rose-500" />
              <span className="font-medium">{error}</span>
            </div>
          )}

          {isLoadingPreview ? (
            <div className="py-8 flex items-center justify-center gap-2 text-sm text-zinc-500">
              <span className="w-4 h-4 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
              <span>{t('auth.invite.verifying')}</span>
            </div>
          ) : preview ? (
            <form onSubmit={handleSubmit} className="flex flex-col gap-5">
              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 transition-colors duration-300">
                  {t('auth.invite.preferredName')}
                </label>
                <div className="relative">
                  <UserIcon
                    size={16}
                    className="absolute left-3.5 top-3.5 text-zinc-400 dark:text-zinc-500 transition-colors duration-300"
                  />
                  <input
                    type="text"
                    value={preferredName}
                    onChange={(e) => setPreferredName(e.target.value)}
                    placeholder={preview.name}
                    className="w-full bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-10 pr-4 py-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50 transition-colors duration-300"
                  />
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 transition-colors duration-300">
                  {t('auth.invite.setPassword')}
                </label>
                <div className="relative">
                  <Lock
                    size={16}
                    className="absolute left-3.5 top-3.5 text-zinc-400 dark:text-zinc-500 transition-colors duration-300"
                  />
                  <input
                    type="password"
                    required
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    placeholder="••••••••••••"
                    autoFocus
                    className="w-full bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-10 pr-4 py-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50 font-mono transition-colors duration-300"
                  />
                </div>
              </div>

              <div className="flex flex-col gap-1.5">
                <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 transition-colors duration-300">
                  {t('auth.invite.confirmPassword')}
                </label>
                <div className="relative">
                  <CheckCircle2
                    size={16}
                    className="absolute left-3.5 top-3.5 text-zinc-400 dark:text-zinc-500 transition-colors duration-300"
                  />
                  <input
                    type="password"
                    required
                    value={confirmPassword}
                    onChange={(e) => setConfirmPassword(e.target.value)}
                    placeholder="••••••••••••"
                    className="w-full bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-10 pr-4 py-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50 font-mono transition-colors duration-300"
                  />
                </div>
              </div>

              <Button
                type="submit"
                variant="primary"
                size="md"
                fullWidth
                isDisabled={isSubmitting}
                className="mt-2 py-3 font-semibold shadow-lg shadow-teal-600/25 flex items-center justify-center gap-2 cursor-pointer"
              >
                <span>
                  {isSubmitting ? t('auth.invite.activating') : t('auth.invite.activateAndSignIn')}
                </span>
                <ArrowRight size={16} />
              </Button>
            </form>
          ) : (
            <Button
              variant="secondary"
              size="md"
              onClick={() => {
                window.location.href = '/login'
              }}
              className="w-full rounded-xl font-semibold text-xs cursor-pointer"
            >
              {t('auth.invite.backToSignIn')}
            </Button>
          )}
        </Card>
      </div>
    </div>
  )
}
