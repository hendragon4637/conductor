const BASE = '/api';

async function http<T>(path: string, opts: RequestInit = {}): Promise<T> {
  const res = await fetch(BASE + path, {
    ...opts,
    headers: { 'content-type': 'application/json', ...(opts.headers || {}) },
  });
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return res.json() as Promise<T>;
}

export interface Project {
  project_id: string;
  name: string;
  description: string | null;
  system_prompt: string | null;
  repo_path: string;
  created_at: string;
}

export interface Session {
  session_id: string;
  project_id: string;
  user_intent: string | null;
  status: string;
  base_branch: string;
  created_at: string;
}

export interface Task {
  task_id: string;
  project_id: string;
  session_id: string;
  user_intent: string;
  status: string;
  completion_signal: string | null;
  created_at: string;
}

export interface TraceSummary {
  trace_id: string;
  task_id: string;
  session_id: string;
  project_id: string;
  agent_config_id: string;
  role: string;
  status: string;
  manual_label: string | null;
  failure_mode: string | null;
  cli_session_id: string | null;
  total_tokens: number | null;
  total_observations: number | null;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
}

export interface TraceDetail extends TraceSummary {
  input_spec: unknown;
  output_spec: unknown;
  skill_snapshot_hash: string | null;
  skill_path: string | null;
  preceding_trace_id: string | null;
  terminates_task: boolean;
  observations: Observation[];
  hitl_events: HitlEvent[];
  scores: Score[];
}

export interface Observation {
  observation_id: string;
  step_index: number | null;
  type: string;
  tool_name: string | null;
  status: string | null;
  tokens_input: number | null;
  tokens_output: number | null;
  latency_ms: number | null;
  started_at: string;
  ended_at: string | null;
}

export interface HitlEvent {
  hitl_id: string;
  prompt: string;
  decision: string;
  asked_at: string;
  answered_at: string | null;
}

export interface Score {
  score_id: string;
  track: string;
  dimension: string;
  value: number;
}

export interface AgentConfig {
  agent_config_id: string;
  cli: string;
  domain: string;
  role: string;
  pattern: string;
  active: boolean;
}

export const api = {
  listProjects: () => http<Project[]>('/projects'),
  createProject: (p: { project_id: string; name: string; description?: string; system_prompt?: string }) =>
    http<Project>('/projects', { method: 'POST', body: JSON.stringify(p) }),

  listSessions: (project_id: string) =>
    http<Session[]>(`/sessions?project_id=${encodeURIComponent(project_id)}`),
  createSession: (p: { project_id: string; session_id: string; user_intent?: string; base_branch?: string }) =>
    http<Session>('/sessions', { method: 'POST', body: JSON.stringify(p) }),

  listTasks: (project_id: string, session_id: string) =>
    http<Task[]>(`/tasks?project_id=${encodeURIComponent(project_id)}&session_id=${encodeURIComponent(session_id)}`),
  createTask: (p: { project_id: string; session_id: string; user_intent: string }) =>
    http<Task>('/tasks', { method: 'POST', body: JSON.stringify(p) }),
  getTask: (task_id: string) => http<Task>(`/tasks/${task_id}`),

  listTraces: (task_id: string) => http<TraceSummary[]>(`/traces?task_id=${task_id}`),
  getTrace: (trace_id: string) => http<TraceDetail>(`/traces/${trace_id}`),

  listConfigs: () => http<AgentConfig[]>('/agent_configs'),

  spawn: (p: { task_id: string; agent_config_id: string; preceding_trace_id?: string; initial_input?: string }) =>
    http<{ trace_id: string; cli_session_id: string; repo_path: string; branch: string }>(
      '/spawn', { method: 'POST', body: JSON.stringify(p) }
    ),

  label: (trace_id: string, p: { manual_label: string; failure_mode?: string; manual_notes?: string }) =>
    http(`/labels/${trace_id}`, { method: 'POST', body: JSON.stringify(p) }),
};
