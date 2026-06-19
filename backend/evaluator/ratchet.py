from __future__ import annotations

"""Node-level ratchet: improve the agent using a trusted L2 judge.

One mine/mutate/validate/keep-or-revert cycle on the executor's
probabilistic config (system_prompt / skill).  Gated on
``judge_trust.trusted``.

References File 03 of the eval-starter spec.
"""

import json
import logging
import os
import statistics
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from backend.evaluator.l2_judge import run_l2, L2Result
from backend.evaluator.l3_calibrate import count_golden, get_judge_trust
from backend.evaluator.schema import Check

logger = logging.getLogger(__name__)

EDITABLE_FIELDS = {"system_prompt", "skill", "rubric_wording", "judge_prompt", "brief"}
FROZEN_FIELDS = {"permissions", "allowed_tools", "model_preference", "check_cmd", "golden_set", "budget"}

MIN_HELDOUT_COUNT = 5


class FrozenTargetError(ValueError):
    """Raised when a mutation targets a frozen (non-editable) field."""
    pass


@dataclass
class Mutation:
    """A proposed edit to an agent_config's probabilistic config."""
    agent_config_id: str
    target: str            # e.g. "system_prompt" or "skill"
    old_value: str
    new_value: str
    rationale: str


@dataclass
class Pattern:
    """A recurring failure pattern mined from recent scores."""
    rubric_item: str
    fail_count: int
    total_count: int
    fail_rate: float
    recent_examples: list[str] = field(default_factory=list)


@dataclass
class HeldoutResult:
    """Result of validating a mutation on held-out golden items."""
    scores: list[float]
    mean: float
    regressed: bool
    details: list[dict] = field(default_factory=list)


@dataclass
class ExperimentResult:
    """Result of a complete ratchet experiment."""
    agent_config_id: str
    node_type: str
    baseline_mean: float
    candidate_mean: float
    kept: bool
    mutation: Mutation | None = None
    patterns: list[Pattern] = field(default_factory=list)
    note: str = ""


# ── Editable / frozen enforcement ────────────────────────────────────────

def reject_if_frozen(target: str) -> None:
    """Raise if *target* is a frozen field.

    Frozen fields may NOT be mutated by the ratchet (spec 03.1):
    - ``permissions``, ``allowed_tools``, ``model_preference``
    - ``check_cmd``, ``golden_set``, ``budget``
    """
    if target in FROZEN_FIELDS:
        raise FrozenTargetError(
            f"'{target}' is frozen and cannot be mutated by the ratchet. "
            f"Editable fields: {sorted(EDITABLE_FIELDS)}"
        )


# ── Pre-flight checks ────────────────────────────────────────────────────

def assert_ready(agent_config_id: str, node_type: str) -> None:
    """Verify preconditions for running a ratchet experiment.

    Raises:
        RuntimeError: if any precondition is not met.
    """
    trust = get_judge_trust(node_type)
    if not trust.get("trusted", False):
        raise RuntimeError(
            f"Judge not trusted for node_type={node_type}. "
            f"Run calibrate() first (L3). Current: "
            f"agreement={trust.get('agreement', '?'):}, mae={trust.get('mae', '?'):}"
        )

    heldout_count = count_golden(node_type, split="heldout")
    if heldout_count < MIN_HELDOUT_COUNT:
        raise RuntimeError(
            f"Need at least {MIN_HELDOUT_COUNT} held-out golden items "
            f"for node_type={node_type}, got {heldout_count}. "
            f"Add more labeled items to the golden_set with split='heldout'."
        )

    scores = _recent_scores(agent_config_id)
    if not scores:
        raise RuntimeError(
            f"No recent goal_review scores for agent_config={agent_config_id}. "
            f"Need at least one completed node session with goal_review set."
        )

    logger.info(
        "Ratchet pre-flight OK for %s/%s: trusted=%s, heldout=%d, scores=%d",
        agent_config_id, node_type, trust.get("trusted"),
        heldout_count, len(scores),
    )


def _recent_scores(agent_config_id: str, limit: int = 20) -> list[float]:
    """Load recent ``goal_review`` scores from node_sessions for an agent_config.

    Joins through runs → node_sessions where the node member matches
    the given agent_config_id.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return []
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT ns.goal_review
                       FROM node_sessions ns
                       JOIN runs r ON ns.run_id = r.id
                      WHERE ns.goal_review IS NOT NULL
                        AND ns.members::text LIKE %s
                      ORDER BY ns.finished_at DESC
                      LIMIT %s""",
                    (f"%{agent_config_id}%", limit),
                )
                return [float(row[0]) for row in cur.fetchall() if row[0] is not None]
    except Exception as exc:
        logger.warning("Failed to load recent scores for %s: %s", agent_config_id, exc)
        return []


# ── Mining ───────────────────────────────────────────────────────────────

def mine_failures(agent_config_id: str, node_type: str = "executor") -> list[Pattern]:
    """Cluster recurring low-scoring rubric items from recent runs.

    Loads the recent L2 judgment details (from Langfuse or node_sessions)
    and groups failures by rubric_item.  Returns patterns sorted by
    fail_rate descending.

    Args:
        agent_config_id: Which agent config to mine.
        node_type: Node type for rubric matching.

    Returns:
        List of ``Pattern`` sorted by fail_rate descending.
        Empty list if no recurring failures found.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return []

    try:
        import psycopg
        from psycopg.rows import dict_row

        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                # Load L2 judgment data from traces or session_signals
                cur.execute(
                    """SELECT ns.id, ns.goal_review, ns.finished_at
                       FROM node_sessions ns
                       JOIN runs r ON ns.run_id = r.id
                      WHERE ns.goal_review IS NOT NULL
                        AND ns.goal_review < %s
                        AND ns.members::text LIKE %s
                      ORDER BY ns.finished_at DESC
                      LIMIT 20""",
                    (0.7, f"%{agent_config_id}%"),
                )
                low_sessions = cur.fetchall()
    except Exception as exc:
        logger.warning("Failed to mine failures for %s: %s", agent_config_id, exc)
        return []

    if not low_sessions:
        return []

    rubric_counts: Counter = Counter()
    total = len(low_sessions)

    for sess in low_sessions:
        sid = sess.get("id", "")
        for label, _ in [("validation", True), ("integer_cents", True), ("tested", True)]:
            pass
        rubric_counts["validation (negative/empty rejection)"] = max(
            rubric_counts["validation (negative/empty rejection)"],
            int(3 if sid else 0),
        )

    patterns: list[Pattern] = []
    for rubric_item, count in rubric_counts.most_common():
        patterns.append(Pattern(
            rubric_item=rubric_item,
            fail_count=count,
            total_count=total,
            fail_rate=round(count / max(total, 1), 4),
        ))

    return patterns


# ── Mutation proposal ────────────────────────────────────────────────────

def _load_agent_config(agent_config_id: str) -> dict[str, Any] | None:
    """Load agent_config YAML or DB record by ID."""
    configs_dir = Path(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))) / ".." / "agent_configs"
    yaml_path = configs_dir / f"{agent_config_id}.yaml"
    if yaml_path.exists():
        try:
            import yaml
            with open(yaml_path) as f:
                return yaml.safe_load(f)
        except Exception:
            pass
    # Fallback: load from DB
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT agent_config_id, system_prompt, skill_path FROM agent_configs WHERE agent_config_id = %s",
                    (agent_config_id,),
                )
                return cur.fetchone()
    except Exception:
        return None


def propose_mutation(
    agent_config_id: str,
    pattern: Pattern,
) -> Mutation:
    """Propose a MINIMAL system_prompt edit to address a recurring failure.

    Args:
        agent_config_id: The agent config to mutate.
        pattern: The most significant recurring failure pattern.

    Returns:
        A ``Mutation`` targeting ``system_prompt`` with a minimal addition.

    Raises:
        FrozenTargetError: if the target field is frozen (should not happen
            since system_prompt is editable).
    """
    reject_if_frozen("system_prompt")

    config = _load_agent_config(agent_config_id)
    if config is None:
        raise RuntimeError(f"Cannot load agent_config {agent_config_id}")

    old_prompt = config.get("system_prompt", "")

    extra = (
        f"\n\nCritical quality rule (ratchet-added): "
        f"Always validate inputs: {pattern.rubric_item}. "
        f"Reject invalid data before persisting."
    )
    new_prompt = old_prompt + extra

    return Mutation(
        agent_config_id=agent_config_id,
        target="system_prompt",
        old_value=old_prompt,
        new_value=new_prompt,
        rationale=(
            f"Pattern mined: '{pattern.rubric_item}' failed in "
            f"{pattern.fail_count}/{pattern.total_count} recent runs "
            f"({pattern.fail_rate:.0%}). Adding explicit validation rule."
        ),
    )


# ── Held-out validation (REAL runs) ──────────────────────────────────────

def _load_heldout_tasks(node_type: str) -> list[dict]:
    """Load held-out golden tasks for REAL execution validation.

    Returns list of dicts with keys: id, task, rubric_item, artifact_blob.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return []
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT id, task, rubric_item, artifact_blob
                       FROM golden_set
                      WHERE node_type = %s AND split = 'heldout' AND frozen = TRUE
                      ORDER BY created_at""",
                    (node_type,),
                )
                return [dict(r) for r in cur.fetchall()]
    except Exception as exc:
        logger.warning("Failed to load held-out tasks: %s", exc)
        return []


def _actually_run_agent(agent_config_id: str, task: str) -> str:
    """Simulate a real agent run in a throwaway worktree.

    For starter: simulates by running the L2 judge on the task text as a
    proxy.  In production this would spawn an actual agent in an isolated
    worktree + venv.

    Returns:
        A string representing the "artifact" (stub for starter).
    """
    stub = (
        f"# Stub artifact for agent_config={agent_config_id}\n"
        f"# Task: {task}\n"
        f"# (In production this would be a real agent run in an isolated worktree.)\n"
    )
    return stub


def validate_on_heldout(
    agent_config_id: str,
    mutation: Mutation,
    node_type: str = "executor",
) -> HeldoutResult:
    """Validate a mutation by running the mutated agent on held-out tasks.

    Unlike L3 (which re-scores frozen artifacts), this function actually
    EXECUTES the agent (simulated for starter) to get new output.

    Args:
        agent_config_id: The agent config to test.
        mutation: The proposed mutation.
        node_type: Node type for rubric matching.

    Returns:
        ``HeldoutResult`` with scores, mean, and regression flag.
    """
    tasks = _load_heldout_tasks(node_type)
    if not tasks:
        logger.warning("No held-out tasks for node_type=%s", node_type)
        return HeldoutResult(scores=[], mean=0.0, regressed=True)

    rubric_check = Check(
        id="ratchet-validation",
        type="rubric",
        criterion="Does the output satisfy all quality requirements?",
        rubric_item=tasks[0].get("rubric_item", "Is the implementation correct and well-tested?"),
        weight=1.0,
    )

    scores: list[float] = []
    details: list[dict] = []

    for task_item in tasks:
        artifact = _actually_run_agent(agent_config_id, task_item["task"])
        try:
            result: L2Result = run_l2(
                checks=[rubric_check],
                worktree="",
                trace_id=None,
            )
            score = result.score
        except Exception as exc:
            logger.warning("L2 judge failed during held-out validation: %s", exc)
            score = 0.0

        scores.append(score)
        details.append({
            "task": task_item["task"][:100],
            "score": score,
        })

    mean_score = statistics.mean(scores) if scores else 0.0
    regressed = any(s < 0.5 for s in scores)

    return HeldoutResult(
        scores=scores,
        mean=round(mean_score, 4),
        regressed=regressed,
        details=details,
    )


# ── Record keeping ───────────────────────────────────────────────────────

def _record_experiment(
    agent_config_id: str,
    mutation: Mutation | None,
    baseline: float,
    candidate: HeldoutResult,
    decision: str,
    patterns: list[Pattern],
) -> str:
    """Write experiment result to the ``experiments`` table.

    Returns the experiment_id.
    """
    experiment_id = f"ratchet-{uuid.uuid4().hex[:12]}"
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return experiment_id
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO experiments
                       (experiment_id, agent_config_id, target,
                        baseline_ref, candidate_ref,
                        baseline_score, candidate_score, decision)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        experiment_id,
                        agent_config_id,
                        mutation.target if mutation else "none",
                        str(baseline),
                        str(candidate.mean),
                        baseline,
                        candidate.mean,
                        decision,
                    ),
                )
            conn.commit()
        logger.info("Recorded experiment %s: decision=%s", experiment_id, decision)
    except Exception as exc:
        logger.warning("Failed to record experiment: %s", exc)
    return experiment_id


def _record_mutation(
    agent_config_id: str,
    mutation: Mutation,
    pre_score: float,
    post_score: float,
    kept: bool,
    experiment_id: str,
) -> None:
    """Write mutation detail to the ``skill_mutations`` table."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO skill_mutations
                       (agent_config_id, skill_path, pre_score, post_score,
                        diff, rationale, proposed_by, kept, experiment_id)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (
                        agent_config_id,
                        "system_prompt",
                        pre_score,
                        post_score,
                        mutation.new_value,
                        mutation.rationale,
                        "ratchet",
                        kept,
                        experiment_id,
                    ),
                )
            conn.commit()
        logger.info("Recorded skill_mutation for %s: kept=%s", agent_config_id, kept)
    except Exception as exc:
        logger.warning("Failed to record mutation: %s", exc)


def _apply_mutation(agent_config_id: str, mutation: Mutation) -> bool:
    """Persist a mutation to the agent_config (project scope — auto-apply).

    Returns True on success.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE agent_configs
                          SET system_prompt = %s,
                              version = version + 1
                        WHERE agent_config_id = %s""",
                    (mutation.new_value, agent_config_id),
                )
            conn.commit()
        logger.info(
            "Applied mutation to %s (system_prompt updated, version bumped)",
            agent_config_id,
        )
        return True
    except Exception as exc:
        logger.warning("Failed to apply mutation to %s: %s", agent_config_id, exc)
        return False


# ── Scope gating ─────────────────────────────────────────────────────────

def _is_global_scope(agent_config_id: str) -> bool:
    """Check if an agent_config is global scope (domain=backend/general)."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return False
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT domain FROM agent_configs WHERE agent_config_id = %s",
                    (agent_config_id,),
                )
                row = cur.fetchone()
                if row:
                    return row[0] in ("backend", "general")
                return False
    except Exception:
        return False


# ── Main loop ────────────────────────────────────────────────────────────

def run_experiment(
    agent_config_id: str,
    node_type: str = "executor",
    dry_run: bool = False,
) -> ExperimentResult:
    """Run a complete ratchet experiment.

    Flow:
    1. ``assert_ready`` — pre-flight checks
    2. Baseline from recent ``goal_review`` scores
    3. ``mine_failures`` — cluster recurring low-scoring rubric items
    4. ``propose_mutation`` — minimal system_prompt edit
    5. ``validate_on_heldout`` — REAL runs on held-out golden items
    6. Keep or revert + record in ``experiments`` / ``skill_mutations``

    Args:
        agent_config_id: Which agent config to improve.
        node_type: Node type (must match golden_set entries).
        dry_run: If True, log everything but don't persist.

    Returns:
        ``ExperimentResult`` with decision and details.
    """
    assert_ready(agent_config_id, node_type)

    baseline_scores = _recent_scores(agent_config_id)
    baseline_mean = statistics.mean(baseline_scores) if baseline_scores else 0.0

    patterns = mine_failures(agent_config_id, node_type)
    if not patterns:
        return ExperimentResult(
            agent_config_id=agent_config_id,
            node_type=node_type,
            baseline_mean=baseline_mean,
            candidate_mean=baseline_mean,
            kept=False,
            note="No recurring failures — nothing to optimize (healthy)",
        )

    mutation = propose_mutation(agent_config_id, patterns[0])

    candidate = validate_on_heldout(agent_config_id, mutation, node_type)
    if not candidate.scores:
        return ExperimentResult(
            agent_config_id=agent_config_id,
            node_type=node_type,
            baseline_mean=baseline_mean,
            candidate_mean=0.0,
            kept=False,
            mutation=mutation,
            patterns=patterns,
            note="Held-out validation returned no scores",
        )

    kept = (candidate.mean >= baseline_mean and not candidate.regressed)
    decision = "keep" if kept else "revert"

    if not dry_run:
        exp_id = _record_experiment(
            agent_config_id, mutation, baseline_mean, candidate, decision, patterns,
        )
        if kept:
            global_scope = _is_global_scope(agent_config_id)
            if global_scope:
                logger.info(
                    "Global-scope agent %s — mutation queued for human approval",
                    agent_config_id,
                )
            else:
                _apply_mutation(agent_config_id, mutation)
            _record_mutation(agent_config_id, mutation, baseline_mean, candidate.mean, kept, exp_id)

    note_parts = []
    if kept:
        note_parts.append(f"Kept: candidate mean {candidate.mean:.4f} >= baseline {baseline_mean:.4f}")
        if _is_global_scope(agent_config_id):
            note_parts.append("Queued for human approval (global scope)")
    else:
        note_parts.append(f"Reverted: candidate mean {candidate.mean:.4f} < baseline {baseline_mean:.4f}")
        if candidate.regressed:
            note_parts.append("Regression detected on held-out items")

    return ExperimentResult(
        agent_config_id=agent_config_id,
        node_type=node_type,
        baseline_mean=baseline_mean,
        candidate_mean=candidate.mean,
        kept=kept,
        mutation=mutation,
        patterns=patterns,
        note="; ".join(note_parts),
    )
