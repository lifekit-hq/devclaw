import {execFile, execFileSync} from 'node:child_process';
import {readFileSync} from 'node:fs';
import {join} from 'node:path';
import {promisify} from 'node:util';

import AxeBuilder from '@axe-core/playwright';
import {chromium, expect, type Page, test} from '@playwright/test';

// The design and accessibility gate: every route of the fixture build, in light and dark, at a
// desktop and a phone viewport, through the impeccable URL scan (lifekit-common's design
// detector) and axe-core (WCAG 2.2 AA). A primary impeccable finding, or a serious or critical
// axe violation, fails unless quality-exceptions.json accepts it with a reason; `match` narrows
// an entry to findings whose snippet (impeccable) or node HTML (axe) contains it, and an
// impeccable entry covers at most `max` findings per scan (default 1). Fix findings in the page,
// not here.

const ROUTES = [
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
// The account's saved theme is the server's (playwright.config.ts starts one per theme), so the
// scan's own browser renders the same theme as the axe pass.
const THEMES = {light: 'http://127.0.0.1:4417', dark: 'http://127.0.0.1:4418'};
const VIEWPORTS = {desktop: {width: 1280, height: 800}, phone: {width: 390, height: 844}};
const AXE_TAGS = ['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'];
const BLOCKING_IMPACTS = new Set(['serious', 'critical']);
const FINDINGS_EXIT = 2;
const SCAN_TIMEOUT_MS = 120_000;

interface Finding {
  antipattern: string;
  snippet: string;
}

interface Accepted {
  rule: string;
  routes: string[];
  match?: string;
  max?: number;
  reason: string;
}

const exceptions = JSON.parse(readFileSync(join(__dirname, 'quality-exceptions.json'), 'utf8')) as {
  impeccable: Accepted[];
  axe: Accepted[];
};

const covers = (entry: Accepted, route: string): boolean =>
  entry.routes.includes('*') || entry.routes.includes(route);

const ready = async (page: Page, url: string, theme: string): Promise<void> => {
  await page.goto(url);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('h1')).toBeVisible();
  await expect(page.locator('cmn-skeleton')).toHaveCount(0);
  await page.evaluate(() => document.fonts.ready);
};

// ROUTES stays in step with the router. The router's table is not importable into Playwright's
// Node loader (Angular ships ESM chunks it cannot link), so the built app answers instead: every
// listed route resolves to itself (the `**` redirect would change the URL), and every console
// link on those pages leads to a route of the same shape as one listed.
test('the gate covers every route of the console', async ({page}) => {
  const shape = (route: string): string => route.replace(/\/[^/]+$/, '/:id');
  const scanned = new Set(ROUTES.map(shape));
  const linked = new Set<string>();
  for (const route of ROUTES) {
    await ready(page, `${THEMES.light}/console/${route}`, 'light');
    expect(new URL(page.url()).pathname).toBe(`/console/${route}`);
    const hrefs = await page
      .locator('a[href]')
      .evaluateAll(links => links.map(link => (link as HTMLAnchorElement).href));
    for (const href of hrefs) {
      const url = new URL(href);
      const path = url.pathname.replace(/^\/console\/?/, '');
      if (url.origin === THEMES.light && url.pathname.startsWith('/console') && path) {
        linked.add(shape(path));
      }
    }
  }
  expect([...linked].filter(path => !scanned.has(path))).toEqual([]);
});

test.describe('impeccable', () => {
  test.describe.configure({mode: 'parallel'});
  let engine = '';

  test.beforeAll(() => {
    engine = execFileSync('sh', [join(__dirname, 'impeccable-engine.sh')], {
      encoding: 'utf8',
    }).trim();
  });

  for (const [theme, origin] of Object.entries(THEMES)) {
    for (const [name, {width, height}] of Object.entries(VIEWPORTS)) {
      for (const route of ROUTES) {
        test(`${route} ${theme} ${name}`, async () => {
          test.setTimeout(SCAN_TIMEOUT_MS);
          const url = `${origin}/console/${route}`;
          const scan = await promisify(execFile)(
            engine,
            [
              'detect',
              '--json',
              '--no-config',
              '--no-advisory',
              '--viewport',
              `${width}x${height}`,
              url,
            ],
            {env: {...process.env, IMPECCABLE_BROWSER: chromium.executablePath()}}
          ).then(
            ({stdout}) => ({code: 0, stdout}),
            (e: {code?: number; stdout?: string; stderr?: string}) => {
              if (e.code !== FINDINGS_EXIT) {
                throw new Error(`impeccable failed on ${url} (exit ${e.code}): ${e.stderr}`);
              }
              return {code: e.code, stdout: e.stdout ?? ''};
            }
          );
          const findings = JSON.parse(scan.stdout) as Finding[];
          expect(findings.length > 0).toBe(scan.code === FINDINGS_EXIT);

          const used = new Map<Accepted, number>();
          const blocking = findings.filter(f => {
            const entry = exceptions.impeccable.find(
              e =>
                e.rule === f.antipattern &&
                covers(e, route) &&
                (!e.match || f.snippet.includes(e.match))
            );
            if (!entry) return true;
            const count = (used.get(entry) ?? 0) + 1;
            used.set(entry, count);
            return count > (entry.max ?? 1);
          });
          expect(blocking.map(f => `${f.antipattern}: ${f.snippet}`)).toEqual([]);
        });
      }
    }
  }
});

test.describe('axe', () => {
  for (const [theme, origin] of Object.entries(THEMES)) {
    for (const [name, viewport] of Object.entries(VIEWPORTS)) {
      test.describe(`${theme} ${name}`, () => {
        test.use({viewport, colorScheme: theme as 'light' | 'dark'});
        for (const route of ROUTES) {
          test(route, async ({page}) => {
            await ready(page, `${origin}/console/${route}`, theme);
            const {violations} = await new AxeBuilder({page}).withTags(AXE_TAGS).analyze();
            const blocking = violations
              .filter(v => BLOCKING_IMPACTS.has(v.impact ?? ''))
              .map(v => ({
                rule: v.id,
                impact: v.impact,
                help: v.help,
                nodes: v.nodes
                  .filter(
                    n =>
                      !exceptions.axe.some(
                        e =>
                          e.rule === v.id &&
                          covers(e, route) &&
                          (!e.match || n.html.includes(e.match))
                      )
                  )
                  .map(n => n.target.join(' ')),
              }))
              .filter(v => v.nodes.length > 0);
            expect(blocking).toEqual([]);
          });
        }
      });
    }
  }
});
