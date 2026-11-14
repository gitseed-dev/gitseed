import React from 'react'

export interface SegmentedControlItem<T extends string = string> {
  id: T
  label: React.ReactNode
  count?: number
  icon?: React.ReactNode
  title?: string
}

export interface SegmentedControlProps<T extends string = string> {
  items: readonly SegmentedControlItem<T>[] | SegmentedControlItem<T>[]
  selectedId: T
  onSelect: (id: T) => void
  disabled?: boolean
  className?: string
  ariaLabel?: string
}

export function SegmentedControl<T extends string = string>({
  items,
  selectedId,
  onSelect,
  disabled = false,
  className = '',
  ariaLabel,
}: SegmentedControlProps<T>) {
  const isFullWidth = className.includes('w-full')

  return (
    <div
      role="group"
      aria-label={ariaLabel}
      className={`flex items-center h-[34px] p-0.5 rounded-xl border border-zinc-200 dark:border-zinc-700/70 bg-zinc-100/70 dark:bg-zinc-800/60 shrink-0 ${
        disabled ? 'opacity-60 cursor-not-allowed' : ''
      } ${className}`}
    >
      {items.map((item) => {
        const isActive = selectedId === item.id
        return (
          <button
            key={item.id}
            type="button"
            disabled={disabled}
            title={item.title}
            aria-pressed={isActive}
            onClick={() => {
              if (!disabled) onSelect(item.id)
            }}
            className={`h-full px-2 sm:px-2.5 rounded-lg text-xs flex items-center justify-center transition select-none whitespace-nowrap shrink-0 ${
              isFullWidth ? 'flex-1' : ''
            } ${
              disabled ? 'cursor-not-allowed' : 'cursor-pointer'
            } ${
              isActive
                ? 'bg-white dark:bg-zinc-900 text-zinc-900 dark:text-zinc-100 shadow-2xs font-semibold'
                : 'text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200 font-medium'
            }`}
          >
            {item.icon && <span className="mr-1.5 shrink-0">{item.icon}</span>}
            <span>{item.label}</span>
            {item.count !== undefined && (
              <span
                className={`ml-1 sm:ml-1.5 px-1.5 py-0.5 rounded-full text-xs font-semibold leading-none ${
                  isActive
                    ? 'bg-zinc-100 dark:bg-zinc-800 text-zinc-700 dark:text-zinc-300'
                    : 'bg-zinc-200/60 dark:bg-zinc-700/50 text-zinc-500 dark:text-zinc-400'
                }`}
              >
                {item.count}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}

