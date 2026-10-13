import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// In development the API is proxied, so the browser talks to one origin.
// In production set VITE_API_URL to the deployed backend's /api URL.
export default defineConfig({
  plugins: [react()],
  build: {
    // MapLibre alone is ~1 MB minified (~350 kB gzipped); that is expected here.
    chunkSizeWarningLimit: 1500,
  },
  server: {
    proxy: {
      '/api': 'http://127.0.0.1:8000',
    },
  },
})
