# Architecture Decision Records (ADR)

## ADR-001: Conductor is a new project, not a rewrite of orchestrator
Date: <fill in>
Status: Accepted
Context: Existing /opt/aipc/orchestrator/ uses CopilotKit AG-UI and CAO patterns we've rejected.
Decision: Build /opt/aipc/conductor/ as separate project. Run side-by-side.
Consequences: Two services until Conductor reaches Stage 1, then deprecate orchestrator.

## ADR-002: Project = git repo, Session = git branch
Date: <fill in>
Status: Accepted
Decision: Identity 1:1 with git. No drift.
Consequences: Sessions get free version control. Branch deletion implies session done.

## ADR-003: One CLI invocation = one trace = one "room"
Date: <fill in>
Status: Accepted
Decision: No multi-pane rooms. Handoffs create new traces.
Consequences: Easier per-config ratchet signal. Tasks group traces.

## ADR-004: Routing rules are declarative (in DB), not code
Date: <fill in>
Status: Accepted
Decision: agent_configs.routing_rules JSONB column.
Consequences: New patterns = new YAML, no LangGraph code change.

## ADR-005: Native desktop terminal is the work surface
Date: <fill in>
Status: Accepted
Decision: FastAPI spawns gnome-terminal -- opencode ... with env vars.
Consequences: Cannot use Conductor remotely without RDP/Sunshine; trade-off accepted.
