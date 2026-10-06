import {computed, inject, Injectable} from '@angular/core';

import {DevclawApiService} from './devclaw-api.service';
import {liveFeed} from './live-feed';

/** The host's own tick is ~15 minutes; 15 seconds shows a decision landing without a reload. */
export const POLL_MS = 15_000;

/**
 * The goals feed, read once for the whole console: the Needs-you badge, the Needs-you page and
 * the goals list all show the same read.
 */
@Injectable({providedIn: 'root'})
export class GoalsFeed {
  private readonly api = inject(DevclawApiService);
  private readonly feed = liveFeed(() => this.api.goals(), POLL_MS);

  public readonly state = this.feed.state;
  /** Goals with an ask nobody has answered yet. */
  public readonly waiting = computed(
    () => (this.state().data ?? []).filter(g => g.attention && !g.attention.answered).length
  );

  public reload(): void {
    this.feed.reload();
  }
}

/** Dispatch control: the hold, the quota pause and the run window. */
@Injectable({providedIn: 'root'})
export class ControlFeed {
  private readonly api = inject(DevclawApiService);
  private readonly feed = liveFeed(() => this.api.control(), POLL_MS);

  public readonly state = this.feed.state;

  public reload(): void {
    this.feed.reload();
  }
}
