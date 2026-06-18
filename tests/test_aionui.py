import os
import tempfile

import pytest

from backend.aionui import AionUiClient, AionUiReader

HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


@pytest.fixture
def client():
    return AionUiClient(HOST)


@pytest.fixture
def reader():
    r = AionUiReader(DB)
    yield r
    r.close()


def test_list_agents(client):
    agents = client.list_agents()
    assert len(agents) > 0
    names = [a.get("name") for a in agents]
    assert "OpenCode" in names


def test_create_conversation_and_send(client):
    workspace = "/opt/aipc/conductor/workspace/_smoke"
    os.makedirs(workspace, exist_ok=True)

    conv_id = client.create_conversation(
        workspace=workspace,
        model="opencode/deepseek-v4-flash-free",
    )
    assert conv_id is not None
    assert isinstance(conv_id, str) and len(conv_id) > 0

    msg_id = client.send_message(conv_id, "say hello and stop")
    assert msg_id is not None

    # Wait briefly for agent to process
    import time
    time.sleep(5)

    conv = client.get_conversation(conv_id)
    assert conv["status"] in ("running", "finished")


def test_reader_messages(client, reader):
    # Create a quick conversation to read messages from
    workspace = "/opt/aipc/conductor/workspace/_smoke"
    conv_id = client.create_conversation(
        workspace=workspace,
        model="opencode/deepseek-v4-flash-free",
    )
    client.send_message(conv_id, "list files")
    import time
    time.sleep(5)

    msgs = reader.messages_for(conv_id)
    assert len(msgs) >= 1
    # At least the user message should be there
    user_msgs = [m for m in msgs if m.get("position") == "right"]
    assert len(user_msgs) >= 1


def test_list_assistants(client):
    assistants = client.list_assistants()
    assert len(assistants) > 0
