import {defineConfig} from '@playwright/test';

// Not 4173 (vite preview's default): the self-hosted runner is shared with other repos' e2e jobs.
const port = 4417;
const origin = `http://127.0.0.1:${port}`;

export default defineConfig({
  testDir: './e2e',
  reporter: process.env['CI'] ? 'github' : 'list',
  use: {
    baseURL: origin,
    headless: true,
    // page.route answers the feeds; a registered worker would fetch them itself, out of the
    // route's sight. pwa.spec.ts opts back in to test the worker.
    serviceWorkers: 'block',
  },
  webServer: {
    // The tests run against the production build; `npm run build` must have run first.
    command: 'node e2e/serve-dist.mjs',
    env: {PORT: String(port)},
    url: origin,
    reuseExistingServer: !process.env['CI'],
    timeout: 15_000,
  },
});
