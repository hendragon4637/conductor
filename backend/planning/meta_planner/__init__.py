"""Meta-planner: three-stage LLM pipeline for plan creation.

Pipeline:
  1. Goal Formulator  (File 01) — raw input → ``MetaGoal`` with clarifying loop
  2. Decomposer       (File 02) — ``MetaGoal`` → ``PlanDAG`` with real roster
  3. Check Generator  (File 02b) — ``PlanDAG`` → per-node L1/L2 checks from rubrics

All stages share the same config-driven ``meta_planner`` model role.
"""

from backend.planning.meta_planner.goal_formulator import (
    MetaGoal,
    Deferred,
    formulate,
    run_formulation,
)

from backend.planning.meta_planner.decomposer import (
    Member,
    PlanNode,
    PlanDAG,
    decompose,
    roster_enum,
)

from backend.planning.meta_planner.check_generator import (
    AllChecks,
    PerNodeChecks,
    generate_checks,
    attach_checks_to_dag,
)

from backend.planning.meta_planner.llm import (
    get_meta_planner_model,
    call_llm_structured,
)

__all__ = [
    "MetaGoal",
    "Deferred",
    "formulate",
    "run_formulation",
    "Member",
    "PlanNode",
    "PlanDAG",
    "decompose",
    "roster_enum",
    "AllChecks",
    "PerNodeChecks",
    "generate_checks",
    "attach_checks_to_dag",
    "get_meta_planner_model",
    "call_llm_structured",
]
