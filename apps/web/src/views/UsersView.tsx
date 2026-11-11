import React, { useMemo, useState } from 'react'
import { Shield, Users } from 'lucide-react'
import { SegmentedControl } from '../components/common/SegmentedControl'
import { Tooltip } from '../components/common/Tooltip'
import {
  EntityPageLayout,
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../components/layout/EntityPageLayout'
import { UserDetail } from '../components/details/UserDetail'
import { UserModal } from '../components/modals/UserModal'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { VIEWS } from '../constants'
import { useTableSort } from '../hooks/useTableSort'
import { usePagination } from '../hooks/usePagination'
import { formatRelativeTime } from '../utils/format'
import { deriveUserStageApprovals, getUserRoleSummary } from '../utils/users'
import type { User } from '../types'

export const UsersView: React.FC = () => {
  const {
    users,
    projects,
    projectMembersByProject,
    projectPoliciesByProject,
    policies: orgPolicies,
    activeDetailId,
    navigateToDetail,
    clearDetail,
  } = useData()
  const { user: authUser } = useAuth()
  const { t } = useTranslation()

  const [searchTerm, setSearchTerm] = useState('')
  const [isModalOpen, setIsModalOpen] = useState(false)
  const [selectedStatus, setSelectedStatus] = useState<'active' | 'suspended' | 'all'>('active')

  const isAdmin = authUser?.org_role === 'admin'

  const filteredUsers = useMemo(() => {
    return users.filter((u) => {
      if (selectedStatus === 'active' && u.status === 'suspended') return false
      if (selectedStatus === 'suspended' && u.status !== 'suspended') return false

      const term = searchTerm.toLowerCase().trim()
      if (!term) return true
      const roleSummary = getUserRoleSummary(
        u,
        projects,
        projectMembersByProject
      ).label.toLowerCase()
      return (
        u.id.toLowerCase().includes(term) ||
        u.email.toLowerCase().includes(term) ||
        u.name.toLowerCase().includes(term) ||
        (u.preferred_name && u.preferred_name.toLowerCase().includes(term)) ||
        roleSummary.includes(term)
      )
    })
  }, [users, selectedStatus, searchTerm, projects, projectMembersByProject])

  type UserSortKey = 'name' | 'role' | 'last_active_at'

  const {
    sortedItems: sortedUsers,
    sortConfig,
    requestSort,
  } = useTableSort<User, UserSortKey>({
    items: filteredUsers,
    initialKey: 'name',
    initialDirection: 'asc',
    customComparators: {
      name: (a, b) => (a.name || a.email).localeCompare(b.name || b.email),
      role: (a, b) =>
        getUserRoleSummary(a, projects, projectMembersByProject).label.localeCompare(
          getUserRoleSummary(b, projects, projectMembersByProject).label
        ),
      last_active_at: (a, b) =>
        (b.last_active_at || '').localeCompare(a.last_active_at || ''),
    },
  })

  const { paginatedItems: pagedUsers, paginationProps } = usePagination({
    items: sortedUsers,
    initialPageSize: 10,
  })

  const activeCount = users.filter((u) => u.status !== 'suspended').length
  const suspendedCount = users.filter((u) => u.status === 'suspended').length

  const activeUser = activeDetailId
    ? users.find((u) => u.id === activeDetailId) ||
    (authUser && (authUser.id === activeDetailId || authUser.email === activeDetailId)
      ? authUser
      : null)
    : null

  const statusTabItems = [
    { id: 'active', label: t('users.tabs.active'), count: activeCount },
    { id: 'suspended', label: t('users.tabs.suspended'), count: suspendedCount },
    { id: 'all', label: t('users.tabs.all'), count: users.length },
  ]

  return (
    <>
      <EntityPageLayout
        title={t('users.title')}
        subtitle={t('users.subtitle')}
        count={activeCount}
        isGlobal
        primaryAction={
          isAdmin
            ? {
              label: t('users.create'),
              onClick: () => setIsModalOpen(true),
            }
            : undefined
        }
        storageKey="gitseed_view_users"
        searchQuery={searchTerm}
        onSearchChange={setSearchTerm}
        searchPlaceholder={t('users.searchPlaceholder')}
        isEmpty={filteredUsers.length === 0}
        filterTabs={
          <div className="flex items-center gap-2 flex-nowrap shrink-0">
            <SegmentedControl
              items={statusTabItems}
              selectedId={selectedStatus}
              onSelect={(id) => setSelectedStatus(id as 'active' | 'suspended' | 'all')}
            />
          </div>
        }
        emptyMessage={
          <div className="flex flex-col items-center gap-3">
            <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
              <Users size={20} />
            </div>
            <h3 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
              {t('users.noMatch')}
            </h3>
            {(selectedStatus !== 'active' || searchTerm) && (
              <button
                type="button"
                onClick={() => {
                  setSelectedStatus('active')
                  setSearchTerm('')
                }}
                className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                {t('users.clearFilter')}
              </button>
            )}
          </div>
        }
        pagination={paginationProps}
        renderList={() => (
          <EntityTableCard>
            <EntityTableHeaderRow>
              <SortableTh<UserSortKey>
                sortKey="name"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('users.table.user')}
              </SortableTh>
              <SortableTh<UserSortKey>
                sortKey="role"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
              >
                {t('users.table.role')}
              </SortableTh>
              <th className="py-3 px-4 font-semibold text-zinc-700 dark:text-zinc-300">
                {t('users.table.canApprove')}
              </th>
              <SortableTh<UserSortKey>
                sortKey="last_active_at"
                currentSortKey={sortConfig.key}
                currentSortDirection={sortConfig.direction}
                onSort={requestSort}
                className="whitespace-nowrap"
              >
                {t('users.table.lastActive')}
              </SortableTh>
            </EntityTableHeaderRow>
            <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800/60">
              {pagedUsers.map((member) => {
                const roleSummary = getUserRoleSummary(
                  member,
                  projects,
                  projectMembersByProject
                )
                const isSuspended = member.status === 'suspended'
                const isInvited = member.status === 'invited'
                const derived = deriveUserStageApprovals(
                  member,
                  projects,
                  projectMembersByProject,
                  projectPoliciesByProject,
                  orgPolicies
                )
                const isSelected = activeUser?.id === member.id

                return (
                  <tr
                    key={member.id}
                    onClick={() => navigateToDetail(VIEWS.USERS, member.id)}
                    className={`transition-colors cursor-pointer ${isSelected
                        ? 'bg-teal-500/[0.06] dark:bg-teal-500/10'
                        : 'hover:bg-zinc-100/80 dark:hover:bg-zinc-800/40'
                      } ${isSuspended ? 'opacity-50' : ''}`}
                  >
                    {/* 1. User (Name with muted email below) */}
                    <td className="py-3.5 px-4">
                      <div className="flex items-center gap-3">
                        <div
                          className={`w-8 h-8 rounded-xl flex items-center justify-center font-bold text-xs shadow-xs shrink-0 ${isSuspended
                              ? 'bg-zinc-300 dark:bg-zinc-700 text-zinc-600 dark:text-zinc-300'
                              : isInvited
                                ? 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border border-amber-500/30'
                                : 'bg-teal-600 text-white'
                            }`}
                        >
                          {member.name
                            ? member.name.charAt(0).toUpperCase()
                            : member.email.charAt(0).toUpperCase()}
                        </div>
                        <div className="min-w-0">
                          <div className="font-semibold text-zinc-900 dark:text-zinc-100 text-xs truncate">
                            {member.name || member.email.split('@')[0]}
                          </div>
                          <div className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                            {member.email}
                          </div>
                        </div>
                      </div>
                    </td>

                    {/* 2. Role (Plain text per-project summary with tooltip) */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      <Tooltip content={roleSummary.detail} placement="top">
                        <span className="text-xs text-zinc-700 dark:text-zinc-300 cursor-default">
                          {roleSummary.label}
                        </span>
                      </Tooltip>
                    </td>

                    {/* 3. Can approve (Plain text derived from project roles & effective policies) */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {derived.stageLabels.length > 0 ? (
                        <span className="inline-flex items-center gap-1 text-xs text-zinc-700 dark:text-zinc-300">
                          <span>{derived.stageLabels.join(' · ')}</span>
                          {derived.requiresMultiApprover && (
                            <>
                              <span className="text-zinc-400 dark:text-zinc-500">·</span>
                              <Tooltip
                                content={t('users.canApprove.twoApprovalsSatisfied')}
                                placement="top"
                              >
                                <span className="inline-flex items-center text-zinc-500 dark:text-zinc-400">
                                  <Shield size={13} className="shrink-0" />
                                </span>
                              </Tooltip>
                            </>
                          )}
                        </span>
                      ) : (
                        <span className="text-zinc-400 dark:text-zinc-600 text-xs">
                          {t('users.canApprove.none')}
                        </span>
                      )}
                    </td>

                    {/* 4. Active (shows Suspended or Invited when applicable) */}
                    <td className="py-3.5 px-4 whitespace-nowrap">
                      {isSuspended ? (
                        <span className="text-xs text-zinc-500 dark:text-zinc-400">
                          {t('users.status.suspended')}
                        </span>
                      ) : isInvited ? (
                        <span className="text-xs text-amber-600 dark:text-amber-400 font-medium">
                          {t('users.signIn.invitedPending')}
                        </span>
                      ) : (
                        <span className="text-xs text-zinc-500 dark:text-zinc-400">
                          {member.last_active_at
                            ? formatRelativeTime(member.last_active_at)
                            : '-'}
                        </span>
                      )}
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </EntityTableCard>
        )}
      />

      {/* Slide-over User Drawer */}
      <UserDetail
        user={activeUser}
        isOpen={Boolean(activeUser)}
        onBack={clearDetail}
      />

      {/* Invite User Modal */}
      <UserModal
        isOpen={isModalOpen}
        onClose={() => setIsModalOpen(false)}
      />
    </>
  )
}

