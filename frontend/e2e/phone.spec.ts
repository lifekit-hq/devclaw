import {readFileSync} from 'node:fs';
import {join} from 'node:path';

import {expect, type Page, test} from '@playwright/test';

// The console against lifekit-common's phone contract (docs/PHONE-CONTRACT.md, "The phone
// standard"). The library's own suite runs on its stories, so the console states the rules that
// belong to an app: content clears the tab bar, notices sit in flow, nothing overflows sideways,
// and a failed load shows the error, never a blank page.
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(join(__dirname, 'fixtures', `${name}.json`), 'utf8'));

const FEEDS: Record<string, string> = {
  '/goals.json': 'goals',
  '/projects.json': 'projects',
  '/verdicts.json': 'verdicts',
  '/control.json': 'control',
  '/oauth2/userinfo': 'userinfo',
};

const PHONES = [375, 390, 402];
const PAGES = ['needs-you', 'goals', 'projects', 'verdicts'];

async function serve(page: Page, failGoals = false): Promise<void> {
  await page.route(
    url => url.pathname in FEEDS,
    route => {
      const path = new URL(route.request().url()).pathname;
      if (failGoals && path === '/goals.json') {
        return route.fulfill({status: 500, json: {error: 'the goals read failed'}});
      }
      return route.fulfill({json: fixture(FEEDS[path])});
    }
  );
  await page.route('**/api/me/preferences', route =>
    route.fulfill({json: {appearance: {theme: 'system'}}})
  );
}

const tabBar = (page: Page) => page.getByRole('navigation', {name: 'Primary'});

for (const width of PHONES) {
  test.describe(`phone ${width}`, () => {
    test.use({viewport: {width, height: 700}});

    for (const name of PAGES) {
      test(`${name}: no sideways scroll, the last content clears the tab bar`, async ({page}) => {
        await serve(page);
        await page.goto(`/console/${name}`);
        await expect(page.getByRole('heading', {level: 1})).toBeVisible();
        await expect(tabBar(page)).toBeVisible();
        await expect(page.locator('cmn-skeleton')).toHaveCount(0);

        expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(
          width
        );
        const gap = await page.evaluate(() => {
          const main = document.querySelector('main') as HTMLElement;
          main.scrollTop = main.scrollHeight;
          const bar = document.querySelector('nav[aria-label="Primary"]') as HTMLElement;
          const leaves = Array.from(main.querySelectorAll('*')).filter(
            el => el.children.length === 0 && el.getBoundingClientRect().height > 0
          );
          const lowest = Math.max(...leaves.map(el => el.getBoundingClientRect().bottom));
          return bar.getBoundingClientRect().top - lowest;
        });
        expect(gap).toBeGreaterThanOrEqual(0);
      });
    }

    test('the offline notice sits between the top bar and the tab bar', async ({page, context}) => {
      await serve(page);
      await page.goto('/console/goals');
      await expect(page.getByRole('heading', {level: 1, name: 'Goals'})).toBeVisible();
      await context.setOffline(true);
      await page.evaluate(() => window.dispatchEvent(new Event('offline')));
      const banner = page.locator('lk-offline-banner');
      await expect(banner.getByText(/offline/i)).toBeVisible();
      const box = (await banner.boundingBox())!;
      const bar = (await tabBar(page).boundingBox())!;
      const top = (await page.locator('header').first().boundingBox())!;
      expect(box.y).toBeGreaterThanOrEqual(top.y + top.height - 1);
      expect(box.y + box.height).toBeLessThanOrEqual(bar.y + 1);
    });

    test('a failed first load shows the error and a retry, not a blank page', async ({page}) => {
      await serve(page, true);
      await page.goto('/console/goals');
      await expect(page.getByRole('alert')).toContainText('the goals read failed');
      await expect(page.getByRole('button', {name: /retry/i})).toBeVisible();
    });
  });
}
