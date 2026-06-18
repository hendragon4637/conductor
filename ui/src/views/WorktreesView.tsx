import { useState, useEffect } from 'react';

interface Worktree {
  path?: string;
  head?: string;
  branch?: string;
  bare?: boolean;
  kind?: string;
}

export function WorktreesView() {
  const [worktrees, setWorktrees] = useState<Worktree[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);

  const [projectId, setProjectId] = useState('');
  const [sourceBranch, setSourceBranch] = useState('main');
  const [newBranch, setNewBranch] = useState('');
  const [kind, setKind] = useState<'work' | 'experiment'>('work');

  const load = () => {
    setLoading(true);
    setError(null);
    fetch('/api/worktrees')
      .then(r => r.json())
      .then(d => setWorktrees(d.worktrees || []))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const create = async () => {
    if (!newBranch.trim()) return;
    const body: Record<string, string> = { branch: newBranch.trim(), kind };
    if (projectId.trim()) body.project_id = projectId.trim();
    if (sourceBranch.trim()) body.source_branch = sourceBranch.trim();
    await fetch('/api/worktrees', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(body),
    });
    setNewBranch('');
    setProjectId('');
    setSourceBranch('main');
    setKind('work');
    setShowForm(false);
    load();
  };

  const remove = async (path: string) => {
    await fetch(`/api/worktrees/${encodeURIComponent(path)}`, { method: 'DELETE' });
    load();
  };

  const autoPath = newBranch.trim()
    ? `${projectId || 'repo'}.${newBranch.trim().replace(/\//g, '-')}/`
    : '';

  if (loading && worktrees.length === 0) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Worktrees</h2>
            <div className="subtitle">git worktree per session. Project = repo, session = branch/worktree.</div>
          </div>
        </div>
        <div className="empty-state"><h3>Loading worktrees…</h3></div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Worktrees</h2>
            <div className="subtitle">git worktree per session. Project = repo, session = branch/worktree.</div>
          </div>
        </div>
        <div className="empty-state">
          <h3>Failed to load worktrees</h3>
          <p className="error">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Worktrees</h2>
          <div className="subtitle">git worktree per session. Project = repo, session = branch/worktree.</div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>
            {showForm ? 'Cancel' : '+ Add worktree'}
          </button>
        </div>
      </div>

      <div className="panel">
        {showForm && (
          <div className="editor">
            <b className="small">+ Add worktree</b>
            <label>Project (repo)</label>
            <input className="input" type="text" value={projectId}
              onChange={e => setProjectId(e.target.value)} placeholder="e.g. finance-tracker" />
            <label>Source branch (base the worktree off)</label>
            <input className="input" type="text" value={sourceBranch}
              onChange={e => setSourceBranch(e.target.value)} placeholder="main" />
            <label>New branch name</label>
            <input className="input" type="text" value={newBranch}
              onChange={e => setNewBranch(e.target.value)} placeholder="e.g. feat/csv-export" />
            <label>Kind</label>
            <div className="seg">
              <button className={kind === 'work' ? 'on' : ''} onClick={() => setKind('work')}>work</button>
              <button className={kind === 'experiment' ? 'on' : ''} onClick={() => setKind('experiment')}>experiment</button>
            </div>
            <label>Path (auto)</label>
            <input className="input mono" type="text" value={autoPath} readOnly />
            {autoPath && (
              <div className="text-xs text-muted" style={{ marginTop: 4 }}>
                Runs: <span className="mono">git -C {projectId || 'repo'} worktree add ../{autoPath.replace('/', '')} -b {newBranch}</span>
              </div>
            )}
            <div className="row" style={{ gap: 6, marginTop: 8 }}>
              <button className="btn btn-primary btn-tiny" onClick={create}
                disabled={!newBranch.trim()}>Create worktree</button>
              <button className="btn btn-tiny" onClick={() => setShowForm(false)}>Cancel</button>
            </div>
          </div>
        )}

        <div className="row between" style={{ marginBottom: 8 }}>
          <b className="small">Worktrees</b>
        </div>

        {worktrees.length === 0 ? (
          <div className="empty-state"><h3>No worktrees</h3><p>Create a worktree above to start working on a new branch.</p></div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Path</th>
                <th>Branch</th>
                <th>Kind</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {worktrees.map((wt, i) => {
                const kindLabel = wt.kind || (wt.branch?.startsWith('exp/') ? 'experiment' : 'work');
                return (
                  <tr key={i}>
                    <td className="mono" style={{ maxWidth: 300, overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {wt.path || '(bare)'}
                    </td>
                    <td className="mono">{wt.branch ? wt.branch.replace('refs/heads/', '') : '-'}</td>
                    <td>{kindLabel}</td>
                    <td>
                      {wt.path && (
                        <button className="btn btn-tiny" style={{ color: 'var(--status-failed)' }}
                          onClick={() => remove(wt.path!)} title="Remove worktree">remove</button>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
