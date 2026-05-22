import { useEffect, useState } from 'react';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { TasksView } from './views/TasksView';
import { WelcomeView } from './views/WelcomeView';
import { AgentConfigsList } from './views/AgentConfigsList';
import { TraceDrawer } from './views/TraceDrawer';

// ───────────────────────────────── routing ─────────────────────────────────

export type Route =
  | { name: 'welcome'; project_id?: string }
  | { name: 'tasks'; project_id: string; session_id: string; trace_id?: string }
  | { name: 'configs' };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, '');
  if (!h) return { name: 'welcome' };
  if (h === 'configs') return { name: 'configs' };
  const parts = h.split('/');
  if (parts[0] === 'p' && parts[1] && !parts[2]) return { name: 'welcome', project_id: parts[1] };
  if (parts[0] === 'p' && parts[1] && parts[2] === 's' && parts[3]) {
    const tail = parts.slice(3).join('/');
    const tIdx = tail.indexOf('/t/');
    let session_id = tail, trace_id: string | undefined;
    if (tIdx !== -1) {
      session_id = tail.slice(0, tIdx);
      trace_id = tail.slice(tIdx + 3);
    }
    session_id = decodeURIComponent(session_id);
    return { name: 'tasks', project_id: parts[1], session_id, trace_id };
  }
  return { name: 'welcome' };
}

export function navigate(hash: string) {
  if (window.location.hash !== hash) window.location.hash = hash;
}

// ───────────────────────────────── App ─────────────────────────────────

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash());

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  const activeProject =
    route.name === 'welcome' ? route.project_id :
    route.name === 'tasks' ? route.project_id :
    undefined;
  const activeSession = route.name === 'tasks' ? route.session_id : undefined;
  const activeTrace = route.name === 'tasks' ? route.trace_id : undefined;

  const drawerOpen = Boolean(activeTrace);

  return (
    <div className="app">
      <Header route={route} />
      <div className="shell">
        <Sidebar
          activeProject={activeProject}
          activeSession={activeSession}
        />
        <main className={`main-pane${drawerOpen ? ' with-drawer' : ''}`}>
          {route.name === 'welcome' && <WelcomeView project_id={route.project_id} />}
          {route.name === 'tasks' && (
            <TasksView
              project_id={route.project_id}
              session_id={route.session_id}
            />
          )}
          {route.name === 'configs' && <AgentConfigsList />}
        </main>
        {drawerOpen && route.name === 'tasks' && (
          <TraceDrawer
            trace_id={activeTrace!}
            onClose={() => navigate(`#/p/${route.project_id}/s/${encodeURIComponent(route.session_id)}`)}
          />
        )}
      </div>
    </div>
  );
}
