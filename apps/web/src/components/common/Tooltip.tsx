import React, { useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'

export type TooltipPlacement =
  | 'top'
  | 'bottom'
  | 'left'
  | 'right'
  | 'top-start'
  | 'top-end'
  | 'bottom-start'
  | 'bottom-end'

export interface TooltipProps {
  content: React.ReactNode
  children: React.ReactElement<Record<string, unknown>>
  placement?: TooltipPlacement
  delay?: number
  disabled?: boolean
  className?: string
}

export const Tooltip: React.FC<TooltipProps> = ({
  content,
  children,
  placement = 'top',
  delay = 120,
  disabled = false,
  className = '',
}) => {
  const [isVisible, setIsVisible] = useState(false)
  const [coords, setCoords] = useState<{ top: number; left: number }>({ top: 0, left: 0 })
  const triggerRef = useRef<HTMLElement | null>(null)
  const tooltipRef = useRef<HTMLDivElement | null>(null)
  const timeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  const updatePosition = () => {
    if (!triggerRef.current) return
    const rect = triggerRef.current.getBoundingClientRect()
    const gap = 8

    let top = 0
    let left = 0

    switch (placement) {
      case 'top':
        top = rect.top - gap
        left = rect.left + rect.width / 2
        break
      case 'top-start':
        top = rect.top - gap
        left = rect.left
        break
      case 'top-end':
        top = rect.top - gap
        left = rect.right
        break
      case 'bottom':
        top = rect.bottom + gap
        left = rect.left + rect.width / 2
        break
      case 'bottom-start':
        top = rect.bottom + gap
        left = rect.left
        break
      case 'bottom-end':
        top = rect.bottom + gap
        left = rect.right
        break
      case 'left':
        top = rect.top + rect.height / 2
        left = rect.left - gap
        break
      case 'right':
        top = rect.top + rect.height / 2
        left = rect.right + gap
        break
    }

    setCoords({ top, left })
  }

  const handleMouseEnter = () => {
    if (disabled || !content) return
    timeoutRef.current = setTimeout(() => {
      updatePosition()
      setIsVisible(true)
    }, delay)
  }

  const handleMouseLeave = () => {
    if (timeoutRef.current) {
      clearTimeout(timeoutRef.current)
    }
    setIsVisible(false)
  }

  useEffect(() => {
    if (disabled) {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
      setIsVisible(false)
    }
  }, [disabled])

  useEffect(() => {
    return () => {
      if (timeoutRef.current) {
        clearTimeout(timeoutRef.current)
      }
    }
  }, [])

  useLayoutEffect(() => {
    if (isVisible && tooltipRef.current) {
      const tooltipRect = tooltipRef.current.getBoundingClientRect()
      const padding = 12
      let deltaX = 0
      let deltaY = 0

      if (tooltipRect.left < padding) {
        deltaX = padding - tooltipRect.left
      } else if (tooltipRect.right > window.innerWidth - padding) {
        deltaX = window.innerWidth - padding - tooltipRect.right
      }

      if (tooltipRect.top < padding) {
        deltaY = padding - tooltipRect.top
      } else if (tooltipRect.bottom > window.innerHeight - padding) {
        deltaY = window.innerHeight - padding - tooltipRect.bottom
      }

      if (deltaX !== 0 || deltaY !== 0) {
        setCoords((prev) => ({
          top: prev.top + deltaY,
          left: prev.left + deltaX,
        }))
      }
    }
  }, [isVisible])

  const getTransformClass = () => {
    switch (placement) {
      case 'top':
        return '-translate-x-1/2 -translate-y-full'
      case 'top-start':
        return '-translate-y-full'
      case 'top-end':
        return '-translate-x-full -translate-y-full'
      case 'bottom':
        return '-translate-x-1/2'
      case 'bottom-start':
        return ''
      case 'bottom-end':
        return '-translate-x-full'
      case 'left':
        return '-translate-x-full -translate-y-1/2'
      case 'right':
        return '-translate-y-1/2'
    }
  }

  const childProps = children.props as {
    onMouseEnter?: (e: React.MouseEvent) => void
    onMouseLeave?: (e: React.MouseEvent) => void
    onFocus?: (e: React.FocusEvent) => void
    onBlur?: (e: React.FocusEvent) => void
  }

  const trigger = React.cloneElement(children, {
    ref: (node: HTMLElement | null) => {
      triggerRef.current = node
      const childRef = (children as unknown as { ref?: unknown }).ref
      if (typeof childRef === 'function') childRef(node)
      else if (childRef && typeof childRef === 'object' && 'current' in childRef) {
        ;(childRef as { current: HTMLElement | null }).current = node
      }
    },
    onMouseEnter: (e: React.MouseEvent) => {
      handleMouseEnter()
      childProps.onMouseEnter?.(e)
    },
    onMouseLeave: (e: React.MouseEvent) => {
      handleMouseLeave()
      childProps.onMouseLeave?.(e)
    },
    onFocus: (e: React.FocusEvent) => {
      handleMouseEnter()
      childProps.onFocus?.(e)
    },
    onBlur: (e: React.FocusEvent) => {
      handleMouseLeave()
      childProps.onBlur?.(e)
    },
  })

  return (
    <>
      {trigger}
      {isVisible &&
        createPortal(
          <div
            ref={tooltipRef}
            style={{
              position: 'fixed',
              top: `${coords.top}px`,
              left: `${coords.left}px`,
            }}
            className={`z-[9999] pointer-events-none transition-all duration-150 ease-out transform ${getTransformClass()} ${className}`}
          >
            <div className="px-2.5 py-1 text-xs font-medium tracking-wide bg-zinc-900/95 dark:bg-zinc-800/95 text-white dark:text-zinc-100 rounded-lg shadow-xl border border-zinc-700/60 backdrop-blur-sm whitespace-nowrap animate-in fade-in-0 zoom-in-95">
              {content}
            </div>
          </div>,
          document.body
        )}
    </>
  )
}
