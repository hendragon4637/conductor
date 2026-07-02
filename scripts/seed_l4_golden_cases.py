from __future__ import annotations

"""Seed 5 L4 golden cases for discrimination testing.

Tests the L4 evaluator's ability to discriminate between different failure
modes in LLM-generated technical documentation (SDK guides, API refs, CLI
docs). Each golden case exercises a specific failure mode across all three L4
rubric dimensions (discoverability, error_feedback, friction).

Failure modes covered:
  1. HALLUCINATION — invents non-existent API functions/parameters
  2. OMISSION     — factually correct but critically incomplete
  3. CONTRADICTION— self-contradictory across sections
  4. PERFECTION   — complete, accurate, well-structured (positive control)
  5. VAGUENESS    — technically correct but overly vague

Usage:
    uv run python scripts/seed_l4_golden_cases.py

Idempotent: skips rows where (node_type, split, artifact_blob, rubric_item)
already exists.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# ── Full L4 rubric ──────────────────────────────────────────────────────────────
# The three dimensions evaluated for every golden case.
L4_RUBRIC = {
    "name": "l4_usage",
    "applies_to": ["l4_persona"],
    "items": [
        {
            "id": "l4_discoverability",
            "rubric_item": (
                "Can a new user figure out the product's primary features "
                "without external docs or guidance?"
            ),
            "weight": 1.5,
        },
        {
            "id": "l4_error_feedback",
            "rubric_item": (
                "When the user makes a mistake or hits an error, does the "
                "product return clear, actionable feedback?"
            ),
            "weight": 1.5,
        },
        {
            "id": "l4_friction",
            "rubric_item": (
                "Do the primary user flows complete without unexpected "
                "failures, confusing responses, or broken steps?"
            ),
            "weight": 2.0,
        },
    ],
}

# ── Golden cases ────────────────────────────────────────────────────────────────
# Each case has: name, task, artifact_blob (the LLM output being evaluated),
# rubric_json (the full L4 rubric), and expectations (per-rubric-item scores
# and human labels).

CASES: list[dict] = [
    # ──────────────────────────────────────────────────────────────────────
    # CASE 1: HALLUCINATION — invents non-existent API functions/params
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "hallucination_sdk_docs",
        "task": "Integrate Acme Payments SDK v2 — set up client, create charge, handle webhook",
        "artifact_blob": (
            "# Acme Payments SDK v2 — Quickstart\n\n"
            "## Installation\n"
            "```bash\n"
            "pip install acme-payments==2.0.0\n"
            "```\n\n"
            "## Initialize Client\n"
            "```python\n"
            "from acme import Client\n"
            "client = Client(api_key='sk_live_...', version='v2')\n"
            "```\n\n"
            "## Create a Charge (Batch)\n"
            "Use the batch endpoint to create multiple charges at once:\n"
            "```python\n"
            "charges = client.charges.create_batch([\n"
            '    {"amount_cents": "500", "currency": "usd", "source": "tok_visa"},\n'
            '    {"amount_cents": 1500, "currency": "usd", "source": "tok_mc"},\n'
            "])\n"
            "```\n"
            "> Note: `amount_cents` accepts string or integer.\n\n"
            "## Webhook Verification\n"
            "```python\n"
            "from acme.webhooks import verify_signature\n"
            "payload = request.body()\n"
            "signature = request.headers['X-Acme-Signature-v2']\n"
            "event = verify_signature(payload, signature, webhook_secret='whsec_...')\n"
            "```\n\n"
            "The `X-Acme-Signature-v2` header is sent on all webhook events.\n\n"
            "## Idempotency\n"
            "Pass an `Idempotency-Key` header (UUID v4) to safely retry requests:\n"
            "```python\n"
            "client.charges.create(amount_cents=2000, idempotency_key=str(uuid4()))\n"
            "```\n"
            "> Note: The `charges.create_batch` method does not support idempotency keys.\n"
        ),
        "rubric_json": L4_RUBRIC,
        "expectations": {
            "l4_discoverability": {
                "human_label": False,
                "expected_score": 0.10,
                "notes": (
                    "Docs reference non-existent `charges.create_batch` method, "
                    "fictional `X-Acme-Signature-v2` header, and "
                    "`verify_signature()` import — user can't follow along."
                ),
            },
            "l4_error_feedback": {
                "human_label": False,
                "expected_score": 0.10,
                "notes": (
                    "No actual error documentation; the invented endpoints "
                    "return 404s the docs never mention."
                ),
            },
            "l4_friction": {
                "human_label": False,
                "expected_score": 0.05,
                "notes": (
                    "Persona attempting the quickstart would hit 404 on "
                    "batch create, missing header on webhooks, and wrong "
                    "import path — flow completely blocked."
                ),
            },
        },
    },
    # ──────────────────────────────────────────────────────────────────────
    # CASE 2: OMISSION — factually correct but critically incomplete
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "omission_error_handling",
        "task": "Set up recurring billing — create customer, subscribe, handle invoice",
        "artifact_blob": (
            "# Billing API — Recurring Payments\n\n"
            "## Create a Customer\n"
            "```python\n"
            "POST /v1/customers\n"
            '{"email": "user@example.com", "payment_method": "pm_card_visa"}\n'
            "```\n"
            "Returns the customer object with an `id`.\n\n"
            "## Create a Subscription\n"
            "```python\n"
            "POST /v1/subscriptions\n"
            '{"customer_id": "cus_123", "plan": "price_monthly"}\n'
            "```\n"
            "Returns the subscription object.\n\n"
            "## Retrieve an Invoice\n"
            "```python\n"
            "GET /v1/invoices/{invoice_id}\n"
            "```\n"
            "Returns the invoice object with `status`, `total`, and `lines`.\n\n"
            "## List Invoices\n"
            "```python\n"
            "GET /v1/invoices?customer_id=cus_123&limit=10\n"
            "```\n"
            "Returns a paginated list of invoices.\n\n"
            "## Webhooks\n"
            "Configure webhook endpoints in the dashboard to receive "
            "`invoice.payment_succeeded` and `invoice.payment_failed` events.\n"
        ),
        "rubric_json": L4_RUBRIC,
        "expectations": {
            "l4_discoverability": {
                "human_label": True,
                "expected_score": 0.70,
                "notes": (
                    "Basic CRUD is documented correctly — user can create "
                    "customer, subscribe, and fetch invoices without guessing."
                ),
            },
            "l4_error_feedback": {
                "human_label": False,
                "expected_score": 0.20,
                "notes": (
                    "Zero error documentation: no status codes, no error "
                    "response schemas, no failure scenarios mentioned. User "
                    "gets a 400/402/500 with no doc support."
                ),
            },
            "l4_friction": {
                "human_label": False,
                "expected_score": 0.40,
                "notes": (
                    "Happy path works but any deviation (expired card, "
                    "duplicate customer, rate limit) leaves the persona "
                    "stuck with no guidance — moderate friction."
                ),
            },
        },
    },
    # ──────────────────────────────────────────────────────────────────────
    # CASE 3: CONTRADICTION — self-contradictory across sections
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "contradiction_api_ref",
        "task": "Upload and process files via the File Processing API",
        "artifact_blob": (
            "# File Processing API v3 — Reference\n\n"
            "## Overview\n"
            "The File Processing API lets you upload, transform, and download "
            "files. All amounts are in integer cents.\n\n"
            "## Authentication\n"
            "> **Important:** Pass your API key in the `Authorization` header.\n\n"
            "```\n"
            "Authorization: Bearer sk_live_abc123\n"
            "```\n\n"
            "### Example Request\n"
            "```\n"
            "curl -X POST https://api.files.example.com/v3/upload \\\n"
            '  -H "Authorization: Bearer sk_live_abc123" \\\n'
            '  -F "file=@report.pdf" \\\n'
            "  -F 'options={\"watermark\": true}'\n"
            "```\n\n"
            "## Upload a File\n"
            "```\n"
            "POST /v3/upload\n"
            'Request: multipart/form-data with "file" field.\n'
            "Returns: `{ \"file_id\": \"file_xyz\", \"size_bytes\": 204800 }`\n"
            "```\n"
            "Maximum file size: **100 MB**.\n\n"
            "## Processing Options\n"
            "| Option      | Type    | Default | Description                     |\n"
            "|-------------|---------|---------|---------------------------------|\n"
            "| watermark   | boolean | false   | Overlays a stamp on each page   |\n"
            "| format      | string  | \"pdf\"   | Output format (pdf, png, svg)  |\n"
            "| dpi         | integer | 150     | Output resolution for raster     |\n\n"
            "## Rate Limits\n"
            "Free tier: **10 requests/second** per API key.\n"
            "Pro tier: **100 requests/second** per API key.\n"
            "See [billing docs] for details.\n\n"
            "## Error Codes\n"
            "| Code | Meaning                    |\n"
            "|------|----------------------------|\n"
            "| 400  | Bad request — invalid input |\n"
            "| 401  | Unauthorized — bad API key  |\n"
            "| 429  | Too many requests           |\n"
            "| 500  | Server error                |\n\n"
            "## Upload Limits\n"
            "Maximum file upload size is **50 MB**. Files exceeding this "
            "limit return a 413 error.\n"
        ),
        "rubric_json": L4_RUBRIC,
        "expectations": {
            "l4_discoverability": {
                "human_label": False,
                "expected_score": 0.15,
                "notes": (
                    "Overview says 'amounts in integer cents' for a file "
                    "processing API that has nothing to do with money. "
                    "Upload section says 100 MB max, upload limits section "
                    "says 50 MB — contradiction. API key in header vs. "
                    "example mixes Authorization and query param styles."
                ),
            },
            "l4_error_feedback": {
                "human_label": False,
                "expected_score": 0.10,
                "notes": (
                    "Error code table exists but contradicts itself on "
                    "file size limits (100 MB vs 50 MB). User doesn't know "
                    "which limit to trust."
                ),
            },
            "l4_friction": {
                "human_label": False,
                "expected_score": 0.10,
                "notes": (
                    "Persona can't trust any documented value — contradictory "
                    "limits, irrelevant mention of cents, and mixed auth "
                    "patterns cause repeated failures."
                ),
            },
        },
    },
    # ──────────────────────────────────────────────────────────────────────
    # CASE 4: PERFECTION — complete, accurate, well-structured (positive control)
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "perfection_cli_guide",
        "task": "Deploy and manage a service using the deployctl CLI tool",
        "artifact_blob": (
            "# deployctl — CLI Reference\n\n"
            "`deployctl` is a command-line tool for deploying and managing "
            "services on the Acme Cloud Platform.\n\n"
            "## Global Flags\n"
            "| Flag           | Env             | Description                     |\n"
            "|----------------|-----------------|---------------------------------|\n"
            "| `--profile`   | `DC_PROFILE`    | Profile name (default: default) |\n"
            "| `--region`    | `DC_REGION`     | Target region (us-east, eu-west)| \n"
            "| `--verbose`   | —               | Enable debug output             |\n"
            "| `--output`    | —               | Output format (table, json)     |\n\n"
            "## Commands\n\n"
            "### deployctl deploy\n"
            "Deploy a service from a manifest file.\n\n"
            "```\n"
            "deployctl deploy ./manifest.yaml --region eu-west --wait\n"
            "```\n\n"
            "**Arguments:**\n"
            "- `manifest` (required): Path to the YAML manifest file.\n\n"
            "**Flags:**\n"
            "- `--wait`: Block until the deployment completes.\n"
            "- `--timeout <sec>`: Max wait time in seconds (default: 300).\n"
            "- `--branch <name>`: Git branch to deploy (default: main).\n\n"
            "**Exit codes:**\n"
            "- `0`: Deployment succeeded.\n"
            "- `1`: Deployment failed (check logs with `deployctl logs`).\n"
            "- `2`: Invalid manifest (YAML parse error or validation failure).\n\n"
            "### deployctl logs\n"
            "Stream logs for a deployed service.\n\n"
            "```\n"
            "deployctl logs my-service --tail 50 --since 1h\n"
            "```\n\n"
            "**Arguments:**\n"
            "- `service` (required): Service name.\n\n"
            "**Flags:**\n"
            "- `--tail <N>`: Show last N lines (default: 100).\n"
            "- `--since <duration>`: Show logs since duration (e.g., 30m, 2h).\n"
            "- `--level <level>`: Filter by level (info, warn, error).\n"
            "- `--follow`, `-f`: Follow log output in real time.\n\n"
            "### deployctl status\n"
            "Show the current status of a deployment.\n\n"
            "```\n"
            "deployctl status my-service --output json\n"
            "```\n\n"
            "**Arguments:**\n"
            "- `service` (required): Service name.\n\n"
            "**Flags:**\n"
            "- `--output`: Output format (table, json). Overrides global flag.\n\n"
            "## Error Handling\n"
            "All commands return non-zero exit codes on failure and print "
            "an error message to stderr:\n"
            "```\n"
            "$ deployctl deploy missing.yaml 2>&1\n"
            'Error: manifest "missing.yaml": no such file or directory\n'
            "$\n"
            "```\n"
            "Common errors:\n"
            "- `Error: authentication required` — run `deployctl login` first.\n"
            "- `Error: region \"xxx\" not found` — use `deployctl regions` to list.\n"
            "- `Error: service \"xxx\" not found` — check `deployctl services`.\n\n"
            "## Examples\n"
            "```bash\n"
            "# Deploy with custom timeout and wait\n"
            "deployctl deploy manifest.yaml --region eu-west --timeout 600 --wait\n\n"
            "# Check deployment status as JSON\n"
            "deployctl status my-app --output json | jq '.state'\n\n"
            "# Follow error logs in real time\n"
            "deployctl logs my-app --level error --follow\n"
            "```\n"
        ),
        "rubric_json": L4_RUBRIC,
        "expectations": {
            "l4_discoverability": {
                "human_label": True,
                "expected_score": 0.90,
                "notes": (
                    "All commands, flags, and arguments are documented with "
                    "clear syntax, defaults, and env-var equivalents. A new "
                    "user can deploy without external docs."
                ),
            },
            "l4_error_feedback": {
                "human_label": True,
                "expected_score": 0.90,
                "notes": (
                    "Every error codepath is documented: exit codes per "
                    "command, common error messages with solutions, and "
                    "stderr examples. User knows exactly what went wrong."
                ),
            },
            "l4_friction": {
                "human_label": True,
                "expected_score": 0.95,
                "notes": (
                    "Complete primary flows with working examples, clear "
                    "error recovery, and consistent flag conventions. No "
                    "ambiguity or broken steps."
                ),
            },
        },
    },
    # ──────────────────────────────────────────────────────────────────────
    # CASE 5: VAGUENESS — technically correct but overly vague
    # ──────────────────────────────────────────────────────────────────────
    {
        "name": "vagueness_api_guide",
        "task": "Send messages and manage threads using the Acme Messaging API",
        "artifact_blob": (
            "# Acme Messaging API — Integration Guide\n\n"
            "## Overview\n"
            "The Acme Messaging API lets you send messages, manage threads, "
            "and handle delivery events. It's a RESTful API that returns JSON.\n\n"
            "## Authentication\n"
            "All requests require authentication. Make sure to include your "
            "API key in requests. See the authentication docs for details.\n\n"
            "## Send a Message\n"
            "```\n"
            "POST /v1/messages\n"
            "```\n"
            "Send a message to a user or channel. The request body should "
            "contain the necessary fields. The response contains the message "
            "details.\n\n"
            "```json\n"
            '// Example request body\n'
            '{\n'
            '  "to": "user_abc",\n'
            '  "text": "Hello!"\n'
            '}\n'
            "```\n\n"
            "## List Threads\n"
            "```\n"
            "GET /v1/threads\n"
            "```\n"
            "Returns a list of threads for the authenticated user. Handle "
            "pagination appropriately for large result sets.\n\n"
            "## Get Thread Messages\n"
            "```\n"
            "GET /v1/threads/{thread_id}/messages\n"
            "```\n"
            "Returns messages in a thread. Make sure to handle errors "
            "appropriately.\n\n"
            "## Delete a Message\n"
            "```\n"
            "DELETE /v1/messages/{message_id}\n"
            "```\n"
            "Deletes a message. Handle the response accordingly.\n\n"
            "## Webhooks\n"
            "Set up webhooks to receive delivery events. See our webhooks "
            "guide for more information.\n\n"
            "## Rate Limiting\n"
            "Be mindful of rate limits. If you exceed them, you'll receive "
            "a rate limit response. See the rate limiting docs for details.\n\n"
            "## Best Practices\n"
            "- Always handle errors gracefully.\n"
            "- Use pagination for list endpoints.\n"
            "- Implement retry logic with exponential backoff.\n"
            "- See our best practices guide for more tips.\n"
        ),
        "rubric_json": L4_RUBRIC,
        "expectations": {
            "l4_discoverability": {
                "human_label": False,
                "expected_score": 0.45,
                "notes": (
                    "Endpoints are listed correctly but critical details are "
                    "missing: no request body schema, no response schema, no "
                    "parameter descriptions. User can find the features but "
                    "can't use them without guessing or external docs."
                ),
            },
            "l4_error_feedback": {
                "human_label": False,
                "expected_score": 0.30,
                "notes": (
                    "No error documentation at all: no status codes, no "
                    "error response formats, no common failure scenarios. "
                    "'Handle errors appropriately' is not actionable."
                ),
            },
            "l4_friction": {
                "human_label": False,
                "expected_score": 0.40,
                "notes": (
                    "Basic happy-path might work through trial and error, "
                    "but every deviation causes friction: unknown request "
                    "format, undocumented pagination, untyped responses. "
                    "Persona wastes time reverse-engineering the API."
                ),
            },
        },
    },
]


def _flatten_cases() -> list[dict]:
    """Flatten nested golden cases into per-rubric-item rows.

    Each case produces one row per L4 rubric item (3 rows per case).
    """
    rows: list[dict] = []
    for case in CASES:
        for item in case["rubric_json"]["items"]:
            item_id = item["id"]
            rubric_text = item["rubric_item"]
            expect = case["expectations"][item_id]
            rows.append({
                "node_type": "l4_usage",
                "split": "calibration",
                "task": case["task"],
                "artifact_ref": f"example:l4/discrimination/{case['name']}/{item_id}",
                "artifact_blob": case["artifact_blob"],
                "rubric_item": rubric_text,
                "human_label": expect["human_label"],
                "expected_score": expect["expected_score"],
                "notes": expect["notes"],
                "case_name": case["name"],
                "rubric_json": case["rubric_json"],
            })
    return rows


def seed() -> dict[str, int]:
    """Insert L4 golden discrimination cases. Idempotent: skips existing."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import psycopg

    conn = psycopg.connect(url)
    inserted = 0
    skipped = 0

    for row in _flatten_cases():
        with conn.cursor() as cur:
            cur.execute(
                """SELECT 1 FROM golden_set
                   WHERE node_type = %s AND split = %s
                     AND artifact_blob = %s AND rubric_item = %s""",
                (row["node_type"], row["split"], row["artifact_blob"], row["rubric_item"]),
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
                    row["node_type"],
                    row["artifact_ref"],
                    row["rubric_item"],
                    row["human_label"],
                    row["expected_score"],
                    row["task"],
                    row["artifact_blob"],
                    row["split"],
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "skipped": skipped}


def print_cases() -> None:
    """Print the golden cases as a readable summary (no DB needed)."""
    print("=" * 72)
    print("L4 Golden Cases — Discrimination Testing Summary")
    print("=" * 72)

    for case in CASES:
        print(f"\n## {case['name']}")
        print(f"   Task: {case['task']}")
        print(f"   Text length: {len(case['artifact_blob'])} chars")
        print(f"   Expected scores:")
        for item in case["rubric_json"]["items"]:
            expect = case["expectations"][item["id"]]
            label = "PASS" if expect["human_label"] else "FAIL"
            print(f"     {item['id']:30s} {expect['expected_score']:.2f}  ({label})")
            print(f"     {'':30s} {expect['notes']}")

    total_rows = len(list(_flatten_cases()))
    print(f"\n{'=' * 72}")
    print(f"Total: {len(CASES)} cases, {total_rows} golden_set rows")
    print(f"{'=' * 72}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Seed L4 golden cases for discrimination testing"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print cases without inserting into DB",
    )
    args = parser.parse_args()

    if args.dry_run:
        print_cases()
        sys.exit(0)

    result = seed()
    print(f"L4 golden cases: {result['inserted']} inserted, {result['skipped']} skipped")
