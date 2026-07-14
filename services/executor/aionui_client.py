"""Extracted AionUiClient — HTTP client for the AionUi API.

Duplicate of ``backend/aionui/client.py`` owned by executor-svc for
separation of concerns.  Used to spawn agents, send messages, and
manage teams via the AionUi REST API.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class AionUiClient:
    """HTTP client for the AionUi aioncore REST API."""

    def __init__(self, host: str):
        self.host = host.rstrip("/")

    def _post(self, path: str, body: dict) -> dict:
        url = f"{self.host}{path}"
        data = json.dumps(body).encode()
        req = urllib.request.Request(url, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode()
            raise RuntimeError(f"POST {path} failed ({e.code}): {body_text}") from e

    def _get(self, path: str) -> dict:
        url = f"{self.host}{path}"
        try:
            with urllib.request.urlopen(url) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            body_text = e.read().decode()
            raise RuntimeError(f"GET {path} failed ({e.code}): {body_text}") from e

    # ------------------------------------------------------------------
    # Agents
    # ------------------------------------------------------------------
    def list_agents(self) -> list[dict]:
        resp = self._get("/api/agents")
        return resp.get("data", resp)

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------
    def create_conversation(
        self,
        preset_agent_type: str = "acp",
        workspace: str = "",
        model: str | None = None,
        assistant_id: str | None = None,
        backend: str | None = None,
    ) -> str:
        body = {
            "name": "conductor-spawn",
            "type": preset_agent_type,
            "extra": {
                "workspace": workspace,
            },
        }
        if model:
            body["extra"]["current_model_id"] = model
        if preset_agent_type == "acp":
            body["extra"]["backend"] = backend or "opencode"
        if assistant_id:
            body["assistant_id"] = assistant_id
        resp = self._post("/api/conversations", body)
        return resp["data"]["id"]

    def send_message(self, conversation_id: str, text: str) -> str:
        resp = self._post(
            f"/api/conversations/{conversation_id}/messages",
            {"content": text, "role": "user"},
        )
        return resp["data"]["msg_id"]

    def get_messages(self, conversation_id: str) -> list[dict]:
        resp = self._get(f"/api/conversations/{conversation_id}/messages")
        return resp["data"]["items"]

    def get_conversation(self, conversation_id: str) -> dict:
        resp = self._get(f"/api/conversations/{conversation_id}")
        return resp["data"]

    def cancel_conversation(self, conversation_id: str) -> None:
        """Cancel any running turn so a new message can be sent.

        AionUi rejects POST /messages with 409 when the conversation is
        still running.  This calls /cancel with the current turn_id and
        polls until the conversation becomes idle (up to 30s).
        """
        import time as _time

        # 1. Check if running
        conv = self.get_conversation(conversation_id)
        runtime = conv.get("runtime", {})
        state = runtime.get("state", "")
        if state != "running":
            return

        turn_id = runtime.get("turn_id")
        if not turn_id:
            return

        # 2. POST /cancel with turn_id
        self._post(
            f"/api/conversations/{conversation_id}/cancel",
            {"turn_id": turn_id},
        )

        # 3. Poll until idle (can_send_message=true)
        for _ in range(30):
            _time.sleep(1)
            try:
                poll = self.get_conversation(conversation_id)
                pr = poll.get("runtime", {})
                if pr.get("state") == "idle" and pr.get("can_send_message") is True:
                    return
            except Exception:
                pass

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------
    def _resolve_assistant_id(self, backend: str) -> str:
        """Map a backend/engine name (e.g. ``opencode``) to a ``bare:...``
        assistant ID by querying the assistants catalog."""
        assistants = self.list_assistants()
        for a in assistants:
            agent = a.get("agent", {})
            if agent.get("acp_backend", "").lower() == backend.lower():
                return a["id"]
        raise ValueError(
            f"No assistant found for backend '{backend}' in AionUi catalog. "
            f"Available acp_backends: {[a.get('agent', {}).get('acp_backend') for a in assistants if a.get('agent', {}).get('acp_backend')]}"
        )

    def get_team(self, team_id: str) -> dict:
        """Fetch full team info including agents with slot_ids."""
        resp = self._get(f"/api/teams/{team_id}")
        data = resp.get("data", {})
        # Normalize assistants → agents for consistency
        if "assistants" in data and "agents" not in data:
            data["agents"] = data.pop("assistants")
        return data

    def send_team_message(self, team_id: str, slot_id: str, text: str) -> str:
        """Send a message to a team agent via the team messages endpoint
        (required for team conversations in AionUi v2.1.33+).

        Returns the message_id from the response.
        """
        resp = self._post(
            f"/api/teams/{team_id}/agents/{slot_id}/messages",
            {"content": text, "role": "user"},
        )
        return resp.get("data", {}).get("message_id", "")

    def create_team(
        self,
        name: str,
        workspace: str = "",
        agents: list[dict] | None = None,
    ) -> dict:
        """Create a team and return the full response data dict.

        Each agent dict may contain a ``backend`` key (deprecated) or an
        ``assistant_id`` key.  If ``assistant_id`` is absent and ``backend``
        is present, this method automatically resolves the backend to the
        correct ``bare:...`` assistant ID via the assistants catalog.
        """
        body = {"name": name}
        if workspace:
            body["workspace"] = workspace

        resolved_agents = []
        for agent in (agents or []):
            agent = dict(agent)
            if "assistant_id" not in agent and "backend" in agent:
                agent["assistant_id"] = self._resolve_assistant_id(agent.pop("backend"))
            elif "backend" in agent:
                agent.pop("backend")
            resolved_agents.append(agent)
        if resolved_agents:
            body["agents"] = resolved_agents

        resp = self._post("/api/teams", body)
        data = resp.get("data", {})

        # v2.1.33+ returns ``assistants`` instead of ``agents``; normalise.
        if "assistants" in data and "agents" not in data:
            data["agents"] = data.pop("assistants")
        return data

    # ------------------------------------------------------------------
    # Assistants
    # ------------------------------------------------------------------
    def list_assistants(self) -> list[dict]:
        resp = self._get("/api/assistants")
        return resp.get("data", resp)

    def create_assistant(self, name: str, context: str = "",
                         preset_agent_type: str = "acp") -> dict:
        resp = self._post("/api/assistants", {
            "name": name,
            "description": context,
            "preset_agent_type": preset_agent_type,
            "source": "user",
        })
        return resp["data"]
