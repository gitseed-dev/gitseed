import { Component, type ErrorInfo, type ReactNode } from 'react'
import { AlertTriangle, RotateCcw, Home } from 'lucide-react'
import { Button, Card } from '@heroui/react'

export interface ErrorBoundaryProps {
  children: ReactNode
  fallbackTitle?: string
  fallbackMessage?: string
  onReset?: () => void
}

interface ErrorBoundaryState {
  hasError: boolean
  error: Error | null
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props)
    this.state = { hasError: false, error: null }
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: ErrorInfo) {
    console.error('GitSeed UI ErrorBoundary caught an exception:', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: null })
    if (this.props.onReset) {
      this.props.onReset()
    }
  }

  handleNavigateHome = () => {
    this.setState({ hasError: false, error: null })
    window.location.href = '/overview'
  }

  render() {
    if (this.state.hasError) {
      return (
        <div
          data-testid="error-boundary-fallback"
          className="w-full py-12 px-4 flex items-center justify-center"
        >
          <Card className="max-w-lg w-full p-6 border border-rose-500/25 dark:border-rose-500/35 bg-white/90 dark:bg-zinc-900/90 backdrop-blur-xl rounded-2xl shadow-xl flex flex-col gap-4">
            <div className="flex items-start gap-3.5">
              <div className="w-10 h-10 rounded-xl bg-rose-500/15 text-rose-600 dark:text-rose-400 flex items-center justify-center shrink-0 border border-rose-500/20">
                <AlertTriangle size={20} />
              </div>
              <div className="flex flex-col min-w-0 flex-1">
                <h3 className="font-heading font-bold text-base text-zinc-900 dark:text-zinc-100">
                  {this.props.fallbackTitle || 'Something went wrong'}
                </h3>
                <p className="text-xs text-zinc-600 dark:text-zinc-400 mt-1">
                  {this.props.fallbackMessage ||
                    'An unexpected error occurred while rendering this view. You can reload the view or return to the overview.'}
                </p>
              </div>
            </div>

            {this.state.error?.message && (
              <div className="p-3 rounded-xl bg-zinc-100 dark:bg-zinc-950/90 text-rose-700 dark:text-rose-300 font-mono text-xs overflow-x-auto border border-zinc-200 dark:border-zinc-800">
                <div className="font-semibold text-zinc-500 dark:text-zinc-400 text-xs uppercase tracking-wider mb-1">
                  Exception Details:
                </div>
                <code>{this.state.error.message}</code>
              </div>
            )}

            <div className="flex items-center justify-end gap-2.5 pt-2 border-t border-zinc-200 dark:border-zinc-800">
              <Button
                variant="secondary"
                size="sm"
                onClick={this.handleNavigateHome}
                className="flex items-center gap-1.5 text-xs font-semibold rounded-xl cursor-pointer"
              >
                <Home size={13} />
                <span>Return to Overview</span>
              </Button>
              <Button
                variant="primary"
                size="sm"
                data-testid="error-boundary-reload-btn"
                onClick={this.handleReset}
                className="flex items-center gap-1.5 text-xs font-semibold rounded-xl bg-teal-600 hover:bg-teal-500 text-white cursor-pointer"
              >
                <RotateCcw size={13} />
                <span>Reload View</span>
              </Button>
            </div>
          </Card>
        </div>
      )
    }

    return this.props.children
  }
}
