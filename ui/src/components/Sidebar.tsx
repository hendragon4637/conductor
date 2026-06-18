import { useEffect, useState, useCallback } from 'react';
import { api } from '../api';
import type { Project, Session } from '../api';
import { navigate } from '../App';

interface Props {
  activeProject?: string;
  activeSession?: string;
  activeTab?: string;
}

const TABS = [
  { key: 'chat', label: 'Chat' },
  { key: 'plan', label: 'Plan' },
  { key: 'sessions', label: 'Sessions' },
  { key: 'scores', label: 'Scores' },
  { key: 'ratchet', label: 'Ratchet' },
  { key: 'triggers', label: 'Triggers' },
  { key: 'worktrees', label: 'Worktrees' },
  { key: 'configs', label: 'Agents' },
  { key: 'memory', label: 'Memory' },
  { key: 'settings', label: 'Settings' },
] as const;

export function Sidebar({ activeProject, activeSession, activeTab }: Props) {
  const [projects, setProjects] = useState<Project[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [refreshSessionsTick, setRefreshSessionsTick] = useState(0);

  const reload = useCallback(() => {
    api.listProjects().then(setProjects).catch((e) => setError(String(e)));
  }, []);

  useEffect(() => { reload(); }, [reload]);

  const [manuallyCollapsed, setManuallyCollapsed] = useState<string | null>(null);

  const isTabActive = (key: string) => {
    if (activeTab === key) return true;
    // Fallback for legacy routing
    if (key === 'configs' && window.location.hash === '#/configs') return true;
    if (key === 'sessions' && window.location.hash.includes('/p/')) return true;
    return false;
  };

  return (
    <aside className="sidebar">
      {/* ── Project navigation ── */}
      <div className="sidebar-section">
        <div className="sidebar-section-title">Projects</div>

        {error && <pre className="error">{error}</pre>}

        {projects === null ? (
          <div className="text-muted text-xs" style={{ padding: '0 8px' }}>Loading…</div>
        ) : projects.length === 0 ? (
          <div className="text-muted text-xs" style={{ padding: '0 8px' }}>None yet.</div>
        ) : (
          projects.map((p) => {
            const isActive = activeProject === p.project_id;
            const isExpanded = isActive && manuallyCollapsed !== p.project_id;
            return (
              <ProjectItem
                key={p.project_id}
                project={p}
                isActive={isActive}
                isExpanded={isExpanded}
                activeSession={isActive ? activeSession : undefined}
                onToggleCollapse={() =>
                  setManuallyCollapsed(manuallyCollapsed === p.project_id ? null : p.project_id)
                }
                refreshSessionsKey={refreshSessionsTick}
                onSessionCreated={() => setRefreshSessionsTick((n) => n + 1)}
              />
            );
          })
        )}

        {!showProjectForm ? (
          <button
            className="nav-item nav-item-action"
            style={{ width: '100%', background: 'transparent', border: 'none' }}
            onClick={() => setShowProjectForm(true)}
          >
            + New project
          </button>
        ) : (
          <NewProjectForm
            onCancel={() => setShowProjectForm(false)}
            onCreated={(pid) => {
              setShowProjectForm(false);
              reload();
              navigate(`#/p/${pid}`);
            }}
          />
        )}
      </div>

      <div className="sidebar-spacer" />

      {/* ── Tab navigation ── */}
      <div className="sidebar-section">
        <div className="sidebar-section-title">Tabs</div>
        {TABS.map(tab => (
          <a
            key={tab.key}
            className={`nav-item ${isTabActive(tab.key) ? 'active' : ''}`}
            href={`#/${tab.key}`}
            onClick={(e) => {
              if (isTabActive(tab.key)) e.preventDefault();
            }}
          >
            <span className="chevron">·</span>
            <span className="nav-label">{tab.label}</span>
          </a>
        ))}
      </div>
    </aside>
  );
}

// ───────────────────────────────── ProjectItem ─────────────────────────────────

function ProjectItem({
  project,
  isActive,
  isExpanded,
  activeSession,
  onToggleCollapse,
  refreshSessionsKey,
  onSessionCreated,
}: {
  project: Project;
  isActive: boolean;
  isExpanded: boolean;
  activeSession?: string;
  onToggleCollapse: () => void;
  refreshSessionsKey: number;
  onSessionCreated: () => void;
}) {
  const handleClick = (e: React.MouseEvent) => {
    if (isActive) {
      e.preventDefault();
      onToggleCollapse();
    }
  };

  return (
    <>
      <a
        className={`nav-item ${isActive ? 'active' : ''}`}
        href={`#/p/${project.project_id}`}
        onClick={handleClick}
        title={project.description || ''}
      >
        <span className="chevron">{isExpanded ? '▾' : '▸'}</span>
        <span className="nav-label">{project.name}</span>
      </a>

      {isExpanded && (
        <SessionList
          project_id={project.project_id}
          activeSession={activeSession}
          refreshKey={refreshSessionsKey}
          onSessionCreated={onSessionCreated}
        />
      )}
    </>
  );
}

// ───────────────────────────────── SessionList ─────────────────────────────────

function SessionList({
  project_id,
  activeSession,
  refreshKey,
  onSessionCreated,
}: {
  project_id: string;
  activeSession?: string;
  refreshKey: number;
  onSessionCreated: () => void;
}) {
  const [sessions, setSessions] = useState<Session[] | null>(null);
  const [showForm, setShowForm] = useState(false);

  useEffect(() => {
    api.listSessions(project_id).then(setSessions).catch(() => setSessions([]));
  }, [project_id, refreshKey]);

  if (sessions === null) {
    return <div className="nav-item-session text-muted text-xs">Loading…</div>;
  }

  return (
    <>
      {sessions.map((s) => (
        <a
          key={s.session_id}
          className={`nav-item nav-item-session ${activeSession === s.session_id ? 'active' : ''}`}
          href={`#/p/${project_id}/s/${encodeURIComponent(s.session_id)}`}
          title={s.user_intent || ''}
        >
          <span className="chevron">·</span>
          <span className="nav-label">{s.session_id}</span>
          <span className="nav-meta">{s.status}</span>
        </a>
      ))}

      {!showForm ? (
        <button
          className="nav-item nav-item-session nav-item-action"
          style={{ width: 'auto', background: 'transparent', border: 'none', marginLeft: 8, marginRight: 8 }}
          onClick={() => setShowForm(true)}
        >
          + New session
        </button>
      ) : (
        <NewSessionForm
          project_id={project_id}
          onCancel={() => setShowForm(false)}
          onCreated={(sid) => {
            setShowForm(false);
            onSessionCreated();
            navigate(`#/p/${project_id}/s/${encodeURIComponent(sid)}`);
          }}
        />
      )}
    </>
  );
}

// ───────────────────────────────── New Project Form ─────────────────────────────────

function NewProjectForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (project_id: string) => void;
}) {
  const [pid, setPid] = useState('');
  const [name, setName] = useState('');
  const [desc, setDesc] = useState('');
  const [sysPrompt, setSysPrompt] = useState('');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const p = await api.createProject({
        project_id: pid.trim(),
        name: name.trim() || pid.trim(),
        description: desc.trim() || undefined,
        system_prompt: sysPrompt.trim() || undefined,
      });
      onCreated(p.project_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-form">
      <div className="field">
        <label>project_id (slug)</label>
        <input
          className="input"
          value={pid}
          onChange={(e) => setPid(e.target.value)}
          placeholder="backend-api"
          autoFocus
        />
      </div>
      <div className="field">
        <label>name</label>
        <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Backend API" />
      </div>
      <div className="field">
        <label>description</label>
        <input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} />
      </div>
      <div className="field">
        <label>system_prompt</label>
        <textarea
          className="textarea"
          rows={3}
          value={sysPrompt}
          onChange={(e) => setSysPrompt(e.target.value)}
          placeholder="Long-lived project context. Injected into AGENTS.md on every spawn."
        />
      </div>
      {err && <pre className="error">{err}</pre>}
      <div className="actions">
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !pid.trim()}>
          {busy ? 'Creating…' : 'Create'}
        </button>
      </div>
    </div>
  );
}

// ───────────────────────────────── New Session Form ─────────────────────────────────

function NewSessionForm({
  project_id,
  onCancel,
  onCreated,
}: {
  project_id: string;
  onCancel: () => void;
  onCreated: (session_id: string) => void;
}) {
  const [sid, setSid] = useState('');
  const [intent, setIntent] = useState('');
  const [baseBranch, setBaseBranch] = useState('main');
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const s = await api.createSession({
        project_id,
        session_id: sid.trim(),
        user_intent: intent.trim() || undefined,
        base_branch: baseBranch.trim() || 'main',
      });
      onCreated(s.session_id);
    } catch (e) {
      setErr(String(e));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="inline-form" style={{ marginLeft: 24, marginRight: 8 }}>
      <div className="field">
        <label>branch name</label>
        <input
          className="input"
          value={sid}
          onChange={(e) => setSid(e.target.value)}
          placeholder="feat/oauth"
          autoFocus
        />
      </div>
      <div className="field">
        <label>user_intent</label>
        <input
          className="input"
          value={intent}
          onChange={(e) => setIntent(e.target.value)}
          placeholder="what's the goal?"
        />
      </div>
      <div className="field">
        <label>base branch</label>
        <input className="input" value={baseBranch} onChange={(e) => setBaseBranch(e.target.value)} />
      </div>
      {err && <pre className="error">{err}</pre>}
      <div className="actions">
        <button className="btn btn-ghost btn-sm" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className="btn btn-primary btn-sm" onClick={submit} disabled={busy || !sid.trim()}>
          {busy ? 'Creating…' : 'Create branch'}
        </button>
      </div>
    </div>
  );
}
