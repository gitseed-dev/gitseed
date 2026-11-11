import React, { Component, Suspense, useEffect, useMemo, useState } from 'react'
import { Loader2, RefreshCw, Smartphone } from 'lucide-react'
import { Highlight, themes } from 'prism-react-renderer'
import { useTheme } from '../../context/ThemeContext'
import { detectLanguageFromPath } from '../../utils/codeLanguage'
import type { MonacoRuntimeProps } from './MonacoRuntime'

const MONACO_RELOAD_STORAGE_KEY = 'gitseed:monaco-chunk-reload-at'
const MONACO_RELOAD_COOLDOWN_MS = 15_000

export function isDynamicImportFailure(err: unknown): boolean {
  const msg = err instanceof Error ? err.message : String(err ?? '')
  return (
    msg.includes('Failed to fetch dynamically imported module') ||
    msg.includes('Importing a module script failed') ||
    msg.includes('error loading dynamically imported module') ||
    msg.includes('Unable to preload CSS')
  )
}

function tryAutoReloadOnStaleChunk(err: unknown): boolean {
  if (typeof window === 'undefined' || !isDynamicImportFailure(err)) {
    return false
  }
  try {
    const now = Date.now()
    const lastReload = Number(
      window.sessionStorage.getItem(MONACO_RELOAD_STORAGE_KEY) || '0'
    )
    if (!lastReload || now - lastReload > MONACO_RELOAD_COOLDOWN_MS) {
      window.sessionStorage.setItem(MONACO_RELOAD_STORAGE_KEY, String(now))
      window.location.reload()
      return true
    }
  } catch {
    // Ignore storage errors
  }
  return false
}

export interface MonacoCodeEditorProps
  extends Omit<MonacoRuntimeProps, 'theme' | 'language'> {
  language?: string
}

function useIsPhoneScreen(): boolean {
  const [isPhone, setIsPhone] = useState<boolean>(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return false
    return window.matchMedia('(max-width: 640px)').matches
  })

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mq = window.matchMedia('(max-width: 640px)')
    const onChange = (e: MediaQueryListEvent) => setIsPhone(e.matches)
    mq.addEventListener('change', onChange)
    return () => mq.removeEventListener('change', onChange)
  }, [])

  return isPhone
}

interface SimpleDiffLine {
  type: 'equal' | 'add' | 'del'
  oldLine: number | null
  newLine: number | null
  text: string
}

function computeSimpleDiff(oldText: string, newText: string): SimpleDiffLine[] {
  const oldLines = oldText ? oldText.split('\n') : []
  const newLines = newText ? newText.split('\n') : []
  if (oldText === newText) {
    return newLines.map((text, idx) => ({
      type: 'equal',
      oldLine: idx + 1,
      newLine: idx + 1,
      text,
    }))
  }
  const result: SimpleDiffLine[] = []
  let i = 0
  let j = 0
  while (i < oldLines.length || j < newLines.length) {
    if (i < oldLines.length && j < newLines.length && oldLines[i] === newLines[j]) {
      result.push({
        type: 'equal',
        oldLine: i + 1,
        newLine: j + 1,
        text: oldLines[i],
      })
      i++
      j++
    } else {
      if (i < oldLines.length) {
        result.push({
          type: 'del',
          oldLine: i + 1,
          newLine: null,
          text: oldLines[i],
        })
        i++
      }
      if (j < newLines.length) {
        result.push({
          type: 'add',
          oldLine: null,
          newLine: j + 1,
          text: newLines[j],
        })
        j++
      }
    }
  }
  return result
}

export interface UnifiedDiffRow {
  type: 'hunk' | 'context' | 'add' | 'del'
  oldLineNumber: number | null
  newLineNumber: number | null
  lineNumber: string
  text: string
}

export interface ParsedUnifiedDiff {
  original: string
  modified: string
  rows: UnifiedDiffRow[]
  originalLineMap: Record<number, string>
  modifiedLineMap: Record<number, string>
  lineMap: Record<number, string>
  resolveModifiedLineNumber: (modelLineNumber: number) => number | null
  resolveOriginalLineNumber: (modelLineNumber: number) => number | null
}

const HUNK_HEADER_RE = /^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$/

/**
 * Parses a unified Git patch (`@@ -oldStart,oldCount +newStart,newCount @@`) into
 * aligned `(original, modified)` buffers AND 1-based Monaco model line-number maps
 * so gutter numbers and review line comments match real repository file line numbers.
 */
export function parseUnifiedDiffLines(patch: string): ParsedUnifiedDiff {
  const emptyResult: ParsedUnifiedDiff = {
    original: '',
    modified: '',
    rows: [],
    originalLineMap: {},
    modifiedLineMap: {},
    lineMap: {},
    resolveModifiedLineNumber: () => null,
    resolveOriginalLineNumber: () => null,
  }
  if (!patch || !patch.trim()) {
    return emptyResult
  }

  const rawLines = patch.replace(/\r\n/g, '\n').split('\n')
  // Drop a trailing empty line left by final newline in patch text
  if (rawLines.length > 0 && rawLines[rawLines.length - 1] === '') {
    rawLines.pop()
  }

  const origLines: string[] = []
  const modLines: string[] = []
  const rows: UnifiedDiffRow[] = []
  const originalLineMap: Record<number, string> = {}
  const modifiedLineMap: Record<number, string> = {}

  let seenHunk = false
  let oldLine = 1
  let newLine = 1

  for (const line of rawLines) {
    if (
      line.startsWith('diff --git ') ||
      line.startsWith('index ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line.startsWith('new file mode') ||
      line.startsWith('deleted file mode') ||
      line.startsWith('similarity index') ||
      line.startsWith('rename from') ||
      line.startsWith('rename to') ||
      line.startsWith('Binary files ')
    ) {
      continue
    }

    const hunkMatch = line.match(HUNK_HEADER_RE)
    if (hunkMatch) {
      oldLine = Number.parseInt(hunkMatch[1], 10)
      const oldCountPart = hunkMatch[2] !== undefined ? `,${hunkMatch[2]}` : ''
      newLine = Number.parseInt(hunkMatch[3], 10)
      const newCountPart = hunkMatch[4] !== undefined ? `,${hunkMatch[4]}` : ''
      const contextLabel = hunkMatch[5]?.trim() ? ` ${hunkMatch[5].trim()}` : ''
      const hunkBanner = `⋯ @@ -${oldLine}${oldCountPart} +${newLine}${newCountPart} @@${contextLabel}`

      origLines.push(hunkBanner)
      modLines.push(hunkBanner)
      originalLineMap[origLines.length] = ''
      modifiedLineMap[modLines.length] = ''
      rows.push({
        type: 'hunk',
        oldLineNumber: null,
        newLineNumber: null,
        lineNumber: '',
        text: hunkBanner,
      })
      seenHunk = true
      continue
    }

    if (!seenHunk) continue
    if (line.startsWith('\\ No newline at end of file')) {
      continue
    }

    const prefix = line[0]
    const content = line.slice(1)
    if (prefix === '-') {
      const currentOld = oldLine++
      origLines.push(content)
      originalLineMap[origLines.length] = String(currentOld)
      rows.push({
        type: 'del',
        oldLineNumber: currentOld,
        newLineNumber: null,
        lineNumber: String(currentOld),
        text: content,
      })
    } else if (prefix === '+') {
      const currentNew = newLine++
      modLines.push(content)
      modifiedLineMap[modLines.length] = String(currentNew)
      rows.push({
        type: 'add',
        oldLineNumber: null,
        newLineNumber: currentNew,
        lineNumber: String(currentNew),
        text: content,
      })
    } else {
      const text = prefix === ' ' ? content : line
      const currentOld = oldLine++
      const currentNew = newLine++
      origLines.push(text)
      modLines.push(text)
      originalLineMap[origLines.length] = String(currentOld)
      modifiedLineMap[modLines.length] = String(currentNew)
      rows.push({
        type: 'context',
        oldLineNumber: currentOld,
        newLineNumber: currentNew,
        lineNumber: String(currentNew),
        text,
      })
    }
  }

  const resolveModifiedLineNumber = (modelLineNumber: number): number | null => {
    const raw = modifiedLineMap[modelLineNumber]
    if (!raw) return null
    const parsed = Number.parseInt(raw, 10)
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null
  }

  const resolveOriginalLineNumber = (modelLineNumber: number): number | null => {
    const raw = originalLineMap[modelLineNumber]
    if (!raw) return null
    const parsed = Number.parseInt(raw, 10)
    return Number.isInteger(parsed) && parsed > 0 ? parsed : null
  }

  return {
    original: origLines.join('\n'),
    modified: modLines.join('\n'),
    rows,
    originalLineMap,
    modifiedLineMap,
    lineMap: modifiedLineMap,
    resolveModifiedLineNumber,
    resolveOriginalLineNumber,
  }
}

/**
 * Reconstructs aligned `(original, modified)` buffers from a unified Git patch
 * so read-only DiffEditor views (such as the seed Changes tab) render with the
 * exact same Monaco DiffEditor used in sessions.
 */
export function reconstructTextPairFromUnifiedPatch(patch: string): {
  original: string
  modified: string
} {
  if (!patch || !patch.trim()) {
    return { original: '', modified: '' }
  }
  const rawLines = patch.replace(/\r\n/g, '\n').split('\n')
  const origLines: string[] = []
  const modLines: string[] = []
  let seenHunk = false

  for (const line of rawLines) {
    if (
      line.startsWith('diff --git ') ||
      line.startsWith('index ') ||
      line.startsWith('--- ') ||
      line.startsWith('+++ ') ||
      line.startsWith('new file mode') ||
      line.startsWith('deleted file mode') ||
      line.startsWith('similarity index') ||
      line.startsWith('rename from') ||
      line.startsWith('rename to') ||
      line.startsWith('Binary files ')
    ) {
      continue
    }

    if (line.startsWith('@@')) {
      const headerMatch = line.match(/^@@\s+-\d+(?:,\d+)?\s+\+\d+(?:,\d+)?\s+@@(.*)$/)
      if (seenHunk) {
        const sep = headerMatch?.[1]?.trim()
          ? `… ${headerMatch[1].trim()} …`
          : '…'
        origLines.push(sep)
        modLines.push(sep)
      }
      seenHunk = true
      continue
    }

    if (!seenHunk) continue

    if (line.startsWith('\\ No newline at end of file')) {
      continue
    }

    const prefix = line[0]
    const content = line.slice(1)
    if (prefix === '-') {
      origLines.push(content)
    } else if (prefix === '+') {
      modLines.push(content)
    } else {
      origLines.push( prefix === ' ' ? content : line)
      modLines.push(prefix === ' ' ? content : line)
    }
  }

  return {
    original: origLines.join('\n'),
    modified: modLines.join('\n'),
  }
}

interface FallbackCodeSurfaceProps extends MonacoRuntimeProps {
  bannerKind: 'phone' | 'chunk-fallback'
}

const FallbackCodeSurface: React.FC<FallbackCodeSurfaceProps> = ({
  mode,
  language,
  value,
  originalValue = '',
  originalLineNumbers,
  modifiedLineNumbers,
  onLineSelect,
  readOnly = false,
  theme,
  onChange,
  onSave,
  minHeight = 160,
  maxHeight = 620,
  autoHeight = false,
  bannerKind,
}) => {
  const canEdit = mode === 'edit' && !readOnly && bannerKind !== 'phone'
  const lineCount = Math.max(
    value ? value.split('\n').length : 1,
    originalValue ? originalValue.split('\n').length : 1,
    1
  )
  const computedHeight = autoHeight
    ? Math.min(maxHeight, Math.max(minHeight, lineCount * 20 + 28))
    : undefined

  return (
    <div
      style={computedHeight ? { height: `${computedHeight}px` } : undefined}
      className={
        autoHeight
          ? 'flex flex-col w-full overflow-hidden bg-white dark:bg-zinc-950'
          : 'flex flex-col flex-1 w-full h-full min-h-0 overflow-hidden bg-white dark:bg-zinc-950'
      }
    >
      {bannerKind === 'phone' ? (
        <div className="px-3 py-1.5 border-b border-zinc-200 dark:border-zinc-800 bg-zinc-100/80 dark:bg-zinc-900/80 text-[11px] text-zinc-600 dark:text-zinc-400 flex items-center gap-1.5">
          <Smartphone size={12} className="text-teal-500 shrink-0" />
          <span>Read-only view on small screens</span>
        </div>
      ) : (
        <div className="px-3 py-1.5 border-b border-amber-500/25 bg-amber-500/10 text-[11px] text-amber-800 dark:text-amber-300 flex items-center justify-between gap-2">
          <span>
            App assets were updated since this tab was opened. Reload to load the full editor runtime.
          </span>
          <button
            type="button"
            onClick={() => window.location.reload()}
            className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md font-semibold bg-amber-500/20 hover:bg-amber-500/30 text-amber-900 dark:text-amber-200 transition-colors cursor-pointer shrink-0"
          >
            <RefreshCw size={11} />
            <span>Reload</span>
          </button>
        </div>
      )}

      {mode === 'diff' ? (
        <div className="flex-1 overflow-auto font-mono text-xs leading-5">
          <table className="w-full border-collapse">
            <tbody>
              {computeSimpleDiff(originalValue, value).map((line, idx) => {
                const mappedOld =
                  line.oldLine !== null && originalLineNumbers
                    ? (originalLineNumbers[line.oldLine] ?? '')
                    : (line.oldLine ?? '')
                const mappedNew =
                  line.newLine !== null && modifiedLineNumbers
                    ? (modifiedLineNumbers[line.newLine] ?? '')
                    : (line.newLine ?? '')
                const isHunkSep =
                  mappedOld === '' && mappedNew === '' && line.text.startsWith('⋯ @@')
                const rowBg = isHunkSep
                  ? 'bg-zinc-100/80 dark:bg-zinc-900/80 text-zinc-500 dark:text-zinc-400'
                  : line.type === 'add'
                    ? 'bg-emerald-500/12 text-emerald-900 dark:text-emerald-200'
                    : line.type === 'del'
                      ? 'bg-rose-500/12 text-rose-900 dark:text-rose-200'
                      : 'text-zinc-700 dark:text-zinc-300'
                const prefix = isHunkSep
                  ? ''
                  : line.type === 'add'
                    ? '+'
                    : line.type === 'del'
                      ? '-'
                      : ' '
                const selectableLine = Number(mappedNew || mappedOld)
                return (
                  <tr
                    key={idx}
                    onClick={() => {
                      if (
                        onLineSelect &&
                        Number.isInteger(selectableLine) &&
                        selectableLine > 0
                      ) {
                        onLineSelect(selectableLine)
                      }
                    }}
                    className={`${rowBg} ${onLineSelect && !isHunkSep ? 'cursor-pointer hover:brightness-95' : ''}`}
                  >
                    <td className="w-9 px-1.5 text-right select-none text-[10px] text-zinc-400 border-r border-zinc-200/70 dark:border-zinc-800/70">
                      {mappedOld}
                    </td>
                    <td className="w-9 px-1.5 text-right select-none text-[10px] text-zinc-400 border-r border-zinc-200/70 dark:border-zinc-800/70">
                      {mappedNew}
                    </td>
                    <td className="w-5 text-center select-none font-bold opacity-75">
                      {prefix}
                    </td>
                    <td className="px-2 whitespace-pre">{line.text || ' '}</td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      ) : canEdit ? (
        <textarea
          value={value}
          onChange={(e) => onChange?.(e.target.value)}
          onKeyDown={(e) => {
            if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 's') {
              e.preventDefault()
              onSave?.()
            }
          }}
          spellCheck={false}
          className="flex-1 w-full h-full p-3 font-mono text-xs leading-5 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100 outline-none resize-none overflow-auto"
        />
      ) : (
        <div className="flex-1 overflow-auto">
          <Highlight
            theme={theme === 'dark' ? themes.vsDark : themes.vsLight}
            code={value}
            language={language}
          >
            {({ tokens, getLineProps, getTokenProps }) => (
              <pre className="m-0 p-3 font-mono text-xs leading-5 whitespace-pre overflow-x-auto">
                {tokens.map((line, i) => (
                  <div key={i} {...getLineProps({ line })}>
                    <span className="inline-block w-8 pr-2 text-right select-none text-zinc-400">
                      {i + 1}
                    </span>
                    {line.map((token, key) => (
                      <span key={key} {...getTokenProps({ token })} />
                    ))}
                  </div>
                ))}
              </pre>
            )}
          </Highlight>
        </div>
      )}
    </div>
  )
}

const LazyMonacoRuntime = React.lazy(async (): Promise<{
  default: React.ComponentType<MonacoRuntimeProps>
}> => {
  try {
    return await import('./MonacoRuntime')
  } catch (err) {
    tryAutoReloadOnStaleChunk(err)
    const FallbackComponent: React.FC<MonacoRuntimeProps> = (props) => (
      <FallbackCodeSurface {...props} bannerKind="chunk-fallback" />
    )
    return { default: FallbackComponent }
  }
})

interface MonacoBoundaryProps {
  fallbackProps: MonacoRuntimeProps
  children: React.ReactNode
}

interface MonacoBoundaryState {
  hasError: boolean
}

class MonacoErrorBoundary extends Component<
  MonacoBoundaryProps,
  MonacoBoundaryState
> {
  constructor(props: MonacoBoundaryProps) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): MonacoBoundaryState {
    tryAutoReloadOnStaleChunk(error)
    return { hasError: true }
  }

  render() {
    if (this.state.hasError) {
      return (
        <FallbackCodeSurface
          {...this.props.fallbackProps}
          bannerKind="chunk-fallback"
        />
      )
    }
    return this.props.children
  }
}

export const MonacoCodeEditor: React.FC<MonacoCodeEditorProps> = ({
  mode,
  filePath,
  language,
  value,
  originalValue = '',
  originalLineNumbers,
  modifiedLineNumbers,
  onLineSelect,
  readOnly = false,
  sideBySide = false,
  onChange,
  onSave,
  minHeight = 160,
  maxHeight = 620,
  autoHeight = false,
  className = '',
}) => {
  const { theme } = useTheme()
  const isPhone = useIsPhoneScreen()
  const resolvedLanguage = useMemo(
    () => language || detectLanguageFromPath(filePath),
    [filePath, language]
  )

  const runtimeProps: MonacoRuntimeProps = {
    mode,
    filePath,
    language: resolvedLanguage,
    value,
    originalValue,
    originalLineNumbers,
    modifiedLineNumbers,
    onLineSelect,
    readOnly,
    sideBySide,
    theme,
    onChange,
    onSave,
    minHeight,
    maxHeight,
    autoHeight,
    className,
  }

  if (isPhone) {
    return <FallbackCodeSurface {...runtimeProps} bannerKind="phone" />
  }

  return (
    <MonacoErrorBoundary fallbackProps={runtimeProps}>
      <Suspense
        fallback={
          <div
            style={autoHeight ? { height: `${minHeight}px` } : undefined}
            className="flex-1 w-full h-full flex items-center justify-center gap-2 text-xs text-zinc-500 bg-white dark:bg-zinc-950"
          >
            <Loader2 size={14} className="animate-spin text-teal-500" />
            <span>Loading editor…</span>
          </div>
        }
      >
        <LazyMonacoRuntime {...runtimeProps} />
      </Suspense>
    </MonacoErrorBoundary>
  )
}

