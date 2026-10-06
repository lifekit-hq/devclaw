import {provideHttpClient, withFetch, withInterceptors} from '@angular/common/http';
import {
  type ApplicationConfig,
  isDevMode,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import {provideRouter, UrlSerializer, withComponentInputBinding} from '@angular/router';
import {provideServiceWorker} from '@angular/service-worker';

import {accessTokenInterceptor, AccessTokenUrlSerializer} from './api/access-token';
import {APP_ROUTES} from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(APP_ROUTES, withComponentInputBinding()),
    {provide: UrlSerializer, useClass: AccessTokenUrlSerializer},
    provideHttpClient(withFetch(), withInterceptors([accessTokenInterceptor])),
    // Precache-only: ngsw-config.json lists the bundle and /console/ navigations, never a feed.
    // `ng serve` has no ngsw, so the worker registers only in the production build; `none` makes
    // the browser revalidate the worker script instead of trusting its HTTP cache.
    provideServiceWorker('ngsw-worker.js', {
      enabled: !isDevMode(),
      registrationStrategy: 'registerWhenStable:30000',
      updateViaCache: 'none',
    }),
  ],
};
