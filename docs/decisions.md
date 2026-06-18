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

## ADR-015: Tauri GUI shell with embedded PTY (partially reverses ADR-012 and Pillar 2)

**Status:** Accepted (week 4)

**Context:**
Week 1–3 used `gnome-terminal` external spawn with native-desktop principle (Pillar 2: "Native desktop is the work surface. CLIs spawn into native terminal windows."). Week-1 retrospective documented:
- 60% receipt emission rate → ratchet input contamination
- Stale terminals accumulate; no per-trace lifecycle (`wmctrl` is fragile)
- t02 was killed mid-flight with no recovery
- `gnome-terminal` overwrites window title at startup → cannot identify rooms reliably
- Manual `UPDATE traces SET status='complete'` SQL needed for non-receipt traces

The original rejection in ADR-012 was about (a) agentic desktop controllers and (b) embedding native GUIs inside a *browser* via Xpra/Guacamole. The latency and feature-loss arguments applied specifically to browser embedding.

**Decision:**
Build a Tauri v2 desktop shell at `/opt/aipc/conductor/gui/` that:
1. Loads the existing React build inside a native WebKitGTK webview (zero rebuild of UI views)
2. Embeds PTY-based terminal tabs via `tauri-plugin-pty` + `xterm.js` for each spawned trace
3. Preserves the `gnome-terminal` external-spawn path as an opt-in "detach" mode for human-driven debugging
4. Calls the same FastAPI backend over HTTP — no backend logic moves into the GUI process

**What ADR-015 does NOT change:**
- Pillar 1 (Project=repo, Session=branch, Task=work unit, Trace=room) — unchanged
- Pillars 3–10 — unchanged
- ADR-012's rejection of agentic desktop control — still rejected
- ADR-012's rejection of browser-embedded GUIs (Xpra/Guacamole) — still rejected
- ADR-005 (native terminal as work surface) — refined: PTY is still native (real `/dev/ptmx`), still the work surface; the *window manager* of those terminals is now the Tauri app rather than the OS window manager

**Consequences:**
- Pillar 2 amended to: *"Native PTY is the work surface. Terminal panes live inside the Tauri GUI by default; external `gnome-terminal` available via opt-in detach mode. No browser-embedded terminals (xterm.js in browser is still rejected per ADR-012)."*
- The browser UI at `:3090` becomes a fallback / dev surface. Embedded-mode features (kill/focus/attach inside the app) are not available in browser mode; the browser still uses external-spawn fallback.
- Completion-source determinism improves: PTY exit code is observed directly by the GUI process and recorded as `completion_source='exit_code'` without polling.
- New dependencies: `tauri@^2`, `tauri-plugin-pty@^0.1`, `xterm@^5`, `xterm-addon-fit@^0.8`. Rust toolchain via `rustup`. Documented in file 02.

**Reversal criteria (for future ADRs):**
Revert this ADR only if:
- Tauri v2 abandons Linux/Wayland support, OR
- PTY input latency exceeds 50 ms p95 in measurement, OR
- The Tauri build process consistently fails on the IT15 hardware budget
None of these are currently expected.
