import {TestBed} from '@angular/core/testing';
import {CmnDialogService} from '@lifekit-hq/ui';
import {of} from 'rxjs';

import {type ControlState} from '../api/devclaw.model';
import {DevclawApiService} from '../api/devclaw-api.service';
import {DispatchVerbs} from './dispatch-verbs.service';
import {dispatchLine, zoneOptions} from './settings-page.component';

function setup(closedWith: string | undefined) {
  const holdDispatch = vi.fn(() => of({}));
  TestBed.configureTestingModule({
    providers: [
      {provide: DevclawApiService, useValue: {holdDispatch}},
      {provide: CmnDialogService, useValue: {open: () => ({afterClosed: () => of(closedWith)})}},
    ],
  });
  return {holdDispatch, verbs: TestBed.inject(DispatchVerbs)};
}

describe('the dispatch hold asks first', () => {
  it('posts nothing when the owner keeps dispatching', () => {
    const {holdDispatch, verbs} = setup(undefined);
    let held: boolean | undefined;
    verbs.hold().subscribe(v => (held = v));
    expect(held).toBe(false);
    expect(holdDispatch).not.toHaveBeenCalled();
  });

  it('posts the typed reason once confirmed', () => {
    const {holdDispatch, verbs} = setup('deploying');
    let held: boolean | undefined;
    verbs.hold().subscribe(v => (held = v));
    expect(held).toBe(true);
    expect(holdDispatch).toHaveBeenCalledWith('deploying');
  });
});

describe('settings reads', () => {
  const control = (over: Partial<ControlState>): ControlState =>
    ({
      operatorHold: {on: false},
      pause: null,
      blocked: false,
      whyBlocked: '',
      schedule: {enabled: false, start: '22:00', end: '05:00', tz: 'UTC'},
      ...over,
    }) as unknown as ControlState;

  it('shows one dispatch line with a relative pause time', () => {
    expect(dispatchLine(control({}))).toBe('Running');
    const now = 1_700_000_000_000;
    const paused = control({pause: {untilMs: now + 2 * 3_600_000, reason: 'quota'}} as never);
    expect(dispatchLine(paused, now)).toBe('Paused, resumes in 2h - quota');
  });

  it('offers a zone list that always holds the saved zone', () => {
    const values = zoneOptions('Mars/Olympus', ['Europe/Dublin']).map(o => o.value);
    expect(values).toEqual(['UTC', 'Mars/Olympus', 'Europe/Dublin']);
  });
});
