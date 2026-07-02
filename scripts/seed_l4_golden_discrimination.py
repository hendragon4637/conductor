from __future__ import annotations

"""Seed L4 golden discrimination scenario — product documentation quality.

Adds two golden-set rows (good case + broken case) that test the evaluator's
ability to distinguish between a well-structured, accurate product description
and one with hallucinations, inconsistencies, and missing coverage.

The rubric is a multi-criteria JSON:
  - factual_accuracy (0.30) — commands/flags/features exist and are correct
  - internal_consistency (0.25) — flag names, arguments, terminology agree
  - complete_coverage (0.20) — all required sections are present and detailed
  - no_hallucinated_apis (0.15) — no fabricated commands, flags, or platforms
  - example_quality (0.10) — examples are correct and match documented signatures

Domain: technical CLI tool documentation (AcmeCLI cloud deployment tool).

Expected discrimination:
  - Good case:  all criteria met → expected_score >= 0.85
  - Broken case: all criteria failed → expected_score <= 0.30

Usage:
    uv run python scripts/seed_l4_golden_discrimination.py

Idempotent: skips rows where (node_type, split, task) already exists.
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── Multi-criteria rubric JSON ──────────────────────────────────────────────

DISCRIMINATION_RUBRIC_JSON = r"""{
  "rubric_name": "technical_documentation_quality",
  "version": 1,
  "criteria": [
    {
      "id": "factual_accuracy",
      "description": "All commands, flags, and features described exist and behave as documented. No hallucinated functionality.",
      "weight": 0.30
    },
    {
      "id": "internal_consistency",
      "description": "Flag names, argument patterns, and terminology are consistent across all commands with no contradictions.",
      "weight": 0.25
    },
    {
      "id": "complete_coverage",
      "description": "All required sections (overview, installation, command reference, examples) are present and adequately detailed.",
      "weight": 0.20
    },
    {
      "id": "no_hallucinated_apis",
      "description": "No fabricated commands, flags, platforms, install methods, or dependencies that do not exist in the real product.",
      "weight": 0.15
    },
    {
      "id": "example_quality",
      "description": "Examples are syntactically correct, use realistic values, and match documented command signatures.",
      "weight": 0.10
    }
  ]
}"""


# ── Good artifact: well-structured, accurate, complete ──────────────────────

GOOD_ARTIFACT = """# AcmeCLI - Cloud Deployment Tool

## Overview
AcmeCLI is a command-line tool for deploying containerized applications to the Acme Cloud platform. It supports creating and managing deployments, viewing logs, scaling services, and configuring environment variables.

## Installation
```bash
curl -fsSL https://acme.dev/install.sh | bash
```

## Quick Start
Deploy a simple web service:
```bash
acme deploy web-api --image nginx:alpine --port 80
```

## Commands

### acme deploy
Deploy a containerized service to Acme Cloud.

**Usage:**
```
acme deploy <service-name> --image <image> [options]
```

**Arguments:**
- `service-name` — Name for the service (required)

**Options:**
- `--image` — Container image to deploy (required)
- `--port` — Internal port the service listens on (default: 8080)
- `--region` — Target deployment region: us-east, eu-west, ap-south (default: us-east)
- `--replicas` — Number of instances (default: 1, max: 100)

**Example:**
```bash
acme deploy api-gateway \
  --image ghcr.io/myorg/gateway:v1.2.3 \
  --port 3000 \
  --region eu-west \
  --replicas 3
```

### acme logs
Retrieve logs from a deployed service.

**Usage:**
```
acme logs <service-name> [options]
```

**Options:**
- `--tail` — Stream logs continuously
- `--since` — Time duration to look back (e.g., 5m, 2h, 1d)
- `--level` — Filter by severity: info, warn, error (default: info)

**Example:**
```bash
acme logs api-gateway --tail --since 30m --level error
```

### acme scale
Change the replica count for a service.

**Usage:**
```
acme scale <service-name> --replicas <count>
```

**Options:**
- `--replicas` — Target number of instances (1-100, required)

**Example:**
```bash
acme scale api-gateway --replicas 5
```

### acme env
Manage environment variables for a service.

**Subcommands:**
- `acme env list <service-name>` — List all environment variables
- `acme env set <service-name> KEY=VALUE [KEY=VALUE...]` — Set one or more variables
- `acme env unset <service-name> KEY [KEY...]` — Remove one or more variables

**Example:**
```bash
acme env set api-gateway LOG_LEVEL=debug MAX_CONNECTIONS=100
```

### Global Flags
- `--verbose` — Enable verbose output
- `--json` — Output in JSON format
- `--profile` — Use a named profile (default: default)

## Exit Codes
| Code | Meaning |
|------|---------|
| 0    | Success |
| 1    | General error |
| 2    | Invalid arguments |
| 3    | Service not found |
| 4    | Rate limited |
"""


# ── Broken artifact: hallucinations, inconsistencies, missing sections ──────

BROKEN_ARTIFACT = """# AcmeCLI - The Ultimate Cloud Tool (v3.0)

## Overview
AcmeCLI deploys containerized apps to Acme Cloud. The most powerful CLI in the universe.

## Installation
Install via pip: `pip install acme-cli`
Or via npm: `npm install -g acme-cli`
Or download from https://get.acmecli.com/download/latest

## Quick Start
Push your code to the cloud:
```bash
acme push web-api --source ./app
```

## Commands

### acme deploy
Deploy a docker container.

**Usage:**
```
acme deploy <name> --dockerfile <path> [options]
```

**Arguments:**
- `name` — Service name

**Options:**
- `--dockerfile` — Path to Dockerfile (default: ./Dockerfile)
- `--port` — Internal port
- `--port-external` — External port
- `--region` — Region
- `--count` — Number of replicas (max: 10)

**Example:**
```bash
acme deploy my-api \
  --dockerfile ./deploy/Dockerfile \
  --port 8080 \
  --port-external 443 \
  --region moon-base \
  --count 9000
```

This deploys to our lunar data center in the moon-base region (currently in alpha).

### acme logs
Show logs. Use --follow to see live logs.

**Usage:**
```
acme logs <name>
```

### acme scale
Scale the number of instances.

**Usage:**
```
acme scale <name> --instances <n>
```

### acme env
Set environment variables.

**Subcommands:**
- `acme env list <name>`
- `acme env set <name> KEY=VALUE`

**Note:** `acme env` is deprecated in v3. Use `acme config` instead. But `acme config` hasn't been released yet, so keep using `acme env` for now.

### acme teleport (PREMIUM)
Instantly teleport your deployment to another data center. Requires the quantum entanglement module (purchased separately).

**Usage:**
```
acme teleport <service-name> --target <region>
```

**Example:**
```bash
acme teleport api-gateway --target mars-colony
```

> Teleportation uses proprietary quantum-entanglement technology to achieve sub-light-speed data relocation.

### acme insights (BETA)
AI-powered deployment insights that predict failures before they happen.

**Usage:**
```
acme insights analyze <service-name>
```

Uses a proprietary neural network model (AcmeNet-9000) trained on millions of deployments. Currently in closed beta.

## Configuration
AcmeCLI reads ~/.acme/config.yaml:
```yaml
region: mars-colony
features:
  teleport: true
  insights: true
api_key: sk-abc123
```

## Global Flags
- `-v` — Verbose mode
- `--format` — Output format: text, json, xml
- `-p` — Profile

## Exit Codes
| Code | Meaning |
|------|---------|
| 0    | OK |
| 1    | Error |
| 99   | Quantum decoherence detected |
"""


# ── Golden entries ──────────────────────────────────────────────────────────

TASK = "AcmeCLI deployment tool — evaluate product documentation quality with discrimination rubric"

ENTRIES = [
    {
        "node_type": "l4_usage",
        "split": "calibration",
        "task": TASK,
        "artifact_blob": GOOD_ARTIFACT,
        "rubric_item": DISCRIMINATION_RUBRIC_JSON,
        "human_label": True,
        "expected_score": 0.88,
        "notes": "Good case: well-structured, accurate commands, consistent flags, complete sections, realistic examples, no hallucinated features",
    },
    {
        "node_type": "l4_usage",
        "split": "calibration",
        "task": TASK,
        "artifact_blob": BROKEN_ARTIFACT,
        "rubric_item": DISCRIMINATION_RUBRIC_JSON,
        "human_label": False,
        "expected_score": 0.20,
        "notes": "Broken case: hallucinated install methods, fabricated commands (teleport/insights), inconsistent flag names, contradictory constraints, fake region 'moon-base'",
    },
]


def seed() -> dict[str, int]:
    """Insert L4 golden discrimination rows. Idempotent: skips existing."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        print("FATAL: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import psycopg

    conn = psycopg.connect(url)
    inserted = 0
    skipped = 0

    for entry in ENTRIES:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM golden_set WHERE node_type = %s AND split = %s AND task = %s",
                (entry["node_type"], entry["split"], entry["task"]),
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
                    entry["node_type"],
                    f"example:l4/discrimination/{item_id}",
                    entry["rubric_item"],
                    entry["human_label"],
                    entry["expected_score"],
                    entry["task"],
                    entry["artifact_blob"],
                    entry["split"],
                ),
            )
            inserted += 1

    conn.commit()
    conn.close()
    return {"inserted": inserted, "skipped": skipped}


def count_entries(conn=None) -> dict[str, int]:
    """Count golden rows for this task, grouped by split."""
    if conn is None:
        import psycopg

        url = os.environ.get("DATABASE_URL", "")
        if not url:
            return {}
        conn = psycopg.connect(url)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT split, COUNT(*) FROM golden_set WHERE task = %s GROUP BY split ORDER BY split",
            (TASK,),
        )
        rows = cur.fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


if __name__ == "__main__":
    result = seed()
    print(f"L4 golden discrimination: {result['inserted']} inserted, {result['skipped']} skipped")
    counts = count_entries()
    print(f"Discrimination entries: {counts}")
