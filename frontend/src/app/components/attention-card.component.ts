import {ChangeDetectionStrategy, Component, computed, input, output} from '@angular/core';
import {ButtonComponent} from '@lifekit-hq/ui';

import {type Attention} from '../api/devclaw.model';
import {AgoPipe} from '../shared/time';
import {DecideFormComponent} from './decide-form.component';

interface Option {
  text: string;
  firstLine: string;
  recommended: boolean;
}

/**
 * One goal's ask, the same on Needs you and on the goal page. Buttons exist only for a session
 * block: they are the session's options, the recommended one first. A host-authored stop shows
 * its fact, a free-text decide and cancel, never options the host made up.
 */
@Component({
  selector: 'dc-attention-card',
  imports: [AgoPipe, ButtonComponent, DecideFormComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @let a = attention();
    @if (a.answered; as answered) {
      <p class="whitespace-pre-wrap text-cmn-sm text-text-primary">{{ a.question }}</p>
      <p class="mt-cmn-2 text-cmn-sm text-text-secondary">
        Answered {{ answered.madeAt | ago }}:
        <span class="text-text-primary">{{ answered.text }}</span>
        @if (answered.commentUrl) {
          ·
          <a
            [href]="answered.commentUrl"
            class="text-accent-default hover:underline"
            target="_blank"
            rel="noopener noreferrer"
            >thread</a
          >
        }
      </p>
      <p class="mt-cmn-1 font-mono text-cmn-xs text-text-secondary">
        waiting for the tick · {{ answered.waitingOn }}
      </p>
    } @else {
      <p class="mb-cmn-2 font-mono text-cmn-xs text-text-secondary">
        {{ a.kind }} · open {{ a.since | ago }}
        @if (a.link) {
          ·
          <a
            [href]="a.link"
            class="text-accent-default hover:underline"
            target="_blank"
            rel="noopener noreferrer"
            >{{ a.kind === 'session' || a.kind === 'env' ? 'issue' : 'thread' }}</a
          >
        }
      </p>
      <p class="whitespace-pre-wrap text-cmn-sm text-text-primary">{{ a.question }}</p>
      @if (a.kind === 'env') {
        <p class="mt-cmn-2 text-cmn-sm text-text-secondary">
          Wakes on its own once the credential probes green.
        </p>
      } @else {
        @if (options().length || takeDefault()) {
          <div
            class="mt-cmn-3 flex flex-wrap gap-cmn-2"
            role="group"
            aria-label="The session's options"
          >
            @for (option of options(); track $index) {
              <cmn-button
                [variant]="option.recommended ? 'primary' : 'secondary'"
                [disabled]="form.busy()"
                [attr.title]="option.text"
                (clicked)="form.decideWith(option.text)"
                size="sm"
              >
                {{ option.firstLine }}
                @if (option.recommended) {
                  <span class="font-mono text-cmn-xs opacity-75">recommended</span>
                }
              </cmn-button>
            }
            @if (takeDefault(); as fallback) {
              <cmn-button
                [disabled]="form.busy()"
                [attr.title]="fallback"
                (clicked)="form.decideWith(fallback)"
                size="sm"
              >
                take the default: {{ fallback }}
              </cmn-button>
            }
          </div>
        }
        <dc-decide-form
          #form
          [goalId]="goalId()"
          [placeholder]="
            options().length ? 'or answer in your own words' : 'your decision, posted on the issue'
          "
          [cancellable]="cancellable()"
          (decided)="decided.emit()"
          (cancelRequested)="cancelRequested.emit()"
          class="mt-cmn-3 block"
        />
      }
    }
  `,
})
export class AttentionCardComponent {
  public readonly goalId = input.required<string>();
  public readonly attention = input.required<Attention>();
  /** Offers Cancel goal next to Decide; the page owns the confirm. */
  public readonly cancellable = input(false);
  /** A decision was posted on the issue; the page re-reads its feed. */
  public readonly decided = output();
  public readonly cancelRequested = output();

  protected readonly options = computed<Option[]>(() => {
    const a = this.attention();
    return a.options
      .map((text, i) => ({text, firstLine: text.split('\n')[0], recommended: i === a.recommended}))
      .sort((x, y) => Number(y.recommended) - Number(x.recommended));
  });
  /** The session's default, offered only when it recommended none of its options. */
  protected readonly takeDefault = computed(() => {
    const a = this.attention();
    return a.default && a.recommended < 0 ? a.default : '';
  });
}
