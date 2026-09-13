export const C = {
  green: "var(--green)",
  amber: "var(--amber)",
  red: "var(--red)",
  blue: "var(--accent)",
  muted: "var(--text-muted)",
};

export function stateColor(state: string | null | undefined): string {
  switch (state) {
    case "running":
      return C.blue;
    case "blocked":
      return C.red;
    case "proposed done":
      return C.amber;
    case "achieved":
      return C.green;
    case "cancelled":
      return C.muted;
    case "interrupted":
      return C.amber;
    default:
      return C.muted;
  }
}

export function stateIsLive(state: string | null | undefined): boolean {
  return state === "running";
}

export function exitColor(exit: string | null | undefined): string {
  switch (exit) {
    case "DELIVERED":
    case "DONE":
    case "REVIEW":
      return C.green;
    case "BLOCKED":
    case "REFUSED":
      return C.red;
    case "INTERRUPTED":
      return C.amber;
    default:
      return C.muted;
  }
}
