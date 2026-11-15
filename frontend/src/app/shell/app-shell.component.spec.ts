import {provideHttpClient} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {provideZonelessChangeDetection, signal} from '@angular/core';
import {TestBed} from '@angular/core/testing';
import {provideRouter, Router, withComponentInputBinding} from '@angular/router';
import {RouterTestingHarness} from '@angular/router/testing';
import {AppUpdateService} from '@lifekit-hq/core/pwa';
import {CmnPullRefreshService, CmnShellService, provideWhatsNew} from '@lifekit-hq/ui';

import {APP_ROUTES} from '../app.routes';

const WAITING_GOAL = {
  id: 'g1',
  objective: 'Ship it',
  attention: {
    kind: 'session',
    question: 'Which?',
    options: [],
    recommended: -1,
    default: '',
    since: 1,
    link: '',
    answered: null,
  },
};

const CONTROL = {
  operatorHold: {on: false, reason: ''},
  schedule: {enabled: false, start: '', end: '', tz: ''},
  pause: null,
  blocked: false,
};

// The test browser's width is not the app's: the sidebar and avatar need the wide mode and the
// More tab the phone one, so the mode is stated.
function shellMode(mode: 'phone' | 'sidebar'): Partial<CmnShellService> {
  return {
    mode: signal(mode),
    isPhone: signal(mode === 'phone'),
  } as unknown as Partial<CmnShellService>;
}

// jsdom has no matchMedia, which the install hint in the notice slot reads.
window.matchMedia ??= ((query: string) =>
  ({
    matches: false,
    media: query,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
  }) as unknown as MediaQueryList) as typeof window.matchMedia;

describe('AppShellComponent', () => {
  let http: HttpTestingController;
  let harness: RouterTestingHarness;

  const root = (): HTMLElement =>
    harness.routeNativeElement?.closest('dc-app-shell') as HTMLElement;
  const menuItems = (): (string | undefined)[] =>
    Array.from(document.querySelectorAll('[role=menuitem]')).map(i => i.textContent?.trim());

  /** Lets the feeds' first poll (a zero-delay timer) go out. */
  async function settle(): Promise<void> {
    await new Promise(resolve => setTimeout(resolve));
    await harness.fixture.whenStable();
  }

  /** Answers every open read of `url` (the badge and the page share one goals feed). */
  function flush(url: string, body: object | string, status = 200): void {
    for (const req of http.match(r => r.url === url)) {
      req.flush(body, {status, statusText: status === 200 ? 'OK' : 'Error'});
    }
  }

  async function start(mode: 'phone' | 'sidebar' = 'sidebar'): Promise<void> {
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        {provide: CmnShellService, useValue: shellMode(mode)},
        {
          provide: AppUpdateService,
          useValue: {updateReady: signal(false), reload: () => undefined},
        },
        provideRouter(APP_ROUTES, withComponentInputBinding()),
        provideHttpClient(),
        provideHttpClientTesting(),
        // The library's What's new, with no notes to fetch and a marker kept in memory.
        provideWhatsNew({
          version: '0.0.0',
          load: () => Promise.resolve({versions: []}),
          storage: {getItem: () => null, setItem: () => undefined},
        }),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    harness = await RouterTestingHarness.create();
  }

  it('opens on Needs you, badged with the goals waiting on the owner', async () => {
    await start();
    await harness.navigateByUrl('/');
    await settle();
    flush('/goals.json', [WAITING_GOAL]);
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/needs-you');
    const nav = Array.from(root().querySelectorAll('cmn-sidebar-nav button')).map(
      b => b.textContent?.replace(/\s+/g, ' ').trim() ?? ''
    );
    for (const label of ['Needs you', 'Goals', 'Projects', 'Verdicts']) {
      expect(nav.some(l => l.includes(label))).toBe(true);
    }
    expect(nav.find(l => l.includes('Needs you'))).toContain('1');
  });

  it.each([
    ['/goals', '/goals'],
    ['/goals/g1', '/goals/g1'],
    ['/projects', '/projects'],
    ['/sessions/t1', '/sessions/t1'],
    ['/verdicts', '/verdicts'],
    ['/settings', '/settings'],
    ['/more', '/more'],
    ['/no-such-page', '/needs-you'],
  ])('lands %s on %s', async (path, landed) => {
    await start();
    await harness.navigateByUrl(path);

    expect(TestBed.inject(Router).url).toBe(landed);
  });

  it('shows the gate identity and a Sign out in the account menu', async () => {
    await start();
    await harness.navigateByUrl('/settings');
    http.expectOne('/oauth2/userinfo').flush({email: 'ada@example.com', name: 'Ada'});
    await harness.fixture.whenStable();

    (root().querySelector('button[aria-label="Account menu"]') as HTMLButtonElement).click();
    await harness.fixture.whenStable();

    expect(menuItems()).toEqual(['Settings', "What's new", 'Sign out']);
    expect(document.body.textContent).toContain('ada@example.com');
  });

  it('offers no sign-out without a gate session', async () => {
    await start();
    await harness.navigateByUrl('/settings');
    http.expectOne('/oauth2/userinfo').flush('', {status: 401, statusText: 'Unauthorized'});
    await harness.fixture.whenStable();

    (root().querySelector('button[aria-label="Account menu"]') as HTMLButtonElement).click();
    await harness.fixture.whenStable();

    expect(menuItems()).toEqual(['Not signed in', 'Settings', "What's new"]);
  });

  it('has no light/dark toggle in the top bar: appearance is Settings and the palette', async () => {
    await start();
    await harness.navigateByUrl('/goals');

    expect(root().querySelector('[aria-label*="theme" i], [aria-label*="dark" i]')).toBeNull();
  });

  it("keeps Settings out of the sidebar: it is the account menu's", async () => {
    await start();
    await harness.navigateByUrl('/goals');

    const nav = Array.from(root().querySelectorAll('cmn-sidebar-nav button')).map(
      b => b.textContent?.trim() ?? ''
    );
    expect(nav.some(l => l.includes('Settings'))).toBe(false);
  });

  it('hands the account to the More tab on a phone: no avatar in the top bar', async () => {
    await start('phone');
    await harness.navigateByUrl('/goals');
    http.expectOne('/oauth2/userinfo').flush({email: 'ada@example.com'});
    await harness.fixture.whenStable();

    expect(root().querySelector('button[aria-label="Account menu"]')).toBeNull();
  });

  // A dead gesture is silent: the shell offers the pull only to a tab root that registered a
  // reload, and matches a tab root by the exact URL after redirects. Each root, and the bare
  // origin that redirects to one, must have a reload that reaches the network.
  it.each([
    ['/', '/needs-you', ['/goals.json']],
    ['/needs-you', '/needs-you', ['/goals.json']],
    ['/goals', '/goals', ['/goals.json']],
    ['/projects', '/projects', ['/projects.json']],
    ['/verdicts', '/verdicts', ['/verdicts.json?limit=50']],
  ])('pulls %s to refresh: its reload reads the feed again', async (path, landed, urls) => {
    await start('phone');
    await harness.navigateByUrl(path);
    await settle();
    for (const req of http.match(() => true)) {
      req.flush(req.request.url === '/control.json' ? CONTROL : [], {
        status: 200,
        statusText: 'OK',
      });
    }
    await harness.fixture.whenStable();
    const pull = TestBed.inject(CmnPullRefreshService);

    expect(TestBed.inject(Router).url).toBe(landed);
    expect(pull.available()).toBe(true);
    void pull.refresh();
    await settle();
    const reads = http.match(() => true).map(r => r.request.url);
    expect(reads.length).toBeGreaterThan(0);
    expect(reads.some(u => urls.some(want => u.startsWith(want.split('?')[0])))).toBe(true);
  });

  // The layout navigates to any palette id that does not start with an underscore, so a mislabelled
  // entry leaves the page instead of reloading it.
  it('reloads the page from the palette entry: it stays on the tab root and reads the feed', async () => {
    await start('phone');
    await harness.navigateByUrl('/projects');
    await settle();
    for (const req of http.match(() => true)) {
      req.flush(req.request.url === '/control.json' ? CONTROL : [], {
        status: 200,
        statusText: 'OK',
      });
    }
    await harness.fixture.whenStable();

    document.dispatchEvent(new KeyboardEvent('keydown', {key: 'k', ctrlKey: true, bubbles: true}));
    await settle();
    const entry = Array.from(document.querySelectorAll<HTMLElement>('*')).find(
      el => el.children.length === 0 && el.textContent?.trim() === 'Refresh this page'
    );
    expect(entry).toBeDefined();
    entry?.click();
    await settle();

    expect(TestBed.inject(Router).url).toBe('/projects');
    expect(http.match(r => r.url.startsWith('/projects.json')).length).toBeGreaterThan(0);
  });
});
