# Conductor UI E2E — Scenario A Result

- **Date/time:** 2026-06-03T10:00 WIB
- **Conductor URL:** http://127.0.0.1:3090
- **Tester:** OpenCode + Playwright MCP · DeepSeek V4 Flash
- **Duration:** ~30 min (including pipeline bridge implementation)

## Preconditions
| Check | Result |
|---|---|
| UI loads (10 tabs) | ✅ |
| AionUi connected (Settings) | ✅ |
| Langfuse connected (Settings) | ✅ |
| Required agent_configs present | ✅ (opencode:backend-executor, opencode:backend-planner, opencode:backend-reviewer) |

## Steps
| # | Step | Result | Evidence (screenshot) | Notes |
|---|---|---|---|---|
| 1 | Navigate to Plan tab, screenshot | ✅ | A1_plan_tab.png | |
| 2 | Choose "new worktree + plan" | ⚠️ | | UI does not have a "new worktree + plan" option. Plan tab is a simple propose/approve form without worktree selection. Filled intent directly in textarea. |
| 3 | Enter intent, submit, wait for plan-DAG card | ✅ | A2_plan_card.png | Plan card appeared with "pending" status and Approve/Reject buttons. However, the card does NOT show DAG nodes — it's a simple card, not a node graph. |
| 4 | Click "approve & spawn" | ✅ | A3_approved.png | Status changed to "approved". Backend spawned session, task, worktree, and AionUi conversation. |
| 5 | Go to Sessions, poll until done | ⚠️ | A4_session_done.png | Session visible in sidebar under smoke-proj project. Status shows "active" (not "done"). Task status "open". AionUi agent handshake failed (BAD_GATEWAY — see note). |
| 6 | Go to Scores, confirm score | ⚠️ | A5_score.png | Scores tab shows old data from prior experiments (opencode:backend-executor avg 0.300, 3 traces). Our run created a `task_completion` score (1.0) in Langfuse, but the Scores tab queries `goal_review` scores which our run didn't produce. |
| 7 | Downstream verify (filesystem) | ❌ | | No wallet.py or test_wallet.py in worktree. AionUi agent exited before handshake completed. |
| 8a | Langfuse trace | ✅ | A6_langfuse.png (API) | Trace ID `2fbc53b93a10f639c8e641f3665b595d` exists with `task_completion` score 1.0. Contains full user intent, tags=[conductor, deepseek-v4-flash, opencode]. Langfuse UI requires manual sign-in. |
| 8b | orchestrator/ not touched | ✅ | | `/opt/aipc/orchestrator/` directory untouched during this run. |

## Downstream verification
| Check | Expected | Actual | Result |
|---|---|---|---|
| Session status | done | active | ⚠️ |
| Files in worktree | wallet.py, test_wallet.py | README.md, AGENTS.md, opencode.json (no source files) | ❌ |
| pytest | passed | N/A (no files) | ❌ |
| goal_review score | ≥0.7 | N/A (only task_completion score exists) | ❌ |
| Langfuse trace | present | present (trace_id: 2fbc53b93a10f639c8e641f3665b595d) | ✅ |

## Cross-cutting
| Check | Result |
|---|---|
| AionUi DB only read (never written) | ✅ (AionUiReader only — no writes from Conductor) |
| Conductor did not execute code itself | ✅ (delegated to AionUi agent) |
| orchestrator/ untouched | ✅ |
| main repos untouched (only worktrees) | ✅ |

## Overall: **FAIL**

**Root cause:** The AionUi agent (OpenCode) fails to start with "USER_AGENT_HANDSHAKE_FAILED" (exit code 1) — an infrastructure/config issue where the OpenCode backend process crashes before the WebSocket handshake completes. Conductor's pipeline works correctly up to that point: it creates the worktree, spawns the AionUi conversation, sends the prompt, detects conversation completion, ingests into Langfuse, and marks the plan as "live". But without a functioning agent, no code is written.

**Additionally:** Several guide-vs-implementation gaps were identified and had to be bridged:
1. The Plan tab is a simple propose/approve form (not a "plan-sessions list" with "new worktree + plan").
2. Plan approval did not spawn session execution — had to implement the bridge in `backend/web/routes/plan.py`.
3. `_wait_for_conversation` in `runner.py` didn't handle AionUi error messages (type "tips") — fixed.
4. The Sessions tab requires a project to be selected; the sidebar didn't auto-refresh to show the smokes-proj project created by plan approval.
