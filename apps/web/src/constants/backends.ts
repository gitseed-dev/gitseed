import type { BackendType } from '../types'

export interface ProviderSecretField {
  key: string
  label: string
  required: boolean
  placeholder: string
}

export interface ProviderDefinition {
  value: string
  label: string
  defaultEndpoint: string
  endpointLabel: string
  endpointPlaceholder: string
  authHelpText?: string
  authHelpUrl?: string
  authHelpLabel?: string
  supportsCliOAuth?: boolean
  cliBinary?: string
  cliAuthHelpText?: string
  secrets: ProviderSecretField[]
}

export const BACKEND_TYPE_ORDER: Record<BackendType, number> = {
  git: 1,
  inference: 2,
  compute: 3,
  tools: 4,
}

export const BACKEND_PROVIDER_DEFINITIONS: Record<BackendType, ProviderDefinition[]> = {
  git: [
    {
      value: 'bitbucket',
      label: 'Bitbucket',
      defaultEndpoint: 'https://api.bitbucket.org/2.0',
      endpointLabel: 'Bitbucket API URL',
      endpointPlaceholder: 'https://api.bitbucket.org/2.0',
      authHelpText:
        'Click below to create a Bitbucket App Password or Repository/Workspace Access Token with repository read/write and pull request permissions.',
      authHelpUrl: 'https://bitbucket.org/account/settings/app-passwords/new?label=GitSeed+Control+Plane',
      authHelpLabel: 'Create Bitbucket App Password',
      secrets: [
        {
          key: 'token',
          label: 'Repository / Workspace Token',
          required: true,
          placeholder: 'ATBB...',
        },
        {
          key: 'webhook_secret',
          label: 'Webhook Secret (optional)',
          required: false,
          placeholder: 'Webhook secret',
        },
      ],
    },
    {
      value: 'github',
      label: 'GitHub (OAuth / Personal Access Token)',
      defaultEndpoint: 'https://api.github.com',
      endpointLabel: 'API Endpoint',
      endpointPlaceholder: 'https://api.github.com',
      authHelpText:
        'Click below to open GitHub with the required scopes ("repo", "read:org", "workflow") pre-selected, or switch to Browser Sign-In (OAuth) above to authorize in one click.',
      authHelpUrl:
        'https://github.com/settings/tokens/new?description=GitSeed+Control+Plane&scopes=repo,read:org,workflow',
      authHelpLabel: 'Generate Pre-Scoped GitHub Token',
      supportsCliOAuth: true,
      cliBinary: 'gh',
      cliAuthHelpText:
        'Sign in with your GitHub account in your browser via GitHub Device OAuth (scopes: repo, read:org, workflow). The resulting token is encrypted at rest and repositories are synced automatically.',
      secrets: [
        {
          key: 'token',
          label: 'Personal Access Token / App Token',
          required: true,
          placeholder: 'ghp_... or github_pat_...',
        },
        {
          key: 'webhook_secret',
          label: 'Webhook Secret (optional)',
          required: false,
          placeholder: 'whsec_...',
        },
      ],
    },
    {
      value: 'github-app',
      label: 'GitHub App (Recommended)',
      defaultEndpoint: 'https://api.github.com',
      endpointLabel: 'API Endpoint',
      endpointPlaceholder: 'https://api.github.com',
      authHelpText:
        'Create or open your GitHub App under Settings → Developer settings → GitHub Apps, generate a Private Key (.pem), and copy the App ID and Installation ID.',
      authHelpUrl: 'https://github.com/settings/apps',
      authHelpLabel: 'Open GitHub Apps Settings',
      secrets: [
        {
          key: 'private_key',
          label: 'App Private Key (PEM)',
          required: true,
          placeholder: '-----BEGIN RSA PRIVATE KEY----- ...',
        },
        {
          key: 'webhook_secret',
          label: 'Webhook Secret (optional)',
          required: false,
          placeholder: 'whsec_...',
        },
      ],
    },
    {
      value: 'github-enterprise',
      label: 'GitHub Enterprise',
      defaultEndpoint: 'https://github.internal.example.com/api/v3',
      endpointLabel: 'Enterprise API URL',
      endpointPlaceholder: 'https://github.internal.example.com/api/v3',
      authHelpText:
        'Click below to open your GitHub Enterprise instance with the required scopes ("repo", "read:org", "workflow") pre-selected.',
      authHelpUrl:
        'https://github.internal.example.com/settings/tokens/new?description=GitSeed+Control+Plane&scopes=repo,read:org,workflow',
      authHelpLabel: 'Generate Pre-Scoped Enterprise Token',
      secrets: [
        {
          key: 'token',
          label: 'Personal Access Token',
          required: true,
          placeholder: 'ghp_...',
        },
        {
          key: 'webhook_secret',
          label: 'Webhook Secret (optional)',
          required: false,
          placeholder: 'whsec_...',
        },
      ],
    },
    {
      value: 'gitlab',
      label: 'GitLab',
      defaultEndpoint: 'https://gitlab.com/api/v4',
      endpointLabel: 'GitLab API URL',
      endpointPlaceholder: 'https://gitlab.com/api/v4',
      authHelpText:
        'Click below to open GitLab with the required scopes ("api", "read_repository", "write_repository") pre-selected, or create a Project/Group Access Token.',
      authHelpUrl:
        'https://gitlab.com/-/user_settings/personal_access_tokens?name=GitSeed+Control+Plane&scopes=api,read_repository,write_repository',
      authHelpLabel: 'Generate Pre-Scoped GitLab Token',
      secrets: [
        {
          key: 'token',
          label: 'Access Token',
          required: true,
          placeholder: 'glpat-...',
        },
        {
          key: 'webhook_secret',
          label: 'Webhook Secret (optional)',
          required: false,
          placeholder: 'Webhook secret token',
        },
      ],
    },
    {
      value: 'git',
      label: 'Self-Hosted Git Server',
      defaultEndpoint: 'https://git.internal.example.com',
      endpointLabel: 'Git Server URL',
      endpointPlaceholder: 'https://git.internal.example.com',
      authHelpText:
        'Provide an HTTP access token or an OpenSSH private key with read/write access to your self-hosted repositories.',
      secrets: [
        {
          key: 'token',
          label: 'HTTP Access Token (optional)',
          required: false,
          placeholder: 'Bearer or Basic token',
        },
        {
          key: 'ssh_key',
          label: 'SSH Private Key (optional)',
          required: false,
          placeholder: '-----BEGIN OPENSSH PRIVATE KEY----- ...',
        },
      ],
    },
  ],
  inference: [
    {
      value: 'anthropic',
      label: 'Anthropic (Claude)',
      defaultEndpoint: 'https://api.anthropic.com',
      endpointLabel: 'API Base URL',
      endpointPlaceholder: 'https://api.anthropic.com',
      authHelpText:
        'Powers Claude Code (ANTHROPIC_API_KEY), OpenCode, and the LiteLLM gateway. Generate an API key in the Anthropic Console under Settings → API Keys.',
      authHelpUrl: 'https://console.anthropic.com/settings/keys',
      authHelpLabel: 'Get Anthropic API Key',
      supportsCliOAuth: true,
      cliBinary: 'claude',
      cliAuthHelpText:
        'Sign in with your Claude Pro / Max / Team subscription inside an isolated runner container, or paste a long-lived setup token from "claude setup-token" (sk-ant-oat01-...). Credentials are encrypted at rest and injected into each run container.',
      secrets: [
        {
          key: 'api_key',
          label: 'Anthropic API Key',
          required: true,
          placeholder: 'sk-ant-api03-...',
        },
      ],
    },
    {
      value: 'custom',
      label: 'Custom OpenAI-Compatible Gateway',
      defaultEndpoint: 'https://models.internal.example.com/v1',
      endpointLabel: 'Gateway Base URL',
      endpointPlaceholder: 'https://models.internal.example.com/v1',
      authHelpText:
        'Provide the Bearer API key expected in the Authorization header by your OpenAI-compatible gateway, or leave blank if unauthenticated.',
      secrets: [
        {
          key: 'api_key',
          label: 'API Key (optional)',
          required: false,
          placeholder: 'sk-...',
        },
      ],
    },
    {
      value: 'antigravity',
      label: 'Google Antigravity (agy CLI)',
      defaultEndpoint: 'https://generativelanguage.googleapis.com/v1beta',
      endpointLabel: 'Antigravity / Gemini API Base URL',
      endpointPlaceholder: 'https://generativelanguage.googleapis.com/v1beta',
      authHelpText:
        'Powers Antigravity (agy) agents. Choose API Key (BYOK) to route through Google AI Studio / LiteLLM, or switch to CLI Subscription Login (OAuth) to sign in with your Google account directly inside a runner container.',
      authHelpUrl: 'https://aistudio.google.com/app/apikey',
      authHelpLabel: 'Get Google AI Studio API Key',
      supportsCliOAuth: true,
      cliBinary: 'agy',
      cliAuthHelpText:
        'Launches an isolated runner container to complete Google OAuth sign-in for Antigravity ("agy") and stores the resulting ~/.gemini/antigravity-cli OAuth credential bundle encrypted in GitSeed.',
      secrets: [
        {
          key: 'api_key',
          label: 'Antigravity / Google API Key',
          required: false,
          placeholder: 'AIzaSy...',
        },
      ],
    },
    {
      value: 'gemini',
      label: 'Google Gemini (Gemini CLI & API)',
      defaultEndpoint: 'https://generativelanguage.googleapis.com/v1beta',
      endpointLabel: 'Gemini API Base URL',
      endpointPlaceholder: 'https://generativelanguage.googleapis.com/v1beta',
      authHelpText:
        'Powers Google Gemini CLI (GEMINI_API_KEY), Antigravity BYOK, OpenCode, and the LiteLLM gateway. Create a Gemini API key in Google AI Studio.',
      authHelpUrl: 'https://aistudio.google.com/app/apikey',
      authHelpLabel: 'Get Gemini API Key',
      supportsCliOAuth: true,
      cliBinary: 'gemini',
      cliAuthHelpText:
        'Sign in with your Google Account (Gemini Code Assist / Google One AI Premium) inside an isolated runner container. Captures and encrypts ~/.gemini/oauth_creds.json for headless agent runs.',
      secrets: [
        {
          key: 'api_key',
          label: 'Google Gemini API Key',
          required: true,
          placeholder: 'AIzaSy...',
        },
      ],
    },
    {
      value: 'litellm',
      label: 'LiteLLM (existing gateway)',
      defaultEndpoint: 'http://litellm:4000',
      endpointLabel: 'LiteLLM Proxy URL',
      endpointPlaceholder: 'http://litellm:4000',
      authHelpText:
        'Connect an external LiteLLM proxy using its master key (LITELLM_MASTER_KEY) or a virtual key generated from the LiteLLM Admin UI.',
      authHelpUrl: 'https://docs.litellm.ai/docs/proxy/virtual_keys',
      authHelpLabel: 'LiteLLM Virtual Keys Docs',
      secrets: [
        {
          key: 'api_key',
          label: 'LiteLLM Master / Virtual Key',
          required: true,
          placeholder: 'sk-litellm-...',
        },
      ],
    },
    {
      value: 'lmstudio',
      label: 'LM Studio',
      defaultEndpoint: 'http://localhost:1234/v1',
      endpointLabel: 'LM Studio Server URL',
      endpointPlaceholder: 'http://localhost:1234/v1',
      authHelpText:
        'LM Studio local servers accept any non-empty string (default "lm-studio") unless token authentication is enabled in the Developer tab.',
      authHelpUrl: 'https://lmstudio.ai/docs/app/api',
      authHelpLabel: 'LM Studio API Docs',
      secrets: [
        {
          key: 'api_key',
          label: 'API Key (optional)',
          required: false,
          placeholder: 'lm-studio',
        },
      ],
    },
    {
      value: 'ollama',
      label: 'Ollama (Local Engine)',
      defaultEndpoint: 'http://localhost:11434',
      endpointLabel: 'Ollama Host URL',
      endpointPlaceholder: 'http://localhost:11434',
      authHelpText:
        'Local Ollama servers do not require an API key by default. Only provide a key if your Ollama endpoint sits behind an authenticated reverse proxy.',
      authHelpUrl: 'https://ollama.com',
      authHelpLabel: 'Ollama Documentation',
      secrets: [
        {
          key: 'api_key',
          label: 'API Key (optional)',
          required: false,
          placeholder: 'Optional proxy token',
        },
      ],
    },
    {
      value: 'openai',
      label: 'OpenAI',
      defaultEndpoint: 'https://api.openai.com/v1',
      endpointLabel: 'API Base URL',
      endpointPlaceholder: 'https://api.openai.com/v1',
      authHelpText:
        'Powers Codex CLI (OPENAI_API_KEY), OpenCode, and the LiteLLM gateway. Generate a project API key in the OpenAI Platform dashboard.',
      authHelpUrl: 'https://platform.openai.com/api-keys',
      authHelpLabel: 'Get OpenAI API Key',
      supportsCliOAuth: true,
      cliBinary: 'codex',
      cliAuthHelpText:
        'Sign in with your ChatGPT Plus / Pro / Team account via "codex login" inside an isolated runner container. Captures and encrypts ~/.codex/auth.json for headless Codex runs.',
      secrets: [
        {
          key: 'api_key',
          label: 'OpenAI API Key',
          required: true,
          placeholder: 'sk-proj-...',
        },
      ],
    },
    {
      value: 'vllm',
      label: 'vLLM Inference Server',
      defaultEndpoint: 'http://localhost:8000/v1',
      endpointLabel: 'vLLM OpenAI-Compatible URL',
      endpointPlaceholder: 'http://localhost:8000/v1',
      authHelpText:
        'Optional bearer token matching the --api-key flag passed to "vllm serve". Leave blank if authentication is not enabled on your vLLM server.',
      authHelpUrl: 'https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html',
      authHelpLabel: 'vLLM Server Docs',
      secrets: [
        {
          key: 'api_key',
          label: 'API Key (optional)',
          required: false,
          placeholder: 'Optional bearer token',
        },
      ],
    },
    {
      value: 'xai',
      label: 'xAI (Grok API via OpenCode)',
      defaultEndpoint: 'https://api.x.ai/v1',
      endpointLabel: 'xAI API Base URL',
      endpointPlaceholder: 'https://api.x.ai/v1',
      authHelpText:
        'Powers OpenCode SDLC agents and the LiteLLM gateway (or custom Grok Build runner images via XAI_API_KEY). Create an API key in the xAI Console under API Keys.',
      authHelpUrl: 'https://console.x.ai',
      authHelpLabel: 'Get xAI API Key',
      supportsCliOAuth: false,
      secrets: [
        {
          key: 'api_key',
          label: 'xAI API Key',
          required: true,
          placeholder: 'xai-...',
        },
      ],
    },
  ],
  compute: [
    {
      value: 'docker',
      label: 'Docker Engine (Socket / Daemon)',
      defaultEndpoint: 'unix:///var/run/docker.sock',
      endpointLabel: 'Docker Socket or Daemon Endpoint',
      endpointPlaceholder: 'unix:///var/run/docker.sock or tcp://docker-host:2376',
      authHelpText:
        'Local Unix sockets do not need TLS credentials. For remote tcp:// daemons with TLS verify enabled, paste your client certificate, private key, and CA.',
      authHelpUrl: 'https://docs.docker.com/engine/security/protect-access/',
      authHelpLabel: 'Docker TLS Docs',
      secrets: [
        {
          key: 'tls_cert',
          label: 'TLS Client Certificate (optional)',
          required: false,
          placeholder: 'PEM certificate content',
        },
        {
          key: 'tls_key',
          label: 'TLS Client Key (optional)',
          required: false,
          placeholder: 'PEM private key content',
        },
        {
          key: 'tls_ca',
          label: 'TLS CA Certificate (optional)',
          required: false,
          placeholder: 'PEM CA certificate content',
        },
      ],
    },
    {
      value: 'kubernetes',
      label: 'Kubernetes Cluster (Batch Jobs)',
      defaultEndpoint: 'https://kubernetes.default.svc',
      endpointLabel: 'Kubernetes API Server URL',
      endpointPlaceholder: 'https://k8s-api.example.com:6443',
      authHelpText:
        'Provide a ServiceAccount bearer token with permissions to create and watch Pods/Jobs in the target namespace (or leave blank when running in-cluster).',
      authHelpUrl: 'https://kubernetes.io/docs/reference/access-authn-authz/service-accounts-admin/',
      authHelpLabel: 'Kubernetes ServiceAccounts Docs',
      secrets: [
        {
          key: 'token',
          label: 'ServiceAccount Bearer Token (optional in-cluster)',
          required: false,
          placeholder: 'eyJhbGciOiJSUzI1NiIsImtpZCI6...',
        },
        {
          key: 'ca_cert',
          label: 'Cluster CA Certificate (optional)',
          required: false,
          placeholder: '-----BEGIN CERTIFICATE----- ...',
        },
      ],
    },
  ],
  tools: [
    {
      value: 'mcp-sse',
      label: 'MCP Server (HTTP / SSE)',
      defaultEndpoint: 'http://localhost:8080/sse',
      endpointLabel: 'SSE / HTTP Endpoint URL',
      endpointPlaceholder: 'http://localhost:8080/sse',
      authHelpText:
        'Optional authentication credentials sent when connecting to a remote MCP HTTP/SSE endpoint (X-API-Key header or Authorization: Bearer token).',
      secrets: [
        {
          key: 'api_key',
          label: 'API Key (optional)',
          required: false,
          placeholder: 'Optional X-API-Key header secret',
        },
        {
          key: 'bearer_token',
          label: 'Bearer Token (optional)',
          required: false,
          placeholder: 'Optional Authorization Bearer token',
        },
      ],
    },
    {
      value: 'mcp-stdio',
      label: 'MCP Stdio Subprocess',
      defaultEndpoint: 'npx -y @modelcontextprotocol/server-filesystem',
      endpointLabel: 'Command / Binary',
      endpointPlaceholder: 'npx -y @modelcontextprotocol/server-filesystem',
      authHelpText:
        'Optional secret injected into the MCP subprocess environment as MCP_ENV_SECRET.',
      secrets: [
        {
          key: 'env_secret',
          label: 'Environment Secret (optional)',
          required: false,
          placeholder: 'Injected as MCP_ENV_SECRET',
        },
      ],
    },
  ],
}

export const DEFAULT_CLOUD_MODELS: Record<string, string[]> = {
  anthropic: [
    'claude-sonnet-5-5',
    'claude-opus-5-5',
    'claude-haiku-5-5',
    'claude-sonnet-4-6',
    'claude-opus-4-6',
  ],
  openai: [
    'gpt-5.4',
    'gpt-5.3-codex',
    'gpt-5.2',
    'gpt-5.2-codex',
    'gpt-5.2-pro',
  ],
  gemini: [
    'gemini-4-argon',
    'gemini-4-hydrogen',
    'gemini-4-carbon',
    'gemini-3.8-flash',
    'gemini-3.5-flash',
    'gemini-3.1-pro',
    'gemini-3.1-flash-lite',
  ],
  antigravity: [
    'gemini-4-argon',
    'gemini-4-hydrogen',
    'gemini-4-carbon',
    'gemini-3.8-flash',
    'gemini-3.5-flash',
    'gemini-3.1-pro',
    'pro',
    'flash',
    'flash_lite',
  ],
  xai: [
    'grok-4.7',
    'grok-4.6',
    'grok-4.5',
    'grok-4.3',
    'grok-4.20-reasoning',
  ],
}

export const CORE_BACKEND_REQUIREMENTS: { type: 'compute' | 'inference' | 'git'; label: string }[] = [
  { type: 'compute', label: 'Compute' },
  { type: 'inference', label: 'Inference' },
  { type: 'git', label: 'Git' },
]
