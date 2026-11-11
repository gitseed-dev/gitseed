import React, { useEffect, useMemo, useRef, useState } from 'react'
import {
  Check,
  Cpu,
  Plus,
  RefreshCw,
  Search,
} from 'lucide-react'
import { Button } from '@heroui/react'
import type { ModelBackendEntry } from '../../../utils/backends'

export interface BackendModelsTabProps {
  isEditing: boolean
  provider: string
  models: ModelBackendEntry[]
  onChangeModels: (models: ModelBackendEntry[]) => void
  isSyncing: boolean
  onSyncFromProvider?: () => Promise<void> | void
}

export const BackendModelsTab: React.FC<BackendModelsTabProps> = ({
  isEditing,
  provider,
  models,
  onChangeModels,
  isSyncing,
  onSyncFromProvider,
}) => {
  const [search, setSearch] = useState('')
  const [justSynced, setJustSynced] = useState(false)
  const [showCustomInput, setShowCustomInput] = useState(false)
  const [customModelId, setCustomModelId] = useState('')
  const autoSyncedRef = useRef(false)

  const handleSyncClick = async () => {
    if (!onSyncFromProvider) return
    await onSyncFromProvider()
    setJustSynced(true)
    setTimeout(() => setJustSynced(false), 2000)
  }

  useEffect(() => {
    if (
      isEditing &&
      models.length === 0 &&
      !isSyncing &&
      onSyncFromProvider &&
      !autoSyncedRef.current
    ) {
      autoSyncedRef.current = true
      onSyncFromProvider()
    }
  }, [isEditing, models.length, isSyncing, onSyncFromProvider])

  const enabledCount = useMemo(
    () => models.filter((m) => m.enabled).length,
    [models]
  )

  const filteredModels = useMemo(() => {
    const q = search.trim().toLowerCase()
    const list = q
      ? models.filter(
          (m) =>
            m.id.toLowerCase().includes(q) ||
            (m.owned_by || '').toLowerCase().includes(q)
        )
      : [...models]
    return list.sort((a, b) =>
      a.id.localeCompare(b.id, undefined, { sensitivity: 'base' })
    )
  }, [models, search])

  const handleToggleModel = (modelId: string) => {
    onChangeModels(
      models.map((m) =>
        m.id.toLowerCase() === modelId.toLowerCase()
          ? { ...m, enabled: !m.enabled }
          : m
      )
    )
  }

  const handleSetAllEnabled = (enabled: boolean) => {
    onChangeModels(models.map((m) => ({ ...m, enabled })))
  }

  const handleAddCustomModel = () => {
    const trimmed = customModelId.trim()
    if (!trimmed) return
    if (models.some((m) => m.id.toLowerCase() === trimmed.toLowerCase())) {
      setCustomModelId('')
      setShowCustomInput(false)
      return
    }
    const next = [
      ...models,
      { id: trimmed, owned_by: provider, enabled: true },
    ].sort((a, b) =>
      a.id.localeCompare(b.id, undefined, { sensitivity: 'base' })
    )
    onChangeModels(next)
    setCustomModelId('')
    setShowCustomInput(false)
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <h4 className="text-xs font-bold uppercase tracking-wider text-zinc-700 dark:text-zinc-300">
            Available Models ({enabledCount}/{models.length} enabled)
          </h4>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
            Enabled models appear in the Target Model selector when configuring stage and utility agents.
          </p>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          {!showCustomInput && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={() => setShowCustomInput(true)}
              className="text-xs font-semibold rounded-xl flex items-center gap-1 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
            >
              <Plus size={13} />
              <span>Add ID</span>
            </Button>
          )}
          {isEditing && onSyncFromProvider && (
            <Button
              type="button"
              variant="secondary"
              size="sm"
              onClick={handleSyncClick}
              isDisabled={isSyncing}
              className="text-xs font-semibold rounded-xl flex items-center gap-1.5 shrink-0 cursor-pointer border border-zinc-200 dark:border-zinc-700/80"
            >
              {justSynced && !isSyncing ? (
                <>
                  <Check size={13} className="text-emerald-500" />
                  <span>Synced</span>
                </>
              ) : (
                <>
                  <RefreshCw
                    size={13}
                    className={isSyncing ? 'animate-spin' : ''}
                  />
                  <span>{isSyncing ? 'Syncing...' : 'Sync from Provider'}</span>
                </>
              )}
            </Button>
          )}
        </div>
      </div>

      {showCustomInput && (
        <div className="flex items-center gap-2 p-2.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/50 border border-zinc-200 dark:border-zinc-700/80">
          <input
            type="text"
            value={customModelId}
            onChange={(e) => setCustomModelId(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') {
                e.preventDefault()
                handleAddCustomModel()
              }
            }}
            placeholder="e.g. qwen2.5-coder-32b-instruct or claude-3-7-sonnet-latest"
            autoFocus
            className="flex-1 px-3 py-1.5 rounded-lg text-xs font-mono bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:border-teal-500"
          />
          <Button
            type="button"
            variant="primary"
            size="sm"
            onClick={handleAddCustomModel}
            isDisabled={!customModelId.trim()}
            className="rounded-lg text-xs font-semibold cursor-pointer"
          >
            Add
          </Button>
          <Button
            type="button"
            variant="secondary"
            size="sm"
            onClick={() => {
              setShowCustomInput(false)
              setCustomModelId('')
            }}
            className="rounded-lg text-xs font-semibold cursor-pointer"
          >
            Cancel
          </Button>
        </div>
      )}

      {models.length > 0 && (
        <div className="flex items-center justify-between gap-2">
          <div className="relative flex-1">
            <Search
              size={14}
              className="absolute left-3 top-1/2 -translate-y-1/2 text-zinc-400"
            />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Filter models..."
              className="w-full pl-8 pr-3 py-1.5 rounded-xl text-xs bg-zinc-100/80 dark:bg-zinc-800/80 border border-zinc-200 dark:border-zinc-700/80 text-zinc-900 dark:text-zinc-100 focus:outline-none focus:ring-2 focus:ring-teal-500/30"
            />
          </div>
          <div className="flex items-center gap-2 text-xs shrink-0">
            <button
              type="button"
              onClick={() => handleSetAllEnabled(true)}
              className="font-semibold text-teal-600 dark:text-teal-400 hover:underline cursor-pointer"
            >
              Enable all
            </button>
            <span className="text-zinc-300 dark:text-zinc-700">·</span>
            <button
              type="button"
              onClick={() => handleSetAllEnabled(false)}
              className="font-semibold text-zinc-500 hover:text-zinc-800 dark:hover:text-zinc-200 cursor-pointer"
            >
              Disable all
            </button>
          </div>
        </div>
      )}

      {models.length === 0 ? (
        <div className="py-10 px-4 rounded-xl border border-dashed border-zinc-200 dark:border-zinc-700/80 flex flex-col items-center justify-center text-center gap-2">
          <div className="w-9 h-9 rounded-xl bg-teal-500/10 text-teal-600 dark:text-teal-400 flex items-center justify-center">
            <Cpu size={18} />
          </div>
          <div className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
            {isSyncing ? 'Discovering models...' : 'No models discovered yet'}
          </div>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 max-w-sm">
            {isEditing
              ? 'Click "Sync from Provider" to query your model server (LM Studio, Ollama, OpenAI, Anthropic, LiteLLM, or vLLM) for available models.'
              : 'Models will be discovered automatically once you register this Inference backend.'}
          </p>
        </div>
      ) : filteredModels.length === 0 ? (
        <div className="py-6 text-center text-xs text-zinc-500 dark:text-zinc-400">
          No models match &ldquo;{search}&rdquo;
        </div>
      ) : (
        <div className="flex flex-col divide-y divide-zinc-200/70 dark:divide-zinc-700/60 rounded-xl border border-zinc-200 dark:border-zinc-700/80 overflow-hidden">
          {filteredModels.map((model) => (
            <div
              key={model.id}
              className={`px-3.5 py-2.5 flex items-center justify-between gap-3 transition-colors ${
                model.enabled
                  ? 'bg-white dark:bg-zinc-900/60'
                  : 'bg-zinc-50/70 dark:bg-zinc-800/30 opacity-65'
              }`}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <Cpu
                  size={15}
                  className={
                    model.enabled
                      ? 'text-teal-600 dark:text-teal-400 shrink-0'
                      : 'text-zinc-400 shrink-0'
                  }
                />
                <div className="min-w-0">
                  <div className="text-sm font-mono font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                    {model.id}
                  </div>
                  {model.owned_by && (
                    <div className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5 font-mono">
                      {model.owned_by}
                    </div>
                  )}
                </div>
              </div>

              <button
                type="button"
                role="switch"
                aria-checked={model.enabled}
                onClick={() => handleToggleModel(model.id)}
                className={`relative inline-flex h-5 w-9 shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none ${
                  model.enabled
                    ? 'bg-teal-600 dark:bg-teal-500'
                    : 'bg-zinc-300 dark:bg-zinc-700'
                }`}
              >
                <span
                  className={`pointer-events-none inline-block h-4 w-4 transform rounded-full bg-white shadow-xs transition duration-200 ease-in-out ${
                    model.enabled ? 'translate-x-4' : 'translate-x-0'
                  }`}
                />
              </button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

