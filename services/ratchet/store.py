"""SQLite persistence for the ratchet service.

Own SQLite (data/ratchet.db) — derived, disposable state. Never writes to
the production Postgres. Schema per guide 01.2: golden_sets, experiments,
runs. JSON columns hold component-shaped payloads so components differ
without schema changes.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

_DATA_DIR = Path(__file__).parent / "data"
DB_PATH = _DATA_DIR / "ratchet.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS golden_sets (
  id            TEXT PRIMARY KEY,
  component     TEXT NOT NULL,
  path          TEXT NOT NULL,
  sha256        TEXT NOT NULL,
  item_count    INTEGER NOT NULL,
  scorable_count INTEGER NOT NULL,
  split_rule    TEXT NOT NULL,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS experiments (
  id                  TEXT PRIMARY KEY,
  component           TEXT NOT NULL,
  golden_id           TEXT NOT NULL REFERENCES golden_sets(id),
  golden_sha          TEXT NOT NULL,
  standards_menu_sha  TEXT,
  baseline_prompt     TEXT NOT NULL,
  mutation_prompt     TEXT,
  target_metric       TEXT NOT NULL,
  guard_metrics       TEXT NOT NULL,
  scores              TEXT,
  decision            TEXT,
  decided_by          TEXT,
  why                 TEXT,
  system_version      TEXT NOT NULL,
  determinism         TEXT,
  created_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS runs (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  experiment_id TEXT NOT NULL REFERENCES experiments(id),
  prompt_version TEXT NOT NULL,
  split         TEXT NOT NULL,
  item_id       TEXT NOT NULL,
  actual        TEXT NOT NULL,
  hits          TEXT NOT NULL,
  raw_response  TEXT,
  duration_ms   INTEGER,
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_runs_exp ON runs(experiment_id, prompt_version, split);
"""


def connect() -> sqlite3.Connection:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA)
    return conn


# ── golden_sets ──────────────────────────────────────────────────

def register_golden(
    gid: str, component: str, path: str, sha256: str,
    item_count: int, scorable_count: int, split_rule: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT OR REPLACE INTO golden_sets
               (id, component, path, sha256, item_count, scorable_count, split_rule)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (gid, component, path, sha256, item_count, scorable_count, split_rule),
        )


def get_golden(gid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM golden_sets WHERE id = ?", (gid,)).fetchone()
        return dict(row) if row else None


def list_goldens() -> list[dict[str, Any]]:
    with connect() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM golden_sets ORDER BY created_at")]


# ── experiments ──────────────────────────────────────────────────

def create_experiment(
    eid: str, component: str, golden_id: str, golden_sha: str,
    standards_menu_sha: str, baseline_prompt: str, mutation_prompt: str | None,
    target_metric: str, guard_metrics: list[str], system_version: str,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO experiments
               (id, component, golden_id, golden_sha, standards_menu_sha,
                baseline_prompt, mutation_prompt, target_metric, guard_metrics,
                system_version)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (eid, component, golden_id, golden_sha, standards_menu_sha,
             baseline_prompt, mutation_prompt, target_metric,
             json.dumps(guard_metrics), system_version),
        )


def get_experiment(eid: str) -> dict[str, Any] | None:
    with connect() as conn:
        row = conn.execute("SELECT * FROM experiments WHERE id = ?", (eid,)).fetchone()
        if not row:
            return None
        d = dict(row)
        if d.get("guard_metrics"):
            d["guard_metrics"] = json.loads(d["guard_metrics"])
        if d.get("scores"):
            d["scores"] = json.loads(d["scores"])
        return d


def list_experiments() -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute("SELECT * FROM experiments ORDER BY created_at").fetchall()
        out = []
        for r in rows:
            d = dict(r)
            if d.get("guard_metrics"):
                d["guard_metrics"] = json.loads(d["guard_metrics"])
            if d.get("scores"):
                d["scores"] = json.loads(d["scores"])
            out.append(d)
        return out


def set_experiment_scores(eid: str, scores: dict[str, Any]) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE experiments SET scores = ? WHERE id = ?",
            (json.dumps(scores), eid),
        )


def set_experiment_determinism(eid: str, determinism: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE experiments SET determinism = ? WHERE id = ?",
            (determinism, eid),
        )


def decide_experiment(eid: str, decision: str, decided_by: str, why: str) -> None:
    with connect() as conn:
        conn.execute(
            "UPDATE experiments SET decision = ?, decided_by = ?, why = ? WHERE id = ?",
            (decision, decided_by, why, eid),
        )


# ── runs ─────────────────────────────────────────────────────────

def insert_run(
    experiment_id: str, prompt_version: str, split: str, item_id: str,
    actual: dict[str, Any], hits: dict[str, Any],
    raw_response: str | None, duration_ms: int,
) -> None:
    with connect() as conn:
        conn.execute(
            """INSERT INTO runs
               (experiment_id, prompt_version, split, item_id, actual, hits,
                raw_response, duration_ms)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (experiment_id, prompt_version, split, item_id,
             json.dumps(actual), json.dumps(hits), raw_response, duration_ms),
        )


def runs_for(experiment_id: str) -> list[dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM runs WHERE experiment_id = ? ORDER BY id", (experiment_id,)
        ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["actual"] = json.loads(d["actual"])
            d["hits"] = json.loads(d["hits"])
            out.append(d)
        return out
