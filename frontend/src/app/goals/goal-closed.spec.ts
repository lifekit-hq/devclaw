import {noVerdictMessage, seenLabel} from './goal-closed';

describe('a closed goal does not read as pending', () => {
  it('says why a closed goal has no review, and keeps the open wording', () => {
    expect(noVerdictMessage('achieved')).toBe('Closed without a done-gate review.');
    expect(noVerdictMessage('cancelled')).toBe('Cancelled — no done-gate review.');
    expect(noVerdictMessage(null)).toBe('No done-gate review yet.');
  });

  it('shows Closed <ago> for a closed row and last-seen for an open one', () => {
    const now = Date.now();
    expect(seenLabel({closedAt: now - 7 * 86_400_000, lastSeenAt: now})).toMatch(/^Closed .+ ago$/);
    expect(seenLabel({closedAt: null, lastSeenAt: now})).not.toContain('Closed');
  });
});
