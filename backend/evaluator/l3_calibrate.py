from __future__ import annotations

"""L3 calibration: check L2 judge accuracy against a frozen golden set.

``calibrate(node_type)`` re-scores frozen golden artifacts (NO real runs),
compares judge vs human label, computes MAE + agreement, and upserts
``judge_trust``.  Ratchet (File 03) is gated on ``judge_trust.trusted``.

Usage:
    from backend.evaluator.l3_calibrate import calibrate
    report = calibrate("executor")
"""

import json
import logging
import os
import statistics
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.evaluator.l2_judge import run_l2
from backend.evaluator.schema import Check

logger = logging.getLogger(__name__)

AGREEMENT_THRESHOLD = float(os.environ.get("L3_AGREEMENT_THRESHOLD", "0.8"))
MAE_THRESHOLD = float(os.environ.get("L3_MAE_THRESHOLD", "0.15"))


@dataclass
class CalibrationItem:
    """Per-item comparison between judge score and human label."""
    item_id: str
    judge_score: float
    human_score: float
    judge_met: bool
    human_met: bool
    absolute_error: float


@dataclass
class CalibrationReport:
    """Aggregate calibration result for one node_type."""
    node_type: str
    total: int
    agreement: float
    mae: float
    trusted: bool
    items: list[CalibrationItem] = field(default_factory=list)
    note: str = ""


def _score_to_binary(score: float) -> bool:
    """Convert a continuous score (0-1) to binary met/not-met.

    Uses 0.5 as the decision boundary (below 0.5 = not met).
    """
    return score >= 0.5


def _load_golden_for_calibrate(node_type: str, split: str = "calibration") -> list[dict]:
    """Load golden items from DB for a node_type + split.

    Returns list of dicts with keys: id, node_type, artifact_blob,
    rubric_item, human_label, expected_score, task.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("No DATABASE_URL — can't load golden set")
        return []

    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, node_type, artifact_blob, rubric_item,
                              human_label, expected_score, task
                       FROM golden_set
                      WHERE node_type = %s AND split = %s AND frozen = TRUE
                      ORDER BY created_at""",
                    (node_type, split),
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.exception("Failed to load golden set for %s/%s: %s", node_type, split, exc)
        return []


def _build_check_from_golden(item: dict) -> Check:
    """Build a single ``Check`` object from a golden-set row.

    The rubric_item column becomes the L2 rubric check.
    """
    return Check(
        id=f"cal-{item['id']}",
        type="rubric",
        criterion=item.get("rubric_item", ""),
        rubric_item=item.get("rubric_item", ""),
        weight=1.0,
    )


def _resolve_active_rubric_id(node_type: str) -> str:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return ""
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM judge_rubrics WHERE capability = %s AND active = TRUE LIMIT 1",
                    (node_type,),
                )
                row = cur.fetchone()
                return row[0] if row else ""
    except Exception:
        return ""


def _upsert_judge_trust(
    node_type: str,
    agreement: float,
    mae: float,
    trusted: bool,
    rubric_id: str = "",
) -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("No DATABASE_URL — can't persist judge_trust")
        return

    try:
        import psycopg

        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO judge_trust (node_type, agreement, mae, trusted, rubric_id, calibrated_at)
                       VALUES (%s, %s, %s, %s, %s, NOW())
                       ON CONFLICT (node_type) DO UPDATE SET
                         agreement = EXCLUDED.agreement,
                         mae = EXCLUDED.mae,
                         trusted = EXCLUDED.trusted,
                         rubric_id = EXCLUDED.rubric_id,
                         calibrated_at = NOW()""",
                    (node_type, agreement, mae, trusted, rubric_id),
                )
            conn.commit()
        logger.info(
            "judge_trust updated for %s: agreement=%.4f mae=%.4f trusted=%s rubric_id=%s",
            node_type, agreement, mae, trusted, rubric_id,
        )
    except Exception as exc:
        logger.exception("Failed to upsert judge_trust for %s: %s", node_type, exc)


def calibrate(
    node_type: str,
    agreement_threshold: float | None = None,
    mae_threshold: float | None = None,
) -> CalibrationReport:
    """Calibrate the L2 judge against the frozen golden set for a node_type.

    Re-scores frozen artifacts (NO real runs), compares judge score to
    human label, computes MAE + agreement, and writes to ``judge_trust``.

    Args:
        node_type: e.g. ``"executor"``, ``"planner"``, ``"reviewer"``.
        agreement_threshold: Minimum agreement rate to mark trusted
            (default: ``L3_AGREEMENT_THRESHOLD`` env var, 0.8).
        mae_threshold: Maximum MAE to mark trusted
            (default: ``L3_MAE_THRESHOLD`` env var, 0.15).

    Returns:
        ``CalibrationReport`` with per-item comparisons and aggregate metrics.

    The ratchet (File 03) is gated on ``trusted == True``.
    """
    if agreement_threshold is None:
        agreement_threshold = AGREEMENT_THRESHOLD
    if mae_threshold is None:
        mae_threshold = MAE_THRESHOLD

    items = _load_golden_for_calibrate(node_type, split="calibration")
    if not items:
        logger.warning("No golden items for node_type=%s — skipping calibration", node_type)
        return CalibrationReport(
            node_type=node_type, total=0, agreement=0.0, mae=0.0,
            trusted=False, note="No golden items available",
        )

    comparisons: list[CalibrationItem] = []

    for item in items:
        artifact_blob = item.get("artifact_blob", "") or item.get("artifact_ref", "")
        check = _build_check_from_golden(item)
        human_raw = item.get("expected_score") or (1.0 if item.get("human_label") else 0.0)
        human_score = float(human_raw) if human_raw is not None else 0.0
        human_met = item.get("human_label", False)

        # Re-score frozen artifact with L2 judge -- NO real run
        # Write artifact_blob to a temp directory so collect_artifact() can read it
        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"cal-{str(item['id'])[:12]}-")
            artifact_path = Path(tmpdir) / "artifact.py"
            artifact_path.write_text(artifact_blob or "", encoding="utf-8")
            result = run_l2(checks=[check], worktree=tmpdir, trace_id=None,
                            node_context={"deliverables": ["artifact.py"]},
                            capability=node_type)
            judge_score = result.score
        except Exception as exc:
            logger.warning("Judge call failed for golden item %s: %s", item["id"], exc)
            judge_score = 0.0
        finally:
            if tmpdir:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)

        judge_met = _score_to_binary(judge_score)
        abs_err = abs(judge_score - human_score)

        comparisons.append(CalibrationItem(
            item_id=str(item["id"]),
            judge_score=judge_score,
            human_score=human_score,
            judge_met=judge_met,
            human_met=human_met,
            absolute_error=abs_err,
        ))

    if not comparisons:
        return CalibrationReport(
            node_type=node_type, total=0, agreement=0.0, mae=0.0,
            trusted=False, note="No items after scoring",
        )

    mae = statistics.mean(c.absolute_error for c in comparisons)
    agreements = sum(
        1 for c in comparisons
        if abs(c.judge_score - c.human_score) <= mae_threshold
    )
    agreement = agreements / len(comparisons)
    trusted = (agreement >= agreement_threshold and mae <= mae_threshold)

    active_rubric_id = _resolve_active_rubric_id(node_type)

    report = CalibrationReport(
        node_type=node_type,
        total=len(comparisons),
        agreement=round(agreement, 4),
        mae=round(mae, 4),
        trusted=trusted,
        items=comparisons,
    )

    _upsert_judge_trust(node_type, report.agreement, report.mae, report.trusted, rubric_id=active_rubric_id)

    # Log calibration metrics to Langfuse (non-blocking)
    try:
        from backend.observability.langfuse_client import get_langfuse
        lf = get_langfuse()
        trace = lf.trace(
            name=f"l3_calibrate_{node_type}",
            metadata={"node_type": node_type, "source": "l3_calibrate"},
        )
        trace.score(
            name="l3_agreement",
            value=report.agreement,
            data_type="NUMERIC",
            comment=f"Agreement rate for {node_type} calibration",
        )
        trace.score(
            name="l3_mae",
            value=report.mae,
            data_type="NUMERIC",
            comment=f"Mean absolute error for {node_type} calibration",
        )
        lf.flush()
    except Exception:
        pass

    if not trusted:
        logger.info(
            "Calibration for %s: agreement=%.4f (need >=%.2f), "
            "mae=%.4f (need <=%.2f) — NOT trusted",
            node_type, agreement, agreement_threshold, mae, mae_threshold,
        )
    else:
        logger.info(
            "Calibration for %s: agreement=%.4f mae=%.4f — TRUSTED",
            node_type, agreement, mae,
        )

    return report


def count_golden(node_type: str, split: str | None = None) -> int:
    """Count frozen golden items for a node_type, optionally filtered by split."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return 0
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                if split:
                    cur.execute(
                        "SELECT COUNT(*) FROM golden_set WHERE node_type = %s AND split = %s AND frozen = TRUE",
                        (node_type, split),
                    )
                else:
                    cur.execute(
                        "SELECT COUNT(*) FROM golden_set WHERE node_type = %s AND frozen = TRUE",
                        (node_type,),
                    )
                row = cur.fetchone()
                return int(row[0]) if row else 0
    except Exception:
        return 0


def get_judge_trust(node_type: str) -> dict[str, Any]:
    url = os.environ.get("DATABASE_URL", "")
    default = {
        "node_type": node_type, "agreement": 0.0, "mae": 1.0,
        "trusted": False, "calibrated_at": None, "rubric_id": None,
    }
    if not url:
        return default
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT node_type, agreement, mae, trusted, calibrated_at, rubric_id FROM judge_trust WHERE node_type = %s",
                    (node_type,),
                )
                row = cur.fetchone()
                if row:
                    return dict(row)
                return default
    except Exception:
        return default
