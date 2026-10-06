import {ChangeDetectionStrategy, Component, inject} from '@angular/core';
import {
  AlertComponent,
  AsyncStateComponent,
  CardComponent,
  EmptyStateComponent,
  PageContainerComponent,
  PageHeaderComponent,
} from '@lifekit-hq/ui';

import {DevclawApiService} from '../api/devclaw-api.service';
import {liveFeed} from '../api/live-feed';
import {VerdictListComponent} from '../components/verdict-list.component';

const LIMIT = 200;
const POLL_MS = 30_000;

/**
 * Every done-gate review across goals, newest first: the live read of the "ran and produced
 * garbage" axis — is the gate refusing on the same kind of clause again and again?
 */
@Component({
  selector: 'dc-verdicts-page',
  imports: [
    AlertComponent,
    AsyncStateComponent,
    CardComponent,
    EmptyStateComponent,
    PageContainerComponent,
    PageHeaderComponent,
    VerdictListComponent,
  ],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <cmn-page-container>
      <cmn-page-header
        title="Verdicts"
        subtitle="What the done-gate found, clause by clause, read from each review session's own output. Open a row to expand."
      />
      @if (feed.state().error; as message) {
        <cmn-alert variant="error">{{ message }}</cmn-alert>
      }
      <cmn-async-state
        [status]="feed.state().data ? 'success' : feed.state().error ? 'idle' : 'loading'"
      >
        @if (feed.state().data; as f) {
          @if (f.count) {
            <cmn-card>
              <dc-verdict-list [rows]="f.verdicts" [showGoal]="true" />
              @if (f.truncated) {
                <p class="mt-cmn-3 text-cmn-xs text-text-secondary">
                  Showing the newest {{ f.count }}; older reviews are not listed.
                </p>
              }
            </cmn-card>
          } @else {
            <cmn-empty-state
              message="No reviews yet"
              subMessage="A verdict appears when a session proposes DONE and CI is green."
              icon="Scale"
            />
          }
        }
      </cmn-async-state>
    </cmn-page-container>
  `,
})
export class VerdictsPageComponent {
  private readonly api = inject(DevclawApiService);

  protected readonly feed = liveFeed(() => this.api.verdicts(LIMIT), POLL_MS);
}
