# Architecture

See `/mnt/user-data/outputs/aipc_data_model_preview_v2.html` for the visual model.

Key invariants:
- Project = git repo. Session = git branch. Task = flexible work unit. Trace = one CLI invocation = one "room".
- Routing rules in agent_configs.routing_rules (JSONB).
- LangGraph orchestration is generic; patterns are data.

## Architectural pillars

1. **Project = repo, Session = branch, Task = work unit, Trace = room.** Identity 1:1 with git. No drift.
2. **Native PTY is the work surface.** CLIs spawn into PTY-backed terminal tabs inside the Tauri GUI by default; external `gnome-terminal` available as opt-in detach mode (ADR-015). The browser UI at `:3090` remains a read-mostly fallback that uses external-spawn only. No xterm.js inside *browser* — embedding is reserved for the Tauri desktop shell, where the webview is local and latency is zero (ADR-012 still applies to browser embedding).
3. **Routing rules are declarative (in DB), not code.** New patterns = new YAML, no LangGraph code change.
4. **LangGraph orchestration is generic; patterns are data.** The orchestration engine is pattern-agnostic; routing rules drive behavior.
5. **CLI-first, UI-read-mostly.** The UI observes and spawns; the CLI does the work.
6. **Completion is signed by the agent.** Agents emit `__CONTRIBUTION_RECEIPT__` to declare completion.
7. **Traces are immutable.** A trace records one CLI invocation. Handoffs create new traces.
8. **Evaluation is 3-track (pass/fail/ratchet).** Not all completions are equal; ratchet avoids regression.
9. **No agentic desktop control.** The system does not move mouse, take screenshots, or drive IDE UI.
10. **Receipt-based verification.** Completion is verified cryptographically via receipt markers.

## Rejected alternatives

| Alternative | Reason for rejection | Reference |
|---|---|---|
| Electron for desktop shell | Tauri is lighter (Rust, WebKitGTK, no Node runtime bundled); Electron requires Chromium bundle | ADR-015 |
| Embedded terminal (xterm.js) in the browser UI at `:3090` | Defeats native desktop principle; browser webview adds latency | ADR-012 (still rejected). NOTE: xterm.js inside the Tauri desktop shell is permitted under ADR-015 because the webview is local-process, not a remote browser. |
| WebSocket for PTY data flow | PTY data flows over Tauri IPC events, not WS; WS adds unnecessary overhead for local-process communication | ADR-015 |
| Agentic desktop control (mouse/keyboard/IDE) | Brittle, OS-dependent, high maintenance | ADR-012 |
| CopilotKit / AG-UI for orchestration UI | Over-engineered for a local-first tool; proprietary dependency | ADR-001 |
| In-browser terminal emulation via Xpra/Guacamole | Latency from remote display protocol; defeats native-desktop principle | ADR-012 |
