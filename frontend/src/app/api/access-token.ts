import {DOCUMENT} from '@angular/common';
import {type HttpInterceptorFn} from '@angular/common/http';
import {inject, Injectable, InjectionToken} from '@angular/core';
import {DefaultUrlSerializer, UrlTree} from '@angular/router';

/**
 * The host's bearer token when the console is opened with `?token=` (a deployment behind
 * DEVCLAW_TOKEN). Read once from the URL the page was opened with; empty when there is none.
 */
export const ACCESS_TOKEN = new InjectionToken<string>('ACCESS_TOKEN', {
  providedIn: 'root',
  factory: () => new URLSearchParams(inject(DOCUMENT).location.search).get('token') ?? '',
});

/** Every same-origin feed and verb carries the token; the gate's own /oauth2 paths do not. */
export const accessTokenInterceptor: HttpInterceptorFn = (req, next) => {
  const token = inject(ACCESS_TOKEN);
  if (!token || !req.url.startsWith('/') || req.url.startsWith('/oauth2/')) {
    return next(req);
  }
  return next(req.clone({setParams: {token}}));
};

/**
 * Keeps `?token=` on every in-app URL the router writes (link hrefs and the address bar), so a
 * reload, a new tab or a copied link stays authorised.
 */
@Injectable()
export class AccessTokenUrlSerializer extends DefaultUrlSerializer {
  private readonly token = inject(ACCESS_TOKEN);

  public override serialize(tree: UrlTree): string {
    if (!this.token || tree.queryParams['token'] === this.token) {
      return super.serialize(tree);
    }
    return super.serialize(
      new UrlTree(tree.root, {...tree.queryParams, token: this.token}, tree.fragment)
    );
  }
}
