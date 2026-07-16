"""Seed judge_rubrics v1 from three sources: hardcoded judge anchors, YAML rubric presets, and historical node_sessions.

Usage:
    python -m backend.evaluator.judge_rubric_seed [--capability backend_api|executor|all] [--dry-run]

Without --capability, seeds all known capabilities.
Idempotent: skips capabilities that already have an active v1 row.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

JUDGE_ANCHORS = [
    {"score_range": [0, 2], "expected_outcome": "deliverable missing or core behavior absent"},
    {"score_range": [3, 5], "expected_outcome": "deliverable exists but the criterion's core behavior is wrong"},
    {"score_range": [6, 8], "expected_outcome": "criterion met for the main path; edge cases unhandled"},
    {"score_range": [9, 10], "expected_outcome": "criterion fully met incl. edge cases"},
]

FEEDBACK_CONTRACT = (
    'In your reason, output STRICT JSON only: {"what": "which specific requirement failed or passed", '
    '"where": "file:function or exact path in the artifact", '
    '"why": "root cause in one sentence", '
    '"how": "the concrete change that would satisfy this criterion"}. '
    "Quote actual file paths and code identifiers FROM THE ARTIFACT \u2014 never generic phrases."
)

BUNDLE_RULES = {
    "include_suffixes": [".py", ".js", ".ts", ".tsx", ".jsx", ".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini"],
    "exclude_parts": [".git", ".venv", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache", "node_modules"],
    "max_chars": 24000,
}

CAPABILITY_RUBRIC_MAP: dict[str, list[str]] = {
    "backend_api": ["api_backend", "generic_quality"],
    "executor": ["code_implementation", "generic_quality"],
    "planner": ["plan_structure", "design_doc", "generic_quality"],
}


def _db_conn():
    import psycopg
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        raise RuntimeError("DATABASE_URL not set")
    return psycopg.connect(url)


def _load_rubric_yaml(name: str) -> dict[str, Any] | None:
    rubrics_dir = Path(os.path.dirname(os.path.abspath(__file__))) / "rubrics"
    path = rubrics_dir / f"{name}.yaml"
    if not path.exists():
        logger.warning("YAML preset not found: %s.yaml", name)
        return None
    import yaml
    with open(path) as f:
        return yaml.safe_load(f)


def _load_golden_summary(conn) -> dict[str, list[dict[str, Any]]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT node_type AS capability,
                   rubric_item,
                   COUNT(*) AS total,
                   jsonb_object_agg(split, cnt) AS split_counts
            FROM (
                SELECT node_type, rubric_item, split, COUNT(*) AS cnt
                FROM golden_set
                WHERE frozen = TRUE
                GROUP BY node_type, rubric_item, split
            ) sub
            GROUP BY node_type, rubric_item
            ORDER BY node_type, rubric_item
        """)
        rows = cur.fetchall()

    result: dict[str, list[dict[str, Any]]] = {}
    for capability, rubric_item, total, split_counts in rows:
        result.setdefault(capability, []).append({
            "id": _rubric_item_to_id(rubric_item),
            "rubric_item": rubric_item,
            "total": total,
            "split_counts": split_counts or {},
        })
    return result


def _rubric_item_to_id(rubric_item: str) -> str:
    import re, hashlib
    cleaned = re.sub(r"[^a-z0-9]+", "_", rubric_item.lower()).strip("_")
    if len(cleaned) > 48:
        cleaned = cleaned[:48]
    suffix = hashlib.md5(rubric_item.encode()).hexdigest()[:6]
    return f"golden-{cleaned}-{suffix}"


def _load_observed_dimensions(conn) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute("""
            SELECT fb->>'check_id' AS check_id, COUNT(*) AS freq
            FROM node_sessions ns,
                 LATERAL jsonb_array_elements(ns.l2_feedback) fb
            WHERE ns.l2_feedback IS NOT NULL
              AND jsonb_typeof(ns.l2_feedback) = 'array'
              AND fb->>'check_id' IS NOT NULL
              AND fb->>'check_id' != ''
            GROUP BY fb->>'check_id'
            ORDER BY freq DESC
        """)
        rows = cur.fetchall()

    return [
        {"id": check_id, "freq": freq, "matched_preset": None}
        for check_id, freq in rows
    ]


def _load_yaml_dimensions(capability: str) -> list[dict[str, Any]]:
    dims: list[dict[str, Any]] = []
    preset_names = CAPABILITY_RUBRIC_MAP.get(capability, [])
    seen_ids: set[str] = set()
    for preset_name in preset_names:
        preset = _load_rubric_yaml(preset_name)
        if not preset or "items" not in preset:
            continue
        for item in preset["items"]:
            dim_id = item.get("id", "")
            if dim_id in seen_ids:
                continue
            seen_ids.add(dim_id)
            evaluation_steps = [
                f"Evaluate the artifact against this criterion: {item.get('rubric_item', '')}",
                "Identify the exact files/functions relevant to the criterion; check their actual content",
                FEEDBACK_CONTRACT,
            ]
            dims.append({
                "id": dim_id,
                "rubric_item": item.get("rubric_item", ""),
                "weight": item.get("weight", 1.0),
                "evaluation_steps": evaluation_steps,
                "calibrated": False,
                "preset": preset_name,
                "golden_items": 0,
            })
    return dims


def _items_match(a: str, b: str) -> bool:
    a_norm = a.lower().strip().rstrip("?.")
    b_norm = b.lower().strip().rstrip("?.")
    if a_norm == b_norm:
        return True
    if len(a_norm) >= 20 and (a_norm in b_norm or b_norm in a_norm):
        return True
    return False


def _merge_golden_dimensions(
    preset_dims: list[dict[str, Any]],
    golden_dims: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    merged = list(preset_dims)
    for golden in golden_dims:
        g_id = golden["id"]
        g_item = golden["rubric_item"]
        matched = False
        for dim in merged:
            if _items_match(dim["rubric_item"], g_item):
                dim["calibrated"] = True
                dim["golden_items"] = golden["total"]
                dim["golden_split_counts"] = golden["split_counts"]
                matched = True
                break
        if not matched:
            evaluation_steps = [
                f"Evaluate the artifact against this criterion: {g_item}",
                "Identify the exact files/functions relevant to the criterion; check their actual content",
                FEEDBACK_CONTRACT,
            ]
            merged.append({
                "id": g_id,
                "rubric_item": g_item,
                "weight": 1.0,
                "evaluation_steps": evaluation_steps,
                "calibrated": True,
                "preset": "golden_set",
                "golden_items": golden["total"],
                "golden_split_counts": golden["split_counts"],
            })
    return merged


def _tag_observed_dimensions(
    dimensions: list[dict[str, Any]],
    observed: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    dim_ids = {d["id"] for d in dimensions}
    for obs in observed:
        if obs["id"] in dim_ids:
            for d in dimensions:
                if d["id"] == obs["id"]:
                    obs["matched_preset"] = d.get("preset", "unknown")
                    break
        else:
            obs["matched_preset"] = None
    return observed


def _build_dims_json(
    capability: str,
    golden_map: dict[str, list[dict[str, Any]]],
    observed: list[dict[str, Any]],
) -> dict[str, Any]:
    preset_dims = _load_yaml_dimensions(capability)
    golden_dims = golden_map.get(capability, [])
    dimensions = _merge_golden_dimensions(preset_dims, golden_dims)
    observed_tagged = _tag_observed_dimensions(dimensions, observed)

    return {
        "anchors": JUDGE_ANCHORS,
        "feedback_contract": FEEDBACK_CONTRACT,
        "bundles": BUNDLE_RULES,
        "dimensions": dimensions,
        "observed_dimensions": observed_tagged,
    }


def get_active_rubric_id(conn, capability: str) -> str | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM judge_rubrics WHERE capability = %s AND active = TRUE LIMIT 1",
            (capability,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def get_active_rubric(conn, capability: str) -> dict[str, Any] | None:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, capability, version, dims, source FROM judge_rubrics WHERE capability = %s AND active = TRUE LIMIT 1",
            (capability,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"id": row[0], "capability": row[1], "version": row[2], "dims": row[3], "source": row[4]}


def ensure_active_rubric(conn, capability: str) -> str:
    rid = get_active_rubric_id(conn, capability)
    return rid or ""


def seed_capability(conn, capability: str, dry_run: bool = False) -> bool:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id FROM judge_rubrics WHERE capability = %s AND version = 1",
            (capability,),
        )
        if cur.fetchone():
            logger.info("judge_rubrics v1 already exists for capability=%s \u2014 skipping", capability)
            return False

    golden_map = _load_golden_summary(conn)
    observed = _load_observed_dimensions(conn)
    dims_json = _build_dims_json(capability, golden_map, observed)

    calibrated_count = sum(1 for d in dims_json["dimensions"] if d.get("calibrated"))
    total_dims = len(dims_json["dimensions"])
    observed_count = len(dims_json["observed_dimensions"])

    rubric_id = f"{capability}-v1"

    if dry_run:
        logger.info(
            "[DRY RUN] Would seed %s: rubric_id=%s dims=%d calibrated=%d observed=%d",
            capability, rubric_id, total_dims, calibrated_count, observed_count,
        )
        return True

    with conn.cursor() as cur:
        cur.execute(
            """INSERT INTO judge_rubrics (id, capability, version, dims, source, active)
               VALUES (%s, %s, 1, %s, 'hand', TRUE)""",
            (rubric_id, capability, json.dumps(dims_json)),
        )
    conn.commit()

    logger.info(
        "Seeded judge_rubrics v1 for capability=%s (%s): %d dimensions (%d calibrated, %d observed from history)",
        capability, rubric_id, total_dims, calibrated_count, observed_count,
    )
    return True


KNOWN_CAPABILITIES = ["backend_api", "executor", "planner"]


def seed_all(dry_run: bool = False) -> int:
    seeded = 0
    conn = _db_conn()
    try:
        for cap in KNOWN_CAPABILITIES:
            if seed_capability(conn, cap, dry_run=dry_run):
                seeded += 1
        if seeded == 0:
            logger.info("All capabilities already seeded (no-op).")
    finally:
        conn.close()
    return seeded


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    args = sys.argv[1:] if len(sys.argv) > 1 else []
    dry_run = "--dry-run" in args

    if "--capability" in args:
        idx = args.index("--capability")
        target = args[idx + 1] if idx + 1 < len(args) else "all"
        if target == "all":
            seed_all(dry_run=dry_run)
        elif target in KNOWN_CAPABILITIES:
            conn = _db_conn()
            try:
                seed_capability(conn, target, dry_run=dry_run)
            finally:
                conn.close()
        else:
            print(f"Unknown capability: {target}. Known: {KNOWN_CAPABILITIES}")
            sys.exit(1)
    else:
        seed_all(dry_run=dry_run)
