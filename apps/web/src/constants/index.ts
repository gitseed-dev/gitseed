import type { SeedStage, SeedStatus } from '../types'

export const SEED_STAGES: Record<string, SeedStage> = {
  IDEATE: 'ideate',
  PLAN: 'plan',
  EXECUTE: 'execute',
  REVIEW: 'review',
  MERGE: 'merge',
}

export const SEED_OPEN_STATUSES: ReadonlySet<SeedStatus> = new Set<SeedStatus>([
  'staged',
  'queued',
  'running',
  'awaiting_approval',
  'blocked',
  'failed',
  'stopped',
])

export const SEED_NEEDS_ATTENTION_STATUSES: ReadonlySet<SeedStatus> = new Set<SeedStatus>([
  'awaiting_approval',
  'blocked',
  'failed',
  'stopped',
])

export const VIEWS = {
  DASHBOARD: 'dashboard',
  SEEDS: 'ideas',
  PROJECTS: 'projects',
  SETTINGS: 'settings',
  USERS: 'users',
  AGENTS: 'agents',
  BACKENDS: 'backends',
  ROUTES: 'routes',
  TELEMETRY: 'telemetry',
  GUIDE: 'guide',
  SESSIONS: 'sessions',
} as const

export type ViewType = (typeof VIEWS)[keyof typeof VIEWS]

export const VIEW_PATHS: Record<ViewType, string> = {
  [VIEWS.DASHBOARD]: '/overview',
  [VIEWS.SEEDS]: '/seeds',
  [VIEWS.PROJECTS]: '/projects',
  [VIEWS.SETTINGS]: '/settings',
  [VIEWS.USERS]: '/users',
  [VIEWS.AGENTS]: '/agents',
  [VIEWS.BACKENDS]: '/backends',
  [VIEWS.ROUTES]: '/policies',
  [VIEWS.TELEMETRY]: '/activity',
  [VIEWS.GUIDE]: '/guide',
  [VIEWS.SESSIONS]: '/sessions',
}

export const PATH_TO_VIEW: Record<string, ViewType> = {
  '/': VIEWS.DASHBOARD,
  '/overview': VIEWS.DASHBOARD,
  '/dashboard': VIEWS.DASHBOARD,
  '/seeds': VIEWS.SEEDS,
  '/ideas': VIEWS.SEEDS,
  '/projects': VIEWS.PROJECTS,
  '/settings': VIEWS.SETTINGS,
  '/users': VIEWS.USERS,
  '/agents': VIEWS.AGENTS,
  '/backends': VIEWS.BACKENDS,
  '/policies': VIEWS.ROUTES,
  '/stages': VIEWS.ROUTES,
  '/routes': VIEWS.ROUTES,
  '/activity': VIEWS.TELEMETRY,
  '/telemetry': VIEWS.TELEMETRY,
  '/guide': VIEWS.GUIDE,
  '/sessions': VIEWS.SESSIONS,
}

export function parsePath(pathname: string): {
  view: ViewType | null
  detailId: string | null
  subTab: string | null
} {
  const clean = pathname.replace(/\/+$/, '') || '/'
  if (clean === '/login' || clean === '/accept-invite') {
    return { view: null, detailId: null, subTab: null }
  }

  const segments = clean.split('/').filter(Boolean)
  if (segments.length === 0) {
    return { view: VIEWS.DASHBOARD, detailId: null, subTab: null }
  }

  const baseSegment = `/${segments[0].toLowerCase()}`
  const mappedView = PATH_TO_VIEW[baseSegment] || null
  const detailId = segments.length >= 2 ? decodeURIComponent(segments[1]) : null
  const subTab = segments.length >= 3 ? decodeURIComponent(segments[2]) : null

  return { view: mappedView, detailId, subTab }
}

export function buildPath(
  view: ViewType,
  detailId?: string | null,
  subTab?: string | null
): string {
  const base = VIEW_PATHS[view] || '/overview'
  if (!detailId) return base
  const encodedId = encodeURIComponent(detailId)
  if (!subTab || subTab === 'overview') {
    return `${base}/${encodedId}`
  }
  return `${base}/${encodedId}/${encodeURIComponent(subTab)}`
}

/** Tooltip for every "New seed" button: explains the word without renaming it */
export const NEW_SEED_HINT =
  'Describe a feature or fix, and agents take it from idea to pull request.'
