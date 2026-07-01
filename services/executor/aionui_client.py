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

    # ------------------------------------------------------------------
    # Teams
    # ------------------------------------------------------------------
    def create_team(
        self,
        name: str,
        workspace: str = "",
        agents: list[dict] | None = None,
    ) -> dict:
        body = {"name": name}
        if workspace:
            body["workspace"] = workspace
        if agents:
            body["agents"] = agents
        resp = self._post("/api/teams", body)
        return resp.get("data", {})

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
