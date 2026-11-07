import {readFileSync} from 'node:fs';
import {join} from 'node:path';

import {expect, type Page, test} from '@playwright/test';

// No backend runs here: every feed is answered from fixtures/, which generate.py writes from the
// host's own routes over a seeded store, so the page reads exactly what the host serves.
const fixture = (name: string): unknown =>
  JSON.parse(readFileSync(join(__dirname, 'fixtures', `${name}.json`), 'utf8'));

const FEEDS = fixture('feeds') as Record<string, string>;

async function serveFeeds(page: Page): Promise<void> {
  await page.route(
    url => url.pathname in FEEDS,
    route => route.fulfill({json: fixture(FEEDS[new URL(route.request().url()).pathname])})
  );
}

// The account's preferences live behind the gate's identity header; a fresh account has none.
async function servePreferences(page: Page, seen: unknown[] = []): Promise<void> {
  await page.route('**/api/me/preferences', route => {
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON();
      seen.push(body);
      return route.fulfill({json: body});
    }
    return route.fulfill({json: {appearance: {theme: 'system'}}});
  });
}

test.beforeEach(async ({page}) => {
  await serveFeeds(page);
  await servePreferences(page);
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

test('the decide field’s keyboard key says send and sends', async ({page}) => {
  const posted: unknown[] = [];
  await page.route('**/goals/g-running/decide', route => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({json: {ok: true}});
  });
  await page.goto('/console/goals/g-running');

  const field = page.getByRole('textbox', {name: 'Your decision'});
  await expect(field).toHaveAttribute('enterkeyhint', 'send');
  await field.fill('Exclude them for now');
  await field.press('Enter');

  await expect.poll(() => posted).toEqual([{text: 'Exclude them for now'}]);
});

test('the goal page decides in the owner’s own words and shows a refusal', async ({page}) => {
  const posted: unknown[] = [];
  await page.route('**/goals/g-running/decide', route => {
    posted.push(route.request().postDataJSON());
    return route.fulfill({status: 400, json: {error: 'goal is closed'}});
  });
  await page.goto('/console/goals/g-running');

  await expect(page.getByRole('heading', {level: 1, name: 'Goal g-running'})).toBeVisible();
  await expect(page.getByText('Speed up the search index rebuild')).toBeVisible();
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
  await page.getByRole('radio', {name: /Closed/}).click();
  await expect(page.getByText('Fix the timezone drift in daily digests')).toBeVisible();
  await expect(page.getByText('Add CSV export to the reports page')).toHaveCount(0);

  await page.getByRole('radio', {name: /Open/}).click();
  await page.getByText('Speed up the search index rebuild').click();
  await expect(page).toHaveURL(/\/console\/goals\/g-running$/);
  await expect(page.getByRole('region', {name: 'Sessions'})).toContainText('t-runnin');
});

test('the filter shows no counts until the goals have loaded', async ({page}) => {
  let release = (): void => undefined;
  const held = new Promise<void>(resolve => (release = resolve));
  await page.route('**/goals.json', async route => {
    await held;
    await route.fulfill({json: fixture('goals')});
  });
  await page.goto('/console/goals');

  const filter = page.getByRole('radiogroup', {name: 'Filter goals'});
  await expect(filter.getByRole('radio')).toHaveText(['Open', 'Blocked', 'Closed', 'All']);
  release();
  await expect(filter.getByRole('radio', {name: /^Open \d+$/})).toBeVisible();
  await expect(filter.getByRole('radio', {name: /^All \d+$/})).toBeVisible();
});

test('a closed goal shows its verdict row and no decide form', async ({page}) => {
  await page.goto('/console/goals/g-done');

  await expect(page.getByRole('region', {name: 'Verdicts'})).toContainText(
    'Digests are computed in UTC and the drift test pins it.'
  );
  await expect(page.getByRole('region', {name: 'Decide'})).toHaveCount(0);
});

test('Projects and a project page list the goals', async ({page}) => {
  await page.goto('/console/projects');

  await expect(page.getByRole('heading', {level: 1, name: 'Projects'})).toBeVisible();
  await page.getByRole('link', {name: 'widgets'}).first().click();

  await expect(page).toHaveURL(/\/console\/projects\/widgets$/);
  await expect(page.getByRole('heading', {level: 1, name: 'Project widgets'})).toBeVisible();
  await expect(page.getByRole('table')).toContainText('Add CSV export to the reports page');
});

test('a session page shows its exit, parts and events', async ({page}) => {
  await page.goto('/console/sessions/t-deliver-0001');

  await expect(page.getByRole('heading', {level: 1})).toContainText('Session');
  await expect(page.getByRole('region', {name: 'Exit'})).toContainText('DELIVERED');
  await expect(page.getByRole('region', {name: 'Delivery'})).toContainText('devclaw/g-blocked');
  await expect(page.getByRole('region', {name: 'Events'})).toContainText('session_exit');
});

test('a session opened directly still has a way up', async ({page}) => {
  await page.goto('/console/sessions/t-deliver-0001');

  await expect(page.getByRole('heading', {level: 1})).toContainText('Session');
  await page.getByRole('button', {name: 'Back'}).click();
  await expect(page).toHaveURL(/\/console\/goals$/);
});

test('Verdicts rows push a page with the review clause by clause', async ({page}) => {
  await page.goto('/console/verdicts');

  await expect(page.getByRole('heading', {level: 1, name: 'Verdicts'})).toBeVisible();
  const clause = page.getByText("Daily digests use the subscriber's timezone");
  await expect(clause).toHaveCount(0);
  await page.getByText('Digests are computed in UTC and the drift test pins it.').first().click();

  await expect(page).toHaveURL(/\/console\/verdicts\/t-review-0005$/);
  await expect(page.getByRole('heading', {level: 1, name: 'Verdict'})).toBeVisible();
  await expect(clause).toBeVisible();
  await expect(page.getByText('tests/test_digest.py::test_tz_boundary')).toBeVisible();
  await page.getByRole('button', {name: 'Back'}).click();
  await expect(page).toHaveURL(/\/console\/verdicts$/);
});

test('a verdict that is not in the feed says so', async ({page}) => {
  await page.goto('/console/verdicts/t-gone');

  await expect(page.getByRole('alert')).toContainText('No such verdict: t-gone');
});

test('Settings shows the account, the run window and posts a hold', async ({page}) => {
  let held = false;
  await page.route('**/control/pause', route => {
    held = true;
    return route.fulfill({json: {operatorHold: {on: true, reason: 'held from the console'}}});
  });
  await page.goto('/console/settings');

  await expect(page.getByRole('heading', {level: 1, name: 'Settings'})).toBeVisible();
  await expect(page.getByRole('region', {name: 'Account'})).toContainText('owner@example.com');
  const window = page.getByRole('region', {name: 'Run window'});
  await expect(window.getByRole('textbox', {name: 'Start'})).toHaveAttribute('type', 'time');
  await expect(window.getByRole('textbox', {name: 'Start'})).toHaveValue('22:00');
  await expect(window.getByRole('textbox', {name: 'End'})).toHaveAttribute('type', 'time');
  await expect(window.getByRole('textbox', {name: 'Time zone'})).toHaveValue('Europe/Dublin');
  await page.getByRole('button', {name: 'Hold all new sessions'}).click();
  await expect.poll(() => held).toBe(true);
});

test('Appearance applies at once and is saved to the account', async ({page}) => {
  const saved: unknown[] = [];
  await servePreferences(page, saved);
  await page.goto('/console/settings');

  await page.getByRole('radio', {name: 'Dark'}).click();

  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect.poll(() => JSON.stringify(saved)).toContain('"theme":"dark"');
});

test('the shell fits a phone: More holds the account and Sign out', async ({page}, testInfo) => {
  await page.setViewportSize({width: 375, height: 700});
  await page.goto('/console/needs-you');

  await expect(page.getByText('Should the export include archived reports?')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth)).toBeLessThanOrEqual(375);
  await page.screenshot({path: testInfo.outputPath('phone-needs-you.png'), fullPage: true});
  await expect(page.getByRole('button', {name: 'Account menu'})).toHaveCount(0);

  await page.goto('/console/more');
  await expect(page.getByRole('heading', {level: 1, name: 'More'})).toBeVisible();
  await expect(page.getByText('owner@example.com')).toBeVisible();
  await page.screenshot({path: testInfo.outputPath('phone-more.png'), fullPage: true});
  const signOut = page.waitForRequest(/\/oauth2\/sign_out\?rd=/);
  await page.route('**/oauth2/sign_out**', route => route.fulfill({body: 'signed out'}));
  await page.getByRole('button', {name: 'Sign out'}).click();
  await signOut;
});

test('the wide shell signs out from the account menu', async ({page}) => {
  await page.setViewportSize({width: 1280, height: 800});
  await page.goto('/console/needs-you');

  await page.getByRole('button', {name: 'Account menu'}).click();
  await expect(page.getByRole('menuitem', {name: 'Settings'})).toBeVisible();
  const signOut = page.waitForRequest(/\/oauth2\/sign_out\?rd=/);
  await page.route('**/oauth2/sign_out**', route => route.fulfill({body: 'signed out'}));
  await page.getByRole('menuitem', {name: 'Sign out'}).click();
  await signOut;
});
