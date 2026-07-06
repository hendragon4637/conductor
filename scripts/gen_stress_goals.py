#!/usr/bin/env python3
"""Generate stress-test goals via free LiteLLM.

Produces 45 goals per domain (Software Delivery + Content Studio):
15 small + 15 medium + 15 large each = 90 total.

Usage:
    uv run python scripts/gen_stress_goals.py
"""
from __future__ import annotations

import sys
import json
import os
import uuid
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import psycopg

# LiteLLM gateway config
GATEWAY_URL = os.environ.get("LITELLM_GATEWAY_URL", "http://localhost:4000/v1")
GATEWAY_KEY = os.environ.get("LITELLM_GATEWAY_KEY") or os.environ.get("LITELLM_KEY_PLANNING", "")
GEN_MODEL = "deepseek-planning"

SCOPES = {
    "small": "single deliverable, 1-2 capabilities, one short plan node",
    "medium": "multi-component, 3-5 capabilities, realistic feature-level product",
    "large": "production-grade multi-service/multi-artifact system, 5+ capabilities, full lifecycle spec→build→tests→deploy→docs",
}

GOAL_GEN_PROMPT = """Generate {n} varied, realistic PRODUCTION-QUALITY {domain} goals at {scope} scope ({scope_desc}).

Span these capabilities: {caps}. Vary capabilities + complexity within the tier.
Each goal = a concrete deliverable a real team would ship.

Return a JSON array of objects, each with:
  - "title": short name
  - "spec": 1-2 sentence concrete spec
  - "expected_capabilities": array of capability names from the list above

OUTPUT ONLY valid JSON. No markdown, no explanations.
"""


def _call_llm(prompt: str) -> str | None:
    import urllib.request

    body = json.dumps({
        "model": GEN_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.8,
    }).encode()
    headers = {"Content-Type": "application/json"}
    if GATEWAY_KEY:
        headers["Authorization"] = f"Bearer {GATEWAY_KEY}"

    req = urllib.request.Request(
        f"{GATEWAY_URL}/chat/completions",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.loads(resp.read().decode())
            return data["choices"][0]["message"]["content"]
    except Exception as exc:
        print(f"  LLM call failed: {exc}")
        return None


def _parse_goals(text: str) -> list[dict]:
    """Parse JSON array from LLM response, stripping mark fences."""
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1]
    if text.endswith("```"):
        text = text.rsplit("```", 1)[0]
    text = text.strip()
    try:
        goals = json.loads(text)
        if isinstance(goals, list):
            return goals
    except json.JSONDecodeError:
        pass
    # Try extracting JSON array from the response
    import re
    match = re.search(r"\[.*\]", text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except json.JSONDecodeError:
            pass
    print(f"  WARNING: could not parse LLM response as JSON array")
    print(f"  Raw response (first 200): {text[:200]}")
    return []


def gen_goals(domain: str, caps: str, per_scope: int = 15) -> list[dict]:
    all_goals: list[dict] = []
    for scope, scope_desc in SCOPES.items():
        print(f"  Generating {per_scope} {scope} goals for {domain}...")
        prompt = GOAL_GEN_PROMPT.format(
            n=per_scope,
            domain=domain,
            scope=scope,
            scope_desc=scope_desc,
            caps=caps,
        )
        resp = _call_llm(prompt)
        if not resp:
            print(f"  FAILED to get response for {domain}/{scope}")
            continue
        goals = _parse_goals(resp)
        for g in goals:
            g["scope"] = scope
            g["domain"] = domain
        all_goals.extend(goals)
        print(f"    Got {len(goals)} goals")
    return all_goals


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("FATAL: DATABASE_URL not set")
        return 1

    software_caps = "requirements_spec, architecture_design, backend_api, frontend, tests_suite, deployment_iac, tech_docs"
    content_caps = "copywriting, image_gen, music_generation, design_layout, content_review"

    all_goals = []

    print("=== Generating Software Delivery goals ===")
    sw_goals = gen_goals("software_delivery", software_caps)
    all_goals.extend(sw_goals)
    print(f"  Total: {len(sw_goals)} goals")

    print("\n=== Generating Content Studio goals ===")
    cs_goals = gen_goals("content_studio", content_caps)
    all_goals.extend(cs_goals)
    print(f"  Total: {len(cs_goals)} goals")

    print(f"\n=== Total generated: {len(all_goals)} goals ===")

    # Persist to DB
    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        count = 0
        for g in all_goals:
            goal_id = f"sg-{uuid.uuid4().hex[:12]}"
            ec = json.dumps(g.get("expected_capabilities", []))
            try:
                cur.execute(
                    """
                    INSERT INTO stress_goals (id, domain, scope, title, spec, expected_capabilities, source)
                    VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'generated')
                    ON CONFLICT (id) DO NOTHING
                    """,
                    (goal_id, g["domain"], g["scope"], g["title"], g["spec"], ec),
                )
                if cur.rowcount > 0:
                    count += 1
            except Exception as exc:
                print(f"  DB insert failed for '{g.get('title', '?')}': {exc}")
        conn.commit()
        print(f"Persisted {count} goals to stress_goals table")

    # Summary
    from collections import Counter
    domain_counts = Counter(g["domain"] for g in all_goals)
    scope_counts = Counter(g["scope"] for g in all_goals)
    print(f"\nSummary by domain: {dict(domain_counts)}")
    print(f"Summary by scope:  {dict(scope_counts)}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
