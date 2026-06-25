"""Domain profile model, retrieval, inference, and seeding.

A domain profile encodes the acceptance criteria, conventions, and quality
dimensions for a type of deliverable (software_app, cli_script, etc.).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg
from psycopg import Connection
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class DomainProfile(BaseModel):
    """Profile for a single deliverable domain."""

    domain: str
    acceptance: dict[str, Any] = Field(
        description="deliverables, runnable_check, completeness_criteria, quality_dimensions"
    )
    conventions: list[str] = Field(default_factory=list)
    custom: dict[str, Any] = Field(default_factory=dict)
    version: int = Field(default=1, ge=1)
    source: str = Field(default="example-generated")


SEED_PROFILES: list[dict[str, Any]] = [
    {
        "domain": "software_app",
        "acceptance": {
            "deliverables": ["backend code", "frontend/UI", "tests", "RUN.md"],
            "runnable_check": "app starts from .venv and the primary user flow works end-to-end",
            "completeness_criteria": [
                "FE present (not backend-only)",
                "deps installable in worktree venv",
                "documented run steps",
            ],
            "quality_dimensions": [
                "correctness",
                "input validation",
                "data integrity",
                "usable UX",
            ],
        },
        "conventions": [
            "an 'app' means runnable end-to-end (FE+BE), not backend-only",
            "all deps install into a worktree .venv; no host installs",
            "include RUN.md with exact run steps",
            "money/quantities use safe types (e.g. integer cents)",
        ],
        "custom": {"default_stack": "FastAPI + minimal HTML/JS unless specified"},
    },
    {
        "domain": "cli_script",
        "acceptance": {
            "deliverables": ["script", "tests", "usage in README"],
            "runnable_check": "`python -m <tool> --help` works and the main command runs",
            "completeness_criteria": ["argparse/CLI interface", "handles bad input"],
            "quality_dimensions": ["correctness", "error handling", "clear help text"],
        },
        "conventions": [
            "a CLI tool means runnable from the shell with --help",
            "deps in .venv",
            "include usage examples",
        ],
        "custom": {},
    },
    {
        "domain": "api_service",
        "acceptance": {
            "deliverables": ["API code", "tests", "OpenAPI/endpoint docs", "RUN.md"],
            "runnable_check": "server starts; documented endpoints return expected status codes",
            "completeness_criteria": [
                "all stated endpoints implemented",
                "input validation",
                "tests hit each endpoint",
            ],
            "quality_dimensions": [
                "RESTful design",
                "correct status codes",
                "validation",
                "data integrity",
            ],
        },
        "conventions": [
            "an API means runnable server + documented endpoints",
            "validate inputs; correct HTTP codes",
            "deps in .venv",
        ],
        "custom": {},
    },
    {
        "domain": "data_pipeline",
        "acceptance": {
            "deliverables": ["pipeline code", "sample input/output", "tests", "README"],
            "runnable_check": "pipeline runs on sample input and produces expected-shape output",
            "completeness_criteria": [
                "reads input",
                "transforms",
                "writes output",
                "handles malformed rows",
            ],
            "quality_dimensions": [
                "correctness",
                "robustness to bad data",
                "reproducibility",
            ],
        },
        "conventions": [
            "a pipeline means runnable on sample data with documented I/O schema",
            "validate/skip malformed rows; don't crash",
            "deps in .venv",
        ],
        "custom": {},
    },
    {
        "domain": "research_report",
        "acceptance": {
            "deliverables": ["report document", "sources/citations"],
            "runnable_check": None,
            "completeness_criteria": [
                "covers all stated questions",
                "cites sources",
                "has a conclusion",
            ],
            "quality_dimensions": ["accuracy", "completeness", "clarity", "sourcing"],
        },
        "conventions": [
            "a report means cited claims, structured sections, a clear conclusion",
            "no fabricated sources",
        ],
        "custom": {"format": "markdown"},
    },
    {
        "domain": "generic",
        "acceptance": {
            "deliverables": ["the stated deliverable"],
            "runnable_check": None,
            "completeness_criteria": [
                "achieves the stated goal",
                "no TODOs/stubs where finished work required",
            ],
            "quality_dimensions": ["meets goal", "completeness", "quality for purpose"],
        },
        "conventions": [],
        "custom": {},
    },
]


def _get_db_url() -> str:
    return os.environ["DATABASE_URL"]


def _find_seed_profile(domain: str) -> dict | None:
    for p in SEED_PROFILES:
        if p["domain"] == domain:
            return p
    return None


def get_domain_profile(domain: str) -> DomainProfile:
    # Try DB first
    try:
        dsn = _get_db_url()
        with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT domain, acceptance, conventions, custom, version, source "
                "FROM domain_profiles WHERE domain = %s",
                (domain,),
            )
            row = cur.fetchone()
            if row is not None:
                return DomainProfile(
                    domain=row["domain"],
                    acceptance=row["acceptance"],
                    conventions=row["conventions"],
                    custom=row["custom"],
                    version=row["version"],
                    source=row["source"],
                )
    except Exception:
        logger.exception("Failed to query domain_profile for '%s'", domain)

    # Fallback to seed profiles (works when DB table doesn't exist yet)
    seed = _find_seed_profile(domain)
    if seed is not None:
        return DomainProfile(**seed)

    # Final fallback to generic
    return _generic_fallback()


def _generic_fallback() -> DomainProfile:
    for p in SEED_PROFILES:
        if p["domain"] == "generic":
            return DomainProfile(**p)
    return DomainProfile(
        domain="generic",
        acceptance={
            "deliverables": ["the stated deliverable"],
            "runnable_check": None,
            "completeness_criteria": [
                "achieves the stated goal",
                "no TODOs/stubs where finished work required",
            ],
            "quality_dimensions": ["meets goal", "completeness", "quality for purpose"],
        },
        conventions=[],
        custom={},
        source="example-generated",
    )


def infer_domain(meta_goal_text: str) -> str:
    text = meta_goal_text.lower()

    if any(kw in text for kw in ("cli", "command", "terminal")):
        return "cli_script"
    # software_app before api_service: "app"/"ui"/"frontend" > plain "api" mention
    if any(kw in text for kw in ("app", "ui", "frontend", "fullstack")):
        return "software_app"
    if any(kw in text for kw in ("api", "rest", "endpoint")):
        return "api_service"
    # "data" alone is too broad (data dashboard, data viz, data science)
    if any(kw in text for kw in ("pipeline", "etl")):
        return "data_pipeline"
    if any(kw in text for kw in ("report", "research", "doc")):
        return "research_report"

    return "generic"


def seed_domain_profiles(conn: Connection) -> None:
    cur = conn.cursor()
    for profile in SEED_PROFILES:
        cur.execute(
            """
            INSERT INTO domain_profiles
                (domain, acceptance, conventions, custom, version, source, updated_at)
            VALUES (%s, %s::jsonb, %s::jsonb, %s::jsonb, %s, %s, now())
            ON CONFLICT (domain) DO UPDATE SET
                acceptance     = EXCLUDED.acceptance,
                conventions    = EXCLUDED.conventions,
                custom         = EXCLUDED.custom,
                version        = EXCLUDED.version,
                source         = EXCLUDED.source,
                updated_at     = now()
            """,
            (
                profile["domain"],
                json.dumps(profile["acceptance"]),
                json.dumps(profile["conventions"]),
                json.dumps(profile["custom"]),
                profile.get("version", 1),
                profile.get("source", "example-generated"),
            ),
        )
        logger.info("Domain profile '%s' upserted", profile["domain"])

    conn.commit()
    cur.close()
