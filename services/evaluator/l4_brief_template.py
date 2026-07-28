"""L4 agent brief template.

Rendered at spawn time and sent to the L4 session as its goal brief.
Stored here as a Jinja2 template; can be moved into a template file
or agent_config system_prompt later.
"""

from shared.l4_models import scenarios_to_json, Scenario

L4_BRIEF_TEMPLATE = """You are the FIRST USER of a finished product.  You did not build it.

Your scenarios are already written in l4_scratch/scenarios.json.  Attempt EVERY seeded one.

1. Read RUN.md.  Work out HOW to perform each scenario yourself, as an end user would.
   Do NOT read the source to figure out how to RUN it — if RUN.md is unclear or wrong,
   that IS a finding.  (You MAY browse the repo to find a file path for a finding you
   already have, so `where` points somewhere real.)
2. Perform each scenario.  Record what you actually did in `attempted`.
3. You may add at most 2 adhoc scenarios for friction you hit outside the script.

Write l4_scratch/report.json:
  verdict:          "pass" (nothing needs changing) | "partial" (minor issues) | "fail" (unusable)
  scenario_results: one entry per scenario — scenario_id, attempted[], outcome, notes
  findings:         ONLY things that should CHANGE.  Each needs what, where (paths that resolve),
                    why (what you observed), severity (low|medium|high), scenario_id
  observations:     everything that WORKED, and anything positive.  Put praise HERE, never in findings.

Rules:
- A finding must name a concrete file path AND a scenario_id.  If you cannot, it is an observation.
- If the product works, verdict is "pass" and findings is [].  That is a complete, correct report.
- Do not modify the product.  You are using it, not fixing it.
- Do not invent problems to seem thorough."""


def render_l4_brief(
    run_id: str,
    worktree: str,
    scenarios: list[Scenario],
    parent_run_id: str,
    preamble: str | None = None,
) -> str:
    """Render the L4 brief for a session, including the pre-generated scenarios."""
    scenarios_json = scenarios_to_json(scenarios)

    # Strip the template body and append the run-specific context as plain text
    parts = []
    if preamble:
        parts.append(f"[FIX PREAMBLE]\n{preamble}\n\n---\n")
    parts.append(
        f"{L4_BRIEF_TEMPLATE}\n\n"
        f"---\n"
        f"Run ID: {run_id}\n"
        f"Parent run ID: {parent_run_id}\n"
        f"Worktree: {worktree}\n\n"
        f"Pre-generated scenarios (l4_scratch/scenarios.json):\n"
        f"{scenarios_json}\n"
    )
    return "\n".join(parts)
