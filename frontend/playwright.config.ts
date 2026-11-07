import {screenBaselineConfig} from '@lifekit-hq/tokens/screen';
import {chromium, defineConfig} from '@playwright/test';

// Not 4173 (vite preview's default): the self-hosted runner is shared with other repos' e2e jobs.
const port = 4417;
const origin = `http://127.0.0.1:${port}`;

// The route scan's design judge starts its own Chrome from one executable path. In the DevClaw
// worker image that path is a wrapper that turns the browser sandbox off (the container cannot
// build Chromium's sandbox); the image sets DEVCLAW_CHROMIUM_NO_SANDBOX, nothing else does, so CI
// and local runs scan in the page's own Chromium as is. The scan never overrides a value set here.
const noSandbox = process.env['DEVCLAW_CHROMIUM_NO_SANDBOX'];
if (noSandbox) {
  process.env['DEVCLAW_CHROMIUM'] = chromium.executablePath();
  process.env['IMPECCABLE_BROWSER'] = noSandbox;
}

export default defineConfig({
  // The baselines are only ever written by the "Update screen baselines" workflow
  // (updateSnapshots: 'none'), and compared exactly with animations off.
  ...screenBaselineConfig(),
  testDir: './e2e',
  // The fixture registers its tests from inside node_modules, where Playwright would look for the
  // images; they live in the repository, next to the spec that lists the routes.
  snapshotPathTemplate: '{testDir}/screens/baselines/{arg}{-projectName}{-snapshotSuffix}{ext}',
  reporter: process.env['CI'] ? 'github' : 'list',
  use: {
    baseURL: origin,
    headless: true,
    // page.route answers the feeds; a registered worker would fetch them itself, out of the
    // route's sight. pwa.spec.ts opts back in to test the worker.
    serviceWorkers: 'block',
  },
  // The tests run against the production build; `npm run build` must have run first. The account's
  // saved theme is the server's `system` default, so each scan's colour scheme sets the theme.
  webServer: {
    command: 'node e2e/serve-dist.mjs',
    env: {PORT: String(port)},
    url: origin,
    reuseExistingServer: !process.env['CI'],
    timeout: 15_000,
  },
});
