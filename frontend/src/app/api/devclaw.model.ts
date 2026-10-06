// The shapes of the JSON feeds the host serves (devclaw/server/routes). The console reads them
// as they are; nothing here is derived or stored.

/** The runner's usage block for one session; null means "not reported", never zeros. */
export interface Usage {
  input_tokens: number;
  output_tokens: number;
  cache_read_tokens: number;
  cache_creation_tokens: number;
  source?: string;
}

/** A live sum over the sessions that reported; the gap between the counts is "not reported". */
export interface UsageTotals extends Usage {
  sessions_total: number;
  sessions_reported: number;
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
  usage: Usage | null;
}

export type WaitingOn = 'hold' | 'pause' | 'window' | 'lane busy' | 'tick';

export interface Answered {
  text: string;
  madeAt: number;
  commentUrl: string;
  waitingOn: WaitingOn;
}

export type AttentionKind =
  'session' | 'env' | 'done-gate refused' | 'red CI' | 'delivery refused' | 'DONE without a PR';

/**
 * What a goal needs from the owner: the session's own words for a session block, the host's
 * fact for a host-authored stop. Derived per request.
 */
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
  usage: UsageTotals;
}

export interface Decision {
  id: number;
  goalId: string;
  text: string;
  commentUrl: string;
  madeAt: number;
}

export interface Clause {
  clause: string;
  satisfied: boolean;
  evidence: string;
}

/** One done-gate review, re-parsed with the gate's own parser: unreadable included. */
export interface VerdictRow {
  taskId: string;
  goalId: string | null;
  createdAt: number;
  completedAt: number | null;
  head: string;
  achieved: boolean;
  unreadable: boolean;
  rawError: string;
  question: string;
  structuralHealth: string;
  concerns: string[];
  summary: string;
  clauses: Clause[];
  satisfied: number;
  total: number;
  prUrl: string;
}

export interface VerdictFeed {
  verdicts: VerdictRow[];
  count: number;
  truncated: boolean;
}

export interface GoalDetail extends GoalRow {
  lastSeen: Record<string, unknown> | null;
  sessions: SessionRow[];
  decisions: Decision[];
  verdicts: VerdictRow[];
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
  status: 'active' | 'paused' | 'archived';
  repoUrl: string | null;
  workspaceDir: string | null;
  health: string;
  goals: ProjectGoalRow[];
  usage: UsageTotals;
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

/** One session, everything the host recorded: an absent part is null, shown as "not recorded". */
export interface TaskDetail {
  task: TaskRow;
  verify: Record<string, unknown> | null;
  delivery: Record<string, unknown> | null;
  change: Record<string, unknown> | null;
  agentOutput: string | null;
  block: BlockFields | null;
  usage: Usage | null;
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

export interface RunSchedule {
  enabled: boolean;
  start: string;
  end: string;
  tz: string;
}

export interface ControlState {
  operatorHold: {on: boolean; reason: string};
  schedule: RunSchedule;
  pause: {untilMs: number; reason: string} | null;
  blocked: boolean;
  whyBlocked: string | null;
  maxConcurrent: number | null;
}
