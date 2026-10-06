import {ChangeDetectionStrategy, Component, computed, inject} from '@angular/core';
import {StatusIndicatorComponent, type StatusIndicatorVariant} from '@lifekit-hq/ui';

import {type ControlState} from '../api/devclaw.model';
import {ControlFeed} from '../api/feeds';

interface Dispatch {
  label: string;
  variant: StatusIndicatorVariant;
  live: boolean;
}

/** Whether new sessions start right now: the hold, then the quota pause, then the run window. */
export function dispatchState(control: ControlState | null): Dispatch | null {
  if (!control) {
    return null;
  }
  if (control.operatorHold.on) {
    return {label: 'Held', variant: 'warning', live: false};
  }
  if (control.pause) {
    return {label: 'Paused', variant: 'warning', live: false};
  }
  if (control.blocked && control.schedule.enabled) {
    return {label: 'Off-hours', variant: 'warning', live: false};
  }
  return {label: 'Running', variant: 'success', live: true};
}

@Component({
  selector: 'dc-dispatch-status',
  imports: [StatusIndicatorComponent],
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    @if (dispatch(); as d) {
      <cmn-status-indicator [variant]="d.variant" [live]="d.live">
        Dispatch · {{ d.label }}
      </cmn-status-indicator>
    }
  `,
})
export class DispatchStatusComponent {
  private readonly control = inject(ControlFeed);

  protected readonly dispatch = computed(() => dispatchState(this.control.state().data));
}
