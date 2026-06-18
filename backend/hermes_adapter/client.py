"""HTTP client for the Hermes Agent REST API.

Hermes runs under Docker sandbox with the Conductor worktree mounted at
``/workspace``.  Conductor sends ONE goal per node; Hermes self-decomposes
and routes to its own subagents internally.

API endpoints::

    POST   /v1/runs       → create_run(goal, worktree)
    GET    /v1/runs/{id}  → get_run_status(run_id)
    DELETE /v1/runs/{id}  → stop_run(run_id)
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any


class HermesClient:
    """Low-level HTTP client for Hermes Agent.

    Args:
        host: Hermes API base URL.  Falls back to ``HERMES_HOST`` env var
            or ``"http://localhost:8642"``.

    Raises:
        RuntimeError: On HTTP errors or connection failures (wraps
            ``urllib.error.HTTPError`` / ``urllib.error.URLError``).
    """

    def __init__(self, host: str | None = None) -> None:
        self.host: str = (host or os.environ.get("HERMES_HOST", "http://localhost:8642")).rstrip("/")

    # ── Public API ──────────────────────────────────────────────────────────

    def create_run(self, goal: str, worktree: str) -> dict[str, Any]:
        """POST ``/v1/runs`` — create a new Hermes run.

        Args:
            goal: The node goal / brief text.
            worktree: Absolute path to the Conductor worktree to mount.

        Returns:
            Run creation response (expected to contain ``run_id``).

        Raises:
            RuntimeError: If the HTTP request fails.
        """
        body: dict[str, Any] = {
            "goal": goal,
            "workspace": worktree,
        }
        return self._request("POST", "/v1/runs", body)

    def get_run_status(self, run_id: str) -> dict[str, Any]:
        """GET ``/v1/runs/{run_id}`` — poll run status.

        Returns:
            Status dict (expected to contain a ``status`` key with values
            such as ``"running"``, ``"completed"``, ``"failed"``, etc.).

        Raises:
            RuntimeError: If the HTTP request fails.
        """
        return self._request("GET", f"/v1/runs/{run_id}")

    def stop_run(self, run_id: str) -> dict[str, Any]:
        """DELETE ``/v1/runs/{run_id}`` — stop / cancel a run.

        Raises:
            RuntimeError: If the HTTP request fails.
        """
        return self._request("DELETE", f"/v1/runs/{run_id}")

    # ── Internal HTTP helpers ───────────────────────────────────────────────

    def _request(
        self,
        method: str,
        path: str,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Send an HTTP request to the Hermes API.

        Handles:
        - ``urllib.error.HTTPError`` (non-2xx responses)
        - ``urllib.error.URLError`` (connection refused, DNS failure, etc.)

        Returns:
            Parsed JSON response dict.
        """
        url = f"{self.host}{path}"
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(url, data=data, method=method)
        req.add_header("Content-Type", "application/json")

        api_key = os.environ.get("HERMES_API_KEY")
        if api_key:
            req.add_header("Authorization", f"Bearer {api_key}")

        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return dict(json.loads(resp.read()))
        except urllib.error.HTTPError as e:
            body_text = e.read().decode(errors="replace")
            raise RuntimeError(
                f"Hermes API {method} {path} failed (HTTP {e.code}): {body_text}"
            ) from e
        except urllib.error.URLError as e:
            raise RuntimeError(
                f"Hermes API {method} {path} connection failed: {e.reason}"
            ) from e
