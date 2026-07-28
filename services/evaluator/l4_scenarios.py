from __future__ import annotations

import logging
from pathlib import Path

from shared.l4_models import Scenario, hash_spec, scenarios_to_json

logger = logging.getLogger(__name__)

# ── System prompt for scenario generation ─────────────────────────

SCENARIO_GENERATOR_PROMPT = """You are a QA scenario designer.  You have been given the GOAL
and SPEC for a software product, and nothing else — no source code, no plan, no RUN.md.

Your task: write 3–5 user-level scenarios that will test whether the product actually works
as promised.  These scenarios will be given to an agent who has never seen the product
before; the agent will read RUN.md and try to perform each scenario as an end user.

RULES:
- Each scenario is ONE intent-level action, written from the user's perspective.
- Do NOT include steps, commands, file paths, or endpoint URLs.  You don't know them.
  (Prescribing steps about an interface you've never seen creates false positives.)
- Each scenario has: user role (as_a), what they want (wants), and what success looks like
  (success_looks_like).  These must be verifiable by observation, not by code review.
- Do NOT test internal quality (code style, test coverage, documentation).  Test the product
  as a black box.
- Do NOT write scenarios the agent could only answer by reading source code.
- Cover the product's core promise and edge cases.  3–5 scenarios total.

Output a JSON array of objects with keys: id (e.g. "s1", "s2"), source ("seeded"),
as_a, wants, success_looks_like."""


def generate_scenarios(goal: str | None, spec: str | None) -> list[Scenario]:
    """Generate 3–5 intent-level scenarios from goal+spec using one cheap LLM call.

    The scenarios are generated BEFORE the L4 session spawns, so the agent has
    never seen the repo when they are written.  This catches *missing* functionality
    that an agent choosing tests after reading the code would never attempt.
    """
    from backend.planning.meta_planner.llm import call_llm_structured
    from pydantic import BaseModel

    class ScenarioList(BaseModel):
        scenarios: list[Scenario]

    prompt_parts = []
    if goal:
        prompt_parts.append(f"GOAL:\n{goal}\n")
    if spec:
        prompt_parts.append(f"SPEC:\n{spec}\n")
    if not prompt_parts:
        logger.warning("generate_scenarios: neither goal nor spec provided — returning defaults")
        return _default_scenarios()

    combined = "\n".join(prompt_parts)

    try:
        full_prompt = f"{SCENARIO_GENERATOR_PROMPT}\n\n{combined}"
        result = call_llm_structured(
            prompt=full_prompt,
            schema=ScenarioList,
            role="meta_planner",
            temperature=0.3,
        )
        scenarios = result.scenarios
        # Ensure source is "seeded" and id format is stable
        for i, s in enumerate(scenarios):
            s.source = "seeded"
            if not s.id or not s.id.startswith("s"):
                s.id = f"s{i + 1}"
        # Cap at 5
        scenarios = scenarios[:5]
        # Ensure minimum 3 (pad with defaults if needed)
        while len(scenarios) < 3:
            idx = len(scenarios) + 1
            scenarios.append(Scenario(
                id=f"s{idx}",
                source="seeded",
                as_a="end user",
                wants="use the product as described in the goal",
                success_looks_like="the product responds without errors",
            ))
        logger.info("Generated %d L4 scenarios from goal+spec", len(scenarios))
        return scenarios
    except Exception as exc:
        logger.warning("LLM scenario generation failed: %s — using defaults", exc)
        return _default_scenarios()


def _default_scenarios() -> list[Scenario]:
    """Fallback scenarios when LLM generation is unavailable."""
    return [
        Scenario(id="s1", source="seeded", as_a="end user",
                 wants="access the product and verify it starts",
                 success_looks_like="the product responds to a basic request"),
        Scenario(id="s2", source="seeded", as_a="end user",
                 wants="perform the primary action described in the product goal",
                 success_looks_like="the primary action completes successfully"),
        Scenario(id="s3", source="seeded", as_a="end user",
                 wants="verify the product handles an edge case gracefully",
                 success_looks_like="the product returns a reasonable error or fallback"),
    ]


def write_scenarios_to_worktree(worktree: str, scenarios: list[Scenario]) -> Path:
    """Write scenarios to ``l4_scratch/scenarios.json`` in the worktree.

    Creates the ``l4_scratch/`` directory if it doesn't exist.
    """
    scratch = Path(worktree) / "l4_scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    path = scratch / "scenarios.json"
    path.write_text(scenarios_to_json(scenarios))
    logger.info("Wrote %d scenarios to %s", len(scenarios), path)
    return path


def make_spec_hash(goal: str | None, spec: str | None) -> str:
    """Build the spec_hash for a run — stable identifier for scenario reuse."""
    return hash_spec(goal, spec)
