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

/** jsdom has no `DOMMatrixReadOnly`; the library's list motion measures rows with it. */
vi.stubGlobal(
  'DOMMatrixReadOnly',
  class {
    public readonly m41 = 0;
    public readonly m42 = 0;
  }
);
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

/** Lets the zero-delay poll timer and the render run; the page's timers are faked. */
async function settle(fixture: ComponentFixture<SessionPageComponent>, ms = 0): Promise<void> {
  fixture.detectChanges();
  await vi.advanceTimersByTimeAsync(ms);
  fixture.detectChanges();
}

describe('session page weight', () => {
  let fixture: ComponentFixture<SessionPageComponent>;
  let http: HttpTestingController;

  const el = (): HTMLElement => fixture.nativeElement as HTMLElement;

  afterEach(() => vi.useRealTimers());

  beforeEach(async () => {
    vi.useFakeTimers();
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
    await settle(fixture);
    http.expectOne(`/tasks/${ID}.json`).flush({
      task: {id: ID, status: 'completed', workspaceDir: '/w'},
      verify: {ok: true, log: 'x'.repeat(5000)},
    });
    await settle(fixture);
    const req = http.expectOne(r => r.url === `/tasks/${ID}/events.json`);
    expect(req.request.params.get('limit')).toBe('100');
    req.flush({events: [ASSISTANT], count: 1, nextCursor: 1});
    await settle(fixture);
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
    await settle(fixture);

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

describe('session page liveness', () => {
  let fixture: ComponentFixture<SessionPageComponent>;
  let http: HttpTestingController;
  const POLL = 15_000;

  const el = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const task = (status: string): object => ({task: {id: ID, status, workspaceDir: '/w'}});

  async function open(status: string): Promise<void> {
    vi.useFakeTimers();
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
    await settle(fixture);
    http.expectOne(`/tasks/${ID}.json`).flush(task(status));
    await settle(fixture);
  }

  afterEach(() => vi.useRealTimers());

  it('appends only the new tail when a running session is re-read', async () => {
    await open('running');
    http
      .expectOne(r => r.url === `/tasks/${ID}/events.json`)
      .flush({events: [ASSISTANT], count: 1, nextCursor: null});
    await settle(fixture, POLL);
    http.expectOne(`/tasks/${ID}.json`).flush(task('completed'));
    await settle(fixture);
    const tail = http.expectOne(r => r.url === `/tasks/${ID}/events.json`);
    expect(tail.request.params.get('since')).toBe('1');
    tail.flush({
      events: [event(2, {type: 'result', result: 'second event'})],
      count: 1,
      nextCursor: null,
    });
    await settle(fixture);
    expect(el().textContent).toContain('Reading the issue.');
    expect(el().textContent).toContain('second event');
  });

  it('reads an ended session once', async () => {
    await open('completed');
    http
      .expectOne(r => r.url === `/tasks/${ID}/events.json`)
      .flush({events: [], count: 0, nextCursor: null});
    await settle(fixture, POLL * 3);
    http.expectNone(`/tasks/${ID}.json`);
    http.expectNone(r => r.url === `/tasks/${ID}/events.json`);
  });

  it('shows a failed event read as an error with a retry, never "not recorded"', async () => {
    await open('completed');
    http
      .expectOne(r => r.url === `/tasks/${ID}/events.json`)
      .flush('boom', {status: 500, statusText: 'Server Error'});
    await settle(fixture);
    expect(el().textContent).toContain('Could not read the events');
    const events = el().querySelector('dc-section:last-of-type');
    expect(events?.textContent).not.toContain('not recorded');

    const retry = Array.from(el().querySelectorAll('button')).find(b =>
      b.textContent?.includes('retry')
    );
    retry?.click();
    http
      .expectOne(r => r.url === `/tasks/${ID}/events.json`)
      .flush({events: [ASSISTANT], count: 1, nextCursor: null});
    await settle(fixture);
    expect(el().textContent).not.toContain('Could not read the events');
    expect(el().textContent).toContain('Reading the issue.');
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
