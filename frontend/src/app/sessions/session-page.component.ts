import {JsonPipe} from '@angular/common';
import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  input,
  signal,
} from '@angular/core';
import {RouterLink} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  ButtonComponent,
  PageContainerComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {type TaskEvent} from '../api/devclaw.model';
import {DevclawApiService} from '../api/devclaw-api.service';
import {liveFeed} from '../api/live-feed';
import {SectionComponent} from '../shared/section.component';
import {exitVariant} from '../shared/status';
import {StampPipe} from '../shared/time';
import {UsageComponent} from '../shared/usage';

const NOT_FOUND = 404;
const SHORT_ID = 8;
const SUMMARY_KEYS = 4;
const SUMMARY_VALUE = 80;
const SUMMARY_RAW = 160;

/** The first few `key=value` pairs of an event's payload, for one line. */
export function payloadSummary(e: TaskEvent): string {
  try {
    const p: unknown = JSON.parse(e.payloadJson);
    if (p && typeof p === 'object') {
      const entries = Object.entries(p);
      const head = entries
        .slice(0, SUMMARY_KEYS)
        .map(
          ([k, v]) =>
            `${k}=${(typeof v === 'string' ? v : JSON.stringify(v)).slice(0, SUMMARY_VALUE)}`
        );
      return head.join('  ') + (entries.length > SUMMARY_KEYS ? '  …' : '');
    }
  } catch {
    // Not JSON: shown raw below.
  }
  return String(e.payloadJson).slice(0, SUMMARY_RAW);
}

/** A part of the session record that may never have been written. */
function absent(value: unknown): boolean {
  return (
    value === null ||
    value === undefined ||
    (typeof value === 'object' && !Object.keys(value).length)
  );
}

/** One session: its exit, the brief it was given, what it recorded and its event log. */
@Component({
  selector: 'dc-session-page',
  imports: [
    AlertComponent,
    AsyncStateComponent,
    ButtonComponent,
    JsonPipe,
    PageContainerComponent,
    RouterLink,
    SectionComponent,
    StampPipe,
    TagComponent,
    UsageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      @if (error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state [status]="detail() ? 'success' : feed.state().error ? 'idle' : 'loading'">
        @if (detail(); as d) {
          @let t = d.task;
          <div class="flex flex-col gap-cmn-5">
            <header class="flex min-w-0 flex-col gap-cmn-1">
              <h1 class="font-headline text-cmn-2xl font-semibold text-text-primary">
                {{ t.kind === 'review_repository' ? 'Review session' : 'Session' }}
                <span class="font-mono text-cmn-sm text-text-secondary">{{
                  t.id.slice(0, shortId)
                }}</span>
              </h1>
              <p class="flex flex-wrap gap-cmn-3 font-mono text-cmn-xs">
                @if (t.parentGoalId) {
                  <a
                    [routerLink]="['/goals', t.parentGoalId]"
                    class="text-accent-default hover:underline"
                    >goal {{ t.parentGoalId }}</a
                  >
                }
                @if (t.projectId) {
                  <a
                    [routerLink]="['/projects', t.projectId]"
                    class="text-accent-default hover:underline"
                    >project {{ t.projectId }}</a
                  >
                }
                @if (t.prUrl) {
                  <a
                    [href]="t.prUrl"
                    class="text-accent-default hover:underline"
                    target="_blank"
                    rel="noopener noreferrer"
                    >the PR</a
                  >
                }
              </p>
            </header>

            <dc-section heading="Exit">
              <p class="flex flex-wrap items-baseline gap-cmn-2 text-cmn-sm">
                <cmn-tag [variant]="exitVariant(t.exit ?? t.status)">{{
                  t.exit ?? t.status
                }}</cmn-tag>
                @if (t.exitDetail) {
                  <span class="whitespace-pre-wrap text-text-primary">{{ t.exitDetail }}</span>
                }
              </p>
              @if (t.error) {
                <p class="whitespace-pre-wrap text-cmn-sm text-status-error">{{ t.error }}</p>
              }
              <dl
                class="grid grid-cols-[minmax(0,8rem)_minmax(0,1fr)] gap-x-cmn-3 gap-y-cmn-1 font-mono text-cmn-xs text-text-secondary"
              >
                <dt>status</dt>
                <dd>{{ t.status }}</dd>
                <dt>created</dt>
                <dd>{{ t.createdAt | stamp }}</dd>
                <dt>started</dt>
                <dd>{{ t.startedAt | stamp }}</dd>
                <dt>completed</dt>
                <dd>{{ t.completedAt | stamp }}</dd>
                <dt>pre-run sha</dt>
                <dd class="break-all">{{ t.preRunSha || 'not recorded' }}</dd>
                <dt>branch</dt>
                <dd class="break-all">{{ t.targetBranch || 'not recorded' }}</dd>
                <dt>workspace</dt>
                <dd class="break-all">{{ t.workspaceDir }}</dd>
                <dt>delivers</dt>
                <dd>{{ t.deliver ? 'yes' : 'no' }}</dd>
                <dt>tokens</dt>
                <dd><dc-usage [usage]="d.usage" /></dd>
              </dl>
            </dc-section>

            <dc-section heading="Brief the session was given">
              <pre
                class="max-h-96 overflow-auto whitespace-pre-wrap font-mono text-cmn-xs text-text-primary"
                >{{ t.goal || 'not recorded' }}</pre>
            </dc-section>

            @if (d.block; as block) {
              <dc-section heading="Block">
                <p class="whitespace-pre-wrap text-cmn-sm text-text-primary">
                  {{ block.question }}
                </p>
                @if (block.options.length) {
                  <ol class="list-decimal pl-cmn-5 text-cmn-sm text-text-primary">
                    @for (option of block.options; track $index) {
                      <li>
                        {{ option }}
                        @if ($index === block.recommended) {
                          <span class="font-mono text-cmn-xs text-text-secondary">
                            — the session would take this</span
                          >
                        }
                      </li>
                    }
                  </ol>
                }
                @if (block.default && block.recommended < 0) {
                  <p class="text-cmn-sm text-text-secondary">default: {{ block.default }}</p>
                }
                @if (block.kind === 'env') {
                  <p class="text-cmn-sm text-text-secondary">environment gap: {{ block.item }}</p>
                }
              </dc-section>
            }

            @for (part of parts(); track part.title) {
              <dc-section [heading]="part.title">
                @if (part.absent) {
                  <p class="text-cmn-sm text-text-secondary">not recorded</p>
                } @else {
                  <pre
                    class="max-h-[30rem] overflow-auto whitespace-pre-wrap font-mono text-cmn-xs text-text-primary"
                    >{{ part.text ?? (part.value | json) }}</pre>
                }
              </dc-section>
            }

            <dc-section [count]="events().length" heading="Events">
              <ul class="divide-y divide-border-default font-mono text-cmn-xs">
                @for (e of events(); track e.id) {
                  <li class="flex flex-wrap gap-x-cmn-3 gap-y-cmn-1 py-cmn-2">
                    <span class="text-text-secondary">{{ e.ts | stamp }}</span>
                    <span class="text-text-primary">{{ e.type }}</span>
                    <span class="text-text-secondary">{{ e.source }}</span>
                    <span
                      [attr.title]="e.payloadJson"
                      class="min-w-0 basis-full truncate text-text-primary"
                      >{{ summary(e) }}</span
                    >
                  </li>
                } @empty {
                  <li class="text-text-secondary">not recorded</li>
                }
              </ul>
              @if (cursor()) {
                <cmn-button
                  [loading]="loadingMore()"
                  (clicked)="more()"
                  variant="secondary"
                  size="sm"
                  >load more</cmn-button
                >
              }
            </dc-section>
          </div>
        }
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class SessionPageComponent {
  private readonly api = inject(DevclawApiService);

  /** The route's `:id`. */
  public readonly id = input.required<string>();

  /** A session's record is final once it ends; read once per id, not polled. */
  protected readonly feed = liveFeed(id => this.api.task(id), 0, this.id);
  protected readonly detail = computed(() => this.feed.state().data);
  protected readonly error = computed(() => {
    const {error, status} = this.feed.state();
    return status === NOT_FOUND ? `No such session: ${this.id()}` : error;
  });
  protected readonly parts = computed(() => {
    const d = this.detail();
    if (!d) {
      return [];
    }
    return [
      {title: 'Verify', value: d.verify as unknown},
      {title: 'Delivery', value: d.delivery as unknown},
      {title: 'Change span', value: d.change as unknown},
      {title: 'Agent output', value: d.agentOutput as unknown},
    ].map(p => ({
      ...p,
      absent: absent(p.value),
      text: typeof p.value === 'string' ? p.value : null,
    }));
  });
  protected readonly events = signal<TaskEvent[]>([]);
  protected readonly cursor = signal<number | null>(null);
  protected readonly loadingMore = signal(false);
  protected readonly shortId = SHORT_ID;
  protected readonly exitVariant = exitVariant;
  protected readonly summary = payloadSummary;

  constructor() {
    effect(onCleanup => {
      const sub = this.api.taskEvents(this.id()).subscribe({
        next: r => {
          this.events.set(r.events);
          this.cursor.set(r.nextCursor);
        },
        error: () => {
          this.events.set([]);
          this.cursor.set(null);
        },
      });
      onCleanup(() => sub.unsubscribe());
    });
  }

  protected more(): void {
    const cursor = this.cursor();
    if (!cursor || this.loadingMore()) {
      return;
    }
    this.loadingMore.set(true);
    this.api.taskEvents(this.id(), cursor).subscribe({
      next: r => {
        this.events.update(ev => [...ev, ...r.events]);
        this.cursor.set(r.nextCursor);
        this.loadingMore.set(false);
      },
      error: () => this.loadingMore.set(false),
    });
  }
}
