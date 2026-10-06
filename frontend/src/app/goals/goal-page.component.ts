import {JsonPipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, computed, inject, input, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  PageContainerComponent,
  StatusIndicatorComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {DevclawApiService, errorText} from '../api/devclaw-api.service';
import {POLL_MS} from '../api/feeds';
import {liveFeed} from '../api/live-feed';
import {AttentionCardComponent} from '../components/attention-card.component';
import {DecideFormComponent} from '../components/decide-form.component';
import {VerdictListComponent} from '../components/verdict-list.component';
import {SectionComponent} from '../shared/section.component';
import {exitVariant, stateIsLive, stateVariant} from '../shared/status';
import {AgoPipe} from '../shared/time';
import {UsageComponent} from '../shared/usage';
import {GoalVerbs} from './goal-verbs.service';

const SHORT_ID = 8;

export function issueUrl(repoUrl: string, issue: number): string {
  return `${repoUrl.replace(/\.git$/, '')}/issues/${issue}`;
}

/** One goal: its contract, what it needs, its sessions, verdicts and decisions. */
@Component({
  selector: 'dc-goal-page',
  imports: [
    AgoPipe,
    AlertComponent,
    AsyncStateComponent,
    AttentionCardComponent,
    DecideFormComponent,
    JsonPipe,
    PageContainerComponent,
    RouterLink,
    SectionComponent,
    StatusIndicatorComponent,
    TagComponent,
    UsageComponent,
    VerdictListComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      @if (error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state [status]="goal() ? 'success' : feed.state().error ? 'idle' : 'loading'">
        <div class="flex flex-col gap-cmn-5">
          @if (goal(); as g) {
            <header class="flex flex-wrap items-start justify-between gap-cmn-4">
              <div class="flex min-w-0 flex-1 basis-64 flex-col gap-cmn-1">
                <h1 class="break-words font-headline text-cmn-2xl font-semibold text-text-primary">
                  {{ g.objective || g.id }}
                </h1>
                <p class="font-mono text-cmn-xs text-text-secondary">
                  {{ g.id }} ·
                  <a
                    [routerLink]="['/projects', g.projectId]"
                    class="text-accent-default hover:underline"
                    >{{ g.projectId }}</a
                  >
                  · <code>{{ g.branch }}</code>
                </p>
                <dc-usage [usage]="g.usage" />
              </div>
              <cmn-status-indicator
                [variant]="stateVariant(g.outcome ?? g.state)"
                [live]="stateIsLive(g.state)"
              >
                {{ g.outcome ?? g.state }}
              </cmn-status-indicator>
            </header>

            <dc-section heading="The contract">
              <p class="flex flex-wrap gap-cmn-3 text-cmn-sm">
                @for (n of g.issues; track n) {
                  <a
                    [href]="issueUrl(g.repoUrl, n)"
                    class="text-accent-default hover:underline"
                    target="_blank"
                    rel="noopener noreferrer"
                    >issue #{{ n }}</a
                  >
                }
                @if (g.lastSession?.prUrl; as pr) {
                  <a
                    [href]="pr"
                    class="text-accent-default hover:underline"
                    target="_blank"
                    rel="noopener noreferrer"
                    >the PR</a
                  >
                }
              </p>
            </dc-section>

            @if (g.attention; as attention) {
              <dc-section heading="Needs you">
                <dc-attention-card
                  [goalId]="g.id"
                  [attention]="attention"
                  [cancellable]="true"
                  (decided)="feed.reload()"
                  (cancelRequested)="cancel()"
                />
              </dc-section>
            }

            @if (!g.outcome) {
              <dc-section heading="Decide">
                <p class="text-cmn-sm text-text-secondary">
                  Posted on the issue as an instruction mentioning the bot; the next tick reads it.
                  This is the owner's one verb.
                </p>
                <dc-decide-form
                  [goalId]="g.id"
                  [cancellable]="true"
                  (decided)="feed.reload()"
                  (cancelRequested)="cancel()"
                  placeholder="e.g. use SQLite; or: accept the gate's finding as a follow-up and close"
                />
              </dc-section>
            }

            <dc-section [count]="g.sessions.length" heading="Sessions">
              <ul class="divide-y divide-border-default text-cmn-sm">
                @for (s of g.sessions; track s.id) {
                  <li class="flex flex-wrap items-center gap-x-cmn-3 gap-y-cmn-1 py-cmn-2">
                    <span class="font-mono text-cmn-xs text-text-secondary">
                      {{ s.createdAt | ago }} ·
                      {{ s.kind === 'review_repository' ? 'review' : 'session' }}
                    </span>
                    <cmn-tag [variant]="exitVariant(s.exit ?? s.status)">{{
                      s.exit ?? s.status
                    }}</cmn-tag>
                    <span class="min-w-0 flex-1 truncate text-text-primary">{{
                      s.exitDetail
                    }}</span>
                    <dc-usage [usage]="s.usage" [compact]="true" />
                    <a
                      [routerLink]="['/sessions', s.id]"
                      class="font-mono text-cmn-xs text-accent-default hover:underline"
                      >{{ s.id.slice(0, shortId) }} →</a
                    >
                  </li>
                } @empty {
                  <li class="text-text-secondary">No session yet.</li>
                }
              </ul>
            </dc-section>

            <dc-section [count]="g.verdicts.length" heading="Verdicts">
              @if (g.verdicts.length) {
                <dc-verdict-list [rows]="g.verdicts" />
              } @else {
                <p class="text-cmn-sm text-text-secondary">No done-gate review yet.</p>
              }
            </dc-section>

            <dc-section heading="Decisions">
              <ul class="divide-y divide-border-default text-cmn-sm">
                @for (d of g.decisions; track d.id) {
                  <li class="py-cmn-2 text-text-primary">
                    <span class="font-mono text-cmn-xs text-text-secondary">{{
                      d.madeAt | ago
                    }}</span>
                    — {{ d.text }}
                    @if (d.commentUrl) {
                      ·
                      <a
                        [href]="d.commentUrl"
                        class="text-accent-default hover:underline"
                        target="_blank"
                        rel="noopener noreferrer"
                        >thread</a
                      >
                    }
                  </li>
                } @empty {
                  <li class="text-text-secondary">None.</li>
                }
              </ul>
            </dc-section>

            <dc-section heading="Last world the session was given">
              <pre
                class="overflow-auto whitespace-pre-wrap font-mono text-cmn-xs text-text-primary"
                >{{ g.lastSeen ? (g.lastSeen | json) : '—' }}</pre>
            </dc-section>
          }
        </div>
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class GoalPageComponent {
  private readonly api = inject(DevclawApiService);
  private readonly verbs = inject(GoalVerbs);
  private readonly verbError = signal<string | null>(null);

  /** The route's `:id`. */
  public readonly id = input.required<string>();

  protected readonly feed = liveFeed(id => this.api.goal(id), POLL_MS, this.id);
  protected readonly goal = computed(() => this.feed.state().data);
  protected readonly error = computed(() => this.verbError() ?? this.feed.state().error);
  protected readonly shortId = SHORT_ID;
  protected readonly issueUrl = issueUrl;
  protected readonly stateVariant = stateVariant;
  protected readonly stateIsLive = stateIsLive;
  protected readonly exitVariant = exitVariant;

  protected cancel(): void {
    this.verbError.set(null);
    this.verbs.cancel(this.id()).subscribe({
      next: cancelled => cancelled && this.feed.reload(),
      error: (e: unknown) => this.verbError.set(errorText(e)),
    });
  }
}
