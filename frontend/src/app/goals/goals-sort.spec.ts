import {type GoalRow} from '../api/devclaw.model';
import {sortGoals} from './goals-page.component';

/* eslint-disable @typescript-eslint/naming-convention -- the wire shape of UsageTotals */
const goal = (id: string, o: Partial<GoalRow>): GoalRow => ({
  id,
  projectId: 'p',
  repoUrl: '',
  objective: '',
  issues: [],
  branch: '',
  createdAt: 1,
  closedAt: null,
  outcome: null,
  lastSeenAt: null,
  state: 'idle',
  lastSession: null,
  attention: null,
  usage: {
    input_tokens: 0,
    output_tokens: 0,
    cache_read_tokens: 0,
    cache_creation_tokens: 0,
    sessions_total: 0,
    sessions_reported: 0,
  },
  ...o,
});

/* eslint-enable @typescript-eslint/naming-convention */

describe('sortGoals', () => {
  it('puts open goals by last activity and closed goals by closedAt, newest first', () => {
    const rows = [
      goal('old-open', {lastSeenAt: 10}),
      goal('new-open', {lastSeenAt: 90}),
      goal('old-closed', {closedAt: 20, outcome: 'merged', lastSeenAt: 100}),
      goal('new-closed', {closedAt: 80, outcome: 'merged'}),
    ];
    expect(sortGoals(rows).map(g => g.id)).toEqual([
      'new-open',
      'new-closed',
      'old-closed',
      'old-open',
    ]);
  });

  it('falls back to createdAt for a goal never seen, without mutating the input', () => {
    const rows = [goal('a', {createdAt: 5}), goal('b', {lastSeenAt: 50})];
    expect(sortGoals(rows).map(g => g.id)).toEqual(['b', 'a']);
    expect(rows[0].id).toBe('a');
  });
});
