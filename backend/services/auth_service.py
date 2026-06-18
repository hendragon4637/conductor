"""Auth service: JWT access tokens, opaque refresh tokens with rotation."""
from __future__ import annotations

import hashlib
import os
import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

from jose import jwt, JWTError

from backend.db import queries

# ── config ──────────────────────────────────────────────────────────────

JWT_SECRET = os.environ["JWT_SECRET_KEY"]
JWT_ALGORITHM = os.environ.get("JWT_ALGORITHM", "HS256")
ACCESS_TTL = int(os.environ.get("ACCESS_TOKEN_TTL", "900"))
REFRESH_TTL = int(os.environ.get("REFRESH_TOKEN_TTL", "2592000"))  # 30 days
GRACE_SECONDS = int(os.environ.get("REFRESH_ROTATION_GRACE", "30"))


# ── helpers ─────────────────────────────────────────────────────────────

def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _generate_opaque_token() -> str:
    return secrets.token_hex(32)  # 64 hex chars = 256 bits


# ── access tokens (JWT) ────────────────────────────────────────────────

def create_access_token(agent_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": agent_id,
        "iat": now,
        "exp": now + timedelta(seconds=ACCESS_TTL),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_access_token(token: str) -> Optional[dict]:
    """Decode and validate a JWT access token.

    Returns the payload dict on success, or None if the token is
    expired, malformed, or signed with a different key.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except JWTError:
        return None


# ── refresh tokens (opaque, stored as SHA-256 hash) ────────────────────

def create_refresh_token(agent_id: str) -> str:
    """Generate an opaque refresh token, persist its hash to DB, return raw token."""
    raw = _generate_opaque_token()
    token_hash = _hash_token(raw)
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=REFRESH_TTL)

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            INSERT INTO refresh_tokens (token_hash, agent_id, expires_at)
            VALUES (%s, %s, %s)
            """,
            (token_hash, agent_id, expires_at),
        )
        c.commit()

    return raw


def create_token_pair(agent_id: str) -> dict:
    """Return a dict with access_token, refresh_token, token_type, expires_in."""
    access = create_access_token(agent_id)
    refresh = create_refresh_token(agent_id)
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "bearer",
        "expires_in": ACCESS_TTL,
    }


# ── rotation ───────────────────────────────────────────────────────────

def rotate_refresh_token(old_token: str) -> Optional[dict]:
    """Validate and rotate a refresh token.

    Steps:
    1. Hash the incoming token, look it up in DB.
    2. Reject if not found, expired, or replayed outside the grace window.
    3. Mark old token as used, insert new token pair.
    4. Return the new token pair dict, or None to indicate 401.

    Race-condition handling:
    - If the old token was already used within the last GRACE_SECONDS,
      we still accept it once to handle networks where the new token
      hasn't arrived yet.
    """
    token_hash = _hash_token(old_token)
    now = datetime.now(timezone.utc)

    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = %s",
            (token_hash,),
        )
        row = cur.fetchone()

    if row is None:
        return None  # unknown token

    # Expired?
    if row["expires_at"] < now:
        return None

    # Already used?
    if row["used_at"] is not None:
        grace_end = row["used_at"] + timedelta(seconds=GRACE_SECONDS)
        if now > grace_end:
            # Outside grace window — reject (replay attack)
            return None
        # Inside grace window — allow one more rotation
        # (the client may not have received the new pair yet)

    # Rotate
    agent_id = row["agent_id"]
    new_access = create_access_token(agent_id)
    new_refresh_raw = _generate_opaque_token()
    new_refresh_hash = _hash_token(new_refresh_raw)
    new_expires_at = now + timedelta(seconds=REFRESH_TTL)

    with queries.conn() as c, c.cursor() as cur:
        # Mark old token as used
        cur.execute(
            "UPDATE refresh_tokens SET used_at = %s, replaced_by = %s WHERE token_hash = %s",
            (now, new_refresh_hash, token_hash),
        )
        # Insert new token
        cur.execute(
            """
            INSERT INTO refresh_tokens (token_hash, agent_id, expires_at, created_at)
            VALUES (%s, %s, %s, %s)
            """,
            (new_refresh_hash, agent_id, new_expires_at, now),
        )
        c.commit()

    return {
        "access_token": new_access,
        "refresh_token": new_refresh_raw,
        "token_type": "bearer",
        "expires_in": ACCESS_TTL,
    }


# ── revocation (optional, for future use) ──────────────────────────────

def revoke_agent_tokens(agent_id: str) -> int:
    """Mark all unused refresh tokens for an agent as used/revoked.

    Returns the number of tokens revoked.
    """
    with queries.conn() as c, c.cursor() as cur:
        cur.execute(
            """
            UPDATE refresh_tokens
               SET used_at = now()
             WHERE agent_id = %s AND used_at IS NULL
            RETURNING token_hash
            """,
            (agent_id,),
        )
        revoked = cur.fetchall()
        c.commit()
    return len(revoked)
