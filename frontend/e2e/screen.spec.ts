import {checkScreen, formatScreen, SCREEN_VIEWPORTS} from '@lifekit-hq/tokens/screen';
import {expect, test} from '@playwright/test';

import {ready, ROUTES} from './routes';

// The screen check (lifekit-common's rules C0 to C10): every route of the console is built on a
// page template, shares one left edge, clears its title and keeps controls, surfaces and labels to
// the design rules, at the phone and the desktop size once its data has settled. A finding is
// fixed in the page template or component in lifekit-common, never ignored here.
for (const [size, viewport] of Object.entries(SCREEN_VIEWPORTS)) {
  test.describe(`screen ${size}`, () => {
    test.use(viewport);

    for (const {name, url} of ROUTES) {
      test(name, async ({page}) => {
        await page.goto(url);
        await ready(page);
        const result = await checkScreen(page);
        expect(result.findings, formatScreen(result)).toEqual([]);
      });
    }
  });
}
