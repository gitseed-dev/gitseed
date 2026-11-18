import React, { useEffect, useMemo, useRef, useState } from 'react'
import { AlertCircle, Check, Copy, Radio, RefreshCw, Trash2, X } from 'lucide-react'
import { Button, Chip } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { ProviderIcon } from '../common/ProviderIcon'
import {
  BACKEND_TYPE_OPTIONS,
  BackendAccessTab,
  BackendCapacityTab,
  BackendConfigTab,
  type CliAuthSessionState,
} from './backend/BackendFormSections'
import { BackendModelsTab } from './backend/BackendModelsTab'
import { BackendRepositoriesTab } from './backend/BackendRepositoriesTab'
import { VIEWS } from '../../constants'
import { useData } from '../../context/DataContext'
import {
  BACKEND_PROVIDER_DEFINITIONS,
  DEFAULT_CLOUD_MODELS,
  getBackendModels,
  getBackendRepositories,
  getProjectsUsingBackend,
  getProviderDefinition,
  type GitBackendRepoEntry,
  type ModelBackendEntry,
} from '../../utils/backends'
import { safeExternalUrl } from '../../utils/gitUrls'
import type { Backend, BackendType } from '../../types'

export interface BackendDrawerProps {
  isOpen: boolean
  onClose: () => void
  backendToEdit?: Backend | null
  defaultType?: BackendType
  defaultProvider?: string
  defaultName?: string
  onDelete?: (backend: Backend) => void
  onSaved?: (backend: Backend) => void
}

export const BackendDrawer: React.FC<BackendDrawerProps> = ({
  isOpen,
  onClose,
  backendToEdit,
  defaultType,
  defaultProvider,
  defaultName,
  onDelete,
  onSaved,
}) => {
  const { backends, agents, projects, projectReposByProject, navigateToDetail } = useData()
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [type, setType] = useState<BackendType>('inference')
  const [provider, setProvider] = useState('anthropic')
  const [endpoint, setEndpoint] = useState('')
  const [secrets, setSecrets] = useState<Record<string, string>>({})
  const [capacity, setCapacity] = useState<number | null>(4)
  const [commandArgs, setCommandArgs] = useState('')
  const [modelEntries, setModelEntries] = useState<ModelBackendEntry[]>([])
  const [isSyncingModels, setIsSyncingModels] = useState(false)
  const [appId, setAppId] = useState('')
  const [installationId, setInstallationId] = useState('')
  const [k8sNamespace, setK8sNamespace] = useState('default')
  const [k8sSandboxNamespace, setK8sSandboxNamespace] = useState('')
  const [k8sNamespaceIsolation, setK8sNamespaceIsolation] = useState<
    'shared' | 'workload' | 'project'
  >('workload')
  const [runnerImage, setRunnerImage] = useState('')
  const [gitRepositories, setGitRepositories] = useState<GitBackendRepoEntry[]>([])
  const [isSyncingRepos, setIsSyncingRepos] = useState(false)
  const [accessRestricted, setAccessRestricted] = useState(false)
  const [selectedAgentIds, setSelectedAgentIds] = useState<string[]>([])
  const [enabled, setEnabled] = useState(true)
  const [authMode, setAuthMode] = useState<'api_key' | 'cli_oauth'>('api_key')
  const [personalModeAcknowledged, setPersonalModeAcknowledged] = useState(false)
  const [autoCreateAgents, setAutoCreateAgents] = useState(true)
  const [setSdlcDefault, setSetSdlcDefault] = useState(false)
  const [localBackend, setLocalBackend] = useState<Backend | null>(null)
  const [cliAuthSession, setCliAuthSession] = useState<CliAuthSessionState | null>(null)
  const [isStartingCliAuth, setIsStartingCliAuth] = useState(false)
  const [isSubmittingCliAuthCode, setIsSubmittingCliAuthCode] = useState(false)
  const [gitAppSetupMode, setGitAppSetupMode] = useState<'manifest' | 'manual'>('manifest')
  const [githubOrg, setGithubOrg] = useState('')
  const [isStartingGitAppManifest, setIsStartingGitAppManifest] = useState(false)
  const [isSyncingGitApp, setIsSyncingGitApp] = useState(false)

  const currentBackend = localBackend || backendToEdit || null

  const [activeTab, setActiveTab] = useState<
    'config' | 'repositories' | 'models' | 'access' | 'capacity'
  >('config')
  const [isSaving, setIsSaving] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState(false)

  // Live connectivity probe state
  const [isPinging, setIsPinging] = useState(false)
  const [pingResult, setPingResult] = useState<{
    status: 'healthy' | 'degraded' | 'unreachable'
    latencyMs: number | null
    error: string | null
  } | null>(null)

  const initializedSessionRef = useRef<string | null>(null)

  useEffect(() => {
    if (!isOpen) {
      initializedSessionRef.current = null
      return
    }
    const sessionKey = `${backendToEdit?.id || 'new'}:${defaultType || ''}:${defaultProvider || ''}`
    if (initializedSessionRef.current === sessionKey) {
      return
    }
    initializedSessionRef.current = sessionKey

    setErrorMessage(null)
    setPingResult(null)
    setActiveTab('config')
    setSecrets({})
    setLocalBackend(null)
    setCliAuthSession(null)
    setIsStartingCliAuth(false)
    setIsSubmittingCliAuthCode(false)
    setIsStartingGitAppManifest(false)
    setIsSyncingGitApp(false)

    if (backendToEdit) {
      setName(backendToEdit.name)
      setType(backendToEdit.type)
      setProvider(backendToEdit.provider)
      setEndpoint(backendToEdit.endpoint || '')
      setCapacity(
        backendToEdit.capacity ??
        (backendToEdit.type === 'compute' ? 2 : backendToEdit.type === 'inference' ? 4 : null)
      )
      const cfg = backendToEdit.config || {}
      const savedAuthMode =
        cfg.auth_mode === 'cli_oauth' ||
        cfg.auth_mode === 'device_oauth' ||
        backendToEdit.secret_keys.includes('cli_auth_bundle') ||
        backendToEdit.secret_keys.includes('oauth_token')
          ? 'cli_oauth'
          : 'api_key'
      setAuthMode(savedAuthMode)
      setPersonalModeAcknowledged(
        cfg.personal_mode_acknowledged === undefined
          ? savedAuthMode === 'cli_oauth'
          : Boolean(cfg.personal_mode_acknowledged)
      )
      setCommandArgs(typeof cfg.command_args === 'string' ? cfg.command_args : '')
      setModelEntries(getBackendModels(backendToEdit))
      setAppId(typeof cfg.app_id === 'string' ? cfg.app_id : '')
      setInstallationId(typeof cfg.installation_id === 'string' ? cfg.installation_id : '')
      setGithubOrg(typeof cfg.github_org === 'string' ? cfg.github_org : '')
      setGitAppSetupMode(
        cfg.manifest_status || cfg.auth_mode === 'manifest' ? 'manifest' : 'manual'
      )
      setK8sNamespace(typeof cfg.namespace === 'string' ? cfg.namespace : 'default')
      setK8sSandboxNamespace(
        typeof cfg.sandbox_namespace === 'string' ? cfg.sandbox_namespace : ''
      )
      setK8sNamespaceIsolation(
        cfg.namespace_isolation === 'shared' ||
          cfg.namespace_isolation === 'workload' ||
          cfg.namespace_isolation === 'project'
          ? cfg.namespace_isolation
          : 'workload'
      )
      setRunnerImage(typeof cfg.runner_image === 'string' ? cfg.runner_image : '')
      setGitRepositories(getBackendRepositories(backendToEdit))
      setAccessRestricted(backendToEdit.access_restricted)
      setSelectedAgentIds([...backendToEdit.allowed_agent_ids])
      setEnabled(backendToEdit.enabled)
      setAutoCreateAgents(cfg.auto_create_agents !== false)
      setSetSdlcDefault(Boolean(cfg.is_sdlc_default))
    } else {
      let urlProvider: string | null = null
      let urlType: BackendType | null = null
      try {
        const sp = new URLSearchParams(window.location.search)
        urlProvider = sp.get('provider')
        const rawType = sp.get('type')
        if (rawType === 'models') {
          urlType = 'inference'
        } else if (rawType === 'inference' || rawType === 'git' || rawType === 'compute' || rawType === 'tools') {
          urlType = rawType
        }
      } catch {
        // Ignore
      }
      const initialType: BackendType = urlType || defaultType || 'inference'
      const providersForType = BACKEND_PROVIDER_DEFINITIONS[initialType] || []
      const preferredProviderValue =
        defaultProvider?.toLowerCase() ||
        (initialType === 'git' ? 'github-app' : undefined)
      const matchedProvider =
        (urlProvider && providersForType.find((p) => p.value === urlProvider.toLowerCase())) ||
        (preferredProviderValue && providersForType.find((p) => p.value === preferredProviderValue)) ||
        providersForType[0]
      const initialProvider = matchedProvider?.value || 'anthropic'
      const initialCloudModels = DEFAULT_CLOUD_MODELS[initialProvider] || []
      setName(defaultName || '')
      setType(initialType)
      setProvider(initialProvider)
      setEndpoint(matchedProvider?.defaultEndpoint || '')
      setCapacity(initialType === 'compute' ? 2 : initialType === 'inference' ? 4 : null)
      setCommandArgs('')
      setModelEntries(
        initialCloudModels.map((id, idx) => ({
          id,
          owned_by: initialProvider,
          enabled: true,
          is_default: idx === 0,
        }))
      )
      setAppId('')
      setInstallationId('')
      setGithubOrg('')
      setGitAppSetupMode('manifest')
      setK8sNamespace('default')
      setK8sSandboxNamespace('')
      setK8sNamespaceIsolation('workload')
      setRunnerImage('')
      setGitRepositories([])
      setAccessRestricted(false)
      setSelectedAgentIds([])
      setEnabled(true)
      setAuthMode('api_key')
      setPersonalModeAcknowledged(false)
      const hasExistingInference = backends.some(
        (b) => b.type === 'inference' && b.enabled
      )
      setAutoCreateAgents(true)
      setSetSdlcDefault(!hasExistingInference)
    }
  }, [backendToEdit, backends, defaultName, defaultProvider, defaultType, isOpen])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !isSaving) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isSaving, onClose])

  const isEditing = Boolean(currentBackend)
  const currentTypeOption =
    BACKEND_TYPE_OPTIONS.find((opt) => opt.id === type) || BACKEND_TYPE_OPTIONS[0]

  const handleProviderChange = (nextProvider: string) => {
    setProvider(nextProvider)
    const def = getProviderDefinition(type, nextProvider)
    setEndpoint(def.defaultEndpoint)
    setSecrets({})
    setPingResult(null)
    setCliAuthSession(null)
    if (!def.supportsCliOAuth) {
      setAuthMode('api_key')
    }
    if (type === 'inference' && !currentBackend) {
      const cloudDefaults = DEFAULT_CLOUD_MODELS[nextProvider] || []
      setModelEntries(
        cloudDefaults.map((id, idx) => ({
          id,
          owned_by: nextProvider,
          enabled: true,
          is_default: idx === 0,
        }))
      )
    }
  }

  const handleSecretChange = (key: string, value: string) => {
    setSecrets((prev) => ({ ...prev, [key]: value }))
    setPingResult(null)
  }

  const handleToggleAgent = (agentId: string) => {
    setSelectedAgentIds((prev) =>
      prev.includes(agentId) ? prev.filter((id) => id !== agentId) : [...prev, agentId]
    )
  }

  const buildConfigPayload = (): Record<string, unknown> => {
    const base: Record<string, unknown> = currentBackend ? { ...currentBackend.config } : {}
    if (type === 'inference') {
      const def = getProviderDefinition(type, provider)
      const effectiveAuth = def.supportsCliOAuth ? authMode : 'api_key'
      base.auth_mode = effectiveAuth
      if (effectiveAuth === 'cli_oauth') {
        base.personal_mode = true
        base.personal_mode_acknowledged = personalModeAcknowledged
      } else {
        base.personal_mode = false
      }
      base.models = modelEntries
      base.discovered_models = modelEntries
        .filter((m) => m.enabled)
        .map((m) => m.id)
      const defaultEntry =
        modelEntries.find((m) => m.enabled && m.is_default) ||
        modelEntries.find((m) => m.enabled)
      if (defaultEntry) {
        base.default_model = defaultEntry.id
      }
      base.auto_create_agents = autoCreateAgents
      base.set_sdlc_default = setSdlcDefault
      base.is_sdlc_default = setSdlcDefault
    } else if (type === 'tools' && provider === 'mcp-stdio') {
      if (commandArgs.trim()) {
        base.command_args = commandArgs.trim()
      } else {
        delete base.command_args
      }
    } else if (type === 'compute') {
      if (provider === 'kubernetes') {
        base.namespace = k8sNamespace.trim() || 'default'
        if (k8sSandboxNamespace.trim()) {
          base.sandbox_namespace = k8sSandboxNamespace.trim()
        } else {
          delete base.sandbox_namespace
        }
        base.namespace_isolation = k8sNamespaceIsolation
      } else {
        delete base.namespace
        delete base.sandbox_namespace
        delete base.namespace_isolation
      }
      if (runnerImage.trim()) {
        base.runner_image = runnerImage.trim()
      } else {
        delete base.runner_image
      }
    } else if (type === 'git') {
      const def = getProviderDefinition(type, provider)
      if (def.supportsCliOAuth) {
        base.auth_mode = authMode
      }
      if (provider === 'github-app') {
        if (appId.trim()) base.app_id = appId.trim()
        else delete base.app_id
        if (installationId.trim()) base.installation_id = installationId.trim()
        else delete base.installation_id
      }
      base.repositories = gitRepositories
    }
    return base
  }

  const isDirty = useMemo(() => {
    if (!currentBackend) return true
    if (name.trim() !== currentBackend.name) return true
    if (endpoint.trim() !== (currentBackend.endpoint || '')) return true
    if (enabled !== currentBackend.enabled) return true
    if (accessRestricted !== currentBackend.access_restricted) return true

    if (Object.values(secrets).some((v) => v && v.trim().length > 0)) {
      return true
    }

    if (type === 'compute' || type === 'inference') {
      const origCap = currentBackend.capacity ?? (type === 'compute' ? 2 : 4)
      const currCap = capacity ?? (type === 'compute' ? 2 : 4)
      if (currCap !== origCap) return true
    }

    const origCfg = currentBackend.config || {}
    if (type === 'inference') {
      const origAuthMode =
        origCfg.auth_mode === 'cli_oauth' ||
        origCfg.auth_mode === 'device_oauth' ||
        currentBackend.secret_keys?.includes('cli_auth_bundle') ||
        currentBackend.secret_keys?.includes('oauth_token')
          ? 'cli_oauth'
          : 'api_key'
      if (authMode !== origAuthMode) return true
      if (
        authMode === 'cli_oauth' &&
        personalModeAcknowledged !== Boolean(origCfg.personal_mode_acknowledged ?? true)
      ) {
        return true
      }
      if (autoCreateAgents !== (origCfg.auto_create_agents !== false)) return true
      if (setSdlcDefault !== Boolean(origCfg.is_sdlc_default)) return true
      const origModels = getBackendModels(currentBackend)
      if (JSON.stringify(modelEntries) !== JSON.stringify(origModels)) {
        return true
      }
    }

    if (type === 'tools' && provider === 'mcp-stdio') {
      const origArgs = typeof origCfg.command_args === 'string' ? origCfg.command_args : ''
      if (commandArgs.trim() !== origArgs) return true
    }

    if (type === 'compute') {
      const origNs = typeof origCfg.namespace === 'string' ? origCfg.namespace : 'default'
      const origSbxNs =
        typeof origCfg.sandbox_namespace === 'string' ? origCfg.sandbox_namespace : ''
      const origIso =
        origCfg.namespace_isolation === 'shared' ||
          origCfg.namespace_isolation === 'workload' ||
          origCfg.namespace_isolation === 'project'
          ? origCfg.namespace_isolation
          : 'workload'
      const origImg = typeof origCfg.runner_image === 'string' ? origCfg.runner_image : ''
      if (provider === 'kubernetes') {
        if ((k8sNamespace.trim() || 'default') !== origNs) return true
        if (k8sSandboxNamespace.trim() !== origSbxNs) return true
        if (k8sNamespaceIsolation !== origIso) return true
      }
      if (runnerImage.trim() !== origImg) return true
    }

    if (type === 'git') {
      const def = getProviderDefinition(type, provider)
      if (def.supportsCliOAuth) {
        const origAuthMode =
          origCfg.auth_mode === 'cli_oauth' || origCfg.auth_mode === 'device_oauth'
            ? 'cli_oauth'
            : 'api_key'
        if (authMode !== origAuthMode) return true
      }
      if (provider === 'github-app') {
        const origApp = typeof origCfg.app_id === 'string' ? origCfg.app_id : ''
        const origInst = typeof origCfg.installation_id === 'string' ? origCfg.installation_id : ''
        if (appId.trim() !== origApp || installationId.trim() !== origInst) return true
      }
      const origRepos = getBackendRepositories(currentBackend)
      if (JSON.stringify(gitRepositories) !== JSON.stringify(origRepos)) {
        return true
      }
    }

    const origAllowed = new Set(currentBackend.allowed_agent_ids)
    if (selectedAgentIds.length !== origAllowed.size) return true
    if (selectedAgentIds.some((id) => !origAllowed.has(id))) return true

    return false
  }, [
    currentBackend,
    name,
    endpoint,
    enabled,
    accessRestricted,
    secrets,
    type,
    capacity,
    authMode,
    modelEntries,
    provider,
    commandArgs,
    appId,
    installationId,
    k8sNamespace,
    k8sSandboxNamespace,
    k8sNamespaceIsolation,
    runnerImage,
    gitRepositories,
    selectedAgentIds,
    autoCreateAgents,
    setSdlcDefault,
    personalModeAcknowledged,
  ])

  const providerDef = getProviderDefinition(type, provider)
  const requiresPersonalModeAck =
    type === 'inference' &&
    Boolean(providerDef.supportsCliOAuth) &&
    authMode === 'cli_oauth' &&
    !personalModeAcknowledged
  const missingRequiredSecret = useMemo(() => {
    if (
      (type === 'inference' || type === 'git') &&
      providerDef.supportsCliOAuth &&
      authMode === 'cli_oauth'
    ) {
      return type === 'git' ? !currentBackend?.secret_keys.includes('token') : false
    }
    if (type === 'git' && provider === 'github-app' && gitAppSetupMode === 'manifest') {
      return !currentBackend?.secret_keys.includes('private_key')
    }
    const existingKeys = currentBackend?.secret_keys || []
    return providerDef.secrets.some(
      (sec) => sec.required && !existingKeys.includes(sec.key) && !secrets[sec.key]?.trim()
    )
  }, [authMode, currentBackend, gitAppSetupMode, provider, providerDef.secrets, providerDef.supportsCliOAuth, secrets, type])

  const isInlineCreatedClean = !backendToEdit && Boolean(localBackend) && !isDirty

  const isSaveDisabled =
    isSaving ||
    !name.trim() ||
    requiresPersonalModeAck ||
    (isEditing ? !isDirty && !isInlineCreatedClean : missingRequiredSecret)

  const cliAuthenticated = Boolean(
    cliAuthSession?.authenticated ||
    currentBackend?.secret_keys.includes('cli_auth_bundle') ||
    currentBackend?.secret_keys.includes('oauth_token') ||
    currentBackend?.config?.cli_auth_status === 'authenticated' ||
    (type === 'git' &&
      currentBackend?.secret_keys.includes('token') &&
      currentBackend?.config?.device_status === 'authenticated')
  )
  const cliAuthUpdatedAt =
    typeof currentBackend?.config?.cli_auth_updated_at === 'string'
      ? currentBackend.config.cli_auth_updated_at
      : typeof currentBackend?.config?.device_updated_at === 'string'
        ? currentBackend.config.device_updated_at
        : null

  useEffect(() => {
    if (
      !isOpen ||
      !currentBackend ||
      !cliAuthSession ||
      cliAuthSession.status !== 'awaiting_user' ||
      cliAuthSession.authenticated
    ) {
      return
    }
    const timer = window.setInterval(async () => {
      try {
        const res = await api.backends.getCliAuthStatus(currentBackend.id)
        setCliAuthSession({
          status: res.status,
          auth_url: res.auth_url,
          user_code: res.user_code,
          log_output: res.log_output,
          authenticated: res.authenticated,
          error: res.error,
        })
        if (res.backend) {
          setLocalBackend(res.backend)
          if (res.backend.type === 'git') {
            setGitRepositories(getBackendRepositories(res.backend))
          } else if (res.backend.type === 'inference' && res.authenticated) {
            setModelEntries(getBackendModels(res.backend))
          }
        }
        if (res.authenticated) {
          await Promise.all([
            queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
            queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
            queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
          ])
        }
      } catch {
        // Ignore transient polling errors
      }
    }, 2500)
    return () => window.clearInterval(timer)
  }, [cliAuthSession, currentBackend, isOpen, queryClient])

  const handleStartCliAuth = async () => {
    if (requiresPersonalModeAck) {
      setErrorMessage('Check the Personal Mode acknowledgment above to launch sign-in.')
      return
    }
    setErrorMessage(null)
    setIsStartingCliAuth(true)
    try {
      let targetBackend = currentBackend
      if (!targetBackend) {
        const effectiveName = name.trim() || providerDef.label
        if (!name.trim()) {
          setName(effectiveName)
        }
        const effectiveCapacity =
          type === 'compute' || type === 'inference'
            ? capacity ?? (type === 'compute' ? 2 : 4)
            : null
        const created = await api.backends.create({
          name: effectiveName,
          type,
          provider,
          endpoint: type === 'git' ? endpoint.trim() || providerDef.defaultEndpoint : null,
          config: buildConfigPayload(),
          secrets: {},
          capacity: effectiveCapacity,
          access_restricted: accessRestricted,
          enabled,
        })
        setLocalBackend(created)
        targetBackend = created
        await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      }
      const res = await api.backends.startCliAuth(targetBackend.id)
      setCliAuthSession({
        status: res.status,
        auth_url: res.auth_url,
        user_code: res.user_code,
        log_output: res.log_output,
        authenticated: res.authenticated,
        error: res.error,
      })
      if (res.error) {
        setErrorMessage(res.error)
      }
      if (res.backend) {
        setLocalBackend(res.backend)
        if (res.backend.type === 'inference' && res.authenticated) {
          setModelEntries(getBackendModels(res.backend))
        }
      }
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError ? err.detail : 'Failed to start CLI sign-in container'
      )
    } finally {
      setIsStartingCliAuth(false)
    }
  }

  const handleSubmitCliAuthCode = async (code: string) => {
    if (!currentBackend) return
    setErrorMessage(null)
    setIsSubmittingCliAuthCode(true)
    try {
      const res = await api.backends.submitCliAuthCode(currentBackend.id, code)
      setCliAuthSession({
        status: res.status,
        auth_url: res.auth_url,
        user_code: res.user_code,
        log_output: res.log_output,
        authenticated: res.authenticated,
        error: res.error,
      })
      if (res.backend) {
        setLocalBackend(res.backend)
        if (res.backend.type === 'inference' && res.authenticated) {
          setModelEntries(getBackendModels(res.backend))
        }
      }
      if (res.authenticated) {
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
          queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
          queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
        ])
      } else if (res.error) {
        setErrorMessage(res.error)
      }
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError ? err.detail : 'Failed to verify CLI authorization code'
      )
    } finally {
      setIsSubmittingCliAuthCode(false)
    }
  }

  const handleCancelCliAuth = async () => {
    if (!currentBackend) return
    try {
      await api.backends.cancelCliAuth(currentBackend.id)
      setCliAuthSession(null)
    } catch {
      setCliAuthSession(null)
    }
  }

  const applySyncedGitAppBackend = (updated: Backend) => {
    setLocalBackend(updated)
    const cfg = updated.config || {}
    if (typeof cfg.app_id === 'string' && cfg.app_id) {
      setAppId(cfg.app_id)
    }
    if (typeof cfg.installation_id === 'string' && cfg.installation_id) {
      setInstallationId(cfg.installation_id)
    }
    setGitRepositories(getBackendRepositories(updated))
  }

  const handleStartGitAppManifest = async () => {
    setErrorMessage(null)
    setIsStartingGitAppManifest(true)
    try {
      const effectiveName =
        name.trim() ||
        (githubOrg.trim() ? `GitHub App (${githubOrg.trim()})` : 'GitHub App')
      if (!name.trim()) {
        setName(effectiveName)
      }
      const res = await api.backends.startGitHubAppManifest({
        backend_id: currentBackend?.id || null,
        name: effectiveName,
        organization: githubOrg.trim() || null,
        endpoint: endpoint.trim() || providerDef.defaultEndpoint,
        origin: window.location.origin,
      })
      applySyncedGitAppBackend(res.backend)
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })

      const safeActionUrl = safeExternalUrl(res.action_url)
      if (!safeActionUrl) {
        throw new Error('Invalid GitHub App registration URL returned by server.')
      }

      const popupName = 'gitseed_github_app_manifest'
      const popup = window.open('about:blank', popupName, 'width=920,height=780')
      if (popup) popup.opener = null
      const form = document.createElement('form')
      form.method = 'POST'
      form.action = safeActionUrl
      form.target = popupName
      form.style.display = 'none'
      const input = document.createElement('input')
      input.type = 'hidden'
      input.name = 'manifest'
      input.value = res.manifest
      form.appendChild(input)
      document.body.appendChild(form)
      form.submit()
      document.body.removeChild(form)
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError ? err.detail : 'Failed to launch GitHub App registration'
      )
    } finally {
      setIsStartingGitAppManifest(false)
    }
  }

  const handleSyncGitApp = async () => {
    if (!currentBackend) return
    setIsSyncingGitApp(true)
    setErrorMessage(null)
    try {
      const updated = await api.backends.syncGitHubApp(currentBackend.id)
      applySyncedGitAppBackend(updated)
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError ? err.detail : 'Failed to sync GitHub App installation'
      )
    } finally {
      setIsSyncingGitApp(false)
    }
  }

  const gitAppManifestStatus =
    typeof currentBackend?.config?.manifest_status === 'string'
      ? currentBackend.config.manifest_status
      : null
  const gitAppHtmlUrl =
    typeof currentBackend?.config?.app_html_url === 'string'
      ? currentBackend.config.app_html_url
      : null

  useEffect(() => {
    if (
      !isOpen ||
      !currentBackend ||
      type !== 'git' ||
      provider !== 'github-app' ||
      (gitAppManifestStatus !== 'awaiting_creation' &&
        gitAppManifestStatus !== 'awaiting_install')
    ) {
      return
    }
    const syncNow = async () => {
      try {
        const updated = await api.backends.syncGitHubApp(currentBackend.id)
        applySyncedGitAppBackend(updated)
        if (updated.config?.manifest_status === 'connected') {
          await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
        }
      } catch {
        // Ignore transient polling errors
      }
    }
    const onMessage = (e: MessageEvent) => {
      if (e.origin !== window.location.origin) return
      if (e.data && typeof e.data === 'object' && e.data.type === 'gitseed:github-app-connected') {
        void syncNow()
      }
    }
    window.addEventListener('message', onMessage)
    let channel: BroadcastChannel | null = null
    if (typeof BroadcastChannel !== 'undefined') {
      try {
        channel = new BroadcastChannel('gitseed:github-app')
        channel.onmessage = (e: MessageEvent) => {
          if (e.data && typeof e.data === 'object' && e.data.type === 'gitseed:github-app-connected') {
            void syncNow()
          }
        }
      } catch {
        channel = null
      }
    }
    const timer = window.setInterval(() => {
      void syncNow()
    }, 2500)
    return () => {
      window.removeEventListener('message', onMessage)
      if (channel) {
        channel.close()
      }
      window.clearInterval(timer)
    }
  }, [currentBackend, gitAppManifestStatus, isOpen, provider, queryClient, type])

  const handleSyncRepos = async () => {
    if (!currentBackend) return
    setIsSyncingRepos(true)
    setErrorMessage(null)
    try {
      const updated = await api.backends.discoverRepos(currentBackend.id)
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      setGitRepositories(getBackendRepositories(updated))
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError
          ? err.detail
          : 'Failed to sync repositories from provider'
      )
    } finally {
      setIsSyncingRepos(false)
    }
  }

  const handleSyncModels = async () => {
    if (!currentBackend) return
    setIsSyncingModels(true)
    setErrorMessage(null)
    try {
      const updated = await api.backends.discoverModels(currentBackend.id)
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      setModelEntries(getBackendModels(updated))
    } catch (err) {
      setErrorMessage(
        err instanceof ApiError
          ? err.detail
          : 'Failed to sync models from provider'
      )
    } finally {
      setIsSyncingModels(false)
    }
  }

  const handleTestPing = async () => {
    setIsPinging(true)
    try {
      const nonEmptySecrets: Record<string, string> = {}
      Object.entries(secrets).forEach(([k, v]) => {
        if (v && v.trim()) {
          nonEmptySecrets[k] = v.trim()
        }
      })

      const hasEnteredSecrets = Object.keys(nonEmptySecrets).length > 0
      const isInferenceCliOAuth =
        type === 'inference' && Boolean(providerDef.supportsCliOAuth) && authMode === 'cli_oauth'
      const isManifestAuth =
        type === 'git' &&
        provider === 'github-app' &&
        (gitAppSetupMode === 'manifest' ||
          currentBackend?.config?.auth_mode === 'manifest' ||
          Boolean(currentBackend?.config?.manifest_status))
      const isDeviceOAuth =
        type === 'git' &&
        ((Boolean(providerDef.supportsCliOAuth) && authMode === 'cli_oauth') ||
          currentBackend?.config?.auth_mode === 'device_oauth')
      const effectiveEndpoint = isInferenceCliOAuth ? null : endpoint.trim() || null

      const providerOrTypeChanged = Boolean(
        currentBackend && (type !== currentBackend.type || provider !== currentBackend.provider)
      )
      const endpointChanged = Boolean(
        currentBackend && (effectiveEndpoint || '') !== (currentBackend.endpoint || '')
      )
      const useStoredBackendPing = Boolean(
        currentBackend?.id &&
          !providerOrTypeChanged &&
          (isManifestAuth ||
            isDeviceOAuth ||
            (isInferenceCliOAuth && !hasEnteredSecrets) ||
            (!hasEnteredSecrets && !endpointChanged))
      )

      if (currentBackend && useStoredBackendPing) {
        const updated = await api.backends.test(currentBackend.id)
        await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
        setLocalBackend(updated)
        if (updated.type === 'git') {
          setGitRepositories(getBackendRepositories(updated))
        } else if (updated.type === 'inference') {
          setModelEntries(getBackendModels(updated))
        }
        setPingResult({
          status:
            updated.health === 'healthy'
              ? 'healthy'
              : updated.health === 'degraded'
                ? 'degraded'
                : 'unreachable',
          latencyMs: updated.health_latency_ms,
          error: updated.health_error,
        })
      } else if (
        currentBackend &&
        !providerOrTypeChanged &&
        !hasEnteredSecrets &&
        endpointChanged &&
        currentBackend.secret_keys.length > 0
      ) {
        setPingResult({
          status: 'unreachable',
          latencyMs: null,
          error: 'Save your updated endpoint first or re-enter credentials to test the new URL.',
        })
      } else {
        const probed = await api.backends.probe({
          type,
          provider,
          endpoint: effectiveEndpoint,
          config: buildConfigPayload(),
          secrets: nonEmptySecrets,
        })
        if (
          type === 'inference' &&
          Array.isArray(probed.models) &&
          probed.models.length > 0
        ) {
          setModelEntries(
            getBackendModels({
              id: 'probe',
              name: name || 'probe',
              type: 'inference',
              provider,
              endpoint: effectiveEndpoint,
              config: { models: probed.models },
              secret_keys: [],
              capacity: null,
              access_restricted: false,
              allowed_agent_ids: [],
              health: probed.health,
              health_checked_at: null,
              health_latency_ms: probed.latency_ms,
              health_error: probed.error,
              enabled: true,
              created_by: null,
              created_at: '',
              updated_at: '',
              updated_by: null,
            })
          )
        } else if (
          type === 'git' &&
          Array.isArray(probed.repositories) &&
          probed.repositories.length > 0
        ) {
          setGitRepositories(
            getBackendRepositories({
              id: 'probe',
              name: name || 'probe',
              type: 'git',
              provider,
              endpoint: effectiveEndpoint,
              config: { repositories: probed.repositories },
              secret_keys: [],
              capacity: null,
              access_restricted: false,
              allowed_agent_ids: [],
              health: probed.health,
              health_checked_at: null,
              health_latency_ms: probed.latency_ms,
              health_error: probed.error,
              enabled: true,
              created_by: null,
              created_at: '',
              updated_at: '',
              updated_by: null,
            })
          )
        }
        setPingResult({
          status:
            probed.health === 'healthy'
              ? 'healthy'
              : probed.health === 'degraded'
                ? 'degraded'
                : 'unreachable',
          latencyMs: probed.latency_ms,
          error: probed.error,
        })
      }
    } catch (err) {
      setPingResult({
        status: 'unreachable',
        latencyMs: null,
        error: err instanceof ApiError ? err.detail : 'Connection test failed',
      })
    } finally {
      setIsPinging(false)
    }
  }

  const handleCopyId = () => {
    if (!backendToEdit) return
    navigator.clipboard.writeText(backendToEdit.id)
    setCopiedId(true)
    setTimeout(() => setCopiedId(false), 2000)
  }

  const usedProjects = useMemo(() => {
    if (!backendToEdit) return []
    return getProjectsUsingBackend(backendToEdit, projects, projectReposByProject)
  }, [backendToEdit, projects, projectReposByProject])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!name.trim() || isSaving) return

    const nonEmptySecrets: Record<string, string> = {}
    Object.entries(secrets).forEach(([k, v]) => {
      if (v && v.trim()) {
        nonEmptySecrets[k] = v.trim()
      }
    })
    const isCliOAuth =
      type === 'inference' && providerDef.supportsCliOAuth && authMode === 'cli_oauth'
    if (isCliOAuth && !cliAuthenticated && Object.keys(nonEmptySecrets).length === 0) {
      setErrorMessage(
        'Complete CLI sign-in (paste and verify the authorization code above) before saving so credentials and default stage agents can be provisioned.'
      )
      return
    }

    if (isInlineCreatedClean && localBackend) {
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
      ])
      onSaved?.(localBackend)
      onClose()
      return
    }

    setIsSaving(true)
    setErrorMessage(null)

    try {
      const effectiveCapacity =
        type === 'compute' || type === 'inference'
          ? capacity ?? (type === 'compute' ? 2 : 4)
          : null
      const configPayload = buildConfigPayload()
      const effectiveEndpoint = isCliOAuth ? null : endpoint.trim() || null

      let savedBackend: Backend
      if (currentBackend) {
        savedBackend = await api.backends.update(currentBackend.id, {
          name: name.trim(),
          endpoint: effectiveEndpoint,
          config: configPayload,
          secrets: Object.keys(nonEmptySecrets).length > 0 ? nonEmptySecrets : undefined,
          capacity: effectiveCapacity,
          access_restricted: accessRestricted,
          enabled,
        })

        // Synchronize agent access grants/revocations
        const origSet = new Set(currentBackend.allowed_agent_ids)
        const targetSet = new Set(accessRestricted ? selectedAgentIds : [])

        for (const agentId of targetSet) {
          if (!origSet.has(agentId)) {
            await api.backends.grantAgent(savedBackend.id, agentId)
          }
        }
        for (const agentId of origSet) {
          if (!targetSet.has(agentId)) {
            await api.backends.revokeAgent(savedBackend.id, agentId)
          }
        }
      } else {
        savedBackend = await api.backends.create({
          name: name.trim(),
          type,
          provider,
          endpoint: effectiveEndpoint,
          config: configPayload,
          secrets: nonEmptySecrets,
          capacity: effectiveCapacity,
          access_restricted: accessRestricted,
          enabled,
        })

        if (accessRestricted && selectedAgentIds.length > 0) {
          for (const agentId of selectedAgentIds) {
            await api.backends.grantAgent(savedBackend.id, agentId)
          }
        }
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      onSaved?.(savedBackend)
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.detail)
      } else {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to save backend')
      }
    } finally {
      setIsSaving(false)
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      {/* Backdrop */}
      <div
        className="fixed inset-0 bg-black/40 backdrop-blur-xs transition-opacity duration-200"
        onClick={() => {
          if (!isSaving) onClose()
        }}
        aria-hidden="true"
      />

      {/* Slide-over Drawer Panel */}
      <aside
        className="relative z-50 w-full max-w-xl bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-700/80 shadow-2xl flex flex-col h-full overflow-hidden"
        role="dialog"
        aria-modal="true"
        aria-label={isEditing ? `Edit Backend · ${name}` : 'Register Backend'}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 shrink-0 bg-zinc-50/50 dark:bg-zinc-900/50">
          <div className="flex items-center gap-2.5 min-w-0">
            <h2 className="text-base sm:text-lg font-heading font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-2 min-w-0">
              <span className="shrink-0">{isEditing ? 'Backend' : 'Register Backend'}</span>
              {isEditing ? (
                <>
                  <span className="text-zinc-400">·</span>
                  <div className="flex items-center gap-1.5 font-semibold min-w-0">
                    <ProviderIcon provider={provider} type={type} size={16} />
                    <span className="truncate max-w-[200px]">{name}</span>
                  </div>
                </>
              ) : (
                <>
                  <span className="text-zinc-400">·</span>
                  <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-lg text-xs font-semibold bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-500/25 shrink-0">
                    {currentTypeOption.icon}
                    <span>{currentTypeOption.label}</span>
                  </span>
                </>
              )}
            </h2>
            {isEditing && (
              <Chip
                size="sm"
                variant="soft"
                color={enabled ? 'success' : 'default'}
                className="text-xs font-semibold ml-1 shrink-0"
              >
                <span
                  className={`w-1.5 h-1.5 rounded-full mr-1.5 inline-block ${enabled ? 'bg-emerald-500 animate-pulse' : 'bg-zinc-400'
                    }`}
                />
                {enabled ? 'Enabled' : 'Disabled'}
              </Chip>
            )}
          </div>
          <div className="flex items-center gap-1 shrink-0">
            {isEditing && backendToEdit && (
              <button
                type="button"
                onClick={handleCopyId}
                title={`Copy ID (${backendToEdit.id})`}
                className="px-2 py-1 rounded-lg text-xs font-mono text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer flex items-center gap-1"
              >
                {copiedId ? (
                  <>
                    <Check size={12} className="text-emerald-500" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy size={12} />
                    <span>{backendToEdit.id}</span>
                  </>
                )}
              </button>
            )}
            <button
              type="button"
              onClick={onClose}
              aria-label="Close"
              className="p-1.5 rounded-xl text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
            >
              <X size={18} />
            </button>
          </div>
        </div>

        {/* Top Connectivity Probe Strip */}
        <div className="px-6 py-2.5 bg-zinc-50 dark:bg-zinc-800/40 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-3 text-xs">
          <div className="flex items-center gap-2 min-w-0">
            <Radio
              size={14}
              className={
                (pingResult?.status || backendToEdit?.health) === 'unreachable'
                  ? 'text-rose-500 shrink-0'
                  : pingResult?.status === 'healthy' ||
                    (isEditing && enabled && backendToEdit?.health === 'healthy')
                    ? 'text-emerald-500 shrink-0'
                    : 'text-zinc-400 shrink-0'
              }
            />
            <span className="font-mono text-zinc-500 dark:text-zinc-400 truncate text-xs">
              {type === 'inference' && providerDef.supportsCliOAuth && authMode === 'cli_oauth'
                ? `CLI Subscription (${providerDef.cliBinary || provider} OAuth)`
                : endpoint || providerDef.defaultEndpoint || 'Local Socket'}
            </span>
            {(pingResult?.status || backendToEdit?.health) === 'unreachable' ? (
              <>
                <span className="text-zinc-400 select-none">·</span>
                <span
                  className="text-rose-600 dark:text-rose-400 font-semibold text-xs truncate"
                  title={pingResult?.error || backendToEdit?.health_error || 'Unreachable'}
                >
                  {pingResult?.error || backendToEdit?.health_error || 'Unreachable'}
                </span>
              </>
            ) : (pingResult?.latencyMs ?? backendToEdit?.health_latency_ms ?? null) !== null ? (
              <>
                <span className="text-zinc-400 select-none">·</span>
                <span className="font-mono text-emerald-600 dark:text-emerald-400 font-semibold text-xs shrink-0">
                  Verified ({pingResult?.latencyMs ?? backendToEdit?.health_latency_ms}ms)
                </span>
              </>
            ) : null}
          </div>
          <button
            type="button"
            onClick={handleTestPing}
            disabled={isPinging}
            className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline flex items-center gap-1 cursor-pointer shrink-0 disabled:opacity-50"
          >
            <RefreshCw size={12} className={isPinging ? 'animate-spin' : ''} />
            <span>{isPinging ? 'Testing...' : 'Test Connection'}</span>
          </button>
        </div>

        {/* Tab Navigation */}
        <div className="px-6 pt-3 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center gap-5 text-sm font-semibold">
          <button
            type="button"
            onClick={() => setActiveTab('config')}
            className={`pb-2.5 border-b-2 transition-colors cursor-pointer ${activeTab === 'config'
                ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
              }`}
          >
            Settings
          </button>
          {type === 'git' && (
            <button
              type="button"
              onClick={() => setActiveTab('repositories')}
              className={`pb-2.5 border-b-2 transition-colors cursor-pointer flex items-center gap-1.5 ${activeTab === 'repositories'
                  ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                  : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
            >
              <span>Repositories</span>
              {gitRepositories.length > 0 && (
                <span className="px-1.5 py-0.5 rounded-full text-xs font-mono bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
                  {gitRepositories.filter((r) => r.enabled).length}/{gitRepositories.length}
                </span>
              )}
            </button>
          )}
          {type === 'inference' && (
            <button
              type="button"
              onClick={() => setActiveTab('models')}
              className={`pb-2.5 border-b-2 transition-colors cursor-pointer flex items-center gap-1.5 ${activeTab === 'models'
                  ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                  : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
            >
              <span>Models</span>
              {modelEntries.length > 0 && (
                <span className="px-1.5 py-0.5 rounded-full text-xs font-mono bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
                  {modelEntries.filter((m) => m.enabled).length}/{modelEntries.length}
                </span>
              )}
            </button>
          )}
          <button
            type="button"
            onClick={() => setActiveTab('access')}
            className={`pb-2.5 border-b-2 transition-colors cursor-pointer ${activeTab === 'access'
                ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
              }`}
          >
            Agent Access
          </button>
          {(type === 'compute' || type === 'inference') && (
            <button
              type="button"
              onClick={() => setActiveTab('capacity')}
              className={`pb-2.5 border-b-2 transition-colors cursor-pointer ${activeTab === 'capacity'
                  ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                  : 'border-transparent text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
            >
              Capacity & Limits
            </button>
          )}
        </div>

        {/* Form Body */}
        <form
          onSubmit={handleSubmit}
          className="flex-1 overflow-y-auto p-6 flex flex-col gap-5 text-sm"
        >
          {errorMessage && (
            <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/25 text-rose-600 dark:text-rose-400 flex items-start gap-2 text-xs">
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <span>{errorMessage}</span>
            </div>
          )}

          {activeTab === 'config' && (
            <BackendConfigTab
              isEditing={isEditing}
              name={name}
              setName={setName}
              type={type}
              provider={provider}
              onProviderChange={handleProviderChange}
              endpoint={endpoint}
              setEndpoint={setEndpoint}
              secrets={secrets}
              onSecretChange={handleSecretChange}
              existingSecretKeys={currentBackend?.secret_keys || []}
              commandArgs={commandArgs}
              setCommandArgs={setCommandArgs}
              appId={appId}
              setAppId={setAppId}
              installationId={installationId}
              setInstallationId={setInstallationId}
              k8sNamespace={k8sNamespace}
              setK8sNamespace={setK8sNamespace}
              k8sSandboxNamespace={k8sSandboxNamespace}
              setK8sSandboxNamespace={setK8sSandboxNamespace}
              k8sNamespaceIsolation={k8sNamespaceIsolation}
              setK8sNamespaceIsolation={setK8sNamespaceIsolation}
              runnerImage={runnerImage}
              setRunnerImage={setRunnerImage}
              enabled={enabled}
              setEnabled={setEnabled}
              authMode={authMode}
              onAuthModeChange={setAuthMode}
              personalModeAcknowledged={personalModeAcknowledged}
              onPersonalModeAcknowledgedChange={setPersonalModeAcknowledged}
              cliAuthenticated={cliAuthenticated}
              cliAuthUpdatedAt={cliAuthUpdatedAt}
              cliAuthSession={cliAuthSession}
              isStartingCliAuth={isStartingCliAuth}
              isSubmittingCliAuthCode={isSubmittingCliAuthCode}
              onStartCliAuth={handleStartCliAuth}
              onSubmitCliAuthCode={handleSubmitCliAuthCode}
              onCancelCliAuth={handleCancelCliAuth}
              gitAppSetupMode={gitAppSetupMode}
              onGitAppSetupModeChange={setGitAppSetupMode}
              githubOrg={githubOrg}
              setGithubOrg={setGithubOrg}
              gitAppManifestStatus={gitAppManifestStatus}
              gitAppHtmlUrl={gitAppHtmlUrl}
              isStartingGitAppManifest={isStartingGitAppManifest}
              isSyncingGitApp={isSyncingGitApp}
              onStartGitAppManifest={handleStartGitAppManifest}
              onSyncGitApp={handleSyncGitApp}
              autoCreateAgents={autoCreateAgents}
              setAutoCreateAgents={setAutoCreateAgents}
              setSdlcDefault={setSdlcDefault}
              setSetSdlcDefault={setSetSdlcDefault}
            />
          )}

          {activeTab === 'repositories' && type === 'git' && (
            <BackendRepositoriesTab
              isEditing={isEditing}
              repositories={gitRepositories}
              onChangeRepositories={setGitRepositories}
              isSyncing={isSyncingRepos}
              onSyncFromProvider={handleSyncRepos}
            />
          )}

          {activeTab === 'models' && type === 'inference' && (
            <BackendModelsTab
              isEditing={isEditing}
              provider={provider}
              models={modelEntries}
              onChangeModels={setModelEntries}
              isSyncing={isSyncingModels}
              onSyncFromProvider={handleSyncModels}
            />
          )}

          {activeTab === 'access' && (
            <BackendAccessTab
              accessRestricted={accessRestricted}
              setAccessRestricted={setAccessRestricted}
              selectedAgentIds={selectedAgentIds}
              onToggleAgent={handleToggleAgent}
              agents={agents}
              usedProjects={usedProjects}
              onNavigateProject={(projectId) => {
                onClose()
                navigateToDetail(VIEWS.PROJECTS, projectId)
              }}
            />
          )}

          {activeTab === 'capacity' && (
            <BackendCapacityTab
              type={type}
              capacity={capacity}
              setCapacity={setCapacity}
            />
          )}

          {/* Footer Actions */}
          <div className="flex items-center justify-between pt-4 mt-auto border-t border-zinc-200 dark:border-zinc-700/80">
            <div>
              {isEditing && onDelete && backendToEdit && (
                <button
                  type="button"
                  onClick={() => onDelete(backendToEdit)}
                  className="text-xs font-medium text-rose-600 dark:text-rose-400 hover:underline cursor-pointer flex items-center gap-1"
                >
                  <Trash2 size={13} />
                  <span>Deregister Backend</span>
                </button>
              )}
            </div>

            <div className="flex items-center gap-2.5">
              {isEditing && (
                <span
                  className={`text-xs font-medium mr-1 select-none ${
                    isDirty
                      ? 'text-amber-600 dark:text-amber-400'
                      : isInlineCreatedClean
                        ? 'text-emerald-600 dark:text-emerald-400'
                        : 'text-zinc-400 dark:text-zinc-500'
                  }`}
                >
                  {isDirty ? 'Unsaved changes' : isInlineCreatedClean ? 'Saved' : 'No changes'}
                </span>
              )}
              <Button
                type="button"
                variant="secondary"
                size="sm"
                onClick={onClose}
                isDisabled={isSaving}
                className="text-xs font-medium text-zinc-700 dark:text-zinc-300 bg-zinc-100 hover:bg-zinc-200 dark:bg-zinc-800 dark:hover:bg-zinc-700 border border-zinc-200 dark:border-zinc-700/80 cursor-pointer"
              >
                Cancel
              </Button>
              <Button
                type="submit"
                variant="primary"
                size="sm"
                isDisabled={isSaveDisabled}
                className={`text-xs font-semibold flex items-center gap-1.5 transition-all ${isSaveDisabled
                    ? 'opacity-40 cursor-not-allowed shadow-none'
                    : 'opacity-100 cursor-pointer shadow-xs'
                  }`}
              >
                <Check size={14} />
                <span>
                  {isSaving
                    ? 'Saving...'
                    : isInlineCreatedClean
                      ? 'Done'
                      : isEditing
                        ? 'Save Changes'
                        : 'Register Backend'}
                </span>
              </Button>
            </div>
          </div>
        </form>
      </aside>
    </div>
  )
}

