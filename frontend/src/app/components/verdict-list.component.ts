import {ChangeDetectionStrategy, Component, input} from '@angular/core';
import {RouterLink} from '@angular/router';
import {DisclosureRowComponent, TagComponent, type TagVariant} from '@lifekit-hq/ui';

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
 * The done-gate's reviews, each expandable to clause by clause with the evidence verbatim. On
 * the verdicts page (across goals) and on the goal page (its own).
 */
@Component({
  selector: 'dc-verdict-list',
  imports: [DisclosureRowComponent, RouterLink, TagComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @for (v of rows(); track v.taskId) {
      @let word = verdictWord(v);
      <cmn-disclosure-row
        [label]="(v.unreadable ? v.rawError : v.summary) || word.text"
        [sublabel]="sublabel(v)"
        [open]="false"
      >
        <cmn-tag [variant]="word.variant" status>{{ word.text }}</cmn-tag>
        <div class="flex flex-col gap-cmn-2 px-cmn-4 pb-cmn-4 text-cmn-sm">
          @if (v.unreadable) {
            <p class="text-status-warning">
              The review produced no readable verdict{{ v.rawError ? ': ' + v.rawError : '' }}.
            </p>
          }
          @if (v.clauses.length) {
            <ul class="flex flex-col gap-cmn-2">
              @for (c of v.clauses; track $index) {
                @let met = c.satisfied && !!c.evidence;
                <li class="grid grid-cols-[1.25rem_minmax(0,1fr)] gap-cmn-2">
                  <span
                    [class]="met ? 'text-status-success' : 'text-status-error'"
                    class="font-mono"
                    >{{ met ? '✓' : '✗' }}</span
                  >
                  <span>
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
              <h3 class="text-cmn-xs font-semibold text-text-secondary">Question for the owner</h3>
              <p class="text-text-primary">{{ v.question }}</p>
            </div>
          }
          @if (v.structuralHealth || v.concerns.length) {
            <div>
              <h3 class="text-cmn-xs font-semibold text-text-secondary">Structural health</h3>
              <p class="font-mono text-text-primary">{{ v.structuralHealth || '—' }}</p>
              @for (concern of v.concerns; track $index) {
                <p class="text-cmn-xs text-text-secondary">· {{ concern }}</p>
              }
            </div>
          }
          <p class="flex flex-wrap gap-cmn-3 font-mono text-cmn-xs">
            @if (showGoal() && v.goalId) {
              <a [routerLink]="['/goals', v.goalId]" class="text-accent-default hover:underline"
                >goal {{ v.goalId }}</a
              >
            }
            <a [routerLink]="['/sessions', v.taskId]" class="text-accent-default hover:underline"
              >review session</a
            >
            @if (v.prUrl) {
              <a
                [href]="v.prUrl"
                class="text-accent-default hover:underline"
                target="_blank"
                rel="noopener noreferrer"
                >the PR</a
              >
            }
          </p>
        </div>
      </cmn-disclosure-row>
    }
  `,
})
export class VerdictListComponent {
  public readonly rows = input.required<VerdictRow[]>();
  /** Across goals, each row names its goal. */
  public readonly showGoal = input(false);

  protected readonly verdictWord = verdictWord;

  protected sublabel(v: VerdictRow): string {
    const parts = [
      v.total > 0 ? `${v.satisfied}/${v.total} clauses` : '— clauses',
      v.head ? v.head.slice(0, SHORT_SHA) : '—',
      ago(v.completedAt ?? v.createdAt),
    ];
    return (this.showGoal() ? [v.goalId ?? '—', ...parts] : parts).join(' · ');
  }
}
