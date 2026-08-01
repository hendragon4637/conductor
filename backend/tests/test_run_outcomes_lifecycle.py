"""File 06 — run-outcome lifecycle tests: merge escalation and image pipeline.

Covers guide 06.5 (merge conflict -> abort + blocked status + project pause,
no intake event), 06.5b (retry/skip/queue/weekly counter), and 06.6
(opt-in container image build + re-tag + prune).
"""
from __future__ import annotations

import tempfile
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from backend.worktree import lifecycle


def _cursor(conn: MagicMock) -> MagicMock:
    return conn.__enter__.return_value.cursor.return_value.__enter__.return_value


def _run_dict(**overrides) -> dict:
    base = {
        "id": "run_x",
        "plan_id": "plan_x",
        "project_id": "proj_x",
        "note": "build login flow",
        "merge_status": "pending",
        "worktree_status": "active",
    }
    base.update(overrides)
    return base


def _make_project(ws_root: Path, variants: dict | None = None) -> Path:
    """Create a project dir under workspace root with an optional manifest."""
    project_dir = ws_root / "proj_x"
    project_dir.mkdir(parents=True)
    if variants is not None:
        mf = project_dir / ".conductor"
        mf.mkdir(parents=True)
        (mf / "workspace.json").write_text(
            __import__("json").dumps({"variants": variants})
        )
    return project_dir


class TestFinalizeSuccessBlocked:
    def _call(self, ws_root, run=None):
        run = run or _run_dict()
        with patch.object(lifecycle, "_get_run", return_value=run), \
             patch.object(lifecycle, "_get_plan",
                          return_value={"plan_id": "plan_x", "project_id": "proj_x"}), \
             patch.object(lifecycle, "_get_active_worktree_root",
                          return_value=str(ws_root / "wt")), \
             patch.object(lifecycle, "_get_worktree_branch", return_value="feat/login"), \
             patch.object(lifecycle, "_update_run") as m_update, \
             patch.object(lifecycle, "pause_project") as m_pause, \
             patch.object(lifecycle, "_sched_cleanup") as m_clean:
            result = lifecycle.finalize_success(run["id"], workspace_root=str(ws_root))
        return result, m_update, m_pause, m_clean

    def test_pre_merge_check_failure_blocks_without_merge(self, tmp_path):
        with patch.object(lifecycle, "_run_pre_merge_checks",
                          return_value="pre-merge check failed: bash gates.sh: boom") as m_checks, \
             patch.object(lifecycle, "_git_merge") as m_merge:
            result, m_update, m_pause, m_clean = self._call(tmp_path)
        m_merge.assert_not_called()
        m_checks.assert_called_once()
        m_pause.assert_called_once_with("proj_x", reason="merge blocked on run run_x")
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["merge_status"] == "blocked"
        assert updates["run_x"]["worktree_status"] == "active"
        assert updates["run_x"]["merge_ref"] == "feat/login"
        m_clean.assert_not_called()

    def test_merge_conflict_aborts_and_blocks(self, tmp_path):
        err = "Merge conflict for branch feat/login: CONFLICT (content) in src/login.tsx"
        with patch.object(lifecycle, "_run_pre_merge_checks", return_value=None), \
             patch.object(lifecycle, "_git_merge", side_effect=RuntimeError(err)) as m_merge, \
             patch.object(lifecycle, "_git_merge_abort") as m_abort:
            result, m_update, m_pause, m_clean = self._call(tmp_path)
        m_merge.assert_called_once()
        m_abort.assert_called_once_with(str(tmp_path / "proj_x"))
        m_pause.assert_called_once_with("proj_x", reason="merge blocked on run run_x")
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["merge_status"] == "blocked"
        assert updates["run_x"]["merge_error"].startswith("Merge conflict for branch feat/login")
        m_clean.assert_not_called()

    def test_outcome_stays_success_on_block(self, tmp_path):
        """A blocked merge must not flip the run outcome — quality passed."""
        with patch.object(lifecycle, "_run_pre_merge_checks", return_value="pre-merge check failed: x"):
            result, m_update, m_pause, m_clean = self._call(tmp_path)
        blocked = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert "state" not in blocked["run_x"]
        assert "outcome" not in blocked["run_x"]


class TestFinalizeSuccessMerged:
    def test_merge_success_sets_merged_and_schedules_cleanup(self, tmp_path):
        run = _run_dict()
        with patch.object(lifecycle, "_get_run", return_value=run), \
             patch.object(lifecycle, "_get_plan",
                          return_value={"plan_id": "plan_x", "project_id": "proj_x"}), \
             patch.object(lifecycle, "_get_active_worktree_root",
                          return_value=str(tmp_path / "wt")), \
             patch.object(lifecycle, "_get_worktree_branch", return_value="feat/login"), \
             patch.object(lifecycle, "_run_pre_merge_checks", return_value=None), \
             patch.object(lifecycle, "_git_merge", return_value="abc123def") as m_merge, \
             patch.object(lifecycle, "_update_run") as m_update, \
             patch.object(lifecycle, "_sched_cleanup") as m_clean, \
             patch.object(lifecycle, "pause_project") as m_pause, \
             patch.object(lifecycle, "_git_merge_abort") as m_abort:
            result = lifecycle.finalize_success(run["id"], workspace_root=str(tmp_path))
        m_merge.assert_called_once()
        m_pause.assert_not_called()
        m_abort.assert_not_called()
        m_clean.assert_called_once_with("run_x", lifecycle.SUCCESS_TTL_DAYS)
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["merge_status"] == "merged"
        assert updates["run_x"]["worktree_status"] == "merged"
        assert updates["run_x"]["merge_commit"] == "abc123def"


class TestRetrySkipMerge:
    def test_retry_merge_resumes_project_on_success(self):
        merged = {"id": "run_x", "merge_status": "merged", "project_id": "proj_x"}
        with patch.object(lifecycle, "finalize_success", return_value=merged) as m_fin, \
             patch.object(lifecycle, "resume_project") as m_resume:
            result = lifecycle.retry_merge("run_x", workspace_root="/tmp")
        m_fin.assert_called_once_with("run_x", workspace_root="/tmp")
        m_resume.assert_called_once_with("proj_x")
        assert result == merged

    def test_retry_merge_keeps_project_paused_when_still_blocked(self):
        blocked = {"id": "run_x", "merge_status": "blocked", "project_id": "proj_x"}
        with patch.object(lifecycle, "finalize_success", return_value=blocked), \
             patch.object(lifecycle, "resume_project") as m_resume:
            lifecycle.retry_merge("run_x", workspace_root="/tmp")
        m_resume.assert_not_called()

    def test_skip_merge_records_and_resumes(self):
        run = _run_dict()
        with patch.object(lifecycle, "_get_run",
                          side_effect=[run, {**run, "merge_status": "skipped",
                                             "merge_error": "not merging this sprint"}]), \
             patch.object(lifecycle, "_update_run") as m_update, \
             patch.object(lifecycle, "resume_project") as m_resume:
            result = lifecycle.skip_merge("run_x", "not merging this sprint")
        m_resume.assert_called_once_with("proj_x")
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["merge_status"] == "skipped"
        assert updates["run_x"]["merge_error"] == "not merging this sprint"
        assert result["merge_status"] == "skipped"


class TestBlockedQueue:
    def test_blocked_merge_queue_returns_rows(self):
        conn = MagicMock()
        cur = _cursor(conn)
        cur.fetchall.return_value = [{"id": "run_x", "merge_status": "blocked"}]
        with patch("psycopg.connect", return_value=conn):
            rows = lifecycle.blocked_merge_queue()
        assert rows == [{"id": "run_x", "merge_status": "blocked"}]
        sql = cur.execute.call_args[0][0]
        assert "merge_status = 'blocked'" in sql

    def test_weekly_blocked_count_queries_7_days(self):
        conn = MagicMock()
        cur = _cursor(conn)
        cur.fetchone.return_value = (2,)
        with patch("psycopg.connect", return_value=conn):
            count = lifecycle.weekly_blocked_merge_count()
        assert count == 2
        sql = cur.execute.call_args[0][0]
        assert "7 days" in sql

    def test_pause_project_upserts_intake_paused(self):
        conn = MagicMock()
        with patch("psycopg.connect", return_value=conn):
            lifecycle.pause_project("proj_x", "merge blocked on run run_x")
        cur = _cursor(conn)
        sql = cur.execute.call_args[0][0]
        params = cur.execute.call_args[0][1]
        assert "intake_paused" in sql and "paused_reason" in sql
        assert params[0] == "proj_x"
        assert params[1] == "merge blocked on run run_x"
        assert params[2] == "merge blocked on run run_x"

    def test_resume_project_clears_pause(self):
        conn = MagicMock()
        with patch("psycopg.connect", return_value=conn):
            lifecycle.resume_project("proj_x")
        cur = _cursor(conn)
        sql = cur.execute.call_args[0][0]
        params = cur.execute.call_args[0][1]
        assert "intake_paused = false" in sql
        assert params[0] == "proj_x"


class TestRunPreMergeChecks:
    def test_no_checks_when_no_gate_and_no_container(self, tmp_path):
        project_dir = _make_project(tmp_path, variants={})
        err = lifecycle._run_pre_merge_checks(str(tmp_path), str(project_dir), _run_dict())
        assert err is None

    def test_gates_sh_passes(self, tmp_path):
        project_dir = _make_project(tmp_path, variants={})
        (tmp_path / "gates.sh").write_text("#!/bin/bash\necho ok\n")
        with patch.object(lifecycle.subprocess, "run") as m_run:
            m_run.return_value = SimpleNamespace(returncode=0, stderr="", stdout="ok")
            err = lifecycle._run_pre_merge_checks(str(tmp_path), str(project_dir), _run_dict())
        assert err is None
        cmd = m_run.call_args[0][0]
        assert cmd == ["bash", "gates.sh"]

    def test_gates_sh_failure_blocks_with_tail(self, tmp_path):
        project_dir = _make_project(tmp_path, variants={})
        (tmp_path / "gates.sh").write_text("#!/bin/bash\nexit 1\n")
        with patch.object(lifecycle.subprocess, "run") as m_run:
            m_run.return_value = SimpleNamespace(returncode=1, stderr="line1\nline2\nboom", stdout="")
            err = lifecycle._run_pre_merge_checks(str(tmp_path), str(project_dir), _run_dict())
        assert err is not None
        assert "pre-merge check failed" in err
        assert "boom" in err

    def test_container_variant_builds_candidate(self, tmp_path):
        project_dir = _make_project(tmp_path, variants={"container": {"enabled": True}})
        with patch.object(lifecycle.subprocess, "run") as m_run:
            m_run.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
            err = lifecycle._run_pre_merge_checks(str(tmp_path), str(project_dir), _run_dict())
        assert err is None
        cmd = m_run.call_args[0][0]
        assert cmd[:3] == ["docker", "build", "-t"]
        assert cmd[-1] == "."


class TestFinalizeImage:
    def _setup(self, tmp_path, variants=None):
        project_dir = _make_project(tmp_path, variants=variants)
        return project_dir

    def test_skipped_without_container_variant(self, tmp_path):
        self._setup(tmp_path, variants={})
        run = _run_dict()
        with patch.object(lifecycle, "_get_run",
                          side_effect=[run, {**run, "image_status": "skipped"}]), \
             patch.object(lifecycle, "_get_plan",
                          return_value={"plan_id": "plan_x", "project_id": "proj_x"}), \
             patch.object(lifecycle, "_update_run") as m_update:
            result = lifecycle.finalize_image("run_x", workspace_root=str(tmp_path))
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["image_status"] == "skipped"
        assert result["image_status"] == "skipped"

    def test_builds_retags_and_prunes(self, tmp_path):
        self._setup(tmp_path, variants={"container": {"enabled": True}})
        with patch.object(lifecycle, "_get_run", return_value=_run_dict()), \
             patch.object(lifecycle, "_get_plan",
                          return_value={"plan_id": "plan_x", "project_id": "proj_x"}), \
             patch.object(lifecycle, "_master_sha", return_value="abcdef123456"), \
             patch.object(lifecycle, "_docker_tag") as m_tag, \
             patch.object(lifecycle, "_image_exists", return_value=True), \
             patch.object(lifecycle, "_prune_image_tags") as m_prune, \
             patch.object(lifecycle, "_update_run") as m_update:
            result = lifecycle.finalize_image("run_x", workspace_root=str(tmp_path))
        tag_calls = m_tag.call_args_list
        assert call("conductor/proj_x:candidate", "conductor/proj_x:abcdef") in tag_calls
        assert call("conductor/proj_x:abcdef", "conductor/proj_x:latest") in tag_calls
        m_prune.assert_called_once_with("conductor/proj_x", keep="conductor/proj_x:abcdef")
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["image_status"] == "built"
        assert updates["run_x"]["image_tag"] == "conductor/proj_x:abcdef"

    def test_tag_failure_records_failed(self, tmp_path):
        self._setup(tmp_path, variants={"container": {"enabled": True}})
        with patch.object(lifecycle, "_get_run", return_value=_run_dict()), \
             patch.object(lifecycle, "_get_plan",
                          return_value={"plan_id": "plan_x", "project_id": "proj_x"}), \
             patch.object(lifecycle, "_master_sha", return_value="abcdef123456"), \
             patch.object(lifecycle, "_docker_tag",
                          side_effect=RuntimeError("docker tag failed: no such image")), \
             patch.object(lifecycle, "_update_run") as m_update:
            result = lifecycle.finalize_image("run_x", workspace_root=str(tmp_path))
        updates = {c.args[0]: c.kwargs for c in m_update.call_args_list}
        assert updates["run_x"]["image_status"] == "failed"
        assert "docker tag failed" in updates["run_x"]["image_error"]


class TestImageHelpers:
    def test_image_name_sanitizes_project_id(self):
        assert lifecycle._image_name("My Project/Web!") == "conductor/my-project-web"

    def test_image_name_fallback(self):
        assert lifecycle._image_name("!!!" ) == "conductor/conductor"

    def test_verify_before_prune_keeps_newest_three(self):
        m_run = MagicMock()
        lines = [
            "conductor/p:aaaaaa|2026-06-30 10:00:00",
            "conductor/p:abcdef|2026-07-01 10:00:00",
            "conductor/p:123456|2026-07-02 10:00:00",
            "conductor/p:999999|2026-07-03 10:00:00",
            "conductor/p:latest|2026-07-03 11:00:00",
            "conductor/p:candidate|2026-07-03 11:30:00",
        ]
        m_run.side_effect = [
            SimpleNamespace(returncode=0, stdout="\n".join(lines)),
            SimpleNamespace(returncode=0, stdout=""),
        ]
        with patch.object(lifecycle.subprocess, "run", m_run):
            lifecycle._prune_image_tags("conductor/p", keep="conductor/p:999999")
        rm_calls = [c.args[0] for c in m_run.call_args_list if "rmi" in c.args[0]]
        # retention=3 keeps newest 3 sha tags (999999 kept, then 123456, abcdef)
        assert rm_calls == [["docker", "rmi", "conductor/p:aaaaaa"]]

    def test_git_merge_abort_runs_abort(self):
        with patch.object(lifecycle.subprocess, "run") as m_run:
            m_run.return_value = SimpleNamespace(returncode=0, stderr="", stdout="")
            lifecycle._git_merge_abort("/proj")
        cmd = m_run.call_args[0][0]
        assert cmd == ["git", "-C", "/proj", "merge", "--abort"]

    def test_git_merge_abort_survives_failure(self):
        with patch.object(lifecycle.subprocess, "run", side_effect=RuntimeError("no merge")):
            lifecycle._git_merge_abort("/proj")  # must not raise


class TestSummarizeConflict:
    def test_takes_first_line(self):
        err = RuntimeError("Merge conflict for branch feat/login: CONFLICT (content)\n  in src/login.tsx")
        summary = lifecycle._summarize_conflict(err)
        assert summary.startswith("Merge conflict for branch feat/login")
        assert "CONFLICT (content)" in summary

    def test_empty_fallback(self):
        assert lifecycle._summarize_conflict(RuntimeError("  ")) == "merge conflict"
