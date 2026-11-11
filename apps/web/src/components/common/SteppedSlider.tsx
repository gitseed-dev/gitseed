import React from 'react'
import { Chip } from '@heroui/react'

export interface SteppedSliderStep<T extends string> {
  value: T
  label: string
  icon: React.ReactNode
  headerIcon: React.ReactNode
  chipColor: 'default' | 'accent' | 'success' | 'warning' | 'danger'
  thumbActiveClass: string
  thumbPassedClass?: string
  dotActiveClass?: string
  dotPassedClass?: string
  labelActiveClass: string
}

export interface SteppedSliderProps<T extends string> {
  title: string
  description: string
  value: T
  steps: [SteppedSliderStep<T>, SteppedSliderStep<T>, SteppedSliderStep<T>]
  onChange: (next: T) => void
  trackGradientClass: string
}

export function SteppedSlider<T extends string>({
  title,
  description,
  value,
  steps,
  onChange,
  trackGradientClass,
}: SteppedSliderProps<T>) {
  const activeIdx = Math.max(
    0,
    steps.findIndex((s) => s.value === value)
  )
  const activeStep = steps[activeIdx] || steps[0]
  const fillWidth = activeIdx === 0 ? '0%' : activeIdx === 1 ? '50%' : '100%'

  return (
    <div className="flex flex-col gap-3.5 p-3.5 sm:p-4 rounded-xl bg-zinc-50 dark:bg-zinc-800/40 border border-zinc-200/60 dark:border-zinc-700/60 min-w-0">
      <div className="flex items-center justify-between gap-3 min-w-0">
        <div className="flex items-center gap-3 min-w-0 flex-1">
          {activeStep.headerIcon}
          <div className="flex flex-col min-w-0">
            <span
              className="text-sm font-semibold text-zinc-900 dark:text-zinc-100 truncate"
              title={title}
            >
              {title}
            </span>
            <span
              className="text-xs text-zinc-500 dark:text-zinc-400 truncate"
              title={description}
            >
              {description}
            </span>
          </div>
        </div>

        <Chip
          size="sm"
          variant="soft"
          color={activeStep.chipColor}
          className="text-xs font-semibold shrink-0"
        >
          {activeStep.label}
        </Chip>
      </div>

      <div className="pt-2 pb-1 px-3">
        <div className="relative py-2">
          <div className="relative w-full h-1.5 bg-zinc-200 dark:bg-zinc-700/80 rounded-full overflow-hidden">
            <div
              className={`absolute top-0 left-0 h-full rounded-full transition-all duration-300 ${trackGradientClass}`}
              style={{ width: fillWidth }}
            />
          </div>

          <input
            type="range"
            min="0"
            max="2"
            step="1"
            value={activeIdx}
            onChange={(e) => {
              const idx = parseInt(e.target.value, 10)
              const target = steps[idx]
              if (target) onChange(target.value)
            }}
            aria-label={title}
            className="absolute inset-0 w-full h-full opacity-0 cursor-pointer z-20"
          />

          <div className="absolute top-1/2 -translate-y-1/2 left-0 right-0 pointer-events-none z-10">
            <div className="relative w-full h-0">
              {steps.map((step, idx) => {
                const isSelected = idx === activeIdx
                const isPassed = idx < activeIdx
                const leftPct = idx === 0 ? '0%' : idx === 1 ? '50%' : '100%'

                const thumbClass = isSelected
                  ? step.thumbActiveClass
                  : isPassed && step.thumbPassedClass
                    ? step.thumbPassedClass
                    : 'bg-white dark:bg-zinc-800 border-zinc-300 dark:border-zinc-600 hover:border-zinc-400'

                const dotClass = isSelected
                  ? step.dotActiveClass || 'bg-white'
                  : isPassed && step.dotPassedClass
                    ? step.dotPassedClass
                    : 'bg-zinc-300 dark:bg-zinc-600'

                return (
                  <button
                    key={step.value}
                    type="button"
                    onClick={() => onChange(step.value)}
                    style={{ left: leftPct }}
                    className={`absolute top-0 -translate-x-1/2 -translate-y-1/2 w-6 h-6 rounded-full flex items-center justify-center border-2 transition-all duration-200 pointer-events-auto cursor-pointer ${thumbClass}`}
                    title={step.label}
                  >
                    <span className={`w-2 h-2 rounded-full ${dotClass}`} />
                  </button>
                )
              })}
            </div>
          </div>
        </div>

        <div className="flex justify-between text-xs mt-2 select-none">
          {steps.map((step, idx) => {
            const isSelected = idx === activeIdx
            const alignClass =
              idx === 0 ? 'text-left' : idx === 1 ? 'text-center' : 'text-right'
            return (
              <button
                key={step.value}
                type="button"
                onClick={() => onChange(step.value)}
                className={`flex items-center gap-1.5 transition-colors cursor-pointer ${alignClass} ${
                  isSelected
                    ? step.labelActiveClass
                    : 'font-medium text-zinc-500 dark:text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-200'
                }`}
              >
                {step.icon}
                <span>{step.label}</span>
              </button>
            )
          })}
        </div>
      </div>
    </div>
  )
}

