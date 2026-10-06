import {ChangeDetectionStrategy, Component, computed, inject, input} from '@angular/core';
import {Router} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  CmnCellDirective,
  CmnColumnComponent,
  DataTableComponent,
  PageContainerComponent,
  StatusIndicatorComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {type ProjectGoalRow} from '../api/devclaw.model';
import {DevclawApiService} from '../api/devclaw-api.service';
import {POLL_MS} from '../api/feeds';
import {liveFeed} from '../api/live-feed';
import {SectionComponent} from '../shared/section.component';
import {exitVariant, stateIsLive, stateVariant} from '../shared/status';
import {AgoPipe} from '../shared/time';
import {UsageComponent} from '../shared/usage';

const NOT_FOUND = 404;

/** One repository: where it lives, its health and its goals. */
@Component({
  selector: 'dc-project-page',
  imports: [
    AgoPipe,
    AlertComponent,
    AsyncStateComponent,
    CmnCellDirective,
    CmnColumnComponent,
    DataTableComponent,
    PageContainerComponent,
    SectionComponent,
    StatusIndicatorComponent,
    TagComponent,
    UsageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      @if (error(); as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state [status]="project() ? 'success' : feed.state().error ? 'idle' : 'loading'">
        @if (project(); as p) {
          <div class="flex flex-col gap-cmn-5">
            <header class="flex flex-wrap items-start justify-between gap-cmn-4">
              <div class="flex min-w-0 flex-1 basis-64 flex-col gap-cmn-1">
                <h1 class="break-words font-headline text-cmn-2xl font-semibold text-text-primary">
                  {{ p.name }}
                </h1>
                <p class="break-all font-mono text-cmn-xs text-text-secondary">
                  {{ p.id }} · {{ p.status }}
                  @if (repoLink(); as repo) {
                    ·
                    <a
                      [href]="repo"
                      class="text-accent-default hover:underline"
                      target="_blank"
                      rel="noopener noreferrer"
                      >{{ repo }}</a
                    >
                  }
                </p>
                <p class="break-all font-mono text-cmn-xs text-text-secondary">
                  {{ p.workspaceDir || 'no workspace' }}
                </p>
                <dc-usage [usage]="p.usage" />
              </div>
              <cmn-tag>{{ p.health }}</cmn-tag>
            </header>

            <dc-section [count]="p.goals.length" heading="Goals">
              @if (p.goals.length) {
                <cmn-data-table
                  [rows]="p.goals"
                  [trackBy]="trackById"
                  [rowsActionable]="true"
                  (rowClick)="open($event)"
                >
                  <cmn-column key="objective" header="Goal" listSlot="primary">
                    <ng-template let-g cmnCell>
                      <span [class.opacity-60]="!!g.outcome" class="block min-w-0">
                        <span class="block truncate font-medium">{{ g.objective || g.id }}</span>
                        <span class="block truncate font-mono text-cmn-xs text-text-secondary">
                          {{ g.id }}{{ g.issues.length ? ' · #' + g.issues.join(', #') : '' }}
                        </span>
                      </span>
                    </ng-template>
                  </cmn-column>
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
                  <cmn-column key="lastSession" header="Last session" listSlot="secondary">
                    <ng-template let-g cmnCell>
                      @if (g.lastSession; as s) {
                        <cmn-tag [variant]="exitVariant(s.exit ?? s.status)">
                          {{ s.exit ?? s.status }} · {{ s.completedAt ?? s.createdAt | ago }}
                        </cmn-tag>
                      } @else {
                        —
                      }
                    </ng-template>
                  </cmn-column>
                  <cmn-column
                    key="attentionKind"
                    header="Needs"
                    align="right"
                    listSlot="trailing-secondary"
                  >
                    <ng-template let-g cmnCell>{{ g.attentionKind ? 'you' : '' }}</ng-template>
                  </cmn-column>
                </cmn-data-table>
              } @else {
                <p class="text-cmn-sm text-text-secondary">No goals on this project.</p>
              }
            </dc-section>
          </div>
        }
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class ProjectPageComponent {
  private readonly api = inject(DevclawApiService);
  private readonly router = inject(Router);

  /** The route's `:id`. */
  public readonly id = input.required<string>();

  protected readonly feed = liveFeed(id => this.api.project(id), POLL_MS, this.id);
  protected readonly project = computed(() => this.feed.state().data);
  protected readonly error = computed(() => {
    const {error, status} = this.feed.state();
    return status === NOT_FOUND ? `No such project: ${this.id()}` : error;
  });
  protected readonly repoLink = computed(
    () => this.project()?.repoUrl?.replace(/\.git$/, '') ?? ''
  );
  protected readonly stateVariant = stateVariant;
  protected readonly stateIsLive = stateIsLive;
  protected readonly exitVariant = exitVariant;

  protected trackById(_index: number, g: ProjectGoalRow): string {
    return g.id;
  }

  protected open(g: ProjectGoalRow): void {
    void this.router.navigate(['/goals', g.id]);
  }
}
