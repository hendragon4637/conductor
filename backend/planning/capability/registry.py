"""Retrieval helpers for the capability registry.
Provides get_capability, caps_in_family, objective_dims, subjective_dims.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

_DB_URL: str | None = None

# In-memory fallback so the registry works without a seeded DB.
# Mirrors scripts/seed_capabilities.py. The DB is the source of truth;
# this is a convenient default for tests and dev environments.
_FALLBACK_CAPS: list[dict[str, Any]] = [
    {"name": "frontend", "family": ["software", "design"], "description": "user-facing UI a person interacts with",
     "quality_dimensions": [{"id": "renders", "dimension": "UI renders without errors", "kind": "objective"},
                            {"id": "builds", "dimension": "build/compile succeeds", "kind": "objective"},
                            {"id": "input_handling", "dimension": "validates input + shows feedback", "kind": "subjective"},
                            {"id": "responsive", "dimension": "works across viewport sizes", "kind": "subjective"},
                            {"id": "integration", "dimension": "correctly calls the backend", "kind": "subjective"}],
     "required_tools": ["write_file", "browser"]},
    {"name": "backend_api", "family": ["software"], "description": "HTTP API with endpoints + data logic",
     "quality_dimensions": [{"id": "tests_pass", "dimension": "unit/endpoint tests pass", "kind": "objective"},
                            {"id": "status_codes", "dimension": "correct HTTP status codes", "kind": "objective"},
                            {"id": "validation", "dimension": "validates inputs, rejects bad data", "kind": "subjective"},
                            {"id": "data_integrity", "dimension": "safe types (integer cents), no corruption", "kind": "subjective"}],
     "required_tools": ["write_file", "shell"]},
    {"name": "cli_tool", "family": ["software"], "description": "command-line tool runnable from shell",
     "quality_dimensions": [{"id": "help_works", "dimension": "help exits 0", "kind": "objective"},
                            {"id": "runs", "dimension": "main command runs on valid input", "kind": "objective"},
                            {"id": "error_handling", "dimension": "handles bad input gracefully", "kind": "subjective"}],
     "required_tools": ["write_file", "shell"]},
    {"name": "data_pipeline", "family": ["data"], "description": "ingest -> transform -> output data pipeline",
     "quality_dimensions": [{"id": "runs_sample", "dimension": "runs on sample input, produces expected output", "kind": "objective"},
                            {"id": "handles_malformed", "dimension": "skips malformed rows without crashing", "kind": "objective"},
                            {"id": "correctness", "dimension": "transformation is correct vs spec", "kind": "subjective"},
                            {"id": "reproducible", "dimension": "deterministic/reproducible output", "kind": "objective"}],
     "required_tools": ["write_file", "shell"]},
    {"name": "analytics_assistant", "family": ["data"], "description": "derives insights from data streams",
     "quality_dimensions": [{"id": "parses", "dimension": "parses input data correctly", "kind": "objective"},
                            {"id": "accuracy", "dimension": "computed metrics accurate vs known fixture", "kind": "objective"},
                            {"id": "actionable", "dimension": "insights are actionable/relevant", "kind": "subjective"},
                            {"id": "clarity", "dimension": "insights presented clearly", "kind": "subjective"}],
     "required_tools": ["read_data", "write_file"]},
    {"name": "music_generation", "family": ["creative"], "description": "generate a musical piece from a prompt",
     "quality_dimensions": [{"id": "valid_audio", "dimension": "produces playable audio of correct length", "kind": "objective"},
                            {"id": "melody", "dimension": "melodic richness/coherence", "kind": "subjective"},
                            {"id": "rhythm", "dimension": "rhythmic structure consistency", "kind": "subjective"},
                            {"id": "prompt_fit", "dimension": "matches requested style/mood", "kind": "subjective"}],
     "required_tools": ["audio_gen", "write_file"]},
    {"name": "video_content", "family": ["creative"], "description": "generate/edit a video clip",
     "quality_dimensions": [{"id": "valid_video", "dimension": "valid encoded video of correct duration", "kind": "objective"},
                            {"id": "visual_quality", "dimension": "scene composition / visual coherence", "kind": "subjective"},
                            {"id": "narrative", "dimension": "narrative/temporal consistency", "kind": "subjective"},
                            {"id": "prompt_fit", "dimension": "matches the brief", "kind": "subjective"}],
     "required_tools": ["video_gen", "write_file"]},
    {"name": "game_build", "family": ["creative"], "description": "build a small playable game",
     "quality_dimensions": [{"id": "runs", "dimension": "game launches + core loop runs", "kind": "objective"},
                            {"id": "no_crash", "dimension": "no crash during a play session", "kind": "objective"},
                            {"id": "fun", "dimension": "core loop is engaging/fun", "kind": "subjective"},
                            {"id": "balance", "dimension": "difficulty/balance reasonable", "kind": "subjective"}],
     "required_tools": ["write_file", "shell", "browser"]},
    {"name": "agentic_business_flow", "family": ["business"], "description": "automate a business workflow across tools",
     "quality_dimensions": [{"id": "executes", "dimension": "workflow runs end-to-end without error", "kind": "objective"},
                            {"id": "correct_actions", "dimension": "performs the intended actions on the right targets", "kind": "objective"},
                            {"id": "appropriateness", "dimension": "decisions are business-appropriate", "kind": "subjective"},
                            {"id": "safety", "dimension": "no unintended side-effects", "kind": "subjective"}],
     "required_tools": ["http", "read_data", "write_file"]},
    {"name": "research_report", "family": ["research"], "description": "researched written report",
     "quality_dimensions": [{"id": "file_present", "dimension": "report file exists", "kind": "objective"},
                            {"id": "covers", "dimension": "covers all stated questions", "kind": "subjective"},
                            {"id": "sourced", "dimension": "claims cited, no fabricated sources", "kind": "subjective"},
                            {"id": "clarity", "dimension": "clear structure + conclusion", "kind": "subjective"}],
     "required_tools": ["read_web", "write_file"]},
    {"name": "generic", "family": ["research"], "description": "fallback for unclassified deliverables",
     "quality_dimensions": [{"id": "deliverable_present", "dimension": "stated deliverable exists", "kind": "objective"},
                            {"id": "meets_goal", "dimension": "achieves the stated goal", "kind": "subjective"}],
     "required_tools": ["write_file"]},
]

_FALLBACK_BY_NAME: dict[str, dict[str, Any]] = {c["name"]: c for c in _FALLBACK_CAPS}


def _get_db_url() -> str:
    global _DB_URL
    if _DB_URL is None:
        _DB_URL = os.environ.get("DATABASE_URL", "")
    return _DB_URL


def _query(sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    import psycopg
    from psycopg.rows import dict_row

    dsn = _get_db_url()
    if not dsn:
        return []
    try:
        with psycopg.connect(dsn, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return list(cur.fetchall())
    except Exception as exc:
        logger.warning("Capability registry query failed: %s", exc)
        return []


def get_capability(name: str) -> dict[str, Any] | None:
    rows = _query(
        "SELECT name, family, description, quality_dimensions, "
        "required_tools, golden_ref_count, source, version "
        "FROM capabilities WHERE name = %s",
        (name,),
    )
    if rows:
        row = rows[0]
        for field in ("quality_dimensions", "required_tools"):
            if isinstance(row.get(field), str):
                row[field] = json.loads(row[field])
        return row
    return _FALLBACK_BY_NAME.get(name)


def caps_in_family(family: str | list[str]) -> list[dict[str, Any]]:
    families = [family] if isinstance(family, str) else family
    rows = _query(
        "SELECT name, family, description, quality_dimensions, "
        "required_tools, golden_ref_count "
        "FROM capabilities WHERE family ?| %s::text[] ORDER BY name",
        (families,),
    )
    if rows:
        for row in rows:
            for field in ("quality_dimensions", "required_tools"):
                if isinstance(row.get(field), str):
                    row[field] = json.loads(row[field])
        return rows
    return [c for c in _FALLBACK_CAPS if any(f in c["family"] for f in families)]


def all_capabilities() -> list[dict[str, Any]]:
    rows = _query(
        "SELECT name, family, description, quality_dimensions, "
        "required_tools, golden_ref_count "
        "FROM capabilities ORDER BY family, name",
    )
    if rows:
        for row in rows:
            for field in ("quality_dimensions", "required_tools"):
                if isinstance(row.get(field), str):
                    row[field] = json.loads(row[field])
        return rows
    return list(_FALLBACK_CAPS)


def objective_dims(cap: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only objective quality dimensions from a capability dict."""
    return [d for d in (cap.get("quality_dimensions") or []) if d.get("kind") == "objective"]


def subjective_dims(cap: dict[str, Any]) -> list[dict[str, Any]]:
    """Return only subjective quality dimensions from a capability dict."""
    return [d for d in (cap.get("quality_dimensions") or []) if d.get("kind") == "subjective"]
