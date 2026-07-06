from __future__ import annotations

import json
import logging
from typing import Any

from backend.planning.capability.registry import (
    all_capabilities,
    caps_in_family,
    get_capability,
)
from backend.planning.meta_planner.llm import call_llm_structured, get_meta_planner_model

logger = logging.getLogger(__name__)

# Domain string -> capability family mapping (deterministic pre-filter)
DOMAIN_TO_FAMILY: dict[str, list[str]] = {
    "software_app": ["software"],
    "fullstack": ["software"],
    "frontend": ["software"],
    "backend": ["software"],
    "api": ["software"],
    "cli": ["software"],
    "data_pipeline": ["data"],
    "analytics": ["data"],
    "data": ["data"],
    "design": ["design", "creative"],
    "creative": ["creative"],
    "music": ["creative"],
    "video": ["creative"],
    "game": ["creative"],
    "research": ["research"],
    "docs": ["research"],
    "business": ["business"],
    "finance": ["business"],
    "general": ["research"],
}

MAX_GAP_PROPOSALS = 2


# ── Step 1: Family pre-filter (deterministic, cheap) ────────────────


def candidate_capabilities(node: dict[str, Any]) -> list[dict[str, Any]]:
    """Determine candidate capability set for a node.

    Bounds the selection context to the node's domain family so the
    registry size does not blow up the LLM prompt. Always includes
    'generic' as a fallback candidate.
    """
    domain = node.get("domain") or _infer_domain(node)
    family = DOMAIN_TO_FAMILY.get(domain)
    cands = caps_in_family(family) if family else all_capabilities()
    generic = get_capability("generic")
    seen_names = {c["name"] for c in cands}
    if generic and generic["name"] not in seen_names:
        cands.append(generic)
    return cands


def _infer_domain(node: dict[str, Any]) -> str:
    """Infer domain from node task text (keyword heuristic)."""
    task = (node.get("task") or {}).get("text", "") or node.get("goal", "")
    text = task.lower()
    if any(w in text for w in ("frontend", "ui", "html", "react", "vue", "dashboard", "web page")):
        return "frontend"
    if any(w in text for w in ("backend", "api", "endpoint", "server", "database", "fastapi")):
        return "backend"
    if any(w in text for w in ("cli", "command", "shell", "terminal")):
        return "cli"
    if any(w in text for w in ("data", "pipeline", "etl", "csv", "analytics", "ingest")):
        return "data"
    if any(w in text for w in ("music", "audio", "track", "beat", "melody")):
        return "music"
    if any(w in text for w in ("video", "clip", "film", "movie")):
        return "video"
    if any(w in text for w in ("game", "playable", "unity", "godot")):
        return "game"
    if any(w in text for w in ("research", "report", "investigate", "find", "analyze")):
        return "research"
    if any(w in text for w in ("business", "workflow", "automate", "process")):
        return "business"
    return "general"


# ── Step 2: LLM capability select ───────────────────────────────────

SELECT_PROMPT = """You are selecting capabilities for a plan node from a bounded slate.

The node must produce a deliverable. Pick 1-N capabilities from the slate
that the node genuinely requires. If NONE of the listed capabilities fit,
return exactly ["__gap__"].

Do NOT invent names outside the slate. Return a JSON list of capability names.

Node task: {task}
Node deliverables: {deliverables}
Node success criterion: {success}

Available capabilities:
{slate}

Return a JSON array of capability names, e.g. ["frontend", "backend_api"]
or ["__gap__"] if none fit. Select only from the list above."""


# Keyword-to-capability mapping for LLM-unavailable fallback
_CAP_KEYWORDS: dict[str, list[str]] = {
    "frontend": ["ui", "html", "react", "vue", "angular", "web page", "dashboard", "user interface", "css", "javascript"],
    "backend_api": ["api", "endpoint", "server", "database", "fastapi", "flask", "django", "backend"],
    "cli_tool": ["cli", "command", "shell", "terminal"],
    "data_pipeline": ["pipeline", "etl", "csv", "ingest", "transform", "extract"],
    "analytics_assistant": ["analytics", "insights", "metrics", "dashboard", "report"],
    "music_generation": ["music", "audio", "track", "beat", "melody", "lo-fi"],
    "video_content": ["video", "clip", "film", "movie"],
    "game_build": ["game", "playable", "unity", "godot"],
    "research_report": ["research", "report", "investigate", "find", "sources"],
    "agentic_business_flow": ["business", "workflow", "automate", "process"],
}


def _keyword_fallback(node: dict[str, Any], cands: list[dict[str, Any]]) -> list[str]:
    task = (node.get("task") or {}).get("text", "") or node.get("goal", "")
    text = task.lower()
    matched = []
    for cap in cands:
        name = cap["name"]
        keywords = _CAP_KEYWORDS.get(name, [])
        if any(kw in text for kw in keywords):
            matched.append(name)
    return matched if matched else ["generic"]


def select_capabilities(node: dict[str, Any]) -> list[str]:
    """LLM selects 1-N capabilities from the family-filtered slate.

    Returns list of validated capability names. Hallucinated names are
    rejected. Empty selection defaults to ['generic'].
    """
    cands = candidate_capabilities(node)
    slate = [
        {
            "name": c["name"],
            "description": c["description"],
            "dimensions": [d["dimension"] for d in (c.get("quality_dimensions") or [])],
        }
        for c in cands
    ]
    task = (node.get("task") or {}).get("text", "")
    deliverables = (node.get("task") or {}).get("deliverables", [])
    success = (node.get("success") or {}).get("text", "")
    prompt = SELECT_PROMPT.format(
        task=task or node.get("goal", ""),
        deliverables=json.dumps(deliverables) if deliverables else "(none)",
        success=success or "(not specified)",
        slate=json.dumps(slate, indent=2),
    )
    try:
        model_cfg = get_meta_planner_model()
        if model_cfg is None:
            raise ValueError("no meta_planner model configured")
        resp = call_llm_structured(prompt, schema=None, model_cfg=model_cfg)
        raw = resp if isinstance(resp, list) else json.loads(str(resp))
    except Exception as exc:
        logger.warning("Capability selection LLM call failed: %s — using keyword fallback", exc)
        return _keyword_fallback(node, cands)

    valid_names = {c["name"] for c in cands}
    if raw == ["__gap__"]:
        return _handle_gap(node, slate)

    picked = [p for p in raw if p in valid_names]
    if not picked:
        picked = ["generic"]
    return picked


# ── Step 3: Gap path (rare, human-gated) ────────────────────────────

PROPOSE_CAP_PROMPT = """You are a capability registry curator. A plan node needs
capabilities that do not exist in the current registry. Propose a NEW capability.

Node task: {task}
Node deliverables: {deliverables}
Existing capabilities (for comparison): {existing}

Propose a new capability as JSON:
{{
  "name": "unique_name",
  "family": "software|data|creative|business|research",
  "description": "one-line description",
  "quality_dimensions": [
    {{"id": "dim_id", "dimension": "what it checks", "kind": "objective"|"subjective"}}
  ],
  "required_tools": ["tool1", "tool2"]
}

Dimensions: objective -> L1 (deterministic), subjective -> L2 (judge).
Every capability needs at least one objective and one subjective dimension.
The name must NOT collide with existing capabilities."""


def _handle_gap(node: dict[str, Any], slate: list[dict[str, Any]]) -> list[str]:
    """Handle the case where no existing capability fits the node.

    Proposes a new capability, queues it for human ratification.
    Returns ["generic"] as a temporary assignment.
    """
    from backend.planning.meta_planner.llm import call_llm_structured

    task = (node.get("task") or {}).get("text", "")
    deliverables = (node.get("task") or {}).get("deliverables", [])
    prompt = PROPOSE_CAP_PROMPT.format(
        task=task or node.get("goal", ""),
        deliverables=json.dumps(deliverables) if deliverables else "(none)",
        existing=json.dumps(slate, indent=2),
    )
    try:
        model_cfg = get_meta_planner_model()
        proposal = call_llm_structured(prompt, schema=None, model_cfg=model_cfg)
        if isinstance(proposal, str):
            proposal = json.loads(proposal)
        proposal["source"] = "proposed-by-llm"
        proposal["golden_ref_count"] = 0
        logger.info(
            "Capability gap proposal: %s — queued for human ratification",
            proposal.get("name", "(unnamed)"),
        )
    except Exception as exc:
        logger.warning("Capability gap proposal failed: %s — falling back to generic", exc)
        return ["generic"]
    _queue_for_ratification(proposal)
    return ["generic"]


def _queue_for_ratification(proposal: dict[str, Any]) -> None:
    """Persist a capability proposal for human ratification.

    Writes to a 'capability_proposals' table or log for review.
    """
    try:
        import os
        import psycopg

        dsn = os.environ.get("DATABASE_URL", "")
        if not dsn:
            logger.warning("No DB URL — logging proposal instead")
            return
        with psycopg.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO capability_proposals (name, family, description,
                    quality_dimensions, required_tools, source, status)
                VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s, 'pending')
                ON CONFLICT (name) DO NOTHING
                """,
                (
                    proposal.get("name", ""),
                    proposal.get("family", ""),
                    proposal.get("description", ""),
                    json.dumps(proposal.get("quality_dimensions", [])),
                    json.dumps(proposal.get("required_tools", [])),
                    proposal.get("source", "example-generated"),
                ),
            )
        conn.commit()
    except Exception as exc:
        logger.warning("Failed to queue capability proposal: %s", exc)


# ── Public API ──────────────────────────────────────────────────────


def resolve_node_capabilities(node: dict[str, Any]) -> list[str]:
    """Resolve capabilities for a single node.

    This is the main entry point called from the planner graph after
    decompose and before check-gen.
    """
    caps = select_capabilities(node)
    node["capabilities"] = caps
    return caps


def resolve_dag_capabilities(dag: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Resolve capabilities for all nodes in the DAG in-place.

    Mutates each node dict to add 'capabilities' list.
    """
    for node in dag:
        resolve_node_capabilities(node)
    return dag
