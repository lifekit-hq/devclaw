import {inject, Injectable} from '@angular/core';
import {CmnDialogService} from '@lifekit-hq/ui';
import {map, type Observable, of, switchMap} from 'rxjs';

import {DevclawApiService} from '../api/devclaw-api.service';

/** Cancel is the one destructive verb, so it asks first. */
@Injectable({providedIn: 'root'})
export class GoalVerbs {
  private readonly api = inject(DevclawApiService);
  private readonly dialog = inject(CmnDialogService);

  /** Emits true once the goal is cancelled, false when the owner kept it. */
  public cancel(goalId: string): Observable<boolean> {
    return this.dialog
      .confirm({
        title: 'Cancel goal',
        message: `Cancel goal ${goalId}? Its running session is torn down; the branch and PR stay.`,
        confirmLabel: 'Cancel goal',
        cancelLabel: 'Keep it',
        confirmVariant: 'destructive',
      })
      .pipe(switchMap(ok => (ok ? this.api.cancelGoal(goalId).pipe(map(() => true)) : of(false))));
  }
}
