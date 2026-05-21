"""
Watchdog — find stuck traces and mark them.

Rules:
  - If trace.status='running' for > 4 hours with no observations: 'abandoned' with ended_reason='timeout'
  - If trace.status='running' for > 4 hours WITH observations but no receipt:
    'failed' with ended_reason='cli_closed'  (assume user closed without receipt)
  - If trace.cli_session_id is null and trace.status='running' for > 30 min: 'failed' / 'spec_invalid'

Run via cron every 30 min.
"""
from __future__ import annotations
from backend.db import queries


THRESHOLDS = {
    "no_obs_timeout_hours": 4,
    "with_obs_timeout_hours": 4,
    "no_session_id_timeout_minutes": 30,
}


def sweep() -> dict:
    summary = {"abandoned": 0, "failed_no_receipt": 0, "failed_no_session": 0}

    with queries.conn() as c, c.cursor() as cur:
        # 1. No observations, very old -> abandoned
        cur.execute(
            f"""UPDATE traces
                   SET status = 'abandoned',
                       ended_at = now(),
                       ended_reason = 'timeout'
                 WHERE status = 'running'
                   AND total_observations = 0
                   AND started_at < now() - INTERVAL '{THRESHOLDS['no_obs_timeout_hours']} hours'
                 RETURNING trace_id""",
        )
        summary["abandoned"] = len(cur.fetchall())

        # 2. Has observations, old, no output_spec -> cli_closed (failed)
        cur.execute(
            f"""UPDATE traces
                   SET status = 'failed',
                       ended_at = now(),
                       ended_reason = 'cli_closed'
                 WHERE status = 'running'
                   AND total_observations > 0
                   AND output_spec IS NULL
                   AND started_at < now() - INTERVAL '{THRESHOLDS['with_obs_timeout_hours']} hours'
                 RETURNING trace_id""",
        )
        summary["failed_no_receipt"] = len(cur.fetchall())

        # 3. No session id even after a while -> spawn problem
        cur.execute(
            f"""UPDATE traces
                   SET status = 'failed',
                       ended_at = now(),
                       ended_reason = 'spec_invalid'
                 WHERE status = 'running'
                   AND cli_session_id IS NULL
                   AND started_at < now() - INTERVAL '{THRESHOLDS['no_session_id_timeout_minutes']} minutes'
                 RETURNING trace_id""",
        )
        summary["failed_no_session"] = len(cur.fetchall())

        c.commit()

    return summary


if __name__ == "__main__":
    print(sweep())
