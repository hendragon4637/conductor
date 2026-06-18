from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Any

from backend.evaluator.l2_judge import JUDGE_USER_PROMPT
from backend.evaluator.l2_judge import _default_judge_llm, _extract_json
from backend.evaluator.l3_meta.golden import GoldenItem, load_golden
from backend.evaluator.l3_meta.jury import jury_score

logger = logging.getLogger(__name__)

DRIFT_TOLERANCE = float(os.environ.get("L3_DRIFT_TOLERANCE", "0.15"))


# ── Helpers ──────────────────────────────────────────────────────────────────


def _evaluate_with_l2(artifact: str, rubric_item: str) -> dict[str, Any]:
    """Run the production L2 judge on a single rubric item."""
    prompt = JUDGE_USER_PROMPT.format(rubric_item=rubric_item, artifact=artifact)
    raw = _default_judge_llm(prompt)
    parsed = _extract_json(raw)
    if parsed is None:
        return {"criteria_met": False, "explanation": "L2 judge unparseable"}
    return {
        "criteria_met": bool(parsed.get("criteria_met", False)),
        "explanation": str(parsed.get("explanation", "")),
    }


def _load_artifact(artifact_ref: str) -> str:
    """Read artifact text from a file path or return the ref as-is."""
    if os.path.isfile(artifact_ref):
        try:
            with open(artifact_ref) as f:
                return f.read()
        except Exception:
            pass
    return artifact_ref


# ── Drift measurement ────────────────────────────────────────────────────────


def measure_disagreement(drift_report: list[dict]) -> dict[str, Any]:
    """Compute aggregate drift metrics from a list of per-item comparisons.

    Args:
        drift_report: List of dicts, each with keys:
            ``l2_met``, ``human_label``, ``jury_met`` (or None).

    Returns:
        Dict with keys:
        - ``total``: int
        - ``disagreements``: int (L2 != human)
        - ``disagreement_rate``: float (0.0–1.0)
        - ``jury_supported``: int (jury agrees with human when L2 disagrees)
    """
    total = len(drift_report)
    disagreements = sum(1 for d in drift_report if d.get("l2_met") != d.get("human_label"))
    jury_supported = sum(
        1 for d in drift_report
        if d.get("l2_met") != d.get("human_label")
        and d.get("jury_met") is not None
        and d.get("jury_met") == d.get("human_label")
    )
    return {
        "total": total,
        "disagreements": disagreements,
        "disagreement_rate": round(disagreements / total, 4) if total > 0 else 0.0,
        "jury_supported": jury_supported,
    }


# ── Rubric refinement proposal ──────────────────────────────────────────────


def propose_rubric_refinement(
    node_type: str,
    drift_report: list[dict],
    metrics: dict,
) -> dict[str, Any] | None:
    """Generate a rubric refinement proposal from drift data.

    When L2 disagrees with human labels, produces a suggestion
    for rewording the rubric items that showed drift.

    Returns a dict with keys: ``node_type``, ``rationale``,
    ``old_rubric``, ``new_rubric``, ``drift_report``, or None
    if no disagreements exist.
    """
    if metrics.get("disagreements", 0) == 0:
        return None

    sample_disagreements = [d for d in drift_report if d.get("l2_met") != d.get("human_label")]

    if not sample_disagreements:
        return None

    sd = sample_disagreements[0]
    old_rubric = sd.get("rubric_item", "")

    rationale = (
        f"L2 judge disagreed with human golden label on {metrics['disagreements']}/{metrics['total']} "
        f"items (disagreement_rate={metrics['disagreement_rate']:.2f}) for node_type={node_type}. "
        f"Jury supported human in {metrics['jury_supported']} cases. "
        f"Proposal: refine rubric wording to reduce drift."
    )

    return {
        "node_type": node_type,
        "rationale": rationale,
        "old_rubric": old_rubric,
        "new_rubric": _suggest_rubric_rewrite(rationale, old_rubric),
        "drift_report": {
            "metrics": metrics,
            "samples": [
                {"rubric_item": d.get("rubric_item", ""),
                 "l2_met": d.get("l2_met"),
                 "human_label": d.get("human_label"),
                 "jury_met": d.get("jury_met")}
                for d in sample_disagreements[:5]
            ],
        },
    }


def _suggest_rubric_rewrite(rationale: str, old_rubric: str) -> str:
    """LLM call to suggest a rubric rewrite that would reduce drift."""
    prompt = (
        f"Rationale: {rationale}\n\n"
        f"Current rubric: {old_rubric}\n\n"
        f"Suggest a more precise rewrite of the rubric item above "
        f"to reduce disagreement with human judges. "
        f"Output ONLY the new rubric text, no explanation."
    )
    try:
        raw = _default_judge_llm(prompt)
        parsed = _extract_json(raw)
        if parsed and "new_rubric" in parsed:
            return parsed["new_rubric"]
        return raw.strip() or old_rubric
    except Exception:
        return old_rubric


# ── Queue for approval ───────────────────────────────────────────────────────


def queue_for_approval(proposal: dict[str, Any]) -> str:
    """Write a rubric refinement proposal to the rubric_refinements table.

    Proposals are written with status='pending' and must be human-approved.

    Returns the proposal UUID as a string, or empty string on failure.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("No DATABASE_URL — can't queue proposal")
        return ""
    proposal_id = str(uuid.uuid4())
    try:
        import psycopg

        with psycopg.connect(url) as c:
            with c.cursor() as cur:
                cur.execute(
                    """INSERT INTO rubric_refinements
                       (id, node_type, rationale, old_rubric, new_rubric,
                        drift_report, status, proposed_by)
                       VALUES (%s, %s, %s, %s, %s, %s, 'pending', 'l3_meta')""",
                    (proposal_id, proposal.get("node_type", ""),
                     proposal.get("rationale", ""),
                     proposal.get("old_rubric", ""),
                     proposal.get("new_rubric", ""),
                     json.dumps(proposal.get("drift_report", {}))),
                )
            c.commit()
        logger.info("Queued rubric refinement %s for node_type=%s",
                     proposal_id, proposal.get("node_type"))
    except Exception as e:
        logger.exception("Failed to queue rubric refinement: %s", e)
        return ""
    return proposal_id


# ── Calibration loop ─────────────────────────────────────────────────────────


def _log_to_langfuse(metrics: dict, node_type: str) -> None:
    """Write calibration metrics to Langfuse as a score on a standalone trace."""
    try:
        from backend.observability.langfuse_client import get_langfuse
        lf = get_langfuse()
        trace = lf.trace(
            name=f"l3-meta-eval-{node_type}",
            metadata={"node_type": node_type, "source": "l3_meta"},
        )
        trace.score(
            name="l3_disagreement_rate",
            value=metrics.get("disagreement_rate", 0.0),
            data_type="NUMERIC",
            comment=f"L2 vs human golden: {metrics.get('disagreements', 0)}/{metrics.get('total', 0)} disagreements",
        )
        lf.flush()
    except Exception:
        pass


def run_meta_eval(
    node_types: list[str] | None = None,
    tolerance: float | None = None,
) -> list[dict[str, Any]]:
    """Run a full L3 meta-evaluation calibration cycle.

    For each active node type:
      1. Load frozen golden items from DB.
      2. Run the L2 judge on each item (what the production judge says).
      3. Run the diverse jury on each item (corroboration).
      4. Compute drift (L2 vs human_label).
      5. If drift > tolerance, generate a rubric refinement proposal and
         queue it for human approval.
      6. Log calibration metrics to Langfuse.

    Args:
        node_types: List of node types to evaluate. Defaults to all types
                    that have golden items.
        tolerance: Maximum acceptable disagreement rate before proposing
                   a refinement. Defaults to ``L3_DRIFT_TOLERANCE`` env var
                   (0.15).

    Returns:
        List of dicts, one per node type, each with keys:
        ``node_type``, ``metrics``, ``proposal_id`` (or None).
    """
    if tolerance is None:
        tolerance = DRIFT_TOLERANCE

    if node_types is None:
        from backend.evaluator.generate import RUBRIC_PRESETS
        node_types = list(RUBRIC_PRESETS.keys())

    results: list[dict] = []

    for node_type in node_types:
        golden_items = load_golden(node_type)
        if not golden_items:
            logger.info("No golden items for node_type=%s — skipping", node_type)
            continue

        drift_report: list[dict] = []
        for item in golden_items:
            artifact = _load_artifact(item.artifact_ref)
            l2_result = _evaluate_with_l2(artifact, item.rubric_item)
            jury_result = jury_score(artifact, item.rubric_item)

            drift_report.append({
                "rubric_item": item.rubric_item,
                "human_label": item.human_label,
                "l2_met": l2_result.get("criteria_met"),
                "l2_explanation": l2_result.get("explanation", ""),
                "jury_met": jury_result.get("criteria_met"),
                "jury_note": jury_result.get("note", ""),
            })

        metrics = measure_disagreement(drift_report)

        _log_to_langfuse(metrics, node_type)

        proposal_id = None
        if metrics["disagreement_rate"] > tolerance:
            proposal = propose_rubric_refinement(node_type, drift_report, metrics)
            if proposal:
                proposal_id = queue_for_approval(proposal)
                logger.info(
                    "Drift %.4f > %.4f for %s — queued proposal %s",
                    metrics["disagreement_rate"], tolerance,
                    node_type, proposal_id,
                )
        else:
            logger.info(
                "Drift %.4f <= %.4f for %s — no proposal needed",
                metrics["disagreement_rate"], tolerance, node_type,
            )

        results.append({
            "node_type": node_type,
            "metrics": metrics,
            "proposal_id": proposal_id,
        })

    return results
