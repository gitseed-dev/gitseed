import React, { useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  ChevronDown,
  ChevronRight,
  Edit3,
  MessageSquare,
  Send,
  Trash2,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api, ApiError } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { ConfirmDialog } from '../../common/ConfirmDialog'
import { MarkdownContent } from '../../common/MarkdownContent'
import { StageBadge } from '../../common/StageBadge'
import { VoiceDictationButton } from '../../common/VoiceDictationButton'
import { useAuth } from '../../../context/AuthContext'
import { formatRelativeTime } from '../../../utils/format'
import type { SeedComment } from '../../../types'

export interface SeedDiscussionCardProps {
  seedId: string
  comments: SeedComment[]
  canContribute: boolean
  canManageProject: boolean
  resolveActorName: (actorType: string, actorId: string | null) => string
  onRefresh: () => Promise<void>
  expandTrigger?: number
}

export const SeedDiscussionCard: React.FC<SeedDiscussionCardProps> = ({
  seedId,
  comments,
  canContribute,
  canManageProject,
  resolveActorName,
  onRefresh,
  expandTrigger,
}) => {
  const queryClient = useQueryClient()
  const { user } = useAuth()

  const [isDiscussionCollapsed, setIsDiscussionCollapsed] = useState(false)

  React.useEffect(() => {
    if (expandTrigger && expandTrigger > 0) {
      setIsDiscussionCollapsed(false)
    }
  }, [expandTrigger])
  const [commentBody, setCommentBody] = useState('')
  const [isPostingComment, setIsPostingComment] = useState(false)
  const [editingCommentId, setEditingCommentId] = useState<string | null>(null)
  const [editingCommentBody, setEditingCommentBody] = useState('')
  const [deletingCommentId, setDeletingCommentId] = useState<string | null>(null)
  const [isDeletingComment, setIsDeletingComment] = useState(false)
  const [commentError, setCommentError] = useState<string | null>(null)

  const activeComments = comments.filter((c) => !c.deleted_at)

  const handlePostComment = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!commentBody.trim() || isPostingComment) return
    setIsPostingComment(true)
    setCommentError(null)
    try {
      await api.seeds.createComment(seedId, { body: commentBody.trim() })
      setCommentBody('')
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedComments(seedId),
      })
      await onRefresh()
    } catch (err) {
      setCommentError(
        err instanceof ApiError ? err.detail : 'Failed to post comment.'
      )
    } finally {
      setIsPostingComment(false)
    }
  }

  const handleUpdateComment = async (commentId: string) => {
    if (!editingCommentBody.trim()) return
    setCommentError(null)
    try {
      await api.seeds.updateComment(seedId, commentId, {
        body: editingCommentBody.trim(),
      })
      setEditingCommentId(null)
      setEditingCommentBody('')
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedComments(seedId),
      })
      await onRefresh()
    } catch (err) {
      setCommentError(
        err instanceof ApiError ? err.detail : 'Failed to update comment.'
      )
    }
  }

  const handleConfirmDeleteComment = async () => {
    if (!deletingCommentId || isDeletingComment) return
    setIsDeletingComment(true)
    setCommentError(null)
    try {
      await api.seeds.deleteComment(seedId, deletingCommentId)
      setDeletingCommentId(null)
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedComments(seedId),
      })
      await onRefresh()
    } catch (err) {
      setCommentError(
        err instanceof ApiError ? err.detail : 'Failed to delete comment.'
      )
    } finally {
      setIsDeletingComment(false)
    }
  }

  return (
    <div id="seed-discussion-section" className="scroll-mt-6">
      <Card className="p-5 sm:p-6 min-w-0 overflow-hidden rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-4">
        <div
          className={`flex items-center justify-between ${
            isDiscussionCollapsed
              ? ''
              : 'pb-3 border-b border-zinc-100 dark:border-zinc-800'
          }`}
        >
          <button
            type="button"
            onClick={() => setIsDiscussionCollapsed((prev) => !prev)}
            className="flex items-center gap-2.5 text-left cursor-pointer group"
          >
            <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
              <MessageSquare size={16} />
            </div>
            <div>
              <div className="flex items-center gap-1.5">
                <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100">
                  Discussion ({activeComments.length})
                </h3>
                <span className="text-zinc-400 group-hover:text-zinc-700 dark:group-hover:text-zinc-200 transition">
                  {isDiscussionCollapsed ? (
                    <ChevronRight size={16} />
                  ) : (
                    <ChevronDown size={16} />
                  )}
                </span>
              </div>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Operator notes, review feedback, and stage guidance
              </p>
            </div>
          </button>
        </div>

        {!isDiscussionCollapsed && (
          <>
            {commentError && (
              <div className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs text-rose-600 dark:text-rose-400">
                {commentError}
              </div>
            )}

            {activeComments.length === 0 ? (
              <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400 py-2">
                No comments yet. Leave a note or guidance for this seed below.
              </p>
            ) : (
              <div className="flex flex-col gap-3">
                {activeComments.map((c, idx) => {
                  const isOwn =
                    c.author_type === 'user' && c.author_id === user?.id
                  const canDelete = isOwn || canManageProject
                  const isEditingThis = editingCommentId === c.id
                  const isLatestComment = idx === activeComments.length - 1

                  return (
                    <div
                      key={c.id}
                      id={isLatestComment ? 'seed-latest-comment' : undefined}
                      className="p-3.5 min-w-0 overflow-hidden rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-2"
                    >
                      <div className="flex items-center justify-between gap-2 text-xs">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                            {resolveActorName(c.author_type, c.author_id)}
                          </span>
                          <StageBadge stage={c.stage} />
                          <span className="text-zinc-400">·</span>
                          <span className="text-zinc-500 dark:text-zinc-400">
                            {formatRelativeTime(c.created_at)}
                          </span>
                          {c.edited_at && (
                            <span className="text-xs text-zinc-400">
                              (edited)
                            </span>
                          )}
                        </div>

                        <div className="flex items-center gap-1.5">
                          {isOwn && !isEditingThis && (
                            <button
                              type="button"
                              onClick={() => {
                                setEditingCommentId(c.id)
                                setEditingCommentBody(c.body)
                              }}
                              className="p-1 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 transition cursor-pointer"
                              title="Edit comment"
                            >
                              <Edit3 size={12} />
                            </button>
                          )}
                          {canDelete && (
                            <button
                              type="button"
                              onClick={() => setDeletingCommentId(c.id)}
                              className="p-1 rounded-lg text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 transition cursor-pointer"
                              title="Delete comment"
                            >
                              <Trash2 size={12} />
                            </button>
                          )}
                        </div>
                      </div>

                      {isEditingThis ? (
                        <div className="flex flex-col gap-2">
                          <div className="relative">
                            <textarea
                              rows={2}
                              value={editingCommentBody}
                              onChange={(e) =>
                                setEditingCommentBody(e.target.value)
                              }
                              className="w-full p-2.5 pr-9 rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                            />
                            <VoiceDictationButton
                              className="absolute right-2 top-2"
                              onTranscript={(spoken) =>
                                setEditingCommentBody((prev) =>
                                  prev.trim() ? `${prev.trim()} ${spoken}` : spoken
                                )
                              }
                            />
                          </div>
                          <div className="flex items-center justify-end gap-2">
                            <Button
                              type="button"
                              variant="secondary"
                              size="sm"
                              onClick={() => setEditingCommentId(null)}
                              className="rounded-lg text-xs cursor-pointer"
                            >
                              Cancel
                            </Button>
                            <Button
                              type="button"
                              variant="primary"
                              size="sm"
                              onClick={() => handleUpdateComment(c.id)}
                              isDisabled={!editingCommentBody.trim()}
                              className="rounded-lg text-xs cursor-pointer"
                            >
                              Save
                            </Button>
                          </div>
                        </div>
                      ) : (
                        <MarkdownContent content={c.body} />
                      )}
                    </div>
                  )
                })}
              </div>
            )}

            {canContribute && (
              <form
                onSubmit={handlePostComment}
                className="flex items-start gap-2 pt-2 border-t border-zinc-100 dark:border-zinc-800"
              >
                <div className="relative flex-1 flex">
                  <textarea
                    rows={2}
                    value={commentBody}
                    onChange={(e) => setCommentBody(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' && (e.metaKey || e.ctrlKey)) {
                        e.preventDefault()
                        handlePostComment(e)
                      }
                    }}
                    placeholder="Add a comment or instruction for this seed..."
                    className="w-full px-3.5 py-2.5 pr-9 text-sm rounded-xl bg-zinc-50 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500 resize-y"
                  />
                  <VoiceDictationButton
                    className="absolute right-2 top-2.5"
                    onTranscript={(spoken) =>
                      setCommentBody((prev) =>
                        prev.trim() ? `${prev.trim()} ${spoken}` : spoken
                      )
                    }
                  />
                </div>
                <Button
                  type="submit"
                  variant="primary"
                  size="sm"
                  isDisabled={isPostingComment || !commentBody.trim()}
                  className="rounded-xl text-xs font-semibold flex items-center gap-1.5 py-2 shrink-0 cursor-pointer"
                >
                  <Send size={13} />
                  <span>{isPostingComment ? 'Posting...' : 'Post'}</span>
                </Button>
              </form>
            )}
          </>
        )}
      </Card>

      <ConfirmDialog
        isOpen={Boolean(deletingCommentId)}
        title="Delete comment"
        message="Are you sure you want to delete this comment? This action cannot be undone."
        confirmLabel={isDeletingComment ? 'Deleting...' : 'Delete comment'}
        danger
        isLoading={isDeletingComment}
        onConfirm={handleConfirmDeleteComment}
        onCancel={() => {
          if (!isDeletingComment) setDeletingCommentId(null)
        }}
      />
    </div>
  )
}
