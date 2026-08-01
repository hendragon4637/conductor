from __future__ import annotations

import json
import logging
import os
from typing import Any

from shared.l4_models import Scenario

logger = logging.getLogger(__name__)

# ── System-level scenario generator prompt ─────────────────────────

SYSTEM_SCENARIO_PROMPT = """You are a QA scenario designer for a multi-service system.  You have been given
the SYSTEM GOAL and the SPEC of each component in the system.

Your task: write 3–5 CROSS-COMPONENT integration scenarios that will test whether
the services work together as promised.  Each scenario MUST span at least two
different services — testing a single service independently is OUT OF SCOPE.

RULES:
- Every scenario MUST involve interaction between ≥2 services.
- Scenarios are intent-level only — do NOT include steps, URLs, or commands.
  (You don't know the deployment topology.)
- Each scenario has: user role (as_a), what they want (wants), and what success
  looks like (success_looks_like).  These must be verifiable by black-box observation.
- Write scenarios the agent could execute by interacting with the running composed
  system — NOT by reading source code or internal docs.
- Cover: data flowing between services, shared state, API dependencies, and
  cross-service error handling.
- Do NOT test individual service features.  Only test composed behaviour.
- Output EXACTLY 3 scenarios.  3–5 is the general range; for systems, 3 focused
  cross-component scenarios are better than 5 shallow ones.

Output a JSON array of objects with keys: id (e.g. "s1", "s2", "s3"),
source ("seeded"), as_a, wants, success_looks_like."""


def generate_system_scenarios(
    system_goal: str | None,
    component_specs: list[dict[str, str]],
) -> list[Scenario]:
    """Generate 3 cross-component scenarios from a system goal + component specs.

    Unlike per-project scenario generation (which tests single-service behaviour),
    this prompt explicitly requires each scenario to span ≥2 services.
    """
    from backend.planning.meta_planner.llm import call_llm_structured
    from pydantic import BaseModel

    class ScenarioList(BaseModel):
        scenarios: list[Scenario]

    prompt_parts = []
    if system_goal:
        prompt_parts.append(f"SYSTEM GOAL:\n{system_goal}\n")

    if component_specs:
        prompt_parts.append("COMPONENTS:")
        for cs in component_specs:
            cname = cs.get("name", "?")
            cspec = cs.get("spec", "")
            prompt_parts.append(f"\n--- {cname} ---\n{cspec[:2000]}")

    if len(prompt_parts) < 2:
        logger.warning("generate_system_scenarios: not enough context — using defaults")
        return _default_system_scenarios()

    combined = "\n".join(prompt_parts)

    try:
        full_prompt = f"{SYSTEM_SCENARIO_PROMPT}\n\n{combined}"
        result = call_llm_structured(
            prompt=full_prompt,
            schema=ScenarioList,
            role="meta_planner",
            temperature=0.3,
        )
        scenarios = result.scenarios
        for i, s in enumerate(scenarios):
            s.source = "seeded"
            if not s.id or not s.id.startswith("s"):
                s.id = f"s{i + 1}"
        scenarios = scenarios[:3]
        while len(scenarios) < 3:
            idx = len(scenarios) + 1
            scenarios.append(Scenario(
                id=f"s{idx}",
                source="seeded",
                as_a="end user",
                wants="use the integrated system as described in the goal",
                success_looks_like="the system responds correctly across services",
            ))
        logger.info("Generated %d system-level L4 scenarios", len(scenarios))
        return scenarios
    except Exception as exc:
        logger.warning("System LLM scenario generation failed: %s — using defaults", exc)
        return _default_system_scenarios()


def _default_system_scenarios() -> list[Scenario]:
    return [
        Scenario(
            id="s1", source="seeded",
            as_a="end user",
            wants="verify all services in the composed system start and are healthy",
            success_looks_like="all services respond to health checks",
        ),
        Scenario(
            id="s2", source="seeded",
            as_a="end user",
            wants="exercise a cross-service data flow that spans at least two components",
            success_looks_like="data flows correctly between services without error",
        ),
        Scenario(
            id="s3", source="seeded",
            as_a="end user",
            wants="verify error propagation when one service is unavailable",
            success_looks_like="dependent services degrade gracefully with clear error messages",
        ),
    ]


# ── Cross-component enforcement — L4 brief template ────────────────

SYSTEM_L4_BRIEF_SUFFIX = """
## CRITICAL: Cross-component-only rule

The system under test is a MULTI-SERVICE COMPOSED SYSTEM.  Your scenarios are
pre-generated and stored in ``l4_scratch/scenarios.json``.

You MUST follow these rules:

1. **Cross-component only** — Every scenario MUST exercise ≥2 services working
   together.  Testing a single service in isolation defeats the purpose of system
   level testing.
2. **Use the composed service URLs** — The services are accessible at the ports
   listed in ``compose_urls.json`` (in the workspace root).  Use those URLs, not
   ``localhost:8000``.
3. **Black-box only** — Do NOT inspect source code, database dumps, or internal
   configuration.  You are a user of the running composed system.
4. **Report findings per scenario** — For each scenario in ``scenarios.json``,
   attempt it and record the outcome.  If a scenario fails or is blocked, write
   a finding explaining what broke and where (``where`` = service name).
5. **Adhoc scenarios are still allowed** (max 2) — If you discover a cross-service
   interaction not covered by the pre-generated scenarios, you may add it.
"""


SYSTEM_WORKSYSTEM_BRIEF = """
You are the FIRST USER of an assembled system.  The components are ALREADY BUILT.
This directory contains published artifacts and a generated compose.yml — NO source code.
You cannot fix the components; you can only configure and use them.
1. Read index.json and compose.yml.
2. Configure: copy .env.example → .env and fill values.  You MAY edit compose.yml if the
   system genuinely cannot start otherwise (a missing database service, a volume, a wiring fix).
3. docker compose up -d --wait
4. Attempt every seeded scenario.  These are CROSS-COMPONENT journeys — do not test a single
   component in isolation; that is already covered by project L4.
5. Write l4_scratch/report.json.  Then: docker compose down -v
Rules: if it cannot start at all, that is a finding.  Do NOT commit anything.
"""


def write_compose_urls(workspace: str, services: list[dict[str, Any]]) -> None:
    """Write service URLs to ``compose_urls.json`` in the workspace.

    Each service entry is: {"name": "...", "url": "http://host:port", "slug": "..."}
    """
    from pathlib import Path

    entries = []
    for s in services:
        host = s.get("host", "127.0.0.1")
        port = s.get("assigned_host_port", s.get("port", 8000))
        entries.append({
            "name": s.get("name", "?"),
            "slug": s.get("slug", "?"),
            "url": f"http://{host}:{port}",
            "port": port,
        })

    path = Path(workspace) / "compose_urls.json"
    path.write_text(json.dumps(entries, indent=2) + "\n")
    logger.info("Wrote %d compose URLs to %s", len(entries), path)


def prepare_system_l4_workspace(
    workspace: str,
    compose_services: list[dict[str, Any]],
    scenarios: list[Scenario],
) -> None:
    """Write compose_urls.json and scenarios.json into the L4 workspace."""
    from pathlib import Path
    from services.evaluator.l4_scenarios import write_scenarios_to_worktree

    wt = Path(workspace)
    write_scenarios_to_worktree(str(wt), scenarios)
    write_compose_urls(str(wt), compose_services)
