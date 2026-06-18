from __future__ import annotations

import json
import os
from typing import Any, Callable

from backend.planning.brain import BRAIN_ENDPOINT, BRAIN_MODEL

VLM_ENABLED = os.environ.get("VLM_ENABLED", "").lower() in ("1", "true", "yes")


def _default_llm(prompt: str) -> str:
    import urllib.request
    body = {
        "model": BRAIN_MODEL,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are a strict technical reviewer. "
                    "Evaluate whether the task output meets the success criterion. "
                    "Output ONLY valid JSON with keys: score (float 0-1), pass (bool), reason (str)."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 512,
    }
    req = urllib.request.Request(
        BRAIN_ENDPOINT,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            result = json.loads(resp.read())
    except Exception as exc:
        return json.dumps({
            "score": 0.5,
            "pass": None,
            "reason": f"LLM judge unavailable: {exc}",
        })
    raw = result["choices"][0]["message"]["content"].strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]
    return raw


def judge_text(
    success_criterion: str,
    evidence: dict[str, Any],
    llm_call: Callable[[str], str] | None = None,
) -> dict[str, Any]:
    """Evaluate textual evidence against a success criterion.

    Args:
        success_criterion: The measurable success condition from the plan node.
        evidence: Dict with keys like ``files``, ``last_output``, ``test_result``.
        llm_call: Overrideable LLM call function (for testing).

    Returns:
        ``{"score": float 0-1, "pass": bool, "reason": str}``
    """
    if llm_call is None:
        llm_call = _default_llm

    prompt = (
        f"Success criterion: {success_criterion}\n\n"
        f"Evidence:\n"
        f"  Files written: {json.dumps(evidence.get('files', []))}\n"
        f"  Last assistant output: {evidence.get('last_output', '')[:2000]}\n"
        f"  Test result: {evidence.get('test_result', 'not run')}\n"
        f"\n"
        f"Respond as JSON: {{\"score\": 0.0-1.0, \"pass\": true/false, "
        f"\"reason\": \"brief explanation\"}}"
    )

    raw = llm_call(prompt)
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"score": 0.5, "pass": None, "reason": "LLM judge returned invalid JSON"}


def judge_visual(
    success_criterion: str,
    rendered_image_path: str,
    ref_image_path: str | None = None,
) -> dict[str, Any]:
    """Evaluate visual output against a success criterion.

    Requires VLM_ENABLED=1 and a suitable VLM endpoint.
    Falls back to score=None if VLM is not configured.
    """
    if not VLM_ENABLED:
        return {"score": None, "pass": None, "reason": "VLM not configured"}
    # Stub — VLM integration is deferred until a VLM endpoint is available.
    # Qwen2.5-VL on Arc is the target. For now return score=None.
    return {"score": None, "pass": None, "reason": "VLM endpoint not available"}
