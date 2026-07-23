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
        "domain": "gui_app",
        "acceptance": {
            "deliverables": ["GUI application code", "tests", "RUN.md", "build/packaging config"],
            "runnable_check": "`xdg-open` or PyInstaller smoke test confirms window appears with expected content",
            "completeness_criteria": [
                "main window opens without crash",
                "UI components are functional",
                "deps installable in worktree .venv",
                "PyInstaller build succeeds on native platform",
            ],
            "quality_dimensions": [
                "correctness",
                "UI responsiveness",
                "error handling",
                "packaging reliability",
            ],
        },
        "conventions": [
            "a GUI app means a window the user can interact with, not a terminal script",
            "use PySide6 for Qt; avoid Tkinter for new projects",
            "separate UI code from business logic (ui/ vs core/ modules)",
            "include a --smoke flag for headless CI verification",
            "all deps install into a worktree .venv; no host installs",
            "include RUN.md with exact run steps and build commands",
        ],
        "custom": {"default_stack": "PySide6 + PyInstaller"},
    },
    {
        "domain": "embedded_firmware",
        "acceptance": {
            "deliverables": ["firmware source", "tests", "platformio.ini config", "RUN.md"],
            "runnable_check": "`pio test -e native` passes (Unity tests compile and run on host)",
            "completeness_criteria": [
                "compiles for target board (e.g. uno)",
                "native tests pass",
                "hardware-agnostic logic is tested separately from Arduino APIs",
                "no hardcoded magic numbers; use constexpr in config.h",
            ],
            "quality_dimensions": [
                "correctness",
                "code size / memory efficiency",
                "test coverage of logic",
                "documentation of pins and protocol",
            ],
        },
        "conventions": [
            "firmware means embedded code running on a microcontroller, not a desktop program",
            "use PlatformIO for build + test; avoid bare Arduino IDE",
            "hardware-agnostic logic in separate modules; Arduino.h only in hardware wrappers",
            "use millis()-based non-blocking patterns, never delay()",
            "native test env (pio test -e native) must pass without hardware",
            "include RUN.md with flash instructions and port hints",
        ],
        "custom": {"default_stack": "PlatformIO + Arduino framework + Unity tests"},
    },
    {
        "domain": "visual_design",
        "acceptance": {
            "deliverables": ["DESIGN.md", "artifacts in exports/", "brief/BRIEF.md"],
            "runnable_check": "exported artifacts are valid files (html, png, pdf) and DESIGN.md tokens are respected",
            "completeness_criteria": [
                "DESIGN.md defines palette, type scale, spacing, voice",
                "brief/BRIEF.md is filled out with audience, purpose, constraints",
                "final artifact matches brief point-by-point",
                "WCAG AA contrast minimum met for all text",
            ],
            "quality_dimensions": [
                "visual hierarchy",
                "brand consistency",
                "accessibility",
                "brief compliance",
            ],
        },
        "conventions": [
            "design means visual layout, not code or firmware",
            "start by defining DESIGN.md tokens (palette, type, spacing, voice)",
            "work iteratively in work/; freeze final exports to exports/",
            "every artifact must have a corresponding brief entry",
            "respect WCAG AA contrast ratios (4.5:1 text, 3:1 large text)",
        ],
        "custom": {"format": "open-design workflow"},
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


def list_domain_names() -> list[str]:
    """Return all domain names from the ``domain_profiles`` table.

    Falls back to the hardcoded ``SEED_PROFILES`` list when the DB is
    unavailable (e.g. before migrations have run).
    """
    try:
        dsn = _get_db_url()
        with psycopg.connect(dsn, row_factory=dict_row) as conn, conn.cursor() as cur:
            cur.execute("SELECT domain FROM domain_profiles ORDER BY domain")
            return [row["domain"] for row in cur.fetchall()]
    except Exception:
        logger.exception("Failed to query domain_profiles — falling back to seed list")
        return [p["domain"] for p in SEED_PROFILES]


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

    # firmware/embedded before cli (Arduino CLI could match both)
    if any(kw in text for kw in ("firmware", "arduino", "embedded", "microcontroller", "platformio")):
        return "embedded_firmware"
    # GUI before software_app (a GUI app is still an app, but more specific)
    if any(kw in text for kw in ("gui", "desktop app", "pyside6", "pyqt", "qt ", "tray icon", "system tray")):
        return "gui_app"
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
    # visual design — layout keywords after research, before generic
    if any(kw in text for kw in ("layout", "poster", "design system", "brand", "visual design", "figma")):
        return "visual_design"

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
