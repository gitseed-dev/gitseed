import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { Bot, GitBranch, Loader2, Terminal } from 'lucide-react'
import { api } from '../../api/client'
import { VIEWS } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { formatRelativeTime } from '../../utils/format'
import { AgentProviderIcon } from '../common/ProviderIcon'
import { EntityTableCard, EntityTableHeaderRow } from '../layout/EntityPageLayout'
import type { SessionListItem } from '../../types'

function minutesLeft(iso?: string | null): number | null {
  if (!iso) return null
  const ms = new Date(iso).getTime() - Date.now()
  return Number.isFinite(ms) ? Math.max(0, Math.round(ms / 60000)) : null
}

function formatLeft(mins: number | null): string {
  if (mins === null) return '-'
  if (mins >= 60) {
    const rem = mins % 60
    if (rem === 0 || (mins >= 60 && (rem < 5 || rem > 55))) {
      return `${Math.round(mins / 60)}h`
    }
    return `${Math.floor(mins / 60)}h ${rem}m`
  }
  return `${mins}m`
}

function statusLabel(item: SessionListItem): { label: string; className: string } {
  if (item.is_running) {
    const working = item.status === 'running_turn' || item.status === 'starting'
    return {
      label: working ? (item.status === 'starting' ? 'Starting' : 'Working') : 'Running',
      className: 'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/30',
    }
  }
  if (item.status === 'failed') return { label: 'Failed', className: 'bg-rose-500/10 text-rose-700 dark:text-rose-300 border-rose-500/30' }
  if (item.status === 'timed_out') return { label: 'Timed out', className: 'bg-amber-500/10 text-amber-700 dark:text-amber-300 border-amber-500/30' }
  if (item.status === 'succeeded') return { label: 'Finished', className: 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30' }
  return { label: 'Ended', className: 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700' }
}

function Segmented<T extends string>({
  value,
  options,
  onChange,
}: {
  value: T
  options: { id: T; label: string }[]
  onChange: (v: T) => void
}) {
  return (
    <div className="inline-flex rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700">
      {options.map((o) => (
        <button
          key={o.id}
          type="button"
          onClick={() => onChange(o.id)}
          className={`px-2.5 py-1 rounded-md text-xs font-semibold cursor-pointer transition ${
            value === o.id
              ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
              : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
          }`}
        >
          {o.label}
        </button>
      ))}
    </div>
  )
}

export interface SessionsListProps {
  /** Filter to one project (project page); omitted = all visible projects */
  projectId?: string | null
  /** Rendered on the right of the filter bar (e.g. New session ▾) */
  actions?: React.ReactNode
}

/** Simple table of sessions: running first, then ended. */
export const SessionsList: React.FC<SessionsListProps> = ({ projectId = null, actions }) => {
  const { user } = useAuth()
  const { users, agents, backends, navigateToDetail } = useData()
  const [scope, setScope] = useState<'mine' | 'everyone'>('mine')
  const [state, setState] = useState<'all' | 'running' | 'ended'>('all')

  const { data: items = [], isLoading } = useQuery({
    queryKey: ['sessions', projectId, scope, state],
    queryFn: () => api.sessions.list({ project_id: projectId, scope, state, limit: 200 }),
    refetchInterval: (q) =>
      ((q.state.data as SessionListItem[] | undefined) || []).some((i) => i.is_running) ? 5000 : 15000,
  })

  const startedBy = useMemo(() => {
    const map = new Map(users.map((u) => [u.id, u.preferred_name || u.name || u.email]))
    return (id: string | null) => (!id ? '-' : id === user?.id ? 'you' : map.get(id) || '-')
  }, [users, user?.id])

  const colCount = projectId ? 7 : 8

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Segmented
            value={scope}
            onChange={setScope}
            options={[
              { id: 'mine', label: 'Mine' },
              { id: 'everyone', label: 'Everyone' },
            ]}
          />
          <Segmented
            value={state}
            onChange={setState}
            options={[
              { id: 'all', label: 'All' },
              { id: 'running', label: 'Running' },
              { id: 'ended', label: 'Ended' },
            ]}
          />
        </div>
        <div className="flex items-center gap-2">{actions}</div>
      </div>

      <EntityTableCard>
        <EntityTableHeaderRow>
          <th className="py-3 px-4 w-10" aria-label="Kind" />
          <th className="py-3 px-4">Name</th>
          {!projectId && <th className="py-3 px-4">Project</th>}
          <th className="py-3 px-4">Branch</th>
          <th className="py-3 px-4">Status</th>
          <th className="py-3 px-4">Started By</th>
          <th className="py-3 px-4">Last Activity</th>
          <th className="py-3 px-4">Stops In</th>
        </EntityTableHeaderRow>
        <tbody className="divide-y divide-zinc-200 dark:divide-zinc-700/80 text-sm">
          {isLoading ? (
            <tr>
              <td colSpan={colCount} className="py-10 px-4 text-center text-xs text-zinc-400">
                <span className="inline-flex items-center gap-2">
                  <Loader2 size={14} className="animate-spin" />
                  Loading sessions…
                </span>
              </td>
            </tr>
          ) : items.length === 0 ? (
            <tr>
              <td colSpan={colCount} className="py-10 px-4 text-center text-xs text-zinc-500 dark:text-zinc-400">
                <div className="flex flex-col items-center gap-2.5">
                  <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
                    <Terminal size={20} />
                  </div>
                  <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
                    {state === 'running' ? 'Nothing running right now' : 'No sessions yet'}
                  </h4>
                  <p>Start a terminal or chat with an agent from New session.</p>
                </div>
              </td>
            </tr>
          ) : (
            items.map((item) => {
              const st = statusLabel(item)
              const matchedAgent =
                item.kind === 'chat'
                  ? agents.find(
                      (a) =>
                        Boolean(item.agent_handle && a.handle.toLowerCase() === item.agent_handle.toLowerCase())
                    ) || null
                  : null
              return (
                <tr
                  key={`${item.kind}-${item.id}`}
                  onClick={() => navigateToDetail(VIEWS.SESSIONS, item.kind, item.id)}
                  className="hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 transition cursor-pointer"
                >
                  <td className="py-3.5 px-4 text-zinc-400">
                    {item.kind === 'chat' ? (
                      matchedAgent ? (
                        <AgentProviderIcon agent={matchedAgent} backends={backends} size={15} />
                      ) : (
                        <Bot size={15} />
                      )
                    ) : (
                      <Terminal size={15} />
                    )}
                  </td>
                  <td className="py-3.5 px-4 font-semibold text-zinc-900 dark:text-zinc-100 max-w-[300px] truncate">
                    {item.name}
                    {item.agent_handle && item.kind === 'chat' && !item.name.includes(`@${item.agent_handle}`) && (
                      <span className="ml-1.5 inline-flex items-center gap-1 font-mono text-xs font-normal text-teal-700 dark:text-teal-300">
                        {matchedAgent && <AgentProviderIcon agent={matchedAgent} backends={backends} size={11} />}
                        @{item.agent_handle}
                      </span>
                    )}
                  </td>
                  {!projectId && (
                    <td className="py-3.5 px-4 text-zinc-600 dark:text-zinc-300 truncate">{item.project_name}</td>
                  )}
                  <td className="py-3.5 px-4 font-mono text-xs text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                    <span className="inline-flex items-center gap-1">
                      <GitBranch size={11} />
                      {item.ref}
                      {item.head_sha ? `@${item.head_sha.slice(0, 7)}` : ''}
                    </span>
                  </td>
                  <td className="py-3.5 px-4">
                    <span className={`inline-flex px-2 py-0.5 rounded-full text-[11px] font-semibold border ${st.className}`}>
                      {st.label}
                    </span>
                  </td>
                  <td className="py-3.5 px-4 text-xs text-zinc-600 dark:text-zinc-300 whitespace-nowrap">
                    {startedBy(item.started_by)}
                  </td>
                  <td className="py-3.5 px-4 text-xs text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                    {item.last_activity_at ? formatRelativeTime(item.last_activity_at) : '-'}
                  </td>
                  <td className="py-3.5 px-4 text-xs text-zinc-500 dark:text-zinc-400 whitespace-nowrap">
                    {item.is_running ? formatLeft(minutesLeft(item.expires_at)) : '-'}
                  </td>
                </tr>
              )
            })
          )}
        </tbody>
      </EntityTableCard>
    </div>
  )
}
