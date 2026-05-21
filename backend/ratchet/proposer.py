"""
Ratchet proposer — STUB.

When activated (week 3+), this module:
  1. Reads recent failing traces for a given agent_config
  2. Clusters failure_modes
  3. Generates a candidate SKILL.md edit targeting the dominant cluster
  4. Inserts a row into skill_mutations with kept=NULL (awaiting decision)
  5. Re-runs golden set against new skill in a sandbox (Karpathy keep/revert)
  6. Decides kept=true/false based on score delta

Week 1: schema only. The function signatures below are placeholders.
"""
from __future__ import annotations
from typing import Optional
from uuid import UUID

from backend.db import queries


def find_dominant_failure_mode(agent_config_id: str, since_n: int = 30) -> Optional[dict]:
    """
    Return {failure_mode, count, trace_ids[]} for the most-common failure_mode
    in the last N labeled traces of this config.
    """
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            SELECT failure_mode, COUNT(*) AS n,
                   array_agg(trace_id ORDER BY ended_at DESC) AS trace_ids
              FROM traces
             WHERE agent_config_id = %s
               AND manual_label IS NOT NULL
               AND manual_label != 'pass'
               AND failure_mode IS NOT NULL
             GROUP BY failure_mode
             ORDER BY n DESC
             LIMIT 1
            """,
            (agent_config_id,),
        )
        row = cur.fetchone()
        if not row:
            return None
        return {"failure_mode": row["failure_mode"], "count": row["n"], "trace_ids": row["trace_ids"][:since_n]}


def propose_skill_mutation(agent_config_id: str) -> Optional[dict]:
    """
    STUB. Would call Hermes-AGENT / Hermes-MODEL to author a SKILL.md edit.
    Week 1: returns the dominant failure mode as a notification, does not write
    any mutation row.
    """
    dom = find_dominant_failure_mode(agent_config_id)
    if not dom:
        return None
    print(f"[ratchet:stub] {agent_config_id} dominant failure_mode='{dom['failure_mode']}' "
          f"in {dom['count']} traces. Ratchet activation deferred to week 3.")
    return dom


if __name__ == "__main__":
    propose_skill_mutation("opencode:backend-executor")
