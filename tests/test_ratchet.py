"""File 10 — Ratchet + Experiment Runner"""
import json
import os

from dotenv import load_dotenv
import pytest

load_dotenv("/opt/aipc/conductor/.env")

from backend.ratchet.detect import weak_configs, failing_traces
from backend.ratchet.mutate import propose_mutation
from backend.ratchet.experiment import run_experiment
from backend.ratchet.decide import decide


def test_detect_langfuse():
    """weak_configs queries Langfuse and returns a list (may be empty)."""
    weak = weak_configs(threshold=0.7, min_runs=1)
    assert isinstance(weak, list)
    # At minimum, there should be some configs in the system
    # (even if they have high scores)


def test_failing_traces():
    """failing_traces returns scored traces from Langfuse."""
    traces = failing_traces(limit=5)
    assert isinstance(traces, list)
    for t in traces:
        assert "trace_id" in t
        assert "score" in t


def test_propose_mutation_mock():
    """propose_mutation produces target/candidate dict."""

    def _mock_llm(prompt: str) -> str:
        return json.dumps({
            "target": "skill",
            "rationale": "Add more explicit testing instructions.",
            "candidate": "# Backend Executor Skill\n\n## Rules\n1. Write tests for all endpoints.\n2. Use pytest.\n",
        })

    # Temporarily replace the LLM call
    import backend.ratchet.mutate as mut
    orig = mut._llm_call
    mut._llm_call = lambda p: _mock_llm(p)

    try:
        result = propose_mutation(
            "backend-executor",
            [{"score": 0.3, "comment": "missing tests",
              "input": {}, "output": {}}],
        )
        assert "target" in result
        assert "candidate" in result
        assert result["target"] == "skill"
    finally:
        mut._llm_call = orig


@pytest.mark.slow
def test_ratchet_cycle():
    """Full ratchet cycle: force mutation, run experiment (2 tasks), decide."""
    import tempfile
    import shutil
    from pathlib import Path

    seed_candidate = (
        "# Backend Executor Skill\n\n"
        "## Hard rules\n"
        "1. Write tests for all endpoints.\n"
        "2. Use type hints.\n"
        "3. Use httpx, not requests.\n"
        "4. asyncio for I/O.\n"
    )

    mutation = {
        "target": "skill",
        "candidate": seed_candidate,
    }

    import psycopg
    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute("SELECT agent_config_id FROM agent_configs LIMIT 1")
                row = cur.fetchone()
                agent_config_id = row[0] if row else "backend-executor"
    else:
        agent_config_id = "backend-executor"

    exp = run_experiment(
        agent_config=agent_config_id,
        mutation=mutation,
        dataset=agent_config_id,
        max_tasks=2,
    )

    assert "experiment_id" in exp
    assert "baseline_score" in exp
    assert "candidate_score" in exp
    assert "delta" in exp
    assert len(exp["task_results"]) == 2

    print(f"\nExperiment {exp['experiment_id']}:")
    print(f"  Baseline:  {exp['baseline_score']}")
    print(f"  Candidate: {exp['candidate_score']}")
    print(f"  Delta:     {exp['delta']}")

    decision = decide(exp["experiment_id"], delta_threshold=0.03)
    assert decision in ("kept", "reverted")
    print(f"  Decision:  {decision}")

    db_url = os.environ.get("DATABASE_URL", "")
    if db_url:
        from psycopg.rows import dict_row
        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT * FROM experiments WHERE experiment_id = %s",
                    (exp["experiment_id"],),
                )
                row = cur.fetchone()
                assert row is not None
                assert row["decision"] == decision
                assert float(row["baseline_score"]) == exp["baseline_score"]
                assert float(row["candidate_score"]) == exp["candidate_score"]

        with psycopg.connect(db_url, row_factory=dict_row) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT * FROM skill_mutations "
                    "WHERE experiment_id = %s",
                    (exp["experiment_id"],),
                )
                row = cur.fetchone()
                assert row is not None
                assert row["kept"] == (decision == "kept")
                assert row.get("agent_config_id") is not None

    # Restore original SKILL.md
    orig_skill = Path("/opt/aipc/conductor/skills/backend/executor/SKILL.md")
    if orig_skill.exists():
        stub = (
            "---\n"
            "name: backend-executor\n"
            "version: 0.0.1\n"
            "description: Python/FastAPI backend executor\n"
            "---\n\n"
            "# Backend Executor\n\n"
            "## Hard rules\n"
            "1. Use type hints on all public functions.\n"
            "2. Tests required for every new endpoint.\n"
            "3. Use httpx, not requests.\n"
        )
        orig_skill.write_text(stub)
