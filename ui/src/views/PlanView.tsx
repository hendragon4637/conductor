import { useState, useEffect } from 'react';
import { navigate } from '../App';

interface AgentConfig {
  agent_config_id: string;
  cli: string;
  harness: string;
  domain: string;
  role: string;
  pattern: string;
  active: boolean;
}

interface PlanNode {
  node_id: string;
  title: string;
  description: string;
  depends_on: string[];
  status: string;
  members?: string[];
  agent_config_id?: string;
  success_criterion?: string;
  node_commit_tag?: string;
  gate_mode?: string;
  /** Backend type for this node: 'aionui' | 'hermes' | 'opencode' | 'opencode_omo' | 'claude-code' | 'codex' | 'gemini' */
  backend_type?: string;
}

interface Plan {
  plan_id: string;
  title: string;
  description: string | null;
  worktree_id: string | null;
  project_id: string | null;
  ratified: boolean;
  nodes: PlanNode[];
  created_at: string;
  source_thread?: string;
  worktree_path?: string;
}

interface Run {
  run_id: string;
  plan_id: string;
  state: string;  // created|approved|running|done|failed|cancelled
  created_at: string | null;
  approved_at: string | null;
  finished_at: string | null;
  worktree_root: string | null;
  note: string | null;
}

export function PlanView() {
  // ── Plan list state ──
  const [plans, setPlans] = useState<Plan[]>([]);
  const [activePlan, setActivePlan] = useState<Plan | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [activeRun, setActiveRun] = useState<Run | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // ── Agent configs ──
  const [agentConfigs, setAgentConfigs] = useState<AgentConfig[]>([]);

  // ── Editor toggle states ──
  const [showWorktreeEditor, setShowWorktreeEditor] = useState(false);
  const [showNodeEditor, setShowNodeEditor] = useState(false);
  const [showAppendEditor, setShowAppendEditor] = useState(false);
  const [showCrossEditor, setShowCrossEditor] = useState(false);
  const [showNewPlanEditor, setShowNewPlanEditor] = useState(false);

  // ── Refine input ──
  const [refineInput, setRefineInput] = useState('');

  // ── New plan form ──
  const [newPlanTitle, setNewPlanTitle] = useState('');
  const [_newPlanDescription, setNewPlanDescription] = useState('');
  const [newPlanProjectId, setNewPlanProjectId] = useState('');
  const [newPlanBranch, setNewPlanBranch] = useState('');
  const [newPlanType, setNewPlanType] = useState<'same' | 'new'>('same');
  const [newPlanGoal, setNewPlanGoal] = useState('');
  const [newPlanSpec, setNewPlanSpec] = useState('');
  const [newPlanIntent, setNewPlanIntent] = useState('');
  const [newPlanBackend, setNewPlanBackend] = useState('aionui');

  // ── Edit node state ──
  const [editNodeId, setEditNodeId] = useState<string | null>(null);
  const [editMembers, setEditMembers] = useState<string[]>([]);
  const [editDepends, setEditDepends] = useState<string[]>([]);
  const [editSuccessCriterion, setEditSuccessCriterion] = useState('');
  const [editBackend, setEditBackend] = useState('aionui');
  const [editOcType, setEditOcType] = useState<'opencode' | 'opencode_omo'>('opencode');
  const [editModel, setEditModel] = useState('');
  const [editAppendedPrompt, setEditAppendedPrompt] = useState('');
  const [editPermissions, setEditPermissions] = useState({ edit: 'allow', bash: 'allow', webfetch: 'deny' });

  // ── Append node state ──
  const [appendMembers, setAppendMembers] = useState<string[]>([]);
  const [appendDepends, setAppendDepends] = useState<string[]>([]);
  const [appendTask, setAppendTask] = useState('');
  const [appendSuccessCriterion, setAppendSuccessCriterion] = useState('');

  // ── Cross-project node state ──
  const [crossTargetProject, setCrossTargetProject] = useState('');
  const [crossWorktreeStrategy, setCrossWorktreeStrategy] = useState<'reuse' | 'create'>('reuse');
  const [crossMembers, setCrossMembers] = useState<string[]>([]);
  const [crossDepends, setCrossDepends] = useState<string[]>([]);
  const [crossTask, setCrossTask] = useState('');

  // ── Worktree editor state ──
  const [wtProject, setWtProject] = useState('');
  const [wtStrategy, setWtStrategy] = useState<'create' | 'reuse'>('create');
  const [wtBranch, setWtBranch] = useState('');

  // ── Load plans and agent configs on mount ──
  useEffect(() => {
    fetch('/api/plans')
      .then(r => { if (!r.ok) throw new Error(`HTTP ${r.status}`); return r.json(); })
      .then(data => {
        setPlans(Array.isArray(data) ? data : []);
        setLoading(false);
      })
      .catch(err => {
        setError(err.message);
        setLoading(false);
      });
    fetch('/api/agent_configs?active_only=true')
      .then(r => r.json())
      .then(data => setAgentConfigs(Array.isArray(data) ? data : []))
      .catch(() => {});
  }, []);

  // ── Select a plan and load details ──
  const selectPlan = async (plan: Plan) => {
    try {
      const res = await fetch(`/api/plans/${plan.plan_id}`);
      if (res.ok) {
        const detail: Plan = await res.json();
        setActivePlan(detail);
      } else {
        setActivePlan(plan);
      }
    } catch {
      setActivePlan(plan);
    }
    setActiveRun(null);
    fetchRuns(plan.plan_id);
    setShowWorktreeEditor(false);
    setShowNodeEditor(false);
    setShowAppendEditor(false);
    setShowCrossEditor(false);
    setShowNewPlanEditor(false);
  };

  // ── Propose a new plan ──
  const propose = async () => {
    if (!newPlanTitle.trim()) return;
    const body: Record<string, unknown> = {
      title: newPlanTitle,
      description: newPlanGoal || undefined,
    };
    if (newPlanSpec.trim()) body.spec = newPlanSpec.trim();
    if (newPlanIntent.trim()) body.quality_intent = newPlanIntent.trim();
    body.backend_type = newPlanBackend;
    if (newPlanProjectId.trim()) body.project_id = newPlanProjectId.trim();

    try {
      if (newPlanType === 'new' && newPlanBranch.trim()) {
        const wtRes = await fetch('/api/worktrees', {
          method: 'POST',
          headers: { 'content-type': 'application/json' },
          body: JSON.stringify({ branch: newPlanBranch.trim(), project_id: newPlanProjectId.trim() || undefined }),
        });
        const wt = await wtRes.json();
        body.worktree_id = wt.path || newPlanBranch.trim();
      }
      const res = await fetch('/api/plans', {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      const p: Plan = await res.json();
      setPlans(prev => [...prev, p]);
      setActivePlan(p);
      setNewPlanTitle('');
      setNewPlanGoal('');
      setNewPlanSpec('');
      setNewPlanIntent('');
      setNewPlanBackend('aionui');
      setNewPlanDescription('');
      setNewPlanProjectId('');
      setNewPlanBranch('');
      setShowNewPlanEditor(false);
    } catch (err) {
      console.error('Failed to propose plan:', err);
    }
  };

  // ── Fetch runs for a plan ──
  const fetchRuns = async (planId: string) => {
    try {
      const res = await fetch(`/api/plans/${planId}/runs`);
      if (res.ok) setRuns(await res.json());
    } catch (err) {
      console.error('Failed to fetch runs:', err);
    }
  };

  // ── Ratify plan ──
  const ratifyPlan = async (plan: Plan) => {
    try {
      const res = await fetch(`/api/plans/${plan.plan_id}/ratify`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ ratified: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      // Refresh the plan from the API
      const refreshed = await fetch(`/api/plans/${plan.plan_id}`);
      if (refreshed.ok) {
        const updated: Plan = await refreshed.json();
        setPlans(prev => prev.map(p => p.plan_id === plan.plan_id ? updated : p));
        if (activePlan?.plan_id === plan.plan_id) setActivePlan(updated);
      }
    } catch (err) {
      console.error('Failed to ratify plan:', err);
    }
  };

  // ── Create run ──
  const createRun = async (plan: Plan) => {
    try {
      const res = await fetch(`/api/plans/${plan.plan_id}/runs`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const run: Run = await res.json();
      setRuns(prev => [...prev, run]);
      setActiveRun(run);
    } catch (err) {
      console.error('Failed to create run:', err);
    }
  };

  // ── Approve run ──
  const approveRun = async (run: Run) => {
    try {
      const res = await fetch(`/api/runs/${run.run_id}/approve`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchRuns(run.plan_id);
    } catch (err) {
      console.error('Failed to approve run:', err);
    }
  };

  // ── Start run ──
  const startRun = async (run: Run) => {
    try {
      const res = await fetch(`/api/runs/${run.run_id}/start`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await fetchRuns(run.plan_id);
      navigate('#/sessions');
    } catch (err) {
      console.error('Failed to start run:', err);
    }
  };

  // ── Refine plan via brain ──
  const refinePlan = async () => {
    if (!activePlan || !refineInput.trim()) return;
    try {
      const res = await fetch(`/api/plans/${activePlan.plan_id}/refine`, {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({ instruction: refineInput.trim() }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated: Plan = await res.json();
      setPlans(prev => prev.map(p => p.plan_id === activePlan.plan_id ? updated : p));
      setActivePlan(updated);
      setRefineInput('');
    } catch (err) {
      console.error('Failed to refine plan:', err);
    }
  };

  // ── Append node ──
  const appendNode = async () => {
    if (!activePlan || !appendTask.trim()) return;
    const body: Record<string, unknown> = {
      title: appendTask.trim().slice(0, 40),
      description: appendTask.trim(),
    };
    if (appendMembers.length > 0) body.members = appendMembers;
    if (appendDepends.length > 0) body.depends_on = appendDepends;
    if (appendSuccessCriterion.trim()) body.success_criterion = appendSuccessCriterion.trim();
    try {
      const res = await fetch(`/api/plans/${activePlan.plan_id}/nodes`, {
        method: 'POST', headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const updated: Plan = await res.json();
      setPlans(prev => prev.map(p => p.plan_id === activePlan.plan_id ? updated : p));
      if (activePlan?.plan_id === activePlan.plan_id) setActivePlan(updated);
      setAppendMembers([]);
      setAppendDepends([]);
      setAppendTask('');
      setAppendSuccessCriterion('');
      setShowAppendEditor(false);
    } catch (err) {
      console.error('Failed to append node:', err);
    }
  };

  // ── Edit node (save) ──
  const saveNodeEdit = async () => {
    if (!activePlan) return;
    // Try to update node via API; fall back to re-fetching plan
    try {
      const body: Record<string, unknown> = {};
      body.backend_type = editBackend;
      if (editBackend === 'opencode' || editBackend === 'opencode_omo') {
        body.model = editModel || undefined;
        body.appended_prompt = editAppendedPrompt || undefined;
        body.permissions = editPermissions;
      }
      if (editBackend !== 'a' && editMembers.length > 0) body.members = editMembers;
      if (editDepends.length > 0) body.depends_on = editDepends;
      if (editSuccessCriterion.trim()) body.success_criterion = editSuccessCriterion.trim();

      // Best-effort PUT to update node
      const targetId = editNodeId || activePlan.nodes[activePlan.nodes.length - 1]?.node_id || '0';
      const res = await fetch(`/api/plans/${activePlan.plan_id}/nodes/${targetId}`, {
        method: 'PUT',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        const updated: Plan = await res.json();
        setPlans(prev => prev.map(p => p.plan_id === activePlan.plan_id ? updated : p));
        if (activePlan?.plan_id === activePlan.plan_id) setActivePlan(updated);
      } else {
        // Fallback: refresh plan
        const refreshed = await fetch(`/api/plans/${activePlan.plan_id}`);
        if (refreshed.ok) {
          const updated: Plan = await refreshed.json();
          setPlans(prev => prev.map(p => p.plan_id === activePlan.plan_id ? updated : p));
          if (activePlan?.plan_id === activePlan.plan_id) setActivePlan(updated);
        }
      }
    } catch (err) {
      console.error('Failed to save node edit:', err);
    }
    setShowNodeEditor(false);
    setEditNodeId(null);
  };

  // ── Open edit node for a specific node ──
  const openEditNode = (node: PlanNode) => {
    if (!activePlan) return;
    const bt = node.backend_type || 'aionui';
    setEditNodeId(node.node_id);
    setEditMembers(node.members || []);
    setEditDepends(node.depends_on || []);
    setEditSuccessCriterion(node.success_criterion || '');
    setEditBackend(bt);
    setEditOcType(bt === 'opencode_omo' ? 'opencode_omo' : 'opencode');
    setEditModel('');
    setEditAppendedPrompt('');
    setEditPermissions({ edit: 'allow', bash: 'allow', webfetch: 'deny' });
    setShowNodeEditor(true);
    setShowAppendEditor(false);
    setShowCrossEditor(false);
  };

  // ── Open worktree editor with initial values ──
  const toggleWorktreeEditor = () => {
    if (!showWorktreeEditor && activePlan) {
      setWtProject(activePlan.project_id || '');
      setWtBranch(activePlan.worktree_id || '');
      setWtStrategy('create');
    }
    setShowWorktreeEditor(!showWorktreeEditor);
  };

  // ── Open new plan sidebar editor ──
  const openNewPlanEditor = (type: 'same' | 'new') => {
    setNewPlanType(type);
    setNewPlanTitle('');
    setNewPlanDescription('');
    setNewPlanProjectId('');
    setNewPlanBranch('');
    setShowNewPlanEditor(true);
  };

  // ── Multi-select helpers ──
  const handleMultiChange = (e: React.ChangeEvent<HTMLSelectElement>, setter: (vals: string[]) => void) => {
    const selected = Array.from(e.target.selectedOptions, opt => opt.value);
    setter(selected);
  };

  // ── Helpers ──
  const runStatePillClass = (state: string) => {
    switch (state) {
      case 'created': return 'pill-queued';
      case 'approved': return 'pill-done';
      case 'running': return 'pill-done';
      case 'done': return 'pill-done';
      case 'failed': return 'pill-fail';
      case 'cancelled': return 'pill-done';
      default: return 'pill-queued';
    }
  };

  const runStateLabel = (state: string) => {
    switch (state) {
      case 'created': return 'created';
      case 'approved': return 'approved';
      case 'running': return 'running';
      case 'done': return 'done';
      case 'failed': return 'failed';
      case 'cancelled': return 'cancelled';
      default: return state;
    }
  };

  const memberCount = (n: PlanNode) => (n.members || []).length;
  const orchestratorTag = (n: PlanNode) => {
    const cls = getBackendClass(n.backend_type);
    const cnt = memberCount(n);
    if (cls === 'a') return 'self-routing (no orchestrator)';
    if (cnt === 0) return 'orchestrator only';
    return `orchestrator + ${cnt} member${cnt !== 1 ? 's' : ''}`;
  };

  const nodeIndex = (nodeId: string) => {
    if (!activePlan) return -1;
    return activePlan.nodes.findIndex(n => n.node_id === nodeId);
  };

  const depLabel = (node: PlanNode) => {
    if (!node.depends_on || node.depends_on.length === 0) return 'no deps';
    const indices = node.depends_on.map(d => {
      const idx = nodeIndex(d);
      return idx >= 0 ? `${idx + 1}` : d;
    });
    return `→ depends on ${indices.join(', ')}`;
  };

  // ── Group plans by project ──
  const grouped = plans.reduce((acc, p) => {
    const key = p.project_id || '__no_project';
    if (!acc[key]) acc[key] = [];
    acc[key].push(p);
    return acc;
  }, {} as Record<string, Plan[]>);

  const projectKeys = Object.keys(grouped).sort((a, b) => {
    if (a === '__no_project') return 1;
    if (b === '__no_project') return -1;
    return a.localeCompare(b);
  });

  const projectLabel = (key: string) => key === '__no_project' ? 'Other' : key;

  // ── Backend class helpers ──
  const BACKEND_CLASSES: Record<string, 'a' | 'b' | 'team'> = {
    hermes: 'a',
    opencode_omo: 'a',
    opencode: 'b',
    'claude-code': 'b',
    codex: 'b',
    gemini: 'b',
    aionui: 'team',
  };
  const BACKEND_LABELS: Record<string, string> = {
    hermes: 'Hermes',
    opencode_omo: 'OpenCode+OMO',
    opencode: 'OpenCode (plain)',
    'claude-code': 'Claude Code',
    codex: 'Codex',
    gemini: 'Gemini',
    aionui: 'AionUi',
  };
  const getBackendClass = (b?: string) => BACKEND_CLASSES[b || 'aionui'] || 'b';
  const isClassA = (b?: string) => getBackendClass(b) === 'a';
  const isClassB = (b?: string) => getBackendClass(b) === 'b';
  const backendLabel = (b?: string) => BACKEND_LABELS[b || 'aionui'] || b || 'AionUi';
  const backendClassLabel = (b?: string) => {
    const cls = getBackendClass(b);
    if (cls === 'a') return 'class-a self-routing';
    if (cls === 'team') return 'team';
    return 'class-b orchestrator + members';
  };

  // ── Inline styles for mockup components not in index.css ──
  const nodeCardStyle: React.CSSProperties = {
    border: '1px solid var(--border-subtle)',
    borderRadius: 'var(--radius-md)',
    padding: '9px',
    margin: '6px 0',
  };

  const nodeBadgeStyle: React.CSSProperties = {
    background: 'var(--accent-soft)',
    color: 'var(--accent-text)',
    borderRadius: '6px',
    padding: '1px 8px',
    fontSize: '11px',
    marginRight: '6px',
  };

  const depStyle: React.CSSProperties = {
    fontSize: '11px',
    color: 'var(--text-muted)',
  };

  const gateStyle: React.CSSProperties = {
    fontSize: '10px',
    color: 'var(--text-muted)',
    border: '1px solid var(--border-subtle)',
    borderRadius: '5px',
    padding: '1px 5px',
    marginLeft: '4px',
  };

  const planmetaStyle: React.CSSProperties = {
    display: 'grid',
    gridTemplateColumns: '1fr 1fr',
    gap: '6px 16px',
    fontSize: '12px',
    margin: '6px 0',
  };

  const labStyle: React.CSSProperties = {
    color: 'var(--text-muted)',
  };

  const sessgrpStyle: React.CSSProperties = {
    fontSize: '11px',
    color: 'var(--text-muted)',
    margin: '8px 0 2px',
    fontWeight: 600,
  };

  const newPillStyle: React.CSSProperties = {
    background: 'var(--accent-soft)',
    color: 'var(--accent-text)',
    border: '1px solid var(--accent-border)',
    borderRadius: '9999px',
    padding: '1px 9px',
    fontSize: '11px',
    display: 'inline-block',
  };

  // ── Render ──
  return (
    <div>
      {/* ── Page header ── */}
      <div className="page-header">
        <div className="page-header-titles">
          <h2>Plan</h2>
          <div className="subtitle">
            A complete plan: goal, worktree decision, agents (existing or new),
            DAG with dependencies, success criteria, then approve.
          </div>
        </div>
      </div>

      {/* ── Brain banner ── */}
      <div className="banner" style={{ marginBottom: 12 }}>
        🧠 Brain decomposes (frontier model) at every create/change: ① promote-from-chat · ② new plan here · ③ refine-with-brain · ④ append / cross-project node · ⑤ trigger (cron run_task). Each re-decomposes the DAG.
      </div>

      {/* ── Loading state ── */}
      {loading && (
        <div className="empty-state"><h3>Loading plans…</h3></div>
      )}

      {/* ── Error state ── */}
      {!loading && error && (
        <div className="empty-state">
          <h3>Error loading plans</h3>
          <pre className="error">{error}</pre>
          <button className="btn btn-sm" onClick={() => {
            setLoading(true); setError(null);
            fetch('/api/plans')
              .then(r => r.json())
              .then(data => { setPlans(Array.isArray(data) ? data : []); setLoading(false); })
              .catch(err => { setError(err.message); setLoading(false); });
          }}>Retry</button>
        </div>
      )}

      {/* ── Empty state ── */}
      {!loading && !error && plans.length === 0 && (
        <div className="empty-state"><h3>No plans yet</h3><p>Create a new plan using one of the options in the sidebar.</p></div>
      )}

      {/* ── Main panel ── */}
      {!loading && !error && plans.length > 0 && (
        <div className="panel" style={{ display: 'grid', gridTemplateColumns: '190px 1fr', gap: 10 }}>

          {/* ═══════════════════════════════════════════════════════════════
             LEFT SIDEBAR — plan list grouped by project
             ═══════════════════════════════════════════════════════════════ */}
          <div data-testid="plan-list">
            {projectKeys.map(key => (
              <div key={key}>
                <div style={sessgrpStyle}>▸ {projectLabel(key)}</div>
                {grouped[key].map(p => {
                  const isActive = activePlan?.plan_id === p.plan_id;
                  const isFromChat = !!p.source_thread;
                  return (
                    <div
                      key={p.plan_id}
                      className={`nav-item ${isActive ? 'active' : ''}`}
                      onClick={() => selectPlan(p)}
                      data-testid={`plan-list-item-${p.plan_id}`}
                    >
                      <span className="nav-label">{p.title}</span>
                      <span className="nav-meta">
                        {isFromChat ? (
                          <span style={newPillStyle}>from chat</span>
                        ) : p.ratified ? (
                          <span className="pill pill-done">ratified</span>
                        ) : (
                          <span className="pill pill-queued">draft</span>
                        )}
                      </span>
                    </div>
                  );
                })}
              </div>
            ))}

            {/* New plan action items */}
            <div
              className="nav-item nav-item-action"
              onClick={() => openNewPlanEditor('same')}
              data-testid="plan-tab-new"
            >
              ＋ new plan, same worktree
            </div>
            <div
              className="nav-item nav-item-action"
              style={{ marginTop: 6 }}
              onClick={() => openNewPlanEditor('new')}
            >
              ＋ new worktree + plan
            </div>

            {/* New plan inline editor */}
            {showNewPlanEditor && (
              <div className="inline-form" style={{ marginTop: 6 }}>
                <input
                  className="input"
                  placeholder="Plan title"
                  value={newPlanTitle}
                  onChange={e => setNewPlanTitle(e.target.value)}
                  data-testid="plan-title-input"
                />
                {newPlanType === 'same' && (
                  <>
                    <input
                      className="input"
                      placeholder="Project ID (optional)"
                      value={newPlanProjectId}
                      onChange={e => setNewPlanProjectId(e.target.value)}
                      data-testid="plan-project-input"
                    />
                    <textarea
                      className="textarea"
                      rows={3}
                      placeholder="Goal — what to build"
                      value={newPlanGoal}
                      onChange={e => setNewPlanGoal(e.target.value)}
                      data-testid="plan-goal-textarea"
                    />
                    <textarea
                      className="textarea"
                      rows={2}
                      placeholder="Spec — constraints/shape/acceptance (optional)"
                      value={newPlanSpec}
                      onChange={e => setNewPlanSpec(e.target.value)}
                      data-testid="plan-spec-textarea"
                    />
                    <div className="tiny muted" style={{ marginTop: -4 }}>
                      Becomes the learnable artifact the ratchet can optimize
                    </div>
                    <textarea
                      className="textarea"
                      rows={2}
                      placeholder="Quality intent — how to judge it (optional)"
                      value={newPlanIntent}
                      onChange={e => setNewPlanIntent(e.target.value)}
                      data-testid="plan-quality-intent-textarea"
                    />
                    <div className="tiny muted" style={{ marginTop: -4 }}>
                      Feeds L1-L4 check generation, grounded in memory, ratified below
                    </div>
                    <label style={{ marginBottom: 2 }}>Backend</label>
                    <select
                      className="select"
                      value={newPlanBackend}
                      onChange={e => setNewPlanBackend(e.target.value)}
                      data-testid="plan-backend-select"
                    >
                      <optgroup label="Self-orchestrating (a) · no orchestrator">
                        <option value="hermes">Hermes (self-routing)</option>
                        <option value="opencode_omo">OpenCode+OMO (self-routing)</option>
                      </optgroup>
                      <optgroup label="Single-agent (b) · orchestrator + members">
                        <option value="opencode">OpenCode (plain)</option>
                        <option value="claude-code">Claude Code</option>
                        <option value="codex">Codex</option>
                        <option value="gemini">Gemini</option>
                      </optgroup>
                      <optgroup label="Team">
                        <option value="aionui">AionUi (team · pick members)</option>
                      </optgroup>
                    </select>
                  </>
                )}
                {newPlanType === 'new' && (
                  <>
                    <input
                      className="input"
                      placeholder="Branch name"
                      value={newPlanBranch}
                      onChange={e => setNewPlanBranch(e.target.value)}
                    />
                    <input
                      className="input"
                      placeholder="Project ID (optional)"
                      value={newPlanProjectId}
                      onChange={e => setNewPlanProjectId(e.target.value)}
                    />
                  </>
                )}
                <div className="actions">
                  <button
                    className="btn btn-primary btn-sm"
                    onClick={propose}
                    disabled={!newPlanTitle.trim()}
                    data-testid="plan-create-btn"
                  >
                    Create
                  </button>
                  <button
                    className="btn btn-sm"
                    onClick={() => setShowNewPlanEditor(false)}
                    data-testid="plan-cancel-btn"
                  >
                    Cancel
                  </button>
                </div>
              </div>
            )}
          </div>

          {/* ═══════════════════════════════════════════════════════════════
             RIGHT PANEL — selected plan detail
             ═══════════════════════════════════════════════════════════════ */}
          {!activePlan ? (
            <div className="empty-state" style={{ minHeight: 200 }}>
              <h3>Select a plan</h3>
              <p>Choose a plan from the sidebar to view details.</p>
            </div>
          ) : (
            <div id="planBody" data-testid="plan-detail">
              {/* ── Title + Status pill ── */}
              <div className="row between">
                <b style={{ fontSize: 'var(--text-md)' }}>{activePlan.title}</b>
                <span className={`pill ${activePlan.ratified ? 'pill-done' : 'pill-queued'}`} data-testid="plan-status-pill">
                  {activePlan.ratified ? 'ratified' : 'draft'}
                </span>
              </div>
              <div className="tiny muted">
                {activePlan.source_thread
                  ? `derived from chat thread "${activePlan.source_thread}" · `
                  : ''}
                plan brain: local-ovms/qwen3-8b-int4
              </div>

              <div className="divider" />

              {/* ── Goal ── */}
              <div className="small"><b>Goal</b> <span className="tiny muted">→ plan brain decomposes</span></div>
              <div className="small muted" style={{ marginTop: 4 }}>
                {activePlan.description || 'No description provided.'}
              </div>
              {(activePlan as any).spec && (
                <>
                  <div className="small" style={{ marginTop: 8 }}><b>Spec</b> <span className="tiny muted">optional · the learnable artifact the ratchet can optimize</span></div>
                  <div className="small muted">{(activePlan as any).spec}</div>
                </>
              )}
              {(activePlan as any).quality_intent && (
                <>
                  <div className="small" style={{ marginTop: 8 }}><b>Quality intent</b> <span className="tiny muted">optional · feeds L1–L4 check generation (grounded in memory, ratified below)</span></div>
                  <div className="small muted">{(activePlan as any).quality_intent}</div>
                </>
              )}

              <div className="divider" />

              {/* ── Workspace / worktree ── */}
              <div className="row between">
                <span className="small"><b>Workspace / worktree</b></span>
                <button className="btn btn-tiny" onClick={toggleWorktreeEditor}>
                  change
                </button>
              </div>

              {!showWorktreeEditor ? (
                <div style={planmetaStyle} id="wtArea">
                  <div>
                    <span style={labStyle}>Project</span>{' '}
                    {activePlan.project_id || '—'}
                  </div>
                  <div>
                    <span style={labStyle}>Worktree</span>{' '}
                    <span className="mono">{activePlan.worktree_id || '—'}</span>
                  </div>
                  <div>
                    <span style={labStyle}>Branch</span>{' '}
                    <span className="mono">{activePlan.worktree_id || '—'}</span>
                  </div>
                  <div>
                    <span style={labStyle}>Kind</span> work
                  </div>
                </div>
              ) : (
                <div className="editor" id="wtEditor">
                  <label>Project</label>
                  <select
                    className="select"
                    value={wtProject}
                    onChange={e => setWtProject(e.target.value)}
                  >
                    <option value="">— select project —</option>
                    {projectKeys.filter(k => k !== '__no_project').map(k => (
                      <option key={k} value={k}>{k}</option>
                    ))}
                  </select>

                  <label>Worktree strategy</label>
                  <div className="seg">
                    <button
                      className={wtStrategy === 'create' ? 'on' : ''}
                      onClick={() => setWtStrategy('create')}
                    >
                      Create new worktree
                    </button>
                    <button
                      className={wtStrategy === 'reuse' ? 'on' : ''}
                      onClick={() => setWtStrategy('reuse')}
                    >
                      Reuse existing worktree
                    </button>
                  </div>

                  <label>Branch name</label>
                  <input
                    className="input"
                    value={wtBranch}
                    onChange={e => setWtBranch(e.target.value)}
                    placeholder="branch name"
                  />

                  <div className="row" style={{ gap: 6, marginTop: 8 }}>
                    <button className="btn btn-primary btn-tiny" onClick={toggleWorktreeEditor}>
                      Save
                    </button>
                    <button className="btn btn-tiny" onClick={toggleWorktreeEditor}>
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              <div className="divider" />

              {/* ── Plan DAG section ── */}
              <div className="row between">
                <span className="small">
                  <b>Plan DAG — chunks (each node = a backend team; backend class determines the routing pattern)</b>
                </span>
                <div className="row" style={{ gap: 4 }}>
                  <button
                    className="btn btn-tiny"
                    onClick={() => {
                      setShowAppendEditor(true); setShowNodeEditor(false); setShowCrossEditor(false);
                    }}
                  >
                    ＋ Append node
                  </button>
                  <button
                    className="btn btn-tiny"
                    onClick={() => {
                      setShowCrossEditor(true); setShowNodeEditor(false); setShowAppendEditor(false);
                    }}
                  >
                    ＋ Cross-project node
                  </button>
                </div>
              </div>
              <div className="tiny muted" style={{ marginBottom: 4 }}>
                worktree: <span className="mono">{activePlan.worktree_id || '—'}</span> · class-a self-routing (Hermes/OpenCode+OMO) = Conductor sends goal, tool self-decomposes · class-b (OpenCode plain/Claude Code/Codex/Gemini) = orchestrator delegates to members · Conductor commits + tags <span className="mono">node-&lt;id&gt;</span> after each
              </div>

              {/* DAG nodes */}
              <div id="dag">
                {activePlan.nodes.length === 0 ? (
                  <div className="tiny muted" style={{ padding: 8 }}>
                    No nodes yet. Append a node to start building the DAG.
                  </div>
                ) : (
                  activePlan.nodes.map((n, idx) => {
                    const isNewCfg = n.status === 'new' || !n.status;
                    return (
                      <div
                        key={n.node_id}
                        style={{
                          ...nodeCardStyle,
                          ...(isNewCfg ? { borderColor: 'rgba(118, 185, 0, 0.6)', background: 'var(--accent-soft)' } : {}),
                        }}
                        className={isNewCfg ? 'newcfg' : ''}
                        data-testid={`node-card-${idx}`}
                      >
                        <div className="row between">
                          <div>
                            <span style={nodeBadgeStyle}>{idx + 1}</span>
                            <b style={{ fontSize: 'var(--text-sm)' }}>{n.title}</b>{' '}
                            <span className="tag" data-testid="node-backend-tag">{backendLabel(n.backend_type)}</span>{' '}
                            <span className="tag" data-testid="node-class-tag">{backendClassLabel(n.backend_type)}</span>{' '}
                            <span className="tag" data-testid="node-orchestrator-tag">{orchestratorTag(n)}</span>
                          </div>
                          <span style={depStyle}>
                            {depLabel(n)}
                            <button
                              className="btn btn-tiny"
                              style={{ marginLeft: 6 }}
                              onClick={() => openEditNode(n)}
                              title="Edit this node"
                            >
                              ✎
                            </button>
                          </span>
                        </div>
                        <div className="tiny muted" style={{ marginTop: 4 }}>
                          backend: <strong>{backendLabel(n.backend_type)}</strong>
                          {' · '}
                          members: {(n.members && n.members.length > 0) ? n.members.join(', ') : '—'}
                          {isNewCfg && (
                            <span style={{ marginLeft: 6, ...newPillStyle }}>NEW agent_config</span>
                          )}
                        </div>
                        <div className="tiny" style={{ marginTop: 2 }}>
                          <b style={{ color: 'var(--text-muted)' }}>success:</b>{' '}
                          {n.success_criterion || n.description || 'criteria not specified'}
                          <span style={gateStyle}>
                            → Conductor commits node-{idx + 1}
                          </span>
                        </div>
                      </div>
                    );
                  })
                )}
              </div>

              {/* ── Edit node editor ── */}
              {showNodeEditor && (() => {
                const editIdx = activePlan.nodes.findIndex(n => n.node_id === editNodeId);
                const backendIsA = isClassA(editBackend);
                const backendIsB = isClassB(editBackend);
                const backendIsOpenCode = editBackend === 'opencode' || editBackend === 'opencode_omo';
                return (
                  <div className="editor" id="editNode">
                    <b className="small">Edit node {editIdx >= 0 ? editIdx + 1 : '?'}</b>

                    <label>Backend</label>
                    <select
                      className="select"
                      value={editBackend}
                      onChange={e => {
                        const val = e.target.value;
                        setEditBackend(val);
                        if (val === 'opencode' || val === 'opencode_omo') {
                          setEditOcType(val === 'opencode_omo' ? 'opencode_omo' : 'opencode');
                        }
                      }}
                      data-testid="node-edit-backend-select"
                    >
                      <optgroup label="Self-orchestrating (a) · no orchestrator">
                        <option value="hermes">Hermes (self-routing)</option>
                        <option value="opencode_omo">OpenCode+OMO (self-routing)</option>
                      </optgroup>
                      <optgroup label="Single-agent (b) · orchestrator + members">
                        <option value="opencode">OpenCode (plain)</option>
                        <option value="claude-code">Claude Code</option>
                        <option value="codex">Codex</option>
                        <option value="gemini">Gemini</option>
                      </optgroup>
                      <optgroup label="Team">
                        <option value="aionui">AionUi (team · pick members)</option>
                      </optgroup>
                    </select>

                    {backendIsA && (
                      <div className="banner" style={{ marginTop: 6 }}>
                        Self-orchestrating: no orchestrator spawned; Conductor sends the goal; the tool self-routes.
                      </div>
                    )}
                    {backendIsB && (
                      <div className="banner" style={{ marginTop: 6 }}>
                        AionUi Leader (orchestrator) coordinates these members.
                      </div>
                    )}

                    {/* Members multi-select: hidden for class-a, shown for class-b/team */}
                    {!backendIsA && (
                      <>
                        <label>
                          Team members (multi-select) — the built-in AionUi orchestrator always leads; you pick the specialist members
                        </label>
                        <select
                          multiple
                          size={4}
                          className="select"
                          value={editMembers}
                          onChange={e => handleMultiChange(e, setEditMembers)}
                          data-testid="node-edit-members"
                        >
                          {agentConfigs.map(ac => (
                            <option key={ac.agent_config_id} value={ac.agent_config_id}>
                              {ac.agent_config_id} ({ac.role})
                            </option>
                          ))}
                        </select>
                        <div className="tiny muted">
                          {editMembers.length} member{editMembers.length !== 1 ? 's' : ''} selected. Conductor sends ONE brief to the orchestrator (single entry point); orchestrator handles delegation.
                        </div>
                      </>
                    )}

                    {/* OpenCode type toggle */}
                    {backendIsOpenCode && (
                      <>
                        <label>OpenCode type</label>
                        <div className="seg" style={{ marginBottom: 6 }}>
                          <button
                            className={editOcType === 'opencode' ? 'on' : ''}
                            onClick={() => {
                              setEditOcType('opencode');
                              setEditBackend('opencode');
                            }}
                          >
                            opencode (plain, single-agent)
                          </button>
                          <button
                            className={editOcType === 'opencode_omo' ? 'on' : ''}
                            onClick={() => {
                              setEditOcType('opencode_omo');
                              setEditBackend('opencode_omo');
                            }}
                          >
                            opencode_omo (self-orchestrating)
                          </button>
                        </div>

                        {/* Per-worktree config for OpenCode nodes */}
                        <label>Model override (optional)</label>
                        <input
                          className="input"
                          value={editModel}
                          onChange={e => setEditModel(e.target.value)}
                          placeholder="e.g. deepseek-v4-flash"
                          data-testid="node-edit-model"
                        />

                        <label>Appended prompt (optional)</label>
                        <textarea
                          className="textarea"
                          rows={2}
                          value={editAppendedPrompt}
                          onChange={e => setEditAppendedPrompt(e.target.value)}
                          placeholder="Additional instructions for this node..."
                          data-testid="node-edit-prompt"
                        />

                        <label>Permissions</label>
                        <div className="row" style={{ gap: 8, flexWrap: 'wrap' }}>
                          <span className="tiny">edit</span>
                          <select
                            style={{ width: 'auto', padding: '3px 6px', fontSize: 11 }}
                            value={editPermissions.edit}
                            onChange={e => setEditPermissions(p => ({ ...p, edit: e.target.value }))}
                          >
                            <option value="allow">allow</option>
                            <option value="deny">deny</option>
                            <option value="ask">ask</option>
                          </select>
                          <span className="tiny">bash</span>
                          <select
                            style={{ width: 'auto', padding: '3px 6px', fontSize: 11 }}
                            value={editPermissions.bash}
                            onChange={e => setEditPermissions(p => ({ ...p, bash: e.target.value }))}
                          >
                            <option value="allow">allow</option>
                            <option value="deny">deny</option>
                            <option value="ask">ask</option>
                          </select>
                          <span className="tiny">webfetch</span>
                          <select
                            style={{ width: 'auto', padding: '3px 6px', fontSize: 11 }}
                            value={editPermissions.webfetch}
                            onChange={e => setEditPermissions(p => ({ ...p, webfetch: e.target.value }))}
                          >
                            <option value="allow">allow</option>
                            <option value="deny">deny</option>
                          </select>
                        </div>
                      </>
                    )}

                    <label>Depends on (node ids, multi-select)</label>
                    <select
                      multiple
                      size={3}
                      className="select"
                      value={editDepends}
                      onChange={e => handleMultiChange(e, setEditDepends)}
                    >
                      {activePlan.nodes.map((node, i) => (
                        <option key={node.node_id} value={node.node_id}>
                          {i + 1} — {node.title}
                        </option>
                      ))}
                    </select>

                    <label>Success criterion</label>
                    <textarea
                      className="textarea"
                      rows={2}
                      value={editSuccessCriterion}
                      onChange={e => setEditSuccessCriterion(e.target.value)}
                      placeholder="Describe the success criteria..."
                    />

                    <div className="row" style={{ gap: 6, marginTop: 8 }}>
                      <button className="btn btn-primary btn-tiny" onClick={saveNodeEdit} data-testid="node-save-btn">
                        Save node
                      </button>
                      <button className="btn btn-tiny" onClick={() => { setShowNodeEditor(false); setEditNodeId(null); }}>
                        Cancel
                      </button>
                    </div>
                  </div>
                );
              })()}

              {/* ── Append node editor ── */}
              {showAppendEditor && (
                <div className="editor" id="appendNode">
                  <b className="small">＋ Append node (same worktree) · re-decomposes the live plan</b>

                  <label>Team members (multi-select) — orchestrator leads automatically</label>
                  <select
                    multiple
                    size={4}
                    className="select"
                    value={appendMembers}
                    onChange={e => handleMultiChange(e, setAppendMembers)}
                  >
                    {agentConfigs.map(ac => (
                      <option key={ac.agent_config_id} value={ac.agent_config_id}>
                        {ac.agent_config_id} ({ac.role})
                      </option>
                    ))}
                  </select>

                  <label>Depends on (node ids, multi-select)</label>
                  <select
                    multiple
                    size={3}
                    className="select"
                    value={appendDepends}
                    onChange={e => handleMultiChange(e, setAppendDepends)}
                  >
                    {activePlan.nodes.map((node, i) => (
                      <option key={node.node_id} value={node.node_id}>
                        {i + 1} — {node.title}
                      </option>
                    ))}
                  </select>

                  <label>Task</label>
                  <textarea
                    className="textarea"
                    rows={2}
                    value={appendTask}
                    onChange={e => setAppendTask(e.target.value)}
                    placeholder="What should this node do?"
                  />

                  <label>Success criterion</label>
                  <input
                    className="input"
                    value={appendSuccessCriterion}
                    onChange={e => setAppendSuccessCriterion(e.target.value)}
                    placeholder="e.g. all tests pass"
                  />

                  <div className="row" style={{ gap: 6, marginTop: 8 }}>
                    <button
                      className="btn btn-primary btn-tiny"
                      onClick={appendNode}
                      disabled={!appendTask.trim()}
                    >
                      Append &amp; spawn
                    </button>
                    <button
                      className="btn btn-tiny"
                      onClick={() => {
                        setShowAppendEditor(false);
                        setAppendMembers([]);
                        setAppendDepends([]);
                        setAppendTask('');
                        setAppendSuccessCriterion('');
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              {/* ── Cross-project node editor ── */}
              {showCrossEditor && (
                <div className="editor" id="crossNode">
                  <b className="small">＋ Cross-project node (different repo, same live plan) · re-decomposes</b>

                  <label>Target project</label>
                  <select
                    className="select"
                    value={crossTargetProject}
                    onChange={e => setCrossTargetProject(e.target.value)}
                  >
                    <option value="">— select project —</option>
                    {projectKeys.filter(k => k !== '__no_project' && k !== activePlan.project_id).map(k => (
                      <option key={k} value={k}>{k}</option>
                    ))}
                  </select>

                  <label>Worktree</label>
                  <div className="seg">
                    <button
                      className={crossWorktreeStrategy === 'reuse' ? 'on' : ''}
                      onClick={() => setCrossWorktreeStrategy('reuse')}
                    >
                      Reuse existing
                    </button>
                    <button
                      className={crossWorktreeStrategy === 'create' ? 'on' : ''}
                      onClick={() => setCrossWorktreeStrategy('create')}
                    >
                      Create new
                    </button>
                  </div>

                  <label>Team members (multi-select) — orchestrator leads automatically</label>
                  <select
                    multiple
                    size={3}
                    className="select"
                    value={crossMembers}
                    onChange={e => handleMultiChange(e, setCrossMembers)}
                  >
                    {agentConfigs.map(ac => (
                      <option key={ac.agent_config_id} value={ac.agent_config_id}>
                        {ac.agent_config_id} ({ac.role})
                      </option>
                    ))}
                  </select>

                  <label>Depends on (node ids, multi-select)</label>
                  <select
                    multiple
                    size={3}
                    className="select"
                    value={crossDepends}
                    onChange={e => handleMultiChange(e, setCrossDepends)}
                  >
                    {activePlan.nodes.map((node, i) => (
                      <option key={node.node_id} value={node.node_id}>
                        {i + 1} — {node.title}
                      </option>
                    ))}
                  </select>

                  <label>Task</label>
                  <textarea
                    className="textarea"
                    rows={2}
                    value={crossTask}
                    onChange={e => setCrossTask(e.target.value)}
                    placeholder="Describe the cross-project task…"
                  />

                  <div className="banner" style={{ marginTop: 8 }}>
                    ℹ Conductor brokers cross-project — this node runs in {crossTargetProject || 'the target project'}'s worktree but stays part of this plan.
                  </div>

                  <div className="row" style={{ gap: 6 }}>
                    <button
                      className="btn btn-primary btn-tiny"
                      onClick={() => setShowCrossEditor(false)}
                    >
                      Add cross-project node
                    </button>
                    <button
                      className="btn btn-tiny"
                      onClick={() => {
                        setShowCrossEditor(false);
                        setCrossTargetProject('');
                        setCrossMembers([]);
                        setCrossDepends([]);
                        setCrossTask('');
                      }}
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}

              <div className="divider" />

              {/* ── Execution settings ── */}
              <div style={planmetaStyle}>
                <div>
                  <span style={labStyle}>Backend</span>{' '}
                  {activePlan.nodes[0]?.backend_type
                    ? `${backendLabel(activePlan.nodes[0].backend_type)} (${backendClassLabel(activePlan.nodes[0].backend_type)})`
                    : 'AionUi (team)'} {'·'} {activePlan.nodes.length} node{activePlan.nodes.length !== 1 ? 's' : ''}
                </div>
                <div>
                  <span style={labStyle}>Autonomy</span> auto (non-stop)
                </div>
                <div>
                  <span style={labStyle}>Est. budget</span>{' '}
                  ~{Math.max(activePlan.nodes.length * 5, 1)}k tok
                </div>
                <div>
                  <span style={labStyle}>Review</span>{' '}
                  L1 (substrate janitor) + L2-L4 (Conductor)
                </div>
              </div>

              {/* ── Runs section ── */}
              <div className="divider" />
              <div className="small"><b>Runs</b></div>
              {runs.length === 0 ? (
                <div className="tiny muted" style={{ padding: '6px 0' }}>
                  No runs yet. Create one to start execution.
                </div>
              ) : (
                runs.map(run => (
                  <div
                    key={run.run_id}
                    style={{
                      border: '1px solid var(--border-subtle)',
                      borderRadius: 'var(--radius-md)',
                      padding: '9px',
                      margin: '6px 0',
                    }}
                  >
                    <div className="row between">
                      <span className="mono small">{run.run_id}</span>
                      <span className={`pill ${runStatePillClass(run.state)}`}>
                        {runStateLabel(run.state)}
                      </span>
                    </div>
                    <div className="tiny muted" style={{ marginTop: 4 }}>
                      created: {run.created_at || '—'}
                      {run.approved_at ? ` · approved: ${run.approved_at}` : ''}
                      {run.finished_at ? ` · finished: ${run.finished_at}` : ''}
                    </div>
                    {run.note && (
                      <div className="tiny muted" style={{ marginTop: 2 }}>
                        note: {run.note}
                      </div>
                    )}
                    <div className="row" style={{ gap: 6, marginTop: 6 }}>
                      {run.state === 'created' && (
                        <button
                          className="btn btn-tiny btn-primary"
                          onClick={() => approveRun(run)}
                        >
                          Approve
                        </button>
                      )}
                      {run.state === 'approved' && (
                        <button
                          className="btn btn-tiny btn-primary"
                          onClick={() => startRun(run)}
                        >
                          Start
                        </button>
                      )}
                    </div>
                  </div>
                ))
              )}

              {/* ── Action buttons ── */}
              <div className="row" style={{ gap: 6, marginTop: 8 }}>
                {!activePlan.ratified ? (
                  <>
                    <button
                      className="btn btn-primary"
                      onClick={() => ratifyPlan(activePlan)}
                      data-testid="plan-ratify-btn"
                    >
                      ✓ Ratify plan
                    </button>
                    <button
                      className="btn"
                      onClick={async () => {
                        const res = await fetch(`/api/plans/${activePlan.plan_id}`);
                        if (res.ok) {
                          const updated: Plan = await res.json();
                          setPlans(prev => prev.map(p => p.plan_id === activePlan.plan_id ? updated : p));
                          setActivePlan(updated);
                        }
                      }}
                    >
                      Save draft
                    </button>
                  </>
                ) : (
                  <button
                    className="btn btn-primary"
                    onClick={() => createRun(activePlan)}
                    data-testid="plan-create-run-btn"
                  >
                    Create Run
                  </button>
                )}
              </div>

              <div className="divider" />

              {/* ── Refine input ── */}
              <div className="row" style={{ gap: 6 }}>
                <button className="btn" title="image for VLM review">
                  🖼️
                </button>
                <input
                  className="input"
                  style={{ flex: 1 }}
                  value={refineInput}
                  onChange={e => setRefineInput(e.target.value)}
                  placeholder="refine the plan with the brain…"
                  onKeyDown={e => { if (e.key === 'Enter') refinePlan(); }}
                />
                <button
                  className="btn"
                  onClick={refinePlan}
                  disabled={!refineInput.trim()}
                >
                  Send
                </button>
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
