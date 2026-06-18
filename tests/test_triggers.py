"""File 11 — Triggers + Guardrails"""
import os

from dotenv import load_dotenv
import pytest

load_dotenv("/opt/aipc/conductor/.env")

from backend.triggers.scheduler import Scheduler


def _real_ids():
    """Return real project_id, session_id, agent_config_id from the DB."""
    import psycopg
    db = os.environ["DATABASE_URL"]
    with psycopg.connect(db) as c:
        with c.cursor() as cur:
            cur.execute(
                "SELECT project_id, session_id FROM sessions LIMIT 1"
            )
            row = cur.fetchone()
            cur.execute("SELECT agent_config_id FROM agent_configs LIMIT 1")
            a = cur.fetchone()
    return (
        row[0] if row else "default",
        row[1] if row else "default",
        a[0] if a else "opencode:backend-executor",
    )


def test_add_and_fire_enrich():
    """Add an enrich trigger, fire it, verify guardrails ran."""
    sch = Scheduler()
    pid, sid, acfg = _real_ids()

    tid = sch.add(
        name="smoke",
        cron="* * * * *",
        job_type="enrich",
        payload={"intents": ["list files in the current directory"]},
        project_id=pid,
        session_id=sid,
        agent_config_id=acfg,
        sandboxed=True,
    )

    assert isinstance(tid, str) and len(tid) == 36

    result = sch.fire(tid)

    assert result["status"] in ("ok", "budget_exceeded")

    if result["status"] == "ok":
        assert "enriched_count" in result
        assert result["enriched_count"] >= 1
        for r in result["results"]:
            assert "conv_id" in r
            assert "trace_id" in r
            assert "score" in r


def test_scheduler_list():
    """Scheduler.list_triggers returns persisted triggers."""
    sch = Scheduler()
    triggers = sch.list_triggers()
    assert isinstance(triggers, list)
    smokes = [t for t in triggers if t["name"] == "smoke"]
    assert len(smokes) >= 1


def test_ratchet_sweep_propose_only(monkeypatch):
    """ratchet_sweep with propose_only=True does not auto-apply."""
    import backend.triggers.jobs as jobs_mod
    import backend.ratchet.mutate as mutate_mod

    # Patch propose_mutation so it doesn't wait on the local LLM (slow).
    def _fast_stub(agent_cfg, traces):
        return {
            "target": "skill",
            "rationale": "stub — LLM call skipped in test",
            "candidate": "",
        }

    monkeypatch.setattr(mutate_mod, "propose_mutation", _fast_stub)
    # Also patch the module-level reference inside jobs.py
    monkeypatch.setattr(jobs_mod, "propose_mutation", _fast_stub)

    sch = Scheduler()
    pid, sid, acfg = _real_ids()

    tid = sch.add(
        name="sweep-test",
        cron="0 2 * * *",
        job_type="ratchet_sweep",
        payload={"propose_only": True, "min_runs": 1},
        project_id=pid,
        session_id=sid,
        agent_config_id=acfg,
        sandboxed=True,
    )

    result = sch.fire(tid)

    assert result["status"] in ("ok", "budget_exceeded")

    if result["status"] == "ok":
        assert "sweep_count" in result
        for r in result["results"]:
            assert "applied" in r
            assert r["applied"] is False
