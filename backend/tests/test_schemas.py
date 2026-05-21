"""Smoke tests for the JSON Schema validators."""
import pytest
from backend.services.schema_validator import validate, assert_valid


def test_reformulated_task_valid_minimal():
    instance = {
        "spec_version": "1.0.0",
        "task_id": "00000000-0000-0000-0000-000000000001",
        "user_intent": "Add /auth/refresh endpoint",
        "domain": "backend",
        "intent_type": "implement",
    }
    assert validate("reformulated_task", instance) == []


def test_reformulated_task_missing_required():
    instance = {"spec_version": "1.0.0"}
    errs = validate("reformulated_task", instance)
    assert any("task_id" in e for e in errs)
    assert any("user_intent" in e for e in errs)


def test_routing_rules_valid_PEV():
    instance = {
        "on_success": [
            {"next_config": "opencode:backend-executor", "pass": "output_spec"}
        ],
        "on_failure": [
            {"next_config": "opencode:backend-planner", "max_retries": 2},
            {"next_config": None, "escalate": "human_review"}
        ]
    }
    assert validate("routing_rules", instance) == []


def test_routing_rules_valid_designer_critic_loop():
    instance = {
        "on_success": [
            {
                "condition": "output_spec.verdict == 'approved'",
                "next_config": None,
                "terminates_task": True
            },
            {
                "condition": "output_spec.verdict == 'rejected'",
                "next_config": "opencode:ui-design-designer",
                "pass": "output_spec"
            }
        ],
        "max_iterations": 3
    }
    assert validate("routing_rules", instance) == []


def test_contribution_receipt_valid():
    instance = {
        "spec_version": "1.0.0",
        "task_id": "00000000-0000-0000-0000-000000000001",
        "status": "completed",
        "summary": "Implemented /auth/refresh endpoint with tests."
    }
    assert validate("contribution_receipt", instance) == []


def test_assert_valid_raises_on_invalid():
    with pytest.raises(ValueError) as exc:
        assert_valid("contribution_receipt", {"spec_version": "1.0.0"})
    assert "task_id" in str(exc.value)
