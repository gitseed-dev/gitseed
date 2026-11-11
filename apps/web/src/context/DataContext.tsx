import React, { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from 'react'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'
import { queryKeys } from '../api/queryClient'
import { VIEWS, buildPath, parsePath, type ViewType } from '../constants'
import type {
  ActivityEvent,
  Agent,
  Backend,
  OrgSettings,
  Project,
  ProjectMember,
  ProjectRepository,
  Run,
  Seed,
  Skill,
  StagePolicy,
  User,
} from '../types'
import { useAuth } from './AuthContext'
import { useTheme } from './ThemeContext'

interface DataContextType {
  activeView: ViewType
  activeDetailId: string | null
  activeDetailTab: string | null
  viewFilterParams: Record<string, string> | null
  setActiveView: (view: ViewType, filterParams?: Record<string, string>) => void
  navigateToDetail: (
    view: ViewType,
    id: string,
    subTab?: string,
    queryParams?: Record<string, string>
  ) => void
  clearDetail: () => void
  selectedProject: string | null
  setSelectedProject: (projectId: string | null) => void
  selectedProjectObj: Project | null
  projects: Project[]
  projectMembersByProject: Record<string, ProjectMember[]>
  projectPoliciesByProject: Record<string, StagePolicy[]>
  projectReposByProject: Record<string, ProjectRepository[]>
  seeds: Seed[]
  allSeeds: Seed[]
  runs: Run[]
  events: ActivityEvent[]
  allEvents: ActivityEvent[]
  backends: Backend[]
  agents: Agent[]
  skills: Skill[]
  policies: StagePolicy[]
  users: User[]
  orgSettings: OrgSettings | null
  showGuideInSidebar: boolean
  setShowGuideInSidebar: (show: boolean) => void
  sidebarSearchOpen: boolean
  searchModalInitialTab: 'search' | 'chat' | 'brainstorm'
  searchModalProjectId: string | null
  openSearchModal: (
    initialTab?: 'search' | 'chat' | 'brainstorm' | React.MouseEvent,
    projectId?: string | null
  ) => void
  closeSearchModal: () => void
  isProfileDrawerOpen: boolean
  openProfileDrawer: () => void
  closeProfileDrawer: () => void
  isInitialLoading: boolean
  refreshData: () => Promise<void>
}

const DataContext = createContext<DataContextType | undefined>(undefined)

const EMPTY_PROJECTS: Project[] = []
const EMPTY_SEEDS: Seed[] = []
const EMPTY_RUNS: Run[] = []
const EMPTY_EVENTS: ActivityEvent[] = []
const EMPTY_BACKENDS: Backend[] = []
const EMPTY_AGENTS: Agent[] = []
const EMPTY_SKILLS: Skill[] = []
const EMPTY_POLICIES: StagePolicy[] = []
const EMPTY_USERS: User[] = []

const GUIDE_STORAGE_KEY = 'gitseed_show_guide_nav'
const SSE_INVALIDATE_THROTTLE_MS = 500
export const SEEDS_LIST_LIMIT = 200

const PROJECT_METADATA_VIEWS: ReadonlySet<ViewType> = new Set<ViewType>([
  VIEWS.DASHBOARD,
  VIEWS.PROJECTS,
  VIEWS.SEEDS,
  VIEWS.SESSIONS,
  VIEWS.USERS,
])

export const DataProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { isAuthenticated, user } = useAuth()
  const { setOrgDefaultTheme, setOrgDefaultAmbientBg } = useTheme()
  const isAdmin = user?.org_role === 'admin'
  const queryClient = useQueryClient()

  const initialRoute = useMemo(() => parsePath(window.location.pathname), [])
  const [activeView, setActiveViewState] = useState<ViewType>(
    initialRoute.view || VIEWS.DASHBOARD
  )
  const [activeDetailId, setActiveDetailId] = useState<string | null>(initialRoute.detailId)
  const [activeDetailTab, setActiveDetailTab] = useState<string | null>(initialRoute.subTab)
  const [viewFilterParams, setViewFilterParams] = useState<Record<string, string> | null>(
    null
  )

  const activeViewRef = useRef<ViewType>(activeView)
  activeViewRef.current = activeView
  const activeDetailIdRef = useRef<string | null>(activeDetailId)
  activeDetailIdRef.current = activeDetailId

  const [selectedProject, setSelectedProjectState] = useState<string | null>(() =>
    localStorage.getItem('gitseed_selected_project')
  )
  const [showGuideInSidebar, setShowGuideInSidebarState] = useState<boolean>(() => {
    const saved = localStorage.getItem(GUIDE_STORAGE_KEY)
    return saved !== null ? saved === 'true' : true
  })
  const [sidebarSearchOpen, setSidebarSearchOpen] = useState(false)
  const [searchModalInitialTab, setSearchModalInitialTab] = useState<
    'search' | 'chat' | 'brainstorm'
  >('search')
  const [searchModalProjectId, setSearchModalProjectId] = useState<string | null>(null)
  const [isProfileDrawerOpen, setIsProfileDrawerOpen] = useState(false)
  const [sseConnected, setSseConnected] = useState(false)

  useEffect(() => {
    if (!isAuthenticated) {
      setIsProfileDrawerOpen(false)
      setSidebarSearchOpen(false)
      setActiveDetailId(null)
      setActiveDetailTab(null)
      setViewFilterParams(null)
      return
    }
    const parsed = parsePath(window.location.pathname)
    setActiveViewState(parsed.view || VIEWS.DASHBOARD)
    setActiveDetailId(parsed.detailId)
    setActiveDetailTab(parsed.subTab)
    setViewFilterParams(null)
  }, [isAuthenticated, user?.id])

  useEffect(() => {
    if (!isAuthenticated || typeof EventSource === 'undefined') {
      setSseConnected(false)
      return
    }

    const es = new EventSource('/api/v1/events/stream', { withCredentials: true })

    let flushTimer: number | null = null
    let invalidateActivity = false
    let invalidateSeeds = false
    let invalidateRuns = false
    let invalidateProjects = false
    const seedDetailIds = new Set<string>()
    const seedActivityIds = new Set<string>()
    const seedCommentIds = new Set<string>()
    const sandboxProjectIds = new Set<string>()

    const flushInvalidations = () => {
      flushTimer = null

      if (invalidateActivity) {
        invalidateActivity = false
        void queryClient.invalidateQueries({ queryKey: queryKeys.activity })
      }
      if (invalidateSeeds) {
        invalidateSeeds = false
        void queryClient.invalidateQueries({ queryKey: queryKeys.seeds })
      }
      if (invalidateRuns) {
        invalidateRuns = false
        void queryClient.invalidateQueries({ queryKey: queryKeys.runs })
      }
      if (invalidateProjects) {
        invalidateProjects = false
        void queryClient.invalidateQueries({ queryKey: queryKeys.projects })
      }

      if (seedDetailIds.size > 0) {
        const ids = Array.from(seedDetailIds)
        seedDetailIds.clear()
        for (const sId of ids) {
          seedActivityIds.delete(sId)
          seedCommentIds.delete(sId)
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedDetail(sId) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedRuns(sId) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedTasks(sId) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedApprovals(sId) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedComments(sId) })
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedChanges(sId) })
          void queryClient.invalidateQueries({ queryKey: ['seeds', sId, 'changes'] })
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedActivity(sId) })
        }
      }

      if (seedActivityIds.size > 0) {
        const ids = Array.from(seedActivityIds)
        seedActivityIds.clear()
        for (const sId of ids) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedActivity(sId) })
        }
      }

      if (seedCommentIds.size > 0) {
        const ids = Array.from(seedCommentIds)
        seedCommentIds.clear()
        for (const sId of ids) {
          void queryClient.invalidateQueries({ queryKey: queryKeys.seedComments(sId) })
        }
      }

      if (sandboxProjectIds.size > 0) {
        const pIds = Array.from(sandboxProjectIds)
        sandboxProjectIds.clear()
        for (const pId of pIds) {
          void queryClient.invalidateQueries({
            queryKey: queryKeys.projectSandboxes(pId),
          })
        }
      }
    }

    const scheduleFlush = () => {
      if (flushTimer === null) {
        flushTimer = window.setTimeout(flushInvalidations, SSE_INVALIDATE_THROTTLE_MS)
      }
    }

    const onConnected = () => setSseConnected(true)
    const onError = () => setSseConnected(false)

    const onGitseedEvent = (ev: MessageEvent) => {
      try {
        const payload = JSON.parse(ev.data) as {
          type?: string
          project_id?: string | null
          seed_id?: string | null
          data?: Record<string, unknown>
        }
        const evType = String(payload.type || '')
        const dataSeedId =
          typeof payload.data?.seed_id === 'string' && payload.data.seed_id
            ? payload.data.seed_id
            : null
        const seedId = payload.seed_id || dataSeedId || null
        const projectId =
          payload.project_id ||
          (typeof payload.data?.project_id === 'string' && payload.data.project_id
            ? payload.data.project_id
            : null)

        if (evType === 'run.log') {
          const runId = String(payload.data?.run_id || '')
          const logTail = String(payload.data?.log_tail || '')
          if (runId && logTail) {
            const updateRunList = (prev: Run[] | undefined) => {
              if (!prev) return prev
              return prev.map((r) =>
                r.id === runId
                  ? { ...r, output: { ...(r.output || {}), log_tail: logTail } }
                  : r
              )
            }
            queryClient.setQueryData<Run[]>(queryKeys.runs, updateRunList)
            if (seedId) {
              queryClient.setQueryData<Run[]>(queryKeys.seedRuns(seedId), updateRunList)
            }
          }
          return
        }

        invalidateActivity = true
        if (seedId) {
          seedActivityIds.add(seedId)
        }

        const isSeedLifecycleEvent =
          evType.startsWith('seed.') ||
          evType.startsWith('stage.') ||
          evType.startsWith('status.') ||
          evType.startsWith('approval.') ||
          evType.startsWith('run.')

        if (isSeedLifecycleEvent) {
          invalidateSeeds = true
          if (evType.startsWith('run.')) {
            invalidateRuns = true
          }
          if (seedId) {
            seedDetailIds.add(seedId)
          }
          if (activeViewRef.current === VIEWS.SEEDS && activeDetailIdRef.current) {
            seedDetailIds.add(activeDetailIdRef.current)
          }
        }

        if (evType.startsWith('comment.')) {
          if (seedId) {
            seedCommentIds.add(seedId)
          }
          if (activeViewRef.current === VIEWS.SEEDS && activeDetailIdRef.current) {
            seedCommentIds.add(activeDetailIdRef.current)
          }
        }

        if (evType.startsWith('sandbox.') && projectId) {
          sandboxProjectIds.add(projectId)
        }

        if (evType.startsWith('project.')) {
          invalidateProjects = true
        }

        scheduleFlush()
      } catch {
        // Ignore malformed SSE frames
      }
    }

    es.addEventListener('connected', onConnected)
    es.addEventListener('gitseed_event', onGitseedEvent as EventListener)
    es.onerror = onError

    return () => {
      if (flushTimer !== null) {
        window.clearTimeout(flushTimer)
      }
      es.removeEventListener('connected', onConnected)
      es.removeEventListener('gitseed_event', onGitseedEvent as EventListener)
      es.close()
      setSseConnected(false)
    }
  }, [isAuthenticated, queryClient])

  const { data: projects = EMPTY_PROJECTS, isPending: isProjectsPending } = useQuery({
    queryKey: queryKeys.projects,
    queryFn: () => api.projects.list(true, 100, 0),
    enabled: isAuthenticated,
  })

  const { data: allSeeds = EMPTY_SEEDS, isPending: isSeedsPending } = useQuery({
    queryKey: queryKeys.seeds,
    queryFn: () => api.seeds.list(null, SEEDS_LIST_LIMIT, 0),
    enabled: isAuthenticated,
    refetchInterval: (query) => {
      const list = query.state.data || []
      const hasActive = list.some((s) => s.status === 'queued' || s.status === 'running')
      if (hasActive) return 3000
      return sseConnected ? 30000 : 12000
    },
  })

  const { data: runs = EMPTY_RUNS } = useQuery({
    queryKey: queryKeys.runs,
    queryFn: () => api.runs.list(500, 0),
    enabled: isAuthenticated,
    refetchInterval: (query) => {
      const list = query.state.data || []
      const hasActive = list.some(
        (r) => r.status === 'queued' || r.status === 'starting' || r.status === 'running'
      )
      if (hasActive) return 3000
      return sseConnected ? 30000 : 12000
    },
  })

  const { data: rawEvents = EMPTY_EVENTS } = useQuery({
    queryKey: queryKeys.activity,
    queryFn: () => api.activity.list(100),
    enabled: isAuthenticated,
    refetchInterval: sseConnected ? 30000 : 8000,
  })

  const allEvents = useMemo(
    () =>
      isAdmin
        ? rawEvents
        : rawEvents.filter(
          (e) =>
            e.project_id !== null &&
            !e.type.startsWith('user.') &&
            !e.type.startsWith('backend.') &&
            !e.type.startsWith('settings.') &&
            !e.type.startsWith('pat.')
        ),
    [rawEvents, isAdmin]
  )

  const { data: backends = EMPTY_BACKENDS, isPending: isBackendsPending } = useQuery({
    queryKey: queryKeys.backends,
    queryFn: () => api.backends.list(),
    enabled: isAuthenticated,
  })

  const { data: agents = EMPTY_AGENTS, isPending: isAgentsPending } = useQuery({
    queryKey: queryKeys.agents,
    queryFn: () => api.agents.list(true),
    enabled: isAuthenticated,
  })

  const { data: skills = EMPTY_SKILLS } = useQuery({
    queryKey: queryKeys.skills,
    queryFn: () => api.skills.list(),
    enabled: isAuthenticated,
  })

  const { data: policies = EMPTY_POLICIES, isPending: isPoliciesPending } = useQuery({
    queryKey: queryKeys.policies,
    queryFn: () => api.policies.list(),
    enabled: isAuthenticated,
  })

  const { data: users = EMPTY_USERS } = useQuery({
    queryKey: queryKeys.users,
    queryFn: () => api.users.list(100, 0),
    enabled: isAuthenticated,
  })

  const { data: orgSettings = null } = useQuery({
    queryKey: queryKeys.settings,
    queryFn: () => api.settings.get(),
    enabled: isAuthenticated,
  })

  useEffect(() => {
    const dt = orgSettings?.default_theme
    if (dt === 'dark' || dt === 'light' || dt === 'system') {
      setOrgDefaultTheme(dt)
    }
    if (typeof orgSettings?.default_ambient_bg === 'boolean') {
      setOrgDefaultAmbientBg(orgSettings.default_ambient_bg)
    }
    if (typeof orgSettings?.default_show_guide === 'boolean') {
      const saved = localStorage.getItem(GUIDE_STORAGE_KEY)
      if (saved === null) {
        setShowGuideInSidebarState(orgSettings.default_show_guide)
      }
    }
  }, [
    orgSettings?.default_theme,
    orgSettings?.default_ambient_bg,
    orgSettings?.default_show_guide,
    setOrgDefaultTheme,
    setOrgDefaultAmbientBg,
  ])

  const needsProjectMetadata =
    PROJECT_METADATA_VIEWS.has(activeView) || isProfileDrawerOpen
  const activeSeedProjectId = useMemo(() => {
    if (activeView !== VIEWS.SEEDS || !activeDetailId) return null
    const direct = allSeeds.find((s) => s.id === activeDetailId)
    if (direct) return direct.project_id
    if (activeDetailId.includes('#')) {
      const [slug] = activeDetailId.split('#', 2)
      const proj = projects.find((p) => p.slug === slug)
      if (proj) return proj.id
    }
    return null
  }, [activeView, activeDetailId, allSeeds, projects])

  const shouldEnableProjectMetadata = useCallback(
    (project: Project): boolean => {
      if (!isAuthenticated || !needsProjectMetadata) return false
      if (
        project.archived_at &&
        project.id !== selectedProject &&
        project.id !== activeDetailId &&
        project.slug !== activeDetailId
      ) {
        return false
      }
      if (
        activeView === VIEWS.PROJECTS ||
        activeView === VIEWS.USERS ||
        isProfileDrawerOpen
      ) {
        return true
      }
      if (activeSeedProjectId) {
        return (
          project.id === activeSeedProjectId || project.id === selectedProject
        )
      }
      if (selectedProject) {
        return project.id === selectedProject
      }
      return true
    },
    [
      isAuthenticated,
      needsProjectMetadata,
      selectedProject,
      activeDetailId,
      activeView,
      isProfileDrawerOpen,
      activeSeedProjectId,
    ]
  )

  const memberQueries = useQueries({
    queries: projects.map((project) => ({
      queryKey: queryKeys.projectMembers(project.id),
      queryFn: () => api.projects.listMembers(project.id),
      enabled: shouldEnableProjectMetadata(project),
      staleTime: 300_000,
      refetchOnWindowFocus: false,
    })),
  })

  const projectPolicyQueries = useQueries({
    queries: projects.map((project) => ({
      queryKey: queryKeys.projectPolicies(project.id),
      queryFn: () => api.projects.listPolicies(project.id),
      enabled: shouldEnableProjectMetadata(project),
      staleTime: 300_000,
      refetchOnWindowFocus: false,
    })),
  })

  const projectRepoQueries = useQueries({
    queries: projects.map((project) => ({
      queryKey: queryKeys.projectRepos(project.id),
      queryFn: () => api.projects.listRepos(project.id),
      enabled: shouldEnableProjectMetadata(project),
      staleTime: 300_000,
      refetchOnWindowFocus: false,
    })),
  })

  const projectMembersByProject = useMemo(() => {
    const map: Record<string, ProjectMember[]> = {}
    for (let i = 0; i < projects.length; i++) {
      const proj = projects[i]
      const data = memberQueries[i]?.data
      if (proj && data) {
        map[proj.id] = data
      }
    }
    return map
  }, [projects, memberQueries])

  const projectPoliciesByProject = useMemo(() => {
    const map: Record<string, StagePolicy[]> = {}
    for (let i = 0; i < projects.length; i++) {
      const proj = projects[i]
      const data = projectPolicyQueries[i]?.data
      if (proj && data) {
        map[proj.id] = data
      }
    }
    return map
  }, [projects, projectPolicyQueries])

  const projectReposByProject = useMemo(() => {
    const map: Record<string, ProjectRepository[]> = {}
    for (let i = 0; i < projects.length; i++) {
      const proj = projects[i]
      const data = projectRepoQueries[i]?.data
      if (proj && data) {
        map[proj.id] = data
      }
    }
    return map
  }, [projects, projectRepoQueries])

  const isInitialLoading =
    isAuthenticated &&
    (isProjectsPending ||
      isSeedsPending ||
      isBackendsPending ||
      isAgentsPending ||
      isPoliciesPending)

  const refreshData = useCallback(async () => {
    if (!isAuthenticated) return
    await queryClient.invalidateQueries()
  }, [isAuthenticated, queryClient])

  useEffect(() => {
    const onPopState = () => {
      const parsed = parsePath(window.location.pathname)
      if (parsed.view) {
        setActiveViewState(parsed.view)
        setActiveDetailId(parsed.detailId)
        setActiveDetailTab(parsed.subTab)
      }
    }
    window.addEventListener('popstate', onPopState)
    return () => window.removeEventListener('popstate', onPopState)
  }, [])

  const setActiveView = useCallback(
    (view: ViewType, filterParams?: Record<string, string>) => {
      setIsProfileDrawerOpen(false)
      setActiveViewState(view)
      setActiveDetailId(null)
      setActiveDetailTab(null)
      setViewFilterParams(filterParams || null)
      const targetPath = buildPath(view)
      if (window.location.pathname !== targetPath) {
        window.history.pushState(null, '', targetPath)
      }
    },
    []
  )

  const navigateToDetail = useCallback(
    (
      view: ViewType,
      id: string,
      subTab?: string,
      queryParams?: Record<string, string>
    ) => {
      setIsProfileDrawerOpen(false)
      setActiveViewState(view)
      setActiveDetailId(id)
      setActiveDetailTab(subTab || null)
      const targetPath = buildPath(view, id, subTab)
      const search = queryParams
        ? new URLSearchParams(queryParams).toString()
        : ''
      const fullUrl = search ? `${targetPath}?${search}` : targetPath
      if (`${window.location.pathname}${window.location.search}` !== fullUrl) {
        window.history.pushState(null, '', fullUrl)
      }
    },
    []
  )

  const clearDetail = useCallback(() => {
    setActiveDetailId(null)
    setActiveDetailTab(null)
    const targetPath = buildPath(activeView)
    if (window.location.pathname !== targetPath) {
      window.history.pushState(null, '', targetPath)
    }
  }, [activeView])

  const setSelectedProject = useCallback((projectId: string | null) => {
    setSelectedProjectState(projectId)
    if (projectId) {
      localStorage.setItem('gitseed_selected_project', projectId)
    } else {
      localStorage.removeItem('gitseed_selected_project')
    }
  }, [])

  const setShowGuideInSidebar = useCallback((show: boolean) => {
    setShowGuideInSidebarState(show)
    localStorage.setItem(GUIDE_STORAGE_KEY, String(show))
  }, [])

  const selectedProjectObj = useMemo(
    () =>
      selectedProject
        ? projects.find((p) => p.id === selectedProject && !p.archived_at) || null
        : null,
    [projects, selectedProject]
  )

  useEffect(() => {
    if (
      isAuthenticated &&
      !isProjectsPending &&
      selectedProject &&
      !selectedProjectObj
    ) {
      setSelectedProjectState(null)
      localStorage.removeItem('gitseed_selected_project')
    }
  }, [isAuthenticated, isProjectsPending, selectedProject, selectedProjectObj])

  const effectiveSelectedProjectId = selectedProjectObj?.id ?? null

  const seeds = useMemo(
    () =>
      effectiveSelectedProjectId
        ? allSeeds.filter((s) => s.project_id === effectiveSelectedProjectId)
        : allSeeds,
    [allSeeds, effectiveSelectedProjectId]
  )

  const events = useMemo(
    () =>
      effectiveSelectedProjectId
        ? allEvents.filter((e) => e.project_id === effectiveSelectedProjectId)
        : allEvents,
    [allEvents, effectiveSelectedProjectId]
  )

  return (
    <DataContext.Provider
      value={{
        activeView,
        activeDetailId,
        activeDetailTab,
        viewFilterParams,
        setActiveView,
        navigateToDetail,
        clearDetail,
        selectedProject,
        setSelectedProject,
        selectedProjectObj,
        projects,
        projectMembersByProject,
        projectPoliciesByProject,
        projectReposByProject,
        seeds,
        allSeeds,
        runs,
        events,
        allEvents,
        backends,
        agents,
        skills,
        policies,
        users,
        orgSettings,
        showGuideInSidebar,
        setShowGuideInSidebar,
        sidebarSearchOpen,
        searchModalInitialTab,
        searchModalProjectId,
        openSearchModal: (initialTab = 'search', projectId = null) => {
          const resolvedTab =
            typeof initialTab === 'string' ? initialTab : 'search'
          setSearchModalInitialTab(resolvedTab)
          setSearchModalProjectId(projectId ?? null)
          setSidebarSearchOpen(true)
        },
        closeSearchModal: () => setSidebarSearchOpen(false),
        isProfileDrawerOpen,
        openProfileDrawer: () => setIsProfileDrawerOpen(true),
        closeProfileDrawer: () => setIsProfileDrawerOpen(false),
        isInitialLoading,
        refreshData,
      }}
    >
      {children}
    </DataContext.Provider>
  )
}

export const useData = () => {
  const context = useContext(DataContext)
  if (!context) throw new Error('useData must be used within DataProvider')
  return context
}
