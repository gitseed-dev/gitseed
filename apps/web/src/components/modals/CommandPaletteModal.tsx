import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Activity,
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Bot,
  Check,
  ChevronDown,
  ChevronUp,
  CornerDownLeft,
  FileCode2,
  FolderGit2,
  LayoutDashboard,
  Lightbulb,
  Loader2,
  Plus,
  RotateCcw,
  Search,
  Send,
  Server,
  Settings,
  Sparkles,
  Sprout,
  Users,
  Workflow,
  X,
} from 'lucide-react'
import { api, ApiError } from '../../api/client'
import { MarkdownContent } from '../common/MarkdownContent'
import { ProviderIcon } from '../common/ProviderIcon'
import { StageBadge } from '../common/StageBadge'
import { VIEWS, type ViewType } from '../../constants'
import { useAuth } from '../../context/AuthContext'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import type { ProposedSeedDraft, Seed } from '../../types'
import {
  getSeedAttentionReason,
  seedNeedsAttention,
} from '../../utils/format'
import {
  getSeedStatusClasses,
  isSeedOpen,
  STATUS_LABELS,
} from '../../utils/seeds'

type PaletteTab = 'search' | 'chat' | 'brainstorm'

interface PaletteItem {
  id: string
  section: string
  title: string
  subtitle?: string
  badge?: React.ReactNode
  icon: React.ReactNode
  onSelect: () => void
}

interface ChatMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  timestamp: string
  model?: string
  tokens?: number
  proposedSeeds?: ProposedSeedDraft[]
  referencedFiles?: string[]
}

const THINKING_STEPS: Record<'brainstorm' | 'general', string[]> = {
  brainstorm: [
    'is reading the repository',
    'is looking through existing seeds',
    'is checking key files',
    'is thinking through the idea',
    'is shaping possible seeds',
    'is writing a reply',
  ],
  general: [
    'is looking at the workspace',
    'is checking seeds and runs',
    'is gathering the details',
    'is writing a reply',
  ],
}

/**
 * "Working…" bubble that visibly progresses: steps through phases (holding on the last one)
 * and shows elapsed time after a few seconds, so a slow reply never looks stuck.
 */
const CoordinatorThinking: React.FC<{ handle: string; mode: 'brainstorm' | 'general' }> = ({ handle, mode }) => {
  const [elapsed, setElapsed] = useState(0)
  useEffect(() => {
    const started = Date.now()
    const t = window.setInterval(() => setElapsed(Math.floor((Date.now() - started) / 1000)), 1000)
    return () => window.clearInterval(t)
  }, [])
  const steps = THINKING_STEPS[mode]
  const step = steps[Math.min(steps.length - 1, Math.floor(elapsed / 4))]
  return (
    <div className="inline-flex items-center gap-2 rounded-2xl px-3.5 py-2.5 text-sm bg-zinc-100 dark:bg-zinc-800/90 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
      <Loader2 size={13} className="animate-spin text-teal-500 shrink-0" />
      <span key={step} className="animate-fadeIn">
        @{handle} {step}…
      </span>
      {elapsed >= 5 && <span className="text-[11px] text-zinc-400 tabular-nums">{elapsed}s</span>}
    </div>
  )
}

export const CommandPaletteModal: React.FC = () => {
  const { user } = useAuth()
  const { t } = useTranslation()
  const {
    sidebarSearchOpen,
    searchModalInitialTab,
    searchModalProjectId,
    openSearchModal,
    closeSearchModal,
    seeds,
    allSeeds,
    projects,
    backends,
    agents,
    users,
    orgSettings,
    selectedProjectObj,
    setActiveView,
    navigateToDetail,
    refreshData,
  } = useData()

  const isAdmin = user?.org_role === 'admin'
  const [activeTab, setActiveTab] = useState<PaletteTab>('search')
  const [query, setQuery] = useState('')
  const [selectedIndex, setSelectedIndex] = useState(0)
  const [chatInput, setChatInput] = useState('')
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [brainstormMessages, setBrainstormMessages] = useState<ChatMessage[]>([])
  const [isAskingCoordinator, setIsAskingCoordinator] = useState(false)
  const [targetProjectId, setTargetProjectId] = useState<string>('')
  const [stageProposedSeeds, setStageProposedSeeds] = useState<boolean>(false)
  const [expandedProposalKeys, setExpandedProposalKeys] = useState<Record<string, boolean>>({})
  const [creatingProposalKeys, setCreatingProposalKeys] = useState<Record<string, boolean>>({})
  const [createdProposalSeeds, setCreatedProposalSeeds] = useState<Record<string, Seed>>({})
  const [proposalError, setProposalError] = useState<string | null>(null)

  const searchInputRef = useRef<HTMLInputElement>(null)
  const chatInputRef = useRef<HTMLInputElement>(null)

  const activeProjects = useMemo(
    () => projects.filter((p) => !p.archived_at),
    [projects]
  )

  const targetProject = useMemo(
    () =>
      activeProjects.find((p) => p.id === targetProjectId) ||
      selectedProjectObj ||
      activeProjects[0] ||
      null,
    [activeProjects, targetProjectId, selectedProjectObj]
  )

  useEffect(() => {
    if (!sidebarSearchOpen) {
      setProposalError(null)
      return
    }
    setActiveTab(searchModalInitialTab || 'search')
    const defaultProjId =
      searchModalProjectId ||
      selectedProjectObj?.id ||
      activeProjects[0]?.id ||
      ''
    setTargetProjectId(defaultProjId)
    const proj = activeProjects.find((p) => p.id === defaultProjId)
    const shouldStage =
      proj?.sandbox_config?.stage_seeds_by_default ??
      orgSettings?.default_stage_seeds ??
      false
    setStageProposedSeeds(Boolean(shouldStage))
  }, [
    sidebarSearchOpen,
    searchModalInitialTab,
    searchModalProjectId,
    selectedProjectObj?.id,
    activeProjects,
    orgSettings?.default_stage_seeds,
  ])

  const coordinatorAgent = useMemo(
    () =>
      agents.find((a) => a.kind === 'assistant' || a.handle === 'coordinator') ||
      null,
    [agents]
  )

  const projectById = useMemo(
    () => new Map(projects.map((p) => [p.id, p])),
    [projects]
  )

  // Global Ctrl+K / Cmd+K shortcut
  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault()
        if (sidebarSearchOpen) {
          closeSearchModal()
        } else {
          openSearchModal()
        }
      } else if (e.key === 'Escape' && sidebarSearchOpen) {
        e.preventDefault()
        closeSearchModal()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [sidebarSearchOpen, openSearchModal, closeSearchModal])

  // Focus input when opened
  useEffect(() => {
    if (!sidebarSearchOpen) return
    const timer = window.setTimeout(() => {
      if (activeTab === 'search') {
        searchInputRef.current?.focus()
      } else {
        chatInputRef.current?.focus()
      }
    }, 30)
    return () => window.clearTimeout(timer)
  }, [sidebarSearchOpen, activeTab])

  const handleAskCoordinator = async (
    promptText: string,
    options?: { mode?: 'general' | 'brainstorm'; proposeSeeds?: boolean }
  ) => {
    const mode =
      options?.mode || (activeTab === 'brainstorm' ? 'brainstorm' : 'general')
    const isBrainstorm = mode === 'brainstorm'
    const currentList = isBrainstorm ? brainstormMessages : messages
    const setList = isBrainstorm ? setBrainstormMessages : setMessages

    const trimmed =
      promptText.trim() ||
      (options?.proposeSeeds
        ? 'Synthesize our discussion and propose concrete, ready-to-plant seeds for this project.'
        : '')
    if (!trimmed || isAskingCoordinator) return
    const now = new Date().toLocaleTimeString([], {
      hour: '2-digit',
      minute: '2-digit',
    })
    const userMsg: ChatMessage = {
      id: `u-${Date.now()}`,
      role: 'user',
      content: trimmed,
      timestamp: now,
    }
    const nextMessages = [...currentList, userMsg]
    setList(nextMessages)
    setChatInput('')
    setQuery('')
    setActiveTab(isBrainstorm ? 'brainstorm' : 'chat')
    setIsAskingCoordinator(true)

    const effectiveProjectId = isBrainstorm
      ? targetProject?.id || selectedProjectObj?.id || null
      : selectedProjectObj?.id || null

    try {
      const res = await api.agents.chatCoordinator({
        messages: nextMessages.map((m) => ({
          role: m.role,
          content: m.content,
        })),
        project_id: effectiveProjectId,
        mode,
        propose_seeds: Boolean(options?.proposeSeeds),
      })
      const replyTime = new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      })
      const totalTokens = (res.input_tokens || 0) + (res.output_tokens || 0)
      const replyMsg: ChatMessage = {
        id: `a-${Date.now() + 1}`,
        role: 'assistant',
        content: res.reply,
        timestamp: replyTime,
        model: res.model,
        tokens: totalTokens > 0 ? totalTokens : undefined,
        proposedSeeds: res.proposed_seeds || [],
        referencedFiles: res.referenced_files || [],
      }
      setList((prev) => [...prev, replyMsg])
    } catch (err) {
      const replyTime = new Date().toLocaleTimeString([], {
        hour: '2-digit',
        minute: '2-digit',
      })
      const detail =
        err instanceof Error && err.message.trim()
          ? err.message
          : 'Unable to reach coordinator backend. Check that an LLM backend is configured and healthy.'
      const errorMsg: ChatMessage = {
        id: `a-${Date.now() + 1}`,
        role: 'assistant',
        content: `⚠️ Coordinator request failed: ${detail}`,
        timestamp: replyTime,
      }
      setList((prev) => [...prev, errorMsg])
    } finally {
      setIsAskingCoordinator(false)
    }
  }

  const handlePlantProposedSeed = async (
    proposal: ProposedSeedDraft,
    proposalKey: string
  ) => {
    if (!targetProject) {
      setProposalError('Select an active project before creating a seed.')
      return
    }
    if (creatingProposalKeys[proposalKey] || createdProposalSeeds[proposalKey]) {
      return
    }
    setProposalError(null)
    setCreatingProposalKeys((prev) => ({ ...prev, [proposalKey]: true }))
    try {
      const created = await api.seeds.create({
        project_id: targetProject.id,
        title: proposal.title,
        description: proposal.description,
        specification: proposal.specification,
        acceptance_criteria: proposal.acceptance_criteria,
        tags: proposal.tags.length > 0 ? proposal.tags : ['brainstorm'],
        autonomy: proposal.autonomy || 'supervised',
        staged: stageProposedSeeds,
      })
      setCreatedProposalSeeds((prev) => ({ ...prev, [proposalKey]: created }))
      await refreshData()
    } catch (err) {
      setProposalError(
        err instanceof ApiError
          ? err.detail
          : err instanceof Error
            ? err.message
            : 'Failed to create seed from proposal.'
      )
    } finally {
      setCreatingProposalKeys((prev) => ({ ...prev, [proposalKey]: false }))
    }
  }

  const handlePlantAllForMessage = async (
    msgId: string,
    proposals: ProposedSeedDraft[]
  ) => {
    for (let i = 0; i < proposals.length; i++) {
      const key = `${msgId}-${i}`
      if (!createdProposalSeeds[key] && !creatingProposalKeys[key]) {
        await handlePlantProposedSeed(proposals[i], key)
      }
    }
  }

  const navPages = useMemo(() => {
    const base: Array<{
      id: ViewType
      label: string
      subtitle: string
      icon: React.ReactNode
    }> = [
      {
        id: VIEWS.DASHBOARD,
        label: t('nav.dashboard'),
        subtitle: 'Operational summary, Needs You queue, and live SDLC metrics',
        icon: <LayoutDashboard size={15} />,
      },
      {
        id: VIEWS.SEEDS,
        label: t('nav.seeds'),
        subtitle: 'Requirement seeds across Ideate → Plan → Execute → Review → Merge',
        icon: <Sprout size={15} />,
      },
      {
        id: VIEWS.PROJECTS,
        label: t('nav.projects'),
        subtitle: 'Projects, repositories, members, and stage policy overrides',
        icon: <FolderGit2 size={15} />,
      },
      {
        id: VIEWS.TELEMETRY,
        label: t('nav.telemetry'),
        subtitle: 'Audit trail and activity stream across seeds, runs, and approvals',
        icon: <Activity size={15} />,
      },
    ]

    if (isAdmin) {
      base.push(
        {
          id: VIEWS.BACKENDS,
          label: t('nav.backends'),
          subtitle: 'Git providers, LLM model providers, compute runners, and MCP tools',
          icon: <Server size={15} />,
        },
        {
          id: VIEWS.AGENTS,
          label: t('nav.agents'),
          subtitle: 'Stage agents, Coordinator assistant, and reusable skills',
          icon: <Bot size={15} />,
        },
        {
          id: VIEWS.ROUTES,
          label: t('nav.routes'),
          subtitle: 'Default SDLC stage gates, required approvals, and assigned agents',
          icon: <Workflow size={15} />,
        },
        {
          id: VIEWS.USERS,
          label: t('nav.users'),
          subtitle: 'Organization members, invitations, and role assignments',
          icon: <Users size={15} />,
        },
        {
          id: VIEWS.SETTINGS,
          label: t('nav.settings'),
          subtitle: 'Organization budgets, runner concurrency, and appearance',
          icon: <Settings size={15} />,
        }
      )
    }

    base.push({
      id: VIEWS.GUIDE,
      label: t('nav.guide'),
      subtitle: 'Architecture guide and SDLC workflow reference',
      icon: <BookOpen size={15} />,
    })

    return base
  }, [isAdmin, t])

  const paletteItems = useMemo<PaletteItem[]>(() => {
    const q = query.trim().toLowerCase()
    const items: PaletteItem[] = []

    // 1. Matching or Active Seeds
    const candidateSeeds = q
      ? allSeeds.filter((s) => {
          const proj = projectById.get(s.project_id)
          const refStr = proj ? `${proj.slug}#${s.number}` : `#${s.number}`
          return (
            refStr.toLowerCase().includes(q) ||
            `#${s.number}`.includes(q) ||
            s.title.toLowerCase().includes(q) ||
            (s.description || '').toLowerCase().includes(q) ||
            s.tags.some((tag) => tag.toLowerCase().includes(q))
          )
        })
      : seeds.filter((s) => seedNeedsAttention(s) || isSeedOpen(s))

    for (const seed of candidateSeeds.slice(0, 5)) {
      const proj = projectById.get(seed.project_id)
      const needsYou = seedNeedsAttention(seed)
      const attentionReason = needsYou ? getSeedAttentionReason(seed) : null
      items.push({
        id: `seed-${seed.id}`,
        section: q ? t('coordinator.sectionSeeds') : t('coordinator.activeSeedsTitle'),
        title: `#${seed.number} · ${seed.title}`,
        subtitle:
          proj && attentionReason
            ? `${proj.name} · ${attentionReason}`
            : attentionReason || proj?.name || undefined,
        badge: (
          <div className="flex items-center gap-1.5">
            <StageBadge stage={seed.stage} isClosed={seed.status === 'closed'} />
            <span
              className={`px-1.5 py-0.5 rounded text-[10px] font-semibold border ${getSeedStatusClasses(
                seed.status
              )}`}
            >
              {STATUS_LABELS[seed.status]}
            </span>
          </div>
        ),
        icon: needsYou ? (
          <AlertTriangle size={15} className="text-amber-500" />
        ) : (
          <Sprout size={15} className="text-teal-500" />
        ),
        onSelect: () => {
          navigateToDetail(VIEWS.SEEDS, seed.id)
          closeSearchModal()
        },
      })
    }

    // 2. Matching Projects
    const matchingProjects = q
      ? projects
          .filter(
            (p) =>
              !p.archived_at &&
              (p.name.toLowerCase().includes(q) ||
                p.slug.toLowerCase().includes(q) ||
                (p.description || '').toLowerCase().includes(q))
          )
          .slice(0, 4)
      : projects.filter((p) => !p.archived_at).slice(0, 3)

    for (const proj of matchingProjects) {
      items.push({
        id: `project-${proj.id}`,
        section: t('coordinator.sectionProjects'),
        title: proj.name,
        subtitle: proj.description || proj.slug,
        badge: (
          <span className="font-mono text-[11px] px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
            {proj.slug}
          </span>
        ),
        icon: <FolderGit2 size={15} className="text-teal-500" />,
        onSelect: () => {
          navigateToDetail(VIEWS.PROJECTS, proj.slug || proj.id)
          closeSearchModal()
        },
      })
    }

    // 3. Matching Backends, Agents, and Users (when searching)
    if (isAdmin && q) {
      const matchingBackends = backends
        .filter(
          (b) =>
            b.name.toLowerCase().includes(q) ||
            b.provider.toLowerCase().includes(q) ||
            b.type.toLowerCase().includes(q) ||
            (b.endpoint || '').toLowerCase().includes(q)
        )
        .slice(0, 5)

      for (const b of matchingBackends) {
        items.push({
          id: `backend-${b.id}`,
          section: t('coordinator.sectionBackends'),
          title: b.name,
          subtitle: [b.provider, b.endpoint].filter(Boolean).join(' · '),
          badge: (
            <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
              {b.type}
            </span>
          ),
          icon: (
            <ProviderIcon
              type={b.type}
              provider={b.provider}
              size={15}
              className="text-teal-500"
            />
          ),
          onSelect: () => {
            navigateToDetail(VIEWS.BACKENDS, b.id)
            closeSearchModal()
          },
        })
      }

      const matchingAgents = agents
        .filter(
          (a) =>
            !a.archived_at &&
            (a.name.toLowerCase().includes(q) ||
              a.handle.toLowerCase().includes(q) ||
              `@${a.handle.toLowerCase()}`.includes(q) ||
              (a.instructions || '').toLowerCase().includes(q) ||
              a.runtime.toLowerCase().includes(q) ||
              a.model.toLowerCase().includes(q))
        )
        .slice(0, 5)

      for (const a of matchingAgents) {
        items.push({
          id: `agent-${a.id}`,
          section: t('coordinator.sectionAgents'),
          title: a.name,
          subtitle: `${a.runtime} · ${a.model}`,
          badge: (
            <span className="font-mono text-[11px] px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
              @{a.handle}
            </span>
          ),
          icon: <Bot size={15} className="text-teal-500" />,
          onSelect: () => {
            navigateToDetail(VIEWS.AGENTS, a.handle)
            closeSearchModal()
          },
        })
      }

      const matchingUsers = users
        .filter(
          (u) =>
            (u.name || '').toLowerCase().includes(q) ||
            (u.preferred_name || '').toLowerCase().includes(q) ||
            u.email.toLowerCase().includes(q) ||
            u.org_role.toLowerCase().includes(q)
        )
        .slice(0, 4)

      for (const u of matchingUsers) {
        items.push({
          id: `user-${u.id}`,
          section: t('coordinator.sectionUsers'),
          title: u.preferred_name || u.name || u.email,
          subtitle: u.email,
          badge: (
            <span className="font-mono text-[10px] uppercase tracking-wider px-2 py-0.5 rounded-md bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80">
              {u.org_role}
            </span>
          ),
          icon: <Users size={15} className="text-teal-500" />,
          onSelect: () => {
            navigateToDetail(VIEWS.USERS, u.id)
            closeSearchModal()
          },
        })
      }
    }

    // 4. Matching Navigation Pages
    const matchingPages = q
      ? navPages.filter(
          (p) =>
            p.label.toLowerCase().includes(q) ||
            p.subtitle.toLowerCase().includes(q)
        )
      : navPages.slice(0, 5)

    for (const page of matchingPages) {
      items.push({
        id: `nav-${page.id}`,
        section: q ? t('coordinator.sectionPages') : t('coordinator.quickNavTitle'),
        title: page.label,
        subtitle: page.subtitle,
        icon: page.icon,
        onSelect: () => {
          setActiveView(page.id)
          closeSearchModal()
        },
      })
    }

    // 5. Ask Coordinator & Brainstorm options when query is non-empty
    if (q) {
      items.push({
        id: 'ask-coordinator',
        section: t('coordinator.sectionCoordinator'),
        title: t('coordinator.askCoordinatorQuery', { query: query.trim() }),
        subtitle: t('coordinator.askCoordinatorSubtext'),
        icon: <Sparkles size={15} className="text-teal-500" />,
        onSelect: () => handleAskCoordinator(query, { mode: 'general' }),
      })
      items.push({
        id: 'brainstorm-coordinator',
        section: t('coordinator.sectionCoordinator'),
        title: `Brainstorm seeds for "${query.trim()}"`,
        subtitle:
          'Architect feature ideas with live repository + seed context and turn them into Seeds',
        icon: <Lightbulb size={15} className="text-amber-500" />,
        onSelect: () => handleAskCoordinator(query, { mode: 'brainstorm' }),
      })
    }

    return items
  }, [
    query,
    allSeeds,
    seeds,
    projects,
    projectById,
    isAdmin,
    backends,
    agents,
    users,
    navPages,
    t,
    navigateToDetail,
    setActiveView,
    closeSearchModal,
  ])

  const effectiveSelectedIndex =
    paletteItems.length === 0
      ? 0
      : Math.min(selectedIndex, paletteItems.length - 1)

  if (!sidebarSearchOpen) return null

  const groupedSections: Array<{
    section: string
    items: Array<{ item: PaletteItem; index: number }>
  }> = []
  paletteItems.forEach((item, index) => {
    const existing = groupedSections.find((g) => g.section === item.section)
    if (existing) {
      existing.items.push({ item, index })
    } else {
      groupedSections.push({ section: item.section, items: [{ item, index }] })
    }
  })

  const suggestions = [
    t('coordinator.copilotSuggestions.needsAttention'),
    t('coordinator.copilotSuggestions.inProgress'),
    t('coordinator.copilotSuggestions.spendBudget'),
    'Backend & agent readiness',
  ]

  const brainstormSuggestions = [
    'Review the repo architecture and suggest high-impact feature seeds',
    'How can we break our next milestone into incremental, testable seeds?',
    'What existing seeds are open and what capability should we build next?',
  ]

  const isBrainstormTab = activeTab === 'brainstorm'
  const activeChatMessages = isBrainstormTab ? brainstormMessages : messages

  return (
    <div
      className="fixed inset-0 z-50 flex items-start justify-center pt-[8vh] px-4 bg-black/50 backdrop-blur-xs animate-fadeIn"
      onClick={closeSearchModal}
    >
      <div
        className="w-full max-w-3xl rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900 shadow-2xl overflow-hidden flex flex-col max-h-[82vh]"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header: mode tabs (left) · context + actions (right). Search input sits below, on its own row. */}
        <div className="px-3 pt-2 border-b border-zinc-200 dark:border-zinc-700/80 flex items-end justify-between gap-3">
          <div className="flex items-center gap-1">
            {(
              [
                { id: 'search', label: t('coordinator.tabSearch'), icon: <Search size={13} /> },
                { id: 'chat', label: 'Ask', icon: <Sparkles size={13} /> },
                { id: 'brainstorm', label: 'Brainstorm', icon: <Lightbulb size={13} /> },
              ] as const
            ).map((tab) => (
              <button
                key={tab.id}
                type="button"
                onClick={() => setActiveTab(tab.id)}
                className={`px-2.5 py-2 -mb-px border-b-2 text-xs font-semibold flex items-center gap-1.5 transition cursor-pointer ${
                  activeTab === tab.id
                    ? 'border-teal-500 text-zinc-900 dark:text-zinc-100'
                    : 'border-transparent text-zinc-500 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-200'
                }`}
              >
                {tab.icon}
                <span>{tab.label}</span>
              </button>
            ))}
          </div>

          <div className="flex items-center gap-1.5 pb-1.5 min-w-0">
            {isBrainstormTab && activeProjects.length > 0 ? (
              <select
                value={targetProject?.id || ''}
                onChange={(e) => {
                  const nextId = e.target.value
                  setTargetProjectId(nextId)
                  const proj = activeProjects.find((p) => p.id === nextId)
                  const shouldStage =
                    proj?.sandbox_config?.stage_seeds_by_default ??
                    orgSettings?.default_stage_seeds ??
                    false
                  setStageProposedSeeds(Boolean(shouldStage))
                }}
                aria-label="Project to brainstorm for"
                title="Project to brainstorm for"
                className="max-w-[180px] text-xs px-2 py-1 rounded-lg bg-transparent hover:bg-zinc-100 dark:hover:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700 focus:outline-none cursor-pointer truncate"
              >
                {activeProjects.map((p) => (
                  <option key={p.id} value={p.id} className="bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100">
                    {p.name}
                  </option>
                ))}
              </select>
            ) : activeTab === 'chat' ? (
              <span className="text-xs text-zinc-500 dark:text-zinc-400 truncate">
                {selectedProjectObj ? selectedProjectObj.name : 'All projects'}
              </span>
            ) : null}
            {activeTab !== 'search' && activeChatMessages.length > 0 && (
              <button
                type="button"
                onClick={() => (isBrainstormTab ? setBrainstormMessages([]) : setMessages([]))}
                title={t('coordinator.actions.clear')}
                className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 cursor-pointer"
              >
                <RotateCcw size={13} />
              </button>
            )}
            <button
              type="button"
              onClick={closeSearchModal}
              className="p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer shrink-0"
              aria-label={t('coordinator.actions.close')}
            >
              <X size={15} />
            </button>
          </div>
        </div>

        {activeTab === 'search' && (
          <div className="px-4 py-3 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center gap-2.5">
            <Search size={16} className="text-zinc-400 shrink-0" />
            <input
              ref={searchInputRef}
              type="text"
              value={query}
              onChange={(e) => {
                setQuery(e.target.value)
                setSelectedIndex(0)
              }}
              onKeyDown={(e) => {
                if (e.key === 'ArrowDown') {
                  e.preventDefault()
                  setSelectedIndex((prev) =>
                    paletteItems.length > 0 ? (prev + 1) % paletteItems.length : 0
                  )
                } else if (e.key === 'ArrowUp') {
                  e.preventDefault()
                  setSelectedIndex((prev) =>
                    paletteItems.length > 0
                      ? (prev - 1 + paletteItems.length) % paletteItems.length
                      : 0
                  )
                } else if (e.key === 'Enter') {
                  e.preventDefault()
                  const target = paletteItems[effectiveSelectedIndex]
                  if (target) {
                    target.onSelect()
                  } else if (query.trim()) {
                    void handleAskCoordinator(query, { mode: 'general' })
                  }
                }
              }}
              placeholder={t('coordinator.searchOrAskPlaceholder')}
              className="w-full bg-transparent text-base text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none"
            />
            {query && (
              <button
                type="button"
                onClick={() => {
                  setQuery('')
                  setSelectedIndex(0)
                }}
                className="p-1 rounded-lg text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 cursor-pointer"
              >
                <X size={14} />
              </button>
            )}
          </div>
        )}

        {/* Body */}
        {activeTab === 'search' ? (
          <div className="flex-1 overflow-y-auto p-3 space-y-4">
            {groupedSections.length === 0 ? (
              <div className="py-10 text-center space-y-2">
                <p className="text-sm font-semibold text-zinc-700 dark:text-zinc-300">
                  {t('coordinator.noMatchHeader')}
                </p>
                {query.trim() && (
                  <div className="flex items-center justify-center gap-2 flex-wrap">
                    <button
                      type="button"
                      onClick={() => handleAskCoordinator(query, { mode: 'general' })}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-teal-500/10 text-teal-600 dark:text-teal-400 border border-teal-500/25 hover:bg-teal-500/20 transition cursor-pointer"
                    >
                      <Sparkles size={13} />
                      <span>
                        {t('coordinator.askCoordinatorQuery', { query: query.trim() })}
                      </span>
                    </button>
                    <button
                      type="button"
                      onClick={() => handleAskCoordinator(query, { mode: 'brainstorm' })}
                      className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-xl text-xs font-semibold bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700 hover:bg-zinc-200/70 dark:hover:bg-zinc-700/70 transition cursor-pointer"
                    >
                      <Lightbulb size={13} />
                      <span>Brainstorm Seeds for &ldquo;{query.trim()}&rdquo;</span>
                    </button>
                  </div>
                )}
              </div>
            ) : (
              groupedSections.map((group) => (
                <div key={group.section} className="space-y-1">
                  <div className="px-2.5 py-1 text-[11px] font-bold uppercase tracking-wider text-zinc-400 dark:text-zinc-500">
                    {group.section}
                  </div>
                  <div className="space-y-0.5">
                    {group.items.map(({ item, index }) => {
                      const isSelected = index === effectiveSelectedIndex
                      return (
                        <button
                          key={item.id}
                          type="button"
                          onMouseEnter={() => setSelectedIndex(index)}
                          onClick={item.onSelect}
                          className={`w-full px-3 py-2.5 rounded-xl flex items-center justify-between gap-3 text-left transition cursor-pointer ${
                            isSelected
                              ? 'bg-teal-500/10 dark:bg-teal-500/15 text-zinc-900 dark:text-zinc-100'
                              : 'hover:bg-zinc-100/80 dark:hover:bg-zinc-800/60 text-zinc-700 dark:text-zinc-300'
                          }`}
                        >
                          <div className="flex items-center gap-3 min-w-0">
                            <span className="text-zinc-400 dark:text-zinc-500 shrink-0">
                              {item.icon}
                            </span>
                            <div className="min-w-0">
                              <div className="text-sm font-semibold truncate">
                                {item.title}
                              </div>
                              {item.subtitle && (
                                <div className="text-xs text-zinc-500 dark:text-zinc-400 truncate mt-0.5">
                                  {item.subtitle}
                                </div>
                              )}
                            </div>
                          </div>
                          <div className="flex items-center gap-2 shrink-0">
                            {item.badge}
                            <CornerDownLeft
                              size={13}
                              className={
                                isSelected
                                  ? 'text-teal-600 dark:text-teal-400 opacity-100'
                                  : 'opacity-0'
                              }
                            />
                          </div>
                        </button>
                      )
                    })}
                  </div>
                </div>
              ))
            )}
          </div>
        ) : (
          <div className="flex-1 flex flex-col min-h-[340px] max-h-[65vh]">
            <div className="flex-1 overflow-y-auto p-4 space-y-3">
              {activeChatMessages.length === 0 ? (
                <div className="h-full flex flex-col items-center justify-center text-center gap-5 py-8 px-4">
                  <div className="space-y-1.5 max-w-md">
                    <div className="mx-auto w-9 h-9 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center">
                      {isBrainstormTab ? <Lightbulb size={17} /> : <Sparkles size={17} />}
                    </div>
                    <p className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
                      {isBrainstormTab
                        ? `Brainstorm ${targetProject ? `for ${targetProject.name}` : 'seeds'}`
                        : `Ask ${coordinatorAgent ? `@${coordinatorAgent.handle}` : 'the coordinator'}`}
                    </p>
                    <p className="text-sm text-zinc-500 dark:text-zinc-400">
                      {isBrainstormTab
                        ? 'Talk an idea through. It reads the repo and existing seeds, then proposes seeds you can plant.'
                        : 'Questions about seeds, runs, spend and agents across your workspace.'}
                    </p>
                  </div>
                  <div className="w-full max-w-md flex flex-col gap-1">
                    {(isBrainstormTab ? brainstormSuggestions : suggestions).map((sug) => (
                      <button
                        key={sug}
                        type="button"
                        onClick={() =>
                          handleAskCoordinator(sug, {
                            mode: isBrainstormTab ? 'brainstorm' : 'general',
                          })
                        }
                        className="group w-full px-3 py-2 rounded-xl text-left text-sm text-zinc-600 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800/70 transition cursor-pointer flex items-center justify-between gap-2"
                      >
                        <span>{sug}</span>
                        <ArrowRight size={12} className="shrink-0 opacity-0 group-hover:opacity-60 transition" />
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <>
                  {activeChatMessages.map((msg) => (
                    <div
                      key={msg.id}
                      className={`flex flex-col ${
                        msg.role === 'user' ? 'items-end' : 'items-start'
                      }`}
                    >
                      <div
                        className={`max-w-[92%] rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed ${
                          msg.role === 'user'
                            ? 'bg-teal-600 text-white font-medium whitespace-pre-wrap'
                            : 'bg-zinc-100 dark:bg-zinc-800/90 text-zinc-800 dark:text-zinc-200 border border-zinc-200 dark:border-zinc-700/80'
                        }`}
                      >
                        {msg.role === 'assistant' ? (
                          <MarkdownContent content={msg.content} />
                        ) : (
                          msg.content
                        )}

                        {/* Referenced repository files */}
                        {msg.role === 'assistant' &&
                          msg.referencedFiles &&
                          msg.referencedFiles.length > 0 && (
                            <div
                              className="mt-2 flex items-center gap-1 text-[11px] text-zinc-400"
                              title={msg.referencedFiles.join('\n')}
                            >
                              <FileCode2 size={11} />
                              <span>
                                Read {msg.referencedFiles.length} file{msg.referencedFiles.length === 1 ? '' : 's'}
                              </span>
                            </div>
                          )}

                        {/* Proposed Seed Cards */}
                        {msg.role === 'assistant' &&
                          msg.proposedSeeds &&
                          msg.proposedSeeds.length > 0 && (
                            <div className="mt-3 pt-3 border-t border-zinc-200 dark:border-zinc-700/80 space-y-2.5">
                              <div className="flex items-center justify-between gap-2 flex-wrap">
                                <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-700 dark:text-zinc-300">
                                  <Sprout size={13} />
                                  <span>
                                    Proposed Seeds ({msg.proposedSeeds.length})
                                  </span>
                                </div>
                                <div className="flex items-center gap-3 flex-wrap">
                                  <label className="inline-flex items-center gap-1.5 text-xs text-zinc-600 dark:text-zinc-300 cursor-pointer select-none">
                                    <input
                                      type="checkbox"
                                      checked={stageProposedSeeds}
                                      onChange={(e) =>
                                        setStageProposedSeeds(e.target.checked)
                                      }
                                      className="rounded border-zinc-300 dark:border-zinc-600 text-teal-600 focus:ring-teal-500"
                                    />
                                    <span>Stage for later</span>
                                  </label>
                                  {msg.proposedSeeds.length > 1 && (
                                    <button
                                      type="button"
                                      onClick={() =>
                                        void handlePlantAllForMessage(
                                          msg.id,
                                          msg.proposedSeeds || []
                                        )
                                      }
                                      className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-600 hover:bg-teal-500 text-white transition cursor-pointer"
                                    >
                                      <Plus size={12} />
                                      <span>
                                        {stageProposedSeeds
                                          ? `Stage All (${msg.proposedSeeds.length})`
                                          : `Plant All (${msg.proposedSeeds.length})`}
                                      </span>
                                    </button>
                                  )}
                                </div>
                              </div>

                              {proposalError && (
                                <div
                                  role="alert"
                                  className="p-2.5 rounded-xl bg-rose-500/10 border border-rose-500/30 flex items-start justify-between gap-2 text-xs text-rose-700 dark:text-rose-300"
                                >
                                  <div className="flex items-start gap-2 min-w-0">
                                    <AlertTriangle size={14} className="shrink-0 mt-0.5 text-rose-500" />
                                    <span className="break-words">{proposalError}</span>
                                  </div>
                                  <button
                                    type="button"
                                    onClick={() => setProposalError(null)}
                                    aria-label="Dismiss error"
                                    className="text-rose-500 hover:text-rose-700 dark:hover:text-rose-200 cursor-pointer shrink-0"
                                  >
                                    <X size={13} />
                                  </button>
                                </div>
                              )}

                              <div className="space-y-2">
                                {msg.proposedSeeds.map((proposal, idx) => {
                                  const key = `${msg.id}-${idx}`
                                  const isExpanded = Boolean(expandedProposalKeys[key])
                                  const isCreating = Boolean(creatingProposalKeys[key])
                                  const createdSeed = createdProposalSeeds[key]

                                  return (
                                    <div
                                      key={key}
                                      className="p-3 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 shadow-2xs space-y-2"
                                    >
                                      <div className="flex items-start justify-between gap-2">
                                        <div className="min-w-0 space-y-0.5">
                                          <div className="font-semibold text-sm text-zinc-900 dark:text-zinc-100">
                                            {proposal.title}
                                          </div>
                                          {proposal.description && (
                                            <p className="text-xs text-zinc-600 dark:text-zinc-400 leading-relaxed mt-0.5">
                                              {proposal.description}
                                            </p>
                                          )}
                                        </div>

                                        <div className="flex items-center gap-1.5 shrink-0">
                                          {createdSeed ? (
                                            <button
                                              type="button"
                                              onClick={() => {
                                                navigateToDetail(
                                                  VIEWS.SEEDS,
                                                  createdSeed.id
                                                )
                                                closeSearchModal()
                                              }}
                                              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border border-emerald-500/30 hover:bg-emerald-500/25 transition cursor-pointer"
                                            >
                                              <Check size={12} />
                                              <span>
                                                Open #{createdSeed.number}
                                              </span>
                                            </button>
                                          ) : (
                                            <button
                                              type="button"
                                              disabled={isCreating || !targetProject}
                                              onClick={() =>
                                                void handlePlantProposedSeed(
                                                  proposal,
                                                  key
                                                )
                                              }
                                              className="inline-flex items-center gap-1 px-2.5 py-1 rounded-lg text-xs font-semibold bg-teal-600 hover:bg-teal-500 disabled:opacity-50 text-white transition cursor-pointer"
                                            >
                                              {isCreating ? (
                                                <Loader2
                                                  size={12}
                                                  className="animate-spin"
                                                />
                                              ) : (
                                                <Plus size={12} />
                                              )}
                                              <span>
                                                {stageProposedSeeds
                                                  ? 'Stage seed'
                                                  : 'Create seed'}
                                              </span>
                                            </button>
                                          )}
                                        </div>
                                      </div>

                                      {(proposal.specification ||
                                        proposal.acceptance_criteria.length > 0) && (
                                        <div>
                                          <button
                                            type="button"
                                            onClick={() =>
                                              setExpandedProposalKeys((prev) => ({
                                                ...prev,
                                                [key]: !prev[key],
                                              }))
                                            }
                                            className="inline-flex items-center gap-1 text-xs font-medium text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
                                          >
                                            {isExpanded ? (
                                              <>
                                                <ChevronUp size={12} />
                                                <span>Hide details</span>
                                              </>
                                            ) : (
                                              <>
                                                <ChevronDown size={12} />
                                                <span>Details</span>
                                              </>
                                            )}
                                          </button>

                                          {isExpanded && (
                                            <div className="mt-2 pt-2 border-t border-zinc-100 dark:border-zinc-800 space-y-2 text-xs">
                                              {proposal.specification && (
                                                <div className="space-y-1">
                                                  <div className="font-semibold text-zinc-700 dark:text-zinc-300">
                                                    Specification
                                                  </div>
                                                  <div className="p-2 rounded-lg bg-zinc-50 dark:bg-zinc-800/60 text-zinc-700 dark:text-zinc-300 whitespace-pre-wrap text-xs leading-relaxed">
                                                    {proposal.specification}
                                                  </div>
                                                </div>
                                              )}
                                              {proposal.acceptance_criteria.length > 0 && (
                                                <div className="space-y-1">
                                                  <div className="font-semibold text-zinc-700 dark:text-zinc-300">
                                                    Acceptance Criteria
                                                  </div>
                                                  <ul className="list-disc list-inside space-y-0.5 text-zinc-600 dark:text-zinc-400">
                                                    {proposal.acceptance_criteria.map(
                                                      (criterion, cIdx) => (
                                                        <li key={cIdx}>{criterion}</li>
                                                      )
                                                    )}
                                                  </ul>
                                                </div>
                                              )}
                                            </div>
                                          )}
                                        </div>
                                      )}
                                    </div>
                                  )
                                })}
                              </div>
                            </div>
                          )}
                      </div>
                      <div className="flex items-center gap-2 text-[11px] text-zinc-400 mt-1 px-1">
                        <span>{msg.timestamp}</span>
                        {msg.role === 'assistant' && msg.model && (
                          <span>· {msg.model}</span>
                        )}
                        {msg.role === 'assistant' && msg.tokens ? (
                          <span>· {msg.tokens.toLocaleString()} tok</span>
                        ) : null}
                      </div>
                    </div>
                  ))}
                  {isAskingCoordinator && (
                    <div className="flex flex-col items-start">
                      <CoordinatorThinking
                        handle={coordinatorAgent ? coordinatorAgent.handle : 'coordinator'}
                        mode={isBrainstormTab ? 'brainstorm' : 'general'}
                      />
                    </div>
                  )}
                </>
              )}
            </div>

            <form
              onSubmit={(e) => {
                e.preventDefault()
                void handleAskCoordinator(chatInput, {
                  mode: isBrainstormTab ? 'brainstorm' : 'general',
                })
              }}
              className="p-3 border-t border-zinc-200 dark:border-zinc-700/80"
            >
              <div className="flex items-center gap-1.5 pl-3.5 pr-1.5 py-1.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/70 border border-zinc-200 dark:border-zinc-700/80 focus-within:border-teal-500 transition">
                <input
                  ref={chatInputRef}
                  type="text"
                  value={chatInput}
                  onChange={(e) => setChatInput(e.target.value)}
                  disabled={isAskingCoordinator}
                  placeholder={isBrainstormTab ? 'Describe an idea or feature…' : t('coordinator.inputPlaceholder')}
                  className="flex-1 min-w-0 bg-transparent text-sm text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none disabled:opacity-60"
                />
                {isBrainstormTab && brainstormMessages.length > 0 && (
                  <button
                    type="button"
                    disabled={isAskingCoordinator}
                    onClick={() =>
                      void handleAskCoordinator(chatInput, {
                        mode: 'brainstorm',
                        proposeSeeds: true,
                      })
                    }
                    className="h-8 px-2.5 rounded-lg text-xs font-semibold text-teal-700 dark:text-teal-300 hover:bg-teal-500/10 disabled:opacity-40 flex items-center gap-1.5 transition cursor-pointer shrink-0"
                    title="Turn the conversation so far into seed proposals"
                  >
                    <Sprout size={13} />
                    <span>Propose seeds</span>
                  </button>
                )}
                <button
                  type="submit"
                  disabled={!chatInput.trim() || isAskingCoordinator}
                  title={t('coordinator.actions.send')}
                  className="h-8 w-8 rounded-lg bg-teal-600 hover:bg-teal-500 disabled:opacity-40 text-white flex items-center justify-center transition cursor-pointer shrink-0"
                >
                  {isAskingCoordinator ? <Loader2 size={14} className="animate-spin" /> : <Send size={14} />}
                </button>
              </div>
            </form>
          </div>
        )}

        <div className="px-4 py-2 border-t border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between text-[11px] text-zinc-400">
          <span>{activeTab === 'search' ? t('coordinator.pressEnterToNavigate') : 'Enter to send'}</span>
          <span className="font-mono">Esc to close</span>
        </div>
      </div>
    </div>
  )
}
