import {ChangeDetectionStrategy, Component, computed, inject, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  CardComponent,
  EmptyStateComponent,
  PageContainerComponent,
  PageHeaderComponent,
} from '@lifekit-hq/ui';

import {type GoalRow} from '../api/devclaw.model';
import {errorText} from '../api/devclaw-api.service';
import {GoalsFeed} from '../api/feeds';
import {AttentionCardComponent} from '../components/attention-card.component';
import {GoalVerbs} from '../goals/goal-verbs.service';
import {DispatchStatusComponent} from '../shared/dispatch-status.component';

type Asking = GoalRow & {attention: NonNullable<GoalRow['attention']>};

const asking = (g: GoalRow): g is Asking => g.attention !== null;

/**
 * The owner's one page: every goal waiting on a decision, oldest first, then the ones already
 * answered and waiting for the tick to read the answer.
 */
@Component({
  selector: 'dc-needs-you-page',
  imports: [
    AlertComponent,
    AsyncStateComponent,
    AttentionCardComponent,
    CardComponent,
    DispatchStatusComponent,
    EmptyStateComponent,
    PageContainerComponent,
    PageHeaderComponent,
    RouterLink,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      <cmn-page-header
        title="Needs you"
        subtitle="A click posts your decision on the issue; the next tick reads it. The options are the session's own."
      />
      <dc-dispatch-status />
      @if (error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feed.state().data ? 'success' : feed.state().error ? 'idle' : 'loading'"
      >
        <div class="flex flex-col gap-cmn-5">
          @if (feed.state().data && !open().length && !answered().length) {
            <cmn-empty-state
              message="Nothing needs you"
              subMessage="Every open goal is running, waiting on the world, or answered."
              icon="CircleCheck"
            />
          }
          @for (group of groups(); track group.title) {
            @if (group.goals.length) {
              <section [attr.aria-label]="group.title" class="flex flex-col gap-cmn-3">
                <h2 class="font-headline text-cmn-sm font-semibold text-text-secondary">
                  {{ group.title }} · {{ group.goals.length }}
                </h2>
                @for (g of group.goals; track g.id) {
                  <cmn-card>
                    <article
                      [attr.aria-label]="g.objective || g.id"
                      class="flex flex-col gap-cmn-2"
                    >
                      <header class="flex flex-wrap items-baseline justify-between gap-cmn-2">
                        <a
                          [routerLink]="['/goals', g.id]"
                          class="min-w-0 truncate font-medium text-text-primary hover:underline"
                        >
                          {{ g.objective || g.id }}
                        </a>
                        <span class="font-mono text-cmn-xs text-text-secondary"
                          >{{ g.id }} · {{ g.projectId }}</span
                        >
                      </header>
                      <dc-attention-card
                        [goalId]="g.id"
                        [attention]="g.attention"
                        [cancellable]="true"
                        (decided)="feed.reload()"
                        (cancelRequested)="cancel(g.id)"
                      />
                    </article>
                  </cmn-card>
                }
              </section>
            }
          }
        </div>
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class NeedsYouPageComponent {
  private readonly verbs = inject(GoalVerbs);
  private readonly verbError = signal<string | null>(null);

  protected readonly feed = inject(GoalsFeed);
  protected readonly error = computed(() => this.verbError() ?? this.feed.state().error);
  protected readonly open = computed(() =>
    (this.feed.state().data ?? [])
      .filter(asking)
      .filter(g => !g.attention.answered)
      .sort((a, b) => a.attention.since - b.attention.since)
  );
  protected readonly answered = computed(() =>
    (this.feed.state().data ?? []).filter(asking).filter(g => g.attention.answered)
  );
  protected readonly groups = computed(() => [
    {title: 'Waiting on you', goals: this.open()},
    {title: 'Answered, waiting for the tick', goals: this.answered()},
  ]);

  protected cancel(goalId: string): void {
    this.verbError.set(null);
    this.verbs.cancel(goalId).subscribe({
      next: cancelled => cancelled && this.feed.reload(),
      error: (e: unknown) => this.verbError.set(errorText(e)),
    });
  }
}
