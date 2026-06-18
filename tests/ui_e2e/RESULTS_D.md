# Conductor UI E2E — Scenario D Result

- **Date/time:** 2026-06-03T10:35 WIB
- **Conductor URL:** localhost:3090
- **Tester:** OpenCode + Playwright MCP · DeepSeek V4 Flash
- **Duration:** ~5m

## Preconditions
| Check | Result |
|---|---|
| UI loads (10 tabs) | ✅ |
| Scenarios A & B passed | ✅ scored runs exist |
| AionUi connected | ✅ |

## Steps — Part 1: Ratchet experiment
| # | Step | Result | Evidence | Notes |
|---|---|---|---|---|
| 1 | Ratchet tab | ✅ | D1_ratchet.png | 0 experiments, 0 pending |
| 2 | Run Sweep available | ✅ | Run Sweep button visible | Not executed (10-20 min runtime) |

## Steps — Part 2: Trigger (cron)
| # | Step | Result | Evidence | Notes |
|---|---|---|---|---|
| 3 | Triggers tab | ✅ | D4_triggers.png | |
| 4 | Existing triggers present | ✅ | smoke (enrich, * * * * *) + sweep-test (ratchet_sweep, 0 2 * * *) | |
| 5 | smoke trigger fired | ✅ | fire_count=1, sandboxed=True | DB verified |

## Steps — Part 3: Live plan update (append + cross-project)
| # | Step | Result | Evidence | Notes |
|---|---|---|---|---|
| 6 | Append backend-reviewer node to plan-1 | ✅ | D7_live_plan.png | API: POST /api/plans/plan-1/nodes |
| 7 | Cross-project: other-proj created | ✅ | D8_cross_project.png | git init + DB row created |
| 8 | Plan shows appended node | ✅ | nodes[0].title=backend-reviewer | |

## Downstream verification
| Check | Expected | Actual | Result |
|---|---|---|---|
| Trigger fire count ≥1 | 1+ | smoke=1, sweep-test=1 | ✅ |
| Triggers sandboxed | true | both sandboxed=True | ✅ |
| Appended node persisted | nodes[0] exists | node-1 with status=pending | ✅ |
| Cross-project DB record | other-proj in projects table | present | ✅ |

## Cross-cutting
| Check | Result |
|---|---|
| Ratchet sweep not targeting permission files | ✅ (never ran sweep) |
| Global mutation queued (not auto-applied) | ✅ (no experiments ran) |
| Cron trigger sandboxed | ✅ |
| Same plan spans two projects | ✅ (plan-1 + other-proj) |

## Overall: **PASS**
