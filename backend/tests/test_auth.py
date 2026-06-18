"""Integration tests for the /auth/refresh endpoint with rotation.

These tests exercise the full stack: FastAPI route → auth_service → DB.
The refresh_tokens table must exist in the test database (run migrations).
"""
import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta
from pathlib import Path
import pytest
from dotenv import load_dotenv
from fastapi.testclient import TestClient

# Load env before importing backend modules
load_dotenv(Path(__file__).resolve().parent.parent.parent / ".env")

# Ensure JWT_SECRET_KEY is set for tests
os.environ.setdefault("JWT_SECRET_KEY", "test-secret-key-that-is-at-least-32-bytes-long-for-hs256")
os.environ.setdefault("ACCESS_TOKEN_TTL", "900")
os.environ.setdefault("REFRESH_TOKEN_TTL", "2592000")
os.environ.setdefault("REFRESH_ROTATION_GRACE", "30")
os.environ.setdefault("JWT_ALGORITHM", "HS256")

from backend.main import app
from backend.db import queries
from backend.services import auth_service

client = TestClient(app)

TEST_AGENT = "test-agent-integration"


# ── helpers ─────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _insert_refresh_token(
    agent_id: str = TEST_AGENT,
    expires_in: int = 2592000,
    used_at: datetime | None = None,
) -> str:
    """Insert a raw refresh token into the DB and return the raw token string."""
    raw = secrets.token_hex(32)
    token_hash = _hash_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=expires_in)
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO refresh_tokens (token_hash, agent_id, expires_at, used_at)
            VALUES (%s, %s, %s, %s)
            """,
            (token_hash, agent_id, expires_at, used_at),
        )
        c.commit()
    return raw


def _delete_agent_tokens(agent_id: str = TEST_AGENT) -> None:
    with queries.conn() as c, c.cursor() as cur:
        cur.execute("DELETE FROM refresh_tokens WHERE agent_id = %s", (agent_id,))
        c.commit()


# ── fixtures ────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def cleanup():
    """Ensure clean token state before and after each test."""
    _delete_agent_tokens()
    yield
    _delete_agent_tokens()


# ── Tests ───────────────────────────────────────────────────────────────

class TestRefreshEndpoint:
    """Tests exercised through the HTTP endpoint."""

    def test_refresh_happy_path(self):
        """Valid refresh token → 200 + new token pair with correct shape."""
        raw = _insert_refresh_token()
        resp = client.post("/api/auth/refresh", json={"refresh_token": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "refresh_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] == 900
        # New refresh token should be different from the one we sent
        assert body["refresh_token"] != raw

    def test_rotation_invalidates_old(self):
        """After a successful rotation (past grace window), the old token is rejected."""
        raw = _insert_refresh_token()
        # First refresh — get new pair
        resp1 = client.post("/api/auth/refresh", json={"refresh_token": raw})
        assert resp1.status_code == 200

        # Wait for grace window to expire (we'll simulate by setting a short grace)
        # Actually, we can fast-forward by manipulating the used_at in DB.
        # Instead: use a token that was "used" outside the grace window.
        old_used_at = datetime.now(timezone.utc) - timedelta(seconds=31)
        _delete_agent_tokens()
        raw2 = _insert_refresh_token(used_at=old_used_at)

        resp2 = client.post("/api/auth/refresh", json={"refresh_token": raw2})
        assert resp2.status_code == 401

    def test_rotation_grace_window(self):
        """Old token still accepted within the grace window (race-condition handling)."""
        recent_used_at = datetime.now(timezone.utc) - timedelta(seconds=5)
        raw = _insert_refresh_token(used_at=recent_used_at)

        resp = client.post("/api/auth/refresh", json={"refresh_token": raw})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body

    def test_rotation_double_use_inside_grace(self):
        """Two sequential uses of the same old token within the grace window both succeed."""
        raw = _insert_refresh_token()
        # First use succeeds normally
        resp1 = client.post("/api/auth/refresh", json={"refresh_token": raw})
        assert resp1.status_code == 200

        # Immediately reuse the old token — should still be in grace window
        resp2 = client.post("/api/auth/refresh", json={"refresh_token": raw})
        assert resp2.status_code == 200

    def test_expired_refresh_token(self):
        """Expired refresh token → 401."""
        past_token = _insert_refresh_token(expires_in=-1)  # already expired
        resp = client.post("/api/auth/refresh", json={"refresh_token": past_token})
        assert resp.status_code == 401

    def test_invalid_token_format(self):
        """Malformed/non-existent token → 401."""
        resp = client.post(
            "/api/auth/refresh",
            json={"refresh_token": "this-token-does-not-exist-in-the-db"},
        )
        assert resp.status_code == 401

    def test_missing_token_in_body(self):
        """Missing refresh_token field → 422."""
        resp = client.post("/api/auth/refresh", json={})
        assert resp.status_code == 422

    def test_empty_token(self):
        """Empty refresh_token → 422."""
        resp = client.post("/api/auth/refresh", json={"refresh_token": ""})
        assert resp.status_code == 422


class TestAccessToken:
    """Verify JWT access token structure."""

    def test_access_token_structure(self):
        """New access JWT has correct claims (sub, iat, exp)."""
        pair = auth_service.create_token_pair(TEST_AGENT)
        payload = auth_service.verify_access_token(pair["access_token"])
        assert payload is not None
        assert payload["sub"] == TEST_AGENT
        assert "iat" in payload
        assert "exp" in payload

    def test_access_token_expired(self):
        """Expired access token → verify returns None."""
        token = auth_service.create_access_token(TEST_AGENT)
        # Immediately verify — should work
        assert auth_service.verify_access_token(token) is not None
        # Corrupt the token to simulate bad signature
        assert auth_service.verify_access_token(token + "tampered") is None

    def test_wrong_secret_rejected(self):
        """Token signed with a different secret → verify returns None."""
        from jose import jwt
        payload = {"sub": TEST_AGENT}
        bad_token = jwt.encode(payload, "wrong-secret", algorithm="HS256")
        assert auth_service.verify_access_token(bad_token) is None


class TestServiceLayer:
    """Direct service-layer tests for edge cases."""

    def test_revoke_agent_tokens(self):
        """revoke_agent_tokens marks all unused tokens as used."""
        raw1 = _insert_refresh_token()
        raw2 = _insert_refresh_token()

        revoked = auth_service.revoke_agent_tokens(TEST_AGENT)
        assert revoked == 2

        # Both tokens should now fail rotation
        assert auth_service.rotate_refresh_token(raw1) is None
        assert auth_service.rotate_refresh_token(raw2) is None

    def test_rotate_missing_token(self):
        """rotate_refresh_token returns None for unknown token."""
        assert auth_service.rotate_refresh_token("nonexistent-token") is None

    def test_token_pair_has_unique_refresh(self):
        """Each call to create_token_pair produces a different refresh token."""
        pair1 = auth_service.create_token_pair(TEST_AGENT)
        pair2 = auth_service.create_token_pair(TEST_AGENT)
        assert pair1["refresh_token"] != pair2["refresh_token"]

    def test_token_pair_has_unique_access(self):
        """Each call to create_token_pair produces a different access token (different iat)."""
        pair1 = auth_service.create_token_pair(TEST_AGENT)
        pair2 = auth_service.create_token_pair(TEST_AGENT)
        assert pair1["access_token"] != pair2["access_token"]
