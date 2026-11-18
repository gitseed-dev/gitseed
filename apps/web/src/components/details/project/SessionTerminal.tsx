import { forwardRef, useCallback, useEffect, useImperativeHandle, useRef, useState } from 'react'
import type { Terminal as XTerm } from '@xterm/xterm'
import type { FitAddon } from '@xterm/addon-fit'
import { Loader2, RotateCcw } from 'lucide-react'
// xterm's stylesheet is imported once in main.tsx; the library itself is loaded on demand.

export interface SessionTerminalHandle {
  /** Type text into the terminal as if the user typed it (e.g. "Run tests") */
  sendText: (text: string) => void
  focus: () => void
}

export interface SessionTerminalProps {
  /** WebSocket URL of the PTY endpoint (without query string) */
  url: string
  /** Extra hint shown while connecting */
  connectingLabel?: string
  /** Called when the connection is lost for good (after automatic retries) */
  onDisconnected?: () => void
  /** Called if the shell prompt indicates a root session (e.g. `root@container:...#`) */
  onRootDetected?: () => void
  className?: string
}

type ConnState = 'connecting' | 'open' | 'closed'

const ROOT_PROMPT_RE = /\broot@[\w.-]+:[^#\r\n]*#/

/** Real interactive terminal (xterm.js) attached to a session container over WebSocket. */
export const SessionTerminal = forwardRef<SessionTerminalHandle, SessionTerminalProps>(
  ({ url, connectingLabel = 'Connecting to terminal…', onDisconnected, onRootDetected, className = '' }, ref) => {
    const hostRef = useRef<HTMLDivElement | null>(null)
    const termRef = useRef<XTerm | null>(null)
    const fitRef = useRef<FitAddon | null>(null)
    const wsRef = useRef<WebSocket | null>(null)
    const rootDetectedRef = useRef(false)
    const onRootDetectedRef = useRef(onRootDetected)
    onRootDetectedRef.current = onRootDetected
    const onDisconnectedRef = useRef(onDisconnected)
    onDisconnectedRef.current = onDisconnected
    const [ready, setReady] = useState(false)
    const [state, setState] = useState<ConnState>('connecting')
    const [closeReason, setCloseReason] = useState<string>('')
    const [attempt, setAttempt] = useState(0)

    const send = useCallback((payload: object) => {
      const ws = wsRef.current
      if (ws && ws.readyState === WebSocket.OPEN) ws.send(JSON.stringify(payload))
    }, [])

    useImperativeHandle(
      ref,
      () => ({
        sendText: (text: string) => {
          send({ type: 'input', data: text })
          termRef.current?.focus()
        },
        focus: () => termRef.current?.focus(),
      }),
      [send]
    )

    // Create the xterm instance once (lazy-loaded)
    useEffect(() => {
      let disposed = false
      let cleanup: (() => void) | null = null
      void (async () => {
        const [{ Terminal }, { FitAddon: Fit }] = await Promise.all([
          import('@xterm/xterm'),
          import('@xterm/addon-fit'),
        ])
        if (disposed || !hostRef.current) return
        const term = new Terminal({
          cursorBlink: true,
          fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
          fontSize: 13,
          scrollback: 5000,
          theme: { background: '#09090b', foreground: '#e4e4e7', cursor: '#2dd4bf' },
        })
        const fit = new Fit()
        term.loadAddon(fit)
        term.open(hostRef.current)
        try {
          fit.fit()
        } catch {
          /* host not laid out yet */
        }
        termRef.current = term
        fitRef.current = fit

        const dataSub = term.onData((data) => send({ type: 'input', data }))
        const resizeSub = term.onResize(({ cols, rows }) => send({ type: 'resize', cols, rows }))
        const observer = new ResizeObserver(() => {
          try {
            if (hostRef.current && hostRef.current.clientWidth > 0 && hostRef.current.clientHeight > 0) {
              fit.fit()
            }
          } catch {
            /* ignore */
          }
        })
        observer.observe(hostRef.current)
        cleanup = () => {
          observer.disconnect()
          dataSub.dispose()
          resizeSub.dispose()
          term.dispose()
          termRef.current = null
        }
        setReady(true)
      })()
      return () => {
        disposed = true
        cleanup?.()
      }
    }, [send])

    // (Re)connect the WebSocket whenever the URL or attempt changes.
    // Drops are retried automatically (1s, 2s, 4s); Reconnect is only offered after that.
    useEffect(() => {
      const term = termRef.current
      if (!ready || !term) return
      let retryTimer: number | null = null
      let retries = 0
      let ws: WebSocket | null = null
      let cancelled = false

      const connect = () => {
        setState('connecting')
        setCloseReason('')
        // The server replays the live terminal's scrollback on every attach, so start clean
        term.reset()
        ws = new WebSocket(`${url}?cols=${term.cols}&rows=${term.rows}`)
        ws.binaryType = 'arraybuffer'
        wsRef.current = ws
        ws.onopen = () => {
          retries = 0
          setState('open')
          term.focus()
        }
        const decoder = new TextDecoder()
        ws.onmessage = (ev) => {
          if (typeof ev.data === 'string') {
            term.write(ev.data)
            if (!rootDetectedRef.current && ROOT_PROMPT_RE.test(ev.data)) {
              rootDetectedRef.current = true
              onRootDetectedRef.current?.()
            }
          } else {
            const bytes = new Uint8Array(ev.data as ArrayBuffer)
            term.write(bytes)
            if (!rootDetectedRef.current) {
              const text = decoder.decode(bytes, { stream: true })
              if (ROOT_PROMPT_RE.test(text)) {
                rootDetectedRef.current = true
                onRootDetectedRef.current?.()
              }
            }
          }
        }
        ws.onclose = (ev) => {
          if (cancelled) return
          // 4400/4401: the server refused (not running, no access) — retrying won't help
          const permanent = ev.code === 4400 || ev.code === 4401 || ev.code === 1000
          if (!permanent && retries < 3) {
            const delay = 1000 * 2 ** retries
            retries += 1
            setState('connecting')
            retryTimer = window.setTimeout(connect, delay)
            return
          }
          setState('closed')
          setCloseReason(ev.reason || (ev.code === 1000 ? 'Shell exited' : 'Connection lost'))
          term.write('\r\n\x1b[2m[disconnected]\x1b[0m\r\n')
          onDisconnectedRef.current?.()
        }
      }
      connect()

      return () => {
        cancelled = true
        if (retryTimer !== null) window.clearTimeout(retryTimer)
        if (ws) {
          ws.onclose = null
          ws.close()
          if (wsRef.current === ws) wsRef.current = null
        }
      }
      // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [url, attempt, ready])

    return (
      <div className={`relative overflow-hidden bg-zinc-950 ${className}`}>
        {/* Inset via positioning, not padding: FitAddon measures the host's full height and
            doesn't subtract its padding, which made the terminal overflow by the padding. */}
        <div ref={hostRef} className="absolute inset-2" />
        {state !== 'open' && (
          <div className="absolute top-2 right-3 flex items-center gap-2 px-2.5 py-1 rounded-lg bg-zinc-900/90 border border-zinc-700 text-xs text-zinc-300">
            {state === 'connecting' ? (
              <>
                <Loader2 size={12} className="animate-spin text-teal-400" />
                {connectingLabel}
              </>
            ) : (
              <>
                <span>{closeReason}</span>
                <button
                  type="button"
                  onClick={() => setAttempt((a) => a + 1)}
                  className="inline-flex items-center gap-1 text-teal-300 hover:text-teal-200 cursor-pointer"
                >
                  <RotateCcw size={11} />
                  Reconnect
                </button>
              </>
            )}
          </div>
        )}
      </div>
    )
  }
)
SessionTerminal.displayName = 'SessionTerminal'
