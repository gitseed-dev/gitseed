import React, { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Button } from '@heroui/react'
import { AlertTriangle, Check, Copy, ExternalLink, FolderGit2, Link2, Plus } from 'lucide-react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { Modal } from '../common/Modal'
import { SegmentedControl } from '../common/SegmentedControl'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { safeExternalUrl } from '../../utils/gitUrls'
import { deriveProjectStageApprovals } from '../../utils/users'
import type { OrgRole, ProjectRole, UserInviteCreated } from '../../types'

interface UserModalProps {
  isOpen: boolean
  onClose: () => void
}

const PROJECT_ROLES: ProjectRole[] = ['maintainer', 'developer', 'viewer']

const UserModalForm: React.FC<{
  onClose: () => void
}> = ({ onClose }) => {
  const queryClient = useQueryClient()
  const { projects, projectPoliciesByProject, policies: orgPolicies, selectedProject } = useData()
  const { t } = useTranslation()

  const activeProjects = projects.filter((p) => !p.archived_at)

  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [orgRole, setOrgRole] = useState<OrgRole>('member')
  const [projectRoles, setProjectRoles] = useState<{ project_id: string; role: ProjectRole }[]>(
    () => {
      const preferred =
        (selectedProject && activeProjects.find((p) => p.id === selectedProject)) ||
        activeProjects[0]
      return preferred ? [{ project_id: preferred.id, role: 'developer' }] : []
    }
  )

  const [isSubmitting, setIsSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [membershipWarning, setMembershipWarning] = useState<string | null>(null)
  const [createdInvite, setCreatedInvite] = useState<UserInviteCreated | null>(null)
  const [copiedInvite, setCopiedInvite] = useState(false)

  const assignedProjectIds = new Set(projectRoles.map((pr) => pr.project_id))
  const unassignedProjects = activeProjects.filter((p) => !assignedProjectIds.has(p.id))

  const handleProjectRoleChange = (projectId: string, value: string) => {
    if (value === '__remove__') {
      setProjectRoles((prev) => prev.filter((pr) => pr.project_id !== projectId))
      return
    }
    const nextRole = value as ProjectRole
    setProjectRoles((prev) =>
      prev.map((pr) => (pr.project_id === projectId ? { ...pr, role: nextRole } : pr))
    )
  }

  const handleAddProject = (projectId: string) => {
    setProjectRoles((prev) => [...prev, { project_id: projectId, role: 'developer' }])
  }

  const handleCopyInviteUrl = async () => {
    if (!createdInvite) return
    try {
      await navigator.clipboard.writeText(createdInvite.invite_url)
      setCopiedInvite(true)
      window.setTimeout(() => setCopiedInvite(false), 2000)
    } catch {
      // ignore clipboard errors
    }
  }

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    const trimmedName = name.trim()
    const trimmedEmail = email.trim()
    if (!trimmedName || !trimmedEmail) {
      setError('Full name and email address are required.')
      return
    }

    setIsSubmitting(true)
    setError(null)
    setMembershipWarning(null)

    try {
      const invited = await api.users.invite({
        name: trimmedName,
        email: trimmedEmail,
        org_role: orgRole,
      })
      setCreatedInvite(invited)

      const failedProjects: string[] = []
      for (const assignment of projectRoles) {
        try {
          await api.projects.addMember(assignment.project_id, {
            user_id: invited.id,
            role: assignment.role,
          })
        } catch (memberErr) {
          const projName =
            projects.find((p) => p.id === assignment.project_id)?.name || assignment.project_id
          const detail = memberErr instanceof Error ? memberErr.message : 'failed to attach'
          failedProjects.push(`${projName} (${detail})`)
        }
      }

      if (failedProjects.length > 0) {
        setMembershipWarning(
          `Project membership could not be attached for ${failedProjects.join(', ')}. You can assign projects anytime from the user's detail drawer.`
        )
      }

      await Promise.allSettled([
        queryClient.invalidateQueries({ queryKey: queryKeys.users }),
        queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
        ...projectRoles.map((pr) =>
          queryClient.invalidateQueries({ queryKey: queryKeys.projectMembers(pr.project_id) })
        ),
      ])
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to invite user.')
    } finally {
      setIsSubmitting(false)
    }
  }

  if (createdInvite) {
    return (
      <div className="flex flex-col gap-5 text-sm">
        <div className="p-4 rounded-xl bg-emerald-500/10 border border-emerald-500/25 flex items-start gap-3">
          <div className="w-8 h-8 rounded-lg bg-emerald-500/20 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0 mt-0.5">
            <Check size={16} />
          </div>
          <div className="min-w-0 flex-1">
            <div className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
              Invitation created for {createdInvite.name} ({createdInvite.email})
            </div>
            <p className="text-xs text-zinc-600 dark:text-zinc-400 mt-1 leading-relaxed">
              Share this one-time invitation link so they can set their password and activate their
              account.
            </p>
          </div>
        </div>

        {membershipWarning && (
          <div className="p-3.5 rounded-xl bg-amber-500/10 border border-amber-500/30 text-xs text-amber-800 dark:text-amber-300 font-medium flex items-start gap-2.5">
            <AlertTriangle size={15} className="shrink-0 mt-0.5 text-amber-600 dark:text-amber-400" />
            <span>{membershipWarning}</span>
          </div>
        )}

        <div className="flex flex-col gap-2">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
            <Link2 size={14} className="text-teal-600 dark:text-teal-400" />
            <span>One-Time Invitation Link</span>
          </label>
          <div className="flex items-center gap-2">
            <input
              type="text"
              readOnly
              value={createdInvite.invite_url}
              onFocus={(e) => e.currentTarget.select()}
              className="flex-1 font-mono text-xs bg-zinc-50 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 py-2.5 text-zinc-800 dark:text-zinc-200 focus:outline-none"
            />
            <Button
              variant="secondary"
              size="md"
              type="button"
              onClick={handleCopyInviteUrl}
              className="shrink-0 flex items-center gap-1.5 rounded-xl text-xs font-semibold cursor-pointer"
            >
              {copiedInvite ? <Check size={14} className="text-emerald-500" /> : <Copy size={14} />}
              <span>{copiedInvite ? 'Copied' : 'Copy Link'}</span>
            </Button>
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            This link expires in 7 days. You can re-issue a fresh link anytime from the user’s
            detail drawer.
          </p>
        </div>

        <div className="flex items-center justify-between gap-3 pt-4 border-t border-zinc-200 dark:border-zinc-700/80 mt-1">
          {(() => {
            const rawInviteUrl = createdInvite.invite_url?.trim() || ''
            const resolvedCandidate =
              rawInviteUrl.startsWith('/') && !rawInviteUrl.startsWith('//')
                ? (() => {
                    try {
                      return new URL(rawInviteUrl, window.location.origin).toString()
                    } catch {
                      return null
                    }
                  })()
                : rawInviteUrl
            const safeInviteUrl = safeExternalUrl(resolvedCandidate)
            return safeInviteUrl ? (
              <a
                href={safeInviteUrl}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1.5 text-xs font-medium text-teal-600 dark:text-teal-400 hover:underline"
              >
                <span>Open Invite Page</span>
                <ExternalLink size={13} />
              </a>
            ) : (
              <span />
            )
          })()}
          <Button variant="primary" size="md" type="button" onClick={onClose} className="rounded-xl text-xs font-semibold">
            Done
          </Button>
        </div>
      </div>
    )
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-5 text-sm">
      {/* Name & Email */}
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-3.5">
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            {t('modals.user.nameLabel')} *
          </label>
          <input
            type="text"
            required
            value={name}
            onChange={(e) => {
              setName(e.target.value)
              if (error) setError(null)
            }}
            placeholder={t('modals.user.namePlaceholder')}
            className="w-full bg-zinc-50 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 py-2 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            {t('modals.user.emailLabel')}
          </label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => {
              setEmail(e.target.value)
              if (error) setError(null)
            }}
            placeholder={t('modals.user.emailPlaceholder')}
            className="w-full bg-zinc-50 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 py-2 text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
      </div>

      {/* Projects & Per-Project Role Table */}
      {activeProjects.length > 0 && (
        <div className="flex flex-col gap-2">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
            <FolderGit2 size={14} className="text-teal-600 dark:text-teal-400" />
            {t('modals.user.projectRolesLabel')}
          </label>

          <div className="border border-zinc-200 dark:border-zinc-700/80 rounded-xl divide-y divide-zinc-200 dark:divide-zinc-700/80 overflow-hidden">
            {projectRoles.map((pr) => {
              const proj = projects.find((p) => p.id === pr.project_id)
              const effectivePolicies = projectPoliciesByProject[pr.project_id]?.length
                ? projectPoliciesByProject[pr.project_id]
                : orgPolicies
              const derived = deriveProjectStageApprovals('active', pr.role, effectivePolicies)

              return (
                <div
                  key={pr.project_id}
                  className="flex items-center justify-between gap-4 px-4 py-3 bg-white dark:bg-zinc-900"
                >
                  <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                    <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                      {proj?.name || pr.project_id}
                    </span>
                    <span className="text-xs text-zinc-500 dark:text-zinc-400">
                      {derived.stageLabels.length > 0 ? (
                        <>
                          {t('details.user.approvesPrefix')}{' '}
                          <span className="text-zinc-700 dark:text-zinc-300 font-medium">
                            {derived.stageLabels.join(' · ')}
                          </span>
                        </>
                      ) : (
                        t('details.user.noApprovals')
                      )}
                    </span>
                  </div>
                  <select
                    value={pr.role}
                    onChange={(e) => handleProjectRoleChange(pr.project_id, e.target.value)}
                    className="bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-teal-500 cursor-pointer shrink-0"
                  >
                    {PROJECT_ROLES.map((r) => (
                      <option key={r} value={r}>
                        {t(`users.roles.${r}`)}
                      </option>
                    ))}
                    <option value="__remove__">{t('details.user.removeFromProject')}</option>
                  </select>
                </div>
              )
            })}

            {projectRoles.length === 0 && (
              <div className="px-4 py-3.5 text-center text-xs text-zinc-400 italic">
                {t('users.noProjects')}
              </div>
            )}
          </div>

          {unassignedProjects.length > 0 && (
            <div className="flex flex-wrap gap-1.5 pt-0.5">
              {unassignedProjects.map((p) => (
                <button
                  key={p.id}
                  type="button"
                  onClick={() => handleAddProject(p.id)}
                  className="inline-flex items-center gap-1 text-xs font-medium px-3 py-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-800/60 text-zinc-700 dark:text-zinc-300 hover:border-teal-500/40 cursor-pointer transition-colors"
                >
                  <Plus size={13} className="text-teal-600 dark:text-teal-400" />
                  <span>{p.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Org Role */}
      <div className="flex flex-col gap-1.5 p-4 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80">
        <div className="flex items-center justify-between gap-3">
          <label className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {t('modals.user.roleLabel')}
          </label>
          <SegmentedControl
            items={[
              { id: 'member', label: t('users.roles.member') },
              { id: 'admin', label: t('users.roles.admin') },
            ]}
            selectedId={orgRole}
            onSelect={(id) => setOrgRole(id as OrgRole)}
          />
        </div>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          {t('modals.user.orgRoleHint')}
        </p>
      </div>

      {error && (
        <div className="px-3.5 py-2.5 rounded-xl bg-rose-500/10 border border-rose-500/20 text-xs font-medium text-rose-600 dark:text-rose-400">
          {error}
        </div>
      )}

      {/* Actions */}
      <div className="flex items-center justify-end gap-3 pt-4 border-t border-zinc-200 dark:border-zinc-700/80 mt-1">
        <Button variant="secondary" size="md" type="button" onClick={onClose} className="rounded-xl text-xs font-semibold">
          {t('common.cancel')}
        </Button>
        <Button variant="primary" size="md" type="submit" isDisabled={isSubmitting} className="rounded-xl text-xs font-semibold">
          {isSubmitting ? 'Creating Invite...' : t('modals.user.submitButton')}
        </Button>
      </div>
    </form>
  )
}

export const UserModal: React.FC<UserModalProps> = ({ isOpen, onClose }) => {
  const { t } = useTranslation()

  return (
    <Modal isOpen={isOpen} onClose={onClose} title={t('modals.user.createTitle')}>
      {isOpen && <UserModalForm onClose={onClose} />}
    </Modal>
  )
}
