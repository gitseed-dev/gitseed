import React, { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react'
import { useAuth } from './AuthContext'

export type ThemeMode = 'dark' | 'light' | 'system'
export type ResolvedTheme = 'dark' | 'light'

interface ThemeContextType {
  theme: ResolvedTheme
  themeMode: ThemeMode
  orgDefaultTheme: ThemeMode
  userThemeOverride: ThemeMode | null
  setOrgDefaultTheme: (mode: ThemeMode) => void
  setThemeMode: (mode: ThemeMode) => void
  clearThemeOverride: () => void
  toggleTheme: () => void
  ambientBg: boolean
  orgDefaultAmbientBg: boolean
  userAmbientOverride: boolean | null
  setOrgDefaultAmbientBg: (enabled: boolean) => void
  setAmbientBg: (enabled: boolean) => void
  clearAmbientOverride: () => void
  clearAppearanceOverrides: () => void
  toggleAmbientBg: () => void
}

const ThemeContext = createContext<ThemeContextType | undefined>(undefined)

const ORG_THEME_STORAGE_KEY = 'gitseed_org_default_theme'
const USER_OVERRIDE_STORAGE_KEY = 'gitseed_user_theme_override'
const ORG_AMBIENT_STORAGE_KEY = 'gitseed_org_default_ambient_bg'
const USER_AMBIENT_OVERRIDE_STORAGE_KEY = 'gitseed_user_ambient_override'

const getSystemTheme = (): ResolvedTheme => {
  if (typeof window !== 'undefined' && window.matchMedia) {
    return window.matchMedia('(prefers-color-scheme: light)').matches ? 'light' : 'dark'
  }
  return 'dark'
}

const isValidThemeMode = (val: unknown): val is ThemeMode =>
  val === 'light' || val === 'dark' || val === 'system'

export const ThemeProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { user, updatePreferences } = useAuth()
  const hydratedUserIdRef = useRef<string | null>(null)

  const [orgDefaultTheme, setOrgDefaultThemeState] = useState<ThemeMode>(() => {
    const saved = localStorage.getItem(ORG_THEME_STORAGE_KEY)
    if (isValidThemeMode(saved)) return saved
    return 'system'
  })

  const [userThemeOverride, setUserThemeOverride] = useState<ThemeMode | null>(() => {
    const saved = localStorage.getItem(USER_OVERRIDE_STORAGE_KEY)
    if (isValidThemeMode(saved)) return saved
    return null
  })

  const [systemTheme, setSystemTheme] = useState<ResolvedTheme>(() => getSystemTheme())

  const [orgDefaultAmbientBg, setOrgDefaultAmbientBgState] = useState<boolean>(() => {
    const saved = localStorage.getItem(ORG_AMBIENT_STORAGE_KEY)
    return saved !== null ? saved === 'true' : true
  })

  const [userAmbientOverride, setUserAmbientOverride] = useState<boolean | null>(() => {
    const saved = localStorage.getItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY)
    if (saved === 'true') return true
    if (saved === 'false') return false
    return null
  })

  useEffect(() => {
    if (typeof window === 'undefined' || !window.matchMedia) return
    const mediaQuery = window.matchMedia('(prefers-color-scheme: light)')
    const handleChange = (e: MediaQueryListEvent) => {
      setSystemTheme(e.matches ? 'light' : 'dark')
    }
    mediaQuery.addEventListener('change', handleChange)
    return () => mediaQuery.removeEventListener('change', handleChange)
  }, [])

  useEffect(() => {
    if (!user) {
      hydratedUserIdRef.current = null
      setUserThemeOverride(null)
      setUserAmbientOverride(null)
      localStorage.removeItem(USER_OVERRIDE_STORAGE_KEY)
      localStorage.removeItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY)
      return
    }
    if (hydratedUserIdRef.current === user.id) return
    hydratedUserIdRef.current = user.id

    const prefOverride = user.preferences?.theme_override
    if (isValidThemeMode(prefOverride)) {
      setUserThemeOverride(prefOverride)
      localStorage.setItem(USER_OVERRIDE_STORAGE_KEY, prefOverride)
    } else {
      setUserThemeOverride(null)
      localStorage.removeItem(USER_OVERRIDE_STORAGE_KEY)
    }

    const prefAmbientOverride = user.preferences?.ambient_bg_override
    if (typeof prefAmbientOverride === 'boolean') {
      setUserAmbientOverride(prefAmbientOverride)
      localStorage.setItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY, String(prefAmbientOverride))
    } else {
      setUserAmbientOverride(null)
      localStorage.removeItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY)
    }
  }, [user])

  const themeMode: ThemeMode = userThemeOverride ?? orgDefaultTheme
  const theme: ResolvedTheme = themeMode === 'system' ? systemTheme : themeMode
  const ambientBg: boolean = userAmbientOverride ?? orgDefaultAmbientBg

  useEffect(() => {
    const root = document.documentElement
    root.classList.remove('light', 'dark')
    root.classList.add(theme)
    root.setAttribute('data-theme', theme)
  }, [theme])

  const setOrgDefaultTheme = useCallback((mode: ThemeMode) => {
    setOrgDefaultThemeState(mode)
    localStorage.setItem(ORG_THEME_STORAGE_KEY, mode)
  }, [])

  const setOrgDefaultAmbientBg = useCallback((enabled: boolean) => {
    setOrgDefaultAmbientBgState(enabled)
    localStorage.setItem(ORG_AMBIENT_STORAGE_KEY, String(enabled))
  }, [])

  const setThemeMode = useCallback(
    (mode: ThemeMode) => {
      setUserThemeOverride(mode)
      localStorage.setItem(USER_OVERRIDE_STORAGE_KEY, mode)
      void updatePreferences({ theme_override: mode })
    },
    [updatePreferences]
  )

  const clearThemeOverride = useCallback(() => {
    setUserThemeOverride(null)
    localStorage.removeItem(USER_OVERRIDE_STORAGE_KEY)
    void updatePreferences({ theme_override: null })
  }, [updatePreferences])

  const toggleTheme = useCallback(() => {
    const effectiveMode: ThemeMode = userThemeOverride ?? orgDefaultTheme
    const currentEffective: ResolvedTheme =
      effectiveMode === 'system' ? getSystemTheme() : effectiveMode
    const next: ResolvedTheme = currentEffective === 'dark' ? 'light' : 'dark'
    setUserThemeOverride(next)
    localStorage.setItem(USER_OVERRIDE_STORAGE_KEY, next)
    void updatePreferences({ theme_override: next })
  }, [orgDefaultTheme, updatePreferences, userThemeOverride])

  const setAmbientBg = useCallback(
    (enabled: boolean) => {
      setUserAmbientOverride(enabled)
      localStorage.setItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY, String(enabled))
      void updatePreferences({ ambient_bg_override: enabled })
    },
    [updatePreferences]
  )

  const clearAmbientOverride = useCallback(() => {
    setUserAmbientOverride(null)
    localStorage.removeItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY)
    void updatePreferences({ ambient_bg_override: null })
  }, [updatePreferences])

  const clearAppearanceOverrides = useCallback(() => {
    setUserThemeOverride(null)
    setUserAmbientOverride(null)
    localStorage.removeItem(USER_OVERRIDE_STORAGE_KEY)
    localStorage.removeItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY)
    void updatePreferences({ theme_override: null, ambient_bg_override: null })
  }, [updatePreferences])

  const toggleAmbientBg = useCallback(() => {
    const next = !(userAmbientOverride ?? orgDefaultAmbientBg)
    setUserAmbientOverride(next)
    localStorage.setItem(USER_AMBIENT_OVERRIDE_STORAGE_KEY, String(next))
    void updatePreferences({ ambient_bg_override: next })
  }, [orgDefaultAmbientBg, updatePreferences, userAmbientOverride])

  return (
    <ThemeContext.Provider
      value={{
        theme,
        themeMode,
        orgDefaultTheme,
        userThemeOverride,
        setOrgDefaultTheme,
        setThemeMode,
        clearThemeOverride,
        toggleTheme,
        ambientBg,
        orgDefaultAmbientBg,
        userAmbientOverride,
        setOrgDefaultAmbientBg,
        setAmbientBg,
        clearAmbientOverride,
        clearAppearanceOverrides,
        toggleAmbientBg,
      }}
    >
      {children}
    </ThemeContext.Provider>
  )
}

export const useTheme = () => {
  const context = useContext(ThemeContext)
  if (!context) throw new Error('useTheme must be used within ThemeProvider')
  return context
}
