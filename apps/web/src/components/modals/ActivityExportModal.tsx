import React, { useMemo, useState } from 'react'
import { Calendar, CheckCircle2, Download, FileJson, FileSpreadsheet, X } from 'lucide-react'
import { Button } from '@heroui/react'
import type { ActivityCategory, ParsedActivity } from '../../utils/activity'

export type ExportFormat = 'csv' | 'json'
export type DateRangePreset = '24h' | '7d' | '30d' | 'all'

interface ActivityExportModalProps {
  isOpen: boolean
  onClose: () => void
  activities: ParsedActivity[]
  activeCategory: 'all' | ActivityCategory
  activeProjectName: string | null
}

function isWithinDateRange(occurredAtIso: string, range: DateRangePreset): boolean {
  if (range === 'all') return true
  const timestamp = Date.parse(occurredAtIso)
  if (Number.isNaN(timestamp)) return true
  const diffMs = Math.max(0, Date.now() - timestamp)
  const diffHours = diffMs / 3_600_000
  if (range === '24h') return diffHours <= 24
  if (range === '7d') return diffHours <= 24 * 7
  if (range === '30d') return diffHours <= 24 * 30
  return true
}

export const ActivityExportModal: React.FC<ActivityExportModalProps> = ({
  isOpen,
  onClose,
  activities,
  activeCategory,
  activeProjectName,
}) => {
  const [format, setFormat] = useState<ExportFormat>('csv')
  const [dateRange, setDateRange] = useState<DateRangePreset>('30d')
  const [exportedSuccess, setExportedSuccess] = useState(false)

  const filteredForExport = useMemo(() => {
    return activities.filter((item) => isWithinDateRange(item.occurred_at, dateRange))
  }, [activities, dateRange])

  if (!isOpen) return null

  const handleDownload = () => {
    const dateStamp = new Date().toISOString().slice(0, 10)
    const projectSlug = activeProjectName
      ? activeProjectName.toLowerCase().replace(/[^a-z0-9]+/g, '-')
      : 'all-projects'
    const filename = `gitseed-activity-${projectSlug}-${dateRange}-${dateStamp}.${format}`

    let content = ''
    let mimeType = ''

    if (format === 'json') {
      const records = filteredForExport.map((item) => ({
        id: item.cursor_seq,
        occurred_at: item.occurred_at,
        day_group: item.dayGroup,
        time: item.timeOfDay,
        category: item.category,
        event_type: item.event_type,
        actor_type: item.actor_type,
        actor_id: item.actor_id,
        project_id: item.projectId,
        project_name: item.projectName,
        seed_id: item.seedId,
        seed_title: item.seedTitle,
        summary: item.fullText,
        data: item.payload,
      }))
      content = JSON.stringify(records, null, 2)
      mimeType = 'application/json;charset=utf-8;'
    } else {
      const headers = [
        'id',
        'occurred_at',
        'category',
        'event_type',
        'actor_type',
        'project',
        'seed',
        'summary',
      ]
      const escapeCsv = (val: string | number | null | undefined) => {
        const str = val == null ? '' : String(val)
        if (str.includes(',') || str.includes('"') || str.includes('\n')) {
          return `"${str.replace(/"/g, '""')}"`
        }
        return str
      }
      const rows = filteredForExport.map((item) =>
        [
          escapeCsv(item.cursor_seq),
          escapeCsv(item.occurred_at),
          escapeCsv(item.category),
          escapeCsv(item.event_type),
          escapeCsv(item.actor_type),
          escapeCsv(item.projectName || 'Global'),
          escapeCsv(item.seedTitle || ''),
          escapeCsv(item.fullText),
        ].join(',')
      )
      content = [headers.join(','), ...rows].join('\n')
      mimeType = 'text/csv;charset=utf-8;'
    }

    const blob = new Blob([content], { type: mimeType })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.setAttribute('download', filename)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(url)

    setExportedSuccess(true)
    setTimeout(() => {
      setExportedSuccess(false)
      onClose()
    }, 900)
  }

  const rangeOptions: Array<{ key: DateRangePreset; label: string }> = [
    { key: '24h', label: 'Last 24 hours' },
    { key: '7d', label: 'Last 7 days' },
    { key: '30d', label: 'Last 30 days' },
    { key: 'all', label: 'All recorded' },
  ]

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-xs animate-in fade-in duration-150"
      onClick={onClose}
    >
      <div
        className="w-full max-w-md rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-zinc-200 dark:border-zinc-700/80">
          <div className="flex items-center gap-2.5">
            <div className="w-8 h-8 rounded-lg bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center">
              <Download size={16} />
            </div>
            <div>
              <h3 className="font-heading font-bold text-sm text-zinc-900 dark:text-zinc-100">
                Export Activity Audit Log
              </h3>
              <p className="text-xs text-zinc-500 dark:text-zinc-400">
                Download append-only fleet activity records
              </p>
            </div>
          </div>
          <button
            type="button"
            onClick={onClose}
            className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
          >
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="p-5 flex flex-col gap-4">
          {/* Format Picker */}
          <div>
            <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-2">
              Export Format
            </label>
            <div className="grid grid-cols-2 gap-2.5">
              <button
                type="button"
                onClick={() => setFormat('csv')}
                className={`flex items-center gap-2.5 p-3 rounded-xl border text-left transition-all cursor-pointer ${
                  format === 'csv'
                    ? 'border-teal-500 bg-teal-500/5 dark:bg-teal-500/10 text-zinc-900 dark:text-zinc-100 ring-1 ring-teal-500/30'
                    : 'border-zinc-200 dark:border-zinc-700/80 hover:border-zinc-300 dark:hover:border-zinc-600 text-zinc-600 dark:text-zinc-400'
                }`}
              >
                <FileSpreadsheet
                  size={18}
                  className={format === 'csv' ? 'text-teal-500' : 'text-zinc-400'}
                />
                <div>
                  <div className="text-xs font-bold">CSV Spreadsheet</div>
                  <div className="text-xs text-zinc-400 dark:text-zinc-500">
                    Excel, Sheets, BI tools
                  </div>
                </div>
              </button>

              <button
                type="button"
                onClick={() => setFormat('json')}
                className={`flex items-center gap-2.5 p-3 rounded-xl border text-left transition-all cursor-pointer ${
                  format === 'json'
                    ? 'border-teal-500 bg-teal-500/5 dark:bg-teal-500/10 text-zinc-900 dark:text-zinc-100 ring-1 ring-teal-500/30'
                    : 'border-zinc-200 dark:border-zinc-700/80 hover:border-zinc-300 dark:hover:border-zinc-600 text-zinc-600 dark:text-zinc-400'
                }`}
              >
                <FileJson
                  size={18}
                  className={format === 'json' ? 'text-teal-500' : 'text-zinc-400'}
                />
                <div>
                  <div className="text-xs font-bold">JSON Audit Trail</div>
                  <div className="text-xs text-zinc-400 dark:text-zinc-500">
                    Full event payloads
                  </div>
                </div>
              </button>
            </div>
          </div>

          {/* Date Range Picker */}
          <div>
            <label className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-2">
              <Calendar size={13} className="text-zinc-400" />
              <span>Date Range</span>
            </label>
            <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5">
              {rangeOptions.map((opt) => (
                <button
                  key={opt.key}
                  type="button"
                  onClick={() => setDateRange(opt.key)}
                  className={`px-2.5 py-2 rounded-lg text-xs font-semibold border transition-colors cursor-pointer ${
                    dateRange === opt.key
                      ? 'bg-zinc-900 text-white border-zinc-900 dark:bg-zinc-100 dark:text-zinc-900 dark:border-zinc-100'
                      : 'bg-zinc-50 dark:bg-zinc-800/60 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700/80 hover:text-zinc-900 dark:hover:text-zinc-200'
                  }`}
                >
                  {opt.label}
                </button>
              ))}
            </div>
          </div>

          {/* Scope Summary */}
          <div className="p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between text-xs">
            <div className="flex flex-col gap-0.5">
              <span className="text-zinc-500 dark:text-zinc-400">
                Scope:{' '}
                <strong className="text-zinc-800 dark:text-zinc-200">
                  {activeProjectName || 'All Projects'}
                </strong>{' '}
                · Category:{' '}
                <strong className="text-zinc-800 dark:text-zinc-200 capitalize">
                  {activeCategory}
                </strong>
              </span>
              <span className="text-xs text-zinc-400 dark:text-zinc-500">
                Includes actor, cursor sequence, and structured event metadata
              </span>
            </div>
            <span className="px-2 py-1 rounded-md bg-teal-500/10 text-teal-600 dark:text-teal-400 font-mono font-bold">
              {filteredForExport.length} events
            </span>
          </div>
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 px-5 py-3.5 bg-zinc-50/70 dark:bg-zinc-900/90 border-t border-zinc-200 dark:border-zinc-700/80">
          <Button
            size="sm"
            variant="ghost"
            onPress={onClose}
            className="text-xs font-semibold text-zinc-600 dark:text-zinc-400"
          >
            Cancel
          </Button>
          <Button
            size="sm"
            onPress={handleDownload}
            isDisabled={filteredForExport.length === 0}
            className="bg-teal-600 hover:bg-teal-500 text-white font-semibold text-xs px-4 rounded-lg inline-flex items-center gap-1.5 shadow-xs disabled:opacity-50"
          >
            {exportedSuccess ? (
              <>
                <CheckCircle2 size={14} />
                <span>Downloaded</span>
              </>
            ) : (
              <>
                <Download size={14} />
                <span>Download .{format.toUpperCase()}</span>
              </>
            )}
          </Button>
        </div>
      </div>
    </div>
  )
}

