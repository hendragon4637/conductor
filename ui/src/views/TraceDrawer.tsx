import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';
import { pendingSpawns } from '../lib/ptyRegistry';
import type { TraceDetail as TraceDetailT } from '../api';

interface Props {
  trace_id: string;
  onClose: () => void;
}

export function TraceDrawer({ trace_id, onClose }: Props) {
  const [t, setT] = useState<TraceDetailT | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const reload = useCallback(() => {
    api.getTrace(trace_id)
      .then(setT)
      .catch((e) => setErr(String(e)));
  }, [trace_id]);

  useEffect(reload, [reload]);

  useEffect(() => {
    if (!t) return;
    if (['complete', 'failed', 'abandoned'].includes(t.status)) return;
    const id = setInterval(reload, 10_000);
    return () => clearInterval(id);
  }, [t?.status, reload]);

  if (err) {
    return (
      <aside className="drawer">
        <DrawerHeader trace_id={trace_id} role="?" status="error" onClose={onClose} />
        <div className="drawer-body">
          <pre className="error">{err}</pre>
        </div>
      </aside>
    );
  }

  if (!t) {
    return (
      <aside className="drawer">
        <DrawerHeader trace_id={trace_id} role="…" status="loading" onClose={onClose} />
        <div className="drawer-body">
          <p className="text-muted">Loading…</p>
        </div>
      </aside>
    );
  }

  return (
    <aside className="drawer">
      <DrawerHeader
        trace_id={trace_id}
        role={t.role}
        status={t.status}
        agent_config_id={t.agent_config_id}
        onClose={onClose}
      />
      <div className="drawer-body">

        <section className="drawer-section">
          <ContinueSection trace_id={trace_id} />
        </section>

        <section className="drawer-section">
          <h4>Metadata</h4>
          <Metadata t={t} />
        </section>

        <section className="drawer-section">
          <h4>Input spec</h4>
          <details className="collapsible">
            <summary>{t.input_spec ? 'Show JSON' : '(no input spec)'}</summary>
            {!!t.input_spec && (
              <div className="collapsible-content">
                <pre className="json-block">{JSON.stringify(t.input_spec, null, 2)}</pre>
              </div>
            )}
          </details>
        </section>

        <section className="drawer-section">
          <h4>Output spec (contribution receipt)</h4>
          {t.output_spec ? (
            <details className="collapsible" open>
              <summary>Show JSON</summary>
              <div className="collapsible-content">
                <pre className="json-block">{JSON.stringify(t.output_spec, null, 2)}</pre>
              </div>
            </details>
          ) : (
            <p className="text-muted text-sm">— not yet emitted —</p>
          )}
        </section>

        <section className="drawer-section">
          <h4>Observations ({t.observations.length})</h4>
          <ObservationsTable observations={t.observations} />
        </section>

        <section className="drawer-section">
          <h4>HITL events ({t.hitl_events.length})</h4>
          <HitlList events={t.hitl_events} />
        </section>

        <section className="drawer-section">
          <h4>Scores ({t.scores.length})</h4>
          <ScoresList scores={t.scores} />
        </section>

        <section className="drawer-section">
          <h4>Label this trace</h4>
          <LabelForm trace_id={trace_id} initial={t} onSaved={reload} />
        </section>
      </div>
    </aside>
  );
}

// ───────────────────────────────── DrawerHeader ─────────────────────────────────

function DrawerHeader({
  trace_id, role, status, agent_config_id, onClose,
}: {
  trace_id: string;
  role: string;
  status: string;
  agent_config_id?: string;
  onClose: () => void;
}) {
  return (
    <div className="drawer-header">
      <div className="drawer-header-title">
        <span className="uppercase-label">Trace · {role}</span>
        <code className="text-code">#{trace_id.slice(0, 12)}</code>
        {agent_config_id && (
          <span className="text-secondary text-xs">{agent_config_id}</span>
        )}
        <div style={{ marginTop: 4 }}>
          <span className={`badge ${statusBadgeClass(status)}`}>{status}</span>
        </div>
      </div>
      <button className="drawer-close" onClick={onClose} aria-label="Close drawer">×</button>
    </div>
  );
}

function statusBadgeClass(s: string): string {
  if (s === 'complete') return 'badge-success';
  if (['running', 'spawned', 'awaiting_hitl'].includes(s)) return 'badge-running';
  if (['failed', 'abandoned'].includes(s)) return 'badge-failed';
  return 'badge-idle';
}

// ───────────────────────────────── Metadata ─────────────────────────────────

function Metadata({ t }: { t: TraceDetailT }) {
  return (
    <dl className="kv-grid">
      <dt>task</dt>        <dd><code className="text-code">{t.task_id.slice(0, 8)}</code></dd>
      <dt>cli</dt>         <dd>{(t as any).cli || '—'}</dd>
      <dt>cli_session</dt> <dd>{t.cli_session_id?.slice(0, 16) || '—'}</dd>
      <dt>preceding</dt>   <dd>
        {t.preceding_trace_id ? (
          <code className="text-code">#{t.preceding_trace_id.slice(0, 8)}</code>
        ) : '—'}
      </dd>
      <dt>skill</dt>       <dd>
        {t.skill_snapshot_hash ? <code className="text-code">{t.skill_snapshot_hash.slice(0, 10)}</code> : '—'}
      </dd>
      <dt>terminates</dt>  <dd>{String(t.terminates_task)}</dd>
      <dt>tokens</dt>      <dd>{t.total_tokens ?? '—'}</dd>
      <dt>obs</dt>         <dd>{t.total_observations ?? 0}</dd>
      <dt>started</dt>     <dd>{formatTs(t.started_at)}</dd>
      <dt>ended</dt>       <dd>{t.ended_at ? formatTs(t.ended_at) : '—'}</dd>
      <dt>duration</dt>    <dd>{t.duration_s != null ? `${t.duration_s.toFixed(1)}s` : '—'}</dd>
    </dl>
  );
}

// ───────────────────────────────── ContinueSection ─────────────────────────────────

function ContinueSection({ trace_id }: { trace_id: string }) {
  const [prompt, setPrompt] = useState('');
  const [busy, setBusy] = useState(false);
  const [doneErr, setDoneErr] = useState<string | null>(null);

  const doContinue = async () => {
    setBusy(true);
    setDoneErr(null);
    try {
      const res = await api.resumeSession(trace_id, {
        initial_input: prompt.trim() || undefined,
      });
      if (res?.pty_spec) {
        pendingSpawns.set(`${trace_id}::resume`, {
          ...res.pty_spec,
          isShell: false,
        });
        setPrompt('');
      }
    } catch (e) {
      setDoneErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="field" style={{ marginBottom: 8 }}>
        <textarea
          className="textarea"
          rows={2}
          value={prompt}
          onChange={(e) => setPrompt(e.target.value)}
          placeholder="Prompt to send on resume (optional)"
        />
      </div>
      {doneErr && <pre className="error" style={{ marginBottom: 8 }}>{doneErr}</pre>}
      <button className="btn btn-primary btn-sm" onClick={doContinue} disabled={busy}>
        {busy ? 'Resuming…' : 'Continue'}
      </button>
    </div>
  );
}

function formatTs(s: string): string {
  try {
    return new Date(s).toLocaleString();
  } catch { return s; }
}

// ───────────────────────────────── ObservationsTable ─────────────────────────────────

function ObservationsTable({ observations }: { observations: TraceDetailT['observations'] }) {
  if (observations.length === 0) {
    return <p className="text-muted text-sm">No observations yet. Adapter polls every 5 min.</p>;
  }
  return (
    <table className="table">
      <thead>
        <tr>
          <th>#</th>
          <th>type</th>
          <th>tool</th>
          <th>status</th>
          <th style={{ textAlign: 'right' }}>latency</th>
        </tr>
      </thead>
      <tbody>
        {observations.map((o) => (
          <tr key={o.observation_id}>
            <td>{o.step_index ?? ''}</td>
            <td>{o.type}</td>
            <td className="obs-tool">{o.tool_name || ''}</td>
            <td>{o.status || ''}</td>
            <td style={{ textAlign: 'right' }}>{o.latency_ms != null ? `${o.latency_ms}ms` : ''}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ───────────────────────────────── HitlList ─────────────────────────────────

function HitlList({ events }: { events: TraceDetailT['hitl_events'] }) {
  if (events.length === 0) return <p className="text-muted text-sm">None.</p>;
  return (
    <table className="table">
      <thead>
        <tr><th>asked</th><th>prompt</th><th>decision</th></tr>
      </thead>
      <tbody>
        {events.map((h) => (
          <tr key={h.hitl_id}>
            <td>{formatTs(h.asked_at)}</td>
            <td style={{ maxWidth: 180, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{h.prompt}</td>
            <td>{h.decision}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ───────────────────────────────── ScoresList ─────────────────────────────────

function ScoresList({ scores }: { scores: TraceDetailT['scores'] }) {
  if (scores.length === 0) return <p className="text-muted text-sm">No scores yet (eval is on-demand).</p>;
  return (
    <table className="table">
      <thead>
        <tr><th>track</th><th>dim</th><th style={{ textAlign: 'right' }}>value</th></tr>
      </thead>
      <tbody>
        {scores.map((s) => (
          <tr key={s.score_id}>
            <td>{s.track}</td>
            <td>{s.dimension}</td>
            <td style={{ textAlign: 'right' }}>{(Number(s.value) * 100).toFixed(1)}%</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

// ───────────────────────────────── LabelForm ─────────────────────────────────

function LabelForm({
  trace_id,
  initial,
  onSaved,
}: {
  trace_id: string;
  initial: TraceDetailT;
  onSaved: () => void;
}) {
  const [label, setLabel] = useState<string>(initial.manual_label || '');
  const [failureMode, setFailureMode] = useState<string>(initial.failure_mode || '');
  const [notes, setNotes] = useState<string>('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const save = async () => {
    if (!label) return;
    setBusy(true);
    setErr(null);
    try {
      await api.label(trace_id, {
        manual_label: label,
        failure_mode: failureMode || undefined,
        manual_notes: notes || undefined,
      });
      onSaved();
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <div className="field">
        <label>label</label>
        <div className="radio-group">
          {(['pass', 'fail', 'partial'] as const).map((v) => (
            <button
              key={v}
              type="button"
              className={`radio-pill ${label === v ? 'selected ' + v : ''}`}
              onClick={() => setLabel(v)}
            >
              {v}
            </button>
          ))}
        </div>
      </div>

      <div className="field">
        <label>failure_mode</label>
        <input
          className="input"
          value={failureMode}
          onChange={(e) => setFailureMode(e.target.value)}
          placeholder="e.g. missing_test, wrong_signature"
        />
      </div>

      <div className="field">
        <label>notes</label>
        <textarea
          className="textarea"
          rows={3}
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="What was right, what was wrong?"
        />
      </div>

      {err && <pre className="error">{err}</pre>}

      <button className="btn btn-primary btn-sm" onClick={save} disabled={busy || !label}>
        {busy ? 'Saving…' : 'Save label'}
      </button>
    </div>
  );
}
