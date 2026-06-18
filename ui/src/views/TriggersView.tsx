import { useState, useEffect } from 'react';

interface Trigger {
  trigger_id: string;
  name: string;
  trigger_type: string;
  project_id: string;
  session_id: string;
  agent_config_id: string;
  cron_expression: string | null;
  active: boolean;
  fire_count: number;
  last_fired_at: string | null;
  next_fire_at: string | null;
  sandboxed: boolean;
  job_type: string;
}

type ScheduleMode = 'cron' | 'interval' | 'once';
type SandboxMode = 'on' | 'off';
type RatchetMode = 'propose_only' | 'auto_keep';

export function TriggersView() {
  const [triggers, setTriggers] = useState<Trigger[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(false);
  const [newName, setNewName] = useState('');
  const [scheduleMode, setScheduleMode] = useState<ScheduleMode>('cron');
  const [newCron, setNewCron] = useState('0 2 * * *');
  const [newJobType, setNewJobType] = useState('ratchet_sweep');
  const [sandboxMode, setSandboxMode] = useState<SandboxMode>('on');
  const [ratchetMode, setRatchetMode] = useState<RatchetMode>('propose_only');
  const [storedIntent, setStoredIntent] = useState('');

  const load = () => {
    setLoading(true);
    setError(null);
    fetch('/api/triggers')
      .then(r => r.json())
      .then(data => setTriggers(Array.isArray(data) ? data : data.triggers || []))
      .catch(err => setError(err.message))
      .finally(() => setLoading(false));
  };

  useEffect(() => { load(); }, []);

  const toggleActive = async (t: Trigger) => {
    await fetch(`/api/triggers/${t.trigger_id}`, {
      method: 'PATCH',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ active: !t.active }),
    });
    setTriggers(prev => prev.map(tr =>
      tr.trigger_id === t.trigger_id ? { ...tr, active: !tr.active } : tr
    ));
  };

  const createTrigger = async () => {
    if (!newName.trim() || !newCron.trim()) return;
    await fetch('/api/triggers', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        name: newName.trim(),
        cron_expression: newCron.trim(),
        job_type: newJobType,
        sandboxed: sandboxMode === 'on',
        ratchet_mode: ratchetMode,
        stored_intent: storedIntent.trim() || undefined,
      }),
    });
    setNewName('');
    setNewCron('0 2 * * *');
    setScheduleMode('cron');
    setNewJobType('ratchet_sweep');
    setSandboxMode('on');
    setRatchetMode('propose_only');
    setStoredIntent('');
    setShowForm(false);
    load();
  };

  if (loading && triggers.length === 0) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Triggers</h2>
            <div className="subtitle">Cron continuous learning. Gated: sandboxed, budget-capped, propose-only ratchet, global apply needs approval.</div>
          </div>
        </div>
        <div className="empty-state"><h3>Loading triggers…</h3></div>
      </div>
    );
  }

  if (error) {
    return (
      <div>
        <div className="page-header">
          <div className="page-header-titles">
            <h2>Triggers</h2>
            <div className="subtitle">Cron continuous learning. Gated: sandboxed, budget-capped, propose-only ratchet, global apply needs approval.</div>
          </div>
        </div>
        <div className="empty-state">
          <h3>Failed to load triggers</h3>
          <p className="error">{error}</p>
        </div>
      </div>
    );
  }

  return (
    <div>
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Triggers</h2>
          <div className="subtitle">Cron continuous learning. Gated: sandboxed, budget-capped, propose-only ratchet, global apply needs approval.</div>
        </div>
        <div className="page-header-actions">
          <button className="btn btn-primary btn-sm" onClick={() => setShowForm(!showForm)}>
            {showForm ? 'Cancel' : '+ Add trigger'}
          </button>
        </div>
      </div>

      <div className="panel">
        {showForm && (
          <div className="editor">
            <b className="small">+ Add trigger</b>
            <div className="grid2">
              <div>
                <label>Name</label>
                <input className="input" type="text" value={newName}
                  onChange={e => setNewName(e.target.value)} placeholder="e.g. nightly-regression" />
                <label>Schedule</label>
                <div className="seg">
                  <button className={scheduleMode === 'cron' ? 'on' : ''} onClick={() => setScheduleMode('cron')}>cron</button>
                  <button className={scheduleMode === 'interval' ? 'on' : ''} onClick={() => setScheduleMode('interval')}>interval</button>
                  <button className={scheduleMode === 'once' ? 'on' : ''} onClick={() => setScheduleMode('once')}>one-time</button>
                </div>
                <input className="input mono" type="text" value={newCron}
                  onChange={e => setNewCron(e.target.value)} style={{ marginTop: 6 }} />
                <div className="text-xs text-muted">cron expr — e.g. <span className="mono">0 2 * * *</span> = daily 02:00</div>
              </div>
              <div>
                <label>Job type</label>
                <select className="select" value={newJobType} onChange={e => setNewJobType(e.target.value)}>
                  <option value="ratchet_sweep">ratchet_sweep — detect weak config → experiment</option>
                  <option value="enrich">enrich — run scenario intents to grow score history</option>
                  <option value="run_task">run_task — run a stored intent</option>
                </select>
                <label>Sandboxed (experiment worktree)</label>
                <div className="seg">
                  <button className={sandboxMode === 'on' ? 'on' : ''} onClick={() => setSandboxMode('on')}>on (recommended)</button>
                  <button className={sandboxMode === 'off' ? 'on' : ''} onClick={() => setSandboxMode('off')}>off</button>
                </div>
                <label>Ratchet apply mode</label>
                <div className="seg">
                  <button className={ratchetMode === 'propose_only' ? 'on' : ''} onClick={() => setRatchetMode('propose_only')}>propose only</button>
                  <button className={ratchetMode === 'auto_keep' ? 'on' : ''} onClick={() => setRatchetMode('auto_keep')}>auto-keep (non-global)</button>
                </div>
              </div>
            </div>
            <label>Stored intent (for run_task) — optional</label>
            <textarea className="textarea" rows={2} value={storedIntent}
              onChange={e => setStoredIntent(e.target.value)}
              placeholder="e.g. Run the finance-tracker golden set and report regressions." />
            <div className="banner">🛡 Inherits guardrails: budget 50k tok/day · global mutations always queued for approval · golden-set pass required to keep</div>
            <div className="row" style={{ gap: 6, marginTop: 8 }}>
              <button className="btn btn-primary btn-tiny" onClick={createTrigger}
                disabled={!newName.trim() || !newCron.trim()}>Create trigger</button>
              <button className="btn btn-tiny" onClick={() => setShowForm(false)}>Cancel</button>
            </div>
          </div>
        )}

        <div className="row between" style={{ marginBottom: 8 }}>
          <b className="small">Scheduled tasks</b>
        </div>

        {triggers.length === 0 ? (
          <div className="empty-state"><h3>No triggers configured</h3></div>
        ) : (
          <table className="table">
            <thead>
              <tr>
                <th>Name</th>
                <th>Cron</th>
                <th>Job</th>
                <th>Sandbox</th>
                <th>Enabled</th>
              </tr>
            </thead>
            <tbody>
              {triggers.map(t => (
                <tr key={t.trigger_id}>
                  <td><span className="text-code">{t.name}</span></td>
                  <td className="mono">{t.cron_expression || '-'}</td>
                  <td>{t.job_type}</td>
                  <td>{t.sandboxed ? '✓' : '-'}</td>
                  <td>
                    <button className={`btn btn-tiny ${t.active ? 'btn-primary' : ''}`}
                      onClick={() => toggleActive(t)}>
                      {t.active ? 'on' : 'off'}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        <div className="banner" style={{ marginTop: 10 }}>
          🛡 Guardrails: budget 50k tok/day · unattended runs sandboxed · global mutations queued · golden-set pass required to keep
        </div>
      </div>
    </div>
  );
}
