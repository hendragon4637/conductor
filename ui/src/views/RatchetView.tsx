import { useState, useEffect } from 'react';

interface Experiment {
  experiment_id: string;
  target: string;
  agent_config: string;
  baseline_score: number;
  candidate_score: number;
  delta: number;
  decision: string;
  created_at: string | null;
}

interface Approval {
  mutation_id: string;
  agent_config: string;
  target: string;
  delta: number;
  rationale: string;
  experiment_id: string;
  created_at: string | null;
}

export function RatchetView() {
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  const load = () => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetch('/api/ratchet/experiments').then(r => r.json()),
      fetch('/api/ratchet/approvals').then(r => r.json()),
    ])
      .then(([expData, appData]) => {
        setExperiments(expData.rows || []);
        setApprovals(appData.rows || []);
      })
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const runExperiment = async () => {
    setRunning(true);
    try {
      await fetch('/api/ratchet/run', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ propose_only: true, min_runs: 1 }),
      });
      load();
    } catch {
    } finally {
      setRunning(false);
    }
  };

  const approve = async (mutationId: string) => {
    await fetch(`/api/ratchet/approvals/${mutationId}/approve`, { method: 'POST' });
    load();
  };

  const reject = async (mutationId: string) => {
    await fetch(`/api/ratchet/approvals/${mutationId}/reject`, { method: 'POST' });
    load();
  };

  const decisionPill = (decision: string) => {
    if (decision === 'running') return 'pill pill-run';
    if (decision === 'kept') return 'pill pill-done';
    if (decision === 'reverted') return 'pill pill-fail';
    return 'pill pill-queued';
  };

  if (loading && experiments.length === 0 && approvals.length === 0) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Ratchet</h2>
            <div className="subtitle">Baseline vs candidate on a golden set. Global wins are queued for approval. Permissions are never mutated.</div>
          </div>
        </div>
        <div className="empty-state"><h3>Loading…</h3></div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Ratchet</h2>
            <div className="subtitle">Baseline vs candidate on a golden set. Global wins are queued for approval. Permissions are never mutated.</div>
          </div>
        </div>
        <div className="empty-state">
          <h3>Failed to load ratchet data</h3>
          <p className="error">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Ratchet</h2>
          <div className="subtitle">Baseline vs candidate on a golden set. Global wins are queued for approval. Permissions are never mutated.</div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary btn-sm" onClick={runExperiment} disabled={running}>
            {running ? 'Running…' : 'Run experiment'}
          </button>
        </div>
      </div>

      <div className="panel">
        <div className="row between" style={{ marginBottom: 8 }}>
          <b className="small">Experiments</b>
        </div>

        {experiments.length === 0 ? (
          <div className="empty-state"><h3>No experiments yet</h3><p>Run an experiment to compare baselines vs candidates.</p></div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Target</th>
                <th>agent_config</th>
                <th>Baseline→Candidate</th>
                <th>Decision</th>
              </tr>
            </thead>
            <tbody>
              {experiments.map(e => (
                <tr key={e.experiment_id}>
                  <td>{e.target || 'skill'}</td>
                  <td><span className="text-code">{e.agent_config}</span></td>
                  <td>
                    {e.decision === 'running' ? (
                      'running on golden set…'
                    ) : (
                      <span>{(e.baseline_score * 100).toFixed(0)}% → {(e.candidate_score * 100).toFixed(0)}%</span>
                    )}
                  </td>
                  <td>
                    <span className={decisionPill(e.decision)}>{e.decision}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      <div className="panel">
        <b className="small">Pending approvals (global-scope mutations)</b>
        {approvals.length === 0 ? (
          <div className="empty-state" style={{ minHeight: 120, padding: 'var(--space-5) var(--space-4)' }}>
            <p className="text-sm text-muted">No pending approvals</p>
          </div>
        ) : (
          <table className="table" style={{ marginTop: 8 }}>
            <thead>
              <tr>
                <th>agent_config</th>
                <th>Target</th>
                <th>Δ</th>
                <th></th>
              </tr>
            </thead>
            <tbody>
              {approvals.map(a => (
                <tr key={a.mutation_id}>
                  <td><span className="text-code">{a.agent_config}</span></td>
                  <td>{a.target || a.rationale}</td>
                  <td style={{ color: 'var(--status-success)' }}>+{a.delta != null ? (a.delta * 100).toFixed(0) : '?'}%</td>
                  <td>
                    <div className="row" style={{ gap: 4 }}>
                      <button className="btn btn-tiny btn-primary" onClick={() => approve(a.mutation_id)}>approve</button>
                      <button className="btn btn-tiny" style={{ color: 'var(--status-failed)' }} onClick={() => reject(a.mutation_id)}>reject</button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}
