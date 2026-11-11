import React from 'react'
import { Modal as HeroModal } from '@heroui/react'
import { X } from 'lucide-react'
import { Tooltip } from './Tooltip'

export type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | '2xl' | '3xl' | '4xl' | '5xl'

interface ModalProps {
  isOpen: boolean
  onClose: () => void
  title: React.ReactNode
  children: React.ReactNode
  size?: ModalSize
}

const sizeClasses: Record<ModalSize, string> = {
  sm: '!max-w-sm',
  md: '!max-w-md',
  lg: '!max-w-lg',
  xl: '!max-w-xl',
  '2xl': '!max-w-2xl',
  '3xl': '!max-w-3xl',
  '4xl': '!max-w-4xl',
  '5xl': '!max-w-5xl',
}

const sizeMaxWidths: Record<ModalSize, string> = {
  sm: '24rem',
  md: '28rem',
  lg: '32rem',
  xl: '36rem',
  '2xl': '42rem',
  '3xl': '48rem',
  '4xl': '56rem',
  '5xl': '64rem',
}

export const Modal: React.FC<ModalProps> = ({
  isOpen,
  onClose,
  title,
  children,
  size = '2xl',
}) => {
  if (!isOpen) return null

  const sizeClass = sizeClasses[size] || sizeClasses['2xl']
  const maxWidth = sizeMaxWidths[size] || sizeMaxWidths['2xl']

  return (
    <HeroModal.Root isOpen={isOpen} onOpenChange={(open: boolean) => !open && onClose()}>
      <HeroModal.Backdrop className="fixed inset-0 bg-black/60 backdrop-blur-sm z-50 flex items-center justify-center p-4">
        <HeroModal.Container className={`!w-full ${sizeClass}`}>
          <HeroModal.Dialog
            style={{ width: `min(calc(100vw - 2rem), ${maxWidth})`, maxWidth }}
            className={`!w-full ${sizeClass} relative bg-white dark:bg-zinc-900 border border-zinc-200 dark:border-zinc-700/80 rounded-2xl shadow-2xl overflow-visible flex flex-col max-h-[90vh] !p-0`}
          >
            <div className="flex items-center justify-between px-6 py-4 border-b border-zinc-200 dark:border-zinc-700/80 shrink-0">
              <HeroModal.Heading className="text-lg font-bold text-zinc-900 dark:text-zinc-100 flex-1 min-w-0 flex items-center gap-2 pr-4">
                {title}
              </HeroModal.Heading>
              <Tooltip content="Close (Esc)" placement="left">
                <button
                  type="button"
                  onClick={onClose}
                  className="shrink-0 p-1.5 rounded-lg text-zinc-400 hover:text-zinc-700 dark:hover:text-zinc-200 hover:bg-zinc-100 dark:hover:bg-zinc-800 transition cursor-pointer"
                  aria-label="Close modal"
                >
                  <X size={18} />
                </button>
              </Tooltip>
            </div>
            <div className="overflow-y-auto p-6 flex flex-col gap-4">
              {children}
            </div>
          </HeroModal.Dialog>
        </HeroModal.Container>
      </HeroModal.Backdrop>
    </HeroModal.Root>
  )
}
