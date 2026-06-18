import { useState, useEffect, type ReactNode } from 'react';
import type { Settings } from '../api';

interface ServiceRow {
  name: string;
  dot: 'g' | 'y' | 'r';
  statusText: string | ReactNode;
  action?: { label: string; onClick: () => void };
}

export function SettingsView() {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [brainModel, setBrainModel] = useState('frontier: claude (API)');
  const [toastMsg, setToastMsg] = useState<string | null>(null);

  const showToast = (msg: string) => {
    setToastMsg(msg);
    setTimeout(() => setToastMsg(null), 1600);
  };

  const load = () => {
    setLoading(true);
    setError(null);
    fetch('/api/settings')
      .then(r => {
        if (!r.ok) throw new Error(`Failed to load settings (${r.status})`);
        return r.json();
      })
      .then((data: Settings) => setSettings(data))
      .catch(e => setError(e instanceof Error ? e.message : 'Failed to load settings'))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const dotForStatus = (status: string): 'g' | 'y' | 'r' => {
    if (status === 'ok' || status === 'connected') return 'g';
    if (status === 'warning' || status === 'degraded') return 'y';
    return 'r';
  };

  const buildRows = (): ServiceRow[] => {
    const rows: ServiceRow[] = [];

    if (settings) {
      rows.push({
        name: 'AionUi',
        dot: dotForStatus(settings.aionui.status),
        statusText: settings.aionui.status === 'ok'
          ? `connected \u00b7 ${settings.aionui.url}`
          : `${settings.aionui.status} \u00b7 ${settings.aionui.url}`,
      });

      // Hermes and Active execution backend are always shown (static config)
      rows.push({
        name: 'Hermes Agent',
        dot: 'g',
        statusText: 'connected \u00b7 localhost:8642/v1 \u00b7 run_events_sse \u2713',
      });

      rows.push({
        name: 'Active execution backend',
        dot: 'g',
        statusText: (
          <>
            <select style={{ width: 'auto', padding: '3px 6px' }}>
              <option>Hermes (self-routing)</option>
              <option>AionUi (team mode)</option>
            </select>
            {' \u00b7 '}per-plan override allowed
          </>
        ),
        action: { label: 'save', onClick: () => showToast('Backend default saved') },
      });

      rows.push({
        name: 'Langfuse',
        dot: dotForStatus(settings.langfuse.status),
        statusText: settings.langfuse.status === 'ok'
          ? `connected \u00b7 ${settings.langfuse.url}`
          : `${settings.langfuse.status} \u00b7 ${settings.langfuse.url}`,
      });

      rows.push({
        name: 'Plan brain model',
        dot: 'g',
        statusText: (
          <>
            primary:{' '}
            <select
              style={{ width: 'auto', padding: '3px 6px' }}
              value={brainModel}
              onChange={e => setBrainModel(e.target.value)}
            >
              <option>frontier: claude (API)</option>
              <option>frontier: deepseek-v4-pro (API)</option>
              <option>frontier: gpt (API)</option>
            </select>
            {' \u00b7 '}fallback: local-ovms/qwen3-8b
          </>
        ),
        action: { label: 'save', onClick: () => showToast('Brain model policy saved') },
      });

      rows.push({
        name: 'Chat model (OVMS local)',
        dot: dotForStatus(settings.brain.status),
        statusText: settings.brain.status === 'ok'
          ? `${settings.brain.model} \u00b7 GPU \u00b7 ${settings.brain.url}`
          : `${settings.brain.status} \u00b7 ${settings.brain.url}`,
        action: { label: 'test', onClick: () => {} },
      });

      rows.push({
        name: 'Provider split',
        dot: 'g',
        statusText: 'Brain/evaluator: OpenRouter/NIM free or local OVMS (programmatic API). Execution: OpenCode/ChatGPT via OAuth (subscription).',
      });
    }

    rows.push({
      name: 'CLI adapters',
      dot: 'g',
      statusText: 'opencode \u00b7 claude-code \u00b7 codex \u00b7 gemini \u00b7 hermes',
    });

    rows.push({
      name: 'Remote access',
      dot: 'y',
      statusText: 'Tailscale \u00b7 auth token required',
    });

    rows.push({
      name: 'Budget cap',
      dot: 'g',
      statusText: 'experiments 50k tok/day',
      action: { label: 'edit', onClick: () => {} },
    });

    return rows;
  };

  return (
    <div>
      <style>{`
        .dot {
          width: 8px;
          height: 8px;
          border-radius: 50%;
          display: inline-block;
          margin-right: 5px;
          flex-shrink: 0;
        }
        .dot.g { background: var(--status-success); }
        .dot.y { background: var(--status-running); }
        .dot.r { background: var(--status-failed); }
      `}</style>

      <div className="page-header">
        <div className="page-header-titles">
          <h2>Settings</h2>
          <div className="subtitle">Connections, CLI adapters, chat model, remote access, budgets.</div>
        </div>
      </div>

      {/* Loading state */}
      {loading && (
        <div className="empty-state"><h3>Loading&hellip;</h3></div>
      )}

      {/* Error state */}
      {!loading && error && (
        <div className="card" style={{ marginBottom: 12 }}>
          <div className="error">{error}</div>
          <div className="row" style={{ marginTop: 8 }}>
            <button className="btn btn-sm" onClick={load}>Retry</button>
          </div>
        </div>
      )}

      {/* Settings table */}
      {!loading && !error && (
        <div className="panel">
          <table className="table" data-testid="settings-table">
            <tbody>
              {buildRows().map((row, idx) => (
                <tr key={idx} data-testid={`settings-row-${row.name}`}>
                  <td style={{ width: '30%' }}>
                    <span className={`dot ${row.dot}`}></span>
                    <span className="text-sm">{row.name}</span>
                  </td>
                  <td className="text-sm text-muted">{row.statusText}</td>
                  <td style={{ width: '15%', textAlign: 'right' }}>
                    {row.action && (
                      <button className="btn btn-tiny" onClick={row.action.onClick} data-testid="settings-row-action">
                        {row.action.label}
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Toast notification */}
      {toastMsg && (
        <div style={{
          position: 'fixed',
          right: '16px',
          bottom: '16px',
          background: 'var(--text-primary)',
          color: 'var(--bg-base)',
          padding: '9px 14px',
          borderRadius: '9px',
          fontSize: '13px',
          zIndex: 50,
          pointerEvents: 'none',
          transition: 'opacity 0.25s',
        }}>
          {toastMsg}
        </div>
      )}
    </div>
  );
}
