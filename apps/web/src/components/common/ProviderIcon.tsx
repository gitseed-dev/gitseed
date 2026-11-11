import React from 'react'
import { Cpu, GitBranch, Layers, Sparkles, Terminal, Wrench } from 'lucide-react'
import type { Agent, AgentRuntime, Backend, BackendType } from '../../types'
import { getEnabledBackendModels } from '../../utils/backends'

const GitHubIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path
      fillRule="evenodd"
      clipRule="evenodd"
      d="M12 2C6.477 2 2 6.484 2 12.017c0 4.425 2.865 8.18 6.839 9.504.5.092.682-.217.682-.483 0-.237-.008-.868-.013-1.703-2.782.605-3.369-1.343-3.369-1.343-.454-1.158-1.11-1.466-1.11-1.466-.908-.62.069-.608.069-.608 1.003.07 1.53 1.032 1.53 1.032.892 1.53 2.341 1.088 2.91.832.092-.647.35-1.088.636-1.338-2.22-.253-4.555-1.113-4.555-4.951 0-1.093.39-1.988 1.029-2.688-.103-.253-.446-1.272.098-2.65 0 0 .84-.27 2.75 1.026A9.564 9.564 0 0112 6.844c.85.004 1.705.115 2.504.337 1.909-1.296 2.747-1.027 2.747-1.027.546 1.379.202 2.398.1 2.651.64.7 1.028 1.595 1.028 2.688 0 3.848-2.339 4.695-4.566 4.943.359.309.678.92.678 1.855 0 1.338-.012 2.419-.012 2.747 0 .268.18.58.688.482A10.019 10.019 0 0022 12.017C22 6.484 17.522 2 12 2z"
    />
  </svg>
)

const GitLabIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="m23.6 9.89-1.28-3.95a.94.94 0 0 0-.35-.46.96.96 0 0 0-.58-.15.98.98 0 0 0-.56.19.95.95 0 0 0-.3.42L18.7 11.5H5.3L3.47 5.94a.95.95 0 0 0-.3-.42.98.98 0 0 0-.56-.19.96.96 0 0 0-.58.15.94.94 0 0 0-.35.46L.4 9.89a1.76 1.76 0 0 0 .64 2L12 20.4l10.96-8.51a1.76 1.76 0 0 0 .64-2z" />
  </svg>
)

const BitbucketIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M2.65 3C2.1 3 1.68 3.49 1.77 4.03l2.74 16.21c.08.47.49.81.97.81h13.28c.37 0 .69-.26.76-.62l2.73-16.4c.09-.54-.33-1.03-.88-1.03H2.65zm11.52 11.83H9.88L8.8 9.17h6.41l-1.04 5.66z" />
  </svg>
)

const AnthropicIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M14.07 3.5h-3.4L18 20.5h3.4L14.07 3.5zm-8.8 0L0 20.5h3.35l1.37-3.2h5.92l1.37 3.2h3.35L9.83 3.5H5.27zm.82 11.05 2.13-5 2.13 5H6.09z" />
  </svg>
)

const OpenAIIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M22.2819 9.8211a5.9847 5.9847 0 0 0-.5157-4.9108 6.0462 6.0462 0 0 0-6.5098-2.9A6.0651 6.0651 0 0 0 4.9807 4.1818a5.9847 5.9847 0 0 0-3.9977 2.9 6.0462 6.0462 0 0 0 .7427 7.0966 5.98 5.98 0 0 0 .511 4.9107 6.051 6.051 0 0 0 6.5146 2.9001A5.9847 5.9847 0 0 0 13.2599 24a6.0557 6.0557 0 0 0 5.7718-4.2058 5.9894 5.9894 0 0 0 3.9977-2.9001 6.0557 6.0557 0 0 0-.7475-7.0729zm-9.022 12.6081a4.4755 4.4755 0 0 1-2.8764-1.0408l.1419-.0804 4.7783-2.7582a.7948.7948 0 0 0 .3927-.6813v-6.7369l2.02 1.1686a.071.071 0 0 1 .038.052v5.5826a4.5045 4.5045 0 0 1-4.4945 4.4944zm-9.6607-4.1254a4.4708 4.4708 0 0 1-.5346-3.0137l.142.0852 4.783 2.7582a.7712.7712 0 0 0 .7806 0l5.8428-3.3685v2.3324a.0804.0804 0 0 1-.0332.0615L9.74 19.9502a4.4992 4.4992 0 0 1-6.1408-1.6464zM2.3408 7.8956a4.485 4.485 0 0 1 2.3655-1.9728V11.6a.7664.7664 0 0 0 .3879.6765l5.8144 3.3543-2.0201 1.1685a.0757.0757 0 0 1-.071 0l-4.8303-2.7865A4.504 4.504 0 0 1 2.3408 7.872zm16.5963 3.8558L13.1038 8.364 15.1192 7.2a.0757.0757 0 0 1 .071 0l4.8303 2.7913a4.4944 4.4944 0 0 1-.6765 8.1042v-5.6772a.79.79 0 0 0-.407-.6667zm2.0107-3.0231l-.142-.0852-4.7735-2.7818a.7759.7759 0 0 0-.7854 0L9.409 9.2297V6.8974a.0662.0662 0 0 1 .0284-.0615l4.8303-2.7866a4.4992 4.4992 0 0 1 6.6802 4.66zM8.3065 12.863l-2.02-1.1638a.0804.0804 0 0 1-.038-.0567V6.0742a4.4992 4.4992 0 0 1 7.3757-3.4537l-.142.0805L8.704 5.459a.7948.7948 0 0 0-.3927.6813zm1.0976-2.3654l2.602-1.4998 2.6069 1.4998v2.9994l-2.5974 1.4997-2.6067-1.4997Z" />
  </svg>
)

const DockerIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M13.983 11.078h2.119a.186.186 0 00.186-.185V9.006a.186.186 0 00-.186-.186h-2.119a.185.185 0 00-.185.185v1.888c0 .102.083.185.185.185m-2.954-5.43h2.118a.186.186 0 00.186-.186V3.574a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.888c0 .102.082.185.185.185m0 2.716h2.118a.187.187 0 00.186-.186V6.29a.186.186 0 00-.186-.185h-2.118a.185.185 0 00-.185.185v1.887c0 .102.082.186.185.186m-2.93 2.714h2.118a.185.185 0 00.185-.185V9.006a.185.185 0 00-.185-.186h-2.118a.186.186 0 00-.186.185v1.888c0 .102.083.185.186.185m0-2.714h2.118a.186.186 0 00.185-.186V6.29a.186.186 0 00-.185-.185h-2.118a.186.186 0 00-.186.185v1.887c0 .102.083.186.186.186m-2.928 2.714h2.118a.185.185 0 00.185-.185V9.006a.185.185 0 00-.185-.186h-2.118a.186.186 0 00-.186.185v1.888c0 .102.084.185.186.185m0-2.714h2.118a.187.187 0 00.185-.186V6.29a.186.186 0 00-.185-.185h-2.118a.186.186 0 00-.186.185v1.887c0 .102.084.186.186.186m-2.928 2.714h2.118a.185.185 0 00.185-.185V9.006a.185.185 0 00-.185-.186h-2.118a.186.186 0 00-.186.185v1.888c0 .102.083.185.186.185m18.72 1.39a.747.747 0 00-.573-.342h-3.673a.186.186 0 01-.186-.186V7.47a.186.186 0 00-.185-.186h-2.119a.186.186 0 00-.186.186v3.421H.928a.928.928 0 00-.928.928c0 4.962 3.652 9.07 8.35 9.473a11.968 11.968 0 005.803-.437c4.664-1.636 7.427-5.748 7.427-10.468a.74.74 0 00-.4-.68" />
  </svg>
)

const KubernetesIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 13,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M12 1.75a1.3 1.3 0 0 0-.57.13L3.25 5.9a1.3 1.3 0 0 0-.69.88L.53 15.65a1.3 1.3 0 0 0 .25 1.1l5.66 6.95a1.3 1.3 0 0 0 1.01.48h9.1a1.3 1.3 0 0 0 1.01-.48l5.66-6.95a1.3 1.3 0 0 0 .25-1.1l-2.03-8.87a1.3 1.3 0 0 0-.69-.88L12.57 1.88a1.3 1.3 0 0 0-.57-.13zm0 3.5a6.75 6.75 0 1 1 0 13.5 6.75 6.75 0 0 1 0-13.5zm0 2.25a4.5 4.5 0 1 0 0 9 4.5 4.5 0 0 0 0-9z" />
  </svg>
)

const OpenCodeIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 14,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.1"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
  >
    <rect x="3" y="4" width="18" height="16" rx="3.5" />
    <path d="m8 9.5 3 2.5-3 2.5" />
    <path d="M13 14.5h3.5" />
  </svg>
)

const GeminiIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 14,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="currentColor"
    className={className}
    aria-hidden="true"
  >
    <path d="M12 1.75c.62 5.42 4.83 9.63 10.25 10.25-5.42.62-9.63 4.83-10.25 10.25-.62-5.42-4.83-9.63-10.25-10.25C7.17 11.38 11.38 7.17 12 1.75Z" />
  </svg>
)

const AntigravityIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 14,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
  >
    <path d="M12 2.5 4.2 19.5h3.6l1.7-3.8h5l1.7 3.8h3.6L12 2.5Z" fill="currentColor" fillOpacity="0.18" />
    <path d="M12 2.5 4.2 19.5h3.6l1.7-3.8h5l1.7 3.8h3.6L12 2.5Z" />
    <circle cx="12" cy="11.2" r="1.6" fill="currentColor" stroke="none" />
  </svg>
)

const GrokIcon: React.FC<{ size?: number; className?: string }> = ({
  size = 14,
  className = '',
}) => (
  <svg
    width={size}
    height={size}
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2.1"
    strokeLinecap="round"
    strokeLinejoin="round"
    className={className}
    aria-hidden="true"
  >
    <circle cx="12" cy="12" r="7.5" />
    <path d="M5 19 19 5" />
  </svg>
)

export interface RuntimeIconProps {
  runtime: AgentRuntime
  size?: number
  className?: string
}

export const RuntimeIcon: React.FC<RuntimeIconProps> = ({
  runtime,
  size = 14,
  className = '',
}) => {
  switch (runtime) {
    case 'opencode':
      return <OpenCodeIcon size={size} className={`text-teal-600 dark:text-teal-400 shrink-0 ${className}`} />
    case 'claude-code':
      return <AnthropicIcon size={size} className={`text-[#d97757] shrink-0 ${className}`} />
    case 'codex':
      return <OpenAIIcon size={size} className={`text-[#10a37f] shrink-0 ${className}`} />
    case 'antigravity':
      return <AntigravityIcon size={size} className={`text-indigo-500 dark:text-indigo-400 shrink-0 ${className}`} />
    case 'gemini':
      return <GeminiIcon size={size} className={`text-blue-500 dark:text-blue-400 shrink-0 ${className}`} />
    case 'grok-build':
      return <GrokIcon size={size} className={`text-zinc-800 dark:text-zinc-200 shrink-0 ${className}`} />
    default:
      return <OpenCodeIcon size={size} className={`text-zinc-500 shrink-0 ${className}`} />
  }
}

export interface ProviderIconProps {
  provider?: string
  type: BackendType
  size?: number
  className?: string
}

export const ProviderIcon: React.FC<ProviderIconProps> = ({
  provider = '',
  type,
  size = 14,
  className = '',
}) => {
  const p = provider.toLowerCase().trim()
  if (p.startsWith('github')) {
    return (
      <GitHubIcon size={size} className={`text-zinc-700 dark:text-zinc-300 shrink-0 ${className}`} />
    )
  }
  if (p === 'gitlab') {
    return <GitLabIcon size={size} className={`text-orange-500 shrink-0 ${className}`} />
  }
  if (p === 'bitbucket') {
    return <BitbucketIcon size={size} className={`text-blue-600 dark:text-blue-400 shrink-0 ${className}`} />
  }
  if (p === 'anthropic' || p.includes('claude')) {
    return <AnthropicIcon size={size} className={`text-[#d97757] shrink-0 ${className}`} />
  }
  if (p === 'openai') {
    return <OpenAIIcon size={size} className={`text-[#10a37f] shrink-0 ${className}`} />
  }
  if (p === 'antigravity') {
    return <AntigravityIcon size={size} className={`text-indigo-500 dark:text-indigo-400 shrink-0 ${className}`} />
  }
  if (p === 'gemini' || p === 'google') {
    return <GeminiIcon size={size} className={`text-blue-500 dark:text-blue-400 shrink-0 ${className}`} />
  }
  if (p === 'xai' || p === 'grok' || p === 'grok-build') {
    return <GrokIcon size={size} className={`text-zinc-800 dark:text-zinc-200 shrink-0 ${className}`} />
  }
  if (p === 'docker') {
    return <DockerIcon size={size} className={`text-sky-500 shrink-0 ${className}`} />
  }
  if (p === 'kubernetes' || p === 'k8s') {
    return <KubernetesIcon size={size} className={`text-blue-500 shrink-0 ${className}`} />
  }
  if (p === 'litellm') {
    return (
      <Sparkles size={size} className={`text-teal-500 dark:text-teal-400 shrink-0 ${className}`} />
    )
  }
  if (p === 'ollama' || p === 'vllm' || p === 'lmstudio') {
    return <Cpu size={size} className={`text-amber-500 shrink-0 ${className}`} />
  }
  if (type === 'git') {
    return (
      <GitBranch size={size} className={`text-zinc-500 dark:text-zinc-400 shrink-0 ${className}`} />
    )
  }
  if (type === 'inference') {
    return <Cpu size={size} className={`text-zinc-500 dark:text-zinc-400 shrink-0 ${className}`} />
  }
  if (type === 'compute') {
    return (
      <Terminal size={size} className={`text-zinc-500 dark:text-zinc-400 shrink-0 ${className}`} />
    )
  }
  if (type === 'tools' || p.startsWith('mcp')) {
    return (
      <Wrench size={size} className={`text-zinc-500 dark:text-zinc-400 shrink-0 ${className}`} />
    )
  }
  return <Layers size={size} className={`text-zinc-400 dark:text-zinc-500 shrink-0 ${className}`} />
}

export interface AgentProviderIconProps {
  agent: Pick<Agent, 'runtime' | 'model' | 'allowed_backend_ids'>
  backends?: Backend[]
  size?: number
  className?: string
}

/**
 * Resolves the most specific provider/runtime icon for a configured Agent:
 * - Dedicated CLI runtimes (`claude-code`, `antigravity`, `codex`, `gemini`, `grok-build`) use their native icon.
 * - `opencode` agents inspect the agent's model or linked Models backend provider (`anthropic`, `antigravity`, `openai`, `gemini`, `xai`, etc.), falling back to the OpenCode icon.
 */
export const AgentProviderIcon: React.FC<AgentProviderIconProps> = ({
  agent,
  backends = [],
  size = 14,
  className = '',
}) => {
  if (agent.runtime !== 'opencode') {
    return <RuntimeIcon runtime={agent.runtime} size={size} className={className} />
  }

  const modelLower = (agent.model || '').toLowerCase().trim()
  if (modelLower.includes('claude') || modelLower.startsWith('anthropic/')) {
    return <ProviderIcon provider="anthropic" type="inference" size={size} className={className} />
  }
  if (modelLower.includes('antigravity') || modelLower.startsWith('agy')) {
    return <ProviderIcon provider="antigravity" type="inference" size={size} className={className} />
  }
  if (modelLower.includes('gemini') || modelLower.startsWith('google/')) {
    return <ProviderIcon provider="gemini" type="inference" size={size} className={className} />
  }
  if (
    modelLower.startsWith('gpt-') ||
    modelLower.startsWith('o1') ||
    modelLower.startsWith('o3') ||
    modelLower.startsWith('o4') ||
    modelLower.includes('codex') ||
    modelLower.startsWith('openai/')
  ) {
    return <ProviderIcon provider="openai" type="inference" size={size} className={className} />
  }
  if (modelLower.includes('grok') || modelLower.startsWith('xai/')) {
    return <ProviderIcon provider="xai" type="inference" size={size} className={className} />
  }

  const modelBackends = backends.filter((b) => b.type === 'inference' && b.enabled)
  if (modelBackends.length > 0) {
    const byModel =
      modelLower && modelLower !== 'default'
        ? modelBackends.find((b) =>
            getEnabledBackendModels(b).some((m) => m.id.toLowerCase() === modelLower)
          )
        : undefined
    const byAllowed =
      agent.allowed_backend_ids?.length > 0
        ? modelBackends.find((b) => agent.allowed_backend_ids.includes(b.id))
        : undefined
    const matched = byModel || byAllowed || (modelBackends.length === 1 ? modelBackends[0] : undefined)
    if (matched?.provider) {
      return (
        <ProviderIcon
          provider={matched.provider}
          type="inference"
          size={size}
          className={className}
        />
      )
    }
  }

  return <RuntimeIcon runtime={agent.runtime} size={size} className={className} />
}


