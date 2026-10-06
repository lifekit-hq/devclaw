import {readFileSync} from 'node:fs';
import {join} from 'node:path';

import {expect, test} from '@playwright/test';

// The PWA half of the cutover, against the built dist: the manifest and the precache-only worker.
// The worker's scope is /console/, so the feeds and the gate's /oauth2/ sit outside it; it caches
// the bundle only and passes every feed read through to the host.
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(join(__dirname, 'fixtures', `${name}.json`), 'utf8'));

const FEEDS: Record<string, string> = {
  '/goals.json': 'goals',
  '/control.json': 'control',
  '/oauth2/userinfo': 'userinfo',
};

test('the manifest installs the console at /console/ with every icon served', async ({
  page,
  request,
}) => {
  await page.goto('/console/needs-you');

  const href = await page
    .locator('link[rel="manifest"]')
    .evaluate(l => (l as HTMLLinkElement).href);
  expect(new URL(href).pathname).toBe('/console/manifest.webmanifest');
  const manifest = (await (await request.get(href)).json()) as {
    id: string;
    scope: string;
    start_url: string;
    icons: {src: string}[];
  };
  expect([manifest.id, manifest.scope, manifest.start_url]).toEqual([
    '/console/',
    '/console/',
    '/console/',
  ]);
  for (const icon of manifest.icons) {
    const res = await request.get(new URL(icon.src, href).href);
    expect(res.status(), icon.src).toBe(200);
    expect(res.headers()['content-type']).toBe('image/png');
  }
});

test('ngsw.json precaches the console bundle and no feed', async ({request}) => {
  const ngsw = (await (await request.get('/console/ngsw.json')).json()) as {
    index: string;
    assetGroups: {urls: string[]}[];
    dataGroups: unknown[];
  };
  expect(ngsw.index).toBe('/console/index.html');
  expect(ngsw.dataGroups).toEqual([]);
  const urls = ngsw.assetGroups.flatMap(group => group.urls);
  expect(urls).toContain('/console/index.html');
  expect(urls).toContain('/console/manifest.webmanifest');
  expect(urls.filter(url => !url.startsWith('/console/'))).toEqual([]);
});

test.describe('with the worker registered', () => {
  test.use({serviceWorkers: 'allow'});
  // registerWhenStable:30000 registers at the latest 30 s after boot.
  test.setTimeout(90_000);

  test('the feeds still reach the host and the shell opens offline', async ({page, context}) => {
    const reads: string[] = [];
    await context.route(
      url => url.pathname in FEEDS,
      route => {
        const path = new URL(route.request().url()).pathname;
        reads.push(path);
        return route.fulfill({json: fixture(FEEDS[path])});
      }
    );
    await page.goto('/console/needs-you');
    const scope = await page.evaluate(async () => (await navigator.serviceWorker.ready).scope);
    expect(new URL(scope).pathname).toBe('/console/');
    await page.reload();
    await expect.poll(() => page.evaluate(() => !!navigator.serviceWorker.controller)).toBe(true);

    // Under the worker, every reload reads the feeds from the host again: nothing is cached.
    reads.length = 0;
    await page.reload();
    await expect(page.getByText('Should the export include archived reports?')).toBeVisible();
    expect(reads).toEqual(expect.arrayContaining(['/goals.json', '/oauth2/userinfo']));

    // The worker prefetches the bundle once it activates; go offline when all of it is cached.
    await expect
      .poll(
        () =>
          page.evaluate(async () => {
            const ngsw = (await (await fetch('/console/ngsw.json')).json()) as {
              assetGroups: {urls: string[]}[];
            };
            const urls = ngsw.assetGroups.flatMap(group => group.urls);
            return (await Promise.all(urls.map(url => caches.match(url)))).every(Boolean);
          }),
        {timeout: 30_000}
      )
      .toBe(true);
    await context.unrouteAll();

    // Losing the network shows the banner; a navigation then opens from the cached bundle.
    await context.setOffline(true);
    await expect(page.locator('lk-offline-banner').getByText(/offline/i)).toBeVisible();
    await page.goto('/console/goals');
    await expect(page.getByRole('heading', {level: 1, name: 'Goals'})).toBeVisible();
  });
});
