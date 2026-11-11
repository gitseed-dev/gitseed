import React, { useState } from 'react'
import { Plus, Shield } from 'lucide-react'
import { Tooltip } from '../../common/Tooltip'
import { useTranslation } from '../../../context/I18nContext'
import {
  deriveProjectStageApprovals,
  type UserProjectAssignment,
} from '../../../utils/users'
import type { Project, ProjectRole, StagePolicy, UserStatus } from '../../../types'

const PROJECT_ROLES: ProjectRole[] = ['maintainer', 'developer', 'viewer']

interface UserProjectsSectionProps {
  userStatus: UserStatus
  projectAssignments: UserProjectAssignment[]
  unassignedProjects: Project[]
  projectPoliciesByProject: Record<string, StagePolicy[]>
  orgPolicies: StagePolicy[]
  canManageProject: (projectId: string) => boolean
  onNavigateProject: (projectId: string) => void
  onChangeProjectRole: (projectId: string, nextValue: string) => void
  onAddProject: (projectId: string) => Promise<void> | void
}

export const UserProjectsSection: React.FC<UserProjectsSectionProps> = ({
  userStatus,
  projectAssignments,
  unassignedProjects,
  projectPoliciesByProject,
  orgPolicies,
  canManageProject,
  onNavigateProject,
  onChangeProjectRole,
  onAddProject,
}) => {
  const { t } = useTranslation()
  const [isAddingProject, setIsAddingProject] = useState(false)

  return (
    <div className="flex flex-col gap-2.5">
      <h3 className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
        {t('details.user.projectsHeading')}
      </h3>

      <div className="border border-zinc-200 dark:border-zinc-700/80 rounded-xl divide-y divide-zinc-200 dark:divide-zinc-700/80 overflow-hidden bg-white dark:bg-zinc-900/60">
        {projectAssignments.map((assignment) => {
          const effectivePolicies = projectPoliciesByProject[assignment.project_id]?.length
            ? projectPoliciesByProject[assignment.project_id]
            : orgPolicies
          const derived = deriveProjectStageApprovals(
            userStatus,
            assignment.role,
            effectivePolicies
          )
          const editable = canManageProject(assignment.project_id)

          return (
            <div
              key={assignment.project_id}
              className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-zinc-50/80 dark:hover:bg-zinc-800/30 transition-colors"
            >
              {/* Left: Project Name + Derived Stage Approvals */}
              <div className="flex flex-col gap-0.5 min-w-0 flex-1">
                <button
                  type="button"
                  onClick={() => onNavigateProject(assignment.project_id)}
                  className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 hover:text-teal-600 dark:hover:text-teal-400 text-left truncate w-fit max-w-full cursor-pointer"
                >
                  {assignment.project_name}
                </button>

                <div className="text-xs leading-snug">
                  {derived.stageLabels.length > 0 ? (
                    <span className="text-zinc-500 dark:text-zinc-400">
                      {t('details.user.approvesPrefix')}{' '}
                      <span className="text-zinc-700 dark:text-zinc-300 font-medium">
                        {derived.stageLabels.join(' · ')}
                      </span>
                      {derived.requiresMultiApprover && (
                        <>
                          <span className="text-zinc-400 dark:text-zinc-500"> · </span>
                          <Tooltip
                            content={t('users.canApprove.twoApprovalsSatisfied')}
                            placement="top"
                          >
                            <span className="inline-flex items-center align-text-bottom text-zinc-600 dark:text-zinc-300">
                              <Shield size={12} />
                            </span>
                          </Tooltip>
                        </>
                      )}
                    </span>
                  ) : (
                    <span className="text-zinc-400 dark:text-zinc-500">
                      {t('details.user.noApprovals')}
                    </span>
                  )}
                </div>
              </div>

              {/* Right: Directly Editable Project Role Picker with RBAC Tooltip */}
              <Tooltip content={t('details.user.rolePickerHint')} placement="top">
                <select
                  value={assignment.role}
                  disabled={!editable}
                  onChange={(e) => onChangeProjectRole(assignment.project_id, e.target.value)}
                  aria-label={`${assignment.project_name} role`}
                  className="bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-teal-500 cursor-pointer disabled:opacity-60 disabled:cursor-not-allowed shrink-0"
                >
                  {PROJECT_ROLES.map((r) => (
                    <option key={r} value={r}>
                      {t(`users.roles.${r}`)}
                    </option>
                  ))}
                  <option value="__remove__">{t('details.user.removeFromProject')}</option>
                </select>
              </Tooltip>
            </div>
          )
        })}

        {projectAssignments.length === 0 && (
          <div className="px-4 py-4 text-center text-xs text-zinc-400 dark:text-zinc-500 italic">
            {t('users.noProjects')}
          </div>
        )}
      </div>

      {/* + Add to project */}
      {unassignedProjects.length > 0 && (
        <div className="relative">
          {!isAddingProject ? (
            <button
              type="button"
              onClick={() => setIsAddingProject(true)}
              className="inline-flex items-center gap-1.5 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:text-teal-700 dark:hover:text-teal-300 cursor-pointer py-1"
            >
              <Plus size={14} />
              <span>{t('details.user.addToProject')}</span>
            </button>
          ) : (
            <div className="flex items-center gap-2 p-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/70 dark:bg-zinc-800/40">
              <select
                defaultValue=""
                onChange={async (e) => {
                  const val = e.target.value
                  if (val) {
                    setIsAddingProject(false)
                    await onAddProject(val)
                  }
                }}
                className="flex-1 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 py-1.5 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-teal-500 cursor-pointer"
              >
                <option value="" disabled>
                  {t('details.user.addToProject')}...
                </option>
                {unassignedProjects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
              <button
                type="button"
                onClick={() => setIsAddingProject(false)}
                className="px-2.5 py-1.5 text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
              >
                {t('common.cancel')}
              </button>
            </div>
          )}
        </div>
      )}
    </div>
  )
}

