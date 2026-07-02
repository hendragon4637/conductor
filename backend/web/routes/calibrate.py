"""Calibrate routes — trigger L3 calibration and query judge trust."""
from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel

from contracts.version import CONTRACTS_VERSION

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/calibrate", tags=["calibrate"])


class CalibrateResponse(BaseModel):
    """Response from the L3 calibration endpoint.

    Mirrors the evaluator-svc ``CalibrateResponse`` model.
    """
    node_type: str
    trusted: bool
    agreement: float
    mae: float
    total: int
    note: str


# ── Helpers ──────────────────────────────────────────────────────────────


def _emit_calibrate_trigger(node_type: str) -> None:
    """Write a ``CalibrateTrigger`` event to the transactional outbox.

    The outbox relay in evaluator-svc picks this up, publishes it to
    RabbitMQ on the ``calibrate.trigger`` routing key, and
    evaluator-svc's ``on_calibration_trigger`` handler processes it.
    """
    db = os.environ.get("DATABASE_URL", "")
    if not db:
        logger.warning("No DATABASE_URL — cannot emit CalibrateTrigger")
        return
    payload = json.dumps({
        "node_type": node_type,
        "env": "staging",
        "ts": time.time(),
    })
    try:
        import psycopg

        with psycopg.connect(db) as c:
            with c.cursor() as cur:
                cur.execute(
                    "INSERT INTO outbox (routing_key, payload, contracts_version) VALUES (%s, %s, %s)",
                    ("calibrate.trigger", payload, CONTRACTS_VERSION),
                )
            c.commit()
        logger.info("CalibrateTrigger emitted for node_type=%s", node_type)
    except Exception as exc:
        logger.exception("Failed to emit CalibrateTrigger: %s", exc)


# ── Routes ───────────────────────────────────────────────────────────────


@router.post("/{node_type}", response_model=CalibrateResponse)
async def calibrate_node(node_type: str) -> CalibrateResponse:
    """Run L3 calibration for a node type against the frozen golden set.

    Re-scores all frozen golden artifacts for ``node_type`` via the L2
    judge, computes MAE and item-level agreement, and persists results
    to ``judge_trust``.  Emits a ``CalibrateTrigger`` event to the
    outbox so that the evaluator-svc can also react.

    This runs out-of-band (not in the hot path).
    """
    from backend.evaluator.l3_calibrate import calibrate as run_calibrate

    try:
        report = run_calibrate(node_type)
    except Exception as exc:
        logger.exception("Calibration failed for node_type=%s", node_type)
        return CalibrateResponse(
            node_type=node_type,
            trusted=False,
            agreement=0.0,
            mae=0.0,
            total=0,
            note=f"Calibration error: {exc}",
        )

    # Emit CalibrateTrigger event so evaluator-svc is aware
    _emit_calibrate_trigger(node_type)

    return CalibrateResponse(
        node_type=report.node_type,
        trusted=report.trusted,
        agreement=report.agreement,
        mae=report.mae,
        total=report.total,
        note=report.note,
    )


@router.post("/sweep")
async def calibrate_sweep() -> dict[str, Any]:
    """Run L3 calibration for all active node types with golden data.

    Delegates to ``backend.triggers.jobs.calibrate_sweep`` which
    discovers node types from ``golden_set`` and calibrates each one.
    """
    from backend.triggers.jobs import calibrate_sweep as run_sweep

    return run_sweep({})


@router.get("/{node_type}")
async def get_calibration_status(node_type: str) -> dict[str, Any]:
    """Get current judge trust status for a node type.

    Returns the ``judge_trust`` row with keys: node_type, agreement,
    mae, trusted, calibrated_at.  Returns all-defaults if no row exists.
    """
    from backend.evaluator.l3_calibrate import get_judge_trust

    return get_judge_trust(node_type)
