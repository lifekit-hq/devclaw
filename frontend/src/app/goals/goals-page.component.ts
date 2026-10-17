import {ChangeDetectionStrategy, Component, computed, inject, signal} from '@angular/core';
import {Router, RouterLink} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  CmnCellDirective,
  CmnColumnComponent,
  DataTableComponent,
  EmptyStateComponent,
  PageContainerComponent,
  SegmentedComponent,
  type SegmentedOption,
  StatusIndicatorComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {type GoalRow} from '../api/devclaw.model';
import {GoalsFeed} from '../api/feeds';
import {feedStatus} from '../api/live-feed';
import {exitVariant, stateIsLive, stateVariant} from '../shared/status';
import {AgoPipe} from '../shared/time';
import {UsageComponent} from '../shared/usage';

type Filter = 'open' | 'blocked' | 'closed' | 'all';

const FILTERS: {id: Filter; label: string}[] = [
  {id: 'open', label: 'Open'},
  {id: 'blocked', label: 'Blocked'},
  {id: 'closed', label: 'Closed'},
  {id: 'all', label: 'All'},
];

function matches(g: GoalRow, filter: Filter): boolean {
  switch (filter) {
    case 'all':
      return true;
    case 'closed':
      return !!g.outcome;
    case 'blocked':
      return g.state === 'blocked';
    default:
      return !g.outcome;
  }
}

/** Every goal: one issue, one branch, one PR, filtered by where it stands. */
@Component({
  selector: 'dc-goals-page',
  imports: [
    AgoPipe,
    AlertComponent,
    AsyncStateComponent,
    CmnCellDirective,
    CmnColumnComponent,
    DataTableComponent,
    EmptyStateComponent,
    PageContainerComponent,
    RouterLink,
    SegmentedComponent,
    StatusIndicatorComponent,
    TagComponent,
    UsageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      <p class="text-cmn-sm text-text-secondary">
        Every goal devclaw is working on, one per GitHub issue.
      </p>
      <cmn-segmented
        [options]="options()"
        [value]="filter()"
        (valueChange)="choose($event)"
        label="Filter goals"
      />
      @if (feed.state().data && feed.state().error; as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feedStatus(feed.state())"
        [errorMessage]="feed.state().error ?? ''"
        (retry)="feed.reload()"
        retryable
      >
        @if (!shown().length) {
          <cmn-empty-state message="Nothing here" subMessage="No goals match this filter." />
        } @else {
          <cmn-data-table
            [rows]="shown()"
            [trackBy]="trackById"
            [rowsActionable]="true"
            (rowClick)="open($event)"
          >
            <cmn-column key="objective" header="Goal" listSlot="primary">
              <ng-template let-g cmnCell>
                <span [class.opacity-60]="!!g.outcome" class="block min-w-0">
                  <span class="block truncate font-medium">{{ g.objective || g.id }}</span>
                  <span class="block truncate font-mono text-cmn-xs text-text-secondary">
                    {{ g.id }} · #{{ g.issues.join(', #') }}
                  </span>
                </span>
              </ng-template>
            </cmn-column>
            <cmn-column key="projectId" header="Project" listSlot="secondary" />
            <cmn-column key="state" header="State" listSlot="trailing">
              <ng-template let-g cmnCell>
                <cmn-status-indicator
                  [variant]="stateVariant(g.outcome ?? g.state)"
                  [live]="stateIsLive(g.state)"
                >
                  {{ g.outcome ?? g.state }}
                </cmn-status-indicator>
              </ng-template>
            </cmn-column>
            <cmn-column key="lastSession" header="Last session">
              <ng-template let-g cmnCell>
                @if (g.lastSession; as s) {
                  <a
                    [routerLink]="['/sessions', s.id]"
                    (click)="$event.stopPropagation()"
                    class="cmn-link cmn-link--inherit"
                    title="open the session"
                  >
                    <cmn-tag [variant]="exitVariant(s.exit ?? s.status)">
                      {{ s.exit ?? s.status }}{{ s.exitDetail ? ': ' + s.exitDetail : '' }}
                    </cmn-tag>
                  </a>
                } @else {
                  —
                }
              </ng-template>
            </cmn-column>
            <cmn-column key="usage" header="Tokens">
              <ng-template let-g cmnCell
                ><dc-usage [usage]="g.usage" [compact]="true"
              /></ng-template>
            </cmn-column>
            <cmn-column key="lastSeenAt" header="Seen" align="right" listSlot="trailing-secondary">
              <ng-template let-g cmnCell>{{ g.lastSeenAt | ago }}</ng-template>
            </cmn-column>
          </cmn-data-table>
        }
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class GoalsPageComponent {
  private readonly router = inject(Router);

  protected readonly feedStatus = feedStatus;
  protected readonly feed = inject(GoalsFeed);
  protected readonly filter = signal<Filter>('open');
  protected readonly counts = computed(() => {
    const goals = this.feed.state().data ?? [];
    return Object.fromEntries(
      FILTERS.map(f => [f.id, goals.filter(g => matches(g, f.id)).length])
    ) as Record<Filter, number>;
  });
  protected readonly options = computed<SegmentedOption[]>(() =>
    FILTERS.map(f => ({value: f.id, label: `${f.label} ${this.counts()[f.id]}`}))
  );
  protected readonly shown = computed(() =>
    (this.feed.state().data ?? []).filter(g => matches(g, this.filter()))
  );
  protected readonly stateVariant = stateVariant;
  protected readonly stateIsLive = stateIsLive;
  protected readonly exitVariant = exitVariant;

  protected choose(value: string): void {
    const picked = FILTERS.find(f => f.id === value);
    if (picked) {
      this.filter.set(picked.id);
    }
  }

  protected trackById(_index: number, g: GoalRow): string {
    return g.id;
  }

  protected open(g: GoalRow): void {
    void this.router.navigate(['/goals', g.id]);
  }
}
