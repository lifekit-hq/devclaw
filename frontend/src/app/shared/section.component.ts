import {NgTemplateOutlet} from '@angular/common';
import {ChangeDetectionStrategy, Component, input} from '@angular/core';
import {CardComponent, ListSectionComponent} from '@lifekit-hq/ui';

/**
 * A titled section on a detail page: the kit's list-section heading, an optional count at its
 * end, then the body in a card. `flush` hands the body the kit's unpadded card instead, for rows
 * that draw their own padding and dividers.
 */
@Component({
  selector: 'dc-section',
  imports: [CardComponent, ListSectionComponent, NgTemplateOutlet],
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {class: 'block'},
  template: `
    <cmn-list-section [heading]="heading()" [card]="flush()">
      @if (count() !== null) {
        <span cmnListSectionAside class="font-mono font-normal">{{ count() }}</span>
      }
      @if (flush()) {
        <ng-container *ngTemplateOutlet="body" />
      } @else {
        <cmn-card>
          <div class="flex flex-col gap-cmn-2">
            <ng-container *ngTemplateOutlet="body" />
          </div>
        </cmn-card>
      }
    </cmn-list-section>
    <ng-template #body><ng-content /></ng-template>
  `,
})
export class SectionComponent {
  public readonly heading = input.required<string>();
  public readonly count = input<number | null>(null);
  public readonly flush = input(false);
}
