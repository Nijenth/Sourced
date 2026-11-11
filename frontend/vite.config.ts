import react from '@vitejs/plugin-react'
import basicSsl from '@vitejs/plugin-basic-ssl'
import { defineConfig } from 'vitest/config'

const api = process.env.SOURCED_API_URL ?? 'http://127.0.0.1:8000'

export default defineConfig({
  plugins: [react(), basicSsl()],
  server: {
    host: '127.0.0.1',
    proxy: {
      '/api': api,
      '/c/': api,
      '/widget-assets/': api,
      '/demo/': api,
    },
  },
  test: {
    environment: 'jsdom',
    include: ['src/**/*.test.tsx'],
    setupFiles: ['src/test-setup.ts'],
  },
})
