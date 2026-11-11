import { useEffect, useMemo, useState } from 'react'
import type { PaginationProps } from '../components/common/Pagination'

export interface UsePaginationOptions<T> {
  items: T[]
  initialPageSize?: number
  initialPage?: number
  pageSizeOptions?: number[]
}

export interface UsePaginationReturn<T> {
  page: number
  setPage: (page: number) => void
  pageSize: number
  setPageSize: (size: number) => void
  resetPage: () => void
  paginatedItems: T[]
  totalPages: number
  totalItems: number
  startIndex: number
  endIndex: number
  paginationProps: PaginationProps
}

export function usePagination<T>({
  items,
  initialPageSize = 25,
  initialPage = 1,
  pageSizeOptions = [25, 50, 100],
}: UsePaginationOptions<T>): UsePaginationReturn<T> {
  const [pageSize, setPageSizeState] = useState<number>(initialPageSize)
  const [page, setPage] = useState<number>(initialPage)

  const totalItems = items.length
  const totalPages = Math.max(1, Math.ceil(totalItems / pageSize))

  useEffect(() => {
    if (page > totalPages) {
      setPage(totalPages)
    }
  }, [page, totalPages])

  const paginatedItems = useMemo(() => {
    const start = (page - 1) * pageSize
    return items.slice(start, start + pageSize)
  }, [items, page, pageSize])

  const startIndex = totalItems === 0 ? 0 : (page - 1) * pageSize + 1
  const endIndex = Math.min(totalItems, page * pageSize)

  const handlePageSizeChange = (newSize: number) => {
    setPageSizeState(newSize)
    setPage(1)
  }

  const resetPage = () => {
    setPage(1)
  }

  const paginationProps: PaginationProps = {
    page,
    pageSize,
    totalItems,
    onPageChange: setPage,
    onPageSizeChange: handlePageSizeChange,
    pageSizeOptions,
  }

  return {
    page,
    setPage,
    pageSize,
    setPageSize: handlePageSizeChange,
    resetPage,
    paginatedItems,
    totalPages,
    totalItems,
    startIndex,
    endIndex,
    paginationProps,
  }
}

