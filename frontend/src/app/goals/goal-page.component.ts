import {JsonPipe} from '@angular/common';
import {ChangeDetectionStrategy, Component, computed, inject, input, signal} from '@angular/core';
import {RouterLink} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  ListItemRowComponent,
  PageContainerComponent,
  StatusIndicatorComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {type SessionRow} from '../api/devclaw.model';
import {DevclawApiService, errorText} from '../api/devclaw-api.service';
import {POLL_MS} from '../api/feeds';
import {feedStatus, liveFeed} from '../api/live-feed';
import {AttentionCardComponent} from '../components/attention-card.component';
import {DecideFormComponent} from '../components/decide-form.component';
import {VerdictListComponent} from '../components/verdict-list.component';
import {SectionComponent} from '../shared/section.component';
import {exitVariant, stateIsLive, stateVariant} from '../shared/status';
import {ago, AgoPipe} from '../shared/time';
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
    ListItemRowComponent,
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
      @if (feed.state().data && error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feedStatus(feed.state())"
        [errorMessage]="error() ?? ''"
        (retry)="feed.reload()"
        retryable
      >
        <div class="flex flex-col gap-cmn-5">
          @if (goal(); as g) {
            <header class="flex flex-wrap items-start justify-between gap-cmn-4">
              <div class="flex min-w-0 flex-1 basis-64 flex-col gap-cmn-1">
                @if (g.objective) {
                  <p class="break-words text-cmn-md font-medium text-text-primary">
                    {{ g.objective }}
                  </p>
                }
                <p class="font-mono text-cmn-xs text-text-secondary">
                  <a [routerLink]="['/projects', g.projectId]" class="cmn-link">{{
                    g.projectId
                  }}</a>
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
                    class="cmn-link"
                    target="_blank"
                    rel="noopener noreferrer"
                    >issue #{{ n }}</a
                  >
                }
                @if (g.lastSession?.prUrl; as pr) {
                  <a [href]="pr" class="cmn-link" target="_blank" rel="noopener noreferrer"
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
                  Posted on the issue as an instruction mentioning the bot; it is read on the next
                  check. This is the owner's one verb.
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

            <dc-section [count]="g.sessions.length" [flush]="true" heading="Sessions">
              @for (s of g.sessions; track s.id) {
                <cmn-list-item-row [label]="s.exitDetail || '—'" [sublabel]="sublabel(s)">
                  <span meta class="flex items-center gap-cmn-2">
                    <cmn-tag [variant]="exitVariant(s.exit ?? s.status)">{{
                      s.exit ?? s.status
                    }}</cmn-tag>
                    <dc-usage [usage]="s.usage" [compact]="true" class="hidden sm:inline" />
                  </span>
                  <a
                    [routerLink]="['/sessions', s.id]"
                    actions
                    class="cmn-link cmn-hit-slop font-mono text-cmn-xs"
                    >{{ s.id.slice(0, shortId) }} →</a
                  >
                </cmn-list-item-row>
              } @empty {
                <p class="px-cmn-4 py-cmn-3 text-cmn-sm text-text-secondary">No session yet.</p>
              }
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
                        class="cmn-link cmn-link--underlined"
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
              <pre class="cmn-code-block cmn-code-block--bare">{{
                g.lastSeen ? (g.lastSeen | json) : '—'
              }}</pre>
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

  protected readonly feedStatus = feedStatus;
  protected readonly feed = liveFeed(id => this.api.goal(id), POLL_MS, this.id);
  protected readonly goal = computed(() => this.feed.state().data);
  protected readonly error = computed(() => this.verbError() ?? this.feed.state().error);
  protected readonly shortId = SHORT_ID;
  protected readonly issueUrl = issueUrl;
  protected readonly stateVariant = stateVariant;
  protected readonly stateIsLive = stateIsLive;
  protected readonly exitVariant = exitVariant;

  protected sublabel(s: SessionRow): string {
    return `${ago(s.createdAt)} · ${s.kind === 'review_repository' ? 'review' : 'session'}`;
  }

  protected cancel(): void {
    this.verbError.set(null);
    this.verbs.cancel(this.id()).subscribe({
      next: cancelled => cancelled && this.feed.reload(),
      error: (e: unknown) => this.verbError.set(errorText(e)),
    });
  }
}
