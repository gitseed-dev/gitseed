import React, { useMemo, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import {
  AlertCircle,
  Check,
  CheckCircle2,
  Circle,
  Edit3,
  ListTodo,
  Plus,
  Trash2,
} from 'lucide-react'
import { Button, Card } from '@heroui/react'
import { api, ApiError } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { MarkdownContent, renderInline } from '../../common/MarkdownContent'
import {
  getTaskStatusClasses,
  TASK_STATUS_LABELS,
} from '../../../utils/seeds'
import type { Seed, SeedTask, TaskStatus } from '../../../types'

export interface SeedTasksTabProps {
  seed: Seed
  tasks: SeedTask[]
  planAgentHandle?: string
  canContribute: boolean
  onRefresh: () => Promise<void>
}

export const SeedTasksTab: React.FC<SeedTasksTabProps> = ({
  seed,
  tasks,
  planAgentHandle = '@planner',
  canContribute,
  onRefresh,
}) => {
  const queryClient = useQueryClient()

  const [newTitle, setNewTitle] = useState('')
  const [newDescription, setNewDescription] = useState('')
  const [isAdding, setIsAdding] = useState(false)
  const [showAddForm, setShowAddForm] = useState(false)

  const [editingTaskId, setEditingTaskId] = useState<string | null>(null)
  const [editTitle, setEditTitle] = useState('')
  const [editDesc, setEditDesc] = useState('')
  const [error, setError] = useState<string | null>(null)

  const isWaitingForPlanApproval =
    seed.stage === 'plan' && seed.status === 'awaiting_approval'
  const isPostPlanApproval =
    (seed.stage === 'execute' || seed.stage === 'review') &&
    seed.status !== 'running' &&
    seed.status !== 'merged' &&
    seed.status !== 'closed'
  const canEditTasks =
    canContribute && (isWaitingForPlanApproval || isPostPlanApproval)

  const doneCount = useMemo(
    () => tasks.filter((t) => t.status === 'done').length,
    [tasks]
  )
  const progressPct =
    tasks.length > 0 ? Math.round((doneCount / tasks.length) * 100) : 0

  const handleAddTask = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!newTitle.trim() || isAdding) return
    setIsAdding(true)
    setError(null)
    try {
      await api.seeds.createTask(seed.id, {
        title: newTitle.trim(),
        description: newDescription.trim(),
      })
      setNewTitle('')
      setNewDescription('')
      setShowAddForm(false)
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedTasks(seed.id),
      })
      await onRefresh()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Failed to create task.'
      )
    } finally {
      setIsAdding(false)
    }
  }

  const handleStatusChange = async (task: SeedTask, nextStatus: TaskStatus) => {
    if (!canContribute || task.status === nextStatus) return
    setError(null)
    try {
      await api.seeds.updateTask(seed.id, task.id, { status: nextStatus })
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedTasks(seed.id),
      })
      await onRefresh()
    } catch (err) {
      setError(
        err instanceof ApiError ? err.detail : 'Failed to update task status.'
      )
    }
  }

  const handleSaveEdit = async (taskId: string) => {
    if (!editTitle.trim()) return
    setError(null)
    try {
      await api.seeds.updateTask(seed.id, taskId, {
        title: editTitle.trim(),
        description: editDesc.trim(),
      })
      setEditingTaskId(null)
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedTasks(seed.id),
      })
      await onRefresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to update task.')
    }
  }

  const handleDeleteTask = async (taskId: string) => {
    setError(null)
    try {
      await api.seeds.deleteTask(seed.id, taskId)
      await queryClient.invalidateQueries({
        queryKey: queryKeys.seedTasks(seed.id),
      })
      await onRefresh()
    } catch (err) {
      setError(err instanceof ApiError ? err.detail : 'Failed to delete task.')
    }
  }

  return (
    <Card className="p-5 sm:p-6 rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-5">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3.5 border-b border-zinc-100 dark:border-zinc-800">
        <div className="flex items-center gap-2.5">
          <div className="w-8 h-8 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <ListTodo size={16} />
          </div>
          <div>
            <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
              <span>Execution Tasks</span>
              {tasks.length > 0 && (
                <span className="px-2 py-0.5 rounded-full text-xs font-mono bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
                  {doneCount}/{tasks.length}
                </span>
              )}
            </h3>
            <p className="text-xs text-zinc-500 dark:text-zinc-400">
              Steps from the plan, worked through during Execute.
            </p>
          </div>
        </div>

        {canEditTasks && (
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => setShowAddForm((prev) => !prev)}
            className="rounded-xl text-xs font-semibold flex items-center gap-1.5 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
          >
            <Plus size={14} />
            <span>Add Task</span>
          </Button>
        )}
      </div>

      {tasks.length > 0 && (
        <div className="flex items-center gap-3">
          <div className="flex-1 h-2 rounded-full bg-zinc-100 dark:bg-zinc-800 overflow-hidden">
            <div
              className="h-full bg-teal-500 transition-all duration-300"
              style={{ width: `${progressPct}%` }}
            />
          </div>
          <span className="text-xs font-mono font-semibold text-zinc-600 dark:text-zinc-400 shrink-0">
            {progressPct}% complete
          </span>
        </div>
      )}

      {error && (
        <div className="p-3 rounded-xl bg-rose-500/10 border border-rose-500/25 text-xs text-rose-600 dark:text-rose-400 flex items-center gap-2">
          <AlertCircle size={14} className="shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {showAddForm && canEditTasks && (
        <form
          onSubmit={handleAddTask}
          className="p-4 rounded-xl bg-zinc-50/80 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80 flex flex-col gap-3"
        >
          <div className="flex items-center justify-between gap-2 flex-wrap">
            <span className="text-xs font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400">
              New Task
            </span>
            {isPostPlanApproval && (
              <span className="text-xs text-amber-600 dark:text-amber-400">
                Adding a task will return the Plan to its approval gate.
              </span>
            )}
          </div>
          <input
            type="text"
            value={newTitle}
            onChange={(e) => setNewTitle(e.target.value)}
            placeholder="Task title (e.g. Implement Redis sliding-window rate limiter)"
            autoFocus
            required
            className="w-full h-10 px-3.5 text-sm rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
          />
          <textarea
            rows={2}
            value={newDescription}
            onChange={(e) => setNewDescription(e.target.value)}
            placeholder="Optional implementation instructions or target files..."
            className="w-full px-3.5 py-2.5 text-sm rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 resize-y"
          />
          <div className="flex items-center justify-end gap-2">
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setShowAddForm(false)}
              className="rounded-xl text-xs font-semibold cursor-pointer"
            >
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              isDisabled={isAdding || !newTitle.trim()}
              className="rounded-xl text-xs font-semibold cursor-pointer"
            >
              {isAdding ? 'Adding...' : 'Add Task'}
            </Button>
          </div>
        </form>
      )}

      {tasks.length === 0 ? (
        <div className="py-10 flex flex-col items-center justify-center text-center px-4 gap-2">
          <div className="w-11 h-11 rounded-2xl bg-zinc-100 dark:bg-zinc-800/80 border border-zinc-200/70 dark:border-zinc-700/60 text-zinc-400 flex items-center justify-center mb-0.5">
            <ListTodo size={20} />
          </div>
          <h4 className="font-heading text-sm font-bold text-zinc-900 dark:text-zinc-100">
            No tasks yet
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-md leading-relaxed">
            {planAgentHandle} breaks the work into steps during Plan.
          </p>
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-zinc-200/70 dark:divide-zinc-700/60 rounded-xl border border-zinc-200 dark:border-zinc-700/80 overflow-hidden">
          {tasks.map((task, idx) => {
            const isEditing = editingTaskId === task.id
            const isDone = task.status === 'done'

            return (
              <div
                key={task.id}
                className="p-4 bg-white dark:bg-zinc-900/40 hover:bg-zinc-50/70 dark:hover:bg-zinc-800/30 transition flex flex-col gap-2"
              >
                {isEditing ? (
                  <div className="flex flex-col gap-2.5">
                    {isPostPlanApproval && (
                      <span className="text-xs text-amber-600 dark:text-amber-400">
                        Saving changes will return the Plan to its approval gate.
                      </span>
                    )}
                    <input
                      type="text"
                      value={editTitle}
                      onChange={(e) => setEditTitle(e.target.value)}
                      className="w-full h-10 px-3 text-sm font-semibold rounded-lg bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                    />
                    <textarea
                      rows={2}
                      value={editDesc}
                      onChange={(e) => setEditDesc(e.target.value)}
                      className="w-full px-3 py-2 text-sm rounded-lg bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 resize-y"
                    />
                    <div className="flex items-center justify-end gap-2">
                      <Button
                        type="button"
                        variant="secondary"
                        size="sm"
                        onClick={() => setEditingTaskId(null)}
                        className="rounded-lg text-xs cursor-pointer"
                      >
                        Cancel
                      </Button>
                      <Button
                        type="button"
                        variant="primary"
                        size="sm"
                        onClick={() => handleSaveEdit(task.id)}
                        isDisabled={!editTitle.trim()}
                        className="rounded-lg text-xs flex items-center gap-1 cursor-pointer"
                      >
                        <Check size={13} />
                        <span>Save</span>
                      </Button>
                    </div>
                  </div>
                ) : (
                  <div className="flex items-start justify-between gap-3">
                    <div className="flex items-start gap-3 min-w-0">
                      <button
                        type="button"
                        disabled={!canEditTasks}
                        onClick={() =>
                          handleStatusChange(
                            task,
                            isDone ? 'pending' : 'done'
                          )
                        }
                        className="mt-0.5 text-zinc-400 hover:text-teal-500 transition cursor-pointer disabled:cursor-default shrink-0"
                        title={
                          isDone ? 'Mark as pending' : 'Mark as completed'
                        }
                      >
                        {isDone ? (
                          <CheckCircle2
                            size={17}
                            className="text-emerald-500"
                          />
                        ) : (
                          <Circle size={17} />
                        )}
                      </button>

                      <div className="min-w-0 flex-1">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-xs font-mono text-zinc-400">
                            #{idx + 1}
                          </span>
                          <span
                            className={`text-sm font-semibold wrap-break-word ${
                              isDone
                                ? 'line-through text-zinc-400 dark:text-zinc-500'
                                : 'text-zinc-900 dark:text-zinc-100'
                            }`}
                          >
                            {renderInline(task.title)}
                          </span>
                        </div>
                        {task.description && (
                          <div className="mt-1.5 min-w-0 text-zinc-600 dark:text-zinc-300">
                            <MarkdownContent content={task.description} />
                          </div>
                        )}
                      </div>
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      {canEditTasks ? (
                        <select
                          value={task.status}
                          onChange={(e) =>
                            handleStatusChange(
                              task,
                              e.target.value as TaskStatus
                            )
                          }
                          className={`px-2.5 py-1 rounded-lg text-[11px] font-semibold border cursor-pointer focus:outline-none ${getTaskStatusClasses(
                            task.status
                          )}`}
                        >
                          {(
                            [
                              'pending',
                              'running',
                              'done',
                              'failed',
                              'skipped',
                            ] as TaskStatus[]
                          ).map((st) => (
                            <option key={st} value={st}>
                              {TASK_STATUS_LABELS[st]}
                            </option>
                          ))}
                        </select>
                      ) : (
                        <span
                          className={`px-2.5 py-0.5 rounded-lg text-[11px] font-semibold border ${getTaskStatusClasses(
                            task.status
                          )}`}
                        >
                          {TASK_STATUS_LABELS[task.status]}
                        </span>
                      )}

                      {canEditTasks && (
                        <>
                          <button
                            type="button"
                            onClick={() => {
                              setEditingTaskId(task.id)
                              setEditTitle(task.title)
                              setEditDesc(task.description || '')
                            }}
                            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 transition cursor-pointer"
                            title="Edit task"
                          >
                            <Edit3 size={13} />
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteTask(task.id)}
                            className="p-1.5 rounded-lg text-zinc-400 hover:text-rose-600 dark:hover:text-rose-400 transition cursor-pointer"
                            title="Delete task"
                          >
                            <Trash2 size={13} />
                          </button>
                        </>
                      )}
                    </div>
                  </div>
                )}
              </div>
            )
          })}
        </div>
      )}
    </Card>
  )
}

