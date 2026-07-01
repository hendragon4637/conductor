"""planner-svc — intent intake, goal formulation, decompose, plan gate.

Runs the formulate → clarify → decompose → gate pipeline as a LangGraph
state machine.  On ratification emits ``plan.ratified`` via the outbox.
"""
