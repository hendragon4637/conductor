"""Evaluator microservice — quality gates for Conductor plan execution.

Runs the meta-evaluator pipeline (L1 deterministic checks → L2 rubric judge)
when a node is observed as "done" by the watcher, and emits gate-evaluated
events to drive remediation or advancement.

Also provides:
- L3 calibration endpoint for periodic drift detection against the golden set.
- Ratchet-trigger consumer for running agent-config experiments.
- Patience-based early stopping (best-so-far + hard cap) across remediation
  attempts to bound retry cost.

Event consumption (``evaluator.q``):
  ``node.observed``   — Run evaluator gates when a node finishes execution.
  ``ratchet.trigger``  — Run a ratchet experiment for a given agent config.

Event emission:
  ``gate.evaluated``  — Outcome of the evaluator gate (done | remediate | failed).
  ``node.steer``      — Reuse existing AionUi conversation for fix-forward (steering_count < 5).
  ``node.remediate``  — Spawn a brand new team when steering is exhausted (steering_count >= 5).
"""
