import { useEffect, useState } from 'react';
import { api } from '../api';
import type { AgentConfig } from '../api';

export function AgentConfigsList() {
  const [items, setItems] = useState<AgentConfig[] | null>(null);

  useEffect(() => {
    api.listConfigs().then(setItems).catch(() => setItems([]));
  }, []);

  return (
    <section>
      <h2>Agent configs <small>(read-only — edit YAML and re-run bootstrap)</small></h2>
      {items === null ? (
        <p>Loading…</p>
      ) : (
        <table style={{ width: '100%', fontSize: 12 }}>
          <thead>
            <tr style={{ background: '#eee' }}>
              <th align="left">id</th>
              <th align="left">cli</th>
              <th align="left">domain</th>
              <th align="left">role</th>
              <th align="left">pattern</th>
              <th align="left">active</th>
            </tr>
          </thead>
          <tbody>
            {items.map((c) => (
              <tr key={c.agent_config_id} style={{ borderBottom: '1px solid #eee' }}>
                <td><code>{c.agent_config_id}</code></td>
                <td>{c.cli}</td>
                <td>{c.domain}</td>
                <td>{c.role}</td>
                <td>{c.pattern}</td>
                <td>{c.active ? '✓' : '—'}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </section>
  );
}
