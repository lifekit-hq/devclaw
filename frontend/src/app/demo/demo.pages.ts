import {
  HttpErrorResponse,
  type HttpInterceptorFn,
  HttpParams,
  HttpResponse,
  HttpStatusCode,
} from '@angular/common/http';
import {of, throwError} from 'rxjs';

import feeds from '../../../e2e/fixtures/feeds.json';

// The hosted demo (GitHub Pages): a static site with no host behind it. The feeds are answered
// from the e2e fixtures, which the `demo` build copies to `demo/` beside the bundle; feeds.json is
// the same feed-path -> fixture map serve-dist.mjs and the specs read. See demo.ts.

/** True only in the hosted demo build. */
export const DEMO = true;

const fixtures: Record<string, string> = feeds;

/** The account's preferences, kept for the visit: a theme change applies without a host. */
let preferences: Record<string, unknown> = {};

const reply = (body: unknown): HttpResponse<unknown> =>
  new HttpResponse({status: HttpStatusCode.Ok, body});

const refuse = (url: string, status: number, error: string): HttpErrorResponse =>
  new HttpErrorResponse({url, status, error: {error}});

const demoFeedsInterceptor: HttpInterceptorFn = (req, next) => {
  if (!req.url.startsWith('/')) {
    return next(req);
  }
  if (req.url === '/api/me/preferences') {
    if (req.method === 'PATCH') {
      preferences = {...preferences, ...(req.body as Record<string, unknown>)};
    }
    return of(reply(preferences));
  }
  if (req.method !== 'GET') {
    return throwError(() =>
      refuse(
        req.url,
        HttpStatusCode.Forbidden,
        'This is a read-only demo with invented data; nothing was sent.'
      )
    );
  }
  const fixture = fixtures[req.url];
  if (!fixture) {
    return throwError(() => refuse(req.url, HttpStatusCode.NotFound, 'Not in the demo data.'));
  }
  // Relative, so it resolves against the page's base href wherever the demo is hosted.
  return next(req.clone({url: `demo/${fixture}.json`, params: new HttpParams()}));
};

export const DEMO_INTERCEPTORS: HttpInterceptorFn[] = [demoFeedsInterceptor];
