import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

const apiTarget = process.env.API_TARGET || 'http://localhost:8420'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    rolldownOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return undefined
          if (
            id.includes('/@heroui/') ||
            id.includes('\\@heroui\\') ||
            id.includes('/@react-aria/') ||
            id.includes('\\@react-aria\\') ||
            id.includes('/@react-stately/') ||
            id.includes('\\@react-stately\\') ||
            id.includes('/@internationalized/') ||
            id.includes('\\@internationalized\\') ||
            id.includes('/framer-motion/') ||
            id.includes('\\framer-motion\\')
          ) {
            return 'vendor-ui'
          }
          if (
            id.includes('/lucide-react/') ||
            id.includes('\\lucide-react\\')
          ) {
            return 'vendor-icons'
          }
          if (
            id.includes('/@tanstack/') ||
            id.includes('\\@tanstack\\')
          ) {
            return 'vendor-query'
          }
          return undefined
        },
      },
    },
  },
  server: {
    host: '0.0.0.0',
    port: 8421,
    watch: {
      usePolling: true,
      interval: 100,
    },
    proxy: {
      '/api': {
        target: apiTarget,
        changeOrigin: true,
        ws: true,
      },
      '/healthz': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/version': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/docs': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/redoc': {
        target: apiTarget,
        changeOrigin: true,
      },
      '/openapi.json': {
        target: apiTarget,
        changeOrigin: true,
      },
    },
  },
})
