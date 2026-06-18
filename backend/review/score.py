from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from backend.observability.langfuse_client import get_langfuse
from backend.review.judge import judge_text, judge_visual


def _mentions_image(criterion: str) -> bool:
    keywords = ("image", "screenshot", "visual", "render", "ui", "screenshot")
    return any(k in criterion.lower() for k in keywords)


def _deterministic_checks(node: dict, evidence: dict) -> dict[str, bool | None]:
    """Cheap pre-checks that don't require an LLM call."""
    result: dict[str, bool | None] = {}

    # Check if pytest tests pass (if tests were run)
    test_result = evidence.get("test_result", "")
    if test_result:
        result["tests_passed"] = "passed" in test_result.lower()
    else:
        result["tests_passed"] = None

    # Check if expected files exist
    expected_files = evidence.get("expected_files", [])
    actual_files = evidence.get("files", [])
    if expected_files:
        actual_set = {f.get("path", f) if isinstance(f, dict) else f for f in actual_files}
        missing = [f for f in expected_files if f not in actual_set]
        result["files_present"] = len(missing) == 0
    else:
        result["files_present"] = None

    return result


def _combine(
    det: dict[str, bool | None],
    txt: dict[str, Any],
    vis: dict[str, Any] | None,
) -> dict[str, Any]:
    """Combine deterministic, text, and visual scores.

    Deterministic gates dominate: if tests fail, score is capped.
    """
    score = txt.get("score", 0.0)
    passed = txt.get("pass", False)
    reasons = [txt.get("reason", "")]

    # Deterministic gates
    if det.get("tests_passed") is False:
        score = min(score, 0.3)
        passed = False
        reasons.append("tests failed (deterministic gate)")

    if det.get("files_present") is False:
        score = min(score, 0.4)
        reasons.append("expected files missing (deterministic gate)")

    # Visual score
    if vis and vis.get("score") is not None:
        score = (score + vis["score"]) / 2
        if vis.get("reason"):
            reasons.append(vis["reason"])

    return {
        "score": round(max(0.0, min(1.0, score)), 2),
        "pass": passed,
        "reason": " | ".join(r for r in reasons if r),
    }


def gather_evidence(
    worktree_path: str | Path,
    conversation_messages: list[dict] | None = None,
    expected_files: list[str] | None = None,
) -> dict[str, Any]:
    """Collect evidence from the worktree and AionUi messages."""
    wt = Path(worktree_path)
    evidence: dict[str, Any] = {
        "files": [],
        "last_output": "",
        "test_result": "not run",
        "expected_files": expected_files or [],
    }

    # Files in worktree
    if wt.exists():
        for p in wt.rglob("*"):
            if p.is_file() and ".git" not in p.parts:
                evidence["files"].append({
                    "path": str(p.relative_to(wt)),
                    "size": p.stat().st_size,
                })

    # Last assistant output from conversation
    if conversation_messages:
        for m in reversed(conversation_messages):
            if m.get("position") == "left":
                content = m.get("content", "")
                if isinstance(content, dict):
                    evidence["last_output"] = content.get("text", str(content))
                elif isinstance(content, str):
                    evidence["last_output"] = content
                else:
                    evidence["last_output"] = str(content)
                break

    # Run tests if worktree has a test directory
    if wt.exists() and (wt / "tests").is_dir() or (wt / "test").is_dir():
        try:
            result = subprocess.run(
                ["python3", "-m", "pytest", "-q", "--tb=short"],
                cwd=str(wt),
                capture_output=True, text=True, timeout=60,
            )
            evidence["test_result"] = (
                f"passed (exit {result.returncode})" if result.returncode == 0
                else f"failed (exit {result.returncode}): {result.stderr[:500]}"
            )
        except Exception as e:
            evidence["test_result"] = f"error: {e}"

    return evidence


def score_node(
    node: dict[str, Any],
    trace_id: str,
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Run deterministic checks + judges, write scores to Langfuse.

    Args:
        node: The plan node dict (needs ``success`` key).
        trace_id: Langfuse trace id for the node's execution.
        evidence: Output from ``gather_evidence()``.

    Returns:
        ``{"score": float, "pass": bool, "reason": str}``
    """
    det = _deterministic_checks(node, evidence)
    txt = judge_text(node.get("success", ""), evidence)

    vis = None
    if _mentions_image(node.get("success", "")):
        vis = judge_visual(node.get("success", ""), "")

    final = _combine(det, txt, vis)

    lf = get_langfuse()

    lf.create_score(
        trace_id=trace_id,
        name="goal_review",
        value=final["score"],
        data_type="NUMERIC",
        comment=final["reason"],
    )
    lf.create_score(
        trace_id=trace_id,
        name="passed",
        value=1.0 if final["pass"] else 0.0,
        data_type="BOOLEAN",
        comment="Deterministic gates: tests={}, files={}".format(
            det.get("tests_passed"),
            det.get("files_present"),
        ),
    )
    lf.flush()

    return final
