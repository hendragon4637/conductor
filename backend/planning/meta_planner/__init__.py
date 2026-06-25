"""Meta-planner: three-stage LLM pipeline for plan creation.

Pipeline:
  1. Goal Formulator  (File 01) — raw input ``MetaGoal`` with clarifying loop
  2. Clarify State    (File 06) — multi-turn pause/resume for vague goals
  3. Decomposer       (File 02) — ``MetaGoal`` → ``PlanDAG`` with size_estimates
  4. Split Oversized  (File 07) — plan-time node splitting for oversized nodes
  5. Check Generator  (File 02b) — ``PlanDAG`` → per-node L1/L2 checks from rubrics

All stages share the same config-driven ``meta_planner`` model role.
"""

from backend.planning.domain_profile import (
    DomainProfile,
    get_domain_profile,
    infer_domain,
    seed_domain_profiles,
)

from backend.planning.meta_planner.goal_formulator import (
    MetaGoal,
    Deferred,
    formulate,
    run_formulation,
)

from backend.planning.meta_planner.clarify import (
    ClarifyPending,
    formulate_or_clarify,
    condense,
)

from backend.planning.meta_planner.decomposer import (
    Member,
    PlanNode,
    PlanDAG,
    decompose,
    roster_enum,
)

from backend.planning.meta_planner.split import (
    split_oversized,
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

from backend.planning.meta_planner.agent_generator import (
    AgentConfigProposal,
    needs_new_agent_config,
    propose_agent_config,
    validate_proposal,
    submit_proposal,
)

__all__ = [
    "DomainProfile",
    "get_domain_profile",
    "infer_domain",
    "seed_domain_profiles",
    "MetaGoal",
    "Deferred",
    "formulate",
    "run_formulation",
    "ClarifyPending",
    "formulate_or_clarify",
    "condense",
    "Member",
    "PlanNode",
    "PlanDAG",
    "decompose",
    "roster_enum",
    "split_oversized",
    "AllChecks",
    "PerNodeChecks",
    "generate_checks",
    "attach_checks_to_dag",
    "get_meta_planner_model",
    "call_llm_structured",
    "AgentConfigProposal",
    "needs_new_agent_config",
    "propose_agent_config",
    "validate_proposal",
    "submit_proposal",
]
