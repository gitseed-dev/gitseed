import React, { useEffect, useMemo, useState } from 'react'
import {
  AlertCircle,
  Archive,
  Bot,
  Check,
  Copy,
  RotateCcw,
  X,
} from 'lucide-react'
import { Button } from '@heroui/react'
import { useQueryClient } from '@tanstack/react-query'
import { api, ApiError } from '../../api/client'
import { queryKeys } from '../../api/queryClient'
import { VIEWS } from '../../constants'
import { AgentForm } from './agent/AgentFormSections'
import { useData } from '../../context/DataContext'
import {
  getAgentFirstPassRate,
  slugifyAgentHandle,
  type AgentTemplateDefinition,
} from '../../utils/agents'
import { formatUsd } from '../../utils/format'
import type {
  Agent,
  AgentKind,
  AgentRuntime,
} from '../../types'

export interface AgentDrawerProps {
  isOpen: boolean
  onClose: () => void
  agentToEdit?: Agent | null
  defaultKind?: AgentKind
  onArchive?: (agent: Agent) => void
  onRestore?: (agent: Agent) => void
  onNavigateToSkills?: () => void
}

export const AgentDrawer: React.FC<AgentDrawerProps> = ({
  isOpen,
  onClose,
  agentToEdit,
  defaultKind = 'stage',
  onArchive,
  onRestore,
  onNavigateToSkills,
}) => {
  const { backends, skills: availableSkills, setActiveView } = useData()
  const queryClient = useQueryClient()

  const [selectedTemplate, setSelectedTemplate] = useState<string | null>(null)
  const [name, setName] = useState('')
  const [handle, setHandle] = useState('')
  const [isHandleCustom, setIsHandleCustom] = useState(false)
  const [kind, setKind] = useState<AgentKind>('stage')
  const [runtime, setRuntime] = useState<AgentRuntime>('opencode')
  const [model, setModel] = useState('default')
  const [instructions, setInstructions] = useState('')
  const [selectedSkillIds, setSelectedSkillIds] = useState<string[]>([])
  const [tools, setTools] = useState<string[]>([])
  const [selectedBackendIds, setSelectedBackendIds] = useState<string[]>([])
  const [enabled, setEnabled] = useState(true)

  const [isSaving, setIsSaving] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [copiedId, setCopiedId] = useState(false)

  const isEditing = Boolean(agentToEdit)

  const validToolBackendIds = useMemo(
    () => new Set(backends.filter((b) => b.type === 'tools').map((b) => b.id)),
    [backends]
  )

  const validSkillIds = useMemo(
    () => new Set(availableSkills.map((s) => s.id)),
    [availableSkills]
  )

  const initialAllowedRestrictedIds = useMemo(() => {
    if (!agentToEdit) return []
    const restrictedIds = new Set(
      backends.filter((b) => b.access_restricted).map((b) => b.id)
    )
    return agentToEdit.allowed_backend_ids.filter((id) => restrictedIds.has(id))
  }, [agentToEdit, backends])

  useEffect(() => {
    if (!isOpen) return
    setErrorMessage(null)

    if (agentToEdit) {
      setSelectedTemplate(agentToEdit.template)
      setName(agentToEdit.name)
      setHandle(agentToEdit.handle)
      setIsHandleCustom(true)
      setKind(agentToEdit.kind)
      setRuntime(agentToEdit.runtime)
      setModel(agentToEdit.model || 'default')
      setInstructions(agentToEdit.instructions || '')
      setSelectedSkillIds(
        (agentToEdit.skills || []).filter((id) => validSkillIds.has(id))
      )
      setTools(
        (agentToEdit.tools || []).filter((id) => validToolBackendIds.has(id))
      )
      const restrictedIds = new Set(
        backends.filter((b) => b.access_restricted).map((b) => b.id)
      )
      setSelectedBackendIds(
        agentToEdit.allowed_backend_ids.filter((id) => restrictedIds.has(id))
      )
      setEnabled(agentToEdit.enabled)
    } else {
      setSelectedTemplate(null)
      setName('')
      setHandle('')
      setIsHandleCustom(false)
      setKind(defaultKind)
      setRuntime('opencode')
      setModel('default')
      setInstructions('')
      setSelectedSkillIds([])
      setTools([])
      setSelectedBackendIds([])
      setEnabled(true)
    }
  }, [agentToEdit, backends, defaultKind, isOpen, validSkillIds, validToolBackendIds])

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && isOpen && !isSaving) {
        onClose()
      }
    }
    window.addEventListener('keydown', handleKeyDown)
    return () => window.removeEventListener('keydown', handleKeyDown)
  }, [isOpen, isSaving, onClose])

  const handleSelectTemplate = (tpl: AgentTemplateDefinition | null) => {
    if (!tpl) {
      setSelectedTemplate(null)
      setName('')
      setHandle('')
      setIsHandleCustom(false)
      setInstructions('')
      return
    }
    setSelectedTemplate(tpl.id)
    setName(tpl.defaultName)
    setHandle(tpl.defaultHandle)
    setIsHandleCustom(false)
    setKind(tpl.kind)
    setRuntime(tpl.runtime)
    setInstructions(tpl.instructions)
  }

  const handleNameChange = (nextName: string) => {
    setName(nextName)
    if (!isHandleCustom && !isEditing) {
      setHandle(slugifyAgentHandle(nextName))
    }
  }

  const handleToggleSkill = (skillId: string) => {
    setSelectedSkillIds((prev) =>
      prev.includes(skillId)
        ? prev.filter((id) => id !== skillId)
        : [...prev, skillId]
    )
  }

  const handleToggleToolBackend = (backendId: string) => {
    setTools((prev) =>
      prev.includes(backendId)
        ? prev.filter((id) => id !== backendId)
        : [...prev, backendId]
    )
  }

  const handleToggleRestrictedBackend = (backendId: string) => {
    setSelectedBackendIds((prev) =>
      prev.includes(backendId)
        ? prev.filter((id) => id !== backendId)
        : [...prev, backendId]
    )
  }

  const isDirty = useMemo(() => {
    if (!agentToEdit) return true
    if (name.trim() !== agentToEdit.name) return true
    if (handle.trim() !== agentToEdit.handle) return true
    if (kind !== agentToEdit.kind) return true
    if (runtime !== agentToEdit.runtime) return true
    if (model.trim() !== agentToEdit.model) return true
    if (instructions.trim() !== (agentToEdit.instructions || '').trim()) return true
    if (enabled !== agentToEdit.enabled) return true

    const origSkills = (agentToEdit.skills || []).filter((id) =>
      validSkillIds.has(id)
    )
    if (selectedSkillIds.join(',') !== origSkills.join(',')) return true

    const origTools = (agentToEdit.tools || []).filter((id) =>
      validToolBackendIds.has(id)
    )
    if (tools.join(',') !== origTools.join(',')) return true

    const origBackends = new Set(initialAllowedRestrictedIds)
    if (selectedBackendIds.length !== origBackends.size) return true
    if (selectedBackendIds.some((id) => !origBackends.has(id))) return true

    return false
  }, [
    agentToEdit,
    name,
    handle,
    kind,
    runtime,
    model,
    instructions,
    enabled,
    selectedSkillIds,
    validSkillIds,
    tools,
    validToolBackendIds,
    selectedBackendIds,
    initialAllowedRestrictedIds,
  ])

  const isSaveDisabled =
    isSaving || !name.trim() || !handle.trim() || !model.trim() || (isEditing && !isDirty)

  const handleCopyId = () => {
    if (!agentToEdit) return
    navigator.clipboard.writeText(agentToEdit.id)
    setCopiedId(true)
    setTimeout(() => setCopiedId(false), 1800)
  }

  const handleSave = async () => {
    if (isSaveDisabled) return
    setIsSaving(true)
    setErrorMessage(null)

    try {
      let savedAgent: Agent
      if (agentToEdit) {
        savedAgent = await api.agents.update(agentToEdit.id, {
          name: name.trim(),
          handle: handle.trim().toLowerCase(),
          kind,
          template: selectedTemplate,
          runtime,
          model: model.trim() || 'default',
          instructions: instructions.trim(),
          skills: selectedSkillIds,
          tools,
          enabled,
        })
      } else {
        savedAgent = await api.agents.create({
          name: name.trim(),
          handle: handle.trim().toLowerCase(),
          kind,
          template: selectedTemplate,
          runtime,
          model: model.trim() || 'default',
          instructions: instructions.trim(),
          skills: selectedSkillIds,
          tools,
          enabled,
        })
      }

      const prevRestrictedSet = new Set(initialAllowedRestrictedIds)
      const nextRestrictedSet = new Set(selectedBackendIds)
      const toGrant = selectedBackendIds.filter((id) => !prevRestrictedSet.has(id))
      const toRevoke = initialAllowedRestrictedIds.filter((id) => !nextRestrictedSet.has(id))

      for (const bId of toGrant) {
        await api.backends.grantAgent(bId, savedAgent.id)
      }
      for (const bId of toRevoke) {
        await api.backends.revokeAgent(bId, savedAgent.id)
      }

      await Promise.all([
        queryClient.invalidateQueries({ queryKey: queryKeys.agents }),
        queryClient.invalidateQueries({ queryKey: queryKeys.policies }),
        queryClient.invalidateQueries({ queryKey: queryKeys.backends }),
        queryClient.invalidateQueries({ queryKey: queryKeys.activity }),
      ])
      onClose()
    } catch (err) {
      if (err instanceof ApiError) {
        setErrorMessage(err.detail)
      } else {
        setErrorMessage(err instanceof Error ? err.message : 'Failed to save agent')
      }
    } finally {
      setIsSaving(false)
    }
  }

  if (!isOpen) return null

  const firstPassRate = agentToEdit ? getAgentFirstPassRate(agentToEdit) : null
  const isArchived = Boolean(agentToEdit?.archived_at)

  return (
    <div
      className="fixed inset-0 z-50 flex justify-end bg-black/50 backdrop-blur-xs transition-opacity"
      onClick={() => {
        if (!isSaving) onClose()
      }}
    >
      <div
        className="w-full max-w-xl bg-white dark:bg-zinc-900 border-l border-zinc-200 dark:border-zinc-700/80 h-full flex flex-col shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        {/* Header */}
        <div className="px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 flex items-center justify-between gap-4 shrink-0 bg-zinc-50/50 dark:bg-zinc-900/50">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-9 h-9 rounded-xl bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700/80 flex items-center justify-center shrink-0">
              <Bot size={18} />
            </div>
            <div className="min-w-0">
              <div className="flex items-center gap-2">
                <h2 className="text-base font-bold font-heading text-zinc-900 dark:text-zinc-100 truncate">
                  {isEditing ? agentToEdit?.name : 'New agent'}
                </h2>
                {handle && (
                  <span className="font-mono text-xs text-zinc-500 dark:text-zinc-400">
                    @{handle}
                  </span>
                )}
              </div>
              {isEditing && agentToEdit && (
                <div className="flex items-center gap-2 mt-0.5 text-xs text-zinc-500 dark:text-zinc-400">
                  <button
                    type="button"
                    onClick={handleCopyId}
                    className="inline-flex items-center gap-1 font-mono hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
                  >
                    <span>{agentToEdit.id}</span>
                    {copiedId ? (
                      <Check size={11} className="text-emerald-500" />
                    ) : (
                      <Copy size={11} />
                    )}
                  </button>
                  <span>·</span>
                  <span>{agentToEdit.run_count} runs</span>
                  {firstPassRate !== null && (
                    <>
                      <span>·</span>
                      <span>{firstPassRate}% pass</span>
                    </>
                  )}
                  <span>·</span>
                  <span className="font-mono">{formatUsd(agentToEdit.total_cost)}</span>
                </div>
              )}
            </div>
          </div>

          <button
            type="button"
            onClick={onClose}
            className="p-2 rounded-xl text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
          >
            <X size={18} />
          </button>
        </div>

        {/* Body */}
        <div className="flex-1 overflow-y-auto p-6">
          {errorMessage && (
            <div className="mb-5 p-3.5 rounded-xl bg-rose-500/10 border border-rose-500/30 text-rose-600 dark:text-rose-400 text-xs flex items-start gap-2.5">
              <AlertCircle size={15} className="shrink-0 mt-0.5" />
              <span className="leading-relaxed">{errorMessage}</span>
            </div>
          )}

          <AgentForm
            isEditing={isEditing}
            selectedTemplate={selectedTemplate}
            onSelectTemplate={handleSelectTemplate}
            name={name}
            onNameChange={handleNameChange}
            handle={handle}
            onHandleChange={setHandle}
            setIsHandleCustom={setIsHandleCustom}
            kind={kind}
            onKindChange={setKind}
            runtime={runtime}
            onRuntimeChange={(nextRuntime) => {
              setRuntime(nextRuntime)
              if (agentToEdit && nextRuntime === agentToEdit.runtime) {
                setModel(agentToEdit.model || 'default')
              } else {
                setModel('default')
              }
            }}
            model={model}
            onModelChange={setModel}
            instructions={instructions}
            onInstructionsChange={setInstructions}
            enabled={enabled}
            onEnabledChange={setEnabled}
            selectedSkillIds={selectedSkillIds}
            onToggleSkill={handleToggleSkill}
            availableSkills={availableSkills}
            onNavigateToSkills={
              onNavigateToSkills
                ? () => {
                    onClose()
                    onNavigateToSkills()
                  }
                : undefined
            }
            tools={tools}
            onToggleToolBackend={handleToggleToolBackend}
            backends={backends}
            selectedBackendIds={selectedBackendIds}
            onToggleRestrictedBackend={handleToggleRestrictedBackend}
            onNavigateToBackends={() => {
              const rtDef = runtime !== 'opencode' ? runtime : null
              const providerMap: Record<string, string> = {
                'claude-code': 'anthropic',
                codex: 'openai',
                antigravity: 'antigravity',
                gemini: 'gemini',
                'grok-build': 'xai',
              }
              const targetProvider = rtDef ? providerMap[rtDef] : undefined
              const queryParams: Record<string, string> = {
                open: 'new',
                type: 'inference',
              }
              if (targetProvider) {
                queryParams.provider = targetProvider
              }
              onClose()
              setActiveView(VIEWS.BACKENDS, queryParams)
            }}
          />
        </div>

        {/* Footer */}
        <div className="px-6 py-4 border-t border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/50 dark:bg-zinc-900/50 flex items-center justify-between gap-3 shrink-0">
          <div>
            {isEditing && agentToEdit && !isArchived && onArchive && (
              <button
                type="button"
                onClick={() => onArchive(agentToEdit)}
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-rose-600 dark:text-rose-400 hover:underline cursor-pointer"
              >
                <Archive size={13} />
                <span>Archive Agent</span>
              </button>
            )}
            {isEditing && agentToEdit && isArchived && onRestore && (
              <button
                type="button"
                onClick={() => onRestore(agentToEdit)}
                className="inline-flex items-center gap-1.5 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
              >
                <RotateCcw size={13} />
                <span>Restore Agent</span>
              </button>
            )}
          </div>

          <div className="flex items-center gap-2.5">
            <Button
              variant="secondary"
              size="sm"
              onClick={onClose}
              isDisabled={isSaving}
              className="cursor-pointer font-medium"
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              onClick={handleSave}
              isDisabled={isSaveDisabled}
              className="cursor-pointer font-semibold shadow-sm shadow-teal-600/20"
            >
              {isSaving
                ? 'Saving...'
                : isEditing
                  ? 'Save Changes'
                  : 'Create agent'}
            </Button>
          </div>
        </div>
      </div>
    </div>
  )
}
