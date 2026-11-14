import React, { useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import {
  AlertTriangle,
  ArrowRight,
  BookOpen,
  Check,
  ChevronDown,
  Lock,
  RefreshCw,
  Wrench,
} from 'lucide-react'
import { api } from '../../../api/client'
import { queryKeys } from '../../../api/queryClient'
import { ProviderIcon, RuntimeIcon } from '../../common/ProviderIcon'
import {
  AGENT_RUNTIME_DEFINITIONS,
  AGENT_TEMPLATES,
  getCompatibleModelBackends,
  getConfiguredModelOptions,
  getNativeProviderBackends,
  getRuntimeDefinition,
  type AgentTemplateDefinition,
} from '../../../utils/agents'
import { formatProviderName, getBackendModels } from '../../../utils/backends'
import type {
  AgentKind,
  AgentRuntime,
  Backend,
  Skill,
} from '../../../types'

export interface AgentFormProps {
  isEditing: boolean
  selectedTemplate: string | null
  onSelectTemplate: (tpl: AgentTemplateDefinition | null) => void
  name: string
  onNameChange: (val: string) => void
  handle: string
  onHandleChange: (val: string) => void
  setIsHandleCustom: (val: boolean) => void
  kind: AgentKind
  onKindChange: (val: AgentKind) => void
  runtime: AgentRuntime
  onRuntimeChange: (val: AgentRuntime) => void
  model: string
  onModelChange: (val: string) => void
  instructions: string
  onInstructionsChange: (val: string) => void
  enabled: boolean
  onEnabledChange: (val: boolean) => void
  selectedSkillIds: string[]
  onToggleSkill: (skillId: string) => void
  availableSkills: Skill[]
  onNavigateToSkills?: () => void
  tools: string[]
  onToggleToolBackend: (backendId: string) => void
  backends: Backend[]
  selectedBackendIds: string[]
  onToggleRestrictedBackend: (backendId: string) => void
  onNavigateToBackends: () => void
}

export const AgentForm: React.FC<AgentFormProps> = ({
  isEditing,
  selectedTemplate,
  onSelectTemplate,
  name,
  onNameChange,
  handle,
  onHandleChange,
  setIsHandleCustom,
  kind,
  onKindChange,
  runtime,
  onRuntimeChange,
  model,
  onModelChange,
  instructions,
  onInstructionsChange,
  enabled,
  onEnabledChange,
  selectedSkillIds,
  onToggleSkill,
  availableSkills,
  onNavigateToSkills,
  tools,
  onToggleToolBackend,
  backends,
  selectedBackendIds,
  onToggleRestrictedBackend,
  onNavigateToBackends,
}) => {
  const queryClient = useQueryClient()
  const [editingHandle, setEditingHandle] = useState(false)
  const [advancedOpen, setAdvancedOpen] = useState(false)
  const [isSyncingModels, setIsSyncingModels] = useState(false)
  const autoDiscoveredModelBackendIdsRef = useRef<Set<string>>(new Set())

  const runtimeDef = getRuntimeDefinition(runtime)
  const { data: runtimeAvailability } = useQuery({
    queryKey: queryKeys.runtimeAvailability,
    queryFn: api.agents.runtimeAvailability,
    staleTime: 5 * 60 * 1000,
    retry: false,
  })
  // `false` only when the server positively knows the binary is missing; unknown => no badge.
  const isRuntimeMissing = (id: AgentRuntime): boolean =>
    runtimeAvailability?.available?.[id] === false
  const selectedRuntimeMissing = isRuntimeMissing(runtime)
  const compatibleModelBackends = getCompatibleModelBackends(runtime, backends)
  const configuredModels = getConfiguredModelOptions(runtime, backends)
  const nativeProviderBackends = getNativeProviderBackends(runtime, backends)
  const builtInModelOptions = configuredModels.filter((m) => m.group === 'builtin')
  const backendModelOptions = configuredModels.filter((m) => m.group === 'backend')
  const toolBackends = backends.filter((b) => b.type === 'tools' && b.enabled)
  const restrictedBackends = backends.filter((b) => b.access_restricted)

  useEffect(() => {
    const unsynced = compatibleModelBackends.filter(
      (b) =>
        getBackendModels(b).length === 0 &&
        !autoDiscoveredModelBackendIdsRef.current.has(b.id)
    )
    if (unsynced.length === 0) return
    for (const b of unsynced) {
      autoDiscoveredModelBackendIdsRef.current.add(b.id)
    }
    setIsSyncingModels(true)
    Promise.allSettled(unsynced.map((b) => api.backends.discoverModels(b.id)))
      .then(() => {
        queryClient.invalidateQueries({ queryKey: queryKeys.backends })
      })
      .finally(() => {
        setIsSyncingModels(false)
      })
  }, [compatibleModelBackends, queryClient])

  const handleSyncAllModels = async () => {
    if (compatibleModelBackends.length === 0) return
    setIsSyncingModels(true)
    try {
      await Promise.allSettled(
        compatibleModelBackends.map((b) => api.backends.discoverModels(b.id))
      )
      await queryClient.invalidateQueries({ queryKey: queryKeys.backends })
    } finally {
      setIsSyncingModels(false)
    }
  }

  return (
    <div className="flex flex-col gap-5">
      {/* Optional Preset Pills (Create mode only — none selected by default) */}
      {!isEditing && (
        <div>
          <div className="flex items-center justify-between mb-2">
            <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              Preset Template <span className="font-normal text-zinc-400">(optional)</span>
            </label>
            {selectedTemplate && (
              <button
                type="button"
                onClick={() => onSelectTemplate(null)}
                className="text-xs font-semibold text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
              >
                Clear preset
              </button>
            )}
          </div>
          <div className="flex items-center gap-2 flex-wrap">
            {AGENT_TEMPLATES.map((tpl) => {
              const isSelected = selectedTemplate === tpl.id
              return (
                <button
                  key={tpl.id}
                  type="button"
                  onClick={() => onSelectTemplate(isSelected ? null : tpl)}
                  className={`px-3 py-1.5 rounded-xl text-xs font-semibold border transition cursor-pointer ${
                    isSelected
                      ? 'border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300'
                      : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50 dark:bg-zinc-800/60 text-zinc-600 dark:text-zinc-400 hover:border-zinc-300 dark:hover:border-zinc-600'
                  }`}
                >
                  {tpl.label}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Name + Subtle Inline @handle */}
      <div>
        <div className="flex items-center justify-between mb-1.5">
          <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300">
            Name <span className="text-rose-500">*</span>
          </label>
          <div className="flex items-center gap-1.5 text-xs">
            <span className="text-zinc-400 font-mono">
              @{handle || 'agent'}
            </span>
            <button
              type="button"
              onClick={() => {
                setEditingHandle((prev) => !prev)
                if (!editingHandle) setIsHandleCustom(true)
              }}
              className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
            >
              {editingHandle ? 'done' : 'edit handle'}
            </button>
          </div>
        </div>
        <input
          type="text"
          value={name}
          onChange={(e) => onNameChange(e.target.value)}
          placeholder="e.g. Security Auditor or Frontend Coder"
          className="w-full px-3.5 py-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-sm text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition"
        />

        {editingHandle && (
          <div className="mt-2 relative flex items-center">
            <span className="absolute left-3.5 text-xs font-mono text-zinc-400 select-none">
              @
            </span>
            <input
              type="text"
              value={handle}
              onChange={(e) => {
                setIsHandleCustom(true)
                onHandleChange(
                  e.target.value
                    .toLowerCase()
                    .replace(/[^a-z0-9-]/g, '')
                    .slice(0, 32)
                )
              }}
              placeholder="security-auditor"
              className="w-full pl-8 pr-3.5 py-2 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition"
            />
          </div>
        )}
      </div>

      {/* Runtime & Target Model */}
      <div className="flex flex-col gap-4 p-4 rounded-xl bg-zinc-50/70 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80">
        <div>
          <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
            Runtime Harness
          </label>
          <div className="grid grid-cols-3 gap-2">
            {AGENT_RUNTIME_DEFINITIONS.map((rt) => {
              const isSelected = runtime === rt.id
              const isMissing = isRuntimeMissing(rt.id)
              return (
                <button
                  key={rt.id}
                  type="button"
                  onClick={() => onRuntimeChange(rt.id)}
                  title={isMissing ? `${rt.shortLabel} is not installed in the runner image` : undefined}
                  data-testid={`runtime-option-${rt.id}`}
                  className={`relative py-2 px-3 rounded-xl border text-xs font-semibold transition cursor-pointer flex items-center justify-center gap-2 ${
                    isSelected
                      ? 'border-teal-500 bg-teal-500/10 text-teal-700 dark:text-teal-300'
                      : 'border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900/80 text-zinc-700 dark:text-zinc-300 hover:border-zinc-300 dark:hover:border-zinc-600'
                  } ${isMissing && !isSelected ? 'opacity-60' : ''}`}
                >
                  <RuntimeIcon runtime={rt.id} size={14} />
                  <span>{rt.shortLabel}</span>
                  {isMissing && (
                    <span className="absolute -top-2 right-1 px-1.5 py-px rounded-md bg-amber-100 dark:bg-amber-500/20 text-amber-800 dark:text-amber-300 text-xs font-semibold uppercase tracking-wide">
                      Not installed
                    </span>
                  )}
                </button>
              )
            })}
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-1.5 leading-relaxed">
            {runtimeDef.description}
          </p>
          {selectedRuntimeMissing && (
            <div
              role="alert"
              data-testid="runtime-not-installed-warning"
              className="mt-2 p-2.5 rounded-lg bg-amber-50 dark:bg-amber-500/10 border border-amber-200 dark:border-amber-500/30 flex gap-2 text-xs text-amber-900 dark:text-amber-200 leading-relaxed"
            >
              <AlertTriangle size={13} className="shrink-0 mt-0.5" />
              <span>
                The <span className="font-mono">{runtimeDef.shortLabel}</span> CLI isn&apos;t installed
                in your runner image
                {runtimeAvailability?.image ? (
                  <>
                    {' '}(<span className="font-mono">{runtimeAvailability.image}</span>)
                  </>
                ) : null}
                , so runs with this agent will fail. Pick an installed runtime, or add the CLI to a
                custom runner image (see <span className="font-mono">apps/runner/README.md</span>).
              </span>
            </div>
          )}
        </div>

        <div>
          <div className="flex items-center justify-between gap-2 mb-1.5">
            <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300">
              {runtime === 'opencode'
                ? 'Target Model (from configured Inference backends)'
                : `${runtimeDef.shortLabel} Model & Routing`}
            </label>
            {compatibleModelBackends.length > 0 && (
              <button
                type="button"
                onClick={handleSyncAllModels}
                disabled={isSyncingModels}
                className="text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline flex items-center gap-1 cursor-pointer disabled:opacity-50"
              >
                <RefreshCw
                  size={11}
                  className={isSyncingModels ? 'animate-spin' : ''}
                />
                <span>{isSyncingModels ? 'Syncing...' : 'Sync models'}</span>
              </button>
            )}
          </div>
          {configuredModels.length > 0 ? (
            <>
            <select
              value={model}
              onChange={(e) => onModelChange(e.target.value)}
              className="w-full h-10 px-3.5 rounded-xl bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition cursor-pointer"
            >
              {builtInModelOptions.length > 0 && backendModelOptions.length > 0 ? (
                <>
                  <optgroup label={`${runtimeDef.shortLabel} Built-in Models`}>
                    {builtInModelOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </optgroup>
                  <optgroup label="Configured Inference Backends (LiteLLM Proxy)">
                    {backendModelOptions.map((opt) => (
                      <option key={opt.value} value={opt.value}>
                        {opt.label}
                      </option>
                    ))}
                  </optgroup>
                </>
              ) : configuredModels.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
              {!configuredModels.some((opt) => opt.value === model) && model && (
                <option value={model}>{model}</option>
              )}
            </select>
            {runtime !== 'opencode' && (
              <div className="mt-2.5 p-3 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-white/70 dark:bg-zinc-900/60 flex items-center justify-between gap-3">
                <div className="flex flex-col gap-0.5 min-w-0">
                  <div className="flex items-center gap-1.5 text-xs font-semibold text-zinc-800 dark:text-zinc-200">
                    {nativeProviderBackends.length > 0 ? (
                      <>
                        <Check size={13} className="text-emerald-500 shrink-0" />
                        <span>
                          Credentials connected via{' '}
                          <span className="text-teal-600 dark:text-teal-400">
                            {nativeProviderBackends[0].name}
                          </span>
                        </span>
                      </>
                    ) : (
                      <span>
                        {runtimeDef.nativeProviderLabel} credentials not registered in Backends
                      </span>
                    )}
                  </div>
                  <p className="text-xs text-zinc-500 dark:text-zinc-400 leading-relaxed">
                    {runtimeDef.authSummary}
                  </p>
                </div>
                <button
                  type="button"
                  onClick={onNavigateToBackends}
                  className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline shrink-0 cursor-pointer"
                >
                  <span>
                    {nativeProviderBackends.length > 0
                      ? 'Manage Backends'
                      : `Connect ${runtimeDef.nativeProviderLabel || 'Backend'}`}
                  </span>
                  <ArrowRight size={12} />
                </button>
              </div>
            )}
            </>
          ) : (
            <div className="p-3 rounded-xl border border-amber-500/30 bg-amber-500/5 flex items-center justify-between gap-3">
              <span className="text-xs text-zinc-600 dark:text-zinc-300">
                {runtime === 'opencode'
                  ? 'No Inference backend configured yet (e.g. LM Studio, Ollama, LiteLLM).'
                  : runtime === 'claude-code'
                    ? 'No Anthropic or LiteLLM Inference backend configured yet.'
                    : runtime === 'gemini' || runtime === 'antigravity' ? 'No Gemini or LiteLLM Inference backend configured yet.' : runtime === 'grok-build' ? 'No xAI or LiteLLM Inference backend configured yet.' : 'No OpenAI or LiteLLM Inference backend configured yet.'}
              </span>
              <button
                type="button"
                onClick={onNavigateToBackends}
                className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline shrink-0 cursor-pointer"
              >
                <span>Configure in Backends</span>
                <ArrowRight size={12} />
              </button>
            </div>
          )}
        </div>
      </div>

      {/* System Instructions */}
      <div>
        <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
          Instructions
        </label>
        <textarea
          rows={4}
          value={instructions}
          onChange={(e) => onInstructionsChange(e.target.value)}
          placeholder="Instructions and guidelines for this agent..."
          className="w-full px-3.5 py-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/90 border border-zinc-200 dark:border-zinc-700/80 text-xs font-mono leading-relaxed text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500 transition resize-y"
        />
      </div>

      {/* Collapsible Advanced Section */}
      <div className="rounded-xl border border-zinc-200 dark:border-zinc-700/80 overflow-hidden">
        <button
          type="button"
          onClick={() => setAdvancedOpen((prev) => !prev)}
          className="w-full px-4 py-3 bg-zinc-50/70 dark:bg-zinc-800/40 flex items-center justify-between text-xs font-semibold text-zinc-700 dark:text-zinc-300 hover:text-zinc-900 dark:hover:text-zinc-100 transition cursor-pointer"
        >
          <span>
            Advanced (Role Type, Skills, MCP Tools
            {restrictedBackends.length > 0 ? ', Restricted Backends' : ''})
          </span>
          <ChevronDown
            size={15}
            className={`text-zinc-400 transition-transform ${
              advancedOpen ? 'rotate-180' : ''
            }`}
          />
        </button>

        {advancedOpen && (
          <div className="p-4 flex flex-col gap-4 border-t border-zinc-200 dark:border-zinc-700/80 bg-white dark:bg-zinc-900/50">
            {/* Role Type & Dispatch Toggle */}
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
              <div>
                <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                  Role Type
                </label>
                <select
                  value={kind}
                  onChange={(e) => onKindChange(e.target.value as AgentKind)}
                  className="w-full h-9 px-3 rounded-xl bg-zinc-50 dark:bg-zinc-800 border border-zinc-200 dark:border-zinc-700/80 text-xs text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
                >
                  <option value="stage">Stage Agent (Pipeline Worker)</option>
                  <option value="assistant">System Assistant (Ctrl+K)</option>
                </select>
              </div>

              <div>
                <label className="block text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5">
                  Status
                </label>
                <button
                  type="button"
                  onClick={() => onEnabledChange(!enabled)}
                  className={`w-full h-9 px-3 rounded-xl text-xs font-semibold border transition cursor-pointer text-left flex items-center justify-between ${
                    enabled
                      ? 'bg-emerald-500/10 text-emerald-700 dark:text-emerald-300 border-emerald-500/30'
                      : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-400 border-zinc-200 dark:border-zinc-700/80'
                  }`}
                >
                  <span>{enabled ? 'Enabled' : 'Paused'}</span>
                  <span
                    className={`w-2 h-2 rounded-full ${
                      enabled ? 'bg-emerald-500' : 'bg-zinc-400'
                    }`}
                  />
                </button>
              </div>
            </div>

            {/* Skills (from defined Skills tab) */}
            <div>
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5 flex items-center gap-1.5">
                <BookOpen size={12} className="text-zinc-400" />
                <span>Skills (from defined Skills)</span>
              </label>
              {availableSkills.length > 0 ? (
                <div className="flex flex-col gap-1.5">
                  {availableSkills.map((sk) => {
                    const isSelected =
                      selectedSkillIds.includes(sk.id) ||
                      selectedSkillIds.includes(sk.slug)
                    return (
                      <button
                        key={sk.id}
                        type="button"
                        onClick={() => onToggleSkill(sk.id)}
                        className={`p-2.5 rounded-xl border text-left transition cursor-pointer flex items-center justify-between gap-3 ${
                          isSelected
                            ? 'border-teal-500/50 bg-teal-500/10'
                            : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/40'
                        }`}
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2">
                            <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                              {sk.name}
                            </span>
                            <span className="font-mono text-xs text-zinc-400">
                              {sk.slug}
                            </span>
                          </div>
                          {sk.description && (
                            <p className="text-xs text-zinc-500 dark:text-zinc-400 truncate mt-0.5">
                              {sk.description}
                            </p>
                          )}
                        </div>
                        <div
                          className={`w-4 h-4 rounded-md border flex items-center justify-center shrink-0 ${
                            isSelected
                              ? 'bg-teal-600 border-teal-600 text-white'
                              : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700/80'
                          }`}
                        >
                          {isSelected && <Check size={11} />}
                        </div>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="p-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/30 flex items-center justify-between gap-3">
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    No skills defined yet.
                  </span>
                  {onNavigateToSkills && (
                    <button
                      type="button"
                      onClick={onNavigateToSkills}
                      className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline shrink-0 cursor-pointer"
                    >
                      <span>Create in Skills tab</span>
                      <ArrowRight size={12} />
                    </button>
                  )}
                </div>
              )}
            </div>

            {/* MCP Tool Backends (strictly from configured Tools backends) */}
            <div>
              <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5 flex items-center gap-1.5">
                <Wrench size={12} className="text-zinc-400" />
                <span>MCP Tools (from configured Tools backends)</span>
              </label>
              {toolBackends.length > 0 ? (
                <div className="flex flex-col gap-1.5">
                  {toolBackends.map((tb) => {
                    const isSelected =
                      tools.includes(tb.id) || tools.includes(tb.name)
                    return (
                      <button
                        key={tb.id}
                        type="button"
                        onClick={() => onToggleToolBackend(tb.id)}
                        className={`p-2.5 rounded-xl border text-left transition cursor-pointer flex items-center justify-between gap-3 ${
                          isSelected
                            ? 'border-teal-500/50 bg-teal-500/10'
                            : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/40'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <ProviderIcon provider={tb.provider} type={tb.type} size={15} />
                          <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                            {tb.name}
                          </span>
                          <span className="text-xs text-zinc-400">
                            ({formatProviderName(tb.provider)})
                          </span>
                        </div>
                        <div
                          className={`w-4 h-4 rounded-md border flex items-center justify-center shrink-0 ${
                            isSelected
                              ? 'bg-teal-600 border-teal-600 text-white'
                              : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700/80'
                          }`}
                        >
                          {isSelected && <Check size={11} />}
                        </div>
                      </button>
                    )
                  })}
                </div>
              ) : (
                <div className="p-2.5 rounded-xl border border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/30 flex items-center justify-between gap-3">
                  <span className="text-xs text-zinc-500 dark:text-zinc-400">
                    No Tools backends connected yet.
                  </span>
                  <button
                    type="button"
                    onClick={onNavigateToBackends}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:underline shrink-0 cursor-pointer"
                  >
                    <span>Connect in Backends</span>
                    <ArrowRight size={12} />
                  </button>
                </div>
              )}
            </div>

            {/* Restricted Backends (only shown when restricted backends exist) */}
            {restrictedBackends.length > 0 && (
              <div>
                <label className="text-xs font-semibold text-zinc-700 dark:text-zinc-300 mb-1.5 flex items-center gap-1.5">
                  <Lock size={12} className="text-amber-500" />
                  <span>Restricted Backends Access</span>
                </label>
                <div className="flex flex-col gap-1.5">
                  {restrictedBackends.map((b) => {
                    const isGranted = selectedBackendIds.includes(b.id)
                    return (
                      <button
                        key={b.id}
                        type="button"
                        onClick={() => onToggleRestrictedBackend(b.id)}
                        className={`p-2.5 rounded-xl border text-left transition cursor-pointer flex items-center justify-between gap-3 ${
                          isGranted
                            ? 'border-teal-500/50 bg-teal-500/10'
                            : 'border-zinc-200 dark:border-zinc-700/80 bg-zinc-50/60 dark:bg-zinc-800/40'
                        }`}
                      >
                        <div className="flex items-center gap-2 min-w-0">
                          <ProviderIcon provider={b.provider} type={b.type} size={15} />
                          <span className="text-xs font-medium text-zinc-900 dark:text-zinc-100 truncate">
                            {b.name}
                          </span>
                          <span className="text-xs text-zinc-400">
                            ({formatProviderName(b.provider)})
                          </span>
                        </div>
                        <div
                          className={`w-4 h-4 rounded-md border flex items-center justify-center shrink-0 ${
                            isGranted
                              ? 'bg-teal-600 border-teal-600 text-white'
                              : 'bg-white dark:bg-zinc-900 border-zinc-200 dark:border-zinc-700/80'
                          }`}
                        >
                          {isGranted && <Check size={11} />}
                        </div>
                      </button>
                    )
                  })}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
