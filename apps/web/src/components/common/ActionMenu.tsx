import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { MoreHorizontal } from 'lucide-react'
import { Button } from '@heroui/react'

export interface ActionMenuItem {
  label: string
  icon?: React.ReactNode
  onClick: () => void
  variant?: 'default' | 'danger'
  disabled?: boolean
  dividerBefore?: boolean
}

export interface ActionMenuProps {
  items: ActionMenuItem[]
  ariaLabel?: string
  align?: 'right' | 'left'
  className?: string
  buttonClassName?: string
  customTrigger?: (helpers: { isOpen: boolean; toggle: () => void }) => React.ReactNode
}

export const ActionMenu: React.FC<ActionMenuProps> = ({
  items,
  ariaLabel = 'More actions',
  align = 'right',
  className = '',
  buttonClassName = '',
  customTrigger,
}) => {
  const [isOpen, setIsOpen] = useState(false)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const menuRef = useRef<HTMLDivElement>(null)
  const [menuStyle, setMenuStyle] = useState<React.CSSProperties>({})

  const updatePosition = useCallback(() => {
    if (!buttonRef.current) return
    const rect = buttonRef.current.getBoundingClientRect()
    const menuEstimatedHeight = items.length * 36 + 12
    const spaceBelow = window.innerHeight - rect.bottom
    const openUpwards = spaceBelow < menuEstimatedHeight && rect.top > menuEstimatedHeight

    const style: React.CSSProperties = {
      position: 'fixed',
      zIndex: 99999,
      minWidth: 160,
    }

    if (openUpwards) {
      style.bottom = `${window.innerHeight - rect.top + 4}px`
    } else {
      style.top = `${rect.bottom + 4}px`
    }

    if (align === 'right') {
      style.right = `${window.innerWidth - rect.right}px`
    } else {
      style.left = `${rect.left}px`
    }

    setMenuStyle(style)
  }, [align, items.length])

  useLayoutEffect(() => {
    if (isOpen) {
      updatePosition()
    }
  }, [isOpen, updatePosition])

  useEffect(() => {
    if (!isOpen) return

    const handleScrollOrResize = () => {
      updatePosition()
    }

    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as Node
      if (
        menuRef.current &&
        !menuRef.current.contains(target) &&
        buttonRef.current &&
        !buttonRef.current.contains(target)
      ) {
        setIsOpen(false)
      }
    }

    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setIsOpen(false)
      }
    }

    window.addEventListener('scroll', handleScrollOrResize, true)
    window.addEventListener('resize', handleScrollOrResize)
    document.addEventListener('mousedown', handleClickOutside)
    document.addEventListener('keydown', handleKeyDown)

    return () => {
      window.removeEventListener('scroll', handleScrollOrResize, true)
      window.removeEventListener('resize', handleScrollOrResize)
      document.removeEventListener('mousedown', handleClickOutside)
      document.removeEventListener('keydown', handleKeyDown)
    }
  }, [isOpen, updatePosition])

  const toggle = () => {
    setIsOpen((prev) => !prev)
  }

  return (
    <div
      className={`relative inline-flex items-center justify-end ${className}`}
      onClick={(e) => e.stopPropagation()}
    >
      {customTrigger ? (
        customTrigger({ isOpen, toggle })
      ) : (
        <Button
          ref={buttonRef}
          variant="ghost"
          size="sm"
          isIconOnly
          onClick={(e) => {
            e.stopPropagation()
            toggle()
          }}
          className={`h-7 w-7 p-0 text-zinc-400 hover:text-zinc-800 dark:hover:text-zinc-100 rounded-lg cursor-pointer transition-colors ${buttonClassName}`}
          aria-label={ariaLabel}
          aria-expanded={isOpen}
        >
          <MoreHorizontal size={14} />
        </Button>
      )}

      {isOpen &&
        createPortal(
          <div
            ref={menuRef}
            style={menuStyle}
            onClick={(e) => e.stopPropagation()}
            className="bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-xl shadow-xl py-1.5 text-left text-[13px] ring-1 ring-black/5 dark:ring-white/10 animate-in fade-in-0 zoom-in-95 duration-100 select-none"
          >
            {items.map((item, index) => {
              const isDanger = item.variant === 'danger'
              return (
                <React.Fragment key={`${item.label}-${index}`}>
                  {item.dividerBefore && index > 0 && (
                    <div className="my-1 border-t border-zinc-200/70 dark:border-zinc-800" />
                  )}
                  <button
                    type="button"
                    disabled={item.disabled}
                    onClick={(e) => {
                      e.stopPropagation()
                      setIsOpen(false)
                      item.onClick()
                    }}
                    className={`w-full px-3.5 py-2 flex items-center gap-2.5 transition-colors cursor-pointer text-left ${item.disabled
                        ? 'opacity-40 cursor-not-allowed text-zinc-400'
                        : isDanger
                          ? 'text-rose-600 dark:text-rose-400 hover:bg-rose-50 dark:hover:bg-rose-950/30'
                          : 'text-zinc-700 dark:text-zinc-300 hover:bg-zinc-100 dark:hover:bg-zinc-800'
                      }`}
                  >
                    {item.icon && (
                      <span
                        className={`shrink-0 ${isDanger
                            ? 'text-rose-500'
                            : 'text-zinc-400 group-hover:text-zinc-600 dark:group-hover:text-zinc-200'
                          }`}
                      >
                        {item.icon}
                      </span>
                    )}
                    <span className="font-medium truncate">{item.label}</span>
                  </button>
                </React.Fragment>
              )
            })}
          </div>,
          document.body
        )}
    </div>
  )
}

