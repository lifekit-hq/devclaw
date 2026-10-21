import {provideHttpClient, withFetch, withInterceptors} from '@angular/common/http';
import {
  type ApplicationConfig,
  inject,
  isDevMode,
  provideAppInitializer,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import {provideRouter, UrlSerializer, withComponentInputBinding} from '@angular/router';
import {provideServiceWorker} from '@angular/service-worker';
import {provideAppearanceSync, provideToastPreferenceErrors} from '@lifekit-hq/ui';

import {AppPreferencesStore} from './account/preferences';
import {accessTokenInterceptor, AccessTokenUrlSerializer} from './api/access-token';
import {APP_ROUTES} from './app.routes';
import {DEMO, DEMO_INTERCEPTORS} from './demo/demo';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(APP_ROUTES, withComponentInputBinding()),
    {provide: UrlSerializer, useClass: AccessTokenUrlSerializer},
    provideHttpClient(
      withFetch(),
      withInterceptors([accessTokenInterceptor, ...DEMO_INTERCEPTORS])
    ),
    // Appearance (mode, seed colour) follows the account: applied from the device cache at once,
    // written through to /api/me/preferences, and a failed save toasts. Also starts the theme.
    provideAppearanceSync(AppPreferencesStore),
    provideToastPreferenceErrors(),
    // The gate only serves the console to a signed-in user, so the account's preferences load at
    // start; the server's appearance then wins over the device cache. A failed load (no gate
    // identity, offline) leaves the cache in charge.
    provideAppInitializer(() => {
      void inject(AppPreferencesStore).load();
    }),
    // Precache-only: ngsw-config.json lists the bundle and /console/ navigations, never a feed.
    // `ng serve` has no ngsw, so the worker registers only in the production build; `none` makes
    // the browser revalidate the worker script instead of trusting its HTTP cache. The hosted demo
    // ships no worker.
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode() && !DEMO,
      registrationStrategy: 'registerWhenStable:30000',
      updateViaCache: 'none',
    }),
  ],
};
