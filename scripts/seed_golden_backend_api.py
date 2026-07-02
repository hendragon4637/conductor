"""Seed example golden-set rows for backend_api node type (example-generated).

Creates ~15 artifacts spanning good/mediocre/bad for L3 calibration.
Idempotent: skips rows where (node_type, split, artifact_blob) already exists.

Usage:
    uv run python scripts/seed_golden_backend_api.py
"""

from __future__ import annotations
import os, sys, uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

EXAMPLES = [
    # ── GOOD artifacts (calibration) ────────────────────────────────
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — validates url, unique codes, 302 redirect\nfrom fastapi import FastAPI, HTTPException\nfrom pydantic import BaseModel\nfrom starlette.responses import RedirectResponse\nimport uuid\n\napp = FastAPI()\nstore: dict[str, str] = {}\n\nclass Req(BaseModel):\n    url: str\n\n@app.post('/shorten')\ndef shorten(req: Req):\n    if not req.url.startswith(('http://', 'https://')):\n        raise HTTPException(400, 'Invalid URL')\n    code = uuid.uuid4().hex[:8]\n    store[code] = req.url\n    return {'short_code': code, 'original_url': req.url}\n\n@app.get('/{code}')\ndef redirect(code: str):\n    target = store.get(code)\n    if not target:\n        raise HTTPException(404)\n    return RedirectResponse(url=target, status_code=302)",
        "human_label": True,
        "expected_score": 0.92,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — validates, 302 ok, unique codes\n# tests: create+redirect+bad_input pass\nimport hashlib\nfrom fastapi import FastAPI, HTTPException\nfrom starlette.responses import RedirectResponse\n\napp = FastAPI()\ndb = {}\n\n@app.post('/shorten')\ndef shorten(url: str):\n    if not url.startswith('http'):\n        raise HTTPException(400)\n    code = hashlib.md5(url.encode()).hexdigest()[:8]\n    db[code] = url\n    return {'code': code}\n\n@app.get('/{code}')\ndef get(code: str):\n    if code not in db:\n        raise HTTPException(404)\n    return RedirectResponse(url=db[code], status_code=302)",
        "human_label": True,
        "expected_score": 0.85,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    # ── MEDIOCRE artifacts (calibration) ──────────────────────────────
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — validates, 302 ok, but no bad-input test\n# only tests: test_create + test_redirect pass",
        "human_label": False,
        "expected_score": 0.65,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — no input validation, accepts any string\n@app.post('/shorten')\ndef shorten(url: str):\n    code = rand_code()\n    db[code] = url\n    return {'code': code}\n\n# GET returns 200 with JSON, not 302 redirect",
        "human_label": False,
        "expected_score": 0.35,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — no validation, accepts junk, returns 200 not 302\n# test only covers create path, no redirect test, no bad-input test",
        "human_label": False,
        "expected_score": 0.20,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    # ── BAD artifacts (calibration) ─────────────────────────────────
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — stores as float amounts, wrong endpoint paths, no tests\n@app.post('/api/short')\ndef create(url: str):\n    return {'id': 1}  # just returns 1, doesn't store anything",
        "human_label": False,
        "expected_score": 0.10,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "calibration",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — comprehensive: Pydantic validation, 302 redirect, unique codes\n# tests: 4 tests covering create, redirect, bad-url, 404\nclass ShortenReq(BaseModel):\n    url: AnyHttpUrl\n\n@app.post('/shorten', status_code=201)\ndef create(req: ShortenReq):\n    code = secrets.token_urlsafe(6)[:8]\n    store[code] = str(req.url)\n    return {'code': code}\n\n@app.get('/{code}', status_code=302)\ndef resolve(code: str):\n    url = store.get(code)\n    if not url:\n        raise HTTPException(404)\n    return RedirectResponse(url=url)",
        "human_label": True,
        "expected_score": 0.95,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    # ── HELDOUT artifacts (5 items) ─────────────────────────────────
    {
        "split": "heldout",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — validation, 302, unique codes, pytest, .venv\n# full CRUD-style: create, redirect, 404 handling\n# tests: test_create_returns_code, test_redirect_302, test_bad_url_rejected, test_missing_404",
        "human_label": True,
        "expected_score": 0.90,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "heldout",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — valid, 302, but only one test exists\n@app.post('/shorten')\ndef shorten(url: str):\n    if '://' not in url:\n        raise HTTPException(400)\n    code = str(hash(url))[:8]\n    store[code] = url\n    return {'short': code}\n\n@app.get('/{code}')\ndef get(code: str):\n    url = store.get(code)\n    if not url:\n        raise HTTPException(404)\n    return RedirectResponse(url, 302)\n\n# only test_create_exists",
        "human_label": False,
        "expected_score": 0.60,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "heldout",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — validates url format, 302 redirect, tests cover all paths\n# strong: Pydantic AnyHttpUrl validation, 302, unique codes, 5 tests",
        "human_label": True,
        "expected_score": 0.88,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "heldout",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — no tests at all, weak validation\n@app.post('/shorten')\ndef shorten(data: dict):\n    store[data.get('url', '')] = str(uuid4())[:6]\n    return {'ok': True}",
        "human_label": False,
        "expected_score": 0.15,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
    {
        "split": "heldout",
        "task": "Build a tiny URL-shortener backend: POST /shorten + GET /{code} (302 redirect), in-memory, pytest",
        "artifact_blob": "# app.py — solid: Pydantic AnyHttpUrl, 302 redirect, codes unique, 6 tests\n# includes test for bad-url, redirect, 404, create-duplicate-url (same url -> new code)\n# .venv setup with requirements.txt + RUN.md",
        "human_label": True,
        "expected_score": 0.93,
        "rubric_item": "Does the implementation validate inputs, use correct HTTP status codes, and have passing tests?",
    },
]


def seed(conn=None) -> dict[str, int]:
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
            cur.execute(
                "SELECT 1 FROM golden_set WHERE node_type = %s AND split = %s AND artifact_blob = %s",
                ("backend_api", ex["split"], ex["artifact_blob"]),
            )
            if cur.fetchone():
                skipped += 1
                continue

            cur.execute(
                """INSERT INTO golden_set
                   (id, node_type, artifact_ref, rubric_item, human_label,
                    expected_score, labeled_by, frozen, task, artifact_blob, split)
                   VALUES (%s, %s, %s, %s, %s, %s, 'example-generated', TRUE,
                           %s, %s, %s)""",
                (
                    str(item_id),
                    "backend_api",
                    f"example:backend_api/{ex['split']}/{item_id}",
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
    if conn is None:
        import psycopg
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return {}
        conn = psycopg.connect(url)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT split, COUNT(*) FROM golden_set WHERE node_type = 'backend_api' GROUP BY split ORDER BY split"
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
    print(f"Golden set totals for backend_api: {counts}")
    conn.close()
