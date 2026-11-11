import React, { useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Download, FileText, Image as ImageIcon, Loader2, Paperclip, Trash2 } from 'lucide-react'
import { Card } from '@heroui/react'
import { api } from '../../../api/client'
import { useData } from '../../../context/DataContext'
import { formatRelativeTime } from '../../../utils/format'
import { filesFromClipboard, formatFileSize, validateAttachments } from '../../seeds/AttachmentPicker'
import type { Seed } from '../../../types'

/**
 * Files attached to a seed. Agents get them in `/workspace/.gitseed/attachments/` on every
 * run (listed in the prompt, opened as needed); they're never committed.
 */
export const SeedAttachmentsCard: React.FC<{ seed: Seed; canContribute: boolean }> = ({ seed, canContribute }) => {
  const queryClient = useQueryClient()
  const { users } = useData()
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [confirmId, setConfirmId] = useState<string | null>(null)

  const queryKey = ['seedAttachments', seed.id]
  const { data: files = [], isLoading } = useQuery({
    queryKey,
    queryFn: () => api.seeds.listAttachments(seed.id),
  })

  const isOpen = seed.status !== 'merged' && seed.status !== 'closed'
  const canEdit = canContribute && isOpen

  const upload = async (incoming: File[]) => {
    if (incoming.length === 0) return
    const err = validateAttachments(files, incoming)
    if (err) {
      setError(err)
      return
    }
    setBusy(true)
    setError(null)
    try {
      for (const f of incoming) await api.seeds.uploadAttachment(seed.id, f)
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Upload failed')
    } finally {
      setBusy(false)
      await queryClient.invalidateQueries({ queryKey })
    }
  }

  const remove = async (id: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.seeds.deleteAttachment(seed.id, id)
      setConfirmId(null)
      await queryClient.invalidateQueries({ queryKey })
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove the file')
    } finally {
      setBusy(false)
    }
  }

  // Nothing to show and nothing to do: keep the sidebar quiet
  if (!isLoading && files.length === 0 && !canEdit) return null

  const who = (id: string | null) => {
    const u = id ? users.find((x) => x.id === id) : undefined
    return u ? u.preferred_name || u.name || u.email : null
  }

  return (
    <Card
      onPaste={canEdit ? (e: React.ClipboardEvent) => void upload(filesFromClipboard(e)) : undefined}
      className="p-5 sm:p-6 min-w-0 overflow-hidden rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-3"
    >
      <div className="flex items-center justify-between pb-2.5 border-b border-zinc-100 dark:border-zinc-800">
        <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
          Files
          {files.length > 0 && (
            <span className="text-xs font-semibold px-2 py-0.5 rounded-full font-mono bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700">
              {files.length}
            </span>
          )}
        </h3>
        {canEdit && (
          <button
            type="button"
            disabled={busy}
            onClick={() => inputRef.current?.click()}
            className="inline-flex items-center gap-1.5 text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer disabled:opacity-50"
          >
            {busy ? <Loader2 size={13} className="animate-spin" /> : <Paperclip size={13} />}
            Add
          </button>
        )}
      </div>

      {isLoading ? (
        <div className="text-xs text-zinc-400">Loading…</div>
      ) : files.length === 0 ? (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Attach mockups, specs, logs or screenshots. Agents can open them on every run.
        </p>
      ) : (
        <ul className="flex flex-col gap-1">
          {files.map((f) => (
            <li key={f.id} className="group flex items-center gap-2 min-w-0 py-1">
              {f.content_type.startsWith('image/') ? (
                <ImageIcon size={14} className="text-zinc-400 shrink-0" />
              ) : (
                <FileText size={14} className="text-zinc-400 shrink-0" />
              )}
              <div className="min-w-0 flex-1">
                <a
                  href={api.seeds.attachmentDownloadUrl(seed.id, f.id)}
                  className="block text-sm text-zinc-800 dark:text-zinc-200 hover:text-teal-600 dark:hover:text-teal-400 truncate"
                  title={`Download ${f.filename}`}
                >
                  {f.filename}
                </a>
                <span className="text-[11px] text-zinc-400">
                  {formatFileSize(f.size)}
                  {who(f.created_by) ? ` · ${who(f.created_by)}` : ''}
                  {f.created_at ? ` · ${formatRelativeTime(f.created_at)}` : ''}
                </span>
              </div>
              {confirmId === f.id ? (
                <span className="flex items-center gap-1 shrink-0 text-xs">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void remove(f.id)}
                    className="px-2 py-0.5 rounded-md bg-rose-600 hover:bg-rose-500 text-white font-semibold cursor-pointer"
                  >
                    Remove
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmId(null)}
                    className="px-1.5 py-0.5 text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                  >
                    Cancel
                  </button>
                </span>
              ) : (
                <span className="flex items-center gap-0.5 shrink-0 opacity-60 group-hover:opacity-100">
                  <a
                    href={api.seeds.attachmentDownloadUrl(seed.id, f.id)}
                    className="p-1 rounded-md text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200"
                    title="Download"
                  >
                    <Download size={13} />
                  </a>
                  {canEdit && (
                    <button
                      type="button"
                      onClick={() => setConfirmId(f.id)}
                      className="p-1 rounded-md text-zinc-400 hover:text-rose-600 cursor-pointer"
                      title="Remove"
                    >
                      <Trash2 size={13} />
                    </button>
                  )}
                </span>
              )}
            </li>
          ))}
        </ul>
      )}

      {error && <div className="text-xs text-rose-600 dark:text-rose-400">{error}</div>}
      <input
        ref={inputRef}
        type="file"
        multiple
        className="hidden"
        onChange={(e) => {
          void upload(Array.from(e.target.files || []))
          e.target.value = ''
        }}
      />
    </Card>
  )
}

