import {provideHttpClient} from '@angular/common/http';
import {HttpTestingController, provideHttpClientTesting} from '@angular/common/http/testing';
import {provideZonelessChangeDetection} from '@angular/core';
import {type ComponentFixture, TestBed} from '@angular/core/testing';

import {type Attention} from '../api/devclaw.model';
import {AttentionCardComponent} from './attention-card.component';

const ASK: Attention = {
  kind: 'session',
  question: 'Which store?',
  options: ['Postgres\nthe team runs it', 'SQLite'],
  recommended: 1,
  default: '',
  since: Date.now(),
  link: 'https://example.com/issues/7',
  answered: null,
};

describe('AttentionCardComponent', () => {
  let fixture: ComponentFixture<AttentionCardComponent>;
  let http: HttpTestingController;
  let decided: number;

  const el = (): HTMLElement => fixture.nativeElement as HTMLElement;
  const buttons = (): HTMLButtonElement[] => Array.from(el().querySelectorAll('button'));

  async function render(attention: Attention): Promise<void> {
    fixture.componentRef.setInput('attention', attention);
    await fixture.whenStable();
  }

  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideHttpClient(),
        provideHttpClientTesting(),
      ],
    });
    http = TestBed.inject(HttpTestingController);
    fixture = TestBed.createComponent(AttentionCardComponent);
    fixture.componentRef.setInput('goalId', 'g 1');
    decided = 0;
    fixture.componentInstance.decided.subscribe(() => decided++);
  });

  it("offers the session's options, the recommended one first", async () => {
    await render(ASK);

    const labels = buttons().map(b => b.textContent?.trim() ?? '');
    expect(labels[0]).toContain('SQLite');
    expect(labels[0]).toContain('recommended');
    expect(labels[1]).toBe('Postgres');
  });

  it('posts a chosen option, in full, on the goal as the decision', async () => {
    await render({...ASK, recommended: -1});
    buttons()[0].click();

    const req = http.expectOne('/goals/g%201/decide');
    expect(req.request.method).toBe('POST');
    expect(req.request.body).toEqual({text: 'Postgres\nthe team runs it'});
    req.flush({ok: true});
    expect(decided).toBe(1);
  });

  it("shows the host's error and keeps the ask open when the decide is refused", async () => {
    await render(ASK);
    buttons()[0].click();

    http
      .expectOne('/goals/g%201/decide')
      .flush({error: 'goal is closed'}, {status: 409, statusText: 'Conflict'});
    await fixture.whenStable();

    expect(el().querySelector('[role="alert"]')?.textContent).toContain('goal is closed');
    expect(decided).toBe(0);
  });

  it('offers no options on an environment stop: it wakes on its own', async () => {
    await render({...ASK, kind: 'env', options: []});

    expect(buttons()).toEqual([]);
    expect(el().textContent).toContain('Wakes on its own');
  });
});
