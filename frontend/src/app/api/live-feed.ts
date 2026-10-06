import {type Signal, signal} from '@angular/core';
import {toObservable, toSignal} from '@angular/core/rxjs-interop';
import {poll} from '@lifekit-hq/core';
import {catchError, map, type Observable, of, scan, startWith, Subject, switchMap} from 'rxjs';

import {errorText} from './devclaw-api.service';

/** A feed's last good read and the error of the newest one, if it failed. */
export interface FeedState<T> {
  data: T | null;
  error: string | null;
  /** The HTTP status of the failed read (404 for an unknown id), else null. */
  status: number | null;
}

export interface LiveFeed<T> {
  readonly state: Signal<FeedState<T>>;
  /** Reads now (after a verb) and restarts the interval. */
  reload(): void;
}

type Read<T> = {ok: true; data: T} | {ok: false; error: string; status: number | null};

const EMPTY = {data: null, error: null, status: null};

/**
 * Polls one feed while the page is open: now, then every `intervalMs` (0 reads once), again whenever `key`
 * changes (a new route id) and on `reload()`. A failed read keeps the last good data and sets
 * the error, so a blip never blanks the page. Call in an injection context.
 */
export function liveFeed<T, K = null>(
  request: (key: K) => Observable<T>,
  intervalMs: number,
  key: Signal<K> = signal(null as K)
): LiveFeed<T> {
  const reloads$ = new Subject<void>();
  const read = (k: K): Observable<Read<T>> =>
    request(k).pipe(
      map((data): Read<T> => ({ok: true, data})),
      catchError(e =>
        of<Read<T>>({
          ok: false,
          error: errorText(e),
          status: (e as {status?: number}).status ?? null,
        })
      )
    );
  const state = toSignal(
    toObservable(key).pipe(
      switchMap(k =>
        reloads$.pipe(
          startWith(undefined),
          switchMap(() =>
            intervalMs > 0 ? poll(() => read(k), {intervalMs, isDone: () => false}) : read(k)
          ),
          scan(
            (prev: FeedState<T>, r: Read<T>): FeedState<T> =>
              r.ok
                ? {data: r.data, error: null, status: null}
                : {data: prev.data, error: r.error, status: r.status},
            EMPTY
          ),
          startWith<FeedState<T>>(EMPTY)
        )
      )
    ),
    {initialValue: EMPTY}
  );
  return {state, reload: () => reloads$.next()};
}
