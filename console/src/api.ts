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

function withToken(path: string): string {
  const qs = tokenQS();
  if (!qs) return path;
  return path.includes("?") ? `${path}&${qs.slice(1)}` : `${path}${qs}`;
}

async function getJSON<T>(path: string): Promise<T> {
  const r = await fetch(withToken(path));
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

export interface Answered {
  text: string;
  madeAt: number;
  commentUrl: string;
  waitingOn: "hold" | "pause" | "window" | "lane busy" | "tick";
}

export type AttentionKind =
  | "session"
  | "env"
  | "done-gate refused"
  | "red CI"
  | "delivery refused"
  | "DONE without a PR";

// What a goal needs from the owner — the session's own words for a session
// block, the host's fact for a host-authored stop. Derived per request.
export interface Attention {
  kind: AttentionKind;
  question: string;
  options: string[];
  recommended: number;
  default: string;
  since: number;
  link: string;
  answered: Answered | null;
}

export interface BlockFields {
  question: string;
  options: string[];
  default: string;
  recommended: number;
  kind: string;
  item: string;
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
  attention: Attention | null;
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

export interface ProjectGoalRow {
  id: string;
  state: string;
  outcome: string | null;
  objective: string;
  issues: number[];
  lastSession: SessionRow | null;
  attentionKind: AttentionKind | null;
}

export interface ProjectRow {
  id: string;
  name: string;
  status: "active" | "paused" | "archived";
  repoUrl: string | null;
  workspaceDir: string | null;
  health: string;
  goals: ProjectGoalRow[];
}

export interface TaskRow extends SessionRow {
  workspaceDir: string;
  parentGoalId: string | null;
  startedAt: number | null;
  preRunSha: string | null;
  targetBranch: string | null;
  projectId: string | null;
  verifyCmd: string | null;
  deliver: boolean;
  error: string | null;
  goal: string;
}

// One session, everything the host recorded: absent parts are null and the
// page says "not recorded" — never an empty value that looks like a result.
export interface TaskDetail {
  task: TaskRow;
  verify: Record<string, unknown> | null;
  delivery: Record<string, unknown> | null;
  change: Record<string, unknown> | null;
  agentOutput: string | null;
  block: BlockFields | null;
}

export interface TaskEvent {
  id: number;
  taskId: string;
  type: string;
  source: string;
  payloadJson: string;
  ts: number;
}

export interface TaskEvents {
  events: TaskEvent[];
  count: number;
  nextCursor: number | null;
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
export const fetchProject = (id: string) => getJSON<ProjectRow>(`/projects/${encodeURIComponent(id)}.json`);
export const fetchTask = (id: string) => getJSON<TaskDetail>(`/tasks/${encodeURIComponent(id)}.json`);
export const fetchTaskEvents = (id: string, since?: number | null) =>
  getJSON<TaskEvents>(`/tasks/${encodeURIComponent(id)}/events.json${since ? `?since=${since}` : ""}`);
export const fetchControl = () => getJSON<ControlState>("/control.json");
export const cancelGoal = (id: string) => postJSON<GoalDetail>(`/goals/${encodeURIComponent(id)}/cancel`, {});
export const decideGoal = (id: string, text: string) =>
  postJSON<{ goal: GoalDetail }>(`/goals/${encodeURIComponent(id)}/decide`, { text });
export const pauseDispatch = (reason: string) => postJSON("/control/pause", { reason });
export const resumeDispatch = () => postJSON("/control/resume", {});
export const setSchedule = (s: { enabled: boolean; start?: string; end?: string; tz?: string }) =>
  postJSON("/control/schedule", s);
