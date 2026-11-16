import {
  ChangeDetectionStrategy,
  Component,
  computed,
  CUSTOM_ELEMENTS_SCHEMA,
  inject,
} from '@angular/core';
import {Router, RouterOutlet} from '@angular/router';
import {AppUpdateService} from '@lifekit-hq/core/pwa';
import {
  ACCOUNT_SIGN_OUT_ID,
  APPEARANCE_PALETTE_ITEMS,
  AppLayoutComponent,
  CmnPullRefreshService,
  CmnShellService,
  CmnWhatsNewService,
  type CommandPaletteItem,
  type MenuItem,
  type NavItem,
  WHATS_NEW_MENU_ID,
} from '@lifekit-hq/ui';

import {SETTINGS_ROUTE} from '../account/account';
import {AccountService} from '../account/account.service';
import {GoalsFeed} from '../api/feeds';

/** The console's four pages: the phone's tabs and the sidebar's entries. */
export const NAV_ITEMS: NavItem[] = [
  {label: 'Needs you', icon: 'CircleAlert', route: '/needs-you'},
  {label: 'Goals', icon: 'Target', route: '/goals'},
  {label: 'Projects', icon: 'FolderGit2', route: '/projects'},
  {label: 'Verdicts', icon: 'Scale', route: '/verdicts'},
];

/** The phone's More tab: the account surface, which replaces the top bar's avatar there. */
export const MORE_ROUTE = '/more';

const PAGE_ITEMS: CommandPaletteItem[] = [
  ...NAV_ITEMS.map((item): CommandPaletteItem => ({
    id: item.route,
    label: item.label,
    icon: item.icon as string,
    group: 'Pages',
  })),
  {id: SETTINGS_ROUTE, label: 'Settings', icon: 'Settings', group: 'Pages'},
];
// Pull to refresh is a touch gesture; this is the reload a keyboard or a screen reader reaches.
// The layout treats a palette id without the leading underscore as a route to navigate to.
const REFRESH_ACTION_ID = '_refresh';
const REFRESH_PALETTE_ITEM: CommandPaletteItem = {
  id: REFRESH_ACTION_ID,
  label: 'Refresh this page',
  icon: 'RefreshCw',
  group: 'Actions',
};
const SIGN_OUT_PALETTE_ITEM: CommandPaletteItem = {
  id: ACCOUNT_SIGN_OUT_ID,
  label: 'Sign out',
  icon: 'LogOut',
  group: 'Actions',
};

/**
 * The console's frame: the shared layout owns the nav, palette, appearance and account menu. On a
 * phone the account lives on the More tab; Settings is the account menu's, never a tab.
 */
@Component({
  selector: 'dc-app-shell',
  imports: [AppLayoutComponent, RouterOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  schemas: [CUSTOM_ELEMENTS_SCHEMA],
  template: `
    <cmn-app-layout
      [navItems]="navItems"
      [paletteItems]="paletteItems()"
      [account]="layoutAccount()"
      [accountPending]="pending()"
      [tabRoutes]="tabRoutes"
      [moreRoute]="moreRoute"
      [versionLabel]="whatsNew.versionLabel"
      [versionDot]="whatsNew.hasUnread()"
      [phoneOverlay]="true"
      (navClick)="navigate($event)"
      (paletteAction)="onPaletteAction($event)"
      (versionClick)="whatsNew.open()"
      (avatarMenuSelect)="onAccountItem($event)"
      brand="devclaw"
    >
      <lk-offline-banner cmnAppLayoutNotice />
      <lk-update-prompt
        [ready]="update.updateReady()"
        (lk-update-prompt-reload)="update.reload()"
        cmnAppLayoutNotice
      />
      <lk-install-hint cmnAppLayoutNotice />
      <router-outlet />
    </cmn-app-layout>
  `,
})
export class AppShellComponent {
  private readonly router = inject(Router);
  private readonly shell = inject(CmnShellService);
  private readonly pullRefresh = inject(CmnPullRefreshService);
  private readonly accounts = inject(AccountService);
  private readonly goals = inject(GoalsFeed);

  protected readonly whatsNew = inject(CmnWhatsNewService);

  protected readonly update = inject(AppUpdateService);

  protected readonly layoutAccount = this.accounts.layoutAccount;
  protected readonly pending = this.accounts.pending;
  protected readonly tabRoutes = NAV_ITEMS.map(item => item.route);
  protected readonly moreRoute = MORE_ROUTE;
  /** Needs you carries the number of goals waiting on the owner. */
  protected readonly navItems = NAV_ITEMS.map((item): NavItem =>
    item.route === '/needs-you' ? {...item, badge: () => this.goals.waiting()} : item
  );
  /** Sign out is offered in the palette only for a signed-in account. */
  protected readonly paletteItems = computed((): CommandPaletteItem[] => [
    ...PAGE_ITEMS,
    ...(this.pullRefresh.available() ? [REFRESH_PALETTE_ITEM] : []),
    ...APPEARANCE_PALETTE_ITEMS,
    ...(this.accounts.account() ? [SIGN_OUT_PALETTE_ITEM] : []),
  ]);

  // On the phone tab bar the shell does the navigating (last screen, scroll, pop to root on a
  // re-tap); navigating here too would fight it. The sidebar leaves navigation to the app.
  protected navigate(item: NavItem): void {
    if (this.shell.isPhone()) {
      return;
    }
    void this.router.navigateByUrl(item.route);
  }

  // The layout navigates to a page entry and applies the appearance entries itself.
  protected onPaletteAction(id: string): void {
    if (id === REFRESH_ACTION_ID) {
      void this.pullRefresh.refresh();
      return;
    }
    if (id === ACCOUNT_SIGN_OUT_ID) {
      this.accounts.signOut();
    }
  }

  // The layout navigates to Settings itself; Sign out and What's new are the app's.
  protected onAccountItem(item: MenuItem): void {
    if (item.id === WHATS_NEW_MENU_ID) {
      this.whatsNew.open();
      return;
    }
    if (item.id === ACCOUNT_SIGN_OUT_ID) {
      this.accounts.signOut();
    }
  }
}
