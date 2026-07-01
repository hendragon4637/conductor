"""L2 rubric judge — one generalist model, preset rubrics, schema-constrained output.

Each rubric item is judged independently via a structured LLM call.
Weighted score is computed from per-item ``criteria_met`` booleans.
Scores are written to Langfuse on the node trace.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from backend.evaluator.schema import Check, Judgment

# ── Judge model config (now via LiteLLM gateway) ────────────────────────────
JUDGE_TIMEOUT = 120.0

# L2 input-size guard — oversized artifacts trigger a flag-fail instead of truncation
L2_MAX_CHARS = int(os.environ.get("L2_MAX_INPUT_CHARS", "24000"))

ARTIFACT_SKIP_PARTS = {".git", ".venv", "__pycache__", "node_modules", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
ARTIFACT_SKIP_SUFFIXES = {".pyc", ".pyo", ".so", ".dll", ".dylib", ".db", ".sqlite", ".sqlite3", ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".zip", ".tar", ".gz"}

# ── Prompt template ─────────────────────────────────────────────────────────

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


def collect_artifact(worktree: str, max_chars: int = L2_MAX_CHARS) -> str:
    """Collect evidence from the worktree for the judge to evaluate.

    Captures working-tree diff, last-commit diff (for committed executor
    results), tracked file listing, file contents, and untracked files.
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
            parts.append("[New files]")
            lines = [f for f in untracked.splitlines() if f.strip() and not _artifact_skip_path(f)]
            lines = sorted(lines, key=_artifact_priority)[:40]
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

def run_l2(
    checks: list[Check],
    worktree: str,
    trace_id: str | None = None,
    llm_call: Callable[[str], str] | None = None,
) -> L2Result:
    """Run rubric judge for all rubric checks on a node.

    Args:
        checks: List of ``Check`` objects (only ``type=="rubric"`` are evaluated).
        worktree: Path to the node's worktree (for artifact collection).
        trace_id: Optional Langfuse trace id for scoring.
        llm_call: Overrideable LLM call function (for testing).

    Returns:
        ``L2Result`` with weighted score and per-item judgments.
    """
    if llm_call is None:
        llm_call = _default_judge_llm

    rubric_checks = [c for c in checks if getattr(c, "tier", None) == "L2"]
    print(f"[L2] run: worktree={worktree} rubric_checks={len(rubric_checks)} trace_id={trace_id}", flush=True)
    if not rubric_checks:
        print("[L2] run: no rubric checks, vacuous pass (score=1.0)", flush=True)
        return L2Result(score=1.0, judgments=[])  # vacuous pass

    artifact = collect_artifact(worktree)
    print(f"[L2] artifact collected: {len(artifact)} chars for {worktree}", flush=True)

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

    judgments: list[Judgment] = []
    total_weight = 0.0
    met_weight = 0.0

    for c in rubric_checks:
        question = getattr(c, "rubric_item", None) or c.criterion
        prompt = JUDGE_USER_PROMPT.format(rubric_item=question, artifact=artifact)
        print(f"[L2] rubric check: id={c.id} weight={getattr(c, 'weight', 1.0)} question={question}", flush=True)

        raw = llm_call(prompt)
        parsed = _extract_json(raw)

        if parsed is None:
            judgment = Judgment(
                check_id=c.id,
                criteria_met=False,
                explanation="Judge returned unparseable response",
            )
            print(f"[L2] rubric {c.id}: unparseable response: {raw[:200]}", flush=True)
        else:
            met = parsed.get("criteria_met")
            if met is None:
                met = False
            judgment = Judgment(
                check_id=c.id,
                criteria_met=bool(met),
                explanation=str(parsed.get("explanation", "")),
            )
            print(f"[L2] rubric {c.id}: criteria_met={judgment.criteria_met} explanation={judgment.explanation}", flush=True)

        judgments.append(judgment)
        w = getattr(c, "weight", 1.0) or 1.0
        total_weight += w
        if judgment.criteria_met:
            met_weight += w

    score = met_weight / total_weight if total_weight > 0 else 1.0
    items_met = sum(1 for j in judgments if j.criteria_met)
    print(f"[L2] result: score={score:.4f} items_met={items_met}/{len(judgments)} for {worktree}", flush=True)

    # Write to Langfuse if trace_id is provided
    if trace_id:
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
                comment=f"L2 score={score:.2f}, items={items_met}/{len(judgments)}",
            )
            lf.flush()
        except Exception:
            pass  # Langfuse write is best-effort

    return L2Result(
        score=round(score, 4),
        judgments=judgments,
        rubric_count=len(rubric_checks),
        items_met=items_met,
    )
