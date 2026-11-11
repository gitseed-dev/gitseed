import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/index.css'
import '@xterm/xterm/css/xterm.css'
import App from './App.tsx'

const PRELOAD_RELOAD_KEY = 'gitseed:vite-preload-reload-at'
const PRELOAD_RELOAD_COOLDOWN_MS = 15_000

if (typeof window !== 'undefined') {
  window.addEventListener('vite:preloadError', (event) => {
    try {
      const now = Date.now()
      const lastReload = Number(sessionStorage.getItem(PRELOAD_RELOAD_KEY) || '0')
      if (!lastReload || now - lastReload > PRELOAD_RELOAD_COOLDOWN_MS) {
        sessionStorage.setItem(PRELOAD_RELOAD_KEY, String(now))
        event.preventDefault()
        window.location.reload()
      }
    } catch {
      // Ignore storage errors in restricted browsing contexts
    }
  })
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
