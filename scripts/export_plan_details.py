"""Export all plan formulation details to a CSV, including refs/deps presence
and the exact formulator prompt used for each plan.

The formulator prompt is reconstructed from the LIVE code path:
  - template: FORMULATE_PROMPT (backend/planning/meta_planner/goal_formulator.py)
  - standards menu: list_standard_menu() (backend/standards/loader.py) — the same
    DB-driven menu injected into the LLM prompt
  - system context: _fetch_system_context(project_id) — systems table lookup
So the CSV's prompt column reflects the prompt as actually constructed at
formulation time (hardcoded template parts included).

Usage:
    cd /opt/aipc/conductor
    uv run python scripts/export_plan_details.py [--output plans_details.csv] [--project <id>]
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import psycopg
from psycopg.rows import dict_row

from backend.planning.meta_planner.goal_formulator import (
    FORMULATE_PROMPT,
    _fetch_system_context,
)
from backend.planning.references import REFERENCES_ROOT, has_references
from backend.standards.loader import list_standard_menu

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://aipc@localhost:5432/aipc_conductor")


# ── Reconstruct the exact standards menu string the LLM saw ──────────
def build_standards_str() -> str:
    """Mirror goal_formulator._formulate() lines 510-524: build the menu text."""
    standards_lines: list[str] = []
    for s in list_standard_menu():
        blurb = s.get("blurb", "")
        families = s.get("families", [])
        delivery_form = s.get("delivery_form", "")
        families_str = ", ".join(families) if families else ""
        line = f"  {s['slug']} | {delivery_form or 'n/a'}"
        if blurb:
            line += f" | {blurb[:100]}"
        if families_str:
            line += f"  [{families_str}]"
        standards_lines.append(line)
    return "\n".join(standards_lines)


def build_formulate_prompt(
    raw_input: str,
    origin: str,
    prior: str,
    project_id: str,
    standards_str: str,
) -> str:
    """Reconstruct the exact prompt passed to the LLM for this plan.

    ``memory`` cannot be replayed (Neo4j recall at formulation time) so it is
    marked as such. Everything else mirrors the code path exactly.
    """
    sys_context = _fetch_system_context(project_id)
    if sys_context:
        sys_context = "--- System context ---\n" + sys_context
    return FORMULATE_PROMPT.format(
        input=raw_input,
        prior=prior or "(none)",
        memory="(not replayable — recalled at formulation time)",
        origin=origin,
        standards=standards_str,
        system_context=sys_context,
    )


# ── Helpers ─────────────────────────────────────────────────────────
def _clean(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value)
    return str(value)


def _roster(dag: list) -> str:
    """agent_config + scaffold per node, e.g. 'node-001:ui-designer@design-layout-v2;...'"""
    out = []
    for node in dag or []:
        nid = node.get("id", "?")
        members = node.get("members") or []
        ac = members[0].get("agent_config", "") if members else ""
        out.append(f"{nid}:{ac}")
    return "; ".join(out)


def main() -> None:
    ap = argparse.ArgumentParser(description="Export plan formulation details to CSV")
    ap.add_argument("--output", default="plans_details.csv")
    ap.add_argument("--project", default=None, help="Filter by project_id (substring)")
    args = ap.parse_args()

    # Pre-build the standards menu once (same for all plans in a run)
    standards_str = build_standards_str()

    with psycopg.connect(DB_DSN, row_factory=dict_row) as conn:
        with conn.cursor() as cur:
            where = "WHERE TRUE"
            params: list = []
            if args.project:
                where = "WHERE p.project_id ILIKE %s"
                params.append(f"%{args.project}%")
            cur.execute(
                f"""SELECT p.plan_id, p.project_id, p.user_intent, p.goal,
                           p.partial_meta_goal, p.dag, p.multimodal_refs,
                           p.plan_status, p.planning_status, p.ratified,
                           p.clarify_context, p.clarify_rounds,
                           p.plan_goal_review, p.planning_attempts,
                           p.needs_usage_sim, p.origin, p.source_ref,
                           p.intake_id, p.version, p.created_at, p.approved_at,
                           r.id AS last_run_id, r.state AS last_run_state,
                           r.merge_status AS last_run_merge, r.dep_shas AS run_dep_shas
                    FROM plans p
                    LEFT JOIN LATERAL (
                        SELECT id, state, merge_status, dep_shas
                        FROM runs WHERE plan_id = p.plan_id
                        ORDER BY created_at DESC LIMIT 1
                    ) r ON true
                    {where}
                    ORDER BY p.created_at""",
                params,
            )
            rows = cur.fetchall()

            # Deps map: project_id -> list of dep_names
            cur.execute(
                """SELECT project_id, dep_name, depends_on_project_id
                   FROM project_dependencies"""
            )
            dep_map: dict[str, list[str]] = {}
            for d in cur.fetchall():
                dep_map.setdefault(d["project_id"], []).append(
                    f"{d['dep_name']}->{d['depends_on_project_id']}"
                )

    # References gate: dir exists AND README.md present
    ref_ok = {p.name: has_references(p.name) for p in REFERENCES_ROOT.glob("*") if p.is_dir()}
    print(f"References root: {REFERENCES_ROOT}")
    print(f"Plans to export: {len(rows)}")

    out_path = Path(args.output)
    with open(out_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "plan_id", "project_id", "created_at",
            "raw_input", "formulation_goal", "spec", "quality_intent",
            "estimated_nodes", "plan_status", "planning_status", "ratified",
            "clarify_rounds", "clarify_questions_answers",
            "plan_goal_review", "planning_attempts", "needs_usage_sim",
            "origin", "source_ref", "intake_id", "version",
            "scaffold_components", "roster_agents",
            "has_references", "references_path",
            "deps", "dep_shas", "last_run_id", "last_run_state", "last_run_merge",
            "formulator_prompt",
        ])

        for r in rows:
            pid = r["project_id"]
            pmg = r["partial_meta_goal"] or {}
            dag = r["dag"] or []
            components = pmg.get("components") or []
            comp_str = "; ".join(
                f"{c.get('standard_slug','')}@{c.get('subdir','')}"
                + (f"[{c.get('variant')}]" if c.get("variant") else "")
                for c in components
            )
            refs = ref_ok.get(pid, False)
            ref_path = str(REFERENCES_ROOT / pid) if (REFERENCES_ROOT / pid).exists() else ""
            deps = "; ".join(dep_map.get(pid, []))
            dep_shas = r["run_dep_shas"] or {}
            raw_input = r["user_intent"] or ""
            prior = ""
            clarify = r["clarify_context"] or []
            if isinstance(clarify, list) and clarify:
                prior = json.dumps(clarify, ensure_ascii=False)
            origin = r["origin"] or "human"

            prompt = build_formulate_prompt(
                raw_input=raw_input,
                origin=origin,
                prior=prior,
                project_id=pid,
                standards_str=standards_str,
            )

            w.writerow([
                r["plan_id"], pid, r["created_at"],
                raw_input, r["goal"] or "",
                _clean(pmg.get("spec")), _clean(pmg.get("quality_intent")),
                pmg.get("estimated_node_count", len(dag)),
                r["plan_status"], r["planning_status"], r["ratified"],
                r["clarify_rounds"], clarify,
                r["plan_goal_review"], r["planning_attempts"], r["needs_usage_sim"],
                origin, r["source_ref"], r["intake_id"], r["version"],
                comp_str, _roster(dag),
                refs, ref_path,
                deps, _clean(dep_shas),
                r["last_run_id"], r["last_run_state"], r["last_run_merge"],
                prompt,
            ])

    print(f"Wrote {out_path} ({len(rows)} rows)")
    print(f"Reference gate: {sum(1 for v in ref_ok.values() if v)}/{len(ref_ok)} projects have references")


if __name__ == "__main__":
    main()
