import {goalPrUrl} from './goal-page.component';

const REPO = 'https://github.com/lifekit-hq/lifekit-dashboard.git';

describe('goalPrUrl', () => {
  it('links the newest verdict that carries a prUrl', () => {
    const verdicts = [
      {createdAt: 1, prUrl: 'https://github.com/o/r/pull/1'},
      {createdAt: 3, prUrl: 'https://github.com/o/r/pull/3'},
      {createdAt: 2, prUrl: 'https://github.com/o/r/pull/2'},
      {createdAt: 4, prUrl: ''},
    ];
    expect(goalPrUrl(REPO, 'g1', verdicts)).toBe('https://github.com/o/r/pull/3');
  });

  it('falls back to the pull-request search for the goal branch', () => {
    expect(goalPrUrl(REPO, 'ui-adopt-dashboard-r3', [{createdAt: 1, prUrl: ''}], null)).toBe(
      'https://github.com/lifekit-hq/lifekit-dashboard/pulls?q=is%3Apr%20head%3Agoal%2Fui-adopt-dashboard-r3'
    );
  });
});
