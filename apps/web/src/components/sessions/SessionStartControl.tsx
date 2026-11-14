import React, { useCallback, useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { Loader2, X } from 'lucide-react'
import { Button } from '@heroui/react'
import { api } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useSessionDock } from '../../context/SessionDockContext'
import { AgentProviderIcon } from '../common/ProviderIcon'
import {
  NewSessionMenu,
  getLaunchSessionInDock,
  setLaunchSessionInDock,
  type NewSessionChoice,
} from '../details/project/NewSessionMenu'
import type { Agent, AgentRuntime, Project } from '../../types'

const LAST_PROJECT_KEY = 'gitseed_last_session_project'

function clockLabel(): string {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
}

export function canStartSessions(user: { org_role?: string } | null, project: Project | null): boolean {
  if (!project || project.archived_at) return false
  return user?.org_role === 'admin' || project.my_role === 'maintainer' || project.my_role === 'developer'
}

const NON_CODING_STAGE_HANDLE_RE = /^(?:[a-z0-9_]+-)?(?:ideator|planner|reviewer|merger)$/i

/** Agents for interactive coding sessions: enabled execute/coder stage agents (and custom stage agents). */
export function useSessionAgents(projectId: string | null): { agents: Agent[]; defaultAgent: Agent | null } {
  const { agents, policies, projectPoliciesByProject } = useData()
  return useMemo(() => {
    const enabledAgents = agents.filter((a) => a.enabled && !a.archived_at && a.kind !== 'assistant')
    const effective =
      (projectId && projectPoliciesByProject[projectId]?.length
        ? projectPoliciesByProject[projectId]
        : policies) || []
    const implementAgentId = effective.find((p) => p.stage === 'execute')?.agent_id
    const executePolicyAgentIds = new Set<string>(
      [
        ...policies.filter((p) => p.stage === 'execute').map((p) => p.agent_id),
        ...Object.values(projectPoliciesByProject)
          .flat()
          .filter((p) => p.stage === 'execute')
          .map((p) => p.agent_id),
      ].filter((id): id is string => Boolean(id))
    )

    const codingAgents = enabledAgents.filter(
      (a) =>
        executePolicyAgentIds.has(a.id) ||
        a.handle === 'coder' ||
        a.handle.endsWith('-coder') ||
        !NON_CODING_STAGE_HANDLE_RE.test(a.handle)
    )
    const list = codingAgents.length > 0 ? codingAgents : enabledAgents

    const defaultAgent =
      list.find((a) => a.id === implementAgentId) ||
      list.find((a) => a.handle === 'coder') ||
      list.find((a) => a.handle.endsWith('-coder')) ||
      list[0] ||
      null
    return { agents: list, defaultAgent }
  }, [agents, policies, projectPoliciesByProject, projectId])
}

interface StartOptions {
  ref?: string
  model?: string
  image?: string
  setup?: string
  asRoot?: boolean
}

export interface SessionStartControlProps {
  /** Fixed project (project page). Otherwise uses the scope switcher, or asks once. */
  projectId?: string | null
  /** Optional handler to show "New Seed" as the first item in the menu (consolidating into "+ New") */
  onNewSeed?: () => void
  /** Custom button label override */
  buttonLabel?: string
  includeBrainstorm?: boolean
  onBrainstorm?: (projectId: string) => void
  tone?: 'primary' | 'secondary'
  size?: 'sm' | 'md'
  align?: 'left' | 'right'
  /** Render nothing (instead of a disabled button) when the user can't start sessions anywhere */
  hideWhenUnavailable?: boolean
}

/**
 * "+ New ▾" (when `onNewSeed` is provided) or "+ New session ▾", used on Overview, Sessions,
 * and the project page: New Seed (optional), Code with default/other agent, Terminal, Editor, and Options.
 */
export const SessionStartControl: React.FC<SessionStartControlProps> = ({
  projectId = null,
  onNewSeed,
  buttonLabel,
  includeBrainstorm = false,
  onBrainstorm,
  tone = 'primary',
  size = 'md',
  align = 'right',
  hideWhenUnavailable = false,
}) => {
  const { user } = useAuth()
  const { projects, projectReposByProject, selectedProject, navigateToDetail, openSearchModal } = useData()
  const { dock } = useSessionDock()
  const queryClient = useQueryClient()

  const startable = useMemo(() => projects.filter((p) => canStartSessions(user, p)), [projects, user])
  const fixedProjectId = projectId || selectedProject || null
  const { agents, defaultAgent } = useSessionAgents(fixedProjectId)

  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pending, setPending] = useState<NewSessionChoice | null>(null)
  const [dialog, setDialog] = useState<'project' | 'options' | null>(null)

  const start = useCallback(
    async (pid: string, choice: NewSessionChoice, opts: StartOptions = {}) => {
      if (choice.kind === 'brainstorm') {
        if (onBrainstorm) onBrainstorm(pid)
        else openSearchModal('brainstorm', pid)
        return
      }
      if (choice.kind === 'options') return
      setBusy(true)
      setError(null)
      try {
        localStorage.setItem(LAST_PROJECT_KEY, pid)
      } catch {
        /* ignore */
      }
      try {
        let kind: 'chat' | 'terminal'
        let id: string
        let title: string
        let agentHandle: string | null = null
        if (choice.kind === 'terminal' || choice.kind === 'editor') {
          const ref = opts.ref?.trim() || null
          const repos = projectReposByProject[pid] || []
          const branch =
            ref || (repos.find((r) => r.is_primary) || repos[0])?.default_branch || null
          const prefix = choice.kind === 'editor' ? 'Editor' : 'Terminal'
          title = branch ? `${prefix} · ${branch} · ${clockLabel()}` : `${prefix} · ${clockLabel()}`
          const proj = projects.find((p) => p.id === pid) || null
          const defaultRoot = user?.org_role === 'admin' || proj?.my_role === 'maintainer'
          const created = await api.projects.createSandbox(pid, {
            ref,
            image: opts.image?.trim() || '',
            setup_command: opts.setup?.trim() || null,
            kind: 'terminal',
            title,
            as_root: opts.asRoot ?? Boolean(defaultRoot),
          })
          kind = 'terminal'
          id = created.id
        } else {
          const agent = agents.find((a) => a.id === choice.agentId) || defaultAgent
          agentHandle = agent?.handle || 'agent'
          title = `Agent · @${agentHandle} · ${clockLabel()}`
          const created = await api.projects.createAgentSession(pid, {
            ref: opts.ref?.trim() || null,
            agent_id: agent?.id ?? null,
            runtime: (agent?.runtime ?? 'opencode') as AgentRuntime,
            model: opts.model?.trim() || null,
            title,
            is_saved: false,
          })
          kind = 'chat'
          id = created.id
        }
        await Promise.all([
          queryClient.invalidateQueries({ queryKey: ['sessions'] }),
          queryClient.invalidateQueries({ queryKey: queryKeys.projectSandboxes(pid) }),
          queryClient.invalidateQueries({ queryKey: queryKeys.projectAgentSessions(pid) }),
        ])
        setDialog(null)
        setPending(null)
        if (getLaunchSessionInDock()) {
          dock(
            {
              kind,
              id,
              projectId: pid,
              title,
              agentHandle,
              pane: choice.kind === 'editor' ? 'code' : 'terminal',
            },
            { expand: true }
          )
        } else {
          navigateToDetail(VIEWS.SESSIONS, kind, id)
        }
      } catch (err) {
        setError(err instanceof Error ? err.message : 'Could not start the session')
      } finally {
        setBusy(false)
      }
    },
    [agents, defaultAgent, dock, navigateToDetail, onBrainstorm, openSearchModal, projectReposByProject, projects, queryClient, user?.org_role]
  )

  const onChoose = (choice: NewSessionChoice) => {
    setError(null)
    if (choice.kind === 'options') {
      setPending({ kind: 'chat', agentId: defaultAgent?.id ?? null })
      setDialog('options')
      return
    }
    if (fixedProjectId) {
      void start(fixedProjectId, choice)
      return
    }
    setPending(choice)
    setDialog('project')
  }

  if (hideWhenUnavailable && startable.length === 0 && !onNewSeed) return null

  return (
    <>
      <NewSessionMenu
        tone={tone}
        size={size}
        align={align}
        busy={busy}
        disabled={startable.length === 0 && !onNewSeed}
        onNewSeed={onNewSeed}
        buttonLabel={buttonLabel}
        agents={agents}
        defaultAgent={defaultAgent}
        includeBrainstorm={includeBrainstorm}
        onChoose={onChoose}
      />
      {error && !dialog && (
        <span className="text-xs text-rose-600 dark:text-rose-400 max-w-xs truncate" title={error}>
          {error}
        </span>
      )}
      {dialog && pending && (
        <SessionStartDialog
          mode={dialog}
          projects={startable}
          fixedProjectId={fixedProjectId}
          initialChoice={pending}
          busy={busy}
          error={error}
          onCancel={() => {
            setDialog(null)
            setPending(null)
            setError(null)
          }}
          onStart={(pid, choice, opts) => void start(pid, choice, opts)}
        />
      )}
    </>
  )
}

const SessionStartDialog: React.FC<{
  mode: 'project' | 'options'
  projects: Project[]
  fixedProjectId: string | null
  initialChoice: NewSessionChoice
  busy: boolean
  error: string | null
  onCancel: () => void
  onStart: (projectId: string, choice: NewSessionChoice, opts: StartOptions) => void
}> = ({ mode, projects, fixedProjectId, initialChoice, busy, error, onCancel, onStart }) => {
  const { user } = useAuth()
  const { backends } = useData()
  const lastUsed = (() => {
    try {
      return localStorage.getItem(LAST_PROJECT_KEY)
    } catch {
      return null
    }
  })()
  const [pid, setPid] = useState<string>(
    fixedProjectId || (projects.some((p) => p.id === lastUsed) ? (lastUsed as string) : projects[0]?.id || '')
  )
  const { agents, defaultAgent } = useSessionAgents(pid || null)
  const [kind, setKind] = useState<'terminal' | 'editor' | 'chat'>(
    initialChoice.kind === 'chat'
      ? 'chat'
      : initialChoice.kind === 'editor'
        ? 'editor'
        : 'terminal'
  )
  const [agentId, setAgentId] = useState<string>(
    initialChoice.kind === 'chat' && initialChoice.agentId ? initialChoice.agentId : ''
  )
  const [ref, setRef] = useState('')
  const [model, setModel] = useState('')
  const [image, setImage] = useState('')
  const [setup, setSetup] = useState('')
  const [asRoot, setAsRoot] = useState(false)
  const [launchInDock, setLaunchInDock] = useState<boolean>(() => getLaunchSessionInDock())

  const project = projects.find((p) => p.id === pid) || null
  const canUseRoot = user?.org_role === 'admin' || project?.my_role === 'maintainer'

  const { data: runConfig } = useQuery({
    queryKey: ['sessionStartRunConfig', pid],
    queryFn: () => api.projects.getSandboxConfig(pid, {}),
    enabled: mode === 'options' && Boolean(pid),
    staleTime: 60_000,
  })

  const choice: NewSessionChoice =
    kind === 'terminal'
      ? { kind: 'terminal' }
      : kind === 'editor'
        ? { kind: 'editor' }
        : { kind: 'chat', agentId: agentId || defaultAgent?.id || null }

  const field =
    'px-2.5 py-1.5 rounded-lg border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-950 text-sm text-zinc-900 dark:text-zinc-100'

  return (
    <div className="fixed inset-0 z-[60] bg-black/40 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="w-full max-w-md rounded-2xl border border-zinc-200 dark:border-zinc-700 bg-white dark:bg-zinc-900 shadow-xl">
        <div className="px-5 py-3.5 border-b border-zinc-200 dark:border-zinc-800 flex items-center justify-between">
          <h3 className="text-sm font-bold text-zinc-900 dark:text-zinc-100">
            {mode === 'project' ? 'Start session in…' : 'New session'}
          </h3>
          <button type="button" onClick={onCancel} className="text-zinc-400 hover:text-zinc-700 cursor-pointer">
            <X size={15} />
          </button>
        </div>
        <form
          className="p-5 flex flex-col gap-3 text-xs"
          onSubmit={(e) => {
            e.preventDefault()
            if (pid) onStart(pid, mode === 'project' ? initialChoice : choice, mode === 'project' ? {} : { ref, model, image, setup, asRoot })
          }}
        >
          {!fixedProjectId && (
            <label className="flex flex-col gap-1">
              <span className="font-semibold text-zinc-600 dark:text-zinc-400">Project</span>
              <select value={pid} onChange={(e) => setPid(e.target.value)} className={field} autoFocus>
                {projects.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            </label>
          )}

          {mode === 'options' && (
            <>
              <div className="inline-flex self-start rounded-lg p-0.5 bg-zinc-100 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700">
                {(['chat', 'terminal', 'editor'] as const).map((k) => (
                  <button
                    key={k}
                    type="button"
                    onClick={() => setKind(k)}
                    className={`px-3 py-1 rounded-md font-semibold cursor-pointer ${
                      kind === k ? 'bg-white dark:bg-zinc-900 text-teal-700 dark:text-teal-300 shadow-2xs' : 'text-zinc-500'
                    }`}
                  >
                    {k === 'chat' ? 'Coding agent' : k === 'editor' ? 'Code editor' : 'Sandbox terminal'}
                  </button>
                ))}
              </div>
              <label className="flex flex-col gap-1">
                <span className="font-semibold text-zinc-600 dark:text-zinc-400">Branch</span>
                <input value={ref} onChange={(e) => setRef(e.target.value)} placeholder="Default branch" className={`${field} font-mono`} />
              </label>
              {kind === 'chat' ? (
                <>
                  <label className="flex flex-col gap-1">
                    <span className="font-semibold text-zinc-600 dark:text-zinc-400">Agent</span>
                    <div className="relative flex items-center">
                      {(() => {
                        const selected =
                          agents.find((a) => a.id === (agentId || defaultAgent?.id)) || defaultAgent
                        return selected ? (
                          <span className="pointer-events-none absolute left-2.5 flex items-center">
                            <AgentProviderIcon agent={selected} backends={backends} size={14} />
                          </span>
                        ) : null
                      })()}
                      <select
                        value={agentId || defaultAgent?.id || ''}
                        onChange={(e) => setAgentId(e.target.value)}
                        className={`${field} w-full pl-8 font-mono`}
                      >
                        {agents.map((a) => (
                          <option key={a.id} value={a.id}>
                            @{a.handle}
                          </option>
                        ))}
                      </select>
                    </div>
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="font-semibold text-zinc-600 dark:text-zinc-400">Model</span>
                    <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="Agent's configured model" className={`${field} font-mono`} />
                  </label>
                </>
              ) : (
                <>
                  <label className="flex flex-col gap-1">
                    <span className="font-semibold text-zinc-600 dark:text-zinc-400">Image</span>
                    <input
                      value={image}
                      onChange={(e) => setImage(e.target.value)}
                      placeholder={runConfig?.image ? `${runConfig.image} (detected)` : 'Detected from the repo'}
                      className={`${field} font-mono`}
                    />
                  </label>
                  <label className="flex flex-col gap-1">
                    <span className="font-semibold text-zinc-600 dark:text-zinc-400">Setup command</span>
                    <input
                      value={setup}
                      onChange={(e) => setSetup(e.target.value)}
                      placeholder={runConfig?.setup_command || 'e.g. pip install -r requirements.txt'}
                      className={`${field} font-mono`}
                    />
                  </label>
                  {canUseRoot && (
                    <label className="inline-flex items-center gap-2 cursor-pointer select-none text-zinc-700 dark:text-zinc-300">
                      <input type="checkbox" checked={asRoot} onChange={(e) => setAsRoot(e.target.checked)} className="rounded border-zinc-300 text-teal-600" />
                      Root terminal (install packages with <code className="font-mono">apt-get install</code>)
                    </label>
                  )}
                </>
              )}
            </>
          )}

          {error && (
            <div className="px-3 py-2 rounded-lg bg-rose-500/10 border border-rose-500/30 text-rose-700 dark:text-rose-300">
              {error}
            </div>
          )}

          <div className="flex items-center justify-between gap-2 pt-1">
            <label className="inline-flex items-center gap-2 cursor-pointer select-none text-zinc-500 dark:text-zinc-400">
              <input
                type="checkbox"
                checked={launchInDock}
                onChange={(e) => {
                  const next = e.target.checked
                  setLaunchInDock(next)
                  setLaunchSessionInDock(next)
                }}
                className="rounded border-zinc-300 text-teal-600"
              />
              <span>Open in bottom dock</span>
            </label>
            <div className="flex items-center gap-2">
              <Button type="button" size="sm" variant="secondary" onClick={onCancel} className="rounded-xl text-xs font-semibold border border-zinc-200 dark:border-zinc-700 cursor-pointer">
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                variant="primary"
                isDisabled={!pid || busy}
                className="rounded-xl bg-teal-600 hover:bg-teal-500 text-white text-xs font-semibold flex items-center gap-1.5 cursor-pointer"
              >
                {busy && <Loader2 size={12} className="animate-spin" />}
                Start
              </Button>
            </div>
          </div>
        </form>
      </div>
    </div>
  )
}
