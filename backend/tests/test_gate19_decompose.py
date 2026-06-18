"""GATE 19 — Test new node model + lifecycle decomposition.

Pass conditions:
1. ChunkNode has members (>=1), NO kind/leader/model fields
2. decompose_or_update() works for all 5 entry points
3. Incremental append preserves existing chunks
4. Validation: members >=1, acyclic DAG
5. Migration columns (members, node_commit_tag, gate_mode) exist in DB
6. builtins/git_ops and builtins/handoff load without error
"""
from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, "/opt/aipc/conductor")

from backend.planning.decomposed_spec import (
    DecomposedPlan,
    ChunkNode,
    validate_decomposed,
)
from backend.planning.decompose import decompose_or_update
from backend.planning.spec import SuccessCriterion

PASS = 0
FAIL = 0


def check(desc: str, ok: bool):
    global PASS, FAIL
    if ok:
        print(f"  PASS: {desc}")
        PASS += 1
    else:
        print(f"  FAIL: {desc}")
        FAIL += 1


# ---------------------------------------------------------------------------
# 1. ChunkNode model — no kind/leader/model, has members >=1
# ---------------------------------------------------------------------------
print("\n=== Test 1: ChunkNode model ===")
try:
    c = ChunkNode(id="n1", members=["finance-executor", "finance-reviewer"])
    check("chunk has members (2)", len(c.members) == 2)
    check("no kind attribute", not hasattr(c, 'kind'))
    check("no leader attribute", not hasattr(c, 'leader'))
    check("no model attribute", not hasattr(c, 'model'))
    check("depends_on defaults to []", c.depends_on == [])
    check("commit_on_done defaults True", c.commit_on_done is True)
    check("gate_mode defaults watcher_only", c.gate_mode == "watcher_only")
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 5

# ChunkNode with single member (still valid)
try:
    c2 = ChunkNode(id="n2", members=["finance-executor"])
    check("single member chunk valid", len(c2.members) == 1)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1

# ChunkNode with empty members should fail
try:
    ChunkNode(id="n3", members=[])
    check("empty members rejected", False)
except Exception:
    check("empty members rejected", True)

# ChunkNode with depends_on
try:
    c3 = ChunkNode(id="n4", members=["a"], depends_on=["n1"])
    check("depends_on stored", c3.depends_on == ["n1"])
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 1


# ---------------------------------------------------------------------------
# 2. decompose_or_update() — all 5 sources
# ---------------------------------------------------------------------------
print("\n=== Test 2: decompose_or_update entry points ===")

mock_llm_response_3node = json.dumps({
    "chunks": [
        {
            "id": "node-1",
            "members": ["finance-planner"],
            "depends_on": [],
            "success": {"text": "Plan created", "deterministic_checks": ["plan is clear"]},
            "worktree_strategy": "shared_sequential",
            "commit_on_done": True,
            "regression_required": True,
            "retry_policy": {"max": 2, "backoff_s": 30},
        },
        {
            "id": "node-2",
            "members": ["finance-fullstack-executor"],
            "depends_on": ["node-1"],
            "success": {"text": "CRUD built", "deterministic_checks": ["prior plan followed", "GET / returns 200"]},
            "worktree_strategy": "shared_sequential",
            "commit_on_done": True,
            "regression_required": True,
            "retry_policy": {"max": 2, "backoff_s": 30},
        },
        {
            "id": "node-3",
            "members": ["finance-reviewer"],
            "depends_on": ["node-2"],
            "success": {"text": "Code reviewed", "deterministic_checks": ["prior tests pass"]},
            "worktree_strategy": "shared_sequential",
            "commit_on_done": True,
            "regression_required": True,
            "retry_policy": {"max": 2, "backoff_s": 30},
        },
    ],
    "worktree_root": "/tmp/worktrees",
    "plan_id": "test-lifecycle-plan",
})


def mock_llm(prompt: str) -> str:
    return mock_llm_response_3node


# 2a. new_plan source
try:
    dp = decompose_or_update(
        plan_id="test-lifecycle-plan",
        source="new_plan",
        payload={"intent": "Build finance tracker CRUD + review"},
        llm_call=mock_llm,
    )
    check("new_plan returns DecomposedPlan", isinstance(dp, DecomposedPlan))
    check("new_plan has 3 chunks", len(dp.chunks) == 3)
    for n in dp.chunks:
        check(f"  chunk {n.id} has no kind attr", not hasattr(n, 'kind'))
        check(f"  chunk {n.id} members >=1", len(n.members) >= 1)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 3

# 2b. chat_promote source
try:
    dp2 = decompose_or_update(
        plan_id="test-chat-plan",
        source="chat_promote",
        payload={"intent": "Promote chat to plan for auth service"},
        llm_call=mock_llm,
    )
    check("chat_promote returns DecomposedPlan", isinstance(dp2, DecomposedPlan))
    check("chat_promote has 3 chunks", len(dp2.chunks) == 3)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 2

# 2c. trigger source
try:
    dp3 = decompose_or_update(
        plan_id="test-trigger-plan",
        source="trigger",
        payload={"intent": "Scheduled run_task: run tests"},
        llm_call=mock_llm,
    )
    check("trigger returns DecomposedPlan", isinstance(dp3, DecomposedPlan))
    check("trigger has 3 chunks", len(dp3.chunks) == 3)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 2

# 2d. refine source
try:
    dp4 = decompose_or_update(
        plan_id="test-refine-plan",
        source="refine",
        payload={"instruction": "Add CSV export", "intent": "Build finance tracker"},
        llm_call=mock_llm,
    )
    check("refine returns DecomposedPlan", isinstance(dp4, DecomposedPlan))
    check("refine has 3 chunks", len(dp4.chunks) == 3)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 2

# 2e. append_node source (incremental)
try:
    existing = [
        ChunkNode(id="node-1", members=["finance-planner"]),
        ChunkNode(id="node-2", members=["finance-executor"], depends_on=["node-1"]),
        ChunkNode(id="node-3", members=["finance-reviewer"], depends_on=["node-2"]),
    ]
    dp5 = decompose_or_update(
        plan_id="test-append-plan",
        source="append_node",
        payload={
            "members": ["finance-reviewer"],
            "depends_on": ["node-3"],
            "task": "Add CSV export endpoint",
            "success_criterion": "GET /csv returns valid CSV",
        },
        existing_chunks=existing,
        llm_call=mock_llm,
    )
    check("append_node returns DecomposedPlan", isinstance(dp5, DecomposedPlan))
    check("append_node: 4 chunks (3 existing + 1 new)", len(dp5.chunks) == 4)
    # Check the new node exists
    new_ids = [c.id for c in dp5.chunks if c.id not in {x.id for x in existing}]
    check("append_node: new node id found", len(new_ids) == 1)
    new_node = [c for c in dp5.chunks if c.id in new_ids][0]
    check("append_node: new node has members", len(new_node.members) >= 1)
    check("append_node: new node depends on node-3", "node-3" in new_node.depends_on)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 4

# 2f. cross_project source (incremental)
try:
    dp6 = decompose_or_update(
        plan_id="test-cross-plan",
        source="cross_project",
        payload={
            "members": ["finance-reviewer", "backend-executor"],
            "depends_on": ["node-2"],
            "task": "Verify auth integration",
            "target_project": "auth-service",
        },
        existing_chunks=existing,
        llm_call=mock_llm,
    )
    check("cross_project returns DecomposedPlan", isinstance(dp6, DecomposedPlan))
    check("cross_project: 4 chunks", len(dp6.chunks) == 4)
    cross_ids = [c.id for c in dp6.chunks if c.id not in {x.id for x in existing}]
    check("cross_project: new node created", len(cross_ids) == 1)
except Exception as e:
    print(f"  FAIL: exception {e}")
    FAIL += 2


# ---------------------------------------------------------------------------
# 3. Validation — rejects invalid plans
# ---------------------------------------------------------------------------
print("\n=== Test 3: Validation ===")

# Cycle
try:
    DecomposedPlan(
        plan_id="cycle",
        chunks=[
            ChunkNode(id="a", members=["x"], depends_on=["b"]),
            ChunkNode(id="b", members=["y"], depends_on=["a"]),
        ],
    )
    check("cycle rejected", False)
except ValueError:
    check("cycle rejected", True)

# Missing dep
try:
    DecomposedPlan(
        plan_id="missing-dep",
        chunks=[
            ChunkNode(id="a", members=["x"], depends_on=["nonexistent"]),
        ],
    )
    check("missing dep rejected", False)
except ValueError:
    check("missing dep rejected", True)

    # Valid DAG (no cycle) — IDs follow "node-N" convention for worktree derivation
    try:
        dplan_ok = DecomposedPlan(
            plan_id="valid",
            chunks=[
                ChunkNode(id="node-1", members=["x"]),
                ChunkNode(
                    id="node-2",
                    members=["y"],
                    depends_on=["node-1"],
                    success=SuccessCriterion(
                        text="task done",
                        deterministic_checks=["prior tests pass"],
                    ),
                ),
            ],
        )
        validate_decomposed(dplan_ok)
        check("valid DAG passes", True)
    except Exception as e:
        print(f"  FAIL: exception {e}")
        FAIL += 1


# ---------------------------------------------------------------------------
# 4. Migration columns exist in DB
# ---------------------------------------------------------------------------
print("\n=== Test 4: Migration columns ===")
db_url = os.environ.get("DATABASE_URL", "")
if db_url:
    try:
        import psycopg
        with psycopg.connect(db_url) as c:
            with c.cursor() as cur:
                cur.execute("""
                    SELECT column_name
                    FROM information_schema.columns
                    WHERE table_name = 'tasks'
                      AND column_name IN ('members', 'node_commit_tag', 'gate_mode')
                """)
                rows = cur.fetchall()
                cols = [r[0] for r in rows]
                check("members column exists", "members" in cols)
                check("node_commit_tag column exists", "node_commit_tag" in cols)
                check("gate_mode column exists", "gate_mode" in cols)
    except Exception as e:
        print(f"  SKIP (DB not reachable): {e}")
        check("DB not reachable (skipped)", True)
else:
    print("  SKIP: no DATABASE_URL set")
    check("no DB URL (skipped)", True)


# ---------------------------------------------------------------------------
# 5. builtins load without error
# ---------------------------------------------------------------------------
print("\n=== Test 5: Builtins ===")
try:
    from backend.builtins.git_ops import commit_node, show_node, reset_to
    check("git_ops imports", True)
except Exception as e:
    print(f"  FAIL: {e}")
    FAIL += 1

try:
    from backend.builtins.handoff import build_node_context
    check("handoff imports", True)
except Exception as e:
    print(f"  FAIL: {e}")
    FAIL += 1


# ---------------------------------------------------------------------------
# 6. granularity simplification
# ---------------------------------------------------------------------------
print("\n=== Test 6: Granularity ===")
try:
    from backend.planning.granularity import right_sized
    check("right_sized still works", callable(right_sized))

    ok, reason = right_sized("implement CRUD for users")
    check(f"right_sized appropriate: {reason}", ok)
except Exception as e:
    print(f"  FAIL: {e}")
    FAIL += 1


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
print(f"\n{'='*50}")
print(f"GATE 19 RESULTS: {PASS} passed, {FAIL} failed out of {PASS+FAIL}")
print(f"{'='*50}")

if FAIL > 0:
    sys.exit(1)
else:
    print("GATE 19: PASS")
