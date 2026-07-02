"""SQLAlchemy ORM models — L3 meta-evaluation golden set, judge trust, experiments, skill mutations.

Ownership convention (enforced by review):
- golden_set:     WRITER = human-only (add_golden)
- judge_trust:    WRITER = evaluator-svc (l3_calibrate)
- experiments:    WRITER = ratchet-svc (run_experiment)
- skill_mutations: WRITER = ratchet-svc (mutate)
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Boolean, Column, Float, ForeignKey, Numeric, String, Text, DateTime
from sqlalchemy.dialects.postgresql import UUID, ARRAY
from sqlalchemy.sql import func

from shared.models import Base

# REAL type removed from sqlalchemy 2.0 top-level; use Float instead
Real = Float


# ── Golden Set (L3 frozen anchor) — WRITER: human only ──────────────────────

class GoldenSet(Base):
    """A frozen, human-curated labeled example used as the ground-truth anchor
    for L3 meta-evaluation.

    Nothing in the automated pipeline writes to this table.
    Entries come ONLY via human action (``add_golden()``).

    Columns ending with a ``_at`` suffix are timestamptz.
    ``split`` partitions items into ``calibration`` (scored by judge) and
    ``heldout`` (unseen during calibration, used to validate generalisation).
    """

    __tablename__ = "golden_set"

    id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    node_type = Column(String, nullable=False)
    artifact_ref = Column(String, nullable=False)
    rubric_item = Column(String, nullable=False)
    human_label = Column(Boolean, nullable=False)
    expected_score = Column(Numeric(5, 4), nullable=True)
    labeled_by = Column(String, nullable=False, default="human")
    frozen = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    task = Column(String, nullable=True)
    artifact_blob = Column(String, nullable=True)
    split = Column(String, nullable=False, default="calibration")

    def __repr__(self) -> str:
        return (
            f"<GoldenSet id={self.id!s} node_type={self.node_type!r} "
            f"frozen={self.frozen} split={self.split!r}>"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a dict view suitable for serialisation."""
        return {
            "id": str(self.id) if self.id else None,
            "node_type": self.node_type,
            "artifact_ref": self.artifact_ref,
            "rubric_item": self.rubric_item,
            "human_label": self.human_label,
            "expected_score": float(self.expected_score) if self.expected_score is not None else None,
            "labeled_by": self.labeled_by,
            "frozen": self.frozen,
            "task": self.task,
            "artifact_blob": self.artifact_blob,
            "split": self.split,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


# ── Judge Trust (L3 calibration result) — WRITER: evaluator-svc ─────────────

class JudgeTrust(Base):
    """Records how well the L2 judge's scores agree with the human golden set
    for a given ``node_type``.

    Written by ``calibrate()`` in ``backend.evaluator.l3_calibrate``.
    The ratchet is gated on ``trusted == True``.
    """

    __tablename__ = "judge_trust"

    node_type = Column(String, primary_key=True)
    agreement = Column(Real, nullable=True)
    mae = Column(Real, nullable=True)
    trusted = Column(Boolean, nullable=False, default=False)
    calibrated_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<JudgeTrust node_type={self.node_type!r} "
            f"agreement={self.agreement} mae={self.mae} trusted={self.trusted}>"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "node_type": self.node_type,
            "agreement": float(self.agreement) if self.agreement is not None else None,
            "mae": float(self.mae) if self.mae is not None else None,
            "trusted": self.trusted,
            "calibrated_at": self.calibrated_at.isoformat() if self.calibrated_at else None,
        }


# ── Experiments (ratchet A/B trials) — WRITER: ratchet-svc ──────────────────

class Experiment(Base):
    """Records a ratchet experiment comparing baseline vs candidate scores.

    Created by ``run_experiment()`` in ``backend.ratchet.experiment``.
    ``decision`` is one of ``running``, ``applied``, ``rejected``, ``pending``.
    """

    __tablename__ = "experiments"

    experiment_id = Column(String, primary_key=True)
    agent_config_id = Column(String, ForeignKey("agent_configs.agent_config_id"), nullable=False)
    target = Column(String, nullable=True)
    baseline_ref = Column(String, nullable=True)
    candidate_ref = Column(String, nullable=True)
    dataset = Column(String, nullable=True)
    baseline_score = Column(Numeric, nullable=True)
    candidate_score = Column(Numeric, nullable=True)
    decision = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:
        return (
            f"<Experiment id={self.experiment_id!r} "
            f"agent_config={self.agent_config_id!r} decision={self.decision!r}>"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "experiment_id": self.experiment_id,
            "agent_config_id": self.agent_config_id,
            "target": self.target,
            "baseline_ref": self.baseline_ref,
            "candidate_ref": self.candidate_ref,
            "dataset": self.dataset,
            "baseline_score": float(self.baseline_score) if self.baseline_score is not None else None,
            "candidate_score": float(self.candidate_score) if self.candidate_score is not None else None,
            "decision": self.decision,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @property
    def delta(self) -> float | None:
        """Score improvement (candidate - baseline)."""
        if self.baseline_score is not None and self.candidate_score is not None:
            return float(self.candidate_score) - float(self.baseline_score)
        return None


# ── Skill Mutations (ratchet trail) — WRITER: ratchet-svc ───────────────────

class SkillMutation(Base):
    """Tracks every mutation to a probabilistic agent-config artifact (skill,
    prompt, rubric, agents_md) along with the pre/post quality scores.

    ``kept`` records the ratchet decision (``True`` = applied,
    ``False`` = reverted, ``None`` = in-flight).

    Columns ``trigger_trace_ids`` is a PostgreSQL UUID array referencing
    Langfuse trace IDs whose failures triggered the mutation proposal.
    """

    __tablename__ = "skill_mutations"

    mutation_id = Column(UUID, primary_key=True, server_default=func.gen_random_uuid())
    agent_config_id = Column(String, ForeignKey("agent_configs.agent_config_id"), nullable=False)
    skill_path = Column(String, nullable=False)
    trigger_trace_ids = Column(ARRAY(UUID), nullable=True)
    pre_score = Column(Numeric(5, 4), nullable=True)
    post_score = Column(Numeric(5, 4), nullable=True)
    pre_hash = Column(String, nullable=True)
    post_hash = Column(String, nullable=True)
    diff = Column(Text, nullable=True)
    rationale = Column(Text, nullable=True)
    proposed_by = Column(String, nullable=True)
    kept = Column(Boolean, nullable=True)
    decision_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())
    experiment_id = Column(String, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<SkillMutation id={self.mutation_id!s} "
            f"config={self.agent_config_id!r} kept={self.kept}>"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "mutation_id": str(self.mutation_id) if self.mutation_id else None,
            "agent_config_id": self.agent_config_id,
            "skill_path": self.skill_path,
            "trigger_trace_ids": [str(t) for t in self.trigger_trace_ids] if self.trigger_trace_ids else None,
            "pre_score": float(self.pre_score) if self.pre_score is not None else None,
            "post_score": float(self.post_score) if self.post_score is not None else None,
            "pre_hash": self.pre_hash,
            "post_hash": self.post_hash,
            "diff": self.diff,
            "rationale": self.rationale,
            "proposed_by": self.proposed_by,
            "kept": self.kept,
            "decision_at": self.decision_at.isoformat() if self.decision_at else None,
            "experiment_id": self.experiment_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
