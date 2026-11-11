import React from 'react'
import { ChevronLeft, ChevronRight, ChevronsLeft, ChevronsRight } from 'lucide-react'
import { Button } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'

export interface PaginationProps {
  page: number
  pageSize: number
  totalItems: number
  onPageChange: (page: number) => void
  onPageSizeChange?: (pageSize: number) => void
  pageSizeOptions?: number[]
  className?: string
  compact?: boolean
}

export const Pagination: React.FC<PaginationProps> = ({
  page,
  pageSize,
  totalItems,
  onPageChange,
  onPageSizeChange,
  pageSizeOptions = [10, 25, 50, 100],
  className = '',
  compact = false,
}) => {
  const { t } = useTranslation()

  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize))
  const start = totalItems === 0 ? 0 : (page - 1) * pageSize + 1
  const end = Math.min(totalItems, page * pageSize)

  const getPageNumbers = (): (number | 'ellipsis')[] => {
    if (totalPages <= 5) {
      return Array.from({ length: totalPages }, (_, i) => i + 1)
    }

    const pages: (number | 'ellipsis')[] = [1]

    if (page > 3) {
      pages.push('ellipsis')
    }

    const startPage = Math.max(2, page - 1)
    const endPage = Math.min(totalPages - 1, page + 1)

    for (let i = startPage; i <= endPage; i++) {
      pages.push(i)
    }

    if (page < totalPages - 2) {
      pages.push('ellipsis')
    }

    pages.push(totalPages)
    return pages
  }

  if ((totalItems === 0 || totalItems <= pageSize) && !compact) {
    return null
  }

  return (
    <div
      className={`flex flex-col sm:flex-row sm:items-center justify-between gap-3 text-xs text-zinc-600 dark:text-zinc-400 select-none ${className}`}
      role="navigation"
      aria-label="Pagination"
    >
      <div className="flex items-center gap-3">
        <span className="font-medium text-zinc-600 dark:text-zinc-400">
          {t('common.pagination.showing', { start, end, total: totalItems })}
        </span>

        {onPageSizeChange && (
          <div className="flex items-center gap-1.5 pl-2 border-l border-zinc-200 dark:border-zinc-700/80">
            <span className="text-[11px] text-zinc-500 dark:text-zinc-400">
              {t('common.pagination.perPage')}:
            </span>
            <select
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
              aria-label={t('common.pagination.perPage')}
              className="bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700 rounded-lg px-2 py-1 text-xs text-zinc-800 dark:text-zinc-200 focus:outline-none focus:ring-1 focus:ring-teal-500 cursor-pointer"
            >
              {pageSizeOptions.map((opt) => (
                <option key={opt} value={opt}>
                  {opt}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 self-end sm:self-center">
        {totalPages > 5 && (
          <Button
            variant="ghost"
            size="sm"
            isIconOnly
            onClick={() => onPageChange(1)}
            isDisabled={page <= 1}
            aria-label="First page"
            className="h-8 w-8 p-0 rounded-lg text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronsLeft size={14} />
          </Button>
        )}

        <Button
          variant="ghost"
          size="sm"
          onClick={() => onPageChange(Math.max(1, page - 1))}
          isDisabled={page <= 1}
          aria-label={t('common.pagination.prev')}
          className="h-8 px-2.5 rounded-lg text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 flex items-center gap-1 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <ChevronLeft size={14} />
          <span className="hidden sm:inline">{t('common.pagination.prev')}</span>
        </Button>

        <div className="flex items-center gap-1">
          {getPageNumbers().map((p, idx) => {
            if (p === 'ellipsis') {
              return (
                <span
                  key={`ellipsis-${idx}`}
                  className="w-7 text-center text-zinc-400 dark:text-zinc-600 select-none text-xs"
                >
                  …
                </span>
              )
            }

            const isActive = p === page
            return (
              <button
                key={p}
                type="button"
                onClick={() => onPageChange(p)}
                aria-current={isActive ? 'page' : undefined}
                className={`h-8 min-w-[32px] px-2 rounded-lg text-xs font-medium transition cursor-pointer flex items-center justify-center ${
                  isActive
                    ? 'bg-teal-500/10 dark:bg-teal-500/15 text-teal-700 dark:text-teal-300 font-semibold border border-teal-500/30 dark:border-teal-500/35 shadow-2xs'
                    : 'text-zinc-600 dark:text-zinc-400 hover:bg-zinc-100 dark:hover:bg-zinc-800 hover:text-zinc-900 dark:hover:text-zinc-100 border border-transparent'
                }`}
              >
                {p}
              </button>
            )
          })}
        </div>

        <Button
          variant="ghost"
          size="sm"
          onClick={() => onPageChange(Math.min(totalPages, page + 1))}
          isDisabled={page >= totalPages}
          aria-label={t('common.pagination.next')}
          className="h-8 px-2.5 rounded-lg text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 flex items-center gap-1 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
        >
          <span className="hidden sm:inline">{t('common.pagination.next')}</span>
          <ChevronRight size={14} />
        </Button>

        {totalPages > 5 && (
          <Button
            variant="ghost"
            size="sm"
            isIconOnly
            onClick={() => onPageChange(totalPages)}
            isDisabled={page >= totalPages}
            aria-label="Last page"
            className="h-8 w-8 p-0 rounded-lg text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 cursor-pointer disabled:opacity-30 disabled:cursor-not-allowed"
          >
            <ChevronsRight size={14} />
          </Button>
        )}
      </div>
    </div>
  )
}

