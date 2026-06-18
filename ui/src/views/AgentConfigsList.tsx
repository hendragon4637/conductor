import { useEffect, useState } from 'react';
import { api } from '../api';
import type { AgentConfig } from '../api';

interface FormState {
  name: string;
  cli: string;
  model: string;
  role: string;
  permEdit: string;
  permBash: string;
  permWebfetch: string;
  skills: string;
  context: string;
}

const INITIAL_FORM: FormState = {
  name: '',
  cli: 'opencode',
  model: 'deepseek-v4-flash',
  role: 'executor',
  permEdit: 'allow',
  permBash: 'allow',
  permWebfetch: 'deny',
  skills: '',
  context: '',
};

const CLI_OPTIONS = ['opencode', 'claude-code', 'gemini', 'codex'];
const MODEL_OPTIONS = ['deepseek-v4-flash', 'local-ovms/qwen3-8b-int4 (GPU)'];
const ROLE_OPTIONS = ['executor', 'reviewer', 'planner'];
const PERM_OPTIONS = ['allow', 'deny', 'ask'];

const permStyle = (val: string): React.CSSProperties => ({
  color: val === 'allow' ? 'var(--status-success)' : val === 'deny' ? 'var(--status-failed)' : 'var(--status-running)',
});

export function AgentConfigsList() {
  const [items, setItems] = useState<AgentConfig[] | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [form, setForm] = useState<FormState>(INITIAL_FORM);

  const load = () => {
    setLoading(true);
    setError(null);
    api.listConfigs()
      .then(setItems)
      .catch((err: Error) => {
        setError(err.message);
        setItems([]);
      })
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const createConfig = async () => {
    if (!form.name.trim()) return;
    try {
      await fetch('/api/agent_configs', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          agent_config_id: form.name.trim(),
          cli: form.cli,
          harness: form.cli,
          domain: form.context,
          role: form.role,
          pattern: form.skills,
          active: true,
        }),
      });
      setForm(INITIAL_FORM);
      setShowForm(false);
      load();
    } catch (err: any) {
      setError(err.message);
    }
  };

  const updatePerm = (key: 'permEdit' | 'permBash' | 'permWebfetch', val: string) => {
    setForm(prev => ({ ...prev, [key]: val }));
  };

  /* ── Loading ── */
  if (loading && items === null) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Agents</h2>
            <div className="subtitle">agent_config templates — engine-agnostic. Deterministic (permissions) vs probabilistic (context/skills) shown distinctly.</div>
          </div>
        </div>
        <div className="empty-state"><h3>Loading agent configs…</h3></div>
      </div>
    );
  }

  /* ── Error ── */
  if (error && items !== null && items.length === 0) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Agents</h2>
            <div className="subtitle">agent_config templates — engine-agnostic. Deterministic (permissions) vs probabilistic (context/skills) shown distinctly.</div>
          </div>
        </div>
        <div className="error">{error}</div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Agents</h2>
          <div className="subtitle">agent_config templates — engine-agnostic. Deterministic (permissions) vs probabilistic (context/skills) shown distinctly.</div>
        </div>
      </div>

      {/* ── Header row: count + button ── */}
      <div className="row between" style={{ marginBottom: 8 }}>
        <span className="muted small">
          {items === null ? '\u2014' : `${items.length} ${items.length === 1 ? 'config' : 'configs'}`}
        </span>
        <button className="btn btn-primary btn-sm" onClick={() => setShowForm(prev => !prev)}>
          {showForm ? 'Cancel' : '+ New agent_config'}
        </button>
      </div>

      {/* ── Inline create form ── */}
      {showForm && (
        <div className="editor">
          <b className="small">+ New agent_config</b>
          <div className="grid2" style={{ gap: '6px 14px' }}>
            <div>
              <label>Name</label>
              <input
                className="input"
                type="text"
                placeholder="e.g. finance-fullstack-executor"
                value={form.name}
                onChange={e => setForm(prev => ({ ...prev, name: e.target.value }))}
              />
              <label>Engine (CLI)</label>
              <select className="select" value={form.cli} onChange={e => setForm(prev => ({ ...prev, cli: e.target.value }))}>
                {CLI_OPTIONS.map(o => <option key={o}>{o}</option>)}
              </select>
              <label>Model</label>
              <select className="select" value={form.model} onChange={e => setForm(prev => ({ ...prev, model: e.target.value }))}>
                {MODEL_OPTIONS.map(o => <option key={o}>{o}</option>)}
              </select>
              <label>Role</label>
              <select className="select" value={form.role} onChange={e => setForm(prev => ({ ...prev, role: e.target.value }))}>
                {ROLE_OPTIONS.map(o => <option key={o}>{o}</option>)}
              </select>
            </div>
            <div>
              <label>
                Permission <span className="tag">deterministic &middot; hard</span>
              </label>
              <div className="row tiny" style={{ gap: 10, marginTop: 2 }}>
                {(['edit', 'bash', 'webfetch'] as const).map(name => (
                  <span key={name}>
                    {name}{' '}
                    <select
                      className="select"
                      style={{ width: 'auto', padding: '3px 6px' }}
                      value={name === 'edit' ? form.permEdit : name === 'bash' ? form.permBash : form.permWebfetch}
                      onChange={e => updatePerm(name === 'edit' ? 'permEdit' : name === 'bash' ? 'permBash' : 'permWebfetch', e.target.value)}
                    >
                      {PERM_OPTIONS.map(o => <option key={o}>{o}</option>)}
                    </select>
                  </span>
                ))}
              </div>
              <label style={{ marginTop: 8 }}>
                Skills <span className="tag">probabilistic</span>
              </label>
              <input
                className="input"
                type="text"
                placeholder="fastapi, tdd (comma-sep)"
                value={form.skills}
                onChange={e => setForm(prev => ({ ...prev, skills: e.target.value }))}
              />
            </div>
          </div>
          <label>
            Context / system prompt <span className="tag">probabilistic &middot; soft</span>
          </label>
          <textarea
            className="textarea"
            rows={3}
            placeholder="You build simple full-stack CRUD apps: FastAPI + minimal vanilla HTML/JS. Integer cents. Write pytest. Work autonomously."
            value={form.context}
            onChange={e => setForm(prev => ({ ...prev, context: e.target.value }))}
          />
          <div className="tiny muted" style={{ marginTop: 4 }}>
            Deterministic config (permission/engine/model) is frozen by the ratchet. Probabilistic config (context/skills) can be optimized.
          </div>
          <div className="row" style={{ gap: 6, marginTop: 8 }}>
            <button className="btn btn-primary btn-sm" onClick={createConfig} disabled={!form.name.trim()}>
              Create agent_config
            </button>
            <button className="btn btn-sm" onClick={() => setShowForm(false)}>
              Cancel
            </button>
          </div>
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && items !== null && items.length === 0 && (
        <div className="empty-state">
          <h3>No agent configs found</h3>
          <p>Create one using the form above or run the bootstrap script.</p>
        </div>
      )}

      {/* ── Config grid ── */}
      {items !== null && items.length > 0 && (
        <div className="grid2" id="agentGrid">
          {items.map(c => {
            /* Map stored fields to mockup-like display values.
               harness -> model (best approximation), domain -> context, pattern -> skills */
            const modelDisplay = c.harness || c.cli;
            const contextDisplay = c.domain || '';
            const skillsDisplay = c.pattern || '';

            const permEdit = (c.role === 'reviewer' || c.role === 'orchestrator') ? 'deny' : 'allow';
            const permBash = 'allow';
            return (
              <div key={c.agent_config_id} className="panel">
                <div className="row between">
                  <b>{c.agent_config_id}</b>
                  <span className="tag">{c.cli}</span>
                </div>
                <div className="kv" style={{ marginTop: 6 }}>
                  <div><b>model</b> {modelDisplay}</div>
                  <div><b>role</b> {c.role}</div>
                  <div className="divider" />
                  <div className="tiny" style={permStyle(permEdit)}>
                    <b style={{ color: 'var(--text-muted)', fontWeight: 500 }}>permission</b>
                    {' '}edit:{permEdit} &middot; bash:{permBash}{' '}
                    <span className="tag">deterministic</span>
                  </div>
                  {contextDisplay && (
                    <div className="tiny">
                      <b>context</b> {contextDisplay}{' '}
                      <span className="tag">probabilistic</span>
                    </div>
                  )}
                  {skillsDisplay && (
                    <div className="tiny">
                      <b>skills</b> {skillsDisplay}{' '}
                      <span className="tag">probabilistic</span>
                    </div>
                  )}
                </div>
                <button className="btn btn-sm" style={{ marginTop: 8 }}>
                  Edit
                </button>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
