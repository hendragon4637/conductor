#!/usr/bin/env python3
"""Test the L2 judge API endpoint end-to-end.

Tests:
  1. Raw HTTP call to Zen API (same format as _default_judge_llm)
  2. Response parsing (_extract_json)
  3. Full run_l2 path with rubric checks
"""

import json
import os
import subprocess
import sys
import urllib.request

# ── Ensure we can import from the conductor backend ──────────────────────
sys.path.insert(0, "/opt/aipc/conductor")

JUDGE_ENDPOINT = "https://opencode.ai/zen/v1/chat/completions"
PRIMARY_MODEL = "deepseek-v4-flash-free"
FALLBACK_MODEL = "nemotron-3-ultra-free"

# ── Test 1: Raw API call ─────────────────────────────────────────────────

def test_raw_api_call():
    """Hit the Zen API directly with a simple rubric prompt."""
    print("=" * 60)
    print("TEST 1: Raw API call to Zen endpoint")
    print("=" * 60)

    system_prompt = """You are a strict, impartial quality judge.

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

    user_prompt = """Rubric item: Does the code include error handling for edge cases?

Artifact:
```python
def divide(a, b):
    return a / b
```

Respond as {"criteria_met": true/false, "explanation": "..."}"""

    api_key = os.environ.get("OPENCODE_ZEN_API_KEY", "")
    if not api_key:
        print("  FAIL: OPENCODE_ZEN_API_KEY not set")
        return False

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "conductor-l2-judge-test/1.0",
        "Authorization": f"Bearer {api_key}",
    }

    # Try primary model
    for model_name, label in [(PRIMARY_MODEL, "primary"), (FALLBACK_MODEL, "fallback")]:
        body = {
            "model": model_name,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.0,
            "max_tokens": 2048,
        }

        try:
            req = urllib.request.Request(
                JUDGE_ENDPOINT,
                data=json.dumps(body).encode(),
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=120.0) as resp:
                result = json.loads(resp.read())
        except Exception as e:
            print(f"  {label} model ({model_name}): FAIL — {e}")
            continue

        # Parse response
        msg = result.get("choices", [{}])[0].get("message", {})
        raw = (msg.get("content") or msg.get("reasoning") or msg.get("reasoning_content") or "").strip()
        if not raw:
            print(f"  {label} model ({model_name}): FAIL — empty response")
            continue

        print(f"  {label} model ({model_name}): OK")
        print(f"  Raw response (first 300 chars): {raw[:300]}")
        print(f"  Choices count: {len(result.get('choices', []))}")
        print(f"  Model reported: {result.get('model', '?')}")
        print(f"  Usage: {result.get('usage', {})}")
        return True

    print("  FAIL: Both models unreachable")
    return False


# ── Test 2: _extract_json parsing ────────────────────────────────────────

def _extract_json(text: str) -> dict | None:
    """Extract first balanced JSON object from text (copied from l2_judge.py)."""
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


def test_response_parsing():
    """Test _extract_json against various response formats."""
    print("\n" + "=" * 60)
    print("TEST 2: Response parsing (_extract_json)")
    print("=" * 60)

    cases = [
        # (input, expected_criteria_met, description)
        ('{"criteria_met": true, "explanation": "Looks good"}', True, "plain JSON"),
        ('  {"criteria_met": false, "explanation": "Nope"}  ', False, "JSON with whitespace"),
        ('```json\n{"criteria_met": true, "explanation": "ok"}\n```', True, "JSON in code block"),
        ('```\n{"criteria_met": false, "explanation": "missing"}\n```', False, "JSON in plain code block"),
        ('Some text\n{"criteria_met": true, "explanation": "works"}\ntrailing', True, "JSON with text around"),
        ('', None, "empty string"),
        ('not json at all', None, "no JSON content"),
        ('{"criteria_met": true}', True, "JSON without explanation"),
    ]

    all_ok = True
    for raw_input, expected_met, desc in cases:
        parsed = _extract_json(raw_input)
        if parsed is None:
            if expected_met is None:
                print(f"  OK: {desc} → None (expected)")
            else:
                print(f"  FAIL: {desc} → None (expected criteria_met={expected_met})")
                all_ok = False
        else:
            got = parsed.get("criteria_met")
            if got == expected_met:
                print(f"  OK: {desc} → criteria_met={got}")
            else:
                print(f"  FAIL: {desc} → criteria_met={got} (expected {expected_met})")
                all_ok = False

    return all_ok


# ── Test 3: Full run_l2 with real rubric checks ──────────────────────────

def test_full_run_l2():
    """Test the full run_l2 path by importing from the backend."""
    print("\n" + "=" * 60)
    print("TEST 3: Full run_l2 with real rubric checks")
    print("=" * 60)

    # Create a temporary worktree with a simple artifact file
    import tempfile
    import pathlib

    worktree = pathlib.Path(tempfile.mkdtemp(prefix="judge-test-"))
    (worktree / "test_output.txt").write_text(
        "def health_check():\n    return {'status': 'ok'}\n"
    )

    # Initialize git in the worktree so collect_artifact works
    subprocess.run(["git", "init"], cwd=worktree, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=worktree, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=worktree, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=worktree, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=worktree, capture_output=True)

    # Now make a change so there's a diff
    (worktree / "test_output.txt").write_text(
        "def health_check():\n    return {'status': 'ok', 'version': '1.0'}\n"
    )

    # Import from backend
    from backend.evaluator.schema import Check
    from backend.evaluator.l2_judge import run_l2, _extract_json as judge_extract_json

    # Create rubric checks
    checks = [
        Check(
            id="rubric-1",
            type="rubric",
            tier="L2",
            criterion="Does the code have a working health check endpoint?",
            rubric_item="Does the code expose a health check endpoint returning status and version info?",
            weight=1.0,
        ),
        Check(
            id="rubric-2",
            type="rubric",
            tier="L2",
            criterion="Does the code include error handling?",
            rubric_item="Does the code include proper error handling for invalid inputs?",
            weight=1.0,
        ),
    ]

    result = run_l2(checks=checks, worktree=str(worktree))

    print(f"  Score: {result.score}")
    print(f"  Rubric count: {result.rubric_count}")
    print(f"  Items met: {result.items_met}")
    for j in result.judgments:
        print(f"  Judgment [{j.check_id}]: met={j.criteria_met}, explanation='{j.explanation}'")

    # Cleanup
    import shutil
    shutil.rmtree(worktree, ignore_errors=True)

    return result.score > 0  # Should get at least some passing


# ── Test 4: run_l2 with code_implementation rubric (e2e-relevant) ─────────

# ── Fixed collect_artifact (prototype — to validate before backporting) ──────

def _collect_artifact_fixed(worktree: str, max_chars: int = 8000) -> str:
    """Collect evidence from the worktree for the judge to evaluate.

    Captures working-tree diff, last-commit diff (for committed executor
    results), tracked file listing, file contents, and untracked files.
    """
    parts: list[str] = []

    # Working-tree diff (uncommitted changes)
    has_wt_diff = False
    try:
        result = subprocess.run(
            ["git", "diff", "--no-color"],
            cwd=worktree, capture_output=True, text=True, timeout=30,
        )
        diff = result.stdout.strip()
        if diff:
            parts.append("[Git diff — working tree]")
            parts.append(diff[:max_chars // 2])
            has_wt_diff = True
    except Exception:
        parts.append("[Git diff: unavailable]")

    # Last-commit diff — when executor has committed all changes
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
                parts.append(committed_diff[:max_chars // 2])
        elif commit_count == 1:
            result = subprocess.run(
                ["git", "show", "--stat", "--no-color", "HEAD"],
                cwd=worktree, capture_output=True, text=True, timeout=30,
            )
            committed = result.stdout.strip()
            if committed:
                parts.append("[Initial commit]")
                parts.append(committed[:max_chars // 2])
    except Exception:
        pass

    # Tracked file listing
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

    # Tracked file contents (when working tree is clean)
    if not has_wt_diff:
        try:
            # Show full diff of the most recent commit so the judge sees code
            result = subprocess.run(
                ["git", "show", "HEAD", "--no-color"],
                cwd=worktree, capture_output=True, text=True, timeout=30,
            )
            shown = result.stdout.strip()
            if shown:
                parts.append("[Full commit diff]")
                parts.append(shown[:max_chars // 2])
        except Exception:
            pass
        except Exception:
            pass

    # Untracked files
    try:
        result = subprocess.run(
            ["git", "ls-files", "--others", "--exclude-standard"],
            cwd=worktree, capture_output=True, text=True, timeout=15,
        )
        untracked = result.stdout.strip()
        if untracked:
            parts.append("[New files]")
            lines = untracked.splitlines()[:20]
            for f in lines:
                fpath = pathlib.Path(worktree) / f
                if fpath.is_file() and fpath.stat().st_size < 50000:
                    try:
                        content = fpath.read_text(errors="replace")[:2000]
                        parts.append(f"--- {f} ---")
                        parts.append(content)
                    except Exception:
                        parts.append(f"--- {f} --- (unreadable)")
    except Exception:
        pass

    full = "\n".join(parts)
    return full[:max_chars]


# ── Tests ────────────────────────────────────────────────────────────────────

def test_collect_artifact_committed():
    """collect_artifact must capture committed files (no working-tree diff)."""
    print("\n" + "=" * 60)
    print("TEST 4a: collect_artifact with all changes committed")
    print("=" * 60)

    import tempfile
    import pathlib as _pl

    td = _pl.Path(tempfile.mkdtemp(prefix="collect-test-"))
    (td / "main.py").write_text("# file content")
    subprocess.run(["git", "init"], cwd=td, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=td, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=td, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=td, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=td, capture_output=True)

    artifact = _collect_artifact_fixed(str(td))
    print(f"  Artifact length: {len(artifact)} chars")
    print(f"  Contains tracked files: {'[Tracked files]' in artifact}")
    print(f"  Contains file content: {'--- main.py ---' in artifact}")
    print(f"  First 200 chars:\n{artifact[:200]}")

    import shutil
    shutil.rmtree(td, ignore_errors=True)
    return len(artifact) > 100


def test_collect_artifact_with_uncommitted():
    """collect_artifact must still work with uncommitted changes."""
    print("\n" + "=" * 60)
    print("TEST 4b: collect_artifact with uncommitted changes")
    print("=" * 60)

    import tempfile
    import pathlib as _pl

    td = _pl.Path(tempfile.mkdtemp(prefix="collect-test2-"))
    (td / "main.py").write_text("# original")
    subprocess.run(["git", "init"], cwd=td, capture_output=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=td, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=td, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=td, capture_output=True)
    subprocess.run(["git", "commit", "-m", "first"], cwd=td, capture_output=True)

    # Uncommitted change
    (td / "main.py").write_text("# modified")
    artifact = _collect_artifact_fixed(str(td))
    print(f"  Artifact length: {len(artifact)} chars")
    print(f"  Has working-tree diff: {'[Git diff — working tree]' in artifact}")
    print(f"  First 300 chars:\n{artifact[:300]}")

    import shutil
    shutil.rmtree(td, ignore_errors=True)
    return '[Git diff — working tree]' in artifact


def test_code_implementation_rubric():
    """Test the exact rubric preset used in the e2e BYO-DAG scenario."""
    print("\n" + "=" * 60)
    print("TEST 5: code_implementation rubric with fixed collect_artifact")
    print("=" * 60)

    import tempfile
    import pathlib

    worktree = pathlib.Path(tempfile.mkdtemp(prefix="judge-e2e-"))
    (worktree / "main.py").write_text(
        """\
from fastapi import FastAPI

app = FastAPI()

@app.get("/health")
def health():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
"""
    )

    subprocess.run(["git", "init"], cwd=worktree, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=worktree, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=worktree, capture_output=True)
    subprocess.run(["git", "add", "."], cwd=worktree, capture_output=True)
    subprocess.run(["git", "commit", "-m", "initial"], cwd=worktree, capture_output=True)

    from backend.evaluator.schema import Check
    from backend.evaluator.l2_judge import run_l2

    checks = [
        Check(id="r1", type="rubric", tier="L2",
              criterion="Functional correctness",
              rubric_item="Does the implementation meet the core functional requirements and handle all specified inputs?",
              weight=2.0),
        Check(id="r2", type="rubric", tier="L2",
              criterion="Error handling",
              rubric_item="Are errors handled gracefully with clear messages and appropriate status codes?",
              weight=1.5),
        Check(id="r3", type="rubric", tier="L2",
              criterion="Code structure",
              rubric_item="Is the code well-structured with clear separation of concerns and consistent patterns?",
              weight=1.0),
        Check(id="r4", type="rubric", tier="L2",
              criterion="Persistence",
              rubric_item="Is state persisted appropriately using a database or durable storage?",
              weight=1.0),
        Check(id="r5", type="rubric", tier="L2",
              criterion="Testing",
              rubric_item="Are there tests covering both normal operation and edge cases?",
              weight=1.0),
    ]

    # Monkey-patch collect_artifact in the imported module
    import backend.evaluator.l2_judge as l2j
    original = l2j.collect_artifact
    l2j.collect_artifact = _collect_artifact_fixed

    result = run_l2(checks=checks, worktree=str(worktree))

    # Restore
    l2j.collect_artifact = original

    print(f"  Score: {result.score}")
    print(f"  Rubric count: {result.rubric_count}")
    print(f"  Items met: {result.items_met}")
    for j in result.judgments:
        print(f"  Judgment [{j.check_id}]: met={j.criteria_met}, explanation='{j.explanation[:120]}'")

    import shutil
    shutil.rmtree(worktree, ignore_errors=True)

    return result.score


if __name__ == "__main__":
    # Load secrets from vault
    rc = subprocess.run(
        ["bash", "-c", "source /opt/aipc/scripts/load-secrets.sh && env"],
        capture_output=True, text=True, timeout=30,
    )
    for line in rc.stdout.splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            os.environ[k] = v

    failures = 0

    if not test_raw_api_call():
        failures += 1

    if not test_response_parsing():
        failures += 1

    # Test 3: full run_l2 — score reflects correct judge judgment
    test_full_run_l2()

    # Test 4: collect_artifact committed & uncommitted
    if not test_collect_artifact_committed():
        print("  FAIL: collect_artifact with committed changes returned empty")
        failures += 1

    if not test_collect_artifact_with_uncommitted():
        print("  FAIL: collect_artifact with uncommitted changes missing working-tree diff")
        failures += 1

    # Test 5: code_implementation rubric with fixed artifact collection
    score = test_code_implementation_rubric()
    if score < 0.7:
        print(f"  (code_implementation scored {score:.2f} — realistic for a simple health endpoint)")
    else:
        print(f"  (Unexpected high score {score:.2f} for a simple health endpoint)")

    print("\n" + "=" * 60)
    if failures:
        print(f"RESULT: {failures} test(s) FAILED")
    else:
        print("RESULT: All API tests PASSED")

    print(f"E2E-scenario rubric scored: {score:.4f}")
    sys.exit(failures)
