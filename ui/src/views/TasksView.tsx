import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';
import type { Task, TraceSummary, AgentConfig } from '../api';
import { navigate } from '../App';

interface Props {
  project_id: string;
  session_id: string;
}

export function TasksView({ project_id, session_id }: Props) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [configs, setConfigs] = useState<AgentConfig[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const reload = useCallback(() => {
    api.listTasks(project_id, session_id).then(setTasks).catch((e) => setErr(String(e)));
  }, [project_id, session_id]);

  useEffect(() => {
    api.listConfigs().then(setConfigs).catch(() => setConfigs([]));
    reload();
  }, [reload]);

  return (
    <div>
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Tasks</h2>
          <div className="subtitle">
            <code className="text-code">{project_id}</code>
            <span className="text-muted"> / </span>
            <code className="text-code">{session_id}</code>
          </div>
        </div>
        <div className="page-header-actions">
          {!showForm && (
            <button className="btn btn-primary" onClick={() => setShowForm(true)}>
              + New task
            </button>
          )}
        </div>
      </div>

      {err && <pre className="error" style={{ marginBottom: 16 }}>{err}</pre>}

      {showForm && (
        <NewTaskForm
          project_id={project_id}
          session_id={session_id}
          onCancel={() => setShowForm(false)}
          onCreated={() => {
            setShowForm(false);
            reload();
          }}
        />
      )}

      {tasks === null ? (
        <p className="text-muted">Loading…</p>
      ) : tasks.length === 0 && !showForm ? (
        <div className="empty-state">
          <h3>No tasks yet</h3>
          <p>Create your first task to start the executor.</p>
          <button className="btn btn-primary" onClick={() => setShowForm(true)}>
            + New task
          </button>
        </div>
      ) : (
        <div className="task-list">
          {tasks?.map((t) => (
            <TaskCard
              key={t.task_id}
              task={t}
              configs={configs}
              project_id={project_id}
              session_id={session_id}
              onChange={reload}
            />
          ))}
        </div>
      )}
    </div>
  );
}

// ───────────────────────────────── NewTaskForm ─────────────────────────────────

function NewTaskForm({
  project_id,
  session_id,
  onCancel,
  onCreated,
}: {
  project_id: string;
  session_id: string;
  onCancel: () => void;
  onCreated: () => void;
}) {
  const [intent, setIntent] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    if (!intent.trim()) return;
    setBusy(true);
    setErr(null);
    try {
      await api.createTask({ project_id, session_id, user_intent: intent.trim() });
      onCreated();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="card" style={{ marginBottom: 24 }}>
      <div className="field">
        <label>user_intent</label>
        <textarea
          className="textarea"
          rows={3}
          autoFocus
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          placeholder="e.g. Add /auth/refresh endpoint with token rotation."
        />
      </div>
      {err && <pre className="error">{err}</pre>}
      <div className="row" style={{ justifyContent: 'flex-end' }}>
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !intent.trim()}>
          {busy ? 'Creating…' : 'Create task'}
        </button>
      </div>
    </div>
  );
}

// ───────────────────────────────── TaskCard ─────────────────────────────────

function TaskCard({
  task,
  configs,
  project_id,
  session_id,
  onChange,
}: {
  task: Task;
  configs: AgentConfig[];
  project_id: string;
  session_id: string;
  onChange: () => void;
}) {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [pickedConfig, setPickedConfig] = useState<string>('');
  const [spawning, setSpawning] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const reloadTraces = useCallback(() => {
    api.listTraces(task.task_id).then(setTraces).catch(() => setTraces([]));
  }, [task.task_id]);

  useEffect(reloadTraces, [reloadTraces]);
  useEffect(() => {
    const id = setInterval(reloadTraces, 10_000);
    return () => clearInterval(id);
  }, [reloadTraces]);

  useEffect(() => {
    if (!pickedConfig && configs.length) setPickedConfig(configs[0].agent_config_id);
  }, [configs, pickedConfig]);

  const doSpawn = async () => {
    if (!pickedConfig) return;
    setSpawning(true);
    setErr(null);
    try {
      await api.spawn({ task_id: task.task_id, agent_config_id: pickedConfig });
      onChange();
      reloadTraces();
    } catch (e) {
      setErr(String(e));
    } finally {
      setSpawning(false);
    }
  };

  return (
    <article className="task-card">
      <header className="task-card-header">
        <div style={{ flex: 1 }}>
          <div className="task-card-title">{task.user_intent}</div>
          <div className="task-card-meta">
            <span>{traces.length} trace{traces.length === 1 ? '' : 's'}</span>
            <span>·</span>
            <span><code className="text-code">{task.task_id.slice(0, 8)}</code></span>
          </div>
        </div>
        <span className={`badge ${taskStatusBadge(task.status)}`}>{task.status}</span>
      </header>

      {traces.length > 0 && (
        <div className="trace-chips">
          {traces.map((tr) => (
            <a
              key={tr.trace_id}
              href={`#/p/${project_id}/s/${encodeURIComponent(session_id)}/t/${tr.trace_id}`}
              className={`chip ${traceChipClass(tr)}`}
            >
              <span>{tr.role}</span>
              <span>{traceStatusGlyph(tr)}</span>
              <span className="text-muted">#{tr.trace_id.slice(0, 6)}</span>
            </a>
          ))}
        </div>
      )}

      <footer className="task-card-footer">
        <select
          className="select config-select"
          value={pickedConfig}
          onChange={(e) => setPickedConfig(e.target.value)}
          disabled={spawning}
        >
          {configs.length === 0 && <option value="">No configs available</option>}
          {configs.map((c) => (
            <option key={c.agent_config_id} value={c.agent_config_id}>
              {c.agent_config_id}
            </option>
          ))}
        </select>
        <button
          className="btn btn-primary btn-sm"
          onClick={doSpawn}
          disabled={spawning || !pickedConfig}
        >
          {spawning ? 'Spawning…' : '▶ Spawn'}
        </button>
      </footer>

      {err && <pre className="error" style={{ marginTop: 12 }}>{err}</pre>}
    </article>
  );
}

// ───────────────────────────────── helpers ─────────────────────────────────

function taskStatusBadge(s: string): string {
  switch (s) {
    case 'done': return 'badge-success';
    case 'in_progress': return 'badge-running';
    case 'blocked':
    case 'abandoned': return 'badge-failed';
    case 'open':
    default: return 'badge-idle';
  }
}

function traceChipClass(tr: TraceSummary): string {
  if (tr.manual_label === 'fail') return 'chip-failed';
  if (tr.manual_label === 'pass') return 'chip-success';
  if (tr.status === 'complete') return 'chip-success';
  if (tr.status === 'running' || tr.status === 'spawned' || tr.status === 'awaiting_hitl') return 'chip-running';
  if (tr.status === 'failed' || tr.status === 'abandoned') return 'chip-failed';
  return 'chip-idle';
}

function traceStatusGlyph(tr: TraceSummary): string {
  if (tr.manual_label === 'fail') return '✗';
  if (tr.manual_label === 'pass') return '✓';
  if (tr.status === 'complete') return '✓';
  if (tr.status === 'running' || tr.status === 'spawned') return '⏳';
  if (tr.status === 'awaiting_hitl') return '?';
  if (tr.status === 'failed') return '✗';
  return '·';
}
