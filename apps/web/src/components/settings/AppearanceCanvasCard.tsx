import React from 'react'
import { BookOpen, Monitor, Moon, Palette, Sparkles, Sun } from 'lucide-react'
import { Card, Chip } from '@heroui/react'
import { useTranslation } from '../../context/I18nContext'
import type { ThemeMode } from '../../context/ThemeContext'
import { SteppedSlider } from '../common/SteppedSlider'
import { ToggleSwitch } from '../common/ToggleSwitch'

interface AppearanceCanvasCardProps {
  themeMode: ThemeMode
  onChangeThemeMode: (next: ThemeMode) => void
  ambientBg: boolean
  onChangeAmbientBg: (next: boolean) => void
  showGuideInSidebar: boolean
  onChangeShowGuideInSidebar: (next: boolean) => void
}

export const AppearanceCanvasCard: React.FC<AppearanceCanvasCardProps> = ({
  themeMode,
  onChangeThemeMode,
  ambientBg,
  onChangeAmbientBg,
  showGuideInSidebar,
  onChangeShowGuideInSidebar,
}) => {
  const { t } = useTranslation()

  return (
    <Card className="p-5 border border-zinc-200/80 dark:border-zinc-700/80 bg-white/60 dark:bg-zinc-900/60 backdrop-blur-md rounded-2xl shadow-xs flex flex-col gap-4 dark:ring-1 dark:ring-white/[0.04]">
      <div className="flex items-center gap-3">
        <div className="w-10 h-10 rounded-xl bg-purple-500/10 text-purple-600 dark:text-purple-400 flex items-center justify-center shrink-0">
          <Palette size={20} />
        </div>
        <div>
          <h3 className="text-base font-bold text-zinc-900 dark:text-zinc-100">
            {t('settings.appearance.title')}
          </h3>
          <p className="text-xs sm:text-sm text-zinc-500 dark:text-zinc-400">
            {t('settings.appearance.description')}
          </p>
        </div>
      </div>

      <div className="flex flex-col gap-3 pt-2 border-t border-zinc-100 dark:border-zinc-800">
        <SteppedSlider<ThemeMode>
          title={t('settings.appearance.themeLabel')}
          description={t('settings.appearance.themeDesc')}
          value={themeMode}
          onChange={onChangeThemeMode}
          trackGradientClass="bg-gradient-to-r from-purple-500 via-amber-500 to-sky-500"
          steps={[
            {
              value: 'dark',
              label: t('settings.appearance.themeDark'),
              icon: <Moon size={12} />,
              headerIcon: <Moon size={16} className="text-purple-500 shrink-0" />,
              chipColor: 'accent',
              thumbActiveClass:
                'bg-purple-600 border-white dark:border-zinc-900 shadow-md ring-4 ring-purple-500/25 scale-110',
              dotActiveClass: 'bg-white',
              labelActiveClass: 'font-bold text-purple-600 dark:text-purple-400',
            },
            {
              value: 'light',
              label: t('settings.appearance.themeLight'),
              icon: <Sun size={12} />,
              headerIcon: <Sun size={16} className="text-amber-500 shrink-0" />,
              chipColor: 'warning',
              thumbActiveClass:
                'bg-amber-500 border-white dark:border-zinc-900 shadow-md ring-4 ring-amber-500/25 scale-110',
              thumbPassedClass: 'bg-amber-400 border-white dark:border-zinc-900 shadow-xs',
              dotActiveClass: 'bg-white',
              dotPassedClass: 'bg-white/80',
              labelActiveClass: 'font-bold text-amber-600 dark:text-amber-400',
            },
            {
              value: 'system',
              label: t('settings.appearance.themeSystem'),
              icon: <Monitor size={12} />,
              headerIcon: <Monitor size={16} className="text-sky-500 shrink-0" />,
              chipColor: 'default',
              thumbActiveClass:
                'bg-sky-500 border-white dark:border-zinc-900 shadow-md ring-4 ring-sky-500/25 scale-110',
              dotActiveClass: 'bg-white',
              labelActiveClass: 'font-bold text-sky-600 dark:text-sky-400',
            },
          ]}
        />

        {/* Ambient Atmosphere row */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <Sparkles
              size={16}
              className={ambientBg ? 'text-teal-500 shrink-0' : 'text-zinc-400 shrink-0'}
            />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.appearance.ambientBg')}
              >
                {t('settings.appearance.ambientBg')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.appearance.ambientBgDesc')}
              >
                {t('settings.appearance.ambientBgDesc')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2.5 shrink-0">
            <Chip
              size="sm"
              variant="soft"
              color={ambientBg ? 'accent' : 'default'}
              className="text-xs font-semibold shrink-0"
            >
              {ambientBg
                ? t('settings.appearance.ambientActive')
                : t('settings.appearance.ambientFlat')}
            </Chip>
            <ToggleSwitch
              checked={ambientBg}
              onChange={onChangeAmbientBg}
              ariaLabel={t('settings.appearance.ambientBg')}
              color="teal"
            />
          </div>
        </div>

        {/* Show "How it Works" in Sidebar row */}
        <div className="flex items-center justify-between gap-3 p-3 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
          <div className="flex items-center gap-3 min-w-0 flex-1">
            <BookOpen
              size={16}
              className={showGuideInSidebar ? 'text-teal-500 shrink-0' : 'text-zinc-400 shrink-0'}
            />
            <div className="flex flex-col min-w-0">
              <span
                className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
                title={t('settings.appearance.showGuideNav')}
              >
                {t('settings.appearance.showGuideNav')}
              </span>
              <span
                className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
                title={t('settings.appearance.showGuideNavDesc')}
              >
                {t('settings.appearance.showGuideNavDesc')}
              </span>
            </div>
          </div>
          <div className="flex items-center gap-2.5 shrink-0">
            <Chip
              size="sm"
              variant="soft"
              color={showGuideInSidebar ? 'accent' : 'default'}
              className="text-xs font-semibold shrink-0"
            >
              {showGuideInSidebar
                ? t('settings.appearance.guideNavVisible')
                : t('settings.appearance.guideNavHidden')}
            </Chip>
            <ToggleSwitch
              checked={showGuideInSidebar}
              onChange={onChangeShowGuideInSidebar}
              ariaLabel={t('settings.appearance.showGuideNav')}
              color="teal"
            />
          </div>
        </div>
      </div>
    </Card>
  )
}

