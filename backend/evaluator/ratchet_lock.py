"""Ratchet mutex — prevents concurrent main/judge ratchets on the same capability.

Uses a ``ratchet_locks`` DB table (created lazily via CREATE TABLE IF NOT EXISTS).
Each row is a held lock keyed by capability name with a holder label ('main' or 'judge').

Usage:
    from backend.evaluator.ratchet_lock import acquire_ratchet_lock, release_ratchet_lock

    if not acquire_ratchet_lock("executor", "judge"):
        logger.warning("REFUSED: main ratchet active on executor")
        return
    try:
        run_judge_ratchet("executor")
    finally:
        release_ratchet_lock("executor", "judge")
"""
from __future__ import annotations

import logging
import os
import time

logger = logging.getLogger(__name__)

_TABLE_CREATED = False


def _ensure_lock_table() -> None:
    """Create the ratchet_locks table if it doesn't exist (idempotent)."""
    global _TABLE_CREATED
    if _TABLE_CREATED:
        return
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS ratchet_locks (
                        capability  TEXT PRIMARY KEY,
                        holder      TEXT NOT NULL CHECK (holder IN ('main', 'judge')),
                        pid         INTEGER NOT NULL,
                        acquired_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
            conn.commit()
        _TABLE_CREATED = True
    except Exception as exc:
        logger.warning("Failed to create ratchet_locks table: %s", exc)


def _cleanup_stale_locks() -> None:
    """Remove locks held by PIDs that no longer exist."""
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute("""
                    DELETE FROM ratchet_locks
                    WHERE pid != %s
                    AND pid NOT IN (
                        SELECT pid FROM pg_stat_activity
                    )
                """, (os.getpid(),))
                deleted = cur.rowcount
            conn.commit()
            if deleted:
                logger.info("Cleaned up %d stale ratchet lock(s)", deleted)
    except Exception as exc:
        logger.warning("Failed to clean stale locks: %s", exc)


def acquire_ratchet_lock(capability: str, which: str) -> bool:
    """Acquire a per-capability ratchet lock.
    
    Args:
        capability: The capability name (e.g. 'executor', 'backend_api').
        which: 'main' or 'judge' — identifies the holder.
    
    Returns:
        True if lock acquired, False if already held by the other ratchet.
    
    Raises:
        ValueError: If ``which`` is not 'main' or 'judge'.
    """
    if which not in ("main", "judge"):
        raise ValueError(f"holder must be 'main' or 'judge', got {which!r}")
    
    _ensure_lock_table()
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        logger.warning("No DATABASE_URL — ratchet lock disabled")
        return True  # Degrade gracefully — no DB, no lock
    
    _cleanup_stale_locks()
    pid = os.getpid()
    
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                # Try to insert — ON CONFLICT DO NOTHING means if row exists, no insert
                cur.execute(
                    """INSERT INTO ratchet_locks (capability, holder, pid)
                       VALUES (%s, %s, %s)
                       ON CONFLICT (capability) DO NOTHING
                       RETURNING capability""",
                    (capability, which, pid),
                )
                row = cur.fetchone()
            conn.commit()
        
        if row:
            logger.info("Acquired ratchet lock for %s (holder=%s, pid=%d)", capability, which, pid)
            return True
        
        # Lock exists — check if WE hold it (re-entrant)
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT holder, pid FROM ratchet_locks WHERE capability = %s",
                    (capability,),
                )
                existing = cur.fetchone()
        
        if existing and existing[1] == pid and existing[0] == which:
            logger.debug("Ratchet lock for %s already held by us (re-entrant)", capability)
            return True
        
        other = "judge" if which == "main" else "main"
        logger.warning(
            "REFUSED: %s ratchet lock held on %s by %s (pid=%d) — one ruler at a time",
            existing[0] if existing else "?",
            capability, existing[1] if existing else "?", existing[1] if existing else "?",
        )
        return False
    except Exception as exc:
        logger.warning("Failed to acquire ratchet lock for %s: %s", capability, exc)
        return False


def release_ratchet_lock(capability: str, which: str) -> None:
    """Release a per-capability ratchet lock.
    
    Args:
        capability: The capability name.
        which: 'main' or 'judge' — must match the holder.
    """
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    pid = os.getpid()
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "DELETE FROM ratchet_locks WHERE capability = %s AND holder = %s AND pid = %s",
                    (capability, which, pid),
                )
                deleted = cur.rowcount
            conn.commit()
        if deleted:
            logger.info("Released ratchet lock for %s (holder=%s, pid=%d)", capability, which, pid)
        else:
            logger.debug("No ratchet lock to release for %s (holder=%s)", capability, which)
    except Exception as exc:
        logger.warning("Failed to release ratchet lock for %s: %s", capability, exc)


def assert_no_ratchet_lock(capability: str, which: str) -> None:
    """Raise RuntimeError if the OTHER ratchet holds a lock on this capability.
    
    Args:
        capability: The capability name.
        which: 'main' or 'judge' — we check that the OTHER one isn't running.
    """
    other = "judge" if which == "main" else "main"
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return
    _cleanup_stale_locks()
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT holder, pid FROM ratchet_locks WHERE capability = %s",
                    (capability,),
                )
                row = cur.fetchone()
        if row and row[0] != which:
            raise RuntimeError(
                f"REFUSED: {row[0]} ratchet active on {capability} (pid={row[1]}) — "
                f"one ruler at a time. Cannot start {which} ratchet."
            )
    except RuntimeError:
        raise
    except Exception as exc:
        logger.warning("Failed to check ratchet lock for %s: %s", capability, exc)
