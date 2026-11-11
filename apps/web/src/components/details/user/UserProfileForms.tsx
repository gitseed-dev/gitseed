import React, { useState } from 'react'
import { Check, Copy, Link2, RefreshCw, Trash2 } from 'lucide-react'
import { Button } from '@heroui/react'
import { useTranslation } from '../../../context/I18nContext'
import type { User } from '../../../types'

interface UserInviteBannerProps {
  user: User
  isReinviting: boolean
  reissuedInviteUrl: { userId: string; url: string } | null
  onReinvite: () => void
  onRevokeInvite: () => void
}

export const UserInviteBanner: React.FC<UserInviteBannerProps> = ({
  user,
  isReinviting,
  reissuedInviteUrl,
  onReinvite,
  onRevokeInvite,
}) => {
  const [copiedInviteUrl, setCopiedInviteUrl] = useState(false)

  return (
    <div className="p-4 rounded-xl border border-amber-500/30 bg-amber-500/5 flex flex-col gap-3">
      <div className="flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
            <Link2 size={14} className="text-amber-500 shrink-0" />
            <span>Invitation Pending Acceptance</span>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
            {user.invite_expires_at
              ? `Expires ${new Date(user.invite_expires_at).toLocaleDateString()}`
              : 'Awaiting account activation'}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <Button
            variant="secondary"
            size="sm"
            type="button"
            onClick={onReinvite}
            isDisabled={isReinviting}
            className="flex items-center gap-1.5 rounded-lg text-xs font-semibold cursor-pointer"
          >
            <RefreshCw size={13} className={isReinviting ? 'animate-spin' : ''} />
            <span>{isReinviting ? 'Minting...' : 'Re-issue Link'}</span>
          </Button>
          <Button
            variant="secondary"
            size="sm"
            type="button"
            onClick={onRevokeInvite}
            className="flex items-center gap-1.5 rounded-lg text-xs font-semibold text-rose-600 dark:text-rose-400 hover:bg-rose-500/10 border-rose-500/20 cursor-pointer"
          >
            <Trash2 size={13} />
            <span>Revoke Invite</span>
          </Button>
        </div>
      </div>

      {reissuedInviteUrl && reissuedInviteUrl.userId === user.id && (
        <div className="flex items-center gap-2 pt-1">
          <input
            type="text"
            readOnly
            value={reissuedInviteUrl.url}
            onFocus={(e) => e.currentTarget.select()}
            className="flex-1 font-mono text-xs bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 py-2 text-zinc-800 dark:text-zinc-200 focus:outline-none"
          />
          <Button
            variant="primary"
            size="sm"
            type="button"
            onClick={async () => {
              await navigator.clipboard?.writeText(reissuedInviteUrl.url)
              setCopiedInviteUrl(true)
              window.setTimeout(() => setCopiedInviteUrl(false), 2000)
            }}
            className="shrink-0 flex items-center gap-1 rounded-lg text-xs font-semibold cursor-pointer"
          >
            {copiedInviteUrl ? <Check size={13} /> : <Copy size={13} />}
            <span>{copiedInviteUrl ? 'Copied' : 'Copy'}</span>
          </Button>
        </div>
      )}
    </div>
  )
}

interface UserProfileEditFormProps {
  profileName: string
  onChangeProfileName: (val: string) => void
  profilePreferredName: string
  onChangeProfilePreferredName: (val: string) => void
  isSaving: boolean
  onSave: (e: React.FormEvent) => void
  onCancel: () => void
}

export const UserProfileEditForm: React.FC<UserProfileEditFormProps> = ({
  profileName,
  onChangeProfileName,
  profilePreferredName,
  onChangeProfilePreferredName,
  isSaving,
  onSave,
  onCancel,
}) => {
  const { t } = useTranslation()

  return (
    <form
      onSubmit={onSave}
      className="p-4 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/70 dark:bg-zinc-800/40 flex flex-col gap-3.5"
    >
      <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">Edit Profile</span>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            {t('modals.user.nameLabel')} *
          </label>
          <input
            type="text"
            required
            value={profileName}
            onChange={(e) => onChangeProfileName(e.target.value)}
            className="w-full bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            Preferred Name
          </label>
          <input
            type="text"
            value={profilePreferredName}
            onChange={(e) => onChangeProfilePreferredName(e.target.value)}
            placeholder={profileName.split(' ')[0] || 'Optional'}
            className="w-full bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
      </div>
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={onCancel}
          className="text-xs font-semibold"
        >
          {t('common.cancel')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          type="submit"
          isDisabled={isSaving}
          className="text-xs font-semibold"
        >
          {t('common.save')}
        </Button>
      </div>
    </form>
  )
}

interface UserPasswordChangeFormProps {
  currentPassword: string
  onChangeCurrentPassword: (val: string) => void
  newPassword: string
  onChangeNewPassword: (val: string) => void
  confirmNewPassword: string
  onChangeConfirmNewPassword: (val: string) => void
  passwordError: string | null
  isSaving: boolean
  onSave: (e: React.FormEvent) => void
  onCancel: () => void
}

export const UserPasswordChangeForm: React.FC<UserPasswordChangeFormProps> = ({
  currentPassword,
  onChangeCurrentPassword,
  newPassword,
  onChangeNewPassword,
  confirmNewPassword,
  onChangeConfirmNewPassword,
  passwordError,
  isSaving,
  onSave,
  onCancel,
}) => {
  const { t } = useTranslation()

  return (
    <form
      onSubmit={onSave}
      className="p-4 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/70 dark:bg-zinc-800/40 flex flex-col gap-3.5"
    >
      <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        Change Password
      </span>
      <div className="flex flex-col gap-3">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            Current Password *
          </label>
          <input
            type="password"
            required
            value={currentPassword}
            onChange={(e) => onChangeCurrentPassword(e.target.value)}
            className="w-full bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              New Password *
            </label>
            <input
              type="password"
              required
              value={newPassword}
              onChange={(e) => onChangeNewPassword(e.target.value)}
              placeholder={t('modals.user.passwordPlaceholder')}
              className="w-full bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              {t('modals.user.confirmPasswordLabel')}
            </label>
            <input
              type="password"
              required
              value={confirmNewPassword}
              onChange={(e) => onChangeConfirmNewPassword(e.target.value)}
              placeholder={t('modals.user.confirmPasswordPlaceholder')}
              className="w-full bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3 py-2 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
            />
          </div>
        </div>
      </div>
      {passwordError && (
        <p className="text-xs font-medium text-rose-600 dark:text-rose-400">{passwordError}</p>
      )}
      <div className="flex items-center justify-end gap-2">
        <Button
          variant="secondary"
          size="sm"
          type="button"
          onClick={onCancel}
          className="text-xs font-semibold"
        >
          {t('common.cancel')}
        </Button>
        <Button
          variant="primary"
          size="sm"
          type="submit"
          isDisabled={isSaving}
          className="text-xs font-semibold"
        >
          {t('details.user.savePassword')}
        </Button>
      </div>
    </form>
  )
}

