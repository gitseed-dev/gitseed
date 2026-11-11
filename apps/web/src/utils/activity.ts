import type {
  ActivityEvent,
  ActivityType,
  Agent,
  Backend,
  Project,
  Seed,
  User,
} from '../types'
import { formatActorLabel, formatRelativeTime } from './format'

export type ActivityCategory = 'approvals' | 'stages' | 'agents' | 'admin'
export type ActivityIconType =
  | 'approval'
  | 'rejection'
  | 'stage'
  | 'agent'
  | 'agent_failed'
  | 'budget'
  | 'admin'

export interface ParsedActivity {
  id: number
  cursor_seq: number
  category: ActivityCategory
  iconType: ActivityIconType
  actor: string
  prefix: string
  seedTitle?: string
  seedId?: string
  suffix?: string
  fullText: string
  projectName?: string
  projectId?: string
  dayGroup: string
  timeAgo: string
  timeOfDay: string
  occurred_at: string
  event_type: ActivityType
  actor_type: ActivityEvent['actor_type']
  actor_id: string | null
  payload: Record<string, unknown>
  runId?: string
}

function str(val: unknown): string {
  return typeof val === 'string' || typeof val === 'number' ? String(val) : ''
}

export function formatStageName(rawStage: string | undefined | null): string {
  if (!rawStage) return ''
  const s = rawStage.toLowerCase().trim()
  if (s === 'ideating' || s === 'ideate') return 'Ideate'
  if (s === 'planning' || s === 'plan') return 'Plan'
  if (s === 'executing' || s === 'execute') return 'Execute'
  if (s === 'reviewing' || s === 'review') return 'Review'
  if (s === 'completed' || s === 'merge' || s === 'merged') return 'Merge'
  if (s === 'rejected' || s === 'reject' || s === 'closed') return 'Closed'
  return rawStage.charAt(0).toUpperCase() + rawStage.slice(1)
}

function getStageApprovalNoun(rawStage: string | undefined | null): {
  lower: string
  capitalized: string
} {
  const s = (rawStage || '').toLowerCase().trim()
  if (s === 'plan' || s === 'planning') return { lower: 'plan', capitalized: 'Plan' }
  if (s === 'execute' || s === 'executing') {
    return { lower: 'execution', capitalized: 'Execution' }
  }
  if (s === 'review' || s === 'reviewing' || s === 'merge' || s === 'completed') {
    return { lower: 'merge', capitalized: 'Merge' }
  }
  return { lower: 'idea', capitalized: 'Idea' }
}

/**
 * Exhaustively maps every backend ActivityType to its UI filter category.
 */
export function getActivityCategory(type: ActivityType): ActivityCategory {
  switch (type) {
    case 'approval.granted':
    case 'approval.rejected':
    case 'approval.changes_requested':
      return 'approvals'

    case 'seed.created':
    case 'seed.autonomy_changed':
    case 'seed.merged':
    case 'seed.closed':
    case 'seed.reopened':
    case 'seed.stopped':
    case 'seed.resumed':
    case 'stage.changed':
      return 'stages'

    case 'run.started':
    case 'run.succeeded':
    case 'run.failed':
    case 'run.cancelled':
    case 'run.timed_out':
      return 'agents'

    case 'policy.updated':
    case 'budget.raised':
    case 'budget.blocked':
    case 'project.created':
    case 'project.archived':
    case 'project.restored':
    case 'user.invited':
    case 'user.activated':
    case 'user.suspended':
    case 'user.reactivated':
    case 'repository.added':
    case 'repository.removed':
    case 'backend.granted':
    case 'backend.revoked':
    case 'backend.secret_updated':
    case 'member.added':
    case 'member.role_changed':
    case 'member.removed':
    case 'settings.updated':
    case 'pat.created':
    case 'pat.revoked':
    case 'sandbox.started':
      return 'admin'

    default: {
      const _exhaustive: never = type
      return _exhaustive
    }
  }
}

/**
 * Group events into human-readable day buckets:
 * Today · Yesterday · Dec 19 · etc.
 */
export function getActivityDayGroup(dateInput: string | Date | undefined | null): string {
  if (!dateInput) return 'Earlier'
  const d = typeof dateInput === 'string' ? new Date(dateInput) : dateInput
  if (Number.isNaN(d.getTime())) return 'Earlier'

  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const eventDate = new Date(d.getFullYear(), d.getMonth(), d.getDate())
  const diffDays = Math.round((today.getTime() - eventDate.getTime()) / (1000 * 60 * 60 * 24))

  if (diffDays <= 0) return 'Today'
  if (diffDays === 1) return 'Yesterday'

  const isDiffYear = d.getFullYear() !== now.getFullYear()
  return d.toLocaleDateString('en-US', {
    month: 'short',
    day: 'numeric',
    year: isDiffYear ? 'numeric' : undefined,
  })
}

export function formatTimeOfDay(dateInput: string | Date | undefined | null): string {
  if (!dateInput) return ''
  const d = typeof dateInput === 'string' ? new Date(dateInput) : dateInput
  if (Number.isNaN(d.getTime())) return ''
  return d.toLocaleTimeString('en-US', {
    hour: 'numeric',
    minute: '2-digit',
    hour12: true,
  })
}

export interface ParseActivityContext {
  seeds?: Seed[]
  projects?: Project[]
  users?: User[]
  agents?: Agent[]
  backends?: Backend[]
  seedScoped?: boolean
}

export function parseActivityEvent(
  event: ActivityEvent,
  context: ParseActivityContext = {}
): ParsedActivity {
  const d = event.data || {}
  const category = getActivityCategory(event.type)
  const dayGroup = getActivityDayGroup(event.occurred_at)
  const timeAgo = formatRelativeTime(event.occurred_at)
  const timeOfDay = formatTimeOfDay(event.occurred_at)

  const linkedSeed = event.seed_id
    ? context.seeds?.find((s) => s.id === event.seed_id)
    : undefined

  const seedNum = linkedSeed?.number ?? (typeof d.number === 'number' ? d.number : null)
  const rawSeedTitle = linkedSeed?.title || str(d.title)
  const seedTitle =
    seedNum !== null
      ? `#${seedNum}${rawSeedTitle ? ` ${rawSeedTitle}` : ''}`
      : rawSeedTitle || (event.seed_id ? `Seed ${event.seed_id}` : undefined)

  const linkedProject = event.project_id
    ? context.projects?.find((p) => p.id === event.project_id)
    : linkedSeed?.project_id
      ? context.projects?.find((p) => p.id === linkedSeed.project_id)
      : undefined

  const resolvedProjectName =
    linkedProject?.name ||
    (event.project_id || linkedSeed?.project_id ? str(d.name) || str(d.slug) || undefined : undefined)

  // Only show the trailing `· projectName` badge for seed/run/approval events so we don't duplicate
  // project names that are already quoted directly inside project/repo/member admin sentences.
  const showTrailingProject = Boolean((event.seed_id || linkedSeed) && !context.seedScoped)
  const projectName = showTrailingProject ? resolvedProjectName : undefined
  const projectId = linkedProject?.id || event.project_id || undefined

  const actor = formatActorLabel(event, {
    users: context.users,
    agents: context.agents,
  })

  const resolveUserRef = (userId: string, fallbackEmail?: string): string => {
    if (fallbackEmail) return fallbackEmail
    const u = userId ? context.users?.find((item) => item.id === userId) : undefined
    return u ? u.preferred_name || u.name || u.email : userId || 'user'
  }

  const make = (
    iconType: ActivityIconType,
    prefix: string,
    sTitle?: string,
    sId?: string,
    suffix?: string
  ): ParsedActivity => {
    const fullText = [prefix.trim(), sTitle?.trim() || '', suffix?.trim() || '']
      .filter(Boolean)
      .join(' ')
    return {
      id: event.id,
      cursor_seq: event.id,
      category,
      iconType,
      actor,
      prefix: sTitle && prefix && !prefix.endsWith(' ') ? `${prefix} ` : prefix,
      seedTitle: sTitle,
      seedId: sId,
      suffix,
      fullText,
      projectName,
      projectId,
      dayGroup,
      timeAgo,
      timeOfDay,
      occurred_at: event.occurred_at,
      event_type: event.type,
      actor_type: event.actor_type,
      actor_id: event.actor_id,
      payload: d,
      runId: str(d.run_id) || undefined,
    }
  }

  if (context.seedScoped) {
    const makeSeedScoped = (iconType: ActivityIconType, summary: string) =>
      make(iconType, `${summary} · ${actor}`, undefined, event.seed_id || undefined)

    switch (event.type) {
      case 'seed.created':
        return makeSeedScoped('stage', 'Seed created')
      case 'seed.autonomy_changed':
        return makeSeedScoped(
          'stage',
          `Autonomy changed: ${str(d.from) || 'previous'} → ${str(d.to) || 'updated'}`
        )
      case 'seed.merged':
        return makeSeedScoped('approval', 'Seed merged')
      case 'seed.closed':
        return makeSeedScoped(
          'rejection',
          d.reason === 'approval_rejected'
            ? 'Seed closed after rejection'
            : `Seed closed (${str(d.stage) || 'current'})`
        )
      case 'seed.reopened':
        return makeSeedScoped('stage', `Seed reopened (${str(d.stage) || 'current'})`)
      case 'seed.stopped':
        return makeSeedScoped('agent_failed', `Agent stopped (${formatStageName(str(d.stage)) || 'current stage'})`)
      case 'seed.resumed':
        return makeSeedScoped('agent', `Resumed (${formatStageName(str(d.stage)) || 'current stage'})`)
      case 'stage.changed': {
        if (d.reason === 'code_edited') {
          const shaShort = str(d.commit_sha).slice(0, 7)
          const fileCount =
            typeof d.changed_files === 'number'
              ? ` (${d.changed_files} file${d.changed_files === 1 ? '' : 's'})`
              : ''
          return makeSeedScoped(
            'stage',
            `Code edited on branch${shaShort ? ` @ ${shaShort}` : ''}${fileCount} · prior approvals reset`
          )
        }
        return makeSeedScoped(
          'stage',
          `Stage advanced: ${str(d.from) || 'previous'} → ${str(d.to) || 'next'}`
        )
      }
      case 'approval.granted':
        return makeSeedScoped('approval', `Stage gate approved (${str(d.stage) || 'stage'})`)
      case 'approval.rejected':
        return makeSeedScoped('rejection', `Stage gate rejected (${str(d.stage) || 'stage'})`)
      case 'approval.changes_requested':
        return makeSeedScoped('rejection', `Changes requested (${str(d.stage) || 'stage'})`)
      case 'run.started':
        return makeSeedScoped(
          'agent',
          `Run started (${str(d.stage) || 'stage'}${d.attempt ? ` #${str(d.attempt)}` : ''})`
        )
      case 'run.succeeded': {
        const tok =
          (typeof d.input_tokens === 'number' ? d.input_tokens : 0) +
          (typeof d.output_tokens === 'number' ? d.output_tokens : 0)
        const tokPart = tok > 0 ? ` · ${tok.toLocaleString()} tok` : ''
        const costPart = d.cost
          ? ` · $${parseFloat(str(d.cost) || '0').toFixed(2)}`
          : ''
        return makeSeedScoped(
          'agent',
          `Run succeeded (${str(d.stage) || 'stage'}${costPart}${tokPart})`
        )
      }
      case 'run.failed':
        return makeSeedScoped(
          'agent_failed',
          `Run failed (${str(d.stage) || 'stage'}${d.attempt ? ` #${str(d.attempt)}` : ''})`
        )
      case 'run.cancelled':
        return makeSeedScoped('agent_failed', `Run cancelled (${str(d.stage) || 'stage'})`)
      case 'run.timed_out':
        return makeSeedScoped('agent_failed', `Run timed out (${str(d.stage) || 'stage'})`)
      case 'budget.raised':
        if (d.checkpoint_approved_through) {
          return makeSeedScoped(
            'budget',
            `Spend checkpoint raised to $${str(d.checkpoint_approved_through)}`
          )
        }
        if (d.budget_cap) {
          return makeSeedScoped('budget', `Seed budget cap raised to $${str(d.budget_cap)}`)
        }
        return makeSeedScoped('budget', 'Budget raised')
      case 'budget.blocked':
        return makeSeedScoped(
          'budget',
          d.reason === 'spend_checkpoint'
            ? `Paused at spend checkpoint ($${str(d.spend_checkpoint)})`
            : `Blocked at budget cap ($${str(d.budget_cap)})`
        )
      default:
        break
    }
  }

  switch (event.type) {
    case 'approval.granted': {
      const noun = getStageApprovalNoun(str(d.stage) || linkedSeed?.stage)
      if (d.automatic === true || event.actor_type === 'system') {
        return make(
          'approval',
          `${noun.capitalized} auto-approved for`,
          seedTitle || 'Seed',
          event.seed_id || undefined,
          ' (autonomous policy)'
        )
      }
      return make(
        'approval',
        `${actor} approved the ${noun.lower} for`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'approval.rejected': {
      const noun = getStageApprovalNoun(str(d.stage) || linkedSeed?.stage)
      return make(
        'rejection',
        `${actor} rejected the ${noun.lower} for`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'approval.changes_requested': {
      const noun = getStageApprovalNoun(str(d.stage) || linkedSeed?.stage)
      return make(
        'rejection',
        `${actor} requested changes to the ${noun.lower} for`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'seed.created':
      return make(
        'stage',
        `${actor} created seed`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )

    case 'seed.autonomy_changed':
      return make(
        'stage',
        `${actor} changed autonomy (${str(d.from) || 'previous'} → ${str(d.to) || 'updated'}) on`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )

    case 'seed.merged':
      return make(
        'approval',
        `${actor} merged`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )

    case 'seed.closed':
      return make(
        'rejection',
        d.reason === 'approval_rejected'
          ? `${actor} closed after rejection`
          : `${actor} closed`,
        seedTitle || 'Seed',
        event.seed_id || undefined,
        d.stage ? ` (${formatStageName(str(d.stage))})` : undefined
      )

    case 'seed.reopened':
      return make(
        'stage',
        `${actor} reopened`,
        seedTitle || 'Seed',
        event.seed_id || undefined,
        d.stage ? ` in ${formatStageName(str(d.stage))}` : undefined
      )

    case 'seed.stopped':
      return make(
        'agent_failed',
        `${actor} stopped the agent on`,
        seedTitle || 'Seed',
        event.seed_id || undefined,
        d.stage ? ` (${formatStageName(str(d.stage))})` : undefined
      )

    case 'seed.resumed':
      return make(
        'agent',
        `${actor} resumed`,
        seedTitle || 'Seed',
        event.seed_id || undefined,
        d.stage ? ` in ${formatStageName(str(d.stage))}` : undefined
      )

    case 'stage.changed': {
      if (d.reason === 'code_edited') {
        const shaShort = str(d.commit_sha).slice(0, 7)
        return make(
          'stage',
          `${actor} pushed code changes${shaShort ? ` (${shaShort})` : ''} to`,
          seedTitle || 'Seed',
          event.seed_id || undefined,
          ' · prior approvals reset'
        )
      }
      const fromStage = formatStageName(str(d.from)) || 'Previous'
      const toStage = formatStageName(str(d.to)) || 'Next'
      return make(
        'stage',
        '',
        seedTitle || 'Seed',
        event.seed_id || undefined,
        ` moved ${fromStage} → ${toStage}`
      )
    }

    case 'run.started': {
      const stageLabel = formatStageName(str(d.stage))
      const attempt = typeof d.attempt === 'number' && d.attempt > 1 ? ` (attempt #${d.attempt})` : ''
      return make(
        'agent',
        `${actor} started ${stageLabel ? `${stageLabel} ` : ''}run${attempt} on`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'run.succeeded': {
      const stageLabel = formatStageName(str(d.stage))
      const costNum = parseFloat(str(d.cost) || '0')
      const tok =
        (typeof d.input_tokens === 'number' ? d.input_tokens : 0) +
        (typeof d.output_tokens === 'number' ? d.output_tokens : 0)
      const metaParts: string[] = []
      if (costNum > 0) metaParts.push(`$${costNum.toFixed(2)}`)
      if (tok > 0) metaParts.push(`${tok.toLocaleString()} tok`)
      const metaSuffix = metaParts.length > 0 ? ` (${metaParts.join(' · ')})` : ''
      return make(
        'agent',
        `${actor} completed ${stageLabel ? `${stageLabel} ` : ''}run${metaSuffix} for`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'run.failed': {
      const stageLabel = formatStageName(str(d.stage))
      const attempt = typeof d.attempt === 'number' ? ` (attempt #${d.attempt})` : ''
      return make(
        'agent_failed',
        `${actor} failed ${stageLabel ? `${stageLabel} ` : ''}run${attempt} on`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'run.cancelled': {
      const stageLabel = formatStageName(str(d.stage))
      return make(
        'agent_failed',
        `${actor} cancelled ${stageLabel ? `${stageLabel} ` : ''}run on`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'run.timed_out': {
      const stageLabel = formatStageName(str(d.stage))
      return make(
        'agent_failed',
        `${stageLabel || 'Stage'} run timed out on`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )
    }

    case 'budget.raised':
      if (d.checkpoint_approved_through) {
        return make(
          'budget',
          `${actor} approved spend checkpoint through $${str(d.checkpoint_approved_through)} on`,
          seedTitle || 'Seed',
          event.seed_id || undefined
        )
      }
      if (d.budget_cap) {
        return make(
          'budget',
          `${actor} raised seed budget cap to $${str(d.budget_cap)} on`,
          seedTitle || 'Seed',
          event.seed_id || undefined
        )
      }
      return make(
        'budget',
        `${actor} updated monthly budget on project "${resolvedProjectName || 'project'}"`
      )

    case 'budget.blocked':
      return make(
        'budget',
        d.reason === 'spend_checkpoint'
          ? `Paused at spend checkpoint ($${str(d.spend_checkpoint)}) on`
          : `Paused at budget cap ($${str(d.budget_cap)}) on`,
        seedTitle || 'Seed',
        event.seed_id || undefined
      )

    case 'policy.updated': {
      const stageLabel = formatStageName(str(d.stage)) || 'stage'
      if (d.reset_to_org_default) {
        return make(
          'admin',
          `${actor} reset ${stageLabel} policy to org default on "${resolvedProjectName || 'project'}"`
        )
      }
      if (event.project_id) {
        return make(
          'admin',
          `${actor} updated ${stageLabel} policy override on "${resolvedProjectName || 'project'}"`
        )
      }
      return make('admin', `${actor} updated org default ${stageLabel} policy`)
    }

    case 'project.created':
      return make(
        'admin',
        `${actor} created project "${resolvedProjectName || str(d.slug) || 'project'}"`
      )

    case 'project.archived':
      return make(
        'admin',
        `${actor} archived project "${resolvedProjectName || str(d.slug) || 'project'}"`
      )

    case 'project.restored':
      return make(
        'admin',
        `${actor} restored project "${resolvedProjectName || str(d.slug) || 'project'}"`
      )

    case 'repository.added':
      return make(
        'admin',
        `${actor} connected repository ${str(d.full_name) || 'repo'} to "${resolvedProjectName || 'project'}"`
      )

    case 'repository.removed':
      return make(
        'admin',
        `${actor} detached repository ${str(d.full_name) || 'repo'} from "${resolvedProjectName || 'project'}"`
      )

    case 'member.added':
      return make(
        'admin',
        `${actor} added ${resolveUserRef(str(d.user_id), str(d.email))} (${str(d.role) || 'member'}) to "${resolvedProjectName || 'project'}"`
      )

    case 'member.role_changed':
      return make(
        'admin',
        `${actor} changed project role (${str(d.from)} → ${str(d.to)}) for ${resolveUserRef(str(d.user_id))} on "${resolvedProjectName || 'project'}"`
      )

    case 'member.removed':
      return make(
        'admin',
        `${actor} removed ${resolveUserRef(str(d.user_id))} from "${resolvedProjectName || 'project'}"`
      )

    case 'user.invited':
      if (d.reinvite) {
        return make('admin', `${actor} re-issued invitation link to ${str(d.email) || 'user'}`)
      }
      return make(
        'admin',
        `${actor} invited ${str(d.email) || 'user'} as ${str(d.org_role) || 'member'}`
      )

    case 'user.activated':
      return make(
        'admin',
        `${actor !== 'User' ? actor : str(d.email) || resolveUserRef(str(d.user_id))} activated their account`
      )

    case 'user.suspended':
      return make(
        'admin',
        `${actor} suspended account for ${str(d.email) || resolveUserRef(str(d.user_id))}`
      )

    case 'user.reactivated':
      return make(
        'admin',
        `${actor} reactivated account for ${str(d.email) || resolveUserRef(str(d.user_id))}`
      )

    case 'backend.granted': {
      const backendName =
        str(d.backend_name) ||
        context.backends?.find((b) => b.id === str(d.backend_id))?.name ||
        str(d.backend_id) ||
        'backend'
      if (d.agent_handle || d.agent_id) {
        const agentHandle =
          str(d.agent_handle) ||
          context.agents?.find((a) => a.id === str(d.agent_id))?.handle ||
          str(d.agent_id)
        return make('admin', `${actor} granted backend ${backendName} to @${agentHandle}`)
      }
      return make(
        'admin',
        `${actor} granted backend ${backendName} to "${resolvedProjectName || 'project'}"`
      )
    }

    case 'backend.revoked': {
      const backendName =
        str(d.backend_name) ||
        context.backends?.find((b) => b.id === str(d.backend_id))?.name ||
        str(d.backend_id) ||
        'backend'
      if (d.agent_id) {
        const agent = context.agents?.find((a) => a.id === str(d.agent_id))
        return make(
          'admin',
          `${actor} revoked backend ${backendName} from ${agent ? `@${agent.handle}` : str(d.agent_id)}`
        )
      }
      return make(
        'admin',
        `${actor} revoked backend ${backendName} from "${resolvedProjectName || 'project'}"`
      )
    }

    case 'backend.secret_updated': {
      const backendName =
        str(d.name) ||
        context.backends?.find((b) => b.id === str(d.backend_id))?.name ||
        str(d.backend_id) ||
        'backend'
      return make('admin', `${actor} updated credentials on backend ${backendName}`)
    }

    case 'settings.updated': {
      const keys =
        d.changes && typeof d.changes === 'object' ? Object.keys(d.changes) : []
      return make(
        'admin',
        keys.length > 0
          ? `${actor} updated organization settings (${keys.join(', ')})`
          : `${actor} updated organization settings`
      )
    }

    case 'pat.created':
      return make(
        'admin',
        `${actor} created personal access token "${str(d.name) || str(d.token_id) || 'token'}"`
      )

    case 'pat.revoked':
      return make(
        'admin',
        `${actor} revoked personal access token ${str(d.name) ? `"${str(d.name)}"` : str(d.token_id) || ''}`.trim()
      )

    case 'sandbox.started': {
      const refStr = str(d.ref) || 'main'
      const shaStr = str(d.head_sha) ? ` @ ${str(d.head_sha)}` : ''
      const cmdStr = str(d.command) ? ` (${str(d.command)})` : ''
      return make(
        'admin',
        `${actor} started sandbox on ${refStr}${shaStr}${cmdStr}`
      )
    }

    default: {
      const _exhaustive: never = event.type
      return make('admin', String(_exhaustive))
    }
  }
}

export interface ActivityEventTypeOption {
  type: ActivityType
  label: string
  category: ActivityCategory
}

export interface ActivityEventTypeGroup {
  category: ActivityCategory
  label: string
  options: ActivityEventTypeOption[]
}

export const ACTIVITY_EVENT_TYPE_GROUPS: ActivityEventTypeGroup[] = [
  {
    category: 'approvals',
    label: 'Approvals',
    options: [
      { type: 'approval.changes_requested', label: 'Changes Requested', category: 'approvals' },
      { type: 'approval.granted', label: 'Approval Granted', category: 'approvals' },
      { type: 'approval.rejected', label: 'Approval Rejected', category: 'approvals' },
    ],
  },
  {
    category: 'stages',
    label: 'Stages & Seeds',
    options: [
      { type: 'seed.autonomy_changed', label: 'Autonomy Changed', category: 'stages' },
      { type: 'seed.closed', label: 'Seed Closed', category: 'stages' },
      { type: 'seed.created', label: 'Seed Created', category: 'stages' },
      { type: 'seed.merged', label: 'Seed Merged', category: 'stages' },
      { type: 'seed.reopened', label: 'Seed Reopened', category: 'stages' },
      { type: 'seed.resumed', label: 'Seed Resumed', category: 'stages' },
      { type: 'seed.stopped', label: 'Seed Stopped', category: 'stages' },
      { type: 'stage.changed', label: 'Stage Changed', category: 'stages' },
    ],
  },
  {
    category: 'agents',
    label: 'Agent Runs',
    options: [
      { type: 'run.cancelled', label: 'Run Cancelled', category: 'agents' },
      { type: 'run.failed', label: 'Run Failed', category: 'agents' },
      { type: 'run.started', label: 'Run Started', category: 'agents' },
      { type: 'run.succeeded', label: 'Run Succeeded', category: 'agents' },
      { type: 'run.timed_out', label: 'Run Timed Out', category: 'agents' },
    ],
  },
  {
    category: 'admin',
    label: 'Governance & Admin',
    options: [
      { type: 'backend.granted', label: 'Backend Granted', category: 'admin' },
      { type: 'backend.revoked', label: 'Backend Revoked', category: 'admin' },
      { type: 'backend.secret_updated', label: 'Backend Secret Updated', category: 'admin' },
      { type: 'budget.blocked', label: 'Budget Blocked', category: 'admin' },
      { type: 'budget.raised', label: 'Budget Raised', category: 'admin' },
      { type: 'member.added', label: 'Member Added', category: 'admin' },
      { type: 'member.removed', label: 'Member Removed', category: 'admin' },
      { type: 'member.role_changed', label: 'Member Role Changed', category: 'admin' },
      { type: 'pat.created', label: 'PAT Created', category: 'admin' },
      { type: 'pat.revoked', label: 'PAT Revoked', category: 'admin' },
      { type: 'policy.updated', label: 'Policy Updated', category: 'admin' },
      { type: 'project.archived', label: 'Project Archived', category: 'admin' },
      { type: 'project.created', label: 'Project Created', category: 'admin' },
      { type: 'project.restored', label: 'Project Restored', category: 'admin' },
      { type: 'repository.added', label: 'Repository Added', category: 'admin' },
      { type: 'repository.removed', label: 'Repository Removed', category: 'admin' },
      { type: 'sandbox.started', label: 'Sandbox Started', category: 'admin' },
      { type: 'settings.updated', label: 'Settings Updated', category: 'admin' },
      { type: 'user.activated', label: 'User Activated', category: 'admin' },
      { type: 'user.invited', label: 'User Invited', category: 'admin' },
      { type: 'user.reactivated', label: 'User Reactivated', category: 'admin' },
      { type: 'user.suspended', label: 'User Suspended', category: 'admin' },
    ],
  },
]

export function formatActivityEventTypeLabel(type: string): string {
  for (const group of ACTIVITY_EVENT_TYPE_GROUPS) {
    const match = group.options.find((opt) => opt.type === type)
    if (match) return match.label
  }
  return type
    .replace(/[._]/g, ' ')
    .replace(/\b\w/g, (ch) => ch.toUpperCase())
}

export function parseActivityEvents(
  events: ActivityEvent[],
  context: ParseActivityContext = {}
): ParsedActivity[] {
  return [...events]
    .sort((a, b) => {
      const tsA = a.occurred_at ? Date.parse(a.occurred_at) : NaN
      const tsB = b.occurred_at ? Date.parse(b.occurred_at) : NaN
      if (!Number.isNaN(tsA) && !Number.isNaN(tsB) && tsB !== tsA) {
        return tsB - tsA
      }
      const idDiff = Number(b.id) - Number(a.id)
      if (!Number.isNaN(idDiff) && idDiff !== 0) return idDiff
      return 0
    })
    .map((ev) => parseActivityEvent(ev, context))
}
