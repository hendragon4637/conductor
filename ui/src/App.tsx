import { useEffect, useState } from 'react';
import { ProjectsList } from './views/ProjectsList';
import { SessionsList } from './views/SessionsList';
import { TasksList } from './views/TasksList';
import { TraceDetail } from './views/TraceDetail';
import { AgentConfigsList } from './views/AgentConfigsList';

type Route =
  | { name: 'projects' }
  | { name: 'sessions'; project_id: string }
  | { name: 'tasks'; project_id: string; session_id: string }
  | { name: 'trace'; trace_id: string }
  | { name: 'configs' };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, '');
  if (!h) return { name: 'projects' };
  if (h === 'configs') return { name: 'configs' };
  if (h.startsWith('trace/')) return { name: 'trace', trace_id: h.slice('trace/'.length) };
  const parts = h.split('/');
  if (parts[0] === 'p' && parts[1] && !parts[2]) return { name: 'sessions', project_id: parts[1] };
  if (parts[0] === 'p' && parts[1] && parts[2] === 's' && parts[3]) {
    const session_id = decodeURIComponent(parts.slice(3).join('/'));
    return { name: 'tasks', project_id: parts[1], session_id };
  }
  return { name: 'projects' };
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash());

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  return (
    <div style={{ fontFamily: 'ui-monospace, monospace', maxWidth: 1100, margin: '0 auto', padding: '16px' }}>
      <Header route={route} />
      <hr />
      <main>
        {route.name === 'projects' && <ProjectsList />}
        {route.name === 'sessions' && <SessionsList project_id={route.project_id} />}
        {route.name === 'tasks' && <TasksList project_id={route.project_id} session_id={route.session_id} />}
        {route.name === 'trace' && <TraceDetail trace_id={route.trace_id} />}
        {route.name === 'configs' && <AgentConfigsList />}
      </main>
    </div>
  );
}

function Header({ route }: { route: Route }) {
  return (
    <header>
      <h1 style={{ margin: 0 }}>AIPC Conductor</h1>
      <nav style={{ fontSize: 12, color: '#666', marginTop: 4 }}>
        <a href="#/">projects</a>
        {' › '}
        {route.name === 'sessions' && <span>{route.project_id}</span>}
        {route.name === 'tasks' && (
          <>
            <a href={`#/p/${route.project_id}`}>{route.project_id}</a> {' › '}
            <span>{route.session_id}</span>
          </>
        )}
        {route.name === 'trace' && <span>trace {route.trace_id.slice(0, 8)}…</span>}
        <span style={{ float: 'right' }}>
          <a href="#/configs">agent configs</a>
        </span>
      </nav>
    </header>
  );
}
