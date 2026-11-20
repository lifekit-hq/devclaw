import {DialogRef} from '@angular/cdk/dialog';
import {ChangeDetectionStrategy, Component, inject, Injectable, signal} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {
  ButtonComponent,
  CmnDialogService,
  DialogActionsComponent,
  FormFieldComponent,
  InputComponent,
} from '@lifekit-hq/ui';
import {map, type Observable, of, switchMap} from 'rxjs';

import {DevclawApiService} from '../api/devclaw-api.service';

const DEFAULT_REASON = 'held from the console';

/** The hold's confirm: one optional reason, closed with the reason, or with nothing on Cancel. */
@Component({
  selector: 'dc-hold-dialog',
  imports: [
    ButtonComponent,
    DialogActionsComponent,
    FormFieldComponent,
    FormsModule,
    InputComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <h2 class="font-headline text-cmn-md font-semibold text-text-primary mb-cmn-2">
      Hold all new sessions
    </h2>
    <p class="text-cmn-sm text-text-secondary mb-cmn-3">
      No new session starts until you release the hold. In-flight sessions finish.
    </p>
    <cmn-form-field [ngModel]="reason()" (ngModelChange)="reason.set($event)" label="Reason">
      <cmn-input [placeholder]="defaultReason" size="sm" />
    </cmn-form-field>
    <cmn-dialog-actions>
      <cmn-button (clicked)="ref.close(undefined)" variant="tertiary">Keep dispatching</cmn-button>
      <cmn-button (clicked)="ref.close(reason().trim() || defaultReason)" variant="primary">
        Hold
      </cmn-button>
    </cmn-dialog-actions>
  `,
})
export class HoldDialogComponent {
  protected readonly ref = inject<DialogRef<string | undefined>>(DialogRef);
  protected readonly reason = signal('');
  protected readonly defaultReason = DEFAULT_REASON;
}

/** The hold stops the loop, so it asks first (and for an optional reason). */
@Injectable({providedIn: 'root'})
export class DispatchVerbs {
  private readonly api = inject(DevclawApiService);
  private readonly dialog = inject(CmnDialogService);

  /** Emits true once the hold is posted, false when the owner kept dispatching. */
  public hold(): Observable<boolean> {
    return this.dialog
      .open<string | undefined, unknown, HoldDialogComponent>(HoldDialogComponent)
      .afterClosed()
      .pipe(
        switchMap(reason =>
          reason ? this.api.holdDispatch(reason).pipe(map(() => true)) : of(false)
        )
      );
  }
}
