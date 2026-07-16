"""LLM failure tagging pipeline → failure_events.

Queries failed episodes from node_sessions that don't yet have a
failure_event, builds a structured summary per episode, calls a cheap
LLM (deepseek-planning, ≠ judge family) via the LiteLLM gateway to
classify the failure, and inserts into failure_events.

Idempotent: skips episodes that already have a failure_event.
Re-runnable: safe to run multiple times — only processes new failures.

Usage:
    cd /opt/aipc/conductor
    uv run python scripts/tag_failures.py
"""
from __future__ import annotations

import json
import logging
import os
import sys

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("tag_failures")

DB_DSN = os.environ.get("DATABASE_URL", "postgresql://aipc@localhost:5432/aipc_conductor")

TAG_PROMPT = """Classify this failed episode. Return STRICT JSON only — no commentary, no markdown.
{{"primary_tag":"spec|coordination|verification|infra","tags":[...],"failure_stage":"planning|execution|evaluation|merge|continuation|infra","note":"one concrete sentence"}}

Definitions:
- spec: bad plan, task misunderstanding, wrong criteria
- coordination: brief↔check↔feedback mismatch, context loss between nodes
- verification: judge wrong (artifact actually fine or judged on wrong evidence)
- infra: conductor/backend/tool/environment bug (not the agent's fault)

failure_stage:
- planning: plan was wrong or incomplete
- execution: agent failed to produce correct output
- evaluation: L2/gate incorrectly scored
- merge: git merge/worktree failure
- continuation: retry/remediation failed repeatedly
- infra: system-level failure

EPISODE:
{episode_summary}"""


def _infer_loop_tier(row: dict) -> str:
    """Infer loop_tier from node_sessions columns.

    node_sessions has no explicit loop_tier column, so we derive it:
    - remediation_of set       → "remediation"
    - steering_count > 0       → "steering"
    - otherwise                → "initial"
    """
    if row.get("remediation_of"):
        return "remediation"
    if (row.get("steering_count") or 0) > 0:
        return "steering"
    return "initial"


def _summarize_episode(row: dict) -> str:
    """Build a concise structured summary for the LLM classifier.

    Includes: task context, verdicts, L2 feedback highlights, steering
    history, and error strings — enough to classify root cause.
    """
    parts = [f"Node: {row.get('node_id', '?')}"]

    # Goal review + L2 score
    gr = row.get("goal_review")
    l2 = row.get("l2_score")
    parts.append(f"Goal review: {gr or 'N/A'} | L2 score: {l2 or 'N/A'} | L1 pass: {row.get('l1_pass', 'N/A')}")

    # Fail reason
    fr = row.get("fail_reason") or ""
    if fr:
        parts.append(f"Fail reason: {fr}")

    # Steering history
    sc = row.get("steering_count") or 0
    if sc > 0:
        parts.append(f"Steering cycles: {sc}")

    # Remediation info
    if row.get("remediation_of"):
        parts.append("Remediation node (retry of previous failure)")
    att = row.get("attempt") or 1
    if att > 1:
        parts.append(f"Attempt #{att}")

    # L2 feedback — extract how/why highlights
    l2_fb = row.get("l2_feedback")
    if l2_fb:
        if isinstance(l2_fb, str):
            try:
                l2_fb = json.loads(l2_fb)
            except (json.JSONDecodeError, TypeError):
                l2_fb = None
        if isinstance(l2_fb, list):
            dims = []
            for d in l2_fb[:4]:  # limit to first 4 dimensions
                how = (d.get("how") or "")[:200]
                why = (d.get("why") or "")[:200]
                dims.append(f"  - how: {how} | why: {why}")
            if dims:
                parts.append("L2 feedback dimensions:")
                parts.extend(dims)

    # Agent config
    mc = row.get("agent_config") or ""
    if mc:
        parts.append(f"Agent config: {mc}")

    return "\n".join(parts)


def _load_agent_capability_map(cur) -> dict[str, str]:
    """Build a map of agent_config_id → first capability name."""
    try:
        cur.execute(
            "SELECT agent_config_id, new_capabilities FROM agent_configs",
        )
        mapping: dict[str, str] = {}
        for r in cur.fetchall():
            caps = r[1] if r[1] else []
            if isinstance(caps, str):
                try:
                    caps = json.loads(caps)
                except (json.JSONDecodeError, TypeError):
                    caps = []
            if isinstance(caps, list) and caps:
                mapping[r[0]] = caps[0]
            else:
                mapping[r[0]] = ""
        return mapping
    except Exception:
        return {}


def _load_plan_dag_map(cur) -> dict[str, list]:
    """plan_id → parsed DAG (list of nodes with members[0].agent_config)."""
    cur.execute("SELECT plan_id, dag FROM plans WHERE dag IS NOT NULL")
    m: dict[str, list] = {}
    for row in cur.fetchall():
        pid, raw = row
        try:
            dag = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(dag, list):
                m[pid] = dag
        except (json.JSONDecodeError, TypeError):
            continue
    return m


def _resolve_agent_config_from_dag(dag: list, node_id: str) -> str:
    """Walk DAG nodes looking for node_id → members[0].agent_config."""
    for node in dag:
        if isinstance(node, dict) and node.get("id") == node_id:
            members = node.get("members")
            if isinstance(members, list) and members:
                ac = members[0].get("agent_config", "")
                if ac:
                    return ac
    return ""


def main() -> None:
    import psycopg

    _CONDUCTOR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    if _CONDUCTOR not in sys.path:
        sys.path.insert(0, _CONDUCTOR)

    from backend.llm.gateway import call as gateway_call

    conn = psycopg.connect(DB_DSN)
    with conn:
        with conn.cursor() as cur:
            cap_map = _load_agent_capability_map(cur)
            plan_dag_map = _load_plan_dag_map(cur)

            cur.execute("""
                SELECT ns.id, ns.run_id, ns.node_id, ns.verdict,
                       ns.goal_review, ns.l2_score, ns.l1_pass,
                       ns.fail_reason, ns.steering_count,
                       ns.remediation_of, ns.attempt,
                       ns.l2_feedback, ns.l1_feedback, ns.backend,
                       ns.members,
                       r.plan_id, r.project_id, r.goal_kind
                FROM node_sessions ns
                JOIN runs r ON ns.run_id = r.id
                WHERE ns.verdict = 'failed'
                  AND ns.id NOT IN (
                      SELECT node_session_id FROM failure_events
                      WHERE node_session_id IS NOT NULL
                  )
                ORDER BY ns.finished_at NULLS LAST, ns.created_at
            """)
            rows = cur.fetchall()
            if not rows:
                logger.info("No untagged failed episodes found.")
                return

            logger.info("=== %d failed episodes to tag ===", len(rows))

            tagged = 0
            skipped = 0
            errors = 0

            for row in rows:
                ns = dict(zip(
                    ["id", "run_id", "node_id", "verdict",
                     "goal_review", "l2_score", "l1_pass",
                     "fail_reason", "steering_count",
                     "remediation_of", "attempt",
                     "l2_feedback", "l1_feedback", "backend",
                     "members",
                     "plan_id", "project_id", "goal_kind"],
                    row,
                ))

                # Extract agent_config from members JSONB (fall back to plan DAG)
                members_raw = ns.get("members")
                agent_config = ""
                if members_raw:
                    if isinstance(members_raw, str):
                        try:
                            members_raw = json.loads(members_raw)
                        except (json.JSONDecodeError, TypeError):
                            members_raw = None
                    if isinstance(members_raw, list) and members_raw:
                        agent_config = (members_raw[0].get("agent_config") or "")

                if not agent_config:
                    # Fallback: look up plan DAG for this node_id
                    dag = plan_dag_map.get(ns.get("plan_id", ""))
                    if dag:
                        agent_config = _resolve_agent_config_from_dag(dag, ns.get("node_id", ""))

                # Resolve capability from agent_configs table
                capability = cap_map.get(agent_config, "")

                # Inject into ns dict so _summarize_episode and LLM see it
                ns["agent_config"] = agent_config
                ns["capability"] = capability

                # Build episode summary
                episode_summary = _summarize_episode(ns)

                # Call LLM via gateway (retry up to 3 times, 600s timeout)
                prompt = TAG_PROMPT.format(episode_summary=episode_summary)
                logger.info("PROMPT>>> %s", prompt)
                raw = None
                last_exc = None
                for attempt in range(1, 4):
                    try:
                        resp = gateway_call(
                            "meta_planner",
                            [{"role": "user", "content": prompt}],
                            temperature=0.1,
                            max_tokens=2048,
                            timeout=600,
                        )
                        raw = resp["choices"][0]["message"]["content"].strip()
                        break
                    except Exception as exc:
                        last_exc = exc
                        logger.warning("  %s → LLM call attempt %d/3 failed: %s", ns["id"], attempt, exc)
                        if attempt < 3:
                            import time
                            time.sleep(5 * attempt)

                if raw is None:
                    logger.error("  %s → LLM call failed after 3 attempts: %s", ns["id"], last_exc)
                    errors += 1
                    continue

                # Parse JSON response — strip markdown fences if present
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[1] if "\n" in raw else raw[3:]
                    raw = raw.rsplit("```", 1)[0] if "```" in raw else raw
                raw = raw.strip()

                try:
                    tag = json.loads(raw)
                except (json.JSONDecodeError, TypeError) as exc:
                    logger.warning("  %s → JSON parse failed: %s | raw: %.120s", ns["id"], exc, raw)
                    errors += 1
                    continue

                primary_tag = tag.get("primary_tag", "")
                if primary_tag not in ("spec", "coordination", "verification", "infra"):
                    logger.warning(
                        "  %s → invalid primary_tag=%r, defaulting to 'infra'",
                        ns["id"], primary_tag,
                    )
                    primary_tag = "infra"

                tags = tag.get("tags", [])
                if not isinstance(tags, list):
                    tags = [str(tags)] if tags else []

                failure_stage = tag.get("failure_stage", "")
                valid_stages = {"planning", "execution", "evaluation", "merge", "continuation", "infra"}
                if failure_stage not in valid_stages:
                    logger.warning(
                        "  %s → invalid failure_stage=%r, defaulting to 'execution'",
                        ns["id"], failure_stage,
                    )
                    failure_stage = "execution"

                note = (tag.get("note") or "")[:500]

                # Build evidence (pointers, not copies)
                loop_tier = _infer_loop_tier(ns)
                steering_count = ns.get("steering_count") or 0
                evidence = {
                    "steering_count": steering_count,
                    "attempt": ns.get("attempt") or 1,
                    "is_remediation": bool(ns.get("remediation_of")),
                    "agent_config": agent_config,
                    "node_id": ns["node_id"],
                }

                # Insert into failure_events
                fe_id = f"fe-{ns['run_id']}-{ns['node_id']}"
                try:
                    cur.execute(
                        """INSERT INTO failure_events
                           (id, run_id, node_session_id, plan_id, project_id,
                            capability, agent_config, backend,
                            goal_kind, loop_tier, failure_stage,
                            primary_tag, tags, evidence, note,
                            labeled_by)
                           VALUES (%s, %s, %s, %s, %s,
                                   %s, %s, %s,
                                   %s, %s, %s,
                                   %s, %s, %s, %s,
                                   'llm')
                           ON CONFLICT (id) DO NOTHING""",
                        (
                            fe_id,
                            ns["run_id"],
                            ns["id"],
                            ns.get("plan_id") or "",
                            ns.get("project_id") or "",
                            capability,
                            agent_config,
                            ns.get("backend") or "",
                            ns.get("goal_kind") or "",
                            loop_tier,
                            failure_stage,
                            primary_tag,
                            json.dumps(tags),
                            json.dumps(evidence),
                            note,
                        ),
                    )
                    if cur.rowcount > 0:
                        tagged += 1
                        logger.info(
                            "  %s → %s/%s/%s (loop=%s, cfg=%s)",
                            ns["id"], primary_tag, failure_stage, ",".join(tags),
                            loop_tier, agent_config,
                        )
                    else:
                        skipped += 1
                    conn.commit()
                except Exception as exc:
                    logger.error("  %s → DB insert failed: %s", ns["id"], exc)
                    conn.rollback()
                    errors += 1

    logger.info("=== Done: %d tagged, %d skipped (duplicate), %d errors ===", tagged, skipped, errors)


if __name__ == "__main__":
    main()
