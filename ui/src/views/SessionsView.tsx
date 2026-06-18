import { useState, useEffect } from 'react';
import type { Session } from '../api';

const AIONUI_BASE = 'http://localhost:8377';

// ── Backend helpers ──
const BACKEND_LABELS: Record<string, string> = {
  hermes: 'Hermes',
  opencode_omo: 'OpenCode+OMO',
  opencode: 'OpenCode',
  'claude-code': 'Claude Code',
  codex: 'Codex',
  gemini: 'Gemini',
  aionui: 'AionUi',
};
const BACKEND_CLASSES: Record<string, 'a' | 'b' | 'team'> = {
  hermes: 'a',
  opencode_omo: 'a',
  opencode: 'b',
  'claude-code': 'b',
  codex: 'b',
  gemini: 'b',
  aionui: 'team',
};
function backendLabel(b: string | undefined): string {
  return BACKEND_LABELS[b || 'aionui'] || b || 'AionUi';
}
function backendClassLabel(b: string | undefined): string {
  const cls = BACKEND_CLASSES[b || 'aionui'] || 'b';
  if (cls === 'a') return 'self-routing (no orchestrator)';
  if (cls === 'b') return 'orchestrator + N members';
  return 'team';
}

function aionUiUrl(s: Session) {
  if (s.aionui_team_id) {
    return `${AIONUI_BASE}/#/team/${s.aionui_team_id}`;
  }
  return '';
}

function pillClass(verdict: string | undefined): string {
  switch (verdict) {
    case 'to do': return 'pill pill-queued';
    case 'running': return 'pill pill-run';
    case 'done': return 'pill pill-done';
    case 'stalled':
    case 'queued': return 'pill pill-queued';
    case 'quota':
    case 'crashed':
    case 'fail': return 'pill pill-fail';
    default: return 'pill pill-queued';
  }
}

function pillLabel(verdict: string | undefined, score?: number | null): string {
  if (verdict === 'done' && score != null) {
    return `done \u00b7 ${score.toFixed(2)}`;
  }
  return verdict || 'unknown';
}

function formatLastActivity(s: Session): string {
  const secs = s.last_activity_s;
  if (secs == null) return '\u2014';
  if (secs < 60) return `${Math.round(secs)}s ago`;
  if (secs < 3600) return `${Math.round(secs / 60)}m ago`;
  return `${Math.round(secs / 3600)}h ago`;
}

function formatHealth(s: Session): string {
  const time = formatLastActivity(s);
  const rate = s.token_rate != null ? `${Math.round(s.token_rate)} tok/s` : '\u2014';
  return `${time} \u00b7 ${rate}`;
}

function healthColor(s: Session): string {
  switch (s.watcher_verdict) {
    case 'stalled': return 'var(--status-running)';
    case 'quota':
    case 'crashed': return 'var(--status-failed)';
    default: return 'var(--text-muted)';
  }
}

export function SessionsView() {
  const [sessions, setSessions] = useState<Session[]>([]);
  const [loading, setLoading] = useState(true);

  const load = () => {
    setLoading(true);
    fetch('/api/sessions')
      .then(r => r.json())
      .then(data => setSessions(Array.isArray(data) ? data : []))
      .catch(() => setSessions([]))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);
  useEffect(() => { const id = setInterval(load, 15000); return () => clearInterval(id); }, []);

  const cancelSession = async (sessionId: string) => {
    await fetch(`/api/sessions/${sessionId}/cancel`, { method: 'POST' });
    load();
  };

  const pauseSession = async (sessionId: string) => {
    await fetch(`/api/sessions/${sessionId}/pause`, { method: 'POST' });
    load();
  };

  const resumeSession = async (sessionId: string) => {
    await fetch(`/api/sessions/${sessionId}/resume`, { method: 'POST' });
    load();
  };

  return (
    <div>
      <style>{`
        .hoverurl {
          position: relative;
          display: inline-block;
        }
        .hoverurl .url {
          position: absolute;
          bottom: calc(100% + 6px);
          left: 50%;
          transform: translateX(-50%);
          background: #1A1A1A;
          border: 1px solid #262626;
          border-radius: 4px;
          padding: 4px 8px;
          font-size: 11px;
          font-family: var(--font-mono);
          color: #A0A0A0;
          white-space: nowrap;
          pointer-events: none;
          opacity: 0;
          transition: opacity 150ms ease-out;
          z-index: 10;
        }
        .hoverurl:hover .url {
          opacity: 1;
        }
        .btn-danger {
          color: #F46666;
          border-color: rgba(244, 102, 102, 0.35);
        }
        .btn-danger:hover:not(:disabled) {
          background: rgba(244, 102, 102, 0.15);
          border-color: rgba(244, 102, 102, 0.5);
          color: #F46666;
        }
      `}</style>

      <div className="page-header">
        <div className="page-header-titles">
          <h2>Sessions</h2>
          <div className="subtitle">Conductor's out-of-band watcher tracks each session from ground-truth signals (token-rate, last-activity, fs/git, pid) {'\u2014'} not AionUi self-report. Verdict drives pause / resume / cancel; resume re-enters at the last incomplete node in the same worktree.</div>
        </div>
      </div>

      <div className="banner">{'\uD83E\uDE7A'} Watcher: 1 deterministic supervisor loop {'\u00b7'} poll 45s {'\u00b7'} verdicts: running / stalled / quota / crashed / done</div>

      {loading ? (
        <div className="empty-state"><h3>Loading sessions{'\u2026'}</h3></div>
      ) : sessions.length === 0 ? (
        <div className="empty-state"><h3>No active sessions</h3><p>Sessions appear here when agents are working.</p></div>
      ) : (
        <div className="panel" style={{ padding: 0, overflow: 'hidden' }}>
          <table className="table" data-testid="session-table">
            <thead>
              <tr>
                <th>Session (worktree)</th>
                <th>Backend {'\u00b7'} node</th>
                <th>Watcher verdict</th>
                <th>Health (last act \u00b7 tok/s)</th>
                <th>Controls</th>
              </tr>
            </thead>
            <tbody>
              {sessions.map((s, idx) => {
                const isRunning = s.watcher_verdict === 'running';
                const isStalledOrQuota = s.watcher_verdict === 'stalled' || s.watcher_verdict === 'quota';
                const isDone = s.watcher_verdict === 'done';
                const canOpenAionUi = !!s.aionui_team_id;
                return (
                  <tr key={s.row_id || `${s.session_id}:${s.active_node_id || 'session'}`} data-testid={`session-row-${idx}`}>
                    <td>
                      <span className="text-code">{s.worktree_label || s.worktree_path || `${s.project_id}.${s.base_branch}`}</span>
                      {' '}<span className="tag" data-testid="session-backend-tag">{backendLabel(s.backend_type)}</span>
                    </td>
                    <td>
                      <span className="text-sm">
                        {s.plan_title || '\u2014'}
                        {s.active_node_title ? ` ${'\u00b7'} ${s.active_node_title}` : ''}
                      </span>
                      <br />
                      <span className="tiny muted" data-testid="session-class-label">{backendClassLabel(s.backend_type)}</span>
                    </td>
                    <td><span className={pillClass(s.watcher_verdict)} data-testid="session-verdict-badge">{pillLabel(s.watcher_verdict, s.score)}</span></td>
                    <td><span className="text-xs" style={{ color: healthColor(s) }} data-testid="session-health-text">{formatHealth(s)}</span></td>
                    <td>
                      <div className="row" style={{ gap: 4, justifyContent: 'flex-end' }}>
                        {!isDone && canOpenAionUi && (
                          <span className="hoverurl">
                            <a href={aionUiUrl(s)} target="_blank" className="btn btn-sm" title="Open in AionUi" data-testid="session-aionui-link">
                              AionUi {'\u2197'}
                            </a>
                            <span className="url">{aionUiUrl(s)}</span>
                          </span>
                        )}
                        {isRunning && (
                          <button className="btn btn-sm" onClick={() => pauseSession(s.session_id)} title="Pause session" data-testid="session-pause-btn">
                            {'\u23F8'} pause
                          </button>
                        )}
                        {isStalledOrQuota && (
                          <button className="btn btn-sm btn-primary" onClick={() => resumeSession(s.session_id)} title="Resume session" data-testid="session-resume-btn">
                            {'\u25B6'} resume
                          </button>
                        )}
                        {(isRunning || isStalledOrQuota) && (
                          <button className="btn btn-sm btn-danger" onClick={() => cancelSession(s.session_id)} title="Cancel session" data-testid="session-cancel-btn">
                            cancel
                          </button>
                        )}
                        {isDone && (
                          <button className="btn btn-sm" onClick={() => { window.location.hash = '#/scores'; }} data-testid="session-score-link">
                            view score
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          <div className="tiny muted" style={{ padding: '8px 12px', borderTop: '1px solid var(--border-subtle)' }}>
            Verdict legend: <span className="pill pill-run">running</span> active {'\u00b7'}{' '}
            <span className="pill pill-queued">stalled</span> no activity + 0 tok/s + no fs change {'\u00b7'}{' '}
            <span className="pill pill-fail">quota</span> silent usage-limit {'\u00b7'}{' '}
            <span className="pill pill-done">done</span> terminal marker + regression gate passed
          </div>
        </div>
      )}
    </div>
  );
}
