import React from 'react'
import { Button } from '@heroui/react'
import { AlertTriangle } from 'lucide-react'
import { Modal } from './Modal'
import { useTranslation } from '../../context/I18nContext'

export interface ConfirmDialogProps {
  isOpen: boolean
  title: string
  message: React.ReactNode
  confirmLabel?: string
  confirmText?: string
  cancelLabel?: string
  cancelText?: string
  onConfirm: () => void
  onCancel: () => void
  danger?: boolean
  confirmVariant?: 'danger' | 'primary' | 'secondary'
  isLoading?: boolean
  secondaryAction?: {
    label: string
    onClick: () => void
  }
}

export const ConfirmDialog: React.FC<ConfirmDialogProps> = ({
  isOpen,
  title,
  message,
  confirmLabel,
  confirmText,
  cancelLabel,
  cancelText,
  onConfirm,
  onCancel,
  danger = true,
  confirmVariant,
  isLoading = false,
  secondaryAction,
}) => {
  const { t } = useTranslation()
  const isDanger = confirmVariant ? confirmVariant === 'danger' : danger
  const cText = confirmText || confirmLabel || t('common.confirm')
  const canText = cancelText || cancelLabel || t('common.cancel')

  return (
    <Modal isOpen={isOpen} onClose={onCancel} title={title} size="md">
      <div className="flex items-start gap-4">
        {isDanger && (
          <div className="p-2 rounded-xl bg-red-500/10 text-red-500 shrink-0 mt-0.5">
            <AlertTriangle size={22} />
          </div>
        )}
        <div className="text-zinc-600 dark:text-zinc-400 text-sm leading-relaxed">
          {message}
        </div>
      </div>
      <div className="flex items-center justify-between gap-3 pt-4 border-t border-zinc-200 dark:border-zinc-700/80">
        <div>
          {secondaryAction && (
            <Button
              variant="secondary"
              size="sm"
              onClick={secondaryAction.onClick}
              className="text-xs text-teal-600 dark:text-teal-400 font-semibold"
            >
              {secondaryAction.label}
            </Button>
          )}
        </div>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={onCancel} isDisabled={isLoading}>
            {canText}
          </Button>
          <Button
            variant={isDanger ? 'danger' : 'primary'}
            size="sm"
            onClick={onConfirm}
            isDisabled={isLoading}
          >
            {cText}
          </Button>
        </div>
      </div>
    </Modal>
  )
}

