import sqlite3
import json


class AionUiReader:
    """Read-only SQLite reader for AionUi's backend database.

    Opens the database with mode=ro so Conductor can never corrupt
    AionUi state.
    """

    def __init__(self, db_path: str):
        self._conn = sqlite3.connect(
            f"file:{db_path}?mode=ro", uri=True, check_same_thread=False
        )
        self._conn.row_factory = sqlite3.Row

    def close(self):
        self._conn.close()

    def conversations(self, limit: int = 50) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM conversations ORDER BY updated_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]

    def messages_for(self, conversation_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM messages WHERE conversation_id = ? ORDER BY created_at",
            (conversation_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            # Parse JSON content field
            if isinstance(d.get("content"), str):
                try:
                    d["content"] = json.loads(d["content"])
                except json.JSONDecodeError:
                    pass
            result.append(d)
        return result

    def team_tasks(self, team_id: str) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM team_tasks WHERE team_id = ? ORDER BY created_at",
            (team_id,),
        ).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            for field in ("blocked_by", "blocks", "metadata"):
                if isinstance(d.get(field), str):
                    try:
                        d[field] = json.loads(d[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            result.append(d)
        return result

    def mailbox(self, team_id: str) -> list[dict]:
        try:
            rows = self._conn.execute(
                "SELECT * FROM mailbox WHERE team_id = ? ORDER BY created_at",
                (team_id,),
            ).fetchall()
            return [dict(r) for r in rows]
        except sqlite3.OperationalError:
            return []

    def agents(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM agent_metadata ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]

    def assistants(self) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM assistant_overrides ORDER BY sort_order"
        ).fetchall()
        return [dict(r) for r in rows]
