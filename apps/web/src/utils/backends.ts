import {
  BACKEND_PROVIDER_DEFINITIONS,
  BACKEND_TYPE_ORDER,
  CORE_BACKEND_REQUIREMENTS,
  DEFAULT_CLOUD_MODELS,
  type ProviderDefinition,
  type ProviderSecretField,
} from '../constants/backends'
import type {
  Agent,
  Backend,
  BackendType,
  Project,
  ProjectRepository,
  Run,
} from '../types'

export {
  BACKEND_PROVIDER_DEFINITIONS,
  BACKEND_TYPE_ORDER,
  CORE_BACKEND_REQUIREMENTS,
  DEFAULT_CLOUD_MODELS,
  type ProviderDefinition,
  type ProviderSecretField,
}

export function getProviderDefinition(
  type: BackendType,
  provider: string
): ProviderDefinition {
  const list = BACKEND_PROVIDER_DEFINITIONS[type] || []
  const normalized = provider.toLowerCase().trim()
  return (
    list.find((p) => p.value === normalized) ||
    list[0] || {
      value: normalized || 'custom',
      label: provider || 'Custom',
      defaultEndpoint: '',
      endpointLabel: 'Endpoint',
      endpointPlaceholder: 'https://...',
      secrets: [],
    }
  )
}

export function formatProviderName(provider: string): string {
  const p = provider.toLowerCase().trim()
  switch (p) {
    case 'github':
      return 'GitHub'
    case 'github-app':
      return 'GitHub App'
    case 'github-enterprise':
      return 'GitHub Enterprise'
    case 'gitlab':
      return 'GitLab'
    case 'bitbucket':
      return 'Bitbucket'
    case 'git':
      return 'Git Server'
    case 'local':
      return 'On-Device Local Git'
    case 'litellm':
      return 'LiteLLM'
    case 'anthropic':
      return 'Anthropic'
    case 'openai':
      return 'OpenAI'
    case 'antigravity':
      return 'Antigravity'
    case 'gemini':
      return 'Google Gemini'
    case 'xai':
      return 'xAI (Grok)'
    case 'ollama':
      return 'Ollama'
    case 'vllm':
      return 'vLLM'
    case 'lmstudio':
      return 'LM Studio'
    case 'custom':
      return 'Custom Gateway'
    case 'docker':
      return 'Docker'
    case 'kubernetes':
    case 'k8s':
      return 'Kubernetes'
    case 'mcp-sse':
      return 'MCP (SSE)'
    case 'mcp-stdio':
      return 'MCP (stdio)'
    default:
      return provider || 'Custom'
  }
}

export function formatCleanEndpoint(backend: Backend): string {
  if (backend.type === 'inference' && backend.config?.auth_mode === 'cli_oauth') {
    return `${formatProviderName(backend.provider)} CLI (OAuth)`
  }
  const raw = backend.endpoint?.trim() || ''
  if (!raw) {
    if (backend.type === 'git' && backend.provider === 'github-app') {
      return 'GitHub App'
    }
    if (backend.type === 'git') {
      return formatProviderName(backend.provider)
    }
    if (backend.type === 'compute') {
      return '/var/run/docker.sock'
    }
    if (backend.type === 'tools' && backend.provider === 'mcp-stdio') {
      const cmd = typeof backend.config?.command === 'string' ? backend.config.command : ''
      return cmd || 'stdio'
    }
    return formatProviderName(backend.provider)
  }
  return raw.replace(/^https?:\/\//, '').replace(/^unix:\/\//, '')
}

export function getProjectsUsingBackend(
  backend: Backend,
  projects: Project[],
  projectReposByProject: Record<string, ProjectRepository[]>
): Project[] {
  const activeProjects = projects.filter((p) => !p.archived_at)
  return activeProjects.filter((project) => {
    if (backend.type === 'git') {
      const repos = projectReposByProject[project.id] || []
      if (repos.some((r) => r.backend_id === backend.id)) {
        return true
      }
      if (!project.backends_restricted) {
        return repos.length === 0
      }
      return project.allowed_backend_ids.includes(backend.id)
    }
    if (!project.backends_restricted) {
      return true
    }
    return project.allowed_backend_ids.includes(backend.id)
  })
}

const ACTIVE_RUN_STATUSES = new Set(['queued', 'starting', 'running'])

function doesRunMatchModelBackend(
  run: Run,
  backend: Backend,
  backendModelIds: Set<string>
): boolean {
  if (backend.access_restricted && !backend.allowed_agent_ids.includes(run.agent_id)) {
    return false
  }
  const rawModel = (run.model || '').toLowerCase().trim()
  if (!rawModel) return false
  const slashIdx = rawModel.indexOf('/')
  const prefix = slashIdx >= 0 ? rawModel.slice(0, slashIdx) : ''
  const bareModel = slashIdx >= 0 ? rawModel.slice(slashIdx + 1) : rawModel

  if (backendModelIds.has(rawModel) || backendModelIds.has(bareModel)) {
    return true
  }

  const provider = backend.provider.toLowerCase().trim()
  if (prefix && prefix === provider) {
    return true
  }
  if (provider === 'anthropic' && bareModel.startsWith('claude')) {
    return true
  }
  if (
    provider === 'openai' &&
    (bareModel.startsWith('gpt-') || /^o[1-9]\b/.test(bareModel))
  ) {
    return true
  }
  if (
    (provider === 'gemini' || provider === 'antigravity') &&
    bareModel.startsWith('gemini')
  ) {
    return true
  }
  if (provider === 'xai' && bareModel.startsWith('grok')) {
    return true
  }

  return false
}

export function getBackendCapacityUsage(
  backend: Backend,
  runs: Run[]
): { current?: number; max?: number; display: string } {
  if (backend.type === 'git' || backend.type === 'tools') {
    return { display: '-' }
  }
  if (!backend.capacity || backend.capacity <= 0) {
    return { display: '-' }
  }

  const backendModelIds =
    backend.type === 'inference'
      ? new Set(getEnabledBackendModels(backend).map((m) => m.id.toLowerCase()))
      : new Set<string>()

  const activeCount = runs.filter((r) => {
    if (!ACTIVE_RUN_STATUSES.has(r.status)) return false
    if (backend.type === 'compute') {
      return r.compute_backend_id === backend.id
    }
    if (backend.type === 'inference') {
      return doesRunMatchModelBackend(r, backend, backendModelIds)
    }
    return false
  }).length

  const current = Math.min(activeCount, backend.capacity)
  return {
    current,
    max: backend.capacity,
    display: `${current} / ${backend.capacity} busy`,
  }
}

export function getBackendAccessSummary(
  backend: Backend,
  allAgents: Agent[],
  unrestrictedLabel = 'All agents'
) {
  const agentMap = new Map(allAgents.map((a) => [a.id, a]))
  const allowedAgents = backend.allowed_agent_ids
    .map((id) => agentMap.get(id))
    .filter((a): a is Agent => Boolean(a))

  const isAll = !backend.access_restricted
  const isRestrictedToOne = backend.access_restricted && allowedAgents.length === 1
  const isRestrictedEmpty = backend.access_restricted && allowedAgents.length === 0

  let label = unrestrictedLabel
  if (backend.access_restricted) {
    if (isRestrictedToOne) {
      label = `@${allowedAgents[0].handle} only`
    } else if (isRestrictedEmpty) {
      label = 'No agents'
    } else {
      label = `${allowedAgents.length} agents`
    }
  }

  const tooltip = isAll
    ? unrestrictedLabel
    : allowedAgents.length > 0
      ? allowedAgents.map((a) => `@${a.handle}`).join(', ')
      : 'Restricted: no agents granted access yet'

  return {
    allowedAgents,
    count: isAll ? allAgents.length : allowedAgents.length,
    isAll,
    isRestrictedToOne,
    isRestrictedEmpty,
    label,
    tooltip,
  }
}

export interface GitBackendRepoEntry {
  full_name: string
  default_branch: string
  private?: boolean
  enabled: boolean
}

export function getBackendRepositories(
  backend?: Backend | null
): GitBackendRepoEntry[] {
  if (!backend || backend.type !== 'git') return []
  const raw = backend.config?.repositories
  if (!Array.isArray(raw)) return []
  const result: GitBackendRepoEntry[] = []
  const seen = new Set<string>()
  for (const item of raw) {
    if (
      item &&
      typeof item === 'object' &&
      typeof (item as Record<string, unknown>).full_name === 'string'
    ) {
      const rec = item as Record<string, unknown>
      const fullName = String(rec.full_name).trim()
      if (!fullName) continue
      const key = fullName.toLowerCase()
      if (seen.has(key)) continue
      seen.add(key)
      result.push({
        full_name: fullName,
        default_branch:
          typeof rec.default_branch === 'string' && rec.default_branch.trim()
            ? rec.default_branch.trim()
            : 'main',
        private: Boolean(rec.private),
        enabled: rec.enabled !== false,
      })
    }
  }
  return result.sort((a, b) =>
    a.full_name.localeCompare(b.full_name, undefined, { sensitivity: 'base' })
  )
}

export function getEnabledBackendRepositories(
  backend?: Backend | null
): GitBackendRepoEntry[] {
  return getBackendRepositories(backend).filter((r) => r.enabled)
}

export interface ModelBackendEntry {
  id: string
  owned_by?: string
  enabled: boolean
}

export function getBackendModels(backend?: Backend | null): ModelBackendEntry[] {
  if (!backend || backend.type !== 'inference') return []
  const rawModels = backend.config?.models
  const legacyDiscovered = backend.config?.discovered_models
  const result: ModelBackendEntry[] = []
  const seen = new Set<string>()

  if (Array.isArray(rawModels)) {
    for (const item of rawModels) {
      if (
        item &&
        typeof item === 'object' &&
        typeof (item as Record<string, unknown>).id === 'string'
      ) {
        const rec = item as Record<string, unknown>
        const modelId = String(rec.id).trim()
        if (!modelId) continue
        const key = modelId.toLowerCase()
        if (seen.has(key)) continue
        seen.add(key)
        result.push({
          id: modelId,
          owned_by:
            typeof rec.owned_by === 'string' && rec.owned_by.trim()
              ? rec.owned_by.trim()
              : undefined,
          enabled: rec.enabled !== false,
        })
      }
    }
  }

  if (Array.isArray(legacyDiscovered)) {
    for (const item of legacyDiscovered) {
      if (typeof item === 'string' && item.trim()) {
        const modelId = item.trim()
        const key = modelId.toLowerCase()
        if (seen.has(key)) continue
        seen.add(key)
        result.push({
          id: modelId,
          owned_by: backend.provider,
          enabled: true,
        })
      }
    }
  }

  if (result.length === 0) {
    const fallback = DEFAULT_CLOUD_MODELS[backend.provider.toLowerCase()]
    if (fallback) {
      for (const modelId of fallback) {
        result.push({
          id: modelId,
          owned_by: backend.provider,
          enabled: true,
        })
      }
    }
  }

  return result.sort((a, b) =>
    a.id.localeCompare(b.id, undefined, { sensitivity: 'base' })
  )
}

export function getEnabledBackendModels(
  backend?: Backend | null
): ModelBackendEntry[] {
  return getBackendModels(backend).filter((m) => m.enabled)
}

export function getMissingCoreBackends(backends: Backend[]): string[] {
  return CORE_BACKEND_REQUIREMENTS.filter(
    (req) => !backends.some((b) => b.type === req.type && b.enabled)
  ).map((req) => req.label)
}
