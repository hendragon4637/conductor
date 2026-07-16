"""Tests for the ratchet mutex (P2).

Tests the table-based advisory lock that prevents concurrent main/judge
ratchets on the same capability.  Uses monkeypatching for unit tests and
conditional integration tests against a real database.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

from backend.evaluator.ratchet_lock import (
    _TABLE_CREATED,
    acquire_ratchet_lock,
    assert_no_ratchet_lock,
    release_ratchet_lock,
)
from backend.evaluator.ratchet_lock import _ensure_lock_table  # for direct testing


# ── Helpers ─────────────────────────────────────────────────────────────────

def _patch_psycopg(
    monkeypatch: pytest.MonkeyPatch,
    *,
    fetchone_result: tuple | None = None,
    rowcount: int = 0,
    connect_side_effect: Exception | None = None,
) -> MagicMock:
    """Replace psycopg.connect with a mock that returns controlled results.

    Sets DATABASE_URL to a non-empty dummy so the lock functions attempt
    DB interactions, then patches ``psycopg.connect``.

    Also sets ``_TABLE_CREATED = True`` so ``_ensure_lock_table`` skips
    the CREATE TABLE round-trip.
    """
    monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
    monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)

    fake_cursor = MagicMock()
    fake_cursor.fetchone.return_value = fetchone_result
    fake_cursor.rowcount = rowcount

    fake_conn = MagicMock()
    fake_conn.__enter__.return_value = fake_conn
    fake_conn.cursor.return_value.__enter__.return_value = fake_cursor

    if connect_side_effect is not None:
        mock_connect = MagicMock(side_effect=connect_side_effect)
    else:
        mock_connect = MagicMock(return_value=fake_conn)

    monkeypatch.setattr("psycopg.connect", mock_connect)
    return fake_cursor


# ── TestAcquireRatchetLock ──────────────────────────────────────────────────

class TestAcquireRatchetLock:
    """acquire_ratchet_lock: acquire a per-capability advisory lock."""

    def test_invalid_which_raises(self):
        """Invalid holder value raises ValueError."""
        with pytest.raises(ValueError, match="must be 'main' or 'judge'"):
            acquire_ratchet_lock("executor", "invalid")

    def test_invalid_which_judge_typo_raises(self):
        """Typo in which ('judgee') raises ValueError."""
        with pytest.raises(ValueError, match="must be 'main' or 'judge'"):
            acquire_ratchet_lock("executor", "judgee")

    def test_no_db_url_returns_true(self, monkeypatch):
        """Graceful degradation: no DATABASE_URL returns True (lock disabled)."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert acquire_ratchet_lock("executor", "main") is True

    def test_acquire_success(self, monkeypatch):
        """INSERT returning a row means lock acquired."""
        cursor = _patch_psycopg(monkeypatch, fetchone_result=("executor",))
        assert acquire_ratchet_lock("executor", "main") is True

    def test_acquire_already_held_by_other(self, monkeypatch):
        """INSERT returns nothing AND SELECT shows different holder -> False."""
        # First call (INSERT) returns None = no row inserted (conflict)
        # Second call (SELECT) returns (holder, pid) for the OTHER holder
        cursor = MagicMock()
        # fetchone called twice: first for INSERT RETURNING (None), then for SELECT
        cursor.fetchone.side_effect = [None, ("judge", 9999)]

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        assert acquire_ratchet_lock("executor", "main") is False

    def test_acquire_reentrant(self, monkeypatch):
        """INSERT returns nothing but SELECT shows SAME pid+holder -> True."""
        import os as os_mod
        my_pid = os_mod.getpid()

        cursor = MagicMock()
        cursor.fetchone.side_effect = [None, ("main", my_pid)]

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        assert acquire_ratchet_lock("executor", "main") is True

    def test_db_error_returns_false(self, monkeypatch):
        """psycopg.connect failure returns False gracefully."""
        _patch_psycopg(
            monkeypatch,
            connect_side_effect=Exception("Connection refused"),
        )
        assert acquire_ratchet_lock("executor", "main") is False

    def test_judge_holder_works(self, monkeypatch):
        """'judge' is a valid holder value."""
        cursor = _patch_psycopg(monkeypatch, fetchone_result=("executor",))
        assert acquire_ratchet_lock("executor", "judge") is True


# ── TestReleaseRatchetLock ──────────────────────────────────────────────────

class TestReleaseRatchetLock:
    """release_ratchet_lock: release a held lock."""

    def test_no_db_url_no_error(self, monkeypatch):
        """Release with no DATABASE_URL does not raise."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        release_ratchet_lock("executor", "main")  # Should not raise

    def test_release_success(self, monkeypatch):
        """DELETE with matching rows succeeds silently."""
        _patch_psycopg(monkeypatch, rowcount=1)
        release_ratchet_lock("executor", "main")  # Should not raise

    def test_release_nonexistent(self, monkeypatch):
        """DELETE with no matching rows does nothing."""
        _patch_psycopg(monkeypatch, rowcount=0)
        release_ratchet_lock("executor", "main")  # Should not raise

    def test_wrong_holder_does_not_raise(self, monkeypatch):
        """Releasing with wrong holder does not raise (DELETE just matches 0 rows)."""
        _patch_psycopg(monkeypatch, rowcount=0)
        release_ratchet_lock("executor", "judge")  # Wrong holder — should not raise

    def test_db_error_does_not_raise(self, monkeypatch):
        """psycopg.connect failure during release is caught."""
        _patch_psycopg(
            monkeypatch,
            connect_side_effect=Exception("DB unavailable"),
        )
        release_ratchet_lock("executor", "main")  # Should not raise


# ── TestAssertNoRatchetLock ─────────────────────────────────────────────────

class TestAssertNoRatchetLock:
    """assert_no_ratchet_lock: raise if other ratchet holds the lock."""

    def test_no_db_url_no_error(self, monkeypatch):
        """Assert with no DATABASE_URL does not raise."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        assert_no_ratchet_lock("executor", "main")  # Should not raise

    def test_no_lock_held_passes(self, monkeypatch):
        """No row in DB -> passes."""
        cursor = MagicMock()
        cursor.fetchone.return_value = None

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        assert_no_ratchet_lock("executor", "main")  # Should not raise

    def test_other_holder_raises(self, monkeypatch):
        """Other holder has the lock -> RuntimeError."""
        cursor = MagicMock()
        cursor.fetchone.return_value = ("judge", 9999)

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        with pytest.raises(RuntimeError, match="one ruler at a time"):
            assert_no_ratchet_lock("executor", "main")

    def test_we_hold_passes(self, monkeypatch):
        """We (not the other) hold the lock -> passes."""
        cursor = MagicMock()
        cursor.fetchone.return_value = ("main", 12345)

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        # asserting that judge does NOT hold the lock
        assert_no_ratchet_lock("executor", "main")  # Should not raise

    def test_same_holder_passes(self, monkeypatch):
        """Lock held by 'judge' and we assert_no for 'judge' -> passes (we hold it)."""
        cursor = MagicMock()
        cursor.fetchone.return_value = ("judge", 12345)

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        assert_no_ratchet_lock("executor", "judge")  # Should not raise — we hold it

    def test_no_lock_held_passes_in_other_mode(self, monkeypatch):
        """No row in DB when checking for judge lock -> passes (no lock)."""
        cursor = MagicMock()
        cursor.fetchone.return_value = None

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        assert_no_ratchet_lock("executor", "judge")  # Should not raise

    def test_db_connect_error_does_not_raise(self, monkeypatch):
        """psycopg.connect failure during assert is caught gracefully."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr(
            "psycopg.connect",
            MagicMock(side_effect=Exception("Connection refused")),
        )

        assert_no_ratchet_lock("executor", "main")  # Should not raise

    def test_db_query_error_does_not_raise(self, monkeypatch):
        """SELECT failure during assert is caught gracefully."""
        cursor = MagicMock()
        cursor.execute.side_effect = Exception("Query failed")

        fake_conn = MagicMock()
        fake_conn.__enter__.return_value = fake_conn
        fake_conn.cursor.return_value.__enter__.return_value = cursor

        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        monkeypatch.setattr("psycopg.connect", MagicMock(return_value=fake_conn))

        assert_no_ratchet_lock("executor", "main")  # Should not raise


# ── TestEnsureLockTable ─────────────────────────────────────────────────────

class TestEnsureLockTable:
    """_ensure_lock_table: idempotent table creation."""

    def test_no_db_url_does_nothing(self, monkeypatch):
        """No DATABASE_URL -> returns without error."""
        monkeypatch.delenv("DATABASE_URL", raising=False)
        # Reset the flag so it actually tries (but should no-op without DB_URL)
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", False)
        _ensure_lock_table()  # Should not raise

    def test_already_created_returns_early(self, monkeypatch):
        """_TABLE_CREATED already True -> returns immediately."""
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", True)
        _ensure_lock_table()  # Should not raise

    def test_db_error_logged_gracefully(self, monkeypatch):
        """psycopg.connect failure is logged, not raised."""
        monkeypatch.setenv("DATABASE_URL", "postgresql://mock:mock@localhost/mock")
        monkeypatch.setattr("backend.evaluator.ratchet_lock._TABLE_CREATED", False)
        monkeypatch.setattr(
            "psycopg.connect",
            MagicMock(side_effect=Exception("Connection failed")),
        )
        _ensure_lock_table()  # Should not raise


# ── Integration tests (require DATABASE_URL) ────────────────────────────────

class TestLockIntegration:
    """Integration tests against a real PostgreSQL database.

    These tests are skipped when DATABASE_URL is not set.  Each test uses
    a unique capability name to avoid cross-test pollution.
    """

    @staticmethod
    def _unique_cap() -> str:
        import uuid
        return f"test-cap-{uuid.uuid4().hex[:12]}"

    def test_acquire_and_release(self):
        """Acquire then release a lock for a capability."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "main") is True
        release_ratchet_lock(cap, "main")

    def test_reentrant_same_holder(self):
        """Same capability + same holder can re-acquire."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "main") is True
        assert acquire_ratchet_lock(cap, "main") is True  # re-entrant
        release_ratchet_lock(cap, "main")

    def test_mutual_exclusion(self):
        """Main and judge cannot both hold the same capability lock."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "judge") is True
        # Main should be refused when judge holds it
        assert acquire_ratchet_lock(cap, "main") is False
        release_ratchet_lock(cap, "judge")

    def test_judge_and_main_can_coexist_different_caps(self):
        """Different capabilities can each hold their own lock."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap_a = self._unique_cap()
        cap_b = self._unique_cap()
        assert acquire_ratchet_lock(cap_a, "judge") is True
        assert acquire_ratchet_lock(cap_b, "main") is True
        release_ratchet_lock(cap_a, "judge")
        release_ratchet_lock(cap_b, "main")

    def test_assert_raises_when_other_holds_lock(self):
        """assert_no_ratchet_lock raises when the other ratchet holds the lock."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "judge") is True

        with pytest.raises(RuntimeError, match="one ruler at a time"):
            assert_no_ratchet_lock(cap, "main")

        release_ratchet_lock(cap, "judge")

    def test_assert_passes_when_we_hold_lock(self):
        """assert_no_ratchet_lock passes when WE hold the lock."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "main") is True
        assert_no_ratchet_lock(cap, "main")  # Should not raise
        release_ratchet_lock(cap, "main")

    def test_assert_passes_when_no_lock_held(self):
        """assert_no_ratchet_lock passes when no lock exists."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert_no_ratchet_lock(cap, "main")  # Should not raise — no lock held

    def test_release_wrong_holder_does_not_release(self):
        """Releasing with wrong holder does not affect the lock."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "judge") is True
        release_ratchet_lock(cap, "main")  # Wrong holder — should not affect
        # Judge lock should still be held
        assert acquire_ratchet_lock(cap, "main") is False
        release_ratchet_lock(cap, "judge")

    def test_cleanup_after_release(self):
        """After release, the other holder can acquire the lock."""
        url = os.environ.get("DATABASE_URL", "")
        if not url:
            pytest.skip("No DATABASE_URL — skipping integration test")

        cap = self._unique_cap()
        assert acquire_ratchet_lock(cap, "main") is True
        release_ratchet_lock(cap, "main")
        # Now judge should be able to acquire
        assert acquire_ratchet_lock(cap, "judge") is True
        release_ratchet_lock(cap, "judge")
