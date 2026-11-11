import type {
  Project,
  ProjectMember,
  ProjectRole,
  SeedStage,
  StagePolicy,
  User,
  UserStatus,
} from '../types'
import { SEED_STAGE_ORDER, STAGE_LABELS } from './seeds'

export { STAGE_LABELS }
export const STAGE_ORDER = SEED_STAGE_ORDER

export interface UserProjectAssignment {
  project_id: string
  project_name: string
  project_slug: string
  role: ProjectRole
}

export interface DerivedStageApprovals {
  stages: SeedStage[]
  stageLabels: string[]
  requiresMultiApprover: boolean
}

/**
 * Return all active (non-archived) project memberships for a given user ID.
 */
export function getUserProjectAssignments(
  userId: string,
  projects: Project[],
  projectMembersByProject: Record<string, ProjectMember[]>
): UserProjectAssignment[] {
  const assignments: UserProjectAssignment[] = []

  for (const project of projects) {
    if (project.archived_at) continue
    const members = projectMembersByProject[project.id] || []
    const match = members.find((m) => m.user_id === userId)
    if (match) {
      assignments.push({
        project_id: project.id,
        project_name: project.name,
        project_slug: project.slug,
        role: match.role,
      })
    }
  }

  return assignments
}

/**
 * Determine whether a project role satisfies a stage policy's approval requirement.
 */
export function canRoleApprovePolicy(role: ProjectRole, policy: StagePolicy): boolean {
  if (!policy.enabled || !policy.requires_approval) {
    return false
  }
  if (role === 'maintainer') {
    return true
  }
  if (role === 'developer') {
    return policy.min_approver_role === 'developer'
  }
  return false
}

/**
 * Derive which SDLC stage gates a user can approve on a single project
 * based on their project role and that project's effective stage policies.
 */
export function deriveProjectStageApprovals(
  userStatus: UserStatus,
  role: ProjectRole,
  effectivePolicies: StagePolicy[]
): DerivedStageApprovals {
  if (userStatus !== 'active') {
    return {
      stages: [],
      stageLabels: [],
      requiresMultiApprover: false,
    }
  }

  const stages: SeedStage[] = []
  let requiresMultiApprover = false

  for (const stage of STAGE_ORDER) {
    const policy = effectivePolicies.find((p) => p.stage === stage)
    if (!policy) continue
    if (canRoleApprovePolicy(role, policy)) {
      stages.push(stage)
      if (policy.min_approvers >= 2) {
        requiresMultiApprover = true
      }
    }
  }

  return {
    stages,
    stageLabels: stages.map((s) => STAGE_LABELS[s]),
    requiresMultiApprover,
  }
}

/**
 * Derive aggregate stage approval capabilities for a user across all their assigned projects.
 * Invited or suspended users cannot approve any stages.
 */
export function deriveUserStageApprovals(
  user: User,
  projects: Project[],
  projectMembersByProject: Record<string, ProjectMember[]>,
  projectPoliciesByProject: Record<string, StagePolicy[]>,
  orgPolicies: StagePolicy[]
): DerivedStageApprovals {
  if (user.status !== 'active') {
    return {
      stages: [],
      stageLabels: [],
      requiresMultiApprover: false,
    }
  }

  const assignments = getUserProjectAssignments(user.id, projects, projectMembersByProject)
  const stageSet = new Set<SeedStage>()
  let requiresMultiApprover = false

  for (const assignment of assignments) {
    const effectivePolicies =
      projectPoliciesByProject[assignment.project_id]?.length
        ? projectPoliciesByProject[assignment.project_id]
        : orgPolicies
    const derived = deriveProjectStageApprovals(user.status, assignment.role, effectivePolicies)
    for (const s of derived.stages) {
      stageSet.add(s)
    }
    if (derived.requiresMultiApprover) {
      requiresMultiApprover = true
    }
  }

  const orderedStages = STAGE_ORDER.filter((s) => stageSet.has(s))
  return {
    stages: orderedStages,
    stageLabels: orderedStages.map((s) => STAGE_LABELS[s]),
    requiresMultiApprover,
  }
}

/**
 * Generate the concise plain-text role summary for the Users list table
 * (e.g. "Admin · Maintainer on 2", "Maintainer on 1 · Dev on 1", "Developer on 1")
 * along with a multi-line tooltip breakdown.
 */
export function getUserRoleSummary(
  user: User,
  projects: Project[],
  projectMembersByProject: Record<string, ProjectMember[]>
): { label: string; detail: string } {
  const orgRole = user.org_role
  const assignments = getUserProjectAssignments(user.id, projects, projectMembersByProject)

  const counts: Record<ProjectRole, number> = {
    maintainer: 0,
    developer: 0,
    viewer: 0,
  }

  for (const assignment of assignments) {
    counts[assignment.role]++
  }

  const activeRoleTypesCount =
    (counts.maintainer > 0 ? 1 : 0) +
    (counts.developer > 0 ? 1 : 0) +
    (counts.viewer > 0 ? 1 : 0)

  const parts: string[] = []
  if (counts.maintainer > 0) {
    parts.push(`Maintainer on ${counts.maintainer}`)
  }
  if (counts.developer > 0) {
    const devLabel = activeRoleTypesCount > 1 ? 'Dev' : 'Developer'
    parts.push(`${devLabel} on ${counts.developer}`)
  }
  if (counts.viewer > 0) {
    parts.push(`Viewer on ${counts.viewer}`)
  }

  const label =
    orgRole === 'admin'
      ? parts.length > 0
        ? `Admin · ${parts.join(' · ')}`
        : 'Admin'
      : parts.length > 0
        ? parts.join(' · ')
        : 'No projects'

  const detailLines = assignments.map((assignment) => {
    const roleTitle = assignment.role.charAt(0).toUpperCase() + assignment.role.slice(1)
    return `${assignment.project_name}: ${roleTitle}`
  })

  if (orgRole === 'admin') {
    detailLines.unshift('Org role: Admin (Backends, Agents, Policies, Users)')
  }

  return {
    label,
    detail: detailLines.join('\n') || 'No projects assigned',
  }
}

