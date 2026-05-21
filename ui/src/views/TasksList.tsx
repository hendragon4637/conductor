import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Task, TraceSummary, AgentConfig } from '../api';

export function TasksList({ project_id, session_id }: { project_id: string; session_id: string }) {
  const [tasks, setTasks] = useState<Task[] | null>(null);
  const [configs, setConfigs] = useState<AgentConfig[]>([]);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [intent, setIntent] = useState('');

  const reload = () => {
    api.listTasks(project_id, session_id).then(setTasks).catch((e) => setErr(String(e)));
  };

  useEffect(() => {
    api.listConfigs().then(setConfigs).catch((e) => setErr(String(e)));
    reload();
  }, [project_id, session_id]);

  const submit = async () => {
    try {
      await api.createTask({ project_id, session_id, user_intent: intent });
      setShowForm(false);
      setIntent('');
      reload();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <section>
      <h2>Tasks <small>(flexible work units)</small></h2>
      {err && <pre style={{ color: 'red' }}>{err}</pre>}

      {showForm ? (
        <div style={{ border: '1px solid #ccc', padding: 8, marginBottom: 12 }}>
          <label>
            user_intent:<br />
            <textarea value={intent} onChange={(e) => setIntent(e.target.value)} rows={3} cols={80}
              placeholder="e.g. Add /auth/refresh endpoint with token rotation" />
          </label>
          <br />
          <button onClick={submit}>Create task</button>{' '}
          <button onClick={() => setShowForm(false)}>Cancel</button>
        </div>
      ) : (
        <button onClick={() => setShowForm(true)}>+ New task</button>
      )}

      {tasks === null ? (
        <p>Loading…</p>
      ) : tasks.length === 0 ? (
        <p style={{ color: '#666' }}>No tasks yet.</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {tasks.map((t) => (
            <TaskRow key={t.task_id} task={t} configs={configs} onChange={reload} />
          ))}
        </ul>
      )}
    </section>
  );
}

function TaskRow({ task, configs, onChange }: { task: Task; configs: AgentConfig[]; onChange: () => void }) {
  const [traces, setTraces] = useState<TraceSummary[]>([]);
  const [spawning, setSpawning] = useState(false);
  const [pickedConfig, setPickedConfig] = useState<string>(configs[0]?.agent_config_id || '');
  const [err, setErr] = useState<string | null>(null);

  const reloadTraces = () => {
    api.listTraces(task.task_id).then(setTraces).catch(() => setTraces([]));
  };

  useEffect(reloadTraces, [task.task_id]);
  useEffect(() => {
    const id = setInterval(reloadTraces, 10_000);
    return () => clearInterval(id);
  }, [task.task_id]);

  useEffect(() => {
    if (!pickedConfig && configs.length) setPickedConfig(configs[0].agent_config_id);
  }, [configs]);

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
    <li style={{ border: '1px solid #aaa', padding: 8, margin: '6px 0' }}>
      <div>
        <b>{task.user_intent}</b>{' '}
        <small style={{ color: '#666' }}>
          [{task.status}] · {traces.length} trace{traces.length === 1 ? '' : 's'} · {task.task_id.slice(0, 8)}…
        </small>
      </div>

      {traces.length > 0 && (
        <div style={{ marginTop: 6, display: 'flex', gap: 4, flexWrap: 'wrap', fontSize: 11 }}>
          {traces.map((tr) => (
            <a key={tr.trace_id}
               href={`#/trace/${tr.trace_id}`}
               style={{
                 border: '1px solid #888', padding: '2px 6px',
                 background: traceColor(tr),
                 textDecoration: 'none', color: 'inherit',
               }}>
              {tr.role}{statusIcon(tr.status)}
              {tr.manual_label === 'pass' && ' ✓'}
              {tr.manual_label === 'fail' && ' ✗'}
            </a>
          ))}
        </div>
      )}

      <div style={{ marginTop: 8 }}>
        <label style={{ fontSize: 12 }}>
          spawn:{' '}
          <select value={pickedConfig} onChange={(e) => setPickedConfig(e.target.value)}>
            {configs.map((c) => (
              <option key={c.agent_config_id} value={c.agent_config_id}>{c.agent_config_id}</option>
            ))}
          </select>
        </label>{' '}
        <button onClick={doSpawn} disabled={spawning || !pickedConfig}>
          {spawning ? 'spawning…' : '▶ Spawn native terminal'}
        </button>
      </div>

      {err && <pre style={{ color: 'red', fontSize: 11 }}>{err}</pre>}
    </li>
  );
}

function statusIcon(s: string): string {
  if (s === 'complete') return ' ✓';
  if (s === 'running' || s === 'spawned') return ' ⏳';
  if (s === 'failed') return ' ✗';
  if (s === 'awaiting_hitl') return ' ?';
  return ' ⋯';
}

function traceColor(tr: TraceSummary): string {
  if (tr.manual_label === 'fail') return '#f8d7da';
  if (tr.status === 'complete' && tr.manual_label === 'pass') return '#d4edda';
  if (tr.status === 'complete') return '#d4edda';
  if (tr.status === 'running' || tr.status === 'spawned') return '#fff3cd';
  if (tr.status === 'failed') return '#f8d7da';
  return '#e2e3e5';
}
