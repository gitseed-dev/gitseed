import { SEED_NEEDS_ATTENTION_STATUSES } from '../constants'
import type {
  ActivityEvent,
  Agent,
  Backend,
  ContextWindowInfo,
  Project,
  Run,
  Seed,
  User,
} from '../types'

export interface ActivityFormatContext {
  seeds?: Seed[]
  projects?: Project[]
  users?: User[]
  agents?: Agent[]
  backends?: Backend[]
}

export function seedNeedsAttention(seed: Seed): boolean {
  return SEED_NEEDS_ATTENTION_STATUSES.has(seed.status)
}

export function getSeedAttentionReason(seed: Seed): string {
  if (seed.blocked_reason) return seed.blocked_reason
  switch (seed.status) {
    case 'awaiting_approval':
      return seed.stage === 'review' || seed.stage === 'merge'
        ? 'Awaiting merge approval'
        : seed.stage === 'execute'
          ? 'Awaiting execution approval'
          : seed.stage === 'plan'
            ? 'Awaiting plan approval'
            : 'Awaiting idea approval'
    case 'blocked':
      return 'Blocked · Operator action required'
    case 'failed':
      return 'Run failed · Needs investigation'
    case 'stopped':
      return 'Stopped · Resume when ready'
    default:
      return `Status: ${seed.status.replace('_', ' ')}`
  }
}

/* eslint-disable no-control-regex */
// Terminal escape sequences: CSI (colors, cursor moves), OSC (titles/links), and lone ESC pairs
const ANSI_ESCAPE_RE = /\x1b\[[0-?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[@-Z\\-_]/g
// SGR fragments whose ESC byte was already lost upstream (shows up as "[0m", "[1;32m")
const ORPHAN_SGR_RE = /\[(?:\d{1,3}(?:;\d{1,3})*)?m/g
/* eslint-enable no-control-regex */

/** Strip terminal color/control codes so CLI output reads cleanly in plain-text views. */
export function stripAnsi(text: string): string {
  if (!text) return text
  return text
    .replace(ANSI_ESCAPE_RE, '')
    // JSON-escaped ESC inside stream-json lines ("\u001b[0m") before they're parsed
    .replace(/\\u001b(?:\[[0-?]*[ -/]*[@-~])?/gi, '')
    .replace(ORPHAN_SGR_RE, '')
    .replace(/\r(?!\n)/g, '\n')
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return 'just now'
  const diffSec = Math.max(0, Math.floor((Date.now() - new Date(iso).getTime()) / 1000))
  if (diffSec < 60) return 'just now'
  const mins = Math.floor(diffSec / 60)
  if (mins < 60) return `${mins}m ago`
  const hrs = Math.floor(mins / 60)
  if (hrs < 24) return `${hrs}h ago`
  const days = Math.floor(hrs / 24)
  return `${days}d ago`
}

export function formatUsd(amount: string | number | null | undefined): string {
  const num = typeof amount === 'number' ? amount : parseFloat(amount || '0')
  return `$${(Number.isFinite(num) ? num : 0).toFixed(2)}`
}

export function formatTokens(tokens?: number | null): string {
  if (tokens === null || tokens === undefined || !Number.isFinite(tokens) || tokens <= 0) {
    return '0'
  }
  if (tokens < 1000) return `${Math.round(tokens)}`
  if (tokens < 1_000_000) {
    return `${(tokens / 1000).toFixed(1).replace(/\.0$/, '')}k`
  }
  return `${(tokens / 1_000_000).toFixed(2).replace(/\.?0+$/, '')}M`
}

export function resolveRunContextWindow(
  run?: Pick<Run, 'output'> | null
): ContextWindowInfo | null {
  if (!run || !run.output || typeof run.output !== 'object') return null
  const raw = (run.output as Record<string, unknown>).context_window
  if (!raw || typeof raw !== 'object') return null
  const obj = raw as Record<string, unknown>
  const maxTokens = Number(obj.max_tokens) || 0
  const usedTokens = Math.max(0, Number(obj.used_tokens) || 0)
  const peakTokens = Math.max(usedTokens, Number(obj.peak_tokens) || 0)
  const turns = Math.max(0, Number(obj.turns) || 0)
  if (maxTokens <= 0 || (usedTokens <= 0 && peakTokens <= 0)) {
    return null
  }
  const pct =
    typeof obj.utilization_pct === 'number' && obj.utilization_pct > 0
      ? Math.min(100, Math.round(obj.utilization_pct * 10) / 10)
      : Math.min(100, Math.round((usedTokens / maxTokens) * 1000) / 10)
  return {
    used_tokens: usedTokens,
    peak_tokens: peakTokens,
    max_tokens: maxTokens,
    utilization_pct: pct,
    turns,
  }
}

export function formatDurationMs(ms?: number | null): string {
  if (ms === null || ms === undefined || !Number.isFinite(ms) || ms < 0) {
    return '-'
  }
  if (ms < 1000) return `${Math.round(ms)}ms`
  const sec = Math.floor(ms / 1000)
  if (sec < 60) return `${sec}s`
  const mins = Math.floor(sec / 60)
  const remSec = sec % 60
  if (mins < 60) return remSec > 0 ? `${mins}m ${remSec}s` : `${mins}m`
  const hrs = Math.floor(mins / 60)
  const remMins = mins % 60
  return remMins > 0 ? `${hrs}h ${remMins}m` : `${hrs}h`
}

export function formatBytes(bytes?: number | null): string {
  if (bytes === null || bytes === undefined || !Number.isFinite(bytes)) return '-'
  if (bytes <= 0) return '0 B'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(2)} MB`
}

export function formatVersionLabel(version?: string | null): string {
  const raw = (version || '0.1.0').trim()
  if (!raw || raw.toLowerCase() === 'dev' || raw.toLowerCase() === 'development') {
    return 'dev'
  }
  return raw.startsWith('v') ? raw : `v${raw}`
}

export function formatActorLabel(
  event: Pick<ActivityEvent, 'actor_type' | 'actor_id'>,
  context: Pick<ActivityFormatContext, 'users' | 'agents'> = {}
): string {
  if (event.actor_type === 'system') return 'System'
  if (event.actor_type === 'agent') {
    const agent = event.actor_id
      ? context.agents?.find((a) => a.id === event.actor_id)
      : undefined
    return agent ? `@${agent.handle}` : event.actor_id || 'Agent'
  }
  const user = event.actor_id
    ? context.users?.find((u) => u.id === event.actor_id)
    : undefined
  if (user) {
    return user.preferred_name || user.name || user.email
  }
  return event.actor_id || 'User'
}

