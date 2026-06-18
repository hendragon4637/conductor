# AIPC Conductor

Local-first multi-CLI agent orchestration with declarative routing,
native desktop work surface, and a thin project management UI.

- Project = git repo at /opt/aipc/conductor/workspace/<name>
- Session = git branch
- Task = flexible work unit
- Trace = one CLI invocation = one "room"

Sibling to /opt/aipc/orchestrator/ (preserved untouched).

## Two UI surfaces (since week 4)

- **Tauri GUI** (default): `/opt/aipc/conductor/gui/` — desktop app with embedded PTY tabs. Run via `cd gui && npm run tauri dev`.
- **Browser fallback**: `/opt/aipc/conductor/ui/` — same React code, runs at `localhost:3090`. Spawn opens external `gnome-terminal`. Run via `cd ui && npm run dev`.

Both surfaces talk to the same FastAPI backend on `:8090`.
