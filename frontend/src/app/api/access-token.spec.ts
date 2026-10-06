import {HttpClient, provideHttpClient, withInterceptors} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {provideZonelessChangeDetection} from '@angular/core';
import {TestBed} from '@angular/core/testing';
import {provideRouter, Router, UrlSerializer} from '@angular/router';

import {ACCESS_TOKEN, accessTokenInterceptor, AccessTokenUrlSerializer} from './access-token';

describe('the ?token= access path', () => {
  function setup(token: string): void {
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideRouter([]),
        {provide: UrlSerializer, useClass: AccessTokenUrlSerializer},
        {provide: ACCESS_TOKEN, useValue: token},
        provideHttpClient(withInterceptors([accessTokenInterceptor])),
        provideHttpClientTesting(),
      ],
    });
  }

  it('sends the token on the host feeds and verbs, never to the gate', () => {
    setup('t0k');
    const http = TestBed.inject(HttpClient);
    const backend = TestBed.inject(HttpTestingController);

    http.get('/goals.json').subscribe();
    http.get('/oauth2/userinfo').subscribe();

    expect(backend.expectOne('/goals.json?token=t0k').request.params.get('token')).toBe('t0k');
    expect(backend.expectOne('/oauth2/userinfo').request.params.has('token')).toBe(false);
    backend.verify();
  });

  it('keeps the token on every in-app URL, so a reload or new tab stays authorised', () => {
    setup('t0k');
    const router = TestBed.inject(Router);

    expect(router.serializeUrl(router.createUrlTree(['/goals', 'g1']))).toBe('/goals/g1?token=t0k');
  });

  it('adds nothing behind the gate, where there is no token', () => {
    setup('');
    const http = TestBed.inject(HttpClient);
    const backend = TestBed.inject(HttpTestingController);
    const router = TestBed.inject(Router);

    http.get('/goals.json').subscribe();

    expect(backend.expectOne('/goals.json').request.params.keys()).toEqual([]);
    expect(router.serializeUrl(router.createUrlTree(['/goals']))).toBe('/goals');
  });
});
