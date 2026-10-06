import {DatePipe} from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  linkedSignal,
  signal,
} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {
  AlertComponent,
  AsyncStateComponent,
  ButtonComponent,
  CheckboxComponent,
  FormFieldComponent,
  InputComponent,
  PageContainerComponent,
  PageHeaderComponent,
} from '@lifekit-hq/ui';
import {type Observable} from 'rxjs';

import {type RunSchedule} from '../api/devclaw.model';
import {DevclawApiService, errorText} from '../api/devclaw-api.service';
import {ControlFeed} from '../api/feeds';
import {DispatchStatusComponent} from '../shared/dispatch-status.component';
import {SectionComponent} from '../shared/section.component';

const NO_SCHEDULE: RunSchedule = {enabled: false, start: '22:00', end: '05:00', tz: 'UTC'};

function sameSchedule(a: RunSchedule, b: RunSchedule): boolean {
  return a.enabled === b.enabled && a.start === b.start && a.end === b.end && a.tz === b.tz;
}

/** Dispatch control: hold or release new sessions, and the run window. */
@Component({
  selector: 'dc-settings-page',
  imports: [
    AlertComponent,
    AsyncStateComponent,
    ButtonComponent,
    CheckboxComponent,
    DatePipe,
    DispatchStatusComponent,
    FormFieldComponent,
    FormsModule,
    InputComponent,
    PageContainerComponent,
    PageHeaderComponent,
    SectionComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      <cmn-page-header title="Settings" subtitle="When devclaw may start new sessions." />
      @if (error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state [status]="control() ? 'success' : feed.state().error ? 'idle' : 'loading'">
        @if (control(); as c) {
          <div class="flex flex-col gap-cmn-5">
            <dc-section heading="Dispatch">
              <dc-dispatch-status />
              <p class="text-cmn-sm text-text-secondary">
                @if (c.operatorHold.on) {
                  Held: {{ c.operatorHold.reason || '(no reason)' }}
                } @else if (c.pause; as pause) {
                  Paused until {{ pause.untilMs | date: 'medium' }} — {{ pause.reason }}
                } @else if (c.blocked) {
                  Off-hours: {{ c.whyBlocked }}
                } @else {
                  Open.
                }
              </p>
              <div>
                @if (c.operatorHold.on) {
                  <cmn-button [loading]="busy()" (clicked)="release()" variant="primary" size="sm">
                    Release the hold
                  </cmn-button>
                } @else {
                  <cmn-button [loading]="busy()" (clicked)="hold()" variant="secondary" size="sm">
                    Hold all new sessions
                  </cmn-button>
                }
              </div>
            </dc-section>

            <dc-section heading="Run window">
              <cmn-checkbox [checked]="form().enabled" (changed)="patch({enabled: $event})">
                Only start sessions inside the window
              </cmn-checkbox>
              <div class="flex flex-wrap items-end gap-cmn-3">
                <cmn-form-field
                  [ngModel]="form().start"
                  (ngModelChange)="patch({start: $event})"
                  class="w-28"
                  label="Start"
                >
                  <cmn-input placeholder="22:00" size="sm" />
                </cmn-form-field>
                <cmn-form-field
                  [ngModel]="form().end"
                  (ngModelChange)="patch({end: $event})"
                  class="w-28"
                  label="End"
                >
                  <cmn-input placeholder="05:00" size="sm" />
                </cmn-form-field>
                <cmn-form-field
                  [ngModel]="form().tz"
                  (ngModelChange)="patch({tz: $event})"
                  class="w-48"
                  label="Time zone"
                >
                  <cmn-input placeholder="Europe/Dublin" size="sm" />
                </cmn-form-field>
                <cmn-button [loading]="busy()" (clicked)="save()" variant="primary" size="sm"
                  >Save</cmn-button
                >
              </div>
              <p class="text-cmn-xs text-text-secondary">
                Disabled = 24/7. In-flight sessions always finish.
              </p>
            </dc-section>
          </div>
        }
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class SettingsPageComponent {
  private readonly api = inject(DevclawApiService);
  private readonly verbError = signal<string | null>(null);
  /** The saved window; a new object on every poll, equal unless it really changed. */
  private readonly saved = computed(() => this.control()?.schedule ?? NO_SCHEDULE, {
    equal: sameSchedule,
  });

  protected readonly feed = inject(ControlFeed);
  protected readonly control = computed(() => this.feed.state().data);
  protected readonly error = computed(() => this.verbError() ?? this.feed.state().error);
  /** The window being edited; resets only when the saved one changes, never on a poll. */
  protected readonly form = linkedSignal(() => ({...this.saved()}));
  protected readonly busy = signal(false);

  protected patch(change: Partial<RunSchedule>): void {
    this.form.update(f => ({...f, ...change}));
  }

  protected hold(): void {
    this.run(this.api.holdDispatch('held from the console'));
  }

  protected release(): void {
    this.run(this.api.releaseDispatch());
  }

  protected save(): void {
    this.run(this.api.setSchedule(this.form()));
  }

  private run(verb: Observable<unknown>): void {
    this.busy.set(true);
    this.verbError.set(null);
    verb.subscribe({
      next: () => {
        this.busy.set(false);
        this.feed.reload();
      },
      error: (e: unknown) => {
        this.busy.set(false);
        this.verbError.set(errorText(e));
      },
    });
  }
}
