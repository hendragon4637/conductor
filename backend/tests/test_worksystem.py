"""File 10 tests — worksystem store, snapshot, adjustments (backend/worksystem)."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from backend.worksystem.adjustments import (
    adjustment_signature,
    recurrence_finding,
    same_adjustment_in_last_n_runs,
    structural_diff,
    tag_possibly_stale,
)
from backend.worksystem.compose import check_compose_valid, render_compose
from backend.worksystem.index import read_index, write_index
from backend.worksystem.repo import ensure_worksystem, git_head, remove_worktree
from backend.worksystem.snapshot import (
    blocked_result,
    compose_services,
    missing_members,
    snapshot_worktree,
)
from shared.l4_models import Finding


@pytest.fixture()
def ws_root(tmp_path, monkeypatch):
    root = tmp_path / "worksystem"
    monkeypatch.setenv("WORKSYSTEM_ROOT", str(root))
    return root


def _git_commit(repo: Path) -> str:
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-m", "t"],
        check=True, capture_output=True,
    )
    return git_head(repo)


def _member_index(system_id: str, name: str, project_id: str, sha: str) -> dict:
    return {
        "system_id": system_id,
        "members": [{
            "name": name,
            "project_id": project_id,
            "runnable": True,
            "port": 8000,
            "health": "/health",
            "image_tag": None,
            "published_sha": sha,
            "published_at": "2026-01-01T00:00:00Z",
            "env_required": ["DB_URL"],
            "env_l4_defaults": {"DB_URL": "postgres://x"},
            "depends_on": [],
            "delivery_form": "service",
        }],
    }


# ── structural_diff / signatures ─────────────────────────────────────


def test_structural_diff_added_removed_changed():
    base = {"services": {"a": {"image": "x"}, "b": {"image": "y"}}}
    final = {"services": {"a": {"image": "x"}, "c": {"image": "z"}}}
    changes = {c["op"] for c in structural_diff(base, final)}
    assert changes == {"added", "removed"}
    targets = {c["target"] for c in structural_diff(base, final)}
    assert targets == {"b", "c"}


def test_structural_diff_changed_key_reported_once():
    base = {"services": {"a": {"image": "x", "ports": ["1"]}}}
    final = {"services": {"a": {"image": "x", "ports": ["2"]}}}
    assert structural_diff(base, final) == [{"target": "a", "op": "changed", "key": "ports"}]


def test_adjustment_signature_ignores_order():
    a = {"semantic": [{"target": "x", "op": "added", "key": ""},
                      {"target": "y", "op": "removed", "key": ""}]}
    b = {"semantic": [{"target": "y", "op": "removed", "key": ""},
                      {"target": "x", "op": "added", "key": ""}]}
    assert adjustment_signature(a) == adjustment_signature(b)
    assert adjustment_signature({"semantic": []}) == repr([])


# ── recurrence logic (no DB — recent runs are mocked) ────────────────


def test_recurrence_requires_prior_run(monkeypatch):
    import backend.worksystem.adjustments as adj

    monkeypatch.setattr(adj, "recent_system_l4_adjustments", lambda system_id, limit=3: [])
    assert same_adjustment_in_last_n_runs("s", {"semantic": [{"target": "a", "op": "added"}]}) is False


def test_recurrence_true_when_prior_matches(monkeypatch):
    import backend.worksystem.adjustments as adj

    sig = {"semantic": [{"target": "api", "op": "changed", "key": "image"}]}
    monkeypatch.setattr(adj, "recent_system_l4_adjustments",
                        lambda system_id, limit=3: [sig, sig])
    assert same_adjustment_in_last_n_runs("s", sig) is True


def test_recurrence_false_when_prior_differs(monkeypatch):
    import backend.worksystem.adjustments as adj

    sig = {"semantic": [{"target": "api", "op": "changed", "key": "image"}]}
    other = {"semantic": [{"target": "db", "op": "added", "key": ""}]}
    monkeypatch.setattr(adj, "recent_system_l4_adjustments",
                        lambda system_id, limit=3: [other])
    assert same_adjustment_in_last_n_runs("s", sig) is False


def test_recurrence_finding_shape():
    adj = {"semantic": [{"target": "api", "op": "changed", "key": "image"},
                        {"target": "db", "op": "added", "key": ""}]}
    f = recurrence_finding("sys1", adj, "s1")
    assert f.severity == "medium"
    assert f.scenario_id == "s1"
    assert f.where == ["members/api/workspace.json", "members/db/workspace.json"]
    assert "sys1" in f.why


# ── missing-members gate ─────────────────────────────────────────────


def test_blocked_result_shape():
    r = blocked_result("sys1", ["db"])
    assert r["status"] == "blocked"
    assert "db" in r["message"]


def test_missing_members_debug_subset(ws_root):
    repo = ensure_worksystem("sys1")
    write_index(repo, _member_index("sys1", "api", "proj-a", "abc"))
    missing = missing_members("sys1", members=["api", "db"])
    assert missing == ["db"]


# ── worksystem repo + snapshot lifecycle ─────────────────────────────


def test_ensure_worksystem_init(ws_root):
    repo = ensure_worksystem("sys1")
    assert (repo / ".gitignore").exists()
    assert read_index(repo) == {"system_id": "sys1", "members": []}
    porcelain = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True,
    ).stdout
    assert porcelain == ""


def test_snapshot_worktree_and_remove(ws_root):
    repo = ensure_worksystem("sys1")
    write_index(repo, _member_index("sys1", "api", "proj-a", "abc"))
    _git_commit(repo)
    wt = snapshot_worktree(repo, "l4sys_test123")
    assert wt.is_dir()
    assert (wt / "index.json").exists()
    assert (wt / ".git").is_file()

    tags = subprocess.run(["git", "-C", str(repo), "tag", "-l", "l4/run-l4sys_test123"],
                          capture_output=True, text=True).stdout.strip()
    assert tags == "l4/run-l4sys_test123"

    remove_worktree(wt)
    assert not wt.exists()
    leftovers = subprocess.run(["git", "-C", str(repo), "worktree", "list"],
                               capture_output=True, text=True).stdout
    assert str(wt) not in leftovers


def test_remove_worktree_plain_dir(tmp_path):
    d = tmp_path / "plain"
    d.mkdir()
    (d / "f").write_text("x")
    remove_worktree(d)
    assert not d.exists()


# ── compose rendering ────────────────────────────────────────────────


def test_render_compose_and_check(ws_root):
    repo = ensure_worksystem("sys1")
    write_index(repo, _member_index("sys1", "api", "proj-a", "abc"))
    text = render_compose(repo)
    assert check_compose_valid(text)
    assert "api" in text
    assert "${DB_URL}" in text
    assert (repo / "compose.yml").exists()
    assert "DB_URL=postgres://x" in (repo / ".env.example").read_text()


def test_render_compose_non_runnable_member_skipped(ws_root):
    import yaml

    repo = ensure_worksystem("sys1")
    idx = _member_index("sys1", "docs", "proj-d", "abc")
    idx["members"][0]["runnable"] = False
    write_index(repo, idx)
    data = yaml.safe_load(render_compose(repo))
    assert "docs" not in data["services"]


def test_compose_services(ws_root):
    repo = ensure_worksystem("sys1")
    write_index(repo, _member_index("sys1", "api", "proj-a", "abc"))
    services = compose_services(repo)
    assert services == [{
        "name": "api", "slug": "api", "host": "127.0.0.1",
        "port": 8000, "assigned_host_port": 8000,
    }]


# ── staleness tagging ────────────────────────────────────────────────


def test_tag_possibly_stale(ws_root, tmp_path, monkeypatch):
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    repo = ensure_worksystem("sys1")
    member_ws = tmp_path / "workspace" / "proj-a"
    member_ws.mkdir(parents=True)
    subprocess.run(["git", "init"], cwd=str(member_ws), check=True, capture_output=True)
    (member_ws / "f").write_text("x")
    master = _git_commit(member_ws)

    write_index(repo, _member_index("sys1", "api", "proj-a", "0" * 40))
    stale_f = Finding(
        what="api misbehaves", where=["members/api/workspace.json"],
        why="observed during scenario", severity="medium", scenario_id="s1",
    )
    fresh_f = Finding(
        what="db image missing", where=["members/db/workspace.json"],
        why="observed during scenario", severity="medium", scenario_id="s1",
    )
    tagged = tag_possibly_stale([stale_f, fresh_f], "sys1")
    assert tagged[0].possibly_stale is True
    assert tagged[1].possibly_stale is False

    write_index(repo, _member_index("sys1", "api", "proj-a", master))
    assert tag_possibly_stale([stale_f], "sys1")[0].possibly_stale is False


# ── shared model + intake filter ─────────────────────────────────────


def test_finding_possibly_stale_default():
    f = Finding(what="x" * 10, where=["a"], why="y" * 10, severity="low", scenario_id="s1")
    assert f.possibly_stale is False


def test_intake_filters_possibly_stale():
    from services.intake.adapters.l4_findings import L4FindingsAdapter

    adapter = L4FindingsAdapter()
    payload = {
        "run_id": "run_x",
        "project_id": "sys1",
        "findings": [
            {"what": "api fails to start", "where": ["members/api/workspace.json"],
             "why": "observed", "severity": "high", "scenario_id": "s1",
             "possibly_stale": True},
            {"what": "db port collision", "where": ["members/db/workspace.json"],
             "why": "observed", "severity": "high", "scenario_id": "s1"},
        ],
    }
    intents = adapter.normalize(payload)
    assert len(intents) == 1
    evidence = "\n".join(intents[0].evidence)
    assert "members/db/workspace.json" in evidence
    assert "members/api/workspace.json" not in evidence


def test_intake_all_stale_emits_nothing():
    from services.intake.adapters.l4_findings import L4FindingsAdapter

    adapter = L4FindingsAdapter()
    payload = {
        "run_id": "run_x", "project_id": "sys1",
        "findings": [
            {"what": "api fails", "where": ["members/api/workspace.json"],
             "why": "observed", "severity": "high", "scenario_id": "s1",
             "possibly_stale": True},
        ],
    }
    assert adapter.normalize(payload) == []


# ── publish-on-merge (guide 10.3, T-10.2) ────────────────────────────


def _publish_db_stubs(monkeypatch):
    import importlib

    pub = importlib.import_module("backend.worksystem.publish")
    monkeypatch.setattr(pub, "system_of", lambda project_id: "sys1")
    monkeypatch.setattr(pub, "project_kind", lambda project_id: "component")
    monkeypatch.setattr(pub, "project_name", lambda project_id: "proj-a")
    monkeypatch.setattr(pub, "depends_on_map", lambda system_id: {})
    monkeypatch.setattr(pub, "standard_for_run", lambda run_id, project_id: {
        "slug": "python-backend",
        "service_template": {"runnable": True, "port": 8000, "health": "/health",
                             "env_required": ["DB_URL"], "env_l4_defaults": {},
                             "commands": {"setup": "true", "verify": "bash gates.sh"}},
        "publish_manifest": {
            "files": ["RUN.md", ".env.example", ".conductor/workspace.json"],
            "artifacts": ["data/output/**"],
        },
    })
    return pub


class FakeCursor:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    def execute(self, sql, params=None):
        self.executed.append((sql, params))
        return self

    def fetchone(self):
        return None

    def fetchall(self):
        return []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class FakeLockConn:
    def __init__(self):
        self.cur = FakeCursor()

    def cursor(self):
        return self.cur

    def commit(self):
        return None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _patch_advisory_lock(monkeypatch):
    monkeypatch.setattr(psycopg, "connect", lambda *a, **k: FakeLockConn())


def _master_worktree(tmp_path, monkeypatch):
    ws = tmp_path / "workspace" / "proj-a"
    ws.mkdir(parents=True)
    (ws / "RUN.md").write_text("# Run\n")
    (ws / ".env.example").write_text("DB_URL=postgres://x\n")
    (ws / ".conductor").mkdir(parents=True)
    (ws / ".conductor" / "workspace.json").write_text(
        json.dumps({"layout": "root", "components": [
            {"subdir": ".", "standard_slug": "python-backend", "runnable": True,
             "port": 8000, "health": "/health", "env_required": ["DB_URL"],
             "env_l4_defaults": {}, "delivery_form": "served_url",
             "commands": {"setup": "true"}}]})
    )
    out = ws / "data" / "output"
    out.mkdir(parents=True)
    (out / "report.csv").write_text("a,b\n")
    subprocess.run(["git", "init"], cwd=str(ws), check=True, capture_output=True)
    _git_commit(ws)
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path / "workspace"))
    return ws


class TestPublishOnMerge:
    def test_publish_copies_files_and_updates_index(self, ws_root, tmp_path, monkeypatch):
        pub = _publish_db_stubs(monkeypatch)
        _patch_advisory_lock(monkeypatch)
        master = _master_worktree(tmp_path, monkeypatch)
        head = git_head(master)
        writes: list[tuple] = []
        monkeypatch.setattr(pub, "_update_run_publish",
                            lambda rid, status, err, commit: writes.append((rid, status, err, commit)))

        result = pub.publish({"id": "run_1", "project_id": "proj-a", "image_tag": None},
                             workspace_root=str(tmp_path / "workspace"))

        assert result["status"] == "published"
        member = ws_root / "repos" / "sys1" / "members" / "proj-a"
        assert (member / "RUN.md").exists()
        assert (member / "workspace.json").exists()  # flattened from .conductor
        assert (member / "data" / "output" / "report.csv").exists()
        src = json.loads((member / "_source.json").read_text())
        assert src["project_id"] == "proj-a"
        assert src["sha"] == head
        index = read_index(ws_root / "repos" / "sys1")
        assert index["members"][0]["name"] == "proj-a"
        assert index["members"][0]["published_sha"] == head
        assert (ws_root / "repos" / "sys1" / "compose.yml").exists()
        assert writes == [("run_1", "published", None, pub.git_head(ws_root / "repos" / "sys1"))]

    def test_publish_skips_without_system(self, ws_root, tmp_path, monkeypatch):
        import importlib

        pub = importlib.import_module("backend.worksystem.publish")
        monkeypatch.setattr(pub, "system_of", lambda project_id: None)
        monkeypatch.setattr(pub, "_update_run_publish", lambda *a: (_ for _ in ()).throw(AssertionError("should not write")))
        result = pub.publish({"id": "run_1", "project_id": "proj-a"},
                             workspace_root=str(tmp_path / "workspace"))
        assert result == {"status": "skipped"}

    def test_publish_skips_assembly_composer(self, ws_root, tmp_path, monkeypatch):
        import importlib

        pub = importlib.import_module("backend.worksystem.publish")
        monkeypatch.setattr(pub, "system_of", lambda project_id: "sys1")
        monkeypatch.setattr(pub, "project_kind", lambda project_id: "assembly")
        result = pub.publish({"id": "run_1", "project_id": "proj-a"},
                             workspace_root=str(tmp_path / "workspace"))
        assert result == {"status": "skipped"}

    def test_publish_failure_marks_stale_never_fails_run(self, ws_root, tmp_path, monkeypatch):
        pub = _publish_db_stubs(monkeypatch)
        _master_worktree(tmp_path, monkeypatch)
        monkeypatch.setattr(pub, "_publish_locked", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        writes: list[tuple] = []
        monkeypatch.setattr(pub, "_update_run_publish",
                            lambda rid, status, err, commit: writes.append((rid, status, err, commit)))

        result = pub.publish({"id": "run_1", "project_id": "proj-a"},
                             workspace_root=str(tmp_path / "workspace"))

        assert result["status"] == "stale"
        assert "boom" in result["error"]
        assert writes == [("run_1", "stale", "boom", None)]

    def test_publish_run_delegates_and_missing_run_skipped(self, ws_root, monkeypatch):
        import importlib

        pub = importlib.import_module("backend.worksystem.publish")

        calls: list[dict] = []
        monkeypatch.setattr(pub, "publish",
                            lambda run, workspace_root=None: calls.append(run) or {"status": "published"})

        class FakeRowConn:
            def __init__(self, row):
                self._row = row

            def cursor(self):
                return self

            def execute(self, sql, params=None):
                return self

            @property
            def description(self):
                return [type("D", (), {"name": n})() for n in ("id", "project_id")]

            def fetchone(self):
                return self._row

            def __enter__(self):
                return self

            def __exit__(self, *exc):
                return False

        monkeypatch.setattr(pub, "_connect", lambda db_url="": FakeRowConn(("run_1", "proj-a")))
        assert pub.publish_run("run_1", workspace_root=str(ws_root))["status"] == "published"
        assert calls[0]["id"] == "run_1"

        monkeypatch.setattr(pub, "_connect", lambda db_url="": FakeRowConn(None))
        assert pub.publish_run("run_ghost", workspace_root=str(ws_root)) == {
            "status": "skipped", "error": "run run_ghost not found",
        }


class TestUpdateRunPublishColumns:
    def test_writes_publish_status_error_commit(self, monkeypatch):
        import importlib

        pub = importlib.import_module("backend.worksystem.publish")

        cur = FakeCursor()
        conn = FakeLockConn()
        conn.cur = cur
        monkeypatch.setattr(pub, "_connect", lambda db_url="": conn)

        pub._update_run_publish("run_1", "published", None, "abc123")

        sql, params = cur.executed[0]
        assert "publish_status" in sql and "publish_error" in sql and "publish_commit" in sql
        assert params == ("published", None, "abc123", "run_1")
