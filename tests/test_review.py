"""File 09 — Goal Review + Scoring"""
import json
import os
import time

from dotenv import load_dotenv
import pytest

load_dotenv("/opt/aipc/conductor/.env")

from backend.aionui import AionUiClient, AionUiReader
from backend.observability.ingest import ingest_run
from backend.review import score_node, gather_evidence
from backend.review.judge import judge_text

HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
DB = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


def test_judge_text_mock():
    """Text judge returns valid JSON with correct keys."""
    def mock_llm(prompt: str) -> str:
        return json.dumps({
            "score": 0.85,
            "pass": True,
            "reason": "Implementation meets all requirements.",
        })

    result = judge_text(
        "Add a /health endpoint returning JSON",
        {"files": [{"path": "app/routes.py", "size": 100}], "last_output": "done",
         "test_result": "passed (exit 0)"},
        llm_call=mock_llm,
    )

    assert "score" in result
    assert "pass" in result
    assert "reason" in result
    assert 0 <= result["score"] <= 1
    assert result["pass"] is True


@pytest.mark.slow
def test_score_node_from_live_run():
    """End-to-end: run a task, ingest to Langfuse, score the trace."""
    client = AionUiClient(HOST)
    workspace = "/opt/aipc/conductor/workspace/_smoke"
    os.makedirs(workspace, exist_ok=True)

    # Create a conversation asking for a simple task
    conv_id = client.create_conversation(
        preset_agent_type="acp",
        workspace=workspace,
        model="opencode/deepseek-v4-flash-free",
    )
    client.send_message(conv_id, "write a hello.py file that prints hello world and stop")
    time.sleep(12)

    # Ingest into Langfuse
    reader = AionUiReader(DB)
    trace_id = ingest_run(
        task_id="review-smoke",
        plan_id="review-test",
        agent_config="backend-executor",
        engine="opencode",
        model="deepseek-v4-flash",
        conversation_id=conv_id,
        reader=reader,
    )
    messages = reader.messages_for(conv_id)
    reader.close()

    assert trace_id is not None

    # Gather evidence
    evidence = gather_evidence(
        worktree_path=workspace,
        conversation_messages=messages,
        expected_files=["hello.py"],
    )

    # Score the node
    node = {
        "id": "review-node",
        "agent_config": "opencode:backend-executor",
        "role": "executor",
        "success": "A hello.py file exists that prints hello world",
    }

    final = score_node(node, trace_id, evidence)

    print(f"\nScore result: {json.dumps(final, indent=2)}")
    print(f"Evidence files: {[f['path'] for f in evidence['files'][:5]]}")
    print(f"Test result: {evidence['test_result']}")
    print(f"Langfuse trace: http://127.0.0.1:3001/trace/{trace_id}")

    assert "score" in final
    assert 0 <= final["score"] <= 1
    assert "reason" in final
    assert "pass" in final
