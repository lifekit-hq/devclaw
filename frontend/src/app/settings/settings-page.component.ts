import {
  ChangeDetectionStrategy,
  Component,
  computed,
  inject,
  linkedSignal,
  signal,
} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {APP_SEEDS} from '@lifekit-hq/tokens/engine';
import {
  AlertComponent,
  AppearanceSettingsComponent,
  ButtonComponent,
  CheckboxComponent,
  FormFieldComponent,
  InputComponent,
  PageContainerComponent,
  SettingRowComponent,
  SettingsSectionComponent,
} from '@lifekit-hq/ui';
import {type Observable} from 'rxjs';

import {AccountService} from '../account/account.service';
import {type RunSchedule} from '../api/devclaw.model';
import {DevclawApiService, errorText} from '../api/devclaw-api.service';
import {ControlFeed} from '../api/feeds';
import {DispatchStatusComponent} from '../shared/dispatch-status.component';
import {VERSION} from '../version';

const NO_SCHEDULE: RunSchedule = {enabled: false, start: '22:00', end: '05:00', tz: 'UTC'};

function sameSchedule(a: RunSchedule, b: RunSchedule): boolean {
  return a.enabled === b.enabled && a.start === b.start && a.end === b.end && a.tz === b.tz;
}

/**
 * The standard settings page: Account (the gate's identity, read-only), Appearance (applies at
 * once and follows the account), the Workspace: Instance controls (dispatch hold and the run
 * window, each its own section with its own Save) and About. Sections are direct children of the
 * page container, which owns the dividers.
 */
@Component({
  selector: 'dc-settings-page',
  imports: [
    AlertComponent,
    AppearanceSettingsComponent,
    ButtonComponent,
    CheckboxComponent,
    DispatchStatusComponent,
    FormFieldComponent,
    FormsModule,
    InputComponent,
    PageContainerComponent,
    SettingRowComponent,
    SettingsSectionComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container spacing="none">
      <cmn-settings-section heading="Account" caption="Signed in through the gate">
        @if (accounts.account(); as account) {
          @if (account.name !== account.email) {
            <cmn-setting-row label="Name">
              <span class="text-cmn-sm text-text-secondary">{{ account.name }}</span>
            </cmn-setting-row>
          }
          <cmn-setting-row label="Email">
            <span class="text-cmn-sm text-text-secondary">{{ account.email || 'Not shared' }}</span>
          </cmn-setting-row>
        } @else {
          <cmn-setting-row label="Account">
            <span class="text-cmn-sm text-text-secondary">Not signed in</span>
          </cmn-setting-row>
        }
      </cmn-settings-section>

      <cmn-settings-section heading="Appearance">
        <cmn-appearance-settings [appSeed]="appSeed" />
      </cmn-settings-section>

      <cmn-settings-section
        heading="Workspace: Instance"
        caption="Dispatch and the run window apply to everyone on this devclaw."
      >
        @if (error(); as message) {
          <cmn-alert variant="error">{{ message }}</cmn-alert>
        }
        <dc-dispatch-status />
        @if (control(); as c) {
          <cmn-setting-row [description]="dispatchState()" label="Dispatch">
            @if (c.operatorHold.on) {
              <cmn-button [loading]="busy()" (clicked)="release()" variant="primary">
                Release the hold
              </cmn-button>
            } @else {
              <cmn-button [loading]="busy()" (clicked)="hold()" variant="secondary">
                Hold all new sessions
              </cmn-button>
            }
          </cmn-setting-row>
        }
      </cmn-settings-section>

      @if (control()) {
        <cmn-settings-section
          heading="Run window"
          caption="Disabled = 24/7. In-flight sessions always finish."
        >
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
          </div>
          <div>
            <cmn-button [loading]="busy()" (clicked)="save()" variant="primary">Save</cmn-button>
          </div>
        </cmn-settings-section>
      }

      <cmn-settings-section heading="About">
        <cmn-setting-row label="Version">
          <span class="text-cmn-sm text-text-secondary">v{{ version }}</span>
        </cmn-setting-row>
      </cmn-settings-section>

      @if (accounts.account()) {
        <cmn-settings-section heading="Session">
          <cmn-setting-row
            description="Ends your session here and at the gate."
            label="Sign out of devclaw"
          >
            <cmn-button (clicked)="accounts.signOut()" variant="secondary" icon="LogOut">
              Sign out
            </cmn-button>
          </cmn-setting-row>
        </cmn-settings-section>
      }
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

  protected readonly accounts = inject(AccountService);
  protected readonly appSeed = APP_SEEDS.dc.seed;
  protected readonly version = VERSION;
  protected readonly feed = inject(ControlFeed);
  protected readonly control = computed(() => this.feed.state().data);
  protected readonly error = computed(() => this.verbError() ?? this.feed.state().error);
  /** The window being edited; resets only when the saved one changes, never on a poll. */
  protected readonly form = linkedSignal(() => ({...this.saved()}));
  protected readonly busy = signal(false);
  /** Why dispatch is or is not starting sessions, in one line. */
  protected readonly dispatchState = computed((): string => {
    const c = this.control();
    if (!c) {
      return '';
    }
    if (c.operatorHold.on) {
      return `Held: ${c.operatorHold.reason || '(no reason)'}`;
    }
    if (c.pause) {
      return `Paused until ${new Date(c.pause.untilMs).toLocaleString()} - ${c.pause.reason}`;
    }
    return c.blocked ? `Off-hours: ${c.whyBlocked}` : 'Open.';
  });

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
