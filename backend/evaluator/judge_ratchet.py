"""Judge ratchet: improve the judge's rubric against frozen golden labels.

One cycle: mine disagreements → propose one bounded mutation → two-split
validate (calibration + heldout) → keep/revert.  Multiple cycles with
plateau detection.

Usage:
    python -m backend.evaluator.judge_ratchet --capability executor [--dry-run]
"""
from __future__ import annotations

import json
import logging
import os
import statistics
from difflib import SequenceMatcher
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.evaluator.l2_judge import run_l2, load_rubric_config
from backend.evaluator.l3_calibrate import (
    _load_golden_for_calibrate,
    _build_check_from_golden,
    calibrate,
    _resolve_active_rubric_id,
)
from backend.evaluator.schema import Check
from backend.evaluator.ratchet_lock import acquire_ratchet_lock, release_ratchet_lock, assert_no_ratchet_lock

logger = logging.getLogger(__name__)

MAX_CYCLES = 3
AGREEMENT_IMPROVEMENT_MIN = 0.02
PLATEAU_CONSECUTIVE_REVERTS = 2
MAX_DIFF_LENGTH = 800


@dataclass
class DimDisagreement:
    dim_id: str
    rubric_item: str
    direction: str  # "lenient" (judge scores higher) or "harsh" (judge scores lower)
    count: int
    examples: list[dict] = field(default_factory=list)


@dataclass
class MinedDisagreements:
    capability: str
    dims: list[DimDisagreement]
    total_items: int


@dataclass
class CycleResult:
    cycle: int
    mined: MinedDisagreements | None
    proposal: dict | None
    decision: str  # kept | reverted | rejected_boundary | nothing_to_mine
    control_agreement: float
    candidate_agreement: float
    control_mae: float
    candidate_mae: float
    rationale: str = ""


def _score_to_binary(score: float) -> bool:
    return score >= 0.5


def _load_golden_items(capability: str, split: str = "calibration") -> list[dict]:
    return _load_golden_for_calibrate(capability, split)


def _build_check_from_golden_item(item: dict) -> Check:
    return _build_check_from_golden(item)


def _current_judge_model() -> str:
    return os.environ.get("JUDGE_MODEL_ID", "gpt-oss-120b")


MINED_DISAGREEMENT_THRESHOLD = 0.15


def mine_disagreements(capability: str) -> MinedDisagreements | None:
    """Compare judge scores vs golden labels per calibration item.

    Uses the current active rubric to re-score calibration items, then
    identifies dimensions where |judge - golden| > threshold.
    Returns None if no significant disagreements exist.
    """
    items = _load_golden_items(capability, split="calibration")
    if not items:
        logger.info("No calibration items for %s — nothing to mine", capability)
        return None

    rubric_cfg = load_rubric_config(capability)
    if not rubric_cfg:
        logger.info("No active rubric for %s — cannot mine", capability)
        return None

    # Score each item with the current rubric
    dim_scores: dict[str, list[dict]] = {}
    for item in items:
        check = _build_check_from_golden_item(item)
        human_score = float(item.get("expected_score") or (1.0 if item.get("human_label") else 0.0))
        human_met = item.get("human_label", False)

        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"mine-{str(item['id'])[:12]}-")
            artifact_path = Path(tmpdir) / "artifact.py"
            artifact_path.write_text(item.get("artifact_blob", "") or "", encoding="utf-8")
            result = run_l2(
                checks=[check], worktree=tmpdir, trace_id=None,
                node_context={"deliverables": ["artifact.py"]},
                rubric_dims=rubric_cfg,
            )
            judge_score = result.score
            judge_reason = result.judgments[0].explanation if result.judgments else ""
        except Exception as exc:
            logger.warning("Judge call failed during mine for %s: %s", item["id"], exc)
            judge_score = 0.0
            judge_reason = ""
        finally:
            if tmpdir:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)

        abs_err = abs(judge_score - human_score)
        if abs_err <= MINED_DISAGREEMENT_THRESHOLD:
            continue

        dim_id = check.id
        dim_scores.setdefault(dim_id, []).append({
            "item_id": str(item["id"]),
            "judge_score": judge_score,
            "human_score": human_score,
            "human_met": human_met,
            "abs_err": abs_err,
            "direction": "lenient" if judge_score > human_score else "harsh",
            "judge_reason": judge_reason[:500],
            "rubric_item": item.get("rubric_item", ""),
        })

    if not dim_scores:
        logger.info("No significant disagreements found for %s", capability)
        return None

    mined_dims: list[DimDisagreement] = []
    for dim_id, scores in sorted(dim_scores.items()):
        directions = [s["direction"] for s in scores]
        dominant_direction = max(set(directions), key=directions.count)
        examples = sorted(scores, key=lambda s: s["abs_err"], reverse=True)[:3]
        mined_dims.append(DimDisagreement(
            dim_id=dim_id,
            rubric_item=scores[0]["rubric_item"],
            direction=dominant_direction,
            count=len(scores),
            examples=examples,
        ))

    mined_dims.sort(key=lambda d: d.count, reverse=True)
    logger.info(
        "Mined %d disagreeing dimensions for %s: %s",
        len(mined_dims), capability,
        [(d.dim_id, d.direction, d.count) for d in mined_dims],
    )

    return MinedDisagreements(
        capability=capability,
        dims=mined_dims,
        total_items=len(items),
    )


def propose_rubric_mutation(
    mined: MinedDisagreements,
    past_reverted_ids: list[str] | None = None,
) -> dict | None:
    """Propose one bounded rubric mutation via LLM for the worst disagreement.

    Returns a mutation dict with keys: target, dim, diff, rationale.
    Returns None if the LLM call fails.
    """
    if not mined.dims:
        return None

    worst = mined.dims[0]

    try:
        from backend.llm.gateway import call as gateway_call
    except ImportError:
        logger.warning("LLM gateway unavailable — cannot propose mutation")
        return None

    current_rubric = load_rubric_config(mined.capability)
    if not current_rubric:
        return None

    current_dim = None
    for d in current_rubric.get("dimensions", []):
        if d.get("id") == worst.dim_id:
            current_dim = d
            break

    dim_config_str = json.dumps(current_dim or worst.rubric_item, indent=2)
    anchors_str = json.dumps(current_rubric.get("anchors", []), indent=2)
    feedback_contract = current_rubric.get("feedback_contract", "")

    examples_str = json.dumps([
        {
            "judge_score": round(e["judge_score"], 4),
            "human_score": round(e["human_score"], 4),
            "direction": e["direction"],
            "judge_reason": e["judge_reason"][:300],
        }
        for e in worst.examples
    ], indent=2)

    reject_history = ""
    if past_reverted_ids:
        reject_history = f"\nPreviously reverted mutations (DO NOT repeat): {', '.join(past_reverted_ids)}\n"

    prompt = f"""The judge disagrees with golden labels on capability "{mined.capability}".

Pattern:
- Dimension: {worst.dim_id}
- Direction: judge is {worst.direction} ({worst.count}/{mined.total_items} calibration items disagree)
- Rubric item: {worst.rubric_item}

Examples (golden vs judge, with judge's reasoning):
{examples_str}

Current rubric config for this dimension:
{dim_config_str}

Current rubric anchors (score scale):
{anchors_str}

Feedback contract:
{feedback_contract}

Propose ONE minimal change to FIX this disagreement pattern:
- Reword ONE evaluation step
- Adjust ONE rubric anchor description
- Add ONE bundle rule (what evidence to inspect)

Do NOT rewrite the rubric. Do NOT change what quality means — change how it is detected/scaled.
Do NOT touch thresholds, golden labels, gate language ("agreement", "0.8", "pass automatically").
{reject_history}
Return JSON only:
{{"target": "step|anchor|bundle_rule", "dim": "{worst.dim_id}", "diff": "<the one change>", "rationale": "<1 sentence>"}}"""

    import time as _time
    max_attempts = 3
    last_exc = None
    mutation = None
    for attempt in range(1, max_attempts + 1):
        try:
            result = gateway_call("planning", [
                {"role": "user", "content": prompt},
            ], temperature=0.3, max_tokens=16384, timeout=600)
            raw = result["choices"][0]["message"]["content"]
            if raw is None:
                raise ValueError("LLM returned empty content")
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[1]
                raw = raw.rsplit("```", 1)[0]
            mutation = json.loads(raw.strip())
            break
        except Exception as exc:
            last_exc = exc
            if attempt < max_attempts:
                logger.warning("Mutation proposal attempt %d/%d failed: %s — retrying in 5s", attempt, max_attempts, exc)
                _time.sleep(5)
            else:
                logger.warning("Mutation proposal LLM call failed after %d attempts: %s", max_attempts, last_exc)
                return None

    assert mutation is not None

    # Diff length cap
    if len(mutation.get("diff", "")) > MAX_DIFF_LENGTH:
        logger.warning("Mutation rejected (boundary): diff exceeds %d chars", MAX_DIFF_LENGTH)
        return {"_rejected": "boundary_violation", "reason": f"Diff exceeds {MAX_DIFF_LENGTH} chars"}

    # Past-reverted similarity check
    diff_text = mutation.get("diff", "")
    if past_reverted_ids:
        for past_diff in past_reverted_ids:
            similarity = SequenceMatcher(None, diff_text, past_diff).ratio()
            if similarity > 0.75:
                logger.warning("Mutation rejected (boundary): similar to past reverted mutation (similarity=%.3f)", similarity)
                return {"_rejected": "boundary_violation", "reason": f"Similar to past reverted mutation (similarity={similarity:.3f})"}

    required_keys = {"target", "dim", "diff", "rationale"}
    if not all(k in mutation for k in required_keys):
        logger.warning("Mutation proposal missing required keys: got %s", list(mutation.keys()))
        return None

    valid_targets = {"step", "anchor", "bundle_rule"}
    if mutation.get("target") not in valid_targets:
        logger.warning("Invalid mutation target: %s", mutation.get("target"))
        return None

    forbidden_phrases = ["agreement", "threshold", "0.8", "pass automatically", "0.15", "mae"]
    diff_lower = mutation.get("diff", "").lower()
    for phrase in forbidden_phrases:
        if phrase in diff_lower:
            logger.warning("Mutation rejected (boundary violation): contains '%s'", phrase)
            return {"_rejected": "boundary_violation", "reason": f"Contains forbidden phrase: {phrase}"}

    logger.info(
        "Proposed mutation for %s dim=%s target=%s: %s",
        mined.capability, mutation["dim"], mutation["target"], mutation["rationale"],
    )
    return mutation


def rescore_split(
    capability: str,
    split: str,
    rubric_dims: dict,
    item_limit: int = 0,
) -> dict:
    """Re-score frozen golden artifacts for a split using an arbitrary rubric config.

    Returns dict with keys: scores (list), mean, mae, agreement, items.
    This is the cheap path — no agent runs, just judge calls.
    """
    items = _load_golden_items(capability, split=split)
    if not items:
        return {"scores": [], "mean": 0.0, "mae": 0.0, "agreement": 0.0, "items": 0, "note": f"No {split} items"}

    if item_limit > 0:
        items = items[:item_limit]

    scores: list[float] = []
    abs_errors: list[float] = []
    item_results: list[dict] = []

    for item in items:
        check = _build_check_from_golden_item(item)
        human_score = float(item.get("expected_score") or (1.0 if item.get("human_label") else 0.0))

        tmpdir = None
        try:
            tmpdir = tempfile.mkdtemp(prefix=f"rescore-{str(item['id'])[:12]}-")
            artifact_path = Path(tmpdir) / "artifact.py"
            artifact_path.write_text(item.get("artifact_blob", "") or "", encoding="utf-8")
            result = run_l2(
                checks=[check], worktree=tmpdir, trace_id=None,
                node_context={"deliverables": ["artifact.py"]},
                rubric_dims=rubric_dims,
            )
            judge_score = result.score
        except Exception as exc:
            logger.warning("Rescore failed for item %s: %s", item["id"], exc)
            judge_score = 0.0
        finally:
            if tmpdir:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)

        scores.append(judge_score)
        abs_err = abs(judge_score - human_score)
        abs_errors.append(abs_err)
        item_results.append({
            "item_id": str(item["id"]),
            "judge_score": judge_score,
            "human_score": human_score,
            "abs_err": abs_err,
        })

    mean_score = statistics.mean(scores) if scores else 0.0
    mae = statistics.mean(abs_errors) if abs_errors else 0.0
    agreements = sum(1 for e in abs_errors if e <= 0.15)
    agreement = agreements / len(abs_errors) if abs_errors else 0.0

    return {
        "scores": [round(s, 4) for s in scores],
        "mean": round(mean_score, 4),
        "mae": round(mae, 4),
        "agreement": round(agreement, 4),
        "items": len(item_results),
        "item_results": item_results,
        "llm_calls": len(scores),
        "estimated_cost": round(len(scores) * 0.002, 4),
    }


def _better(candidate: dict, control: dict) -> bool:
    """Return True if candidate improves over control on both agreement and mae."""
    if candidate["items"] == 0:
        return False
    if control["items"] == 0:
        return True
    agreement_up = candidate["agreement"] >= control["agreement"] + AGREEMENT_IMPROVEMENT_MIN
    mae_not_worse = candidate["mae"] <= control["mae"] + 0.01
    return agreement_up and mae_not_worse


def _record_judge_experiment(
    capability: str,
    control_rubric: str,
    candidate_rubric: str,
    mined: MinedDisagreements | None,
    mutation: dict | None,
    calib_control: dict,
    calib_candidate: dict,
    heldout_control: dict,
    heldout_candidate: dict,
    decision: str,
    judge_model: str,
) -> None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    import uuid
    exp_id = f"jr-{uuid.uuid4().hex[:12]}"
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO judge_experiments
                       (id, capability, control_rubric, candidate_rubric,
                        mined_disagreement, mutation_diff, rationale,
                        calib_control, calib_candidate,
                        heldout_control, heldout_candidate,
                        decision, judge_model, decided_at)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, NOW())""",
                    (
                        exp_id, capability, control_rubric, candidate_rubric,
                        json.dumps({
                            "dims": [{"id": d.dim_id, "direction": d.direction, "count": d.count} for d in (mined.dims if mined else [])],
                            "total_items": mined.total_items if mined else 0,
                        }) if mined else None,
                        json.dumps(mutation) if mutation else None,
                        (mutation or {}).get("rationale", "") if mutation else "",
                        json.dumps(calib_control), json.dumps(calib_candidate),
                        json.dumps(heldout_control), json.dumps(heldout_candidate),
                        decision, judge_model,
                    ),
                )
            conn.commit()
        logger.info("Recorded judge experiment %s: decision=%s for %s", exp_id, decision, capability)
    except Exception as exc:
        logger.warning("Failed to record judge experiment: %s", exc)


def run_judge_ratchet(capability: str, dry_run: bool = False) -> list[CycleResult]:
    """Run up to MAX_CYCLES of the judge ratchet on a capability.

    Each cycle: mine disagreements → propose mutation → two-split validate
    → keep/revert. Stops early on plateau (2 consecutive reverts) or when
    no disagreements remain.

    Returns list of CycleResult, one per cycle.
    """
    judge_model = _current_judge_model()
    results: list[CycleResult] = []
    consecutive_reverts = 0
    active_rubric_id = _resolve_active_rubric_id(capability)

    if not active_rubric_id:
        logger.warning("No active rubric for %s — cannot run judge ratchet", capability)
        return results

    # Acquire ratchet lock
    if not dry_run:
        assert_no_ratchet_lock(capability, "judge")
        if not acquire_ratchet_lock(capability, "judge"):
            logger.warning("REFUSED: main ratchet active on %s — one ruler at a time", capability)
            results.append(CycleResult(
                cycle=0, mined=None, proposal=None,
                decision="rejected_boundary",
                control_agreement=0, candidate_agreement=0,
                control_mae=0, candidate_mae=0,
                rationale="Main ratchet active on this capability — lock refused",
            ))
            return results

    try:
        for cycle in range(1, MAX_CYCLES + 1):
            logger.info("=== Judge ratchet cycle %d/%d for %s ===", cycle, MAX_CYCLES, capability)

            mined = mine_disagreements(capability)
            if mined is None:
                logger.info("No disagreements to mine — judge ratchet complete for %s", capability)
                results.append(CycleResult(
                    cycle=cycle, mined=None, proposal=None,
                    decision="nothing_to_mine",
                    control_agreement=0, candidate_agreement=0,
                    control_mae=0, candidate_mae=0,
                    rationale="No disagreements found",
                ))
                break

            past_reverted = _load_past_reverted_mutation_ids(capability)
            mutation = propose_rubric_mutation(mined, past_reverted_ids=past_reverted)
            if mutation is None:
                logger.warning("No mutation proposed — aborting ratchet for %s", capability)
                results.append(CycleResult(
                    cycle=cycle, mined=mined, proposal=None,
                    decision="rejected_boundary",
                    control_agreement=0, candidate_agreement=0,
                    control_mae=0, candidate_mae=0,
                    rationale="No valid mutation proposed",
                ))
                break

            if mutation.get("_rejected") == "boundary_violation":
                logger.warning("Mutation rejected (boundary): %s", mutation.get("reason", ""))
                results.append(CycleResult(
                    cycle=cycle, mined=mined, proposal=mutation,
                    decision="rejected_boundary",
                    control_agreement=0, candidate_agreement=0,
                    control_mae=0, candidate_mae=0,
                    rationale=mutation.get("reason", "Boundary violation"),
                ))
                break

            if dry_run:
                logger.info("[DRY RUN] cycle=%d: mined=%d dims, proposal=%s", cycle, len(mined.dims), mutation.get("diff", ""))
                results.append(CycleResult(
                    cycle=cycle, mined=mined, proposal=mutation,
                    decision="kept" if dry_run else "reverted",
                    control_agreement=0, candidate_agreement=0,
                    control_mae=0, candidate_mae=0,
                    rationale="Dry run — no changes applied",
                ))
                continue

            # Two-split validation
            control_rubric = load_rubric_config(capability)
            if not control_rubric:
                logger.warning("Lost active rubric for %s — aborting", capability)
                break

            calib_control = rescore_split(capability, "calibration", control_rubric)
            heldout_control = rescore_split(capability, "heldout", control_rubric)

            candidate_rubric = _apply_mutation_to_dims(control_rubric, mutation)
            calib_candidate = rescore_split(capability, "calibration", candidate_rubric)
            heldout_candidate = rescore_split(capability, "heldout", candidate_rubric)

            calib_improved = _better(calib_candidate, calib_control)
            heldout_improved = _better(heldout_candidate, heldout_control)

            if calib_improved and heldout_improved:
                decision = "kept"
                rationale = f"Calibration agreement {calib_control['agreement']}->{calib_candidate['agreement']}, heldout {heldout_control['agreement']}->{heldout_candidate['agreement']}"
            else:
                decision = "reverted"
                if not calib_improved:
                    rationale = f"Calibration did not improve: {calib_control['agreement']} -> {calib_candidate['agreement']}"
                else:
                    rationale = f"Heldout did not improve (overfitting): {heldout_control['agreement']} -> {heldout_candidate['agreement']}"

            logger.info(
                "Cycle %d: calib %s/%s heldout %s/%s → %s (%s)",
                cycle,
                calib_control["agreement"], calib_candidate["agreement"],
                heldout_control["agreement"], heldout_candidate["agreement"],
                decision, rationale,
            )

            new_version: str | None = None
            if decision == "kept":
                new_version = _activate_candidate_rubric(capability, control_rubric, candidate_rubric, mutation)
                consecutive_reverts = 0
                if new_version:
                    calibrate(capability)
            else:
                consecutive_reverts += 1

            _record_judge_experiment(
                capability=capability,
                control_rubric=active_rubric_id,
                candidate_rubric=new_version,
                mined=mined, mutation=mutation,
                calib_control=calib_control, calib_candidate=calib_candidate,
                heldout_control=heldout_control, heldout_candidate=heldout_candidate,
                decision=decision, judge_model=judge_model,
            )

            total_calls = calib_control.get("llm_calls", 0) + calib_candidate.get("llm_calls", 0) + \
                          heldout_control.get("llm_calls", 0) + heldout_candidate.get("llm_calls", 0)
            logger.info("Cycle %d: %d judge calls, estimated cost $%.4f", cycle, total_calls, total_calls * 0.002)

            results.append(CycleResult(
                cycle=cycle, mined=mined, proposal=mutation,
                decision=decision,
                control_agreement=calib_control["agreement"],
                candidate_agreement=calib_candidate["agreement"],
                control_mae=calib_control["mae"],
                candidate_mae=calib_candidate["mae"],
                rationale=rationale,
            ))

            if consecutive_reverts >= PLATEAU_CONSECUTIVE_REVERTS:
                logger.info(
                    "Plateau detected: %d consecutive reverts for %s — stopping ratchet",
                    consecutive_reverts, capability,
                )
                logger.warning(
                    "ESCALATE: judge ratchet plateaued on %s after %d cycles. "
                    "Manual ladder: (1) review golden labels, (2) check evidence extraction, "
                    "(3) consider model swap for the judge.",
                    capability, cycle,
                )
                break

    finally:
        if not dry_run:
            release_ratchet_lock(capability, "judge")

    return results


def _apply_mutation_to_dims(base_rubric: dict, mutation: dict) -> dict:
    """Apply a mutation to a copy of the rubric dims (immutable operation)."""
    import copy
    result = copy.deepcopy(base_rubric)
    target_dim = mutation.get("dim", "")
    target = mutation.get("target", "")
    diff = mutation.get("diff", "")

    for dim in result.get("dimensions", []):
        if dim.get("id") != target_dim:
            continue
        if target == "step":
            steps = dim.get("evaluation_steps", [])
            if steps:
                steps[-1] = diff
            else:
                steps.append(diff)
        elif target == "anchor":
            anchors = result.get("anchors", [])
            new_text = diff
            match_old = None
            for sep in [" → ", " -> ", " ==> ", " => "]:
                if sep in diff:
                    parts = diff.split(sep, 1)
                    match_old = parts[0].strip().strip('"\'')
                    new_text = parts[1].strip().strip('"\'')
                    break
            if anchors:
                matched = False
                for anchor in anchors:
                    old_outcome = anchor.get("expected_outcome", "")
                    if match_old and (match_old in old_outcome or old_outcome in match_old):
                        anchor["expected_outcome"] = new_text
                        matched = True
                        break
                if not matched:
                    anchors[-1]["expected_outcome"] = new_text
            else:
                anchors.append({"score_range": [0, 2], "expected_outcome": new_text})
            result["anchors"] = anchors
        elif target == "bundle_rule":
            bundles = result.setdefault("bundles", {})
            bundles[f"rule_{target_dim}"] = diff
        break

    return result


def _activate_candidate_rubric(
    capability: str,
    control: dict,
    candidate: dict,
    mutation: dict,
) -> str | None:
    """Create a new judge_rubrics version from candidate, deactivate old, activate new."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None

    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COALESCE(MAX(version), 0) + 1 FROM judge_rubrics WHERE capability = %s",
                    (capability,),
                )
                next_version = cur.fetchone()[0]

                cur.execute(
                    "SELECT id FROM judge_rubrics WHERE capability = %s AND active = TRUE LIMIT 1",
                    (capability,),
                )
                active_row = cur.fetchone()
                parent_id = active_row[0] if active_row else None

                new_id = f"{capability}-v{next_version}"
                cur.execute(
                    """UPDATE judge_rubrics SET active = FALSE WHERE capability = %s AND active = TRUE""",
                    (capability,),
                )
                cur.execute(
                    """INSERT INTO judge_rubrics (id, capability, version, dims, source, parent_version, active)
                       VALUES (%s, %s, %s, %s, 'judge_ratchet', %s, TRUE)""",
                    (new_id, capability, next_version, json.dumps(candidate), parent_id),
                )
            conn.commit()
        logger.info("Activated new rubric %s for %s (from parent %s)", new_id, capability, parent_id)
        return new_id
    except Exception as exc:
        logger.warning("Failed to activate candidate rubric: %s", exc)
        return None


def _load_past_reverted_mutation_ids(capability: str) -> list[str]:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return []
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT mutation_diff FROM judge_experiments
                       WHERE capability = %s AND decision = 'reverted'
                       ORDER BY created_at DESC LIMIT 5""",
                    (capability,),
                )
                rows = cur.fetchall()
                ids = []
                for row in rows:
                    if row[0]:
                        try:
                            parsed = json.loads(row[0])
                            if isinstance(parsed, dict) and "diff" in parsed:
                                ids.append(parsed["diff"][:60])
                        except (json.JSONDecodeError, TypeError):
                            ids.append(str(row[0])[:60])
                return ids
    except Exception:
        return []


def rollback_rubric(capability: str, version: int) -> bool:
    """Rollback to a specific rubric version for a capability.
    
    Deactivates the current active rubric and activates the specified version.
    Returns True on success.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                # Deactivate current
                cur.execute(
                    "UPDATE judge_rubrics SET active = FALSE WHERE capability = %s AND active = TRUE",
                    (capability,),
                )
                # Activate target version
                cur.execute(
                    "UPDATE judge_rubrics SET active = TRUE WHERE capability = %s AND version = %s",
                    (capability, version),
                )
            conn.commit()
        logger.info("Rolled back %s to version %d", capability, version)
        return True
    except Exception as exc:
        logger.warning("Rollback failed for %s version %d: %s", capability, version, exc)
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    import argparse
    parser = argparse.ArgumentParser(description="Judge ratchet: improve rubric against frozen golden labels")
    parser.add_argument("--capability", required=True, help="Capability to run ratchet on (e.g. executor, backend_api)")
    parser.add_argument("--dry-run", action="store_true", help="Run without persisting changes")
    parser.add_argument("--cycles", type=int, default=MAX_CYCLES, help=f"Max cycles (default {MAX_CYCLES})")

    args = parser.parse_args()
    # Override MAX_CYCLES from CLI
    MAX_CYCLES = args.cycles

    logger.info("Starting judge ratchet for capability=%s dry_run=%s", args.capability, args.dry_run)
    results = run_judge_ratchet(args.capability, dry_run=args.dry_run)

    print("\n=== Judge Ratchet Results ===")
    for r in results:
        print(f"  Cycle {r.cycle}: {r.decision}")
        if r.proposal:
            print(f"    Proposal: {r.proposal.get('diff', '')[:100]}")
        print(f"    Control:  agreement={r.control_agreement:.4f} mae={r.control_mae:.4f}")
        print(f"    Candidate: agreement={r.candidate_agreement:.4f} mae={r.candidate_mae:.4f}")
        print(f"    Rationale: {r.rationale}")
    print(f"Done: {len(results)} cycles")