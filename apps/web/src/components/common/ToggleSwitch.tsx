import React from 'react'

export interface ToggleSwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  disabled?: boolean
  label?: string
  ariaLabel?: string
  size?: 'sm' | 'md'
  color?: 'teal' | 'indigo' | 'emerald'
  className?: string
}

export const ToggleSwitch: React.FC<ToggleSwitchProps> = ({
  checked,
  onChange,
  disabled = false,
  label,
  ariaLabel,
  size = 'md',
  color = 'teal',
  className = '',
}) => {
  const isSm = size === 'sm'

  const activeColorClasses = {
    teal: 'bg-teal-600 dark:bg-teal-500 focus-visible:ring-teal-500/50',
    indigo: 'bg-indigo-600 dark:bg-indigo-500 focus-visible:ring-indigo-500/50',
    emerald: 'bg-emerald-600 dark:bg-emerald-500 focus-visible:ring-emerald-500/50',
  }[color]

  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      aria-label={ariaLabel || label}
      disabled={disabled}
      onClick={() => !disabled && onChange(!checked)}
      className={`relative inline-flex shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none focus-visible:ring-2 focus-visible:ring-offset-2 ${
        isSm ? 'h-5 w-9' : 'h-6 w-11'
      } ${
        checked ? activeColorClasses : 'bg-zinc-300 dark:bg-zinc-700 focus-visible:ring-zinc-400/50'
      } ${disabled ? 'opacity-50 cursor-not-allowed' : ''} ${className}`}
    >
      <span
        aria-hidden="true"
        className={`pointer-events-none inline-block rounded-full bg-white shadow-md ring-0 transition duration-200 ease-in-out ${
          isSm ? 'h-4 w-4' : 'h-5 w-5'
        } ${
          checked
            ? isSm
              ? 'translate-x-4'
              : 'translate-x-5'
            : 'translate-x-0'
        }`}
      />
    </button>
  )
}

