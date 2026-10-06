import {ChangeDetectionStrategy, Component, input} from '@angular/core';
import {CardComponent} from '@lifekit-hq/ui';

/** A titled card on a detail page: the heading, an optional count, then the body. */
@Component({
  selector: 'dc-section',
  imports: [CardComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-card>
      <section [attr.aria-label]="heading()" class="flex flex-col gap-cmn-2">
        <h2 class="font-headline text-cmn-sm font-semibold text-text-secondary">
          {{ heading() }}
          @if (count() !== null) {
            <span class="font-mono font-normal">· {{ count() }}</span>
          }
        </h2>
        <ng-content />
      </section>
    </cmn-card>
  `,
})
export class SectionComponent {
  public readonly heading = input.required<string>();
  public readonly count = input<number | null>(null);
}
