import React, { useRef, useState } from 'react'
import { Paperclip, X } from 'lucide-react'

export const ATTACHMENT_LIMITS = {
  maxFileBytes: 10 * 1024 * 1024,
  maxTotalBytes: 25 * 1024 * 1024,
  maxFiles: 10,
}

export function formatFileSize(bytes: number): string {
  if (bytes >= 1024 * 1024) return `${(bytes / 1024 / 1024).toFixed(1)} MB`
  return `${Math.max(1, Math.round(bytes / 1024))} KB`
}

/** Check new files against the limits (with files already attached); returns an error or null. */
export function validateAttachments(existing: { size: number }[], incoming: File[]): string | null {
  const tooBig = incoming.find((f) => f.size > ATTACHMENT_LIMITS.maxFileBytes)
  if (tooBig) return `${tooBig.name} is larger than ${formatFileSize(ATTACHMENT_LIMITS.maxFileBytes)}.`
  if (existing.length + incoming.length > ATTACHMENT_LIMITS.maxFiles)
    return `A seed can have at most ${ATTACHMENT_LIMITS.maxFiles} files.`
  const total = [...existing, ...incoming].reduce((sum, f) => sum + f.size, 0)
  if (total > ATTACHMENT_LIMITS.maxTotalBytes)
    return `Files can total at most ${formatFileSize(ATTACHMENT_LIMITS.maxTotalBytes)} per seed.`
  return null
}

/** Name pasted screenshots (clipboard images arrive as "image.png") so they're distinguishable. */
export function filesFromClipboard(e: React.ClipboardEvent): File[] {
  const files = Array.from(e.clipboardData?.files || [])
  const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
  return files.map((f, i) =>
    f.name && f.name !== 'image.png'
      ? f
      : new File([f], `screenshot-${stamp}${files.length > 1 ? `-${i + 1}` : ''}.png`, { type: f.type || 'image/png' })
  )
}

export interface AttachmentPickerProps {
  files: File[]
  onChange: (files: File[]) => void
  disabled?: boolean
}

/**
 * Files to attach to a new seed: drop zone + picker, listed as removable chips. The files are
 * uploaded after the seed is created; agents get them as reference material.
 */
export const AttachmentPicker: React.FC<AttachmentPickerProps> = ({ files, onChange, disabled }) => {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [dragging, setDragging] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const add = (incoming: File[]) => {
    if (incoming.length === 0) return
    const err = validateAttachments(files, incoming)
    setError(err)
    if (!err) onChange([...files, ...incoming])
  }

  return (
    <div className="flex flex-col gap-1.5">
      <div
        onDragOver={(e) => {
          e.preventDefault()
          if (!disabled) setDragging(true)
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={(e) => {
          e.preventDefault()
          setDragging(false)
          if (!disabled) add(Array.from(e.dataTransfer.files || []))
        }}
        className={`flex flex-wrap items-center gap-1.5 px-3 py-2 rounded-xl border border-dashed transition ${
          dragging
            ? 'border-teal-500 bg-teal-500/5'
            : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/30'
        }`}
      >
        <button
          type="button"
          disabled={disabled}
          onClick={() => inputRef.current?.click()}
          className="inline-flex items-center gap-1.5 text-xs font-semibold text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer disabled:opacity-50"
        >
          <Paperclip size={13} />
          Attach files
        </button>
        {files.length === 0 && (
          <span className="text-xs text-zinc-400">or drop / paste them here · up to {ATTACHMENT_LIMITS.maxFiles} files</span>
        )}
        {files.map((f, i) => (
          <span
            key={`${f.name}-${i}`}
            className="inline-flex items-center gap-1.5 max-w-[240px] pl-2 pr-1 py-0.5 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-xs text-zinc-700 dark:text-zinc-200"
          >
            <span className="truncate">{f.name}</span>
            <span className="text-zinc-400 shrink-0">{formatFileSize(f.size)}</span>
            <button
              type="button"
              disabled={disabled}
              onClick={() => onChange(files.filter((_, j) => j !== i))}
              className="p-0.5 rounded text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 cursor-pointer"
              aria-label={`Remove ${f.name}`}
            >
              <X size={11} />
            </button>
          </span>
        ))}
        <input
          ref={inputRef}
          type="file"
          multiple
          className="hidden"
          onChange={(e) => {
            add(Array.from(e.target.files || []))
            e.target.value = ''
          }}
        />
      </div>
      {error && <span className="text-xs text-rose-600 dark:text-rose-400">{error}</span>}
    </div>
  )
}

