import {ChangeDetectionStrategy, Component, inject} from '@angular/core';
import {RouterLink} from '@angular/router';
import {
  AlertComponent,
  AsyncStateComponent,
  CardComponent,
  EmptyStateComponent,
  PageContainerComponent,
  PageHeaderComponent,
  TagComponent,
} from '@lifekit-hq/ui';

import {DevclawApiService} from '../api/devclaw-api.service';
import {liveFeed} from '../api/live-feed';
import {UsageComponent} from '../shared/usage';

/** The repositories devclaw works on, each with its goals. */
@Component({
  selector: 'dc-projects-page',
  imports: [
    AlertComponent,
    AsyncStateComponent,
    CardComponent,
    EmptyStateComponent,
    PageContainerComponent,
    PageHeaderComponent,
    RouterLink,
    TagComponent,
    UsageComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      <cmn-page-header title="Projects" subtitle="The repositories devclaw works on." />
      @if (feed.state().error; as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feed.state().data ? 'success' : feed.state().error ? 'idle' : 'loading'"
      >
        <div class="flex flex-col gap-cmn-3">
          @for (p of feed.state().data ?? []; track p.id) {
            <cmn-card>
              <article [attr.aria-label]="p.name" class="flex flex-col gap-cmn-2">
                <header class="flex flex-wrap items-start justify-between gap-cmn-3">
                  <div class="flex min-w-0 flex-col gap-cmn-1">
                    <p class="font-medium text-text-primary">
                      <a [routerLink]="['/projects', p.id]" class="hover:underline">{{ p.name }}</a>
                      <span class="ml-cmn-2 font-mono text-cmn-xs text-text-secondary">{{
                        p.id
                      }}</span>
                    </p>
                    <p class="break-all font-mono text-cmn-xs text-text-secondary">
                      {{ p.repoUrl || 'no repo_url' }} · {{ p.workspaceDir || 'no workspace' }}
                    </p>
                    <dc-usage [usage]="p.usage" />
                  </div>
                  <cmn-tag>{{ p.health }}</cmn-tag>
                </header>
                @if (p.goals.length) {
                  <p class="flex flex-wrap gap-cmn-3 font-mono text-cmn-xs">
                    @for (g of p.goals; track g.id) {
                      <a
                        [routerLink]="['/goals', g.id]"
                        class="text-accent-default hover:underline"
                      >
                        {{ g.id }} ({{ g.outcome ?? g.state
                        }}{{ g.attentionKind ? ', needs you' : '' }})
                      </a>
                    }
                  </p>
                }
              </article>
            </cmn-card>
          } @empty {
            @if (feed.state().data) {
              <cmn-empty-state
                message="No projects"
                subMessage="register_project from the waiter or the CLI."
              />
            }
          }
        </div>
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class ProjectsPageComponent {
  private readonly api = inject(DevclawApiService);

  protected readonly feed = liveFeed(() => this.api.projects(), 0);
}
