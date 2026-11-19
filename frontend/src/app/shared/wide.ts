import {BreakpointObserver} from '@angular/cdk/layout';
import {inject, type Signal} from '@angular/core';
import {toSignal} from '@angular/core/rxjs-interop';
import {map} from 'rxjs';

// The data table's own switch from list rows to a table (md and up).
const WIDE = '(min-width: 768px)';

/** True while the console shows tables; false on a phone, where the data table draws list rows. */
export function isWide(): Signal<boolean> {
  const breakpoints = inject(BreakpointObserver);
  return toSignal(breakpoints.observe(WIDE).pipe(map(state => state.matches)), {
    initialValue: breakpoints.isMatched(WIDE),
  });
}
