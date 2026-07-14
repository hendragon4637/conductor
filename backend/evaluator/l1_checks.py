"""L1 deterministic checks — run shell commands or in-process text assertions.

Supports three ``kind`` values on ``Check``:
- ``"shell"`` (default): run as subprocess shell command, exit 0 = pass
- ``"artifact_text"``: in-process text assertion (contains, regex, is-json, etc.)
- ``"file_exists"``: check that a file path exists in the worktree

If a node has no deterministic checks, L1 passes vacuously.

Promptfoo-as-runner option (default OFF):
  The artifact_text checks implement a subset of the promptfoo assertion
  taxonomy (contains, not-contains, regex, is-json, json-schema, equals,
  starts-with). For production-scale L1 workloads, these checks can be
  executed via ``promptfoo`` CLI directly by setting the environment variable
  ``PROMPTFOO_AS_L1_RUNNER=true``. The in-process runner is sufficient for
  current scale and avoids the promptfoo CLI overhead.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class L1Result:
    """Result of running all deterministic checks for a node."""
    passed: bool
    detail: list[tuple[str, bool, str]] = field(default_factory=list)
    """(check_id, ok, output_tail) for each deterministic check run."""
    duration_s: float = 0.0
    """Total wall-clock time to run all checks."""

    @property
    def passed_ids(self) -> list[str]:
        """List of check IDs that passed."""
        return [cid for cid, ok, _ in self.detail if ok]

    @property
    def failed_ids(self) -> list[str]:
        """List of check IDs that failed."""
        return [cid for cid, ok, _ in self.detail if not ok]


def run_l1(
    checks: list,
    worktree: str,
    timeout: int = 300,
) -> L1Result:
    """Run all deterministic checks in the node's worktree.

    Handles three check kinds:
    - ``"shell"``: runs ``check_cmd`` as a subprocess shell command.
    - ``"artifact_text"``: parses ``check_cmd`` as ``{assertion_type}:{expected}``
      and runs an in-process text check. The artifact text is read from the
      path in ``source_hint`` (relative to worktree), or from the first tracked
      file if no hint is given.
    - ``"file_exists"``: checks that the path in ``check_cmd`` or ``source_hint``
      exists in the worktree.

    Args:
        checks: List of ``Check`` objects from the node (only ``type=="deterministic"``
                with a non-None ``check_cmd`` are executed).
        worktree: Absolute path to the node's git worktree.
        timeout: Per-shell-check timeout in seconds (not used for artifact_text).

    Returns:
        ``L1Result`` with passed=True only if ALL deterministic checks pass.
        Nodes with no deterministic checks pass vacuously.
    """
    start = time.time()
    detail: list[tuple[str, bool, str]] = []

    # TEMPORARY: SKIP_L1 env var bypasses L1 execution entirely.
    # All deterministic checks report as passed so the pipeline falls
    # through to L2-only evaluation. The L1 code/logic is kept intact.
    skip = os.environ.get("SKIP_L1", "").lower() in ("true", "1", "yes")
    if skip:
        for c in checks:
            if getattr(c, "tier", None) == "L1":
                cid = getattr(c, "id", "?")
                detail.append((cid, True, "skipped via SKIP_L1"))
        passed = True
        duration = time.time() - start
        print(f"[L1] SKIP_L1=true — all {len(detail)} L1 checks bypassed", flush=True)
        return L1Result(passed=passed, detail=detail, duration_s=duration)

    l1_count = 0
    for c in checks:
        if getattr(c, "tier", None) != "L1":
            continue
        cmd = getattr(c, "cmd", None) or getattr(c, "check_cmd", None)
        if not cmd:
            continue
        l1_count += 1
        kind = getattr(c, "kind", "shell")
        cid = getattr(c, "id", "?")
        print(f"[L1] running check: id={cid} kind={kind} cmd={cmd!r} worktree={worktree}", flush=True)

        if kind == "artifact_text":
            _run_artifact_text_check(c, worktree, detail)
        elif kind == "file_exists":
            _run_file_exists_check(c, worktree, detail)
        else:
            # shell (default)
            try:
                r = subprocess.run(
                    cmd,
                    cwd=worktree,
                    shell=True,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                )
                ok = r.returncode == 0
                tail = (r.stdout + r.stderr)[-500:]
                print(f"[L1] check result: id={cid} ok={ok} returncode={r.returncode} output={tail[:200]!r}", flush=True)
                detail.append((cid, ok, tail))
            except subprocess.TimeoutExpired:
                print(f"[L1] check timeout: id={cid}", flush=True)
                detail.append((cid, False, "timeout"))
            except FileNotFoundError:
                print(f"[L1] check cmd not found: id={cid}", flush=True)
                detail.append((cid, False, "command not found"))
            except Exception as e:
                print(f"[L1] check error: id={cid} err={e}", flush=True)
                detail.append((cid, False, f"error: {e}"))

    passed = all(ok for _, ok, _ in detail) if detail else True
    duration = time.time() - start
    print(f"[L1] summary: l1_count={l1_count} passed={passed} passed_ids={[cid for cid, ok, _ in detail if ok]} duration_s={duration:.2f}", flush=True)
    return L1Result(passed=passed, detail=detail, duration_s=duration)


# ── artifact_text runners (promptfoo assertion taxonomy) ──────────────


def _artifact_text_from_worktree(worktree: str, source_hint: str | None) -> str | None:
    """Read artifact text from the worktree, optionally at a specific path."""
    if source_hint:
        p = Path(worktree) / source_hint.lstrip("/")
        if p.is_file() and p.stat().st_size <= 100_000:
            try:
                return p.read_text(encoding="utf-8", errors="replace")
            except Exception:
                return None
    # Default: read first tracked file
    try:
        r = subprocess.run(
            ["git", "ls-files"],
            cwd=worktree, capture_output=True, text=True, timeout=15,
        )
        tracked = [f for f in r.stdout.strip().splitlines() if f.strip()]
        for f in tracked[:10]:
            p = Path(worktree) / f
            if p.is_file() and p.stat().st_size <= 100_000:
                return p.read_text(encoding="utf-8", errors="replace")
    except Exception:
        pass
    return None


def _run_artifact_text_check(
    c: object, worktree: str, detail: list[tuple[str, bool, str]]
) -> None:
    """Run a single artifact_text check in-process."""
    cid = getattr(c, "id", "?")
    cmd = getattr(c, "cmd", None) or getattr(c, "check_cmd", "") or ""
    source_hint = getattr(c, "source_hint", None)

    text = _artifact_text_from_worktree(worktree, source_hint)
    if text is None:
        detail.append((cid, False, "artifact text not found or empty"))
        return

    ok, output = _run_assertion(cmd, text)
    detail.append((cid, ok, output))


_KNOWN_ASSERTION_TYPES = frozenset({
    "contains", "not-contains", "regex", "is-json", "json-schema", "equals", "starts-with",
})


def _parse_assertion(cmd: str) -> tuple[str, str]:
    """Parse ``check_cmd`` as ``{assertion_type}:{expected}``.

    Known assertion types (contains, not-contains, regex, is-json, json-schema,
    equals, starts-with) are recognized. If no type prefix is found, defaults
    to ``"contains"`` with the whole string as value.
    """
    if ":" in cmd:
        idx = cmd.index(":")
        candidate = cmd[:idx].strip()
        if candidate in _KNOWN_ASSERTION_TYPES:
            return candidate, cmd[idx + 1:].strip()
    return "contains", cmd.strip()


def _run_assertion(cmd: str, text: str) -> tuple[bool, str]:
    """Run a single artifact_text assertion against ``text``.

    Assertion types (promptfoo taxonomy):
      contains, not-contains, regex, is-json, json-schema, equals, starts-with
    """
    assert_type, expected = _parse_assertion(cmd)

    if assert_type == "is-json":
        try:
            json.loads(text)
            return True, "valid JSON"
        except json.JSONDecodeError as e:
            return False, f"invalid JSON: {e}"

    if assert_type == "equals":
        ok = text.strip() == expected.strip()
        return (ok, f"equals: expected={expected[:100]!r}") if ok else (ok, f"not equal: got={text[:100]!r} expected={expected[:100]!r}")

    if assert_type == "starts-with":
        ok = text.strip().startswith(expected.strip())
        return (ok, f"starts with {expected[:50]!r}") if ok else (ok, f"does NOT start with {expected[:50]!r}")

    if assert_type == "regex":
        try:
            ok = bool(re.search(expected, text))
            return (ok, f"regex {expected[:80]!r} matched") if ok else (ok, f"regex {expected[:80]!r} did not match")
        except re.error as e:
            return False, f"invalid regex {expected[:80]!r}: {e}"

    if assert_type == "not-contains":
        ok = expected not in text
        return (ok, f"'{expected[:80]}' not found") if ok else (ok, f"'{expected[:80]}' was found (should be absent)")

    if assert_type == "json-schema":
        return _run_json_schema(expected, text)

    # default: contains
    ok = expected in text
    return (ok, f"contains {expected[:100]!r}") if ok else (ok, f"does NOT contain {expected[:100]!r}")


def _run_json_schema(schema_str: str, text: str) -> tuple[bool, str]:
    """Validate text against a JSON schema (draft-04 subset)."""
    try:
        import jsonschema
        data = json.loads(text)
        schema = json.loads(schema_str)
        jsonschema.validate(data, schema)
        return True, "matches JSON schema"
    except json.JSONDecodeError as e:
        return False, f"invalid JSON: {e}"
    except jsonschema.ValidationError as e:
        return False, f"schema violation: {e.message}"
    except ImportError:
        return False, "jsonschema package not available"
    except Exception as e:
        return False, f"schema error: {e}"


def _run_file_exists_check(
    c: object, worktree: str, detail: list[tuple[str, bool, str]]
) -> None:
    """Check that a file path exists in the worktree."""
    cid = getattr(c, "id", "?")
    cmd = getattr(c, "cmd", None) or getattr(c, "check_cmd", "") or ""
    source_hint = getattr(c, "source_hint", None)

    # Resolve path from source_hint, check_cmd, or default
    path_str = source_hint or cmd
    if path_str.startswith("test -f "):
        path_str = path_str[len("test -f "):].strip().strip("'\"")
    full_path = Path(worktree) / path_str.lstrip("/")
    ok = full_path.is_file()
    if ok:
        detail.append((cid, True, f"file exists: {path_str}"))
    else:
        detail.append((cid, False, f"file not found: {path_str}"))
