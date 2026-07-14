#!/usr/bin/env python3
"""Seed quality_dimensions for gap capabilities via RaR-style rubric generation.

Gap detection: capabilities referenced by imported agent_configs that have
empty/null quality_dimensions.  For each gap, calls meta_planner LLM
(deepseek-planning) with a RaR rubric-generator prompt, validates the
output, and upserts with source='example-generated'.

Usage:
    uv run python scripts/seed_capability_dims.py          # seed gaps
    uv run python scripts/seed_capability_dims.py --dry-run # preview only

Idempotent — safe to re-run after gaps are filled (no-ops when dims exist).

NOTE: Dims seeding ≠ golden labels. Seeded dims are templates (source=example-generated)
with golden_ref_count=0 and confidence=provisional.  L3 trust still requires human-labeled
golden examples + calibration (see het-moat File 04/05).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import psycopg

from backend.llm.gateway import call as gateway_call

DB_URL = os.environ.get("DATABASE_URL", "")

# ── RaR prompt template ──────────────────────────────────────────────────────

RAR_PROMPT = """You are an expert rubric writer. Generate a self-contained set of evaluation
criteria for judging a {name} deliverable ({description}).

Domain context:
{domain_context}

Return a JSON array of objects with this exact shape:
[
  {{
    "id": "short_snake_case_id",
    "dimension": "one measurable criterion (one short sentence)",
    "kind": "objective" or "subjective"
  }}
]

Rules:
1. 2-6 dimensions total.
2. "objective" = deterministically checkable (file exists, command runs, tests pass, output parses, status code).
3. "subjective" = requires judgment (quality, clarity, fit, design, correctness).
4. Include at least 1 objective dimension.
5. All ids must be unique and snake_case.

OUTPUT ONLY valid JSON. No markdown, no explanations, no extra text."""

# ── Domain profile lookup ───────────────────────────────────────────────────

# Fallback domain context when no domain_profiles match
FALLBACK_DOMAIN_CONTEXT = (
    "General software/data deliverable. "
    "Objective criteria: build/compile, tests, file existence, CLI exit codes. "
    "Subjective criteria: code quality, design, correctness, clarity, fit for purpose."
)

# Family → domain mapping for profile lookup
FAMILY_TO_DOMAIN = {
    "software": "software_app",
    "design": "software_app",
    "data": "data_pipeline",
    "business": "generic",
    "research": "research_report",
    "creative": "generic",
}


def load_domain_profiles(cur) -> dict[str, dict]:
    """Load domain_profiles table into a dict keyed by domain name."""
    cur.execute("SELECT domain, acceptance, conventions FROM domain_profiles")
    profiles: dict[str, dict] = {}
    for row in cur.fetchall():
        domain = row[0]
        acceptance = row[1] or {}
        conventions = row[2] or []
        profiles[domain] = {
            "domain": domain,
            "acceptance": acceptance,
            "conventions": conventions,
        }
    return profiles


def profile_for(family: list[str], profiles: dict[str, dict]) -> str:
    """Build domain context string for a capability's family array."""
    for fam in family:
        domain_key = FAMILY_TO_DOMAIN.get(fam)
        if domain_key and domain_key in profiles:
            p = profiles[domain_key]
            parts: list[str] = [f"Domain: {domain_key}"]
            acc = p.get("acceptance", {}) or {}
            if acc.get("deliverables"):
                parts.append(f"Deliverables: {', '.join(acc['deliverables'])}")
            if acc.get("quality_dimensions"):
                parts.append(f"Quality dimensions: {', '.join(acc['quality_dimensions'])}")
            if acc.get("runnable_check"):
                parts.append(f"Runnable check: {acc['runnable_check']}")
            if p.get("conventions"):
                parts.append(f"Conventions: {'; '.join(p['conventions'][:3])}")
            return "\n".join(parts)
    return FALLBACK_DOMAIN_CONTEXT


# ── Gap detection ────────────────────────────────────────────────────────────


def find_gaps(cur) -> list[dict]:
    """Return capabilities referenced by agent_configs that lack quality_dimensions."""
    cur.execute(
        """
        SELECT DISTINCT c.name, c.description, c.family
        FROM capabilities c
        WHERE (c.quality_dimensions IS NULL OR jsonb_array_length(c.quality_dimensions) = 0)
          AND c.name IN (
              SELECT jsonb_array_elements_text(new_capabilities) FROM agent_configs
          )
        ORDER BY c.name
        """
    )
    return [
        {"name": row[0], "description": row[1] or "", "family": row[2] or []}
        for row in cur.fetchall()
    ]


# ── LLM generation ───────────────────────────────────────────────────────────


def generate_dims(name: str, description: str, domain_context: str) -> list[dict]:
    """Call meta_planner with RaR prompt to generate quality_dimensions."""
    prompt = RAR_PROMPT.format(
        name=name,
        description=description or "(not provided)",
        domain_context=domain_context,
    )
    result = gateway_call(
        "meta_planner",
        [{"role": "user", "content": prompt}],
        max_tokens=2048,
        temperature=0.3,
    )
    raw = (
        result.get("choices", [{}])[0]
        .get("message", {})
        .get("content", "")
    )
    # Strip markdown fences if present
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return json.loads(raw.strip())


# ── Validation ───────────────────────────────────────────────────────────────


def validate_dims(dims: list[dict], cap_name: str) -> list[str]:
    """Validate generated dimensions.  Return list of error messages (empty = valid)."""
    errors: list[str] = []

    if not isinstance(dims, list):
        return [f"capability={cap_name}: result is not a JSON array (got {type(dims).__name__})"]

    if len(dims) < 2:
        errors.append(f"capability={cap_name}: only {len(dims)} dimensions (min 2)")
    if len(dims) > 6:
        errors.append(f"capability={cap_name}: {len(dims)} dimensions (max 6)")

    kinds_valid = {"objective", "subjective"}
    ids_seen: set[str] = set()
    objective_count = 0

    for i, d in enumerate(dims):
        if not isinstance(d, dict):
            errors.append(f"capability={cap_name} dim[{i}]: not a dict")
            continue

        dim_id = d.get("id", "")
        if not dim_id:
            errors.append(f"capability={cap_name} dim[{i}]: missing 'id'")
        elif dim_id in ids_seen:
            errors.append(f"capability={cap_name} dim[{i}]: duplicate id '{dim_id}'")
        else:
            ids_seen.add(dim_id)

        if "dimension" not in d or not d["dimension"]:
            errors.append(f"capability={cap_name} dim[{i}]: missing or empty 'dimension'")

        kind = d.get("kind", "")
        if kind not in kinds_valid:
            errors.append(
                f"capability={cap_name} dim[{i}]: invalid kind '{kind}' "
                f"(must be 'objective' or 'subjective')"
            )
        if kind == "objective":
            objective_count += 1

    if objective_count < 1:
        errors.append(f"capability={cap_name}: no objective dimensions (need at least 1)")

    return errors


# ── Upsert ───────────────────────────────────────────────────────────────────


def update_capability(cur, name: str, dims: list[dict]) -> None:
    """Upsert quality_dimensions on a capability row."""
    cur.execute(
        """
        UPDATE capabilities
        SET quality_dimensions = %s::jsonb,
            source = 'example-generated',
            version = version + 1,
            updated_at = now()
        WHERE name = %s
        """,
        (json.dumps(dims), name),
    )


# ── Main ─────────────────────────────────────────────────────────────────────


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Seed quality_dimensions for gap capabilities via RaR LLM"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview gaps without generating or writing dims",
    )
    parser.add_argument(
        "--gap-only",
        action="store_true",
        help="Only list gaps, do not generate (implies --dry-run)",
    )
    args = parser.parse_args()

    dry_run = args.dry_run or args.gap_only
    gap_only = args.gap_only

    if not DB_URL:
        print("ERROR: DATABASE_URL not set")
        return 1

    conn = psycopg.connect(DB_URL)
    try:
        cur = conn.cursor()

        # Load domain profiles for context
        domain_profiles = load_domain_profiles(cur)
        print(f"[seed] Loaded {len(domain_profiles)} domain profiles")

        # Gap detection
        gaps = find_gaps(cur)
        if not gaps:
            print("[seed] No gaps found — all capabilities referenced by "
                  "agent_configs have quality_dimensions.")
            if gap_only:
                return 0
            print("[seed] Nothing to do. Exiting.")
            return 0

        print(f"[seed] Found {len(gaps)} gap capabilities missing quality_dimensions:")
        for g in gaps:
            print(f"       - {g['name']}: {g['description'][:80] or '(no description)'}")

        if dry_run:
            print("\n[seed] Dry-run mode — no generation or writes performed.")
            return 0

        # Generate dims for each gap
        generated = 0
        errors = 0
        for gap in gaps:
            name = gap["name"]
            print(f"\n[seed] Generating dims for {name}...", end=" ")

            try:
                domain_context = profile_for(gap["family"], domain_profiles)
                dims = generate_dims(name, gap["description"], domain_context)
                validation_errors = validate_dims(dims, name)

                if validation_errors:
                    print("VALIDATION FAILED")
                    for err in validation_errors:
                        print(f"       ERROR: {err}")
                    errors += 1
                    continue

                update_capability(cur, name, dims)
                conn.commit()
                dim_kinds = ", ".join(f"{d['id']}({d['kind']})" for d in dims)
                print(f"OK — {len(dims)} dims: {dim_kinds}")
                generated += 1

            except json.JSONDecodeError as e:
                print(f"JSON PARSE ERROR: {e}")
                errors += 1
            except Exception as e:
                print(f"ERROR: {e}")
                errors += 1

        # Summary
        print(f"\n[seed] Done: {generated} generated, {errors} errors, {len(gaps) - generated - errors} skipped")
        return 1 if errors > 0 else 0

    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
