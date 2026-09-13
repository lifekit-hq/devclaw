// Thin fetch wrappers over the v2 JSON feeds. The bearer token rides as ?token=
// when the console is served behind DEVCLAW_TOKEN.

function tokenQS(): string {
  if (typeof window === "undefined") return "";
  const tok = new URLSearchParams(window.location.search).get("token");
  return tok ? `?token=${encodeURIComponent(tok)}` : "";
}

export function tokenQueryString(): string {
  return tokenQS();
}

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(`${path}${tokenQS()}`);
  if (!r.ok) throw new Error(`${path} ${r.status}`);
  return r.json();
}

async function postJSON<T>(path: string, body: unknown): Promise<T> {
  const r = await fetch(`${path}${tokenQS()}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body ?? {}),
  });
  if (!r.ok) {
    let detail = `${path} ${r.status}`;
    try {
      const j = await r.json();
      if (j && j.error) detail = String(j.error);
    } catch {
      /* ignore */
    }
    throw new Error(detail);
  }
  return r.json();
}

export interface SessionRow {
  id: string;
  kind: string;
  status: string;
  exit: string | null;
  exitDetail: string | null;
  prUrl: string | null;
  createdAt: number;
  completedAt: number | null;
}

export interface GoalRow {
  id: string;
  projectId: string;
  repoUrl: string;
  objective: string;
  issues: number[];
  branch: string;
  createdAt: number;
  closedAt: number | null;
  outcome: string | null;
  lastSeenAt: number | null;
  state: string;
  lastSession: SessionRow | null;
}

export interface Decision {
  id: number;
  goalId: string;
  text: string;
  commentUrl: string;
  madeAt: number;
}

export interface GoalDetail extends GoalRow {
  lastSeen: Record<string, unknown> | null;
  sessions: SessionRow[];
  decisions: Decision[];
}

export interface ProjectRow {
  id: string;
  name: string;
  status: "active" | "paused" | "archived";
  repoUrl: string | null;
  workspaceDir: string | null;
  health: string;
  goals: { id: string; state: string; outcome: string | null }[];
}

export interface ControlState {
  operatorHold: { on: boolean; reason: string };
  schedule: { enabled: boolean; start: string; end: string; tz: string };
  pause: { untilMs: number; reason: string } | null;
  blocked: boolean;
  whyBlocked: string | null;
  maxConcurrent: number | null;
}

export const fetchGoals = () => getJSON<GoalRow[]>("/goals.json");
export const fetchGoal = (id: string) => getJSON<GoalDetail>(`/goals/${encodeURIComponent(id)}.json`);
export const fetchProjects = () => getJSON<ProjectRow[]>("/projects.json");
export const fetchControl = () => getJSON<ControlState>("/control.json");
export const cancelGoal = (id: string) => postJSON<GoalDetail>(`/goals/${encodeURIComponent(id)}/cancel`, {});
export const decideGoal = (id: string, text: string) =>
  postJSON<{ goal: GoalDetail }>(`/goals/${encodeURIComponent(id)}/decide`, { text });
export const pauseDispatch = (reason: string) => postJSON("/control/pause", { reason });
export const resumeDispatch = () => postJSON("/control/resume", {});
export const setSchedule = (s: { enabled: boolean; start?: string; end?: string; tz?: string }) =>
  postJSON("/control/schedule", s);
