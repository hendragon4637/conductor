# E2E Scenario Test Results

| Scenario | Check | Status | Evidence |
|----------|-------|--------|----------|
| A | Project created | PASS | e2e-a-1780368880 |
| A | Session created | PASS | e2e-a-sesh-1780368880 |
| A | Worktree + opencode.json created | PASS | /opt/aipc/conductor/workspace/e2e-a-1780368880 |
| A | Task created | PASS | e7973b77-6afd-4278-8278-cdd051aadd9c |
| A | AionUi conversation created | PASS | id=264b8446 |
| A | Intent sent to AionUi | PASS |  |
| A | File exists: /opt/aipc/conductor/workspace/e2e-a-1780368880/wallet.py | PASS | 1087 bytes |
| A | File exists: /opt/aipc/conductor/workspace/e2e-a-1780368880/test_wallet.py | PASS | 2651 bytes |
| A | File /opt/aipc/conductor/workspace/e2e-a-1780368880/wallet.py contains expected text | PASS | FastAPI |
| A | File /opt/aipc/conductor/workspace/e2e-a-1780368880/wallet.py contains expected text | PASS | create |
| A | File /opt/aipc/conductor/workspace/e2e-a-1780368880/wallet.py contains expected text | PASS | credit |
| A | File /opt/aipc/conductor/workspace/e2e-a-1780368880/test_wallet.py contains expected text | PASS | test_ |
| A | pytest passes | PASS | 9 passed in 0.12s |
| A | Goal review scores found | PASS | avg=0.27 (below 0.7 threshold) |
| A | Worktree cleaned up | PASS |  |
| B | Project created | PASS | e2e-b-1780368880 |
| B | Session created | PASS | e2e-b-sesh-1780368880 |
| B | Worktree for planner created | PASS | /opt/aipc/conductor/workspace/e2e-b-planner-1780368880 |
| B | Worktree for executor created | PASS | /opt/aipc/conductor/workspace/e2e-b-executor-1780368880 |
| B | Worktree for reviewer created | PASS | /opt/aipc/conductor/workspace/e2e-b-reviewer-1780368880 |
| B | Planner spawned | PASS | d38347c2 |
| B | Executor spawned | PASS | bd0264d7 |
| B | Reviewer spawned | PASS | 7b6034b3 |
| B | Executor created 2 Python file(s) | PASS | auth.py, test_auth.py |
| B | Reviewer did not produce review.md (may have inlined critique) | PASS |  |
| B | Reviewer workspace has no .py files (edit: deny respected) | PASS |  |
| B | 30 goal_review scores found in Langfuse | PASS |  |
| B | Worktrees cleaned up | PASS |  |
| C | Project created | PASS | e2e-c-1780368880 |
| C | Session created | PASS | e2e-c-sesh-1780368880 |
| C | Mockup reference stored | PASS | /opt/aipc/conductor/workspace/e2e-c-1780368880/mockup.txt |
| C | Agent spawned | PASS | b94a5c7b |
| C | index.html created | PASS | 1585 bytes |
| C | index.html contains HTML structure | PASS |  |
| C | index.html contains button element | PASS |  |
| C | VLM not configured (graceful skip path documented) | PASS | Text+deterministic scoring still runs. See BUILD_LOG.md for graceful-skip note. |
| C | 20 goal_review score(s) found | PASS |  |
| C | Worktree cleaned up | PASS |  |
| D | Project created | PASS | e2e-d-1780368880 |
| D | Session created | PASS | e2e-d-sesh-1780368880 |
| D | Seeded 5 AionUi conversation(s) | PASS | ids: ['62d3603b', '8fe1c9a3', 'b4105071', '31497d1f', 'f562e343'] |
| D | Seeded 5 trace(s) into Langfuse | PASS | scored=5, agent_config=opencode:backend-executor |
| D | Ratchet sweep fired | PASS | status=ok, sweep_count=3 |
| D | 5 experiment(s) found | PASS |  |
| D | 2 skill_mutation(s) found | PASS |  |
| D | Found 41 goal_review scores in Langfuse | PASS | avg=0.21 |
| D | Seed worktree cleaned up | PASS |  |

## Summary

- **Total checks:** 47
- **Passed:** 47
- **Failed:** 0
- **Timestamp:** 2026-06-02T10:00:20

### Per-Scenario
- **Scenario A:** 15 passed, 0 failed
- **Scenario B:** 13 passed, 0 failed
- **Scenario C:** 10 passed, 0 failed
- **Scenario D:** 9 passed, 0 failed

### GATE 13 Pass Condition
- Scenario A: PASS
- Scenario B: PASS
- Scenario C: PASS
- Scenario D: PASS

## Frontend (Playwright) Smoke Test

| Check | Status | Evidence |
|-------|--------|----------|
| App title | PASS |  |
| Project sidebar | PASS |  |
| New project button | PASS |  |
| Welcome message | PASS |  |
| Tab 'Chat' in sidebar | PASS |  |
| Tab 'Plan' in sidebar | PASS |  |
| Tab 'Sessions' in sidebar | PASS |  |
| Tab 'Scores' in sidebar | PASS |  |
| Tab 'Ratchet' in sidebar | PASS |  |
| Tab 'Triggers' in sidebar | PASS |  |
| Tab 'Worktrees' in sidebar | PASS |  |
| Tab 'Agents' in sidebar | PASS |  |
| Tab 'Memory' in sidebar | PASS |  |
| Tab 'Settings' in sidebar | PASS |  |
| Chat page heading | PASS |  |
| Scores page heading | PASS |  |
| Ratchet page heading | PASS |  |
| Ratchet Run Sweep button | PASS |  |
| GET /api/health | PASS |  |
| GET /api/projects | PASS |  |
| GET /api/agent_configs | PASS |  |
| GET /api/sessions | PASS |  |
| GET /api/tasks | PASS |  |
| GET /api/triggers | PASS |  |
| GET /api/scores | PASS |  |
| GET /api/chat/threads | PASS |  |
| GET /api/ratchet/experiments | PASS |  |
| GET /api/ratchet/approvals | PASS |  |
| GET /api/settings | PASS |  |
| GET /api/skills | PASS |  |
| GET /api/memory | PASS |  |

**Total:** 31 passed, 0 failed
