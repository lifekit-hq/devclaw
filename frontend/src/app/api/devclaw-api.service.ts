import {HttpClient, type HttpErrorResponse} from '@angular/common/http';
import {inject, Injectable} from '@angular/core';
import {type Observable} from 'rxjs';

import {
  type ControlState,
  type GoalDetail,
  type GoalRow,
  type ProjectRow,
  type RunSchedule,
  type TaskDetail,
  type TaskEvents,
  type VerdictFeed,
} from './devclaw.model';

const id = (value: string): string => encodeURIComponent(value);

/** The verb error the host answers with (`{"error": "..."}`), else the status line. */
export function errorText(error: unknown): string {
  const http = error as Partial<HttpErrorResponse>;
  const body = http.error as {error?: unknown} | null | undefined;
  if (body && typeof body === 'object' && body.error) {
    return typeof body.error === 'string' ? body.error : JSON.stringify(body.error);
  }
  if (http.url !== undefined && http.status !== undefined) {
    return `${new URL(http.url ?? '', 'http://x').pathname} ${http.status}`;
  }
  return String(error);
}

/** The console's reads and the owner's verbs, over the host's JSON feeds. */
@Injectable({providedIn: 'root'})
export class DevclawApiService {
  private readonly http = inject(HttpClient);

  public goals(): Observable<GoalRow[]> {
    return this.http.get<GoalRow[]>('/goals.json');
  }

  public goal(goalId: string): Observable<GoalDetail> {
    return this.http.get<GoalDetail>(`/goals/${id(goalId)}.json`);
  }

  public projects(): Observable<ProjectRow[]> {
    return this.http.get<ProjectRow[]>('/projects.json');
  }

  public project(projectId: string): Observable<ProjectRow> {
    return this.http.get<ProjectRow>(`/projects/${id(projectId)}.json`);
  }

  public task(taskId: string): Observable<TaskDetail> {
    return this.http.get<TaskDetail>(`/tasks/${id(taskId)}.json`);
  }

  public taskEvents(taskId: string, since?: number | null): Observable<TaskEvents> {
    const params: Record<string, number> = since ? {since} : {};
    return this.http.get<TaskEvents>(`/tasks/${id(taskId)}/events.json`, {params});
  }

  public verdicts(limit: number): Observable<VerdictFeed> {
    return this.http.get<VerdictFeed>('/verdicts.json', {params: {limit}});
  }

  public control(): Observable<ControlState> {
    return this.http.get<ControlState>('/control.json');
  }

  public cancelGoal(goalId: string): Observable<unknown> {
    return this.http.post(`/goals/${id(goalId)}/cancel`, {});
  }

  /** The owner's one verb: posted on the issue as an instruction; the next tick reads it. */
  public decide(goalId: string, text: string): Observable<unknown> {
    return this.http.post(`/goals/${id(goalId)}/decide`, {text});
  }

  public holdDispatch(reason: string): Observable<unknown> {
    return this.http.post('/control/pause', {reason});
  }

  public releaseDispatch(): Observable<unknown> {
    return this.http.post('/control/resume', {});
  }

  public setSchedule(schedule: RunSchedule): Observable<unknown> {
    return this.http.post('/control/schedule', schedule);
  }
}
