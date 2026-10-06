import {HttpClient} from '@angular/common/http';
import {inject, Injectable} from '@angular/core';
import {toSignal} from '@angular/core/rxjs-interop';
import {catchError, map, type Observable, of} from 'rxjs';

import {type Account, accountFromUserinfo} from './account';

@Injectable({providedIn: 'root'})
export class AccountService {
  private readonly http = inject(HttpClient);

  /** The signed-in account, or null while loading and when there is no readable session. */
  public readonly account = toSignal(
    this.http.get<Record<string, unknown>>('/oauth2/userinfo').pipe(
      map(accountFromUserinfo),
      catchError((): Observable<Account | null> => of(null))
    ),
    {initialValue: null}
  );
}
