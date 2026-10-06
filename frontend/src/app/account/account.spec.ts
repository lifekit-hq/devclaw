import {accountFromUserinfo, signOutUrl} from './account';

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
});
