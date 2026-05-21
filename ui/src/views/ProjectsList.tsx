import { useEffect, useState } from 'react';
import { api } from '../api';
import type { Project } from '../api';

export function ProjectsList() {
  const [items, setItems] = useState<Project[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [pid, setPid] = useState('');
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [sysPrompt, setSysPrompt] = useState('');

  const reload = () => {
    api.listProjects().then(setItems).catch((e) => setErr(String(e)));
  };

  useEffect(reload, []);

  const submit = async () => {
    try {
      await api.createProject({ project_id: pid, name, description: desc || undefined, system_prompt: sysPrompt || undefined });
      setShowForm(false);
      setPid(''); setName(''); setDesc(''); setSysPrompt('');
      reload();
    } catch (e) {
      setErr(String(e));
    }
  };

  return (
    <section>
      <h2>Projects <small>(git repos)</small></h2>
      {err && <pre style={{ color: 'red' }}>{err}</pre>}

      {showForm ? (
        <div style={{ border: '1px solid #ccc', padding: 8, marginBottom: 12 }}>
          <div><label>project_id: <input value={pid} onChange={(e) => setPid(e.target.value)} placeholder="backend-api" /></label></div>
          <div><label>name: <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Backend API" /></label></div>
          <div><label>description: <input value={desc} onChange={(e) => setDesc(e.target.value)} style={{ width: 400 }} /></label></div>
          <div>
            <label>system_prompt:<br />
              <textarea value={sysPrompt} onChange={(e) => setSysPrompt(e.target.value)} rows={4} cols={70}
                placeholder="Long-lived project context. Injected into AGENTS.md on every spawn." />
            </label>
          </div>
          <button onClick={submit}>Create</button>{' '}
          <button onClick={() => setShowForm(false)}>Cancel</button>
        </div>
      ) : (
        <button onClick={() => setShowForm(true)}>+ New project</button>
      )}

      {items === null ? (
        <p>Loading…</p>
      ) : items.length === 0 ? (
        <p style={{ color: '#666' }}>No projects yet.</p>
      ) : (
        <ul style={{ listStyle: 'none', padding: 0 }}>
          {items.map((p) => (
            <li key={p.project_id} style={{ border: '1px solid #aaa', padding: 8, margin: '6px 0' }}>
              <a href={`#/p/${p.project_id}`}><b>{p.name}</b></a>{' '}
              <small style={{ color: '#666' }}>{p.project_id} · {p.repo_path}</small>
              {p.description && <div style={{ fontSize: 12, color: '#444' }}>{p.description}</div>}
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
