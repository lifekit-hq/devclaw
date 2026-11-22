import {type VerdictRow} from '../api/devclaw.model';
import {verdictLabel} from './verdict-list.component';

const row = (over: Partial<VerdictRow>): VerdictRow => ({
  taskId: 't',
  goalId: null,
  createdAt: 0,
  completedAt: null,
  head: '',
  achieved: false,
  unreadable: false,
  rawError: '',
  question: '',
  structuralHealth: '',
  concerns: [],
  summary: '',
  clauses: [],
  satisfied: 0,
  total: 0,
  prUrl: '',
  ...over,
});

describe('verdictLabel', () => {
  it('names the question that blocked a review with no summary', () => {
    expect(verdictLabel(row({question: 'Which env?'}))).toBe('Asked: Which env?');
  });

  it('keeps the summary, or the plain word, when nothing was asked', () => {
    expect(verdictLabel(row({summary: 'All clauses met', achieved: true}))).toBe('All clauses met');
    expect(verdictLabel(row({}))).toBe('not achieved');
  });
});
