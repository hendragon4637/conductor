"""File 01 — Goal Formulator + Clarifying Loop.

Turns a raw input (possibly vague) into a structured ``MetaGoal``.
If too vague, runs a clarifying loop: ask the human (interactive) or
defer with an "underspecified" flag (autonomous). Never fabricates
specificity for an autonomous goal.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

from backend.planning.meta_planner.llm import call_llm_structured

logger = logging.getLogger(__name__)

MAX_CLARIFY_ROUNDS = 3


class MetaGoal(BaseModel):
    """Structured meta-goal produced by the formulator.

    Maps to plan fields: ``goal`` → plan.goal, raw input → plan.user_intent,
    ``spec`` + ``quality_intent`` flow to decompose + check-gen.
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

Recalled memory context:
{memory}

Origin: {origin}

Now produce the MetaGoal JSON."""



def formulate(
    raw_input: str,
    origin: str = "human",
    recalled: str = "",
) -> MetaGoal:
    """Single LLM call: raw input → MetaGoal.

    Args:
        raw_input: The raw user ask (can be vague).
        origin: ``"human"`` (interactive clarify) or ``"internal_drive"`` (autonomous).
        recalled: Optional memory context from Neo4j/product memory.

    Returns:
        A ``MetaGoal`` parsed from the LLM response.
    """
    prompt = FORMULATE_PROMPT.format(input=raw_input, memory=recalled, origin=origin)
    return call_llm_structured(prompt, schema=MetaGoal)


def run_formulation(
    raw_input: str,
    origin: str = "human",
    ask_human=None,
    recalled: str = "",
) -> MetaGoal | Deferred:
    """Full clarifying loop.

    For human-origin goals: if vague, generates questions and calls
    ``ask_human(questions)`` to get answers, folds them back, re-formulates.
    Cap at ``MAX_CLARIFY_ROUNDS``.

    For autonomous (internal_drive) goals: one memory-resolution attempt.
    If still vague → returns ``Deferred`` (never fabricates specifics).

    Args:
        raw_input: The raw user ask.
        origin: ``"human"`` (interactive) or ``"internal_drive"`` (autonomous).
        ask_human: Callable that takes ``list[str]`` questions and returns
            a single string of answers. Required for interactive mode;
            ignored for autonomous.
        recalled: Optional memory context.

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

    return mg
