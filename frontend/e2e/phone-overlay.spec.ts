import {expect, type Locator, type Page, test} from '@playwright/test';

// The phone overlay shell on an installed-PWA phone, in the shape of finance-sentry's
// frontend/e2e/phone-overlay.spec.ts. phone.spec.ts covers what a PAGE owes the shell (content
// clears the bar, no sideways scroll) at three widths with no insets; this spec covers what the
// SHELL owes the screen when the safe-area insets are real: the tab bar sits on the bottom edge,
// pads the home indicator, and the window never scrolls.

// iPhone 17 (CSS px) and its Dynamic Island / home-indicator insets.
const PHONE = {width: 402, height: 874};
const INSET_TOP = 62;
const INSET_BOTTOM = 34;
const SCROLL_PROBE = 500;

/** @lifekit-hq/ui's tab bar: 49pt of items, plus the home-indicator inset it pads for. */
const TAB_BAR_HEIGHT = 49;

const TABS = ['Needs you', 'Goals', 'Projects', 'Verdicts', 'More'];
const ROUTES = ['needs-you', 'goals', 'projects', 'verdicts', 'more'];

// iOS 26 home-screen web app: the page's viewport starts under the status bar while viewport
// units still resolve to the full screen, so anything sized by them overflows by the inset. The
// shell is pinned with `fixed inset-0` precisely so it does not.
//
// Inflating the viewport-unit utilities is finance-sentry's simulation of that shape, and it is
// kept here — but on its own it only bites the named utilities, and today the console's bundle
// carries none of them, so it would pass over a regression written as `h-[100dvh]`, a component
// stylesheet or an inline style. `expectNoViewportUnitHeights` is the general half: it reads what
// the shell is ACTUALLY sized by, in any of those forms.
const VIEWPORT_UNITS_TALLER_BY_INSET = `
  .h-screen, .h-dvh, .h-svh, .h-lvh { height: calc(100dvh + ${INSET_TOP}px) !important; }
  .min-h-screen, .min-h-dvh, .min-h-svh, .min-h-lvh { min-height: calc(100dvh + ${INSET_TOP}px) !important; }
`;

async function box(
  locator: Locator
): Promise<{x: number; y: number; width: number; height: number}> {
  const b = await locator.boundingBox();
  if (!b) {
    throw new Error('element has no box');
  }
  return b;
}

// Installed-PWA shape: the status bar and home indicator are drawn under, so the safe-area insets
// are non-zero. Chromium can simulate them through CDP.
async function simulateInsets(page: Page, top: number, bottom: number): Promise<void> {
  const cdp = await page.context().newCDPSession(page);
  await cdp.send('Emulation.setSafeAreaInsetsOverride', {insets: {top, bottom, left: 0, right: 0}});
}

/** The feeds are answered by e2e/serve-dist.mjs; a page is ready when its title and rows are in. */
async function openTab(
  page: Page,
  route: string,
  insets: {top: number; bottom: number}
): Promise<void> {
  await page.goto(`/console/${route}`);
  await simulateInsets(page, insets.top, insets.bottom);
  await page.reload();
  await expect(page.getByRole('heading', {level: 1})).toBeVisible();
  await expect(page.locator('cmn-skeleton')).toHaveCount(0);
}

// The shell never scrolls: html/body fit the viewport, a scroll attempt leaves the window at 0,
// and everything that overflows and scrolls lives in the content region (main).
async function expectShellNeverScrolls(page: Page): Promise<void> {
  const result = await page.evaluate(probe => {
    const root = document.documentElement;
    const body = document.body;
    window.scrollTo(0, probe);
    const scrollers = Array.from(document.querySelectorAll('*'))
      .filter(el => {
        const overflowY = getComputedStyle(el).overflowY;
        return (
          (overflowY === 'auto' || overflowY === 'scroll') && el.scrollHeight > el.clientHeight
        );
      })
      .map(el => (el.tagName === 'MAIN' ? 'main' : el.closest('main') ? 'in-main' : 'outside'));
    return {
      rootOverflow: root.scrollHeight - root.clientHeight,
      bodyOverflow: body.scrollHeight - body.clientHeight,
      windowScrollY: window.scrollY,
      scrollers,
    };
  }, SCROLL_PROBE);
  expect(result.rootOverflow).toBeLessThanOrEqual(0);
  expect(result.bodyOverflow).toBeLessThanOrEqual(0);
  expect(result.windowScrollY).toBe(0);
  expect(result.scrollers).not.toContain('outside');
}

// Nothing on the page takes its height from a viewport unit, in any form: a utility class, an
// arbitrary value, a component stylesheet or an inline style. That is the property the iOS 26
// home-screen shape punishes. The scan starts at `body`, so it covers the console's own wrappers
// around the shell (`dc-root`, `dc-app-shell`) as well as the shell and the page inside it.
// `max-height` is not included: a dialog or drawer panel legitimately caps itself at `100dvh`
// minus the insets.
async function expectNoViewportUnitHeights(page: Page): Promise<void> {
  const offenders = await page.evaluate(() => {
    const SIZING = ['height', 'min-height'];
    const UNIT = /\b\d*\.?\d+(dvh|svh|lvh|vh)\b/;
    const root = document.body;
    const found: string[] = [];
    if (!document.querySelector('cmn-app-layout')) {
      return ['no cmn-app-layout'];
    }
    const label = (el: Element): string =>
      `${el.tagName.toLowerCase()}${el.className ? `.${String(el.className).split(/\s+/)[0]}` : ''}`;

    for (const el of [root, ...Array.from(root.querySelectorAll('*'))]) {
      const inline = (el as HTMLElement).style;
      for (const property of SIZING) {
        if (UNIT.test(inline.getPropertyValue(property))) {
          found.push(`${label(el)} inline ${property}: ${inline.getPropertyValue(property)}`);
        }
      }
    }
    for (const sheet of Array.from(document.styleSheets)) {
      let rules: CSSRule[];
      try {
        rules = Array.from(sheet.cssRules);
      } catch {
        continue; // a cross-origin sheet; the bundle's own are same-origin
      }
      for (const rule of rules) {
        if (!(rule instanceof CSSStyleRule)) {
          continue;
        }
        const declared = SIZING.filter(property =>
          UNIT.test(rule.style.getPropertyValue(property))
        );
        if (declared.length === 0) {
          continue;
        }
        let matched: Element[] = [];
        try {
          matched = Array.from(document.querySelectorAll(rule.selectorText));
        } catch {
          continue; // a selector this browser cannot parse standalone
        }
        for (const el of matched) {
          if (el === root || root.contains(el)) {
            found.push(`${label(el)} via \`${rule.selectorText}\` ${declared.join(', ')}`);
          }
        }
      }
    }
    return found;
  });
  expect(offenders).toEqual([]);
}

// The tab bar is edge to edge on the bottom edge of the screen, 49pt of items plus the
// home-indicator inset it pads for. Every label and icon sits fully above that inset, and no
// label is squeezed: its box is as tall as its text.
async function expectTabBarOnTheBottomEdge(page: Page): Promise<void> {
  const nav = page.locator('cmn-bottom-tab-bar nav');
  await expect(nav).toBeVisible();
  const navBox = await box(nav);
  expect(navBox.x).toBe(0);
  expect(navBox.width).toBeCloseTo(PHONE.width, 0);
  expect(navBox.y).toBeGreaterThanOrEqual(0);
  expect(navBox.y + navBox.height).toBeCloseTo(PHONE.height, 0);
  expect(navBox.height).toBeCloseTo(TAB_BAR_HEIGHT + INSET_BOTTOM, 0);

  const labels = await nav.locator('button span.font-label').evaluateAll(els =>
    els.map(el => {
      const r = el.getBoundingClientRect();
      return {
        text: el.textContent?.trim(),
        top: r.top,
        bottom: r.bottom,
        clientHeight: el.clientHeight,
        scrollHeight: el.scrollHeight,
      };
    })
  );
  expect(labels.map(label => label.text)).toEqual(TABS);
  for (const label of labels) {
    expect(label.clientHeight).toBe(label.scrollHeight);
    expect(label.top).toBeGreaterThanOrEqual(navBox.y);
    expect(label.bottom).toBeLessThanOrEqual(PHONE.height - INSET_BOTTOM);
  }
  const icons = await nav
    .locator('button cmn-icon')
    .evaluateAll(els => els.map(el => el.getBoundingClientRect().bottom));
  expect(icons).toHaveLength(labels.length);
  for (const bottom of icons) {
    expect(bottom).toBeLessThanOrEqual(PHONE.height - INSET_BOTTOM);
  }
}

test.describe('phone overlay shell', () => {
  test.use({viewport: PHONE, hasTouch: true, isMobile: true});

  for (const route of ROUTES) {
    test(`${route}: the tab bar sits on the bottom edge and the window never scrolls`, async ({
      page,
    }) => {
      await openTab(page, route, {top: INSET_TOP, bottom: INSET_BOTTOM});

      await expectTabBarOnTheBottomEdge(page);
      await expectShellNeverScrolls(page);

      // Main spans the whole frame: the bars overlay it rather than sitting in flow beside it.
      const main = await box(page.locator('cmn-app-layout main'));
      const topBar = await box(page.locator('cmn-top-bar'));
      const tabBar = await box(page.locator('cmn-bottom-tab-bar'));
      expect(main.y).toBeLessThanOrEqual(topBar.y);
      expect(main.y + main.height).toBeGreaterThanOrEqual(tabBar.y + tabBar.height);
      expect(tabBar.y + tabBar.height).toBeCloseTo(PHONE.height, 0);
    });

    test(`${route}: the shell does not depend on viewport units (iOS 26 web app shape)`, async ({
      page,
    }) => {
      await openTab(page, route, {top: 0, bottom: INSET_BOTTOM});
      await expectNoViewportUnitHeights(page);
      await page.addStyleTag({content: VIEWPORT_UNITS_TALLER_BY_INSET});

      await expectTabBarOnTheBottomEdge(page);
      await expectShellNeverScrolls(page);
    });
  }

  // Each notice carries `cmnAppLayoutNotice` itself, so each is its own child of the shell's
  // notices column rather than sharing one wrapper. The shell measures that column and publishes
  // its height as `--cmn-sticky-top`, the offset a page's own sticky header sticks at, so the
  // column's children are the shell's contract and not an implementation detail.
  //
  // (Height alone would NOT pin this: every `lk-*` notice sets `:host {display: block}`, so a
  // wrapper collapses to 0 too and the test would pass either way.)
  test('each notice is its own child of the notices column', async ({page}) => {
    await openTab(page, 'goals', {top: INSET_TOP, bottom: INSET_BOTTOM});

    const column = await page.evaluate(() => {
      const main = document.querySelector('cmn-app-layout main');
      const notices = main
        ? Array.from(main.children).find(el =>
            el.querySelector('lk-offline-banner, lk-update-prompt, lk-install-hint')
          )
        : undefined;
      if (!notices) {
        return null;
      }
      return {
        children: Array.from(notices.children).map(el => el.tagName.toLowerCase()),
        height: notices.getBoundingClientRect().height,
        stickyTop: getComputedStyle(main as HTMLElement)
          .getPropertyValue('--cmn-sticky-top')
          .trim(),
      };
    });
    expect(column).not.toBeNull();
    expect(column?.children).toEqual(['lk-offline-banner', 'lk-update-prompt', 'lk-install-hint']);
    // Inactive, they cost the page nothing: no band, and a page's sticky header starts at the top.
    expect(column?.height).toBe(0);
    expect(column?.stickyTop).toBe('0px');
  });
});
