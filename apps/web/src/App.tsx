import React, { Suspense } from 'react'
import { QueryClientProvider } from '@tanstack/react-query'
import { queryClient } from './api/queryClient'
import { ThemeProvider, useTheme } from './context/ThemeContext'
import { AuthProvider, useAuth } from './context/AuthContext'
import { StatusProvider, useControlPlaneStatus } from './context/StatusContext'
import { DataProvider, useData } from './context/DataContext'
import { I18nProvider, useTranslation } from './context/I18nContext'
import { Sidebar } from './components/layout/Sidebar'
import { DisconnectedBanner } from './components/common/DisconnectedBanner'
import { ErrorBoundary } from './components/ErrorBoundary'
import { SessionDockProvider, useSessionDock } from './context/SessionDockContext'
import { useDocumentTitle } from './hooks/useDocumentTitle'
import { VIEWS, parsePath, type ViewType } from './constants'

const LoginView = React.lazy(() =>
  import('./views/LoginView').then((m) => ({ default: m.LoginView }))
)
const AcceptInviteView = React.lazy(() =>
  import('./views/AcceptInviteView').then((m) => ({
    default: m.AcceptInviteView,
  }))
)
const OverviewView = React.lazy(() =>
  import('./views/OverviewView').then((m) => ({ default: m.OverviewView }))
)
const GuideView = React.lazy(() =>
  import('./views/GuideView').then((m) => ({ default: m.GuideView }))
)
const UsersView = React.lazy(() =>
  import('./views/UsersView').then((m) => ({ default: m.UsersView }))
)
const SettingsView = React.lazy(() =>
  import('./views/SettingsView').then((m) => ({ default: m.SettingsView }))
)
const ActivityView = React.lazy(() =>
  import('./views/ActivityView').then((m) => ({ default: m.ActivityView }))
)
const BackendsView = React.lazy(() =>
  import('./views/BackendsView').then((m) => ({ default: m.BackendsView }))
)
const AgentsView = React.lazy(() =>
  import('./views/AgentsView').then((m) => ({ default: m.AgentsView }))
)
const PoliciesView = React.lazy(() =>
  import('./views/PoliciesView').then((m) => ({ default: m.PoliciesView }))
)
const ProjectsView = React.lazy(() =>
  import('./views/ProjectsView').then((m) => ({ default: m.ProjectsView }))
)
const SeedsView = React.lazy(() =>
  import('./views/SeedsView').then((m) => ({ default: m.SeedsView }))
)
const SessionsView = React.lazy(() =>
  import('./views/SessionsView').then((m) => ({ default: m.SessionsView }))
)
const SessionDock = React.lazy(() =>
  import('./components/sessions/SessionDock').then((m) => ({
    default: m.SessionDock,
  }))
)
const CommandPaletteModal = React.lazy(() =>
  import('./components/modals/CommandPaletteModal').then((m) => ({
    default: m.CommandPaletteModal,
  }))
)
const ControlPlaneStatusModal = React.lazy(() =>
  import('./components/modals/ControlPlaneStatusModal').then((m) => ({
    default: m.ControlPlaneStatusModal,
  }))
)
const UserDetail = React.lazy(() =>
  import('./components/details/UserDetail').then((m) => ({
    default: m.UserDetail,
  }))
)

const ADMIN_ONLY_VIEWS: ReadonlySet<ViewType> = new Set<ViewType>([
  VIEWS.BACKENDS,
  VIEWS.AGENTS,
  VIEWS.ROUTES,
  VIEWS.USERS,
  VIEWS.SETTINGS,
])

const AppContent: React.FC = () => {
  const { user, isAuthenticated, isLoading } = useAuth()
  const {
    activeView,
    setActiveView,
    activeDetailId,
    isProfileDrawerOpen,
    closeProfileDrawer,
    sidebarSearchOpen,
    openSearchModal,
  } = useData()
  const { items: dockItems } = useSessionDock()
  const { isStatusModalOpen, closeStatusModal } = useControlPlaneStatus()
  const { ambientBg } = useTheme()
  const { t } = useTranslation()
  const isAdmin = user?.org_role === 'admin'
  const [paletteMounted, setPaletteMounted] = React.useState(false)
  const [dockMounted, setDockMounted] = React.useState(false)

  React.useEffect(() => {
    if (sidebarSearchOpen) {
      setPaletteMounted(true)
    }
  }, [sidebarSearchOpen])

  React.useEffect(() => {
    if (dockItems.length > 0) {
      setDockMounted(true)
    }
  }, [dockItems.length])

  React.useEffect(() => {
    if (!isAuthenticated || paletteMounted) return
    const onKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        openSearchModal()
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [isAuthenticated, paletteMounted, openSearchModal])

  useDocumentTitle()

  const mainRef = React.useRef<HTMLElement>(null)

  React.useEffect(() => {
    if (mainRef.current) {
      mainRef.current.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior })
    }
    window.scrollTo({ top: 0, left: 0, behavior: 'instant' as ScrollBehavior })
  }, [activeView, activeDetailId])

  React.useEffect(() => {
    if (isLoading) return

    const enforceAuthRoute = () => {
      const currentPath = window.location.pathname

      if (currentPath === '/accept-invite') {
        return
      }

      if (!isAuthenticated) {
        if (currentPath !== '/login') {
          window.history.replaceState(null, '', '/login')
        }
      } else {
        if (currentPath === '/login' || currentPath === '/') {
          window.history.replaceState(null, '', '/overview')
          setActiveView(VIEWS.DASHBOARD)
        } else {
          const parsed = parsePath(currentPath)
          if (!parsed.view || (!isAdmin && ADMIN_ONLY_VIEWS.has(parsed.view))) {
            window.history.replaceState(null, '', '/overview')
            setActiveView(VIEWS.DASHBOARD)
          }
        }
      }
    }

    enforceAuthRoute()
    window.addEventListener('popstate', enforceAuthRoute)
    return () => window.removeEventListener('popstate', enforceAuthRoute)
  }, [isAdmin, isAuthenticated, isLoading, setActiveView])

  const fullScreenFallback = (
    <div className="h-screen w-screen flex items-center justify-center bg-zinc-950 text-zinc-200 text-base font-semibold gap-3">
      <span className="w-5 h-5 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
      <span>{t('common.initializing')}</span>
    </div>
  )

  if (window.location.pathname === '/accept-invite') {
    return (
      <ErrorBoundary key="accept-invite">
        <Suspense fallback={fullScreenFallback}>
          <AcceptInviteView />
        </Suspense>
      </ErrorBoundary>
    )
  }

  if (isLoading) {
    return fullScreenFallback
  }

  if (!isAuthenticated) {
    return (
      <ErrorBoundary key="login">
        <Suspense fallback={fullScreenFallback}>
          <LoginView />
        </Suspense>
      </ErrorBoundary>
    )
  }

  const renderActiveView = () => {
    switch (activeView) {
      case VIEWS.DASHBOARD:
        return <OverviewView />
      case VIEWS.SEEDS:
        return <SeedsView />
      case VIEWS.PROJECTS:
        return <ProjectsView />
      case VIEWS.SETTINGS:
        return <SettingsView />
      case VIEWS.USERS:
        return <UsersView />
      case VIEWS.AGENTS:
        return <AgentsView />
      case VIEWS.BACKENDS:
        return <BackendsView />
      case VIEWS.ROUTES:
        return <PoliciesView />
      case VIEWS.TELEMETRY:
        return <ActivityView />
      case VIEWS.SESSIONS:
        return <SessionsView />
      case VIEWS.GUIDE:
        return <GuideView />
      default:
        return <OverviewView />
    }
  }

  return (
    <div className="flex h-screen w-screen overflow-hidden text-zinc-900 dark:text-zinc-100 font-sans relative bg-[#f4f5f7] dark:bg-[#070b12] transition-colors duration-300">
      {ambientBg && (
        <div
          aria-hidden="true"
          className="pointer-events-none fixed inset-0 z-0 overflow-hidden"
        >
          {/* Light Mode Linear & Radial Atmosphere */}
          <div className="absolute inset-0 bg-gradient-to-b from-[#f8fafc] via-[#f1f5f9] to-[#e8edf5] opacity-100 dark:opacity-0 transition-opacity duration-300">
            <div
              className="absolute -top-36 left-1/2 -translate-x-1/2 w-[1250px] h-[720px]"
              style={{
                background:
                  'radial-gradient(ellipse 65% 50% at 50% 30%, rgba(20, 184, 166, 0.08) 0%, rgba(13, 148, 136, 0.03) 45%, transparent 75%)',
              }}
            />
          </div>

          {/* Dark Mode Linear & Radial Atmosphere + Ambient Orbs */}
          <div className="absolute inset-0 bg-gradient-to-b from-[#091526] via-[#060c16] to-[#03060b] opacity-0 dark:opacity-100 transition-opacity duration-300">
            <div
              className="absolute -top-36 left-1/2 -translate-x-1/2 w-[1250px] h-[720px]"
              style={{
                background:
                  'radial-gradient(ellipse 65% 55% at 50% 32%, rgba(20, 184, 166, 0.14) 0%, rgba(13, 148, 136, 0.07) 42%, rgba(6, 12, 22, 0) 75%)',
              }}
            />
            <div className="absolute top-6 right-16 w-[450px] h-[450px] bg-teal-500/8 blur-[130px] rounded-full" />
            <div className="absolute top-48 left-1/4 w-[350px] h-[350px] bg-cyan-500/5 blur-[120px] rounded-full" />
          </div>
        </div>
      )}

      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0 h-screen relative overflow-hidden">
        <DisconnectedBanner />
        <main ref={mainRef} className="flex-1 overflow-y-auto p-6 md:p-8 min-w-0">
          <div className="max-w-7xl mx-auto">
            <ErrorBoundary key={activeView + (activeDetailId || '')}>
              <Suspense
                fallback={
                  <div className="py-16 flex items-center justify-center gap-2.5 text-sm font-medium text-zinc-500 dark:text-zinc-400">
                    <span className="w-4 h-4 border-2 border-teal-500 border-t-transparent rounded-full animate-spin" />
                    <span>Loading view…</span>
                  </div>
                }
              >
                {renderActiveView()}
              </Suspense>
            </ErrorBoundary>
          </div>
        </main>
        {dockMounted && (
          <Suspense fallback={null}>
            <SessionDock />
          </Suspense>
        )}
      </div>
      {isStatusModalOpen && (
        <Suspense fallback={null}>
          <ControlPlaneStatusModal isOpen={isStatusModalOpen} onClose={closeStatusModal} />
        </Suspense>
      )}
      {paletteMounted && (
        <Suspense fallback={null}>
          <CommandPaletteModal />
        </Suspense>
      )}
      {isProfileDrawerOpen && user && (
        <Suspense fallback={null}>
          <UserDetail user={user} onBack={closeProfileDrawer} />
        </Suspense>
      )}
    </div>
  )
}

export const App: React.FC = () => {
  return (
    <QueryClientProvider client={queryClient}>
      <I18nProvider>
        <AuthProvider>
          <ThemeProvider>
            <StatusProvider>
              <DataProvider>
                <SessionDockProvider>
                  <AppContent />
                </SessionDockProvider>
              </DataProvider>
            </StatusProvider>
          </ThemeProvider>
        </AuthProvider>
      </I18nProvider>
    </QueryClientProvider>
  )
}

export default App

