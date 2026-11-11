import './prismSetup'
import 'prismjs/components/prism-bash'
import 'prismjs/components/prism-csharp'
import 'prismjs/components/prism-diff'
import 'prismjs/components/prism-docker'
import 'prismjs/components/prism-hcl'
import 'prismjs/components/prism-ini'
import 'prismjs/components/prism-java'
import 'prismjs/components/prism-makefile'
import 'prismjs/components/prism-ruby'
import 'prismjs/components/prism-scss'
import 'prismjs/components/prism-toml'

const EXTENSION_LANGUAGE_MAP: Record<string, string> = {
  py: 'python',
  pyi: 'python',
  pyw: 'python',
  ts: 'typescript',
  mts: 'typescript',
  cts: 'typescript',
  tsx: 'tsx',
  js: 'javascript',
  mjs: 'javascript',
  cjs: 'javascript',
  jsx: 'jsx',
  json: 'json',
  jsonc: 'json',
  jsonl: 'json',
  webmanifest: 'json',
  yml: 'yaml',
  yaml: 'yaml',
  toml: 'toml',
  lock: 'toml',
  md: 'markdown',
  mdx: 'markdown',
  markdown: 'markdown',
  sh: 'bash',
  bash: 'bash',
  zsh: 'bash',
  fish: 'bash',
  env: 'bash',
  sql: 'sql',
  go: 'go',
  rs: 'rust',
  java: 'java',
  kt: 'kotlin',
  kts: 'kotlin',
  swift: 'swift',
  c: 'c',
  h: 'c',
  cpp: 'cpp',
  cc: 'cpp',
  cxx: 'cpp',
  hpp: 'cpp',
  hh: 'cpp',
  cs: 'csharp',
  rb: 'ruby',
  html: 'markup',
  htm: 'markup',
  vue: 'markup',
  svelte: 'markup',
  xml: 'xml',
  svg: 'svg',
  plist: 'xml',
  csproj: 'xml',
  css: 'css',
  scss: 'scss',
  graphql: 'graphql',
  gql: 'graphql',
  tf: 'hcl',
  tfvars: 'hcl',
  hcl: 'hcl',
  ini: 'ini',
  cfg: 'ini',
  conf: 'ini',
  properties: 'ini',
  diff: 'diff',
  patch: 'diff',
  mk: 'makefile',
  dockerfile: 'docker',
}

const LANGUAGE_ALIASES: Record<string, string> = {
  py: 'python',
  python3: 'python',
  js: 'javascript',
  node: 'javascript',
  ts: 'typescript',
  sh: 'bash',
  shell: 'bash',
  zsh: 'bash',
  console: 'bash',
  yml: 'yaml',
  md: 'markdown',
  dockerfile: 'docker',
  containerfile: 'docker',
  rb: 'ruby',
  rs: 'rust',
  golang: 'go',
  cs: 'csharp',
  'c#': 'csharp',
  'c++': 'cpp',
  tf: 'hcl',
  terraform: 'hcl',
  html: 'markup',
  text: 'plain',
  txt: 'plain',
  plaintext: 'plain',
}

export function normalizeCodeLanguage(lang?: string | null): string {
  if (!lang) return 'plain'
  const cleaned = lang.trim().toLowerCase()
  if (!cleaned) return 'plain'
  return LANGUAGE_ALIASES[cleaned] || EXTENSION_LANGUAGE_MAP[cleaned] || cleaned
}

export function detectLanguageFromPath(filePath?: string | null): string {
  if (!filePath) return 'plain'
  const filename = (filePath.split('/').pop() || filePath).toLowerCase()

  if (
    filename === 'dockerfile' ||
    filename === 'containerfile' ||
    filename.startsWith('dockerfile.') ||
    filename.endsWith('.dockerfile')
  ) {
    return 'docker'
  }

  if (filename === 'makefile' || filename === 'gnumakefile') {
    return 'makefile'
  }

  if (
    filename === '.env' ||
    filename.startsWith('.env.') ||
    filename === '.bashrc' ||
    filename === '.zshrc' ||
    filename === '.profile'
  ) {
    return 'bash'
  }

  if (
    filename === '.gitignore' ||
    filename === '.dockerignore' ||
    filename === '.gitattributes' ||
    filename === '.editorconfig'
  ) {
    return 'ini'
  }

  if (filename === 'pipfile' || filename === 'cargo.lock' || filename === 'poetry.lock') {
    return 'toml'
  }

  if (filename === 'gemfile' || filename === 'rakefile') {
    return 'ruby'
  }

  const dotIdx = filename.lastIndexOf('.')
  if (dotIdx !== -1 && dotIdx < filename.length - 1) {
    const ext = filename.slice(dotIdx + 1)
    if (EXTENSION_LANGUAGE_MAP[ext]) {
      return EXTENSION_LANGUAGE_MAP[ext]
    }
  }

  return 'plain'
}

