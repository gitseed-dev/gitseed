import React from 'react'
import { Tooltip } from '../common/Tooltip'
import type { ViewType } from '../../constants'

export interface SidebarNavItemConfig {
  id: ViewType
  label: string
  icon: React.ComponentType<{ size?: number; className?: string }>
  badgeType?: 'count' | 'dot'
  badgeCount?: number
  badgeVariant?: 'amber' | 'red' | 'neutral'
  badgeTooltip?: string
}

export interface SidebarNavItemProps {
  item: SidebarNavItemConfig
  isActive: boolean
  isCollapsed: boolean
  onSelect: (view: ViewType) => void
}

export const SidebarNavItem: React.FC<SidebarNavItemProps> = ({
  item,
  isActive,
  isCollapsed,
  onSelect,
}) => {
  const Icon = item.icon

  if (isCollapsed) {
    const tooltipText = item.badgeTooltip
      ? `${item.label} · ${item.badgeTooltip}`
      : item.label

    return (
      <Tooltip content={tooltipText} placement="right">
        <button
          type="button"
          onClick={() => onSelect(item.id)}
          aria-label={item.label}
          className={`relative flex items-center justify-center w-11 h-11 shrink-0 mx-auto rounded-xl transition cursor-pointer ${
            isActive
              ? 'bg-teal-600 dark:bg-teal-500 text-white dark:text-zinc-950 font-semibold shadow-md shadow-teal-600/30 dark:shadow-teal-500/20'
              : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-900'
          }`}
        >
          <Icon
            size={19}
            className={
              isActive ? 'text-white dark:text-zinc-950' : 'text-zinc-500 dark:text-zinc-400'
            }
          />
          {item.badgeType === 'count' &&
            item.badgeCount !== undefined &&
            item.badgeCount > 0 && (
              <span
                className={`absolute top-1 right-1 min-w-[17px] h-[17px] px-1 text-[9px] font-bold font-mono rounded-full flex items-center justify-center leading-none shadow-xs ${
                  item.badgeVariant === 'neutral'
                    ? isActive
                      ? 'bg-white text-teal-700'
                      : 'bg-teal-600 text-white'
                    : item.badgeVariant === 'red'
                    ? isActive
                      ? 'bg-white text-rose-700'
                      : 'bg-rose-500 text-white'
                    : isActive
                      ? 'bg-zinc-950 text-amber-300'
                      : 'bg-amber-500 text-zinc-950'
                }`}
              >
                {item.badgeCount > 99 ? '99+' : item.badgeCount}
              </span>
            )}
          {item.badgeType === 'dot' && (
            <span className="absolute top-2 right-2 w-2 h-2 rounded-full bg-rose-500 shadow-[0_0_6px_#f43f5e] ring-2 ring-white dark:ring-zinc-950" />
          )}
        </button>
      </Tooltip>
    )
  }

  return (
    <button
      type="button"
      onClick={() => onSelect(item.id)}
      className={`flex items-center justify-between w-full h-10 shrink-0 px-3 rounded-xl text-sm font-medium transition-colors cursor-pointer ${
        isActive
          ? 'bg-teal-600 dark:bg-teal-500 text-white dark:text-zinc-950 font-semibold shadow-md shadow-teal-600/30 dark:shadow-teal-500/20'
          : 'text-zinc-600 dark:text-zinc-400 hover:text-zinc-900 dark:hover:text-zinc-100 hover:bg-zinc-100 dark:hover:bg-zinc-900'
      }`}
    >
      <div className="flex items-center gap-3">
        <Icon
          size={18}
          className={
            isActive ? 'text-white dark:text-zinc-950' : 'text-zinc-500 dark:text-zinc-400'
          }
        />
        <span>{item.label}</span>
      </div>
      {item.badgeType === 'count' &&
        item.badgeCount !== undefined &&
        item.badgeCount > 0 && (
          <span
            className={`h-5 min-w-[20px] px-1.5 inline-flex items-center justify-center rounded-full text-[11px] font-bold font-mono leading-none border transition-colors shrink-0 ${
              item.badgeVariant === 'neutral'
                ? isActive
                  ? 'bg-white/20 text-white dark:text-zinc-950 border-white/30 dark:border-zinc-950/20'
                  : 'bg-zinc-100 dark:bg-zinc-800 text-zinc-600 dark:text-zinc-300 border-zinc-200 dark:border-zinc-700'
                : item.badgeVariant === 'red'
                ? isActive
                  ? 'bg-rose-700 text-white border-rose-700 shadow-xs'
                  : 'bg-rose-500/15 text-rose-600 dark:text-rose-400 border-rose-500/25'
                : isActive
                  ? 'bg-amber-400 text-zinc-950 border-amber-400 shadow-xs'
                  : 'bg-amber-500/15 text-amber-700 dark:text-amber-300 border-amber-500/25'
            }`}
          >
            {item.badgeCount}
          </span>
        )}
      {item.badgeType === 'dot' && (
        <Tooltip content={item.badgeTooltip || 'Requires attention'} placement="right">
          <span className="w-2 h-2 rounded-full bg-rose-500 shadow-[0_0_6px_#f43f5e] shrink-0" />
        </Tooltip>
      )}
    </button>
  )
}

