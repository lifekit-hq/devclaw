import {type VerdictRow} from '../api/devclaw.model';
import {verdictLabel} from './verdict-list.component';

const row = (over: Partial<VerdictRow>): VerdictRow =>
  ({summary: '', question: '', unreadable: false, achieved: false, ...over}) as VerdictRow;

describe('verdictLabel', () => {
  it('names the question that blocked a review with no summary', () => {
    expect(verdictLabel(row({question: 'Which env?'}))).toBe('Asked: Which env?');
  });

  it('keeps the summary, or the plain word, when nothing was asked', () => {
    expect(verdictLabel(row({summary: 'All clauses met', achieved: true}))).toBe('All clauses met');
    expect(verdictLabel(row({}))).toBe('not achieved');
  });
});
