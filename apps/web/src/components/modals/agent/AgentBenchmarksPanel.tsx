import React, { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { BarChart3, Bot } from 'lucide-react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { RuntimeIcon } from '../../common/ProviderIcon'
import { StageBadge } from '../../common/StageBadge'
import {
  EntityTableCard,
  EntityTableHeaderRow,
  SortableTh,
} from '../../layout/EntityPageLayout'
import { useTableSort } from '../../../hooks/useTableSort'
import { getRuntimeDefinition } from '../../../utils/agents'
import type { AgentBenchmarkEntry, Project, SeedStage } from '../../../types'

const STAGES: { id: SeedStage | 'all'; label: string }[] = [
  { id: 'all', label: 'All Stages' },
  { id: 'ideate', label: 'Ideate' },
  { id: 'plan', label: 'Plan' },
  { id: 'execute', label: 'Execute' },
  { id: 'review', label: 'Review' },
  { id: 'merge', label: 'Merge' },
]

type BenchmarkSortKey =
  | 'agent'
  | 'stage'
  | 'runtime'
  | 'firstPass'
  | 'attempts'
  | 'costPerSeed'
  | 'runs'

export interface AgentBenchmarksPanelProps {
  projects: Project[]
  searchQuery: string
}

export const AgentBenchmarksPanel: React.FC<AgentBenchmarksPanelProps> = ({
  projects,
  searchQuery,
}) => {
  const [selectedProjectId, setSelectedProjectId] = useState<string>('all')
  const [selectedStage, setSelectedStage] = useState<SeedStage | 'all'>('all')

  const { data: benchmarks, isPending } = useQuery({
    queryKey: queryKeys.agentBenchmarks(
      selectedProjectId === 'all' ? null : selectedProjectId,
      selectedStage === 'all' ? null : selectedStage
    ),
    queryFn: () =>
      api.agents.benchmarks({
        project_id: selectedProjectId === 'all' ? null : selectedProjectId,
        stage: selectedStage === 'all' ? null : selectedStage,
      }),
  })

  const filteredEntries = useMemo(() => {
    const list = benchmarks?.entries || []
    if (!searchQuery.trim()) return list
    const q = searchQuery.toLowerCase().trim()
    return list.filter(
      (e) =>
        e.agent_handle.toLowerCase().includes(q) ||
        e.agent_name.toLowerCase().includes(q) ||
        e.model.toLowerCase().includes(q) ||
        e.runtime.toLowerCase().includes(q) ||
        (e.stage || '').toLowerCase().includes(q)
    )
  }, [benchmarks?.entries, searchQuery])

  const stageOrder: Record<string, number> = {
    ideate: 0,
    plan: 1,
    execute: 2,
    review: 3,
    merge: 4,
  }

  const {
    sortedItems: sortedEntries,
    sortConfig,
    requestSort,
  } = useTableSort<AgentBenchmarkEntry, BenchmarkSortKey>({
    items: filteredEntries,
    initialKey: 'stage',
    initialDirection: 'asc',
    customComparators: {
      agent: (a, b) => a.agent_name.localeCompare(b.agent_name),
      stage: (a, b) =>
        (stageOrder[a.stage || ''] ?? 99) - (stageOrder[b.stage || ''] ?? 99),
      runtime: (a, b) =>
        `${a.runtime}:${a.model}`.localeCompare(`${b.runtime}:${b.model}`),
      firstPass: (a, b) =>
        (a.first_pass_approval_rate ?? -1) - (b.first_pass_approval_rate ?? -1),
      attempts: (a, b) => a.avg_attempts_per_stage - b.avg_attempts_per_stage,
      costPerSeed: (a, b) =>
        Number(a.avg_cost_per_seed || 0) - Number(b.avg_cost_per_seed || 0),
      runs: (a, b) => a.run_count - b.run_count,
    },
  })

  // Group entries by stage for the compact visual stage cards (replaces prose wall)
  const stageSummaries = useMemo(() => {
    const byStage = new Map<SeedStage, AgentBenchmarkEntry[]>()
    for (const entry of filteredEntries) {
      if (!entry.stage) continue
      const existing = byStage.get(entry.stage) || []
      existing.push(entry)
      byStage.set(entry.stage, existing)
    }
    return Array.from(byStage.entries())
      .sort(([stA], [stB]) => (stageOrder[stA] ?? 99) - (stageOrder[stB] ?? 99))
      .map(([stage, items]) => {
        const sorted = [...items].sort((a, b) => {
          const fpDiff =
            (b.first_pass_approval_rate ?? -1) -
            (a.first_pass_approval_rate ?? -1)
          if (fpDiff !== 0) return fpDiff
          return (
            Number(a.avg_cost_per_seed || 0) - Number(b.avg_cost_per_seed || 0)
          )
        })
        return {
          stage,
          leader: sorted[0],
          runnerUp: sorted.length > 1 ? sorted[1] : null,
        }
      })
  }, [filteredEntries])

  const activeProjects = projects.filter((p) => !p.archived_at)

  return (
    <div className="space-y-4">
      {/* Scope Filter Bar */}
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 px-4 py-3 shadow-xs">
        <div className="flex items-center gap-1.5 flex-wrap">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400 mr-1">
            Stage:
          </span>
          {STAGES.map((st) => {
            const active = selectedStage === st.id
            return (
              <button
                key={st.id}
                type="button"
                onClick={() => setSelectedStage(st.id)}
                className={`px-2.5 py-1 rounded-xl text-xs font-medium transition-colors cursor-pointer border ${
                  active
                    ? 'bg-zinc-200/85 dark:bg-zinc-700/75 text-zinc-900 dark:text-zinc-100 border-zinc-300 dark:border-zinc-600 font-semibold'
                    : 'bg-transparent border-transparent text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800/60'
                }`}
              >
                {st.label}
              </button>
            )
          })}
        </div>

        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-zinc-500 dark:text-zinc-400">
            Project:
          </span>
          <select
            value={selectedProjectId}
            onChange={(e) => setSelectedProjectId(e.target.value)}
            className="rounded-xl border border-zinc-200/80 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-800/80 px-3 py-1.5 text-xs font-medium text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-2 focus:ring-teal-500/30"
          >
            <option value="all">All Projects</option>
            {activeProjects.map((p) => (
              <option key={p.id} value={p.id}>
                {p.name} ({p.slug})
              </option>
            ))}
          </select>
        </div>
      </div>

      {/* Compact Stage Benchmark Cards (Scannable KPIs instead of prose wall) */}
      {stageSummaries.length > 0 && (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
          {stageSummaries.map(({ stage, leader, runnerUp }) => {
            const fp = leader.first_pass_approval_rate
            const fpColor =
              fp === null
                ? 'text-zinc-400'
                : fp >= 80
                  ? 'text-emerald-600 dark:text-emerald-400'
                  : fp >= 50
                    ? 'text-amber-600 dark:text-amber-400'
                    : 'text-rose-600 dark:text-rose-400'

            return (
              <div
                key={stage}
                className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 p-3.5 shadow-xs flex flex-col justify-between gap-2.5"
              >
                <div className="flex items-center justify-between gap-2">
                  <StageBadge stage={stage} className="w-[4.5rem] shrink-0" />
                  <span className="font-mono tabular-nums text-xs text-zinc-500 dark:text-zinc-400">
                    {leader.seed_count} {leader.seed_count === 1 ? 'seed' : 'seeds'}
                  </span>
                </div>

                <div className="min-w-0">
                  <div className="flex items-center gap-1.5 min-w-0">
                    <span className="font-semibold text-xs text-zinc-900 dark:text-zinc-100 truncate">
                      {leader.agent_name}
                    </span>
                    <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400 shrink-0">
                      @{leader.agent_handle}
                    </span>
                  </div>
                  <div className="font-mono text-xs text-zinc-500 dark:text-zinc-400 truncate mt-0.5">
                    {leader.model}
                  </div>
                </div>

                <div className="pt-2 border-t border-zinc-100 dark:border-zinc-800/80 flex items-center justify-between gap-2 text-xs font-mono tabular-nums">
                  <span className={`font-semibold ${fpColor}`}>
                    {fp !== null ? `${fp.toFixed(0)}% 1st-pass` : '-'}
                  </span>
                  <span className="text-zinc-700 dark:text-zinc-300 font-medium">
                    ${Number(leader.avg_cost_per_seed || 0).toFixed(2)}/seed
                  </span>
                </div>

                {runnerUp && (
                  <div className="text-xs text-zinc-500 dark:text-zinc-400 flex items-center justify-between gap-1 truncate">
                    <span className="font-mono truncate">vs @{runnerUp.agent_handle}</span>
                    <span className="font-mono tabular-nums shrink-0">
                      {runnerUp.first_pass_approval_rate !== null
                        ? `${runnerUp.first_pass_approval_rate.toFixed(0)}%`
                        : '-'}{' '}
                      · ${Number(runnerUp.avg_cost_per_seed || 0).toFixed(2)}
                    </span>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}

      {/* Leaderboard Table */}
      {isPending ? (
        <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 p-10 text-center text-xs text-zinc-500 dark:text-zinc-400">
          Computing empirical agent & model benchmarks...
        </div>
      ) : sortedEntries.length === 0 ? (
        <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 p-10 flex flex-col items-center gap-2 text-center">
          <div className="w-10 h-10 rounded-2xl bg-zinc-100 dark:bg-zinc-800 flex items-center justify-center text-zinc-400">
            <BarChart3 size={18} />
          </div>
          <div className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
            No stage runs recorded for this filter yet
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
            Once agents execute seed stages and pass human or policy gates, empirical first-pass approval rates and cost-per-seed comparisons appear here automatically.
          </p>
        </div>
      ) : (
        <EntityTableCard>
          <EntityTableHeaderRow>
            <SortableTh<BenchmarkSortKey>
              sortKey="agent"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
            >
              Agent
            </SortableTh>
            <SortableTh<BenchmarkSortKey>
              sortKey="stage"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
            >
              Stage
            </SortableTh>
            <SortableTh<BenchmarkSortKey>
              sortKey="runtime"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
            >
              Runtime · Model
            </SortableTh>
            <SortableTh<BenchmarkSortKey>
              sortKey="firstPass"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
            >
              First-Pass Approval
            </SortableTh>
            <SortableTh<BenchmarkSortKey>
              sortKey="attempts"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
              align="right"
            >
              Rework
            </SortableTh>
            <SortableTh<BenchmarkSortKey>
              sortKey="costPerSeed"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
              align="right"
            >
              Avg Cost / Seed
            </SortableTh>
            <SortableTh<BenchmarkSortKey>
              sortKey="runs"
              currentSortKey={sortConfig.key}
              currentSortDirection={sortConfig.direction}
              onSort={requestSort}
              align="right"
            >
              Runs & Latency
            </SortableTh>
          </EntityTableHeaderRow>
          <tbody className="divide-y divide-zinc-200 dark:divide-zinc-800/60">
            {sortedEntries.map((entry) => {
              const runtimeDef = getRuntimeDefinition(entry.runtime)
              const fp = entry.first_pass_approval_rate
              const fpColor =
                fp === null
                  ? 'text-zinc-400'
                  : fp >= 80
                    ? 'text-emerald-600 dark:text-emerald-400'
                    : fp >= 50
                      ? 'text-amber-600 dark:text-amber-400'
                      : 'text-rose-600 dark:text-rose-400'
              const barColor =
                fp === null
                  ? 'bg-zinc-300 dark:bg-zinc-700'
                  : fp >= 80
                    ? 'bg-emerald-500'
                    : fp >= 50
                      ? 'bg-amber-500'
                      : 'bg-rose-500'

              return (
                <tr
                  key={`${entry.agent_id}:${entry.runtime}:${entry.model}:${entry.stage}`}
                  className="hover:bg-zinc-100/80 dark:hover:bg-zinc-800/40 transition-colors"
                >
                  <td className="py-3.5 px-4 whitespace-nowrap">
                    <div className="flex items-center gap-3 min-w-0">
                      <div className="w-8 h-8 rounded-xl bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border border-zinc-200/80 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
                        <Bot size={15} />
                      </div>
                      <div className="flex items-center gap-2 min-w-0">
                        <span className="font-semibold text-xs text-zinc-900 dark:text-zinc-100 truncate">
                          {entry.agent_name}
                        </span>
                        <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                          @{entry.agent_handle}
                        </span>
                      </div>
                    </div>
                  </td>

                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {entry.stage ? (
                      <StageBadge stage={entry.stage} className="w-[4.5rem]" />
                    ) : (
                      <span className="text-xs text-zinc-500">All</span>
                    )}
                  </td>

                  <td className="py-3.5 px-4 whitespace-nowrap">
                    <div className="flex items-center gap-1.5 text-xs">
                      <RuntimeIcon runtime={entry.runtime} size={13} />
                      <span className="font-medium text-zinc-800 dark:text-zinc-200">
                        {runtimeDef.shortLabel}
                      </span>
                      <span className="text-zinc-400">·</span>
                      <span className="font-mono text-zinc-500 dark:text-zinc-400">
                        {entry.model}
                      </span>
                    </div>
                  </td>

                  <td className="py-3.5 px-4 whitespace-nowrap">
                    {fp !== null ? (
                      <div className="flex items-center gap-2.5 text-xs font-mono tabular-nums">
                        <div className="w-16 h-1.5 rounded-full bg-zinc-200 dark:bg-zinc-800 overflow-hidden shrink-0">
                          <div
                            className={`h-full rounded-full ${barColor}`}
                            style={{ width: `${Math.min(100, Math.max(0, fp))}%` }}
                          />
                        </div>
                        <span className={`font-semibold ${fpColor}`}>
                          {fp.toFixed(0)}%
                        </span>
                        <span className="text-zinc-400 dark:text-zinc-500">
                          ({entry.first_pass_approved_count}/{entry.evaluated_gate_count})
                        </span>
                      </div>
                    ) : (
                      <span className="text-xs text-zinc-400">-</span>
                    )}
                  </td>

                  <td className="py-3.5 px-4 text-right whitespace-nowrap font-mono tabular-nums text-xs">
                    <span
                      className={
                        entry.avg_attempts_per_stage <= 1.2
                          ? 'text-emerald-600 dark:text-emerald-400 font-semibold'
                          : entry.avg_attempts_per_stage <= 1.8
                            ? 'text-amber-600 dark:text-amber-400 font-semibold'
                            : 'text-rose-600 dark:text-rose-400 font-semibold'
                      }
                    >
                      {entry.avg_attempts_per_stage.toFixed(2)}x
                    </span>
                  </td>

                  <td className="py-3.5 px-4 text-right whitespace-nowrap font-mono tabular-nums text-xs">
                    <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                      ${Number(entry.avg_cost_per_seed || 0).toFixed(2)}
                    </span>
                    <span className="text-zinc-400 dark:text-zinc-500">
                      {' '}
                      · ${Number(entry.total_cost || 0).toFixed(2)} total
                    </span>
                  </td>

                  <td className="py-3.5 px-4 text-right whitespace-nowrap font-mono tabular-nums text-xs">
                    <span className="text-zinc-800 dark:text-zinc-200 font-medium">
                      {entry.succeeded_runs}/{entry.run_count}
                    </span>
                    <span className="text-zinc-400 dark:text-zinc-500">
                      {entry.avg_duration_seconds !== null
                        ? ` · ${entry.avg_duration_seconds.toFixed(0)}s`
                        : ''}
                    </span>
                  </td>
                </tr>
              )
            })}
          </tbody>
        </EntityTableCard>
      )}
    </div>
  )
}
