import React, { useState } from 'react'
import { AlertCircle, ArrowRight, Lock, Mail, Moon, Sun } from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { Tooltip } from '../components/common/Tooltip'
import { SdlcStreamAnimation } from '../components/common/SdlcStreamAnimation'
import { useAuth } from '../context/AuthContext'
import { useTheme } from '../context/ThemeContext'
import { useTranslation } from '../context/I18nContext'

export const LoginView: React.FC = () => {
  const { login, authError, clearAuthError } = useAuth()
  const { theme, toggleTheme, ambientBg } = useTheme()
  const { t } = useTranslation()

  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setIsSubmitting(true)
    const success = await login(email, password)
    setIsSubmitting(false)
    if (success) {
      window.history.replaceState(null, '', '/overview')
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center py-10 sm:py-14 px-6 relative text-zinc-900 dark:text-zinc-100 overflow-hidden bg-[#f4f5f7] dark:bg-[#070b12] transition-colors duration-300">
      {/* Cross-fading ambient background gradients & radial atmosphere (smooth 300ms opacity transition) */}
      {ambientBg && (
        <div aria-hidden="true" className="absolute inset-0 pointer-events-none z-0 overflow-hidden">
          {/* Light Mode Linear & Radial Atmosphere */}
          <div className="absolute inset-0 bg-gradient-to-b from-[#f8fafc] via-[#f1f5f9] to-[#e2e8f0] opacity-100 dark:opacity-0 transition-opacity duration-300">
            <div
              className="absolute -top-28 left-1/2 -translate-x-1/2 w-[1100px] h-[750px]"
              style={{
                background:
                  'radial-gradient(ellipse 65% 55% at 50% 38%, rgba(20, 184, 166, 0.12) 0%, rgba(13, 148, 136, 0.08) 42%, rgba(248, 250, 252, 0) 72%)',
              }}
            />
          </div>

          {/* Dark Mode Linear & Radial Atmosphere + Ambient Orbs */}
          <div className="absolute inset-0 bg-gradient-to-b from-[#0e1a2b] via-[#08101a] to-[#03060a] opacity-0 dark:opacity-100 transition-opacity duration-300">
            <div
              className="absolute -top-28 left-1/2 -translate-x-1/2 w-[1100px] h-[750px]"
              style={{
                background:
                  'radial-gradient(ellipse 65% 55% at 50% 38%, rgba(20, 184, 166, 0.18) 0%, rgba(13, 148, 136, 0.12) 38%, rgba(8, 16, 26, 0) 72%)',
              }}
            />
            <div className="absolute top-1/3 -right-20 w-[500px] h-[500px] bg-lime-400/10 blur-[140px] rounded-full" />
            <div className="absolute -bottom-20 -left-20 w-[550px] h-[450px] bg-teal-600/12 blur-[130px] rounded-full" />
          </div>
        </div>
      )}

      {/* Dynamic SDLC Stream Animation flowing horizontally behind login card */}
      <SdlcStreamAnimation />

      {/* Theme toggle in top right */}
      <div className="absolute top-6 right-6 z-20">
        <Tooltip content={theme === 'dark' ? t('topbar.switchToLight') : t('topbar.switchToDark')} placement="left">
          <Button
            variant="secondary"
            size="sm"
            isIconOnly
            onClick={toggleTheme}
            aria-label={theme === 'dark' ? t('topbar.switchToLight') : t('topbar.switchToDark')}
            className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white/80 dark:bg-zinc-900/80 backdrop-blur-md shadow-sm cursor-pointer transition-colors duration-300"
          >
            {theme === 'dark' ? (
              <Sun size={17} className="text-amber-400" aria-hidden="true" />
            ) : (
              <Moon size={17} className="text-zinc-700 dark:text-zinc-300" aria-hidden="true" />
            )}
          </Button>
        </Tooltip>
      </div>

      <div className="w-full max-w-md relative z-10 my-4 sm:my-6">
        {/* Soft, tailored card aura that gently frames the login form without bleeding down into the footer */}
        <div
          aria-hidden="true"
          className="absolute -inset-2 sm:-inset-3 rounded-[32px] bg-gradient-to-b from-teal-500/15 via-teal-600/10 to-transparent blur-2xl -z-10 pointer-events-none opacity-100 dark:opacity-0 transition-opacity duration-300"
        />
        <div
          aria-hidden="true"
          className="absolute -inset-2 sm:-inset-3 rounded-[32px] bg-gradient-to-b from-teal-400/20 via-teal-600/10 to-transparent blur-2xl -z-10 pointer-events-none opacity-0 dark:opacity-100 transition-opacity duration-300"
        />

        <Card className="px-8 sm:px-10 pt-12 pb-12 sm:pt-14 sm:pb-14 border border-slate-200/90 dark:border-slate-700/80 bg-white/94 dark:bg-[#0b1626]/94 backdrop-blur-2xl shadow-2xl shadow-slate-400/25 dark:shadow-[0_20px_50px_-15px_rgba(0,0,0,0.85)] ring-1 ring-black/[0.04] dark:ring-white/[0.08] rounded-3xl transition-[background-color,border-color,box-shadow] duration-300">
          <div className="text-center mb-8 sm:mb-9">
            <div className="flex items-center justify-center gap-3 mb-2.5">
              <div className="w-10 h-10 rounded-xl overflow-hidden shadow-[0_0_16px_rgba(20,184,166,0.22)] dark:shadow-[0_0_20px_rgba(95,214,198,0.25)] border border-teal-500/30 flex items-center justify-center shrink-0 transition-shadow duration-300">
                <img src="/gitseed-logo.svg" alt="GitSeed" className="w-full h-full object-cover" />
              </div>
              <h1 className="text-2xl sm:text-[26px] font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100 transition-colors duration-300">
                {t('common.appName')}
              </h1>
            </div>
            <p className="text-[15px] font-medium text-zinc-500 dark:text-zinc-400 leading-normal transition-colors duration-300">
              {t('auth.headline')}
            </p>
          </div>

          {/* Error Banner */}
          {authError && (
            <div className="mb-6 p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-300 text-xs flex items-center gap-2.5 transition-colors duration-300">
              <AlertCircle size={16} className="text-rose-500 dark:text-rose-400 shrink-0" />
              <span className="font-medium">{authError}</span>
            </div>
          )}

          <form onSubmit={handleSubmit} className="flex flex-col gap-5">
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 transition-colors duration-300">
                {t('auth.emailLabel')}
              </label>
              <div className="relative">
                <Mail
                  size={16}
                  className="absolute left-3.5 top-3.5 text-zinc-400 dark:text-zinc-500 transition-colors duration-300"
                />
                <input
                  type="email"
                  className="w-full bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-10 pr-4 py-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50 transition-colors duration-300"
                  value={email}
                  onChange={(e) => {
                    setEmail(e.target.value)
                    if (authError) clearAuthError()
                  }}
                  placeholder={t('auth.emailPlaceholder')}
                  required
                  autoFocus
                />
              </div>
            </div>

            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 transition-colors duration-300">
                {t('auth.passwordLabel')}
              </label>
              <div className="relative">
                <Lock
                  size={16}
                  className="absolute left-3.5 top-3.5 text-zinc-400 dark:text-zinc-500 transition-colors duration-300"
                />
                <input
                  type="password"
                  className="w-full bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-10 pr-4 py-3 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 dark:placeholder-zinc-500 focus:outline-none focus:ring-2 focus:ring-teal-500/40 dark:focus:ring-teal-500/50 font-mono transition-colors duration-300"
                  value={password}
                  onChange={(e) => {
                    setPassword(e.target.value)
                    if (authError) clearAuthError()
                  }}
                  placeholder="••••••••••••"
                  required
                />
              </div>
            </div>

            <Button
              type="submit"
              variant="primary"
              size="md"
              fullWidth
              isDisabled={isSubmitting}
              className="mt-3 py-3 font-semibold shadow-lg shadow-teal-600/25 dark:shadow-teal-500/20 flex items-center justify-center gap-2 cursor-pointer transition-all duration-300"
            >
              <span>{isSubmitting ? t('auth.authenticating') : t('auth.signInButton')}</span>
              <ArrowRight size={16} />
            </Button>
          </form>
        </Card>

        {/* Minimal Footer */}
        <div className="mt-8 sm:mt-10 flex items-center justify-center gap-2.5 text-xs font-medium text-zinc-500 dark:text-zinc-400 transition-colors duration-300">
          <span>{t('common.appName')} v0.1.0</span>
          <span className="w-1 h-1 rounded-full bg-zinc-400 dark:bg-zinc-500 shrink-0 transition-colors duration-300" aria-hidden="true" />
          <span>{t('auth.selfHosted')}</span>
          <span className="w-1 h-1 rounded-full bg-zinc-400 dark:bg-zinc-500 shrink-0 transition-colors duration-300" aria-hidden="true" />
          <a
            href="https://github.com/gitseed-dev/gitseed"
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 hover:text-zinc-900 dark:hover:text-zinc-100 transition-colors duration-300"
          >
            <svg className="w-3.5 h-3.5 fill-current" viewBox="0 0 24 24" aria-hidden="true">
              <path
                fillRule="evenodd"
                clipRule="evenodd"
                d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
              />
            </svg>
            <span>GitHub</span>
          </a>
        </div>
      </div>
    </div>
  )
}
