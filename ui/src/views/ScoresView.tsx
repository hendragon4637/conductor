import { useState, useEffect } from 'react';
import { api } from '../api';
import type { ScoreRow, ScoreTrend } from '../api';

export function ScoresView() {
  const [rows, setRows] = useState<ScoreRow[]>([]);
  const [trends, setTrends] = useState<ScoreTrend[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      api.listScores(),
      api.listTrends(),
    ])
      .then(([scoresRes, trendsRes]) => {
        setRows(scoresRes.rows || []);
        setTrends(trendsRes.trends || []);
      })
      .catch((err: Error) => setError(err.message))
      .finally(() => setLoading(false));
  }, []);

  /* ── Loading ── */
  if (loading) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Scores</h2>
            <div className="subtitle">Goal-review scores per agent_config (from Langfuse). Includes a conductor-self row (co-evolution).</div>
          </div>
        </div>
        <div className="empty-state"><h3>Loading scores…</h3></div>
      </div>
    );
  }

  /* ── Error ── */
  if (error && rows.length === 0 && trends.length === 0) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Scores</h2>
            <div className="subtitle">Goal-review scores per agent_config (from Langfuse). Includes a conductor-self row (co-evolution).</div>
          </div>
        </div>
        <div className="error">{error}</div>
      </div>
    );
  }

  /* Compute deltas from trend data (first → last) */
  const computeDelta = (): number | null => {
    if (trends.length < 2) return null;
    const first = trends[0].average;
    const last = trends[trends.length - 1].average;
    return last - first;
  };

  /* Build SVG polyline points from trends */
  const trendPoints = (): string => {
    if (trends.length === 0) return '';
    const w = 320;
    const baselineY = 64;
    const maxVal = Math.max(...trends.map(t => t.average), 1);
    const values = trends.map(t => t.average);
    const minVal = Math.min(...values);
    const range = Math.max(maxVal - minVal, 0.1);

    return values
      .map((v, i) => {
        const x = (i / Math.max(values.length - 1, 1)) * w;
        const y = baselineY - ((v - minVal) / range) * 40;
        return `${x},${y}`;
      })
      .join(' ');
  };

  /* Pick the first score row for trend chart title / caption */
  const primaryScore = rows[0];

  /* Format a delta as a signed percentage string */
  const fmtDelta = (d: number | null): string | null => {
    if (d === null) return null;
    const sign = d >= 0 ? '+' : '';
    return `${sign}${d.toFixed(2)}`;
  };

  return (
    <div>
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Scores</h2>
          <div className="subtitle">Goal-review scores per agent_config (from Langfuse). Includes a conductor-self row (co-evolution).</div>
        </div>
      </div>

      {/* ── Empty state ── */}
      {rows.length === 0 && trends.length === 0 && (
        <div className="empty-state">
          <h3>No scores yet</h3>
          <p>Run a review to see scores from Langfuse evaluations.</p>
        </div>
      )}

      {/* ── Metric cards ── */}
      {rows.length > 0 && (
        <div className="grid3" style={{ marginBottom: 12 }}>
          {rows.slice(0, 3).map(r => {
            const delta = computeDelta();
            const isUp = delta !== null && delta >= 0;
            return (
              <div key={r.agent_config} className="metric">
                <div className="muted small">{r.agent_config}</div>
                <div className="value">
                  {r.average_score.toFixed(2)}
                  {delta !== null && (
                    <span className={isUp ? 'up' : 'down'} style={{ marginLeft: 6 }}>
                      {isUp ? '\u25B2' : '\u25BC'} {fmtDelta(delta)}
                    </span>
                  )}
                </div>
                <div className="tiny muted">goal review &middot; 30d</div>
              </div>
            );
          })}
        </div>
      )}

      {/* ── SVG trend chart ── */}
      {trends.length > 0 && primaryScore && (
        <div className="panel">
          <b className="small">{primaryScore.agent_config} &middot; task_completion (30d)</b>
          <svg viewBox="0 0 320 70" width="100%" height="70" style={{ marginTop: 6 }}>
            <polyline
              points={trendPoints()}
              fill="none"
              stroke="var(--status-success)"
              strokeWidth="2"
            />
            <line x1="0" y1="64" x2="320" y2="64" stroke="var(--border-subtle)" />
          </svg>
          <div className="tiny muted" style={{ marginTop: 2 }}>
            {trends.length >= 2
              ? trends[0].average.toFixed(2) + ' \u2192 ' + trends[trends.length - 1].average.toFixed(2) + ' after ' + (trends.length - 1) + ' data points'
              : 'Trend data collected'}
            &middot; last: goal_review={primaryScore.average_score.toFixed(2)}, passed=true
          </div>
        </div>
      )}

      {/* ── Scores table ── */}
      {rows.length > 0 && (
        <table className="table" style={{ marginTop: 16 }}>
          <thead>
            <tr>
              <th>Agent Config</th>
              <th>Avg Score</th>
              <th>Traces</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(r => (
              <tr key={r.agent_config}>
                <td><span className="text-code">{r.agent_config}</span></td>
                <td className="mono">{(r.average_score * 100).toFixed(1)}%</td>
                <td>{r.trace_count}</td>
                <td>
                  <span className={r.status === 'healthy' ? 'badge badge-success' : 'badge badge-failed'}>
                    {r.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}
