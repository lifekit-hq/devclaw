import {ChangeDetectionStrategy, Component, inject} from '@angular/core';
import {
  ACCOUNT_SIGN_OUT_ID,
  type MenuItem,
  MorePageComponent as KitMorePageComponent,
} from '@lifekit-hq/ui';

import {AccountService} from '../account/account.service';
import {VERSION} from '../version';
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
      [versionLabel]="versionLabel"
      (itemSelect)="select($event)"
    />
  `,
})
export class MorePageComponent {
  protected readonly accounts = inject(AccountService);
  protected readonly navItems = NAV_ITEMS;
  protected readonly tabRoutes = NAV_ITEMS.map(item => item.route);
  protected readonly versionLabel = `v${VERSION}`;

  /** Settings is a link the kit follows itself; Sign out is the app's. */
  protected select(item: MenuItem): void {
    if (item.id === ACCOUNT_SIGN_OUT_ID) {
      this.accounts.signOut();
    }
  }
}
