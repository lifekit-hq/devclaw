import {type Page} from '@playwright/test';

// The console's routes as the fixture build answers them: the one list the screen check, the
// route scan and the baselines all walk, so a route is covered by all three or by none.
// `name` is the route's id in test titles, exceptions and baseline file names (`<name>-<size>-
// <scheme>-chromium-linux.png`); `path` is what the router serves it at.
export const PATHS = [
  'needs-you',
  'goals',
  'goals/g-blocked',
  'goals/g-running',
  'goals/g-done',
  'projects',
  'projects/widgets',
  'sessions/t-deliver-0001',
  'verdicts',
  'more',
  'settings',
];

export const ROUTES = PATHS.map(path => ({
  name: path.replaceAll('/', '-'),
  path,
  url: `/console/${path}`,
}));

// The large title is the route's own and is drawn the moment the route activates; the data
// behind it lands later, which the fixtures wait out themselves (no skeleton, no busy region).
export const ready = (page: Page): Promise<void> => page.locator('h1').first().waitFor();
