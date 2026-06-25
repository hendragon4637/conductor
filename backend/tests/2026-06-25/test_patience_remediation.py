from __future__ import annotations

from backend.evaluator.remediation import AttemptSnapshot, best_score, should_continue


def snapshots(scores: list[float], gate_outcome: str = "remediate") -> list[AttemptSnapshot]:
    return [AttemptSnapshot(l2_score=score, gate_outcome=gate_outcome) for score in scores]


def test_best_so_far_patience_stops_not_latest_comparison(monkeypatch):
    monkeypatch.setenv("REMEDIATION_PATIENCE", "2")
    monkeypatch.setenv("REMEDIATION_HARD_CAP", "10")
    monkeypatch.setenv("REMEDIATION_MIN_DELTA", "0.02")

    cont, reason = should_continue(snapshots([0.60, 0.50, 0.55, 0.58]))

    assert cont is False
    assert reason == "patience_exhausted"


def test_noisy_convergence_is_not_stopped_before_later_improvement(monkeypatch):
    monkeypatch.setenv("REMEDIATION_PATIENCE", "2")
    monkeypatch.setenv("REMEDIATION_HARD_CAP", "10")
    monkeypatch.setenv("REMEDIATION_MIN_DELTA", "0.02")

    cont, reason = should_continue(snapshots([0.50, 0.55, 0.52]))
    assert cont is True
    assert reason == "within_patience"

    cont, reason = should_continue(snapshots([0.50, 0.55, 0.52, 0.62]))
    assert cont is True
    assert reason == "within_patience"


def test_genuinely_stuck_sequence_stops_at_patience(monkeypatch):
    monkeypatch.setenv("REMEDIATION_PATIENCE", "2")
    monkeypatch.setenv("REMEDIATION_HARD_CAP", "10")
    monkeypatch.setenv("REMEDIATION_MIN_DELTA", "0.02")

    cont, reason = should_continue(snapshots([0.50, 0.50, 0.50]))

    assert cont is False
    assert reason == "patience_exhausted"


def test_hard_cap_stops_tiny_gain_sequence(monkeypatch):
    monkeypatch.setenv("REMEDIATION_PATIENCE", "20")
    monkeypatch.setenv("REMEDIATION_HARD_CAP", "10")
    monkeypatch.setenv("REMEDIATION_MIN_DELTA", "0.02")

    cont, reason = should_continue(snapshots([0.50 + i * 0.001 for i in range(10)]))

    assert cont is False
    assert reason == "hard_cap"


def test_pass_at_any_attempt_stops_with_passed_reason(monkeypatch):
    monkeypatch.setenv("REMEDIATION_PATIENCE", "2")
    monkeypatch.setenv("REMEDIATION_HARD_CAP", "10")
    monkeypatch.setenv("REMEDIATION_MIN_DELTA", "0.02")

    history = snapshots([0.50, 0.52])
    history.append(AttemptSnapshot(l2_score=0.80, gate_outcome="done"))

    cont, reason = should_continue(history)

    assert cont is False
    assert reason == "passed"


def test_best_score_reports_max_score():
    assert best_score(snapshots([0.60, 0.50, 0.55, 0.58])) == 0.60
