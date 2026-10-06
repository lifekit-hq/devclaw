import {ChangeDetectionStrategy, Component, inject, input, output, signal} from '@angular/core';
import {FormsModule} from '@angular/forms';
import {ButtonComponent, FormFieldComponent, InputComponent} from '@lifekit-hq/ui';

import {DevclawApiService, errorText} from '../api/devclaw-api.service';

/**
 * The owner's one verb: words posted on the goal's issue as an instruction mentioning the bot.
 * `decideWith` lets a parent post one of the session's options through the same path.
 */
@Component({
  selector: 'dc-decide-form',
  imports: [ButtonComponent, FormFieldComponent, FormsModule, InputComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="flex flex-wrap items-end gap-cmn-2">
      <cmn-form-field
        [ngModel]="text()"
        [disabled]="busy()"
        (ngModelChange)="text.set($event)"
        class="min-w-0 flex-1 basis-64"
        label="Your decision"
      >
        <cmn-input [placeholder]="placeholder()" size="sm" />
      </cmn-form-field>
      <cmn-button
        [disabled]="busy() || !text().trim()"
        (clicked)="decideWith(text())"
        variant="secondary"
        size="sm"
      >
        Decide
      </cmn-button>
      @if (cancellable()) {
        <cmn-button
          [disabled]="busy()"
          (clicked)="cancelRequested.emit()"
          variant="secondary"
          size="sm"
        >
          Cancel goal
        </cmn-button>
      }
    </div>
    @if (error(); as message) {
      <p class="mt-cmn-2 text-cmn-sm text-status-error" role="alert">{{ message }}</p>
    }
  `,
})
export class DecideFormComponent {
  private readonly api = inject(DevclawApiService);

  public readonly goalId = input.required<string>();
  public readonly placeholder = input('your decision, posted on the issue');
  /** Offers Cancel goal next to Decide; the page owns the confirm. */
  public readonly cancellable = input(false);
  /** A decision was posted on the issue; the page re-reads its feed. */
  public readonly decided = output();
  public readonly cancelRequested = output();

  public readonly busy = signal(false);
  protected readonly text = signal('');
  protected readonly error = signal<string | null>(null);

  public decideWith(raw: string): void {
    const text = raw.trim();
    if (!text || this.busy()) {
      return;
    }
    this.busy.set(true);
    this.error.set(null);
    this.api.decide(this.goalId(), text).subscribe({
      next: () => {
        this.busy.set(false);
        this.text.set('');
        this.decided.emit();
      },
      error: (e: unknown) => {
        this.busy.set(false);
        this.error.set(errorText(e));
      },
    });
  }
}
