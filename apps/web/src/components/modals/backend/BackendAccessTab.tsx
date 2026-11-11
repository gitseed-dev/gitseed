import React from 'react'
import { ShieldAlert } from 'lucide-react'
import type { Agent, Project } from '../../../types'

export interface BackendAccessTabProps {
  accessRestricted: boolean
  setAccessRestricted: (restricted: boolean) => void
  selectedAgentIds: string[]
  onToggleAgent: (agentId: string) => void
  agents: Agent[]
  usedProjects: Project[]
  onNavigateProject?: (projectId: string) => void
}

export const BackendAccessTab: React.FC<BackendAccessTabProps> = ({
  accessRestricted,
  setAccessRestricted,
  selectedAgentIds,
  onToggleAgent,
  agents,
  usedProjects,
  onNavigateProject,
}) => {
  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
          Agent Scoping & Access
        </span>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Choose whether all agents in the organization can use this backend or restrict it to
          specific agents.
        </p>
      </div>

      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={() => setAccessRestricted(false)}
          className={`px-3.5 py-2 rounded-xl border text-xs font-medium cursor-pointer transition-all ${
            !accessRestricted
              ? 'bg-teal-500/10 border-teal-500/40 text-teal-700 dark:text-teal-300 font-semibold shadow-xs'
              : 'bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700/80 text-zinc-600 dark:text-zinc-400'
          }`}
        >
          All agents (Unrestricted)
        </button>
        <button
          type="button"
          onClick={() => setAccessRestricted(true)}
          className={`px-3.5 py-2 rounded-xl border text-xs font-medium cursor-pointer transition-all ${
            accessRestricted
              ? 'bg-amber-500/10 border-amber-500/40 text-amber-700 dark:text-amber-300 font-semibold shadow-xs'
              : 'bg-zinc-100 dark:bg-zinc-800 border-zinc-200 dark:border-zinc-700/80 text-zinc-600 dark:text-zinc-400'
          }`}
        >
          Restricted to specific agents
        </button>
      </div>

      {accessRestricted && (
        <div className="flex flex-col gap-2.5 pt-3 border-t border-zinc-200 dark:border-zinc-700/80">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              Authorized Agents ({selectedAgentIds.length})
            </span>
            {selectedAgentIds.length === 0 && (
              <span className="inline-flex items-center gap-1 text-xs text-amber-600 dark:text-amber-400 font-medium">
                <ShieldAlert size={13} />
                <span>No agents selected</span>
              </span>
            )}
          </div>

          {agents.length === 0 ? (
            <div className="p-4 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-800/40 text-zinc-500 dark:text-zinc-400 text-xs">
              No agents registered yet. Register agents in the Agents view to grant restricted
              access.
            </div>
          ) : (
            <div className="flex flex-col divide-y divide-zinc-200 dark:divide-zinc-700/80 border border-zinc-200 dark:border-zinc-700/80 rounded-xl overflow-hidden">
              {agents.map((ag) => {
                const isChecked = selectedAgentIds.includes(ag.id)
                return (
                  <label
                    key={ag.id}
                    className="flex items-center justify-between p-3.5 hover:bg-zinc-50 dark:hover:bg-zinc-800/50 cursor-pointer"
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <input
                        type="checkbox"
                        checked={isChecked}
                        onChange={() => onToggleAgent(ag.id)}
                        className="w-4 h-4 rounded border-zinc-200 dark:border-zinc-700/80 text-teal-600 focus:ring-teal-500 cursor-pointer"
                      />
                      <div className="min-w-0 text-sm">
                        <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                          {ag.name}
                        </span>
                        <span className="font-mono text-xs text-zinc-400 ml-1.5">
                          @{ag.handle}
                        </span>
                      </div>
                    </div>
                    <span className="text-xs text-zinc-400 font-mono uppercase">
                      {ag.kind} · {ag.runtime}
                    </span>
                  </label>
                )
              })}
            </div>
          )}
        </div>
      )}

      {/* Projects Using Backend */}
      <div className="flex flex-col gap-2 pt-3 border-t border-zinc-200 dark:border-zinc-700/80">
        <span className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
          Projects Using This Backend ({usedProjects.length})
        </span>
        {usedProjects.length === 0 ? (
          <p className="text-xs text-zinc-500 dark:text-zinc-400">
            No active projects currently use this backend.
          </p>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {usedProjects.map((p) => (
              <button
                key={p.id}
                type="button"
                onClick={() => onNavigateProject?.(p.id)}
                className="px-2.5 py-1 rounded-lg bg-zinc-100 dark:bg-zinc-800 hover:bg-zinc-200/80 dark:hover:bg-zinc-700 text-xs font-mono text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 cursor-pointer transition-colors"
              >
                {p.name}
              </button>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
