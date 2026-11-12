import React, { useEffect, useRef, useState } from 'react'
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Copy,
  Cpu,
  ExternalLink,
  Eye,
  EyeOff,
  GitBranch,
  KeyRound,
  Loader2,
  RefreshCw,
  Sparkles,
  Terminal,
  Wrench,
} from 'lucide-react'
import { ProviderIcon } from '../../common/ProviderIcon'
import { getProviderSdlcStageHandles } from '../../../utils/agents'
import { BACKEND_PROVIDER_DEFINITIONS, getProviderDefinition } from '../../../utils/backends'
import { safeExternalUrl } from '../../../utils/gitUrls'
import type { BackendType } from '../../../types'
import { BackendAccessTab, type BackendAccessTabProps } from './BackendAccessTab'
import { BackendCapacityTab, type BackendCapacityTabProps } from './BackendCapacityTab'

export {
  BackendAccessTab,
  type BackendAccessTabProps,
  BackendCapacityTab,
  type BackendCapacityTabProps,
}

export interface CliAuthSessionState {
  status: string
  auth_url: string | null
  user_code: string | null
  log_output: string
  authenticated: boolean
  error: string | null
}

export interface BackendConfigTabProps {
  isEditing: boolean
  name: string
  setName: (v: string) => void
  type: BackendType
  provider: string
  onProviderChange: (p: string) => void
  endpoint: string
  setEndpoint: (v: string) => void
  secrets: Record<string, string>
  onSecretChange: (key: string, value: string) => void
  existingSecretKeys: string[]
  commandArgs: string
  setCommandArgs: (v: string) => void
  appId: string
  setAppId: (v: string) => void
  installationId: string
  setInstallationId: (v: string) => void
  k8sNamespace?: string
  setK8sNamespace?: (v: string) => void
  k8sSandboxNamespace?: string
  setK8sSandboxNamespace?: (v: string) => void
  k8sNamespaceIsolation?: 'shared' | 'workload' | 'project'
  setK8sNamespaceIsolation?: (v: 'shared' | 'workload' | 'project') => void
  runnerImage?: string
  setRunnerImage?: (v: string) => void
  enabled: boolean
  setEnabled: (v: boolean) => void
  authMode?: 'api_key' | 'cli_oauth'
  onAuthModeChange?: (mode: 'api_key' | 'cli_oauth') => void
  personalModeAcknowledged?: boolean
  onPersonalModeAcknowledgedChange?: (v: boolean) => void
  cliAuthenticated?: boolean
  cliAuthUpdatedAt?: string | null
  cliAuthSession?: CliAuthSessionState | null
  isStartingCliAuth?: boolean
  isSubmittingCliAuthCode?: boolean
  onStartCliAuth?: () => void
  onSubmitCliAuthCode?: (code: string) => void
  onCancelCliAuth?: () => void
  gitAppSetupMode?: 'manifest' | 'manual'
  onGitAppSetupModeChange?: (mode: 'manifest' | 'manual') => void
  githubOrg?: string
  setGithubOrg?: (v: string) => void
  gitAppManifestStatus?: string | null
  gitAppHtmlUrl?: string | null
  isStartingGitAppManifest?: boolean
  isSyncingGitApp?: boolean
  onStartGitAppManifest?: () => void
  onSyncGitApp?: () => void
  autoCreateAgents?: boolean
  setAutoCreateAgents?: (v: boolean) => void
  setSdlcDefault?: boolean
  setSetSdlcDefault?: (v: boolean) => void
}

export const BACKEND_TYPE_OPTIONS: {
  id: BackendType
  label: string
  subtitle: string
  icon: React.ReactNode
}[] = [
    {
      id: 'inference',
      label: 'Inference',
      subtitle: 'LLM proxies & inference APIs',
      icon: <Cpu size={15} />,
    },
    {
      id: 'git',
      label: 'Git',
      subtitle: 'Repositories & pull request hosts',
      icon: <GitBranch size={15} />,
    },
    {
      id: 'compute',
      label: 'Compute',
      subtitle: 'Container & sandbox runners',
      icon: <Terminal size={15} />,
    },
    {
      id: 'tools',
      label: 'Tools',
      subtitle: 'MCP tool servers',
      icon: <Wrench size={15} />,
    },
  ]

function resolveProviderAuthHelpUrl(
  type: BackendType,
  provider: string,
  endpoint: string,
  fallbackUrl?: string
): string | undefined {
  if (type !== 'git') return fallbackUrl
  const raw = endpoint.trim().replace(/\/+$/, '')
  if (provider === 'gitlab') {
    const base = (raw || 'https://gitlab.com').replace(/\/api\/v\d+$/i, '')
    if (/^https?:\/\//i.test(base)) {
      return `${base}/-/user_settings/personal_access_tokens?name=GitSeed+Control+Plane&scopes=api,read_repository,write_repository`
    }
  }
  if (provider === 'github-enterprise') {
    const base = (raw || 'https://github.com').replace(/\/api\/v\d+$/i, '')
    if (/^https?:\/\//i.test(base)) {
      return `${base}/settings/tokens/new?description=GitSeed+Control+Plane&scopes=repo,read:org,workflow`
    }
  }
  if (provider === 'github' && raw && !raw.includes('api.github.com')) {
    const base = raw.replace(/\/api\/v\d+$/i, '')
    if (/^https?:\/\//i.test(base)) {
      return `${base}/settings/tokens/new?description=GitSeed+Control+Plane&scopes=repo,read:org,workflow`
    }
  }
  return fallbackUrl
}

export const BackendConfigTab: React.FC<BackendConfigTabProps> = ({
  isEditing,
  name,
  setName,
  type,
  provider,
  onProviderChange,
  endpoint,
  setEndpoint,
  secrets,
  onSecretChange,
  existingSecretKeys,
  commandArgs,
  setCommandArgs,
  appId,
  setAppId,
  installationId,
  setInstallationId,
  k8sNamespace = 'default',
  setK8sNamespace,
  k8sSandboxNamespace = '',
  setK8sSandboxNamespace,
  k8sNamespaceIsolation = 'workload',
  setK8sNamespaceIsolation,
  runnerImage = '',
  setRunnerImage,
  enabled,
  setEnabled,
  authMode = 'api_key',
  onAuthModeChange,
  personalModeAcknowledged = false,
  onPersonalModeAcknowledgedChange,
  cliAuthenticated = false,
  cliAuthUpdatedAt,
  cliAuthSession,
  isStartingCliAuth = false,
  isSubmittingCliAuthCode = false,
  onStartCliAuth,
  onSubmitCliAuthCode,
  onCancelCliAuth,
  gitAppSetupMode = 'manifest',
  onGitAppSetupModeChange,
  githubOrg = '',
  setGithubOrg,
  gitAppManifestStatus = null,
  gitAppHtmlUrl = null,
  isStartingGitAppManifest = false,
  isSyncingGitApp = false,
  onStartGitAppManifest,
  onSyncGitApp,
  autoCreateAgents = true,
  setAutoCreateAgents,
  setSdlcDefault = false,
  setSetSdlcDefault,
}) => {
  const [visibleSecrets, setVisibleSecrets] = useState<Record<string, boolean>>({})
  const [cliCodeInput, setCliCodeInput] = useState('')
  const [copiedUserCode, setCopiedUserCode] = useState(false)
  const [isProviderMenuOpen, setIsProviderMenuOpen] = useState(false)
  const providerMenuRef = useRef<HTMLDivElement>(null)
  const providerDef = getProviderDefinition(type, provider)
  const supportsCliOAuth =
    (type === 'inference' || type === 'git') && Boolean(providerDef.supportsCliOAuth)
  const isGitHubApp = type === 'git' && provider === 'github-app'
  const effectiveAuthMode = supportsCliOAuth ? authMode : 'api_key'
  const resolvedAuthHelpUrl = safeExternalUrl(
    resolveProviderAuthHelpUrl(
      type,
      provider,
      endpoint,
      providerDef.authHelpUrl
    )
  )
  const safeGitAppInstallUrl = safeExternalUrl(
    gitAppHtmlUrl ? `${gitAppHtmlUrl.replace(/\/$/, '')}/installations/new` : null
  )
  const safeCliAuthUrl = safeExternalUrl(cliAuthSession?.auth_url)
  const providersForType = [...(BACKEND_PROVIDER_DEFINITIONS[type] || [])].sort((a, b) =>
    a.label.localeCompare(b.label, undefined, { sensitivity: 'base' })
  )

  useEffect(() => {
    if (!isProviderMenuOpen) return
    const handleClickOutside = (event: MouseEvent) => {
      if (
        providerMenuRef.current &&
        !providerMenuRef.current.contains(event.target as Node)
      ) {
        setIsProviderMenuOpen(false)
      }
    }
    const handleEscape = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        setIsProviderMenuOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleEscape)
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleEscape)
    }
  }, [isProviderMenuOpen])

  const isLocalDockerSocket =
    type === 'compute' &&
    provider === 'docker' &&
    (!endpoint.trim() || endpoint.trim().toLowerCase().startsWith('unix://')) &&
    existingSecretKeys.length === 0
  const showSecretsSection = providerDef.secrets.length > 0 && !isLocalDockerSocket

  const toggleSecretVisibility = (key: string) => {
    setVisibleSecrets((prev) => ({ ...prev, [key]: !prev[key] }))
  }

  return (
    <div className="flex flex-col gap-5">
      {/* 1. Provider Selection */}
      <div className="flex flex-col gap-1.5" ref={providerMenuRef}>
        <div className="flex items-center justify-between">
          <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
            Provider
          </label>
          {isEditing && (
            <span className="text-xs text-zinc-400 dark:text-zinc-500 font-normal">
              Locked after registration
            </span>
          )}
        </div>
        <div className="relative">
          <button
            type="button"
            disabled={isEditing}
            onClick={() => {
              if (!isEditing) {
                setIsProviderMenuOpen((prev) => !prev)
              }
            }}
            aria-haspopup="listbox"
            aria-expanded={isProviderMenuOpen}
            className={`w-full h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-semibold text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50 flex items-center justify-between gap-2.5 transition ${
              isEditing
                ? 'opacity-65 cursor-not-allowed'
                : 'cursor-pointer hover:border-zinc-300 dark:hover:border-zinc-600'
            }`}
          >
            <span className="flex items-center gap-2.5 min-w-0">
              <ProviderIcon provider={provider} type={type} size={15} />
              <span className="truncate">{providerDef.label}</span>
            </span>
            {!isEditing && (
              <ChevronDown
                size={15}
                className={`text-zinc-400 shrink-0 transition-transform duration-150 ${
                  isProviderMenuOpen ? 'rotate-180' : ''
                }`}
              />
            )}
          </button>

          {isProviderMenuOpen && !isEditing && (
            <div
              role="listbox"
              className="absolute z-40 left-0 right-0 mt-1.5 max-h-72 overflow-y-auto rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-xl py-1.5 flex flex-col gap-0.5"
            >
              {providersForType.map((p) => {
                const isSelected = p.value === provider
                return (
                  <button
                    key={p.value}
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    onClick={() => {
                      onProviderChange(p.value)
                      setIsProviderMenuOpen(false)
                    }}
                    className={`mx-1.5 px-3 py-2 rounded-lg text-xs font-semibold flex items-center justify-between gap-2.5 transition cursor-pointer text-left ${
                      isSelected
                        ? 'bg-teal-500/10 text-teal-700 dark:text-teal-300'
                        : 'text-zinc-800 dark:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800/80'
                    }`}
                  >
                    <span className="flex items-center gap-2.5 min-w-0">
                      <ProviderIcon provider={p.value} type={type} size={15} />
                      <span className="truncate">{p.label}</span>
                    </span>
                    {isSelected && (
                      <Check size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                    )}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </div>

      {/* 3. Backend Name */}
      <div className="flex flex-col gap-1.5">
        <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
          Backend Name *
        </label>
        <input
          type="text"
          required
          maxLength={120}
          placeholder={`e.g. ${providerDef.label}`}
          value={name}
          onChange={(e) => setName(e.target.value)}
          className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-medium text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
        />
      </div>

      {/* 4. Endpoint / Command (not applicable when using Model CLI Subscription Login) */}
      {(effectiveAuthMode !== 'cli_oauth' || type === 'git') && (
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
            {providerDef.endpointLabel}
          </label>
          <input
            type="text"
            placeholder={providerDef.endpointPlaceholder}
            value={endpoint}
            onChange={(e) => setEndpoint(e.target.value)}
            className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
      )}

      {/* Provider-specific config fields */}
      {isGitHubApp && gitAppSetupMode === 'manual' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
              GitHub App ID
            </label>
            <input
              type="text"
              placeholder="e.g. 982104"
              value={appId}
              onChange={(e) => setAppId(e.target.value)}
              className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
            />
          </div>
          <div className="flex flex-col gap-1.5">
            <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
              Installation ID <span className="font-normal text-zinc-400">(auto-detected if blank)</span>
            </label>
            <input
              type="text"
              placeholder="e.g. 51204918"
              value={installationId}
              onChange={(e) => setInstallationId(e.target.value)}
              className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
            />
          </div>
        </div>
      )}

      {type === 'tools' && provider === 'mcp-stdio' && (
        <div className="flex flex-col gap-1.5">
          <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
            Command Arguments (optional)
          </label>
          <input
            type="text"
            placeholder="e.g. /workspace --read-only"
            value={commandArgs}
            onChange={(e) => setCommandArgs(e.target.value)}
            className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
          />
        </div>
      )}

      {type === 'compute' && (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {provider === 'kubernetes' && setK8sNamespace && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                Kubernetes Namespace
              </label>
              <input
                type="text"
                placeholder="default"
                value={k8sNamespace}
                onChange={(e) => setK8sNamespace(e.target.value)}
                className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
              />
            </div>
          )}
          {provider === 'kubernetes' && setK8sSandboxNamespace && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                Sandbox Namespace (optional)
              </label>
              <input
                type="text"
                placeholder={`${k8sNamespace.trim() || 'default'}-sandboxes`}
                value={k8sSandboxNamespace}
                onChange={(e) => setK8sSandboxNamespace(e.target.value)}
                className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
              />
            </div>
          )}
          {provider === 'kubernetes' && setK8sNamespaceIsolation && (
            <div className="flex flex-col gap-1.5">
              <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                Namespace Isolation
              </label>
              <select
                value={k8sNamespaceIsolation}
                onChange={(e) =>
                  setK8sNamespaceIsolation(
                    e.target.value as 'shared' | 'workload' | 'project'
                  )
                }
                className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-xs font-medium text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
              >
                <option value="workload">Separate SDLC Stages &amp; Sandboxes</option>
                <option value="project">Isolate Per Project (Stages &amp; Sandboxes)</option>
                <option value="shared">Single Shared Namespace</option>
              </select>
            </div>
          )}
          {setRunnerImage && (
            <div
              className={`flex flex-col gap-1.5 ${provider === 'kubernetes' && setK8sNamespaceIsolation ? '' : 'sm:col-span-2'
                }`}
            >
              <label className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                Runner Image Override (optional)
              </label>
              <input
                type="text"
                placeholder="gitseed/runner:dev"
                value={runnerImage}
                onChange={(e) => setRunnerImage(e.target.value)}
                className="h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl px-3.5 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
              />
            </div>
          )}
        </div>
      )}

      {/* Authentication / Encrypted Secrets */}
      {showSecretsSection && (
        <div className="flex flex-col gap-3.5 pt-3 border-t border-zinc-200 dark:border-zinc-700/80">
          <div className="flex items-center justify-between">
            <div className="flex items-center gap-1.5">
              <KeyRound size={14} className="text-teal-600 dark:text-teal-400" />
              <span className="text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                Credentials & Secrets
              </span>
            </div>
            <span className="text-xs text-zinc-400 dark:text-zinc-500">
              Encrypted at rest (AES-GCM)
            </span>
          </div>

          {isGitHubApp && onGitAppSetupModeChange && (
            <div className="grid grid-cols-2 gap-1.5 p-1 rounded-xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80">
              <button
                type="button"
                onClick={() => onGitAppSetupModeChange('manifest')}
                className={`py-2 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  gitAppSetupMode === 'manifest'
                    ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-xs border border-zinc-200/80 dark:border-zinc-700'
                    : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                <Sparkles size={13} className="text-teal-600 dark:text-teal-400" />
                <span>1-Click GitHub Setup</span>
              </button>
              <button
                type="button"
                onClick={() => onGitAppSetupModeChange('manual')}
                className={`py-2 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  gitAppSetupMode === 'manual'
                    ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-xs border border-zinc-200/80 dark:border-zinc-700'
                    : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                <KeyRound size={13} />
                <span>Manual App ID & PEM</span>
              </button>
            </div>
          )}

          {isGitHubApp && gitAppSetupMode === 'manifest' ? (
            <div className="p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 flex flex-col gap-3 text-xs">
              <div className="flex items-start justify-between gap-2">
                <div className="flex flex-col gap-1">
                  <span className="font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                    <Sparkles size={13} className="text-teal-600 dark:text-teal-400" />
                    <span>Create & Connect GitHub App (Manifest Flow)</span>
                  </span>
                  <p className="text-zinc-600 dark:text-zinc-300 leading-relaxed">
                    Registers a dedicated GitHub App pre-configured with exact repository & pull request permissions, then automatically saves its App ID, private key (.pem), webhook secret, and installation ID back into GitSeed.
                  </p>
                </div>
                {appId && existingSecretKeys.includes('private_key') && installationId && (
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 shrink-0">
                    <Check size={12} />
                    <span>Connected</span>
                  </span>
                )}
              </div>

              {setGithubOrg && (
                <div className="flex flex-col gap-1">
                  <label className="text-[11px] font-semibold text-zinc-700 dark:text-zinc-300">
                    GitHub Organization (optional, leave blank for personal account)
                  </label>
                  <input
                    type="text"
                    placeholder="e.g. my-org"
                    value={githubOrg}
                    onChange={(e) => setGithubOrg(e.target.value)}
                    className="h-9 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-lg px-3 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
                  />
                </div>
              )}

              {(appId || gitAppManifestStatus) && (
                <div className="p-2.5 rounded-lg bg-white dark:bg-zinc-900/90 border border-zinc-200/80 dark:border-zinc-700/80 flex flex-wrap items-center justify-between gap-2">
                  <div className="flex items-center gap-2 flex-wrap font-mono text-[11px]">
                    {appId ? (
                      <span className="text-zinc-700 dark:text-zinc-200">
                        App ID: <strong>{appId}</strong>
                      </span>
                    ) : (
                      <span className="text-zinc-500 inline-flex items-center gap-1.5">
                        <Loader2 size={12} className="animate-spin text-teal-500" />
                        Waiting for GitHub App creation in browser…
                      </span>
                    )}
                    {appId && (
                      <>
                        <span className="text-zinc-300 dark:text-zinc-700">·</span>
                        {installationId ? (
                          <span className="text-emerald-600 dark:text-emerald-400">
                            Installation: <strong>{installationId}</strong>
                          </span>
                        ) : (
                          <span className="text-amber-600 dark:text-amber-400 inline-flex items-center gap-1.5">
                            <Loader2 size={11} className="animate-spin" />
                            Waiting for App installation on GitHub…
                          </span>
                        )}
                      </>
                    )}
                  </div>
                  {appId && !installationId && safeGitAppInstallUrl && (
                    <a
                      href={safeGitAppInstallUrl}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="inline-flex items-center gap-1 text-[11px] font-semibold text-teal-600 dark:text-teal-400 hover:underline"
                    >
                      <span>Install App on GitHub</span>
                      <ExternalLink size={11} />
                    </a>
                  )}
                </div>
              )}

              <div className="flex items-center gap-2 flex-wrap">
                <button
                  type="button"
                  onClick={onStartGitAppManifest}
                  disabled={isStartingGitAppManifest}
                  className="px-3.5 py-2 rounded-xl bg-teal-600 hover:bg-teal-500 text-white font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors disabled:opacity-50"
                >
                  {isStartingGitAppManifest ? (
                    <>
                      <Loader2 size={13} className="animate-spin" />
                      <span>Opening GitHub…</span>
                    </>
                  ) : (
                    <>
                      <ExternalLink size={13} />
                      <span>
                        {appId && existingSecretKeys.includes('private_key')
                          ? 'Register New GitHub App'
                          : 'Create & Connect on GitHub'}
                      </span>
                    </>
                  )}
                </button>

                {appId && existingSecretKeys.includes('private_key') && onSyncGitApp && (
                  <button
                    type="button"
                    onClick={onSyncGitApp}
                    disabled={isSyncingGitApp}
                    className="px-3 py-2 rounded-xl bg-zinc-200/80 dark:bg-zinc-700/80 hover:bg-zinc-300 dark:hover:bg-zinc-700 text-zinc-800 dark:text-zinc-200 font-semibold text-xs inline-flex items-center gap-1.5 cursor-pointer transition-colors disabled:opacity-50"
                  >
                    <RefreshCw size={12} className={isSyncingGitApp ? 'animate-spin' : ''} />
                    <span>{installationId ? 'Re-sync Installation & Repos' : 'Check Installation'}</span>
                  </button>
                )}
              </div>
            </div>
          ) : null}

          {supportsCliOAuth && onAuthModeChange && (
            <div className="grid grid-cols-2 gap-1.5 p-1 rounded-xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80">
              <button
                type="button"
                onClick={() => onAuthModeChange('api_key')}
                className={`py-2 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  effectiveAuthMode === 'api_key'
                    ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-xs border border-zinc-200/80 dark:border-zinc-700'
                    : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                <KeyRound size={13} />
                <span>{type === 'git' ? 'Personal Access Token' : 'API Key (Pay-As-You-Go)'}</span>
              </button>
              <button
                type="button"
                onClick={() => onAuthModeChange('cli_oauth')}
                className={`py-2 px-3 rounded-lg text-xs font-semibold transition-all cursor-pointer flex items-center justify-center gap-1.5 ${
                  effectiveAuthMode === 'cli_oauth'
                    ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-xs border border-zinc-200/80 dark:border-zinc-700'
                    : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                {type === 'git' ? <Sparkles size={13} /> : <Terminal size={13} />}
                <span>{type === 'git' ? 'Browser Sign-In (OAuth)' : 'CLI Subscription Login'}</span>
              </button>
            </div>
          )}

          {supportsCliOAuth && type !== 'git' && effectiveAuthMode === 'cli_oauth' && (
            <div
              role="note"
              data-testid="subscription-terms-warning"
              className="p-3 rounded-xl bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 flex gap-2 text-xs text-amber-900 dark:text-amber-200 leading-relaxed"
            >
              <AlertTriangle size={14} className="shrink-0 mt-0.5" />
              <div className="flex flex-col gap-2">
                <div className="flex items-center justify-between gap-2 flex-wrap">
                  <p className="font-semibold">Consumer subscriptions are for one person</p>
                  <span className="px-2 py-0.5 rounded-full text-[10px] font-semibold uppercase tracking-wide bg-amber-500/20 text-amber-900 dark:text-amber-200 border border-amber-500/30">
                    Personal Mode (Single-User Subscription)
                  </span>
                </div>
                <p>
                  Plans like Claude Pro/Max or ChatGPT Plus/Pro are usually licensed to one individual.
                  Sharing one login across a team, or using it for always-on automation, may break the
                  provider&apos;s terms. Connect only your own subscription for your own work. For team or
                  scheduled use, choose <span className="font-semibold">API Key (Pay-As-You-Go)</span>{' '}
                  instead. Spend in Personal Mode is <span className="font-semibold">Estimated</span> from CLI token reports rather than hard-capped by a LiteLLM virtual key.
                </p>
                <label className="mt-1 inline-flex items-start gap-2 cursor-pointer select-none font-medium text-amber-950 dark:text-amber-100">
                  <input
                    type="checkbox"
                    data-testid="personal-mode-acknowledged-checkbox"
                    checked={personalModeAcknowledged}
                    onChange={(e) => onPersonalModeAcknowledgedChange?.(e.target.checked)}
                    className="mt-0.5 rounded border-amber-400 text-teal-600 focus:ring-teal-500"
                  />
                  <span>
                    I acknowledge that this CLI OAuth backend runs in Personal Mode (single-user subscription, bound to my user account, with estimated spend).
                  </span>
                </label>
              </div>
            </div>
          )}

          {isGitHubApp && gitAppSetupMode === 'manifest' ? null : effectiveAuthMode === 'api_key' ? (
            <>
              {providerDef.authHelpText && (
                <div className="p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 flex flex-col gap-2 text-xs text-zinc-600 dark:text-zinc-300 leading-relaxed">
                  <p>{providerDef.authHelpText}</p>
                  {resolvedAuthHelpUrl && (
                    <div>
                      <a
                        href={resolvedAuthHelpUrl}
                        target="_blank"
                        rel="noopener noreferrer"
                        className={
                          type === 'git'
                            ? 'inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-600 hover:bg-teal-500 text-white font-semibold text-xs transition-colors'
                            : 'inline-flex items-center gap-1 font-semibold text-teal-600 dark:text-teal-400 hover:underline'
                        }
                      >
                        <span>{providerDef.authHelpLabel || 'Open Provider Console'}</span>
                        <ExternalLink size={12} />
                      </a>
                    </div>
                  )}
                </div>
              )}

              {providerDef.secrets.map((sec) => {
                const isConfigured = existingSecretKeys.includes(sec.key)
                const isRequired = sec.required && !isConfigured
                const showValue = Boolean(visibleSecrets[sec.key])
                const currentVal = secrets[sec.key] || ''

                return (
                  <div key={sec.key} className="flex flex-col gap-1.5">
                    <div className="flex items-center justify-between">
                      <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                        <span>{sec.label}</span>
                        <span className="font-mono text-xs text-zinc-400">({sec.key})</span>
                      </label>
                      {isConfigured ? (
                        <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                          <Check size={12} />
                          <span>Configured · enter new value to rotate</span>
                        </span>
                      ) : sec.required ? (
                        <span className="text-xs font-semibold text-amber-600 dark:text-amber-400">
                          Required
                        </span>
                      ) : null}
                    </div>
                    <div className="relative">
                      <input
                        type={showValue ? 'text' : 'password'}
                        required={isRequired}
                        placeholder={
                          isConfigured
                            ? '•••••••••••••••• (leave blank to keep current secret)'
                            : sec.placeholder
                        }
                        value={currentVal}
                        onChange={(e) => onSecretChange(sec.key, e.target.value)}
                        className="w-full h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-3.5 pr-9 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
                      />
                      <button
                        type="button"
                        onClick={() => toggleSecretVisibility(sec.key)}
                        aria-label={showValue ? 'Hide secret' : 'Show secret'}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors cursor-pointer"
                      >
                        {showValue ? <EyeOff size={15} /> : <Eye size={15} />}
                      </button>
                    </div>
                  </div>
                )
              })}
            </>
          ) : (
            <div className="flex flex-col gap-3">
              <div className="p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/70 flex flex-col gap-3 text-xs">
                <div className="flex items-start justify-between gap-2">
                  <div className="flex flex-col gap-1">
                    <span className="font-semibold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                      {type === 'git' ? (
                        <Sparkles size={13} className="text-teal-600 dark:text-teal-400" />
                      ) : (
                        <Terminal size={13} className="text-teal-600 dark:text-teal-400" />
                      )}
                      <span>
                        {type === 'git'
                          ? 'Browser Device Sign-In (GitHub OAuth)'
                          : `In-Container CLI Login (${providerDef.cliBinary || provider})`}
                      </span>
                    </span>
                    <p className="text-zinc-600 dark:text-zinc-300 leading-relaxed">
                      {providerDef.cliAuthHelpText ||
                        'Authenticate inside an isolated runner container without requiring any CLI tools on your host machine.'}
                    </p>
                  </div>
                  {cliAuthenticated && (
                    <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-full text-[11px] font-semibold bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border border-emerald-500/30 shrink-0">
                      <Check size={12} />
                      <span>Authenticated</span>
                    </span>
                  )}
                </div>

                {cliAuthenticated && cliAuthUpdatedAt && (
                  <p className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono">
                    Last authenticated: {new Date(cliAuthUpdatedAt).toLocaleString()}
                  </p>
                )}

                <div className="flex items-center gap-2 flex-wrap">
                  <button
                    type="button"
                    onClick={onStartCliAuth}
                    disabled={isStartingCliAuth || (type !== 'git' && !personalModeAcknowledged)}
                    className="px-3.5 py-2 rounded-xl bg-teal-600 hover:bg-teal-500 text-white font-semibold text-xs flex items-center gap-1.5 cursor-pointer transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {isStartingCliAuth ? (
                      <>
                        <Loader2 size={13} className="animate-spin" />
                        <span>
                          {type === 'git' ? 'Requesting Device Code...' : 'Starting CLI Container...'}
                        </span>
                      </>
                    ) : (
                      <>
                        {type === 'git' ? <ExternalLink size={13} /> : <Terminal size={13} />}
                        <span>
                          {type === 'git'
                            ? cliAuthenticated
                              ? 'Re-authenticate with GitHub'
                              : 'Sign in with GitHub in Browser'
                            : cliAuthenticated
                              ? `Re-authenticate (${providerDef.cliBinary === 'agy' ? 'agy' : `${providerDef.cliBinary || provider} login`})`
                              : `Launch CLI Sign-In (${providerDef.cliBinary === 'agy' ? 'agy' : `${providerDef.cliBinary || provider} login`})`}
                        </span>
                      </>
                    )}
                  </button>
                  {cliAuthSession &&
                    cliAuthSession.status === 'awaiting_user' &&
                    onCancelCliAuth && (
                      <button
                        type="button"
                        onClick={onCancelCliAuth}
                        className="px-3 py-2 rounded-xl bg-zinc-200/80 dark:bg-zinc-700/80 hover:bg-zinc-300 dark:hover:bg-zinc-700 text-zinc-700 dark:text-zinc-200 font-semibold text-xs cursor-pointer transition-colors"
                      >
                        Cancel Session
                      </button>
                    )}
                </div>

                {type !== 'git' && !personalModeAcknowledged && (
                  <p className="text-[11px] font-medium text-amber-700 dark:text-amber-300">
                    Check the Personal Mode acknowledgment above to launch sign-in
                  </p>
                )}

                {cliAuthSession && cliAuthSession.status !== 'idle' && (
                  <div className="flex flex-col gap-2.5 pt-2 border-t border-zinc-200 dark:border-zinc-700/80">
                    {safeCliAuthUrl && (
                      <div className="p-2.5 rounded-lg bg-teal-500/10 border border-teal-500/25 flex flex-col gap-1.5">
                        <span className="font-semibold text-teal-800 dark:text-teal-200">
                          Step 1: Authorize in your browser:
                        </span>
                        <div className="flex items-center gap-2 flex-wrap">
                          <a
                            href={safeCliAuthUrl}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-teal-600 hover:bg-teal-500 text-white font-semibold text-xs"
                          >
                            <span>Open Sign-In URL</span>
                            <ExternalLink size={12} />
                          </a>
                          {cliAuthSession.user_code && (
                            <button
                              type="button"
                              onClick={() => {
                                navigator.clipboard.writeText(cliAuthSession.user_code || '')
                                setCopiedUserCode(true)
                                setTimeout(() => setCopiedUserCode(false), 2000)
                              }}
                              className="inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-300 dark:border-zinc-700 font-mono font-bold text-xs text-zinc-900 dark:text-zinc-100 cursor-pointer"
                            >
                              <span>Code: {cliAuthSession.user_code}</span>
                              {copiedUserCode ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                            </button>
                          )}
                        </div>
                      </div>
                    )}

                    {cliAuthSession.log_output && (
                      <div className="flex flex-col gap-1">
                        <span className="text-[11px] font-semibold text-zinc-500 dark:text-zinc-400">
                          Container CLI Output:
                        </span>
                        <pre className="p-2.5 rounded-lg bg-zinc-900 text-zinc-100 font-mono text-[11px] max-h-36 overflow-y-auto whitespace-pre-wrap break-all leading-relaxed">
                          {cliAuthSession.log_output}
                        </pre>
                      </div>
                    )}

                    {!cliAuthSession.authenticated && type !== 'git' && (
                      <div className="flex flex-col gap-1.5">
                        <label className="font-semibold text-zinc-700 dark:text-zinc-300">
                          Step 2: Paste Authorization Code, Callback URL, or Setup Token:
                        </label>
                        <div className="flex items-center gap-2">
                          <input
                            type="text"
                            placeholder="Paste code, http://localhost:... callback URL, or token"
                            value={cliCodeInput}
                            onChange={(e) => setCliCodeInput(e.target.value)}
                            className="flex-1 h-9 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 rounded-lg px-3 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
                          />
                          <button
                            type="button"
                            disabled={!cliCodeInput.trim() || isSubmittingCliAuthCode}
                            onClick={() => {
                              if (onSubmitCliAuthCode && cliCodeInput.trim()) {
                                onSubmitCliAuthCode(cliCodeInput.trim())
                                setCliCodeInput('')
                              }
                            }}
                            className="h-9 px-3.5 rounded-lg bg-zinc-900 dark:bg-zinc-100 text-white dark:text-zinc-900 font-semibold text-xs cursor-pointer disabled:opacity-50 shrink-0"
                          >
                            {isSubmittingCliAuthCode ? 'Verifying...' : 'Submit Code'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                )}
              </div>

              {/* Direct OAuth Token / Bundle fallback field */}
              {type !== 'git' && (
                <div className="flex flex-col gap-1.5">
                  <div className="flex items-center justify-between">
                    <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 flex items-center gap-1.5">
                      <span>Direct OAuth Token / Credential Bundle (optional)</span>
                      <span className="font-mono text-xs text-zinc-400">(oauth_token)</span>
                    </label>
                    {existingSecretKeys.includes('oauth_token') ||
                    existingSecretKeys.includes('cli_auth_bundle') ? (
                      <span className="inline-flex items-center gap-1 text-xs font-semibold text-emerald-600 dark:text-emerald-400">
                        <Check size={12} />
                        <span>Stored · enter new value to rotate</span>
                      </span>
                    ) : null}
                  </div>
                  <div className="relative">
                    <input
                      type={visibleSecrets.oauth_token ? 'text' : 'password'}
                      placeholder={
                        existingSecretKeys.includes('oauth_token') ||
                        existingSecretKeys.includes('cli_auth_bundle')
                          ? '•••••••••••••••• (stored in encrypted vault)'
                          : provider === 'anthropic'
                            ? 'sk-ant-oat01-... (from "claude setup-token") or leave blank when using Launch CLI Sign-In'
                            : 'Paste OAuth token or JSON credential bundle (optional)'
                      }
                      value={secrets.oauth_token || ''}
                      onChange={(e) => onSecretChange('oauth_token', e.target.value)}
                      className="w-full h-10 bg-zinc-100 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-3.5 pr-9 text-sm font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/50"
                    />
                    <button
                      type="button"
                      onClick={() => toggleSecretVisibility('oauth_token')}
                      aria-label={visibleSecrets.oauth_token ? 'Hide token' : 'Show token'}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 transition-colors cursor-pointer"
                    >
                      {visibleSecrets.oauth_token ? <EyeOff size={15} /> : <Eye size={15} />}
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}
        </div>
      )}

      {/* SDLC Stage Agents Auto-Provisioning (Inference Backends) */}
      {type === 'inference' && setAutoCreateAgents && (
        <div className="p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-3">
          <label className="flex items-start gap-2.5 cursor-pointer select-none">
            <input
              type="checkbox"
              checked={autoCreateAgents}
              onChange={(e) => {
                const next = e.target.checked
                setAutoCreateAgents(next)
                if (!next && setSetSdlcDefault) {
                  setSetSdlcDefault(false)
                }
              }}
              className="mt-0.5 w-4 h-4 rounded border-zinc-300 dark:border-zinc-600 text-teal-600 focus:ring-teal-500/50 cursor-pointer accent-teal-600"
            />
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-1.5 flex-wrap">
                <Sparkles size={13} className="text-teal-600 dark:text-teal-400 shrink-0" />
                <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                  Auto-create stage agents
                </span>
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
                Automatically provisions the 5 stage agents for this provider so you can immediately launch interactive sessions with them or assign them in Policies.
              </p>
              <div className="flex items-center gap-1.5 flex-wrap mt-2">
                {getProviderSdlcStageHandles(provider).map((st) => (
                  <span
                    key={st.stage}
                    className="px-2 py-0.5 rounded-md bg-white dark:bg-zinc-900 border border-zinc-200/80 dark:border-zinc-700/80 text-[11px] font-mono text-teal-700 dark:text-teal-300"
                    title={`${st.stageLabel} stage agent`}
                  >
                    {st.handle}
                  </span>
                ))}
              </div>
            </div>
          </label>

          {(autoCreateAgents || isEditing || setSdlcDefault) && setSetSdlcDefault && (
            <label className="flex items-start gap-2.5 pt-2.5 border-t border-zinc-200/70 dark:border-zinc-700/60 cursor-pointer select-none">
              <input
                type="checkbox"
                checked={setSdlcDefault}
                onChange={(e) => {
                  const next = e.target.checked
                  setSetSdlcDefault(next)
                  if (next && !autoCreateAgents && setAutoCreateAgents) {
                    setAutoCreateAgents(true)
                  }
                }}
                className="mt-0.5 w-4 h-4 rounded border-zinc-300 dark:border-zinc-600 text-teal-600 focus:ring-teal-500/50 cursor-pointer accent-teal-600"
              />
              <div className="flex-1 min-w-0">
                <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100">
                  Set as default SDLC pipeline provider
                </span>
                <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 leading-relaxed">
                  Routes the 5 organization SDLC stage policies (Ideate → Merge) and <code className="font-mono">@coordinator</code> to this provider&apos;s stage agents by default.
                </p>
              </div>
            </label>
          )}
        </div>
      )}

      {/* Status Toggle */}
      <div className="flex items-center justify-between p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 mt-1">
        <div>
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            Backend Enabled
          </span>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
            Allow agents and projects to route work through this backend
          </p>
        </div>
        <button
          type="button"
          role="switch"
          aria-checked={enabled}
          onClick={() => setEnabled(!enabled)}
          className={`w-10 h-6 flex items-center rounded-full p-1 cursor-pointer transition-colors ${enabled ? 'bg-teal-500' : 'bg-zinc-300 dark:bg-zinc-700'
            }`}
        >
          <div
            className={`bg-white w-4 h-4 rounded-full shadow-md transform transition-transform ${enabled ? 'translate-x-4' : 'translate-x-0'
              }`}
          />
        </button>
      </div>
    </div>
  )
}

