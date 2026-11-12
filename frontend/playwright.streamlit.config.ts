import { defineConfig, devices } from '@playwright/test'

// The Streamlit demo's text-rendering check. Starts the fixture page with the Python on PATH (or $PYTHON).
const python = process.env.PYTHON ?? 'python'
const port = 8599

export default defineConfig({
  testDir: './e2e',
  testMatch: 'streamlit-text.spec.ts',
  timeout: 60_000,
  use: { ...devices['Desktop Chrome'], baseURL: `http://127.0.0.1:${port}` },
  webServer: {
    command: `${python} -m streamlit run e2e/streamlit_text_page.py --server.port ${port} --server.address 127.0.0.1 --server.headless true --browser.gatherUsageStats false`,
    url: `http://127.0.0.1:${port}`,
    timeout: 60_000,
    reuseExistingServer: false,
  },
})
