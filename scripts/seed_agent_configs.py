"""Seed agent_configs on the fresh capability-aligned schema.

Agent configs now declare capabilities (competency model) + tools (realizability
source). Default_checks are dropped — checks come from capability dimensions.

Creative capabilities (music/video/game) are DELIBERATELY unstaffed — proves
the gap->generate path in the capstone.

Run after v6_020 migration:
    uv run python scripts/seed_agent_configs.py
"""
from __future__ import annotations

import json
import os
import sys

import psycopg

DB_URL = os.environ["DATABASE_URL"]

CONFIGS = [
    {
        "agent_config_id": "software-fullstack-executor",
        "role": "executor",
        "domain": "software",
        "new_capabilities": ["frontend", "backend_api", "cli_tool"],
        "tools": ["write_file", "shell", "browser"],
        "system_prompt": (
            "Build runnable full-stack software: frontend + backend + tests. "
            "Write complete files. Create a venv, install deps, produce a RUN.md. "
            "Do NOT start the server in the background."
        ),
        "execution": {
            "backend": "opencode",
            "model_preference": "nvidia/openai/gpt-oss-120b",
            "sub_agents": [],
        },
    },
    {
        "agent_config_id": "backend-api-executor",
        "role": "executor",
        "domain": "software",
        "new_capabilities": ["backend_api", "cli_tool"],
        "tools": ["write_file", "shell"],
        "system_prompt": (
            "Build backend APIs with tests. Use FastAPI. Validate inputs, "
            "return correct HTTP status codes, handle errors. Do NOT start "
            "the server in the background."
        ),
        "execution": {
            "backend": "opencode",
            "model_preference": "nvidia/openai/gpt-oss-120b",
            "sub_agents": [],
        },
    },
    {
        "agent_config_id": "data-executor",
        "role": "executor",
        "domain": "data",
        "new_capabilities": ["data_pipeline", "analytics_assistant"],
        "tools": ["write_file", "shell", "read_data"],
        "system_prompt": (
            "Build data pipelines and analytics. Handle malformed rows gracefully "
            "without crashing. Document input/output schemas. Ensure deterministic "
            "reproducible output."
        ),
        "execution": {
            "backend": "opencode",
            "model_preference": "nvidia/openai/gpt-oss-120b",
            "sub_agents": [],
        },
    },
    {
        "agent_config_id": "research-writer",
        "role": "executor",
        "domain": "research",
        "new_capabilities": ["research_report", "generic"],
        "tools": ["read_web", "write_file"],
        "system_prompt": (
            "Write cited, structured research reports. Never fabricate sources. "
            "Cover all stated questions. Produce a clear conclusion."
        ),
        "execution": {
            "backend": "opencode",
            "model_preference": "deepseek/deepseek-chat",
            "sub_agents": [],
        },
    },
    {
        "agent_config_id": "code-reviewer",
        "role": "reviewer",
        "domain": "software",
        "new_capabilities": ["backend_api", "frontend", "cli_tool"],
        "tools": ["read_file", "shell", "browser"],
        "system_prompt": (
            "Review the product. Run tests, check file structure, exercise "
            "the UI if possible. Report issues clearly with reproduction steps."
        ),
        "execution": {
            "backend": "opencode",
            "model_preference": "nvidia/openai/gpt-oss-120b",
            "sub_agents": [],
        },
    },
    {
        "agent_config_id": "l4-persona",
        "role": "l4_persona",
        "domain": "generic",
        "new_capabilities": [],
        "tools": ["browser", "shell", "http", "read_file"],
        "system_prompt": (
            "Use the finished product like a real user pursuing a goal. "
            "Report UX friction, missing features, and bugs."
        ),
        "execution": {
            "backend": "opencode",
            "model_preference": "nvidia/openai/gpt-oss-120b",
            "sub_agents": [],
        },
    },
]


def main() -> int:
    with psycopg.connect(DB_URL) as conn:
        with conn.cursor() as cur:
            for cfg in CONFIGS:
                cur.execute(
                    """
                    INSERT INTO agent_configs (
                        agent_config_id, role, domain,
                        new_capabilities, tools,
                        system_prompt, execution,
                        permission_policy, source, version
                    ) VALUES (
                        %(agent_config_id)s, %(role)s, %(domain)s,
                        %(new_capabilities)s::jsonb, %(tools)s::jsonb,
                        %(system_prompt)s, %(execution)s::jsonb,
                        '{}'::jsonb, 'example-generated', 1
                    )
                    ON CONFLICT (agent_config_id) DO UPDATE SET
                        role = EXCLUDED.role,
                        domain = EXCLUDED.domain,
                        new_capabilities = EXCLUDED.new_capabilities,
                        tools = EXCLUDED.tools,
                        system_prompt = EXCLUDED.system_prompt,
                        execution = EXCLUDED.execution,
                        version = agent_configs.version + 1,
                        updated_at = now()
                    """,
                    {
                        "agent_config_id": cfg["agent_config_id"],
                        "role": cfg["role"],
                        "domain": cfg["domain"],
                        "new_capabilities": json.dumps(cfg["new_capabilities"]),
                        "tools": json.dumps(cfg["tools"]),
                        "system_prompt": cfg["system_prompt"],
                        "execution": json.dumps(cfg["execution"]),
                    },
                )
        conn.commit()
    print(f"Seeded {len(CONFIGS)} agent configs.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
