import { useEffect, useState } from 'react';
import { Header } from './components/Header';
import { Sidebar } from './components/Sidebar';
import { TasksView } from './views/TasksView';
import { WelcomeView } from './views/WelcomeView';
import { AgentConfigsList } from './views/AgentConfigsList';
import { TraceDrawer } from './views/TraceDrawer';
import { ChatView } from './views/ChatView';
import { PlanView } from './views/PlanView';
import { ScoresView } from './views/ScoresView';
import { RatchetView } from './views/RatchetView';
import { TriggersView } from './views/TriggersView';
import { WorktreesView } from './views/WorktreesView';
import { MemoryView } from './views/MemoryView';
import { SettingsView } from './views/SettingsView';
import { SessionsView } from './views/SessionsView';
import TerminalTabs from './components/TerminalTabs';
import { ptyRegistry } from './lib/ptyRegistry';
import { pendingSpawns } from './lib/ptyRegistry';

// ───────────────────────────────── routing ─────────────────────────────────

export type Route =
  | { name: 'welcome'; project_id?: string }
  | { name: 'tasks'; project_id: string; session_id: string; trace_id?: string }
  | { name: 'configs' }
  | { name: 'chat' }
  | { name: 'plan' }
  | { name: 'scores' }
  | { name: 'ratchet' }
  | { name: 'triggers' }
  | { name: 'worktrees' }
  | { name: 'memory' }
  | { name: 'settings' }
  | { name: 'sessions' };

function parseHash(): Route {
  const h = window.location.hash.replace(/^#\/?/, '');
  if (!h) return { name: 'welcome' };
  if (h === 'configs') return { name: 'configs' };
  if (h === 'chat') return { name: 'chat' };
  if (h === 'plan') return { name: 'plan' };
  if (h === 'scores') return { name: 'scores' };
  if (h === 'ratchet') return { name: 'ratchet' };
  if (h === 'sessions') return { name: 'sessions' };
  if (h === 'triggers') return { name: 'triggers' };
  if (h === 'worktrees') return { name: 'worktrees' };
  if (h === 'memory') return { name: 'memory' };
  if (h === 'settings') return { name: 'settings' };
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
  const [, forceRender] = useState(0);

  useEffect(() => ptyRegistry.subscribe(() => forceRender(n => n + 1)), []);

  const hasTerminals = ptyRegistry.list().length > 0 || Array.from(pendingSpawns.keys()).length > 0;

  useEffect(() => {
    const onHash = () => setRoute(parseHash());
    window.addEventListener('hashchange', onHash);
    return () => window.removeEventListener('hashchange', onHash);
  }, []);

  // PTY cleanup on window close (Tauri only)
  useEffect(() => {
    let unlisten: (() => void) | undefined;
    (async () => {
      try {
        const { listen } = await import("@tauri-apps/api/event");
        unlisten = await listen("app-close-requested", () => {
          ptyRegistry.killAll();
        });
      } catch {}
    })();
    return () => unlisten?.();
  }, []);

  const activeProject =
    route.name === 'welcome' ? route.project_id :
    route.name === 'tasks' ? route.project_id :
    undefined;
  const activeSession = route.name === 'tasks' ? route.session_id : undefined;
  const activeTrace = route.name === 'tasks' ? route.trace_id : undefined;

  const drawerOpen = Boolean(activeTrace);

  // Tab view names for sidebar highlighting
  const tabRoutes = new Set(['chat', 'plan', 'scores', 'ratchet', 'sessions', 'triggers', 'worktrees', 'memory', 'settings']);

  return (
    <div className="app">
      <Header route={route} />
      <div className="shell">
        <Sidebar
          activeProject={activeProject}
          activeSession={activeSession}
          activeTab={tabRoutes.has(route.name) ? route.name : undefined}
        />
        <div style={{ display: "flex", flexDirection: "column", height: "100%", flex: 1 }}>
          <div style={{ display: "flex", flex: 1, overflow: "hidden" }}>
            <main className={`main-pane${drawerOpen ? ' with-drawer' : ''}`}>
              {route.name === 'welcome' && <WelcomeView project_id={route.project_id} />}
              {route.name === 'tasks' && (
                <TasksView
                  project_id={route.project_id}
                  session_id={route.session_id}
                />
              )}
              {route.name === 'configs' && <AgentConfigsList />}
              {route.name === 'chat' && <ChatView />}
              {route.name === 'plan' && <PlanView />}
              {route.name === 'scores' && <ScoresView />}
              {route.name === 'ratchet' && <RatchetView />}
              {route.name === 'triggers' && <TriggersView />}
              {route.name === 'worktrees' && <WorktreesView />}
              {route.name === 'memory' && <MemoryView />}
              {route.name === 'settings' && <SettingsView />}
              {route.name === 'sessions' && <SessionsView />}
            </main>
            {drawerOpen && route.name === 'tasks' && (
              <TraceDrawer
                trace_id={activeTrace!}
                onClose={() => navigate(`#/p/${route.project_id}/s/${encodeURIComponent(route.session_id)}`)}
              />
            )}
          </div>
          {hasTerminals && <div style={{
            height: "40%",
            minHeight: 200,
            borderTop: "1px solid var(--border-strong)",
            background: "var(--bg-base)",
          }}>
            <TerminalTabs />
          </div>}
        </div>
      </div>
    </div>
  );
}
