import { useMemo, useState } from 'react'

export type SortDirection = 'asc' | 'desc'

export interface SortConfig<K extends string = string> {
  key: K
  direction: SortDirection
}

export interface UseTableSortOptions<T, K extends string> {
  items: T[]
  initialKey: K
  initialDirection?: SortDirection
  customComparators?: Partial<Record<K, (a: T, b: T) => number>>
}

export function useTableSort<T, K extends string>({
  items,
  initialKey,
  initialDirection = 'asc',
  customComparators,
}: UseTableSortOptions<T, K>) {
  const [sortConfig, setSortConfig] = useState<SortConfig<K>>({
    key: initialKey,
    direction: initialDirection,
  })

  const requestSort = (key: K) => {
    setSortConfig((prev) => {
      if (prev.key === key) {
        return {
          key,
          direction: prev.direction === 'asc' ? 'desc' : 'asc',
        }
      }
      return {
        key,
        direction: 'asc',
      }
    })
  }

  const sortedItems = useMemo(() => {
    if (!sortConfig.key) return items

    const sorted = [...items]
    const customComp = customComparators?.[sortConfig.key]

    sorted.sort((a, b) => {
      let comparison = 0
      if (customComp) {
        comparison = customComp(a, b)
      } else {
        const recA = a as Record<string, unknown>
        const recB = b as Record<string, unknown>
        const valA = recA[sortConfig.key]
        const valB = recB[sortConfig.key]

        if (valA === undefined || valA === null) comparison = 1
        else if (valB === undefined || valB === null) comparison = -1
        else if (typeof valA === 'number' && typeof valB === 'number') {
          comparison = valA - valB
        } else if (typeof valA === 'boolean' && typeof valB === 'boolean') {
          comparison = valA === valB ? 0 : valA ? -1 : 1
        } else {
          comparison = String(valA).localeCompare(String(valB), undefined, {
            numeric: true,
            sensitivity: 'base',
          })
        }
      }

      return sortConfig.direction === 'asc' ? comparison : -comparison
    })

    return sorted
  }, [items, sortConfig, customComparators])

  return {
    sortedItems,
    sortConfig,
    requestSort,
    setSortConfig,
  }
}

