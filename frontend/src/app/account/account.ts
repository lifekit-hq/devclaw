// The signed-in user comes from the sign-in gate in front of the console (oauth2-proxy), which
// answers /oauth2/userinfo on this same origin. Without the gate (local dev, a `?token=` link)
// there is no readable session and the menu says so.

export interface Account {
  name: string;
  email: string;
}

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
