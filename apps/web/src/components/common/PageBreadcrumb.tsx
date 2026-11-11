import React from 'react'
import { Tooltip } from './Tooltip'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import type { Project } from '../../types'

export interface BreadcrumbItem {
  label: string
  tooltip?: string
  mono?: boolean
  onClick?: () => void
}

export interface PageBreadcrumbProps {
  /** Current page or entity label (final crumb) */
  currentLabel: string
  /** Optional explicit project override (defaults to selectedProjectObj unless hideProject is true) */
  project?: Project | null
  /** Whether to hide the active project crumb (e.g. on global admin views) */
  hideProject?: boolean
  /** Optional intermediate breadcrumb items between project and currentLabel */
  items?: BreadcrumbItem[]
  className?: string
}

export const PageBreadcrumb: React.FC<PageBreadcrumbProps> = ({
  currentLabel,
  project,
  hideProject = false,
  items = [],
  className = '',
}) => {
  const { t } = useTranslation()
  const { selectedProjectObj, setActiveView, navigateToDetail } = useData()

  const effectiveProject = hideProject
    ? null
    : project !== undefined
      ? project
      : selectedProjectObj

  return (
    <div
      className={`flex items-center gap-1.5 text-xs text-zinc-400 dark:text-zinc-500 font-medium flex-wrap ${className}`}
    >
      <Tooltip content={t('common.appName')} placement="bottom-start">
        <button
          type="button"
          onClick={() => setActiveView(VIEWS.DASHBOARD)}
          className="text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200 transition-colors cursor-pointer font-semibold"
        >
          {t('common.appName')}
        </button>
      </Tooltip>

      {effectiveProject && (
        <>
          <span className="text-zinc-300 dark:text-zinc-600 select-none">›</span>
          <Tooltip
            content={effectiveProject.name}
            placement="bottom-start"
          >
            <button
              type="button"
              onClick={() => navigateToDetail(VIEWS.PROJECTS, effectiveProject.slug || effectiveProject.id)}
              className="font-semibold text-zinc-700 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100 hover:underline cursor-pointer truncate max-w-[200px]"
            >
              {effectiveProject.name}
            </button>
          </Tooltip>
        </>
      )}

      {items.map((item, idx) => (
        <React.Fragment key={`${item.label}-${idx}`}>
          <span className="text-zinc-300 dark:text-zinc-600 select-none">›</span>
          {item.onClick ? (
            <Tooltip content={item.tooltip || item.label} placement="bottom-start">
              <button
                type="button"
                onClick={item.onClick}
                className={`${
                  item.mono ? 'font-mono font-semibold' : 'font-medium'
                } text-zinc-600 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100 hover:underline transition-colors cursor-pointer truncate max-w-[180px]`}
              >
                {item.label}
              </button>
            </Tooltip>
          ) : (
            <span
              className={`${
                item.mono ? 'font-mono font-semibold' : 'font-medium'
              } text-zinc-500 dark:text-zinc-400 truncate max-w-[180px]`}
            >
              {item.label}
            </span>
          )}
        </React.Fragment>
      ))}

      <span className="text-zinc-300 dark:text-zinc-600 select-none">›</span>
      <Tooltip content={currentLabel} placement="bottom-start">
        <span className="text-zinc-500 dark:text-zinc-400 cursor-default truncate max-w-[220px]">
          {currentLabel}
        </span>
      </Tooltip>
    </div>
  )
}

