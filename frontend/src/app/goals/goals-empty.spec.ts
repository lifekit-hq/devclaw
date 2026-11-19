import {goalsEmpty} from './goals-page.component';

describe('goalsEmpty', () => {
  it('names the other counts and offers the filter that has goals', () => {
    const e = goalsEmpty('open', {open: 0, blocked: 0, closed: 19, all: 19});
    expect(e.message).toBe('No open goals');
    expect(e.subMessage).toBe('19 closed.');
    expect(e.switchTo).toBe('closed');
  });

  it('offers no switch when no goal exists at all', () => {
    expect(goalsEmpty('open', {open: 0, blocked: 0, closed: 0, all: 0}).switchTo).toBeNull();
  });
});
