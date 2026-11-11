import React, { useEffect, useRef, useState } from 'react'
import {
  AlertCircle,
  Box,
  Check,
  CheckCircle2,
  Clock,
  Copy,
  CornerDownLeft,
  Download,
  FileArchive,
  GitBranch,
  GitCommit,
  Loader2,
  Package,
  Smartphone,
  Square,
  Terminal,
  Trash2,
  XCircle,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../../api/client'
import { formatBytes, stripAnsi } from '../../../utils/format'
import type {
  SandboxArtifact,
  SandboxSession,
  SandboxStatus,
} from '../../../types'

export function getSandboxStatusBadge(status: SandboxStatus) {
  switch (status) {
    case 'starting':
    case 'running':
      return {
        label: status === 'starting' ? 'Starting' : 'Running',
        className:
          'bg-teal-500/15 text-teal-700 dark:text-teal-300 border-teal-500/30',
        icon: <Loader2 size={12} className="animate-spin" />,
      }
    case 'succeeded':
      return {
        label: 'Succeeded',
        className:
          'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/30',
        icon: <CheckCircle2 size={12} />,
      }
    case 'failed':
      return {
        label: 'Failed',
        className:
          'bg-rose-500/15 text-rose-700 dark:text-rose-300 border-rose-500/30',
        icon: <XCircle size={12} />,
      }
    case 'timed_out':
      return {
        label: 'Timed Out',
        className:
          'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/30',
        icon: <Clock size={12} />,
      }
    case 'stopped':
    default:
      return {
        label: 'Stopped',
        className:
          'bg-zinc-200/80 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border-zinc-300 dark:border-zinc-700',
        icon: <Square size={11} />,
      }
  }
}

function getArtifactIcon(name: string) {
  const lower = name.toLowerCase()
  if (lower.endsWith('.apk') || lower.endsWith('.aab')) {
    return <Smartphone size={16} className="text-emerald-500 shrink-0" />
  }
  if (lower.endsWith('.whl') || lower.endsWith('.tgz')) {
    return <Package size={16} className="text-sky-500 shrink-0" />
  }
  return <FileArchive size={16} className="text-amber-500 shrink-0" />
}

export interface SandboxSessionInspectorProps {
  projectId: string
  session: SandboxSession | null
  canContribute: boolean
  stoppingId: string | null
  onStopSession: (sandboxId: string) => void
  onSendInput: (
    sandboxId: string,
    inputText: string,
    opts?: { eof?: boolean }
  ) => Promise<void>
  /**
   * `full` (default) shows provenance header + artifacts + terminal.
   * `terminal` shows only the terminal, for hosts that render their own header/artifacts.
   */
  variant?: 'full' | 'terminal'
}

/** Remove duplicate artifacts (same SHA-256 at multiple paths), keeping the shortest path. */
export function dedupeArtifacts(artifacts: SandboxArtifact[] | null | undefined): SandboxArtifact[] {
  const bySha = new Map<string, SandboxArtifact>()
  for (const art of artifacts || []) {
    const key = art.sha256 || art.rel_path
    const existing = bySha.get(key)
    if (!existing || art.rel_path.length < existing.rel_path.length) {
      bySha.set(key, art)
    }
  }
  return Array.from(bySha.values())
}

export const SandboxArtifactsList: React.FC<{
  projectId: string
  session: SandboxSession
  canContribute?: boolean
  onChanged?: (session: SandboxSession) => void
}> = ({ projectId, session, canContribute = false, onChanged }) => {
  const artifacts = dedupeArtifacts(session.artifacts)
  const isRunning = session.status === 'starting' || session.status === 'running'
  const isTerminal = session.kind === 'terminal'
  const [savePath, setSavePath] = useState('')
  const [busyPath, setBusyPath] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const expiresInDays = session.artifacts_expire_at
    ? Math.max(0, Math.ceil((new Date(session.artifacts_expire_at).getTime() - Date.now()) / 86_400_000))
    : null

  const handleSave = async () => {
    const p = savePath.trim()
    if (!p) return
    setBusyPath('__save__')
    setError(null)
    try {
      const updated = await api.projects.saveSandboxArtifact(projectId, session.id, p)
      setSavePath('')
      onChanged?.(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not save file')
    } finally {
      setBusyPath(null)
    }
  }

  const handleDelete = async (relPath: string) => {
    setBusyPath(relPath)
    setError(null)
    try {
      const updated = await api.projects.deleteSandboxArtifact(projectId, session.id, relPath)
      onChanged?.(updated)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not delete artifact')
    } finally {
      setBusyPath(null)
    }
  }

  return (
    <div className="p-4 flex flex-col gap-3">
      {isTerminal && canContribute && isRunning && (
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void handleSave()
          }}
          className="flex items-center gap-2"
        >
          <input
            value={savePath}
            onChange={(e) => setSavePath(e.target.value)}
            placeholder="Path in /workspace to keep, e.g. dist/app.apk"
            className="flex-1 h-8 px-3 rounded-lg text-xs font-mono border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-zinc-900 dark:text-zinc-100"
          />
          <Button
            type="submit"
            size="sm"
            variant="primary"
            isDisabled={!savePath.trim() || busyPath === '__save__'}
            className="rounded-lg bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold px-3 cursor-pointer"
          >
            {busyPath === '__save__' ? <Loader2 size={12} className="animate-spin" /> : 'Save file'}
          </Button>
        </form>
      )}

      <p className="text-[11px] text-zinc-500 dark:text-zinc-400">
        {isTerminal ? (
          <>
            Only files you keep are stored: use <strong>Save file</strong>, or put them in{' '}
            <code className="font-mono">/workspace/.gitseed/artifacts/</code> (collected when the session stops).
          </>
        ) : (
          <>Files matching the project's artifact rules that were created or changed by the run.</>
        )}
        {expiresInDays !== null && artifacts.length > 0 && (
          <> Stored outside the container; deleted automatically in {expiresInDays} day{expiresInDays === 1 ? '' : 's'}.</>
        )}
      </p>

      {error && (
        <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-500/30 text-xs text-rose-700 dark:text-rose-300">
          {error}
        </div>
      )}

      {artifacts.length === 0 ? (
        <div className="py-8 text-center text-xs text-zinc-500 dark:text-zinc-400">No artifacts yet.</div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
          {artifacts.map((art) => {
            const downloadUrl = api.projects.getSandboxArtifactDownloadUrl(projectId, session.id, art.rel_path)
            return (
              <div
                key={art.rel_path}
                className="group rounded-xl p-3 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 hover:border-teal-500/50 transition flex items-center justify-between gap-3 shadow-2xs"
              >
                <a href={downloadUrl} download={art.name} className="flex items-center gap-2.5 min-w-0">
                  {getArtifactIcon(art.name)}
                  <div className="flex flex-col min-w-0">
                    <span className="text-xs font-bold font-mono text-zinc-900 dark:text-zinc-100 truncate group-hover:text-teal-600 dark:group-hover:text-teal-400">
                      {art.name}
                    </span>
                    <span className="text-[11px] font-mono text-zinc-400 truncate">
                      {art.rel_path} · {formatBytes(art.size_bytes)}
                      {art.rule ? ` · ${art.rule === 'saved' ? 'saved by you' : `rule: ${art.rule}`}` : ''}
                    </span>
                  </div>
                </a>
                <div className="flex items-center gap-1 shrink-0">
                  <a
                    href={downloadUrl}
                    download={art.name}
                    title="Download"
                    className="p-1.5 rounded-lg text-teal-700 dark:text-teal-300 hover:bg-teal-500/10"
                  >
                    <Download size={14} />
                  </a>
                  {canContribute && (
                    <button
                      type="button"
                      title="Delete artifact"
                      disabled={busyPath === art.rel_path}
                      onClick={() => void handleDelete(art.rel_path)}
                      className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-600 hover:bg-rose-500/10 cursor-pointer disabled:opacity-50"
                    >
                      {busyPath === art.rel_path ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                    </button>
                  )}
                </div>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}

export const SandboxSessionInspector: React.FC<SandboxSessionInspectorProps> = ({
  projectId,
  session: selectedSession,
  canContribute,
  stoppingId,
  onStopSession,
  onSendInput,
  variant = 'full',
}) => {
  const [copiedSha, setCopiedSha] = useState(false)
  const [copiedLogs, setCopiedLogs] = useState(false)
  const [liveInput, setLiveInput] = useState('')
  const [isSendingInput, setIsSendingInput] = useState(false)
  const logContainerRef = useRef<HTMLPreElement | null>(null)
  const liveInputRef = useRef<HTMLInputElement | null>(null)

  // Auto-scroll logs while active
  useEffect(() => {
    if (
      selectedSession &&
      (selectedSession.status === 'starting' ||
        selectedSession.status === 'running') &&
      logContainerRef.current
    ) {
      logContainerRef.current.scrollTop = logContainerRef.current.scrollHeight
    }
  }, [selectedSession?.log_tail, selectedSession?.status])

  const handleSubmitInput = async (
    sandboxId: string,
    opts?: { eof?: boolean }
  ) => {
    setIsSendingInput(true)
    const textToSend = opts?.eof ? '' : liveInput
    if (!opts?.eof) {
      setLiveInput('')
      liveInputRef.current?.focus()
    }
    try {
      await onSendInput(sandboxId, textToSend, opts)
    } finally {
      setIsSendingInput(false)
      if (!opts?.eof) {
        window.requestAnimationFrame(() => {
          liveInputRef.current?.focus()
        })
      }
    }
  }

  if (!selectedSession) {
    return (
      <div className="rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs p-12 text-center flex flex-col items-center justify-center gap-2.5 min-h-[360px]">
        <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center">
          <Terminal size={20} />
        </div>
        <h4 className="font-heading text-base font-bold text-zinc-900 dark:text-zinc-100">
          Ready to Run
        </h4>
        <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
          Launch a command above to stream container stdout/stderr and collect
          build artifacts.
        </p>
      </div>
    )
  }

  const statusBadge = getSandboxStatusBadge(selectedSession.status)
  const isFull = variant === 'full'
  const uniqueArtifacts = dedupeArtifacts(selectedSession.artifacts)

  return (
    <div
      className={
        isFull
          ? 'rounded-2xl border border-zinc-200/80 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs overflow-hidden flex flex-col'
          : 'overflow-hidden flex flex-col'
      }
    >
      {/* Session Provenance Header */}
      {isFull && (
      <div className="p-4 border-b border-zinc-200 dark:border-zinc-800 flex flex-col sm:flex-row sm:items-center justify-between gap-3 bg-zinc-50/70 dark:bg-zinc-900/90">
        <div className="flex flex-col gap-1 min-w-0">
          <div className="flex items-center gap-2 flex-wrap">
            <span
              className={`inline-flex items-center gap-1 px-2.5 py-0.5 rounded-lg text-xs font-bold border ${statusBadge.className}`}
            >
              {statusBadge.icon}
              <span>{statusBadge.label}</span>
            </span>

            {/* Commit SHA Provenance Pill */}
            <span className="inline-flex items-center gap-1.5 px-2.5 py-0.5 rounded-lg text-xs font-mono font-semibold bg-zinc-200/70 dark:bg-zinc-800 text-zinc-800 dark:text-zinc-200 border border-zinc-300/80 dark:border-zinc-700">
              <GitBranch size={12} className="text-teal-600 dark:text-teal-400" />
              <span>{selectedSession.ref}</span>
              {selectedSession.head_sha && (
                <>
                  <span className="text-zinc-400">@</span>
                  <GitCommit
                    size={12}
                    className="text-teal-600 dark:text-teal-400"
                  />
                  <span className="text-teal-700 dark:text-teal-300">
                    {selectedSession.head_sha.slice(0, 7)}
                  </span>
                  <button
                    type="button"
                    onClick={() => {
                      navigator.clipboard.writeText(selectedSession.head_sha)
                      setCopiedSha(true)
                      setTimeout(() => setCopiedSha(false), 1500)
                    }}
                    title={`Copy full commit SHA (${selectedSession.head_sha})`}
                    className="text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
                  >
                    {copiedSha ? <Check size={11} /> : <Copy size={11} />}
                  </button>
                </>
              )}
            </span>

            <span className="inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-[11px] font-mono bg-zinc-100 dark:bg-zinc-800/80 text-zinc-600 dark:text-zinc-400 border border-zinc-200 dark:border-zinc-700">
              <Box size={11} />
              {selectedSession.image}
            </span>

            {selectedSession.exit_code !== null && (
              <span
                className={`px-2 py-0.5 rounded-md text-[11px] font-mono font-semibold ${
                  selectedSession.exit_code === 0
                    ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300'
                    : 'bg-rose-500/10 text-rose-700 dark:text-rose-300'
                }`}
              >
                exit {selectedSession.exit_code}
              </span>
            )}
          </div>

          <div className="font-mono text-xs text-zinc-700 dark:text-zinc-300 truncate mt-0.5">
            {selectedSession.setup_command ? (
              <span className="text-zinc-400">
                {selectedSession.setup_command} &amp;&amp;{' '}
              </span>
            ) : null}
            <span className="font-semibold">{selectedSession.command}</span>
          </div>
        </div>

        {/* Stop Action */}
        <div className="flex items-center gap-2 shrink-0">
          {(selectedSession.status === 'starting' ||
            selectedSession.status === 'running') &&
            canContribute && (
              <Button
                type="button"
                variant="danger"
                size="sm"
                isDisabled={stoppingId === selectedSession.id}
                onClick={() => onStopSession(selectedSession.id)}
                className="rounded-xl font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                {stoppingId === selectedSession.id ? (
                  <Loader2 size={13} className="animate-spin" />
                ) : (
                  <Square size={13} />
                )}
                <span>Stop</span>
              </Button>
            )}
        </div>
      </div>
      )}

      {/* Error Banner if present */}
      {selectedSession.error && (
        <div className="px-4 py-2.5 bg-rose-500/10 border-b border-rose-500/20 flex items-center gap-2 text-xs text-rose-700 dark:text-rose-300">
          <AlertCircle size={14} className="shrink-0" />
          <span className="font-medium">{selectedSession.error}</span>
        </div>
      )}

      {/* Collected Artifacts Panel */}
      {isFull && uniqueArtifacts.length > 0 && (
        <div className="p-4 border-b border-zinc-200 dark:border-zinc-800 bg-amber-500/5 flex flex-col gap-2.5">
          <div className="flex items-center justify-between">
            <span className="text-xs font-bold text-zinc-900 dark:text-zinc-100 inline-flex items-center gap-1.5">
              <Package size={14} className="text-amber-500" />
              Collected Build Artifacts ({uniqueArtifacts.length})
            </span>
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400 font-mono">
              commit {selectedSession.head_sha.slice(0, 7) || 'HEAD'}
            </span>
          </div>

          <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
            {uniqueArtifacts.map((art: SandboxArtifact) => {
              const downloadUrl = api.projects.getSandboxArtifactDownloadUrl(
                projectId,
                selectedSession.id,
                art.rel_path
              )
              return (
                <a
                  key={art.rel_path}
                  href={downloadUrl}
                  download={art.name}
                  className="group rounded-xl p-3 bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-800 hover:border-teal-500/50 transition flex items-center justify-between gap-3 shadow-2xs"
                >
                  <div className="flex items-center gap-2.5 min-w-0">
                    {getArtifactIcon(art.name)}
                    <div className="flex flex-col min-w-0">
                      <span className="text-xs font-bold font-mono text-zinc-900 dark:text-zinc-100 truncate group-hover:text-teal-600 dark:group-hover:text-teal-400">
                        {art.name}
                      </span>
                      <span className="text-[11px] font-mono text-zinc-400 truncate">
                        {art.rel_path} · {formatBytes(art.size_bytes)} · sha256:
                        {art.sha256.slice(0, 8)}
                      </span>
                    </div>
                  </div>
                  <span className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-500/10 text-teal-700 dark:text-teal-300 group-hover:bg-teal-500 group-hover:text-white transition shrink-0">
                    <Download size={13} />
                    <span>Download</span>
                  </span>
                </a>
              )
            })}
          </div>
        </div>
      )}

      {/* Terminal Output */}
      <div className="flex flex-col bg-zinc-950 text-zinc-100">
        <div className="px-4 py-2 border-b border-zinc-800 flex items-center justify-between text-[11px] font-mono text-zinc-400">
          <span className="inline-flex items-center gap-1.5">
            <Terminal size={12} className="text-teal-400" />
            <span>
              /workspace ({selectedSession.ref}
              {selectedSession.head_sha
                ? ` @ ${selectedSession.head_sha.slice(0, 7)}`
                : ''}
              )
            </span>
          </span>
          <button
            type="button"
            onClick={() => {
              navigator.clipboard.writeText(selectedSession.log_tail || '')
              setCopiedLogs(true)
              setTimeout(() => setCopiedLogs(false), 1500)
            }}
            className="inline-flex items-center gap-1 hover:text-zinc-200 cursor-pointer"
          >
            {copiedLogs ? <Check size={12} /> : <Copy size={12} />}
            <span>{copiedLogs ? 'Copied' : 'Copy logs'}</span>
          </button>
        </div>

        <pre
          ref={logContainerRef}
          className={`p-4 text-xs font-mono leading-relaxed overflow-x-auto whitespace-pre-wrap break-words text-zinc-200 ${
            isFull ? 'max-h-[460px] min-h-[260px]' : 'h-[min(62vh,640px)] min-h-[320px]'
          }`}
        >
          {selectedSession.log_tail ? (
            stripAnsi(selectedSession.log_tail)
          ) : selectedSession.status === 'starting' ||
            selectedSession.status === 'running' ? (
            <span className="text-zinc-500 italic">
              Waiting for container stdout/stderr stream... (If your script is
              waiting for input, type below and press Enter)
            </span>
          ) : (
            <span className="text-zinc-500 italic">
              (Command produced no stdout/stderr output)
            </span>
          )}
        </pre>

        {/* Interactive Stdin Bar while session is running */}
        {(selectedSession.status === 'starting' ||
          selectedSession.status === 'running') &&
          canContribute && (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                void handleSubmitInput(selectedSession.id)
              }}
              className="px-3 py-2.5 border-t border-zinc-800 bg-zinc-900/95 flex items-center gap-2"
            >
              <span className="text-xs font-mono font-bold text-teal-400 select-none pl-1">
                stdin &gt;
              </span>
              <input
                ref={liveInputRef}
                type="text"
                value={liveInput}
                onChange={(e) => setLiveInput(e.target.value)}
                placeholder="Type interactive input and press Enter to send to running process..."
                className="flex-1 h-8 px-3 rounded-lg text-xs font-mono bg-zinc-950 border border-zinc-700/90 text-zinc-100 placeholder-zinc-500 focus:outline-none focus:border-teal-500"
              />
              <button
                type="submit"
                disabled={isSendingInput}
                title="Send line (appends newline)"
                className="inline-flex items-center gap-1.5 h-8 px-3 rounded-lg text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white transition cursor-pointer disabled:opacity-50"
              >
                {isSendingInput ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <CornerDownLeft size={12} />
                )}
                <span>Send</span>
              </button>
              <button
                type="button"
                disabled={isSendingInput}
                onClick={() =>
                  void handleSubmitInput(selectedSession.id, {
                    eof: true,
                  })
                }
                title="Close stdin (send EOF / Ctrl+D)"
                className="inline-flex items-center gap-1 h-8 px-2.5 rounded-lg text-[11px] font-mono font-semibold bg-zinc-800 hover:bg-zinc-700 text-zinc-300 border border-zinc-700 transition cursor-pointer disabled:opacity-50"
              >
                <span>EOF (Ctrl+D)</span>
              </button>
            </form>
          )}
      </div>
    </div>
  )
}

