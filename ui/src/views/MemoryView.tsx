import { useState, useEffect } from 'react';

interface MemoryEntry {
  memory_id?: string;
  scope: string;
  content: string;
  used_count?: number;
  created_at?: string;
}

export function MemoryView() {
  const [projectId, setProjectId] = useState('');
  const [entries, setEntries] = useState<MemoryEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [newScope, setNewScope] = useState('global');
  const [newScopeTarget, setNewScopeTarget] = useState('');
  const [newContent, setNewContent] = useState('');
  const [adding, setAdding] = useState(false);

  const load = async () => {
    if (!projectId.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const res = await fetch(`/api/memory?project_id=${encodeURIComponent(projectId.trim())}`);
      if (!res.ok) throw new Error(`Failed to load memory (${res.status})`);
      const data = await res.json();
      setEntries(Array.isArray(data) ? data : []);
      setLoaded(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Failed to load memory entries');
      setEntries([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (projectId.trim()) load();
  }, []);

  const addEntry = async () => {
    if (!newContent.trim() || !projectId.trim()) return;
    setAdding(true);
    try {
      const body: Record<string, string> = {
        project_id: projectId.trim(),
        content: newContent.trim(),
        scope: newScope,
      };
      if (newScope !== 'global' && newScopeTarget) {
        body.scope_target = newScopeTarget;
      }
      await fetch('/api/memory', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      setNewContent('');
      setShowForm(false);
      load();
    } catch {
      // silently fail
    } finally {
      setAdding(false);
    }
  };

  const promoteEntry = async (entry: MemoryEntry) => {
    if (!entry.memory_id) return;
    await fetch(`/api/memory/${entry.memory_id}/promote`, { method: 'POST' });
    load();
  };

  const handleScopeChange = (e: React.ChangeEvent<HTMLSelectElement>) => {
    setNewScope(e.target.value);
    if (e.target.value === 'global') {
      setNewScopeTarget('');
    }
  };

  const scopeLabel = (scope: string): string => {
    return scope;
  };

  return (
    <div>
      <style>{`
        .pill-info {
          background: var(--accent-soft);
          color: var(--accent-text);
          border-color: var(--accent-border);
        }
      `}</style>

      <div className="page-header">
        <div className="page-header-titles">
          <h2>Memory</h2>
          <div className="subtitle">Scoped hierarchy assembled into the worktree at spawn. Promote session → project → global.</div>
        </div>
      </div>

      {/* Project selector */}
      <div className="card" style={{ marginBottom: 12 }}>
        <div className="row">
          <div className="field" style={{ flex: 1, marginBottom: 0 }}>
            <label>Project ID</label>
            <input
              className="input"
              value={projectId}
              onChange={e => setProjectId(e.target.value)}
              placeholder="e.g. finance-tracker"
              onKeyDown={e => e.key === 'Enter' && load()}
            />
          </div>
          <button
            className="btn btn-primary btn-sm"
            onClick={load}
            disabled={!projectId.trim() || loading}
            style={{ marginTop: 18 }}
          >
            {loading ? '\u2026' : 'Load'}
          </button>
        </div>
      </div>

      {/* Loading state */}
      {loading && !loaded && (
        <div className="empty-state"><h3>Loading&hellip;</h3></div>
      )}

      {/* Error state */}
      {error && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="error">{error}</div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn btn-sm" onClick={load}>Retry</button>
          </div>
        </div>
      )}

      {/* Main memory panel */}
      {loaded && !error && (
        <div className="panel">
          <div className="row between" style={{ marginBottom: 8 }}>
            <b className="small">Entries</b>
            <button className="btn btn-primary btn-tiny" onClick={() => setShowForm(!showForm)}>
              ＋ Add
            </button>
          </div>

          {/* Add form */}
          {showForm && (
            <div className="editor" style={{ marginBottom: 12 }}>
              <b className="small">＋ Add memory entry</b>
              <label>Scope</label>
              <select className="select" value={newScope} onChange={handleScopeChange}>
                <option value="global">global (all projects)</option>
                <option value="project">project</option>
                <option value="agent_config">agent_config (role)</option>
                <option value="session">session</option>
              </select>

              {newScope !== 'global' && (
                <>
                  <label>Scope target</label>
                  <select className="select" value={newScopeTarget} onChange={e => setNewScopeTarget(e.target.value)}>
                    <option value="">Select target&hellip;</option>
                    <option value="finance-tracker">finance-tracker</option>
                    <option value="backend-api">backend-api</option>
                    <option value="auth-service">auth-service</option>
                  </select>
                </>
              )}

              <label>Content</label>
              <textarea
                className="textarea"
                rows={2}
                value={newContent}
                onChange={e => setNewContent(e.target.value)}
                placeholder="e.g. Always use integer cents for money; never floats."
              />

              <div className="tiny muted" style={{ marginTop: 4 }}>
                Assembled into the worktree at spawn (least-specific &rarr; most-specific): global &rarr; project &rarr; agent_config &rarr; session.
              </div>

              <div className="row" style={{ gap: 6, marginTop: 8 }}>
                <button className="btn btn-primary btn-tiny" onClick={addEntry} disabled={!newContent.trim() || adding}>
                  {adding ? 'Adding\u2026' : 'Add entry'}
                </button>
                <button className="btn btn-tiny" onClick={() => { setShowForm(false); setNewContent(''); }}>
                  Cancel
                </button>
              </div>
            </div>
          )}

          {/* Entries table */}
          <table className="table">
            <thead>
              <tr>
                <th style={{ width: '20%' }}>Scope</th>
                <th>Content</th>
                <th style={{ width: '12%' }}>Used</th>
                <th style={{ width: '18%' }}></th>
              </tr>
            </thead>
            <tbody>
              {entries.length === 0 ? (
                <tr>
                  <td colSpan={4} style={{ textAlign: 'center', padding: 24, color: 'var(--text-muted)' }}>
                    No memory entries for project &ldquo;{projectId}&rdquo;
                  </td>
                </tr>
              ) : (
                entries.map(e => (
                  <tr key={e.memory_id || Math.random()}>
                    <td>
                      <span className={`pill ${e.scope === 'global' ? 'pill-info' : 'pill-queued'}`}>
                        {scopeLabel(e.scope)}
                      </span>
                    </td>
                    <td className="text-sm">{e.content}</td>
                    <td className="text-xs text-muted">{e.used_count ?? 0}&times;</td>
                    <td>
                      <div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                        {e.scope === 'project' && (
                          <button className="btn btn-tiny" onClick={() => promoteEntry(e)} title="Promote to global">
                            &uarr; global
                          </button>
                        )}
                        {e.scope === 'session' && (
                          <button className="btn btn-tiny" onClick={() => promoteEntry(e)} title="Promote to project">
                            &uarr; project
                          </button>
                        )}
                        {e.scope === 'agent_config' && (
                          <button className="btn btn-tiny" onClick={() => promoteEntry(e)} title="Promote to global">
                            &uarr; global
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      )}

      {/* Initial state — no project loaded */}
      {!loaded && !loading && !error && (
        <div className="empty-state">
          <h3>Enter a project ID</h3>
          <p>Load memory entries by providing a project identifier above.</p>
        </div>
      )}
    </div>
  );
}
