#!/usr/bin/env python
"""List all agent_configs grouped by pattern."""
import os
from collections import defaultdict
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from backend.db import queries


def main():
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT pattern, agent_config_id, harness, domain, role, active FROM agent_configs ORDER BY pattern, agent_config_id"
        )
        rows = cur.fetchall()

    by_pattern = defaultdict(list)
    for r in rows:
        by_pattern[r["pattern"]].append(r)

    for pattern, items in sorted(by_pattern.items()):
        print(f"\n## {pattern} ({len(items)})")
        for r in items:
            mark = "\u2713" if r["active"] else "\u2014"
            print(f"  {mark} {r['agent_config_id']:35s} {r['harness']:12s} {r['domain']:12s} {r['role']}")


if __name__ == "__main__":
    main()
