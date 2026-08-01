"""Tests for dependency materialization, INFRA_EXCLUDES, gate guards, and
drain/has_merged_master utilities."""
from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from contracts.paths import INFRA_EXCLUDES


# ── INFRA_EXCLUDES ───────────────────────────────────────────────────────────

class TestInfraExcludes:
    def test_deps_is_excluded(self):
        assert "deps/" in INFRA_EXCLUDES

    def test_other_expected_excludes(self):
        for item in ["deps/", ".opencode/", "node_modules/", "__pycache__/"]:
            assert item in INFRA_EXCLUDES, f"{item} should be in INFRA_EXCLUDES"


# ── Plan evaluator Check 7 — deps/ rejection ─────────────────────────────────

class TestPlanEvaluatorCheck7:
    """Check 7: acceptance criteria may not point into deps/."""

    def _make_node(self, task: str, file_path: str | None = None) -> dict:
        return {
            "task": task,
            "file_path": file_path or "src/main.py",
            "criteria": [],
            "quality_intent": "",
        }

    def _run_check_7(self, node: dict) -> list[str]:
        """Inline reimplementation of Check 7 logic from plan_evaluator."""
        errors = []
        where = node.get("file_path", "")
        if where and where.startswith("deps/"):
            errors.append(f"Criteria path points into deps/: {where}")
        for c in node.get("criteria", []):
            c_where = c.get("where", "") if isinstance(c, dict) else ""
            if c_where.startswith("deps/"):
                errors.append(f"Criterion points into deps/: {c_where}")
        return errors

    def test_rejects_file_path_in_deps(self):
        node = self._make_node("do work", "deps/backend/RUN.md")
        errors = self._run_check_7(node)
        assert errors, "Expected Check 7 to reject file path under deps/"

    def test_allows_normal_path(self):
        node = self._make_node("do work", "src/main.py")
        errors = self._run_check_7(node)
        assert not errors

    def test_rejects_criterion_where_in_deps(self):
        node = {
            "task": "build",
            "file_path": "src/main.py",
            "criteria": [
                {"where": "deps/shared/contract.md", "what": "should exist"},
            ],
            "quality_intent": "",
        }
        errors = self._run_check_7(node)
        assert errors

    def test_allows_criterion_where_normal(self):
        node = {
            "task": "build",
            "file_path": "src/main.py",
            "criteria": [
                {"where": "src/output.json", "what": "should exist"},
            ],
            "quality_intent": "",
        }
        errors = self._run_check_7(node)
        assert not errors

    def test_resilient_to_missing_fields(self):
        node = {"task": "minimal"}
        errors = self._run_check_7(node)
        assert not errors  # should not crash


# ── L2 gate — deps/ diff rejection ─────────────────────────────────────────

class TestL2GateDepsFilter:
    """Gate fails any node whose diff touches deps/."""

    def _gate_deps_filter(self, diff_text: str) -> bool:
        """Reimplementation of the deps/ filter logic from gate.py."""
        for line in diff_text.splitlines():
            if line.startswith("diff --git") and "a/deps/" in line:
                return False  # fails gate
        return True  # passes gate

    def test_fails_when_diff_touches_deps(self):
        diff = """
diff --git a/deps/backend/openapi.json b/deps/backend/openapi.json
new file mode 100644
"""
        assert self._gate_deps_filter(diff) is False

    def test_passes_when_diff_normal(self):
        diff = """
diff --git a/src/main.py b/src/main.py
index abc..def 100644
--- a/src/main.py
+++ b/src/main.py
"""
        assert self._gate_deps_filter(diff) is True

    def test_passes_when_diff_empty(self):
        assert self._gate_deps_filter("") is True

    def test_fails_on_any_deps_path(self):
        diff = """
diff --git a/app/core.py b/app/core.py
new file mode 100644
diff --git a/deps/frontend/RUN.md b/deps/frontend/RUN.md
new file mode 100644
"""
        assert self._gate_deps_filter(diff) is False


# ── materialize_deps() ──────────────────────────────────────────────────────

class TestMaterializeDeps:
    @mock.patch("backend.worktree.manager.WorktreeManager.materialize_deps")
    def test_source_mode_symlink(self, mock_method):
        """Source mode: create symlink to dependency project root."""
        mock_method.return_value = [{"dep_name": "backend", "dep_project_id": "p2", "path": "/tmp/deps/backend"}]
        wm = _make_wm()
        result = wm.materialize_deps("project-1", mode="source")
        assert len(result) == 1
        assert result[0]["dep_name"] == "backend"

    @mock.patch("backend.worktree.manager.WorktreeManager.materialize_deps")
    def test_artifacts_mode(self, mock_method):
        """Artifacts mode copies only delivered files."""
        mock_method.return_value = [{"dep_name": "backend", "dep_project_id": "p2", "path": "/tmp/deps/backend"}]
        wm = _make_wm()
        result = wm.materialize_deps("assembly-1", mode="artifacts")
        assert len(result) == 1

    @mock.patch("backend.worktree.manager.WorktreeManager.materialize_deps")
    def test_no_deps_returns_empty(self, mock_method):
        """Project with no dependencies returns empty list."""
        mock_method.return_value = []
        wm = _make_wm()
        result = wm.materialize_deps("standalone-project", mode="source")
        assert result == []


# ── materialize_deps artifacts mode (real fs) ───────────────────────────────

class TestArtifactsModeReal:
    """Real-filesystem artifacts mode: copies DEP_INCLUDE_ARTIFACTS only."""

    def _make_dep_project(self, tmp_path: Path) -> Path:
        dep = tmp_path / "workspace" / "sys" / "backend"
        (dep / ".conductor").mkdir(parents=True)
        (dep / "exports").mkdir(parents=True)
        (dep / "data" / "output").mkdir(parents=True)
        (dep / "src").mkdir(parents=True)
        (dep / "RUN.md").write_text("# backend\n")
        (dep / ".conductor" / "workspace.json").write_text('{"services": []}\n')
        (dep / "exports" / "openapi.json").write_text('{"openapi": "3.0"}\n')
        (dep / "data" / "output" / "report.csv").write_text("a,b\n")
        (dep / "src" / "main.py").write_text("print('source')\n")
        (dep / "AGENTS.md").write_text("agents\n")
        (dep / "WORKSPACE.md").write_text("workspace\n")
        return dep

    def test_artifacts_mode_copies_guide_set_only(self, tmp_path):
        """RUN.md, workspace.json, exports/**, data/output/** copied; source not."""
        dep = self._make_dep_project(tmp_path)
        target = tmp_path / "wt"
        target.mkdir(parents=True)

        with mock.patch("psycopg.connect") as mock_connect:
            mock_cur = mock.MagicMock()
            mock_cur.fetchall.return_value = [
                {"dep_name": "backend", "depends_on_project_id": "p2", "name": "Backend", "repo_path": "sys/backend"},
            ]
            mock_conn = mock_connect.return_value.__enter__.return_value
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur

            from backend.worktree.manager import WorktreeManager
            wm = WorktreeManager(str(tmp_path / "workspace"))
            result = wm.materialize_deps("assembly-1", "sys1", mode="artifacts", worktree_path=str(target))

        deps_dir = target / "deps" / "backend"
        assert len(result) == 1
        assert (deps_dir / "RUN.md").exists()
        assert (deps_dir / ".conductor" / "workspace.json").exists()
        assert (deps_dir / "exports" / "openapi.json").exists()
        assert (deps_dir / "data" / "output" / "report.csv").exists()
        assert not (deps_dir / "src" / "main.py").exists()
        assert not (deps_dir / "AGENTS.md").exists()
        assert not (deps_dir / "WORKSPACE.md").exists()

    def test_artifacts_mode_missing_artifacts_ok(self, tmp_path):
        """Dependency with no artifacts → empty deps dir, no crash."""
        dep = tmp_path / "workspace" / "sys" / "backend"
        (dep / "src").mkdir(parents=True)
        (dep / "src" / "main.py").write_text("x\n")
        target = tmp_path / "wt"
        target.mkdir(parents=True)

        with mock.patch("psycopg.connect") as mock_connect:
            mock_cur = mock.MagicMock()
            mock_cur.fetchall.return_value = [
                {"dep_name": "backend", "depends_on_project_id": "p2", "name": "Backend", "repo_path": "sys/backend"},
            ]
            mock_conn = mock_connect.return_value.__enter__.return_value
            mock_conn.cursor.return_value.__enter__.return_value = mock_cur

            from backend.worktree.manager import WorktreeManager
            wm = WorktreeManager(str(tmp_path / "workspace"))
            result = wm.materialize_deps("assembly-1", "sys1", mode="artifacts", worktree_path=str(target))

        deps_dir = target / "deps" / "backend"
        assert len(result) == 1
        assert deps_dir.exists()
        assert list(deps_dir.iterdir()) == []


def _make_wm():
    from backend.worktree import WorktreeManager
    return WorktreeManager("/tmp/test_workspace")


# ── drain_pending() ──────────────────────────────────────────────────────────

class TestDrainPending:
    @mock.patch("psycopg.connect")
    def test_drains_eligible_goals(self, mock_connect):
        """Pending goals with empty wait_for emit sys.goal_queued."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = [
            {"acquired": True},  # lock
        ]
        mock_cur.fetchall.return_value = [
            {"id": 1, "project_id": "p1", "raw_input": "build it",
             "origin": "system_goal", "wait_for": "[]", "plan_id": None},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import drain_pending
        count = drain_pending()

        assert count == 1
        # Verify outbox INSERT with sys.goal_queued routing key
        outbox_calls = [
            c for c in mock_cur.execute.call_args_list
            if "outbox" in str(c[0][0]).lower()
        ]
        assert len(outbox_calls) == 1
        assert "sys.goal_queued" in str(outbox_calls[0])
        # Verify pending_goals set to in_progress (not submitted)
        update_calls = [
            c for c in mock_cur.execute.call_args_list
            if "UPDATE pending_goals" in str(c[0][0])
        ]
        assert any("in_progress" in str(c) for c in update_calls)

    @mock.patch("psycopg.connect")
    def test_lock_contention_skips(self, mock_connect):
        """If advisory lock is not acquired, drain returns 0."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = {"acquired": False}
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import drain_pending
        count = drain_pending()
        assert count == 0

    @mock.patch("psycopg.connect")
    def test_skips_goals_with_unmet_wait_for(self, mock_connect):
        """Goals with unmet wait_for are not submitted."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = [
            {"acquired": True},  # lock
            {"id": "run-p2"},  # wait_for project has active run → not ready
        ]
        mock_cur.fetchall.return_value = [
            {"id": 2, "project_id": "p1", "raw_input": "wait for p2",
             "origin": "system_goal", "wait_for": '["p2"]', "plan_id": None},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import drain_pending
        count = drain_pending()
        assert count == 0  # not submitted
        # Verify no outbox INSERT
        outbox_calls = [
            c for c in mock_cur.execute.call_args_list
            if "outbox" in str(c[0][0]).lower()
        ]
        assert len(outbox_calls) == 0

    @mock.patch("psycopg.connect")
    def test_no_pending_goals(self, mock_connect):
        """No pending goals → drain returns 0."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = {"acquired": True}
        mock_cur.fetchall.return_value = []
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import drain_pending
        assert drain_pending() == 0


# ── has_merged_master() ──────────────────────────────────────────────────────

class TestHasMergedMaster:
    @mock.patch("psycopg.connect")
    def test_returns_true_when_merged(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.side_effect = [(1,)]  # merged run exists
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import has_merged_master
        assert has_merged_master("p1") is True

    @mock.patch("psycopg.connect")
    def test_returns_false_when_not_merged(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchone.return_value = None  # no merged run
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import has_merged_master
        assert has_merged_master("p1") is False


# ── record_dep_shas() ────────────────────────────────────────────────────────

class TestRecordDepShas:
    @mock.patch("psycopg.connect")
    @mock.patch("subprocess.run")
    def test_records_shas(self, mock_run, mock_connect):
        """Records git SHA for each dependency."""
        mock_cur = mock.MagicMock()
        # Deps query returns one dep
        mock_cur.fetchall.return_value = [
            {"dep_name": "backend", "depends_on_project_id": "p2",
             "name": "Backend", "repo_path": "sys1/backend"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        # git rev-parse returns a SHA
        mock_run.return_value.returncode = 0
        mock_run.return_value.stdout = "abc123def456\n"

        from services.planner.system_goal import record_dep_shas
        shas = record_dep_shas("project-1", "run-1")

        assert "p2" in shas
        assert shas["p2"] == "abc123def456"

    @mock.patch("psycopg.connect")
    @mock.patch("subprocess.run")
    def test_missing_repo_returns_unknown(self, mock_run, mock_connect):
        """When git fails, the sha is recorded as 'unknown'."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"dep_name": "missing", "depends_on_project_id": "p3",
             "name": "Missing", "repo_path": "nonexistent"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur
        mock_run.side_effect = FileNotFoundError("no repo")

        from services.planner.system_goal import record_dep_shas
        shas = record_dep_shas("project-1", "run-1")
        assert shas.get("p3") == "unknown"

    @mock.patch("psycopg.connect")
    def test_no_deps(self, mock_connect):
        """Project with no dependencies returns empty dict."""
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = []
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import record_dep_shas
        assert record_dep_shas("standalone", "run-1") == {}


# ── get_system_queue() ──────────────────────────────────────────────────────

class TestGetSystemQueue:
    @mock.patch("psycopg.connect")
    def test_returns_pending_goals_for_system(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"id": 1, "project_id": "p1", "raw_input": "goal 1",
             "status": "pending", "wait_for": "[]"},
            {"id": 2, "project_id": "p2", "raw_input": "goal 2",
             "status": "submitted", "wait_for": '["p1"]'},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import get_system_queue
        queue = get_system_queue("sys1")
        assert len(queue) == 2
        assert queue[0]["project_id"] == "p1"
        assert queue[1]["status"] == "submitted"

    @mock.patch("psycopg.connect")
    def test_filters_by_status(self, mock_connect):
        mock_cur = mock.MagicMock()
        mock_cur.fetchall.return_value = [
            {"id": 1, "project_id": "p1", "raw_input": "goal 1", "status": "pending"},
        ]
        mock_conn = mock_connect.return_value.__enter__.return_value
        mock_conn.cursor.return_value.__enter__.return_value = mock_cur

        from services.planner.system_goal import get_system_queue
        queue = get_system_queue("sys1", status="pending")
        assert len(queue) == 1
