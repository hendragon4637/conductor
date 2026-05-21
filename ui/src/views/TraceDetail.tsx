import { useEffect, useState } from 'react';
import { api } from '../api';
import type { TraceDetail as TraceDetailT } from '../api';

export function TraceDetail({ trace_id }: { trace_id: string }) {
  const [t, setT] = useState<TraceDetailT | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const [label, setLabel] = useState<string>('');
  const [failureMode, setFailureMode] = useState<string>('');
  const [notes, setNotes] = useState<string>('');

  const reload = () => {
    api.getTrace(trace_id).then((tt) => {
      setT(tt);
      setLabel(tt.manual_label || '');
      setFailureMode(tt.failure_mode || '');
    }).catch((e) => setErr(String(e)));
  };

  useEffect(reload, [trace_id]);

  useEffect(() => {
    if (!t) return;
    if (t.status === 'complete' || t.status === 'failed' || t.status === 'abandoned') return;
    const id = setInterval(reload, 10_000);
    return () => clearInterval(id);
  }, [t?.status, trace_id]);

  const saveLabel = async () => {
    if (!label) return;
    try {
      await api.label(trace_id, {
        manual_label: label,
        failure_mode: failureMode || undefined,
        manual_notes: notes || undefined,
      });
      reload();
    } catch (e) {
      setErr(String(e));
    }
  };

  if (err) return <pre style={{ color: 'red' }}>{err}</pre>;
  if (!t) return <p>Loading…</p>;

  return (
    <section>
      <h2>Trace <small>(room) — {t.role}</small></h2>

      <div style={{ display: 'flex', gap: 12 }}>
        <div style={{ flex: '0 0 320px', border: '1px solid #ccc', padding: 8 }}>
          <h3 style={{ marginTop: 0 }}>Metadata</h3>
          <div style={{ fontSize: 12 }}>
            <Row k="trace_id" v={t.trace_id} />
            <Row k="task_id" v={<a href={`#/p/${t.project_id}/s/${encodeURIComponent(t.session_id)}`}>{t.task_id.slice(0, 8)}…</a>} />
            <Row k="agent_config" v={t.agent_config_id} />
            <Row k="role" v={t.role} />
            <Row k="status" v={t.status} />
            <Row k="cli_session_id" v={t.cli_session_id?.slice(0, 12) || '—'} />
            <Row k="preceding" v={t.preceding_trace_id ? <a href={`#/trace/${t.preceding_trace_id}`}>{t.preceding_trace_id.slice(0, 8)}…</a> : '—'} />
            <Row k="skill_hash" v={t.skill_snapshot_hash ? t.skill_snapshot_hash.slice(0, 10) + '…' : '—'} />
            <Row k="terminates_task" v={String(t.terminates_task)} />
            <Row k="tokens" v={t.total_tokens ?? '—'} />
            <Row k="observations" v={t.total_observations ?? 0} />
            <Row k="started_at" v={t.started_at} />
            <Row k="ended_at" v={t.ended_at || '—'} />
            <Row k="duration_s" v={t.duration_s?.toFixed(1) ?? '—'} />
          </div>

          <h3 style={{ marginTop: 16 }}>Hand-label</h3>
          <div style={{ fontSize: 12 }}>
            <div>
              <label>
                manual_label:{' '}
                <select value={label} onChange={(e) => setLabel(e.target.value)}>
                  <option value="">—</option>
                  <option value="pass">pass</option>
                  <option value="fail">fail</option>
                  <option value="partial">partial</option>
                </select>
              </label>
            </div>
            <div>
              <label>
                failure_mode:{' '}
                <input value={failureMode} onChange={(e) => setFailureMode(e.target.value)}
                       placeholder="e.g. missed_constraint, wrong_tool" />
              </label>
            </div>
            <div>
              <label>
                notes:<br />
                <textarea value={notes} onChange={(e) => setNotes(e.target.value)} rows={3} cols={36} />
              </label>
            </div>
            <button onClick={saveLabel} disabled={!label}>Save label</button>
          </div>
        </div>

        <div style={{ flex: 1 }}>
          <h3 style={{ marginTop: 0 }}>Input spec</h3>
          <pre style={{ fontSize: 11, background: '#f4f4f4', padding: 8, maxHeight: 200, overflow: 'auto' }}>
            {JSON.stringify(t.input_spec, null, 2)}
          </pre>

          <h3>Output spec (contribution receipt)</h3>
          <pre style={{ fontSize: 11, background: '#f4f4f4', padding: 8, maxHeight: 200, overflow: 'auto' }}>
            {t.output_spec ? JSON.stringify(t.output_spec, null, 2) : '— (not yet emitted) —'}
          </pre>

          <h3>Observations ({t.observations.length})</h3>
          {t.observations.length === 0 ? (
            <p style={{ color: '#666', fontSize: 12 }}>
              No observations yet. The adapter ingests every 5 min — refresh in a few minutes.
            </p>
          ) : (
            <table style={{ width: '100%', fontSize: 11, borderCollapse: 'collapse' }}>
              <thead>
                <tr style={{ background: '#eee' }}>
                  <th align="left">#</th>
                  <th align="left">type</th>
                  <th align="left">tool</th>
                  <th align="left">status</th>
                  <th align="right">tok in/out</th>
                  <th align="right">latency</th>
                </tr>
              </thead>
              <tbody>
                {t.observations.map((o) => (
                  <tr key={o.observation_id} style={{ borderBottom: '1px solid #eee' }}>
                    <td>{o.step_index ?? ''}</td>
                    <td>{o.type}</td>
                    <td>{o.tool_name || ''}</td>
                    <td>{o.status || ''}</td>
                    <td align="right">{o.tokens_input ?? ''}/{o.tokens_output ?? ''}</td>
                    <td align="right">{o.latency_ms ?? ''}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>HITL events ({t.hitl_events.length})</h3>
          {t.hitl_events.length === 0 ? (
            <p style={{ color: '#666', fontSize: 12 }}>None.</p>
          ) : (
            <table style={{ width: '100%', fontSize: 11 }}>
              <thead><tr style={{ background: '#eee' }}><th align="left">asked</th><th align="left">prompt</th><th align="left">decision</th></tr></thead>
              <tbody>
                {t.hitl_events.map((h) => (
                  <tr key={h.hitl_id}>
                    <td>{h.asked_at}</td>
                    <td style={{ maxWidth: 400, overflow: 'hidden', textOverflow: 'ellipsis' }}>{h.prompt}</td>
                    <td>{h.decision}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}

          <h3>Scores ({t.scores.length})</h3>
          {t.scores.length === 0 ? (
            <p style={{ color: '#666', fontSize: 12 }}>None yet (eval runs nightly).</p>
          ) : (
            <table style={{ width: '100%', fontSize: 11 }}>
              <thead><tr style={{ background: '#eee' }}><th align="left">track</th><th align="left">dim</th><th align="right">value</th></tr></thead>
              <tbody>
                {t.scores.map((s) => (
                  <tr key={s.score_id}>
                    <td>{s.track}</td>
                    <td>{s.dimension}</td>
                    <td align="right">{(Number(s.value) * 100).toFixed(1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </section>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div>
      <span style={{ display: 'inline-block', width: 110, color: '#666' }}>{k}</span>
      <span>{v}</span>
    </div>
  );
}
