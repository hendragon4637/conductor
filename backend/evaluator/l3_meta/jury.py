from __future__ import annotations

import json
import os
from typing import Any

from backend.llm.gateway import call as gateway_call


JURY_MODELS_RAW = os.environ.get(
    "JURY_MODELS",
    '["opencode/deepseek-v4-flash-free", "opencode/qwen-2.5-coder-32b"]',
)
JURY_TIMEOUT = 60.0

JUDGE_SYSTEM_PROMPT = (
    "You are a strict, impartial quality judge. "
    "You will receive a rubric item (a yes/no quality question) "
    "and an artifact. Answer with a JSON object: "
    '{"criteria_met": true/false, "explanation": "one short sentence"}.'
)
JUDGE_USER_PROMPT = "Rubric item: {rubric_item}\n\nArtifact:\n{artifact}"


def _call_model(model: str, prompt: str) -> dict[str, Any]:
    """Call a single model through the LiteLLM gateway and return parsed response."""
    try:
        result = gateway_call("l3_jury", [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], temperature=0.0, max_tokens=256, timeout=JURY_TIMEOUT)
        raw = result["choices"][0]["message"]["content"].strip()
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[1]
            raw = raw.rsplit("```", 1)[0]
        return json.loads(raw)
    except Exception as exc:
        return {"criteria_met": None, "explanation": f"Model unavailable: {exc}"}


def jury_score(
    artifact: str,
    rubric_item: str,
    models: list[str] | None = None,
) -> dict[str, Any]:
    """Score an artifact against a rubric item using a diverse jury panel.

    Calls each model in the panel independently and aggregates results
    via majority vote. If all models are unavailable, returns a fallback.

    Args:
        artifact: The artifact text to evaluate.
        rubric_item: The rubric question.
        models: Override list of model identifiers. Defaults to
                ``JURY_MODELS`` env var (JSON array of model names).

    Returns:
        Dict with keys:
        - ``criteria_met``: bool (majority vote) or None if all unavailable
        - ``votes``: list of per-model results
        - ``models_used``: list of model identifiers used
        - ``note``: string with any caveat (e.g. single-family fallback)
    """
    if models is None:
        try:
            models = json.loads(JURY_MODELS_RAW)
        except (json.JSONDecodeError, TypeError):
            models = ["opencode/deepseek-v4-flash-free"]

    prompt = JUDGE_USER_PROMPT.format(rubric_item=rubric_item, artifact=artifact)

    votes: list[dict[str, Any]] = []
    used_models: list[str] = []

    for model in models:
        result = _call_model(model, prompt)
        votes.append({"model": model, "criteria_met": result.get("criteria_met"),
                       "explanation": result.get("explanation", "")})
        used_models.append(model)

    valid_votes = [v for v in votes if v["criteria_met"] is not None]
    if not valid_votes:
        return {"criteria_met": None, "votes": votes,
                "models_used": used_models,
                "note": "All jury models unavailable"}

    majority_met = sum(1 for v in valid_votes if v["criteria_met"]) > len(valid_votes) / 2

    note = ""
    if len(models) < 2:
        note = "Single-family fallback — bias possible"
    elif len(valid_votes) < len(votes):
        note = f"{len(valid_votes)}/{len(votes)} models responded"

    return {
        "criteria_met": majority_met,
        "votes": votes,
        "models_used": used_models,
        "note": note,
    }
