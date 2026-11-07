import {ChangeDetectionStrategy, Component, inject, input} from '@angular/core';
import {Router, RouterLink} from '@angular/router';
import {
  AlertComponent,
  DisclosureRowComponent,
  IconComponent,
  ListItemRowComponent,
  TagComponent,
  type TagVariant,
} from '@lifekit-hq/ui';

import {type VerdictRow} from '../api/devclaw.model';
import {ago} from '../shared/time';

const SHORT_SHA = 7;

export function verdictWord(v: VerdictRow): {text: string; variant: TagVariant} {
  if (v.unreadable) {
    return {text: 'unreadable', variant: 'warning'};
  }
  return v.achieved
    ? {text: 'achieved', variant: 'success'}
    : {text: 'not achieved', variant: 'error'};
}

/**
 * The done-gate's reviews. On the verdicts page (across goals, `pushed`) a row pushes the
 * review's own page (`/verdicts/:id`); on the goal page (its own) each row expands in place to
 * clause by clause with the evidence verbatim, from the row the goal payload carries.
 */
@Component({
  selector: 'dc-verdict-list',
  imports: [
    AlertComponent,
    DisclosureRowComponent,
    IconComponent,
    ListItemRowComponent,
    RouterLink,
    TagComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (v of rows(); track v.taskId) {
      @let word = verdictWord(v);
      @if (pushed()) {
        <cmn-list-item-row
          [label]="(v.unreadable ? v.rawError : v.summary) || word.text"
          [sublabel]="sublabel(v)"
          [action]="true"
          (activated)="open(v)"
        >
          <span meta class="flex items-center gap-cmn-2">
            <cmn-tag [variant]="word.variant">{{ word.text }}</cmn-tag>
            <cmn-icon
              name="ChevronRight"
              size="sm"
              class="text-text-secondary"
              aria-hidden="true"
            />
          </span>
        </cmn-list-item-row>
      } @else {
        <cmn-disclosure-row
          [label]="(v.unreadable ? v.rawError : v.summary) || word.text"
          [sublabel]="sublabel(v)"
          [open]="false"
        >
          <cmn-tag [variant]="word.variant" status>{{ word.text }}</cmn-tag>
          <div class="flex flex-col gap-cmn-2 px-cmn-4 pb-cmn-4 text-cmn-sm">
            @if (v.unreadable) {
              <cmn-alert variant="warning">
                The review produced no readable verdict{{ v.rawError ? ': ' + v.rawError : '' }}.
              </cmn-alert>
            }
            @if (v.clauses.length) {
              <ul class="flex flex-col gap-cmn-2">
                @for (c of v.clauses; track $index) {
                  @let met = c.satisfied && !!c.evidence;
                  <li class="flex gap-cmn-2">
                    <span
                      [class]="met ? 'text-status-success' : 'text-status-error'"
                      class="w-cmn-5 shrink-0 font-mono"
                      >{{ met ? '✓' : '✗' }}</span
                    >
                    <span class="min-w-0 flex-1">
                      <span class="block text-text-primary">{{ c.clause }}</span>
                      <span
                        class="block whitespace-pre-wrap font-mono text-cmn-xs text-text-secondary"
                        >{{ c.evidence || 'no evidence' }}</span
                      >
                    </span>
                  </li>
                }
              </ul>
            }
            @if (v.question) {
              <div>
                <p class="text-cmn-xs font-semibold text-text-secondary">Question for the owner</p>
                <p class="text-text-primary">{{ v.question }}</p>
              </div>
            }
            @if (v.structuralHealth || v.concerns.length) {
              <div>
                <p class="text-cmn-xs font-semibold text-text-secondary">Structural health</p>
                <p class="font-mono text-text-primary">{{ v.structuralHealth || '—' }}</p>
                @for (concern of v.concerns; track $index) {
                  <p class="text-cmn-xs text-text-secondary">· {{ concern }}</p>
                }
              </div>
            }
            <p class="flex flex-wrap gap-cmn-3 font-mono text-cmn-xs">
              @if (showGoal() && v.goalId) {
                <a [routerLink]="['/goals', v.goalId]" class="cmn-link">goal {{ v.goalId }}</a>
              }
              <a [routerLink]="['/sessions', v.taskId]" class="cmn-link">review session</a>
              @if (v.prUrl) {
                <a [href]="v.prUrl" class="cmn-link" target="_blank" rel="noopener noreferrer"
                  >the PR</a
                >
              }
            </p>
          </div>
        </cmn-disclosure-row>
      }
    }
  `,
})
export class VerdictListComponent {
  private readonly router = inject(Router);

  public readonly rows = input.required<VerdictRow[]>();
  /** Across goals, each row names its goal. */
  public readonly showGoal = input(false);
  /** Rows that push the review's page instead of expanding in place. */
  public readonly pushed = input(false);

  protected readonly verdictWord = verdictWord;

  protected sublabel(v: VerdictRow): string {
    const parts = [
      v.total > 0 ? `${v.satisfied}/${v.total} clauses` : '— clauses',
      ...(this.pushed() ? [] : [v.head ? v.head.slice(0, SHORT_SHA) : '—']),
      ago(v.completedAt ?? v.createdAt),
    ];
    return (this.showGoal() ? [v.goalId ?? '—', ...parts] : parts).join(' · ');
  }

  protected open(v: VerdictRow): void {
    void this.router.navigate(['/verdicts', v.taskId]);
  }
}
