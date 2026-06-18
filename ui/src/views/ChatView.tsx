import { useState, useEffect, useRef } from 'react';

interface Message {
  message_id: string;
  role: string;
  content: string;
  created_at: string;
}

interface Thread {
  thread_id: string;
  title: string;
  project_id: string | null;
  project_ids: string[];
  model: string;
  created_at: string;
}

const MODELS = ['local-test', 'deepseek-v4-flash-free'];

interface ProjectItem {
  project_id: string;
  name: string;
}

export function ChatView() {
  const [threads, setThreads] = useState<Thread[]>([]);
  const [activeThread, setActiveThread] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [model, setModel] = useState(MODELS[0]);
  const [projects, setProjects] = useState<ProjectItem[]>([]);
  const [selectedProjects, setSelectedProjects] = useState<string[]>([]);
  const [attachFile, setAttachFile] = useState<File | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const imageRef = useRef<HTMLInputElement>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const mergeThread = (thread: Thread) => {
    setThreads(prev => {
      const exists = prev.some(t => t.thread_id === thread.thread_id);
      if (!exists) return [...prev, thread];
      return prev.map(t => (t.thread_id === thread.thread_id ? thread : t));
    });
  };

  const persistThreadSettings = async (threadId: string, next: { model?: string; project_ids?: string[] }) => {
    const res = await fetch(`/api/chat/threads/${threadId}`, {
      method: 'PUT',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(next),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const updated: Thread & { messages?: Message[] } = await res.json();
    mergeThread(updated);
    setModel(updated.model || MODELS[0]);
    setSelectedProjects(updated.project_ids || []);
    if (updated.messages) setMessages(updated.messages);
    return updated;
  };

  useEffect(() => { fetch('/api/chat/threads').then(r => r.json()).then(setThreads); }, []);
  useEffect(() => { fetch('/api/projects').then(r => r.json()).then(setProjects); }, []);

  useEffect(() => {
    if (activeThread) {
      fetch(`/api/chat/threads/${activeThread}`)
        .then(r => r.json())
        .then(d => {
          const detail = d as Thread & { messages?: Message[] };
          mergeThread(detail);
          setMessages(detail.messages || []);
          setModel(detail.model || MODELS[0]);
          setSelectedProjects(detail.project_ids || []);
        });
    } else {
      setMessages([]);
      setModel(MODELS[0]);
      setSelectedProjects([]);
    }
  }, [activeThread]);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const createThread = async () => {
    const res = await fetch('/api/chat/threads', {
      method: 'POST', headers: { 'content-type': 'application/json' },
      body: JSON.stringify({
        title: 'New Chat',
        model: MODELS[0],
        project_id: null,
      }),
    });
    const t = await res.json();
    mergeThread(t);
    setActiveThread(t.thread_id);
  };

  const send = async () => {
    if (!input.trim() || !activeThread) return;
    setSending(true);
    try {
      const res = await fetch(`/api/chat/threads/${activeThread}/messages`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ content: input, project_ids: selectedProjects }),
      });
      const data = await res.json();
      setMessages(prev => [...prev, data.user_message, data.assistant_message]);
      setInput('');
    } catch (e) {
      console.error('Send failed', e);
    } finally {
      setSending(false);
    }
  };

  const promoteToPlan = async () => {
    if (!activeThread) return;
    const msgIds = messages.filter(m => m.role === 'user').slice(-5).map(m => m.message_id);
    // CRITICAL: always pass project_id when projects are selected so backend can set it on the plan
    const body: Record<string, unknown> = { thread_id: activeThread, message_ids: msgIds, project_ids: selectedProjects };
    if (selectedProjects.length > 0) {
      body.project_id = selectedProjects[0];
    }
    try {
      const res = await fetch('/api/chat/promote-to-plan', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      await res.json();
      window.location.hash = '#/plan';
    } catch (e) {
      console.error('Promote failed', e);
    }
  };

  const updateThreadModel = async (nextModel: string) => {
    setModel(nextModel);
    if (!activeThread) return;
    try {
      await persistThreadSettings(activeThread, { model: nextModel });
    } catch (e) {
      console.error('Failed to update thread model', e);
    }
  };

  const updateThreadProjects = async (nextProjectIds: string[]) => {
    setSelectedProjects(nextProjectIds);
    if (!activeThread) return;
    try {
      await persistThreadSettings(activeThread, { project_ids: nextProjectIds });
    } catch (e) {
      console.error('Failed to update thread projects', e);
    }
  };

  return (
    <div className="chat-layout">
      <div className="chat-sidebar-inner" style={{ width: 170, flexShrink: 0 }}>
        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, padding: '0 4px' }}>
          <span className="uppercase-label">Threads</span>
          <button className="btn btn-sm" onClick={createThread} title="New thread" style={{ padding: '2px 8px', fontSize: 14, lineHeight: 1 }}>
            ＋
          </button>
        </div>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
          {threads.length === 0 && (
            <div style={{ padding: '8px 4px', fontSize: 12, color: 'var(--text-muted)' }}>
              No threads yet
            </div>
          )}
          {threads.map(t => (
            <div
              key={t.thread_id}
              className={`nav-item ${activeThread === t.thread_id ? 'active' : ''}`}
              onClick={() => setActiveThread(t.thread_id)}
            >
              <span className="nav-label">{t.title}</span>
            </div>
          ))}
        </div>
      </div>

      <div className="chat-main">
        {!activeThread ? (
          <div className="empty-state">
            <h3>Select or create a thread</h3>
            <p style={{ maxWidth: 320, fontSize: 'var(--text-sm)' }}>
              Choose a thread from the sidebar or click ＋ to start a new conversation.
            </p>
          </div>
        ) : (
          <>
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, flexWrap: 'wrap', marginBottom: 8, fontSize: 13 }}>
              <select
                className="select"
                style={{ width: 'auto', minWidth: 150, padding: '4px 8px' }}
                value={model}
                onChange={e => void updateThreadModel(e.target.value)}
              >
                {MODELS.map(m => <option key={m} value={m}>{m}</option>)}
              </select>
              <span style={{ color: 'var(--text-muted)', fontSize: 11 }}>projects:</span>
              {projects
                .filter(p => selectedProjects.includes(p.project_id))
                .map(p => (
                  <span key={p.project_id} className="chip chip-success" style={{ fontSize: 11, padding: '2px 8px' }}>
                    {p.name || p.project_id}
                    <span
                      style={{ cursor: 'pointer', marginLeft: 2, fontSize: 12, lineHeight: 1 }}
                      onClick={e => {
                        e.stopPropagation();
                        void updateThreadProjects(selectedProjects.filter(id => id !== p.project_id));
                      }}
                    >
                      ✕
                    </span>
                  </span>
                ))}
              <details style={{ position: 'relative' }}>
                <summary className="btn btn-sm" style={{ padding: '2px 8px', fontSize: 11, cursor: 'pointer' }}>
                  ＋ project
                </summary>
                <div style={{
                  position: 'absolute',
                  top: 'calc(100% + 4px)',
                  left: 0,
                  minWidth: 220,
                  maxHeight: 220,
                  overflowY: 'auto',
                  padding: 8,
                  border: '1px solid var(--border-subtle)',
                  background: 'var(--bg-base)',
                  zIndex: 20,
                  boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
                }}>
                  {projects.map(project => {
                    const checked = selectedProjects.includes(project.project_id);
                    return (
                      <label key={project.project_id} style={{ display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', fontSize: 12 }}>
                        <input
                          type="checkbox"
                          checked={checked}
                          onChange={() => {
                            const next = checked
                              ? selectedProjects.filter(id => id !== project.project_id)
                              : [...selectedProjects, project.project_id];
                            void updateThreadProjects(next);
                          }}
                        />
                        <span>{project.name || project.project_id}</span>
                      </label>
                    );
                  })}
                </div>
              </details>
              <span className="text-muted" style={{ marginLeft: 'auto', fontSize: 10 }}>think only</span>
            </div>

            {messages.length === 0 ? (
              <div className="empty-state" style={{ minHeight: 200 }}>
                <h3>Send a message to start chatting</h3>
              </div>
            ) : (
              <div className="chat-messages">
                {messages.map(m => (
                  <div
                    key={m.message_id}
                    className={`chat-msg chat-msg-${m.role}${m.role === 'user' ? ' me' : ''}`}
                  >
                    <div className="chat-msg-role">{m.role}</div>
                    <div className="chat-msg-content">{m.content}</div>
                  </div>
                ))}
                <div ref={endRef} />
              </div>
            )}

            <div style={{ borderTop: '1px solid var(--border-subtle)', margin: '8px 0' }} />
            {attachFile && (
              <div style={{ fontSize: 11, color: 'var(--text-secondary)', marginBottom: 4 }}>
                Attached: {attachFile.name}
                <button className="btn btn-ghost btn-sm" onClick={() => setAttachFile(null)} style={{ marginLeft: 4 }}>✕</button>
              </div>
            )}

            <div className="chat-input-bar">
              <input
                type="file"
                ref={fileRef}
                hidden
                accept="*/*"
                onChange={e => setAttachFile(e.target.files?.[0] || null)}
              />
              <input
                type="file"
                ref={imageRef}
                hidden
                accept="image/*"
                onChange={e => setAttachFile(e.target.files?.[0] || null)}
              />
              <button className="btn btn-sm" onClick={() => fileRef.current?.click()} title="Attach file">
                📎
              </button>
              <button className="btn btn-sm" onClick={() => imageRef.current?.click()} title="Attach image (multimodal)">
                🖼️
              </button>
              <input
                className="input"
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && !e.shiftKey && send()}
                placeholder="ask anything…"
                disabled={sending}
              />
              <button className="btn btn-sm" onClick={send} disabled={sending || !input.trim()}>
                {sending ? '…' : 'Send'}
              </button>
              <button
                className="btn btn-primary btn-sm"
                onClick={promoteToPlan}
                disabled={!activeThread || messages.length === 0}
                title="Promote thread to a plan"
              >
                Promote to plan →
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
