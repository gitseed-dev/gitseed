import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  BookOpen,
  Check,
  Copy,
  Trash2,
  X,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { slugifyAgentHandle } from '../../utils/agents'
import type { Skill } from '../../types'

export interface SkillDrawerProps {
  isOpen: boolean
  onClose: () => void
  skillToEdit?: Skill | null
  onDelete?: (skill: Skill) => void
}

export const SkillDrawer: React.FC<SkillDrawerProps> = ({
  isOpen,
  onClose,
  skillToEdit,
  onDelete,
}) => {
  const queryClient = useQueryClient()

  const [name, setName] = useState('')
  const [slug, setSlug] = useState('')
  const [isSlugCustom, setIsSlugCustom] = useState(false)
  const [editingSlug, setEditingSlug] = useState(false)
  const [description, setDescription] = useState('')
  const [content, setContent] = useState('')

  const [isSaving, setIsSaving] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState(false)

  const isEditing = Boolean(skillToEdit)

  useEffect(() => {
    if (!isOpen) return
    setErrorMessage(null)
    setEditingSlug(false)

    if (skillToEdit) {
      setName(skillToEdit.name)
      setSlug(skillToEdit.slug)
      setIsSlugCustom(true)
      setDescription(skillToEdit.description || '')
      setContent(skillToEdit.content || '')
    } else {
      setName('')
      setSlug('')
      setIsSlugCustom(false)
      setDescription('')
      setContent('')
    }
  }, [isOpen, skillToEdit])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !isSaving) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isSaving, onClose])

  const handleNameChange = (nextName: string) => {
    setName(nextName)
    if (!isSlugCustom && !isEditing) {
      setSlug(slugifyAgentHandle(nextName))
    }
  }

  const isDirty = useMemo(() => {
    if (!skillToEdit) return true
    if (name.trim() !== skillToEdit.name) return true
    if (slug.trim() !== skillToEdit.slug) return true
    if (description.trim() !== (skillToEdit.description || '').trim()) return true
    if (content.trim() !== (skillToEdit.content || '').trim()) return true
    return false
  }, [skillToEdit, name, slug, description, content])

  const isSaveDisabled =
    isSaving || !name.trim() || !slug.trim() || (isEditing && !isDirty)

  const handleCopyId = () => {
    if (!skillToEdit) return
    navigator.clipboard.writeText(skillToEdit.id)
    setCopiedId(true)
    setTimeout(() => setCopiedId(false), 1800)
  }

  const handleSave = async () => {
    if (isSaveDisabled) return
    setIsSaving(true)
    setErrorMessage(null)

    try {
      if (skillToEdit) {
        await api.skills.update(skillToEdit.id, {
          name: name.trim(),
          slug: slug.trim().toLowerCase(),
          description: description.trim(),
          content: content.trim(),
        })
      } else {
        await api.skills.create({
          name: name.trim(),
          slug: slug.trim().toLowerCase(),
          description: description.trim(),
          content: content.trim(),
        })
      }

      await queryClient.invalidateQueries({ queryKey: queryKeys.skills })
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.detail)
      } else {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to save skill')
      }
    } finally {
      setIsSaving(false)
    }
  }

  if (!isOpen) return null

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-xs transition-opacity"
      onClick={() => {
        if (!isSaving) onClose()
      }}
    >
      <div
        className="w-full max-w-xl bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-700/80 h-full flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-4 shrink-0 bg-zinc-50/50 dark:bg-zinc-900/50">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-xl bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
              <BookOpen size={18} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold font-heading text-zinc-900 dark:text-zinc-100 truncate">
                  {isEditing ? skillToEdit?.name : 'New skill'}
                </h2>
                {slug && (
                  <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                    {slug}
                  </span>
                )}
              </div>
              {isEditing && skillToEdit && (
                <div className="flex items-center gap-2 mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  <button
                    type="button"
                    onClick={handleCopyId}
                    className="inline-flex items-center gap-1 font-mono hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                  >
                    <span>{skillToEdit.id}</span>
                    {copiedId ? (
                      <Check size={11} className="text-emerald-500" />
                    ) : (
                      <Copy size={11} />
                    )}
                  </button>
                </div>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-xl text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6 flex flex-col gap-5">
          {errorMessage && (
            <div className="p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs flex items-start gap-2.5">
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <span className="leading-relaxed">{errorMessage}</span>
            </div>
          )}

          {/* Name + Slug */}
          <div>
            <div className="flex items-center justify-between mb-1.5">
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                Skill Name <span className="text-rose-500">*</span>
              </label>
              <div className="flex items-center gap-1.5 text-xs">
                <span className="text-zinc-400 font-mono">
                  {slug || 'skill-slug'}
                </span>
                <button
                  type="button"
                  onClick={() => {
                    setEditingSlug((prev) => !prev)
                    if (!editingSlug) setIsSlugCustom(true)
                  }}
                  className="text-[11px] font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                >
                  {editingSlug ? 'done' : 'edit slug'}
                </button>
              </div>
            </div>
            <input
              type="text"
              value={name}
              onChange={(e) => handleNameChange(e.target.value)}
              placeholder="e.g. OWASP Security Checklist or Python FastAPI Conventions"
              className="w-full px-3.5 py-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition"
            />

            {editingSlug && (
              <div className="mt-2">
                <input
                  type="text"
                  value={slug}
                  onChange={(e) => {
                    setIsSlugCustom(true)
                    setSlug(
                      e.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9-]/g, '')
                        .slice(0, 48)
                    )
                  }}
                  placeholder="owasp-security-checklist"
                  className="w-full px-3.5 py-2 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition"
                />
              </div>
            )}
          </div>

          {/* Description */}
          <div>
            <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
              Description
            </label>
            <input
              type="text"
              value={description}
              onChange={(e) => setDescription(e.target.value)}
              placeholder="Short summary of what this skill teaches or enforces..."
              className="w-full px-3.5 py-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-xs text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition"
            />
          </div>

          {/* Playbook Markdown Content */}
          <div>
            <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
              Skill Playbook (Markdown)
            </label>
            <textarea
              rows={12}
              value={content}
              onChange={(e) => setContent(e.target.value)}
              placeholder={`# Guidelines\n- Follow repository naming conventions\n- Validate inputs with Pydantic schemas\n- Add regression tests for every bug fix`}
              className="w-full px-3.5 py-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono leading-relaxed text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition resize-y"
            />
          </div>
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/50 dark:bg-zinc-900/50 flex items-center justify-between gap-3 shrink-0">
          <div>
            {isEditing && skillToEdit && onDelete && (
              <button
                type="button"
                onClick={() => onDelete(skillToEdit)}
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:underline cursor-pointer"
              >
                <Trash2 size={13} />
                <span>Delete Skill</span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={onClose}
              isDisabled={isSaving}
              className="cursor-pointer font-medium"
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isDisabled={isSaveDisabled}
              className="cursor-pointer font-semibold shadow-sm shadow-teal-600/20"
            >
              {isSaving
                ? 'Saving...'
                : isEditing
                  ? 'Save Changes'
                  : 'Create skill'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
