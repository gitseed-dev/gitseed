import type {
  Agent,
  AgentKind,
  AgentRuntime,
  Backend,
  Run,
  SeedStage,
  StagePolicy,
} from '../types'
import { getEnabledBackendModels } from './backends'

export interface RuntimeBuiltInModelOption {
  value: string
  label: string
}

export interface AgentRuntimeDefinition {
  id: AgentRuntime
  label: string
  shortLabel: string
  description: string
  compatibleProviders: string[] | 'all'
  nativeProvider: string | null
  nativeProviderLabel: string | null
  authSummary: string
  builtInModels: RuntimeBuiltInModelOption[]
}

export const AGENT_RUNTIME_DEFINITIONS: AgentRuntimeDefinition[] = [
  {
    id: 'opencode',
    label: 'OpenCode (Local & Cloud Models)',
    shortLabel: 'OpenCode',
    description:
      'Open-source harness routed via LiteLLM Proxy to any configured Inference backend (LM Studio, Ollama, vLLM, OpenAI, Anthropic, Gemini, or xAI).',
    compatibleProviders: 'all',
    nativeProvider: null,
    nativeProviderLabel: null,
    authSummary:
      'Uses any enabled Inference backend configured in Backends (local Ollama / LM Studio or cloud API keys).',
    builtInModels: [],
  },
  {
    id: 'claude-code',
    label: 'Claude Code (Anthropic CLI)',
    shortLabel: 'Claude Code',
    description:
      'Anthropic Claude Code CLI harness (`claude -p`) with native Claude models or LiteLLM Proxy routing.',
    compatibleProviders: 'all',
    nativeProvider: 'anthropic',
    nativeProviderLabel: 'Anthropic (Claude)',
    authSummary:
      'Authenticates via an Anthropic Inference backend API key (or LiteLLM gateway) injected as ANTHROPIC_AUTH_TOKEN.',
    builtInModels: [
      { value: 'default', label: 'default (Claude Code Default)' },
      { value: 'claude-sonnet-5-5', label: 'claude-sonnet-5-5 (Claude Sonnet 5.5)' },
      { value: 'claude-opus-5-5', label: 'claude-opus-5-5 (Claude Opus 5.5)' },
      { value: 'claude-haiku-5-5', label: 'claude-haiku-5-5 (Claude Haiku 5.5)' },
      { value: 'claude-sonnet-4-6', label: 'claude-sonnet-4-6 (Claude Sonnet 4.6)' },
      { value: 'claude-opus-4-6', label: 'claude-opus-4-6 (Claude Opus 4.6)' },
      { value: 'claude-sonnet-4-5', label: 'claude-sonnet-4-5 (Claude Sonnet 4.5)' },
      { value: 'claude-opus-4-5', label: 'claude-opus-4-5 (Claude Opus 4.5)' },
    ],
  },
  {
    id: 'codex',
    label: 'Codex (OpenAI CLI)',
    shortLabel: 'Codex',
    description:
      'OpenAI Codex CLI harness (`codex exec --full-auto`) with native OpenAI models or LiteLLM Proxy routing.',
    compatibleProviders: 'all',
    nativeProvider: 'openai',
    nativeProviderLabel: 'OpenAI',
    authSummary:
      'Authenticates via an OpenAI Inference backend API key (or LiteLLM gateway) injected into ~/.codex/config.toml.',
    builtInModels: [
      { value: 'default', label: 'default (Codex CLI Default)' },
      { value: 'gpt-5.4', label: 'gpt-5.4 (GPT-5.4 Frontier & Coding)' },
      { value: 'gpt-5.3-codex', label: 'gpt-5.3-codex (GPT-5.3 Codex)' },
      { value: 'gpt-5.2-codex', label: 'gpt-5.2-codex (GPT-5.2 Codex)' },
      { value: 'gpt-5.2', label: 'gpt-5.2' },
      { value: 'gpt-5.2-pro', label: 'gpt-5.2-pro' },
      { value: 'gpt-5.1-codex-max', label: 'gpt-5.1-codex-max' },
      { value: 'gpt-5.1-codex', label: 'gpt-5.1-codex' },
      { value: 'o3', label: 'o3' },
      { value: 'o3-pro', label: 'o3-pro' },
    ],
  },
  {
    id: 'antigravity',
    label: 'Antigravity (agy Headless CLI)',
    shortLabel: 'Antigravity',
    description:
      'Google Antigravity headless CLI (`agy -p --dangerously-skip-permissions`) with built-in model tiers or LiteLLM Proxy routing.',
    compatibleProviders: 'all',
    nativeProvider: 'antigravity',
    nativeProviderLabel: 'Antigravity',
    authSummary:
    'Uses Antigravity built-in model tiers (default, pro, flash, flash_lite) and authenticates via an Antigravity backend or ~/.gemini/antigravity-cli.',
    builtInModels: [
      { value: 'default', label: 'default (Antigravity Default / inherit)' },
      { value: 'pro', label: 'pro (Antigravity Pro Tier)' },
      { value: 'flash', label: 'flash (Antigravity Flash Tier)' },
      { value: 'flash_lite', label: 'flash_lite (Antigravity Flash-Lite Tier)' },
      { value: 'gemini-4-argon', label: 'gemini-4-argon (Gemini 4 Argon)' },
      { value: 'gemini-4-carbon', label: 'gemini-4-carbon (Gemini 4 Carbon)' },
      { value: 'gemini-3.8-flash', label: 'gemini-3.8-flash (Gemini 3.8 Flash)' },
      { value: 'gemini-3.5-flash', label: 'gemini-3.5-flash (Gemini 3.5 Flash)' },
      { value: 'gemini-3.1-pro', label: 'gemini-3.1-pro (Gemini 3.1 Pro)' },
      { value: 'gemini-3.1-flash-lite', label: 'gemini-3.1-flash-lite (Gemini 3.1 Flash-Lite)' },
    ],
  },
  {
    id: 'gemini',
    label: 'Gemini (Google Gemini CLI)',
    shortLabel: 'Gemini',
    description:
      'Google Gemini CLI harness (`gemini -p --yolo`) with native Gemini models or LiteLLM Proxy routing.',
    compatibleProviders: 'all',
    nativeProvider: 'gemini',
    nativeProviderLabel: 'Google Gemini',
    authSummary:
      'Authenticates via a Google Gemini Inference backend API key (GEMINI_API_KEY) or LiteLLM Proxy.',
    builtInModels: [
      { value: 'default', label: 'default (Gemini CLI Default)' },
      { value: 'gemini-4-argon', label: 'gemini-4-argon (Gemini 4 Argon)' },
      { value: 'gemini-4-carbon', label: 'gemini-4-carbon (Gemini 4 Carbon)' },
      { value: 'gemini-3.8-flash', label: 'gemini-3.8-flash (Gemini 3.8 Flash)' },
      { value: 'gemini-3.5-flash', label: 'gemini-3.5-flash (Gemini 3.5 Flash)' },
      { value: 'gemini-3.1-pro', label: 'gemini-3.1-pro (Gemini 3.1 Pro)' },
      { value: 'gemini-3.1-flash-lite', label: 'gemini-3.1-flash-lite (Gemini 3.1 Flash-Lite)' },
      { value: 'gemini-3-pro', label: 'gemini-3-pro (Gemini 3 Pro)' },
      { value: 'gemini-3-flash', label: 'gemini-3-flash (Gemini 3 Flash)' },
    ],
  },
  {
    id: 'grok-build',
    label: 'Grok Build (xAI CLI)',
    shortLabel: 'Grok Build',
    description:
      'xAI Grok Build CLI harness (`grok -p --full-auto`) with native Grok models or LiteLLM Proxy routing.',
    compatibleProviders: 'all',
    nativeProvider: 'xai',
    nativeProviderLabel: 'xAI (Grok)',
    authSummary:
      'Authenticates via an xAI Inference backend API key (XAI_API_KEY) written to ~/.grok/settings.json or LiteLLM Proxy.',
    builtInModels: [
      { value: 'default', label: 'default (Grok Build Default)' },
      { value: 'grok-4.7', label: 'grok-4.7 (Grok 4.7 Flagship & Coding)' },
      { value: 'grok-4.6', label: 'grok-4.6 (Grok 4.6)' },
      { value: 'grok-4.5', label: 'grok-4.5 (Grok 4.5)' },
      { value: 'grok-4.3', label: 'grok-4.3 (Grok 4.3 1M Context)' },
      { value: 'grok-4.20-reasoning', label: 'grok-4.20-reasoning (Grok 4.20 Reasoning)' },
    ],
  },
]

export interface AgentTemplateDefinition {
  id: string
  label: string
  description: string
  defaultName: string
  defaultHandle: string
  kind: AgentKind
  runtime: AgentRuntime
  instructions: string
}

export const AGENT_TEMPLATES: AgentTemplateDefinition[] = [
  {
    id: 'ideator',
    label: 'Ideator',
    description: 'Refines raw seed ideas into specifications & testable acceptance criteria',
    defaultName: 'Ideator',
    defaultHandle: 'ideator',
    kind: 'stage',
    runtime: 'opencode',
    instructions:
      'Refines raw seed ideas into structured technical specifications and testable, bounded acceptance criteria tailored to the repository architecture.',
  },
  {
    id: 'planner',
    label: 'Planner',
    description: 'Concrete file-level change manifest, verification commands & task breakdown',
    defaultName: 'Planner',
    defaultHandle: 'planner',
    kind: 'stage',
    runtime: 'opencode',
    instructions:
      'Inspects the repository and produces a concrete file-by-file change manifest (exact files to create/modify/delete, key functions/classes/interfaces, and verification commands) plus an ordered task list without repeating the specification.',
  },
  {
    id: 'coder',
    label: 'Coder',
    description: 'Containerized code synthesis, test execution & branch push',
    defaultName: 'Coder',
    defaultHandle: 'coder',
    kind: 'stage',
    runtime: 'opencode',
    instructions:
      'Executes approved task plans inside isolated container runners, writes unit and integration tests, verifies test suites pass, and pushes seed branches.',
  },
  {
    id: 'reviewer',
    label: 'Reviewer',
    description: 'Diff audit against spec, plan, acceptance criteria & security boundaries',
    defaultName: 'Reviewer',
    defaultHandle: 'reviewer',
    kind: 'stage',
    runtime: 'opencode',
    instructions:
      'Audits code diffs and pull requests against the approved specification, implementation plan, acceptance criteria, CI status checks, and security boundaries.',
  },
  {
    id: 'merger',
    label: 'Merger',
    description: 'Final PR readiness, CI verification & branch integration',
    defaultName: 'Merger',
    defaultHandle: 'merger',
    kind: 'stage',
    runtime: 'opencode',
    instructions:
      'Verifies final pull request readiness, CI status checks, branch conflicts, and approval gates before completing branch integration into the default branch.',
  },
]

export function getProviderAgentPrefix(provider: string): {
  handlePrefix: string
  displayPrefix: string
  runtime: AgentRuntime
} {
  const norm = (provider || '').trim().toLowerCase()
  switch (norm) {
    case 'anthropic':
      return { handlePrefix: 'claude', displayPrefix: 'Claude', runtime: 'claude-code' }
    case 'antigravity':
      return { handlePrefix: 'agy', displayPrefix: 'Antigravity', runtime: 'antigravity' }
    case 'openai':
      return { handlePrefix: 'open', displayPrefix: 'OpenAI', runtime: 'codex' }
    case 'gemini':
      return { handlePrefix: 'gemini', displayPrefix: 'Gemini', runtime: 'gemini' }
    case 'xai':
      return { handlePrefix: 'grok', displayPrefix: 'Grok', runtime: 'grok-build' }
    default:
      return { handlePrefix: 'open', displayPrefix: 'OpenCode', runtime: 'opencode' }
  }
}

export function getProviderSdlcStageHandles(provider: string): Array<{
  stage: SeedStage
  stageLabel: string
  handle: string
}> {
  const { handlePrefix } = getProviderAgentPrefix(provider)
  return [
    { stage: 'ideate', stageLabel: 'Ideate', handle: `@${handlePrefix}-ideator` },
    { stage: 'plan', stageLabel: 'Plan', handle: `@${handlePrefix}-planner` },
    { stage: 'execute', stageLabel: 'Execute', handle: `@${handlePrefix}-coder` },
    { stage: 'review', stageLabel: 'Review', handle: `@${handlePrefix}-reviewer` },
    { stage: 'merge', stageLabel: 'Merge', handle: `@${handlePrefix}-merger` },
  ]
}

export function slugifyAgentHandle(name: string): string {
  const raw = name
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '')
  if (!raw) return ''
  const prefixed = /^[a-z]/.test(raw) ? raw : `agent-${raw}`
  const padded = prefixed.length === 1 ? `${prefixed}-agent` : prefixed
  return padded.slice(0, 32).replace(/-+$/g, '')
}

export function getRuntimeDefinition(runtime: AgentRuntime): AgentRuntimeDefinition {
  return (
    AGENT_RUNTIME_DEFINITIONS.find((r) => r.id === runtime) ||
    AGENT_RUNTIME_DEFINITIONS[0]
  )
}

export function getAgentAssignedStages(
  agentId: string,
  policies: StagePolicy[]
): SeedStage[] {
  const order: Record<SeedStage, number> = {
    ideate: 1,
    plan: 2,
    execute: 3,
    review: 4,
    merge: 5,
  }
  return policies
    .filter((p) => p.agent_id === agentId)
    .map((p) => p.stage)
    .sort((a, b) => (order[a] ?? 99) - (order[b] ?? 99))
}

export function getAgentActiveRuns(agentId: string, runs: Run[]): Run[] {
  return runs.filter(
    (r) =>
      r.agent_id === agentId &&
      (r.status === 'running' || r.status === 'starting' || r.status === 'queued')
  )
}

export function getAgentFirstPassRate(agent: Agent): number | null {
  if (!agent.run_count || agent.run_count <= 0) return null
  return Math.round((agent.succeeded_runs / agent.run_count) * 100)
}

export interface ConfiguredModelOption {
  value: string
  label: string
  backendName: string
  group?: 'builtin' | 'backend'
}

export function getNativeProviderBackends(
  runtime: AgentRuntime,
  backends: Backend[]
): Backend[] {
  const rtDef = getRuntimeDefinition(runtime)
  if (!rtDef.nativeProvider) return []
  const target = rtDef.nativeProvider.toLowerCase()
  const matchingProviders =
    target === 'antigravity'
      ? new Set(['antigravity', 'gemini', 'litellm'])
      : new Set([target, 'litellm'])
  return backends.filter(
    (b) =>
      b.type === 'inference' &&
      b.enabled &&
      matchingProviders.has(b.provider.toLowerCase())
  )
}

export function getCompatibleModelBackends(
  runtime: AgentRuntime,
  backends: Backend[]
): Backend[] {
  const rtDef = getRuntimeDefinition(runtime)
  const filtered = backends.filter((b) => {
    if (b.type !== 'inference' || !b.enabled) return false
    if (rtDef.compatibleProviders === 'all') return true
    return rtDef.compatibleProviders.includes(b.provider)
  })
  if (rtDef.nativeProvider) {
    const preferred = rtDef.nativeProvider.toLowerCase()
    filtered.sort((a, b) => {
      const rankA =
        a.provider.toLowerCase() === preferred
          ? 0
          : a.provider.toLowerCase() === 'litellm'
            ? 1
            : 2
      const rankB =
        b.provider.toLowerCase() === preferred
          ? 0
          : b.provider.toLowerCase() === 'litellm'
            ? 1
            : 2
      return rankA - rankB
    })
  }
  return filtered
}

export function getConfiguredModelOptions(
  runtime: AgentRuntime,
  backends: Backend[]
): ConfiguredModelOption[] {
  const rtDef = getRuntimeDefinition(runtime)
  const compatible = getCompatibleModelBackends(runtime, backends)

  if (runtime === 'opencode') {
    if (compatible.length === 0) {
      return []
    }
    const options: ConfiguredModelOption[] = [
      {
        value: 'default',
        label:
          compatible.length === 1
            ? `default (${compatible[0].name} default)`
            : 'default (Backend default model)',
        backendName: compatible[0].name,
        group: 'backend',
      },
    ]
    const seen = new Set<string>(['default'])
    for (const b of compatible) {
      const enabledModels = getEnabledBackendModels(b)
      for (const entry of enabledModels) {
        const modelId = entry.id.trim()
        if (modelId && !seen.has(modelId)) {
          seen.add(modelId)
          options.push({
            value: modelId,
            label: `${modelId} (${b.name})`,
            backendName: b.name,
            group: 'backend',
          })
        }
      }
    }
    return options
  }

  const options: ConfiguredModelOption[] = []
  const seen = new Set<string>()

  for (const builtIn of rtDef.builtInModels) {
    seen.add(builtIn.value)
    options.push({
      value: builtIn.value,
      label: builtIn.label,
      backendName: rtDef.shortLabel,
      group: 'builtin',
    })
  }

  for (const b of compatible) {
    const enabledModels = getEnabledBackendModels(b)
    for (const entry of enabledModels) {
      const modelId = entry.id.trim()
      if (modelId && !seen.has(modelId)) {
        seen.add(modelId)
        options.push({
          value: modelId,
          label: `${modelId} (${b.name})`,
          backendName: b.name,
          group: 'backend',
        })
      }
    }
  }

  return options
}
