#!/usr/bin/env python3
"""Seed stress-test domains: Software Delivery + Content Studio.

Creates capabilities (family as JSONB array) and agent_configs for
the heterogeneous stress test.  Idempotent — re-run safe.

Usage:
    uv run python scripts/seed_stress_domains.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_project_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_project_root))

import json
import os

import psycopg

# ── Software Delivery capabilities (strong oracle) ───────────────────

SOFTWARE_DELIVERY = [
    {
        "name": "requirements_spec",
        "family": ["software"],
        "description": "structured requirements/spec document",
        "quality_dimensions": [
            {"id": "complete", "dimension": "covers goal + constraints", "kind": "subjective"},
            {"id": "file", "dimension": "spec file exists", "kind": "objective"},
        ],
        "required_tools": ["write_file"],
    },
    {
        "name": "architecture_design",
        "family": ["software"],
        "description": "architecture/design document",
        "quality_dimensions": [
            {"id": "file", "dimension": "design doc exists", "kind": "objective"},
            {"id": "sound", "dimension": "components + data flow sound", "kind": "subjective"},
        ],
        "required_tools": ["write_file"],
    },
    {
        "name": "backend_api",
        "family": ["software"],
        "description": "HTTP API + business logic",
        "quality_dimensions": [
            {"id": "tests", "dimension": "tests pass", "kind": "objective"},
            {"id": "codes", "dimension": "correct status codes", "kind": "objective"},
            {"id": "validation", "dimension": "validates input", "kind": "subjective"},
        ],
        "required_tools": ["write_file", "shell"],
    },
    {
        "name": "frontend",
        "family": ["software", "design"],
        "description": "user-facing UI",
        "quality_dimensions": [
            {"id": "builds", "dimension": "builds ok", "kind": "objective"},
            {"id": "responsive", "dimension": "responsive layout", "kind": "subjective"},
            {"id": "integration", "dimension": "calls backend", "kind": "subjective"},
        ],
        "required_tools": ["write_file", "browser"],
    },
    {
        "name": "tests_suite",
        "family": ["software"],
        "description": "automated test suite",
        "quality_dimensions": [
            {"id": "run", "dimension": "suite runs", "kind": "objective"},
            {"id": "coverage", "dimension": "covers key paths", "kind": "subjective"},
        ],
        "required_tools": ["write_file", "shell"],
    },
    {
        "name": "deployment_iac",
        "family": ["software"],
        "description": "deploy / IaC configuration",
        "quality_dimensions": [
            {"id": "valid", "dimension": "config parses", "kind": "objective"},
            {"id": "reproducible", "dimension": "reproducible environment", "kind": "subjective"},
        ],
        "required_tools": ["write_file", "shell"],
    },
    {
        "name": "tech_docs",
        "family": ["software", "research"],
        "description": "technical documentation / RUN.md",
        "quality_dimensions": [
            {"id": "file", "dimension": "doc exists", "kind": "objective"},
            {"id": "clear", "dimension": "clear + accurate", "kind": "subjective"},
        ],
        "required_tools": ["write_file"],
    },
]

# ── Content Studio capabilities (weak oracle) ───────────────────────

CONTENT_STUDIO = [
    {
        "name": "copywriting",
        "family": ["creative", "content"],
        "description": "marketing / creative copy",
        "quality_dimensions": [
            {"id": "file", "dimension": "text file exists", "kind": "objective"},
            {"id": "tone", "dimension": "tone matches brief", "kind": "subjective"},
            {"id": "persuasive", "dimension": "persuasive / clear", "kind": "subjective"},
        ],
        "required_tools": ["write_file"],
    },
    {
        "name": "image_gen",
        "family": ["creative", "design"],
        "description": "generate an image from a brief",
        "quality_dimensions": [
            {"id": "valid", "dimension": "valid image file", "kind": "objective"},
            {"id": "prompt_fit", "dimension": "matches brief", "kind": "subjective"},
            {"id": "aesthetic", "dimension": "visually coherent", "kind": "subjective"},
        ],
        "required_tools": ["write_file"],
    },
    {
        "name": "music_generation",
        "family": ["creative"],
        "description": "generate music from a prompt",
        "quality_dimensions": [
            {"id": "valid_audio", "dimension": "playable audio, correct length", "kind": "objective"},
            {"id": "melody", "dimension": "melodic coherence", "kind": "subjective"},
            {"id": "prompt_fit", "dimension": "matches style/mood", "kind": "subjective"},
        ],
        "required_tools": ["audio_gen", "write_file"],
    },
    {
        "name": "design_layout",
        "family": ["creative", "design"],
        "description": "visual layout / poster",
        "quality_dimensions": [
            {"id": "file", "dimension": "layout file exists", "kind": "objective"},
            {"id": "hierarchy", "dimension": "clear visual hierarchy", "kind": "subjective"},
            {"id": "brief_fit", "dimension": "matches brief", "kind": "subjective"},
        ],
        "required_tools": ["write_file"],
    },
    {
        "name": "content_review",
        "family": ["creative", "content"],
        "description": "review / QA of content",
        "quality_dimensions": [
            {"id": "file", "dimension": "review notes exist", "kind": "objective"},
            {"id": "actionable", "dimension": "actionable feedback", "kind": "subjective"},
        ],
        "required_tools": ["read_file", "write_file"],
    },
]

# ── Stress-test agent configs (both backends) ───────────────────────

STRESS_CONFIGS = [
    {
        "agent_config_id": "sw-fullstack-opencode",
        "role": "executor",
        "domain": "software",
        "new_capabilities": ["requirements_spec", "architecture_design", "backend_api", "frontend", "tests_suite", "deployment_iac", "tech_docs"],
        "tools": ["write_file", "shell", "browser"],
        "execution": {"backend": "opencode", "model_preference": "gptoss-exec"},
        "source": "example-generated",
    },
    {
        "agent_config_id": "sw-backend-hermes",
        "role": "executor",
        "domain": "software",
        "new_capabilities": ["backend_api", "tests_suite", "tech_docs"],
        "tools": ["write_file", "shell"],
        "execution": {"backend": "hermes", "model_preference": "gptoss-exec"},
        "source": "example-generated",
    },
    {
        "agent_config_id": "content-writer-opencode",
        "role": "executor",
        "domain": "content",
        "new_capabilities": ["copywriting", "content_review", "design_layout"],
        "tools": ["write_file", "read_file"],
        "execution": {"backend": "opencode", "model_preference": "gptoss-exec"},
        "source": "example-generated",
    },
]


def _upsert_capability(cur, cap: dict) -> None:
    cur.execute(
        """
        INSERT INTO capabilities (name, family, description, quality_dimensions, required_tools, source, golden_ref_count)
        VALUES (%s, %s::jsonb, %s, %s::jsonb, %s::jsonb, 'example-generated', 0)
        ON CONFLICT (name) DO UPDATE SET
            family = EXCLUDED.family,
            description = EXCLUDED.description,
            quality_dimensions = EXCLUDED.quality_dimensions,
            required_tools = EXCLUDED.required_tools
        """,
        (
            cap["name"],
            json.dumps(cap["family"]),
            cap["description"],
            json.dumps(cap["quality_dimensions"]),
            json.dumps(cap["required_tools"]),
        ),
    )


def _upsert_agent_config(cur, cfg: dict) -> None:
    cur.execute(
        """
        INSERT INTO agent_configs (agent_config_id, role, domain, new_capabilities, tools, execution, source)
        VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb, %s)
        ON CONFLICT (agent_config_id) DO UPDATE SET
            role = EXCLUDED.role,
            domain = EXCLUDED.domain,
            new_capabilities = EXCLUDED.new_capabilities,
            tools = EXCLUDED.tools,
            execution = EXCLUDED.execution
        """,
        (
            cfg["agent_config_id"],
            cfg["role"],
            cfg["domain"],
            json.dumps(cfg["new_capabilities"]),
            json.dumps(cfg["tools"]),
            json.dumps(cfg["execution"]),
            cfg["source"],
        ),
    )


def main() -> int:
    dsn = os.environ.get("DATABASE_URL", "")
    if not dsn:
        print("FATAL: DATABASE_URL not set")
        return 1

    with psycopg.connect(dsn) as conn, conn.cursor() as cur:
        # Seed Software Delivery capabilities
        for cap in SOFTWARE_DELIVERY:
            _upsert_capability(cur, cap)
        print(f"Seeded {len(SOFTWARE_DELIVERY)} Software Delivery capabilities")

        # Seed Content Studio capabilities
        for cap in CONTENT_STUDIO:
            _upsert_capability(cur, cap)
        print(f"Seeded {len(CONTENT_STUDIO)} Content Studio capabilities")

        # Seed agent configs
        for cfg in STRESS_CONFIGS:
            _upsert_agent_config(cur, cfg)
        print(f"Seeded {len(STRESS_CONFIGS)} stress-test agent configs")

        conn.commit()

    print("Stress-test domains seeded successfully")
    return 0


if __name__ == "__main__":
    sys.exit(main())
