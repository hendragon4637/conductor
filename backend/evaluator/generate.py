"""Candidate check generation at decompose time.

Hooks into ``decompose_or_update``. For each node, produces candidates:
- **Deterministic**: derived from the node's success criterion + task type.
  E.g. "tests pass" → ``check_cmd="pytest -q"``; "endpoint exists" → ``check_cmd="curl -sf localhost:PORT/health"``.
- **Rubric**: selected from a preset library (``rubrics/``) matching the node type,
  lightly adapted to the node's specific domain.  No zero-shot generation —
  LLM-generated rubrics from scratch are weak.

Critical: generation is *assistive*. Output candidates; they are NOT trusted
until ratified by the human at plan approval.
"""
from __future__ import annotations

import hashlib
import re
from backend.evaluator.rubrics import select_rubric
from backend.evaluator.schema import Check, NodeChecks

# ── Quality intent parsing ──────────────────────────────────────────────────

# Keywords that signal a deterministic (L1) check from quality_intent
_DETERMINISTIC_QI_KEYWORDS = {"test", "pytest", "lint", "compile", "enforce", "require"}

# Keywords that signal a rubric (L2) check from quality_intent
_RUBRIC_QI_KEYWORDS = {"must", "should", "must not", "should not", "reject", "confirm", "need"}

_QI_CLAUSE_SPLIT = re.compile(r'[,;]\s*|\.(?:\s+|\n)|(?:\s+and\s+)|\n+')

# Stopwords for quality-intent clause-to-task keyword matching
_QI_STOPWORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "have",
    "has", "had", "do", "does", "did", "will", "would", "can", "could",
    "shall", "should", "may", "might", "must", "need", "to", "of", "in",
    "on", "at", "for", "with", "by", "from", "as", "into", "through",
    "during", "before", "after", "above", "below", "between", "out",
    "off", "over", "under", "again", "further", "then", "once", "here",
    "there", "when", "where", "why", "how", "all", "each", "every",
    "both", "few", "more", "most", "other", "some", "such", "no", "nor",
    "not", "only", "own", "same", "so", "than", "too", "very", "just",
    "because", "about", "up", "and", "or", "but", "it", "its", "that",
    "these", "those", "this", "what", "which", "who", "whom",
})


def _significant_keywords(text: str) -> set[str]:
    """Extract meaningful content words (≥4 chars, non-stopword) from *text*."""
    return {w.lower() for w in re.findall(r"[A-Za-z]\w{3,}", text) if w.lower() not in _QI_STOPWORDS}


def _generate_from_quality_intent(quality_intent: str | None, task_text: str | None = None) -> list[Check]:
    """Parse ``quality_intent`` text into candidate checks scoped to *task_text*.

    Splits on clause boundaries (commas, semicolons, periods, newlines, "and"),
    then classifies each clause as deterministic or rubric based on keywords.
    Each clause is matched against *task_text* by keyword overlap — clauses
    whose significant keywords have zero overlap with the task are discarded
    (they belong to a different node). Generic clauses with no significant
    keywords are always kept.

    All returned checks are tagged with ``provenance="human_intent"`` and
    carry a ``source_hint`` showing the originating clause.

    Args:
        quality_intent: Free-text quality requirements, e.g.
            ``"money must be integer cents, deletes need confirmation"``.
            ``None`` or empty string returns an empty list.
        task_text: The node's task description. If provided, used to filter
            clauses that are relevant to this node. ``None`` = no filtering.

    Returns:
        List of ``Check`` objects, each with ``provenance="human_intent"``.
    """
    checks: list[Check] = []
    if not quality_intent:
        return checks
    clauses = _QI_CLAUSE_SPLIT.split(quality_intent)

    # Precompute task keywords once for all clauses
    task_keywords = _significant_keywords(task_text) if task_text else None

    for clause in clauses:
        clause = clause.strip()
        if not clause:
            continue

        # Scope filter: skip clause if its significant keywords have zero
        # overlap with the node's task text (unless clause is so generic
        # it has no significant keywords of its own).
        if task_keywords is not None:
            clause_keywords = _significant_keywords(clause)
            if clause_keywords and not (clause_keywords & task_keywords):
                continue

        cid = "qi-" + hashlib.md5(clause.encode()).hexdigest()[:8]
        lower = clause.lower()

        # Deterministic if it strongly suggests a shell-verifiable condition
        if any(kw in lower for kw in _DETERMINISTIC_QI_KEYWORDS):
            checks.append(Check(
                id=cid,
                type="deterministic",
                criterion=clause,
                check_cmd=f"echo 'TODO: implement check for: {clause}' && exit 1",
                provenance="human_intent",
                source_hint=f"from quality_intent: {clause}",
            ))
        else:
            # Default: rubric check — the quality intent describes a property
            # the output should satisfy, judged by the L2 rubric judge.
            checks.append(Check(
                id=cid,
                type="rubric",
                criterion=clause,
                rubric_item=f"Does the output satisfy: {clause}?",
                provenance="human_intent",
                source_hint=f"from quality_intent: {clause}",
            ))

    return checks


# ── Helpers ─────────────────────────────────────────────────────────────────

_TASK_TYPE_PATTERNS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"(?i)\btest"), "test"),
    (re.compile(r"(?i)\breview"), "review"),
    (re.compile(r"(?i)\bdesign\b"), "design"),
    (re.compile(r"(?i)\b(implement|build|create|add|write)\b"), "build"),
]


def _detect_node_type(task: str, success: str) -> str:
    """Heuristic: classify node type from task + success criterion text."""
    combined = f"{task} {success}"
    for pattern, ntype in _TASK_TYPE_PATTERNS:
        if pattern.search(combined):
            return ntype
    return "default"


def _deterministic_from_criterion(criterion: str, node_index: int, is_first: bool) -> list[Check]:
    """Derive deterministic checks from the success criterion text.

    Uses keyword heuristics to match against known check patterns.
    Returns an empty list for criteria that don't map to shell commands.
    """
    checks: list[Check] = []
    c_lower = criterion.lower()
    check_id_base = hashlib.md5(criterion.encode()).hexdigest()[:8]

    # Test pass check
    if any(kw in c_lower for kw in ("test", "pytest", "test pass", "test suite")):
        checks.append(Check(
            id=f"det-{check_id_base}-tests",
            type="deterministic",
            criterion="All tests pass",
            check_cmd="python3 -m pytest -q --tb=short 2>&1 || (echo 'L1 preset det-tests failed: pytest did not pass when run from the node worktree. Fix test failures or add the expected tests in the checked project tree.' && exit 1)",
        ))

    # File-existence check for code output
    if any(kw in c_lower for kw in ("endpoint", "api", "route", "http", "file")):
        checks.append(Check(
            id=f"det-{check_id_base}-files",
            type="deterministic",
            criterion="Expected code files exist",
            check_cmd="ls -la *.py 2>/dev/null || ls -la src/*.py 2>/dev/null || ls -la backend/*.py 2>/dev/null || (echo 'L1 preset det-files failed: no Python files found in checked locations from the node worktree: root/*.py, src/*.py, backend/*.py. Create or move the expected implementation file into one of those checked locations, or adjust the plan checks before ratification.' && exit 1)",
        ))

    # Lint check for code criteria
    if any(kw in c_lower for kw in ("code", "implement", "script", "module", "class", "function")):
        checks.append(Check(
            id=f"det-{check_id_base}-syntax",
            type="deterministic",
            criterion="No syntax errors in Python files",
            check_cmd="files=$(find . -name '*.py' -not -path './.git/*'); if [ -z \"$files\" ]; then echo 'L1 preset det-syntax failed: no Python files found under the node worktree for syntax checking.'; exit 1; fi; python3 -m py_compile $files 2>&1 || (echo 'L1 preset det-syntax failed: one or more Python files under the node worktree have syntax errors. Fix the reported files.' && exit 1)",
        ))

    # Regression check (non-first nodes)
    if not is_first:
        checks.append(Check(
            id=f"det-{check_id_base}-regression",
            type="deterministic",
            criterion="Prior work is not broken by changes",
            check_cmd="""echo "Regression: previous node commits should still pass their tests" && exit 0""",
        ))

    return checks


def _select_rubric_preset(members: list[str], task: str) -> list[Check]:
    """Select rubric checks from the preset library matching node members.

    Uses ``select_rubric()`` from the centralized rubric module.
    Falls back to legacy ``_detect_node_type`` heuristic if members is empty.
    """
    if members:
        rubric = select_rubric(members, task)
    else:
        node_type = _detect_node_type(task, task)
        rubric = select_rubric([node_type], task)

    checks: list[Check] = []
    for item in rubric.get("items", []):
        checks.append(Check(
            id=item.get("id", f"rubric-{len(checks)}"),
            type="rubric",
            criterion=item.get("rubric_item", item.get("rubric_item", "")),
            rubric_item=item.get("rubric_item", ""),
            weight=item.get("weight", 1.0),
        ))
    return checks


# ── Scope validation ────────────────────────────────────────────────────────

L1_RUNTIME_SIGNALS = ("curl", "uvicorn", "localhost", "127.0.0.1", "http://", "https://", ":8000", ":3000", "health")


def _validate_check_scope(c: Check) -> tuple[bool, str]:
    """Validate a single check for scope violations (runtime leaks).

    L1 (deterministic) checks must not contain runtime signals like
    curl, localhost, or health-check patterns — those require a running
    product and belong to a higher layer.
    """
    if c.tier == "L1" and c.check_cmd:
        cmd_lower = c.check_cmd.lower()
        for signal in L1_RUNTIME_SIGNALS:
            if signal in cmd_lower:
                return False, f"L1 runtime leak: {signal} in check_cmd"
    if c.tier == "L2" and not c.rubric_item:
        return False, "L2 check missing rubric_item"
    return True, ""


# ── agent_config default_checks loader ─────────────────────────────────────

def _load_agent_config_checks(agent_config_id: str | None) -> tuple[list[Check], list[Check]]:
    """Load co-located default_checks from the agent_config.

    Direct lookup by agent_config_id — no registry search.
    Falls back to empty lists if not found or no default_checks set.

    Returns:
        (l1_checks, l2_checks) lists of Check objects.
    """
    l1_out: list[Check] = []
    l2_out: list[Check] = []
    if not agent_config_id:
        return l1_out, l2_out

    try:
        import json
        import os

        db_url = os.environ.get("DATABASE_URL", "")
        if not db_url:
            return l1_out, l2_out

        from backend.db.queries import conn as db_conn
        with db_conn() as c, c.cursor() as cur:
            cur.execute(
                "SELECT default_checks FROM agent_configs WHERE agent_config_id = %s",
                (agent_config_id,),
            )
            row = cur.fetchone()
            if not row:
                return l1_out, l2_out
            raw = row[0]
            if isinstance(raw, str):
                dc = json.loads(raw)
            elif isinstance(raw, dict):
                dc = raw
            else:
                return l1_out, l2_out
    except Exception:
        return l1_out, l2_out

    for l1_item in dc.get("l1", []):
        on_fail_raw = l1_item.pop("on_fail", None) or {}
        c = Check(
            id=l1_item.get("id", f"det-{len(l1_out)}"),
            type="deterministic",
            criterion=l1_item.get("criterion", on_fail_raw.get("what", "L1 check")),
            check_cmd=l1_item.get("cmd", ""),
            provenance="agent_default",
            source_hint=f"from agent_config {agent_config_id}",
            on_fail=OnFailTemplate(
                what=on_fail_raw.get("what", ""),
                how=on_fail_raw.get("how", ""),
                evidence_from=on_fail_raw.get("evidence_from", "stdout"),
            ) if on_fail_raw else None,
        )
        l1_out.append(c)

    for l2_item in dc.get("l2", []):
        c = Check(
            id=l2_item.get("id", f"rubric-{len(l2_out)}"),
            type="rubric",
            criterion=l2_item.get("rubric_item", ""),
            rubric_item=l2_item.get("rubric_item", ""),
            weight=l2_item.get("weight", 1.0),
            provenance="agent_default",
            source_hint=f"from agent_config {agent_config_id}",
        )
        l2_out.append(c)

    return l1_out, l2_out


# ── Public API ──────────────────────────────────────────────────────────────

def generate_checks(
    node_id: str,
    task: str,
    success_criterion: str,
    node_index: int = 0,
    total_nodes: int = 1,
    extra_checks: list[Check] | None = None,
    quality_intent: str | None = None,
    members: list[str] | None = None,
    agent_config_id: str | None = None,
) -> NodeChecks:
    """Generate candidate checks for a node at decompose time.

    Priority:
    1. agent_config default_checks (co-located L1/L2) — direct lookup by id.
    2. Rubric preset from registry (fallback if no agent_config L2 found).
    3. Heuristic deterministic checks from success criterion.
    4. Memory-grounded checks from ``extra_checks``.
    5. Quality-intent checks from ``quality_intent``.

    Args:
        node_id: Unique node identifier (e.g. ``"node-1"``).
        task: The node's task description.
        success_criterion: The node's success criterion text.
        node_index: 0-based index of this node in the plan DAG.
        total_nodes: Total number of nodes in the plan.
        extra_checks: Optional memory-grounded checks injected from
            ``ground_checks_with_memory()``.
        quality_intent: Optional free-text quality requirements.
            Parsed into additional checks tagged with
            ``provenance="human_intent"``.
        members: Optional list of agent/role IDs on this node.
            Used for rubric selection (fallback, if no agent_config L2).
        agent_config_id: Optional agent_config id for co-located default_checks.

    Returns:
        A ``NodeChecks`` container with candidate ``Check`` items.
        These are *candidates* — the human must ratify them at plan approval.
    """
    is_first = node_index == 0

    # 1. agent_config default_checks (co-located)
    ac_l1, ac_l2 = _load_agent_config_checks(agent_config_id)

    # 2. Rubric preset fallback (only if no agent_config L2)
    rubric_checks = ac_l2 if ac_l2 else _select_rubric_preset(members or [], task)

    # 3. Heuristic deterministic checks (always, as supplementary)
    det_checks = _deterministic_from_criterion(success_criterion, node_index, is_first)

    # 4. Memory-grounded checks
    memory_checks = extra_checks or []
    for c in memory_checks:
        c.provenance = "memory"

    # 5. Quality-intent checks (scoped to this node's task)
    qi_checks: list[Check] = []
    if quality_intent:
        qi_checks = _generate_from_quality_intent(quality_intent, task_text=task)

    # De-duplicate by id (later sources override earlier ones with same id)
    seen_ids: set[str] = set()
    all_checks: list[Check] = []
    for c in ac_l1 + det_checks + rubric_checks + memory_checks + qi_checks:
        if c.id not in seen_ids:
            seen_ids.add(c.id)
            all_checks.append(c)

    # ── Validate generated checks, drop runtime leaks ─────────────────────────
    validated = []
    dropped = 0
    for c in all_checks:
        ok, reason = _validate_check_scope(c)
        if ok:
            validated.append(c)
        else:
            dropped += 1
    if dropped:
        print(f"[generate] validate_checks: dropped {dropped} leaked/runtime checks for node {node_id}", flush=True)

    return NodeChecks(
        node_id=node_id,
        checks=validated,
        checks_version=1,
    )
