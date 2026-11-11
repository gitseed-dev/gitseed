import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

const apiTarget = process.env.API_TARGET || 'http://localhost:8420'

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
