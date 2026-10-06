import {Pipe, type PipeTransform} from '@angular/core';
import {formatRelativeTime} from '@lifekit-hq/core/format';

const ISO_SECONDS = 19;

/** `5m ago`; a dash when the host has no time for it. */
export function ago(ms: number | null | undefined): string {
  return ms ? formatRelativeTime(ms) : '—';
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

/** `2026-10-06 14:03:09Z (5m ago)`, for the session record. */
@Pipe({name: 'stamp'})
export class StampPipe implements PipeTransform {
  public transform(ms: number | null | undefined): string {
    if (!ms) {
      return '—';
    }
    return `${utcSecond(ms)} (${formatRelativeTime(ms)})`;
  }
}
