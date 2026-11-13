import type {
  ActivityEvent,
  Agent,
  AgentBenchmarksResponse,
  AgentKind,
  AgentRuntime,
  AgentRuntimeAvailability,
  AgentSession,
  Approval,
  ApprovalDecision,
  Backend,
  BackendType,
  GovernanceReceiptEnvelope,
  InitLocalProjectRepositoryRequest,
  InitLocalProjectRepositoryResponse,
  InvitePreview,
  OrgRole,
  OrgSettings,
  PersonalAccessToken,
  PersonalAccessTokenCreated,
  Project,
  ProjectMember,
  ProjectRepository,
  ProjectRole,
  ProjectVisibility,
  ProjectWebhookConfig,
  ProposedSeedDraft,
  PullRequestCommentItem,
  PullRequestStatus,
  ReceiptPublicKey,
  RepoBlobResponse,
  RepoBranchesResponse,
  RepoTreeResponse,
  Run,
  SandboxRunConfig,
  SandboxSession,
  SessionPushDraft,
  SessionPushResult,
  SessionListItem,
  SessionVsCodeInfo,
  SessionWorkspaceFileContent,
  SessionWorkspaceFilesResponse,
  Seed,
  SeedAttachment,
  SeedAutonomy,
  SeedChangesResponse,
  SeedComment,
  SeedLinkItem,
  SeedStage,
  SeedTask,
  Skill,
  StagePolicy,
  TaskStatus,
  User,
  UserInviteCreated,
  UserPreferences,
  WebhookTestResponse,
} from '../types'

export class ApiError extends Error {
  readonly status: number
  readonly detail: string

  constructor(status: number, detail: string) {
    super(detail)
    this.name = 'ApiError'
    this.status = status
    this.detail = detail
  }
}

export interface HealthCheckResponse {
  status: 'ok' | 'degraded'
  service: string
  version: string
  environment: string
  database: string
  redis: string
}

let unauthorizedHandler: (() => void) | null = null

export function setOnUnauthorized(handler: (() => void) | null): void {
  unauthorizedHandler = handler
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  timeoutMs?: number
  skipUnauthorizedHook?: boolean
}

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { body, timeoutMs, skipUnauthorizedHook = false, headers: customHeaders, ...init } = options

  const controller = new AbortController()
  const timeoutId =
    typeof timeoutMs === 'number' && timeoutMs > 0
      ? setTimeout(() => controller.abort(), timeoutMs)
      : null

  const headers: Record<string, string> = {
    Accept: 'application/json',
    'X-GitSeed-CSRF': '1',
    ...(customHeaders as Record<string, string> | undefined),
  }

  let serializedBody: BodyInit | undefined
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
    serializedBody = JSON.stringify(body)
  }

  const effectiveSignal =
    init.signal && typeof AbortSignal.any === 'function'
      ? AbortSignal.any([init.signal, controller.signal])
      : (init.signal ?? controller.signal)

  try {
    const res = await fetch(path, {
      ...init,
      credentials: 'include',
      headers,
      body: serializedBody,
      signal: effectiveSignal,
    })

    if (res.status === 204) {
      return undefined as T
    }

    const contentType = res.headers.get('content-type') || ''
    const isJson = contentType.includes('application/json')

    if (!res.ok) {
      let detail = `Request failed (HTTP ${res.status})`
      if (isJson) {
        try {
          const errData = (await res.json()) as { detail?: unknown }
          if (typeof errData?.detail === 'string') {
            detail = errData.detail
          } else if (Array.isArray(errData?.detail) && errData.detail.length > 0) {
            const first = errData.detail[0] as { msg?: string }
            if (first?.msg) detail = first.msg
          }
        } catch {
          // ignore JSON parse failure
        }
      }

      if (res.status === 401 && !skipUnauthorizedHook && unauthorizedHandler) {
        unauthorizedHandler()
      }

      throw new ApiError(res.status, detail)
    }

    if (!isJson) {
      throw new ApiError(res.status, 'Expected JSON response from control plane API.')
    }

    return (await res.json()) as T
  } catch (err) {
    if (err instanceof ApiError) {
      throw err
    }
    if (err instanceof Error && err.name === 'AbortError') {
      throw new ApiError(0, `Connection timed out (${timeoutMs ?? 0}ms)`)
    }
    throw new ApiError(
      0,
      err instanceof Error ? err.message : 'Unable to reach GitSeed Control Plane API.'
    )
  } finally {
    if (timeoutId !== null) {
      clearTimeout(timeoutId)
    }
  }
}

export function isValidUser(data: unknown): data is User {
  if (!data || typeof data !== 'object') return false
  const candidate = data as Record<string, unknown>
  return (
    typeof candidate.id === 'string' &&
    candidate.id.length > 0 &&
    typeof candidate.email === 'string' &&
    candidate.email.includes('@') &&
    (candidate.org_role === 'admin' || candidate.org_role === 'member')
  )
}

export const api = {
  system: {
    health: (timeoutMs = 4000) =>
      request<HealthCheckResponse>('/healthz', { timeoutMs, skipUnauthorizedHook: true }),
  },

  auth: {
    me: async (): Promise<User> => {
      const data = await request<unknown>('/api/v1/auth/me', { skipUnauthorizedHook: true })
      if (!isValidUser(data)) {
        throw new ApiError(500, 'Invalid user payload from /api/v1/auth/me')
      }
      return data
    },
    login: async (email: string, password: string): Promise<User> => {
      const data = await request<unknown>('/api/v1/auth/login', {
        method: 'POST',
        body: { email, password },
        skipUnauthorizedHook: true,
      })
      if (!isValidUser(data)) {
        throw new ApiError(500, 'Invalid response from control plane server.')
      }
      return data
    },
    logout: () =>
      request<void>('/api/v1/auth/logout', {
        method: 'POST',
        skipUnauthorizedHook: true,
      }),
    changePassword: (currentPassword: string, newPassword: string) =>
      request<void>('/api/v1/auth/change-password', {
        method: 'POST',
        body: { current_password: currentPassword, new_password: newPassword },
      }),
    previewInvite: (token: string) =>
      request<InvitePreview>(`/api/v1/auth/invite?token=${encodeURIComponent(token)}`, {
        skipUnauthorizedHook: true,
      }),
    acceptInvite: (payload: { token: string; password: string; preferred_name?: string | null }) =>
      request<User>('/api/v1/auth/accept-invite', {
        method: 'POST',
        body: payload,
        skipUnauthorizedHook: true,
      }),
    listTokens: () => request<PersonalAccessToken[]>('/api/v1/auth/tokens'),
    createToken: (name: string, expiresInDays?: number) =>
      request<PersonalAccessTokenCreated>('/api/v1/auth/tokens', {
        method: 'POST',
        body: { name, expires_in_days: expiresInDays },
      }),
    revokeToken: (tokenId: string) =>
      request<void>(`/api/v1/auth/tokens/${encodeURIComponent(tokenId)}`, {
        method: 'DELETE',
      }),
  },

  users: {
    list: (limit = 100, offset = 0) =>
      request<User[]>(`/api/v1/users?limit=${limit}&offset=${offset}`),
    get: (userId: string) => request<User>(`/api/v1/users/${encodeURIComponent(userId)}`),
    invite: (payload: { email: string; name: string; org_role?: OrgRole }) =>
      request<UserInviteCreated>('/api/v1/users/invite', {
        method: 'POST',
        body: payload,
      }),
    reinvite: (userId: string) =>
      request<UserInviteCreated>(`/api/v1/users/${encodeURIComponent(userId)}/reinvite`, {
        method: 'POST',
      }),
    update: (
      userId: string,
      payload: {
        name?: string
        preferred_name?: string | null
        org_role?: OrgRole
        preferences?: UserPreferences
      }
    ) =>
      request<User>(`/api/v1/users/${encodeURIComponent(userId)}`, {
        method: 'PATCH',
        body: payload,
      }),
    suspend: (userId: string) =>
      request<User>(`/api/v1/users/${encodeURIComponent(userId)}/suspend`, {
        method: 'POST',
      }),
    reactivate: (userId: string) =>
      request<User>(`/api/v1/users/${encodeURIComponent(userId)}/reactivate`, {
        method: 'POST',
      }),
    delete: (userId: string) =>
      request<void>(`/api/v1/users/${encodeURIComponent(userId)}`, {
        method: 'DELETE',
      }),
  },

  projects: {
    list: (includeArchived = true, limit = 100, offset = 0) =>
      request<Project[]>(
        `/api/v1/projects?include_archived=${includeArchived}&limit=${limit}&offset=${offset}`
      ),
    get: (projectId: string) =>
      request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`),
    create: (payload: {
      name: string
      slug?: string | null
      description?: string
      tags?: string[]
      visibility?: ProjectVisibility
      monthly_budget?: string | number | null
      seed_budget_cap?: string | number | null
      spend_checkpoint?: string | number | null
      max_concurrent_runs?: number | null
      backends_restricted?: boolean
    }) =>
      request<Project>('/api/v1/projects', {
        method: 'POST',
        body: payload,
      }),
    update: (
      projectId: string,
      payload: {
        name?: string
        slug?: string
        description?: string
        tags?: string[]
        visibility?: ProjectVisibility
        monthly_budget?: string | number | null
        seed_budget_cap?: string | number | null
        spend_checkpoint?: string | number | null
        max_concurrent_runs?: number | null
        sandbox_config?: Record<string, unknown> | null
        backends_restricted?: boolean
      }
    ) =>
      request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}`, {
        method: 'PATCH',
        body: payload,
      }),
    archive: (projectId: string) =>
      request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}/archive`, {
        method: 'POST',
      }),
    restore: (projectId: string) =>
      request<Project>(`/api/v1/projects/${encodeURIComponent(projectId)}/restore`, {
        method: 'POST',
      }),
    listRepos: (projectId: string) =>
      request<ProjectRepository[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/repositories`),
    addRepo: (
      projectId: string,
      payload: {
        backend_id: string
        full_name: string
        default_branch?: string
        is_primary?: boolean
      }
    ) =>
      request<ProjectRepository>(`/api/v1/projects/${encodeURIComponent(projectId)}/repositories`, {
        method: 'POST',
        body: payload,
      }),
    initLocalRepo: (
      projectId: string,
      payload: InitLocalProjectRepositoryRequest = {}
    ) =>
      request<InitLocalProjectRepositoryResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/init-local`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    updateRepo: (
      projectId: string,
      repoId: number,
      payload: {
        default_branch?: string
        is_primary?: boolean
      }
    ) =>
      request<ProjectRepository>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/${encodeURIComponent(repoId)}`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    removeRepo: (projectId: string, repoId: number) =>
      request<void>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/${encodeURIComponent(repoId)}`,
        {
          method: 'DELETE',
        }
      ),
    listRepoBranches: (projectId: string, repoId: number) =>
      request<RepoBranchesResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/${encodeURIComponent(repoId)}/branches`
      ),
    getRepoTree: (
      projectId: string,
      repoId: number,
      params?: { ref?: string; path?: string }
    ) => {
      const qs = new URLSearchParams()
      if (params?.ref) qs.set('ref', params.ref)
      if (params?.path) qs.set('path', params.path)
      const suffix = qs.toString() ? `?${qs.toString()}` : ''
      return request<RepoTreeResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/${encodeURIComponent(repoId)}/tree${suffix}`
      )
    },
    getRepoBlob: (
      projectId: string,
      repoId: number,
      params: { path: string; ref?: string }
    ) => {
      const qs = new URLSearchParams({ path: params.path })
      if (params.ref) qs.set('ref', params.ref)
      return request<RepoBlobResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/${encodeURIComponent(repoId)}/blob?${qs.toString()}`
      )
    },
    listMembers: (projectId: string) =>
      request<ProjectMember[]>(`/api/v1/projects/${encodeURIComponent(projectId)}/members`),
    addMember: (projectId: string, payload: { user_id: string; role: ProjectRole }) =>
      request<ProjectMember>(`/api/v1/projects/${encodeURIComponent(projectId)}/members`, {
        method: 'POST',
        body: payload,
      }),
    updateMember: (projectId: string, userId: string, payload: { role: ProjectRole }) =>
      request<ProjectMember>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    removeMember: (projectId: string, userId: string) =>
      request<void>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/members/${encodeURIComponent(userId)}`,
        {
          method: 'DELETE',
        }
      ),
    allowBackend: (projectId: string, backendId: string) =>
      request<Project>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/backends/${encodeURIComponent(backendId)}`,
        {
          method: 'POST',
        }
      ),
    disallowBackend: (projectId: string, backendId: string) =>
      request<Project>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/backends/${encodeURIComponent(backendId)}`,
        {
          method: 'DELETE',
        }
      ),
    listPolicies: (projectId: string) =>
      request<StagePolicy[]>(`/api/v1/policies/projects/${encodeURIComponent(projectId)}`),
    getWebhooks: (projectId: string) =>
      request<ProjectWebhookConfig>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/webhooks`
      ),
    updateWebhooks: (
      projectId: string,
      payload: {
        enabled?: boolean
        secret?: string | null
        issue_label_trigger?: string
        auto_create_seed_on_issue?: boolean
        sync_pr_comments?: boolean
        auto_remediate_ci?: boolean
        auto_create_pr?: boolean
        outbound_url?: string | null
        outbound_events?: string[]
      }
    ) =>
      request<ProjectWebhookConfig>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/webhooks`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    rotateWebhookSecret: (projectId: string) =>
      request<ProjectWebhookConfig>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/webhooks/rotate-secret`,
        {
          method: 'POST',
        }
      ),
    testWebhook: (projectId: string) =>
      request<WebhookTestResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/webhooks/test`,
        {
          method: 'POST',
        }
      ),
    getSandboxConfig: (
      projectId: string,
      params?: { repo_id?: number | null; ref?: string | null }
    ) => {
      const qs = new URLSearchParams()
      if (params?.repo_id != null) qs.set('repo_id', String(params.repo_id))
      if (params?.ref) qs.set('ref', params.ref)
      const suffix = qs.toString() ? `?${qs.toString()}` : ''
      return request<SandboxRunConfig>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/config${suffix}`
      )
    },
    listSandboxes: (projectId: string, limit = 25) =>
      request<SandboxSession[]>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes?limit=${limit}`
      ),
    createSandbox: (
      projectId: string,
      payload: {
        repository_id?: number | null
        seed_id?: string | null
        ref?: string | null
        image?: string
        setup_command?: string | null
        command?: string
        artifact_globs?: string[]
        initial_stdin?: string | null
        as_root?: boolean
        kind?: 'job' | 'terminal'
        title?: string | null
      }
    ) =>
      request<SandboxSession>(`/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes`, {
        method: 'POST',
        body: payload,
      }),
    getSandbox: (projectId: string, sandboxId: string) =>
      request<SandboxSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}`
      ),
    sendSandboxInput: (
      projectId: string,
      sandboxId: string,
      payload: { input?: string; append_newline?: boolean; eof?: boolean }
    ) =>
      request<SandboxSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/input`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    stopSandbox: (projectId: string, sandboxId: string) =>
      request<SandboxSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/stop`,
        {
          method: 'POST',
        }
      ),
    getSandboxArtifactDownloadUrl: (
      projectId: string,
      sandboxId: string,
      relPath: string
    ) =>
      `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/artifacts/${relPath
        .split('/')
        .map(encodeURIComponent)
        .join('/')}`,

    listAgentSessions: (projectId: string, savedOnly = false, limit = 30) =>
      request<AgentSession[]>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/agent-sessions?saved_only=${savedOnly}&limit=${limit}`
      ),
    createAgentSession: (
      projectId: string,
      payload: {
        repository_id?: number | null
        ref?: string | null
        agent_id?: string | null
        runtime?: AgentRuntime | null
        model?: string | null
        inference_backend_id?: string | null
        models_backend_id?: string | null
        provider_backend_id?: string | null
        title?: string | null
        initial_prompt?: string | null
        is_saved?: boolean
      }
    ) =>
      request<AgentSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/agent-sessions`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    getAgentSession: (projectId: string, sessionId: string) =>
      request<AgentSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/agent-sessions/${encodeURIComponent(sessionId)}`
      ),
    updateAgentSession: (
      projectId: string,
      sessionId: string,
      payload: { title?: string | null; is_saved?: boolean | null; agent_id?: string | null }
    ) =>
      request<AgentSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/agent-sessions/${encodeURIComponent(sessionId)}`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    stopAgentSession: (projectId: string, sessionId: string) =>
      request<AgentSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/agent-sessions/${encodeURIComponent(sessionId)}/stop`,
        {
          method: 'POST',
        }
      ),
    deleteAgentSession: (projectId: string, sessionId: string) =>
      request<{ deleted: boolean }>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/agent-sessions/${encodeURIComponent(sessionId)}`,
        {
          method: 'DELETE',
        }
      ),

    // --- Interactive sessions: rename, artifacts, delete, push → seed, PTY ---
    updateSandbox: (projectId: string, sandboxId: string, payload: { title?: string | null }) =>
      request<SandboxSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}`,
        { method: 'PATCH', body: payload }
      ),
    deleteSandbox: (projectId: string, sandboxId: string) =>
      request<void>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}`,
        { method: 'DELETE' }
      ),
    saveSandboxArtifact: (projectId: string, sandboxId: string, path: string) =>
      request<SandboxSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/artifacts`,
        { method: 'POST', body: { path } }
      ),
    deleteSandboxArtifact: (projectId: string, sandboxId: string, relPath: string) =>
      request<SandboxSession>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/sandboxes/${encodeURIComponent(sandboxId)}/artifacts/${relPath
          .split('/')
          .map(encodeURIComponent)
          .join('/')}`,
        { method: 'DELETE' }
      ),
    draftSessionPush: (projectId: string, kind: 'chat' | 'terminal', sessionId: string) =>
      request<SessionPushDraft>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/push/draft`,
        { method: 'POST' }
      ),
    pushSession: (
      projectId: string,
      kind: 'chat' | 'terminal',
      sessionId: string,
      payload: {
        title: string
        summary: string
        acceptance_criteria: string[]
        target_seed_id?: string | null
      }
    ) =>
      request<SessionPushResult>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/push`,
        { method: 'POST', body: payload }
      ),
    listSessionFiles: (projectId: string, kind: 'chat' | 'terminal', sessionId: string) =>
      request<SessionWorkspaceFilesResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/files`
      ),
    getSessionFile: (
      projectId: string,
      kind: 'chat' | 'terminal',
      sessionId: string,
      path: string
    ) =>
      request<SessionWorkspaceFileContent>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`
      ),
    saveSessionFile: (
      projectId: string,
      kind: 'chat' | 'terminal',
      sessionId: string,
      payload: { path: string; content: string }
    ) =>
      request<SessionWorkspaceFileContent>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/file`,
        { method: 'PUT', body: payload }
      ),
    deleteSessionFile: (
      projectId: string,
      kind: 'chat' | 'terminal',
      sessionId: string,
      path: string
    ) =>
      request<void>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/file?path=${encodeURIComponent(path)}`,
        { method: 'DELETE' }
      ),
    discardSessionChanges: (
      projectId: string,
      kind: 'chat' | 'terminal',
      sessionId: string,
      path?: string | null
    ) =>
      request<SessionWorkspaceFilesResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/discard`,
        { method: 'POST', body: { path: path ?? null } }
      ),
    openSessionVsCode: (
      projectId: string,
      kind: 'chat' | 'terminal',
      sessionId: string,
      theme?: 'dark' | 'light'
    ) =>
      request<SessionVsCodeInfo>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/${
          kind === 'chat' ? 'agent-sessions' : 'sandboxes'
        }/${encodeURIComponent(sessionId)}/vscode${
          theme ? `?theme=${encodeURIComponent(theme)}` : ''
        }`,
        { method: 'POST' }
      ),
    sessionPtyUrl: (projectId: string, kind: 'chat' | 'terminal', sessionId: string) => {
      const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
      return `${proto}//${window.location.host}/api/v1/projects/${encodeURIComponent(projectId)}/${
        kind === 'chat' ? 'agent-sessions' : 'sandboxes'
      }/${encodeURIComponent(sessionId)}/pty`
    },
  },

  sessions: {
    list: (params: {
      scope?: 'mine' | 'everyone'
      state?: 'running' | 'ended' | 'all'
      project_id?: string | null
      limit?: number
      offset?: number
    } = {}) => {
      const qs = new URLSearchParams()
      if (params.scope) qs.set('scope', params.scope)
      if (params.state) qs.set('state', params.state)
      if (params.project_id) qs.set('project_id', params.project_id)
      if (params.limit) qs.set('limit', String(params.limit))
      if (typeof params.offset === 'number' && params.offset > 0) {
        qs.set('offset', String(params.offset))
      }
      const suffix = qs.toString() ? `?${qs.toString()}` : ''
      return request<SessionListItem[]>(`/api/v1/sessions${suffix}`)
    },
    get: (kind: 'chat' | 'terminal', sessionId: string) =>
      request<SessionListItem>(
        `/api/v1/sessions/${encodeURIComponent(kind)}/${encodeURIComponent(sessionId)}`
      ),
  },

  receipts: {
    getPublicKey: () => request<ReceiptPublicKey>('/api/v1/receipts/public-key'),
  },

  localWorkspaces: {
    init: (
      projectId: string,
      payload: InitLocalProjectRepositoryRequest = {}
    ) =>
      request<InitLocalProjectRepositoryResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/init-local`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    linkDirectory: (
      projectId: string,
      workspacePath: string,
      defaultBranch = 'main'
    ) =>
      request<InitLocalProjectRepositoryResponse>(
        `/api/v1/projects/${encodeURIComponent(projectId)}/repositories/init-local`,
        {
          method: 'POST',
          body: {
            workspace_path: workspacePath,
            default_branch: defaultBranch,
          },
        }
      ),
    getCloneUrl: (repoSlug: string) =>
      `${typeof window !== 'undefined' ? window.location.origin : ''}/api/v1/local-git/${encodeURIComponent(repoSlug.replace(/\.git$/i, ''))}.git`,
  },

  seeds: {
    list: (
      projectId?: string | null,
      limit = 100,
      offset = 0,
      options?: { openOnly?: boolean }
    ) => {
      const qs = new URLSearchParams({ limit: String(limit) })
      if (projectId) qs.set('project_id', projectId)
      if (offset > 0) qs.set('offset', String(offset))
      if (options?.openOnly) qs.set('open_only', 'true')
      return request<Seed[]>(`/api/v1/seeds?${qs.toString()}`)
    },
    get: (seedId: string) => request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}`),
    getReceipt: (seedId: string) =>
      request<GovernanceReceiptEnvelope>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/receipt`
      ),
    create: (payload: {
      project_id: string
      title: string
      description?: string
      specification?: string
      acceptance_criteria?: string[]
      plan?: string | null
      tags?: string[]
      stage?: SeedStage | null
      fast_track?: boolean
      autonomy?: SeedAutonomy
      budget_cap?: string | number | null
      staged?: boolean | null
      ideate_agent_ids?: string[]
    }) =>
      request<Seed>('/api/v1/seeds', {
        method: 'POST',
        body: payload,
      }),
    start: (seedId: string) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/start`, {
        method: 'POST',
      }),
    update: (
      seedId: string,
      payload: {
        title?: string
        description?: string
        specification?: string
        acceptance_criteria?: string[]
        plan?: string | null
        tags?: string[]
        autonomy?: SeedAutonomy
        budget_cap?: string | number | null
      }
    ) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}`, {
        method: 'PATCH',
        body: payload,
      }),
    advance: (seedId: string, stage?: SeedStage | null, fastTrack?: boolean) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/advance`, {
        method: 'POST',
        body: {
          ...(stage ? { stage } : {}),
          ...(fastTrack ? { fast_track: true } : {}),
        },
      }),
    approveCheckpoint: (seedId: string, approvedThrough: string | number) =>
      request<Seed>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/approve-checkpoint`,
        {
          method: 'POST',
          body: { approved_through: approvedThrough },
        }
      ),
    close: (seedId: string) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/close`, {
        method: 'POST',
      }),
    reopen: (seedId: string) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/reopen`, {
        method: 'POST',
      }),
    /** Stop the agent working on the seed (cancels its run); the seed waits as 'stopped' */
    stop: (seedId: string) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/stop`, {
        method: 'POST',
      }),
    /** Re-queue a stopped seed's current stage */
    resume: (seedId: string) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/resume`, {
        method: 'POST',
      }),
    /** Send a seed back to an earlier stage (resets approvals from there on) */
    moveBack: (seedId: string, payload: { stage: SeedStage; reason: string }) =>
      request<Seed>(`/api/v1/seeds/${encodeURIComponent(seedId)}/move-back`, {
        method: 'POST',
        body: payload,
      }),
    /** Request an additional stage agent run (second opinion) for Ideate, Plan, or Review */
    requestSecondOpinion: (
      seedId: string,
      payload: { agent_id: string; compute_backend_id?: string | null }
    ) =>
      request<Run>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/second-opinion`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    /** Select which agent run's draft (or 'original') is active on the seed for Ideate or Plan */
    selectRun: (seedId: string, runId: string) =>
      request<Seed>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/select-run/${encodeURIComponent(runId)}`,
        {
          method: 'POST',
        }
      ),
    listAttachments: (seedId: string) =>
      request<SeedAttachment[]>(`/api/v1/seeds/${encodeURIComponent(seedId)}/attachments`),
    /** Upload one file (raw body; the server keeps its name and type) */
    uploadAttachment: async (seedId: string, file: File): Promise<SeedAttachment> => {
      const res = await fetch(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/attachments?filename=${encodeURIComponent(file.name || 'pasted-file')}`,
        {
          method: 'POST',
          credentials: 'include',
          headers: {
            'Content-Type': file.type || 'application/octet-stream',
            Accept: 'application/json',
            'X-GitSeed-CSRF': '1',
          },
          body: file,
        }
      )
      if (!res.ok) {
        if (res.status === 401) {
          unauthorizedHandler?.()
        }
        let detail = `Upload failed (HTTP ${res.status})`
        try {
          const data = (await res.json()) as { detail?: unknown }
          if (typeof data?.detail === 'string') detail = data.detail
        } catch {
          /* not JSON */
        }
        throw new ApiError(res.status, detail)
      }
      return (await res.json()) as SeedAttachment
    },
    deleteAttachment: (seedId: string, attachmentId: string) =>
      request<void>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/attachments/${encodeURIComponent(attachmentId)}`,
        { method: 'DELETE' }
      ),
    attachmentDownloadUrl: (seedId: string, attachmentId: string) =>
      `/api/v1/seeds/${encodeURIComponent(seedId)}/attachments/${encodeURIComponent(attachmentId)}/download`,
    listLinks: (seedId: string) =>
      request<SeedLinkItem[]>(`/api/v1/seeds/${encodeURIComponent(seedId)}/links`),
    addLink: (
      seedId: string,
      payload: { target_seed_id: string; kind?: 'related' | 'follow_up' }
    ) =>
      request<SeedLinkItem>(`/api/v1/seeds/${encodeURIComponent(seedId)}/links`, {
        method: 'POST',
        body: payload,
      }),
    deleteLink: (seedId: string, linkId: string) =>
      request<void>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/links/${encodeURIComponent(linkId)}`,
        { method: 'DELETE' }
      ),
    listTasks: (seedId: string) =>
      request<SeedTask[]>(`/api/v1/seeds/${encodeURIComponent(seedId)}/tasks`),
    listRuns: (seedId: string, limit = 200) =>
      request<Run[]>(
        `/api/v1/runs?seed_id=${encodeURIComponent(seedId)}&limit=${limit}`
      ),
    createTask: (
      seedId: string,
      payload: {
        title: string
        description?: string
        position?: number | null
      }
    ) =>
      request<SeedTask>(`/api/v1/seeds/${encodeURIComponent(seedId)}/tasks`, {
        method: 'POST',
        body: payload,
      }),
    updateTask: (
      seedId: string,
      taskId: string,
      payload: {
        title?: string
        description?: string
        position?: number | null
        status?: TaskStatus
      }
    ) =>
      request<SeedTask>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/tasks/${encodeURIComponent(taskId)}`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    deleteTask: (seedId: string, taskId: string) =>
      request<void>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/tasks/${encodeURIComponent(taskId)}`,
        {
          method: 'DELETE',
        }
      ),
    listApprovals: (seedId: string, stage?: SeedStage) =>
      request<Approval[]>(
        stage
          ? `/api/v1/seeds/${encodeURIComponent(seedId)}/approvals?stage=${encodeURIComponent(stage)}`
          : `/api/v1/seeds/${encodeURIComponent(seedId)}/approvals`
      ),
    createApproval: (
      seedId: string,
      payload: {
        decision: ApprovalDecision
        comment?: string | null
      }
    ) =>
      request<Approval>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/approvals`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    listComments: (seedId: string) =>
      request<SeedComment[]>(`/api/v1/seeds/${encodeURIComponent(seedId)}/comments`),
    createComment: (seedId: string, payload: { body: string }) =>
      request<SeedComment>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/comments`,
        {
          method: 'POST',
          body: payload,
        }
      ),
    updateComment: (
      seedId: string,
      commentId: string,
      payload: { body: string }
    ) =>
      request<SeedComment>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/comments/${encodeURIComponent(commentId)}`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    deleteComment: (seedId: string, commentId: string) =>
      request<SeedComment>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/comments/${encodeURIComponent(commentId)}`,
        {
          method: 'DELETE',
        }
      ),
    getChanges: (seedId: string, repoId?: number | null) =>
      request<SeedChangesResponse>(
        repoId
          ? `/api/v1/seeds/${encodeURIComponent(seedId)}/changes?repo_id=${encodeURIComponent(String(repoId))}`
          : `/api/v1/seeds/${encodeURIComponent(seedId)}/changes`
      ),
    createPullRequest: (seedId: string, repoId?: number | null) =>
      request<PullRequestStatus>(
        repoId
          ? `/api/v1/seeds/${encodeURIComponent(seedId)}/pull-request?repo_id=${encodeURIComponent(String(repoId))}`
          : `/api/v1/seeds/${encodeURIComponent(seedId)}/pull-request`,
        {
          method: 'POST',
        }
      ),
    createPullRequestComment: (
      seedId: string,
      payload: {
        body: string
        repo_id?: number | null
        mirror_to_seed?: boolean
      }
    ) =>
      request<PullRequestCommentItem>(
        `/api/v1/seeds/${encodeURIComponent(seedId)}/pull-request/comments`,
        {
          method: 'POST',
          body: payload,
        }
      ),
  },

  runs: {
    list: (limit = 100, offset = 0) =>
      request<Run[]>(
        offset > 0 ? `/api/v1/runs?limit=${limit}&offset=${offset}` : `/api/v1/runs?limit=${limit}`
      ),
    get: (runId: string) => request<Run>(`/api/v1/runs/${encodeURIComponent(runId)}`),
    getLogs: (runId: string, tail = 800) =>
      request<{ run_id: string; status: Run['status']; log_tail: string }>(
        `/api/v1/runs/${encodeURIComponent(runId)}/logs?tail=${tail}`
      ),
    create: (payload: {
      seed_id: string
      task_id?: string | null
      stage?: SeedStage | null
      agent_id: string
      compute_backend_id?: string | null
    }) =>
      request<Run>('/api/v1/runs', {
        method: 'POST',
        body: payload,
      }),
    cancel: (runId: string) =>
      request<Run>(`/api/v1/runs/${encodeURIComponent(runId)}/cancel`, {
        method: 'POST',
      }),
  },

  backends: {
    list: () => request<Backend[]>('/api/v1/backends'),
    get: (backendId: string) =>
      request<Backend>(`/api/v1/backends/${encodeURIComponent(backendId)}`),
    create: (payload: {
      name: string
      type: BackendType
      provider: string
      endpoint?: string | null
      config?: Record<string, unknown>
      secrets?: Record<string, string>
      capacity?: number | null
      access_restricted?: boolean
      enabled?: boolean
    }) =>
      request<Backend>('/api/v1/backends', {
        method: 'POST',
        body: payload,
      }),
    update: (
      backendId: string,
      payload: {
        name?: string
        endpoint?: string | null
        config?: Record<string, unknown>
        secrets?: Record<string, string>
        capacity?: number | null
        access_restricted?: boolean
        enabled?: boolean
      }
    ) =>
      request<Backend>(`/api/v1/backends/${encodeURIComponent(backendId)}`, {
        method: 'PATCH',
        body: payload,
      }),
    delete: (backendId: string) =>
      request<void>(`/api/v1/backends/${encodeURIComponent(backendId)}`, {
        method: 'DELETE',
      }),
    grantAgent: (backendId: string, agentId: string) =>
      request<Backend>(
        `/api/v1/backends/${encodeURIComponent(backendId)}/agents/${encodeURIComponent(agentId)}`,
        {
          method: 'POST',
        }
      ),
    revokeAgent: (backendId: string, agentId: string) =>
      request<Backend>(
        `/api/v1/backends/${encodeURIComponent(backendId)}/agents/${encodeURIComponent(agentId)}`,
        {
          method: 'DELETE',
        }
      ),
    test: (backendId: string) =>
      request<Backend>(`/api/v1/backends/${encodeURIComponent(backendId)}/test`, {
        method: 'POST',
      }),
    discoverRepos: (backendId: string) =>
      request<Backend>(
        `/api/v1/backends/${encodeURIComponent(backendId)}/discover-repos`,
        {
          method: 'POST',
        }
      ),
    createRepo: (
      backendId: string,
      payload: {
        name: string
        description?: string
        private?: boolean
        auto_init?: boolean
      }
    ) =>
      request<{
        full_name: string
        default_branch: string
        private: boolean
        web_url: string | null
        backend: Backend
      }>(`/api/v1/backends/${encodeURIComponent(backendId)}/create-repo`, {
        method: 'POST',
        body: payload,
      }),
    discoverModels: (backendId: string) =>
      request<Backend>(
        `/api/v1/backends/${encodeURIComponent(backendId)}/discover-models`,
        {
          method: 'POST',
        }
      ),
    setSdlcDefault: (backendId: string) =>
      request<Backend>(
        `/api/v1/backends/${encodeURIComponent(backendId)}/sdlc-default`,
        {
          method: 'POST',
        }
      ),
    probe: (payload: {
      type: BackendType
      provider: string
      endpoint?: string | null
      config?: Record<string, unknown>
      secrets?: Record<string, string>
    }) =>
      request<{
        health: 'unknown' | 'healthy' | 'degraded' | 'unreachable'
        latency_ms: number | null
        error: string | null
        models?: Array<{ id: string; owned_by?: string; enabled?: boolean }>
        repositories?: Array<Record<string, unknown>>
      }>('/api/v1/backends/probe', {
        method: 'POST',
        body: payload,
      }),
    startCliAuth: (backendId: string) =>
      request<{
        status: string
        auth_url: string | null
        user_code: string | null
        log_output: string
        authenticated: boolean
        error: string | null
        backend: Backend | null
      }>(`/api/v1/backends/${encodeURIComponent(backendId)}/cli-auth/start`, {
        method: 'POST',
      }),
    getCliAuthStatus: (backendId: string) =>
      request<{
        status: string
        auth_url: string | null
        user_code: string | null
        log_output: string
        authenticated: boolean
        error: string | null
        backend: Backend | null
      }>(`/api/v1/backends/${encodeURIComponent(backendId)}/cli-auth/status`),
    submitCliAuthCode: (backendId: string, code: string) =>
      request<{
        status: string
        auth_url: string | null
        user_code: string | null
        log_output: string
        authenticated: boolean
        error: string | null
        backend: Backend | null
      }>(`/api/v1/backends/${encodeURIComponent(backendId)}/cli-auth/submit`, {
        method: 'POST',
        body: { code },
      }),
    cancelCliAuth: (backendId: string) =>
      request<{
        status: string
        auth_url: string | null
        user_code: string | null
        log_output: string
        authenticated: boolean
        error: string | null
        backend: Backend | null
      }>(`/api/v1/backends/${encodeURIComponent(backendId)}/cli-auth/cancel`, {
        method: 'POST',
      }),
    startGitHubAppManifest: (payload: {
      backend_id?: string | null
      name?: string | null
      organization?: string | null
      endpoint?: string | null
      origin?: string | null
    }) =>
      request<{
        action_url: string
        manifest: string
        state: string
        backend: Backend
      }>('/api/v1/backends/github-app/manifest-start', {
        method: 'POST',
        body: payload,
      }),
    syncGitHubApp: (backendId: string) =>
      request<Backend>(
        `/api/v1/backends/${encodeURIComponent(backendId)}/github-app/sync`,
        {
          method: 'POST',
        }
      ),
  },

  agents: {
    list: (includeArchived = false) =>
      request<Agent[]>(`/api/v1/agents?include_archived=${includeArchived}`),
    benchmarks: (params?: { project_id?: string | null; stage?: SeedStage | null }) => {
      const qs = new URLSearchParams()
      if (params?.project_id) qs.set('project_id', params.project_id)
      if (params?.stage) qs.set('stage', params.stage)
      const suffix = qs.toString() ? `?${qs.toString()}` : ''
      return request<AgentBenchmarksResponse>(`/api/v1/agents/benchmarks${suffix}`)
    },
    runtimeAvailability: () =>
      request<AgentRuntimeAvailability>('/api/v1/agents/runtimes/availability'),
    get: (agentId: string) => request<Agent>(`/api/v1/agents/${encodeURIComponent(agentId)}`),
    create: (payload: {
      name: string
      handle?: string | null
      kind?: AgentKind
      template?: string | null
      instructions?: string
      runtime?: AgentRuntime
      model?: string
      skills?: string[]
      tools?: string[]
      enabled?: boolean
    }) =>
      request<Agent>('/api/v1/agents', {
        method: 'POST',
        body: payload,
      }),
    update: (
      agentId: string,
      payload: {
        name?: string
        handle?: string
        kind?: AgentKind
        template?: string | null
        instructions?: string
        runtime?: AgentRuntime
        model?: string
        skills?: string[]
        tools?: string[]
        enabled?: boolean
      }
    ) =>
      request<Agent>(`/api/v1/agents/${encodeURIComponent(agentId)}`, {
        method: 'PATCH',
        body: payload,
      }),
    archive: (agentId: string) =>
      request<Agent>(`/api/v1/agents/${encodeURIComponent(agentId)}/archive`, {
        method: 'POST',
      }),
    restore: (agentId: string) =>
      request<Agent>(`/api/v1/agents/${encodeURIComponent(agentId)}/restore`, {
        method: 'POST',
      }),
    chatCoordinator: (payload: {
      messages: Array<{ role: 'user' | 'assistant'; content: string }>
      project_id?: string | null
      seed_id?: string | null
      mode?: 'general' | 'brainstorm'
      propose_seeds?: boolean
    }) =>
      request<{
        reply: string
        agent_handle: string
        model: string
        input_tokens: number
        output_tokens: number
        proposed_seeds?: ProposedSeedDraft[]
        referenced_files?: string[]
      }>('/api/v1/agents/coordinator/chat', {
        method: 'POST',
        body: payload,
      }),
  },

  skills: {
    list: (includeArchived = false) =>
      request<Skill[]>(`/api/v1/skills?include_archived=${includeArchived}`),
    get: (skillId: string) => request<Skill>(`/api/v1/skills/${encodeURIComponent(skillId)}`),
    create: (payload: {
      name: string
      slug?: string | null
      description?: string
      content?: string
    }) =>
      request<Skill>('/api/v1/skills', {
        method: 'POST',
        body: payload,
      }),
    update: (
      skillId: string,
      payload: {
        name?: string
        slug?: string
        description?: string
        content?: string
      }
    ) =>
      request<Skill>(`/api/v1/skills/${encodeURIComponent(skillId)}`, {
        method: 'PATCH',
        body: payload,
      }),
    archive: (skillId: string) =>
      request<Skill>(`/api/v1/skills/${encodeURIComponent(skillId)}/archive`, {
        method: 'POST',
      }),
    restore: (skillId: string) =>
      request<Skill>(`/api/v1/skills/${encodeURIComponent(skillId)}/restore`, {
        method: 'POST',
      }),
  },

  policies: {
    list: () => request<StagePolicy[]>('/api/v1/policies'),
    updateOrg: (
      stage: SeedStage,
      payload: {
        enabled?: boolean
        agent_id?: string | null
        requires_approval?: boolean
        min_approvers?: number
        min_approver_role?: ProjectRole
        allow_auto_approve?: boolean
        allow_self_approval?: boolean
        allow_second_opinions?: boolean
        max_attempts?: number
        timeout_minutes?: number
        min_acceptance_criteria?: number | null
        max_acceptance_criteria?: number | null
        max_tasks?: number | null
        instructions?: string | null
      }
    ) =>
      request<StagePolicy>(`/api/v1/policies/${encodeURIComponent(stage)}`, {
        method: 'PATCH',
        body: payload,
      }),
    listProject: (projectId: string) =>
      request<StagePolicy[]>(`/api/v1/policies/projects/${encodeURIComponent(projectId)}`),
    updateProject: (
      projectId: string,
      stage: SeedStage,
      payload: {
        enabled?: boolean | null
        agent_id?: string | null
        requires_approval?: boolean | null
        min_approvers?: number | null
        min_approver_role?: ProjectRole | null
        allow_auto_approve?: boolean | null
        allow_self_approval?: boolean | null
        allow_second_opinions?: boolean | null
        max_attempts?: number | null
        timeout_minutes?: number | null
        min_acceptance_criteria?: number | null
        max_acceptance_criteria?: number | null
        max_tasks?: number | null
        instructions?: string | null
      }
    ) =>
      request<StagePolicy>(
        `/api/v1/policies/projects/${encodeURIComponent(projectId)}/${encodeURIComponent(stage)}`,
        {
          method: 'PATCH',
          body: payload,
        }
      ),
    resetProject: (projectId: string, stage: SeedStage) =>
      request<StagePolicy>(
        `/api/v1/policies/projects/${encodeURIComponent(projectId)}/${encodeURIComponent(stage)}`,
        {
          method: 'DELETE',
        }
      ),
  },

  settings: {
    get: () => request<OrgSettings>('/api/v1/settings'),
    update: (payload: Partial<OrgSettings>) =>
      request<OrgSettings>('/api/v1/settings', {
        method: 'PATCH',
        body: payload,
      }),
  },

  activity: {
    list: (
      limit = 25,
      params?: {
        seed_id?: string | null
        project_id?: string | null
        offset?: number
        before_id?: number
      }
    ) => {
      const qs = new URLSearchParams({ limit: String(limit) })
      if (params?.seed_id) qs.set('seed_id', params.seed_id)
      if (params?.project_id) qs.set('project_id', params.project_id)
      if (typeof params?.offset === 'number' && params.offset > 0) {
        qs.set('offset', String(params.offset))
      }
      if (typeof params?.before_id === 'number') {
        qs.set('before_id', String(params.before_id))
      }
      return request<ActivityEvent[]>(`/api/v1/activity?${qs.toString()}`)
    },
  },
}
