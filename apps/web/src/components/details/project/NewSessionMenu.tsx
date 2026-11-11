import React, { useEffect, useRef, useState } from 'react'
import { Bot, Box, CheckSquare, ChevronDown, ChevronRight, Code2, Lightbulb, PanelBottom, Plus, SlidersHorizontal, Sprout, Square } from 'lucide-react'
import { Button } from '@heroui/react'
import { useData } from '../../../context/DataContext'
import { AgentProviderIcon } from '../../common/ProviderIcon'
import type { Agent } from '../../../types'

export const LAUNCH_IN_DOCK_STORAGE_KEY = 'gitseed_launch_session_in_dock'

export function getLaunchSessionInDock(): boolean {
  try {
    return window.localStorage.getItem(LAUNCH_IN_DOCK_STORAGE_KEY) === '1'
  } catch {
    return false
  }
}

export function setLaunchSessionInDock(value: boolean): void {
  try {
    window.localStorage.setItem(LAUNCH_IN_DOCK_STORAGE_KEY, value ? '1' : '0')
  } catch {
    /* ignore */
  }
}

export type NewSessionKind = 'terminal' | 'editor' | 'chat' | 'brainstorm'

export type NewSessionChoice =
  | { kind: 'terminal' }
  | { kind: 'editor' }
  | { kind: 'chat'; agentId: string | null }
  | { kind: 'brainstorm' }
  | { kind: 'options' }

export interface NewSessionMenuProps {
  onChoose: (choice: NewSessionChoice) => void
  /** Optional handler to show "New Seed" as the first item in the menu (consolidating into "+ New") */
  onNewSeed?: () => void
  /** Custom button label override; defaults to "New" when `onNewSeed` is provided, otherwise "New session" */
  buttonLabel?: string
  /** Agents offered under "Chat with another agent…" */
  agents?: Agent[]
  /** The project's default agent (shown as "Chat with @handle") */
  defaultAgent?: Agent | null
  /** Show the Brainstorm entry (project header) */
  includeBrainstorm?: boolean
  /** Show "Options…" for sessions that need a branch, image, model, root… */
  includeOptions?: boolean
  /** `primary` = solid teal; `secondary` = outlined (next to Create Seed) */
  tone?: 'primary' | 'secondary'
  /** Match neighbouring header buttons: `md` on page headers, `sm` on compact headers */
  size?: 'sm' | 'md'
  disabled?: boolean
  busy?: boolean
  align?: 'left' | 'right'
  className?: string
}

export const NewSessionMenu: React.FC<NewSessionMenuProps> = ({
  onChoose,
  onNewSeed,
  buttonLabel,
  agents = [],
  defaultAgent = null,
  includeBrainstorm = false,
  includeOptions = true,
  tone = 'secondary',
  size = 'md',
  disabled = false,
  busy = false,
  align = 'right',
  className = '',
}) => {
  const { backends } = useData()
  const [open, setOpen] = useState(false)
  const [showAgents, setShowAgents] = useState(false)
  const [launchInDock, setLaunchInDockState] = useState<boolean>(() => getLaunchSessionInDock())
  const rootRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    if (!open) {
      setShowAgents(false)
      return
    }
    setLaunchInDockState(getLaunchSessionInDock())
    const onDocClick = (e: MouseEvent) => {
      if (rootRef.current && !rootRef.current.contains(e.target as Node)) setOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false)
    }
    document.addEventListener('mousedown', onDocClick)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDocClick)
      document.removeEventListener('keydown', onKey)
    }
  }, [open])

  const choose = (choice: NewSessionChoice) => {
    setOpen(false)
    onChoose(choice)
  }

  const resolvedLabel = buttonLabel || (onNewSeed ? 'New' : 'New session')
  const otherAgents = agents.filter((a) => a.id !== defaultAgent?.id)
  const baseItemClass =
    'w-full flex items-center gap-2.5 px-2.5 py-2 rounded-lg text-left text-xs hover:bg-zinc-100 dark:hover:bg-zinc-800/80 cursor-pointer transition-colors'
  const itemClass = `${baseItemClass} font-semibold text-zinc-800 dark:text-zinc-100`

  return (
    <div ref={rootRef} className={`relative inline-flex ${className}`}>
      <Button
        type="button"
        variant={tone === 'primary' ? 'primary' : 'secondary'}
        size={size}
        isDisabled={disabled || busy}
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        className={`flex items-center gap-2 font-semibold shrink-0 rounded-xl cursor-pointer ${
          tone === 'primary'
            ? 'shadow-lg shadow-teal-600/20 dark:shadow-teal-500/20'
            : 'border border-zinc-200 dark:border-zinc-700/80'
        }`}
      >
        <Plus size={size === 'md' ? 16 : 15} />
        <span>{busy ? 'Starting…' : resolvedLabel}</span>
        <ChevronDown size={14} className={`transition ${open ? 'rotate-180' : ''}`} />
      </Button>

      {open && (
        <div
          role="menu"
          className={`absolute top-full mt-1.5 z-40 w-64 p-1 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 shadow-lg ${
            align === 'right' ? 'right-0' : 'left-0'
          }`}
        >
          {/* Section 0: Governed Seed Pipeline (when consolidated into "+ New") */}
          {onNewSeed && (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => {
                  setOpen(false)
                  onNewSeed()
                }}
                className={`${itemClass} justify-between`}
              >
                <span className="flex items-center gap-2.5">
                  <Sprout size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                  <span>New Seed</span>
                </span>
                <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-teal-500/10 text-teal-600 dark:text-teal-400">
                  Pipeline
                </span>
              </button>
              <div className="my-1 border-t border-zinc-100 dark:border-zinc-800" />
            </>
          )}

          {/* Section 1: Agent Workflows */}
          <button
            type="button"
            role="menuitem"
            onClick={() => choose({ kind: 'chat', agentId: defaultAgent?.id ?? null })}
            className={itemClass}
          >
            {defaultAgent ? (
              <AgentProviderIcon agent={defaultAgent} backends={backends} size={14} />
            ) : (
              <Bot size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
            )}
            <span className="truncate">
              Code with <span className="font-mono text-[11px]">@{defaultAgent?.handle || 'coder'}</span>
            </span>
          </button>
          {otherAgents.length > 0 && (
            <>
              <button
                type="button"
                role="menuitem"
                onClick={() => setShowAgents((v) => !v)}
                className={`${itemClass} justify-between`}
              >
                <span className="flex items-center gap-2.5">
                  <Bot size={14} className="text-zinc-400 shrink-0" />
                  <span>Code with another agent…</span>
                </span>
                <ChevronRight size={14} className={`text-zinc-400 shrink-0 transition ${showAgents ? 'rotate-90' : ''}`} />
              </button>
              {showAgents && (
                <div className="max-h-56 overflow-y-auto ml-5 my-0.5 border-l border-zinc-200 dark:border-zinc-800 pl-1.5 flex flex-col gap-0.5">
                  {otherAgents.map((a) => (
                    <button
                      key={a.id}
                      type="button"
                      role="menuitem"
                      onClick={() => choose({ kind: 'chat', agentId: a.id })}
                      className={itemClass}
                    >
                      <AgentProviderIcon agent={a} backends={backends} size={14} />
                      <span className="font-mono text-[11px] truncate">@{a.handle}</span>
                    </button>
                  ))}
                </div>
              )}
            </>
          )}
          {includeBrainstorm && (
            <button type="button" role="menuitem" onClick={() => choose({ kind: 'brainstorm' })} className={itemClass}>
              <Lightbulb size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
              <span>Brainstorm</span>
            </button>
          )}

          <div className="my-1 border-t border-zinc-100 dark:border-zinc-800" />

          {/* Section 2: Manual Shells & Environments */}
          <button
            type="button"
            role="menuitem"
            onClick={() => choose({ kind: 'terminal' })}
            className={`${itemClass} justify-between`}
          >
            <span className="flex items-center gap-2.5">
              <Box size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
              <span>Sandbox Terminal</span>
            </span>
            <span className="text-[10px] font-medium px-1.5 py-0.5 rounded bg-zinc-100 dark:bg-zinc-800 text-zinc-500 dark:text-zinc-400">
              Container
            </span>
          </button>
          <button type="button" role="menuitem" onClick={() => choose({ kind: 'editor' })} className={itemClass}>
            <Code2 size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
            <span>Code editor</span>
          </button>

          {includeOptions && (
            <>
              <div className="my-1 border-t border-zinc-100 dark:border-zinc-800" />
              <button
                type="button"
                role="menuitem"
                onClick={() => choose({ kind: 'options' })}
                className={`${baseItemClass} font-medium text-zinc-500 dark:text-zinc-400`}
              >
                <SlidersHorizontal size={14} className="shrink-0" />
                <span>Session options…</span>
              </button>
              <button
                type="button"
                role="menuitemcheckbox"
                aria-checked={launchInDock}
                onClick={() => {
                  const next = !launchInDock
                  setLaunchInDockState(next)
                  setLaunchSessionInDock(next)
                }}
                className={`${baseItemClass} justify-between font-medium text-zinc-500 dark:text-zinc-400`}
              >
                <span className="flex items-center gap-2.5">
                  {launchInDock ? (
                    <CheckSquare size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                  ) : (
                    <Square size={14} className="text-zinc-400 shrink-0" />
                  )}
                  <span>Open in bottom dock</span>
                </span>
                <PanelBottom size={12} className="text-zinc-400 shrink-0" />
              </button>
            </>
          )}
        </div>
      )}
    </div>
  )
}
