import {ago} from '../shared/time';

/** What the Verdicts section says when a goal has no done-gate review. */
export function noVerdictMessage(outcome: string | null): string {
  if (!outcome) {
    return 'No done-gate review yet.';
  }
  return outcome === 'cancelled'
    ? 'Cancelled — no done-gate review.'
    : 'Closed without a done-gate review.';
}

/** The Seen column: a closed goal reads "Closed 1w ago", an open one when it was last seen. */
export function seenLabel(g: {closedAt: number | null; lastSeenAt: number | null}): string {
  return g.closedAt ? `Closed ${ago(g.closedAt)}` : ago(g.lastSeenAt);
}
