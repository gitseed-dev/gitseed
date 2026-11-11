import React, { useState } from 'react'
import {
  AlertTriangle,
  ArrowRight,
  Check,
  ChevronDown,
  Copy,
  DollarSign,
  Play,
  Shield,
  XCircle,
} from 'lucide-react'
import { VIEWS } from '../../constants'
import { Highlight, themes } from 'prism-react-renderer'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTheme } from '../../context/ThemeContext'
import type { ActivityIconType, ParsedActivity } from '../../utils/activity'

interface ActivityRowProps {
  item: ParsedActivity
  onNavigateSeed?: (seedId: string, subTab?: string, runId?: string) => void
  showProject?: boolean
  timeFormat?: 'clock' | 'relative'
}

export const ActivityRow: React.FC<ActivityRowProps> = ({
  item,
  onNavigateSeed,
  showProject = true,
  timeFormat = 'clock',
}) => {
  const { user } = useAuth()
  const { navigateToDetail, openProfileDrawer } = useData()
  const { theme } = useTheme()
  const [expanded, setExpanded] = useState(false)
  const [copied, setCopied] = useState(false)

  const isAdmin = user?.org_role === 'admin'
  const canNavigateActor = Boolean(
    item.actor_id &&
      ((item.actor_type === 'user' && (isAdmin || item.actor_id === user?.id)) ||
        (item.actor_type === 'agent' && isAdmin))
  )

  const handleActorClick = (e: React.MouseEvent | React.KeyboardEvent) => {
    e.stopPropagation()
    if (!item.actor_id || !canNavigateActor) return
    if (item.actor_type === 'user') {
      if (isAdmin) {
        navigateToDetail(VIEWS.USERS, item.actor_id)
      } else if (item.actor_id === user?.id) {
        openProfileDrawer()
      }
    } else if (item.actor_type === 'agent' && isAdmin) {
      navigateToDetail(VIEWS.AGENTS, item.actor_id)
    }
  }

  const renderActorNode = () => {
    if (!canNavigateActor) {
      return <span className="font-medium text-zinc-900 dark:text-zinc-100">{item.actor}</span>
    }
    return (
      <span
        role="button"
        tabIndex={0}
        onClick={handleActorClick}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            handleActorClick(e)
          }
        }}
        className="inline font-medium text-zinc-900 dark:text-zinc-100 hover:text-teal-600 dark:hover:text-teal-400 hover:underline transition-colors cursor-pointer"
      >
        {item.actor}
      </span>
    )
  }

  const renderPrefixWithActor = () => {
    if (item.actor && item.prefix.startsWith(item.actor)) {
      return (
        <>
          {renderActorNode()}
          <span>{item.prefix.slice(item.actor.length)}</span>
        </>
      )
    }
    const seedScopedSuffix = ` · ${item.actor}`
    if (item.actor && item.prefix.endsWith(seedScopedSuffix)) {
      return (
        <>
          <span>{item.prefix.slice(0, item.prefix.length - item.actor.length)}</span>
          {renderActorNode()}
        </>
      )
    }
    return <span>{item.prefix}</span>
  }

  const renderIcon = (iconType: ActivityIconType) => {
    switch (iconType) {
      case 'approval':
        return (
          <div className="w-5 h-5 rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400 flex items-center justify-center shrink-0">
            <Check size={12} className="stroke-[2.5]" />
          </div>
        )
      case 'rejection':
        return (
          <div className="w-5 h-5 rounded-full bg-rose-500/10 text-rose-600 dark:text-rose-400 flex items-center justify-center shrink-0">
            <XCircle size={12} className="stroke-[2.2]" />
          </div>
        )
      case 'stage':
        return (
          <div className="w-5 h-5 rounded-full bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center shrink-0">
            <ArrowRight size={12} className="stroke-[2.5]" />
          </div>
        )
      case 'agent':
        return (
          <div className="w-5 h-5 rounded-full bg-sky-500/10 text-sky-600 dark:text-sky-400 flex items-center justify-center shrink-0">
            <Play size={10} className="fill-current ml-0.5" />
          </div>
        )
      case 'agent_failed':
        return (
          <div className="w-5 h-5 rounded-full bg-rose-500/10 text-rose-600 dark:text-rose-400 flex items-center justify-center shrink-0">
            <AlertTriangle size={12} className="stroke-[2.2]" />
          </div>
        )
      case 'budget':
        return (
          <div className="w-5 h-5 rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400 flex items-center justify-center shrink-0">
            <DollarSign size={12} className="stroke-[2.5]" />
          </div>
        )
      case 'admin':
      default:
        return (
          <div className="w-5 h-5 rounded-full bg-zinc-500/10 text-zinc-600 dark:text-zinc-400 flex items-center justify-center shrink-0">
            <Shield size={12} className="stroke-[2.5]" />
          </div>
        )
    }
  }

  const handleCopyPayload = (e: React.MouseEvent) => {
    e.stopPropagation()
    const exportObj = {
      id: item.cursor_seq,
      type: item.event_type,
      occurred_at: item.occurred_at,
      actor_type: item.actor_type,
      actor_id: item.actor_id,
      project_id: item.projectId,
      seed_id: item.seedId,
      data: item.payload,
    }
    navigator.clipboard?.writeText(JSON.stringify(exportObj, null, 2))
    setCopied(true)
    setTimeout(() => setCopied(false), 1800)
  }

  return (
    <div
      className={`py-2.5 px-2.5 sm:px-3 flex flex-col hover:bg-zinc-100/70 dark:hover:bg-zinc-800/40 rounded-xl transition group ${
        expanded ? 'bg-zinc-50 dark:bg-zinc-900/60' : ''
      }`}
    >
      <div className="flex items-center justify-between gap-3 text-sm">
        <div className="flex items-center gap-2.5 min-w-0 flex-1">
          {renderIcon(item.iconType)}

          <p
            title={item.fullText}
            className="min-w-0 flex-1 text-zinc-700 dark:text-zinc-300 truncate leading-relaxed"
          >
            {renderPrefixWithActor()}
            {item.seedTitle && item.seedId && onNavigateSeed ? (
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation()
                  onNavigateSeed(item.seedId!)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    e.stopPropagation()
                    onNavigateSeed(item.seedId!)
                  }
                }}
                className="inline font-semibold text-zinc-900 dark:text-zinc-100 hover:text-teal-600 dark:hover:text-teal-400 hover:underline transition-colors cursor-pointer"
              >
                {item.seedTitle}
              </span>
            ) : item.seedTitle ? (
              <span className="font-semibold text-zinc-900 dark:text-zinc-100">
                {item.seedTitle}
              </span>
            ) : null}
            <span>{item.suffix}</span>

            {item.runId && item.seedId && onNavigateSeed && (
              <span
                role="button"
                tabIndex={0}
                onClick={(e) => {
                  e.stopPropagation()
                  onNavigateSeed(item.seedId!, 'runs', item.runId)
                }}
                onKeyDown={(e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault()
                    e.stopPropagation()
                    onNavigateSeed(item.seedId!, 'runs', item.runId)
                  }
                }}
                className="inline ml-1.5 font-mono text-xs font-medium text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                · run log →
              </span>
            )}

            {showProject && item.projectName && (
              <span className="ml-1.5 text-zinc-400 dark:text-zinc-500 font-normal">
                · {item.projectName}
              </span>
            )}
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0 ml-2">
          <span className="font-mono text-xs text-zinc-400 dark:text-zinc-500 text-right tabular-nums min-w-[62px]">
            {timeFormat === 'clock' ? item.timeOfDay || item.timeAgo : item.timeAgo}
          </span>
          <button
            type="button"
            onClick={(e) => {
              e.stopPropagation()
              setExpanded((prev) => !prev)
            }}
            className="w-5 h-5 flex items-center justify-center rounded-md text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-700/60 transition-colors cursor-pointer"
            title={expanded ? 'Collapse payload' : 'Inspect payload'}
            aria-label="Toggle event payload"
          >
            <ChevronDown
              size={13}
              className={`transition-transform duration-150 ${expanded ? 'rotate-180 text-teal-500' : ''}`}
            />
          </button>
        </div>
      </div>

      {expanded && (
        <div
          onClick={(e) => e.stopPropagation()}
          className="mt-2.5 pt-2.5 border-t border-zinc-200/60 dark:border-zinc-800 text-xs flex flex-col gap-1.5 pl-7 pr-1 cursor-default"
        >
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2 flex-wrap font-mono">
              <span className="font-semibold text-zinc-500 dark:text-zinc-400 bg-zinc-200/60 dark:bg-zinc-800 px-1.5 py-0.5 rounded">
                #{item.cursor_seq}
              </span>
              <span className="px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-600 dark:text-teal-400 font-semibold">
                {item.event_type}
              </span>
              <span className="text-zinc-400 dark:text-zinc-500">
                actor:{' '}
                {canNavigateActor ? (
                  <button
                    type="button"
                    onClick={handleActorClick}
                    className="text-zinc-600 dark:text-zinc-300 hover:text-teal-600 dark:hover:text-teal-400 hover:underline cursor-pointer font-mono"
                  >
                    {item.actor} ({item.actor_type})
                  </button>
                ) : (
                  <span>
                    {item.actor} ({item.actor_type})
                  </span>
                )}
              </span>
            </div>
            <button
              type="button"
              onClick={handleCopyPayload}
              className="inline-flex items-center gap-1 px-2 py-0.5 rounded text-xs font-medium text-zinc-500 hover:text-zinc-800 dark:text-zinc-400 dark:hover:text-zinc-200 hover:bg-zinc-200/60 dark:hover:bg-zinc-800 transition-colors cursor-pointer"
            >
              {copied ? (
                <>
                  <Check size={12} className="text-emerald-500" />
                  <span className="text-emerald-600 dark:text-emerald-400">Copied</span>
                </>
              ) : (
                <>
                  <Copy size={12} />
                  <span>Copy JSON</span>
                </>
              )}
            </button>
          </div>
          <Highlight
            code={JSON.stringify(item.payload, null, 2)}
            language="json"
            theme={theme === 'dark' ? themes.oneDark : themes.oneLight}
          >
            {({ tokens, getLineProps, getTokenProps }) => (
              <pre className="p-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-950/80 border border-zinc-200/80 dark:border-zinc-800/70 font-mono text-xs text-zinc-800 dark:text-zinc-300 overflow-x-auto whitespace-pre-wrap break-all leading-relaxed">
                <code>
                  {tokens.map((line, lineIdx) => (
                    <div
                      key={lineIdx}
                      {...getLineProps({ line })}
                      style={undefined}
                    >
                      {line.map((token, tokenIdx) => (
                        <span key={tokenIdx} {...getTokenProps({ token })} />
                      ))}
                    </div>
                  ))}
                </code>
              </pre>
            )}
          </Highlight>
        </div>
      )}
    </div>
  )
}
