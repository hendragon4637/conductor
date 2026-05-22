from __future__ import annotations
import hashlib
import os
import re
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Any

from backend.db import queries


MEMORY_ROOT = Path(os.environ.get("MEMORY_ROOT", "/opt/aipc/conductor/memory"))
PREVIEW_LEN = 280


# ──────────────────────── frontmatter ────────────────────────

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    m = FRONTMATTER_RE.match(text)
    if not m:
        return {}, text
    meta_text = m.group(1)
    body = text[m.end():]
    meta: dict = {}
    for line in meta_text.split("\n"):
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, v = line.split(":", 1)
        k = k.strip()
        v = v.strip()
        if v.startswith("[") and v.endswith("]"):
            v = [item.strip().strip("'\"") for item in v[1:-1].split(",") if item.strip()]
        elif v in ("true", "false"):
            v = (v == "true")
        meta[k] = v
    return meta, body


def _emit_frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        if v is None:
            continue
        if isinstance(v, list):
            inner = ", ".join(str(item) for item in v)
            lines.append(f"{k}: [{inner}]")
        elif isinstance(v, bool):
            lines.append(f"{k}: {'true' if v else 'false'}")
        else:
            lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


# ──────────────────────── path helpers ────────────────────────

def _safe_slug(s: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]+", "_", s)


def _path_for(
    scope: str,
    *,
    memory_id: str,
    project_id: Optional[str] = None,
    agent_config_id: Optional[str] = None,
    session_id: Optional[str] = None,
) -> Path:
    if scope == "global":
        return MEMORY_ROOT / "global" / f"{memory_id}.md"
    if scope == "project":
        assert project_id, "project_id required for scope=project"
        return MEMORY_ROOT / "projects" / _safe_slug(project_id) / f"{memory_id}.md"
    if scope == "agent_config":
        assert agent_config_id, "agent_config_id required for scope=agent_config"
        return MEMORY_ROOT / "agent_configs" / _safe_slug(agent_config_id) / f"{memory_id}.md"
    if scope == "session":
        assert project_id and session_id, "project_id and session_id required for scope=session"
        return MEMORY_ROOT / "sessions" / _safe_slug(project_id) / _safe_slug(session_id) / f"{memory_id}.md"
    raise ValueError(f"unknown scope: {scope}")


# ──────────────────────── CRUD ────────────────────────

def _hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def create_memory(
    *,
    title: str,
    body: str,
    scope: str,
    project_id: Optional[str] = None,
    agent_config_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    source: str = "manual",
) -> dict:
    memory_id = secrets.token_hex(4)
    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    tags = tags or []

    meta = {
        "memory_id": memory_id,
        "title": title,
        "scope": scope,
        "project_id": project_id,
        "agent_config_id": agent_config_id,
        "session_id": session_id,
        "tags": tags,
        "created_at": now_iso,
        "updated_at": now_iso,
        "source": source,
    }
    text = _emit_frontmatter(meta) + body.strip() + "\n"

    path = _path_for(
        scope, memory_id=memory_id,
        project_id=project_id, agent_config_id=agent_config_id, session_id=session_id,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")

    content_hash = _hash(text)
    preview = (body.strip()[:PREVIEW_LEN] + "...") if len(body.strip()) > PREVIEW_LEN else body.strip()

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO agent_memory (
              memory_id, scope, project_id, agent_config_id, session_id,
              title, tags, source, file_path, content_hash, body_preview, active
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, TRUE)
            RETURNING *
            """,
            (memory_id, scope, project_id, agent_config_id, session_id,
             title, tags, source, str(path), content_hash, preview),
        )
        row = cur.fetchone()
        c.commit()
        return row


def list_memory(
    *,
    scope: Optional[str] = None,
    project_id: Optional[str] = None,
    agent_config_id: Optional[str] = None,
    session_id: Optional[str] = None,
    tags: Optional[list[str]] = None,
    active_only: bool = True,
) -> list[dict]:
    sql = "SELECT * FROM agent_memory WHERE TRUE"
    params: list[Any] = []
    if active_only:
        sql += " AND active"
    if scope:
        sql += " AND scope = %s"; params.append(scope)
    if project_id:
        sql += " AND project_id = %s"; params.append(project_id)
    if agent_config_id:
        sql += " AND agent_config_id = %s"; params.append(agent_config_id)
    if session_id:
        sql += " AND session_id = %s"; params.append(session_id)
    if tags:
        sql += " AND tags && %s"; params.append(tags)
    sql += " ORDER BY scope, created_at DESC"

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def read_memory_body(memory_id: str) -> Optional[str]:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("SELECT file_path FROM agent_memory WHERE memory_id = %s AND active", (memory_id,))
        row = cur.fetchone()
        if not row:
            return None
        p = Path(row["file_path"])
        if not p.is_file():
            return None
        text = p.read_text(encoding="utf-8")
        _, body = _parse_frontmatter(text)
        return body


def deactivate_memory(memory_id: str) -> bool:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "UPDATE agent_memory SET active = FALSE WHERE memory_id = %s RETURNING memory_id",
            (memory_id,),
        )
        return cur.fetchone() is not None


# ──────────────────────── assembly for spawn injection ────────────────────────

def assemble_for_spawn(
    *,
    project_id: str,
    session_id: Optional[str],
    agent_config_id: str,
) -> str:
    parts: list[str] = []

    def _section(label: str, rows: list[dict]) -> None:
        if not rows:
            return
        parts.append(f"\n### Memory -- {label}\n")
        for r in rows:
            body = read_memory_body(r["memory_id"]) or ""
            parts.append(f"#### {r['title']}\n{body.strip()}\n")

    _section("global",   list_memory(scope="global"))
    _section("project",  list_memory(scope="project", project_id=project_id))
    _section("agent_config", list_memory(scope="agent_config", agent_config_id=agent_config_id))
    if session_id:
        _section("session", list_memory(scope="session", project_id=project_id, session_id=session_id))

    if not parts:
        return ""
    return "\n## Agent Memory\n" + "\n".join(parts)
