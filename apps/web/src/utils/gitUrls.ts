import type { Backend, ProjectRepository } from '../types'

export function resolveGitWebBase(
  provider?: string | null,
  endpoint?: string | null
): string {
  const p = (provider || 'github').trim().toLowerCase()
  const raw = (endpoint || '').trim().replace(/\/+$/, '')

  if (p === 'github' || p === 'github-enterprise' || p === 'github-app') {
    if (!raw || raw === 'https://api.github.com') {
      return 'https://github.com'
    }
    if (raw.endsWith('/api/v3')) {
      return raw.slice(0, -'/api/v3'.length)
    }
    if (raw.startsWith('https://api.')) {
      return `https://${raw.slice('https://api.'.length)}`
    }
    return raw
  }

  if (p === 'gitlab') {
    if (!raw) return 'https://gitlab.com'
    if (raw.endsWith('/api/v4')) {
      return raw.slice(0, -'/api/v4'.length)
    }
    return raw
  }

  if (p === 'bitbucket') {
    if (!raw || raw.includes('api.bitbucket.org')) {
      return 'https://bitbucket.org'
    }
    if (raw.endsWith('/2.0')) {
      return raw.slice(0, -'/2.0'.length)
    }
    return raw
  }

  return raw || 'https://github.com'
}

export function buildGitWebUrl(options: {
  provider?: string | null
  endpoint?: string | null
  fullName: string
  ref?: string | null
  path?: string | null
  isDir?: boolean
  commitSha?: string | null
}): string {
  const p = (options.provider || 'github').trim().toLowerCase()
  const base = resolveGitWebBase(p, options.endpoint)
  const cleanRepo = options.fullName.trim().replace(/^\/+|\/+$/g, '')
  const cleanPath = (options.path || '').trim().replace(/^\/+|\/+$/g, '')
  const isDir = options.isDir ?? true

  if (options.commitSha) {
    const sha = encodeURIComponent(options.commitSha.trim())
    if (p === 'gitlab') {
      return `${base}/${cleanRepo}/-/commit/${sha}`
    }
    if (p === 'bitbucket') {
      return `${base}/${cleanRepo}/commits/${sha}`
    }
    return `${base}/${cleanRepo}/commit/${sha}`
  }

  if (!options.ref) {
    return `${base}/${cleanRepo}`
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
      return `${base}/${cleanRepo}/-/tree/${encodedRef}`
    }
    return `${base}/${cleanRepo}/-/${isDir ? 'tree' : 'blob'}/${encodedRef}/${encodedPath}`
  }

  if (p === 'bitbucket') {
    if (!encodedPath) {
      return `${base}/${cleanRepo}/src/${encodedRef}`
    }
    return `${base}/${cleanRepo}/src/${encodedRef}/${encodedPath}`
  }

  if (!encodedPath) {
    return `${base}/${cleanRepo}/tree/${encodedRef}`
  }
  return `${base}/${cleanRepo}/${isDir ? 'tree' : 'blob'}/${encodedRef}/${encodedPath}`
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
): string {
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
): string {
  const p = (provider || 'github').trim().toLowerCase()
  const base = resolveGitWebBase(p, endpoint)
  if (p === 'gitlab') {
    return `${base}/projects/new`
  }
  if (p === 'bitbucket') {
    return `${base}/repo/create`
  }
  return `${base}/new`
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
  const base = resolveGitWebBase(p, backend?.endpoint)
  const cleanRepo = repo.full_name.trim().replace(/^\/+|\/+$/g, '')
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


