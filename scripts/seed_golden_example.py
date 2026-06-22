from __future__ import annotations

"""Seed ~20 example golden-set rows for the executor node type.

These are GENERATED example data (labeled_by='example-generated') to make
the L3 calibration loop runnable without waiting on human labels.

Replace with real human labels before trusting in production.

Usage:
    uv run python scripts/seed_golden_example.py

Idempotent: skips rows where (node_type, split, task) already exists.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


EXAMPLES = [
    {"node_type":"executor","split":"calibration","task":"Implement FastAPI transactions CRUD with integer cents","artifact_blob":"# app.py\nclass Tx(BaseModel): amount_cents: int = Field(gt=0)\n\n@app.post('/transactions')\ndef create(tx: Tx):\n    # validates amount_cents > 0 via Field(gt=0)\n    ...\n\n# test_api.py\ndef test_create():\n    r = client.post('/transactions', json={'amount_cents': 500})\n    assert r.status_code == 200\n\ndef test_negative_rejected():\n    r = client.post('/transactions', json={'amount_cents': -100})\n    assert r.status_code == 422",
        "human_label": True,
        "expected_score": 0.92,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "clean integer cents, Field(gt=0) validation, tested",
    },
    {"node_type":"executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nclass Tx(BaseModel):\n    amount_cents: int\n    description: str\n\n@app.post('/transactions')\ndef create(tx: Tx):\n    if tx.amount_cents <= 0:\n        raise HTTPException(400, 'amount must be positive')\n    ...\n\n# tests cover create, list, delete",
        "human_label": True,
        "expected_score": 0.88,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "integer cents, explicit validation, tests cover CRUD",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nclass Tx(BaseModel):\n    amount_cents: int = Field(gt=0)\n    category: str\n\n@app.post('/transactions')\ndef create(tx: Tx):\n    db.insert(tx.dict())\n\n@app.get('/transactions')\ndef list():\n    return db.all()\n\n# tests pass",
        "human_label": True,
        "expected_score": 0.85,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "validates, CRUD complete, clean code",
    },
    # ── MEDIOCRE artifacts (calibration) ────────────────────────────────
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nclass Tx(BaseModel):\n    amount_cents: int  # no validation\n    description: str\n\n@app.post('/transactions')\ndef create(tx: Tx):\n    db.append(tx.dict())\n    return tx\n\n# tests: only test_create exists",
        "human_label": False,
        "expected_score": 0.55,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "integer cents but no validation, no negative test, thin tests",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\namount_cents: int = 0  # uses float-like default\n\n@app.post('/transactions')\ndef create(amount: float = Body()):  # FLOAT!\n    db.insert({'amount_cents': int(amount * 100)})\n    ...",
        "human_label": False,
        "expected_score": 0.30,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "float input, client-side conversion, no validation",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py uses Decimal for money\namount: Decimal = Field(max_digits=10, decimal_places=2)\n# no integer cents enforcement",
        "human_label": False,
        "expected_score": 0.25,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "Decimal type, no integer cents, float-like precision issues",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py uses str for amount\namount: str  # stored as string to 'avoid float issues'\n\n# no validation, no type checking\n@app.post('/transactions')\ndef create(amount: str):\n    db.insert({'amount': amount})\n\n# only one test: test_create_status_200",
        "human_label": False,
        "expected_score": 0.15,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "string money, no validation at all, single weak test",
    },
    # ── BORDERLINE artifacts (calibration) ──────────────────────────────
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nclass Tx(BaseModel):\n    amount_cents: int = Field(gt=0)\n\n@app.post('/transactions')\ndef create(tx: Tx):\n    db.insert(tx.dict())\n    return {'id': len(db)}\n\n@app.get('/transactions')\ndef list():\n    return db\n\n@app.delete('/transactions/{id}')\ndef delete(id: int):\n    if id >= len(db):\n        raise HTTPException(404)\n    db.pop(id)\n\n# tests: test_create, test_list, test_delete pass",
        "human_label": True,
        "expected_score": 0.80,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "all CRUD, integer cents with validation, tests pass but thin edge cases",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py uses Pydantic v2\nclass Tx(BaseModel):\n    amount_cents: int = Field(gt=0, description='Amount in cents')\n    description: str = Field(min_length=1)\n    created_at: datetime = Field(default_factory=datetime.utcnow)\n\n# full CRUD, validation on both fields, proper error messages",
        "human_label": True,
        "expected_score": 0.90,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "Pydantic v2, full validation, description required, timestamped",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py integer cents OK but tests don't verify edge cases\nclass Tx(BaseModel):\n    amount_cents: int\n\n# no Field(gt=0), relies on app-level check\n@app.post('/transactions')\ndef create(tx: Tx):\n    if tx.amount_cents < 0:\n        return JSONResponse({'error':'negative'}, status=400)\n    ...\n\n# tests missing negative/zero cases",
        "human_label": False,
        "expected_score": 0.60,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "integer cents, app-level validation but tests don't verify rejection",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nfrom enum import IntEnum\nclass Cents(int): pass  # noqa: custom type but no bounds\n\nclass Tx(BaseModel):\n    amount_cents: Cents\n    description: str\n\n# validation exists but allows zero/negative\n@app.post('/transactions')\ndef create(tx: Tx):\n    if tx.amount_cents == 0:\n        raise HTTPException(400, 'zero not allowed')\n    # NOTE: does NOT check negative!\n    ...",
        "human_label": False,
        "expected_score": 0.45,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "custom type but no negative check, zero checked but negative slips through",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nfrom pydantic import BaseModel, Field\n\nclass Transaction(BaseModel):\n    amount_cents: int = Field(..., gt=0, le=99999999)\n    description: str = Field(..., min_length=1, max_length=200)\n    type: str = Field(default='expense')\n\n# tests use pytest + httpx, cover:\n# - create transaction with valid amount\n# - reject negative amount (422)\n# - reject zero amount (422)\n# - reject empty description\n# - list returns all\n# - delete existing\n# - delete nonexistent (404)",
        "human_label": True,
        "expected_score": 0.95,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "comprehensive: Field constraints, 7 tests covering all edge cases, clean code",
    },
    {
        "node_type": "executor",
        "split": "calibration",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nfrom pydantic import BaseModel\n\nclass Item(BaseModel):\n    amount: float  # wrong type!\n    name: str\n\n@app.post('/items')\ndef create(item: Item):\n    return {'cents': int(item.amount * 100)}  # converts float to cents\n\n# tests pass but test amount is 5.00 (works), no edge cases",
        "human_label": False,
        "expected_score": 0.20,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "float model, runtime conversion, no integer-cents enforcement, floating point risk",
    },
    # ── HELDOUT artifacts (6 items) ──────────────────────────────────────
    {
        "node_type": "executor",
        "split": "heldout",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nclass Tx(BaseModel):\n    amount_cents: int = Field(gt=0)\n    description: str = Field(min_length=1)\n\n@app.post('/transactions')\ndef create(tx: Tx): return db.insert(tx)\n@app.get('/transactions')\ndef list(): return db.all()\n@app.delete('/transactions/{i}')\ndef delete(i: int): return db.pop(i)\n\n# tests: 4 tests, all pass, cover create/list/delete/negative-rejected",
        "human_label": True,
        "expected_score": 0.87,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "complete CRUD, proper validation, all tests pass",
    },
    {
        "node_type": "executor",
        "split": "heldout",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\namount_cents: int  # no gt=0\n\n@app.post('/transactions')\ndef create(amount: int):\n    if amount < 0:\n        abort(400, 'no negative')\n    db.insert({'cents': amount})\n    return {'ok': True}",
        "human_label": False,
        "expected_score": 0.50,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "integer type but no pydantic validation, basic app-level check, no test for negative",
    },
    {
        "node_type": "executor",
        "split": "heldout",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\n@app.post('/transactions')\ndef create(amount: int = Body()):\n    # money in cents\n    if amount <= 0:\n        return {'error': 'must be positive'}, 400\n    db.records.append({'cents': amount, 'id': len(db.records)})\n\n# tests minimal, no negative test",
        "human_label": False,
        "expected_score": 0.40,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "integer param, no model validation, basic check, no negative test",
    },
    {
        "node_type": "executor",
        "split": "heldout",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py full CRUD + integer cents + comprehensive tests + venv setup\n# see RUN.md for exact steps\n\n# app/main.py\nfrom pydantic import BaseModel, Field\nclass Tx(BaseModel):\n    amount_cents: int = Field(ge=1)  # at least 1 cent\n    note: str = ''\n\n# tests/ 7 tests including edge cases\n# .venv/ with all deps\n# RUN.md with run steps",
        "human_label": True,
        "expected_score": 0.93,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "production quality: full CRUD, validation, 7 tests, venv, RUN.md",
    },
    {
        "node_type": "executor",
        "split": "heldout",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py uses floats throughout\nclass Tx(BaseModel):\n    amount: float  # BUG: float money\n\n@app.post('/transactions')\ndef create(tx: Tx):\n    if tx.amount <= 0:\n        raise HTTPException(400, 'bad')\n    db.append({'amount': tx.amount})  # stored as float!",
        "human_label": False,
        "expected_score": 0.10,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "float money, no integer cents, stored as float, rounding errors certain",
    },
    {
        "node_type": "executor",
        "split": "heldout",
        "task": "Implement FastAPI transactions CRUD with integer cents",
        "artifact_blob": "# app.py\nfrom decimal import Decimal, ROUND_HALF_UP\n\nclass Tx(BaseModel):\n    amount_cents: int = Field(gt=0)\n    description: str\n\n# proper integer cents, validation, CRUD complete, 6 tests\n# includes test for negative, zero, large amounts, boundary values\n# .venv setup with requirements.txt",
        "human_label": True,
        "expected_score": 0.90,
        "rubric_item": "Is money stored as integer cents and are negative amounts rejected?",
        "notes": "proper integer cents, comprehensive tests, venv, boundary testing",
    },
]


def seed(conn=None) -> dict[str, int]:
    """Insert example golden rows. Idempotent: skips existing (node_type, split, task).

    Returns dict with keys: 'inserted', 'skipped'.
    """
    if conn is None:
        import psycopg

        url = os.environ.get("DATABASE_URL", "")
        if not url:
            print("FATAL: DATABASE_URL not set", file=sys.stderr)
            sys.exit(1)
        conn = psycopg.connect(url)

    inserted = 0
    skipped = 0
    item_id = uuid.uuid4()

    for ex in EXAMPLES:
        with conn.cursor() as cur:
            # Idempotent: skip if (node_type, split, artifact_blob) already exists
            cur.execute(
                "SELECT 1 FROM golden_set WHERE node_type = %s AND split = %s AND artifact_blob = %s",
                (ex["node_type"], ex["split"], ex["artifact_blob"]),
            )
            if cur.fetchone():
                skipped += 1
                continue

            human_label_json = {
                "score": ex["expected_score"],
                "criteria_met": {ex["rubric_item"]: ex["human_label"]},
                "notes": ex.get("notes", ""),
            }

            cur.execute(
                """INSERT INTO golden_set
                   (id, node_type, artifact_ref, rubric_item, human_label,
                    expected_score, labeled_by, frozen, task, artifact_blob, split)
                   VALUES (%s, %s, %s, %s, %s, %s, 'example-generated', TRUE,
                           %s, %s, %s)""",
                (
                    str(item_id),
                    ex["node_type"],
                    f"example:{ex['node_type']}/{ex['split']}/{item_id}",
                    ex["rubric_item"],
                    ex["human_label"],
                    ex["expected_score"],
                    ex["task"],
                    ex["artifact_blob"],
                    ex["split"],
                ),
            )
            item_id = uuid.uuid4()
            inserted += 1

    conn.commit()
    return {"inserted": inserted, "skipped": skipped}


def count_golden(conn=None) -> dict[str, int]:
    """Count golden rows by split."""
    if conn is None:
        import psycopg

        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return {}
        conn = psycopg.connect(url)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT split, COUNT(*) FROM golden_set GROUP BY split ORDER BY split"
        )
        rows = cur.fetchall()
    return {r[0]: r[1] for r in rows}


if __name__ == "__main__":
    import psycopg

    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    conn = psycopg.connect(url)
    result = seed(conn)
    print(f"Seeded: {result['inserted']} inserted, {result['skipped']} skipped")
    counts = count_golden(conn)
    print(f"Golden set totals: {counts}")
    conn.close()
