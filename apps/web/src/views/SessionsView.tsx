import React from 'react'
import { useQuery } from '@tanstack/react-query'
import { Loader2 } from 'lucide-react'
import { api } from '../api/client'
import { VIEWS } from '../constants'
import { useData } from '../context/DataContext'
import { PageBreadcrumb } from '../components/common/PageBreadcrumb'
import { SessionsList } from '../components/sessions/SessionsList'
import { SessionDetail } from '../components/sessions/SessionDetail'
import { SessionStartControl } from '../components/sessions/SessionStartControl'

/** Top-level Sessions: everything running (or recently run) across projects, plus one-click start. */
export const SessionsView: React.FC = () => {
  const { activeDetailId, activeDetailTab, selectedProject, selectedProjectObj, projects, setActiveView, navigateToDetail } = useData()
  const kind = activeDetailId === 'chat' || activeDetailId === 'terminal' ? activeDetailId : null
  const sessionId = kind ? activeDetailTab : null

  const { data: item, isLoading, error } = useQuery({
    queryKey: ['sessions', 'item', kind, sessionId],
    queryFn: () => api.sessions.get(kind as 'chat' | 'terminal', sessionId as string),
    enabled: Boolean(kind && sessionId),
  })

  if (kind && sessionId) {
    const project = item ? projects.find((p) => p.id === item.project_id) || null : null
    return (
      <div className="flex flex-col gap-3 max-w-7xl mx-auto pb-4">
        <PageBreadcrumb
          items={[
            { label: 'Sessions', onClick: () => setActiveView(VIEWS.SESSIONS) },
            ...(item
              ? [
                  {
                    label: item.project_name,
                    onClick: () => navigateToDetail(VIEWS.PROJECTS, item.project_slug || item.project_id, 'run'),
                  },
                ]
              : []),
          ]}
          currentLabel={item?.name || 'Session'}
        />
        {isLoading ? (
          <div className="py-16 flex items-center justify-center gap-2 text-sm text-zinc-500">
            <Loader2 size={16} className="animate-spin" />
            Loading session…
          </div>
        ) : error || !item || !project ? (
          <div className="py-16 text-center text-sm text-zinc-500">This session no longer exists or you don't have access.</div>
        ) : (
          <SessionDetail
            key={`${kind}-${sessionId}`}
            project={project}
            kind={kind}
            sessionId={sessionId}
            onDeleted={() => setActiveView(VIEWS.SESSIONS)}
          />
        )}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-6 max-w-7xl mx-auto pb-16">
      {/* Page Header */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div>
          <PageBreadcrumb currentLabel="Sessions" className="mb-1.5" />
          <h2 className="text-2xl font-extrabold tracking-tight font-heading text-zinc-900 dark:text-zinc-100">Sessions</h2>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 mt-1">
            Terminals and agent chats on your projects' code
            {selectedProjectObj ? ` · ${selectedProjectObj.name}` : ''}.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <SessionStartControl projectId={selectedProject} tone="primary" />
        </div>
      </div>
      <SessionsList projectId={selectedProject} />
    </div>
  )
}

