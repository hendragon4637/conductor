# CLI Log Sources — Empirical Discovery

## Per the spec (File 15.1), paths verified on 2026-06-06:

### 1. AionUi SQLite DB (conversation/team/mailbox state)
- **Path:** `~/.config/AionUi/aionui/aionui-backend.db`
- **Alias:** `/opt/aipc/data/aionui-backend.db` (symlinked)
- **Format:** SQLite, read-only access (mode=ro)
- **Tables:** `conversations`, `messages`, `teams`, `team_tasks`, `mailbox`, `agent_metadata`

### 2. OpenCode DB (per-session message/token data)
- **Path:** `~/.local/share/opencode/opencode.db`
- **Format:** SQLite (not JSONL as originally assumed)
- **Key tables:**
  - `session` — id, project_id, title, agent, model, timestamps
  - `message` — session_id, data (JSON with role, tokens {input,output,reasoning,cache}, cost, agent, model, providerID)
  - `part` — message_id, data (JSON with type, text for sub-message parts)
- **Token fields in message.data:** `tokens.input`, `tokens.output`, `tokens.reasoning`, `tokens.cache.read`, `tokens.cache.write`

### 3. OpenCode Structured Logs (server/tool-call events)
- **Path:** `~/.local/share/opencode/log/` — files named `YYYY-MM-DDTHHmmss.log`
- **Format:** Structured text log (key=value pairs), NOT JSONL
- **Parser:** `sources/cli_jsonl.py` adapts by querying OpenCode DB + tailing log files

### 4. OpenCode Storage (agent usage reminders)
- **Path:** `~/.local/share/opencode/storage/agent-usage-reminder/*.json`
- **Format:** JSON-per-file — `{sessionID, agentUsed, reminderCount, updatedAt}`

### Adaptation note
The spec originally described "Per-CLI JSONL logs", but the actual OpenCode CLI stores structured data in SQLite + key=value log files. The `cli_jsonl.py` source adapter queries the OpenCode DB for message/token data and tails the log directory for live events, normalizing both into the `Event` schema.
