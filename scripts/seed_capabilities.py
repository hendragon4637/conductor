"""Seed the capabilities registry with heterogeneous starter entries.

Each capability carries quality_dimensions tagged objective→L1 or subjective→L2,
required_tools for realizability, and golden_ref_count=0 (starter-generated).

Source is "example-generated" — these are NOT expert-authored. They make
heterogeneous E2E runnable now. The objective/subjective split is the
important part; refine wording + add golden labels later.

Run after v6_010 migration:
    uv run python scripts/seed_capabilities.py
"""
from __future__ import annotations

import json
import os
import sys

import psycopg

DB_URL = os.environ["DATABASE_URL"]

CAPS = [
    # ---------- SOFTWARE (strong objective oracle) ----------
    {"name": "frontend", "family": "software",
     "description": "user-facing UI a person interacts with",
     "quality_dimensions": [
         {"id": "renders", "dimension": "UI renders without errors", "kind": "objective"},
         {"id": "builds", "dimension": "build/compile succeeds", "kind": "objective"},
         {"id": "input_handling", "dimension": "validates input + shows feedback", "kind": "subjective"},
         {"id": "responsive", "dimension": "works across viewport sizes", "kind": "subjective"},
         {"id": "integration", "dimension": "correctly calls the backend", "kind": "subjective"},
     ],
     "required_tools": ["write_file", "browser"]},

    {"name": "backend_api", "family": "software",
     "description": "HTTP API with endpoints + data logic",
     "quality_dimensions": [
         {"id": "tests_pass", "dimension": "unit/endpoint tests pass", "kind": "objective"},
         {"id": "status_codes", "dimension": "correct HTTP status codes", "kind": "objective"},
         {"id": "validation", "dimension": "validates inputs, rejects bad data", "kind": "subjective"},
         {"id": "data_integrity", "dimension": "safe types (e.g. integer cents), no corruption",
          "kind": "subjective"},
     ],
     "required_tools": ["write_file", "shell"]},

    {"name": "cli_tool", "family": "software",
     "description": "command-line tool runnable from shell",
     "quality_dimensions": [
         {"id": "help_works", "dimension": "`--help` exits 0", "kind": "objective"},
         {"id": "runs", "dimension": "main command runs on valid input", "kind": "objective"},
         {"id": "error_handling", "dimension": "handles bad input gracefully", "kind": "subjective"},
     ],
     "required_tools": ["write_file", "shell"]},

    # ---------- DATA ----------
    {"name": "data_pipeline", "family": "data",
     "description": "ingest->transform->output data pipeline",
     "quality_dimensions": [
         {"id": "runs_sample", "dimension": "runs on sample input, produces expected-shape output",
          "kind": "objective"},
         {"id": "handles_malformed", "dimension": "skips/handles malformed rows without crashing",
          "kind": "objective"},
         {"id": "correctness", "dimension": "transformation is correct vs spec", "kind": "subjective"},
         {"id": "reproducible", "dimension": "deterministic/reproducible output", "kind": "objective"},
     ],
     "required_tools": ["write_file", "shell"]},

    {"name": "analytics_assistant", "family": "data",
     "description": "derives insights from data streams",
     "quality_dimensions": [
         {"id": "parses", "dimension": "parses input data correctly", "kind": "objective"},
         {"id": "accuracy", "dimension": "computed metrics accurate vs known fixture", "kind": "objective"},
         {"id": "actionable", "dimension": "insights are actionable/relevant", "kind": "subjective"},
         {"id": "clarity", "dimension": "insights presented clearly", "kind": "subjective"},
     ],
     "required_tools": ["read_data", "write_file"]},

    # ---------- MEDIA / CREATIVE (weak objective oracle -> mostly subjective + golden) ----------
    {"name": "music_generation", "family": "creative",
     "description": "generate a musical piece from a prompt",
     "quality_dimensions": [
         {"id": "valid_audio", "dimension": "produces playable audio of correct length", "kind": "objective"},
         {"id": "melody", "dimension": "melodic richness/coherence", "kind": "subjective"},
         {"id": "rhythm", "dimension": "rhythmic structure consistency", "kind": "subjective"},
         {"id": "prompt_fit", "dimension": "matches requested style/mood", "kind": "subjective"},
     ],
     "required_tools": ["audio_gen", "write_file"]},

    {"name": "video_content", "family": "creative",
     "description": "generate/edit a video clip",
     "quality_dimensions": [
         {"id": "valid_video", "dimension": "valid encoded video of correct duration", "kind": "objective"},
         {"id": "visual_quality", "dimension": "scene composition / visual coherence", "kind": "subjective"},
         {"id": "narrative", "dimension": "narrative/temporal consistency", "kind": "subjective"},
         {"id": "prompt_fit", "dimension": "matches the brief", "kind": "subjective"},
     ],
     "required_tools": ["video_gen", "write_file"]},

    {"name": "game_build", "family": "creative",
     "description": "build a small playable game",
     "quality_dimensions": [
         {"id": "runs", "dimension": "game launches + core loop runs", "kind": "objective"},
         {"id": "no_crash", "dimension": "no crash during a play session", "kind": "objective"},
         {"id": "fun", "dimension": "core loop is engaging/fun", "kind": "subjective"},
         {"id": "balance", "dimension": "difficulty/balance reasonable", "kind": "subjective"},
     ],
     "required_tools": ["write_file", "shell", "browser"]},

    # ---------- BUSINESS ----------
    {"name": "agentic_business_flow", "family": "business",
     "description": "automate a business workflow across tools",
     "quality_dimensions": [
         {"id": "executes", "dimension": "workflow runs end-to-end without error", "kind": "objective"},
         {"id": "correct_actions", "dimension": "performs the intended actions on the right targets",
          "kind": "objective"},
         {"id": "appropriateness", "dimension": "decisions are business-appropriate", "kind": "subjective"},
         {"id": "safety", "dimension": "no unintended side-effects / respects gates", "kind": "subjective"},
     ],
     "required_tools": ["http", "read_data", "write_file"]},

    # ---------- RESEARCH / DOCS (non-runnable) ----------
    {"name": "research_report", "family": "research",
     "description": "researched written report",
     "quality_dimensions": [
         {"id": "file_present", "dimension": "report file exists", "kind": "objective"},
         {"id": "covers", "dimension": "covers all stated questions", "kind": "subjective"},
         {"id": "sourced", "dimension": "claims cited, no fabricated sources", "kind": "subjective"},
         {"id": "clarity", "dimension": "clear structure + conclusion", "kind": "subjective"},
     ],
     "required_tools": ["read_web", "write_file"]},

    # ---------- GENERIC FALLBACK (always available) ----------
    {"name": "generic", "family": "research",
     "description": "fallback for unclassified deliverables",
     "quality_dimensions": [
         {"id": "deliverable_present", "dimension": "stated deliverable exists", "kind": "objective"},
         {"id": "meets_goal", "dimension": "achieves the stated goal", "kind": "subjective"},
     ],
     "required_tools": ["write_file"]},
]


def main() -> int:
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            for cap in CAPS:
                cur.execute(
                    """
                    INSERT INTO capabilities (name, family, description, quality_dimensions,
                                               required_tools, source, golden_ref_count, version)
                    VALUES (%(name)s, %(family)s, %(description)s, %(quality_dimensions)s::jsonb,
                            %(required_tools)s::jsonb, 'example-generated', 0, 1)
                    ON CONFLICT (name) DO UPDATE SET
                      family = EXCLUDED.family,
                      description = EXCLUDED.description,
                      quality_dimensions = EXCLUDED.quality_dimensions,
                      required_tools = EXCLUDED.required_tools,
                      version = capabilities.version + 1,
                      updated_at = now()
                    """,
                    {
                        "name": cap["name"],
                        "family": cap["family"],
                        "description": cap["description"],
                        "quality_dimensions": json.dumps(cap["quality_dimensions"]),
                        "required_tools": json.dumps(cap["required_tools"]),
                    },
                )
        conn.commit()
    print(f"Seeded {len(CAPS)} capabilities.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
