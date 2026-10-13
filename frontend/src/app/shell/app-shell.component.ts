import {ChangeDetectionStrategy, Component, computed, inject} from '@angular/core';
import {Router, RouterOutlet} from '@angular/router';
import {
  ACCOUNT_SIGN_OUT_ID,
  APPEARANCE_PALETTE_ITEMS,
  AppLayoutComponent,
  CmnShellService,
  type CommandPaletteItem,
  type MenuItem,
  type NavItem,
} from '@lifekit-hq/ui';

import {SETTINGS_ROUTE} from '../account/account';
import {AccountService} from '../account/account.service';
import {GoalsFeed} from '../api/feeds';
import {VERSION} from '../version';

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
  template: `
    <cmn-app-layout
      [navItems]="navItems"
      [paletteItems]="paletteItems()"
      [account]="layoutAccount()"
      [tabRoutes]="tabRoutes"
      [moreRoute]="moreRoute"
      [versionLabel]="versionLabel"
      (navClick)="navigate($event)"
      (paletteAction)="onPaletteAction($event)"
      (avatarMenuSelect)="onAccountItem($event)"
      brand="devclaw"
    >
      <router-outlet />
    </cmn-app-layout>
  `,
  host: {class: 'block h-full'},
})
export class AppShellComponent {
  private readonly router = inject(Router);
  private readonly shell = inject(CmnShellService);
  private readonly accounts = inject(AccountService);
  private readonly goals = inject(GoalsFeed);

  protected readonly layoutAccount = this.accounts.layoutAccount;
  protected readonly tabRoutes = NAV_ITEMS.map(item => item.route);
  protected readonly moreRoute = MORE_ROUTE;
  protected readonly versionLabel = `v${VERSION}`;
  /** Needs you carries the number of goals waiting on the owner. */
  protected readonly navItems = NAV_ITEMS.map((item): NavItem =>
    item.route === '/needs-you' ? {...item, badge: () => this.goals.waiting()} : item
  );
  /** Sign out is offered in the palette only for a signed-in account. */
  protected readonly paletteItems = computed((): CommandPaletteItem[] => [
    ...PAGE_ITEMS,
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
    if (id === ACCOUNT_SIGN_OUT_ID) {
      this.accounts.signOut();
    }
  }

  // The layout navigates to Settings itself; Sign out is the app's.
  protected onAccountItem(item: MenuItem): void {
    if (item.id === ACCOUNT_SIGN_OUT_ID) {
      this.accounts.signOut();
    }
  }
}
