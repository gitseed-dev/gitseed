import { useEffect } from 'react'
import { VIEWS, type ViewType } from '../constants'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import type { Project, Seed, User } from '../types'

const VIEW_LABELS: Record<ViewType, string> = {
  [VIEWS.DASHBOARD]: 'Overview',
  [VIEWS.PROJECTS]: 'Projects',
  [VIEWS.SEEDS]: 'Seeds',
  [VIEWS.TELEMETRY]: 'Activity',
  [VIEWS.BACKENDS]: 'Backends',
  [VIEWS.AGENTS]: 'Agents',
  [VIEWS.ROUTES]: 'Policies',
  [VIEWS.USERS]: 'Users',
  [VIEWS.SETTINGS]: 'Settings',
  [VIEWS.GUIDE]: 'Guide',
  [VIEWS.SESSIONS]: 'Sessions',
}

const PROJECT_TAB_LABELS: Record<string, string> = {
  seeds: 'Seeds',
  members: 'Members',
  policies: 'Policies',
  settings: 'Settings',
}

export interface DocumentTitleOptions {
  pathname: string
  isAuthenticated: boolean
  activeView: ViewType
  activeDetailId: string | null
  activeDetailTab: string | null
  selectedProjectObj: Project | null
  projects: Project[]
  allSeeds: Seed[]
  users: User[]
}

export function buildDocumentTitle({
  pathname,
  isAuthenticated,
  activeView,
  activeDetailId,
  activeDetailTab,
  selectedProjectObj,
  projects,
  allSeeds,
  users,
}: DocumentTitleOptions): string {
  if (pathname === '/accept-invite') {
    return 'Accept Invite · GitSeed'
  }
  if (!isAuthenticated) {
    return 'Sign in · GitSeed'
  }

  if (activeView === VIEWS.SEEDS && activeDetailId) {
    const seed = allSeeds.find((s) => {
      if (s.id === activeDetailId) return true
      if (activeDetailId.includes('#')) {
        const [slug, numStr] = activeDetailId.split('#', 2)
        const proj = projects.find((p) => p.slug === slug)
        return Boolean(
          proj && s.project_id === proj.id && s.number === parseInt(numStr, 10)
        )
      }
      return false
    })
    if (seed) {
      const seedProj = projects.find((p) => p.id === seed.project_id)
      return `${seed.title} · #${seed.number}${seedProj ? ` · ${seedProj.name}` : ''} · GitSeed`
    }
  }

  if (activeView === VIEWS.PROJECTS && activeDetailId) {
    const proj = projects.find(
      (p) => p.id === activeDetailId || p.slug === activeDetailId
    )
    if (proj) {
      const tabPrefix =
        activeDetailTab && PROJECT_TAB_LABELS[activeDetailTab]
          ? `${PROJECT_TAB_LABELS[activeDetailTab]} · `
          : ''
      return `${tabPrefix}${proj.name} · GitSeed`
    }
  }

  if (activeView === VIEWS.USERS && activeDetailId) {
    const targetUser = users.find((u) => u.id === activeDetailId)
    if (targetUser) {
      const userName =
        targetUser.preferred_name || targetUser.name || targetUser.email
      return `${userName} · Users · GitSeed`
    }
  }

  const viewLabel = VIEW_LABELS[activeView] || 'Overview'
  const isProjectScopedView =
    activeView === VIEWS.DASHBOARD ||
    activeView === VIEWS.SEEDS ||
    activeView === VIEWS.TELEMETRY
  const scopeSuffix =
    isProjectScopedView && selectedProjectObj
      ? ` · ${selectedProjectObj.name}`
      : ''

  return `${viewLabel}${scopeSuffix} · GitSeed`
}

export function useDocumentTitle(): void {
  const { isAuthenticated } = useAuth()
  const {
    activeView,
    activeDetailId,
    activeDetailTab,
    selectedProjectObj,
    projects,
    allSeeds,
    users,
  } = useData()

  useEffect(() => {
    document.title = buildDocumentTitle({
      pathname: window.location.pathname,
      isAuthenticated,
      activeView,
      activeDetailId,
      activeDetailTab,
      selectedProjectObj,
      projects,
      allSeeds,
      users,
    })
  }, [
    isAuthenticated,
    activeView,
    activeDetailId,
    activeDetailTab,
    selectedProjectObj,
    projects,
    allSeeds,
    users,
  ])
}

