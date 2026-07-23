"""Seed default_checks into an active python-backend agent_config."""
import json
import os

import psycopg

default_checks = {
    "l1": [
        {
            "id": "tests_pass",
            "kind": "shell",
            "cmd": "python3 -m pytest -q --tb=short 2>&1",
            "expect": {"exit_code": 0},
            "on_fail": {
                "what": "Tests failed",
                "how": "Read the failing assertion in the pytest output and fix the implementation",
                "evidence_from": "stdout",
            },
        },
        {
            "id": "python_syntax",
            "kind": "shell",
            "cmd": (
                'files=$(find . -name "*.py" -not -path "./.git/*" -not -path "./.venv/*"); '
                'if [ -z "$files" ]; then echo "No Python files found"; exit 1; fi; '
                "python3 -m py_compile $files 2>&1"
            ),
            "expect": {"exit_code": 0},
            "on_fail": {
                "what": "Python syntax errors detected",
                "how": "Fix the syntax errors reported by python3 -m py_compile in the checked files",
                "evidence_from": "stdout",
            },
        },
    ],
    "l2": [
        {"id": "integer_cents", "rubric_item": "Money stored as integer cents (no float)?", "weight": 1.5},
        {"id": "validation", "rubric_item": "Inputs validated (reject negative/empty)?", "weight": 1.0},
        {"id": "tests_pass_l2", "rubric_item": "All existing tests still pass after changes?", "weight": 1.0},
        {"id": "types_used", "rubric_item": "Type hints on all new public functions?", "weight": 1.0},
    ],
}

db_url = os.environ.get("DATABASE_URL", "")
if not db_url:
    print("DATABASE_URL not set")
    exit(1)

dc_json = json.dumps(default_checks)
conn = psycopg.connect(db_url)
cur = conn.cursor()
cur.execute(
    "UPDATE agent_configs SET default_checks = %s::jsonb WHERE agent_config_id = %s",
    (dc_json, "python-development-fastapi-pro"),
)
conn.commit()
print(f"Updated {cur.rowcount} row(s)")
cur.close()
conn.close()
