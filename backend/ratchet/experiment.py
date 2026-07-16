from __future__ import annotations

import os
import subprocess
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any

from backend.aionui import AionUiClient, AionUiReader
from backend.observability.ingest import ingest_run
from backend.review import score_node, gather_evidence
from backend.evaluator.ratchet_lock import acquire_ratchet_lock, release_ratchet_lock, assert_no_ratchet_lock

HOST = os.environ.get("AIONUI_HOST", "http://127.0.0.1:40937")
AGENT_CONFIGS_DIR = Path("/opt/aipc/conductor/skills")
DB_PATH = os.environ.get(
    "AIONUI_DB",
    "/home/aipc/.config/AionUi/aionui/aionui-backend.db",
)


def _find_golden_path(agent_config: str) -> Path:
    cfg_no_prefix = agent_config.split(":", 1)[-1]
    cfg_short = cfg_no_prefix.replace("backend-", "").replace("frontend-", "")
    candidates = [
        AGENT_CONFIGS_DIR / cfg_no_prefix / "golden",
        AGENT_CONFIGS_DIR / cfg_short / "golden",
        AGENT_CONFIGS_DIR / "backend" / cfg_short / "golden",
        AGENT_CONFIGS_DIR / "frontend" / cfg_short / "golden",
        Path("/opt/aipc/conductor/golden") / cfg_short,
    ]
    for p in candidates:
        if p.is_dir():
            return p
    return candidates[0]


_OPENCODE_CONFIG = (
    '{"$schema":"https://opencode.ai/config.json",'
    '"permission":{"edit":"allow","webfetch":"allow",'
    '"bash":{"*":"allow"}}}'
)


def _write_opencode_config(workspace: Path) -> None:
    cfg = workspace / "opencode.json"
    if not cfg.exists():
        cfg.write_text(_OPENCODE_CONFIG)


def _init_git_workspace(workspace: Path) -> None:
    """Initialise a git repo in the experiment workspace.
    
    Required so that ``collect_artifact`` (used by the L2 judge) can
    produce a useful diff of the agent's output.
    """
    try:
        subprocess.run(["git", "init"], cwd=str(workspace),
                       capture_output=True, timeout=15)
        subprocess.run(["git", "config", "user.email", "ratchet@conductor.local"],
                       cwd=str(workspace), capture_output=True, timeout=15)
        subprocess.run(["git", "config", "user.name", "Ratchet"],
                       cwd=str(workspace), capture_output=True, timeout=15)
        subprocess.run(["git", "add", "-A"], cwd=str(workspace),
                       capture_output=True, timeout=15)
        subprocess.run(["git", "commit", "-m", "init workspace"],
                       cwd=str(workspace), capture_output=True, timeout=15)
    except Exception:
        pass  # workspace diff is best-effort


def _extract_section(text: str, header: str) -> str:
    lines = text.split("\n")
    in_section = False
    parts: list[str] = []
    for line in lines:
        if line.strip().startswith(header):
            in_section = True
            continue
        if in_section and line.strip().startswith("## "):
            break
        if in_section:
            parts.append(line)
    return "\n".join(p.strip() for p in parts if p.strip()).strip()


def _apply_file(target: str, candidate: str) -> None:
    if target == "skill":
        path = AGENT_CONFIGS_DIR / "backend" / "executor" / "SKILL.md"
    elif target == "agents_md":
        path = AGENT_CONFIGS_DIR / "backend" / "executor" / "AGENTS.md"
    elif target == "prompt":
        path = AGENT_CONFIGS_DIR / "backend" / "executor" / "PROMPT.md"
    else:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(candidate)


def _run_task(client: AionUiClient, workspace: Path, intent: str) -> str:
    conv_id = client.create_conversation(
        preset_agent_type="acp",
        workspace=str(workspace),
            model="nvidia/openai/gpt-oss-120b",
    )
    client.send_message(conv_id, intent)
    time.sleep(15)
    return conv_id


def _score_with_evaluator(
    task: dict[str, Any], worktree_path: str, trace_id: str | None = None,
) -> float:
    """Score a task using the evaluator's L2 rubric judge.

    Generates checks from the golden task's success criteria, collects
    the artifact from the worktree, and runs the L2 judge (LLM call)
    which writes ``goal_review`` to Langfuse along the way.

    This replaces the old deterministic ``_score_task`` so the ratchet
    optimises against the same quality signal the evaluator produces
    during gated operations.
    """
    from backend.evaluator.generate import generate_checks
    from backend.evaluator.l2_judge import run_l2

    nc = generate_checks(
        node_id="experiment",
        task=task.get("intent", ""),
        success_criterion=task.get("success", ""),
        node_index=0,
        total_nodes=1,
    )
    result = run_l2(
        checks=nc.checks,
        worktree=worktree_path,
        trace_id=trace_id,
    )
    return result.score


def _save_experiment(
    experiment_id: str,
    agent_config: str,
    target: str,
    dataset: str,
    baseline_score: float,
    candidate_score: float,
    judge_model: str | None = None,
    rubric_id: str | None = None,
) -> None:
    import psycopg
    db_url = os.environ.get("DATABASE_URL", "")
    if not db_url:
        return
    with psycopg.connect(db_url) as c:
        with c.cursor() as cur:
            cur.execute(
                """INSERT INTO experiments
                   (experiment_id, agent_config_id, target, dataset,
                    baseline_score, candidate_score, decision,
                    judge_model, rubric_id)
                   VALUES (%s, %s, %s, %s, %s, %s, 'running', %s, %s)
                   ON CONFLICT (experiment_id) DO UPDATE SET
                     baseline_score = EXCLUDED.baseline_score,
                     candidate_score = EXCLUDED.candidate_score,
                     judge_model = EXCLUDED.judge_model,
                     rubric_id = EXCLUDED.rubric_id
                """,
                (experiment_id, agent_config, target, dataset,
                 str(baseline_score), str(candidate_score),
                 judge_model, rubric_id),
            )
        c.commit()


def _current_judge_model() -> str:
    return os.environ.get("JUDGE_MODEL_ID", "gpt-oss-120b")


def _resolve_active_rubric_id(capability: str) -> str | None:
    url = os.environ.get("DATABASE_URL", "")
    if not url:
        return None
    try:
        import psycopg
        with psycopg.connect(url) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT id FROM judge_rubrics WHERE capability = %s AND active = TRUE LIMIT 1",
                    (capability,),
                )
                row = cur.fetchone()
                return row[0] if row else None
    except Exception:
        return None


def run_experiment(
    agent_config: str,
    mutation: dict[str, Any],
    dataset: str | None = None,
    max_tasks: int = 0,
) -> dict[str, Any]:
    """Run baseline vs candidate experiment.

    Acquires a ratchet lock on the agent_config to prevent concurrent
    main/judge ratchet cycles on the same capability.

    Args:
        agent_config: The config to test (e.g. "backend-executor").
        mutation: Dict with keys ``target`` and ``candidate``.
        dataset: Optional dataset name; defaults to agent_config.
        max_tasks: Max golden tasks to run (0=all).

    Returns:
        Dict with ``experiment_id``, ``baseline_score``, ``candidate_score``,
        ``delta``, and per-task results.
    """
    # Lock: prevent concurrent main/judge ratchets on the same agent_config
    assert_no_ratchet_lock(agent_config, "main")
    if not acquire_ratchet_lock(agent_config, "main"):
        raise RuntimeError(
            f"REFUSED: another ratchet is active on {agent_config} — "
            f"one ruler at a time. Cannot start run_experiment."
        )

    experiment_id = f"exp-{uuid.uuid4().hex[:12]}"
    dataset = dataset or agent_config
    judge_model = _current_judge_model()
    rubric_id = _resolve_active_rubric_id(agent_config)

    try:
        golden_path = _find_golden_path(agent_config)
        golden_files = sorted(golden_path.glob("t*.md"))
        if not golden_files:
            raise ValueError(f"No golden tasks in {golden_path}")

        if max_tasks > 0:
            golden_files = golden_files[:max_tasks]

        golden_tasks = []
        for f in golden_files:
            text = f.read_text()
            golden_tasks.append({
                "file": f.name,
                "intent": _extract_section(text, "## User intent"),
                "success": _extract_section(text, "## Success criteria"),
            })

        if not golden_tasks:
            raise ValueError(f"No golden tasks parsed for {agent_config}")

        client = AionUiClient(HOST)
        reader = AionUiReader(DB_PATH)
        workspace = Path(tempfile.mkdtemp(prefix="ratchet-"))
        _write_opencode_config(workspace)
        _init_git_workspace(workspace)

        task_results = []
        baseline_scores = []
        candidate_scores = []

        for i, task in enumerate(golden_tasks):
            conv_id_b = _run_task(client, workspace, task["intent"])
            time.sleep(3)
            msgs_b = reader.messages_for(conv_id_b)
            trace_b = ingest_run(
                task_id=f"exp-{experiment_id[:8]}-b-{task['file'].replace('.md','')}",
                plan_id=experiment_id,
                agent_config=agent_config,
                engine="opencode",
                model="deepseek-v4-flash",
                conversation_id=conv_id_b,
                reader=reader,
            )
            score_b = _score_with_evaluator(task, str(workspace), trace_id=trace_b)
            baseline_scores.append(score_b)
            task_results.append({"task": task["file"], "baseline_score": score_b})

        _apply_file(mutation.get("target", ""), mutation.get("candidate", ""))

        for i, task in enumerate(golden_tasks):
            conv_id_c = _run_task(client, workspace, task["intent"])
            time.sleep(3)
            msgs_c = reader.messages_for(conv_id_c)
            trace_c = ingest_run(
                task_id=f"exp-{experiment_id[:8]}-c-{task['file'].replace('.md','')}",
                plan_id=experiment_id,
                agent_config=f"{agent_config}-mutated",
                engine="opencode",
                model="deepseek-v4-flash",
                conversation_id=conv_id_c,
                reader=reader,
            )
            score_c = _score_with_evaluator(task, str(workspace), trace_id=trace_c)
            candidate_scores.append(score_c)
            task_results[i]["candidate_score"] = score_c

        reader.close()

        avg_b = sum(baseline_scores) / len(baseline_scores) if baseline_scores else 0.0
        avg_c = sum(candidate_scores) / len(candidate_scores) if candidate_scores else 0.0

        _save_experiment(
            experiment_id, agent_config, mutation.get("target", ""), dataset,
            avg_b, avg_c, judge_model=judge_model, rubric_id=rubric_id,
        )
    finally:
        release_ratchet_lock(agent_config, "main")

    return {
        "experiment_id": experiment_id,
        "agent_config": agent_config,
        "dataset": dataset,
        "baseline_score": round(avg_b, 4),
        "candidate_score": round(avg_c, 4),
        "delta": round(avg_c - avg_b, 4),
        "task_results": task_results,
    }
