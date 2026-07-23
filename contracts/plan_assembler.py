"""Deterministic .plan/ assembler + PlanDAG Pydantic contract.

Pipeline (called in order by planner-svc on ``node.observed``):
  1. ``assemble_plan(worktree)`` — reads .plan/ files, checks structural integrity
  2. ``validate_assembled(dag_dict, roster)`` — Pydantic PlanDAG + roster membership

All errors are returned verbatim (file-targeted) for retry feedback.
No LLM calls in this module — deterministic only.
"""

from __future__ import annotations

import json
import os
from glob import glob
from typing import Optional

from pydantic import BaseModel, Field, field_validator
from contracts.feedback import AcceptanceCriterion

# ── .plan/ split structure constants ──────────────────────────────────────

INDEX_RELPATH = ".plan/index.json"
NODES_GLOB = ".plan/nodes/*.json"
CHECKS_GLOB = ".plan/checks/*.json"

# ── Pydantic models ───────────────────────────────────────────────────────


class NodeTask(BaseModel):
    text: str
    inputs: list[str] = []
    deliverables: list[str]


class NodeSuccess(BaseModel):
    text: str


class Member(BaseModel):
    agent_config: str
    backend: str
    role: str


class Check(BaseModel):
    """A single evaluation check — L1 (deterministic) or L2 (rubric)."""

    id: str
    tier: str
    kind: str
    weight: float = 1.0
    cmd: Optional[str] = None
    expect: Optional[dict] = None
    rubric_item: Optional[str] = None
    criterion: Optional[str] = None

    @field_validator("tier")
    @classmethod
    def valid_tier(cls, v: str) -> str:
        if v not in ("L1", "L2"):
            raise ValueError(f"tier must be 'L1' or 'L2', got '{v}'")
        return v

    @field_validator("kind")
    @classmethod
    def valid_kind(cls, v: str) -> str:
        if v not in ("deterministic", "rubric"):
            raise ValueError(f"kind must be 'deterministic' or 'rubric', got '{v}'")
        return v


class PlanNode(BaseModel):
    id: str
    capabilities: list[str] = []
    members: list[Member]
    depends_on: list[str] = []
    task: NodeTask
    success: NodeSuccess
    checks: list[Check] = []
    acceptance_criteria: list[AcceptanceCriterion] = Field(
        default_factory=list,
        description="≥1 validated acceptance criterion per node; drives brief, L2 judge, and remediation",
    )

    @field_validator("depends_on")
    @classmethod
    def no_self_dep(cls, v: list[str], info) -> list[str]:
        values = info.data
        nid = values.get("id", "")
        if nid in v:
            raise ValueError(f"node {nid} depends_on itself")
        return v


class PlanDAG(BaseModel):
    goal: str
    spec: str
    quality_intent: str
    nodes: list[PlanNode]

    @field_validator("nodes")
    @classmethod
    def checks(cls, v: list[PlanNode]) -> list[PlanNode]:
        errs: list[str] = []
        ids = [n.id for n in v]
        if len(ids) != len(set(ids)):
            dupes = {i for i in ids if ids.count(i) > 1}
            errs.append(f"duplicate node ids: {sorted(dupes)}")
        idset = set(ids)
        for n in v:
            for d in n.depends_on:
                if d not in idset:
                    errs.append(
                        f"unresolved dep '{d}' in node '{n.id}'"
                    )
            if not n.task.deliverables:
                errs.append(f"node '{n.id}' missing deliverables")
        if errs:
            raise ValueError("; ".join(errs))
        _assert_acyclic(v)
        return v


# ── Deterministic helpers ─────────────────────────────────────────────────


def _assert_acyclic(nodes: list[PlanNode]) -> None:
    """DFS-based cycle detection. Raises ValueError if a cycle is found."""
    adj: dict[str, list[str]] = {n.id: list(n.depends_on) for n in nodes}
    visited: set[str] = set()
    stack: set[str] = set()

    def _dfs(nid: str) -> None:
        if nid in stack:
            raise ValueError(f"cycle detected involving node '{nid}'")
        if nid in visited:
            return
        visited.add(nid)
        stack.add(nid)
        for dep in adj.get(nid, []):
            _dfs(dep)
        stack.remove(nid)

    for nid in adj:
        if nid not in visited:
            _dfs(nid)


def _read_json(path: str) -> Optional[dict]:
    """Safely read a JSON file; returns None on failure with details."""
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        return None


# ── Assembler — deterministic .plan/ → dict ────────────────────────────────


def assemble_plan(worktree: str) -> tuple[Optional[dict], list[str]]:
    """Read .plan/ files from ``worktree`` and reconstruct the DAG dict.

    Args:
        worktree: Absolute path to the planning worktree root.

    Returns:
        ``(dag_dict, errors)`` where ``dag_dict`` is None on any error
        and ``errors`` is a list of file-targeted error messages.
    """
    errors: list[str] = []

    # 1. Read index
    idx_path = os.path.join(worktree, INDEX_RELPATH)
    idx = _read_json(idx_path)
    if idx is None:
        return None, [f"missing/malformed {INDEX_RELPATH}"]

    # 2. Validate required index fields
    for key in ("goal", "spec", "quality_intent", "nodes"):
        if key not in idx:
            errors.append(f"{INDEX_RELPATH}: missing required field '{key}'")

    if errors:
        return None, errors

    # 3. Build file→id mapping from index
    index_nodes = idx["nodes"]
    listed: dict[str, str] = {}  # nid → filename
    for entry in index_nodes:
        nid = entry.get("id")
        fname = entry.get("file")
        if not nid:
            errors.append(f"{INDEX_RELPATH}: node entry missing 'id'")
            continue
        if not fname:
            errors.append(f"{INDEX_RELPATH}: node '{nid}' missing 'file'")
            continue
        listed[nid] = fname

    if errors:
        return None, errors

    # 4. Collect actual node files on disk
    node_glob = os.path.join(worktree, NODES_GLOB)
    actual_files: set[str] = {os.path.basename(p) for p in glob(node_glob)}

    # 5. Check for orphan files (on disk but not in index)
    expected_files = set(listed.values())
    orphans = actual_files - expected_files
    for of in sorted(orphans):
        errors.append(f"orphan file .plan/nodes/{of} not in index")

    # 5b. Check for orphan check files (checks exist but no matching node)
    check_glob = os.path.join(worktree, CHECKS_GLOB)
    actual_checks: set[str] = {os.path.basename(p) for p in glob(check_glob)}
    orphan_checks = actual_checks - expected_files
    for oc in sorted(orphan_checks):
        errors.append(f"orphan check file .plan/checks/{oc} (no matching node)")

    if errors:
        return None, errors

    # 6. Read each listed node file + its checks
    nodes: list[dict] = []
    for nid, fname in listed.items():
        node_path = os.path.join(worktree, ".plan", "nodes", fname)
        if not os.path.exists(node_path):
            errors.append(f"missing file .plan/nodes/{fname} (index lists '{nid}')")
            continue
        node = _read_json(node_path)
        if node is None:
            errors.append(f"malformed JSON in .plan/nodes/{fname}")
            continue
        if node.get("id") != nid:
            errors.append(
                f".plan/nodes/{fname}: id '{node.get('id')}' != index '{nid}'"
            )
            continue

        # Read corresponding checks file
        check_path = os.path.join(worktree, ".plan", "checks", fname)
        checks = _read_json(check_path)
        if checks is None:
            errors.append(f"missing/malformed .plan/checks/{fname} for node '{nid}'")
            continue
        node["checks"] = checks
        nodes.append(node)

    if errors:
        return None, errors

    return {
        "goal": idx["goal"],
        "spec": idx["spec"],
        "quality_intent": idx["quality_intent"],
        "nodes": nodes,
    }, []


# ── Pydantic + roster validation ──────────────────────────────────────────


def validate_assembled(
    dag_dict: dict,
    roster: list[str],
) -> tuple[Optional[PlanDAG], list[str]]:
    """Validate an assembled DAG dict through Pydantic + roster check.

    Args:
        dag_dict: Output of ``assemble_plan()`` (full DAG dict).
        roster: List of allowed ``agent_config`` IDs (e.g. from capability slate).

    Returns:
        ``(dag, errors)``. On success ``dag`` is a valid ``PlanDAG``.
        On failure ``dag`` is None and ``errors`` contains verbatim messages.
    """
    errors: list[str] = []

    # Pydantic model_validate (covers schema, fields, value-level rules)
    try:
        dag = PlanDAG.model_validate(dag_dict)
    except Exception as e:
        return None, [str(e)]

    # Roster check: every member's agent_config must be in the allowed roster
    for n in dag.nodes:
        for m in n.members:
            if m.agent_config not in roster:
                errors.append(
                    f"node '{n.id}': member '{m.agent_config}' not in roster"
                )

    if errors:
        return None, errors

    return dag, []


# ── Check boundary validation ────────────────────────────────────────────


def validate_check_boundaries(
    dag_nodes: list[dict],
    standard_bearing: bool = False,
) -> list[str]:
    """Deterministic check boundary validation on assembled nodes.

    - Appends ``run_md_present`` L1 check to every node that lacks it
      (mutates the list in place).
    - Appends ``gates_green`` L1 check to every node when the workspace
      is standard-bearing (has an AGENTS.md with verification gates).
    - Validates L1 checks have ``cmd`` and L2 checks have ``rubric_item``
      or ``criterion``.

    Args:
        dag_nodes: List of assembled node dicts (mutated in place).
        standard_bearing: When True, the workspace has an AGENTS.md standard
            with verification gates. The ``gates_green`` L1 check is appended
            to every node that lacks it.

    Returns a list of error messages (empty = all valid).
    """
    errors: list[str] = []

    RUN_MD_CHECK = {
        "id": "run_md_present",
        "tier": "L1",
        "kind": "deterministic",
        "cmd": "test -f RUN.md",
        "criterion": "RUN.md exists in the worktree root",
    }

    GATES_GREEN_CHECK = {
        "id": "gates_green",
        "tier": "L1",
        "kind": "deterministic",
        "cmd": "bash gates.sh",
        "criterion": "All verification gates pass; see AGENTS.md for gate definitions",
    }

    for node in dag_nodes:
        nid = node.get("id", "?")
        checks: list[dict] = node.get("checks") or []

        # Append run_md_present if missing
        has_run_md = any(c.get("id") == "run_md_present" for c in checks)
        if not has_run_md:
            checks.append(dict(RUN_MD_CHECK))
            node["checks"] = checks

        if standard_bearing:
            has_gates_green = any(c.get("id") == "gates_green" for c in checks)
            if not has_gates_green:
                checks.append(dict(GATES_GREEN_CHECK))
                node["checks"] = checks

        # Structural validation per check
        for ci, c in enumerate(checks):
            tid = c.get("id", f"check-{ci}")
            tier = c.get("tier", "")
            kind = c.get("kind", "")

            if tier == "L1" and kind == "deterministic":
                if not c.get("cmd"):
                    errors.append(
                        f"node '{nid}', check '{tid}': L1 deterministic check missing 'cmd'"
                    )
            elif tier == "L2" and kind == "rubric":
                if not c.get("rubric_item") and not c.get("criterion"):
                    errors.append(
                        f"node '{nid}', check '{tid}': L2 rubric check missing 'rubric_item' or 'criterion'"
                    )
            else:
                errors.append(
                    f"node '{nid}', check '{tid}': tier/kind mismatch "
                    f"(tier={tier}, kind={kind})"
                )

    return errors


# ── Structured ✓/FIX feedback for remediation ─────────────────────────────

_VALID_TIERS = {"L1", "L2"}
_VALID_KINDS = {"deterministic", "rubric"}


def render_deterministic_feedback(worktree: str) -> str:
    """Build a structured ✓/FIX block from deterministic .plan/ validation.

    Pure file-system introspection — zero LLM. Returns a string with two
    sections: CORRECT (files the agent must NOT touch) and FIX THESE
    (actionable per-file problems with exact verbs: CREATE, FIX, DELETE, ADD).

    Used by the remediation retry brief so the agent edits only the
    offending files.
    """
    import json as _json
    from glob import glob as _glob

    ok: list[str] = []
    bad: list[str] = []

    # 1. Index file
    idx_path = os.path.join(worktree, INDEX_RELPATH)
    idx = _read_json(idx_path)
    if idx is None:
        return (
            "- MISSING: .plan/index.json — CREATE it first (STEP 1), "
            "then nodes (STEP 2), then checks (STEP 3)."
        )
    idx_desc_ok = True
    for n in idx.get("nodes", []):
        nid = n.get("id", "?")
        desc = n.get("description", "")
        if not desc or not isinstance(desc, str) or not desc.strip():
            bad.append(
                f"- INDEX INCOMPLETE: .plan/index.json node '{nid}' "
                f"has empty description. Add a brief description."
            )
            idx_desc_ok = False
    if idx_desc_ok:
        ok.append("index.json ✓")

    # 2. Per-node validation
    for n in idx.get("nodes", []):
        nid = n.get("id", "?")
        fname = n.get("file", f"{nid}.json")
        nf = f".plan/nodes/{fname}"
        cf = f".plan/checks/{fname}"

        # Node file
        node_path = os.path.join(worktree, ".plan", "nodes", fname)
        if not os.path.exists(node_path):
            bad.append(f"- MISSING: {nf} (index lists id '{nid}'). CREATE it (STEP 2).")
        else:
            node = _read_json(node_path)
            if node is None:
                bad.append(f"- MALFORMED: {nf} invalid JSON. FIX the syntax.")
            elif node.get("id") != nid:
                bad.append(
                    f"- ID MISMATCH: {nf} has id '{node.get('id')}' "
                    f"≠ index '{nid}'. FIX the id field."
                )
            else:
                idx_deps = n.get("depends_on") or []
                node_deps = node.get("depends_on") or []
                if idx_deps != node_deps:
                    bad.append(
                        f"- DEPENDENCY MISMATCH: {nf} depends_on={node_deps} "
                        f"≠ index depends_on={idx_deps}. "
                        f"Make them identical in both places."
                    )

                nbad: list[str] = []
                caps = node.get("capabilities")
                if not caps or not isinstance(caps, list) or len(caps) == 0:
                    nbad.append(
                        f"- CONTENT INCOMPLETE: {nf} `capabilities` is empty. "
                        f"Populate with capability names from the ROSTER."
                    )
                members = node.get("members")
                if not members or not isinstance(members, list) or len(members) == 0:
                    nbad.append(
                        f"- CONTENT INCOMPLETE: {nf} `members` is empty. "
                        f"Assign agent_config_ids from the ROSTER."
                    )
                task = node.get("task") or {}
                dels = task.get("deliverables") if isinstance(task, dict) else None
                if not dels or not isinstance(dels, list) or len(dels) == 0:
                    nbad.append(
                        f"- CONTENT INCOMPLETE: {nf} `task.deliverables` is empty. "
                        f"List concrete file paths or artifacts this node produces."
                    )
                task_text = task.get("text") if isinstance(task, dict) else None
                if not task_text or not isinstance(task_text, str) or not task_text.strip():
                    nbad.append(
                        f"- CONTENT INCOMPLETE: {nf} `task.text` is empty. "
                        f"Describe what work this node performs."
                    )
                if nbad:
                    bad.extend(nbad)
                else:
                    ok.append(f"{nf} ✓")

        # Checks file
        check_path = os.path.join(worktree, ".plan", "checks", fname)
        if not os.path.exists(check_path):
            bad.append(
                f"- MISSING CHECKS: {cf}. CREATE it (STEP 3) "
                f"from the node's capability dims."
            )
            continue

        checks_raw = _read_json(check_path)
        if checks_raw is None:
            bad.append(f"- MALFORMED: {cf} invalid JSON. FIX it.")
            continue

        # Validate checks content
        cbad: list[str] = []
        if not isinstance(checks_raw, list):
            cbad.append(f"- CHECKS STRUCTURE: {cf} must be a JSON array. FIX it.")
        else:
            if not any(c.get("id") == "run_md_present" for c in checks_raw):
                cbad.append(
                    f"- CHECKS INCOMPLETE: {cf} missing mandatory "
                    f"run_md_present L1 check. ADD it."
                )
            tiers = {c.get("tier") for c in checks_raw}
            if "L1" not in tiers:
                cbad.append(
                    f"- CHECKS INCOMPLETE: {cf} has no L1 checks. ADD from objective dims."
                )
            if "L2" not in tiers:
                cbad.append(
                    f"- CHECKS INCOMPLETE: {cf} has no L2 rubric items. "
                    f"ADD from subjective dims."
                )
            bad_tiers = [
                c.get("id") for c in checks_raw
                if c.get("tier") not in _VALID_TIERS
            ]
            if bad_tiers:
                cbad.append(
                    f"- INVALID TIER: {cf} items {bad_tiers} have "
                    f"tier ∉ (L1, L2). FIX tier."
                )
            bad_kinds = [
                c.get("id") for c in checks_raw
                if c.get("kind") not in _VALID_KINDS
            ]
            if bad_kinds:
                cbad.append(
                    f"- INVALID KIND: {cf} items {bad_kinds} have "
                    f"kind ∉ (deterministic, rubric). FIX kind."
                )
        bad.extend(cbad)
        if not cbad:
            ok.append(f"{cf} ✓")

    # 3. Orphan detection
    listed = {n.get("file", f"node-{i}.json") for i, n in enumerate(idx.get("nodes", []))}
    for subdir in ("nodes", "checks"):
        pattern = os.path.join(worktree, ".plan", subdir, "*.json")
        for fp in _glob(pattern):
            fname = os.path.basename(fp)
            if fname not in listed:
                bad.append(
                    f"- ORPHAN: .plan/{subdir}/{fname} not in index. "
                    f"ADD to index or DELETE the file."
                )

    # 4. Assemble output
    ok_lines = "\n".join(f"  {o}" for o in ok) if ok else "  (none)"
    fix_lines = "\n".join(bad) if bad else "  (structure OK — no deterministic errors)"
    return (
        "CORRECT (do NOT modify):\n"
        f"{ok_lines}\n"
        "\n"
        "FIX THESE (only these):\n"
        f"{fix_lines}"
    )


# ── JSON schema helpers for the brief ─────────────────────────────────────


def per_node_json_schema() -> dict:
    """Return the JSON schema for a single PlanNode — used in the brief."""
    return PlanNode.model_json_schema()


def index_json_schema() -> dict:
    """Return the expected structure of .plan/index.json as a JSON schema."""

    # Inline schema because index.json is not a Pydantic model
    return {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "type": "object",
        "required": ["goal", "spec", "quality_intent", "nodes"],
        "properties": {
            "goal": {"type": "string", "description": "High-level goal statement"},
            "spec": {"type": "string", "description": "Detailed specification"},
            "quality_intent": {
                "type": "string",
                "description": "Quality expectations",
            },
            "nodes": {
                "type": "array",
                "description": "Skeleton list of plan nodes",
                "items": {
                    "type": "object",
                    "required": ["id", "file", "depends_on"],
                    "properties": {
                        "id": {
                            "type": "string",
                            "description": "Unique node id (e.g. node-001)",
                        },
                        "file": {
                            "type": "string",
                            "description": "Filename in .plan/nodes/ (e.g. node-001.json)",
                        },
                        "depends_on": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "List of node ids this node depends on",
                        },
                        "description": {
                            "type": "string",
                            "description": "Brief description of what this node does",
                        },
                    },
                },
            },
        },
    }


def check_json_schema() -> dict:
    """Return the JSON schema for a single Check — used in the brief."""
    return Check.model_json_schema()
