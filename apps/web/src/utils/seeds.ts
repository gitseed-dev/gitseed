import { SEED_OPEN_STATUSES } from '../constants'
import { canUserContributeToProject, canUserManageProject } from './projects'
import type {
  Agent,
  Backend,
  Project,
  ProjectMember,
  ProjectRepository,
  Seed,
  SeedAutonomy,
  SeedStage,
  SeedStatus,
  StagePolicy,
  TaskStatus,
  User,
} from '../types'

export const SEED_STAGE_ORDER: SeedStage[] = [
  'ideate',
  'plan',
  'execute',
  'review',
  'merge',
]

export const STAGE_LABELS: Record<SeedStage, string> = {
  ideate: 'Ideate',
  plan: 'Plan',
  execute: 'Execute',
  review: 'Review',
  merge: 'Merge',
}

export const STATUS_LABELS: Record<SeedStatus, string> = {
  staged: 'Staged',
  queued: 'Queued',
  running: 'Running',
  awaiting_approval: 'Awaiting Approval',
  blocked: 'Blocked',
  failed: 'Failed',
  stopped: 'Stopped',
  merged: 'Merged',
  closed: 'Closed',
}

export const AUTONOMY_LABELS: Record<SeedAutonomy, string> = {
  manual: 'Manual',
  supervised: 'Supervised',
  autonomous: 'Autonomous',
}

export const AUTONOMY_DESCRIPTIONS: Record<SeedAutonomy, string> = {
  manual:
    'Every stage waits for a person, even ones the policy would approve automatically.',
  supervised:
    'Follows your stage policies: pauses at every gate they require.',
  autonomous:
    'Skips gates the policy marks as auto-approvable. Gates that always require a person, like Merge by default, still stop.',
}

export const TASK_STATUS_LABELS: Record<TaskStatus, string> = {
  pending: 'Pending',
  running: 'Running',
  done: 'Done',
  failed: 'Failed',
  skipped: 'Skipped',
}

export function getSeedStatusClasses(status: SeedStatus): string {
  switch (status) {
    case 'staged':
      return 'bg-indigo-500/10 text-indigo-700 dark:text-indigo-300 border-indigo-500/25'
    case 'running':
      return 'bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-500/25'
    case 'queued':
      return 'bg-zinc-500/10 text-zinc-700 dark:text-zinc-300 border-zinc-400/25'
    case 'awaiting_approval':
      return 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/25'
    case 'blocked':
    case 'failed':
      return 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/25'
    case 'merged':
      return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25'
    case 'stopped':
    case 'closed':
      return 'bg-zinc-200/70 dark:bg-zinc-800/70 text-zinc-500 dark:text-zinc-400 border-zinc-300/70 dark:border-zinc-700/70'
    default:
      return 'bg-zinc-500/10 text-zinc-700 dark:text-zinc-300 border-zinc-400/25'
  }
}

export function getTaskStatusClasses(status: TaskStatus): string {
  switch (status) {
    case 'done':
      return 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/25'
    case 'running':
      return 'bg-sky-500/10 text-sky-700 dark:text-sky-300 border-sky-500/25'
    case 'failed':
      return 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/25'
    case 'skipped':
      return 'bg-zinc-200/60 dark:bg-zinc-800/60 text-zinc-500 dark:text-zinc-400 border-zinc-300/60 dark:border-zinc-700/60'
    case 'pending':
    default:
      return 'bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 border-zinc-400/25'
  }
}

export function isSeedOpen(seed: Seed): boolean {
  return SEED_OPEN_STATUSES.has(seed.status)
}

export function getEffectiveSeedPolicies(
  projectId: string,
  projectPoliciesByProject: Record<string, StagePolicy[]>,
  orgPolicies: StagePolicy[]
): StagePolicy[] {
  const projPols = projectPoliciesByProject[projectId]
  if (projPols && projPols.length > 0) return projPols
  return orgPolicies
}

export function canUserDecideSeedStage(
  user: User | null,
  project: Project | null | undefined,
  policy?: StagePolicy | null,
  members: ProjectMember[] = []
): boolean {
  if (!user || !project || project.archived_at) return false
  if (!canUserContributeToProject(user, project, members)) return false
  if (policy?.min_approver_role === 'maintainer') {
    if (!canUserManageProject(user, project, members)) return false
  }
  return true
}

export function canUserApproveSeedStage(
  user: User | null,
  project: Project | null | undefined,
  seed: Seed,
  policy?: StagePolicy | null,
  codeEditorUserIds?: ReadonlySet<string> | string[],
  members: ProjectMember[] = []
): boolean {
  if (!canUserDecideSeedStage(user, project, policy, members)) return false
  if (!user) return false
  if (policy && policy.allow_self_approval === false) {
    if (seed.created_by === user.id) {
      return false
    }
    if (
      (seed.stage === 'review' || seed.stage === 'merge') &&
      codeEditorUserIds
    ) {
      const editedByUser = Array.isArray(codeEditorUserIds)
        ? codeEditorUserIds.includes(user.id)
        : codeEditorUserIds.has(user.id)
      if (editedByUser) {
        return false
      }
    }
  }
  return true
}

const STAGE_ROLE_SLUGS: Record<SeedStage, string> = {
  ideate: 'ideator',
  plan: 'planner',
  execute: 'coder',
  review: 'reviewer',
  merge: 'merger',
}

export function resolveStageRunAgent(
  stage: SeedStage,
  policy: StagePolicy | null | undefined,
  agents: Agent[]
): Agent | undefined {
  if (policy?.agent_id) {
    const configured = agents.find(
      (a) => a.id === policy.agent_id && a.kind === 'stage' && !a.archived_at && a.enabled
    )
    if (configured) return configured
  }
  const stageAgents = agents.filter(
    (a) => a.kind === 'stage' && !a.archived_at && a.enabled
  )
  const roleSlug = STAGE_ROLE_SLUGS[stage]
  const matchingStageAgent = stageAgents.find((a) => {
    const h = a.handle.toLowerCase()
    return (
      h === roleSlug ||
      h.endsWith(`-${roleSlug}`) ||
      Boolean(a.template && a.template.toLowerCase() === roleSlug)
    )
  })
  return matchingStageAgent || stageAgents[0]
}

export function resolveProjectCloneUrl(
  project?: Project | null,
  repos?: ProjectRepository[],
  backends?: Backend[]
): string | null {
  if (!project || !repos || repos.length === 0) return null
  const primary = repos.find((r) => r.is_primary) || repos[0]
  if (!primary?.full_name) return null
  const backend = backends?.find((b) => b.id === primary.backend_id)
  if (backend?.endpoint) {
    try {
      const u = new URL(backend.endpoint)
      if (u.hostname === 'api.github.com') {
        return `https://github.com/${primary.full_name}.git`
      }
      return `${u.origin}/${primary.full_name}.git`
    } catch {
      // Fallback below
    }
  }
  if (backend?.provider === 'gitlab') {
    return `https://gitlab.com/${primary.full_name}.git`
  }
  return `https://github.com/${primary.full_name}.git`
}

