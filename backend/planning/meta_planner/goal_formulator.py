"""Goal Formulator + Clarifying Loop (multi-component).

Turns a raw input (possibly vague) into a structured ``MetaGoal`` with
one or more ``Component`` entries, each referencing a domain standard.

Multi-component change (2026-07):
- ``MetaGoal`` now carries ``components: list[Component]`` instead of
  a single ``domains[0]``. Backward-compat ``domains`` / ``domain``
  properties provided.
- ``formulate()`` selects ``standard_ids`` from a standards menu (max 3).
- ``build_components()`` assigns deterministic subdirs from ``default_subdir``.
- Conventions reach the executor via ``AGENTS.md`` from scaffolding, not via spec.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, field_validator

from backend.planning.meta_planner.llm import call_llm_structured

from backend.planning.domain_profile import DomainProfile, get_domain_profile, infer_domain, list_domain_names
from backend.standards.loader import list_standard_menu, get_standard

logger = logging.getLogger(__name__)

MAX_CLARIFY_ROUNDS = 3
MAX_COMPONENT_RETRIES = 3


class Component(BaseModel):
    """A named component within a plan, linked to a domain standard.

    Each component maps to exactly one domain_standard entry (via slug)
    and gets a deterministic subdir for scaffold isolation.
    """
    standard_slug: str = Field(description="Slug of the domain_standard this component represents")
    subdir: str = Field(
        default="",
        description="Worktree subdirectory for this component (from default_subdir)",
    )


class MetaGoal(BaseModel):
    """Structured meta-goal produced by the formulator.

    Multi-component (2026-07): carries ``components`` — the list of
    domain-standard-backed parts of the plan. Legacy ``domains`` field
    is still present for backward compatibility with stored plans;
    new code should read ``components`` and use ``domain`` / ``domains``
    as convenience properties.

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
    # --- Multi-component (replaces single domain) ---
    components: list[Component] = Field(
        default_factory=list,
        description="Plan components mapped to domain standards. Max 3.",
    )
    # --- Legacy backward compat ---
    domains: list[str] = Field(
        default_factory=list,
        description="Legacy: product domain(s). Deprecated — use components.",
    )
    applied_conventions: list[str] = Field(
        default_factory=list,
        description="Conventions the formulator injected because the user was silent on them",
    )
    success_seed: str = Field(
        default="",
        description="Seeded success criteria from the domain profile's acceptance block",
    )
    needs_usage_sim: bool = Field(
        default=False,
        description="True when the product has a user-facing surface that warrants L4 persona simulation",
    )
    estimated_node_count: int = Field(
        default=0,
        description="Estimated number of plan nodes needed (1-5). The LLM sets this based on goal scope.",
        ge=0,
    )

    # --- Compatibility properties ---
    @property
    def domain(self) -> str:
        """First component's standard_slug, or '' if empty (legacy compat)."""
        if self.components:
            return self.components[0].standard_slug
        if self.domains:
            return self.domains[0]
        return ""

    @field_validator("estimated_node_count")
    @classmethod
    def clamp_node_count(cls, v: int) -> int:
        return min(v, 5)


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

Select the **standard(s)** from the table below that best match this goal.
Each standard represents a type of deliverable with its own conventions,
layout, and quality gates. You may select 1-3 standards. Prefer FEWER:
a single standard is the default. Select more than one only when the goal
clearly spans multiple distinct deliverable types (e.g. a backend API +
a CLI tool to manage it). Both together are rare — err on the side of one.

Available standards:
{standards}

Output your selections in the ``standard_ids`` field: a list of standard
slugs from the table above. This becomes the plan's ``components`` list.

Estimate the number of plan nodes needed (estimated_node_count). A node
is one unit of work assignable to a single agent. Estimate conservatively:
most goals need 2-5 nodes. The maximum is 5. A positive value is REQUIRED.

Raw input:
{input}

Previous clarification history (if any):
{prior}

Recalled memory context:
{memory}

Origin: {origin}

{system_context}
Now produce the MetaGoal JSON."""


# ── New formulation output schema for multi-component ──────────────


class FormulateOutput(BaseModel):
    """Structured output from the LLM formulation call.

    Contains the same fields as MetaGoal but replaces ``domains`` with
    ``standard_ids`` (list of standard slugs) for the multi-component flow.
    """
    goal: str = Field(description="Normalized one-sentence objective")
    spec: str = Field(description="Constraints/shape/acceptance criteria")
    quality_intent: str = Field(description="How to judge the result — free-text rubric guidance")
    needs_clarification: bool = Field(default=False)
    questions: list[str] = Field(default_factory=list)
    defer_reason: str | None = Field(default=None)
    standard_ids: list[str] = Field(
        default_factory=list,
        description="Standard slugs selected from the menu. 1-3 items, prefer fewer.",
    )
    estimated_node_count: int = Field(default=0, ge=0)


# ── Component validation ─────────────────────────────────────────


def _components_valid(
    standard_ids: list[str],
    valid_slugs: list[str],
) -> list[str]:
    """Check all standard_ids are valid and present. Returns list of issues (empty = valid)."""
    issues: list[str] = []
    if not standard_ids:
        issues.append("standard_ids list is empty — at least one standard must be selected")
        return issues
    if len(standard_ids) > 3:
        issues.append(f"too many standards ({len(standard_ids)}), max 3")
    for sid in standard_ids:
        if sid not in valid_slugs:
            issues.append(f"unknown standard '{sid}' not in the menu")
    return issues


def build_components(standard_ids: list[str]) -> list[Component]:
    """Build Component list from standard slugs with deterministic subdirs."""
    # Fetch default_subdir for each slug
    menu = list_standard_menu()
    slug_to_subdir: dict[str, str] = {m["slug"]: m["default_subdir"] for m in menu}

    components: list[Component] = []
    seen_slugs: set[str] = set()
    for sid in standard_ids:
        if sid in seen_slugs:
            continue
        seen_slugs.add(sid)
        subdir = slug_to_subdir.get(sid, "")
        components.append(Component(standard_slug=sid, subdir=subdir))
    return components


# ── Legacy deserialization ────────────────────────────────────────


def metagoal_from_stored(data: dict[str, Any]) -> MetaGoal:
    """Deserialize a MetaGoal from stored/legacy format.

    Handles both the old format (``domains`` list of strings) and the
    new format (``components`` list of Component dicts). If the old
    format is detected, creates a Component from ``domains[0]`` and
    looks up the standard slug via ``infer_domain()`` fallback naming.
    """
    if "components" in data and data["components"]:
        # Already in new format — parse directly
        return MetaGoal(**data)

    # Legacy format: build components from domains
    mg = MetaGoal(**{k: v for k, v in data.items() if k != "components"})
    domains = data.get("domains", [])
    if domains:
        # Map domain profile name → standard slug
        # Domain profile names (software_app, cli_script...) are different
        # from standard slugs (python-backend, cli-tool...). Infer the
        # standard from the goal text as a best-effort mapping.
        domain_to_standard = {
            "software_app": "react-frontend",
            "api_service": "python-backend",
            "cli_script": "cli-tool",
            "data_pipeline": "python-etl",
            "research_report": "tech-docs",
            "visual_design": "design-layout",
            "gui_app": "python-gui",
            "embedded_firmware": "arduino",
            "generic": "planning",
        }
        for dom in domains:
            slug = domain_to_standard.get(dom, infer_domain(f"{mg.goal} {mg.spec}"))
            # Check it's a real standard
            std = get_standard(slug)
            if std and std["slug"] not in [c.standard_slug for c in mg.components]:
                mg.components.append(Component(standard_slug=slug))
    return mg


# ── System context lookup ─────────────────────────────────────────


def _fetch_system_context(project_id: str) -> str:
    """Query DB for system context if project belongs to a system.

    Returns a formatted string suitable for injection into the LLM prompt,
    or empty string if project has no system_id or DB is unreachable.

    Fields: system name, sibling projects, shared glossary.
    """
    import os
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return ""

    try:
        import psycopg
        from psycopg.rows import dict_row
    except ImportError:
        return ""

    try:
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    """SELECT p.system_id, s.name AS system_name,
                              s.description AS system_description, s.glossary
                       FROM projects p
                       JOIN systems s ON p.system_id = s.system_id
                       WHERE p.project_id = %s""",
                    (project_id,),
                )
                sys_row = cur.fetchone()
                if not sys_row or not sys_row.get("system_id"):
                    return ""

                system_id = sys_row["system_id"]
                parts: list[str] = []
                parts.append(f"System: {sys_row.get('system_name', system_id)}")
                sys_desc = sys_row.get("system_description", "")
                if sys_desc:
                    parts.append(f"  Description: {sys_desc[:200]}")

                # Sibling projects
                cur.execute(
                    """SELECT project_id, name, kind, description
                       FROM projects
                       WHERE system_id = %s AND project_id != %s
                       ORDER BY name""",
                    (system_id, project_id),
                )
                siblings = cur.fetchall()
                if siblings:
                    parts.append("  Sibling projects:")
                    for sib in siblings:
                        label = sib.get("name") or sib["project_id"]
                        kind = sib.get("kind", "component")
                        desc = sib.get("description", "")
                        line = f"    - {label} ({kind})"
                        if desc:
                            line += f": {desc[:150]}"
                        parts.append(line)

                # Glossary from system row
                glossary_raw = sys_row.get("glossary")
                if glossary_raw and isinstance(glossary_raw, dict) and glossary_raw:
                    parts.append("  Shared glossary:")
                    for term, definition in glossary_raw.items():
                        parts.append(f"    {term}: {str(definition)[:200]}")

                return "\n".join(parts)

    except Exception:
        logger.warning("Failed to fetch system context for %s", project_id, exc_info=True)
        return ""


# ── Core formulation ──────────────────────────────────────────────


def formulate(
    raw_input: str,
    origin: str = "human",
    recalled: str = "",
    prior: str = "",
    valid_standards: list[dict[str, Any]] | None = None,
    project_id: str | None = None,
) -> MetaGoal:
    """LLM call: raw input → MetaGoal with standard_ids + component validation.

    The LLM selects 1-3 standard slugs from the menu. If components are
    invalid (unknown slug, empty, >3) or ``estimated_node_count <= 0``,
    retries up to ``MAX_COMPONENT_RETRIES`` (3) times. Falls back to
    legacy ``infer_domain()`` and single-component if all retries exhausted.

    Args:
        raw_input: The raw user ask (can be vague).
        origin: ``"human"`` (interactive clarify) or ``"internal_drive"`` (autonomous).
        recalled: Optional memory context from Neo4j/product memory.
        prior: Optional condensed multi-turn Q&A history.
        valid_standards: List of standard dicts from ``list_standard_menu()``.
            If None, loads from DB.

    Returns:
        A ``MetaGoal`` parsed from the LLM response.
    """
    if valid_standards is None:
        valid_standards = list_standard_menu()

    valid_slugs = [s["slug"] for s in valid_standards]

    # Build standards table for prompt (guide 02.3: standard_id | delivery_form | blurb)
    standards_lines: list[str] = []
    for s in valid_standards:
        blurb = s.get("blurb", "")
        families = s.get("families", [])
        delivery_form = s.get("delivery_form", "")
        families_str = ", ".join(families) if families else ""
        line = f"  {s['slug']} | {delivery_form or 'n/a'}"
        if blurb:
            line += f" | {blurb[:100]}"
        if families_str:
            line += f"  [{families_str}]"
        standards_lines.append(line)

    standards_str = "\n".join(standards_lines)

    # Fetch system context if project belongs to a multi-project system
    sys_context = ""
    if project_id:
        sys_context = _fetch_system_context(project_id)
        if sys_context:
            sys_context = "--- System context ---\n" + sys_context

    prompt = FORMULATE_PROMPT.format(
        input=raw_input,
        prior=prior or "(none)",
        memory=recalled or "(none)",
        origin=origin,
        standards=standards_str,
        system_context=sys_context,
    )

    # Call LLM with the FormulateOutput schema
    fo = call_llm_structured(prompt, schema=FormulateOutput)

    retries = 0
    while retries < MAX_COMPONENT_RETRIES:
        invalid_reasons = _components_valid(fo.standard_ids, valid_slugs)
        if fo.estimated_node_count <= 0:
            invalid_reasons.append(
                f"estimated_node_count={fo.estimated_node_count} must be positive"
            )
        if not invalid_reasons:
            break

        retries += 1
        logger.warning(
            "Formulate invalid (%s) — retry %d/%d",
            "; ".join(invalid_reasons), retries, MAX_COMPONENT_RETRIES,
        )
        retry_prompt = (
            prompt
            + "\n\nIMPORTANT — fix the following issues:\n"
            + "\n".join(f"- {r}" for r in invalid_reasons)
            + "\nstandard_ids MUST be from the menu list (1-3 items, prefer fewer). "
            "estimated_node_count MUST be a positive integer between 1 and 5."
        )
        fo = call_llm_structured(retry_prompt, schema=FormulateOutput)

    # Build MetaGoal from FormulateOutput
    if not fo.standard_ids:
        logger.warning("Standard retry exhausted — falling back to legacy infer_domain()")
        domain = infer_domain(f"{fo.goal} {fo.spec}")
        fallback_std = get_standard(domain)  # probably won't match
        # Best effort: pick first standard from menu
        comps: list[Component] = []
        if valid_slugs:
            comps = [Component(standard_slug=valid_slugs[0])]
        mg = MetaGoal(
            goal=fo.goal, spec=fo.spec, quality_intent=fo.quality_intent,
            needs_clarification=fo.needs_clarification,
            questions=fo.questions, defer_reason=fo.defer_reason,
            origin=origin,
            components=comps,
            estimated_node_count=fo.estimated_node_count,
        )
    else:
        comps = build_components(fo.standard_ids)
        mg = MetaGoal(
            goal=fo.goal, spec=fo.spec, quality_intent=fo.quality_intent,
            needs_clarification=fo.needs_clarification,
            questions=fo.questions, defer_reason=fo.defer_reason,
            origin=origin,
            components=comps,
            estimated_node_count=fo.estimated_node_count,
        )

    if mg.estimated_node_count <= 0:
        mg.estimated_node_count = 2
        logger.warning("Formulate retry exhausted — falling back estimated_node_count=2")

    return mg


# ── Post-formulation enrichment ──────────────────────────────────


def _enrich_meta_goal(meta_goal: MetaGoal) -> MetaGoal:
    """Set needs_usage_sim and success_seed from component standards.

    Convention prose is NOT injected into the spec — it reaches the
    executor via ``AGENTS.md`` from scaffolding (per subdir).
    """
    for comp in meta_goal.components:
        std = get_standard(comp.standard_slug)
        if std is None:
            continue
        families = std.get("families", [])
        if isinstance(families, list) and ("software" in families or "design" in families):
            meta_goal.needs_usage_sim = True

    if meta_goal.components:
        domain_name = meta_goal.components[0].standard_slug
        profile = get_domain_profile(domain_name)
        if profile is None:
            profile = get_domain_profile("generic")
    else:
        profile = get_domain_profile("generic")

    if profile:
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


# ── Runner (clarifying loop) ──────────────────────────────────────


def run_formulation(
    raw_input: str,
    origin: str = "human",
    ask_human: Callable[[list[str]], str] | None = None,
    recalled: str = "",
) -> MetaGoal | Deferred:
    """Full clarifying loop with post-formulation enrichment.

    For human-origin goals: if vague, generates questions and calls
    ``ask_human(questions)`` to get answers, folds them back, re-formulates.
    Cap at ``MAX_CLARIFY_ROUNDS``.

    For autonomous (internal_drive) goals: one memory-resolution attempt.
    If still vague → returns ``Deferred`` (never fabricates specifics).

    After the clarifying loop, runs post-formulation enrichment to set
    ``needs_usage_sim`` and ``success_seed`` from component standards.
    Convention prose is NOT injected into the spec — it reaches the
    executor via ``AGENTS.md`` from scaffolding.
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

    mg = _enrich_meta_goal(mg)

    return mg
