import React from 'react'
import type { BackendType } from '../../../types'

export interface BackendCapacityTabProps {
  type: BackendType
  capacity: number | null
  setCapacity: (c: number | null) => void
}

export const BackendCapacityTab: React.FC<BackendCapacityTabProps> = ({
  type,
  capacity,
  setCapacity,
}) => {
  const effectiveCap = capacity ?? (type === 'compute' ? 2 : 4)

  return (
    <div className="flex flex-col gap-5">
      <div className="flex flex-col gap-1">
        <span className="text-sm font-semibold text-zinc-800 dark:text-zinc-200">
          Capacity & Concurrency Limits
        </span>
        <p className="text-xs text-zinc-500 dark:text-zinc-400">
          {type === 'compute'
            ? 'Control how many agent runs can execute in parallel on this runner. Host CPU and memory allocations are managed directly by your Docker Engine or Kubernetes cluster.'
            : 'Control how many concurrent inference requests can be dispatched to this model provider at once.'}
        </p>
      </div>

      {/* Max Concurrency Stepper */}
      <div className="flex items-center justify-between p-3.5 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200 dark:border-zinc-700/80">
        <div>
          <span className="text-sm font-semibold text-zinc-900 dark:text-zinc-100">
            {type === 'compute' ? 'Max Concurrent Runs' : 'Max Concurrent Requests'}
          </span>
          <p className="text-xs text-zinc-500 dark:text-zinc-400 mt-0.5">
            Simultaneous active slots permitted on this backend
          </p>
        </div>
        <div className="flex items-center border border-zinc-200 dark:border-zinc-700/80 rounded-xl bg-white dark:bg-zinc-800 overflow-hidden shadow-xs">
          <button
            type="button"
            onClick={() => setCapacity(Math.max(1, effectiveCap - 1))}
            className="px-3 py-1.5 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors text-sm font-bold cursor-pointer"
          >
            -
          </button>
          <span className="px-3.5 py-1.5 font-mono text-sm font-bold text-zinc-900 dark:text-zinc-100 min-w-[36px] text-center select-none">
            {effectiveCap}
          </span>
          <button
            type="button"
            onClick={() => setCapacity(Math.min(128, effectiveCap + 1))}
            className="px-3 py-1.5 text-zinc-500 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-700 transition-colors text-sm font-bold cursor-pointer"
          >
            +
          </button>
        </div>
      </div>
    </div>
  )
}
