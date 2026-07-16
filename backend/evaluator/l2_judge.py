"""L2 rubric judge — deepeval GEval metrics via LiteLLM JUDGE gateway.

Each rubric item is evaluated as a separate GEval metric. The weighted
score is computed from per-item criteria_met booleans. Langfuse scoring
is preserved.

Backward compat: if ``llm_call`` is passed to ``run_l2()``, the original
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

from deepeval.metrics import GEval
from deepeval.metrics.g_eval.utils import Rubric
from deepeval.test_case import LLMTestCase, LLMTestCaseParams

from backend.evaluator.schema import Check, Judgment
from shared.eval_models import JUDGE as JUDGE_MODEL

# ── Judge model config ──────────────────────────────────────────────────────
JUDGE_TIMEOUT = 300.0

# L2 input-size — oversized artifacts trigger a flag-fail instead of truncation
L2_MAX_CHARS = int(os.environ.get("L2_MAX_INPUT_CHARS", "24000"))

GEVAL_THRESHOLD = 0.5
"""Score threshold for per-item criteria_met conversion."""

from contracts.paths import INFRA_EXCLUDES, INFRA_SKIP_PARTS
from contracts.feedback import get_dim_feedback, parse_feedback, try_validate_feedback

ARTIFACT_SKIP_PARTS = INFRA_SKIP_PARTS | {".git"}
ARTIFACT_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".dylib", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz"}

# ── Structured feedback contract (folded into evaluation_steps) ─────────────

FEEDBACK_CONTRACT = (
    'In your reason, output STRICT JSON only: {"what": "which specific requirement failed or passed", '
    '"where": "file:function or exact path in the artifact", '
    '"why": "root cause in one sentence", '
    '"how": "the concrete change that would satisfy this criterion"}. '
    'Quote actual file paths and code identifiers FROM THE ARTIFACT — never generic phrases.'
)

L2_RUBRIC_ANCHORS = [
    Rubric(score_range=(0, 2),  expected_outcome="deliverable missing or core behavior absent"),
    Rubric(score_range=(3, 5),  expected_outcome="deliverable exists but the criterion's core behavior is wrong"),
    Rubric(score_range=(6, 8),  expected_outcome="criterion met for the main path; edge cases unhandled"),
    Rubric(score_range=(9, 10), expected_outcome="criterion fully met incl. edge cases"),
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


def _dims_to_rubric_anchors(dims: dict) -> list[Rubric]:
    anchors_raw = dims.get("anchors", L2_RUBRIC_ANCHORS_SERIAL)
    return [
        Rubric(score_range=a["score_range"], expected_outcome=a["expected_outcome"])
        for a in anchors_raw
    ]


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


# Serialisable form used for DB round-trip (Rubric objects are not JSON-serialisable)
L2_RUBRIC_ANCHORS_SERIAL = [
    {"score_range": [0, 2], "expected_outcome": "deliverable missing or core behavior absent"},
    {"score_range": [3, 5], "expected_outcome": "deliverable exists but the criterion's core behavior is wrong"},
    {"score_range": [6, 8], "expected_outcome": "criterion met for the main path; edge cases unhandled"},
    {"score_range": [9, 10], "expected_outcome": "criterion fully met incl. edge cases"},
]


def build_dim_metric(
    dim_id: str,
    rubric_question: str,
    steps: list[str] | None = None,
    rubric_anchors: list[Rubric] | None = None,
    feedback_contract: str | None = None,
) -> GEval:
    """Build a single GEval metric with explicit steps + rubric anchors.

    ``evaluation_steps`` replaces GEval's auto-generated vague steps;
    ``rubric`` anchors give the judge a 0-10 scale with clear mappings;
    ``FEEDBACK_CONTRACT`` demands structured JSON in ``reason``.

    If ``steps`` is provided, it replaces the default steps. This allows
    acceptance criteria from the shared contract to drive the judge's
    evaluation focus.

    If ``rubric_anchors`` or ``feedback_contract`` are provided, they override
    the module-level defaults — used when scoring under a versioned rubric
    from ``judge_rubrics``.
    """
    fc = feedback_contract or FEEDBACK_CONTRACT
    return GEval(
        name=dim_id,
        evaluation_steps=steps or [
            f"Evaluate the artifact against this criterion: {rubric_question}",
            "Identify the exact files/functions relevant to the criterion; check their actual content",
            fc,
        ],
        rubric=rubric_anchors or L2_RUBRIC_ANCHORS,
        evaluation_params=[LLMTestCaseParams.INPUT, LLMTestCaseParams.ACTUAL_OUTPUT],
        model=JUDGE_MODEL,
        threshold=GEVAL_THRESHOLD,
        strict_mode=False,
    )

_MAX_GEVAL_RETRIES = 3
_GEVAL_RETRY_DELAY_S = 5

_RETRYABLE_ERROR_PATTERNS = [
    "rate_limit", "rate limit", "ratelimit",
    "timeout",
    "badrequesterror",
    "serviceunavailable",
    "resource_exhausted", "resourceexhausted",
    "upstream request failed",
    "connection", "connectionrefused", "connectionreset",
    "internal server error",
    "server error",
    "429", "502", "503",
    "api error",
    "try again",
]


def _is_retryable(exc: Exception) -> bool:
    """Return True if *exc* looks like a transient infra error."""
    low = str(exc).lower()
    return any(p in low for p in _RETRYABLE_ERROR_PATTERNS)

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


# ── Artifact collection ──────────────────────────────────────────────────────

def _artifact_skip_path(path: str) -> bool:
    p = Path(path)
    return any(part in ARTIFACT_SKIP_PARTS for part in p.parts) or p.suffix.lower() in ARTIFACT_SKIP_SUFFIXES


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


def _build_repomix_snapshot(worktree: str, node_context: dict | None = None) -> str:
    """Generate a bounded repomix snapshot of the worktree for the judge.

    Includes full content for: criterion ``where`` paths + deliverables +
    manifests (pyproject.toml, package.json, RUN.md). Everything else is
    a compressed tree listing (~2KB). Falls back gracefully if repomix
    CLI is unavailable.
    """
    include_paths: list[str] = ["pyproject.toml", "package.json", "requirements.txt", "RUN.md"]
    if node_context:
        for ac in (node_context.get("acceptance_criteria") or []):
            include_paths.extend(ac.get("where", []))
        for d in (node_context.get("deliverables") or node_context.get("task", {}).get("deliverables", [])):
            if d not in include_paths:
                include_paths.append(d)
    include_paths = list(dict.fromkeys(include_paths))  # dedupe, preserve order
    ignore_str = ",".join(INFRA_EXCLUDES)
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
        parts.append(snapshot[:10000])
    if tree:
        parts.append("[REPO TREE — bounded]")
        parts.append(tree)
    return "\n".join(parts)


def collect_artifact(worktree: str, max_chars: int = L2_MAX_CHARS, node_context: dict | None = None) -> str:
    """Collect evidence from the worktree for the judge to evaluate.

    Captures working-tree diff, last-commit diff (for committed executor
    results), tracked file listing, file contents, untracked files, and
    a bounded repomix snapshot ("what exists" alongside "what changed").
    """
    parts: list[str] = []

    has_wt_diff = False
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        diff = result.stdout.strip()
        if diff:
            parts.append("[Git diff working tree]")
            parts.append(diff[:max_chars // 2])
            has_wt_diff = True
    except Exception:
        parts.append("[Git diff: unavailable]")

    # Report existence of important directories that would otherwise be
    # excluded from the artifact (e.g., .venv), so the L2 judge has
    # evidence they exist without including their full contents.
    for _marker_dir in (".venv",):
        if (Path(worktree) / _marker_dir).is_dir():
            parts.append(f"[Directory exists: {_marker_dir}/]")

    try:
        result = subprocess.run(
            ["git", "ls-files"],
            cwd=worktree, capture_output=True, text=True, timeout=15,
        )
        tracked = [f for f in result.stdout.strip().splitlines() if f.strip()]
        if tracked:
            parts.append("[Tracked files]")
            parts.append("\n".join(tracked[:30]))
    except Exception:
        pass

    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=worktree,
            capture_output=True, text=True, timeout=15,
        )
        untracked = result.stdout.strip()
        if untracked:
            all_lines = [f for f in untracked.splitlines() if f.strip()]
            # Log what was excluded
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

    try:
        rc = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            cwd=worktree, capture_output=True, text=True, timeout=15,
        )
        commit_count = int(rc.stdout.strip() or 0)
        if commit_count > 1:
            result = subprocess.run(
                ["git", "diff", "HEAD~1..HEAD", "--no-color"],
                cwd=worktree, capture_output=True, text=True, timeout=30,
            )
            committed_diff = result.stdout.strip()
            if committed_diff:
                parts.append("[Last commit diff]")
                parts.append(committed_diff[:max_chars // 3])
        elif commit_count == 1:
            result = subprocess.run(
                ["git", "show", "HEAD", "--no-color", "--stat"],
                cwd=worktree, capture_output=True, text=True, timeout=30,
            )
            shown = result.stdout.strip()
            if shown:
                parts.append("[Initial commit summary]")
                parts.append(shown[:max_chars // 4])
    except Exception:
        pass

    # Append bounded repomix snapshot ("what exists" alongside "what changed")
    snapshot = _build_repomix_snapshot(worktree, node_context)
    if snapshot:
        parts.append("")
        parts.append(snapshot)

    full = "\n".join(parts)
    return full[:max_chars]


# ── Judge model call ─────────────────────────────────────────────────────────

def _default_judge_llm(prompt: str) -> str:
    """Call the judge model through the LiteLLM gateway."""
    from backend.llm.gateway import call as gateway_call

    print(f"[L2] LLM request via gateway: role=l2_judge prompt_preview={prompt[:300]}", flush=True)

    try:
        result = gateway_call("l2_judge", [
            {"role": "system", "content": JUDGE_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ], temperature=0.0, max_tokens=2048, timeout=JUDGE_TIMEOUT)
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

def _build_eval_steps_from_criterion(criterion: Check, node_context: dict | None = None, feedback_contract: str | None = None) -> list[str]:
    steps = [
        f"Evaluate the artifact against this criterion: {criterion.rubric_item or criterion.criterion}",
        "Identify the exact files/functions relevant to the criterion; check their actual content",
        feedback_contract or FEEDBACK_CONTRACT,
    ]
    if node_context:
        ac_list = node_context.get("acceptance_criteria", []) or node_context.get("criteria", [])
        for ac in ac_list:
            if ac.get("id") == criterion.id or ac.get("what", "").startswith((criterion.rubric_item or "")[:40]):
                where = ac.get("where", [])
                verified = ac.get("how_verified", "")
                if where:
                    steps.insert(1, f"Inspect these paths: {', '.join(where)}")
                if verified:
                    steps.insert(2, f"It is satisfied when: {verified}")
                break
    return steps


def run_l2(
    checks: list[Check],
    worktree: str,
    trace_id: str | None = None,
    llm_call: Callable[[str], str] | None = None,
    node_context: dict | None = None,
    capability: str | None = None,
    rubric_dims: dict | None = None,
) -> L2Result:
    """Run rubric judge for all rubric checks on a node.

    Default path uses deepeval GEval metrics via ``JUDGE_MODEL``.
    If ``llm_call`` is provided, falls back to the legacy raw-LLM path (for
    testing with mock responses).

    Args:
        checks: List of ``Check`` objects (only ``type=="rubric"`` are evaluated).
        worktree: Path to the node's worktree (for artifact collection).
        trace_id: Optional Langfuse trace id for scoring.
        llm_call: Legacy override — if provided, uses raw LLM call instead of GEval.
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

    # ── Default: deepeval GEval path ──────────────────────────────────────
    artifact = collect_artifact(worktree, node_context=node_context)
    print(f"[L2] artifact collected: {len(artifact)} chars for {worktree}", flush=True)
    print(f"[L2] artifact content:\n{artifact}", flush=True)

    # L2 input-size guard: oversize → flag-fail (no silent truncation)
    if len(artifact) > L2_MAX_CHARS:
        print(f"[L2] artifact OVERSIZE: {len(artifact)} chars > {L2_MAX_CHARS} cap for {worktree}", flush=True)
        return L2Result(
            score=0.0,
            judgments=[],
            rubric_count=len(rubric_checks),
            items_met=0,
            oversize=True,
        )

    # Load rubric config from judge_rubrics if capability is known
    if rubric_dims is None and capability:
        rubric_dims = load_rubric_config(capability)
    if rubric_dims:
        active_anchors = _dims_to_rubric_anchors(rubric_dims)
        active_fc = rubric_dims.get("feedback_contract", FEEDBACK_CONTRACT)
        rubric_source = f"judge_rubrics/{capability}"
    else:
        active_anchors = L2_RUBRIC_ANCHORS
        active_fc = FEEDBACK_CONTRACT
        rubric_source = "hardcoded"

    judgments: list[Judgment] = []
    total_weight = 0.0
    score_sum = 0.0

    deepeval_timeout = os.environ.get("DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE", "not set")
    artifact_chars = len(artifact)
    print(f"[L2] GEval config: model={JUDGE_MODEL.model} model_base={JUDGE_MODEL.base_url} threshold={GEVAL_THRESHOLD} deepeval_timeout={deepeval_timeout}s artifact_size={artifact_chars}chars rubric_source={rubric_source}", flush=True)

    for c in rubric_checks:
        question = getattr(c, "rubric_item", None) or c.criterion
        print(f"[L2] rubric check: id={c.id} weight={getattr(c, 'weight', 1.0)} question={question}", flush=True)

        # Build eval steps from acceptance criteria if available
        steps = _build_eval_steps_from_criterion(c, node_context, feedback_contract=active_fc)

        last_error: str | None = None
        judgment = None
        for attempt in range(1 + _MAX_GEVAL_RETRIES):
            try:
                metric = build_dim_metric(c.id, question, steps=steps, rubric_anchors=active_anchors, feedback_contract=active_fc)
                test_case = LLMTestCase(
                    input=question,
                    actual_output=artifact,
                )
                if attempt == 0:
                    print(f"[L2] GEval >>> name={c.id} steps={metric.evaluation_steps} rubric={L2_RUBRIC_ANCHORS} model={JUDGE_MODEL} threshold={GEVAL_THRESHOLD}", flush=True)
                    print(f"[L2] GEval >>> input_len={len(artifact)} chars", flush=True)
                t0 = _time.time()
                metric.measure(test_case)
                elapsed = _time.time() - t0
                if attempt > 0:
                    print(f"[L2] GEval retry #{attempt} succeeded id={c.id} elapsed={elapsed:.1f}s", flush=True)
                else:
                    print(f"[L2] GEval <<< completed id={c.id} elapsed={elapsed:.1f}s", flush=True)

                raw_reason = json.dumps(metric.reason) if isinstance(metric.reason, dict) else (metric.reason or "")
                original_score = float(getattr(metric, "score", 0.0) or 0.0)
                feedback, feedback_degraded = get_dim_feedback(
                    metric, c.id, test_case, raw_reason=raw_reason,
                )
                met = original_score >= GEVAL_THRESHOLD
                judgment = Judgment(
                    check_id=c.id,
                    criteria_met=met,
                    score=original_score,
                    explanation=raw_reason,
                    feedback_raw=feedback,
                )
                where = feedback.get("where", "unspecified")
                what = feedback.get("what", "")
                print(f"[L2] {c.id} score={original_score:.4f} WHERE={where} WHAT={what}", flush=True)
                if feedback_degraded or feedback.get("_degraded"):
                    print(f"[L2] {c.id} WARNING: feedback failed content validation, marked degraded", flush=True)
                elif feedback.get("_unstructured"):
                    print(f"[L2] {c.id} WARNING: unstructured GEval reason, feedback degraded", flush=True)
                break

            except Exception as exc:
                exc_str = str(exc)
                last_error = exc_str
                if _is_retryable(exc) and attempt < _MAX_GEVAL_RETRIES:
                    delay = _GEVAL_RETRY_DELAY_S * (2 ** attempt)
                    print(f"[L2] GEval transient error (attempt {attempt+1}), retrying in {delay}s: {exc_str[:200]}", flush=True)
                    _time.sleep(delay)
                else:
                    print(f"[L2] GEval permanent error (attempt {attempt+1}): {exc_str[:300]}", flush=True)
                    judgment = Judgment(
                        check_id=c.id,
                        criteria_met=False,
                        score=0.0,
                        explanation=f"GEval error ({'retries exhausted' if attempt > 0 else 'permanent'}): {exc}",
                    )
                    break

        if judgment is None:
            judgment = Judgment(
                check_id=c.id,
                criteria_met=False,
                score=0.0,
                explanation=f"GEval error: {last_error or 'unknown'}",
            )

        judgments.append(judgment)
        w = getattr(c, "weight", 1.0) or 1.0
        total_weight += w
        score_sum += (judgment.score or 0.0) * w

    score = score_sum / total_weight if total_weight > 0 else 1.0
    items_met = sum(1 for j in judgments if j.criteria_met)
    print(f"[L2] result: score={score:.4f} items_met={items_met}/{len(judgments)} for {worktree}", flush=True)

    _write_langfuse(trace_id, score, judgments)
    return L2Result(
        score=round(score, 4),
        judgments=judgments,
        rubric_count=len(rubric_checks),
        items_met=items_met,
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
        return L2Result(score=0.0, judgments=[], rubric_count=len(rubric_checks), items_met=0, oversize=True)

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
