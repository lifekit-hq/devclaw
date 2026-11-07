import {ChangeDetectionStrategy, Component, computed, inject, input} from '@angular/core';
import {RouterLink} from '@angular/router';
import {AlertComponent, AsyncStateComponent, PageComponent, TagComponent} from '@lifekit-hq/ui';

import {DevclawApiService} from '../api/devclaw-api.service';
import {feedStatus, liveFeed} from '../api/live-feed';
import {verdictWord} from '../components/verdict-list.component';
import {SectionComponent} from '../shared/section.component';
import {ago} from '../shared/time';

const LIMIT = 200;
const SHORT_SHA = 7;

/**
 * One done-gate review, clause by clause with the evidence verbatim. It reads the same feed as
 * the verdicts list, so a review older than the newest few hundred is not reachable here.
 */
@Component({
  selector: 'dc-verdict-page',
  imports: [
    AlertComponent,
    AsyncStateComponent,
    PageComponent,
    RouterLink,
    SectionComponent,
    TagComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page kind="detail">
      @if (feed.state().data && feed.state().error; as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feedStatus(feed.state())"
        [errorMessage]="feed.state().error ?? ''"
        (retry)="feed.reload()"
        emptySubMessage="This review has no detail to show."
        class="block"
        retryable
      >
        @if (verdict(); as v) {
          @let word = verdictWord(v);
          <div class="flex flex-col gap-cmn-section-gap">
            <header class="flex min-w-0 flex-col gap-cmn-2">
              <p class="flex flex-wrap items-baseline gap-cmn-2 text-cmn-sm">
                <cmn-tag [variant]="word.variant">{{ word.text }}</cmn-tag>
                <span class="font-mono text-cmn-xs text-text-secondary">{{ meta() }}</span>
              </p>
              @if (v.summary) {
                <p class="text-cmn-sm text-text-primary">{{ v.summary }}</p>
              }
              <p class="flex flex-wrap gap-cmn-3 font-mono text-cmn-xs">
                @if (v.goalId) {
                  <a [routerLink]="['/goals', v.goalId]" class="cmn-link">goal {{ v.goalId }}</a>
                }
                <a [routerLink]="['/sessions', v.taskId]" class="cmn-link">review session</a>
                @if (v.prUrl) {
                  <a [href]="v.prUrl" class="cmn-link" target="_blank" rel="noopener noreferrer"
                    >the PR</a
                  >
                }
              </p>
            </header>

            @if (v.unreadable) {
              <cmn-alert variant="warning">
                The review produced no readable verdict{{ v.rawError ? ': ' + v.rawError : '' }}.
              </cmn-alert>
            }

            @if (v.clauses.length) {
              <dc-section [count]="v.clauses.length" heading="Clauses">
                <ul class="flex flex-col gap-cmn-3 text-cmn-sm">
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
                          class="block whitespace-pre-wrap break-words font-mono text-cmn-xs text-text-secondary"
                          >{{ c.evidence || 'no evidence' }}</span
                        >
                      </span>
                    </li>
                  }
                </ul>
              </dc-section>
            }

            @if (v.question) {
              <dc-section heading="Question for the owner">
                <p class="whitespace-pre-wrap text-cmn-sm text-text-primary">{{ v.question }}</p>
              </dc-section>
            }

            @if (v.structuralHealth || v.concerns.length) {
              <dc-section heading="Structural health">
                <p class="font-mono text-cmn-sm text-text-primary">
                  {{ v.structuralHealth || '—' }}
                </p>
                @for (concern of v.concerns; track $index) {
                  <p class="text-cmn-xs text-text-secondary">· {{ concern }}</p>
                }
              </dc-section>
            }
          </div>
        } @else if (feed.state().data) {
          <cmn-alert variant="error">No such verdict: {{ id() }}</cmn-alert>
        }
      </cmn-async-state>
    </cmn-page>
  `,
})
export class VerdictPageComponent {
  private readonly api = inject(DevclawApiService);

  /** The route's `:id`: the review session's task id. */
  public readonly id = input.required<string>();

  protected readonly feedStatus = feedStatus;
  protected readonly verdictWord = verdictWord;
  protected readonly feed = liveFeed(() => this.api.verdicts(LIMIT), 0);
  protected readonly verdict = computed(
    () => this.feed.state().data?.verdicts.find(v => v.taskId === this.id()) ?? null
  );
  protected readonly meta = computed(() => {
    const v = this.verdict();
    return v
      ? [
          v.total > 0 ? `${v.satisfied}/${v.total} clauses` : '— clauses',
          v.head ? v.head.slice(0, SHORT_SHA) : '—',
          ago(v.completedAt ?? v.createdAt),
        ].join(' · ')
      : '';
  });
}
