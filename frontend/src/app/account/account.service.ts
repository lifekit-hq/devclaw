import {DOCUMENT} from '@angular/common';
import {HttpClient} from '@angular/common/http';
import {computed, inject, Injectable} from '@angular/core';
import {toSignal} from '@angular/core/rxjs-interop';
import {catchError, map, type Observable, of} from 'rxjs';

import {type Account, accountFromUserinfo, layoutAccount, signOutUrl} from './account';
import {AppPreferencesStore} from './preferences';

@Injectable({providedIn: 'root'})
export class AccountService {
  private readonly http = inject(HttpClient);
  private readonly document = inject(DOCUMENT);
  private readonly preferences = inject(AppPreferencesStore);

  /** The signed-in account, or null while loading and when there is no readable session. */
  public readonly account = toSignal(
    this.http.get<Record<string, unknown>>('/oauth2/userinfo').pipe(
      map(accountFromUserinfo),
      catchError((): Observable<Account | null> => of(null))
    ),
    {initialValue: null}
  );

  /** The account as the app layout takes it: identity header, Settings and Sign out. */
  public readonly layoutAccount = computed(() => layoutAccount(this.account()));

  /** Ends the gate's session; the next user starts with no preferences loaded. */
  public signOut(): void {
    this.preferences.clear();
    const location = this.document.location;
    location.assign(signOutUrl(location.origin));
  }
}
