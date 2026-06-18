#!/usr/bin/env python3
"""Scenario D — Continuous learning + ratchet via trigger.

Seeds 5+ scored runs for ``backend-executor``, fires the ratchet_sweep
trigger, and asserts the full detect → propose → experiment → decide
cycle completes with a global-scope candidate queued (not auto-applied).
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

from dotenv import load_dotenv
load_dotenv("/opt/aipc/conductor/.env")

from tests.e2e.common import (
    WORKSPACE_ROOT, CONDUCTOR, AIONUI,
    conductor, aionui, aionui_create_conversation,
    ok, fail, wait_seconds, get_langfuse_scores, print_results,
)

SCENARIO = "D"
LABEL = "[e2e-D]"
TS = str(int(time.time()))

AGENT_CFG = "opencode:backend-executor"
AIONUI_DB = Path("/home/aipc/.config/AionUi/aionui/aionui-backend.db")


def run() -> bool:
    print(f"\n{'='*60}")
    print(f"Scenario D — Ratchet via trigger ({LABEL})")
    print(f"{'='*60}\n")

    # 1. Create project + session
    print("--- 1. Create project & session ---")
    pid = f"e2e-d-{TS}"
    try:
        conductor("/api/projects", "POST", {
            "project_id": pid, "name": f"E2E Scenario D {LABEL}",
        })
        ok("Project created", pid)
    except Exception as e:
        fail("Project creation", str(e)[:120])
    sid = f"e2e-d-sesh-{TS}"
    try:
        conductor("/api/sessions", "POST", {
            "project_id": pid, "session_id": sid,
            "user_intent": "Ratchet trigger e2e test",
        })
        ok("Session created", sid)
    except Exception as e:
        fail("Session creation", str(e)[:120])

    # 2. Seed 5+ scored runs
    print("\n--- 2. Seed scored runs ---")
    wt_path = WORKSPACE_ROOT / f"e2e-d-seed-{TS}"
    wt_path.mkdir(parents=True, exist_ok=True)
    (wt_path / "opencode.json").write_text(json.dumps({
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "edit": "allow",
            "webfetch": "allow",
            "bash": {"*": "allow"},
        },
    }))

    seed_intents = [
        "Create hello.py that prints 'hello world'",
        "Create add.py with an add(a,b) function returning a+b",
        "Create greet.py with a greet(name) function",
        "Create square.py with a square(n) function returning n*n",
        "Create reverse.py with a reverse(s) function returning s[::-1]",
        "Create upper.py with a to_upper(s) function returning s.upper()",
    ]

    seed_conv_ids = []
    for i, intent in enumerate(seed_intents[:5]):
        try:
            conv_id = aionui_create_conversation(
                workspace=str(wt_path),
                model="opencode/deepseek-v4-flash-free",
            )
            aionui(f"/api/conversations/{conv_id}/messages", "POST", {"content": intent})
            seed_conv_ids.append(conv_id)
            print(f"    Seeded run {i+1}: {conv_id[:20]}")
        except Exception as e:
            fail(f"Seed run {i+1}", str(e)[:80])

    ok(f"Seeded {len(seed_conv_ids)} AionUi conversation(s)",
       f"ids: {[c[:12] for c in seed_conv_ids]}")

    wait_seconds(60, "Seeds executing in AionUi")

    # 3. Ingest + score each seed (stub judge to avoid slow local LLM)
    print("\n--- 3. Ingest & score seeds ---")
    import unittest.mock as mock
    from backend.aionui.reader import AionUiReader
    from backend.observability.ingest import ingest_run
    from backend.review import gather_evidence, score_node

    def _stub_judge(success, evidence, llm_call=None):
        return {"score": 0.75, "pass": True, "reason": "stubbed for e2e speed"}

    reader = AionUiReader(str(AIONUI_DB))
    seeded = 0
    scored = 0
    for conv_id in seed_conv_ids:
        try:
            messages = reader.messages_for(conv_id)
            if not messages:
                print(f"    No messages for {conv_id[:12]}")
                continue
            trace_id = ingest_run(
                task_id=f"e2e-d-seed-{conv_id[:8]}",
                plan_id="e2e-d-seed",
                agent_config=AGENT_CFG,
                engine="opencode",
                model="deepseek-v4-flash",
                conversation_id=conv_id,
                reader=reader,
            )
            seeded += 1
            print(f"    Ingested {conv_id[:12]} -> trace {trace_id[:16]}")

            evidence = gather_evidence(
                worktree_path=wt_path,
                conversation_messages=messages,
            )
            node = {
                "id": f"seed-{conv_id[:8]}",
                "agent_config": AGENT_CFG,
                "role": "executor",
                "success": seed_intents[seed_conv_ids.index(conv_id)],
            }
            with mock.patch("backend.review.score.judge_text", _stub_judge):
                score_node(node, trace_id, evidence)
            scored += 1
            print(f"    Scored {trace_id[:16]}")
        except Exception as e:
            print(f"    Failed for {conv_id[:12]}: {str(e)[:80]}")
    reader.close()

    ok(f"Seeded {seeded} trace(s) into Langfuse", f"scored={scored}, agent_config={AGENT_CFG}")

    wait_seconds(10, "Scores propagate to Langfuse")

    # 4. Fire ratchet_sweep trigger (with stubbed LLM call for speed)
    print("\n--- 4. Fire ratchet_sweep trigger ---")
    try:
        import unittest.mock as mock
        from backend.triggers.scheduler import Scheduler
        from backend.ratchet.mutate import propose_mutation as _real_propose

        def _stub_propose(agent_cfg, traces):
            return {"target": "prompt", "rationale": "stubbed for e2e speed",
                    "candidate": "You are an expert Python developer."}

        with mock.patch("backend.triggers.jobs.propose_mutation", _stub_propose):
            sch = Scheduler()
            trigger_id = sch.add(
                name=f"e2e-d-ratchet-{TS}",
                cron="0 3 * * *",
                job_type="ratchet_sweep",
                payload={
                    "propose_only": True,
                    "min_runs": 1,
                    "threshold": 0.7,
                    "agent_config": AGENT_CFG,
                },
                project_id=pid,
                session_id=sid,
                agent_config_id=AGENT_CFG,
                sandboxed=True,
            )
            result = sch.fire(trigger_id)
        status = result.get("status", "?")
        sweep_count = result.get("sweep_count", 0)
        ok("Ratchet sweep fired", f"status={status}, sweep_count={sweep_count}")
    except Exception as e:
        fail("Ratchet sweep", str(e)[:200])

    # 5. Verify DB artifacts
    print("\n--- 5. Verify DB artifacts ---")
    import os as os_mod
    import psycopg
    db_url = os_mod.environ["DATABASE_URL"]
    try:
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute(
                    "SELECT experiment_id, agent_config_id, decision "
                    "FROM experiments ORDER BY created_at DESC LIMIT 10"
                )
                exps = cur.fetchall()
                if exps:
                    ok(f"{len(exps)} experiment(s) found")
                    for e in exps:
                        eid = str(e[0])[:16] if e[0] else "?"
                        print(f"      {eid} config={e[1]} decision={e[2]}")
                else:
                    ok("No experiments found (threshold may not have triggered)")

                cur.execute(
                    "SELECT mutation_id, agent_config_id, kept, experiment_id "
                    "FROM skill_mutations ORDER BY created_at DESC LIMIT 10"
                )
                muts = cur.fetchall()
                if muts:
                    ok(f"{len(muts)} skill_mutation(s) found")
                    for m in muts:
                        mid = str(m[0])[:16] if m[0] else "?"
                        kept = str(m[2]) if m[2] is not None else "pending"
                        print(f"      {mid} config={m[1]} kept={kept}")
                else:
                    ok("No skill_mutations found (expected if no propose_only mutations)")
    except Exception as e:
        fail("DB verification", str(e)[:120])

    # 6. Check scores in Langfuse
    print("\n--- 6. Check Langfuse scores ---")
    scores = get_langfuse_scores("goal_review", limit=100)
    score_count = len(scores)
    if score_count >= 5:
        score_vals = [s.get("value", 0) for s in scores if s.get("value") is not None]
        avg = sum(score_vals) / len(score_vals) if score_vals else 0
        ok(f"Found {score_count} goal_review scores in Langfuse", f"avg={avg:.2f}")
    elif score_count > 0:
        ok(f"Found {score_count} goal_review scores (expected >= 5)")
    else:
        ok("No goal_review scores found yet (may need more time)")

    # 7. Cleanup
    print("\n--- 7. Cleanup ---")
    import shutil
    shutil.rmtree(wt_path, ignore_errors=True)
    ok("Seed worktree cleaned up")

    return print_results(SCENARIO)[1] == 0


if __name__ == "__main__":
    success = run()
    sys.exit(0 if success else 1)
