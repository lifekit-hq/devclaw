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
  CheckboxComponent,
  ListItemRowComponent,
  PageComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {type TaskEvent, type TaskRow} from '../api/devclaw.model';
import {DevclawApiService} from '../api/devclaw-api.service';
import {feedStatus, liveFeed} from '../api/live-feed';
import {SectionComponent} from '../shared/section.component';
import {exitVariant} from '../shared/status';
import {ago, utcSecond} from '../shared/time';
import {UsageComponent} from '../shared/usage';

const NOT_FOUND = 404;
const SUMMARY_KEYS = 4;
const SUMMARY_VALUE = 80;
const SUMMARY_RAW = 160;
const SHORT_SHA = 7;
/** Events fetched per request. */
export const EVENT_PAGE = 100;
const FACT_VALUE = 120;
const INDENT = 2;
/** Parts of the record shown as short `key: value` lists instead of JSON. */
const FACT_PARTS = new Set(['Verify', 'Delivery']);

/** One `key: value` fact of a recorded object; nested values are cut to one short line. */
export interface Fact {
  key: string;
  value: string;
}

/** A recorded object as short facts — never the whole JSON. */
export function factList(value: unknown): Fact[] {
  if (!value || typeof value !== 'object') {
    return [];
  }
  return Object.entries(value).map(([key, v]) => {
    const text = typeof v === 'string' ? v : (JSON.stringify(v) ?? String(v));
    return {key, value: text.length > FACT_VALUE ? `${text.slice(0, FACT_VALUE)}…` : text};
  });
}

/** An event's payload, indented for the disclosure; text that is not JSON stays as written. */
export function prettyPayload(payloadJson: string): string {
  try {
    return JSON.stringify(JSON.parse(payloadJson), null, INDENT);
  } catch {
    return payloadJson;
  }
}

/** One `name: value` row of the record; a row with `at` is a time and can show its exact form. */
interface RecordRow {
  name: string;
  value: string;
  at?: number | null;
  title?: string;
}

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
    CheckboxComponent,
    JsonPipe,
    ListItemRowComponent,
    PageComponent,
    RouterLink,
    SectionComponent,
    TagComponent,
    UsageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page kind="detail">
      @if (feed.state().data && error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feedStatus(feed.state())"
        [errorMessage]="error() ?? ''"
        (retry)="feed.reload()"
        skeletonShape="detail"
        emptySubMessage="This session has no detail to show yet."
        class="block"
        retryable
      >
        @if (detail(); as d) {
          @let t = d.task;
          <div class="flex flex-col gap-cmn-section-gap">
            <header class="flex min-w-0 flex-col gap-cmn-1">
              @if (t.kind === 'review_repository') {
                <p class="text-cmn-md font-medium text-text-primary">Review session</p>
              }
              <p class="flex flex-wrap gap-cmn-3 font-mono text-cmn-xs">
                @if (t.parentGoalId) {
                  <a [routerLink]="['/goals', t.parentGoalId]" class="cmn-link"
                    >goal {{ t.parentGoalId }}</a
                  >
                }
                @if (t.projectId) {
                  <a [routerLink]="['/projects', t.projectId]" class="cmn-link"
                    >project {{ t.projectId }}</a
                  >
                }
                @if (t.prUrl) {
                  <a [href]="t.prUrl" class="cmn-link" target="_blank" rel="noopener noreferrer"
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
                <cmn-alert variant="error"
                  ><span class="whitespace-pre-wrap">{{ t.error }}</span></cmn-alert
                >
              }
              <cmn-checkbox [checked]="exactTimes()" (changed)="exactTimes.set($event)">
                Exact times (UTC)
              </cmn-checkbox>
              <dl class="flex flex-col gap-cmn-1 font-mono text-cmn-xs text-text-secondary">
                @for (row of record(t); track row.name) {
                  <div class="flex gap-cmn-3">
                    <dt class="w-32 shrink-0">{{ row.name }}</dt>
                    <dd [attr.title]="row.title" class="min-w-0 flex-1 break-words">
                      @if (row.at) {
                        <time
                          [attr.datetime]="isoTime(row.at)"
                          [attr.title]="utcSecond(row.at)"
                          class="whitespace-nowrap"
                          >{{ exactTimes() ? utcSecond(row.at) : ago(row.at) }}</time
                        >
                      } @else {
                        {{ row.value }}
                      }
                    </dd>
                  </div>
                }
                <div class="flex gap-cmn-3">
                  <dt class="w-32 shrink-0">tokens</dt>
                  <dd class="min-w-0 flex-1"><dc-usage [usage]="d.usage" /></dd>
                </div>
              </dl>
            </dc-section>

            <dc-section heading="Brief the session was given">
              <pre
                class="cmn-code-block cmn-code-block--bare dc-code-capped"
                aria-label="Brief the session was given"
                role="group"
                tabindex="0"
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
                } @else if (part.facts; as facts) {
                  <dl
                    [attr.aria-label]="part.title"
                    class="flex flex-col gap-cmn-1 font-mono text-cmn-xs text-text-secondary"
                  >
                    @for (f of facts; track f.key) {
                      <div class="flex gap-cmn-3">
                        <dt class="w-32 shrink-0">{{ f.key }}</dt>
                        <dd class="min-w-0 flex-1 break-words text-text-primary">{{ f.value }}</dd>
                      </div>
                    }
                  </dl>
                } @else {
                  <pre
                    [attr.aria-label]="part.title"
                    class="cmn-code-block cmn-code-block--bare dc-code-capped"
                    role="group"
                    tabindex="0"
                    >{{ part.text ?? (part.value | json) }}</pre>
                }
              </dc-section>
            }

            <dc-section [count]="events().length" [flush]="true" heading="Events">
              @for (e of events(); track e.id) {
                <div class="border-b border-border-default last:border-b-0">
                  <cmn-list-item-row [label]="e.type" [sublabel]="eventLine(e)" />
                  <div class="px-cmn-4 pb-cmn-2">
                    <cmn-button
                      [attr.aria-expanded]="isOpen(e.id)"
                      (clicked)="toggle(e.id)"
                      variant="secondary"
                      >{{ isOpen(e.id) ? 'hide payload' : 'show payload' }}</cmn-button
                    >
                    @if (isOpen(e.id)) {
                      <pre
                        class="cmn-code-block cmn-code-block--bare dc-code-capped mt-cmn-1"
                        role="group"
                        aria-label="Event payload"
                        tabindex="0"
                        >{{ payload(e) }}</pre>
                    }
                  </div>
                </div>
              } @empty {
                <p class="px-cmn-4 py-cmn-3 text-cmn-sm text-text-secondary">not recorded</p>
              }
              @if (cursor()) {
                <div class="border-t border-border-default p-cmn-3">
                  <cmn-button [loading]="loadingMore()" (clicked)="more()" variant="secondary"
                    >load more</cmn-button
                  >
                </div>
              }
            </dc-section>
          </div>
        }
      </cmn-async-state>
    </cmn-page>
  `,
})
export class SessionPageComponent {
  private readonly api = inject(DevclawApiService);

  /** Events whose payload is expanded; a closed row holds no payload in the page. */
  private readonly open = signal<ReadonlySet<number>>(new Set());

  /** The route's `:id`. */
  public readonly id = input.required<string>();

  protected readonly feedStatus = feedStatus;

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
      facts:
        FACT_PARTS.has(p.title) && !absent(p.value) && typeof p.value === 'object'
          ? factList(p.value)
          : null,
    }));
  });
  protected readonly events = signal<TaskEvent[]>([]);
  protected readonly cursor = signal<number | null>(null);
  protected readonly loadingMore = signal(false);
  protected readonly exitVariant = exitVariant;
  protected readonly ago = ago;
  protected readonly utcSecond = utcSecond;
  /** Every time on the record shows its UTC second instead of how long ago. */
  protected readonly exactTimes = signal(false);

  constructor() {
    effect(onCleanup => {
      const sub = this.api.taskEvents(this.id(), null, EVENT_PAGE).subscribe({
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

  protected isoTime(ms: number): string {
    return new Date(ms).toISOString();
  }

  /** The record's plain facts, one `name: value` row each. */
  protected record(t: TaskRow): RecordRow[] {
    return [
      {name: 'status', value: t.status},
      {name: 'created', value: ago(t.createdAt), at: t.createdAt},
      {name: 'started', value: ago(t.startedAt), at: t.startedAt},
      {name: 'completed', value: ago(t.completedAt), at: t.completedAt},
      {
        name: 'pre-run sha',
        value: t.preRunSha ? t.preRunSha.slice(0, SHORT_SHA) : 'not recorded',
        title: t.preRunSha || undefined,
      },
      {name: 'branch', value: t.targetBranch || 'not recorded'},
      {name: 'workspace', value: t.workspaceDir},
      {name: 'delivers', value: t.deliver ? 'yes' : 'no'},
    ];
  }

  protected isOpen(id: number): boolean {
    return this.open().has(id);
  }

  protected toggle(id: number): void {
    this.open.update(o => {
      const next = new Set(o);
      if (!next.delete(id)) {
        next.add(id);
      }
      return next;
    });
  }

  protected payload(e: TaskEvent): string {
    return prettyPayload(e.payloadJson);
  }

  protected eventLine(e: TaskEvent): string {
    return `${utcSecond(e.ts)} · ${e.source} · ${payloadSummary(e)}`;
  }

  protected more(): void {
    const cursor = this.cursor();
    if (!cursor || this.loadingMore()) {
      return;
    }
    this.loadingMore.set(true);
    this.api.taskEvents(this.id(), cursor, EVENT_PAGE).subscribe({
      next: r => {
        this.events.update(ev => [...ev, ...r.events]);
        this.cursor.set(r.nextCursor);
        this.loadingMore.set(false);
      },
      error: () => this.loadingMore.set(false),
    });
  }
}
