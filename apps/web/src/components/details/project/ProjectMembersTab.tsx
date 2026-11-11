import React, { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Plus,
  Search,
  Shield,
  Users,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { Modal } from '../../common/Modal'
import { SegmentedControl } from '../../common/SegmentedControl'
import { Tooltip } from '../../common/Tooltip'
import {
  EntityTableCard,
  EntityTableHeaderRow,
} from '../../layout/EntityPageLayout'
import { useData } from '../../../context/DataContext'
import { useTranslation } from '../../../context/I18nContext'
import { deriveProjectStageApprovals } from '../../../utils/users'
import type {
  Project,
  ProjectMember,
  ProjectRole,
  StagePolicy,
} from '../../../types'

const PROJECT_ROLES: ProjectRole[] = ['maintainer', 'developer', 'viewer']

export interface ProjectMembersTabProps {
  project: Project
  members: ProjectMember[]
  effectivePolicies: StagePolicy[]
  canManage: boolean
}

export const ProjectMembersTab: React.FC<ProjectMembersTabProps> = ({
  project,
  members,
  effectivePolicies,
  canManage,
}) => {
  const queryClient = useQueryClient()
  const { users } = useData()
  const { t } = useTranslation()

  const [searchQuery, setSearchQuery] = useState('')
  const [addModalOpen, setAddModalOpen] = useState(false)
  const [selectedUserId, setSelectedUserId] = useState('')
  const [selectedRole, setSelectedRole] = useState<ProjectRole>('developer')
  const [isAdding, setIsAdding] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [modalError, setModalError] = useState<string | null>(null)

  const usersById = useMemo(
    () => new Map(users.map((u) => [u.id, u])),
    [users]
  )

  const memberUserIds = useMemo(
    () => new Set(members.map((m) => m.user_id)),
    [members]
  )

  const availableUsers = useMemo(
    () =>
      users.filter(
        (u) => !memberUserIds.has(u.id) && u.status !== 'suspended'
      ),
    [users, memberUserIds]
  )

  const filteredMembers = useMemo(() => {
    const q = searchQuery.trim().toLowerCase()
    if (!q) return members
    return members.filter(
      (m) =>
        m.name.toLowerCase().includes(q) ||
        m.email.toLowerCase().includes(q) ||
        (m.preferred_name && m.preferred_name.toLowerCase().includes(q)) ||
        m.role.toLowerCase().includes(q)
    )
  }, [members, searchQuery])

  const previewDerivedApprovals = useMemo(
    () => deriveProjectStageApprovals('active', selectedRole, effectivePolicies),
    [selectedRole, effectivePolicies]
  )

  const invalidateMembers = async () => {
    await Promise.all([
      queryClient.invalidateQueries({
        queryKey: queryKeys.projectMembers(project.id),
      }),
      queryClient.invalidateQueries({ queryKey: queryKeys.projects }),
      queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
    ])
  }

  const openAddModal = () => {
    setSelectedUserId(availableUsers[0]?.id || '')
    setSelectedRole('developer')
    setModalError(null)
    setAddModalOpen(true)
  }

  const handleAddMember = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!selectedUserId) {
      setModalError('Please select a user to add.')
      return
    }
    setIsAdding(true)
    setModalError(null)
    try {
      await api.projects.addMember(project.id, {
        user_id: selectedUserId,
        role: selectedRole,
      })
      await invalidateMembers()
      setAddModalOpen(false)
    } catch (err) {
      setModalError(
        err instanceof Error ? err.message : 'Failed to add member to project.'
      )
    } finally {
      setIsAdding(false)
    }
  }

  const handleRoleChange = async (userId: string, nextValue: string) => {
    setErrorMessage(null)
    try {
      if (nextValue === '__remove__') {
        await api.projects.removeMember(project.id, userId)
      } else {
        await api.projects.updateMember(project.id, userId, {
          role: nextValue as ProjectRole,
        })
      }
      await invalidateMembers()
    } catch (err) {
      setErrorMessage(
        err instanceof Error
          ? err.message
          : 'Failed to update project member role.'
      )
    }
  }

  return (
    <div className="flex flex-col gap-4">
      {errorMessage && (
        <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
          <AlertCircle size={15} className="shrink-0" />
          <span>{errorMessage}</span>
        </div>
      )}

      {/* Toolbar: Search + Add Member Button */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div className="relative flex-1 max-w-sm">
          <Search
            size={15}
            className="absolute left-3.5 top-1/2 -translate-y-1/2 text-zinc-400 pointer-events-none"
          />
          <input
            type="text"
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            placeholder="Search project members..."
            className="w-full pl-9 pr-3.5 py-2 text-xs rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500 shadow-2xs"
          />
        </div>

        {canManage && (
          <Button
            type="button"
            variant="primary"
            size="sm"
            isDisabled={availableUsers.length === 0}
            onClick={openAddModal}
            className="rounded-xl font-semibold flex items-center gap-1.5 shadow-sm shadow-teal-600/20 cursor-pointer shrink-0"
          >
            <Plus size={15} />
            <span>Add Member</span>
          </Button>
        )}
      </div>

      {/* Members Table */}
      <EntityTableCard>
        <EntityTableHeaderRow>
          <th className="py-3 px-4">Member</th>
          <th className="py-3 px-4">Project Role</th>
          <th className="py-3 px-4">Can Approve Stages</th>
        </EntityTableHeaderRow>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-700/80 text-sm">
          {filteredMembers.length === 0 ? (
            <tr>
              <td
                colSpan={3}
                className="py-10 px-4 text-center text-xs text-zinc-500 dark:text-zinc-400"
              >
                <div className="flex flex-col items-center gap-2.5">
                  <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
                    <Users size={20} />
                  </div>
                  <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
                    {t('details.project.emptyMembers')}
                  </h4>
                </div>
              </td>
            </tr>
          ) : (
            filteredMembers.map((member) => {
              const orgUser = usersById.get(member.user_id)
              const userStatus = orgUser?.status || 'active'
              const derived = deriveProjectStageApprovals(
                userStatus,
                member.role,
                effectivePolicies
              )
              const displayName = member.preferred_name
                ? `${member.name} (${member.preferred_name})`
                : member.name || member.email

              return (
                <tr
                  key={member.user_id}
                  className="hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 transition"
                >
                  <td className="py-3.5 px-4">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-xl bg-teal-500/15 border border-teal-500/30 text-teal-700 dark:text-teal-300 flex items-center justify-center font-mono font-bold text-xs uppercase shrink-0">
                        {(member.name || member.email || '?')
                          .trim()
                          .slice(0, 2)}
                      </div>
                      <div className="flex flex-col min-w-0">
                        <div className="flex items-center gap-2">
                          <span className="font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                            {displayName}
                          </span>
                          {orgUser?.org_role === 'admin' && (
                            <span className="px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25">
                              Org Admin
                            </span>
                          )}
                          {userStatus === 'invited' && (
                            <span className="px-1.5 py-0.5 rounded-md text-[10px] font-bold uppercase tracking-wider bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/25">
                              Invited
                            </span>
                          )}
                        </div>
                        <span className="text-xs text-zinc-500 dark:text-zinc-400 font-mono truncate">
                          {member.email}
                        </span>
                      </div>
                    </div>
                  </td>

                  <td className="py-3.5 px-4">
                    <Tooltip
                      content={t('details.user.rolePickerHint')}
                      placement="top"
                    >
                      <select
                        value={member.role}
                        disabled={!canManage}
                        onChange={(e) =>
                          handleRoleChange(member.user_id, e.target.value)
                        }
                        aria-label={`${displayName} project role`}
                        className="bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-teal-500 cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed"
                      >
                        {PROJECT_ROLES.map((r) => (
                          <option key={r} value={r}>
                            {t(`users.roles.${r}`)}
                          </option>
                        ))}
                        <option value="__remove__">
                          {t('details.user.removeFromProject')}
                        </option>
                      </select>
                    </Tooltip>
                  </td>

                  <td className="py-3.5 px-4">
                    {derived.stageLabels.length > 0 ? (
                      <span className="text-xs text-zinc-600 dark:text-zinc-300 font-medium inline-flex items-center gap-1.5">
                        <span>{derived.stageLabels.join(' · ')}</span>
                        {derived.requiresMultiApprover && (
                          <Tooltip
                            content={t(
                              'users.canApprove.twoApprovalsSatisfied'
                            )}
                            placement="top"
                          >
                            <span className="inline-flex items-center text-zinc-500 dark:text-zinc-400">
                              <Shield size={12} />
                            </span>
                          </Tooltip>
                        )}
                      </span>
                    ) : (
                      <span className="text-xs text-zinc-400 dark:text-zinc-500">
                        {t('details.user.noApprovals')}
                      </span>
                    )}
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </EntityTableCard>

      {/* Brief Add Member Modal */}
      <Modal
        isOpen={addModalOpen}
        onClose={() => setAddModalOpen(false)}
        title={`Add Member to ${project.name}`}
        size="lg"
      >
        <form onSubmit={handleAddMember} className="flex flex-col gap-4">
          {modalError && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/30 text-xs text-rose-600 dark:text-rose-400 font-medium flex items-center gap-2">
              <AlertCircle size={15} className="shrink-0" />
              <span>{modalError}</span>
            </div>
          )}

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              User *
            </label>
            <select
              value={selectedUserId}
              onChange={(e) => setSelectedUserId(e.target.value)}
              className="w-full px-3.5 py-2.5 text-sm font-medium rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 cursor-pointer"
            >
              {availableUsers.map((u) => (
                <option key={u.id} value={u.id}>
                  {u.preferred_name ? `${u.name} (${u.preferred_name})` : u.name} · {u.email}
                </option>
              ))}
            </select>
          </div>

          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              Project Role
            </label>
            <SegmentedControl<ProjectRole>
              ariaLabel="Project role"
              selectedId={selectedRole}
              onSelect={setSelectedRole}
              className="w-full grid grid-cols-3"
              items={PROJECT_ROLES.map((r) => ({
                id: r,
                label: t(`users.roles.${r}`),
              }))}
            />
            <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1">
              {previewDerivedApprovals.stageLabels.length > 0 ? (
                <>
                  {t('details.user.approvesPrefix')}{' '}
                  <span className="font-medium text-zinc-800 dark:text-zinc-200">
                    {previewDerivedApprovals.stageLabels.join(' · ')}
                  </span>
                </>
              ) : (
                t('details.user.noApprovals')
              )}
            </p>
          </div>

          <div className="flex items-center justify-end gap-2.5 pt-3 border-t border-zinc-200 dark:border-zinc-700/80">
            <Button
              type="button"
              variant="secondary"
              size="md"
              onClick={() => setAddModalOpen(false)}
              isDisabled={isAdding}
              className="rounded-xl font-semibold cursor-pointer"
            >
              {t('common.cancel')}
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="md"
              isDisabled={!selectedUserId || isAdding}
              className="rounded-xl font-semibold shadow-sm shadow-teal-600/20 cursor-pointer"
            >
              {isAdding ? 'Adding...' : 'Add Member'}
            </Button>
          </div>
        </form>
      </Modal>
    </div>
  )
}
