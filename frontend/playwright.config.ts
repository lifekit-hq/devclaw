import {defineConfig} from '@playwright/test';

export default defineConfig({
  testDir: './e2e',
  reporter: process.env['CI'] ? 'github' : 'list',
  use: {
    baseURL: 'http://127.0.0.1:4173',
    headless: true,
  },
  webServer: {
    // The tests run against the production build; `npm run build` must have run first.
    command: 'node e2e/serve-dist.mjs',
    url: 'http://127.0.0.1:4173',
    reuseExistingServer: !process.env['CI'],
    timeout: 15_000,
  },
});
