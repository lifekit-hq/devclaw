import {DOCUMENT} from '@angular/common';
import {ChangeDetectionStrategy, Component, computed, inject} from '@angular/core';
import {Router, RouterOutlet} from '@angular/router';
import {
  type AppLayoutAccount,
  AppLayoutComponent,
  type CommandPaletteItem,
  type MenuItem,
  type NavItem,
  PALETTE_THEME_ACTION,
} from '@lifekit-hq/ui';

import {signOutUrl} from '../account/account';
import {AccountService} from '../account/account.service';
import {GoalsFeed} from '../api/feeds';
import {VERSION} from '../version';

const SIGN_OUT_ID = '_sign-out';
const SIGNED_OUT_MENU: MenuItem[] = [{id: '_none', label: 'Not signed in', disabled: true}];
/** The bottom tabs below md; Settings goes under "More". */
const TAB_ROUTES = ['/needs-you', '/goals', '/projects', '/verdicts'];

/** The console's frame: the shared layout owns the nav, palette, theme and account menu. */
@Component({
  selector: 'dc-app-shell',
  imports: [AppLayoutComponent, RouterOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-app-layout
      [navItems]="navItems"
      [paletteItems]="paletteItems"
      [account]="account()"
      [tabRoutes]="tabRoutes"
      [versionLabel]="versionLabel"
      (navClick)="navigate($event)"
      (avatarMenuSelect)="onAccountMenu($event)"
      brand="devclaw"
    >
      <router-outlet />
    </cmn-app-layout>
  `,
  host: {class: 'block h-full'},
})
export class AppShellComponent {
  private readonly router = inject(Router);
  private readonly document = inject(DOCUMENT);
  private readonly signedIn = inject(AccountService).account;
  private readonly goals = inject(GoalsFeed);

  protected readonly navItems: NavItem[] = [
    {
      label: 'Needs you',
      icon: 'CircleAlert',
      route: '/needs-you',
      badge: () => this.goals.waiting(),
    },
    {label: 'Goals', icon: 'Target', route: '/goals'},
    {label: 'Projects', icon: 'FolderGit2', route: '/projects'},
    {label: 'Verdicts', icon: 'Scale', route: '/verdicts'},
    {label: 'Settings', icon: 'Settings', route: '/settings'},
  ];
  protected readonly paletteItems: CommandPaletteItem[] = [
    ...this.navItems.map((item): CommandPaletteItem => ({
      id: item.route,
      label: item.label,
      icon: item.icon,
      group: 'Pages',
    })),
    {id: PALETTE_THEME_ACTION, label: 'Toggle Dark Mode', icon: 'Moon', group: 'Actions'},
  ];
  protected readonly tabRoutes = TAB_ROUTES;
  protected readonly versionLabel = `v${VERSION}`;
  protected readonly account = computed((): AppLayoutAccount => {
    const who = this.signedIn();
    if (!who) {
      return {label: '?', menuItems: SIGNED_OUT_MENU};
    }
    return {
      label: who.name || who.email,
      menuItems: [
        {id: '_identity', label: who.email || who.name, icon: 'User', disabled: true},
        {id: SIGN_OUT_ID, label: 'Sign out', icon: 'LogOut', destructive: true},
      ],
    };
  });

  protected navigate(item: NavItem): void {
    void this.router.navigateByUrl(item.route);
  }

  protected onAccountMenu(item: MenuItem): void {
    if (item.id === SIGN_OUT_ID) {
      const location = this.document.location;
      location.assign(signOutUrl(location.origin));
    }
  }
}
