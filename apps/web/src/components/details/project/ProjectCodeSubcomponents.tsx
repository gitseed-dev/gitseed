import React, { useMemo, useState } from 'react'
import {
  AlertCircle,
  ArrowLeft,
  Check,
  ChevronDown,
  Copy,
  Download,
  ExternalLink,
  FileText,
  Loader2,
  Terminal,
} from 'lucide-react'
import { Card } from '@heroui/react'
import { Highlight, themes } from 'prism-react-renderer'
import { detectLanguageFromPath } from '../../../utils/codeLanguage'
import { formatBytes } from '../../../utils/format'
import { buildRepoCloneUrls, safeExternalUrl } from '../../../utils/gitUrls'
import type { Backend, ProjectRepository, RepoBlobResponse } from '../../../types'

export const formatCodeBytes = formatBytes

export interface RepoClonePopoverProps {
  repo: ProjectRepository
  backend: Backend | null
  selectedRef: string
}

export const RepoClonePopover: React.FC<RepoClonePopoverProps> = ({
  repo,
  backend,
  selectedRef,
}) => {
  const [isCloneOpen, setIsCloneOpen] = useState(false)
  const [cloneTab, setCloneTab] = useState<'cli' | 'https' | 'ssh'>('cli')
  const [copiedClone, setCopiedClone] = useState(false)

  const cloneUrls = useMemo(
    () => buildRepoCloneUrls(repo, backend, selectedRef),
    [repo, backend, selectedRef]
  )
  const activeCloneValue =
    cloneTab === 'https'
      ? cloneUrls.httpsUrl
      : cloneTab === 'ssh'
        ? cloneUrls.sshUrl
        : cloneUrls.gitCliCommand

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setIsCloneOpen((prev) => !prev)}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-zinc-700 dark:text-zinc-200 bg-zinc-100/90 dark:bg-zinc-800/90 hover:bg-zinc-200/80 dark:hover:bg-zinc-700/80 border border-zinc-200 dark:border-zinc-700/80 transition cursor-pointer"
      >
        <Terminal size={12} className="text-teal-600 dark:text-teal-400" />
        <span>Clone</span>
        <ChevronDown size={12} className="opacity-70" />
      </button>

      {isCloneOpen && (
        <>
          <div
            className="fixed inset-0 z-30"
            onClick={() => setIsCloneOpen(false)}
          />
          <div className="absolute right-0 mt-2 w-80 sm:w-96 rounded-2xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/90 shadow-xl p-3.5 z-40 flex flex-col gap-2.5">
            <div className="flex items-center justify-between">
              <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100 flex items-center gap-1.5">
                <Terminal size={13} className="text-teal-500" />
                Clone Repository ({selectedRef})
              </span>
              <div className="inline-flex items-center rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700">
                {(
                  [
                    { id: 'cli', label: 'Git CLI' },
                    { id: 'https', label: 'HTTPS' },
                    { id: 'ssh', label: 'SSH' },
                  ] as const
                ).map((tab) => (
                  <button
                    key={tab.id}
                    type="button"
                    onClick={() => setCloneTab(tab.id)}
                    className={`px-2 py-0.5 rounded-md text-[11px] font-semibold transition cursor-pointer ${
                      cloneTab === tab.id
                        ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs'
                        : 'text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200'
                    }`}
                  >
                    {tab.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="flex items-center gap-1.5">
              <input
                type="text"
                readOnly
                value={activeCloneValue}
                onFocus={(e) => e.currentTarget.select()}
                className="w-full h-8 px-2.5 rounded-xl text-xs font-mono bg-zinc-50 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700 text-zinc-800 dark:text-zinc-200 focus:outline-none"
              />
              <button
                type="button"
                onClick={() => {
                  navigator.clipboard.writeText(activeCloneValue)
                  setCopiedClone(true)
                  setTimeout(() => setCopiedClone(false), 1500)
                }}
                className="h-8 px-2.5 rounded-xl text-xs font-semibold bg-teal-500/15 hover:bg-teal-500/25 text-teal-700 dark:text-teal-300 border border-teal-500/30 inline-flex items-center gap-1 shrink-0 cursor-pointer"
              >
                {copiedClone ? <Check size={12} /> : <Copy size={12} />}
                <span>{copiedClone ? 'Copied' : 'Copy'}</span>
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

export interface CodeBlobViewerProps {
  currentPath: string
  blobData: RepoBlobResponse | undefined
  isLoading: boolean
  isError: boolean
  error: unknown
  currentExternalUrl: string | null
  providerLabel: string
  theme: string
  onNavigateUp: () => void
}

export const CodeBlobViewer: React.FC<CodeBlobViewerProps> = ({
  currentPath,
  blobData,
  isLoading,
  isError,
  error,
  currentExternalUrl,
  providerLabel,
  theme,
  onNavigateUp,
}) => {
  const [copiedBlob, setCopiedBlob] = useState(false)
  const safeCurrentExternalUrl = safeExternalUrl(currentExternalUrl)

  const blobLines = useMemo(
    () => (blobData?.content ? blobData.content.split('\n') : []),
    [blobData?.content]
  )

  const blobLanguage = useMemo(
    () => detectLanguageFromPath(blobData?.name || currentPath),
    [blobData?.name, currentPath]
  )

  const handleCopyFileContent = async () => {
    const content = blobData?.content
    if (content === null || content === undefined) return
    try {
      await navigator.clipboard.writeText(content)
      setCopiedBlob(true)
      setTimeout(() => setCopiedBlob(false), 1800)
    } catch {
      // ignore clipboard errors
    }
  }

  const handleDownloadFile = () => {
    const content = blobData?.content
    if (content === null || content === undefined) return
    const fileName =
      blobData?.name ||
      currentPath.split('/').filter(Boolean).pop() ||
      'download.txt'
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = fileName
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)
  }

  return (
    <Card className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs overflow-hidden">
      {/* File Header */}
      <div className="px-4 py-3 bg-zinc-50/90 dark:bg-zinc-800/60 border-b border-zinc-200/80 dark:border-zinc-800 flex flex-wrap items-center justify-between gap-3">
        <div className="flex items-center gap-2.5 min-w-0">
          <button
            type="button"
            onClick={onNavigateUp}
            className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
          >
            <ArrowLeft size={13} />
            <span>Folder</span>
          </button>
          <FileText
            size={15}
            className="text-teal-600 dark:text-teal-400 shrink-0"
          />
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate">
            {blobData?.name || currentPath.split('/').pop()}
          </span>
          {blobLanguage !== 'plain' && (
            <span className="px-2 py-0.5 rounded-md text-[10px] font-mono uppercase tracking-wider bg-zinc-200/80 dark:bg-zinc-700/80 text-zinc-600 dark:text-zinc-300">
              {blobLanguage}
            </span>
          )}
          {blobData && (
            <span className="text-xs text-zinc-500 dark:text-zinc-400">
              · {blobLines.length} lines ·{' '}
              <span className="font-mono">{formatCodeBytes(blobData.size)}</span>
            </span>
          )}
        </div>

        <div className="flex items-center gap-2">
          {blobData?.content !== null &&
            blobData?.content !== undefined &&
            !blobData?.is_binary && (
              <>
                <button
                  type="button"
                  onClick={handleCopyFileContent}
                  title="Copy file contents"
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
                >
                  {copiedBlob ? (
                    <>
                      <Check size={12} className="text-emerald-500" />
                      <span>Copied</span>
                    </>
                  ) : (
                    <>
                      <Copy size={12} />
                      <span>Copy</span>
                    </>
                  )}
                </button>

                <button
                  type="button"
                  onClick={handleDownloadFile}
                  title={`Download ${blobData?.name || currentPath.split('/').pop() || 'file'}`}
                  className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-700 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
                >
                  <Download size={12} />
                  <span>Download</span>
                </button>
              </>
            )}

          {safeCurrentExternalUrl && (
            <a
              href={safeCurrentExternalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-2.5 py-1 rounded-lg text-xs font-semibold text-teal-700 dark:text-teal-300 bg-teal-500/10 border border-teal-500/30 hover:bg-teal-500/20 transition whitespace-nowrap"
            >
              <span>Open on {providerLabel}</span>
              <ExternalLink size={12} />
            </a>
          )}
        </div>
      </div>

      {isLoading ? (
        <div className="py-16 flex flex-col items-center justify-center gap-2.5 text-zinc-500 dark:text-zinc-400">
          <Loader2 size={22} className="animate-spin text-teal-500" />
          <span className="text-xs font-medium">Loading {currentPath}...</span>
        </div>
      ) : isError ? (
        <div className="p-8 text-center flex flex-col items-center gap-2">
          <AlertCircle size={20} className="text-rose-500" />
          <p className="text-xs text-zinc-600 dark:text-zinc-400">
            {error instanceof Error
              ? error.message
              : 'Unable to load file content.'}
          </p>
        </div>
      ) : blobData?.is_binary ? (
        <div className="p-10 text-center flex flex-col items-center gap-2.5">
          <p className="text-xs sm:text-sm font-medium text-zinc-600 dark:text-zinc-300">
            Binary file ({formatCodeBytes(blobData.size)}) cannot be rendered
            inline.
          </p>
          {safeCurrentExternalUrl && (
            <a
              href={safeCurrentExternalUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold text-teal-700 dark:text-teal-300 bg-teal-500/10 border border-teal-500/30"
            >
              <span>View / Download on {providerLabel}</span>
              <ExternalLink size={12} />
            </a>
          )}
        </div>
      ) : (
        <div className="overflow-x-auto max-h-[72vh] overflow-y-auto bg-zinc-50/70 dark:bg-zinc-950 text-zinc-800 dark:text-zinc-100 font-mono text-xs leading-relaxed">
          <Highlight
            code={blobData?.content || ''}
            language={blobLanguage}
            theme={theme === 'dark' ? themes.oneDark : themes.oneLight}
          >
            {({ tokens, getLineProps, getTokenProps }) => (
              <table className="w-full border-collapse">
                <tbody>
                  {tokens.map((line, index) => {
                    const lineProps = getLineProps({ line })
                    return (
                      <tr
                        key={index}
                        className="hover:bg-zinc-200/50 dark:hover:bg-zinc-900/90 transition-colors"
                      >
                        <td className="select-none text-right pr-4 pl-3 py-0.5 text-zinc-400 dark:text-zinc-600 border-r border-zinc-200/80 dark:border-zinc-800/80 bg-zinc-100/50 dark:bg-transparent w-12 align-top">
                          {index + 1}
                        </td>
                        <td
                          {...lineProps}
                          style={undefined}
                          className="pl-4 pr-6 py-0.5 whitespace-pre align-top"
                        >
                          {line.map((token, key) => (
                            <span key={key} {...getTokenProps({ token })} />
                          ))}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            )}
          </Highlight>
          {blobData?.truncated && (
            <div className="px-4 py-2.5 bg-amber-500/10 border-t border-amber-500/30 text-amber-800 dark:text-amber-300 font-sans text-xs flex items-center justify-between">
              <span>
                File preview truncated at 512 KB (total size:{' '}
                {formatCodeBytes(blobData.size)}).
              </span>
              {safeCurrentExternalUrl && (
                <a
                  href={safeCurrentExternalUrl}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="underline font-semibold"
                >
                  View full file on {providerLabel} ↗
                </a>
              )}
            </div>
          )}
        </div>
      )}
    </Card>
  )
}

