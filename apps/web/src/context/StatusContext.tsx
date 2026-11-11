import React, { createContext, useCallback, useContext, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ApiError, api } from '../api/client'
import { queryKeys } from '../api/queryClient'

export type ControlPlaneStatusState = 'online' | 'degraded' | 'offline' | 'checking'

export interface ControlPlaneHealth {
  status: ControlPlaneStatusState
  service: string
  version: string
  environment: string
  database: string
  redis: string
  latencyMs: number | null
  lastChecked: Date | null
  error: string | null
}

interface StatusContextType {
  health: ControlPlaneHealth
  isChecking: boolean
  isStatusModalOpen: boolean
  openStatusModal: () => void
  closeStatusModal: () => void
  checkHealth: () => Promise<void>
}

const initialHealth: ControlPlaneHealth = {
  status: 'checking',
  service: 'GitSeed Control Plane',
  version: '0.1.0',
  environment: 'development',
  database: 'checking...',
  redis: 'checking...',
  latencyMs: null,
  lastChecked: null,
  error: null,
}

const StatusContext = createContext<StatusContextType | undefined>(undefined)

const RETRY_DELAYS_MS = [1000, 2000, 5000]

async function fetchControlPlaneHealth(): Promise<ControlPlaneHealth> {
  const startTime = performance.now()
  const data = await api.system.health(4000)
  const latencyMs = Math.max(1, Math.round(performance.now() - startTime))
  return {
    status: data.status === 'ok' ? 'online' : 'degraded',
    service: data.service || 'GitSeed Control Plane',
    version: data.version || '0.1.0',
    environment: data.environment || 'development',
    database: data.database || 'connected',
    redis: data.redis || 'connected',
    latencyMs,
    lastChecked: new Date(),
    error: null,
  }
}

export const StatusProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [isStatusModalOpen, setIsStatusModalOpen] = useState<boolean>(false)

  const {
    data,
    error,
    failureCount,
    isError,
    isFetching: isChecking,
    refetch,
  } = useQuery({
    queryKey: queryKeys.health,
    queryFn: fetchControlPlaneHealth,
    refetchInterval: 15_000,
    staleTime: 10_000,
    retry: RETRY_DELAYS_MS.length,
    retryDelay: (attemptIndex) =>
      RETRY_DELAYS_MS[attemptIndex] ?? RETRY_DELAYS_MS[RETRY_DELAYS_MS.length - 1],
  })

  const health: ControlPlaneHealth = React.useMemo(() => {
    if (failureCount > 0 && isChecking) {
      return {
        ...(data || initialHealth),
        status: 'checking',
      }
    }
    if (isError) {
      const errorMsg =
        error instanceof ApiError
          ? error.detail
          : error instanceof Error
            ? error.message
            : 'Failed to reach /healthz'
      return {
        ...(data || initialHealth),
        status: 'offline',
        database: 'unreachable',
        redis: 'unreachable',
        latencyMs: null,
        lastChecked: new Date(),
        error: errorMsg,
      }
    }
    return data || initialHealth
  }, [data, error, failureCount, isChecking, isError])

  const checkHealth = useCallback(async () => {
    await refetch()
  }, [refetch])

  return (
    <StatusContext.Provider
      value={{
        health,
        isChecking,
        isStatusModalOpen,
        openStatusModal: () => setIsStatusModalOpen(true),
        closeStatusModal: () => setIsStatusModalOpen(false),
        checkHealth,
      }}
    >
      {children}
    </StatusContext.Provider>
  )
}

export const useControlPlaneStatus = () => {
  const context = useContext(StatusContext)
  if (!context) {
    throw new Error('useControlPlaneStatus must be used within a StatusProvider')
  }
  return context
}
