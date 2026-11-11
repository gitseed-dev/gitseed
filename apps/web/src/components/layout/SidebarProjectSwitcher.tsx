import React, { useEffect, useMemo, useRef, useState } from 'react'
import { Check, ChevronDown, ChevronRight, Search } from 'lucide-react'
import { Tooltip } from '../common/Tooltip'
import { useData } from '../../context/DataContext'
import { useTranslation } from '../../context/I18nContext'
import { VIEWS } from '../../constants'
import { getProjectInitials } from '../../utils/projects'

export interface SidebarProjectSwitcherProps {
  isCollapsed: boolean
  isOpen: boolean
  onOpenChange: (open: boolean) => void
}

export const SidebarProjectSwitcher: React.FC<SidebarProjectSwitcherProps> = ({
  isCollapsed,
  isOpen,
  onOpenChange,
}) => {
  const { t } = useTranslation()
  const {
    projects,
    selectedProject,
    selectedProjectObj,
    setSelectedProject,
    setActiveView,
  } = useData()

  const [searchQuery, setSearchQuery] = useState('')
  const containerRef = useRef<HTMLDivElement>(null)
  const searchInputRef = useRef<HTMLInputElement>(null)

  const activeProjects = useMemo(
    () => projects.filter((p) => !p.archived_at),
    [projects]
  )

  useEffect(() => {
    if (selectedProject && projects.length > 0) {
      const current = projects.find((p) => p.id === selectedProject)
      if (current?.archived_at) {
        setSelectedProject(null)
      }
    }
  }, [selectedProject, projects, setSelectedProject])

  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      if (containerRef.current && !containerRef.current.contains(e.target as Node)) {
        onOpenChange(false)
      }
    }
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        onOpenChange(false)
      }
    }
    if (isOpen) {
      document.addEventListener('mousedown', handleClickOutside)
      document.addEventListener('keydown', handleKeyDown)
      setTimeout(() => {
        searchInputRef.current?.focus()
      }, 50)
    } else {
      setSearchQuery('')
    }
    return () => {
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen, onOpenChange])

  const filteredProjects = useMemo(() => {
    return [...activeProjects]
      .filter((p) => {
        if (!searchQuery.trim()) return true
        const q = searchQuery.toLowerCase()
        return p.name.toLowerCase().includes(q) || p.slug.toLowerCase().includes(q)
      })
      .sort((a, b) => a.name.localeCompare(b.name))
  }, [activeProjects, searchQuery])

  return (
    <div ref={containerRef} className="relative shrink-0">
      {isCollapsed ? (
        <div className="h-16 flex items-center justify-center border-b border-zinc-200/80 dark:border-zinc-700/80 px-2">
          <Tooltip
            content={
              selectedProjectObj
                ? `${selectedProjectObj.name} (${selectedProjectObj.slug}) · ${t('nav.switchProject')}`
                : `${t('common.appName')} · ${t('common.allProjects')}`
            }
            placement="right"
            disabled={isOpen}
          >
            <button
              type="button"
              onClick={() => onOpenChange(!isOpen)}
              aria-label={t('nav.switchProject')}
              className="w-10 h-10 rounded-xl hover:bg-zinc-100 dark:hover:bg-zinc-900 flex items-center justify-center cursor-pointer transition relative group border border-transparent hover:border-zinc-200 dark:hover:border-zinc-700/80"
            >
              <div className="w-7 h-7 rounded-lg overflow-hidden shadow-2xs flex items-center justify-center shrink-0">
                <img src="/gitseed-logo.svg" alt="GitSeed" className="w-full h-full object-cover" />
              </div>
              <span className="absolute bottom-1 right-1 w-2 h-2 rounded-full bg-emerald-500 ring-2 ring-white dark:ring-zinc-950" />
            </button>
          </Tooltip>
        </div>
      ) : (
        <div className="h-16 flex items-center px-3 border-b border-zinc-200/80 dark:border-zinc-700/80">
          <Tooltip
            content={
              selectedProjectObj
                ? `${selectedProjectObj.name} (${selectedProjectObj.slug}) · ${t('nav.switchProject')}`
                : `${t('common.allProjects')} · ${t('nav.switchProject')}`
            }
            placement="bottom-start"
            disabled={isOpen}
          >
            <button
              type="button"
              onClick={() => onOpenChange(!isOpen)}
              className="w-full flex items-center justify-between px-2.5 py-2 rounded-xl hover:bg-zinc-100 dark:hover:bg-zinc-900 transition-colors text-left cursor-pointer group"
              aria-label={t('nav.switchProject')}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-7 h-7 rounded-lg overflow-hidden shadow-2xs flex items-center justify-center shrink-0">
                  <img src="/gitseed-logo.svg" alt="GitSeed" className="w-full h-full object-cover" />
                </div>
                <div className="flex items-center text-sm min-w-0 leading-none">
                  {/* Just "GitSeed" across all projects; "GitSeed / Project" once one is picked */}
                  {selectedProjectObj ? (
                    <>
                      <span className="font-medium text-zinc-500 dark:text-zinc-400 shrink-0">
                        GitSeed
                      </span>
                      <span className="text-zinc-400 dark:text-zinc-600 select-none mx-1.5 shrink-0 font-normal">
                        /
                      </span>
                      <span className="font-semibold text-zinc-900 dark:text-zinc-100 truncate">
                        {selectedProjectObj.name}
                      </span>
                    </>
                  ) : (
                    <span className="font-semibold text-zinc-900 dark:text-zinc-100 shrink-0">
                      GitSeed
                    </span>
                  )}
                </div>
              </div>
              <ChevronDown
                size={14}
                className={`text-zinc-400 group-hover:text-zinc-700 dark:group-hover:text-zinc-200 shrink-0 transition-transform duration-200 ml-1.5 ${
                  isOpen ? 'rotate-180' : ''
                }`}
              />
            </button>
          </Tooltip>
        </div>
      )}

      {isOpen && (
        <div
          className={`absolute bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-2xl shadow-2xl z-50 overflow-hidden flex flex-col ring-1 ring-black/5 dark:ring-white/10 w-80 max-h-[calc(100vh-80px)] ${
            isCollapsed ? 'left-[76px] top-3' : 'left-3 top-14'
          }`}
        >
          <div className="p-2.5 border-b border-zinc-100 dark:border-zinc-800">
            <div className="relative">
              <Search size={14} className="absolute left-2.5 top-2.5 text-zinc-400" />
              <input
                ref={searchInputRef}
                type="text"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                placeholder={t('nav.searchProjects')}
                className="w-full bg-zinc-100/80 dark:bg-zinc-800/60 border border-zinc-200 dark:border-zinc-700/80 rounded-xl pl-8 pr-3 py-1.5 text-xs text-zinc-800 dark:text-zinc-200 placeholder-zinc-400 focus:outline-none focus:border-teal-500/60 focus:ring-2 focus:ring-teal-500/25 transition-all"
              />
            </div>
          </div>

          <div className="flex-1 min-h-0 max-h-[calc(100vh-160px)] sm:max-h-[500px] overflow-y-auto p-1.5 flex flex-col gap-0.5">
            <button
              type="button"
              onClick={() => {
                setSelectedProject(null)
                onOpenChange(false)
              }}
              className={`w-full flex items-center justify-between px-2.5 py-2 rounded-xl text-xs transition cursor-pointer ${
                !selectedProject
                  ? 'bg-teal-500/10 dark:bg-teal-500/15 text-teal-700 dark:text-teal-300 font-semibold'
                  : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800/60'
              }`}
            >
              <div className="flex items-center gap-2.5 min-w-0">
                <div className="w-6 h-6 rounded-lg overflow-hidden flex items-center justify-center shrink-0 border border-zinc-200 dark:border-zinc-700/80 shadow-2xs">
                  <img src="/gitseed-logo.svg" alt="GitSeed" className="w-full h-full object-cover" />
                </div>
                <div className="flex flex-col text-left min-w-0">
                  <span className="truncate font-semibold">{t('common.allProjects')}</span>
                </div>
              </div>
              {!selectedProject && (
                <Check size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
              )}
            </button>

            <div className="my-1 border-t border-zinc-100 dark:border-zinc-800 px-2 pt-1.5">
              <span className="text-[11px] font-bold uppercase tracking-wider text-zinc-500 dark:text-zinc-400 font-mono">
                {t('nav.projects')} ({activeProjects.length})
              </span>
            </div>

            {filteredProjects.length === 0 ? (
              <div className="p-3 text-center text-xs text-zinc-400">
                {t('nav.noProjectsFound')}
              </div>
            ) : (
              filteredProjects.map((p) => {
                const isSelected = selectedProject === p.id
                return (
                  <button
                    key={p.id}
                    type="button"
                    onClick={() => {
                      setSelectedProject(p.id)
                      onOpenChange(false)
                    }}
                    className={`w-full flex items-center justify-between px-2.5 py-2 rounded-xl text-xs transition cursor-pointer ${
                      isSelected
                        ? 'bg-teal-500/10 dark:bg-teal-500/15 text-teal-700 dark:text-teal-300 font-semibold'
                        : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800/60'
                    }`}
                  >
                    <div className="flex items-center gap-2.5 min-w-0">
                      <div className="w-6 h-6 rounded-lg bg-teal-500/15 border border-teal-500/30 text-teal-700 dark:text-teal-300 flex items-center justify-center font-mono font-bold text-[10px] uppercase shrink-0">
                        {getProjectInitials(p)}
                      </div>
                      <div className="flex flex-col text-left min-w-0">
                        <span className="truncate font-medium">{p.name}</span>
                        <span className="text-[10px] font-mono text-zinc-400 truncate">
                          {p.slug}
                        </span>
                      </div>
                    </div>
                    {isSelected && (
                      <Check size={14} className="text-teal-600 dark:text-teal-400 shrink-0" />
                    )}
                  </button>
                )
              })
            )}
          </div>

          <div className="p-2 border-t border-zinc-100 dark:border-zinc-800 bg-zinc-50/50 dark:bg-zinc-900/30">
            <button
              type="button"
              onClick={() => {
                onOpenChange(false)
                setActiveView(VIEWS.PROJECTS)
              }}
              className="w-full flex items-center justify-between px-2.5 py-1.5 rounded-lg text-xs font-semibold text-zinc-500 hover:text-teal-600 dark:hover:text-teal-400 transition cursor-pointer"
            >
              <span>{t('nav.manageProjects')}</span>
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}

