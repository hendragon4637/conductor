"""Tests for shared/l4_models.py — pure function tests for L4 models and helpers.

Covers: Scenario/Finding/L4Report model validation, hash_spec, spec_hash_from_run,
scenarios_to_json, report_consistent (6 checks), and resolve_where_paths.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from shared.l4_models import (
    L4Report,
    Scenario,
    ScenarioResult,
    Finding,
    hash_spec,
    spec_hash_from_run,
    scenarios_to_json,
    report_consistent,
    resolve_where_paths,
    MAX_ADHOC,
)


# ── Scenarios ─────────────────────────────────────────────────────────────

class TestScenario:
    def test_valid_scenario(self):
        s = Scenario(id="s1", as_a="developer", wants="deploy the app", success_looks_like="app is live")
        assert s.id == "s1"
        assert s.source == "seeded"

    def test_adhoc_source(self):
        s = Scenario(id="s2", source="adhoc", as_a="user", wants="login", success_looks_like="dashboard visible")
        assert s.source == "adhoc"

    def test_as_a_min_length(self):
        import pydantic
        try:
            Scenario(id="s3", as_a="ab", wants="login", success_looks_like="dashboard visible")
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass

    def test_wants_min_length(self):
        import pydantic
        try:
            Scenario(id="s4", as_a="user", wants="abcd", success_looks_like="dashboard visible")
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass


# ── Findings ──────────────────────────────────────────────────────────────

class TestFinding:
    def test_valid_finding(self):
        f = Finding(
            what="The submit button is unresponsive",
            where=["src/button.tsx"],
            why="Observed: clicking submit does nothing for 10s",
            severity="high",
            scenario_id="s1",
        )
        assert f.severity == "high"

    def test_where_min_length_enforced(self):
        import pydantic
        try:
            Finding(
                what="The submit button is unresponsive",
                where=[],
                why="Observed: clicking submit does nothing",
                severity="low",
                scenario_id="s1",
            )
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass

    def test_what_min_length_enforced(self):
        import pydantic
        try:
            Finding(
                what="Short",  # < 10 chars
                where=["src/button.tsx"],
                why="Observed: clicking submit does nothing",
                severity="low",
                scenario_id="s1",
            )
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass

    def test_why_min_length_enforced(self):
        import pydantic
        try:
            Finding(
                what="The submit button is unresponsive",
                where=["src/button.tsx"],
                why="Short",  # < 10 chars
                severity="low",
                scenario_id="s1",
            )
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass

    def test_severity_literal(self):
        import pydantic
        try:
            Finding(
                what="The submit button is unresponsive",
                where=["src/button.tsx"],
                why="Observed: clicking submit does nothing",
                severity="critical",  # not in Literal
                scenario_id="s1",
            )
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass


# ── L4Report ──────────────────────────────────────────────────────────────

class TestL4Report:
    def test_valid_report(self):
        report = L4Report(
            verdict="pass",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked button"], outcome="pass"),
            ],
            findings=[],
            observations=["Everything looks good."],
        )
        assert report.verdict == "pass"
        assert len(report.findings) == 0
        assert len(report.observations) == 1

    def test_verdict_literal(self):
        import pydantic
        try:
            L4Report(
                verdict="unknown",
                scenario_results=[],
            )
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass

    def test_scenario_result_must_have_at_least_one_attempt(self):
        import pydantic
        try:
            ScenarioResult(scenario_id="s1", attempted=[], outcome="pass")
            assert False, "expected ValidationError"
        except pydantic.ValidationError:
            pass

    def test_default_findings_empty(self):
        report = L4Report(verdict="pass", scenario_results=[])
        assert report.findings == []

    def test_default_observations_empty(self):
        report = L4Report(verdict="pass", scenario_results=[])
        assert report.observations == []


# ── hash_spec ─────────────────────────────────────────────────────────────

class TestHashSpec:
    def test_deterministic(self):
        assert hash_spec("goal", "spec") == hash_spec("goal", "spec")

    def test_different_inputs_different_hash(self):
        h1 = hash_spec("goal A", "spec A")
        h2 = hash_spec("goal B", "spec B")
        assert h1 != h2

    def test_none_goal(self):
        h = hash_spec(None, "spec")
        assert isinstance(h, str)
        assert len(h) == 16

    def test_none_spec(self):
        h = hash_spec("goal", None)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_both_none(self):
        h = hash_spec(None, None)
        assert isinstance(h, str)
        assert len(h) == 16

    def test_hex_chars_only(self):
        h = hash_spec("goal", "spec")
        assert all(c in "0123456789abcdef" for c in h)


# ── spec_hash_from_run ───────────────────────────────────────────────────

class TestSpecHashFromRun:
    def test_from_run_dict(self):
        run = {"goal": "deploy app", "spec": "must be fast"}
        assert spec_hash_from_run(run) == hash_spec("deploy app", "must be fast")

    def test_missing_keys(self):
        run = {"goal": "deploy app"}
        h = spec_hash_from_run(run)
        assert h == hash_spec("deploy app", None)

    def test_empty_run(self):
        h = spec_hash_from_run({})
        assert h == hash_spec(None, None)


# ── scenarios_to_json ────────────────────────────────────────────────────

class TestScenariosToJson:
    def test_serializes_correctly(self):
        scenarios = [
            Scenario(id="s1", as_a="user", wants="login", success_looks_like="dashboard"),
        ]
        result = scenarios_to_json(scenarios)
        parsed = json.loads(result)
        assert len(parsed) == 1
        assert parsed[0]["id"] == "s1"
        assert parsed[0]["source"] == "seeded"

    def test_valid_json_output(self):
        scenarios = [
            Scenario(id="s1", as_a="dev", wants="deploy", success_looks_like="live site"),
            Scenario(id="s2", source="adhoc", as_a="qa user", wants="run tests", success_looks_like="passed ok"),
        ]
        result = scenarios_to_json(scenarios)
        json.loads(result)  # should not raise

    def test_empty_list(self):
        assert scenarios_to_json([]) == "[]"


# ── report_consistent: 6 deterministic checks ────────────────────────────

class TestReportConsistent:
    def _make_scenario(self, sid: str) -> Scenario:
        return Scenario(id=sid, as_a="user", wants="do something", success_looks_like="done well")

    def test_all_check_pass(self):
        """All 6 checks pass."""
        seeded = [self._make_scenario("s1"), self._make_scenario("s2")]
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
                ScenarioResult(scenario_id="s2", attempted=["typed"], outcome="pass"),
            ],
            findings=[
                Finding(
                    what="Button does not respond",
                    where=["src/button.tsx"],
                    why="Observed no response on click",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        assert report_consistent(report, seeded) is None

    # Check 1: Every seeded scenario must have a result
    def test_missing_result_for_seeded_scenario(self):
        seeded = [self._make_scenario("s1"), self._make_scenario("s2")]
        report = L4Report(
            verdict="pass",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="pass"),
                # s2 missing
            ],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "s2" in err

    # Check 2: Adhoc scenario count cap
    def test_too_many_adhoc_scenarios(self):
        seeded = [self._make_scenario("s1")]
        adhoc_results = [
            ScenarioResult(scenario_id=f"adhoc_{i}", attempted=["x"], outcome="pass")
            for i in range(MAX_ADHOC + 1)
        ]
        report = L4Report(
            verdict="pass",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="pass"),
                *adhoc_results,
            ],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "adhoc" in err.lower()

    # Check 3: Every finding must reference a known scenario_id
    def test_finding_references_unknown_scenario(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Something is wrong",
                    where=["src/x.ts"],
                    why="Observed a failure",
                    severity="high",
                    scenario_id="unknown_scenario",
                ),
            ],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "unknown" in err

    # Check 4: Failed/blocked scenario must have at least one finding
    def test_failed_scenario_without_finding(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
            ],
            findings=[],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "no finding" in err

    def test_blocked_scenario_without_finding(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="partial",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="blocked"),
            ],
            findings=[],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "no finding" in err

    # Check 5: verdict=pass must have empty findings
    def test_pass_verdict_with_findings(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="pass",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="pass"),
            ],
            findings=[
                Finding(
                    what="Minor issue found",
                    where=["src/x.ts"],
                    why="Observed a minor problem",
                    severity="low",
                    scenario_id="s1",
                ),
            ],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "pass" in err

    # Check 6: verdict=partial with high-severity findings, or negative verdict with no findings
    def test_partial_with_high_severity(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="partial",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Major issue found",
                    where=["src/x.ts"],
                    why="Observed a critical problem",
                    severity="high",
                    scenario_id="s1",
                ),
            ],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        assert "high" in err

    def test_partial_with_no_findings(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="partial",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
            ],
            findings=[],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        # Check #4 fires before #6: "scenario 's1' fail with no finding"
        assert "no finding" in err

    def test_fail_with_no_findings(self):
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="fail",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
            ],
            findings=[],
        )
        err = report_consistent(report, seeded)
        assert err is not None
        # Check #4 fires before #6: "scenario 's1' fail with no finding"
        assert "no finding" in err

    def test_partial_with_low_findings_is_ok(self):
        """partial + low/medium findings is valid."""
        seeded = [self._make_scenario("s1")]
        report = L4Report(
            verdict="partial",
            scenario_results=[
                ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
            ],
            findings=[
                Finding(
                    what="Minor issue found",
                    where=["src/x.ts"],
                    why="Observed a minor problem",
                    severity="low",
                    scenario_id="s1",
                ),
            ],
        )
        assert report_consistent(report, seeded) is None


# ── resolve_where_paths ─────────────────────────────────────────────────

class TestResolveWherePaths:
    def test_all_paths_resolve(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            (wt / "src" / "button.tsx").write_text("")
            (wt / "README.md").write_text("")
            report = L4Report(
                verdict="fail",
                scenario_results=[
                    ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
                ],
                findings=[
                    Finding(
                        what="Button issue",
                        where=["src/button.tsx", "README.md"],
                        why="Observed failure on click",
                        severity="high",
                        scenario_id="s1",
                    ),
                ],
            )
            assert resolve_where_paths(report, tmp) is True

    def test_missing_path_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            report = L4Report(
                verdict="fail",
                scenario_results=[
                    ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
                ],
                findings=[
                    Finding(
                        what="Button issue",
                        where=["src/missing.tsx"],
                        why="Observed failure on click",
                        severity="high",
                        scenario_id="s1",
                    ),
                ],
            )
            assert resolve_where_paths(report, tmp) is False

    def test_resolves_directory_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            report = L4Report(
                verdict="fail",
                scenario_results=[
                    ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
                ],
                findings=[
                    Finding(
                        what="Directory issue",
                        where=["src"],
                        why="Observed layout problem",
                        severity="medium",
                        scenario_id="s1",
                    ),
                ],
            )
            assert resolve_where_paths(report, tmp) is True

    def test_empty_findings_returns_true(self):
        with tempfile.TemporaryDirectory() as tmp:
            report = L4Report(
                verdict="pass",
                scenario_results=[
                    ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="pass"),
                ],
                findings=[],
            )
            assert resolve_where_paths(report, tmp) is True

    def test_partial_path_match_fails(self):
        """If any single where path fails to resolve, the whole check fails."""
        with tempfile.TemporaryDirectory() as tmp:
            wt = Path(tmp)
            (wt / "src").mkdir()
            report = L4Report(
                verdict="fail",
                scenario_results=[
                    ScenarioResult(scenario_id="s1", attempted=["clicked"], outcome="fail"),
                ],
                findings=[
                    Finding(
                        what="Partial issue",
                        where=["src", "docs/missing.md"],
                        why="Observed missing docs",
                        severity="low",
                        scenario_id="s1",
                    ),
                ],
            )
            assert resolve_where_paths(report, tmp) is False
