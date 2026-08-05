"""L2 rubric judge — single structured LLM call per node via LiteLLM JUDGE gateway.

All rubric items for a node are judged in ONE ``call_llm_structured`` call
(``role="l2_judge"``), replacing the previous per-item deepeval GEval loop.
This lowers judge latency from N*C calls (items x chunks) to at most C calls
(one per artifact chunk), and removes the requeue machinery.

Option A semantics: any rubric item missing from the judge's response is
treated as an invalid judge — the item is hard-failed (``criteria_met=False,
score=0.0, explanation="missing from L2 response"``). Never requeued.

Backward compat: if ``llm_call`` is passed to ``run_l2()``, the legacy
raw-LLM code path is used (for testing with mocks).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time as _time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from pydantic import BaseModel, Field

from backend.evaluator.schema import Check, Judgment
from backend.planning.meta_planner.llm import call_llm_structured
from contracts.feedback import validate_feedback

# ── Judge model config ──────────────────────────────────────────────────────
JUDGE_TIMEOUT = 300.0

# L2 input-size — oversized artifacts trigger a flag-fail instead of truncation
L2_MAX_CHARS = int(os.environ.get("L2_MAX_INPUT_CHARS", "24000"))

# Chunked evaluation: artifacts above this size are split into overlapping chunks
L2_ARTIFACT_MAX_CHUNK_SIZE = int(os.environ.get("L2_ARTIFACT_MAX_CHUNK_SIZE", "200000"))
L2_ARTIFACT_CHUNK_OVERLAP = int(os.environ.get("L2_ARTIFACT_CHUNK_OVERLAP", "20000"))

GEVAL_THRESHOLD = 0.5
"""Score threshold for per-item criteria_met conversion."""

from contracts.paths import INFRA_EXCLUDES, INFRA_SKIP_PARTS, is_infra

ARTIFACT_SKIP_PARTS = INFRA_SKIP_PARTS | {".git", "AGENTS.md"}
ARTIFACT_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".dylib", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz"}

# ── Structured feedback contract (folded into the judge prompt) ───────────

FEEDBACK_CONTRACT = (
    'In your reason, output STRICT JSON only: {"what": "which specific requirement failed or passed", '
    '"where": "file:function or exact path in the artifact", '
    '"why": "root cause in one sentence", '
    '"how": "the concrete change that would satisfy this criterion"}. '
    'Quote actual file paths and code identifiers FROM THE ARTIFACT — never generic phrases.'
)

# Serialisable 0-10 anchor scale used for DB round-trip and prompt rendering
L2_RUBRIC_ANCHORS_SERIAL = [
    {"score_range": [0, 2], "expected_outcome": "deliverable missing or core behavior absent"},
    {"score_range": [3, 5], "expected_outcome": "deliverable exists but the criterion's core behavior is wrong"},
    {"score_range": [6, 8], "expected_outcome": "criterion met for the main path; edge cases unhandled"},
    {"score_range": [9, 10], "expected_outcome": "criterion fully met incl. edge cases"},
]


# ── Rubric config from judge_rubrics table ────────────────────────────────

def load_rubric_config(capability: str) -> dict | None:
    """Load the active rubric config for a capability from judge_rubrics.

    Returns the dims dict (with ``anchors``, ``feedback_contract``, ``bundles``,
    ``dimensions``) or None if no active rubric exists.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    try:
        import psycopg
        from psycopg.rows import dict_row
        with psycopg.connect(url, row_factory=dict_row) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT dims FROM judge_rubrics WHERE capability = %s AND active = TRUE LIMIT 1",
                    (capability,),
                )
                row = cur.fetchone()
                return dict(row["dims"]) if row else None
    except Exception:
        return None


def _get_feedback_contract(dims: dict | None) -> str:
    if dims and dims.get("feedback_contract"):
        return dims["feedback_contract"]
    return FEEDBACK_CONTRACT


def _get_dim_config(dims: dict | None, dim_id: str) -> dict | None:
    if not dims:
        return None
    for d in dims.get("dimensions", []):
        if d.get("id") == dim_id:
            return d
    return None


# ── Chunked artifact evaluation ─────────────────────────────────────────────

def _chunk_artifact(text: str, max_size: int = L2_ARTIFACT_MAX_CHUNK_SIZE,
                    overlap: int = L2_ARTIFACT_CHUNK_OVERLAP) -> list[str]:
    """Split artifact into N overlapping chunks capped at max_size chars.

    Returns [text] when text fits in one chunk. Overlap ensures code spanning
    adjacent chunks is visible in both. Split on newline boundaries.
    """
    n = len(text)
    if n <= max_size:
        return [text]

    stride = max_size - overlap
    if stride <= 0:
        stride = max_size

    chunks: list[str] = []
    pos = 0
    while pos < n:
        chunk_end = min(pos + max_size, n)
        if chunk_end < n:
            nl = text.rfind('\n', pos, chunk_end)
            if nl > pos + max_size // 2:
                chunk_end = nl + 1
        chunks.append(text[pos:chunk_end])
        if chunk_end >= n:
            break
        pos += stride

    return chunks


class NeedsRequeueError(Exception):
    """Raised when all retries for all chunks are exhausted for a rubric item
    and no valid score (>0) was produced. The caller should save partial
    judgments and re-queue the node for later evaluation."""
    pass


# ── Legacy prompt templates (kept for ``llm_call`` backward compat) ─────────

JUDGE_SYSTEM_PROMPT = """You are a strict, impartial quality judge.

You will receive:
  1. A rubric item (a yes/no quality question).
  2. An artifact (git diff, file contents, and any test output).

Answer the rubric item with a structured response.

Respond ONLY with a single JSON object exactly matching this shape:
{
  "criteria_met": true or false,
  "explanation": "one short sentence explaining why"
}

Do not add commentary outside the JSON."""

JUDGE_USER_PROMPT = """Rubric item: {rubric_item}

Artifact:
{artifact}

Respond as {{"criteria_met": true/false, "explanation": "..."}}"""


class JudgeUnavailableError(RuntimeError):
    """Raised when ALL configured judge models are unreachable.

    Never caught silently — the gate MUST surface this to the UI
    (``node_sessions.judge_error``) rather than silently passing the node.
    """
    pass


# ── Results ──────────────────────────────────────────────────────────────────

@dataclass
class L2Result:
    score: float = 0.0
    judgments: list[Judgment] = field(default_factory=list)
    """Per-rubric judgments returned by the judge."""
    rubric_count: int = 0
    """Total number of rubric items evaluated."""
    items_met: int = 0
    """Number of rubric items that met criteria."""
    oversize: bool = False
    """True when artifact exceeds L2_MAX_CHARS — flag-fail, not truncated."""
    partial: bool = False
    """True when evaluation was interrupted by retry exhaustion and needs re-queue.
    Partial judgments for completed items are in ``judgments``."""
    best_chunk_idx: int = 0
    """Chunk index that most recently passed a rubric item.
    Used on re-delivery to try the best-known chunk first."""
    raw_response: str | None = None
    """Raw (pre-parse) LLM response text, for observability."""


# ── Artifact collection ──────────────────────────────────────────────────────

def _artifact_skip_path(path: str) -> bool:
    p = Path(path)
    if any(part in ARTIFACT_SKIP_PARTS for part in p.parts):
        return True
    if p.suffix.lower() in ARTIFACT_SKIP_SUFFIXES:
        return True
    if any(part.endswith(".egg-info") for part in p.parts):
        return True
    return False


def _artifact_priority(path: str) -> tuple[int, str]:
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".py", ".js", ".ts", ".tsx", ".jsx"}:
        return (0, path)
    if suffix in {".html", ".css"}:
        return (1, path)
    if suffix in {".md", ".txt", ".json", ".yaml", ".yml", ".toml", ".ini", ".env", ".example"}:
        return (2, path)
    return (3, path)


def _read_artifact_text(path: Path, limit: int = 3000) -> str | None:
    try:
        if not path.is_file() or path.stat().st_size > 100_000:
            return None
        raw = path.read_bytes()[:limit]
        if b"\x00" in raw:
            return None
        return raw.decode("utf-8", errors="replace")
    except Exception:
        return None


def _collect_changed_files(worktree: str) -> list[str]:
    """Return sorted, deduplicated, infra-filtered list of changed files.

    Combines uncommitted working-tree changes (``git diff --name-only``)
    and the last-commit diff (``git diff HEAD~1..HEAD --name-only``) so
    that both in-progress edits and recently-committed work are visible.
    Paths matching ``is_infra()`` are excluded.
    """
    changed: set[str] = set()
    for cmd in (
        ["git", "diff", "--name-only"],
        ["git", "diff", "HEAD~1..HEAD", "--name-only"],
    ):
        try:
            r = subprocess.run(cmd, cwd=worktree, capture_output=True, text=True, timeout=15)
            for line in r.stdout.strip().splitlines():
                line = line.strip()
                if line and not is_infra(line):
                    changed.add(line)
        except Exception:
            pass
    return sorted(changed)


def _build_repomix_snapshot(worktree: str, node_context: dict | None = None,
                           extra_ignore: list[str] | None = None,
                           extra_include_suffixes: list[str] | None = None,
                           changed_files: list[str] | None = None,
                           deliverables_only: bool = False) -> str:
    """Generate a repomix snapshot.

    When ``changed_files`` is non-empty (the common case), the snapshot
    includes full content for those files + manifests + criterion
    ``where`` paths + deliverables. When empty (e.g. first run with no
    commits yet), falls back to auto-detected source directories
    (``src/``, ``app/``, ``lib/``).

    When ``deliverables_only`` is True (L2 evaluation), changed_files is
    ignored and only deliverables + manifests + tree are included. This
    produces a smaller, focused artifact matching the design constraint
    that rubric items must be answerable from the repomix text snapshot.

    Always includes a compressed tree listing (~2KB) for overall
    project structure context. Falls back gracefully if repomix CLI
    is unavailable.

    ``extra_ignore`` merges bundle-level exclude patterns (e.g., "node_modules")
    into the repomix ``--ignore`` list. ``extra_include_suffixes`` adds suffix-
    based include globs (e.g., ``**.py``, ``**.ts``) to ``--include``.
    """
    # ── Build include list: manifests + criteria paths always included ──
    include_paths: list[str] = ["pyproject.toml", "package.json", "requirements.txt", "RUN.md"]
    if node_context:
        for ac in (node_context.get("acceptance_criteria") or []):
            include_paths.extend(ac.get("where", []))
        for d in (node_context.get("deliverables") or node_context.get("task", {}).get("deliverables", [])):
            if d not in include_paths:
                include_paths.append(d)
    include_paths = list(dict.fromkeys(include_paths))

    # ── File selection ────────────────────────────────────────────────
    wt_path = Path(worktree)

    if deliverables_only:
        # L2 mode: only deliverables + manifests + tree, no changed files
        pass
    elif changed_files:
        # Full mode: include changed files from git diff
        for f in changed_files:
            candidate = f.removeprefix("./")
            if _artifact_skip_path(candidate):
                continue
            if candidate not in include_paths and (wt_path / candidate).is_file():
                include_paths.append(candidate)
    else:
        # Fallback: auto-detect source directories for fresh worktrees
        for src_dir in ("src", "app", "lib"):
            candidate = f"{src_dir}/**"
            if (wt_path / src_dir).is_dir() and candidate not in include_paths:
                include_paths.append(candidate)

    # ── Merge ignore patterns ─────────────────────────────────────────
    ignores = list(INFRA_EXCLUDES)
    if extra_ignore:
        for p in extra_ignore:
            if p not in ignores:
                ignores.append(p)
    ignore_str = ",".join(ignores)

    # ── Suffix-based include globs from bundle_rules ──────────────────
    if extra_include_suffixes:
        for suffix in extra_include_suffixes:
            glob = f"**{suffix}"
            if glob not in include_paths:
                include_paths.append(glob)
    include_str = ",".join(include_paths)
    snapshot_path = os.path.join(worktree, ".conductor", "snapshot.md")
    os.makedirs(os.path.dirname(snapshot_path), exist_ok=True)

    try:
        subprocess.run(
            ["repomix", "--include", include_str, "--ignore", ignore_str,
             "--no-gitignore", "--style", "markdown", "--output", snapshot_path, worktree],
            capture_output=True, text=True, timeout=60,
        )
        if os.path.isfile(snapshot_path):
            with open(snapshot_path) as f:
                snapshot = f.read()
        else:
            snapshot = ""
    except Exception:
        snapshot = ""

    try:
        result = subprocess.run(
            ["repomix", "--include", "**", "--ignore", ignore_str,
             "--no-gitignore", "--style", "plain", "--output", "-", worktree],
            capture_output=True, text=True, timeout=60,
        )
        tree = result.stdout.strip()[:2000]
    except Exception:
        tree = ""

    parts: list[str] = []
    if snapshot:
        parts.append("[REPOMIX SNAPSHOT — contract paths + manifests (full content)]")
        parts.append(snapshot)
    if tree:
        parts.append("[REPO TREE — bounded]")
        parts.append(tree)
    return "\n".join(parts)


def collect_deliverables_artifact(worktree: str,
                                   node_context: dict | None = None,
                                   bundle_rules: dict | None = None) -> str:
    """Collect deliverables-only artifact — no git diff or untracked files.

    Only the repomix snapshot targeting deliverables + manifests + tree.
    This is smaller and more focused for L2 rubric evaluation, matching
    the design constraint that rubric items must be answerable from the
    repomix text snapshot alone.
    """
    extra_ignore = (bundle_rules or {}).get("exclude_parts")
    extra_include_suffixes = (bundle_rules or {}).get("include_suffixes")
    snapshot = _build_repomix_snapshot(
        worktree, node_context,
        extra_ignore=extra_ignore,
        extra_include_suffixes=extra_include_suffixes,
        changed_files=None,
        deliverables_only=True,
    )
    if not snapshot:
        return ""
    return snapshot


def collect_artifact(worktree: str, max_chars: int = L2_MAX_CHARS,
                     node_context: dict | None = None,
                     bundle_rules: dict | None = None) -> str:
    """Collect evidence from the worktree for the judge to evaluate.

    Captures working-tree diff, last-commit diff (for committed executor
    results), tracked file listing, file contents, untracked files, and
    a bounded repomix snapshot ("what exists" alongside "what changed").

    ``bundle_rules`` (from the active rubric's ``bundles`` config) controls
    which file suffixes to prioritise (``include_suffixes``) and which path
    parts to skip (``exclude_parts``) when building the repomix snapshot.
    """
    parts: list[str] = []

    try:
        result = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        diff = result.stdout.strip()
        if diff:
            parts.append("[Git diff working tree]")
            parts.append(diff)
    except Exception:
        parts.append("[Git diff: unavailable]")

    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=worktree,
            capture_output=True, text=True, timeout=15,
        )
        untracked = result.stdout.strip()
        if untracked:
            all_lines = [f for f in untracked.splitlines() if f.strip()]
            excluded = [f for f in all_lines if _artifact_skip_path(f)]
            if excluded:
                print(f"[ARTIFACT] excluded {len(excluded)} files (skip path): {excluded[:5]}...", flush=True)
            included = [f for f in all_lines if not _artifact_skip_path(f)]
            parts.append("[New files]")
            lines = sorted(included, key=_artifact_priority)[:40]
            if len(included) > 40:
                print(f"[ARTIFACT] untracked overflow: {len(included)} files, showing first 40 (sorted by priority, alphabetical)", flush=True)
            for f in lines:
                fpath = Path(worktree) / f
                content = _read_artifact_text(fpath)
                if content is not None:
                    parts.append(f"--- {f} ---")
                    parts.append(content)
    except Exception:
        pass

    # Append repomix snapshot targeting changed files (+ tree for structure)
    extra_ignore = (bundle_rules or {}).get("exclude_parts")
    extra_include_suffixes = (bundle_rules or {}).get("include_suffixes")
    changed = _collect_changed_files(worktree)
    snapshot = _build_repomix_snapshot(worktree, node_context,
                                       extra_ignore=extra_ignore,
                                       extra_include_suffixes=extra_include_suffixes,
                                       changed_files=changed)
    if snapshot:
        parts.append("")
        parts.append(snapshot)

    full = "\n".join(parts)
    return full


# ── Judge model call ─────────────────────────────────────────────────────────

def _default_judge_llm(prompt: str) -> str:
    """Call the judge model through the LiteLLM gateway."""
    from backend.llm.gateway import call as gateway_call

    print(f"[L2] LLM request via gateway: role=l2_judge prompt_preview={prompt[:300]}", flush=True)

    try:
        result = gateway_call("l2_judge", [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], temperature=0.0, max_tokens=16386, timeout=JUDGE_TIMEOUT)
    except Exception as exc:
        raise JudgeUnavailableError(
            f"Judge model unavailable via LiteLLM gateway: {exc}"
        ) from exc

    msg = result["choices"][0]["message"]
    raw = (msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
    if raw.startswith("```"):
        raw = raw.split("\n", 1)[1]
        raw = raw.rsplit("```", 1)[0]

    print(f"[L2] LLM response: raw_len={len(raw)} preview={raw[:300]}", flush=True)
    return raw


def _extract_json(text: str) -> dict | None:
    """Extract first balanced JSON object from text."""
    if not text:
        return None
    start = text.find("{")
    if start == -1:
        return None
    depth = 0
    in_str = False
    esc = False
    for i in range(start, len(text)):
        ch = text[i]
        if esc:
            esc = False
            continue
        if ch == "\\":
            esc = True
            continue
        if ch == '"' and not esc:
            in_str = not in_str
        if in_str:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:i + 1])
                except json.JSONDecodeError:
                    return None
    return None


# ── Main entry point ─────────────────────────────────────────────────────────

# ── Single-call judge response schema ──────────────────────────────────────

class NodeJudgeItem(BaseModel):
    """A single rubric item judged by the node-level L2 (0-10 anchor scale)."""
    id: str = Field(description="Rubric check id (must match a check id exactly)")
    score: float = Field(description="0-10 on the anchor scale; 5 or more = MET")
    what: str = Field(default="", description="Which specific requirement failed or passed")
    where: str = Field(default="", description="file:function or exact path in the artifact")
    why: str = Field(default="", description="Root cause in one sentence")
    how: str = Field(default="", description="Concrete change that would satisfy this criterion")


class NodeJudgeResponse(BaseModel):
    """All rubric items for the node, judged in a single call."""
    items: list[NodeJudgeItem] = Field(default_factory=list)


def build_node_judge_prompt(
    rubric_checks: list[Check],
    artifact: str,
    anchors_serial: list[dict] | None = None,
    feedback_contract: str | None = None,
) -> str:
    """Build the single-call judge prompt for a node.

    Embeds the 0-10 anchor scale (from DB ``judge_rubrics`` or hardcoded
    defaults), the feedback contract, the artifact, and every rubric item
    with its weight. Demands strict JSON matching ``NodeJudgeResponse``.
    """
    anchors = anchors_serial or L2_RUBRIC_ANCHORS_SERIAL
    anchors_text = "\n".join(
        f"  score {a['score_range'][0]}-{a['score_range'][1]}: {a['expected_outcome']}"
        for a in anchors
    )
    items_text = "\n".join(
        f"- id={c.id} (weight {getattr(c, 'weight', 1.0)}): {c.rubric_item or c.criterion}"
        for c in rubric_checks
    )
    return f"""You are a strict, impartial quality judge scoring a node's work product.
Rate each rubric item against the artifact using the 0-10 score scale below.

SCORE SCALE (anchors):
{anchors_text}

FEEDBACK CONTRACT:
{feedback_contract or FEEDBACK_CONTRACT}

ARTIFACT:
{artifact}

RUBRIC ITEMS TO JUDGE:
{items_text}

Return a JSON object ONLY: {{"items": [{{"id": ..., "score": 0-10, "what": ..., "where": ..., "why": ..., "how": ...}}]}}.
A score of 5 or more means the criterion is MET. Every rubric item MUST appear exactly once."""


def _item_feedback(item: NodeJudgeItem) -> dict:
    """Build the feedback_raw dict for a judged item, marked degraded when
    the what/where/why/how content fails the deterministic DimFeedback checks."""
    fb = {
        "what": item.what or "",
        "where": item.where or "unspecified",
        "why": item.why or "",
        "how": item.how or "unspecified",
    }
    validated, _ = validate_feedback(fb)
    if validated is None:
        fb["_degraded"] = True
    return fb


def _item_feedback_missing() -> dict:
    return {
        "what": "judge response missing item",
        "where": "unspecified",
        "why": "invalid judge response",
        "how": "specify a score for every rubric item",
        "_degraded": True,
    }


# ── Main entry point ─────────────────────────────────────────────────────────
def run_l2(
    checks: list[Check],
    worktree: str,
    trace_id: str | None = None,
    llm_call: Callable[[str], str] | None = None,
    node_context: dict | None = None,
    capability: str | None = None,
    rubric_dims: dict | None = None,
    existing_judgments: list[Judgment] | None = None,
) -> L2Result:
    """Run rubric judge for all rubric checks on a node.

    Default path uses ONE structured judge call (``role="l2_judge"``) covering
    all rubric items. If ``llm_call`` is provided, falls back to the legacy
    raw-LLM path (for testing with mock responses).

    Args:
        checks: List of ``Check`` objects (only ``type=="rubric"`` are evaluated).
        worktree: Path to the node's worktree (for artifact collection).
        trace_id: Optional Langfuse trace id for scoring.
        llm_call: Legacy override — if provided, uses raw LLM call instead of the single-call judge.
        node_context: Optional node dict with ``acceptance_criteria`` for deriving
            evaluation steps from the shared contract.
        capability: Optional capability name (e.g. ``"executor"``, ``"backend_api"``).
            When provided (and ``rubric_dims`` is not), the active rubric config from
            ``judge_rubrics`` is loaded and used for scoring. Falls back to hardcoded
            defaults if no active rubric exists.
        rubric_dims: Optional pre-loaded rubric dimensions dict. When provided,
            takes priority over ``capability``. Used by the judge ratchet to score
            with a candidate rubric during two-split validation.

    Returns:
        ``L2Result`` with weighted score and per-item judgments.
    """
    rubric_checks = [c for c in checks if getattr(c, "tier", None) == "L2"]
    print(f"[L2] run: worktree={worktree} rubric_checks={len(rubric_checks)} trace_id={trace_id}", flush=True)
    if not rubric_checks:
        print("[L2] run: no rubric checks, vacuous pass (score=1.0)", flush=True)
        return L2Result(score=1.0, judgments=[])  # vacuous pass

    # If legacy llm_call is provided, use old code path (for mock-based tests)
    if llm_call is not None:
        return _run_l2_legacy(rubric_checks, worktree, trace_id, llm_call)

    # Load rubric config from judge_rubrics if capability is known
    # (MUST happen before collect_artifact so BUNDLE_RULES can filter the artifact)
    if rubric_dims is None and capability:
        rubric_dims = load_rubric_config(capability)
    if rubric_dims:
        anchors_serial = rubric_dims.get("anchors", L2_RUBRIC_ANCHORS_SERIAL)
        active_fc = rubric_dims.get("feedback_contract", FEEDBACK_CONTRACT)
        rubric_source = f"judge_rubrics/{capability}"
        bundle_rules = rubric_dims.get("bundles") or {}
    else:
        anchors_serial = L2_RUBRIC_ANCHORS_SERIAL
        active_fc = FEEDBACK_CONTRACT
        rubric_source = "hardcoded"
        bundle_rules = {}

    return _run_l2_single_call(
        rubric_checks, worktree, trace_id, node_context=node_context,
        anchors_serial=anchors_serial, feedback_contract=active_fc,
        bundle_rules=bundle_rules, rubric_source=rubric_source,
    )


def _run_l2_single_call(
    rubric_checks: list[Check],
    worktree: str,
    trace_id: str | None,
    node_context: dict | None = None,
    anchors_serial: list[dict] | None = None,
    feedback_contract: str | None = None,
    bundle_rules: dict | None = None,
    rubric_source: str = "hardcoded",
) -> L2Result:
    """Single structured judge call covering ALL rubric items (Option A).

    Collects the artifact, chunks it if oversized, and for each chunk (best
    known first) issues ONE ``call_llm_structured`` judge call. The first
    chunk whose response covers every rubric item is used. Any rubric item
    still missing from the chosen response is hard-failed as an invalid judge
    (``criteria_met=False, score=0.0, explanation="missing from L2 response"``).
    """
    artifact = collect_artifact(worktree, node_context=node_context, bundle_rules=bundle_rules)
    print(f"[L2] artifact collected: {len(artifact)} chars for {worktree}", flush=True)

    chunks = _chunk_artifact(artifact) if len(artifact) > L2_ARTIFACT_MAX_CHUNK_SIZE else [artifact]
    num_chunks = len(chunks)
    if num_chunks > 1:
        print(f"[L2] artifact chunked: {len(artifact)} chars -> {num_chunks} chunks (max_size={L2_ARTIFACT_MAX_CHUNK_SIZE}, overlap={L2_ARTIFACT_CHUNK_OVERLAP})", flush=True)
    else:
        print(f"[L2] artifact fits in 1 chunk ({len(artifact)} chars <= {L2_ARTIFACT_MAX_CHUNK_SIZE})", flush=True)

    best_chunk_idx: int = (node_context or {}).get("best_chunk_idx", 0)
    chunk_order = [best_chunk_idx] + [i for i in range(num_chunks) if i != best_chunk_idx]

    best_chunk_used = best_chunk_idx
    best_score: float = -1.0
    best_judgments: list[Judgment] | None = None
    best_items_met: int = 0
    best_raw: str | None = None
    best_missing: int = len(rubric_checks)  # lower is better

    for chunk_idx in chunk_order:
        prompt = build_node_judge_prompt(
            rubric_checks, chunks[chunk_idx],
            anchors_serial=anchors_serial, feedback_contract=feedback_contract,
        )
        print(f"[L2] single-call judge >>> chunk={chunk_idx}/{num_chunks} items={len(rubric_checks)} rubric_source={rubric_source} model=(l2_judge) input_len={len(chunks[chunk_idx])} chars", flush=True)
        t0 = _time.time()
        try:
            resp, raw = call_llm_structured(
                prompt, NodeJudgeResponse, role="l2_judge",
                include_raw=True, temperature=0.0,
            )
        except Exception as exc:
            print(f"[L2] single-call judge raised (chunk={chunk_idx}): {str(exc)[:300]}", flush=True)
            raise JudgeUnavailableError(
                f"Judge model unavailable for L2 single-call: {exc}"
            ) from exc
        elapsed = _time.time() - t0
        judged = {item.id: item for item in (resp.items or [])}

        total_weight = 0.0
        score_sum = 0.0
        this_judgments: list[Judgment] = []
        missing_ids: list[str] = []
        for c in rubric_checks:
            w = getattr(c, "weight", 1.0) or 1.0
            total_weight += w
            item = judged.get(c.id)
            if item is None:
                missing_ids.append(c.id)
                continue
            norm = max(0.0, min(1.0, item.score / 10.0))
            met = norm >= GEVAL_THRESHOLD
            judgment = Judgment(
                check_id=item.id, criteria_met=met, score=norm,
                explanation=item.why or ("met" if met else "not met"),
                feedback_raw=_item_feedback(item),
            )
            this_judgments.append(judgment)
            score_sum += (judgment.score or 0.0) * w
        score = score_sum / total_weight if total_weight > 0 else 1.0
        print(f"[L2] single-call judge <<< chunk={chunk_idx} elapsed={elapsed:.1f}s score={score:.4f} items={len(this_judgments)} missing={missing_ids}", flush=True)

        # Prefer the most complete response; among equal-missing, the higher score.
        n_missing = len(missing_ids)
        if n_missing < best_missing or (n_missing == best_missing and score > best_score):
            best_score = score
            best_judgments = this_judgments
            best_items_met = sum(1 for j in this_judgments if j.criteria_met)
            best_raw = raw
            best_chunk_used = chunk_idx
            best_missing = n_missing

        if n_missing == 0:
            break  # complete response from the first (best) chunk — done

    if best_judgments is None:
        return L2Result(
            partial=True,
            judgments=[],
            rubric_count=len(rubric_checks),
            best_chunk_idx=best_chunk_used,
            raw_response=best_raw,
        )

    # Hard-fail any item still missing (Option A: invalid judge response).
    if best_missing:
        judged_complete = {j.check_id for j in best_judgments}
        for c in rubric_checks:
            if c.id not in judged_complete:
                best_judgments.append(Judgment(
                    check_id=c.id, criteria_met=False, score=0.0,
                    explanation="missing from L2 response",
                    feedback_raw=_item_feedback_missing(),
                ))

    score = round(best_score, 4)
    items_met = best_items_met
    print(f"[L2] result: score={score:.4f} items_met={items_met}/{len(best_judgments)} for {worktree}", flush=True)

    _write_langfuse(trace_id, score, best_judgments)
    return L2Result(
        score=score,
        judgments=best_judgments,
        rubric_count=len(rubric_checks),
        items_met=items_met,
        best_chunk_idx=best_chunk_used,
        raw_response=best_raw,
    )


def _run_l2_legacy(
    rubric_checks: list[Check],
    worktree: str,
    trace_id: str | None,
    llm_call: Callable[[str], str],
) -> L2Result:
    """Legacy code path: raw LLM call per rubric item (used when ``llm_call`` is injected for tests)."""
    artifact = collect_artifact(worktree)
    if len(artifact) > L2_MAX_CHARS:
        print(f"[L2] legacy artifact OVERSIZE: {len(artifact)} chars > {L2_MAX_CHARS} — proceeding anyway", flush=True)

    judgments: list[Judgment] = []
    total_weight = 0.0
    met_weight = 0.0

    for c in rubric_checks:
        question = getattr(c, "rubric_item", None) or c.criterion
        prompt = JUDGE_USER_PROMPT.format(rubric_item=question, artifact=artifact)
        raw = llm_call(prompt)
        parsed = _extract_json(raw)
        if parsed is None:
            judgment = Judgment(check_id=c.id, criteria_met=False, explanation="Judge returned unparseable response")
        else:
            met = bool(parsed.get("criteria_met", False))
            judgment = Judgment(check_id=c.id, criteria_met=met, explanation=str(parsed.get("explanation", "")))
        judgments.append(judgment)
        w = getattr(c, "weight", 1.0) or 1.0
        total_weight += w
        if judgment.criteria_met:
            met_weight += w

    score = met_weight / total_weight if total_weight > 0 else 1.0
    items_met = sum(1 for j in judgments if j.criteria_met)
    _write_langfuse(trace_id, score, judgments)
    return L2Result(score=round(score, 4), judgments=judgments, rubric_count=len(rubric_checks), items_met=items_met)


def _write_langfuse(trace_id: str | None, score: float, judgments: list[Judgment]) -> None:
    """Write L2 scores to Langfuse (best-effort)."""
    if not trace_id:
        return
    try:
        from backend.observability.langfuse_client import get_langfuse
        lf = get_langfuse()
        lf.create_score(
            trace_id=trace_id,
            name="goal_review",
            value=round(score, 4),
            data_type="NUMERIC",
            comment=" | ".join(
                f"{j.check_id}: {'pass' if j.criteria_met else 'FAIL'} ({j.explanation[:100]})"
                for j in judgments
            ),
        )
        lf.create_score(
            trace_id=trace_id,
            name="passed",
            value=1.0 if score >= 0.7 else 0.0,
            data_type="BOOLEAN",
            comment=f"L2 score={score:.2f}, items={sum(1 for j in judgments if j.criteria_met)}/{len(judgments)}",
        )
        lf.flush()
    except Exception:
        pass  # Langfuse write is best-effort
