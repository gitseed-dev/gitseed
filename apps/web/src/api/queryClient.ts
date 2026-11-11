import { QueryClient } from '@tanstack/react-query'

export const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      gcTime: 5 * 60_000,
      retry: 1,
      refetchOnWindowFocus: true,
    },
  },
})

export const queryKeys = {
  health: ['system', 'health'] as const,
  projects: ['projects'] as const,
  projectDetail: (id: string) => ['projects', id] as const,
  projectRepos: (id: string) => ['projects', id, 'repos'] as const,
  projectMembers: (id: string) => ['projects', id, 'members'] as const,
  projectPolicies: (id: string) => ['projects', id, 'policies'] as const,
  projectSandboxes: (id: string) => ['projects', id, 'sandboxes'] as const,
  projectAgentSessions: (id: string) => ['projects', id, 'agentSessions'] as const,
  projectSandboxConfig: (id: string, repoId?: number | null, ref?: string | null) =>
    ['projects', id, 'sandboxConfig', repoId ?? 'primary', ref ?? 'default'] as const,
  seeds: ['seeds'] as const,
  seedDetail: (id: string) => ['seeds', id] as const,
  seedRuns: (id: string) => ['seeds', id, 'runs'] as const,
  seedTasks: (id: string) => ['seeds', id, 'tasks'] as const,
  seedApprovals: (id: string) => ['seeds', id, 'approvals'] as const,
  seedComments: (id: string) => ['seeds', id, 'comments'] as const,
  seedChanges: (id: string, repoId?: number | null) =>
    ['seeds', id, 'changes', repoId ?? 'primary'] as const,
  seedActivity: (id: string) => ['seeds', id, 'activity'] as const,
  runs: ['runs'] as const,
  activity: ['activity'] as const,
  backends: ['backends'] as const,
  agents: ['agents'] as const,
  agentBenchmarks: (projectId?: string | null, stage?: string | null) =>
    ['agents', 'benchmarks', projectId ?? 'all', stage ?? 'all'] as const,
  runtimeAvailability: ['runtime-availability'] as const,
  skills: ['skills'] as const,
  policies: ['policies'] as const,
  users: ['users'] as const,
  settings: ['settings'] as const,
  tokens: ['auth', 'tokens'] as const,
}

