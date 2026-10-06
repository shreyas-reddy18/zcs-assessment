import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// In development the gateway runs on :8080; Vite proxies the API to it.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: { '/api': 'http://127.0.0.1:8080' },
  },
  build: { target: 'es2022' },
})
