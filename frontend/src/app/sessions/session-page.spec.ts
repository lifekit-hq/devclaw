import {EVENT_PAGE, factList, prettyPayload} from './session-page.component';

describe('session page weight', () => {
  it('reads 100 events at a time', () => {
    expect(EVENT_PAGE).toBe(100);
  });

  it('shows a recorded object as short facts, never its whole JSON', () => {
    const facts = factList({ok: true, log: 'x'.repeat(5000), nested: {a: 1}});
    expect(facts.map(f => f.key)).toEqual(['ok', 'log', 'nested']);
    expect(facts[0].value).toBe('true');
    expect(facts[1].value.length).toBeLessThan(200);
    expect(facts[2].value).toBe('{"a":1}');
    expect(factList(null)).toEqual([]);
  });

  it('indents a JSON payload for the disclosure and leaves other text alone', () => {
    expect(prettyPayload('{"a":1}')).toBe('{\n  "a": 1\n}');
    expect(prettyPayload('not json')).toBe('not json');
  });
});
