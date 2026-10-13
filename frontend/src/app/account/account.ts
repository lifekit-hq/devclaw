// The signed-in user comes from the sign-in gate in front of the console (oauth2-proxy), which
// answers /oauth2/userinfo on this same origin. Without the gate (local dev, a `?token=` link)
// there is no readable session and the menu says so.
import {accountMenu, type AppLayoutAccount, type MenuItem} from '@lifekit-hq/ui';

export interface Account {
  name: string;
  email: string;
}

export const SETTINGS_ROUTE = '/settings';

/** Sign out ends the gate's session and lands back on the console, which signs in again. */
export function signOutUrl(origin: string): string {
  return `/oauth2/sign_out?rd=${encodeURIComponent(`${origin}/`)}`;
}

const text = (value: unknown): string => (typeof value === 'string' ? value : '');

/** null when the body names nobody: the menu then offers no sign-out rather than a broken one. */
export function accountFromUserinfo(body: Record<string, unknown>): Account | null {
  const email = text(body['email']);
  const name = text(body['name']) || text(body['preferredUsername']) || text(body['user']) || email;
  if (!name && !email) {
    return null;
  }
  return {name, email};
}

const NOT_SIGNED_IN: MenuItem = {id: '_none', label: 'Not signed in', disabled: true};

/**
 * The layout's account model: the standard account menu (identity header, Settings, Sign out).
 * Without a gate session there is no identity and no Sign out, only a note and Settings.
 */
export function layoutAccount(account: Account | null): AppLayoutAccount {
  if (!account) {
    return {
      label: '',
      menuItems: [NOT_SIGNED_IN, ...accountMenu({settingsRoute: SETTINGS_ROUTE, signOut: false})],
    };
  }
  return {
    label: account.name || account.email,
    name: account.name && account.name !== account.email ? account.name : undefined,
    email: account.email || undefined,
    menuItems: accountMenu({settingsRoute: SETTINGS_ROUTE}),
  };
}
