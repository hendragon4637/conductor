#!/usr/bin/env python3
"""CLI: Install global skills and optionally agents to harness directories.

Usage:
  uv run python scripts/renderer.py               # install global skills
  uv run python scripts/renderer.py --agents       # also install imported agents
  uv run python scripts/renderer.py --list-harnesses
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

from sqlalchemy import text
from backend.skills import HarnessRenderer, RENDERERS, install_global_skills, install_global_agents, backend_supports

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("renderer")


def main() -> int:
    logger.info("=" * 60)
    logger.info("Conductor Agent Profiles — File 04 — Harness Renderers")
    logger.info("=" * 60)

    import argparse
    parser = argparse.ArgumentParser(description="Render agent profiles to harness files")
    parser.add_argument("--agents", action="store_true", help="Also render imported agents")
    parser.add_argument("--harness", default="opencode", help="Harness to render for")
    parser.add_argument("--list-harnesses", action="store_true", help="List registered renderers")
    args = parser.parse_args()

    if args.list_harnesses:
        logger.info("Registered renderers: %s", ", ".join(sorted(RENDERERS)))
        for name, r in RENDERERS.items():
            logger.info("  %s: %s", name, type(r).__name__)
        return 0

    renderer = RENDERERS.get(args.harness)
    if not renderer:
        logger.error("Unknown harness '%s'. Available: %s", args.harness, ", ".join(RENDERERS))
        return 1

    from backend.skills import _make_engine
    try:
        engine = _make_engine()
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        logger.info("Database connection OK")
    except Exception as exc:
        logger.error("Database connection failed: %s", exc)
        return 1

    logger.info("\n--- Installing global skills for '%s' ---", args.harness)
    skill_count = install_global_skills(engine, renderer)

    agent_count = 0
    if args.agents:
        logger.info("\n--- Installing global agents for '%s' ---", args.harness)
        agent_count = install_global_agents(engine, renderer)

    logger.info("\n" + "=" * 60)
    logger.info("RENDER COMPLETE")
    logger.info("  Harness:        %s", args.harness)
    logger.info("  Global skills:  %d", skill_count)
    logger.info("  Global agents:  %d (--agents=%s)", agent_count, args.agents)
    logger.info("  Registered harnesses: %s", ", ".join(sorted(RENDERERS)))
    logger.info("=" * 60)
    return 0


if __name__ == "__main__":
    sys.exit(main())
