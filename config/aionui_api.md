# AionUi API Surface (discovered 2026-05-29)

## REST Endpoints (port 40937)

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| GET | /api/agents | ✅ | Returns list of registered agent backends |
| GET | /api/assistants | ✅ | Returns built-in + user-created assistants |
| GET | /api/conversations | ✅ | List all conversations |
| GET | /api/conversations/{id} | ✅ | Single conversation details + status |
| GET | /api/conversations/{id}/messages | ✅ | Messages with types: text, thinking, acp_tool_call |
| POST | /api/conversations | ✅ | Create conversation (type, assistant_id, extra) |
| POST | /api/conversations/{id}/messages | ✅ | Send message (content, role) |
| GET | /api/teams | ✅ | List teams |
| POST | /api/teams | ✅ | Create team (name, agents[], workspace) |

## WebSocket (port 34931)

- **Status:** ⚠️ NOT DISCOVERABLE during v4 build
- Port 34931 is open (aioncore process) but returns 404 for all HTTP paths
- WS upgrade requests return 404 for all common paths tested
- **Workaround:** Use REST polling + SQLite read-only for event detection
- **Needs:** Inspect AionUi Electron renderer network traffic to find WS path

## SQLite Database

- **Path:** `/home/aipc/.config/AionUi/aionui/aionui-backend.db`
- **Access:** Read-only via `sqlite3.connect(f"file:{path}?mode=ro", uri=True)`
- **Tables used by Conductor:**
  - `conversations` — conversation metadata (id, status, extra as JSON)
  - `messages` — all messages with type/position/status/content
  - `acp_session` — agent session state with runtime config
  - `team_tasks` — team task tracking
  - `agent_metadata` — registered CLIs
  - `assistant_overrides` — user-created assistant configs
