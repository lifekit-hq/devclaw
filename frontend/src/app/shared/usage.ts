import {ChangeDetectionStrategy, Component, computed, input} from '@angular/core';

import {type Usage, type UsageTotals} from '../api/devclaw.model';

const THOUSAND = 1_000;
const MILLION = 1_000_000;
const ONE_DECIMAL_BELOW = 10;

function short(n: number): string {
  if (n >= MILLION) {
    return `${(n / MILLION).toFixed(n >= ONE_DECIMAL_BELOW * MILLION ? 0 : 1)}M`;
  }
  if (n >= THOUSAND) {
    return `${(n / THOUSAND).toFixed(n >= ONE_DECIMAL_BELOW * THOUSAND ? 0 : 1)}k`;
  }
  return String(n);
}

export interface UsageText {
  text: string;
  /** The exact counts, for the hover title. */
  title: string;
  reported: boolean;
}

/**
 * Tokens for one session or a live total. Absent is "not reported", never 0; a total says how
 * many of its sessions did not report. Compact is `in+cache→out` for table cells.
 */
export function formatUsage(usage: Usage | UsageTotals | null, compact = false): UsageText {
  if (!usage) {
    return {text: 'not reported', title: '', reported: false};
  }
  const totals = 'sessions_total' in usage ? usage : null;
  const title =
    `in ${usage.input_tokens} · out ${usage.output_tokens} · cache read ${usage.cache_read_tokens}` +
    ` · cache write ${usage.cache_creation_tokens}` +
    (totals ? ` · ${totals.sessions_reported} of ${totals.sessions_total} sessions reported` : '');
  if (totals && totals.sessions_reported === 0) {
    return {text: 'not reported', title, reported: false};
  }
  const text = compact
    ? `${short(usage.input_tokens + usage.cache_read_tokens + usage.cache_creation_tokens)}→${short(usage.output_tokens)}`
    : `in ${short(usage.input_tokens)} · out ${short(usage.output_tokens)} · cache ${short(usage.cache_read_tokens)}r/${short(usage.cache_creation_tokens)}w`;
  const unreported = totals ? totals.sessions_total - totals.sessions_reported : 0;
  return {
    text: unreported > 0 ? `${text} · ${unreported} not reported` : text,
    title,
    reported: true,
  };
}

/** Token usage as text, the exact counts on hover. */
@Component({
  selector: 'dc-usage',
  changeDetection: ChangeDetectionStrategy.OnPush,
  host: {class: 'font-mono text-cmn-xs text-text-secondary'},
  template: '<span [attr.title]="view().title || null">{{ view().text }}</span>',
})
export class UsageComponent {
  public readonly usage = input.required<Usage | UsageTotals | null>();
  public readonly compact = input(false);

  protected readonly view = computed(() => formatUsage(this.usage(), this.compact()));
}
