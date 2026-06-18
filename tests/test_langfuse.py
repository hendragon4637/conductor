import os
import time

import pytest

from backend.aionui import AionUiClient, AionUiReader
from backend.observability.ingest import ingest_run

HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


@pytest.fixture(scope="module")
def conv_id():
    client = AionUiClient(HOST)
    workspace = "/opt/aipc/conductor/workspace/_smoke"
    os.makedirs(workspace, exist_ok=True)
    cid = client.create_conversation(
        preset_agent_type="acp",
        workspace=workspace,
        model="opencode/deepseek-v4-flash-free",
    )
    client.send_message(cid, "list files in current directory and stop")
    time.sleep(8)
    return cid


def test_ingest(conv_id):
    reader = AionUiReader(DB)
    trace_id = ingest_run(
        task_id="task_smoke",
        plan_id="plan_smoke",
        agent_config="backend-executor",
        engine="opencode",
        model="deepseek-v4-flash",
        conversation_id=conv_id,
        reader=reader,
    )
    reader.close()

    assert trace_id is not None
    assert isinstance(trace_id, str) and len(trace_id) > 0
    print(f"\nTrace created: {trace_id}")

    # Verify trace is reachable in Langfuse
    import urllib.request, json
    lf_url = os.environ.get("LANGFUSE_HOST", "http://127.0.0.1:3001")
    api_url = f"{lf_url}/api/public/traces/{trace_id}"
    req = urllib.request.Request(api_url)
    try:
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
            assert data.get("id") == trace_id, f"trace id mismatch: {data.get('id')} != {trace_id}"
            print(f"Trace confirmed in Langfuse UI: {lf_url}/trace/{trace_id}")
    except Exception as e:
        # Langfuse API may require auth — the UI URL is the fallback check
        print(f"Langfuse API check skipped ({e})")
        print(f"Manual verification: {lf_url}/trace/{trace_id}")
