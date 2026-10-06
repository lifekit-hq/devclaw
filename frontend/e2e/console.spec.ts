import {readFileSync} from 'node:fs';
import {join} from 'node:path';

import {expect, type Page, test} from '@playwright/test';

// No backend runs here: every feed is answered from fixtures/, which generate.py writes from the
// host's own routes over a seeded store, so the page reads exactly what the host serves.
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(join(__dirname, 'fixtures', `${name}.json`), 'utf8'));

const FEEDS: Record<string, string> = {
  '/goals.json': 'goals',
  '/goals/g-blocked.json': 'goal-blocked',
  '/goals/g-running.json': 'goal-running',
  '/goals/g-done.json': 'goal-done',
  '/projects.json': 'projects',
  '/projects/widgets.json': 'project-widgets',
  '/tasks/t-deliver-0001.json': 'task-deliver',
  '/tasks/t-deliver-0001/events.json': 'task-deliver-events',
  '/verdicts.json': 'verdicts',
  '/control.json': 'control',
  '/oauth2/userinfo': 'userinfo',
};

async function serveFeeds(page: Page): Promise<void> {
  await page.route(
    url => url.pathname in FEEDS,
    route => route.fulfill({json: fixture(FEEDS[new URL(route.request().url()).pathname])})
  );
}

test.beforeEach(async ({page}) => {
  await serveFeeds(page);
});

test('Needs you lists the blocked goal with the session options', async ({page}) => {
  await page.goto('/');

  await expect(page).toHaveURL(/\/console\/needs-you$/);
  await expect(page.getByRole('heading', {level: 1, name: 'Needs you'})).toBeVisible();
  await expect(page.getByRole('button', {name: '1 Needs you'})).toBeVisible();
  await expect(page.getByText('Should the export include archived reports?')).toBeVisible();
  const options = page.getByRole('group', {name: "The session's options"});
  await expect(options.getByRole('button')).toHaveText([
    /Include them behind a checkbox\s*recommended/,
    'Exclude archived reports',
  ]);
});

test('an option posts its full text on the decide route', async ({page}) => {
  let posted: unknown = null;
  await page.route('**/goals/g-blocked/decide', route => {
    posted = route.request().postDataJSON();
    return route.fulfill({json: {ok: true}});
  });
  await page.goto('/console/needs-you');

  await page.getByRole('button', {name: /Include them behind a checkbox/}).click();

  await expect.poll(() => posted).toEqual({text: 'Include them behind a checkbox'});
});

test('the goal page decides in the owner’s own words and shows a refusal', async ({page}) => {
  const posted: unknown[] = [];
  await page.route('**/goals/g-blocked/decide', route => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({status: 400, json: {error: 'goal is closed'}});
  });
  await page.goto('/console/goals/g-blocked');

  await expect(
    page.getByRole('heading', {level: 1, name: 'Add CSV export to the reports page'})
  ).toBeVisible();
  const decide = page.getByRole('region', {name: 'Decide'});
  await decide.getByRole('textbox', {name: 'Your decision'}).fill('  Exclude them for now  ');
  await decide.getByRole('button', {name: 'Decide'}).click();

  await expect.poll(() => posted).toEqual([{text: 'Exclude them for now'}]);
  await expect(decide.getByRole('alert')).toHaveText('goal is closed');
});

test('Goals filters open from closed and opens one', async ({page}) => {
  await page.goto('/console/goals');

  await expect(page.getByRole('heading', {level: 1, name: 'Goals'})).toBeVisible();
  await expect(page.getByText('Add CSV export to the reports page')).toBeVisible();
  await expect(page.getByText('Fix the timezone drift in daily digests')).toHaveCount(0);
  await page.getByRole('button', {name: /Closed/}).click();
  await expect(page.getByText('Fix the timezone drift in daily digests')).toBeVisible();
  await expect(page.getByText('Add CSV export to the reports page')).toHaveCount(0);

  await page.getByRole('button', {name: /Open/}).click();
  await page.getByText('Speed up the search index rebuild').click();
  await expect(page).toHaveURL(/\/console\/goals\/g-running$/);
  await expect(page.getByRole('region', {name: 'Sessions'})).toContainText('t-runnin');
});

test('a closed goal shows its verdict and no decide form', async ({page}) => {
  await page.goto('/console/goals/g-done');

  await expect(page.getByRole('region', {name: 'Verdicts'})).toContainText(
    "Daily digests use the subscriber's timezone"
  );
  await expect(page.getByRole('region', {name: 'Decide'})).toHaveCount(0);
});

test('Projects and a project page list the goals', async ({page}) => {
  await page.goto('/console/projects');

  await expect(page.getByRole('heading', {level: 1, name: 'Projects'})).toBeVisible();
  await page.getByRole('link', {name: 'widgets'}).first().click();

  await expect(page).toHaveURL(/\/console\/projects\/widgets$/);
  await expect(page.getByRole('heading', {level: 1, name: 'widgets'})).toBeVisible();
  await expect(page.getByRole('table')).toContainText('Add CSV export to the reports page');
});

test('a session page shows its exit, parts and events', async ({page}) => {
  await page.goto('/console/sessions/t-deliver-0001');

  await expect(page.getByRole('heading', {level: 1})).toContainText('Session');
  await expect(page.getByRole('region', {name: 'Exit'})).toContainText('DELIVERED');
  await expect(page.getByRole('region', {name: 'Delivery'})).toContainText('devclaw/g-blocked');
  await expect(page.getByRole('region', {name: 'Events'})).toContainText('session_exit');
});

test('Verdicts lists each review clause by clause', async ({page}) => {
  await page.goto('/console/verdicts');

  await expect(page.getByRole('heading', {level: 1, name: 'Verdicts'})).toBeVisible();
  const clause = page.getByText("Daily digests use the subscriber's timezone");
  await expect(clause).toBeHidden();
  await page.getByText('Digests are computed in UTC and the drift test pins it.').click();
  await expect(clause).toBeVisible();
});

test('Settings shows the run window and posts a hold', async ({page}) => {
  let held = false;
  await page.route('**/control/pause', route => {
    held = true;
    return route.fulfill({json: {operatorHold: {on: true, reason: 'held from the console'}}});
  });
  await page.goto('/console/settings');

  await expect(page.getByRole('heading', {level: 1, name: 'Settings'})).toBeVisible();
  const window = page.getByRole('region', {name: 'Run window'});
  await expect(window.getByRole('textbox', {name: 'Start'})).toHaveValue('22:00');
  await expect(window.getByRole('textbox', {name: 'Time zone'})).toHaveValue('Europe/Dublin');
  await page.getByRole('button', {name: 'Hold all new sessions'}).click();
  await expect.poll(() => held).toBe(true);
});

test('the shell fits a phone and signs out', async ({page}, testInfo) => {
  await page.setViewportSize({width: 375, height: 700});
  await page.goto('/console/needs-you');

  await expect(page.getByText('Should the export include archived reports?')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({path: testInfo.outputPath('phone-needs-you.png'), fullPage: true});

  await page.getByRole('button', {name: 'Account menu'}).click();
  await expect(page.getByRole('menuitem', {name: 'owner@example.com'})).toBeDisabled();
  const signOut = page.waitForRequest(/\/oauth2\/sign_out\?rd=/);
  await page.route('**/oauth2/sign_out**', route => route.fulfill({body: 'signed out'}));
  await page.getByRole('menuitem', {name: 'Sign out'}).click();
  await signOut;
});
