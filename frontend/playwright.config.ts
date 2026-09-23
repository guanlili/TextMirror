import { defineConfig, devices } from '@playwright/test'
import { env } from 'node:process'

const baseURL = env.E2E_BASE_URL || 'http://127.0.0.1:3022'
const url = new URL(baseURL)
if (!['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) || url.protocol !== 'http:') {
  throw new Error('E2E_BASE_URL must point to a local HTTP development server')
}

export default defineConfig({
  testDir: './e2e',
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 30_000,
  expect: { timeout: 8_000 },
  outputDir: 'test-results',
  reporter: [['list'], ['html', { open: 'never' }], ['json', { outputFile: 'test-results/results.json' }]],
  use: {
    baseURL,
    ...devices['Desktop Chrome'],
    channel: env.E2E_BROWSER_CHANNEL || undefined,
    serviceWorkers: 'block',
    acceptDownloads: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
  },
})
