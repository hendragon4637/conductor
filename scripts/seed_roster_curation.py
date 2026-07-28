#!/usr/bin/env python3
"""Curate the ACTIVE roster from the full 452-config pool.

Idempotent — safe to re-run.

Usage:
    uv run python scripts/seed_roster_curation.py
"""

from __future__ import annotations

import json
import logging
import os
import sys

import psycopg

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

GROUPS: list[dict] = [
    {"group_id": "software-executors",
     "description": "backend/frontend/CLI build + test work",
     "families": ["software"]},
    {"group_id": "data-executors",
     "description": "pipelines, analytics, data transforms",
     "families": ["data"]},
    {"group_id": "content-writers",
     "description": "copy, docs, reports, reviews",
     "families": ["creative", "content", "research"]},
    {"group_id": "design-executors",
     "description": "layout, UI/UX design work",
     "families": ["design"]},
    {"group_id": "ops-deploy",
     "description": "deployment, IaC, CI/CD",
     "families": ["software"]},
    {"group_id": "reviewers",
     "description": "code/content review roles",
     "families": ["software", "creative"]},
    {"group_id": "planners",
     "description": "meta-planner + planning aids",
     "families": ["generic"]},
    {"group_id": "personas",
     "description": "L4 usage personas",
     "families": ["generic"]},
]

ACTIVE_CONFIGS: list[dict] = [
    # software-executors
    {"id": "python-development-fastapi-pro",            "group": "software-executors", "caps": []},
    {"id": "backend-development-backend-architect",     "group": "software-executors", "caps": []},
    {"id": "frontend-developer",                        "group": "software-executors", "caps": []},
    {"id": "typescript-pro",                            "group": "software-executors", "caps": []},
    {"id": "backend-development-test-automator",        "group": "software-executors", "caps": ["tests_suite"]},
    {"id": "unit-testing-test-automator",               "group": "software-executors", "caps": ["tests_suite"]},
    {"id": "python-pro",                                "group": "software-executors", "caps": []},
    {"id": "software-architect",                        "group": "software-executors", "caps": ["architecture_design"]},
    {"id": "rapid-prototyper",                          "group": "software-executors", "caps": ["gui_app"]},
    {"id": "senior-developer",                          "group": "software-executors", "caps": ["gui_app"]},
    {"id": "embedded-firmware-engineer",                "group": "software-executors", "caps": ["embedded_firmware"]},
    {"id": "arm-cortex-expert",                         "group": "software-executors", "caps": ["embedded_firmware"]},
    # ops-deploy
    {"id": "cicd-automation-deployment-engineer",       "group": "ops-deploy",        "caps": ["deployment_iac"]},
    # data-executors
    {"id": "data-engineer",                             "group": "data-executors",    "caps": []},
    {"id": "data-scientist",                            "group": "data-executors",    "caps": []},
    # content-writers
    {"id": "technical-writer",                          "group": "content-writers",   "caps": ["tech_docs"]},
    {"id": "documentation-generation-docs-architect",   "group": "content-writers",   "caps": ["tech_docs"]},
    {"id": "business-analyst",                          "group": "content-writers",   "caps": ["requirements_spec"]},
    {"id": "product-manager",                           "group": "content-writers",   "caps": ["requirements_spec"]},
    {"id": "search-specialist",                         "group": "content-writers",   "caps": []},
    {"id": "seo-content-writer",                        "group": "content-writers",   "caps": ["copywriting"]},
    {"id": "content-creator",                           "group": "content-writers",   "caps": ["copywriting"]},
    # reviewers
    {"id": "seo-content-auditor",                       "group": "reviewers",         "caps": ["content_review"]},
    {"id": "imp-code-reviewer",                         "group": "reviewers",         "caps": []},
    {"id": "comprehensive-review-code-reviewer",        "group": "reviewers",         "caps": []},
    # design-executors
    {"id": "ui-designer",                               "group": "design-executors",  "caps": ["design_layout"]},
    {"id": "ui-ux-designer",                            "group": "design-executors",  "caps": ["design_layout"]},
    # planners
    {"id": "meta-planner",                              "group": "planners",          "caps": []},
    {"id": "opencode:backend-planner",                  "group": "planners",          "caps": []},
    # generic fallback
    {"id": "opencode:backend-executor",                 "group": "software-executors","caps": []},
]


def get_db_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        logger.error("DATABASE_URL is not set")
        sys.exit(1)
    return url


def seed_groups(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        for g in GROUPS:
            cur.execute(
                """INSERT INTO roster_groups (group_id, description, families)
                   VALUES (%s, %s, %s::jsonb)
                   ON CONFLICT (group_id) DO NOTHING""",
                (g["group_id"], g["description"], json.dumps(g["families"])),
            )
    conn.commit()
    logger.info("Seeded %d roster_groups", len(GROUPS))
    return len(GROUPS)


def deactivate_all(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute("UPDATE agent_configs SET active = false")
        deactivated = cur.rowcount
    conn.commit()
    logger.info("Deactivated %d configs", deactivated)
    return deactivated


def activate_picks(conn: psycopg.Connection) -> int:
    activated = 0
    with conn.cursor() as cur:
        for cfg in ACTIVE_CONFIGS:
            cur.execute(
                """UPDATE agent_configs
                      SET active = true, group_id = %s
                    WHERE agent_config_id = %s""",
                (cfg["group"], cfg["id"]),
            )
            if cur.rowcount:
                activated += 1

            for cap in cfg.get("caps", []):
                cur.execute(
                    """UPDATE agent_configs
                          SET new_capabilities = new_capabilities || %s::jsonb
                        WHERE agent_config_id = %s
                          AND NOT (new_capabilities ? %s)""",
                    (json.dumps([cap]), cfg["id"], cap),
                )
                if cur.rowcount:
                    logger.info("  Added cap '%s' to %s", cap, cfg["id"])
    conn.commit()
    logger.info("Activated %d configs", activated)
    return activated


def activate_personas(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE agent_configs
                  SET active = true, group_id = 'personas'
                WHERE role = 'l4_persona'
                  AND source = 'hand'
                  AND NOT active""",
        )
        activated = cur.rowcount
    conn.commit()
    if activated:
        logger.info("Activated %d hand-authored persona configs", activated)
    return activated


def safety_net(conn: psycopg.Connection) -> int:
    with conn.cursor() as cur:
        cur.execute(
            """UPDATE agent_configs
                  SET group_id = 'software-executors'
                WHERE active AND group_id IS NULL""",
        )
        fixed = cur.rowcount
    conn.commit()
    if fixed:
        logger.info("Safety net: assigned %d ungrouped active configs to software-executors", fixed)
    return fixed


def verify_coverage(conn: psycopg.Connection) -> None:
    used_caps = [
        "backend_api", "frontend", "tests_suite", "cli_tool",
        "architecture_design", "data_pipeline", "analytics_assistant",
        "deployment_iac", "tech_docs", "requirements_spec",
        "research_report", "copywriting", "content_review",
        "design_layout", "generic", "gui_app", "embedded_firmware",
    ]
    uncovered = []
    with conn.cursor() as cur:
        for cap in used_caps:
            cur.execute(
                """SELECT 1 FROM agent_configs
                    WHERE active AND new_capabilities ? %s
                    LIMIT 1""",
                (cap,),
            )
            if not cur.fetchone():
                uncovered.append(cap)

    if uncovered:
        logger.warning("UNCOVERED capabilities (no active config): %s", uncovered)
    else:
        logger.info("All %d stress capabilities are staffable by ≥1 active config", len(used_caps))


def main() -> None:
    db_url = get_db_url()
    logger.info("Connecting to database …")
    conn = psycopg.connect(db_url)
    try:
        logger.info("── Roster groups ──")
        seed_groups(conn)

        logger.info("── Deactivating all configs ──")
        deactivate_all(conn)

        logger.info("── Activating picks ──")
        activate_picks(conn)

        logger.info("── Activating hand personas ──")
        activate_personas(conn)

        logger.info("── Safety net ──")
        safety_net(conn)

        logger.info("── Coverage check ──")
        verify_coverage(conn)

        logger.info("── Done ──")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
