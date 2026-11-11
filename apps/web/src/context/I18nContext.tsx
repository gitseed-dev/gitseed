import React, { createContext, useContext, useState, useCallback } from 'react'
import messagesEn from '../locales/messages.json'

export type Messages = typeof messagesEn

interface I18nContextType {
  locale: string
  setLocale: (locale: string) => void
  t: (key: string, params?: Record<string, string | number>) => string
}

const I18nContext = createContext<I18nContextType | undefined>(undefined)

const getNestedValue = (obj: unknown, path: string): string | undefined => {
  const parts = path.split('.')
  let current: unknown = obj
  for (const part of parts) {
    if (!current || typeof current !== 'object') return undefined
    const rec = current as Record<string, unknown>
    if (rec[part] !== undefined) {
      current = rec[part]
    } else {
      const lower = part.toLowerCase()
      const foundKey = Object.keys(rec).find((k) => k.toLowerCase() === lower)
      if (foundKey && rec[foundKey] !== undefined) {
        current = rec[foundKey]
      } else {
        return undefined
      }
    }
  }
  return typeof current === 'string' ? current : undefined
}

export const I18nProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const [locale, setLocale] = useState<string>(() => localStorage.getItem('gitseed_locale') || 'en')

  const t = useCallback(
    (key: string, params?: Record<string, string | number>): string => {
      const raw = getNestedValue(messagesEn, key)
      if (typeof raw !== 'string') {
        if (key.endsWith('.id')) {
          return messagesEn.common?.id || 'ID'
        }
        return key
      }

      if (!params) return raw

      let result = raw
      Object.entries(params).forEach(([paramKey, val]) => {
        result = result.replace(new RegExp(`\\{${paramKey}\\}`, 'g'), String(val))
      })
      return result
    },
    []
  )

  const handleSetLocale = useCallback((newLocale: string) => {
    setLocale(newLocale)
    localStorage.setItem('gitseed_locale', newLocale)
  }, [])

  return (
    <I18nContext.Provider value={{ locale, setLocale: handleSetLocale, t }}>
      {children}
    </I18nContext.Provider>
  )
}

export const useTranslation = () => {
  const context = useContext(I18nContext)
  if (!context) {
    throw new Error('useTranslation must be used within an I18nProvider')
  }
  return context
}

export default useTranslation
