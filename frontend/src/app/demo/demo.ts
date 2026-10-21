import {type HttpInterceptorFn} from '@angular/common/http';

// The hosted demo's swap point. The console talks to its host; the `demo` build configuration
// replaces this file with demo.pages.ts, which answers the feeds from the e2e fixtures instead.

/** True only in the hosted demo build. */
export const DEMO = false;

/** Interceptors the demo build answers the feeds with; none in the real console. */
export const DEMO_INTERCEPTORS: HttpInterceptorFn[] = [];
