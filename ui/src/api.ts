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

// ── Projects ──────────────────────────────────────────────────────────
export interface Project {
  project_id: string;
  name: string;
  description: string | null;
  system_prompt: string | null;
  repo_path: string;
  created_at: string;
}

// ── Sessions ──────────────────────────────────────────────────────────
export interface Session {
  row_id?: string;
  session_id: string;
  project_id: string;
  user_intent: string | null;
  status: string;
  base_branch: string;
  created_at: string;
  aionui_team_id?: string | null;
  /** Watcher verdict: 'running' | 'stalled' | 'quota' | 'crashed' | 'done' */
  watcher_verdict?: string;
  /** Seconds since last activity */
  last_activity_s?: number | null;
  /** Token rate (tok/s) */
  token_rate?: number | null;
  plan_title?: string | null;
  active_node_id?: string | null;
  active_node_title?: string | null;
  node_commit_tag?: string | null;
  score?: number | null;
  worktree_path?: string | null;
  worktree_label?: string | null;
  /** Backend type: 'aionui' | 'hermes' | 'opencode' | 'opencode_omo' | 'claude-code' | 'codex' | 'gemini' */
  backend_type?: string;
}

// ── Tasks ─────────────────────────────────────────────────────────────
export interface Task {
  task_id: string;
  project_id: string;
  session_id: string;
  user_intent: string;
  status: string;
  completion_signal: string | null;
  created_at: string;
}

// ── Traces ────────────────────────────────────────────────────────────
export interface TraceSummary {
  trace_id: string;
  task_id: string;
  session_id: string;
  project_id: string;
  agent_config_id: string;
  harness: string;
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

// ── Agent Configs ─────────────────────────────────────────────────────
export interface AgentConfig {
  agent_config_id: string;
  cli: string;
  harness: string;
  domain: string;
  role: string;
  pattern: string;
  active: boolean;
}

// ── Chat ──────────────────────────────────────────────────────────────
export interface Thread {
  thread_id: string;
  title: string;
  project_id: string | null;
  model: string;
  created_at: string;
}

export interface ChatMessage {
  message_id: string;
  role: string;
  content: string;
  created_at: string;
}

export interface SendMessageResponse {
  user_message: ChatMessage;
  assistant_message: ChatMessage;
}

// ── Plans ─────────────────────────────────────────────────────────────
export interface PlanNode {
  node_id: string;
  title: string;
  description: string;
  depends_on: string[];
  status: string;
  /** Specialist agent_config_ids for this node.
   *  The built-in orchestrator is IMPLICIT (always present, not in this list). */
  members?: string[];
  /** Legacy single-agent_config_id (kept for backward compat) */
  agent_config_id?: string;
  success_criterion?: string;
  /** Node commit tag set by Conductor after completion */
  node_commit_tag?: string;
  /** Gate mode: 'watcher_only' (v1), 'test_cmd', 'reviewer' */
  gate_mode?: string;
}

export interface Plan {
  plan_id: string;
  title: string;
  description: string | null;
  worktree_id: string | null;
  project_id: string | null;
  status: string;
  nodes: PlanNode[];
  created_at: string;
  source_thread?: string;
  messages?: ChatMessage[];
  /** Worktree path assigned during execution */
  worktree_path?: string;
}

// ── Scores ────────────────────────────────────────────────────────────
export interface ScoreRow {
  agent_config: string;
  average_score: number;
  trace_count: number;
  status: string;
}

export interface ScoreTrend {
  date: string;
  average: number;
}

export interface ScoresResponse {
  rows: ScoreRow[];
  total: number;
  error?: string;
}

export interface TrendsResponse {
  trends: ScoreTrend[];
}

// ── Ratchet ───────────────────────────────────────────────────────────
export interface Experiment {
  experiment_id: string;
  agent_config: string;
  baseline_score: number;
  candidate_score: number;
  delta: number;
  decision: string;
  created_at: string | null;
}

export interface Approval {
  mutation_id: string;
  agent_config: string;
  skill_path: string;
  kept: boolean | null;
  rationale: string;
  experiment_id: string;
  created_at: string | null;
}

// ── Triggers ──────────────────────────────────────────────────────────
export interface Trigger {
  trigger_id: string;
  name: string;
  trigger_type: string;
  project_id: string;
  session_id: string;
  agent_config_id: string;
  cron_expression: string | null;
  active: boolean;
  fire_count: number;
  last_fired_at: string | null;
  next_fire_at: string | null;
  sandboxed: boolean;
  job_type: string;
}

// ── Worktrees ─────────────────────────────────────────────────────────
export interface Worktree {
  path?: string;
  head?: string;
  branch?: string;
  bare?: boolean;
}

export interface WorktreesResponse {
  worktrees: Worktree[];
  total: number;
  error?: string;
}

// ── Settings ──────────────────────────────────────────────────────────
export interface ConnectionStatus {
  url: string;
  status: string;
}

export interface BrainStatus extends ConnectionStatus {
  model: string;
}

export interface Settings {
  aionui: ConnectionStatus;
  langfuse: ConnectionStatus;
  brain: BrainStatus;
  conductor: { version: string; workspace_root: string };
}

// ── API object ────────────────────────────────────────────────────────
export const api = {
  // ── Projects ──
  listProjects: () => http<Project[]>('/projects'),
  createProject: (p: { project_id: string; name: string; description?: string; system_prompt?: string }) =>
    http<Project>('/projects', { method: 'POST', body: JSON.stringify(p) }),

  // ── Sessions ──
  listSessions: (project_id: string) =>
    http<Session[]>(`/sessions?project_id=${encodeURIComponent(project_id)}`),
  createSession: (p: { project_id: string; session_id: string; user_intent?: string; base_branch?: string }) =>
    http<Session>('/sessions', { method: 'POST', body: JSON.stringify(p) }),

  // ── Tasks ──
  listTasks: (project_id: string, session_id: string) =>
    http<Task[]>(`/tasks?project_id=${encodeURIComponent(project_id)}&session_id=${encodeURIComponent(session_id)}`),
  createTask: (p: { project_id: string; session_id: string; user_intent: string }) =>
    http<Task>('/tasks', { method: 'POST', body: JSON.stringify(p) }),
  getTask: (task_id: string) => http<Task>(`/tasks/${task_id}`),

  // ── Traces ──
  listTraces: (task_id: string) => http<TraceSummary[]>(`/traces?task_id=${task_id}`),
  getTrace: (trace_id: string) => http<TraceDetail>(`/traces/${trace_id}`),

  // ── Agent Configs ──
  listConfigs: () => http<AgentConfig[]>('/agent_configs'),

  // ── Spawn ──
  spawn: (p: { task_id: string; agent_config_id: string; preceding_trace_id?: string; initial_input?: string; spawn_mode?: string }) =>
    http<{ trace_id: string; cli_session_id: string; repo_path: string; branch: string; spawn_mode: string; pty_spec: any }>(
      '/spawn', { method: 'POST', body: JSON.stringify(p) }
    ),

  // ── Label ──
  label: (trace_id: string, p: { manual_label: string; failure_mode?: string; manual_notes?: string }) =>
    http(`/labels/${trace_id}`, { method: 'POST', body: JSON.stringify(p) }),

  // ── Resume ──
  resumeSession: (trace_id: string, body?: { initial_input?: string }) =>
    http<{ pty_spec: any }>(`/traces/${trace_id}/resume-session`, {
      method: 'POST',
      body: body ? JSON.stringify(body) : undefined,
    }),

  // ── Chat ──
  listThreads: () => http<Thread[]>('/chat/threads'),
  createThread: (p?: { title?: string; project_id?: string; model?: string }) =>
    http<Thread>('/chat/threads', { method: 'POST', body: JSON.stringify(p || {}) }),
  getThread: (thread_id: string) => http<{ thread_id: string; title: string; messages: ChatMessage[] }>(`/chat/threads/${thread_id}`),
  sendMessage: (thread_id: string, content: string) =>
    http<SendMessageResponse>(`/chat/threads/${thread_id}/messages`, {
      method: 'POST', body: JSON.stringify({ content }),
    }),
  promoteToPlan: (thread_id: string, message_ids: string[]) =>
    http<Plan>('/chat/promote-to-plan', {
      method: 'POST', body: JSON.stringify({ thread_id, message_ids }),
    }),

  // ── Plans ──
  listPlans: () => http<Plan[]>('/plans'),
  proposePlan: (p: { title: string; description?: string; project_id?: string }) =>
    http<Plan>('/plans', { method: 'POST', body: JSON.stringify(p) }),
  getPlan: (plan_id: string) => http<Plan>(`/plans/${plan_id}`),
  approvePlan: (plan_id: string, approve: boolean, auto_approve?: boolean, comment?: string) =>
    http<Plan>(`/plans/${plan_id}/approve`, {
      method: 'POST', body: JSON.stringify({ approve, auto_approve, comment }),
    }),
  appendNode: (plan_id: string, p: { title: string; description: string; depends_on?: string[] }) =>
    http<Plan>(`/plans/${plan_id}/nodes`, {
      method: 'POST', body: JSON.stringify(p),
    }),

  // ── Scores ──
  listScores: () => http<ScoresResponse>('/scores'),
  listTrends: () => http<TrendsResponse>('/scores/trends'),

  // ── Ratchet ──
  listExperiments: () => http<{ rows: Experiment[]; total: number }>('/ratchet/experiments'),
  runRatchet: (p?: { threshold?: number; min_runs?: number; propose_only?: boolean; max_tasks?: number }) =>
    http<any>('/ratchet/run', { method: 'POST', body: JSON.stringify(p || {}) }),
  listApprovals: () => http<{ rows: Approval[]; total: number }>('/ratchet/approvals'),

  // ── Triggers ──
  listTriggers: () => http<Trigger[]>('/triggers'),

  // ── Worktrees ──
  listWorktrees: () => http<WorktreesResponse>('/worktrees'),
  createWorktree: (p: { branch: string; project_id?: string }) =>
    http<{ path: string; branch: string; project_id: string }>('/worktrees', {
      method: 'POST', body: JSON.stringify(p),
    }),
  removeWorktree: (path: string) =>
    http<{ removed: string }>(`/worktrees/${encodeURIComponent(path)}`, { method: 'DELETE' }),

  // ── Settings ──
  getSettings: () => http<Settings>('/settings'),

  // ── Memory ──
  listMemory: (project_id: string) =>
    http<any[]>(`/memory?project_id=${encodeURIComponent(project_id)}`),
};
