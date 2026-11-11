import React from 'react'
import { Archive, Server } from 'lucide-react'
import { Button } from '@heroui/react'
import { ConfirmDialog } from '../../common/ConfirmDialog'
import { ProviderIcon } from '../../common/ProviderIcon'
import { SegmentedControl } from '../../common/SegmentedControl'
import { useTranslation } from '../../../context/I18nContext'
import type { Backend, Project } from '../../../types'

export interface ProjectAccessAndDangerCardsProps {
  project: Project
  backends: Backend[]
  canManage: boolean
  confirmArchiveOpen: boolean
  setConfirmArchiveOpen: (open: boolean) => void
  onToggleBackendsRestricted: (restricted: boolean) => Promise<void>
  onToggleAllowedBackend: (backendId: string, isAllowed: boolean) => Promise<void>
  onArchiveOrRestore: () => Promise<void>
}

export const ProjectAccessAndDangerCards: React.FC<
  ProjectAccessAndDangerCardsProps
> = ({
  project,
  backends,
  canManage,
  confirmArchiveOpen,
  setConfirmArchiveOpen,
  onToggleBackendsRestricted,
  onToggleAllowedBackend,
  onArchiveOrRestore,
}) => {
  const { t } = useTranslation()

  return (
    <>
      {/* 4. Backend Access Boundary */}
      <div className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-zinc-200/80 dark:border-zinc-700/80 rounded-2xl shadow-xs overflow-hidden">
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex flex-col sm:flex-row sm:items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <div className="w-8 h-8 rounded-xl bg-teal-500/10 border border-teal-500/20 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
              <Server size={16} />
            </div>
            <div>
              <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
                Backend Access Boundary
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Control which model gateways, Git providers, MCP tools, and compute drivers this project can use
              </p>
            </div>
          </div>

          <SegmentedControl<'all' | 'restricted'>
            ariaLabel="Backend access boundary"
            selectedId={project.backends_restricted ? 'restricted' : 'all'}
            onSelect={(val: 'all' | 'restricted') =>
              void onToggleBackendsRestricted(val === 'restricted')
            }
            disabled={!canManage}
            items={[
              { id: 'all', label: 'All Org Backends' },
              { id: 'restricted', label: 'Restricted List' },
            ]}
          />
        </div>

        {project.backends_restricted && (
          <div className="p-6">
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-2.5">
              {backends.map((backend) => {
                const isAllowed = (project.allowed_backend_ids || []).includes(
                  backend.id
                )
                return (
                  <label
                    key={backend.id}
                    className={`flex items-center justify-between gap-3 p-3 rounded-xl border transition cursor-pointer ${
                      isAllowed
                        ? 'bg-teal-500/[0.06] dark:bg-teal-500/10 border-teal-500/30'
                        : 'bg-zinc-50/60 dark:bg-zinc-800/30 border-zinc-200 dark:border-zinc-700/80'
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <input
                        type="checkbox"
                        checked={isAllowed}
                        disabled={!canManage}
                        onChange={() =>
                          void onToggleAllowedBackend(backend.id, isAllowed)
                        }
                        className="rounded border-zinc-300 text-teal-600 focus:ring-teal-500 cursor-pointer"
                      />
                      <span className="text-zinc-600 dark:text-zinc-300 shrink-0">
                        <ProviderIcon
                          type={backend.type}
                          provider={backend.provider}
                          size={15}
                        />
                      </span>
                      <div className="flex flex-col min-w-0">
                        <span className="text-xs font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                          {backend.name}
                        </span>
                        <span className="text-[11px] font-mono text-zinc-400 uppercase">
                          {backend.type} · {backend.provider}
                        </span>
                      </div>
                    </div>
                  </label>
                )
              })}
            </div>
          </div>
        )}
      </div>

      {/* 5. Danger Zone (Archive / Restore) */}
      {canManage && (
        <div className="bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl border border-rose-500/30 rounded-2xl shadow-xs overflow-hidden">
          <div className="px-6 py-4 flex flex-col sm:flex-row sm:items-center justify-between gap-4">
            <div className="flex items-center gap-3">
              <div className="w-8 h-8 rounded-xl bg-rose-500/10 border border-rose-500/20 text-rose-600 dark:text-rose-400 flex items-center justify-center shrink-0">
                <Archive size={16} />
              </div>
              <div>
                <h3 className="font-heading text-sm sm:text-base font-bold text-zinc-900 dark:text-zinc-100">
                  {project.archived_at ? 'Restore Project' : 'Archive Project'}
                </h3>
                <p className="text-xs text-zinc-500 dark:text-zinc-400">
                  {project.archived_at
                    ? 'Restore this project to active pipelines and the sidebar project switcher.'
                    : 'Archiving freezes new seed dispatching and hides the project from active lists.'}
                </p>
              </div>
            </div>

            <Button
              type="button"
              variant={project.archived_at ? 'primary' : 'danger'}
              size="sm"
              onClick={() => setConfirmArchiveOpen(true)}
              className="rounded-xl font-semibold shrink-0 cursor-pointer"
            >
              {project.archived_at
                ? t('projects.actions.unarchive')
                : t('projects.actions.archive')}
            </Button>
          </div>
        </div>
      )}

      <ConfirmDialog
        isOpen={confirmArchiveOpen}
        onCancel={() => setConfirmArchiveOpen(false)}
        onConfirm={() => void onArchiveOrRestore()}
        title={
          project.archived_at
            ? `Restore ${project.name}?`
            : t('projects.dialog.archiveTitle')
        }
        message={
          project.archived_at
            ? `Are you sure you want to restore ${project.name} to active status?`
            : t('projects.dialog.archiveMessage', { name: project.name })
        }
        confirmText={
          project.archived_at
            ? t('projects.actions.unarchive')
            : t('projects.dialog.archiveConfirm')
        }
        confirmVariant={project.archived_at ? 'primary' : 'danger'}
      />
    </>
  )
}
