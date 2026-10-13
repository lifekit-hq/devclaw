import {ACCOUNT_SIGN_OUT_ID} from '@lifekit-hq/ui';

import {accountFromUserinfo, layoutAccount, SETTINGS_ROUTE, signOutUrl} from './account';

describe('account', () => {
  it('reads the gate userinfo, falling back through the name fields to the email', () => {
    expect(accountFromUserinfo({email: 'a@example.com', name: 'Ada'})).toEqual({
      name: 'Ada',
      email: 'a@example.com',
    });
    expect(accountFromUserinfo({email: 'a@example.com'})).toEqual({
      name: 'a@example.com',
      email: 'a@example.com',
    });
    expect(accountFromUserinfo({preferredUsername: 'ada'})?.name).toBe('ada');
  });

  it('is null when the body names nobody', () => {
    expect(accountFromUserinfo({})).toBeNull();
    expect(accountFromUserinfo({email: 42})).toBeNull();
  });

  it('signs out through the gate and lands back on this origin', () => {
    expect(signOutUrl('https://console.example')).toBe(
      '/oauth2/sign_out?rd=https%3A%2F%2Fconsole.example%2F'
    );
  });

  it('offers Settings and Sign out to a signed-in account', () => {
    const account = layoutAccount({name: 'Ada', email: 'a@example.com'});

    expect(account.label).toBe('Ada');
    expect(account.email).toBe('a@example.com');
    const ids = account.menuItems.map(item => item.id);
    expect(ids).toContain(SETTINGS_ROUTE);
    expect(ids).toContain(ACCOUNT_SIGN_OUT_ID);
  });

  it('offers Settings but no Sign out without a gate session', () => {
    const ids = layoutAccount(null).menuItems.map(item => item.id);

    expect(ids).toContain(SETTINGS_ROUTE);
    expect(ids).not.toContain(ACCOUNT_SIGN_OUT_ID);
  });
});
