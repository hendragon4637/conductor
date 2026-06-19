from __future__ import annotations

"""Seed ~6 example L4 golden cases for testing.

These are GENERATED example data (labeled_by='example-generated') to make
the L4 calibration loop runnable.  Replace with real human labels before
production trust.

Usage:
    uv run python scripts/seed_l4_golden_example.py
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


EXAMPLES = [
    {"node_type":"l4_usage","split":"calibration","task":"Personal finance tracker — add, list, delete transactions","artifact_blob":"Persona added 3 transactions, listed them, deleted 1. All CRUD operations returned correct HTTP statuses. Amounts displayed in cents. Delete had no confirmation prompt but worked.","human_label":True,"expected_score":0.80,"rubric_item":"Did the persona complete the end-to-end user goal in the running product?","notes":"Works but delete lacks confirmation"},
    {"node_type":"l4_usage","split":"calibration","task":"Personal finance tracker — add, list, delete transactions","artifact_blob":"Persona added 2 transactions, listed them, deleted 1 with confirmation dialog. All operations smooth. Amounts formatted as dollars (expected cents). Minor formatting issue.","human_label":True,"expected_score":0.85,"rubric_item":"Did the persona complete the end-to-end user goal in the running product?","notes":"Functional but display format mismatch"},
    {"node_type":"l4_usage","split":"calibration","task":"Personal finance tracker — add, list, delete transactions","artifact_blob":"Persona attempted to add a transaction. Form submission returned 500 error. Cannot proceed.","human_label":False,"expected_score":0.10,"rubric_item":"Did the persona complete the end-to-end user goal in the running product?","notes":"Broken — server error on create"},
    {"node_type":"l4_usage","split":"calibration","task":"Personal finance tracker — add, list, delete transactions","artifact_blob":"Persona added transaction OK, listed OK, delete returned 404. Cannot complete full cycle.","human_label":False,"expected_score":0.25,"rubric_item":"Did the persona complete the end-to-end user goal in the running product?","notes":"Delete endpoint broken"},
    {"node_type":"l4_usage","split":"heldout","task":"Personal finance tracker — add, list, delete transactions","artifact_blob":"Persona completed full cycle: add 2, list (saw both), delete first (confirmed), list (only second remains). All steps smooth with clear feedback.","human_label":True,"expected_score":0.95,"rubric_item":"Did the persona complete the end-to-end user goal in the running product?","notes":"Full cycle, confirmation, clean UX"},
    {"node_type":"l4_usage","split":"heldout","task":"Personal finance tracker — add, list, delete transactions","artifact_blob":"Persona opened the app. Page is blank (no content loaded). Cannot interact.","human_label":False,"expected_score":0.0,"rubric_item":"Did the persona complete the end-to-end user goal in the running product?","notes":"Blank page — app not rendering"},
]


def seed() -> dict:
    """Insert example L4 golden rows. Idempotent: skips existing."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import psycopg
    conn = psycopg.connect(url)
    inserted = 0
    skipped = 0

    for ex in EXAMPLES:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM golden_set WHERE node_type = %s AND split = %s AND task = %s",
                (ex["node_type"], ex["split"], ex["task"]),
            )
            if cur.fetchone():
                skipped += 1
                continue

            item_id = str(uuid.uuid4())
            cur.execute(
                """INSERT INTO golden_set
                   (id, node_type, artifact_ref, rubric_item, human_label,
                    expected_score, labeled_by, frozen, task, artifact_blob, split)
                   VALUES (%s, %s, %s, %s, %s, %s, 'example-generated', TRUE,
                           %s, %s, %s)""",
                (
                    item_id,
                    ex["node_type"],
                    f"example:l4/{ex['split']}/{item_id}",
                    ex["rubric_item"],
                    ex["human_label"],
                    ex["expected_score"],
                    ex["task"],
                    ex["artifact_blob"],
                    ex["split"],
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "skipped": skipped}


if __name__ == "__main__":
    result = seed()
    print(f"L4 golden: {result['inserted']} inserted, {result['skipped']} skipped")
