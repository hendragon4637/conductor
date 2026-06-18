from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import psycopg
from psycopg.rows import dict_row

from backend.ratchet.scope import detect_scope as _detect_scope


AGENT_CONFIGS_DIR = Path("/opt/aipc/conductor/skills")


def _get_db() -> str:
    return os.environ["DATABASE_URL"]


def load_experiment(experiment_id: str) -> dict[str, Any] | None:
    """Load experiment record from the database."""
    with psycopg.connect(_get_db(), row_factory=dict_row) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT * FROM experiments WHERE experiment_id = %s",
                (experiment_id,),
            )
            return cur.fetchone()


def apply_mutation(experiment: dict[str, Any]) -> None:
    """Write the candidate artifact into the real artifact path."""
    target = experiment.get("target", "")
    candidate_ref = experiment.get("candidate_ref", "")
    agent_config = experiment.get("agent_config_id", "")

    if not candidate_ref or not target:
        return

    # candidate_ref is a JSON-encoded path or inline text
    try:
        candidate_data = json.loads(candidate_ref)
    except (json.JSONDecodeError, TypeError):
        candidate_data = {"text": candidate_ref}

    cfg_dir = agent_config.replace("backend-", "").replace("frontend-", "")
    if target == "skill":
        path = AGENT_CONFIGS_DIR / cfg_dir / "SKILL.md"
    elif target == "agents_md":
        path = AGENT_CONFIGS_DIR / cfg_dir / "AGENTS.md"
    elif target == "prompt":
        path = AGENT_CONFIGS_DIR / cfg_dir / "PROMPT.md"
    else:
        return

    text = candidate_data.get("text", candidate_ref)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def git_tag(experiment: dict[str, Any]) -> None:
    """Create a git tag for the mutation."""
    agent_config = experiment.get("agent_config_id", "unknown")
    experiment_id = experiment.get("experiment_id", "unknown")
    tag = f"ratchet/{agent_config}/{experiment_id[:8]}"
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    full_tag = f"{tag}-{timestamp}"

    try:
        subprocess.run(
            ["git", "tag", "-a", full_tag,
             "-m", f"ratchet keep: {agent_config} {experiment_id}"],
            cwd="/opt/aipc/conductor",
            capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass  # Non-fatal


def _make_skill_path(agent_config_id: str) -> str:
    cfg_short = agent_config_id.split(":", 1)[-1].replace("backend-", "")
    p = AGENT_CONFIGS_DIR / "backend" / cfg_short / "SKILL.md"
    return str(p)


def record_mutation(
    experiment_id: str,
    agent_config_id: str,
    kept: bool,
    rationale: str = "",
) -> None:
    """Write a record to skill_mutations."""
    import uuid
    with psycopg.connect(_get_db()) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO skill_mutations
                   (mutation_id, agent_config_id, skill_path,
                    experiment_id, kept, rationale)
                   VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (str(uuid.uuid4()), agent_config_id,
                 _make_skill_path(agent_config_id),
                 experiment_id, kept, rationale),
            )
        c.commit()


def decide(
    experiment_id: str,
    delta_threshold: float = 0.03,
) -> str:
    """Decide whether to keep or revert a mutation.

    Args:
        experiment_id: The experiment to evaluate.
        delta_threshold: Minimum score improvement to keep the mutation.

    Returns:
        ``"kept"``, ``"reverted"``, or ``"queued"`` (global-scope winners
        that require human approval).
    """
    exp = load_experiment(experiment_id)
    if exp is None:
        raise ValueError(f"Experiment {experiment_id} not found")

    baseline = float(exp.get("baseline_score", 0) or 0)
    candidate = float(exp.get("candidate_score", 0) or 0)
    delta = candidate - baseline
    score_decision = "kept" if delta >= delta_threshold else "reverted"

    cfg_id = exp.get("agent_config_id", "unknown")
    mutation_target = exp.get("target", "")

    # Scope gating (File 04.6)
    scope = _detect_scope(cfg_id)

    if score_decision == "kept" and scope == "global":
        decision = "queued"
    else:
        decision = score_decision

    # Update experiment record
    with psycopg.connect(_get_db()) as c:
        with c.cursor() as cur:
            cur.execute(
                """UPDATE experiments
                   SET decision = %s, candidate_score = %s
                   WHERE experiment_id = %s
                """,
                (decision, str(candidate), experiment_id),
            )
        c.commit()

    if decision == "kept":
        apply_mutation(exp)
        git_tag(exp)
        record_mutation(experiment_id, cfg_id, kept=True,
                        rationale=f"delta={delta:.4f} >= {delta_threshold}")
    elif decision == "queued":
        record_mutation(experiment_id, cfg_id, kept=None,
                        rationale=(
                            f"delta={delta:.4f} >= {delta_threshold} "
                            f"[GLOBAL] queued for human approval"
                        ))
    else:
        record_mutation(experiment_id, cfg_id, kept=False,
                        rationale=f"delta={delta:.4f} < {delta_threshold}")

    return decision
