import React, { useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Check,
  Copy,
  Edit3,
  KeyRound,
  Monitor,
  Moon,
  RefreshCw,
  RotateCcw,
  Sparkles,
  Sun,
  Trash2,
  UserCheck,
  UserX,
  X,
} from 'lucide-react'
import { Chip } from '@heroui/react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { ActionMenu, type ActionMenuItem } from '../common/ActionMenu'
import { SegmentedControl } from '../common/SegmentedControl'
import { SteppedSlider } from '../common/SteppedSlider'
import { ToggleSwitch } from '../common/ToggleSwitch'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { useTheme, type ThemeMode } from '../../context/ThemeContext'
import { VIEWS } from '../../constants'
import { formatRelativeTime } from '../../utils/format'
import { getUserProjectAssignments } from '../../utils/users'
import {
  UserInviteBanner,
  UserPasswordChangeForm,
  UserProfileEditForm,
} from './user/UserProfileForms'
import { UserProjectsSection } from './user/UserProjectsSection'
import { UserTokensSection } from './user/UserTokensSection'
import type { OrgRole, ProjectRole, User } from '../../types'

export interface UserDetailProps {
  user: User | null
  onBack: () => void
  isOpen?: boolean
}

export const UserDetail: React.FC<UserDetailProps> = ({
  user,
  onBack,
  isOpen = true,
}) => {
  const queryClient = useQueryClient()
  const { user: authUser, refreshUser } = useAuth()
  const {
    projects,
    projectMembersByProject,
    projectPoliciesByProject,
    policies: orgPolicies,
    orgSettings,
    navigateToDetail,
  } = useData()
  const { t } = useTranslation()
  const {
    themeMode,
    userThemeOverride,
    userAmbientOverride,
    setThemeMode,
    clearAppearanceOverrides,
    ambientBg,
    setAmbientBg,
  } = useTheme()

  const [editingProfileUserId, setEditingProfileUserId] = useState<string | null>(null)
  const isEditingProfile = Boolean(user && editingProfileUserId === user.id)
  const [profileName, setProfileName] = useState('')
  const [profilePreferredName, setProfilePreferredName] = useState('')
  const [isSavingProfile, setIsSavingProfile] = useState(false)

  const [changingPasswordUserId, setChangingPasswordUserId] = useState<string | null>(null)
  const isChangingPassword = Boolean(user && changingPasswordUserId === user.id)
  const [currentPassword, setCurrentPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirmNewPassword, setConfirmNewPassword] = useState('')
  const [passwordError, setPasswordError] = useState<string | null>(null)
  const [isSavingPassword, setIsSavingPassword] = useState(false)
  const [passwordSavedForUserId, setPasswordSavedForUserId] = useState<string | null>(null)
  const isPasswordSaved = Boolean(user && passwordSavedForUserId === user.id)

  const [reissuedInviteUrl, setReissuedInviteUrl] = useState<{
    userId: string
    url: string
  } | null>(null)
  const [isReinviting, setIsReinviting] = useState(false)
  const [isChildDialogOpen, setIsChildDialogOpen] = useState(false)
  const [actionError, setActionError] = useState<string | null>(null)

  const isSelf = Boolean(user && authUser && user.id === authUser.id)
  const isAdmin = authUser?.org_role === 'admin'

  const { data: tokens = [] } = useQuery({
    queryKey: queryKeys.tokens,
    queryFn: () => api.auth.listTokens(),
    enabled: Boolean(isOpen && isSelf),
  })

  useEffect(() => {
    setActionError(null)
  }, [user?.id])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && user && !isChildDialogOpen) {
        onBack()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, user, onBack, isChildDialogOpen])

  if (!user || !isOpen) return null

  const orgRole = user.org_role
  const userStatus = user.status

  const projectAssignments = getUserProjectAssignments(
    user.id,
    projects,
    projectMembersByProject
  )
  const assignedProjectIds = new Set(projectAssignments.map((pa) => pa.project_id))

  const canManageProject = (projectId: string): boolean => {
    if (isAdmin) return true
    if (!authUser) return false
    const members = projectMembersByProject[projectId] || []
    return members.some((m) => m.user_id === authUser.id && m.role === 'maintainer')
  }

  const unassignedProjects = projects.filter(
    (p) => !p.archived_at && !assignedProjectIds.has(p.id) && canManageProject(p.id)
  )

  const invalidateUserAndProjectData = async (projectId?: string) => {
    await Promise.all([
      queryClient.invalidateQueries({ queryKey: queryKeys.users }),
      queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      projectId
        ? queryClient.invalidateQueries({ queryKey: queryKeys.projectMembers(projectId) })
        : queryClient.invalidateQueries({ queryKey: ['projects'] }),
    ])
    if (isSelf) {
      await refreshUser()
    }
  }

  const handleOrgRoleChange = async (nextRole: OrgRole) => {
    if (nextRole === orgRole || !isAdmin || isSelf || userStatus === 'suspended') return
    setActionError(null)
    try {
      await api.users.update(user.id, { org_role: nextRole })
      await invalidateUserAndProjectData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update organization role.')
    }
  }

  const handleProjectRoleChange = async (projectId: string, nextValue: string) => {
    setActionError(null)
    try {
      if (nextValue === '__remove__') {
        await api.projects.removeMember(projectId, user.id)
      } else {
        await api.projects.updateMember(projectId, user.id, {
          role: nextValue as ProjectRole,
        })
      }
      await invalidateUserAndProjectData(projectId)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update project membership.')
    }
  }

  const handleAddProject = async (projectId: string) => {
    if (!projectId) return
    setActionError(null)
    try {
      await api.projects.addMember(projectId, {
        user_id: user.id,
        role: 'developer',
      })
      await invalidateUserAndProjectData(projectId)
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to add user to project.')
    }
  }

  const handleToggleSuspend = async () => {
    if (!isAdmin || isSelf) return
    setActionError(null)
    try {
      if (user.status === 'suspended') {
        await api.users.reactivate(user.id)
      } else if (user.status === 'active') {
        await api.users.suspend(user.id)
      }
      await invalidateUserAndProjectData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update user status.')
    }
  }

  const handleReinvite = async () => {
    if (!isAdmin || user.status !== 'invited') return
    setActionError(null)
    setIsReinviting(true)
    try {
      const res = await api.users.reinvite(user.id)
      setReissuedInviteUrl({ userId: user.id, url: res.invite_url })
      await invalidateUserAndProjectData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to re-issue invitation.')
    } finally {
      setIsReinviting(false)
    }
  }

  const handleRevokeInvite = async () => {
    if (!isAdmin || user.status !== 'invited') return
    setActionError(null)
    try {
      await api.users.delete(user.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.users }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
        queryClient.invalidateQueries({ queryKey: ['projects'] }),
      ])
      onBack()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to revoke invitation.')
    }
  }

  const handleSaveProfile = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!profileName.trim()) return
    setActionError(null)
    setIsSavingProfile(true)
    try {
      await api.users.update(user.id, {
        name: profileName.trim(),
        preferred_name: profilePreferredName.trim() || null,
      })
      setEditingProfileUserId(null)
      await invalidateUserAndProjectData()
    } catch (err) {
      setActionError(err instanceof Error ? err.message : 'Failed to update profile.')
    } finally {
      setIsSavingProfile(false)
    }
  }

  const handleSavePassword = async (e: React.FormEvent) => {
    e.preventDefault()
    if (newPassword.length < 8) {
      setPasswordError(t('modals.user.passwordTooShort'))
      return
    }
    if (newPassword !== confirmNewPassword) {
      setPasswordError(t('modals.user.passwordMismatch'))
      return
    }
    setPasswordError(null)
    setIsSavingPassword(true)
    try {
      await api.auth.changePassword(currentPassword, newPassword)
      setChangingPasswordUserId(null)
      setCurrentPassword('')
      setNewPassword('')
      setConfirmNewPassword('')
      setPasswordSavedForUserId(user.id)
      window.setTimeout(() => {
        setPasswordSavedForUserId((prev) => (prev === user.id ? null : prev))
      }, 2500)
    } catch (err) {
      setPasswordError(err instanceof Error ? err.message : 'Failed to change password.')
    } finally {
      setIsSavingPassword(false)
    }
  }

  const maxPatLifetimeDays = Math.min(
    365,
    Math.max(30, orgSettings?.pat_lifetime_days ?? 365)
  )

  const statusSummaryText =
    userStatus === 'suspended'
      ? t('users.status.suspended')
      : userStatus === 'invited'
        ? t('users.signIn.invitedPending')
        : user.last_active_at
          ? `${t('users.status.active')} ${formatRelativeTime(user.last_active_at)}`
          : t('users.status.active')

  const menuItems: ActionMenuItem[] = [
    {
      label: t('details.user.copyId', { id: user.id }),
      icon: <Copy size={14} />,
      onClick: () => {
        navigator.clipboard?.writeText(user.id)
      },
    },
  ]

  if (isSelf || isAdmin) {
    menuItems.push({
      label: 'Edit Profile',
      icon: <Edit3 size={14} />,
      onClick: () => {
        setProfileName(user.name)
        setProfilePreferredName(user.preferred_name || '')
        setEditingProfileUserId(user.id)
      },
    })
  }

  if (isSelf) {
    menuItems.push({
      label: 'Change Password',
      icon: <KeyRound size={14} />,
      onClick: () => {
        setCurrentPassword('')
        setNewPassword('')
        setConfirmNewPassword('')
        setPasswordError(null)
        setChangingPasswordUserId(user.id)
      },
    })
  }

  if (isAdmin && userStatus === 'invited') {
    menuItems.push({
      label: t('users.actions.resendInvite'),
      icon: <RefreshCw size={14} />,
      onClick: handleReinvite,
    })
    menuItems.push({
      label: 'Revoke Invite',
      icon: <Trash2 size={14} />,
      variant: 'danger',
      onClick: handleRevokeInvite,
    })
  }

  if (isAdmin && !isSelf && userStatus !== 'invited') {
    menuItems.push({
      label:
        userStatus === 'suspended'
          ? t('details.user.actions.activate')
          : t('details.user.actions.deactivate'),
      icon: userStatus === 'suspended' ? <UserCheck size={14} /> : <UserX size={14} />,
      variant: userStatus === 'suspended' ? 'default' : 'danger',
      onClick: handleToggleSuspend,
    })
  }

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-xs transition-opacity duration-200"
        onClick={onBack}
        aria-hidden="true"
      />

      {/* Slide-over Drawer Panel */}
      <aside
        className="relative z-50 w-full max-w-xl bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-700/80 shadow-2xl flex flex-col h-full overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-label={user.name || user.email}
      >
        {/* Header */}
        <div className="px-6 py-5 border-b border-zinc-200 dark:border-zinc-700/80 shrink-0 bg-zinc-50/40 dark:bg-zinc-900/50">
          <div className="flex items-start justify-between gap-3">
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="text-lg font-heading font-bold text-zinc-900 dark:text-zinc-100 truncate">
                  {user.name || user.email.split('@')[0]}
                </h2>
                {user.preferred_name && user.preferred_name !== user.name && (
                  <span className="text-sm text-zinc-400 dark:text-zinc-500 truncate">
                    ({user.preferred_name})
                  </span>
                )}
              </div>
              <div className="flex items-center gap-1.5 flex-wrap text-xs text-zinc-500 dark:text-zinc-400 mt-1">
                <span>{user.email}</span>
                <span className="text-zinc-300 dark:text-zinc-700">·</span>
                <span>{t(`users.roles.${orgRole}`)}</span>
                <span className="text-zinc-300 dark:text-zinc-700">·</span>
                <span
                  className={
                    userStatus === 'suspended'
                      ? 'text-zinc-400 dark:text-zinc-500'
                      : userStatus === 'invited'
                        ? 'text-amber-600 dark:text-amber-400 font-medium'
                        : ''
                  }
                >
                  {statusSummaryText}
                </span>
              </div>
            </div>

            <div className="flex items-center gap-1 shrink-0">
              <ActionMenu ariaLabel={t('users.table.actions')} items={menuItems} />

              <button
                type="button"
                onClick={onBack}
                aria-label="Close"
                className="p-1.5 rounded-xl text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
              >
                <X size={18} />
              </button>
            </div>
          </div>
        </div>

        {/* Drawer Body */}
        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-6 text-sm">
          {actionError && (
            <div className="px-3.5 py-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-xs font-medium text-rose-600 dark:text-rose-400 flex items-center justify-between gap-2">
              <span>{actionError}</span>
              <button
                type="button"
                onClick={() => setActionError(null)}
                className="text-rose-500 hover:text-rose-700 cursor-pointer shrink-0"
              >
                <X size={14} />
              </button>
            </div>
          )}

          {userStatus === 'invited' && isAdmin && (
            <UserInviteBanner
              user={user}
              isReinviting={isReinviting}
              reissuedInviteUrl={reissuedInviteUrl}
              onReinvite={handleReinvite}
              onRevokeInvite={handleRevokeInvite}
            />
          )}

          {isEditingProfile && (
            <UserProfileEditForm
              profileName={profileName}
              onChangeProfileName={setProfileName}
              profilePreferredName={profilePreferredName}
              onChangeProfilePreferredName={setProfilePreferredName}
              isSaving={isSavingProfile}
              onSave={handleSaveProfile}
              onCancel={() => setEditingProfileUserId(null)}
            />
          )}

          {isChangingPassword && (
            <UserPasswordChangeForm
              currentPassword={currentPassword}
              onChangeCurrentPassword={(val) => {
                setCurrentPassword(val)
                if (passwordError) setPasswordError(null)
              }}
              newPassword={newPassword}
              onChangeNewPassword={(val) => {
                setNewPassword(val)
                if (passwordError) setPasswordError(null)
              }}
              confirmNewPassword={confirmNewPassword}
              onChangeConfirmNewPassword={(val) => {
                setConfirmNewPassword(val)
                if (passwordError) setPasswordError(null)
              }}
              passwordError={passwordError}
              isSaving={isSavingPassword}
              onSave={handleSavePassword}
              onCancel={() => {
                setChangingPasswordUserId(null)
                setPasswordError(null)
              }}
            />
          )}

          {isPasswordSaved && !isChangingPassword && (
            <div className="flex items-center gap-1.5 text-xs font-medium text-emerald-600 dark:text-emerald-400">
              <Check size={14} />
              <span>{t('details.user.passwordUpdated')}</span>
            </div>
          )}

          <UserProjectsSection
            userStatus={userStatus}
            projectAssignments={projectAssignments}
            unassignedProjects={unassignedProjects}
            projectPoliciesByProject={projectPoliciesByProject}
            orgPolicies={orgPolicies}
            canManageProject={canManageProject}
            onNavigateProject={(projectId) => navigateToDetail(VIEWS.PROJECTS, projectId)}
            onChangeProjectRole={handleProjectRoleChange}
            onAddProject={handleAddProject}
          />

          {/* Org Role Section */}
          <div className="pt-4 border-t border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-2">
            <div className="flex items-center justify-between gap-4">
              <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                {t('details.user.orgRoleLabel')}
              </span>
              <SegmentedControl
                items={[
                  { id: 'member', label: t('users.roles.member') },
                  { id: 'admin', label: t('users.roles.admin') },
                ]}
                selectedId={orgRole}
                onSelect={(id) => handleOrgRoleChange(id as OrgRole)}
                disabled={!isAdmin || isSelf || userStatus === 'suspended'}
              />
            </div>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              {t('details.user.orgRoleHint')}
            </p>
          </div>

          {isSelf && (
            <div className="pt-4 border-t border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-3">
              <div className="flex items-center justify-between gap-2">
                <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                  {t('settings.appearance.title')}
                </span>
                {userThemeOverride !== null || userAmbientOverride !== null ? (
                  <button
                    type="button"
                    onClick={clearAppearanceOverrides}
                    className="inline-flex items-center gap-1 text-xs font-medium text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                  >
                    <RotateCcw size={11} />
                    <span>{t('details.user.resetThemeToOrgDefault')}</span>
                  </button>
                ) : (
                  <span className="text-xs font-medium text-zinc-400 dark:text-zinc-500">
                    {t('details.user.followingOrgDefault')}
                  </span>
                )}
              </div>

              <SteppedSlider<ThemeMode>
                title={t('details.user.personalThemeLabel')}
                description={t('details.user.personalThemeDesc')}
                value={themeMode}
                onChange={setThemeMode}
                trackGradientClass="bg-gradient-to-r from-purple-500 via-amber-500 to-sky-500"
                steps={[
                  {
                    value: 'dark',
                    label: t('settings.appearance.themeDark'),
                    icon: <Moon size={12} />,
                    headerIcon: <Moon size={16} className="text-purple-500 shrink-0" />,
                    chipColor: 'accent',
                    thumbActiveClass:
                      'bg-purple-600 border-white dark:border-zinc-900 shadow-md ring-4 ring-purple-500/25 scale-110',
                    dotActiveClass: 'bg-white',
                    labelActiveClass: 'font-bold text-purple-600 dark:text-purple-400',
                  },
                  {
                    value: 'light',
                    label: t('settings.appearance.themeLight'),
                    icon: <Sun size={12} />,
                    headerIcon: <Sun size={16} className="text-amber-500 shrink-0" />,
                    chipColor: 'warning',
                    thumbActiveClass:
                      'bg-amber-500 border-white dark:border-zinc-900 shadow-md ring-4 ring-amber-500/25 scale-110',
                    thumbPassedClass:
                      'bg-amber-400 border-white dark:border-zinc-900 shadow-xs',
                    dotActiveClass: 'bg-white',
                    dotPassedClass: 'bg-white/80',
                    labelActiveClass: 'font-bold text-amber-600 dark:text-amber-400',
                  },
                  {
                    value: 'system',
                    label: t('settings.appearance.themeSystem'),
                    icon: <Monitor size={12} />,
                    headerIcon: <Monitor size={16} className="text-sky-500 shrink-0" />,
                    chipColor: 'default',
                    thumbActiveClass:
                      'bg-sky-500 border-white dark:border-zinc-900 shadow-md ring-4 ring-sky-500/25 scale-110',
                    dotActiveClass: 'bg-white',
                    labelActiveClass: 'font-bold text-sky-600 dark:text-sky-400',
                  },
                ]}
              />

              <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
                <div className="flex items-center gap-3 min-w-0 flex-1">
                  <Sparkles
                    size={16}
                    className={
                      ambientBg ? 'text-teal-500 shrink-0' : 'text-zinc-400 shrink-0'
                    }
                  />
                  <div className="flex flex-col min-w-0">
                    <span
                      className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                      title={t('settings.appearance.ambientBg')}
                    >
                      {t('settings.appearance.ambientBg')}
                    </span>
                    <span
                      className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                      title={t('settings.appearance.ambientBgDesc')}
                    >
                      {t('settings.appearance.ambientBgDesc')}
                    </span>
                  </div>
                </div>
                <div className="flex items-center gap-2.5 shrink-0">
                  <Chip
                    size="sm"
                    variant="soft"
                    color={ambientBg ? 'accent' : 'default'}
                    className="text-xs font-semibold shrink-0"
                  >
                    {ambientBg
                      ? t('settings.appearance.ambientActive')
                      : t('settings.appearance.ambientFlat')}
                  </Chip>
                  <ToggleSwitch
                    checked={ambientBg}
                    onChange={setAmbientBg}
                    ariaLabel={t('settings.appearance.ambientBg')}
                    color="teal"
                  />
                </div>
              </div>
            </div>
          )}

          {isSelf && (
            <UserTokensSection
              tokens={tokens}
              maxPatLifetimeDays={maxPatLifetimeDays}
              onError={setActionError}
              onDialogOpenChange={setIsChildDialogOpen}
            />
          )}
        </div>
      </aside>
    </div>
  )
}
