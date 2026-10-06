import {type StatusIndicatorVariant, type TagVariant} from '@lifekit-hq/ui';

/** A goal's state word (or its outcome once closed) on the library's status scale. */
export function stateVariant(state: string | null | undefined): StatusIndicatorVariant {
  switch (state) {
    case 'blocked':
      return 'error';
    case 'proposed done':
    case 'interrupted':
      return 'warning';
    case 'achieved':
      return 'success';
    default:
      return 'neutral';
  }
}

/** A running goal pulses: the one state that changes on its own. */
export function stateIsLive(state: string | null | undefined): boolean {
  return state === 'running';
}

/** A session's exit line (or its status before it exits) as a tag. */
export function exitVariant(exit: string | null | undefined): TagVariant {
  switch (exit) {
    case 'DELIVERED':
    case 'DONE':
    case 'REVIEW':
      return 'success';
    case 'BLOCKED':
    case 'REFUSED':
      return 'error';
    case 'INTERRUPTED':
      return 'warning';
    default:
      return 'neutral';
  }
}
