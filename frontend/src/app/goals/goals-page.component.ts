import {ChangeDetectionStrategy, Component, computed, inject, signal} from '@angular/core';
import {Router, RouterLink} from '@angular/router';
import {
  AlertComponent,
  ChipComponent,
  CmnCellDirective,
  CmnColumnComponent,
  DataTableComponent,
  EmptyStateComponent,
  PageContainerComponent,
  PageHeaderComponent,
  StatusIndicatorComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {type GoalRow} from '../api/devclaw.model';
import {GoalsFeed} from '../api/feeds';
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
    ChipComponent,
    CmnCellDirective,
    CmnColumnComponent,
    DataTableComponent,
    EmptyStateComponent,
    PageContainerComponent,
    PageHeaderComponent,
    RouterLink,
    StatusIndicatorComponent,
    TagComponent,
    UsageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      <cmn-page-header
        title="Goals"
        subtitle="One issue, one branch, one PR. The thread on GitHub is the record; this is the index."
      />
      <div class="flex flex-wrap gap-cmn-2" role="group" aria-label="Filter goals">
        @for (f of filters; track f.id) {
          <cmn-chip [selected]="filter() === f.id" (clicked)="filter.set(f.id)">
            {{ f.label }} <span class="font-mono opacity-70">{{ counts()[f.id] }}</span>
          </cmn-chip>
        }
      </div>
      @if (feed.state().error; as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      @if (feed.state().data && !shown().length) {
        <cmn-empty-state message="Nothing here" subMessage="No goals match this filter." />
      } @else {
        <cmn-data-table
          [rows]="shown()"
          [loading]="!feed.state().data && !feed.state().error"
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
            <ng-template let-g cmnCell><dc-usage [usage]="g.usage" [compact]="true" /></ng-template>
          </cmn-column>
          <cmn-column key="lastSeenAt" header="Seen" align="right" listSlot="trailing-secondary">
            <ng-template let-g cmnCell>{{ g.lastSeenAt | ago }}</ng-template>
          </cmn-column>
        </cmn-data-table>
      }
    </cmn-page-container>
  `,
})
export class GoalsPageComponent {
  private readonly router = inject(Router);

  protected readonly feed = inject(GoalsFeed);
  protected readonly filters = FILTERS;
  protected readonly filter = signal<Filter>('open');
  protected readonly counts = computed(() => {
    const goals = this.feed.state().data ?? [];
    return Object.fromEntries(
      FILTERS.map(f => [f.id, goals.filter(g => matches(g, f.id)).length])
    ) as Record<Filter, number>;
  });
  protected readonly shown = computed(() =>
    (this.feed.state().data ?? []).filter(g => matches(g, this.filter()))
  );
  protected readonly stateVariant = stateVariant;
  protected readonly stateIsLive = stateIsLive;
  protected readonly exitVariant = exitVariant;

  protected trackById(_index: number, g: GoalRow): string {
    return g.id;
  }

  protected open(g: GoalRow): void {
    void this.router.navigate(['/goals', g.id]);
  }
}
