import React, { createContext, useCallback, useContext, useEffect, useState } from 'react'
import { ApiError, api, setOnUnauthorized } from '../api/client'
import { queryClient } from '../api/queryClient'
import type { User, UserPreferences } from '../types'

interface AuthContextType {
  user: User | null
  isAuthenticated: boolean
  isLoading: boolean
  authError: string | null
  clearAuthError: () => void
  login: (email: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  refreshUser: () => Promise<User | null>
  updatePreferences: (prefs: Partial<UserPreferences>) => Promise<void>
}

const AuthContext = createContext<AuthContextType | undefined>(undefined)

export const AuthProvider: React.FC<{
  children?: React.ReactNode
  initialUser?: User | null
}> = ({ children, initialUser = null }) => {
  const [user, setUser] = useState<User | null>(initialUser)
  const [isLoading, setIsLoading] = useState(initialUser === null)
  const [authError, setAuthError] = useState<string | null>(null)

  const clearAuthError = useCallback(() => {
    setAuthError(null)
  }, [])

  useEffect(() => {
    setOnUnauthorized(() => {
      setUser(null)
      queryClient.clear()
      window.history.replaceState(null, '', '/login')
    })
    return () => setOnUnauthorized(null)
  }, [])

  const refreshUser = useCallback(async (): Promise<User | null> => {
    try {
      const me = await api.auth.me()
      setUser(me)
      return me
    } catch {
      setUser(null)
      return null
    }
  }, [])

  useEffect(() => {
    let mounted = true
    refreshUser().finally(() => {
      if (mounted) setIsLoading(false)
    })
    return () => {
      mounted = false
    }
  }, [refreshUser])

  const login = async (email: string, password: string): Promise<boolean> => {
    setAuthError(null)
    try {
      const authenticatedUser = await api.auth.login(email, password)
      setUser(authenticatedUser)
      return true
    } catch (err) {
      if (err instanceof ApiError) {
        setAuthError(
          err.status === 0
            ? 'Unable to reach GitSeed Control Plane API.'
            : err.detail || 'Authentication failed. Please check your credentials.'
        )
      } else {
        setAuthError('Unable to reach GitSeed Control Plane API.')
      }
      return false
    }
  }

  const logout = async () => {
    try {
      await api.auth.logout()
    } catch {
      // ignore network error on logout
    } finally {
      setUser(null)
      setAuthError(null)
      queryClient.clear()
      window.history.replaceState(null, '', '/login')
    }
  }

  const updatePreferences = useCallback(
    async (prefs: Partial<UserPreferences>): Promise<void> => {
      if (!user) return
      const nextPreferences: UserPreferences = {
        ...user.preferences,
        ...prefs,
      }
      setUser((prev) => (prev ? { ...prev, preferences: nextPreferences } : prev))
      try {
        const updated = await api.users.update(user.id, { preferences: nextPreferences })
        setUser(updated)
      } catch {
        // Keep optimistic preference state if network is temporarily unreachable
      }
    },
    [user]
  )

  return (
    <AuthContext.Provider
      value={{
        user,
        isAuthenticated: !!user,
        isLoading,
        authError,
        clearAuthError,
        login,
        logout,
        refreshUser,
        updatePreferences,
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export const useAuth = () => {
  const context = useContext(AuthContext)
  if (!context) throw new Error('useAuth must be used within AuthProvider')
  return context
}
