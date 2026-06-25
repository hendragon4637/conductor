"""File 01 — Goal Formulator + Clarifying Loop + Convention Injection.

Turns a raw input (possibly vague) into a structured ``MetaGoal``.
If too vague, runs a clarifying loop: ask the human (interactive) or
defer with an "underspecified" flag (autonomous). Never fabricates
specificity for an autonomous goal.

After formulating, the domain-aware injector (File 02) enriches the
meta-goal with domain conventions from the profile registry — but only
for what the user didn't already state.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Callable, Literal

from pydantic import BaseModel, Field

from backend.planning.meta_planner.llm import call_llm_structured

from backend.planning.domain_profile import DomainProfile, get_domain_profile, infer_domain

logger = logging.getLogger(__name__)

MAX_CLARIFY_ROUNDS = 3


class MetaGoal(BaseModel):
    """Structured meta-goal produced by the formulator.

    Maps to plan fields: ``goal`` → plan.goal, raw input → plan.user_intent,
    ``spec`` + ``quality_intent`` flow to decompose + check-gen.

    Domain fields (File 02): ``domain``, ``applied_conventions``, and
    ``success_seed`` are populated by the convention-injection step after
    the initial formulation.
    """
    goal: str = Field(description="Normalized one-sentence objective")
    spec: str = Field(description="Constraints/shape/acceptance criteria")
    quality_intent: str = Field(description="How to judge the result — free-text rubric guidance")
    needs_clarification: bool = Field(
        default=False,
        description="True when the input is too vague to produce a complete meta-goal",
    )
    questions: list[str] = Field(
        default_factory=list,
        description="Minimal set of clarifying questions (only when needs_clarification=True)",
    )
    defer_reason: str | None = Field(
        default=None,
        description="Autonomous + unresolved: explains why the goal was deferred",
    )
    origin: str = Field(
        default="human",
        description="'human' | 'internal_drive' — controls clarifying strategy",
    )
    # --- Domain-aware convention injection (File 02) ---
    domain: str = Field(
        default="generic",
        description="Inferred product domain (software_app, cli_script, …)",
    )
    applied_conventions: list[str] = Field(
        default_factory=list,
        description="Conventions the formulator injected because the user was silent on them",
    )
    success_seed: str = Field(
        default="",
        description="Seeded success criteria from the domain profile's acceptance block",
    )


@dataclass
class Deferred:
    """Returned when an autonomous goal cannot be resolved."""
    reason: str
    questions: list[str] = field(default_factory=list)


FORMULATE_PROMPT = """\
You are a goal-formulation engine. Given a raw user input and optional
recalled memory context, produce a structured meta-goal.

Assess if the input is specific enough to produce a complete, measurable
meta-goal. A specific input clearly states:
  - WHAT to build or do (feature, fix, change)
  - Any obvious constraints (tech stack, libraries, conventions)
  - The expected outcome or success signal

If a key decision is missing — what feature, what "better" means,
success criteria, scope boundaries — set needs_clarification=true and
list the MINIMUM set of clarifying questions. Be concise: 1-3 questions
maximum. DO NOT ask trivial questions.

Never invent unstated requirements. Never add scope or constraints the
user did not express.

Raw input:
{input}

Previous clarification history (if any):
{prior}

Recalled memory context:
{memory}

Origin: {origin}

Now produce the MetaGoal JSON."""



def formulate(
    raw_input: str,
    origin: str = "human",
    recalled: str = "",
    prior: str = "",
) -> MetaGoal:
    """Single LLM call: raw input → MetaGoal.

    Args:
        raw_input: The raw user ask (can be vague).
        origin: ``"human"`` (interactive clarify) or ``"internal_drive"`` (autonomous).
        recalled: Optional memory context from Neo4j/product memory.
        prior: Optional condensed multi-turn Q&A history (File 06 clarification).

    Returns:
        A ``MetaGoal`` parsed from the LLM response.
    """
    prompt = FORMULATE_PROMPT.format(
        input=raw_input,
        prior=prior or "(none)",
        memory=recalled or "(none)",
        origin=origin,
    )
    return call_llm_structured(prompt, schema=MetaGoal)


# ── Convention injection (File 02) ────────────────────────────────────────


def _user_already_addressed(raw_input: str, convention: str) -> bool:
    """Check if a convention is already implied by the user's raw input.

    Uses simple keyword overlap: if any significant word from the convention
    appears in the raw input, we assume the user may have addressed it.
    """
    # Extract significant words (4+ chars, not common stopwords)
    stopwords = {"with", "that", "this", "from", "have", "been", "will", "would", "could", "should", "their", "there", "about", "which"}
    convention_lower = convention.lower()
    raw_lower = raw_input.lower()
    for word in convention_lower.split():
        word = word.strip(".,;:!?()[]{}'\"")
        if len(word) >= 4 and word not in stopwords:
            if word in raw_lower:
                return True
    return False


def enrich_with_conventions(meta_goal: MetaGoal, raw_input: str) -> MetaGoal:
    """Inject domain conventions into a meta-goal based on its inferred domain.

    Only injects conventions the user didn't already mention (avoids overriding
    explicit intent).  For unknown domains (``infer_domain`` returns
    ``"generic"`` without keyword match), sets ``needs_clarification`` for
    interactive origins or applies the generic profile for autonomous ones.

    Args:
        meta_goal: The formulated meta-goal (may already have a domain set).
        raw_input: The original user input (used to detect unstated conventions).

    Returns:
        The enriched ``MetaGoal`` with ``domain``, ``applied_conventions``,
        and ``success_seed`` populated.
    """
    domain = infer_domain(f"{meta_goal.goal} {meta_goal.spec}")
    meta_goal.domain = domain

    if domain == "generic":
        # No specific domain keywords matched → mark for clarification
        if meta_goal.origin == "human" and not meta_goal.needs_clarification:
            meta_goal.needs_clarification = True
            meta_goal.questions.append(
                "What kind of deliverable is this (app, script, API, report, …)? "
                "I don't have conventions for it and need guidance."
            )
        # Still apply generic profile for the success seed
        profile = get_domain_profile("generic")
    else:
        profile = get_domain_profile(domain)

    if profile is None:
        logger.warning("No domain profile found for '%s' — skipping convention injection", domain)
        return meta_goal

    # Inject only UNSTATED conventions
    injected: list[str] = []
    for conv in profile.conventions:
        if not _user_already_addressed(raw_input, conv):
            injected.append(conv)

    if injected:
        conv_text = "\nConventions (auto-applied, confirm): " + "; ".join(injected)
        meta_goal.spec = (meta_goal.spec + conv_text) if meta_goal.spec else conv_text.strip()

    meta_goal.applied_conventions = injected

    # Seed success_seed from acceptance
    acc = profile.acceptance or {}
    seed_parts: list[str] = []
    for key in ("runnable_check", "completeness_criteria"):
        val = acc.get(key)
        if val:
            if isinstance(val, list):
                seed_parts.extend(val)
            elif isinstance(val, str):
                seed_parts.append(val)
    if seed_parts:
        meta_goal.success_seed = "; ".join(seed_parts)

    return meta_goal


def run_formulation(
    raw_input: str,
    origin: str = "human",
    ask_human: Callable[[list[str]], str] | None = None,
    recalled: str = "",
    skip_domain_injection: bool = False,
) -> MetaGoal | Deferred:
    """Full clarifying loop with domain-aware convention injection.

    For human-origin goals: if vague, generates questions and calls
    ``ask_human(questions)`` to get answers, folds them back, re-formulates.
    Cap at ``MAX_CLARIFY_ROUNDS``.

    For autonomous (internal_drive) goals: one memory-resolution attempt.
    If still vague → returns ``Deferred`` (never fabricates specifics).

    After the clarifying loop, runs convention injection (File 02) to
    enrich the meta-goal with the domain profile's conventions — unless
    ``skip_domain_injection=True``.

    Args:
        raw_input: The raw user ask.
        origin: ``"human"`` (interactive) or ``"internal_drive"`` (autonomous).
        ask_human: Callable that takes ``list[str]`` questions and returns
            a single string of answers. Required for interactive mode;
            ignored for autonomous.
        recalled: Optional memory context.
        skip_domain_injection: If True, skip the convention-injection step
            (used when caller wants to defer injection).

    Returns:
        ``MetaGoal`` on success, ``Deferred`` on unresolvable vagueness.

    Raises:
        ValueError: If ``origin="human"`` but ``ask_human`` is not provided.
    """
    if origin == "human" and ask_human is None:
        raise ValueError("human-origin goals require ask_human callable")

    mg = formulate(raw_input, origin, recalled)
    rounds = 0

    while mg.needs_clarification and rounds < MAX_CLARIFY_ROUNDS:
        if origin == "human":
            answers = ask_human(mg.questions)
            mg = formulate(raw_input + "\n" + answers, origin, recalled)
        else:
            # Autonomous: one memory attempt; if still vague → defer
            if recalled:
                mg = formulate(raw_input, origin, recalled)
            if mg.needs_clarification:
                return Deferred(
                    reason="underspecified autonomous goal",
                    questions=mg.questions,
                )
        rounds += 1

    if mg.needs_clarification:
        return Deferred(
            reason=f"clarification cap reached ({MAX_CLARIFY_ROUNDS} rounds)",
            questions=mg.questions,
        )

    # Convention injection
    if not skip_domain_injection:
        mg = enrich_with_conventions(mg, raw_input)

    return mg
