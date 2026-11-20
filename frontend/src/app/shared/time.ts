import {Pipe, type PipeTransform} from '@angular/core';
import {formatRelativeTime} from '@lifekit-hq/core/format';

const ISO_SECONDS = 19;
const MS_PER_MINUTE = 60_000;
const MINUTES_PER_HOUR = 60;
const HOURS_PER_DAY = 24;

/** `5m ago`; a dash when the host has no time for it. */
export function ago(ms: number | null | undefined): string {
  return ms ? formatRelativeTime(ms) : '—';
}

/** `in 2h`; the future twin of `ago` (the kit's relative format reads the past only). */
export function inFuture(ms: number, now = Date.now()): string {
  const minutes = Math.max(1, Math.round((ms - now) / MS_PER_MINUTE));
  if (minutes < MINUTES_PER_HOUR) {
    return `in ${minutes}m`;
  }
  const hours = Math.round(minutes / MINUTES_PER_HOUR);
  return hours < HOURS_PER_DAY ? `in ${hours}h` : `in ${Math.round(hours / HOURS_PER_DAY)}d`;
}

@Pipe({name: 'ago'})
export class AgoPipe implements PipeTransform {
  public transform(ms: number | null | undefined): string {
    return ago(ms);
  }
}

/** `2026-10-06 14:03:09Z`, a UTC second. */
export function utcSecond(ms: number): string {
  return `${new Date(ms).toISOString().replace('T', ' ').slice(0, ISO_SECONDS)}Z`;
}
