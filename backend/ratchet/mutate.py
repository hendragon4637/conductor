from __future__ import annotations

import json
import os
from typing import Any

from backend.llm.gateway import call as gateway_call


def _read_current_artifact(agent_config: str) -> dict[str, str]:
    """Read the current skill/prompt/agents_md from the filesystem.

    Returns a dict like ``{"skill": "content...", "agents_md": "..."}``.
    """
    result: dict[str, str] = {}
    # Skill
    skill_path = os.path.join(
        "/opt/aipc/conductor/skills",
        agent_config.replace("backend-", "").replace("frontend-", ""),
        "SKILL.md",
    )
    if os.path.isfile(skill_path):
        with open(skill_path) as f:
            result["skill"] = f.read()

    # AGENTS.md
    agents_path = os.path.join(
        "/opt/aipc/conductor/skills",
        agent_config.replace("backend-", "").replace("frontend-", ""),
        "AGENTS.md",
    )
    if os.path.isfile(agents_path):
        with open(agents_path) as f:
            result["agents_md"] = f.read()

    return result


def _llm_call(prompt: str) -> str:
    result = gateway_call("plan_brain", [
        {
            "role": "system",
            "content": (
                "You are a skill-mutation engineer. "
                "Given an agent's current skill config and failing traces, "
                "propose a single targeted mutation that would fix the most "
                "common failure pattern.\n\n"
                "Rules:\n"
                "- Target ONE probabilistic artifact: skill, agents_md, or prompt.\n"
                "- NEVER change permission_template, engine, or model.\n"
                "- Output ONLY valid JSON with keys:\n"
                '  target: "skill" | "agents_md" | "prompt"\n'
                "  rationale: str\n"
                "  candidate: str (the full new content of that artifact)\n"
            ),
        },
        {"role": "user", "content": prompt},
    ], temperature=0.3, max_tokens=2048, timeout=120)

    raw = result["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw


_FROZEN_ARTIFACTS = frozenset({
    "permission_template",
    "engine",
    "model",
    "golden",
    "golden_set",
})

_FROZEN_KEYWORDS = frozenset({
    "permission", "engine", "model",
    "golden", "opencode_config", "safety_cap",
    "budget_cap", "preset", "check_cmd",
})


def _reject_frozen_target(target: str, candidate: str | None = None) -> None:
    """Raise ValueError if the mutation targets a frozen (non-probabilistic) artifact.

    MAY touch: skill, agents_md, prompt, rubric wording, judge-prompt.
    MUST NOT touch: permissions, engine, model, golden set,
                    deterministic safety bounds, budget caps.
    """
    if target in _FROZEN_ARTIFACTS:
        raise ValueError(
            f"Mutation targets frozen artifact '{target}'. "
            f"Ratchet may only optimize probabilistic artifacts: "
            f"skill, agents_md, prompt, rubric, judge-prompt."
        )
    # Check candidate content for frozen keywords (defensive — LLM may ignore the target field)
    if candidate:
        c_lower = candidate.lower()
        for kw in _FROZEN_KEYWORDS:
            if kw in c_lower:
                # Only flag on structural config-value separators (`:` / `=`),
                # not natural-language mentions like "handle permission errors"
                if any(
                    marker in c_lower
                    for marker in (f"{kw}:", f"{kw}=")
                ):
                    raise ValueError(
                        f"Mutation content references frozen keyword '{kw}' "
                        f"in structural position. Rejected."
                    )


def propose_mutation(
    agent_config: str,
    failing_traces: list[dict],
) -> dict[str, Any]:
    """LLM proposes a mutation to one probabilistic artifact.

    Returns:
        ``{"target": str, "rationale": str, "candidate": str}``

    Raises:
        ValueError: If the LLM-produced mutation targets a frozen
                    (non-probabilistic) artifact.
    """
    current = _read_current_artifact(agent_config)

    failures_text = json.dumps(
        [
            {
                "score": t["score"],
                "comment": t.get("comment", ""),
                "input": t.get("input", {}),
                "output": t.get("output", {}),
            }
            for t in failing_traces[:5]
        ],
        indent=2,
    )

    prompt = (
        f"Agent config: {agent_config}\n\n"
        f"Current skill content:\n{current.get('skill', '(none)')}\n\n"
        f"Current AGENTS.md:\n{current.get('agents_md', '(none)')}\n\n"
        f"Recent failing traces (first 5):\n{failures_text}\n\n"
        f"Propose a single mutation that fixes the most common failure pattern. "
        f"Output ONLY valid JSON."
    )

    raw = _llm_call(prompt)
    mutation = json.loads(raw)

    target = mutation.get("target", "")
    candidate = mutation.get("candidate", "")
    _reject_frozen_target(target, candidate)

    return mutation
