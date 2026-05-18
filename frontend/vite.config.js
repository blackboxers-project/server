import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

const API_URL = process.env.API_URL || 'http://localhost:8000'
const WS_URL  = process.env.WS_URL  || 'ws://localhost:8000'

export default defineConfig({
  plugins: [react()],
  server: {
    host: '0.0.0.0',
    watch: { usePolling: true },
    proxy: {
      '/api': { target: API_URL, changeOrigin: true },
      '/ws':  { target: WS_URL,  ws: true },
    },
  },
})
