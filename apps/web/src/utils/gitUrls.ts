import type { Backend, ProjectRepository } from '../types'

/**
 * Sanitizes untrusted external URLs rendered in `<a href>` or `window.open(...)`
 * against `javascript:`, `data:`, `vbscript:`, `file:`, and relative/malformed URLs.
 * Only trimmed `http:` and `https:` URLs are permitted.
 */
export function safeExternalUrl(raw?: string | null): string | null {
  if (!raw || typeof raw !== 'string') return null
  const trimmed = raw.trim()
  if (!trimmed) return null
  try {
    const parsed = new URL(trimmed)
    if (parsed.protocol === 'http:' || parsed.protocol === 'https:') {
      return trimmed
    }
    return null
  } catch {
    return null
  }
}

const CROSS_ORIGIN_VSCODE_SANDBOX =
  'allow-scripts allow-same-origin allow-forms allow-modals allow-downloads allow-popups'
const SAME_ORIGIN_VSCODE_SANDBOX =
  'allow-scripts allow-forms allow-modals allow-downloads allow-popups'

function isLoopbackHostname(hostname: string): boolean {
  const normalized = hostname.trim().toLowerCase()
  return (
    normalized === 'localhost' ||
    normalized === '127.0.0.1' ||
    normalized === '[::1]' ||
    normalized === '::1'
  )
}

/**
 * Resolves the embedded VS Code Web iframe `src` and `sandbox` attributes:
 * 1. Rewrites loopback (`127.0.0.1` / `localhost`) VS Code URLs to the browser's
 *    current hostname (preserving the dedicated sandbox port, e.g. `:8423`) when
 *    accessed from a LAN, Tailscale, or remote hostname.
 * 2. Strips `allow-same-origin` if the resolved iframe URL shares the exact same
 *    origin as the parent control plane UI so the iframe cannot escape its sandbox
 *    or access parent cookies/DOM.
 */
export function resolveVsCodeIframeProps(
  rawUrl: string,
  windowOrigin?: string
): { url: string; sandbox: string } {
  const fallbackOrigin =
    windowOrigin ||
    (typeof window !== 'undefined' && window.location?.origin
      ? window.location.origin
      : 'http://localhost:8421')

  let originUrl: URL
  try {
    originUrl = new URL(fallbackOrigin)
  } catch {
    originUrl = new URL('http://localhost:8421')
  }

  let parsed: URL
  try {
    parsed = new URL(rawUrl.trim(), originUrl.origin)
  } catch {
    return { url: 'about:blank', sandbox: '' }
  }

  if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
    return { url: 'about:blank', sandbox: '' }
  }

  if (
    isLoopbackHostname(parsed.hostname) &&
    !isLoopbackHostname(originUrl.hostname)
  ) {
    parsed.hostname = originUrl.hostname
    if (originUrl.protocol === 'https:') {
      parsed.protocol = 'https:'
    }
  }

  const isSameOrigin = parsed.origin === originUrl.origin
  return {
    url: parsed.toString(),
    sandbox: isSameOrigin
      ? SAME_ORIGIN_VSCODE_SANDBOX
      : CROSS_ORIGIN_VSCODE_SANDBOX,
  }
}

export function resolveGitWebBase(
  provider?: string | null,
  endpoint?: string | null
): string | null {
  const p = (provider || 'github').trim().toLowerCase()
  if (p === 'local') {
    return null
  }

  const rawInput = (endpoint || '').trim().replace(/\/+$/, '')
  let raw = ''
  if (rawInput) {
    const validatedRaw = safeExternalUrl(rawInput)
    if (!validatedRaw) {
      return null
    }
    raw = validatedRaw.replace(/\/+$/, '')
  }

  let candidate: string
  if (p === 'github' || p === 'github-enterprise' || p === 'github-app') {
    if (!raw || raw === 'https://api.github.com') {
      candidate = 'https://github.com'
    } else if (raw.endsWith('/api/v3')) {
      candidate = raw.slice(0, -'/api/v3'.length)
    } else if (raw.startsWith('https://api.')) {
      candidate = `https://${raw.slice('https://api.'.length)}`
    } else {
      candidate = raw
    }
  } else if (p === 'gitlab') {
    if (!raw) {
      candidate = 'https://gitlab.com'
    } else if (raw.endsWith('/api/v4')) {
      candidate = raw.slice(0, -'/api/v4'.length)
    } else {
      candidate = raw
    }
  } else if (p === 'bitbucket') {
    if (!raw || raw.includes('api.bitbucket.org')) {
      candidate = 'https://bitbucket.org'
    } else if (raw.endsWith('/2.0')) {
      candidate = raw.slice(0, -'/2.0'.length)
    } else {
      candidate = raw
    }
  } else {
    candidate = raw || 'https://github.com'
  }

  return safeExternalUrl(candidate.replace(/\/+$/, ''))
}

export function buildGitWebUrl(options: {
  provider?: string | null
  endpoint?: string | null
  fullName: string
  ref?: string | null
  path?: string | null
  isDir?: boolean
  commitSha?: string | null
}): string | null {
  const p = (options.provider || 'github').trim().toLowerCase()
  if (p === 'local') {
    return null
  }
  const cleanRepo = options.fullName.trim().replace(/^\/+|\/+$/g, '')
  if (!cleanRepo || cleanRepo.toLowerCase().startsWith('local/')) {
    return null
  }
  const base = resolveGitWebBase(p, options.endpoint)
  if (!base) {
    return null
  }
  const cleanPath = (options.path || '').trim().replace(/^\/+|\/+$/g, '')
  const isDir = options.isDir ?? true

  if (options.commitSha) {
    const sha = encodeURIComponent(options.commitSha.trim())
    if (p === 'gitlab') {
      return safeExternalUrl(`${base}/${cleanRepo}/-/commit/${sha}`)
    }
    if (p === 'bitbucket') {
      return safeExternalUrl(`${base}/${cleanRepo}/commits/${sha}`)
    }
    return safeExternalUrl(`${base}/${cleanRepo}/commit/${sha}`)
  }

  if (!options.ref) {
    return safeExternalUrl(`${base}/${cleanRepo}`)
  }

  const encodedRef = options.ref
    .split('/')
    .map((segment) => encodeURIComponent(segment))
    .join('/')
  const encodedPath = cleanPath
    ? cleanPath
        .split('/')
        .map((segment) => encodeURIComponent(segment))
        .join('/')
    : ''

  if (p === 'gitlab') {
    if (!encodedPath) {
      return safeExternalUrl(`${base}/${cleanRepo}/-/tree/${encodedRef}`)
    }
    return safeExternalUrl(
      `${base}/${cleanRepo}/-/${isDir ? 'tree' : 'blob'}/${encodedRef}/${encodedPath}`
    )
  }

  if (p === 'bitbucket') {
    if (!encodedPath) {
      return safeExternalUrl(`${base}/${cleanRepo}/src/${encodedRef}`)
    }
    return safeExternalUrl(`${base}/${cleanRepo}/src/${encodedRef}/${encodedPath}`)
  }

  if (!encodedPath) {
    return safeExternalUrl(`${base}/${cleanRepo}/tree/${encodedRef}`)
  }
  return safeExternalUrl(
    `${base}/${cleanRepo}/${isDir ? 'tree' : 'blob'}/${encodedRef}/${encodedPath}`
  )
}

export function buildRepoWebUrl(
  repo: Pick<ProjectRepository, 'full_name' | 'default_branch'>,
  backend?: Pick<Backend, 'provider' | 'endpoint'> | null,
  options?: {
    ref?: string | null
    path?: string | null
    isDir?: boolean
    commitSha?: string | null
  }
): string | null {
  if (
    backend?.provider?.trim().toLowerCase() === 'local' ||
    repo.full_name.trim().replace(/^\/+/, '').toLowerCase().startsWith('local/')
  ) {
    return null
  }
  return buildGitWebUrl({
    provider: backend?.provider,
    endpoint: backend?.endpoint,
    fullName: repo.full_name,
    ref: options?.ref,
    path: options?.path,
    isDir: options?.isDir,
    commitSha: options?.commitSha,
  })
}

export function buildNewRepoWebUrl(
  provider?: string | null,
  endpoint?: string | null
): string | null {
  const p = (provider || 'github').trim().toLowerCase()
  if (p === 'local') {
    return null
  }
  const base = resolveGitWebBase(p, endpoint)
  if (!base) {
    return null
  }
  if (p === 'gitlab') {
    return safeExternalUrl(`${base}/projects/new`)
  }
  if (p === 'bitbucket') {
    return safeExternalUrl(`${base}/repo/create`)
  }
  return safeExternalUrl(`${base}/new`)
}

export function buildRepoCloneUrls(
  repo: Pick<ProjectRepository, 'full_name' | 'default_branch'>,
  backend?: Pick<Backend, 'provider' | 'endpoint'> | null,
  ref?: string | null
): {
  httpsUrl: string
  sshUrl: string
  gitCliCommand: string
} {
  const p = (backend?.provider || 'github').trim().toLowerCase()
  const cleanRepo = repo.full_name.trim().replace(/^\/+|\/+$/g, '')
  const base = resolveGitWebBase(p, backend?.endpoint)
  if (!base || p === 'local' || cleanRepo.toLowerCase().startsWith('local/')) {
    return {
      httpsUrl: '',
      sshUrl: '',
      gitCliCommand: '',
    }
  }
  const httpsUrl = `${base}/${cleanRepo}.git`

  let host = 'github.com'
  try {
    host = new URL(base).hostname || host
  } catch {
    // ignore URL parse error
  }
  const sshUrl = `git@${host}:${cleanRepo}.git`
  const defaultBranch = (repo.default_branch || 'main').trim()
  const activeRef = (ref || defaultBranch).trim()
  const branchFlag =
    activeRef && activeRef !== defaultBranch ? ` -b ${activeRef}` : ''
  const gitCliCommand = `git clone${branchFlag} ${httpsUrl}`

  return {
    httpsUrl,
    sshUrl,
    gitCliCommand,
  }
}


