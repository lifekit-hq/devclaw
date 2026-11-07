import {defineRouteScans} from '@lifekit-hq/tokens/screen';
import {expect, test} from '@playwright/test';

import {PATHS, ready, ROUTES} from './routes';

// The design and accessibility gate: every route of the fixture build, in light and dark, at the
// phone (390) and desktop (1280) size, through lifekit-common's route scan (the impeccable design
// judge and axe-core, WCAG 2.2 AA). The fixture settles, freezes and judges each screen and
// pins the judge's engine; this file only lists the routes. The app accepts no findings: one in a
// library component, token or page template is fixed in lifekit-common (open an issue there), and
// one in the app's own page is fixed in the page.
defineRouteScans({test, expect}, {routes: ROUTES.map(({name, url}) => ({name, url, ready}))});

// ROUTES stays in step with the router. The router's table is not importable into Playwright's
// Node loader (Angular ships ESM chunks it cannot link), so the built app answers instead: every
// listed route resolves to itself (the `**` redirect would change the URL), and every console
// link on those pages leads to a route of the same shape as one listed.
test('the gate covers every route of the console', async ({page}) => {
  const shape = (route: string): string => route.replace(/\/[^/]+$/, '/:id');
  const scanned = new Set(PATHS.map(shape));
  const linked = new Set<string>();
  for (const path of PATHS) {
    await page.goto(`/console/${path}`);
    await ready(page);
    expect(new URL(page.url()).pathname).toBe(`/console/${path}`);
    const hrefs = await page
      .locator('a[href]')
      .evaluateAll(links => links.map(link => (link as HTMLAnchorElement).href));
    for (const href of hrefs) {
      const url = new URL(href);
      const route = url.pathname.replace(/^\/console\/?/, '');
      if (
        url.origin === new URL(page.url()).origin &&
        url.pathname.startsWith('/console') &&
        route
      ) {
        linked.add(shape(route));
      }
    }
  }
  expect([...linked].filter(route => !scanned.has(route))).toEqual([]);
});
