import { defineConfig, devices } from '@playwright/test'

export default defineConfig({
  testDir: './e2e',
  timeout: 60_000,
  use: { ...devices['Desktop Chrome'], baseURL: 'https://127.0.0.1:4173', ignoreHTTPSErrors: true },
  webServer: {
    command: 'node node_modules/vite/bin/vite.js --port 4173 --strictPort',
    url: 'https://127.0.0.1:4173',
    ignoreHTTPSErrors: true,
    reuseExistingServer: false,
  },
})
