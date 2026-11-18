import React, { useMemo, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { GitBranch, Link2, Loader2, Plus, Search, Trash2, X } from 'lucide-react'
import { Card } from '@heroui/react'
import { api } from '../../../api/client'
import { VIEWS } from '../../../constants'
import { useData } from '../../../context/DataContext'
import { getSeedStatusClasses, STATUS_LABELS } from '../../../utils/seeds'
import type { Seed, SeedLinkItem } from '../../../types'

const RELATION_LABELS: Record<SeedLinkItem['relation'], string | null> = {
  related: null,
  follow_up_of: 'Follow-up of',
  followed_up_by: 'Follow-up',
}

/**
 * Seeds linked to this seed (two-way "related" or directional "follow-up").
 * Agents receive a compact pointer in the stage prompt and full context in
 * `/workspace/.gitseed/related/seed-<number>.md`.
 */
export const SeedRelatedCard: React.FC<{
  seed: Seed
  canContribute: boolean
  onNewRelatedSeed?: () => void
}> = ({ seed, canContribute, onNewRelatedSeed }) => {
  const queryClient = useQueryClient()
  const { allSeeds, navigateToDetail } = useData()
  const [isLinking, setIsLinking] = useState(false)
  const [search, setSearch] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const queryKey = ['seedLinks', seed.id]
  const { data: links = [], isLoading } = useQuery({
    queryKey,
    queryFn: () => api.seeds.listLinks(seed.id),
  })

  const linkedIds = useMemo(
    () => new Set([seed.id, ...links.map((l) => l.seed_id)]),
    [seed.id, links]
  )

  const candidates = useMemo(() => {
    const q = search.trim().toLowerCase()
    return allSeeds
      .filter((s) => s.project_id === seed.project_id && !linkedIds.has(s.id))
      .filter((s) => {
        if (!q) return true
        return (
          `#${s.number}`.includes(q) ||
          String(s.number) === q ||
          s.title.toLowerCase().includes(q)
        )
      })
      .sort((a, b) => b.number - a.number)
      .slice(0, 8)
  }, [allSeeds, seed.project_id, linkedIds, search])

  const handleLinkSeed = async (targetSeedId: string) => {
    setBusy(true)
    setError(null)
    try {
      await api.seeds.addLink(seed.id, { target_seed_id: targetSeedId, kind: 'related' })
      setIsLinking(false)
      setSearch('')
      await Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: ['seedLinks', targetSeedId] }),
      ])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not link seed')
    } finally {
      setBusy(false)
    }
  }

  const handleUnlink = async (link: SeedLinkItem) => {
    setBusy(true)
    setError(null)
    try {
      await api.seeds.deleteLink(seed.id, link.id)
      await Promise.all([
        queryClient.invalidateQueries({ queryKey }),
        queryClient.invalidateQueries({ queryKey: ['seedLinks', link.seed_id] }),
      ])
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not remove link')
    } finally {
      setBusy(false)
    }
  }

  if (!isLoading && links.length === 0 && !canContribute) return null

  return (
    <Card className="p-5 sm:p-6 min-w-0 overflow-hidden rounded-2xl border border-zinc-200 dark:border-zinc-700/80 bg-white/88 dark:bg-zinc-900/88 backdrop-blur-xl shadow-xs flex flex-col gap-3">
      <div className="flex items-center justify-between pb-2.5 border-b border-zinc-100 dark:border-zinc-800">
        <h3 className="font-heading font-bold text-sm sm:text-base text-zinc-900 dark:text-zinc-100 flex items-center gap-2">
          Related Seeds
          {links.length > 0 && (
            <span className="text-xs font-semibold px-2 py-0.5 rounded-full font-mono bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border border-zinc-200 dark:border-zinc-700">
              {links.length}
            </span>
          )}
        </h3>
        {canContribute && (
          <div className="flex items-center gap-2.5">
            <button
              type="button"
              disabled={busy}
              onClick={() => {
                setIsLinking((prev) => !prev)
                setError(null)
              }}
              className="inline-flex items-center gap-1 text-xs font-semibold text-zinc-500 dark:text-zinc-400 hover:text-teal-600 dark:hover:text-teal-400 cursor-pointer disabled:opacity-50"
              title="Link an existing seed in this project"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <Link2 size={13} />}
              <span>Link</span>
            </button>
            {onNewRelatedSeed && (
              <button
                type="button"
                disabled={busy}
                onClick={onNewRelatedSeed}
                className="inline-flex items-center gap-1 text-xs font-semibold text-teal-600 dark:text-teal-400 hover:text-teal-500 cursor-pointer disabled:opacity-50"
                title="Create a new follow-up seed linked to this one"
              >
                <Plus size={13} />
                <span>New related</span>
              </button>
            )}
          </div>
        )}
      </div>

      {isLinking && canContribute && (
        <div className="flex flex-col gap-2 p-2.5 rounded-xl bg-zinc-50/90 dark:bg-zinc-800/50 border border-zinc-200/80 dark:border-zinc-700/80">
          <div className="relative flex items-center">
            <Search size={13} className="absolute left-2.5 text-zinc-400 pointer-events-none" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search seeds by #number or title…"
              autoFocus
              className="w-full h-8 pl-8 pr-7 text-xs rounded-lg bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700 text-zinc-900 dark:text-zinc-100 placeholder-zinc-400 focus:outline-none focus:border-teal-500"
            />
            <button
              type="button"
              onClick={() => {
                setIsLinking(false)
                setSearch('')
              }}
              className="absolute right-2 text-zinc-400 hover:text-zinc-600 dark:hover:text-zinc-200 cursor-pointer"
              aria-label="Close seed picker"
            >
              <X size={13} />
            </button>
          </div>
          {candidates.length === 0 ? (
            <p className="text-xs text-zinc-400 px-1 py-1">No matching seeds in this project.</p>
          ) : (
            <ul className="flex flex-col divide-y divide-zinc-200/60 dark:divide-zinc-700/50 max-h-44 overflow-y-auto">
              {candidates.map((c) => (
                <li key={c.id}>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleLinkSeed(c.id)}
                    className="w-full px-2 py-1.5 text-left flex items-center justify-between gap-2 hover:bg-zinc-100 dark:hover:bg-zinc-800 rounded-lg transition cursor-pointer disabled:opacity-50"
                  >
                    <span className="text-xs text-zinc-800 dark:text-zinc-200 truncate">
                      <span className="font-mono font-semibold text-zinc-500 dark:text-zinc-400 mr-1.5">
                        #{c.number}
                      </span>
                      {c.title}
                    </span>
                    <span
                      className={`shrink-0 px-1.5 py-0.5 rounded border text-xs font-medium ${getSeedStatusClasses(
                        c.status
                      )}`}
                    >
                      {STATUS_LABELS[c.status]}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {isLoading ? (
        <div className="text-xs text-zinc-400">Loading…</div>
      ) : links.length === 0 ? (
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          Link related seeds or create a follow-up seed so agents can inspect prior specs and plans when needed.
        </p>
      ) : (
        <ul className="flex flex-col gap-1.5">
          {links.map((item) => {
            const relLabel = RELATION_LABELS[item.relation]
            return (
              <li
                key={item.id}
                className="group flex items-start justify-between gap-2 min-w-0 py-1"
              >
                <button
                  type="button"
                  onClick={() => navigateToDetail(VIEWS.SEEDS, item.seed_id)}
                  className="min-w-0 flex-1 text-left cursor-pointer"
                >
                  <div className="flex items-center gap-1.5 min-w-0">
                    <GitBranch size={13} className="text-teal-500 shrink-0" />
                    <span className="font-mono text-xs font-bold text-zinc-600 dark:text-zinc-300 shrink-0">
                      #{item.number}
                    </span>
                    <span className="text-sm font-medium text-zinc-800 dark:text-zinc-200 group-hover:text-teal-600 dark:group-hover:text-teal-400 truncate">
                      {item.title}
                    </span>
                  </div>
                  <div className="mt-0.5 pl-5 flex items-center gap-1.5 flex-wrap">
                    {relLabel && (
                      <span className="text-xs text-zinc-400">{relLabel} ·</span>
                    )}
                    <span
                      className={`inline-flex items-center px-1.5 py-0.2 rounded border text-xs font-medium ${getSeedStatusClasses(
                        item.status
                      )}`}
                    >
                      {STATUS_LABELS[item.status] || item.status}
                    </span>
                  </div>
                </button>
                {canContribute && (
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => void handleUnlink(item)}
                    title={`Unlink #${item.number}`}
                    className="p-1 rounded-md text-zinc-400 hover:text-rose-500 hover:bg-rose-500/10 opacity-0 group-hover:opacity-100 transition cursor-pointer shrink-0"
                  >
                    <Trash2 size={13} />
                  </button>
                )}
              </li>
            )
          })}
        </ul>
      )}

      {error && <p className="text-xs text-rose-600 dark:text-rose-400">{error}</p>}
    </Card>
  )
}

