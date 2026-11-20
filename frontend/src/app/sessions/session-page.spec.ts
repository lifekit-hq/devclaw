import {provideHttpClient} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {provideZonelessChangeDetection} from '@angular/core';
import {type ComponentFixture, TestBed} from '@angular/core/testing';

import {type TaskEvent} from '../api/devclaw.model';
import {
  factList,
  payloadSummary,
  prettyPayload,
  SessionPageComponent,
} from './session-page.component';

const ID = 'ea178b7d';
const SECRET = 'SECRET-PAYLOAD-MARKER';

function event(id: number, payload: object): TaskEvent {
  return {
    id,
    taskId: ID,
    type: 'assistant',
    source: 'claude',
    ts: 1_700_000_000_000,
    payloadJson: JSON.stringify(payload),
  };
}

const ASSISTANT = event(1, {
  type: 'assistant',
  message: {model: 'claude-x', content: [{type: 'text', text: `Reading the issue. ${SECRET}`}]},
});

describe('session page weight', () => {
  let fixture: ComponentFixture<SessionPageComponent>;
  let http: HttpTestingController;

  const el = (): HTMLElement => fixture.nativeElement as HTMLElement;

  beforeEach(async () => {
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(SessionPageComponent);
    fixture.componentRef.setInput('id', ID);
    await fixture.whenStable();
    http.expectOne(`/tasks/${ID}.json`).flush({
      task: {id: ID, status: 'completed', workspaceDir: '/w'},
      verify: {ok: true, log: 'x'.repeat(5000)},
    });
    const req = http.expectOne(r => r.url === `/tasks/${ID}/events.json`);
    expect(req.request.params.get('limit')).toBe('100');
    req.flush({events: [ASSISTANT], count: 1, nextCursor: 1});
    await fixture.whenStable();
  });

  it('asks for the next 100 events when loading more', () => {
    const more = Array.from(el().querySelectorAll('cmn-button')).find(b =>
      b.textContent?.includes('load more')
    ) as HTMLElement;
    more.querySelector('button')?.click();
    const req = http.expectOne(r => r.url === `/tasks/${ID}/events.json`);
    expect(req.request.params.get('limit')).toBe('100');
    expect(req.request.params.get('since')).toBe('1');
    req.flush({events: [], count: 0, nextCursor: null});
  });

  it('holds no payload in the page until its row is opened', async () => {
    expect(el().textContent).not.toContain('"model"');
    expect(el().querySelector('[title*="model"]')).toBeNull();
    expect(el().querySelector('pre[aria-label="Event payload"]')).toBeNull();

    const toggle = Array.from(el().querySelectorAll('button')).find(b =>
      b.textContent?.includes('show payload')
    );
    toggle?.click();
    await fixture.whenStable();

    const payload = el().querySelector('pre[aria-label="Event payload"]');
    expect(payload?.textContent).toContain('"model": "claude-x"');
  });

  it('shows one readable line per event, not key=JSON', () => {
    const text = el().textContent ?? '';
    expect(text).toContain(`Reading the issue. ${SECRET}`);
    expect(text).not.toContain('message={');
    expect(text).not.toContain('type=assistant');
  });

  it('shows Verify as short key-value facts', () => {
    const facts = el().querySelector('dl[aria-label="Verify"]');
    expect(facts?.textContent).toContain('ok');
    expect(facts?.textContent).toContain('true');
    expect((facts?.textContent ?? '').length).toBeLessThan(400);
  });
});

describe('event summary', () => {
  it('names the tool, the text or the result — never raw JSON', () => {
    const tool = event(2, {
      type: 'assistant',
      message: {content: [{type: 'tool_use', name: 'Bash', input: {command: 'ls'}}]},
    });
    expect(payloadSummary(tool)).toBe('tool Bash');
    expect(
      payloadSummary(event(3, {type: 'user', message: {content: [{type: 'tool_result'}]}}))
    ).toBe('tool result');
    expect(payloadSummary(event(4, {type: 'result', result: 'all\ndone'}))).toBe('all done');
    expect(payloadSummary(event(5, {goal: {deep: 1}}))).toBe('');
    expect(payloadSummary({...ASSISTANT, payloadJson: 'plain text'})).toBe('plain text');
  });
});

describe('record helpers', () => {
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
