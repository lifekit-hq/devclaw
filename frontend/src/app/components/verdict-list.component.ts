import {ChangeDetectionStrategy, Component, inject, input} from '@angular/core';
import {Router} from '@angular/router';
import {IconComponent, ListItemRowComponent, TagComponent, type TagVariant} from '@lifekit-hq/ui';

import {type VerdictRow} from '../api/devclaw.model';
import {ago} from '../shared/time';

export function verdictWord(v: VerdictRow): {text: string; variant: TagVariant} {
  if (v.unreadable) {
    return {text: 'unreadable', variant: 'warning'};
  }
  return v.achieved
    ? {text: 'achieved', variant: 'success'}
    : {text: 'not achieved', variant: 'error'};
}

/**
 * The done-gate's reviews, one line each: the verdict, what it found and when. A row pushes the
 * review's own page (`/verdicts/:id`) with the clauses and their evidence. On the verdicts page
 * (across goals) and on the goal page (its own).
 */
@Component({
  selector: 'dc-verdict-list',
  imports: [IconComponent, ListItemRowComponent, TagComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (v of rows(); track v.taskId) {
      @let word = verdictWord(v);
      <cmn-list-item-row
        [label]="(v.unreadable ? v.rawError : v.summary) || word.text"
        [sublabel]="sublabel(v)"
        [action]="true"
        (activated)="open(v)"
      >
        <span meta class="flex items-center gap-cmn-2">
          <cmn-tag [variant]="word.variant">{{ word.text }}</cmn-tag>
          <cmn-icon name="ChevronRight" size="sm" class="text-text-secondary" aria-hidden="true" />
        </span>
      </cmn-list-item-row>
    }
  `,
})
export class VerdictListComponent {
  private readonly router = inject(Router);

  public readonly rows = input.required<VerdictRow[]>();
  /** Across goals, each row names its goal. */
  public readonly showGoal = input(false);

  protected readonly verdictWord = verdictWord;

  protected sublabel(v: VerdictRow): string {
    const parts = [
      v.total > 0 ? `${v.satisfied}/${v.total} clauses` : '— clauses',
      ago(v.completedAt ?? v.createdAt),
    ];
    return (this.showGoal() ? [v.goalId ?? '—', ...parts] : parts).join(' · ');
  }

  protected open(v: VerdictRow): void {
    void this.router.navigate(['/verdicts', v.taskId]);
  }
}
