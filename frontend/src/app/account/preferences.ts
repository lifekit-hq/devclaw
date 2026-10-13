import {accountPreferencesStore} from '@lifekit-hq/core/preferences';

/**
 * The signed-in user's account-tier preferences (`GET`/`PATCH /api/me/preferences`): today only
 * the appearance. Wired into the theme by `provideAppearanceSync` in `app.config.ts`.
 */
export const AppPreferencesStore = accountPreferencesStore();
