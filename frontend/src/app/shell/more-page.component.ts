import {ChangeDetectionStrategy, Component, inject} from '@angular/core';
import {
  ACCOUNT_SIGN_OUT_ID,
  CmnWhatsNewService,
  type MenuItem,
  MorePageComponent as KitMorePageComponent,
  WHATS_NEW_MENU_ID,
} from '@lifekit-hq/ui';

import {AccountService} from '../account/account.service';
import {NAV_ITEMS} from './app-shell.component';

/**
 * The phone's More tab: the account (identity, Settings, Sign out) and the version. The sidebar
 * never lists it; on a wide screen the same items are in the avatar menu.
 */
@Component({
  selector: 'dc-more-page',
  imports: [KitMorePageComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-more-page
      [items]="navItems"
      [tabRoutes]="tabRoutes"
      [account]="accounts.layoutAccount()"
      [versionLabel]="whatsNew.versionLabel"
      (itemSelect)="select($event)"
    />
  `,
})
export class MorePageComponent {
  protected readonly accounts = inject(AccountService);
  protected readonly navItems = NAV_ITEMS;
  protected readonly tabRoutes = NAV_ITEMS.map(item => item.route);
  protected readonly whatsNew = inject(CmnWhatsNewService);

  /** Settings is a link the kit follows itself; What's new and Sign out are the app's. */
  protected select(item: MenuItem): void {
    if (item.id === WHATS_NEW_MENU_ID) {
      this.whatsNew.open();
      return;
    }
    if (item.id === ACCOUNT_SIGN_OUT_ID) {
      this.accounts.signOut();
    }
  }
}
