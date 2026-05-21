import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Session } from '../api';

export function SessionsList({ project_id }: { project_id: string }) {
  const [items, setItems] = useState<Session[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [sid, setSid] = useState('');
  const [intent, setIntent] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');

  const reload = () => {
    api.listSessions(project_id).then(setItems).catch((e) => setErr(String(e)));
  };

  useEffect(reload, [project_id]);

  const submit = async () => {
    try {
      await api.createSession({ project_id, session_id: sid, user_intent: intent || undefined, base_branch: baseBranch });
      setShowForm(false);
      setSid(''); setIntent('');
      reload();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <section>
      <h2>Sessions <small>(git branches)</small></h2>
      {err && <pre style={{ color: 'red' }}>{err}</pre>}

      {showForm ? (
        <div style={{ border: '1px solid #ccc', padding: 8, marginBottom: 12 }}>
          <div><label>branch name: <input value={sid} onChange={(e) => setSid(e.target.value)} placeholder="feat/oauth" /></label></div>
          <div><label>user_intent:<br /><input value={intent} onChange={(e) => setIntent(e.target.value)} style={{ width: 500 }} /></label></div>
          <div><label>base_branch: <input value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} /></label></div>
          <button onClick={submit}>Create branch</button>{' '}
          <button onClick={() => setShowForm(false)}>Cancel</button>
        </div>
      ) : (
        <button onClick={() => setShowForm(true)}>+ New session (branch)</button>
      )}

      {items === null ? (
        <p>Loading…</p>
      ) : items.length === 0 ? (
        <p style={{ color: '#666' }}>No sessions yet.</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {items.map((s) => (
            <li key={s.session_id} style={{ border: '1px solid #aaa', padding: 8, margin: '6px 0' }}>
              <a href={`#/p/${project_id}/s/${encodeURIComponent(s.session_id)}`}><b>{s.session_id}</b></a>{' '}
              <small style={{ color: '#666' }}>[{s.status}] · from {s.base_branch}</small>
              {s.user_intent && <div style={{ fontSize: 12, color: '#444' }}>{s.user_intent}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
