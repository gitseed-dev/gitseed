import { SEED_NEEDS_ATTENTION_STATUSES } from '../constants'
import type {
  ActivityEvent,
  OrgSettings,
  Project,
  ProjectMember,
  ProjectRepository,
  Seed,
  SeedStage,
  User,
} from '../types'

export type ProjectOperationalStatus =
  | 'needs_action'
  | 'blocked'
  | 'active'
  | 'idle'
  | 'archived'

export interface ProjectSummary {
  project: Project
  status: ProjectOperationalStatus
  allProjectSeeds: Seed[]
  openSeeds: Seed[]
  needsYouSeeds: Seed[]
  blockedSeeds: Seed[]
  runningSeeds: Seed[]
  queuedSeeds: Seed[]
  mergedSeeds: Seed[]
  merged30dCount: number
  stageCounts: Record<SeedStage, number>
  totalSpend: number
  effectiveMonthlyBudget: number | null
  isBudgetOverridden: boolean
  primaryRepo: ProjectRepository | null
  repos: ProjectRepository[]
  members: ProjectMember[]
  lastActivityAt: string | null
}

/**
 * Generate a URL-safe project slug (2-63 chars, lowercase alphanumeric + hyphens) from a display name.
 */
export function deriveProjectSlug(name: string): string {
  const base = name
    .toLowerCase()
    .trim()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
    .slice(0, 63)
    .replace(/-+$/g, '')
  return base
}

/**
 * Derive a 2-letter avatar monogram from a project's name or slug:
 * uses the first letter of the first two words (e.g. "My Test Project" -> "MT"),
 * falling back to the first two characters for single-word names.
 */
export function getProjectInitials(
  project: Pick<Project, 'name' | 'slug'>
): string {
  const source = (project.name || project.slug || '').trim()
  const words = source.split(/[\s\-_]+/).filter(Boolean)
  if (words.length >= 2) {
    return `${words[0][0]}${words[1][0]}`.toUpperCase()
  }
  return (words[0] || source || 'PR').slice(0, 2).toUpperCase()
}

/**
 * Check whether the current user can manage a project (org admin or project maintainer).
 */
export function canUserManageProject(
  user: User | null | undefined,
  project: Project,
  members: ProjectMember[] = []
): boolean {
  if (!user) return false
  if (user.org_role === 'admin') return true
  if (project.my_role === 'maintainer') return true
  return members.some((m) => m.user_id === user.id && m.role === 'maintainer')
}

/**
 * Check whether the current user can create/modify seeds in a project
 * (org admin, project maintainer, or project developer).
 */
export function canUserContributeToProject(
  user: User | null | undefined,
  project: Project,
  members: ProjectMember[] = []
): boolean {
  if (!user) return false
  if (user.org_role === 'admin') return true
  if (project.my_role === 'maintainer' || project.my_role === 'developer') {
    return true
  }
  return members.some(
    (m) =>
      m.user_id === user.id &&
      (m.role === 'maintainer' || m.role === 'developer')
  )
}

/**
 * Check whether the current user can create a new project in the organization.
 * Org admins, new members with no project assignments yet, and users with
 * maintainer/developer roles on at least one project can create projects;
 * users whose only project memberships are read-only viewer cannot.
 */
export function canUserCreateProject(
  user: User | null | undefined,
  projects: Project[],
  projectMembersByProject: Record<string, ProjectMember[]> = {}
): boolean {
  if (!user) return false
  if (user.org_role === 'admin') return true
  const assignedRoles: string[] = []
  for (const p of projects) {
    if (p.my_role) {
      assignedRoles.push(p.my_role)
      continue
    }
    const member = (projectMembersByProject[p.id] || []).find(
      (m) => m.user_id === user.id
    )
    if (member?.role) {
      assignedRoles.push(member.role)
    }
  }
  if (assignedRoles.length === 0) return true
  return assignedRoles.some((r) => r === 'maintainer' || r === 'developer')
}

/**
 * Compute operational metrics and status for a project.
 */
export function computeProjectSummary(
  project: Project,
  allSeeds: Seed[],
  repos: ProjectRepository[] = [],
  members: ProjectMember[] = [],
  events: ActivityEvent[] = [],
  orgSettings: OrgSettings | null = null
): ProjectSummary {
  const allProjectSeeds = allSeeds.filter((s) => s.project_id === project.id)
  const openSeeds = allProjectSeeds.filter(
    (s) => s.status !== 'merged' && s.status !== 'closed'
  )
  const needsYouSeeds = openSeeds.filter((s) =>
    SEED_NEEDS_ATTENTION_STATUSES.has(s.status)
  )
  const blockedSeeds = openSeeds.filter(
    (s) => s.status === 'blocked' || s.status === 'failed'
  )
  const runningSeeds = openSeeds.filter((s) => s.status === 'running')
  const queuedSeeds = openSeeds.filter(
    (s) => s.status === 'awaiting_approval'
  )
  const mergedSeeds = allProjectSeeds.filter((s) => s.status === 'merged')

  const thirtyDaysAgoMs = Date.now() - 30 * 24 * 60 * 60 * 1000
  const merged30dCount = mergedSeeds.filter((s) => {
    const ts = s.merged_at || s.updated_at || s.created_at
    if (!ts) return true
    return new Date(ts).getTime() >= thirtyDaysAgoMs
  }).length

  const stageCounts: Record<SeedStage, number> = {
    ideate: 0,
    plan: 0,
    execute: 0,
    review: 0,
    merge: mergedSeeds.length,
  }
  for (const seed of openSeeds) {
    stageCounts[seed.stage] = (stageCounts[seed.stage] || 0) + 1
  }

  const totalSpend = allProjectSeeds.reduce((acc, s) => {
    const val = parseFloat(s.spent || '0')
    return acc + (Number.isFinite(val) ? val : 0)
  }, 0)

  const isBudgetOverridden =
    project.monthly_budget !== null && project.monthly_budget !== undefined
  const rawBudget = isBudgetOverridden
    ? project.monthly_budget
    : orgSettings?.default_monthly_budget ?? null
  const parsedBudget = rawBudget !== null ? parseFloat(String(rawBudget)) : NaN
  const effectiveMonthlyBudget =
    Number.isFinite(parsedBudget) && parsedBudget > 0 ? parsedBudget : null

  const primaryRepo =
    repos.find((r) => r.is_primary) || (repos.length > 0 ? repos[0] : null)

  let status: ProjectOperationalStatus = 'idle'
  if (project.archived_at) {
    status = 'archived'
  } else if (blockedSeeds.length > 0) {
    status = 'blocked'
  } else if (needsYouSeeds.length > 0) {
    status = 'needs_action'
  } else if (openSeeds.length > 0) {
    status = 'active'
  }

  let latestMs = 0
  let latestIso: string | null = project.updated_at || project.created_at || null
  if (latestIso) {
    const t = new Date(latestIso).getTime()
    if (Number.isFinite(t)) latestMs = t
  }

  for (const s of allProjectSeeds) {
    const candidate = s.updated_at || s.created_at
    if (candidate) {
      const t = new Date(candidate).getTime()
      if (Number.isFinite(t) && t > latestMs) {
        latestMs = t
        latestIso = candidate
      }
    }
  }

  for (const ev of events) {
    if (ev.project_id === project.id && ev.occurred_at) {
      const t = new Date(ev.occurred_at).getTime()
      if (Number.isFinite(t) && t > latestMs) {
        latestMs = t
        latestIso = ev.occurred_at
      }
    }
  }

  return {
    project,
    status,
    allProjectSeeds,
    openSeeds,
    needsYouSeeds,
    blockedSeeds,
    runningSeeds,
    queuedSeeds,
    mergedSeeds,
    merged30dCount,
    stageCounts,
    totalSpend,
    effectiveMonthlyBudget,
    isBudgetOverridden,
    primaryRepo,
    repos,
    members,
    lastActivityAt: latestIso,
  }
}
