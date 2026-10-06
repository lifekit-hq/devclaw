import {provideHttpClient, withFetch, withInterceptors} from '@angular/common/http';
import {
  type ApplicationConfig,
  provideBrowserGlobalErrorListeners,
  provideZonelessChangeDetection,
} from '@angular/core';
import {provideRouter, UrlSerializer, withComponentInputBinding} from '@angular/router';

import {accessTokenInterceptor, AccessTokenUrlSerializer} from './api/access-token';
import {APP_ROUTES} from './app.routes';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideZonelessChangeDetection(),
    provideRouter(APP_ROUTES, withComponentInputBinding()),
    {provide: UrlSerializer, useClass: AccessTokenUrlSerializer},
    provideHttpClient(withFetch(), withInterceptors([accessTokenInterceptor])),
  ],
};
