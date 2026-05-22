import { useEffect, useState } from 'react';
import { api } from '../api';
import type { AgentConfig } from '../api';

export function AgentConfigsList() {
  const [items, setItems] = useState<AgentConfig[] | null>(null);

  useEffect(() => {
    api.listConfigs().then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <div>
      <h2 className="text-xl" style={{ marginBottom: '4px' }}>Agent configs</h2>
      <p className="text-secondary text-sm" style={{ marginBottom: '20px' }}>
        Read-only — edit YAML in <code className="text-code">agent_configs/</code> and re-run bootstrap.
      </p>
      {items === null ? (
        <p className="text-muted">Loading…</p>
      ) : items.length === 0 ? (
        <div className="empty-state">
          <p>No agent configs found. Run the bootstrap script.</p>
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <table className="table">
            <thead>
              <tr>
                <th>id</th>
                <th>cli</th>
                <th>domain</th>
                <th>role</th>
                <th>pattern</th>
                <th>active</th>
              </tr>
            </thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.agent_config_id}>
                  <td><code className="text-code">{c.agent_config_id}</code></td>
                  <td>{c.cli}</td>
                  <td>{c.domain}</td>
                  <td>{c.role}</td>
                  <td>{c.pattern}</td>
                  <td>{c.active ? '✓' : '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
