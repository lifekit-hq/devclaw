/* eslint-disable @typescript-eslint/naming-convention -- the host reports usage in snake_case */
import {formatUsage} from './usage';

const ONE = {
  input_tokens: 1200,
  output_tokens: 3400,
  cache_read_tokens: 56_000,
  cache_creation_tokens: 0,
};

describe('formatUsage', () => {
  it('says "not reported" for an absent count, never 0', () => {
    expect(formatUsage(null).text).toBe('not reported');
    expect(formatUsage({...ONE, sessions_total: 3, sessions_reported: 0}).text).toBe(
      'not reported'
    );
  });

  it('reads one session in full and compact', () => {
    expect(formatUsage(ONE).text).toBe('in 1.2k · out 3.4k · cache 56kr/0w');
    expect(formatUsage(ONE, true).text).toBe('57k→3.4k');
  });

  it('says how many sessions of a total did not report', () => {
    expect(formatUsage({...ONE, sessions_total: 3, sessions_reported: 2}).text).toBe(
      'in 1.2k · out 3.4k · cache 56kr/0w · 1 not reported'
    );
  });
});
