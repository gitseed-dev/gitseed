import React, { useMemo, useState } from 'react'
import { AgentsWorkingCard } from '../components/common/AgentsWorkingCard'
import { GettingStartedCard } from '../components/common/GettingStartedCard'
import { OperatorReviewPanel } from '../components/common/OperatorReviewPanel'
import { PageBreadcrumb } from '../components/common/PageBreadcrumb'
import { RecentActivityCard } from '../components/common/RecentActivityCard'
import { SdlcPipelineStrip } from '../components/common/SdlcPipelineStrip'
import { SpendTrendCard } from '../components/common/SpendTrendCard'
import { SeedModal } from '../components/modals/SeedModal'
import { SessionStartControl } from '../components/sessions/SessionStartControl'
import { useAuth } from '../context/AuthContext'
import { useData } from '../context/DataContext'
import { useTranslation } from '../context/I18nContext'
import { VIEWS } from '../constants'
import { seedNeedsAttention } from '../utils/format'

export const OverviewView: React.FC = () => {
  const { user } = useAuth()
  const {
    seeds,
    allSeeds,
    runs,
    events,
    backends,
    projects,
    isInitialLoading,
    selectedProject,
    selectedProjectObj,
    navigateToDetail,
  } = useData()
  const { t } = useTranslation()
  const [isSeedModalOpen, setIsSeedModalOpen] = useState(false)

  const backendsReady = useMemo(
    () =>
      isInitialLoading ||
      (backends.some((b) => b.type === 'compute' && b.enabled) &&
        backends.some((b) => b.type === 'inference' && b.enabled)),
    [backends, isInitialLoading]
  )
  const projectsReady = useMemo(
    () => isInitialLoading || projects.some((p) => !p.archived_at),
    [projects, isInitialLoading]
  )
  const showOperationalSections = useMemo(
    () =>
      backendsReady &&
      projectsReady &&
      (allSeeds.length > 0 || runs.length > 0 || events.length > 0),
    [backendsReady, projectsReady, allSeeds.length, runs.length, events.length]
  )

  const firstName = useMemo(() => {
    if (user?.preferred_name) return user.preferred_name
    if (user?.name) return user.name.split(' ')[0]
    if (user?.email) return user.email.split('@')[0]
    return 'Admin'
  }, [user])

  const currentHour = new Date().getHours()
  const greetingKey =
    currentHour < 12
      ? 'dashboard.greetingMorning'
      : currentHour < 17
        ? 'dashboard.greetingAfternoon'
        : 'dashboard.greetingEvening'
  const greetingTitle = t(greetingKey, { name: firstName })

  const needsYouCount = useMemo(
    () => seeds.filter(seedNeedsAttention).length,
    [seeds]
  )

  const workingCount = useMemo(
    () => seeds.filter((s) => s.status === 'running').length,
    [seeds]
  )

  const reviewPart =
    needsYouCount === 0
      ? t('dashboard.statusLineAllNominal')
      : needsYouCount === 1
        ? t('dashboard.statusLineReviewItemOne')
        : t('dashboard.statusLineReviewItems', { count: needsYouCount })

  const agentsPart =
    workingCount === 0
      ? t('dashboard.statusLineAgentsWorkingNone')
      : workingCount === 1
        ? t('dashboard.statusLineAgentWorkingOne')
        : t('dashboard.statusLineAgentsWorking', { count: workingCount })

  const statusLine = !backendsReady
    ? t('dashboard.statusLineAwaitingBackends')
    : !projectsReady
      ? t('dashboard.statusLineAwaitingProject')
      : t('dashboard.statusLine', { reviewPart, agentsPart })

  const statusDotClass = !backendsReady
    ? 'bg-rose-500 animate-pulse'
    : !projectsReady || needsYouCount > 0
      ? 'bg-amber-500'
      : 'bg-emerald-500'

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto pb-16">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <PageBreadcrumb
            currentLabel={t('nav.dashboard')}
            className="mb-1"
          />

          <h2 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100">
            {selectedProjectObj ? selectedProjectObj.name : greetingTitle}
          </h2>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1 font-medium inline-flex items-center gap-2">
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${statusDotClass}`} />
            <span>{statusLine}</span>
          </p>
        </div>

        {backendsReady && projectsReady && (
          <div className="flex items-center gap-2.5 shrink-0">
            <SessionStartControl
              tone="primary"
              size="md"
              onNewSeed={() => setIsSeedModalOpen(true)}
              hideWhenUnavailable
            />
          </div>
        )}
      </div>

      <GettingStartedCard
        variant="overview"
        onNewSeed={() => setIsSeedModalOpen(true)}
      />

      {showOperationalSections && (
        <>
          <SdlcPipelineStrip seeds={seeds} className="sticky top-0 z-20" />
          <OperatorReviewPanel seeds={seeds} showNominalState />
          {/* What's happening now: full width, a single line when idle */}
          <AgentsWorkingCard seeds={seeds} showMySessions compactWhenIdle />
          {/* History: usage and activity side by side, starting at the same height */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 items-start">
            <SpendTrendCard
              seeds={seeds}
              runs={runs}
              monthlyBudgetUsd={selectedProjectObj?.monthly_budget}
              showSeedBreakdown
            />
            <RecentActivityCard events={events} />
          </div>
        </>
      )}

      <SeedModal
        isOpen={isSeedModalOpen}
        onClose={() => setIsSeedModalOpen(false)}
        defaultProjectId={selectedProject || null}
        onSaved={(created) => navigateToDetail(VIEWS.SEEDS, created.id)}
      />
    </div>
  )
}
